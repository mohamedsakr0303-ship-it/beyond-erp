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
    """Sheet definitions (C is interchange.C), with the formula columns and posting legs that let the Excel workbook
    book the same money as the app when it is used on its own (see _standalone)."""
    return _standalone(_specs(C), C)


def _specs(C):
    return [
        {"name": "Payroll Settings", "ar": "إعدادات الرواتب", "module": "payroll", "setup": True,
         "title": "Payroll rules: social insurance, salary tax bands, the high-income rule (start_band_N_above), Martyrs Fund, overtime "
                  "and the pay items (name | Arabic | earning/deduction | fixed/run | taxable | insurable | on/off). Rates as decimals: 0.11 = 11 %",
         "cols": [C("key", "Setting", "الإعداد", width=26, required=True), C("value", "Value", "القيمة", width=60),
                  C("note", "Note", "ملاحظة", width=30)]},
        {"name": "Employees", "ar": "الموظفون والعمال", "module": "payroll", "setup": True,
         "title": "Employees and workers (the contract). Re-importing a row with the same code updates the employee — nothing is doubled",
         "cols": [C("code", "Employee Code", "كود الموظف", width=10, required=True), C("name", "Name", "الاسم", width=22, required=True),
                  C("name_ar", "Arabic Name", "الاسم بالعربية", width=20), C("national_id", "National ID", "الرقم القومي", width=14),
                  C("job_title", "Job Title", "الوظيفة", width=16), C("department", "Department", "القسم", width=14),
                  C("emp_type", "Paid", "طريقة الأجر", width=9, choices=["monthly", "daily", "hourly", "piece"]),
                  C("cost_type", "Cost Classification", "تصنيف التكلفة", width=30, choices=list(acc.PAYROLL_TYPES)),
                  C("account", "Cost Account (optional)", "حساب التكلفة", kind="acct"), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"),
                  C("hire_date", "Hired On", "تاريخ التعيين", kind="date"), C("end_date", "Left On", "تاريخ الترك", kind="date"),
                  C("basic", "Basic Monthly Salary", "الأجر الأساسي", kind="money"), C("daily_rate", "Daily Wage", "اليومية", kind="money"),
                  C("hourly_rate", "Hourly Rate", "أجر الساعة", kind="money"), C("piece_rate", "Rate per Unit", "سعر الوحدة", kind="money"),
                  C("piece_unit", "Unit", "الوحدة", width=7)]
                 + [C("al_" + c, n, ar, kind="money") for c, n, ar in (("housing", "Housing Allowance", "بدل سكن"), ("transport", "Transport Allowance", "بدل انتقال"),
                                                                        ("meal", "Meal Allowance", "بدل وجبة"), ("phone", "Phone Allowance", "بدل اتصالات"),
                                                                        ("site", "Site Allowance", "بدل موقع"), ("other_allow", "Other Allowance", "بدلات أخرى"))]
                 + [C("insured", "Insured", "مؤمن عليه", width=7, choices=["Yes", "No"]),
                    C("insurable_wage", "Insurable Wage (blank = basic + allowances)", "أجر الاشتراك", kind="money"),
                    C("insurance_no", "Insurance No.", "الرقم التأميني", width=12), C("taxed", "Salary Tax", "ضريبة كسب العمل", width=7, choices=["Yes", "No"]),
                    C("method", "Paid By", "طريقة الصرف", choices=acc.PAYMENT_METHODS), C("bank_account", "Bank / wallet", "الحساب", width=16),
                    C("phone", "Mobile", "الموبايل", width=12), C("username", "App user", "مستخدم التطبيق", width=14,
                                                                   note="The person's sign-in name: their start / end of day fills their attendance"), C("status", "Status", "الحالة", width=7, choices=["active", "left"]),
                    C("notes", "Notes", "ملاحظات", width=18)]},
        {"name": "Payroll Run", "ar": "مسير الرواتب", "module": "payroll", "setup": True,
         "title": "One row per employee per month. Monthly staff: type unpaid absent days; daily / hourly / piece: the days, hours or units. "
                  "Gross, insurance, tax and net are calculated (grey). Rows of one month form that month's payroll. " + MONEY_NOTE,
         "cols": [C("date", "Posting Date", "تاريخ القيد", kind="date", required=True, note="usually the month end"),
                  C("employee", "Employee Code", "كود الموظف", width=10, required=True),
                  C("units", "Days / Hours / Units", "أيام / ساعات / وحدات", kind="num", width=8), C("absent_days", "Absent Days (unpaid)", "أيام الغياب", kind="num", width=8),
                  C("ot_day", "Overtime h — day", "إضافي نهاري (س)", kind="num", width=7), C("ot_night", "Overtime h — night", "إضافي ليلي (س)", kind="num", width=7),
                  C("ot_rest", "Overtime h — rest day", "إضافي راحة (س)", kind="num", width=7),
                  C("bonus", "Bonus", "مكافأة", kind="money"), C("commission", "Commission", "عمولة", kind="money"),
                  C("incentive", "Incentive", "حافز", kind="money"), C("other_earn", "Other Earnings", "مستحقات أخرى", kind="money"),
                  C("penalty", "Penalties", "جزاءات", kind="money"), C("advance", "Advance Recovery", "استرداد سلفة", kind="money"),
                  C("other_ded", "Other Deductions", "خصومات أخرى", kind="money"), C("paid", "Paid Now", "المدفوع الآن", kind="money"),
                  C("method", "Payment Method", "طريقة الدفع", choices=acc.PAYMENT_METHODS)]},
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
         "title": "Cash given to employees (custody). Booking: money = paid out now; opening = the employee already holds it. " + MONEY_NOTE,
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
                  C("signed_us", "Signed by (us)", "وقّع عن الشركة", width=14),
                  C("bu_code", "Business Unit (site)", "وحدة الأعمال (الموقع)", kind="bu", note="links the contract to its extracts and receipts"),
                  C("delivered_on", "Delivered On", "تاريخ التسليم", kind="date", note="filled (with the PO and both signatures) = the signed delivery: the pieces are on site"),
                  C("invoiced_to", "Invoiced Up To", "تمت الفوترة حتى", kind="date", note="rent already billed on an extract up to this date — the app bills from the next day")]},
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
        {"name": "Service Plans", "ar": "خطط الخدمات والاشتراكات", "module": "shop", "setup": True,
         "title": "Maintenance, warranty / insurance, software support and add-on plans you sell by the month / quarter / year",
         "cols": [C("code", "Plan Code", "كود الخطة", width=10, note="blank = automatic"), C("name", "Plan", "الخطة", width=28, required=True),
                  C("name_ar", "Arabic Name", "الاسم بالعربية", width=24),
                  C("kind", "Type", "النوع", width=11, choices=["maintenance", "warranty", "insurance", "software", "support", "addon", "other"]),
                  C("price", "Price per Period", "السعر لكل فترة", kind="money", required=True),
                  C("period_months", "Every (months)", "كل (شهور)", kind="num", width=7, note="1, 3, 6, 12…"),
                  C("visits", "Visits Included / Period", "زيارات مشمولة", kind="num", width=7),
                  C("extra_visit_price", "Extra Visit Price", "سعر الزيارة الإضافية", kind="money"),
                  C("revenue_account", "Revenue Account", "حساب الإيراد", kind="acct", role="service_revenue"),
                  C("vat_code", "VAT Code", "كود الضريبة", width=8), C("description", "What is included", "ما تشمله", width=30)]},
        {"name": "Service Subscriptions", "ar": "اشتراكات العملاء", "module": "shop", "setup": True,
         "title": "Each customer's contract. Billing starts on the start date (bill due periods on the Subscriptions screen). " + MONEY_NOTE,
         "cols": [C("code", "Code", "الكود", width=10, note="blank = automatic; needed to attach add-ons"),
                  C("customer", "Customer", "العميل", width=22, required=True), C("plan", "Plan Code", "كود الخطة", width=10),
                  C("parent", "Add-on to (code)", "إضافة على (كود)", width=10), C("title", "Service", "الخدمة", width=24),
                  C("covers", "Covers (unit / device / app)", "يغطي (وحدة / جهاز / تطبيق)", width=24),
                  C("start", "Start", "البداية", kind="date", required=True), C("price", "Price per Period", "السعر لكل فترة", kind="money"),
                  C("period_months", "Every (months)", "كل (شهور)", kind="num", width=7),
                  C("term_months", "Term (months, 0 = open)", "المدة (شهور)", kind="num", width=7),
                  C("auto_renew", "Renews", "يتجدد", width=7, choices=["Yes", "No"]), C("vat_code", "VAT Code", "كود الضريبة", width=8),
                  C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"), C("notes", "Notes", "ملاحظات", width=20)]},
        {"name": "Purchase Orders", "ar": "أوامر الشراء", "module": "purchasing", "setup": True,
         "title": "One row per order line; rows with the same order code form one order. Approved = Yes approves it "
                  "(nothing is booked until a delivery is received on its screen)",
         "cols": [C("code", "Order Code", "كود الأمر", width=10, required=True, note="e.g. PO-0101"),
                  C("date", "Date", "التاريخ", kind="date", required=True), C("supplier", "Supplier", "المورد", width=22, required=True),
                  C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"), C("warehouse", "Receive Stock Into", "المخزن", width=14),
                  C("expected", "Delivery By", "التسليم قبل", kind="date"), C("vat_code", "VAT Code", "كود الضريبة", width=8),
                  C("kind", "Line Type", "نوع البند", width=9, choices=["cost", "expense", "stock"], note="cost needs a business unit; stock a warehouse"),
                  C("description", "Description / Stock Item", "البيان / الصنف", width=28, required=True),
                  C("account", "Account", "الحساب", kind="acct"), C("qty", "Qty", "الكمية", kind="num", width=8, required=True),
                  C("unit", "Unit", "الوحدة", width=7), C("rate", "Unit Price", "سعر الوحدة", kind="money", required=True),
                  C("terms", "Terms", "الشروط", width=20), C("approved", "Approved", "معتمد", width=8, choices=["Yes", "No"])]},
        {"name": "Budgets", "ar": "الموازنات", "module": "accounting", "setup": True,
         "title": "Monthly budget per income / cost / expense account (blank business unit = company level). Fill the months, or "
                  "only the Year Total to spread it evenly. Re-importing a row replaces that account's budget",
         "cols": [C("year", "Year", "السنة", kind="num", width=7, required=True),
                  C("account", "Account", "الحساب", kind="acct", required=True), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"),
                  C("total", "Year Total (if no months)", "إجمالي السنة (بدون الشهور)", kind="money")] +
                 [C(f"m{i + 1}", m, ar, kind="money", width=11) for i, (m, ar) in enumerate(zip(
                     ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
                     ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]))] +
                 [C("notes", "Notes", "ملاحظات", width=18)]},
        # ---- projects, contracts & the site
        {"name": "Project Phases", "ar": "مراحل المشروعات", "module": "projects", "setup": True,
         "title": "Each project's phases with a weight (its share of the project), planned dates and % complete. Re-importing a phase "
                  "with the same project and name updates it",
         "cols": [C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu", required=True), C("seq", "Order", "الترتيب", kind="num", width=6),
                  C("name", "Phase", "المرحلة", width=26, required=True), C("weight", "Weight", "الوزن", kind="num", width=8),
                  C("start", "Planned Start", "البداية المخططة", kind="date"), C("finish", "Planned Finish", "النهاية المخططة", kind="date"),
                  C("progress", "% Complete", "نسبة الإنجاز", kind="pct", width=9),
                  C("status", "Status", "الحالة", width=10, choices=["planned", "in_progress", "done", "on_hold"]), C("notes", "Notes", "ملاحظات", width=20)]},
        {"name": "Project Tasks", "ar": "مهام المشروعات", "module": "projects", "setup": True,
         "title": "Tasks with who is responsible and when they are due (business unit blank = a company task)",
         "cols": [C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"), C("phase", "Phase", "المرحلة", width=18),
                  C("title", "Task", "المهمة", width=30, required=True), C("assignee", "Assigned To", "المسؤول", width=16),
                  C("due", "Due", "الاستحقاق", kind="date"), C("priority", "Priority", "الأولوية", width=8, choices=["low", "normal", "high"]),
                  C("status", "Status", "الحالة", width=9, choices=["todo", "doing", "blocked", "done"]), C("notes", "Notes", "ملاحظات", width=20),
                  C("created_by", "Created By", "أنشأها", width=12), C("done_at", "Done On", "تاريخ الإنجاز", width=12)]},
        {"name": "Contracts", "ar": "العقود", "module": "contracts", "setup": True,
         "title": "Client contracts and subcontracts. One row per bill-of-quantities line; rows with the same contract code form one "
                  "contract (its details are read from the first row). Percentages as decimals: 0.1 = 10 %",
         "cols": [C("code", "Contract Code", "كود العقد", width=10, required=True, note="e.g. CC-0001 / SC-0001"),
                  C("kind", "Type", "النوع", width=8, required=True, choices=["client", "sub"]),
                  C("party", "Client / Subcontractor", "العميل / مقاول الباطن", width=22, required=True), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"),
                  C("title", "Title", "العنوان", width=24), C("value", "Contract Value (blank = lines)", "قيمة العقد", kind="money"),
                  C("signed_on", "Signed On", "تاريخ التوقيع", kind="date"), C("start", "Start", "البداية", kind="date"), C("finish", "End", "النهاية", kind="date"),
                  C("retention_pct", "Retention %", "نسبة المحتجزات", kind="pct", width=8), C("retention_release", "Retention Due Back", "موعد رد المحتجزات", kind="date"),
                  C("advance", "Advance Agreed", "الدفعة المقدمة", kind="money"), C("advance_recovery_pct", "Advance Recovery %", "نسبة استرداد المقدم", kind="pct", width=8),
                  C("vat_code", "VAT Code", "كود الضريبة", width=8), C("wht_code", "WHT Code", "كود الخصم", width=8),
                  C("payment_terms", "Payment Terms", "شروط الدفع", width=22), C("status", "Status", "الحالة", width=9, choices=["draft", "active", "completed", "terminated"]),
                  C("item_no", "Item No.", "رقم البند", width=7), C("description", "Line Description", "بيان البند", width=26),
                  C("unit", "Unit", "الوحدة", width=7), C("qty", "Qty", "الكمية", kind="num", width=8), C("rate", "Rate", "الفئة", kind="money")]},
        {"name": "Variation Orders", "ar": "أوامر التغيير", "module": "contracts", "setup": True,
         "title": "Changes to a contract. Only approved ones change its value and end date (and the project's contract value)",
         "cols": [C("contract", "Contract Code", "كود العقد", width=10, required=True), C("code", "VO No.", "رقم الأمر", width=8),
                  C("date", "Date", "التاريخ", kind="date", required=True), C("description", "Description", "البيان", width=30, required=True),
                  C("amount", "Change in Value (− omission)", "التغير في القيمة", kind="money"), C("days", "Extension (days)", "مد المدة (أيام)", kind="num", width=8),
                  C("status", "Status", "الحالة", width=9, choices=["pending", "approved", "rejected"]), C("decided_by", "Decided By", "صاحب القرار", width=12),
                  C("decided_at", "Decided On", "تاريخ القرار", width=12)]},
        {"name": "Subcontractor Certificates", "ar": "مستخلصات مقاولي الباطن", "module": "contracts", "setup": True,
         "title": "One row per line of a certificate: the CUMULATIVE quantity executed to date. Rows with the same contract and number form "
                  "one certificate; Status posted books it (cost, retention, advance recovery, taxes). " + MONEY_NOTE,
         "cols": [C("contract", "Contract Code", "كود العقد", width=10, required=True), C("number", "Certificate No.", "رقم المستخلص", kind="num", width=7, required=True),
                  C("date", "Date", "التاريخ", kind="date", required=True), C("period_to", "Work Up To", "الأعمال حتى", kind="date"),
                  C("deductions", "Other Deductions", "خصومات أخرى", kind="money"), C("description", "Contract Line", "بند العقد", width=26, required=True),
                  C("qty_to_date", "Qty to Date", "الكمية التراكمية", kind="num", width=9, required=True),
                  C("status", "Status", "الحالة", width=9, choices=["draft", "approved", "posted"])]},
        {"name": "Subcontract Payments", "ar": "مدفوعات مقاولي الباطن", "module": "contracts", "setup": True,
         "title": "Advances, payments and retention released on a subcontract. " + MONEY_NOTE,
         "cols": [C("contract", "Contract Code", "كود العقد", width=10, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
                  C("kind", "Type", "النوع", width=9, required=True, choices=["advance", "payment", "release"]),
                  C("amount", "Amount", "المبلغ", kind="money", required=True), C("method", "Method", "طريقة الدفع", choices=acc.PAYMENT_METHODS),
                  C("paid_from", "Paid From (account)", "الصرف من (حساب)", kind="acct")]},
        {"name": "Purchase Requests", "ar": "طلبات الشراء", "module": "purchasing", "setup": True,
         "title": "One row per requested line; rows with the same request code form one request (nothing is booked)",
         "cols": [C("code", "Request Code", "كود الطلب", width=10, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
                  C("requested_by", "Requested By", "مقدم الطلب", width=14), C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"),
                  C("warehouse", "For Store", "للمخزن", width=14), C("needed_by", "Needed By", "مطلوب قبل", kind="date"),
                  C("status", "Status", "الحالة", width=10, choices=["draft", "submitted", "approved", "rejected", "ordered", "cancelled"]),
                  C("kind", "Line Type", "نوع البند", width=8, choices=["cost", "expense", "stock"]), C("description", "Description", "البيان", width=26, required=True),
                  C("qty", "Qty", "الكمية", kind="num", width=8), C("unit", "Unit", "الوحدة", width=7), C("est_rate", "Estimated Price", "السعر التقديري", kind="money"),
                  C("notes", "Notes", "ملاحظات", width=18)]},
        {"name": "Supplier Quotes", "ar": "عروض الموردين", "module": "purchasing", "setup": True,
         "title": "Suppliers' prices against a request's lines (by description); rows with the same request and supplier form one quote",
         "cols": [C("request", "Request Code", "كود الطلب", width=10, required=True), C("supplier", "Supplier", "المورد", width=22, required=True),
                  C("date", "Date", "التاريخ", kind="date"), C("valid_until", "Valid Until", "صالح حتى", kind="date"),
                  C("delivery_days", "Delivery (days)", "التوريد (أيام)", kind="num", width=7), C("vat_code", "VAT Code", "كود الضريبة", width=8),
                  C("terms", "Terms", "الشروط", width=18), C("description", "Request Line", "بند الطلب", width=26, required=True),
                  C("rate", "Unit Price", "سعر الوحدة", kind="money", required=True), C("awarded", "Awarded", "تمت الترسية", width=7, choices=["Yes", "No"])]},
        {"name": "Stock Counts", "ar": "الجرد الدوري", "module": "stock", "setup": True,
         "title": "A count sheet per warehouse: one row per item counted; rows with the same count code form one count. Post = Yes "
                  "books the differences as stock adjustments. " + MONEY_NOTE,
         "cols": [C("code", "Count Code", "كود الجرد", width=10, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
                  C("warehouse", "Warehouse", "المخزن", width=16, required=True), C("item", "Item", "الصنف", width=22, required=True),
                  C("counted", "Counted Qty", "الكمية الفعلية", kind="num", width=9), C("note", "Note", "ملاحظة", width=16),
                  C("counted_by", "Counted By", "القائم بالجرد", width=12), C("post", "Post", "ترحيل", width=6, choices=["Yes", "No"])]},
        {"name": "Equipment", "ar": "المعدات", "module": "equipment", "setup": True,
         "title": "Machines the company owns or runs. Re-importing a row with the same code updates it",
         "cols": [C("code", "Code", "الكود", width=9, required=True), C("name", "Name", "الاسم", width=22, required=True),
                  C("category", "Category", "الفئة", width=14), C("serial", "Serial / Plate", "الرقم التسلسلي", width=12),
                  C("asset_code", "Fixed Asset Code", "كود الأصل", width=10), C("hourly_rate", "Charge per Hour", "الفئة لكل ساعة", kind="money"),
                  C("meter_start", "Hour Meter at Start", "العداد عند التسجيل", kind="num", width=9), C("bu_code", "Now At", "موجودة في", kind="bu"),
                  C("status", "Status", "الحالة", width=10, choices=["available", "in_use", "maintenance", "retired"]), C("notes", "Notes", "ملاحظات", width=18)]},
        {"name": "Maintenance Plans", "ar": "خطط الصيانة", "module": "equipment", "setup": True,
         "title": "Preventive maintenance: every N days and/or every N working hours",
         "cols": [C("equipment", "Equipment Code", "كود المعدة", width=10, required=True), C("title", "Plan", "الخطة", width=24, required=True),
                  C("every_days", "Every (days)", "كل (يوم)", kind="num", width=7), C("every_hours", "Every (hours)", "كل (ساعة)", kind="num", width=7),
                  C("start_date", "Counting From", "الاحتساب من", kind="date"), C("notes", "Notes", "ملاحظات", width=18)]},
        {"name": "Equipment Usage", "ar": "تشغيل المعدات", "module": "equipment", "setup": True,
         "title": "Working hours (charged to the project at the machine's rate) and fuel per project. " + MONEY_NOTE,
         "cols": [C("equipment", "Equipment Code", "كود المعدة", width=10, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
                  C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu", required=True), C("hours", "Hours", "الساعات", kind="num", width=7),
                  C("operator", "Operator", "المشغل", width=14), C("fuel_qty", "Fuel (L)", "الوقود (لتر)", kind="num", width=7),
                  C("fuel_cost", "Fuel Cost", "تكلفة الوقود", kind="money"), C("method", "Fuel Paid By", "طريقة الدفع", choices=acc.PAYMENT_METHODS),
                  C("notes", "Notes", "ملاحظات", width=16)]},
        {"name": "Maintenance Log", "ar": "سجل الصيانة", "module": "equipment", "setup": True,
         "title": "Service, repairs and inspections done (cost to the equipment running-costs pool). " + MONEY_NOTE,
         "cols": [C("equipment", "Equipment Code", "كود المعدة", width=10, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
                  C("plan", "Plan", "الخطة", width=18), C("kind", "Type", "النوع", width=9, choices=["service", "repair", "inspection"]),
                  C("description", "Description", "البيان", width=24), C("hours_at", "Hour Meter", "العداد", kind="num", width=8),
                  C("cost", "Cost", "التكلفة", kind="money"), C("vendor", "Workshop / Supplier", "الورشة / المورد", width=16),
                  C("on_credit", "On Credit", "آجل", width=6, choices=["Yes", "No"]), C("method", "Paid By", "طريقة الدفع", choices=acc.PAYMENT_METHODS)]},
        {"name": "Attendance", "ar": "الحضور والانصراف", "module": "payroll", "setup": True,
         "title": "One row per worker per day: status, project, hours, overtime and output (for productivity). Re-importing a day replaces it",
         "cols": [C("date", "Date", "التاريخ", kind="date", required=True), C("employee", "Employee Code", "كود الموظف", width=10, required=True),
                  C("status", "Status", "الحالة", width=8, required=True, choices=["present", "half", "absent", "leave", "sick"]),
                  C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"), C("hours", "Hours", "الساعات", kind="num", width=6),
                  C("overtime", "Overtime h", "إضافي (س)", kind="num", width=6), C("qty", "Output Qty", "الإنتاج", kind="num", width=7),
                  C("unit", "Unit", "الوحدة", width=6), C("task", "Work Done", "العمل المنفذ", width=18), C("notes", "Notes", "ملاحظات", width=14)]},
        {"name": "E-Receipts", "ar": "الإيصالات الإلكترونية", "module": "tax", "setup": True,
         "title": "Point-of-sale receipts (B2C). One row per item; rows with the same receipt number form one receipt. Issue = Yes chains "
                  "it and posts the sale. Item codes: your EGS / GS1 codes registered with the tax authority. " + MONEY_NOTE,
         "cols": [C("number", "Receipt No.", "رقم الإيصال", width=10, required=True), C("date", "Date", "التاريخ", kind="date", required=True),
                  C("time", "Time (UTC)", "الوقت", width=6), C("kind", "Type", "النوع", width=7, choices=["sale", "return"]),
                  C("ref_number", "Return of Receipt", "مرتجع الإيصال", width=10), C("buyer_type", "Buyer Type", "نوع المشتري", width=6, choices=["P", "B", "F"]),
                  C("buyer_name", "Buyer", "المشتري", width=16), C("buyer_id", "Buyer ID / Tax No.", "الرقم القومي / الضريبي", width=14),
                  C("payment_method", "Payment", "الدفع", width=6, choices=["C", "V", "CC", "VC", "VO", "PR", "GC", "P", "O"]),
                  C("bu_code", "Business Unit", "وحدة الأعمال", kind="bu"), C("description", "Item", "الصنف", width=22, required=True),
                  C("code", "ETA Item Code", "كود الصنف", width=14), C("unit", "Unit", "الوحدة", width=6), C("qty", "Qty", "الكمية", kind="num", width=6),
                  C("price", "Unit Price", "سعر الوحدة", kind="money"), C("discount", "Discount", "الخصم", kind="money"),
                  C("vat_code", "VAT Code", "كود الضريبة", width=8), C("issue", "Issue", "إصدار", width=6, choices=["Yes", "No"])]},
    ]


# ------------------------------------------------------------------ the workbook on its own
# Formula columns (grey) and posting legs: the same entries the app makes when these rows are imported, so the
# statements in Excel include loans, custody, rental purchases, installment sales, recurring entries and subscriptions.
# Recurring entries and subscriptions count every period started by the report end date (Settings → report_to).
# {col} = this row's cell, {row} = this row's number, [[Sheet.col]] = that column of another sheet (data rows).
def _standalone(sheets, C):
    from .interchange import ROLE, TAXRATE
    by = {s["name"]: s for s in sheets}

    def calc(key, label, f, width=10):
        return C(key, label, label, kind="calc", width=width, calc=f)

    def role(key, r):
        return calc(key, f"{r} account", "=" + ROLE(r), 9)

    money = lambda m: f'IF({{{m}}}="Cash",{ROLE("cash")},{ROLE("bank")})'  # noqa: E731

    # Payroll run: the same payslip as the app (payroll.compute), and its entry
    from .interchange import PRM
    pr = "Payroll Run"
    G = lambda col: f'INDEX([[Employees.{col}]],{{x_m}})'  # noqa: E731
    on = lambda c: PRM(f"{c}_on")  # noqa: E731
    fixed = ["housing", "transport", "meal", "phone", "site", "other_allow"]
    earn_v, ded_v = ["bonus", "commission", "incentive", "other_earn"], ["penalty", "advance", "other_ded"]
    U = [PRM(f"band_{i}_upto") for i in range(1, 7)] + ["1E+15"]
    R = [PRM(f"band_{i}_rate") for i in range(1, 8)]
    T = [PRM(f"start_band_{i}_above") for i in range(2, 8)]
    A = "{x_annual}"
    k = "{x_k}"
    tax_ann = ("CHOOSE(" + k + "," + ",".join(f"{R[j]}*MIN({A},{U[j]})" for j in range(7)) + ")+"
               + "+".join(f"IF({j + 1}>{k},{R[j]}*MAX(0,MIN({A},{U[j]})-{U[j - 1]}),0)" for j in range(1, 7)))
    cost_role = (f'IF(LEFT({{x_ct}},6)="Direct",{ROLE("direct_labour")},IF(LEFT({{x_ct}},6)="Indire",{ROLE("project_overhead")},'
                 f'{ROLE("admin_salaries")}))')
    by[pr]["cols"] += [
        calc("x_m", "employee row", '=IF({employee}="","",MATCH({employee}&"",[[Employees.code]],0))'),
        calc("x_type", "pay type", f'=IF({{x_m}}="","",IF({G("emp_type")}&""="","monthly",{G("emp_type")}&""))'),
        calc("bu_code", "business unit", f'=IF({{x_m}}="","",{G("bu_code")}&"")'),
        calc("x_ct", "cost type", f'=IF({{x_m}}="","",IF({G("cost_type")}&""="","Administrative Salaries (Shared Overhead)",{G("cost_type")}&""))'),
        calc("x_dm", "days / month", "=" + PRM("days_month")),
        calc("x_factor", "paid share of the month", f'=IF({{x_type}}="monthly",MAX(0,{{x_dm}}-N({{absent_days}}))/{{x_dm}},1)'),
        calc("x_basic", "basic earned", f'=IF({{x_m}}="",0,ROUND(IF({{x_type}}="monthly",N({G("basic")})*{{x_factor}},'
                                         f'IF({{x_type}}="daily",N({G("daily_rate")}),IF({{x_type}}="hourly",N({G("hourly_rate")}),N({G("piece_rate")})))*N({{units}})),2))'),
        calc("x_pf", "allowance share", f'=IF(AND({{x_type}}="monthly",{PRM("prorate_allowances")}=1),{{x_factor}},1)')]
    by[pr]["cols"] += [calc(f"x_al_{c}", f"{c} allowance", f'=IF({{x_m}}="",0,ROUND(N({G("al_" + c)})*{{x_pf}},2)*{on(c)})') for c in fixed]
    by[pr]["cols"] += [
        calc("x_hr", "hourly base", f'=IF({{x_m}}="",0,IF({{x_type}}="monthly",N({G("basic")})/{{x_dm}}/{PRM("hours_day")},'
                                    f'IF({{x_type}}="daily",N({G("daily_rate")})/{PRM("hours_day")},N({G("hourly_rate")}))))'),
        calc("x_ot", "overtime", f'=ROUND({{x_hr}}*(N({{ot_day}})*{PRM("ot_day")}+N({{ot_night}})*{PRM("ot_night")}+N({{ot_rest}})*{PRM("ot_rest")}),2)'),
        calc("x_gross", "gross", "={x_basic}+" + "+".join(f"{{x_al_{c}}}" for c in fixed) + "+{x_ot}+"
             + "+".join(f"ROUND(N({{{c}}}),2)*{on(c)}" for c in earn_v)),
        calc("x_taxearn", "taxable earnings", "={x_basic}+{x_ot}+" + "+".join(f"{{x_al_{c}}}*{PRM(c + '_tax')}" for c in fixed) + "+"
             + "+".join(f"ROUND(N({{{c}}}),2)*{on(c)}*{PRM(c + '_tax')}" for c in earn_v)),
        calc("x_pretax", "deductions before tax", "=" + "+".join(f"ROUND(N({{{c}}}),2)*{on(c)}*{PRM(c + '_tax')}" for c in ded_v)),
        calc("x_base", "insurable base", f'=IF({{x_m}}="",0,IF(N({G("insurable_wage")})>0,N({G("insurable_wage")}),IF({{x_type}}="monthly",N({G("basic")}),'
                                         f'IF({{x_type}}="daily",N({G("daily_rate")})*{{x_dm}},0))+'
                                         + "+".join(f'N({G("al_" + c)})*{PRM(c + "_ins")}*{on(c)}' for c in fixed) + "))"),
        calc("x_ins", "insurable wage", f'=IF({{x_m}}="",0,IF(AND({G("insured")}&""="Yes",{{x_base}}>0),MIN(MAX({{x_base}},{PRM("si_min")}),{PRM("si_max")}),0))'),
        calc("x_siemp", "insurance (employee)", f'=ROUND({{x_ins}}*{PRM("si_emp_rate")},2)'),
        calc("x_sier", "insurance (employer)", f'=ROUND({{x_ins}}*{PRM("si_er_rate")},2)'),
        calc("x_taxed", "taxed", f'=IF({{x_m}}="",0,IF({G("taxed")}&""="Yes",1,0))'),
        calc("x_annual", "annual taxable", f'=IF({{x_taxed}}=1,ROUND(MAX(0,{{x_taxearn}}-{{x_pretax}}-{{x_siemp}})*12-{PRM("exemption")},2),0)'),
        calc("x_k", "first band", "=MAX(1," + ",".join(f"IF({A}>{T[i]},{i + 2},1)" for i in range(6)) + ")"),
        calc("x_tax", "salary tax", f'=IF({A}<=0,0,ROUND(({tax_ann})/12,2))'),
        calc("x_mf", "Martyrs Fund", f'=IF({{x_taxed}}=1,ROUND({{x_gross}}*{PRM("martyrs_rate")},2),0)'),
        calc("x_adv", "advance recovered", f'=ROUND(N({{advance}}),2)*{on("advance")}'),
        calc("x_kept", "penalties & deductions", f'=ROUND(N({{penalty}}),2)*{on("penalty")}+ROUND(N({{other_ded}}),2)*{on("other_ded")}'),
        calc("x_net", "net pay", "=ROUND({x_gross}-{x_kept}-{x_adv}-{x_siemp}-{x_tax}-{x_mf},2)"),
        calc("x_cost", "salary cost", "=ROUND({x_gross}-{x_kept},2)"),
        calc("x_cacct", "cost account", f'=IF({{x_m}}="","",IF({G("account")}&""<>"",{G("account")}&"",{cost_role}))'),
        calc("x_eracct", "employer insurance account", f'=IF(LEFT({{x_ct}},6)="Admini",IFERROR({ROLE("employer_si")},{{x_cacct}}),{{x_cacct}})'),
        calc("x_sitot", "insurance payable", "={x_siemp}+{x_sier}"), role("x_siacct", "social_insurance_payable"),
        role("x_taxacct", "salary_tax_payable"), role("x_mfacct", "martyrs_fund"), role("x_advacct", "employee_advances"),
        calc("x_paid", "paid (up to the net)", "=MAX(0,MIN(N({paid}),{x_net}))"),
        calc("x_money", "money account", f'=IF({{x_m}}="",{ROLE("bank")},IF(TRIM(IF({{method}}<>"",{{method}},{G("method")}&""))="Cash",{ROLE("cash")},{ROLE("bank")}))'),
        calc("x_unpaid", "owed to the employee", "={x_net}-{x_paid}"), role("x_accr", "accrued_payroll")]
    by[pr]["legs"] = [("x_cost", "x_cacct", "D"), ("x_sier", "x_eracct", "D"), ("x_sitot", "x_siacct", "C"), ("x_tax", "x_taxacct", "C"),
                      ("x_mf", "x_mfacct", "C"), ("x_adv", "x_advacct", "C"), ("x_paid", "x_money", "C"), ("x_unpaid", "x_accr", "C")]

    # Loans: the drawdown (money paid out / received); "opening" loans are already in the books
    by["Loans"]["cols"] += [
        calc("date", "Date (start)", '=IF({start_date}="","",{start_date})'),
        calc("x_amt", "booked amount", '=IF(OR({start_date}="",{booking}="opening"),0,N({principal}))'),
        calc("x_cash", "money account", "=" + money("method")),
        calc("x_lacct", "loan account", f'=IF({{direction}}="lent",IF(N({{term_months}})>12,{ROLE("loans_receivable_lt")},{ROLE("loans_receivable")}),'
                                       f'IF(N({{term_months}})>12,{ROLE("long_term_debt")},{ROLE("short_term_debt")}))'),
        calc("x_dr", "debit", '=IF({direction}="lent",{x_lacct},{x_cash})'),
        calc("x_cr", "credit", '=IF({direction}="lent",{x_cash},{x_lacct})')]
    by["Loans"]["legs"] = [("x_amt", "x_dr", "D"), ("x_amt", "x_cr", "C")]

    # Loan transactions: installment (principal + interest), accrued interest, fees, further drawdowns
    look = lambda col, dflt: f'IFERROR(INDEX([[Loans.{col}]],MATCH({{loan}},[[Loans.code]],0)),{dflt})'  # noqa: E731
    lt = '{x_dir}="lent"'
    by["Loan Transactions"]["cols"] += [
        calc("x_dir", "direction", "=" + look("direction", '"borrowed"')),
        calc("x_lacct", "loan account", "=" + look("x_lacct", ROLE("short_term_debt"))),
        calc("x_cash", "money account", "=" + money("method")),
        calc("x_d1a", "debit 1", f'=IF({{action}}="pay",IF({lt},N({{principal}})+N({{interest}}),N({{principal}})),'
                                 'IF(OR({action}="interest",{action}="fee",{action}="draw"),N({amount}),0))'),
        calc("x_d1", "debit 1 account", f'=IF({{action}}="pay",IF({lt},{{x_cash}},{{x_lacct}}),IF({{action}}="interest",'
                                         f'IF({lt},{ROLE("interest_receivable")},{ROLE("interest")}),IF({{action}}="fee",'
                                         f'IF({lt},{{x_cash}},{ROLE("bank_charges")}),IF({lt},{{x_lacct}},{{x_cash}}))))'),
        calc("x_d2a", "debit 2", f'=IF(AND({{action}}="pay",NOT({lt})),N({{interest}}),0)'),
        role("x_d2", "interest"),
        calc("x_c1a", "credit 1", f'=IF({{action}}="pay",IF({lt},N({{principal}}),N({{principal}})+N({{interest}})),{{x_d1a}})'),
        calc("x_c1", "credit 1 account", f'=IF({{action}}="pay",IF({lt},{{x_lacct}},{{x_cash}}),IF({{action}}="interest",'
                                          f'IF({lt},{ROLE("interest_income")},{ROLE("accrued_interest")}),IF({{action}}="fee",'
                                          f'IF({lt},{ROLE("other_income")},{{x_cash}}),IF({lt},{{x_cash}},{{x_lacct}}))))'),
        calc("x_c2a", "credit 2", f'=IF(AND({{action}}="pay",{lt}),N({{interest}}),0)'),
        role("x_c2", "interest_income")]
    by["Loan Transactions"]["legs"] = [("x_d1a", "x_d1", "D"), ("x_d2a", "x_d2", "D"), ("x_c1a", "x_c1", "C"), ("x_c2a", "x_c2", "C")]

    # Cash custody given to an employee
    by["Cash Custody"]["cols"] += [
        calc("date", "Date (given)", '=IF({issued_on}="","",{issued_on})'),
        calc("x_amt", "booked amount", '=IF(OR({issued_on}="",{booking}="opening"),0,N({amount}))'),
        role("x_cust", "custody"), calc("x_cash", "money account", "=" + money("method"))]
    by["Cash Custody"]["legs"] = [("x_amt", "x_cust", "D"), ("x_amt", "x_cash", "C")]

    # Custody expense reports: the expense (net) and its VAT, out of the custody
    by["Custody Expenses"]["cols"] += [
        calc("x_net", "net", f'=IF({{amount}}="",0,ROUND(N({{amount}})/(1+{TAXRATE("{vat_code}")}),2))'),
        calc("x_vat", "VAT", "=N({amount})-{x_net}"),
        calc("x_acct", "expense account", f'=IF({{account}}="",{ROLE("other_overhead")},{{account}}&"")'),
        role("x_vin", "vat_input"), role("x_cust", "custody")]
    by["Custody Expenses"]["legs"] = [("x_net", "x_acct", "D"), ("x_vat", "x_vin", "D"), ("amount", "x_cust", "C")]

    # Rental pieces bought: to the fleet, paid now from the bank, the rest owed to the supplier
    by["Rental Stock Lots"]["cols"] += [
        calc("x_total", "cost", '=IF({source}="opening",0,N({qty})*N({unit_cost}))'),
        calc("x_paid", "paid", '=IF({paid}="",{x_total},MIN(N({paid}),{x_total}))'),
        calc("x_unpaid", "owed", "={x_total}-{x_paid}"),
        role("x_fleet", "rental_fleet"), role("x_bank", "bank"), role("x_ap", "ap")]
    by["Rental Stock Lots"]["legs"] = [("x_total", "x_fleet", "D"), ("x_paid", "x_bank", "C"), ("x_unpaid", "x_ap", "C")]

    # Rental contracts: our cost of one-time services (installation, transport) for their profit
    by["Rental Contracts"]["cols"].insert(next(i for i, c in enumerate(by["Rental Contracts"]["cols"]) if c["key"] == "charge_type") + 1,
                                          C("cost", "Our Cost (charge lines)", "تكلفتنا (للمبالغ)", kind="money",
                                            note="what the crew / truck costs us — for the services profit"))

    # Rental contracts: each line's monthly rent and the contract's total on its first row (the dashboard's ongoing rents)
    rc = "Rental Contracts"
    by[rc]["cols"] += [
        calc("x_first", "first row of the contract", f'=IF({{ref}}="",0,IF(MATCH({{ref}}&"",[[{rc}.ref]],0)={{row}}-4,1,0))'),
        calc("x_rent", "rent / month (line)", '=IF(OR({ref}="",{line_kind}="charge",{charge_type}<>""),0,N({qty})*N({rate}))'),
        calc("x_crent", "contract rent / month", f'=IF({{x_first}}=1,SUMIFS([[{rc}.x_rent]],[[{rc}.ref]],{{ref}}),0)'),
        calc("x_chg", "one-time charge (line)", '=IF({ref}="",0,IF(OR({line_kind}="charge",{charge_type}<>""),N({qty})*N({rate}),0))'),
        calc("x_charges", "one-time charges (contract)", f'=IF({{x_first}}=1,SUMIFS([[{rc}.x_chg]],[[{rc}.ref]],{{ref}}),0)')]

    # Sales contracts (one row per line; sale-level amounts on the sale's first row)
    sc = "Sales Contracts"
    first = "{x_first}=1"
    by[sc]["cols"] += [
        calc("x_price", "unit price", '=IF({price}<>"",N({price}),IFERROR(INDEX([[Products.price]],MATCH({product},[[Products.code]],0)),0))'),
        calc("x_net", "line net", '=IF({code}="",0,N(IF({qty}="",1,{qty}))*{x_price}-N({discount}))'),
        calc("x_first", "first row of the sale", f'=IF({{code}}="",0,IF(MATCH({{code}},[[{sc}.code]],0)={{row}}-4,1,0))'),
        calc("x_on", "booked", f'=IF(AND({first},OR({{confirm}}="Yes",N({{collected}})>0)),1,0)'),
        calc("x_tnet", "sale net", f'=IF({{x_on}}=1,SUMIFS([[{sc}.x_net]],[[{sc}.code]],{{code}}),0)'),
        calc("x_vat", "sale VAT", f'=ROUND({{x_tnet}}*{TAXRATE("{vat_code}")},2)'),
        calc("x_total", "sale total", "={x_tnet}+{x_vat}"),
        calc("x_sale", "revenue now", '=IF({revenue_on}="delivery",0,1)'),
        calc("x_every", "months between", '=IF({frequency}="quarterly",3,IF({frequency}="semiannual",6,IF({frequency}="annual",12,1)))'),
        calc("x_markup", "installment interest", '=IF(AND({x_sale}=1,{plan}="installments"),ROUND(({x_total}-ROUND({x_total}*N({down_pct})/100,2))'
                                                 '*N({interest})/100*N(IF({n}="",12,{n}))*{x_every}/12,2),0)'),
        calc("x_ar", "invoiced", "={x_total}*{x_sale}"), calc("x_rev", "revenue", "={x_tnet}*{x_sale}"),
        calc("x_vatp", "output VAT", "={x_vat}*{x_sale}"), calc("x_col", "collected", "=IF({x_on}=1,N({collected}),0)"),
        calc("x_colacct", "collected into", f'=IF({{x_sale}}=1,{ROLE("ar")},{ROLE("client_advances")})'),
        calc("x_earn", "interest earned", "=IF({x_markup}>0,ROUND({x_markup}*MIN(1,{x_col}/({x_total}+{x_markup})),2),0)"),
        role("x_arac", "ar"), role("x_sales", "product_sales"), role("x_vo", "vat_output"), role("x_idef", "installment_deferred"),
        role("x_iinc", "installment_income"), calc("x_money", "money account", "=" + money("method")),
        # cost of sales when the revenue is booked: stock items at their average cost, units / goods at the product's cost
        calc("x_stk", "stock item", '=IF({product}="","",IFERROR(INDEX([[Products.stock_item]],MATCH({product},[[Products.code]],0))&"",""))'),
        calc("x_pinv", "inventory account", '=IF({product}="","",IFERROR(INDEX([[Products.inventory_account]],MATCH({product},[[Products.code]],0))&"",""))'),
        calc("x_cu", "unit cost", '=IF({x_stk}<>"",IFERROR(SUMIFS([[Stock Moves.x_rval]],[[Stock Moves.item]],{x_stk},[[Stock Moves.date]],"<="&{date})'
                                  '/SUMIFS([[Stock Moves.x_rqty]],[[Stock Moves.item]],{x_stk},[[Stock Moves.date]],"<="&{date}),0),'
                                  'IF({x_pinv}<>"",N(IFERROR(INDEX([[Products.cost]],MATCH({product},[[Products.code]],0)),0)),0))'),
        calc("x_cogs", "cost of sales", f'=IF({{code}}="",0,ROUND(N(IF({{qty}}="",1,{{qty}}))*{{x_cu}},2)'
                                        f'*IFERROR(INDEX([[{sc}.x_on]],MATCH({{code}},[[{sc}.code]],0))*INDEX([[{sc}.x_sale]],MATCH({{code}},[[{sc}.code]],0)),0))'),
        role("x_cos", "cost_of_sales"),
        calc("x_cinv", "stock / units account", f'=IF({{x_stk}}<>"",{ROLE("inventory")},IF({{x_pinv}}<>"",{{x_pinv}},{ROLE("goods_inventory")}))')]
    by[sc]["legs"] = [("x_ar", "x_arac", "D"), ("x_rev", "x_sales", "C"), ("x_vatp", "x_vo", "C"), ("x_markup", "x_arac", "D"),
                      ("x_markup", "x_idef", "C"), ("x_col", "x_money", "D"), ("x_col", "x_colacct", "C"), ("x_earn", "x_idef", "D"),
                      ("x_earn", "x_iinc", "C"), ("x_cogs", "x_cos", "D"), ("x_cogs", "x_cinv", "C")]

    # Recurring entries: every period started by the report end date (automatic ones post by themselves in the app)
    re_ = "Recurring Entries"
    lim = '{x_lim}'
    by[re_]["cols"] += [
        calc("date", "Date (first)", '=IF({start}="","",{start})'),
        calc("x_lim", "counted until", '=MIN(ReportTo,IF({end_date}="",ReportTo,{end_date}))'),
        calc("x_n", "times so far", f'=IF(OR({{start}}="",{{start}}>{lim}),0,MIN(IF(N({{max_count}})>0,N({{max_count}}),99999),'
                                    f'INT(IF({{unit}}="day",{lim}-{{start}},IF({{unit}}="week",({lim}-{{start}})/7,IF({{unit}}="year",'
                                    f'DATEDIF({{start}},{lim},"y"),DATEDIF({{start}},{lim},"m"))))/MAX(1,N({{every}})))+1))'),
        calc("x_tot", "total so far", '=IF(OR({doc_type}="depreciation",{mode}<>"auto"),0,{x_n}*N({amount}))'),
        calc("x_dr", "debit", f'=IF({{account}}<>"",{{account}}&"",IF({{doc_type}}="direct_cost",{ROLE("materials")},IF({{doc_type}}="payroll",'
                              f'{ROLE("direct_labour")},IF({{doc_type}}="payment",{ROLE("ap")},IF({{doc_type}}="receipt",{ROLE("bank")},'
                              f'{ROLE("other_overhead")})))))'),
        calc("x_cr", "credit", f'=IF({{doc_type}}="expense",{ROLE("bank")},IF({{doc_type}}="direct_cost",{ROLE("ap")},IF({{doc_type}}="payroll",'
                               f'{ROLE("accrued_payroll")},IF({{doc_type}}="payment",{ROLE("bank")},IF({{credit_account}}<>"",{{credit_account}}&"",'
                               f'{ROLE("revenue")})))))')]
    # a receipt debits the bank whatever "account" says; its credit is the credit account (or revenue)
    by[re_]["cols"][-2] = calc("x_dr", "debit", f'=IF({{doc_type}}="receipt",{ROLE("bank")},IF({{account}}<>"",{{account}}&"",'
                                                 f'IF({{doc_type}}="direct_cost",{ROLE("materials")},IF({{doc_type}}="payroll",{ROLE("direct_labour")},'
                                                 f'IF({{doc_type}}="payment",{ROLE("ap")},{ROLE("other_overhead")})))))')
    by[re_]["legs"] = [("x_tot", "x_dr", "D"), ("x_tot", "x_cr", "C")]

    # Service subscriptions: invoices for every period started, longer periods earned month by month
    ss, sp = "Service Subscriptions", "Service Plans"
    pl = lambda col, dflt: f'IFERROR(INDEX([[{sp}.{col}]],MATCH({{plan}},[[{sp}.code]],0)),{dflt})'  # noqa: E731
    plan_vat, plan_rev = pl("vat_code", '""'), pl("revenue_account", '""')
    by[ss]["cols"] += [
        calc("date", "Date (start)", '=IF({start}="","",{start})'),
        calc("x_price", "price", f'=IF({{price}}<>"",N({{price}}),N({pl("price", 0)}))'),
        calc("x_per", "every (months)", f'=MAX(1,IF({{period_months}}<>"",N({{period_months}}),N({pl("period_months", 1)})))'),
        calc("x_vatc", "VAT code", f'=IF({{vat_code}}<>"",{{vat_code}}&"",{plan_vat}&"")'),
        calc("x_racc", "revenue account", f'=IF({plan_rev}&""="",{ROLE("service_revenue")},{plan_rev}&"")'),
        calc("x_m", "months started", '=IF(OR({start}="",{start}>ReportTo),0,DATEDIF({start},ReportTo,"m")+1)'),
        calc("x_n", "invoices", '=IF({x_m}=0,0,MIN(INT(({x_m}-1)/{x_per})+1,IF(AND(N({term_months})>0,{auto_renew}="No"),'
                                'ROUNDUP(N({term_months})/{x_per},0),99999)))'),
        calc("x_billed", "billed", "={x_n}*{x_price}"),
        calc("x_vat", "VAT", f'=ROUND({{x_billed}}*{TAXRATE("{x_vatc}")},2)'),
        calc("x_ar", "receivable", "={x_billed}+{x_vat}"),
        calc("x_earned", "earned", "=IF({x_per}=1,{x_billed},ROUND({x_price}/{x_per}*MIN({x_n}*{x_per},{x_m}),2))"),
        calc("x_def", "billed, not yet earned", "={x_billed}-{x_earned}"),
        calc("x_monthly", "monthly value", '=IF({x_price}=0,0,ROUND({x_price}/{x_per},2))'),
        calc("x_next", "next invoice", '=IF({start}="","",EDATE({start},{x_n}*{x_per}))'),
        role("x_arac", "ar"), role("x_sdef", "service_deferred"), role("x_vo", "vat_output")]
    by[ss]["legs"] = [("x_ar", "x_arac", "D"), ("x_earned", "x_racc", "C"), ("x_def", "x_sdef", "C"), ("x_vat", "x_vo", "C")]

    # Subcontract payments: an advance (to supplier advances), a payment (settles what he is owed), retention released
    sp_ = "Subcontract Payments"
    by[sp_]["cols"] += [
        calc("x_money", "money account", f'=IF({{paid_from}}<>"",{{paid_from}}&"",{money("method")})'),
        calc("x_dr", "debit", f'=IF({{kind}}="advance",{ROLE("supplier_advances")},IF({{kind}}="release",{ROLE("retention_payable")},{ROLE("sub_payable")}))'),
        calc("x_cr", "credit", f'=IF({{kind}}="release",{ROLE("sub_payable")},{{x_money}})')]
    by[sp_]["legs"] = [("amount", "x_dr", "D"), ("amount", "x_cr", "C")]

    # Subcontractor certificates (posted ones): this period = (qty to date − the previous certificate's) × the contract rate;
    # retention, advance recovery (cumulative, never more than the advance paid) and taxes on the certificate's first row
    sc_, cn = "Subcontractor Certificates", "Contracts"
    head = lambda col: f'INDEX([[{cn}.{col}]],MATCH({{contract}},[[{cn}.code]],0))'  # noqa: E731
    by[sc_]["cols"] += [
        calc("x_key", "certificate", '=IF({contract}="","",{contract}&"|"&{number})'),
        calc("x_on", "posted", '=IF(INDEX([[Subcontractor Certificates.status]],MATCH({x_key},[[Subcontractor Certificates.x_key]],0))="posted",1,0)'),
        calc("x_prev", "previous qty", f'=SUMIFS([[{sc_}.qty_to_date]],[[{sc_}.contract]],{{contract}},[[{sc_}.description]],{{description}},'
                                       f'[[{sc_}.number]],N({{number}})-1)'),
        calc("x_rate", "rate", f'=SUMIFS([[{cn}.rate]],[[{cn}.code]],{{contract}},[[{cn}.description]],{{description}})'),
        calc("x_val", "this period (line)", "=IF({contract}=\"\",0,ROUND((N({qty_to_date})-{x_prev})*{x_rate},2)*{x_on})"),
        calc("x_first", "first row", f'=IF({{x_key}}="",0,IF(MATCH({{x_key}},[[{sc_}.x_key]],0)={{row}}-4,1,0))'),
        calc("x_this", "this period", f'=IF({{x_first}}=1,SUMIFS([[{sc_}.x_val]],[[{sc_}.x_key]],{{x_key}}),0)'),
        calc("x_base", "after deductions", "=IF({x_first}=1,{x_this}-N({deductions})*{x_on},0)"),
        calc("x_ret", "retention", f'=IF({{x_first}}=1,ROUND({{x_this}}*N({head("retention_pct")}),2),0)'),
        calc("x_adv", "advance paid", f'=SUMIFS([[{sp_}.amount]],[[{sp_}.contract]],{{contract}},[[{sp_}.kind]],"advance")'),
        calc("x_cum", "certified to here", f'=SUMIFS([[{sc_}.x_val]],[[{sc_}.contract]],{{contract}},[[{sc_}.number]],"<="&N({{number}}))'),
        calc("x_rec", "advance recovered", f'=IF({{x_first}}=1,MIN({{x_adv}},ROUND({{x_cum}}*N({head("advance_recovery_pct")}),2))'
                                           f'-MIN({{x_adv}},ROUND(({{x_cum}}-{{x_this}})*N({head("advance_recovery_pct")}),2)),0)'),
        calc("x_vatc", "VAT code", f'=IFERROR({head("vat_code")}&"","")'), calc("x_whtc", "WHT code", f'=IFERROR({head("wht_code")}&"","")'),
        calc("x_vat", "VAT", f'=ROUND({{x_base}}*{TAXRATE("{x_vatc}")},2)'), calc("x_wht", "withholding", f'=ROUND({{x_base}}*{TAXRATE("{x_whtc}")},2)'),
        calc("x_net", "owed to him", "={x_base}+{x_vat}-{x_ret}-{x_rec}-{x_wht}"),
        role("x_cost", "subcontract_cost"), role("x_vin", "vat_input"), role("x_pay", "sub_payable"), role("x_retp", "retention_payable"),
        role("x_sadv", "supplier_advances"), role("x_whtp", "wht_payable")]
    by[sc_]["legs"] = [("x_base", "x_cost", "D"), ("x_vat", "x_vin", "D"), ("x_net", "x_pay", "C"), ("x_ret", "x_retp", "C"),
                       ("x_rec", "x_sadv", "C"), ("x_wht", "x_whtp", "C")]

    # Equipment: hours × the machine's rate to the project against the running-costs pool; fuel straight to the project
    eu = "Equipment Usage"
    by[eu]["cols"] += [
        calc("x_charge", "hours charged", '=ROUND(N({hours})*N(IFERROR(INDEX([[Equipment.hourly_rate]],MATCH({equipment},[[Equipment.code]],0)),0)),2)'),
        role("x_used", "equipment_charge"), role("x_pool", "equipment_pool"), calc("x_money", "money account", "=" + money("method"))]
    by[eu]["legs"] = [("x_charge", "x_used", "D"), ("x_charge", "x_pool", "C"), ("fuel_cost", "x_used", "D"), ("fuel_cost", "x_money", "C")]
    ml = "Maintenance Log"
    by[ml]["cols"] += [
        role("x_pool", "equipment_pool"),
        calc("x_cr", "paid from / owed to", f'=IF(AND({{on_credit}}="Yes",{{vendor}}<>""),{ROLE("ap")},{money("method")})')]
    by[ml]["legs"] = [("cost", "x_pool", "D"), ("cost", "x_cr", "C")]

    # E-receipts (issued): the sale (or a return, reversed) into cash / bank, revenue and output VAT
    er = "E-Receipts"
    hd = lambda col: f'INDEX([[{er}.{col}]],MATCH({{number}},[[{er}.number]],0))'  # noqa: E731
    by[er]["cols"] += [
        calc("x_sign", "booked", f'=IF({{number}}="",0,IF({hd("issue")}="Yes",IF({hd("kind")}="return",-1,1),0))'),
        calc("x_net", "net", "=ROUND((N(IF({qty}=\"\",1,{qty}))*N({price})-N({discount}))*{x_sign},2)"),
        calc("x_vat", "VAT", f'=ROUND({{x_net}}*{TAXRATE("{vat_code}")},2)'),
        calc("x_total", "total", "={x_net}+{x_vat}"),
        calc("x_money", "money account", f'=IF({{number}}="","",IF(OR({hd("payment_method")}="C",{hd("payment_method")}="CC"),{ROLE("cash")},{ROLE("bank")}))'),
        role("x_rev", "revenue"), role("x_vo", "vat_output")]
    by[er]["legs"] = [("x_total", "x_money", "D"), ("x_net", "x_rev", "C"), ("x_vat", "x_vo", "C")]

    # Purchase orders: commitments (no money until received): line value and the order's open amount
    po = "Purchase Orders"
    by[po]["cols"] += [
        calc("x_amt", "line value", '=N({qty})*N({rate})'),
        calc("x_first", "first row", f'=IF({{code}}="",0,IF(MATCH({{code}},[[{po}.code]],0)={{row}}-4,1,0))'),
        calc("x_order", "order total", f'=IF({{x_first}}=1,SUMIFS([[{po}.x_amt]],[[{po}.code]],{{code}}),0)'),
        calc("x_received", "received (direct costs)", '=IF({x_first}=1,IFERROR(SUMIFS([[Direct Costs.total]],[[Direct Costs.deal_ref]],{code}),0),0)'),
        calc("x_open", "still on order", '=IF(AND({x_first}=1,{approved}="Yes"),MAX(0,{x_order}-{x_received}),0)')]
    return sheets


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
        ln = {"kind": kind, "qty": r.get("qty"), "rate": r.get("rate"), "description": r.get("description"), "cost": r.get("cost") or 0}
        if kind == "item":
            ln["item"] = r.get("item")
        elif kind == "kit":
            ln["kit"] = r.get("item")
        else:
            ln["charge_type"] = r.get("charge_type") or "other"
        lines.append(ln)
    rid = rentals.save(db, {**h, "lines": lines}, user=user)
    if h.get("bu_code"):    # the contract's site = an existing business unit (its extracts and receipts)
        bu = db.one("SELECT id FROM business_units WHERE code=?", (text(h["bu_code"]),))
        if not bu:
            raise ValidationError(f"Business unit {h['bu_code']} not found")
        db.conn.execute("UPDATE rentals SET bu_id=? WHERE id=?", (bu["id"], rid))
    if h.get("po_number"):
        rentals.action(db, rid, "po", {"po_number": h["po_number"], "start_date": h.get("start_date")}, user)
        if h.get("signed_client"):
            rentals.action(db, rid, "sign", {"signed_client": h["signed_client"], "signed_us": h.get("signed_us") or "",
                                            "signed_on": h.get("start_date") or h.get("quote_date")}, user)
    if h.get("delivered_on"):
        if not (h.get("po_number") and h.get("signed_client")):
            raise ValidationError("Delivered On needs the client's PO number and both signatures on the contract")
        rentals.import_delivery(db, rid, acc.clean_date(h["delivered_on"]), h.get("signed_us") or "", h["signed_client"],
                                acc.clean_date(h["invoiced_to"]) if h.get("invoiced_to") else None, user)


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


def budget_row(db, r, user):
    from . import budgets
    budgets.import_row(db, r)


def service_plan(db, r, user):
    from . import services
    ex = db.one("SELECT id FROM service_plans WHERE code=?", (r.get("code"),)) if r.get("code") else None
    services.save_plan(db, r, ex["id"] if ex else None)


def service_sub(db, r, user):
    from . import services
    services.save(db, {**r, "auto_renew": "No" if str(r.get("auto_renew") or "").lower() in ("no", "لا") else "Yes"}, user)


def purchase_order(db, items, user):
    from . import purchasing
    h = items[0][1]
    lines = [{"kind": r.get("kind") or ("cost" if h.get("bu_code") else "expense"), "description": r.get("description"),
              "item": r.get("description") if r.get("kind") == "stock" else "", "account": r.get("account"), "qty": r.get("qty"),
              "unit": r.get("unit"), "rate": r.get("rate")} for _, r in items]
    pid = purchasing.save(db, {"code": h.get("code"), "date": h.get("date"), "supplier": h.get("supplier"), "bu_code": h.get("bu_code"),
                               "warehouse": h.get("warehouse") or None, "expected": h.get("expected"), "vat_code": h.get("vat_code"),
                               "terms": h.get("terms"), "lines": lines}, user)
    if _yes(h.get("approved")):
        purchasing.approve(db, pid, user)


def employee_row(db, r, user=""):
    from . import payroll
    ex = db.one("SELECT id FROM employees WHERE code=?", (text(r.get("code")),))
    payroll.save_employee(db, r, ex["id"] if ex else None)


def payroll_settings_rows(db, items, user=""):
    from . import payroll
    payroll.import_settings_rows(db, [r for _, r in items])


def payroll_run_rows(db, items, user=""):
    from . import payroll
    payroll.run_sheet_import(db, items, user)


# ------------------------------------------------------------------ projects, contracts & the site
def phase_row(db, r, user=""):
    from . import projects
    bu = db.one("SELECT id FROM business_units WHERE code=?", (text(r.get("bu_code")),))
    ex = db.one("SELECT id FROM project_phases WHERE bu_id=? AND name=?", (bu["id"], text(r.get("name")))) if bu else None
    projects.save_phase(db, r, ex["id"] if ex else None)


def task_row(db, r, user=""):
    from . import projects
    bu = db.one("SELECT id FROM business_units WHERE code=?", (text(r.get("bu_code")),)) if r.get("bu_code") else None
    ph = db.one("SELECT id FROM project_phases WHERE bu_id=? AND name=?", (bu["id"], text(r.get("phase")))) if bu and r.get("phase") else None
    ex = db.one("SELECT id FROM project_tasks WHERE COALESCE(bu_id,0)=? AND title=?", (bu["id"] if bu else 0, text(r.get("title"))))
    tid = projects.save_task(db, {**r, "bu_id": bu["id"] if bu else None, "phase_id": ph["id"] if ph else None},
                             ex["id"] if ex else None, text(r.get("created_by")) or user)
    if r.get("done_at"):
        db.x("UPDATE project_tasks SET done_at=? WHERE id=?", (text(r["done_at"]), tid))


def contract_rows(db, items, user=""):
    from . import contracts
    h = items[0][1]
    lines = [{k: r.get(k) for k in ("item_no", "description", "unit", "qty", "rate")} for _, r in items if text(r.get("description"))]
    ex = db.one("SELECT id FROM contracts WHERE code=?", (text(h.get("code")),))
    contracts.save(db, {**h, "lines": lines}, ex["id"] if ex else None)


def vo_row(db, r, user=""):
    from . import contracts
    c = contracts.get(db, text(r.get("contract")))
    if r.get("code") and db.one("SELECT 1 FROM variation_orders WHERE contract_id=? AND code=?", (c["id"], text(r["code"]))):
        return
    vid = contracts.save_vo(db, c["id"], r)
    st = text(r.get("status")).lower()
    if st in ("approved", "rejected"):
        contracts.decide_vo(db, vid, st, text(r.get("decided_by")) or user)
        if r.get("decided_at"):
            db.x("UPDATE variation_orders SET decided_at=? WHERE id=?", (text(r["decided_at"]), vid))


def certificate_rows(db, items, user=""):
    from . import contracts
    h = items[0][1]
    c = contracts.get(db, text(h.get("contract")))
    by = {ln["description"]: ln["id"] for ln in c["lines"]}
    lines = []
    for _, r in items:
        d = text(r.get("description"))
        if d not in by:
            raise ValidationError(f"'{d}' is not a line of contract {c['code']}")
        lines.append({"line_id": by[d], "qty_to_date": r.get("qty_to_date")})
    xid = contracts.save_certificate(db, c["id"], {**h, "lines": lines})
    db.x("UPDATE sub_certificates SET number=? WHERE id=?", (int(float(h.get("number"))), xid))
    st = text(h.get("status")).lower()
    if st in ("approved", "posted"):
        contracts.approve_certificate(db, xid, user)
    if st == "posted":
        contracts.post_certificate(db, xid, user)


def sub_payment(db, r, user=""):
    from . import contracts
    contracts.pay(db, text(r.get("contract")), {**r, "allow_overpay": True}, user)


def request_rows(db, items, user=""):
    from . import procurement
    h = items[0][1]
    if db.one("SELECT 1 FROM purchase_requests WHERE code=?", (text(h.get("code")),)):
        raise ValidationError(f"Request {h.get('code')} already exists")
    pid = procurement.save(db, {**h, "warehouse": h.get("warehouse") or None,
                                "lines": [{k: r.get(k) for k in ("kind", "description", "qty", "unit", "est_rate")} for _, r in items]},
                           text(h.get("requested_by")) or user)
    st = text(h.get("status")).lower()
    if st and st != "draft":
        db.x("UPDATE purchase_requests SET status=?, approved_by=? WHERE id=?",
             (st, user if st in ("approved", "rejected", "ordered") else "", pid))


def quote_rows(db, items, user=""):
    from . import procurement
    h = items[0][1]
    pr = procurement.get(db, text(h.get("request")))
    by = {ln["description"]: ln["id"] for ln in pr["lines"]}
    rates = {}
    for _, r in items:
        d = text(r.get("description"))
        if d not in by:
            raise ValidationError(f"'{d}' is not a line of request {pr['code']}")
        rates[by[d]] = r.get("rate")
    qid = procurement.save_quote(db, pr["id"], {**h, "rates": rates})
    db.conn.executemany("UPDATE pr_quote_lines SET awarded=1 WHERE quote_id=? AND line_id=?",
                        [(qid, by[text(r.get("description"))]) for _, r in items if _yes(r.get("awarded"))])


def count_rows(db, items, user=""):
    from . import stocktake
    h = items[0][1]
    if db.one("SELECT 1 FROM stock_counts WHERE code=?", (text(h.get("code")),)):
        raise ValidationError(f"Count {h.get('code')} already exists")
    cid = stocktake.start(db, {"code": h.get("code"), "date": h.get("date"), "warehouse": h.get("warehouse"),
                               "counted_by": h.get("counted_by")}, user)
    stocktake.save_counts(db, cid, {"lines": [{"item": r.get("item"), "counted": r.get("counted"), "note": r.get("note")} for _, r in items]})
    if _yes(h.get("post")):
        stocktake.post(db, cid, user)


def equipment_row(db, r, user=""):
    from . import equipment
    ex = db.one("SELECT id FROM equipment WHERE code=?", (text(r.get("code")),))
    equipment.save(db, r, ex["id"] if ex else None)


def plan_row(db, r, user=""):
    from . import equipment
    e = equipment.get(db, text(r.get("equipment")))
    ex = db.one("SELECT id FROM maintenance_plans WHERE equipment_id=? AND title=?", (e["id"], text(r.get("title"))))
    equipment.save_plan(db, {**r, "equipment_id": e["id"]}, ex["id"] if ex else None)


def usage_row(db, r, user=""):
    from . import equipment
    equipment.log_usage(db, {**r, "equipment_id": equipment.get(db, text(r.get("equipment")))["id"]}, user)


def maint_row(db, r, user=""):
    from . import equipment
    e = equipment.get(db, text(r.get("equipment")))
    plan = db.one("SELECT id FROM maintenance_plans WHERE equipment_id=? AND title=?", (e["id"], text(r.get("plan")))) if r.get("plan") else None
    equipment.log_maintenance(db, {**r, "equipment_id": e["id"], "plan_id": plan["id"] if plan else None,
                                   "on_credit": _yes(r.get("on_credit"))}, user)


def attendance_rows(db, items, user=""):
    from . import labour
    labour.save_day(db, {"date": items[0][1].get("date"), "rows": [r for _, r in items]}, user)


def receipt_rows(db, items, user=""):
    from . import ereceipt
    h = items[0][1]
    if db.one("SELECT 1 FROM ereceipts WHERE number=?", (text(h.get("number")),)):
        raise ValidationError(f"Receipt {h.get('number')} already exists")
    rid = ereceipt.save(db, {**h, "lines": [{k: r.get(k) for k in ("description", "code", "unit", "qty", "price", "discount", "vat_code")}
                                            for _, r in items]})
    if _yes(h.get("issue")):
        ereceipt.issue(db, rid, user)


ROW_IMPORTERS = [("Employees", employee_row), ("Loans", loan), ("Loan Transactions", loan_tx), ("Cash Custody", custody_row),
                 ("Rental Equipment", rent_item), ("Rental Stock Lots", rent_lot), ("Recurring Entries", recurring_row),
                 ("Budgets", budget_row), ("Service Plans", service_plan), ("Service Subscriptions", service_sub),
                 ("Project Phases", phase_row), ("Project Tasks", task_row), ("Variation Orders", vo_row), ("Subcontract Payments", sub_payment),
                 ("Equipment", equipment_row), ("Maintenance Plans", plan_row), ("Equipment Usage", usage_row), ("Maintenance Log", maint_row)]
GROUP_IMPORTERS = [("Payroll Settings", lambda r: "all", payroll_settings_rows),
                   ("Payroll Run", lambda r: str(r.get("date"))[:7], payroll_run_rows),
                   ("Custody Expenses", lambda r: (str(r.get("custody")), str(r.get("report") or r.get("date"))), custody_reports),
                   ("Rental Contracts", lambda r: str(r.get("ref")), rent_contract),
                   ("Sales Contracts", lambda r: str(r.get("code")), sale_contract),
                   ("Purchase Orders", lambda r: str(r.get("code")), purchase_order),
                   ("Bank Statement Lines", lambda r: (str(r.get("account")), str(r.get("statement") or "")), bank_statement),
                   ("Contracts", lambda r: str(r.get("code")), contract_rows),
                   ("Subcontractor Certificates", lambda r: (str(r.get("contract")), str(r.get("number"))), certificate_rows),
                   ("Purchase Requests", lambda r: str(r.get("code")), request_rows),
                   ("Supplier Quotes", lambda r: (str(r.get("request")), str(r.get("supplier"))), quote_rows),
                   ("Stock Counts", lambda r: str(r.get("code")), count_rows),
                   ("Attendance", lambda r: str(r.get("date"))[:10], attendance_rows),
                   ("E-Receipts", lambda r: str(r.get("number")), receipt_rows)]
ORDER = ["Payroll Settings", "Employees", "Payroll Run", "Loans", "Loan Transactions", "Cash Custody", "Custody Expenses", "Rental Equipment", "Rental Stock Lots",
         "Rental Contracts", "Sales Contracts", "Recurring Entries", "Bank Statement Lines", "Purchase Orders", "Budgets", "Service Plans", "Service Subscriptions",
         "Project Phases", "Project Tasks", "Contracts", "Variation Orders", "Subcontractor Certificates", "Subcontract Payments",
         "Purchase Requests", "Supplier Quotes", "Stock Counts", "Equipment", "Maintenance Plans", "Equipment Usage", "Maintenance Log",
         "Attendance", "E-Receipts"]
