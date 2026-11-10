"""Which jobs does this GitHub Actions run do? One place that decides, instead of cron strings
copied into every step's `if:`.

The workflow (.github/workflows/daily.yml) runs this first and then each step just reads
`steps.plan.outputs.<job>`. To change a schedule, edit `on.schedule` in daily.yml AND the
SCHEDULES table below; tests/test_plan.py fails if the two disagree.

Try it locally:
    python pipeline/plan.py --event schedule --schedule "47 11 * * *"
    python pipeline/plan.py --event workflow_dispatch --task notify

Jobs:  discover (find restaurants on OpenStreetMap)  ·  scrape (read websites, publish deals)
       notify (send daily push alerts)
The decision is made per city (cities.json), from that city's own files: a new city with no
restaurant list gets discovered first, and a backup run only refreshes cities whose deals are old.
Outputs: discover / scrape / notify (true|false) and discover_cities / scrape_cities (city ids).
The search pages and the health check run on every run (they only read each city's deals.json).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import cities

ROOT = Path(__file__).resolve().parent.parent

# GitHub cron (UTC) -> what that schedule is for. Edmonton is UTC-6 year-round since Nov 2026.
DAILY, BACKUP, NOTIFY, WEEKLY = "daily", "backup", "notify", "weekly"
SCHEDULES = {
    "17 10 * * *": DAILY,    # 4:17 am Edmonton: refresh deals
    "47 11 * * *": BACKUP,   # 5:47 am: refresh only if the 4 am run didn't happen (GitHub can drop or delay runs)
    "23 13 * * *": BACKUP,   # 7:23 am: same rule
    "45 21 * * *": NOTIFY,   # 3:45 pm: send daily alerts
    "5 9 * * 0": WEEKLY,     # Sunday 3:05 am: re-discover restaurants, then refresh deals
}
TASKS = ("all", "discover", "scrape", "notify", "pages")  # manual "Run workflow" choices
BACKUP_FRESH_HOURS = 12  # a backup run skips the refresh if deals are newer than this
DAILY_FRESH_HOURS = 3    # the 4:17 run skips if the exact 4 am trigger (worker/cron) already refreshed


@dataclass(frozen=True)
class Plan:
    discover: bool
    scrape: bool
    notify: bool
    why: str


def decide(event: str, schedule: str = "", task: str = "", *, restaurants_empty: bool = False,
           deals_age_hours: float | None = None) -> Plan:
    """Pure decision table. `deals_age_hours` = hours since data/deals.json was generated (None if unknown)."""
    if event == "schedule":
        role = SCHEDULES.get(schedule)
        if role is None:
            raise ValueError(f"Unknown schedule {schedule!r}: add it to SCHEDULES in pipeline/plan.py")
        if role == WEEKLY:
            return Plan(True, True, False, "weekly: find restaurants, then refresh deals")
        if role == NOTIFY:
            return Plan(restaurants_empty, False, True, "afternoon: send alerts")
        if role == BACKUP and deals_age_hours is not None and deals_age_hours < BACKUP_FRESH_HOURS:
            return Plan(restaurants_empty, False, False,
                        f"backup: deals refreshed {int(deals_age_hours)} h ago, nothing to do")
        if role == DAILY and deals_age_hours is not None and deals_age_hours < DAILY_FRESH_HOURS:
            return Plan(restaurants_empty, False, False,
                        f"morning: the 4 am trigger already refreshed deals {deals_age_hours * 60:.0f} min ago")
        return Plan(restaurants_empty, True, False, "backup: the morning refresh hadn't run" if role == BACKUP else "morning: refresh deals")

    task = (task or "all").strip().lower()
    if task not in TASKS:
        raise ValueError(f"Unknown task {task!r}: choose one of {', '.join(TASKS)}")
    scrape = task in ("all", "scrape")
    discover = task in ("all", "discover") or (restaurants_empty and scrape)
    return Plan(discover, scrape, task in ("all", "notify"), f"manual run: {task}")


def _facts(city: cities.City) -> dict:
    facts = {"restaurants_empty": True, "deals_age_hours": None}
    try:
        facts["restaurants_empty"] = not json.loads(city.restaurants.read_text())
    except (OSError, ValueError):
        pass
    try:
        g = dt.datetime.fromisoformat(json.loads(city.deals.read_text())["generated_at"])
        facts["deals_age_hours"] = (dt.datetime.now(dt.timezone.utc) - g).total_seconds() / 3600
    except (OSError, ValueError, KeyError):
        pass
    return facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--event", default=os.environ.get("PLAN_EVENT", "workflow_dispatch"))
    ap.add_argument("--schedule", default=os.environ.get("PLAN_SCHEDULE", ""))
    ap.add_argument("--task", default=os.environ.get("PLAN_TASK", ""))
    args = ap.parse_args(argv)
    try:
        plans = {c.id: decide(args.event, args.schedule, args.task, **_facts(c)) for c in cities.all_cities()}
    except ValueError as e:
        print(f"::error::{e}")
        return 1
    for cid, p in plans.items():
        print(f"Plan for {cid}: {p.why} -> discover={str(p.discover).lower()}, scrape={str(p.scrape).lower()}, notify={str(p.notify).lower()}")
    discover = [cid for cid, p in plans.items() if p.discover]
    scrape = [cid for cid, p in plans.items() if p.scrape]
    out = {
        "discover": str(bool(discover)).lower(), "scrape": str(bool(scrape)).lower(),
        "notify": str(any(p.notify for p in plans.values())).lower(),
        "discover_cities": " ".join(discover), "scrape_cities": " ".join(scrape),
        "why": "; ".join(f"{cid}: {p.why}" for cid, p in plans.items()),
    }
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.writelines(f"{k}={v}\n" for k, v in out.items())
    return 0


if __name__ == "__main__":
    sys.exit(main())
