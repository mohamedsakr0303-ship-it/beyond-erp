#!/usr/bin/env python3
"""Start Beyond ERP.

    python run.py                    # open http://127.0.0.1:8765 in your browser
    python run.py --host 0.0.0.0     # share on your office network
    python run.py --data D:\\Books    # keep the database somewhere else
    python run.py reset-password admin
    python run.py import-legacy "BEYOND_Accounting_System_V0_6_1.xlsx"
    python run.py build-workbook out.xlsx

Needs only Python 3.9+ (Windows, macOS, Linux). Excel features use the optional
'openpyxl' package:  python -m pip install openpyxl
"""
import argparse
import getpass
import sys
import threading
import webbrowser
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit("Beyond ERP needs Python 3.9 or newer — download it from https://www.python.org/downloads/")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from erp import __version__, auth  # noqa: E402
from erp.db import Database, default_data_dir  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=f"Beyond ERP {__version__}")
    p.add_argument("command", nargs="?", default="serve",
                   choices=["serve", "reset-password", "import-legacy", "import", "build-workbook", "backup",
                            "licence-keygen", "licence-issue"])
    p.add_argument("arg", nargs="?")
    p.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this computer only; 0.0.0.0 = whole network")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--data", default=None, help="folder for the database and backups")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--replace", action="store_true", help="import: wipe existing data first")
    p.add_argument("--verbose", action="store_true")
    lic = p.add_argument_group("licences (vendor only)")
    lic.add_argument("--key", help="licence-issue: your private key file (from licence-keygen)")
    lic.add_argument("--company", help="licence-issue: the customer's company name")
    lic.add_argument("--edition", default="business", help="starter | business | pro | enterprise")
    lic.add_argument("--users", type=int, default=5)
    lic.add_argument("--companies", type=int, default=1)
    lic.add_argument("--updates-months", type=int, default=12, help="months of updates included (maintenance)")
    lic.add_argument("--install", default="", help="the customer's installation ID (Settings → System → Licence)")
    lic.add_argument("--expires", default="", help="YYYY-MM-DD for a time-limited licence; blank = perpetual")
    a = p.parse_args(argv)

    if a.command == "licence-keygen":
        from erp import licence
        try:
            priv, pub = licence.keygen(a.arg or Path.home() / ".beyond-licence")
        except FileExistsError as e:
            sys.exit(str(e))
        print(f"Private key: {priv}\n  KEEP IT SECRET AND BACKED UP. Anyone with it can make licences; losing it means you cannot.")
        print(f"Public key written to {licence.PUBLIC_KEY_FILE} — commit it: from the next release, installations need a licence.")
        return
    if a.command == "licence-issue":
        from erp import licence
        import datetime as _dt
        if not a.key or not a.company:
            sys.exit("Give --key (your private key file) and --company")
        seed = bytes.fromhex(Path(a.key).expanduser().read_text().strip())
        today = _dt.date.today()
        m = today.month - 1 + a.updates_months
        until = today.replace(year=today.year + m // 12, month=m % 12 + 1, day=min(today.day, 28)).isoformat()
        payload = {"company": a.company, "edition": a.edition, "users": a.users, "companies": a.companies,
                   "issued": today.isoformat(), "updates_until": until, "install": a.install.strip().upper(),
                   "expires": a.expires, "id": __import__("secrets").token_hex(4)}
        print(licence.issue(seed, payload))
        return

    data_dir = Path(a.data).expanduser() if a.data else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(data_dir / "beyond-erp.sqlite3")

    if a.command == "reset-password":
        user = a.arg or input("Username: ")
        row = db.one("SELECT id FROM users WHERE username=?", (user,))
        if not row:
            sys.exit(f"No user named {user!r}")
        pw = getpass.getpass("New password (8+ characters): ")
        db.x("UPDATE users SET pw_hash=?, active=1 WHERE id=?", (auth.hash_password(pw), row["id"]))
        db.x("DELETE FROM sessions WHERE user_id=?", (row["id"],))
        print("Password updated.")
        return

    if a.command in ("import-legacy", "import"):
        from erp import interchange, legacy
        src = Path(a.arg or "")
        if not src.is_file():
            sys.exit("Give the path of the file to import")
        if a.command == "import-legacy":
            data = legacy.read_legacy_workbook(src)
        elif src.suffix.lower() == ".json":
            data = interchange.read_json(src.read_bytes())
        else:
            data = interchange.read_xlsx(src)
        try:
            res = interchange.import_data(db, data, mode="replace" if a.replace else "append", user="cli")
        except interchange.ImportErrors as e:
            for err in e.errors:
                print(f"  {err['sheet']} row {err['row']}: {err['error']}")
            sys.exit(str(e))
        for note in data.get("_notes", []):
            print("  note:", note)
        print("Imported:", res["counts"])
        return

    if a.command == "build-workbook":
        from erp import workbook
        out = Path(a.arg or "BEYOND_ERP.xlsx")
        out.write_bytes(workbook.build_workbook(db))
        print(f"Wrote {out.resolve()}")
        return

    from erp.server import ERPServer
    url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '') else a.host}:{a.port}/"
    try:
        server = ERPServer((a.host, a.port), db, data_dir / "backups", verbose=a.verbose)
    except OSError:
        # already running (e.g. the app was opened twice): just show it
        import urllib.request
        try:
            with urllib.request.urlopen(url + "api/bootstrap", timeout=3) as r:
                running = b'"version"' in r.read(200)
        except OSError:
            running = False
        if running and a.command == "serve":
            print(f"Beyond ERP is already running — opening {url}")
            if not a.no_browser:
                if getattr(sys, "frozen", False):
                    from erp.appwindow import open_window
                    open_window(url, "offline-window")
                else:
                    webbrowser.open(url)
            return
        sys.exit(f"Port {a.port} is in use by another program — start with --port 8766")

    if a.command == "backup":
        print("Backup written:", server.backup("manual"))
        return

    server.daily_backup()
    print(f"Beyond ERP {__version__}")
    print(f"  Open:     {url}")
    print(f"  Data:     {data_dir}")
    if a.host == "0.0.0.0":
        print("  Network:  other computers can connect using this computer's IP address and the same port")
    print("  Stop:     press Ctrl+C" + (" or close this window" if getattr(sys, "frozen", False) else ""))
    if not a.no_browser:  # the installed app opens in its own window; running from source opens a browser tab
        opener = (lambda: __import__("erp.appwindow", fromlist=["x"]).open_window(url, "offline-window")) \
            if getattr(sys, "frozen", False) else (lambda: webbrowser.open(url))
        threading.Timer(1.0, opener).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        server.server_close()
        db.close()


if __name__ == "__main__":
    main()
