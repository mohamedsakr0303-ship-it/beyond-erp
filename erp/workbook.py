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
import json
import re

from . import accounting as acc
from .db import SUBTYPES, to_major
from .interchange import FIRST_DATA_ROW, HEADER_ROW, POSTING_SHEETS, ROLE, SHEET_BY_NAME, SHEETS, export_data, input_cols

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

_PLASTER_AR = {"category": "أعمال البياض", "description": "بياض حوائط", "unit": "م٢"}
EXAMPLES_AR = {"Price List": _PLASTER_AR, "BOQ Lines": _PLASTER_AR, "Invoice Lines": _PLASTER_AR}
EXAMPLES = {
    "Settings": {"key": "company_name", "value": "Company Name"},
    "Chart of Accounts": {"code": "6095", "name": "Cleaning & Hospitality", "type": "Expense",
                          "subtype": "general_admin", "role": "", "active": "Yes"},
    "Tax Codes": {"code": "VAT14", "name": "VAT 14% (standard)", "name_ar": "ضريبة القيمة المضافة 14%", "kind": "vat",
                  "rate": "14%", "eta_type": "T1", "eta_subtype": "V009", "active": "Yes"},
    "Parties": {"name": "Customer name", "type": "customer", "receiver_type": "B", "tax_id": "123456789",
                "country": "EG", "governate": "Governorate", "region": "Area", "street": "Street name",
                "building": "12", "default_vat": "VAT14", "default_wht": "WHT1-C"},
    "Business Units": {"code": "PRJ-2026-004", "name": "Project name", "type": "Villa", "location": "City",
                       "area": 420, "start_date": "2026-10-01", "status": "In Progress", "basis": "Cost-Plus %",
                       "fee_pct": "25%", "contract_value": "", "overhead_pct": "30%"},
    "Partners": {"name": "Partner name", "default_share": "25%", "active": "Yes"},
    "Fixed Assets": {"code": "FA-0001", "name": "Site pickup truck", "account": "1420", "acquired": "2026-01-15",
                     "cost": 600000, "life_months": 60},
    "Partner Shares": {"partner": "Partner name", "bu_code": "PRJ-2026-004", "share": "25%"},
    "Warehouses": {"name": "Site store", "location": "City", "account": "1200"},
    "Stock Items": {"name": "Ceramic tiles 60×60", "unit": "m2", "sku": "CT-6060", "reorder_level": 50},
    "Products": {"code": "P-0001", "name": "Apartment A-301", "kind": "unit", "category": "Apartments", "unit": "unit",
                 "price": 2400000, "status": "available", "specs": "Area: 150 m²; Floor: 3; Bedrooms: 3"},
    "Price List": {"category": "Plastering works", "item_no": "3.1", "description": "Wall plaster", "unit": "m²",
                   "cost": 219.23, "markup": "30%", "price": ""},
    "BOQ Lines": {"title": "Interior works", "bu_code": "PRJ-2026-004", "rev": "Rev 01", "date": "2026-10-01",
                  "category": "Plastering works", "item_no": "3.1", "description": "Wall plaster", "unit": "m²", "qty": 320,
                  "price": 285, "cost": 219.23},
    "Direct Costs": {"date": "2026-10-03", "bu_code": "PRJ-2026-004", "account": "5010", "description": "Cement 50 kg",
                     "vendor": "Supplier name", "qty": 16, "unit": "bag", "rate": 240, "paid": 2000,
                     "method": "Cash", "vat_code": "VAT14", "wht_code": "WHT1-S"},
    "Operating Expenses": {"date": "2026-10-01", "bu_code": "", "account": "6010", "description": "Office rent — Oct",
                           "vendor": "Landlord", "amount": 15000, "paid": "", "method": "Bank Transfer"},
    "Payroll": {"date": "2026-10-31", "bu_code": "PRJ-2026-004", "worker": "Employee name", "role": "Site engineer",
                "cost_type": "Direct Labour (Cost of Revenue)", "period": "2026-10", "basis": "Monthly", "qty": 1,
                "rate": 12000, "paid": 12000},
    "Receipt Vouchers": {"date": "2026-10-05", "bu_code": "PRJ-2026-004", "ref": "RV-001", "memo": "Advance payment",
                         "amount": 100000, "method": "Bank Transfer", "credit_account": "2100"},
    "Sales Invoices": {"number": "INV-2026-001", "date": "2026-10-30", "bu_code": "PRJ-2026-004",
                       "status": "Posted", "customer": "Customer name", "vat_code": "VAT14",
                       "wht_code": "WHT1-C"},
    "Invoice Lines": {"number": "INV-2026-001", "category": "Plastering works", "item_no": "3.1",
                      "description": "Wall plaster", "unit": "m²", "qty": 150, "price": 285},
    "Payment Vouchers": {"date": "2026-10-10", "party": "Supplier name", "against": "2010", "amount": 1840,
                         "method": "Cash", "ref": "PV-001"},
    "Partner Transactions": {"date": "2026-10-01", "partner": "Partner name", "type": "Capital Contribution",
                             "amount": 250000, "method": "Bank Transfer"},
    "Stock Moves": {"date": "2026-10-02", "type": "Receipt", "warehouse": "Main Store", "item": "Ceramic tiles 60×60",
                    "unit": "m2", "qty": 200, "rate": 310, "vendor": "Supplier name", "paid": 62000},
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


_STYLE_CACHE = {}


def _cached(cls):
    """Same arguments, same style object: a workbook sets the same few fonts/fills on hundreds of thousands of
    cells, and building a new openpyxl object for each one was most of the time spent making a workbook."""
    def make(*a, **k):
        key = (cls, a, tuple(sorted(k.items())))
        try:
            return _STYLE_CACHE[key]
        except KeyError:
            obj = _STYLE_CACHE[key] = cls(*a, **k)
            return obj
        except TypeError:   # an argument that cannot be a dictionary key
            return cls(*a, **k)
    return make


EXTRAS_SHEET = "_BeyondData"


def _embed_extras(wb, db, files=True, secrets=False):
    """What the sheets cannot hold (logos and icons, offers, loans, custody, rentals, sales, payroll, bank
    reconciliation, recurring entries, purchase orders, services, ownership history, files…), as JSON in a very
    hidden sheet, so importing this workbook back restores everything — exactly like a JSON backup."""
    from . import interchange
    blob = json.dumps(interchange.export_extras(db, files=files, secrets=secrets), ensure_ascii=False, default=str)
    ws = wb.create_sheet(EXTRAS_SHEET)
    ws["A1"] = "beyond-erp-extras-1"
    for i in range(0, len(blob), 32000):          # an Excel cell holds 32,767 characters
        ws.cell(2 + i // 32000, 1, blob[i:i + 32000])
    ws.sheet_state = "veryHidden"


# what a universal template's Settings sheet keeps: how the books work, nothing that names or identifies a company
GENERIC_SETTING_KEYS = ("report_from", "report_to", "currency", "fiscal_year_start", "reserve_pct", "legal_reserve_pct",
                        "legal_reserve_cap", "units", "offer_digits", "offer_prefix", "offer_validity_days")


def build_workbook(db, template=False, data=None, hide=None, lang="en", extend=None, cover_extra=None, identity=None, files=True, secrets=False):
    """The Excel workbook. lang="ar": right-to-left with Arabic sheet names and labels (imports back the same).
    A template is branded by branding.template_identity (the universal BeyondERP one unless the company chose its own);
    identity= gives it explicitly (e.g. a business-type template built in a scratch book for this company).
    extend(wb) -> [sheet names]: add a company's own sheets before the cover is drawn (they are placed after the
    Dashboard and listed first on the cover with cover_extra = [(sheet, description), ...])."""
    _need()
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    Alignment, Border, Font, PatternFill, Side = (_cached(c) for c in (Alignment, Border, Font, PatternFill, Side))
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
    from . import branding
    ident = identity or (branding.template_identity(db) if template and given is None else None)   # not the demo file
    if ident and not ident["own"]:   # universal: placeholders instead of this company's details (skipped on import)
        rows = [r for r in data.get("Settings") or [] if r.get("key") in GENERIC_SETTING_KEYS]
        data = {**data, "Settings": [{"key": "company_name", "value": ident["name"]},
                                     {"key": "company_tagline", "value": ident["tagline"]}] + rows}
    company = ident["name"] if ident else (db.setting("company_name", "") or branding.GENERIC_NAME)
    tagline = ident["tagline"] if ident else db.setting("company_tagline", "")
    currency = db.setting("currency", "EGP")
    wb = Workbook()
    wb._lang = lang          # builders that hold their own two-language labels write only this language
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
        ex = {**EXAMPLES.get(s["name"], {}), **(EXAMPLES_AR.get(s["name"], {}) if lang == "ar" else {})}
        for i, c in enumerate(cols, start=1):
            h = ws.cell(HEADER_ROW, i, c["en"])
            calc = c["kind"] == "calc"
            h.font = F(bold=True, color="FFFFFF", size=9)
            h.fill = fill("7F8C8D" if calc else NAVY)
            h.alignment = Alignment(wrap_text=True, vertical="center")
            if c.get("note"):
                h.comment = Comment(c["note"], "BeyondERP")
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
    # the unbilled-revenue (work in progress) and custody accounts, looked up once (Partner Pack)
    lists["ZY1"] = f'=IFERROR({ROLE("unbilled_revenue")}&"","1180")'
    lists["ZY2"] = f'=IFERROR({ROLE("custody")}&"","")'
    WIPACCT, CUSTACCT = "Lists!$ZY$1", "Lists!$ZY$2"
    # payroll parameters from the Payroll Settings sheet (key / value rows), one cell each, used by Payroll Run
    from .interchange import PAYROLL_PARAMS, PAYROLL_FLAGS
    from .payroll import DEFAULT_COMPONENTS
    ps = "'Payroll Settings'!$A$5:$A$500", "'Payroll Settings'!$B$5:$B$500"
    look = lambda k: f'INDEX({ps[1]},MATCH("{k}",{ps[0]},0))'  # noqa: E731
    zx = 1
    for k, dflt in PAYROLL_PARAMS:
        lists[f"ZX{zx}"] = f'=IFERROR(VALUE({look(k)}&""),{dflt!r})'
        zx += 1
    lists[f"ZX{zx}"] = f'=IFERROR(IF(UPPER(LEFT({look("prorate_allowances")}&"Y",1))="N",0,1),1)'
    zx += 1
    dflt = {c[0]: c for c in DEFAULT_COMPONENTS}
    for code in PAYROLL_FLAGS:
        t = look(f"component_{code}")
        lists[f"ZX{zx}"] = f'=IFERROR(IF(ISNUMBER(SEARCH("| taxable |",{t})),1,0),{int(dflt[code][5])})'
        lists[f"ZX{zx + 1}"] = f'=IFERROR(IF(ISNUMBER(SEARCH("| insurable |",{t})),1,0),{int(dflt[code][6])})'
        lists[f"ZX{zx + 2}"] = f'=IFERROR(IF(ISNUMBER(SEARCH("| off",{t})),0,1),1)'
        zx += 3
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
            # Partner Pack: work in progress, partner capital / drawings, and the cash basis
            ("_wip", "work in progress", lambda r: "=" + "+".join(
                f'IF({letters[acct]}{r}&""={WIPACCT},{"" if side == "D" else "-"}N({letters[amt]}{r}),0)'
                for amt, acct, side in s["legs"])),
            ("_cap", "partner capital", lambda r: metric(r, lambda t: f'OR({t}="Equity|capital",{t}="Equity|partner_current")', False)),
            ("_drw", "partner drawings", lambda r: metric(r, lambda t: f'{t}="Equity|drawings"', True)),
            ("_pty", "partner of the line", lambda r: (f'=IF({letters["line_party"]}{r}<>"",{letters["line_party"]}{r}&"",{letters["party"]}{r}&"")'
                                         if "line_party" in letters else
                                         (f'={letters[PARTY_COL[s["name"]]]}{r}&""' if PARTY_COL.get(s["name"]) in letters else '=""'))),
            ("_cash", "cash moved", lambda r: metric(r, lambda t: f'{t}="Asset|cash"', True)),
            ("_emp", "staff costs", lambda r: metric(r, lambda t: f'{t}="Expense|employee"', True)),
            ("_ga", "G&A costs", lambda r: metric(r, lambda t: f'OR({t}="Expense|general_admin",{t}="Expense|marketing",{t}="Expense|utilities")', True)),
            ("_arcr", "invoice collected", lambda r: "=" + ("+".join(
                f'IF({letters["_t" + str(li)]}{r}="Asset|receivable",N({letters[amt]}{r}),0)'
                for li, (amt, acct, side) in enumerate(s["legs"]) if side == "C") or "0")),
            ("_gcash", "cash in the voucher", lambda r: (f'=SUMIFS({letters["_cash"]}:{letters["_cash"]},{letters["voucher"]}:{letters["voucher"]},{letters["voucher"]}{r})'
                                                         if "voucher" in letters else f'={letters["_cash"]}{r}')),
            ("_grev", "revenue in the voucher", lambda r: (f'=SUMIFS({letters["_rev"]}:{letters["_rev"]},{letters["voucher"]}:{letters["voucher"]},{letters["voucher"]}{r})'
                                                           f'+SUMIFS({letters["_arcr"]}:{letters["_arcr"]},{letters["voucher"]}:{letters["voucher"]},{letters["voucher"]}{r})'
                                                           if "voucher" in letters else f'={letters["_rev"]}{r}+{letters["_arcr"]}{r}')),
            ("_gcost", "costs in the voucher", lambda r: (f'=SUMIFS({letters["_cogs"]}:{letters["_cogs"]},{letters["voucher"]}:{letters["voucher"]},{letters["voucher"]}{r})'
                                                          f'+SUMIFS({letters["_exp"]}:{letters["_exp"]},{letters["voucher"]}:{letters["voucher"]},{letters["voucher"]}{r})'
                                                          if "voucher" in letters else f'={letters["_cogs"]}{r}+{letters["_exp"]}{r}')),
            ("_cr", "revenue received in cash", lambda r: (f'=IF(AND({letters["_gcash"]}{r}>0,{letters["_grev"]}{r}>0),'
                                                           f'({letters["_rev"]}{r}+{letters["_arcr"]}{r})*MIN(1,{letters["_gcash"]}{r}/{letters["_grev"]}{r}),0)')),
            ("_cf", "share of costs paid in cash", lambda r: (f'=IF(AND({letters["_gcash"]}{r}<0,{letters["_gcost"]}{r}>0),'
                                                              f'MIN(1,-{letters["_gcash"]}{r}/{letters["_gcost"]}{r}),0)')),
            ("_ccogs", "cost of sales paid", lambda r: f'={letters["_cogs"]}{r}*{letters["_cf"]}{r}'),
            ("_cemp", "staff costs paid", lambda r: f'={letters["_emp"]}{r}*{letters["_cf"]}{r}'),
            ("_cga", "G&A paid", lambda r: f'={letters["_ga"]}{r}*{letters["_cf"]}{r}'),
            ("_coth", "other costs paid", lambda r: f'=({letters["_exp"]}{r}-{letters["_emp"]}{r}-{letters["_ga"]}{r})*{letters["_cf"]}{r}'),
            ("_cin", "collected from clients", lambda r: (f'=MAX(0,{letters["_cash"]}{r})' if s["name"] == "Receipt Vouchers" else
                                                         (f'=IF({letters["kind"]}{r}="Receipt Voucher",MAX(0,{letters["_cash"]}{r}),0)'
                                                          if s["name"] == "Journal Vouchers" else "=0"))),
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
    _crew_calculator(wb, db, F, fill, NAVY, BLUE, MONEY)
    _guide(wb, F, fill)
    _extract_print(wb, data, colmap, F, fill)

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

    audit_ref = f"'Model Audit'!$E${4 + len(checks) + 1}"
    # ---------------------------------------------------------- dashboard
    # sheets of sections this company switched off (Settings → Menus) are hidden — unless they hold data
    hide = _hide_list(db, hide)
    hidden = _hidden_sheets(db, wb, data, hide)
    _dashboard(wb, data, colmap, POSTING_SHEETS, rng, pl_refs, bs_refs, n_bu, audit_ref, currency, F, fill, L, hide)
    wb.move_sheet("Dashboard", offset=-(len(wb.sheetnames) - 2))
    _partner_pack(wb, data, colmap, POSTING_SHEETS, rng, within, upto, pl_refs, n_bu, audit_ref, currency, F, fill, L, WIPACCT, CUSTACCT)
    for name in hidden:
        wb[name].sheet_state = "hidden"
    # sheet order: as in the BEYOND V1.4 workbook — what partners and managers read first, then the books, then the
    # registers and tools, then data entry and setup. The importer finds sheets by name, so the order never matters to it.
    own = list(extend(wb) or []) if extend else []
    front = [n for n in SHEET_ORDER if n in wb.sheetnames]
    if own:
        at = front.index("Dashboard") + 1 if "Dashboard" in front else 0
        mine = [n for n in own if n in wb.sheetnames]
        front = front[:at] + mine + [n for n in front[at:] if n not in mine]
    rest = [n for n in wb.sheetnames if n not in front and n != "Lists"]
    wb._sheets = [wb[n] for n in front + rest + ["Lists"]]
    wb.active = 0
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
    if lang != "ar":
        english_labels(wb)          # the Arabic workbook keeps the Arabic half instead (workbook_ar.localize)
    classic_style(wb, company)
    _cover(wb, cover, company, tagline, template and not any((data or {}).values()), currency, audit_ref, hidden, F, fill,
           extra=cover_extra)
    _polish(wb, data, fill)
    if lang == "ar":
        from . import workbook_ar
        workbook_ar.localize(wb)
    if not template:
        _embed_extras(wb, db, files=files, secrets=secrets)   # files=False: they travel next to the workbook (see interchange.workbook_zip)
    wb.calculation.fullCalcOnLoad = True
    check_formula_limits(wb)
    buf = io.BytesIO()
    wb.save(buf)
    from . import documents
    return documents.inject_xlsx_images(buf.getvalue(), ident["logo"] if ident else branding.logo_for(db, "workbook"),
                                        ident["watermark"] if ident else branding.watermark_for(db, "workbook"), sheet_name="Cover" if lang != "ar" else "الغلاف",
                                        col=4, row=2, px=110)


_AR_SEGMENT = re.compile(r"\s*[/|—]\s*[^/|A-Za-z]*[\u0600-\u06FF][^/|]*")
_ARABIC_LEAD = re.compile(r"^[^A-Za-z]*[\u0600-\u06FF][^A-Za-z]*—\s*")
REPORT_SHEETS = ("Cover", "Trial Balance", "Income Statement", "Balance Sheet", "Cash Flow", "Tax Summary",
                 "BU Profitability", "Partner Equity", "Payables", "Quick Estimator", "Model Audit")


def english_labels(wb):
    """The workbook is English (like the original). Remove the Arabic half of every *label* the builder wrote —
    report sheets and data-sheet titles/headers only; data values (Arabic names, descriptions) are untouched."""
    data_sheets = {s["name"] for s in SHEETS}
    for ws in wb.worksheets:
        if ws.title in data_sheets or ws.title == "Payables":
            rows = ws.iter_rows(max_row=4)
        else:
            rows = ws.iter_rows()
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
    Font, PatternFill = _cached(Font), _cached(PatternFill)
    brand = (company or "Company Name").upper()
    for ws in wb.worksheets:
        if ws.title == "Lists":
            continue
        if ws.title == "Dashboard":
            ws["A1"].value = f"{brand}  |  DASHBOARD"
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
                a1.value = f"{brand} | {(ws.title if a1.value.strip().upper() == brand else a1.value).upper()}"
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

    ar = getattr(wb, "_lang", "en") == "ar"
    lab = lambda en, a: a if ar and a else en  # noqa: E731
    head(5, 1, ["Input / Setting", "Value", "Unit"])
    head(5, 6, ["Calculated", "Value"])
    refs, r = {}, 6
    rows = spec["inputs"] + spec["params"] + [{"key": "months", "label": "Rental period", "label_ar": "مدة الإيجار",
                                               "unit": "months", "value": 1}]
    for p in rows:
        ws.cell(r, 1, lab(p.get("label") or p["key"], p.get("label_ar"))).font = F(bold=p in spec["inputs"])
        c = ws.cell(r, 2, p.get("value", 0))
        c.font = F(color=BLUE, bold=p in spec["inputs"])
        ws.cell(r, 3, p.get("unit") or "")
        refs[p["key"]] = f"$B${r}"
        r += 1
    last_input = r
    d = 6
    for p in spec["derived"]:
        ws.cell(d, 6, lab(p.get("label") or p["key"], p.get("label_ar")))
        refs[p["key"]] = f"$G${d}"
        ws.cell(d, 7, "=" + rentals.to_excel(p["expr"], refs)).number_format = "General"
        d += 1
    if spec["area"].strip() in refs:
        area = refs[spec["area"].strip()]
    else:
        ws.cell(d, 6, lab("Area (m²)", "المساحة (م²)")).font = F(bold=True)
        ws.cell(d, 7, "=" + rentals.to_excel(spec["area"], refs)).number_format = "General"
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
        ws.cell(row, 4, f'=IF($A{row}="",0,N(C{row})*{fallback(code, "weight", row)}0{close("weight")})').number_format = "General"
        ws.cell(row, 6, f'=IF(N(J{row})>0,J{row},IF($A{row}="",0,{fallback(code, "monthly_rate", row)}0{close("monthly_rate")}))').number_format = MONEY
        ws.cell(row, 7, f"=N(C{row})*F{row}").number_format = MONEY
        ws.cell(row, 8, f'=IF($A{row}="",0,{fallback(code, "loss_price", row)}0{close("loss_price")})').number_format = MONEY
        ws.cell(row, 9, f"=N(C{row})*H{row}").number_format = MONEY
        ws.cell(row, 10).font = F(color=BLUE)
        ws.cell(row, 10).number_format = MONEY
    end = first + len(lines) - 1
    tot = end + 1
    ws.cell(tot, 1, "TOTAL  |  الإجمالي").font = F(bold=True)
    for col, fmt in ((3, "#,##0"), (4, "General"), (7, MONEY), (9, MONEY)):
        c = ws.cell(tot, col, f"=SUM({L(col)}{first}:{L(col)}{end})")
        c.number_format, c.font = fmt, F(bold=True)
    sm = tot + 2
    summary = [("Rent per month", "الإيجار الشهري", f"=G{tot}", MONEY),
               ("Rent for the period", "إيجار المدة", f"=G{tot}*N({refs['months']})", MONEY),
               ("Rent per m² per month", "الإيجار لكل م² شهرياً", f"=IFERROR(G{tot}/{area},0)", MONEY),
               ("Pieces", "عدد القطع", f"=C{tot}", "#,##0"),
               ("Total weight (kg)", "الوزن الإجمالي (كجم)", f"=D{tot}", "#,##0"),
               ("Loss value of all pieces", "قيمة فقد كل القطع", f"=I{tot}", MONEY)]
    head(sm, 1, ["Summary", "Value"])
    for i, (en, a, f, fmt) in enumerate(summary, start=1):
        ws.cell(sm + i, 1, lab(en, a)).font = F(bold=i == 1)
        c = ws.cell(sm + i, 2, f)
        c.number_format, c.font = fmt, F(bold=i == 1)

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
    for i, (en, v, unit, a) in enumerate(inputs):
        row = ins + i
        ws.cell(row, 1, lab(en, a))
        c = ws.cell(row, 2, v)
        c.font = F(color=BLUE)
        c.number_format = "General"
        ws.cell(row, 3, unit)
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
        c.font, c.number_format = F(color=BLUE), "General"
        qty = "1" if r_["basis"] == "rent_months" else measure.get(r_["basis"], "1")
        ws.cell(row, 5, f"={qty}").number_format = "General"
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


def _crew_calculator(wb, db, F, fill, NAVY, BLUE, MONEY):
    """What a crew / subcontractor earns for measured work: count × width × height × depth = quantity × rate.
    Stands alone on purpose — no other sheet reads it and the import ignores it. Record what you actually paid as an
    Operating Expense, a Payroll row or a Journal Voucher (or 'Record payment' on the app's crew calculator)."""
    from openpyxl.styles import Alignment
    from . import payroll
    ws = wb.create_sheet("Crew Pay Calculator")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Crew Pay Calculator  |  حاسبة أجور الأطقم والمقاولين"
    ws["A1"].font = F(bold=True, size=14, color=NAVY)
    ws["A2"] = ("Quantity = count × width / length × height / width × depth (blank = 1). Scaffold frames: count = frames, width = frame width, "
                "height = frame height → m² installed. This sheet is only a calculator: it never touches the books, rents or other calculators.")
    ws["A2"].font = F(size=8, color="555555")
    ws["A3"] = ("To book what you ACTUALLY paid (it can differ from the calculation): add an Operating Expenses or Payroll row, "
                "or a Journal Voucher (Dr labour cost, Cr cash / bank). In the app: Crew pay calculator → Record payment.")
    ws["A3"].font = F(size=8, color="B00020")
    heads = ["Date", "Crew / worker", "Building / work", "Item", "Unit", "Count", "Width / length", "Height / width", "Depth",
             "Quantity", "Rate", "Calculated", "Actually paid", "Difference", "Notes"]
    widths = [11, 18, 22, 28, 7, 8, 10, 10, 8, 11, 9, 13, 13, 12, 22]
    for i, (h, w) in enumerate(zip(heads, widths), start=1):
        c = ws.cell(5, i, h)
        c.font, c.fill = F(bold=True, color="FFFFFF", size=9), fill(NAVY)
        c.alignment = Alignment(wrap_text=True, horizontal="center")
        ws.column_dimensions[chr(64 + i)].width = w
    kind = db.setting("business_type") or ""
    items = payroll.DEFAULT_WORK_ITEMS.get(kind) or payroll.DEFAULT_WORK_ITEMS["general"]
    first, n = 6, 60
    for k in range(n):
        r = first + k
        for col in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 13, 15):
            ws.cell(r, col).font = F(color=BLUE)
        if k < len(items):
            ws.cell(r, 4, items[k][0])
            ws.cell(r, 5, items[k][1])
        ws.cell(r, 1).number_format = "yyyy-mm-dd"
        ws.cell(r, 10, f'=IF(F{r}="","",N(F{r})*IF(N(G{r})=0,1,G{r})*IF(N(H{r})=0,1,H{r})*IF(N(I{r})=0,1,I{r}))').number_format = "General"
        ws.cell(r, 12, f'=IF(J{r}="",0,ROUND(J{r}*N(K{r}),2))').number_format = MONEY
        ws.cell(r, 13).number_format = MONEY
        ws.cell(r, 14, f'=IF(M{r}="","",N(M{r})-L{r})').number_format = MONEY
    t = first + n
    ws.cell(t, 4, "TOTAL  |  الإجمالي").font = F(bold=True)
    for col, f in ((10, f"=SUM(J{first}:J{t - 1})"), (12, f"=SUM(L{first}:L{t - 1})"), (13, f"=SUM(M{first}:M{t - 1})"), (14, f"=M{t}-SUMIFS(L{first}:L{t - 1},M{first}:M{t - 1},\"<>\")")):
        c = ws.cell(t, col, f)
        c.font, c.number_format = F(bold=True), MONEY if col != 10 else "General"
    ws.cell(t + 1, 4, "Difference = what was paid − what the sheet calculates, on the rows where a payment is typed").font = F(size=8, color="555555")
    # per crew summary
    s0 = t + 3
    for i, h in enumerate(["Crew / worker (type a name)", "Quantity", "Calculated", "Actually paid", "Still to pay (calculated − paid)"], start=2):
        c = ws.cell(s0, i, h)
        c.font, c.fill = F(bold=True, color="FFFFFF", size=9), fill(NAVY)
    for k in range(8):
        r = s0 + 1 + k
        ws.cell(r, 2).font = F(color=BLUE)
        ws.cell(r, 3, f'=IF(B{r}="","",SUMIFS(J{first}:J{t - 1},B{first}:B{t - 1},B{r}))').number_format = "General"
        ws.cell(r, 4, f'=IF(B{r}="","",SUMIFS(L{first}:L{t - 1},B{first}:B{t - 1},B{r}))').number_format = MONEY
        ws.cell(r, 5, f'=IF(B{r}="","",SUMIFS(M{first}:M{t - 1},B{first}:B{t - 1},B{r}))').number_format = MONEY
        ws.cell(r, 6, f'=IF(B{r}="","",D{r}-E{r})').number_format = MONEY


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



def _extract_print(wb, data, colmap, F, fill):
    """An extract (مستخلص) laid out like the client's: pick the invoice number; running = every work to date on the site
    (its earlier issued extracts too), less what was paid before, less deductions = due."""
    from openpyxl.styles import Alignment, Border, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    ar = getattr(wb, "_lang", "en") == "ar"
    T = lambda en, a: a if ar else en  # noqa: E731
    si, il, rv = colmap["Sales Invoices"], colmap["Invoice Lines"], colmap["Receipt Vouchers"]
    S = lambda k: f"'Sales Invoices'!${si[k]}$5:${si[k]}$2000"  # noqa: E731
    IL = lambda k: f"'Invoice Lines'!${il[k]}$5:${il[k]}$2000"  # noqa: E731
    pick = lambda k: f'IFERROR(INDEX({S(k)},MATCH($J$2&"",{S("number")},0)),"")'  # noqa: E731
    ws = wb.create_sheet("Extract Print")
    ws.sheet_view.rightToLeft = ar
    ws.sheet_view.showGridLines = False
    med, thin = Side(style="medium", color="000000"), Side(style="thin", color="000000")
    box, cb = Border(left=med, right=med, top=med, bottom=med), Border(left=thin, right=thin, top=thin, bottom=thin)
    ctr = Alignment(horizontal="center", vertical="center", wrap_text=True)
    side = Alignment(horizontal="right" if ar else "left", vertical="center", wrap_text=True)
    for c, w in zip("ABCDEFGHIJ", (5, 46, 7, 9, 10, 11, 13, 3, 24, 22)):
        ws.column_dimensions[c].width = w
    # the controls (outside the printed area)
    sales = sorted([r for r in (data or {}).get("Sales Invoices") or [] if r.get("number")], key=lambda r: str(r.get("date") or ""))
    ctl = [(T("Extract (invoice No.)", "المستخلص (رقم الفاتورة)"), sales[-1]["number"] if sales else ""),
           (T("Show: running = all works to date; single = this extract only", "العرض: running = كل الأعمال حتى تاريخه؛ single = هذا المستخلص فقط"), "running"),
           (T("Business unit", "وحدة العمل"), f"={pick('bu_code')}&\"\""), (T("Date", "التاريخ"), f"={pick('date')}")]
    for i, (lab, v) in enumerate(ctl, start=2):
        ws.cell(i, 9, lab).font = F(size=9, color="555555")
        c = ws.cell(i, 10, v)
        c.font = F(bold=True, color=BLUE if i <= 3 else "111111")
    ws["J5"].number_format = "dd/mm/yyyy"
    dv = DataValidation(type="list", formula1=f"={S('number')}", allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("J2")
    dv2 = DataValidation(type="list", formula1='"running,single"', allow_blank=False)
    ws.add_data_validation(dv2)
    dv2.add("J3")
    ws["I7"] = T("Type in the blue cells. The extract is built from Sales Invoices / Invoice Lines; print the area A1:G.",
                 "اكتب في الخلايا الزرقاء. المستخلص يُبنى من فواتير المبيعات وبنودها؛ اطبع النطاق A1:G.")
    ws["I7"].font = F(size=8, italic=True, color="666666")
    ws["I7"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("I7:J10")
    # header boxes
    run, fin = T("INTERIM", "جارى"), T("FINAL", "ختامى")
    title = (f'="{T("", "مستخلـــــــص  (")}"&IF({pick("final")}="Yes","{fin}","{run}")&"{T(" EXTRACT No. ( ", ")  رقم ( ")}"&{pick("extract_no")}&" )"')
    ws.merge_cells("A2:D3")
    ws["A2"] = title
    ws.merge_cells("E2:G3")
    ws["E2"] = f"={pick('project')}&\"\""
    keys = "Settings!$A$5:$A$400"
    vals = "Settings!$B$5:$B$400"
    co = f'IFERROR(INDEX({vals},MATCH("extract_contractor",{keys},0))&"","")'
    co_name = f'IFERROR(INDEX({vals},MATCH("company_name",{keys},0))&"","")'
    ws.merge_cells("A4:C4")
    ws["A4"] = f'="{T("Contractor:  ", "أسم المقاول  :  ")}"&IF({co}<>"",{co},{co_name})'
    ws.merge_cells("D4:G4")
    ws["D4"] = f"=IF({pick('work')}&\"\"<>\"\",{pick('work')}&\"\",$J$4)"
    for a, f, al in (("A2", F(bold=True, size=14), ctr), ("E2", F(bold=True, size=14), ctr), ("A4", F(bold=True, size=10), side), ("D4", F(bold=True, size=12), ctr)):
        ws[a].font, ws[a].alignment = f, al
    for rng in ("A2:D3", "E2:G3", "A4:C4", "D4:G4"):
        for row in ws[rng]:
            for c in row:
                c.border = box
    heads = [T("Item", "بند"), T("Description of works done", "بيان الاعمال المنفذة"), T("Unit", "الوحدة"), T("%", "النسبة"),
             T("Qty", "الكمية"), T("Rate", "الفئة"), T("Total", "الإجمالي")]
    for i, h in enumerate(heads, start=1):
        c = ws.cell(6, i, h)
        c.font, c.alignment, c.border = F(bold=True, size=10), ctr, cb
    # the lines: progressive MATCH on the Invoice Lines rows flagged for this extract (helper in the hidden column K)
    n, first = 30, 7
    ws.column_dimensions["K"].hidden = True
    tail = f"'Invoice Lines'!${il['x_xp']}$2000"
    for k in range(n):
        r = first + k
        prev = "4" if k == 0 else f"$K{r - 1}"
        ws.cell(r, 11, f'=IF(""&{prev}="","",IFERROR(MATCH(1,INDEX({IL("x_xp")},{prev}-3):{tail},0)+{prev},""))')
        g = lambda key: f"INDEX('Invoice Lines'!${il[key]}:${il[key]},$K{r})"  # noqa: E731
        ws.cell(r, 1, f'=IF($K{r}="","",{k + 1})')
        ws.cell(r, 2, f'=IF($K{r}="","",{g("description")}&"")')
        ws.cell(r, 3, f'=IF($K{r}="","",{g("unit")}&"")')
        ws.cell(r, 4, f'=IF($K{r}="","",IF({g("pct")}="",1,N({g("pct")})))')
        ws.cell(r, 5, f'=IF($K{r}="","",N({g("qty")}))')
        ws.cell(r, 6, f'=IF($K{r}="","",N({g("price")}))')
        ws.cell(r, 7, f'=IF($K{r}="","",N({g("amount")}))')
        for i in range(1, 8):
            c = ws.cell(r, i)
            c.border, c.font, c.alignment = cb, F(size=11 if i == 2 else 10, bold=i == 2), side if i == 2 else ctr
        ws.cell(r, 4).number_format, ws.cell(r, 5).number_format = "0.0%", "#,##0.0"
        ws.cell(r, 6).number_format = ws.cell(r, 7).number_format = "#,##0.00"
    last = first + n - 1
    s0 = last + 2
    run_f = '$J$3="running"'
    bu_f = '$J$4'
    vat = (f"=IF({run_f},SUMIFS({S('vat_amt')},{S('bu_code')},{bu_f},{S('date')},\"<\"&$J$5),0)+N({pick('vat_amt')})")
    wht = (f"=IF({run_f},SUMIFS({S('wht_amt')},{S('bu_code')},{bu_f},{S('date')},\"<\"&$J$5),0)+N({pick('wht_amt')})")
    paid = (f"=IF({pick('prev_paid')}&\"\"<>\"\",N({pick('prev_paid')}),SUMIFS('Receipt Vouchers'!${rv['amount']}$5:${rv['amount']}$2000,"
            f"'Receipt Vouchers'!${rv['bu_code']}$5:${rv['bu_code']}$2000,{bu_f},'Receipt Vouchers'!${rv['date']}$5:${rv['date']}$2000,\"<\"&$J$5))")
    sums = [(T("Total value of works", "اجمالى قيمة الاعمال"), f"=SUM(G{first}:G{last})"), (T("VAT", "ضريبة القيمة المضافة"), vat),
            (T("Withholding tax", "خصم من المنبع"), wht), (T("Less paid before", "خصم ماسبق صرفه"), paid),
            (T("Deductions", "خصومات"), f"=N({pick('deductions')})")]
    ws.merge_cells(f"A{s0}:D{s0 + len(sums) - 1}")
    ws[f"A{s0}"] = T("Notes", "الملاحظات")
    ws[f"A{s0}"].font, ws[f"A{s0}"].alignment = F(bold=True, size=12), Alignment(horizontal="center", vertical="top")
    for i, (lab, v) in enumerate(sums):
        r = s0 + i
        ws.merge_cells(f"E{r}:F{r}")
        ws[f"E{r}"] = f'=IF(N(G{r})=0,"","{lab}")' if i in (1, 2) else lab    # VAT / WHT show only when there is some
        ws[f"E{r}"].font, ws[f"E{r}"].alignment = F(size=9), side
        ws[f"G{r}"] = v
        ws[f"G{r}"].number_format = '#,##0.00;-#,##0.00;""' if i in (1, 2) else "#,##0.00"
        ws[f"G{r}"].font, ws[f"G{r}"].alignment = F(size=10), ctr
    due = s0 + len(sums)
    ws.merge_cells(f"A{due}:F{due}")
    ws[f"A{due}"] = T("AMOUNT DUE", "المستحق")
    ws[f"A{due}"].font, ws[f"A{due}"].alignment = F(bold=True, size=12), ctr
    ws[f"G{due}"] = f"=G{s0}+G{s0 + 1}-G{s0 + 2}-G{s0 + 3}-G{s0 + 4}"
    ws[f"G{due}"].font, ws[f"G{due}"].number_format, ws[f"G{due}"].alignment = F(bold=True, size=11), "#,##0.00", ctr
    for row in ws[f"A{s0}:G{due}"]:
        for c in row:
            c.border = cb
    foot = [f'="{T("Issued on:  ", "تحريرا فى :  ")}"&IF(N($J$5)>0,TEXT($J$5,"d/m/yyyy"),"")', f"={pick('notes')}&\"\"", T("Signature", "التوقيع")]
    for k, v in enumerate(foot):
        c = ws.cell(due + 2 + k, 2, v)
        c.font, c.alignment = F(bold=True, size=9), side
    ws.page_setup.orientation, ws.page_setup.paperSize = "portrait", ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1
    ws.print_area = f"A1:G{due + 5}"


# what to type where — (task EN, sheet(s), what follows by itself EN, task AR, what follows AR)
GUIDE = [
    ("Start renting to a new site / building", "Business Units, Rental Contracts",
     "One Business Units row (the site, e.g. RNT-2026-003), then one Rental Contracts row per kind of piece, all with the same "
     "contract ref and business unit. Fill 'Delivered on' when the pieces leave the yard. → Dashboard 'Ongoing rentals', "
     "stock on hire, BU Profitability.",
     "بدء تأجير لموقع / عمارة جديدة",
     "صف في وحدات الأعمال (الموقع)، ثم صف في عقود التأجير لكل نوع قطعة بنفس رقم العقد ونفس وحدة العمل. اكتب تاريخ التسليم عند خروج "
     "القطع من المخزن ← تظهر تلقائياً في «الإيجارات الجارية» بلوحة المتابعة والمخزون المؤجر وربحية المشروعات."),
    ("Price by frame (the extract method)", "Rental Contracts",
     "Frame line: qty = frames, rate = 94.50 a month (3.15 a day) including its accessories; accessories lines at rate 0 (they "
     "still count as stock out); boards on their own line; installation + dismantling and transport as charge lines with "
     "their cost. Per-piece pricing stays available — just type a rate on each piece.",
     "التسعير بالشباك (طريقة المستخلص)",
     "سطر الشباك: الكمية = عدد الشبابيك والسعر 94.50 شهرياً (3.15 يومياً) شامل المستلزمات، وسطور المستلزمات بسعر صفر (تُحسب "
     "كمخزون خارج)، واللوح البونط في سطر مستقل، والتركيب والفك والنقل كسطور رسوم مع تكلفتها. التسعير بالقطعة ما زال متاحاً."),
    ("Issue an extract (progress invoice)", "Sales Invoices, Invoice Lines",
     "One Sales Invoices row (number, date, client, business unit, Status = Posted) and one Invoice Lines row per extract "
     "line (rent → 4045, installation / transport → 4046). Keep it Draft until it is issued. → Receivables, Income "
     "Statement, Dashboard 'Invoiced', Partner Pack.",
     "إصدار مستخلص / فاتورة",
     "صف في فواتير المبيعات (الرقم، التاريخ، العميل، وحدة العمل، الحالة = Posted) وصف في بنود الفواتير لكل بند (إيجار ← 4045، "
     "تركيب / نقل ← 4046). اتركها Draft حتى تصدر ← المدينون وقائمة الدخل ولوحة المتابعة وتقرير الشركاء."),
    ("Mark how far a rental is billed", "Rental Contracts",
     "Type the last day billed in 'Invoiced to' on the contract's first row. → Dashboard shows 'still to invoice'; the app "
     "starts the next extract from the day after.",
     "تحديد آخر فترة تم إصدار مستخلص عنها",
     "اكتب آخر يوم تمت فوترته في عمود «مفوتر حتى» في أول صف للعقد ← تظهر «المتبقي للفوترة» ويبدأ المستخلص التالي من اليوم التالي."),
    ("Client pays", "Receipt Vouchers",
     "Date, client, amount, method (Cash / InstaPay …), business unit, credit account 1100. → cash balance, Receivables, "
     "Dashboard 'Collected' and 'Client owes'.",
     "تحصيل من العميل",
     "التاريخ والعميل والمبلغ وطريقة الدفع ووحدة العمل والحساب الدائن 1100 ← رصيد النقدية والمدينون و«المحصل» و«المستحق على العميل»."),
    ("Funding partner pays for equipment", "Loan Transactions, Rental Stock Lots",
     "Loan Transactions: action 'draw' on the funder's loan (0 % interest). Rental Stock Lots: the pieces, qty and unit cost "
     "(the company owns them). → Funder account, Loans, Balance Sheet, stock count.",
     "الشريك الممول يدفع ثمن معدات",
     "حركات القروض: الحركة draw على قرض الممول (بدون فائدة). دفعات معدات التأجير: القطع والكمية وتكلفة الوحدة (المعدات ملك الشركة) "
     "← حساب الممول والقروض والميزانية والمخزون."),
    ("Company buys equipment with its own money", "Rental Stock Lots",
     "Pieces, qty, unit cost, supplier, 'Paid now' (blank = paid in full). → fleet value, stock count, Payables if not fully paid.",
     "الشركة تشتري معدات من أموالها",
     "القطع والكمية وتكلفة الوحدة والمورد والمدفوع (فارغ = مدفوع بالكامل) ← قيمة الأسطول والمخزون والدائنون إن لم يُسدد بالكامل."),
    ("Repay the funder (50 % of each collection)", "Loan Transactions",
     "Action 'pay', principal = the amount, method. → Funder account balance, Loans, cash.",
     "سداد للممول (50% من كل تحصيل)",
     "الحركة pay وأصل الدين = المبلغ وطريقة الدفع ← رصيد حساب الممول والقروض والنقدية."),
    ("Pay the installation crew", "Crew Pay Calculator → Direct Costs",
     "Use the calculator to see what is due (frames × width × height × rate) — it never posts. Book what you really paid "
     "as a Direct Costs row with the site's business unit. → BU Profitability, Income Statement.",
     "دفع أجر طاقم التركيب",
     "استخدم حاسبة أجور الأطقم لمعرفة المستحق (لا تُرحّل شيئاً). سجّل المبلغ المدفوع فعلاً كصف في التكاليف المباشرة بوحدة عمل الموقع "
     "← ربحية المشروعات وقائمة الدخل."),
    ("Expenses", "Operating Expenses / Direct Costs",
     "Office, transport, tips … in Operating Expenses; anything for one site in Direct Costs with its business unit.",
     "المصروفات", "المصروفات العامة في المصروفات التشغيلية، وما يخص موقعاً واحداً في التكاليف المباشرة بوحدة العمل."),
    ("Salaries", "Employees, Payroll Run", "One Employees row per person, one Payroll Run row per person a month. Social "
     "insurance and tax are worked out for you (Payroll Settings).",
     "الرواتب", "صف لكل موظف في الموظفون والعمال، وصف لكل موظف شهرياً في مسير الرواتب. التأمينات والضريبة تُحسب تلقائياً."),
    ("Partner puts in / takes out money", "Partner Transactions", "→ Partner Equity, Partner Pack.",
     "إيداع / سحب شريك", "← حقوق الشركاء وتقرير الشركاء."),
    ("Anything else", "Journal Vouchers", "Debit and credit rows with the same voucher number; they must balance.",
     "أي حركة أخرى", "صفوف مدين ودائن بنفس رقم القيد ويجب أن تتوازن."),
]
GUIDE_RULES = [
    ("Type only in the white / blue cells; grey columns are formulas.", "اكتب في الخلايا البيضاء / الزرقاء فقط؛ الأعمدة الرمادية معادلات."),
    ("Add rows under the last one — never insert rows in the middle of the header.", "أضف الصفوف تحت آخر صف ولا تُدرج صفوفاً داخل العناوين."),
    ("Pick codes from the drop-downs (Cash, Posted, monthly …) — keep them in English.", "اختر الأكواد من القوائم المنسدلة (Cash, Posted …) واتركها بالإنجليزية."),
    ("Model Audit must say ALL OK after every batch of entries.", "يجب أن يظهر «ALL OK» في فحص سلامة الدفاتر بعد كل مجموعة قيود."),
    ("The app imports this file as it is (English or Arabic): Data → Import workbook.", "يقبل البرنامج هذا الملف كما هو (عربي أو إنجليزي): البيانات ← استيراد ملف."),
]


def _guide(wb, F, fill):
    """How to use this workbook: what to type where, and what updates by itself — in the workbook's language only."""
    from openpyxl.styles import Alignment
    ar = getattr(wb, "_lang", "en") == "ar"
    names = {}
    if ar:
        from .workbook_ar import sheet_names
        names = sheet_names()
    ws = wb.create_sheet("How to Use")
    ws.sheet_view.showGridLines = False
    ws["B2"] = "دليل الاستخدام" if ar else "HOW TO USE THIS WORKBOOK"
    ws["B2"].font = F(bold=True, size=14, color=NAVY)
    ws["B3"] = ("كل التقارير ولوحة المتابعة وتقرير الشركاء تقرأ من أوراق الإدخال: أضف صفاً في الورقة الصحيحة وكل ما يرتبط به يتحدث تلقائياً."
                if ar else "Every report, the dashboard and the Partner Pack read the entry sheets. Add a row in the right sheet and "
                           "everything related to it updates by itself.")
    ws["B3"].font = F(size=9, italic=True, color="555555")
    wrap = Alignment(wrap_text=True, vertical="top")
    heads = ["أريد أن …", "أضف صفوفاً في", "ماذا أكتب ← ماذا يتحدث"] if ar else ["I want to …", "Add rows on", "What to type → what updates"]
    for i, t in enumerate(heads):
        c = ws.cell(5, 2 + i, t)
        c.font, c.fill = F(bold=True, color="FFFFFF"), fill(NAVY)
    for r, (task, sheets, how, task_ar, how_ar) in enumerate(GUIDE, start=6):
        parts = [x.strip() for x in re.split(r"[,→/]", sheets)]
        shown = sheets
        if ar:
            for n in parts:
                shown = shown.replace(n, names.get(n, n))
            shown = shown.replace(",", "،").replace("→", "←")
        for i, v in enumerate((task_ar, shown, how_ar) if ar else (task, shown, how)):
            c = ws.cell(r, 2 + i, v)
            c.alignment = wrap
            c.font = F(bold=i == 0, size=9)
        if parts[0] in wb.sheetnames:
            link(ws.cell(r, 3), parts[0])
            ws.cell(r, 3).font = F(size=9, color="1F4E79")
    r = 6 + len(GUIDE) + 1
    ws.cell(r, 2, "قواعد" if ar else "RULES").font = F(bold=True, size=11, color=NAVY)
    for i, (en, a) in enumerate(GUIDE_RULES, start=1):
        ws.cell(r + i, 2, f"{i}. {a if ar else en}").font = F(size=9)
    for col, w in zip("ABCD", (2, 32, 30, 90)):
        ws.column_dimensions[col].width = w


SHEET_ORDER = ["Cover", "How to Use", "Partner Pack", "Dashboard", "Journal Vouchers", "Chart of Accounts", "Trial Balance", "Income Statement",
               "Balance Sheet", "Cash Flow", "BU Profitability", "Payables", "Receivables", "Partner Equity", "Business Units",
               "Sales Invoices", "Invoice Lines", "Rental Contracts", "Rental Equipment", "Rental Stock Lots", "Loans",
               "Loan Transactions", "Employees", "Payroll Run", "Price List", "BOQ Lines", "Quick Estimator", "Scaffold Calculator",
               "Crew Pay Calculator", "Extract Print", "Budget vs Actual",
               "Tax Summary", "Model Audit", "Receipt Vouchers", "Payment Vouchers", "Direct Costs", "Operating Expenses", "Payroll",
               "Partner Transactions", "Cash Custody", "Custody Expenses", "Settings", "Parties", "Partners", "Partner Shares",
               "Payroll Settings", "Tax Codes"]


# ------------------------------------------------------------------ sections a company does not use
# report / tool sheets that belong to one section of the app (data sheets carry their own "module")
EXTRA_SHEET_MODULE = {"Extract Print": "sales.extracts", "Crew Pay Calculator": "payroll.crew", "Scaffold Calculator": "rentals.calculator", "Quick Estimator": "estimating", "Budget vs Actual": "accounting.budgets",
                      "Receivables": "sales.receivables", "Payables": "purchasing.payables", "Partner Equity": "partners",
                      "Tax Summary": "tax", "BU Profitability": "business_units.profitability"}
NEVER_HIDDEN = {"Cover", "Partner Pack", "Dashboard", "Settings", "Chart of Accounts", "Journal Vouchers", "Trial Balance", "Income Statement",
                "Balance Sheet", "Cash Flow", "Model Audit"}


def _hide_list(db, hide=None):
    if hide is None:
        try:
            hide = json.loads(db.setting("hidden_modules") or "[]")
        except ValueError:
            hide = []
    return set(hide or [])


def _hidden_sheets(db, wb, data, hide=None):
    """Sheets of the sections switched off in Settings → Menus (or `hide`, a list of 'module' / 'module.screen').
    A sheet that holds rows is never hidden, so nothing in the books disappears from view."""
    from .importkit import SHEET_PERM as SHEET_MODULE
    hide = _hide_list(db, hide)
    if not hide:
        return []
    out = []
    for ws in wb.worksheets:
        name = ws.title
        rows = data.get(name) or []
        if name == "Warehouses" and len(rows) <= 1:   # only the default store: not data anyone typed
            rows = []
        if name in NEVER_HIDDEN or ws.sheet_state != "visible" or rows:
            continue
        sheet = SHEET_BY_NAME.get(name)
        key = EXTRA_SHEET_MODULE.get(name) or SHEET_MODULE.get(name) or (sheet or {}).get("module") or ""
        if not key:
            continue
        if key in hide or key.split(".")[0] in hide or ((sheet or {}).get("module") in hide):
            out.append(name)
    return out


# ------------------------------------------------------------------ dashboard
DASH_BAR, DASH_TILE, DASH_LINE = "2E4A6B", "F3F6FA", "D5DDE8"


def _dashboard(wb, data, colmap, posting, rng, pl_refs, bs_refs, n_bu, audit_ref, currency, F, fill, L, hide=()):
    """One page with the position, the period's results, each business unit, the months and where the money went —
    all formulas over the statements, so it follows every change."""
    from openpyxl.chart import BarChart, Reference
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Side
    ws = wb.create_sheet("Dashboard")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "C9A227"
    ws.column_dimensions["A"].width = 2
    for i in range(2, 14):
        ws.column_dimensions[L(i)].width = 11.5
    ws["A1"] = "Dashboard"
    ws["A1"].font = F(bold=True, size=16, color=NAVY)
    ws["A2"] = ('="Live from the books for "&TEXT(ReportFrom,"dd mmm yyyy")&" – "&TEXT(ReportTo,"dd mmm yyyy")&'
                f'"  ·  {currency}  ·  change the period on the Settings sheet"')
    ws["A2"].font = F(size=9, italic=True, color="666666")
    line = Side(style="thin", color=DASH_LINE)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def bar(r, text):
        for c in range(2, 14):
            ws.cell(r, c).fill = fill(DASH_BAR)
        ws.cell(r, 2, text).font = F(bold=True, size=10, color="FFFFFF")
        ws.row_dimensions[r].height = 20

    def span(r, c1, c2, value=None, fmt=None, font=None, bg=None, align=None):
        if c2 > c1:
            ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
        cell = ws.cell(r, c1, value)
        if fmt:
            cell.number_format = fmt
        if font:
            cell.font = font
        for c in range(c1, c2 + 1):
            if bg:
                ws.cell(r, c).fill = fill(bg)
            ws.cell(r, c).border = Border(bottom=line)
        cell.alignment = align or Alignment(vertical="center")
        return cell

    def tiles(r, items):
        for i, (label, formula, fmt) in enumerate(items):
            c1 = 2 + 2 * i
            span(r, c1, c1 + 1, label.upper(), font=F(bold=True, size=8, color="FFFFFF"), bg=NAVY, align=center)
            span(r + 1, c1, c1 + 1, formula, fmt, F(bold=True, size=15, color=NAVY), DASH_TILE, center)
        ws.row_dimensions[r].height = 26
        ws.row_dimensions[r + 1].height = 34

    tb = "'Trial Balance'"
    bal = lambda sub, sign=1: f"={'-' if sign < 0 else ''}SUMIFS({tb}!$H:$H,{tb}!$D:$D,\"{sub}\")"  # noqa: E731
    IS = lambda k: f"='Income Statement'!$B${pl_refs[k]}"  # noqa: E731
    bar(4, '="POSITION ON "&UPPER(TEXT(ReportTo,"dd mmm yyyy"))')
    tiles(5, [("Cash & bank", bal("cash"), MONEY), ("Clients owe us", bal("receivable"), MONEY),
              ("Work done, not invoiced", f"=SUMIFS({tb}!$H:$H,{tb}!$A:$A,\"1180\")", MONEY),
              ("We owe suppliers", bal("payable", -1), MONEY), ("Client advances held", bal("deferred_revenue", -1), MONEY),
              ("Stock & goods", bal("inventory"), MONEY)])
    bar(8, "RESULTS FOR THE PERIOD")
    tiles(9, [("Net sales", IS("net_sales"), MONEY), ("Gross profit", IS("gp"), MONEY), ("EBITDA", IS("ebitda"), MONEY),
              ("Net profit", IS("net"), MONEY),
              ("Gross margin", f"=IFERROR('Income Statement'!$B${pl_refs['gp']}/'Income Statement'!$B${pl_refs['net_sales']},0)", PCT),
              ("Net margin", f"=IFERROR('Income Statement'!$B${pl_refs['net']}/'Income Statement'!$B${pl_refs['net_sales']},0)", PCT)])
    st = span(12, 2, 13, f'="Books integrity (Model Audit):  "&{audit_ref}&"     ·     open the Model Audit sheet for the detail"',
              font=F(bold=True, size=10), align=center)
    link(st, "Model Audit")
    ws.row_dimensions[12].height = 22
    ws.conditional_formatting.add("B12:M12", FormulaRule(formula=[f'ISNUMBER(SEARCH("ALL OK",{audit_ref}))'], fill=fill("E3F4E8"),
                                                         font=F(bold=True, color="1E7B34")))
    ws.conditional_formatting.add("B12:M12", FormulaRule(formula=[f'NOT(ISNUMBER(SEARCH("ALL OK",{audit_ref})))'],
                                                         fill=fill("FDE7E7"), font=F(bold=True, color="B42318")))

    # ---- business units
    r = 14
    bar(r, "PROJECTS / BUSINESS UNITS  (period results, after shared overhead)")
    heads = [("Code", 2, 2), ("Name", 3, 5), ("Revenue", 6, 7), ("Direct costs", 8, 9), ("Overhead", 10, 11), ("Net profit", 12, 12),
             ("Margin", 13, 13)]
    for h, c1, c2 in heads:
        span(r + 1, c1, c2, h, font=F(bold=True, size=9, color=NAVY), bg="E6ECF4", align=center)
    n_rows = max(len(data.get("Business Units") or []) + 2, 5)
    bu = "'BU Profitability'"
    for k in range(n_rows):
        rr, src = r + 2 + k, 5 + k
        span(rr, 2, 2, f'={bu}!A{src}', font=F(size=9, bold=True))
        span(rr, 3, 5, f'={bu}!B{src}', font=F(size=9))
        span(rr, 6, 7, f'=IF($B{rr}="","",{bu}!C{src})', MONEY, F(size=9))
        span(rr, 8, 9, f'=IF($B{rr}="","",{bu}!D{src})', MONEY, F(size=9))
        span(rr, 10, 11, f'=IF($B{rr}="","",{bu}!E{src}+{bu}!G{src})', MONEY, F(size=9))
        span(rr, 12, 12, f'=IF($B{rr}="","",{bu}!H{src})', MONEY, F(size=9, bold=True))
        span(rr, 13, 13, f'=IF($B{rr}="","",{bu}!I{src})', PCT, F(size=9))
    tr = r + 2 + n_rows
    span(tr, 2, 5, "TOTAL", font=F(bold=True, size=9), bg="E6ECF4")
    for c1, c2, col in ((6, 7, "C"), (8, 9, "D"), (10, 11, None), (12, 12, "H")):
        f = f"={bu}!E{5 + n_bu}+{bu}!G{5 + n_bu}" if col is None else f"={bu}!{col}{5 + n_bu}"
        span(tr, c1, c2, f, MONEY, F(bold=True, size=9), "E6ECF4")
    span(tr, 13, 13, f"=IFERROR(L{tr}/F{tr},0)", PCT, F(bold=True, size=9), "E6ECF4")

    # ---- months of the report year (helpers in hidden columns V:Z keep each formula short)
    r = tr + 2
    bar(r, '="REVENUE AND COSTS BY MONTH — "&YEAR(ReportTo)')
    for h, c1, c2 in (("Month", 2, 3), ("Revenue", 4, 5), ("Costs", 6, 7), ("Net", 8, 9), ("", 10, 13)):
        span(r + 1, c1, c2, h, font=F(bold=True, size=9, color=NAVY), bg="E6ECF4", align=center)

    def month_sum(key, a, b):
        return "+".join(f'SUMIFS({rng(sh["name"], key)},{rng(sh["name"], "date")},">="&{a},{rng(sh["name"], "date")},"<="&{b})'
                        for sh in posting if key in colmap[sh["name"]]) or "0"
    m0 = r + 2
    for m in range(12):
        rr = m0 + m
        ws.cell(rr, 22, f"=DATE(YEAR(ReportTo),{m + 1},1)").number_format = DATE
        ws.cell(rr, 23, f"=EOMONTH(V{rr},0)").number_format = DATE
        ws.cell(rr, 24, "=" + month_sum("_rev", f"$V{rr}", f"$W{rr}"))
        ws.cell(rr, 25, "=" + month_sum("_cogs", f"$V{rr}", f"$W{rr}"))
        ws.cell(rr, 26, "=" + month_sum("_exp", f"$V{rr}", f"$W{rr}"))
        span(rr, 2, 3, f"=V{rr}", "mmm yyyy", F(size=9), align=Alignment(horizontal="left", indent=1))
        span(rr, 4, 5, f"=X{rr}", MONEY, F(size=9))
        span(rr, 6, 7, f"=Y{rr}+Z{rr}", MONEY, F(size=9))
        span(rr, 8, 9, f"=D{rr}-F{rr}", MONEY, F(size=9, bold=True))
        span(rr, 10, 13, f'=REPT("█",ROUND(10*MAX(0,D{rr})/MAX(1,MAX($D${m0}:$D${m0 + 11},$F${m0}:$F${m0 + 11})),0))&"  "',
             font=F(size=9, color="2E4A6B"))
    for col in "VWXYZ":
        ws.column_dimensions[col].hidden = True
    ch = BarChart()
    ch.type, ch.grouping, ch.overlap = "col", "clustered", -10
    ch.title, ch.height, ch.width, ch.legend.position = "Revenue vs costs", 7.5, 16, "b"
    ch.y_axis.numFmt, ch.y_axis.majorGridlines = "#,##0", None
    ch.add_data(Reference(ws, min_col=4, max_col=4, min_row=m0, max_row=m0 + 11), titles_from_data=False)
    ch.add_data(Reference(ws, min_col=6, max_col=6, min_row=m0, max_row=m0 + 11), titles_from_data=False)
    from openpyxl.chart.series import SeriesLabel
    ch.series[0].tx, ch.series[1].tx = SeriesLabel(v="Revenue"), SeriesLabel(v="Costs")
    ch.series[0].graphicalProperties.solidFill, ch.series[1].graphicalProperties.solidFill = "2E4A6B", "C9A227"
    ch.set_categories(Reference(ws, min_col=2, min_row=m0, max_row=m0 + 11))
    ws.add_chart(ch, f"O{r}")

    # ---- where the money went: each cost-of-sales account, then expenses by statement line
    r = m0 + 13
    bar(r, "WHERE THE MONEY WENT  (period)")
    for h, c1, c2 in (("Cost", 2, 5), ("Amount", 6, 7), ("Share", 8, 8), ("", 9, 13)):
        span(r + 1, c1, c2, h, font=F(bold=True, size=9, color=NAVY), bg="E6ECF4", align=center)
    lines = [(a["name"], f'SUMIFS({tb}!$F:$F,{tb}!$A:$A,"{a["code"]}")-SUMIFS({tb}!$G:$G,{tb}!$A:$A,"{a["code"]}")')
             for a in (data.get("Chart of Accounts") or []) if a.get("type") == "COGS"
             and not (a.get("role") == "cost_of_sales" and "shop" in hide) and not (a.get("role") == "stock_adjust" and "stock" in hide)]
    sub = lambda *subs: "+".join(f'SUMIFS({tb}!$F:$F,{tb}!$D:$D,"{x}")-SUMIFS({tb}!$G:$G,{tb}!$D:$D,"{x}")' for x in subs)  # noqa: E731
    lines += [("Staff (office)", sub("employee")), ("General & administrative", sub("general_admin")),
              ("Marketing", sub("marketing")), ("Utilities", sub("utilities")), ("Other operating", sub("other_operating")),
              ("Depreciation & amortisation", sub("depreciation", "amortization")), ("Interest & non-operating", sub("interest", "other_nonoperating")),
              ("Income tax", sub("income_tax"))]
    c0 = r + 2
    cend = c0 + len(lines) - 1
    for i, (name, f) in enumerate(lines):
        rr = c0 + i
        span(rr, 2, 5, name, font=F(size=9))
        span(rr, 6, 7, "=" + f, MONEY, F(size=9))
        span(rr, 8, 8, f"=IFERROR(F{rr}/$F${cend + 1},0)", PCT, F(size=9))
        span(rr, 9, 13, f'=REPT("█",ROUND(20*MAX(0,H{rr}),0))', font=F(size=9, color="C9A227"))
    span(cend + 1, 2, 5, "TOTAL", font=F(bold=True, size=9), bg="E6ECF4")
    span(cend + 1, 6, 7, f"=SUM(F{c0}:F{cend})", MONEY, F(bold=True, size=9), "E6ECF4")
    span(cend + 1, 8, 8, f"=IFERROR(F{cend + 1}/F{cend + 1},0)", PCT, F(bold=True, size=9), "E6ECF4")
    mix = BarChart()
    mix.type, mix.title, mix.height, mix.width, mix.legend = "bar", "Cost mix", 9, 12, None
    mix.y_axis.numFmt, mix.y_axis.majorGridlines = "#,##0", None
    mix.x_axis.scaling.orientation = "maxMin"
    mix.add_data(Reference(ws, min_col=6, min_row=c0, max_row=cend), titles_from_data=False)
    mix.set_categories(Reference(ws, min_col=2, min_row=c0, max_row=cend))
    mix.series[0].graphicalProperties.solidFill = "C9A227"
    ws.add_chart(mix, f"O{r}")
    end = cend + 2
    if "rentals" not in set(hide or ()) and "Rental Contracts" in colmap:
        end = _ongoing_rents(ws, colmap, cend + 3, F, fill, L)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1
    ws.print_area = f"A1:U{end}"
    return ws


def _ongoing_rents(ws, colmap, top, F, fill, L, n=10):
    """Ongoing rentals on the dashboard, live from the Rental Contracts sheet: each contract's rent a month, rent earned
    since delivery, one-time charges, what its site was invoiced (Sales Invoices) and collected (Receipt Vouchers)."""
    rc, si, rv = colmap["Rental Contracts"], colmap["Sales Invoices"], colmap["Receipt Vouchers"]
    R = lambda key: f"'Rental Contracts'!${rc[key]}$5:${rc[key]}$2000"  # noqa: E731
    ws.cell(top, 2, "ONGOING RENTALS").font = F(bold=True, size=12, color=NAVY)
    ws.cell(top + 1, 2, "From the Rental Contracts sheet (one block of rows per contract). Rent to date = rent a month ÷ 30 × days since "
                        "delivery, up to today or the report end. Invoiced and collected are the contract's business unit on Sales "
                        "Invoices and Receipt Vouchers.").font = F(size=8, italic=True, color="666666")
    heads = ["Contract", "Client", "Site", "Delivered / start", "Rent / month", "Days on hire", "Rent to date", "One-time charges",
             "Invoiced", "Collected", "Client owes", "Still to invoice"]
    h = top + 2
    for i, t in enumerate(heads):
        c = ws.cell(h, 2 + i, t)
        c.font, c.fill = F(bold=True, color="FFFFFF", size=9), fill(NAVY)
    ws.column_dimensions["W"].hidden = True
    for k in range(1, n + 1):
        r = h + k
        prev = "4" if k == 1 else f"$W{r - 1}"
        tail = f"'Rental Contracts'!${rc['x_first']}$2000"
        ws.cell(r, 23, f'=IF(""&{prev}="","",IFERROR(MATCH(1,INDEX({R("x_first")},{prev}-3):{tail},0)+{prev},""))')
        g = lambda key: f"INDEX('Rental Contracts'!${rc[key]}:${rc[key]},$W{r})"  # noqa: E731
        ws.cell(r, 2, f'=IF($W{r}="","",{g("ref")}&"")')
        ws.cell(r, 3, f'=IF($W{r}="","",{g("client")}&"")')
        ws.cell(r, 4, f'=IF($W{r}="","",{g("site")}&"")')
        ws.cell(r, 5, f'=IF($W{r}="","",IF({g("delivered_on")}<>"",{g("delivered_on")},{g("start_date")}))').number_format = "dd-mmm-yy"
        ws.cell(r, 6, f'=IF($W{r}="","",N({g("x_crent")}))').number_format = MONEY
        ws.cell(r, 7, f'=IF(OR($W{r}="",N(E{r})=0),"",MAX(0,MIN(TODAY(),ReportTo)-E{r}))').number_format = "#,##0"
        ws.cell(r, 8, f'=IF(G{r}="","",ROUND(F{r}/30*G{r},2))').number_format = MONEY
        ws.cell(r, 9, f'=IF($W{r}="","",N({g("x_charges")}))').number_format = MONEY
        bu = g("bu_code")
        ws.cell(r, 10, f"=IF($W{r}=\"\",\"\",IF({bu}&\"\"=\"\",0,SUMIFS('Sales Invoices'!${si['ar_amt']}$5:${si['ar_amt']}$2000,"
                       f"'Sales Invoices'!${si['bu_code']}$5:${si['bu_code']}$2000,{bu}&\"\")))").number_format = MONEY
        ws.cell(r, 11, f"=IF($W{r}=\"\",\"\",IF({bu}&\"\"=\"\",0,SUMIFS('Receipt Vouchers'!${rv['amount']}$5:${rv['amount']}$2000,"
                       f"'Receipt Vouchers'!${rv['bu_code']}$5:${rv['bu_code']}$2000,{bu}&\"\")))").number_format = MONEY
        ws.cell(r, 12, f'=IF($W{r}="","",J{r}-K{r})').number_format = MONEY
        ws.cell(r, 13, f'=IF($W{r}="","",MAX(0,N(H{r})+I{r}-J{r}))').number_format = MONEY
        for c in range(2, 14):
            ws.cell(r, c).font = F(size=9)
    t = h + n + 1
    ws.cell(t, 2, "TOTAL").font = F(bold=True)
    for c in (6, 8, 9, 10, 11, 12, 13):
        x = ws.cell(t, c, f"=SUM({L(c)}{h + 1}:{L(c)}{h + n})")
        x.font, x.number_format = F(bold=True), MONEY
    return t + 1


# ------------------------------------------------------------------ cover
COVER_SECTIONS = [
    ("START HERE", [("How to Use", "What to type on which sheet, and what updates by itself"),
                    ("Partner Pack", "The partners' management accounts: billed profit, work in progress, statements, each partner's equity"),
                    ("Dashboard", "Live position: cash, receivables, payables, profit, margins, projects, months"),
                    ("Settings", "Company details and the report period (report_from / report_to)")]),
    ("EVERY DAY", [("Journal Vouchers", "Any entry as balanced debit and credit lines"),
                   ("Direct Costs", "Project costs: materials, subcontractors, site labour"),
                   ("Operating Expenses", "Office and company overhead"), ("Payroll", "Quick wage entries (one line per worker)"),
                   ("Receipt Vouchers", "Money received from clients"), ("Payment Vouchers", "Payments to suppliers and staff"),
                   ("Sales Invoices", "One row per invoice — header and terms"), ("Invoice Lines", "The lines of each invoice"),
                   ("Cash Custody", "Cash given to staff (custody)"), ("Custody Expenses", "What the custody was spent on"),
                   ("Partner Transactions", "Capital in, drawings and distributions"),
                   ("Receivables", "What each client owes, less advances"), ("Payables", "What you owe each supplier and worker")]),
    ("THE ACCOUNTS", [("Chart of Accounts", "The account list and how each maps to the statements"),
                      ("Trial Balance", "Every account: opening, period debits and credits, closing"),
                      ("Income Statement", "Net sales → gross profit → EBITDA → net profit"),
                      ("Balance Sheet", "What the business owns and owes"), ("Cash Flow", "Where the cash actually went"),
                      ("BU Profitability", "Each project's own result, after shared overhead"),
                      ("Partner Equity", "Profit share, reserve, drawings and balances"),
                      ("Budget vs Actual", "The budget for the period against the books"),
                      ("Tax Summary", "VAT and withholding balances"), ("Tax Codes", "VAT / withholding rates")]),
    ("PEOPLE & PAYROLL", [("Employees", "Staff and workers: pay type, allowances, insurance, tax, cost type and project"),
                          ("Payroll Run", "Monthly payroll: absence, overtime, bonuses, penalties, advances → insurance, tax, net"),
                          ("Payroll Settings", "Insurance rates and limits, tax bands, overtime rates, the pay items"),
                          ("Crew Pay Calculator", "Measured-work pay (frames × width × height m² × rate) — a calculator only")]),
    ("PROJECTS & PRICING", [("Business Units", "Projects: client, billing basis and fee"), ("Parties", "Clients, suppliers, staff"),
                            ("Partners", "Owners and their default share"), ("Partner Shares", "Each partner's share per project"),
                            ("BOQ Lines", "Bills of quantities"), ("Price List", "Your priced item library"),
                            ("Quick Estimator", "A fast budget estimate from the price list")]),
]


def _cover(wb, cover, company, tagline, template, currency, audit_ref, hidden, F, fill, extra=None):
    """A cover in the style of the original BEYOND workbook: the name, what the file is, then every visible sheet
    grouped by what it is for, each a link."""
    from openpyxl.styles import Alignment, Border, Side
    for row in cover.iter_rows():
        for c in row:
            c.value, c.fill, c.font, c.border, c.hyperlink = None, fill("FFFFFF"), F(), Border(), None
    cover.sheet_properties.tabColor = BROWN
    for col, w in (("A", 3), ("B", 30), ("C", 62), ("D", 22)):
        cover.column_dimensions[col].width = w
    cover.row_dimensions[1].height = 10
    cover["B2"] = (company or "Company Name").upper()
    cover["B2"].font = F(bold=True, size=24, color=NAVY)
    cover.row_dimensions[2].height = 34
    cover["B3"] = "   ·   ".join(x.strip().upper() for x in str(tagline or "").split("·") if x.strip())
    cover["B3"].font = F(bold=True, size=9, color="2E6DA4")
    cover["B5"] = "Accounting & Project Control System"
    cover["B5"].font = F(bold=True, size=15, color="1F1F1F")
    cover["B6"] = (f"Double-entry books with live statements. Type in the data sheets (blue text); every statement, the dashboard "
                   f"and the checks recalculate by themselves. All values in {currency}. Import this same file back into the app "
                   f"from Import & Export." + ("  —  EMPTY TEMPLATE" if template else ""))
    cover["B6"].font = F(italic=True, size=9, color="555555")
    cover.merge_cells("B6:C7")
    cover["B6"].alignment = Alignment(wrap_text=True, vertical="top")
    cover.row_dimensions[6].height = 18
    for c in range(2, 4):
        cover.cell(8, c).border = Border(bottom=Side(style="medium", color=BROWN))
    line = Side(style="thin", color="D5DDE8")
    visible = {ws.title for ws in wb.worksheets if ws.sheet_state == "visible"}
    listed = set()
    sections = ([("THIS COMPANY", [(n, d) for n, d in extra if n in visible])] if extra else []) + \
        [(t, [(n, d) for n, d in items if n in visible]) for t, items in COVER_SECTIONS]
    others = [ws.title for ws in wb.worksheets if ws.title in visible and ws.title not in ("Cover", "Model Audit")
              and not any(ws.title == n for _, items in COVER_SECTIONS for n, _ in items) and not any(ws.title == n for n, _ in (extra or []))]
    descs = {s["name"]: s["title"].split(" — ")[0].split(". ")[0] for s in SHEETS}
    descs.update({"Scaffold Calculator": "Pieces, weight, rent and installation for a façade",
                  "Quick Estimator": "A fast budget estimate from the price list"})
    if others:
        sections.append(("MORE MODULES", [(n, descs.get(n, "")) for n in others]))
    sections.append(("CHECKS", [("Model Audit", "Every integrity check, live — must read ALL OK")]))
    r = 10
    for title, items in sections:
        if not items:
            continue
        for c in range(2, 4):
            cover.cell(r, c).fill = fill("2E4A6B")
        cover.cell(r, 2, title).font = F(bold=True, size=10, color="FFFFFF")
        cover.row_dimensions[r].height = 20
        r += 1
        for name, desc in items:
            c = cover.cell(r, 2, name)
            link(c, name)
            c.font = F(bold=True, size=10, color="1155CC", underline="single")
            d = cover.cell(r, 3, desc[:110])
            d.font = F(size=9, color="333333")
            d.alignment = Alignment(horizontal="left", vertical="center", readingOrder=1)
            for cc in (2, 3):
                cover.cell(r, cc).border = Border(bottom=line)
            listed.add(name)
            r += 1
        r += 1
    cover.cell(r, 2, "Books integrity").font = F(bold=True, size=10, color=NAVY)
    cover.cell(r, 3, f"={audit_ref}").font = F(bold=True, size=10, color=NAVY)
    r += 2
    if getattr(wb, "_lang", "en") == "ar":
        from .workbook_ar import sheet_names
        nm = sheet_names()
        note = ("أوراق الأقسام التي لا تستخدمها الشركة مخفية (" + "، ".join(nm.get(h, h) for h in hidden) + "). أظهر القسم من البرنامج "
                "(الإعدادات ← القوائم) ثم حمّل الملف مرة أخرى، أو أظهر الورقة من إكسل.") if hidden else \
            "كل الأقسام ظاهرة. الأقسام المُغلقة في البرنامج (الإعدادات ← القوائم) تُخفى من هذا الملف."
    else:
        note = ("Sheets for sections this company does not use are hidden (" + ", ".join(hidden) + "). Show a section in the app "
                "(Settings → Menus) and download again, or unhide the sheet in Excel.") if hidden else \
            "Every section is shown. Sections switched off in the app (Settings → Menus) are hidden from this file."
    cover.cell(r, 2, note).font = F(italic=True, size=8, color="777777")
    cover.merge_cells(start_row=r, start_column=2, end_row=r + 1, end_column=3)
    cover.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
    cover.freeze_panes = None
    return cover


def link(cell, sheet, cell_ref="A1"):
    """A link to another sheet of this workbook (an in-file location, not a web address), so Excel, LibreOffice,
    Google Sheets and phone apps all follow it."""
    from openpyxl.worksheet.hyperlink import Hyperlink
    cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'{sheet}'!{cell_ref}", display=str(cell.value or sheet))


def _home(ws):
    """A '⌂ Home' cell beside the title that opens the Cover: past the end of the title, and outside the print area
    on sheets that are printed."""
    from openpyxl.utils import get_column_letter as L
    from openpyxl.utils import column_index_from_string as CI
    a1 = ws.cell(1, 1)
    if not a1.value:
        return
    title = str(a1.value)
    if title.startswith("="):
        title = "x" * 40
    need = len(title) * 1.5 + 2                    # the title is bold 14 pt: about 1.5 character widths per letter
    for rng in ws.merged_cells.ranges:             # a merged title spans its range
        if rng.min_row == 1 and rng.min_col == 1:
            need = max(need, sum((ws.column_dimensions[L(c)].width or 8.43) for c in range(1, rng.max_col + 1)))
    col, acc_w = 1, 0.0
    while acc_w < need and col < 60:
        acc_w += ws.column_dimensions[L(col)].width or 8.43
        col += 1
    pa = ws.print_area
    if pa:                                         # printed sheets: keep it off the page
        last = str(pa).split(":")[-1].replace("$", "").split("!")[-1]
        col = max(col, CI("".join(ch for ch in last if ch.isalpha())) + 1)
    while ws.cell(1, col).value not in (None, ""):
        col += 1
    c = ws.cell(1, col, "⌂ Home")
    link(c, "Cover")
    from openpyxl.styles import Font
    c.font = Font(bold=True, size=10, color="1155CC", underline="single")


def _polish(wb, data, fill):
    """Finishing touches on every sheet: a Home link beside the title opens the Cover; data sheets get light row banding over
    the rows in use, their header row repeated on every printed page, and landscape pages one page wide."""
    from openpyxl.formatting.rule import FormulaRule
    for ws in wb.worksheets:
        if ws.title in ("Cover", "Lists"):
            continue
        _home(ws)
        if ws.title not in SHEET_BY_NAME:
            continue
        last_col = ws.max_column
        from openpyxl.utils import get_column_letter as L
        end = max(ws.max_row, FIRST_DATA_ROW + 50)
        ws.conditional_formatting.add(f"A{FIRST_DATA_ROW}:{L(last_col)}{end}",
                                      FormulaRule(formula=[f"AND(MOD(ROW(),2)=0,COUNTA($A{FIRST_DATA_ROW}:$C{FIRST_DATA_ROW})>0)"],
                                                  fill=fill("F5F8FC")))
        ws.print_title_rows = f"{HEADER_ROW}:{HEADER_ROW}"
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_view.zoomScale = 90
        rows = len(data.get(ws.title) or [])
        ws.print_area = f"A1:{L(last_col)}{FIRST_DATA_ROW + max(rows, 15) - 1}"


# ------------------------------------------------------------------ partner pack
def _partner_pack(wb, data, colmap, posting, rng, within, upto, pl_refs, n_bu, audit_ref, currency, F, fill, L, wipacct, custacct):
    """The partners' management accounts (BEYOND V1.4 'Partner Pack'), live formulas over this workbook:
    at a glance & project results, income statement earned vs cash, balance sheet, cash flow, partners' equity.
    Profit is distributable once BILLED: a project's billed profit = its net result when it has no revenue or makes a
    loss, else net x (revenue - work in progress) / revenue; the rest waits in work in progress."""
    from openpyxl.styles import Alignment, Border, Side
    ws = wb.create_sheet("Partner Pack")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "C9A227"
    for col, w in zip("ABCDEFGH", (58, 18, 18, 18, 18, 18, 18, 18)):
        ws.column_dimensions[col].width = w
    tb = "'Trial Balance'"
    IS = lambda k: f"'Income Statement'!$B${pl_refs[k]}"  # noqa: E731
    H = lambda subs: "+".join(f'SUMIFS({tb}!$H:$H,{tb}!$D:$D,"{x}")' for x in subs)  # closing, Dr-Cr  # noqa: E731
    MV = lambda subs: "+".join(f'(SUMIFS({tb}!$F:$F,{tb}!$D:$D,"{x}")-SUMIFS({tb}!$G:$G,{tb}!$D:$D,"{x}"))' for x in subs)  # noqa: E731
    OPN = lambda subs: "+".join(f'SUMIFS({tb}!$E:$E,{tb}!$D:$D,"{x}")' for x in subs)  # noqa: E731
    CODE = lambda cell: f'SUMIFS({tb}!$H:$H,{tb}!$A:$A,{cell})'  # noqa: E731

    def tot(key, extra=""):
        parts = [f"SUMIFS({rng(sh['name'], key)}{within(sh['name'])}{extra})" for sh in posting if key in colmap[sh["name"]]]
        return "+".join(parts) or "0"

    def tot_by(key, col_key, cell):
        parts = [f"SUMIFS({rng(sh['name'], key)},{rng(sh['name'], col_key)},{cell}{within(sh['name'])})"
                 for sh in posting if key in colmap[sh["name"]] and col_key in colmap[sh["name"]]]
        return "+".join(parts) or "0"

    def tot_upto_by(key, col_key, cell):
        parts = [f"SUMIFS({rng(sh['name'], key)},{rng(sh['name'], col_key)},{cell}{upto(sh['name'])})"
                 for sh in posting if key in colmap[sh["name"]] and col_key in colmap[sh["name"]]]
        return "+".join(parts) or "0"

    line = Side(style="thin", color="D5DDE8")
    r = [1]

    def put(label, f=None, fmt=MONEY, style="item", indent=0, cols=None):
        row = r[0]
        c = ws.cell(row, 1, label)
        c.font = F(bold=style in ("total", "grand", "section", "head"), size=10 if style != "section" else 11,
                   color="FFFFFF" if style == "section" else ("555555" if style == "note" else "111111"), italic=style == "note")
        c.alignment = Alignment(indent=indent, wrap_text=style == "note", vertical="top")
        vals = cols if cols is not None else ([f] if f is not None else [])
        for i, v in enumerate(vals):
            cell = ws.cell(row, 2 + i, v)
            cell.number_format = fmt if not isinstance(fmt, (list, tuple)) else fmt[i]
            cell.font = F(bold=style in ("total", "grand", "head"), size=10, color="1F2A44" if style == "head" else "111111")
            if style == "head":
                cell.alignment = Alignment(horizontal="center", wrap_text=True)
        span = max(len(vals), 1) + 1
        for ci in range(1, max(span, 8) + 1 if style in ("section", "grand", "total") else span + 1):
            if style == "section":
                ws.cell(row, ci).fill = fill("2E4A6B")
            elif style == "grand":
                ws.cell(row, ci).fill = fill("E6ECF4")
                ws.cell(row, ci).border = Border(top=Side(style="thin", color="111111"), bottom=Side(style="double", color="111111"))
            elif style == "total":
                ws.cell(row, ci).fill = fill("F3F6FA")
                ws.cell(row, ci).border = Border(top=Side(style="thin", color="9CA3AF"))
            elif style in ("item", "head"):
                ws.cell(row, ci).border = Border(bottom=line)
        if style == "note":
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
            ws.row_dimensions[row].height = 30
        r[0] += 1
        return row

    ws["A1"] = "Partner Pack"
    ws["A1"].font = F(bold=True, size=16, color=NAVY)
    ws["A2"] = f'="Management accounts as at "&TEXT(ReportTo,"dd mmmm yyyy")&"   ·   {currency}   ·   period from "&TEXT(ReportFrom,"dd mmm yyyy")'
    ws["A2"].font = F(size=9, italic=True, color="666666")
    r[0] = 4

    # ---- hidden helper block (columns K:Z): per project and per partner
    bu = "'BU Profitability'"
    bus = "'Business Units'"
    bl = colmap["Business Units"]
    n_rows = max(len(data.get("Business Units") or []) + 3, 6)
    H0 = 300                              # helper rows start here, out of the printed area
    ws.cell(H0 - 1, 11, "Helpers (project & partner workings for the Partner Pack)").font = F(bold=True, size=8, color="777777")
    heads = ["Code", "Name", "Basis", "Revenue", "Total cost", "Net profit", "Work in progress", "Billed profit", "Collected"]
    for i, h in enumerate(heads):
        ws.cell(H0, 11 + i, h).font = F(bold=True, size=8)
    for k in range(n_rows):
        rr, src = H0 + 1 + k, 5 + k
        ws.cell(rr, 11, f'={bu}!A{src}&""')
        ws.cell(rr, 12, f'={bu}!B{src}')
        ws.cell(rr, 13, f'=IF(K{rr}="","",IFERROR(INDEX({bus}!${bl["basis"]}$5:${bl["basis"]}$3000,MATCH(K{rr},{bus}!$A$5:$A$3000,0)),""))')
        ws.cell(rr, 14, f'=IF(K{rr}="",0,N({bu}!C{src}))')
        ws.cell(rr, 15, f'=IF(K{rr}="",0,N({bu}!D{src})+N({bu}!E{src})+N({bu}!G{src}))')
        ws.cell(rr, 16, f'=IF(K{rr}="",0,N({bu}!H{src}))')
        ws.cell(rr, 17, f'=IF(K{rr}="",0,{tot_by("_wip", "bu_code", f"$K{rr}")})')
        ws.cell(rr, 18, f'=IF(K{rr}="",0,IF(N{rr}=0,P{rr},IF(P{rr}<0,P{rr},P{rr}*(N{rr}-Q{rr})/N{rr})))')
        ws.cell(rr, 19, f'=IF(K{rr}="",0,{tot_by("_cin", "bu_code", f"$K{rr}")})')
    he = H0 + n_rows
    hr = f"${H0 + 1}:$"
    rngh = lambda col: f"${col}${H0 + 1}:${col}${he}"  # noqa: E731
    # company-level
    ws.cell(he + 2, 11, "Company overhead not charged to a project")
    ws.cell(he + 2, 12, f"={IS('net')}-SUM({rngh('P')})")
    LEFT = f"$L${he + 2}"
    ws.cell(he + 3, 11, "Billed profit — company")
    ws.cell(he + 3, 12, f"=SUM({rngh('R')})+{LEFT}")
    BILLED = f"$L${he + 3}"
    ws.cell(he + 4, 11, "Profit held in work in progress")
    ws.cell(he + 4, 12, f"={IS('net')}-{BILLED}")
    WIPP = f"$L${he + 4}"
    ws.cell(he + 5, 11, "Reserve %")
    st = colmap["Settings"]
    ws.cell(he + 5, 12, f'=IFERROR(VALUE(INDEX(Settings!${st["value"]}$5:${st["value"]}$500,MATCH("reserve_pct",Settings!$A$5:$A$500,0))&""),0.4)')
    RES = f"$L${he + 5}"
    ws.cell(he + 6, 11, "Months of activity")
    ws.cell(he + 6, 11, "Months of activity (first entry to today / report end)")
    firsts = ",".join(f'IF(COUNT({rng(sh["name"], "date")})=0,99999,MIN({rng(sh["name"], "date")}))' for sh in posting if "date" in colmap[sh["name"]])
    ws.cell(he + 5, 14, f"=MIN({firsts})")
    ws.cell(he + 5, 14).number_format = DATE
    ws.cell(he + 6, 12, f'=MAX(1,(YEAR(MIN(ReportTo,TODAY()))-YEAR($N${he + 5}))*12+MONTH(MIN(ReportTo,TODAY()))-MONTH($N${he + 5})+1)')
    ws.cell(he + 7, 11, "Cash safety floor (3 months of overhead + owed)")
    ws.cell(he + 7, 12, f'=({H(("employee", "general_admin", "marketing", "utilities", "other_operating"))})/L{he + 6}*3-({H(("payable", "accrued"))})')
    FLOOR = f"$L${he + 7}"
    for i, (key, lab) in enumerate((("_cr", "Revenue received in cash"), ("_ccogs", "Direct costs paid in cash"),
                                    ("_cemp", "Staff costs paid in cash"), ("_cga", "G&A paid in cash"), ("_coth", "Other costs paid in cash"))):
        ws.cell(he + 8 + i, 11, lab)
        ws.cell(he + 8 + i, 12, "=" + tot(key))
    CASH = {"_cr": f"$L${he + 8}", "_ccogs": f"$L${he + 9}", "_cemp": f"$L${he + 10}", "_cga": f"$L${he + 11}", "_coth": f"$L${he + 12}"}
    # partners (up to 6, from the Partners sheet) and their share of each project
    pl = colmap["Partners"]
    psh = colmap["Partner Shares"]
    P0 = he + 15
    ws.cell(P0, 11, "Partner").font = F(bold=True, size=8)
    for j in range(6):
        c = 12 + j
        ws.cell(P0, c, f'=IF(Partners!$A${5 + j}="","",Partners!$A${5 + j}&"")')
        ws.cell(P0 + 1, c, f'=N(Partners!${pl["default_share"]}${5 + j})')
        PN, PD = f"{L(c)}${P0}", f"{L(c)}${P0 + 1}"
        for k in range(n_rows):
            rr = H0 + 1 + k
            cnt = f"COUNTIFS('Partner Shares'!$A$5:$A$3000,{PN},'Partner Shares'!${psh['bu_code']}$5:${psh['bu_code']}$3000,$K{rr})"
            sm = f"SUMIFS('Partner Shares'!${psh['share']}$5:${psh['share']}$3000,'Partner Shares'!$A$5:$A$3000,{PN},'Partner Shares'!${psh['bu_code']}$5:${psh['bu_code']}$3000,$K{rr})"
            ws.cell(P0 + 2 + k, c, f'=IF(OR({PN}="",$K{rr}=""),0,IF({cnt}>0,{sm},{PD}))')
        ws.cell(P0 + 2 + n_rows, c, f'=IF({PN}="",0,SUMPRODUCT({rngh("R")},{L(c)}{P0 + 2}:{L(c)}{P0 + 1 + n_rows})+{LEFT}*{PD})')
        ws.cell(P0 + 3 + n_rows, c, f'=IF({L(c)}{P0 + 2 + n_rows}>0,{L(c)}{P0 + 2 + n_rows}*(1-{RES}),{L(c)}{P0 + 2 + n_rows})')
        ws.cell(P0 + 4 + n_rows, c, f'=IF({PN}="",0,{tot_upto_by("_cap", "_pty", PN)})')
        ws.cell(P0 + 5 + n_rows, c, f'=IF({PN}="",0,{tot_upto_by("_drw", "_pty", PN)})')
    for i, lab in enumerate(("Profit share before reserve", "Share of billed profit", "Capital put in", "Distributions")):
        ws.cell(P0 + 2 + n_rows + i, 11, lab)
    SHARE, CAP, DRW = P0 + 3 + n_rows, P0 + 4 + n_rows, P0 + 5 + n_rows
    for row in ws.iter_rows(min_row=H0 - 1, max_row=DRW, min_col=11, max_col=19):
        for c in row:
            c.font = F(size=8, color="777777")

    # ---- 1. at a glance
    put("1.  AT A GLANCE", style="section")
    put("Net profit — billed (what partners can distribute from)", f"={BILLED}", style="total")
    put("Work in progress — value of work done, not yet invoiced", f"={CODE(wipacct)}")
    put("Expected profit on that work (NOT distributable until invoiced)", f"={WIPP}")
    cash_rev = f"$L${he + 8}"
    cash_cost = f"$L${he + 9}+$L${he + 10}+$L${he + 11}+$L${he + 12}"
    cn = put("Net profit — cash actually received", f"={cash_rev}-({cash_cost})")
    put("Cash & bank", f"={H(('cash',))}")
    put("Clients — invoiced, not yet collected", f"={H(('receivable', 'allowance_doubtful'))}")
    put("Owed by the company (suppliers & staff)", f"=-({H(('payable', 'accrued'))})")
    put("Client advances held (work not yet done)", f"=-({H(('deferred_revenue',))})")
    put("Loans owed", f"=-({H(('short_term_debt', 'long_term_debt'))})")
    put("Partner capital — funding", f"=-({H(('capital', 'partner_current'))})")
    cash_now = H(("cash",))
    put("Distribution recommendation", f'=IF(({cash_now})<{FLOOR},"DO NOT DISTRIBUTE — below the cash floor ("&TEXT({FLOOR},"#,##0.00")&")",'
                                         f'"Distribution possible up to "&TEXT(({cash_now})-{FLOOR},"#,##0.00")&" above the cash floor")', fmt="General")
    put("Books integrity (Model Audit)", f'=IF({audit_ref}="ALL OK","ALL INTEGRITY CHECKS PASS","REVIEW THE MODEL AUDIT")', fmt="General")
    r[0] += 1
    put("PROJECT RESULTS", style="section")
    put("Project", cols=["Billing basis", "Revenue billed", "Work in progress", "Total cost", "Net profit — billed", "Collected (cash)"],
        style="head", fmt="General")
    p_first = r[0]
    for k in range(n_rows):
        hrw = H0 + 1 + k
        put(f'=IF(K{hrw}="","",K{hrw}&"  ·  "&L{hrw})', cols=[f'=IF(K{hrw}="","",M{hrw})', f'=IF(K{hrw}="","",N{hrw}-Q{hrw})',
                                                                f'=IF(K{hrw}="","",Q{hrw})', f'=IF(K{hrw}="","",O{hrw})',
                                                                f'=IF(K{hrw}="","",R{hrw})', f'=IF(K{hrw}="","",S{hrw})'],
            fmt=["General", MONEY, MONEY, MONEY, MONEY, MONEY])
    p_last = r[0] - 1
    put("Company overhead not charged to any project", cols=["", "", "", f"=-{LEFT}", f"={LEFT}", ""], fmt=MONEY)
    put("TOTAL", cols=["", f"=SUM(C{p_first}:C{p_last})", f"=SUM(D{p_first}:D{p_last})", f"=SUM(E{p_first}:E{p_last})-{LEFT}",
                       f"={BILLED}", f"=SUM(G{p_first}:G{p_last})"], style="total")
    put("Billed = invoiced to the client (or settled). Work in progress = work done but not invoiced yet: its revenue and profit are held back "
        "until the invoice is issued, so a project in progress shows no billed profit. A project heading for a loss shows that loss at once. "
        "Collected = cash actually received.", style="note")
    r[0] += 1

    # ---- 2. income statement, earned vs cash
    put("2.  INCOME STATEMENT — earned and cash side by side", style="section")
    put("", cols=["Earned (accrual)", "Cash basis"], style="head", fmt="General")
    rv = put("Revenue earned from projects", cols=[f"={IS('net_sales')}", f"={cash_rev}"])
    cg = put("Direct project costs (materials, subcontractors, site labour)", cols=[f"=-{IS('cogs')}", f"=-{CASH['_ccogs']}"])
    gp = put("GROSS PROFIT", cols=[f"=B{rv}+B{cg}", f"=C{rv}+C{cg}"], style="total")
    put("Gross margin", cols=[f"=IFERROR(B{gp}/B{rv},0)", f"=IFERROR(C{gp}/C{rv},0)"], fmt=PCT, indent=1)
    e1 = put("Staff salaries (office)", cols=[f"=-{IS('emp')}", f"=-{CASH['_cemp']}"], indent=1)
    e2 = put("General & administrative", cols=[f"=-({IS('ga')}+{IS('mkt')}+{IS('util')})", f"=-{CASH['_cga']}"], indent=1)
    e3 = put("Other operating expenses", cols=[f"=-{IS('oth')}", f"=-{CASH['_coth']}"], indent=1)
    ox = put("Total operating expenses", cols=[f"=-(B{e1}+B{e2}+B{e3})", f"=-(C{e1}+C{e2}+C{e3})"], style="total")
    eb = put("EBITDA", cols=[f"=B{gp}-B{ox}", f"=C{gp}-C{ox}"], style="total")
    dp = put("Depreciation & amortisation", cols=[f"=-({IS('dep')}+{IS('amort')})", 0])
    it = put("Interest & non-operating (net of other income)", cols=[f"=-({IS('int')}+{IS('nonop')}-{IS('oi')})", 0])
    pb = put("PROFIT BEFORE TAX", cols=[f"=B{eb}+B{dp}+B{it}", f"=C{eb}+C{dp}+C{it}"], style="total")
    tx = put("Income tax", cols=[f"=-{IS('tax')}", 0])
    npr = put("NET PROFIT", cols=[f"=B{pb}+B{tx}", f"=C{pb}+C{tx}"], style="grand")
    put("Net margin", cols=[f"=IFERROR(B{npr}/B{rv},0)", f"=IFERROR(C{npr}/C{rv},0)"], fmt=PCT, indent=1)
    put("Of which: profit on work in progress, not yet billed (not distributable)", f"={WIPP}")
    put("NET PROFIT — BILLED (distributable base)", f"={BILLED}", style="total")
    put("Earned basis counts revenue when the work is done and costs when incurred. Cash basis counts only money that moved in the same entry "
        "(collections and payments).", style="note")
    r[0] += 1

    # ---- 3. balance sheet
    put("3.  BALANCE SHEET — what the company owns and owes", style="section")
    put("", cols=['="As at "&TEXT(ReportTo,"dd mmm yyyy")'], style="head", fmt="General")
    a = [put("Cash & bank", f"={H(('cash',))}", indent=1),
         put("Clients — invoiced, not yet collected", f"={H(('receivable', 'allowance_doubtful'))}", indent=1),
         put("Work executed, not yet invoiced (work in progress)", f"={CODE(wipacct)}", indent=1),
         put("Materials inventory", f"={H(('inventory',))}", indent=1),
         put("Advances paid to suppliers", f"={H(('prepaid',))}", indent=1),
         put("Cash held by staff (custody)", f'=IF({custacct}="",0,{CODE(custacct)})', indent=1),
         put("Other current assets", f"={H(('other_current_asset',))}-{CODE(wipacct)}-IF({custacct}=\"\",0,{CODE(custacct)})", indent=1)]
    tca = put("Total current assets", f"=SUM(B{a[0]}:B{a[-1]})", style="total")
    n = [put("Equipment at cost", f"={H(('fixed_asset',))}", indent=1),
         put("Less: accumulated depreciation", f"={H(('accumulated_depreciation',))}", indent=1),
         put("Other non-current assets", f"={H(('intangible', 'accumulated_amortization', 'investment', 'other_noncurrent_asset'))}", indent=1)]
    tnca = put("Total non-current assets", f"=SUM(B{n[0]}:B{n[-1]})", style="total")
    ta = put("TOTAL ASSETS", f"=B{tca}+B{tnca}", style="grand")
    li = [put("Owed to suppliers & subcontractors", f"=-({H(('payable',))})", indent=1),
          put("Salaries owed", f"=-({H(('accrued',))})", indent=1),
          put("Client advances — paid before work is done", f"=-({H(('deferred_revenue',))})", indent=1),
          put("Taxes payable", f"=-({H(('tax_payable',))})", indent=1),
          put("Loans", f"=-({H(('short_term_debt',))})", indent=1),
          put("Other current liabilities", f"=-({H(('other_current_liability', 'dividends_payable'))})", indent=1)]
    tcl = put("Total current liabilities", f"=SUM(B{li[0]}:B{li[-1]})", style="total")
    ncl = put("Non-current liabilities", f"=-({H(('long_term_debt', 'other_noncurrent_liability'))})", indent=1)
    tl = put("TOTAL LIABILITIES", f"=B{tcl}+B{ncl}", style="total")
    eq = [put("Partner capital — funding", f"=-({H(('capital', 'partner_current'))})", indent=1),
          put("Reserves & retained earnings", f"=-({H(('reserves', 'retained_earnings', 'other_equity'))})", indent=1),
          put("Profit to date (incl. work in progress)", f"=-({H(('operating_revenue', 'sales_discount', 'other_income', 'cogs', 'employee', 'general_admin', 'marketing', 'utilities', 'other_operating', 'depreciation', 'amortization', 'interest', 'other_nonoperating', 'income_tax'))})", indent=1),
          put("Less: distributions paid to partners", f"=-({H(('drawings',))})", indent=1)]
    te = put("TOTAL PARTNERS' EQUITY", f"=SUM(B{eq[0]}:B{eq[-1]})", style="total")
    tle = put("TOTAL LIABILITIES + PARTNERS' EQUITY", f"=B{tl}+B{te}", style="grand")
    put(f'=IF(ABS(B{ta}-B{tle})<0.01,"✓  Balanced — total assets equal total liabilities plus equity","✗  NOT balanced — open the Model Audit")',
        style="note")
    put("Working capital (current assets − current liabilities)", f"=B{tca}-B{tcl}")
    put("Current ratio (current assets ÷ current liabilities)", f'=IFERROR(B{tca}/B{tcl},"")', fmt="0.00")
    put("Cash after paying what is owed to suppliers & staff", f"=B{a[0]}-B{li[0]}-B{li[1]}")
    r[0] += 1

    # ---- 4. cash flow (period movements)
    put("4.  CASH FLOW — where the cash actually went", style="section")
    c0 = put("Net profit (earned basis)", f"=B{npr}")
    cs = [put("Add back: depreciation", f"=-({MV(('accumulated_depreciation', 'accumulated_amortization'))})", indent=1),
          put("Less: work done not yet collected (receivables & WIP)", f"=-({MV(('receivable', 'allowance_doubtful'))})-(SUMIFS({tb}!$F:$F,{tb}!$A:$A,{wipacct})-SUMIFS({tb}!$G:$G,{tb}!$A:$A,{wipacct}))", indent=1),
          put("Less: materials bought for stock", f"=-({MV(('inventory',))})", indent=1),
          put("Less: advances, custody & other current assets", f"=-({MV(('prepaid', 'other_current_asset'))})+(SUMIFS({tb}!$F:$F,{tb}!$A:$A,{wipacct})-SUMIFS({tb}!$G:$G,{tb}!$A:$A,{wipacct}))", indent=1),
          put("Add: owed to suppliers, not yet paid", f"=-({MV(('payable',))})", indent=1),
          put("Add: salaries owed, not yet paid", f"=-({MV(('accrued',))})", indent=1),
          put("Add: client advances received", f"=-({MV(('deferred_revenue',))})", indent=1),
          put("Add: taxes & other liabilities owed", f"=-({MV(('tax_payable', 'other_current_liability', 'dividends_payable'))})", indent=1)]
    ops = put("Net cash from operations", f"=B{c0}+SUM(B{cs[0]}:B{cs[-1]})", style="total")
    eqp = put("Equipment & other long-term assets bought", f"=-({MV(('fixed_asset', 'intangible', 'investment', 'other_noncurrent_asset'))})", indent=1)
    lns = put("Loans received / (repaid)", f"=-({MV(('short_term_debt', 'long_term_debt', 'other_noncurrent_liability'))})", indent=1)
    cin = put("Partner capital & funding put in", f"=-({MV(('capital', 'partner_current', 'reserves', 'retained_earnings', 'other_equity'))})", indent=1)
    dis = put("Distributions paid to partners", f"=-({MV(('drawings',))})", indent=1)
    fin = put("Net cash from investment, loans & partners", f"=B{eqp}+B{lns}+B{cin}+B{dis}", style="total")
    op = put("Opening cash", f"={OPN(('cash',))}")
    ch = put("Net change in cash", f"=B{ops}+B{fin}")
    cl = put("CLOSING CASH", f"=B{op}+B{ch}", style="grand")
    put(f'=IF(ABS(B{cl}-B{a[0]})<0.01,"✓  Ties to the cash & bank on the balance sheet","✗  Does not tie to the balance sheet cash")', style="note")
    r[0] += 1

    # ---- 5. partners' equity
    put("5.  STATEMENT OF PARTNERS' EQUITY", style="section")
    pc = [f"={L(12 + j)}{P0}" for j in range(6)]
    put("Partner", cols=pc + ["TOTAL"], style="head", fmt="General")
    row_cap = put("Capital put in (funding)", cols=[f"={L(12 + j)}{CAP}" for j in range(6)] + [f"=SUM(B{r[0]}:G{r[0]})"])
    row_sh = put("Share of billed profit", cols=[f"={L(12 + j)}{SHARE}" for j in range(6)] + [f"=SUM(B{r[0]}:G{r[0]})"])
    row_dr = put("Less: distributions received", cols=[f"=-{L(12 + j)}{DRW}" for j in range(6)] + [f"=SUM(B{r[0]}:G{r[0]})"])
    row_cl = put("CLOSING EQUITY — billed", cols=[f"={L(2 + j)}{row_cap}+{L(2 + j)}{row_sh}+{L(2 + j)}{row_dr}" for j in range(7)], style="total")
    put("Share of total equity", cols=[f'=IFERROR({L(2 + j)}{row_cl}/$H${row_cl},0)' for j in range(7)], fmt=PCT)
    put("Billed profit still to be distributed (share − distributions)", cols=[f"={L(2 + j)}{row_sh}+{L(2 + j)}{row_dr}" for j in range(7)])
    kept = put('="Kept in company (reserve "&TEXT(' + RES + ',"0%")&")"', f"={BILLED}-H{row_sh}")
    other = put("Capital / drawings not tagged to a named partner",
                f"=(B{eq[0]}-H{row_cap})-(-B{eq[3]}+H{row_dr})")
    wipr = put("Profit on work in progress — not billed yet, held until invoiced", f"={WIPP}")
    rese = put("Reserves & retained earnings (balance sheet)", f"=B{eq[1]}")
    tot_eq = put("TOTAL EQUITY PER BALANCE SHEET", f"=B{te}", style="grand")
    put(f'=IF(ABS(H{row_cl}+B{kept}+B{other}+B{wipr}+B{rese}-B{tot_eq})<0.01,"✓  Ties to the balance sheet — total partners\' equity "&TEXT(B{tot_eq},"#,##0.00"),'
        f'"✗  Does not tie to the balance sheet — check partner names on capital and distributions")', style="note")
    put("Each partner's profit share follows their ownership % per project (Partner Shares sheet, else their default share on the Partners "
        "sheet), after the company reserve on the Settings sheet (reserve_pct). Capital and distributions are read from the books by partner name.",
        style="note")
    r[0] += 1
    put("Prepared by: ______________________      Reviewed by (partner): ______________________", style="item")
    for col in "BCDEFGH":
        ws.column_dimensions[col].width = 18
    for c in range(11, 20):
        ws.column_dimensions[L(c)].hidden = True
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.print_area = f"A1:H{r[0]}"
    return ws
