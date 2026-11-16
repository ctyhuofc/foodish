"""Daily alerts only count deals still on or still to come today (pipeline/notify.py still_to_come)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import deal_state as ds  # noqa: E402

try:
    import notify  # noqa: E402  (needs pywebpush, installed by pipeline/requirements.txt)
except ImportError:
    notify = None


@unittest.skipUnless(notify, "pywebpush isn't installed (pip install -r pipeline/requirements.txt)")
class StillToCome(unittest.TestCase):
    def test_evening_alert(self):
        wed = 3
        deals = [
            {"id": "lunch", "days": [wed], "start": "11:00", "end": "14:00"},
            {"id": "happy", "days": [wed], "start": "17:00", "end": "21:00"},
            {"id": "late", "days": [wed], "start": "21:00", "end": "23:00"},
            {"id": "allday", "days": [wed], "start": None, "end": None},
            {"id": "thursday", "days": [4], "start": None, "end": None},
        ]
        at = ds.Moment("2026-11-18", wed, 19 * 60 + 30)
        self.assertEqual([d["id"] for d in notify.still_to_come(deals, at)], ["happy", "late", "allday"])


if __name__ == "__main__":
    unittest.main()
