"""Each page's Content-Security-Policy (the list of places it may load from) must match what the page really uses.

If a test here fails after you edited an inline <script> or <style>, the message gives the new fingerprint to
paste into that page's Content-Security-Policy line.
"""
import base64
import hashlib
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit

SITE = Path(__file__).resolve().parent.parent / "site"


def policy(html: str) -> dict:
    m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html)
    assert m, "no Content-Security-Policy meta tag"
    out = {}
    for part in m.group(1).split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


def fingerprint(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}"


class AppPolicy(unittest.TestCase):
    html = (SITE / "index.html").read_text()
    csp = policy(html)

    def test_comes_before_any_script(self):
        self.assertLess(self.html.index("Content-Security-Policy"), self.html.index("<script"))

    def test_every_inline_script_is_allowed_by_its_fingerprint(self):
        inline = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        self.assertTrue(inline)
        for body in inline:
            self.assertIn(fingerprint(body), self.csp["script-src"],
                          f"put {fingerprint(body)} in script-src in site/index.html")

    def test_no_unsafe_scripts(self):
        for bad in ("'unsafe-inline'", "'unsafe-eval'", "*", "https:", "data:"):
            self.assertNotIn(bad, self.csp["script-src"])
        self.assertNotRegex(self.html, r"<[^>]+\son[a-z]+=", "inline event handlers (onclick=...) are refused")
        for k in ("object-src", "base-uri", "form-action"):
            self.assertEqual(self.csp[k], ["'none'"], k)

    def test_every_outside_script_is_allowed_and_pinned(self):
        for tag in re.findall(r"<script[^>]*\ssrc=\"https?://[^>]*>", self.html):
            self.assertIn(origin(re.search(r'src="([^"]+)"', tag).group(1)), self.csp["script-src"], tag)
            self.assertRegex(tag, r'integrity="sha384-[A-Za-z0-9+/=]+"', "outside scripts need an integrity value")

    def test_config_addresses_are_allowed(self):
        config = (SITE / "config.js").read_text()
        urls = re.findall(r'(?:dataUrl|API_URL):\s*"(https://[^"]+)"', config)
        self.assertGreaterEqual(len(urls), 2)
        for u in urls:
            self.assertIn(origin(u), self.csp["connect-src"], f"add {origin(u)} to connect-src in site/index.html")

    def test_visit_counter_is_allowed(self):
        counter = re.search(r'data-goatcounter="([^"]+)"', self.html).group(1)
        self.assertIn(origin(counter), self.csp["connect-src"])

    def test_map_tiles_and_fonts_are_allowed(self):
        app = (SITE / "app.js").read_text()
        tiles = re.search(r'tileLayer\("([^"{]+)', app).group(1)
        self.assertIn(origin(tiles), self.csp["img-src"])
        for link in re.findall(r'<link href="(https://[^"]+)" rel="stylesheet">', self.html):
            self.assertIn(origin(link), self.csp["style-src-elem"])


class StaticPagePolicies(unittest.TestCase):
    def test_static_pages_allow_only_their_own_style(self):
        for page in ("privacy/index.html", "bot/index.html"):
            html = (SITE / page).read_text()
            csp = policy(html)
            self.assertEqual(csp["default-src"], ["'none'"], page)
            self.assertNotIn("<script", html, page)
            styles = re.findall(r"<style>(.*?)</style>", html, re.S)
            for body in styles:
                self.assertIn(fingerprint(body), csp["style-src"], f"put {fingerprint(body)} in style-src in site/{page}")
            self.assertNotRegex(html, r"<[^>]+\sstyle=", f"{page}: a style=\"...\" attribute would be refused")


if __name__ == "__main__":
    unittest.main()
