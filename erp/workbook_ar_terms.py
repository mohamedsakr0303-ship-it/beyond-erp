"""Arabic texts for the Arabic workbook: sheet descriptions, helper-column headers, notes and the words used inside
formulas that show on screen. The English workbook never uses these; the Arabic one never shows the English."""

# the description under each data sheet's title (row 2), by sheet name
TITLES_AR = {
    "Settings": "إعدادات الشركة وفترة التقارير",
    "Chart of Accounts": "دليل الحسابات — عدّل الأكواد والأسماء بحرية؛ الدور (Role) هو وسم النظام (لا تكرره)",
    "Journal Vouchers": "قيود اليومية — أرصدة أول المدة والإهلاك والاستهلاك والضرائب والقروض والمستحقات. الصفوف التي تحمل نفس رقم القيد تكوّن قيداً واحداً متوازناً",
    "Business Units": "وحدات الأعمال (مراكز التكلفة / المشروعات). نسبة التحميل = حصة يدوية من التكاليف غير المباشرة المشتركة",
    "Sales Invoices": "فواتير المبيعات / المستخلصات. المبلغ = مجموع بنود الفاتورة",
    "Invoice Lines": "بنود كل فاتورة مبيعات",
    "Receipt Vouchers": "المبالغ المحصلة من العملاء. الحساب الدائن: إيراد (أساس نقدي) أو مدينون (مقابل فاتورة) أو دفعات مقدمة من العملاء",
    "Payment Vouchers": "سداد المستحق للموردين ومقاولي الباطن والعمال",
    "Direct Costs": "التكاليف المباشرة (تكلفة الإيراد) لكل وحدة عمل — خامات ومقاولو باطن وأعمال موقع",
    "Operating Expenses": "المصروفات التشغيلية. اترك وحدة العمل فارغة للمصروفات المشتركة (تُوزع بنسبة التحميل). المدفوع فارغ = مدفوع بالكامل",
    "Payroll": "استحقاقات الأجور — ما استحقه كل عامل (وما دُفع الآن)",
    "Partner Transactions": "مساهمات رأس المال ومسحوبات / توزيعات الشركاء",
    "Parties": "العملاء والموردون — رقم التسجيل الضريبي والعنوان مطلوبان للفاتورة الإلكترونية",
    "Partners": "الشركاء ونسبة أرباحهم الافتراضية",
    "Partner Shares": "نسبة الملكية لكل وحدة عمل (تتقدم على النسبة الافتراضية)",
    "Tax Codes": "أكواد ضريبة القيمة المضافة والخصم من المنبع مع نوع الضريبة في الفاتورة الإلكترونية. النسب قابلة للتعديل",
    "Price List": "قائمة أسعار الوحدات (التكلفة التاريخية × (1 + هامش الربح) = سعر العميل)",
    "BOQ Lines": "المقايسات — صف لكل بند؛ الصفوف بنفس عنوان المقايسة ووحدة العمل تكوّن مقايسة واحدة",
    "Fixed Assets": "سجل الأصول الثابتة (الشراء والإهلاك والاستبعاد قيود يومية بمرجع FA:<الكود>)",
    "Warehouses": "المخازن وحساب المخزون لكل منها",
    "Stock Items": "دليل الأصناف",
    "Products": "كتالوج المعرض — أصناف ووحدات (شقق، فيلات، سيارات…) وخدمات؛ الصور تضاف من البرنامج",
    "Stock Moves": "استلام بضاعة وصرف خامات لوحدات الأعمال وتحويلات وتسويات جرد (كمية التسوية = المعدود − الدفتري، وقد تكون سالبة)",
    "Sales Contracts": "مبيعات المنتجات / الوحدات بخطة السداد. الصفوف بنفس رقم البيع تكوّن عملية بيع واحدة (صف لكل بند). الصفوف هنا تنشئ سجلات جديدة بقيودها؛ التصدير الكامل يترك هذه الورقة فارغة",
    "Bank Statement Lines": "حركات كشف البنك. الصفوف بنفس الحساب واسم الكشف تكوّن كشفاً واحداً؛ ويُطابق مع سنداتك تلقائياً بعد الاستيراد",
    "Recurring Entries": "إيجار ورواتب واشتراكات… تُرحّل حسب جدول. يمكن استخدام {month} في النص",
    "Service Plans": "خطط الصيانة والضمان / التأمين ودعم البرامج والإضافات التي تبيعها شهرياً / ربع سنوياً / سنوياً",
    "Service Subscriptions": "عقد كل عميل. تبدأ الفوترة من تاريخ البداية (فوترة الفترات المستحقة من شاشة الاشتراكات). الصفوف هنا تنشئ سجلات جديدة بقيودها؛ التصدير الكامل يترك هذه الورقة فارغة",
    "Purchase Orders": "صف لكل بند أمر شراء؛ الصفوف بنفس كود الأمر تكوّن أمراً واحداً. معتمد = Yes يعتمده (لا يُسجل شيء حتى يتم الاستلام من شاشته)",
    "Budgets": "الموازنة الشهرية لكل حساب إيراد / تكلفة / مصروف (وحدة العمل فارغة = مستوى الشركة). املأ الشهور أو إجمالي السنة فقط ليوزع بالتساوي. إعادة استيراد صف تستبدل موازنة ذلك الحساب",
    "Loans": "القروض التي حصلت عليها الشركة أو منحتها. التسجيل: money = يُسجل السحب الآن؛ opening = موجود بالدفاتر ويُتابع رصيده المتبقي فقط. الصفوف هنا تنشئ سجلات جديدة بقيودها؛ التصدير الكامل يترك هذه الورقة فارغة",
    "Loan Transactions": "الأقساط والفوائد والرسوم والسحوبات الإضافية على القروض. الصفوف هنا تنشئ سجلات جديدة بقيودها؛ التصدير الكامل يترك هذه الورقة فارغة",
    "Cash Custody": "العهد النقدية المسلمة للعاملين. التسجيل: money = صُرفت الآن؛ opening = العهدة مع العامل بالفعل. الصفوف هنا تنشئ سجلات جديدة بقيودها؛ التصدير الكامل يترك هذه الورقة فارغة",
    "Custody Expenses": "تقارير المصروفات التي تسوي العهدة. الصفوف بنفس كود العهدة ورقم التقرير تكوّن تقريراً واحداً. الصفوف هنا تنشئ سجلات جديدة بقيودها؛ التصدير الكامل يترك هذه الورقة فارغة",
    "Rental Equipment": "أصناف معدات التأجير: الإيجار الشهري للقطعة والتكلفة المرجعية وسعر الفقد والعمر الإنتاجي",
    "Rental Stock Lots": "قطع إضافية مشتراة (تُسجل على أسطول التأجير) أو مملوكة من قبل (أول المدة). الصفوف هنا تنشئ سجلات جديدة بقيودها؛ التصدير الكامل يترك هذه الورقة فارغة",
    "Rental Contracts": "عروض / عقود التأجير. الصفوف بنفس رقم العقد تكوّن عقداً واحداً (صف لكل بند). تدخل كعروض أسعار؛ أمر الشراء والتوقيعات تنقلها للتنفيذ، وتاريخ التسليم يسجل خروج القطع",
    "Employees": "الموظفون والعمال (العقد). إعادة استيراد صف بنفس الكود تحدّث الموظف — لا شيء يتكرر",
    "Payroll Run": "صف لكل موظف لكل شهر. الشهري: اكتب أيام الغياب بدون أجر؛ اليومي / بالساعة / بالقطعة: الأيام أو الساعات أو الوحدات. الإجمالي والتأمينات والضريبة والصافي تُحسب (رمادي). الصفوف هنا تنشئ سجلات جديدة بقيودها؛ التصدير الكامل يترك هذه الورقة فارغة",
    "Payroll Settings": "قواعد الرواتب: التأمينات الاجتماعية وشرائح ضريبة كسب العمل وقاعدة الدخل المرتفع وصندوق الشهداء ومعاملات الإضافي",
}

# grey helper columns (row 4) that the sheet calculates
HELPERS_AR = {
    "revenue": "إيراد", "cost of sales": "تكلفة المبيعات", "expenses": "مصروفات", "operating expenses": "مصروفات تشغيلية",
    "payables": "دائنون", "accruals": "مستحقات", "receivables": "مدينون", "client advances": "دفعات مقدمة من العملاء",
    "work in progress": "أعمال تحت التنفيذ", "partner capital": "رأس مال الشريك", "partner drawings": "مسحوبات الشريك",
    "partner of the line": "شريك السطر", "cash moved": "حركة النقدية", "staff costs": "تكاليف العاملين", "G&A costs": "مصروفات عمومية وإدارية",
    "invoice collected": "تحصيل فاتورة", "cash in the voucher": "النقدية في القيد", "revenue in the voucher": "الإيراد في القيد",
    "costs in the voucher": "التكاليف في القيد", "revenue received in cash": "إيراد محصل نقداً", "share of costs paid in cash": "نسبة التكاليف المدفوعة نقداً",
    "cost of sales paid": "تكلفة مبيعات مدفوعة", "staff costs paid": "تكاليف عاملين مدفوعة", "G&A paid": "عمومية وإدارية مدفوعة",
    "other costs paid": "تكاليف أخرى مدفوعة", "collected from clients": "محصل من العملاء", "unknown account": "حساب غير معروف",
    "first row of the contract": "أول صف في العقد", "rent / month (line)": "الإيجار / شهر (البند)", "contract rent / month": "إيجار العقد / شهر",
    "one-time charge (line)": "رسوم لمرة واحدة (البند)", "one-time charges (contract)": "رسوم لمرة واحدة (العقد)",
    "cost": "التكلفة", "paid": "المدفوع", "owed": "المتبقي", "Date (start)": "التاريخ (البداية)", "booked amount": "المبلغ المسجل",
    "money account": "حساب النقدية", "loan account": "حساب القرض", "debit": "مدين", "credit": "دائن", "direction": "الاتجاه",
    "employee row": "صف الموظف", "pay type": "نوع الأجر", "business unit": "وحدة العمل", "cost type": "نوع التكلفة", "days / month": "أيام / شهر",
    "paid share of the month": "نسبة الشهر المدفوعة", "basic earned": "الأساسي المستحق", "allowance share": "نسبة البدلات",
    "hourly base": "أجر الساعة", "overtime": "الإضافي", "gross": "الإجمالي", "taxable earnings": "الاستحقاقات الخاضعة",
    "deductions before tax": "استقطاعات قبل الضريبة", "insurable base": "أساس التأمين", "insurable wage": "أجر الاشتراك",
    "insurance (employee)": "التأمينات (حصة العامل)", "insurance (employer)": "التأمينات (حصة صاحب العمل)", "taxed": "خاضع للضريبة",
    "annual taxable": "الوعاء السنوي", "first band": "أول شريحة", "salary tax": "ضريبة كسب العمل", "Martyrs Fund": "صندوق الشهداء",
    "advance recovered": "سلفة مستردة", "penalties & deductions": "جزاءات واستقطاعات", "net pay": "صافي الأجر", "salary cost": "تكلفة الأجر",
    "cost account": "حساب التكلفة", "employer insurance account": "حساب تأمينات صاحب العمل", "insurance payable": "تأمينات مستحقة",
    "paid (up to the net)": "المدفوع (حتى الصافي)", "owed to the employee": "المستحق للموظف", "Date (given)": "التاريخ (التسليم)",
    "net": "الصافي", "VAT": "ضريبة القيمة المضافة", "expense account": "حساب المصروف", "unit price": "سعر الوحدة", "line net": "صافي البند",
    "first row of the sale": "أول صف في البيع", "booked": "مسجل", "sale net": "صافي البيع", "sale VAT": "ضريبة البيع", "sale total": "إجمالي البيع",
    "revenue now": "الإيراد الآن", "months between": "الشهور بين الأقساط", "installment interest": "فائدة التقسيط", "invoiced": "المفوتر",
    "output VAT": "ضريبة المخرجات", "collected": "المحصل", "collected into": "حُصّل في", "interest earned": "فوائد مكتسبة",
    "stock item": "صنف المخزون", "inventory account": "حساب المخزون", "unit cost": "تكلفة الوحدة", "stock / units account": "حساب المخزون / الوحدات",
    "Date (first)": "التاريخ (الأول)", "counted until": "محسوب حتى", "times so far": "عدد المرات حتى الآن", "total so far": "الإجمالي حتى الآن",
    "price": "السعر", "every (months)": "كل (شهور)", "VAT code": "كود الضريبة", "revenue account": "حساب الإيراد", "months started": "الشهور المبدوءة",
    "invoices": "الفواتير", "billed": "المفوتر", "receivable": "المدين", "earned": "المكتسب", "billed, not yet earned": "مفوتر ولم يُكتسب بعد",
    "monthly value": "القيمة الشهرية", "next invoice": "الفاتورة التالية", "line value": "قيمة البند", "first row": "أول صف",
    "order total": "إجمالي الأمر", "received (direct costs)": "المستلم (تكاليف مباشرة)", "still on order": "متبقي على الأمر",
    "Hours worked": "ساعات العمل", "Line price": "سعر البند", "Line cost": "تكلفة البند", "Client name": "اسم العميل",
}
ROLE_AR = {"rental_fleet": "أسطول التأجير", "bank": "البنك", "ap": "الدائنين", "ar": "المدينين", "interest": "الفوائد",
           "interest_income": "إيراد الفوائد", "social_insurance_payable": "التأمينات المستحقة", "salary_tax_payable": "ضريبة كسب العمل المستحقة",
           "martyrs_fund": "صندوق الشهداء", "employee_advances": "سلف العاملين", "accrued_payroll": "الأجور المستحقة", "custody": "العهد",
           "vat_input": "ضريبة المدخلات", "vat_output": "ضريبة المخرجات", "product_sales": "مبيعات المنتجات",
           "installment_deferred": "فوائد تقسيط مؤجلة", "installment_income": "إيراد فوائد التقسيط", "cost_of_sales": "تكلفة المبيعات",
           "service_deferred": "إيرادات خدمات مؤجلة", "wht_receivable": "ضريبة خصم مدينة", "wht_payable": "ضريبة خصم دائنة",
           "revenue": "الإيراد", "expense": "المصروف", "cost": "التكلفة", "inventory": "المخزون", "client_advances": "دفعات العملاء",
           "accumulated_depreciation": "مجمع الإهلاك", "account": "الحساب", "accum": "مجمع الإهلاك", "against": "المقابل",
           "cash": "النقدية", "credit": "الدائن", "equity": "حقوق الملكية", "paid from": "الدفع من", "payable": "الدائنين",
           "received into": "التحصيل في", "depreciation": "الإهلاك", "capital": "رأس المال", "drawings": "المسحوبات"}

# notes, cover descriptions, dashboard / partner pack / calculator labels
MORE_AR = {
    "Double-entry books with live statements. Type in the data sheets (blue text); every statement, the dashboard and the checks recalculate by themselves. All values in EGP. Import this same file back into the app from Import & Export.":
        "دفاتر بالقيد المزدوج وقوائم مالية حية. اكتب في أوراق الإدخال (النص الأزرق)؛ كل القوائم ولوحة المتابعة والفحوص تُعاد حسابها تلقائياً. كل القيم بالجنيه المصري. يمكن استيراد هذا الملف نفسه إلى البرنامج من الاستيراد والتصدير.",
    "What to type on which sheet, and what updates by itself": "ماذا تكتب في كل ورقة، وما الذي يتحدث تلقائياً",
    "Fixed asset register (its purchase, depreciation and disposal are journal entries with reference FA:<code>)": "سجل الأصول الثابتة (الشراء والإهلاك والاستبعاد قيود يومية بمرجع FA:<الكود>)",
    "Warehouses / stores and their inventory account": "المخازن وحساب المخزون لكل منها", "Item master": "دليل الأصناف", "Showroom catalog": "كتالوج المعرض",
    "Goods receipts, material issues to business units, transfers and count adjustments (Adjustment Qty = counted − system, may be negative)":
        "استلام بضاعة وصرف خامات وتحويلات وتسويات جرد",
    "Sales of products / units with their payment plan": "مبيعات المنتجات / الوحدات بخطة السداد", "The bank's statement lines": "حركات كشف البنك",
    "Rent, salaries, subscriptions… posted on a schedule": "إيجار ورواتب واشتراكات… تُرحّل حسب جدول",
    "Maintenance, warranty / insurance, software support and add-on plans you sell by the month / quarter / year": "خطط الصيانة والضمان والدعم والإضافات",
    "Each customer's contract": "عقد كل عميل", "One row per order line; rows with the same order code form one order": "صف لكل بند؛ الصفوف بنفس الكود تكوّن أمراً واحداً",
    "Monthly budget per income / cost / expense account (blank business unit = company level)": "الموازنة الشهرية لكل حساب (وحدة العمل فارغة = مستوى الشركة)",
    "Every section is shown. Sections switched off in the app (Settings → Menus) are hidden from this file.": "كل الأقسام ظاهرة. الأقسام المُغلقة في البرنامج (الإعدادات ← القوائم) تُخفى من هذا الملف.",
    "Billed = invoiced to the client (or settled). Work in progress = work done but not invoiced yet: its revenue and profit are held back until the invoice is issued, so a project in progress shows no billed profit. A project heading for a loss shows that loss at once. Collected = cash actually received.":
        "المفوتر = ما صدرت به فاتورة للعميل (أو تمت تسويته). تحت التنفيذ = أعمال تمت ولم تُفوتر بعد: يُحجز إيرادها وربحها حتى إصدار الفاتورة، فلا يظهر للمشروع الجاري ربح مفوتر. والمشروع المتجه لخسارة تظهر خسارته فوراً. المحصل = النقدية المستلمة فعلاً.",
    "Earned basis counts revenue when the work is done and costs when incurred. Cash basis counts only money that moved in the same entry (collections and payments).":
        "أساس الاستحقاق يحسب الإيراد عند تنفيذ العمل والتكلفة عند تحملها. الأساس النقدي يحسب فقط الأموال التي تحركت في نفس القيد (التحصيلات والمدفوعات).",
    "Less: advances, custody & other current assets": "يُخصم: السلف والعهد والأصول المتداولة الأخرى",
    "Equipment & other long-term assets bought": "معدات وأصول طويلة الأجل مشتراة", "Net cash from investment, loans & partners": "صافي النقدية من الاستثمار والقروض والشركاء",
    "Reserves & retained earnings (balance sheet)": "الاحتياطيات والأرباح المحتجزة (الميزانية)",
    "Each partner's profit share follows their ownership % per project (Partner Shares sheet, else their default share on the Partners sheet), after the company reserve on the Settings sheet (reserve_pct). Capital and distributions are read from the books by partner name.":
        "نصيب كل شريك من الربح يتبع نسبة ملكيته في كل مشروع (ورقة حصص الشركاء، وإلا نسبته الافتراضية في ورقة الشركاء) بعد احتياطي الشركة في الإعدادات (reserve_pct). رأس المال والتوزيعات تُقرأ من الدفاتر باسم الشريك.",
    "Helpers (project & partner workings for the Partner Pack)": "حسابات مساعدة (للمشروعات والشركاء في تقرير الشركاء)",
    "Company overhead not charged to a project": "مصروفات الشركة غير المحملة على مشروع", "Billed profit — company": "الربح المفوتر — الشركة",
    "Profit held in work in progress": "ربح محجوز في الأعمال تحت التنفيذ", "Months of activity (first entry to today / report end)": "شهور النشاط (من أول قيد حتى اليوم / نهاية التقرير)",
    "Cash safety floor (3 months of overhead + owed)": "حد الأمان النقدي (3 شهور مصروفات + المستحق)", "Revenue received in cash": "إيراد محصل نقداً",
    "Direct costs paid in cash": "تكاليف مباشرة مدفوعة نقداً", "Staff costs paid in cash": "تكاليف عاملين مدفوعة نقداً", "G&A paid in cash": "عمومية وإدارية مدفوعة نقداً",
    "Other costs paid in cash": "تكاليف أخرى مدفوعة نقداً", "Cost of Goods & Units Sold": "تكلفة البضاعة والوحدات المباعة", "Inventory Adjustments": "تسويات المخزون",
    "From the Rental Contracts sheet (one block of rows per contract). Rent to date = rent a month ÷ 30 × days since delivery, up to today or the report end. Invoiced and collected are the contract's business unit on Sales Invoices and Receipt Vouchers.":
        "من ورقة عقود التأجير (مجموعة صفوف لكل عقد). الإيجار حتى تاريخه = الإيجار الشهري ÷ 30 × الأيام منذ التسليم حتى اليوم أو نهاية التقرير. المفوتر والمحصل من فواتير المبيعات وسندات القبض لوحدة عمل العقد.",
    "Operating expenses booked without a business unit (depreciation, interest and tax are not allocated), shared out by each unit's Overhead Allocation %.":
        "المصروفات التشغيلية المسجلة بدون وحدة عمل (لا يُوزع الإهلاك والفوائد والضرائب)، موزعة بنسبة تحميل كل وحدة.",
    "Every name on the Parties sheet is listed here — add a supplier / contact there and its balance appears.": "كل اسم في ورقة العملاء والموردين يظهر هنا — أضف مورداً هناك ويظهر رصيده.",
    "Profit share = Σ (business-unit net profit × share) + company-level result × default share; distributable = share × (1 − reserve %)":
        "نصيب الربح = Σ (صافي ربح وحدة العمل × النسبة) + نتيجة مستوى الشركة × النسبة الافتراضية؛ القابل للتوزيع = النصيب × (1 − نسبة الاحتياطي)",
    "Owner Drawing": "مسحوبات المالك", "COGS": "تكلفة المبيعات",
    "Pick a description from the Price List; unit, prices and cost fill in automatically.": "اختر البيان من قائمة الأسعار؛ الوحدة والأسعار والتكلفة تُملأ تلقائياً.",
    "Type the blue cells. Quantities follow the same rules as the app's rental calculator; rent, loss price and weight come from the Rental Equipment sheet by item code (or type your own rate in the last column).":
        "اكتب في الخلايا الزرقاء. الكميات تتبع قواعد حاسبة التأجير في البرنامج؛ الإيجار وسعر الفقد والوزن من ورقة معدات التأجير بكود الصنف (أو اكتب سعرك في العمود الأخير).",
    "(not on Rental Equipment)": "(غير موجود في معدات التأجير)",
    "Client price by months of rent, m², trip, ton or lump sum; our cost by m², trip, ton or lump sum. Scope: both / install / dismantle / none (the client installs). Change the blue cells.":
        "سعر العميل بشهور الإيجار أو م² أو النقلة أو الطن أو مقطوعية؛ وتكلفتنا بالـ م² أو النقلة أو الطن أو مقطوعية. النطاق: both / install / dismantle / none (العميل يركب). عدّل الخلايا الزرقاء.",
    "both / install / dismantle / none": "both (الكل) / install (تركيب) / dismantle (فك) / none (لا شيء)", "Unit price / × rent": "سعر الوحدة / × الإيجار",
    "× monthly rent  (cost per m²)": "× الإيجار الشهري (التكلفة لكل م²)", "per trip  (cost per trip)": "للنقلة (التكلفة لكل نقلة)",
    "per m²  (cost per m²)": "للمتر² (التكلفة لكل م²)", "per ton  (cost per ton)": "للطن (التكلفة لكل طن)", "lump sum  (cost lump sum)": "مقطوعية (التكلفة مقطوعية)",
    "Transport to site": "النقل إلى الموقع", "Transport back to the yard": "النقل إلى المخزن", "SERVICES TOTAL": "إجمالي الخدمات",
    "Margin on services / per m²": "هامش الخدمات / لكل م²", "First invoice (first month's rent + services at delivery)": "الفاتورة الأولى (إيجار أول شهر + الخدمات عند التسليم)",
    "Quantity = count × width / length × height / width × depth (blank = 1). Scaffold frames: count = frames, width = frame width, height = frame height → m² installed. This sheet is only a calculator: it never touches the books, rents or other calculators.":
        "الكمية = العدد × العرض / الطول × الارتفاع / العرض × العمق (الفارغ = 1). شبابيك السقالات: العدد = الشبابيك، العرض = عرض الشباك، الارتفاع = ارتفاع الشباك ← م² المركبة. هذه الورقة حاسبة فقط: لا تؤثر على الدفاتر أو الإيجارات أو الحاسبات الأخرى.",
    "To book what you ACTUALLY paid (it can differ from the calculation): add an Operating Expenses or Payroll row, or a Journal Voucher (Dr labour cost, Cr cash / bank). In the app: Crew pay calculator → Record payment.":
        "لتسجيل ما دفعته فعلاً (قد يختلف عن الحساب): أضف صفاً في المصروفات التشغيلية أو الرواتب، أو قيد يومية (مدين تكلفة العمالة، دائن النقدية / البنك). في البرنامج: حاسبة أجور الأطقم ← تسجيل الدفع.",
    "Difference = what was paid − what the sheet calculates, on the rows where a payment is typed": "الفرق = المدفوع − ما تحسبه الورقة، في الصفوف التي كُتب فيها دفع",
    "Model: Frame scaffolding (standard)": "النموذج: سقالة شبابيك (قياسي)",
    "Goods receipts, material issues to business units, transfers and count adjustments (Adjustment Qty = counted −": "استلام بضاعة وصرف خامات وتحويلات وتسويات جرد",
    "Client name": "اسم العميل", "Line price": "سعر البند", "Line cost": "تكلفة البند", "Hours worked": "ساعات العمل",
    "Balances owed by party (suppliers & workers)": "الأرصدة المستحقة للأطراف (الموردون والعمال)",
    "Balances owed to us by party (customers)": "الأرصدة المستحقة لنا (العملاء)", "Partner equity & distributions": "حقوق الشركاء والتوزيعات",
    "Frame 2 m": "شباك 2 م", "Cross brace 3 m": "مقص 3 م", "Base plate 60 cm": "قاعدة 60 سم", "Screw jack": "جاك (رجل قلاووظ)",
    "Fixed clamp": "كلبس ثابت", "Swivel clamp": "كلبس متحرك", "Tube 2.8 mm × 6 m": "ماسورة 2.8 مم × 6 م", "Walking board 3.6 m": "لوح مشاية 3.6 م",
}

# words inside formulas that show in a cell (codes compared by formulas — Yes, Posted, ALL OK — are never here)
LITERALS_AR = {
    "Management accounts as at ": "الحسابات الإدارية في ", "   ·   EGP   ·   period from ": "   ·   جنيه   ·   الفترة من ", " to ": " إلى ",
    "DO NOT DISTRIBUTE — below the cash floor (": "لا توزع — أقل من حد الأمان النقدي (", "Distribution possible up to ": "يمكن التوزيع حتى ",
    " above the cash floor": " فوق حد الأمان النقدي", "ALL INTEGRITY CHECKS PASS": "كل فحوص السلامة سليمة", "REVIEW THE MODEL AUDIT": "راجع فحص سلامة الدفاتر",
    "✓  Balanced — total assets equal total liabilities plus equity": "✓  متوازنة — إجمالي الأصول = الالتزامات + حقوق الملكية",
    "✗  NOT balanced — open the Model Audit": "✗  غير متوازنة — افتح فحص سلامة الدفاتر",
    "✓  Ties to the cash & bank on the balance sheet": "✓  يطابق النقدية والبنوك في الميزانية", "✗  Does not tie to the balance sheet cash": "✗  لا يطابق نقدية الميزانية",
    "Kept in company (reserve ": "محتجز بالشركة (احتياطي ", "✓  Ties to the balance sheet — total partners' equity ": "✓  يطابق الميزانية — إجمالي حقوق الشركاء ",
    "✗  Does not tie to the balance sheet — check partner names on capital and distributions": "✗  لا يطابق الميزانية — راجع أسماء الشركاء في رأس المال والتوزيعات",
    "Live from the books for ": "مباشرة من الدفاتر للفترة ", "  ·  EGP  ·  change the period on the Settings sheet": "  ·  جنيه  ·  غيّر الفترة من ورقة الإعدادات",
    "POSITION ON ": "المركز في ", "Books integrity (Model Audit):  ": "سلامة الدفاتر:  ", "     ·     open the Model Audit sheet for the detail": "     ·     افتح فحص سلامة الدفاتر للتفاصيل",
    "REVENUE AND COSTS BY MONTH — ": "الإيرادات والتكاليف شهرياً — ", " · EGP": " · جنيه", "As at ": "في ", "Period ": "الفترة ",
    "*** CHECK": "*** راجع", "REVIEW": "مراجعة", "(not on Rental Equipment)": "(غير موجود في معدات التأجير)",
    "Frame 2 m": "شباك 2 م", "Cross brace 3 m": "مقص 3 م", "Base plate 60 cm": "قاعدة 60 سم", "Screw jack": "جاك (رجل قلاووظ)",
    "Fixed clamp": "كلبس ثابت", "Swivel clamp": "كلبس متحرك", "Tube 2.8 mm × 6 m": "ماسورة 2.8 مم × 6 م", "Walking board 3.6 m": "لوح مشاية 3.6 م",
    " — actuals are company-wide; months of the budget inside the period are counted": " — الفعلي على مستوى الشركة؛ تُحسب شهور الموازنة داخل الفترة",
}

# example row (row 3) values on the data sheets
EXAMPLE_AR = {
    "Account name": "اسم الحساب", "Project type": "نوع المشروع", "Asset name": "اسم الأصل", "Warehouse name": "اسم المخزن",
    "Item name": "اسم الصنف", "Product name": "اسم المنتج", "Category": "التصنيف", "SKU-0001": "SKU-0001",
    "Spec 1: value; Spec 2: value": "مواصفة 1: قيمة؛ مواصفة 2: قيمة", "Item or service description": "وصف الصنف أو الخدمة",
    "BOQ title": "عنوان المقايسة", "Item or service": "صنف أو خدمة", "Expense description": "وصف المصروف", "Job title": "المسمى الوظيفي",
    "Monthly depreciation": "إهلاك شهري",
    "Rev 01": "مراجعة 01", "Advance payment": "دفعة مقدمة",     "bag": "شكارة", "Customer name": "اسم العميل", "e.g. Customer name": "مثال: اسم العميل", "Governorate": "المحافظة", "Area": "المنطقة", "Street name": "اسم الشارع", "Project name": "اسم المشروع", "City": "المدينة", "Site store": "مخزن الموقع", "e.g. Site store": "مثال: مخزن الموقع", "Supplier name": "اسم المورد", "Employee name": "اسم الموظف", "Ahmed Ali": "أحمد علي",
    "Site engineer": "مهندس موقع", "Partner name": "اسم الشريك", "e.g. Partner name": "مثال: اسم الشريك", "Metro": "المدينة", "North District": "الحي الشمالي",
    "VAT 14% (standard)": "ضريبة القيمة المضافة 14%", "Site pickup truck": "سيارة نقل للموقع",
        "Apartments": "شقق",     "Receipt": "استلام", "Main Store": "المخزن الرئيسي", }


def helper(label):
    """A grey helper-column header in Arabic, or None."""
    import re
    if label in HELPERS_AR:
        return HELPERS_AR[label]
    m = re.fullmatch(r"leg (\d+) type", label)
    if m:
        return f"نوع الطرف {m.group(1)}"
    m = re.fullmatch(r"(debit|credit) (\d+)( account)?", label)
    if m:
        return ("حساب " if m.group(3) else "") + ("مدين" if m.group(1) == "debit" else "دائن") + f" {m.group(2)}"
    m = re.fullmatch(r"([a-z_]+) \(effective\)", label)
    if m:
        return "الحساب الفعلي — " + ROLE_AR.get(m.group(1).replace("_account", ""), m.group(1).replace("_account", "").replace("_", " "))
    m = re.fullmatch(r"([a-z_]+) account", label)
    if m and m.group(1) in ROLE_AR:
        return f"حساب {ROLE_AR[m.group(1)]}"
    m = re.fullmatch(r"([a-z_ ]+) allowance", label)
    if m:
        return {"housing": "بدل سكن", "transport": "بدل انتقال", "meal": "بدل وجبة", "phone": "بدل اتصالات", "site": "بدل موقع",
                "other_allow": "بدلات أخرى"}.get(m.group(1), f"بدل {m.group(1)}")
    return None
