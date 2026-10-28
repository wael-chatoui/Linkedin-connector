"""Isolated launchd contracts; never call the real scheduler or LinkedIn."""

import hashlib
import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app_settings import DEFAULTS
from automation_schedule import ScheduleError, ScheduleManager


class FakeLaunchctl:
    def __init__(self):
        self.loaded = False
        self.live_job = None
        self.inherited_data_dir = None
        self.calls = []
        self.jobs = []
        self.fail_bootstrap = 0
        self.fail_bootout = False
        self.timeout = False
        self.runs = 0
        self.last_exit_code = "(never exited)"

    def __call__(self, command, **kwargs):
        assert command[0] == "launchctl"
        assert kwargs == {"capture_output": True, "text": True, "timeout": 15, "check": False}
        self.calls.append(command)
        if self.timeout:
            raise subprocess.TimeoutExpired(command, 15)
        code = 0
        stdout = ""
        if command[1] == "print":
            code = 0 if self.loaded else 113
            if self.loaded and self.live_job is not None:
                job = self.live_job
                arguments = "\n".join(f"\t\t{value}" for value in job.get("ProgramArguments", []))
                environment = "\n".join(f"\t\t{key} => {value}" for key, value
                                        in job.get("EnvironmentVariables", {}).items())
                inherited = (f"\t\tLINKEDIN_DATA_DIR => {self.inherited_data_dir}\n"
                             if self.inherited_data_dir else "\t\tPATH => /usr/bin:/bin\n")
                stdout = (f"{command[2]} = {{\n"
                          f"\truns = {self.runs}\n"
                          f"\tlast exit code = {self.last_exit_code}\n"
                          f"\tprogram = {job.get('Program', job['ProgramArguments'][0])}\n"
                          f"\targuments = {{\n{arguments}\n\t}}\n"
                          f"\tworking directory = {job.get('WorkingDirectory', '')}\n"
                          f"\tinherited environment = {{\n{inherited}\t}}\n"
                          f"\tenvironment = {{\n{environment}\n\t}}\n}}")
        elif command[1] == "bootout":
            code = 5 if self.fail_bootout else 0
            if not code:
                self.loaded = False
        elif command[1] == "bootstrap":
            self.jobs.append(plistlib.loads(Path(command[3]).read_bytes()))
            if self.fail_bootstrap:
                self.fail_bootstrap -= 1
                code = 5
            else:
                self.loaded = True
                self.live_job = self.jobs[-1]
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, code, stdout, "simulated launchd failure" if code else "")


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="linkedin-scheduler-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve() / "projet avec espace & é"
        self.data = self.project / "autres données"
        self.logs = Path(self.temp.name).resolve() / "logs hors Documents"
        self.runner = FakeLaunchctl()
        self.manager = ScheduleManager(self.project, self.data,
                                       launch_agents_dir=Path(self.temp.name) / "agents",
                                       log_dir=self.logs,
                                       runner=self.runner, platform="darwin", username="test-user", uid=501)
        self.settings = {**DEFAULTS, "keywords": "directeur association", "schedule_time": "09:05"}

    def old_schedule(self, *, loaded=True, run_at_load=True):
        job = {"Label": self.manager.label, "RunAtLoad": run_at_load,
               "ProgramArguments": [str(self.project / ".venv/bin/python"),
                                    str(self.project / "linkedin_connect.py"), "auto"],
               "WorkingDirectory": str(self.project),
               "EnvironmentVariables": {"LINKEDIN_DATA_DIR": str(self.data)},
               "StandardOutPath": str(self.data / "auto.log"),
               "StandardErrorPath": str(self.data / "auto.log"),
               "StartCalendarInterval": [{"Weekday": day, "Hour": 10, "Minute": 30} for day in range(1, 6)]}
        self.manager.plist_path.parent.mkdir(parents=True, exist_ok=True)
        self.manager.plist_path.write_bytes(plistlib.dumps(job))
        self.manager.plist_path.chmod(0o644)
        self.runner.loaded = loaded
        self.runner.live_job = job
        return self.manager.plist_path.read_bytes()

    def test_install_everyday_uses_saved_time_and_data_without_immediate_run(self):
        self.manager.install(self.settings)
        job = plistlib.loads(self.manager.plist_path.read_bytes())
        self.assertFalse(job["RunAtLoad"])
        self.assertNotIn("KeepAlive", job)
        self.assertEqual(job["ProgramArguments"], [str(self.project / ".venv/bin/python"),
                                                  str(self.project / "linkedin_connect.py"), "auto"])
        self.assertEqual(job["WorkingDirectory"], str(self.project))
        self.assertEqual(job["EnvironmentVariables"]["LINKEDIN_DATA_DIR"], str(self.data))
        self.assertEqual(job["StandardOutPath"], str(self.logs / "auto.log"))
        self.assertEqual(job["StandardErrorPath"], str(self.logs / "auto.log"))
        self.assertTrue(self.logs.is_dir())
        self.assertNotEqual(self.manager.log_path.parent, self.data)
        self.assertEqual(job["StartCalendarInterval"], [{"Weekday": day, "Hour": 9, "Minute": 5} for day in range(7)])
        self.assertEqual(self.runner.calls[-1], ["launchctl", "bootstrap", "gui/501", str(self.manager.plist_path)])
        state = self.manager.status()
        self.assertTrue(state["installed"])
        self.assertTrue(state["loaded"])
        self.assertEqual(state["schedule_time"], "09:05")
        self.assertEqual(state["schedule_days"], "everyday")
        self.assertEqual(state["log_path"], str(self.logs / "auto.log"))
        self.assertFalse(state["legacy"])

    def test_default_log_directory_is_outside_documents_on_macos(self):
        with patch("automation_schedule.Path.home", return_value=Path(self.temp.name)):
            manager = ScheduleManager(self.project, self.data, platform="darwin")
        data_identity = hashlib.sha256(str(self.data).encode("utf-8")).hexdigest()[:16]
        self.assertEqual(manager.log_path, Path(self.temp.name).resolve()
                         / "Library/Logs/Linkedin-connector" / data_identity / "auto.log")
        self.assertTrue(manager.log_path.is_absolute())

    def test_status_reports_live_launch_failures_before_python_started(self):
        self.manager.install(self.settings)
        self.runner.runs = 3
        self.runner.last_exit_code = 78
        state = self.manager.status()
        self.assertEqual(state["runs"], 3)
        self.assertEqual(state["last_exit_code"], 78)
        self.assertTrue(state["loaded"])

    def test_status_parses_launchctl_exit_code_with_symbolic_description(self):
        self.manager.install(self.settings)
        self.runner.runs = 1
        self.runner.last_exit_code = "78: EX_CONFIG"
        self.assertEqual(self.manager.status()["last_exit_code"], 78)

    def test_status_distinguishes_no_exit_yet_from_a_successful_exit(self):
        self.manager.install(self.settings)
        self.assertIsNone(self.manager.status()["last_exit_code"])
        self.runner.runs = 1
        self.runner.last_exit_code = 0
        state = self.manager.status()
        self.assertEqual(state["runs"], 1)
        self.assertEqual(state["last_exit_code"], 0)

    def test_default_log_directory_is_isolated_per_data_directory(self):
        first = ScheduleManager(self.project, self.data, platform="darwin")
        second = ScheduleManager(self.project, self.project / "other data", platform="darwin")
        self.assertNotEqual(first.log_path, second.log_path)
        self.assertEqual(first.log_path.parent.parent, second.log_path.parent.parent)

    def test_default_log_identity_is_stable_for_data_directory_aliases(self):
        self.data.mkdir(parents=True)
        alias = Path(self.temp.name) / "data alias"
        alias.symlink_to(self.data, target_is_directory=True)
        direct = ScheduleManager(self.project, self.data, platform="darwin")
        aliased = ScheduleManager(self.project, alias, platform="darwin")
        self.assertEqual(direct.log_path, aliased.log_path)

    def test_default_log_directory_follows_data_on_other_platforms(self):
        manager = ScheduleManager(self.project, self.data, platform="linux")
        self.assertEqual(manager.log_path, self.data / "auto.log")

    def test_reactivation_migrates_protected_logs_without_starting_automation(self):
        original = self.old_schedule(run_at_load=False)
        state = self.manager.status()
        self.assertTrue(state["legacy"])
        self.assertEqual(self.manager.plist_path.read_bytes(), original)
        self.manager.install(self.settings)
        job = self.runner.jobs[-1]
        self.assertEqual(job["StandardOutPath"], str(self.logs / "auto.log"))
        self.assertEqual(job["StandardErrorPath"], str(self.logs / "auto.log"))
        self.assertFalse(job["RunAtLoad"])
        self.assertNotIn("KeepAlive", job)
        self.assertFalse(self.manager.status()["legacy"])
        self.assertTrue(all(call[1] in ("print", "bootout", "bootstrap") for call in self.runner.calls))

    def test_migration_required_if_either_log_stream_uses_old_location(self):
        for stream in ("StandardOutPath", "StandardErrorPath"):
            with self.subTest(stream=stream):
                self.manager.install(self.settings)
                job = plistlib.loads(self.manager.plist_path.read_bytes())
                job[stream] = str(self.data / "auto.log")
                original = plistlib.dumps(job)
                self.manager.plist_path.write_bytes(original)
                self.runner.live_job = job
                self.assertTrue(self.manager.status()["legacy"])
                self.assertEqual(self.manager.plist_path.read_bytes(), original)

    def test_weekdays_and_replacement_use_same_exact_label(self):
        self.old_schedule()
        self.manager.install({**self.settings, "schedule_days": "weekdays"})
        job = self.runner.jobs[-1]
        self.assertEqual([entry["Weekday"] for entry in job["StartCalendarInterval"]], [1, 2, 3, 4, 5])
        self.assertEqual(self.manager.status()["schedule_days"], "weekdays")
        self.assertIn(["launchctl", "bootout", "gui/501/com.test-user.linkedin-connector"], self.runner.calls)
        self.assertEqual(len(list(self.manager.plist_path.parent.glob("*.plist"))), 1)

    def test_status_reads_legacy_without_mutation_and_ignores_other_user(self):
        self.manager.plist_path.parent.mkdir(parents=True)
        other = self.manager.plist_path.with_name("com.someone-else.linkedin-connector.plist")
        other.write_bytes(plistlib.dumps({"Label": "com.someone-else.linkedin-connector"}))
        self.assertFalse(self.manager.status()["installed"])
        old = self.old_schedule()
        state = self.manager.status()
        self.assertTrue(state["legacy"])
        self.assertEqual(state["schedule_time"], "10:30")
        self.assertEqual(self.manager.plist_path.read_bytes(), old)
        self.assertTrue(all(call == ["launchctl", "print", self.manager.service] for call in self.runner.calls))

    def test_bootstrap_failure_restores_original_file_mode_and_loaded_job_without_run_at_load(self):
        original = self.old_schedule()
        self.runner.fail_bootstrap = 1
        with self.assertRaisesRegex(ScheduleError, "précédente a été restaurée"):
            self.manager.install(self.settings)
        self.assertEqual(self.manager.plist_path.read_bytes(), original)
        self.assertEqual(self.manager.plist_path.stat().st_mode & 0o777, 0o644)
        self.assertTrue(self.runner.loaded)
        restored = self.runner.jobs[-1]
        self.assertEqual(restored["ProgramArguments"], plistlib.loads(original)["ProgramArguments"])
        self.assertFalse(restored["RunAtLoad"])

    def test_failure_does_not_load_a_previously_unloaded_job(self):
        original = self.old_schedule(loaded=False)
        self.runner.fail_bootstrap = 1
        with self.assertRaises(ScheduleError):
            self.manager.install(self.settings)
        self.assertEqual(self.manager.plist_path.read_bytes(), original)
        self.assertFalse(self.runner.loaded)
        self.assertEqual(len(self.runner.jobs), 1)

    def test_failure_removes_new_file_when_there_was_no_job(self):
        self.runner.fail_bootstrap = 1
        with self.assertRaises(ScheduleError):
            self.manager.install(self.settings)
        self.assertFalse(self.manager.plist_path.exists())
        self.assertFalse(self.runner.loaded)

    def test_failed_rollback_still_preserves_original_plist(self):
        original = self.old_schedule()
        self.runner.fail_bootstrap = 2
        with self.assertRaisesRegex(ScheduleError, "restauration.*échoué"):
            self.manager.install(self.settings)
        self.assertEqual(self.manager.plist_path.read_bytes(), original)

    def test_bootout_failure_preserves_previous_job(self):
        original = self.old_schedule()
        self.runner.fail_bootout = True
        with self.assertRaises(ScheduleError):
            self.manager.install(self.settings)
        self.assertTrue(self.runner.loaded)
        self.assertEqual(self.manager.plist_path.read_bytes(), original)
        self.assertEqual(self.runner.jobs, [])

    def test_no_keywords_or_invalid_time_never_calls_launchctl(self):
        for changes in ({"keywords": "  "}, {"schedule_time": "25:03"}, {"schedule_days": "sometimes"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, ScheduleError)):
                self.manager.install({**self.settings, **changes})
        self.assertEqual(self.runner.calls, [])

    def test_unsupported_platform_is_read_only(self):
        self.manager.platform = "linux"
        self.assertEqual(self.manager.status()["supported"], False)
        with self.assertRaisesRegex(ScheduleError, "macOS"):
            self.manager.install(self.settings)
        with self.assertRaises(ScheduleError):
            self.manager.uninstall()
        self.assertEqual(self.runner.calls, [])

    def test_timeout_is_visible_and_does_not_touch_existing_file(self):
        original = self.old_schedule()
        self.runner.timeout = True
        self.assertIn("délai", self.manager.status()["error"])
        with self.assertRaisesRegex(ScheduleError, "délai"):
            self.manager.install(self.settings)
        self.assertEqual(self.manager.plist_path.read_bytes(), original)

    def test_uninstall_removes_only_our_exact_job_and_preserves_data(self):
        self.manager.install(self.settings)
        sentinel = self.data / "sent_log.csv"
        sentinel.write_text("keep me")
        self.manager.uninstall()
        self.assertFalse(self.manager.plist_path.exists())
        self.assertFalse(self.runner.loaded)
        self.assertEqual(sentinel.read_text(), "keep me")
        self.manager.uninstall()  # Idempotent.

    def test_corrupt_or_mismatched_file_is_reported_and_preserved(self):
        self.manager.plist_path.parent.mkdir(parents=True)
        for original in (b"broken plist", plistlib.dumps({"Label": "unrelated.job"})):
            self.manager.plist_path.write_bytes(original)
            self.assertIn("error", self.manager.status())
            with self.assertRaises(ScheduleError):
                self.manager.install(self.settings)
            with self.assertRaises(ScheduleError):
                self.manager.uninstall()
            self.assertEqual(self.manager.plist_path.read_bytes(), original)

    def test_foreign_checkout_or_data_is_never_replaced_or_stopped(self):
        variants = (
            {"ProgramArguments": ["/another/project/.venv/bin/python", str(self.project / "linkedin_connect.py"), "auto"]},
            {"ProgramArguments": [str(self.project / ".venv/bin/python"), "/another/project/linkedin_connect.py", "auto"]},
            {"WorkingDirectory": "/another/project"},
            {"EnvironmentVariables": {"LINKEDIN_DATA_DIR": "/another/data"}},
            {"Program": "/another/interpreter"},
        )
        for changes in variants:
            with self.subTest(changes=changes):
                self.old_schedule()
                job = {**self.runner.live_job, **changes}
                original = plistlib.dumps(job)
                self.manager.plist_path.write_bytes(original)
                self.runner.live_job = job
                self.runner.calls.clear()
                state = self.manager.status()
                self.assertTrue(state["foreign"])
                self.assertIn("error", state)
                for action in (lambda: self.manager.install(self.settings), self.manager.uninstall):
                    with self.assertRaises(ScheduleError):
                        action()
                self.assertTrue(self.runner.loaded)
                self.assertEqual(self.manager.plist_path.read_bytes(), original)
                self.assertFalse(any(call[1] in ("bootout", "bootstrap") for call in self.runner.calls))

    def test_live_foreign_job_is_protected_even_when_plist_looks_local(self):
        original = self.old_schedule()
        self.runner.live_job = {**self.runner.live_job,
                                "EnvironmentVariables": {"LINKEDIN_DATA_DIR": "/other/live/data"}}
        self.assertTrue(self.manager.status()["foreign"])
        with self.assertRaises(ScheduleError):
            self.manager.install(self.settings)
        with self.assertRaises(ScheduleError):
            self.manager.uninstall()
        self.assertTrue(self.runner.loaded)
        self.assertEqual(self.manager.plist_path.read_bytes(), original)
        self.assertTrue(all(call[1] == "print" for call in self.runner.calls))

    def test_loaded_job_without_plist_is_never_stopped_or_overwritten(self):
        self.old_schedule()
        self.manager.plist_path.unlink()
        self.assertTrue(self.manager.status()["foreign"])
        with self.assertRaises(ScheduleError):
            self.manager.install(self.settings)
        with self.assertRaises(ScheduleError):
            self.manager.uninstall()
        self.assertTrue(self.runner.loaded)
        self.assertFalse(self.manager.plist_path.exists())
        self.assertTrue(all(call[1] == "print" for call in self.runner.calls))

    def test_unreadable_live_identity_fails_closed(self):
        original = self.old_schedule()
        self.runner.live_job = None
        self.assertTrue(self.manager.status()["foreign"])
        with self.assertRaises(ScheduleError):
            self.manager.install(self.settings)
        with self.assertRaises(ScheduleError):
            self.manager.uninstall()
        self.assertTrue(self.runner.loaded)
        self.assertEqual(self.manager.plist_path.read_bytes(), original)
        self.assertTrue(all(call[1] == "print" for call in self.runner.calls))

    def test_legacy_missing_environment_migrates_only_for_this_checkouts_default_data(self):
        self.old_schedule()
        legacy = dict(self.runner.live_job)
        legacy.pop("EnvironmentVariables")
        self.runner.live_job = legacy
        self.manager.plist_path.write_bytes(plistlib.dumps(legacy))
        self.assertTrue(self.manager.status()["foreign"])
        with self.assertRaises(ScheduleError):
            self.manager.install(self.settings)
        self.manager.data_dir = self.project / "linkedin_data"
        state = self.manager.status()
        self.assertFalse(state["foreign"])
        self.assertTrue(state["legacy"])
        self.assertTrue(state["loaded"])
        self.manager.install(self.settings)
        self.assertFalse(self.manager.status()["legacy"])

    def test_legacy_live_job_with_inherited_foreign_data_is_not_migrated(self):
        self.manager.data_dir = self.project / "linkedin_data"
        self.old_schedule()
        legacy = dict(self.runner.live_job)
        legacy.pop("EnvironmentVariables")
        self.runner.live_job = legacy
        self.manager.plist_path.write_bytes(plistlib.dumps(legacy))
        self.runner.inherited_data_dir = "/other/inherited/data"
        self.assertTrue(self.manager.status()["foreign"])
        with self.assertRaises(ScheduleError):
            self.manager.install(self.settings)
        with self.assertRaises(ScheduleError):
            self.manager.uninstall()
        self.assertTrue(self.runner.loaded)
        self.assertTrue(all(call[1] == "print" for call in self.runner.calls))

    def test_explicit_live_data_dir_takes_precedence_over_inherited_environment(self):
        self.old_schedule()
        self.runner.inherited_data_dir = "/other/inherited/data"
        self.assertFalse(self.manager.status()["foreign"])
        self.manager.install(self.settings)
        self.assertTrue(self.manager.status()["loaded"])


if __name__ == "__main__":
    unittest.main()
