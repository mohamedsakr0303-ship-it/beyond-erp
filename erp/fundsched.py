"""The funding partner's repayment schedule: what clients paid and his share of each so far, then month by month what he
is expected to get back from the money still to come in, until his money in the company is repaid.

His share of each client payment is the distribution policy's `funding_repay_pct`; his money in the company is the
balance of his funding account. Money expected in: what customers owe today (collected `lag` months from now),
installments, customer cheques and recurring receipts from the cash forecast, and each active rental contract's monthly
rent from its next unbilled month to its last month. The rent and the last month of each contract can be set here
(`funding_schedule` setting) since a contract has no end date of its own; left blank, rent runs to the end of the horizon.
"""
import datetime as _dt
import json

from . import accounting as acc
from .db import to_major

KEY = "funding_schedule"


def _month(d):
    return d[:7]


def _add(month, n):
    y, m = int(month[:4]), int(month[5:7]) - 1 + n
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def load(db):
    try:
        v = json.loads(db.setting(KEY) or "{}")
    except ValueError:
        v = {}
    return v if isinstance(v, dict) else {}


def clean(v):
    out = {"months": max(6, min(int(acc.num((v or {}).get("months") or 36, "months")), 120)),
           "lag": max(0, min(int(acc.num((v or {}).get("lag") or 0, "months")), 12)), "rentals": {}}
    for code, r in ((v or {}).get("rentals") or {}).items():
        if not isinstance(r, dict):
            continue
        row = {}
        if r.get("monthly") not in (None, ""):
            row["monthly"] = round(max(0.0, acc.num(r["monthly"], "monthly rent")), 2)
        last = acc.text(r.get("last"))
        if last:
            if len(last) < 7 or not last[:4].isdigit() or last[4] != "-" or not last[5:7].isdigit():
                raise acc.ValidationError("The last month looks like 2028-07")
            row["last"] = last[:7]
        if row:
            out["rentals"][acc.text(code)[:40]] = row
    return out


def history(db, date_to):
    """Each client payment with his share, and what was paid back to him on the same day."""
    pays = db.q(f"""SELECT e.id, e.date, e.memo, e.party, SUM(l.credit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                    JOIN accounts a ON a.code=l.account WHERE {acc.LIVE} AND e.date<=? AND e.kind='Receipt Voucher'
                    AND (a.subtype='receivable' OR a.role='client_advances') AND l.credit>0 GROUP BY e.id ORDER BY e.date, e.id""",
                 (date_to,))
    back = {}
    for r in db.q(f"""SELECT e.date, SUM(l.debit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                      JOIN accounts a ON a.code=l.account WHERE {acc.LIVE} AND e.date<=? AND a.role='funding_partner' AND l.debit>0
                      GROUP BY e.date""", (date_to,)):
        back[r["date"]] = to_major(r["n"])
    return pays, back


def build(db, date_to=None, opts=None):
    from . import cashplan, partner_pack, rentals, settlement
    date_to = acc.clean_date(date_to) if date_to else acc.today()
    o = clean({**load(db), **(opts or {})})
    pol = partner_pack.policy(db)
    pct = pol.get("funding_repay_pct")
    name = settlement.funding_partner(db)
    rows = acc.account_totals(db, date_to=date_to)
    owed_him = round(-sum(r["net"] for r in rows if r.get("role") == "funding_partner") / 100, 2)
    pays, back = history(db, date_to)
    hist, used = [], set()
    for p in pays:
        amt = to_major(p["n"])
        rep = back.get(p["date"], 0.0) if p["date"] not in used else 0.0
        used.add(p["date"])
        hist.append({"date": p["date"], "id": p["id"], "memo": p["memo"], "party": p["party"] or "", "payment": amt,
                     "share": round(amt * (pct or 0), 2), "repaid": rep})
    other_back = round(sum(v for d, v in back.items() if d not in used), 2)       # repaid on days with no client payment
    paid = round(sum(h["payment"] for h in hist), 2)
    repaid = round(sum(back.values()), 2)
    due_now = round(max(0.0, min(max(owed_him, 0.0), paid * (pct or 0) - repaid)), 2)
    # ---- money expected in, by month
    start = _month(date_to)
    first = _add(start, 1) if date_to[8:] > "25" else start          # late in a month: the projection starts next month
    months = [_add(first, k) for k in range(o["months"])]
    streams = {}

    def put(key, label, month, amt, href=""):
        if amt and month in months:
            s = streams.setdefault(key, {"key": key, "label": label, "href": href, "by": {}})
            s["by"][month] = round(s["by"].get(month, 0.0) + amt, 2)
    recv = round(sum(to_major(r["net"]) for r in rows if r["subtype"] in ("receivable",)), 2)
    if recv > 0:
        put("receivables", "Customers owe today", _add(first, o["lag"]), recv, "#/aging")
    until = f"{months[-1]}-28"
    for it in cashplan._items(db, date_to, until):
        if it["amount"] > 0 and it["source"] in ("installments", "cheques", "recurring"):
            put(it["source"], {"installments": "Customer installments", "cheques": "Customer cheques",
                               "recurring": "Recurring receipts"}[it["source"]], max(_month(it["date"]), first), it["amount"], it.get("href", ""))
    contracts = []
    for r in rentals.ongoing(db, date_to)["rows"]:
        conf = o["rentals"].get(r["code"], {})
        monthly = conf.get("monthly", r["monthly"])
        nxt = _month(r["next_from"]) if r["next_from"] else first
        last = conf.get("last", "")
        contracts.append({"code": r["code"], "client": r["client"], "site": r["site"], "monthly": monthly, "contract_monthly": r["monthly"],
                          "next": nxt, "last": last, "owed": r["owed"]})
        for m in months:
            if m >= nxt and (not last or m <= last):
                put("rent:" + r["code"], f"Rent {r['code']} — {r['client']}", _add(m, o["lag"]) if o["lag"] else m, monthly, f"#/rental/{r['id']}")
    proj, bal, done_in = [], max(owed_him, 0.0), ""
    for m in months:
        coll = round(sum(s["by"].get(m, 0.0) for s in streams.values()), 2)
        to_him = round(max(0.0, min(bal, (pct or 0) * coll)), 2)
        opening, bal = bal, round(bal - to_him, 2)
        if not done_in and opening > 0.005 and bal <= 0.005:
            done_in = m
        proj.append({"month": m, "in": coll, "opening": opening, "to_funder": to_him, "closing": bal, "partners": round(coll - to_him, 2),
                     "by": {k: s["by"].get(m, 0.0) for k, s in streams.items()}})
    return {"partner": name, "pct": pct, "owed": owed_him, "date_to": date_to, "options": o,
            "history": hist, "paid": paid, "share": round(paid * (pct or 0), 2), "repaid": repaid, "other_repaid": other_back,
            "due_now": due_now, "streams": [{k: v for k, v in s.items() if k != "by"} for s in streams.values()],
            "contracts": contracts, "rows": proj, "repaid_in": done_in, "left_at_end": bal,
            "total_to_funder": round(sum(r["to_funder"] for r in proj), 2), "total_partners": round(sum(r["partners"] for r in proj), 2)}


def table(db, date_to=None, lang="en"):
    """The schedule as a report (screen print / PDF / Excel / Word)."""
    from .reports import _row
    ar = lang == "ar"
    T = lambda en, a: a if ar else en  # noqa: E731
    d = build(db, date_to)
    blank = lambda n: [""] * n  # noqa: E731
    rows = [_row(T("Money expected in, month by month", "التحصيل المتوقع شهرياً"), blank(5), "section")]
    for r in d["rows"]:
        rows.append(_row(r["month"], [r["in"], r["opening"], r["to_funder"], r["closing"], r["partners"]]))
    rows.append(_row(T("TOTAL", "الإجمالي"), [round(sum(r["in"] for r in d["rows"]), 2), "", d["total_to_funder"], d["left_at_end"],
                                             d["total_partners"]], "total"))
    rows.append(_row(T(f"Repaid in full in {d['repaid_in']}" if d["repaid_in"] else
                       f"Still owed to him at the end: {d['left_at_end']:,.2f}",
                       f"يُرد بالكامل في {d['repaid_in']}" if d["repaid_in"] else f"المتبقي له في النهاية: {d['left_at_end']:,.2f}"),
                     blank(5), "note"))
    rows.append(_row(T("So far", "ما تم حتى تاريخه"), blank(5), "section"))
    for h in d["history"]:
        rows.append(_row(f"{h['date']}  {h['memo'][:60]}", [h["payment"], "", h["share"], h["repaid"], round(h["share"] - h["repaid"], 2)]))
    rows.append(_row(T("TOTAL", "الإجمالي"), [d["paid"], "", d["share"], d["repaid"], round(d["share"] - d["repaid"], 2)], "total"))
    return {"title": T("Funding partner repayment schedule", "جدول سداد الشريك الممول"),
            "subtitle": T(f"{d['partner']} · {(d['pct'] or 0):.0%} of each client payment · owed to him {d['owed']:,.2f} on {d['date_to']}",
                          f"{d['partner']} · {(d['pct'] or 0):.0%} من كل دفعة عميل · المستحق له {d['owed']:,.2f} في {d['date_to']}"),
            "columns": [{"label": T("Month / payment", "الشهر / الدفعة"), "align": "left"},
                        {"label": T("Clients pay", "تحصيل العملاء"), "align": "right", "fmt": "money"},
                        {"label": T("Owed to him (start)", "المستحق له أول الشهر"), "align": "right", "fmt": "money"},
                        {"label": T("To him", "يُرد له"), "align": "right", "fmt": "money"},
                        {"label": T("Owed to him (end) / repaid", "المستحق له آخر الشهر / رُد له"), "align": "right", "fmt": "money"},
                        {"label": T("Partners' part / difference", "نصيب الشركاء / الفرق"), "align": "right", "fmt": "money"}],
            "rows": rows, "lang": lang, "data": d}
