"""Year-end close: a guided close of a financial year.

The year (from Settings → fiscal year start) gets a checklist read from the books — the trial balance balances,
nothing waits for approval, the banks are reconciled to the year end, depreciation, payroll and recurring entries are
booked for every month, no invoice is left as a draft, no cash account ends below zero. Closing then

* locks the year: the lock date ("books closed up to") moves to the year end, so nothing dated in it can be posted,
  changed or deleted any more, and
* moves its profit to retained earnings: from then on the balance sheet shows the closed years' profit as
  "Retained earnings — closed years" and only the profit since as current earnings.

No closing entry is posted: the closed year's income statement, project reports and budgets keep their real figures
(a closing entry would zero them), and the balance sheet is the same either way. Profit is shared with partners or
set aside as reserves through Partners → profit appropriation as before. A super administrator can reopen the last
closed year (the lock date goes back to what it was).
"""
import datetime as _dt
import json

from . import accounting as acc
from .accounting import ValidationError, now_iso, text
from .db import to_major, to_minor


def _fy_start_md(db):
    v = (db.setting("fiscal_year_start") or "01-01").strip()
    try:
        m, d = (int(x) for x in v.split("-")[:2])
        _dt.date(2001, m, d)
        return m, d
    except (ValueError, TypeError):
        return 1, 1


def year_of(db, day):
    """(start, end) of the financial year that holds `day`."""
    m, d = _fy_start_md(db)
    day = _dt.date.fromisoformat(day)
    start = _dt.date(day.year, m, min(d, 28) if m == 2 else d)
    if start > day:
        start = _dt.date(day.year - 1, start.month, start.day)
    end = _dt.date(start.year + 1, start.month, start.day) - _dt.timedelta(days=1)
    return start.isoformat(), end.isoformat()


def closed(db):
    return db.q("SELECT * FROM year_closes ORDER BY year_end")


def last_closed_end(db, date_to=None):
    r = db.one("SELECT MAX(year_end) e FROM year_closes" + (" WHERE year_end<=?" if date_to else ""), (date_to,) if date_to else ())
    return r["e"] if r and r["e"] else None


def years(db):
    """Every financial year with entries, newest first, with its profit and whether it is closed."""
    first = db.one("SELECT MIN(date) d FROM journal_entries WHERE status='posted'")["d"]
    today = acc.today()
    start, _ = year_of(db, first or today)
    done = {r["year_end"]: r for r in closed(db)}
    out = []
    while start <= today:
        s, e = year_of(db, start)
        net = acc.income_statement(db, s, e)["net_profit"]
        c = done.get(e)
        out.append({"start": s, "end": e, "label": s[:4] if s[5:] == "01-01" else f"{s[:4]}/{e[2:4]}", "net_profit": net,
                    "ended": e < today, "closed": bool(c), "closed_by": c["closed_by"] if c else "", "closed_at": c["closed_at"] if c else "",
                    "notes": c["notes"] if c else ""})
        start = (_dt.date.fromisoformat(e) + _dt.timedelta(days=1)).isoformat()
    return list(reversed(out))


def _month_list(s, e):
    d, out = _dt.date.fromisoformat(s[:8] + "01"), []
    while d.isoformat() <= e:
        out.append(d.isoformat()[:7])
        d = _dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def checklist(db, start, end, lang="en"):
    """What to look at before closing. state: ok | warn (should be fixed or knowingly accepted) | info."""
    out = []
    T = (lambda en, ar: ar) if lang == "ar" else (lambda en, ar: en)  # noqa: E731
    nm = lambda r: (r.get("name_ar") or r["name"]) if lang == "ar" else r["name"]  # noqa: E731 — an account's name in the reader's language

    def add(key, state, title, detail="", href=""):
        out.append({"key": key, "state": state, "title": title, "detail": detail, "href": href})

    def safe(fn):
        try:
            fn()
        except Exception as e:     # noqa: BLE001 — one module's problem must not hide the rest of the list
            add(fn.__name__, "info", T("Could not check ", "تعذر فحص ") + fn.__name__.strip("_").replace("_", " "), str(e)[:200])

    def _trial_balance():
        tb = acc.trial_balance(db, start, end)
        add("trial_balance", "ok" if tb["balanced"] else "warn", T("Trial balance balances", "ميزان المراجعة متوازن") if tb["balanced"] else T("Trial balance does not balance", "ميزان المراجعة غير متوازن"),
            T(f"Debits {tb['totals']['debit']:,.2f} · credits {tb['totals']['credit']:,.2f}", f"المدين {tb['totals']['debit']:,.2f} · الدائن {tb['totals']['credit']:,.2f}"), "#/report/trial_balance?" + f"date_from={start}&date_to={end}")

    def _approvals():
        n = db.one("SELECT COUNT(*) n FROM approvals WHERE status='pending' AND (date='' OR date<=?)", (end,))["n"]
        add("approvals", "warn" if n else "ok", T(f"{n} entr{'ies' if n != 1 else 'y'} waiting for approval", f"{n} قيد في انتظار الاعتماد") if n else T("Nothing waiting for approval", "لا يوجد ما ينتظر الاعتماد"),
            T("Approve or reject them — after closing they can no longer be posted in this year", "اعتمدها أو ارفضها — بعد الإقفال لا يمكن ترحيلها في هذه السنة") if n else "", "#/approvals")

    def _banks():
        rows = db.q("""SELECT a.code, a.name, a.name_ar, (SELECT MAX(s.date_to) FROM bank_statements s WHERE s.account=a.code AND s.status='reconciled') rec,
                              (SELECT COUNT(*) FROM bank_statements s WHERE s.account=a.code) n
                       FROM accounts a WHERE a.active=1 AND a.subtype='cash' AND a.role IN ('bank','bank2') OR
                             (a.subtype='cash' AND EXISTS (SELECT 1 FROM bank_statements s WHERE s.account=a.code))""")
        behind = [r for r in rows if r["n"] and (r["rec"] or "") < end]
        never = [r for r in rows if not r["n"]]
        if behind:
            add("bank", "warn", T("Bank reconciliation is not done to the year end", "التسوية البنكية لم تتم حتى نهاية السنة"),
                "؛ ".join(T(f"{r['code']} {nm(r)}: reconciled to {r['rec'] or 'never'}", f"{r['code']} {nm(r)}: مسوّى حتى {r['rec'] or 'لم يُسوَّ'}") for r in behind), "#/bankrec")
        elif rows and not never:
            add("bank", "ok", T("Banks are reconciled to the year end", "البنوك مسوّاة حتى نهاية السنة"), "", "#/bankrec")
        if never:
            add("bank_none", "info", T("No bank statement imported for ", "لم يُستورد كشف بنك لـ ") + "، ".join(f"{r['code']} {nm(r)}" for r in never),
                T("Importing the year's statements and reconciling them confirms the bank balances", "استيراد كشوف السنة وتسويتها يؤكد أرصدة البنوك"), "#/bankrec")

    def _depreciation():
        from . import assets
        if not db.one("SELECT 1 FROM fixed_assets"):
            return
        p = assets.plan_depreciation(db, end[:7])
        add("depreciation", "warn" if p["rows"] else "ok",
            T(f"Depreciation not booked up to {end[:7]}: {p['total']:,.2f} on {len(p['rows'])} asset(s)", f"الإهلاك غير مسجل حتى {end[:7]}: {p['total']:,.2f} على {len(p['rows'])} أصل")
            if p["rows"] else T("Depreciation is booked for the whole year", "الإهلاك مسجل عن السنة كاملة"),
            T("Run depreciation up to the last month of the year", "شغّل الإهلاك حتى آخر شهر في السنة") if p["rows"] else "", "#/depreciation")

    def _payroll():
        if not db.one("SELECT 1 FROM employees"):
            return
        posted = {r["period"] for r in db.q("SELECT period FROM payroll_runs WHERE status='posted'")}
        used = posted or {r["period"] for r in db.q("SELECT period FROM payroll_runs")}
        if not used:
            return
        first = min(used)
        missing = [m for m in _month_list(start, end) if m >= first and m not in posted]
        add("payroll", "warn" if missing else "ok", T(f"Payroll not posted for {', '.join(missing)}", f"الرواتب غير مرحّلة عن {'، '.join(missing)}") if missing else T("Payroll is posted for every month", "الرواتب مرحّلة عن كل الشهور"),
            "", "#/payruns")

    def _recurring():
        from . import recurring
        due = [r for r in recurring.due_list(db, end) if r["due"] >= start]
        add("recurring", "warn" if due else "ok", T(f"{len(due)} recurring entr{'ies' if len(due) != 1 else 'y'} due in the year not posted or skipped",
              f"{len(due)} قيد دوري مستحق في السنة لم يُرحّل أو يُتخطَّ") if due
            else T("Recurring entries are all posted or skipped", "كل القيود الدورية مرحّلة أو متخطاة"), ", ".join(sorted({r["name"] for r in due}))[:300], "#/recurring")

    def _drafts():
        n = db.one("SELECT COUNT(*) n FROM extracts WHERE status='draft' AND (date IS NULL OR date<=?)", (end,))["n"]
        add("invoices", "warn" if n else "ok", T(f"{n} invoice{'s' if n != 1 else ''} still draft", f"{n} فاتورة ما زالت مسودة") if n else T("No draft invoices", "لا توجد فواتير مسودة"),
            T("Post or delete them: a draft is not in the books", "رحّلها أو احذفها: المسودة ليست في الدفاتر") if n else "", "#/invoices")

    def _cash():
        rows = db.q("""SELECT a.code, a.name, a.name_ar, COALESCE(SUM(l.debit-l.credit),0) n FROM accounts a JOIN journal_lines l ON l.account=a.code
                       JOIN journal_entries e ON e.id=l.entry_id WHERE e.status='posted' AND e.date<=? AND a.subtype='cash'
                       GROUP BY a.code, a.name, a.name_ar HAVING n<0""", (end,))
        add("cash", "warn" if rows else "ok", T("Cash / bank accounts below zero at the year end", "حسابات نقدية / بنكية بالسالب في نهاية السنة") if rows
            else T("No cash or bank account below zero", "لا يوجد حساب نقدي أو بنكي بالسالب"),
            "؛ ".join(f"{r['code']} {nm(r)}: {to_major(r['n']):,.2f}" for r in rows), "#/cash")

    def _open_pos():
        n = db.one("SELECT COUNT(*) n FROM purchase_orders WHERE status IN ('approved','partial') AND date<=?", (end,))["n"]
        if n:
            add("purchase_orders", "info", T(f"{n} purchase order{'s' if n != 1 else ''} from the year not fully received", f"{n} أمر شراء من السنة لم يُستلم بالكامل"),
                T("They carry into the next year as commitments — nothing to book", "تنتقل للسنة التالية كالتزامات — لا يلزم قيد"), "#/pos")

    def _earlier():
        prev = db.one("SELECT 1 FROM journal_entries WHERE status='posted' AND date<? LIMIT 1", (start,))
        if prev:
            s0, e0 = year_of(db, (_dt.date.fromisoformat(start) - _dt.timedelta(days=1)).isoformat())
            if not db.one("SELECT 1 FROM year_closes WHERE year_end=?", (e0,)):
                add("earlier", "info", T(f"The year before ({s0} → {e0}) is not closed", f"السنة السابقة ({s0} ← {e0}) غير مقفلة"),
                    T("Usually years are closed in order", "تُقفل السنوات عادة بالترتيب"), "#/year-end")

    for f in (_trial_balance, _approvals, _banks, _depreciation, _payroll, _recurring, _drafts, _cash, _open_pos, _earlier):
        safe(f)
    return out


def preview(db, year_end, lang="en"):
    s, e = year_of(db, year_end)
    if e != year_end:
        raise ValidationError(f"{year_end} is not the end of a financial year (the year runs {s} → {e})")
    pnl = acc.income_statement(db, s, e)
    re_now = acc.balance_sheet(db, e)
    retained = round(sum(x["amount"] for x in re_now["equity"] if x["subtype"] in ("retained_earnings", "earnings_closed")), 2)
    row = db.one("SELECT * FROM year_closes WHERE year_end=?", (e,))
    return {"start": s, "end": e, "net_profit": pnl["net_profit"], "revenue": pnl["net_sales"], "gross_profit": pnl["gross_profit"],
            "ebitda": pnl["ebitda"], "retained_before": retained, "retained_after": round(retained + pnl["net_profit"], 2),
            "checks": checklist(db, s, e, lang), "closed": dict(row) if row else None, "lock_date": db.setting("lock_date") or "",
            "ended": e < acc.today()}


def close(db, year_end, user, notes=""):
    p = preview(db, year_end)
    if p["closed"]:
        raise ValidationError(f"The year ending {year_end} is already closed")
    if not p["ended"]:
        raise ValidationError(f"The year ends on {p['end']} — it can be closed from the day after")
    lock = db.setting("lock_date") or ""
    with db.tx():
        db.x("INSERT INTO year_closes(year_start, year_end, net_profit, prev_lock, checks, notes, closed_by, closed_at) VALUES (?,?,?,?,?,?,?,?)",
             (p["start"], p["end"], to_minor(p["net_profit"]), lock, json.dumps(p["checks"], ensure_ascii=False), text(notes, maxlen=2000), user, now_iso()))
        if lock < p["end"]:
            db.set_setting("lock_date", p["end"])
    return preview(db, year_end)


def reopen(db, year_end):
    row = db.one("SELECT * FROM year_closes WHERE year_end=?", (year_end,))
    if not row:
        raise ValidationError("This year is not closed")
    if db.one("SELECT 1 FROM year_closes WHERE year_end>?", (year_end,)):
        raise ValidationError("Reopen the later years first")
    with db.tx():
        db.x("DELETE FROM year_closes WHERE id=?", (row["id"],))
        if (db.setting("lock_date") or "") == row["year_end"]:
            db.set_setting("lock_date", row["prev_lock"] or "")
    return preview(db, year_end)


TABLES = ("year_closes",)


def export_rows(db):
    return db.q("SELECT year_start, year_end, net_profit, prev_lock, checks, notes, closed_by, closed_at FROM year_closes ORDER BY year_end")


def import_rows(db, rows, replace=False):
    if replace:
        db.x("DELETE FROM year_closes")
    n = 0
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("year_end") or db.one("SELECT 1 FROM year_closes WHERE year_end=?", (r["year_end"],)):
            continue
        db.x("INSERT INTO year_closes(year_start, year_end, net_profit, prev_lock, checks, notes, closed_by, closed_at) VALUES (?,?,?,?,?,?,?,?)",
             (r.get("year_start") or "", r["year_end"], int(r.get("net_profit") or 0), r.get("prev_lock") or "", r.get("checks") or "[]",
              r.get("notes") or "", r.get("closed_by") or "", r.get("closed_at") or ""))
        n += 1
    return n
