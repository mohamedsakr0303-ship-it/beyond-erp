"""Payroll: employees, pay components, monthly payroll runs, payslips — and measured-work (crew) pay sheets.

Built the way mature ERPs (Odoo's salary structures and rules) organise it, with Egyptian statutory defaults:
  employee   a contract: monthly / daily / hourly / piece-rate pay, fixed allowances, insured & taxed flags,
             the default business unit and cost type (direct labour, site overhead, admin)
  components the pay items, each with an English / Arabic name, earning or deduction, taxable, insurable,
             fixed on the employee or entered on each run — rename them, switch them off, add your own
  settings   social insurance (employee 11 %, employer 18.75 %, insurable wage 2,700 – 16,700 a month from 1 Jan 2026),
             salary tax bands (Law 7 of 2024: 20,000 personal exemption; 0 / 10 / 15 / 20 / 22.5 / 25 / 27.5 %),
             the high-income rule that drops the lower bands (switchable), the Martyrs Fund (0.05 % of gross),
             overtime rates (day 1.35, night 1.70, rest day / holiday 2.0), days in a month and hours in a day.
             Every number is editable: rules change, and your accountant has the last word.
  run        one per month: a line per employee with days / hours / units, absence, overtime hours, bonus,
             commission, penalties and advance recovery; computed lines are posted as one entry per employee:
                 Dr salary cost (gross − penalties − other deductions)  [the employee's cost account, business unit]
                 Dr employer social insurance                            [admin: 6055; others: their cost account]
                 Cr social insurance payable (employee + employer share)
                 Cr salary tax payable · Cr Martyrs Fund payable
                 Cr employee advances (recovered) · Cr accrued payroll (net, owed to the employee)
             and paid later with a payment voucher (or "paid now" on the line).
Crew sheets: what a crew / subcontractor earns for measured work (frames × width × height m², metres, pieces…), on
its own — nothing reaches the books unless you record what you actually paid.
"""
import json
import math
from decimal import ROUND_HALF_UP, Decimal

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

EMP_TYPES = ("monthly", "daily", "hourly", "piece")
COST_TYPES = list(acc.PAYROLL_TYPES)
KIND = "Payroll Accrual"

# code, English, Arabic, kind (earning/deduction), where (fixed on the employee / run = entered each month), taxable, insurable
DEFAULT_COMPONENTS = [
    ("housing", "Housing allowance", "بدل سكن", "earning", "fixed", True, True),
    ("transport", "Transport allowance", "بدل انتقال", "earning", "fixed", True, True),
    ("meal", "Meal allowance", "بدل وجبة", "earning", "fixed", True, True),
    ("phone", "Phone allowance", "بدل اتصالات", "earning", "fixed", True, True),
    ("site", "Site / hardship allowance", "بدل موقع / طبيعة عمل", "earning", "fixed", True, True),
    ("other_allow", "Other allowance", "بدلات أخرى", "earning", "fixed", True, True),
    ("bonus", "Bonus", "مكافأة", "earning", "run", True, False),
    ("commission", "Sales commission", "عمولة مبيعات", "earning", "run", True, False),
    ("incentive", "Incentive / productivity", "حافز / إنتاج", "earning", "run", True, False),
    ("other_earn", "Other earnings", "مستحقات أخرى", "earning", "run", True, False),
    ("penalty", "Penalties", "جزاءات", "deduction", "run", True, False),
    ("advance", "Advance recovery", "استرداد سلفة", "deduction", "run", False, False),
    ("other_ded", "Other deductions", "خصومات أخرى", "deduction", "run", False, False),
]
FIXED = [c[0] for c in DEFAULT_COMPONENTS if c[4] == "fixed"]
VARIABLE = [c[0] for c in DEFAULT_COMPONENTS if c[4] == "run"]

DEFAULT_SETTINGS = {
    "si_emp_rate": 0.11, "si_er_rate": 0.1875, "si_min": 2700, "si_max": 16700,
    "exemption": 20000, "martyrs_rate": 0.0005,
    "ot_day": 1.35, "ot_night": 1.70, "ot_rest": 2.0, "days_month": 30, "hours_day": 8, "prorate_allowances": True,
    # salary tax bands on annual taxable income after the personal exemption (Law 7 of 2024); last band open-ended
    "bands": [[40000, 0], [55000, 0.10], [70000, 0.15], [200000, 0.20], [400000, 0.225], [1200000, 0.25], [None, 0.275]],
    # high-income rule: above this annual taxable income, tax starts at band N from the first pound (the lower bands
    # are dropped). Sources differ on whether / how this applies after 2024 — check with your accountant; blank = off.
    "high_income": [[600000, 2], [700000, 3], [800000, 4], [900000, 5], [1200000, 6]],
    "components": [{"code": c, "name": n, "name_ar": ar, "kind": k, "where": w, "taxable": t, "insurable": i, "active": True}
                   for c, n, ar, k, w, t, i in DEFAULT_COMPONENTS],
}
SETTING_KEYS = ("si_emp_rate", "si_er_rate", "si_min", "si_max", "exemption", "martyrs_rate", "ot_day", "ot_night", "ot_rest",
                "days_month", "hours_day")


def _role(db, role, fallback=None):
    r = db.one("SELECT code FROM accounts WHERE role=?", (role,))
    return r["code"] if r else fallback


# ================================================================== settings
def settings(db):
    try:
        saved = json.loads(db.setting("payroll_settings") or "{}")
    except ValueError:
        saved = {}
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    for k, v in (saved or {}).items():
        if k in s and k != "components":
            s[k] = v
    comps = {c["code"]: c for c in s["components"]}
    for c in (saved or {}).get("components") or []:
        if isinstance(c, dict) and c.get("code"):
            comps[c["code"]] = {**comps.get(c["code"], {}), **c}
    s["components"] = list(comps.values())
    return s


def save_settings(db, d):
    s = settings(db)
    for k in SETTING_KEYS:
        if k in d and d[k] not in ("", None):
            s[k] = num(d[k], k)
    if "prorate_allowances" in d:
        s["prorate_allowances"] = d["prorate_allowances"] in (True, 1, "1", "true", "Yes", "yes", "on")
    if isinstance(d.get("bands"), list):
        bands = []
        for i, b in enumerate(d["bands"]):
            up = b[0] if isinstance(b, (list, tuple)) else b.get("upto")
            rate = b[1] if isinstance(b, (list, tuple)) else b.get("rate")
            if rate in ("", None) and up in ("", None):
                continue
            bands.append([None if up in ("", None) else num(up, f"band {i + 1} up to"), num(rate or 0, f"band {i + 1} rate")])
        if not bands:
            raise ValidationError("Keep at least one tax band")
        if bands[-1][0] is not None:
            bands.append([None, bands[-1][1]])
        ups = [b[0] for b in bands[:-1]]
        if any(u is None for u in ups) or ups != sorted(ups):
            raise ValidationError("Tax bands must go up in order; only the last one is open-ended")
        s["bands"] = bands
    if isinstance(d.get("high_income"), list):
        rules = []
        for r in d["high_income"]:
            over = r[0] if isinstance(r, (list, tuple)) else r.get("over")
            band = r[1] if isinstance(r, (list, tuple)) else r.get("band")
            if over in ("", None) or band in ("", None):
                continue
            rules.append([num(over, "above"), int(num(band, "band"))])
        s["high_income"] = sorted(rules)
    if isinstance(d.get("components"), list):
        comps, seen = [], set()
        for c in d["components"]:
            code = text(c.get("code"), maxlen=30).lower().replace(" ", "_")
            if not code or code in seen:
                continue
            seen.add(code)
            kind = c.get("kind") if c.get("kind") in ("earning", "deduction") else "earning"
            where = c.get("where") if c.get("where") in ("fixed", "run") else "run"
            comps.append({"code": code, "name": text(c.get("name"), maxlen=80) or code, "name_ar": text(c.get("name_ar"), maxlen=80),
                          "kind": kind, "where": where, "taxable": bool(c.get("taxable", kind == "earning")),
                          "insurable": bool(c.get("insurable", False)), "active": bool(c.get("active", True))})
        for code in ("advance", "penalty"):   # the books rely on these two
            if code not in seen:
                comps.append(next(x for x in DEFAULT_SETTINGS["components"] if x["code"] == code))
        s["components"] = comps
    db.set_setting("payroll_settings", json.dumps(s, ensure_ascii=False))
    return s


# ================================================================== the maths
def r2(x):
    """Round to the piaster, halves up — as Excel's ROUND and an accountant do (Python's round() goes to even)."""
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def annual_tax(x, s):
    """Salary tax on annual taxable income x (after the personal exemption)."""
    if x <= 0:
        return 0.0
    bands = s["bands"]
    k = 1
    for over, band in s.get("high_income") or []:
        if x > over:
            k = max(k, int(band))
    k = min(max(k, 1), len(bands))
    up = lambda j: math.inf if bands[j - 1][0] is None else bands[j - 1][0]  # noqa: E731
    tax = bands[k - 1][1] * min(x, up(k))
    prev = up(k)
    for j in range(k + 1, len(bands) + 1):
        if x <= prev:
            break
        tax += bands[j - 1][1] * (min(x, up(j)) - prev)
        prev = up(j)
    return tax


def compute(emp, inp, s):
    """One payslip. emp: employee dict (money in pounds); inp: the run's inputs. Returns the lines and totals."""
    comps = {c["code"]: c for c in s["components"] if c.get("active", True)}
    dm, hd = float(s["days_month"] or 30), float(s["hours_day"] or 8)
    t = emp.get("emp_type") or "monthly"
    units = float(inp.get("units") or 0)
    absent = float(inp.get("absent_days") or 0)
    allow = emp.get("allowances") or {}
    factor = 1.0
    if t == "monthly":
        factor = max(0.0, dm - absent) / dm
        basic = (emp.get("basic") or 0) * factor
        hourly = (emp.get("basic") or 0) / dm / hd
    elif t == "daily":
        basic = (emp.get("daily_rate") or 0) * units
        hourly = (emp.get("daily_rate") or 0) / hd
    elif t == "hourly":
        basic = (emp.get("hourly_rate") or 0) * units
        hourly = emp.get("hourly_rate") or 0
    else:
        basic = (emp.get("piece_rate") or 0) * units
        hourly = emp.get("hourly_rate") or 0
    pf = factor if (t == "monthly" and s.get("prorate_allowances", True)) else 1.0
    earn, ded = [("basic", "Basic pay", "الأجر الأساسي", r2(basic), True, True)], []
    for code, c in comps.items():
        if c["where"] == "fixed" and c["kind"] == "earning":
            v = r2(float(allow.get(code) or 0) * pf)
            if v:
                earn.append((code, c["name"], c.get("name_ar", ""), v, c["taxable"], c["insurable"]))
    ot = hourly * (float(inp.get("ot_day") or 0) * s["ot_day"] + float(inp.get("ot_night") or 0) * s["ot_night"]
                   + float(inp.get("ot_rest") or 0) * s["ot_rest"])
    if ot:
        earn.append(("overtime", "Overtime", "أجر إضافي", r2(ot), True, False))
    vals = inp.get("components") or {}
    for code, c in comps.items():
        if c["where"] != "run" and not (c["where"] == "fixed" and c["kind"] == "deduction"):
            continue
        v = r2(float(vals.get(code) or 0))
        if not v:
            continue
        (earn if c["kind"] == "earning" else ded).append((code, c["name"], c.get("name_ar", ""), v, c["taxable"], c["insurable"]))
    gross = r2(sum(e[3] for e in earn))
    pre_tax = r2(sum(d[3] for d in ded if d[4]))        # deductions that lower taxable pay (penalties)
    if emp.get("insured"):
        if float(emp.get("insurable_wage") or 0) > 0:
            base = float(emp["insurable_wage"])
        else:
            month_basic = (emp.get("basic") or 0) if t == "monthly" else (emp.get("daily_rate") or 0) * dm if t == "daily" else 0
            base = month_basic + sum(float(allow.get(c) or 0) for c, x in comps.items() if x["where"] == "fixed" and x["insurable"])
        insurable = min(max(base, s["si_min"]), s["si_max"]) if base > 0 else 0
    else:
        insurable = 0
    si_emp, si_er = r2(insurable * s["si_emp_rate"]), r2(insurable * s["si_er_rate"])
    if emp.get("taxed"):
        taxable = max(0.0, sum(e[3] for e in earn if e[4]) - pre_tax - si_emp)
        annual = r2(taxable * 12 - s["exemption"])
        tax = r2(annual_tax(annual, s) / 12)
        martyrs = r2(gross * s["martyrs_rate"])
    else:
        taxable = annual = tax = martyrs = 0
    advance = r2(sum(d[3] for d in ded if d[0] == "advance"))
    kept = r2(sum(d[3] for d in ded if d[0] != "advance"))    # penalties & other deductions: the company keeps them
    net = r2(gross - kept - advance - si_emp - tax - martyrs)
    return {"earnings": [{"code": e[0], "name": e[1], "name_ar": e[2], "amount": e[3]} for e in earn],
            "deductions": [{"code": d[0], "name": d[1], "name_ar": d[2], "amount": d[3]} for d in ded]
            + [x for x in ({"code": "si_emp", "name": "Social insurance (employee 11 %)", "name_ar": "التأمينات (حصة العامل)", "amount": si_emp},
                           {"code": "tax", "name": "Salary tax", "name_ar": "ضريبة كسب العمل", "amount": tax},
                           {"code": "martyrs", "name": "Martyrs Fund", "name_ar": "صندوق الشهداء", "amount": martyrs}) if x["amount"]],
            "gross": gross, "kept": kept, "advance": advance, "insurable": r2(insurable), "si_emp": si_emp, "si_er": si_er,
            "taxable": r2(taxable), "annual_taxable": max(annual, 0), "tax": tax, "martyrs": martyrs, "net": net,
            "cost": r2(gross - kept + si_er), "days_paid": r2(dm * factor) if t == "monthly" else units}


# ================================================================== employees
def _emp_row(db, e):
    e = dict(e)
    for k in ("basic", "daily_rate", "hourly_rate", "piece_rate", "insurable_wage"):
        e[k] = to_major(e[k] or 0)
    e["allowances"] = {k: to_major(v) for k, v in json.loads(e.get("allowances") or "{}").items()}
    e["insured"], e["taxed"] = bool(e["insured"]), bool(e["taxed"])
    b = db.one("SELECT code FROM business_units WHERE id=?", (e["bu_id"],)) if e.get("bu_id") else None
    e["bu_code"] = b["code"] if b else ""
    return e


def list_employees(db, q=None, status=None):
    w, p = [], []
    if q:
        w.append("(code LIKE ? OR name LIKE ? OR job_title LIKE ? OR department LIKE ?)")
        p += [f"%{q}%"] * 4
    if status:
        w.append("status=?")
        p.append(status)
    return [_emp_row(db, e) for e in db.q("SELECT * FROM employees" + (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY status, code", p)]


def get_employee(db, eid):
    e = db.one("SELECT * FROM employees WHERE id=? OR code=?", (eid if str(eid).isdigit() else -1, text(eid)))
    if not e:
        raise ValidationError("Employee not found")
    return _emp_row(db, e)


def next_code(db):
    n = 1
    for r in db.q("SELECT code FROM employees WHERE code LIKE 'EMP-%'"):
        if r["code"][4:].isdigit():
            n = max(n, int(r["code"][4:]) + 1)
    return f"EMP-{n:03d}"


def save_employee(db, d, eid=None):
    name = text(d.get("name"), "name", required=True, maxlen=120)
    t = d.get("emp_type") or "monthly"
    if t not in EMP_TYPES:
        raise ValidationError("Pay type must be monthly, daily, hourly or piece")
    ct = d.get("cost_type") or COST_TYPES[2]
    if ct not in COST_TYPES:
        raise ValidationError("Unknown cost type")
    code = text(d.get("code"), maxlen=30) or (get_employee(db, eid)["code"] if eid else next_code(db))
    clash = db.one("SELECT id FROM employees WHERE code=?", (code,))
    if clash and str(clash["id"]) != str(eid or ""):
        raise ValidationError(f"Employee code {code} is already used")
    acct = text(d.get("account"))
    if acct:
        acct = acc.account_code(db, acct, types=("COGS", "Expense"))
    bu = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"))
    allow = d.get("allowances") or {}
    if isinstance(allow, str):
        allow = json.loads(allow or "{}")
    for c in FIXED:     # flat columns (Excel rows: al_housing…) also work
        if d.get("al_" + c) not in (None, ""):
            allow[c] = d["al_" + c]
    yes = lambda v, dflt: dflt if v in (None, "") else str(v).lower() in ("1", "true", "yes", "y", "on")  # noqa: E731
    vals = (code, name, text(d.get("name_ar"), maxlen=120), text(d.get("national_id"), maxlen=30), text(d.get("job_title"), maxlen=80),
            text(d.get("department"), maxlen=60), t, ct, acct, bu, acc.clean_date(d.get("hire_date")) if d.get("hire_date") else "",
            acc.clean_date(d.get("end_date")) if d.get("end_date") else "",
            "left" if d.get("status") == "left" or d.get("end_date") else "active",
            to_minor(num(d.get("basic") or 0, "basic salary")), to_minor(num(d.get("daily_rate") or 0, "daily rate")),
            to_minor(num(d.get("hourly_rate") or 0, "hourly rate")), to_minor(num(d.get("piece_rate") or 0, "piece rate")),
            text(d.get("piece_unit"), maxlen=20),
            json.dumps({k: to_minor(num(v or 0, k)) for k, v in allow.items() if num(v or 0, k)}),
            1 if yes(d.get("insured"), t == "monthly") else 0, to_minor(num(d.get("insurable_wage") or 0, "insurable wage")),
            text(d.get("insurance_no"), maxlen=30), 1 if yes(d.get("taxed"), t == "monthly") else 0,
            d.get("method") if d.get("method") in acc.PAYMENT_METHODS else "", text(d.get("bank_account"), maxlen=80),
            text(d.get("phone"), maxlen=30), text(d.get("notes"), maxlen=1000))
    cols = ("code,name,name_ar,national_id,job_title,department,emp_type,cost_type,account,bu_id,hire_date,end_date,status,basic,"
            "daily_rate,hourly_rate,piece_rate,piece_unit,allowances,insured,insurable_wage,insurance_no,taxed,method,bank_account,phone,notes")
    if eid:
        db.x(f"UPDATE employees SET {', '.join(c + '=?' for c in cols.split(','))} WHERE id=?", vals + (get_employee(db, eid)["id"],))
        return get_employee(db, eid)["id"]
    return db.x(f"INSERT INTO employees({cols}) VALUES ({','.join('?' * len(cols.split(',')))})", vals).lastrowid


def delete_employee(db, eid):
    e = get_employee(db, eid)
    if db.one("SELECT 1 FROM payroll_lines WHERE employee_id=?", (e["id"],)):
        raise ValidationError("This employee is on a payroll run — mark them as left instead")
    db.x("DELETE FROM employees WHERE id=?", (e["id"],))


# ================================================================== runs
def _run(db, rid):
    r = db.one("SELECT * FROM payroll_runs WHERE id=? OR period=?", (rid if str(rid).isdigit() else -1, text(rid)))
    if not r:
        raise ValidationError("Payroll run not found")
    return r


def _line_inputs(ln):
    return {"units": ln["units"], "absent_days": ln["absent_days"], "ot_day": ln["ot_day"], "ot_night": ln["ot_night"],
            "ot_rest": ln["ot_rest"], "components": {k: to_major(v) for k, v in json.loads(ln["inputs"] or "{}").items()},
            "paid": to_major(ln["paid"]), "method": ln["method"]}


def get_run(db, rid):
    r = dict(_run(db, rid))
    s = settings(db)
    lines, tot = [], {k: 0.0 for k in ("gross", "kept", "advance", "si_emp", "si_er", "tax", "martyrs", "net", "cost", "paid")}
    for ln in db.q("SELECT l.*, e.code, e.name, e.job_title, e.emp_type FROM payroll_lines l JOIN employees e ON e.id=l.employee_id "
                   "WHERE l.run_id=? ORDER BY e.code", (r["id"],)):
        emp = get_employee(db, ln["employee_id"])
        inp = _line_inputs(ln)
        c = compute(emp, inp, s) if r["status"] == "draft" else json.loads(ln["result"] or "{}") or compute(emp, inp, s)
        lines.append({"id": ln["id"], "employee_id": ln["employee_id"], "code": ln["code"], "name": ln["name"], "job_title": ln["job_title"],
                      "emp_type": ln["emp_type"], **inp, "result": c, "entry_id": ln["entry_id"]})
        for k in tot:
            tot[k] += c.get(k, 0) if k != "paid" else inp["paid"]
    r["lines"], r["totals"] = lines, {k: round(v, 2) for k, v in tot.items()}
    return r


def list_runs(db):
    out = []
    for r in db.q("SELECT * FROM payroll_runs ORDER BY period DESC"):
        g = get_run(db, r["id"])
        out.append({k: r[k] for k in ("id", "period", "date", "status", "notes")} | {"employees": len(g["lines"]), **g["totals"]})
    return out


def create_run(db, d):
    period = text(d.get("period"), "month", required=True)
    if len(period) != 7 or period[4] != "-":
        raise ValidationError("Month as YYYY-MM")
    if db.one("SELECT 1 FROM payroll_runs WHERE period=?", (period,)):
        raise ValidationError(f"There is already a payroll run for {period}")
    date = acc.clean_date(d.get("date") or _month_end(period))
    rid = db.x("INSERT INTO payroll_runs(period,date,status,notes) VALUES (?,?,'draft',?)", (period, date, text(d.get("notes")))).lastrowid
    s = settings(db)
    for e in db.q("SELECT * FROM employees WHERE status='active' ORDER BY code"):
        units = s["days_month"] if e["emp_type"] == "daily" else 0
        db.x("INSERT INTO payroll_lines(run_id,employee_id,units,method) VALUES (?,?,?,?)", (rid, e["id"], units, e["method"] or ""))
    return rid


def _month_end(period):
    import calendar
    y, m = int(period[:4]), int(period[5:7])
    return f"{period}-{calendar.monthrange(y, m)[1]:02d}"


def save_lines(db, rid, lines):
    r = _run(db, rid)
    if r["status"] != "draft":
        raise ValidationError("This run is posted — unpost it to change it")
    for ln in lines or []:
        eid = ln.get("employee_id") or (get_employee(db, ln["employee"])["id"] if ln.get("employee") else None)
        if not eid:
            continue
        comps = {k: to_minor(num(v or 0, k)) for k, v in (ln.get("components") or {}).items() if num(v or 0, k)}
        vals = (num(ln.get("units") or 0, "days / hours / units"), num(ln.get("absent_days") or 0, "absent days"),
                num(ln.get("ot_day") or 0, "overtime"), num(ln.get("ot_night") or 0, "overtime"), num(ln.get("ot_rest") or 0, "overtime"),
                json.dumps(comps), to_minor(num(ln.get("paid") or 0, "paid now")),
                ln.get("method") if ln.get("method") in acc.PAYMENT_METHODS else "")
        if db.one("SELECT 1 FROM payroll_lines WHERE run_id=? AND employee_id=?", (r["id"], eid)):
            db.x("UPDATE payroll_lines SET units=?,absent_days=?,ot_day=?,ot_night=?,ot_rest=?,inputs=?,paid=?,method=? "
                 "WHERE run_id=? AND employee_id=?", vals + (r["id"], eid))
        else:
            db.x("INSERT INTO payroll_lines(units,absent_days,ot_day,ot_night,ot_rest,inputs,paid,method,run_id,employee_id) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)", vals + (r["id"], eid))
    if isinstance(lines, list) and any(ln.get("remove") for ln in lines):
        for ln in lines:
            if ln.get("remove") and ln.get("employee_id"):
                db.x("DELETE FROM payroll_lines WHERE run_id=? AND employee_id=?", (r["id"], ln["employee_id"]))


def post_run(db, rid, user=""):
    r = _run(db, rid)
    if r["status"] != "draft":
        raise ValidationError("Already posted")
    s = settings(db)
    si = _role(db, "social_insurance_payable")
    tx = _role(db, "salary_tax_payable")
    mf = _role(db, "martyrs_fund")
    adv = _role(db, "employee_advances")
    er_admin = _role(db, "employer_si")
    accrued = _role(db, "accrued_payroll")
    if not all((si, tx, mf, adv, accrued)):
        raise ValidationError("Payroll accounts are missing from the chart of accounts")
    with db.tx():
        for ln in db.q("SELECT * FROM payroll_lines WHERE run_id=?", (r["id"],)):
            emp = get_employee(db, ln["employee_id"])
            inp = _line_inputs(ln)
            c = compute(emp, inp, s)
            if c["net"] < 0:
                raise ValidationError(f"{emp['name']}: deductions are more than the pay (net {c['net']:,.2f})")
            if not c["gross"]:
                db.x("UPDATE payroll_lines SET result=? WHERE id=?", (json.dumps(c), ln["id"]))
                continue
            role, needs_bu = acc.PAYROLL_TYPES[emp["cost_type"]]
            cost_acct = emp["account"] or _role(db, role)
            bu = emp["bu_id"]
            if needs_bu and not bu:
                raise ValidationError(f"{emp['name']}: {emp['cost_type']} needs a business unit on the employee")
            er_acct = er_admin if (role == "admin_salaries" and er_admin) else cost_acct
            paid = min(inp["paid"], c["net"])
            name = emp["name"]
            L = [{"account": cost_acct, "debit": round(c["gross"] - c["kept"], 2)}]
            if c["si_er"]:
                L.append({"account": er_acct, "debit": c["si_er"]})
            for acct, amt in ((si, round(c["si_emp"] + c["si_er"], 2)), (tx, c["tax"]), (mf, c["martyrs"])):
                if amt:
                    L.append({"account": acct, "credit": amt})
            if c["advance"]:
                L.append({"account": adv, "credit": c["advance"], "party": name})
            if paid:
                L.append({"account": acc.money_account(db, inp["method"] or emp["method"]), "credit": paid})
            if round(c["net"] - paid, 2):
                L.append({"account": accrued, "credit": round(c["net"] - paid, 2), "party": name})
            L = [x for x in L if x.get("debit") or x.get("credit")]
            eid = acc._insert_entry(db, date=r["date"], kind=KIND, memo=f"Payroll {r['period']} — {name}" + (f" — {emp['job_title']}" if emp["job_title"] else ""),
                                    bu_id=bu, party=name, ref=f"PR:{r['period']}:{emp['code']}", lines=L,
                                    meta={"payroll_run": r["period"], "employee": emp["code"], "cost_type": emp["cost_type"],
                                          "gross": c["gross"], "net": c["net"]}, user=user)
            db.x("UPDATE payroll_lines SET result=?, entry_id=? WHERE id=?", (json.dumps(c), eid, ln["id"]))
        db.x("UPDATE payroll_runs SET status='posted' WHERE id=?", (r["id"],))


def unpost_run(db, rid, user=""):
    """Reopen a posted run: its entries are voided (kept for the audit trail), the lines become editable again."""
    r = _run(db, rid)
    with db.tx():
        for ln in db.q("SELECT * FROM payroll_lines WHERE run_id=?", (r["id"],)):
            e = db.one("SELECT status FROM journal_entries WHERE id=?", (ln["entry_id"],)) if ln["entry_id"] else None
            if e and e["status"] != "void":
                acc.void_entry(db, ln["entry_id"], f"payroll {r['period']} reopened", user)
        db.x("UPDATE payroll_lines SET entry_id=NULL, result='' WHERE run_id=?", (r["id"],))
        db.x("UPDATE payroll_runs SET status='draft' WHERE id=?", (r["id"],))


def delete_run(db, rid):
    r = _run(db, rid)
    if r["status"] != "draft":
        raise ValidationError("Unpost the run first")
    db.x("DELETE FROM payroll_lines WHERE run_id=?", (r["id"],))
    db.x("DELETE FROM payroll_runs WHERE id=?", (r["id"],))


def payslip(db, rid, emp):
    r = get_run(db, rid)
    e = get_employee(db, emp)
    ln = next((x for x in r["lines"] if x["employee_id"] == e["id"]), None)
    if not ln:
        raise ValidationError("This employee is not on the run")
    return {"run": {k: r[k] for k in ("period", "date", "status")}, "employee": e, "line": ln,
            "company": db.setting("company_name", ""), "currency": db.setting("currency", "EGP")}


def preview(db, d):
    emp = d.get("employee") or {}
    if d.get("employee_id"):
        emp = get_employee(db, d["employee_id"])
    return compute(emp, d.get("inputs") or {}, settings(db))


# ================================================================== crew / measured-work pay
DEFAULT_WORK_ITEMS = {   # per business type: (item, unit, rate) — rates are blank on purpose, they are yours
    "scaffolding": [("Frame scaffold — installation", "m²"), ("Frame scaffold — dismantling", "m²"), ("Loading / unloading a truck", "trip")],
    "interior": [("Wall painting", "m²"), ("Gypsum-board ceiling", "m²"), ("Floor tiling", "m²"), ("Skirting", "m"), ("Electrical point", "piece")],
    "contracting": [("Formwork", "m²"), ("Rebar fixing", "ton"), ("Blockwork", "m²"), ("Plastering", "m²"), ("Excavation", "m³")],
    "development": [("Formwork", "m²"), ("Blockwork", "m²"), ("Plastering", "m²"), ("Finishing works", "m²")],
    "sales": [("Commission per unit sold", "piece"), ("Site visit", "piece")],
    "investment": [("Hours worked", "hour")],
    "general": [("Hours worked", "hour"), ("Pieces", "piece"), ("Area", "m²")],
}


def crew_qty(ln):
    """Quantity of one measured line = count × length/width × height/depth (blank dimensions count as 1)."""
    q = float(ln.get("count") or 0)
    for k in ("dim1", "dim2", "dim3"):
        if ln.get(k) not in (None, "", 0, "0"):
            q *= float(ln[k])
    return round(q, 4)


def crew_calc(lines):
    out, total, qty = [], 0.0, 0.0
    for ln in lines or []:
        q = crew_qty(ln)
        amt = round(q * float(ln.get("rate") or 0), 2)
        out.append({**ln, "qty": q, "amount": amt})
        total += amt
        qty += q
    return {"lines": out, "qty": round(qty, 4), "total": round(total, 2)}


def list_crew(db):
    rows = []
    for r in db.q("SELECT c.*, b.code AS bu_code FROM crew_sheets c LEFT JOIN business_units b ON b.id=c.bu_id ORDER BY c.date DESC, c.id DESC"):
        rows.append({**r, "lines": json.loads(r["lines"] or "[]"), "total": to_major(r["total"]), "paid": to_major(r["paid"])})
    return rows


def save_crew(db, d, cid=None):
    calc = crew_calc(d.get("lines") or [])
    vals = (acc.clean_date(d.get("date")), text(d.get("crew"), "crew / worker", required=True, maxlen=120), text(d.get("title"), maxlen=160),
            acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), json.dumps(calc["lines"], ensure_ascii=False),
            to_minor(calc["total"]), text(d.get("notes"), maxlen=1000))
    if cid:
        c = db.one("SELECT * FROM crew_sheets WHERE id=?", (cid,))
        if not c:
            raise ValidationError("Not found")
        db.x("UPDATE crew_sheets SET date=?,crew=?,title=?,bu_id=?,lines=?,total=?,notes=? WHERE id=?", vals + (cid,))
        return int(cid)
    return db.x("INSERT INTO crew_sheets(date,crew,title,bu_id,lines,total,notes) VALUES (?,?,?,?,?,?,?)", vals).lastrowid


def delete_crew(db, cid):
    c = db.one("SELECT * FROM crew_sheets WHERE id=?", (cid,))
    if c and c["entry_id"]:
        raise ValidationError("A payment is recorded for this sheet — delete that entry first")
    db.x("DELETE FROM crew_sheets WHERE id=?", (cid,))


def pay_crew(db, cid, d, user=""):
    """Record what you ACTUALLY paid the crew (it may differ from the sheet's total) as a labour cost."""
    c = db.one("SELECT * FROM crew_sheets WHERE id=?", (cid,))
    if not c:
        raise ValidationError("Not found")
    amount = num(d.get("amount") if d.get("amount") not in (None, "") else to_major(c["total"]), "amount paid", allow_zero=False)
    bu = acc.bu_id_or_none(db, d.get("bu_id") or c["bu_id"])
    ct = d.get("cost_type") or ("Direct Labour (Cost of Revenue)" if bu else "Administrative Salaries (Shared Overhead)")
    eid = acc.record_payroll(db, {"date": d.get("date") or c["date"], "worker": c["crew"], "role": c["title"] or "measured work",
                                  "cost_type": ct, "bu_id": bu, "account": d.get("account"), "qty": 1, "rate": amount,
                                  "basis": "Lump Sum", "paid": amount if d.get("paid_now", True) else 0, "method": d.get("method"),
                                  "paid_from": d.get("paid_from"), "period": (d.get("date") or c["date"])[:7]}, user)
    db.x("UPDATE crew_sheets SET paid=?, entry_id=? WHERE id=?", (to_minor(amount), eid, cid))
    return eid


# ================================================================== backup (JSON extras)
def export_rows(db):
    return {"employees": [dict(e) for e in db.q("SELECT * FROM employees ORDER BY id")],
            "runs": [dict(r) for r in db.q("SELECT * FROM payroll_runs ORDER BY id")],
            "lines": [dict(r) for r in db.q("SELECT * FROM payroll_lines ORDER BY id")],
            "crew": [dict(r) for r in db.q("SELECT * FROM crew_sheets ORDER BY id")]}


def import_rows(db, ex, counts):
    """Restore from a JSON backup (the journal entries come back with the journal; entry ids are matched by ref)."""
    emap, rmap = {}, {}
    db.conn.execute("DELETE FROM payroll_lines")
    db.conn.execute("DELETE FROM payroll_runs")
    db.conn.execute("DELETE FROM crew_sheets")
    db.conn.execute("DELETE FROM employees")
    cols = [r[1] for r in db.conn.execute("PRAGMA table_info(employees)")]
    for e in ex.get("employees") or []:
        e = {k: v for k, v in e.items() if k in cols and k != "id"}
        if e.get("bu_id") and not db.one("SELECT 1 FROM business_units WHERE id=?", (e["bu_id"],)):
            e["bu_id"] = None
        new = db.conn.execute(f"INSERT INTO employees({','.join(e)}) VALUES ({','.join('?' * len(e))})", tuple(e.values())).lastrowid
        old = next(x["id"] for x in ex["employees"] if x.get("code") == e.get("code"))
        emap[old] = new
    for r in ex.get("runs") or []:
        rmap[r["id"]] = db.conn.execute("INSERT INTO payroll_runs(period,date,status,notes) VALUES (?,?,?,?)",
                                        (r["period"], r["date"], r["status"], r.get("notes") or "")).lastrowid
    for ln in ex.get("lines") or []:
        if ln["run_id"] not in rmap or ln["employee_id"] not in emap:
            continue
        run = next(r for r in ex["runs"] if r["id"] == ln["run_id"])
        emp = next(e for e in ex["employees"] if e["id"] == ln["employee_id"])
        ent = db.one("SELECT id FROM journal_entries WHERE ref=? AND status!='void' ORDER BY id DESC", (f"PR:{run['period']}:{emp['code']}",))
        db.conn.execute("INSERT INTO payroll_lines(run_id,employee_id,units,absent_days,ot_day,ot_night,ot_rest,inputs,paid,method,result,entry_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rmap[ln["run_id"]], emap[ln["employee_id"]], ln["units"], ln["absent_days"], ln["ot_day"], ln["ot_night"],
                         ln["ot_rest"], ln["inputs"], ln["paid"], ln["method"], ln["result"], ent["id"] if ent else None))
    for c in ex.get("crew") or []:
        db.conn.execute("INSERT INTO crew_sheets(date,crew,title,bu_id,lines,total,paid,notes) VALUES (?,?,?,?,?,?,?,?)",
                        (c["date"], c["crew"], c.get("title") or "", c.get("bu_id"), c["lines"], c["total"], c.get("paid") or 0, c.get("notes") or ""))
    counts["Employees"] = len(emap)
    if rmap:
        counts["Payroll runs"] = len(rmap)


# ================================================================== spreadsheet rows (Employees / Payroll Run sheets)
def employee_sheet_rows(db):
    out = []
    for e in list_employees(db):
        row = {k: e[k] for k in ("code", "name", "name_ar", "national_id", "job_title", "department", "emp_type", "cost_type", "account",
                                 "bu_code", "hire_date", "end_date", "basic", "daily_rate", "hourly_rate", "piece_rate", "piece_unit",
                                 "insurable_wage", "insurance_no", "method", "bank_account", "phone", "notes")}
        row.update({"al_" + c: e["allowances"].get(c, "") or "" for c in FIXED})
        row.update({"insured": "Yes" if e["insured"] else "No", "taxed": "Yes" if e["taxed"] else "No",
                    "status": e["status"]})
        out.append({k: ("" if v in (None, 0, 0.0) and k not in ("insured", "taxed") else v) for k, v in row.items()})
    return out


def settings_sheet_rows(db):
    s = settings(db)
    rows = [{"key": k, "value": s[k]} for k in SETTING_KEYS] + [{"key": "prorate_allowances", "value": "Yes" if s["prorate_allowances"] else "No"}]
    for i, (up, rate) in enumerate(s["bands"], start=1):
        rows += [{"key": f"band_{i}_upto", "value": "" if up is None else up}, {"key": f"band_{i}_rate", "value": rate}]
    for over, band in s["high_income"]:
        rows.append({"key": f"start_band_{band}_above", "value": over})
    for c in s["components"]:
        rows.append({"key": f"component_{c['code']}", "value": f"{c['name']} | {c.get('name_ar', '')} | {c['kind']} | {c['where']} | "
                                                             f"{'taxable' if c['taxable'] else 'not taxable'} | "
                                                             f"{'insurable' if c['insurable'] else 'not insurable'} | {'on' if c.get('active', True) else 'off'}"})
    return rows


def import_settings_rows(db, rows):
    """The Payroll Settings sheet back into the settings (unknown keys are ignored)."""
    d, bands, rules, comps = {}, {}, [], []
    for r in rows:
        k, v = text(r.get("key")), r.get("value")
        if k in SETTING_KEYS:
            d[k] = v
        elif k == "prorate_allowances":
            d[k] = str(v).lower() in ("yes", "1", "true")
        elif k.startswith("band_") and k.count("_") == 2:
            _, i, what = k.split("_")
            bands.setdefault(int(i), [None, 0])[0 if what == "upto" else 1] = None if v in ("", None) else float(v)
        elif k.startswith("start_band_") and k.endswith("_above") and v not in ("", None):
            rules.append([float(v), int(k.split("_")[2])])
        elif k.startswith("component_"):
            p = [x.strip() for x in str(v or "").split("|")]
            if len(p) >= 6:
                comps.append({"code": k[10:], "name": p[0], "name_ar": p[1], "kind": p[2], "where": p[3], "taxable": p[4] == "taxable",
                              "insurable": p[5] == "insurable", "active": (p[6] if len(p) > 6 else "on") != "off"})
    if bands:
        d["bands"] = [bands[i] for i in sorted(bands)]
    d["high_income"] = rules
    if comps:
        d["components"] = comps
    save_settings(db, d)


def run_sheet_import(db, items, user=""):
    """Rows of the Payroll Run sheet for one month: create (or refill) that month's run and post it."""
    period = acc.clean_date(items[0][1].get("date"))[:7]
    r = db.one("SELECT * FROM payroll_runs WHERE period=?", (period,))
    if r and r["status"] == "posted":
        raise ValidationError(f"The {period} payroll is already posted in the app")
    rid = r["id"] if r else db.x("INSERT INTO payroll_runs(period,date,status,notes) VALUES (?,?,'draft','from Excel')",
                                 (period, acc.clean_date(items[0][1].get("date")))).lastrowid
    lines = []
    for _i, row in items:
        e = get_employee(db, row.get("employee"))
        lines.append({"employee_id": e["id"], "units": row.get("units"), "absent_days": row.get("absent_days"), "ot_day": row.get("ot_day"),
                      "ot_night": row.get("ot_night"), "ot_rest": row.get("ot_rest"), "paid": row.get("paid"), "method": row.get("method"),
                      "components": {c: row.get(c) for c in VARIABLE if row.get(c) not in (None, "")}})
    save_lines(db, rid, lines)
    post_run(db, rid, user)


# ================================================================== printing
def payslips_html(db, rid, who="all", lang="en"):
    """Printable payslips (one per page) — bilingual labels, company logo when set."""
    import html as _h
    r = get_run(db, rid)
    ar = lang == "ar"
    L = lambda en, a: a if ar else en  # noqa: E731
    co = _h.escape(db.setting("company_name", "") or "")
    cur = _h.escape(db.setting("currency", "EGP") or "EGP")
    lines = r["lines"] if who == "all" else [x for x in r["lines"] if str(x["employee_id"]) == str(get_employee(db, who)["id"])]
    m = lambda v: f"{v:,.2f}"  # noqa: E731
    pages = []
    for ln in lines:
        e = get_employee(db, ln["employee_id"])
        c = ln["result"] or {}
        nm = lambda x: _h.escape((x.get("name_ar") if ar and x.get("name_ar") else x["name"]))  # noqa: E731
        earn = "".join(f"<tr><td>{nm(x)}</td><td class=n>{m(x['amount'])}</td></tr>" for x in c.get("earnings", []))
        ded = "".join(f"<tr><td>{nm(x)}</td><td class=n>{m(x['amount'])}</td></tr>" for x in c.get("deductions", []))
        info = [(L("Employee", "الموظف"), f"{_h.escape(e['name'])} ({_h.escape(e['code'])})"), (L("Job", "الوظيفة"), _h.escape(e["job_title"] or "—")),
                (L("Department", "القسم"), _h.escape(e["department"] or "—")), (L("Days paid / units", "أيام / وحدات"), m(c.get("days_paid", 0))),
                (L("Insurance no.", "الرقم التأميني"), _h.escape(e["insurance_no"] or "—")), (L("Paid by", "طريقة الصرف"), _h.escape(e["method"] or "—"))]
        pages.append(f"""<section class=slip><header><div><b>{co}</b><br>{L("Payslip", "قسيمة راتب")} — {_h.escape(r['period'])}</div>
          <div class=st>{L("Posted", "مرحّل") if r['status'] == 'posted' else L("Draft", "مسودة")}</div></header>
          <table class=info>{''.join(f'<tr><td>{a}</td><td>{b}</td></tr>' for a, b in info)}</table>
          <div class=cols><table><thead><tr><th>{L("Earnings", "الاستحقاقات")}</th><th class=n>{cur}</th></tr></thead>{earn}
            <tr class=t><td>{L("Gross", "الإجمالي")}</td><td class=n>{m(c.get('gross', 0))}</td></tr></table>
          <table><thead><tr><th>{L("Deductions", "الاستقطاعات")}</th><th class=n>{cur}</th></tr></thead>{ded}
            <tr class=t><td>{L("Total deductions", "إجمالي الاستقطاعات")}</td><td class=n>{m(round(c.get('gross', 0) - c.get('net', 0), 2))}</td></tr></table></div>
          <div class=net>{L("NET PAY", "صافي الأجر")}: {m(c.get('net', 0))} {cur}</div>
          <p class=small>{L("Insurable wage", "أجر الاشتراك")} {m(c.get('insurable', 0))} · {L("employer insurance", "حصة صاحب العمل")} {m(c.get('si_er', 0))} ·
             {L("annual taxable", "الوعاء السنوي")} {m(c.get('annual_taxable', 0))}</p>
          <div class=sign><span>{L("Employee signature", "توقيع الموظف")}: ____________</span><span>{L("Approved by", "اعتماد")}: ____________</span></div></section>""")
    css = """body{font-family:Arial,Tahoma,sans-serif;font-size:12px;color:#111;margin:0}section.slip{padding:18px 26px;page-break-after:always;max-width:780px;margin:auto}
      header{display:flex;justify-content:space-between;border-bottom:2px solid #1f3a5f;padding-bottom:6px;margin-bottom:8px;font-size:14px}.st{color:#555}
      table{border-collapse:collapse;width:100%}td,th{padding:4px 6px;border-bottom:1px solid #e5e7eb;text-align:start}th{background:#1f3a5f;color:#fff}.n{text-align:end;direction:ltr}
      .info td:first-child{color:#555;width:30%}.cols{display:flex;gap:14px;margin-top:10px}.cols table{flex:1}tr.t td{font-weight:700;border-top:2px solid #111}
      .net{margin-top:12px;font-size:16px;font-weight:700;background:#eef2f7;padding:8px;text-align:center}.small{color:#555}.sign{display:flex;justify-content:space-between;margin-top:30px}
      @media print{.noprint{display:none}}"""
    return (f"<!doctype html><html lang='{lang}' dir='{'rtl' if ar else 'ltr'}'><head><meta charset=utf-8><title>{L('Payslips', 'قسائم الرواتب')} {r['period']}</title>"
            f"<style>{css}</style></head><body><div class=noprint style='padding:8px;text-align:center'><button onclick='print()'>{L('Print / Save as PDF', 'طباعة / حفظ PDF')}</button></div>"
            + ("".join(pages) or f"<p style='padding:20px'>{L('Nothing to print', 'لا يوجد ما يُطبع')}</p>") + "</body></html>")


def register_table(db, rid, lang="en"):
    """The month's payroll register for the reports engine (PDF / Excel / Word)."""
    r = get_run(db, rid)
    ar = lang == "ar"
    L = lambda en, a: a if ar else en  # noqa: E731
    heads = [L("Employee", "الموظف"), L("Gross", "الإجمالي"), L("Penalties & deductions", "الجزاءات والخصومات"), L("Advances", "السلف"),
             L("Insurance (employee)", "التأمينات (العامل)"), L("Salary tax", "الضريبة"), L("Martyrs Fund", "الشهداء"), L("Net", "الصافي"),
             L("Insurance (employer)", "التأمينات (صاحب العمل)"), L("Cost to company", "التكلفة")]
    cols = [{"label": heads[0], "align": "left"}] + [{"label": h, "align": "right", "fmt": "money"} for h in heads[1:]]
    rows = []
    for ln in r["lines"]:
        c = ln["result"] or {}
        rows.append({"label": f"{ln['code']} — {ln['name']}", "cells": [f"{ln['code']} — {ln['name']}", c.get("gross", 0), c.get("kept", 0), c.get("advance", 0),
                     c.get("si_emp", 0), c.get("tax", 0), c.get("martyrs", 0), c.get("net", 0), c.get("si_er", 0), c.get("cost", 0)], "style": "item", "indent": 0})
    t = r["totals"]
    rows.append({"label": L("TOTAL", "الإجمالي"), "cells": [L("TOTAL", "الإجمالي"), t["gross"], t["kept"], t["advance"], t["si_emp"], t["tax"], t["martyrs"],
                                                            t["net"], t["si_er"], t["cost"]], "style": "grand", "indent": 0})
    return {"title": L(f"Payroll register — {r['period']}", f"كشف الرواتب — {r['period']}"), "subtitle": L("Status: ", "الحالة: ") + r["status"],
            "columns": cols, "rows": rows, "lang": lang, "name": "payroll_register"}
