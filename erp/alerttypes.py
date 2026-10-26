"""Which notifications each department (user group) and each person receives.

Every bell alert has a type (the catalogue below). A super administrator decides, per user group and if needed per
person, which types they get; the person's own choice wins over the group's, and anything not decided is shown. This
comes on top of permissions: nobody is told about a screen they cannot open.
Kept in the setting `alert_access` = {"groups": {group: {type: false}}, "users": {username: {type: true|false}}}.
"""
import json

# (type, section, English, Arabic)
CATALOG = [
    ("approvals", "entries", "Entries waiting for my approval", "قيود بانتظار اعتمادي"),
    ("approvals_back", "entries", "My entries sent back for changes", "قيودي المُعادة للتعديل"),
    ("recurring_due", "entries", "Recurring entries due", "قيود متكررة مستحقة"),
    ("depreciation_due", "entries", "Depreciation not booked", "إهلاك لم يُسجل"),
    ("rental_depreciation", "entries", "Rental equipment depreciation not booked", "إهلاك معدات التأجير لم يُسجل"),
    ("ereceipt_rejected", "entries", "E-receipts rejected", "إيصالات إلكترونية مرفوضة"),
    ("ereceipt_unsent", "entries", "E-receipts not sent", "إيصالات لم تُرسل"),
    ("rent_billing", "sales", "Rental contracts to bill", "عقود تأجير مستحقة الفوترة"),
    ("installments_overdue", "sales", "Overdue installments", "أقساط متأخرة"),
    ("cheques_due", "sales", "Customer cheques due", "شيكات عملاء مستحقة"),
    ("followups", "sales", "Follow-ups due", "متابعات مستحقة"),
    ("desk", "sales", "Guest messages on the online desk", "رسائل الزوار على المكتب الإلكتروني"),
    ("desk_err", "sales", "Online desk items not added", "طلبات المكتب الإلكتروني التي لم تُضف"),
    ("pr_approve", "purchasing", "Purchase requests to approve", "طلبات شراء للاعتماد"),
    ("pr_order", "purchasing", "Approved requests not ordered", "طلبات معتمدة لم يُصدر لها أمر شراء"),
    ("bills", "purchasing", "Subscriptions and bills due", "اشتراكات وفواتير مستحقة"),
    ("price_capture", "purchasing", "Prices used in the books not in the price list", "أسعار مستخدمة غير موجودة في قائمة الأسعار"),
    ("tasks_overdue", "projects", "Overdue project tasks", "مهام مشروعات متأخرة"),
    ("phases_late", "projects", "Project phases past their finish date", "مراحل تجاوزت موعد انتهائها"),
    ("contracts_expired", "projects", "Contracts past their end date", "عقود تجاوزت تاريخ انتهائها"),
    ("contracts_ending", "projects", "Contracts ending within 30 days", "عقود تنتهي خلال 30 يوماً"),
    ("variations", "projects", "Variation orders to decide", "أوامر تغيير بانتظار القرار"),
    ("sub_certificates", "projects", "Subcontractor certificates to approve or post", "مستخلصات مقاولي باطن للاعتماد أو الترحيل"),
    ("retention_due", "projects", "Retention due for release", "محتجزات مستحقة الرد"),
    ("stock_counts", "operations", "Stock counts open", "جرد مفتوح"),
    ("maintenance", "operations", "Equipment maintenance due or overdue", "صيانة معدات مستحقة أو متأخرة"),
    ("hr_requests", "team", "Employee requests awaiting approval", "طلبات موظفين بانتظار الاعتماد"),
    ("checkin_self", "team", "I am online without checking in", "أنا متصل دون تسجيل الحضور"),
    ("checkin_idle", "team", "Staff online without checking in", "موظفون متصلون دون تسجيل الحضور"),
    ("checkin_flags", "team", "Check-ins with location warnings", "تسجيلات حضور بها تحذيرات موقع"),
    ("books_check", "finance", "Books checks that do not tie", "فحوصات دفاتر لم تتطابق"),
    ("funding_limit", "finance", "Funding partner's facility near its limit", "تمويل الشريك الممول قريب من حده"),
    ("cash_negative", "finance", "Cash or bank went below zero", "رصيد نقدية أو بنك بالسالب"),
    ("review_flags", "finance", "Other figures to review", "أرقام أخرى تحتاج مراجعة"),
    ("my_alerts", "mine", "My own alert rules", "تنبيهاتي الخاصة"),
]
SECTIONS = [("entries", "Entries and accounting", "القيود والمحاسبة"), ("sales", "Sales and customers", "المبيعات والعملاء"),
            ("purchasing", "Purchasing and costs", "المشتريات والتكاليف"), ("projects", "Projects and contracts", "المشروعات والعقود"),
            ("operations", "Stock and equipment", "المخزون والمعدات"), ("team", "Team and HR", "الفريق والموارد البشرية"),
            ("finance", "Finance and books review", "المالية ومراجعة الدفاتر"), ("mine", "Personal", "شخصي")]
TYPES = {k for k, *_ in CATALOG}
SECTION_OF = {k: sec for k, sec, *_ in CATALOG}

# what each starting department receives until a super administrator changes it: its own work, plus what is always
# personal (its approvals, entries sent back, its own check-in and alert rules)
PERSONAL = {"approvals", "approvals_back", "checkin_self", "my_alerts"}
DEPT_DEFAULTS = {
    "management": None,                                               # everything
    "finance": {"sections": {"entries", "finance", "purchasing"}, "types": {"installments_overdue", "cheques_due", "retention_due"}},
    "sales": {"sections": {"sales"}, "types": {"rent_billing"}},
    "projects": {"sections": {"projects", "operations"}, "types": {"rent_billing", "pr_approve", "pr_order"}},
    "purchasing": {"sections": {"purchasing", "operations"}, "types": {"sub_certificates"}},
    "hr": {"sections": {"team"}, "types": set()},
    "general": None,                                                  # not in a department: everything, as before
}


def default_on(group, t):
    """Shown to this department when nothing has been decided for it."""
    d = DEPT_DEFAULTS.get(group or "general", None) if (group or "general") in DEPT_DEFAULTS else None
    if d is None or t in PERSONAL:
        return True
    return SECTION_OF.get(t) in d["sections"] or t in d["types"]

_BY_KIND = {"feed": "my_alerts"}
_BY_HREF = (("#/rental/", "rent_billing"), ("#/installments?status=overdue", "installments_overdue"), ("#/installments", "cheques_due"),
            ("#/recurring", "recurring_due"), ("#/depreciation", "depreciation_due"), ("#/rentfleet", "rental_depreciation"),
            ("#/tasks", "tasks_overdue"), ("#/projects", "phases_late"), ("#/variations", "variations"),
            ("#/certificates", "sub_certificates"), ("#/purchase-requests?status=submitted", "pr_approve"),
            ("#/purchase-requests", "pr_order"), ("#/stock-counts", "stock_counts"), ("#/maintenance", "maintenance"),
            ("#/ereceipts?status=rejected", "ereceipt_rejected"), ("#/ereceipts", "ereceipt_unsent"),
            ("#/partner-exit", "funding_limit"), ("#/prices?ratecap", "price_capture"), ("#/ledger?account=", "cash_negative"))


def classify(r):
    """The type of one bell alert (its kind, else where it leads)."""
    if r.get("type") in TYPES:
        return r["type"]
    k = r.get("kind") or ""
    if k in TYPES:
        return k
    if k in _BY_KIND:
        return _BY_KIND[k]
    h, en = r.get("href") or "", r.get("en") or ""
    if h.startswith("#/contracts"):
        return "contracts_expired" if en.startswith("Contracts past") else "retention_due" if en.startswith("Retention") \
            else "contracts_ending"
    if h.startswith("#/model-audit"):
        return "books_check" if en.startswith("Books check") else "review_flags"
    for prefix, t in _BY_HREF:
        if h.startswith(prefix):
            return t
    return "review_flags" if k == "review" else ""


def load(db):
    try:
        v = json.loads(db.setting("alert_access") or "{}")
    except ValueError:
        v = {}
    return {"groups": v.get("groups") if isinstance(v.get("groups"), dict) else {},
            "users": v.get("users") if isinstance(v.get("users"), dict) else {}}


def clean(v):
    out = {"groups": {}, "users": {}}
    for part in ("groups", "users"):
        for who, rules in ((v or {}).get(part) or {}).items():
            if not isinstance(rules, dict):
                continue
            r = {t: bool(on) for t, on in rules.items() if t in TYPES and on is not None}
            if r:
                out[part][str(who)[:120]] = r
    return out


def allowed_fn(db, user):
    """t -> True / False for this person: their own setting, else their department's, else the department's default.
    Super administrators receive everything unless they switch a type off for themselves."""
    rules = load(db)
    mine = rules["users"].get(user.get("username") or "", {})
    gkey = user.get("group_key") or "general"
    group = rules["groups"].get(gkey, {})

    def ok(t):
        if not t:
            return True
        if t in mine:
            return mine[t]
        if user.get("is_super"):
            return True
        return group[t] if t in group else default_on(gkey, t)
    return ok


def filter_rows(db, user, rows):
    ok = allowed_fn(db, user)
    out = []
    for r in rows:
        r["type"] = classify(r)
        if ok(r["type"]):
            out.append(r)
    return out
