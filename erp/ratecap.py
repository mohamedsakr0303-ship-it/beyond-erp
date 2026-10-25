"""New rate capture: prices and costs seen in the books that the price list does not have yet.

Selling prices come from invoices (extract lines); costs come from direct costs booked with a
quantity and unit cost, and from materials booked on journal entries. Lines are matched to the price list on
description + unit (case, spacing and punctuation ignored). Nothing is added
automatically: the list only suggests, a person reviews and adds.
"""
import json
import re

from .db import to_major, to_minor

IGNORE_KEY = "ratecap_ignore"


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(s or "").lower())).strip()


def key(description, unit):
    return norm(description) + "|" + norm(unit)


def ignored(db):
    try:
        v = json.loads(db.setting(IGNORE_KEY) or "[]")
        return set(v) if isinstance(v, list) else set()
    except ValueError:
        return set()


def set_ignored(db, k, on=True):
    s = ignored(db)
    (s.add if on else s.discard)(k)
    db.set_setting(IGNORE_KEY, json.dumps(sorted(s), ensure_ascii=False))


def _seen(db):
    """(kind, description, unit, category, rate_minor, date, source) for every priced line in the books. BOQs are
    estimates, not the books, so they are not read; nor are rent lines (priced on the rental contract)."""
    out = []
    # rent and its one-time charges are priced on the rental contract, not from the price list
    rent = {r["code"] for r in db.q("SELECT code FROM accounts WHERE role IN ('rental_revenue','rental_services','rental_transport','rental_compensation')")}
    for r in db.q("SELECT l.description, l.unit, l.category, l.price, l.account, x.date, x.number, x.status "
                  "FROM extract_lines l JOIN extracts x ON x.id=l.extract_id WHERE l.price>0"):
        if r["account"] in rent:
            continue
        out.append(("price", r["description"], r["unit"], r["category"], r["price"], r["date"] or "",
                    f"Invoice {r['number']}" + (" (draft)" if r["status"] == "draft" else "")))
    for r in db.q("SELECT e.memo, e.date, e.ref, json_extract(e.meta,'$.unit') AS unit, json_extract(e.meta,'$.rate') AS rate "
                  "FROM journal_entries e WHERE e.status='posted' AND e.kind='Direct Cost' AND json_extract(e.meta,'$.rate')>0 "
                  "AND NOT EXISTS (SELECT 1 FROM material_lines m WHERE m.entry_id=e.id)"):
        out.append(("cost", r["memo"], r["unit"], "", to_minor(r["rate"]), r["date"] or "", f"Entry {r['ref'] or ''}".strip()))
    for r in db.q("SELECT m.description, m.unit, m.rate, m.date, e.ref FROM material_lines m "
                  "JOIN journal_entries e ON e.id=m.entry_id WHERE e.status='posted' AND m.rate>0"):
        out.append(("cost", r["description"], r["unit"], "", r["rate"], r["date"] or "",
                    f"Entry {r['ref'] or ''}".strip()))
    return out


def candidates(db, include_ignored=False):
    known = {key(r["description"], r["unit"]) for r in db.q("SELECT description, unit FROM rates")}
    skip = ignored(db)
    found = {}
    for kind, desc, unit, cat, rate, date, src in _seen(db):
        if not norm(desc) or not norm(unit):   # no unit: a lump sum, not a rate
            continue
        k = key(desc, unit)
        if k in known or (k in skip and not include_ignored):
            continue
        c = found.setdefault(k, {"key": k, "description": desc.strip(), "unit": (unit or "").strip(), "category": "",
                                 "price": None, "cost": None, "ignored": k in skip})
        if cat and not c["category"]:
            c["category"] = cat
        s = c[kind]
        if s is None:
            s = c[kind] = {"last": 0, "min": rate, "max": rate, "count": 0, "last_date": "", "source": ""}
        s["count"] += 1
        s["min"], s["max"] = min(s["min"], rate), max(s["max"], rate)
        if date >= s["last_date"]:
            s["last"], s["last_date"], s["source"] = rate, date, src
    rows = []
    for c in found.values():
        for kind in ("price", "cost"):
            if c[kind]:
                for f in ("last", "min", "max"):
                    c[kind][f] = to_major(c[kind][f])
        c["last_date"] = max((c[k]["last_date"] for k in ("price", "cost") if c[k]), default="")
        c["seen"] = sum(c[k]["count"] for k in ("price", "cost") if c[k])
        rows.append(c)
    rows.sort(key=lambda c: (c["last_date"], c["seen"]), reverse=True)
    return rows


def add_many(db, keys, category=""):
    """Add several captured rates to the price list at once. Each keeps the cost and / or client price it was seen at;
    a price that was never seen stays blank (0) rather than being made up. Rows with no category take the one given."""
    want = set(keys)
    rows = [c for c in candidates(db, include_ignored=True) if c["key"] in want]
    missing = [c["description"] for c in rows if not (c["category"] or category)]
    if missing:
        raise ValueError("category")
    n = 0
    for c in rows:
        cost = c["cost"]["last"] if c["cost"] else 0
        price = c["price"]["last"] if c["price"] else 0
        markup = round(price / cost - 1, 6) if cost and price else 0
        src = " · ".join(f"{x['source']} {x['last_date']}".strip() for x in (c["price"], c["cost"]) if x)
        db.x("INSERT INTO rates(category,item_no,sub,description,unit,cost,markup,price,notes) VALUES (?,?,?,?,?,?,?,?,?)",
             (c["category"] or category, "", "", c["description"], c["unit"], to_minor(cost), markup, to_minor(price),
              ("Captured from " + src)[:500]))
        n += 1
    return n
