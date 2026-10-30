"""BeyondERP (online) — opens the hosted app in its own clean window: no tabs, no address bar, its own icon.

It is the website itself, so it uses the same sign-in and the same shared books: whatever is done here is on the
website and on every phone at once. Uses Edge or Chrome's app mode (a separate profile keeps the sign-in); falls back
to the default browser. The site address is baked in at build time (packaging/site_url.txt).
"""
import sys
from pathlib import Path

from erp.appwindow import open_window


def site_url():
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    try:
        return (here / "site_url.txt").read_text().strip() or "https://beyonderp.org/"
    except OSError:
        return "https://beyonderp.org/"


if __name__ == "__main__":
    open_window(site_url())
