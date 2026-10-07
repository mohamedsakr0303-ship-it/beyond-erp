"""Loans the company takes (borrowed) or gives (lent): terms, repayment schedule, interest, payments.

The loan card keeps the terms. Every money movement is a journal entry whose reference starts with LN:<code>
(LN:<code>:DRAW, :PAY, :INT, :FEE, :OFF), so balances come straight from the books and survive export / import,
void and reverse like any entry. Registering a loan that is already in the books keeps its balance as an opening
figure (nothing is posted for it).

Interest:
  none       interest-free
  declining  annual rate on the outstanding balance (per installment period in the schedule; per day otherwise)
  flat       annual rate on the original amount for the whole term, spread evenly over the installments
  fixed      a fixed total amount of interest, spread evenly
Schedules: equal installments, equal principal, bullet (interest only, principal at the end), custom rows, or none
(flexible — pay whatever, whenever).
"""
import datetime as _dt
import json

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

LIVE_REF = f"{acc.LIVE} AND e.reversed_by IS NULL AND e.reversal_of IS NULL"
DIRECTIONS = ("borrowed", "lent")
INTEREST_TYPES = ("none", "declining", "flat", "fixed")
SCHEDULES = ("equal", "equal_principal", "bullet", "custom", "none")
KINDS = {"borrowed": {"DRAW": "Loan Received", "PAY": "Loan Repayment", "INT": "Loan Interest", "FEE": "Loan Charges",
                      "OFF": "Loan Forgiven"},
         "lent": {"DRAW": "Loan Given", "PAY": "Loan Collection", "INT": "Loan Interest", "FEE": "Loan Charges",
                  "OFF": "Loan Written Off"}}
ALL_KINDS = sorted({k for d in KINDS.values() for k in d.values()})


def _date(v, field="date"):
    return _dt.date.fromisoformat(acc.clean_date(v, field))


def _add_months(d, n, day=None):
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    last = (_dt.date(y + (m == 12), m % 12 + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(y, m, min(day or d.day, last))


def _role_or(db, role, fallback=None):
    r = db.one("SELECT code FROM accounts WHERE role=?", (role,))
    return r["code"] if r else fallback


def _pct(v, field="interest rate"):
    """Percent as typed (12 = 12 %)."""
    x = num(v, field)
    if x < 0 or x > 100:
        raise ValidationError(f"{field} must be between 0 and 100 %")
    return round(x / 100, 8)


def next_code(db):
    n = 1
    for r in db.q("SELECT code FROM loans WHERE code LIKE 'LN-%'"):
        tail = r["code"][3:]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"LN-{n:04d}"


def get(db, lid):
    ln = db.one("SELECT * FROM loans WHERE id=? OR code=?", (lid if str(lid).isdigit() else -1, text(lid)))
    if not ln:
        raise ValidationError("Loan not found")
    ln["schedule"] = json.loads(ln["schedule"] or "[]")
    return ln


def default_accounts(db, direction, term_months=12):
    long = (term_months or 0) > 12
    if direction == "borrowed":
        return {"account": _role_or(db, "long_term_debt" if long else "short_term_debt"),
                "interest_account": _role_or(db, "interest"), "accrued_account": _role_or(db, "accrued_interest"),
                "fee_account": _role_or(db, "bank_charges", _role_or(db, "other_overhead")),
                "writeoff_account": _role_or(db, "other_income")}
    return {"account": _role_or(db, "loans_receivable_lt" if long else "loans_receivable", _role_or(db, "loans_receivable")),
            "interest_account": _role_or(db, "interest_income"), "accrued_account": _role_or(db, "interest_receivable"),
            "fee_account": _role_or(db, "other_income"), "writeoff_account": _role_or(db, "bad_debt")}


# ------------------------------------------------------------------ ledger
def _entries(db, code):
    return db.q(f"SELECT e.* FROM journal_entries e WHERE {LIVE_REF} AND (e.ref=? OR e.ref LIKE ?) ORDER BY e.date, e.id",
                (f"LN:{code}", f"LN:{code}:%"))


def _sign(ln):
    """+1 when a debit raises the balance of the loan-side accounts (money lent), −1 when a credit does."""
    return 1 if ln["direction"] == "lent" else -1


def _sum(db, ln, account, types=None, upto=None):
    """Signed movement on one account across this loan's entries (+ = more owed on the loan side)."""
    if not account:
        return 0
    w = [LIVE_REF, "(e.ref=? OR e.ref LIKE ?)", "l.account=?"]
    p = [f"LN:{ln['code']}", f"LN:{ln['code']}:%", account]
    if types:
        w.append("(" + " OR ".join("e.ref LIKE ?" for _ in types) + ")")
        p += [f"LN:{ln['code']}:{t}%" for t in types]
    if upto:
        w.append("e.date<=?")
        p.append(upto)
    r = db.one(f"SELECT COALESCE(SUM(l.debit-l.credit),0) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
               f"WHERE {' AND '.join(w)}", p)
    return r["n"] * _sign(ln)


def principal_balance(db, ln, upto=None):
    return ln["opening_balance"] + _sum(db, ln, ln["account"], upto=upto)


def accrued_balance(db, ln):
    return _sum(db, ln, ln["accrued_account"])


def interest_recognized(db, ln):
    """Interest booked to the income statement so far (expense when borrowed, income when lent)."""
    return -_sum(db, ln, ln["interest_account"])


def _last_interest_date(db, ln):
    last = None
    for e in _entries(db, ln["code"]):
        to = json.loads(e["meta"] or "{}").get("interest_to")
        if to:
            last = max(last or to, to)
    return last


def _interest_start(ln):
    return _date(ln["opening_date"] if ln["opening_balance"] and ln["opening_date"] else ln["start_date"])


def earned(db, ln, upto):
    """Interest the loan has earned from its start (or opening) up to a date."""
    upto = _date(upto) if isinstance(upto, str) else upto
    start = _interest_start(ln)
    if upto <= start or ln["interest_type"] == "none":
        return 0
    term_end = _add_months(_date(ln["start_date"]), ln["term_months"] or 12)
    if ln["interest_type"] == "fixed":
        span = max((term_end - start).days, 1)
        return int(round(ln["fixed_interest"] * min((upto - start).days, span) / span))
    if ln["interest_type"] == "flat":
        base = ln["principal"] or ln["opening_balance"]
        days = min((upto - start).days, max((term_end - start).days, 0))
        return int(round(base * ln["rate"] * days / 365))
    # declining: day by day on the outstanding balance
    moves = {}
    for r in db.q(f"""SELECT e.date, SUM(l.debit-l.credit) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                      WHERE {LIVE_REF} AND (e.ref=? OR e.ref LIKE ?) AND l.account=? AND e.date<=? GROUP BY e.date""",
                  (f"LN:{ln['code']}", f"LN:{ln['code']}:%", ln["account"], upto.isoformat())):
        moves[r["date"]] = r["n"] * _sign(ln)
    bal = ln["opening_balance"] + sum(v for k, v in moves.items() if k <= start.isoformat())
    total, day = 0.0, start + _dt.timedelta(days=1)
    while day <= upto:
        bal += moves.get(day.isoformat(), 0)
        total += max(bal, 0) * ln["rate"] / 365
        day += _dt.timedelta(days=1)
    return int(round(total))


# ---------------------------------------------------------------- schedule
def build_schedule(ln):
    """Installments [{n, due, principal, interest, payment, balance}] in minor units, from the loan's terms."""
    kind = ln["schedule_type"]
    if kind == "none":
        return []
    if kind == "custom":
        bal = ln["principal"] or ln["opening_balance"]
        out = []
        for i, r in enumerate(sorted(ln["schedule"], key=lambda x: x.get("due") or ""), 1):
            p, it = int(r.get("principal") or 0), int(r.get("interest") or 0)
            bal -= p
            out.append({"n": i, "due": r["due"], "principal": p, "interest": it, "payment": p + it, "balance": bal})
        return out
    P = ln["principal"] or ln["opening_balance"]
    freq = ln["frequency"] or 1
    n = max(1, (ln["term_months"] or freq) // freq)
    start = _date(ln["start_date"])
    first = _date(ln["first_due"]) if ln["first_due"] else _add_months(start, freq)
    dues = [_add_months(first, k * freq, first.day) for k in range(n)]
    i = ln["rate"] * freq / 12 if ln["interest_type"] == "declining" else 0.0
    share = 0
    if ln["interest_type"] == "flat":
        share = P * ln["rate"] * (ln["term_months"] or 12) / 12 / n
    elif ln["interest_type"] == "fixed":
        share = ln["fixed_interest"] / n
    rows, bal, paid_share = [], P, 0
    annuity = (P * i / (1 - (1 + i) ** -n)) if i else None
    for k in range(n):
        last = k == n - 1
        interest = int(round(bal * i)) if i else (int(round(share * (k + 1))) - paid_share)
        paid_share += 0 if i else interest
        if kind == "bullet":
            principal = bal if last else 0
        elif kind == "equal_principal" or not annuity:
            principal = bal if last else int(round(P / n))
        else:
            principal = bal if last else int(round(annuity)) - interest
        principal = max(min(principal, bal), 0)
        bal -= principal
        rows.append({"n": k + 1, "due": dues[k].isoformat(), "principal": principal, "interest": interest,
                     "payment": principal + interest, "balance": bal})
    return rows


def preview_schedule(d):
    """The installments a set of terms would give (for the form, before saving)."""
    itype = d.get("interest_type") or "none"
    if itype not in INTEREST_TYPES:
        itype = "none"
    stype = d.get("schedule_type") or "equal"
    if stype not in SCHEDULES:
        stype = "equal"
    try:
        start = acc.clean_date(d.get("start_date") or acc.today())
        first = acc.clean_date(d.get("first_due")) if d.get("first_due") else ""
    except ValidationError:
        return {"rows": [], "totals": {}}
    principal = to_minor(num(d.get("principal") or d.get("opening_balance")))
    custom = []
    for r in d.get("schedule") or []:
        if isinstance(r, dict) and r.get("due"):
            try:
                custom.append({"due": acc.clean_date(r["due"]), "principal": to_minor(num(r.get("principal"))),
                               "interest": to_minor(num(r.get("interest")))})
            except ValidationError:
                continue
    ln = {"schedule_type": stype, "interest_type": itype, "principal": principal, "opening_balance": 0, "start_date": start,
          "first_due": first, "rate": num(d.get("rate")) / 100 if itype in ("declining", "flat") else 0.0,
          "fixed_interest": to_minor(num(d.get("fixed_interest"))) if itype == "fixed" else 0,
          "term_months": int(num(d.get("term_months") or 0)), "frequency": int(num(d.get("frequency") or 1)) or 1,
          "schedule": custom}
    if not principal or (stype not in ("custom", "none") and not ln["term_months"]) or ln["frequency"] not in (1, 2, 3, 4, 6, 12):
        return {"rows": [], "totals": {}}
    rows = build_schedule(ln)
    return {"rows": [{**r, **{k: to_major(r[k]) for k in ("principal", "interest", "payment", "balance")}} for r in rows],
            "totals": {k: to_major(sum(r[k] for r in rows)) for k in ("principal", "interest", "payment")}}


def _paid_totals(db, ln):
    """Principal and interest paid through repayments (collections), in minor units."""
    principal = -_sum(db, ln, ln["account"], types=["PAY"])
    interest = 0
    for e in _entries(db, ln["code"]):
        if e["ref"].startswith(f"LN:{ln['code']}:PAY"):
            interest += to_minor(json.loads(e["meta"] or "{}").get("interest", 0))
    return principal, interest


def schedule_status(db, ln, today=None):
    """The schedule with what is paid, due and overdue."""
    today = today or acc.today()
    rows = build_schedule(ln)
    p_paid, i_paid = _paid_totals(db, ln)
    before = ln["opening_date"] if ln["opening_balance"] and ln["opening_date"] else ""
    if before:   # registered part-way: what was repaid before (amount − balance then) and earlier interest are settled
        p_paid += max((ln["principal"] or ln["opening_balance"]) - ln["opening_balance"], 0)
        i_paid += sum(r["interest"] for r in rows if r["due"] < before)
    cum_p = cum_i = 0
    overdue = due_now = 0
    nxt = None
    for r in rows:
        p_left = max(min(cum_p + r["principal"] - p_paid, r["principal"]), 0)
        i_left = max(min(cum_i + r["interest"] - i_paid, r["interest"]), 0)
        cum_p += r["principal"]
        cum_i += r["interest"]
        left = p_left + i_left
        r["principal_left"], r["interest_left"], r["left"] = p_left, i_left, left
        r["state"] = "paid" if not left else ("partial" if left < r["payment"] else "open")
        if left and r["due"] < today:
            r["state"] = "overdue"
            overdue += left
        if left and r["due"] <= today:
            due_now += left
        if left and not nxt:
            nxt = r
    return {"rows": rows, "overdue": overdue, "due_now": due_now, "next": nxt}


def status(db, ln, today=None):
    today = today or acc.today()
    bal = principal_balance(db, ln)
    accrued = accrued_balance(db, ln)
    sch = schedule_status(db, ln, today)
    drawn = ln["opening_balance"] + _sum(db, ln, ln["account"], types=["DRAW"])
    p_paid, i_paid = _paid_totals(db, ln)
    maturity = _add_months(_date(ln["start_date"]), ln["term_months"] or 0).isoformat() if ln["term_months"] else ""
    if sch["rows"]:
        maturity = sch["rows"][-1]["due"]
    nxt = sch["next"]
    return {"outstanding": to_major(bal), "accrued": to_major(accrued), "drawn": to_major(drawn),
            "principal_paid": to_major(p_paid), "interest_paid": to_major(i_paid),
            "interest_recognized": to_major(interest_recognized(db, ln)),
            "interest_to_date": to_major(max(earned(db, ln, today) - interest_recognized(db, ln), 0)),
            "overdue": to_major(sch["overdue"]), "due_now": to_major(sch["due_now"]),
            "next_due": nxt["due"] if nxt else "", "next_amount": to_major(nxt["left"]) if nxt else 0,
            "maturity": maturity, "settled": bal <= 0 and accrued <= 0 and drawn > 0,
            "progress": round(min(p_paid / (ln["principal"] or drawn or 1), 1), 4) if (ln["principal"] or drawn) else 0}


def _row(db, ln, today=None):
    bu = db.one("SELECT code FROM business_units WHERE id=?", (ln["bu_id"],)) if ln["bu_id"] else None
    out = {k: v for k, v in ln.items() if k != "schedule"}
    out.update(principal=to_major(ln["principal"]), opening_balance=to_major(ln["opening_balance"]),
               fixed_interest=to_major(ln["fixed_interest"]), rate_pct=round(ln["rate"] * 100, 6),
               bu_code=bu["code"] if bu else "", **status(db, ln, today))
    return out


def register(db, q=None, direction=None, state=None, today=None):
    w, p = ["1=1"], []
    if q:
        w.append("(code LIKE ? OR party LIKE ? OR purpose LIKE ?)")
        p += [f"%{q}%"] * 3
    if direction in DIRECTIONS:
        w.append("direction=?")
        p.append(direction)
    rows = [_row(db, dict(ln, schedule=json.loads(ln["schedule"] or "[]")), today)
            for ln in db.q(f"SELECT * FROM loans WHERE {' AND '.join(w)} ORDER BY start_date DESC, id DESC", p)]
    if state == "active":
        rows = [r for r in rows if r["status"] == "active"]
    elif state == "closed":
        rows = [r for r in rows if r["status"] == "closed"]
    elif state == "overdue":
        rows = [r for r in rows if r["overdue"] > 0]
    tot = {}
    for d in DIRECTIONS:
        rs = [r for r in rows if r["direction"] == d and r["status"] == "active"]
        tot[d] = {k: round(sum(r[k] for r in rs), 2) for k in ("outstanding", "accrued", "overdue", "due_now")}
        tot[d]["count"] = len(rs)
    return {"rows": rows, "totals": tot, "next_code": next_code(db)}


def detail(db, lid, today=None):
    ln = get(db, lid)
    today = today or acc.today()
    sch = schedule_status(db, ln, today)
    entries = [acc.get_entry(db, e["id"]) for e in _entries(db, ln["code"])]
    return {"loan": _row(db, ln, today), "custom_schedule": ln["schedule"],
            "schedule": [{**r, **{k: to_major(r[k]) for k in ("principal", "interest", "payment", "balance",
                                                                 "principal_left", "interest_left", "left")}}
                         for r in sch["rows"]],
            "entries": entries, "suggest": suggest(db, ln, today), "accounts": default_accounts(db, ln["direction"], ln["term_months"])}


def suggest(db, ln, date=None):
    """What to pay / collect now and how much interest to book, as of a date."""
    date = acc.clean_date(date) if date else acc.today()
    sch = schedule_status(db, ln, date)
    bal = principal_balance(db, ln)
    accrued = accrued_balance(db, ln)
    new_interest = max(earned(db, ln, date) - interest_recognized(db, ln), 0)
    due = [r for r in sch["rows"] if r["left"] and r["due"] <= date] or ([sch["next"]] if sch["next"] else [])
    if due:
        principal = min(sum(r["principal_left"] for r in due), max(bal, 0))
        interest = sum(r["interest_left"] for r in due)
        basis = "schedule"
    else:
        principal, interest, basis = 0, accrued + new_interest, "interest to date"
    return {"date": date, "principal": to_major(principal), "interest": to_major(interest),
            "total": to_major(principal + interest), "accrue": to_major(new_interest), "basis": basis,
            "installments": [r["n"] for r in due], "outstanding": to_major(bal), "accrued": to_major(accrued)}


# ------------------------------------------------------------------- save
def save(db, d, lid=None, user=""):
    """Create a loan (optionally booking the money now) or change its terms."""
    old = get(db, lid) if lid else None
    direction = d.get("direction") or (old and old["direction"])
    if direction not in DIRECTIONS:
        raise ValidationError("Choose whether the company borrowed or lent the money")
    if old and direction != old["direction"] and _entries(db, old["code"]):
        raise ValidationError("This loan has entries — it cannot switch between borrowed and lent")
    code = text(d.get("code"), maxlen=30) or (old["code"] if old else next_code(db))
    party = text(d.get("party"), "lender" if direction == "borrowed" else "borrower", required=True)
    term = int(num(d.get("term_months") if d.get("term_months") not in (None, "") else 12, "term (months)"))
    if term < 0 or term > 600:
        raise ValidationError("The term must be between 0 and 600 months")
    dflt = default_accounts(db, direction, term)
    main_types = ("Liability",) if direction == "borrowed" else ("Asset",)
    account = acc.account_code(db, d.get("account") or dflt["account"], types=main_types,
                               field="loan account")
    interest_account = acc.account_code(db, d.get("interest_account") or dflt["interest_account"],
                                        types=("Expense", "COGS") if direction == "borrowed" else ("Revenue",),
                                        field="interest account")
    accrued_account = acc.account_code(db, d.get("accrued_account") or dflt["accrued_account"], types=main_types,
                                       field="accrued interest account")
    if accrued_account == account:
        raise ValidationError("Accrued interest needs its own account, not the loan account")
    start = acc.clean_date(d.get("start_date") or (old and old["start_date"]), "start date")
    principal = to_minor(num(d.get("principal"), "loan amount"))
    itype = d.get("interest_type") or ("declining" if num(d.get("rate")) else "none")
    if itype not in INTEREST_TYPES:
        raise ValidationError("Unknown interest type")
    rate = _pct(d.get("rate")) if itype in ("declining", "flat") else 0.0
    fixed = to_minor(num(d.get("fixed_interest"), "fixed interest")) if itype == "fixed" else 0
    if itype in ("declining", "flat") and not rate:
        raise ValidationError("Enter the interest rate, or choose interest-free")
    freq = int(num(d.get("frequency") or 1, "installment frequency"))
    if freq not in (1, 2, 3, 4, 6, 12):
        raise ValidationError("Installments are every 1, 2, 3, 4, 6 or 12 months")
    stype = d.get("schedule_type") or "equal"
    if stype not in SCHEDULES:
        raise ValidationError("Unknown repayment schedule")
    if stype not in ("none", "custom") and not term:
        raise ValidationError("Enter the term in months (or choose a flexible schedule)")
    first_due = acc.clean_date(d.get("first_due"), "first installment") if d.get("first_due") else ""
    if first_due and first_due < start:
        raise ValidationError("The first installment cannot be before the loan starts")
    custom = []
    if stype == "custom":
        for r in d.get("schedule") or []:
            if not isinstance(r, dict) or not r.get("due"):
                continue
            custom.append({"due": acc.clean_date(r["due"], "installment date"),
                           "principal": to_minor(num(r.get("principal"), "installment principal")),
                           "interest": to_minor(num(r.get("interest"), "installment interest"))})
        if not custom:
            raise ValidationError("Add the installments of the custom schedule")
    how = d.get("booking") or ("keep" if old else "money")
    opening = old["opening_balance"] if old else 0
    opening_date = old["opening_date"] if old else ""
    if not old and how == "opening":   # already in the books: track its balance from now on
        opening = to_minor(num(d.get("opening_balance") if d.get("opening_balance") not in (None, "") else
                               to_major(principal), "balance still owed"))
        opening_date = acc.clean_date(d.get("opening_date") or start, "balance date")
        if not opening:
            raise ValidationError("Enter the balance still owed on this loan")
    if old and d.get("opening_balance") not in (None, "") and old["opening_balance"]:
        opening = to_minor(num(d.get("opening_balance"), "opening balance"))
        opening_date = acc.clean_date(d.get("opening_date") or old["opening_date"] or start, "balance date")
    if not principal and not opening and how != "none":
        raise ValidationError("Enter the loan amount")
    vals = (code, direction, party, text(d.get("purpose"), maxlen=300), account, interest_account, accrued_account, start,
            principal, opening, opening_date, rate, itype, fixed, term, freq, first_due, stype,
            json.dumps(custom), acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), text(d.get("notes"), maxlen=2000))
    with db.tx():
        dup = db.one("SELECT id FROM loans WHERE code=?", (code,))
        if dup and (not old or dup["id"] != old["id"]):
            raise ValidationError(f"Loan code {code} is already used")
        if old:
            has = _entries(db, old["code"])
            if has and code != old["code"]:
                raise ValidationError("This loan has entries — its code cannot change")
            if has and account != old["account"] and _sum(db, old, old["account"]):
                raise ValidationError("Money is already booked on the old loan account — it cannot change")
            db.conn.execute("UPDATE loans SET code=?, direction=?, party=?, purpose=?, account=?, interest_account=?, "
                            "accrued_account=?, start_date=?, principal=?, opening_balance=?, opening_date=?, rate=?, "
                            "interest_type=?, fixed_interest=?, term_months=?, frequency=?, first_due=?, schedule_type=?, "
                            "schedule=?, bu_id=?, notes=? WHERE id=?", vals + (old["id"],))
            return old["id"]
        lid = db.conn.execute("INSERT INTO loans(code,direction,party,purpose,account,interest_account,accrued_account,"
                              "start_date,principal,opening_balance,opening_date,rate,interest_type,fixed_interest,term_months,"
                              "frequency,first_due,schedule_type,schedule,bu_id,notes,created_at) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              vals + (acc.now_iso(),)).lastrowid
        if how == "money" and principal:
            post(db, lid, "draw", {"date": start, "amount": to_major(principal), "method": d.get("method"),
                                   "paid_from": d.get("paid_from"), "fees": d.get("fees"), "memo": d.get("memo")}, user)
        return lid


def delete(db, lid):
    ln = get(db, lid)
    if _entries(db, ln["code"]):
        raise ValidationError("This loan has journal entries — void them first, or write the loan off / close it")
    db.x("DELETE FROM loans WHERE id=?", (ln["id"],))


def set_status(db, lid, closed, date=None):
    ln = get(db, lid)
    if closed:
        st = status(db, ln)
        if st["outstanding"] or st["accrued"]:
            raise ValidationError(f"The loan still has {st['outstanding'] + st['accrued']:,.2f} outstanding — "
                                  "record the last payment or write it off first")
        db.x("UPDATE loans SET status='closed', closed_on=? WHERE id=?", (acc.clean_date(date or acc.today()), ln["id"]))
    else:
        db.x("UPDATE loans SET status='active', closed_on='' WHERE id=?", (ln["id"],))


# -------------------------------------------------------------- movements
def _L(account, debit=0, credit=0, party=""):
    return {"account": account, "debit": to_major(debit), "credit": to_major(credit), "party": party}


def plan(db, lid, action, d):
    """The entry an action would post: {kind, date, memo, lines, meta, notes}. Nothing is saved."""
    ln = get(db, lid)
    if ln["status"] == "closed" and action != "fee":
        raise ValidationError("This loan is closed — reopen it first")
    borrowed = ln["direction"] == "borrowed"
    date = acc.clean_date(d.get("date") or acc.today())
    party = ln["party"]
    src = str(d.get("paid_from") or d.get("received_into") or "")
    if src.startswith("partner:"):
        # the money left (or reached) a partner's own pocket, not the company's bank: it goes to his capital account,
        # tagged to him — e.g. the funding partner paying a loan out personally
        from . import equity
        mparty = equity.partner(db, src[8:])["name"]
        money = db.acct("capital")
    else:
        mparty = ""
        money = acc.money_account(db, d.get("method"), src or None)
    dflt = default_accounts(db, ln["direction"], ln["term_months"])

    def _M(debit=0, credit=0):
        return _L(money, debit, credit, party=mparty)
    lines, notes, meta, tag = [], [], {"loan": ln["code"]}, ""
    if action == "draw":
        amount = to_minor(num(d.get("amount"), "amount", allow_zero=False))
        fees = to_minor(num(d.get("fees"), "fees deducted"))
        if fees >= amount:
            raise ValidationError("Fees cannot be as much as the loan itself")
        if borrowed:
            lines = [_M(amount - fees)]
            if fees:
                lines.append(_L(acc.account_code(db, d.get("fee_account") or dflt["fee_account"], types=("Expense",),
                                                 field="fees account"), fees))
                notes.append(f"{to_major(fees):,.2f} fees kept by the lender are booked as an expense.")
            lines.append(_L(ln["account"], credit=amount, party=party))
        else:
            lines = [_L(ln["account"], amount, party=party), _M(credit=amount - fees)]
            if fees:
                lines.append(_L(acc.account_code(db, d.get("fee_account") or dflt["fee_account"], types=("Revenue",),
                                                 field="fees account"), credit=fees))
                notes.append(f"{to_major(fees):,.2f} fees kept from the amount paid out are booked as income.")
        meta["amount"] = to_major(amount)
        tag = "DRAW"
        memo = f"{'Loan received from' if borrowed else 'Loan given to'} {party} ({ln['code']})"
    elif action == "pay":
        principal = to_minor(num(d.get("principal"), "principal"))
        interest = to_minor(num(d.get("interest"), "interest"))
        extra = to_minor(num(d.get("fees"), "fees / penalty"))
        if principal + interest + extra <= 0:
            raise ValidationError("Enter the principal and / or interest")
        bal = principal_balance(db, ln)
        if principal > bal:
            raise ValidationError(f"Only {to_major(bal):,.2f} of principal is outstanding")
        accrued = max(accrued_balance(db, ln), 0)
        from_accrued = min(interest, accrued)
        rest = interest - from_accrued
        total = principal + interest + extra
        fee_acc = acc.account_code(db, d.get("fee_account") or dflt["fee_account"],
                                   types=("Expense",) if borrowed else ("Revenue",), field="fees account") if extra else None
        side = []
        if principal:
            side.append((ln["account"], principal, party))
        if from_accrued:
            side.append((ln["accrued_account"], from_accrued, party))
        if rest:
            side.append((ln["interest_account"], rest, ""))
        if extra:
            side.append((fee_acc, extra, ""))
        if borrowed:
            lines = [_L(a, v, party=p) for a, v, p in side] + [_M(credit=total)]
        else:
            lines = [_M(total)] + [_L(a, credit=v, party=p) for a, v, p in side]
        if from_accrued:
            notes.append(f"{to_major(from_accrued):,.2f} of the interest clears interest already accrued.")
        meta.update(principal=to_major(principal), interest=to_major(interest), fees=to_major(extra), interest_to=date)
        tag = "PAY"
        memo = f"{'Repayment to' if borrowed else 'Collection from'} {party} ({ln['code']})"
    elif action == "interest":   # accrue (or add to the loan) without money moving
        amount = to_minor(num(d.get("amount"), "interest", allow_zero=False))
        capital = d.get("add_to_loan") in (True, 1, "1", "true", "on")
        target = ln["account"] if capital else ln["accrued_account"]
        if borrowed:
            lines = [_L(ln["interest_account"], amount), _L(target, credit=amount, party=party)]
        else:
            lines = [_L(target, amount, party=party), _L(ln["interest_account"], credit=amount)]
        meta.update(amount=to_major(amount), added_to_loan=capital, interest_to=date)
        notes.append("Added to the loan balance (capitalised)." if capital else
                     "Booked as accrued interest — it is cleared when the interest is paid.")
        tag = "INT"
        memo = f"Interest on loan {ln['code']} ({party}) to {date}"
    elif action == "fee":
        amount = to_minor(num(d.get("amount"), "amount", allow_zero=False))
        on_loan = d.get("add_to_loan") in (True, 1, "1", "true", "on")
        fee_acc = acc.account_code(db, d.get("fee_account") or dflt["fee_account"],
                                   types=("Expense",) if borrowed else ("Revenue",), field="charges account")
        other = ln["account"] if on_loan else money
        if borrowed:
            lines = [_L(fee_acc, amount), _L(other, credit=amount, party=party if on_loan else "")]
        else:
            lines = [_L(other, amount, party=party if on_loan else ""), _L(fee_acc, credit=amount)]
        meta.update(amount=to_major(amount), added_to_loan=on_loan)
        tag = "FEE"
        memo = text(d.get("memo")) or f"Loan charges {ln['code']} ({party})"
    elif action == "writeoff":
        bal, accrued = principal_balance(db, ln), max(accrued_balance(db, ln), 0)
        amount = to_minor(num(d.get("amount"), "amount")) if d.get("amount") not in (None, "") else bal
        if amount <= 0 and not accrued:
            raise ValidationError("Nothing is outstanding on this loan")
        if amount > bal:
            raise ValidationError(f"Only {to_major(bal):,.2f} of principal is outstanding")
        off_acc = acc.account_code(db, d.get("account") or dflt["writeoff_account"],
                                   types=("Revenue",) if borrowed else ("Expense",), field="write-off account")
        clear_accrued = accrued if d.get("include_accrued", True) not in (False, 0, "0", "false") else 0
        total = amount + clear_accrued
        if borrowed:   # the lender forgave it: a gain
            lines = ([_L(ln["account"], amount, party=party)] if amount else []) + \
                    ([_L(ln["accrued_account"], clear_accrued, party=party)] if clear_accrued else []) + [_L(off_acc, credit=total)]
        else:          # we will not collect it: a loss
            lines = [_L(off_acc, total)] + ([_L(ln["account"], credit=amount, party=party)] if amount else []) + \
                    ([_L(ln["accrued_account"], credit=clear_accrued, party=party)] if clear_accrued else [])
        meta.update(principal=to_major(amount), interest=to_major(clear_accrued))
        tag = "OFF"
        memo = f"{'Loan forgiven by' if borrowed else 'Loan written off —'} {party} ({ln['code']})"
    else:
        raise ValidationError("Unknown loan action")
    return {"loan": ln, "kind": KINDS[ln["direction"]][tag], "date": date, "memo": text(d.get("memo")) or memo,
            "ref": f"LN:{ln['code']}:{tag}", "lines": [x for x in lines if x["debit"] or x["credit"]], "meta": meta,
            "notes": notes}


def post(db, lid, action, d, user=""):
    with db.tx():
        pl = plan(db, lid, action, d)
        ln = pl["loan"]
        eid = acc._insert_entry(db, date=pl["date"], kind=pl["kind"], memo=pl["memo"], bu_id=ln["bu_id"], party=ln["party"],
                                ref=pl["ref"], lines=pl["lines"], meta=pl["meta"], user=user)
        return eid


# ------------------------------------------------------------- backups
def export_rows(db):
    out = []
    for ln in db.q("SELECT l.*, b.code AS bu_code FROM loans l LEFT JOIN business_units b ON b.id=l.bu_id ORDER BY l.id"):
        ln.pop("id")
        ln.pop("bu_id")
        out.append(ln)
    return out


def import_rows(db, rows):
    cols = ("code", "direction", "party", "purpose", "account", "interest_account", "accrued_account", "start_date",
            "principal", "opening_balance", "opening_date", "rate", "interest_type", "fixed_interest", "term_months",
            "frequency", "first_due", "schedule_type", "schedule", "status", "closed_on", "notes", "created_at")
    n = 0
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("code") or r.get("direction") not in DIRECTIONS:
            continue
        bu = db.one("SELECT id FROM business_units WHERE code=?", (r.get("bu_code"),)) if r.get("bu_code") else None
        nums = {"principal", "opening_balance", "rate", "fixed_interest", "term_months", "frequency"}
        vals = [r.get(c) if r.get(c) is not None else (0 if c in nums else "") for c in cols]
        db.conn.execute(f"INSERT OR REPLACE INTO loans({','.join(cols)},bu_id) VALUES ({','.join('?' * len(cols))},?)",
                        vals + [bu["id"] if bu else None])
        n += 1
    return n
