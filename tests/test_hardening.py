"""Security checks on what the robot reads and writes (pipeline/scrape.py, pages.py, llm.py).
No network, no browser, no AI: addresses, responses and lookups are fakes."""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import cities  # noqa: E402
import llm  # noqa: E402
import pages  # noqa: E402
import scrape  # noqa: E402
import site_state as ss  # noqa: E402


def response(url, body=b"", status=200, ctype="text/html", location=None):
    r = requests.Response()
    r.status_code, r.url = status, url
    r.headers.update({"content-type": ctype, **({"location": location} if location else {})})
    r.raw = io.BytesIO(body)
    return r


def web(pages_by_url):
    """A fake internet: url -> (status, location or body). Records every url opened."""
    opened = []

    def opener(url, **kw):
        assert kw.get("allow_redirects") is False  # redirects are followed by download(), one checked step at a time
        opened.append(url)
        status, x = pages_by_url[url]
        return response(url, status=status, location=x) if 300 <= status < 400 else response(url, x, status)
    return opener, opened


PUBLIC = lambda host: {"93.184.216.34"}  # noqa: E731  every name is on the public internet


class Addresses(unittest.TestCase):
    def test_private_and_local_addresses_are_never_opened(self):
        opener, opened = web({})
        for url in ["http://127.0.0.1/", "http://10.1.2.3/menu", "http://169.254.169.254/latest/meta-data/",
                    "http://[::1]/", "http://[::ffff:10.0.0.1]/", "http://192.168.0.1:8080/", "http://localhost/",
                    "https://printer.local/", "ftp://pizza.ca/", "file:///etc/passwd", "https://www.timhortons.com@evil.ca/"]:
            with self.assertRaisesRegex(scrape.FetchStopped, scrape.ADDRESS_NOT_ALLOWED, msg=url):
                scrape.download(url, 1000, opener=opener, lookup=PUBLIC)
        self.assertEqual(opened, [])

    def test_a_name_pointing_at_a_private_address_is_refused(self):
        opener, opened = web({"https://sneaky.example/": (200, b"<p>hi</p>")})
        for addrs in ({"10.0.0.5"}, {"93.184.216.34", "10.0.0.5"}, {"fd00::1"}):
            with self.assertRaisesRegex(scrape.FetchStopped, scrape.ADDRESS_NOT_ALLOWED):
                scrape.download("https://sneaky.example/", 1000, opener=opener, lookup=lambda h, _a=addrs: _a)
        self.assertEqual(opened, [])

    def test_a_name_that_does_not_exist_is_cant_connect_as_before(self):
        def nxdomain(host):
            raise OSError("no such name")
        with mock.patch.object(scrape, "bounded", lambda fn, *a, **kw: fn(*a, opener=web({})[0], lookup=nxdomain, **kw)), \
             mock.patch.object(scrape, "allowed", lambda url: True):
            self.assertIsNone(scrape.get("https://gone.example/"))
        self.assertEqual(scrape.REASONS["https://gone.example/"], scrape.CANT_CONNECT)

    def test_the_report_says_address_not_allowed(self):
        with mock.patch.object(scrape, "bounded", lambda fn, *a, **kw: fn(*a, opener=web({})[0], lookup=lambda h: {"10.9.9.9"}, **kw)), \
             mock.patch.object(scrape, "allowed", lambda url: True):
            self.assertIsNone(scrape.get("https://inside.example/"))
        self.assertEqual(scrape.REASONS["https://inside.example/"], "address not allowed")
        self.assertNotIn(scrape.ADDRESS_NOT_ALLOWED, ss.BLOCKED_READS)  # unreadable today: saved deals kept

    def test_a_redirect_to_the_cloud_secrets_address_is_refused(self):
        opener, opened = web({"https://pizza.example/": (302, "http://169.254.169.254/latest/meta-data/")})
        with self.assertRaisesRegex(scrape.FetchStopped, scrape.ADDRESS_NOT_ALLOWED):
            scrape.download("https://pizza.example/", 1000, opener=opener, lookup=PUBLIC)
        self.assertEqual(opened, ["https://pizza.example/"])

    def test_a_redirect_to_a_name_on_a_private_network_is_refused(self):
        opener, opened = web({"https://pizza.example/": (301, "https://intranet.example/")})
        lookup = lambda h: {"10.0.0.1"} if h == "intranet.example" else {"93.184.216.34"}  # noqa: E731
        with self.assertRaisesRegex(scrape.FetchStopped, scrape.ADDRESS_NOT_ALLOWED):
            scrape.download("https://pizza.example/", 1000, opener=opener, lookup=lookup)
        self.assertEqual(opened, ["https://pizza.example/"])

    def test_more_than_five_redirects_are_not_followed(self):
        hops = {f"https://loop.example/{i}": (302, f"/{i + 1}") for i in range(10)}
        opener, opened = web(hops)
        with self.assertRaisesRegex(scrape.FetchStopped, scrape.TOO_MANY_REDIRECTS):
            scrape.download("https://loop.example/0", 1000, opener=opener, lookup=PUBLIC)
        self.assertEqual(len(opened), scrape.MAX_REDIRECTS + 1)
        five = {**{f"https://loop.example/{i}": (302, f"/{i + 1}") for i in range(5)}, "https://loop.example/5": (200, b"ok")}
        r, body = scrape.download("https://loop.example/0", 1000, opener=web(five)[0], lookup=PUBLIC)
        self.assertEqual(body, b"ok")

    def test_normal_redirects_still_work(self):
        opener, opened = web({"http://pizza.example/": (301, "https://pizza.example/"),
                              "https://pizza.example/": (301, "https://www.pizza.example/"),
                              "https://www.pizza.example/": (200, b"<p>Wing Wednesday $5</p>")})
        asked = []
        robots = lambda url: asked.append(url)  # noqa: E731  None: allowed
        r, body = scrape.download("http://pizza.example/", 1000, opener=opener, lookup=PUBLIC, robots=robots)
        self.assertEqual(body, b"<p>Wing Wednesday $5</p>")
        self.assertEqual(opened, ["http://pizza.example/", "https://pizza.example/", "https://www.pizza.example/"])
        self.assertEqual(asked, ["https://pizza.example/", "https://www.pizza.example/"])  # each new address's robots.txt

    def test_a_redirect_to_a_page_robots_txt_forbids_is_not_read(self):
        opener, opened = web({"https://pizza.example/": (302, "https://other.example/menu"),
                              "https://other.example/menu": (200, b"secret")})
        with mock.patch.object(scrape, "bounded", lambda fn, *a, **kw: fn(*a, opener=opener, lookup=PUBLIC, **kw)), \
             mock.patch.object(scrape, "allowed", lambda url: "other.example" not in url), \
             mock.patch.object(scrape, "robots_reason", lambda url: ss.READ_ROBOTS):
            self.assertIsNone(scrape.get("https://pizza.example/"))
        self.assertEqual(scrape.REASONS["https://pizza.example/"], ss.READ_ROBOTS)
        self.assertEqual(opened, ["https://pizza.example/"])

    def test_robots_txt_is_fetched_with_the_same_checks(self):
        scrape._robots.clear()
        opener, opened = web({"https://pizza.example/robots.txt": (302, "http://127.0.0.1/robots.txt")})
        with mock.patch.object(scrape, "bounded", lambda fn, *a, **kw: fn(*a, opener=opener, lookup=PUBLIC, **kw)):
            self.assertFalse(scrape.allowed("https://pizza.example/menu"))
            self.assertEqual(scrape.robots_reason("https://pizza.example/menu"), scrape.ADDRESS_NOT_ALLOWED)
        scrape._robots.clear()

    def test_the_browser_only_loads_public_web_addresses(self):
        for url in ["https://pizza.example/a.js", "http://cdn.example/x.png", "https://93.184.216.34/x"]:
            self.assertTrue(scrape.browser_may_load(url), url)
        for url in ["http://127.0.0.1:9222/json", "http://169.254.169.254/", "http://10.0.0.1/", "http://[::1]/",
                    "http://localhost:3000/", "http://router.local/", "file:///etc/passwd", "chrome://settings", "ws://10.0.0.1/"]:
            self.assertFalse(scrape.browser_may_load(url), url)


class UnreadableToday(unittest.TestCase):
    """A site whose robots.txt can't be read today (or whose address is refused) keeps its saved deals for
    STALE_DAYS, like any site that is down: it is unreachable, not blocked."""
    def test_site_state_keeps_then_clears(self):
        for status in (scrape.ROBOTS_UNAVAILABLE, scrape.ADDRESS_NOT_ALLOWED):
            blocked = status in ss.BLOCKED_READS
            kw = dict(readable=False, fingerprint=None, has_deal_words=False, prompt_version=1, stale_before="2026-11-02",
                      budget_left=True, ai_stopped=False, blocked=blocked)
            self.assertEqual(ss.next_step(entry={"ok_at": "2026-11-15"}, **kw), ss.Step(ss.UNREACHABLE, ss.KEEP))
            self.assertEqual(ss.next_step(entry={"ok_at": "2026-10-20"}, **kw), ss.Step(ss.GONE, ss.CLEAR))


class RemovalRequests(unittest.TestCase):
    def test_the_same_site_written_differently_still_matches(self):
        for url in ["https://x.ca", "https://x.ca:443/menu", "https://X.ca./", "https://user@x.ca/", "http://www.x.ca:80", "x.ca"]:
            self.assertEqual(scrape.site_host(url), "x.ca", url)
        self.assertEqual(scrape.domain("https://x.ca:443/"), "x.ca:443")  # the cache key is left as it was

    def test_removed_json_entries_are_normalised(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "removed.json").write_text(json.dumps({"sites": ["WWW.X.ca", "https://y.ca:443/", "z.ca.", "", 5]}))
        with mock.patch.object(scrape, "REMOVED", tmp / "removed.json"):
            self.assertEqual(scrape.removed_sites(), {"x.ca", "y.ca", "z.ca"})
        shutil.rmtree(tmp)

    def test_a_removed_site_with_a_port_or_login_is_not_read(self):
        tmp = Path(tempfile.mkdtemp())
        city = cities.City(**{**cities.get("edmonton").__dict__, "id": "_test_removed", "data_dir": str(tmp)})
        sites = ["https://x.ca:443/", "https://user@X.ca./", "https://www.x.ca"]
        (tmp / "restaurants.json").write_text(json.dumps([
            {"id": f"osm-{n}", "name": f"Place {n}", "lat": 53.5, "lng": -113.5, "address": "", "website": w} for n, w in enumerate(sites)]))
        read = []
        with mock.patch.object(scrape, "removed_sites", lambda: {"x.ca"}), \
             mock.patch.object(scrape, "read_site_once", lambda site, info: read.append(site)), \
             mock.patch.object(cities.City, "site_copy", property(lambda self: tmp / "copy.json")), \
             mock.patch.object(scrape, "used_elsewhere", lambda city, month: 0), mock.patch("builtins.print"):
            scrape.run_city(city, argparse.Namespace(limit=0, workers=1, cities=[]))
        report = json.loads(city.report.read_text())
        self.assertEqual(read, [])
        self.assertEqual({s["status"] for s in report["sites"].values()}, {ss.READ_REMOVED})
        shutil.rmtree(tmp)


PAGE = "Wing Wednesday: 59¢ wings all day"


def ai(**kw):
    return {"title": "Wing night", "description": "Dine in only.", "price": "59¢", "days": ["Wed"],
            "quote": "Wing Wednesday: 59¢ wings all day", **kw}


class AIDeals(unittest.TestCase):
    def clean(self, **kw):
        return scrape.clean_deal(ai(**kw), "https://wingplace.ca", PAGE)

    def test_only_known_deal_types_are_kept(self):
        d = self.clean(categories=["Wings", "monday", "index", "", None, 5, {"x": 1}, "happy hour", "wings", "drinks", "kids", "pizza"])
        self.assertEqual(d["categories"], ["wings", "happy hour", "drinks", "kids"])
        self.assertEqual(self.clean(categories="wings")["categories"], ["wings"])
        self.assertEqual(self.clean(categories=None)["categories"], [])

    def test_the_prompt_lists_exactly_the_allowed_types(self):
        prompt = llm.PROMPT.format(today="t", name="n", url="u", text="x", city="c", region="r", categories=", ".join(llm.CATEGORIES))
        self.assertIn("tags from: " + ", ".join(llm.CATEGORIES) + ",", prompt)
        for c in llm.CATEGORIES:
            self.assertEqual(c, c.strip().lower())

    def test_applies_to_is_all_or_a_short_list_of_names(self):
        self.assertEqual(self.clean(applies_to=["Whyte Ave", 5, "", "x" * 81])["applies_to"], ["Whyte Ave"])
        self.assertEqual(self.clean(applies_to={"a": 1})["applies_to"], "all")
        self.assertEqual(self.clean(applies_to="everywhere but here")["applies_to"], "all")
        self.assertEqual(self.clean(applies_to=[1, 2])["applies_to"], "all")
        self.assertEqual(len(self.clean(applies_to=[f"Branch {i}" for i in range(30)])["applies_to"]), 20)

    def test_deals_naming_a_link_email_or_handle_are_dropped(self):
        for field, text in [("title", "Wings at cheap-wings.example"), ("description", "Book at https://evil.example/now"),
                            ("description", "Order via www.evil.example"), ("description", "Email deals@evil.example"),
                            ("description", "DM @wingsguy for the code"), ("title", "Free wings bit.ly/abc"),
                            ("description", "See http://10.0.0.1/x")]:
            self.assertIsNone(self.clean(**{field: text}), text)

    def test_ordinary_text_and_the_restaurants_own_site_are_kept(self):
        for text in ["Take 10% off pickup orders placed online at wingplace.ca.", "Order at www.wingplace.ca",
                     "Mon.-Fri. 3-6 p.m., e.g. pints and wings", "2 @ $5 after 9 pm", "No. 1 combo, $5.99"]:
            self.assertIsNotNone(self.clean(description=text), text)


class LLMAddress(unittest.TestCase):
    def test_a_plain_http_ai_address_is_refused_before_anything_is_sent(self):
        before = llm.requests_made
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "openai-compatible", "LLM_BASE_URL": "http://api.example/v1",
                                          "LLM_API_KEY": "k", "LLM_MODEL": "m"}), \
             mock.patch.object(llm.requests, "post", side_effect=AssertionError("sent")):
            with self.assertRaisesRegex(llm.LLMError, "https://"):
                llm.extract_deals("A", "https://a.ca", "text")
        self.assertEqual(llm.requests_made, before)

    def test_https_and_the_default_are_fine(self):
        for env in ({"LLM_BASE_URL": "https://api.example/v1/"}, {}):
            with mock.patch.dict(os.environ, env, clear=False):
                if not env:
                    os.environ.pop("LLM_BASE_URL", None)
                self.assertTrue(llm._base_url().startswith("https://"))


class GeneratedPages(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.city = cities.City(**{**cities.get("edmonton").__dict__, "id": "_test_pages", "data_dir": str(self.tmp),
                                   "pages_dir": str(self.tmp / "pages")})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, deals):
        rest = [{"id": "r1", "name": "Wing Place", "address": "1 Main St", "website": "https://wingplace.ca"}]
        base = {"days": [0, 1, 2, 3, 4, 5, 6], "restaurant_ids": ["r1"]}
        (self.tmp / "deals.json").write_text(json.dumps({"generated_at": "2026-11-16T10:00:00+00:00", "restaurants": rest,
                                                         "deals": [{**base, **d} for d in deals]}))
        with mock.patch("builtins.print"):
            pages.build(self.city, [])
        return {p.name: p.read_text() for p in self.city.pages.glob("*.html")}

    def test_every_page_has_a_tight_content_security_policy(self):
        html = pages.page("Wings", "Wing deals", "deals/wings.html", "<p>x</p>", "today", cities.get("edmonton"))
        csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html).group(1)
        self.assertLess(html.index("Content-Security-Policy"), html.index("<style>"))
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src https://gc.zgo.at;", csp)
        self.assertIn("connect-src https://ctyh.goatcounter.com", csp)
        self.assertNotIn("unsafe", csp)
        style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)  # the page's one style is the one allowed
        self.assertIn("'sha256-" + base64.b64encode(hashlib.sha256(style.encode()).digest()).decode() + "'", csp)
        self.assertNotIn("style=", html)
        self.assertEqual(re.findall(r"<script[^>]*>", html), ['<script data-goatcounter="https://ctyh.goatcounter.com/count" async src="https://gc.zgo.at/count.v5.js" crossorigin="anonymous" integrity="sha384-atnOLvQb9t+jTSipvd75X2yginT4PjVbqDdlJAmxMm+wYElFmeR6EmLP5bYeoRVQ">'])

    def test_a_javascript_link_is_never_written(self):
        out = self.build([{"title": "Wing night", "source_url": "javascript:alert(1)", "categories": ["wings"]}] * 3)
        self.assertTrue(out)
        for html in out.values():
            self.assertNotIn("javascript:", html)
            self.assertIn("Content-Security-Policy", html)
        self.assertIn('href="https://wingplace.ca"', out["wings.html"])

    def test_a_deal_type_cannot_overwrite_a_day_page_or_the_hub(self):
        deals = [{"title": f"Deal {i}", "categories": ["monday", "index", "!!!", "happy hour", "happy-hour"]} for i in range(3)]
        out = self.build(deals)
        self.assertIn("Monday restaurant deals", out["monday.html"])
        self.assertIn("<h1>Restaurant deals in", out["index.html"])
        self.assertEqual(sorted(out), sorted(["index.html", "happy-hour.html"] + [d.lower() + ".html" for d in pages.DAY_LONG]))
        self.assertIn("cat=happy+hour", out["happy-hour.html"])  # the address is encoded


if __name__ == "__main__":
    unittest.main()
