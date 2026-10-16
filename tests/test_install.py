"""Installer tests in temporary projects, without network, launchd or LinkedIn."""

import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest


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
        self.assertFalse(any(any("web_app.py" in arg for arg in call) for call in self.calls()))

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

    def test_schedule_plist_escapes_paths_and_never_runs_at_load(self):
        spec = importlib.util.spec_from_file_location("check_install", ROOT / "scripts/check_install.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        target = self.project / "test.plist"
        module.schedule(self.project, "test.linkedin-connector", target, 9, 5)
        job = plistlib.loads(target.read_bytes())
        self.assertFalse(job["RunAtLoad"])
        self.assertEqual(job["WorkingDirectory"], str(self.project))
        self.assertEqual(job["ProgramArguments"], [str(self.project / ".venv/bin/python"), str(self.project / "linkedin_connect.py"), "auto"])
        self.assertEqual(job["StartCalendarInterval"], [{"Weekday": day, "Hour": 9, "Minute": 5} for day in range(1, 6)])


if __name__ == "__main__":
    unittest.main()
