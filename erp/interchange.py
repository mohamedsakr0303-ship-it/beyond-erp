"""The interchange format shared by Excel, JSON and CSV import/export.

One spec (SHEETS) defines every data sheet: its columns, their Arabic labels,
which calculated helper columns it has, and which accounts each row debits and
credits ("legs"). The Excel workbook uses the legs to build its Trial Balance
with SUMIFS formulas, so the workbook computes the same statements as the app.

Rows are plain dicts keyed by column key; Excel/CSV headers read like
"Date / التاريخ" and are matched on either the key or the English part.
"""
import csv
import datetime as _dt
import io
import json

from . import accounting as acc
from . import custody, loans, rentals
from .db import SUBTYPES, to_major, to_minor

FORMAT = "beyond-erp"
FORMAT_VERSION = 1
FIRST_DATA_ROW = 5
HEADER_ROW = 4


def C(key, en, ar, kind="text", width=14, required=False, role=None, choices=None, calc=None, note=""):
    """Column spec. kind: text|date|num|money|pct|acct|bu|calc. role: default account role for blanks."""
    return {"key": key, "en": en, "ar": ar, "kind": kind, "width": width, "required": required, "role": role,
            "choices": choices, "calc": calc, "note": note}


def ROLE(role):
    """Excel formula returning the account code that holds a system role."""
    return (f"INDEX('Chart of Accounts'!$A${FIRST_DATA_ROW}:$A$3000,"
            f"MATCH(\"{role}\",'Chart of Accounts'!$F${FIRST_DATA_ROW}:$F$3000,0))")


def WH_ACCT(cell):
    return (f"IFERROR(INDEX(Warehouses!$C${FIRST_DATA_ROW}:$C$1000,MATCH({cell},Warehouses!$A${FIRST_DATA_ROW}:"
            f"$A$1000,0))&\"\",\"\")")


def TAXRATE(cell):
    return f"IFERROR(VLOOKUP({cell},'Tax Codes'!$A${FIRST_DATA_ROW}:$E$300,5,FALSE),0)"


def ROLECOL(key, role):
    """Helper column holding the account code of a system role (e.g. the input-VAT account)."""
    return C(key, f"{role} account", "الحساب", kind="calc", width=9, calc="=" + ROLE(role))


def eff(key, role):
    """Helper column: the entered account, or the role's default account when blank."""
    return C(f"{key}__eff", f"{key} (effective)", "الحساب الفعلي", kind="calc", width=10,
             calc=f'=IF({{{key}}}="",{ROLE(role)},{{{key}}}&"")')


YES_NO = ["Posted", "Draft"]
METHODS = acc.PAYMENT_METHODS
PAYROLL = list(acc.PAYROLL_TYPES)

SHEETS = [
    {"name": "Settings", "ar": "الإعدادات", "module": "settings",
     "title": "Company settings & report period",
     "cols": [C("key", "Key", "المفتاح", width=26, required=True), C("value", "Value", "القيمة", width=40)]},
    {"name": "Chart of Accounts", "ar": "دليل الحسابات", "module": "accounting",
     "title": "Chart of accounts — edit codes and names freely; Role is the system tag (do not duplicate)",
     "cols": [C("code", "Code", "رقم الحساب", width=10, required=True),
              C("name", "Account Name", "اسم الحساب", width=36, required=True),
              C("type", "Type", "النوع", width=11, required=True, choices=list(SUBTYPES)),
              C("subtype", "Statement Line", "بند القوائم المالية", width=24, required=True,
                choices=sorted({s for v in SUBTYPES.values() for s in v})),
              C("normal", "Normal Balance", "الطبيعة", kind="calc", width=10,
                calc='=IF(OR({type}="Asset",{type}="COGS",{type}="Expense"),IF(OR({subtype}="accumulated_depreciation",'
                     '{subtype}="accumulated_amortization",{subtype}="allowance_doubtful"),"Credit","Debit"),IF(OR({subtype}="drawings",'
                     '{subtype}="sales_discount"),"Debit","Credit"))'),
              C("role", "System Role", "الدور في النظام", width=18),
              C("name_ar", "Arabic Name", "الاسم بالعربية", width=34),
              C("active", "Active", "نشط", width=7, choices=["Yes", "No"])]},
    {"name": "Tax Codes", "ar": "أكواد الضرائب", "module": "tax",
     "title": "VAT and withholding-tax codes with their e-invoice (ETA) tax type / sub-type. Rates are editable",
     "cols": [C("code", "Code", "الكود", width=12, required=True), C("name", "Name", "الاسم", width=30, required=True),
              C("name_ar", "Arabic Name", "الاسم بالعربية", width=30),
              C("kind", "Kind", "النوع", width=7, required=True, choices=["vat", "wht"]),
              C("rate", "Rate", "النسبة", kind="pct", width=8), C("eta_type", "ETA Tax Type", "نوع الضريبة (المنظومة)", width=10),
              C("eta_subtype", "ETA Sub-type", "النوع الفرعي", width=10), C("active", "Active", "نشط", width=7, choices=["Yes", "No"])]},
    {"name": "Business Units", "ar": "وحدات الأعمال", "module": "business_units",
     "title": "Business units (cost centres / projects). Overhead % = manual share of shared indirect costs",
     "cols": [C("code", "Code", "الكود", width=14, required=True), C("name", "Name / Client", "الاسم / العميل", width=34, required=True),
              C("type", "Type", "النوع", width=12), C("location", "Location", "الموقع", width=16),
              C("area", "Area (m²)", "المساحة", kind="num", width=9), C("start_date", "Start Date", "تاريخ البدء", kind="date"),
              C("status", "Status", "الحالة", width=12, choices=["Tendering", "In Progress", "On Hold", "Completed", "Closed"]),
              C("basis", "Billing Basis", "أساس الفوترة", width=18, choices=acc.BILLING_BASES),
              C("fee_pct", "Fee %", "نسبة الأتعاب", kind="pct", width=8),
              C("contract_value", "Contract Value", "قيمة العقد", kind="money"),
              C("overhead_pct", "Overhead Allocation %", "نسبة تحميل المصروفات المشتركة", kind="pct", width=10),
              C("customer", "Customer", "العميل", width=24), C("notes", "Notes", "ملاحظات", width=24)]},
    {"name": "Parties", "ar": "العملاء والموردون", "module": "sales",
     "title": "Customers & suppliers master — tax registration numbers and addresses are needed for e-invoices",
     "cols": [C("name", "Name", "الاسم", width=28, required=True),
              C("type", "Type", "النوع", width=10, choices=["customer", "supplier", "both", "employee", "other"]),
              C("receiver_type", "Receiver Type", "نوع المستلم", width=8, choices=["B", "P", "F"],
                note="B = business (tax ID), P = person (national ID), F = foreign"),
              C("tax_id", "Tax Registration No.", "رقم التسجيل الضريبي", width=14),
              C("national_id", "National ID", "الرقم القومي", width=16), C("country", "Country", "الدولة", width=7),
              C("governate", "Governorate", "المحافظة", width=12), C("region", "City / Region", "المدينة", width=12),
              C("street", "Street", "الشارع", width=20), C("building", "Building No.", "رقم المبنى", width=8),
              C("postal_code", "Postal Code", "الرمز البريدي", width=8), C("email", "Email", "البريد", width=20),
              C("phone", "Phone", "الهاتف", width=14), C("default_vat", "Default VAT", "ض.ق.م الافتراضية", kind="tax", width=10),
              C("default_wht", "Default WHT", "خصم وتحصيل افتراضي", kind="tax", width=10), C("notes", "Notes", "ملاحظات", width=20),
              C("category", "Category", "التصنيف", width=14,
                note="supplier, subcontractor, driver, transport, technician, consultant, customer, employee, other — or your own"),
              C("contact_person", "Contact Person", "الشخص المسؤول", width=18), C("phone2", "Other Phone", "هاتف آخر", width=14),
              C("whatsapp", "WhatsApp", "واتساب", width=14), C("specialty", "Trade / Specialty", "النشاط / التخصص", width=16),
              C("vehicle", "Vehicle / Plate", "المركبة / اللوحة", width=12),
              C("bank_details", "Bank Details", "بيانات البنك", width=26)]},
    {"name": "Partners", "ar": "الشركاء", "module": "partners", "title": "Partners and their default profit share",
     "cols": [C("name", "Partner", "الشريك", width=30, required=True),
              C("default_share", "Default Share %", "نسبة الملكية الافتراضية", kind="pct", width=12),
              C("active", "Still a Partner", "شريك حالي", width=8, choices=["Yes", "No"]),
              C("joined", "Joined", "تاريخ الانضمام", kind="date"), C("left_on", "Left", "تاريخ الخروج", kind="date"),
              C("capital_interest", "Interest on Capital %", "فائدة رأس المال", kind="pct", width=10),
              C("loan_rate", "Loan Interest %", "فائدة القرض", kind="pct", width=10),
              C("notes", "Notes", "ملاحظات", width=24)]},
    {"name": "Partner Shares", "ar": "حصص الشركاء", "module": "partners",
     "title": "Ownership per business unit (overrides the default share)",
     "cols": [C("partner", "Partner", "الشريك", width=30, required=True), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu", required=True),
              C("share", "Share %", "النسبة", kind="pct", width=10)]},
    {"name": "Fixed Assets", "ar": "الأصول الثابتة", "module": "assets",
     "title": "Fixed asset register (its purchase, depreciation and disposal are journal entries with reference FA:<code>)",
     "cols": [C("code", "Asset Code", "كود الأصل", width=10, required=True), C("name", "Asset", "الأصل", width=28, required=True),
              C("account", "Asset Account", "حساب الأصل", kind="acct", required=True),
              C("accum_account", "Accumulated Depreciation Account", "حساب مجمع الإهلاك", kind="acct", role="accum_depreciation"),
              C("expense_account", "Depreciation Expense Account", "حساب مصروف الإهلاك", kind="acct", role="depreciation"),
              C("acquired", "Date Acquired", "تاريخ الشراء", kind="date", required=True),
              C("cost", "Cost", "التكلفة", kind="money", required=True), C("salvage", "Residual Value", "القيمة التخريدية", kind="money"),
              C("life_months", "Useful Life (months)", "العمر الإنتاجي (شهور)", kind="num", width=9, note="0 = not depreciated (land)"),
              C("depr_start", "Depreciate From (YYYY-MM)", "بداية الإهلاك", width=10),
              C("opening_depr", "Depreciation Before Registering", "إهلاك سابق", kind="money"),
              C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"), C("location", "Location", "الموقع", width=14),
              C("serial", "Serial / Plate No.", "الرقم التسلسلي / اللوحة", width=14), C("notes", "Notes", "ملاحظات", width=20)]},
    {"name": "Warehouses", "ar": "المخازن", "module": "stock", "title": "Warehouses / stores and their inventory account",
     "cols": [C("name", "Warehouse", "المخزن", width=22, required=True), C("location", "Location", "الموقع", width=20),
              C("account", "Inventory Account", "حساب المخزون", kind="acct", width=12)]},
    {"name": "Stock Items", "ar": "الأصناف", "module": "stock", "title": "Item master",
     "cols": [C("name", "Item", "الصنف", width=30, required=True), C("unit", "Unit", "الوحدة", width=9),
              C("sku", "SKU", "كود الصنف", width=10), C("reorder_level", "Reorder Level", "حد إعادة الطلب", kind="num", width=10)]},
    {"name": "Price List", "ar": "قائمة الأسعار", "module": "estimating",
     "title": "Unit-rate price list (historical cost × (1 + markup) = client price)",
     "cols": [C("category", "Category", "الباب", width=24, required=True), C("item_no", "Item No.", "رقم البند", width=8),
              C("sub", "Sub-category", "البند الفرعي", width=16),
              C("description", "Description", "الوصف", width=60, required=True), C("unit", "Unit", "الوحدة", width=8),
              C("cost", "Unit Cost", "تكلفة الوحدة", kind="money"), C("markup", "Markup %", "هامش الربح", kind="pct", width=8),
              C("price", "Client Price", "سعر البيع", kind="money", note="Blank = cost × (1 + markup)"),
              C("notes", "Notes", "ملاحظات", width=20),
              C("eta_code", "ETA Item Code", "كود الصنف بالمنظومة", width=18, note="EGS code registered on the e-invoice portal")]},
    {"name": "BOQ Lines", "ar": "بنود المقايسات", "module": "estimating",
     "title": "Bills of quantities — one row per line; rows with the same BOQ Title + Business Unit form one BOQ",
     "cols": [C("title", "BOQ Title", "عنوان المقايسة", width=20, required=True), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"),
              C("rev", "Revision", "الإصدار", width=8), C("date", "Date", "التاريخ", kind="date"),
              C("client", "Client", "العميل", width=18), C("location", "Location", "الموقع", width=14),
              C("category", "Category", "الباب", width=22), C("sub", "Sub-category", "البند الفرعي", width=14),
              C("item_no", "Item No.", "رقم البند", width=8), C("description", "Description", "الوصف", width=50, required=True),
              C("unit", "Unit", "الوحدة", width=8), C("qty", "Qty", "الكمية", kind="num", width=9),
              C("price", "Unit Price", "سعر الوحدة", kind="money"), C("cost", "Unit Cost", "تكلفة الوحدة", kind="money"),
              C("amount", "Amount", "الإجمالي", kind="calc", calc="=ROUND(N({qty})*N({price}),2)")]},
    {"name": "Direct Costs", "ar": "التكاليف المباشرة", "module": "purchasing", "kind": "Direct Cost",
     "title": "Direct costs (cost of revenue) per business unit — materials, subcontractors, site work",
     "cols": [C("date", "Date", "التاريخ", kind="date", required=True), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu", required=True),
              C("account", "Cost Account", "حساب التكلفة", kind="acct", role="materials"),
              C("description", "Description", "البيان", width=34, required=True), C("vendor", "Supplier", "المورد", width=18),
              C("deal_ref", "Deal Ref.", "مرجع الاتفاق", width=10), C("deal_status", "Deal Status", "حالة الاتفاق", width=10, choices=["Open", "Settled"]),
              C("qty", "Qty", "الكمية", kind="num", width=8), C("unit", "Unit", "الوحدة", width=8),
              C("rate", "Unit Cost", "تكلفة الوحدة", kind="money"),
              C("savings", "Negotiation Discount", "خصم التفاوض", kind="money"),
              C("paid", "Paid Now", "المدفوع", kind="money"), C("method", "Payment Method", "طريقة الدفع", choices=METHODS),
              C("paid_from", "Paid From", "دفع من حساب", kind="acct", role="bank"),
              C("payable_account", "Payable Account", "حساب الدائنين", kind="acct", role="ap"),
              C("vat_code", "VAT Code", "كود ض.ق.م", kind="tax", width=10), C("wht_code", "WHT Code", "كود خصم وتحصيل", kind="tax", width=10),
              C("total", "Total Cost", "صافي التكلفة", kind="calc", calc="=ROUND(N({qty})*N({rate})-N({savings}),2)"),
              C("vat_amt", "Input VAT", "ض.ق.م المدخلات", kind="calc", calc="=ROUND({total}*" + TAXRATE("{vat_code}") + ",2)"),
              C("wht_amt", "WHT Withheld", "الخصم والتحصيل", kind="calc", calc="=ROUND({total}*" + TAXRATE("{wht_code}") + ",2)"),
              C("unpaid", "Unpaid", "المتبقي", kind="calc", calc="={total}+{vat_amt}-{wht_amt}-N({paid})"),
              eff("account", "materials"), eff("paid_from", "bank"), eff("payable_account", "ap"),
              ROLECOL("vat_acct", "vat_input"), ROLECOL("wht_acct", "wht_payable")],
     "legs": [("total", "account__eff", "D"), ("vat_amt", "vat_acct", "D"), ("paid", "paid_from__eff", "C"),
              ("unpaid", "payable_account__eff", "C"), ("wht_amt", "wht_acct", "C")]},
    {"name": "Operating Expenses", "ar": "المصروفات التشغيلية", "module": "purchasing", "kind": "Operating Expense",
     "title": "Operating expenses. Leave Business Unit blank for shared overhead (allocated by Overhead %). "
              "Paid Now blank = paid in full",
     "cols": [C("date", "Date", "التاريخ", kind="date", required=True), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"),
              C("account", "Expense Account", "حساب المصروف", kind="acct", role="other_overhead"),
              C("description", "Description", "البيان", width=34, required=True), C("vendor", "Supplier", "المورد", width=18),
              C("amount", "Amount", "المبلغ", kind="money", required=True), C("paid", "Paid Now", "المدفوع", kind="money"),
              C("method", "Payment Method", "طريقة الدفع", choices=METHODS),
              C("paid_from", "Paid From", "دفع من حساب", kind="acct", role="bank"),
              C("payable_account", "Payable Account", "حساب الدائنين", kind="acct", role="ap"),
              C("vat_code", "VAT Code", "كود ض.ق.م", kind="tax", width=10), C("wht_code", "WHT Code", "كود خصم وتحصيل", kind="tax", width=10),
              C("vat_amt", "Input VAT", "ض.ق.م المدخلات", kind="calc", calc="=ROUND(N({amount})*" + TAXRATE("{vat_code}") + ",2)"),
              C("wht_amt", "WHT Withheld", "الخصم والتحصيل", kind="calc", calc="=ROUND(N({amount})*" + TAXRATE("{wht_code}") + ",2)"),
              C("paid_eff", "Paid (effective)", "المدفوع الفعلي", kind="calc",
                calc='=IF({paid}="",N({amount})+{vat_amt}-{wht_amt},N({paid}))'),
              C("unpaid", "Unpaid", "المتبقي", kind="calc", calc="=N({amount})+{vat_amt}-{wht_amt}-{paid_eff}"),
              eff("account", "other_overhead"), eff("paid_from", "bank"), eff("payable_account", "ap"),
              ROLECOL("vat_acct", "vat_input"), ROLECOL("wht_acct", "wht_payable")],
     "legs": [("amount", "account__eff", "D"), ("vat_amt", "vat_acct", "D"), ("paid_eff", "paid_from__eff", "C"),
              ("unpaid", "payable_account__eff", "C"), ("wht_amt", "wht_acct", "C")]},
    {"name": "Payroll", "ar": "الرواتب والأجور", "module": "payroll", "kind": "Payroll Accrual",
     "title": "Payroll accruals — wages earned (and paid now) per worker",
     "cols": [C("date", "Date", "التاريخ", kind="date", required=True), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"),
              C("worker", "Employee / Worker", "الموظف / العامل", width=22, required=True), C("role", "Position", "الوظيفة", width=14),
              C("cost_type", "Cost Classification", "تصنيف التكلفة", width=30, choices=PAYROLL),
              C("account", "Expense Account", "حساب المصروف", kind="acct"),
              C("period", "Period", "الفترة", width=9), C("basis", "Rate Basis", "أساس الأجر", width=10,
                                                            choices=["Daily", "Monthly", "Lump Sum", "Hourly"]),
              C("qty", "Days / Months", "العدد", kind="num", width=8), C("rate", "Rate", "الأجر", kind="money"),
              C("paid", "Paid Now", "المدفوع", kind="money"), C("method", "Payment Method", "طريقة الدفع", choices=METHODS),
              C("paid_from", "Paid From", "دفع من حساب", kind="acct", role="bank"),
              C("payable_account", "Payroll Payable Account", "حساب الأجور المستحقة", kind="acct", role="accrued_payroll"),
              C("total", "Gross Pay", "إجمالي الأجر", kind="calc", calc="=ROUND(N({qty})*N({rate}),2)"),
              C("unpaid", "Unpaid", "المتبقي", kind="calc", calc="={total}-N({paid})"),
              C("account__eff", "account (effective)", "الحساب الفعلي", kind="calc", width=10,
                calc='=IF({account}<>"",{account}&"",IF(LEFT({cost_type},6)="Indire",' + ROLE("project_overhead") +
                     ',IF(LEFT({cost_type},6)="Admini",' + ROLE("admin_salaries") + ',' + ROLE("direct_labour") + ')))'),
              eff("paid_from", "bank"), eff("payable_account", "accrued_payroll")],
     "legs": [("total", "account__eff", "D"), ("paid", "paid_from__eff", "C"), ("unpaid", "payable_account__eff", "C")]},
    {"name": "Receipt Vouchers", "ar": "سندات القبض", "module": "sales", "kind": "Receipt Voucher",
     "title": "Money received from clients. Credit Account: revenue (cash basis), receivables (against an invoice) "
              "or client advances",
     "cols": [C("date", "Date", "التاريخ", kind="date", required=True), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu", required=True),
              C("ref", "Receipt No.", "رقم السند", width=10), C("memo", "Description", "البيان", width=28),
              C("party", "Received From", "المستلم منه", width=20),
              C("amount", "Amount", "المبلغ", kind="money", required=True), C("method", "Payment Method", "طريقة الدفع", choices=METHODS),
              C("received_into", "Received Into", "إيداع في حساب", kind="acct", role="bank"),
              C("credit_account", "Credit Account", "الحساب الدائن", kind="acct", role="revenue"),
              C("withheld", "Tax Withheld by Client", "خصم وتحصيل بواسطة العميل", kind="money"),
              C("vat_code", "VAT Code (cash revenue)", "كود ض.ق.م (إيراد نقدي)", kind="tax", width=10,
                note="Only when the receipt is booked straight to revenue: the amount is VAT-inclusive"),
              C("gross", "Gross", "الإجمالي", kind="calc", calc="=N({amount})+N({withheld})"),
              C("vat_amt", "Output VAT", "ض.ق.م المخرجات", kind="calc",
                calc='=IF({vat_code}="",0,{gross}-ROUND({gross}/(1+' + TAXRATE("{vat_code}") + '),2))'),
              C("net_credit", "Net Credit", "صافي الدائن", kind="calc", calc="={gross}-{vat_amt}"),
              eff("received_into", "bank"), eff("credit_account", "revenue"),
              ROLECOL("whtr_acct", "wht_receivable"), ROLECOL("vato_acct", "vat_output")],
     "legs": [("amount", "received_into__eff", "D"), ("withheld", "whtr_acct", "D"),
              ("net_credit", "credit_account__eff", "C"), ("vat_amt", "vato_acct", "C")]},
    {"name": "Sales Invoices", "ar": "فواتير المبيعات", "module": "sales", "kind": "Sales Invoice",
     "title": "Sales invoices / progress billings. Amount is the sum of the invoice's lines",
     "cols": [C("number", "Invoice No.", "رقم الفاتورة", width=14, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
              C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu", required=True),
              C("boq_title", "Based on BOQ", "بناءً على مقايسة", width=16),
              C("status", "Status", "الحالة", width=8, choices=YES_NO),
              C("ar_account", "Receivable Account", "حساب المدينين", kind="acct", role="ar"),
              C("revenue_account", "Revenue Account", "حساب الإيراد", kind="acct", role="revenue"),
              C("customer", "Customer", "العميل", width=22), C("vat_code", "VAT Code", "كود ض.ق.م", kind="tax", width=10), C("wht_code", "WHT Code", "كود خصم وتحصيل", kind="tax", width=10),
              C("amount", "Net Amount", "صافي المبلغ", kind="calc",
                calc="=SUMIFS('Invoice Lines'!$I:$I,'Invoice Lines'!$A:$A,{number})"),
              C("posted_amount", "Posted Amount", "المبلغ المرحّل", kind="calc", calc='=IF({status}="Draft",0,{amount})'),
              C("vat_amt", "Output VAT", "ض.ق.م المخرجات", kind="calc", calc="=ROUND({posted_amount}*" + TAXRATE("{vat_code}") + ",2)"),
              C("wht_amt", "WHT by Client", "خصم وتحصيل العميل", kind="calc", calc="=ROUND({posted_amount}*" + TAXRATE("{wht_code}") + ",2)"),
              C("ar_amt", "Receivable", "المستحق من العميل", kind="calc", calc="={posted_amount}+{vat_amt}-{wht_amt}"),
              eff("ar_account", "ar"), eff("revenue_account", "revenue"),
              ROLECOL("whtr_acct", "wht_receivable"), ROLECOL("vato_acct", "vat_output")],
     "legs": [("ar_amt", "ar_account__eff", "D"), ("wht_amt", "whtr_acct", "D"),
              ("posted_amount", "revenue_account__eff", "C"), ("vat_amt", "vato_acct", "C")]},
    {"name": "Invoice Lines", "ar": "بنود الفواتير", "module": "sales", "title": "Lines of each sales invoice",
     "cols": [C("number", "Invoice No.", "رقم الفاتورة", width=14, required=True), C("category", "Category", "الباب", width=20),
              C("sub", "Sub-category", "البند الفرعي", width=12), C("item_no", "Item No.", "رقم البند", width=8),
              C("description", "Description", "الوصف", width=50, required=True), C("unit", "Unit", "الوحدة", width=8),
              C("qty", "Qty Executed", "الكمية المنفذة", kind="num", width=9), C("price", "Unit Price", "سعر الوحدة", kind="money"),
              C("amount", "Amount", "الإجمالي", kind="calc", calc="=ROUND(N({qty})*N({price}),2)")]},
    {"name": "Payment Vouchers", "ar": "سندات الصرف", "module": "purchasing", "kind": "Payment Voucher",
     "title": "Payments settling what we owe suppliers, subcontractors and workers",
     "cols": [C("date", "Date", "التاريخ", kind="date", required=True), C("party", "Payee", "المستفيد", width=22, required=True),
              C("against", "Pay Against Account", "الحساب المدين", kind="acct", role="ap"),
              C("amount", "Amount", "المبلغ", kind="money", required=True), C("method", "Payment Method", "طريقة الدفع", choices=METHODS),
              C("paid_from", "Paid From", "دفع من حساب", kind="acct", role="bank"), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"),
              C("ref", "Voucher No.", "رقم السند", width=10), C("memo", "Description", "البيان", width=26),
              eff("against", "ap"), eff("paid_from", "bank")],
     "legs": [("amount", "against__eff", "D"), ("amount", "paid_from__eff", "C")]},
    {"name": "Partner Transactions", "ar": "حركات الشركاء", "module": "partners",
     "title": "Capital contributions and owner drawings / dividends",
     "cols": [C("date", "Date", "التاريخ", kind="date", required=True), C("partner", "Partner", "الشريك", width=26, required=True),
              C("type", "Type", "النوع", width=20, required=True, choices=["Capital Contribution", "Owner Drawing"]),
              C("amount", "Amount", "المبلغ", kind="money", required=True), C("method", "Payment Method", "طريقة الدفع", choices=METHODS),
              C("cash_account", "Cash / Bank Account", "حساب النقدية", kind="acct", role="bank"),
              C("equity_account", "Equity Account", "حساب حقوق الملكية", kind="acct"),
              C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"), C("memo", "Description", "البيان", width=22),
              eff("cash_account", "bank"),
              C("equity__eff", "equity (effective)", "الحساب الفعلي", kind="calc", width=10,
                calc='=IF({equity_account}<>"",{equity_account}&"",IF({type}="Capital Contribution",' + ROLE("capital") +
                     "," + ROLE("drawings") + "))"),
              C("dr_acct", "Debit Account", "الحساب المدين", kind="calc", width=10,
                calc='=IF({type}="Capital Contribution",{cash_account__eff},{equity__eff})'),
              C("cr_acct", "Credit Account", "الحساب الدائن", kind="calc", width=10,
                calc='=IF({type}="Capital Contribution",{equity__eff},{cash_account__eff})')],
     "legs": [("amount", "dr_acct", "D"), ("amount", "cr_acct", "C")]},
    {"name": "Stock Moves", "ar": "حركات المخزون", "module": "stock",
     "title": "Goods receipts, material issues to business units, transfers and count adjustments "
              "(Adjustment Qty = counted − system, may be negative)",
     "cols": [C("date", "Date", "التاريخ", kind="date", required=True),
              C("type", "Type", "نوع الحركة", width=11, required=True, choices=acc.STOCK_MOVE_TYPES),
              C("warehouse", "Warehouse", "المخزن", width=16, required=True), C("to_warehouse", "To Warehouse", "إلى مخزن", width=14),
              C("item", "Item", "الصنف", width=24, required=True), C("unit", "Unit", "الوحدة", width=8),
              C("qty", "Qty", "الكمية", kind="num", width=8, required=True), C("rate", "Unit Cost", "تكلفة الوحدة", kind="money"),
              C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"), C("vendor", "Supplier", "المورد", width=16),
              C("paid", "Paid Now", "المدفوع", kind="money"), C("method", "Payment Method", "طريقة الدفع", choices=METHODS),
              C("paid_from", "Paid From", "دفع من حساب", kind="acct", role="bank"),
              C("payable_account", "Payable Account", "حساب الدائنين", kind="acct", role="ap"),
              C("account", "Cost Account (issues)", "حساب التكلفة", kind="acct", role="materials"),
              C("note", "Note", "ملاحظة", width=16), C("vat_code", "VAT Code", "كود ض.ق.م", kind="tax", width=10), C("wht_code", "WHT Code", "كود خصم وتحصيل", kind="tax", width=10),
              C("value", "Value", "القيمة", kind="calc", calc="=ROUND(ABS(N({qty}))*N({rate}),2)"),
              C("vat_amt", "Input VAT", "ض.ق.م المدخلات", kind="calc",
                calc='=IF({type}="Receipt",ROUND({value}*' + TAXRATE("{vat_code}") + ',2),0)'),
              C("wht_amt", "WHT Withheld", "الخصم والتحصيل", kind="calc",
                calc='=IF({type}="Receipt",ROUND({value}*' + TAXRATE("{wht_code}") + ',2),0)'),
              ROLECOL("vat_acct", "vat_input"), ROLECOL("wht_acct", "wht_payable"),
              eff("paid_from", "bank"), eff("payable_account", "ap"), eff("account", "materials"),
              C("wh_acct", "Warehouse Account", "حساب المخزن", kind="calc", width=10,
                calc='=IF(' + WH_ACCT("{warehouse}") + '="",' + ROLE("inventory") + ',' + WH_ACCT("{warehouse}") + ')'),
              C("to_acct", "To-Warehouse Account", "حساب المخزن المستلم", kind="calc", width=10,
                calc='=IF(' + WH_ACCT("{to_warehouse}") + '="",' + ROLE("inventory") + ',' + WH_ACCT("{to_warehouse}") + ')'),
              C("paid_eff", "Paid (effective)", "المدفوع الفعلي", kind="calc", calc='=IF({type}="Receipt",N({paid}),0)'),
              C("dr_acct", "Debit Account", "الحساب المدين", kind="calc", width=10,
                calc='=IF({type}="Receipt",{wh_acct},IF({type}="Issue",{account__eff},IF({type}="Transfer",{to_acct},'
                     'IF(N({qty})>=0,{wh_acct},' + ROLE("stock_adjust") + '))))'),
              C("cr_acct", "Credit Account", "الحساب الدائن", kind="calc", width=10,
                calc='=IF({type}="Receipt",{payable_account__eff},IF({type}="Adjustment",IF(N({qty})>=0,' +
                     ROLE("stock_adjust") + ',{wh_acct}),{wh_acct}))'),
              C("cr_amt", "Credit Amount", "المبلغ الدائن", kind="calc", calc="={value}+{vat_amt}-{wht_amt}-{paid_eff}")],
     "legs": [("value", "dr_acct", "D"), ("vat_amt", "vat_acct", "D"), ("cr_amt", "cr_acct", "C"),
              ("paid_eff", "paid_from__eff", "C"), ("wht_amt", "wht_acct", "C")]},
    {"name": "Journal Vouchers", "ar": "قيود اليومية", "module": "accounting", "kind": "Journal Voucher",
     "title": "Journal vouchers — opening balances, depreciation, amortization, tax, loans, accruals. "
              "Rows sharing a Voucher No. form one balanced entry",
     "cols": [C("voucher", "Voucher No.", "رقم القيد", width=10, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
              C("kind", "Voucher Type", "نوع القيد", width=16), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"),
              C("party", "Party", "الطرف", width=16), C("ref", "Reference", "المرجع", width=10),
              C("memo", "Description", "البيان", width=30), C("account", "Account", "الحساب", kind="acct", required=True),
              C("debit", "Debit", "مدين", kind="money"), C("credit", "Credit", "دائن", kind="money"),
              C("line_party", "Line Party", "طرف السطر", width=14,
                note="Only when a line belongs to someone other than the voucher's party (e.g. each partner in a share transfer)")],
     "legs": [("debit", "account", "D"), ("credit", "account", "C")]},
]
SHEET_BY_NAME = {s["name"]: s for s in SHEETS}
POSTING_SHEETS = [s for s in SHEETS if s.get("legs")]


def input_cols(sheet):
    return [c for c in sheet["cols"] if c["kind"] != "calc"]


# =================================================================== EXPORT
def _d(v):
    return v or ""


def _num(v):
    return round(v, 4) if isinstance(v, float) else v


def export_data(db):
    """Everything in the books as {sheet name: [row dicts]} (input columns only)."""
    out = {s["name"]: [] for s in SHEETS}
    period_from = db.setting("report_from") or f"{_dt.date.today().year}-01-01"
    period_to = db.setting("report_to") or f"{_dt.date.today().year}-12-31"
    out["Settings"] = [{"key": "report_from", "value": period_from}, {"key": "report_to", "value": period_to}]
    for r in db.q("SELECT key, value FROM settings ORDER BY key"):
        if r["key"] not in ("schema_version", "report_from", "report_to", "docs_seeded", "eta_client_secret"):
            out["Settings"].append({"key": r["key"], "value": r["value"]})
    for t in db.q("SELECT * FROM tax_codes ORDER BY kind, code"):
        out["Tax Codes"].append({k: t[k] for k in ("code", "name", "name_ar", "kind", "rate", "eta_type", "eta_subtype")}
                                | {"active": "Yes" if t["active"] else "No"})
    for p in db.q("SELECT * FROM parties ORDER BY name"):
        out["Parties"].append({k: v for k, v in p.items() if k != "id"})
    for a in db.q("SELECT * FROM accounts ORDER BY code"):
        out["Chart of Accounts"].append({"code": a["code"], "name": a["name"], "name_ar": a["name_ar"], "type": a["type"],
                                         "subtype": a["subtype"], "role": a["role"] or "",
                                         "active": "Yes" if a["active"] else "No"})
    bus = {b["id"]: b for b in db.q("SELECT * FROM business_units ORDER BY code")}
    code = lambda bid: bus[bid]["code"] if bid in bus else ""  # noqa: E731
    for b in bus.values():
        out["Business Units"].append({k: b[k] for k in ("code", "name", "type", "location", "area", "start_date",
                                                         "status", "basis", "fee_pct", "overhead_pct", "customer",
                                                         "notes")}
                                     | {"contract_value": to_major(b["contract_value"])})
    for p in db.q("SELECT * FROM partners ORDER BY id"):
        out["Partners"].append({"name": p["name"], "default_share": p["default_share"],
                                "active": "Yes" if p["active"] else "No", "joined": p["joined"] or "",
                                "left_on": p["left_on"] or "", "capital_interest": p["capital_interest"] or "",
                                "loan_rate": p["loan_rate"] or "", "notes": p["notes"] or ""})
    for s in db.q("SELECT p.name, s.bu_id, s.share FROM partner_shares s JOIN partners p ON p.id=s.partner_id "
                  "ORDER BY p.id, s.bu_id"):
        out["Partner Shares"].append({"partner": s["name"], "bu_code": code(s["bu_id"]), "share": s["share"]})
    for a in db.q("SELECT * FROM fixed_assets ORDER BY code"):
        out["Fixed Assets"].append({k: a[k] for k in ("code", "name", "account", "accum_account", "expense_account", "acquired",
                                                      "life_months", "depr_start", "location", "serial", "notes")}
                                   | {"cost": to_major(a["cost"]), "salvage": to_major(a["salvage"]) or "",
                                      "opening_depr": to_major(a["opening_depr"]) or "", "bu_code": code(a["bu_id"])})
    whs = {w["id"]: w for w in db.q("SELECT * FROM warehouses ORDER BY id")}
    for w in whs.values():
        out["Warehouses"].append({"name": w["name"], "location": w["location"], "account": w["account"] or ""})
    items = {i["id"]: i for i in db.q("SELECT * FROM stock_items ORDER BY name")}
    for i in items.values():
        out["Stock Items"].append({"name": i["name"], "unit": i["unit"], "sku": i["sku"], "reorder_level": i["reorder_level"]})
    for r in db.q("SELECT * FROM rates ORDER BY category, id"):
        out["Price List"].append({"category": r["category"], "item_no": r["item_no"], "sub": r["sub"],
                                  "description": r["description"], "unit": r["unit"], "cost": to_major(r["cost"]),
                                  "markup": r["markup"], "price": to_major(r["price"]), "notes": r["notes"],
                                  "eta_code": r["eta_code"]})
    for b in db.q("SELECT * FROM boqs ORDER BY id"):
        for ln in db.q("SELECT * FROM boq_lines WHERE boq_id=? ORDER BY seq, id", (b["id"],)):
            out["BOQ Lines"].append({"title": b["title"], "bu_code": code(b["bu_id"]), "rev": b["rev"], "date": b["date"],
                                     "client": b["client"], "location": b["location"], "category": ln["category"],
                                     "sub": ln["sub"], "item_no": ln["item_no"], "description": ln["description"],
                                     "unit": ln["unit"], "qty": ln["qty"], "price": to_major(ln["price"]),
                                     "cost": to_major(ln["cost"])})
    # sales invoices from extracts
    invoice_entries = set()
    boq_titles = {b["id"]: b["title"] for b in db.q("SELECT id, title FROM boqs")}
    for x in db.q("SELECT * FROM extracts ORDER BY date, id"):
        e = acc.get_entry(db, x["entry_id"]) if x["entry_id"] else None
        if e and e["status"] == "posted" and not e["reversed_by"]:
            invoice_entries.add(e["id"])
            roles_ = {a["code"]: a["role"] for a in db.q("SELECT code, role FROM accounts")}
            dr = next(ln for ln in e["lines"] if ln["debit"] and roles_.get(ln["account"]) != "wht_receivable")
            cr = next(ln for ln in e["lines"] if ln["credit"] and roles_.get(ln["account"]) != "vat_output")
            status, ar_acc, rev_acc = "Posted", dr["account"], cr["account"]
        else:
            status, ar_acc, rev_acc = "Draft", "", ""
        out["Sales Invoices"].append({"number": x["number"], "date": x["date"], "bu_code": code(x["bu_id"]),
                                      "boq_title": boq_titles.get(x["boq_id"], ""), "status": status,
                                      "ar_account": ar_acc, "revenue_account": rev_acc, "customer": x["customer"],
                                      "vat_code": x["vat_code"], "wht_code": x["wht_code"]})
        for ln in db.q("SELECT * FROM extract_lines WHERE extract_id=? ORDER BY seq, id", (x["id"],)):
            out["Invoice Lines"].append({"number": x["number"], "category": ln["category"], "sub": ln["sub"],
                                         "item_no": ln["item_no"], "description": ln["description"], "unit": ln["unit"],
                                         "qty": ln["qty"], "price": to_major(ln["price"])})
    # stock moves (only those still attached to a live entry, or entry-less transfers/counts)
    stock_entries = set()
    moves = db.q("SELECT m.*, i.name AS item, i.unit, w.name AS warehouse FROM stock_moves m "
                 "JOIN stock_items i ON i.id=m.item_id JOIN warehouses w ON w.id=m.warehouse_id ORDER BY m.id")
    skip = set()
    for idx, m in enumerate(moves):
        if m["id"] in skip:
            continue
        e = acc.get_entry(db, m["entry_id"]) if m["entry_id"] else None
        if e and (e["status"] != "posted" or e["reversed_by"]):
            continue
        row = {"date": m["date"], "type": m["kind"], "warehouse": m["warehouse"], "item": m["item"],
               "unit": m["unit"], "qty": _num(m["qty"]), "rate": to_major(m["rate"]),
               "bu_code": code(m["bu_id"]), "note": m["note"]}
        if m["kind"] == "Receipt" and e:
            row["vendor"] = e["party"]
            inv_line = next(ln for ln in e["lines"] if ln["debit"])
            for ln in e["lines"]:
                if ln["credit"]:
                    arow = db.one("SELECT subtype, role FROM accounts WHERE code=?", (ln["account"],))
                    if arow["role"] == "wht_payable":
                        continue
                    if arow["subtype"] == "cash":
                        row["paid"], row["paid_from"] = ln["credit"], ln["account"]
                    else:
                        row["payable_account"] = ln["account"]
            row["vat_code"], row["wht_code"] = e["meta"].get("vat_code", ""), e["meta"].get("wht_code", "")
            row["rate"] = round(inv_line["debit"] / m["qty"], 6) if m["qty"] else row["rate"]
        elif m["kind"] == "Issue" and e:
            row["qty"] = -m["qty"]
            row["account"] = next(ln["account"] for ln in e["lines"] if ln["debit"])
            row["rate"] = round(e["amount"] / row["qty"], 6)
        elif m["kind"] == "Transfer":
            pair = next((n for n in moves[idx + 1: idx + 3] if n["kind"] == "Transfer" and n["qty"] > 0
                         and n["item_id"] == m["item_id"] and n["entry_id"] == m["entry_id"]), None)
            if pair:
                skip.add(pair["id"])
                row["to_warehouse"] = pair["warehouse"]
            row["qty"] = -m["qty"]
        elif m["kind"] == "Adjustment" and e:
            row["rate"] = round(e["amount"] / abs(m["qty"]), 6) if m["qty"] else 0
        if e:
            stock_entries.add(e["id"])
        out["Stock Moves"].append(row)

    # journal-based documents
    accounts = {a["code"]: a for a in db.q("SELECT * FROM accounts")}
    for e in (acc.get_entry(db, r["id"]) for r in
              db.q("SELECT id FROM journal_entries WHERE status='posted' ORDER BY date, id")):
        if e["id"] in invoice_entries or e["id"] in stock_entries:
            continue
        row = _entry_to_row(e, accounts)
        if row:
            sheet, data = row
            out[sheet].append(data)
        else:
            for ln in e["lines"]:
                out["Journal Vouchers"].append({"voucher": f"JV-{e['id']}", "date": e["date"], "kind": e["kind"],
                                                "bu_code": e["bu_code"] or "", "party": e["party"], "ref": e["ref"],
                                                "memo": e["memo"], "account": ln["account"],
                                                "debit": ln["debit"] or "", "credit": ln["credit"] or "",
                                                "line_party": ln.get("party", "")})
    return out


def _entry_to_row(e, accounts):
    """Map a posted entry back onto its document sheet; None -> goes to Journal Vouchers."""
    if e["reversed_by"] or e["reversal_of"]:
        return None
    if any(ln.get("party") and ln["party"] != e["party"] for ln in e["lines"]):
        return None  # lines for several parties (partners' capital moves) only fit the Journal Vouchers sheet
    role = lambda code: accounts[code]["role"]  # noqa: E731
    dr = [ln for ln in e["lines"] if ln["debit"] and role(ln["account"]) not in ("vat_input", "wht_receivable")]
    cr = [ln for ln in e["lines"] if ln["credit"] and role(ln["account"]) not in ("wht_payable", "vat_output")]
    taxes = {"vat_code": e["meta"].get("vat_code", ""), "wht_code": e["meta"].get("wht_code", "")}
    sub = lambda code: accounts[code]["subtype"]  # noqa: E731
    typ = lambda code: accounts[code]["type"]  # noqa: E731
    m, base = e["meta"], {"date": e["date"], "bu_code": e["bu_code"] or ""}

    def split(credits, payable_types=("Liability",)):
        paid = [ln for ln in credits if sub(ln["account"]) == "cash"]
        owed = [ln for ln in credits if sub(ln["account"]) != "cash" and typ(ln["account"]) in payable_types]
        if len(paid) > 1 or len(owed) > 1 or len(paid) + len(owed) != len(credits):
            return None
        return {"paid": paid[0]["credit"] if paid else 0, "paid_from": paid[0]["account"] if paid else "",
                "payable_account": owed[0]["account"] if owed else ""}

    k = e["kind"]
    if k == "Direct Cost" and len(dr) == 1 and typ(dr[0]["account"]) == "COGS":
        s = split(cr)
        if s is None or abs(dr[0]["debit"] - (m.get("quoted", 0) - m.get("savings", 0))) > 0.005:
            return None
        return "Direct Costs", base | s | taxes | {"account": dr[0]["account"], "description": e["memo"], "vendor": e["party"],
                                           "deal_ref": m.get("deal_ref", ""), "deal_status": m.get("deal_status", ""),
                                           "qty": m.get("qty"), "unit": m.get("unit", ""), "rate": m.get("rate"),
                                           "savings": m.get("savings") or "", "method": m.get("method", "")}
    if k == "Operating Expense" and len(dr) == 1 and typ(dr[0]["account"]) == "Expense":
        s = split(cr)
        if s is None:
            return None
        return "Operating Expenses", base | s | taxes | {"account": dr[0]["account"], "description": e["memo"],
                                                 "vendor": e["party"], "amount": dr[0]["debit"],
                                                 "method": m.get("method", "")}
    if k == "Payroll Accrual" and len(dr) == 1:
        s = split(cr)
        if s is None or abs(dr[0]["debit"] - round(m.get("qty", 0) * m.get("rate", 0), 2)) > 0.005:
            return None
        return "Payroll", base | s | {"worker": e["party"], "role": m.get("role", ""), "cost_type": m.get("cost_type", ""),
                                      "account": dr[0]["account"], "period": m.get("period", ""),
                                      "basis": m.get("basis", ""), "qty": m.get("qty"), "rate": m.get("rate"),
                                      "method": m.get("method", "")}
    if k == "Receipt Voucher" and len(dr) == 1 and len(cr) == 1 and sub(dr[0]["account"]) == "cash":
        return "Receipt Vouchers", base | {"ref": e["ref"], "memo": e["memo"], "party": e["party"],
                                           "amount": dr[0]["debit"], "method": m.get("method", ""),
                                           "received_into": dr[0]["account"], "credit_account": cr[0]["account"],
                                           "withheld": m.get("withheld") or "", "vat_code": m.get("vat_code", "")}
    if k == "Payment Voucher" and len(dr) == 1 and len(cr) == 1 and sub(cr[0]["account"]) == "cash":
        return "Payment Vouchers", base | {"party": e["party"], "against": dr[0]["account"], "amount": dr[0]["debit"],
                                           "method": m.get("method", ""), "paid_from": cr[0]["account"],
                                           "ref": e["ref"], "memo": e["memo"]}
    if k in ("Capital Contribution", "Owner Drawing") and len(dr) == 1 and len(cr) == 1 and \
            sub((dr[0] if k == "Capital Contribution" else cr[0])["account"]) == "cash":
        cash, eq = (dr[0], cr[0]) if k == "Capital Contribution" else (cr[0], dr[0])
        return "Partner Transactions", {"date": e["date"], "partner": e["party"], "type": k,
                                        "amount": dr[0]["debit"], "cash_account": cash["account"],
                                        "equity_account": eq["account"], "bu_code": e["bu_code"] or "",
                                        "memo": e["memo"]}
    return None


# =================================================================== IMPORT
class ImportErrors(acc.ValidationError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s) found — nothing was imported")


def _norm_header(h):
    h = ("" if h is None else str(h)).strip()
    return h.split(" / ")[0].strip().lower()


def header_map(sheet, headers):
    """Map incoming header cells to column keys (accepts key, English label, or 'English / Arabic')."""
    lookup = {}
    for c in sheet["cols"]:
        lookup[f"{c['en']} / {c['ar']}".lower()] = c["key"]
        lookup[c["key"].lower()] = c["key"]
        lookup[c["en"].lower()] = c["key"]
        lookup[c["ar"]] = c["key"]
    return {i: lookup.get(str(h or "").strip().lower()) or lookup.get(_norm_header(h)) or lookup.get(str(h or "").strip())
            for i, h in enumerate(headers)}


def _cell(v, col):
    if v is None:
        return ""
    if col["kind"] == "date":
        if isinstance(v, (_dt.datetime, _dt.date)):
            return acc.clean_date(v)
        s = str(v).strip()
        if not s:
            return ""
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return _dt.datetime.strptime(s[:10], fmt).date().isoformat()
            except ValueError:
                pass
        if s.replace(".", "", 1).isdigit():  # Excel serial number
            return (_dt.date(1899, 12, 30) + _dt.timedelta(days=int(float(s)))).isoformat()
        return s
    if isinstance(v, (_dt.datetime, _dt.date)):
        return acc.clean_date(v)
    if isinstance(v, float) and v.is_integer() and col["kind"] in ("text", "acct", "bu"):
        v = int(v)
    if isinstance(v, str):
        v = v.strip()
    return v


def rows_from_table(sheet, headers, raw_rows):
    hmap = header_map(sheet, headers)
    cols = {c["key"]: c for c in sheet["cols"]}
    out = []
    for raw in raw_rows:
        row = {}
        for i, v in enumerate(raw):
            k = hmap.get(i)
            if k and cols[k]["kind"] != "calc":
                row[k] = _cell(v, cols[k])
        if any(v not in ("", None) for v in row.values()):
            out.append(row)
    return out


def read_xlsx(path):
    try:
        import openpyxl
    except ImportError:
        raise acc.ValidationError("Excel import needs openpyxl:  python -m pip install openpyxl") from None
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    data = {}
    for ws in wb.worksheets:
        sheet = SHEET_BY_NAME.get(ws.title)
        if not sheet:
            continue
        rows = list(ws.iter_rows(values_only=True))
        # header row: first row within the top 10 whose cells match at least 2 known columns
        hi = next((i for i, r in enumerate(rows[:10])
                   if sum(1 for k in header_map(sheet, r).values() if k) >= min(2, len(sheet["cols"]))), None)
        if hi is None:
            continue
        data[ws.title] = rows_from_table(sheet, rows[hi], rows[hi + 1:])
    wb.close()
    return data


def read_csv(sheet_name, content: bytes):
    sheet = SHEET_BY_NAME.get(sheet_name)
    if not sheet:
        raise acc.ValidationError(f"Unknown sheet {sheet_name!r}")
    textc = content.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(textc)))
    if not rows:
        return {sheet_name: []}
    return {sheet_name: rows_from_table(sheet, rows[0], rows[1:])}


def read_json(content: bytes):
    try:
        obj = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise acc.ValidationError(f"Not valid JSON: {e}") from None
    sheets = obj.get("sheets") if isinstance(obj, dict) and "sheets" in obj else obj
    if not isinstance(sheets, dict):
        raise acc.ValidationError('JSON must be {"sheets": {"Sheet name": [ {column: value}, ... ]}}')
    data = {}
    for name, rows in sheets.items():
        sheet = SHEET_BY_NAME.get(name)
        if not sheet or not isinstance(rows, list):
            continue
        cols = {c["key"]: c for c in sheet["cols"]}
        clean = []
        for r in rows:
            if isinstance(r, dict):
                hm = header_map(sheet, list(r.keys()))
                clean.append({hm[i]: _cell(v, cols[hm[i]]) for i, v in enumerate(r.values())
                              if hm.get(i) and cols[hm[i]]["kind"] != "calc"})
        data[name] = clean
    if isinstance(obj, dict) and isinstance(obj.get("extras"), dict):
        data["_extras"] = obj["extras"]
    return data


def export_extras(db):
    """What the sheets cannot hold: letterhead images, offer types and offers (JSON backups only)."""
    return {"assets": db.q("SELECT key, mime, w, h, data FROM assets ORDER BY key"),
            "offer_types": db.q("SELECT id, name, content FROM offer_types ORDER BY id"),
            "offers": db.q("SELECT o.*, u.code AS bu_code FROM offers o LEFT JOIN business_units u ON u.id=o.bu_id ORDER BY o.id"),
            "ownership_changes": _ownership_export(db), "loans": loans.export_rows(db),
            "custodies": custody.export_rows(db), "rentals": rentals.export_rows(db)}


def _ownership_export(db):
    """Ownership history with partners by name (ids differ after an import)."""
    names = {str(p["id"]): p["name"] for p in db.q("SELECT id, name FROM partners")}
    out = []
    for ch in db.q("SELECT * FROM ownership_changes ORDER BY id"):
        conv = {}
        for side in ("before", "after"):
            st = json.loads(ch[side])
            conv[side] = {"shares": {names.get(k, k): v for k, v in st["shares"].items()},
                          "active": {names.get(k, k): v for k, v in st.get("active", {}).items()},
                          "dates": {names.get(k, k): v for k, v in st.get("dates", {}).items()},
                          "bu": [[names.get(p, p), b, v] for p, b, v in st.get("bu", [])]}
        out.append({"date": ch["date"], "kind": ch["kind"], "memo": ch["memo"], "created_by": ch["created_by"],
                    "created_at": ch["created_at"], **conv})
    return out


def _imp_extras(db, ex, mode, counts):
    from . import branding
    if mode == "replace":
        for t in ("offers", "offer_types", "loans", "custodies"):
            db.conn.execute(f"DELETE FROM {t}")
    if ex.get("loans"):
        counts["Loans"] = loans.import_rows(db, ex["loans"])
    if ex.get("custodies"):
        counts["Cash custody"] = custody.import_rows(db, ex["custodies"])
    if isinstance(ex.get("rentals"), dict) and (mode == "replace" or not db.one("SELECT 1 FROM rentals")):
        rentals.import_rows(db, ex["rentals"], counts)
    for a in ex.get("assets") or []:
        if isinstance(a, dict) and a.get("key") in branding.ASSET_KEYS and a.get("data"):
            db.conn.execute("INSERT OR REPLACE INTO assets(key,mime,w,h,data) VALUES (?,?,?,?,?)",
                            (a["key"], a.get("mime") or "image/png", int(a.get("w") or 0), int(a.get("h") or 0), str(a["data"])))
    ids = {}
    for t in ex.get("offer_types") or []:
        if isinstance(t, dict) and t.get("name"):
            ids[t.get("id")] = db.conn.execute("INSERT INTO offer_types(name, content) VALUES (?,?)",
                                               (acc.text(t["name"]), str(t.get("content") or "{}"))).lastrowid
    n = 0
    for o in ex.get("offers") or []:
        if isinstance(o, dict) and o.get("number"):
            bu = db.one("SELECT id FROM business_units WHERE code=?", (o.get("bu_code") or "",))
            db.conn.execute("INSERT INTO offers(number,type_id,date,client,project,bu_id,status,content) VALUES (?,?,?,?,?,?,?,?)",
                            (acc.text(o["number"]), ids.get(o.get("type_id")), o.get("date"), acc.text(o.get("client")),
                             acc.text(o.get("project")), bu["id"] if bu else None, o.get("status") or "Draft",
                             str(o.get("content") or "{}")))
            n += 1
    if ex.get("offer_types") or ex.get("offers"):
        counts["Offers"] = n
    if ex.get("ownership_changes"):
        if mode == "replace":
            db.conn.execute("DELETE FROM ownership_changes")
        ids = {p["name"]: str(p["id"]) for p in db.q("SELECT id, name FROM partners")}
        for ch in ex["ownership_changes"]:
            if not isinstance(ch, dict):
                continue
            conv = {}
            for side in ("before", "after"):
                st = ch.get(side) or {}
                conv[side] = json.dumps({"shares": {ids[k]: v for k, v in (st.get("shares") or {}).items() if k in ids},
                                         "active": {ids[k]: v for k, v in (st.get("active") or {}).items() if k in ids},
                                         "dates": {ids[k]: v for k, v in (st.get("dates") or {}).items() if k in ids},
                                         "bu": [[ids[p], b, v] for p, b, v in (st.get("bu") or []) if p in ids]})
            db.conn.execute("INSERT INTO ownership_changes(date,kind,memo,before,after,created_by,created_at) "
                            "VALUES (?,?,?,?,?,?,?)", (acc.clean_date(ch.get("date")), acc.text(ch.get("kind")),
                                                      acc.text(ch.get("memo")), conv["before"], conv["after"],
                                                      acc.text(ch.get("created_by")), acc.text(ch.get("created_at"))))


def to_json(db):
    return json.dumps({"format": FORMAT, "version": FORMAT_VERSION,
                       "exported_at": _dt.datetime.now().isoformat(timespec="seconds"),
                       "sheets": export_data(db), "extras": export_extras(db)}, ensure_ascii=False, indent=1, default=str)


def to_csv(db, sheet_name, template=False):
    sheet = SHEET_BY_NAME[sheet_name]
    cols = input_cols(sheet)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"{c['en']} / {c['ar']}" for c in cols])
    if not template:
        for r in export_data(db)[sheet_name]:
            w.writerow([r.get(c["key"], "") for c in cols])
    return ("﻿" + buf.getvalue()).encode("utf-8")


WIPE_ORDER = ("rent_moves", "rental_invoices", "rental_charges", "rental_handovers", "rental_lines", "rentals", "rent_lots",
              "rent_kit_lines", "rent_kits", "rent_models", "rent_items", "loans", "custodies", "parties", "ownership_changes", "fixed_assets", "stock_moves", "stock_levels", "stock_items", "extract_lines", "extracts", "boq_lines", "boqs",
              "journal_lines", "journal_entries", "partner_shares", "partners", "rates", "business_units")


def import_data(db, data, mode="append", dry_run=False, user=""):
    """Import sheets. All-or-nothing: any row error rolls everything back and is reported."""
    if mode not in ("append", "replace"):
        raise acc.ValidationError("mode must be append or replace")
    errors, counts = [], {}

    def run(name, fn):
        rows = data.get(name) or []
        n = 0
        for i, row in enumerate(rows, start=FIRST_DATA_ROW):
            sheet = SHEET_BY_NAME[name]
            missing = [c["en"] for c in sheet["cols"] if c["required"] and row.get(c["key"]) in ("", None)]
            if missing:
                errors.append({"sheet": name, "row": i, "error": "missing " + ", ".join(missing)})
                continue
            try:
                fn(row)
                n += 1
            except (acc.ValidationError, LookupError) as e:
                errors.append({"sheet": name, "row": i, "error": str(e)})
        if rows:
            counts[name] = n

    class _Rollback(Exception):
        pass

    try:
        with db.tx():
            if mode == "replace":
                for t in WIPE_ORDER:
                    db.conn.execute(f"DELETE FROM {t}")
                db.conn.execute("DELETE FROM warehouses WHERE id NOT IN (SELECT MIN(id) FROM warehouses)")
            # the lock date is applied last, so the entries it protects can be restored first
            lock = [r for r in (data.get("Settings") or []) if str(r.get("key")) in ("lock_date", "default:lock_date")]
            data["Settings"] = [r for r in (data.get("Settings") or []) if r not in lock]
            saved_lock = db.setting("lock_date") or ""
            if mode == "replace":  # a restore; appending into closed periods stays refused
                db.conn.execute("UPDATE settings SET value='' WHERE key='lock_date'")
            run("Settings", lambda r: _imp_setting(db, r))
            run("Chart of Accounts", lambda r: _imp_account(db, r))
            run("Tax Codes", lambda r: _imp_tax_code(db, r))
            run("Business Units", lambda r: _imp_bu(db, r))
            run("Parties", lambda r: _imp_party(db, r))
            run("Partners", lambda r: _imp_partner(db, r))
            run("Partner Shares", lambda r: _imp_share(db, r))
            run("Fixed Assets", lambda r: _imp_asset(db, r))
            run("Warehouses", lambda r: _imp_wh(db, r))
            run("Stock Items", lambda r: _imp_item(db, r))
            run("Price List", lambda r: _imp_rate(db, r))
            _imp_boqs(db, data.get("BOQ Lines") or [], errors, counts)
            run("Direct Costs", lambda r: acc.record_project_cost(db, r, user))
            run("Operating Expenses", lambda r: acc.record_expense(db, r, user))
            run("Payroll", lambda r: acc.record_payroll(db, r, user))
            run("Receipt Vouchers", lambda r: acc.record_collection(db, r, user))
            _imp_invoices(db, data, errors, counts, user)
            run("Partner Transactions", lambda r: acc.record_partner_tx(db, {**r, "type": "Capital" if str(
                r.get("type", "")).startswith("Capital") else "Distribution"}, user))
            data["Stock Moves"] = sorted(data.get("Stock Moves") or [],
                                         key=lambda r: (str(r.get("date") or ""), r.get("type") != "Receipt"))
            run("Stock Moves", lambda r: _imp_stock(db, r, user))
            run("Payment Vouchers", lambda r: acc.record_payment(db, {**r, "allow_overpay": True}, user))
            _imp_journal(db, data.get("Journal Vouchers") or [], errors, counts, user)
            if isinstance(data.get("_extras"), dict):
                _imp_extras(db, data["_extras"], mode, counts)
            db.conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('lock_date', ?)",
                            (acc.text(lock[-1].get("value")) if lock else saved_lock,))
            if errors or dry_run:
                raise _Rollback()
    except _Rollback:
        pass
    if errors:
        raise ImportErrors(errors[:200])
    return {"counts": counts, "dry_run": dry_run, "mode": mode}


def _imp_setting(db, r):
    key = acc.text(r.get("key"))
    if key in ("schema_version", "docs_seeded", "eta_client_secret"):
        return
    if key.startswith("default:"):  # fill only when empty
        key = key[8:]
        if (db.setting(key) or "").strip():
            return
    db.conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, acc.text(r.get("value"), maxlen=20000)))


def _imp_account(db, r):
    code = acc.text(r.get("code"))
    existing = db.one("SELECT * FROM accounts WHERE code=?", (code,))
    role = acc.text(r.get("role")) or None
    by_role = db.one("SELECT * FROM accounts WHERE role=?", (role,)) if role else None
    d = {"code": code, "name": r.get("name"), "name_ar": r.get("name_ar"), "type": r.get("type"), "subtype": r.get("subtype"),
         "active": 0 if str(r.get("active", "Yes")).lower() in ("no", "0", "false") else 1}
    if by_role and by_role["code"] != code:
        acc.save_account(db, d, old_code=by_role["code"])  # renumbered system account
    elif existing:
        acc.save_account(db, d, old_code=code)
    else:
        acc.save_account(db, d)
        if role:
            db.conn.execute("UPDATE accounts SET role=? WHERE code=?", (role, code))


def _imp_tax_code(db, r):
    code = acc.text(r.get("code")).upper()
    old = code if db.one("SELECT 1 FROM tax_codes WHERE code=?", (code,)) else None
    acc.save_tax_code(db, {**r, "code": code, "rate": _pct(r.get("rate"))}, old_code=old)


def _imp_party(db, r):
    p = db.one("SELECT id FROM parties WHERE name=?", (acc.text(r.get("name")),))
    acc.save_party(db, r, p["id"] if p else None)


def _pct(v):
    v = acc.num(v, "percentage")
    return v / 100 if v > 1 else v


def _imp_bu(db, r):
    vals = (acc.text(r.get("name"), "name", required=True), acc.text(r.get("type")), acc.text(r.get("location")),
            acc.num(r.get("area"), "area"), acc.text(r.get("start_date")), acc.text(r.get("status")) or "In Progress",
            r.get("basis") if r.get("basis") in acc.BILLING_BASES else "Cost-Plus %", _pct(r.get("fee_pct")),
            to_minor(acc.num(r.get("contract_value"), "contract value")), _pct(r.get("overhead_pct")),
            acc.text(r.get("notes"), maxlen=4000), acc.text(r.get("customer")))
    code = acc.text(r.get("code"), "code", required=True)
    if db.one("SELECT 1 FROM business_units WHERE code=?", (code,)):
        db.conn.execute("UPDATE business_units SET name=?,type=?,location=?,area=?,start_date=?,status=?,basis=?,"
                        "fee_pct=?,contract_value=?,overhead_pct=?,notes=?,customer=? WHERE code=?", vals + (code,))
    else:
        db.conn.execute("INSERT INTO business_units(name,type,location,area,start_date,status,basis,fee_pct,"
                        "contract_value,overhead_pct,notes,customer,code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        vals + (code,))


def _imp_partner(db, r):
    active = 0 if str(r.get("active") or "").strip().lower() in ("no", "0", "false") else 1
    db.conn.execute("INSERT INTO partners(name,default_share,active,joined,left_on,capital_interest,loan_rate,notes) "
                    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET default_share=excluded.default_share, "
                    "active=excluded.active, joined=excluded.joined, left_on=excluded.left_on, "
                    "capital_interest=excluded.capital_interest, loan_rate=excluded.loan_rate, notes=excluded.notes",
                    (acc.text(r.get("name")), _pct(r.get("default_share")), active,
                     acc.clean_date(r["joined"]) if r.get("joined") else "",
                     acc.clean_date(r["left_on"]) if r.get("left_on") else "", _pct(r.get("capital_interest")),
                     _pct(r.get("loan_rate")), acc.text(r.get("notes"), maxlen=2000)))


def _imp_share(db, r):
    p = db.one("SELECT id FROM partners WHERE name=?", (acc.text(r.get("partner")),))
    if not p:
        raise acc.ValidationError(f"Unknown partner {r.get('partner')}")
    db.conn.execute("INSERT OR REPLACE INTO partner_shares(partner_id,bu_id,share) VALUES (?,?,?)",
                    (p["id"], acc.bu_id_or_none(db, r.get("bu_code"), required=True), _pct(r.get("share"))))


def _imp_asset(db, r):
    from . import assets
    old = db.one("SELECT id FROM fixed_assets WHERE code=?", (acc.text(r.get("code")),))
    assets.save(db, {**r, "pay": "none"}, old["id"] if old else None)


def _imp_wh(db, r):
    account = acc.account_code(db, r["account"], types=("Asset",)) if r.get("account") else None
    db.conn.execute("INSERT INTO warehouses(name,location,account) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
                    "location=excluded.location, account=excluded.account",
                    (acc.text(r.get("name")), acc.text(r.get("location")), account))


def _imp_item(db, r):
    db.conn.execute("INSERT INTO stock_items(name,unit,sku,reorder_level) VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE "
                    "SET unit=excluded.unit, sku=excluded.sku, reorder_level=excluded.reorder_level",
                    (acc.text(r.get("name")), acc.text(r.get("unit")) or "pcs", acc.text(r.get("sku")),
                     acc.num(r.get("reorder_level"), "reorder level")))


def _imp_rate(db, r):
    cost, mk = acc.num(r.get("cost"), "cost"), _pct(r.get("markup"))
    price = acc.num(r.get("price"), "price") or cost * (1 + mk)
    db.conn.execute("INSERT INTO rates(category,item_no,sub,description,unit,cost,markup,price,notes,eta_code) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (acc.text(r.get("category")), acc.text(r.get("item_no")), acc.text(r.get("sub")),
                     acc.text(r.get("description"), maxlen=2000), acc.text(r.get("unit")), to_minor(cost), mk,
                     to_minor(price), acc.text(r.get("notes")), acc.text(r.get("eta_code"))))


def _imp_boqs(db, rows, errors, counts):
    groups = {}
    for i, r in enumerate(rows, start=FIRST_DATA_ROW):
        groups.setdefault((acc.text(r.get("title")), acc.text(r.get("bu_code"))), []).append((i, r))
    n = 0
    for (title, bu), items in groups.items():
        first = items[0][1]
        try:
            acc.save_boq(db, {"title": title, "bu_code": bu, "rev": first.get("rev"), "date": first.get("date"),
                              "client": first.get("client"), "location": first.get("location"),
                              "lines": [r for _, r in items]})
            n += 1
        except acc.ValidationError as e:
            errors.append({"sheet": "BOQ Lines", "row": items[0][0], "error": str(e)})
    if rows:
        counts["BOQ Lines"] = n


def _imp_invoices(db, data, errors, counts, user):
    lines = {}
    for r in data.get("Invoice Lines") or []:
        lines.setdefault(acc.text(r.get("number")), []).append(r)
    n = 0
    for i, r in enumerate(data.get("Sales Invoices") or [], start=FIRST_DATA_ROW):
        try:
            boq = db.one("SELECT id FROM boqs WHERE title=? ORDER BY id DESC", (acc.text(r.get("boq_title")),)) \
                if r.get("boq_title") else None
            number = acc.text(r.get("number"), "invoice number", required=True)
            if not lines.get(number):
                raise acc.ValidationError(f"Invoice {number} has no rows in 'Invoice Lines'")
            xid = acc.save_extract(db, {"number": number, "date": r.get("date"), "bu_code": r.get("bu_code"),
                                        "boq_id": boq["id"] if boq else None, "lines": lines[number],
                                        "customer": r.get("customer"), "vat_code": r.get("vat_code"),
                                        "wht_code": r.get("wht_code")})
            if str(r.get("status") or "Posted").lower() != "draft":
                acc.post_extract(db, xid, user=user, ar_account=r.get("ar_account") or None,
                                 revenue_account=r.get("revenue_account") or None)
            n += 1
        except acc.ValidationError as e:
            errors.append({"sheet": "Sales Invoices", "row": i, "error": str(e)})
    if data.get("Sales Invoices"):
        counts["Sales Invoices"] = n


def _imp_stock(db, r, user):
    typ = r.get("type")
    d = dict(r)
    if typ == "Adjustment":
        wh = acc._warehouse(db, r.get("warehouse"))
        item = acc._item(db, r)
        d["counted"] = acc._level(db, item["id"], wh["id"])["qty"] + acc.num(r.get("qty"), "qty", allow_negative=True)
    acc.stock_move(db, d, user)


def _imp_journal(db, rows, errors, counts, user):
    groups = {}
    for i, r in enumerate(rows, start=FIRST_DATA_ROW):
        groups.setdefault(acc.text(r.get("voucher")), []).append((i, r))
    n = 0
    for v, items in groups.items():
        first = items[0][1]
        try:
            acc.record_journal(db, {"date": first.get("date"), "kind": first.get("kind") or "Journal Voucher",
                                    "bu_code": first.get("bu_code"), "party": first.get("party"),
                                    "ref": first.get("ref") or v, "memo": first.get("memo"),
                                    "lines": [{"account": r.get("account"), "debit": r.get("debit"),
                                               "credit": r.get("credit"), "party": r.get("line_party")}
                                              for _, r in items]}, user)
            n += 1
        except acc.ValidationError as e:
            errors.append({"sheet": "Journal Vouchers", "row": items[0][0], "error": f"voucher {v}: {e}"})
    if rows:
        counts["Journal Vouchers"] = n
