"""Shared workspace on Supabase: many people, many devices, one set of books.

How it works
------------
Every device keeps the full engine and its own SQLite copy. What devices share is an ordered **log of changes**
(the `ops` table in Supabase): a change is the same API call the screen made (method, path, body), plus who made
it and when. To make a change a device

1. pulls the changes it has not seen yet and replays them,
2. runs its own change as a dry run (so a mistake never reaches the log),
3. appends it with the next sequence number — if another device got that number first, Supabase refuses
   (SQLSTATE 40001) and the device goes back to step 1 — and
4. applies it for real.

Replays use the change's own timestamp as "now" and run as the app user who made it, so every device computes the
same result and re-checks that user's permissions. A new device starts from the latest snapshot (a full copy saved
in Supabase) and replays only what came after it.

This module holds the transport-free core (used by both editions) and the Supabase client for the desktop/server
edition (the browser edition talks to Supabase from JavaScript, see web-src/bridge.js).
"""
import base64
import datetime as _dt
import gzip
import json
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from . import db as _db

# Calls that only concern this device (sessions, previews, maintenance) or talk to outside systems — replaying them
# on every device would be wrong (e.g. submitting an e-invoice to the tax authority once per device).
LOCAL_ONLY = [r"POST /api/login", r"POST /api/logout", r"POST /api/setup", r"PUT /api/me/password",
              r"POST /api/maintenance", r"POST /api/offers-preview", r"POST /api/einvoice/test",
              r"POST /api/einvoice/submit", r"POST /api/ereceipts-submit", r"POST /api/einvoice/\d+/refresh", r"POST /api/einvoice/\d+/cancel",
              r"POST /api/[\w-]+-preview(?:/[\w-]+)?", r"\w+ /api/cloud/.*",
              r"\w+ /api/ai/.*"]   # the assistant's key, requests and conversations stay on the device
_LOCAL_RX = re.compile("^(?:" + "|".join(LOCAL_ONLY) + ")$")
ETA_CALLS = re.compile(r"^POST /api/einvoice/(?:submit|(\d+)/(?:refresh|cancel))$")
SNAPSHOT_EVERY = 300          # changes between automatic workspace copies
# People created in Users without an email sign in through an internal address <username>@PLACEHOLDER_DOMAIN
# (supabase/functions/manage-user); it is never emailed. The ".invalid" top-level domain is reserved for this.
PLACEHOLDER_DOMAIN = "users.beyond-erp.invalid"


def username_for(email):
    """The app username behind a workspace account address, when it is an internal placeholder."""
    e = (email or "").strip().lower()
    return e.rsplit("@", 1)[0] if e.endswith("@" + PLACEHOLDER_DOMAIN) else None
PULL_PAGE = 500


class Conflict(Exception):
    """Someone else appended first."""


class Offline(Exception):
    pass


class NeedsUpdate(Exception):
    """The log contains changes made by a newer app version."""


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "0"))[:3])


def replicated(method, target):
    """Is this API call a change every device must replay?"""
    if method not in ("POST", "PUT", "DELETE"):
        return False
    url = urlparse(target)
    if not url.path.startswith("/api/") or _LOCAL_RX.match(f"{method} {url.path}"):
        return False
    if url.path == "/api/import" and "dry_run=1" in (url.query or ""):
        return False
    return True


# ------------------------------------------------------------------ device-local settings (never replicated)
def local_get(db, key, default=None):
    r = db.one("SELECT value FROM cloud_local WHERE key=?", (key,))
    return r["value"] if r else default


def local_set(db, key, value):
    if value is None:
        db.x("DELETE FROM cloud_local WHERE key=?", (key,))
    else:
        db.x("INSERT OR REPLACE INTO cloud_local(key,value) VALUES (?,?)", (key, str(value)))


def device_id(db):
    d = local_get(db, "device")
    if not d:
        d = uuid.uuid4().hex[:12]
        local_set(db, "device", d)
    return d


def last_seq(db):
    return int(local_get(db, "last_seq", "-1") or -1)


def joined(db):
    return last_seq(db) >= 0 and bool(local_get(db, "url"))


# ------------------------------------------------------------------ change encoding
def encode_body(raw, ctype):
    if not raw:
        return ""
    if "json" in (ctype or ""):
        return raw.decode("utf-8")
    return "b64:" + base64.b64encode(raw).decode()


def decode_body(text):
    if not text:
        return b""
    if text.startswith("b64:"):
        return base64.b64decode(text[4:])
    return text.encode("utf-8")


def _strip_password(method, path, body_text):
    """Passwords never go to the workspace: people sign in there with their own account. Returns (logged body,
    password to set on this device only)."""
    if not re.match(r"^/api/users(/\d+)?$", urlparse(path).path) or not body_text:
        return body_text, None
    try:
        body = json.loads(body_text)
    except ValueError:
        return body_text, None
    pw = body.pop("password", None) or None
    if method == "POST":
        body["_no_password"] = True
    return json.dumps(body, ensure_ascii=False), pw


def _parse_clock(s):
    s = str(s).replace("Z", "+00:00")
    t = _dt.datetime.fromisoformat(s)
    if t.tzinfo:  # store and replay in the device's local wall-clock of the author: keep it as written
        t = t.replace(tzinfo=None)
    return t.replace(microsecond=0)


# ------------------------------------------------------------------ core: dry run, apply
def prepare(app, method, target, headers, raw, user):
    """Dry-run a change as `user`; returns (reply, op or None). The database is left untouched."""
    from .server import handle, ok, route_module
    if headers.get("x-ai-call") == "1":   # the assistant's change: same policy, check and review as without the workspace
        from . import ai
        path = urlparse(target).path
        try:
            ai.guard(app.db, user, method, path, raw, headers, route_module(method, path))
        except PermissionError as e:
            return ok({"error": str(e) or "Not allowed"}, 403), None
    conn = app.db.conn
    with app.db.lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            reply = handle(app, method, target, headers, raw, as_user=user["username"], dry=True)
        finally:
            conn.execute("ROLLBACK")
    if reply.status >= 400:
        return reply, None
    ctype = headers.get("content-type") or ""
    body, pw = _strip_password(method, target, encode_body(raw, ctype))
    op = {"version": __version__, "app_user": user["username"], "device": device_id(app.db), "method": method,
          "path": target, "body": body, "content_type": ctype,
          "clock": _db.now().isoformat(timespec="seconds"), "_password": pw}
    return reply, op


def apply(app, op):
    """Replay one change from the log (own or someone else's). Returns the reply."""
    from .server import handle
    if version_tuple(op.get("version")) > version_tuple(__version__):
        raise NeedsUpdate(op.get("version"))
    headers = {"x-requested-with": "BeyondERP", "content-type": op.get("content_type") or "application/json"}
    _db.CLOCK["at"] = _parse_clock(op["clock"])
    try:
        reply = handle(app, op["method"], op["path"], headers, decode_body(op.get("body")), as_user=op["app_user"])
    finally:
        _db.CLOCK["at"] = None
    local_set(app.db, "last_seq", int(op["seq"]))
    pw = op.get("_password")
    if pw and reply.status < 400:  # this device's own change: set the password locally only
        _set_local_password(app.db, op, reply, pw)
    return reply


def _set_local_password(db, op, reply, pw):
    from . import auth
    m = re.match(r"^/api/users/(\d+)$", urlparse(op["path"]).path)
    uid = int(m.group(1)) if m else (json.loads(reply.body or b"{}").get("id"))
    if uid:
        db.x("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_password(pw), uid))


def followups(db, method, target, body):
    """After a device talks to the tax authority, share the resulting e-invoice status with everyone."""
    m = ETA_CALLS.match(f"{method} {urlparse(target).path}")
    if not m:
        return []
    ids = [int(m.group(1))] if m.group(1) else [int(i) for i in (body or {}).get("ids") or []]
    out = []
    for xid in ids:
        x = db.one("SELECT eta_uuid, eta_status, eta_submission, eta_error FROM extracts WHERE id=?", (xid,))
        if x:
            out.append(("PUT", f"/api/extracts/{xid}/eta-state", json.dumps(x, ensure_ascii=False).encode()))
    return out


def ensure_app_user(db, email, admin=False):
    """A workspace account signs in as the app user whose username is its email."""
    from . import auth
    email = email.strip().lower()
    u = db.one("SELECT * FROM users WHERE lower(email)=? OR lower(username)=?", (email, email))
    if not u:
        name = email.split("@")[0]
        username = name if not db.one("SELECT 1 FROM users WHERE lower(username)=?", (name.lower(),)) else email
        # the company's first administrator owns the books (super administrator); later ones are administrators
        first = admin and not db.one("SELECT 1 FROM users WHERE is_super=1 AND active=1")
        auth.create_user(db, {"username": username, "email": email, "full_name": name, "is_admin": admin,
                              "_no_password": True, "perms": {"dashboard": "view"}}, first=first)
    elif admin and not u["is_admin"]:
        db.x("UPDATE users SET is_admin=1, active=1 WHERE id=?", (u["id"],))


# ------------------------------------------------------------------ snapshots (server edition; the browser does
# the same in JavaScript on its sql.js image)
LOCAL_TABLES = ("cloud_local", "sessions", "ai_threads", "ai_usage")   # left out of workspace copies (device-only)


def _keep_on_restore(db):
    # sessions are dropped: user ids change with the restored books, so everyone signs in again
    return {"cloud_local": db.q("SELECT * FROM cloud_local"), "sessions": []}


def snapshot_native(db):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snap.sqlite3"
        db.backup_to(path)
        c = _db.sqlite3.connect(str(path))
        for t in LOCAL_TABLES:
            c.execute(f"DELETE FROM {t}")
        c.commit()
        c.execute("VACUUM")
        c.close()
        return base64.b64encode(gzip.compress(path.read_bytes(), 6)).decode()


def restore_native(db, data_b64):
    keep = _keep_on_restore(db)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "restore.sqlite3"
        path.write_bytes(gzip.decompress(base64.b64decode(data_b64)))
        src = _db.sqlite3.connect(str(path))
        with db.lock:
            src.backup(db.conn)
        src.close()
    restore_local_rows(db, keep)


def restore_local_rows(db, keep):
    with db.lock:
        db._init()  # schema upgrades if the copy came from an older version
        for t, rows in keep.items():
            db.conn.execute(f"DELETE FROM {t}")
            for r in rows:
                cols = list(r)
                db.conn.execute(f"INSERT OR REPLACE INTO {t}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                [r[c] for c in cols])


# ------------------------------------------------------------------ Supabase REST client (stdlib)
AUTH_HELP = {
    "invalid_credentials": "Wrong email or password. No account yet? Use “Create account”. Just created one? Open the "
                           "confirmation email Supabase sent first.",
    "email_not_confirmed": "Please confirm your email first: open the link Supabase sent you (check spam), then sign in.",
    "over_email_send_rate_limit": "Supabase's free email service only sends a few emails per hour — try again later, or "
                                  "create the account in the Supabase dashboard (Authentication → Users → Add user, "
                                  "tick “Auto Confirm User”).",
    "user_already_exists": "An account with this email already exists — sign in instead.",
    "weak_password": "Choose a longer password (at least 6 characters).",
}


def auth_help(code, msg):
    if code in AUTH_HELP:
        return AUTH_HELP[code]
    low = msg.lower()
    for needle, key in (("invalid login credentials", "invalid_credentials"), ("email not confirmed", "email_not_confirmed"),
                        ("rate limit", "over_email_send_rate_limit"), ("already registered", "user_already_exists")):
        if needle in low:
            return AUTH_HELP[key]
    return msg


def fresh_books(company_name=""):
    """Empty books for a new company (default chart of accounts, settings…), named after the company."""
    from .db import Database
    db = Database(":memory:")
    if company_name:
        db.set_setting("company_name", company_name)
    return db


class Supabase:
    def __init__(self, url, key, timeout=25):
        self.url, self.key, self.timeout = url.rstrip("/"), key, timeout

    def _call(self, method, path, body=None, token=None, headers=None):
        h = {"apikey": self.key, "Content-Type": "application/json", "Authorization": f"Bearer {token or self.key}"}
        h.update(headers or {})
        req = urllib.request.Request(self.url + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                err = json.loads(raw)
            except ValueError:
                err = {"message": raw.decode("utf-8", "replace")[:300]}
            msg = err.get("message") or err.get("msg") or err.get("error_description") or err.get("error") or str(e)
            if err.get("code") == "40001":
                raise Conflict(msg) from None
            msg = auth_help(err.get("error_code") or "", msg)
            ex = PermissionError(msg) if e.code in (401, 403) or err.get("code") == "42501" else RuntimeError(msg)
            ex.status = e.code
            raise ex from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise Offline(f"Cannot reach the workspace ({getattr(e, 'reason', e)})") from None

    def sign_in(self, email, password):
        return self._call("POST", "/auth/v1/token?grant_type=password", {"email": email, "password": password})

    def sign_up(self, email, password):
        return self._call("POST", "/auth/v1/signup", {"email": email, "password": password})

    def sign_in_username(self, username, password):
        try:
            return self._call("POST", "/functions/v1/login", {"username": username, "password": password})
        except RuntimeError as e:
            if getattr(e, "status", 0) == 404:
                raise PermissionError("Signing in with a username is not set up on this workspace yet — use the email "
                                      "address (the administrator deploys the “login” function, see the setup guide)") from None
            raise

    def refresh(self, refresh_token):
        return self._call("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": refresh_token})

    def rpc(self, name, params, token):
        return self._call("POST", f"/rest/v1/rpc/{name}", params, token)

    def select(self, table, query, token):
        return self._call("GET", f"/rest/v1/{table}?{query}", token=token)


class CloudSync:
    """The desktop/server edition's link to the workspace. One workspace account (ideally marked "gateway") relays
    the changes of this server's local users."""

    def __init__(self, app):
        self.app, self.db = app, app.db
        self.lock = threading.RLock()
        self.status = {"state": "off", "error": "", "last_sync": ""}
        self._token, self._token_exp = None, 0
        self._stop = threading.Event()
        self._thread = None

    # -- configuration
    @property
    def active(self):
        return joined(self.db)

    def client(self):
        url, key = local_get(self.db, "url"), local_get(self.db, "key")
        if not url or not key:
            raise PermissionError("Connect this device to a workspace first (Settings → Workspace)")
        return Supabase(url, key)

    def configure(self, url, key):
        url = (url or "").strip().rstrip("/")
        if not re.match(r"^https?://", url) or not (key or "").strip():
            raise ValueError("Enter the project URL (https://….supabase.co) and the anon public key")
        local_set(self.db, "url", url)
        local_set(self.db, "key", key.strip())

    def _save_session(self, s):
        if not s or not s.get("access_token"):
            raise PermissionError("Sign-in failed — confirm your email first if Supabase sent you a link")
        self._token, self._token_exp = s["access_token"], time.time() + int(s.get("expires_in") or 3600) - 60
        local_set(self.db, "refresh", s.get("refresh_token"))
        local_set(self.db, "email", ((s.get("user") or {}).get("email") or local_get(self.db, "email") or "").lower())

    def sign_in(self, email, password, create=False):
        c = self.client()
        email = (email or "").strip().lower()
        if create:
            r = c.sign_up(email, password) or {}
            if not r.get("access_token"):   # "Confirm email" is on (Supabase's default)
                if isinstance(r.get("identities"), list) and not r["identities"]:
                    raise PermissionError(AUTH_HELP["user_already_exists"])
                raise PermissionError(f"Almost done: we sent a confirmation email to {email}. Open the link in it, then "
                                      "come back here and choose Sign in.")
            self._save_session(r)
            return self.info()
        self._save_session(c.sign_in(email, password) if "@" in email else c.sign_in_username(email, password))
        return self.info()

    def token(self):
        if self._token and time.time() < self._token_exp:
            return self._token
        rt = local_get(self.db, "refresh")
        if not rt:
            raise PermissionError("Sign in to the workspace again (Settings → Workspace)")
        self._save_session(self.client().refresh(rt))
        return self._token

    # -- companies: one workspace holds several companies, each with its own shared books and members. This device
    # follows one of them (local "company"); switching means disconnecting and joining the other one.
    @property
    def company(self):
        return local_get(self.db, "company") or ""

    def _co(self):
        if not self.company:
            raise PermissionError("Choose a company first (Settings → Workspace)")
        return self.company

    def rpc(self, name, params=None):
        return self.client().rpc(name, params or {}, self.token())

    def companies(self):
        return self.rpc("my_companies") or []

    def choose_company(self, cid):
        cid = (cid or "").strip()
        if self.active and cid != self.company:
            raise RuntimeError("This device is connected to another company — disconnect it first")
        if not any(c["id"] == cid and c.get("active") and not c.get("pending") for c in self.companies()):
            raise PermissionError("You are not a member of that company")
        local_set(self.db, "company", cid)
        return self.info()

    def create_company(self, name):
        co = self.rpc("create_company", {"p_name": name})
        if not self.active:
            local_set(self.db, "company", co["id"])
        return co

    def join_company(self, code):
        return self.rpc("join_company", {"p_code": code})

    def info(self):
        """This device's company as the signed-in person sees it, plus the list of their companies."""
        cos = self.companies()
        if not self.company:
            live = [c for c in cos if c.get("active") and not c.get("pending")]
            if len(live) == 1:
                local_set(self.db, "company", live[0]["id"])
        if not self.company:
            return {"email": local_get(self.db, "email") or "", "is_member": False, "has_members": True, "companies": cos,
                    "needs_company": True}
        return {**(self.rpc("workspace_info", {"p_company": self.company}) or {}), "companies": cos,
                "company": self.company}

    def members(self):
        return self.rpc("company_members", {"p_company": self._co()}) or []

    def set_member(self, email, is_admin=False, gateway=False, active=True):
        return self.rpc("set_member", {"p_company": self._co(), "p_email": email, "p_is_admin": bool(is_admin),
                                       "p_gateway": bool(gateway), "p_active": active is not False})

    def remove_member(self, email):
        return self.rpc("remove_member", {"p_company": self._co(), "p_email": email})

    def link_member(self, email, username="", active=True):
        return self.rpc("link_member", {"p_company": self._co(), "p_email": email, "p_username": username or "",
                                        "p_active": active is not False})

    def manage_user(self, body):
        return self.client()._call("POST", "/functions/v1/manage-user", {**body, "company": self._co()}, self.token())

    def rename_company(self, name):
        return self.rpc("rename_company", {"p_company": self._co(), "p_name": name})

    def new_invite_code(self):
        return self.rpc("new_invite_code", {"p_company": self._co()})

    # -- joining
    def claim(self, name=None):
        """Start a new company from this device's books (you become its administrator)."""
        with self.lock:
            if self.active:
                raise RuntimeError("This device is already connected to a company — disconnect it first")
            c, tok = self.client(), self.token()
            name = (name or self.db.setting("company_name") or "My company").strip()
            co = c.rpc("create_company", {"p_name": name}, tok)
            local_set(self.db, "company", co["id"])
            ensure_app_user(self.db, local_get(self.db, "email") or "", admin=True)
            c.rpc("put_snapshot", {"p_company": co["id"], "p_seq": 0, "p_version": __version__,
                                   "p_data": snapshot_native(self.db)}, tok)
            local_set(self.db, "last_seq", 0)
            local_set(self.db, "snap_seq", 0)
            self.start()
            return self.info()

    def join(self, company=None):
        """Replace this device's books with the company's shared books (a backup is taken first)."""
        with self.lock:
            if company:
                self.choose_company(company)
            c, tok = self.client(), self.token()
            info = self.info()
            if info.get("needs_company"):
                raise PermissionError("Choose which company to open")
            if not info.get("is_member"):
                raise PermissionError("This account is not a member of this company yet — ask its administrator")
            rows = c.select("snapshots", f"select=seq,version,data&company_id=eq.{self.company}&order=seq.desc&limit=1", tok)
            if not rows:
                if not info.get("is_admin"):
                    raise RuntimeError("This company's books are not set up yet — its administrator opens it first")
                # a brand-new company: its administrator starts its books here (empty, with the company's name)
                if hasattr(self.app, "backup"):
                    self.app.backup("before-new-company")
                restore_native(self.db, snapshot_native(fresh_books(info.get("company_name") or "")))
                ensure_app_user(self.db, info.get("email") or "", admin=True)
                c.rpc("put_snapshot", {"p_company": self.company, "p_seq": 0, "p_version": __version__,
                                       "p_data": snapshot_native(self.db)}, tok)
                local_set(self.db, "last_seq", 0)
                local_set(self.db, "snap_seq", 0)
                self.start()
                return self.info()
            snap = rows[0]
            if version_tuple(snap["version"]) > version_tuple(__version__):
                raise NeedsUpdate(snap["version"])
            if hasattr(self.app, "backup"):
                self.app.backup("before-join")
            restore_native(self.db, snap["data"])
            local_set(self.db, "last_seq", int(snap["seq"]))
            local_set(self.db, "snap_seq", int(snap["seq"]))
            self.pull()
            self.start()
            return info

    def disconnect(self):
        self.stop()
        for k in ("last_seq", "refresh", "email", "snap_seq", "company"):
            local_set(self.db, k, None)
        self._token = None
        self.status = {"state": "off", "error": "", "last_sync": ""}

    # -- syncing
    def pull(self):
        """Replay everything new. Returns how many changes were applied."""
        with self.lock:
            c, n = self.client(), 0
            while True:
                rows = c.select("ops", f"select=*&company_id=eq.{self._co()}&seq=gt.{last_seq(self.db)}"
                                       f"&order=seq.asc&limit={PULL_PAGE}", self.token())
                for op in rows:
                    if int(op["seq"]) != last_seq(self.db) + 1:
                        raise RuntimeError("The workspace log has a gap — try again in a moment")
                    apply(self.app, op)
                    n += 1
                if len(rows) < PULL_PAGE:
                    break
            self.status.update(state="ok", error="", last_sync=_dt.datetime.now().isoformat(timespec="seconds"))
            return n

    def write(self, method, target, headers, raw, user):
        """Make a change through the workspace; returns the reply the screen expects."""
        from .server import ok
        with self.lock:
            try:
                for _ in range(8):
                    self.pull()
                    reply, op = prepare(self.app, method, target, headers, raw, user)
                    if op is None:
                        return reply
                    op["seq"] = last_seq(self.db) + 1
                    try:
                        stored = self.client().rpc("append_op", {
                            "p_company": self._co(), "p_seq": op["seq"], "p_version": op["version"], "p_app_user": op["app_user"],
                            "p_device": op["device"], "p_method": op["method"], "p_path": op["path"],
                            "p_body": op["body"], "p_content_type": op["content_type"], "p_clock": op["clock"]},
                            self.token())
                    except Conflict:
                        continue
                    op["app_user"] = (stored or {}).get("app_user") or op["app_user"]  # exactly as the log has it
                    reply = apply(self.app, op)
                    self._maybe_snapshot()
                    return reply
                return ok({"error": "The workspace is very busy — please try again"}, 409)
            except Offline as e:
                self.status.update(state="offline", error=str(e))
                return ok({"error": f"{e}. Changes need a connection to the shared workspace."}, 503)
            except NeedsUpdate as e:
                return ok({"error": f"A newer app version ({e}) has written to the workspace — update this app first"}, 409)
            except PermissionError as e:
                self.status.update(state="signed_out", error=str(e))
                return ok({"error": f"Workspace: {e}"}, 403)

    def _maybe_snapshot(self):
        seq, snap = last_seq(self.db), int(local_get(self.db, "snap_seq", "0") or 0)
        if seq - snap < SNAPSHOT_EVERY:
            return
        try:
            self.client().rpc("put_snapshot", {"p_company": self._co(), "p_seq": seq, "p_version": __version__,
                                               "p_data": snapshot_native(self.db)}, self.token())
            local_set(self.db, "snap_seq", seq)
        except (PermissionError, RuntimeError, Offline):
            local_set(self.db, "snap_seq", seq)  # not allowed or offline: try again after the next batch

    def touch(self):
        """Someone is using the app (called on every API request) — keep syncing at the quick pace."""
        self._used = time.time()

    def start(self, every=20, idle_every=300, idle_after=600):
        """Check for other people's changes every 20 s while the app is in use, every 5 min when nobody has used it for
        10 min. Every check is a request Supabase logs, so fewer checks keep its log usage down. A change made here
        always fetches what is new first, so nothing is missed."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._used = getattr(self, "_used", time.time())

        def loop():
            last = 0.0
            while not self._stop.wait(every):
                if not self.active:
                    continue
                idle = time.time() - self._used > idle_after
                if idle and time.time() - last < idle_every:
                    continue
                last = time.time()
                try:
                    self.pull()
                except Offline as e:
                    self.status.update(state="offline", error=str(e))
                except NeedsUpdate as e:
                    self.status.update(state="update", error=f"Update this app to version {e} or newer")
                except Exception as e:  # noqa: BLE001 — keep the loop alive, show the problem in Settings
                    self.status.update(state="error", error=str(e)[:300])
        self._thread = threading.Thread(target=loop, name="workspace-sync", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def public_status(self):
        return {"configured": bool(local_get(self.db, "url")), "url": local_get(self.db, "url") or "",
                "key": local_get(self.db, "key") or "", "email": local_get(self.db, "email") or "",
                "joined": self.active, "last_seq": last_seq(self.db), "device": device_id(self.db),
                "company": self.company, **self.status}
