"""Company subscriptions & recurring bills: what the company itself pays for every week / month / quarter / year
(software, internet, services, rent…).

Each one knows its next due date and how its cost is carried:
  * shared   — shared overhead: no project; spread over the projects by their overhead % (like other overheads)
  * company  — a company cost kept OUT of the project allocation (it stays on the company's own P&L)
  * project  — charged to one project
  * split    — charged to several projects by percentage
"Record payment" books it as an operating expense (one per project in a split) and moves the due date on by one
cycle. A reminder goes to the chosen people N days before it is due, at the time they chose — in the bell, and on
their phones / computers even with the app closed (shared workspace)."""
import calendar
import json

from . import accounting as acc
from .accounting import ValidationError, now_iso, text, to_major, to_minor

_dt = acc._dt
CYCLES = ("weekly", "monthly", "quarterly", "yearly")
ALLOC = ("shared", "company", "project", "split")
CYCLE_LABEL = {"weekly": ("week", "أسبوع"), "monthly": ("month", "شهر"), "quarterly": ("quarter", "ربع سنة"), "yearly": ("year", "سنة")}


def _add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def next_after(day, cycle, anchor=None):
    """The due date one cycle after `day` (a month after the 31st is the month's last day, then back to the 31st)."""
    d = _dt.date.fromisoformat(day)
    if cycle == "weekly":
        return (d + _dt.timedelta(days=7)).isoformat()
    n = {"monthly": 1, "quarterly": 3, "yearly": 12}[cycle]
    out = _add_months(d, n)
    if anchor:                                   # keep the original day of the month when the month allows it
        a = int(anchor)
        out = out.replace(day=min(a, calendar.monthrange(out.year, out.month)[1]))
    return out.isoformat()


def per_month(amount, cycle):
    return round(amount * {"weekly": 52 / 12, "monthly": 1, "quarterly": 1 / 3, "yearly": 1 / 12}[cycle], 2)


def _clean(db, d):
    name = text(d.get("name"), "name", required=True, maxlen=120)
    cycle = d.get("cycle") if d.get("cycle") in CYCLES else "monthly"
    amount = acc.num(d.get("amount"), "amount", allow_zero=False)
    due = acc.clean_date(d.get("next_due"))
    account = acc.account_code(db, d.get("account") or "6020", types=("Expense",), field="expense account")
    alloc = d.get("alloc") if d.get("alloc") in ALLOC else "shared"
    bu_id, split = None, []
    if alloc == "project":
        bu_id = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
    elif alloc == "split":
        for s in d.get("split") or []:
            if not isinstance(s, dict) or not (s.get("bu_code") or s.get("bu_id")):
                continue
            b = acc.bu_id_or_none(db, s.get("bu_id") or s.get("bu_code"), required=True)
            code = db.one("SELECT code FROM business_units WHERE id=?", (b,))["code"]
            pct = acc.num(s.get("pct"), "percentage")
            if pct > 0:
                split.append({"bu_code": code, "pct": round(pct, 4)})
        if len(split) < 2:
            raise ValidationError("Split between at least two projects")
        if abs(sum(s["pct"] for s in split) - 100) > 0.01:
            raise ValidationError(f"The split adds up to {sum(s['pct'] for s in split):g}% — it must be 100%")
    to = [u for u in (d.get("remind_to") or []) if isinstance(u, str) and db.one("SELECT 1 FROM users WHERE username=?", (u,))]
    rtime = text(d.get("remind_time"), maxlen=5) or "09:00"
    if not acc.re.match(r"^\d{1,2}:\d{2}$", rtime):
        raise ValidationError("Reminder time looks like 09:00")
    days = int(acc.num(d.get("remind_days") if d.get("remind_days") not in (None, "") else 3, "days before"))
    if not 0 <= days <= 60:
        raise ValidationError("Remind between 0 and 60 days before")
    return {"name": name, "vendor": text(d.get("vendor"), maxlen=120), "plan": text(d.get("plan"), maxlen=120), "cycle": cycle,
            "amount": to_minor(amount), "currency_note": text(d.get("currency_note"), maxlen=40), "next_due": due,
            "account": account, "alloc": alloc, "bu_id": bu_id, "split": json.dumps(split), "paid_from": text(d.get("paid_from"), maxlen=20),
            "method": text(d.get("method"), maxlen=40), "remind_days": days, "remind_time": rtime, "remind_to": json.dumps(to),
            "active": 0 if d.get("active") in (False, 0, "0") else 1, "notes": text(d.get("notes"), maxlen=1000)}


def save(db, d, user, sid=None):
    v = _clean(db, d)
    if not json.loads(v["remind_to"]):
        v["remind_to"] = json.dumps([user])           # nobody chosen: the person setting it up
    if sid:
        if not db.one("SELECT 1 FROM cost_subs WHERE id=?", (int(sid),)):
            raise ValidationError("Not found")
        db.x(f"UPDATE cost_subs SET {', '.join(k + '=?' for k in v)}, updated_at=? WHERE id=?", [*v.values(), now_iso(), int(sid)])
        return int(sid)
    v["anchor_day"] = int(v["next_due"][8:10])
    return db.x(f"INSERT INTO cost_subs({', '.join(v)}, created_by, created_at) VALUES ({', '.join('?' * len(v))}, ?, ?)",
                [*v.values(), user, now_iso()]).lastrowid


def delete(db, sid):
    db.x("DELETE FROM cost_subs WHERE id=?", (int(sid),))


def _row(db, r, today):
    r = dict(r)
    r["amount"] = to_major(r["amount"])
    r["split"] = json.loads(r["split"] or "[]")
    r["remind_to"] = json.loads(r["remind_to"] or "[]")
    r["bu_code"] = (db.one("SELECT code FROM business_units WHERE id=?", (r["bu_id"],)) or {}).get("code", "") if r["bu_id"] else ""
    r["days_left"] = (_dt.date.fromisoformat(r["next_due"]) - _dt.date.fromisoformat(today)).days
    r["state"] = "off" if not r["active"] else "overdue" if r["days_left"] < 0 else "due" if r["days_left"] <= r["remind_days"] else "ok"
    r["per_month"] = per_month(r["amount"], r["cycle"])
    r["remind_on"] = (_dt.date.fromisoformat(r["next_due"]) - _dt.timedelta(days=r["remind_days"])).isoformat()
    return r


def listing(db):
    today = acc.today()
    rows = [_row(db, r, today) for r in db.q("SELECT * FROM cost_subs ORDER BY active DESC, next_due, name")]
    live = [r for r in rows if r["active"]]
    return {"rows": rows, "per_month": round(sum(r["per_month"] for r in live), 2), "per_year": round(sum(r["per_month"] for r in live) * 12, 2),
            "due_soon": [r for r in live if r["state"] in ("due", "overdue")]}


def get(db, sid):
    r = db.one("SELECT * FROM cost_subs WHERE id=?", (int(sid),))
    if not r:
        raise ValidationError("Not found")
    out = _row(db, r, acc.today())
    out["payments"] = [{"id": e["id"], "date": e["date"], "memo": e["memo"], "bu_code": e["bu_code"] or "", "amount": to_major(e["amount"])}
                       for e in db.q("""SELECT e.id, e.date, e.memo, b.code bu_code, (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) amount
                                        FROM journal_entries e LEFT JOIN business_units b ON b.id=e.bu_id
                                        WHERE e.status='posted' AND json_extract(e.meta,'$.bill')=? ORDER BY e.date DESC, e.id DESC LIMIT 60""", (int(sid),))]
    return out


def pay(db, sid, d, user=""):
    """Book one payment as operating expense(s) and move the due date on by one cycle."""
    s = db.one("SELECT * FROM cost_subs WHERE id=?", (int(sid),))
    if not s:
        raise ValidationError("Not found")
    amount = to_minor(acc.num(d.get("amount") if d.get("amount") not in (None, "") else to_major(s["amount"]), "amount", allow_zero=False))
    date = acc.clean_date(d.get("date") or acc.today())
    period = s["next_due"]
    memo = f"{s['name']}" + (f" — {s['plan']}" if s["plan"] else "") + f" ({CYCLE_LABEL[s['cycle']][0]} due {period})"
    parts = []
    if s["alloc"] == "split":
        sp = json.loads(s["split"] or "[]")
        left = amount
        for i, x in enumerate(sp):
            share = left if i == len(sp) - 1 else int(round(amount * x["pct"] / 100))
            left -= share
            parts.append((x["bu_code"], share))
    else:
        parts.append(((db.one("SELECT code FROM business_units WHERE id=?", (s["bu_id"],)) or {}).get("code") if s["alloc"] == "project" else None, amount))
    ids = []
    with db.tx():
        for bu_code, share in parts:
            if share <= 0:
                continue
            eid = acc.record_expense(db, {"description": memo, "amount": to_major(share), "account": s["account"], "date": date,
                                          "bu_code": bu_code, "vendor": s["vendor"], "method": d.get("method") or s["method"],
                                          "paid_from": d.get("paid_from") or s["paid_from"] or None}, user=user)
            meta = json.loads(db.one("SELECT meta FROM journal_entries WHERE id=?", (eid,))["meta"] or "{}")
            meta.update({"bill": int(sid), "bill_period": period, **({"no_alloc": 1} if s["alloc"] == "company" else {})})
            db.x("UPDATE journal_entries SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), eid))
            ids.append(eid)
        nxt = next_after(period, s["cycle"], s["anchor_day"])
        db.x("UPDATE cost_subs SET next_due=?, last_paid=?, last_amount=?, amount=CASE WHEN ? THEN ? ELSE amount END, updated_at=? WHERE id=?",
             (nxt, date, amount, 1 if d.get("update_amount") else 0, amount, now_iso(), int(sid)))
    return {"entries": ids, "next_due": nxt}


def reminder(db, sid):
    """The push reminder for a subscription's next due date (or a cancel when it is off)."""
    s = db.one("SELECT * FROM cost_subs WHERE id=?", (int(sid),))
    key = f"bill:{sid}"
    if not s or not s["active"]:
        return [{"cancel": key}]
    r = _row(db, s, acc.today())
    day = max(r["remind_on"], acc.today())
    return [{"kind": "notify", "key": key, "day": day, "time": r["remind_time"], "to": r["remind_to"], "pref": "reminders",
             "title": f"{r['name']} is due {'today' if r['next_due'] == day else 'on ' + r['next_due']}",
             "body": f"{r['amount']:,.2f}" + (f" ({r['currency_note']})" if r["currency_note"] else "") + (f" · {r['vendor']}" if r["vendor"] else ""),
             "href": f"#/bills/{sid}", "tag": f"bill{sid}"}]


def due_for(db, username):
    """Subscriptions in their reminder window that this person is reminded of (the bell)."""
    return [r for r in listing(db)["due_soon"] if username in r["remind_to"]]


def export_rows(db):
    return [{k: r[k] for k in r.keys() if k != "id"} | {"bu_code": (db.one("SELECT code FROM business_units WHERE id=?", (r["bu_id"],)) or {}).get("code", "")}
            for r in db.q("SELECT * FROM cost_subs ORDER BY id")]


def import_rows(db, rows, replace=False):
    if replace:
        db.x("DELETE FROM cost_subs")
    cols = {r["name"] for r in db.q("PRAGMA table_info(cost_subs)")} - {"id"}
    n = 0
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("name") or db.one("SELECT 1 FROM cost_subs WHERE name=? AND COALESCE(vendor,'')=?", (r["name"], r.get("vendor") or "")):
            continue
        v = {k: r[k] for k in r if k in cols}
        v["bu_id"] = (db.one("SELECT id FROM business_units WHERE code=?", (r.get("bu_code") or "",)) or {}).get("id")
        db.x(f"INSERT INTO cost_subs({', '.join(v)}) VALUES ({', '.join('?' * len(v))})", list(v.values()))
        n += 1
    return n
