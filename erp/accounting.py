"""Business logic: double-entry posting, operational documents and statements.

Every money movement ends up as one balanced journal entry. Documents (costs,
payroll, collections, invoices, stock moves...) are thin helpers that build
the right debit/credit lines, so every report comes from one source of truth.

System accounts are looked up by *role* (db.acct('bank')), never by a
hard-coded number, so the chart of accounts can be renumbered freely.
"""
import copy
import datetime as _dt
import functools
import json
import re
import weakref

from .db import SUBTYPES, normal_for, to_major, to_minor

BILLING_BASES = ["Cost-Plus %", "Fixed Price", "Flexible / Settled", "Live Fee (Paid vs Costs)"]
# "Live Fee": nothing is billed ahead — the fee is whatever the client has actually paid so far over what the project has
# actually cost so far (see bu_summaries: client_paid, actual_costs, live_fee)
LIVE_FEE = "Live Fee (Paid vs Costs)"
PAYMENT_METHODS = ["Bank Transfer", "Cash", "Cheque", "Credit Card", "Instapay"]
PAYROLL_TYPES = {  # cost type -> (account role, business unit required?)
    "Direct Labour (Cost of Revenue)": ("direct_labour", True),
    "Indirect Labour (Cost-Centre Overhead)": ("project_overhead", True),
    "Administrative Salaries (Shared Overhead)": ("admin_salaries", False),
}
STOCK_MOVE_TYPES = ["Receipt", "Issue", "Transfer", "Adjustment"]
ENTRY_KINDS = ["Direct Cost", "Operating Expense", "Payroll Accrual", "Payment Voucher", "Supplier Discount",
               "Receipt Voucher", "Sales Invoice", "Capital Contribution", "Capital Withdrawal", "Owner Drawing", "Partner Loan", "Partner Loan Repayment",
               "Partner Loan Interest", "Share Transfer", "Partner Admission", "Partner Exit", "Profit Distribution",
               "Reserve Transfer", "Cash Transfer", "Asset Purchase", "Depreciation", "Asset Disposal", "Journal Voucher",
               "Goods Receipt", "Material Issue", "Stock Transfer", "Stock Adjustment", "Reversing Entry",
               "Loan Received", "Loan Repayment", "Loan Given", "Loan Collection", "Loan Interest", "Loan Charges",
               "Loan Forgiven", "Loan Written Off", "Custody Issued", "Custody Settlement", "Custody Return",
               "Custody Deduction", "Rental Equipment Purchase", "Rental Equipment Loss", "Rental Equipment Depreciation",
               "Rental Deposit", "Installment Interest", "Advance Applied", "Cost of Sales", "Cheque Received",
               "Cheque Cleared", "Cheque Bounced", "Sale Refund", "Bank Entry",
               "Subcontractor Certificate", "Equipment Usage", "Equipment Maintenance", "E-Receipt"]


class ValidationError(Exception):
    """Bad user input; the API turns it into a 400 with this message."""


# ------------------------------------------------------------------------ utils
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today():
    from .db import now
    return now().date().isoformat()


def now_iso():
    from .db import now
    return now().isoformat(timespec="seconds")


def clean_date(value, field="date"):
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    value = ("" if value is None else str(value)).strip()
    if not value:
        return today()
    value = value[:10]
    if not DATE_RE.match(value):
        raise ValidationError(f"{field} must be YYYY-MM-DD")
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{field} is not a real date") from None
    return value


def num(value, field="amount", allow_zero=True, allow_negative=False):
    if value in (None, ""):
        value = 0
    try:
        v = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number") from None
    if v != v or v in (float("inf"), float("-inf")):
        raise ValidationError(f"{field} must be a number")
    if not allow_negative and v < 0:
        raise ValidationError(f"{field} cannot be negative")
    if not allow_zero and v == 0:
        raise ValidationError(f"{field} must be greater than zero")
    return v


def text(value, field=None, required=False, maxlen=500):
    v = ("" if value is None else str(value)).strip()
    if required and not v:
        raise ValidationError(f"{field or 'value'} is required")
    return v[:maxlen]


def is_cash_method(method):
    return (method or "").strip().lower() == "cash"


def money_account(db, method=None, explicit=None):
    """Where money goes in/out: an explicit cash/bank account (or the funding partner's account), else by payment
    method — and when the company has a funding partner, his account is where its money comes from and goes to."""
    if explicit:
        row = db.one("SELECT role FROM accounts WHERE code=?", (text(explicit),))
        if row and row["role"] == "funding_partner":
            return account_code(db, explicit)
        return account_code(db, explicit, subtypes=("cash", "short_term_debt"))
    if (db.setting("funding_partner") or "").strip():
        f = db.one("SELECT code FROM accounts WHERE role='funding_partner' AND active=1")
        if f:
            return f["code"]
    return db.acct("cash" if is_cash_method(method) else "bank")


def account_code(db, value, types=None, subtypes=None, field="account"):
    code = text(value)
    row = db.one("SELECT * FROM accounts WHERE code=?", (code,))
    if not row:
        raise ValidationError(f"Unknown {field} {code!r}")
    if not row["active"]:
        raise ValidationError(f"Account {code} is inactive")
    if types and row["type"] not in types:
        raise ValidationError(f"{field} {code} must be a {' / '.join(types)} account")
    if subtypes and row["subtype"] not in subtypes:
        raise ValidationError(f"{field} {code} is not a valid choice here")
    return code


def bu_id_or_none(db, value, required=False):
    if value in (None, "", 0, "0"):
        if required:
            raise ValidationError("Choose a business unit")
        return None
    row = db.one("SELECT id FROM business_units WHERE code=? OR (id=? AND ? GLOB '[0-9]*')",
                 (str(value), value, str(value)))
    if not row:
        raise ValidationError(f"Unknown business unit: {value}")
    return row["id"]


# ------------------------------------------------------------------------ posting
def _validate_lines(db, lines):
    if not lines or len(lines) < 2:
        raise ValidationError("An entry needs at least two lines")
    accounts = {r["code"]: r for r in db.q("SELECT code, active, role FROM accounts")}
    funder = None
    out, dr, cr = [], 0, 0
    touched = set()
    for ln in lines:
        acc = text(ln.get("account"))
        if acc not in accounts:
            raise ValidationError(f"Unknown account {acc!r}")
        d = to_minor(num(ln.get("debit"), "debit"))
        c = to_minor(num(ln.get("credit"), "credit"))
        if d and c:
            raise ValidationError("A line cannot have both a debit and a credit")
        if not d and not c:
            continue
        party = text(ln.get("party"))
        if not party and accounts[acc]["role"] == "funding_partner":
            # money through the funding partner's account is always his
            funder = funder if funder is not None else (db.setting("funding_partner") or "").strip()
            party = funder
        out.append((acc, d, c, party))
        touched.add((acc, party))
        dr += d
        cr += c
    if dr == 0:
        raise ValidationError("Entry total must be greater than zero")
    if len(touched) == 1:
        raise ValidationError("The debit and the credit are on the same account, so nothing would change — "
                              "choose the account the money comes from and the one it goes to")
    if dr != cr:
        raise ValidationError(f"Debits ({to_major(dr):,.2f}) must equal credits ({to_major(cr):,.2f})")
    return out


def check_open(db, date):
    """Refuse changes dated on or before the lock date ("books closed up to")."""
    lock = (db.setting("lock_date") or "").strip()
    if lock and str(date or "") <= lock:
        raise ValidationError(f"The books are closed up to {lock} — change the lock date in Settings to post or edit on {date}")


# set by replace_doc(): the next posted entry overwrites this one instead of adding a new entry
_REPLACE = {"eid": None}


def _insert_entry(db, *, date, kind, memo="", bu_id=None, party="", ref="", lines, meta=None,
                  reversal_of=None, user=""):
    """Insert a balanced entry. Caller must hold a transaction (db.tx())."""
    check_open(db, clean_date(date))
    vlines = _validate_lines(db, lines)
    if _REPLACE["eid"]:
        eid, _REPLACE["eid"] = _REPLACE["eid"], None
        e = get_entry(db, eid)
        snap = {k: e[k] for k in ("date", "memo", "bu_code", "party", "lines")}
        snap.update(ref=e["ref"], edited_at=now_iso(), edited_by=user)
        db.conn.execute("UPDATE journal_entries SET date=?, kind=?, memo=?, bu_id=?, party=?, ref=?, meta=?, history=? "
                        "WHERE id=?", (clean_date(date), kind, text(memo, maxlen=1000), bu_id, text(party), text(ref),
                                       json.dumps(meta or {}, ensure_ascii=False),
                                       json.dumps(e["history"] + [snap], ensure_ascii=False), eid))
        db.conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (eid,))
        db.conn.executemany("INSERT INTO journal_lines(entry_id,account,debit,credit,party) VALUES (?,?,?,?,?)",
                            [(eid, a, d, c, p) for a, d, c, p in vlines])
        _wip_after(db, kind, bu_id, e.get("bu_id"))
        return eid
    cur = db.conn.execute(
        "INSERT INTO journal_entries(date,kind,memo,bu_id,party,ref,meta,reversal_of,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (clean_date(date), kind, text(memo, maxlen=1000), bu_id, text(party), text(ref),
         json.dumps(meta or {}, ensure_ascii=False), reversal_of, user or "", now_iso().replace("T", " ")))
    eid = cur.lastrowid
    db.conn.executemany("INSERT INTO journal_lines(entry_id,account,debit,credit,party) VALUES (?,?,?,?,?)",
                        [(eid, a, d, c, p) for a, d, c, p in vlines])
    _wip_after(db, kind, bu_id)
    return eid


def _wip_after(db, kind, *bu_ids):
    """A project's costs or invoices changed: bring its work in progress up to date (not for that entry itself)."""
    if kind == "Work in Progress":
        return
    from . import wip
    for b in {x for x in bu_ids if x}:
        wip.sync(db, b)


def post_entry(db, **kw):
    with db.tx():
        return _insert_entry(db, **kw)


def possible_duplicates(db, eid, days=3):
    """Posted entries that look like this one was already recorded: the same kind and party (or the same description
    when there is no party), the same total, dated within `days` of it — or the same kind, party and reference (e.g.
    the supplier's invoice number) on any date."""
    e = db.one("SELECT e.*, (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) amt FROM journal_entries e WHERE e.id=?", (int(eid),))
    if not e or not e["amt"]:
        return []
    d = _dt.date.fromisoformat(e["date"])
    lo, hi = (d - _dt.timedelta(days=days)).isoformat(), (d + _dt.timedelta(days=days)).isoformat()
    who = ("e.party=?", e["party"]) if e["party"] else ("LOWER(TRIM(e.memo))=LOWER(TRIM(?)) AND e.party=''", e["memo"] or "")
    if not e["party"] and not (e["memo"] or "").strip():
        return []
    ref = "OR (e.kind=? AND e.party<>'' AND e.party=? AND e.ref<>'' AND e.ref=?)" if e["party"] and (e["ref"] or "").strip() else ""
    rows = db.q(f"""SELECT e.id, e.date, e.kind, e.memo, e.party, e.ref, e.created_by, e.created_at, b.code bu_code,
                          (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) amt
                   FROM journal_entries e LEFT JOIN business_units b ON b.id=e.bu_id
                   WHERE {LIVE} AND e.id<>? AND ((e.kind=? AND e.date BETWEEN ? AND ? AND {who[0]}
                         AND (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id)=?) {ref})
                   ORDER BY e.date DESC, e.id DESC LIMIT 5""",
                [e["id"], e["kind"], lo, hi, who[1], e["amt"]] + ([e["kind"], e["party"], e["ref"]] if ref else []))
    return [{**r, "amount": to_major(r.pop("amt") or 0), "same_ref": bool(ref) and r["ref"] == e["ref"]} for r in rows]


def get_entry(db, eid):
    e = db.one("SELECT e.*, b.code AS bu_code, (SELECT COUNT(*) FROM attachments WHERE owner='entry' AND owner_id=e.id) AS files "
               "FROM journal_entries e LEFT JOIN business_units b ON b.id=e.bu_id WHERE e.id=?", (eid,))
    if not e:
        return None
    e["meta"] = json.loads(e["meta"] or "{}")
    e["history"] = json.loads(e["history"] or "[]")
    e["lines"] = [{"account": r["account"], "name": r["name"], "name_ar": r["name_ar"], "debit": to_major(r["debit"]),
                   "credit": to_major(r["credit"]), **({"party": r["party"]} if r["party"] else {})}
                  for r in db.q("SELECT l.*, a.name, a.name_ar FROM journal_lines l JOIN accounts a ON a.code=l.account "
                                "WHERE entry_id=? ORDER BY l.id", (eid,))]
    e["amount"] = round(sum(ln["debit"] for ln in e["lines"]), 2)
    e["tags"] = [r["tag"] for r in db.q("SELECT tag FROM entry_tags WHERE entry_id=? ORDER BY tag", (eid,))]
    return e


def set_tags(db, eid, tags):
    """An entry's tags: short labels, at most 12, each up to 40 characters."""
    clean = []
    for t in tags or []:
        t = re.sub(r"\s+", " ", str(t or "")).strip()[:40]
        if t and t.lower() not in [c.lower() for c in clean]:
            clean.append(t)
    with db.tx():
        db.conn.execute("DELETE FROM entry_tags WHERE entry_id=?", (int(eid),))
        db.conn.executemany("INSERT INTO entry_tags(entry_id, tag) VALUES (?,?)", [(int(eid), t) for t in clean[:12]])
    return clean[:12]


def _split(db, total_minor, paid_minor, payable_code, method=None, paid_from=None):
    """Credit lines for 'total, of which paid now; the rest is owed'."""
    if paid_minor < 0 or paid_minor > total_minor:
        raise ValidationError("Paid now must be between zero and the total")
    lines = []
    if paid_minor:
        lines.append({"account": money_account(db, method, paid_from), "credit": to_major(paid_minor)})
    if total_minor - paid_minor:
        lines.append({"account": payable_code, "credit": to_major(total_minor - paid_minor)})
    return lines


# ---------------------------------------------------------------------- taxes
def tax_code(db, code, kind):
    """(code, rate) for a VAT or WHT code; blank code = no tax."""
    code = text(code)
    if not code:
        return "", 0.0
    t = db.one("SELECT * FROM tax_codes WHERE code=?", (code,))
    if not t:
        raise ValidationError(f"Unknown tax code {code!r}")
    if t["kind"] != kind:
        raise ValidationError(f"{code} is not a {'VAT' if kind == 'vat' else 'withholding'} code")
    if not t["active"]:
        raise ValidationError(f"Tax code {code} is inactive")
    return code, float(t["rate"])


def _purchase_taxes(db, d, net_minor):
    """Input VAT we can recover and withholding tax we deduct from the supplier."""
    vat_code, vat_rate = tax_code(db, d.get("vat_code"), "vat")
    wht_code, wht_rate = tax_code(db, d.get("wht_code"), "wht")
    vat = int(round(net_minor * vat_rate))
    wht = int(round(net_minor * wht_rate))
    return vat_code, vat, wht_code, wht


def _purchase_lines(db, d, debit_account, net, vat, wht, payable, paid):
    """Dr cost/asset (net) + Dr input VAT; Cr cash (paid) + Cr supplier (rest) + Cr WHT payable."""
    supplier_total = net + vat - wht
    lines = [{"account": debit_account, "debit": to_major(net)}]
    if vat:
        lines.append({"account": db.acct("vat_input"), "debit": to_major(vat)})
    lines += _split(db, supplier_total, paid, payable, d.get("method"), d.get("paid_from"))
    if wht:
        lines.append({"account": db.acct("wht_payable"), "credit": to_major(wht)})
    return lines


# ---------------------------------------------------------------- purchasing
def record_project_cost(db, d, user="", strict=False):
    """Direct costs for a business unit (materials, subcontractors, site work)."""
    bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
    account = account_code(db, d.get("account") or db.acct("materials"), types=("COGS",), field="cost account")
    qty = num(d.get("qty", 1) or 1, "quantity", allow_zero=False)
    rate = num(d.get("rate"), "unit cost", allow_zero=False)
    quoted = to_minor(qty * rate)
    savings = to_minor(num(d.get("savings"), "negotiation savings"))
    if savings > quoted:
        raise ValidationError("Savings cannot exceed the quoted total")
    cost = quoted - savings
    vat_code, vat, wht_code, wht = _purchase_taxes(db, d, cost)
    paid = to_minor(num(d.get("paid"), "paid now"))
    vendor = text(d.get("vendor"))
    if (paid < cost + vat - wht or wht) and not vendor:
        raise ValidationError("Name the supplier so the unpaid balance / withholding can be tracked")
    payable = account_code(db, d.get("payable_account") or db.acct("ap"), types=("Liability",))
    # new entries only (strict): replays of the shared log and imports keep whatever was recorded before this rule
    if strict and text(d.get("deal_status")).lower() == "settled" and paid < cost + vat - wht:
        raise ValidationError(
            f"The deal is marked Settled but {to_major(cost + vat - wht - paid):,.2f} is still unpaid. If the supplier "
            "accepted less, add the difference to Negotiation discount; otherwise leave the status Open until it is paid.")
    lines = _purchase_lines(db, d, account, cost, vat, wht, payable, paid)
    mode = negotiation_mode(db, bu, d.get("negotiation"))
    if mode == "keep" and savings:   # cost stays at the full price (what the client is billed on); the discount is ours
        lines[0]["debit"] = to_major(quoted)
        lines.append({"account": db.acct("negotiation_income"), "credit": to_major(savings)})
    meta = {"qty": qty, "unit": text(d.get("unit")), "rate": rate, "quoted": to_major(quoted),
            "savings": to_major(savings), "deal_ref": text(d.get("deal_ref")),
            "deal_status": text(d.get("deal_status")), "method": text(d.get("method")),
            "vat_code": vat_code, "wht_code": wht_code, "negotiation": mode if savings else ""}
    return post_entry(db, date=d.get("date"), kind="Direct Cost",
                      memo=text(d.get("description"), "description", required=True), bu_id=bu, party=vendor,
                      ref=meta["deal_ref"], lines=lines, meta=meta, user=user)


def record_expense(db, d, user=""):
    """Operating / overhead expense. With no business unit it is *shared* overhead,
    later allocated to business units by their manual overhead %."""
    account = account_code(db, d.get("account") or db.acct("other_overhead"), types=("Expense",),
                           field="expense account")
    amount = to_minor(num(d.get("amount"), "amount", allow_zero=False))
    vat_code, vat, wht_code, wht = _purchase_taxes(db, d, amount)
    due = amount + vat - wht
    paid = due if d.get("paid") in (None, "") else to_minor(num(d.get("paid"), "paid now"))
    vendor = text(d.get("vendor"))
    if (paid < due or wht) and not vendor:
        raise ValidationError("Name the supplier so the unpaid balance / withholding can be tracked")
    payable = account_code(db, d.get("payable_account") or db.acct("ap"), types=("Liability",))
    lines = _purchase_lines(db, d, account, amount, vat, wht, payable, paid)
    return post_entry(db, date=d.get("date"), kind="Operating Expense",
                      memo=text(d.get("description"), "description", required=True),
                      bu_id=bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), party=vendor, lines=lines,
                      meta={"method": text(d.get("method")), "vat_code": vat_code, "wht_code": wht_code}, user=user)


def record_payroll(db, d, user=""):
    ctype = d.get("cost_type") or "Direct Labour (Cost of Revenue)"
    if ctype not in PAYROLL_TYPES:
        raise ValidationError("Unknown payroll cost type")
    role, needs_bu = PAYROLL_TYPES[ctype]
    bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=needs_bu)
    account = account_code(db, d.get("account") or db.acct(role), types=("COGS", "Expense"))
    worker = text(d.get("worker"), "worker name", required=True)
    qty = num(d.get("qty", 1) or 1, "days / months", allow_zero=False)
    rate = num(d.get("rate"), "rate", allow_zero=False)
    total = to_minor(qty * rate)
    paid = to_minor(num(d.get("paid"), "paid now"))
    payable = account_code(db, d.get("payable_account") or db.acct("accrued_payroll"), types=("Liability",))
    lines = [{"account": account, "debit": to_major(total)}] + _split(db, total, paid, payable, d.get("method"),
                                                                      d.get("paid_from"))
    role_txt = text(d.get("role"))
    meta = {"role": role_txt, "cost_type": ctype, "qty": qty, "rate": rate, "period": text(d.get("period")),
            "basis": text(d.get("basis")), "method": text(d.get("method"))}
    return post_entry(db, date=d.get("date"), kind="Payroll Accrual",
                      memo=f"{worker} — {role_txt}" if role_txt else worker,
                      bu_id=bu, party=worker, lines=lines, meta=meta, user=user)


def record_payment(db, d, user=""):
    """Settle what we owe a supplier or worker (any liability account)."""
    account = account_code(db, d.get("against") or db.acct("ap"), types=("Liability",), field="pay-against account")
    party = text(d.get("party"), "payee", required=True)
    amount = num(d.get("amount"), "amount", allow_zero=False)
    owed = party_balance(db, account, party)
    if to_minor(amount) > to_minor(owed) and not d.get("allow_overpay"):
        raise ValidationError(f"{party} is owed {owed:,.2f} on {account}. Tick 'allow overpayment' to pay more.")
    return post_entry(db, date=d.get("date"), kind="Payment Voucher", memo=text(d.get("memo")) or f"Payment to {party}",
                      bu_id=bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), party=party, ref=text(d.get("ref")),
                      lines=[{"account": account, "debit": amount},
                             {"account": money_account(db, d.get("method"), d.get("paid_from")), "credit": amount}],
                      meta={"method": text(d.get("method"))}, user=user)


NEGOTIATION_MODES = ("keep", "pass")


def negotiation_mode(db, bu_id, asked=None):
    """What happens to a negotiated discount: 'keep' = company earnings (the cost and the client's bill stay at the
    full price), 'pass' = the cost goes down (the client benefits). The entry's own choice wins, then the business
    unit's; entries from before this option existed were always 'pass'."""
    asked = text(asked).lower()
    if asked in NEGOTIATION_MODES:
        return asked
    if asked:
        raise ValidationError("Negotiation discount handling must be 'keep' (company earnings) or 'pass' (lower cost)")
    b = db.one("SELECT negotiation FROM business_units WHERE id=?", (bu_id,)) if bu_id else None
    return b["negotiation"] if b and b["negotiation"] in NEGOTIATION_MODES else "pass"


def supplier_discount_defaults(db, party, account=None):
    """Where a late discount from a supplier goes: company earnings or back to the cost of their latest purchase
    (same account, business unit and deal), following that project's negotiation setting."""
    account = account or db.acct("ap")
    last = db.one(f"""SELECT e.bu_id, e.meta, (SELECT l.account FROM journal_lines l JOIN accounts a ON a.code=l.account
                         WHERE l.entry_id=e.id AND l.debit>0 AND a.type IN ('COGS','Expense') ORDER BY l.debit DESC LIMIT 1) AS cost
                      FROM journal_entries e WHERE {LIVE} AND e.party=?
                        AND EXISTS (SELECT 1 FROM journal_lines x WHERE x.entry_id=e.id AND x.account=? AND x.credit>0)
                      ORDER BY e.date DESC, e.id DESC LIMIT 1""", (party, account))
    meta = json.loads(last["meta"] or "{}") if last else {}
    bu = (last or {}).get("bu_id")
    b = db.one("SELECT negotiation FROM business_units WHERE id=?", (bu,)) if bu else None
    mode = meta.get("negotiation") or (b["negotiation"] if b and b["negotiation"] in NEGOTIATION_MODES else "") \
        or ("pass" if (last or {}).get("cost") else "keep")
    keep = db.acct("negotiation_income")
    cost = (last or {}).get("cost") or keep
    return {"credit_account": keep if mode == "keep" else cost, "negotiation": mode, "keep_account": keep,
            "cost_account": cost, "bu_id": bu, "deal_ref": meta.get("deal_ref", ""),
            "owed": party_balance(db, account, party)}


def record_supplier_discount(db, d, user=""):
    """The supplier accepted less than what is still owed: clear the rest as a discount (reduces that cost)."""
    party = text(d.get("party"), "supplier", required=True)
    account = account_code(db, d.get("against") or db.acct("ap"), types=("Liability",), field="payable account")
    dflt = supplier_discount_defaults(db, party, account)
    amount = num(d.get("amount") or dflt["owed"], "amount", allow_zero=False)
    if to_minor(amount) > to_minor(dflt["owed"]):
        raise ValidationError(f"{party} is only owed {dflt['owed']:,.2f} on {account}")
    mode = text(d.get("negotiation")).lower()
    if not d.get("credit_account") and mode in NEGOTIATION_MODES:
        d = {**d, "credit_account": dflt["keep_account"] if mode == "keep" else dflt["cost_account"]}
    credit = account_code(db, d.get("credit_account") or dflt["credit_account"], types=("COGS", "Expense", "Revenue"),
                          field="discount account")
    mode = "keep" if db.one("SELECT type FROM accounts WHERE code=?", (credit,))["type"] == "Revenue" else "pass"
    bu = bu_id_or_none(db, d.get("bu_id") if d.get("bu_id") not in (None, "") else dflt["bu_id"])
    deal = text(d.get("deal_ref")) or dflt["deal_ref"]
    return post_entry(db, date=d.get("date"), kind="Supplier Discount",
                      memo=text(d.get("memo")) or f"Discount agreed with {party}" + (f" — {deal}" if deal else ""),
                      bu_id=bu, party=party, ref=deal,
                      lines=[{"account": account, "debit": amount}, {"account": credit, "credit": amount}],
                      meta={"deal_ref": deal, "savings": amount, "deal_status": "Settled", "negotiation": mode},
                      user=user)


# ----------------------------------------------------------------------- sales
def record_collection(db, d, user=""):
    """Money received from a client. The credit account decides what it is:
    revenue (cash-basis), receivables (settles an invoice) or client advances."""
    bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
    amount = num(d.get("amount"), "amount", allow_zero=False)
    mode_roles = {"revenue": "revenue", "invoice": "ar", "advance": "client_advances"}
    credit = d.get("credit_account") or db.acct(mode_roles.get(d.get("mode") or "revenue", "revenue"))
    credit = account_code(db, credit, types=("Revenue", "Asset", "Liability"), field="credit account")
    bu_row = db.one("SELECT name, customer FROM business_units WHERE id=?", (bu,))
    received = to_minor(amount)
    withheld = to_minor(num(d.get("withheld"), "tax withheld by client"))
    gross = received + withheld
    vat_code, vat_rate = tax_code(db, d.get("vat_code"), "vat")
    vat = 0
    if vat_code:
        if db.one("SELECT type FROM accounts WHERE code=?", (credit,))["type"] != "Revenue":
            raise ValidationError("VAT on a receipt applies only when it is booked straight to revenue "
                                  "(invoiced VAT is charged on the sales invoice)")
        vat = gross - int(round(gross / (1 + vat_rate)))
    lines = [{"account": money_account(db, d.get("method"), d.get("received_into")), "debit": to_major(received)}]
    if withheld:
        lines.append({"account": db.acct("wht_receivable"), "debit": to_major(withheld)})
    lines.append({"account": credit, "credit": to_major(gross - vat)})
    if vat:
        lines.append({"account": db.acct("vat_output"), "credit": to_major(vat)})
    return post_entry(db, date=d.get("date"), kind="Receipt Voucher", memo=text(d.get("memo")) or "Client payment",
                      bu_id=bu, party=text(d.get("party")) or bu_row["customer"] or bu_row["name"],
                      ref=text(d.get("ref")), lines=lines,
                      meta={"method": text(d.get("method")), "vat_code": vat_code, "withheld": to_major(withheld)},
                      user=user)


# ------------------------------------------------------------------ partners
def _partner(db, d):
    p = db.one("SELECT * FROM partners WHERE id=? OR name=?", (d.get("partner_id"), text(d.get("partner"))))
    if not p:
        raise ValidationError("Choose a partner")
    return p


def record_partner_tx(db, d, user=""):
    """Capital in, or distribution (dividend/drawing) out."""
    p = _partner(db, d)
    typ = d.get("type") or "Distribution"
    amount = num(d.get("amount"), "amount", allow_zero=False)
    money = money_account(db, d.get("method"), d.get("cash_account"))
    if typ == "Capital":
        lines = [{"account": money, "debit": amount},
                 {"account": account_code(db, d.get("equity_account") or db.acct("capital"), types=("Equity",)),
                  "credit": amount}]
    elif typ == "Distribution":
        lines = [{"account": account_code(db, d.get("equity_account") or db.acct("drawings"), types=("Equity",)),
                  "debit": amount}, {"account": money, "credit": amount}]
    else:
        raise ValidationError("Type must be Capital or Distribution")
    kind = "Capital Contribution" if typ == "Capital" else "Owner Drawing"
    return post_entry(db, date=d.get("date"), kind=kind, memo=text(d.get("memo")) or kind,
                      bu_id=bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), party=p["name"], lines=lines,
                      user=user)


# ----------------------------------------------------------------- journal
def _journal_items(db, raw, bu_default):
    """Item lines of a journal voucher → (debit lines, rows to keep). Each: item (an existing one or a new name), unit,
    qty × unit price, and where it goes — a warehouse (its inventory account; the stock goes up on this same entry, so
    the asset is counted once) or straight onto a project (a cost account, materials unless another is chosen)."""
    out, debits = [], []
    for i, ln in enumerate(raw or []):
        name = text(ln.get("item") or ln.get("name"), maxlen=200)
        if not name and not ln.get("item_id"):
            continue
        qty = num(ln.get("qty"), f"quantity (item {i + 1})", allow_zero=False)
        if qty < 0:
            raise ValidationError(f"Item {i + 1}: the quantity must be positive")
        rate = to_minor(num(ln.get("rate") if ln.get("rate") not in (None, "") else ln.get("price"), f"unit price (item {i + 1})",
                            allow_zero=False))
        amount = int(round(qty * rate))
        wh = ln.get("warehouse_id") or ln.get("warehouse")
        if wh not in (None, "", "project"):
            w = _warehouse(db, wh)
            it = _item(db, {"item": name, "item_id": ln.get("item_id"), "unit": ln.get("unit")}, create=True)
            account, bu = _wh_account(db, w), None
        else:
            w = None
            it = db.one("SELECT * FROM stock_items WHERE id=? OR name=?", (ln.get("item_id"), name))
            account = account_code(db, ln.get("account") or db.acct("materials"), types=("COGS", "Expense"), field="cost account")
            bu = bu_id_or_none(db, ln.get("bu_id") or ln.get("bu_code")) or bu_default
            if not bu:
                raise ValidationError(f"Item {i + 1}: choose the project it was used on")
        if it and ln.get("unit") and not it.get("unit"):
            db.conn.execute("UPDATE stock_items SET unit=? WHERE id=?", (text(ln.get("unit")), it["id"]))
        out.append({"item": it, "description": (it or {}).get("name") or name, "unit": text(ln.get("unit")) or (it or {}).get("unit") or "",
                    "qty": qty, "rate": rate, "amount": amount, "warehouse": w, "account": account, "bu_id": bu})
        debits.append({"account": account, "debit": to_major(amount)})
    return debits, out


def record_journal(db, d, user=""):
    """Manual journal: opening balances, depreciation, tax, loans, accruals... With item lines (qty × unit price) the
    voucher also receives stock into a warehouse or charges materials to a project; give only the other side."""
    with db.tx():
        bu_default = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"))
        debits, items = _journal_items(db, d.get("items"), bu_default)
        meta = {**{k: text(d[k]) for k in ("deal_ref", "deal_status") if d.get(k)},
                # a negotiated deal: what was quoted, the saving and how it is treated (keep / pass)
                **{k: num(d[k], k) for k in ("quoted", "savings") if d.get(k) not in (None, "")},
                **({"negotiation": d["negotiation"]} if d.get("negotiation") in ("keep", "pass") else {})}
        if items:
            meta["items"] = [{"item": x["description"], "unit": x["unit"], "qty": x["qty"], "rate": to_major(x["rate"]),
                              "warehouse": x["warehouse"]["name"] if x["warehouse"] else "", "account": x["account"]} for x in items]
        date = clean_date(d.get("date"))
        eid = _insert_entry(db, date=date, kind=text(d.get("kind")) or "Journal Voucher", memo=text(d.get("memo")),
                            bu_id=bu_default, party=text(d.get("party")), ref=text(d.get("ref")),
                            lines=debits + list(d.get("lines") or []), user=user, meta=meta or None)
        for x in items:
            if x["warehouse"]:
                db.conn.execute("INSERT INTO stock_moves(item_id,warehouse_id,date,qty,rate,bu_id,entry_id,kind,note) "
                                "VALUES (?,?,?,?,?,?,?,?,?)", (x["item"]["id"], x["warehouse"]["id"], date, x["qty"], x["rate"],
                                                               None, eid, "Receipt", "journal voucher"))
                _recompute_level(db, x["item"]["id"], x["warehouse"]["id"])
            db.conn.execute("INSERT INTO material_lines(entry_id,date,bu_id,item_id,description,unit,qty,rate,amount,warehouse_id,account) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (eid, date, x["bu_id"], (x["item"] or {}).get("id"), x["description"], x["unit"], x["qty"], x["rate"],
                             x["amount"], (x["warehouse"] or {}).get("id"), x["account"]))
        return eid


# ------------------------------------------------------------- corrections
def _unlink_documents(db, eid):
    from . import equity
    equity.undo_change(db, entry_id=eid)  # an ownership change booked by this entry goes with it
    db.conn.execute("UPDATE extracts SET status='draft', entry_id=NULL WHERE entry_id=?", (eid,))
    pairs = db.q("SELECT DISTINCT item_id, warehouse_id FROM stock_moves WHERE entry_id=?", (eid,))
    db.conn.execute("DELETE FROM stock_moves WHERE entry_id=?", (eid,))
    db.conn.execute("DELETE FROM material_lines WHERE entry_id=?", (eid,))
    for p in pairs:
        _recompute_level(db, p["item_id"], p["warehouse_id"])


def void_entry(db, eid, reason="", user=""):
    with db.tx():
        e = db.one("SELECT * FROM journal_entries WHERE id=?", (eid,))
        if not e:
            raise ValidationError("Entry not found")
        if e["status"] == "void":
            raise ValidationError("Already void")
        check_open(db, e["date"])
        if e["reversed_by"] or e["reversal_of"]:
            raise ValidationError("This entry is part of a reversal pair and cannot be voided")
        meta = json.loads(e["meta"] or "{}")
        meta["void_reason"] = text(reason)
        meta["voided_by"] = user
        db.conn.execute("UPDATE journal_entries SET status='void', meta=? WHERE id=?",
                        (json.dumps(meta, ensure_ascii=False), eid))
        _unlink_documents(db, eid)
        _wip_after(db, e["kind"], e["bu_id"])


def reverse_entry(db, eid, date=None, user=""):
    with db.tx():
        e = get_entry(db, eid)
        if not e:
            raise ValidationError("Entry not found")
        if e["status"] != "posted" or e["reversed_by"] or e["reversal_of"]:
            raise ValidationError("Only a live, un-reversed entry can be reversed")
        lines = [{"account": ln["account"], "debit": ln["credit"], "credit": ln["debit"], "party": ln.get("party", "")}
                 for ln in e["lines"]]
        rid = _insert_entry(db, date=date or today(), kind="Reversing Entry", memo=f"Reversal of #{eid}: {e['memo']}",
                            bu_id=e["bu_id"], party=e["party"], ref=e["ref"], lines=lines, reversal_of=eid, user=user)
        db.conn.execute("UPDATE journal_entries SET reversed_by=? WHERE id=?", (rid, eid))
        _unlink_documents(db, eid)
        return rid


def edit_entry(db, eid, d, user=""):
    """Correct an entry in place; the previous version is kept in its history."""
    with db.tx():
        e = get_entry(db, eid)
        if not e:
            raise ValidationError("Entry not found")
        if e["status"] != "posted" or e["reversed_by"] or e["reversal_of"]:
            raise ValidationError("Only live entries can be edited")
        check_open(db, e["date"])
        check_open(db, clean_date(d.get("date") or e["date"]))
        if db.one("SELECT 1 FROM stock_moves WHERE entry_id=?", (eid,)) or db.one("SELECT 1 FROM material_lines WHERE entry_id=?", (eid,)) or \
                db.one("SELECT 1 FROM extracts WHERE entry_id=?", (eid,)):
            raise ValidationError("This entry belongs to a stock move or invoice — reverse it and re-enter instead")
        vlines = _validate_lines(db, d.get("lines") or e["lines"])
        snap = {k: e[k] for k in ("date", "memo", "bu_code", "party", "lines")}
        snap.update(ref=e["ref"], edited_at=now_iso(), edited_by=user)
        meta = dict(e["meta"])
        for k in ("deal_ref", "deal_status"):   # the deal a cost belongs to can be corrected here too
            if k in d:
                meta[k] = text(d.get(k))
        db.conn.execute("UPDATE journal_entries SET date=?, memo=?, bu_id=?, party=?, ref=?, meta=?, history=? "
                        "WHERE id=?",
                        (clean_date(d.get("date") or e["date"]), text(d.get("memo", e["memo"]), maxlen=1000),
                         bu_id_or_none(db, d.get("bu_id", e["bu_id"])), text(d.get("party", e["party"])),
                         text(d.get("ref", e["ref"])), json.dumps(meta, ensure_ascii=False),
                         json.dumps(e["history"] + [snap], ensure_ascii=False), eid))
        db.conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (eid,))
        db.conn.executemany("INSERT INTO journal_lines(entry_id,account,debit,credit,party) VALUES (?,?,?,?,?)",
                            [(eid, a, dd, c, p) for a, dd, c, p in vlines])


def delete_entry(db, eid):
    with db.tx():
        e = db.one("SELECT status FROM journal_entries WHERE id=?", (eid,))
        if not e:
            raise ValidationError("Entry not found")
        if e["status"] != "void":
            raise ValidationError("Void an entry before deleting it")
        check_open(db, db.one("SELECT date FROM journal_entries WHERE id=?", (eid,))["date"])
        db.conn.execute("DELETE FROM journal_entries WHERE id=?", (eid,))


# ------------------------------------------------------------- chart of accounts
def save_account(db, d, old_code=None):
    code = text(d.get("code"), "code", required=True, maxlen=20)
    name = text(d.get("name"), "name", required=True)
    name_ar = text(d.get("name_ar"))
    typ = d.get("type")
    if typ not in SUBTYPES:
        raise ValidationError("Choose an account type")
    sub = d.get("subtype") or SUBTYPES[typ][0]
    if sub not in SUBTYPES[typ]:
        raise ValidationError(f"Subtype {sub!r} does not belong to {typ}")
    role = text(d.get("role")) or None
    active = 0 if d.get("active") in (0, "0", False, "false") else 1
    with db.tx():
        if old_code:
            cur = db.one("SELECT * FROM accounts WHERE code=?", (old_code,))
            if not cur:
                raise ValidationError("Account not found")
            if code != old_code and db.one("SELECT 1 FROM accounts WHERE code=?", (code,)):
                raise ValidationError(f"Code {code} is already used")
            if cur["role"] and not active:
                raise ValidationError("System accounts cannot be deactivated")
            used = db.one("SELECT 1 FROM journal_lines WHERE account=? LIMIT 1", (old_code,))
            if used and cur["type"] != typ:
                raise ValidationError("This account has postings; its type cannot change (subtype can)")
            db.conn.execute("UPDATE accounts SET code=?, name=?, name_ar=?, type=?, subtype=?, normal=?, active=? "
                            "WHERE code=?", (code, name, name_ar, typ, sub, normal_for(typ, sub), active, old_code))
        else:
            if db.one("SELECT 1 FROM accounts WHERE code=?", (code,)):
                raise ValidationError(f"Code {code} is already used")
            db.conn.execute("INSERT INTO accounts(code,name,name_ar,type,subtype,normal,active) VALUES (?,?,?,?,?,?,?)",
                            (code, name, name_ar, typ, sub, normal_for(typ, sub), active))
    return code


def delete_account(db, code):
    a = db.one("SELECT * FROM accounts WHERE code=?", (code,))
    if not a:
        raise ValidationError("Account not found")
    if a["role"]:
        raise ValidationError("System accounts cannot be deleted (you can rename or renumber them)")
    if db.one("SELECT 1 FROM journal_lines WHERE account=? LIMIT 1", (code,)):
        raise ValidationError("Account has postings — deactivate it instead")
    db.x("DELETE FROM accounts WHERE code=?", (code,))


# --------------------------------------------------------------------- stock
def _warehouse(db, value):
    w = db.one("SELECT * FROM warehouses WHERE id=? OR name=?", (value, text(value)))
    if not w:
        raise ValidationError(f"Unknown warehouse: {value}")
    return w


def _wh_account(db, w):
    return w["account"] or db.acct("inventory")


def _recompute_level(db, item_id, wh_id):
    qty, avg = 0.0, 0.0
    for m in db.q("SELECT qty, rate FROM stock_moves WHERE item_id=? AND warehouse_id=? ORDER BY date, id",
                  (item_id, wh_id)):
        if m["qty"] > 0:
            new_qty = qty + m["qty"]
            avg = ((max(qty, 0) * avg) + m["qty"] * m["rate"]) / max(new_qty, 1e-9) if new_qty > 0 else m["rate"]
            qty = new_qty
        else:
            qty += m["qty"]
    db.conn.execute("INSERT OR REPLACE INTO stock_levels(item_id,warehouse_id,qty,avg_cost) VALUES (?,?,?,?)",
                    (item_id, wh_id, round(qty, 6), avg))


def _item(db, d, create=False):
    name = text(d.get("item") or d.get("name"), "item", required=True)
    it = db.one("SELECT * FROM stock_items WHERE id=? OR name=?", (d.get("item_id"), name))
    if it:
        return it
    if not create:
        raise ValidationError(f"Unknown item: {name}")
    iid = db.conn.execute("INSERT INTO stock_items(name,unit) VALUES (?,?)", (name, text(d.get("unit")) or "pcs")).lastrowid
    return db.one("SELECT * FROM stock_items WHERE id=?", (iid,))


def _level(db, item_id, wh_id):
    return db.one("SELECT * FROM stock_levels WHERE item_id=? AND warehouse_id=?", (item_id, wh_id)) or \
        {"qty": 0.0, "avg_cost": 0.0}


def stock_move(db, d, user=""):
    """One entry point for Receipt / Issue / Transfer / Adjustment."""
    typ = d.get("type") or "Receipt"
    if typ not in STOCK_MOVE_TYPES:
        raise ValidationError("Unknown stock move type")
    date = clean_date(d.get("date"))
    with db.tx():
        wh = _warehouse(db, d.get("warehouse_id") or d.get("warehouse"))
        item = _item(db, d, create=(typ == "Receipt"))
        lvl = _level(db, item["id"], wh["id"])
        inv = _wh_account(db, wh)
        if typ == "Receipt":
            qty = num(d.get("qty"), "quantity", allow_zero=False)
            rate = num(d.get("rate"), "unit cost", allow_zero=False)
            total = to_minor(qty * rate)
            vat_code, vat, wht_code, wht = _purchase_taxes(db, d, total)
            paid = to_minor(num(d.get("paid"), "paid now"))
            vendor = text(d.get("vendor"))
            if (paid < total + vat - wht or wht) and not vendor:
                raise ValidationError("Name the supplier so the unpaid balance / withholding can be tracked")
            payable = account_code(db, d.get("payable_account") or db.acct("ap"), types=("Liability",))
            lines = _purchase_lines(db, d, inv, total, vat, wht, payable, paid)
            eid = _insert_entry(db, date=date, kind="Goods Receipt", memo=f"Received {qty:g} {item['unit']} "
                                f"{item['name']} → {wh['name']}", party=vendor, lines=lines, user=user,
                                meta={"qty": qty, "rate": rate, "item": item["name"], "warehouse": wh["name"],
                                      "vat_code": vat_code, "wht_code": wht_code})
            moves = [(wh["id"], qty, rate * 100, None)]
        elif typ == "Issue":
            qty = num(d.get("qty"), "quantity", allow_zero=False)
            if qty > lvl["qty"] + 1e-9:
                raise ValidationError(f"Only {lvl['qty']:g} {item['unit']} of {item['name']} in {wh['name']}")
            value = int(round(qty * lvl["avg_cost"]))
            if value <= 0:
                raise ValidationError("Item has no cost value to issue")
            bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
            cost_acc = account_code(db, d.get("account") or db.acct("materials"), types=("COGS", "Expense"))
            eid = _insert_entry(db, date=date, kind="Material Issue", bu_id=bu, user=user,
                                memo=f"Issued {qty:g} {item['unit']} {item['name']} from {wh['name']}",
                                lines=[{"account": cost_acc, "debit": to_major(value)},
                                       {"account": inv, "credit": to_major(value)}],
                                meta={"qty": qty, "item": item["name"], "warehouse": wh["name"]})
            moves = [(wh["id"], -qty, lvl["avg_cost"], bu)]
        elif typ == "Transfer":
            qty = num(d.get("qty"), "quantity", allow_zero=False)
            to = _warehouse(db, d.get("to_warehouse_id") or d.get("to_warehouse"))
            if to["id"] == wh["id"]:
                raise ValidationError("Pick a different destination warehouse")
            if qty > lvl["qty"] + 1e-9:
                raise ValidationError(f"Only {lvl['qty']:g} {item['unit']} in {wh['name']}")
            value = int(round(qty * lvl["avg_cost"]))
            to_acc = _wh_account(db, to)
            eid = None
            if to_acc != inv and value:
                eid = _insert_entry(db, date=date, kind="Stock Transfer", user=user,
                                    memo=f"Transfer {qty:g} {item['name']}: {wh['name']} → {to['name']}",
                                    lines=[{"account": to_acc, "debit": to_major(value)},
                                           {"account": inv, "credit": to_major(value)}])
            moves = [(wh["id"], -qty, lvl["avg_cost"], None), (to["id"], qty, lvl["avg_cost"], None)]
        else:  # Adjustment to a physical count
            counted = num(d.get("counted"), "counted quantity")
            diff = counted - lvl["qty"]
            if abs(diff) < 1e-9:
                raise ValidationError("Count matches the system — nothing to adjust")
            rate = lvl["avg_cost"] or to_minor(num(d.get("rate"), "unit cost"))
            value = int(round(abs(diff) * rate))
            adj = db.acct("stock_adjust")
            eid = None
            if value:
                lines = ([{"account": inv, "debit": to_major(value)}, {"account": adj, "credit": to_major(value)}]
                         if diff > 0 else
                         [{"account": adj, "debit": to_major(value)}, {"account": inv, "credit": to_major(value)}])
                eid = _insert_entry(db, date=date, kind="Stock Adjustment", user=user, lines=lines,
                                    memo=f"Count adjustment — {item['name']} @ {wh['name']} ({diff:+g})")
            moves = [(wh["id"], diff, rate, None)]
        for wid, q, r, bu in moves:
            db.conn.execute("INSERT INTO stock_moves(item_id,warehouse_id,date,qty,rate,bu_id,entry_id,kind,note) "
                            "VALUES (?,?,?,?,?,?,?,?,?)", (item["id"], wid, date, q, r, bu, eid, typ,
                                                           text(d.get("note"))))
            _recompute_level(db, item["id"], wid)
        return eid


def materials_used(db, bu_id=None, q=None, date_from=None, date_to=None, limit=5000, item=None, source=None):
    """Every material that went into a project, wherever it was recorded: issued from a store (Material Issue), bought
    straight for the project on a journal voucher's item lines, or a direct cost on a materials account. Newest first."""
    live = f"{LIVE} AND e.reversed_by IS NULL AND e.reversal_of IS NULL"
    rows = []
    for r in db.q(f"""SELECT m.entry_id, m.date, m.bu_id, m.description, m.unit, m.qty, m.rate, m.amount, e.party, e.kind
                      FROM material_lines m JOIN journal_entries e ON e.id=m.entry_id
                      WHERE {live} AND m.warehouse_id IS NULL"""):
        rows.append({**r, "source": "bought", "from": r["party"] or ""})
    for r in db.q(f"""SELECT s.entry_id, s.date, s.bu_id, i.name AS description, i.unit, -s.qty AS qty, s.rate, w.name AS wh,
                             e.party, e.kind FROM stock_moves s JOIN stock_items i ON i.id=s.item_id JOIN warehouses w ON w.id=s.warehouse_id
                      JOIN journal_entries e ON e.id=s.entry_id WHERE {live} AND s.kind='Issue' AND s.qty<0 AND s.bu_id IS NOT NULL"""):
        rows.append({**r, "amount": int(round(r["qty"] * r["rate"])), "source": "issued", "from": r.pop("wh")})
    mats = [a["code"] for a in db.q("SELECT code FROM accounts WHERE role='materials' OR subtype='materials'")]
    if mats:
        marks = ",".join("?" * len(mats))
        for r in db.q(f"""SELECT e.id AS entry_id, e.date, e.bu_id, e.memo AS description, e.meta, e.party, e.kind,
                                 SUM(l.debit) AS amount FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id
                          WHERE {live} AND e.kind='Direct Cost' AND l.account IN ({marks}) GROUP BY e.id""", mats):
            m = json.loads(r.pop("meta") or "{}")
            qty = float(m.get("qty") or 1)
            rows.append({**r, "unit": m.get("unit") or "", "qty": qty, "rate": int(round(r["amount"] / qty)) if qty else r["amount"],
                         "source": "direct", "from": r["party"] or ""})
    bus = {b["id"]: b for b in db.q("SELECT id, code, name FROM business_units")}
    want_bu = int(bu_id) if str(bu_id or "").isdigit() else None
    needle = (q or "").strip().lower()
    want_item = (item or "").strip().lower()
    names = sorted({r["description"] for r in rows if r.get("description")}, key=str.lower)
    out = []
    for r in rows:
        if want_item and (r.get("description") or "").strip().lower() != want_item:
            continue
        if source and r["source"] != source:
            continue
        if want_bu and r["bu_id"] != want_bu:
            continue
        if date_from and r["date"] < clean_date(date_from):
            continue
        if date_to and r["date"] > clean_date(date_to):
            continue
        b = bus.get(r["bu_id"]) or {}
        r["bu_code"], r["bu_name"] = b.get("code", ""), b.get("name", "")
        if needle and needle not in " ".join(str(r.get(k) or "") for k in ("description", "from", "bu_code", "bu_name", "unit")).lower():
            continue
        r["rate"], r["amount"] = to_major(r["rate"]), to_major(r["amount"])
        out.append(r)
    out.sort(key=lambda r: (r["date"], r["entry_id"]), reverse=True)
    units = {(r.get("unit") or "").strip().lower() for r in out}
    # a quantity total only makes sense for one material in one unit (a whole project mixes m², drums, bags…)
    one = {"qty": round(sum(r["qty"] or 0 for r in out), 4), "unit": (out[0].get("unit") or "") if out else ""} \
        if want_item and out and len(units) == 1 else None
    return {"rows": out[:limit], "count": len(out), "total": round(sum(r["amount"] for r in out), 2), "one_material": one,
            "materials": names}


def stock_report(db, warehouse_id=None, q=None):
    w, p = [], []
    if warehouse_id:
        w.append("l.warehouse_id=?")
        p.append(int(warehouse_id))
    if q:
        w.append("i.name LIKE ?")
        p.append(f"%{q}%")
    rows = db.q("SELECT i.id AS item_id, i.name, i.unit, i.reorder_level, w.id AS warehouse_id, w.name AS warehouse, "
                "l.qty, l.avg_cost FROM stock_levels l JOIN stock_items i ON i.id=l.item_id "
                "JOIN warehouses w ON w.id=l.warehouse_id" + (" WHERE " + " AND ".join(w) if w else "") +
                " ORDER BY i.name, w.name", p)
    for r in rows:
        r["value"] = to_major(r["qty"] * r["avg_cost"])
        r["avg_cost"] = to_major(r["avg_cost"])
        r["low"] = bool(r["reorder_level"]) and r["qty"] <= r["reorder_level"]
    return rows


# ------------------------------------------------------------- BOQ & extracts
def _lines_in(raw):
    out = []
    for i, ln in enumerate(raw or []):
        desc = text(ln.get("description"), maxlen=2000)
        if not desc:
            continue
        out.append({"seq": i, "category": text(ln.get("category")), "sub": text(ln.get("sub")),
                    "item_no": text(ln.get("item_no")), "description": desc, "unit": text(ln.get("unit")),
                    "qty": num(ln.get("qty"), "quantity"), "price": to_minor(num(ln.get("price"), "price")),
                    "cost": to_minor(num(ln.get("cost"), "cost")), "account": text(ln.get("account"), maxlen=20),
                    "pct": _pct_in(ln.get("pct")), "style": line_style(ln.get("style"))})
    return out


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def line_style(v):
    """A BOQ / invoice line's look, as JSON text ('' = plain): b, i, u; color and bg (#rrggbb); align left|center|right."""
    if isinstance(v, str):
        try:
            v = json.loads(v) if v.strip() else {}
        except ValueError:
            v = {}
    if not isinstance(v, dict):
        return ""
    out = {k: 1 for k in ("b", "i", "u") if v.get(k)}
    for k in ("color", "bg"):
        if isinstance(v.get(k), str) and _HEX.match(v[k]):
            out[k] = v[k].lower()
    if v.get("align") in ("left", "center", "right"):
        out["align"] = v["align"]
    return json.dumps(out, sort_keys=True) if out else ""


def _pct_in(v):
    """The share of an item executed on an extract line (النسبة): 1 = 100 %; '50%', 50 or 0.5 all mean half."""
    if v in (None, ""):
        return 1.0
    s = str(v).strip().replace("%", "")
    p = num(s, "percentage")
    if p > 1 or "%" in str(v):
        p = p / 100
    if p < 0 or p > 1:
        raise ValidationError("The percentage must be between 0 and 100 %")
    return p


def _line_out(ln):
    ln = dict(ln)
    if "style" in ln:
        ln["style"] = json.loads(ln["style"]) if ln["style"] else {}
    ln["price"] = to_major(ln["price"])
    if "cost" in ln:
        ln["cost"] = to_major(ln["cost"])
    pct = ln.get("pct")
    pct = 1.0 if pct is None else pct
    ln["amount"] = round(ln["qty"] * ln["price"] * pct, 2)
    return ln


def save_boq(db, d, boq_id=None):
    lines = _lines_in(d.get("lines"))
    bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"))
    with db.tx():
        vals = (bu, text(d.get("title")) or "BOQ", text(d.get("rev")) or "Rev 01", clean_date(d.get("date")),
                text(d.get("client")), text(d.get("location")), text(d.get("notes"), maxlen=4000))
        if boq_id:
            if not db.one("SELECT 1 FROM boqs WHERE id=?", (boq_id,)):
                raise ValidationError("BOQ not found")
            db.conn.execute("UPDATE boqs SET bu_id=?,title=?,rev=?,date=?,client=?,location=?,notes=? WHERE id=?",
                            vals + (boq_id,))
            db.conn.execute("DELETE FROM boq_lines WHERE boq_id=?", (boq_id,))
        else:
            boq_id = db.conn.execute("INSERT INTO boqs(bu_id,title,rev,date,client,location,notes) "
                                     "VALUES (?,?,?,?,?,?,?)", vals).lastrowid
        db.conn.executemany("INSERT INTO boq_lines(boq_id,seq,category,sub,item_no,description,unit,qty,price,cost,style) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            [(boq_id, ln["seq"], ln["category"], ln["sub"], ln["item_no"], ln["description"],
                              ln["unit"], ln["qty"], ln["price"], ln["cost"], ln["style"]) for ln in lines])
    return boq_id


def get_boq(db, boq_id):
    b = db.one("SELECT b.*, u.code AS bu_code, u.name AS bu_name FROM boqs b "
               "LEFT JOIN business_units u ON u.id=b.bu_id WHERE b.id=?", (boq_id,))
    if not b:
        return None
    b["lines"] = [_line_out(ln) for ln in db.q("SELECT * FROM boq_lines WHERE boq_id=? ORDER BY seq, id", (boq_id,))]
    b["total"] = round(sum(ln["amount"] for ln in b["lines"]), 2)
    b["cost_total"] = round(sum(ln["qty"] * ln["cost"] for ln in b["lines"]), 2)
    b["billed"] = round(sum(x["total"] for x in list_extracts(db, boq_id=boq_id) if x["status"] == "posted"), 2)
    return b


def save_extract(db, d, extract_id=None):
    bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
    number = text(d.get("number"), "invoice / extract number", required=True)
    lines = _lines_in(d.get("lines"))
    boq_id = d.get("boq_id") or None
    vat_code, _ = tax_code(db, d.get("vat_code"), "vat")
    wht_code, _ = tax_code(db, d.get("wht_code"), "wht")
    old_bu = (db.one("SELECT bu_id FROM extracts WHERE id=?", (extract_id,)) or {}).get("bu_id") if extract_id else None
    with db.tx():
        prev = d.get("prev_paid")
        prev = None if prev in (None, "") else to_minor(num(prev, "previously paid"))
        seq_no = int(num(d.get("seq_no") or 0, "extract no."))
        if not seq_no and not extract_id:   # extract no. on this site: the next one
            r = db.one("SELECT COALESCE(MAX(seq_no),0) AS n, COUNT(*) AS c FROM extracts WHERE bu_id=?", (bu,))
            seq_no = max(r["n"] or 0, r["c"] or 0) + 1
        vals = (bu, boq_id, number, clean_date(d.get("date")), text(d.get("customer")), vat_code, wht_code,
                text(d.get("project")), text(d.get("work")), seq_no, prev, to_minor(num(d.get("deductions") or 0, "deductions")),
                text(d.get("notes"), maxlen=2000), 1 if d.get("final") in (1, True, "1", "true", "yes", "Yes") else 0)
        if extract_id:
            x = db.one("SELECT status FROM extracts WHERE id=?", (extract_id,))
            if not x:
                raise ValidationError("Extract not found")
            if x["status"] == "posted":
                raise ValidationError("Posted extracts are locked — reverse the invoice entry first")
            db.conn.execute("UPDATE extracts SET bu_id=?,boq_id=?,number=?,date=?,customer=?,vat_code=?,wht_code=?,"
                            "project=?,work=?,seq_no=?,prev_paid=?,deductions=?,notes=?,final=? WHERE id=?", vals + (extract_id,))
            db.conn.execute("DELETE FROM extract_lines WHERE extract_id=?", (extract_id,))
        else:
            if db.one("SELECT 1 FROM extracts WHERE number=?", (number,)):
                raise ValidationError(f"Invoice number {number} already exists")
            extract_id = db.conn.execute("INSERT INTO extracts(bu_id,boq_id,number,date,customer,vat_code,wht_code,project,work,"
                                         "seq_no,prev_paid,deductions,notes,final) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals).lastrowid
        for ln in lines:   # a line may earn into its own revenue account (rent, services, compensation...)
            if ln["account"] and ln["account"] != db.acct("service_deferred"):   # billed in advance, earned month by month
                account_code(db, ln["account"], types=("Revenue",), field="revenue account")
        db.conn.executemany("INSERT INTO extract_lines(extract_id,seq,category,sub,item_no,description,unit,qty,price,account,pct,style) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            [(extract_id, ln["seq"], ln["category"], ln["sub"], ln["item_no"], ln["description"],
                              ln["unit"], ln["qty"], ln["price"], ln["account"], ln["pct"], ln["style"]) for ln in lines])
        _wip_after(db, "Sales Invoice", bu, old_bu)
    return extract_id


def get_extract(db, xid):
    x = db.one("SELECT x.*, u.code AS bu_code, u.name AS bu_name, u.location AS bu_location, u.customer AS bu_customer, "
               "b.title AS boq_title "
               "FROM extracts x JOIN business_units u ON u.id=x.bu_id LEFT JOIN boqs b ON b.id=x.boq_id "
               "WHERE x.id=?", (xid,))
    if not x:
        return None
    x["lines"] = [_line_out(ln) for ln in
                  db.q("SELECT * FROM extract_lines WHERE extract_id=? ORDER BY seq, id", (xid,))]
    x["total"] = round(sum(ln["amount"] for ln in x["lines"]), 2)
    x.update(invoice_taxes(db, x))
    if x["boq_id"]:
        contract = {ln["item_no"] or ln["description"]: ln["qty"]
                    for ln in db.q("SELECT item_no, description, qty FROM boq_lines WHERE boq_id=?", (x["boq_id"],))}
        before = {}
        for r in db.q("SELECT l.item_no, l.description, l.qty FROM extract_lines l JOIN extracts e "
                      "ON e.id=l.extract_id WHERE e.boq_id=? AND e.status='posted' AND e.id<>?", (x["boq_id"], xid)):
            k = r["item_no"] or r["description"]
            before[k] = before.get(k, 0) + r["qty"]
        for ln in x["lines"]:
            k = ln["item_no"] or ln["description"]
            ln["contract_qty"] = contract.get(k)
            ln["billed_before"] = before.get(k, 0)
    return x


def invoice_taxes(db, x):
    """VAT and WHT for a sales invoice, computed on its net total."""
    _, vr = tax_code(db, x.get("vat_code"), "vat")
    _, wr = tax_code(db, x.get("wht_code"), "wht")
    net = to_minor(x["total"])
    vat, wht = int(round(net * vr)), int(round(net * wr))
    party = x.get("customer") or ""
    return {"vat": to_major(vat), "wht": to_major(wht), "gross": to_major(net + vat),
            "receivable": to_major(net + vat - wht), "customer_name": party}


def list_extracts(db, boq_id=None, bu_id=None, status=None, date_from=None, date_to=None):
    w, p = [], []
    for cond, val in (("x.boq_id=?", boq_id), ("x.bu_id=?", bu_id), ("x.status=?", status),
                      ("x.date>=?", date_from), ("x.date<=?", date_to)):
        if val:
            w.append(cond)
            p.append(val)
    rows = db.q("SELECT x.*, u.code AS bu_code, b.title AS boq_title, "
                "(SELECT COALESCE(SUM(ROUND(qty*price*COALESCE(pct,1))),0) FROM extract_lines WHERE extract_id=x.id) AS total_minor "
                "FROM extracts x JOIN business_units u ON u.id=x.bu_id LEFT JOIN boqs b ON b.id=x.boq_id" +
                (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY x.date DESC, x.id DESC", p)
    for r in rows:
        r["total"] = to_major(r.pop("total_minor"))
    return rows


def post_extract(db, xid, date=None, user="", ar_account=None, revenue_account=None):
    """Invoice executed work: Dr Receivables / Cr Revenue."""
    x = get_extract(db, xid)
    if not x:
        raise ValidationError("Extract not found")
    if x["status"] == "posted":
        raise ValidationError("Already invoiced")
    if x["total"] <= 0:
        raise ValidationError("Extract total must be greater than zero")
    ar = account_code(db, ar_account or db.acct("ar"), types=("Asset",))
    rev = account_code(db, revenue_account or db.acct("revenue"), types=("Revenue",))
    lines = [{"account": ar, "debit": x["receivable"]}]
    if x["wht"]:
        lines.append({"account": db.acct("wht_receivable"), "debit": x["wht"]})
    by_acc = {}
    for ln in x["lines"]:
        a = ln.get("account") or rev
        by_acc[a] = by_acc.get(a, 0) + to_minor(ln["amount"])
    diff = to_minor(x["total"]) - sum(by_acc.values())   # rounding lands on the main revenue account
    by_acc[rev] = by_acc.get(rev, 0) + diff
    # Work already recognised as "done, not yet invoiced" (unbilled revenue / contract asset) is billed out of that
    # balance, not earned a second time: Dr Receivables / Cr Unbilled revenue, and only the rest is new revenue.
    from . import wip
    managed = wip.managed(db, x["bu_id"])          # kept up to date automatically: it adjusts once this is posted
    unbilled_acc, billed_from = db.acct("unbilled_revenue") if x["bu_id"] and not managed else None, 0
    if unbilled_acc:
        bal = db.one(f"SELECT COALESCE(SUM(l.debit-l.credit),0) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                     f"WHERE {LIVE} AND l.account=? AND e.bu_id=?", (unbilled_acc, x["bu_id"]))["n"]
        total = sum(by_acc.values())
        billed_from = max(0, min(bal, total))
        if billed_from:
            keep = total - billed_from
            scaled = {a: (v * keep) // total for a, v in by_acc.items()}
            scaled[rev] = scaled.get(rev, 0) + keep - sum(scaled.values())
            by_acc = scaled
            by_acc[unbilled_acc] = by_acc.get(unbilled_acc, 0) + billed_from
    for a, v in by_acc.items():
        if v:
            lines.append({"account": a, "credit": to_major(v)} if v > 0 else {"account": a, "debit": to_major(-v)})
    if x["vat"]:
        lines.append({"account": db.acct("vat_output"), "credit": x["vat"]})
    with db.tx():
        eid = _insert_entry(db, date=date or x["date"], kind="Sales Invoice", memo=f"Invoice — {x['number']}",
                            bu_id=x["bu_id"], party=x["customer"] or x["bu_customer"] or x["bu_name"], ref=x["number"],
                            user=user, lines=lines,
                            meta={"extract_id": xid, "vat_code": x["vat_code"], "wht_code": x["wht_code"],
                                  **({"billed_from_unbilled": to_major(billed_from)} if billed_from else {})})
        db.conn.execute("UPDATE extracts SET status='posted', entry_id=? WHERE id=?", (eid, xid))
        _wip_after(db, "Sales Invoice", x["bu_id"])
    return eid


# ====================================================================== REPORTS
LIVE = "e.status='posted'"
PARTY = "COALESCE(NULLIF(l.party,''), e.party)"  # a line's own party, else the entry's
def ref_prefix(prefix):
    """A GLOB pattern for references starting with this text (case-sensitive, so the ref index is used)."""
    return re.sub(r"([*?\[])", r"[\1]", prefix) + "*"


PARTY_IS = "l.id IN (SELECT id FROM party_lines WHERE party=?)"  # same as {PARTY_IS} but uses the indexes



_MEMO = weakref.WeakKeyDictionary()


def memo(fn):
    """Figures read from the books, reused until anything is written. Keyed on the connection's change count
    and SQLite's data_version (which moves when another connection commits) plus today's date, so a cached
    answer can never be stale; callers get their own copy."""
    @functools.wraps(fn)
    def wrap(db, *a, **kw):
        conn = getattr(db, "conn", None)
        ver = getattr(conn, "total_changes", None)
        if ver is None:                       # browser build: no cheap change counter
            return fn(db, *a, **kw)
        try:
            with db.lock:
                dv = conn.execute("PRAGMA data_version").fetchone()[0]
            key = (fn.__name__, a, tuple(sorted(kw.items())))
            hash(key)
        except (TypeError, AttributeError):
            return fn(db, *a, **kw)
        stamp = (ver, dv, today())
        try:
            box = _MEMO.get(db)
        except TypeError:
            return fn(db, *a, **kw)
        if not box or box[0] != stamp or box[2] is not conn:     # a restore opens a new connection: start again
            box = _MEMO[db] = (stamp, {}, conn)
        if key not in box[1]:
            if len(box[1]) > 200:
                box[1].clear()
            box[1][key] = fn(db, *a, **kw)
        return copy.deepcopy(box[1][key])
    return wrap

def _where(date_from=None, date_to=None, bu_id=None):
    w, p = [LIVE], []
    if date_from:
        w.append("e.date>=?")
        p.append(clean_date(date_from))
    if date_to:
        w.append("e.date<=?")
        p.append(clean_date(date_to))
    if bu_id not in (None, ""):
        if bu_id == "none":
            w.append("e.bu_id IS NULL")
        else:
            w.append("e.bu_id=?")
            p.append(int(bu_id))
    return " AND ".join(w), p


@memo
def account_totals(db, **kw):
    w, p = _where(**kw)
    rows = db.q(f"""SELECT a.code, a.name, a.type, a.subtype, a.normal, a.role,
                    COALESCE(t.dr,0) AS dr, COALESCE(t.cr,0) AS cr
                    FROM accounts a LEFT JOIN (SELECT l.account, SUM(l.debit) AS dr, SUM(l.credit) AS cr
                         FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id WHERE {w} GROUP BY l.account) t
                      ON t.account=a.code ORDER BY a.code""", p)
    for r in rows:
        r["net"] = r["dr"] - r["cr"]  # debit-positive
    return rows


def _day_before(d):
    return (_dt.date.fromisoformat(clean_date(d)) - _dt.timedelta(days=1)).isoformat()


def trial_balance(db, date_from=None, date_to=None, bu_id=None):
    """Opening, period movement and closing per account (debit/credit columns)."""
    opening = {r["code"]: r["net"] for r in account_totals(db, date_to=_day_before(date_from), bu_id=bu_id)} \
        if date_from else {}
    rows = []
    for r in account_totals(db, date_from=date_from, date_to=date_to, bu_id=bu_id):
        o = opening.get(r["code"], 0)
        c = o + r["net"]
        if not (o or r["dr"] or r["cr"]):
            continue
        rows.append({"code": r["code"], "name": r["name"], "type": r["type"], "subtype": r["subtype"],
                     "opening_debit": to_major(max(o, 0)), "opening_credit": to_major(max(-o, 0)),
                     "period_debit": to_major(r["dr"]), "period_credit": to_major(r["cr"]),
                     "debit": to_major(max(c, 0)), "credit": to_major(max(-c, 0))})
    tot = {k: round(sum(r[k] for r in rows), 2) for k in
           ("opening_debit", "opening_credit", "period_debit", "period_credit", "debit", "credit")}
    return {"rows": rows, "totals": tot, "balanced": abs(tot["debit"] - tot["credit"]) < 0.005,
            "date_from": date_from, "date_to": date_to}


def _by_subtype(rows):
    m = {}
    for r in rows:
        m.setdefault(r["subtype"], []).append(r)
    return m


@memo
def income_statement(db, date_from=None, date_to=None, bu_id=None, allocate_overhead=False):
    """Revenue → Net sales → Gross profit → Operating expenses → EBITDA → D&A → EBIT
    → interest & other → EBT → income tax → Net profit."""
    rows = [r for r in account_totals(db, date_from=date_from, date_to=date_to, bu_id=bu_id)
            if r["type"] in ("Revenue", "COGS", "Expense") and (r["dr"] or r["cr"])]
    sub = _by_subtype(rows)

    def lines(subtype, sign):  # sign +1: credit-positive (income), -1: debit-positive (cost)
        return [{"code": r["code"], "name": r["name"], "amount": to_major(-r["net"] if sign > 0 else r["net"])}
                for r in sub.get(subtype, [])]

    def total(ls):
        return round(sum(x["amount"] for x in ls), 2)

    revenue = lines("operating_revenue", +1)
    discounts = lines("sales_discount", -1)
    cogs = lines("cogs", -1)
    opex_groups = [("Employee expenses", lines("employee", -1)),
                   ("General & administrative expenses", lines("general_admin", -1)),
                   ("Marketing & advertising expenses", lines("marketing", -1)),
                   ("Utilities (electricity / telephone)", lines("utilities", -1)),
                   ("Other operating expenses", lines("other_operating", -1))]
    dep, amort = lines("depreciation", -1), lines("amortization", -1)
    interest, other_nonop = lines("interest", -1), lines("other_nonoperating", -1)
    other_income = lines("other_income", +1)
    tax = lines("income_tax", -1)

    gross_sales = total(revenue)
    net_sales = round(gross_sales - total(discounts), 2)
    cost_of_sales = total(cogs)
    gross_profit = round(net_sales - cost_of_sales, 2)
    opex = round(sum(total(g) for _, g in opex_groups), 2)
    allocated = 0.0
    if allocate_overhead and bu_id not in (None, "", "none"):
        allocated = allocated_overhead(db, int(bu_id), date_from, date_to)
        opex_groups.append(("Allocated shared overhead", [{"code": "", "name": "Share of company overhead",
                                                           "amount": allocated}] if allocated else []))
        opex = round(opex + allocated, 2)
    ebitda = round(gross_profit - opex, 2)
    da = round(total(dep) + total(amort), 2)
    ebit = round(ebitda - da, 2)
    ebt = round(ebit - total(interest) - total(other_nonop) + total(other_income), 2)
    net = round(ebt - total(tax), 2)
    pct = (lambda v: round(v / net_sales, 4) if net_sales else 0)
    return {
        "date_from": date_from, "date_to": date_to, "bu_id": bu_id,
        "revenue": revenue, "gross_sales": gross_sales, "discounts": discounts, "net_sales": net_sales,
        "cogs": cogs, "cost_of_sales": cost_of_sales, "gross_profit": gross_profit,
        "opex_groups": [{"label": lbl, "lines": ls, "total": total(ls)} for lbl, ls in opex_groups],
        "operating_expenses": opex, "allocated_overhead": allocated, "ebitda": ebitda,
        "depreciation": dep, "amortization": amort, "d_and_a": da, "ebit": ebit,
        "interest": interest, "other_nonoperating": other_nonop, "other_income": other_income, "ebt": ebt,
        "tax": tax, "income_tax": total(tax), "net_profit": net,
        "margins": {"gross": pct(gross_profit), "ebitda": pct(ebitda), "ebit": pct(ebit), "net": pct(net)},
    }


BS_CURRENT_ASSETS = ["cash", "receivable", "allowance_doubtful", "inventory", "prepaid", "other_current_asset"]
BS_NONCURRENT_ASSETS = ["fixed_asset", "accumulated_depreciation", "intangible", "accumulated_amortization", "investment",
                        "other_noncurrent_asset"]
BS_CURRENT_LIABILITIES = ["payable", "accrued", "deferred_revenue", "tax_payable", "dividends_payable",
                          "other_current_liability", "short_term_debt"]
BS_NONCURRENT_LIABILITIES = ["long_term_debt", "other_noncurrent_liability"]
BS_EQUITY = ["capital", "partner_current", "reserves", "retained_earnings", "other_equity", "drawings"]


@memo
def balance_sheet(db, date_to=None, bu_id=None):
    rows = account_totals(db, date_to=date_to, bu_id=bu_id)
    sub = _by_subtype([r for r in rows if r["dr"] or r["cr"]])
    earnings = -sum(r["net"] for r in rows if r["type"] in ("Revenue", "COGS", "Expense"))

    def sect(subtypes, sign):
        out = []
        for s in subtypes:
            for r in sub.get(s, []):
                out.append({"code": r["code"], "name": r["name"], "subtype": s, "amount": to_major(sign * r["net"])})
        return out

    cur_assets = sect(BS_CURRENT_ASSETS, 1)
    nc_assets = sect(BS_NONCURRENT_ASSETS, 1)
    cur_liab = sect(BS_CURRENT_LIABILITIES, -1)
    nc_liab = sect(BS_NONCURRENT_LIABILITIES, -1)
    equity = sect(BS_EQUITY, -1)
    # closed financial years (yearend.py): their profit is retained earnings; only the profit since is current earnings
    closed_to = db.one("SELECT MAX(year_end) e FROM year_closes" + (" WHERE year_end<=?" if date_to else ""),
                       (clean_date(date_to),) if date_to else ())["e"]
    if closed_to:
        prior = -sum(r["net"] for r in account_totals(db, date_to=closed_to, bu_id=bu_id) if r["type"] in ("Revenue", "COGS", "Expense"))
        equity.append({"code": "", "name": "Retained earnings — closed years", "subtype": "earnings_closed", "amount": to_major(prior)})
        earnings -= prior
    equity.append({"code": "", "name": "Current earnings (profit to date)", "subtype": "earnings",
                   "amount": to_major(earnings)})
    t = lambda ls: round(sum(x["amount"] for x in ls), 2)  # noqa: E731
    ta = round(t(cur_assets) + t(nc_assets), 2)
    tl = round(t(cur_liab) + t(nc_liab), 2)
    te = t(equity)
    return {"date_to": date_to, "current_assets": cur_assets, "noncurrent_assets": nc_assets,
            "current_liabilities": cur_liab, "noncurrent_liabilities": nc_liab, "equity": equity,
            "total_current_assets": t(cur_assets), "total_noncurrent_assets": t(nc_assets),
            "total_current_liabilities": t(cur_liab), "total_noncurrent_liabilities": t(nc_liab),
            "total_assets": ta, "total_liabilities": tl, "total_equity": te,
            "total_liabilities_equity": round(tl + te, 2), "balanced": abs(ta - tl - te) < 0.005}


CF_OPERATING = [(("receivable", "allowance_doubtful"), "Accounts receivable"), ("inventory", "Inventory"),
                ("prepaid", "Prepaid expenses & advances"), ("other_current_asset", "Other current assets"),
                ("payable", "Accounts payable"), ("accrued", "Accrued liabilities & payroll"),
                ("deferred_revenue", "Deferred revenue (client advances)"), ("tax_payable", "Taxes payable"),
                ("other_current_liability", "Other current liabilities")]
CF_INVESTING = [("fixed_asset", "Capital expenditures (fixed assets)"), ("intangible", "Additions to intangibles"),
                ("investment", "Investments bought / (sold)"), ("other_noncurrent_asset", "Other long-term assets")]
CF_FINANCING = [("short_term_debt", "Short-term debt issued / (repaid)"),
                ("long_term_debt", "Long-term debt issued / (repaid)"),
                ("other_noncurrent_liability", "Other long-term liabilities"),
                ("capital", "Capital contributed"), ("partner_current", "Partners' current accounts"),
                (("other_equity", "reserves"), "Other equity movements"),
                (("drawings", "dividends_payable"), "Dividends / partner drawings paid")]


@memo
def cash_flow(db, date_from=None, date_to=None, bu_id=None):
    """Indirect-method statement: net income, add back non-cash, working-capital
    changes, investing and financing — reconciled to the change in cash."""
    date_to = date_to or today()
    start = {r["code"]: r for r in account_totals(db, date_to=_day_before(date_from), bu_id=bu_id)} \
        if date_from else {}
    period = account_totals(db, date_from=date_from, date_to=date_to, bu_id=bu_id)
    by_sub = {}
    for r in period:
        by_sub[r["subtype"]] = by_sub.get(r["subtype"], 0) + r["net"]
    pnl = income_statement(db, date_from, date_to, bu_id)
    # change in an asset (debit up) uses cash: -net; change in a liability/equity (credit up) adds cash: -net too.
    effect = lambda s: to_major(-sum(by_sub.get(x, 0) for x in ((s,) if isinstance(s, str) else s)))  # noqa: E731
    ops = [{"label": "(+) Depreciation", "amount": effect("accumulated_depreciation")},
           {"label": "(+) Amortization", "amount": effect("accumulated_amortization")}]
    ops += [{"label": f"(+/-) {lbl}", "amount": effect(s)} for s, lbl in CF_OPERATING]
    inv = [{"label": f"(-) {lbl}", "amount": effect(s)} for s, lbl in CF_INVESTING]
    fin = [{"label": ("(-) " if "drawings" in s else "(+/-) ") + lbl, "amount": effect(s)} for s, lbl in CF_FINANCING]
    # retained earnings moved directly (e.g. opening balances or prior-year adjustments)
    fin.append({"label": "(+/-) Retained earnings adjustments", "amount": effect("retained_earnings")})
    net_ops = round(pnl["net_profit"] + sum(x["amount"] for x in ops), 2)
    net_inv = round(sum(x["amount"] for x in inv), 2)
    net_fin = round(sum(x["amount"] for x in fin), 2)
    begin = to_major(sum(r["net"] for r in start.values() if r["subtype"] == "cash"))
    change = to_major(by_sub.get("cash", 0))
    end = round(begin + change, 2)
    computed = round(net_ops + net_inv + net_fin, 2)
    return {"date_from": date_from, "date_to": date_to, "net_income": pnl["net_profit"], "operating": ops,
            "net_operating": net_ops, "investing": inv, "net_investing": net_inv, "financing": fin,
            "net_financing": net_fin, "beginning_cash": begin, "change_in_cash": computed, "ending_cash": end,
            "reconciles": abs(computed - change) < 0.005}


def general_ledger(db, account=None, date_from=None, date_to=None, bu_id=None, party=None, limit=500, offset=0):
    w, p = _where(date_from=date_from, date_to=date_to, bu_id=bu_id)
    acc = None
    if account and len(str(account)) == 1:  # account-class filter, e.g. "5" = all 5000s
        w += " AND l.account LIKE ?"
        p.append(f"{account}%")
        account = None
    if account:
        acc = db.one("SELECT * FROM accounts WHERE code=?", (account,))
        if not acc:
            raise ValidationError("Unknown account")
        w += " AND l.account=?"
        p.append(account)
    if party:
        w += f" AND {PARTY_IS}"
        p.append(party)
    opening = 0
    if acc and date_from:
        w0, p0 = _where(date_to=_day_before(date_from), bu_id=bu_id)
        r = db.one(f"SELECT COALESCE(SUM(l.debit-l.credit),0) AS n FROM journal_lines l JOIN journal_entries e "
                   f"ON e.id=l.entry_id WHERE l.account=? AND {w0}", [account] + p0)
        opening = r["n"]
    sign = 1 if (not acc or acc["normal"] == "Debit") else -1
    rows = db.q(f"""SELECT e.id, e.date, e.kind, e.memo, {PARTY} AS party, b.code AS bu_code, l.account, a.name AS account_name,
                    l.debit, l.credit FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                    JOIN accounts a ON a.code=l.account LEFT JOIN business_units b ON b.id=e.bu_id
                    WHERE {w} ORDER BY e.date, e.id, l.id LIMIT ? OFFSET ?""", p + [int(limit), int(offset)])
    run = opening
    for r in rows:
        run += r["debit"] - r["credit"]
        r["debit"], r["credit"] = to_major(r["debit"]), to_major(r["credit"])
        r["balance"] = to_major(sign * run) if acc else None
    return {"account": acc, "opening": to_major(sign * opening), "rows": rows,
            "closing": to_major(sign * run) if acc else None}


def party_balance(db, account, party):
    r = db.one(f"SELECT COALESCE(SUM(l.credit-l.debit),0) AS n FROM journal_lines l JOIN journal_entries e "
               f"ON e.id=l.entry_id WHERE {LIVE} AND l.account=? AND {PARTY_IS}", (account, party))
    return to_major(r["n"])


_OPEN_ITEM_SUBTYPES = ("receivable", "payable", "accrued", "prepaid", "deferred_revenue", "short_term_debt",
                       "long_term_debt", "other_current_liability", "other_noncurrent_liability", "other_current_asset",
                       "other_noncurrent_asset")


def contact_summary(db, name, limit=30):
    """What the books say about one contact: open balances per account, totals and the latest vouchers."""
    rows = db.q(f"""SELECT l.account, a.name, a.name_ar, a.type, a.subtype, a.role, SUM(l.debit) dr, SUM(l.credit) cr
                    FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                    WHERE {LIVE} AND {PARTY_IS} GROUP BY l.account ORDER BY l.account""", (name,))
    balances, spent, earned = [], 0, 0
    for r in rows:
        if r["type"] in ("COGS", "Expense") or r["subtype"] in ("fixed_asset", "inventory"):
            spent += r["dr"] - r["cr"]
        elif r["type"] == "Revenue":
            earned += r["cr"] - r["dr"]
        elif r["subtype"] in _OPEN_ITEM_SUBTYPES and r["role"] not in ("vat_input", "wht_receivable") and r["dr"] != r["cr"]:
            bal = r["dr"] - r["cr"]
            liab = r["type"] == "Liability"
            balances.append({"account": r["account"], "name": r["name"], "name_ar": r["name_ar"], "type": r["type"],
                             "we_owe": to_major(-bal) if liab else 0, "owes_us": 0 if liab else to_major(bal)})
    recent = db.q(f"""SELECT DISTINCT e.id, e.date, e.kind, e.memo, e.ref FROM journal_entries e
                      JOIN journal_lines l ON l.entry_id=e.id WHERE {LIVE} AND {PARTY_IS}
                      ORDER BY e.date DESC, e.id DESC LIMIT ?""", (name, int(limit)))
    for r in recent:
        t = db.one("SELECT SUM(debit) n FROM journal_lines WHERE entry_id=?", (r["id"],))
        r["amount"] = to_major(t["n"] or 0)
    n = db.one(f"""SELECT COUNT(DISTINCT e.id) n, MIN(e.date) first, MAX(e.date) last FROM journal_entries e
                   JOIN journal_lines l ON l.entry_id=e.id WHERE {LIVE} AND {PARTY_IS}""", (name,))
    return {"balances": balances, "spent": to_major(spent), "earned": to_major(earned), "vouchers": n["n"],
            "first": n["first"], "last": n["last"], "recent": recent}


def party_balances(db, subtypes=("payable",), accounts=None, date_to=None):
    """Open balances per party on liability accounts (suppliers, workers)."""
    if accounts:
        codes = list(accounts)
    else:
        codes = [r["code"] for r in db.q("SELECT code FROM accounts WHERE subtype IN (%s)" %
                                          ",".join("?" * len(subtypes)), subtypes)]
    if not codes:
        return []
    w, p = _where(date_to=date_to)
    rows = db.q(f"""SELECT {PARTY} AS party, l.account, SUM(l.credit) AS owed, SUM(l.debit) AS paid,
                    SUM(l.credit-l.debit) AS balance, MAX(e.date) AS last_date
                    FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                    WHERE {w} AND l.account IN ({",".join("?" * len(codes))})
                    GROUP BY 1, l.account HAVING balance<>0 ORDER BY balance DESC""", p + codes)
    for r in rows:
        for k in ("owed", "paid", "balance"):
            r[k] = to_major(r[k])
    return rows


def shared_overhead_total(db, date_from=None, date_to=None):
    """Operating expenses booked without a business unit (the indirect-cost pool)."""
    w, p = _where(date_from=date_from, date_to=date_to, bu_id="none")
    r = db.one(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) AS n FROM journal_lines l
                   JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                   WHERE {w} AND a.subtype IN ('employee','general_admin','marketing','utilities','other_operating')
                     AND COALESCE(json_extract(e.meta,'$.no_alloc'),0)<>1""",   # company costs kept out of the projects
               p)
    return to_major(r["n"])


def allocated_overhead(db, bu_id, date_from=None, date_to=None):
    bu = db.one("SELECT overhead_pct FROM business_units WHERE id=?", (bu_id,))
    return round(shared_overhead_total(db, date_from, date_to) * (bu["overhead_pct"] or 0 if bu else 0), 2)


@memo
def bu_summaries(db, date_from=None, date_to=None, status=None, q=None):
    w, p = _where(date_from=date_from, date_to=date_to)
    agg = {r["bid"]: r for r in db.q(f"""
        SELECT e.bu_id AS bid,
          SUM(CASE WHEN a.type='Revenue' THEN l.credit-l.debit ELSE 0 END) AS revenue,
          SUM(CASE WHEN a.type='COGS' THEN l.debit-l.credit ELSE 0 END) AS cogs,
          SUM(CASE WHEN a.type='Expense' THEN l.debit-l.credit ELSE 0 END) AS direct_overhead,
          SUM(CASE WHEN a.subtype='receivable' THEN l.debit-l.credit ELSE 0 END) AS receivable,
          SUM(CASE WHEN a.subtype='deferred_revenue' THEN l.credit-l.debit ELSE 0 END) AS advances,
          SUM(CASE WHEN e.kind='Receipt Voucher' AND a.subtype='cash' THEN l.debit ELSE 0 END) AS collected
        FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
        WHERE {w} AND e.bu_id IS NOT NULL GROUP BY e.bu_id""", p)}
    # money the client actually paid into the project: money in on entries settled with the client (receipts against
    # invoices, advances, sales paid on the spot, cheques when they clear), less refunds to them — whether it landed in
    # cash / a bank, the funding partner's account (when it replaces the bank) or with a partner personally; not
    # partner money, loans, refundable deposits or supplier payments
    paid = {r["bid"]: r for r in db.q(f"""
        SELECT x.bid, SUM(x.cash) AS n, SUM(x.bank) AS bank, SUM(x.funder) AS funder, SUM(x.partner) AS partner FROM (
          SELECT e.bu_id AS bid, e.id,
            SUM(CASE WHEN a.subtype IN ('cash','partner_current') OR a.role='funding_partner' THEN l.debit-l.credit ELSE 0 END) AS cash,
            SUM(CASE WHEN a.subtype='cash' THEN l.debit-l.credit ELSE 0 END) AS bank,
            SUM(CASE WHEN a.role='funding_partner' THEN l.debit-l.credit ELSE 0 END) AS funder,
            SUM(CASE WHEN a.subtype='partner_current' AND COALESCE(a.role,'')<>'funding_partner' THEN l.debit-l.credit ELSE 0 END) AS partner,
            MAX(CASE WHEN a.subtype IN ('receivable','deferred_revenue','operating_revenue','sales_discount') AND a.code<>? THEN 1 ELSE 0 END) AS client
          FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
          WHERE {w} AND e.bu_id IS NOT NULL GROUP BY e.bu_id, e.id) x
        WHERE x.client=1 GROUP BY x.bid""", [db.acct("negotiation_income")] + p)}   # a supplier discount is not the client's money
    # the VAT share of what clients pay, per project (output VAT over sales + VAT billed): taken out of "paid", since the
    # tax is collected for the state and is not the company's fee
    vat_share = {r["bid"]: (r["vat"] / (r["rev"] + r["vat"]) if (r["rev"] or 0) + (r["vat"] or 0) > 0 and (r["vat"] or 0) > 0 else 0) for r in db.q(f"""
        SELECT e.bu_id AS bid,
          SUM(CASE WHEN l.account=? THEN l.credit-l.debit ELSE 0 END) AS vat,
          SUM(CASE WHEN a.subtype IN ('operating_revenue','sales_discount') AND a.code<>? THEN l.credit-l.debit ELSE 0 END) AS rev
        FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
        WHERE {w} AND e.bu_id IS NOT NULL GROUP BY e.bu_id""", [db.acct("vat_output"), db.acct("negotiation_income")] + p)}
    pool = shared_overhead_total(db, date_from, date_to)
    savings, kept = {}, {}
    for r in db.q(f"SELECT e.bu_id, e.meta FROM journal_entries e WHERE {w} AND kind IN ('Direct Cost','Supplier Discount')", p):
        m = json.loads(r["meta"] or "{}")
        savings[r["bu_id"]] = savings.get(r["bu_id"], 0) + to_minor(m.get("savings", 0))
        if m.get("negotiation") == "keep":
            kept[r["bu_id"]] = kept.get(r["bu_id"], 0) + to_minor(m.get("savings", 0))
    bw, bp = [], []
    if status:
        bw.append("status=?")
        bp.append(status)
    if q:
        bw.append("(code LIKE ? OR name LIKE ? OR location LIKE ?)")
        bp += [f"%{q}%"] * 3
    out = []
    for b in db.q("SELECT * FROM business_units" + (" WHERE " + " AND ".join(bw) if bw else "") + " ORDER BY code", bp):
        a = agg.get(b["id"], {})
        rev, cogs, ovh = (a.get(k) or 0 for k in ("revenue", "cogs", "direct_overhead"))
        collected = a.get("collected") or 0
        alloc = to_minor(pool * (b["overhead_pct"] or 0))
        if b["basis"] == "Cost-Plus %":
            expected = int(round(cogs * (1 + (b["fee_pct"] or 0))))
        elif b["basis"] == "Fixed Price":
            expected = b["contract_value"] or 0
        else:
            expected = collected
        pr = paid.get(b["id"]) or {}
        paid_gross, actual = pr.get("n") or 0, cogs + ovh   # the project's own costs, not the shared overhead
        paid_vat = int(round(paid_gross * min(vat_share.get(b["id"], 0), 0.5))) if paid_gross > 0 else 0
        client_paid = paid_gross - paid_vat
        if b["basis"] == LIVE_FEE:
            expected = client_paid       # billed as paid: nothing waits to be collected
        net = rev - cogs - ovh - alloc
        out.append({**b, "contract_value": to_major(b["contract_value"]), "revenue": to_major(rev),
                    "cogs": to_major(cogs), "direct_overhead": to_major(ovh), "allocated_overhead": to_major(alloc),
                    "gross_profit": to_major(rev - cogs), "net_profit": to_major(net),
                    "margin": round(net / rev, 4) if rev else 0, "receivable": to_major(a.get("receivable") or 0),
                    "advances": to_major(a.get("advances") or 0), "collected": to_major(collected),
                    "expected_billing": to_major(expected), "to_collect": 0.0 if b["basis"] == LIVE_FEE else to_major(max(expected - collected, 0)),
                    "savings": to_major(savings.get(b["id"], 0)), "savings_kept": to_major(kept.get(b["id"], 0)),
                    "client_paid": to_major(client_paid), "client_paid_gross": to_major(paid_gross), "client_paid_vat": to_major(paid_vat),
                    "actual_costs": to_major(actual),
                    "paid_into": {k: to_major(pr.get(k) or 0) for k in ("bank", "funder", "partner")},   # where it landed
                    "live_gap": to_major(client_paid - actual),
                    "live_fee": round((client_paid - actual) / actual, 4) if actual > 0 else None,
                    "live_margin": round((client_paid - actual) / client_paid, 4) if client_paid > 0 else None})
    return out


def negotiations(db, date_from=None, date_to=None, bu_id=None):
    """Every negotiated discount, per business unit: kept as company earnings or passed on as a lower cost."""
    w, p = _where(date_from=date_from, date_to=date_to, bu_id=bu_id)
    keep_acc = db.acct("negotiation_income")
    rows = []
    for e in db.q(f"""SELECT e.id, e.date, e.kind, e.memo, e.party, e.ref, e.meta, e.bu_id, b.code AS bu_code,
                             b.name AS bu_name FROM journal_entries e LEFT JOIN business_units b ON b.id=e.bu_id
                      WHERE {w} AND (e.kind IN ('Direct Cost','Supplier Discount') OR EXISTS
                            (SELECT 1 FROM journal_lines x WHERE x.entry_id=e.id AND x.account=?))
                      ORDER BY e.date, e.id""", p + [keep_acc]):
        meta = json.loads(e["meta"] or "{}")
        savings = to_minor(meta.get("savings", 0))
        if e["kind"] in ("Direct Cost", "Supplier Discount"):
            mode = meta.get("negotiation") or "pass"
        else:   # any other entry booked straight to Negotiation Earnings
            n = db.one("SELECT COALESCE(SUM(credit-debit),0) AS n FROM journal_lines WHERE entry_id=? AND account=?",
                       (e["id"], keep_acc))["n"]
            savings, mode = n, "keep"
        if not savings:
            continue
        quoted = to_minor(meta.get("quoted", 0))
        rows.append({"id": e["id"], "date": e["date"], "kind": e["kind"], "memo": e["memo"], "party": e["party"],
                     "deal_ref": meta.get("deal_ref") or e["ref"] or "", "bu_id": e["bu_id"], "bu_code": e["bu_code"] or "",
                     "bu_name": e["bu_name"] or "", "quoted": to_major(quoted), "savings": to_major(savings),
                     "mode": mode, "kept": to_major(savings if mode == "keep" else 0),
                     "passed": to_major(savings if mode == "pass" else 0)})
    by_bu = {}
    for r in rows:
        g = by_bu.setdefault(r["bu_id"], {"bu_id": r["bu_id"], "bu_code": r["bu_code"], "bu_name": r["bu_name"],
                                          "quoted": 0, "savings": 0, "kept": 0, "passed": 0, "count": 0})
        g["count"] += 1
        for k in ("quoted", "savings", "kept", "passed"):
            g[k] = round(g[k] + r[k], 2)
    modes = {b["id"]: b["negotiation"] or "" for b in db.q("SELECT id, negotiation FROM business_units")}
    for g in by_bu.values():
        g["setting"] = modes.get(g["bu_id"], "")
    total = {k: round(sum(r[k] for r in rows), 2) for k in ("quoted", "savings", "kept", "passed")}
    return {"rows": rows, "by_bu": sorted(by_bu.values(), key=lambda g: g["bu_code"]), "total": total,
            "account": keep_acc}


def replace_doc(db, eid, fn, d, user="", **kw):
    """Re-enter a document through its own form: the entry keeps its number and history, its lines are rebuilt."""
    with db.tx():
        e = get_entry(db, eid)
        if not e:
            raise ValidationError("Entry not found")
        if e["status"] != "posted" or e["reversed_by"] or e["reversal_of"]:
            raise ValidationError("Only live entries can be edited")
        check_open(db, e["date"])
        if db.one("SELECT 1 FROM stock_moves WHERE entry_id=?", (eid,)) or \
                db.one("SELECT 1 FROM extracts WHERE entry_id=?", (eid,)):
            raise ValidationError("This entry belongs to a stock move or invoice — reverse it and re-enter instead")
        _REPLACE["eid"] = eid
        try:
            out = fn(db, d, user, **kw)
        finally:
            _REPLACE["eid"] = None
        if out != eid:
            raise ValidationError("This document could not be edited in place")
        return eid


def doc_form(db, e):
    """The values of the form an entry was typed into (for editing it the same way)."""
    meta = e["meta"] or {}
    if meta.get("form"):
        form = {**meta["form"], "date": e["date"]}
        if e["kind"] == "Direct Cost":   # how the discount was actually booked, even if the form did not say
            form["negotiation"] = meta.get("negotiation") or form.get("negotiation") or "pass"
        return form
    lines = e["lines"]
    types = {a["code"]: a for a in db.q("SELECT code, type, subtype FROM accounts")}
    cash = [ln for ln in lines if ln["credit"] and types.get(ln["account"], {}).get("subtype") == "cash"]
    paid = round(sum(ln["credit"] for ln in cash), 2)
    base = {"date": e["date"], "bu_id": e["bu_id"] or "", "method": meta.get("method", ""),
            "paid_from": cash[0]["account"] if cash else "", "vat_code": meta.get("vat_code", ""),
            "wht_code": meta.get("wht_code", "")}
    if e["kind"] == "Direct Cost":
        cost = next((ln for ln in lines if ln["debit"] and types.get(ln["account"], {}).get("type") == "COGS"), None)
        return {**base, "account": cost["account"] if cost else "", "description": e["memo"], "vendor": e["party"],
                "deal_ref": meta.get("deal_ref", ""), "deal_status": meta.get("deal_status", ""),
                "qty": meta.get("qty", 1), "unit": meta.get("unit", ""), "rate": meta.get("rate", ""),
                "savings": meta.get("savings", 0) or "", "paid": paid,
                "negotiation": meta.get("negotiation") or ("pass" if meta.get("savings") else "")}
    if e["kind"] == "Operating Expense":
        ex = next((ln for ln in lines if ln["debit"] and types.get(ln["account"], {}).get("type") == "Expense"), None)
        return {**base, "account": ex["account"] if ex else "", "description": e["memo"], "vendor": e["party"],
                "amount": ex["debit"] if ex else "", "paid": paid}
    if e["kind"] == "Supplier Discount":
        dr = next((ln for ln in lines if ln["debit"]), None)
        cr = next((ln for ln in lines if ln["credit"]), None)
        return {"date": e["date"], "party": e["party"], "against": dr["account"] if dr else "",
                "amount": dr["debit"] if dr else "", "credit_account": cr["account"] if cr else "",
                "bu_id": e["bu_id"] or "", "deal_ref": meta.get("deal_ref", ""), "memo": e["memo"]}
    return None


def deals(db):
    m = {}
    for r in db.q(f"SELECT e.party, e.meta FROM journal_entries e WHERE {LIVE} AND kind IN ('Direct Cost','Supplier Discount')"):
        meta = json.loads(r["meta"] or "{}")
        ref = meta.get("deal_ref")
        if not ref:
            continue
        g = m.setdefault(ref, {"ref": ref, "vendor": r["party"], "quoted": 0, "savings": 0, "entries": 0, "status": ""})
        g["entries"] += 1
        g["vendor"] = g["vendor"] or r["party"]
        g["quoted"] += to_minor(meta.get("quoted", 0))
        g["savings"] += to_minor(meta.get("savings", 0))
        g["status"] = meta.get("deal_status") or g["status"]
    for g in m.values():
        g["cost"] = to_major(g["quoted"] - g["savings"])
        g["quoted"], g["savings"] = to_major(g["quoted"]), to_major(g["savings"])
    return sorted(m.values(), key=lambda g: g["ref"])


@memo
def partner_report(db, date_from=None, date_to=None):
    partners = db.q("SELECT * FROM partners ORDER BY id")
    shares = {(r["partner_id"], r["bu_id"]): r["share"] for r in db.q("SELECT * FROM partner_shares")}
    bus = bu_summaries(db, date_from, date_to)
    company = income_statement(db, date_from, date_to, bu_id="none")["net_profit"]
    alloc_total = round(sum(b["allocated_overhead"] for b in bus), 2)
    company_after_alloc = round(company + alloc_total, 2)  # overhead already charged to units is not double-counted
    reserve = float(db.setting("reserve_pct", "0.4") or 0)
    draws = {r["party"]: to_major(r["n"]) for r in db.q(
        f"SELECT {PARTY} AS party, SUM(l.debit-l.credit) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
        f"JOIN accounts a ON a.code=l.account WHERE {LIVE} AND a.subtype='drawings' AND e.kind<>'Profit Appropriation'"
        + (" AND e.date>=?" if date_from else "") + (" AND e.date<=?" if date_to else "") + " GROUP BY 1",
        [x for x in (date_from, date_to) if x])}
    from . import equity
    ratio, typed = equity.profit_ratios(db, partners)
    ratio = {int(k): v for k, v in ratio.items()}
    # agreed interest on capital comes out of the company's result before the rest is shared
    cap_int = equity.capital_interest(db, date_from, date_to)
    pool = round(company_after_alloc - sum(cap_int.values()), 2)
    # profit is only distributable once BILLED: a project's work done but not invoiced (unbilled revenue) carries
    # its share of the profit, which is earned but held in the company until the invoice goes out
    from .partner_pack import billed_profit, wip_accounts
    wip_codes = wip_accounts(db)
    w, p = _where(date_to=date_to)
    wip_bu = {r["bu_id"]: to_major(r["n"]) for r in db.q(
        f"""SELECT e.bu_id, SUM(l.debit-l.credit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
            WHERE {w} AND l.account IN ({','.join('?' * len(wip_codes)) or "''"}) GROUP BY e.bu_id""", p + list(wip_codes))}
    billed = {b["id"]: billed_profit(b["revenue"], wip_bu.get(b["id"], 0), b["net_profit"]) for b in bus}
    rows = []
    for pt in partners:
        per, total, total_billed = {}, 0.0, 0.0
        for b in bus:
            s = shares.get((pt["id"], b["id"]), ratio[pt["id"]])
            per[b["code"]] = {"share": s, "profit": round(b["net_profit"] * s, 2), "billed": round(billed[b["id"]] * s, 2)}
            total += b["net_profit"] * s
            total_billed += billed[b["id"]] * s
        rest = pool * ratio[pt["id"]] + cap_int.get(pt["name"], 0)
        total += rest
        total_billed += rest
        distributable = total_billed * (1 - reserve) if total_billed > 0 else total_billed
        drawn = draws.get(pt["name"], 0)
        rows.append({"id": pt["id"], "name": pt["name"], "default_share": pt["default_share"], "units": per,
                     "active": pt["active"], "joined": pt["joined"], "left_on": pt["left_on"],
                     "capital_interest_rate": pt["capital_interest"], "loan_rate": pt["loan_rate"], "notes": pt["notes"],
                     "profit_ratio": ratio[pt["id"]], "profit_share_typed": pt["profit_share"], "salary": to_major(pt["salary"] or 0),
                     "drawings_interest": pt["drawings_interest"],
                     "capital_interest": cap_int.get(pt["name"], 0), "accounts": equity.balances(db, pt["name"], date_to),
                     "profit_share": round(total, 2), "billed_share": round(total_billed, 2),
                     "held_in_wip": round(total - total_billed, 2), "distributable": round(distributable, 2), "drawn": drawn,
                     "balance": round(distributable - drawn, 2)})
    checks = {b["code"]: round(sum(shares.get((pt["id"], b["id"]), ratio[pt["id"]]) for pt in partners), 4)
              for b in bus}
    return {"partners": rows, "units": [{"id": b["id"], "code": b["code"], "net_profit": b["net_profit"], "billed_profit": billed[b["id"]],
                                         "wip": wip_bu.get(b["id"], 0)} for b in bus],
            "company_result": company_after_alloc, "reserve_pct": reserve, "share_checks": checks,
            "default_check": round(sum(pt["default_share"] or 0 for pt in partners), 4), "ratio_typed": typed,
            "ratio_check": round(sum(ratio.values()), 4), "appropriations": equity.appropriations(db),
            "legal_reserve_pct": float(db.setting("legal_reserve_pct", "0") or 0),
            "legal_reserve_cap": float(db.setting("legal_reserve_cap", "0.5") or 0)}


def cash_position(db):
    rows = account_totals(db)
    cash = sum(r["net"] for r in rows if r["subtype"] == "cash")
    span = db.one(f"SELECT MIN(date) AS a FROM journal_entries e WHERE {LIVE}")
    n = 1
    if span["a"]:
        a, b = _dt.date.fromisoformat(span["a"]), _dt.date.today()
        n = max(1, (b.year - a.year) * 12 + b.month - a.month + 1)
    opex = sum(r["net"] for r in rows if r["subtype"] in ("employee", "general_admin", "marketing", "utilities",
                                                           "other_operating"))
    owed = -sum(r["net"] for r in rows if r["subtype"] in ("payable", "accrued"))
    floor = opex / n * 3 + owed
    return {"cash": to_major(cash), "monthly_overhead": to_major(opex / n), "months": n, "owed": to_major(owed),
            "floor": to_major(floor), "gap": to_major(max(floor - cash, 0))}


def dashboard(db, date_from=None, date_to=None, bu_id=None):
    rows = {r["subtype"]: 0 for r in []}
    for r in account_totals(db, date_to=date_to, bu_id=bu_id):
        rows[r["subtype"]] = rows.get(r["subtype"], 0) + r["net"]
    pnl = income_statement(db, date_from, date_to, bu_id)
    start = (_dt.date.today().replace(day=1) - _dt.timedelta(days=330)).replace(day=1).isoformat()
    w, p = _where(date_from=start, bu_id=bu_id)
    series = db.q(f"""SELECT substr(e.date,1,7) AS month,
            SUM(CASE WHEN a.type='Revenue' THEN l.credit-l.debit ELSE 0 END) AS revenue,
            SUM(CASE WHEN a.type IN ('COGS','Expense') THEN l.debit-l.credit ELSE 0 END) AS costs
            FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
            WHERE {w} GROUP BY month ORDER BY month""", p)
    for s in series:
        s["revenue"], s["costs"] = to_major(s["revenue"]), to_major(s["costs"])
    return {"cash": to_major(rows.get("cash", 0)), "receivable": to_major(rows.get("receivable", 0)),
            "payable": to_major(-rows.get("payable", 0)), "accrued": to_major(-rows.get("accrued", 0)),
            "advances": to_major(-rows.get("deferred_revenue", 0)), "inventory": to_major(rows.get("inventory", 0)),
            "revenue": pnl["net_sales"], "ebitda": pnl["ebitda"], "net_profit": pnl["net_profit"],
            "margins": pnl["margins"], "series": series, "units": bu_summaries(db, date_from, date_to),
            "cash_position": cash_position(db), "audit": audit(db)}


@memo
def audit(db):
    tb = trial_balance(db)
    bs = balance_sheet(db)
    stock_val = round(sum(r["value"] for r in stock_report(db)), 2)
    inv_accounts = {w["account"] or db.acct("inventory") for w in db.q("SELECT account FROM warehouses")}
    inv = to_major(sum(r["net"] for r in account_totals(db) if r["code"] in inv_accounts))
    bad = db.q("SELECT e.id FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id "
               "GROUP BY e.id HAVING SUM(l.debit)<>SUM(l.credit)")
    shares = db.one("SELECT COALESCE(SUM(overhead_pct),0) AS s FROM business_units")["s"]
    cf = cash_flow(db)
    return [
        {"check": "Trial balance: debits = credits", "a": tb["totals"]["debit"], "b": tb["totals"]["credit"],
         "ok": tb["balanced"]},
        {"check": "Balance sheet: assets = liabilities + equity", "a": bs["total_assets"],
         "b": bs["total_liabilities_equity"], "ok": bs["balanced"]},
        {"check": "Cash flow reconciles to change in cash", "a": cf["change_in_cash"],
         "b": round(cf["ending_cash"] - cf["beginning_cash"], 2), "ok": cf["reconciles"]},
        {"check": "Stock register = inventory accounts", "a": stock_val, "b": inv, "ok": abs(stock_val - inv) < 0.05},
        {"check": "Every journal entry balances", "a": len(bad), "b": 0, "ok": not bad},
        {"check": "Shared-overhead % across business units ≤ 100%", "a": round(shares * 100, 2), "b": 100,
         "ok": shares <= 1.0001},
    ]


# ================================================================== tax module
PARTY_FIELDS = ("name", "type", "receiver_type", "tax_id", "national_id", "country", "governate", "region", "street",
                "building", "postal_code", "email", "phone", "default_vat", "default_wht", "notes",
                "category", "contact_person", "phone2", "whatsapp", "specialty", "vehicle", "bank_details")
# suggested directory categories; any other word typed by the user is kept as-is
CONTACT_CATEGORIES = ("supplier", "subcontractor", "driver", "transport", "technician", "consultant", "customer",
                      "employee", "other")
# accounting type implied by a category when the user did not pick one
_CATEGORY_TYPE = {"customer": "customer", "employee": "employee", "driver": "supplier", "supplier": "supplier",
                  "subcontractor": "supplier", "transport": "supplier", "technician": "supplier",
                  "consultant": "supplier"}


def save_party(db, d, party_id=None):
    v = {k: text(d.get(k), maxlen=2000 if k in ("notes", "bank_details") else 300) for k in PARTY_FIELDS}
    if not v["name"]:
        raise ValidationError("name is required")
    v["category"] = v["category"].strip().lower()[:40]
    if v["type"] not in ("customer", "supplier", "both", "employee", "other"):
        v["type"] = _CATEGORY_TYPE.get(v["category"], "other" if v["category"] else "customer")
    v["receiver_type"] = v["receiver_type"] if v["receiver_type"] in ("B", "P", "F") else "B"
    v["country"] = (v["country"] or "EG").upper()[:2]
    if v["tax_id"] and v["receiver_type"] == "B" and v["country"] == "EG" and not re.fullmatch(r"\d{9}", v["tax_id"]):
        raise ValidationError("An Egyptian tax registration number has 9 digits")
    if v["national_id"] and not re.fullmatch(r"\d{14}", v["national_id"]):
        raise ValidationError("A national ID has 14 digits")
    for k in ("default_vat", "default_wht"):
        if v[k]:
            tax_code(db, v[k], "vat" if k == "default_vat" else "wht")
    with db.tx():
        dup = db.one("SELECT id FROM parties WHERE name=?", (v["name"],))
        if party_id:
            if dup and dup["id"] != int(party_id):
                raise ValidationError("Another party already has this name")
            old = db.one("SELECT name FROM parties WHERE id=?", (int(party_id),))
            if not old:
                raise ValidationError("Party not found")
            db.conn.execute(f"UPDATE parties SET {','.join(k + '=?' for k in PARTY_FIELDS)} WHERE id=?",
                            tuple(v[k] for k in PARTY_FIELDS) + (int(party_id),))
            if old["name"] != v["name"]:  # keep vouchers linked to the renamed party
                db.conn.execute("UPDATE journal_entries SET party=? WHERE party=?", (v["name"], old["name"]))
                db.conn.execute("UPDATE extracts SET customer=? WHERE customer=?", (v["name"], old["name"]))
            return int(party_id)
        if dup:
            raise ValidationError("A party with this name already exists")
        return db.conn.execute(f"INSERT INTO parties({','.join(PARTY_FIELDS)}) VALUES ({','.join('?' * len(PARTY_FIELDS))})",
                               tuple(v[k] for k in PARTY_FIELDS)).lastrowid


def save_tax_code(db, d, old_code=None):
    code = text(d.get("code"), "code", required=True, maxlen=20).upper()
    kind = d.get("kind")
    if kind not in ("vat", "wht"):
        raise ValidationError("kind must be vat or wht")
    rate = num(d.get("rate"), "rate")
    rate = rate / 100 if rate > 1 else rate
    vals = (code, text(d.get("name"), "name", required=True), text(d.get("name_ar")), kind, rate,
            text(d.get("eta_type")) or ("T1" if kind == "vat" else "T4"), text(d.get("eta_subtype")),
            0 if d.get("active") in (0, "0", False, "No", "no") else 1)
    with db.tx():
        if old_code:
            db.conn.execute("UPDATE tax_codes SET code=?,name=?,name_ar=?,kind=?,rate=?,eta_type=?,eta_subtype=?,active=? "
                            "WHERE code=?", vals + (old_code,))
        else:
            if db.one("SELECT 1 FROM tax_codes WHERE code=?", (code,)):
                raise ValidationError("Code already exists")
            db.conn.execute("INSERT INTO tax_codes(code,name,name_ar,kind,rate,eta_type,eta_subtype,active) "
                            "VALUES (?,?,?,?,?,?,?,?)", vals)
    return code


def tax_summary(db, date_from=None, date_to=None):
    """VAT return figures and withholding-tax schedules for a period."""
    w, p = _where(date_from=date_from, date_to=date_to)
    roles = {r: db.acct(r) for r in ("vat_output", "vat_input", "wht_payable", "wht_receivable")}

    def mv(code):
        r = db.one(f"SELECT COALESCE(SUM(l.debit),0) dr, COALESCE(SUM(l.credit),0) cr FROM journal_lines l "
                   f"JOIN journal_entries e ON e.id=l.entry_id WHERE {w} AND l.account=?", p + [code])
        return r["dr"], r["cr"]

    o_dr, o_cr = mv(roles["vat_output"])
    i_dr, i_cr = mv(roles["vat_input"])
    output_vat, input_vat = o_cr - o_dr, i_dr - i_cr
    tax_ids = {r["name"]: r for r in db.q("SELECT name, tax_id, national_id FROM parties")}

    def by_party(code, sign):  # sign 1: credit-positive
        rows = db.q(f"""SELECT {PARTY} AS party, SUM(l.credit-l.debit) AS n, COUNT(DISTINCT e.id) AS docs,
                        SUM(CASE WHEN e.kind IN ('Journal Voucher','Payment Voucher') AND l.debit>0 THEN l.debit ELSE 0 END) AS settled
                        FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                        WHERE {w} AND l.account=? GROUP BY 1 ORDER BY 1""", p + [code])
        return [{"party": r["party"], "tax_id": (tax_ids.get(r["party"]) or {}).get("tax_id", ""),
                 "amount": to_major(sign * r["n"]), "documents": r["docs"]} for r in rows if r["n"]]

    # taxable base per VAT code from invoices / purchases in the period (from voucher metadata)
    base = {}
    for r in db.q(f"SELECT e.id, e.kind, e.meta FROM journal_entries e WHERE {w} AND e.meta LIKE '%vat_code%'", p):
        m = json.loads(r["meta"] or "{}")
        if not m.get("vat_code"):
            continue
        e = get_entry(db, r["id"])
        side = "sales" if r["kind"] in ("Sales Invoice", "Receipt Voucher") else "purchases"
        vat_line = sum(ln["credit"] - ln["debit"] if side == "sales" else ln["debit"] - ln["credit"]
                       for ln in e["lines"] if ln["account"] == roles["vat_output" if side == "sales" else "vat_input"])
        net = sum(ln["credit"] for ln in e["lines"] if side == "sales" and db.one(
            "SELECT type FROM accounts WHERE code=?", (ln["account"],))["type"] == "Revenue") if side == "sales" else \
            sum(ln["debit"] for ln in e["lines"] if ln["debit"] and ln["account"] != roles["vat_input"])
        k = (side, m["vat_code"])
        b = base.setdefault(k, {"side": side, "code": m["vat_code"], "net": 0.0, "vat": 0.0, "documents": 0})
        b["net"] = round(b["net"] + net, 2)
        b["vat"] = round(b["vat"] + vat_line, 2)
        b["documents"] += 1
    return {"date_from": date_from, "date_to": date_to, "accounts": roles,
            "output_vat": to_major(output_vat), "input_vat": to_major(input_vat),
            "net_vat": to_major(output_vat - input_vat),
            "vat_by_code": sorted(base.values(), key=lambda b: (b["side"], b["code"])),
            "wht_withheld_from_suppliers": by_party(roles["wht_payable"], 1),
            "wht_deducted_by_clients": by_party(roles["wht_receivable"], -1),
            "wht_payable_balance": party_total(db, roles["wht_payable"], date_to, 1),
            "wht_receivable_balance": party_total(db, roles["wht_receivable"], date_to, -1)}


def party_total(db, code, date_to=None, sign=1):
    w, p = _where(date_to=date_to)
    r = db.one(f"SELECT COALESCE(SUM(l.credit-l.debit),0) n FROM journal_lines l JOIN journal_entries e "
               f"ON e.id=l.entry_id WHERE {w} AND l.account=?", p + [code])
    return to_major(sign * r["n"])
