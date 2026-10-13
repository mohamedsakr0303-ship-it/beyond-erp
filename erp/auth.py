"""Users, password hashing (PBKDF2, stdlib) and per-module permissions."""
import hashlib
import hmac
import json
import re
import os
import secrets

from .accounting import ValidationError, text
from .db import ACTIONS, MODULES, PERM_DEFAULTS, PERM_FROM, SUBMODULES, SUBTABS

ITERATIONS = 240_000
if os.environ.get("BEYOND_ERP_STATIC") == "1":  # browser build: pure-Python hashing is slow, data stays local
    ITERATIONS = 20_000


def _pbkdf2(pw: bytes, salt: bytes, iterations: int) -> bytes:
    """hashlib.pbkdf2_hmac, with a pure-Python fallback where OpenSSL is unavailable (browser build)."""
    if hasattr(hashlib, "pbkdf2_hmac"):
        return hashlib.pbkdf2_hmac("sha256", pw, salt, iterations)
    inner, outer = hashlib.sha256(), hashlib.sha256()
    key = pw if len(pw) <= 64 else hashlib.sha256(pw).digest()
    key = key.ljust(64, b"\0")
    inner.update(bytes(k ^ 0x36 for k in key))
    outer.update(bytes(k ^ 0x5C for k in key))

    def prf(msg):
        i, o = inner.copy(), outer.copy()
        i.update(msg)
        o.update(i.digest())
        return o.digest()

    u = prf(salt + b"\x00\x00\x00\x01")
    acc = int.from_bytes(u, "big")
    for _ in range(iterations - 1):
        u = prf(u)
        acc ^= int.from_bytes(u, "big")
    return acc.to_bytes(32, "big")
SESSION_DAYS = 14
# none < view < add (view + create new records) < edit (full: create, change, void, delete)
LEVELS = ("none", "view", "add", "edit")
# Special rights: every super administrator has them all; a super administrator gives them to other people one by one
# (administrators included — holding every permission does not include these)
GRANTS = ("design_dashboards", "required_fields", "arrange_forms", "build_calculators")


# The platform owner (BeyondERP itself): the only one who may change the in-app BeyondERP name, logo and icon.
# Known by a fingerprint (SHA-256) of their sign-in email, so the address is not written here.
OWNER_HASHES = {"925c7911981b1b6071e005d720ab441693cd63931a8378257b9d8835a8de5965"}


def platform_owner(u):
    if not u:
        return False
    for v in (u.get("email"), u.get("username")):
        if v and hashlib.sha256(str(v).strip().lower().encode()).hexdigest() in OWNER_HASHES:
            return True
    return False


def clean_grants(v):
    return sorted({g for g in (v or []) if g in GRANTS}) if isinstance(v, (list, tuple)) else []


def granted(user, right):
    """Super administrators: always; anyone else when a super administrator gave them this right."""
    return bool(user) and (bool(user.get("is_super")) or right in (user.get("grants") or []))


def hash_password(pw: str) -> str:
    if len(pw or "") < 8:
        raise ValidationError("Password must be at least 8 characters")
    salt = secrets.token_hex(16)
    dk = _pbkdf2(pw.encode("utf-8"), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, h = stored.split("$")
        dk = _pbkdf2((pw or "").encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), h)
    except (ValueError, AttributeError):
        return False


def clean_perms(perms):
    """Section levels (always present) + optional per-screen overrides ('section.screen'); 'inherit' = no override."""
    perms = perms or {}
    out = {m: (perms.get(m) if perms.get(m) in LEVELS else PERM_DEFAULTS.get(m, "none")) for m in MODULES}
    for new, old in PERM_FROM.items():      # a screen split off later keeps the level of the screen it came from
        if perms.get(new) not in LEVELS and perms.get(old) in LEVELS:
            perms = {**perms, new: perms[old]}
    for m, subs in SUBMODULES.items():
        for s in subs:
            v = perms.get(f"{m}.{s}")
            if v in LEVELS:
                out[f"{m}.{s}"] = v
            for a in ACTIONS:
                if perms.get(f"{m}.{s}!{a}") == "no":
                    out[f"{m}.{s}!{a}"] = "no"
        if not subs:
            for a in ACTIONS:
                if perms.get(f"{m}!{a}") == "no":
                    out[f"{m}!{a}"] = "no"
    for scr, tabs in SUBTABS.items():
        for tb in tabs:
            v = perms.get(f"{scr}.{tb}")
            if v in LEVELS:
                out[f"{scr}.{tb}"] = v
    if perms.get("_grid"):                 # saved from the permission grid (administrators then follow it)
        out["_grid"] = 1
    return out


def public_user(u):
    if not u:
        return None
    stored = json.loads(u["perms"] or "{}")
    # only super administrators skip the permissions; administrators follow them too — one whose permissions were never
    # set keeps full access (as administrators had before) until a super administrator saves theirs
    full = u.get("is_super") or (u["is_admin"] and not stored.get("_grid"))
    perms = {m: "edit" for m in MODULES} if full else clean_perms(stored)
    return {"id": u["id"], "username": u["username"], "full_name": u["full_name"], "email": u.get("email") or "",
            "is_admin": bool(u["is_admin"]), "is_super": bool(u.get("is_super")), "active": bool(u["active"]), "perms": perms,
            "group_key": u.get("group_key") or "", "needs_approval": bool(u.get("needs_approval")),
            "grants": list(GRANTS) if u.get("is_super") else clean_grants(json.loads(u.get("grants") or "[]")),
            "platform_owner": platform_owner(u)}


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_email(db, value, uid=None):
    """Optional email for a user: the address they sign in with on the shared workspace (Google or email)."""
    email = (value or "").strip().lower()
    if not email:
        return ""
    if not EMAIL_RE.match(email):
        raise ValidationError("Enter a valid email address")
    other = db.one("SELECT id FROM users WHERE lower(email)=? AND id<>?", (email, uid or 0))
    if other:
        raise ValidationError("Another user already has this email address")
    return email


def needs_setup(db):
    return db.one("SELECT COUNT(*) AS n FROM users")["n"] == 0


def create_user(db, d, first=False):
    username = text(d.get("username"), "username", required=True, maxlen=60)
    if db.one("SELECT 1 FROM users WHERE username=?", (username,)):
        raise ValidationError("Username already exists")
    is_super = 1 if (first or d.get("is_super")) else 0      # the first account owns the books
    is_admin = 1 if (is_super or d.get("is_admin")) else 0
    email = clean_email(db, d.get("email"))
    # "!" = no local password: the person signs in through the shared workspace (Supabase), or the password is
    # set on this device only (passwords are never sent to the workspace)
    pw = "!" if d.get("_no_password") and not d.get("password") else hash_password(d.get("password") or "")
    from .db import now
    uid = db.x("INSERT INTO users(username,full_name,pw_hash,is_admin,is_super,perms,created_at,email,group_key) VALUES (?,?,?,?,?,?,?,?,?)",
               (username, text(d.get("full_name")), pw, is_admin, is_super,
                json.dumps(clean_perms(d.get("perms"))), now().strftime("%Y-%m-%d %H:%M:%S"), email,
                _group(db, d.get("group_key")))).lastrowid
    if d.get("needs_approval") and not d.get("is_super"):
        db.x("UPDATE users SET needs_approval=1 WHERE id=?", (uid,))
    if d.get("grants"):                  # (the server lets only a super administrator send them)
        db.x("UPDATE users SET grants=? WHERE id=?", (json.dumps(clean_grants(d["grants"])), uid))
    return uid


def _group(db, key):
    """The dashboard group a person belongs to ('' = General)."""
    from . import dashboards
    dashboards.seed(db)
    key = text(key)
    return key if key and db.one("SELECT 1 FROM user_groups WHERE key=?", (key,)) else ""


def update_user(db, uid, d, acting_user_id):
    u = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise ValidationError("User not found")
    is_super = 1 if d.get("is_super", bool(u.get("is_super"))) else 0
    if "is_admin" in d and not d["is_admin"] and not d.get("is_super"):   # no longer an administrator: no longer super either
        is_super = 0
    is_admin = 1 if (is_super or d.get("is_admin", bool(u["is_admin"]))) else 0
    active = 1 if d.get("active", bool(u["active"])) else 0
    if u["is_admin"] and (not is_admin or not active):
        admins = db.one("SELECT COUNT(*) AS n FROM users WHERE is_admin=1 AND active=1")["n"]
        if admins <= 1:
            raise ValidationError("At least one active administrator must remain")
    if u.get("is_super") and (not is_super or not active):
        supers = db.one("SELECT COUNT(*) AS n FROM users WHERE is_super=1 AND active=1")["n"]
        if supers <= 1:
            raise ValidationError("At least one active super administrator must remain — make someone else super administrator first")
    if uid == acting_user_id and not active:
        raise ValidationError("You cannot deactivate yourself")
    email = clean_email(db, d.get("email", u.get("email")), uid)
    db.x("UPDATE users SET full_name=?, is_admin=?, is_super=?, active=?, perms=?, email=? WHERE id=?",
         (text(d.get("full_name", u["full_name"])), is_admin, is_super, active,
          json.dumps(clean_perms(d.get("perms") or json.loads(u["perms"] or "{}"))), email, uid))
    if "group_key" in d:
        db.x("UPDATE users SET group_key=? WHERE id=?", (_group(db, d.get("group_key")), uid))
    if "grants" in d:              # special rights (the server lets only a super administrator change them)
        db.x("UPDATE users SET grants=? WHERE id=?", (json.dumps(clean_grants(d["grants"])), uid))
    if "needs_approval" in d:      # their entries wait for someone to approve and post them
        db.x("UPDATE users SET needs_approval=? WHERE id=?", (1 if d["needs_approval"] and not is_super else 0, uid))
    if d.get("password"):
        db.x("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(d["password"]), uid))
        db.x("DELETE FROM sessions WHERE user_id=? ", (uid,))
    if not active:
        db.x("DELETE FROM sessions WHERE user_id=?", (uid,))


def delete_user(db, uid, acting_user_id):
    """Remove a user. Their name stays on everything they recorded (entries and the audit log keep the username)."""
    u = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise ValidationError("User not found")
    if uid == acting_user_id:
        raise ValidationError("You cannot delete yourself — ask another administrator")
    if u["is_admin"] and u["active"]:
        admins = db.one("SELECT COUNT(*) AS n FROM users WHERE is_admin=1 AND active=1")["n"]
        if admins <= 1:
            raise ValidationError("At least one active administrator must remain")
    if u.get("is_super") and u["active"]:
        if db.one("SELECT COUNT(*) AS n FROM users WHERE is_super=1 AND active=1")["n"] <= 1:
            raise ValidationError("At least one active super administrator must remain")
    db.x("DELETE FROM sessions WHERE user_id=?", (uid,))
    db.x("DELETE FROM users WHERE id=?", (uid,))
    return u


# Password guessing: after MAX_FAILS wrong passwords for one username within LOCK_MINUTES, that username is refused
# until the period passes (kept per running app; a restart clears it).
MAX_FAILS, LOCK_MINUTES = 5, 15
_fails = {}


def _throttle(key):
    import time
    now = time.time()
    recent = [t for t in _fails.get(key, []) if now - t < LOCK_MINUTES * 60]
    _fails[key] = recent
    if len(recent) >= MAX_FAILS:
        wait = int((LOCK_MINUTES * 60 - (now - recent[0])) // 60) + 1
        raise ValidationError(f"Too many wrong passwords — try again in {wait} minute{'s' if wait != 1 else ''}")


def login(db, username, password):
    import time
    key = str(username or "").strip().lower()
    _throttle(key)
    u = db.one("SELECT * FROM users WHERE username=? AND active=1", (text(username),))
    if not u or not check_password(password, u["pw_hash"]):
        _fails.setdefault(key, []).append(time.time())     # (not written to the shared audit log: it syncs to every device)
        raise ValidationError("Wrong username or password")
    _fails.pop(key, None)
    token = secrets.token_urlsafe(32)
    db.x("INSERT INTO sessions(token,user_id,expires) VALUES (?,?,datetime('now',?))",
         (token, u["id"], f"+{SESSION_DAYS} days"))
    return token, public_user(u)


def logout(db, token):
    db.x("DELETE FROM sessions WHERE token=?", (token,))


def user_for_token(db, token):
    if not token:
        return None
    u = db.one("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
               "WHERE s.token=? AND s.expires>datetime('now') AND u.active=1", (token,))
    return public_user(u)


# ------------------------------------------------------------------ API keys (outside tools, scripts, AI coding tools)
API_PREFIX = "berp_"


def create_api_key(db, user_id, name, read_only=True, by=""):
    """A key for one person: it acts with that person's permissions (read-only unless chosen otherwise). The full key is
    shown once; only its hash is kept."""
    name = text(name, "name", required=True, maxlen=80)
    if db.one("SELECT COUNT(*) n FROM api_keys WHERE user_id=? AND revoked_at IS NULL", (int(user_id),))["n"] >= 20:
        raise ValidationError("This person already has 20 keys — revoke some first")
    key = API_PREFIX + secrets.token_urlsafe(30)
    kid = db.x("INSERT INTO api_keys(user_id, name, key_hash, prefix, read_only, created_by, created_at) VALUES (?,?,?,?,?,?,datetime('now'))",
               (int(user_id), name, hashlib.sha256(key.encode()).hexdigest(), key[:12], 1 if read_only else 0, by)).lastrowid
    return kid, key


def user_for_api_key(db, key):
    if not key or not str(key).startswith(API_PREFIX):
        return None, None
    r = db.one("SELECT k.id kid, k.read_only, u.* FROM api_keys k JOIN users u ON u.id=k.user_id WHERE k.key_hash=? AND k.revoked_at IS NULL AND u.active=1",
               (hashlib.sha256(str(key).encode()).hexdigest(),))
    if not r:
        return None, None
    db.conn.execute("UPDATE api_keys SET last_used=datetime('now') WHERE id=?", (r["kid"],))
    return public_user({k: v for k, v in r.items() if k not in ("kid", "read_only")}), {"id": r["kid"], "read_only": bool(r["read_only"])}


def api_keys(db, user_id=None):
    w, p = ("WHERE k.user_id=?", [int(user_id)]) if user_id else ("", [])
    return db.q(f"SELECT k.id, k.name, k.prefix, k.read_only, k.created_at, k.created_by, k.last_used, k.revoked_at, u.username FROM api_keys k "
                f"JOIN users u ON u.id=k.user_id {w} ORDER BY k.revoked_at IS NOT NULL, k.id DESC", p)


def revoke_api_key(db, kid):
    db.x("UPDATE api_keys SET revoked_at=datetime('now') WHERE id=? AND revoked_at IS NULL", (int(kid),))


def level_of(user, key):
    """Effective level for 'section' or 'section.screen'. A section counts as granted when the section itself
    or any of its screens is granted (used for showing menus); screens fall back to their section."""
    if not user:
        return "none"
    if user.get("is_super"):
        return "edit"
    p = user["perms"]
    if key.count(".") >= 2:            # a tab inside a screen: its own level, else the screen's
        v = p.get(key)
        return v if v in LEVELS else level_of(user, key.rsplit(".", 1)[0])
    if "." in key:
        v = p.get(key)
        return v if v in LEVELS else p.get(key.split(".")[0], "none")
    best = p.get(key, "none")
    for k, v in p.items():
        if k.startswith(key + ".") and v in LEVELS and LEVELS.index(v) > LEVELS.index(best):
            best = v
    return best


def can(user, module, level="view"):
    """'a|b': either permission is enough (a screen two sections share)."""
    return any(LEVELS.index(level_of(user, m)) >= LEVELS.index(level) for m in str(module).split("|"))


def action_ok(user, module, action):
    """Whether an action (export / import / delete) is allowed on a screen. Off only where a super administrator
    switched it off ("section.screen!action": "no"); a tab follows its screen; a whole section allows it while any of
    its screens that this person can open allows it."""
    if not action or not module or not user or user.get("is_super"):
        return True
    if "|" in module:
        return any(action_ok(user, m, action) for m in module.split("|"))
    p = user.get("perms") or {}
    parts = module.split(".")
    if len(parts) >= 2:
        return p.get(f"{parts[0]}.{parts[1]}!{action}") != "no"
    subs = SUBMODULES.get(module) or []
    if not subs:
        return p.get(f"{module}!{action}") != "no"
    return any(p.get(f"{module}.{s}!{action}") != "no" and level_of(user, f"{module}.{s}") != "none" for s in subs)
