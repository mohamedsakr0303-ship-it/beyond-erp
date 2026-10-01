"""Recurring entries: rent, salaries, subscriptions, loan installments, depreciation… posted on a schedule.

A template is any voucher the app can post (operating expense, direct cost, payroll, payment, receipt, partner
drawing, journal voucher, one of the company's own entry types, or a depreciation run) with its usual form values,
plus a schedule: every N days / weeks / months / years from a start date (optionally the last day of the month),
until an end date or a number of times.

  auto    — posts itself when it falls due (the app runs due templates when it opens; nothing is posted twice:
            each occurrence is recorded once, so a second device or a replay finds it done)
  review  — waits in "Due" for someone to check the amount (electricity, variable bills) and post or skip it

Text fields may use {month} (2026-10), {year} and {date} — "Office rent {month}".
Posted vouchers carry meta {"recurring": id, "due": date} and open in their normal form for corrections.
"""
import calendar
import datetime as _dt
import json

from . import accounting as acc
from .accounting import ValidationError, num, text

UNITS = ("day", "week", "month", "year")
DOC_TYPES = {  # type -> (function, permission)
    "expense": (acc.record_expense, "purchasing.expenses"),
    "direct_cost": (acc.record_project_cost, "purchasing.costs"),
    "payroll": (acc.record_payroll, "payroll"),
    "payment": (acc.record_payment, "purchasing.payments"),
    "receipt": (acc.record_collection, "sales.receipts"),
    "partner_tx": (acc.record_partner_tx, "partners.transactions"),
    "journal": (acc.record_journal, "accounting.journal"),
}
AMOUNT_FIELD = {"expense": "amount", "direct_cost": "rate", "payroll": "rate", "payment": "amount", "receipt": "amount",
                "partner_tx": "amount"}
MAX_RUN = 120


def _j(v, default):
    try:
        return json.loads(v) if isinstance(v, str) and v else (v if v is not None else default)
    except ValueError:
        return default


def module_of(doc_type):
    if doc_type == "depreciation":
        return "assets.depreciation"
    if doc_type.startswith("entry:"):
        return "accounting.journal"
    if doc_type not in DOC_TYPES:
        raise ValidationError("Unknown voucher type")
    return DOC_TYPES[doc_type][1]


def _add(d, unit, n, month_end=False):
    d = _dt.date.fromisoformat(d)
    if unit == "day":
        return (d + _dt.timedelta(days=n)).isoformat()
    if unit == "week":
        return (d + _dt.timedelta(weeks=n)).isoformat()
    months = n * (12 if unit == "year" else 1)
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    last = calendar.monthrange(y, m)[1]
    return _dt.date(y, m, last if month_end else min(d.day, last)).isoformat()


def occurrences(t, through, limit=1000):
    """Due dates of a template up to a date (inclusive)."""
    out = []
    for k in range(limit):
        if t["max_count"] and k >= t["max_count"]:
            break
        d = _add(t["start"], t["unit"], k * t["every"], bool(t["month_end"]))
        if d > through or (t["end_date"] and d > t["end_date"]):
            break
        out.append(d)
    return out


def get(db, rid):
    t = db.one("SELECT * FROM recurring WHERE id=?", (int(rid),))
    if not t:
        raise ValidationError("Recurring entry not found")
    return t


def _done(db, rid):
    return {r["due"]: r for r in db.q("SELECT * FROM recurring_runs WHERE template_id=?", (rid,))}


def next_due(db, t):
    done = _done(db, t["id"])
    for d in occurrences(t, "9999-12-31", 5000):
        if d not in done:
            return d
    return None


def save(db, d, rid=None):
    old = get(db, rid) if rid else None
    doc = text(d.get("doc_type")) or (old and old["doc_type"])
    module_of(doc)
    if doc.startswith("entry:"):
        from . import entries
        if not any(x["id"] == doc[6:] for x in entries.load(db)):
            raise ValidationError("That entry type no longer exists")
    unit = d.get("unit") or (old and old["unit"]) or "month"
    if unit not in UNITS:
        raise ValidationError("Repeat every day, week, month or year")
    every = int(num(d.get("every") or (old and old["every"]) or 1, "repeat every"))
    if not 1 <= every <= 120:
        raise ValidationError("Repeat every 1–120")
    start = acc.clean_date(d.get("start") or (old and old["start"]), "first date")
    end = acc.clean_date(d["end_date"], "end date") if d.get("end_date") else ""
    if end and end < start:
        raise ValidationError("The end date is before the first date")
    count = int(num(d.get("max_count") or 0, "number of times"))
    mode = d.get("mode") if d.get("mode") in ("auto", "review") else (old["mode"] if old else "review")
    payload = d.get("payload") if isinstance(d.get("payload"), dict) else (_j(old["payload"], {}) if old else {})
    payload = {k: v for k, v in payload.items() if k != "date"}
    name = text(d.get("name"), "name", required=True, maxlen=120)
    vals = dict(name=name, doc_type=doc, payload=json.dumps(payload, ensure_ascii=False), unit=unit, every=every, start=start,
                end_date=end, max_count=count, month_end=1 if d.get("month_end") in (True, 1, "1", "true", "on") else 0,
                mode=mode, active=0 if d.get("active") in (False, 0, "0", "false") else 1, last_error="", notes=text(d.get("notes"), maxlen=2000))
    with db.tx():
        if old:
            db.conn.execute(f"UPDATE recurring SET {','.join(k + '=?' for k in vals)} WHERE id=?", (*vals.values(), old["id"]))
            return old["id"]
        return db.conn.execute(f"INSERT INTO recurring({','.join(vals)}) VALUES ({','.join('?' * len(vals))})", tuple(vals.values())).lastrowid


def delete(db, rid):
    t = get(db, rid)
    with db.tx():
        db.conn.execute("DELETE FROM recurring_runs WHERE template_id=?", (t["id"],))
        db.conn.execute("DELETE FROM recurring WHERE id=?", (t["id"],))


def _fill(v, due):
    if isinstance(v, str):
        return v.replace("{month}", due[:7]).replace("{year}", due[:4]).replace("{date}", due)
    if isinstance(v, list):
        return [_fill(x, due) for x in v]
    if isinstance(v, dict):
        return {k: _fill(x, due) for k, x in v.items()}
    return v


def _post_one(db, t, due, overrides, user):
    body = _fill({**_j(t["payload"], {}), **(overrides or {})}, due)
    body["date"] = due
    doc = t["doc_type"]
    if doc == "depreciation":
        from . import assets
        try:
            r = assets.run_depreciation(db, due[:7], user=user)
            eids = r["entries"]
        except ValidationError as e:
            if "Nothing to depreciate" not in str(e):
                raise
            eids = []
    elif doc.startswith("entry:"):
        from . import entries
        eids = [entries.post(db, doc[6:], body, user)]
    else:
        fn = DOC_TYPES[doc][0]
        eids = [fn(db, body, user)]
    for eid in eids:
        if not isinstance(eid, int):
            continue
        e = db.one("SELECT meta FROM journal_entries WHERE id=?", (eid,))
        m = _j(e["meta"], {})
        m.update(recurring=t["id"], due=due)
        if doc in DOC_TYPES and doc != "journal":
            m["form"] = {k: v for k, v in body.items() if isinstance(v, (str, int, float, bool)) or v is None}
        db.conn.execute("UPDATE journal_entries SET meta=? WHERE id=?", (json.dumps(m, ensure_ascii=False), eid))
    return eids


def post(db, rid, due, overrides=None, user=""):
    """Post one occurrence (the one due, or an earlier one not posted yet)."""
    t = get(db, rid)
    due = acc.clean_date(due, "due date")
    if due not in occurrences(t, due):
        raise ValidationError(f"{due} is not one of this entry's dates")
    if due in _done(db, t["id"]):
        raise ValidationError(f"{due} is already posted or skipped")
    with db.tx():
        eids = _post_one(db, t, due, overrides, user)
        db.conn.execute("INSERT INTO recurring_runs(template_id,due,skipped,entries) VALUES (?,?,0,?)",
                        (t["id"], due, len(eids)))
        db.conn.execute("UPDATE recurring SET last_error='' WHERE id=?", (t["id"],))
    return eids


def skip(db, rid, due):
    t = get(db, rid)
    due = acc.clean_date(due, "due date")
    if due not in occurrences(t, due) or due in _done(db, t["id"]):
        raise ValidationError("Nothing to skip on that date")
    db.x("INSERT INTO recurring_runs(template_id,due,skipped,entries) VALUES (?,?,1,0)", (t["id"], due))


def due_list(db, through=None, can=None):
    """Every occurrence up to a date that is neither posted nor skipped."""
    through = through or acc.today()
    out = []
    for t in db.q("SELECT * FROM recurring WHERE active=1 ORDER BY name"):
        if can and not can(module_of(t["doc_type"])):
            continue
        done = _done(db, t["id"])
        for d in occurrences(t, through):
            if d not in done:
                out.append({"id": t["id"], "name": t["name"], "doc_type": t["doc_type"], "mode": t["mode"], "due": d,
                            "amount": _amount(t), "last_error": t["last_error"]})
    out.sort(key=lambda r: (r["due"], r["name"]))
    return out


def run(db, through, user="", can=None):
    """Post every automatic occurrence due up to a date. A failing template stops (and says why) until it is fixed."""
    through = acc.clean_date(through, "run up to")
    posted, errors, failed = 0, [], set()
    for item in due_list(db, through, can):
        if item["mode"] != "auto" or item["last_error"] or item["id"] in failed or posted >= MAX_RUN:
            continue
        try:
            post(db, item["id"], item["due"], None, user)
            posted += 1
        except (ValidationError, LookupError) as e:
            db.x("UPDATE recurring SET last_error=? WHERE id=?", (f"{item['due']}: {e}"[:500], item["id"]))
            failed.add(item["id"])
            errors.append({"id": item["id"], "name": item["name"], "due": item["due"], "error": str(e)})
    return {"posted": posted, "errors": errors}


def _amount(t):
    p = _j(t["payload"], {})
    if t["doc_type"] == "journal":
        return round(sum(float(x.get("debit") or 0) for x in p.get("lines") or []), 2)
    f = AMOUNT_FIELD.get(t["doc_type"])
    try:
        v = float(p.get(f) or 0)
        if t["doc_type"] in ("direct_cost", "payroll"):
            v *= float(p.get("qty") or 1)
        return round(v, 2)
    except (TypeError, ValueError):
        return 0


def schedule_text(t):
    n, u = t["every"], t["unit"]
    return (f"every {n} {u}s" if n > 1 else f"every {u}") + (" (month end)" if t["month_end"] else "")


def listing(db):
    rows = []
    for t in db.q("SELECT * FROM recurring ORDER BY active DESC, name"):
        done = _done(db, t["id"])
        posted = [d for d, r in done.items() if not r["skipped"]]
        rows.append({**t, "payload": _j(t["payload"], {}), "amount": _amount(t), "next_due": next_due(db, t) if t["active"] else None,
                     "posted": len(posted), "last_posted": max(posted) if posted else None, "schedule": schedule_text(t)})
    return rows


def history(db, rid):
    t = get(db, rid)
    runs = db.q("SELECT * FROM recurring_runs WHERE template_id=? ORDER BY due DESC", (t["id"],))
    ents = {}
    for e in db.q("SELECT id, date, memo, meta FROM journal_entries WHERE status='posted' AND meta LIKE ?", (f'%"recurring": {t["id"]}%',)):
        m = _j(e["meta"], {})
        if m.get("recurring") == t["id"]:
            ents.setdefault(m.get("due"), []).append({"id": e["id"], "date": e["date"], "memo": e["memo"]})
    return [{**r, "vouchers": ents.get(r["due"], [])} for r in runs]


# ------------------------------------------------------------------- backup
TABLES = ("recurring", "recurring_runs")


def export_rows(db):
    return {t: db.q(f"SELECT * FROM {t} ORDER BY id") for t in TABLES}


def import_rows(db, data, counts):
    if not isinstance(data, dict):
        return
    for t in reversed(TABLES):
        db.conn.execute(f"DELETE FROM {t}")
    for t in TABLES:
        rows = data.get(t) or []
        cols = [c[1] for c in db.conn.execute(f"PRAGMA table_info({t})")]
        for r in rows:
            if isinstance(r, dict):
                keep = [c for c in cols if c in r]
                db.conn.execute(f"INSERT INTO {t}({','.join(keep)}) VALUES ({','.join('?' * len(keep))})", [r[c] for c in keep])
        if rows:
            counts[t] = len(rows)
