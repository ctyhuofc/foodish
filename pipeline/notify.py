"""Step 3 (daily, ~3:45 pm Edmonton / 2:45 pm Vancouver / 4:45 or 5:45 pm Toronto): send each alert subscriber a push for today's
deals near them. Deals from every city are considered; "near" is the subscriber's own radius.

Needs: API_URL, ADMIN_TOKEN, VAPID_PRIVATE_KEY, VAPID_SUBJECT (e.g. mailto:you@foodish.ca)
"""
import json
import math
import os
from pathlib import Path

import requests
from pywebpush import WebPushException, webpush

import cities
import deal_state as ds  # the shared deal-timing rules (spec/deal-states.json)

ROOT = Path(__file__).resolve().parent.parent
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
    by_id, todays = {}, []
    for city in cities.all_cities():
        if not city.deals.exists():
            continue
        data = json.loads(city.deals.read_text())
        by_id.update({r["id"]: r for r in data["restaurants"]})
        now = ds.now_in(city.timezone)  # "today" on that city's own clock
        # deals that run today and are within their valid dates (same rules as the app)
        todays += [d for d in data["deals"] if ds.deal_state(d, ds.Moment(now.date, now.day)) == ds.SCHEDULED]
    today = ds.now_in().date

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
