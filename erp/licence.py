"""Licences for the offline (desktop / self-hosted) edition.

A licence is a short text the vendor gives the customer: what was bought (edition, users, companies, modules), who for,
which installation, and until when updates are included. It is signed with the vendor's private Ed25519 key; the app
only holds the public key, so it can check a licence without the internet and nobody can make one up.

Licensing is switched on by the vendor: it applies once a public key is present (erp/licence_public.txt, written by
`python run.py licence-keygen`). Until then the app runs free, exactly as before. With licensing on:
  * a new installation runs a 15-day trial;
  * a valid licence unlocks it: users and companies up to what was bought, updates released while maintenance runs;
  * otherwise the books are read-only — everything can still be viewed, printed and exported, nothing is deleted.
The browser (hosted) edition is governed by the cloud subscription instead and never checks a licence.
"""
import base64
import datetime as _dt
import hashlib
import json
import os
import secrets
from pathlib import Path

TRIAL_DAYS = 15
RELEASE_DATE = "2026-10-04"     # when this version was released: a licence covers versions released before its updates end
EDITIONS = {"starter": "Starter", "business": "Business", "pro": "Pro", "enterprise": "Enterprise"}
PUBLIC_KEY_FILE = Path(__file__).with_name("licence_public.txt")
PREFIX = "BEYOND1-"

# ------------------------------------------------------------------ Ed25519 (RFC 8032), pure Python
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _xrecover(y):
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = x * _I % _P
    if x % 2 != 0:
        x = _P - x
    return x


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_xrecover(_BY), _BY, 1, _xrecover(_BY) * _BY % _P)


def _add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    A = (y1 - x1) * (y2 - x2) % _P
    B = (y1 + x1) * (y2 + x2) % _P
    C = t1 * 2 * _D * t2 % _P
    D = z1 * 2 * z2 % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _enc(p):
    x, y, z, _t = p
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _dec(s):
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        raise ValueError("bad point")
    x = _xrecover(y)
    if (x * x * -1 + y * y - 1 - _D * x * x * y * y) % _P:
        raise ValueError("bad point")
    if (x & 1) != sign:
        x = _P - x
    return (x, y, 1, x * y % _P)


def _h(m):
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def _expand(seed):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_from_private(seed):
    a, _ = _expand(seed)
    return _enc(_mul(a, _B))


def sign(seed, msg):
    a, prefix = _expand(seed)
    A = _enc(_mul(a, _B))
    r = _h(prefix + msg) % _L
    R = _enc(_mul(r, _B))
    k = _h(R + A + msg) % _L
    return R + int.to_bytes((r + k * a) % _L, 32, "little")


def verify(pub, msg, sig):
    if len(sig) != 64 or len(pub) != 32:
        return False
    try:
        R, A = _dec(sig[:32]), _dec(pub)
    except ValueError:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _L:
        return False
    k = _h(sig[:32] + pub + msg) % _L
    return _enc(_mul(s, _B)) == _enc(_add(R, _mul(k, A)))


# ------------------------------------------------------------------ licence text
def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(private_seed, payload):
    """The licence text for a payload (vendor side)."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()
    return PREFIX + _b64(body) + "." + _b64(sign(private_seed, body))


def parse(text, public_key=None):
    """(payload, problem). problem is '' for a genuine licence."""
    t = "".join(str(text or "").split())
    if not t.startswith(PREFIX) or "." not in t:
        return None, "This is not a BeyondERP licence key"
    body_s, sig_s = t[len(PREFIX):].rsplit(".", 1)
    try:
        body, sig = _unb64(body_s), _unb64(sig_s)
        payload = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return None, "The licence key is damaged — copy it again exactly as you received it"
    pub = public_key or vendor_key()
    if not pub or not verify(pub, body, sig):
        return None, "The licence key is not genuine (or was changed)"
    return payload, ""


def vendor_key():
    env = os.environ.get("BEYOND_LICENCE_PUBLIC_KEY", "").strip()
    raw = env or (PUBLIC_KEY_FILE.read_text().strip() if PUBLIC_KEY_FILE.exists() else "")
    raw = "".join(x for x in raw.splitlines() if x and not x.startswith("#")).strip()
    try:
        return bytes.fromhex(raw) if raw else None
    except ValueError:
        return None


def keygen(folder):
    """A new vendor key pair: the private key stays in `folder` (keep it safe and backed up); the public key goes into
    the app (erp/licence_public.txt)."""
    folder = Path(folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    priv = folder / "beyond-licence-private.key"
    if priv.exists():
        raise FileExistsError(f"{priv} already exists — keep using it (a new key would make every licence sold so far invalid)")
    seed = secrets.token_bytes(32)
    priv.write_text(seed.hex() + "\n")
    try:
        os.chmod(priv, 0o600)
    except OSError:
        pass
    pub = public_from_private(seed).hex()
    PUBLIC_KEY_FILE.write_text("# BEYOND ERP licence public key (safe to share; the private key never leaves the vendor)\n" + pub + "\n")
    return priv, pub


# ------------------------------------------------------------------ the installation
def install_id(db):
    """A code for this installation (customers send it when buying, so the licence works on this installation only)."""
    v = db.setting("install_id")
    if not v:
        v = "-".join(secrets.token_hex(2).upper() for _ in range(4))
        db.set_setting("install_id", v)
    return v


def _today():
    return _dt.date.today().isoformat()


def status(db, active=None):
    """Where this installation stands. can_write = False: view / print / export only."""
    on = vendor_key() is not None if active is None else active
    iid = install_id(db)
    if not on:
        return {"enabled": False, "state": "free", "can_write": True, "install_id": iid}
    first = db.setting("first_run")
    if not first:
        first = _today()
        db.set_setting("first_run", first)
    trial_end = (_dt.date.fromisoformat(first) + _dt.timedelta(days=TRIAL_DAYS)).isoformat()
    out = {"enabled": True, "install_id": iid, "release_date": RELEASE_DATE, "trial_ends": trial_end}
    text = db.setting("licence_key")
    payload, problem = parse(text) if text else (None, "")
    users = db.one("SELECT COUNT(*) n FROM users WHERE active=1")["n"]
    if payload:
        exp = payload.get("expires") or ""               # term licences (rental of the desktop edition) only
        upd = payload.get("updates_until") or ""
        if payload.get("install") and payload["install"] != iid:
            problem = f"This licence is for installation {payload['install']}, not this one ({iid})"
        elif exp and exp < _today():
            problem = f"The licence ended on {exp}"
        elif upd and upd < RELEASE_DATE:
            problem = (f"This version was released on {RELEASE_DATE}, after the licence's updates ended ({upd}). "
                       "Renew the yearly maintenance, or reinstall a version released before that date.")
        if not problem:
            over = users > int(payload.get("users") or 1)
            return {**out, "state": "licensed", "can_write": True, "licence": payload, "users": users,
                    "over_users": over, "updates_until": upd, "expires": exp,
                    "warning": (f"{users} active users — the licence covers {payload.get('users')}. Deactivate someone or buy more users."
                                if over else "")}
        out.update(licence=payload)
    days = (_dt.date.fromisoformat(trial_end) - _dt.date.fromisoformat(_today())).days
    if days > 0 and not payload:
        return {**out, "state": "trial", "can_write": True, "days_left": days, "problem": problem}
    return {**out, "state": "unlicensed", "can_write": False, "problem": problem or "The 15-day trial has ended", "days_left": 0}


def activate(db, text):
    payload, problem = parse(text)
    if problem:
        raise ValueError(problem)
    iid = install_id(db)
    if payload.get("install") and payload["install"] != iid:
        raise ValueError(f"This licence is for installation {payload['install']}, not this one ({iid}). Ask for a licence for {iid}.")
    db.set_setting("licence_key", "".join(str(text).split()))
    return status(db)
