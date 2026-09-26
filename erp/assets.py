"""Fixed asset register: buy (or register) an asset, depreciate it month by month, sell or scrap it.

The register keeps what an asset is and how it is depreciated. Everything with money is a journal entry whose
reference starts with FA:<asset code> — the purchase (FA:<code>), each depreciation run (FA:<code>:DEP:<from>:<to>)
and the disposal (FA:<code>:DISPOSAL) — so the figures survive export / import and void / reverse like any entry.
Straight-line, month by month: (cost − residual value) ÷ useful life in months; a life of 0 is never depreciated (land).
"""
import datetime as _dt
import re

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
LIVE_REF = f"{acc.LIVE} AND e.reversed_by IS NULL AND e.reversal_of IS NULL"


def _month(v, field="month"):
    v = text(v)[:7]
    if not MONTH_RE.match(v) or not 1 <= int(v[5:]) <= 12:
        raise ValidationError(f"{field} must be YYYY-MM")
    return v


def _add_months(m, n):
    y, mo = int(m[:4]), int(m[5:]) + n
    y, mo = y + (mo - 1) // 12, (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"


def _months_between(a, b):
    """Months a..b inclusive."""
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:]) - int(a[5:]) + 1


def _month_end(m):
    y, mo = int(m[:4]), int(m[5:])
    nxt = _dt.date(y + (mo == 12), mo % 12 + 1, 1)
    return (nxt - _dt.timedelta(days=1)).isoformat()


def _role_or(db, role, fallback=None):
    r = db.one("SELECT code FROM accounts WHERE role=?", (role,))
    return r["code"] if r else fallback


def _defaults(db, account):
    """Accumulated and expense accounts that go with a cost account (depreciation, or amortization for intangibles)."""
    a = db.one("SELECT subtype FROM accounts WHERE code=?", (account,))
    if a and a["subtype"] == "intangible":
        return _role_or(db, "accum_amortization"), _role_or(db, "amortization")
    return _role_or(db, "accum_depreciation"), _role_or(db, "depreciation")


def next_code(db):
    n = 1
    for r in db.q("SELECT code FROM fixed_assets WHERE code LIKE 'FA-%'"):
        tail = r["code"][3:]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"FA-{n:04d}"


# ------------------------------------------------------------------- status
def _entries(db, code):
    return db.q(f"SELECT e.id, e.date, e.kind, e.ref FROM journal_entries e WHERE {LIVE_REF} AND "
                f"(e.ref=? OR e.ref LIKE ?) ORDER BY e.date, e.id", (f"FA:{code}", f"FA:{code}:%"))


def status(db, a, as_of=None):
    """Figures for one register row: accumulated depreciation, book value, months booked, disposal."""
    code = a["code"]
    booked, through, purchase, disposal, dep_entries = 0, None, None, None, []
    for e in _entries(db, code):
        if as_of and e["date"] > as_of:
            continue
        parts = e["ref"].split(":")
        if len(parts) == 2:
            purchase = e["id"]
        elif len(parts) >= 5 and parts[2] == "DEP":
            amt = db.one("SELECT COALESCE(SUM(credit),0) AS n FROM journal_lines WHERE entry_id=? AND account=?",
                         (e["id"], a["accum_account"]))["n"]
            booked += amt
            through = max(through or parts[4], parts[4])
            dep_entries.append({"id": e["id"], "date": e["date"], "from": parts[3], "to": parts[4], "amount": to_major(amt)})
        elif len(parts) >= 3 and parts[2] == "DISPOSAL":
            disposal = e["id"]
    accumulated = a["opening_depr"] + booked
    base = max(a["cost"] - a["salvage"], 0)
    monthly = int(round(base / a["life_months"])) if a["life_months"] else 0
    start = a["depr_start"] or a["acquired"][:7]
    months_before = int(round(a["opening_depr"] / monthly)) if monthly else 0
    next_month = _add_months(through, 1) if through else _add_months(start, months_before)
    remaining = max(base - accumulated, 0)
    return {"accumulated": to_major(accumulated), "book_value": to_major(a["cost"] - accumulated), "monthly": to_major(monthly),
            "remaining": to_major(remaining), "booked_through": through, "next_month": next_month if remaining and monthly else None,
            "purchase_entry": purchase, "disposal_entry": disposal, "disposed": bool(disposal),
            "fully_depreciated": bool(a["life_months"]) and remaining == 0, "depreciation": dep_entries,
            "end_month": _add_months(start, a["life_months"] - 1) if a["life_months"] else None}


def _row(db, a, as_of=None):
    bu = db.one("SELECT code FROM business_units WHERE id=?", (a["bu_id"],)) if a["bu_id"] else None
    return {**a, "cost": to_major(a["cost"]), "salvage": to_major(a["salvage"]), "opening_depr": to_major(a["opening_depr"]),
            "bu_code": bu["code"] if bu else "", **status(db, a, as_of)}


def register(db, q=None, account=None, state=None, as_of=None):
    w, p = ["1=1"], []
    if q:
        w.append("(code LIKE ? OR name LIKE ? OR location LIKE ? OR serial LIKE ?)")
        p += [f"%{q}%"] * 4
    if account:
        w.append("account=?")
        p.append(account)
    rows = [_row(db, a, as_of) for a in db.q(f"SELECT * FROM fixed_assets WHERE {' AND '.join(w)} ORDER BY code", p)]
    if state == "active":
        rows = [r for r in rows if not r["disposed"]]
    elif state == "disposed":
        rows = [r for r in rows if r["disposed"]]
    tot = {k: round(sum(r[k] for r in rows if not r["disposed"]), 2) for k in ("cost", "accumulated", "book_value")}
    return {"rows": rows, "totals": tot, "next_code": next_code(db)}


def get(db, aid):
    a = db.one("SELECT * FROM fixed_assets WHERE id=? OR code=?", (aid if str(aid).isdigit() else -1, text(aid)))
    if not a:
        raise ValidationError("Asset not found")
    return a


# ------------------------------------------------------------------- save
def save(db, d, aid=None, user=""):
    """Add an asset (optionally booking its purchase) or change its details and depreciation policy."""
    old = get(db, aid) if aid else None
    code = text(d.get("code"), maxlen=30) or (old["code"] if old else next_code(db))
    name = text(d.get("name"), "name", required=True)
    account = acc.account_code(db, d.get("account") or (old and old["account"]), types=("Asset",), field="asset account")
    if db.one("SELECT subtype FROM accounts WHERE code=?", (account,))["subtype"] not in ("fixed_asset", "intangible",
                                                                                        "investment", "other_noncurrent_asset"):
        raise ValidationError("Choose a fixed-asset or intangible account")
    da, de = _defaults(db, account)
    accum = acc.account_code(db, d.get("accum_account") or da, types=("Asset",), field="accumulated depreciation account")
    expense = acc.account_code(db, d.get("expense_account") or de, types=("Expense", "COGS"), field="depreciation expense account")
    acquired = acc.clean_date(d.get("acquired") or (old and old["acquired"]), "date acquired")
    cost = to_minor(num(d.get("cost"), "cost", allow_zero=False))
    salvage = to_minor(num(d.get("salvage"), "residual value"))
    if salvage > cost:
        raise ValidationError("The residual value cannot be more than the cost")
    life = int(num(d.get("life_months") if d.get("life_months") not in (None, "") else
                   (num(d.get("life_years")) * 12 if d.get("life_years") not in (None, "") else 60), "useful life"))
    start = _month(d.get("depr_start")) if d.get("depr_start") else acquired[:7]
    opening = to_minor(num(d.get("opening_depr"), "depreciation already taken"))
    if opening > cost - salvage:
        raise ValidationError("Depreciation already taken cannot be more than cost − residual value")
    vals = (code, name, account, accum, expense, acquired, cost, salvage, life, start, opening,
            acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), text(d.get("location")), text(d.get("serial")),
            text(d.get("notes"), maxlen=2000))
    with db.tx():
        dup = db.one("SELECT id FROM fixed_assets WHERE code=?", (code,))
        if dup and (not old or dup["id"] != old["id"]):
            raise ValidationError(f"Asset code {code} is already used")
        if old:  # a new cost, residual value or life applies to the depreciation not booked yet
            st = status(db, old)
            if code != old["code"] and _entries(db, old["code"]):
                raise ValidationError("This asset has journal entries — its code cannot change")
            if st["depreciation"] and accum != old["accum_account"]:
                raise ValidationError("Depreciation is already booked to the old accumulated account — it cannot change")
            db.conn.execute("UPDATE fixed_assets SET code=?, name=?, account=?, accum_account=?, expense_account=?, "
                            "acquired=?, cost=?, salvage=?, life_months=?, depr_start=?, opening_depr=?, bu_id=?, "
                            "location=?, serial=?, notes=? WHERE id=?", vals + (old["id"],))
            return old["id"]
        aid = db.conn.execute("INSERT INTO fixed_assets(code,name,account,accum_account,expense_account,acquired,cost,"
                              "salvage,life_months,depr_start,opening_depr,bu_id,location,serial,notes) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals).lastrowid
        how = d.get("pay") or "none"
        if how != "none":
            _book_purchase(db, d, code, name, account, cost, acquired, how, vals[11], user)
        return aid


def _book_purchase(db, d, code, name, account, cost, date, how, bu_id, user):
    party = text(d.get("vendor"))
    if how == "partner":  # contributed by a partner in kind: their capital goes up
        from . import equity
        p = equity.partner(db, d.get("partner"))
        lines = [{"account": account, "debit": to_major(cost)},
                 {"account": db.acct("capital"), "credit": to_major(cost), "party": p["name"]}]
        party = p["name"]
        kind = "Capital Contribution"
    else:
        vat_code, vat, wht_code, wht = acc._purchase_taxes(db, d, cost)
        due = cost + vat - wht
        paid = due if how == "cash" else to_minor(num(d.get("paid"), "paid now"))
        if (paid < due or wht) and not party:
            raise ValidationError("Name the supplier so the unpaid balance / withholding can be tracked")
        payable = acc.account_code(db, d.get("payable_account") or db.acct("ap"), types=("Liability",))
        lines = acc._purchase_lines(db, d, account, cost, vat, wht, payable, paid)
        kind = "Asset Purchase"
    acc._insert_entry(db, date=date, kind=kind, memo=f"{name} ({code})", bu_id=bu_id, party=party, ref=f"FA:{code}",
                      lines=lines, meta={"asset": code, "method": text(d.get("method")), "vat_code": text(d.get("vat_code")),
                                         "wht_code": text(d.get("wht_code"))}, user=user)


def delete(db, aid):
    a = get(db, aid)
    if _entries(db, a["code"]):
        raise ValidationError("This asset has journal entries (purchase, depreciation or disposal) — void them first, "
                              "or dispose of the asset instead")
    db.x("DELETE FROM fixed_assets WHERE id=?", (a["id"],))


# ------------------------------------------------------------ depreciation
def plan_depreciation(db, through, ids=None):
    """What a depreciation run up to a month would book, per asset (catching up any months not booked yet)."""
    through = _month(through, "depreciate up to")
    out = []
    for a in db.q("SELECT * FROM fixed_assets ORDER BY code"):
        if ids and a["id"] not in ids and str(a["id"]) not in ids and a["code"] not in ids:
            continue
        if not a["life_months"]:
            continue
        st = status(db, a)
        if st["disposed"] or not st["next_month"] or st["next_month"] > through:
            continue
        frm = st["next_month"]
        end = min(through, st["end_month"]) if st["end_month"] else through
        months = _months_between(frm, end) if end >= frm else 0
        if months <= 0:
            continue
        amount = min(to_minor(st["monthly"]) * months, to_minor(st["remaining"]))
        if end == st["end_month"]:
            amount = to_minor(st["remaining"])  # the last month takes the rounding
        if amount <= 0:
            continue
        out.append({"id": a["id"], "code": a["code"], "name": a["name"], "from": frm, "to": end, "months": months,
                    "amount": to_major(amount), "expense_account": a["expense_account"], "accum_account": a["accum_account"],
                    "bu_id": a["bu_id"]})
    return {"through": through, "rows": out, "total": round(sum(r["amount"] for r in out), 2)}


def run_depreciation(db, through, ids=None, user=""):
    """Book the depreciation up to a month: one entry per asset, dated the last day of that month."""
    plan = plan_depreciation(db, through, ids)
    if not plan["rows"]:
        raise ValidationError(f"Nothing to depreciate up to {plan['through']}")
    made = []
    with db.tx():
        for r in plan["rows"]:
            span = r["from"] if r["from"] == r["to"] else f"{r['from']} to {r['to']}"
            made.append(acc._insert_entry(
                db, date=_month_end(r["to"]), kind="Depreciation", memo=f"Depreciation {r['name']} ({r['code']}) — {span}",
                bu_id=r["bu_id"], ref=f"FA:{r['code']}:DEP:{r['from']}:{r['to']}",
                lines=[{"account": r["expense_account"], "debit": r["amount"]},
                       {"account": r["accum_account"], "credit": r["amount"]}],
                meta={"asset": r["code"], "months": r["months"]}, user=user))
    return {"entries": made, "total": plan["total"], "count": len(made)}


# ---------------------------------------------------------------- disposal
def plan_disposal(db, aid, d):
    a = get(db, aid)
    st = status(db, a)
    if st["disposed"]:
        raise ValidationError("This asset is already sold / written off")
    date = acc.clean_date(d.get("date"))
    notes, catch_up = [], None
    if d.get("depreciate_first", True) not in (False, 0, "0", "false") and a["life_months"]:
        p = plan_depreciation(db, date[:7], [a["id"]])
        if p["rows"]:
            catch_up = p["rows"][0]
            notes.append(f"Depreciation {catch_up['from']} to {catch_up['to']} ({catch_up['amount']:,.2f}) is booked first.")
    accumulated = to_minor(st["accumulated"]) + (to_minor(catch_up["amount"]) if catch_up else 0)
    proceeds = to_minor(num(d.get("proceeds"), "sale price"))
    book = a["cost"] - accumulated
    gain = proceeds - book
    lines = []
    if proceeds:
        lines.append({"account": acc.money_account(db, d.get("method"), d.get("received_into")) if not d.get("on_credit")
                      else db.acct("ar"), "debit": to_major(proceeds)})
    if accumulated:
        lines.append({"account": a["accum_account"], "debit": to_major(accumulated)})
    lines.append({"account": a["account"], "credit": to_major(a["cost"])})
    if gain > 0:
        lines.append({"account": db.acct("disposal_gain"), "credit": to_major(gain)})
    elif gain < 0:
        lines.append({"account": db.acct("disposal_loss"), "debit": to_major(-gain)})
    notes.append(f"Book value {to_major(book):,.2f}; {'sold' if proceeds else 'written off'} for {to_major(proceeds):,.2f} → "
                 f"{'gain' if gain >= 0 else 'loss'} of {to_major(abs(gain)):,.2f}.")
    return {"asset": a, "date": date, "catch_up": catch_up, "lines": lines, "gain": to_major(gain), "notes": notes,
            "memo": text(d.get("memo")) or f"{'Sale' if proceeds else 'Write-off'} of {a['name']} ({a['code']})"}


def dispose(db, aid, d, user=""):
    with db.tx():
        pl = plan_disposal(db, aid, d)
        a = pl["asset"]
        if pl["catch_up"]:
            run_depreciation(db, pl["catch_up"]["to"], [a["id"]], user)
        return acc._insert_entry(db, date=pl["date"], kind="Asset Disposal", memo=pl["memo"], bu_id=a["bu_id"],
                                 party=text(d.get("buyer")), ref=f"FA:{a['code']}:DISPOSAL", lines=pl["lines"],
                                 meta={"asset": a["code"], "gain": pl["gain"]}, user=user)
