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
from . import payroll

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
}

TYPES = {
    "general": {"name": "General business", "ar": "نشاط عام", "hide": ["rentals", "shop", "estimating", "contracts", "equipment"],
                "departments": ["Management", "Finance", "Sales", "Operations", "Admin"],
                "jobs": ["General Manager", "Accountant", "Sales Representative", "Administrator", "Driver", "Worker"],
                "bu": "Department / branch", "components": {"site": False},
                "guide": ["Use business units for branches or departments so each shows its own profit.",
                          "Record purchases as Operating Expenses or Direct Costs; stock items only if you hold stock."]},
    "interior": {"name": "Interior design & finishing", "ar": "التصميم الداخلي والتشطيبات", "hide": ["rentals", "shop"],
                 "departments": ["Design", "Execution / Site", "Procurement", "Finance", "Admin"],
                 "jobs": ["Design Manager", "Interior Designer", "3D Visualiser", "Site Engineer", "Site Supervisor", "Foreman",
                          "Quantity Surveyor", "Accountant", "Technician", "Worker"],
                 "bu": "Project", "components": {"commission": False},
                 "guide": ["One business unit per project with its billing basis (cost-plus %, fixed price or settled).",
                           "Design fees, execution and supply are separate revenue accounts; BOQs and the price list feed offers.",
                           "Site workers on daily pay go on payroll as daily employees with the project as their business unit; "
                           "finishing subcontractors paid per m² use the crew calculator."]},
    "contracting": {"name": "Contracting & construction", "ar": "المقاولات العامة والإنشاءات", "hide": ["rentals", "shop"],
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
    "development": {"name": "Real-estate development", "ar": "التطوير العقاري", "hide": ["rentals"],
                    "departments": ["Development", "Construction", "Sales & Marketing", "Customer Care", "Finance", "Legal", "Admin"],
                    "jobs": ["Development Manager", "Project Manager", "Site Engineer", "Sales Manager", "Property Consultant",
                             "Customer Service", "Collections Officer", "Legal Counsel", "Accountant"],
                    "bu": "Project / phase", "components": {"commission": True},
                    "guide": ["Land and construction costs build up in development WIP (1220 / 1225) per phase; completed units "
                              "move to 1230 and are released to cost of sales when each unit is sold.",
                              "Unit sales on installments with cheques run from Sales & showroom; reservation deposits sit in 2140.",
                              "Brokers' and consultants' commissions: payroll commission line or 6032."]},
    "sales": {"name": "Trading & sales", "ar": "التجارة والمبيعات", "hide": ["rentals", "estimating", "contracts", "equipment"],
              "departments": ["Sales", "Warehouse", "Purchasing", "Delivery", "Finance", "Admin"],
              "jobs": ["Sales Manager", "Sales Representative", "Cashier", "Storekeeper", "Purchasing Officer", "Driver", "Accountant"],
              "bu": "Branch / channel", "components": {"commission": True},
              "guide": ["Products, stock and installments live in Sales & showroom; business units split branches or channels.",
                        "Pay commission as a monthly payroll line so it is taxed and costed with the salary."]},
    "investment": {"name": "Investment & holding", "ar": "الاستثمار والقابضة", "hide": ["rentals", "shop", "estimating", "stock", "projects", "contracts", "equipment"],
                   "departments": ["Investments", "Finance", "Legal", "Admin"],
                   "jobs": ["Investment Manager", "Financial Analyst", "Accountant", "Legal Counsel", "Administrator"],
                   "bu": "Portfolio / investment", "components": {"commission": False, "site": False},
                   "guide": ["Each holding or property is a business unit; dividends, gains and fair-value changes have their own accounts.",
                             "Loans given and taken run from Loans; partners' capital and distributions from Partners."]},
    "scaffolding": {"name": "Scaffolding & equipment rental", "ar": "تأجير السقالات والمعدات", "hide": ["shop", "estimating", "contracts"],
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
}
ORDER = ["general", "interior", "contracting", "development", "sales", "investment", "scaffolding"]


def catalogue():
    return [{"key": k, "name": TYPES[k]["name"], "name_ar": TYPES[k]["ar"], "hide": TYPES[k]["hide"], "bu": TYPES[k]["bu"],
             "departments": TYPES[k]["departments"], "jobs": TYPES[k]["jobs"], "guide": TYPES[k]["guide"],
             "accounts": [{"code": c, "name": n, "name_ar": ar} for c, n, ar, *_ in _ACC.get(k, [])],
             "work_items": [{"item": i, "unit": u} for i, u in payroll.DEFAULT_WORK_ITEMS.get(k, payroll.DEFAULT_WORK_ITEMS["general"])]}
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
