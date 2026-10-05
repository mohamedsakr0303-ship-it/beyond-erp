"""The Arabic workbook: the same file, right-to-left, with Arabic sheet names, headers and labels.

`localize(wb)` runs on a finished workbook (before it is saved):
  - every sheet gets its Arabic name, and every reference to it is rewritten — cell formulas, hyperlinks, defined
    names, data validations, conditional formats and chart series — so the formulas keep working;
  - data-sheet headers take their Arabic labels (the importer reads English, Arabic or "English / عربي" headers, and
    Arabic sheet names, so this file imports back into the app like the English one);
  - labels on the reports and tools are translated (the app's own Arabic texts, plus the workbook's terms below);
  - every sheet reads right-to-left.
Codes in drop-downs (Cash, Posted, monthly, Yes…) stay as they are: the formulas and the importer rely on them."""
import re

from .interchange import SHEETS

REPORT_AR = {
    "Cover": "الغلاف", "How to Use": "دليل الاستخدام", "Partner Pack": "تقرير الشركاء", "Dashboard": "لوحة المتابعة", "Trial Balance": "ميزان المراجعة",
    "Income Statement": "قائمة الدخل", "Balance Sheet": "الميزانية العمومية", "Cash Flow": "التدفقات النقدية",
    "BU Profitability": "ربحية المشروعات", "Payables": "الدائنون", "Receivables": "المدينون", "Partner Equity": "حقوق الشركاء",
    "Tax Summary": "ملخص الضرائب", "Model Audit": "فحص سلامة الدفاتر", "Budget vs Actual": "الموازنة مقابل الفعلي",
    "Quick Estimator": "التقدير السريع", "Scaffold Calculator": "حاسبة السقالات الهندسية", "Crew Pay Calculator": "حاسبة أجور الأطقم",
    "Funder Account": "حساب الممول", "Building Calculator": "حاسبة المباني", "Lists": "Lists",
}

# the workbook's own terms (the app's texts cover the rest)
TERMS = {
    # cover
    "Accounting & Project Control System": "نظام الحسابات ومتابعة المشروعات", "START HERE": "ابدأ هنا", "EVERY DAY": "العمل اليومي",
    "THE ACCOUNTS": "الحسابات", "PEOPLE & PAYROLL": "الموظفون والرواتب", "PROJECTS & PRICING": "المشروعات والتسعير",
    "MORE MODULES": "أقسام أخرى", "CHECKS": "المراجعة", "THIS COMPANY": "هذه الشركة", "Books integrity": "سلامة الدفاتر",
    "Any entry as balanced debit and credit lines": "أي قيد بسطور مدين ودائن متوازنة",
    "Project costs: materials, subcontractors, site labour": "تكاليف المشروعات: خامات ومقاولو باطن وعمالة",
    "Office and company overhead": "مصروفات المكتب والشركة", "Quick wage entries (one line per worker)": "قيود أجور سريعة (سطر لكل عامل)",
    "Money received from clients": "المقبوضات من العملاء", "Payments to suppliers and staff": "المدفوعات للموردين والعاملين",
    "One row per invoice — header and terms": "سطر لكل فاتورة / مستخلص — البيانات والشروط", "The lines of each invoice": "بنود كل فاتورة",
    "Cash held by staff (custody)": "عهد نقدية لدى العاملين", "Cash given to staff (custody)": "العهد النقدية لدى العاملين", "What the custody was spent on": "تسويات العهد",
    "Capital in, drawings and distributions": "رأس المال والمسحوبات والتوزيعات", "What each client owes, less advances": "المستحق على كل عميل بعد الدفعات المقدمة",
    "What you owe each supplier and worker": "المستحق لكل مورد وعامل", "The account list and how each maps to the statements": "دليل الحسابات وارتباطه بالقوائم",
    "Every account: opening, period debits and credits, closing": "كل حساب: أول المدة وحركة المدة والرصيد الختامي",
    "Net sales → gross profit → EBITDA → net profit": "صافي المبيعات ← مجمل الربح ← الربح التشغيلي ← صافي الربح",
    "What the business owns and owes": "ما تملكه الشركة وما عليها", "Where the cash actually went": "أين ذهبت النقدية فعلاً",
    "Each project's own result, after shared overhead": "نتيجة كل مشروع بعد المصروفات المشتركة",
    "Profit share, reserve, drawings and balances": "حصص الأرباح والاحتياطي والمسحوبات والأرصدة",
    "The budget for the period against the books": "موازنة الفترة مقابل الدفاتر", "VAT and withholding balances": "أرصدة القيمة المضافة والخصم والتحصيل",
    "VAT / withholding rates": "نسب الضرائب", "Projects: client, billing basis and fee": "المشروعات: العميل وأساس الفوترة والأتعاب",
    "Clients, suppliers, staff": "العملاء والموردون والعاملون", "Owners and their default share": "الشركاء وحصصهم",
    "Each partner's share per project": "حصة كل شريك في كل مشروع", "Bills of quantities": "المقايسات", "Your priced item library": "قائمة الأسعار",
    "A fast budget estimate from the price list": "تقدير سريع من قائمة الأسعار",
    "The partners' management accounts: billed profit, work in progress, statements, each partner's equity":
        "حسابات الشركاء الإدارية: الربح المفوتر والأعمال تحت التنفيذ والقوائم وحقوق كل شريك",
    "Live position: cash, receivables, payables, profit, margins, projects, months": "الموقف الحالي: النقدية والمدينون والدائنون والأرباح والمشروعات والشهور",
    "Company details and the report period (report_from / report_to)": "بيانات الشركة وفترة التقارير (report_from / report_to)",
    "Staff and workers: pay type, allowances, insurance, tax, cost type and project": "الموظفون والعمال: طريقة الأجر والبدلات والتأمينات والضرائب ومركز التكلفة",
    "Monthly payroll: absence, overtime, bonuses, penalties, advances → insurance, tax, net": "المسير الشهري: الغياب والإضافي والمكافآت والجزاءات والسلف ← التأمينات والضريبة والصافي",
    "Insurance rates and limits, tax bands, overtime rates, the pay items": "نسب وحدود التأمينات وشرائح الضريبة والإضافي وبنود الأجر",
    "Measured-work pay (frames × width × height m² × rate) — a calculator only": "أجور الأعمال المقاسة (شبابيك × عرض × ارتفاع م² × سعر) — حاسبة فقط",
    "Equipment you rent out (scaffolding pieces, elevators, cradles) with optional opening stock": "المعدات المؤجرة (قطع السقالات والمصاعد والسقالات المعلقة) ورصيدها",
    "More pieces bought (booked to the rental fleet) or already owned (opening)": "دفعات القطع المشتراة أو المملوكة سابقاً",
    "Rental offers / contracts": "عروض وعقود التأجير", "Loans the company took or gave": "القروض المأخوذة والممنوحة",
    "Repayments, interest, fees and further drawdowns on loans (Loans sheet or already in the app)": "السداد والفوائد والمسحوبات الإضافية على القروض",
    "Pieces, weight, rent and installation for a façade": "القطع والوزن والإيجار والتركيب لواجهة",
    "Every integrity check, live — must read ALL OK": "كل فحوصات السلامة — يجب أن تكون سليمة",
    # dashboard
    "RESULTS FOR THE PERIOD": "نتائج الفترة", "WHERE THE MONEY WENT  (period)": "أين ذهبت الأموال (الفترة)",
    "PROJECTS / BUSINESS UNITS  (period results, after shared overhead)": "المشروعات / وحدات الأعمال (نتائج الفترة بعد المصروفات المشتركة)",
    "CLIENTS OWE US": "مستحق على العملاء", "WE OWE SUPPLIERS": "مستحق للموردين", "CLIENT ADVANCES HELD": "دفعات مقدمة من العملاء",
    "WORK DONE, NOT INVOICED": "أعمال منفذة لم تُفوتر", "STOCK & GOODS": "المخزون", "CASH & BANK": "النقدية والبنوك",
    "NET SALES": "صافي المبيعات", "GROSS PROFIT": "مجمل الربح", "NET PROFIT": "صافي الربح", "Contract": "العقد", "Client": "العميل", "Site": "الموقع",
    "Delivered / start": "التسليم / البداية", "Rent / month": "الإيجار الشهري", "Days on hire": "أيام التأجير", "Rent to date": "الإيجار حتى اليوم",
    "One-time charges": "مبالغ لمرة واحدة", "Invoiced": "المفوتر", "Collected": "المحصل", "Client owes": "مستحق على العميل",
    "Still to invoice": "لم يُفوتر بعد", "ONGOING RENTALS": "الإيجارات الجارية", "TOTAL": "الإجمالي",
    "Materials": "خامات", "Subcontractor Costs": "مقاولو الباطن", "Direct Labour (billable)": "عمالة مباشرة", "Site Preparation": "تجهيز الموقع",
    "Transport & Hauling": "نقل وتشوين", "Design Outsourcing & Renders": "تصميم خارجي", "Staff (office)": "رواتب إدارية", "Marketing": "تسويق",
    "Overhead": "مصروفات عامة", "Other operating": "مصروفات تشغيلية أخرى", "Interest & non-operating": "فوائد وغير تشغيلي",
    # statements
    "Gross sales (operating revenue)": "إجمالي المبيعات (الإيرادات التشغيلية)", "Less: sales discounts & allowances": "يخصم: خصومات ومسموحات المبيعات",
    "% of net sales": "% من صافي المبيعات", "General & administrative expenses": "مصروفات عمومية وإدارية", "Marketing & advertising expenses": "مصروفات تسويق وإعلان",
    "Utilities (electricity / telephone)": "مرافق (كهرباء / هاتف)", "Amortization": "الاستهلاك", "EBIT / Operating profit": "الربح التشغيلي",
    "Interest expense": "مصروفات الفوائد", "Other income": "إيرادات أخرى", "Other non-operating expenses": "مصروفات غير تشغيلية أخرى",
    "EBT / Profit before tax": "الربح قبل الضرائب", "Net profit / (loss)": "صافي الربح / (الخسارة)",
    "Accounts receivable (net)": "العملاء (صافي)", "Property & equipment (cost)": "الأصول الثابتة (بالتكلفة)", "Less: accumulated amortization": "يخصم: مجمع الاستهلاك",
    "LIABILITIES": "الالتزامات", "Accrued liabilities & payroll": "مصروفات وأجور مستحقة", "Deferred revenue (client advances)": "إيرادات مؤجلة (دفعات مقدمة)",
    "Partner capital": "رأس مال الشركاء", "Less: partner drawings": "يخصم: مسحوبات الشركاء", "Current earnings (profit to date)": "أرباح الفترة الحالية",
    "TOTAL EQUITY": "إجمالي حقوق الملكية", "TOTAL LIABILITIES & EQUITY": "إجمالي الالتزامات وحقوق الملكية", "Check: assets − (liabilities + equity)": "مراجعة: الأصول − (الالتزامات + حقوق الملكية)",
    "CASH FLOWS FROM OPERATING ACTIVITIES": "التدفقات النقدية من الأنشطة التشغيلية", "CASH FLOWS FROM INVESTING ACTIVITIES": "التدفقات النقدية من الأنشطة الاستثمارية",
    "CASH FLOWS FROM FINANCING ACTIVITIES": "التدفقات النقدية من الأنشطة التمويلية", "Net income": "صافي الربح", "(+) Depreciation": "(+) الإهلاك",
    "(+) Amortization": "(+) الاستهلاك", "(+/-) Accounts receivable": "(+/-) العملاء", "(+/-) Inventory": "(+/-) المخزون",
    "(+/-) Prepaid expenses & advances": "(+/-) مصروفات مقدمة ودفعات", "(+/-) Other current assets": "(+/-) أصول متداولة أخرى",
    "(+/-) Accounts payable": "(+/-) الموردون", "(+/-) Accrued liabilities": "(+/-) مصروفات مستحقة", "(+/-) Deferred revenue": "(+/-) إيرادات مؤجلة",
    "(+/-) Taxes payable": "(+/-) ضرائب مستحقة", "(+/-) Other current liabilities": "(+/-) التزامات متداولة أخرى",
    "Net cash provided by operating activities": "صافي النقدية من الأنشطة التشغيلية", "(-) Capital expenditures": "(-) شراء أصول ثابتة",
    "(-) Additions to intangibles": "(-) أصول غير ملموسة", "(-) Investments bought / (sold)": "(-) استثمارات", "(-) Other long-term assets": "(-) أصول طويلة الأجل أخرى",
    "Net cash used in investing activities": "صافي النقدية في الأنشطة الاستثمارية", "(+) Capital contributed": "(+) رأس مال مدفوع",
    "(-) Dividends / partner drawings paid": "(-) توزيعات / مسحوبات الشركاء", "(+/-) Partners' current accounts": "(+/-) جاري الشركاء",
    "(+/-) Short-term debt": "(+/-) قروض قصيرة الأجل", "(+/-) Long-term debt": "(+/-) قروض طويلة الأجل", "(+/-) Other long-term liabilities": "(+/-) التزامات طويلة الأجل أخرى",
    "(+/-) Other equity & reserves": "(+/-) حقوق ملكية واحتياطيات أخرى", "(+/-) Retained earnings adjustments": "(+/-) تسويات أرباح محتجزة",
    "Net cash provided by financing activities": "صافي النقدية من الأنشطة التمويلية", "(+/-) Change in cash & cash equivalents": "(+/-) التغير في النقدية",
    "Beginning cash": "النقدية أول المدة", "Ending cash": "النقدية آخر المدة", "Check: ending cash − cash per trial balance": "مراجعة: النقدية آخر المدة − نقدية الميزان",
    "Opening net (Dr−Cr)": "صافي أول المدة", "Period debit": "مدين الفترة", "Period credit": "دائن الفترة", "Closing debit": "مدين ختامي",
    "Closing credit": "دائن ختامي", "Closing net (Dr−Cr)": "الصافي الختامي",
    "Overhead %": "نسبة المصروفات المشتركة", "Shared overhead pool (no business unit, all expense lines)": "وعاء المصروفات المشتركة",
    "Company-level result (net profit − Σ unit profits)": "نتيجة الشركة (صافي الربح − أرباح الوحدات)", "Default share": "الحصة الافتراضية",
    "Total owed": "إجمالي المستحق", "Payables (2010…)": "الموردون", "Accrued / payroll": "مستحقات / أجور", "Net owed to us": "صافي المستحق لنا",
    "Receivable (invoices, installments)": "مستحق (فواتير وأقساط)", "Advances & billed not earned": "دفعات مقدمة",
    "Every name on the Parties sheet is listed here.": "كل اسم في ورقة الجهات يظهر هنا.",
    "VALUE ADDED TAX": "ضريبة القيمة المضافة", "WITHHOLDING TAX": "ضريبة الخصم والتحصيل", "Output VAT charged on sales (period)": "ضريبة المخرجات على المبيعات",
    "Input VAT on purchases (period)": "ضريبة المدخلات على المشتريات", "Net VAT payable / (refundable) for the period": "صافي الضريبة المستحقة / (المستردة)",
    "Deducted from suppliers in the period (to remit)": "المخصوم من الموردين (للتوريد)", "Deducted by clients in the period (tax credit)": "المخصوم بواسطة العملاء (رصيد دائن)",
    "Balance still owed to the Tax Authority": "الرصيد المستحق لمصلحة الضرائب", "Tax credit balance": "رصيد الضريبة الدائن",
    "All checks": "كل الفحوصات", "Value A": "القيمة أ", "Value B": "القيمة ب",
    "Trial balance: closing debits = closing credits": "الميزان: المدين الختامي = الدائن الختامي",
    "Balance sheet: assets = liabilities + equity": "الميزانية: الأصول = الالتزامات + حقوق الملكية",
    "Cash flow: ending cash = cash per trial balance": "التدفقات: النقدية آخر المدة = نقدية الميزان",
    "Journal vouchers: total debits = total credits": "القيود: المدين = الدائن",
    "Every posting row resolves to a valid account (unknown codes)": "كل سطر مرحل على حساب صحيح",
    "Overhead allocation % across business units ≤ 100%": "مجموع نسب توزيع المصروفات ≤ 100%",
    "Budget (period)": "الموازنة (الفترة)", "Actual (period)": "الفعلي (الفترة)", "Variance (+ good)": "الانحراف (+ جيد)", "Used %": "نسبة الاستخدام",
    "Full-year budget": "موازنة السنة", "Totals": "الإجماليات",
    # partner pack
    "1.  AT A GLANCE": "١. نظرة سريعة", "2.  INCOME STATEMENT — earned and cash side by side": "٢. قائمة الدخل — الاستحقاق والنقدي",
    "3.  BALANCE SHEET — what the company owns and owes": "٣. الميزانية — ما تملكه الشركة وما عليها", "4.  CASH FLOW — where the cash actually went": "٤. التدفقات النقدية",
    "5.  STATEMENT OF PARTNERS' EQUITY": "٥. حقوق الشركاء", "Billed profit": "الربح المفوتر", "Basis": "أساس الفوترة", "Capital put in": "رأس المال المدفوع",
    "Distributions": "التوزيعات", "EBITDA": "الربح التشغيلي قبل الإهلاك", "Reserve %": "نسبة الاحتياطي", "Profit share before reserve": "الحصة قبل الاحتياطي",
    "Work in progress — value of work done, not yet invoiced": "أعمال تحت التنفيذ — منفذة ولم تُفوتر",
    "Prepared by: ______________________      Reviewed by (partner): ______________________": "إعداد: ______________      مراجعة (الشريك): ______________",
    # calculators
    "Scaffolding calculator — per building": "حاسبة السقالات — لكل مبنى", "Building 1": "عمارة 1", "Building 3": "عمارة 3", "Any building": "أي مبنى",
    "Any building — try a quote": "أي مبنى — جرّب عرض سعر", "Building 3 — installed (1st batch)": "عمارة 3 — مركبة (الدفعة الأولى)",
    "Building 1 — 2nd batch": "عمارة 1 — الدفعة الثانية", "Owned (books)": "المملوك (الدفاتر)", "+ Planned purchase": "+ شراء مخطط", "Stock total": "إجمالي المخزون",
    "Avg purchase cost": "متوسط تكلفة الشراء", "Contract rent / day": "إيجار العقد / يوم", "Your rent / month": "إيجارك / شهر", "Rent / month used": "الإيجار المستخدم / شهر",
    "Rent / month as % of cost": "الإيجار % من التكلفة", "Months to pay back a piece": "شهور استرداد القطعة", "Loss / damage price": "سعر الفقد / التلف",
    "Handed over in building 3": "المسلم في عمارة 3", "Per 100 m² (building 3 actual)": "لكل 100 م² (فعلي عمارة 3)", "Your per 100 m²": "معدلك لكل 100 م²",
    "Per 100 m² used": "المعدل المستخدم", "m² the free stock covers (after buildings 3 & 1)": "م² يغطيها المخزون المتاح", "Code": "الكود", "Piece": "القطعة",
    "Building 3 — pieces (handover)": "عمارة 3 — القطع (المسلمة)", "Building 1 — pieces (handover, type)": "عمارة 1 — القطع (المسلمة)",
    "Building 1 — short": "عمارة 1 — عجز", "Any — pieces (per 100 m²)": "أي مبنى — القطع", "Any — short": "أي مبنى — عجز",
    "Our cost — installation": "تكلفتنا — التركيب", "Our cost — dismantling": "تكلفتنا — الفك", "Our cost — delivery": "تكلفتنا — النقل",
    "Our cost — inspections": "تكلفتنا — الفحص", "Rent per m² per month": "الإيجار لكل م² شهرياً", "Months of rent to recover the pieces": "شهور الإيجار لاسترداد القطع",
    "First month's invoice (rent + installation & dismantling + delivery)": "فاتورة الشهر الأول (إيجار + تركيب وفك + نقل)",
    "Pieces handed over = 573 (handover & inspection form)": "القطع المسلمة = 573 (محضر التسليم)",
    "Rent per month at per-piece rates = 22,864 (accounts program, 30 days)": "الإيجار بسعر القطعة = 22,864 (برنامج الحسابات)",
    "Loss / damage value — form prices × pieces": "قيمة الفقد — أسعار المحضر × القطع", "Building 3 rent a month = 22,650 (extract 1)": "إيجار عمارة 3 شهرياً = 22,650 (المستخلص 1)",
    "Building 3 extract 1 = 114,200": "مستخلص عمارة 3 رقم 1 = 114,200", "Building 1 extract 1 = 87,170.60": "مستخلص عمارة 1 رقم 1 = 87,170.60",
    "Days in a rent month": "أيام شهر الإيجار", "Loss / damage price = cost + (handover form)": "سعر الفقد = التكلفة + (محضر التسليم)",
    "Rent as % of purchase cost per month (optional — blank = contract rates)": "الإيجار % من التكلفة شهرياً (اختياري)",
    "Target payback (months) — rent = average cost ÷ months (optional)": "شهور الاسترداد المستهدفة (اختياري)", "Largest facade the free stock can cover:": "أكبر واجهة يغطيها المخزون المتاح:",
    "on rent (from stock)": "مؤجر (من المخزون)", "↑ total = value of the fleet at cost": "↑ قيمة المعدات بالتكلفة", "↑ rent / month if every piece were out": "↑ الإيجار لو أُجرت كل القطع",
    "Actually paid": "المدفوع فعلاً", "Building / work": "المبنى / العمل", "Count": "العدد", "Crew / worker (type a name)": "الطقم / العامل",
    "Still to pay (calculated − paid)": "المتبقي (المحسوب − المدفوع)", "Frame scaffold — installation": "تركيب سقالات الشبابيك", "Frame scaffold — dismantling": "فك سقالات الشبابيك",
    "Date": "التاريخ", "Crew / worker": "الطقم / العامل", "Item": "البند", "Unit": "الوحدة", "Width / length": "العرض / الطول", "Height / width": "الارتفاع / العرض",
    "Depth": "العمق", "Quantity": "الكمية", "Rate": "السعر", "Calculated": "المحسوب", "Difference": "الفرق", "Notes": "ملاحظات",
    # funder
    "Funder account — Abdelrahman Elshorbgy": "حساب الممول — م. عبد الرحمن الشوربجي", "AT A GLANCE": "نظرة سريعة", "Funder / partner": "الممول / الشريك",
    "Loan code": "كود القرض", "Repaid from each collection": "يُرد من كل تحصيل", "Put into the company": "إجمالي ما موّله", "Paid back to him": "ما رُدّ إليه",
    "Company still owes him": "الرصيد المستحق له", "Client extracts collected": "المحصل من المستخلصات", "Due to him at the rule": "المستحق له حسب النسبة",
    "Paid vs rule (0 = on track)": "الفرق عن القاعدة", "Collections still needed to clear": "تحصيلات مطلوبة للسداد", "Profit distributions to him (as partner)": "توزيعات أرباح له (كشريك)",
    "Movement": "الحركة", "Method": "الطريقة", "Description": "البيان", "In from him (+)": "وارد منه (+)", "Paid back to him (−)": "مردود له (−)",
    "Balance owed to him": "الرصيد المستحق له", "Extract collected that day": "تحصيل المستخلص في نفس اليوم", "Share of it": "النسبة منه", "Rule check": "مطابقة القاعدة",
    "Expected date": "التاريخ المتوقع", "Amount": "المبلغ", "Paid to supplier?": "سُدد للمورد؟", "Paid on": "تاريخ السداد", "Still to come": "المتبقي",
    "Equipment": "المعدات", "Cost (purchase lots)": "التكلفة (دفعات الشراء)", "Paid by the funder (loan)": "دفعها الممول (قرض)", "Paid by the company": "دفعتها الشركة",
    "How": "التفاصيل", "Batch 1 — building 3 (first batch)": "الدفعة الأولى — عمارة 3", "Batch 2 — building 1 (second batch)": "الدفعة الثانية — عمارة 1",
    "30 used fixed clamps": "30 كلبس ثابت مستعمل", "Type": "النوع",
    # scaffold calculator (engineering)
    "Input / Setting": "المدخل / الإعداد", "Value": "القيمة", "Calculated ": "المحسوب", "Summary": "الملخص", "Rent per month": "الإيجار الشهري",
    "Rent for the period": "إيجار المدة", "Pieces": "عدد القطع", "Total weight (kg)": "الوزن الإجمالي (كجم)", "Loss value of all pieces": "قيمة فقد كل القطع",
    "Scope": "نطاق الخدمة", "Monthly rent used": "الإيجار الشهري المستخدم", "Truck capacity": "حمولة الشاحنة", "Trips": "عدد النقلات", "Tons": "الأطنان",
    "Include?": "يشمل؟", "Price basis": "أساس السعر", "Client pays": "يدفع العميل", "Min. charge": "الحد الأدنى", "Unit cost": "تكلفة الوحدة", "Our cost": "تكلفتنا",
    "Profit": "الربح", "Margin": "الهامش", "Line rent / month": "إيجار السطر / شهر", "Loss value": "قيمة الفقد", "Your rate (optional)": "سعرك (اختياري)",
    "Weight kg (line)": "الوزن كجم", "Rental period": "مدة الإيجار",
}


def _ar_half(v):
    """'English / عربي' or 'English  |  عربي' → the Arabic half."""
    for sep in ("  |  ", " | ", " / "):
        if sep in v:
            parts = [p.strip() for p in v.split(sep)]
            ar = [p for p in parts if re.search("[\u0600-\u06FF]", p) and not re.search("[A-Za-z]{3}", p)]
            if ar:
                return " / ".join(ar)
    return None


def translations():
    """English → Arabic from the app's own texts (screens, reports, partner pack, payroll) and the terms above."""
    import os
    pairs = {}
    here = os.path.dirname(__file__)
    for f in ("static/app.js", "static/i18n.js"):
        try:
            with open(os.path.join(here, f), encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        for en, ar in re.findall(r'\["([^"\\]{2,160})",\s*"([^"\\]*[\u0600-\u06FF][^"\\]*)"\]', src) + \
                re.findall(r'L2\("([^"\\]{2,200})",\s*"([^"\\]*[\u0600-\u06FF][^"\\]*)"\)', src):
            pairs.setdefault(en.strip(), ar.strip())
    for f in ("partner_pack.py", "reports.py", "payroll.py"):
        try:
            with open(os.path.join(here, f), encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        for en, ar in re.findall(r'(?:T|L)\(\s*"([^"\\{]{2,200})",\s*"([^"\\]*[\u0600-\u06FF][^"\\]*)"\)', src):
            pairs.setdefault(en.strip(), ar.strip())
    for s in SHEETS:
        for c in s["cols"]:
            if re.search("[\u0600-\u06FF]", c["ar"]):
                pairs.setdefault(c["en"], c["ar"])
    pairs.update(TERMS)
    from .workbook_ar_terms import EXAMPLE_AR, MORE_AR
    pairs.update(MORE_AR)
    pairs.update(EXAMPLE_AR)
    return pairs


def sheet_names(extra=None):
    names = {s["name"]: s["ar"] for s in SHEETS}
    names.update(REPORT_AR)
    names.update(extra or {})
    return {k: v for k, v in names.items() if k != v}


def _ref_rx(names):
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    plain = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True) if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", n))
    rx = rf"'(?P<q>{alts})'!"
    if plain:
        rx += rf"|(?<![A-Za-z0-9_.'\u0600-\u06FF])(?P<u>{plain})!"
    return re.compile(rx)


# values formulas compare against — kept as they are wherever they are typed
CODES = {"Yes", "No", "both", "install", "dismantle", "none", "Posted", "Draft", "OK", "ALL OK"}


def _literals(f):
    """Words inside a formula that show in the cell ("Management accounts as at " …) — codes are never touched."""
    from .workbook_ar_terms import LITERALS_AR
    return re.sub(r'"([^"]*)"', lambda m: '"' + LITERALS_AR.get(m.group(1), m.group(1)) + '"', f)


def localize(wb, extra_names=None):
    from .workbook_ar_terms import TITLES_AR, helper
    names = sheet_names(extra_names)
    rx = _ref_rx(names)
    fix = lambda f: rx.sub(lambda m: f"'{names[m.group('q') or m.group('u')]}'!", f) if isinstance(f, str) else f  # noqa: E731
    tr = translations()
    trl = {k.lower(): v for k, v in tr.items()}
    by_name = {s["name"]: s for s in SHEETS}
    for ws in wb.worksheets:
        data_sheet = by_name.get(ws.title)
        heads = {c["en"]: c["ar"] for c in data_sheet["cols"] if c.get("kind") != "calc"} if data_sheet else {}
        calc_ar = {c["en"]: c["ar"] for c in data_sheet["cols"] if c.get("kind") == "calc"} if data_sheet else {}
        # cells under a drop-down hold codes that formulas compare against ("both", "Yes", "monthly") — never translate them
        coded = {cell for dv in ws.data_validations.dataValidation if dv.type == "list"
                 for rg in dv.sqref.ranges for row in ws.iter_rows(min_row=rg.min_row, max_row=min(rg.max_row, ws.max_row),
                                                                     min_col=rg.min_col, max_col=rg.max_col) for cell in row}
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, str) and (c in coded or v in CODES) and not v.startswith("="):
                    pass
                elif isinstance(v, str):
                    if v.startswith("="):
                        c.value = _literals(fix(v))
                    elif data_sheet and c.row <= 5 and v in heads and re.search("[\u0600-\u06FF]", heads[v]):
                        c.value = heads[v]
                    elif data_sheet and c.row == 2 and ws.title in TITLES_AR:
                        c.value = TITLES_AR[ws.title]
                    elif data_sheet and c.row == 4 and v in calc_ar and not helper(v):
                        c.value = calc_ar[v]
                    elif data_sheet and c.row == 4 and helper(v):
                        h = helper(v)       # never the same heading as a column the importer reads
                        c.value = h if h not in set(heads.values()) else f"{h} (محسوب)"
                    elif data_sheet and c.row == 3 and v.startswith("e.g. "):
                        rest = v[5:]
                        c.value = "مثال: " + (tr.get(rest) or rest)
                    elif not data_sheet or c.row <= 3:
                        k = v.strip()
                        t = tr.get(k) or trl.get(k.lower()) or (_ar_half(k) if re.search("[\u0600-\u06FF]", k) and re.search("[A-Za-z]", k) else None)
                        if not t and k in names:
                            t = names[k]
                        if not t:
                            m = re.fullmatch(r"(.+?) (opening|period Dr|period Cr)", k)
                            if m and m.group(1) in names:
                                t = f"{names[m.group(1)]} — " + {"opening": "أول المدة", "period Dr": "مدين الفترة", "period Cr": "دائن الفترة"}[m.group(2)]
                        if not t and "|" in k:     # 'COMPANY | SHEET NAME' titles
                            a, b = [x.strip() for x in k.rsplit("|", 1)]
                            hit = next((n for n in names if n.upper() == b.upper()), None) or next((n for n in tr if n.upper() == b.upper()), None)
                            if hit:
                                t = f"{a}  |  {names.get(hit) or tr[hit]}"
                        if t:
                            c.value = t
                if isinstance(c.value, str) and c.value.startswith("#'"):
                    c.value = "#" + fix(c.value[1:])
                if c.hyperlink is not None:
                    if c.hyperlink.location:
                        c.hyperlink.location = fix(c.hyperlink.location)
                    if c.hyperlink.target and c.hyperlink.target.startswith("#"):
                        c.hyperlink.target = "#" + fix(c.hyperlink.target[1:])
        for dv in ws.data_validations.dataValidation:
            dv.formula1, dv.formula2 = fix(dv.formula1), fix(dv.formula2)
        for cf in ws.conditional_formatting:
            for rule in cf.rules:
                rule.formula = [fix(f) for f in (rule.formula or [])]
        for ch in getattr(ws, "_charts", []):
            for s in ch.series:
                for part in (s.val, s.cat, s.xVal, s.yVal):
                    for refname in ("numRef", "strRef"):
                        ref = getattr(part, refname, None) if part is not None else None
                        if ref is not None and ref.f:
                            ref.f = fix(ref.f)
                if s.tx is not None and s.tx.strRef is not None and s.tx.strRef.f:
                    s.tx.strRef.f = fix(s.tx.strRef.f)
        if data_sheet:      # row 4: every heading unique, and no grey helper may look like a column the importer reads
            from .interchange import input_cols
            n_in = len(input_cols(data_sheet))
            inputs = {c.value for c in ws[4][:n_in] if c.value}
            seen = set()
            for c in ws[4]:
                v = c.value
                if not isinstance(v, str) or not v:
                    continue
                col = c.column <= n_in
                if not col or v in seen:
                    n, base = 2, v
                    while v in seen or (not col and v in inputs):
                        v = f"{base} ({n})" if n > 2 or base not in inputs else f"{base} (محسوب)"
                        n += 1
                    c.value = v
                seen.add(v)
        ws.sheet_view.rightToLeft = True
    try:
        dn = list(wb.defined_names.values())
    except AttributeError:
        dn = list(wb.defined_names.definedName)
    for d in dn:
        d.attr_text = fix(d.attr_text)
    for ws in wb.worksheets:
        if ws.title in names:
            ws.title = names[ws.title]
    return names
