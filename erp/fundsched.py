"""The funding partners' repayment schedule: what clients paid and a funding partner's share of each so far, then month
by month what each funding partner is expected to get back from the money still to come in, until his money in the
company is repaid.

The main funding partner's share of each client payment is the distribution policy's `funding_repay_pct`; each other
funding partner has his own (Partner settlement → Other funding partners). A funding partner's money in the company is
the balance of his own funding account. Money expected in: what customers owe today (collected `lag` months from now),
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


def history(db, date_to, account=None):
    """Each client payment, and what was paid back to a funding partner (debits on his account) on each day.
    account: his account (default: the main funding partner's)."""
    pays = db.q(f"""SELECT e.id, e.date, e.memo, e.party, SUM(l.credit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                    JOIN accounts a ON a.code=l.account WHERE {acc.LIVE} AND e.date<=? AND e.kind='Receipt Voucher'
                    AND (a.subtype='receivable' OR a.role='client_advances') AND l.credit>0 GROUP BY e.id ORDER BY e.date, e.id""",
                 (date_to,))
    back = {}
    where, args = ("a.code=?", (date_to, account)) if account else ("a.role='funding_partner'", (date_to,))
    for r in db.q(f"""SELECT e.date, SUM(l.debit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                      JOIN accounts a ON a.code=l.account WHERE {acc.LIVE} AND e.date<=? AND {where} AND l.debit>0
                      GROUP BY e.date""", args):
        back[r["date"]] = to_major(r["n"])
    return pays, back


def _funders(db, date_to):
    """Every funding partner with an account: the main one (his share is the distribution policy's) and the others
    (each with his own share), what the company owes each today."""
    from . import funders as fd
    out = []
    for f in fd.all_funders(db):
        if not f.get("account"):
            continue
        out.append({"name": f["name"], "account": f["account"], "main": f["main"], "pct": f["repay_pct"],
                    "interest_pct": f.get("interest_pct"), "since": fd.first_funded(db, f, date_to),
                    "owed": round(fd.balance(db, f, date_to), 2)})
    return out


def build(db, date_to=None, opts=None, funder=None):
    """The schedule of one funding partner (`funder`: his name; default the main one, else the first), with every
    funding partner repaid side by side month by month — each takes his own share of what clients pay until his money
    is back, and the partners' part is what is left after all of them."""
    from . import cashplan, rentals
    date_to = acc.clean_date(date_to) if date_to else acc.today()
    o = clean({**load(db), **(opts or {})})
    fl = _funders(db, date_to)
    sel = next((f for f in fl if funder and f["name"].lower() == str(funder).strip().lower()), None) or (fl[0] if fl else None)
    name, pct = (sel["name"], sel["pct"]) if sel else ("", None)
    since = sel["since"] if sel else None
    owed_him = sel["owed"] if sel else 0.0
    rows = acc.account_totals(db, date_to=date_to)
    hist, used, back, other_back, paid, repaid, due_now = [], set(), {}, 0.0, 0.0, 0.0, 0.0
    if sel:
        pays, back = history(db, date_to, sel["account"])
        for p in pays:
            if not since or p["date"] < since:
                continue                         # before he put any money in: nothing of it is his
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
    # every funding partner with a share of client payments is repaid at the same time, each from his own share
    payers = [f for f in fl if f["pct"]]
    # his deal's interest (if any) is added each month on what is still owed to him — a projection, booked only with
    # Partner transaction → Funding partner interest
    bal = {f["name"]: max(f["owed"], 0.0) for f in payers}
    done = {f["name"]: "" for f in payers}
    proj, interest = [], {f["name"]: 0.0 for f in payers}
    for m in months:
        coll = round(sum(s["by"].get(m, 0.0) for s in streams.values()), 2)
        to, intr = {}, {}
        for f in payers:
            n = f["name"]
            intr[n] = round(bal[n] * (f.get("interest_pct") or 0) / 12, 2) if bal[n] > 0.005 else 0.0
            interest[n] += intr[n]
            opening = round(bal[n] + intr[n], 2)
            to[n] = round(max(0.0, min(opening, f["pct"] * coll)), 2)
            bal[n] = round(opening - to[n], 2)
            if not done[n] and opening > 0.005 and bal[n] <= 0.005:
                done[n] = m
        row = {"month": m, "in": coll, "to": to, "interest": intr, "partners": round(coll - sum(to.values()), 2),
               "by": {k: s["by"].get(m, 0.0) for k, s in streams.items()}}
        if name in to:
            row.update(opening=round(bal[name] + to[name], 2), to_funder=to[name], closing=bal[name], interest_due=intr[name])
        else:
            row.update(opening=max(owed_him, 0.0), to_funder=0.0, closing=max(owed_him, 0.0))
        proj.append(row)
    summary = [{**f, "repaid_in": done.get(f["name"], ""), "left_at_end": bal.get(f["name"], max(f["owed"], 0.0)),
                "total_to": round(sum(r["to"].get(f["name"], 0.0) for r in proj), 2),
                "interest_ahead": round(interest.get(f["name"], 0.0), 2)} for f in fl]
    return {"partner": name, "pct": pct, "owed": owed_him, "date_to": date_to, "options": o, "since": since,
            "interest_pct": sel.get("interest_pct") if sel else None, "interest_ahead": round(interest.get(name, 0.0), 2),
            "funders": summary, "payers": [f["name"] for f in payers],
            "history": hist, "paid": paid, "share": round(paid * (pct or 0), 2), "repaid": repaid, "other_repaid": other_back,
            "due_now": due_now, "streams": [{k: v for k, v in s.items() if k != "by"} for s in streams.values()],
            "contracts": contracts, "rows": proj, "repaid_in": done.get(name, ""), "left_at_end": proj[-1]["closing"] if proj else max(owed_him, 0.0),
            "total_to_funder": round(sum(r["to_funder"] for r in proj), 2), "total_partners": round(sum(r["partners"] for r in proj), 2)}


def table(db, date_to=None, lang="en", funder=None):
    """The schedule as a report (screen print / PDF / Excel / Word)."""
    from .reports import _row
    ar = lang == "ar"
    T = lambda en, a: a if ar else en  # noqa: E731
    d = build(db, date_to, funder=funder)
    blank = lambda n: [""] * n  # noqa: E731
    rows = []
    if len(d["funders"]) > 1:      # every funding partner, side by side
        rows.append(_row(T("Funding partners", "الشركاء الممولون"), blank(5), "section"))
        for f in d["funders"]:
            when = (T(f"repaid in {f['repaid_in']}", f"يُرد في {f['repaid_in']}") if f["repaid_in"] else
                    T("no share of client payments set", "لم يُحدد نصيب من دفعات العملاء") if not f["pct"] else T("not repaid in this period", "لا يُرد خلال الفترة"))
            rows.append(_row(f"{f['name']} · {(f['pct'] or 0):.0%} · {when}", ["", max(f["owed"], 0.0), f["total_to"], f["left_at_end"], ""]))
    rows.append(_row(T("Money expected in, month by month", "التحصيل المتوقع شهرياً"), blank(5), "section"))
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
