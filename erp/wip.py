"""Work in progress, kept up to date per project — no snapshots.

Work done but not invoiced yet is revenue earned (accrual basis, IFRS 15 / EAS 48 over-time recognition) held in the
unbilled-revenue account (1180) until the invoice goes out. The workbook recalculated it in a formula; here one
system-managed entry per business unit (ref "WIP:<code>") is rewritten whenever the project's costs, invoices or draft
invoices change, so the books always carry the right amount:

  * wip_mode "cost_plus": work done = direct costs × (1 + fee %); less what is already invoiced.
  * wip_mode "drafts" (measured / BOQ-rate work): work done = the executed work on the project's draft invoices.
  * Not set (the default): no automatic work in progress (manual entries are left alone).

The entry sets the project's unbilled revenue to exactly that amount (Dr unbilled / Cr revenue, or back), so when an
invoice is posted the work moves from "not yet invoiced" to the invoice and is never counted twice.
"""
from . import accounting as acc
from .db import to_major, to_minor

KIND = "Work in Progress"
_BUSY = {"on": False}


def ref(code):
    return f"WIP:{code}"


MODES = ("cost_plus", "drafts")


def mode(bu):
    """'cost_plus', 'drafts' or None (the default: no automatic work in progress)."""
    m = (bu.get("wip_mode") or "").strip().lower()
    return m if m in MODES else None


def _sum(db, bu_id, code, where, params=()):
    r = db.one(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                   JOIN accounts a ON a.code=l.account
                   WHERE {acc.LIVE} AND e.bu_id=? AND COALESCE(e.ref,'')<>? AND {where}""", (bu_id, ref(code)) + tuple(params))
    return r["n"] or 0


def target(db, bu):
    """What the project's unbilled revenue should be now, in minor units (None = not managed)."""
    m = mode(bu)
    if m == "cost_plus":
        cost = _sum(db, bu["id"], bu["code"], "a.type='COGS'")
        billed = -_sum(db, bu["id"], bu["code"], "a.type='Revenue' AND COALESCE(a.role,'')<>'negotiation_income'")
        return max(0, int(round(cost * (1 + float(bu.get("fee_pct") or 0)))) - billed)
    if m == "drafts":
        total = 0
        for x in db.q("SELECT id FROM extracts WHERE bu_id=? AND status='draft'", (bu["id"],)):
            total += to_minor(acc.get_extract(db, x["id"])["total"])
        return total
    return None


def sync(db, bu_id):
    """Rewrite the project's work-in-progress entry so its unbilled revenue equals the work done but not invoiced."""
    if _BUSY["on"] or not bu_id:
        return
    bu = db.one("SELECT * FROM business_units WHERE id=?", (int(bu_id),))
    if not bu:
        return
    try:
        unb, rev = db.acct("unbilled_revenue"), db.acct("revenue")
    except LookupError:
        return
    _BUSY["on"] = True
    try:
        tgt = target(db, bu)
        old = db.one("SELECT id, date FROM journal_entries WHERE ref=? AND status='posted' ORDER BY id DESC LIMIT 1", (ref(bu["code"]),))
        amt = 0 if tgt is None else tgt - _sum(db, bu["id"], bu["code"], "l.account=?", (unb,))
        dates = [r["d"] for r in db.q(f"""SELECT MAX(e.date) AS d FROM journal_entries e WHERE {acc.LIVE} AND e.bu_id=?
                                          AND COALESCE(e.ref,'')<>? UNION ALL
                                          SELECT MAX(date) FROM extracts WHERE bu_id=? AND status='draft'""",
                                       (bu["id"], ref(bu["code"]), bu["id"])) if r["d"]]
        date = max(dates) if dates else acc.today()
        lock = (db.setting("lock_date") or "").strip()
        if lock and (date <= lock or (old and old["date"] <= lock)):
            return                                  # a closed period is not rewritten
        if not amt:
            if old:
                db.conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (old["id"],))
                db.conn.execute("DELETE FROM journal_entries WHERE id=?", (old["id"],))
            return
        v = to_major(abs(amt))
        lines = ([{"account": unb, "debit": v}, {"account": rev, "credit": v}] if amt > 0 else
                 [{"account": rev, "debit": v}, {"account": unb, "credit": v}])
        how = "direct costs × (1 + fee)" if mode(bu) == "cost_plus" else "executed work on draft invoices"
        memo = f"Work done, not invoiced yet — {bu['code']} ({how}); kept up to date automatically"
        meta = {"auto_wip": True, "wip": to_major(tgt or 0)}
        if old:
            vl = acc._validate_lines(db, lines)
            db.conn.execute("UPDATE journal_entries SET date=?, memo=?, meta=? WHERE id=?",
                            (date, memo, __import__("json").dumps(meta), old["id"]))
            db.conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (old["id"],))
            db.conn.executemany("INSERT INTO journal_lines(entry_id,account,debit,credit,party) VALUES (?,?,?,?,?)",
                                [(old["id"], a, d, c, p) for a, d, c, p in vl])
        else:
            acc._insert_entry(db, date=date, kind=KIND, memo=memo, bu_id=bu["id"], ref=ref(bu["code"]), lines=lines,
                              meta=meta, user="system")
    finally:
        _BUSY["on"] = False


def sync_all(db):
    for b in db.q("SELECT id FROM business_units ORDER BY id"):
        sync(db, b["id"])


def managed(db, bu_id):
    bu = db.one("SELECT * FROM business_units WHERE id=?", (int(bu_id),)) if bu_id else None
    return bool(bu and mode(bu))
