"""The offline copy of the app (site/sw.js SHELL) must hold every script the page loads, and only real files."""
import json
import re
import unittest
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


def shell() -> list[str]:
    m = re.search(r"const SHELL = (\[[^\]]*\]);", (SITE / "sw.js").read_text())
    return json.loads(m.group(1))


def local_scripts() -> list[str]:
    srcs = re.findall(r'<script[^>]*\ssrc="([^"]+)"', (SITE / "index.html").read_text())
    return [s for s in srcs if not re.match(r"^(https?:)?//", s)]


class AppShell(unittest.TestCase):
    def test_every_local_script_is_cached_for_offline(self):
        scripts = local_scripts()
        self.assertIn("app-state.js", scripts)
        for s in scripts:
            self.assertIn(s, shell(), f"add {s} to SHELL in site/sw.js (and bump CACHE)")

    def test_every_cached_file_exists(self):
        for f in shell():
            if f != "./":
                self.assertTrue((SITE / f).is_file(), f"SHELL in site/sw.js lists {f}, which isn't in site/")

    def test_app_state_loads_before_app(self):
        scripts = local_scripts()
        self.assertLess(scripts.index("app-state.js"), scripts.index("app.js"))


if __name__ == "__main__":
    unittest.main()
