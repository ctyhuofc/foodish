"""Daily health check: a one-screen summary of how the robot did.

On GitHub it appears on the run's page (Actions → the run → Summary). Locally it prints to the terminal.
Reads each city's cache/report.json (written by scrape.py) and deals.json. No extra packages.

    python pipeline/health.py                  summary for every city
    python pipeline/health.py --gate [cities]  exit 1 if the robot is broken (last workflow step)

It answers: how many deals went out, which big chains we can't read (most locations lost),
how much AI was used, and which hand-added deals are now duplicated by the robot.
"""
from __future__ import annotations

import collections
import difflib
import json
import os
import re
import datetime as dt
import sys
from pathlib import Path

import cities
import deal_state as ds  # each city's clock (fixed offsets since Nov 2026)

ROOT = Path(__file__).resolve().parent.parent


def ds_utc_date() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def price_num(p):
    m = re.search(r"\d+(?:\.\d+)?", p or "")
    return float(m.group()) if m else None


def gate(report: dict, today: set[str]) -> str | None:
    """Is the robot broken? Returns what's wrong (and how to fix it), or None if it's working.
    `today` holds the dates that count as today (scrape.py stamps its report with the runner's UTC date).
    Used by the last workflow step: a non-None answer turns the run red, so GitHub emails the owner."""
    if report.get("date") not in today:
        return (f"Today's deal refresh didn't finish (the latest report is from {report.get('date', 'never')}). "
                "Open this run's 'Scrape deals' step to see the error.")
    stopped = report.get("ai_stopped")
    if not stopped:
        return None
    low = stopped.lower()
    if "401" in stopped or "x-api-key" in low or "authentication" in low or "llm_api_key" in low:
        fix = ("The AI key in GitHub isn't valid (it was probably replaced or deleted). Create a key at "
               "console.anthropic.com and save it as the LLM_API_KEY secret: repo Settings → Secrets and variables → Actions.")
    elif "credit" in low or "billing" in low or "quota" in low or "429" in stopped:
        fix = "The AI account is out of credit or over its limit. Add credit or raise the limit in the AI provider's console."
    else:
        fix = "The AI step kept failing the same way, so no new deals were read today."
    return f"{fix} Error: {stopped}. Deals already on the site are kept, but nothing new is being picked up."


def check_gate(which: list[cities.City]) -> None:
    problems = []
    for city in which:
        report = json.loads(city.report.read_text()) if city.report.exists() else {}
        problem = gate(report, {ds.now_in(city.timezone).date, ds_utc_date()})
        if problem:
            problems.append(f"{city.name}: {problem}")
    if problems:
        for p in problems:
            print(f"::error title=Foodish robot needs attention::{p}")
        sys.exit(1)
    print(f"Robot is healthy for {', '.join(c.name for c in which)}: today's refresh ran and the AI step worked.")


def summary(city: cities.City) -> list[str]:
    lines: list[str] = []
    report = json.loads(city.report.read_text()) if city.report.exists() else {}
    data = json.loads(city.deals.read_text()) if city.deals.exists() else {"deals": [], "restaurants": []}
    sites = report.get("sites", {})
    status = report.get("status_counts", {})
    total = sum(status.values()) or 1
    ok = status.get("ok", 0)

    lines.append(f"## Foodish daily check · {city.name} · {report.get('date', 'no run yet')}")
    lines.append("")
    lines.append(f"- **Published:** {len(data['deals'])} deals at {len(data['restaurants'])} locations")
    lines.append(f"- **Websites read:** {ok} of {total} ({ok * 100 // total}%)")
    lines.append(f"- **AI calls this run:** {report.get('ai_calls', 0)}" + (f" · ⚠️ stopped: {report['ai_stopped']}" if report.get("ai_stopped") else ""))
    # Per-site outcomes (pipeline/site_state.py). Older reports don't have them.
    oc = report.get("outcome_counts") or {}
    if oc:
        labels = [("unchanged", "unchanged"), ("extracted", "read by the AI"), ("no_deals", "no deals posted"),
                  ("unreachable", "unreachable today"), ("gone", "gone (deals removed)")]
        parts = [f"{oc[k]} {label}" for k, label in labels if oc.get(k)]
        lines.append(f"- **Sites:** {', '.join(parts) or 'none'}")
        waiting = {k: oc.get(k, 0) for k in ("deferred_budget", "ai_error", "deferred_ai_stopped")}
        if any(waiting.values()):
            why = {"deferred_budget": "AI limit reached", "ai_error": "AI error", "deferred_ai_stopped": "AI stopped"}
            lines.append("- ⚠️ **Waiting for tomorrow (old deals kept):** "
                         + ", ".join(f"{n} ({why[k]})" for k, n in waiting.items() if n))
    if city.cache.exists():  # scrape.py keeps monthly AI usage under "_usage"
        try:
            usage = json.loads(city.cache.read_text()).get("_usage", {})
            month, used = sorted(usage.items())[-1]
            lines.append(f"- **AI calls this month ({month}):** {used}")
        except Exception:  # noqa: BLE001
            pass

    # Biggest losses: unreadable sites ranked by how many of the city's locations they cover
    failing = sorted(((v.get("locations", 0), k, v.get("status")) for k, v in sites.items() if v.get("status") != "ok"), reverse=True)
    if failing:
        lines += ["", "### Chains we couldn't read (most locations first)", "", "| Site | Locations | Problem |", "| --- | --- | --- |"]
        lines += [f"| {k} | {n} | {s} |" for n, k, s in failing[:10]]

    # Readable sites whose pages show no text: they need RENDER_JS=1 (JavaScript-only sites)
    js_only = sorted(((v.get("locations", 0), k) for k, v in sites.items() if v.get("status") == "ok" and v.get("js_page") and not v.get("deal_chars")), reverse=True)
    if js_only:
        lines += ["", f"**JavaScript-only sites with no readable text:** {', '.join(f'{k} ({n})' for n, k in js_only[:8])}. "
                      "Setting the GitHub variable `RENDER_JS` = `1` lets the robot read these."]

    # Likely duplicates: a hand-added deal (data/manual_deals.json) that the robot now also finds on the website
    manual_path = city.manual_deals
    manual_ids = {m.get("id") for m in json.loads(manual_path.read_text())} if manual_path.exists() else set()
    names = {r["id"]: r.get("name", "") for r in data["restaurants"]}
    manual = [d for d in data["deals"] if d.get("id") in manual_ids]
    scraped = [d for d in data["deals"] if d.get("id") not in manual_ids]
    dupes = []
    for m in manual:
        for s_ in scraped:
            shared = set(m.get("restaurant_ids", [])) & set(s_.get("restaurant_ids", []))
            if not shared:
                continue
            same_days = set(m.get("days") or range(7)) & set(s_.get("days") or range(7))
            same_type = set(m.get("categories") or []) & set(s_.get("categories") or [])
            pm, ps = price_num(m.get("price")), price_num(s_.get("price"))
            close_title = difflib.SequenceMatcher(None, norm(m.get("title")), norm(s_.get("title"))).ratio() >= 0.7
            if same_days and same_type and ((pm is not None and pm == ps) or close_title):
                dupes.append(f"{names.get(next(iter(shared)), '?')}: your manual “{m.get('title')}” ({m.get('id')}) looks like the website's “{s_.get('title')}”")
    if dupes:
        lines += ["", "### Hand-added deals the robot now finds itself", ""] + [f"- {x}" for x in sorted(set(dupes))[:10]]
        lines.append(f"\nIf they match, delete the manual entry from `{manual_path.relative_to(ROOT)}` so the deal isn't listed twice.")
    return lines


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--gate"]
    if "--gate" in sys.argv:
        check_gate(cities.pick(args))
        return
    lines: list[str] = []
    for city in cities.pick(args):
        if not city.report.exists() and not city.deals.exists():
            lines += [f"## Foodish daily check · {city.name} · no run yet", "",
                      "This city's restaurants haven't been found yet. It starts on the next daily run.", ""]
            continue
        lines += summary(city) + [""]
    out = "\n".join(lines) + "\n"
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(out)
    print(out)


if __name__ == "__main__":
    main()
