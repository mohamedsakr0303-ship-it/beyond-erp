"""Statements of account and aging: everything posted with a contact on the balance-sheet accounts that hold what
they owe us or what we owe them (receivables, cheques under collection, payables, advances, deposits, custody,
loans), with a running balance and the open balance split by age."""
import datetime as _dt

from . import accounting as acc
from .db import to_major

# accounts that carry money owed by / to a contact; taxes are owed to the tax authority, not to the contact
SUBTYPES = ("receivable", "payable", "accrued", "deferred_revenue", "prepaid", "other_current_asset",
            "other_current_liability", "short_term_debt", "long_term_debt", "other_noncurrent_liability",
            "other_noncurrent_asset")
NOT_ROLES = ("vat_input", "vat_output", "wht_receivable", "wht_payable", "stamp_tax")
BUCKETS = [(0, 30, "0–30", "0–30 يوم"), (31, 60, "31–60", "31–60 يوم"), (61, 90, "61–90", "61–90 يوم"),
           (91, 180, "91–180", "91–180 يوم"), (181, None, "180+", "أكثر من 180 يوم")]


def accounts(db):
    marks = ",".join("?" * len(SUBTYPES))
    nots = ",".join("?" * len(NOT_ROLES))
    return [r["code"] for r in db.q(f"SELECT code FROM accounts WHERE subtype IN ({marks}) AND COALESCE(role,'') NOT IN ({nots})",
                                    SUBTYPES + NOT_ROLES)]


def _lines(db, party=None, date_to=None):
    codes = accounts(db)
    if not codes:
        return []
    w, p = [acc.LIVE, f"l.account IN ({','.join('?' * len(codes))})", f"{acc.PARTY}<>''"], list(codes)
    if party is not None:
        w.append(f"{acc.PARTY}=?")
        p.append(party)
    if date_to:
        w.append("e.date<=?")
        p.append(date_to)
    return db.q(f"""SELECT e.id, e.date, e.kind, e.ref, e.memo, {acc.PARTY} AS party, l.account, a.name AS account_name,
                           a.name_ar AS account_name_ar, l.debit, l.credit
                    FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                    WHERE {' AND '.join(w)} ORDER BY e.date, e.id, l.id""", p)


def later(db, date_to, party=None):
    """Entries with a contact dated after the as-of date: left out of the balance, but often a typo in the date (a
    month off), so the reports say so instead of quietly showing a half-settled balance."""
    out = {}
    for r in _lines(db, party):
        if r["date"] > date_to:
            g = out.setdefault(r["party"], {"party": r["party"], "entries": set(), "first": r["date"], "last": r["date"], "net": 0})
            g["entries"].add(r["id"])
            g["first"], g["last"] = min(g["first"], r["date"]), max(g["last"], r["date"])
            g["net"] += r["debit"] - r["credit"]
    return [{**g, "entries": len(g["entries"]), "net": to_major(g["net"])} for g in sorted(out.values(), key=lambda g: g["first"])]


def _age(items, balance, as_of):
    """Split an open balance by age: the newest charges in the balance's direction make it up (first in, first out)."""
    out = [0] * len(BUCKETS)
    left = abs(balance)
    end = _dt.date.fromisoformat(as_of)
    for date, amt in reversed(items):  # newest first
        if left <= 0:
            break
        take = min(left, amt)
        days = (end - _dt.date.fromisoformat(date)).days
        for i, (lo, hi, *_x) in enumerate(BUCKETS):
            if days >= lo and (hi is None or days <= hi) or (i == 0 and days < 0):
                out[i] += take
                break
        left -= take
    if left > 0:
        out[-1] += left
    sign = 1 if balance >= 0 else -1
    return [to_major(sign * x) for x in out]


def _aging_of(lines, as_of):
    net = sum(r["debit"] - r["credit"] for r in lines)
    side = [(r["date"], r["debit"] - r["credit"]) for r in lines if (r["debit"] - r["credit"]) * net > 0]
    side = [(d, abs(a)) for d, a in side]
    return net, _age(side, net, as_of)


def statement(db, party, date_from=None, date_to=None):
    """One contact's statement: opening, each voucher with a running balance (+ they owe us, − we owe them), aging."""
    party = str(party or "").strip()
    if not party:
        raise acc.ValidationError("Pick a contact")
    date_to = acc.clean_date(date_to) if date_to else acc.today()
    date_from = acc.clean_date(date_from) if date_from else None
    lines = _lines(db, party, date_to)
    opening = sum(r["debit"] - r["credit"] for r in lines if date_from and r["date"] < date_from)
    rows, run, by_entry = [], opening, {}
    for r in lines:
        if date_from and r["date"] < date_from:
            continue
        e = by_entry.get(r["id"])
        if not e:
            e = by_entry[r["id"]] = {"id": r["id"], "date": r["date"], "kind": r["kind"], "ref": r["ref"], "memo": r["memo"],
                                     "debit": 0, "credit": 0, "accounts": []}
            rows.append(e)
        e["debit"] += r["debit"]
        e["credit"] += r["credit"]
        if r["account"] not in e["accounts"]:
            e["accounts"].append(r["account"])
    tot_dr = tot_cr = 0
    for e in rows:
        net = e["debit"] - e["credit"]   # an entry moving money between two of the contact's accounts nets out
        e["debit"], e["credit"] = (net, 0) if net > 0 else (0, -net)
        run += net
        tot_dr += e["debit"]
        tot_cr += e["credit"]
        e["balance"] = to_major(run)
        e["debit"], e["credit"] = to_major(e["debit"]), to_major(e["credit"])
    rows = [e for e in rows if e["debit"] or e["credit"]]
    per_account = {}
    for r in lines:
        a = per_account.setdefault(r["account"], {"account": r["account"], "name": r["account_name"],
                                                  "name_ar": r["account_name_ar"], "balance": 0})
        a["balance"] += r["debit"] - r["credit"]
    balances = [{**a, "balance": to_major(a["balance"])} for a in per_account.values() if a["balance"]]
    net, aging = _aging_of(lines, date_to)
    p = db.one("SELECT * FROM parties WHERE name=?", (party,))
    return {"party": party, "contact": p, "date_from": date_from, "date_to": date_to, "opening": to_major(opening),
            "rows": rows, "debits": to_major(tot_dr), "credits": to_major(tot_cr), "closing": to_major(run),
            "balances": balances, "aging": aging, "later": later(db, date_to, party), "buckets": [b[2] for b in BUCKETS], "buckets_ar": [b[3] for b in BUCKETS]}


def aging(db, date_to=None, side="all"):
    """Every contact's open balance by age. side: 'receivable' (they owe us), 'payable' (we owe them) or 'all'."""
    date_to = acc.clean_date(date_to) if date_to else acc.today()
    groups = {}
    for r in _lines(db, None, date_to):
        groups.setdefault(r["party"], []).append(r)
    out = []
    for party, lines in groups.items():
        net, ag = _aging_of(lines, date_to)
        if not net or (side == "receivable" and net < 0) or (side == "payable" and net > 0):
            continue
        out.append({"party": party, "balance": to_major(net), "aging": ag, "last": max(r["date"] for r in lines)})
    out.sort(key=lambda r: -abs(r["balance"]))
    tot = lambda rows: [round(sum(r["aging"][i] for r in rows), 2) for i in range(len(BUCKETS))]  # noqa: E731
    rec = [r for r in out if r["balance"] > 0]
    pay = [r for r in out if r["balance"] < 0]
    return {"date_to": date_to, "rows": out, "later": later(db, date_to), "buckets": [b[2] for b in BUCKETS], "buckets_ar": [b[3] for b in BUCKETS],
            "receivable": {"balance": round(sum(r["balance"] for r in rec), 2), "aging": tot(rec), "count": len(rec)},
            "payable": {"balance": round(sum(r["balance"] for r in pay), 2), "aging": tot(pay), "count": len(pay)}}
