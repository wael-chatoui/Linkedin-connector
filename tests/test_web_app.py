"""Tests isolés : jamais de navigateur LinkedIn ni d'invitation réelle."""

import csv
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import web_app


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = web_app.Application(self.directory.name)
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
        settings = self.app.save_settings({"keywords": "  fondateur santé  ", "pages": 3})
        self.assertEqual(settings["keywords"], "fondateur santé")
        self.assertEqual(web_app.Application(self.directory.name).settings, settings)
        for value in ({"pages": True}, {"pages": 100}, {"network": "all"}, {"note": "yes"}, {"other": 1}):
            with self.subTest(value=value), self.assertRaises(web_app.InputError):
                self.app.save_settings(value)
        self.assertEqual(self.app.settings, settings)

    def test_profile_does_not_mean_connected(self):
        (self.app.data_dir / ".profile").mkdir()
        state = self.app.state()
        self.assertEqual(state["session"], {"profile_exists": True, "status": "unknown"})
        self.assertEqual(state["stats"]["eligible"], 2)

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
        self.app = web_app.Application(self.directory.name)
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

    def test_validation_and_conflict_status(self):
        headers = {"Content-Type": "application/json", "X-CSRF-Token": self.app.csrf_token}
        self.assertEqual(self.request("POST", "/api/settings", {"pages": -2}, headers)[0], 400)
        self.assertEqual(self.request("POST", "/api/jobs/cancel", {}, headers)[0], 409)
        self.assertEqual(self.request("POST", "/api/jobs", {"action": "auto"}, headers)[0], 400)
        headers["Content-Type"] = "text/plain"
        self.assertEqual(self.request("POST", "/api/settings", {}, headers)[0], 415)


if __name__ == "__main__":
    unittest.main()
