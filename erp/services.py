"""Service subscriptions sold to customers: maintenance contracts (an apartment, a villa), extended warranty /
insurance on a device, software support or extra modules (add-ons) — anything billed every month, quarter or year.

* A plan is the catalogue entry (price per period, visits included, revenue account).
* A subscription is one customer's contract: what it covers (a unit, a device serial, an app), price, period,
  term and renewal. Add-ons hang under a main subscription and are billed with it.
* Billing posts a normal sales invoice (so it prints, e-invoices and ages like any other). A period longer than a
  month is booked to deferred revenue and earned month by month.
* Visits (maintenance calls) are logged per subscription; visits beyond what the plan includes can carry a charge
  that goes on the next invoice."""
import datetime as _dt

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

KINDS = ("maintenance", "warranty", "insurance", "software", "support", "addon", "other")
STATUSES = ("active", "paused", "cancelled", "ended")
SERVICE_BU = "SVC"


def add_months(d, n):
    d = _dt.date.fromisoformat(d) if isinstance(d, str) else d
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return d.replace(year=y, month=m, day=min(d.day, last)).isoformat()


def _next(db, table, prefix):
    n = 1
    for r in db.q(f"SELECT code FROM {table} WHERE code LIKE ?", (prefix + "%",)):
        tail = r["code"][len(prefix):]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"{prefix}{n:04d}"


# ------------------------------------------------------------------ plans
def save_plan(db, d, pid=None):
    kind = d.get("kind") or "maintenance"
    if kind not in KINDS:
        raise ValidationError("Type must be one of: " + ", ".join(KINDS))
    period = int(num(d.get("period_months") or 1, "period (months)", allow_zero=False))
    if period not in (1, 2, 3, 4, 6, 12, 24, 36):
        raise ValidationError("Bill every 1, 2, 3, 4, 6, 12, 24 or 36 months")
    rev = acc.account_code(db, d.get("revenue_account") or db.acct("service_revenue"), types=("Revenue",), field="revenue account")
    vat = acc.tax_code(db, d.get("vat_code"), "vat")[0] if d.get("vat_code") else ""
    vals = (text(d.get("name"), "plan name", required=True, maxlen=200), text(d.get("name_ar"), maxlen=200), kind,
            to_minor(num(d.get("price"), "price")), period, int(num(d.get("visits") or 0, "visits included")),
            to_minor(num(d.get("extra_visit_price") or 0, "extra visit price")), rev, vat,
            text(d.get("description"), maxlen=4000), 0 if d.get("active") in (False, 0, "0", "No", "no") else 1)
    cols = "name,name_ar,kind,price,period_months,visits,extra_visit_price,revenue_account,vat_code,description,active"
    with db.tx():
        if pid:
            plan = get_plan(db, pid)
            db.conn.execute(f"UPDATE service_plans SET {', '.join(c + '=?' for c in cols.split(','))} WHERE id=?", vals + (plan["id"],))
            return plan["id"]
        code = text(d.get("code")) or _next(db, "service_plans", "SP-")
        if db.one("SELECT 1 FROM service_plans WHERE code=?", (code,)):
            raise ValidationError(f"Plan {code} already exists")
        return db.conn.execute(f"INSERT INTO service_plans(code,{cols}) VALUES (?{',?' * len(vals)})", (code,) + vals).lastrowid


def get_plan(db, pid):
    p = db.one("SELECT * FROM service_plans WHERE id=? OR code=?", (int(pid) if str(pid).isdigit() else -1, str(pid)))
    if not p:
        raise ValidationError(f"Plan not found: {pid}")
    return p


def plans(db):
    out = []
    for p in db.q("SELECT p.*, (SELECT COUNT(*) FROM subscriptions s WHERE s.plan_id=p.id AND s.status='active') AS active_subs "
                  "FROM service_plans p ORDER BY p.active DESC, p.name"):
        out.append({**p, "price": to_major(p["price"]), "extra_visit_price": to_major(p["extra_visit_price"]),
                    "monthly": round(to_major(p["price"]) / p["period_months"], 2)})
    return out


# ------------------------------------------------------------------ subscriptions
def save(db, d, user="", sid=None):
    """Create or change a subscription. A change of price or period applies from the next invoice."""
    plan = get_plan(db, d.get("plan_id") or d.get("plan")) if d.get("plan_id") or d.get("plan") else None
    customer = text(d.get("customer"), "customer", required=True)
    period = int(num(d.get("period_months") or (plan["period_months"] if plan else 1), "period (months)", allow_zero=False))
    price = to_minor(num(d.get("price") if d.get("price") not in (None, "") else (to_major(plan["price"]) if plan else None), "price"))
    if price <= 0:
        raise ValidationError("Enter the price per period")
    parent = None
    if d.get("parent") or d.get("parent_id"):
        parent = get(db, d.get("parent_id") or d.get("parent"))
        if parent["parent_id"]:
            raise ValidationError("Add-ons go under a main subscription, not under another add-on")
    bu = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")) or (parent["bu_id"] if parent else None)
    start = acc.clean_date(d.get("start") or (parent["next_bill"] if parent and parent["status"] == "active" else None), "start date")
    vat = d.get("vat_code") if d.get("vat_code") is not None else (plan["vat_code"] if plan else "")
    vat = acc.tax_code(db, vat, "vat")[0] if vat else ""
    wht = acc.tax_code(db, d.get("wht_code"), "wht")[0] if d.get("wht_code") else ""
    vals = dict(customer=customer, plan_id=plan["id"] if plan else None, parent_id=parent["id"] if parent else None,
                title=text(d.get("title"), maxlen=200) or (plan["name"] if plan else "Service"),
                covers=text(d.get("covers"), maxlen=500), sale_code=text(d.get("sale_code"), maxlen=40),
                bu_id=bu, period_months=period, price=price, vat_code=vat, wht_code=wht,
                term_months=int(num(d.get("term_months") or 0, "term (months)")),
                auto_renew=0 if d.get("auto_renew") in (False, 0, "0", "No", "no") else 1,
                notes=text(d.get("notes"), maxlen=4000))
    with db.tx():
        if sid:
            s = get(db, sid)
            db.conn.execute(f"UPDATE subscriptions SET {', '.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [s["id"]])
            return s["id"]
        code = text(d.get("code")) or _next(db, "subscriptions", "SUB-")
        if db.one("SELECT 1 FROM subscriptions WHERE code=?", (code,)):
            raise ValidationError(f"Subscription {code} already exists")
        return db.conn.execute(f"INSERT INTO subscriptions(code, start, next_bill, status, created_by, {', '.join(vals)}) "
                               f"VALUES (?,?,?,'active',?{',?' * len(vals)})",
                               [code, start, start, user] + list(vals.values())).lastrowid


def get(db, sid):
    s = db.one("SELECT s.*, p.code AS plan_code, p.name AS plan_name, p.kind, p.visits AS plan_visits, p.extra_visit_price, "
               "p.revenue_account, u.code AS bu_code, par.code AS parent_code FROM subscriptions s "
               "LEFT JOIN service_plans p ON p.id=s.plan_id LEFT JOIN business_units u ON u.id=s.bu_id "
               "LEFT JOIN subscriptions par ON par.id=s.parent_id WHERE s.id=? OR s.code=?",
               (int(sid) if str(sid).isdigit() else -1, str(sid)))
    if not s:
        raise ValidationError(f"Subscription not found: {sid}")
    return s


def _end(s):
    """The last day covered when the contract has a fixed term that does not renew, else ''."""
    if s["term_months"] and not s["auto_renew"]:
        return (_dt.date.fromisoformat(add_months(s["start"], s["term_months"])) - _dt.timedelta(days=1)).isoformat()
    return ""


def _current_period(db, s):
    r = db.one("SELECT period_from, period_to FROM sub_invoices WHERE sub_id=? ORDER BY period_from DESC LIMIT 1", (s["id"],))
    return (r["period_from"], r["period_to"]) if r else (s["start"], add_months(s["start"], s["period_months"]))


def detail(db, sid):
    s = get(db, sid)
    out = _row(db, s)
    out["invoices"] = [{**r, "amount": to_major(r["amount"])} for r in db.q(
        "SELECT i.*, x.number, x.status AS invoice_status, x.entry_id FROM sub_invoices i JOIN extracts x ON x.id=i.extract_id "
        "WHERE i.sub_id=? ORDER BY i.period_from DESC", (s["id"],))]
    out["visits"] = [{**v, "cost": to_major(v["cost"]), "charge": to_major(v["charge"])}
                     for v in db.q("SELECT * FROM sub_visits WHERE sub_id=? ORDER BY date DESC, id DESC", (s["id"],))]
    out["addons"] = [_row(db, a) for a in db.q("SELECT id FROM subscriptions WHERE parent_id=? ORDER BY id", (s["id"],))
                     for a in [get(db, a["id"])]]
    deferred = db.one("SELECT COALESCE(SUM(amount),0) n FROM sub_recognitions WHERE sub_id=? AND entry_id IS NULL", (s["id"],))["n"]
    out["deferred"] = to_major(deferred)
    p = db.one("SELECT phone, whatsapp, email FROM parties WHERE name=?", (s["customer"],))
    out["contact"] = p or {}
    return out


def _row(db, s):
    pf, pt = _current_period(db, s)
    used = db.one("SELECT COUNT(*) n FROM sub_visits WHERE sub_id=? AND status='done' AND date>=? AND date<?", (s["id"], pf, pt))["n"]
    end = _end(s)
    renew_soon = bool(end and s["status"] == "active" and end <= add_months(acc.today(), 1))
    unbilled = db.one("SELECT COALESCE(SUM(charge),0) n FROM sub_visits WHERE sub_id=? AND status='done' AND charge>0 AND extract_id IS NULL",
                      (s["id"],))["n"]
    return {**s, "price": to_major(s["price"]), "monthly": round(to_major(s["price"]) / s["period_months"], 2) if s["status"] == "active" else 0,
            "end": end, "renew_soon": renew_soon, "due": s["status"] == "active" and s["next_bill"] <= acc.today() and (not end or s["next_bill"] <= end),
            "visits_used": used, "visits_included": s["plan_visits"] or 0, "period_from": pf, "period_to": pt,
            "unbilled_charges": to_major(unbilled), "extra_visit_price": to_major(s["extra_visit_price"] or 0)}


def listing(db, q=None, status=None, kind=None, customer=None):
    w, p = ["1=1"], []
    if status == "due":
        w.append("s.status='active' AND s.next_bill<=?")
        p.append(acc.today())
    elif status:
        w.append("s.status=?")
        p.append(status)
    if kind:
        w.append("p.kind=?")
        p.append(kind)
    if customer:
        w.append("s.customer=?")
        p.append(customer)
    if q:
        w.append("(s.code LIKE ? OR s.customer LIKE ? OR s.title LIKE ? OR s.covers LIKE ?)")
        p += [f"%{q}%"] * 4
    rows = [_row(db, get(db, r["id"])) for r in db.q(
        f"SELECT s.id FROM subscriptions s LEFT JOIN service_plans p ON p.id=s.plan_id WHERE {' AND '.join(w)} "
        "ORDER BY s.status='active' DESC, s.next_bill, s.id", p)]
    live = [r for r in db.q("SELECT id FROM subscriptions WHERE status='active'")]
    mrr = round(sum(_row(db, get(db, r["id"]))["monthly"] for r in live), 2)
    return {"rows": rows, "mrr": mrr, "arr": round(mrr * 12, 2), "active": len(live),
            "due": db.one("SELECT COUNT(*) n FROM subscriptions WHERE status='active' AND next_bill<=?", (acc.today(),))["n"],
            "renew_soon": sum(1 for r in rows if r["renew_soon"]),
            "deferred": to_major(db.one("SELECT COALESCE(SUM(amount),0) n FROM sub_recognitions WHERE entry_id IS NULL")["n"]),
            "next_code": _next(db, "subscriptions", "SUB-")}


def set_status(db, sid, status, d=None, user=""):
    """pause / resume (active) / cancel (now or at the end of the paid period)."""
    s = get(db, sid)
    d = d or {}
    if status == "active":
        if s["status"] not in ("paused", "cancelled", "ended"):
            raise ValidationError("Only a paused, cancelled or ended subscription can be resumed")
        nb = max(s["next_bill"], acc.clean_date(d.get("from") or acc.today()))
        db.x("UPDATE subscriptions SET status='active', next_bill=?, cancel_at='' WHERE id=?", (nb, s["id"]))
    elif status == "paused":
        if s["status"] != "active":
            raise ValidationError("Only an active subscription can be paused")
        db.x("UPDATE subscriptions SET status='paused' WHERE id=?", (s["id"],))
    elif status == "cancelled":
        if s["status"] in ("cancelled", "ended"):
            raise ValidationError("Already stopped")
        if d.get("at_period_end"):
            _pf, pt = _current_period(db, s)
            db.x("UPDATE subscriptions SET cancel_at=? WHERE id=?", (pt, s["id"]))
        else:
            db.x("UPDATE subscriptions SET status='cancelled', cancel_at=? WHERE id=?", (acc.today(), s["id"]))
        for a in db.q("SELECT id FROM subscriptions WHERE parent_id=? AND status IN ('active','paused')", (s["id"],)):
            set_status(db, a["id"], "cancelled", d, user)
    else:
        raise ValidationError("Unknown status")


# ------------------------------------------------------------------ visits
def save_visit(db, sid, d, vid=None):
    s = get(db, sid)
    st = d.get("status") or "done"
    if st not in ("scheduled", "done", "cancelled"):
        raise ValidationError("Visit status must be scheduled, done or cancelled")
    charge = d.get("charge")
    if charge in (None, "") and st == "done" and s["plan_visits"]:
        pf, pt = _current_period(db, s)
        used = db.one("SELECT COUNT(*) n FROM sub_visits WHERE sub_id=? AND status='done' AND date>=? AND date<? AND id<>?",
                      (s["id"], pf, pt, int(vid or 0)))["n"]
        charge = to_major(s["extra_visit_price"] or 0) if used >= s["plan_visits"] else 0
    vals = (acc.clean_date(d.get("date")), st, text(d.get("technician"), maxlen=120), text(d.get("description"), "description", required=True, maxlen=2000),
            to_minor(num(d.get("cost") or 0, "cost")), to_minor(num(charge or 0, "charge")), text(d.get("notes"), maxlen=2000))
    with db.tx():
        if vid:
            v = db.one("SELECT * FROM sub_visits WHERE id=? AND sub_id=?", (int(vid), s["id"]))
            if not v:
                raise ValidationError("Visit not found")
            if v["extract_id"]:
                raise ValidationError("This visit's charge is already invoiced")
            db.conn.execute("UPDATE sub_visits SET date=?, status=?, technician=?, description=?, cost=?, charge=?, notes=? WHERE id=?",
                            vals + (v["id"],))
            return v["id"]
        return db.conn.execute("INSERT INTO sub_visits(sub_id,date,status,technician,description,cost,charge,notes) VALUES (?,?,?,?,?,?,?,?)",
                               (s["id"],) + vals).lastrowid


def delete_visit(db, sid, vid):
    v = db.one("SELECT * FROM sub_visits WHERE id=? AND sub_id=?", (int(vid), get(db, sid)["id"]))
    if not v:
        raise ValidationError("Visit not found")
    if v["extract_id"]:
        raise ValidationError("This visit's charge is already invoiced")
    db.x("DELETE FROM sub_visits WHERE id=?", (v["id"],))


# ------------------------------------------------------------------ billing
def _service_bu(db):
    bu = db.one("SELECT id FROM business_units WHERE code=?", (SERVICE_BU,))
    if bu:
        return bu["id"]
    return db.conn.execute("INSERT INTO business_units(code,name,type,status,basis) VALUES (?,?,?,?,?)",
                           (SERVICE_BU, "Maintenance, services & subscriptions", "Services", "In Progress", "Flexible / Settled")).lastrowid


def due(db, through=None):
    through = acc.clean_date(through or acc.today())
    out = []
    for s in db.q("SELECT id FROM subscriptions WHERE status='active' AND next_bill<=? ORDER BY next_bill, id", (through,)):
        s = get(db, s["id"])
        end = _end(s)
        if (end and s["next_bill"] > end) or (s["cancel_at"] and s["next_bill"] >= s["cancel_at"]):
            continue
        out.append(s)
    return out


def run(db, through=None, user="", post=True):
    """Bill every period that has started by `through` (one invoice per customer, business unit and taxes per date,
    add-ons with their main subscription), earn deferred revenue for every month started, close ended contracts."""
    through = acc.clean_date(through or acc.today())
    made = {"invoices": 0, "amount": 0, "recognized": 0, "ended": 0}
    with db.tx():
        for _ in range(240):   # catch up month by month (a contract left unbilled for a while)
            batch = due(db, through)
            if not batch:
                break
            groups = {}
            for s in batch:
                key = (s["customer"], s["bu_id"] or 0, s["vat_code"], s["wht_code"], s["next_bill"])
                groups.setdefault(key, []).append(s)
            for (customer, bu_id, vat, wht, date), subs in groups.items():
                made["amount"] += _bill(db, subs, date, user, post)
                made["invoices"] += 1
        made["recognized"] = recognize(db, through, user)
        for s in db.q("SELECT id FROM subscriptions WHERE status='active'"):
            s = get(db, s["id"])
            end = _end(s)
            if (end and s["next_bill"] > end and end < through) or (s["cancel_at"] and s["cancel_at"] <= through and s["next_bill"] >= s["cancel_at"]):
                db.conn.execute("UPDATE subscriptions SET status=? WHERE id=?", ("cancelled" if s["cancel_at"] else "ended", s["id"]))
                made["ended"] += 1
                if post and db.one("SELECT 1 FROM sub_visits WHERE sub_id=? AND status='done' AND charge>0 AND extract_id IS NULL", (s["id"],)):
                    bill_charges(db, s["id"], min(through, end or s["cancel_at"] or through), user)
    made["amount"] = to_major(made["amount"])
    return made


def _bill(db, subs, date, user, post):
    first = subs[0]
    bu = first["bu_id"] or _service_bu(db)
    n = db.one("SELECT COUNT(*) n FROM sub_invoices WHERE sub_id=?", (first["id"],))["n"] + 1
    number = f"{first['code']}-{n:02d}"
    while db.one("SELECT 1 FROM extracts WHERE number=?", (number,)):
        n += 1
        number = f"{first['code']}-{n:02d}"
    lines, per_sub, total = [], [], 0
    for s in subs:
        pf = s["next_bill"]
        pt = add_months(pf, s["period_months"])
        last = (_dt.date.fromisoformat(pt) - _dt.timedelta(days=1)).isoformat()
        rev = s["revenue_account"] or db.acct("service_revenue")
        account = rev if s["period_months"] == 1 else db.acct("service_deferred")
        lines.append({"description": f"{s['title']}{' — ' + s['covers'] if s['covers'] else ''} ({pf} → {last})",
                      "unit": f"{s['period_months']} mo" if s["period_months"] > 1 else "month", "qty": 1,
                      "price": to_major(s["price"]), "account": account})
        for v in db.q("SELECT * FROM sub_visits WHERE sub_id=? AND status='done' AND charge>0 AND extract_id IS NULL AND date<=?",
                      (s["id"], date)):
            lines.append({"description": f"Extra visit {v['date']} — {v['description']}"[:300], "unit": "visit", "qty": 1,
                          "price": to_major(v["charge"]), "account": rev, "visit_id": v["id"]})
        per_sub.append((s, pf, pt, rev))
        total += s["price"]
    xid = acc.save_extract(db, {"bu_id": bu, "number": number, "date": date, "customer": first["customer"],
                                "vat_code": first["vat_code"], "wht_code": first["wht_code"], "lines": lines})
    for ln in lines:
        if ln.get("visit_id"):
            db.conn.execute("UPDATE sub_visits SET extract_id=? WHERE id=?", (xid, ln["visit_id"]))
            total += to_minor(ln["price"])
    for s, pf, pt, rev in per_sub:
        iid = db.conn.execute("INSERT INTO sub_invoices(sub_id,extract_id,period_from,period_to,amount) VALUES (?,?,?,?,?)",
                              (s["id"], xid, pf, pt, s["price"])).lastrowid
        if s["period_months"] > 1:   # earned evenly, a month at a time
            each = s["price"] // s["period_months"]
            for k in range(s["period_months"]):
                amt = each if k < s["period_months"] - 1 else s["price"] - each * (s["period_months"] - 1)
                db.conn.execute("INSERT INTO sub_recognitions(sub_id,invoice_id,month,amount,account,bu_id) VALUES (?,?,?,?,?,?)",
                                (s["id"], iid, add_months(pf, k), amt, rev, bu))
        db.conn.execute("UPDATE subscriptions SET next_bill=? WHERE id=?", (pt, s["id"]))
    if post:
        eid = acc.post_extract(db, xid, user=user, revenue_account=per_sub[0][3])
        import json
        e = db.one("SELECT meta FROM journal_entries WHERE id=?", (eid,))
        meta = {**json.loads(e["meta"] or "{}"), "subscription": [s["code"] for s, *_x in per_sub]}
        db.conn.execute("UPDATE journal_entries SET meta=? WHERE id=?", (json.dumps(meta), eid))
    return total


def bill_charges(db, sid, date=None, user=""):
    """Invoice extra-visit charges now instead of with the next period (or when the contract has ended)."""
    s = get(db, sid)
    date = acc.clean_date(date or acc.today())
    visits = db.q("SELECT * FROM sub_visits WHERE sub_id=? AND status='done' AND charge>0 AND extract_id IS NULL ORDER BY date", (s["id"],))
    if not visits:
        raise ValidationError("No charges waiting to be invoiced")
    with db.tx():
        bu = s["bu_id"] or _service_bu(db)
        n = db.one("SELECT COUNT(*) n FROM extracts WHERE number LIKE ?", (s["code"] + "-X%",))["n"] + 1
        number = f"{s['code']}-X{n:02d}"
        while db.one("SELECT 1 FROM extracts WHERE number=?", (number,)):
            n += 1
            number = f"{s['code']}-X{n:02d}"
        rev = s["revenue_account"] or db.acct("service_revenue")
        xid = acc.save_extract(db, {"bu_id": bu, "number": number, "date": date, "customer": s["customer"], "vat_code": s["vat_code"],
                                    "wht_code": s["wht_code"], "lines": [{"description": f"Extra visit {v['date']} — {v['description']}"[:300],
                                                                          "unit": "visit", "qty": 1, "price": to_major(v["charge"]),
                                                                          "account": rev} for v in visits]})
        for v in visits:
            db.conn.execute("UPDATE sub_visits SET extract_id=? WHERE id=?", (xid, v["id"]))
        acc.post_extract(db, xid, user=user, revenue_account=rev)
    return xid


def recognize(db, through, user=""):
    """Move deferred revenue to income for every month that has started (one entry per subscription per month)."""
    n = 0
    for r in db.q("SELECT r.*, s.code, s.customer, s.title FROM sub_recognitions r JOIN subscriptions s ON s.id=r.sub_id "
                  "JOIN sub_invoices i ON i.id=r.invoice_id JOIN extracts x ON x.id=i.extract_id "
                  "WHERE r.entry_id IS NULL AND r.month<=? AND x.status='posted' ORDER BY r.month, r.id", (through,)):
        eid = acc._insert_entry(db, date=r["month"], kind="Revenue Recognition", memo=f"{r['code']} — {r['title']} earned {r['month'][:7]}",
                                bu_id=r["bu_id"], party=r["customer"], ref=r["code"],
                                lines=[{"account": db.acct("service_deferred"), "debit": to_major(r["amount"])},
                                       {"account": r["account"], "credit": to_major(r["amount"])}],
                                meta={"subscription": r["code"]}, user=user)
        db.conn.execute("UPDATE sub_recognitions SET entry_id=? WHERE id=?", (eid, r["id"]))
        n += 1
    return n


# ------------------------------------------------------------------ backups
TABLES = ("service_plans", "subscriptions", "sub_invoices", "sub_recognitions", "sub_visits")


def export_rows(db):
    out = {t: db.q(f"SELECT * FROM {t} ORDER BY id") for t in TABLES}
    xs = {x["id"]: x["number"] for x in db.q("SELECT id, number FROM extracts")}
    bus = {b["id"]: b["code"] for b in db.q("SELECT id, code FROM business_units")}
    ents = {}
    for t in ("sub_invoices", "sub_visits"):
        for r in out[t]:
            r["extract_number"] = xs.get(r.get("extract_id"), "")
    for t in ("subscriptions", "sub_recognitions"):
        for r in out[t]:
            r["bu_code"] = bus.get(r.get("bu_id"), "")
    for r in out["sub_recognitions"]:
        if r["entry_id"]:
            e = db.one("SELECT date, memo FROM journal_entries WHERE id=?", (r["entry_id"],))
            ents[r["id"]] = e
            r["entry_key"] = [e["date"], e["memo"]] if e else None
    return out


def import_rows(db, data, counts):
    """Restore the subscription tables from a JSON backup; invoices, business units and entries matched by number / code / memo."""
    if not isinstance(data, dict):
        return
    for t in reversed(TABLES):
        db.conn.execute(f"DELETE FROM {t}")
    xs = {x["number"]: x["id"] for x in db.q("SELECT id, number FROM extracts")}
    bus = {b["code"]: b["id"] for b in db.q("SELECT id, code FROM business_units")}
    for t in TABLES:
        cols = [c[1] for c in db.conn.execute(f"PRAGMA table_info({t})")]
        for r in data.get(t) or []:
            r = dict(r)
            if "extract_number" in r:
                r["extract_id"] = xs.get(r["extract_number"])
            if "bu_code" in r:
                r["bu_id"] = bus.get(r["bu_code"])
            if t == "sub_recognitions":
                k = r.get("entry_key")
                e = db.one("SELECT id FROM journal_entries WHERE date=? AND memo=? AND kind='Revenue Recognition' AND status='posted'",
                           tuple(k)) if k else None
                r["entry_id"] = e["id"] if e else None
            keep = {c: r.get(c) for c in cols if c in r}
            db.conn.execute(f"INSERT INTO {t}({','.join(keep)}) VALUES ({','.join('?' * len(keep))})", list(keep.values()))
    counts["Subscriptions"] = len(data.get("subscriptions") or [])
