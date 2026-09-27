"""Make every app icon from packaging/app-logo.png (the app's own logo — separate from the document / branding logo
chosen inside the ERP). Replace that file and run:  python packaging/make_icons.py

Writes: packaging/icon.ico (Windows exe + installers), packaging/icon.png (Mac app), and the installable-site icons in
web-src/ (Android / iPhone home screen)."""
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
logo = Image.open(HERE / "app-logo.png").convert("RGBA")
logo = logo.crop(logo.getbbox())            # trim empty edges


def icon(n, pad, bg=None):
    im = Image.new("RGBA", (n, n), bg or (0, 0, 0, 0))
    box = int(n * (1 - 2 * pad))
    lg = logo.copy()
    scale = box / max(lg.size)
    lg = lg.resize((max(1, round(lg.width * scale)), max(1, round(lg.height * scale))), Image.LANCZOS)
    im.alpha_composite(lg, ((n - lg.width) // 2, (n - lg.height) // 2))
    return im


icon(256, 0.04).save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
icon(1024, 0.08).save(HERE / "icon.png")
white = (255, 255, 255, 255)
for n in (192, 512):
    icon(n, 0.1, white).convert("RGB").save(ROOT / "web-src" / f"icon-{n}.png", optimize=True)
icon(512, 0.2, white).convert("RGB").save(ROOT / "web-src" / "icon-maskable-512.png", optimize=True)
icon(180, 0.1, white).convert("RGB").save(ROOT / "web-src" / "apple-touch-icon.png", optimize=True)
icon(64, 0.04).save(ROOT / "web-src" / "favicon.png")
print("icons written")
