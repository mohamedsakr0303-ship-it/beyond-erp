"""Equipment the company owns or runs (المعدات): register, working hours per project, fuel, maintenance plans and work.

Costing — each project pays for the machine time it used, and the machines' running costs are pooled:

  * Working hours on a project: hours × the machine's hourly rate
        Dr Own Equipment Used on Projects (5052, the project)   /   Cr Equipment Running Costs pool (6094)
  * Fuel put in on a project:      Dr 5052 (the project)  /  Cr cash / bank (or the supplier)
  * Maintenance, repairs, inspections:  Dr 6094 pool  /  Cr cash / bank (or the supplier)

So the pool carries maintenance (and depreciation, if booked there) less what projects were charged: a debit balance
means the hourly rates are too low, a credit balance that they recover more than the machines cost. The register
links a machine to its fixed asset (if it is one) for depreciation; rented-in machines are simply not linked.

Maintenance plans fall due every N days and/or every N working hours (from the hour meter = start reading + hours
logged); the dashboard warns a week / 10 % of the interval ahead.
"""
import datetime as _dt

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

STATUSES = ("available", "in_use", "maintenance", "retired")
LOG_KINDS = ("service", "repair", "inspection")


def _acct(db, role, fallback_role):
    try:
        return db.acct(role)
    except LookupError:
        return db.acct(fallback_role)


def next_code(db):
    n = 1
    for r in db.q("SELECT code FROM equipment WHERE code LIKE 'EQ-%'"):
        tail = r["code"][3:]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"EQ-{n:03d}"


def save(db, d, eq_id=None):
    name = text(d.get("name"), "name", required=True)
    asset = d.get("asset_id") or d.get("asset_code")
    asset_id = None
    if asset:
        a = db.one("SELECT id FROM fixed_assets WHERE id=? OR code=?", (asset, str(asset)))
        if not a:
            raise ValidationError(f"Unknown fixed asset {asset}")
        asset_id = a["id"]
    vals = dict(name=name, category=text(d.get("category")), serial=text(d.get("serial")), asset_id=asset_id,
                hourly_rate=to_minor(num(d.get("hourly_rate") or 0, "hourly rate")),
                meter_start=num(d.get("meter_start") or 0, "hour meter"),
                bu_id=acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")),
                status=d.get("status") if d.get("status") in STATUSES else "available", notes=text(d.get("notes"), maxlen=2000))
    if eq_id:
        e = get(db, eq_id)
        db.x(f"UPDATE equipment SET {', '.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [e["id"]])
        return e["id"]
    code = text(d.get("code")) or next_code(db)
    if db.one("SELECT 1 FROM equipment WHERE code=?", (code,)):
        raise ValidationError(f"Equipment {code} already exists")
    return db.x(f"INSERT INTO equipment(code, {', '.join(vals)}) VALUES (?, {', '.join('?' * len(vals))})",
                [code] + list(vals.values())).lastrowid


def get(db, eq_id, today=None):
    e = db.one("SELECT e.*, b.code AS bu_code, b.name AS bu_name, f.code AS asset_code FROM equipment e "
               "LEFT JOIN business_units b ON b.id=e.bu_id LEFT JOIN fixed_assets f ON f.id=e.asset_id WHERE e.id=? OR e.code=?",
               (int(eq_id) if str(eq_id).isdigit() else -1, str(eq_id)))
    if not e:
        raise ValidationError("Equipment not found")
    e["hourly_rate"] = to_major(e["hourly_rate"])
    u = db.one("SELECT COALESCE(SUM(hours),0) h, COALESCE(SUM(fuel_qty),0) fq, COALESCE(SUM(fuel_cost),0) fc FROM equipment_usage "
               "WHERE equipment_id=?", (e["id"],))
    e["hours"] = round(u["h"], 2)
    e["meter"] = round(e["meter_start"] + u["h"], 2)
    e["fuel_qty"], e["fuel_cost"] = round(u["fq"], 2), to_major(u["fc"])
    e["charged"] = round(e["hours"] * e["hourly_rate"], 2)
    e["maintenance_cost"] = to_major(db.one("SELECT COALESCE(SUM(cost),0) n FROM maintenance_logs WHERE equipment_id=?", (e["id"],))["n"])
    e["plans"] = plans(db, e, today)
    e["next_due"] = min((p["due_date"] for p in e["plans"] if p["due_date"]), default="")
    e["due"] = [p for p in e["plans"] if p["state"] in ("due", "overdue")]
    return e


def plans(db, e, today=None):
    today = today or acc.today()
    out = []
    meter = e.get("meter")
    if meter is None:
        meter = e["meter_start"] + db.one("SELECT COALESCE(SUM(hours),0) h FROM equipment_usage WHERE equipment_id=?", (e["id"],))["h"]
    for p in db.q("SELECT * FROM maintenance_plans WHERE equipment_id=? ORDER BY id", (e["id"],)):
        last = db.one("SELECT date, hours_at FROM maintenance_logs WHERE plan_id=? ORDER BY date DESC, id DESC LIMIT 1", (p["id"],))
        due_date = due_hours = None
        state = "ok"
        if p["every_days"]:
            base = (last or {}).get("date") or p["start_date"] or today
            due_date = (_dt.date.fromisoformat(base) + _dt.timedelta(days=p["every_days"])).isoformat()
            if due_date < today:
                state = "overdue"
            elif due_date <= (_dt.date.fromisoformat(today) + _dt.timedelta(days=7)).isoformat():
                state = "due"
        if p["every_hours"]:
            base = (last or {}).get("hours_at")
            due_hours = round((base if base is not None else e["meter_start"]) + p["every_hours"], 2)
            if meter >= due_hours:
                state = "overdue"
            elif meter >= due_hours - p["every_hours"] * 0.1 and state != "overdue":
                state = "due"
        out.append({**p, "last_date": (last or {}).get("date") or "", "last_hours": (last or {}).get("hours_at"),
                    "due_date": due_date or "", "due_hours": due_hours, "meter": round(meter, 2), "state": state})
    return out


def delete(db, eq_id):
    e = get(db, eq_id)
    if db.one("SELECT 1 FROM equipment_usage WHERE equipment_id=? AND entry_id IS NOT NULL UNION "
              "SELECT 1 FROM maintenance_logs WHERE equipment_id=? AND entry_id IS NOT NULL", (e["id"], e["id"])):
        raise ValidationError("This equipment has posted usage or maintenance. Retire it instead.")
    db.x("DELETE FROM equipment WHERE id=?", (e["id"],))


# ------------------------------------------------------------------ usage
def log_usage(db, d, user=""):
    e = get(db, d.get("equipment_id") or d.get("equipment"))
    bu = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
    hours = num(d.get("hours") or 0, "hours")
    fuel_qty = num(d.get("fuel_qty") or 0, "fuel quantity")
    fuel_cost = to_minor(num(d.get("fuel_cost") or 0, "fuel cost"))
    if not hours and not fuel_cost:
        raise ValidationError("Enter the hours worked and/or the fuel cost")
    date = acc.clean_date(d.get("date"))
    charge = to_minor(hours * e["hourly_rate"])
    used, pool = _acct(db, "equipment_charge", "materials"), _acct(db, "equipment_pool", "other_overhead")
    lines = []
    if charge:
        lines += [{"account": used, "debit": to_major(charge)}, {"account": pool, "credit": to_major(charge)}]
    if fuel_cost:
        vendor = text(d.get("vendor"))
        cr = ({"account": db.acct("ap"), "credit": to_major(fuel_cost), "party": vendor} if vendor and d.get("on_credit")
              else {"account": acc.money_account(db, d.get("method"), d.get("paid_from")), "credit": to_major(fuel_cost)})
        lines += [{"account": used, "debit": to_major(fuel_cost)}, cr]
    operator = text(d.get("operator"))
    with db.tx():
        eid = acc._insert_entry(db, date=date, kind="Equipment Usage", bu_id=bu, user=user, ref=e["code"],
                                memo=f"{e['name']} ({e['code']}) — {hours:g} h" + (f", fuel {fuel_qty:g}" if fuel_qty else "")
                                     + (f" · {operator}" if operator else ""),
                                lines=lines, meta={"equipment": e["code"], "hours": hours, "rate": e["hourly_rate"], "fuel_qty": fuel_qty})
        uid = db.conn.execute("INSERT INTO equipment_usage(equipment_id,date,bu_id,hours,operator,fuel_qty,fuel_cost,notes,entry_id) "
                              "VALUES (?,?,?,?,?,?,?,?,?)", (e["id"], date, bu, hours, operator, fuel_qty, fuel_cost,
                                                             text(d.get("notes"), maxlen=1000), eid)).lastrowid
        if e["status"] == "available" or e["bu_id"] != bu:
            db.conn.execute("UPDATE equipment SET bu_id=?, status=CASE WHEN status='available' THEN 'in_use' ELSE status END WHERE id=?",
                            (bu, e["id"]))
    return uid


def delete_usage(db, uid, user=""):
    u = db.one("SELECT * FROM equipment_usage WHERE id=?", (int(uid),))
    if not u:
        raise ValidationError("Usage line not found")
    with db.tx():
        if u["entry_id"] and db.one("SELECT 1 FROM journal_entries WHERE id=? AND status='posted'", (u["entry_id"],)):
            acc.void_entry(db, u["entry_id"], "Equipment usage deleted", user)
        db.conn.execute("DELETE FROM equipment_usage WHERE id=?", (u["id"],))


def usage(db, equipment_id=None, bu_id=None, date_from=None, date_to=None):
    w, p = ["1=1"], []
    for col, v in (("u.equipment_id", equipment_id), ("u.bu_id", bu_id)):
        if v:
            w.append(f"{col}=?")
            p.append(int(v))
    if date_from:
        w.append("u.date>=?")
        p.append(date_from)
    if date_to:
        w.append("u.date<=?")
        p.append(date_to)
    rows = db.q(f"SELECT u.*, e.code, e.name, e.hourly_rate, b.code AS bu_code FROM equipment_usage u JOIN equipment e ON e.id=u.equipment_id "
                f"LEFT JOIN business_units b ON b.id=u.bu_id WHERE {' AND '.join(w)} ORDER BY u.date DESC, u.id DESC", p)
    for r in rows:
        r["charge"] = round(r["hours"] * to_major(r["hourly_rate"]), 2)
        r["hourly_rate"] = to_major(r["hourly_rate"])
        r["fuel_cost"] = to_major(r["fuel_cost"])
    return rows


# ------------------------------------------------------------------ maintenance
def save_plan(db, d, plan_id=None):
    e = get(db, d.get("equipment_id") or d.get("equipment"))
    every_days = int(num(d.get("every_days") or 0, "every (days)"))
    every_hours = num(d.get("every_hours") or 0, "every (hours)")
    if not every_days and not every_hours:
        raise ValidationError("Set how often: every N days and/or every N working hours")
    vals = dict(equipment_id=e["id"], title=text(d.get("title"), "title", required=True), every_days=every_days, every_hours=every_hours,
                start_date=acc.clean_date(d.get("start_date"), "start date") if d.get("start_date") else "", notes=text(d.get("notes"), maxlen=1000))
    if plan_id:
        db.x(f"UPDATE maintenance_plans SET {', '.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [int(plan_id)])
        return int(plan_id)
    return db.x(f"INSERT INTO maintenance_plans({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})", list(vals.values())).lastrowid


def delete_plan(db, plan_id):
    db.x("DELETE FROM maintenance_plans WHERE id=?", (int(plan_id),))


def log_maintenance(db, d, user=""):
    e = get(db, d.get("equipment_id") or d.get("equipment"))
    kind = d.get("kind") if d.get("kind") in LOG_KINDS else "service"
    plan = int(d["plan_id"]) if d.get("plan_id") else None
    if plan and not db.one("SELECT 1 FROM maintenance_plans WHERE id=? AND equipment_id=?", (plan, e["id"])):
        raise ValidationError("That plan is for another machine")
    cost = to_minor(num(d.get("cost") or 0, "cost"))
    date = acc.clean_date(d.get("date"))
    hours_at = num(d.get("hours_at"), "hour meter") if d.get("hours_at") not in (None, "") else e["meter"]
    vendor = text(d.get("vendor"))
    desc = text(d.get("description"), maxlen=1000) or kind.title()
    with db.tx():
        eid = None
        if cost:
            pool = _acct(db, "equipment_pool", "other_overhead")
            cr = ({"account": db.acct("ap"), "credit": to_major(cost), "party": vendor} if vendor and d.get("on_credit")
                  else {"account": acc.money_account(db, d.get("method"), d.get("paid_from")), "credit": to_major(cost)})
            eid = acc._insert_entry(db, date=date, kind="Equipment Maintenance", party=vendor, user=user, ref=e["code"],
                                    memo=f"{kind.title()} — {e['name']} ({e['code']}): {desc}",
                                    lines=[{"account": pool, "debit": to_major(cost)}, cr], meta={"equipment": e["code"], "hours_at": hours_at})
        lid = db.conn.execute("INSERT INTO maintenance_logs(equipment_id,plan_id,date,kind,description,hours_at,cost,vendor,entry_id) "
                              "VALUES (?,?,?,?,?,?,?,?,?)", (e["id"], plan, date, kind, desc, hours_at, cost, vendor, eid)).lastrowid
        if d.get("back_in_service") and e["status"] == "maintenance":
            db.conn.execute("UPDATE equipment SET status=? WHERE id=?", ("in_use" if e["bu_id"] else "available", e["id"]))
    return lid


def delete_log(db, lid, user=""):
    lg = db.one("SELECT * FROM maintenance_logs WHERE id=?", (int(lid),))
    if not lg:
        raise ValidationError("Maintenance record not found")
    with db.tx():
        if lg["entry_id"] and db.one("SELECT 1 FROM journal_entries WHERE id=? AND status='posted'", (lg["entry_id"],)):
            acc.void_entry(db, lg["entry_id"], "Maintenance record deleted", user)
        db.conn.execute("DELETE FROM maintenance_logs WHERE id=?", (lg["id"],))


def logs(db, equipment_id=None):
    rows = db.q("SELECT m.*, e.code, e.name, p.title AS plan FROM maintenance_logs m JOIN equipment e ON e.id=m.equipment_id "
                "LEFT JOIN maintenance_plans p ON p.id=m.plan_id" + (" WHERE m.equipment_id=?" if equipment_id else "") +
                " ORDER BY m.date DESC, m.id DESC", (int(equipment_id),) if equipment_id else ())
    for r in rows:
        r["cost"] = to_major(r["cost"])
    return rows


# ------------------------------------------------------------------ registers, reports & alerts
def listing(db, status=None, q=None, today=None):
    w, p = ["1=1"], []
    if status:
        w.append("status=?")
        p.append(status)
    if q:
        w.append("(code LIKE ? OR name LIKE ? OR category LIKE ? OR serial LIKE ?)")
        p += [f"%{q}%"] * 4
    return [get(db, r["id"], today) for r in db.q(f"SELECT id FROM equipment WHERE {' AND '.join(w)} ORDER BY code", p)]


def report(db, date_from=None, date_to=None):
    """Per machine: hours, charged to projects, fuel and maintenance; and per project: hours and cost charged."""
    w, p = ["1=1"], []
    if date_from:
        w.append("u.date>=?")
        p.append(date_from)
    if date_to:
        w.append("u.date<=?")
        p.append(date_to)
    by_bu = db.q(f"""SELECT b.code AS bu_code, b.name AS bu_name, e.code, e.name, SUM(u.hours) hours,
                            SUM(u.hours*e.hourly_rate) charge, SUM(u.fuel_cost) fuel
                     FROM equipment_usage u JOIN equipment e ON e.id=u.equipment_id LEFT JOIN business_units b ON b.id=u.bu_id
                     WHERE {' AND '.join(w)} GROUP BY u.bu_id, u.equipment_id ORDER BY b.code, e.code""", p)
    for r in by_bu:
        r["charge"], r["fuel"] = to_major(int(round(r["charge"] or 0))), to_major(r["fuel"])
        r["total"] = round(r["charge"] + r["fuel"], 2)
    pool = 0
    try:
        acct = db.acct("equipment_pool")
        pool = to_major(db.one(f"SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                               f"WHERE {acc.LIVE} AND l.account=?", (acct,))["n"])
    except LookupError:
        pass
    machines = listing(db)
    return {"by_project": by_bu, "machines": [{k: m[k] for k in ("code", "name", "status", "bu_code", "hours", "meter", "hourly_rate",
                                                                   "charged", "fuel_cost", "maintenance_cost", "next_due")} for m in machines],
            "pool_balance": pool,
            "pool_note": "Debit = machines cost more than projects were charged (rates too low); credit = rates recover more."}


def alerts(db, today=None):
    rows = listing(db, today=today)
    due = [{"code": m["code"], "name": m["name"], "plan": p["title"], "state": p["state"], "due_date": p["due_date"],
            "due_hours": p["due_hours"], "meter": p["meter"]} for m in rows if m["status"] != "retired" for p in m["due"]]
    return {"maintenance_due": due, "overdue": sum(1 for x in due if x["state"] == "overdue"),
            "in_maintenance": sum(1 for m in rows if m["status"] == "maintenance")}


def export_rows(db):
    eqs = db.q("SELECT e.*, b.code AS bu_code, f.code AS asset_code FROM equipment e LEFT JOIN business_units b ON b.id=e.bu_id "
               "LEFT JOIN fixed_assets f ON f.id=e.asset_id ORDER BY e.id")
    code = {e["id"]: e["code"] for e in eqs}
    bu = {b["id"]: b["code"] for b in db.q("SELECT id, code FROM business_units")}
    plans_ = db.q("SELECT * FROM maintenance_plans ORDER BY id")
    ptitle = {p["id"]: p["title"] for p in plans_}
    return {"equipment": [{k: e[k] for k in ("code", "name", "category", "serial", "asset_code", "meter_start", "bu_code", "status", "notes")}
                          | {"hourly_rate": to_major(e["hourly_rate"])} for e in eqs],
            "plans": [{"equipment": code.get(p["equipment_id"]), **{k: p[k] for k in ("title", "every_days", "every_hours", "start_date", "notes")}}
                      for p in plans_],
            "usage": [{"equipment": code.get(u["equipment_id"]), "bu_code": bu.get(u["bu_id"], ""), "fuel_cost": to_major(u["fuel_cost"]),
                       "posted": bool(u["entry_id"]), **{k: u[k] for k in ("date", "hours", "operator", "fuel_qty", "notes")}}
                      for u in db.q("SELECT * FROM equipment_usage ORDER BY id")],
            "logs": [{"equipment": code.get(m["equipment_id"]), "plan": ptitle.get(m["plan_id"], ""), "cost": to_major(m["cost"]),
                      "posted": bool(m["entry_id"]), **{k: m[k] for k in ("date", "kind", "description", "hours_at", "vendor")}}
                     for m in db.q("SELECT * FROM maintenance_logs ORDER BY id")]}


def import_rows(db, data, counts):
    """JSON restore — usage and maintenance come back as records (their entries come back with the journal)."""
    n = 0
    for e in data.get("equipment") or []:
        if not db.one("SELECT 1 FROM equipment WHERE code=?", (e.get("code"),)):
            save(db, {**e, "asset_code": e.get("asset_code") if e.get("asset_code") and
                      db.one("SELECT 1 FROM fixed_assets WHERE code=?", (e["asset_code"],)) else None})
            n += 1
    ids = {r["code"]: r["id"] for r in db.q("SELECT id, code FROM equipment")}
    for p in data.get("plans") or []:
        if p.get("equipment") in ids and not db.one("SELECT 1 FROM maintenance_plans WHERE equipment_id=? AND title=?", (ids[p["equipment"]], p.get("title"))):
            save_plan(db, {**p, "equipment_id": ids[p["equipment"]]})
    for u in data.get("usage") or []:
        if u.get("equipment") in ids:
            bu = db.one("SELECT id FROM business_units WHERE code=?", (u.get("bu_code") or "",))
            ref = db.one("SELECT code FROM equipment WHERE id=?", (ids[u["equipment"]],))["code"]
            ent = db.one("SELECT id FROM journal_entries WHERE kind='Equipment Usage' AND ref=? AND date=? AND status='posted' AND "
                         "id NOT IN (SELECT entry_id FROM equipment_usage WHERE entry_id IS NOT NULL) ORDER BY id LIMIT 1",
                         (ref, u.get("date"))) if u.get("posted") else None
            db.x("INSERT INTO equipment_usage(equipment_id,date,bu_id,hours,operator,fuel_qty,fuel_cost,notes,entry_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (ids[u["equipment"]], u.get("date"), bu["id"] if bu else None, u.get("hours") or 0, u.get("operator") or "",
                  u.get("fuel_qty") or 0, to_minor(u.get("fuel_cost") or 0), u.get("notes") or "", ent["id"] if ent else None))
    for m in data.get("logs") or []:
        if m.get("equipment") in ids:
            plan = db.one("SELECT id FROM maintenance_plans WHERE equipment_id=? AND title=?", (ids[m["equipment"]], m.get("plan") or ""))
            ref = db.one("SELECT code FROM equipment WHERE id=?", (ids[m["equipment"]],))["code"]
            ent = db.one("SELECT id FROM journal_entries WHERE kind='Equipment Maintenance' AND ref=? AND date=? AND status='posted' AND "
                         "id NOT IN (SELECT entry_id FROM maintenance_logs WHERE entry_id IS NOT NULL) ORDER BY id LIMIT 1",
                         (ref, m.get("date"))) if m.get("posted") else None
            db.x("INSERT INTO maintenance_logs(equipment_id,plan_id,date,kind,description,hours_at,cost,vendor,entry_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (ids[m["equipment"]], plan["id"] if plan else None, m.get("date"), m.get("kind") or "service", m.get("description") or "",
                  m.get("hours_at"), to_minor(m.get("cost") or 0), m.get("vendor") or "", ent["id"] if ent else None))
    if n:
        counts["Equipment"] = n
