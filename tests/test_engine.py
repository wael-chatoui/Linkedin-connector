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
