"""Put the desktop builds and the offline zip into web/downloads/ with a small index the app reads
(downloads/beyond-downloads.json). Files over Cloudflare Pages' 25 MB limit are left out with a warning."""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 25 * 1024 * 1024 - 4096
src = Path(sys.argv[1] if len(sys.argv) > 1 else "dl")
out = ROOT / "web" / "downloads"
out.mkdir(parents=True, exist_ok=True)
version = re.search(r'__version__ = "([^"]+)"', (ROOT / "erp" / "__init__.py").read_text()).group(1)
wanted = [("windows", "BeyondERP-Setup.exe"), ("mac", "BeyondERP-mac.zip"), ("windows_offline", "BeyondERP-Offline-Setup.exe"),
          ("mac_offline", "BeyondERP-Offline-mac.zip"), ("web", "beyond-erp-web.zip")]
files = []
for kind, name in wanted:
    found = next(iter(sorted(src.rglob(name))), None) or (ROOT / name if (ROOT / name).exists() else None)
    if not found:
        print(f"::warning::{name} was not built — the download page will say so")
        continue
    if found.stat().st_size > LIMIT:
        print(f"::warning::{name} is {found.stat().st_size / 1048576:.1f} MB — over the 25 MB hosting limit, left out")
        continue
    shutil.copy(found, out / name)
    files.append({"kind": kind, "file": name, "size": found.stat().st_size})
    print(f"{name}: {found.stat().st_size / 1048576:.1f} MB")
(out / "beyond-downloads.json").write_text(json.dumps({"version": version, "files": files}, indent=1))
