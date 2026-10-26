"""Cash forecast: the money expected in and out over the next 30 / 60 / 90 days, against cash & bank today.

Only what the books already date is projected — nothing is guessed:

  in   customer installments not yet paid (on their due dates), customer cheques waiting to be deposited (on the
       cheque date), loans the company lent (instalments to collect), recurring receipts
  out  company subscriptions & bills (every cycle in the window), loan instalments the company owes, recurring
       expenses / payments / drawings, monthly payroll (last posted month's net pay, on the same day of each month,
       for months not posted yet), purchase orders still to be received (on their expected delivery date)

What is owed with no due date (customer balances, supplier balances, taxes payable, purchase orders without an
expected date) is listed beside the forecast, not spread over dates. Amounts already late can be counted as
collected / paid today ("include overdue") or left out.
"""
import datetime as _dt
import json

from . import accounting as acc
from .db import to_major

WINDOWS = (30, 60, 90)


def _d(s):
    return _dt.date.fromisoformat(s)


def _add_months(day, n):
    import calendar
    y, m = divmod(day.month - 1 + n, 12)
    y += day.year
    return _dt.date(y, m + 1, min(day.day, calendar.monthrange(y, m + 1)[1]))


def cash_now(db):
    r = db.one("""SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                  JOIN accounts a ON a.code=l.account WHERE e.status='posted' AND a.subtype='cash' AND e.date<=?""", (acc.today(),))
    return to_major(r["n"])


def _items(db, today, until):
    """Every dated movement up to `until` (dates before today = overdue)."""
    from . import billsubs, loans, purchasing, recurring, sales
    out = []

    def add(day, amount, source, label, href="", party=""):
        if amount and day <= until:
            out.append({"date": day, "amount": round(amount, 2), "source": source, "label": label, "href": href, "party": party})

    # customer installments (those covered by a cheque come in on the cheque's date instead)
    for r in sales.installments(db, today, "open")["rows"]:
        left = float(r.get("left") or 0)
        if left > 0.005:
            add(r["due"], left, "installments", f"{r['customer']} — {r['code']} installment", f"#/sales/{r['sale_id']}", r["customer"])
    for c in db.q("""SELECT c.*, s.customer, s.code, s.id sale_id FROM sale_cheques c JOIN sales_orders s ON s.id=c.sale_id
                     WHERE c.status='pending' ORDER BY c.due"""):
        add(c["due"], to_major(c["amount"]), "cheques", f"{c['customer']} — cheque {c['number'] or ''} ({c['bank'] or ''})".replace(" ()", ""),
            "#/installments", c["customer"])
    # loans: instalments to collect (lent) or to pay (borrowed)
    for ln in db.q("SELECT * FROM loans"):
        if ln["status"] != "active":
            continue
        try:
            st = loans.schedule_status(db, ln, today)
        except Exception:      # noqa: BLE001 — a loan without a schedule
            continue
        sign = 1 if ln["direction"] == "lent" else -1
        for r in st.get("rows") or []:
            if r.get("left"):
                add(r["due"], sign * to_major(r["left"]), "loans", f"Loan {ln['code']} — {ln['party']}", f"#/loans/{ln['id']}", ln["party"])
    # company subscriptions & bills: each cycle in the window
    for b in billsubs.listing(db)["rows"]:
        if not b["active"]:
            continue
        day, n = b["next_due"], 0
        while day <= until and n < 120:
            add(day, -b["amount"], "bills", b["name"] + (f" ({b['currency_note']})" if b["currency_note"] else ""), f"#/bills/{b['id']}", b["vendor"] or "")
            day, n = billsubs.next_after(day, b["cycle"], b["anchor_day"]), n + 1
    # recurring entries that move cash
    has_payroll_template = False
    for t in db.q("SELECT * FROM recurring WHERE active=1"):
        p = json.loads(t["payload"] or "{}")
        amt = recurring._amount(t)
        kind = t["doc_type"]
        if kind == "payroll":
            has_payroll_template = True
        if kind == "expense":
            flow = -(float(p["paid"]) if p.get("paid") not in (None, "") else amt)
        elif kind in ("payment",):
            flow = -amt
        elif kind in ("direct_cost", "payroll"):
            flow = -float(p.get("paid") or 0)
        elif kind == "receipt":
            flow = amt
        elif kind == "partner_tx":
            flow = amt if p.get("type") == "Capital" else -amt
        elif kind == "journal":
            cash = {r["code"] for r in db.q("SELECT code FROM accounts WHERE subtype='cash'")} | _facility_accounts(db)
            flow = sum(float(x.get("debit") or 0) - float(x.get("credit") or 0) for x in p.get("lines") or [] if x.get("account") in cash)
        else:
            flow = 0
        if not flow:
            continue
        done = {r["due"] for r in db.q("SELECT due FROM recurring_runs WHERE template_id=?", (t["id"],))}
        for day in recurring.occurrences(t, until):
            if day not in done:
                add(day, flow, "recurring", recurring._fill(t["name"], day), "#/recurring")
    # payroll: last posted month's net pay, each month not posted yet
    if not has_payroll_template and db.one("SELECT 1 FROM employees WHERE status='active'"):
        from . import payroll
        last = db.one("SELECT * FROM payroll_runs WHERE status='posted' ORDER BY period DESC LIMIT 1")
        if last:
            net = payroll.get_run(db, last["id"])["totals"]["net"]
            pay_day = _d(last["date"])
            for k in range(1, 5):
                day = _add_months(pay_day, k)
                period = f"{day.year:04d}-{day.month:02d}"
                if db.one("SELECT 1 FROM payroll_runs WHERE period=? AND status='posted'", (period,)):
                    continue
                add(day.isoformat(), -net, "payroll", f"Payroll {period} (as {last['period']}: {net:,.2f})", "#/payruns")
    # purchase orders still to be received, on their expected date
    for po in purchasing.listing(db, status="open")["rows"]:
        if po["open_value"] and po["expected"] and po["status"] in ("approved", "partial"):
            add(po["expected"], -po["open_value"], "purchase_orders", f"{po['code']} — {po['supplier']}", f"#/po/{po['id']}", po["supplier"])
    return out


def _undated(db):
    """Owed with no due date: shown next to the forecast, never spread over dates."""
    from . import purchasing

    def sub(subtypes, sign):
        r = db.one(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                       JOIN accounts a ON a.code=l.account WHERE e.status='posted' AND a.subtype IN ({','.join('?' * len(subtypes))})""", subtypes)
        return round(sign * to_major(r["n"]), 2)
    po = sum(r["open_value"] for r in purchasing.listing(db, status="open")["rows"] if r["status"] in ("approved", "partial") and not r["expected"])
    return [{"key": "receivables", "label": "Customers owe (invoices and balances)", "amount": sub(("receivable",), 1), "dir": "in", "href": "#/aging"},
            {"key": "payables", "label": "Owed to suppliers & subcontractors", "amount": sub(("payable",), -1), "dir": "out", "href": "#/aging?side=payable"},
            {"key": "taxes", "label": "Taxes payable", "amount": sub(("tax_payable",), -1), "dir": "out", "href": "#/report/tax"},
            {"key": "purchase_orders", "label": "Purchase orders with no expected date", "amount": round(po, 2), "dir": "out", "href": "#/po"}]


def _facility(db):
    """The funding partner's facility when his account is the company's bank (facility mode), else None."""
    if (db.setting("funding_mode") or "capital") != "facility":
        return None
    from . import settlement
    f = settlement.facility(db)
    return f if f and f["limit"] else None


def _facility_accounts(db):
    return {r["code"] for r in db.q("SELECT code FROM accounts WHERE role='funding_partner'")} if _facility(db) else set()


def build(db, days=90, include_overdue=True, today=None):
    days = max(7, min(int(days or 90), 366))
    today = today or acc.today()
    until = (_d(today) + _dt.timedelta(days=days)).isoformat()
    items = sorted(_items(db, today, until), key=lambda r: (r["date"], -r["amount"]))
    overdue = [r for r in items if r["date"] < today]
    dated = [r for r in items if r["date"] >= today]
    start = cash_now(db)
    fac = _facility(db)
    if fac:       # facility mode: what can be spent is the room left in his facility, plus any cash of the company's own
        start = round(start + fac["room"], 2)
    o_in = round(sum(r["amount"] for r in overdue if r["amount"] > 0), 2)
    o_out = round(-sum(r["amount"] for r in overdue if r["amount"] < 0), 2)
    opening = round(start + (o_in - o_out if include_overdue else 0), 2)
    # week by week, with the running balance and the lowest point
    weeks, bal, low = [], opening, (opening, today)
    t0 = _d(today)
    for w in range((days + 6) // 7):
        a, b = t0 + _dt.timedelta(days=7 * w), t0 + _dt.timedelta(days=7 * w + 6)
        rows = [r for r in dated if a.isoformat() <= r["date"] <= b.isoformat()]
        i = round(sum(r["amount"] for r in rows if r["amount"] > 0), 2)
        o = round(-sum(r["amount"] for r in rows if r["amount"] < 0), 2)
        for r in rows:
            bal = round(bal + r["amount"], 2)
            if bal < low[0]:
                low = (bal, r["date"])
        weeks.append({"from": a.isoformat(), "to": min(b.isoformat(), until), "in": i, "out": o, "net": round(i - o, 2), "balance": bal})
    windows = []
    for n in WINDOWS:
        if n > days:
            break
        end = (t0 + _dt.timedelta(days=n)).isoformat()
        rows = [r for r in dated if r["date"] <= end]
        i = round(sum(r["amount"] for r in rows if r["amount"] > 0), 2)
        o = round(-sum(r["amount"] for r in rows if r["amount"] < 0), 2)
        windows.append({"days": n, "until": end, "in": i, "out": o, "balance": round(opening + i - o, 2)})
    by_source = {}
    for r in dated:
        s = by_source.setdefault(r["source"], {"source": r["source"], "in": 0.0, "out": 0.0, "count": 0})
        s["in" if r["amount"] > 0 else "out"] = round(s["in" if r["amount"] > 0 else "out"] + abs(r["amount"]), 2)
        s["count"] += 1
    return {"today": today, "until": until, "days": days, "cash_now": start, "basis": "facility" if fac else "cash",
            "facility": {k: fac[k] for k in ("partner", "limit", "fronted", "room")} if fac else None, "include_overdue": bool(include_overdue),
            "overdue": {"in": o_in, "out": o_out, "rows": overdue}, "opening": opening, "windows": windows, "weeks": weeks,
            "lowest": {"balance": low[0], "date": low[1]}, "items": dated, "sources": sorted(by_source.values(), key=lambda s: s["source"]),
            "undated": _undated(db)}
