"""The exact-time trigger (worker/cron) asks for a task the workflow knows, for every time it runs."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import plan  # noqa: E402


class CronTrigger(unittest.TestCase):
    def test_every_cloudflare_time_has_a_task_the_workflow_accepts(self):
        toml = (ROOT / "worker" / "cron" / "wrangler.toml").read_text()
        crons = json.loads(re.search(r"crons\s*=\s*(\[.*?\])", toml).group(1))
        js = (ROOT / "worker" / "cron" / "worker.js").read_text()
        tasks = dict(re.findall(r'"([\d*/ ,-]+)":\s*"(\w+)"', js))
        self.assertEqual(set(crons), set(tasks), "wrangler.toml crons and TASKS in worker.js must match")
        for task in tasks.values():
            self.assertIn(task, plan.TASKS)
        self.assertEqual(tasks["0 10 * * *"], "scrape")
        self.assertEqual(tasks["45 21 * * *"], "notify")


if __name__ == "__main__":
    unittest.main()
