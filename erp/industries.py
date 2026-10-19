"""Business-type templates: one company file set up for the way that kind of business works.

Applying a template is additive and safe on live books: it never deletes an account, an entry or a screen's data.
It switches on the sections the business uses (the rest are hidden, not removed — Settings → Menus brings them back),
adds the accounts that business needs when they are missing, and sets the departments, job titles, pay items and
measured-work items that payroll and the crew calculator offer. Every choice stays editable afterwards.

The set-ups follow how established ERPs package industries (Odoo's industry apps; scaffolding-specific systems such as
Avontus Quantify for the rental side): track stock by where it is (yard / each site), bill rentals by period with a
minimum, charge loss and damage at a price list, price services (installation, dismantling, transport) separately
with their own cost, and keep labour per project so every site shows its own profit.
"""
import json

from . import accounting as acc
from . import care, payroll

# code, name, Arabic, type, subtype
_ACC = {
    "development": [("1220", "Land Held for Development", "أراضٍ بغرض التطوير", "Asset", "inventory"),
                    ("1225", "Units Under Construction (Development WIP)", "وحدات تحت الإنشاء", "Asset", "inventory"),
                    ("1230", "Completed Units for Sale", "وحدات تامة معدة للبيع", "Asset", "inventory"),
                    ("2140", "Unit Reservation Deposits", "جديات حجز الوحدات", "Liability", "deferred_revenue"),
                    ("5085", "Land & Development Cost of Units Sold", "تكلفة الأرض والتطوير للوحدات المباعة", "COGS", "cogs"),
                    ("6032", "Sales Commissions (brokers & agents)", "عمولات البيع (السماسرة)", "Expense", "marketing")],
    "investment": [("1705", "Investments in Listed Shares", "استثمارات في أسهم مقيدة", "Asset", "investment"),
                   ("1710", "Investments in Subsidiaries & Associates", "استثمارات في شركات تابعة وشقيقة", "Asset", "investment"),
                   ("1715", "Investment Property", "استثمارات عقارية", "Asset", "investment"),
                   ("1720", "Treasury Bills & Deposits", "أذون خزانة وودائع", "Asset", "investment"),
                   ("4091", "Dividend Income", "إيرادات توزيعات أرباح", "Revenue", "other_income"),
                   ("4092", "Gains on Sale of Investments", "أرباح بيع استثمارات", "Revenue", "other_income"),
                   ("4093", "Fair-Value Changes on Investments", "فروق تقييم الاستثمارات", "Revenue", "other_income"),
                   ("7055", "Losses on Sale of Investments", "خسائر بيع استثمارات", "Expense", "other_nonoperating")],
    "sales": [("6032", "Sales Commissions", "عمولات المبيعات", "Expense", "marketing"),
              ("6034", "Delivery & Freight Out", "مصروفات توصيل وشحن", "Expense", "other_operating"),
              ("4055", "Sales Returns", "مردودات المبيعات", "Revenue", "sales_discount")],
    "contracting": [("5025", "Plant & Machinery Running Costs", "تشغيل المعدات والآلات", "COGS", "cogs"),
                    ("5045", "Site Camp, Security & Utilities", "معسكر الموقع والحراسة والمرافق", "COGS", "cogs"),
                    ("6072", "Tendering & Bid Costs", "مصروفات المناقصات والعطاءات", "Expense", "other_operating")],
    "interior": [("5075", "Furniture & Accessories (client supply)", "أثاث وإكسسوارات (توريد للعميل)", "COGS", "cogs")],
    "scaffolding": [("5065", "Installation & Dismantling Crews", "أطقم التركيب والفك", "COGS", "cogs"),
                    ("5066", "Scaffold Repairs, Painting & Maintenance", "إصلاح ودهان وصيانة السقالات", "COGS", "cogs"),
                    ("5067", "Transport of Equipment", "نقل المعدات", "COGS", "cogs")],
    "general": [],
    "healthcare": care.ACCOUNTS,
    "homecare": care.ACCOUNTS,
}

TYPES = {
    "general": {"name": "General business", "ar": "نشاط عام", "hide": ["rentals", "shop", "estimating", "contracts", "equipment", "care"],
                "departments": ["Management", "Finance", "Sales", "Operations", "Admin"],
                "jobs": ["General Manager", "Accountant", "Sales Representative", "Administrator", "Driver", "Worker"],
                "bu": "Department / branch", "components": {"site": False},
                "guide": ["Use business units for branches or departments so each shows its own profit.",
                          "Record purchases as Operating Expenses or Direct Costs; stock items only if you hold stock."]},
    "interior": {"name": "Interior design & finishing", "ar": "التصميم الداخلي والتشطيبات", "hide": ["rentals", "shop", "care"],
                 "departments": ["Design", "Execution / Site", "Procurement", "Finance", "Admin"],
                 "jobs": ["Design Manager", "Interior Designer", "3D Visualiser", "Site Engineer", "Site Supervisor", "Foreman",
                          "Quantity Surveyor", "Accountant", "Technician", "Worker"],
                 "bu": "Project", "components": {"commission": False},
                 "guide": ["One business unit per project with its billing basis (cost-plus %, fixed price or settled).",
                           "Design fees, execution and supply are separate revenue accounts; BOQs and the price list feed offers.",
                           "Site workers on daily pay go on payroll as daily employees with the project as their business unit; "
                           "finishing subcontractors paid per m² use the crew calculator."]},
    "contracting": {"name": "Contracting & construction", "ar": "المقاولات العامة والإنشاءات", "hide": ["rentals", "shop", "care"],
                    "departments": ["Projects", "Technical Office", "Procurement", "Equipment", "QA / QC & Safety", "Finance", "Admin"],
                    "jobs": ["Project Manager", "Site Engineer", "Technical Office Engineer", "Quantity Surveyor", "Safety Officer",
                             "Foreman", "Storekeeper", "Equipment Operator", "Driver", "Accountant", "Worker"],
                    "bu": "Project / contract", "components": {"commission": False},
                    "guide": ["Each contract is a business unit; invoices are progress certificates (مستخلصات) with retention "
                              "(1150 retention receivable) and subcontractor retention (2110).",
                              "Issue materials to the project from stock, so its cost is in its profit when used.",
                              "Measured subcontract work (formwork m², rebar ton, blockwork m²) is priced in the crew calculator.",
                              "Contracts: the client contract and approved variation orders set the project's value; subcontracts are paid "
                              "by cumulative certificates (retention, advance recovery, VAT / withholding) — Projects & Contracts.",
                              "Site: purchase requests → quotes compared → orders; daily attendance; equipment hours and maintenance; "
                              "phases with % complete and tasks per project."]},
    "development": {"name": "Real-estate development", "ar": "التطوير العقاري", "hide": ["rentals", "care"],
                    "departments": ["Development", "Construction", "Sales & Marketing", "Customer Care", "Finance", "Legal", "Admin"],
                    "jobs": ["Development Manager", "Project Manager", "Site Engineer", "Sales Manager", "Property Consultant",
                             "Customer Service", "Collections Officer", "Legal Counsel", "Accountant"],
                    "bu": "Project / phase", "components": {"commission": True},
                    "guide": ["Land and construction costs build up in development WIP (1220 / 1225) per phase; completed units "
                              "move to 1230 and are released to cost of sales when each unit is sold.",
                              "Unit sales on installments with cheques run from Sales & showroom; reservation deposits sit in 2140.",
                              "Brokers' and consultants' commissions: payroll commission line or 6032."]},
    "sales": {"name": "Trading & sales", "ar": "التجارة والمبيعات", "hide": ["rentals", "estimating", "contracts", "equipment", "care"],
              "departments": ["Sales", "Warehouse", "Purchasing", "Delivery", "Finance", "Admin"],
              "jobs": ["Sales Manager", "Sales Representative", "Cashier", "Storekeeper", "Purchasing Officer", "Driver", "Accountant"],
              "bu": "Branch / channel", "components": {"commission": True},
              "guide": ["Products, stock and installments live in Sales & showroom; business units split branches or channels.",
                        "Pay commission as a monthly payroll line so it is taxed and costed with the salary."]},
    "investment": {"name": "Investment & holding", "ar": "الاستثمار والقابضة", "hide": ["rentals", "shop", "estimating", "stock", "projects", "contracts", "equipment", "care"],
                   "departments": ["Investments", "Finance", "Legal", "Admin"],
                   "jobs": ["Investment Manager", "Financial Analyst", "Accountant", "Legal Counsel", "Administrator"],
                   "bu": "Portfolio / investment", "components": {"commission": False, "site": False},
                   "guide": ["Each holding or property is a business unit; dividends, gains and fair-value changes have their own accounts.",
                             "Loans given and taken run from Loans; partners' capital and distributions from Partners."]},
    "scaffolding": {"name": "Scaffolding & equipment rental", "ar": "تأجير السقالات والمعدات", "hide": ["shop", "estimating", "contracts", "care"],
                    "departments": ["Operations / Yard", "Installation Crews", "Sales", "Finance", "Admin"],
                    "jobs": ["Operations Manager", "Yard Keeper", "Installation Supervisor", "Scaffolder", "Safety Inspector",
                             "Driver", "Sales Representative", "Accountant"],
                    "bu": "Rental contract / site", "components": {"commission": False},
                    "guide": ["Every piece type is a rental item with its monthly rent, loss / damage price and weight; buy pieces "
                              "as stock lots so the fleet's cost and depreciation are in the books.",
                              "Stock is tracked by where it is: in the yard or on each contract's site, through signed delivery and "
                              "return handovers (good / damaged / lost) — the return count bills losses at the price list.",
                              "Bill rent per period with a minimum; installation, dismantling and transport are separate services "
                              "with their own client price and crew / truck cost (rental calculator).",
                              "Crews paid per m² (frames × width × height) use the crew calculator; record what you actually paid.",
                              "Watch utilisation (pieces out ÷ owned) and payback (purchase cost ÷ monthly rent) per piece."]},
    "healthcare": {"name": "Clinics & medical centres", "ar": "العيادات والمراكز الطبية", "hide": ["rentals", "shop", "estimating", "contracts", "projects"],
                   "departments": ["Clinics / Outpatient", "Laboratory", "Radiology", "Nursing", "Reception & Billing", "Insurance & Claims",
                                   "Pharmacy & Stores", "Finance", "Admin"],
                   "jobs": ["Medical Director", "Consultant", "Specialist", "Resident Doctor", "Dentist", "Nurse", "Lab Technician",
                            "Radiology Technician", "Pharmacist", "Receptionist", "Insurance Coordinator", "Accountant", "Cleaner"],
                   "bu": "Branch / clinic / department", "components": {"commission": True},
                   "guide": ["Healthcare: patients (with their insurer / TPA, policy and coverage %), a price list of services (cash price, "
                             "contracted insurer price, the doctor's share, consumables), and appointments that become visits.",
                             "Billing a visit splits it into the patient's co-pay (cash / card at the desk) and the insurer's share "
                             "(1105, claimed monthly); revenue goes to 4061. The doctor's share is booked as a cost (5090) owed to "
                             "them (2115) — pay it from Payments, or through payroll.",
                             "Consumables (gloves, syringes, dressing kits) leave stock at cost on each visit (5091): keep them as stock "
                             "items in the clinic's store.",
                             "Insurance claims: gather a month's insurer shares into one claim; when the insurer pays, record what was "
                             "paid and what was rejected (4056).",
                             "Each branch, clinic or department is a business unit, so each shows its own profit. Medical services are "
                             "generally VAT-exempt; medical devices you buy may carry 5% VAT (2026)."]},
    "homecare": {"name": "Home healthcare", "ar": "الرعاية الصحية المنزلية", "hide": ["rentals", "shop", "estimating", "contracts", "projects", "equipment"],
                 "departments": ["Home Nursing", "Physiotherapy", "Home Doctors", "Elderly Care", "Care Coordination", "Finance", "Admin"],
                 "jobs": ["Medical Director", "Care Coordinator", "Home Visit Doctor", "Registered Nurse", "Assistant Nurse",
                          "Physiotherapist", "Caregiver / Elderly Companion", "Driver", "Accountant"],
                 "bu": "Service line / area team", "components": {"commission": True},
                 "guide": ["Home healthcare: each visit has the patient's address and district (to route the team), a travel fee "
                           "(4063), and check-in / check-out times — the record that the visit happened and how long a nursing "
                           "shift lasted.",
                           "Packages (12 physiotherapy sessions, a month of nursing) are paid in advance and held as prepaid (2108); "
                           "each session given moves its share to revenue (4062).",
                           "Nurses and therapists paid per visit or per shift: their share of each service is booked as a cost (5090) "
                           "owed to them (2115) and paid weekly or monthly; salaried staff stay on payroll.",
                           "Insured patients: the insurer's share is claimed monthly like a clinic; family-paid care is billed to the patient.",
                           "Consumables used at home (dressing kits, catheters) leave stock at cost on each visit."]},
}
ORDER = ["general", "interior", "contracting", "development", "sales", "investment", "scaffolding", "healthcare", "homecare"]


def catalogue():
    from .textl10n import UNITS
    return [{"key": k, "name": TYPES[k]["name"], "name_ar": TYPES[k]["ar"], "hide": TYPES[k]["hide"], "bu": TYPES[k]["bu"],
             "bu_ar": TYPES_AR.get(k, ("", []))[0], "guide_ar": TYPES_AR.get(k, ("", []))[1],
             "departments": TYPES[k]["departments"], "jobs": TYPES[k]["jobs"], "guide": TYPES[k]["guide"],
             "accounts": [{"code": c, "name": n, "name_ar": ar} for c, n, ar, *_ in _ACC.get(k, [])],
             "work_items": [{"item": i, "unit": u, "item_ar": WORK_AR.get(i, i), "unit_ar": UNITS.get(u, u)}
                            for i, u in payroll.DEFAULT_WORK_ITEMS.get(k, payroll.DEFAULT_WORK_ITEMS["general"])]}
            for k in ORDER]


def apply(db, key, hide_modules=True):
    if key not in TYPES:
        raise acc.ValidationError("Unknown business type")
    t = TYPES[key]
    added = []
    with db.tx():
        for code, name, ar, typ, sub in _ACC.get(key, []):
            if db.one("SELECT 1 FROM accounts WHERE code=?", (code,)) or db.one("SELECT 1 FROM accounts WHERE name=?", (name,)):
                continue
            acc.save_account(db, {"code": code, "name": name, "name_ar": ar, "type": typ, "subtype": sub})
            added.append(code)
        db.set_setting("business_type", key)
        if key in ("interior", "contracting"):          # design / fit-out / supervision offers fit these businesses
            from .documents import DESIGN_OFFER_TYPES
            for ot in DESIGN_OFFER_TYPES:
                if not db.one("SELECT 1 FROM offer_types WHERE name=?", (ot["name"],)):
                    db.conn.execute("INSERT INTO offer_types(name, content) VALUES (?,?)",
                                    (ot["name"], json.dumps({k: v for k, v in ot.items() if k != "name"}, ensure_ascii=False)))
        if hide_modules:
            db.set_setting("hidden_modules", json.dumps(t["hide"]))
        db.set_setting("payroll_departments", json.dumps(t["departments"], ensure_ascii=False))
        db.set_setting("payroll_jobs", json.dumps(t["jobs"], ensure_ascii=False))
        s = payroll.settings(db)
        for c in s["components"]:
            if c["code"] in t["components"]:
                c["active"] = t["components"][c["code"]]
        payroll.save_settings(db, {"components": s["components"]})
    return {"type": key, "accounts_added": added, "hidden": t["hide"] if hide_modules else None}


# ---- the same guide in Arabic (catalogue() sends both; the screen shows the reader's language)
TYPES_AR = {
    "general": ("قسم / فرع", [
        "استخدم وحدات الأعمال للفروع أو الأقسام ليظهر ربح كل منها على حدة.",
        "سجّل المشتريات كمصروفات تشغيل أو تكاليف مباشرة؛ وأصناف المخزون فقط إن كان لديك مخزون."]),
    "interior": ("مشروع", [
        "وحدة أعمال لكل مشروع بطريقة محاسبته (التكلفة + نسبة، سعر ثابت، أو تسوية).",
        "أتعاب التصميم والتنفيذ والتوريد حسابات إيراد منفصلة؛ والمقايسات وقائمة الأسعار تغذي العروض.",
        "عمال الموقع باليومية يُسجّلون في الرواتب كموظفين يوميين على وحدة أعمال المشروع؛ ومقاولو التشطيب بالمتر يُحسبون في حاسبة الأطقم."]),
    "contracting": ("مشروع / عقد", [
        "كل عقد وحدة أعمال؛ والفواتير مستخلصات جارية بتأمين أعمال (1150 تأمين أعمال لدى العملاء) وتأمين مقاولي الباطن (2110).",
        "اصرف الخامات للمشروع من المخزن لتدخل تكلفته في ربحه عند الاستخدام.",
        "أعمال مقاولي الباطن بالقياس (شدات م²، حديد طن، مباني م²) تُسعَّر في حاسبة الأطقم.",
        "العقود: عقد العميل وأوامر التغيير المعتمدة تحدد قيمة المشروع؛ ومقاولو الباطن يُصرف لهم بمستخلصات تراكمية (تأمين، استرداد دفعة مقدمة، ضريبة قيمة مضافة / خصم) — المشروعات والعقود.",
        "الموقع: طلبات شراء ← مقارنة عروض ← أوامر شراء؛ حضور يومي؛ ساعات وصيانة المعدات؛ مراحل بنسب إنجاز ومهام لكل مشروع."]),
    "development": ("مشروع / مرحلة", [
        "تتجمع تكاليف الأرض والإنشاء في أعمال التطوير تحت التنفيذ (1220 / 1225) لكل مرحلة؛ والوحدات المكتملة تنتقل إلى 1230 وتُحمّل على تكلفة المبيعات عند بيع كل وحدة.",
        "بيع الوحدات بالتقسيط والشيكات من المبيعات والمعرض؛ ومقدمات الحجز في 2140.",
        "عمولات الوسطاء والاستشاريين: بند عمولة في الرواتب أو حساب 6032."]),
    "sales": ("فرع / قناة بيع", [
        "المنتجات والمخزون والأقساط في المبيعات والمعرض؛ ووحدات الأعمال تفصل الفروع أو قنوات البيع.",
        "اصرف العمولة كبند شهري في الرواتب لتُحسب ضريبتها وتكلفتها مع الراتب."]),
    "investment": ("محفظة / استثمار", [
        "كل مساهمة أو عقار وحدة أعمال؛ ولتوزيعات الأرباح والأرباح الرأسمالية وفروق التقييم حسابات خاصة بها.",
        "القروض الممنوحة والمستلمة من شاشة القروض؛ ورأس مال الشركاء والتوزيعات من شاشة الشركاء."]),
    "scaffolding": ("عقد تأجير / موقع", [
        "كل نوع قطعة صنف تأجير بإيجاره الشهري وسعر الفقد / التلف ووزنه؛ واشترِ القطع كدفعات مخزون لتدخل تكلفة الأسطول وإهلاكه الدفاتر.",
        "المخزون يُتابع حسب مكانه: في الساحة أو في موقع كل عقد، عبر محاضر تسليم واستلام موقعة (سليم / تالف / مفقود) — وعد المرتجع يحاسب المفقود بقائمة الأسعار.",
        "فوترة الإيجار لكل فترة بحد أدنى؛ والتركيب والفك والنقل خدمات منفصلة بسعرها للعميل وتكلفة الطاقم / السيارة (حاسبة التأجير).",
        "الأطقم المدفوع لها بالمتر (الشبابيك × العرض × الارتفاع) تُحسب في حاسبة الأطقم؛ وسجّل ما دفعته فعلاً.",
        "تابع نسبة التشغيل (القطع المؤجرة ÷ المملوكة) واسترداد التكلفة (سعر الشراء ÷ الإيجار الشهري) لكل قطعة."]),
    "healthcare": ("فرع / عيادة / قسم", [
        "الرعاية الصحية: المرضى (بشركة التأمين / إدارة المطالبات، والوثيقة ونسبة التغطية)، وقائمة أسعار الخدمات (السعر النقدي، سعر التعاقد مع التأمين، حصة الطبيب، المستهلكات)، ومواعيد تتحول إلى زيارات.",
        "فوترة الزيارة تقسمها إلى حصة المريض (نقداً / ببطاقة في الاستقبال) وحصة التأمين (1105، تُطالب شهرياً)؛ والإيراد في 4061. حصة الطبيب تُسجّل كتكلفة (5090) مستحقة له (2115) — تُصرف من المدفوعات أو عبر الرواتب.",
        "المستهلكات (جوانتيات، سرنجات، أطقم غيار) تخرج من المخزون بالتكلفة مع كل زيارة (5091): احتفظ بها كأصناف في مخزن العيادة.",
        "مطالبات التأمين: اجمع حصص الشهر في مطالبة واحدة؛ وعند السداد سجّل المدفوع والمرفوض (4056).",
        "كل فرع أو عيادة أو قسم وحدة أعمال ليظهر ربحه. الخدمات الطبية معفاة غالباً من ضريبة القيمة المضافة؛ وقد تخضع الأجهزة الطبية المشتراة لضريبة 5% (2026)."]),
    "homecare": ("خط خدمة / فريق منطقة", [
        "الرعاية المنزلية: لكل زيارة عنوان المريض ومنطقته (لتوجيه الفريق) ورسم انتقال (4063) وأوقات الوصول والمغادرة — إثبات حدوث الزيارة ومدة وردية التمريض.",
        "الباقات (12 جلسة علاج طبيعي، شهر تمريض) تُدفع مقدماً وتُحفظ كإيراد مقدم (2108)؛ وكل جلسة تنقل حصتها إلى الإيراد (4062).",
        "الممرضون والمعالجون بالزيارة أو بالوردية: حصتهم من كل خدمة تكلفة (5090) مستحقة لهم (2115) تُصرف أسبوعياً أو شهرياً؛ والموظفون بالراتب يبقون في الرواتب.",
        "المرضى المؤمَّن عليهم: حصة التأمين تُطالب شهرياً مثل العيادة؛ والرعاية التي تدفعها الأسرة تُفوتر للمريض.",
        "المستهلكات المستخدمة بالمنزل (أطقم غيار، قساطر) تخرج من المخزون بالتكلفة مع كل زيارة."]),
}
WORK_AR = {"Frame scaffold — installation": "سقالة شبابيك — تركيب", "Frame scaffold — dismantling": "سقالة شبابيك — فك",
           "Loading / unloading a truck": "تحميل / تفريغ سيارة", "Wall painting": "دهان حوائط", "Gypsum-board ceiling": "سقف جبس بورد",
           "Floor tiling": "تبليط أرضيات", "Skirting": "وزرة", "Electrical point": "نقطة كهرباء", "Formwork": "شدات خشبية",
           "Rebar fixing": "تركيب حديد التسليح", "Blockwork": "مباني طوب", "Plastering": "بياض", "Excavation": "حفر",
           "Finishing works": "أعمال تشطيب", "Commission per unit sold": "عمولة لكل وحدة مباعة", "Site visit": "زيارة موقع",
           "Hours worked": "ساعات العمل", "Pieces": "قطع", "Area": "مساحة"}
