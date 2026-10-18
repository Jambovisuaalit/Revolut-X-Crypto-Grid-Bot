"""Local GET-only shadow endpoint; synthetic fixtures and no exchange calls."""
from __future__ import annotations
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import http.client
import json
import unittest

from eth_shadow_v1.config import Config
from eth_shadow_v1.grid import PairRules
from eth_shadow_v1.journal import Journal
from product_shell.server import local_server
from product_shell.shadow_readonly import CONFIG_PATH
from scripts.run_eth_eur_shadow import fixture
from tests.react_fixture import make_react_fixture


class ShadowHttpTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.path = root / "v05-test-shadow.sqlite"
        payload = fixture()
        with Journal(self.path, "v05-api-fixture", Config.load(CONFIG_PATH),
                     PairRules.from_dict(payload["pair_rules"])) as journal:
            for event in payload["events"]:
                journal.process(event)
        self.server = local_server(
            None, 0, shadow_db=self.path, ui="react",
            react_root=make_react_fixture(root / "react"),
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._close)

    def _close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def request(self, method: str):
        with closing(http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=4)) as conn:
            conn.request(method,"/api/shadow")
            response=conn.getresponse()
            return response.status,response.read()

    def test_verified_simulated_financials_only(self):
        code,body=self.request("GET")
        self.assertEqual(code,200)
        data=json.loads(body)
        self.assertEqual(data["journal_integrity"],"PASS")
        self.assertEqual(data["status"],"READY")
        self.assertEqual(data["source"],"SYNTHETIC_FIXTURE_ONLY")
        self.assertEqual(data["live_orders"],"DISABLED")
        self.assertEqual(data["account_connection"],"NOT_CONNECTED")
        self.assertEqual(data["reconciled_for_trading"],False)
        self.assertIsNotNone(data["total_pnl_eur"])
        self.assertNotIn(str(self.path),body.decode("utf-8"))

    def test_unsafe_http_verbs_rejected(self):
        for method in ("POST","PUT","PATCH","DELETE"):
            self.assertEqual(self.request(method)[0],405)

    def test_get_does_not_modify_shadow_journal(self):
        before=self.path.read_bytes()
        self.request("GET")
        self.assertEqual(before,self.path.read_bytes())


if __name__=="__main__":
    unittest.main()
