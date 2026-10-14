"""Daily health check: a one-screen summary of how the robot did.

On GitHub it appears on the run's page (Actions → the run → Summary). Locally it prints to the terminal.
Reads data/cache/report.json (written by scrape.py) and data/deals.json. No extra packages.

It answers: how many deals went out, which big chains we can't read (most locations lost),
how much AI was used, and which hand-added deals are now duplicated by the robot.
"""
from __future__ import annotations

import collections
import difflib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "data" / "cache" / "report.json"
DEALS = ROOT / "data" / "deals.json"
CACHE = ROOT / "data" / "cache" / "sites.json"  # scrape.py keeps monthly AI usage under "_usage"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def price_num(p):
    m = re.search(r"\d+(?:\.\d+)?", p or "")
    return float(m.group()) if m else None


def main() -> None:
    lines: list[str] = []
    report = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    data = json.loads(DEALS.read_text()) if DEALS.exists() else {"deals": [], "restaurants": []}
    sites = report.get("sites", {})
    status = report.get("status_counts", {})
    total = sum(status.values()) or 1
    ok = status.get("ok", 0)

    lines.append(f"## Foodish daily check · {report.get('date', '?')}")
    lines.append("")
    lines.append(f"- **Published:** {len(data['deals'])} deals at {len(data['restaurants'])} locations")
    lines.append(f"- **Websites read:** {ok} of {total} ({ok * 100 // total}%)")
    lines.append(f"- **AI calls this run:** {report.get('ai_calls', 0)}" + (f" · ⚠️ stopped: {report['ai_stopped']}" if report.get("ai_stopped") else ""))
    if CACHE.exists():
        try:
            usage = json.loads(CACHE.read_text()).get("_usage", {})
            month, used = sorted(usage.items())[-1]
            lines.append(f"- **AI calls this month ({month}):** {used}")
        except Exception:  # noqa: BLE001
            pass

    # Biggest losses: unreadable sites ranked by how many Edmonton locations they cover
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
    manual_path = ROOT / "data" / "manual_deals.json"
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
        lines.append("\nIf they match, delete the manual entry from `data/manual_deals.json` so the deal isn't listed twice.")

    out = "\n".join(lines) + "\n"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(out)
    print(out)


if __name__ == "__main__":
    main()
