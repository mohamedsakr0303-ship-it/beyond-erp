"""Labour on site (العمالة): daily attendance per worker and project, productivity, labour cost by project, and the
month's attendance carried into the payroll run.

A day's sheet lists the active employees; for each one: status (present / half day / absent / leave / sick), the project
he worked on, hours, overtime, and what he produced (quantity + unit + task) for productivity. Nothing posts from here
— pay is posted by the payroll run, which can be filled from the month's attendance:

  * daily workers: days worked (half day = ½)          * hourly workers: hours worked
  * piece workers: quantity produced                   * monthly staff: unpaid absent days
  * everyone: overtime hours → day overtime

Labour cost by project spreads each worker's day cost (daily rate, hourly rate × hours, or monthly basic ÷ days in the
month) over the projects he was on — an estimate from the rates for site management; the books keep the posted payroll.
"""
from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major

STATUSES = ("present", "half", "absent", "leave", "sick")
WORKED = ("present", "half")


def sheet(db, date, bu_id=None):
    """The day's sheet: every active employee (and anyone already marked that day), with his entry if any."""
    date = acc.clean_date(date)
    rows = db.q("""SELECT e.id AS employee_id, e.code, e.name, e.job_title, e.emp_type, e.bu_id AS home_bu,
                          a.id, a.status, a.bu_id, a.hours, a.overtime, a.qty, a.unit, a.task, a.notes
                   FROM employees e LEFT JOIN attendance a ON a.employee_id=e.id AND a.date=?
                   WHERE e.status='active' OR a.id IS NOT NULL ORDER BY e.code""", (date,))
    if bu_id:
        rows = [r for r in rows if (r["bu_id"] or r["home_bu"]) == int(bu_id)]
    hd = _hours_day(db)
    for r in rows:
        r["marked"] = r["id"] is not None
        if not r["marked"]:
            r.update(status="", bu_id=r["home_bu"], hours=hd, overtime=0, qty=0, unit="", task="", notes="")
    return {"date": date, "hours_day": hd, "rows": rows,
            "summary": {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES} | {"unmarked": sum(1 for r in rows if not r["marked"])}}


def _hours_day(db):
    try:
        from . import payroll
        return float(payroll.settings(db)["hours_day"] or 8)
    except Exception:  # noqa: BLE001 — payroll settings missing: a normal 8-hour day
        return 8.0


def save_day(db, d, user=""):
    """d: {"date", "rows": [{"employee_id"|"employee", "status", "bu_id"|"bu_code", "hours", "overtime", "qty", "unit", "task", "notes"}]}
    A row with a blank status clears that worker's day."""
    date = acc.clean_date(d.get("date"))
    hd = _hours_day(db)
    n = 0
    with db.tx():
        for i, r in enumerate(d.get("rows") or [], start=1):
            if not isinstance(r, dict):
                continue
            emp = db.one("SELECT id, bu_id FROM employees WHERE id=? OR code=? OR name=?",
                         (r.get("employee_id") or -1, text(r.get("employee")), text(r.get("employee"))))
            if not emp:
                raise ValidationError(f"Row {i}: unknown employee {r.get('employee') or r.get('employee_id')}")
            status = text(r.get("status")).lower()
            if not status:
                db.conn.execute("DELETE FROM attendance WHERE date=? AND employee_id=?", (date, emp["id"]))
                continue
            if status not in STATUSES:
                raise ValidationError(f"Row {i}: status must be one of {', '.join(STATUSES)}")
            bu = acc.bu_id_or_none(db, r.get("bu_id") or r.get("bu_code")) or (emp["bu_id"] if status in WORKED else None)
            hours = num(r.get("hours"), f"row {i} hours") if r.get("hours") not in (None, "") else (hd if status == "present" else hd / 2 if status == "half" else 0)
            if status not in WORKED:
                hours = 0
            vals = (bu, status, hours, num(r.get("overtime") or 0, f"row {i} overtime"), num(r.get("qty") or 0, f"row {i} quantity"),
                    text(r.get("unit")), text(r.get("task"), maxlen=300), text(r.get("notes"), maxlen=1000))
            db.conn.execute("""INSERT INTO attendance(date,employee_id,bu_id,status,hours,overtime,qty,unit,task,notes) VALUES (?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(date, employee_id) DO UPDATE SET bu_id=excluded.bu_id, status=excluded.status,
                               hours=excluded.hours, overtime=excluded.overtime, qty=excluded.qty, unit=excluded.unit,
                               task=excluded.task, notes=excluded.notes""", (date, emp["id"]) + vals)
            n += 1
    return n


def mark_all(db, date, bu_id=None, status="present"):
    """Quick fill: everyone not yet marked that day gets the status (at his home project)."""
    s = sheet(db, date, bu_id)
    rows = [{"employee_id": r["employee_id"], "status": status, "bu_id": bu_id or r["bu_id"]} for r in s["rows"] if not r["marked"]]
    return save_day(db, {"date": s["date"], "rows": rows})


def records(db, date_from=None, date_to=None, bu_id=None, employee_id=None):
    w, p = ["1=1"], []
    if date_from:
        w.append("a.date>=?")
        p.append(date_from)
    if date_to:
        w.append("a.date<=?")
        p.append(date_to)
    if bu_id:
        w.append("a.bu_id=?")
        p.append(int(bu_id))
    if employee_id:
        w.append("a.employee_id=?")
        p.append(int(employee_id))
    return db.q(f"""SELECT a.*, e.code, e.name, e.emp_type, b.code AS bu_code FROM attendance a JOIN employees e ON e.id=a.employee_id
                    LEFT JOIN business_units b ON b.id=a.bu_id WHERE {' AND '.join(w)} ORDER BY a.date DESC, e.code""", p)


def _day_cost(e, r, days_month, hours_day):
    """Estimated cost of one attendance row (pounds)."""
    t = e["emp_type"]
    ot = (r["overtime"] or 0)
    if t == "daily":
        base = to_major(e["daily_rate"]) * (1 if r["status"] == "present" else 0.5)
        rate_h = to_major(e["daily_rate"]) / hours_day
    elif t == "hourly":
        base = to_major(e["hourly_rate"]) * (r["hours"] or 0)
        rate_h = to_major(e["hourly_rate"])
    elif t == "piece":
        base = to_major(e["piece_rate"]) * (r["qty"] or 0)
        rate_h = to_major(e["hourly_rate"])
    else:
        base = to_major(e["basic"]) / days_month * (1 if r["status"] == "present" else 0.5)
        rate_h = to_major(e["basic"]) / days_month / hours_day
    return base + ot * rate_h


def summary(db, date_from, date_to, bu_id=None):
    """Per worker: days, hours, overtime, absences, output and productivity; per project: worker-days, hours and estimated cost."""
    try:
        from . import payroll
        s = payroll.settings(db)
        dm, hd = float(s["days_month"] or 30), float(s["hours_day"] or 8)
    except Exception:  # noqa: BLE001
        dm, hd = 30.0, 8.0
    rows = records(db, date_from, date_to, bu_id)
    emps = {e["id"]: e for e in db.q("SELECT * FROM employees")}
    workers, projects = {}, {}
    for r in rows:
        e = emps[r["employee_id"]]
        w = workers.setdefault(r["employee_id"], {"code": e["code"], "name": e["name"], "emp_type": e["emp_type"], "days": 0.0, "hours": 0.0,
                                                  "overtime": 0.0, "absent": 0, "leave": 0, "sick": 0, "output": {}, "cost": 0.0})
        if r["status"] in WORKED:
            w["days"] += 1 if r["status"] == "present" else 0.5
            w["hours"] += r["hours"] or 0
            w["overtime"] += r["overtime"] or 0
            cost = _day_cost(e, r, dm, hd)
            w["cost"] += cost
            if r["qty"]:
                key = f"{r['task'] or 'output'} ({r['unit'] or 'units'})"
                w["output"][key] = w["output"].get(key, 0) + r["qty"]
            p = projects.setdefault(r["bu_code"] or "—", {"bu_code": r["bu_code"] or "", "worker_days": 0.0, "hours": 0.0, "overtime": 0.0,
                                                          "cost": 0.0, "workers": set()})
            p["worker_days"] += 1 if r["status"] == "present" else 0.5
            p["hours"] += r["hours"] or 0
            p["overtime"] += r["overtime"] or 0
            p["cost"] += cost
            p["workers"].add(r["employee_id"])
        elif r["status"] in ("absent", "leave", "sick"):
            w[r["status"]] += 1
    for w in workers.values():
        w["cost"] = round(w["cost"], 2)
        w["productivity"] = {k: round(v / w["hours"], 3) if w["hours"] else None for k, v in w["output"].items()}
    for p in projects.values():
        p["workers"] = len(p["workers"])
        p["cost"] = round(p["cost"], 2)
    return {"from": date_from, "to": date_to, "workers": sorted(workers.values(), key=lambda w: w["code"]),
            "projects": sorted(projects.values(), key=lambda p: p["bu_code"]),
            "note": "Cost is estimated from each worker's rates; the posted payroll is what the books carry."}


def payroll_inputs(db, period):
    """What the month's attendance says for each worker's payroll line."""
    if len(period or "") != 7:
        raise ValidationError("Month as YYYY-MM")
    out = {}
    emps = {e["id"]: e for e in db.q("SELECT id, emp_type FROM employees")}
    for r in db.q("SELECT * FROM attendance WHERE substr(date,1,7)=?", (period,)):
        t = emps.get(r["employee_id"], {}).get("emp_type", "monthly")
        o = out.setdefault(r["employee_id"], {"employee_id": r["employee_id"], "units": 0.0, "absent_days": 0.0, "ot_day": 0.0})
        if r["status"] in WORKED:
            o["units"] += (1 if r["status"] == "present" else 0.5) if t == "daily" else (r["hours"] or 0) if t == "hourly" else \
                (r["qty"] or 0) if t == "piece" else 0
            o["ot_day"] += r["overtime"] or 0
            if t == "monthly" and r["status"] == "half":
                o["absent_days"] += 0.5
        elif r["status"] == "absent" and t == "monthly":
            o["absent_days"] += 1
    return list(out.values())


def fill_payroll(db, run_id):
    """Carry the month's attendance into a draft payroll run (only the workers with attendance; other fields kept)."""
    from . import payroll
    r = payroll._run(db, run_id)
    if r["status"] != "draft":
        raise ValidationError("This run is posted — unpost it to change it")
    run = payroll.get_run(db, r["id"])
    have = {ln["employee_id"]: ln for ln in run["lines"]}
    lines = []
    for x in payroll_inputs(db, r["period"]):
        old = have.get(x["employee_id"], {})
        lines.append({**{k: old.get(k) for k in ("ot_night", "ot_rest", "components", "paid", "method")}, **x})
    payroll.save_lines(db, r["id"], lines)
    return len(lines)


def alerts(db, today=None):
    today = today or acc.today()
    active = db.one("SELECT COUNT(*) n FROM employees WHERE status='active'")["n"]
    marked = db.one("SELECT COUNT(*) n FROM attendance WHERE date=?", (today,))["n"]
    return {"unmarked_today": max(active - marked, 0) if active else 0, "absent_today":
            db.one("SELECT COUNT(*) n FROM attendance WHERE date=? AND status IN ('absent','sick')", (today,))["n"]}


def export_rows(db):
    return [{"date": r["date"], "employee": r["code"], "bu_code": r["bu_code"] or "", **{k: r[k] for k in ("status", "hours", "overtime", "qty",
                                                                                                        "unit", "task", "notes")}}
            for r in db.q("SELECT a.*, e.code, b.code AS bu_code FROM attendance a JOIN employees e ON e.id=a.employee_id "
                          "LEFT JOIN business_units b ON b.id=a.bu_id ORDER BY a.date, e.code")]


def import_rows(db, rows, counts):
    by_date = {}
    for r in rows or []:
        if r.get("date") and r.get("employee"):
            by_date.setdefault(acc.clean_date(r["date"]), []).append(r)
    n = 0
    for date, rs in sorted(by_date.items()):
        ok = [r for r in rs if db.one("SELECT 1 FROM employees WHERE code=? OR name=?", (r["employee"], r["employee"]))]
        n += save_day(db, {"date": date, "rows": ok})
    if n:
        counts["Attendance"] = n
