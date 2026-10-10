"""Updates for the installed desktop app (the offline edition, which runs on this computer).

The app asks the website which version it offers (downloads/beyond-downloads.json, written by every deploy). When it is
newer, the app shows "Update now": a backup of the books is made, the new installer is downloaded and run silently, this
copy closes, and the installer opens the new version when it finishes. The books live in the data folder (APPDATA /
Application Support), which the installer never touches, so everything is kept.

The cloud apps (the website, the installed phone / PC app) need none of this: they check the site's build number
themselves and reload into the new files (see app.js, "app updates").
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from . import __version__

SITE = (os.environ.get("BEYOND_UPDATE_SITE") or "https://beyond-erp.pages.dev/").rstrip("/") + "/"
UA = {"User-Agent": "Mozilla/5.0 (BeyondERP desktop)"}   # Cloudflare refuses Python's default user agent
_cache = {"at": 0.0, "info": None}


def vt(v):
    out = []
    for part in str(v or "0").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out)


def installer_name():
    if sys.platform.startswith("win"):
        return "BeyondERP-Offline-Setup.exe"
    if sys.platform == "darwin":
        return "BeyondERP-Offline-mac.zip"
    return None


def _fetch(url, timeout):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def latest(max_age=3600):
    """What the website offers now (cached for an hour)."""
    if _cache["info"] is not None and time.time() - _cache["at"] < max_age:
        return _cache["info"]
    info = json.loads(_fetch(SITE + "downloads/beyond-downloads.json", 10))
    _cache.update(at=time.time(), info=info)
    return info


def check():
    frozen = bool(getattr(sys, "frozen", False))
    out = {"current": __version__, "latest": None, "newer": False, "desktop": frozen, "can_install": False, "url": SITE + "#download"}
    if not frozen:            # running from source / on a server: updated with git, not from here
        return out
    try:
        info = latest()
    except (OSError, ValueError):
        return out            # offline: ask again later
    name = installer_name()
    files = {f.get("file") for f in info.get("files", [])}
    out["latest"] = info.get("version")
    out["newer"] = vt(out["latest"]) > vt(__version__)
    if name and name in files:
        out["url"] = SITE + "downloads/" + name
        out["can_install"] = sys.platform.startswith("win")
    return out


def install(backup, stop):
    """Back up the books, download the installer, start it silently and close this copy (Windows)."""
    info = check()
    if not (info["newer"] and info["can_install"]):
        raise ValueError("No update to install here — download it from the website instead")
    backup("before-update")
    data = _fetch(info["url"], 600)
    if len(data) < 1_000_000 or data[:2] != b"MZ":
        raise ValueError("The download was incomplete — please try again")
    path = Path(tempfile.mkdtemp(prefix="beyond-update-")) / installer_name()
    path.write_bytes(data)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=flags, close_fds=True)
    threading.Timer(1.5, stop).start()      # let the reply reach the window, then free the files for the installer
    return {"started": True, "version": info["latest"]}
