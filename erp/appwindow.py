"""Open the app in its own clean window (Edge / Chrome / Brave app mode: no tabs, no address bar), with a separate
browser profile that keeps the sign-in. Falls back to the default browser. Used by the desktop apps."""
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path


def candidates():
    if sys.platform.startswith("win"):
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        rel = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
               r"BraveSoftware\Brave-Browser\Application\brave.exe"]
        return [str(Path(r) / x) for x in rel for r in roots if r]
    if sys.platform == "darwin":
        return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    return [shutil.which(x) or "" for x in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]


def profile_dir():
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    d = base / "BeyondERP" / "app-window"
    d.mkdir(parents=True, exist_ok=True)
    return d


def open_window(url, profile="app-window"):
    for exe in candidates():
        if exe and Path(exe).is_file():
            d = profile_dir().parent / profile
            d.mkdir(parents=True, exist_ok=True)
            flags = 0x08000000 if sys.platform.startswith("win") else 0  # CREATE_NO_WINDOW
            subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={d}", "--no-first-run",
                              "--no-default-browser-check", "--window-size=1400,900"],
                             creationflags=flags, close_fds=True)
            return True
    webbrowser.open(url)
    return False
