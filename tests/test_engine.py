"""Engine tests use temporary data and mocked browsers; no LinkedIn requests."""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import linkedin_connect as engine
from runtime_lock import OperationBusy, OperationLock, operation_in_progress


ROOT = Path(__file__).resolve().parents[1]


def prospect(slug, score=80, status=""):
    row = dict.fromkeys(engine.FIELDS, "")
    row.update(url=f"https://www.linkedin.com/in/{slug}/", name=slug,
               score=str(score), status=status, mutual="0")
    return row


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.data_dir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in {
            "DATA_DIR": self.data_dir,
            "PROFILE_DIR": self.data_dir / ".profile",
            "PROSPECTS_CSV": self.data_dir / "prospects.csv",
            "LOG_FILE": self.data_dir / "sent_log.csv",
            "LAST_RUN_FILE": self.data_dir / "last_auto_run.txt",
        }.items():
            self.stack.enter_context(patch.object(engine, name, value))
        self.stack.enter_context(patch("builtins.print"))

    def selection(self, urls):
        path = self.data_dir / "selected.json"
        path.write_text(json.dumps(urls), encoding="utf-8")
        return path

    def run_args(self, **overrides):
        values = dict(min_score=40, active_only=False, limit=15, dry_run=False,
                      note=False, selected_file=None)
        values.update(overrides)
        return SimpleNamespace(**values)

    def browser(self):
        context, page = MagicMock(), MagicMock()
        self.stack.enter_context(patch.object(engine, "sync_playwright"))
        self.stack.enter_context(patch.object(engine, "start", return_value=(context, page)))
        return context, page

    def test_search_url_keeps_france_and_other_search_filters(self):
        query = parse_qs(urlsplit(engine.search_url("fondateur santé & IA", 2, "S,O", engine.GEO_FRANCE)).query)
        self.assertEqual(query, {
            "keywords": ["fondateur santé & IA"], "network": ['["S","O"]'],
            "geoUrn": [f'["{engine.GEO_FRANCE}"]'], "origin": ["FACETED_SEARCH"], "page": ["2"],
        })

    def test_international_search_url_omits_geography_filter(self):
        for geo in [None, ""]:
            with self.subTest(geo=geo):
                query = parse_qs(urlsplit(engine.search_url("startup founder", 1, "S", geo)).query,
                                 keep_blank_values=True)
                self.assertNotIn("geoUrn", query)
                self.assertEqual(query["keywords"], ["startup founder"])
                self.assertEqual(query["network"], ['["S"]'])

    def test_find_routes_france_international_and_legacy_paris(self):
        cases = [({}, engine.GEO_FRANCE), ({"international": False}, engine.GEO_FRANCE),
                 ({"international": True}, None), ({"paris": True}, engine.GEO_PARIS)]
        for overrides, expected_geo in cases:
            with self.subTest(overrides=overrides):
                args = SimpleNamespace(keywords="startup founder", segments=None,
                                       paris=False, pages=2, network="S,O")
                for key, value in overrides.items():
                    setattr(args, key, value)
                context, page = self.browser()
                with patch.object(engine, "find_prospects", return_value=0) as find:
                    engine.cmd_find(args)
                find.assert_called_once_with(page, [], [("custom", "startup founder")], 2, "S,O", expected_geo,
                                             found_urls=set())
                context.close.assert_called_once()

    def test_find_snapshot_includes_known_matches_and_excludes_old_stock(self):
        current, stale = prospect("current"), prospect("stale")
        engine.save_rows(engine.PROSPECTS_CSV, [stale, current])
        self.browser()
        def find(_page, _rows, *_args, found_urls=None):
            found_urls.add(current["url"])
            return 0
        args = SimpleNamespace(keywords="  santé, IA\n founder ", segments=None,
                               international=True, paris=False, pages=1, network="S")
        with patch.object(engine, "find_prospects", side_effect=find), \
                patch.object(engine, "print_top") as show:
            engine.cmd_find(args)
        snapshot = json.loads((self.data_dir / "last_search.json").read_text())
        self.assertEqual(snapshot["keywords"], ["santé, IA", "founder"])
        self.assertEqual(snapshot["geography"], "international")
        self.assertEqual(snapshot["urls"], [current["url"]])
        self.assertIsNotNone(engine.datetime.fromisoformat(snapshot["finished_at"]).tzinfo)
        self.assertEqual([row["url"] for row in show.call_args.args[0]], [current["url"]])
        self.assertEqual(len(engine.load_rows(engine.PROSPECTS_CSV)), 2)

    def test_empty_find_snapshot_does_not_reuse_old_results(self):
        engine.save_rows(engine.PROSPECTS_CSV, [prospect("old")])
        (self.data_dir / "last_search.json").write_text('{"urls": ["old"]}')
        self.browser()
        args = SimpleNamespace(keywords="new search", segments=None, international=False,
                               paris=False, pages=1, network="S")
        with patch.object(engine, "find_prospects", return_value=0), \
                patch.object(engine, "print_top") as show:
            engine.cmd_find(args)
        snapshot = json.loads((self.data_dir / "last_search.json").read_text())
        self.assertEqual(snapshot["urls"], [])
        self.assertEqual(snapshot["geography"], "france")
        show.assert_called_once_with([])

    def test_failed_find_preserves_previous_completed_snapshot(self):
        previous = '{"urls": ["previous"], "finished_at": "previous run"}'
        (self.data_dir / "last_search.json").write_text(previous)
        context, _page = self.browser()
        args = SimpleNamespace(keywords="new search", segments=None, international=False,
                               paris=False, pages=1, network="S")
        with patch.object(engine, "find_prospects", side_effect=engine.Blocked("checkpoint")):
            with self.assertRaises(engine.Blocked):
                engine.cmd_find(args)
        self.assertEqual((self.data_dir / "last_search.json").read_text(), previous)
        context.close.assert_called_once()

    def test_find_cli_accepts_international(self):
        with patch.object(sys, "argv", ["linkedin_connect.py", "find", "--international"]), \
                patch.object(engine, "cmd_find") as find:
            engine.main()
        args = find.call_args.args[0]
        self.assertTrue(args.international)
        self.assertFalse(args.paris)

    def test_find_cli_rejects_conflicting_geographies_before_browser_start(self):
        with patch.object(sys, "argv", ["linkedin_connect.py", "find", "--paris", "--international"]), \
                patch.object(engine, "sync_playwright") as browser, patch.object(sys, "stderr"):
            with self.assertRaises(SystemExit) as raised:
                engine.main()
        self.assertEqual(raised.exception.code, 2)
        browser.assert_not_called()

    def test_selection_never_expands_to_other_eligible_profiles(self):
        chosen, unchosen = prospect("chosen", 60), prospect("unchosen", 100)
        engine.save_rows(engine.PROSPECTS_CSV, [unchosen, chosen])
        args = self.run_args(selected_file=self.selection([chosen["url"]]))
        context, page = self.browser()
        with patch.object(engine, "send_batch") as send:
            engine.cmd_run(args)
        self.assertEqual([r["url"] for r in send.call_args.args[2]], [chosen["url"]])
        self.assertEqual(len(send.call_args.args[1]), 2)
        context.close.assert_called_once()

    def test_selection_still_excludes_sent_or_ineligible_profiles(self):
        rows = [prospect("sent", status="sent"), prospect("low", score=5), prospect("ok")]
        engine.save_rows(engine.PROSPECTS_CSV, rows)
        args = self.run_args(selected_file=self.selection([r["url"] for r in rows]))
        self.browser()
        with patch.object(engine, "send_batch") as send:
            engine.cmd_run(args)
        self.assertEqual([r["name"] for r in send.call_args.args[2]], ["ok"])

    def test_invalid_selection_is_rejected_before_browser_start(self):
        row = prospect("known")
        engine.save_rows(engine.PROSPECTS_CSV, [row])
        cases = [[], {}, [42], [row["url"], row["url"]],
                 ["https://www.linkedin.com/in/unknown/"], ["known"]]
        for selection in cases:
            with self.subTest(selection=selection):
                args = self.run_args(selected_file=self.selection(selection))
                with patch.object(engine, "sync_playwright") as browser:
                    with self.assertRaises(ValueError):
                        engine.cmd_run(args)
                    browser.assert_not_called()

    def test_selection_rejects_invalid_urls_even_if_present_in_csv(self):
        for url in ["https://other.example/in/known/", "https://www.linkedin.com/in/known/?x=1",
                    "https://www.linkedin.com@other.example/in/known/", "file:///in/known/"]:
            with self.subTest(url=url):
                row = prospect("known")
                row["url"] = url
                with self.assertRaises(ValueError):
                    engine.selected_rows(self.selection([url]), [row])

    def test_selection_rejects_ambiguous_duplicate_rows(self):
        row = prospect("duplicate")
        with self.assertRaises(ValueError):
            engine.selected_rows(self.selection([row["url"]]), [row, row.copy()])

    def test_legacy_run_without_selected_file_is_supported(self):
        engine.save_rows(engine.PROSPECTS_CSV, [prospect("one")])
        args = self.run_args(dry_run=True)
        del args.selected_file
        with patch.object(engine, "print_top") as show, patch.object(engine, "sync_playwright") as browser:
            engine.cmd_run(args)
        self.assertEqual(len(show.call_args.args[0]), 1)
        browser.assert_not_called()

    def test_empty_eligible_selection_does_not_open_browser(self):
        row = prospect("sent", status="sent")
        engine.save_rows(engine.PROSPECTS_CSV, [row])
        with patch.object(engine, "sync_playwright") as browser:
            engine.cmd_run(self.run_args(selected_file=self.selection([row["url"]])))
        browser.assert_not_called()

    def test_commands_propagate_blocks_and_close_context(self):
        engine.save_rows(engine.PROSPECTS_CSV, [prospect("one")])
        commands = [
            (engine.cmd_find, "find_prospects", SimpleNamespace(
                keywords="founder", segments=None, paris=False, pages=1, network="S")),
            (engine.cmd_enrich, "enrich_rows", SimpleNamespace(top=1, min_score=40)),
            (engine.cmd_run, "send_batch", self.run_args()),
        ]
        for command, operation, args in commands:
            with self.subTest(command=command.__name__):
                context, _ = self.browser()
                with patch.object(engine, operation, side_effect=engine.Blocked("verification")):
                    with self.assertRaises(engine.Blocked):
                        command(args)
                context.close.assert_called_once()

    def test_start_closes_context_on_navigation_failure(self):
        context = MagicMock()
        with patch.object(engine, "open_context", return_value=context), \
                patch.object(engine, "is_logged_in", side_effect=RuntimeError("navigation")):
            with self.assertRaises(RuntimeError):
                engine.start(MagicMock())
        context.close.assert_called_once()

    def test_start_reports_checkpoint_as_blocked(self):
        context = MagicMock()
        with patch.object(engine, "open_context", return_value=context), \
                patch.object(engine, "is_logged_in", return_value=False), \
                patch.object(engine, "check_blocked", side_effect=engine.Blocked("checkpoint")):
            with self.assertRaises(engine.Blocked):
                engine.start(MagicMock())
        context.close.assert_called_once()

    def test_login_check_requires_the_linkedin_feed_page(self):
        page = MagicMock()
        cases = [
            ("https://www.linkedin.com/feed/", True),
            ("https://www.linkedin.com/feed?trk=login", True),
            ("https://www.linkedin.com/login?session_redirect=/feed/", False),
            ("https://www.linkedin.com/checkpoint/?redirect=/feed/", False),
            ("https://other.example/feed/", False),
        ]
        with patch.object(engine, "goto"), patch.object(engine, "pause"):
            for url, expected in cases:
                with self.subTest(url=url):
                    page.url = url
                    self.assertEqual(engine.is_logged_in(page), expected)

    def test_sent_invitation_is_recorded_before_a_followup_security_check(self):
        row = prospect("one")
        results = []
        with patch.object(engine, "send_invite", return_value="sent"), \
                patch.object(engine, "check_blocked", side_effect=engine.Blocked("checkpoint")):
            with self.assertRaises(engine.Blocked):
                engine.send_batch(MagicMock(), [row], [row], 1, False, results)
        self.assertEqual(engine.sent_since(1), 1)
        self.assertEqual(engine.load_rows(engine.PROSPECTS_CSV)[0]["status"], "sent")
        self.assertEqual(results[0]["status"], "sent")

    def test_sent_invitation_counts_toward_caps_when_prospects_cannot_be_saved(self):
        row = prospect("one")
        with patch.object(engine, "send_invite", return_value="sent"), \
                patch.object(engine, "save_rows", side_effect=OSError("disk unavailable")), \
                patch.object(engine, "check_blocked"):
            with self.assertRaises(OSError):
                engine.send_batch(MagicMock(), [row], [row], 1, False, [])
        self.assertEqual(engine.sent_since(1), 1)
        self.assertEqual(engine.remaining_budget(engine.DAILY_CAP), engine.DAILY_CAP - 1)

    def test_empty_existing_sent_log_keeps_the_first_invitation(self):
        engine.LOG_FILE.touch()
        engine.log_sent("https://www.linkedin.com/in/first/")
        engine.log_sent("https://www.linkedin.com/in/second/")
        self.assertEqual(engine.sent_since(1), 2)
        self.assertEqual(engine.LOG_FILE.read_text().count("date,url"), 1)

    def test_batch_and_enrichment_do_not_swallow_blocks(self):
        row = prospect("one")
        page = MagicMock()
        with patch.object(engine, "last_post_days", side_effect=engine.Blocked("checkpoint")):
            with self.assertRaises(engine.Blocked):
                engine.enrich_rows(page, [row], 1, 40)
        with patch.object(engine, "send_invite", side_effect=engine.Blocked("checkpoint")):
            with self.assertRaises(engine.Blocked):
                engine.send_batch(page, [row], [row], 1, False, [])
        self.assertEqual(row["status"], "")

    def test_login_wait_detects_feed_without_terminal_input(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/login"
        page.is_closed.return_value = False
        page.wait_for_timeout.side_effect = lambda _: setattr(page, "url", "https://www.linkedin.com/feed/")
        context = MagicMock(pages=[page])
        with patch.object(engine, "sync_playwright"), \
                patch.object(engine, "open_context", return_value=context), \
                patch("builtins.input") as terminal_input:
            engine.cmd_login(SimpleNamespace(wait=True, timeout=300))
        terminal_input.assert_not_called()
        context.close.assert_called_once()

    def test_login_wait_timeout_closes_context(self):
        page = MagicMock(url="https://www.linkedin.com/checkpoint/")
        page.is_closed.return_value = False
        context = MagicMock(pages=[page])
        with patch.object(engine, "sync_playwright"), \
                patch.object(engine, "open_context", return_value=context), \
                patch.object(engine.time, "monotonic", side_effect=[0, 0, 301]):
            with self.assertRaises(engine.NotLoggedIn):
                engine.cmd_login(SimpleNamespace(wait=True, timeout=300))
        context.close.assert_called_once()

    def test_cli_exit_codes_are_distinct(self):
        for exception, code in [(engine.NotLoggedIn("login"), 3), (engine.Blocked("blocked"), 4),
                                (OperationBusy("busy"), 5), (ValueError("selection"), 2)]:
            with self.subTest(code=code), patch.object(sys, "argv", ["linkedin_connect.py", "rescore"]), \
                    patch.object(engine, "cmd_rescore", side_effect=exception):
                with self.assertRaises(SystemExit) as raised:
                    engine.main()
                self.assertEqual(raised.exception.code, code)
                self.assertFalse(operation_in_progress(self.data_dir / ".operation.lock"))


class LockTests(unittest.TestCase):
    def test_lock_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".operation.lock"
            with self.assertRaisesRegex(RuntimeError, "failure"):
                with OperationLock(path):
                    self.assertTrue(operation_in_progress(path))
                    raise RuntimeError("failure")
            self.assertFalse(operation_in_progress(path))

    def test_every_cli_command_obeys_same_process_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".operation.lock"
            env = dict(os.environ, LINKEDIN_DATA_DIR=directory)
            with OperationLock(path):
                for command in ["login", "find", "enrich", "rescore", "run", "auto"]:
                    with self.subTest(command=command):
                        result = subprocess.run(
                            [sys.executable, str(ROOT / "linkedin_connect.py"), command],
                            env=env, text=True, capture_output=True, timeout=10,
                        )
                        self.assertEqual(result.returncode, 5, result.stderr)
                        self.assertIn("déjà en cours", result.stderr)
            self.assertEqual([p.name for p in Path(directory).iterdir()], [".operation.lock"])
            result = subprocess.run(
                [sys.executable, str(ROOT / "linkedin_connect.py"), "rescore"],
                env=env, text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(directory) / "prospects.csv").exists())


if __name__ == "__main__":
    unittest.main()
