"""Import any screen's data from your own spreadsheet, with a downloadable layout the app fully supports.

For every importable sheet (Direct Costs, Operating Expenses, Payroll, Receipts, Payments, Sales Invoices + lines,
BOQs, Price List, Customers & Suppliers, Products, Business Units, Fixed Assets, Stock, Journal Vouchers, Chart of
Accounts, Partners, Tax Codes…):

  template_xlsx / template_csv — the exact layout: bilingual headings, an example row, drop-downs for the allowed
                                 values (accounts, business units, tax codes, choices) and a "How to fill" sheet
                                 explaining every column (required?, format, allowed values)
  analyze                      — reads YOUR file (Excel or CSV, any sheet, heading row anywhere in the top 30
                                 rows), matches its columns to the app's by name in English or Arabic, common
                                 synonyms and spelling closeness, and says what is still missing
  rows                         — turns the file into rows with the column matching the user confirmed
then the normal all-or-nothing import runs (a check run first lists every problem with its row number).
"""
import csv
import datetime as _dt
import difflib
import io
import re

from . import accounting as acc
from .interchange import FIRST_DATA_ROW, HEADER_ROW, SHEET_BY_NAME, SHEETS, _cell, header_map, input_cols

# which screen a sheet belongs to (permission to import = permission to add there)
SHEET_PERM = {
    "Direct Costs": "purchasing.costs", "Operating Expenses": "purchasing.expenses", "Payroll": "payroll", "Employees": "payroll.employees", "Payroll Run": "payroll.runs", "Payroll Settings": "payroll.settings",
    "Receipt Vouchers": "sales.receipts", "Payment Vouchers": "purchasing.payments", "Sales Invoices": "sales.invoices",
    "Invoice Lines": "sales.invoices", "BOQ Lines": "estimating.boqs", "Price List": "estimating.prices",
    "Parties": "sales.parties", "Business Units": "business_units.units", "Fixed Assets": "assets.register",
    "Stock Items": "stock.balances", "Stock Moves": "stock.moves", "Warehouses": "stock.warehouses",
    "Journal Vouchers": "accounting.journal", "Chart of Accounts": "accounting.coa", "Partners": "partners",
    "Partner Shares": "partners", "Partner Transactions": "partners.transactions", "Tax Codes": "tax.codes",
    "Products": "shop.products", "Settings": "settings", "Loans": "accounting.loans", "Loan Transactions": "accounting.loans",
    "Cash Custody": "purchasing.custody", "Custody Expenses": "purchasing.custody", "Rental Equipment": "rentals.fleet",
    "Rental Stock Lots": "rentals.fleet", "Rental Contracts": "rentals.contracts", "Sales Contracts": "shop.orders",
    "Bank Statement Lines": "accounting.bankrec", "Recurring Entries": "accounting.recurring", "Budgets": "accounting.budgets", "Purchase Orders": "purchasing.orders", "Service Plans": "shop.subscriptions", "Service Subscriptions": "shop.subscriptions",
}

# words people use for the same thing (English & Arabic); matched after the exact names
SYNONYMS = {
    "date": ["date", "day", "transaction date", "posting date", "invoice date", "doc date", "التاريخ", "تاريخ", "اليوم"],
    "description": ["description", "details", "item", "item description", "narration", "particulars", "memo", "البيان",
                    "الوصف", "التفاصيل", "البند", "بيان"],
    "memo": ["memo", "notes", "description", "remarks", "البيان", "ملاحظات"],
    "amount": ["amount", "value", "total", "sum", "net", "المبلغ", "القيمة", "الإجمالي", "الاجمالي", "مبلغ"],
    "vendor": ["vendor", "supplier", "supplier name", "vendor name", "paid to", "payee", "subcontractor", "المورد",
               "اسم المورد", "المقاول", "الجهة"],
    "party": ["party", "payee", "name", "paid to", "received from", "customer", "client", "supplier", "الجهة", "العميل",
              "المورد", "الاسم", "المستفيد"],
    "customer": ["customer", "client", "client name", "customer name", "العميل", "اسم العميل"],
    "worker": ["worker", "employee", "name", "staff", "الموظف", "العامل", "الاسم"],
    "bu_code": ["business unit", "project", "project id", "project code", "cost centre", "cost center", "job", "site",
                "المشروع", "كود المشروع", "مركز التكلفة", "الموقع"],
    "account": ["account", "account code", "gl account", "gl", "account no", "الحساب", "رقم الحساب", "كود الحساب"],
    "qty": ["qty", "quantity", "units", "no", "count", "days", "الكمية", "العدد", "كمية"],
    "rate": ["rate", "unit cost", "unit price", "price", "cost", "السعر", "سعر الوحدة", "الفئة", "التكلفة"],
    "price": ["price", "unit price", "selling price", "rate", "السعر", "سعر البيع", "سعر الوحدة"],
    "unit": ["unit", "uom", "unit of measure", "الوحدة", "وحدة"],
    "paid": ["paid", "paid now", "amount paid", "cash paid", "المدفوع", "المسدد", "مدفوع"],
    "method": ["method", "payment method", "paid by", "mode", "طريقة الدفع", "طريقة السداد"],
    "ref": ["ref", "reference", "ref no", "receipt no", "voucher no", "doc no", "cheque no", "المرجع", "رقم المرجع", "رقم الإيصال"],
    "debit": ["debit", "dr", "مدين"], "credit": ["credit", "cr", "دائن"],
    "name": ["name", "full name", "title", "الاسم", "اسم"],
    "code": ["code", "id", "no", "number", "sku", "item code", "الكود", "الرقم", "كود"],
    "category": ["category", "group", "type", "التصنيف", "الفئة", "المجموعة", "النوع"],
    "phone": ["phone", "mobile", "tel", "telephone", "الهاتف", "الموبايل", "رقم الهاتف", "تليفون"],
    "email": ["email", "e-mail", "mail", "البريد", "البريد الإلكتروني"],
    "tax_id": ["tax id", "tax no", "tax number", "tax registration", "trn", "vat no", "الرقم الضريبي", "رقم التسجيل الضريبي"],
    "number": ["number", "invoice no", "invoice number", "no", "رقم الفاتورة", "الرقم"],
    "item_no": ["item no", "item", "no", "code", "رقم البند", "البند", "م"],
    "vat_code": ["vat", "vat code", "tax code", "ضريبة القيمة المضافة", "كود الضريبة"],
    "wht_code": ["wht", "withholding", "withholding tax", "خصم وتحصيل", "ضريبة الخصم"],
    "notes": ["notes", "remarks", "comment", "comments", "ملاحظات"],
    "cost": ["cost", "unit cost", "purchase price", "التكلفة", "سعر الشراء"],
    "type": ["type", "kind", "النوع"], "status": ["status", "state", "الحالة"],
    "partner": ["partner", "owner", "الشريك"], "warehouse": ["warehouse", "store", "location", "المخزن"],
    "item": ["item", "product", "material", "الصنف", "المنتج", "الخامة"],
}
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


def _norm(s):
    s = str(s or "").translate(_AR_DIGITS).strip().lower()
    s = s.split(" / ")[0] if " / " in s and not re.search(r"[؀-ۿ]", s.split(" / ")[0]) else s
    s = re.sub(r"[_\-.:#()\[\]*/\\]+", " ", s)
    s = re.sub(r"[إأآ]", "ا", s).replace("ة", "ه").replace("ى", "ي")
    s = re.sub(r"(^|\s)(?:وال|بال|ال)(?=[\u0600-\u06FF]{2})", r"\1", s)     # الحساب = حساب
    return re.sub(r"\s+", " ", s).strip()


def sheet(name):
    s = SHEET_BY_NAME.get(name)
    if not s:
        raise acc.ValidationError(f"Unknown sheet {name!r}")
    return s


def importable():
    return [s["name"] for s in SHEETS]


def describe(name):
    """The columns of a sheet as the user sees them (for the mapping screen and the 'How to fill' sheet)."""
    s = sheet(name)
    out = []
    for c in input_cols(s):
        fmt = {"date": "date (2026-10-02, 02/10/2026 or an Excel date)", "num": "number", "money": "amount (number)",
               "pct": "percentage (25% or 0.25)", "acct": "account code from the chart of accounts",
               "bu": "business unit code", "tax": "tax code (VAT14…)"}.get(c["kind"], "text")
        out.append({"key": c["key"], "en": c["en"], "ar": c["ar"], "kind": c["kind"], "required": bool(c["required"]),
                    "choices": c.get("choices") or [], "note": c.get("note") or "", "format": fmt,
                    "default_role": c.get("role") or ""})
    return {"name": s["name"], "ar": s.get("ar", ""), "title": s.get("title", ""), "columns": out}


# ------------------------------------------------------------------ templates
def _lists(db):
    return {"acct": [r["code"] for r in db.q("SELECT code FROM accounts WHERE active=1 ORDER BY code")],
            "bu": [r["code"] for r in db.q("SELECT code FROM business_units ORDER BY code")],
            "tax": [r["code"] for r in db.q("SELECT code FROM tax_codes WHERE active=1 ORDER BY code")]}


FORMATS = {"date": ("date (2026-10-02, 02/10/2026 or an Excel date)", "تاريخ (2026-10-02 أو 02/10/2026 أو تاريخ إكسل)"),
           "num": ("number", "رقم"), "money": ("amount (number)", "مبلغ (رقم)"), "pct": ("percentage (25% or 0.25)", "نسبة (25% أو 0.25)"),
           "acct": ("account code from the chart of accounts", "كود حساب من دليل الحسابات"), "bu": ("business unit code", "كود وحدة العمل"),
           "tax": ("tax code (VAT14…)", "كود ضريبة (VAT14…)")}


def _fmt(kind, ar=False):
    return FORMATS.get(kind, ("text", "نص"))[1 if ar else 0]


def template_xlsx(db, name, lang="en", rows=None):
    """One sheet laid out exactly as the importer reads it, plus 'Lists' (drop-down values) and 'How to fill'."""
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter as L
    from openpyxl.worksheet.datavalidation import DataValidation
    from .workbook import EXAMPLES, EXAMPLES_AR
    s, d = sheet(name), describe(name)
    cols = input_cols(s)
    ar = lang == "ar"
    tr = {}
    if ar:              # one language per file: the Arabic template carries no English labels
        from .workbook_ar import translations
        tr = translations()
    T = lambda en, a: a if ar else en  # noqa: E731
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (s.get("ar") if ar and s.get("ar") else s["name"])[:31]
    if ar:
        ws.sheet_view.rightToLeft = True
    navy, grey = PatternFill("solid", fgColor="1F2A44"), PatternFill("solid", fgColor="C0392B")
    ws.cell(1, 1, (s.get("ar") if ar else s["name"]) + " — " + ("قالب الاستيراد" if ar else "import template")).font = Font(bold=True, size=13)
    ws.cell(2, 1, "اكتب البيانات من الصف 5. الأعمدة الحمراء مطلوبة. لا تغيّر صف العناوين (الصف 4). الصف 3 مثال فقط ولا يُستورد."
            if ar else "Enter data from row 5. Red headings are required. Keep the heading row (row 4) as it is. "
            "Row 3 is only an example and is not imported. See 'How to fill' for every column.").font = Font(size=9, color="555555")
    ex = {**EXAMPLES.get(s["name"], {}), **(EXAMPLES_AR.get(s["name"], {}) if ar else {})}
    if ar:
        ex = {k: tr.get(v, v) if isinstance(v, str) else v for k, v in ex.items()}
    lists = _lists(db)
    lst = wb.create_sheet("Lists")
    lcol = 0
    for i, c in enumerate(cols, start=1):
        h = ws.cell(HEADER_ROW, i, c["ar"] if ar else c["en"])
        h.font = Font(bold=True, color="FFFFFF", size=9)
        h.fill = grey if c["required"] else navy
        h.alignment = Alignment(wrap_text=True, vertical="center")
        tip = next(x for x in d["columns"] if x["key"] == c["key"])
        note = (tr.get(tip["note"]) or "") if ar else tip["note"]
        h.comment = Comment((T("Required. ", "مطلوب. ") if c["required"] else "") + _fmt(tip["kind"], ar) +
                            (f"{T('. Allowed: ', '. القيم: ')}{', '.join(map(str, tip['choices']))}" if tip["choices"] else "") +
                            (f". {note}" if note else "") +
                            (T(". Blank = the default account", ". فارغ = الحساب الافتراضي") if tip["default_role"] else ""), "BeyondERP")
        ws.column_dimensions[L(i)].width = max(12, min(40, c["width"] + 2))
        if c["key"] in ex:
            e = ws.cell(3, i, ex[c["key"]])
            e.font = Font(italic=True, size=8, color="999999")
        values = c.get("choices") or lists.get(c["kind"])
        if values:
            lcol += 1
            lst.cell(1, lcol, c["ar"] if ar else c["en"])
            for r, v in enumerate(values, start=2):
                lst.cell(r, lcol, v)
            dv = DataValidation(type="list", formula1=f"=Lists!${L(lcol)}$2:${L(lcol)}${len(values) + 1}", allow_blank=True)
            dv.showErrorMessage = bool(c.get("choices"))      # accounts / units may be new: warn only for fixed choices
            ws.add_data_validation(dv)
            dv.add(f"{L(i)}{FIRST_DATA_ROW}:{L(i)}{FIRST_DATA_ROW + 2000}")
        if c["kind"] == "date":
            for r in range(FIRST_DATA_ROW, FIRST_DATA_ROW + 300):
                ws.cell(r, i).number_format = "yyyy-mm-dd"
    ws.cell(3, 1).value = ws.cell(3, 1).value if ws.cell(3, 1).value not in (None, "") else ("مثال" if ar else "e.g.")
    for r, row in enumerate(rows or [], start=FIRST_DATA_ROW):       # demo rows, ready to import
        for i, c in enumerate(cols, start=1):
            v = row.get(c["key"])
            if v not in (None, ""):
                if c["kind"] == "date":
                    try:
                        v = _dt.date.fromisoformat(str(v)[:10])
                    except ValueError:
                        pass
                ws.cell(r, i, v)
    ws.row_dimensions[HEADER_ROW].height = 32
    lst.sheet_state = "hidden"
    how = wb.create_sheet("How to fill" if not ar else "طريقة التعبئة")
    if ar:
        how.sheet_view.rightToLeft = True
    head = ["العمود", "مطلوب", "الصيغة", "القيم المسموحة", "ملاحظات"] if ar else ["Column", "Required", "Format", "Allowed values", "Notes"]
    for i, t in enumerate(head, start=1):
        c = how.cell(1, i, t)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = navy
    for r, x in enumerate(d["columns"], start=2):
        note = ((tr.get(x["note"]) or "") if ar else x["note"]) + (T(" Blank = the default account.", " فارغ = الحساب الافتراضي.") if x["default_role"] else "")
        for i, v in enumerate([x["ar"] if ar else x["en"], T("Yes", "نعم") if x["required"] else "", _fmt(x["kind"], ar),
                               ", ".join(map(str, x["choices"])), note.strip()], start=1):
            how.cell(r, i, v).alignment = Alignment(wrap_text=True, vertical="top")
    for i, w in enumerate((28, 9, 34, 34, 50), start=1):
        how.column_dimensions[L(i)].width = w
    how.cell(len(d["columns"]) + 3, 1, T("Your own file works too: import it from the same screen and match its columns to these. "
             "Headings in English or Arabic and common synonyms are matched automatically.",
             "يمكنك أيضاً استيراد ملفك الخاص من نفس الشاشة ومطابقة أعمدته بهذه. العناوين بالعربية أو الإنجليزية تُطابق تلقائياً.")).font = Font(italic=True, size=9)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def template_csv(name, rows=None):
    s = sheet(name)
    cols = input_cols(s)
    from .workbook import EXAMPLES
    ex = EXAMPLES.get(s["name"], {})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"{c['en']} / {c['ar']}" for c in cols])
    if rows:
        for row in rows:
            w.writerow([row.get(c["key"], "") for c in cols])
    elif ex:
        w.writerow([ex.get(c["key"], "") for c in cols])
    return ("﻿" + buf.getvalue()).encode("utf-8")


# ------------------------------------------------------------------- reading
def _tables(raw, filename=""):
    """[(sheet title, rows)] from an Excel or CSV file."""
    name = (filename or "").lower()
    if raw[:2] == b"PK" or name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        out = []
        for ws in wb.worksheets:
            if ws.sheet_state != "visible" and len(wb.worksheets) > 1:
                continue
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            out.append((ws.title, rows))
        return out
    for enc in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4000]
    delim = "\t" if "\t" in sample else (";" if sample.count(";") > sample.count(",") else ",")
    return [("CSV", [r for r in csv.reader(io.StringIO(text), delimiter=delim)])]


def _guess(cols, headers):
    """{file column index: key} — exact names first, then synonyms, then closest spelling."""
    exact = header_map({"cols": cols}, headers)
    out, used = {}, set()
    for i, k in exact.items():
        if k and k not in used and any(c["key"] == k for c in cols):
            out[i], _ = k, used.add(k)
    names = {}
    for c in cols:
        cands = {_norm(c["en"]), _norm(c["ar"]), _norm(c["key"].replace("_", " "))}
        cands |= {_norm(x) for x in SYNONYMS.get(c["key"], [])}
        base = c["key"].split("_")[0]
        if base != c["key"]:
            cands |= {_norm(x) for x in SYNONYMS.get(base, [])}
        names[c["key"]] = {x for x in cands if x}
    norm = {i: _norm(h) for i, h in enumerate(headers) if str(h or "").strip()}
    for i, h in norm.items():      # synonyms: the required columns win ties
        if i in out:
            continue
        for c in sorted(cols, key=lambda c: not c["required"]):
            if c["key"] not in used and h in names[c["key"]]:
                out[i] = c["key"]
                used.add(c["key"])
                break
    for i, h in norm.items():      # closest spelling
        if i in out or len(h) < 3:
            continue
        best, score = None, 0.0
        for c in cols:
            if c["key"] in used:
                continue
            for n in names[c["key"]]:
                r = difflib.SequenceMatcher(None, h, n).ratio()
                if r > score:
                    best, score = c["key"], r
        if best and score >= 0.84:
            out[i] = best
            used.add(best)
    return out


def analyze(db, name, raw, filename="", table=None, header_row=None):
    """Find the heading row and match the file's columns to the sheet's. Nothing is imported."""
    s = sheet(name)
    cols = input_cols(s)
    if not raw:
        raise acc.ValidationError("Choose a file")
    tables = _tables(raw, filename)
    if not tables:
        raise acc.ValidationError("The file has no sheets")
    best = None
    for title, rows in tables:
        if table and title != table:
            continue
        for hi, r in enumerate(rows[:30]):
            if header_row is not None and hi != int(header_row):
                continue
            filled = sum(1 for x in r if str(x or "").strip())
            if filled < 1:
                continue
            g = _guess(cols, r)
            score = len(g) * 10 + sum(1 for k in g.values() if any(c["key"] == k and c["required"] for c in cols)) * 5 \
                + (50 if title == s["name"] else 0)
            if best is None or score > best[0]:
                best = (score, title, hi, r, g, rows)
    if best is None:
        raise acc.ValidationError("No heading row was found")
    _, title, hi, headers, guess, rows = best
    body = [r for r in rows[hi + 1:] if any(str(x or "").strip() for x in r)]
    head = [str(h if h is not None else "").strip() or f"Column {i + 1}" for i, h in enumerate(headers)]
    mapping = {str(i): k for i, k in guess.items()}
    missing = [c["key"] for c in cols if c["required"] and c["key"] not in guess.values()]
    return {"sheet": s["name"], "table": title, "tables": [t for t, _ in tables], "header_row": hi, "headers": head,
            "mapping": mapping, "missing": missing, "rows": len(body),
            "sample": [[_show(x) for x in r[:len(head)]] for r in body[:5]],
            "columns": describe(name)["columns"], "exact": len(guess) == len(cols) and not missing}


def _show(v):
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()[:10]
    return "" if v is None else str(v)[:60]


def read(db, name, raw, filename="", mapping=None, table=None, header_row=None):
    """The file's rows as import rows, with the confirmed column matching ({file column index: key})."""
    s = sheet(name)
    a = analyze(db, name, raw, filename, table, header_row)
    m = {int(k): v for k, v in (mapping if mapping is not None else a["mapping"]).items() if v}
    missing = [c["en"] for c in input_cols(s) if c["required"] and c["key"] not in m.values()]
    if missing:
        raise acc.ValidationError("Match a column of your file to: " + ", ".join(missing))
    keys = {c["key"]: c for c in s["cols"]}
    rows = next(r for t, r in _tables(raw, filename) if t == a["table"])
    out, nums = [], []
    for n, raw_row in enumerate(rows[a["header_row"] + 1:], start=a["header_row"] + 2):
        row = {}
        for i, k in m.items():
            if k in keys and i < len(raw_row):
                v = raw_row[i]
                if isinstance(v, str):
                    v = v.translate(_AR_DIGITS) if re.fullmatch(r"[\s٠-٩0-9.,٫٬()+-]+", v) else v
                    if keys[k]["kind"] in ("num", "money", "pct") and re.fullmatch(r"\s*\(?[\d,]+(\.\d+)?\)?\s*", v):
                        v = ("-" if "(" in v else "") + v.strip(" ()").replace(",", "")
                row[k] = _cell(v, keys[k])
        if any(v not in ("", None) for v in row.values()):
            out.append(row)
            nums.append(n)
    if not out:
        raise acc.ValidationError("No rows to import under the heading row")
    return out, nums
