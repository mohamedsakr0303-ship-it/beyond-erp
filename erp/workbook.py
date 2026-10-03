"""Build the Excel mirror of the ERP — data sheets + live-formula statements.

The workbook is both (a) a full export of the books and (b) the import
template: its data sheets use exactly the interchange columns, and the
statement sheets (Trial Balance, Income Statement, Balance Sheet, Cash Flow,
Business-Unit Profitability, Partner Equity, Model Audit) are plain formulas
over those sheets, so the file keeps working on its own in Excel/LibreOffice.

Conventions: blue font = input, black = formula, grey columns = helper
formulas (grouped/collapsed), Arial throughout.
"""
import datetime as _dt
import io
import re

from . import accounting as acc
from .db import SUBTYPES, to_major
from .interchange import FIRST_DATA_ROW, HEADER_ROW, POSTING_SHEETS, ROLE, SHEETS, export_data, input_cols

# the original BEYOND workbook palette (V0.6.1): navy headers, light-blue bands, brown cover
NAVY, BAND, LIGHT, GREY, BLUE = "1F2A44", "DCE6F1", "F2F2F2", "EDEDED", "0000FF"
BROWN, TAUPE, CREAM = "443328", "A48E78", "FFFDE7"
MONEY = '#,##0.00;(#,##0.00);"-"'
PCT = "0.0%"
DATE = "yyyy-mm-dd"

# legs' party column per sheet (for the Payables sheet)
PARTY_COL = {"Direct Costs": "vendor", "Operating Expenses": "vendor", "Payroll": "worker",
             "Payment Vouchers": "party", "Stock Moves": "vendor", "Journal Vouchers": "party",
             "Receipt Vouchers": "party", "Partner Transactions": "partner", "Sales Invoices": "customer",
             "Loans": "party", "Cash Custody": "holder", "Custody Expenses": "supplier", "Rental Stock Lots": "supplier",
             "Sales Contracts": "customer", "Recurring Entries": "party", "Service Subscriptions": "customer"}

EXAMPLES = {
    "Settings": {"key": "company_name", "value": "BEYOND DESIGN"},
    "Chart of Accounts": {"code": "6095", "name": "Cleaning & Hospitality", "type": "Expense",
                          "subtype": "general_admin", "role": "", "active": "Yes"},
    "Tax Codes": {"code": "VAT14", "name": "VAT 14% (standard)", "name_ar": "ضريبة القيمة المضافة 14%", "kind": "vat",
                  "rate": "14%", "eta_type": "T1", "eta_subtype": "V009", "active": "Yes"},
    "Parties": {"name": "Palm Hills Developments", "type": "customer", "receiver_type": "B", "tax_id": "123456789",
                "country": "EG", "governate": "Giza", "region": "6th of October", "street": "26th of July Axis",
                "building": "12", "default_vat": "VAT14", "default_wht": "WHT1-C"},
    "Business Units": {"code": "PRJ-2026-004", "name": "Villa 12 — Mr. Aly", "type": "Villa", "location": "New Cairo",
                       "area": 420, "start_date": "2026-10-01", "status": "In Progress", "basis": "Cost-Plus %",
                       "fee_pct": "25%", "contract_value": "", "overhead_pct": "30%"},
    "Partners": {"name": "Partner name", "default_share": "25%", "active": "Yes"},
    "Fixed Assets": {"code": "FA-0001", "name": "Site pickup truck", "account": "1420", "acquired": "2026-01-15",
                     "cost": 600000, "life_months": 60},
    "Partner Shares": {"partner": "Partner name", "bu_code": "PRJ-2026-004", "share": "25%"},
    "Warehouses": {"name": "Site Store — Villa 12", "location": "New Cairo", "account": "1200"},
    "Stock Items": {"name": "Ceramic tiles 60×60", "unit": "m2", "sku": "CT-6060", "reorder_level": 50},
    "Products": {"code": "P-0001", "name": "Apartment A-301", "kind": "unit", "category": "Apartments", "unit": "unit",
                 "price": 2400000, "status": "available", "specs": "Area: 150 m²; Floor: 3; Bedrooms: 3"},
    "Price List": {"category": "أعمال البياض", "item_no": "3.1", "description": "بياض حوائط", "unit": "م٢",
                   "cost": 219.23, "markup": "30%", "price": ""},
    "BOQ Lines": {"title": "Interior works", "bu_code": "PRJ-2026-004", "rev": "Rev 01", "date": "2026-10-01",
                  "category": "أعمال البياض", "item_no": "3.1", "description": "بياض حوائط", "unit": "م٢", "qty": 320,
                  "price": 285, "cost": 219.23},
    "Direct Costs": {"date": "2026-10-03", "bu_code": "PRJ-2026-004", "account": "5010", "description": "Cement 50 kg",
                     "vendor": "El-Masry Supplies", "qty": 16, "unit": "bag", "rate": 240, "paid": 2000,
                     "method": "Cash", "vat_code": "VAT14", "wht_code": "WHT1-S"},
    "Operating Expenses": {"date": "2026-10-01", "bu_code": "", "account": "6010", "description": "Office rent — Oct",
                           "vendor": "Landlord", "amount": 15000, "paid": "", "method": "Bank Transfer"},
    "Payroll": {"date": "2026-10-31", "bu_code": "PRJ-2026-004", "worker": "Ahmed Ali", "role": "Site engineer",
                "cost_type": "Direct Labour (Cost of Revenue)", "period": "2026-10", "basis": "Monthly", "qty": 1,
                "rate": 12000, "paid": 12000},
    "Receipt Vouchers": {"date": "2026-10-05", "bu_code": "PRJ-2026-004", "ref": "RV-001", "memo": "Advance payment",
                         "amount": 100000, "method": "Bank Transfer", "credit_account": "2100"},
    "Sales Invoices": {"number": "INV-2026-001", "date": "2026-10-30", "bu_code": "PRJ-2026-004",
                       "status": "Posted", "customer": "Palm Hills Developments", "vat_code": "VAT14",
                       "wht_code": "WHT1-C"},
    "Invoice Lines": {"number": "INV-2026-001", "category": "أعمال البياض", "item_no": "3.1",
                      "description": "بياض حوائط", "unit": "م٢", "qty": 150, "price": 285},
    "Payment Vouchers": {"date": "2026-10-10", "party": "El-Masry Supplies", "against": "2010", "amount": 1840,
                         "method": "Cash", "ref": "PV-001"},
    "Partner Transactions": {"date": "2026-10-01", "partner": "Partner name", "type": "Capital Contribution",
                             "amount": 250000, "method": "Bank Transfer"},
    "Stock Moves": {"date": "2026-10-02", "type": "Receipt", "warehouse": "Main Store", "item": "Ceramic tiles 60×60",
                    "unit": "m2", "qty": 200, "rate": 310, "vendor": "Cleopatra", "paid": 62000},
    "Journal Vouchers": {"voucher": "JV-001", "date": "2026-10-31", "kind": "Depreciation", "memo": "October depreciation",
                         "account": "7010", "debit": 2500, "credit": ""},
}


def _need():
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise acc.ValidationError("Excel export needs openpyxl:  python -m pip install openpyxl") from None


def q(name):
    return f"'{name}'"


def build_workbook(db, template=False, data=None):
    _need()
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as L
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation

    given = data
    data = base = export_data(db)
    if given is not None:   # a ready-made set of rows (e.g. the demo company) in the import layout
        data = {k: given.get(k, v if k in ("Chart of Accounts", "Tax Codes") else []) for k, v in data.items()}
        # the statements read the report period from Settings: keep it (on top) when the given rows leave it out
        have = {r.get("key") for r in data["Settings"]}
        period = [r for r in base["Settings"] if r["key"] in ("report_from", "report_to") and r["key"] not in have]
        data["Settings"] = period + list(data["Settings"])
    elif template:
        keep = {"Settings", "Chart of Accounts", "Warehouses", "Tax Codes"}
        data = {k: (v if k in keep else []) for k, v in data.items()}
    company = db.setting("company_name", "BEYOND DESIGN")
    currency = db.setting("currency", "EGP")
    wb = Workbook()
    F = lambda **k: Font(name="Arial", **k)  # noqa: E731
    fill = lambda c: PatternFill("solid", fgColor=c)  # noqa: E731
    thin = Side(style="thin", color="9CA3AF")

    # ---------------------------------------------------------------- cover
    cover = wb.active
    cover.title = "Cover"
    cover.sheet_view.showGridLines = False
    cover.column_dimensions["A"].width = 3
    cover.column_dimensions["B"].width = 34
    cover.column_dimensions["C"].width = 70
    cover["B2"] = company
    cover["B2"].font = F(bold=True, size=22, color=NAVY)
    cover["B3"] = db.setting("company_tagline", "")
    cover["B3"].font = F(size=12, color="555555")
    cover["B4"] = "ERP workbook — books, statements and import template  |  دفتر الحسابات والقوائم المالية وقالب الاستيراد"
    cover["B4"].font = F(size=11, bold=True)
    cover["B5"] = f"Generated {_dt.datetime.now():%Y-%m-%d %H:%M} · all amounts in {currency}" + \
                  (" · EMPTY TEMPLATE" if template else "")
    cover["B5"].font = F(size=9, color="777777")
    legend = [("How to use", "Enter data only in the data sheets (blue text). Statements recalculate automatically. "
                             "Import the whole file back into the app from Import & Export (same sheet and column names)."),
              ("Report period", "Change report_from / report_to on the Settings sheet (rows 5–6)."),
              ("Blue text", "Input cells — safe to edit."), ("Black text", "Formulas — do not overwrite."),
              ("Grey columns", "Helper formulas (collapsed with [+]); copy them down if you add rows beyond the prepared ones."),
              ("Row 3 (grey italic)", "An example of the expected format in every data sheet — not imported."),
              ("Accounts", "Leave account columns blank to use the defaults (by system role), or pick a code."),
              ("Model Audit", "Must show OK on every line before you rely on the statements.")]
    r = 7
    for k, v in legend:
        cover.cell(r, 2, k).font = F(bold=True)
        cover.cell(r, 3, v).font = F(color=BLUE if k == "Blue text" else "111111")
        cover.cell(r, 3).alignment = Alignment(wrap_text=True, vertical="top", horizontal="left", readingOrder=1)
        r += 1
    r += 1
    cover.cell(r, 2, "Sheets / الأوراق").font = F(bold=True, size=12, color=NAVY)
    r += 1
    index_row = r

    # hidden list sheet for validations
    lists = wb.create_sheet("Lists")
    list_ranges = {}

    def list_range(key, values):
        col = len(list_ranges) + 1
        for i, v in enumerate(values, start=1):
            lists.cell(i, col, v)
        rng = f"Lists!${L(col)}$1:${L(col)}${max(len(values), 1)}"
        list_ranges[key] = rng
        return rng

    # ---------------------------------------------------------- data sheets
    maxrow = {}
    colmap = {}
    # [[Sheet.col]] in a formula column: that column of another sheet, over its data rows
    xref = {f"[[{sh['name']}.{c['key']}]]": f"{q(sh['name'])}!${L(i + 1)}${FIRST_DATA_ROW}:${L(i + 1)}$20000"
            for sh in SHEETS for i, c in enumerate(sh["cols"])}
    for s in SHEETS:
        ws = wb.create_sheet(s["name"])
        ws.sheet_view.showGridLines = False
        ws.freeze_panes = ws.cell(FIRST_DATA_ROW, 1)
        rows = data.get(s["name"], [])
        cols = s["cols"]
        letters = {c["key"]: L(i + 1) for i, c in enumerate(cols)}
        colmap[s["name"]] = letters
        n_rows = len(rows)
        spare = 0 if s["name"] in ("Settings", "Chart of Accounts") else 200
        last = FIRST_DATA_ROW + n_rows + spare - 1
        maxrow[s["name"]] = max(last, FIRST_DATA_ROW + n_rows + 2000)
        ws.cell(1, 1, s["name"]).font = F(bold=True, size=14, color=NAVY)
        ws.cell(2, 1, s["title"]).font = F(size=9, color="555555")
        ex = EXAMPLES.get(s["name"], {})
        for i, c in enumerate(cols, start=1):
            h = ws.cell(HEADER_ROW, i, c["en"])
            calc = c["kind"] == "calc"
            h.font = F(bold=True, color="FFFFFF", size=9)
            h.fill = fill("7F8C8D" if calc else NAVY)
            h.alignment = Alignment(wrap_text=True, vertical="center")
            if c.get("note"):
                h.comment = Comment(c["note"], "Beyond ERP")
            if c["key"] in ex:
                e = ws.cell(3, i, ex[c["key"]])
                e.font = F(italic=True, size=8, color="999999")
            ws.column_dimensions[L(i)].width = c["width"]
            if calc:
                ws.column_dimensions[L(i)].outlineLevel = 1 if c["key"].endswith("__eff") or c["width"] <= 10 else 0
        ws.cell(3, 1).value = ws.cell(3, 1).value if ws.cell(3, 1).value is not None else ""
        if ex:
            ws.cell(3, 1).value = ("e.g. " + str(ws.cell(3, 1).value)) if ws.cell(3, 1).value else "e.g."
        ws.row_dimensions[HEADER_ROW].height = 30
        for ri in range(FIRST_DATA_ROW, last + 1):
            row = rows[ri - FIRST_DATA_ROW] if ri - FIRST_DATA_ROW < n_rows else {}
            for i, c in enumerate(cols, start=1):
                cell = ws.cell(ri, i)
                if c["kind"] == "calc":
                    f = c["calc"]
                    if "[[" in f:
                        for tok in set(re.findall(r"\[\[[^\]]+\]\]", f)):
                            f = f.replace(tok, xref[tok])
                    f = f.replace("{row}", str(ri))
                    for k, letter in letters.items():
                        f = f.replace("{" + k + "}", f"{letter}{ri}")
                    cell.value = f
                    cell.font = F(size=9, color="111111")
                    cell.fill = fill(GREY)
                    if c["key"] in ("total", "unpaid", "amount", "value", "posted_amount", "paid_eff", "cr_amt",
                                    "vat_amt", "wht_amt", "ar_amt", "gross", "net_credit", "dep_monthly", "dep_to_date",
                                    "dep_book_value"):
                        cell.number_format = MONEY
                    continue
                v = row.get(c["key"], None)
                if v in ("", None):
                    v = None
                elif c["kind"] == "date":
                    try:
                        v = _dt.date.fromisoformat(str(v)[:10])
                    except ValueError:
                        pass
                elif c["kind"] in ("money", "num", "pct"):
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        pass
                elif c["kind"] in ("acct", "bu", "text", "tax"):
                    v = str(v)
                cell.value = v
                cell.font = F(size=10, color=BLUE)
                if c["kind"] == "date":
                    cell.number_format = DATE
                elif c["kind"] == "money":
                    cell.number_format = MONEY
                elif c["kind"] == "pct":
                    cell.number_format = PCT
        # settings: report period as real dates
        if s["name"] == "Settings":
            for ri in range(FIRST_DATA_ROW, last + 1):
                if ws.cell(ri, 1).value in ("report_from", "report_to"):
                    try:
                        ws.cell(ri, 2).value = _dt.date.fromisoformat(str(ws.cell(ri, 2).value)[:10])
                        ws.cell(ri, 2).number_format = DATE
                    except ValueError:
                        pass
        # validations
        vrows = f"{FIRST_DATA_ROW}:{max(last, FIRST_DATA_ROW + 200)}"
        for i, c in enumerate(cols, start=1):
            rng = f"{L(i)}{FIRST_DATA_ROW}:{L(i)}{max(last, FIRST_DATA_ROW + 200)}"
            if c.get("choices"):
                src = list_range(f"{s['name']}.{c['key']}", c["choices"])
                dv = DataValidation(type="list", formula1=src, allow_blank=True)
            elif c["kind"] == "acct":
                dv = DataValidation(type="list", formula1=f"'Chart of Accounts'!$A${FIRST_DATA_ROW}:$A$3000",
                                    allow_blank=True)
            elif c["kind"] == "tax":
                dv = DataValidation(type="list", formula1=f"'Tax Codes'!$A${FIRST_DATA_ROW}:$A$300", allow_blank=True)
            elif c["kind"] == "bu" and s["name"] != "Business Units":
                dv = DataValidation(type="list", formula1=f"'Business Units'!$A${FIRST_DATA_ROW}:$A$3000",
                                    allow_blank=True)
            elif c["kind"] == "date":
                dv = DataValidation(type="date", operator="greaterThan", formula1="DATE(2000,1,1)", allow_blank=True)
                dv.error = "Enter a date (YYYY-MM-DD)"
            else:
                continue
            dv.showErrorMessage = c["kind"] != "acct"
            ws.add_data_validation(dv)
            dv.add(rng)
        del vrows
        ws.sheet_properties.outlinePr.summaryRight = False
        for i, c in enumerate(cols, start=1):
            if c["kind"] == "calc" and ws.column_dimensions[L(i)].outlineLevel:
                ws.column_dimensions[L(i)].hidden = True
        ws.auto_filter.ref = f"A{HEADER_ROW}:{L(len(cols))}{max(last, FIRST_DATA_ROW)}"
    lists.sheet_state = "hidden"

    # defined names for the report period
    sws = wb["Settings"]
    period_rows = {sws.cell(r, 1).value: r for r in range(FIRST_DATA_ROW, sws.max_row + 1)}
    wb.defined_names["ReportFrom"] = DefinedName("ReportFrom", attr_text=f"Settings!$B${period_rows.get('report_from', 5)}")
    wb.defined_names["ReportTo"] = DefinedName("ReportTo", attr_text=f"Settings!$B${period_rows.get('report_to', 6)}")

    def rng(sheet, key):
        letter = colmap[sheet][key]
        return f"{q(sheet)}!${letter}${FIRST_DATA_ROW}:${letter}${maxrow[sheet]}"

    def leg_sum(acct_cell, side, crit, sheets=None):
        """SUM of SUMIFS over the posting legs of one side. crit: extra criteria string."""
        parts = []
        for s in sheets or POSTING_SHEETS:
            for amt, acct, sd in s["legs"]:
                if sd != side:
                    continue
                parts.append(f"SUMIFS({rng(s['name'], amt)},{rng(s['name'], acct)},{acct_cell}"
                             f"{crit(s['name'])})")
        return "+".join(parts) or "0"

    before = lambda sh: f',{rng(sh, "date")},"<"&ReportFrom'  # noqa: E731
    within = lambda sh: f',{rng(sh, "date")},">="&ReportFrom,{rng(sh, "date")},"<="&ReportTo'  # noqa: E731
    upto = lambda sh: f',{rng(sh, "date")},"<="&ReportTo'  # noqa: E731

    # ------------------------------------------------- per-row helper columns
    # Every posting sheet gets hidden helper columns: the account "Type|statement line" of each leg, and per-row
    # totals for revenue, cost of sales, expenses, shared operating overhead, payables, accruals and bad codes.
    # Statements then sum these with short SUMIFS — keeping every formula far below Excel's 8,192-char limit.
    coa_a = f"'Chart of Accounts'!$A${FIRST_DATA_ROW}:$A$3000"
    OPEX = '{"Expense|employee","Expense|general_admin","Expense|marketing","Expense|utilities","Expense|other_operating"}'
    for s in POSTING_SHEETS:
        ws = wb[s["name"]]
        letters = colmap[s["name"]]
        col = len(s["cols"]) + 1
        last_formula_row = FIRST_DATA_ROW + len(data.get(s["name"], [])) + 199
        heads = []
        for li, (amt, acct, side) in enumerate(s["legs"]):
            heads.append((f"_t{li}", f"leg {li + 1} type", lambda r, a=acct: (
                f'=IFERROR(INDEX(\'Chart of Accounts\'!$C${FIRST_DATA_ROW}:$C$3000,MATCH({letters[a]}{r}&"",{coa_a},0))'
                f'&"|"&INDEX(\'Chart of Accounts\'!$D${FIRST_DATA_ROW}:$D$3000,MATCH({letters[a]}{r}&"",{coa_a},0)),"")')))

        def metric(r, test, debit_positive):
            parts = []
            for li, (amt, acct, side) in enumerate(s["legs"]):
                t = f"{letters['_t' + str(li)]}{r}"
                sign = "" if (side == "D") == debit_positive else "-"
                parts.append(f"IF({test(t)},{sign}N({letters[amt]}{r}),0)")
            return "=" + "+".join(parts)

        heads += [
            ("_rev", "revenue", lambda r: metric(r, lambda t: f'LEFT({t},8)="Revenue|"', False)),
            ("_cogs", "cost of sales", lambda r: metric(r, lambda t: f'LEFT({t},5)="COGS|"', True)),
            ("_exp", "expenses", lambda r: metric(r, lambda t: f'LEFT({t},8)="Expense|"', True)),
            ("_expop", "operating expenses", lambda r: metric(r, lambda t: f"ISNUMBER(MATCH({t},{OPEX},0))", True)),
            ("_ap", "payables", lambda r: metric(r, lambda t: f'{t}="Liability|payable"', False)),
            ("_accr", "accruals", lambda r: metric(r, lambda t: f'{t}="Liability|accrued"', False)),
            ("_ar", "receivables", lambda r: metric(r, lambda t: f'{t}="Asset|receivable"', True)),
            ("_adv", "client advances", lambda r: metric(r, lambda t: f'{t}="Liability|deferred_revenue"', False)),
            ("_bad", "unknown account", lambda r: "=" + "+".join(
                f'IF(AND(N({letters[amt]}{r})<>0,{letters["_t" + str(li)]}{r}=""),1,0)'
                for li, (amt, acct, side) in enumerate(s["legs"]))),
        ]
        for key, label, _ in heads:
            letters[key] = L(col)
            h = ws.cell(HEADER_ROW, col, label)
            h.font = F(bold=True, color="FFFFFF", size=8)
            h.fill = fill("7F8C8D")
            ws.column_dimensions[L(col)].width = 10
            ws.column_dimensions[L(col)].outlineLevel = 1
            ws.column_dimensions[L(col)].hidden = True
            col += 1
        for r in range(FIRST_DATA_ROW, last_formula_row + 1):
            for key, _, fn in heads:
                c = ws.cell(r, column_index(letters[key]), fn(r))
                c.font = F(size=8)
                c.fill = fill(GREY)

    # --------------------------------------------------------- trial balance
    tb = wb.create_sheet("Trial Balance")
    tb.sheet_view.showGridLines = False
    n_acc = len(data["Chart of Accounts"]) + 40
    heads = ["Code / الكود", "Account / الحساب", "Type / النوع", "Statement line / البند", "Opening net (Dr−Cr)",
             "Period debit / مدين الفترة", "Period credit / دائن الفترة", "Closing net (Dr−Cr)",
             "Closing debit / مدين", "Closing credit / دائن"]
    tb["A1"] = "Trial Balance  |  ميزان المراجعة"
    tb["A1"].font = F(bold=True, size=14, color=NAVY)
    tb["A2"] = '="Period "&TEXT(ReportFrom,"yyyy-mm-dd")&" to "&TEXT(ReportTo,"yyyy-mm-dd")'
    tb["A2"].font = F(size=9, color="555555")
    for i, h in enumerate(heads, start=1):
        c = tb.cell(4, i, h)
        c.font = F(bold=True, color="FFFFFF", size=9)
        c.fill = fill(NAVY)
        c.alignment = Alignment(wrap_text=True)
        tb.column_dimensions[L(i)].width = [9, 38, 10, 22, 16, 16, 16, 16, 16, 16][i - 1]
    coa = "'Chart of Accounts'"
    tb_first, tb_last = 5, 4 + n_acc
    for k in range(n_acc):
        r = tb_first + k
        src = FIRST_DATA_ROW + k
        tb.cell(r, 1, f'=IF({coa}!A{src}="","",{coa}!A{src}&"")')
        tb.cell(r, 2, f'=IF(A{r}="","",{coa}!B{src})')
        tb.cell(r, 3, f'=IF(A{r}="","",{coa}!C{src})')
        tb.cell(r, 4, f'=IF(A{r}="","",{coa}!D{src})')
        hc = 12  # hidden helper block starts at column L: per posting sheet opening / period Dr / period Cr
        for sh in POSTING_SHEETS:
            one = [sh]
            tb.cell(r, hc, f'=IF($A{r}="",0,({leg_sum(f"$A{r}", "D", before, one)})-({leg_sum(f"$A{r}", "C", before, one)}))')
            tb.cell(r, hc + 1, f'=IF($A{r}="",0,{leg_sum(f"$A{r}", "D", within, one)})')
            tb.cell(r, hc + 2, f'=IF($A{r}="",0,{leg_sum(f"$A{r}", "C", within, one)})')
            hc += 3
        n_help = 3 * len(POSTING_SHEETS)
        tb.cell(r, 5, "=" + "+".join(f"{L(12 + 3 * i)}{r}" for i in range(len(POSTING_SHEETS))))
        tb.cell(r, 6, "=" + "+".join(f"{L(13 + 3 * i)}{r}" for i in range(len(POSTING_SHEETS))))
        tb.cell(r, 7, "=" + "+".join(f"{L(14 + 3 * i)}{r}" for i in range(len(POSTING_SHEETS))))
        tb.cell(r, 8, f"=E{r}+F{r}-G{r}")
        tb.cell(r, 9, f"=MAX(H{r},0)")
        tb.cell(r, 10, f"=MAX(-H{r},0)")
        for ci in range(1, 11):
            tb.cell(r, ci).font = F(size=9)
            if ci >= 5:
                tb.cell(r, ci).number_format = MONEY
    tr = tb_last + 1
    tb.cell(tr, 2, "TOTAL / الإجمالي").font = F(bold=True)
    for ci in range(5, 11):
        c = tb.cell(tr, ci, f"=SUM({L(ci)}{tb_first}:{L(ci)}{tb_last})")
        c.font = F(bold=True)
        c.number_format = MONEY
        c.border = Border(top=thin, bottom=Side(style="double"))
    tb.freeze_panes = "C5"
    for i, sh in enumerate(POSTING_SHEETS):
        for j, lbl in enumerate(("opening", "period Dr", "period Cr")):
            c = tb.cell(4, 12 + 3 * i + j, f"{sh['name']} {lbl}")
            c.font = F(bold=True, color="FFFFFF", size=7)
            c.fill = fill("7F8C8D")
    for ci in range(12, 12 + n_help):
        tb.column_dimensions[L(ci)].outlineLevel = 1
        tb.column_dimensions[L(ci)].hidden = True
    TBR = lambda col: f"'Trial Balance'!${col}${tb_first}:${col}${tb_last}"  # noqa: E731
    ROLE_F = ROLE

    def by_sub(col, subs, sign=1):
        subs = [subs] if isinstance(subs, str) else subs
        s = "+".join(f'SUMIFS({TBR(col)},{TBR("D")},"{x}")' for x in subs)
        return f"{'-' if sign < 0 else ''}({s})"

    def period_net(subs, sign=1):
        """Period movement for statement lines: sign 1 = debit − credit, −1 = credit − debit."""
        a, b = (by_sub("F", subs), by_sub("G", subs)) if sign > 0 else (by_sub("G", subs), by_sub("F", subs))
        return f"(({a})-({b}))"

    closing = lambda subs, sign=1: by_sub("H", subs, sign)  # noqa: E731
    opening = lambda subs, sign=1: by_sub("E", subs, sign)  # noqa: E731

    def statement(title, ar, rows, first_col_width=52):
        ws = wb.create_sheet(title)
        ws.sheet_view.showGridLines = False
        ws.column_dimensions["A"].width = first_col_width
        ws.column_dimensions["B"].width = 20
        ws.column_dimensions["C"].width = 12
        ws["A1"] = company
        ws["A1"].font = F(bold=True, color=NAVY)
        ws["A2"] = f"{title}  |  {ar}"
        ws["A2"].font = F(bold=True, size=14)
        ws["A3"] = '="Period "&TEXT(ReportFrom,"yyyy-mm-dd")&" to "&TEXT(ReportTo,"yyyy-mm-dd")&" · ' + currency + '"'
        ws["A3"].font = F(size=9, color="555555")
        ws.cell(5, 1, "").fill = fill(NAVY)
        hb = ws.cell(5, 2, currency)
        hb.font = F(bold=True, color="FFFFFF")
        hb.fill = fill(NAVY)
        hb.alignment = Alignment(horizontal="right")
        ws.cell(5, 3, "% of net sales").font = F(bold=True, color="FFFFFF", size=8)
        ws.cell(5, 3).fill = fill(NAVY)
        refs = {}
        r = 6
        for item in rows:
            key, label, formula, style = item[0], item[1], item[2], item[3] if len(item) > 3 else "item"
            c = ws.cell(r, 1, label)
            c.font = F(bold=style in ("section", "subtotal", "total", "grand"), size=10)
            if style == "item":
                c.alignment = Alignment(indent=2)
            if formula is not None:
                f = formula
                for k2, rr in refs.items():
                    f = f.replace("{" + k2 + "}", f"B{rr}")
                v = ws.cell(r, 2, f)
                v.number_format = MONEY
                v.font = F(bold=style in ("subtotal", "total", "grand"))
                if refs.get("net_sales") and title == "Income Statement":
                    p = ws.cell(r, 3, f"=IFERROR(B{r}/B{refs['net_sales']},0)")
                    p.number_format = PCT
                    p.font = F(size=8, color="777777")
            for ci in (1, 2, 3):
                cell = ws.cell(r, ci)
                if style == "section":
                    cell.fill = fill(BAND)
                elif style in ("total", "grand"):
                    cell.fill = fill(LIGHT if style == "total" else BAND)
                    cell.border = Border(top=Side(style="thin"), bottom=Side(style="double") if style == "grand" else None)
                elif style == "subtotal":
                    cell.border = Border(top=thin)
            if key:
                refs[key] = r
            r += 1
        return ws, refs

    # ------------------------------------------------------ income statement
    pl_rows = [
        (None, "Revenue / الإيرادات", None, "section"),
        ("gross_sales", "Gross sales (operating revenue) / إجمالي المبيعات", period_net("operating_revenue", -1)),
        ("discounts", "Less: sales discounts & allowances / خصومات المبيعات", period_net("sales_discount", 1)),
        ("net_sales", "Net sales / صافي المبيعات", "={gross_sales}-{discounts}", "subtotal"),
        ("cogs", "Cost of sales / تكلفة المبيعات", period_net("cogs", 1)),
        ("gp", "Gross profit / مجمل الربح", "={net_sales}-{cogs}", "total"),
        (None, "Operating expenses / المصروفات التشغيلية", None, "section"),
        ("emp", "Employee expenses / مصروفات العاملين", period_net("employee", 1)),
        ("ga", "General & administrative expenses / مصروفات عمومية وإدارية", period_net("general_admin", 1)),
        ("mkt", "Marketing & advertising expenses / مصروفات تسويق", period_net("marketing", 1)),
        ("util", "Utilities (electricity / telephone) / مرافق", period_net("utilities", 1)),
        ("oth", "Other operating expenses / مصروفات تشغيلية أخرى", period_net("other_operating", 1)),
        ("opex", "Operating expenses / إجمالي المصروفات التشغيلية", "={emp}+{ga}+{mkt}+{util}+{oth}", "subtotal"),
        ("ebitda", "EBITDA", "={gp}-{opex}", "total"),
        ("dep", "Depreciation / الإهلاك", period_net("depreciation", 1)),
        ("amort", "Amortization / الاستهلاك", period_net("amortization", 1)),
        ("ebit", "EBIT / Operating profit / الربح التشغيلي", "={ebitda}-{dep}-{amort}", "total"),
        ("int", "Interest expense / مصروفات الفوائد", period_net("interest", 1)),
        ("nonop", "Other non-operating expenses / مصروفات غير تشغيلية", period_net("other_nonoperating", 1)),
        ("oi", "Other income / إيرادات أخرى", period_net("other_income", -1)),
        ("ebt", "EBT / Profit before tax / الربح قبل الضرائب", "={ebit}-{int}-{nonop}+{oi}", "total"),
        ("tax", "Income tax / ضريبة الدخل", period_net("income_tax", 1)),
        ("net", "Net profit / (loss) / صافي الربح (الخسارة)", "={ebt}-{tax}", "grand"),
    ]
    pl_rows = [(k, lbl, ("=" + f if f and not f.startswith("=") else f), *rest) for k, lbl, f, *rest in pl_rows]
    pl_ws, pl_refs = statement("Income Statement", "قائمة الدخل", pl_rows)

    # ---------------------------------------------------------- balance sheet
    def bs_line(subs, sign=1):
        return "=" + closing(subs, sign)

    bs_rows = [
        (None, "ASSETS / الأصول", None, "section"),
        ("cash", "Cash & cash equivalents / النقدية", bs_line("cash")),
        ("ar", "Accounts receivable (net) / المدينون", bs_line(["receivable", "allowance_doubtful"])),
        ("inv", "Inventory / المخزون", bs_line("inventory")),
        ("pre", "Prepaid expenses & advances / مصروفات مقدمة ودفعات", bs_line("prepaid")),
        ("oca", "Other current assets / أصول متداولة أخرى", bs_line("other_current_asset")),
        ("tca", "Total current assets / الأصول المتداولة", "={cash}+{ar}+{inv}+{pre}+{oca}", "subtotal"),
        ("fa", "Property & equipment (cost) / الأصول الثابتة", bs_line("fixed_asset")),
        ("ad", "Less: accumulated depreciation / مجمع الإهلاك", bs_line("accumulated_depreciation")),
        ("ia", "Intangible assets / أصول غير ملموسة", bs_line("intangible")),
        ("aa", "Less: accumulated amortization / مجمع الاستهلاك", bs_line("accumulated_amortization")),
        ("lti", "Long-term investments / استثمارات طويلة الأجل", bs_line("investment")),
        ("onca", "Other non-current assets / أصول غير متداولة أخرى", bs_line("other_noncurrent_asset")),
        ("tnca", "Total non-current assets / الأصول غير المتداولة", "={fa}+{ad}+{ia}+{aa}+{lti}+{onca}", "subtotal"),
        ("ta", "TOTAL ASSETS / إجمالي الأصول", "={tca}+{tnca}", "grand"),
        (None, "LIABILITIES / الالتزامات", None, "section"),
        ("ap", "Accounts payable / الدائنون", bs_line("payable", -1)),
        ("acc", "Accrued liabilities & payroll / مستحقات", bs_line("accrued", -1)),
        ("def", "Deferred revenue (client advances) / إيرادات مؤجلة", bs_line("deferred_revenue", -1)),
        ("taxp", "Taxes payable / ضرائب مستحقة", bs_line("tax_payable", -1)),
        ("divp", "Profit distributions payable / توزيعات مستحقة", bs_line("dividends_payable", -1)),
        ("ocl", "Other current liabilities / التزامات متداولة أخرى", bs_line("other_current_liability", -1)),
        ("std", "Short-term debt / قروض قصيرة الأجل", bs_line("short_term_debt", -1)),
        ("tcl", "Total current liabilities / الالتزامات المتداولة", "={ap}+{acc}+{def}+{taxp}+{divp}+{ocl}+{std}", "subtotal"),
        ("ltd", "Long-term debt / قروض طويلة الأجل", bs_line("long_term_debt", -1)),
        ("oncl", "Other non-current liabilities / التزامات غير متداولة أخرى", bs_line("other_noncurrent_liability", -1)),
        ("tl", "TOTAL LIABILITIES / إجمالي الالتزامات", "={tcl}+{ltd}+{oncl}", "total"),
        (None, "EQUITY / حقوق الملكية", None, "section"),
        ("cap", "Partner capital / رأس المال", bs_line("capital", -1)),
        ("pcur", "Partners' current accounts / جاري الشركاء", bs_line("partner_current", -1)),
        ("rsv", "Reserves / الاحتياطيات", bs_line("reserves", -1)),
        ("re", "Retained earnings / الأرباح المحتجزة", bs_line("retained_earnings", -1)),
        ("oeq", "Other equity / حقوق ملكية أخرى", bs_line("other_equity", -1)),
        ("dr", "Less: partner drawings / مسحوبات الشركاء", bs_line("drawings", -1)),
        ("ce", "Current earnings (profit to date) / أرباح الفترة حتى تاريخه",
         "=-(" + "+".join(f'SUMIFS({TBR("H")},{TBR("C")},"{t}")' for t in ("Revenue", "COGS", "Expense")) + ")"),
        ("te", "TOTAL EQUITY / إجمالي حقوق الملكية", "={cap}+{pcur}+{rsv}+{re}+{oeq}+{dr}+{ce}", "total"),
        ("tle", "TOTAL LIABILITIES & EQUITY / إجمالي الالتزامات وحقوق الملكية", "={tl}+{te}", "grand"),
        ("chk", "Check: assets − (liabilities + equity) / فرق التوازن", "={ta}-{tle}", "item"),
    ]
    bs_ws, bs_refs = statement("Balance Sheet", "الميزانية العمومية", bs_rows)
    bs_ws["A3"] = '="As of "&TEXT(ReportTo,"yyyy-mm-dd")&" · ' + currency + '"'
    bs_ws.cell(5, 3).value = None

    # --------------------------------------------------------------- cash flow
    def chg(subs):  # cash effect of a balance change in the period = -(debit − credit)
        return "=" + period_net(subs, -1)

    cf_rows = [
        (None, "CASH FLOWS FROM OPERATING ACTIVITIES / التدفقات من الأنشطة التشغيلية", None, "section"),
        ("ni", "Net income / صافي الدخل", f"='Income Statement'!B{pl_refs['net']}", "subtotal"),
        ("dep", "(+) Depreciation / الإهلاك", chg("accumulated_depreciation")),
        ("amo", "(+) Amortization / الاستهلاك", chg("accumulated_amortization")),
        ("dar", "(+/-) Accounts receivable / المدينون", chg(["receivable", "allowance_doubtful"])),
        ("dinv", "(+/-) Inventory / المخزون", chg("inventory")),
        ("dpre", "(+/-) Prepaid expenses & advances / مصروفات مقدمة", chg("prepaid")),
        ("doca", "(+/-) Other current assets / أصول متداولة أخرى", chg("other_current_asset")),
        ("dap", "(+/-) Accounts payable / الدائنون", chg("payable")),
        ("dacc", "(+/-) Accrued liabilities / المستحقات", chg("accrued")),
        ("ddef", "(+/-) Deferred revenue / إيرادات مؤجلة", chg("deferred_revenue")),
        ("dtax", "(+/-) Taxes payable / ضرائب مستحقة", chg("tax_payable")),
        ("docl", "(+/-) Other current liabilities / التزامات متداولة أخرى", chg("other_current_liability")),
        ("ops", "Net cash provided by operating activities / صافي النقد التشغيلي",
         "={ni}+{dep}+{amo}+{dar}+{dinv}+{dpre}+{doca}+{dap}+{dacc}+{ddef}+{dtax}+{docl}", "total"),
        (None, "CASH FLOWS FROM INVESTING ACTIVITIES / التدفقات من الأنشطة الاستثمارية", None, "section"),
        ("capex", "(-) Capital expenditures / النفقات الرأسمالية", chg("fixed_asset")),
        ("intg", "(-) Additions to intangibles / أصول غير ملموسة", chg("intangible")),
        ("lti", "(-) Investments bought / (sold) / استثمارات", chg("investment")),
        ("olta", "(-) Other long-term assets / أصول طويلة الأجل", chg("other_noncurrent_asset")),
        ("inv", "Net cash used in investing activities / صافي النقد الاستثماري", "={capex}+{intg}+{lti}+{olta}", "total"),
        (None, "CASH FLOWS FROM FINANCING ACTIVITIES / التدفقات من الأنشطة التمويلية", None, "section"),
        ("std", "(+/-) Short-term debt / قروض قصيرة الأجل", chg("short_term_debt")),
        ("ltd", "(+/-) Long-term debt / قروض طويلة الأجل", chg("long_term_debt")),
        ("oltl", "(+/-) Other long-term liabilities / التزامات طويلة الأجل", chg("other_noncurrent_liability")),
        ("cap", "(+) Capital contributed / رأس المال", chg("capital")),
        ("pcur", "(+/-) Partners' current accounts / جاري الشركاء", chg("partner_current")),
        ("oeq", "(+/-) Other equity & reserves / حقوق ملكية أخرى واحتياطيات", chg(["other_equity", "reserves"])),
        ("re", "(+/-) Retained earnings adjustments / تسويات الأرباح المحتجزة", chg("retained_earnings")),
        ("div", "(-) Dividends / partner drawings paid / توزيعات ومسحوبات", chg(["drawings", "dividends_payable"])),
        ("fin", "Net cash provided by financing activities / صافي النقد التمويلي",
         "={std}+{ltd}+{oltl}+{cap}+{pcur}+{oeq}+{re}+{div}", "total"),
        ("beg", "Beginning cash / النقدية أول المدة", "=" + opening("cash")),
        ("chg", "(+/-) Change in cash & cash equivalents / التغير في النقدية", "={ops}+{inv}+{fin}"),
        ("end", "Ending cash / النقدية آخر المدة", "={beg}+{chg}", "grand"),
        ("chk", "Check: ending cash − cash per trial balance / فرق المطابقة", "={end}-(" + closing("cash") + ")"),
    ]
    cf_ws, cf_refs = statement("Cash Flow", "قائمة التدفقات النقدية", cf_rows)
    cf_ws.cell(5, 3).value = None

    # ------------------------------------------------------------ tax summary
    def tb_code(col, role):
        return f"SUMIFS({TBR(col)},{TBR('A')},{ROLE_F(role)})"

    tax_rows = [
        (None, "VALUE ADDED TAX / ضريبة القيمة المضافة", None, "section"),
        ("out", "Output VAT charged on sales (period) / ضريبة المخرجات", f"={tb_code('G', 'vat_output')}-{tb_code('F', 'vat_output')}"),
        ("inp", "Input VAT on purchases (period) / ضريبة المدخلات", f"={tb_code('F', 'vat_input')}-{tb_code('G', 'vat_input')}"),
        ("net", "Net VAT payable / (refundable) for the period / صافي الضريبة المستحقة", "={out}-{inp}", "grand"),
        (None, "WITHHOLDING TAX / ضريبة الخصم والتحصيل", None, "section"),
        ("whp", "Deducted from suppliers in the period (to remit) / المخصوم من الموردين",
         f"={tb_code('G', 'wht_payable')}-{tb_code('F', 'wht_payable')}"),
        ("whpb", "Balance still owed to the Tax Authority / الرصيد المستحق للمصلحة", f"=-{tb_code('H', 'wht_payable')}", "subtotal"),
        ("whr", "Deducted by clients in the period (tax credit) / المخصوم بواسطة العملاء",
         f"={tb_code('F', 'wht_receivable')}-{tb_code('G', 'wht_receivable')}"),
        ("whrb", "Tax credit balance / رصيد دائن لدى المصلحة", f"={tb_code('H', 'wht_receivable')}", "subtotal"),
    ]
    tax_ws, _ = statement("Tax Summary", "ملخص الضرائب", tax_rows)
    tax_ws.cell(5, 3).value = None

    # --------------------------------------------------- BU profitability
    bu_ws = wb.create_sheet("BU Profitability")
    bu_ws.sheet_view.showGridLines = False
    bu_ws["A1"] = "Profitability by Business Unit  |  ربحية وحدات الأعمال"
    bu_ws["A1"].font = F(bold=True, size=14, color=NAVY)
    bu_ws["A2"] = '="Period "&TEXT(ReportFrom,"yyyy-mm-dd")&" to "&TEXT(ReportTo,"yyyy-mm-dd")'
    # per-leg statement-line lookups are expensive; instead use the per-leg helper approach via SUMPRODUCT on TB? No:
    # business-unit results need BU-level sums, so we SUMIFS each leg by BU and by the account's type.
    heads = ["Code", "Name", "Revenue", "Direct costs", "Direct overhead", "Overhead %", "Allocated overhead",
             "Net profit", "Margin"]
    for i, h in enumerate(heads, start=1):
        c = bu_ws.cell(4, i, h)
        c.font = F(bold=True, color="FFFFFF", size=9)
        c.fill = fill(NAVY)
        bu_ws.column_dimensions[L(i)].width = [14, 34, 16, 16, 16, 10, 16, 16, 9][i - 1]
    def bu_sum(bu_cell, key):
        parts = [f"SUMIFS({rng(sh['name'], key)},{rng(sh['name'], 'bu_code')},{bu_cell}{within(sh['name'])})"
                 for sh in POSTING_SHEETS if "bu_code" in colmap[sh["name"]]]
        return "+".join(parts) or "0"

    def shared_pool():
        return bu_sum('""', "_expop")

    bu_ws["K4"] = "Shared overhead pool (no business unit, all expense lines) / المصروفات المشتركة"
    bu_ws["K4"].font = F(bold=True, size=9)
    bu_ws["K5"] = "=" + shared_pool()
    bu_ws["K5"].number_format = MONEY
    bu_ws["K6"] = ("Operating expenses booked without a business unit (depreciation, interest and tax are not "
                   "allocated), shared out by each unit's Overhead Allocation %.")
    bu_ws["K6"].font = F(size=8, italic=True, color="777777")
    bu_ws.column_dimensions["K"].width = 40
    n_bu = len(data["Business Units"]) + 30
    bus_sheet = "'Business Units'"
    for k in range(n_bu):
        r, src = 5 + k, FIRST_DATA_ROW + k
        bu_ws.cell(r, 1, f'=IF({bus_sheet}!A{src}="","",{bus_sheet}!A{src}&"")')
        bu_ws.cell(r, 2, f'=IF(A{r}="","",{bus_sheet}!B{src})')
        bu_ws.cell(r, 3, f'=IF(A{r}="",0,{bu_sum(f"$A{r}", "_rev")})')
        bu_ws.cell(r, 4, f'=IF(A{r}="",0,{bu_sum(f"$A{r}", "_cogs")})')
        bu_ws.cell(r, 5, f'=IF(A{r}="",0,{bu_sum(f"$A{r}", "_exp")})')
        bu_ws.cell(r, 6, f'=IF(A{r}="","",N({bus_sheet}!{colmap["Business Units"]["overhead_pct"]}{src}))')
        bu_ws.cell(r, 7, f'=IF(A{r}="",0,$K$5*N(F{r}))')
        bu_ws.cell(r, 8, f"=C{r}-D{r}-E{r}-G{r}")
        bu_ws.cell(r, 9, f"=IFERROR(H{r}/C{r},0)")
        for ci in range(1, 10):
            bu_ws.cell(r, ci).font = F(size=9)
            bu_ws.cell(r, ci).number_format = PCT if ci in (6, 9) else (MONEY if ci >= 3 else "General")
    tr = 5 + n_bu
    bu_ws.cell(tr, 2, "TOTAL").font = F(bold=True)
    for ci in (3, 4, 5, 6, 7, 8):
        c = bu_ws.cell(tr, ci, f"=SUM({L(ci)}5:{L(ci)}{tr - 1})")
        c.font = F(bold=True)
        c.number_format = PCT if ci == 6 else MONEY
    bu_total_row = tr

    # ---------------------------------------------------------- partner equity
    pe = wb.create_sheet("Partner Equity")
    pe.sheet_view.showGridLines = False
    pe["A1"] = "Partner Equity & Distributions  |  حقوق الشركاء والتوزيعات"
    pe["A1"].font = F(bold=True, size=14, color=NAVY)
    pe["A2"] = "Profit share = Σ (business-unit net profit × share) + company-level result × default share; " \
               "distributable = share × (1 − reserve %)"
    pe["A2"].font = F(size=8, color="555555")
    pe["A3"] = "Reserve % / نسبة الاحتياطي"
    pe["B3"] = f'=IFERROR(VALUE(VLOOKUP("reserve_pct",Settings!$A${FIRST_DATA_ROW}:$B$500,2,FALSE)),0.4)'
    pe["B3"].number_format = PCT
    pe["D3"] = "Company-level result (net profit − Σ unit profits) / نتيجة الشركة"
    pe["E3"] = f"='Income Statement'!B{pl_refs['net']}-'BU Profitability'!H{bu_total_row}"
    pe["E3"].number_format = MONEY
    for i, h in enumerate(["Partner", "Default share", "Profit share", "Distributable", "Drawn", "Balance"], start=1):
        c = pe.cell(5, i, h)
        c.font = F(bold=True, color="FFFFFF", size=9)
        c.fill = fill(NAVY)
        pe.column_dimensions[L(i)].width = [30, 12, 16, 16, 16, 16][i - 1]
    n_p = len(data["Partners"]) + 10
    ps = "'Partner Shares'"
    for k in range(n_p):
        r, src = 6 + k, FIRST_DATA_ROW + k
        pe.cell(r, 1, f'=IF(Partners!A{src}="","",Partners!A{src})')
        pe.cell(r, 2, f'=IF(A{r}="","",N(Partners!B{src}))')
        # Σ over business units: net profit × (explicit share, else default share)
        crit = (f"{ps}!$A${FIRST_DATA_ROW}:$A$3000,$A{r},{ps}!$B${FIRST_DATA_ROW}:$B$3000,"
                f"'BU Profitability'!$A$5:$A${bu_total_row - 1}")
        share = f"(SUMIFS({ps}!$C${FIRST_DATA_ROW}:$C$3000,{crit})+(COUNTIFS({crit})=0)*$B{r})"
        pe.cell(r, 3, f'=IF(A{r}="",0,SUMPRODUCT(\'BU Profitability\'!$H$5:$H${bu_total_row - 1},{share})+$E$3*B{r})')
        pe.cell(r, 4, f"=IF(C{r}>0,C{r}*(1-$B$3),C{r})")
        pt = "'Partner Transactions'"
        pl = colmap["Partner Transactions"]
        pe.cell(r, 5, f'=IF(A{r}="",0,SUMIFS({pt}!${pl["amount"]}${FIRST_DATA_ROW}:${pl["amount"]}$5000,'
                      f'{pt}!${pl["partner"]}${FIRST_DATA_ROW}:${pl["partner"]}$5000,A{r},'
                      f'{pt}!${pl["type"]}${FIRST_DATA_ROW}:${pl["type"]}$5000,"Owner Drawing"))')
        pe.cell(r, 6, f"=D{r}-E{r}")
        for ci in range(1, 7):
            pe.cell(r, ci).font = F(size=9)
            pe.cell(r, ci).number_format = PCT if ci == 2 else (MONEY if ci >= 3 else "General")

    # ------------------------------------------------------------------ payables
    pay = wb.create_sheet("Payables")
    pay.sheet_view.showGridLines = False
    pay["A1"] = "Balances owed by party (suppliers & workers)  |  أرصدة الدائنين"
    pay["A1"].font = F(bold=True, size=14, color=NAVY)
    pay["A2"] = "Names come from the data at export time — add a name in column A to track a new party."
    pay["A2"].font = F(size=8, color="555555")
    for i, h in enumerate(["Party", "Payables (2010…)", "Accrued / payroll", "Total owed"], start=1):
        c = pay.cell(4, i, h)
        c.font = F(bold=True, color="FFFFFF", size=9)
        c.fill = fill(NAVY)
        pay.column_dimensions[L(i)].width = [34, 18, 18, 18][i - 1]
    parties = sorted({r["party"] for r in acc.party_balances(db, ("payable", "accrued"))} if not template else [])
    def party_sum(pcell, key):
        parts = [f"SUMIFS({rng(sh['name'], key)},{rng(sh['name'], PARTY_COL[sh['name']])},{pcell}{upto(sh['name'])})"
                 for sh in POSTING_SHEETS if PARTY_COL.get(sh["name"]) in colmap[sh["name"]]]
        return "+".join(parts) or "0"

    if template:  # an empty book: list every name on the Parties sheet (suppliers, subcontractors, drivers…)
        pay["A2"] = "Every name on the Parties sheet is listed here — add a supplier / contact there and its balance appears."
    pr = FIRST_DATA_ROW
    for k in range(len(parties) + 25 if not template else 200):
        r = 5 + k
        if template:
            pay.cell(r, 1, f"=IF(Parties!A{pr + k}=\"\",\"\",Parties!A{pr + k})")
        elif k < len(parties):
            pay.cell(r, 1, parties[k]).font = F(color=BLUE)
        pay.cell(r, 2, f'=IF(A{r}="",0,{party_sum(f"$A{r}", "_ap")})').number_format = MONEY
        pay.cell(r, 3, f'=IF(A{r}="",0,{party_sum(f"$A{r}", "_accr")})').number_format = MONEY
        pay.cell(r, 4, f"=B{r}+C{r}").number_format = MONEY

    # ------------------------------------------------------------------ receivables
    rcv = wb.create_sheet("Receivables")
    rcv.sheet_view.showGridLines = False
    rcv["A1"] = "Balances owed to us by party (customers)  |  أرصدة العملاء"
    rcv["A1"].font = F(bold=True, size=14, color=NAVY)
    rcv["A2"] = ("Every name on the Parties sheet is listed here." if template else
                 "Names come from the data at export time — add a name in column A to track a new customer.")
    rcv["A2"].font = F(size=8, color="555555")
    for i, h in enumerate(["Party", "Receivable (invoices, installments)", "Advances & billed not earned", "Net owed to us"], start=1):
        c = rcv.cell(4, i, h)
        c.font = F(bold=True, color="FFFFFF", size=9)
        c.fill = fill(NAVY)
        rcv.column_dimensions[L(i)].width = [34, 20, 20, 18][i - 1]
    from . import statements
    custs = [] if template else sorted({r["party"] for r in statements.aging(db)["rows"] if r["balance"] > 0})
    for k in range(len(custs) + 25 if not template else 200):
        r = 5 + k
        if template:
            rcv.cell(r, 1, f"=IF(Parties!A{pr + k}=\"\",\"\",Parties!A{pr + k})")
        elif k < len(custs):
            rcv.cell(r, 1, custs[k]).font = F(color=BLUE)
        rcv.cell(r, 2, f'=IF(A{r}="",0,{party_sum(f"$A{r}", "_ar")})').number_format = MONEY
        rcv.cell(r, 3, f'=IF(A{r}="",0,{party_sum(f"$A{r}", "_adv")})').number_format = MONEY
        rcv.cell(r, 4, f"=B{r}-C{r}").number_format = MONEY

    # ------------------------------------------------------------ budget vs actual
    _budget_sheet(wb, colmap, F, fill, NAVY, BLUE, MONEY, PCT, L)

    # ------------------------------------------------------------ quick estimator
    qe = wb.create_sheet("Quick Estimator")
    qe.sheet_view.showGridLines = False
    qe["A1"] = "Quick Estimator  |  التسعير السريع"
    qe["A1"].font = F(bold=True, size=14, color=NAVY)
    qe["A2"] = "Client / العميل"
    qe["B2"] = "Client name"
    qe["B2"].font = F(color=BLUE)
    qe["A3"] = "Pick a description from the Price List; unit, prices and cost fill in automatically."
    qe["A3"].font = F(size=8, color="555555")
    pl_sheet = "'Price List'"
    plc = colmap["Price List"]
    for i, h in enumerate(["Description", "Unit", "Qty", "Client price", "Unit cost", "Line price", "Line cost",
                           "Margin"], start=1):
        c = qe.cell(5, i, h)
        c.font = F(bold=True, color="FFFFFF", size=9)
        c.fill = fill(NAVY)
        qe.column_dimensions[L(i)].width = [60, 9, 10, 14, 14, 16, 16, 9][i - 1]
    look = lambda r, col: (f'=IFERROR(INDEX({pl_sheet}!${plc[col]}${FIRST_DATA_ROW}:${plc[col]}$5000,MATCH($A{r},'  # noqa: E731
                           f'{pl_sheet}!${plc["description"]}${FIRST_DATA_ROW}:${plc["description"]}$5000,0)),"")')
    for r in range(6, 56):
        qe.cell(r, 1).font = F(color=BLUE)
        qe.cell(r, 2, look(r, "unit"))
        qe.cell(r, 3).font = F(color=BLUE)
        qe.cell(r, 4, f'=IFERROR(IF(INDEX({pl_sheet}!${plc["price"]}${FIRST_DATA_ROW}:${plc["price"]}$5000,MATCH($A{r},'
                      f'{pl_sheet}!${plc["description"]}${FIRST_DATA_ROW}:${plc["description"]}$5000,0))="",'
                      f'N(INDEX({pl_sheet}!${plc["cost"]}${FIRST_DATA_ROW}:${plc["cost"]}$5000,MATCH($A{r},'
                      f'{pl_sheet}!${plc["description"]}${FIRST_DATA_ROW}:${plc["description"]}$5000,0)))*(1+N(INDEX('
                      f'{pl_sheet}!${plc["markup"]}${FIRST_DATA_ROW}:${plc["markup"]}$5000,MATCH($A{r},'
                      f'{pl_sheet}!${plc["description"]}${FIRST_DATA_ROW}:${plc["description"]}$5000,0)))),'
                      f'INDEX({pl_sheet}!${plc["price"]}${FIRST_DATA_ROW}:${plc["price"]}$5000,MATCH($A{r},'
                      f'{pl_sheet}!${plc["description"]}${FIRST_DATA_ROW}:${plc["description"]}$5000,0))),0)')
        qe.cell(r, 5, f'=N({look(r, "cost")[1:]})')
        qe.cell(r, 6, f"=N(C{r})*N(D{r})")
        qe.cell(r, 7, f"=N(C{r})*N(E{r})")
        qe.cell(r, 8, f"=IFERROR(1-G{r}/F{r},0)")
        for ci in (4, 5, 6, 7):
            qe.cell(r, ci).number_format = MONEY
        qe.cell(r, 8).number_format = PCT
    dv = DataValidation(type="list", formula1=f"{pl_sheet}!${plc['description']}${FIRST_DATA_ROW}:"
                                              f"${plc['description']}${FIRST_DATA_ROW + max(len(data['Price List']), 1) + 200}",
                        allow_blank=True)
    dv.showErrorMessage = False
    qe.add_data_validation(dv)
    dv.add("A6:A55")
    qe["E57"] = "TOTAL"
    qe["E57"].font = F(bold=True)
    for col in ("F", "G"):
        qe[f"{col}57"] = f"=SUM({col}6:{col}55)"
        qe[f"{col}57"].number_format = MONEY
        qe[f"{col}57"].font = F(bold=True)
    qe["H57"] = "=IFERROR(1-G57/F57,0)"
    qe["H57"].number_format = PCT

    _scaffold_calculator(wb, db, colmap, F, fill, NAVY, BLUE, MONEY, L)

    # ----------------------------------------------------------------- audit
    au = wb.create_sheet("Model Audit")
    au.sheet_view.showGridLines = False
    au["A1"] = "Model Audit  |  مراجعة النموذج"
    au["A1"].font = F(bold=True, size=14, color=NAVY)
    for i, h in enumerate(["Check", "Value A", "Value B", "Difference", "Status"], start=1):
        c = au.cell(3, i, h)
        c.font = F(bold=True, color="FFFFFF", size=9)
        c.fill = fill(NAVY)
        au.column_dimensions[L(i)].width = [58, 16, 16, 14, 12][i - 1]
    jv = colmap["Journal Vouchers"]
    ov = colmap["Business Units"]["overhead_pct"]
    checks = [
        ("Trial balance: closing debits = closing credits", f"='Trial Balance'!I{tr_tb(tb_last)}",
         f"='Trial Balance'!J{tr_tb(tb_last)}"),
        ("Balance sheet: assets = liabilities + equity", f"='Balance Sheet'!B{bs_refs['ta']}",
         f"='Balance Sheet'!B{bs_refs['tle']}"),
        ("Cash flow: ending cash = cash per trial balance", f"='Cash Flow'!B{cf_refs['end']}",
         f"='Cash Flow'!B{cf_refs['end']}-'Cash Flow'!B{cf_refs['chk']}"),
        ("Journal vouchers: total debits = total credits",
         f"=SUM('Journal Vouchers'!{jv['debit']}{FIRST_DATA_ROW}:{jv['debit']}5000)",
         f"=SUM('Journal Vouchers'!{jv['credit']}{FIRST_DATA_ROW}:{jv['credit']}5000)"),
        ("Every posting row resolves to a valid account (unknown codes)",
         "=" + "+".join(f"SUM({rng(sh['name'], '_bad')})" for sh in POSTING_SHEETS), "=0"),
        ("Overhead allocation % across business units ≤ 100%",
         f"=SUM('Business Units'!{ov}{FIRST_DATA_ROW}:{ov}3000)", "=MIN(1,B{r})"),
    ]
    for i, (lbl, a, b) in enumerate(checks):
        r = 4 + i
        au.cell(r, 1, lbl)
        au.cell(r, 2, a).number_format = MONEY
        au.cell(r, 3, b.replace("{r}", str(r))).number_format = MONEY
        au.cell(r, 4, f"=ROUND(B{r}-C{r},2)").number_format = MONEY
        au.cell(r, 5, f'=IF(ABS(D{r})<0.01,"OK","*** CHECK")')
        au.cell(r, 5).font = F(bold=True)
    au.cell(4 + len(checks) + 1, 1, "All checks / كل الفحوصات").font = F(bold=True)
    au.cell(4 + len(checks) + 1, 5, f'=IF(COUNTIF(E4:E{3 + len(checks)},"OK")={len(checks)},"ALL OK","REVIEW")')
    au.cell(4 + len(checks) + 1, 5).font = F(bold=True, color=NAVY)

    # ---------------------------------------------------------- cover index
    r = index_row
    order = ["Settings", "Chart of Accounts", "Tax Codes", "Business Units", "Parties", "Partners", "Partner Shares",
             "Fixed Assets", "Warehouses", "Products",
             "Stock Items", "Price List", "BOQ Lines", "Direct Costs", "Operating Expenses", "Payroll",
             "Receipt Vouchers", "Sales Invoices", "Invoice Lines", "Payment Vouchers", "Partner Transactions",
             "Stock Moves", "Journal Vouchers", "Trial Balance", "Income Statement", "Balance Sheet", "Cash Flow",
             "Tax Summary", "BU Profitability", "Partner Equity", "Payables", "Receivables", "Budget vs Actual", "Quick Estimator", "Scaffold Calculator",
             "Model Audit"]
    order[order.index("Trial Balance"):order.index("Trial Balance")] = [s["name"] for s in SHEETS if s["name"] not in order]
    descs = {s["name"]: s["title"] for s in SHEETS}
    descs.update({"Trial Balance": "ميزان المراجعة — opening, period movement, closing per account",
                  "Income Statement": "قائمة الدخل — Net sales → Gross profit → EBITDA → EBIT → EBT → Net profit",
                  "Balance Sheet": "الميزانية العمومية — as of the report end date",
                  "Cash Flow": "قائمة التدفقات النقدية — indirect method, reconciled to cash",
                  "BU Profitability": "ربحية وحدات الأعمال — incl. allocated shared overhead",
                  "Tax Summary": "ملخص الضرائب — output / input VAT, net VAT due, withholding balances",
                  "Partner Equity": "حقوق الشركاء — profit share, reserve, drawings, balance",
                  "Payables": "أرصدة الدائنين — what is owed to each supplier / worker",
                  "Receivables": "أرصدة العملاء — what each customer owes, less advances",
                  "Budget vs Actual": "الموازنة مقابل الفعلي — budget for the report period against the books",
                  "Quick Estimator": "التسعير السريع — price a scope from the price list",
                  "Scaffold Calculator": "حاسبة السقالات — pieces, weight and monthly rent from a façade's length × height",
                  "Model Audit": "مراجعة النموذج — integrity checks (must all be OK)"})
    for name in order:
        c = cover.cell(r, 2, name)
        c.hyperlink = f"#'{name}'!A1"
        c.font = F(color="1155CC", underline="single")
        cover.cell(r, 3, descs.get(name, "")).font = F(size=9)
        cover.cell(r, 3).alignment = Alignment(horizontal="left", readingOrder=1)
        r += 1
    r += 1
    cover.cell(r, 2, "Audit status / حالة المراجعة").font = F(bold=True)
    cover.cell(r, 3, f"='Model Audit'!E{4 + len(checks) + 1}").font = F(bold=True, color=NAVY)

    # sheet order: put statements after data
    wb.move_sheet("Lists", offset=len(wb.sheetnames))
    for ws in wb.worksheets:
        ws.sheet_properties.tabColor = (NAVY if ws.title in ("Trial Balance", "Income Statement", "Balance Sheet",
                                                              "Cash Flow", "Tax Summary", "BU Profitability", "Partner Equity",
                                                              "Payables", "Receivables", "Budget vs Actual", "Model Audit")
                                        else ("C9A227" if ws.title in ("Cover", "Quick Estimator", "Scaffold Calculator") else None))
    for name in ("Trial Balance", "Income Statement", "Balance Sheet", "Cash Flow", "Tax Summary", "BU Profitability",
                 "Partner Equity",
                 "Payables", "Receivables", "Budget vs Actual", "Quick Estimator", "Scaffold Calculator", "Model Audit", "Cover"):
        ws = wb[name]
        ws.page_setup.orientation = "landscape" if name in ("Trial Balance", "BU Profitability", "Quick Estimator", "Budget vs Actual",
                                                                "Scaffold Calculator") \
            else "portrait"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.print_options.horizontalCentered = True
        ws.oddFooter.center.text = "&P / &N"
        ws.oddFooter.right.text = company
    for name in ("Trial Balance", "Income Statement", "Balance Sheet", "Cash Flow"):
        wb[name].print_area = f"A1:{'J' if name == 'Trial Balance' else 'C'}{wb[name].max_row}"
    english_labels(wb)
    classic_style(wb, company)
    wb.calculation.fullCalcOnLoad = True
    check_formula_limits(wb)
    buf = io.BytesIO()
    wb.save(buf)
    from . import branding, documents
    return documents.inject_xlsx_images(buf.getvalue(), branding.logo_for(db, "workbook"),
                                        branding.watermark_for(db, "workbook"), sheet_name="Cover", col=6, row=1, px=90)


_AR_SEGMENT = re.compile(r"\s*[/|—]\s*[^/|A-Za-z]*[\u0600-\u06FF][^/|]*")
_ARABIC_LEAD = re.compile(r"^[^A-Za-z]*[\u0600-\u06FF][^A-Za-z]*—\s*")
REPORT_SHEETS = ("Cover", "Trial Balance", "Income Statement", "Balance Sheet", "Cash Flow", "Tax Summary",
                 "BU Profitability", "Partner Equity", "Payables", "Quick Estimator", "Model Audit")


def english_labels(wb):
    """The workbook is English (like the original). Remove the Arabic half of every *label* the builder wrote —
    report sheets and data-sheet titles/headers only; data values (Arabic names, descriptions) are untouched."""
    for ws in wb.worksheets:
        if ws.title in REPORT_SHEETS:
            rows = ws.iter_rows(max_row=4) if ws.title == "Payables" else ws.iter_rows()
        else:
            rows = ws.iter_rows(max_row=2)
        for row in rows:
            for c in row:
                v = c.value
                if isinstance(v, str) and not v.startswith("=") and re.search("[\u0600-\u06FF]", v):
                    nv = _ARABIC_LEAD.sub("", _AR_SEGMENT.sub("", v)).strip()
                    if nv and nv != v and re.search("[A-Za-z]", nv):
                        c.value = nv


EXCEL_FORMULA_LIMIT = 8192


def check_formula_limits(wb, limit=EXCEL_FORMULA_LIMIT - 200):
    """Excel silently deletes formulas longer than 8,192 characters ('Removed Records: Formula').
    Refuse to write such a workbook instead of shipping a broken file."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("=") and len(c.value) > limit:
                    raise RuntimeError(f"Formula too long for Excel in {ws.title}!{c.coordinate} ({len(c.value)} chars)")


def column_index(letter):
    from openpyxl.utils import column_index_from_string
    return column_index_from_string(letter)


def tr_tb(tb_last):
    return tb_last + 1


def classic_style(wb, company):
    """The look of the original BEYOND workbook: 'COMPANY | SHEET' titles in 16 pt navy, Arial 11 body,
    tall navy header rows, and the brown cover page. Only fonts, fills and heights change — never values."""
    from openpyxl.styles import Font, PatternFill
    brand = (company or "BEYOND DESIGN").upper()
    for ws in wb.worksheets:
        if ws.title == "Lists":
            continue
        if ws.title == "Cover":
            ws.sheet_properties.tabColor = BROWN
            for row in ws.iter_rows():
                for c in row:
                    if c.value is None or c.hyperlink:
                        continue
                    f = c.font
                    if f.sz and f.sz >= 20:
                        c.font = Font(name="Arial", size=22, bold=True, color=BROWN)
                    elif f.b:
                        c.font = Font(name="Arial", size=11, bold=True, color=BROWN)
                    else:
                        c.font = Font(name="Arial", size=10, color="666666")
            ws.row_dimensions[2].height = 30
            for col in range(2, 4):
                ws.cell(1, col).fill = PatternFill("solid", fgColor=BROWN)
            ws.row_dimensions[1].height = 8
            continue
        a1 = ws.cell(1, 1)
        if isinstance(a1.value, str) and not a1.value.startswith("="):
            if " | " not in a1.value:
                a1.value = f"{brand} | {a1.value.upper()}"
            a1.font = Font(name="Arial", size=16, bold=True, color=NAVY)
            ws.row_dimensions[1].height = 24
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            for c in row:
                if c.value is None and not c.fill.fill_type:
                    continue
                f = c.font
                if c.row == 3 and f.i:  # example row stays small and grey
                    continue
                if c.row == 2 and f.sz and f.sz < 11 and not f.b:
                    c.font = Font(name="Arial", size=10, italic=True, color="666666")
                    continue
                if f.sz and f.sz < 11 or f.name != "Arial":
                    c.font = Font(name="Arial", size=11, bold=f.b, italic=f.i, underline=f.u,
                                  color=f.color if f.color is not None and f.color.type == "rgb" else None)
        if ws.row_dimensions[4].height and ws.row_dimensions[4].height < 26.25:
            ws.row_dimensions[4].height = 26.25


def _scaffold_calculator(wb, db, colmap, F, fill, NAVY, BLUE, MONEY, L):
    """The rental scaffold calculator as live formulas: the company's first scaffolding model (or the standard
    frame model), item rent / loss price / weight looked up on the Rental Equipment sheet."""
    from . import rentals
    spec, title = rentals.FRAME_MODEL, "Frame scaffolding (standard)"
    try:
        for m in rentals.list_models(db):
            if m["category"] == "scaffolding" and (m["spec"] or {}).get("components"):
                spec, title = rentals._spec(m["spec"]), m["name"]
                break
    except Exception:  # noqa: BLE001 — an older book without the rentals tables
        pass
    ws = wb.create_sheet("Scaffold Calculator")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Scaffold Calculator  |  حاسبة السقالات"
    ws["A1"].font = F(bold=True, size=14, color=NAVY)
    ws["A2"] = f"Model: {title}"
    ws["A2"].font = F(bold=True, color=NAVY)
    ws["A3"] = ("Type the blue cells. Quantities follow the same rules as the app's rental calculator; rent, loss price and "
                "weight come from the Rental Equipment sheet by item code (or type your own rate in the last column).")
    ws["A3"].font = F(size=8, color="555555")
    for col, w in zip("ABCDEFGHIJ", (34, 13, 8, 30, 3, 26, 12, 24, 12, 13)):
        ws.column_dimensions[col].width = w

    def head(row, col, labels):
        for i, h in enumerate(labels):
            c = ws.cell(row, col + i, h)
            c.font = F(bold=True, color="FFFFFF", size=9)
            c.fill = fill(NAVY)

    head(5, 1, ["Input / Setting", "Value", "Unit", "البيان"])
    head(5, 6, ["Calculated", "Value", "البيان"])
    refs, r = {}, 6
    rows = spec["inputs"] + spec["params"] + [{"key": "months", "label": "Rental period", "label_ar": "مدة الإيجار",
                                               "unit": "months", "value": 1}]
    for p in rows:
        ws.cell(r, 1, p.get("label") or p["key"]).font = F(bold=p in spec["inputs"])
        c = ws.cell(r, 2, p.get("value", 0))
        c.font = F(color=BLUE, bold=p in spec["inputs"])
        ws.cell(r, 3, p.get("unit") or "")
        ws.cell(r, 4, p.get("label_ar") or "")
        refs[p["key"]] = f"$B${r}"
        r += 1
    last_input = r
    d = 6
    for p in spec["derived"]:
        ws.cell(d, 6, p.get("label") or p["key"])
        refs[p["key"]] = f"$G${d}"
        ws.cell(d, 7, "=" + rentals.to_excel(p["expr"], refs)).number_format = "#,##0.##"
        ws.cell(d, 8, p.get("label_ar") or "")
        d += 1
    if spec["area"].strip() in refs:
        area = refs[spec["area"].strip()]
    else:
        ws.cell(d, 6, "Area (m²)").font = F(bold=True)
        ws.cell(d, 7, "=" + rentals.to_excel(spec["area"], refs)).number_format = "#,##0.##"
        ws.cell(d, 8, "المساحة (م²)")
        area = f"$G${d}"
        d += 1

    top = max(last_input, d) + 1
    head(top, 1, ["Item code", "Item", "Qty", "", "", "Rent / month", "Line rent / month", "Loss / damage price",
                  "Loss value", "Your rate (optional)"])
    ws.cell(top, 4, "Weight kg (line)").font = F(bold=True, color="FFFFFF", size=9)
    rc = colmap["Rental Equipment"]
    rng = lambda key: f"'Rental Equipment'!${rc[key]}${FIRST_DATA_ROW}:${rc[key]}$2000"  # noqa: E731

    # when the Rental Equipment sheet has no row for a code, fall back to the company's own item (or the standard piece)
    known = {c: {"name": n, "monthly_rate": 0, "loss_price": 0, "weight": 0} for c, n, *_ in rentals.STARTER_ITEMS}
    try:
        for it in db.q("SELECT code, name, monthly_rate, loss_price, weight FROM rent_items"):
            known[it["code"]] = {"name": it["name"], "monthly_rate": to_major(it["monthly_rate"] or 0),
                                 "loss_price": to_major(it["loss_price"] or 0), "weight": it["weight"] or 0}
    except Exception:  # noqa: BLE001
        pass

    def look(row, key):
        return f'INDEX({rng(key)},MATCH($A{row},{rng("code")},0))'

    def fallback(code, key, row):
        """A lookup that falls back to the known value of the default code (only while the code is unchanged)."""
        v = (known.get(code) or {}).get(key)
        if not code or v in (None, "", 0):
            return f'IFERROR({"" if key == "name" else "N("}{look(row, key)}{"" if key == "name" else ")"},'
        v = f'"{v}"' if key == "name" else repr(v)
        return f'IFERROR({"" if key == "name" else "N("}{look(row, key)}{"" if key == "name" else ")"},IF($A{row}="{code}",{v},'
    first = top + 1
    lines = [(c["item"], "=" + rentals.to_excel(c["expr"], refs)) for c in spec["components"]] + [("", None)] * 4
    for i, (code, qty) in enumerate(lines):
        row = first + i
        ws.cell(row, 1, code or None).font = F(color=BLUE)
        close = lambda key: "))" if (known.get(code) or {}).get(key) not in (None, "", 0) else ")"  # noqa: E731
        ws.cell(row, 2, f'=IF($A{row}="","",{fallback(code, "name", row)}"(not on Rental Equipment)"{close("name")})')
        q = ws.cell(row, 3, f"=MAX(ROUNDUP({qty[1:]},0),0)" if qty else None)
        if not qty:
            q.font = F(color=BLUE)
        ws.cell(row, 4, f'=IF($A{row}="",0,N(C{row})*{fallback(code, "weight", row)}0{close("weight")})').number_format = "#,##0.#"
        ws.cell(row, 6, f'=IF(N(J{row})>0,J{row},IF($A{row}="",0,{fallback(code, "monthly_rate", row)}0{close("monthly_rate")}))').number_format = MONEY
        ws.cell(row, 7, f"=N(C{row})*F{row}").number_format = MONEY
        ws.cell(row, 8, f'=IF($A{row}="",0,{fallback(code, "loss_price", row)}0{close("loss_price")})').number_format = MONEY
        ws.cell(row, 9, f"=N(C{row})*H{row}").number_format = MONEY
        ws.cell(row, 10).font = F(color=BLUE)
        ws.cell(row, 10).number_format = MONEY
    end = first + len(lines) - 1
    tot = end + 1
    ws.cell(tot, 1, "TOTAL  |  الإجمالي").font = F(bold=True)
    for col, fmt in ((3, "#,##0"), (4, "#,##0.#"), (7, MONEY), (9, MONEY)):
        c = ws.cell(tot, col, f"=SUM({L(col)}{first}:{L(col)}{end})")
        c.number_format, c.font = fmt, F(bold=True)
    sm = tot + 2
    summary = [("Rent per month", "الإيجار الشهري", f"=G{tot}", MONEY),
               ("Rent for the period", "إيجار المدة", f"=G{tot}*N({refs['months']})", MONEY),
               ("Rent per m² per month", "الإيجار لكل م² شهرياً", f"=IFERROR(G{tot}/{area},0)", MONEY),
               ("Pieces", "عدد القطع", f"=C{tot}", "#,##0"),
               ("Total weight (kg)", "الوزن الإجمالي (كجم)", f"=D{tot}", "#,##0"),
               ("Loss value of all pieces", "قيمة فقد كل القطع", f"=I{tot}", MONEY)]
    head(sm, 1, ["Summary", "Value", "", "الملخص"])
    for i, (en, ar, f, fmt) in enumerate(summary, start=1):
        ws.cell(sm + i, 1, en).font = F(bold=i == 1)
        c = ws.cell(sm + i, 2, f)
        c.number_format, c.font = fmt, F(bold=i == 1)
        ws.cell(sm + i, 4, ar)

    # ---- installation / dismantling / transport: what the client pays vs what it costs us (Settings → rental services)
    try:
        rates = rentals.service_rates(db)
    except Exception:  # noqa: BLE001
        rates = rentals.SERVICE_DEFAULTS
    sv = sm + len(summary) + 2
    ws.cell(sv, 1, "Installation, dismantling & transport  |  التركيب والفك والنقل").font = F(bold=True, size=12, color=NAVY)
    ws.cell(sv + 1, 1, "Client price by months of rent, m², trip, ton or lump sum; our cost by m², trip, ton or lump sum. "
                       "Scope: both / install / dismantle / none (the client installs). Change the blue cells.").font = F(size=8, color="555555")
    ins = sv + 2
    inputs = [("Scope", "both", "both / install / dismantle / none", "نطاق الخدمة"),
              ("Monthly rent used", f"=B{sm + 1}", "EGP", "الإيجار الشهري"),
              ("Area (m²)", f"={area}", "m²", "المساحة"),
              ("Weight (kg)", f"=D{tot}", "kg", "الوزن"),
              ("Truck capacity", rates.get("truck_tons") or 10, "tons", "حمولة الشاحنة"),
              ("Trips", None, "trips", "عدد النقلات"),
              ("Tons", None, "tons", "الأطنان")]
    at = {}
    for i, (en, v, unit, ar) in enumerate(inputs):
        row = ins + i
        ws.cell(row, 1, en)
        c = ws.cell(row, 2, v)
        c.font = F(color=BLUE)
        c.number_format = "#,##0.##"
        ws.cell(row, 3, unit)
        ws.cell(row, 4, ar)
        at[en] = f"$B${row}"
    from openpyxl.worksheet.datavalidation import DataValidation
    dv = DataValidation(type="list", formula1='"both,install,dismantle,none"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add(at["Scope"])
    ws[at["Trips"].replace("$", "")] = f'=IF(N({at["Weight (kg)"]})>0,MAX(1,ROUNDUP({at["Weight (kg)"]}/(MAX(N({at["Truck capacity"]}),0.001)*1000),0)),1)'
    ws[at["Tons"].replace("$", "")] = f'=N({at["Weight (kg)"]})/1000'
    ws[at["Trips"].replace("$", "")].font = ws[at["Tons"].replace("$", "")].font = F()
    measure = {"m2": at["Area (m²)"], "trip": at["Trips"], "ton": at["Tons"], "lump": "1"}
    hr = ins + len(inputs) + 1
    head(hr, 1, ["Service", "Include?", "Price basis", "Unit price / × rent", "", "Client pays", "Min. charge", "Unit cost", "Our cost", "Profit"])
    ws.cell(hr, 5, "Qty").font = F(bold=True, color="FFFFFF", size=9)
    labels = {"rent_months": "× monthly rent", "m2": "per m²", "trip": "per trip", "ton": "per ton", "lump": "lump sum"}
    first_s = hr + 1
    for i, r_ in enumerate(rates["services"]):
        row = first_s + i
        ws.cell(row, 1, f'{r_["name"]} / {r_.get("name_ar", "")}')
        ct = r_["charge_type"]
        if ct in ("install", "dismantle"):
            ws.cell(row, 2, f'=IF(OR({at["Scope"]}="both",{at["Scope"]}="{ct}"),"Yes","No")')
        else:
            ws.cell(row, 2, "Yes").font = F(color=BLUE)
        ws.cell(row, 3, labels.get(r_["basis"], r_["basis"]) + f'  (cost {labels.get(r_.get("cost_basis") or "m2")})')
        c = ws.cell(row, 4, r_["price"])
        c.font, c.number_format = F(color=BLUE), "#,##0.##"
        qty = "1" if r_["basis"] == "rent_months" else measure.get(r_["basis"], "1")
        ws.cell(row, 5, f"={qty}").number_format = "#,##0.##"
        gross = f"N({at['Monthly rent used']})*D{row}" if r_["basis"] == "rent_months" else f"E{row}*D{row}"
        ws.cell(row, 6, f'=IF(B{row}="Yes",MAX({gross},N(G{row})),0)').number_format = MONEY
        c = ws.cell(row, 7, r_.get("min_charge") or 0)
        c.font, c.number_format = F(color=BLUE), MONEY
        c = ws.cell(row, 8, r_["cost"])
        c.font, c.number_format = F(color=BLUE), MONEY
        ws.cell(row, 9, f'=IF(B{row}="Yes",{measure.get(r_.get("cost_basis") or "m2", "1")}*H{row},0)').number_format = MONEY
        ws.cell(row, 10, f"=F{row}-I{row}").number_format = MONEY
    last_s = first_s + len(rates["services"]) - 1
    st = last_s + 1
    ws.cell(st, 1, "SERVICES TOTAL  |  إجمالي الخدمات").font = F(bold=True)
    for col in (6, 9, 10):
        c = ws.cell(st, col, f"=SUM({L(col)}{first_s}:{L(col)}{last_s})")
        c.number_format, c.font = MONEY, F(bold=True)
    ws.cell(st + 1, 1, "Margin on services / per m²")
    ws.cell(st + 1, 6, f"=IFERROR(J{st}/F{st},0)").number_format = "0.0%"
    ws.cell(st + 1, 9, f"=IFERROR(F{st}/{at['Area (m²)']},0)").number_format = MONEY
    ws.cell(st + 2, 1, "First invoice (first month's rent + services at delivery)").font = F(bold=True)
    deliv = [first_s + i for i, r_ in enumerate(rates["services"]) if r_.get("when") != "return"]
    c = ws.cell(st + 2, 6, f"=N({at['Monthly rent used']})" + "".join(f"+F{x}" for x in deliv))
    c.number_format, c.font = MONEY, F(bold=True)
    ws.freeze_panes = "A5"


def _budget_sheet(wb, colmap, F, fill, NAVY, BLUE, MONEY, PCT, L):
    """Budget vs actual for the report period (Settings: report_from / report_to): each Budgets row against the
    account's movement on the Trial Balance (company-wide). A positive variance is good for profit."""
    ws = wb.create_sheet("Budget vs Actual")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Budget vs Actual  |  الموازنة مقابل الفعلي"
    ws["A1"].font = F(bold=True, size=14, color=NAVY)
    ws["A2"] = '="Period "&TEXT(ReportFrom,"yyyy-mm-dd")&" to "&TEXT(ReportTo,"yyyy-mm-dd")&" — actuals are company-wide; months of the budget inside the period are counted"'
    ws["A2"].font = F(size=9, color="555555")
    heads = ["Year", "Account", "Account name", "Business unit", "Budget (period)", "Actual (period)", "Variance (+ good)", "Used %", "Full-year budget"]
    for i, h in enumerate(heads, start=1):
        c = ws.cell(4, i, h)
        c.font = F(bold=True, color="FFFFFF", size=9)
        c.fill = fill(NAVY)
        ws.column_dimensions[L(i)].width = [8, 10, 34, 12, 16, 16, 16, 10, 16][i - 1]
    b, bl = "Budgets", colmap["Budgets"]
    coa = "'Chart of Accounts'"
    for k in range(400):
        r, src = 5 + k, FIRST_DATA_ROW + k
        cell = lambda key: f"{b}!${bl[key]}${src}"  # noqa: E731
        ws.cell(r, 1, f'=IF({cell("account")}="","",{cell("year")})')
        ws.cell(r, 2, f'=IF(A{r}="","",{cell("account")}&"")')
        ws.cell(r, 3, f'=IF(A{r}="","",IFERROR(INDEX({coa}!$B${FIRST_DATA_ROW}:$B$3000,MATCH(B{r},{coa}!$A${FIRST_DATA_ROW}:$A$3000,0)),"?"))')
        ws.cell(r, 4, f'=IF(A{r}="","",{cell("bu_code")}&"")')
        months = [f"m{i}" for i in range(1, 13)]
        anym = "OR(" + ",".join(f'{cell(m)}<>""' for m in months) + ")"
        per_month = lambda i: (f'IF(AND(DATE(A{r},{i},1)>=DATE(YEAR(ReportFrom),MONTH(ReportFrom),1),DATE(A{r},{i},1)<=ReportTo),'  # noqa: E731
                               f'IF({anym},N({cell(months[i - 1])}),N({cell("total")})/12),0)')
        ws.cell(r, 5, f'=IF(A{r}="",0,' + "+".join(per_month(i) for i in range(1, 13)) + ")").number_format = MONEY
        typ = f'IFERROR(INDEX({coa}!$E${FIRST_DATA_ROW}:$E$3000,MATCH(B{r},{coa}!$A${FIRST_DATA_ROW}:$A$3000,0)),"")'  # normal side
        mv = f"IFERROR(INDEX('Trial Balance'!$F$5:$F$3000,MATCH(B{r},'Trial Balance'!$A$5:$A$3000,0))-INDEX('Trial Balance'!$G$5:$G$3000,MATCH(B{r},'Trial Balance'!$A$5:$A$3000,0)),0)"
        ws.cell(r, 6, f'=IF(A{r}="",0,IF({typ}="Credit",-1,1)*{mv})').number_format = MONEY
        ws.cell(r, 7, f'=IF(A{r}="",0,IF({typ}="Credit",F{r}-E{r},E{r}-F{r}))').number_format = MONEY
        ws.cell(r, 8, f'=IF(OR(A{r}="",E{r}=0),"",F{r}/E{r})').number_format = PCT
        ws.cell(r, 9, f'=IF(A{r}="",0,IF({anym},' + "+".join(f"N({cell(m)})" for m in months) + f',N({cell("total")})))').number_format = MONEY
        for ci in range(1, 10):
            ws.cell(r, ci).font = F(size=9)
    ws["K4"] = "Totals"
    ws["K4"].font = F(bold=True)
    for i, (lab, f) in enumerate((("Budget (period)", "=SUM(E5:E404)"), ("Actual (period)", "=SUM(F5:F404)"), ("Variance (+ good)", "=SUM(G5:G404)")), start=5):
        ws.cell(i, 11, lab).font = F(size=9)
        c = ws.cell(i, 12, f)
        c.number_format, c.font = MONEY, F(bold=True)
    ws.column_dimensions["K"].width, ws.column_dimensions["L"].width = 18, 16

