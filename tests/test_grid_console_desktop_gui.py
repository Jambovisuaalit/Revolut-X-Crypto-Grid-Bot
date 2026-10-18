"""Windows GUI entrypoint safety tests, independent of any real accounts."""
from __future__ import annotations

from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from windows_distribution import grid_console_desktop as gui
from tests.react_fixture import make_react_fixture


class GridConsoleDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._bundle_temp = tempfile.TemporaryDirectory()
        cls.react_root = make_react_fixture(Path(cls._bundle_temp.name))

    @classmethod
    def tearDownClass(cls):
        cls._bundle_temp.cleanup()

    def offline_status(self):
        return gui.status_through_loopback(react_root=self.react_root)

    def test_gui_01_loopback_ui_and_status_are_available(self):
        result = self.offline_status()
        self.assertEqual(result["schema"], gui.SCHEMA)
        self.assertEqual(result["status"], "DEV_DESKTOP_PASS")
        self.assertEqual(result["ui"], "GRID_CONSOLE_REACT_LOCAL_GUI")
        self.assertEqual(result["http_bind"], "127.0.0.1")
        self.assertEqual(result["local_assets"], "VERIFIED")
        self.assertGreaterEqual(result["react_assets"], 2)
        self.assertEqual(result["runtime_status"], "NOT_CONFIGURED")

    def test_gui_02_no_api_or_market_collector(self):
        result = self.offline_status()
        self.assertEqual(result["account_connection"], "NOT_CONNECTED")
        self.assertEqual(result["live_orders"], "DISABLED")
        self.assertEqual(result["market_collector"], "NOT_STARTED")
        self.assertEqual(result["authenticated_account_get"], "NOT_RUN")
        self.assertEqual(result["exchange_mutations"], 0)
        self.assertIs(result["signed_installer"], False)
        self.assertEqual(result["sellable_product"], "NO_GO")

    def test_gui_03_unexpected_live_env_is_blocked(self):
        with patch.dict(os.environ, {"LIVE_TRADING": "true"}):
            with self.assertRaisesRegex(RuntimeError, "LIVE_ENV_FORBIDDEN"):
                gui.status_through_loopback()
            self.assertEqual(gui.main(["--self-test"]), 2)

    def test_gui_04_status_never_claims_real_account_or_started_strategy(self):
        base = dict(gui.OFFLINE_REQUIRED, strategy={"state": "NOT_STARTED"},
                    equity_eur=None, realized_pnl_eur=None)
        gui.check_status(base)
        for change in (
            {"account_connection":"CONNECTED"},
            {"live_orders":"ENABLED"},
            {"mode":"LIVE"},
            {"equity_eur":"1000"},
            {"realized_pnl_eur":"20"},
            {"strategy":{"state":"RUNNING"}},
        ):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                gui.check_status(base | change)

    def test_gui_05_self_test_report_contains_no_sensitive_data(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/"result.json"
            original = gui.status_through_loopback
            with patch.object(gui, "status_through_loopback",
                              side_effect=lambda supervisor_db=None, shadow_db=None: original(
                                  supervisor_db, shadow_db=shadow_db, react_root=self.react_root)):
                code=gui.main(["--self-test", "--report", str(out)])
            self.assertEqual(code,0)
            payload=json.loads(out.read_text("utf-8"))
            self.assertEqual(payload["status"],"DEV_DESKTOP_PASS")
            self.assertFalse(any(x in payload for x in
                                 ("api_key","balance","order_id","private_key")))
            self.assertEqual(payload["live_orders"],"DISABLED")

    def test_gui_06_failed_self_test_writes_fail_closed_redacted_report(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/"result.json"
            with patch.object(gui,"status_through_loopback",
                              side_effect=RuntimeError("PRIVATE-DATA-SHOULD-NOT-LEAK")):
                self.assertEqual(gui.main(["--self-test", "--report",str(out)]),2)
            body=out.read_text("utf-8")
            self.assertIn("DEV_DESKTOP_FAIL",body)
            self.assertIn("NO_GO",body)
            self.assertNotIn("PRIVATE-DATA",body)

    def test_gui_07_launch_module_does_not_load_account_credentials(self):
        src=Path(gui.__file__).read_text("utf-8")
        for token in (
            "from revolut_x_client import",
            "from account_readonly import",
            "from windows_key_vault import",
            "from pilot_budget_store import",
            "cancel_all_orders(",
            "place_order(",
            "requests.post(",
        ):
            self.assertNotIn(token,src)

    def test_gui_08_report_cannot_be_created_outside_self_test_mode(self):
        with self.assertRaises(SystemExit) as exc:
            gui.main(["--report","should-not-write.json"])
        self.assertEqual(exc.exception.code,2)


if __name__ == "__main__":
    unittest.main()
