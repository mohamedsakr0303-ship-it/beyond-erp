"""Files kept with a record: the supplier's invoice behind an expense, a signed contract, a cheque photo, a delivery
note. Stored in the database (so backups and sync carry them), at most MAX_FILE each."""
import base64
import re

from .accounting import ValidationError, text

MAX_FILE = 8 * 1024 * 1024
# record type -> (table, natural key kept across backups, permissions that can see it)
OWNERS = {"entry": ("journal_entries", None, None), "party": ("parties", "name", ("purchasing.contacts", "sales.parties")),
          "bu": ("business_units", "code", ("business_units.units",)), "rental": ("rentals", "code", ("rentals.contracts",)),
          "sale": ("sales_orders", "code", ("shop.orders",)), "loan": ("loans", "code", ("accounting.loans",)),
          "custody": ("custodies", "code", ("purchasing.custody",)), "asset": ("fixed_assets", "code", ("assets.register",)),
          "offer": ("offers", "number", ("sales.offers",)), "po": ("purchase_orders", "code", ("purchasing.orders",)),
          "sub": ("subscriptions", "code", ("shop.subscriptions",)),
          "approval": ("approvals", None, None)}   # a draft waiting for approval: its files move to the entry once posted


def move(db, owner, oid, to_owner, to_id):
    """Hand a record's files to another record (an approved draft's files to the entry it became)."""
    db.conn.execute("UPDATE attachments SET owner=?, owner_id=? WHERE owner=? AND owner_id=?", (to_owner, int(to_id), owner, int(oid)))


def _owner(db, owner, oid):
    if owner not in OWNERS:
        raise ValidationError("Unknown record type")
    table, key, _p = OWNERS[owner]
    r = db.one(f"SELECT id FROM {table} WHERE id=?", (int(oid),)) if str(oid).isdigit() else None
    if not r and key:   # pages open loans, rentals, sales… by their code
        r = db.one(f"SELECT id FROM {table} WHERE {key}=?", (str(oid or ""),))
    if not r:
        raise ValidationError("Record not found")
    return r["id"]


def listing(db, owner, oid):
    oid = _owner(db, owner, oid)
    return db.q("SELECT id, title, mime, size, thumb, url, created_by, created_at FROM attachments WHERE owner=? AND owner_id=? "
                "ORDER BY id", (owner, oid))


def counts(db, owner, ids):
    """{record id: number of files} for a list of records (paper-clip marks in lists)."""
    ids = [int(i) for i in ids if str(i).isdigit()][:2000]
    if not ids:
        return {}
    return {r["owner_id"]: r["n"] for r in db.q(f"SELECT owner_id, COUNT(*) n FROM attachments WHERE owner=? AND owner_id IN "
                                                 f"({','.join('?' * len(ids))}) GROUP BY owner_id", [owner] + ids)}


def add(db, owner, oid, d, user=""):
    oid = _owner(db, owner, oid)
    url, data, mime, size = text(d.get("url"), maxlen=2000), "", text(d.get("mime"), maxlen=100), 0
    raw = d.get("data") or ""
    if raw:
        m = re.match(r"^data:([\w/+.\-]*);base64,(.*)$", raw, re.S)
        if m:
            mime, raw = m.group(1) or mime, m.group(2)
        try:
            size = len(base64.b64decode(raw, validate=False))
        except (ValueError, TypeError):
            raise ValidationError("The file could not be read")
        if size > MAX_FILE:
            raise ValidationError(f"The file is {size / 1048576:.1f} MB — the limit is {MAX_FILE // 1048576} MB. "
                                  "Shrink it (or save the photo smaller), or add a link to it instead.")
        data = raw
    elif not url:
        raise ValidationError("Choose a file or paste a link")
    elif not re.match(r"^https?://", url, re.I):
        raise ValidationError("A link must start with http:// or https://")
    thumb = d.get("thumb") or ""
    if thumb and (not str(thumb).startswith("data:image/") or len(thumb) > 200_000):
        thumb = ""
    title = text(d.get("title"), maxlen=200) or text(d.get("name"), maxlen=200) or ("Link" if url else "File")
    return db.x("INSERT INTO attachments(owner,owner_id,title,mime,data,thumb,url,size,created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                (owner, oid, title, mime, data, thumb, url, size, user)).lastrowid


def get(db, aid):
    a = db.one("SELECT * FROM attachments WHERE id=?", (int(aid),))
    if not a:
        raise ValidationError("File not found")
    return a


def rename(db, aid, title):
    get(db, aid)
    db.x("UPDATE attachments SET title=? WHERE id=?", (text(title, "title", required=True, maxlen=200), int(aid)))


def delete(db, aid):
    get(db, aid)
    db.x("DELETE FROM attachments WHERE id=?", (int(aid),))


def _fp(db, eid):
    e = db.one("SELECT date, kind, memo, party, (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) total "
               "FROM journal_entries e WHERE id=?", (eid,))
    return [e["date"], e["kind"], e["memo"] or "", e["party"] or "", int(e["total"] or 0)] if e else None


def export_rows(db):
    """For JSON backups. Restored records get new ids, so each file names its record by a natural key
    (a voucher by date, type, memo, party and amount; the others by their code / name)."""
    out = []
    for a in db.q("SELECT * FROM attachments ORDER BY id"):
        table, key, _p = OWNERS.get(a["owner"], (None, None, None))
        if not table:
            continue
        if a["owner"] == "entry":
            k = _fp(db, a["owner_id"])
        elif not key:               # files of a draft waiting for approval stay with that device's draft
            continue
        else:
            r = db.one(f"SELECT {key} AS k FROM {table} WHERE id=?", (a["owner_id"],))
            k = r["k"] if r else None
        if k is not None:
            out.append({f: a[f] for f in ("owner", "title", "mime", "data", "thumb", "url", "size", "created_by", "created_at")} | {"key": k})
    return out


def import_rows(db, rows):
    pool = {}
    if any(r.get("owner") == "entry" for r in rows):
        for e in db.q("SELECT id FROM journal_entries WHERE status='posted' ORDER BY id"):
            pool.setdefault(tuple(_fp(db, e["id"])), []).append(e["id"])
    n = 0
    for r in rows or []:
        table, key, _p = OWNERS.get(r.get("owner"), (None, None, None))
        if not table:
            continue
        if r["owner"] == "entry":
            ids = pool.get(tuple(r.get("key") or ()))
            oid = ids[0] if ids else None
        else:
            x = db.one(f"SELECT id FROM {table} WHERE {key}=?", (r.get("key"),))
            oid = x["id"] if x else None
        if oid and db.one("SELECT 1 FROM attachments WHERE owner=? AND owner_id=? AND title=? AND size=? AND url=?",
                          (r["owner"], oid, r.get("title") or "", int(r.get("size") or 0), r.get("url") or "")):
            continue                  # already there (the same file imported again)
        if oid:
            db.conn.execute("INSERT INTO attachments(owner,owner_id,title,mime,data,thumb,url,size,created_by,created_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)", (r["owner"], oid, r.get("title") or "", r.get("mime") or "", r.get("data") or "",
                                                            r.get("thumb") or "", r.get("url") or "", int(r.get("size") or 0),
                                                            r.get("created_by") or "", r.get("created_at") or ""))
            n += 1
    return n
