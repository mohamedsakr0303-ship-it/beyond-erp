"""Make every app icon from packaging/app-logo.png (the app's own logo — separate from the document / branding logo
chosen inside the ERP). Replace that file and run:  python packaging/make_icons.py

Writes: packaging/icon.ico (Windows exe + installers), packaging/icon.png (Mac app), the installable-site icons in
web-src/ (Android / iPhone home screen), the in-app default icon / sidebar logo (erp/static/icons/beyond.png) and the
small icon carried inside both index pages.
The mark itself stays transparent (no white square); where a platform needs a solid square (iPhone, Android's
full-bleed "maskable" icon) it sits on the app's night navy."""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
logo = Image.open(HERE / "app-logo.png").convert("RGBA")
logo = logo.crop(logo.getbbox())            # trim empty edges


def green(n):
    """The app's night navy, a touch of indigo top-left to near-black bottom-right (#0b0c3a → #02021a)."""
    im = Image.new("RGBA", (n, n))
    d = ImageDraw.Draw(im)
    a, b = (11, 12, 58), (2, 2, 26)
    for i in range(2 * n):
        t = i / (2 * n - 1)
        d.line([(i, 0), (0, i)], fill=tuple(round(a[k] + (b[k] - a[k]) * t) for k in range(3)) + (255,))
    return im


def icon(n, pad, bg=None, src=None):
    im = green(n) if bg == "green" else Image.new("RGBA", (n, n), bg or (0, 0, 0, 0))
    box = int(n * (1 - 2 * pad))
    lg = (src or logo).copy()
    scale = box / max(lg.size)
    lg = lg.resize((max(1, round(lg.width * scale)), max(1, round(lg.height * scale))), Image.LANCZOS)
    im.alpha_composite(lg, ((n - lg.width) // 2, (n - lg.height) // 2))
    return im


icon(256, 0.04).save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
icon(1024, 0.08).save(HERE / "icon.png")
for n in (192, 512):
    icon(n, 0.06).save(ROOT / "web-src" / f"icon-{n}.png", optimize=True)
icon(512, 0.1, "green").convert("RGB").save(ROOT / "web-src" / "icon-maskable-512.png", optimize=True)
icon(180, 0.07, "green").convert("RGB").save(ROOT / "web-src" / "apple-touch-icon.png", optimize=True)
icon(64, 0.04).save(ROOT / "web-src" / "favicon.png")
icon(512, 0.0).save(ROOT / "erp" / "static" / "icons" / "beyond.png", optimize=True)   # in-app default icon + sidebar logo
# Every icon choice in Settings -> App icon gets real icon files and its own manifest on the site, so an installed
# phone app is a proper app with that icon (a manifest made in the page would make Android show Chrome's badge on it)
import json
CHOICES = {"beyond": logo}       # the one BeyondERP icon (the earlier alternatives were retired)
base = json.loads((ROOT / "web-src" / "manifest.webmanifest").read_text(encoding="utf-8"))
(ROOT / "erp" / "static" / "icons" / "app").mkdir(exist_ok=True)
for k, src in CHOICES.items():
    src = src.crop(src.getbbox())
    out = ROOT / "erp" / "static" / "icons" / "app"
    icon(192, 0.06, src=src).save(out / f"{k}-192.png", optimize=True)
    icon(512, 0.06, src=src).save(out / f"{k}-512.png", optimize=True)
    icon(512, 0.12, "green", src=src).convert("RGB").save(out / f"{k}-maskable.png", optimize=True)
    m = dict(base, icons=[{"src": f"icons/app/{k}-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": f"icons/app/{k}-512.png", "sizes": "512x512", "type": "image/png"},
                          {"src": f"icons/app/{k}-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}])
    (ROOT / "web-src" / f"manifest-{k}.webmanifest").write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
# the offline desktop app serves erp/static, and the page itself carries the small icon (works from a zip / file://
# too): Edge / Chrome use it for the app window's title bar and taskbar
import base64
import re
import shutil
fav = base64.b64encode((ROOT / "web-src" / "favicon.png").read_bytes()).decode()
for name in ("favicon.png", "icon-192.png"):
    shutil.copy(ROOT / "web-src" / name, ROOT / "erp" / "static" / name)
for page in (ROOT / "web-src" / "index.html", ROOT / "erp" / "static" / "index.html"):
    html = page.read_text(encoding="utf-8")
    html = re.sub(r'(<link rel="icon" id="appicon" type="image/png" sizes="64x64" href=")[^"]*(">)',
                  lambda m: m.group(1) + "data:image/png;base64," + fav + m.group(2), html)
    page.write_text(html, encoding="utf-8")
# the universal default letterhead logo (new books, import templates): the mark trimmed, at most 400 px
mark = logo.copy()
mark.thumbnail((400, 400), Image.LANCZOS)
b = __import__("io").BytesIO()
mark.save(b, "PNG", optimize=True)
# the default watermark: the same mark, greyscale and faded (as Settings -> Documents & branding makes it from a logo)
wm = mark.convert("LA").convert("RGBA")
wm.putalpha(wm.getchannel("A").point(lambda v: int(v * 0.16)))
wm.thumbnail((480, 480), Image.LANCZOS)
wb = __import__("io").BytesIO()
wm.save(wb, "PNG", optimize=True)
(ROOT / "erp" / "brand_assets.py").write_text(
    '"""The universal default logo (the BeyondERP mark), used where a company has not set its own: new books\' letterhead\n'
    'and the import templates, plus its faded watermark. Replace them in Settings -> Documents & branding.\n'
    'Built from packaging/app-logo.png by packaging/make_icons.py."""\n'
    f'LOGO = {{"mime": "image/png", "w": {mark.width}, "h": {mark.height}, "data": "{base64.b64encode(b.getvalue()).decode()}"}}\n'
    f'WATERMARK = {{"mime": "image/png", "w": {wm.width}, "h": {wm.height}, "data": "{base64.b64encode(wb.getvalue()).decode()}"}}\n')
print("icons written")
