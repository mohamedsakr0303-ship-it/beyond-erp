"""Cash custody (petty cash advances / العهد): money handed to an employee to spend for the company.

A custody card says who holds it, whether it is a one-off advance (temporary, settle by a due date) or a standing
float (permanent / imprest, topped back up after each settlement), and its limit. Every movement is a journal entry
whose reference starts with CU:<code> — issue / top-up (CU:<code>:ISSUE), expense report (:SET), money returned
(:RET) and deductions / write-offs (:DED) — so the balance comes from the books.

  issue        Dr custody (holder)          Cr cash / bank
  settlement   Dr expenses / costs (+VAT)   Cr custody        (one entry per business unit on the report)
  return       Dr cash / bank               Cr custody
  deduction    Dr accrued payroll / expense Cr custody        (kept from salary, or written off)
A negative balance means the holder spent more than they were given: the company owes them (reimburse = issue).
"""
import datetime as _dt
import json

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

LIVE_REF = f"{acc.LIVE} AND e.reversed_by IS NULL AND e.reversal_of IS NULL"
KINDS = {"ISSUE": "Custody Issued", "SET": "Custody Settlement", "RET": "Custody Return", "DED": "Custody Deduction"}
ALL_KINDS = list(KINDS.values())
TYPES = ("temporary", "permanent")


def _role_or(db, role, fallback=None):
    r = db.one("SELECT code FROM accounts WHERE role=?", (role,))
    return r["code"] if r else fallback


def next_code(db):
    n = 1
    for r in db.q("SELECT code FROM custodies WHERE code LIKE 'CU-%'"):
        tail = r["code"][3:]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"CU-{n:04d}"


def get(db, cid):
    c = db.one("SELECT * FROM custodies WHERE id=? OR code=?", (cid if str(cid).isdigit() else -1, text(cid)))
    if not c:
        raise ValidationError("Custody not found")
    return c


def _entries(db, code):
    return db.q(f"SELECT e.* FROM journal_entries e WHERE {LIVE_REF} AND (e.ref=? OR e.ref LIKE ?) ORDER BY e.date, e.id",
                (f"CU:{code}", f"CU:{code}:%"))


def _moves(db, c, tag=None):
    """Signed movement on the custody account (+ = more money with the holder)."""
    w, p = [LIVE_REF, "l.account=?"], [c["account"]]
    if tag:
        w.append("e.ref LIKE ?")
        p.append(f"CU:{c['code']}:{tag}%")
    else:
        w.append("(e.ref=? OR e.ref LIKE ?)")
        p += [f"CU:{c['code']}", f"CU:{c['code']}:%"]
    return db.one(f"SELECT COALESCE(SUM(l.debit-l.credit),0) AS n FROM journal_lines l JOIN journal_entries e "
                  f"ON e.id=l.entry_id WHERE {' AND '.join(w)}", p)["n"]


def status(db, c, today=None):
    today = today or acc.today()
    bal = c["opening_balance"] + _moves(db, c)
    issued, spent, returned, deducted = _moves(db, c, "ISSUE"), -_moves(db, c, "SET"), -_moves(db, c, "RET"), -_moves(db, c, "DED")
    ents = _entries(db, c["code"])
    last = ents[-1]["date"] if ents else (c["issued_on"] or "")
    last_settle = max((e["date"] for e in ents if e["ref"].startswith(f"CU:{c['code']}:SET")), default="")
    open_days = (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(c["issued_on"])).days if c["issued_on"] else 0
    overdue = c["status"] == "open" and c["kind"] == "temporary" and bool(c["due_date"]) and c["due_date"] < today and bal > 0
    return {"balance": to_major(bal), "issued": to_major(issued + c["opening_balance"]), "spent": to_major(spent),
            "returned": to_major(returned), "deducted": to_major(deducted), "last_activity": last,
            "last_settlement": last_settle, "days_open": max(open_days, 0), "overdue": overdue,
            "owed_to_holder": to_major(-bal) if bal < 0 else 0,
            "to_replenish": to_major(max(c["float_amount"] - bal, 0)) if c["kind"] == "permanent" and c["float_amount"] else 0,
            "over_limit": bool(c["float_amount"]) and bal > c["float_amount"]}


def _row(db, c, today=None):
    bu = db.one("SELECT code FROM business_units WHERE id=?", (c["bu_id"],)) if c["bu_id"] else None
    return {**c, "float_amount": to_major(c["float_amount"]), "opening_balance": to_major(c["opening_balance"]),
            "bu_code": bu["code"] if bu else "", **status(db, c, today)}


def register(db, q=None, state=None, holder=None, today=None):
    w, p = ["1=1"], []
    if q:
        w.append("(code LIKE ? OR holder LIKE ? OR purpose LIKE ?)")
        p += [f"%{q}%"] * 3
    if holder:
        w.append("holder=?")
        p.append(holder)
    rows = [_row(db, c, today) for c in db.q(f"SELECT * FROM custodies WHERE {' AND '.join(w)} ORDER BY status DESC, issued_on DESC, id DESC", p)]
    if state in ("open", "closed"):
        rows = [r for r in rows if r["status"] == state]
    elif state == "overdue":
        rows = [r for r in rows if r["overdue"]]
    live = [r for r in rows if r["status"] == "open"]
    holders = {}
    for r in live:
        holders[r["holder"]] = round(holders.get(r["holder"], 0) + r["balance"], 2)
    return {"rows": rows, "next_code": next_code(db),
            "totals": {"balance": round(sum(r["balance"] for r in live), 2), "open": len(live),
                       "overdue": sum(1 for r in live if r["overdue"]),
                       "owed_to_holders": round(sum(r["owed_to_holder"] for r in live), 2)},
            "by_holder": [{"holder": h, "balance": v} for h, v in sorted(holders.items())],
            "holders": [r["holder"] for r in db.q("SELECT DISTINCT holder FROM custodies ORDER BY holder")]}


def statement(db, cid):
    """Every movement with a running balance, and the lines of each expense report."""
    c = get(db, cid)
    bal = c["opening_balance"]
    rows = []
    if bal:
        rows.append({"date": c["issued_on"], "kind": "Opening balance", "memo": "", "in": to_major(bal), "out": 0,
                     "balance": to_major(bal), "id": None, "lines": []})
    for e in _entries(db, c["code"]):
        n = db.one("SELECT COALESCE(SUM(debit-credit),0) AS n FROM journal_lines WHERE entry_id=? AND account=?",
                   (e["id"], c["account"]))["n"]
        bal += n
        meta = json.loads(e["meta"] or "{}")
        rows.append({"id": e["id"], "date": e["date"], "kind": e["kind"], "memo": e["memo"], "ref": e["ref"],
                     "in": to_major(max(n, 0)), "out": to_major(max(-n, 0)), "balance": to_major(bal),
                     "report_no": meta.get("report_no", ""), "lines": meta.get("lines", [])})
    return {"custody": _row(db, c), "rows": rows}


# ------------------------------------------------------------------- save
def save(db, d, cid=None, user=""):
    old = get(db, cid) if cid else None
    code = text(d.get("code"), maxlen=30) or (old["code"] if old else next_code(db))
    holder = text(d.get("holder"), "employee holding the custody", required=True)
    kind = d.get("kind") or (old and old["kind"]) or "temporary"
    if kind not in TYPES:
        raise ValidationError("Choose temporary or permanent custody")
    account = acc.account_code(db, d.get("account") or (old and old["account"]) or _role_or(db, "custody"),
                               types=("Asset",), field="custody account")
    if db.one("SELECT subtype FROM accounts WHERE code=?", (account,))["subtype"] in ("fixed_asset", "inventory",
                                                                                     "accumulated_depreciation"):
        raise ValidationError("Choose a custody / advances account")
    issued_on = acc.clean_date(d.get("issued_on") or (old and old["issued_on"]) or acc.today(), "date")
    due = acc.clean_date(d.get("due_date"), "settle by") if d.get("due_date") else ""
    if due and due < issued_on:
        raise ValidationError("The settle-by date cannot be before the custody is given")
    limit = to_minor(num(d.get("float_amount"), "limit"))
    how = d.get("booking") or ("keep" if old else "money")
    opening = old["opening_balance"] if old else 0
    if not old and how == "opening":
        opening = to_minor(num(d.get("opening_balance") or d.get("amount"), "balance with the employee", allow_zero=False))
    vals = (code, holder, kind, text(d.get("purpose"), maxlen=300), account, limit, issued_on, due, opening,
            acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), text(d.get("notes"), maxlen=2000))
    with db.tx():
        dup = db.one("SELECT id FROM custodies WHERE code=?", (code,))
        if dup and (not old or dup["id"] != old["id"]):
            raise ValidationError(f"Custody code {code} is already used")
        if old:
            has = _entries(db, old["code"])
            if has and code != old["code"]:
                raise ValidationError("This custody has entries — its code cannot change")
            if has and account != old["account"]:
                raise ValidationError("Money is already booked on the old custody account — it cannot change")
            db.conn.execute("UPDATE custodies SET code=?, holder=?, kind=?, purpose=?, account=?, float_amount=?, issued_on=?, "
                            "due_date=?, opening_balance=?, bu_id=?, notes=? WHERE id=?", vals + (old["id"],))
            return old["id"]
        cid = db.conn.execute("INSERT INTO custodies(code,holder,kind,purpose,account,float_amount,issued_on,due_date,"
                              "opening_balance,bu_id,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                              vals + (acc.now_iso(),)).lastrowid
        amount = d.get("amount") if d.get("amount") not in (None, "") else (to_major(limit) if limit else None)
        if how == "money" and amount not in (None, "", 0, "0"):
            post(db, cid, "issue", {"date": issued_on, "amount": amount, "method": d.get("method"),
                                    "paid_from": d.get("paid_from"), "memo": d.get("memo")}, user)
        return cid


def delete(db, cid):
    c = get(db, cid)
    if _entries(db, c["code"]):
        raise ValidationError("This custody has journal entries — void them first, or settle and close it")
    db.x("DELETE FROM custodies WHERE id=?", (c["id"],))


def set_status(db, cid, closed, date=None):
    c = get(db, cid)
    if closed:
        bal = status(db, c)["balance"]
        if bal:
            raise ValidationError(f"The custody balance is {bal:,.2f} — settle, return, reimburse or deduct it to reach "
                                  "zero before closing")
        db.x("UPDATE custodies SET status='closed', closed_on=? WHERE id=?", (acc.clean_date(date or acc.today()), c["id"]))
    else:
        db.x("UPDATE custodies SET status='open', closed_on='' WHERE id=?", (c["id"],))


# -------------------------------------------------------------- movements
def _L(account, debit=0, credit=0, party=""):
    return {"account": account, "debit": to_major(debit), "credit": to_major(credit), "party": party}


def plan(db, cid, action, d):
    """What an action would post: a list of entries (a settlement splits by business unit). Nothing is saved."""
    c = get(db, cid)
    if c["status"] == "closed":
        raise ValidationError("This custody is closed — reopen it first")
    date = acc.clean_date(d.get("date") or acc.today())
    holder = c["holder"]
    bal = c["opening_balance"] + _moves(db, c)
    out, notes = [], []
    base = {"custody": c["code"], "holder": holder}
    if action == "issue":
        amount = to_minor(num(d.get("amount"), "amount", allow_zero=False))
        money = acc.money_account(db, d.get("method"), d.get("paid_from"))
        what = "Reimbursement to" if bal < 0 else ("Top-up of custody" if _entries(db, c["code"]) or c["opening_balance"] else "Custody given to")
        out.append({"kind": KINDS["ISSUE"], "ref": f"CU:{c['code']}:ISSUE", "bu_id": c["bu_id"],
                    "memo": text(d.get("memo")) or f"{what} {holder} ({c['code']})",
                    "lines": [_L(c["account"], amount, party=holder), _L(money, credit=amount)],
                    "meta": {**base, "amount": to_major(amount)}})
        if c["float_amount"] and bal + amount > c["float_amount"]:
            notes.append(f"This takes the balance to {to_major(bal + amount):,.2f}, above the limit of "
                         f"{to_major(c['float_amount']):,.2f}.")
    elif action == "settle":
        rows = []
        for i, r in enumerate(d.get("lines") or [], 1):
            if not isinstance(r, dict) or r.get("amount") in (None, "", 0, "0"):
                continue
            gross = to_minor(num(r.get("amount"), f"line {i} amount", allow_zero=False))
            account = acc.account_code(db, r.get("account") or _role_or(db, "other_overhead"),
                                       types=("Expense", "COGS", "Asset"), field=f"line {i} account")
            vat_code, vat_rate = acc.tax_code(db, r.get("vat_code"), "vat")
            net = int(round(gross / (1 + vat_rate))) if vat_code else gross
            bu = acc.bu_id_or_none(db, r.get("bu_id") if r.get("bu_id") not in (None, "") else c["bu_id"])
            rows.append({"n": i, "desc": text(r.get("description"), f"line {i} description", required=True, maxlen=300),
                         "account": account, "gross": gross, "net": net, "vat": gross - net, "vat_code": vat_code,
                         "bu_id": bu, "supplier": text(r.get("supplier")), "receipt": text(r.get("receipt"), maxlen=60),
                         "date": acc.clean_date(r.get("date") or date)})
        if not rows:
            raise ValidationError("Add at least one expense to the report")
        report = text(d.get("report_no"), maxlen=40)
        total = sum(r["gross"] for r in rows)
        groups = {}
        for r in rows:
            groups.setdefault(r["bu_id"], []).append(r)
        vat_acc = db.acct("vat_input") if any(r["vat"] for r in rows) else None
        for bu, rs in groups.items():
            lines = []
            for r in rs:
                lines.append(_L(r["account"], r["net"], party=r["supplier"]))
                if r["vat"]:
                    lines.append(_L(vat_acc, r["vat"], party=r["supplier"]))
            sub = sum(r["gross"] for r in rs)
            lines.append(_L(c["account"], credit=sub, party=holder))
            out.append({"kind": KINDS["SET"], "ref": f"CU:{c['code']}:SET", "bu_id": bu,
                        "memo": text(d.get("memo")) or f"Custody expense report {report or ''} — {holder} ({c['code']})".replace("  ", " "),
                        "lines": lines,
                        "meta": {**base, "report_no": report, "total": to_major(sub),
                                 "lines": [{"date": r["date"], "description": r["desc"], "account": r["account"],
                                            "amount": to_major(r["gross"]), "vat": to_major(r["vat"]), "vat_code": r["vat_code"],
                                            "supplier": r["supplier"], "receipt": r["receipt"]} for r in rs]}})
        after = bal - total
        if after < 0:
            notes.append(f"The report is {to_major(-after):,.2f} more than the custody balance — the company owes "
                         f"{holder} that amount (reimburse it with Give / top up).")
        if len(groups) > 1:
            notes.append(f"Split into {len(groups)} entries, one per business unit.")
        if d.get("replenish") in (True, 1, "1", "true", "on"):
            money = acc.money_account(db, d.get("method"), d.get("paid_from"))
            out.append({"kind": KINDS["ISSUE"], "ref": f"CU:{c['code']}:ISSUE", "bu_id": c["bu_id"],
                        "memo": f"Replenish custody {c['code']} ({holder}) after report {report}".strip(),
                        "lines": [_L(c["account"], total, party=holder), _L(money, credit=total)],
                        "meta": {**base, "amount": to_major(total), "replenish": True}})
            notes.append(f"The float is topped back up by {to_major(total):,.2f}.")
    elif action == "return":
        amount = to_minor(num(d.get("amount"), "amount", allow_zero=False))
        if amount > bal:
            raise ValidationError(f"{holder} only holds {to_major(max(bal, 0)):,.2f} on this custody")
        money = acc.money_account(db, d.get("method"), d.get("received_into"))
        out.append({"kind": KINDS["RET"], "ref": f"CU:{c['code']}:RET", "bu_id": c["bu_id"],
                    "memo": text(d.get("memo")) or f"Custody money returned by {holder} ({c['code']})",
                    "lines": [_L(money, amount), _L(c["account"], credit=amount, party=holder)],
                    "meta": {**base, "amount": to_major(amount)}})
    elif action == "deduct":
        amount = to_minor(num(d.get("amount"), "amount", allow_zero=False))
        if amount > bal:
            raise ValidationError(f"{holder} only holds {to_major(max(bal, 0)):,.2f} on this custody")
        target = acc.account_code(db, d.get("account") or db.acct("accrued_payroll"),
                                  types=("Liability", "Expense", "Asset"), field="deduct to")
        payroll = db.one("SELECT type FROM accounts WHERE code=?", (target,))["type"] != "Expense"
        out.append({"kind": KINDS["DED"], "ref": f"CU:{c['code']}:DED", "bu_id": c["bu_id"],
                    "memo": text(d.get("memo")) or (f"Custody balance kept from {holder}'s salary ({c['code']})" if payroll
                                                    else f"Custody balance written off — {holder} ({c['code']})"),
                    "lines": [_L(target, amount, party=holder if payroll else ""), _L(c["account"], credit=amount, party=holder)],
                    "meta": {**base, "amount": to_major(amount)}})
    else:
        raise ValidationError("Unknown custody action")
    return {"custody": c, "date": date, "entries": out, "notes": notes}


def post(db, cid, action, d, user=""):
    with db.tx():
        pl = plan(db, cid, action, d)
        ids = [acc._insert_entry(db, date=pl["date"], kind=e["kind"], memo=e["memo"], bu_id=e["bu_id"],
                                 party=pl["custody"]["holder"], ref=e["ref"], lines=e["lines"], meta=e["meta"], user=user)
               for e in pl["entries"]]
        return ids


# ------------------------------------------------------------- backups
def export_rows(db):
    out = []
    for c in db.q("SELECT c.*, b.code AS bu_code FROM custodies c LEFT JOIN business_units b ON b.id=c.bu_id ORDER BY c.id"):
        c.pop("id")
        c.pop("bu_id")
        out.append(c)
    return out


def import_rows(db, rows):
    cols = ("code", "holder", "kind", "purpose", "account", "float_amount", "issued_on", "due_date", "opening_balance",
            "status", "closed_on", "notes", "created_at")
    nums = {"float_amount", "opening_balance"}
    n = 0
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("code") or not r.get("holder"):
            continue
        bu = db.one("SELECT id FROM business_units WHERE code=?", (r.get("bu_code"),)) if r.get("bu_code") else None
        vals = [r.get(k) if r.get(k) is not None else (0 if k in nums else "") for k in cols]
        db.conn.execute(f"INSERT OR REPLACE INTO custodies({','.join(cols)},bu_id) VALUES ({','.join('?' * len(cols))},?)",
                        vals + [bu["id"] if bu else None])
        n += 1
    return n
