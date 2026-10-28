"""Tests isolés : jamais de navigateur LinkedIn ni d'invitation réelle."""

import csv
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import web_app
from runtime_lock import OperationLock


class FakeScheduler:
    def __init__(self):
        self.installed = False
        self.settings = None
        self.install_calls = 0
        self.uninstall_calls = 0

    def status(self):
        return {"supported": True, "installed": self.installed, "loaded": self.installed,
                "schedule_time": self.settings["schedule_time"] if self.settings else None,
                "schedule_days": self.settings["schedule_days"] if self.settings else None}

    def install(self, settings):
        self.install_calls += 1
        self.installed = True
        self.settings = dict(settings)

    def uninstall(self):
        self.uninstall_calls += 1
        self.installed = False


class NextRunTests(unittest.TestCase):
    def setUp(self):
        original = os.environ.get("TZ")
        def restore():
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()
        self.addCleanup(restore)
        os.environ["TZ"] = "Europe/Paris"
        time.tzset()

    def test_daily_before_after_and_already_processed(self):
        settings = {"schedule_time": "10:30", "schedule_days": "everyday"}
        self.assertEqual(web_app.next_scheduled_run(settings, datetime(2026, 10, 21, 9)),
                         "2026-10-21T10:30:00+02:00")
        self.assertEqual(web_app.next_scheduled_run(settings, datetime(2026, 10, 21, 10, 30)),
                         "2026-10-22T10:30:00+02:00")
        self.assertEqual(web_app.next_scheduled_run(settings, datetime(2026, 10, 21, 9), "2026-10-21"),
                         "2026-10-22T10:30:00+02:00")

    def test_weekend_and_clock_change_use_the_future_local_offset(self):
        settings = {"schedule_time": "10:30", "schedule_days": "weekdays"}
        self.assertEqual(web_app.next_scheduled_run(settings, datetime(2026, 10, 23, 11)),
                         "2026-10-26T10:30:00+01:00")
        self.assertEqual(web_app.next_scheduled_run({**settings, "schedule_days": "everyday"}, datetime(2026, 10, 24, 11)),
                         "2026-10-25T10:30:00+01:00")

    def test_repeated_hour_never_predicts_an_instant_in_the_past(self):
        from zoneinfo import ZoneInfo
        settings = {"schedule_time": "02:30", "schedule_days": "everyday"}
        now = datetime(2026, 10, 25, 2, 15, tzinfo=ZoneInfo("Europe/Paris"), fold=1)
        result = web_app.next_scheduled_run(settings, now)
        self.assertEqual(result, "2026-10-25T02:30:00+01:00")
        self.assertGreater(datetime.fromisoformat(result).timestamp(), now.timestamp())
        self.assertEqual(web_app.next_scheduled_run(settings, now, "2026-10-25"),
                         "2026-10-26T02:30:00+01:00")

    def test_nonexistent_hour_does_not_invent_a_trigger_time(self):
        settings = {"schedule_time": "02:30", "schedule_days": "everyday"}
        self.assertIsNone(web_app.next_scheduled_run(settings, datetime(2026, 3, 28, 20)))


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.scheduler = FakeScheduler()
        self.app = web_app.Application(self.directory.name, scheduler=self.scheduler)
        (self.app.data_dir / ".profile").mkdir()
        self.addCleanup(self.app.close)
        self.rows = []
        for slug, score, status in (("chosen", "70", ""), ("other", "80", ""), ("sent", "90", "sent")):
            self.rows.append({**dict.fromkeys(web_app.engine.FIELDS, ""),
                              "url": f"https://www.linkedin.com/in/{slug}/", "name": slug,
                              "headline": "Founder SaaS", "score": score, "status": status})
        with (self.app.data_dir / "prospects.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=web_app.engine.FIELDS)
            writer.writeheader()
            writer.writerows(self.rows)

    def wait_job(self):
        self.app.worker.join(timeout=10)
        self.assertFalse(self.app.worker.is_alive(), "Le worker aurait dû se terminer.")
        return self.app.state()["job"]

    def test_settings_persist_and_reject_invalid_input(self):
        settings = self.app.save_settings({"keywords": "  fondateur santé  ", "pages": 3, "geography": "international"})
        self.assertEqual(settings["keywords"], "fondateur santé")
        self.assertEqual(settings["geography"], "international")
        self.assertEqual(web_app.Application(self.directory.name).settings, settings)
        for value in ({"pages": True}, {"pages": 100}, {"network": "all"}, {"note": "yes"}, {"other": 1}, {"geography": "paris"}):
            with self.subTest(value=value), self.assertRaises(web_app.InputError):
                self.app.save_settings(value)
        self.assertEqual(self.app.settings, settings)

    def test_previous_geography_setting_preserves_saved_search(self):
        for paris in (False, True):
            with self.subTest(paris=paris):
                self.app.settings_path.write_text(json.dumps({"keywords": "founder", "pages": 5, "paris": paris}))
                restored = web_app.Application(self.directory.name).settings
                self.assertEqual(restored["geography"], "france")
                self.assertEqual(restored["keywords"], "founder")
                self.assertEqual(restored["pages"], 5)
                self.assertNotIn("paris", restored)

    def test_activation_schedules_saved_settings_without_immediate_job(self):
        with patch("web_app.subprocess.Popen") as process:
            result = self.app.configure_automation({"enabled": True, "settings": {
                "keywords": "CEO santé\nFounder biotech", "geography": "international", "limit": 4,
                "schedule_time": "09:15", "schedule_days": "everyday"}})
            process.assert_not_called()
        saved = json.loads(self.app.settings_path.read_text())
        self.assertTrue(saved["automation_enabled"])
        self.assertEqual(saved["geography"], "international")
        self.assertEqual(saved["limit"], 4)
        self.assertEqual(self.scheduler.settings, saved)
        self.assertTrue(result["ok"])
        self.assertIsNone(self.app.job)
        self.assertTrue(self.app.state()["automation"]["scheduler"]["loaded"])

    def test_activation_rejects_empty_search_without_installing(self):
        with self.assertRaises(web_app.InputError):
            self.app.configure_automation({"enabled": True, "settings": {"keywords": " "}})
        self.assertEqual(self.scheduler.install_calls, 0)
        self.assertFalse(self.app.settings["automation_enabled"])

    def test_first_activation_requires_a_linkedin_connection(self):
        (self.app.data_dir / ".profile").rmdir()
        with self.assertRaisesRegex(web_app.InputError, "étape 1"):
            self.app.configure_automation({"enabled": True, "settings": {"keywords": "fondateur"}})
        self.assertEqual(self.scheduler.install_calls, 0)
        self.assertEqual(self.app.state()["automation"]["attention"]["kind"], "login")

    def test_failed_login_is_remembered_after_server_restart(self):
        self.app._record_session("disconnected")
        restored = web_app.Application(self.directory.name, scheduler=self.scheduler)
        with self.assertRaisesRegex(web_app.InputError, "étape 1"):
            restored.configure_automation({"enabled": True, "settings": {"keywords": "fondateur"}})
        self.assertEqual(restored.state()["session"]["status"], "disconnected")

    def test_reconnection_clears_old_automatic_login_error_after_restart(self):
        report = {"status": "error", "reason": "login", "finished_at": "2020-01-01T10:30:00+01:00"}
        (self.app.data_dir / "automation_status.json").write_text(json.dumps(report))
        self.assertEqual(self.app.state()["automation"]["attention"]["kind"], "login")
        self.app._record_session("connected")
        restored = web_app.Application(self.directory.name, scheduler=self.scheduler)
        self.assertIsNone(restored.state()["automation"]["attention"])
        self.assertEqual(restored.state()["session"]["status"], "unknown")

    def test_next_run_is_only_shown_for_a_loaded_matching_schedule(self):
        self.app.configure_automation({"enabled": True, "settings": {"keywords": "fondateur"}})
        self.assertIsNotNone(self.app.state()["automation"]["next_run_at"])
        self.scheduler.installed = False
        self.app.schedule_cache = None
        self.assertIsNone(self.app.state()["automation"]["next_run_at"])

    def test_automation_logs_keep_old_history_and_read_new_scheduler_output(self):
        old_log = self.app.data_dir / "auto.log"
        old_log.write_text("historique du 12 octobre\n", encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            new_log = Path(directory) / "auto.log"
            status = {**self.scheduler.status(), "log_path": str(new_log)}
            with patch.object(self.scheduler, "status", return_value=status):
                self.assertEqual(self.app.automation_state()["logs"], ["historique du 12 octobre"])
                new_log.write_text("nouvelle exécution\n", encoding="utf-8")
                self.assertEqual(self.app.automation_state()["logs"],
                                 ["historique du 12 octobre", "nouvelle exécution"])
                new_log.write_text("\n".join(f"ligne {i}" for i in range(110)), encoding="utf-8")
                logs = self.app.automation_state()["logs"]
                self.assertEqual(len(logs), 100)
                self.assertEqual(logs[0], "ligne 10")
                self.assertEqual(logs[-1], "ligne 109")

    def test_automation_logs_do_not_duplicate_a_shared_legacy_path(self):
        log_path = self.app.data_dir / "auto.log"
        log_path.write_text("ancien journal\n", encoding="utf-8")
        status = {**self.scheduler.status(), "log_path": str(log_path)}
        with patch.object(self.scheduler, "status", return_value=status):
            self.assertEqual(self.app.automation_state()["logs"], ["ancien journal"])

    def test_previous_error_is_visible_without_changing_saved_schedule(self):
        self.app.configure_automation({"enabled": True, "settings": {"keywords": "fondateur"}})
        (self.app.data_dir / "automation_status.json").write_text(json.dumps({
            "status": "error", "reason": "error", "summary": "La recherche n’a pas abouti."}))
        state = self.app.state()["automation"]
        self.assertEqual(state["attention"]["message"], "La recherche n’a pas abouti.")
        self.assertTrue(state["enabled"])
        self.assertIsNotNone(state["next_run_at"])

    def test_interrupted_run_never_keeps_an_in_progress_summary(self):
        (self.app.data_dir / "automation_status.json").write_text(json.dumps({
            "status": "running", "summary": "Recherche et invitations en cours.", "pid": -1}))
        state = self.app.state()["automation"]
        self.assertFalse(state["running"])
        self.assertEqual(state["last_run"]["status"], "error")
        self.assertIn("arrêté", state["last_run"]["summary"])
        self.assertNotIn("en cours", state["attention"]["message"])

    def test_schedule_failure_leaves_sends_disabled(self):
        with patch.object(self.scheduler, "install", side_effect=web_app.ScheduleError("test")):
            with self.assertRaises(web_app.ScheduleError):
                self.app.configure_automation({"enabled": True, "settings": {"keywords": "fondateur"}})
        saved = json.loads(self.app.settings_path.read_text())
        self.assertFalse(saved["automation_enabled"])
        self.assertEqual(saved["keywords"], "fondateur")

    def test_active_settings_require_explicit_save(self):
        self.app.configure_automation({"enabled": True, "settings": {"keywords": "fondateur"}})
        with self.assertRaises(web_app.BusyError):
            self.app.save_settings({"keywords": "autre"})
        self.assertEqual(json.loads(self.app.settings_path.read_text())["keywords"], "fondateur")
        self.app.configure_automation({"enabled": True, "settings": {"keywords": "autre", "limit": 5}})
        self.assertEqual(self.scheduler.settings["limit"], 5)
        self.assertEqual(self.app.settings["keywords"], "autre")

    def test_pause_during_operation_does_not_kill_scheduler(self):
        self.app.configure_automation({"enabled": True, "settings": {"keywords": "fondateur"}})
        with OperationLock(self.app.data_dir / ".operation.lock"):
            result = self.app.configure_automation({"enabled": False})
        self.assertFalse(json.loads(self.app.settings_path.read_text())["automation_enabled"])
        self.assertEqual(self.scheduler.uninstall_calls, 0)
        self.assertIn("prochaine invitation", result["message"])
        self.app.configure_automation({"enabled": False})
        self.assertEqual(self.scheduler.uninstall_calls, 1)

    def test_settings_endpoint_cannot_enable_automation(self):
        with self.assertRaises(web_app.InputError):
            self.app.save_settings({"keywords": "fondateur", "automation_enabled": True})
        with self.assertRaises(web_app.InputError):
            self.app.start_job({"action": "login", "settings": {"automation_enabled": True}})

    def test_reconfiguration_refuses_active_operation(self):
        with OperationLock(self.app.data_dir / ".operation.lock"):
            with self.assertRaises(web_app.BusyError):
                self.app.configure_automation({"enabled": True, "settings": {"keywords": "fondateur"}})
        self.assertEqual(self.scheduler.install_calls, 0)

    def test_profile_does_not_mean_connected(self):
        state = self.app.state()
        self.assertEqual(state["session"], {"profile_exists": True, "status": "unknown"})
        self.assertEqual(state["stats"]["eligible"], 2)

    def test_last_search_distinguishes_known_and_zero_matches_from_history(self):
        self.assertIsNone(self.app.state()["last_search"])
        report = {"keywords": ["santé"], "geography": "france", "urls": [self.rows[0]["url"]],
                  "finished_at": "2026-10-28T11:30:00+01:00"}
        path = self.app.data_dir / "last_search.json"
        path.write_text(json.dumps(report))
        self.assertEqual(self.app.state()["last_search"], report)
        report["urls"] = []
        path.write_text(json.dumps(report))
        state = self.app.state()
        self.assertEqual(state["last_search"]["urls"], [])
        self.assertEqual(len(state["prospects"]), 3)
        for invalid in ("{", "[]", '{"urls": []}'):
            path.write_text(invalid)
            self.assertIsNone(self.app.state()["last_search"])

    def test_two_servers_can_save_settings_without_shared_temporary_file(self):
        other = web_app.Application(self.directory.name)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(app.save_settings, {"keywords": f"recherche {index}"})
                       for index in range(40) for app in (self.app, other)]
            for result in results:
                result.result()
        self.assertTrue(json.loads(self.app.settings_path.read_text())["keywords"].startswith("recherche "))
        self.assertEqual(list(self.app.data_dir.glob(".settings-*")), [])

    def test_preview_uses_real_cli_and_only_selected_profile(self):
        before = (self.app.data_dir / "prospects.csv").read_bytes()
        self.app.start_job({"action": "preview", "selected_urls": [self.rows[0]["url"]]})
        job = self.wait_job()
        self.assertEqual(job["status"], "success", job)
        text = "\n".join(job["logs"])
        self.assertIn("chosen", text)
        self.assertNotIn("other", text)
        self.assertEqual(before, (self.app.data_dir / "prospects.csv").read_bytes())
        self.assertFalse((self.app.data_dir / "sent_log.csv").exists())
        self.assertEqual(list(self.app.data_dir.glob(".selection-*")), [])

    def test_unsafe_or_unconfirmed_action_never_spawns(self):
        cases = [
            {"action": "find", "settings": {"keywords": " "}},
            {"action": "send", "selected_urls": [self.rows[0]["url"]]},
            {"action": "send", "confirmed": True, "selected_urls": []},
            {"action": "send", "confirmed": True, "selected_urls": [self.rows[2]["url"]]},
            {"action": "preview", "selected_urls": ["https://example.com/"]},
            {"action": "auto"},
        ]
        with patch("web_app.subprocess.Popen") as spawn:
            for case in cases:
                with self.subTest(case=case), self.assertRaises(web_app.InputError):
                    self.app.start_job(case)
            spawn.assert_not_called()

    def test_concurrent_job_refused_and_cancel_reaps_process(self):
        real_popen = subprocess.Popen

        def harmless_process(_args, **kwargs):
            return real_popen([sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(30)"], **kwargs)

        with patch("web_app.subprocess.Popen", side_effect=harmless_process):
            self.app.start_job({"action": "login"})
            deadline = time.monotonic() + 5
            while self.app.process is None and time.monotonic() < deadline:
                time.sleep(.01)
            process = self.app.process
            self.assertIsNotNone(process)
            with self.assertRaises(web_app.BusyError):
                self.app.start_job({"action": "rescore"})
            self.app.cancel()
            self.assertEqual(self.wait_job()["status"], "cancelled")
            self.assertIsNotNone(process.poll())

    def test_disconnected_exit_updates_state(self):
        real_popen = subprocess.Popen

        def failed_login(_args, **kwargs):
            return real_popen([sys.executable, "-c", "raise SystemExit(3)"], **kwargs)

        with patch("web_app.subprocess.Popen", side_effect=failed_login):
            self.app.start_job({"action": "login"})
            self.assertEqual(self.wait_job()["status"], "error")
            self.assertEqual(self.app.session_status, "disconnected")

    def test_cancel_also_reaps_descendant_holding_stdout(self):
        real_popen = subprocess.Popen
        child = "import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); print('child-ready', flush=True); time.sleep(30)"
        parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-u', '-c', {child!r}]); time.sleep(30)"

        def process_with_child(_args, **kwargs):
            return real_popen([sys.executable, "-u", "-c", parent], **kwargs)

        with patch("web_app.subprocess.Popen", side_effect=process_with_child):
            self.app.start_job({"action": "login"})
            deadline = time.monotonic() + 5
            while "child-ready" not in self.app.state()["job"]["logs"] and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIn("child-ready", self.app.state()["job"]["logs"])
            self.app.cancel()
            self.assertEqual(self.wait_job()["status"], "cancelled")

    def test_export_defuses_formula(self):
        with patch.object(self.app, "rows", return_value=[{**self.rows[0], "headline": "=1+1"}]):
            result = self.app.export_csv().decode("utf-8-sig")
        self.assertIn("'=1+1", result)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = web_app.Application(self.directory.name, scheduler=FakeScheduler())
        (self.app.data_dir / ".profile").mkdir()
        self.server = web_app.LocalServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def request(self, method, path, payload=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(client.close)
        body = json.dumps(payload) if payload is not None else None
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    def test_loopback_state_and_static_allowlist(self):
        status, body, headers = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["csrf_token"], self.app.csrf_token)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        for path in ("/.env", "/../.env", "/linkedin_data/prospects.csv", "/%2e%2e/.env"):
            self.assertEqual(self.request("GET", path)[0], 404)

    def test_second_open_reuses_same_project_with_versioned_health_response(self):
        with patch.object(sys, "argv", ["web_app.py", "--port", str(self.server.server_port)]), \
                patch("web_app.Application", return_value=self.app), \
                patch("web_app.webbrowser.open") as open_browser, patch("builtins.print"):
            web_app.main()
        open_browser.assert_called_once_with(self.server.origin)

    def test_reject_host_origin_and_missing_token(self):
        for headers in ({"Host": "evil.test"}, {"Origin": "https://evil.test"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("GET", "/api/state", headers=headers)[0], 403)
        headers = {"Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/settings", {"pages": 3}, headers)[0], 403)
        headers["X-CSRF-Token"] = self.app.csrf_token
        headers["Origin"] = "https://evil.test"
        self.assertEqual(self.request("POST", "/api/settings", {"pages": 3}, headers)[0], 403)
        headers["Origin"] = self.server.origin
        self.assertEqual(self.request("POST", "/api/settings", {"pages": 3}, headers)[0], 200)
        self.assertEqual(self.app.settings["pages"], 3)

    def test_external_link_can_open_page_but_cannot_read_apis_or_mutate(self):
        navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        self.assertEqual(self.request("GET", "/", headers=navigation)[0], 200)
        self.assertEqual(self.request("GET", "/index.html", headers=navigation)[0], 200)
        self.assertEqual(self.request("GET", "/api/state", headers=navigation)[0], 403)
        self.assertEqual(self.request("GET", "/api/export.csv", headers=navigation)[0], 403)
        self.assertEqual(self.request("GET", "/", headers={**navigation, "Sec-Fetch-Dest": "iframe"})[0], 403)
        self.assertEqual(self.request("POST", "/api/automation", {"enabled": True}, navigation)[0], 403)

    def test_validation_and_conflict_status(self):
        headers = {"Content-Type": "application/json", "X-CSRF-Token": self.app.csrf_token}
        self.assertEqual(self.request("POST", "/api/settings", {"pages": -2}, headers)[0], 400)
        self.assertEqual(self.request("POST", "/api/jobs/cancel", {}, headers)[0], 409)
        self.assertEqual(self.request("POST", "/api/jobs", {"action": "auto"}, headers)[0], 400)
        headers["Content-Type"] = "text/plain"
        self.assertEqual(self.request("POST", "/api/settings", {}, headers)[0], 415)

    def test_automation_endpoint_requires_csrf_and_explicit_activation(self):
        payload = {"enabled": True, "settings": {"keywords": "fondateur"}}
        self.assertEqual(self.request("POST", "/api/automation", payload, {"Content-Type": "application/json"})[0], 403)
        headers = {"Content-Type": "application/json", "X-CSRF-Token": self.app.csrf_token}
        self.assertEqual(self.request("POST", "/api/automation", payload, headers)[0], 200)
        self.assertTrue(self.app.settings["automation_enabled"])
        self.assertEqual(self.request("POST", "/api/automation", {"enabled": False}, headers)[0], 200)
        self.assertFalse(self.app.settings["automation_enabled"])

    def test_mail_endpoint_requires_csrf_and_keeps_credentials_out_of_state(self):
        payload = {"enabled": True, "email": "ami@example.fr"}
        with patch.object(self.app.mail, "_request", return_value={"status": "pending"}) as relay:
            self.assertEqual(self.request("POST", "/api/mail", payload,
                                          {"Content-Type": "application/json"})[0], 403)
            relay.assert_not_called()
            headers = {"Content-Type": "application/json", "X-CSRF-Token": self.app.csrf_token}
            with OperationLock(self.app.data_dir / ".operation.lock"):
                status, body, _ = self.request("POST", "/api/mail", payload, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["mail"]["status"], "pending")
            config = json.loads(self.app.mail.config_path.read_text())
            self.assertNotIn(config["token"], body.decode())
            status, state, _ = self.request("GET", "/api/state")
            self.assertEqual(status, 200)
            self.assertNotIn(config["token"], state.decode())
            self.assertEqual(json.loads(state)["mail"]["email"], payload["email"])
            self.app.mail.last_poll -= 6
            relay.return_value = {"status": "verified", "email": payload["email"]}
            self.assertEqual(json.loads(self.request("GET", "/api/state")[1])["mail"]["status"], "verified")
            relay.return_value = {"ok": True}
            status, body, _ = self.request("POST", "/api/mail", {"enabled": False}, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["mail"]["status"], "disabled")
            self.assertFalse(self.app.settings["automation_enabled"])

    def test_mail_validation_and_remote_errors_are_visible_without_starting_linkedin(self):
        headers = {"Content-Type": "application/json", "X-CSRF-Token": self.app.csrf_token}
        for payload in ({"enabled": "yes"}, {"enabled": True, "email": "invalid"},
                        {"enabled": True, "email": "ami@example.fr", "token": "not-allowed"}):
            self.assertEqual(self.request("POST", "/api/mail", payload, headers)[0], 400)
        error = web_app.MailError("Le service de rapports n’est pas encore configuré.", status=503)
        with patch.object(self.app.mail, "_request", side_effect=error):
            status, body, _ = self.request("POST", "/api/mail", {"enabled": True, "email": "ami@example.fr"}, headers)
        self.assertEqual(status, 503)
        self.assertIn("pas encore configuré", json.loads(body)["error"])
        self.assertEqual(json.loads(body)["mail"]["status"], "error")
        self.assertIsNone(self.app.process)
        self.assertEqual(self.app.scheduler.install_calls, 0)


if __name__ == "__main__":
    unittest.main()
