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


PWA_FILES = ("manifest.webmanifest", "manifest-beyond.webmanifest", "manifest-rising.webmanifest", "manifest-seal.webmanifest",
             "manifest-original.webmanifest", "sw.js", "icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png", "favicon.png")


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
    for f in ("index.html", "bridge.js"):
        shutil.copy(ROOT / "web-src" / f, OUT / f)
    # installable app (manifest, offline service worker, icons) — used by hosted builds, left out of the zip
    for f in PWA_FILES:
        shutil.copy(ROOT / "web-src" / f, OUT / f)
    with zipfile.ZipFile(OUT / "erp.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((ROOT / "erp").glob("*.py")):
            z.write(p, f"erp/{p.name}")
    pack(names)
    # Workspace preset for hosted builds (GitHub Pages…): BEYOND_SUPABASE_URL / BEYOND_SUPABASE_ANON_KEY. The anon key
    # is public by design — access is enforced by Supabase sign-in + row level security.
    import os
    preset = {"url": os.environ.get("BEYOND_SUPABASE_URL", ""), "key": os.environ.get("BEYOND_SUPABASE_ANON_KEY", "")}
    (OUT / "config.js").write_text("window.BEYOND_CLOUD_CONFIG = " + (json.dumps(preset) if all(preset.values()) else "null")
                                   + ";\n", encoding="utf-8")
    # build number: changes whenever any app file changes. The open app compares it with build.json on the site and
    # offers "Update now" when they differ (app.js, "app updates")
    h = hashlib.sha256()
    for f in sorted([*(OUT.glob("*.js")), *(OUT.glob("*.css")), OUT / "index.html"]):
        h.update(f.name.encode() + f.read_bytes())
    build_id = h.hexdigest()[:12]
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "erp" / "__init__.py").read_text()).group(1)
    (OUT / "build.js").write_text(f"window.BEYOND_BUILD = {json.dumps(build_id)};\n", encoding="utf-8")
    (OUT / "build.json").write_text(json.dumps({"build": build_id, "version": version}), encoding="utf-8")
    # headers file understood by Netlify / Cloudflare Pages / static.app-style hosts (ignored elsewhere)
    (OUT / "_headers").write_text("/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: same-origin\n"
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
