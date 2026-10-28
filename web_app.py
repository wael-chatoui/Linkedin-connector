#!/usr/bin/env python3
"""Interface locale du connecteur. Aucune action LinkedIn au démarrage."""

import argparse
import csv
import io
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import linkedin_connect as engine
from app_settings import DEFAULTS, SettingsError as InputError, read_settings, validate_settings, write_settings
from automation_schedule import ScheduleError, ScheduleManager
from mail_client import MailClient, MailError
from runtime_lock import OperationBusy, OperationLock, operation_in_progress

ROOT = Path(__file__).resolve().parent
APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
MAX_BODY = 256_000


class BusyError(RuntimeError):
    pass


def stamp():
    return datetime.now().isoformat(timespec="seconds")


def numeric(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def parse_timestamp(value):
    try:
        return datetime.fromisoformat(value).astimezone()
    except (ValueError, TypeError, OverflowError):
        return None


def local_timezone_name():
    candidates = [os.environ.get("TZ", "").lstrip(":")]
    try:
        resolved = str(Path("/etc/localtime").resolve())
        if "/zoneinfo/" in resolved:
            candidates.append(resolved.split("/zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in candidates:
        if not name:
            continue
        try:
            ZoneInfo(name)
            return name
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return None


def next_scheduled_run(settings, now=None, already_ran=None):
    """Estimate the next calendar trigger in this computer's local timezone."""
    now = now or datetime.now().astimezone()
    local_now = now.astimezone().replace(tzinfo=None)
    hour, minute = map(int, settings["schedule_time"].split(":"))
    for offset in range(8):
        day = local_now.date() + timedelta(days=offset)
        if settings["schedule_days"] == "weekdays" and day.weekday() >= 5:
            continue
        if day.isoformat() == already_ran:
            continue
        candidate = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
        # Both occurrences of a repeated local hour must be compared as
        # instants, not as wall-clock text. A saved daily marker still prevents
        # showing a second passage after today's run was already processed.
        occurrences = [candidate.replace(fold=fold).astimezone() for fold in (0, 1)]
        valid = [value for value in occurrences if value.replace(tzinfo=None) == candidate]
        future = [value for value in valid if value.timestamp() > now.timestamp()]
        if future:
            return min(future, key=lambda value: value.timestamp()).isoformat(timespec="seconds")
        if not valid and candidate > local_now:
            # The chosen hour does not exist on the spring clock-change day.
            # Show the recurring schedule rather than promise an invented time.
            return None
    return None


class Application:
    def __init__(self, data_dir=None, scheduler=None, mail_client=None):
        self.data_dir = Path(data_dir or engine.DATA_DIR).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.lock = threading.RLock()
        self.csrf_token = secrets.token_urlsafe(32)
        self.session_status = "unknown"
        self.session_checked_at = None
        try:
            saved_session = json.loads((self.data_dir / "session_state.json").read_text(encoding="utf-8"))
            if isinstance(saved_session, dict):
                self.session_checked_at = parse_timestamp(saved_session.get("checked_at"))
                if saved_session.get("status") == "disconnected":
                    self.session_status = "disconnected"
        except (OSError, ValueError):
            pass
        self.job = None
        self.process = None
        self.worker = None
        self.stopping = False
        self.scheduler = scheduler if scheduler is not None else ScheduleManager(ROOT, data_dir=self.data_dir)
        self.mail = mail_client if mail_client is not None else MailClient(self.data_dir)
        self.schedule_cache = None
        self.schedule_checked_at = 0
        self.configuration_error = None
        self.settings = dict(DEFAULTS)
        self._refresh_settings()

    def _refresh_settings(self):
        try:
            self.settings = read_settings(self.settings_path)
            self.configuration_error = None
        except FileNotFoundError:
            self.settings = dict(DEFAULTS)
            self.configuration_error = None
        except (OSError, ValueError) as error:
            self.settings = dict(DEFAULTS)
            self.configuration_error = f"Réglages illisibles : {error}. Enregistre une configuration pour les réparer."

    def rows(self):
        path = self.data_dir / "prospects.csv"
        if not path.exists():
            return []
        with path.open(encoding="utf-8", newline="") as stream:
            return [{key: (row.get(key) or "").strip() for key in engine.FIELDS}
                    for row in csv.DictReader(stream)]

    def last_search(self):
        try:
            report = json.loads((self.data_dir / "last_search.json").read_text(encoding="utf-8"))
            if (not isinstance(report, dict) or not isinstance(report.get("urls"), list)
                    or not all(isinstance(url, str) for url in report["urls"])
                    or not isinstance(report.get("keywords"), list)
                    or not all(isinstance(word, str) for word in report["keywords"])
                    or report.get("geography") not in ("france", "international", "paris")
                    or not parse_timestamp(report.get("finished_at"))):
                return None
            return report
        except (OSError, ValueError):
            return None

    def eligible(self, rows, settings):
        return sorted((row for row in rows if not row["status"]
                       and numeric(row["score"]) >= settings["min_score"]
                       and (not settings["active_only"] or
                            (row["last_post_days"] != "" and
                             0 <= numeric(row["last_post_days"], 999) <= 30))),
                      key=lambda row: -numeric(row["score"]))

    def sent_count(self, days):
        path = self.data_dir / "sent_log.csv"
        if not path.exists():
            return 0
        cutoff = date.today() - timedelta(days=days - 1)
        with path.open(encoding="utf-8", newline="") as stream:
            count = 0
            for row in csv.DictReader(stream):
                try:
                    count += date.fromisoformat(row.get("date", "")) >= cutoff
                except ValueError:
                    continue
            return count

    def save_settings(self, settings):
        with self.lock:
            self._refresh_settings()
            if isinstance(settings, dict) and "automation_enabled" in settings:
                raise InputError("Utilise le bouton d'activation ou de pause pour changer l'automatisation.")
            if self.settings["automation_enabled"]:
                raise BusyError("Utilise « Enregistrer les modifications » pour appliquer les réglages à l'automatisation active.")
            settings = validate_settings(settings, self.settings)
            return self._persist_settings(settings)

    def _persist_settings(self, settings):
        write_settings(self.settings_path, settings)
        self.settings = dict(settings)
        self.configuration_error = None
        return dict(settings)

    def _record_session(self, status):
        self.session_status = status
        self.session_checked_at = datetime.now().astimezone()
        path = self.data_dir / "session_state.json"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".session-",
                                             suffix=".tmp", dir=self.data_dir, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"status": status, "checked_at": self.session_checked_at.isoformat()}, stream)
            temporary.replace(path)
        except OSError:
            # A successfully checked connection remains usable if only the
            # local reminder could not be saved. It is checked again next run.
            pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _automation_attention(self, last_run):
        if not (self.data_dir / ".profile").is_dir() or self.session_status == "disconnected":
            return {"kind": "login", "title": "Connectez votre compte LinkedIn",
                    "message": "Cliquez sur « Connecter LinkedIn », puis terminez la connexion dans la fenêtre qui s’ouvre."}
        if not last_run:
            return None
        reason = last_run.get("reason")
        # Older run reports did not include a structured reason.
        if not reason and "NotLoggedIn" in str(last_run.get("message", "")):
            reason = "login"
        checked = self.session_checked_at
        finished = parse_timestamp(last_run.get("finished_at") or last_run.get("started_at"))
        resolved = checked and finished and checked > finished and self.session_status != "disconnected"
        if reason in ("login", "linkedin_check") and not resolved:
            return {"kind": reason, "title": "Une connexion LinkedIn est nécessaire" if reason == "login" else "LinkedIn demande une vérification",
                    "message": "Ouvrez la connexion LinkedIn et suivez les instructions affichées. Le programme réessaiera au prochain passage prévu."}
        if reason in ("login", "linkedin_check") and resolved:
            return None
        if reason == "network":
            return {"kind": "network", "title": "Le dernier passage n’avait pas accès à Internet",
                    "message": "Vérifiez la connexion Internet du Mac. Le programme réessaiera au prochain passage prévu."}
        if last_run.get("status") == "error" or reason == "error":
            return {"kind": "error", "title": "Le dernier passage n’a pas abouti",
                    "message": last_run.get("summary") or "Consultez le résultat ci-dessous. Vous pouvez tester la recherche sans envoyer d’invitation."}
        return None

    def configure_automation(self, payload):
        if (not isinstance(payload, dict) or payload.keys() - {"enabled", "settings"}
                or type(payload.get("enabled")) is not bool):
            raise InputError("L'activation doit être indiquée par un booléen.")
        with self.lock:
            self._refresh_settings()
            enabled = payload["enabled"]
            changes = payload.get("settings", {})
            if not isinstance(changes, dict) or "automation_enabled" in changes:
                raise InputError("Réglages d'automatisation invalides.")
            if not enabled:
                # Disable first, even while an existing daily run owns the profile.
                # The runner checks this switch before every new invitation.
                settings = self._persist_settings({**self.settings, "automation_enabled": False})
                warning = None
                try:
                    with OperationLock(self.data_dir / ".operation.lock"):
                        self.scheduler.uninstall()
                except OperationBusy:
                    warning = "Pause enregistrée. L'exécution en cours s'arrêtera avant la prochaine invitation."
                except ScheduleError as error:
                    warning = f"Envois automatiques désactivés. Le calendrier n'a pas pu être retiré : {error}"
                self.schedule_cache = None
                return {"ok": True, "settings": settings, "message": warning or "Automatisation mise en pause."}
            if self.stopping or (self.job and self.job["status"] == "running"):
                raise BusyError("Attends la fin de l'opération avant de modifier l'automatisation.")
            settings = validate_settings(changes, self.settings)
            if not settings["keywords"]:
                raise InputError("Entre au moins une recherche par mots-clés avant d'activer l'automatisation.")
            attention = self._automation_attention(self.automation_state().get("last_run"))
            if attention and attention["kind"] in ("login", "linkedin_check"):
                raise InputError("Connectez d’abord LinkedIn depuis l’étape 1, puis revenez activer les invitations.")
            try:
                with OperationLock(self.data_dir / ".operation.lock"):
                    # A crash or a scheduler error must leave automatic sends disabled.
                    self._persist_settings({**settings, "automation_enabled": False})
                    self.schedule_cache = None
                    try:
                        self.scheduler.install({**settings, "automation_enabled": True})
                    except ScheduleError as error:
                        raise ScheduleError(f"Activation impossible ; les envois restent désactivés. {error}") from error
                    self._persist_settings({**settings, "automation_enabled": True})
            except OperationBusy as error:
                raise BusyError("Une opération LinkedIn est en cours. Réessaie après sa fin.") from error
            return {"ok": True, "settings": dict(self.settings), "message": "Automatisation enregistrée. Le prochain passage utilisera ces réglages."}

    def automation_state(self):
        if self.schedule_cache is None or time.monotonic() - self.schedule_checked_at > 5:
            self.schedule_cache = self.scheduler.status()
            self.schedule_checked_at = time.monotonic()
        last_run = None
        path = self.data_dir / "automation_status.json"
        try:
            last_run = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(last_run, dict):
                raise ValueError("état invalide")
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            last_run = {"status": "error", "message": "Le compte rendu de la dernière exécution est illisible."}
        running = False
        if last_run and last_run.get("status") == "running":
            try:
                owner = (self.data_dir / ".operation.lock").read_text().strip()
                running = str(last_run.get("pid")) == owner and operation_in_progress(self.data_dir / ".operation.lock")
            except OSError:
                pass
            if not running:
                last_run = {**last_run, "status": "error", "reason": "error",
                            "summary": "Le dernier passage s’est arrêté avant la fin. Consultez les détails avant de réessayer.",
                            "message": "La dernière exécution a été interrompue. Consulte le journal avant de relancer."}
        scheduler = dict(self.schedule_cache)
        logs = []
        # Keep the earlier history visible after moving launchd's output out
        # of Documents, where macOS can deny xpcproxy access before Python runs.
        log_paths = dict.fromkeys([self.data_dir / "auto.log",
                                   Path(scheduler.get("log_path") or self.data_dir / "auto.log")])
        for log_path in log_paths:
            try:
                with log_path.open("rb") as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 32768))
                    logs.extend(stream.read().decode("utf-8", errors="replace").splitlines())
            except OSError:
                pass
        logs = logs[-100:]
        scheduled = (self.settings["automation_enabled"] and not self.configuration_error
                     and scheduler.get("installed") and scheduler.get("loaded")
                     and not scheduler.get("error") and not scheduler.get("legacy")
                     and not scheduler.get("foreign")
                     and scheduler.get("schedule_time") == self.settings["schedule_time"]
                     and scheduler.get("schedule_days") == self.settings["schedule_days"])
        already_ran = None
        try:
            already_ran = (self.data_dir / "last_auto_run.txt").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        next_run = next_scheduled_run(self.settings, already_ran=already_ran) if scheduled else None
        return {"enabled": self.settings["automation_enabled"], "configured": bool(self.settings["keywords"]),
                "scheduler": scheduler, "last_run": last_run, "logs": logs,
                "running": running, "error": self.configuration_error,
                "next_run_at": next_run, "timezone": local_timezone_name(),
                "attention": self._automation_attention(last_run)}

    def state(self):
        with self.lock:
            self._refresh_settings()
            rows = self.rows()
            automation = self.automation_state()
            # A profile directory alone does not prove an authenticated session.
            return {
                "version": APP_VERSION,
                "csrf_token": self.csrf_token,
                "settings": dict(self.settings),
                "note_template": engine.NOTE_TEMPLATE,
                "prospects": sorted(rows, key=lambda row: -numeric(row["score"])),
                "last_search": self.last_search(),
                "stats": {"total": len(rows), "eligible": len(self.eligible(rows, self.settings)),
                          "sent_today": self.sent_count(1), "sent_week": self.sent_count(7),
                          "daily_cap": engine.DAILY_CAP, "weekly_cap": engine.WEEKLY_CAP},
                "session": {"status": self.session_status,
                            "profile_exists": (self.data_dir / ".profile").exists()},
                "job": json.loads(json.dumps(self.job)) if self.job else None,
                "automation": automation,
                "mail": self.mail.state(),
                "legacy_schedule": automation["scheduler"]["installed"] and not automation["enabled"],
            }

    def configure_mail(self, payload):
        # Mail settings have their own credential lock and remain available
        # while an automation owns the LinkedIn browser/profile lock.
        return {"ok": True, "mail": self.mail.configure(payload)}

    def start_job(self, payload):
        if not isinstance(payload, dict) or payload.keys() - {"action", "settings", "selected_urls", "confirmed"}:
            raise InputError("Action invalide.")
        action = payload.get("action")
        if action not in ("login", "find", "preview", "send", "enrich", "rescore"):
            raise InputError("Action inconnue.")
        with self.lock:
            self._refresh_settings()
            if self.stopping or (self.job and self.job["status"] == "running"):
                raise BusyError("Une opération est déjà en cours. Attends sa fin ou arrête-la.")
            changes = payload.get("settings", {})
            if isinstance(changes, dict) and "automation_enabled" in changes:
                raise InputError("L'activation automatique doit passer par son bouton dédié.")
            settings = validate_settings(changes, self.settings)
            args = [sys.executable, "-u", str(ROOT / "linkedin_connect.py")]
            selected = None
            if action == "login":
                if not (self.data_dir / ".profile").is_dir():
                    self._record_session("disconnected")
                args += ["login", "--wait", "--timeout", "300"]
            elif action == "find":
                if not settings["keywords"]:
                    raise InputError("Entre des mots-clés pour lancer une recherche.")
                args += ["find", "--keywords=" + settings["keywords"], "--pages", str(settings["pages"]),
                         "--network", settings["network"]]
                if settings["geography"] == "international":
                    args.append("--international")
            elif action in ("send", "preview"):
                selected = payload.get("selected_urls")
                if (not isinstance(selected, list) or not selected or len(selected) > 10000
                        or any(not isinstance(url, str) for url in selected)
                        or len(set(selected)) != len(selected)):
                    raise InputError("Sélectionne au moins un profil dans les résultats.")
                allowed = {row["url"] for row in self.eligible(self.rows(), settings)}
                if not set(selected) <= allowed:
                    raise InputError("Certains profils ne sont plus éligibles. Actualise ta sélection.")
                if action == "send" and payload.get("confirmed") is not True:
                    raise InputError("Confirme l'envoi aux profils sélectionnés.")
                args += ["run", "--limit", str(settings["limit"]), "--min-score", str(settings["min_score"])]
                if settings["active_only"]:
                    args.append("--active-only")
                if settings["note"]:
                    args.append("--note")
                if action == "preview":
                    args.append("--dry-run")
            elif action == "enrich":
                args += ["enrich", "--top", "30", "--min-score", str(settings["min_score"])]
            else:
                args += ["rescore"]
            if not self.settings["automation_enabled"]:
                self._persist_settings(settings)
            self.job = {"id": secrets.token_hex(8), "action": action, "status": "running",
                        "message": "Opération en cours…", "logs": [], "started_at": stamp(),
                        "finished_at": None}
            self.worker = threading.Thread(target=self._run, args=(args, selected, self.job), daemon=True)
            self.worker.start()

    def _run(self, args, selected, job):
        selection_path = None
        process = None
        status = "error"
        message = "L'opération s'est arrêtée. Consulte le journal ci-dessous."
        try:
            if selected is not None:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json",
                                                 prefix=".selection-", dir=self.data_dir, delete=False) as stream:
                    selection_path = Path(stream.name)
                    json.dump(selected, stream)
                args += ["--selected-file", str(selection_path)]
            with self.lock:
                if job.get("cancel_requested"):
                    return
                process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, encoding="utf-8", errors="replace", bufsize=1,
                                           start_new_session=True,
                                           env={**os.environ, "LINKEDIN_DATA_DIR": str(self.data_dir),
                                                "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
                self.process = process
            for line in process.stdout:
                with self.lock:
                    job["logs"].append(line.rstrip()[:4000])
                    job["logs"] = job["logs"][-300:]
            code = process.wait()
            with self.lock:
                if not job.get("cancel_requested"):
                    status = "success" if code == 0 else "error"
                    if code == 0:
                        message = "Opération terminée."
                    if code == 3:
                        self._record_session("disconnected")
                        message = "La connexion LinkedIn n’est pas terminée. Cliquez sur « Connecter LinkedIn » et suivez les instructions dans la fenêtre qui s’ouvre."
                    elif code == 0 and job["action"] in ("login", "find", "enrich"):
                        self._record_session("connected")
                        message = {"login": "LinkedIn est connecté. Vous pouvez maintenant activer vos invitations.",
                                   "find": "Recherche terminée. Aucune invitation n’a été envoyée.",
                                   "enrich": "L’activité des profils a été vérifiée. Aucune invitation n’a été envoyée."}[job["action"]]
                    elif code == 4:
                        message = "LinkedIn a interrompu l’action. Ouvrez la connexion LinkedIn pour consulter la limite ou la vérification demandée."
                    elif code == 5:
                        message = "Une autre action LinkedIn est déjà en cours. Attendez sa fin, puis réessayez."
        except Exception as error:
            message = f"Impossible de terminer l'opération : {error}"
            if process and process.poll() is None:
                self._signal_group(process, signal.SIGKILL)
                process.wait()
        finally:
            if process and process.stdout:
                process.stdout.close()
            if selection_path:
                selection_path.unlink(missing_ok=True)
            with self.lock:
                if job.get("cancel_requested"):
                    status = "cancelled"
                    message = "Opération arrêtée. Les invitations déjà envoyées restent envoyées."
                job.update(status=status, message=message, finished_at=stamp())
                self.process = None

    @staticmethod
    def _signal_group(process, sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    def cancel(self):
        with self.lock:
            if not self.job or self.job["status"] != "running":
                raise BusyError("Aucune opération en cours.")
            self.job["cancel_requested"] = True
            self.job["message"] = "Arrêt en cours…"
            process = self.process
            if process:
                self._signal_group(process, signal.SIGINT)
                threading.Thread(target=self._finish_cancel, args=(process, self.worker), daemon=True).start()

    def _finish_cancel(self, process, worker):
        # A browser descendant can keep stdout open after its Python parent exits.
        # Wait for the reader, then reap the whole group, not just the parent.
        worker.join(timeout=5)
        self._signal_group(process, signal.SIGKILL)

    def close(self):
        with self.lock:
            self.stopping = True
            if self.job and self.job["status"] == "running":
                self.cancel()
            worker = self.worker
        if worker:
            worker.join(timeout=7)

    def export_csv(self):
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=engine.FIELDS)
        writer.writeheader()
        for row in self.rows():
            # A LinkedIn headline must never become a spreadsheet formula.
            writer.writerow({key: "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
                             for key, value in row.items()})
        return output.getvalue().encode("utf-8-sig")


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "LinkedInConnector/1.0"

    def log_message(self, *_args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, status, data, content_type="application/json; charset=utf-8"):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if content_type.startswith("text/csv"):
            self.send_header("Content-Disposition", 'attachment; filename="prospects.csv"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, mutation=False):
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected:
            self.reply(403, {"error": "Accès local uniquement. Utilise l'adresse 127.0.0.1 affichée dans le terminal."})
            return False
        origin = self.headers.get("Origin")
        # A link from the README may open this public HTML document. APIs and
        # mutations still require their own same-origin checks and CSRF token.
        document_navigation = (
            not mutation and self.command == "GET" and urlsplit(self.path).path in ("/", "/index.html")
            and self.headers.get("Sec-Fetch-Mode") == "navigate"
            and self.headers.get("Sec-Fetch-Dest") == "document")
        if ((origin and origin != self.server.origin)
                or (self.headers.get("Sec-Fetch-Site") == "cross-site" and not document_navigation)):
            self.reply(403, {"error": "Origine non autorisée."})
            return False
        if mutation and not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.app.csrf_token):
            self.reply(403, {"error": "La session de la page a changé. Recharge la page."})
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/state":
                self.reply(200, self.server.app.state())
            elif path == "/api/health":
                self.reply(200, {"app": "linkedin-connector", "project": str(ROOT), "version": APP_VERSION})
            elif path == "/api/export.csv":
                self.reply(200, self.server.app.export_csv(), "text/csv; charset=utf-8")
            elif path == "/favicon.ico":
                self.reply(204, b"", "image/x-icon")
            elif path in ("/", "/index.html", "/app.js", "/styles.css"):
                name, kind = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
                              "/app.js": ("app.js", "text/javascript"), "/styles.css": ("styles.css", "text/css")}[path]
                self.reply(200, (ROOT / "web" / name).read_bytes(), kind + "; charset=utf-8")
            else:
                self.reply(404, {"error": "Page introuvable."})
        except (OSError, ValueError) as error:
            self.reply(500, {"error": f"Impossible de lire les données locales : {error}"})

    def do_POST(self):
        if not self.allowed(mutation=True):
            return
        if self.headers.get_content_type() != "application/json":
            self.reply(415, {"error": "Le corps de la requête doit être du JSON."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                raise InputError("Taille de requête invalide.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise InputError("Un objet JSON est attendu.")
            path = urlsplit(self.path).path
            if path == "/api/settings":
                self.reply(200, {"ok": True, "settings": self.server.app.save_settings(payload)})
            elif path == "/api/mail":
                self.reply(200, self.server.app.configure_mail(payload))
            elif path == "/api/automation":
                self.reply(200, self.server.app.configure_automation(payload))
            elif path == "/api/jobs":
                self.server.app.start_job(payload)
                self.reply(202, {"ok": True})
            elif path == "/api/jobs/cancel":
                self.server.app.cancel()
                self.reply(200, {"ok": True})
            else:
                self.reply(404, {"error": "Action introuvable."})
        except MailError as error:
            self.reply(error.status, {"error": str(error), "mail": self.server.app.mail.state(refresh=False)})
        except BusyError as error:
            self.reply(409, {"error": str(error)})
        except ScheduleError as error:
            self.reply(500, {"error": str(error)})
        except (ValueError, UnicodeDecodeError) as error:
            self.reply(400, {"error": str(error)})
        except OSError as error:
            self.reply(500, {"error": f"Impossible d'accéder aux données locales : {error}"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Le port doit être compris entre 1 et 65535.")
    origin = f"http://127.0.0.1:{args.port}"
    app = Application()
    try:
        server = LocalServer(("127.0.0.1", args.port), app)
    except OSError:
        try:
            with urlopen(origin + "/api/health", timeout=2) as response:
                existing = json.load(response)
            if (isinstance(existing, dict) and existing.get("app") == "linkedin-connector"
                    and existing.get("project") == str(ROOT)):
                print(f"Interface déjà ouverte : {origin}", flush=True)
                if not args.no_browser:
                    webbrowser.open(origin)
                return
        except (OSError, ValueError, URLError):
            pass
        parser.exit(1, f"Le port {args.port} est occupé. Relance avec ./install.sh --port {args.port + 1}.\n")
    print(f"Interface locale : {origin}\nGarde ce terminal ouvert. Ctrl+C pour arrêter.", flush=True)
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGHUP, interrupt)
    if not args.no_browser:
        try:
            if not webbrowser.open(origin):
                print("Ouvre cette adresse dans ton navigateur.", flush=True)
        except webbrowser.Error:
            print("Ouvre cette adresse dans ton navigateur.", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nArrêt de l'interface…", flush=True)
    finally:
        app.close()
        server.server_close()


if __name__ == "__main__":
    main()
