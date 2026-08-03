import tempfile
import unittest
from pathlib import Path

from grid_executor import GridExecutor
from revolut_x_client import RevolutXError


class FailingCancelClient:
    def cancel_all_orders(self) -> None:
        raise RevolutXError("network failure")


class GridExecutorTests(unittest.TestCase):
    def test_kill_switch_persists_before_cancel_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "state.db")
            executor = GridExecutor(FailingCancelClient(), db_path=db, dry_run=False)  # type: ignore[arg-type]
            try:
                with self.assertRaises(RevolutXError):
                    executor.trigger_kill_switch()
                self.assertEqual(executor.get_meta("kill_switch_triggered"), "true")
            finally:
                executor.close()


if __name__ == "__main__":
    unittest.main()
