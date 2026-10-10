"""Fixed asset register: buy (or register) an asset, depreciate it month by month, sell or scrap it.

The register keeps what an asset is and how it is depreciated. Everything with money is a journal entry whose
reference starts with FA:<asset code> — the purchase (FA:<code>), each depreciation run (FA:<code>:DEP:<from>:<to>)
and the disposal (FA:<code>:DISPOSAL) — so the figures survive export / import and void / reverse like any entry.
Month by month, by the company's depreciation policy (Settings → Depreciation) or the asset's own method:
  straight line — what is left to depreciate ÷ the months left, so a changed cost / residual / life applies from now on;
  declining balance — (book value − residual) × factor ÷ life each month, switching to straight line once that is
  higher, so the asset reaches its residual value exactly at the end of its life.
A life of 0 is never depreciated (land).
"""
import datetime as _dt
import json
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


# ------------------------------------------------------------------ policy
METHODS = ("straight_line", "declining")
CONVENTIONS = ("same_month", "next_month")
POLICY_DEFAULTS = {"method": "straight_line", "factor": 2.0, "convention": "same_month", "life_months": 60,
                   "residual_pct": 0.0, "by_account": {}, "remind": True}


def policy(db):
    """The company's depreciation policy, with defaults per asset account (vehicles 5 years, computers 3 …)."""
    try:
        saved = json.loads(db.setting("depreciation_policy") or "{}")
    except ValueError:
        saved = {}
    return {**POLICY_DEFAULTS, **{k: v for k, v in saved.items() if k in POLICY_DEFAULTS}}


def _rule(d, base=None):
    base = base or {}
    out = {}
    m = text(d.get("method"))
    if m:
        if m not in METHODS:
            raise ValidationError("Depreciation method must be straight_line or declining")
        out["method"] = m
    for k, label, lo, hi in (("factor", "declining factor", 1, 4), ("life_months", "useful life", 0, 1200),
                             ("residual_pct", "residual %", 0, 100)):
        v = d.get(k)
        if v in (None, ""):
            continue
        v = num(v, label)
        if not lo <= v <= hi:
            raise ValidationError(f"{label} must be between {lo} and {hi}")
        out[k] = int(v) if k == "life_months" else v
    return {**base, **out}


def save_policy(db, d):
    cur = policy(db)
    new = _rule(d, {k: cur[k] for k in ("method", "factor", "life_months", "residual_pct")})
    conv = text(d.get("convention")) or cur["convention"]
    if conv not in CONVENTIONS:
        raise ValidationError("First month must be same_month or next_month")
    by = {}
    for code, r in (d.get("by_account") or {}).items():
        a = db.one("SELECT code, subtype FROM accounts WHERE code=?", (text(code),))
        if not a:
            raise ValidationError(f"Unknown account {code}")
        rule = _rule(r or {})
        if rule:
            by[a["code"]] = rule
    pol = {**new, "convention": conv, "by_account": by,
           "remind": d.get("remind", cur["remind"]) not in (False, 0, "0", "false", "")}
    db.set_setting("depreciation_policy", json.dumps(pol))
    return policy(db)


def account_rule(db, account, pol=None):
    """Method, factor, life and residual % a new asset on this account gets unless told otherwise."""
    pol = pol or policy(db)
    r = pol["by_account"].get(account or "", {})
    return {k: r.get(k, pol[k]) for k in ("method", "factor", "life_months", "residual_pct")}


def _method_of(db, a, pol=None):
    rule = account_rule(db, a["account"], pol)
    m = a["method"] if a["method"] in METHODS else rule["method"]
    f = a["factor"] if a["factor"] else rule["factor"]
    return m, float(f or 2)


def month_amounts(cost, salvage, accumulated, months_left, n, method, factor, life):
    """Depreciation (minor units) for the next n months, given what is already booked and the months left in the life."""
    out, done = [], accumulated
    for i in range(n):
        left = months_left - i
        remaining = cost - salvage - done
        if remaining <= 0 or left <= 0:
            break
        amt = remaining / left
        if method == "declining" and life:  # declining on what is left to depreciate; straight line once that is higher
            amt = max(remaining * factor / life, amt)
        amt = remaining if left == 1 else min(int(round(amt)), remaining)
        out.append(amt)
        done += amt
    return out


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
                f"(e.ref=? OR e.ref GLOB ?) ORDER BY e.date, e.id", (f"FA:{code}", acc.ref_prefix(f"FA:{code}:")))


def status(db, a, as_of=None, pol=None):
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
    sl_monthly = int(round(base / a["life_months"])) if a["life_months"] else 0
    start = a["depr_start"] or a["acquired"][:7]
    months_before = int(round(a["opening_depr"] / sl_monthly)) if sl_monthly else 0
    next_month = _add_months(through, 1) if through else _add_months(start, months_before)
    remaining = max(base - accumulated, 0)
    end = _add_months(start, a["life_months"] - 1) if a["life_months"] else None
    method, factor = _method_of(db, a, pol)
    left = max(_months_between(next_month, end), 1) if end else 0
    nxt = month_amounts(a["cost"], a["salvage"], accumulated, left, 1, method, factor, a["life_months"]) if left else []
    monthly = nxt[0] if nxt else 0
    return {"accumulated": to_major(accumulated), "book_value": to_major(a["cost"] - accumulated), "monthly": to_major(monthly),
            "method": method, "factor": factor, "months_left": left if remaining else 0,
            "remaining": to_major(remaining), "booked_through": through, "next_month": next_month if remaining and monthly else None,
            "purchase_entry": purchase, "disposal_entry": disposal, "disposed": bool(disposal),
            "fully_depreciated": bool(a["life_months"]) and remaining == 0, "depreciation": dep_entries,
            "end_month": _add_months(start, a["life_months"] - 1) if a["life_months"] else None}


def _row(db, a, as_of=None, pol=None):
    bu = db.one("SELECT code FROM business_units WHERE id=?", (a["bu_id"],)) if a["bu_id"] else None
    return {**a, "cost": to_major(a["cost"]), "salvage": to_major(a["salvage"]), "opening_depr": to_major(a["opening_depr"]),
            "bu_code": bu["code"] if bu else "", "own_method": a["method"] or "", "own_factor": a["factor"] or "",
            **status(db, a, as_of, pol)}


def register(db, q=None, account=None, state=None, as_of=None):
    w, p = ["1=1"], []
    if q:
        w.append("(code LIKE ? OR name LIKE ? OR location LIKE ? OR serial LIKE ?)")
        p += [f"%{q}%"] * 4
    if account:
        w.append("account=?")
        p.append(account)
    pol = policy(db)
    rows = [_row(db, a, as_of, pol) for a in db.q(f"SELECT * FROM fixed_assets WHERE {' AND '.join(w)} ORDER BY code", p)]
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
    pol = policy(db)
    rule = account_rule(db, account, pol)
    if d.get("salvage") in (None, "") and not old and rule["residual_pct"]:
        salvage = int(round(cost * rule["residual_pct"] / 100))
    else:
        salvage = to_minor(num(d.get("salvage"), "residual value"))
    if salvage > cost:
        raise ValidationError("The residual value cannot be more than the cost")
    life = int(num(d.get("life_months") if d.get("life_months") not in (None, "") else
                   (num(d.get("life_years")) * 12 if d.get("life_years") not in (None, "") else
                    (old["life_months"] if old else rule["life_months"])), "useful life"))
    if d.get("depr_start"):
        start = _month(d.get("depr_start"))
    elif old and old["depr_start"]:
        start = old["depr_start"]
    else:  # the company's first-month rule: the month it was bought, or the month after
        start = _add_months(acquired[:7], 1) if pol["convention"] == "next_month" else acquired[:7]
    method = text(d.get("depr_method")) if "depr_method" in d else (old["method"] if old else "")
    if method and method not in METHODS:
        raise ValidationError("Depreciation method must be straight_line or declining")
    factor = num(d.get("depr_factor") or 0, "declining factor") if "depr_factor" in d else (old["factor"] if old else 0)
    if factor and not 1 <= factor <= 4:
        raise ValidationError("declining factor must be between 1 and 4")
    opening = to_minor(num(d.get("opening_depr"), "depreciation already taken"))
    if opening > cost - salvage:
        raise ValidationError("Depreciation already taken cannot be more than cost − residual value")
    vals = (code, name, account, accum, expense, acquired, cost, salvage, life, start, opening,
            acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), text(d.get("location")), text(d.get("serial")),
            text(d.get("notes"), maxlen=2000), method, factor or 0)
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
                            "location=?, serial=?, notes=?, method=?, factor=? WHERE id=?", vals + (old["id"],))
            return old["id"]
        aid = db.conn.execute("INSERT INTO fixed_assets(code,name,account,accum_account,expense_account,acquired,cost,"
                              "salvage,life_months,depr_start,opening_depr,bu_id,location,serial,notes,method,factor) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals).lastrowid
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
    pol = policy(db)
    for a in db.q("SELECT * FROM fixed_assets ORDER BY code"):
        if ids and a["id"] not in ids and str(a["id"]) not in ids and a["code"] not in ids:
            continue
        if not a["life_months"]:
            continue
        st = status(db, a, pol=pol)
        if st["disposed"] or not st["next_month"] or st["next_month"] > through:
            continue
        frm = st["next_month"]
        end = min(through, st["end_month"]) if st["end_month"] else through
        months = _months_between(frm, end) if end >= frm else 0
        if months <= 0:
            continue
        amount = sum(month_amounts(a["cost"], a["salvage"], to_minor(st["accumulated"]), st["months_left"], months,
                                   st["method"], st["factor"], a["life_months"]))
        if end == st["end_month"]:
            amount = to_minor(st["remaining"])  # the last month takes the rounding
        if amount <= 0:
            continue
        out.append({"id": a["id"], "code": a["code"], "name": a["name"], "from": frm, "to": end, "months": months,
                    "amount": to_major(amount), "expense_account": a["expense_account"], "accum_account": a["accum_account"],
                    "bu_id": a["bu_id"]})
    return {"through": through, "rows": out, "total": round(sum(r["amount"] for r in out), 2)}


def schedule(db, a, pol=None):
    """Year by year: depreciation booked so far, then what the policy will book until the end of the life."""
    st = status(db, a, pol=pol)
    years = {}
    for x in st["depreciation"]:
        y = years.setdefault(x["date"][:4], {"booked": 0, "planned": 0})
        y["booked"] += to_minor(x["amount"])
    if st["next_month"] and a["life_months"]:
        amts = month_amounts(a["cost"], a["salvage"], to_minor(st["accumulated"]), st["months_left"], st["months_left"],
                             st["method"], st["factor"], a["life_months"])
        for i, amt in enumerate(amts):
            years.setdefault(_add_months(st["next_month"], i)[:4], {"booked": 0, "planned": 0})["planned"] += amt
    acc_ = a["opening_depr"]
    rows = []
    for y in sorted(years):
        v = years[y]
        acc_ += v["booked"] + v["planned"]
        rows.append({"year": y, "booked": to_major(v["booked"]), "planned": to_major(v["planned"]),
                     "depreciation": to_major(v["booked"] + v["planned"]), "accumulated": to_major(acc_),
                     "book_value": to_major(a["cost"] - acc_)})
    return {"method": st["method"], "factor": st["factor"], "rows": rows}


def preview_schedule(db, d):
    """The schedule an asset form would give, before saving it."""
    pol = policy(db)
    acct = text(d.get("account"))
    rule = account_rule(db, acct, pol)
    cost = to_minor(num(d.get("cost"), "cost"))
    salvage = (to_minor(num(d.get("salvage"), "residual value")) if d.get("salvage") not in (None, "")
               else int(round(cost * rule["residual_pct"] / 100)))
    life = int(num(d.get("life_months"), "useful life")) if d.get("life_months") not in (None, "") else rule["life_months"]
    acquired = acc.clean_date(d.get("acquired") or _dt.date.today().isoformat(), "date acquired")
    start = _month(d.get("depr_start")) if d.get("depr_start") else (
        _add_months(acquired[:7], 1) if pol["convention"] == "next_month" else acquired[:7])
    real = get(db, d["id"]) if d.get("id") else None
    fake = {"code": real["code"] if real else "__preview__", "account": acct, "cost": cost, "salvage": min(salvage, cost), "life_months": life,
            "depr_start": start, "acquired": acquired, "opening_depr": to_minor(num(d.get("opening_depr"), "opening")),
            "method": text(d.get("depr_method")), "factor": num(d.get("depr_factor") or 0, "factor"),
            "accum_account": real["accum_account"] if real else ""}
    out = schedule(db, fake, pol)
    return {**out, "start": start, "salvage": to_major(fake["salvage"]), "life_months": life,
            "end_month": _add_months(start, life - 1) if life else None}


def due(db, through=None):
    """Depreciation not booked yet up to last month — for the reminder on the dashboard."""
    if not policy(db)["remind"]:
        return None
    last = _add_months(_dt.date.today().isoformat()[:7], -1)
    p = plan_depreciation(db, through or last)
    return {"through": p["through"], "count": len(p["rows"]), "total": p["total"]} if p["rows"] else None


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
