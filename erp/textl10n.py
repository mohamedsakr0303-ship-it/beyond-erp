"""Texts the app writes into the books (journal memos, schedule labels, bank lines…) shown in the reader's language.

The books keep one wording (English, the app's own); screens and reports translate it when they show it, so every
person sees their language, entries made before a language was chosen are covered, and adding a language means adding
a column here. Names, codes and numbers inside a text are kept as they are (they are data).
"""
import re

# (English pattern, Arabic) — {1}, {2}… are the parts the pattern captured
_P = [
    (r"Invoice — (.+)", "فاتورة — {1}"),
    (r"Fees paid — (.+)", "أتعاب مدفوعة — {1}"),
    (r"Payment on (.+)", "سداد على {1}"),
    (r"Payment to (.+)", "دفعة إلى {1}"),
    (r"Advance to (.+)", "دفعة مقدمة إلى {1}"),
    (r"Repayment to (.+)", "سداد إلى {1}"),
    (r"Collection from (.+)", "تحصيل من {1}"),
    (r"Loan received from (.+)", "قرض مستلم من {1}"),
    (r"Loan given to (.+)", "قرض ممنوح إلى {1}"),
    (r"Loan from partner — (.+)", "قرض من الشريك — {1}"),
    (r"Loan repaid — (.+)", "سداد قرض — {1}"),
    (r"Loan (\S+) — (.+)", "قرض {1} — {2}"),
    (r"Custody given to (.+)", "عهدة مسلّمة إلى {1}"),
    (r"Custody expense report — (.+)", "تقرير مصروفات العهدة — {1}"),
    (r"Replenish custody (\S+) \((.+)\) after report (.+)", "تعويض العهدة {1} ({2}) بعد التقرير {3}"),
    (r"Security deposit received from (.+)", "تأمين مستلم من {1}"),
    (r"Retention released to (.+)", "رد تأمين الأعمال إلى {1}"),
    (r"Certificate (\S+) — (.+)", "مستخلص {1} — {2}"),
    (r"Cheque (\S+) cleared — (.+)", "تحصيل الشيك {1} — {2}"),
    (r"Cheque (\S+) bounced — (.+)", "رد الشيك {1} — {2}"),
    (r"Cheque (\S+) (.+) due (\S+) — (.+)", "شيك {1} {2} يستحق {3} — {4}"),
    (r"(.+) — cheque (\S+) \((.+)\)", "{1} — شيك {2} ({3})"),
    (r"(.+) — (\S+) installment", "{1} — قسط {2}"),
    (r"Installment (\d+)/(\d+)", "القسط {1}/{2}"),
    (r"Down payment", "الدفعة المقدمة"),
    (r"Full payment", "السداد الكامل"),
    (r"Installment interest earned — (.+)", "فوائد تقسيط مكتسبة — {1}"),
    (r"Installment interest \(deferred\) — (.+)", "فوائد تقسيط مؤجلة — {1}"),
    (r"Cost of (.+)", "تكلفة {1}"),
    (r"Sale — e-receipt (.+)", "بيع — إيصال إلكتروني {1}"),
    (r"Return — e-receipt (.+)", "مرتجع — إيصال إلكتروني {1}"),
    (r"Received ([\d.,]+) (\S+) (.+) → (.+)", "استلام {1} {2} {3} ← {4}"),
    (r"Issued ([\d.,]+) (\S+) (.+) from (.+)", "صرف {1} {2} {3} من {4}"),
    (r"Transfer ([\d.,]+) (.+): (.+) → (.+)", "تحويل {1} {2}: {3} ← {4}"),
    (r"Scrapped: (.+) × ([\d.,]+)", "إعدام: {1} × {2}"),
    (r"Count adjustment — (.+) @ (.+)", "تسوية جرد — {1} @ {2}"),
    (r"Stocktake (\S+) — (.+)", "جرد {1} — {2}"),
    (r"Stocktake (\S+)", "جرد {1}"),
    (r"Depreciation of rental equipment to (\S+)", "إهلاك معدات التأجير حتى {1}"),
    (r"Depreciation (.+) — (\S+) to (\S+)", "إهلاك {1} — من {2} إلى {3}"),
    (r"Depreciation (.+)", "إهلاك {1}"),
    (r"Repair — (.+)", "إصلاح — {1}"),
    (r"Service — (.+)", "صيانة — {1}"),
    (r"(.+) — ([\d.,]+) h, fuel ([\d.,]+)(.*)", "{1} — {2} ساعة، وقود {3}{4}"),
    (r"(.+) — ([\d.,]+) h", "{1} — {2} ساعة"),
    (r"Payroll (\S+) — (.+)", "رواتب {1} — {2}"),
    (r"Payroll (\S+) \(as (\S+): (.+)\)", "رواتب {1} (مثل {2}: {3})"),
    (r"Reversal of #(\d+): (.*)", "عكس القيد #{1}: {2}"),
    (r"(\S+) — (.+)'s share", "{1} — حصة {2}"),
    (r"(\S+) — (.+) settled \(([\d.,]+) rejected\)", "{1} — {2} تمت التسوية (مرفوض {3})"),
    (r"(\S+) — (.+) settled", "{1} — {2} تمت التسوية"),
    (r"(\S+) — (.+) earned (\S+)", "{1} — {2} مكتسب {3}"),
    (r"(.+) transfers ([\d.,]+%) to (.+)", "{1} يحوّل {2} إلى {3}"),
    (r"Pieces lost / damaged on (\S+) \((.+)\) — (.+)", "قطع مفقودة / تالفة على {1} ({2}) — {3}"),
    (r"Rent: (.+) (\d{4}-\d\d-\d\d) to (\d{4}-\d\d-\d\d) — average ([\d.,]+) (\S+) × (\d+) days",
     "إيجار: {1} من {2} إلى {3} — متوسط {4} {5} × {6} يوم"),
    (r"Extra visit (\S+) — (.+)", "زيارة إضافية {1} — {2}"),
    (r"Funding partner — his own money in the company brought to (.+)", "الشريك الممول — رصيد أمواله في الشركة أصبح {1}"),
    (r"From purchase request (\S+) \(quote of (\S+)\)", "من طلب الشراء {1} (عرض بتاريخ {2})"),
    (r"Created by the automation “(.+)”", "أُنشئت بواسطة القاعدة «{1}»"),
    (r"Work done on (.+), not yet invoiced", "أعمال منفذة في {1} لم تُفوتر بعد"),
    (r"(.+) \(demo file\)", "{1} (ملف تجريبي)"),
    (r"BANK CHARGES", "مصاريف بنكية"), (r"CREDIT INTEREST", "فوائد دائنة"),
    (r"broken in the store", "تالف في المخزن"),
    (r"Taxpayer activity code is missing — Settings → E-invoicing", "كود نشاط الممول غير موجود — الإعدادات ← الفاتورة الإلكترونية"),
    (r"The POS device serial number is missing — Settings → E-invoicing → Point of sale",
     "الرقم المسلسل لجهاز نقطة البيع غير موجود — الإعدادات ← الفاتورة الإلكترونية ← نقطة البيع"),
]
_RX = [(re.compile(r"^" + p + r"$", re.S), ar) for p, ar in _P]
# fixed values the app stores (entry kinds, billing bases, methods, payroll types, statuses) — the same words as the screens
VALUES = {
    "Care Package": "باقة رعاية", "Insurance Claim": "مطالبة تأمين", "Medical Invoice": "فاتورة طبية", "Opening balance": "رصيد افتتاحي", "Provider Fees": "أتعاب مقدمي الخدمة", "Revenue Recognition": "إثبات إيراد",
    "Direct Cost": "تكلفة مباشرة", "Operating Expense": "مصروف تشغيلي", "Payroll Accrual": "استحقاق أجور",
    "Payment Voucher": "سند صرف", "Receipt Voucher": "سند قبض", "Sales Invoice": "فاتورة مبيعات",
    "Capital Contribution": "مساهمة رأسمالية", "Capital Withdrawal": "تخفيض رأس المال", "Owner Drawing": "مسحوبات شريك",
    "Partner Loan": "قرض من شريك", "Partner Loan Repayment": "سداد قرض شريك", "Partner Loan Interest": "فوائد قرض شريك",
    "Share Transfer": "تنازل عن حصة", "Partner Admission": "دخول شريك", "Partner Exit": "خروج شريك",
    "Profit Distribution": "توزيع أرباح", "Reserve Transfer": "تكوين احتياطي", "Asset Purchase": "شراء أصل",
    "Depreciation": "إهلاك", "Asset Disposal": "استبعاد أصل", "Journal Voucher": "قيد يومية",
    "Goods Receipt": "استلام بضاعة", "Material Issue": "صرف مواد", "Stock Transfer": "تحويل مخزني",
    "Stock Adjustment": "تسوية جرد", "Reversing Entry": "قيد عكسي", "Loan Received": "استلام قرض",
    "Loan Repayment": "سداد قرض", "Loan Given": "صرف قرض ممنوح", "Loan Collection": "تحصيل قرض",
    "Loan Interest": "فوائد قرض", "Loan Charges": "مصاريف قرض", "Loan Forgiven": "إعفاء من قرض",
    "Loan Written Off": "إعدام قرض", "Custody Issued": "صرف عهدة", "Custody Settlement": "تسوية عهدة",
    "Custody Return": "رد عهدة", "Custody Deduction": "خصم عهدة", "Rental Equipment Purchase": "شراء معدات تأجير",
    "Rental Equipment Loss": "خسائر معدات تأجير", "Rental Equipment Depreciation": "إهلاك معدات تأجير", "Rental Deposit": "تأمين تأجير",
    "Installment Interest": "فوائد تقسيط", "Advance Applied": "تسوية دفعات مقدمة", "Cost of Sales": "تكلفة مبيعات",
    "Cheque Received": "استلام شيك", "Cheque Cleared": "صرف شيك", "Cheque Bounced": "شيك مرتد",
    "Sale Refund": "رد مبلغ بيع", "Bank Entry": "قيد بنكي", "Subcontractor Certificate": "مستخلص مقاول باطن",
    "Equipment Usage": "تشغيل معدات", "Equipment Maintenance": "صيانة معدات", "E-Receipt": "إيصال إلكتروني",
    "Cost-Plus %": "التكلفة + نسبة", "Fixed Price": "سعر ثابت (مقطوعية)", "Flexible / Settled": "مرن / بالتسوية",
    "Live Fee (Paid vs Costs)": "نسبة حية — مدفوعات العميل مقابل التكاليف الفعلية", "Bank Transfer": "تحويل بنكي", "Cash": "نقدي",
    "Cheque": "شيك", "Credit Card": "بطاقة ائتمان", "Instapay": "إنستاباي",
    "Direct Labour (Cost of Revenue)": "عمالة مباشرة (تكلفة إيراد)", "Indirect Labour (Cost-Centre Overhead)": "عمالة غير مباشرة (مصروفات مركز تكلفة)", "Administrative Salaries (Shared Overhead)": "رواتب إدارية (مصروفات مشتركة)",
    "Receipt": "استلام", "Issue": "صرف", "Transfer": "تحويل",
    "Adjustment": "تسوية جرد", "In Progress": "قيد التنفيذ", "Completed": "مكتمل",
    "On Hold": "متوقف", "Supplier Discount": "خصم مورد", "Cash Transfer": "تحويل نقدي",
    "Cancelled": "ملغي", "Planned": "مخطط", "Draft": "مسودة",
    "Posted": "مُرحّل", "Void": "ملغى",
}
# fixed wording the app shows from its own lists
PHRASES = {
    "most capable": "الأقوى", "faster, lower cost": "أسرع وأقل تكلفة", "quickest, lowest cost": "الأسرع والأقل تكلفة",
    "OpenRouter (hundreds of models)": "OpenRouter (مئات النماذج)", "Ollama (local, on this computer)": "Ollama (محلي، على هذا الجهاز)",
    "LM Studio (local)": "LM Studio (محلي)", "Other — any OpenAI-compatible address": "أخرى — أي عنوان متوافق مع OpenAI",
    "Main Store": "المخزن الرئيسي", "General": "عام", "Down payment": "الدفعة المقدمة", "Full payment": "السداد الكامل",
}
# units of measure as people type them
UNITS = {"piece": "قطعة", "pieces": "قطعة", "pcs": "قطعة", "unit": "وحدة", "units": "وحدة", "set": "طقم", "bag": "شكارة",
         "drum": "جركن", "pair": "زوج", "pack": "عبوة", "box": "علبة", "m2": "م²", "m²": "م²", "m3": "م³", "m³": "م³", "m": "م",
         "ton": "طن", "kg": "كجم", "l": "لتر", "L": "لتر", "trip": "نقلة", "visit": "زيارة", "point": "نقطة", "lump sum": "مقطوعية",
         "Lump Sum": "مقطوعية", "Lot": "مقطوعية", "LS": "مقطوعية", "hour": "ساعة", "hours": "ساعة", "day": "يوم", "days": "يوم",
         "month": "شهر", "months": "شهر", "frame": "شباك", "Task": "مهمة", "session": "جلسة", "shift": "وردية"}


def text(v, lang):
    """One stored text in the reader's language ("; "-joined messages are translated one by one)."""
    if lang != "ar" or not isinstance(v, str) or not v or not re.search(r"[A-Za-z]", v):
        return v
    if v in VALUES:
        return VALUES[v]
    if v in PHRASES:
        return PHRASES[v]
    if v in UNITS:
        return UNITS[v]
    m = re.fullmatch(r"#(\d+) (.+)", v)              # "#12 Direct Cost" (an entry and its kind)
    if m and m.group(2) in VALUES:
        return f"#{m.group(1)} {VALUES[m.group(2)]}"
    name, sep, rest = v.rpartition(" — ")             # "<name> — <how it is described>"
    if sep and rest in PHRASES:
        return f"{name} — {PHRASES[rest]}"
    if "; " in v:
        return "؛ ".join(text(x, lang) for x in v.split("; "))
    for rx, ar in _RX:
        m = rx.match(v)
        if m:
            out = ar
            for i, g in enumerate(m.groups(), start=1):
                out = out.replace("{%d}" % i, text(g, lang) if g and g != v else (g or ""))
            return out
    return v


# the fields of an API answer that hold such texts (anything else — names, codes, user notes — is left alone)
FIELDS = {"memo", "description", "label", "error", "note", "notes", "eta_error", "title", "unit"}


def walk(obj, lang):
    """Translate those fields everywhere in an API answer."""
    if lang != "ar":
        return obj
    if isinstance(obj, dict):
        return {k: (text(v, lang) if k in FIELDS and isinstance(v, str) else walk(v, lang)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(x, lang) for x in obj]
    return obj


def table(t, lang):
    """A report (title, columns, rows of cells) with the app's own texts in the reader's language."""
    if lang != "ar":
        return t
    for r in t.get("rows") or []:
        if isinstance(r, dict) and isinstance(r.get("cells"), list):
            r["cells"] = [text(c, lang) if isinstance(c, str) else c for c in r["cells"]]
    return t


_ERR = []       # (regex, Arabic) for the server's messages, longest fixed text first so a general one never wins


def error(msg, lang, depth=0):
    """A message the server sends back (validation, permission, not found…) in the reader's language."""
    if lang != "ar" or not isinstance(msg, str) or not msg or not re.search(r"[A-Za-z]", msg):
        return msg
    from . import errors_ar
    if msg in errors_ar.MESSAGES:
        return errors_ar.MESSAGES[msg]
    field = errors_ar.FIELDS.get(msg) or errors_ar.FIELDS.get(msg[:1].lower() + msg[1:])
    if field:
        return field
    if not _ERR:
        for en, ar in sorted(errors_ar.MESSAGES.items(), key=lambda p: -len(p[0].replace("{}", ""))):
            if "{}" in en:
                _ERR.append((re.compile("^" + re.escape(en).replace(r"\{\}", "(.*?)") + "$", re.S), ar))
    for rx, ar in _ERR:
        m = rx.match(msg)
        if m:
            out = ar
            for i, g in enumerate(m.groups(), start=1):
                out = out.replace("{%d}" % i, error(g, lang, depth + 1) if depth < 2 and g != msg else g)
            return out
    return text(msg, lang)


# names the app gives its own starting records; renamed with the company's language while nobody has changed them
_SEEDS = [("warehouses", "name", "Main Store", "المخزن الرئيسي"), ("chat_rooms", "name", "General", "عام")]


def seed_names(db, lang):
    """The app's own starting records (main store, general chat room) named in the company's language."""
    for table, col, en, ar in _SEEDS:
        old, new = (en, ar) if lang == "ar" else (ar, en)
        try:
            db.conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new, old))
        except Exception:      # noqa: BLE001 — books without that table
            pass
