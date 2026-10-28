"""Mail requests and private files use temporary fixtures, never real accounts."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from mail_client import MailClient, MailError, RELAY_URL, relay_summary
import notify


TOKEN = "t" * 43


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())


class Requests:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, **kwargs):
        self.calls.append((request, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return Response(result)


def http_error(code, reason, message="private-provider-detail"):
    return urllib.error.HTTPError(RELAY_URL, code, "provider error", {},
                                 io.BytesIO(json.dumps({"code": reason, "error": message}).encode()))


def summary(**values):
    return {"report_id": "auto-example", "date": "2026-10-28", "status": "success",
            "reason": None, "sent": 2, "added": 5, "checked": 4, "matched": 5, "eligible": 3,
            **values}


class MailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.clock = [0]
        self.requests = Requests()
        self.client = MailClient(self.directory, opener=self.requests, monotonic=lambda: self.clock[0])
        token_patch = patch("mail_client.secrets.token_urlsafe", return_value=TOKEN)
        self.token_generator = token_patch.start()
        self.addCleanup(token_patch.stop)

    def enroll(self):
        self.requests.responses.append({"status": "pending"})
        return self.client.configure({"enabled": True, "email": "ami@example.fr"})

    def test_enrollment_persists_private_token_without_exposing_it(self):
        public = self.enroll()
        self.assertEqual(public["status"], "pending")
        self.assertEqual(public["email"], "ami@example.fr")
        self.assertNotIn(TOKEN, json.dumps(public))
        self.token_generator.assert_called_once_with(32)
        config = json.loads(self.client.config_path.read_text())
        self.assertEqual(config["token"], TOKEN)
        self.assertEqual(self.client.config_path.stat().st_mode & 0o777, 0o600)
        request, options = self.requests.calls[0]
        self.assertEqual(request.full_url, RELAY_URL + "/enroll")
        self.assertEqual(json.loads(request.data), {"email": "ami@example.fr", "token": TOKEN})
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(options["timeout"], 20)
        self.assertEqual(list(self.directory.glob(".mail_config-*.tmp")), [])

    def test_pending_poll_is_throttled_and_updates_verification(self):
        self.enroll()
        self.assertEqual(self.client.state()["status"], "pending")
        self.clock[0] = 4.9
        self.client.state()
        self.assertEqual(len(self.requests.calls), 1)
        self.clock[0] = 5
        self.requests.responses.append({"status": "pending", "email": "ami@example.fr"})
        self.assertEqual(self.client.state()["status"], "pending")
        self.clock[0] = 10
        self.requests.responses.append({"status": "verified", "email": "ami@example.fr"})
        self.assertEqual(self.client.state()["status"], "verified")
        request, options = self.requests.calls[-1]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(options["timeout"], 5)
        self.assertNotIn(TOKEN, json.dumps(self.client.state(refresh=False)))

    def test_mailbox_case_matches_the_relay_normalization_during_verification(self):
        self.requests.responses.append({"status": "pending"})
        state = self.client.configure({"enabled": True, "email": "Wael@Example.FR"})
        self.assertEqual(state["email"], "wael@example.fr")
        self.assertEqual(json.loads(self.requests.calls[0][0].data)["email"], "wael@example.fr")
        self.clock[0] = 5
        self.requests.responses.append({"status": "verified", "email": "wael@example.fr"})
        self.assertEqual(self.client.state()["status"], "verified")

    def test_service_configuration_errors_are_clear_and_never_echo_provider_data(self):
        self.requests.responses.append(http_error(503, "email_not_configured", TOKEN))
        with self.assertRaisesRegex(MailError, "pas encore configuré") as raised:
            self.client.configure({"enabled": True, "email": "ami@example.fr"})
        self.assertEqual(raised.exception.status, 503)
        state = self.client.state(refresh=False)
        self.assertEqual(state["status"], "error")
        self.assertNotIn(TOKEN, json.dumps(state))
        self.assertNotIn("private-provider", json.dumps(state))

    def test_changing_a_verified_address_revokes_the_previous_credential(self):
        self.enroll()
        self.clock[0] = 5
        self.requests.responses.append({"status": "verified", "email": "ami@example.fr"})
        self.assertEqual(self.client.state()["status"], "verified")
        replacement = "u" * 43
        self.token_generator.return_value = replacement
        self.requests.responses.extend([{"ok": True}, {"status": "pending"}])
        public = self.client.configure({"enabled": True, "email": "new@example.fr"})
        revoke, options = self.requests.calls[-2]
        self.assertEqual(revoke.get_method(), "DELETE")
        self.assertEqual(revoke.full_url, RELAY_URL + "/installation")
        self.assertEqual(revoke.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(options["timeout"], 5)
        enroll, _ = self.requests.calls[-1]
        self.assertEqual(json.loads(enroll.data), {"email": "new@example.fr", "token": replacement})
        self.assertEqual((public["email"], public["status"]), ("new@example.fr", "pending"))
        self.assertNotIn(TOKEN, json.dumps(public))
        self.assertEqual(json.loads(self.client.config_path.read_text())["token"], replacement)

    def test_failed_old_credential_revocation_does_not_block_the_new_address(self):
        self.enroll()
        self.token_generator.return_value = "u" * 43
        self.requests.responses.extend([http_error(503, "email_unavailable"), {"status": "pending"}])
        public = self.client.configure({"enabled": True, "email": "new@example.fr"})
        self.assertEqual((public["email"], public["status"]), ("new@example.fr", "pending"))
        self.assertEqual(self.requests.calls[-1][0].full_url, RELAY_URL + "/enroll")
        self.assertEqual(json.loads(self.client.config_path.read_text())["token"], "u" * 43)

    def test_retry_after_enrollment_error_reuses_the_existing_installation_token(self):
        self.requests.responses.append(http_error(503, "email_unavailable"))
        with self.assertRaises(MailError):
            self.client.configure({"enabled": True, "email": "ami@example.fr"})
        self.assertEqual(self.client.state(refresh=False)["status"], "error")
        self.requests.responses.append({"status": "pending"})
        public = self.client.configure({"enabled": True, "email": "AMI@example.fr"})
        self.assertEqual(public["status"], "pending")
        self.token_generator.assert_called_once_with(32)
        self.assertEqual([request.get_method() for request, _ in self.requests.calls], ["POST", "POST"])
        self.assertEqual([json.loads(request.data)["token"] for request, _ in self.requests.calls], [TOKEN, TOKEN])

    def test_remote_revoke_is_mapped_to_local_disabled_state(self):
        self.enroll()
        self.clock[0] = 5
        self.requests.responses.append({"status": "revoked", "email": "ami@example.fr"})
        public = self.client.state()
        self.assertEqual(public["status"], "disabled")
        self.assertFalse(public["enabled"])

    def test_wrong_status_email_is_not_adopted(self):
        self.enroll()
        self.clock[0] = 5
        self.requests.responses.append({"status": "verified", "email": "other@example.fr"})
        public = self.client.state()
        self.assertEqual(public["status"], "error")
        self.assertEqual(public["email"], "ami@example.fr")

    def test_disabling_is_saved_even_when_remote_revoke_fails(self):
        self.enroll()
        self.requests.responses.append(OSError("private-network-token=" + TOKEN))
        state = self.client.configure({"enabled": False})
        self.assertFalse(state["enabled"])
        self.assertEqual(state["status"], "disabled")
        self.assertIn("ce Mac n’enverra plus", state["message"])
        self.assertNotIn(TOKEN, json.dumps(state))
        self.assertFalse(self.client.deliver(summary()))
        self.assertEqual(len(self.requests.calls), 2)

    def test_reports_send_only_fixed_counters_and_record_duplicate_success(self):
        self.enroll()
        self.requests.responses.append({"status": "duplicate"})
        value = summary(names=["Private Name"], urls=["https://private.example"], token="secret")
        self.assertTrue(self.client.deliver(value))
        request, _ = self.requests.calls[-1]
        self.assertEqual(request.full_url, RELAY_URL + "/report")
        self.assertEqual(json.loads(request.data), summary())
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(self.client.state(refresh=False)["last_delivery"]["status"], "duplicate")
        self.assertEqual(self.client.delivery_path.stat().st_mode & 0o777, 0o600)

    def test_pending_verification_delivery_is_visible_without_config_mutation(self):
        self.enroll()
        original = self.client.config_path.read_bytes()
        self.requests.responses.append(http_error(403, "verification_required"))
        self.assertFalse(self.client.deliver(summary()))
        self.assertEqual(self.client.config_path.read_bytes(), original)
        state = self.client.state(refresh=False)
        self.assertEqual(state["last_delivery"]["status"], "pending")
        self.assertIn("Confirmez", state["last_delivery"]["message"])

    def test_report_failure_never_undoes_a_concurrent_local_disable(self):
        self.enroll()
        def disabled_during_delivery(request, **_kwargs):
            other = MailClient(self.directory, opener=Requests({"ok": True}))
            other.configure({"enabled": False})
            return Response({"status": "sent"})
        self.client.opener = disabled_during_delivery
        self.assertTrue(self.client.deliver(summary()))
        self.assertFalse(self.client.state(refresh=False)["enabled"])

    def test_invalid_addresses_never_create_credentials_or_make_requests(self):
        for email in (None, "", "name", "a@example.fr\nBcc:other@example.fr", "a..b@example.fr", "A <a@example.fr>"):
            with self.subTest(email=email), self.assertRaises(ValueError):
                self.client.configure({"enabled": True, "email": email})
        self.assertEqual(self.requests.calls, [])
        self.assertFalse(self.client.config_path.exists())

    def test_malformed_private_config_fails_closed_and_never_falls_back(self):
        self.client.config_path.write_text("invalid-json")
        self.assertEqual(self.client.state()["status"], "error")
        with patch("notify.send_email") as legacy:
            self.assertFalse(notify.send_report(self.directory, summary(), legacy_subject="subject", legacy_html="html"))
        legacy.assert_not_called()

    def test_legacy_fallback_exists_only_before_any_central_configuration(self):
        with patch("notify.send_email", return_value=True) as legacy:
            self.assertTrue(notify.send_report(self.directory, summary(), legacy_subject="subject", legacy_html="html"))
            legacy.assert_called_once_with("subject", "html")
            legacy.reset_mock()
            self.client.configure({"enabled": False})
            self.assertFalse(notify.send_report(self.directory, summary(), legacy_subject="subject", legacy_html="html"))
            legacy.assert_not_called()

    def test_central_delivery_never_evaluates_a_failing_legacy_renderer(self):
        self.enroll()
        renderer = Mock(side_effect=ValueError("private broken CSV"))
        with patch("notify.MailClient.deliver", return_value=True) as central, \
                patch("notify.send_email") as legacy:
            self.assertTrue(notify.send_report(self.directory, summary(),
                                               legacy_subject="subject", legacy_html=renderer))
        central.assert_called_once_with(summary())
        renderer.assert_not_called()
        legacy.assert_not_called()

    def test_legacy_fallback_evaluates_the_html_factory(self):
        renderer = Mock(return_value="<p>Legacy report</p>")
        with patch("notify.send_email", return_value=True) as legacy:
            self.assertTrue(notify.send_report(self.directory, summary(),
                                               legacy_subject="subject", legacy_html=renderer))
        renderer.assert_called_once_with()
        legacy.assert_called_once_with("subject", "<p>Legacy report</p>")

    def test_invalid_report_fields_are_rejected_without_request(self):
        self.enroll()
        for changes in ({"sent": 21}, {"sent": True}, {"date": "20261028"},
                        {"reason": "private exception"}, {"report_id": "a" * 101}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                relay_summary(summary(**changes))
        self.assertEqual(len(self.requests.calls), 1)


class LegacyMailTests(unittest.TestCase):
    def test_legacy_direct_mail_preserves_explicit_sender_and_recipients(self):
        requests = Requests({"id": "example-delivery"})
        environment = {"RESEND_API_KEY": "private-test-key", "NOTIFY_EMAIL": "first@example.fr,second@example.fr",
                       "RESEND_FROM": "Rapports <rapports@example.fr>"}
        with patch.dict("os.environ", environment, clear=True), \
                patch("notify.urllib.request.urlopen", side_effect=requests):
            self.assertTrue(notify.send_email("Bilan", "<p>Test</p>"))
        request, _ = requests.calls[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["from"], environment["RESEND_FROM"])
        self.assertEqual(payload["to"], ["first@example.fr", "second@example.fr"])
        self.assertEqual(request.get_header("Authorization"), "Bearer private-test-key")

    def test_legacy_error_logs_do_not_echo_provider_body_or_private_key(self):
        environment = {"RESEND_API_KEY": "private-test-key", "NOTIFY_EMAIL": "first@example.fr"}
        error = http_error(401, "unauthorized", "private-test-key")
        with patch.dict("os.environ", environment, clear=True), \
                patch("notify.urllib.request.urlopen", side_effect=error), \
                self.assertLogs("notify", level="WARNING") as logs:
            self.assertFalse(notify.send_email("Bilan", "<p>Test</p>"))
        self.assertIn("401", logs.output[0])
        self.assertNotIn("private-test-key", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
