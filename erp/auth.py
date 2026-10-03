"""Users, password hashing (PBKDF2, stdlib) and per-module permissions."""
import hashlib
import hmac
import json
import re
import os
import secrets

from .accounting import ValidationError, text
from .db import MODULES, SUBMODULES

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
    out = {m: (perms.get(m) if perms.get(m) in LEVELS else "none") for m in MODULES}
    for m, subs in SUBMODULES.items():
        for s in subs:
            v = perms.get(f"{m}.{s}")
            if v in LEVELS:
                out[f"{m}.{s}"] = v
    return out


def public_user(u):
    if not u:
        return None
    perms = {m: "edit" for m in MODULES} if u["is_admin"] else clean_perms(json.loads(u["perms"] or "{}"))
    return {"id": u["id"], "username": u["username"], "full_name": u["full_name"], "email": u.get("email") or "",
            "is_admin": bool(u["is_admin"]), "is_super": bool(u.get("is_super")), "active": bool(u["active"]), "perms": perms}


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
    uid = db.x("INSERT INTO users(username,full_name,pw_hash,is_admin,is_super,perms,created_at,email) VALUES (?,?,?,?,?,?,?,?)",
               (username, text(d.get("full_name")), pw, is_admin, is_super,
                json.dumps(clean_perms(d.get("perms"))), now().strftime("%Y-%m-%d %H:%M:%S"), email)).lastrowid
    return uid


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


def login(db, username, password):
    u = db.one("SELECT * FROM users WHERE username=? AND active=1", (text(username),))
    if not u or not check_password(password, u["pw_hash"]):
        raise ValidationError("Wrong username or password")
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


def level_of(user, key):
    """Effective level for 'section' or 'section.screen'. A section counts as granted when the section itself
    or any of its screens is granted (used for showing menus); screens fall back to their section."""
    if not user:
        return "none"
    if user["is_admin"]:
        return "edit"
    p = user["perms"]
    if "." in key:
        v = p.get(key)
        return v if v in LEVELS else p.get(key.split(".")[0], "none")
    best = p.get(key, "none")
    for k, v in p.items():
        if k.startswith(key + ".") and LEVELS.index(v) > LEVELS.index(best):
            best = v
    return best


def can(user, module, level="view"):
    return LEVELS.index(level_of(user, module)) >= LEVELS.index(level)
