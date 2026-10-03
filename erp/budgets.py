"""Budgets: twelve monthly amounts per profit-and-loss account, for the whole company (bu_id 0 = shared / company
level) or for one business unit, and the budget-vs-actual comparison.

Amounts follow each account's normal side (sales as a positive income, costs as a positive cost), so a variance is
"favourable" when income beats its budget or a cost stays under it."""
import datetime as _dt
import json

from . import accounting as acc
from .accounting import ValidationError
from .db import to_major, to_minor

PNL = ("Revenue", "COGS", "Expense")
SECTIONS = [("Revenue", "Income", "الإيرادات"), ("COGS", "Cost of sales", "تكلفة المبيعات"), ("Expense", "Expenses", "المصروفات")]


def _year(y):
    try:
        y = int(y or _dt.date.today().year)
    except (TypeError, ValueError):
        raise ValidationError("Year must be a number such as 2026")
    if not 1990 <= y <= 2200:
        raise ValidationError("Year out of range")
    return y


def _bu(db, bu):
    """'' / None = whole company (all budgets), 0 = company level only, else a business unit id or code."""
    if bu in (None, "", "all"):
        return None
    if str(bu) in ("0", "none"):
        return 0
    r = db.one("SELECT id FROM business_units WHERE id=? OR code=?", (int(bu) if str(bu).isdigit() else -1, str(bu)))
    if not r:
        raise ValidationError(f"Business unit not found: {bu}")
    return r["id"]


def _accounts(db):
    return db.q(f"SELECT code, name, name_ar, type, subtype, normal FROM accounts WHERE type IN ({','.join('?' * len(PNL))}) "
                "AND active=1 ORDER BY code", PNL)


def _actuals(db, year, bu):
    """{account: [12 amounts in minor units on the account's normal side]}"""
    w, p = [acc.LIVE, "e.date>=?", "e.date<=?"], [f"{year}-01-01", f"{year}-12-31"]
    if bu == 0:
        w.append("e.bu_id IS NULL")
    elif bu:
        w.append("e.bu_id=?")
        p.append(bu)
    out = {}
    for r in db.q(f"""SELECT l.account, CAST(substr(e.date,6,2) AS INTEGER) m, a.normal, SUM(l.debit) dr, SUM(l.credit) cr
                      FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                      WHERE {' AND '.join(w)} AND a.type IN ({','.join('?' * len(PNL))}) GROUP BY l.account, m""", p + list(PNL)):
        v = (r["dr"] - r["cr"]) if r["normal"] == "Debit" else (r["cr"] - r["dr"])
        out.setdefault(r["account"], [0] * 12)[r["m"] - 1] += v
    return out


def _budgets(db, year, bu):
    w, p = ["year=?"], [year]
    if bu is not None:
        w.append("bu_id=?")
        p.append(bu)
    out = {}
    for r in db.q(f"SELECT account, months FROM budgets WHERE {' AND '.join(w)}", p):
        m = (json.loads(r["months"] or "[]") + [0] * 12)[:12]
        cur = out.setdefault(r["account"], [0] * 12)
        for i in range(12):
            cur[i] += int(m[i] or 0)
    return out


def grid(db, year=None, bu=None):
    """Every P&L account with its twelve budget amounts (editable) and last year's actual for reference."""
    year, bu_id = _year(year), _bu(db, bu)
    bud = _budgets(db, year, 0 if bu_id is None else bu_id)
    last = _actuals(db, year - 1, bu_id)
    rows = []
    for a in _accounts(db):
        m = bud.get(a["code"], [0] * 12)
        rows.append({**a, "months": [to_major(x) for x in m], "total": to_major(sum(m)),
                     "last_year": to_major(sum(last.get(a["code"], [0] * 12)))})
    years = [r["year"] for r in db.q("SELECT DISTINCT year FROM budgets ORDER BY year")]
    return {"year": year, "bu_id": 0 if bu_id is None else bu_id, "rows": rows, "years": years,
            "total": {s: to_major(sum(sum(bud.get(a["code"], [0] * 12)) for a in _accounts(db) if a["type"] == s)) for s in PNL}}


def save(db, year, bu, rows):
    """rows: [{account, months: [12 amounts]} or {account, total} (spread evenly)]. A row of zeros removes it."""
    year, bu_id = _year(year), _bu(db, bu) or 0
    valid = {a["code"] for a in _accounts(db)}
    n = 0
    with db.tx():
        for r in rows or []:
            code = str(r.get("account") or "").strip()
            if code not in valid:
                raise ValidationError(f"Account {code} is not an active income, cost or expense account")
            if r.get("months") is not None:
                months = list(r["months"]) + [0] * 12
                months = [to_minor(acc.num(v or 0, "amount", allow_negative=True)) for v in months[:12]]
            else:
                tot = to_minor(acc.num(r.get("total") or 0, "amount", allow_negative=True))
                months = [tot // 12] * 12
                months[11] += tot - sum(months)
            if any(months):
                db.x("INSERT INTO budgets(year,account,bu_id,months,notes) VALUES (?,?,?,?,?) ON CONFLICT(year,account,bu_id) "
                     "DO UPDATE SET months=excluded.months, notes=excluded.notes",
                     (year, code, bu_id, json.dumps(months), acc.text(r.get("notes"), maxlen=300)))
            else:
                db.x("DELETE FROM budgets WHERE year=? AND account=? AND bu_id=?", (year, code, bu_id))
            n += 1
    return n


def seed(db, year, bu, from_year=None, growth_pct=0, replace=False):
    """Fill a year's budget from another year's actual results, month by month, plus a growth %."""
    year, bu_id = _year(year), _bu(db, bu) or 0
    from_year = _year(from_year or year - 1)
    f = 1 + acc.num(growth_pct or 0, "growth %", allow_negative=True) / 100
    act = _actuals(db, from_year, bu_id or None)   # a company-level budget starts from the whole company's results
    have = {r["account"] for r in db.q("SELECT account FROM budgets WHERE year=? AND bu_id=?", (year, bu_id))}
    rows = [{"account": code, "months": [to_major(int(round(v * f))) for v in m]} for code, m in act.items()
            if any(m) and (replace or code not in have)]
    if not rows:
        raise ValidationError(f"No results in {from_year} to copy" + ("" if replace else " (or every account already has a budget)"))
    return save(db, year, bu_id, rows)


def report(db, year=None, bu=None, through=None):
    """Budget vs actual for the months up to `through` (default: this month for the current year, else December)."""
    year, bu_id = _year(year), _bu(db, bu)
    today = _dt.date.today()
    through = int(through or (today.month if year == today.year else 12))
    if not 1 <= through <= 12:
        raise ValidationError("Month must be 1 to 12")
    bud, act = _budgets(db, year, bu_id), _actuals(db, year, bu_id)
    names = {a["code"]: a for a in db.q("SELECT code, name, name_ar, type, normal FROM accounts")}
    sections = []
    totals = {}
    for typ, en, ar in SECTIONS:
        rows = []
        for code in sorted(set(bud) | set(act)):
            a = names.get(code)
            if not a or a["type"] != typ:
                continue
            b, x = bud.get(code, [0] * 12), act.get(code, [0] * 12)
            if not any(b) and not any(x):
                continue
            by, ay = sum(b[:through]), sum(x[:through])
            good = (ay - by) if a["normal"] == "Credit" else (by - ay)   # credit-side accounts add to profit
            rows.append({"account": code, "name": a["name"], "name_ar": a["name_ar"], "budget": to_major(by), "actual": to_major(ay),
                         "variance": to_major(good), "pct": round(good / abs(by) * 100, 1) if by else None,
                         "used_pct": round(ay / by * 100, 1) if by else None, "year_budget": to_major(sum(b)),
                         "months_budget": [to_major(v) for v in b], "months_actual": [to_major(v) for v in x]})
        side = "Credit" if typ == "Revenue" else "Debit"   # a contra account (e.g. sales returns) reduces its section
        sign = lambda r: 1 if names[r["account"]]["normal"] == side else -1  # noqa: E731
        t = {k: round(sum(r[k] * sign(r) for r in rows), 2) for k in ("budget", "actual", "year_budget")}
        t["variance"] = round(sum(r["variance"] for r in rows), 2)
        totals[typ] = t
        sections.append({"type": typ, "label": en, "label_ar": ar, "rows": rows, "total": t})
    profit = {k: round(totals["Revenue"][k] - totals["COGS"][k] - totals["Expense"][k], 2) for k in ("budget", "actual", "year_budget")}
    profit["variance"] = round(profit["actual"] - profit["budget"], 2)
    has_budget = bool(db.one("SELECT 1 FROM budgets WHERE year=?", (year,)))
    return {"year": year, "through": through, "bu_id": bu_id, "sections": sections, "profit": profit, "has_budget": has_budget}


def export_rows(db):
    out = []
    for r in db.q("SELECT b.*, u.code AS bu_code FROM budgets b LEFT JOIN business_units u ON u.id=b.bu_id ORDER BY year, bu_id, account"):
        m = (json.loads(r["months"] or "[]") + [0] * 12)[:12]
        out.append({"year": r["year"], "account": r["account"], "bu_code": r["bu_code"] or "", "notes": r["notes"] or "",
                    **{f"m{i + 1}": to_major(v) for i, v in enumerate(m)}})
    return out


def import_row(db, r):
    """One Budgets sheet row: a year, an account, an optional business unit and twelve monthly amounts (or a total)."""
    months = [r.get(f"m{i}") for i in range(1, 13)]
    row = {"account": str(r.get("account") or "").split(" ")[0], "notes": r.get("notes")}
    if any(v not in (None, "") for v in months):
        row["months"] = [v or 0 for v in months]
    else:
        row["total"] = r.get("total") or 0
    return save(db, r.get("year"), r.get("bu_code") or 0, [row])
