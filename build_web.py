#!/usr/bin/env python3
"""Build the browser-only edition into ./web — upload that folder to static.app, Netlify, Cloudflare Pages…

    python build_web.py            # downloads the runtimes once (npm + PyPI), then builds ./web
    python build_web.py --zip      # also writes beyond-erp-web.zip, ready for drag-and-drop upload

Everything the site needs is self-hosted inside ./web (no CDN at runtime):
  vendor/pyodide/   Python 3.12 compiled to WebAssembly (from the npm package "pyodide")
  vendor/sql-wasm.* SQLite compiled to WebAssembly (npm package "sql.js")
  vendor/wheels/    openpyxl + et_xmlfile (pure-Python wheels from PyPI) for Excel import/export
  erp.zip           this app's Python engine
"""
import argparse
import base64
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".web-cache"
OUT = ROOT / "web"
NPM = {"pyodide": "0.26.4", "sql.js": "1.14.2"}
WHEELS = {"openpyxl": "3.1.5", "et-xmlfile": "2.0.0"}
PYODIDE_FILES = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]


def fetch(url):
    CACHE.mkdir(exist_ok=True)
    target = CACHE / url.rsplit("/", 1)[1]
    if not target.exists():
        print("  downloading", url)
        with urllib.request.urlopen(url, timeout=120) as r:
            target.write_bytes(r.read())
    return target.read_bytes()


def npm_tarball(name, version):
    base = name.split("/")[-1]
    return tarfile.open(fileobj=io.BytesIO(fetch(f"https://registry.npmjs.org/{name}/-/{base}-{version}.tgz")))


def wheel_url(name, version):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as r:
        info = json.load(r)
    for f in info["urls"]:
        if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-none-any.whl"):
            return f["url"]
    raise SystemExit(f"No pure-Python wheel for {name} {version}")


PACK_CHUNK = 2 * 1024 * 1024  # bytes per pack file (≈2.7 MB of text) — under every host's per-file limit


def pack(wheel_names):
    """Move every binary (.wasm, .zip, .whl, .json) into plain .js files loaded with <script> tags.

    Hosts often refuse or mistype those file types, cap file sizes (pyodide.asm.wasm is 10 MB), and browsers block
    fetch() for pages opened straight from disk (file://). Script tags work everywhere, so the bytes travel as
    base64 inside .js files and bridge.js hands them to Pyodide / sql.js from memory.
    """
    binaries = [OUT / "vendor" / "pyodide" / f for f in ("pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")]
    binaries += [OUT / "vendor" / "sql-wasm.wasm", OUT / "erp.zip"] + [OUT / "vendor" / "wheels" / w for w in wheel_names]
    (OUT / "packs").mkdir()
    files = []
    for path in binaries:
        data = path.read_bytes()
        parts = [data[i:i + PACK_CHUNK] for i in range(0, len(data), PACK_CHUNK)] or [b""]
        for i, part in enumerate(parts):
            body = f"BEYOND_PACK({json.dumps(path.name)},{i},{len(parts)},\"{base64.b64encode(part).decode()}\");\n"
            # named by content: a file that did not change keeps its address, so browsers keep their stored copy
            # across updates (only the changed parts — usually just erp.zip — are downloaded again)
            name = f"packs/{hashlib.sha256(body.encode()).hexdigest()[:16]}.js"
            (OUT / name).write_text(body, encoding="ascii")
            files.append(name)
        path.unlink()
    shutil.rmtree(OUT / "vendor" / "wheels")
    (OUT / "packs.js").write_text(f"window.BEYOND_WHEELS = {json.dumps(wheel_names)};\n"
                                  f"window.BEYOND_PACK_FILES = {json.dumps(files)};\n", encoding="utf-8")


PWA_FILES = ("og-image.jpg", "og-image-ar.jpg", "manifest.webmanifest", "manifest-beyond.webmanifest", "sw.js", "icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png", "favicon.png")


COPYRIGHT = "BeyondERP — © {year} BEYONDERP. All rights reserved. Proprietary and confidential: copying, modifying, " \
            "reverse-engineering or redistributing this software, in whole or in part, without written permission is prohibited."
ESBUILD = "esbuild@0.24.0"


def minify():
    """The app's own scripts and styles go out minified (no comments, short local names) with the copyright notice."""
    import datetime
    banner = "/*! " + COPYRIGHT.format(year=datetime.date.today().year) + " */"
    npx = shutil.which("npx")
    if not npx:
        raise SystemExit("npx (Node.js) is needed to minify the web app")
    for f in ("app.js", "i18n.js", "bridge.js", "sw.js", "app.css", "owner.js", "owner.css"):
        path = OUT / f
        subprocess.run([npx, "--yes", ESBUILD, str(path), "--minify", "--legal-comments=none", f"--banner:{'css' if f.endswith('.css') else 'js'}={banner}",
                        "--target=es2020" if f.endswith(".js") else "--target=chrome100", f"--outfile={path}", "--allow-overwrite", "--log-level=warning"],
                       check=True)


AR = {"title": "BeyondERP · أدِر أعمالك، لا أوراقها",
      "description": "أدِر أعمالك، لا أوراقها. يجمع BeyondERP الحسابات والمبيعات والمشتريات والمخزون والمشروعات والموارد البشرية والرواتب "
                     "في نظام سحابي واحد، مع تقارير لحظية وفاتورة إلكترونية، بالعربية والإنجليزية. ابدأ بفترة تجريبية مجانية.",
      "og": "الحسابات والمبيعات والمخزون والمشروعات والموارد البشرية والرواتب في نظام سحابي واحد، بالعربية والإنجليزية."}


def arabic_page(html, site):
    """The home page for Arabic searches and shares (ar.html): the same app, with its title, description and preview in
    Arabic, opening in Arabic for a first-time visitor."""
    html = html.replace('<html lang="en">', '<html lang="ar" dir="rtl">')
    html = re.sub(r"<title>.*?</title>", f"<title>{AR['title']}</title>", html)
    html = re.sub(r'<meta name="description" content="[^"]*">', f'<meta name="description" content="{AR["description"]}">', html)
    html = re.sub(r'<meta property="og:title" content="[^"]*">', f'<meta property="og:title" content="{AR["title"]}">', html)
    html = re.sub(r'<meta property="og:description" content="[^"]*">', f'<meta property="og:description" content="{AR["og"]}">', html)
    html = html.replace('<meta property="og:locale" content="en_US">', '<meta property="og:locale" content="ar_AR">')
    html = html.replace('<meta property="og:locale:alternate" content="ar_AR">', '<meta property="og:locale:alternate" content="en_US">')
    html = html.replace("og-image.jpg", "og-image-ar.jpg").replace(
        '<meta property="og:image:alt" content="BeyondERP: Run the business, not the paperwork.">',
        '<meta property="og:image:alt" content="BeyondERP: أدِر أعمالك، لا أوراقها.">')
    if site:
        html = html.replace(f'<link rel="canonical" href="{site}">', f'<link rel="canonical" href="{site}ar.html">')
        html = html.replace(f'<meta property="og:url" content="{site}">', f'<meta property="og:url" content="{site}ar.html">')
    html = re.sub(r"<noscript>.*?</noscript>", "<noscript><h1>BeyondERP</h1><p>أدِر أعمالك، لا أوراقها. نظام سحابي واحد للحسابات والمبيعات "
                  "والمشتريات والمخزون والمشروعات والموارد البشرية والرواتب، بالعربية والإنجليزية. فعّل JavaScript لاستخدام التطبيق.</p></noscript>",
                  html, flags=re.S)
    # a first-time visitor of the Arabic page gets the app in Arabic (a language chosen before is kept)
    return html.replace('<script>(function(){try{var l="";', '<script>try{if(!localStorage.getItem("berp.lang")&&!localStorage.getItem("berp.look"))'
                        'localStorage.setItem("berp.lang",\'"ar"\');}catch(e){}</script>\n<script>(function(){try{var l="";', 1)


def search_files():
    """What search engines read: the site's own address in the home page (canonical link, sharing preview, structured
    data), robots.txt and sitemap.xml. SITE_URL (set by the deploy) is the public address; without it the
    address-dependent tags are left out. The app's other pages (owner console, public booking / form pages) say noindex."""
    import datetime
    import os
    site = (os.environ.get("SITE_URL") or "").strip()
    if site and not site.endswith("/"):
        site += "/"
    page = OUT / "index.html"
    html = page.read_text(encoding="utf-8")
    if site:
        html = html.replace("__SITE__", site)
    else:
        html = "\n".join(x for x in html.split("\n") if "__SITE__" not in x or "ld+json" in x)
        html = html.replace('"url":"__SITE__",', "")
    page.write_text(html, encoding="utf-8")
    (OUT / "ar.html").write_text(arabic_page(html, site), encoding="utf-8")
    robots = "User-agent: *\nAllow: /\n"           # scripts and styles stay open: search engines render the page with them
    if site:
        robots += f"\nSitemap: {site}sitemap.xml\n"
        (OUT / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                         '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
                                         + "".join(f"  <url><loc>{site}{p}</loc><lastmod>{datetime.date.today().isoformat()}</lastmod>"
                                                   f'<xhtml:link rel="alternate" hreflang="en" href="{site}"/>'
                                                   f'<xhtml:link rel="alternate" hreflang="ar" href="{site}ar.html"/></url>\n' for p in ("", "ar.html"))
                                         + "</urlset>\n", encoding="utf-8")
    (OUT / "robots.txt").write_text(robots, encoding="utf-8")


def build(make_zip=False):
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "vendor" / "pyodide").mkdir(parents=True)
    (OUT / "vendor" / "wheels").mkdir(parents=True)
    print("Runtimes")
    tar = npm_tarball("pyodide", NPM["pyodide"])
    for f in PYODIDE_FILES:
        (OUT / "vendor" / "pyodide" / f).write_bytes(tar.extractfile(f"package/{f}").read())
    tar = npm_tarball("sql.js", NPM["sql.js"])
    for f in ("sql-wasm.js", "sql-wasm.wasm"):
        (OUT / "vendor" / f).write_bytes(tar.extractfile(f"package/dist/{f}").read())
    names = []
    for name, ver in WHEELS.items():
        cache_name = None
        for p in CACHE.glob(f"{name.replace('-', '_')}-{ver}-*.whl") if CACHE.exists() else []:
            cache_name = p.name
        data = (CACHE / cache_name).read_bytes() if cache_name else fetch(wheel_url(name, ver))
        fname = cache_name or next(CACHE.glob(f"{name.replace('-', '_')}-{ver}-*.whl")).name
        (OUT / "vendor" / "wheels" / fname).write_bytes(data)
        names.append(fname)
    print("App")
    for f in ("app.css", "i18n.js", "app.js", "assistant.jpg"):
        shutil.copy(ROOT / "erp" / "static" / f, OUT / f)
    shutil.copytree(ROOT / "erp" / "static" / "icons", OUT / "icons", dirs_exist_ok=True)   # app icon choices
    for f in (ROOT / "erp" / "static" / "vendor").glob("*"):   # fonts, 3D model viewer (loaded only when a model is shown)
        if f.is_dir():
            shutil.copytree(f, OUT / "vendor" / f.name, dirs_exist_ok=True)
        else:
            shutil.copy(f, OUT / "vendor" / f.name)
    # p.html: the public pages (booking, forms, signing, guest chat); owner.*: the owner console, a page of its own
    for f in ("index.html", "bridge.js", "p.html", "owner.html", "owner.js", "owner.css"):
        shutil.copy(ROOT / "web-src" / f, OUT / f)
    search_files()
    # installable app (manifest, offline service worker, icons) — used by hosted builds, left out of the zip
    for f in PWA_FILES:
        shutil.copy(ROOT / "web-src" / f, OUT / f)
    # the ERP's Python goes out as bytecode only (no source, comments or docstrings) — see packaging/compile_web_py.py
    py312 = [sys.executable] if sys.version_info[:2] == (3, 12) else [shutil.which("python3.12") or "python3.12"]
    subprocess.run(py312 + [str(ROOT / "packaging" / "compile_web_py.py"), str(OUT / "erp.zip")], check=True)
    minify()
    pack(names)
    # Workspace preset for hosted builds (GitHub Pages…): BEYOND_SUPABASE_URL / BEYOND_SUPABASE_ANON_KEY. The anon key
    # is public by design — access is enforced by Supabase sign-in + row level security.
    import os
    preset = {"url": os.environ.get("BEYOND_SUPABASE_URL", ""), "key": os.environ.get("BEYOND_SUPABASE_ANON_KEY", "")}
    # where the platform owner downloads the offline editions (a private GitHub release; GITHUB_REPOSITORY is set in Actions)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    releases = f"https://github.com/{repo}/releases/tag/desktop-latest" if repo else ""
    (OUT / "config.js").write_text("window.BEYOND_CLOUD_CONFIG = " + (json.dumps(preset) if all(preset.values()) else "null")
                                   + ";\nwindow.BEYOND_RELEASES = " + json.dumps(releases) + ";\n", encoding="utf-8")
    # build number: changes whenever any app file changes. The open app compares it with build.json on the site and
    # offers "Update now" when they differ (app.js, "app updates")
    h = hashlib.sha256()
    for f in sorted([*(OUT.glob("*.js")), *(OUT.glob("*.css")), OUT / "index.html"]):
        h.update(f.name.encode() + f.read_bytes())
    build_id = h.hexdigest()[:12]
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "erp" / "__init__.py").read_text()).group(1)
    (OUT / "build.js").write_text(f"window.BEYOND_BUILD = {json.dumps(build_id)};\n", encoding="utf-8")
    (OUT / "build.json").write_text(json.dumps({"build": build_id, "version": version}), encoding="utf-8")
    # headers file understood by Netlify / Cloudflare Pages / static.app-style hosts (ignored elsewhere): no framing by
    # other sites, HTTPS only, no sniffing, nothing leaked in referrers, no camera / microphone / location for others
    (OUT / "_headers").write_text("/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: same-origin\n"
                                  "  X-Frame-Options: DENY\n  Content-Security-Policy: frame-ancestors 'none'; base-uri 'self'; object-src 'none'\n"
                                  "  Strict-Transport-Security: max-age=31536000; includeSubDomains\n"
                                  "  Cross-Origin-Opener-Policy: same-origin-allow-popups\n"
                                  "  Permissions-Policy: camera=(self), microphone=(self), geolocation=(self), payment=()\n"
                                  "  X-Robots-Tag: noai, noimageai\n"
                                  "/vendor/*\n  Cache-Control: public, max-age=31536000, immutable\n"
                                  "/build.json\n  Cache-Control: no-store\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"Built {OUT} ({size / 1048576:.1f} MB)")
    if make_zip:
        out = ROOT / "beyond-erp-web.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(OUT.rglob("*")):
                if p.is_file() and p.name not in PWA_FILES and "downloads" not in p.parts:
                    z.write(p, p.relative_to(OUT).as_posix())
        print(f"Wrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", action="store_true")
    build(ap.parse_args().zip)
    sys.exit(0)
