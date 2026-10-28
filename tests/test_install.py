"""Installer tests in temporary projects, without network, launchd or LinkedIn."""

import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from app_settings import DEFAULTS, read_settings, write_settings
from automation_schedule import ScheduleError
from runtime_lock import OperationLock


ROOT = Path(__file__).resolve().parents[1]
FAKE_PYTHON = r'''import json, os, shutil, sys
from pathlib import Path
project = Path(os.environ["INSTALL_TEST_PROJECT"])
args = sys.argv[1:]
with (project / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\n")
if args[:1] == ["-c"]:
    if os.environ.get("INSTALL_TEST_OLD_PYTHON") and Path(sys.argv[0]).parent.name == "mock-bin":
        raise SystemExit(1)
    raise SystemExit(0)
if args[:2] == ["-m", "venv"]:
    target = Path(args[2]) / "bin/python"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sys.argv[0], target)
    raise SystemExit(0)
if args[:3] == ["-m", "pip", "--version"]:
    raise SystemExit(0)
if args[:3] == ["-m", "pip", "install"]:
    if os.environ.get("INSTALL_TEST_FAIL_PIP"):
        raise SystemExit(17)
    (project / "deps-ready").touch()
    raise SystemExit(0)
if args[:3] == ["-m", "playwright", "install"]:
    (project / "browser-ready").touch()
    raise SystemExit(0)
if args[:2] == ["scripts/check_install.py", "dependencies"]:
    raise SystemExit(0 if (project / "deps-ready").exists() else 1)
if args[:2] == ["scripts/check_install.py", "chromium"]:
    raise SystemExit(0 if (project / "browser-ready").exists() else 1)
if args[:2] == ["scripts/check_install.py", "schedule"]:
    raise SystemExit(0)
if len(args) >= 2 and args[0].endswith("/scripts/check_install.py") and args[1] == "unschedule":
    raise SystemExit(0)
if args and args[0].endswith("/web_app.py"):
    raise SystemExit(0)
raise SystemExit("Unexpected fake Python call: " + repr(args))
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="linkedin-installer-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve() / "projet avec espace & accent é"
        self.project.mkdir()
        shutil.copy2(ROOT / "install.sh", self.project / "install.sh")
        shutil.copy2(ROOT / "Démarrer.command", self.project / "Démarrer.command")
        shutil.copy2(ROOT / "uninstall.sh", self.project / "uninstall.sh")
        self.bin = self.project / "mock-bin"
        self.bin.mkdir()
        self.python = self.bin / "python3"
        self.python.write_text(f"#!{sys.executable}\n" + FAKE_PYTHON, encoding="utf-8")
        self.python.chmod(0o755)
        for executable in ("curl", "wget", "launchctl"):
            script = self.bin / executable
            script.write_text('#!/bin/sh\necho "Forbidden external action" >&2\nexit 98\n')
            script.chmod(0o755)
        # Use a stable simulated platform regardless of the test host.
        uname = self.bin / "uname"
        uname.write_text('#!/bin/sh\necho Linux\n')
        uname.chmod(0o755)
        self.env = os.environ.copy()
        for key in ("PYTHON", "LINKEDIN_PORT", "HOUR", "MINUTE"):
            self.env.pop(key, None)
        self.env.update({"PATH": f"{self.bin}:/usr/bin:/bin", "INSTALL_TEST_PROJECT": str(self.project)})
        (self.project / ".env").write_text("private-sentinel\n")
        (self.project / "linkedin_data").mkdir()
        (self.project / "linkedin_data/sentinel").write_text("preserve-me\n")

    def run_installer(self, *args):
        result = subprocess.run(
            ["/bin/bash", str(self.project / "install.sh"), *args],
            cwd=self.temp.name, env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual((self.project / ".env").read_text(), "private-sentinel\n")
        self.assertEqual((self.project / "linkedin_data/sentinel").read_text(), "preserve-me\n")
        return result

    def calls(self):
        path = self.project / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def ready(self):
        target = self.project / ".venv/bin/python"
        target.parent.mkdir(parents=True)
        shutil.copy2(self.python, target)
        (self.project / "deps-ready").touch()
        (self.project / "browser-ready").touch()

    def test_fresh_install_installs_then_opens_ui(self):
        self.env["PYTHON"] = str(self.python)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(any(call[:2] == ["-m", "venv"] for call in calls))
        self.assertTrue(any(call[:3] == ["-m", "pip", "install"] for call in calls))
        self.assertIn(["-m", "playwright", "install", "chromium"], calls)
        self.assertEqual(calls[-1], [str(self.project / "web_app.py"), "--port", "8765"])

    def test_missing_python_bootstraps_only_in_project(self):
        self.env["INSTALL_TEST_OLD_PYTHON"] = "1"
        self.env["INSTALL_TEST_PYTHON_SOURCE"] = str(self.python)
        fake_uv = self.bin / "fake-uv"
        fake_uv.write_text(f"#!{sys.executable}\n" + '''import json, os, shutil, sys
from pathlib import Path
project = Path(os.environ["INSTALL_TEST_PROJECT"])
assert Path(os.environ["UV_UNMANAGED_INSTALL"]) == project / ".runtime/bin"
assert Path(os.environ["UV_PYTHON_INSTALL_DIR"]) == project / ".runtime/python"
assert Path(os.environ["UV_CACHE_DIR"]) == project / ".runtime/cache"
assert os.environ["UV_PYTHON_INSTALL_BIN"] == "0"
target = project / ".runtime/python/python"
args = sys.argv[1:]
with (project / "uv-calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args == ["python", "install", "--no-bin", "3.12"]:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(os.environ["INSTALL_TEST_PYTHON_SOURCE"], target)
elif args == ["python", "find", "--managed-python", "3.12"]:
    print(target)
else:
    raise SystemExit("Unexpected uv call: " + repr(args))
''')
        fake_uv.chmod(0o755)
        self.env["INSTALL_TEST_UV_SOURCE"] = str(fake_uv)
        curl = self.bin / "curl"
        curl.write_text('''#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output ]; then
    shift
    cat > "$1" <<'INSTALLER'
#!/bin/sh
mkdir -p "$UV_UNMANAGED_INSTALL"
cp "$INSTALL_TEST_UV_SOURCE" "$UV_UNMANAGED_INSTALL/uv"
INSTALLER
    exit 0
  fi
  shift
done
exit 99
''')
        curl.chmod(0o755)
        result = self.run_installer("--install-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.project / ".runtime/bin/uv").exists())
        self.assertTrue((self.project / ".runtime/python/python").exists())
        uv_calls = [json.loads(line) for line in (self.project / "uv-calls.jsonl").read_text().splitlines()]
        self.assertEqual(uv_calls, [["python", "install", "--no-bin", "3.12"], ["python", "find", "--managed-python", "3.12"]])

    def test_broken_environment_is_preserved_before_repair(self):
        (self.project / ".venv").mkdir()
        (self.project / ".venv/sentinel").write_text("old-environment")
        self.env["PYTHON"] = str(self.python)
        result = self.run_installer("--install-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list((self.project / ".runtime").glob("venv-backup.*/venv/sentinel"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "old-environment")

    def test_relaunch_is_offline_and_forwards_options(self):
        self.ready()
        result = self.run_installer("--no-schedule", "--no-browser", "--port", "8877")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertFalse(any("install" in call for call in calls))
        self.assertEqual(calls[-1], [str(self.project / "web_app.py"), "--port", "8877", "--no-browser"])

    def test_install_only_does_not_start_server(self):
        self.ready()
        result = self.run_installer("--install-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(any("web_app.py" in arg for arg in call) for call in self.calls()))

    def test_missing_browser_is_repaired_without_reinstalling_packages(self):
        self.ready()
        (self.project / "browser-ready").unlink()
        result = self.run_installer("--install-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn(["-m", "playwright", "install", "chromium"], calls)
        self.assertFalse(any(call[:3] == ["-m", "pip", "install"] for call in calls))

    def test_install_failure_never_launches_ui(self):
        self.ready()
        (self.project / "deps-ready").unlink()
        self.env["INSTALL_TEST_FAIL_PIP"] = "1"
        result = self.run_installer()
        self.assertEqual(result.returncode, 17)
        self.assertIn("vérifiez votre connexion Internet", result.stderr)
        self.assertIn("réglages et votre historique sont conservés", result.stderr)
        self.assertFalse(any(any("web_app.py" in arg for arg in call) for call in self.calls()))

    def test_double_click_launcher_works_outside_project_with_nonexecutable_installer(self):
        self.ready()
        (self.project / "install.sh").chmod(0o644)
        result = subprocess.run(
            [str(self.project / "Démarrer.command"), "--no-browser", "--port", "8877"],
            cwd=self.temp.name, env=self.env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], [str(self.project / "web_app.py"), "--port", "8877", "--no-browser"])
        self.assertIn("Vous pouvez fermer cette fenêtre", result.stdout)
        self.assertEqual((self.project / "linkedin_data/sentinel").read_text(), "preserve-me\n")

    def test_double_click_launcher_keeps_failure_visible_until_enter(self):
        self.ready()
        (self.project / "deps-ready").unlink()
        self.env["INSTALL_TEST_FAIL_PIP"] = "1"
        master, slave = pty.openpty()
        process = subprocess.Popen(
            [str(self.project / "Démarrer.command")], cwd=self.temp.name, env=self.env,
            stdin=slave, stdout=slave, stderr=slave,
        )
        os.close(slave)
        try:
            output = b""
            deadline = time.monotonic() + 10
            while "Appuyez sur Entrée".encode() not in output and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    output += os.read(master, 65536)
                if process.poll() is not None:
                    break
            self.assertIn("Appuyez sur Entrée".encode(), output)
            self.assertIsNone(process.poll(), "Le lanceur doit laisser le message lisible.")
            os.write(master, b"\n")
            self.assertEqual(process.wait(timeout=3), 17)
            text = output.decode()
            self.assertIn("code 17", text)
            self.assertIn("relancez Démarrer.command", text)
            self.assertFalse(any(any("web_app.py" in arg for arg in call) for call in self.calls()))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            os.close(master)

    def test_double_click_launcher_distinguishes_interruption_from_error(self):
        (self.project / "install.sh").write_text("#!/bin/bash\nexit 130\n")
        result = subprocess.run(
            [str(self.project / "Démarrer.command")], cwd=self.temp.name, env=self.env,
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10,
        )
        self.assertEqual(result.returncode, 130)
        self.assertIn("a été arrêté", result.stdout)
        self.assertNotIn("n’a pas pu démarrer", result.stdout)
        self.assertIn("Mettre en pause", result.stdout)

    def test_invalid_ports_fail_before_setup(self):
        for port in ("0", "65536", "-1", "abc", "9999999999999999999999"):
            with self.subTest(port=port):
                result = self.run_installer("--port", port)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_unknown_and_missing_options_fail_before_setup(self):
        self.assertNotEqual(self.run_installer("--unknown").returncode, 0)
        self.assertNotEqual(self.run_installer("--port").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_help_does_not_install(self):
        result = self.run_installer("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_schedule_is_rejected_on_linux_before_setup(self):
        result = self.run_installer("--schedule", "--install-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("macOS", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_schedule_delegates_to_saved_settings_instead_of_environment_defaults(self):
        self.ready()
        (self.bin / "uname").write_text('#!/bin/sh\necho Darwin\n')
        self.env.update({"HOUR": "invalid-old-setting", "MINUTE": "invalid-old-setting"})
        result = self.run_installer("--schedule", "--install-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["scripts/check_install.py", "schedule", str(self.project)], self.calls())

    def test_uninstall_delegates_to_owned_schedule_checks_from_another_directory(self):
        self.ready()
        (self.bin / "uname").write_text('#!/bin/sh\necho Darwin\n')
        result = subprocess.run(
            ["/bin/bash", str(self.project / "uninstall.sh")], cwd=self.temp.name,
            env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [[str(self.project / "scripts/check_install.py"),
                                        "unschedule", str(self.project)]])
        self.assertEqual((self.project / "linkedin_data/sentinel").read_text(), "preserve-me\n")

    def test_uninstall_on_linux_has_no_external_action(self):
        self.ready()
        result = subprocess.run(
            ["/bin/bash", str(self.project / "uninstall.sh")], cwd=self.temp.name,
            env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Aucun service", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_uninstall_without_environment_explains_how_to_pause(self):
        (self.bin / "uname").write_text('#!/bin/sh\necho Darwin\n')
        result = subprocess.run(
            ["/bin/bash", str(self.project / "uninstall.sh")], cwd=self.temp.name,
            env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Mettre en pause", result.stderr)
        self.assertEqual(self.calls(), [])


class ScheduleInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="linkedin-schedule-installer-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        self.settings_path = self.project / "linkedin_data/settings.json"
        spec = importlib.util.spec_from_file_location("check_install", ROOT / "scripts/check_install.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        patcher = mock.patch("automation_schedule.ScheduleManager")
        self.scheduler = patcher.start().return_value
        self.addCleanup(patcher.stop)
        self.scheduler.data_dir = self.settings_path.parent
        self.scheduler.supported = True
        self.scheduler.status.return_value = {"supported": True, "foreign": False}
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))

    def test_cli_activation_reads_all_settings_and_persists_enabled(self):
        settings = {**DEFAULTS, "keywords": "association locale", "geography": "international",
                    "limit": 7, "schedule_time": "08:15", "schedule_days": "everyday"}
        write_settings(self.settings_path, settings)
        self.scheduler.install.side_effect = lambda _: self.assertFalse(
            read_settings(self.settings_path)["automation_enabled"])
        self.module.schedule(self.project)
        expected = {**settings, "automation_enabled": True}
        self.scheduler.install.assert_called_once_with(expected)
        self.assertEqual(read_settings(self.settings_path), expected)

    def test_missing_or_empty_keywords_never_install_a_schedule(self):
        with self.assertRaisesRegex(ValueError, "mots-clés"):
            self.module.schedule(self.project)
        write_settings(self.settings_path, DEFAULTS)
        with self.assertRaisesRegex(ValueError, "mots-clés"):
            self.module.schedule(self.project)
        self.scheduler.install.assert_not_called()
        self.assertFalse(read_settings(self.settings_path)["automation_enabled"])

    def test_failed_activation_keeps_previous_paused_settings(self):
        settings = {**DEFAULTS, "keywords": "fondateur"}
        write_settings(self.settings_path, settings)
        self.scheduler.install.side_effect = ScheduleError("Échec simulé")
        with self.assertRaisesRegex(ScheduleError, "simulé"):
            self.module.schedule(self.project)
        self.assertEqual(read_settings(self.settings_path), settings)

    def test_failed_reactivation_leaves_previously_enabled_sends_paused(self):
        settings = {**DEFAULTS, "keywords": "fondateur", "automation_enabled": True, "limit": 15}
        write_settings(self.settings_path, settings)
        self.scheduler.install.side_effect = ScheduleError("Échec simulé")
        with self.assertRaisesRegex(ScheduleError, "simulé"):
            self.module.schedule(self.project)
        self.assertEqual(read_settings(self.settings_path), {**settings, "automation_enabled": False})

    def test_uninstall_pauses_before_removal_and_preserves_settings_and_profile(self):
        settings = {**DEFAULTS, "keywords": "fondateur", "automation_enabled": True, "limit": 15}
        write_settings(self.settings_path, settings)
        profile = self.settings_path.parent / ".profile" / "session-sentinel"
        profile.parent.mkdir()
        profile.write_text("keep-session")
        self.scheduler.uninstall.side_effect = lambda: self.assertFalse(
            read_settings(self.settings_path)["automation_enabled"])
        self.module.unschedule(self.project)
        self.scheduler.uninstall.assert_called_once_with()
        self.assertEqual(read_settings(self.settings_path), {**settings, "automation_enabled": False})
        self.assertEqual(profile.read_text(), "keep-session")

    def test_uninstall_refuses_a_foreign_schedule_without_changing_settings(self):
        settings = {**DEFAULTS, "keywords": "fondateur", "automation_enabled": True}
        write_settings(self.settings_path, settings)
        self.scheduler.status.return_value = {"foreign": True, "error": "Autre installation"}
        with self.assertRaisesRegex(ScheduleError, "Autre installation"):
            self.module.unschedule(self.project)
        self.scheduler.uninstall.assert_not_called()
        self.assertEqual(read_settings(self.settings_path), settings)

    def test_uninstall_during_a_run_pauses_and_requests_calendar_removal_afterwards(self):
        settings = {**DEFAULTS, "keywords": "fondateur", "automation_enabled": True}
        write_settings(self.settings_path, settings)
        with OperationLock(self.settings_path.parent / ".operation.lock"):
            with self.assertRaisesRegex(ScheduleError, "en pause.*encore en cours"):
                self.module.unschedule(self.project)
        self.assertFalse(read_settings(self.settings_path)["automation_enabled"])
        self.scheduler.uninstall.assert_not_called()

    def test_uninstall_removes_owned_schedule_when_settings_are_missing_or_invalid(self):
        self.module.unschedule(self.project)
        self.assertFalse(self.settings_path.exists())
        self.settings_path.write_text("invalid-settings")
        self.module.unschedule(self.project)
        self.assertEqual(self.settings_path.read_text(), "invalid-settings")
        self.assertEqual(self.scheduler.uninstall.call_count, 2)

    def test_failed_calendar_removal_keeps_sends_paused(self):
        settings = {**DEFAULTS, "keywords": "fondateur", "automation_enabled": True}
        write_settings(self.settings_path, settings)
        self.scheduler.uninstall.side_effect = ScheduleError("Échec simulé")
        with self.assertRaisesRegex(ScheduleError, "simulé"):
            self.module.unschedule(self.project)
        self.assertFalse(read_settings(self.settings_path)["automation_enabled"])


if __name__ == "__main__":
    unittest.main()
