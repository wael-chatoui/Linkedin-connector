"""Manage this user's macOS schedule without starting a LinkedIn action now."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import plistlib
import pwd
import subprocess
import sys
import tempfile


class ScheduleError(RuntimeError):
    """A readable scheduling failure, suitable for the local interface."""


class ForeignScheduleError(ScheduleError):
    """The shared launchd label belongs to another checkout or data directory."""


class ScheduleManager:
    def __init__(self, project_dir, data_dir=None, *, launch_agents_dir=None,
                 log_dir=None, runner=None, platform=None, username=None, uid=None):
        self.project_dir = Path(project_dir).resolve()
        self.data_dir = Path(data_dir or os.environ.get(
            "LINKEDIN_DATA_DIR", self.project_dir / "linkedin_data")).resolve()
        self.platform = sys.platform if platform is None else platform
        # launchd opens stdout/stderr before starting Python. macOS can deny
        # that access to a log under Documents even when Python can read it.
        data_identity = hashlib.sha256(str(self.data_dir).encode("utf-8")).hexdigest()[:16]
        default_log_dir = (Path.home() / "Library/Logs/Linkedin-connector" / data_identity
                           if self.supported else self.data_dir)
        self.log_path = (Path(log_dir) if log_dir is not None else default_log_dir).resolve() / "auto.log"
        self.uid = os.getuid() if uid is None else uid
        self.username = username or pwd.getpwuid(self.uid).pw_name
        self.label = f"com.{self.username}.linkedin-connector"
        self.domain = f"gui/{self.uid}"
        self.service = f"{self.domain}/{self.label}"
        self.plist_path = Path(launch_agents_dir or Path.home() / "Library/LaunchAgents") / f"{self.label}.plist"
        self.runner = runner or subprocess.run

    @property
    def supported(self):
        return self.platform == "darwin"

    def _run(self, *arguments, check=True):
        try:
            result = self.runner(["launchctl", *arguments], capture_output=True,
                                 text=True, timeout=15, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ScheduleError("La planification macOS ne répond pas (délai de 15 secondes).") from exc
        except OSError as exc:
            raise ScheduleError(f"Impossible d'appeler launchctl : {exc}") from exc
        if check and result.returncode:
            detail = (result.stderr or result.stdout or "erreur inconnue").strip()
            raise ScheduleError(f"Échec de launchctl {arguments[0]} : {detail[:600]}")
        return result

    def _read_job(self):
        try:
            job = plistlib.loads(self.plist_path.read_bytes())
            if not isinstance(job, dict) or job.get("Label") != self.label:
                raise ValueError("le label ne correspond pas à cet utilisateur")
        except (OSError, ValueError, plistlib.InvalidFileException) as exc:
            raise ScheduleError(f"Tâche macOS illisible ({self.plist_path.name}) : {exc}") from exc
        self._assert_owned(job)
        return job

    @staticmethod
    def _same_path(value, expected):
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            return False
        # Resolve directory aliases (e.g. /var -> /private/var), but do not
        # collapse venv Python symlinks shared by otherwise distinct checkouts.
        path = Path(value)
        return path.parent.resolve() / path.name == expected.parent.resolve() / expected.name

    def _assert_owned(self, job):
        arguments = job.get("ProgramArguments")
        environment = job.get("EnvironmentVariables", {})
        expected_python = self.project_dir / ".venv/bin/python"
        own_arguments = (isinstance(arguments, list) and len(arguments) >= 3
                         and self._same_path(arguments[0], expected_python)
                         and self._same_path(arguments[1], self.project_dir / "linkedin_connect.py")
                         and arguments[2] == "auto")
        if (not own_arguments
                or not self._same_path(job.get("WorkingDirectory"), self.project_dir)
                or ("Program" in job and not self._same_path(job["Program"], expected_python))
                or not isinstance(environment, dict)):
            raise ForeignScheduleError(
                "Cette tâche macOS appartient à une autre copie du projet ou son identité ne peut pas "
                "être vérifiée. Ouvre cette copie pour gérer son automatisation.")
        # Old jobs without an explicit environment used this checkout's
        # linkedin_data directory. They are safe to migrate only for that data.
        data_path = environment.get("LINKEDIN_DATA_DIR", str(self.project_dir / "linkedin_data"))
        if not self._same_path(data_path, self.data_dir):
            raise ForeignScheduleError(
                "Cette tâche macOS utilise un autre dossier de données. Ouvre la copie qui gère "
                "ce dossier pour modifier son automatisation.")

    def _loaded_job(self):
        result = self._run("print", self.service, check=False)
        if result.returncode:
            if result.returncode == 113 or "Could not find service" in (result.stderr or ""):
                return None
            detail = (result.stderr or result.stdout or "erreur inconnue").strip()
            raise ScheduleError(f"Impossible de vérifier la tâche macOS chargée : {detail[:600]}")
        # launchctl print reports the live definition, which can differ from a
        # plist overwritten since bootstrap. Fail closed if it cannot be read.
        job = {"Label": self.label, "EnvironmentVariables": {}, "_runtime": {}}
        environments = {"environment": {}, "inherited environment": {}, "default environment": {}}
        section = None
        for raw_line in (result.stdout or "").splitlines():
            line = raw_line.strip()
            if section:
                if line == "}":
                    section = None
                elif section == "arguments":
                    job["ProgramArguments"].append(line)
                elif " => " in line:
                    key, value = line.split(" => ", 1)
                    environments[section][key] = value
                continue
            if line == "arguments = {":
                section = "arguments"
                job["ProgramArguments"] = []
            elif line.removesuffix(" = {") in environments:
                section = line.removesuffix(" = {")
            elif line.startswith("program = "):
                job["Program"] = line.removeprefix("program = ")
            elif line.startswith("working directory = "):
                job["WorkingDirectory"] = line.removeprefix("working directory = ")
            elif line.startswith(("runs = ", "last exit code = ")):
                key, value = line.split(" = ", 1)
                try:
                    job["_runtime"]["runs" if key == "runs" else "last_exit_code"] = int(value.split(":", 1)[0].strip())
                except ValueError:
                    # launchd prints "(never exited)" before the first run.
                    pass
        job["EnvironmentVariables"] = {**environments["default environment"],
                                       **environments["inherited environment"],
                                       **environments["environment"]}
        self._assert_owned(job)
        return job

    def status(self):
        result = {"supported": self.supported, "installed": False, "loaded": False,
                  "schedule_time": None, "schedule_days": None, "legacy": False, "foreign": False,
                  "log_path": str(self.log_path), "runs": None, "last_exit_code": None}
        if not self.supported:
            return result
        try:
            result["installed"] = self.plist_path.exists()
            if result["installed"]:
                job = self._read_job()
                entries = job.get("StartCalendarInterval", [])
                if isinstance(entries, dict):
                    entries = [entries]
                if isinstance(entries, list) and entries and all(isinstance(entry, dict) for entry in entries):
                    times = {(entry.get("Hour"), entry.get("Minute")) for entry in entries}
                    if len(times) == 1:
                        hour, minute = next(iter(times))
                        if type(hour) is int and type(minute) is int and 0 <= hour < 24 and 0 <= minute < 60:
                            result["schedule_time"] = f"{hour:02d}:{minute:02d}"
                    days = {entry.get("Weekday") for entry in entries}
                    if days == {1, 2, 3, 4, 5}:
                        result["schedule_days"] = "weekdays"
                    elif days in ({0, 1, 2, 3, 4, 5, 6}, {1, 2, 3, 4, 5, 6, 7}, {None}):
                        result["schedule_days"] = "everyday"
                environment = job.get("EnvironmentVariables") or {}
                if not isinstance(environment, dict):
                    raise ScheduleError("Les variables de la tâche macOS sont invalides.")
                result["legacy"] = bool(
                    job.get("RunAtLoad") or not environment.get("LINKEDIN_DATA_DIR")
                    or not self._same_path(job.get("StandardOutPath"), self.log_path)
                    or not self._same_path(job.get("StandardErrorPath"), self.log_path))
            loaded_job = self._loaded_job()
            result["loaded"] = loaded_job is not None
            if loaded_job is not None:
                result.update(loaded_job["_runtime"])
            if result["loaded"] and not result["installed"]:
                raise ForeignScheduleError(
                    "Une tâche macOS est chargée sans fichier local permettant de la gérer. "
                    "Retrouve sa configuration avant de la remplacer.")
        except ForeignScheduleError as exc:
            result["foreign"] = True
            result["error"] = str(exc)
        except (ScheduleError, TypeError) as exc:
            result["error"] = str(exc)
        return result

    def _write(self, content, mode=0o600):
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.plist_path.parent, prefix=".linkedin-schedule-",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            temporary.chmod(mode)
            temporary.replace(self.plist_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _require_supported(self):
        if not self.supported:
            raise ScheduleError("La planification automatique utilise launchd et nécessite macOS.")

    def install(self, settings):
        self._require_supported()
        from app_settings import validate_settings

        settings = validate_settings(settings)
        if not settings["keywords"]:
            raise ScheduleError("Saisis et enregistre des mots-clés avant d'activer l'automatisation.")
        hour, minute = map(int, settings["schedule_time"].split(":"))
        days = range(7) if settings["schedule_days"] == "everyday" else range(1, 6)
        job = {
            "Label": self.label,
            "ProgramArguments": [str(self.project_dir / ".venv/bin/python"),
                                 str(self.project_dir / "linkedin_connect.py"), "auto"],
            "WorkingDirectory": str(self.project_dir),
            "RunAtLoad": False,
            "StartCalendarInterval": [{"Weekday": day, "Hour": hour, "Minute": minute} for day in days],
            "StandardOutPath": str(self.log_path),
            "StandardErrorPath": str(self.log_path),
            "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "LINKEDIN_DATA_DIR": str(self.data_dir)},
        }
        previous = None
        previous_mode = 0o600
        if self.plist_path.exists():
            self._read_job()  # Never overwrite an invalid or unrelated plist.
            previous = self.plist_path.read_bytes()
            previous_mode = self.plist_path.stat().st_mode & 0o777
        was_loaded = self._loaded_job() is not None
        if was_loaded and previous is None:
            raise ScheduleError("Une tâche est déjà chargée sans fichier local. Retire-la avant de la remplacer.")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if was_loaded:
            self._run("bootout", self.service)
        try:
            self._write(plistlib.dumps(job))
            self._run("bootstrap", self.domain, str(self.plist_path))
        except (ScheduleError, OSError) as exc:
            restore_errors = []
            try:
                # A failed bootstrap can leave a partially registered job.
                self._run("bootout", self.service, check=False)
            except ScheduleError as rollback_exc:
                restore_errors.append(str(rollback_exc))
            try:
                if previous is None:
                    self.plist_path.unlink(missing_ok=True)
                elif was_loaded:
                    old_job = plistlib.loads(previous)
                    # Historical jobs used RunAtLoad. Restoring them must not
                    # send invitations as a side effect of recovering a failure.
                    old_job["RunAtLoad"] = False
                    old_job.pop("KeepAlive", None)
                    try:
                        self._write(plistlib.dumps(old_job), previous_mode)
                        self._run("bootstrap", self.domain, str(self.plist_path))
                    finally:
                        self._write(previous, previous_mode)
                else:
                    self._write(previous, previous_mode)
            except (ScheduleError, OSError) as rollback_exc:
                restore_errors.append(str(rollback_exc))
            suffix = (" La restauration de l'ancienne tâche a aussi échoué : " + "; ".join(restore_errors)
                      if restore_errors else " La planification précédente a été restaurée.")
            raise ScheduleError(f"Impossible d'activer l'automatisation : {exc}.{suffix}") from exc

    def uninstall(self):
        self._require_supported()
        installed = self.plist_path.exists()
        if installed:
            self._read_job()
        if self._loaded_job() is not None:
            if not installed:
                raise ScheduleError("Une tâche est chargée sans fichier local. Retrouve sa configuration avant de la retirer.")
            self._run("bootout", self.service)
        try:
            self.plist_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ScheduleError(f"La tâche est arrêtée mais son fichier n'a pas pu être supprimé : {exc}") from exc
