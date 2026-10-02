"""Import layouts for the modules built on their own screens: loans, cash custody, equipment rental, product sales,
bank statements and recurring entries.

Each row creates a NEW record through the module itself (so its money entries, schedules and checks are exactly what
the screen would make). The full-ERP export leaves these sheets empty on purpose: existing records already live in
the journal and in the JSON backup, so exporting them here would book their money twice on re-import.
Grouped sheets (a contract with several lines, a statement with many lines, an expense report) use a key column:
rows sharing the key form one record.
"""
from . import accounting as acc
from .accounting import ValidationError, text

MONEY_NOTE = "Rows here create NEW records with their money entries; the full export leaves this sheet empty"


def specs(C):
    """Sheet definitions (C is interchange.C). No posting legs: the workbook does not compute them."""
    return [
        {"name": "Loans", "ar": "القروض", "module": "accounting", "setup": True,
         "title": "Loans the company took or gave. Booking: money = the drawdown is booked now; opening = already in the "
                  "books, only its remaining balance is tracked. " + MONEY_NOTE,
         "cols": [C("code", "Loan Code", "كود القرض", width=10, note="blank = automatic; needed to add repayments in Loan Transactions"),
                  C("direction", "Borrowed / Lent", "مقترض / ممنوح", width=10, required=True, choices=["borrowed", "lent"]),
                  C("party", "Lender / Borrower", "المقرض / المقترض", width=22, required=True),
                  C("purpose", "Purpose", "الغرض", width=20), C("start_date", "Start Date", "تاريخ البداية", kind="date", required=True),
                  C("principal", "Loan Amount", "مبلغ القرض", kind="money", required=True),
                  C("booking", "Booking", "التسجيل", width=9, choices=["money", "opening"], note="money (default) or opening"),
                  C("opening_balance", "Balance Still Owed (opening)", "الرصيد المتبقي", kind="money"),
                  C("interest_type", "Interest", "نوع الفائدة", width=10, choices=["none", "declining", "flat", "fixed"]),
                  C("rate", "Interest % / Year", "الفائدة % سنوياً", kind="num", width=8, note="12 = 12 %"),
                  C("fixed_interest", "Fixed Interest Amount", "فائدة ثابتة", kind="money"),
                  C("term_months", "Term (months)", "المدة (شهور)", kind="num", width=8),
                  C("frequency", "Every (months)", "كل (شهور)", kind="num", width=8, note="1, 2, 3, 4, 6 or 12"),
                  C("schedule_type", "Schedule", "الجدول", width=12, choices=["equal", "equal_principal", "bullet", "none"]),
                  C("first_due", "First Installment", "أول قسط", kind="date"),
                  C("method", "Payment Method", "طريقة الدفع", choices=acc.PAYMENT_METHODS),
                  C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"), C("notes", "Notes", "ملاحظات", width=20)]},
        {"name": "Loan Transactions", "ar": "حركات القروض", "module": "accounting", "setup": True,
         "title": "Repayments, interest, fees and further drawdowns on loans (Loans sheet or already in the app). " + MONEY_NOTE,
         "cols": [C("loan", "Loan Code", "كود القرض", width=10, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
                  C("action", "Action", "الحركة", width=10, required=True, choices=["pay", "interest", "fee", "draw", "writeoff"],
                    note="pay = installment (principal + interest); interest = accrue interest; draw = more money; writeoff"),
                  C("principal", "Principal", "أصل الدين", kind="money"), C("interest", "Interest", "الفائدة", kind="money"),
                  C("amount", "Amount (draw / fee / interest)", "المبلغ", kind="money"),
                  C("method", "Payment Method", "طريقة الدفع", choices=acc.PAYMENT_METHODS), C("memo", "Description", "البيان", width=24)]},
        {"name": "Cash Custody", "ar": "العهد النقدية", "module": "purchasing", "setup": True,
         "title": "Cash given to employees (عهدة). Booking: money = paid out now; opening = the employee already holds it. " + MONEY_NOTE,
         "cols": [C("code", "Custody Code", "كود العهدة", width=10, note="blank = automatic; needed to add expense reports"),
                  C("holder", "Employee", "الموظف", width=20, required=True),
                  C("kind", "Type", "النوع", width=10, choices=["temporary", "permanent"]),
                  C("purpose", "Purpose", "الغرض", width=22), C("issued_on", "Date Given", "تاريخ الصرف", kind="date", required=True),
                  C("amount", "Amount", "المبلغ", kind="money", required=True), C("due_date", "Settle By", "التسوية قبل", kind="date"),
                  C("float_amount", "Limit (permanent)", "الحد (مستديمة)", kind="money"),
                  C("booking", "Booking", "التسجيل", width=9, choices=["money", "opening"]),
                  C("method", "Payment Method", "طريقة الدفع", choices=acc.PAYMENT_METHODS),
                  C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"), C("notes", "Notes", "ملاحظات", width=20)]},
        {"name": "Custody Expenses", "ar": "تسويات العهد", "module": "purchasing", "setup": True,
         "title": "Expense reports that settle a custody. Rows with the same custody code and report no. form one report. " + MONEY_NOTE,
         "cols": [C("custody", "Custody Code", "كود العهدة", width=10, required=True), C("report", "Report No.", "رقم التقرير", width=8),
                  C("date", "Date", "التاريخ", kind="date", required=True), C("description", "Description", "البيان", width=26, required=True),
                  C("amount", "Amount (incl. VAT)", "المبلغ (شامل الضريبة)", kind="money", required=True),
                  C("account", "Expense Account", "حساب المصروف", kind="acct", role="other_overhead"),
                  C("vat_code", "VAT Code", "كود الضريبة", kind="tax", width=9), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"),
                  C("supplier", "Supplier", "المورد", width=16), C("receipt", "Receipt No.", "رقم الإيصال", width=10)]},
        {"name": "Rental Equipment", "ar": "معدات التأجير", "module": "rentals", "setup": True,
         "title": "Equipment you rent out (scaffolding pieces, elevators, cradles) with optional opening stock",
         "cols": [C("code", "Item Code", "كود الصنف", width=10, note="blank = automatic"),
                  C("name", "Item", "الصنف", width=26, required=True), C("name_ar", "Arabic Name", "الاسم بالعربية", width=22),
                  C("category", "Category", "الفئة", width=14, choices=["scaffolding", "elevator", "cradle_manual", "cradle_motorized", "other"]),
                  C("unit", "Unit", "الوحدة", width=8), C("monthly_rate", "Rent / Month", "الإيجار الشهري", kind="money"),
                  C("loss_price", "Loss / Damage Price", "سعر الفقد", kind="money"), C("std_cost", "Reference Cost", "التكلفة المرجعية", kind="money"),
                  C("weight", "Weight (kg)", "الوزن", kind="num", width=8),
                  C("life_months", "Useful Life (months)", "العمر (شهور)", kind="num", width=8),
                  C("residual_pct", "Residual %", "التخريدية %", kind="num", width=8, note="10 = 10 %"),
                  C("opening_qty", "Opening Qty Owned", "الكمية المملوكة", kind="num", width=9),
                  C("opening_unit_cost", "Opening Unit Cost", "تكلفة الوحدة", kind="money"),
                  C("opening_date", "Owned Since", "تاريخ الامتلاك", kind="date")]},
        {"name": "Rental Stock Lots", "ar": "دفعات معدات التأجير", "module": "rentals", "setup": True,
         "title": "More pieces bought (booked to the rental fleet) or already owned (opening). " + MONEY_NOTE,
         "cols": [C("item", "Item Code", "كود الصنف", width=10, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
                  C("qty", "Qty", "الكمية", kind="num", required=True), C("unit_cost", "Unit Cost", "تكلفة الوحدة", kind="money", required=True),
                  C("source", "Source", "المصدر", width=9, choices=["purchase", "opening"]),
                  C("supplier", "Supplier", "المورد", width=16), C("paid", "Paid Now", "المدفوع", kind="money", note="blank = paid in full"),
                  C("opening_accum", "Depreciation Already Taken", "إهلاك سابق", kind="money")]},
        {"name": "Rental Contracts", "ar": "عقود التأجير", "module": "rentals", "setup": True,
         "title": "Rental offers / contracts. Rows with the same Contract Ref form one contract (one row per line). "
                  "They come in as price offers; PO and signatures move them on. Deliveries are done in the app (signed handover).",
         "cols": [C("ref", "Contract Ref", "مرجع العقد", width=10, required=True), C("client", "Client", "العميل", width=20, required=True),
                  C("site", "Site", "الموقع", width=18), C("site_location", "Address", "العنوان", width=18),
                  C("quote_date", "Offer Date", "تاريخ العرض", kind="date"), C("start_date", "Start", "البداية", kind="date"),
                  C("duration_months", "Duration (months)", "المدة (شهور)", kind="num", width=8),
                  C("min_days", "Minimum Days", "الحد الأدنى (أيام)", kind="num", width=8), C("deposit", "Security Deposit", "التأمين", kind="money"),
                  C("vat_code", "VAT Code", "كود الضريبة", kind="tax", width=9),
                  C("line_kind", "Line Type", "نوع السطر", width=8, choices=["item", "kit", "charge"]),
                  C("item", "Item / Kit Code", "كود الصنف / الطقم", width=10), C("description", "Description", "البيان", width=22),
                  C("qty", "Qty", "الكمية", kind="num"), C("rate", "Rate (per month / charge)", "السعر", kind="money"),
                  C("charge_type", "Charge Type", "نوع المبلغ", width=10, choices=["install", "dismantle", "transport", "materials", "other"]),
                  C("po_number", "Client PO No.", "رقم أمر الشراء", width=10, note="filled = the PO was received"),
                  C("signed_client", "Signed by (client)", "وقّع عن العميل", width=14, note="filled with the PO = contract signed"),
                  C("signed_us", "Signed by (us)", "وقّع عن الشركة", width=14)]},
        {"name": "Sales Contracts", "ar": "عقود البيع", "module": "shop", "setup": True,
         "title": "Sales of products / units with their payment plan. Rows with the same Sale No. form one sale (one row per line). " + MONEY_NOTE,
         "cols": [C("code", "Sale No.", "رقم البيع", width=10, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
                  C("customer", "Customer", "العميل", width=20, required=True), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu", required=True),
                  C("product", "Product Code", "كود المنتج", width=10, note="or leave blank and write the description"),
                  C("description", "Description", "البيان", width=22), C("qty", "Qty", "الكمية", kind="num"),
                  C("price", "Unit Price", "سعر الوحدة", kind="money", note="blank = the product's price"),
                  C("discount", "Discount (line)", "الخصم", kind="money"), C("vat_code", "VAT Code", "كود الضريبة", kind="tax", width=9),
                  C("plan", "Payment", "السداد", width=12, choices=["full", "deferred", "installments"]),
                  C("down_pct", "Down Payment %", "المقدم %", kind="num", width=8), C("n", "Installments", "عدد الأقساط", kind="num", width=8),
                  C("frequency", "Every", "كل", width=10, choices=["monthly", "quarterly", "semiannual", "annual"]),
                  C("first_due", "First Installment / Pay On", "أول قسط / تاريخ السداد", kind="date"),
                  C("interest", "Interest % / Year", "الفائدة % سنوياً", kind="num", width=8),
                  C("revenue_on", "Revenue On", "الإيراد عند", width=9, choices=["sale", "delivery"]),
                  C("confirm", "Confirmed", "مؤكد", width=7, choices=["Yes", "No"]),
                  C("collected", "Collected So Far", "المحصل حتى الآن", kind="money", note="booked as one receipt when confirmed"),
                  C("method", "Payment Method", "طريقة الدفع", choices=acc.PAYMENT_METHODS)]},
        {"name": "Bank Statement Lines", "ar": "حركات كشف البنك", "module": "accounting", "setup": True,
         "title": "The bank's statement lines. Rows with the same account and statement name form one statement; it is matched "
                  "to your vouchers automatically after import",
         "cols": [C("account", "Bank Account", "حساب البنك", kind="acct", required=True, role="bank"),
                  C("statement", "Statement", "الكشف", width=14, note="e.g. CIB 2026-09; blank = one statement per account"),
                  C("date", "Date", "التاريخ", kind="date", required=True), C("description", "Description", "البيان", width=28),
                  C("ref", "Reference", "المرجع", width=10), C("amount", "Amount (+ in / − out)", "المبلغ (+ وارد / − منصرف)", kind="money", required=True),
                  C("balance", "Balance", "الرصيد", kind="money"),
                  C("opening", "Opening Balance (first row)", "الرصيد الافتتاحي", kind="money"),
                  C("closing", "Closing Balance (first row)", "الرصيد الختامي", kind="money")]},
        {"name": "Recurring Entries", "ar": "القيود المتكررة", "module": "accounting", "setup": True,
         "title": "Rent, salaries, subscriptions… posted on a schedule. Text may use {month}",
         "cols": [C("name", "Name", "الاسم", width=20, required=True),
                  C("doc_type", "Posts a", "يرحّل", width=12, required=True,
                    choices=["expense", "direct_cost", "payroll", "payment", "receipt", "journal", "depreciation"]),
                  C("start", "First Date", "أول تاريخ", kind="date", required=True), C("every", "Every", "كل", kind="num", width=6),
                  C("unit", "Unit", "الوحدة", width=8, choices=["day", "week", "month", "year"]),
                  C("month_end", "Month End", "آخر الشهر", width=7, choices=["Yes", "No"]),
                  C("end_date", "Until", "حتى", kind="date"), C("max_count", "Times", "عدد المرات", kind="num", width=6),
                  C("mode", "Mode", "الطريقة", width=8, choices=["auto", "review"]),
                  C("amount", "Amount", "المبلغ", kind="money"), C("description", "Description", "البيان", width=24),
                  C("account", "Account (expense / debit)", "الحساب (المصروف / المدين)", kind="acct"),
                  C("credit_account", "Credit Account (journal / receipt)", "الحساب الدائن", kind="acct"),
                  C("party", "Supplier / Party", "المورد / الطرف", width=16), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu")]},
    ]


def _yes(v):
    return str(v or "").strip().lower() in ("yes", "y", "true", "1", "نعم")


def _groups(rows, key):
    out, order = {}, []
    for i, r in enumerate(rows):
        k = key(r)
        if k not in out:
            order.append(k)
        out.setdefault(k, []).append((i, r))
    return [(k, out[k]) for k in order]


# ------------------------------------------------------------------ row importers
def loan(db, r, user):
    from . import loans
    d = dict(r)
    d["booking"] = r.get("booking") or "money"
    d["interest_type"] = r.get("interest_type") or ("declining" if r.get("rate") not in (None, "", 0) else "none")
    d["schedule_type"] = r.get("schedule_type") or "equal"
    old = db.one("SELECT id FROM loans WHERE code=?", (text(r.get("code")),)) if r.get("code") else None
    if old:
        raise ValidationError(f"Loan {r['code']} already exists")
    loans.save(db, d, user=user)


def loan_tx(db, r, user):
    from . import loans
    ln = loans.get(db, r.get("loan"))
    action = r.get("action")
    d = dict(r)
    if action in ("draw", "fee", "interest", "writeoff") and r.get("amount") not in (None, ""):
        d["amount"] = r["amount"]
    if action == "interest" and d.get("amount") in (None, ""):
        d["amount"] = r.get("interest")
    loans.post(db, ln["id"], action, d, user)


def custody_row(db, r, user):
    from . import custody
    d = dict(r)
    d["booking"] = r.get("booking") or "money"
    d["kind"] = r.get("kind") or "temporary"
    if d["booking"] == "opening":
        d["opening_balance"] = r.get("amount")
    if r.get("code") and db.one("SELECT 1 FROM custodies WHERE code=?", (text(r["code"]),)):
        raise ValidationError(f"Custody {r['code']} already exists")
    custody.save(db, d, user=user)


def custody_reports(db, items, user):
    from . import custody
    c = custody.get(db, items[0][1].get("custody"))
    lines = []
    for _, r in items:
        lines.append({"date": r.get("date"), "description": r.get("description"), "amount": r.get("amount"),
                      "account": r.get("account"), "vat_code": r.get("vat_code"), "supplier": r.get("supplier"),
                      "receipt": r.get("receipt"), "bu_id": acc.bu_id_or_none(db, r.get("bu_code")) or ""})
    custody.post(db, c["id"], "settle", {"date": max(str(r.get("date")) for _, r in items), "lines": lines}, user)


def rent_item(db, r, user):
    from . import rentals
    d = dict(r)
    d["category"] = r.get("category") or "scaffolding"
    old = db.one("SELECT id FROM rent_items WHERE code=?", (text(r.get("code")),)) if r.get("code") else None
    iid = rentals.save_item(db, d, old["id"] if old else None)
    if r.get("opening_qty") not in (None, "", 0, "0"):
        rentals.add_lot(db, iid, {"qty": r["opening_qty"], "unit_cost": r.get("opening_unit_cost") or r.get("std_cost") or 0,
                                  "date": r.get("opening_date") or acc.today(), "source": "opening"}, user)


def rent_lot(db, r, user):
    from . import rentals
    it = rentals.get_item(db, r.get("item"))
    rentals.add_lot(db, it["id"], {**r, "source": r.get("source") or "purchase"}, user)


def rent_contract(db, items, user):
    from . import rentals
    h = items[0][1]
    lines = []
    for _, r in items:
        kind = r.get("line_kind") or ("charge" if r.get("charge_type") else "item")
        ln = {"kind": kind, "qty": r.get("qty"), "rate": r.get("rate"), "description": r.get("description")}
        if kind == "item":
            ln["item"] = r.get("item")
        elif kind == "kit":
            ln["kit"] = r.get("item")
        else:
            ln["charge_type"] = r.get("charge_type") or "other"
        lines.append(ln)
    rid = rentals.save(db, {**h, "lines": lines}, user=user)
    if h.get("po_number"):
        rentals.action(db, rid, "po", {"po_number": h["po_number"], "start_date": h.get("start_date")}, user)
        if h.get("signed_client"):
            rentals.action(db, rid, "sign", {"signed_client": h["signed_client"], "signed_us": h.get("signed_us") or "",
                                            "signed_on": h.get("start_date") or h.get("quote_date")}, user)


def sale_contract(db, items, user):
    from . import sales
    h = items[0][1]
    if db.one("SELECT 1 FROM sales_orders WHERE code=?", (text(h["code"]),)):
        raise ValidationError(f"Sale {h['code']} already exists")
    lines = []
    for _, r in items:
        p = sales.get_product(db, r["product"]) if r.get("product") else None
        lines.append({"product_id": p["id"] if p else None, "description": r.get("description"), "qty": r.get("qty") or 1,
                      "price": r.get("price"), "discount": r.get("discount")})
    t = h.get("plan") or "full"
    plan = {"type": t}
    if t == "deferred":
        plan["due"] = h.get("first_due")
    elif t == "installments":
        plan.update(down_pct=h.get("down_pct") or 0, n=h.get("n") or 12, frequency=h.get("frequency") or "monthly",
                    first_due=h.get("first_due") or "", rate=h.get("interest") or 0)
    sid = sales.save(db, {"code": h["code"], "date": h["date"], "customer": h["customer"], "bu_code": h["bu_code"],
                          "vat_code": h.get("vat_code"), "revenue_on": h.get("revenue_on") or None, "lines": lines, "plan": plan})
    if _yes(h.get("confirm")) or h.get("collected") not in (None, "", 0, "0"):
        sales.confirm(db, sid, {"date": h["date"], "collect": h.get("collected") or 0, "method": h.get("method") or "Bank Transfer"}, user)


def bank_statement(db, items, user):
    from . import bankrec
    h = items[0][1]
    lines = [{"date": r["date"], "description": r.get("description"), "ref": r.get("ref"), "amount": r["amount"],
              "balance": r.get("balance")} for _, r in items]
    dates = sorted(x["date"] for x in lines)
    opening = h.get("opening")
    if opening in (None, "") and h.get("balance") not in (None, ""):
        opening = round(float(h["balance"]) - float(h["amount"]), 2)
    sid = bankrec.save(db, {"account": h["account"], "name": h.get("statement") or "", "date_from": dates[0], "date_to": dates[-1],
                            "opening": opening if opening not in (None, "") else 0, "closing": h.get("closing"), "lines": lines})
    bankrec.auto_match(db, sid)


def recurring_row(db, r, user):
    from . import recurring
    t = r.get("doc_type")
    amt, desc, party, bu = r.get("amount") or 0, r.get("description") or r.get("name"), r.get("party") or "", r.get("bu_code") or ""
    payload = {
        "expense": {"description": desc, "amount": amt, "account": r.get("account"), "vendor": party, "bu_code": bu},
        "direct_cost": {"description": desc, "qty": 1, "rate": amt, "account": r.get("account"), "vendor": party, "bu_code": bu},
        "payroll": {"worker": party or desc, "qty": 1, "rate": amt, "bu_code": bu, "period": "{month}"},
        "payment": {"party": party, "amount": amt, "against": r.get("account") or None, "memo": desc},
        "receipt": {"amount": amt, "party": party, "bu_code": bu, "credit_account": r.get("credit_account"), "memo": desc},
        "journal": {"memo": desc, "party": party, "bu_code": bu,
                    "lines": [{"account": r.get("account"), "debit": amt}, {"account": r.get("credit_account"), "credit": amt}]},
        "depreciation": {},
    }.get(t)
    if payload is None:
        raise ValidationError("Unknown voucher type")
    recurring.save(db, {"name": r["name"], "doc_type": t, "start": r["start"], "every": r.get("every") or 1, "unit": r.get("unit") or "month",
                        "month_end": _yes(r.get("month_end")), "end_date": r.get("end_date"), "max_count": r.get("max_count") or 0,
                        "mode": r.get("mode") or "review", "payload": {k: v for k, v in payload.items() if v not in (None, "")}})


ROW_IMPORTERS = [("Loans", loan), ("Loan Transactions", loan_tx), ("Cash Custody", custody_row),
                 ("Rental Equipment", rent_item), ("Rental Stock Lots", rent_lot), ("Recurring Entries", recurring_row)]
GROUP_IMPORTERS = [("Custody Expenses", lambda r: (str(r.get("custody")), str(r.get("report") or r.get("date"))), custody_reports),
                   ("Rental Contracts", lambda r: str(r.get("ref")), rent_contract),
                   ("Sales Contracts", lambda r: str(r.get("code")), sale_contract),
                   ("Bank Statement Lines", lambda r: (str(r.get("account")), str(r.get("statement") or "")), bank_statement)]
ORDER = ["Loans", "Loan Transactions", "Cash Custody", "Custody Expenses", "Rental Equipment", "Rental Stock Lots",
         "Rental Contracts", "Sales Contracts", "Recurring Entries", "Bank Statement Lines"]
