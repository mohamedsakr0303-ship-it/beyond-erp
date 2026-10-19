"""Entry types: named two-line entries people use often ("Pay rent", "Move cash to the bank", "Transfer to legal
reserve"...). Each side is a fixed account or a choice made when entering. They are editable in Settings → Menus,
feed the journal voucher's template list and can be put on the "+ New" menu.

A side is written as:
  "1020"                      a fixed account
  "role:bank"                 the account holding a system role (follows renumbering)
  "subtype:cash"              choose among accounts on these statement lines ("subtype:short_term_debt,long_term_debt")
  "type:Expense"              choose among accounts of these types ("type:COGS,Expense")
  ""                          choose any account
"""
import json
import re

from . import accounting as acc
from .accounting import ValidationError, text

# id, name, name_ar, voucher type (kind), debit side, credit side, party, business unit, on "+ New" by default
BUILTIN = [
    ("cash_transfer", "Move money between cash & bank", "تحويل بين الخزينة والبنك", "Journal Voucher", "subtype:cash", "subtype:cash", "none", "none"),
    ("bank_charges", "Bank charges", "مصروفات بنكية", "Journal Voucher", "type:Expense", "subtype:cash", "none", "optional"),
    ("loan_in", "Loan received", "استلام قرض", "Journal Voucher", "subtype:cash", "subtype:short_term_debt,long_term_debt", "optional", "none"),
    ("loan_out", "Loan repayment", "سداد قرض", "Journal Voucher", "subtype:short_term_debt,long_term_debt", "subtype:cash", "optional", "none"),
    ("interest", "Interest paid", "فوائد مدفوعة", "Journal Voucher", "role:interest", "subtype:cash", "optional", "none"),
    ("interest_in", "Interest received", "فوائد دائنة", "Journal Voucher", "subtype:cash", "role:interest_income", "optional", "none"),
    ("depreciation", "Depreciation (manual)", "إهلاك (يدوي)", "Depreciation", "role:depreciation", "role:accum_depreciation", "none", "optional"),
    ("amortization", "Amortization (manual)", "استهلاك (يدوي)", "Depreciation", "role:amortization", "role:accum_amortization", "none", "optional"),
    ("asset_buy", "Asset purchase (without the register)", "شراء أصل (بدون السجل)", "Journal Voucher", "subtype:fixed_asset,intangible", "subtype:cash", "optional", "optional"),
    ("prepaid_use", "Use up a prepaid expense", "استهلاك مصروف مقدم", "Journal Voucher", "type:Expense", "subtype:prepaid", "optional", "optional"),
    ("accrue", "Accrue an expense (owed, not yet billed)", "إثبات مصروف مستحق", "Journal Voucher", "type:Expense", "subtype:accrued", "optional", "optional"),
    ("unbilled", "Work done, not yet invoiced", "إيراد مستحق غير مفوتر", "Journal Voucher", "subtype:other_current_asset", "type:Revenue", "optional", "required"),
    ("advance_apply", "Apply client advance to invoice", "تسوية دفعة مقدمة مع فاتورة", "Journal Voucher", "role:client_advances", "role:ar", "required", "optional"),
    ("bad_debt", "Write off a bad debt", "إعدام دين معدوم", "Journal Voucher", "role:bad_debts", "role:ar", "required", "optional"),
    ("doubtful", "Provision for doubtful debts", "مخصص ديون مشكوك فيها", "Journal Voucher", "role:bad_debts", "role:doubtful_allowance", "optional", "none"),
    ("lg_margin", "Letter of guarantee — cash margin", "غطاء خطاب ضمان", "Journal Voucher", "subtype:other_current_asset", "subtype:cash", "optional", "optional"),
    ("tax_accrual", "Income tax accrual", "إثبات ضريبة الدخل", "Journal Voucher", "role:income_tax", "role:income_tax_payable", "none", "none"),
    ("vat_offset", "VAT settlement (offset input VAT)", "تسوية ضريبة القيمة المضافة", "Journal Voucher", "role:vat_output", "role:vat_input", "none", "none"),
    ("vat_pay", "Pay VAT to the Tax Authority", "سداد ضريبة القيمة المضافة", "Journal Voucher", "role:vat_output", "subtype:cash", "none", "none"),
    ("wht_pay", "Pay withholding tax to the Tax Authority", "سداد ضريبة الخصم والتحصيل", "Journal Voucher", "role:wht_payable", "subtype:cash", "none", "none"),
    ("wht_offset", "Offset client-withheld tax against income tax", "خصم ضريبة الخصم من ضريبة الدخل", "Journal Voucher", "role:income_tax_payable", "role:wht_receivable", "none", "none"),
    ("legal_reserve", "Transfer to legal reserve", "تجنيب الاحتياطي القانوني", "Reserve Transfer", "role:retained", "subtype:reserves", "none", "none"),
    ("investment", "Buy an investment", "شراء استثمار", "Journal Voucher", "subtype:investment", "subtype:cash", "optional", "none"),
]
ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
SIDE_RE = re.compile(r"^(|[\w.-]{1,20}|role:[a-z_]+|subtype:[a-z_,]+|type:[A-Za-z,]+)$")


def defaults():
    return [{"id": i, "name": n, "name_ar": ar, "kind": k, "debit": dr, "credit": cr, "party": pa, "bu": bu,
             "active": True, "builtin": True} for i, n, ar, k, dr, cr, pa, bu in BUILTIN]


def load(db):
    raw = (db.setting("entry_types") or "").strip()
    if not raw:
        return defaults()
    try:
        types = json.loads(raw)
    except ValueError:
        return defaults()
    have = {t.get("id") for t in types}
    removed = set(json.loads(db.setting("entry_types_removed") or "[]"))
    # built-ins added by later versions appear unless they were removed on purpose
    return types + [t for t in defaults() if t["id"] not in have and t["id"] not in removed]


def resolve_side(db, spec, accounts=None):
    """{"fixed": code} or {"choose": [codes], "default": code} for one side of an entry type."""
    accounts = accounts if accounts is not None else db.q("SELECT code, type, subtype, role, active FROM accounts ORDER BY code")
    live = [a for a in accounts if a["active"]]
    spec = (spec or "").strip()
    if spec.startswith("role:"):
        a = next((a for a in accounts if a["role"] == spec[5:]), None)
        return {"fixed": a["code"]} if a else {"choose": [a["code"] for a in live], "default": "", "missing": spec[5:]}
    if spec.startswith("subtype:"):
        subs = spec[8:].split(",")
        codes = [a["code"] for a in live if a["subtype"] in subs]
        return {"choose": codes, "default": codes[0] if codes else ""}
    if spec.startswith("type:"):
        types = spec[5:].split(",")
        codes = [a["code"] for a in live if a["type"] in types]
        return {"choose": codes, "default": codes[0] if codes else ""}
    if spec:
        return {"fixed": spec} if any(a["code"] == spec for a in accounts) else {"choose": [a["code"] for a in live], "default": ""}
    return {"choose": [a["code"] for a in live], "default": ""}


def for_client(db):
    """Entry types with each side resolved to account codes (what the forms need)."""
    accounts = db.q("SELECT code, type, subtype, role, active FROM accounts ORDER BY code")
    return [{**t, "dr": resolve_side(db, t["debit"], accounts), "cr": resolve_side(db, t["credit"], accounts)}
            for t in load(db)]


def save(db, types):
    if not isinstance(types, list):
        raise ValidationError("Entry types must be a list")
    out, seen = [], set()
    for t in types:
        tid = text(t.get("id")).lower() or re.sub(r"[^a-z0-9]+", "_", text(t.get("name")).lower()).strip("_")[:40]
        if not ID_RE.match(tid or ""):
            raise ValidationError(f"Bad entry type id {tid!r}")
        if tid in seen:
            tid = f"{tid}_{len(seen)}"
        seen.add(tid)
        name = text(t.get("name"), "entry type name", required=True, maxlen=80)
        for side in ("debit", "credit"):
            if not SIDE_RE.match(text(t.get(side))):
                raise ValidationError(f"{name}: bad {side} account setting")
        out.append({"id": tid, "name": name, "name_ar": text(t.get("name_ar"), maxlen=80),
                    "kind": text(t.get("kind"), maxlen=60) or name, "debit": text(t.get("debit")), "credit": text(t.get("credit")),
                    "party": t.get("party") if t.get("party") in ("none", "optional", "required") else "optional",
                    "bu": t.get("bu") if t.get("bu") in ("none", "optional", "required") else "optional",
                    "active": t.get("active") not in (False, 0, "0", "false"),
                    "builtin": tid in {b[0] for b in BUILTIN}})
    removed = sorted({b[0] for b in BUILTIN} - {t["id"] for t in out})
    db.set_setting("entry_types", json.dumps(out, ensure_ascii=False))
    db.set_setting("entry_types_removed", json.dumps(removed))
    return out


def kinds(db):
    return [t["kind"] for t in load(db)]


def post(db, tid, d, user=""):
    t = next((t for t in load(db) if t["id"] == tid), None)
    if not t:
        raise ValidationError("Unknown entry type")
    if not t.get("active", True):
        raise ValidationError(f"“{t['name']}” is switched off in Settings")
    amount = acc.num(d.get("amount"), "amount", allow_zero=False)
    codes = {}
    for side in ("debit", "credit"):
        r = resolve_side(db, t[side])
        if "fixed" in r:
            codes[side] = r["fixed"]
        else:
            chosen = text(d.get(side + "_account"))
            if r.get("missing"):
                raise ValidationError(f"No account holds the '{r['missing']}' role. Assign it in the chart of accounts.")
            if chosen not in r["choose"]:
                raise ValidationError(f"Choose the {side} account")
            codes[side] = chosen
    party = text(d.get("party"))
    if t["party"] == "required" and not party:
        raise ValidationError("Enter the party")
    bu = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=t["bu"] == "required")
    return acc.post_entry(db, date=d.get("date"), kind=t["kind"], memo=text(d.get("memo")) or t["name"], bu_id=bu,
                          party=party if t["party"] != "none" else "", ref=text(d.get("ref")),
                          lines=[{"account": codes["debit"], "debit": amount}, {"account": codes["credit"], "credit": amount}],
                          meta={"entry_type": t["id"]}, user=user)


# ---------------------------------------------------------------- "+ New" menu
# key, English, Arabic, permission needed (module, level)
MENU_ITEMS = [
    ("doc:direct_cost", "Direct Costs (Cost of Revenue)", "التكاليف المباشرة", "purchasing.costs"),
    ("doc:expense", "Operating Expenses", "المصروفات التشغيلية", "purchasing.expenses"),
    ("doc:payment", "Payment Vouchers", "سندات الصرف", "purchasing.payments"),
    ("doc:receipt", "Receipt Vouchers", "سندات القبض", "sales.receipts"),
    ("doc:invoice", "Sales Invoices / Progress Billings", "فواتير المبيعات / المستخلصات", "sales.invoices"),
    ("doc:payroll", "Payroll", "الأجور", "payroll"),
    ("doc:stock_move", "Stock Movements", "حركات المخزون", "stock.moves"),
    ("doc:journal", "Journal Vouchers", "قيود اليومية", "accounting.journal"),
    ("doc:bu", "Business Units (Cost Centres)", "وحدات الأعمال", "business_units.units"),
    ("doc:offer", "Offers", "العروض", "sales.offers"),
    ("eq:contribute", "Capital contribution", "زيادة رأس المال (مساهمة شريك)", "partners.transactions"),
    ("eq:withdraw", "Capital withdrawal", "تخفيض رأس المال (سحب شريك)", "partners.transactions"),
    ("eq:loan", "Loan from a partner", "قرض من شريك", "partners.transactions"),
    ("eq:repay", "Repay a partner's loan / interest", "سداد قرض شريك / فوائده", "partners.transactions"),
    ("eq:interest", "Interest on a partner's loan", "فوائد قرض شريك", "partners.transactions"),
    ("eq:drawing", "Partner drawing", "مسحوبات شريك", "partners.transactions"),
    ("eq:distribute", "Profit distribution", "توزيع أرباح", "partners.transactions"),
    ("eq:transfer", "Transfer / sell a share", "تنازل / بيع حصة", "partners.ownership"),
    ("eq:admit", "Admit a new partner", "دخول شريك جديد", "partners.ownership"),
    ("eq:exit", "Partner retirement / buy-out", "تخارج شريك", "partners.ownership"),
    ("asset:new", "New fixed asset", "أصل ثابت جديد", "assets.register"),
    ("asset:depr", "Run depreciation", "احتساب الإهلاك", "assets.depreciation"),
    ("po:new", "New purchase order", "أمر شراء جديد", "purchasing.orders"),
    ("sub:new", "New subscription / maintenance contract", "اشتراك / عقد صيانة جديد", "shop.subscriptions"),
    ("loan:new", "New loan (taken or given)", "قرض جديد (مأخوذ أو ممنوح)", "accounting.loans"),
    ("loan:pay", "Loan payment / collection", "سداد / تحصيل قسط قرض", "accounting.loans"),
    ("custody:new", "Give cash custody", "صرف عهدة نقدية", "purchasing.custody"),
    ("sale:new", "New sale (cash / installments)", "بيع جديد (كاش / تقسيط)", "shop.orders"),
    ("sale:collect", "Collect an installment", "تحصيل قسط", "shop.installments"),
    ("product:new", "New product / unit", "منتج / وحدة جديدة", "shop.products"),
    ("recurring:new", "New recurring entry (rent, salaries, subscriptions…)", "قيد متكرر جديد (إيجار، رواتب، اشتراكات…)", "accounting.recurring"),
    ("bank:import", "Import a bank statement", "استيراد كشف حساب بنك", "accounting.bankrec"),
    ("contact:new", "New contact (supplier, subcontractor, driver…)", "جهة اتصال جديدة (مورد، مقاول، سائق…)", "purchasing.contacts"),
    ("custody:settle", "Custody expense report (settlement)", "تسوية عهدة (تقرير مصروفات)", "purchasing.custody"),
    ("site:task", "New project task", "مهمة مشروع جديدة", "projects.tasks"),
    ("site:contract", "New contract (client)", "عقد جديد (عميل)", "contracts.contracts"),
    ("site:subcontract", "New subcontract", "عقد مقاول باطن جديد", "contracts.contracts"),
    ("site:prq", "New purchase requisition", "طلب شراء جديد", "purchasing.requests"),
    ("site:usage", "Equipment hours / fuel", "ساعات تشغيل / وقود معدة", "equipment.usage"),
    ("site:maint", "Equipment maintenance done", "صيانة معدة تمت", "equipment.maintenance"),
    ("site:attendance", "Today's attendance", "حضور اليوم", "payroll.attendance"),
    ("site:ereceipt", "New e-receipt", "إيصال إلكتروني جديد", "tax.ereceipt"),
]
DEFAULT_MENU = ["po:new", "doc:direct_cost", "doc:expense", "doc:payment", "doc:receipt", "doc:invoice", "doc:payroll",
                "doc:stock_move", "doc:journal", "eq:contribute", "eq:distribute", "asset:new", "asset:depr", "loan:new", "loan:pay",
                "custody:new", "custody:settle", "contact:new", "sale:new", "sale:collect", "sub:new", "doc:bu", "doc:offer", "site:prq", "site:task", "site:subcontract", "site:usage", "site:attendance"]


def menu(db):
    """The "+ New" menu: [{key, show}] in order. Items not placed yet are listed after, hidden."""
    try:
        saved = json.loads(db.setting("new_menu") or "[]")
    except ValueError:
        saved = []
    if not saved:
        saved = [{"key": k, "show": True} for k in DEFAULT_MENU]
    keys = [m["key"] for m in saved]
    extra = [k for k, *_ in MENU_ITEMS if k not in keys] + [f"entry:{t['id']}" for t in load(db) if f"entry:{t['id']}" not in keys]
    return saved + [{"key": k, "show": False} for k in extra]


def save_menu(db, items):
    known = {k for k, *_ in MENU_ITEMS} | {f"entry:{t['id']}" for t in load(db)}
    out = [{"key": m["key"], "show": bool(m.get("show"))} for m in items or [] if isinstance(m, dict) and m.get("key") in known]
    db.set_setting("new_menu", json.dumps(out))
    return out
