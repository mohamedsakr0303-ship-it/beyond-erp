"""What search engines read on the hosted site: the home page's tags, robots.txt and sitemap.xml (build_web.search_files)."""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_web  # noqa: E402


class SearchFiles(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, True)
        shutil.copy(ROOT / "web-src" / "index.html", self.out / "index.html")
        self.was, build_web.OUT = build_web.OUT, self.out
        self.addCleanup(setattr, build_web, "OUT", self.was)
        self.env = os.environ.pop("SITE_URL", None)
        self.addCleanup(lambda: os.environ.__setitem__("SITE_URL", self.env) if self.env is not None else os.environ.pop("SITE_URL", None))

    def page(self):
        return (self.out / "index.html").read_text(encoding="utf-8")

    def test_with_the_site_address(self):
        os.environ["SITE_URL"] = "https://erp.example.com"
        build_web.search_files()
        html = self.page()
        self.assertIn('<link rel="canonical" href="https://erp.example.com/">', html)
        self.assertIn('content="https://erp.example.com/icon-512.png"', html)
        self.assertIn('<meta name="description"', html)
        self.assertEqual(len(re.findall(r"<h1[ >]", html)), 2)              # the page's one heading (and its no-script copy)
        self.assertIn("© 2026 BEYONDERP", html)
        data = json.loads(re.search(r'ld\+json">(.*?)</script>', html).group(1))
        self.assertEqual((data["@type"], data["url"]), ("SoftwareApplication", "https://erp.example.com/"))
        self.assertIn("Sitemap: https://erp.example.com/sitemap.xml", (self.out / "robots.txt").read_text())
        self.assertIn("<loc>https://erp.example.com/</loc>", (self.out / "sitemap.xml").read_text())
        self.assertIn('hreflang="ar" href="https://erp.example.com/ar.html"', html)
        ar = (self.out / "ar.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="ar" dir="rtl">', ar)
        self.assertIn("<title>BeyondERP · أدِر أعمالك، لا أوراقها</title>", ar)
        self.assertIn('<link rel="canonical" href="https://erp.example.com/ar.html">', ar)
        self.assertIn('localStorage.setItem("berp.lang",\'"ar"\')', ar)          # opens in Arabic the first time
        self.assertIn("<loc>https://erp.example.com/ar.html</loc>", (self.out / "sitemap.xml").read_text())

    def test_without_it(self):
        build_web.search_files()
        html = self.page()
        self.assertNotIn("__SITE__", html)
        self.assertNotIn("canonical", html)
        self.assertNotIn("url", json.loads(re.search(r'ld\+json">(.*?)</script>', html).group(1)))
        self.assertFalse((self.out / "sitemap.xml").exists())
        self.assertIn("Allow: /", (self.out / "robots.txt").read_text())

    def test_private_pages_stay_out_of_search(self):
        for f in ("owner.html", "p.html"):
            self.assertIn('name="robots" content="noindex', (ROOT / "web-src" / f).read_text(encoding="utf-8"), f)


if __name__ == "__main__":
    unittest.main()
