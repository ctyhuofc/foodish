"""Step 3 (daily, ~3:45 pm Edmonton / 2:45 pm Vancouver / 4:45 or 5:45 pm Toronto): send each alert subscriber a push for today's
deals near them. Deals from every city are considered; "near" is the subscriber's own radius.
Only deals still on or still to come today (on that city's clock) count: one that already ended is left out.

Needs: API_URL, ADMIN_TOKEN, VAPID_PRIVATE_KEY, VAPID_SUBJECT (e.g. mailto:you@foodish.ca)

One bad sign-up can't stop the others: each subscriber is tried on its own, with a 10-second limit, and the
whole send stops after 10 minutes. Endpoints are personal data and the Actions log is public, so the log only
ever shows counts and error types.
"""
import base64
import binascii
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives.asymmetric import ec  # comes with pywebpush
from pywebpush import WebPushException, webpush

import cities
import deal_state as ds  # the shared deal-timing rules (spec/deal-states.json)

ROOT = Path(__file__).resolve().parent.parent


def _api_url():
    """The alerts service address: the API_URL variable if set, else the one the app uses (site/config.js)."""
    if os.environ.get("API_URL"):
        return os.environ["API_URL"]
    m = re.search(r'API_URL:\s*"([^"]*)"', (ROOT / "site" / "config.js").read_text())
    return m.group(1) if m else ""


API = _api_url().rstrip("/")
AUTH = {"Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', '')}"}
SEND_TIMEOUT = 10        # seconds per push service call
BUDGET = 10 * 60         # seconds for all the sends together, well inside the workflow's time limit

# The push services real browsers use; same list as worker/worker.js (tests/test_notify.py checks they match).
PUSH_HOSTS = ["fcm.googleapis.com"]
PUSH_HOST_ENDINGS = [".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com"]


def _key_bytes(s):
    """A browser key (base64url or base64, padding optional) as bytes, or None if it isn't one."""
    if not isinstance(s, str) or len(s) > 200 or not re.fullmatch(r"[A-Za-z0-9_+/-]+={0,2}", s):
        return None
    t = s.rstrip("=").replace("-", "+").replace("_", "/")
    try:
        return base64.b64decode(t + "=" * (-len(t) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None


def good_subscription(sub):
    """Is this a real browser push sign-up (a known push service, proper keys)? Same rules as the worker."""
    if not isinstance(sub, dict):
        return False
    e = sub.get("endpoint")
    if not isinstance(e, str) or len(e) > 1000 or not re.fullmatch(r"[\x21-\x7e]+", e):
        return False
    try:
        u = urlsplit(e)
        host, port = u.hostname or "", u.port
    except ValueError:
        return False
    if u.scheme != "https" or u.username is not None or u.password is not None or port is not None:
        return False
    if host not in PUSH_HOSTS and not any(host.endswith(end) for end in PUSH_HOST_ENDINGS):
        return False
    if not e.startswith(f"https://{host}/"):
        return False
    keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else {}
    p256dh, auth = _key_bytes(keys.get("p256dh")), _key_bytes(keys.get("auth"))
    if not (p256dh and len(p256dh) == 65 and p256dh[0] == 4 and auth and len(auth) == 16):
        return False
    try:  # the browser's key must be a real point on the P-256 curve, or the alert can't be encrypted for it
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
        return True
    except ValueError:
        return False


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _unsubscribe(endpoint):
    """Ask the worker to forget a sign-up. A failure here never stops the run."""
    try:
        requests.post(f"{API}/unsubscribe", json={"endpoint": endpoint}, timeout=15)
    except Exception:  # noqa: BLE001
        pass


def fetch_subscriptions():
    """The stored sign-ups, or stop with a one-line reason (never the token or the reply's headers)."""
    try:
        r = requests.get(f"{API}/subscriptions", headers=AUTH, timeout=30)
        r.raise_for_status()
        subs = r.json()
    except requests.HTTPError as e:
        sys.exit(f"Couldn't read alert sign-ups: the alerts service said {e.response.status_code} (check ADMIN_TOKEN and API_URL).")
    except (requests.RequestException, ValueError) as e:
        sys.exit(f"Couldn't read alert sign-ups: {type(e).__name__}.")
    if not isinstance(subs, list):
        sys.exit("Couldn't read alert sign-ups: the alerts service didn't send a list.")
    return subs


def km(a_lat, a_lng, b_lat, b_lng):
    r = math.radians
    h = math.sin(r(b_lat - a_lat) / 2) ** 2 + math.cos(r(a_lat)) * math.cos(r(b_lat)) * math.sin(r(b_lng - a_lng) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def still_to_come(deals, now):
    """The deals that are on now, start later today, or run all day, at the city's moment `now`."""
    return [d for d in deals if ds.deal_state(d, now) in (ds.ON_NOW, ds.LATER, ds.ALL_DAY)]


def alert_for(s, todays, by_id, today):
    """The push message for one subscriber (JSON text), or None if no deal today is near them."""
    cats = set(s.get("categories") or [])
    near = []
    for d in todays:
        if cats and not cats & set(d.get("categories") or []):
            continue
        # the closest of this deal's places that we actually know, with its name
        places = [(km(s["lat"], s["lng"], by_id[i]["lat"], by_id[i]["lng"]), by_id[i]["name"])
                  for i in d.get("restaurant_ids") or [] if i in by_id]
        if places:
            dist, name = min(places, key=lambda x: x[0])
            if dist <= s.get("radius_km", 5):
                near.append((dist, d, name))
    if not near:
        return None
    near.sort(key=lambda x: x[0])
    top = near[0]
    body = f"{top[1]['title']} at {top[2]}" + (f" + {len(near) - 1} more near you" if len(near) > 1 else "")
    return json.dumps({"title": f"{len(near)} deal{'s' if len(near) > 1 else ''} near you today", "body": body,
                       "url": "https://www.foodish.ca/", "tag": f"daily-{today}"})


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
        now = ds.now_in(city.timezone)  # the time on that city's own clock
        # deals still on or still to come today, within their valid dates (same rules as the app)
        todays += still_to_come(data["deals"], now)
    today = ds.now_in().date

    subs = fetch_subscriptions()
    sent = gone = skipped = 0
    failed = {}  # error type -> how many
    started, left = time.monotonic(), 0
    session = requests.Session()
    session.max_redirects = 0  # push services answer directly; never follow a redirect somewhere else
    for n, s in enumerate(subs):
        if time.monotonic() - started > BUDGET:
            left = len(subs) - n
            break
        if not isinstance(s, dict) or not good_subscription(s.get("subscription")):
            skipped += 1  # junk sign-up (e.g. stored before the worker checked them): clean it up
            if isinstance(s, dict) and isinstance(s.get("subscription"), dict) and isinstance(s["subscription"].get("endpoint"), str):
                _unsubscribe(s["subscription"]["endpoint"])
            continue
        if not (_number(s.get("lat")) and _number(s.get("lng")) and _number(s.get("radius_km", 5))):
            skipped += 1
            continue
        try:
            payload = alert_for(s, todays, by_id, today)
            if payload is None:
                continue
            webpush(s["subscription"], payload, vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                    vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:yates@foodish.ca")}, ttl=6 * 3600,
                    timeout=SEND_TIMEOUT, requests_session=session)
            sent += 1
        except WebPushException as e:
            if e.response is not None and e.response.status_code in (404, 410):  # subscriber uninstalled / revoked
                _unsubscribe(s["subscription"]["endpoint"])
                gone += 1
            else:
                failed["WebPushException"] = failed.get("WebPushException", 0) + 1
        except Exception as e:  # noqa: BLE001  one subscriber's problem never stops the others
            failed[type(e).__name__] = failed.get(type(e).__name__, 0) + 1
    nfail = sum(failed.values())
    why = " (" + ", ".join(f"{k} {v}" for k, v in sorted(failed.items())) + ")" if failed else ""
    print(f"{len(subs)} subscribers · {sent} alerts sent · {gone} expired removed · {skipped} skipped (bad sign-up)"
          f" · {nfail} failed{why}" + (f" · {left} not tried (out of time)" if left else ""))
    # Red only when nothing at all got through although there were alerts to send: one bad sign-up
    # mustn't turn the daily run red, but a broken key or a push outage should.
    if sent == 0 and (nfail or left):
        sys.exit("No alerts could be sent today.")


if __name__ == "__main__":
    main()
