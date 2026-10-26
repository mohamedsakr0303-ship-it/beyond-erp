"""What each partner would take out if he left today, and the funding partner's own account.

Funding partner (BEYOND's model): one partner's personal account is the company's bank. Every client payment lands in
it and every cost, loan to a partner or distribution leaves it. The money he has put in of his own is therefore
"out − in" across the company's cash / bank movements — live, so it moves with every payment, like the workbook's
Balances Tracker section 5. His capital in the books is the same figure once it is booked (the workbook's automatic
funding entry, or "Book funding to date" here).

Leaving partner: capital (+ funding not yet booked) + current account + his undrawn share of BILLED profit (after the
reserve, less his drawings) + his share of the reserve held back − what he owes the company (loans to him, anything else
in his name) + what the company owes him (loans from him, interest, amounts payable). His share of the profit expected on
work done but not invoiced is a memo only — never in the total.
"""
from . import accounting as acc
from .db import to_major, to_minor

LIVE = acc.LIVE
PARTY = acc.PARTY
PARTY_IS = acc.PARTY_IS


def funding_partner(db):
    return (db.setting("funding_partner") or "").strip()


def _cash_codes(db):
    """Where the company's money moves: cash / bank, and the funding partner's own account when there is one."""
    return [r["code"] for r in db.q("SELECT code FROM accounts WHERE subtype='cash' OR role='funding_partner'")]


def funding_ledger(db, name=None, date_to=None):
    """Every movement of the company's cash / bank as the funding partner's money in and out, grouped like the
    workbook: per project, then custody, partner loans, distributions and company overhead. Entries that book his
    funding itself (cash against his capital) are left out — they are the result, not a movement."""
    name = name or funding_partner(db)
    if not name:
        return None
    date_to = acc.clean_date(date_to) if date_to else acc.today()
    cash = _cash_codes(db)
    if not cash:
        return None
    marks = ",".join("?" * len(cash))
    lines = db.q(f"""SELECT e.id, e.date, e.kind, e.ref, e.memo, e.bu_id, b.code AS bu_code, b.name AS bu_name, {PARTY} AS party,
                            l.account, a.subtype, a.role, a.type, l.debit, l.credit
                     FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                     LEFT JOIN business_units b ON b.id=e.bu_id
                     WHERE {LIVE} AND e.date<=? AND e.id IN (SELECT entry_id FROM journal_lines WHERE account IN ({marks}))
                     ORDER BY e.date, e.id, l.id""", [date_to] + cash)
    by_entry = {}
    for ln in lines:
        by_entry.setdefault(ln["id"], []).append(ln)
    groups, rows = {}, []
    for eid, ls in by_entry.items():
        is_money = lambda x: x["subtype"] == "cash" or x["role"] == "funding_partner"  # noqa: E731
        if any(x["subtype"] in ("capital", "partner_current") and x["role"] != "funding_partner" and x["party"] == name for x in ls):
            continue                                    # booking his funding as capital: not a movement of money
        money = sum(x["debit"] - x["credit"] for x in ls if is_money(x))
        if not money:
            continue                                    # a transfer between the company's own accounts
        other = [x for x in ls if not is_money(x)]
        e = ls[0]
        if e["bu_code"]:
            key, label = "bu:" + e["bu_code"], f"{e['bu_code']} · {e['bu_name'] or ''}".strip(" ·")
        elif any(x["role"] == "custody" for x in other):
            key, label = "custody", "Cash handed to staff as custody (عهدة)"
        elif any(x["subtype"] == "drawings" or x["subtype"] == "dividends_payable" for x in other):
            key, label = "dist", "Distributions paid to partners"
        elif any(x["type"] in ("Asset", "Liability") and x["party"] and x["subtype"] not in ("receivable", "payable", "accrued")
                 and x["role"] not in ("ar", "ap") for x in other):
            key, label = "loans", "Loans to partners / others — lent out, paid back"
        else:
            key, label = "company", "Company overhead & other (no project)"
        g = groups.setdefault(key, {"key": key, "label": label, "in": 0, "out": 0})
        g["in" if money > 0 else "out"] += abs(money)
        rows.append({"id": eid, "date": e["date"], "kind": e["kind"], "ref": e["ref"], "memo": e["memo"], "group": label,
                     "party": e["party"], "in": to_major(max(money, 0)), "out": to_major(max(-money, 0))})
    # money he paid (or received) personally on the company's behalf — booked straight to his capital with no
    # cash / bank line (e.g. a loan he paid out of his own pocket)
    for e in db.q(f"""SELECT e.id, e.date, e.kind, e.ref, e.memo, b.code AS bu_code, b.name AS bu_name, SUM(l.credit-l.debit) AS n
                      FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                      LEFT JOIN business_units b ON b.id=e.bu_id
                      WHERE {LIVE} AND e.date<=? AND {PARTY_IS} AND a.subtype IN ('capital','partner_current')
                        AND COALESCE(a.role,'')<>'funding_partner' AND e.kind<>'Profit Appropriation'
                        AND e.id NOT IN (SELECT entry_id FROM journal_lines WHERE account IN ({marks}))
                      GROUP BY e.id ORDER BY e.date, e.id""", [date_to, name] + cash):
        if not e["n"]:
            continue
        g = groups.setdefault("personal", {"key": "personal", "label": "Paid / received by him personally", "in": 0, "out": 0})
        g["out" if e["n"] > 0 else "in"] += abs(e["n"])
        rows.append({"id": e["id"], "date": e["date"], "kind": e["kind"], "ref": e["ref"], "memo": e["memo"],
                     "group": g["label"], "party": name, "in": to_major(max(-e["n"], 0)), "out": to_major(max(e["n"], 0))})
    rows.sort(key=lambda r: (r["date"], r["id"]))
    run = 0
    for r in rows:
        run += to_minor(r["out"]) - to_minor(r["in"])
        r["own_money"] = to_major(run)                  # his own money in the company after this movement
    order = sorted(groups.values(), key=lambda g: (not g["key"].startswith("bu:"), g["key"]))
    tin, tout = sum(g["in"] for g in order), sum(g["out"] for g in order)
    booked = to_minor(_bal(db, name, date_to, ("capital", "partner_current")))
    own = tout - tin
    limit = float(db.setting("funding_limit") or 0)
    lent = sum(-g["in"] + g["out"] for g in order if g["key"] == "loans")
    return {"partner": name, "date_to": date_to, "lent_out": to_major(lent), "spent_on_company": to_major(max(own, 0) - lent),
            "groups": [{**g, "in": to_major(g["in"]), "out": to_major(g["out"]), "net": to_major(g["in"] - g["out"])} for g in order],
            "rows": rows, "money_in": to_major(tin), "money_out": to_major(tout),
            "own_money": to_major(max(own, 0)), "in_his_account": to_major(max(-own, 0)),
            "booked_capital": to_major(booked), "unbooked": to_major(max(own, 0) - booked),
            "limit": limit, "limit_used": round(max(own, 0) / to_minor(limit), 4) if limit else None}


FUNDING_MODES = ("capital", "facility")


def apply_funding_mode(db, mode=None):
    """capital: what he fronts is his capital in the company (equity). facility: it is a credit line he gives the
    company, owed back to him first (a current liability, like the workbook's account 2400)."""
    mode = mode or (db.setting("funding_mode") or "capital")
    if mode not in FUNDING_MODES:
        raise acc.ValidationError("The funding partner's money is either capital or a facility.")
    typ, sub = ("Liability", "short_term_debt") if mode == "facility" else ("Equity", "capital")
    db.x("UPDATE accounts SET type=?, subtype=? WHERE role='funding_partner'", (typ, sub))
    return mode


def _partner_loans_out(db, date_to):
    """What partners still owe on loans the company made them (registered under Loans, lent)."""
    from . import loans
    names = {r["name"] for r in db.q("SELECT name FROM partners")}
    return to_major(sum(max(0, loans.principal_balance(db, ln, date_to)) for ln in
                        db.q("SELECT * FROM loans WHERE direction='lent' AND status='active'") if ln["party"] in names))


def facility(db, date_to=None, led=None):
    """The funding partner's facility: a base line he always covers plus an optional temporary line (agreed for loans to
    partners; it can shrink as they repay), what he has fronted today and once the suppliers still owed are paid from
    his account, the peak so far, and a status against the warning levels."""
    name = funding_partner(db)
    if not name:
        return None
    date_to = acc.clean_date(date_to) if date_to else acc.today()
    led = led or funding_ledger(db, name, date_to)
    num = lambda k, d=0.0: float(db.setting(k) or d)  # noqa: E731
    base, extra = num("funding_limit"), num("funding_extra")
    shrinks = (db.setting("funding_extra_shrinks") or "yes").lower() not in ("no", "0", "false")
    loans_out = _partner_loans_out(db, date_to) if extra else 0.0
    extra_now = 0.0 if not extra or loans_out <= 0.005 else (min(extra, loans_out) if shrinks else extra)
    limit = round(base + extra_now, 2)
    fronted = led["own_money"]
    owed = acc.deals(db, date_to)["total_owed"]
    # the company's own cash is already part of the ledger (money in), so only what is left over in his favour offsets
    after = round(max(0.0, fronted + owed - led["in_his_account"]), 2)
    peak = max([r["own_money"] for r in led["rows"]] + [fronted, 0.0])
    warn, crit = num("funding_warn", 0.75), num("funding_critical", 0.9)
    used = round(after / limit, 4) if limit else None
    if not limit:
        state, en, ar = "none", "No limit set", "لم يُحدد حد"
    elif used >= 1:
        state, en, ar = "over", f"Over the limit by {after - limit:,.0f}", f"تجاوز الحد بمقدار {after - limit:,.0f}"
    elif used >= crit:
        state, en, ar = "near", f"Near the limit, {limit - after:,.0f} left", f"قريب جداً من الحد، متبقٍ {limit - after:,.0f}"
    elif used >= warn:
        state, en, ar = "approaching", f"Getting close to the limit, {limit - after:,.0f} left", f"يقترب من الحد، متبقٍ {limit - after:,.0f}"
    else:
        state, en, ar = "ok", f"Within the limit, {limit - after:,.0f} left", f"ضمن الحد، متبقٍ {limit - after:,.0f}"
    return {"partner": name, "base": base, "extra": extra, "extra_shrinks": shrinks, "partner_loans": loans_out,
            "extra_now": extra_now, "limit": limit, "fronted": fronted, "room": round(limit - fronted, 2), "owed": owed,
            "after_suppliers": after, "room_after": round(limit - after, 2), "used": used, "peak": round(peak, 2),
            "warn": warn, "critical": crit, "state": state, "status": en, "status_ar": ar,
            "weeks": _weeks(led["rows"], base, date_to)}


def _weeks(rows, base, date_to):
    """Weekly statement of his account (weeks start on Monday): paid out, came in, and where it stood at week end."""
    import datetime as _d
    if not rows:
        return []
    start = _d.date.fromisoformat(rows[0]["date"])
    start -= _d.timedelta(days=start.weekday())
    end = _d.date.fromisoformat(date_to)
    out, i, run = [], 0, 0
    while start <= end:
        nxt = start + _d.timedelta(days=7)
        w_out = w_in = 0
        while i < len(rows) and rows[i]["date"] < nxt.isoformat():
            w_out += to_minor(rows[i]["out"])
            w_in += to_minor(rows[i]["in"])
            run += to_minor(rows[i]["out"]) - to_minor(rows[i]["in"])
            i += 1
        out.append({"week": start.isoformat(), "out": to_major(w_out), "in": to_major(w_in), "net": to_major(w_in - w_out),
                    "fronted": to_major(max(run, 0)), "company_money": to_major(max(-run, 0)),
                    "share_of_base": round(max(run, 0) / to_minor(base), 4) if base else None})
        start = nxt
    return out


def _bal(db, name, date_to, subtypes):
    """Credit-positive balance on accounts of these subtypes in a party's name."""
    marks = ",".join("?" * len(subtypes))
    r = db.one(f"""SELECT COALESCE(SUM(l.credit-l.debit),0) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                   JOIN accounts a ON a.code=l.account WHERE {LIVE} AND e.date<=? AND {PARTY_IS} AND a.subtype IN ({marks})""",
               (date_to, name) + tuple(subtypes))
    return to_major(r["n"])


def book_funding(db, date=None, user=""):
    """Bring the funding partner's capital in the books up (or down) to the money he has actually put in."""
    led = funding_ledger(db, date_to=date)
    if not led:
        raise acc.ValidationError("Choose the funding partner first.")
    diff = to_minor(led["unbooked"])
    if not diff:
        raise acc.ValidationError("The partner's capital per the books already equals the funds contributed.")
    cap = db.acct("capital")
    bank = db.acct("bank")
    amt = to_major(abs(diff))
    lines = ([{"account": bank, "debit": amt}, {"account": cap, "credit": amt, "party": led["partner"]}] if diff > 0 else
             [{"account": cap, "debit": amt, "party": led["partner"]}, {"account": bank, "credit": amt}])
    return acc.record_journal(db, {"date": led["date_to"], "kind": "Capital Contribution" if diff > 0 else "Capital Withdrawal",
                                   "party": led["partner"], "ref": "FUNDING",
                                   "memo": f"Funding partner — his own money in the company brought to {led['own_money']:,.2f}",
                                   "lines": lines}, user=user) if diff else None


def exit_view(db, date_to=None):
    """For every partner: what the company would owe him (or he would owe it) if he left today."""
    from .accounting import partner_report
    date_to = acc.clean_date(date_to) if date_to else acc.today()
    rep = partner_report(db, None, date_to)
    fund = funding_partner(db)
    led = funding_ledger(db, fund, date_to) if fund else None
    reserve = rep["reserve_pct"]
    out = []
    for p in rep["partners"]:
        name = p["name"]
        capital = _bal(db, name, date_to, ("capital",))
        current = _bal(db, name, date_to, ("partner_current",))
        drawings = -_bal(db, name, date_to, ("drawings",))
        # profit already moved into his current account by "Share profit" is not counted again
        shared = db.one(f"""SELECT COALESCE(SUM(l.credit-l.debit),0) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                            JOIN accounts a ON a.code=l.account WHERE {LIVE} AND e.date<=? AND e.kind='Profit Appropriation'
                            AND {PARTY_IS} AND a.subtype IN ('partner_current','drawings')""", (date_to, name))["n"]
        shared = to_major(shared)
        billed = round(p["billed_share"] - shared, 2) if shared else p["billed_share"]
        r = db.one(f"""SELECT COALESCE(SUM(CASE WHEN a.type='Asset' THEN l.debit-l.credit ELSE 0 END),0) AS owes,
                              COALESCE(SUM(CASE WHEN a.type='Liability' THEN l.credit-l.debit ELSE 0 END),0) AS owed
                       FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                       WHERE {LIVE} AND e.date<=? AND {PARTY_IS} AND a.subtype<>'cash'
                             AND COALESCE(a.role,'')<>'funding_partner'""", (date_to, name))   # his facility: counted once, below
        owes, owed = to_major(r["owes"]), to_major(r["owed"])
        unbooked = led["unbooked"] if led and name == fund else 0
        # his billed profit splits into what he may take (less what he already took) and the reserve the company
        # holds back — still his equity, so it is paid out when he leaves
        distributable = round(billed * (1 - reserve), 2) if billed > 0 else billed
        undrawn = round(distributable - drawings, 2)
        reserve_share = round(billed - distributable, 2)
        now = round(capital + unbooked + current + undrawn + reserve_share - owes + owed, 2)
        out.append({"partner": name, "active": p["active"], "ratio": p["profit_ratio"], "capital": capital, "funding_unbooked": unbooked,
                    "current": current, "billed_profit": billed, "distributable": distributable, "drawings": drawings,
                    "undrawn_profit": undrawn, "reserve_share": reserve_share, "owes_company": owes, "company_owes": owed,
                    "owed_now": now, "wip_profit": p["held_in_wip"], "funding": name == fund})
    # memo only: profit expected on work done but not invoiced yet — not owed to anyone until it is billed
    return {"date_to": date_to, "partners": out, "funding_partner": fund, "funding": led, "reserve_pct": reserve,
            "facility": facility(db, date_to, led) if led else None, "funding_mode": db.setting("funding_mode") or "capital"}
