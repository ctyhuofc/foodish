"""Daily alerts (pipeline/notify.py): only deals still on or still to come today count, and one bad
sign-up (junk keys, a stranger's server, a server that never answers) can't stop everyone else's alert."""
import base64
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import deal_state as ds  # noqa: E402

try:
    import notify  # noqa: E402  (needs pywebpush, installed by pipeline/requirements.txt)
    import requests  # noqa: E402
    from cryptography.hazmat.primitives import serialization  # noqa: E402
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
except ImportError:
    notify = None

TOKEN = "secret-admin-token-for-tests"


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


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def fake_keys():
    """Made-up browser keys of the real shape: a fresh P-256 public key (65 bytes) and 16 random bytes."""
    pub = ec.generate_private_key(ec.SECP256R1()).public_key()
    raw = pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"p256dh": b64url(raw), "auth": b64url(os.urandom(16))}


def sub(endpoint, lat=53.544, lng=-113.491, keys=None):
    return {"subscription": {"endpoint": endpoint, "keys": keys or fake_keys()}, "lat": lat, "lng": lng,
            "radius_km": 5, "categories": []}


FCM = "https://fcm.googleapis.com/fcm/send/"


@unittest.skipUnless(notify, "pywebpush isn't installed (pip install -r pipeline/requirements.txt)")
class SendLoop(unittest.TestCase):
    """main() with the network faked: the worker's /subscriptions reply, /unsubscribe calls and webpush()."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.deals_file = Path(tmp.name) / "deals.json"
        self.write_deals([{"id": "r1", "name": "Wing Shack", "lat": 53.545, "lng": -113.49}],
                         [{"id": "d1", "title": "Wing night", "restaurant_ids": ["r1"]}])
        city = SimpleNamespace(deals=self.deals_file, timezone="America/Edmonton")
        for p in (mock.patch.object(notify.cities, "all_cities", return_value=[city]),
                  mock.patch.object(notify, "still_to_come", side_effect=lambda deals, now: deals),
                  mock.patch.object(notify, "API", "https://api.example.test"),
                  mock.patch.object(notify, "AUTH", {"Authorization": f"Bearer {TOKEN}"}),
                  mock.patch.dict(os.environ, {"VAPID_PRIVATE_KEY": "fake-vapid-key"})):
            p.start()
            self.addCleanup(p.stop)

    def write_deals(self, restaurants, deals):
        self.deals_file.write_text(json.dumps({"restaurants": restaurants, "deals": deals}))

    def run_main(self, reply, webpush=None):
        """Runs main(); returns (printed text, the fake webpush, the fake requests.post)."""
        r = mock.Mock(status_code=200)
        r.json.return_value = reply
        r.raise_for_status.return_value = None
        push = webpush or mock.Mock()
        out = io.StringIO()
        with mock.patch.object(notify.requests, "get", return_value=r), \
                mock.patch.object(notify.requests, "post") as post, \
                mock.patch.object(notify, "webpush", push), redirect_stdout(out):
            notify.main()
        return out.getvalue(), push, post

    def fail_for(self, bad_endpoint, exc):
        """A fake webpush that raises `exc` for one subscriber and works for the rest."""
        def push(info, *a, **kw):
            if info["endpoint"] == bad_endpoint:
                raise exc
        return mock.Mock(side_effect=push)

    def test_junk_keys_mid_list_dont_stop_the_others(self):
        subs = [sub(FCM + "a"), sub(FCM + "b"), sub(FCM + "c")]
        out, push, _ = self.run_main(subs, self.fail_for(FCM + "b", ValueError("Could not deserialize key data")))
        self.assertEqual(push.call_count, 3)
        self.assertIn("2 alerts sent", out)
        self.assertIn("1 failed (ValueError 1)", out)
        self.assertNotIn("fcm.googleapis.com", out)  # endpoints are personal data: never in the log

    def test_network_errors_dont_stop_the_others(self):
        for exc in (requests.ConnectionError("refused"), requests.Timeout("slow")):
            with self.subTest(type(exc).__name__):
                subs = [sub(FCM + "a"), sub(FCM + "b"), sub(FCM + "c")]
                out, _, _ = self.run_main(subs, self.fail_for(FCM + "a", exc))
                self.assertIn("2 alerts sent", out)
                self.assertIn(f"({type(exc).__name__} 1)", out)

    def test_timeout_is_passed_to_webpush(self):
        _, push, _ = self.run_main([sub(FCM + "a")])
        self.assertEqual(push.call_args.kwargs["timeout"], 10)

    def test_stored_junk_is_skipped_and_removed(self):
        bad = [sub("https://attacker.example/x"), sub("https://fcm.googleapis.com.evil.ca/x"),
               sub("http://fcm.googleapis.com/x"), sub(FCM + "short-keys", keys={"p256dh": "AAAA", "auth": "AAAA"})]
        out, push, post = self.run_main(bad + [sub(FCM + "ok")])
        self.assertEqual([c.args[0]["endpoint"] for c in push.call_args_list], [FCM + "ok"])
        removed = [c.kwargs["json"]["endpoint"] for c in post.call_args_list]
        self.assertEqual(removed, [b["subscription"]["endpoint"] for b in bad])
        self.assertIn("4 skipped", out)

    def test_failed_cleanup_doesnt_stop_the_run(self):
        r = mock.Mock(status_code=200)
        r.json.return_value = [sub("https://attacker.example/x"), sub(FCM + "ok")]
        with mock.patch.object(notify.requests, "get", return_value=r), \
                mock.patch.object(notify.requests, "post", side_effect=requests.ConnectionError("down")), \
                mock.patch.object(notify, "webpush") as push, redirect_stdout(io.StringIO()):
            notify.main()
        self.assertEqual(push.call_count, 1)

    def test_bad_location_is_skipped(self):
        out, push, _ = self.run_main([sub(FCM + "a", lat="53.5"), sub(FCM + "b", lat=None), sub(FCM + "c")])
        self.assertEqual(push.call_count, 1)
        self.assertIn("2 skipped", out)

    def test_expired_sign_ups_are_still_removed(self):
        gone = notify.WebPushException("Push failed: 410", response=mock.Mock(status_code=410))
        out, _, post = self.run_main([sub(FCM + "a"), sub(FCM + "b")], self.fail_for(FCM + "a", gone))
        self.assertEqual([c.kwargs["json"]["endpoint"] for c in post.call_args_list], [FCM + "a"])
        self.assertIn("1 alerts sent · 1 expired removed", out)

    def test_first_restaurant_missing(self):
        """A deal whose first place isn't in the list uses the nearest place that is."""
        self.write_deals([{"id": "r2", "name": "Taco Spot", "lat": 53.545, "lng": -113.49}],
                         [{"id": "d1", "title": "Taco Tuesday", "restaurant_ids": ["gone", "r2"]}])
        _, push, _ = self.run_main([sub(FCM + "a")])
        self.assertEqual(json.loads(push.call_args.args[1])["body"], "Taco Tuesday at Taco Spot")

    def test_reply_that_isnt_a_list_stops_with_one_line(self):
        with self.assertRaises(SystemExit) as e:
            self.run_main({"error": "not found"})
        self.assertIn("didn't send a list", str(e.exception.code))
        self.assertNotIn(TOKEN, str(e.exception.code))

    def test_refused_reply_stops_with_one_line(self):
        r = mock.Mock(status_code=401, headers={"Authorization": f"Bearer {TOKEN}"})
        r.raise_for_status.side_effect = requests.HTTPError("401 Client Error", response=r)
        with mock.patch.object(notify.requests, "get", return_value=r), self.assertRaises(SystemExit) as e:
            notify.main()
        self.assertIn("401", str(e.exception.code))
        self.assertNotIn(TOKEN, str(e.exception.code))

    def test_run_is_red_only_when_nothing_could_be_sent(self):
        with self.assertRaises(SystemExit):
            self.run_main([sub(FCM + "a")], mock.Mock(side_effect=requests.Timeout("slow")))
        self.run_main([sub(FCM + "a"), sub(FCM + "b")], self.fail_for(FCM + "a", requests.Timeout("slow")))  # no exit
        self.run_main([sub("https://attacker.example/x")])  # only junk: nothing to send, not red

    def test_stops_when_out_of_time(self):
        with mock.patch.object(notify, "BUDGET", -1), self.assertRaises(SystemExit):
            self.run_main([sub(FCM + "a"), sub(FCM + "b")])


@unittest.skipUnless(notify, "pywebpush isn't installed (pip install -r pipeline/requirements.txt)")
class GoodSubscription(unittest.TestCase):
    def test_real_browser_shapes(self):
        for e in (FCM + "abc:APA91b", "https://updates.push.services.mozilla.com/wpush/v2/gAAAA",
                  "https://wns2-par02p.notify.windows.com/w/?token=BQYAAAB", "https://web.push.apple.com/QGuQyavXut"):
            with self.subTest(e):
                self.assertTrue(notify.good_subscription({"endpoint": e, "keys": fake_keys()}))

    def test_padded_and_plain_base64_keys(self):
        k = fake_keys()
        std = {n: base64.b64encode(unb64(v)).decode() for n, v in k.items()}
        self.assertTrue(notify.good_subscription({"endpoint": FCM + "a", "keys": std}))

    def test_rejected(self):
        k = fake_keys()
        for e in ("http://fcm.googleapis.com/x", "https://fcm.googleapis.com.evil.ca/x", "https://evilfcm.googleapis.com/x",
                  "https://a@fcm.googleapis.com/x", "https://fcm.googleapis.com:8443/x", "https://fcm.googleapis.com\\@evil.ca/x",
                  "https://push.apple.com.evil.ca/x", "https://10.0.0.1/x", "https://fcm.googleapis.com/" + "a" * 1000):
            with self.subTest(e):
                self.assertFalse(notify.good_subscription({"endpoint": e, "keys": k}))
        off_curve = bytearray(unb64(k["p256dh"]))
        off_curve[40] ^= 1
        for keys in ({"p256dh": k["p256dh"]}, {"p256dh": k["p256dh"], "auth": 5}, {"p256dh": "AAAA", "auth": k["auth"]},
                     {"p256dh": k["p256dh"], "auth": b64url(os.urandom(17))}, {"p256dh": b64url(bytes(off_curve)), "auth": k["auth"]}):
            with self.subTest(keys):
                self.assertFalse(notify.good_subscription({"endpoint": FCM + "a", "keys": keys}))

    def test_same_push_services_as_the_worker(self):
        js = (ROOT / "worker" / "worker.js").read_text()
        for name in ("PUSH_HOSTS", "PUSH_HOST_ENDINGS"):
            m = re.search(rf"const {name} = (\[[^\]]*\]);", js)
            self.assertEqual(json.loads(m.group(1)), getattr(notify, name), f"{name} differs in worker/worker.js")


if __name__ == "__main__":
    unittest.main()
