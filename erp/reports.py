"""Financial statements as printable tables + renderers (Excel, Word, HTML→PDF, CSV).

Every report is first built as a neutral "table" dict:
    {"title", "subtitle", "columns": [{"label", "align", "fmt"}],
     "rows": [{"cells": [...], "style": "item|header|subtotal|total|note", "indent": 0}]}
and then rendered by one of the writers. The UI shows the same table, so what
you see on screen is exactly what gets exported.
"""
import datetime as _dt
import html
import io
import zipfile

from . import accounting as acc

# ----------------------------------------------------------------- i18n labels
T = {
    "income_statement": ("Income Statement (Profit & Loss)", "قائمة الدخل (الأرباح والخسائر)"),
    "balance_sheet": ("Balance Sheet (Statement of Financial Position)", "الميزانية العمومية (قائمة المركز المالي)"),
    "cash_flow": ("Cash Flow Statement", "قائمة التدفقات النقدية"),
    "trial_balance": ("Trial Balance", "ميزان المراجعة"),
    "general_ledger": ("General Ledger", "دفتر الأستاذ العام"),
    "bu_pnl": ("Profitability by Business Unit", "ربحية وحدات الأعمال"),
    "payables": ("Accounts Payable by Party", "أرصدة الدائنين"),
    "receivables": ("Accounts Receivable by Business Unit", "أرصدة المدينين"),
    "stock": ("Inventory Valuation", "تقييم المخزون"),
    "vouchers": ("Vouchers", "السندات والقيود"),
    "journal": ("Journal", "دفتر اليومية"),
    "revenue": ("Revenue", "الإيرادات"),
    "gross_sales": ("Gross sales", "إجمالي المبيعات"),
    "discounts": ("Sales discounts & allowances", "خصومات ومسموحات المبيعات"),
    "net_sales": ("Net sales", "صافي المبيعات"),
    "cost_of_sales": ("Cost of sales", "تكلفة المبيعات"),
    "gross_profit": ("Gross profit", "مجمل الربح"),
    "operating_expenses": ("Operating expenses", "المصروفات التشغيلية"),
    "Employee expenses": ("Employee expenses", "مصروفات العاملين"),
    "General & administrative expenses": ("General & administrative expenses", "مصروفات عمومية وإدارية"),
    "Marketing & advertising expenses": ("Marketing & advertising expenses", "مصروفات تسويق وإعلان"),
    "Utilities (electricity / telephone)": ("Utilities (electricity / telephone)", "مرافق (كهرباء / اتصالات)"),
    "Other operating expenses": ("Other operating expenses", "مصروفات تشغيلية أخرى"),
    "Allocated shared overhead": ("Allocated shared overhead", "حصة المصروفات المشتركة المحملة"),
    "ebitda": ("EBITDA", "الأرباح قبل الفوائد والضرائب والإهلاك والاستهلاك (EBITDA)"),
    "depreciation": ("Depreciation", "الإهلاك"),
    "amortization": ("Amortization", "الاستهلاك (الأصول غير الملموسة)"),
    "d_and_a": ("Depreciation & amortization", "الإهلاك والاستهلاك"),
    "ebit": ("EBIT / Operating profit", "الربح التشغيلي (EBIT)"),
    "interest": ("Interest expense", "مصروفات الفوائد"),
    "other_nonoperating": ("Other non-operating expenses", "مصروفات غير تشغيلية أخرى"),
    "other_income": ("Other income", "إيرادات أخرى"),
    "ebt": ("EBT / Profit before tax", "الربح قبل الضرائب"),
    "income_tax": ("Income tax", "ضريبة الدخل"),
    "net_profit": ("Net profit / (loss)", "صافي الربح / (الخسارة)"),
    "margin": ("margin", "هامش"),
    "assets": ("ASSETS", "الأصول"),
    "current_assets": ("Current assets", "الأصول المتداولة"),
    "noncurrent_assets": ("Non-current assets", "الأصول غير المتداولة"),
    "total_assets": ("TOTAL ASSETS", "إجمالي الأصول"),
    "liabilities": ("LIABILITIES", "الالتزامات"),
    "current_liabilities": ("Current liabilities", "الالتزامات المتداولة"),
    "noncurrent_liabilities": ("Non-current liabilities", "الالتزامات غير المتداولة"),
    "total_liabilities": ("TOTAL LIABILITIES", "إجمالي الالتزامات"),
    "equity": ("EQUITY", "حقوق الملكية"),
    "total_equity": ("TOTAL EQUITY", "إجمالي حقوق الملكية"),
    "total_le": ("TOTAL LIABILITIES & EQUITY", "إجمالي الالتزامات وحقوق الملكية"),
    "cf_ops": ("CASH FLOWS FROM OPERATING ACTIVITIES", "التدفقات النقدية من الأنشطة التشغيلية"),
    "net_income": ("Net income", "صافي الدخل"),
    "adjustments": ("Adjustments for non-cash charges", "تسويات البنود غير النقدية"),
    "wc_changes": ("Changes in operating assets and liabilities", "التغير في الأصول والالتزامات التشغيلية"),
    "net_ops": ("Net cash provided by operating activities", "صافي النقد من الأنشطة التشغيلية"),
    "cf_inv": ("CASH FLOWS FROM INVESTING ACTIVITIES", "التدفقات النقدية من الأنشطة الاستثمارية"),
    "net_inv": ("Net cash used in investing activities", "صافي النقد من الأنشطة الاستثمارية"),
    "cf_fin": ("CASH FLOWS FROM FINANCING ACTIVITIES", "التدفقات النقدية من الأنشطة التمويلية"),
    "net_fin": ("Net cash provided by financing activities", "صافي النقد من الأنشطة التمويلية"),
    "beginning_cash": ("Beginning cash", "النقدية أول المدة"),
    "change_cash": ("(+/-) Change in cash & cash equivalents", "صافي التغير في النقدية"),
    "ending_cash": ("Ending cash", "النقدية آخر المدة"),
    "code": ("Code", "الكود"), "account": ("Account", "الحساب"), "debit": ("Debit", "مدين"),
    "credit": ("Credit", "دائن"), "total": ("TOTAL", "الإجمالي"), "opening": ("Opening", "أول المدة"),
    "movement": ("Movement", "الحركة"), "closing": ("Closing", "آخر المدة"), "balance": ("Balance", "الرصيد"),
    "date": ("Date", "التاريخ"), "voucher": ("Voucher", "القيد"), "description": ("Description", "البيان"),
    "party": ("Party", "الطرف"), "bu": ("Business unit", "وحدة الأعمال"),
    "as_of": ("As of", "في"), "period": ("Period", "الفترة"), "to": ("to", "إلى"),
    "all_units": ("All business units", "كل وحدات الأعمال"), "shared_only": ("Shared / company level", "على مستوى الشركة"),
    "balanced_ok": ("Balanced ✓", "متوازن ✓"), "unbalanced": ("OUT OF BALANCE", "غير متوازن"),
}
CF_AR = {"Depreciation": "الإهلاك", "Amortization": "الاستهلاك", "Accounts receivable": "المدينون",
         "Inventory": "المخزون", "Prepaid expenses & advances": "المصروفات المقدمة والدفعات",
         "Other current assets": "أصول متداولة أخرى", "Accounts payable": "الدائنون",
         "Accrued liabilities & payroll": "المستحقات والأجور المستحقة",
         "Deferred revenue (client advances)": "إيرادات مؤجلة (دفعات العملاء المقدمة)", "Taxes payable": "ضرائب مستحقة",
         "Other current liabilities": "التزامات متداولة أخرى",
         "Capital expenditures (fixed assets)": "النفقات الرأسمالية (أصول ثابتة)",
         "Additions to intangibles": "إضافات أصول غير ملموسة", "Other long-term assets": "أصول طويلة الأجل أخرى",
         "Investments bought / (sold)": "استثمارات (مشتراة / مباعة)", "Partners' current accounts": "جاري الشركاء",
         "Short-term debt issued / (repaid)": "قروض قصيرة الأجل (مستلمة / مسددة)",
         "Long-term debt issued / (repaid)": "قروض طويلة الأجل (مستلمة / مسددة)",
         "Other long-term liabilities": "التزامات طويلة الأجل أخرى", "Capital contributed": "رأس المال المدفوع",
         "Other equity movements": "حركات حقوق ملكية أخرى",
         "Dividends / partner drawings paid": "توزيعات الأرباح / مسحوبات الشركاء",
         "Retained earnings adjustments": "تسويات الأرباح المحتجزة"}


def L(key, lang):
    v = T.get(key)
    if not v:
        return key
    return v[1] if lang == "ar" else v[0]


# -------------------------------------------------------------------- periods
def periods(date_from, date_to, compare="none"):
    """[(label, from, to)] for a range, optionally split by month/quarter/year."""
    today = _dt.date.today()
    d1 = _dt.date.fromisoformat(acc.clean_date(date_from)) if date_from else today.replace(month=1, day=1)
    d2 = _dt.date.fromisoformat(acc.clean_date(date_to)) if date_to else today
    if d2 < d1:
        raise acc.ValidationError("'To' date is before 'From' date")
    if compare in (None, "", "none"):
        return [(f"{d1.isoformat()} → {d2.isoformat()}", d1.isoformat(), d2.isoformat())]
    out, cur = [], d1
    while cur <= d2 and len(out) < 36:
        if compare == "month":
            nxt = (cur.replace(day=1) + _dt.timedelta(days=32)).replace(day=1)
            label = cur.strftime("%b %Y")
        elif compare == "quarter":
            q = (cur.month - 1) // 3
            nxt = _dt.date(cur.year + (1 if q == 3 else 0), 1 if q == 3 else q * 3 + 4, 1)
            label = f"Q{q + 1} {cur.year}"
        elif compare == "year":
            nxt = _dt.date(cur.year + 1, 1, 1)
            label = f"FY{cur.year}"
        else:
            raise acc.ValidationError("compare must be none, month, quarter or year")
        end = min(nxt - _dt.timedelta(days=1), d2)
        out.append((label, cur.isoformat(), end.isoformat()))
        cur = nxt
    return out


def _row(label, values, style="item", indent=0, code=""):
    return {"cells": [label] + list(values), "style": style, "indent": indent, "code": code}


_NAMES = {}


def _names(db, lang):
    """code -> display name in the report language (Arabic name when set)."""
    if lang != "ar":
        return {}
    return {r["code"]: r["name_ar"] for r in db.q("SELECT code, name_ar FROM accounts WHERE name_ar<>''")}


def _merge_lines(results, getter):
    """Union of account lines across periods, preserving order: [(code, name, [amount per period])]."""
    order, names = [], {}
    for r in results:
        for ln in getter(r):
            k = ln["code"] or ln["name"]
            if k not in names:
                order.append(k)
                names[k] = ln["name"]
    out = []
    for k in order:
        vals = []
        for r in results:
            vals.append(next((ln["amount"] for ln in getter(r) if (ln["code"] or ln["name"]) == k), 0.0))
        out.append((k if k != names[k] else "", names[k], vals))
    return out


def _subtitle(db, lang, pers, bu_id, as_of=False):
    unit = L("all_units", lang)
    if bu_id == "none":
        unit = L("shared_only", lang)
    elif bu_id:
        b = db.one("SELECT code, name FROM business_units WHERE id=?", (int(bu_id),))
        unit = f"{b['code']} — {b['name']}" if b else unit
    if as_of:
        when = f"{L('as_of', lang)} {pers[-1][2]}"
    else:
        when = f"{L('period', lang)}: {pers[0][1]} {L('to', lang)} {pers[-1][2]}"
    return f"{when} · {unit} · {db.setting('currency', 'EGP')}"


def _cols(pers, lang, first=None):
    return [{"label": first or "", "align": "left"}] + [{"label": p[0], "align": "right", "fmt": "money"} for p in pers]


# ------------------------------------------------------------------ statements
def income_statement_table(db, date_from=None, date_to=None, compare="none", bu_id=None, detail=True,
                           lang="en", allocate=True):
    pers = periods(date_from, date_to, compare)
    res = [acc.income_statement(db, f, t, bu_id, allocate_overhead=allocate) for _, f, t in pers]
    nm = _names(db, lang)
    rows = []
    v = lambda key: [r[key] for r in res]  # noqa: E731

    def block(getter, indent=1):
        if detail:
            for code, name, vals in _merge_lines(res, getter):
                rows.append(_row(f"{code}  {nm.get(code, name)}".strip(), vals, "item", indent, code))

    rows.append(_row(L("revenue", lang), ["" for _ in pers], "header"))
    block(lambda r: r["revenue"])
    rows.append(_row(L("gross_sales", lang), v("gross_sales"), "subtotal"))
    if any(r["discounts"] for r in res):
        block(lambda r: r["discounts"])
        rows.append(_row(L("discounts", lang), [-sum(x["amount"] for x in r["discounts"]) for r in res], "item"))
    rows.append(_row(L("net_sales", lang), v("net_sales"), "subtotal"))
    block(lambda r: r["cogs"])
    rows.append(_row(L("cost_of_sales", lang), [-x for x in v("cost_of_sales")], "subtotal"))
    rows.append(_row(L("gross_profit", lang), v("gross_profit"), "total"))
    rows.append(_row(L("operating_expenses", lang), ["" for _ in pers], "header"))
    labels = [g["label"] for g in res[0]["opex_groups"]]
    for i, lbl in enumerate(labels):
        vals = [r["opex_groups"][i]["total"] if i < len(r["opex_groups"]) else 0 for r in res]
        if not any(vals):
            continue
        if detail:
            for code, name, lv in _merge_lines(res, lambda r, i=i: r["opex_groups"][i]["lines"]
                                                if i < len(r["opex_groups"]) else []):
                rows.append(_row(f"{code}  {nm.get(code, name)}".strip(), [-x for x in lv], "item", 2, code))
        rows.append(_row(L(lbl, lang), [-x for x in vals], "item" if not detail else "subtotal", 1))
    rows.append(_row(L("operating_expenses", lang), [-x for x in v("operating_expenses")], "subtotal"))
    rows.append(_row(L("ebitda", lang), v("ebitda"), "total"))
    rows.append(_row(L("margin", lang) + " %", [r["margins"]["ebitda"] for r in res], "note"))
    rows.append(_row(L("depreciation", lang), [-sum(x["amount"] for x in r["depreciation"]) for r in res], "item", 1))
    rows.append(_row(L("amortization", lang), [-sum(x["amount"] for x in r["amortization"]) for r in res], "item", 1))
    rows.append(_row(L("ebit", lang), v("ebit"), "total"))
    rows.append(_row(L("interest", lang), [-sum(x["amount"] for x in r["interest"]) for r in res], "item", 1))
    if any(r["other_nonoperating"] for r in res):
        rows.append(_row(L("other_nonoperating", lang), [-sum(x["amount"] for x in r["other_nonoperating"])
                                                         for r in res], "item", 1))
    if any(r["other_income"] for r in res):
        rows.append(_row(L("other_income", lang), [sum(x["amount"] for x in r["other_income"]) for r in res], "item", 1))
    rows.append(_row(L("ebt", lang), v("ebt"), "total"))
    rows.append(_row(L("income_tax", lang), [-x for x in v("income_tax")], "item", 1))
    rows.append(_row(L("net_profit", lang), v("net_profit"), "grand"))
    rows.append(_row(L("margin", lang) + " %", [r["margins"]["net"] for r in res], "note"))
    return {"title": L("income_statement", lang), "subtitle": _subtitle(db, lang, pers, bu_id),
            "columns": _cols(pers, lang), "rows": rows, "lang": lang}


def balance_sheet_table(db, date_to=None, date_from=None, compare="none", bu_id=None, lang="en"):
    pers = periods(date_from or date_to or None, date_to, compare) if compare not in (None, "", "none") \
        else [(f"{L('as_of', lang)} {acc.clean_date(date_to)}", None, acc.clean_date(date_to))]
    res = [acc.balance_sheet(db, t, bu_id) for _, _, t in pers]
    nm = _names(db, lang)
    rows = []

    def sect(title, key, total_key, sign_label):
        rows.append(_row(L(title, lang), ["" for _ in pers], "header"))
        for code, name, vals in _merge_lines(res, lambda r: r[key]):
            rows.append(_row(f"{code}  {nm.get(code, name)}".strip(), vals, "item", 1, code))
        rows.append(_row(L(sign_label, lang), [r[total_key] for r in res], "subtotal"))

    rows.append(_row(L("assets", lang), ["" for _ in pers], "section"))
    sect("current_assets", "current_assets", "total_current_assets", "current_assets")
    sect("noncurrent_assets", "noncurrent_assets", "total_noncurrent_assets", "noncurrent_assets")
    rows.append(_row(L("total_assets", lang), [r["total_assets"] for r in res], "grand"))
    rows.append(_row(L("liabilities", lang), ["" for _ in pers], "section"))
    sect("current_liabilities", "current_liabilities", "total_current_liabilities", "current_liabilities")
    sect("noncurrent_liabilities", "noncurrent_liabilities", "total_noncurrent_liabilities", "noncurrent_liabilities")
    rows.append(_row(L("total_liabilities", lang), [r["total_liabilities"] for r in res], "total"))
    rows.append(_row(L("equity", lang), ["" for _ in pers], "section"))
    for code, name, vals in _merge_lines(res, lambda r: r["equity"]):
        rows.append(_row(f"{code}  {nm.get(code, name)}".strip(), vals, "item", 1, code))
    rows.append(_row(L("total_equity", lang), [r["total_equity"] for r in res], "total"))
    rows.append(_row(L("total_le", lang), [r["total_liabilities_equity"] for r in res], "grand"))
    ok = all(r["balanced"] for r in res)
    rows.append(_row(L("balanced_ok" if ok else "unbalanced", lang), ["" for _ in pers], "note"))
    return {"title": L("balance_sheet", lang), "subtitle": _subtitle(db, lang, pers, bu_id, as_of=True),
            "columns": _cols(pers, lang), "rows": rows, "lang": lang, "ok": ok}


def cash_flow_table(db, date_from=None, date_to=None, compare="none", bu_id=None, lang="en"):
    pers = periods(date_from, date_to, compare)
    res = [acc.cash_flow(db, f, t, bu_id) for _, f, t in pers]
    lab = lambda s: s if lang != "ar" else (s.split(") ", 1)[0] + ") " + CF_AR.get(s.split(") ", 1)[1], s.split(") ", 1)[1])
                                            if ") " in s else CF_AR.get(s, s))  # noqa: E731
    rows = [_row(L("cf_ops", lang), ["" for _ in pers], "section"),
            _row(L("net_income", lang), [r["net_income"] for r in res], "subtotal"),
            _row(L("adjustments", lang), ["" for _ in pers], "header")]
    n_adj = 2
    for i in range(len(res[0]["operating"])):
        if i == n_adj:
            rows.append(_row(L("wc_changes", lang), ["" for _ in pers], "header"))
        rows.append(_row(lab(res[0]["operating"][i]["label"]), [r["operating"][i]["amount"] for r in res], "item", 1))
    rows.append(_row(L("net_ops", lang), [r["net_operating"] for r in res], "total"))
    rows.append(_row(L("cf_inv", lang), ["" for _ in pers], "section"))
    for i in range(len(res[0]["investing"])):
        rows.append(_row(lab(res[0]["investing"][i]["label"]), [r["investing"][i]["amount"] for r in res], "item", 1))
    rows.append(_row(L("net_inv", lang), [r["net_investing"] for r in res], "total"))
    rows.append(_row(L("cf_fin", lang), ["" for _ in pers], "section"))
    for i in range(len(res[0]["financing"])):
        vals = [r["financing"][i]["amount"] for r in res]
        if any(vals) or i < 4:
            rows.append(_row(lab(res[0]["financing"][i]["label"]), vals, "item", 1))
    rows.append(_row(L("net_fin", lang), [r["net_financing"] for r in res], "total"))
    rows.append(_row(L("beginning_cash", lang), [r["beginning_cash"] for r in res], "item"))
    rows.append(_row(L("change_cash", lang), [r["change_in_cash"] for r in res], "item"))
    rows.append(_row(L("ending_cash", lang), [r["ending_cash"] for r in res], "grand"))
    ok = all(r["reconciles"] for r in res)
    rows.append(_row(L("balanced_ok" if ok else "unbalanced", lang), ["" for _ in pers], "note"))
    return {"title": L("cash_flow", lang), "subtitle": _subtitle(db, lang, pers, bu_id), "columns": _cols(pers, lang),
            "rows": rows, "lang": lang, "ok": ok}


def trial_balance_table(db, date_from=None, date_to=None, bu_id=None, lang="en"):
    tb = acc.trial_balance(db, date_from, date_to, bu_id)
    cols = [{"label": L("account", lang), "align": "left"}]
    if date_from:
        cols += [{"label": f"{L('opening', lang)} {L('debit', lang)}", "align": "right", "fmt": "money"},
                 {"label": f"{L('opening', lang)} {L('credit', lang)}", "align": "right", "fmt": "money"},
                 {"label": f"{L('movement', lang)} {L('debit', lang)}", "align": "right", "fmt": "money"},
                 {"label": f"{L('movement', lang)} {L('credit', lang)}", "align": "right", "fmt": "money"}]
    cols += [{"label": f"{L('closing', lang)} {L('debit', lang)}" if date_from else L("debit", lang), "align": "right",
              "fmt": "money"},
             {"label": f"{L('closing', lang)} {L('credit', lang)}" if date_from else L("credit", lang), "align": "right",
              "fmt": "money"}]
    keys = (["opening_debit", "opening_credit", "period_debit", "period_credit"] if date_from else []) + ["debit", "credit"]
    nm = _names(db, lang)
    rows = [_row(f"{r['code']}  {nm.get(r['code'], r['name'])}", [r[k] or "" for k in keys], "item", 0, r["code"])
            for r in tb["rows"]]
    rows.append(_row(L("total", lang), [tb["totals"][k] for k in keys], "grand"))
    rows.append(_row(L("balanced_ok" if tb["balanced"] else "unbalanced", lang), ["" for _ in keys], "note"))
    pers = [("", date_from or "…", acc.clean_date(date_to))]
    return {"title": L("trial_balance", lang), "subtitle": _subtitle(db, lang, pers, bu_id, as_of=not date_from),
            "columns": cols, "rows": rows, "lang": lang, "ok": tb["balanced"]}


def general_ledger_table(db, account=None, date_from=None, date_to=None, bu_id=None, party=None, lang="en"):
    gl = acc.general_ledger(db, account, date_from, date_to, bu_id, party, limit=100000)
    nm = _names(db, lang)
    cols = [{"label": L("date", lang), "align": "left"}, {"label": L("voucher", lang), "align": "left"},
            {"label": L("description", lang), "align": "left"}, {"label": L("party", lang), "align": "left"},
            {"label": L("bu", lang), "align": "left"}]
    if not account:
        cols.append({"label": L("account", lang), "align": "left"})
    cols += [{"label": L("debit", lang), "align": "right", "fmt": "money"},
             {"label": L("credit", lang), "align": "right", "fmt": "money"}]
    if account:
        cols.append({"label": L("balance", lang), "align": "right", "fmt": "money"})
    rows = []
    if account and date_from:
        rows.append({"cells": ["", "", L("opening", lang), "", "", "", "", gl["opening"]], "style": "subtotal", "indent": 0})
    for r in gl["rows"]:
        cells = [r["date"], f"#{r['id']} {r['kind']}", r["memo"], r["party"], r["bu_code"] or ""]
        if not account:
            cells.append(f"{r['account']} {nm.get(r['account'], r['account_name'])}")
        cells += [r["debit"] or "", r["credit"] or ""]
        if account:
            cells.append(r["balance"])
        rows.append({"cells": cells, "style": "item", "indent": 0})
    td = round(sum(r["debit"] for r in gl["rows"]), 2)
    tc = round(sum(r["credit"] for r in gl["rows"]), 2)
    tot = [L("total", lang)] + [""] * (len(cols) - (4 if account else 3)) + [td, tc] + ([gl["closing"]] if account else [])
    rows.append({"cells": tot, "style": "grand", "indent": 0})
    title = L("general_ledger", lang) + (f" — {gl['account']['code']} {nm.get(gl['account']['code'], gl['account']['name'])}"
                                         if gl["account"] else "")
    pers = [("", date_from or "…", acc.clean_date(date_to))]
    return {"title": title, "subtitle": _subtitle(db, lang, pers, bu_id), "columns": cols, "rows": rows, "lang": lang}


def bu_table(db, date_from=None, date_to=None, status=None, lang="en"):
    data = acc.bu_summaries(db, date_from, date_to, status)
    heads = [("Code", "الكود", "left"), ("Name", "الاسم", "left"), ("Basis", "أساس الفوترة", "left"),
             ("Revenue", "الإيراد", "right"), ("Direct costs", "التكاليف المباشرة", "right"),
             ("Direct overhead", "مصروفات مباشرة", "right"), ("Allocated overhead", "مصروفات محملة", "right"),
             ("Net profit", "صافي الربح", "right"), ("Margin", "الهامش", "right"),
             ("Receivable", "المدينون", "right"), ("To collect", "المتبقي للتحصيل", "right")]
    cols = [{"label": h[1] if lang == "ar" else h[0], "align": h[2], "fmt": "money" if h[2] == "right" else None}
            for h in heads]
    cols[8]["fmt"] = "pct"
    rows = [{"cells": [b["code"], b["name"], b["basis"], b["revenue"], b["cogs"], b["direct_overhead"],
                       b["allocated_overhead"], b["net_profit"], b["margin"], b["receivable"], b["to_collect"]],
             "style": "item", "indent": 0} for b in data]
    tot = lambda k: round(sum(b[k] for b in data), 2)  # noqa: E731
    rows.append({"cells": [L("total", lang), "", "", tot("revenue"), tot("cogs"), tot("direct_overhead"),
                           tot("allocated_overhead"), tot("net_profit"), "", tot("receivable"), tot("to_collect")],
                 "style": "grand", "indent": 0})
    pers = [("", date_from or "…", acc.clean_date(date_to))]
    return {"title": L("bu_pnl", lang), "subtitle": _subtitle(db, lang, pers, None), "columns": cols, "rows": rows,
            "lang": lang}


def payables_table(db, date_to=None, lang="en"):
    data = acc.party_balances(db, subtypes=("payable", "accrued"), date_to=date_to)
    cols = [{"label": L("party", lang), "align": "left"}, {"label": L("account", lang), "align": "left"},
            {"label": "Billed" if lang != "ar" else "المستحق", "align": "right", "fmt": "money"},
            {"label": "Paid" if lang != "ar" else "المدفوع", "align": "right", "fmt": "money"},
            {"label": L("balance", lang), "align": "right", "fmt": "money"},
            {"label": "Last activity" if lang != "ar" else "آخر حركة", "align": "left"}]
    rows = [{"cells": [r["party"] or "—", r["account"], r["owed"], r["paid"], r["balance"], r["last_date"]],
             "style": "item", "indent": 0} for r in data]
    rows.append({"cells": [L("total", lang), "", "", "", round(sum(r["balance"] for r in data), 2), ""],
                 "style": "grand", "indent": 0})
    pers = [("", "…", acc.clean_date(date_to))]
    return {"title": L("payables", lang), "subtitle": _subtitle(db, lang, pers, None, as_of=True), "columns": cols,
            "rows": rows, "lang": lang}


def stock_table(db, warehouse_id=None, q=None, lang="en"):
    data = acc.stock_report(db, warehouse_id, q)
    heads = [("Item", "الصنف", "left", None), ("Warehouse", "المخزن", "left", None), ("Unit", "الوحدة", "left", None),
             ("On hand", "الرصيد", "right", "num"), ("Avg cost", "متوسط التكلفة", "right", "money"),
             ("Value", "القيمة", "right", "money")]
    cols = [{"label": h[1] if lang == "ar" else h[0], "align": h[2], "fmt": h[3]} for h in heads]
    rows = [{"cells": [r["name"], r["warehouse"], r["unit"], r["qty"], r["avg_cost"], r["value"]], "style": "item",
             "indent": 0} for r in data]
    rows.append({"cells": [L("total", lang), "", "", "", "", round(sum(r["value"] for r in data), 2)], "style": "grand",
                 "indent": 0})
    return {"title": L("stock", lang), "subtitle": f"{L('as_of', lang)} {acc.today()} · {db.setting('currency')}",
            "columns": cols, "rows": rows, "lang": lang}


def fixed_assets_table(db, date_to=None, q=None, state=None, lang="en"):
    from . import assets
    data = assets.register(db, q, None, state or "active", acc.clean_date(date_to) if date_to else None)
    heads = [("Code", "الكود", "left", None), ("Asset", "الأصل", "left", None), ("Account", "الحساب", "left", None),
             ("Acquired", "تاريخ الشراء", "left", None), ("Life (months)", "العمر (شهور)", "right", "num"),
             ("Cost", "التكلفة", "right", "money"), ("Accumulated depreciation", "مجمع الإهلاك", "right", "money"),
             ("Book value", "القيمة الدفترية", "right", "money"), ("Monthly", "الإهلاك الشهري", "right", "money")]
    cols = [{"label": h[1] if lang == "ar" else h[0], "align": h[2], "fmt": h[3]} for h in heads]
    rows = [{"cells": [r["code"], r["name"] + (" (disposed)" if r["disposed"] and lang != "ar" else " (مستبعد)" if r["disposed"] else ""),
                       r["account"], r["acquired"], r["life_months"], r["cost"], r["accumulated"], r["book_value"],
                       r["monthly"]], "style": "item", "indent": 0} for r in data["rows"]]
    t = data["totals"]
    rows.append({"cells": [L("total", lang), "", "", "", "", t["cost"], t["accumulated"], t["book_value"], ""],
                 "style": "grand", "indent": 0})
    title = "سجل الأصول الثابتة" if lang == "ar" else "Fixed asset register"
    return {"title": title, "subtitle": f"{L('as_of', lang)} {acc.clean_date(date_to) if date_to else acc.today()} · "
                                        f"{db.setting('currency')}", "columns": cols, "rows": rows, "lang": lang}


def receivables_table(db, date_from=None, date_to=None, lang="en"):
    data = [b for b in acc.bu_summaries(db, date_from, date_to) if b["receivable"] or b["advances"] or b["to_collect"]]
    heads = [("Code", "الكود", "left"), ("Name", "الاسم", "left"), ("Invoiced receivable", "مديونية الفواتير", "right"),
             ("Client advances", "دفعات مقدمة", "right"), ("Expected billing", "الفوترة المتوقعة", "right"),
             ("Collected", "المحصل", "right"), ("To collect", "المتبقي للتحصيل", "right")]
    cols = [{"label": h[1] if lang == "ar" else h[0], "align": h[2], "fmt": "money" if h[2] == "right" else None}
            for h in heads]
    rows = [{"cells": [b["code"], b["name"], b["receivable"], b["advances"], b["expected_billing"], b["collected"],
                       b["to_collect"]], "style": "item", "indent": 0} for b in data]
    t = lambda k: round(sum(b[k] for b in data), 2)  # noqa: E731
    rows.append({"cells": [L("total", lang), "", t("receivable"), t("advances"), t("expected_billing"), t("collected"),
                           t("to_collect")], "style": "grand", "indent": 0})
    pers = [("", date_from or "…", acc.clean_date(date_to))]
    return {"title": L("receivables", lang), "subtitle": _subtitle(db, lang, pers, None), "columns": cols, "rows": rows,
            "lang": lang}


def vouchers_table(db, entries, title, lang="en", subtitle=""):
    """A filtered list of vouchers (any module screen) as an exportable table."""
    heads = [("No.", "رقم"), ("Date", "التاريخ"), ("Type", "النوع"), ("Description", "البيان"), ("Party", "الطرف"),
             ("Business unit", "وحدة الأعمال"), ("Debit lines", "الحسابات المدينة"), ("Credit lines", "الحسابات الدائنة"),
             ("Amount", "المبلغ"), ("Status", "الحالة")]
    cols = [{"label": h[1] if lang == "ar" else h[0], "align": "right" if h[0] == "Amount" else "left",
             "fmt": "money" if h[0] == "Amount" else None} for h in heads]
    rows = []
    for e in entries:
        dr = "; ".join(f"{ln['account']} {ln['name']} {ln['debit']:,.2f}" for ln in e["lines"] if ln["debit"])
        cr = "; ".join(f"{ln['account']} {ln['name']} {ln['credit']:,.2f}" for ln in e["lines"] if ln["credit"])
        status = "void" if e["status"] == "void" else ("reversed" if e["reversed_by"] else "posted")
        rows.append({"cells": [e["id"], e["date"], e["kind"], e["memo"], e["party"], e["bu_code"] or "", dr, cr,
                               e["amount"], status], "style": "item", "indent": 0})
    rows.append({"cells": [L("total", lang), "", "", "", "", "", "", "",
                           round(sum(e["amount"] for e in entries if e["status"] == "posted"), 2), ""],
                 "style": "grand", "indent": 0})
    return {"title": title, "subtitle": subtitle, "columns": cols, "rows": rows, "lang": lang}


def tax_table(db, date_from=None, date_to=None, lang="en"):
    t = acc.tax_summary(db, date_from, date_to)
    ar = lang == "ar"
    cols = [{"label": "البند" if ar else "Item", "align": "left"}, {"label": "الرقم الضريبي" if ar else "Tax ID",
                                                                  "align": "left"},
            {"label": "الأساس الخاضع" if ar else "Taxable base", "align": "right", "fmt": "money"},
            {"label": "الضريبة" if ar else "Tax", "align": "right", "fmt": "money"}]
    rows = [{"cells": ["ضريبة القيمة المضافة" if ar else "VALUE ADDED TAX", "", "", ""], "style": "section", "indent": 0}]
    for b in t["vat_by_code"]:
        side = ("مبيعات" if ar else "Sales") if b["side"] == "sales" else ("مشتريات" if ar else "Purchases")
        rows.append({"cells": [f"{side} — {b['code']} ({b['documents']})", "", b["net"], b["vat"]], "style": "item",
                     "indent": 1})
    rows += [{"cells": ["ضريبة المخرجات" if ar else "Output VAT (on sales)", "", "", t["output_vat"]], "style": "subtotal",
              "indent": 0},
             {"cells": ["ضريبة المدخلات" if ar else "Input VAT (on purchases)", "", "", -t["input_vat"]],
              "style": "subtotal", "indent": 0},
             {"cells": ["صافي الضريبة المستحقة" if ar else "Net VAT payable / (refundable)", "", "", t["net_vat"]],
              "style": "grand", "indent": 0},
             {"cells": ["ضريبة الخصم والتحصيل — المخصومة من الموردين" if ar else
                        "WITHHOLDING TAX DEDUCTED FROM SUPPLIERS (to remit)", "", "", ""], "style": "section", "indent": 0}]
    for x in t["wht_withheld_from_suppliers"]:
        rows.append({"cells": [x["party"] or "—", x["tax_id"], "", x["amount"]], "style": "item", "indent": 1})
    rows.append({"cells": ["الرصيد المستحق للمصلحة" if ar else "Balance owed to the Tax Authority", "", "",
                           t["wht_payable_balance"]], "style": "total", "indent": 0})
    rows.append({"cells": ["ضريبة الخصم والتحصيل — المخصومة بواسطة العملاء" if ar else
                           "WITHHOLDING TAX DEDUCTED BY CLIENTS (tax credit)", "", "", ""], "style": "section", "indent": 0})
    for x in t["wht_deducted_by_clients"]:
        rows.append({"cells": [x["party"] or "—", x["tax_id"], "", x["amount"]], "style": "item", "indent": 1})
    rows.append({"cells": ["رصيد دائن لدى المصلحة" if ar else "Tax credit balance", "", "", t["wht_receivable_balance"]],
                 "style": "total", "indent": 0})
    pers = [("", date_from or "…", acc.clean_date(date_to))]
    return {"title": "الإقرار الضريبي — ضريبة القيمة المضافة والخصم والتحصيل" if ar else "Tax report — VAT & withholding",
            "subtitle": _subtitle(db, lang, pers, None), "columns": cols, "rows": rows, "lang": lang}


def build(db, name, p):
    """Dispatch a report by name with query params dict p."""
    lang = p.get("lang") or "en"
    bu = p.get("bu_id") or None
    if name == "income_statement":
        return income_statement_table(db, p.get("date_from"), p.get("date_to"), p.get("compare"), bu,
                                      p.get("detail", "1") != "0", lang, p.get("allocate", "1") != "0")
    if name == "balance_sheet":
        return balance_sheet_table(db, p.get("date_to"), p.get("date_from"), p.get("compare"), bu, lang)
    if name == "cash_flow":
        return cash_flow_table(db, p.get("date_from"), p.get("date_to"), p.get("compare"), bu, lang)
    if name == "trial_balance":
        return trial_balance_table(db, p.get("date_from"), p.get("date_to"), bu, lang)
    if name == "general_ledger":
        return general_ledger_table(db, p.get("account"), p.get("date_from"), p.get("date_to"), bu, p.get("party"), lang)
    if name == "bu_pnl":
        return bu_table(db, p.get("date_from"), p.get("date_to"), p.get("status"), lang)
    if name == "payables":
        return payables_table(db, p.get("date_to"), lang)
    if name == "stock":
        return stock_table(db, p.get("warehouse_id"), p.get("q"), lang)
    if name == "tax":
        return tax_table(db, p.get("date_from"), p.get("date_to"), lang)
    if name == "receivables":
        return receivables_table(db, p.get("date_from"), p.get("date_to"), lang)
    if name == "fixed_assets":
        return fixed_assets_table(db, p.get("date_to"), p.get("q"), p.get("state"), lang)
    raise acc.ValidationError(f"Unknown report {name!r}")


REPORT_PERMS = {"income_statement": "reports.income_statement", "balance_sheet": "reports.balance_sheet",
                "cash_flow": "reports.cash_flow", "trial_balance": "reports.trial_balance",
                "general_ledger": "accounting.ledger", "bu_pnl": "business_units.profitability",
                "payables": "purchasing.payables", "stock": "stock.balances", "receivables": "sales.receivables",
                "tax": "tax.report", "fixed_assets": "assets.register"}


# ==================================================================== writers
def _fmt(v, fmt, lang="en"):
    if v in ("", None):
        return ""
    if fmt == "pct" or (isinstance(v, float) and fmt == "money" and False):
        return f"{v * 100:.1f}%"
    if isinstance(v, (int, float)):
        if fmt == "num":
            return f"{v:,.2f}".rstrip("0").rstrip(".")
        s = f"{abs(v):,.2f}"
        return f"({s})" if v < 0 else s
    return str(v)


def _cell_fmt(table, row, i):
    if row["style"] == "note" and i > 0 and isinstance(row["cells"][i], float) and abs(row["cells"][i]) <= 10:
        return "pct"
    return table["columns"][i].get("fmt")


def to_html(table, company="", auto_print=False, font="system", size=12, logo=None, wm=None):
    rtl = table.get("lang") == "ar"
    head = "".join(f'<th class="{c.get("align", "left")}">{html.escape(str(c["label"]))}</th>'
                   for c in table["columns"])
    body = []
    for r in table["rows"]:
        tds = []
        for i, v in enumerate(r["cells"]):
            f = _cell_fmt(table, r, i)
            cls = table["columns"][i].get("align", "left")
            neg = " neg" if isinstance(v, (int, float)) and v < 0 else ""
            pad = f' style="padding-inline-start:{8 + 16 * r.get("indent", 0)}px"' if i == 0 else ""
            tds.append(f'<td class="{cls}{neg}"{pad}>{html.escape(_fmt(v, f))}</td>')
        body.append(f'<tr class="{r["style"]}">{"".join(tds)}</tr>')
    fam = "Arial, 'Segoe UI', Tahoma, sans-serif" if font in ("system", "", None) else f"'{font}', Arial, sans-serif"
    return f"""<!doctype html><html lang="{'ar' if rtl else 'en'}" dir="{'rtl' if rtl else 'ltr'}"><head><meta charset="utf-8">
<title>{html.escape(table['title'])}</title><style>
@page {{ size: A4 {"landscape" if len(table['columns']) > 6 else "portrait"}; margin: 14mm 12mm; }}
body{{font-family:{fam};font-size:{size}px;color:#111;margin:24px}}
h1{{font-size:{size + 8}px;margin:0}} .co{{font-weight:700;color:#1f3a5f;letter-spacing:.5px}}
.sub{{color:#555;margin:4px 0 14px}} table{{border-collapse:collapse;width:100%}}
th{{background:#1f3a5f;color:#fff;padding:7px 8px;font-size:{size - 1}px}} td{{padding:5px 8px;border-bottom:1px solid #e5e7eb}}
.right{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;direction:ltr;unicode-bidi:isolate}} .left{{text-align:start}}
[dir=rtl] .right{{text-align:left}}
tr.section td{{background:#dfe6ee;font-weight:700;text-transform:uppercase}} tr.header td{{font-weight:700;color:#1f3a5f}}
tr.subtotal td{{font-weight:600;border-top:1px solid #9ca3af}} tr.total td{{font-weight:700;background:#f3f4f6;border-top:1px solid #111}}
tr.grand td{{font-weight:800;background:#e8eef6;border-top:2px solid #111;border-bottom:3px double #111}}
tr.note td{{color:#6b7280;font-style:italic;font-size:{size - 1}px}} .neg{{color:#b00020}}
.foot{{margin-top:16px;color:#888;font-size:{size - 2}px}} @media print{{body{{margin:0}} .noprint{{display:none}} .wm{{position:fixed}}}}
.hd{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}} .hd img{{max-height:80px;max-width:220px}}
.wm{{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:55%;max-width:520px;z-index:-1;pointer-events:none}}
</style></head><body>{f'<img class="wm" src="data:image/png;base64,{wm["data"]}" alt="">' if wm else ''}
<div class="hd"><div><div class="co">{html.escape(company)}</div><h1>{html.escape(table['title'])}</h1>
<div class="sub">{html.escape(table.get('subtitle', ''))}</div></div>{f'<img src="data:{logo["mime"]};base64,{logo["data"]}" alt="">' if logo else ''}</div>
<button class="noprint" onclick="window.print()" style="margin-bottom:10px">Print / Save as PDF</button>
<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>
<div class="foot">Generated {_dt.datetime.now():%Y-%m-%d %H:%M}</div>
{'<script>window.onload=()=>setTimeout(()=>window.print(),300)</script>' if auto_print else ''}</body></html>"""


def to_xlsx(table, company=""):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    except ImportError:
        raise acc.ValidationError("Excel export needs openpyxl:  python -m pip install openpyxl") from None
    wb = Workbook()
    ws = wb.active
    ws.title = table["title"][:31].replace("/", "-").replace("(", "").replace(")", "")[:31]
    rtl = table.get("lang") == "ar"
    ws.sheet_view.rightToLeft = rtl
    ws.sheet_view.showGridLines = False
    n = len(table["columns"])
    navy, band, light = "1F3A5F", "DFE6EE", "F3F4F6"
    ws.cell(1, 1, company).font = Font(name="Arial", bold=True, size=11, color=navy)
    ws.cell(2, 1, table["title"]).font = Font(name="Arial", bold=True, size=14)
    ws.cell(3, 1, table.get("subtitle", "")).font = Font(name="Arial", size=9, color="555555")
    thin = Side(style="thin", color="111111")
    for i, c in enumerate(table["columns"], start=1):
        cell = ws.cell(5, i, c["label"])
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.alignment = Alignment(horizontal="right" if c.get("align") == "right" else "left", wrap_text=True)
    r0 = 6
    for ri, row in enumerate(table["rows"]):
        st = row["style"]
        for ci, v in enumerate(row["cells"], start=1):
            f = _cell_fmt(table, row, ci - 1)
            cell = ws.cell(r0 + ri, ci, v if v != "" else None)
            bold = st in ("section", "header", "subtotal", "total", "grand")
            cell.font = Font(name="Arial", bold=bold, italic=st == "note", color="6B7280" if st == "note" else "111111")
            if isinstance(v, (int, float)):
                cell.number_format = "0.0%" if f == "pct" else ("#,##0.00;[Red](#,##0.00);-" if f == "money" else "#,##0.##")
                cell.alignment = Alignment(horizontal="right")
            if ci == 1 and row.get("indent"):
                cell.alignment = Alignment(indent=row["indent"] * 2)
            if st == "section":
                cell.fill = PatternFill("solid", fgColor=band)
            elif st in ("total", "grand"):
                cell.fill = PatternFill("solid", fgColor=light if st == "total" else band)
                cell.border = Border(top=thin, bottom=Side(style="double", color="111111") if st == "grand" else None)
            elif st == "subtotal":
                cell.border = Border(top=Side(style="thin", color="9CA3AF"))
    ws.column_dimensions["A"].width = 48
    from openpyxl.utils import get_column_letter
    for i in range(2, n + 1):
        ws.column_dimensions[get_column_letter(i)].width = 18
    ws.freeze_panes = "B6"
    ws.page_setup.orientation = "landscape" if n > 6 else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _x(s):
    return html.escape(str(s), quote=False)


def to_docx(table, company="", logo=None, wm=None):
    """Minimal but valid Word document (stdlib only); optional logo (top) and watermark (page header)."""
    rtl = table.get("lang") == "ar"
    bidi = "<w:bidi/>" if rtl else ""
    rtl_run = "<w:rtl/>" if rtl else ""
    n = len(table["columns"])
    landscape = n > 6
    page_w, page_h = (16838, 11906) if landscape else (11906, 16838)
    usable = page_w - 1440
    first = int(usable * (0.34 if n > 3 else 0.5))
    other = int((usable - first) / max(n - 1, 1))
    widths = [first] + [other] * (n - 1)

    def para(t, bold=False, size=20, color="111111", align=None, italic=False):
        jc = f'<w:jc w:val="{align}"/>' if align else ""
        return (f'<w:p><w:pPr>{bidi}{jc}<w:spacing w:after="60"/></w:pPr><w:r><w:rPr>'
                f'<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>{"<w:b/><w:bCs/>" if bold else ""}'
                f'{"<w:i/>" if italic else ""}<w:color w:val="{color}"/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
                f'{rtl_run}</w:rPr><w:t xml:space="preserve">{_x(t)}</w:t></w:r></w:p>')

    def cell(t, w, bold=False, fill=None, align="left", color="111111", italic=False, indent=0, top=None, bottom=None):
        shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
        borders = ""
        if top or bottom:
            borders = "<w:tcBorders>" + (f'<w:top w:val="{top}" w:sz="6" w:color="111111"/>' if top else "") + \
                      (f'<w:bottom w:val="{bottom}" w:sz="6" w:color="111111"/>' if bottom else "") + "</w:tcBorders>"
        jc = {"right": "end" if not rtl else "start", "left": "start" if not rtl else "end"}.get(align, "start")
        jc = "right" if align == "right" else "left"
        if rtl:
            jc = "left" if align == "right" else "right"
        ind = f'<w:ind w:start="{indent * 240}"/>' if indent else ""
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{borders}{shd}</w:tcPr><w:p><w:pPr>{bidi}'
                f'<w:jc w:val="{jc}"/>{ind}<w:spacing w:after="0"/></w:pPr><w:r><w:rPr>'
                f'<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>{"<w:b/><w:bCs/>" if bold else ""}'
                f'{"<w:i/>" if italic else ""}<w:color w:val="{color}"/><w:sz w:val="17"/><w:szCs w:val="17"/>{rtl_run}'
                f'</w:rPr><w:t xml:space="preserve">{_x(t)}</w:t></w:r></w:p></w:tc>')

    rows = ['<w:tr><w:trPr><w:tblHeader/></w:trPr>' +
            "".join(cell(c["label"], widths[i], True, "1F3A5F", c.get("align", "left"), "FFFFFF")
                    for i, c in enumerate(table["columns"])) + "</w:tr>"]
    for r in table["rows"]:
        st = r["style"]
        fill = {"section": "DFE6EE", "total": "F3F4F6", "grand": "DFE6EE"}.get(st)
        bold = st in ("section", "header", "subtotal", "total", "grand")
        rows.append("<w:tr>" + "".join(
            cell(_fmt(v, _cell_fmt(table, r, i)), widths[i], bold, fill, table["columns"][i].get("align", "left"),
                 "6B7280" if st == "note" else ("B00020" if isinstance(v, (int, float)) and v < 0 else "111111"),
                 st == "note", r.get("indent", 0) if i == 0 else 0,
                 "single" if st in ("subtotal", "total", "grand") else None, "double" if st == "grand" else None)
            for i, v in enumerate(r["cells"])) + "</w:tr>")
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    tbl = (f'<w:tbl><w:tblPr>{"<w:bidiVisual/>" if rtl else ""}<w:tblW w:w="{usable}" w:type="dxa"/>'
           f'<w:tblBorders><w:insideH w:val="single" w:sz="2" w:color="E5E7EB"/></w:tblBorders>'
           f'<w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{"".join(rows)}</w:tbl>')
    logo_xml = ""
    if logo:
        h = 60
        w = round(h * (logo["w"] or 1) / (logo["h"] or 1))
        logo_xml = (f'<w:p><w:pPr>{bidi}<w:jc w:val="{"left" if rtl else "right"}"/><w:spacing w:after="0"/></w:pPr><w:r><w:drawing>'
                    f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{w * 9525}" cy="{h * 9525}"/><wp:docPr id="1" '
                    'name="Logo"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
                    '<pic:nvPicPr><pic:cNvPr id="1" name="Logo"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="rIdLogo"/>'
                    '<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/>'
                    f'<a:ext cx="{w * 9525}" cy="{h * 9525}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
                    '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')
    body = (logo_xml + para(company, True, 22, "1F3A5F") + para(table["title"], True, 32) +
            para(table.get("subtitle", ""), size=18, color="555555") + tbl +
            para(f"Generated {_dt.datetime.now():%Y-%m-%d %H:%M}", size=16, color="888888"))
    orient = ' w:orient="landscape"' if landscape else ""
    from .documents import REL, SHAPETYPE75, b64bytes, rels
    ns = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
          'officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
          'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/'
          '2006/picture" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
          'xmlns:w10="urn:schemas-microsoft-com:office:word"')
    doc_rels, parts, overrides = [], [], ""
    hdr_ref = ""
    if logo:
        ext = "png" if logo["mime"] == "image/png" else "jpeg"
        parts.append((f"word/media/logo.{ext}", b64bytes(logo["data"])))
        doc_rels.append(f'<Relationship Id="rIdLogo" Type="{REL}image" Target="media/logo.{ext}"/>')
    if wm:
        parts.append(("word/media/watermark.png", b64bytes(wm["data"])))
        hpt = 300
        parts.append(("word/_rels/header1.xml.rels", rels([f'<Relationship Id="rIdWm" Type="{REL}image" Target="media/watermark.png"/>'])))
        parts.append(("word/header1.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {ns}><w:p><w:r><w:pict>'
                      f'{SHAPETYPE75}<v:shape id="WordPictureWatermark1" type="#_x0000_t75" style="position:absolute;margin-left:0;'
                      f'margin-top:0;width:{hpt}pt;height:{round(hpt * (wm["h"] or 1) / (wm["w"] or 1))}pt;z-index:-251656192;'
                      'mso-position-horizontal:center;mso-position-horizontal-relative:margin;mso-position-vertical:center;'
                      'mso-position-vertical-relative:margin" o:allowincell="f"><v:imagedata r:id="rIdWm" o:title="watermark"/>'
                      '</v:shape></w:pict></w:r></w:p></w:hdr>'))
        doc_rels.append(f'<Relationship Id="rIdHdr" Type="{REL}header" Target="header1.xml"/>')
        overrides = ('<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                     'wordprocessingml.header+xml"/>')
        hdr_ref = '<w:headerReference w:type="default" r:id="rIdHdr"/>'
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {ns}><w:body>' + body +
           f'<w:sectPr>{hdr_ref}<w:pgSz w:w="{page_w}" w:h="{page_h}"{orient}/><w:pgMar w:top="850" w:right="720" '
           'w:bottom="850" w:left="720" w:header="400" w:footer="400" w:gutter="0"/></w:sectPr></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
                   'package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" '
                   'ContentType="image/png"/><Default Extension="jpeg" ContentType="image/jpeg"/><Override '
                   'PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                   'wordprocessingml.document.main+xml"/>' + overrides + '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                   'openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '</Relationships>')
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", rels(doc_rels))
        for name, data in parts:
            z.writestr(name, data)
    return buf.getvalue()


def to_csv(table):
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c["label"] for c in table["columns"]])
    for r in table["rows"]:
        w.writerow(["" if v is None else v for v in r["cells"]])
    return ("﻿" + buf.getvalue()).encode("utf-8")
