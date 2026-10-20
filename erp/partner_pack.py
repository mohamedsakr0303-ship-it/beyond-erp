"""Partner Pack — the management accounts the partners read, as in the BEYOND V1.4 workbook.

Five parts: at a glance & project results, income statement (earned and cash side by side), balance sheet,
cash flow, statement of partners' equity.

The idea it is built on: profit is only distributable once it is BILLED.
  * Work in progress (WIP) = work done but not invoiced yet = the unbilled-revenue account (1180), per project.
  * A project's billed profit (the V1.4 rule):  no revenue -> its net result;  a loss -> the whole loss at once;
    otherwise net profit x (revenue - WIP) / revenue.  The rest is profit held in work in progress.
  * Cash basis: a revenue or cost line counts as cash for the part settled by cash / bank in the same entry
    (money that moved) — the V1.4 "cash Dr / Cr in JE" columns; collecting an invoice (receivable) counts as revenue received.
"""
import datetime as _dt

from . import accounting as acc
from .db import to_major

OPEX = ("employee", "general_admin", "marketing", "utilities", "other_operating")


def wip_accounts(db):
    rows = [r["code"] for r in db.q("SELECT code FROM accounts WHERE role='unbilled_revenue'")]
    if not rows and db.one("SELECT 1 FROM accounts WHERE code='1180'"):
        rows = ["1180"]
    return rows


def _bal(rows, pred, sign=1):
    return to_major(sign * sum(r["net"] for r in rows if pred(r)))


def cash_basis(db, date_from=None, date_to=None):
    """Revenue and costs by statement line, counting only what was paid / received in the same entry."""
    w, p = acc._where(date_from=date_from, date_to=date_to)
    lines = db.q(f"""SELECT e.id, e.bu_id, a.type, a.subtype, l.debit, l.credit FROM journal_lines l
                     JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account WHERE {w}""", p)
    by_entry = {}
    for ln in lines:
        by_entry.setdefault(ln["id"], []).append(ln)
    out = {"revenue": 0.0, "cogs": 0.0, **{s: 0.0 for s in OPEX}, "other": 0.0, "by_bu": {}}
    for ls in by_entry.values():
        cash = sum(x["debit"] - x["credit"] for x in ls if x["subtype"] == "cash")
        # revenue received = revenue credited with cash in the same entry, or an invoice (receivable) being collected
        rev = sum(x["credit"] - x["debit"] for x in ls if x["type"] == "Revenue") + \
            max(0, sum(x["credit"] - x["debit"] for x in ls if x["subtype"] == "receivable"))
        cost = [x for x in ls if x["type"] in ("COGS", "Expense")]
        ctot = sum(x["debit"] - x["credit"] for x in cost)
        if cash > 0 and rev > 0:
            f = min(1.0, cash / rev)
            out["revenue"] += rev * f
            bu = ls[0]["bu_id"]
            out["by_bu"][bu] = out["by_bu"].get(bu, 0) + rev * f
        if cash < 0 and ctot > 0:
            f = min(1.0, -cash / ctot)
            for x in cost:
                k = "cogs" if x["type"] == "COGS" else (x["subtype"] if x["subtype"] in OPEX else "other")
                out[k] += (x["debit"] - x["credit"]) * f
    res = {k: to_major(round(v)) for k, v in out.items() if k != "by_bu"}
    res["by_bu"] = {k: to_major(round(v)) for k, v in out["by_bu"].items()}
    return res


POLICY = {"reserve_pct": 0.4, "reserve_early_pct": None, "reserve_early_until": 0.0, "capital_goal": 0.0,
          "buffer_months": 3.0, "repay_funder_first": True}


def policy(db):
    """The company's profit distribution policy (Settings → Partners): reserve rates, capital goal, cash buffer."""
    def f(k, d):
        v = db.setting(k)
        try:
            return float(v) if v not in (None, "") else d
        except ValueError:
            return d
    return {"reserve_pct": f("reserve_pct", POLICY["reserve_pct"]), "reserve_early_pct": f("reserve_early_pct", None),
            "reserve_early_until": f("reserve_early_until", 0.0), "capital_goal": f("capital_goal", 0.0),
            "buffer_months": f("buffer_months", POLICY["buffer_months"]),
            "repay_funder_first": (db.setting("repay_funder_first") or "1") not in ("0", "no", "false")}


def reserve_for(clean, pol):
    """Company capital reserve kept from clean profit: the early rate until the reserve reaches its target, then the
    base rate on the rest."""
    if clean <= 0:
        return 0.0
    base, early, until = pol["reserve_pct"] or 0, pol["reserve_early_pct"], pol["reserve_early_until"] or 0
    if not early or not until:
        return round(clean * base, 2)
    if clean * early <= until:
        return round(clean * early, 2)
    return round(until + (clean - until / early) * base, 2)


def distributable(db, pol, rows, bs, earned, held, reserve_amt, partners, date_to):
    """How much can safely be paid to partners now: the company's own cash less what it owes, client advances, money
    held for remaining project costs, a buffer of months of overhead and the capital reserve — and never more than the
    clean profit still owed to partners. With "repay the funding partner first", nothing until his money is repaid."""
    first = db.one(f"SELECT MIN(date) a, MAX(date) b FROM journal_entries e WHERE {acc.LIVE} AND date<=?", (date_to,))
    days = (_dt.date.fromisoformat(first["b"]) - _dt.date.fromisoformat(first["a"])).days if first["a"] else 0
    monthly = round(earned["opex"] / max(1.0, days / 30.4), 2) if earned.get("opex") else 0.0
    buffer = round(monthly * (pol["buffer_months"] or 0), 2)
    owed = round(bs["payable"] + bs["accrued"], 2)
    funder = round(-sum(r["net"] for r in rows if r.get("role") == "funding_partner") / 100, 2)
    # when the funding partner's account replaces the bank, a debit balance there is the company's own money with him
    cash = round(bs["cash"] + max(0.0, -funder), 2)
    free = round(cash - owed - bs["advances"] - held - buffer - reserve_amt, 2)
    to_partners = round(sum(max(0.0, x["undistributed"]) for x in partners), 2)
    ahead = round(sum(x["undistributed"] for x in partners), 2)
    hold_funder = pol["repay_funder_first"] and funder > 0.005
    safe = 0.0 if hold_funder else round(max(0.0, min(free, to_partners)), 2)
    if free < 0:
        v = (f"DO NOT DISTRIBUTE — own cash short by {-free:,.0f}", f"لا يُنصح بالتوزيع — النقدية الذاتية أقل بمقدار {-free:,.0f}")
    elif hold_funder:
        v = (f"DO NOT DISTRIBUTE — repay the funding partner first ({funder:,.0f} of his money is in the company)",
             f"لا يُنصح بالتوزيع — يُسدد للشريك الممول أولاً ({funder:,.0f} من أمواله في الشركة)")
    elif to_partners <= 0:
        v = ((f"DO NOT DISTRIBUTE — partners are already ahead of their earned share by {-ahead:,.0f}",
              f"لا يُنصح بالتوزيع — حصل الشركاء على أكثر من نصيبهم المستحق بمقدار {-ahead:,.0f}") if ahead < 0 else
             ("DO NOT DISTRIBUTE — no clean profit left to distribute", "لا يُنصح بالتوزيع — لا يوجد ربح صافٍ متبقٍ للتوزيع"))
    else:
        lim = safe < to_partners
        v = (f"OK to distribute up to {safe:,.0f}" + (" (limited by cash)" if lim else " (limited by clean profit)"),
             f"يمكن التوزيع حتى {safe:,.0f}" + (" (محدود بالنقدية)" if lim else " (محدود بالربح الصافي)"))
    set_aside = round(sum(r["net"] for r in rows if r.get("role") == "reserve_cash") / 100, 2)
    goal = pol["capital_goal"] or 0
    return {"cash": cash, "owed": owed, "advances": bs["advances"], "held": held, "monthly_overhead": monthly,
            "buffer": buffer, "reserve": reserve_amt, "free": free, "owed_to_partners": to_partners, "funder_money": funder,
            "max_safe": safe, "verdict": v, "capital_goal": goal, "progress": round(min(1, reserve_amt / goal), 4) if goal else None,
            "set_aside": set_aside, "not_backed": round(max(0.0, reserve_amt - set_aside), 2),
            "months_covered": round(reserve_amt / monthly, 2) if monthly else None}


def billed_profit(revenue, wip, net):
    if not revenue:
        return net
    if net < 0:
        return net
    return round(net * (revenue - wip) / revenue, 2)


def compute(db, date_from=None, date_to=None):
    date_to = acc.clean_date(date_to) if date_to else acc.today()
    wip_codes = set(wip_accounts(db))
    rows = acc.account_totals(db, date_to=date_to)
    mov = acc.account_totals(db, date_from=date_from, date_to=date_to) if date_from else rows
    custody = {r["code"] for r in db.q("SELECT code FROM accounts WHERE role='custody'")}

    # ---- projects
    w, p = acc._where(date_to=date_to, date_from=date_from)
    wip_bu = {r["bu_id"]: r["n"] for r in db.q(
        f"""SELECT e.bu_id, SUM(l.debit-l.credit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
            WHERE {w} AND l.account IN ({','.join('?' * len(wip_codes)) or "''"}) GROUP BY e.bu_id""", p + list(wip_codes))}
    cb = cash_basis(db, date_from, date_to)
    projects = []
    for b in acc.bu_summaries(db, date_from, date_to):
        wip = to_major(wip_bu.get(b["id"], 0))
        cost = round(b["cogs"] + b["direct_overhead"] + b["allocated_overhead"], 2)
        billed = billed_profit(b["revenue"], wip, b["net_profit"])
        # money received that the project still needs for its remaining costs is not profit yet
        held = round(min(billed, b["cost_still_to_come"]), 2) if billed > 0 else 0.0
        projects.append({"code": b["code"], "name": b["name"], "basis": b["basis"], "revenue": b["revenue"],
                         "billed_revenue": round(b["revenue"] - wip, 2), "wip": wip, "cost": cost,
                         "net_profit": b["net_profit"], "billed_profit": billed, "held": held,
                         "clean_profit": round(billed - held, 2), "still_to_come": b["cost_still_to_come"],
                         "pct_complete": b["pct_complete"],
                         "wip_profit": round(b["net_profit"] - billed, 2), "collected": b["collected"],
                         "allocated": b["allocated_overhead"]})
    inc = acc.income_statement(db, date_from, date_to)
    unit_net = round(sum(x["net_profit"] for x in projects), 2)
    company_left = round(inc["net_profit"] - unit_net, 2)           # overhead not charged to any project
    billed_total = round(sum(x["billed_profit"] for x in projects) + company_left, 2)
    held_total = round(sum(x["held"] for x in projects), 2)
    clean_total = round(billed_total - held_total, 2)
    wip_profit = round(inc["net_profit"] - billed_total, 2)

    # ---- income statement, earned vs cash
    def pl(sub, src=mov):
        return _bal(src, lambda r: r["subtype"] in sub)
    earned = {"revenue": round(_bal(mov, lambda r: r["subtype"] in ("operating_revenue", "sales_discount"), -1), 2),
              "cogs": _bal(mov, lambda r: r["type"] == "COGS"), "employee": pl(("employee",)),
              "general_admin": pl(("general_admin", "marketing", "utilities")), "other_operating": pl(("other_operating",)),
              "dep": pl(("depreciation", "amortization")),
              "interest": round(pl(("interest", "other_nonoperating")) + _bal(mov, lambda r: r["subtype"] == "other_income"), 2),
              "tax": pl(("income_tax",))}
    cash = {"revenue": cb["revenue"], "cogs": cb["cogs"], "employee": cb["employee"],
            "general_admin": round(cb["general_admin"] + cb["marketing"] + cb["utilities"], 2),
            "other_operating": round(cb["other_operating"] + cb["other"], 2), "dep": 0.0, "interest": 0.0, "tax": 0.0}
    for d in (earned, cash):
        d["gross"] = round(d["revenue"] - d["cogs"], 2)
        d["opex"] = round(d["employee"] + d["general_admin"] + d["other_operating"], 2)
        d["ebitda"] = round(d["gross"] - d["opex"], 2)
        d["pbt"] = round(d["ebitda"] - d["dep"] - d["interest"], 2)
        d["net"] = round(d["pbt"] - d["tax"], 2)
        d["gross_margin"] = round(d["gross"] / d["revenue"], 4) if d["revenue"] else 0
        d["net_margin"] = round(d["net"] / d["revenue"], 4) if d["revenue"] else 0

    # ---- balance sheet
    S = lambda *subs: _bal(rows, lambda r: r["subtype"] in subs)  # noqa: E731
    L = lambda *subs: _bal(rows, lambda r: r["subtype"] in subs, -1)  # noqa: E731
    bs = {"cash": S("cash"), "receivable": S("receivable", "allowance_doubtful"),
          "wip": _bal(rows, lambda r: r["code"] in wip_codes), "inventory": S("inventory"), "prepaid": S("prepaid"),
          "custody": _bal(rows, lambda r: r["code"] in custody),
          "other_ca": _bal(rows, lambda r: r["subtype"] == "other_current_asset" and r["code"] not in wip_codes | custody),
          "equipment": S("fixed_asset"), "accum": S("accumulated_depreciation"),
          "other_nca": S("intangible", "accumulated_amortization", "investment", "other_noncurrent_asset"),
          "payable": L("payable"), "accrued": L("accrued"), "advances": L("deferred_revenue"), "taxes": L("tax_payable"),
          "loans": L("short_term_debt"), "other_cl": L("other_current_liability", "dividends_payable"),
          "ncl": L("long_term_debt", "other_noncurrent_liability"),
          "capital": L("capital", "partner_current"), "reserves": L("reserves", "retained_earnings", "other_equity"),
          "drawings": S("drawings")}
    bs["profit_to_date"] = round(-sum(r["net"] for r in rows if r["type"] in ("Revenue", "COGS", "Expense")) / 100, 2)
    bs["tca"] = round(sum(bs[k] for k in ("cash", "receivable", "wip", "inventory", "prepaid", "custody", "other_ca")), 2)
    bs["tnca"] = round(bs["equipment"] + bs["accum"] + bs["other_nca"], 2)
    bs["ta"] = round(bs["tca"] + bs["tnca"], 2)
    bs["tcl"] = round(sum(bs[k] for k in ("payable", "accrued", "advances", "taxes", "loans", "other_cl")), 2)
    bs["tl"] = round(bs["tcl"] + bs["ncl"], 2)
    bs["te"] = round(bs["capital"] + bs["reserves"] + bs["profit_to_date"] - bs["drawings"], 2)
    bs["tle"] = round(bs["tl"] + bs["te"], 2)
    bs["balanced"] = abs(bs["ta"] - bs["tle"]) < 0.01

    # ---- cash flow (indirect, from the period's movements)
    M = lambda pred, sign=1: _bal(mov, pred, sign)  # noqa: E731
    sub = lambda *s: (lambda r: r["subtype"] in s)  # noqa: E731
    cf = {"net": earned["net"], "dep": round(M(sub("accumulated_depreciation", "accumulated_amortization"), -1), 2),
          "receivables": -M(lambda r: r["subtype"] in ("receivable", "allowance_doubtful") or r["code"] in wip_codes),
          "inventory": -M(sub("inventory")), "advances_out": -M(lambda r: r["subtype"] == "prepaid" or r["code"] in custody
                                                                 or (r["subtype"] == "other_current_asset" and r["code"] not in wip_codes)),
          "payable": M(sub("payable"), -1), "accrued": M(sub("accrued"), -1), "client_adv": M(sub("deferred_revenue"), -1),
          "taxes": M(sub("tax_payable", "other_current_liability", "dividends_payable"), -1),
          "equipment": -M(sub("fixed_asset", "intangible", "investment", "other_noncurrent_asset")),
          "loans": M(sub("short_term_debt", "long_term_debt", "other_noncurrent_liability"), -1),
          "capital_in": M(sub("capital", "partner_current", "reserves", "retained_earnings", "other_equity"), -1),
          "distributions": -M(sub("drawings"))}
    cf["ops"] = round(sum(cf[k] for k in ("net", "dep", "receivables", "inventory", "advances_out", "payable", "accrued",
                                          "client_adv", "taxes")), 2)
    cf["inv"] = round(cf["equipment"], 2)
    cf["partners"] = round(cf["capital_in"] + cf["distributions"], 2)
    cf["change"] = round(cf["ops"] + cf["inv"] + cf["loans"] + cf["partners"], 2)
    opening = acc.account_totals(db, date_to=(_dt.date.fromisoformat(date_from) - _dt.timedelta(days=1)).isoformat()) if date_from else []
    cf["opening"] = _bal(opening, sub("cash"))
    cf["closing"] = round(cf["opening"] + cf["change"], 2)
    cf["ties"] = abs(cf["closing"] - bs["cash"]) < 0.01

    # ---- partners: capital, share of billed profit (per project ownership, after the reserve), distributions
    pol = policy(db)
    reserve_amt = reserve_for(clean_total, pol)
    reserve = round(reserve_amt / clean_total, 6) if clean_total > 0 else pol["reserve_pct"]    # effective rate
    shares = {(r["partner_id"], r["bu_id"]): r["share"] for r in db.q("SELECT * FROM partner_shares")}
    bus = {b["code"]: b["id"] for b in db.q("SELECT id, code FROM business_units")}
    from . import equity
    partners = []
    for pt in db.q("SELECT * FROM partners WHERE active=1 OR active IS NULL ORDER BY id"):
        share = sum(x["clean_profit"] * shares.get((pt["id"], bus.get(x["code"])), pt["default_share"] or 0) for x in projects)
        share += company_left * (pt["default_share"] or 0)
        share = round(share * (1 - reserve) if share > 0 else share, 2)
        b = equity.balances(db, pt["name"], date_to)
        partners.append({"name": pt["name"], "capital": round(b["capital"] + b["current"], 2), "share": share,
                         "distributions": round(b["drawings"], 2)})
    for x in partners:
        x["closing"] = round(x["capital"] + x["share"] - x["distributions"], 2)
        x["undistributed"] = round(x["share"] - x["distributions"], 2)
    kept = round(clean_total - sum(x["share"] for x in partners), 2)
    capital_unnamed = round(bs["capital"] - sum(x["capital"] for x in partners), 2)
    drawn_unnamed = round(bs["drawings"] - sum(x["distributions"] for x in partners), 2)
    closing_total = round(sum(x["closing"] for x in partners) + kept + capital_unnamed - drawn_unnamed, 2)
    total_eq = closing_total if closing_total else 0
    for x in partners:
        x["pct"] = round(x["closing"] / total_eq, 4) if total_eq else 0
    cp = acc.cash_position(db)
    dist = distributable(db, pol, rows, bs, earned, held_total, reserve_amt, partners, date_to)
    audit = acc.audit(db)
    return {"date_from": date_from, "date_to": date_to, "projects": projects, "company_left": company_left,
            "billed_total": billed_total, "wip_profit": wip_profit, "earned": earned, "cash": cash, "bs": bs, "cf": cf,
            "partners": partners, "kept": kept, "capital_unnamed": capital_unnamed, "drawn_unnamed": drawn_unnamed,
            "closing_total": closing_total, "reserve_pct": reserve, "cash_position": cp, "verdict": dist["verdict"][0],
            "verdict_ar": dist["verdict"][1], "dist": dist, "policy": pol, "held_total": held_total, "clean_total": clean_total,
            "reserve_amount": reserve_amt,
            "audit_ok": all(c["ok"] for c in audit), "audit_fails": [c["check"] for c in audit if not c["ok"]]}


# ---------------------------------------------------------------- the report (screen / PDF / Excel / Word)
def table(db, date_from=None, date_to=None, lang="en"):
    from .reports import _row
    d = compute(db, date_from, date_to)
    ar = lang == "ar"
    T = lambda en, a: a if ar else en  # noqa: E731
    n = 7
    pad = lambda vals: list(vals) + [""] * (n - 1 - len(vals))  # noqa: E731
    R = lambda label, *vals, style="item", indent=0: _row(label, pad(vals), style, indent)  # noqa: E731
    H = lambda label, *heads: _row(label, pad(heads), "header")  # noqa: E731
    sec = lambda label: _row(label, pad([]), "section")  # noqa: E731
    note = lambda text: _row(text, pad([]), "note")  # noqa: E731
    e, c, bs, cf = d["earned"], d["cash"], d["bs"], d["cf"]
    rows = [sec(T("1. At a glance", "١. نظرة سريعة")),
            R(T("Clean net profit (what partners can distribute from)", "صافي الربح الصافي (أساس التوزيع)"), d["clean_total"], style="total"),
            R(T("Net profit — billed", "صافي الربح المفوتر"), d["billed_total"]),
            R(T("Held for remaining project costs (received, still needed by the projects)", "محتجز لتكاليف المشروعات المتبقية"), d["held_total"]),
            R(T("Work in progress — work done, not yet invoiced", "أعمال تحت التنفيذ — منفذة ولم تُفوتر"), bs["wip"]),
            R(T("Expected profit on that work (NOT distributable until invoiced)", "الربح المتوقع عليها (غير قابل للتوزيع قبل الفوترة)"), d["wip_profit"]),
            R(T("Net profit — cash actually received", "صافي الربح النقدي"), c["net"]),
            R(T("Cash & bank", "النقدية والبنوك"), bs["cash"]),
            R(T("Clients — invoiced, not yet collected", "العملاء — مفوتر ولم يُحصّل"), bs["receivable"]),
            R(T("Owed by the company (suppliers & staff)", "مستحق على الشركة (موردون وعاملون)"), round(bs["payable"] + bs["accrued"], 2)),
            R(T("Client advances held (work not yet done)", "دفعات مقدمة من العملاء"), bs["advances"])]
    if bs["loans"] or bs["ncl"]:
        rows.append(R(T("Loans owed", "قروض مستحقة"), round(bs["loans"] + bs["ncl"], 2)))
    rows += [R(T("Partner capital — funding", "رأس مال / تمويل الشركاء"), bs["capital"]),
             R(T("Distribution recommendation", "توصية التوزيع"), d["verdict_ar"] if ar else d["verdict"]),
             R(T("Books integrity (Model Audit)", "سلامة الدفاتر"), T("ALL INTEGRITY CHECKS PASS", "كل الفحوصات سليمة") if d["audit_ok"]
               else T("REVIEW: ", "مراجعة: ") + ", ".join(d["audit_fails"])),
             sec(T("Project results", "نتائج المشروعات")),
             H(T("Project", "المشروع"), T("Billing basis", "أساس الفوترة"), T("Revenue billed", "الإيراد المفوتر"), T("Work in progress", "تحت التنفيذ"),
               T("Total cost", "إجمالي التكلفة"), T("Net profit — billed", "الربح المفوتر"), T("Collected (cash)", "المحصّل"))]
    for x in d["projects"]:
        rows.append(R(f"{x['code']}  ·  {x['name']}", x["basis"], x["billed_revenue"], x["wip"], x["cost"], x["billed_profit"], x["collected"]))
    rows.append(R(T("Company overhead not charged to any project", "مصروفات الشركة غير المحمّلة على مشروع"), "", "", "", round(-d["company_left"], 2), d["company_left"]))
    rows.append(R(T("TOTAL", "الإجمالي"), "", round(sum(x["billed_revenue"] for x in d["projects"]), 2), bs["wip"] if bs["wip"] else
                  round(sum(x["wip"] for x in d["projects"]), 2), round(sum(x["cost"] for x in d["projects"]) - d["company_left"], 2),
                  d["billed_total"], round(sum(x["collected"] for x in d["projects"]), 2), style="total"))
    rows.append(note(T("Billed = invoiced to the client (or settled). Work in progress = work done but not invoiced yet: its revenue and profit "
                       "are held back until the invoice is issued, so a project in progress shows no billed profit. A project heading for a "
                       "loss shows that loss at once. Collected = cash actually received.",
                       "المفوتر = ما صدرت به فاتورة. تحت التنفيذ = أعمال منفذة لم تُفوتر بعد، يُحجز ربحها حتى الفوترة؛ والخسارة تظهر فوراً.")))
    # 2. income statement
    if any(x["held"] or x["still_to_come"] for x in d["projects"]):
        rows += [sec(T("Costs still to come on running projects", "التكاليف المتبقية على المشروعات الجارية")),
                 H(T("Project", "المشروع"), T("Total cost to date", "التكلفة حتى تاريخه"), T("Cost still to come", "التكلفة المتبقية"),
                   T("% complete (by cost)", "نسبة الإنجاز (بالتكلفة)"), T("Net profit — billed", "الربح المفوتر"),
                   T("Held for remaining costs", "المحتجز للتكاليف المتبقية"), T("Clean profit", "الربح الصافي"))]
        for x in d["projects"]:
            rows.append(R(f"{x['code']}  ·  {x['name']}", x["cost"], x["still_to_come"], x["pct_complete"] if x["pct_complete"] is not None else "",
                          x["billed_profit"], x["held"], x["clean_profit"]))
        rows.append(note(T("Money received that a project still needs for its remaining costs (expected cost less cost to date) is held "
                           "back from profit until the project is complete.",
                           "الأموال المحصلة التي يحتاجها المشروع لتكاليفه المتبقية (التكلفة المتوقعة ناقص التكلفة حتى تاريخه) تُحتجز من الربح حتى اكتمال المشروع.")))
    rows += [sec(T("2. Income statement — earned and cash side by side", "٢. قائمة الدخل — الاستحقاق والنقدي")),
             H("", T("Earned (accrual)", "الاستحقاق"), T("Cash basis", "النقدي")),
             R(T("Revenue earned from projects", "الإيرادات"), e["revenue"], c["revenue"]),
             R(T("Direct project costs (materials, subcontractors, site labour)", "التكاليف المباشرة"), -e["cogs"], -c["cogs"]),
             R(T("GROSS PROFIT", "مجمل الربح"), e["gross"], c["gross"], style="subtotal"),
             R(T("Gross margin", "هامش مجمل الربح"), e["gross_margin"], c["gross_margin"], style="note"),
             R(T("Staff salaries (office)", "رواتب إدارية"), -e["employee"], -c["employee"], indent=1),
             R(T("General & administrative", "مصروفات عمومية وإدارية"), -e["general_admin"], -c["general_admin"], indent=1),
             R(T("Other operating expenses", "مصروفات تشغيلية أخرى"), -e["other_operating"], -c["other_operating"], indent=1),
             R(T("Total operating expenses", "إجمالي المصروفات التشغيلية"), e["opex"], c["opex"], style="subtotal"),
             R("EBITDA", e["ebitda"], c["ebitda"], style="total"),
             R(T("Depreciation & amortisation", "الإهلاك والاستهلاك"), -e["dep"], -c["dep"]),
             R(T("Interest & non-operating (net of other income)", "الفوائد وغير التشغيلي (صافي)"), -e["interest"], -c["interest"]),
             R(T("PROFIT BEFORE TAX", "الربح قبل الضرائب"), e["pbt"], c["pbt"], style="subtotal"),
             R(T("Income tax", "ضريبة الدخل"), -e["tax"], -c["tax"]),
             R(T("NET PROFIT", "صافي الربح"), e["net"], c["net"], style="grand"),
             R(T("Net margin", "هامش صافي الربح"), e["net_margin"], c["net_margin"], style="note"),
             R(T("Of which: profit on work in progress, not yet billed (not distributable)", "منه: ربح أعمال تحت التنفيذ"), d["wip_profit"]),
             R(T("NET PROFIT — BILLED (distributable base)", "صافي الربح المفوتر (أساس التوزيع)"), d["billed_total"], style="total"),
             note(T(f"Earned basis counts revenue when the work is done and costs when incurred; cash basis counts only money that moved. "
                    f"The {e['net'] - c['net']:,.2f} gap is revenue earned or invoiced but not received in cash, net of costs not yet paid.",
                    "الاستحقاق يسجل الإيراد عند تنفيذ العمل؛ النقدي يسجل ما تحرك فعلاً من نقدية."))]
    # 3. balance sheet
    rows += [sec(T("3. Balance sheet — what the company owns and owes", "٣. الميزانية")), H("", d["date_to"]),
             R(T("Cash & bank", "النقدية والبنوك"), bs["cash"], indent=1),
             R(T("Clients — invoiced, not yet collected", "العملاء"), bs["receivable"], indent=1),
             R(T("Work executed, not yet invoiced (work in progress)", "أعمال منفذة لم تُفوتر"), bs["wip"], indent=1),
             R(T("Materials inventory", "المخزون"), bs["inventory"], indent=1),
             R(T("Advances paid to suppliers", "دفعات مقدمة للموردين"), bs["prepaid"], indent=1),
             R(T("Cash held by staff (custody)", "عهد نقدية"), bs["custody"], indent=1),
             R(T("Other current assets", "أصول متداولة أخرى"), bs["other_ca"], indent=1),
             R(T("Total current assets", "إجمالي الأصول المتداولة"), bs["tca"], style="subtotal"),
             R(T("Equipment at cost", "المعدات بالتكلفة"), bs["equipment"], indent=1),
             R(T("Less: accumulated depreciation", "مجمع الإهلاك"), bs["accum"], indent=1),
             R(T("Other non-current assets", "أصول غير متداولة أخرى"), bs["other_nca"], indent=1),
             R(T("Total non-current assets", "إجمالي الأصول غير المتداولة"), bs["tnca"], style="subtotal"),
             R(T("TOTAL ASSETS", "إجمالي الأصول"), bs["ta"], style="total"),
             R(T("Owed to suppliers & subcontractors", "الموردون ومقاولو الباطن"), bs["payable"], indent=1),
             R(T("Salaries owed", "رواتب مستحقة"), bs["accrued"], indent=1),
             R(T("Client advances — paid before work is done", "دفعات مقدمة من العملاء"), bs["advances"], indent=1),
             R(T("Taxes payable", "ضرائب مستحقة"), bs["taxes"], indent=1),
             R(T("Loans", "القروض"), bs["loans"], indent=1),
             R(T("Other current liabilities", "التزامات متداولة أخرى"), bs["other_cl"], indent=1),
             R(T("Total current liabilities", "إجمالي الالتزامات المتداولة"), bs["tcl"], style="subtotal"),
             R(T("Non-current liabilities", "التزامات غير متداولة"), bs["ncl"], indent=1),
             R(T("TOTAL LIABILITIES", "إجمالي الالتزامات"), bs["tl"], style="total"),
             R(T("Partner capital — funding", "رأس مال الشركاء"), bs["capital"], indent=1),
             R(T("Reserves & retained earnings", "الاحتياطيات والأرباح المحتجزة"), bs["reserves"], indent=1),
             R(T("Profit to date (incl. work in progress)", "الربح حتى تاريخه"), bs["profit_to_date"], indent=1),
             R(T("Less: distributions paid to partners", "ناقص: التوزيعات المدفوعة"), -bs["drawings"], indent=1),
             R(T("TOTAL PARTNERS' EQUITY", "إجمالي حقوق الشركاء"), bs["te"], style="total"),
             R(T("TOTAL LIABILITIES + PARTNERS' EQUITY", "إجمالي الالتزامات وحقوق الشركاء"), bs["tle"], style="grand"),
             note(T("✓ Balanced — total assets equal liabilities plus equity", "✓ متوازنة") if bs["balanced"]
                  else T("✗ NOT balanced — check the Model Audit", "✗ غير متوازنة")),
             R(T("Working capital (current assets − current liabilities)", "رأس المال العامل"), round(bs["tca"] - bs["tcl"], 2)),
             R(T("Current ratio (current assets ÷ current liabilities)", "نسبة التداول"),
               round(bs["tca"] / bs["tcl"], 2) if bs["tcl"] else "", style="item"),
             R(T("Cash after paying what is owed to suppliers & staff", "النقدية بعد سداد المستحقات"), round(bs["cash"] - bs["payable"] - bs["accrued"], 2))]
    # 4. cash flow
    rows += [sec(T("4. Cash flow — where the cash actually went", "٤. التدفقات النقدية")),
             R(T("Net profit (earned basis)", "صافي الربح (استحقاق)"), cf["net"]),
             R(T("Add back: depreciation", "يضاف: الإهلاك"), cf["dep"], indent=1),
             R(T("Less: work done not yet collected (receivables & WIP)", "يخصم: أعمال لم تُحصّل"), cf["receivables"], indent=1),
             R(T("Less: materials bought for stock", "يخصم: مشتريات مخزون"), cf["inventory"], indent=1),
             R(T("Less: advances & custody paid out", "يخصم: دفعات مقدمة وعهد"), cf["advances_out"], indent=1),
             R(T("Add: owed to suppliers, not yet paid", "يضاف: مستحق للموردين"), cf["payable"], indent=1),
             R(T("Add: salaries owed, not yet paid", "يضاف: رواتب مستحقة"), cf["accrued"], indent=1),
             R(T("Add: client advances received", "يضاف: دفعات مقدمة من العملاء"), cf["client_adv"], indent=1),
             R(T("Add: taxes & other liabilities owed", "يضاف: ضرائب والتزامات أخرى"), cf["taxes"], indent=1),
             R(T("Net cash from operations", "صافي النقد من التشغيل"), cf["ops"], style="subtotal"),
             R(T("Equipment purchased", "شراء معدات"), cf["equipment"], indent=1),
             R(T("Net cash from investment", "صافي النقد من الاستثمار"), cf["inv"], style="subtotal"),
             R(T("Loans received / (repaid)", "قروض مستلمة / (مسددة)"), cf["loans"], indent=1),
             R(T("Partner capital & funding put in", "رأس مال وتمويل الشركاء"), cf["capital_in"], indent=1),
             R(T("Distributions paid to partners", "التوزيعات المدفوعة"), cf["distributions"], indent=1),
             R(T("Net cash from financing & partners", "صافي النقد من التمويل والشركاء"), round(cf["loans"] + cf["partners"], 2), style="subtotal"),
             R(T("Opening cash", "النقدية أول المدة"), cf["opening"]), R(T("Net change in cash", "صافي التغير"), cf["change"]),
             R(T("CLOSING CASH", "النقدية آخر المدة"), cf["closing"], style="grand"),
             note(T("✓ Ties to the cash & bank on the balance sheet", "✓ تطابق النقدية في الميزانية") if cf["ties"]
                  else T("✗ Does not tie to the balance sheet cash", "✗ لا تطابق نقدية الميزانية"))]
    # 5. partners' equity
    ps = d["partners"]
    rows += [sec(T("5. Statement of partners' equity", "٥. قائمة حقوق الشركاء"))]
    cols = [x["name"] for x in ps][:5]
    more = len(ps) > 5
    rows.append(H(T("Partner", "الشريك"), *cols, T("TOTAL", "الإجمالي")))
    tot = lambda k: round(sum(x[k] for x in ps), 2)  # noqa: E731
    for label, k, sign in ((T("Capital put in (funding)", "رأس المال / التمويل"), "capital", 1),
                           (T("Share of billed profit", "نصيب الربح المفوتر"), "share", 1),
                           (T("Less: distributions received", "ناقص: التوزيعات"), "distributions", -1)):
        rows.append(R(label, *[sign * x[k] for x in ps[:5]], sign * tot(k)))
    rows.append(R(T("CLOSING EQUITY — billed", "حقوق الملكية — مفوتر"), *[x["closing"] for x in ps[:5]], tot("closing"), style="total"))
    rows.append(R(T("Share of total equity", "النسبة من الإجمالي"), *[x["pct"] for x in ps[:5]], 1 if ps else "", style="note"))
    rows.append(R(T("Billed profit still to be distributed (share − distributions)", "ربح لم يُوزع بعد"), *[x["undistributed"] for x in ps[:5]], tot("undistributed")))
    if more:
        rows.append(note(T(f"{len(ps) - 5} more partners are included in the totals.", "شركاء آخرون مضمنون في الإجمالي.")))
    rows += [R(T("Kept in company (reserve)", "محتجز في الشركة (احتياطي)") + f" {d['reserve_pct']:.0%}", d["kept"]),
             R(T("Held for remaining project costs (not profit yet)", "محتجز لتكاليف المشروعات المتبقية"), d["held_total"]),
             R(T("Profit on work in progress — not billed yet, held until invoiced", "ربح تحت التنفيذ"), d["wip_profit"])]
    if d["capital_unnamed"] or d["drawn_unnamed"]:
        rows.append(R(T("Capital / drawings not tagged to a named partner", "رأس مال / مسحوبات غير منسوبة لشريك"),
                      round(d["capital_unnamed"] - d["drawn_unnamed"], 2)))
    eq_total = round(d["closing_total"] + d["held_total"] + d["wip_profit"] + bs["reserves"], 2)
    rows += [R(T("TOTAL EQUITY PER BALANCE SHEET", "إجمالي حقوق الملكية بالميزانية"), bs["te"], style="grand"),
             note((T("✓ Ties to the balance sheet", "✓ يطابق الميزانية") if abs(eq_total - bs["te"]) < 0.01
                   else T(f"✗ Difference {eq_total - bs['te']:,.2f} — check partner names on capital and drawings",
                          "✗ فرق — راجع أسماء الشركاء في رأس المال والمسحوبات"))),
             note(T(f"Each partner's profit share follows their ownership % per project (Partners → ownership), after the company reserve "
                    f"of {d['reserve_pct']:.0%}. Capital put in is each partner's capital / current account in the books.",
                    "نصيب كل شريك حسب نسبته في كل مشروع بعد الاحتياطي."))]
    q, pol = d["dist"], d["policy"]
    rows += [sec(T("6. Distribution and company capital", "٦. التوزيع ورأس مال الشركة")),
             R(T("Clean net profit", "صافي الربح الصافي"), d["clean_total"]),
             R(T("Company capital reserve", "احتياطي رأس مال الشركة") + (f" ({pol['reserve_early_pct']:.0%} " + T("until", "حتى") +
               f" {pol['reserve_early_until']:,.0f}, " + T("then", "ثم") + f" {pol['reserve_pct']:.0%})" if pol["reserve_early_pct"] and pol["reserve_early_until"]
               else f" ({pol['reserve_pct']:.0%})"), -d["reserve_amount"]),
             R(T("Distributable to partners", "قابل للتوزيع على الشركاء"), round(d["clean_total"] - d["reserve_amount"], 2), style="total"),
             H(T("Distributable cash — the company's own money", "النقدية القابلة للتوزيع — أموال الشركة الذاتية")),
             R(T("Company's own cash", "النقدية الذاتية للشركة"), q["cash"]),
             R(T("Less: owed to suppliers and staff", "ناقص: المستحق للموردين والعاملين"), -q["owed"]),
             R(T("Less: client advances for work not yet done", "ناقص: دفعات العملاء عن أعمال لم تُنفذ"), -q["advances"]),
             R(T("Less: held for remaining project costs", "ناقص: المحتجز لتكاليف المشروعات المتبقية"), -q["held"]),
             R(T(f"Less: operating buffer ({pol['buffer_months']:g} months of overhead at {q['monthly_overhead']:,.0f})",
                 f"ناقص: احتياطي التشغيل ({pol['buffer_months']:g} شهر من المصروفات بواقع {q['monthly_overhead']:,.0f})"), -q["buffer"]),
             R(T("Less: company capital reserve", "ناقص: احتياطي رأس مال الشركة"), -q["reserve"]),
             R(T("Free own cash", "النقدية الذاتية الحرة"), q["free"], style="total"),
             R(T("Clean profit still owed to partners", "الربح الصافي المستحق للشركاء"), q["owed_to_partners"]),
             R(T("Maximum safe to distribute now", "الحد الأقصى الآمن للتوزيع الآن"), q["max_safe"], style="grand"),
             R(T("Recommendation", "التوصية"), d["verdict_ar"] if ar else d["verdict"])]
    if q["capital_goal"]:
        rows += [H(T("Company capital — progress to the goal", "رأس مال الشركة — التقدم نحو الهدف")),
                 R(T("Company capital (reserve earned from clean profit)", "رأس مال الشركة (الاحتياطي من الربح الصافي)"), d["reserve_amount"]),
                 R(T("Goal", "الهدف"), q["capital_goal"]), R(T("Progress to goal", "نسبة التقدم"), q["progress"]),
                 R(T("Cash set aside in the company reserve account", "النقدية المجنّبة في حساب احتياطي الشركة"), q["set_aside"]),
                 R(T("Reserve not yet backed by cash", "احتياطي غير مغطى بالنقدية"), q["not_backed"])]
        if q["months_covered"] is not None:
            rows.append(R(T("Months of overhead the reserve covers", "عدد أشهر المصروفات التي يغطيها الاحتياطي"), q["months_covered"]))
    period = f"{d['date_from']} → {d['date_to']}" if d["date_from"] else T(f"As at {d['date_to']}", f"حتى {d['date_to']}")
    return {"title": T("Partner Pack", "تقرير الشركاء"), "subtitle": period + T("  ·  management accounts for the partners", "  ·  حسابات إدارية للشركاء"),
            "columns": [{"label": "", "align": "left"}] + [{"label": "", "align": "right", "fmt": "money"} for _ in range(n - 1)],
            "rows": rows, "lang": lang, "ok": True, "label_width": "36%"}
