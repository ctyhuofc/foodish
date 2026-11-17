"""Limits that stop one website from stalling the run or running up the AI bill (pipeline/scrape.py).
No network, no browser, no AI: responses, the clock and the AI are fakes."""
import argparse
import gzip
import io
import json
import os
import shutil
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import cities  # noqa: E402
import llm  # noqa: E402
import scrape  # noqa: E402
import site_state as ss  # noqa: E402


def response(body=b"", status=200, ctype="text/html", headers=None, raw=None):
    """A real requests.Response, read from memory instead of the network."""
    r = requests.Response()
    r.status_code, r.url = status, "https://wingplace.ca/"
    r.headers.update({"content-type": ctype, **(headers or {})})
    r.raw = raw if raw is not None else io.BytesIO(body)
    return r


def opener_for(make):
    return lambda url, **kw: make()


class Drip(io.RawIOBase):
    """A server that sends a little at a time, forever. Each piece moves the fake clock on 5 seconds."""
    def __init__(self, clock):
        self.clock = clock

    def readable(self):
        return True

    def read(self, n=-1):
        self.clock[0] += 5
        return b"<p>" * 10


class Download(unittest.TestCase):
    def test_a_normal_page_reads_the_same_as_before(self):
        body = "<html><head><meta charset='windows-1252'></head><body>Café 59¢ wings</body></html>".encode("cp1252")
        with mock.patch.object(scrape, "bounded", lambda fn, *a, **kw: fn(*a, opener=opener_for(lambda: response(body, ctype="text/html")), **kw)), \
             mock.patch.object(scrape, "allowed", lambda url: True):
            got = scrape.get("https://wingplace.ca/")
        # what the old code did: requests reads it all, then r.text with the <meta> charset
        old = response(body, ctype="text/html")
        old.encoding = "windows-1252"
        self.assertEqual(got, old.text)
        self.assertIn("Café 59¢", got)

    def test_header_charset_and_guessing_match_requests(self):
        for ctype, body in [("text/html; charset=utf-8", "Crêpe".encode()), ("text/html", "<p>plain ascii</p>".encode())]:
            r, data = scrape.download("https://x.ca/", 1000, opener=opener_for(lambda: response(body, ctype=ctype)))
            self.assertEqual(data, body)
        old = response("Crêpe".encode(), ctype="text/html; charset=utf-8")
        self.assertEqual(scrape.as_text("Crêpe".encode(), requests.utils.get_encoding_from_headers(old.headers)), old.text)

    def test_an_oversize_page_is_refused(self):
        big = b"x" * (scrape.PAGE_BYTES + 1)
        with self.assertRaisesRegex(scrape.FetchStopped, "too large"):
            scrape.download("https://x.ca/", scrape.PAGE_BYTES, opener=opener_for(lambda: response(big)))
        # ...even when the server says so up front, without reading any of it
        said = response(b"", headers={"content-length": str(scrape.PAGE_BYTES * 10)})
        with self.assertRaisesRegex(scrape.FetchStopped, "too large"):
            scrape.download("https://x.ca/", scrape.PAGE_BYTES, opener=opener_for(lambda: said))

    def test_a_zip_bomb_is_stopped_after_the_limit_not_unzipped(self):
        import urllib3
        bomb = gzip.compress(b"\0" * 50_000_000)  # 50 MB of nothing in ~50 KB
        raw = urllib3.HTTPResponse(body=io.BytesIO(bomb), headers={"content-encoding": "gzip"}, preload_content=False)
        r = response(raw=raw, headers={"content-encoding": "gzip"})
        with self.assertRaisesRegex(scrape.FetchStopped, "too large"):
            scrape.download("https://x.ca/", 500_000, opener=opener_for(lambda: r))

    def test_a_slow_drip_is_cut_off_at_the_deadline(self):
        clock = [0.0]
        r = response(raw=Drip(clock))
        with self.assertRaisesRegex(scrape.FetchStopped, "too slow"):
            scrape.download("https://x.ca/", scrape.PAGE_BYTES, clock=lambda: clock[0], opener=opener_for(lambda: r))
        self.assertLessEqual(clock[0], scrape.FETCH_SECONDS + 5)

    def test_a_download_stuck_before_it_starts_is_given_up(self):
        stuck = threading.Event()
        with self.assertRaisesRegex(scrape.FetchStopped, "too slow"):
            scrape.bounded(stuck.wait, 60, seconds=0.05)
        stuck.set()

    def test_not_a_page_or_an_error_page_is_not_downloaded(self):
        for r, reason in [(response(b"", status=404), "HTTP 404"), (response(b"%PDF", ctype="application/pdf"), "not a web page (application/pdf)")]:
            scrape.REASONS.clear()
            body_read = mock.Mock(side_effect=AssertionError("body read"))
            r.iter_content = body_read
            with mock.patch.object(scrape, "bounded", lambda fn, *a, _r=r, **kw: fn(*a, opener=opener_for(lambda: _r), **kw)), \
                 mock.patch.object(scrape, "allowed", lambda url: True):
                self.assertIsNone(scrape.get("https://x.ca/"))
            self.assertEqual(scrape.REASONS["https://x.ca/"], reason)

    def test_reasons_are_recorded_for_the_report(self):
        for err, reason in [(scrape.FetchStopped("too slow"), "too slow"), (scrape.FetchStopped("too large"), "too large"),
                            (requests.exceptions.ConnectTimeout(), "timeout"), (requests.exceptions.SSLError(), "SSL error")]:
            def boom(*a, _e=err, **kw):
                raise _e
            with mock.patch.object(scrape, "bounded", boom), mock.patch.object(scrape, "allowed", lambda url: True):
                self.assertIsNone(scrape.get("https://slow.ca/"))
            self.assertEqual(scrape.REASONS["https://slow.ca/"], reason)

    def test_robots_txt_rules_and_a_robots_txt_we_cant_read_blocks_the_site_today(self):
        # RFC 9309: no robots.txt (4xx) allows everything; a server error, or one too large or too slow, allows nothing
        # today. (Before Nov 2026 those allowed everything.) The reason is not "blocked by robots.txt", so the
        # site's saved deals are kept as for any site we couldn't read today.
        cases = [(response(b"User-agent: FoodishBot\nDisallow: /", ctype="text/plain"), False, ss.READ_ROBOTS),
                 (response(b"User-agent: *\nDisallow: /", status=404, ctype="text/plain"), True, None),
                 (response(b"User-agent: FoodishBot\nDisallow: /" + b"#" * scrape.ROBOTS_BYTES, ctype="text/plain"), False,
                  scrape.ROBOTS_UNAVAILABLE),
                 (response(b"oops", status=503, ctype="text/plain"), False, scrape.ROBOTS_UNAVAILABLE)]
        for r, ok, why in cases:
            scrape._robots.clear()
            with mock.patch.object(scrape, "bounded", lambda fn, *a, _r=r, **kw: fn(*a, opener=opener_for(lambda: _r), **kw)):
                self.assertEqual(scrape.allowed("https://robots.example/menu"), ok)
                if why:
                    self.assertEqual(scrape.robots_reason("https://robots.example/menu"), why)
        for err, why in [(scrape.FetchStopped("too slow"), scrape.ROBOTS_UNAVAILABLE), (requests.exceptions.ReadTimeout(), scrape.ROBOTS_UNAVAILABLE),
                         (requests.exceptions.ConnectionError(), scrape.CANT_CONNECT)]:
            scrape._robots.clear()
            def boom(*a, _e=err, **kw):
                raise _e
            with mock.patch.object(scrape, "bounded", boom):
                self.assertFalse(scrape.allowed("https://robots.example/menu"))
                self.assertEqual(scrape.robots_reason("https://robots.example/menu"), why)
            self.assertNotIn(why, ss.BLOCKED_READS)  # unreachable today (deals kept), not "blocked" (deals cleared)
        scrape._robots.clear()

    def test_a_slow_site_stops_opening_more_pages(self):
        home = "<a href='/specials'>Specials</a><a href='/deals'>Deals</a><a href='/happy-hour'>Happy hour</a>" + "<p>Wing Wednesday $5 wings</p>" * 40
        t = [0.0]
        fetched = []
        def fake_get(url):
            fetched.append(url)
            t[0] += 50  # every page takes 50 s
            return home
        with mock.patch.object(scrape, "get", fake_get), mock.patch.object(scrape.time, "sleep"):
            info = {}
            scrape.read_site("https://wingplace.ca/", info, clock=lambda: t[0])
        self.assertEqual(len(fetched), 2)  # home + one more; then the site's 90 s are used up
        self.assertTrue(info["cut_short"])


class Browser(unittest.TestCase):
    def test_the_browser_gets_no_secrets(self):
        env = {"PATH": "/bin", "HOME": "/home/x", "LLM_API_KEY": "sk-secret", "GITHUB_TOKEN": "ghs", "ADMIN_TOKEN": "a",
               "PLAYWRIGHT_BROWSERS_PATH": "/pw", "VAPID_PRIVATE_KEY": "v"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(scrape.browser_env(), {"PATH": "/bin", "HOME": "/home/x", "PLAYWRIGHT_BROWSERS_PATH": "/pw"})

    def test_no_sandbox_falls_back_with_one_warning(self):
        tries = []
        def fake(url, sandbox):
            tries.append(sandbox)
            return (None, scrape.LAUNCH_FAILED) if sandbox else ("<html>ok</html>", 0)
        with mock.patch.object(scrape, "_render", fake), mock.patch.object(scrape, "allowed", lambda u: True), \
             mock.patch.dict(os.environ, {"RENDER_JS": "1"}), mock.patch.dict(scrape._sandbox, {"on": True}), \
             mock.patch("builtins.print") as said:
            self.assertEqual(scrape.render_js("https://a.ca/"), "<html>ok</html>")
            self.assertEqual(scrape.render_js("https://b.ca/"), "<html>ok</html>")
        self.assertEqual(tries, [True, False, False])  # sandbox tried once; the second site goes straight to no sandbox
        self.assertEqual(said.call_count, 1)
        self.assertIn("sandbox", said.call_args[0][0])

    def test_a_page_that_never_finishes_is_stopped(self):
        hang = [sys.executable, "-c", "import time; time.sleep(30)"]
        with mock.patch.object(scrape, "_render_cmd", lambda url, sandbox: hang), mock.patch.object(scrape, "RENDER_SECONDS", 0.5):
            self.assertEqual(scrape._render("https://a.ca/", True), (None, -1))

    def test_too_much_html_from_the_browser_is_refused(self):
        big = [sys.executable, "-c", f"import sys; sys.stdout.write('x' * {scrape.PAGE_BYTES + 1})"]
        ok = [sys.executable, "-c", "import sys; sys.stdout.write('<p>hi</p>')"]
        with mock.patch.object(scrape, "_render_cmd", lambda url, sandbox: big):
            self.assertEqual(scrape._render("https://a.ca/", True)[0], None)
        with mock.patch.object(scrape, "_render_cmd", lambda url, sandbox: ok):
            self.assertEqual(scrape._render("https://a.ca/", True), ("<p>hi</p>", 0))


TEXT = "### PAGE: https://wings{n}.example/\nWing Wednesday: 59¢ wings all day"


class AICalls(unittest.TestCase):
    """Every request sent to the AI counts toward the limits, and a page the AI can't read is paused."""
    def setUp(self):
        self.dir = ROOT / "data" / "_test_city"
        self.site = ROOT / "site" / "data" / "_test_city"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)
        (self.dir / "restaurants.json").write_text(json.dumps([
            {"id": f"osm-{n}", "name": f"Wings {n}", "lat": 53.5, "lng": -113.5, "address": f"{n} Main St",
             "website": f"https://wings{n}.example/"} for n in (1, 2)]))
        self.text = {f"https://wings{n}.example/": TEXT.format(n=n) for n in (1, 2)}
        base = cities.get("edmonton")
        self.city = cities.City(**{**base.__dict__, "id": "_test_city", "data_dir": "data/_test_city", "pages_dir": "_test_city/deals"})

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.site, ignore_errors=True)

    def run_once(self, ai, today="2026-11-16", max_calls="10"):
        asked = []
        def fake_ai(name, url, text, **kw):
            asked.append(url)
            return ai(url)
        with mock.patch.object(scrape, "read_site_once", lambda site, info: info.update(status=ss.READ_OK) or self.text[site]), \
             mock.patch.object(scrape, "extract_deals", fake_ai), mock.patch.object(scrape, "TODAY", today), \
             mock.patch.object(scrape, "used_elsewhere", lambda city, month: 0), \
             mock.patch.dict(os.environ, {"MAX_LLM_CALLS": max_calls, "MONTHLY_LLM_CALLS": "1000"}), \
             mock.patch("builtins.print"):
            scrape.run_city(self.city, argparse.Namespace(limit=0, workers=1, cities=[]))
        report = json.loads(self.city.report.read_text())
        cache = json.loads(self.city.cache.read_text())
        return asked, report, cache

    @staticmethod
    def unreadable(requests_sent=2):
        def ai(url):
            llm.requests_made += requests_sent  # asked twice, both replies unreadable
            raise llm.UnreadableReply("unreadable AI reply: 'Sorry, I can't'")
        return ai

    @staticmethod
    def works(url):
        llm.requests_made += 1
        return [{"title": "Wing night", "price": "59¢", "days": ["Wed"], "quote": "Wing Wednesday: 59¢ wings all day", "source_url": url}]

    def test_failed_requests_count_toward_the_run_and_month(self):
        asked, report, cache = self.run_once(self.unreadable(), max_calls="2")
        self.assertEqual(len(asked), 1)  # the first site used up both allowed requests by failing
        self.assertEqual(report["ai_calls"], 2)
        self.assertEqual(cache["_usage"]["2026-11"], 2)
        outcomes = sorted(s["outcome"] for s in report["sites"].values())
        self.assertEqual(outcomes, [ss.AI_ERROR, ss.DEFERRED_BUDGET])

    def test_a_provider_error_counts_too_but_is_retried_tomorrow(self):
        def down(url):
            llm.requests_made += 3
            e = llm.LLMError("HTTP 529 overloaded")
            e.status = 529
            raise e
        asked, report, cache = self.run_once(down)
        self.assertEqual(report["ai_calls"], 6)
        self.assertNotIn("ai_fail", cache.get("wings1.example", {}))
        asked, _, _ = self.run_once(self.works, today="2026-11-17")
        self.assertEqual(len(asked), 2)

    def test_an_unreadable_reply_pauses_that_site_then_retries(self):
        asked, _, cache = self.run_once(self.unreadable())
        self.assertEqual(len(asked), 2)
        self.assertEqual(cache["wings1.example"]["ai_fail"]["until"], "2026-11-19")
        asked, report, _ = self.run_once(self.works, today="2026-11-17")  # same text: not asked, not paid for
        self.assertEqual(asked, [])
        self.assertEqual({s["outcome"] for s in report["sites"].values()}, {ss.DEFERRED_AI_BACKOFF})
        self.assertEqual(report["ai_calls"], 0)
        asked, report, cache = self.run_once(self.works, today="2026-11-19")  # pause over
        self.assertEqual(len(asked), 2)
        self.assertEqual({s["outcome"] for s in report["sites"].values()}, {ss.EXTRACTED})
        self.assertNotIn("ai_fail", cache["wings1.example"])
        self.assertEqual(cache["_usage"]["2026-11"], 4 + 2)

    def test_changed_text_is_asked_again_at_once(self):
        self.run_once(self.unreadable())
        self.text["https://wings1.example/"] += "\nTaco Tuesday $3 tacos"
        asked, _, _ = self.run_once(self.works, today="2026-11-17")
        self.assertEqual(asked, ["https://wings1.example/"])


class LLMCounter(unittest.TestCase):
    def test_every_request_sent_is_counted(self):
        replies = iter(["cut off {", '{"deals": []}'])
        before = llm.requests_made
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}), \
             mock.patch.object(llm, "_call_anthropic", lambda p: next(replies)):
            llm.extract_deals("A", "https://a.ca", "text")
        self.assertEqual(llm.requests_made - before, 2)

    def test_unreadable_is_its_own_kind_of_error(self):
        with self.assertRaises(llm.UnreadableReply):
            llm.parse_reply("Sorry")
        self.assertTrue(issubclass(llm.UnreadableReply, llm.LLMError))


if __name__ == "__main__":
    unittest.main()
