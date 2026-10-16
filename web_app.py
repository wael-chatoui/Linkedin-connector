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
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

import linkedin_connect as engine

ROOT = Path(__file__).resolve().parent
DEFAULTS = {
    "keywords": "", "pages": 2, "network": "S", "paris": False,
    "min_score": 40, "limit": 3, "active_only": False, "note": False,
}
MAX_BODY = 256_000


class InputError(ValueError):
    pass


class BusyError(RuntimeError):
    pass


def validate_settings(value, base=None):
    if not isinstance(value, dict) or value.keys() - DEFAULTS.keys():
        raise InputError("Réglages invalides.")
    settings = {**(base or DEFAULTS), **value}
    if not isinstance(settings["keywords"], str) or len(settings["keywords"]) > 500:
        raise InputError("Les mots-clés doivent contenir au maximum 500 caractères.")
    settings["keywords"] = settings["keywords"].strip()
    for field, minimum, maximum in (("pages", 1, 10), ("min_score", 0, 100), ("limit", 1, engine.DAILY_CAP)):
        if type(settings[field]) is not int or not minimum <= settings[field] <= maximum:
            raise InputError(f"{field} doit être un entier entre {minimum} et {maximum}.")
    if settings["network"] not in ("S", "O", "S,O"):
        raise InputError("Choisis un réseau valide : 2e degré, 3e degré ou les deux.")
    for field in ("paris", "active_only", "note"):
        if type(settings[field]) is not bool:
            raise InputError(f"{field} doit être un booléen.")
    return settings


def stamp():
    return datetime.now().isoformat(timespec="seconds")


def numeric(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class Application:
    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir or engine.DATA_DIR).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.lock = threading.RLock()
        self.csrf_token = secrets.token_urlsafe(32)
        self.session_status = "unknown"
        self.job = None
        self.process = None
        self.worker = None
        self.stopping = False
        self.settings = dict(DEFAULTS)
        try:
            self.settings = validate_settings(json.loads(self.settings_path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            print("Réglages illisibles : valeurs par défaut utilisées.", flush=True)

    def rows(self):
        path = self.data_dir / "prospects.csv"
        if not path.exists():
            return []
        with path.open(encoding="utf-8", newline="") as stream:
            return [{key: (row.get(key) or "").strip() for key in engine.FIELDS}
                    for row in csv.DictReader(stream)]

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
            settings = validate_settings(settings, self.settings)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".settings-",
                                             suffix=".tmp", dir=self.data_dir, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                temporary.replace(self.settings_path)
            finally:
                temporary.unlink(missing_ok=True)
            self.settings = settings
            return dict(settings)

    def state(self):
        with self.lock:
            rows = self.rows()
            # A profile directory alone does not prove an authenticated session.
            return {
                "csrf_token": self.csrf_token,
                "settings": dict(self.settings),
                "note_template": engine.NOTE_TEMPLATE,
                "prospects": sorted(rows, key=lambda row: -numeric(row["score"])),
                "stats": {"total": len(rows), "eligible": len(self.eligible(rows, self.settings)),
                          "sent_today": self.sent_count(1), "sent_week": self.sent_count(7),
                          "daily_cap": engine.DAILY_CAP, "weekly_cap": engine.WEEKLY_CAP},
                "session": {"status": self.session_status,
                            "profile_exists": (self.data_dir / ".profile").exists()},
                "job": json.loads(json.dumps(self.job)) if self.job else None,
                "legacy_schedule": any((Path.home() / "Library/LaunchAgents").glob("com.*.linkedin-connector.plist")),
            }

    def start_job(self, payload):
        if not isinstance(payload, dict) or payload.keys() - {"action", "settings", "selected_urls", "confirmed"}:
            raise InputError("Action invalide.")
        action = payload.get("action")
        if action not in ("login", "find", "preview", "send", "enrich", "rescore"):
            raise InputError("Action inconnue.")
        with self.lock:
            if self.stopping or (self.job and self.job["status"] == "running"):
                raise BusyError("Une opération est déjà en cours. Attends sa fin ou arrête-la.")
            settings = validate_settings(payload.get("settings", {}), self.settings)
            args = [sys.executable, "-u", str(ROOT / "linkedin_connect.py")]
            selected = None
            if action == "login":
                args += ["login", "--wait", "--timeout", "300"]
            elif action == "find":
                if not settings["keywords"]:
                    raise InputError("Entre des mots-clés pour lancer une recherche.")
                args += ["find", "--keywords=" + settings["keywords"], "--pages", str(settings["pages"]),
                         "--network", settings["network"]]
                if settings["paris"]:
                    args.append("--paris")
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
            self.save_settings(settings)
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
                        self.session_status = "disconnected"
                    elif code == 0 and job["action"] in ("login", "find", "enrich"):
                        self.session_status = "connected"
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
        if (origin and origin != self.server.origin) or self.headers.get("Sec-Fetch-Site") == "cross-site":
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
                self.reply(200, {"app": "linkedin-connector", "project": str(ROOT)})
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
            elif path == "/api/jobs":
                self.server.app.start_job(payload)
                self.reply(202, {"ok": True})
            elif path == "/api/jobs/cancel":
                self.server.app.cancel()
                self.reply(200, {"ok": True})
            else:
                self.reply(404, {"error": "Action introuvable."})
        except BusyError as error:
            self.reply(409, {"error": str(error)})
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
            if existing == {"app": "linkedin-connector", "project": str(ROOT)}:
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
