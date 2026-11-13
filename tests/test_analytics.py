"""The visit counter is on the app and on every generated search page."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import pages  # noqa: E402


class Counter(unittest.TestCase):
    def test_app_has_the_counter(self):
        self.assertIn("ctyh.goatcounter.com/count", (ROOT / "site" / "index.html").read_text())

    def test_generated_pages_have_the_counter(self):
        import cities
        html = pages.page("Wings", "Wing deals", "deals/wings.html", "<p>x</p>", "today", cities.pick([])[0])
        self.assertIn("ctyh.goatcounter.com/count", html)
        self.assertLess(html.index("goatcounter"), html.index("</head>"))
