"""Python side of the shared deal-timing rules: must pass every case in spec/deal-states.json."""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import deal_state as ds  # noqa: E402

SPEC = json.loads((ROOT / "spec" / "deal-states.json").read_text())


class SharedCases(unittest.TestCase):
    def test_every_spec_case(self):
        for case in SPEC["cases"]:
            with self.subTest(case["name"]):
                got = ds.deal_state(case["deal"], ds.moment_from_local(case["at"]))
                self.assertEqual(got, case["expect"])

    def test_spec_lists_exactly_the_states_the_code_returns(self):
        self.assertEqual(set(SPEC["states"]), set(ds.STATES))
        self.assertEqual({c["expect"] for c in SPEC["cases"]}, set(ds.STATES), "every state needs at least one case")


class EdmontonClock(unittest.TestCase):
    """Alberta stays on UTC-6 year-round from 1 Nov 2026; these must not depend on the machine's tz data."""

    def test_november_evening_is_utc_minus_6(self):
        m = ds.edmonton_now(dt.datetime(2026, 11, 11, 23, 30, tzinfo=dt.timezone.utc))
        self.assertEqual((m.date, m.day, m.mins), ("2026-11-11", 3, 17 * 60 + 30))

    def test_utc_early_morning_is_still_yesterday_in_edmonton(self):
        m = ds.edmonton_now(dt.datetime(2026, 11, 12, 3, 0, tzinfo=dt.timezone.utc))
        self.assertEqual((m.date, m.day, m.mins), ("2026-11-11", 3, 21 * 60))

    def test_midnight_reads_as_zero(self):
        self.assertEqual(ds.edmonton_now(dt.datetime(2026, 11, 12, 6, 0, tzinfo=dt.timezone.utc)).mins, 0)

    def test_january_2027_stays_utc_minus_6(self):
        m = ds.edmonton_now(dt.datetime(2027, 1, 15, 18, 0, tzinfo=dt.timezone.utc))
        self.assertEqual((m.date, m.mins), ("2027-01-15", 12 * 60))

    def test_summer_time(self):
        m = ds.edmonton_now(dt.datetime(2026, 7, 1, 18, 0, tzinfo=dt.timezone.utc))
        self.assertEqual((m.date, m.mins), ("2026-07-01", 12 * 60))

    def test_old_winters_were_utc_minus_7(self):
        m = ds.edmonton_now(dt.datetime(2026, 1, 15, 19, 0, tzinfo=dt.timezone.utc))
        self.assertEqual((m.date, m.mins), ("2026-01-15", 12 * 60))


if __name__ == "__main__":
    unittest.main()
