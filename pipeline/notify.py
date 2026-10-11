"""Step 3 (daily, ~3:45 pm Edmonton): send each alert subscriber a push for today's deals near them.

Needs: API_URL, ADMIN_TOKEN, VAPID_PRIVATE_KEY, VAPID_SUBJECT (e.g. mailto:you@foodish.ca)
"""
import datetime as dt
import json
import math
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from pywebpush import WebPushException, webpush

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("America/Edmonton")
API = (os.environ.get("API_URL") or "").rstrip("/")
AUTH = {"Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', '')}"}


def km(a_lat, a_lng, b_lat, b_lng):
    r = math.radians
    h = math.sin(r(b_lat - a_lat) / 2) ** 2 + math.cos(r(a_lat)) * math.cos(r(b_lat)) * math.sin(r(b_lng - a_lng) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def main():
    if not API or not os.environ.get("VAPID_PRIVATE_KEY"):
        print("Push alerts aren't set up yet (README step 6) — skipping.")
        return
    data = json.loads((ROOT / "data" / "deals.json").read_text())
    by_id = {r["id"]: r for r in data["restaurants"]}
    now = dt.datetime.now(TZ)
    weekday = (now.weekday() + 1) % 7  # Python Mon=0 -> app Sun=0
    today = now.date().isoformat()
    todays = [d for d in data["deals"] if (not d.get("days") or weekday in d["days"]) and not (d.get("valid_until") and d["valid_until"] < today)]

    subs = requests.get(f"{API}/subscriptions", headers=AUTH, timeout=30).json()
    sent = gone = 0
    for s in subs:
        cats = set(s.get("categories") or [])
        near = []
        for d in todays:
            if cats and not cats & set(d.get("categories") or []):
                continue
            dists = [km(s["lat"], s["lng"], by_id[i]["lat"], by_id[i]["lng"]) for i in d["restaurant_ids"] if i in by_id]
            if dists and min(dists) <= s.get("radius_km", 5):
                near.append((min(dists), d, by_id[d["restaurant_ids"][0]]["name"]))
        if not near:
            continue
        near.sort(key=lambda x: x[0])
        top = near[0]
        body = f"{top[1]['title']} at {top[2]}" + (f" + {len(near) - 1} more near you" if len(near) > 1 else "")
        payload = json.dumps({"title": f"{len(near)} deal{'s' if len(near) > 1 else ''} near you today", "body": body,
                              "url": "https://www.foodish.ca/", "tag": f"daily-{today}"})
        try:
            webpush(s["subscription"], payload, vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                    vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:hello@foodish.ca")}, ttl=6 * 3600)
            sent += 1
        except WebPushException as e:
            if e.response is not None and e.response.status_code in (404, 410):  # subscriber uninstalled / revoked
                requests.post(f"{API}/unsubscribe", json={"endpoint": s["subscription"]["endpoint"]}, timeout=15)
                gone += 1
    print(f"{len(subs)} subscribers · {sent} alerts sent · {gone} expired removed")


if __name__ == "__main__":
    main()
