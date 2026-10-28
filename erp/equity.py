"""Partners' equity: capital contributions and withdrawals, partner loans (with or without interest), share transfers
between partners, admitting a new partner, a partner's exit / buy-out, and profit distributions.

Each partner has a sub-ledger: the capital, current-account, loan, interest and distribution accounts are shared
accounts, and every line carries the partner's name (journal_lines.party), so one entry can move capital from one
partner to another. Ownership percentages live on the partners (and per business unit in partner_shares); every
change is recorded in ownership_changes with the shares before and after, so the latest can be undone — voiding or
reversing its journal entry undoes it too.

Every action has a plan_* function that only computes (the screens show it as a preview) and post() that books it.
"""
import datetime as _dt
import json

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

EPS = 1e-6


# ------------------------------------------------------------------ lookups
def partner(db, value, required=True):
    v = value.get("id") if isinstance(value, dict) else value
    p = db.one("SELECT * FROM partners WHERE id=? OR name=?", (v if str(v).isdigit() else -1, text(v))) if v not in (None, "") else None
    if not p and required:
        raise ValidationError("Choose a partner")
    return p


def _role(db, role):
    try:
        return db.acct(role)
    except LookupError as e:
        raise ValidationError(str(e)) from None


def balances(db, name, date_to=None):
    """What the company owes a partner (credit-positive) on each equity / loan account, as of a date."""
    w, p = acc._where(date_to=date_to)
    rows = db.q(f"""SELECT a.subtype, a.role, SUM(l.credit-l.debit) AS n FROM journal_lines l
                    JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                    WHERE {w} AND {acc.PARTY_IS} GROUP BY a.subtype, a.role""", p + [name])
    out = {"capital": 0, "current": 0, "drawings": 0, "loan": 0, "interest": 0, "distributions_payable": 0,
           "due_to": 0, "due_from": 0}
    for r in rows:
        n = r["n"] or 0
        if r["subtype"] == "capital":
            out["capital"] += n
        elif r["subtype"] == "partner_current":
            out["current"] += n
        elif r["subtype"] == "drawings":
            out["drawings"] -= n
        elif r["role"] == "partner_loans":
            out["loan"] += n
        elif r["role"] == "accrued_interest":
            out["interest"] += n
        elif r["subtype"] == "dividends_payable":
            out["distributions_payable"] += n
        elif r["role"] == "partner_payable":
            out["due_to"] += n
        elif r["role"] == "partner_receivable":
            out["due_from"] -= n
    return {k: to_major(v) for k, v in out.items()}


def total_capital(db, date_to=None):
    w, p = acc._where(date_to=date_to)
    r = db.one(f"""SELECT COALESCE(SUM(l.credit-l.debit),0) AS n FROM journal_lines l JOIN journal_entries e
                   ON e.id=l.entry_id JOIN accounts a ON a.code=l.account WHERE {w} AND a.subtype='capital'""", p)
    return to_major(r["n"])


# ---------------------------------------------------------------- ownership
def state(db):
    """Ownership as data: default shares, per-business-unit overrides and who is still a partner."""
    ps = db.q("SELECT id, default_share, active, joined, left_on FROM partners ORDER BY id")
    return {"shares": {str(p["id"]): round(p["default_share"] or 0, 6) for p in ps},
            "active": {str(p["id"]): p["active"] for p in ps},
            "dates": {str(p["id"]): [p["joined"] or "", p["left_on"] or ""] for p in ps},
            "bu": [[str(r["partner_id"]), r["bu_id"], r["share"]] for r in
                   db.q("SELECT * FROM partner_shares ORDER BY partner_id, bu_id")]}


def _write_state(db, st):
    for pid, s in st["shares"].items():
        if not pid.isdigit():
            continue
        a = st.get("active", {}).get(pid, 1)
        joined, left = st.get("dates", {}).get(pid, ["", ""])
        db.conn.execute("UPDATE partners SET default_share=?, active=?, joined=?, left_on=? WHERE id=?",
                        (s, a, joined, left, int(pid)))
    known = [pid for pid in st["shares"] if pid.isdigit()]
    db.conn.execute("DELETE FROM partner_shares")
    for pid, bu, s in st["bu"]:
        if pid in known:
            db.conn.execute("INSERT OR REPLACE INTO partner_shares(partner_id,bu_id,share) VALUES (?,?,?)", (int(pid), bu, s))


def _apply(st, fn):
    """New state from a function that maps one share vector {pid: share} to another; applied to the default shares
    and to every business unit that has its own shares."""
    out = json.loads(json.dumps(st))
    out["shares"] = {k: round(v, 6) for k, v in fn(dict(st["shares"])).items()}
    bus = sorted({b for _, b, _ in st["bu"]})
    over = {(pid, b): s for pid, b, s in st["bu"]}
    rows = []
    for b in bus:
        vec = {pid: over.get((pid, b), st["shares"][pid]) for pid in st["shares"]}
        for pid, s in fn(vec).items():
            rows.append([pid, b, round(s, 6)])
    out["bu"] = rows
    for pid in out["shares"]:
        out["active"].setdefault(pid, 1)
        out["dates"].setdefault(pid, ["", ""])
    return out


def _check_total(vec, what="Ownership"):
    total = sum(vec.values())
    if total > 1 + EPS:
        raise ValidationError(f"{what} would total {total * 100:.2f}%; the maximum is 100%.")
    if any(v < -EPS for v in vec.values()):
        raise ValidationError(f"{what} cannot be negative")


def _latest_change(db):
    return db.one("SELECT * FROM ownership_changes ORDER BY id DESC LIMIT 1")


def record_change(db, date, kind, memo, before, after, entry_id=None, user=""):
    return db.conn.execute("INSERT INTO ownership_changes(date,kind,memo,entry_id,before,after,created_by,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?)", (date, kind, memo, entry_id, json.dumps(before), json.dumps(after),
                                                          user, acc.now_iso().replace("T", " "))).lastrowid


def undo_change(db, cid=None, entry_id=None):
    """Put the shares back as they were before an ownership change. Only the latest change can be undone."""
    ch = db.one("SELECT * FROM ownership_changes WHERE " + ("entry_id=?" if entry_id else "id=?"),
                (entry_id or cid,))
    if not ch:
        if entry_id:
            return False
        raise ValidationError("Ownership change not found")
    last = _latest_change(db)
    if last["id"] != ch["id"]:
        raise ValidationError(f"Ownership changed again later ({last['kind']} on {last['date']}). Undo that change first.")
    if not entry_id and ch["entry_id"]:
        e = db.one("SELECT status, reversed_by FROM journal_entries WHERE id=?", (ch["entry_id"],))
        if e and e["status"] == "posted" and not e["reversed_by"]:
            raise ValidationError(f"Void or reverse journal entry #{ch['entry_id']} instead; this also undoes the change.")
    acc.check_open(db, ch["date"])
    before = json.loads(ch["before"])
    for p in db.q("SELECT id FROM partners"):  # partners added by the change drop back to no share
        if str(p["id"]) not in before["shares"]:
            before["shares"][str(p["id"])] = 0
            before["active"][str(p["id"])] = 0
    _write_state(db, before)
    db.conn.execute("DELETE FROM ownership_changes WHERE id=?", (ch["id"],))
    return True


def history(db):
    names = {str(p["id"]): p["name"] for p in db.q("SELECT id, name FROM partners")}
    out = []
    for ch in db.q("SELECT * FROM ownership_changes ORDER BY id DESC"):
        b, a = json.loads(ch["before"])["shares"], json.loads(ch["after"])["shares"]
        moves = [{"partner": names.get(pid, pid), "before": b.get(pid, 0), "after": a.get(pid, 0)}
                 for pid in sorted(set(b) | set(a), key=lambda x: int(x) if x.isdigit() else 0)
                 if abs(b.get(pid, 0) - a.get(pid, 0)) > EPS]
        out.append({k: ch[k] for k in ("id", "date", "kind", "memo", "entry_id", "created_by")} | {"moves": moves})
    if out:
        out[0]["latest"] = True
    return out


# ---------------------------------------------------------------- helpers
def _money(db, d):
    return acc.money_account(db, d.get("method"), d.get("cash_account"))


def _pct(v, field):
    """A percentage as typed on the screens (1 = 1%, not 100%)."""
    x = num(v, field) / 100
    if x > 1 + EPS:
        raise ValidationError(f"{field} cannot be more than 100%")
    return round(x, 6)


def _active_ids(st):
    return [pid for pid, a in st["active"].items() if a]


def _shares_from(db, d, st, base):
    """Ownership mode chosen on a contribution / withdrawal: keep, follow capital, or typed in."""
    mode = d.get("ownership") or "keep"
    if mode == "keep":
        return None
    if mode == "by_capital":
        caps = {}
        for pid in _active_ids(st):
            name = db.one("SELECT name FROM partners WHERE id=?", (int(pid),))["name"]
            caps[pid] = max(balances(db, name, d.get("date"))["capital"] + base.get(pid, 0), 0)  # base: major units
        tot = sum(caps.values())
        if tot <= 0:
            raise ValidationError("No partner has capital recorded yet, so shares cannot follow capital")
        return lambda vec: {pid: (caps.get(pid, 0) / tot if pid in caps else 0) for pid in vec}
    if mode == "set":
        given = {str(k): _pct(v, "share") for k, v in (d.get("shares") or {}).items() if str(v).strip() != ""}
        if abs(sum(given.values()) - 1) > 0.0001:
            raise ValidationError(f"The new shares total {sum(given.values()) * 100:.2f}%; they must total 100%.")
        return lambda vec: {pid: given.get(pid, 0) for pid in vec}
    raise ValidationError("Unknown ownership option")


def _plan(kind, memo, party, lines, date, meta=None, transform=None, notes=(), new_partner=None, flags=None):
    return {"kind": kind, "memo": memo, "party": party, "lines": lines, "date": date, "meta": meta or {},
            "transform": transform, "notes": list(notes), "new_partner": new_partner, "flags": flags or {}}


def _L(account, party, debit=0, credit=0):
    return {"account": account, "party": party, "debit": round(debit, 2), "credit": round(credit, 2)}


# ---------------------------------------------------------------- actions
def plan_contribute(db, d):
    """Partner puts money (or an asset, or a loan / current-account balance they are owed) into capital."""
    p = partner(db, d.get("partner"))
    amount = num(d.get("amount"), "amount", allow_zero=False)
    date = acc.clean_date(d.get("date"))
    src = d.get("source") or "cash"
    b = balances(db, p["name"], date)
    if src == "cash":
        dr = _L(_money(db, d), "", amount)
    elif src == "asset":
        dr = _L(acc.account_code(db, d.get("asset_account"), types=("Asset",), field="asset account"), "", amount)
    elif src == "loan":
        if to_minor(amount) > to_minor(b["loan"]):
            raise ValidationError(f"{p['name']}'s loan balance is only {b['loan']:,.2f}")
        dr = _L(_role(db, "partner_loans"), p["name"], amount)
    elif src == "current":
        if to_minor(amount) > to_minor(b["current"]):
            raise ValidationError(f"{p['name']}'s current account is only {b['current']:,.2f}")
        dr = _L(_role(db, "partner_current"), p["name"], amount)
    else:
        raise ValidationError("Unknown source of the contribution")
    lines = [dr, _L(_role(db, "capital"), p["name"], credit=amount)]
    st = state(db)
    return _plan("Capital Contribution", text(d.get("memo")) or f"Capital contribution — {p['name']}", p["name"], lines,
                 date, {"source": src}, _shares_from(db, d, st, {str(p["id"]): amount}))


def plan_withdraw(db, d):
    """Partner takes part of their capital back (a capital reduction — not a profit distribution)."""
    p = partner(db, d.get("partner"))
    amount = num(d.get("amount"), "amount", allow_zero=False)
    date = acc.clean_date(d.get("date"))
    cap = balances(db, p["name"], date)["capital"]
    if to_minor(amount) > to_minor(cap):
        raise ValidationError(f"{p['name']}'s capital is only {cap:,.2f}")
    to = d.get("to") or "cash"
    cr = _L(_money(db, d), "", credit=amount) if to == "cash" else \
        _L(_role(db, "partner_current" if to == "current" else "partner_payable"), p["name"], credit=amount)
    lines = [_L(_role(db, "capital"), p["name"], amount), cr]
    st = state(db)
    return _plan("Capital Withdrawal", text(d.get("memo")) or f"Capital withdrawn — {p['name']}", p["name"], lines, date,
                 {"to": to}, _shares_from(db, d, st, {str(p["id"]): -amount}))


def plan_loan(db, d):
    """Partner lends money to the company — a liability, not ownership. Interest optional."""
    p = partner(db, d.get("partner"))
    amount = num(d.get("amount"), "amount", allow_zero=False)
    rate = (_pct(d.get("rate"), "interest rate") if d.get("rate") not in (None, "") else (p["loan_rate"] or 0))
    lines = [_L(_money(db, d), "", amount), _L(_role(db, "partner_loans"), p["name"], credit=amount)]
    note = [f"Interest {rate * 100:.2f}% a year on {p['name']}'s loan balance — book it with “Loan interest”."] if rate \
        else ["Interest-free loan."]
    return _plan("Partner Loan", text(d.get("memo")) or f"Loan from partner — {p['name']}", p["name"], lines,
                 acc.clean_date(d.get("date")), {"rate": rate}, notes=note, flags={"loan_rate": (p["id"], rate)})


def plan_repay(db, d):
    """Repay a partner's loan, or pay the interest owed on it."""
    p = partner(db, d.get("partner"))
    amount = num(d.get("amount"), "amount", allow_zero=False)
    date = acc.clean_date(d.get("date"))
    what = d.get("what") or "principal"
    b = balances(db, p["name"], date)
    owed, role = (b["interest"], "accrued_interest") if what == "interest" else (b["loan"], "partner_loans")
    if to_minor(amount) > to_minor(owed):
        raise ValidationError(f"{p['name']} is owed {owed:,.2f} on this")
    lines = [_L(_role(db, role), p["name"], amount), _L(_money(db, d), "", credit=amount)]
    return _plan("Partner Loan Repayment", text(d.get("memo")) or
                 (f"Loan interest paid — {p['name']}" if what == "interest" else f"Loan repaid — {p['name']}"),
                 p["name"], lines, date, {"what": what})


def _daily_interest(db, name, account, start, end, rate):
    """Σ balance × rate / 365 over the days start..end (inclusive), from the loan account's movements."""
    rows = db.q(f"""SELECT e.date, SUM(l.credit-l.debit) AS n FROM journal_lines l JOIN journal_entries e
                    ON e.id=l.entry_id WHERE {acc.LIVE} AND l.account=? AND {acc.PARTY_IS} AND e.date<=?
                    GROUP BY e.date ORDER BY e.date""", (account, name, end.isoformat()))
    bal = sum(r["n"] for r in rows if r["date"] < start.isoformat())
    changes = {r["date"]: r["n"] for r in rows if r["date"] >= start.isoformat()}
    total, day = 0.0, start
    while day <= end:  # at most a few thousand days
        bal += changes.get(day.isoformat(), 0)
        total += max(bal, 0) * rate / 365
        day += _dt.timedelta(days=1)
    return int(round(total))


def plan_interest(db, d):
    """Interest on a partner's loan from the day after the last interest booked (or the first loan) to a date."""
    p = partner(db, d.get("partner"))
    end = _dt.date.fromisoformat(acc.clean_date(d.get("date")))
    rate = (_pct(d.get("rate"), "interest rate") if d.get("rate") not in (None, "") else (p["loan_rate"] or 0))
    if not rate:
        raise ValidationError(f"{p['name']}'s loans are interest-free. Set an interest rate first.")
    loan = _role(db, "partner_loans")
    last = None
    for r in db.q(f"SELECT meta FROM journal_entries e WHERE {acc.LIVE} AND e.reversed_by IS NULL AND kind='Partner Loan Interest' "
                  "AND party=?", (p["name"],)):
        if json.loads(r["meta"] or "{}").get("funder_account"):
            continue                                    # interest on his funding account: booked on its own
        to = json.loads(r["meta"] or "{}").get("period_to")
        last = max(last or to, to) if to else last
    if last:
        start = _dt.date.fromisoformat(last) + _dt.timedelta(days=1)
    else:
        first = db.one(f"SELECT MIN(e.date) AS d FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                       f"WHERE {acc.LIVE} AND l.account=? AND {acc.PARTY_IS}", (loan, p["name"]))["d"]
        if not first:
            raise ValidationError(f"{p['name']} has no loan to the company")
        start = _dt.date.fromisoformat(first) + _dt.timedelta(days=1)
    if d.get("from"):
        start = _dt.date.fromisoformat(acc.clean_date(d.get("from")))
    if start > end:
        raise ValidationError(f"Interest is already booked up to {last}")
    amount = _daily_interest(db, p["name"], loan, start, end, rate)
    if not amount:
        raise ValidationError("No interest is due for this period (no loan balance)")
    add_to_loan = d.get("add_to_loan") in (True, 1, "1", "true", "on")
    lines = [_L(_role(db, "interest"), "", to_major(amount)),
             _L(loan if add_to_loan else _role(db, "accrued_interest"), p["name"], credit=to_major(amount))]
    return _plan("Partner Loan Interest", text(d.get("memo")) or
                 f"Interest {rate * 100:.2f}% on {p['name']}'s loan, {start} to {end}", p["name"], lines, end.isoformat(),
                 {"period_from": start.isoformat(), "period_to": end.isoformat(), "rate": rate, "added_to_loan": add_to_loan},
                 notes=[f"{(end - start).days + 1} days at {rate * 100:.2f}% a year on the daily loan balance."])


def plan_transfer(db, d):
    """A partner sells (part of) their share to another partner or to a newcomer. The price is settled between them;
    the company moves the matching part of the seller's capital to the buyer and changes the ownership."""
    seller = partner(db, d.get("seller"))
    date = acc.clean_date(d.get("date"))
    buyer = partner(db, d.get("buyer"), required=False)
    new_name = None if buyer else text(d.get("buyer"), "buyer", required=True)
    if buyer and buyer["id"] == seller["id"]:
        raise ValidationError("The buyer and the seller must be different partners")
    st = state(db)
    s_id = str(seller["id"])
    have = st["shares"].get(s_id, 0)
    pts = have if d.get("all") in (True, 1, "1", "true", "on") else _pct(d.get("share"), "share transferred")
    if pts <= 0:
        raise ValidationError("Enter the share being transferred")
    if pts > have + EPS:
        raise ValidationError(f"{seller['name']} owns only {have * 100:.2f}%")
    frac = min(pts / have, 1) if have else 0
    b_id = str(buyer["id"]) if buyer else "new"
    cap = balances(db, seller["name"], date)["capital"]
    moved = round(cap * frac, 2)
    lines = []
    if moved > 0:
        capital = _role(db, "capital")
        lines = [_L(capital, seller["name"], moved), _L(capital, buyer["name"] if buyer else new_name, credit=moved)]
    price = num(d.get("price"), "price") if d.get("price") not in (None, "") else None
    whole = frac >= 1 - EPS

    def fn(vec):
        out = dict(vec)
        m = out.get(s_id, 0) * frac
        out[s_id] = out.get(s_id, 0) - m
        out[b_id] = out.get(b_id, 0) + m
        return out
    bname = buyer["name"] if buyer else new_name
    memo = text(d.get("memo")) or (f"{seller['name']} {'transfers the whole share' if whole else f'transfers {pts * 100:.2f}%'}"
                                   f" to {bname}" + (f" for {price:,.2f} (paid between them)" if price else ""))
    notes = [f"{seller['name']}'s capital of {cap:,.2f} → {moved:,.2f} moves to {bname}."] if moved else \
        [f"{seller['name']} has no capital recorded, so only the ownership changes (no journal entry)."]
    if price:
        notes.append("The price is paid by the buyer to the seller directly; it does not pass through the company's books.")
    if whole:
        notes.append(f"{seller['name']} will have no share left and is marked as a former partner.")
    return _plan("Share Transfer", memo, seller["name"], lines, date, {"share": pts, "price": price}, fn, notes,
                 new_partner=new_name, flags={"leaves": s_id if whole else None})


def plan_admit(db, d):
    """A new partner invests in the company for a share of it; everyone else is diluted in proportion."""
    name = text(d.get("name"), "new partner's name", required=True)
    if db.one("SELECT 1 FROM partners WHERE name=?", (name,)):
        raise ValidationError(f"{name} is already a partner. Use a capital contribution or a share transfer.")
    date = acc.clean_date(d.get("date"))
    amount = num(d.get("amount"), "amount invested")
    n = _pct(d.get("share"), "share")
    if n <= 0 or n >= 1:
        raise ValidationError("The new partner's share must be between 0% and 100%")
    method = d.get("basis") or "capital"  # how the price above / below book value is treated
    st = state(db)
    old_cap = total_capital(db, date)
    lines, notes = [], []
    capital = _role(db, "capital")
    if amount:
        src = d.get("source") or "cash"
        dr_acc = _money(db, d) if src == "cash" else acc.account_code(db, d.get("asset_account"), types=("Asset",),
                                                                        field="asset account")
        lines.append(_L(dr_acc, "", amount))
        credited = amount if method == "capital" else round(n * (old_cap + amount), 2)
        diff = round(amount - credited, 2)
        lines.append(_L(capital, name, credit=credited))
        if diff and method == "premium":
            if diff < 0:
                raise ValidationError("The investment is below the book value of the share. Use the bonus method.")
            lines.append(_L(_role(db, "share_premium"), "", credit=diff))
            notes.append(f"{diff:,.2f} above the book value of {n * 100:.2f}% goes to share premium.")
        elif diff and method == "bonus":
            olds = {pid: s for pid, s in st["shares"].items() if s > EPS}
            tot = sum(olds.values())
            if not tot:
                raise ValidationError("The existing partners have no shares to share the bonus")
            names = {str(p["id"]): p["name"] for p in db.q("SELECT id, name FROM partners")}
            left = diff
            for i, (pid, s) in enumerate(sorted(olds.items())):
                part = left if i == len(olds) - 1 else round(diff * s / tot, 2)
                left = round(left - part, 2)
                if part:
                    lines.append(_L(capital, names[pid], credit=part) if part > 0 else _L(capital, names[pid], -part))
            notes.append(f"Bonus of {diff:,.2f} {'to the existing partners' if diff > 0 else 'from the existing partners to ' + name},"
                         " by their shares.")
        elif method not in ("capital", "bonus", "premium"):
            raise ValidationError("Unknown method")
        if method != "capital":
            notes.insert(0, f"Book value of {n * 100:.2f}% after the investment = {n * 100:.2f}% × ({old_cap:,.2f} + {amount:,.2f})"
                            f" = {credited:,.2f}.")
    else:
        notes.append("No money is invested — only the ownership changes (no journal entry).")

    def fn(vec):
        out = {pid: s * (1 - n) for pid, s in vec.items()}
        out["new"] = n
        return out
    return _plan("Partner Admission", text(d.get("memo")) or f"{name} joins with {n * 100:.2f}%", name, lines, date,
                 {"share": n, "method": method}, fn, notes, new_partner=name)


def plan_exit(db, d):
    """A partner leaves: their capital and current account are closed against the agreed settlement, paid now and/or
    owed to them; any difference is borne by (or credited to) the remaining partners by their shares."""
    p = partner(db, d.get("partner"))
    date = acc.clean_date(d.get("date"))
    st = state(db)
    pid = str(p["id"])
    others = {k: s for k, s in st["shares"].items() if k != pid and st["active"].get(k)}
    if not others:
        raise ValidationError("The last partner cannot retire. Transfer the share to a new partner instead.")
    b = balances(db, p["name"], date)
    book = round(b["capital"] + b["current"], 2)
    settle = num(d.get("settlement"), "settlement") if d.get("settlement") not in (None, "") else book
    paid = num(d.get("paid"), "paid now") if d.get("paid") not in (None, "") else settle
    if paid > settle + 0.005:
        raise ValidationError("Paid now cannot be more than the settlement")
    lines, notes = [], []
    capital = _role(db, "capital")
    if b["capital"]:
        lines.append(_L(capital, p["name"], b["capital"]) if b["capital"] > 0 else _L(capital, p["name"], credit=-b["capital"]))
    if b["current"]:
        cur = _role(db, "partner_current")
        lines.append(_L(cur, p["name"], b["current"]) if b["current"] > 0 else _L(cur, p["name"], credit=-b["current"]))
    if paid:
        lines.append(_L(_money(db, d), "", credit=paid))
    if settle - paid > 0.005:
        lines.append(_L(_role(db, "partner_payable"), p["name"], credit=round(settle - paid, 2)))
    diff = round(settle - book, 2)
    names = {str(x["id"]): x["name"] for x in db.q("SELECT id, name FROM partners")}
    tot = sum(others.values())
    if diff:
        if not tot:
            raise ValidationError("The remaining partners have no shares to absorb the difference")
        left = diff
        for i, (k, s) in enumerate(sorted(others.items())):
            part = left if i == len(others) - 1 else round(diff * s / tot, 2)
            left = round(left - part, 2)
            if part:
                lines.append(_L(capital, names[k], part) if part > 0 else _L(capital, names[k], credit=-part))
        notes.append(f"The settlement is {abs(diff):,.2f} {'above' if diff > 0 else 'below'} {p['name']}'s book balance "
                     f"({book:,.2f}); the remaining partners' capital {'bears' if diff > 0 else 'receives'} it by their shares.")
    if b["loan"] or b["interest"]:
        notes.append(f"{p['name']} still has a loan of {b['loan']:,.2f} (+ {b['interest']:,.2f} interest) — repay it separately.")
    if b["drawings"]:
        notes.append(f"Their past drawings ({b['drawings']:,.2f}) stay on record under their name.")
    given = {str(k): _pct(v, "share") for k, v in (d.get("shares") or {}).items() if str(v).strip() != ""}

    def fn(vec):
        rest = sum(s for k, s in vec.items() if k != pid)
        if given and vec == st["shares"]:  # the company-wide shares; business units follow proportionally
            out = {k: given.get(k, 0) for k in vec}
        else:
            out = {k: (s / rest if rest > EPS else 0) for k, s in vec.items()}
        out[pid] = 0
        return out
    if given and abs(sum(given.values()) - 1) > 0.0001:
        raise ValidationError(f"The new shares total {sum(given.values()) * 100:.2f}%; they must total 100%.")
    return _plan("Partner Exit", text(d.get("memo")) or f"{p['name']} leaves the company", p["name"],
                 lines if any(ln["debit"] or ln["credit"] for ln in lines) and len(lines) > 1 else [], date,
                 {"settlement": settle, "book_value": book}, fn, notes, flags={"leaves": pid})


def plan_distribute(db, d):
    """Distribute profit to the partners by their shares (or amounts typed per partner): paid now or owed to them."""
    date = acc.clean_date(d.get("date"))
    custom = {str(k): num(v, "amount") for k, v in (d.get("amounts") or {}).items() if str(v).strip() not in ("", "0")}
    ps = {str(p["id"]): p for p in db.q("SELECT * FROM partners WHERE active=1 ORDER BY id")}
    if custom:
        split = {k: v for k, v in custom.items() if k in ps}
        total = round(sum(split.values()), 2)
    else:
        total = num(d.get("amount"), "amount", allow_zero=False)
        shares = {k: p["default_share"] or 0 for k, p in ps.items() if (p["default_share"] or 0) > EPS}
        tot = sum(shares.values())
        if not tot:
            raise ValidationError("Set the partners' shares first.")
        split, left = {}, total
        for i, (k, s) in enumerate(sorted(shares.items())):
            split[k] = left if i == len(shares) - 1 else round(total * s / tot, 2)
            left = round(left - split[k], 2)
    if total <= 0:
        raise ValidationError("Enter the amount to distribute")
    pay_now = d.get("pay_now") in (True, 1, "1", "true", "on")
    draw = _role(db, "drawings")
    lines = [_L(draw, ps[k]["name"], v) for k, v in split.items() if v]
    if pay_now:
        lines.append(_L(_money(db, d), "", credit=total))
    else:
        payable = _role(db, "dividends_payable")
        lines += [_L(payable, ps[k]["name"], credit=v) for k, v in split.items() if v]
    notes = [] if pay_now else ["Owed to the partners until paid — pay each one with a payment voucher "
                                "against “Profit Distributions Payable”."]
    if pay_now:
        from . import funders
        via = funders.by_account(db, lines[-1]["account"])
        if via:
            notes.append(f"Paid through {via['name']}'s account: he pays the partners for the company, so the company owes him "
                         f"{total:,.2f} more there.")
    from .accounting import cash_position
    cp = cash_position(db)
    if pay_now and cp.get("facility"):
        left = cp["facility"]["room_after"] - total
        if left < 0:
            notes.append(f"Paying this would take the funding facility {-left:,.2f} over its {cp['facility']['limit']:,.2f} limit "
                         f"once suppliers are paid.")
    elif pay_now and cp["cash"] - total < cp["floor"]:
        notes.append(f"Cash after paying would be {cp['cash'] - total:,.2f}, below the safety floor of {cp['floor']:,.2f}.")
    return _plan("Profit Distribution", text(d.get("memo")) or f"Profit distribution {total:,.2f}", "", lines, date,
                 {"split": {ps[k]["name"]: v for k, v in split.items()}, "paid_now": pay_now}, notes=notes)


def plan_funder_capital(db, d):
    """Move money between a funding partner's account and his capital, as a journal entry:
       to_capital   — what the company owes him on his account becomes his capital: Dr his account, Cr capital
                      (suggested at most what the company owes him; more is allowed with a warning);
       from_capital — part of his capital goes back to his account (the company then owes it to him there):
                      Dr capital, Cr his account (never more than his capital).
    Ownership shares stay as they are, follow capital, or are typed in (chosen each time)."""
    from . import funders
    f = funders.by_name(db, d.get("funder") or d.get("partner"))
    if not f or not f["account"]:
        raise ValidationError("Choose a funding partner.")
    p = partner(db, f["name"])
    amount = num(d.get("amount"), "amount", allow_zero=False)
    date = acc.clean_date(d.get("date"))
    direction = d.get("direction") or "to_capital"
    owed = funders.balance(db, f, date)
    cap = balances(db, p["name"], date)["capital"]
    notes = []
    if direction == "to_capital":
        if to_minor(amount) > to_minor(max(owed, 0)):
            notes.append(f"The company owes {p['name']} {max(owed, 0):,.2f} on his account; moving {amount:,.2f} goes beyond it "
                         f"(his account would then show {amount - max(owed, 0):,.2f} owed by him).")
        lines = [_L(f["account"], p["name"], amount), _L(_role(db, "capital"), p["name"], credit=amount)]
        kind, memo, base = "Capital Contribution", f"{p['name']}: from his funding account to capital", {str(p["id"]): amount}
    elif direction == "from_capital":
        if to_minor(amount) > to_minor(cap):
            raise ValidationError(f"{p['name']}'s capital is only {cap:,.2f}")
        lines = [_L(_role(db, "capital"), p["name"], amount), _L(f["account"], p["name"], credit=amount)]
        kind, memo, base = "Capital Withdrawal", f"{p['name']}: from capital to his funding account", {str(p["id"]): -amount}
    else:
        raise ValidationError("Choose the direction of the move")
    st = state(db)
    return _plan(kind, text(d.get("memo")) or memo, p["name"], lines, date,
                 {"funder_move": direction, "account": f["account"], "owed_before": owed, "capital_before": cap},
                 _shares_from(db, d, st, base), notes=notes)


def plan_funder_interest(db, d):
    """Interest a funding partner's deal gives him on the money the company owes him on his account: his agreed yearly
    rate on the daily balance, from the day after the interest last booked (or his first funding) to a date.
    Dr interest expense, Cr his account — so it is repaid to him with his money."""
    from . import funders
    f = funders.by_name(db, d.get("funder") or d.get("partner"))
    if not f or not f["account"]:
        raise ValidationError("Choose a funding partner.")
    end = _dt.date.fromisoformat(acc.clean_date(d.get("date")))
    rate = _pct(d.get("rate"), "interest rate") if d.get("rate") not in (None, "") else (f.get("interest_pct") or 0)
    if not rate:
        raise ValidationError(f"{f['name']}'s funding has no interest. Set his interest rate first (only if his deal has one).")
    last = None
    for r in db.q(f"SELECT meta FROM journal_entries e WHERE {acc.LIVE} AND e.reversed_by IS NULL AND kind='Partner Loan Interest' "
                  "AND party=?", (f["name"],)):
        m = json.loads(r["meta"] or "{}")
        if m.get("funder_account") == f["account"] and m.get("period_to"):
            last = max(last or m["period_to"], m["period_to"])
    if last:
        start = _dt.date.fromisoformat(last) + _dt.timedelta(days=1)
    else:
        first = funders.first_funded(db, f)
        if not first:
            raise ValidationError(f"{f['name']} has not funded the company yet")
        start = _dt.date.fromisoformat(first) + _dt.timedelta(days=1)
    if d.get("from"):
        start = _dt.date.fromisoformat(acc.clean_date(d.get("from")))
    if start > end:
        raise ValidationError(f"Interest is already booked up to {last}")
    amount = _daily_interest(db, f["name"], f["account"], start, end, rate)
    if not amount:
        raise ValidationError("No interest is due for this period (nothing owed to him on his account)")
    lines = [_L(_role(db, "interest"), "", to_major(amount)), _L(f["account"], f["name"], credit=to_major(amount))]
    return _plan("Partner Loan Interest", text(d.get("memo")) or
                 f"Interest {rate * 100:.2f}% on {f['name']}'s funding, {start} to {end}", f["name"], lines, end.isoformat(),
                 {"period_from": start.isoformat(), "period_to": end.isoformat(), "rate": rate, "funder_account": f["account"]},
                 notes=[f"{(end - start).days + 1} days at {rate * 100:.2f}% a year on what the company owed him each day; "
                        f"added to what it owes him on his account."])


PLANS = {"contribute": plan_contribute, "withdraw": plan_withdraw, "loan": plan_loan, "repay": plan_repay,
         "interest": plan_interest, "transfer": plan_transfer, "admit": plan_admit, "exit": plan_exit,
         "distribute": plan_distribute, "funder_capital": plan_funder_capital, "funder_interest": plan_funder_interest}
OWNERSHIP_ACTIONS = ("transfer", "admit", "exit")


def preview(db, action, d):
    if action not in PLANS:
        raise ValidationError("Unknown action")
    pl = PLANS[action](db, d)
    names = {str(p["id"]): p["name"] for p in db.q("SELECT id, name FROM partners")}
    names["new"] = pl["new_partner"] or ""
    out = {"kind": pl["kind"], "memo": pl["memo"], "date": pl["date"], "notes": pl["notes"],
           "lines": [ln for ln in pl["lines"] if ln["debit"] or ln["credit"]], "ownership": None,
           "statement": pl["meta"].get("statement")}
    acc_names = {a["code"]: a for a in db.q("SELECT code, name, name_ar FROM accounts")}
    for ln in out["lines"]:
        ln["name"], ln["name_ar"] = acc_names[ln["account"]]["name"], acc_names[ln["account"]]["name_ar"]
    if pl["transform"]:
        st = state(db)
        after = pl["transform"](dict(st["shares"]))
        _check_total(after)
        out["ownership"] = [{"partner": names.get(k, k), "before": st["shares"].get(k, 0), "after": round(v, 6)}
                            for k, v in after.items() if st["shares"].get(k, 0) > EPS or v > EPS]
    return out


def post(db, action, d, user=""):
    if action not in PLANS:
        raise ValidationError("Unknown action")
    with db.tx():
        pl = PLANS[action](db, d)
        acc.check_open(db, pl["date"])
        before = state(db)
        new_id = None
        if pl["new_partner"]:
            new_id = db.conn.execute("INSERT INTO partners(name, default_share, active, joined) VALUES (?,0,1,?)",
                                     (pl["new_partner"], pl["date"])).lastrowid
        eid = None
        lines = [ln for ln in pl["lines"] if ln["debit"] or ln["credit"]]
        if lines:
            eid = acc._insert_entry(db, date=pl["date"], kind=pl["kind"], memo=pl["memo"], party=pl["party"],
                                    bu_id=acc.bu_id_or_none(db, d.get("bu_id")), ref=text(d.get("ref")),
                                    lines=lines, meta=pl["meta"], user=user)
        if pl["flags"].get("loan_rate"):
            db.conn.execute("UPDATE partners SET loan_rate=? WHERE id=?", pl["flags"]["loan_rate"][::-1])
        cid = None
        if pl["transform"]:
            fn = pl["transform"]
            if new_id:
                def fn(vec, f=pl["transform"], nid=str(new_id)):  # noqa: E306
                    return {(nid if k == "new" else k): v for k, v in f({k: v for k, v in vec.items() if k != nid}).items()}
            after = _apply(before, fn)
            _check_total(after["shares"])
            leaves = pl["flags"].get("leaves")
            if leaves:
                after["active"][leaves] = 0
                after["dates"][leaves] = [after["dates"].get(leaves, ["", ""])[0], pl["date"]]
            if new_id:
                after["dates"][str(new_id)] = [pl["date"], ""]
            _write_state(db, after)
            cid = record_change(db, pl["date"], pl["kind"], pl["memo"], before, after, eid, user)
        return {"id": eid, "change": cid, "partner_id": new_id}


def set_ownership(db, d, user=""):
    """Type in new shares directly (e.g. a new partnership agreement) — recorded so it can be undone."""
    date = acc.clean_date(d.get("date"))
    with db.tx():
        acc.check_open(db, date)
        before = state(db)
        fn = _shares_from(db, {**d, "ownership": "set"}, before, {})
        after = _apply(before, fn) if d.get("all_units") in (True, 1, "1", "true", "on") else \
            {**before, "shares": {k: round(v, 6) for k, v in fn(dict(before["shares"])).items()}}
        _write_state(db, after)
        return record_change(db, date, "Ownership Change", text(d.get("memo")) or "Shares changed", before, after, None, user)


# ------------------------------------------------------------ interest on capital
def capital_interest(db, date_from, date_to):
    """Agreed yearly return on each partner's capital for a period (an appropriation of profit, not an expense)."""
    out = {}
    if not date_to:
        date_to = acc.today()
    capital = [a["code"] for a in db.q("SELECT code FROM accounts WHERE subtype='capital'")]
    for p in db.q("SELECT * FROM partners WHERE capital_interest>0"):
        if not date_from:
            first = db.one(f"SELECT MIN(e.date) AS d FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                           f"WHERE {acc.LIVE} AND {acc.PARTY_IS} AND l.account IN ({','.join('?' * len(capital))})",
                           [p["name"]] + capital)["d"]
            if not first:
                continue
            start = _dt.date.fromisoformat(first)
        else:
            start = _dt.date.fromisoformat(acc.clean_date(date_from))
        end = _dt.date.fromisoformat(acc.clean_date(date_to))
        total = sum(_daily_interest(db, p["name"], c, start, end, p["capital_interest"]) for c in capital)
        out[p["name"]] = to_major(total)
    return out


# ------------------------------------------------- sharing the profit (appropriation)
APPROPRIATION = "Profit Appropriation"
_LIVE_APPR = f"{acc.LIVE} AND e.reversed_by IS NULL AND e.reversal_of IS NULL AND e.kind='{APPROPRIATION}'"


def profit_ratios(db, partners=None):
    """Each active partner's profit / loss sharing ratio: the agreed ratio when one is typed for any partner,
    otherwise their ownership. {partner id (str): ratio}."""
    ps = partners if partners is not None else db.q("SELECT * FROM partners WHERE active=1 ORDER BY id")
    typed = any(p["profit_share"] is not None for p in ps)
    return {str(p["id"]): float((p["profit_share"] if typed else p["default_share"]) or 0) for p in ps}, typed


def _months(start, end):
    """Calendar months in start..end, a part month counted by its days (1 Jan–31 Mar = 3; 1–15 Jan = 15/31)."""
    total, d = 0.0, start
    while d <= end:
        nxt = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
        last = min(end, nxt - _dt.timedelta(days=1))
        total += ((last - d).days + 1) / (nxt - d.replace(day=1)).days
        d = nxt
    return total


def _drawings_interest(db, name, account, start, end, rate):
    """Σ drawings outstanding × rate / 365 over the days, counting only what was drawn inside the period."""
    rows = db.q(f"""SELECT e.date, SUM(l.debit-l.credit) AS n FROM journal_lines l JOIN journal_entries e
                    ON e.id=l.entry_id WHERE {acc.LIVE} AND e.kind<>? AND l.account=? AND {acc.PARTY_IS}
                    AND e.date>=? AND e.date<=? GROUP BY e.date""",
                (APPROPRIATION, account, name, start.isoformat(), end.isoformat()))
    changes = {r["date"]: r["n"] for r in rows}
    total, bal, day = 0.0, 0, start
    while day <= end:
        bal += changes.get(day.isoformat(), 0)
        total += max(bal, 0) * rate / 365
        day += _dt.timedelta(days=1)
    return int(round(total))


def appropriations(db):
    out = []
    for e in db.q(f"SELECT e.id, e.date, e.memo, e.meta FROM journal_entries e WHERE {_LIVE_APPR} ORDER BY e.date DESC, e.id DESC"):
        m = json.loads(e["meta"] or "{}")
        out.append({"id": e["id"], "date": e["date"], "memo": e["memo"], "period_from": m.get("period_from"),
                    "period_to": m.get("period_to"), "profit": m.get("profit", 0), "statement": m.get("statement")})
    return out


def _split(total, weights):
    """Split an amount in minor units by weights; the last one takes the rounding."""
    keys = list(weights)
    tot = sum(weights.values())
    out, left = {}, total
    for i, k in enumerate(keys):
        out[k] = left if i == len(keys) - 1 else (int(round(total * weights[k] / tot)) if tot else 0)
        left -= out[k]
    return out


def plan_appropriate(db, d):
    """Share a period's profit (or loss), in the professional order: legal reserve → partners' salaries and interest on
    capital (paid only out of profit unless agreed otherwise) → interest charged on drawings added back → the rest by
    the profit-sharing ratio. Everything lands on each partner's current account; capital stays as agreed. The
    period's drawings are closed into the current accounts too."""
    end = _dt.date.fromisoformat(acc.clean_date(d.get("date_to") or d.get("date")))
    if d.get("date_from"):
        start = _dt.date.fromisoformat(acc.clean_date(d.get("date_from")))
    else:
        last = max((a["period_to"] for a in appropriations(db) if a["period_to"]), default=None)
        if last:
            start = _dt.date.fromisoformat(last) + _dt.timedelta(days=1)
        else:
            first = db.one(f"SELECT MIN(e.date) AS d FROM journal_entries e WHERE {acc.LIVE}")["d"]
            start = _dt.date.fromisoformat(first) if first else end
    if start > end:
        raise ValidationError("The period starts after it ends")
    for a in appropriations(db):
        if a["period_from"] and a["period_from"] <= end.isoformat() and a["period_to"] >= start.isoformat():
            raise ValidationError((f"Profit for {a['period_from']} to {a['period_to']} has already been appropriated (entry #{a['id']}). Choose a later period, or void that entry first."))
    sf, st_ = start.isoformat(), end.isoformat()
    ps = db.q("SELECT * FROM partners WHERE active=1 ORDER BY id")
    if not ps:
        raise ValidationError("Add the partners first.")
    ratio, typed = profit_ratios(db, ps)
    if abs(sum(ratio.values()) - 1) > 0.0001:
        raise ValidationError((f"The {'profit-sharing ratios' if typed else 'partners’ shares'} total {sum(ratio.values()) * 100:.2f}%; they must total 100%."))
    names = {str(p["id"]): p["name"] for p in ps}
    notes = []
    if typed and any(r <= EPS for r in ratio.values()):
        notes.append("A partner has a 0% profit share. Under Egyptian Civil Code article 515 a clause that keeps a partner out "
                     "of the profits or the losses is void — check the partnership agreement.")
    left = [p["name"] for p in db.q("SELECT name, left_on FROM partners WHERE active=0 AND left_on>=? AND left_on<=?", (sf, st_))]
    if left:
        notes.append(f"{', '.join(left)} left during this period — their share up to the exit date is not included here; "
                     "share the profit up to the exit date before recording an exit.")
    # 1 · the period's result, and who it belongs to (business units can have their own owners)
    profit = to_minor(acc.income_statement(db, sf, st_)["net_profit"])
    base = {k: 0 for k in ratio}
    over = {}
    for r in db.q("SELECT * FROM partner_shares"):
        over.setdefault(r["bu_id"], {})[str(r["partner_id"])] = r["share"]
    in_units = 0
    for b in acc.bu_summaries(db, sf, st_):
        bp = to_minor(b["net_profit"])
        if not bp:
            continue
        vec = {k: over.get(b["id"], {}).get(k, ratio[k]) for k in ratio}
        if abs(sum(vec.values()) - 1) > 0.0001:
            raise ValidationError((f"The partners' shares in business unit {b['code']} total {sum(vec.values()) * 100:.2f}%; they must total 100%."))
        for k, v in _split(bp, vec).items():
            base[k] += v
        in_units += bp
    for k, v in _split(profit - in_units, ratio).items():
        base[k] += v
    # 2 · legal reserve, out of a profit only, until it reaches the cap
    pct = _pct(d["reserve_pct"], "legal reserve %") if d.get("reserve_pct") not in (None, "") else \
        float(db.setting("legal_reserve_pct", "0") or 0)
    cap = float(db.setting("legal_reserve_cap", "0.5") or 0)
    reserve, reserve_note = 0, ""
    if profit > 0 and pct > 0:
        reserve = int(round(profit * pct))
        if cap > 0:
            have = db.one(f"SELECT COALESCE(SUM(l.credit-l.debit),0) AS n FROM journal_lines l JOIN journal_entries e "
                       f"ON e.id=l.entry_id WHERE {acc.LIVE} AND e.date<=? AND l.account=?", (st_, _role(db, "legal_reserve")))["n"]
            room = max(int(round(to_minor(total_capital(db, st_)) * cap)) - have, 0)
            if reserve > room:
                reserve, reserve_note = room, f"limited — the reserve reaches {cap * 100:g}% of capital"
    # 3 · per partner: salary, interest on capital, interest on drawings, drawings to close
    draw_acc = _role(db, "drawings")
    capital = [a["code"] for a in db.q("SELECT code FROM accounts WHERE subtype='capital'")]
    months = _months(start, end)
    rows = {}
    for p in ps:
        k, pstart = str(p["id"]), start
        if p["joined"] and p["joined"] > sf:
            pstart = _dt.date.fromisoformat(p["joined"])
        sal = int(round((p["salary"] or 0) * _months(pstart, end))) if pstart <= end else 0
        ioc = sum(_daily_interest(db, p["name"], c, pstart, end, p["capital_interest"]) for c in capital) \
            if p["capital_interest"] else 0
        iod = _drawings_interest(db, p["name"], draw_acc, start, end, p["drawings_interest"]) if p["drawings_interest"] else 0
        drawn = to_minor(balances(db, p["name"], st_)["drawings"])
        rows[k] = {"partner": p["name"], "ratio": ratio[k], "salary": sal, "ioc": ioc, "iod": iod, "drawings": max(drawn, 0)}
    asked = sum(r["salary"] + r["ioc"] for r in rows.values())
    iod_total = sum(r["iod"] for r in rows.values())
    avail = profit - reserve + iod_total
    mode = d.get("mode") or "limit"
    if mode not in ("limit", "full"):
        raise ValidationError("Unknown option for salaries and interest when profit is short")
    factor = 1.0 if mode == "full" or not asked else max(0.0, min(1.0, avail / asked))
    if asked and factor < 1:
        notes.append("Profit is not enough for the agreed salaries and interest on capital — they are "
                     + ("not paid this period (there is no profit)." if factor == 0 else f"cut to {factor * 100:.1f}% of the agreed amounts.")
                     + " Choose “pay them in full” if your agreement says they are due whatever the result; "
                       "the shortfall is then shared as a loss.")
    if factor < 1:
        for r in rows.values():
            r["salary"], r["ioc"] = int(round(r["salary"] * factor)), int(round(r["ioc"] * factor))
    paid = sum(r["salary"] + r["ioc"] for r in rows.values())
    residual = profit - reserve + iod_total - paid
    # the result of each unit goes to its owners; the company-wide items are borne by the sharing ratio
    charges = _split(reserve + paid - iod_total, ratio)
    for k, r in rows.items():
        r["residual"] = base[k] - charges[k]
    if not (profit or any(r["salary"] or r["ioc"] or r["iod"] or r["drawings"] for r in rows.values())):
        raise ValidationError("Nothing to appropriate for this period: no profit or loss, salaries, interest or drawings.")
    # 4 · the entry
    M = to_major
    cur, appr = _role(db, "partner_current"), _role(db, "appropriation")
    lines = [_L(appr, "", M(profit)) if profit > 0 else _L(appr, "", credit=M(-profit))]
    if reserve:
        lines.append(_L(_role(db, "legal_reserve"), "", credit=M(reserve)))
    close = d.get("close_drawings", True) not in (False, 0, "0", "false", "off")
    for r in rows.values():
        n = r["partner"]
        lines += [_L(cur, n, credit=M(r["salary"])), _L(cur, n, credit=M(r["ioc"])), _L(cur, n, M(r["iod"])),
                  _L(cur, n, credit=M(r["residual"])) if r["residual"] >= 0 else _L(cur, n, M(-r["residual"]))]
        if close and r["drawings"]:
            lines += [_L(cur, n, M(r["drawings"])), _L(draw_acc, n, credit=M(r["drawings"]))]
    opening = {r["partner"]: balances(db, r["partner"], st_)["current"] for r in rows.values()}
    statement = {
        "period_from": sf, "period_to": st_, "months": round(months, 4), "profit": M(profit),
        "reserve": M(reserve), "reserve_pct": pct, "reserve_note": reserve_note, "interest_on_drawings": M(iod_total),
        "salaries": M(sum(r["salary"] for r in rows.values())), "interest_on_capital": M(sum(r["ioc"] for r in rows.values())),
        "residual": M(residual), "ratio_basis": "agreed profit-sharing ratio" if typed else "ownership", "mode": mode,
        "factor": round(factor, 6), "drawings_closed": close,
        "partners": [{"partner": r["partner"], "ratio": r["ratio"], "salary": M(r["salary"]), "interest_on_capital": M(r["ioc"]),
                      "interest_on_drawings": M(r["iod"]), "share_of_rest": M(r["residual"]),
                      "total": M(r["salary"] + r["ioc"] - r["iod"] + r["residual"]),
                      "drawings": M(r["drawings"]) if close else 0, "current_before": opening[r["partner"]],
                      "current_after": round(opening[r["partner"]] + M(r["salary"] + r["ioc"] - r["iod"] + r["residual"])
                                             - (M(r["drawings"]) if close else 0), 2)}
                     for r in rows.values()]}
    if over:
        notes.append("Business units with their own partner shares: each unit's result goes to its owners; the reserve, "
                     "salaries and interest are borne by the sharing ratio.")
    memo = text(d.get("memo")) or f"Profit shared {sf} to {st_}"
    return _plan(APPROPRIATION, memo, "", lines, st_, {"period_from": sf, "period_to": st_, "profit": M(profit),
                                                        "statement": statement}, notes=notes)


PLANS["appropriate"] = plan_appropriate
