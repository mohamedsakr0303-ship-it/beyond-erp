"""Put the public desktop builds (the connected app) into web/downloads/ with a small index the app reads
(downloads/beyond-downloads.json). Files over Cloudflare Pages' 25 MB limit are left out with a warning.

  python packaging/collect_downloads.py <artifacts folder> [site url]

Installers not built in this run (the app code did not change) are copied from the live site, so the download page
keeps offering them."""
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 25 * 1024 * 1024 - 4096
src = Path(sys.argv[1] if len(sys.argv) > 1 else "dl")
site = (sys.argv[2] if len(sys.argv) > 2 else "").rstrip("/")
out = ROOT / "web" / "downloads"
out.mkdir(parents=True, exist_ok=True)
version = re.search(r'__version__ = "([^"]+)"', (ROOT / "erp" / "__init__.py").read_text()).group(1)
UA = {"User-Agent": "Mozilla/5.0 (beyond-erp build)"}  # Cloudflare refuses Python's default user agent


def fetch(url, timeout):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


live = {}
if site:
    try:
        live = json.loads(fetch(f"{site}/downloads/beyond-downloads.json", 30))
    except (OSError, ValueError):
        print("::notice::No downloads on the live site yet")
live_files = {f["file"]: f for f in live.get("files", [])}
reused = False
# Only the connected desktop app (a small launcher that opens the website) is public. The offline editions carry the whole
# app, so they are never put on the website (nor built by Actions while the repository is public).
wanted = [("windows", "BeyondERP-Setup.exe"), ("mac", "BeyondERP-mac.zip")]
files = []
for kind, name in wanted:
    found = next(iter(sorted(src.rglob(name))), None) or (ROOT / name if (ROOT / name).exists() else None)
    if not found and name in live_files:
        try:  # not rebuilt this time: keep the one the site already offers
            (out / name).write_bytes(fetch(f"{site}/downloads/{name}", 300))
            files.append(live_files[name])
            reused = True
            print(f"{name}: kept from the live site")
            continue
        except OSError as e:
            print(f"::warning::{name} could not be copied from the live site ({e})")
    if not found:
        print(f"::warning::{name} was not built — the download page will say so")
        continue
    if found.stat().st_size > LIMIT:
        print(f"::warning::{name} is {found.stat().st_size / 1048576:.1f} MB — over the 25 MB hosting limit, left out")
        continue
    shutil.copy(found, out / name)
    files.append({"kind": kind, "file": name, "size": found.stat().st_size})
    print(f"{name}: {found.stat().st_size / 1048576:.1f} MB")
# installers are only reused when the app code did not change, so their version is still the current one
(out / "beyond-downloads.json").write_text(json.dumps({"version": live.get("version", version) if reused else version,
                                                       "files": files}, indent=1))
