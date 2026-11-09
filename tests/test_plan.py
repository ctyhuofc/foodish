"""The run plan (pipeline/plan.py) and the health gate (pipeline/health.py --gate)."""
import itertools
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import health  # noqa: E402
import plan  # noqa: E402

WORKFLOW = (ROOT / ".github" / "workflows" / "daily.yml").read_text()


def legacy(event, schedule, task, restaurants_empty, age):
    """What daily.yml did before plan.py existed (cron strings in each step's if: + shell checks).
    Kept as the reference the new plan must match."""
    task = task if event == "workflow_dispatch" else ""
    discover = (schedule == "5 9 * * 0" or task in ("discover", "all")
                or (event == "schedule" and restaurants_empty))
    scrape = schedule in ("17 10 * * *", "47 11 * * *", "23 13 * * *", "5 9 * * 0") or task in ("scrape", "all")
    if scrape and schedule in ("47 11 * * *", "23 13 * * *") and age is not None and int(age) < 12:
        scrape = False
    notify = schedule == "45 21 * * *" or task in ("notify", "all")
    return discover, scrape, notify


class MatchesTheOldWorkflow(unittest.TestCase):
    def test_every_combination(self):
        events = [("schedule", s, "") for s in plan.SCHEDULES] + [("workflow_dispatch", "", t) for t in plan.TASKS]
        for (event, schedule, task), empty, age in itertools.product(events, (False, True), (None, 2.5, 11.9, 12.0, 30)):
            with self.subTest(event=event, schedule=schedule, task=task, empty=empty, age=age):
                p = plan.decide(event, schedule, task, restaurants_empty=empty, deals_age_hours=age)
                want = legacy(event, schedule, task, empty, age)
                if event == "workflow_dispatch" and task == "scrape" and empty:
                    # one deliberate change: a manual "scrape" with no restaurants now finds them first
                    want = (True,) + want[1:]
                self.assertEqual((p.discover, p.scrape, p.notify), want)


class Guards(unittest.TestCase):
    def test_workflow_and_plan_list_the_same_schedules(self):
        in_yaml = set(re.findall(r'-\s*cron:\s*"([^"]+)"', WORKFLOW))
        self.assertEqual(in_yaml, set(plan.SCHEDULES), "edit both daily.yml and SCHEDULES in pipeline/plan.py")

    def test_unknown_schedule_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(ValueError):
            plan.decide("schedule", "0 4 * * *")

    def test_typo_in_manual_task_is_an_error(self):
        with self.assertRaises(ValueError):
            plan.decide("workflow_dispatch", task="scrap")

    def test_steps_use_the_plan_not_cron_strings(self):
        for line in WORKFLOW.splitlines():
            if line.strip().startswith("if:"):
                self.assertNotIn("github.event.schedule", line)

    def test_backup_runs_when_deals_file_is_missing(self):
        self.assertTrue(plan.decide("schedule", "47 11 * * *", deals_age_hours=None).scrape)


class HealthGate(unittest.TestCase):
    TODAY = {"2026-11-09"}

    def test_healthy(self):
        self.assertIsNone(health.gate({"date": "2026-11-09", "ai_stopped": None}, self.TODAY))

    def test_bad_key_says_how_to_fix_it(self):
        msg = health.gate({"date": "2026-11-09", "ai_stopped": "HTTP 401 authentication_error: invalid x-api-key"}, self.TODAY)
        self.assertIn("LLM_API_KEY", msg)

    def test_out_of_credit(self):
        msg = health.gate({"date": "2026-11-09", "ai_stopped": "HTTP 400 invalid_request_error: credit balance too low"}, self.TODAY)
        self.assertIn("credit", msg)

    def test_refresh_that_never_finished(self):
        self.assertIn("didn't finish", health.gate({"date": "2026-11-08"}, self.TODAY))


if __name__ == "__main__":
    unittest.main()
