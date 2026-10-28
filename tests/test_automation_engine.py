"""Scheduled-run tests use isolated files and mocked LinkedIn and email calls."""

from contextlib import ExitStack
from datetime import date
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import app_settings
import linkedin_connect as engine


def prospect(slug, score=80, days=""):
    row = dict.fromkeys(engine.FIELDS, "")
    row.update(url=f"https://www.linkedin.com/in/{slug}/", name=slug,
               headline="Founder SaaS", score=str(score), mutual="0", last_post_days=days)
    return row


class SettingsTests(unittest.TestCase):
    def test_old_settings_migrate_without_activating_automation(self):
        settings = app_settings.validate_settings({"paris": True, "keywords": "  founder  "})
        self.assertEqual(settings["geography"], "france")
        self.assertEqual(settings["keywords"], "founder")
        self.assertFalse(settings["automation_enabled"])
        self.assertEqual(settings["schedule_days"], "everyday")
        self.assertEqual(settings["schedule_time"], "10:30")

    def test_partial_updates_preserve_schedule_and_search(self):
        base = app_settings.validate_settings({"automation_enabled": True, "keywords": "healthcare",
                                               "schedule_time": "14:00", "geography": "international"})
        updated = app_settings.validate_settings({"limit": 7}, base)
        self.assertTrue(updated["automation_enabled"])
        self.assertEqual(updated["keywords"], "healthcare")
        self.assertEqual(updated["schedule_time"], "14:00")
        self.assertEqual(updated["limit"], 7)

    def test_rejects_invalid_schedule_and_search(self):
        cases = [{"schedule_time": value} for value in ("9:00", "24:00", "12:60", "12:00\n", 123)]
        cases += [{"schedule_days": "weekly"}, {"automation_enabled": 1}, {"limit": True},
                  {"limit": 21}, {"keywords": "x\n" * 11}, {"surprise": True}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(app_settings.SettingsError):
                app_settings.validate_settings(value)

    def test_reading_missing_or_invalid_configuration_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with self.assertRaises(FileNotFoundError):
                app_settings.read_settings(path)
            for raw in ("{", "[]", '{"automation_enabled": "true"}'):
                path.write_text(raw, encoding="utf-8")
                with self.subTest(raw=raw), self.assertRaises(app_settings.SettingsError):
                    app_settings.read_settings(path)

    def test_atomic_write_preserves_previous_configuration_if_replacement_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            app_settings.write_settings(path, {"keywords": "existing"})
            with patch.object(Path, "replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    app_settings.write_settings(path, {"keywords": "new"})
            self.assertEqual(app_settings.read_settings(path)["keywords"], "existing")
            self.assertEqual([item.name for item in Path(directory).iterdir()], ["settings.json"])


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in {
            "DATA_DIR": self.directory, "PROFILE_DIR": self.directory / ".profile",
            "PROSPECTS_CSV": self.directory / "prospects.csv", "LOG_FILE": self.directory / "sent_log.csv",
            "LAST_RUN_FILE": self.directory / "last_auto_run.txt",
        }.items():
            self.stack.enter_context(patch.object(engine, name, value))
        engine.PROFILE_DIR.mkdir()
        self.stack.enter_context(patch("builtins.print"))
        self.browser = self.stack.enter_context(patch.object(engine, "sync_playwright"))
        self.context, self.page = MagicMock(), MagicMock()
        self.start = self.stack.enter_context(patch.object(engine, "start", return_value=(self.context, self.page)))
        self.network = self.stack.enter_context(patch.object(engine, "wait_for_network", return_value=True))
        self.stack.enter_context(patch.object(engine, "check_blocked"))
        self.stack.enter_context(patch.object(engine, "pause"))
        self.stack.enter_context(patch.object(engine.time, "sleep"))
        self.invite = self.stack.enter_context(patch.object(engine, "send_invite",
                                                           side_effect=AssertionError("Unexpected invitation")))
        self.email = self.stack.enter_context(patch("notify.send_email"))

    def configure(self, **values):
        return app_settings.write_settings(self.directory / "settings.json", {
            "automation_enabled": True, "keywords": "Founder SaaS", **values,
        })

    def invoke(self, **values):
        engine.cmd_auto(SimpleNamespace(**{"force": True, "enrich": 20, **values}))

    def status(self):
        return json.loads((self.directory / "automation_status.json").read_text())

    def pipeline(self, matches):
        def find(_page, rows, _queries, _pages, _network, _geo, found_urls=None):
            found_urls.update(matches)
            return 0
        finder = self.stack.enter_context(patch.object(engine, "find_prospects", side_effect=find))
        enricher = self.stack.enter_context(patch.object(engine, "enrich_rows", return_value=0))
        sender = self.stack.enter_context(patch.object(engine, "send_batch"))
        return finder, enricher, sender

    def test_missing_disabled_and_empty_keywords_never_open_linkedin_even_when_forced(self):
        for case in (None, {"automation_enabled": False}, {"keywords": " \n "}):
            with self.subTest(case=case):
                if case is not None:
                    self.configure(**case)
                self.invoke()
                self.assertEqual(self.status()["status"], "skipped")
                if case == {"automation_enabled": False}:
                    self.assertNotIn("reason", self.status())
                else:
                    self.assertEqual(self.status()["reason"], "configuration")
                self.assertIsNone(self.status()["matched"])
                self.assertIsNone(self.status()["eligible"])
                self.assertFalse(engine.LAST_RUN_FILE.exists())
        self.network.assert_not_called()
        self.browser.assert_not_called()
        self.invite.assert_not_called()
        self.email.assert_not_called()

    def test_invalid_settings_produce_visible_error_without_browser(self):
        (self.directory / "settings.json").write_text("{", encoding="utf-8")
        with self.assertRaises(app_settings.SettingsError):
            self.invoke()
        self.assertEqual(self.status()["status"], "error")
        self.assertEqual(self.status()["reason"], "configuration")
        self.assertNotIn("JSON", self.status()["summary"])
        self.browser.assert_not_called()

    def test_each_run_reads_latest_settings_and_keeps_commas_in_one_query(self):
        finder, _, sender = self.pipeline(set())
        cases = [
            dict(keywords="  fondateur santé, IA\n\n dirigeant PME ", geography="france", limit=2,
                 pages=3, network="O", min_score=52, note=True),
            dict(keywords="startup founder", geography="international", limit=8,
                 pages=1, network="S,O", min_score=15, note=False),
        ]
        for values in cases:
            with self.subTest(values=values):
                self.configure(**values)
                self.invoke()
                args = finder.call_args.args
                expected_queries = [("custom", line.strip()) for line in values["keywords"].splitlines()
                                    if line.strip()]
                self.assertEqual(args[2:6], (expected_queries, values["pages"], values["network"],
                                             engine.GEO_FRANCE if values["geography"] == "france" else None))
                self.assertEqual(sender.call_args.args[3:5], (values["limit"], values["note"]))
                self.assertEqual(self.status()["settings"]["limit"], values["limit"])
                self.assertEqual(self.status()["status"], "success")
        self.assertEqual(finder.call_count, 2)

    def test_manual_search_uses_same_queries_as_automation(self):
        keywords = "  fondateur SaaS, IA\n\n dirigeant PME \n"
        expected = [("custom", "fondateur SaaS, IA"), ("custom", "dirigeant PME")]
        args = SimpleNamespace(keywords=keywords, segments=None, international=True,
                               paris=False, pages=3, network="S,O")
        with patch.object(engine, "find_prospects", return_value=0) as finder:
            engine.cmd_find(args)
        finder.assert_called_once_with(self.page, [], expected, 3, "S,O", None, found_urls=set())
        self.context.close.assert_called_once()

    def test_old_stock_is_excluded_even_when_it_contains_many_eligible_profiles(self):
        current = prospect("current", score=60)
        old = [prospect(f"old-{index}") for index in range(40)]
        engine.save_rows(engine.PROSPECTS_CSV, [*old, current])
        self.configure()
        finder, enricher, sender = self.pipeline({current["url"]})
        self.invoke()
        finder.assert_called_once()
        self.assertEqual(len(enricher.call_args.args[1]), 41)
        self.assertEqual([row["name"] for row in enricher.call_args.kwargs["candidates"]], ["current"])
        self.assertEqual([row["name"] for row in sender.call_args.args[2]], ["current"])
        self.assertEqual(len(sender.call_args.args[1]), 41)
        self.assertEqual(len(engine.load_rows(engine.PROSPECTS_CSV)), 41)
        self.assertEqual(self.status()["matched"], 1)
        self.assertEqual(self.status()["eligible"], 1)

    def test_changes_during_run_apply_next_time_without_mixing_current_search(self):
        current = prospect("current")
        engine.save_rows(engine.PROSPECTS_CSV, [current])
        self.configure(keywords="original", limit=1)
        def find(_page, _rows, *_args, found_urls=None):
            found_urls.add(current["url"])
            self.configure(keywords="next search", geography="international", limit=9, note=True)
            return 0
        with patch.object(engine, "find_prospects", side_effect=find), \
                patch.object(engine, "enrich_rows", return_value=0), \
                patch.object(engine, "send_batch") as sender:
            self.invoke()
        self.assertEqual(sender.call_args.args[3:5], (1, False))
        self.assertEqual(self.status()["settings"], {"keywords": "original", "geography": "france", "limit": 1})
        self.assertEqual(app_settings.read_settings(self.directory / "settings.json")["keywords"], "next search")

    def test_auto_cli_rejects_overrides_that_would_ignore_interface_settings(self):
        for option in (["--limit", "20"], ["--min-score", "0"], ["--note"]):
            with self.subTest(option=option), \
                    patch.object(sys, "argv", ["linkedin_connect.py", "auto", *option]), \
                    patch.object(sys, "stderr"), self.assertRaises(SystemExit) as raised:
                engine.main()
            self.assertEqual(raised.exception.code, 2)
        self.browser.assert_not_called()

    def test_score_and_active_filters_apply_to_current_search(self):
        rows = [prospect("active", days="5"), prospect("inactive", days="80"),
                prospect("unknown"), prospect("low-score", score=39, days="5")]
        engine.save_rows(engine.PROSPECTS_CSV, rows)
        self.configure(active_only=True, min_score=40)
        _, _, sender = self.pipeline({row["url"] for row in rows})
        self.invoke()
        self.assertEqual([row["name"] for row in sender.call_args.args[2]], ["active"])
        self.assertEqual(self.status()["matched"], 4)
        self.assertEqual(self.status()["eligible"], 1)

    def test_configured_maximum_is_capped_by_remaining_budgets(self):
        self.configure(limit=7)
        _, _, sender = self.pipeline(set())
        with patch.object(engine, "sent_since", side_effect=lambda days: 19 if days == 1 else 79):
            self.invoke()
        self.assertEqual(sender.call_args.args[3], 1)
        self.browser.reset_mock()
        with patch.object(engine, "sent_since", return_value=80):
            self.invoke()
        self.assertEqual(self.status()["status"], "skipped")
        self.assertEqual(self.status()["reason"], "quota")
        self.browser.assert_not_called()

    def test_weekends_run_only_when_configured_and_same_day_is_not_repeated(self):
        class Saturday(date):
            @classmethod
            def today(cls):
                return cls(2026, 10, 17)
        self.pipeline(set())
        with patch.object(engine, "date", Saturday):
            self.configure(schedule_days="weekdays")
            self.invoke(force=False)
            self.browser.assert_not_called()
            self.configure(schedule_days="everyday")
            self.invoke(force=False)
            self.browser.assert_called_once()
            self.assertTrue(engine.ran_today())
            self.invoke(force=False)
            self.browser.assert_called_once()
            self.assertEqual(self.status()["status"], "skipped")

    def test_pause_stops_before_next_invitation_and_keeps_sent_count(self):
        rows = [prospect("first"), prospect("second")]
        engine.save_rows(engine.PROSPECTS_CSV, rows)
        self.configure(limit=2)
        def find(_page, _rows, *_args, found_urls=None):
            found_urls.update(row["url"] for row in rows)
            return 0
        def send(_page, _row, _note):
            self.configure(automation_enabled=False)
            return "sent"
        self.invite.side_effect = send
        with patch.object(engine, "find_prospects", side_effect=find), \
                patch.object(engine, "enrich_rows", return_value=0):
            self.invoke()
        self.invite.assert_called_once()
        self.context.close.assert_called_once()
        self.assertEqual(self.status()["status"], "skipped")
        self.assertEqual(self.status()["sent"], 1)
        self.assertEqual(self.status()["matched"], 2)
        self.assertEqual(self.status()["eligible"], 2)
        self.assertNotIn("reason", self.status())
        self.assertTrue(engine.ran_today())
        persisted = engine.load_rows(engine.PROSPECTS_CSV)
        self.assertEqual([row["status"] for row in persisted], ["sent", ""])

    def test_block_marks_day_reports_error_and_closes_browser(self):
        self.configure()
        with patch.object(engine, "find_prospects", side_effect=engine.Blocked("checkpoint")):
            with self.assertRaises(engine.Blocked):
                self.invoke()
        self.context.close.assert_called_once()
        self.assertTrue(engine.ran_today())
        self.assertEqual(self.status()["status"], "error")
        self.assertEqual(self.status()["reason"], "linkedin_check")
        self.assertIn("checkpoint", self.status()["message"])
        self.assertNotIn("checkpoint", self.status()["summary"])
        self.assertIsNotNone(self.status()["finished_at"])

    def test_missing_session_and_network_do_not_start_browser(self):
        self.configure()
        engine.PROFILE_DIR.rmdir()
        self.invoke()
        self.assertEqual(self.status()["status"], "skipped")
        self.assertEqual(self.status()["reason"], "login")
        engine.PROFILE_DIR.mkdir()
        self.network.return_value = False
        self.invoke()
        self.assertEqual(self.status()["status"], "skipped")
        self.assertEqual(self.status()["reason"], "network")
        self.browser.assert_not_called()

    def test_zero_invitations_distinguish_no_matches_from_selection_filters(self):
        self.configure()
        row = prospect("low-score", score=10)
        engine.save_rows(engine.PROSPECTS_CSV, [row])
        finder, _, sender = self.pipeline(set())
        self.invoke()
        self.assertEqual(self.status()["reason"], "no_matches")
        self.assertEqual((self.status()["matched"], self.status()["eligible"]), (0, 0))
        self.assertEqual(self.status()["status"], "success")

        def find(_page, _rows, *_args, found_urls=None):
            found_urls.add(row["url"])
            return 0
        finder.side_effect = find
        self.invoke()
        self.assertEqual(self.status()["reason"], "no_eligible")
        self.assertEqual((self.status()["matched"], self.status()["eligible"]), (1, 0))
        self.assertEqual(sender.call_args.args[2], [])
        self.assertIn("filtres", self.status()["summary"])

    def test_success_keeps_eligible_count_before_rows_are_marked_sent(self):
        self.configure(limit=1)
        rows = [prospect("one"), prospect("two")]
        engine.save_rows(engine.PROSPECTS_CSV, rows)
        def find(_page, _rows, *_args, found_urls=None):
            found_urls.update(row["url"] for row in rows)
            return 0
        self.invite.side_effect = None
        self.invite.return_value = "sent"
        with patch.object(engine, "find_prospects", side_effect=find), \
                patch.object(engine, "enrich_rows", return_value=0):
            self.invoke()
        status = self.status()
        self.assertEqual((status["matched"], status["eligible"], status["sent"]), (2, 2, 1))
        self.assertEqual(status["summary"], "1 invitation envoyée.")
        self.assertNotIn("reason", status)
        self.invite.assert_called_once()

    def test_public_failure_summary_never_contains_raw_exception_details(self):
        self.configure()
        cases = [
            (engine.NotLoggedIn("private session details"), "login"),
            (engine.PWError("Page.goto: net::ERR_INTERNET_DISCONNECTED at https://private.example/token"), "network"),
            (ConnectionResetError("private transport details"), "network"),
            (engine.PWTimeout("private selector wait"), "error"),
            (RuntimeError("private traceback details"), "error"),
        ]
        for error, expected_reason in cases:
            with self.subTest(error=type(error).__name__):
                self.start.side_effect = error
                with self.assertRaises(type(error)):
                    self.invoke()
                status = self.status()
                self.assertEqual(status["status"], "error")
                self.assertEqual(status["reason"], expected_reason)
                self.assertIn("private", status["message"])
                self.assertNotIn("private", status["summary"])
                self.assertNotIn("http", status["summary"])
                self.assertIsNone(status["matched"])
                self.assertIsNone(status["eligible"])
        self.invite.assert_not_called()

    def test_configuration_removed_during_search_is_explained_without_sending(self):
        self.configure()
        row = prospect("current")
        engine.save_rows(engine.PROSPECTS_CSV, [row])
        def find(_page, _rows, *_args, found_urls=None):
            found_urls.add(row["url"])
            (self.directory / "settings.json").unlink()
            return 0
        with patch.object(engine, "find_prospects", side_effect=find):
            with self.assertRaises(FileNotFoundError):
                self.invoke()
        self.assertEqual(self.status()["reason"], "configuration")
        self.assertEqual(self.status()["matched"], 1)
        self.assertIsNone(self.status()["eligible"])
        self.assertNotIn(str(self.directory), self.status()["summary"])
        self.invite.assert_not_called()

    def test_failed_and_unavailable_invitations_have_different_public_reasons(self):
        self.configure()
        row = prospect("current")
        def find(_page, _rows, *_args, found_urls=None):
            found_urls.add(row["url"])
            return 0
        cases = [("error_send_disabled", "error"), ("no_connect_button", "no_eligible")]
        self.invite.side_effect = None
        for invite_status, reason in cases:
            with self.subTest(invite_status=invite_status):
                engine.save_rows(engine.PROSPECTS_CSV, [row])
                self.invite.return_value = invite_status
                with patch.object(engine, "find_prospects", side_effect=find), \
                        patch.object(engine, "enrich_rows", return_value=0):
                    self.invoke()
                self.assertEqual(self.status()["reason"], reason)
                self.assertEqual(self.status()["eligible"], 1)
                self.assertEqual(self.status()["sent"], 0)
                # Outcome reporting does not change the established run status.
                self.assertEqual(self.status()["status"], "success")

    def test_finder_collects_previously_known_matches_and_new_profiles(self):
        known, stale = prospect("known"), prospect("stale")
        rows, matches = [known, stale], set()
        self.page.evaluate.return_value = [
            {"slug": "known", "text": "Known\nFounder SaaS\nFrance"},
            {"slug": "new", "text": "New\nFounder SaaS\nFrance"},
        ]
        with patch.object(engine, "goto"):
            added = engine.find_prospects(self.page, rows, [("custom", "SaaS")], 1, "S",
                                          engine.GEO_FRANCE, found_urls=matches)
        self.assertEqual(added, 1)
        self.assertEqual(matches, {known["url"], "https://www.linkedin.com/in/new/"})
        self.assertEqual(len(engine.load_rows(engine.PROSPECTS_CSV)), 3)

    def test_enrichment_saves_full_csv_when_candidates_are_restricted(self):
        old, current = prospect("old"), prospect("current")
        with patch.object(engine, "last_post_days", return_value=3) as activity:
            checked = engine.enrich_rows(self.page, [old, current], 20, 40, candidates=[current])
        self.assertEqual(checked, 1)
        activity.assert_called_once_with(self.page, current["url"])
        saved = engine.load_rows(engine.PROSPECTS_CSV)
        self.assertEqual([row["name"] for row in saved], ["old", "current"])
        self.assertEqual([row["last_post_days"] for row in saved], ["", "3"])

    def test_every_eligible_attempt_has_one_anonymous_final_mail_summary(self):
        self.configure()
        self.pipeline(set())
        with patch("notify.send_report", return_value=True) as mail:
            self.invoke()
            mail.assert_called_once()
            payload = mail.call_args.args[1]
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["reason"], "no_matches")
            self.assertEqual(payload["sent"], 0)
            self.assertEqual(set(payload), {"report_id", "date", "status", "reason", "sent", "added", "checked", "matched", "eligible"})
            self.assertNotIn("Founder SaaS", json.dumps(payload))
            mail.reset_mock()
            self.network.return_value = False
            self.invoke()
            mail.assert_called_once()
            self.assertEqual(mail.call_args.args[1]["reason"], "network")
            mail.reset_mock()
            with patch.object(engine, "remaining_budget", return_value=0):
                self.invoke()
            mail.assert_called_once()
            self.assertEqual(mail.call_args.args[1]["reason"], "quota")
            mail.reset_mock()
            engine.PROFILE_DIR.rmdir()
            self.invoke()
            mail.assert_called_once()
            self.assertEqual(mail.call_args.args[1]["reason"], "login")

    def test_finished_failures_report_once_and_mail_failure_does_not_change_pipeline_result(self):
        self.configure()
        self.start.side_effect = engine.Blocked("private LinkedIn checkpoint URL")
        with patch("notify.send_report", side_effect=RuntimeError("private mail token")) as mail:
            with self.assertRaises(engine.Blocked):
                self.invoke()
        mail.assert_called_once()
        payload = mail.call_args.args[1]
        self.assertEqual((payload["status"], payload["reason"]), ("error", "linkedin_check"))
        self.assertNotIn("private", json.dumps(payload))
        self.assertEqual(self.status()["reason"], "linkedin_check")

    def test_duplicate_and_disabled_attempts_do_not_email_again(self):
        self.configure()
        engine.mark_ran_today()
        with patch("notify.send_report") as mail:
            self.invoke(force=False)
            mail.assert_not_called()
            self.configure(automation_enabled=False)
            self.invoke()
            mail.assert_not_called()

    def test_broken_sent_history_still_sends_the_central_error_summary(self):
        from mail_client import MailClient
        self.configure()
        with patch("mail_client.MailClient._request", return_value={"status": "pending"}):
            MailClient(self.directory).configure({"enabled": True, "email": "ami@example.fr"})
        with patch.object(engine, "sent_since", side_effect=ValueError("private broken CSV")), \
                patch.object(engine, "render_report", side_effect=ValueError("private HTML failure")) as renderer, \
                patch("notify.MailClient.deliver", return_value=True) as central:
            with self.assertRaisesRegex(ValueError, "private broken CSV"):
                self.invoke()
        renderer.assert_not_called()
        central.assert_called_once()
        payload = central.call_args.args[0]
        self.assertEqual((payload["status"], payload["reason"], payload["sent"]), ("error", "error", 0))
        self.assertNotIn("private", json.dumps(payload))
        self.email.assert_not_called()


if __name__ == "__main__":
    unittest.main()
