"""Client documents in the company's own layouts (stdlib only — works in the desktop, server and browser editions).

* Quotation / bill of quantities (BOQ) and invoice / progress extract: Excel (.xlsx) with live formulas and
  the same model rendered as a printable HTML page (PDF), matching the BEYOND BOQ / "Invoice 01" workbooks.
* Offers (design package, execution, supervision... any offer type): Word (.docx) and printable HTML (PDF),
  matching the BEYOND design-package template: logo + date band, ruled title, sections, signature footer.

Logo and watermark come from Settings -> Documents & branding, per kind of file (see branding.DOC_KINDS).
"""
import datetime as _dt
import html as _html
import io
import json
import re
import zipfile

from . import accounting as acc
from . import branding
from .accounting import ValidationError, text

# ---------------------------------------------------------------- starter offer types
DEFAULT_OFFER_TYPES = [
    {"name": "Design Package", "lang": "en", "title": "DESIGN PACKAGE :{project} ({client})",
     "intro": "Dear {client},\nKindly find this quotation (design package) for\n{project}",
     "sections": [
         {"kind": "text", "heading": "INTERIOR DESIGN",
          "body": "1. Architectural and Furniture Layout\n2. Mood Board (Look & Feel)\n3. 3D Renders"},
         {"kind": "pricing", "heading": "PROJECT COST", "style": "lines", "words": True,
          "items": [{"desc": "Total Cost", "amount": 60000}], "discount": 10000, "vat": 0},
         {"kind": "payment", "heading": "PAYMENT TERMS", "style": "lines",
          "milestones": [{"label": "Advance Payment", "pct": 60}, {"label": "after Renders submission", "pct": 20},
                         {"label": "after Final approval", "pct": 20}]},
         {"kind": "bank", "heading": "", "style": "lines"},
         {"kind": "text", "heading": "TIME DURATION", "bold": True,
          "body": "1. Time duration for the drawings of the unit is 30 working days.\n2. Execution fees is 20% on total "
                  "project cost according to the quotation (Optional).\n3. Design is subject to 1 minor Revision."}],
     "closing": ""},
    {"name": "باقة التصميم (عربي)", "lang": "ar", "title": "باقة التصميم : {project} ({client})",
     "intro": "السيد / {client}\nتحية طيبة وبعد،\nنتشرف بأن نقدم لسيادتكم عرض سعر (باقة التصميم) لـ\n{project}",
     "sections": [
         {"kind": "text", "heading": "التصميم الداخلي",
          "body": "1. التخطيط المعماري وتوزيع الأثاث\n2. لوحة الإلهام (الطابع والإحساس العام)\n3. تصميمات ثلاثية الأبعاد (3D Renders)"},
         {"kind": "pricing", "heading": "تكلفة المشروع", "style": "lines", "words": True,
          "items": [{"desc": "التكلفة الإجمالية", "amount": 60000}], "discount": 10000, "vat": 0},
         {"kind": "payment", "heading": "شروط الدفع", "style": "lines",
          "milestones": [{"label": "دفعة مقدمة", "pct": 60}, {"label": "بعد تسليم التصميمات ثلاثية الأبعاد", "pct": 20},
                         {"label": "بعد الاعتماد النهائي", "pct": 20}]},
         {"kind": "bank", "heading": "", "style": "lines"},
         {"kind": "text", "heading": "المدة الزمنية", "bold": True,
          "body": "1. مدة تنفيذ رسومات الوحدة 30 يوم عمل.\n2. أتعاب التنفيذ 20% من إجمالي تكلفة المشروع طبقاً للمقايسة "
                  "(اختياري).\n3. يشمل التصميم تعديلاً بسيطاً واحداً."}],
     "closing": ""},
    {"name": "Execution / Fit-out", "lang": "en", "title": "EXECUTION OFFER : {project}",
     "intro": "Dear {client},\nFurther to our site visit and the approved design, please find below our offer for the "
              "finishing works of {project}.",
     "sections": [
         {"kind": "text", "heading": "SCOPE OF WORKS",
          "body": "1. Demolition, building and plastering works\n2. Plumbing, electrical and HVAC works\n3. Flooring, "
                  "ceramics and porcelain\n4. Gypsum board and painting\n5. Joinery and finishing works as per the "
                  "attached BOQ"},
         {"kind": "pricing", "heading": "OFFER VALUE",
          "items": [{"desc": "Finishing works as per the attached bill of quantities", "amount": 0}], "discount": 0, "vat": 0},
         {"kind": "payment", "heading": "PAYMENT TERMS",
          "milestones": [{"label": "Advance payment on contract signing", "pct": 30},
                         {"label": "Progress payments against monthly extracts", "pct": 60},
                         {"label": "On handover", "pct": 10}]},
         {"kind": "text", "heading": "TERMS & CONDITIONS",
          "body": "1. Quantities are approximate; billing follows the quantities actually executed on site.\n2. Prices "
                  "are based on today's exchange rate; any change may affect material prices.\n3. Prices exclude VAT "
                  "and social insurance.\n4. This offer is valid for {validityDays} days from its date."},
         {"kind": "bank", "heading": "BANK DETAILS"},
         {"kind": "signature", "heading": "ACCEPTANCE",
          "body": "Client name: ____________________\nSignature: ____________________   Date: __________"}],
     "closing": "Best regards,\n{company}"},
    {"name": "Design Packages", "lang": "en", "title": "DESIGN PACKAGES :{project} ({client})",
     "intro": "Dear {client},\nKindly find our design packages for {project}. Choose the one that suits you best.",
     "sections": [
         {"kind": "packages", "heading": "PACKAGES", "style": "table", "chosen": -1, "vat": 0,
          "tiers": [{"name": "Essential", "price": 0,
                     "includes": "Architectural and furniture layout\nMood board (look & feel)\n1 revision"},
                    {"name": "Signature", "price": 0,
                     "includes": "Architectural and furniture layout\nMood board (look & feel)\n3D renders\n2 revisions"},
                    {"name": "Prestige", "price": 0,
                     "includes": "Architectural and furniture layout\nMood board (look & feel)\n3D renders\nShop drawings\n"
                                 "Site supervision visits\nUnlimited minor revisions"}]},
         {"kind": "payment", "heading": "PAYMENT TERMS",
          "milestones": [{"label": "Advance payment", "pct": 50}, {"label": "On delivery of the drawings", "pct": 50}]},
         {"kind": "bank", "heading": "BANK DETAILS"},
         {"kind": "signature", "heading": "ACCEPTANCE",
          "body": "Chosen package: ____________________\nClient name: ____________________\nSignature: ____________________   Date: __________"}],
     "closing": "Best regards,\n{company}"},
    {"name": "Supervision", "lang": "en", "title": "SUPERVISION OFFER : {project}",
     "intro": "Dear {client},\nPlease find below our offer for the technical supervision of the finishing works of {project}.",
     "sections": [
         {"kind": "text", "heading": "SCOPE",
          "body": "1. Weekly site visits and progress reports\n2. Review and approval of materials and samples\n"
                  "3. Coordination between contractors and suppliers\n4. Review of contractor extracts before payment"},
         {"kind": "pricing", "heading": "SUPERVISION FEE", "items": [{"desc": "Monthly supervision fee", "amount": 0}],
          "discount": 0, "vat": 0},
         {"kind": "payment", "heading": "PAYMENT TERMS", "milestones": [{"label": "Monthly in advance", "pct": 100}]},
         {"kind": "bank", "heading": "BANK DETAILS"}],
     "closing": "Best regards,\n{company}"},
]
SECTION_KINDS = ("text", "pricing", "packages", "payment", "bank", "signature")
MAX_TIERS = 6
OFFER_STATUS = ("Draft", "Sent", "Accepted", "Rejected")

# ------------------------------------------------------------------ helpers
AR_RE = re.compile("[؀-ۿ]")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
AR_SYMBOL = {"EGP": "ج.م", "SAR": "ر.س", "AED": "د.إ", "USD": "$", "EUR": "€", "KWD": "د.ك", "QAR": "ر.ق"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
XML_BAD = re.compile("[\u0000-\u0008\u000B\u000C\u000E-\u001F]")


def X(s):
    s = XML_BAD.sub("", "" if s is None else str(s))
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def E(s):
    return _html.escape("" if s is None else str(s))


def hex6(c):
    return (str(c or "#000000").replace("#", "").upper() + "000000")[:6]


def is_ar(s):
    return bool(AR_RE.search(str(s or "")))


def rels(items):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships">' + "".join(items) + "</Relationships>")


def core_xml(title, company):
    now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats'
            '.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://'
            'purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{X(title)}</dc:title><dc:creator>{X(company)}</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>')


def zip_files(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    return buf.getvalue()


def b64bytes(s):
    import base64
    return base64.b64decode(s)


def r2(v):
    return round(float(v or 0) + 0.0, 2)


BANK_FIELDS = ("bank", "acct", "holder", "swift", "iban")
SAMPLE_BANK = {"bank": "Bank Name — Branch", "acct": "0000 0000 0000", "holder": "Company Name", "swift": "XXXXXXXX",
               "iban": "EG00 0000 0000 0000 0000 0000 000"}   # placeholders for previews


def clean_bank(b):
    b = b if isinstance(b, dict) else {}
    return {k: text(b.get(k), maxlen=200) for k in BANK_FIELDS}


def banks(db):
    """The company's bank accounts (Settings → Company). Books from before the list existed keep their one bank."""
    try:
        out = json.loads(db.setting("banks", "") or "[]")
    except json.JSONDecodeError:
        out = []
    out = [{"id": text(b.get("id"), maxlen=40), "label": text(b.get("label"), maxlen=120), **clean_bank(b)}
           for b in out if isinstance(b, dict) and b.get("id")]
    if not out:
        s = lambda k: db.setting(k, "") or ""  # noqa: E731
        legacy = {"bank": s("bank_name"), "acct": s("bank_account"), "holder": s("bank_holder"),
                  "swift": s("bank_swift"), "iban": s("bank_iban")}
        if any(legacy.values()):
            out = [{"id": "main", "label": legacy["bank"], **legacy}]
    return out


def default_bank(db):
    bs = banks(db)
    want = db.setting("bank_default", "") or ""
    return next((b for b in bs if b["id"] == want), bs[0] if bs else {"id": "", "label": "", **clean_bank({})})


def bank_for(db, section):
    """Bank shown in an offer section: its own details, one of the company's banks, or the default."""
    pick = (section or {}).get("bank_id") or ""
    if pick == "custom":
        return clean_bank(section.get("bank_details"))
    return next((b for b in banks(db) if b["id"] == pick), None) or default_bank(db)


def company(db):
    s = lambda k: db.setting(k, "") or ""  # noqa: E731
    return {"name": s("company_name"), "legal": s("company_legal_name"), "currency": s("currency") or "EGP",
            "bank": clean_bank(default_bank(db)),
            "validity": int(float(s("offer_validity_days") or 15))}


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', " ", str(s or "")).strip()[:120] or "document"


# ------------------------------------------------------------- amounts in words
_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
         "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _chunk_en(n):
    s = []
    if n >= 100:
        s.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n >= 20:
        s.append(_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else ""))
    elif n:
        s.append(_ONES[n])
    return " ".join(s)


def words_en(n):
    if not n:
        return "Zero"
    sc, out, i = ["", "Thousand", "Million", "Billion"], [], 0
    while n > 0:
        c = n % 1000
        if c:
            out.insert(0, _chunk_en(c) + (" " + sc[i] if sc[i] else ""))
        n //= 1000
        i += 1
    return " ".join(out)


_AR_ONES = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة", "عشرة", "أحد عشر", "اثنا عشر",
            "ثلاثة عشر", "أربعة عشر", "خمسة عشر", "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
_AR_TENS = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_AR_HUND = ["", "مائة", "مائتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة"]
_AR_SCALES = [None, ["ألف", "ألفان", "آلاف", "ألف"], ["مليون", "مليونان", "ملايين", "مليون"],
              ["مليار", "ملياران", "مليارات", "مليار"]]


def _chunk_ar(n):
    p, h, r = [], n // 100, n % 100
    if h:
        p.append(_AR_HUND[h])
    if r:
        p.append(_AR_ONES[r] if r < 20 else (_AR_ONES[r % 10] + " و" + _AR_TENS[r // 10] if r % 10 else _AR_TENS[r // 10]))
    return " و".join(p)


def words_ar(n):
    if not n:
        return "صفر"
    parts, i = [], 0
    while n > 0:
        c = n % 1000
        if c:
            sc = _AR_SCALES[i]
            parts.insert(0, _chunk_ar(c) if not sc else sc[0] if c == 1 else sc[1] if c == 2 else
                         _chunk_ar(c) + " " + sc[2] if c <= 10 else _chunk_ar(c) + " " + sc[3])
        n //= 1000
        i += 1
    return " و".join(parts)


def money_words(v, lang, b):
    n = int(round(abs(float(v or 0)) * 100))
    i, f = n // 100, n % 100
    if lang == "ar":
        return f"فقط {words_ar(i)} {b['cur_words_ar'] or 'جنيه مصري'}" + \
            (f" و{words_ar(f)} {b['cur_fraction_ar'] or 'قرش'}" if f else "") + " لا غير"
    return f"{words_en(i)} {b['cur_words']}" + (f" and {words_en(f)} {b['cur_fraction']}" if f else "")


def doc_money(n, lang, code="EGP"):
    v = r2(n)
    num = f"{abs(v):,.0f}" if float(v).is_integer() else f"{abs(v):,.2f}"
    return ("-" if v < 0 else "") + num + " " + (AR_SYMBOL.get(code, code) if lang == "ar" else code)


def doc_date(iso, lang):
    try:
        d = _dt.date.fromisoformat(str(iso or acc.today())[:10])
    except ValueError:
        d = _dt.date.today()
    if lang == "ar":
        return f"{d.day} {MONTHS_AR[d.month - 1]} {d.year}"
    n = d.day
    suf = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf} {MONTHS[d.month - 1]} {d.year}"


def dmy(iso):
    try:
        d = _dt.date.fromisoformat(str(iso)[:10])
        return f"{d.day}/{d.month}/{d.year}"
    except ValueError:
        return str(iso or "")


DOC_LABELS = {
    "en": {"desc": "Description", "amount": "Amount", "total": "Total", "discount": "Discount", "vat": "VAT",
           "pay": "Total Payment", "milestone": "Milestone", "bank": "BANK", "acct": "BANK ACCOUNT NUMBER", "holder": "NAME",
           "swift": "SWIFT CODE", "iban": "IBAN", "to": "TO :", "mob": "MOB", "pk_inc": "What's included", "pk_price": "Package price"},
    "ar": {"desc": "البيان", "amount": "المبلغ", "total": "الإجمالي", "discount": "الخصم", "vat": "ضريبة القيمة المضافة",
           "pay": "الإجمالي المستحق", "milestone": "الدفعة", "bank": "البنك", "acct": "رقم الحساب", "holder": "اسم صاحب الحساب",
           "swift": "SWIFT", "iban": "IBAN", "to": "إلى :", "mob": "موبايل", "pk_inc": "ما تشمله الباقة", "pk_price": "سعر الباقة"},
}

SHAPETYPE75 = ('<v:shapetype id="_x0000_t75" coordsize="21600,21600" o:spt="75" o:preferrelative="t" path="m@4@5l@4@11@9@11@9'
               '@5xe" filled="f" stroked="f"><v:stroke joinstyle="miter"/><v:formulas><v:f eqn="if lineDrawn pixelLineWidth 0"'
               '/><v:f eqn="sum @0 1 0"/><v:f eqn="sum 0 0 @1"/><v:f eqn="prod @2 1 2"/><v:f eqn="prod @3 21600 pixelWidth"/>'
               '<v:f eqn="prod @3 21600 pixelHeight"/><v:f eqn="sum @0 0 1"/><v:f eqn="prod @6 1 2"/><v:f eqn="prod @7 21600 '
               'pixelWidth"/><v:f eqn="sum @8 21600 0"/><v:f eqn="prod @7 21600 pixelHeight"/><v:f eqn="sum @10 21600 0"/>'
               '</v:formulas><v:path o:extrusionok="f" gradientshapeok="t" o:connecttype="rect"/><o:lock v:ext="edit" '
               'aspectratio="t"/></v:shapetype>')


# ====================================================================== XLSX
def col_name(n):
    s = ""
    while n > 0:
        m = (n - 1) % 26
        s = chr(65 + m) + s
        n = (n - 1) // 26
    return s


def ref(r, c):
    return f"{col_name(c)}{r}"


class Styles:
    """Named cell styles modelled on the company's BOQ / invoice workbook (also used for the HTML / PDF view)."""

    def __init__(self, b, money):
        self.b, self.money = b, money
        self.fonts, self.fills, self.xfs, self.S, self.defs = [], ['<fill><patternFill patternType="none"/></fill>',
                                                                   '<fill><patternFill patternType="gray125"/></fill>'], \
            ['<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'], {}, {}
        self.font_id(10)
        C, D, A, Lt = "#FFFFFF", b["dark"], b["accent"], b["light"]
        d = self.define
        d("label", b=1); d("value"); d("company", wrap=1)
        d("barDark", b=1, sz=12, color=C, fill=D, wrap=1); d("barAccent", b=1, sz=11, color=C, fill=A)
        d("sumNo", b=1); d("sumDesc", h="right"); d("sumNum", num="money")
        d("sumDescHi", h="right", fill="#F8CBAD"); d("sumNumHi", num="money", fill="#F8CBAD"); d("sumNoHi", b=1, fill="#F8CBAD")
        d("totL", b=1, sz=12, color=C, fill=A); d("totN", b=1, sz=13, color=C, fill=A, num="money")
        d("note", sz=9, fill="#F2F2F2", border=3, wrap=1)
        d("client", b=1, sz=12, color=C, fill=D, border=0)
        d("secBar", b=1, sz=11, color=C, fill=D, border=2)
        d("subBar", b=1, sz=9, fill="#F2F2F2", h="right")
        d("colHead", b=1, sz=9, fill=Lt, wrap=1)
        d("itemNo", sz=9); d("itemDesc", sz=9, h="right", wrap=1); d("itemC", sz=9)
        d("itemNum", sz=9, num="money"); d("itemNumB", sz=9, b=1, num="moneyBlank"); d("itemNeed", sz=9, fill="#FFFF00")
        d("secTotL", b=1, sz=13, fill="#F2F2F2", border=2); d("secTotN", b=1, sz=13, fill="#F2F2F2", border=2, num="money")

    def font_id(self, sz, bold=False, color=None):
        x = (f'<font>{"<b/>" if bold else ""}<sz val="{sz}"/><color rgb="FF{hex6(color or "#000000")}"/>'
             f'<name val="{X(self.b["font"] or "Arial")}"/></font>')
        if x not in self.fonts:
            self.fonts.append(x)
        return self.fonts.index(x)

    def fill_id(self, color):
        if not color:
            return 0
        x = f'<fill><patternFill patternType="solid"><fgColor rgb="FF{hex6(color)}"/><bgColor indexed="64"/></patternFill></fill>'
        if x not in self.fills:
            self.fills.append(x)
        return self.fills.index(x)

    def define(self, name, b=0, sz=10, color=None, fill=None, border=1, h="center", v="center", wrap=0, num=None):
        nf = 164 if num == "money" else 165 if num == "moneyBlank" else 0
        self.xfs.append(f'<xf numFmtId="{nf}" fontId="{self.font_id(sz, b, color)}" fillId="{self.fill_id(fill)}" '
                        f'borderId="{border}" xfId="0" applyFont="1" applyFill="1" applyBorder="1"'
                        + (' applyNumberFormat="1"' if nf else "") + f' applyAlignment="1"><alignment horizontal="{h}" '
                        f'vertical="{v}"' + (' wrapText="1"' if wrap else "") + ' readingOrder="2"/></xf>')
        self.S[name] = len(self.xfs) - 1
        self.defs[len(self.xfs) - 1] = dict(b=b, sz=sz, color=color, fill=fill, border=border, h=h, wrap=wrap, num=num)

    def xml(self):
        m = self.money
        borders = ['<border><left/><right/><top/><bottom/><diagonal/></border>',
                   '<border>' + "".join(f'<{k} style="thin"><color rgb="FF000000"/></{k}>' for k in ("left", "right", "top", "bottom")) + '<diagonal/></border>',
                   '<border>' + "".join(f'<{k} style="medium"><color rgb="FF000000"/></{k}>' for k in ("left", "right", "top", "bottom")) + '<diagonal/></border>',
                   '<border>' + "".join(f'<{k} style="thin"><color rgb="FFA6A6A6"/></{k}>' for k in ("left", "right", "top", "bottom")) + '<diagonal/></border>']
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main">'
                f'<numFmts count="2"><numFmt numFmtId="164" formatCode="{m}"/><numFmt numFmtId="165" formatCode="{m};-{m};;@"/></numFmts>'
                f'<fonts count="{len(self.fonts)}">{"".join(self.fonts)}</fonts><fills count="{len(self.fills)}">{"".join(self.fills)}</fills>'
                f'<borders count="{len(borders)}">{"".join(borders)}</borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                f'<cellXfs count="{len(self.xfs)}">{"".join(self.xfs)}</cellXfs>'
                '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


class Sheet:
    """A one-sheet layout model: rows of styled cells, merges, column widths — rendered to .xlsx or HTML."""

    def __init__(self):
        self.rows, self.merges, self.heights = {}, [], {}

    def cell(self, r, c, v, s=None, f=None, rich=None):
        self.rows.setdefault(r, {})[c] = {"v": v, "s": s, "f": f, "rich": rich}

    def band(self, r, c1, c2, v, s, f=None):
        self.cell(r, c1, v, s, f)
        for k in range(c1 + 1, c2 + 1):
            self.cell(r, k, "", s)
        if c2 > c1:
            self.merges.append((r, c1, r, c2))

    def ht(self, r, h):
        self.heights[r] = h


def write_xlsx(m, st, *, sheet, rtl, cols, title, company_name, footer="", logo=None, wm=None):
    files, sheet_rels, ct = [], [], []

    def cell_xml(r, c, x):
        rf, s = ref(r, c), f' s="{x["s"]}"' if x["s"] else ""
        if x["rich"]:
            runs = "".join(f'<r><rPr>{"<b/>" if t.get("b") else ""}<sz val="{t.get("sz", 10)}"/><color rgb="FF000000"/>'
                           f'<rFont val="{X(st.b["font"])}"/></rPr><t xml:space="preserve">{X(t["t"])}</t></r>' for t in x["rich"])
            return f'<c r="{rf}"{s} t="inlineStr"><is>{runs}</is></c>'
        v = x["v"]
        if x["f"]:
            return f'<c r="{rf}"{s}><f>{X(x["f"])}</f>{f"<v>{v}</v>" if isinstance(v, (int, float)) and not isinstance(v, bool) else ""}</c>'
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f'<c r="{rf}"{s}><v>{v}</v></c>'
        if v in (None, ""):
            return f'<c r="{rf}"{s}/>'
        return f'<c r="{rf}"{s} t="inlineStr"><is><t xml:space="preserve">{X(v)}</t></is></c>'

    rows_xml = "".join(
        f'<row r="{r}"' + (f' ht="{m.heights[r]}" customHeight="1"' if r in m.heights else "") + ">"
        + "".join(cell_xml(r, c, m.rows[r][c]) for c in sorted(m.rows[r])) + "</row>" for r in sorted(m.rows))
    rid = [0]

    def rel(typ, target):
        rid[0] += 1
        sheet_rels.append(f'<Relationship Id="rId{rid[0]}" Type="{REL}{typ}" Target="{target}"/>')
        return f"rId{rid[0]}"

    drawing = legacy = header = ""
    if logo:
        img, ext = logo["img"], "png" if logo["img"]["mime"] == "image/png" else "jpeg"
        hpx = logo["px"]
        wpx = round(hpx * (img["w"] or 1) / (img["h"] or 1))
        files.append((f"xl/media/logo.{ext}", b64bytes(img["data"])))
        files.append(("xl/drawings/_rels/drawing1.xml.rels", rels([f'<Relationship Id="rId1" Type="{REL}image" Target="../media/logo.{ext}"/>'])))
        files.append(("xl/drawings/drawing1.xml", drawing_xml(logo["col"], logo["row"], wpx, hpx)))
        ct.append('<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>')
        drawing = f'<drawing r:id="{rel("drawing", "../drawings/drawing1.xml")}"/>'
    if wm:
        files.append(("xl/media/watermark.png", b64bytes(wm["data"])))
        files += wm_vml_parts()
        legacy = f'<legacyDrawingHF r:id="{rel("vmlDrawing", "../drawings/vmlDrawingHF1.vml")}"/>'
        header = "&amp;C&amp;G"
    hf = lambda s: X(str(s or "").replace("&", "&&"))  # noqa: E731
    foot = f'&amp;L&amp;8{hf(company_name)}{" · " + hf(footer) if footer else ""}&amp;R&amp;8صفحة &amp;P من &amp;N'
    merges = "".join(f'<mergeCell ref="{ref(a, b)}:{ref(c, d)}"/>' for a, b, c, d in m.merges)
    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/'
                 'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                 '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
                 '<sheetViews><sheetView workbookViewId="0"' + (' rightToLeft="1"' if rtl else "") + ' showGridLines="0" zoomScaleNormal="100"/></sheetViews>'
                 '<sheetFormatPr defaultRowHeight="15"/>'
                 '<cols>' + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(cols, 1)) + '</cols>'
                 f'<sheetData>{rows_xml}</sheetData>'
                 + (f'<mergeCells count="{len(m.merges)}">{merges}</mergeCells>' if m.merges else "")
                 + f'<pageMargins left="0.25" right="0.25" top="{0.6 if wm else 0.3}" bottom="0.45" header="0.2" footer="0.2"/>'
                 '<pageSetup paperSize="9" orientation="portrait" fitToWidth="1" fitToHeight="0"/>'
                 f'<headerFooter>{f"<oddHeader>{header}</oddHeader>" if header else ""}<oddFooter>{foot}</oddFooter></headerFooter>'
                 f'{drawing}{legacy}</worksheet>')
    name = X(re.sub(r"[\[\]:*?/\\]", " ", sheet)[:31])
    files = [
        ("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/'
         'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
         '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
         '<Default Extension="jpeg" ContentType="image/jpeg"/><Default Extension="vml" ContentType="application/vnd.openxmlformats-'
         'officedocument.vmlDrawing"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
         'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
         'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType='
         '"application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/docProps/core.xml" '
         'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>' + "".join(ct) + "</Types>"),
        ("_rels/.rels", rels([f'<Relationship Id="rId1" Type="{REL}officeDocument" Target="xl/workbook.xml"/>',
                              '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
                              'metadata/core-properties" Target="docProps/core.xml"/>'])),
        ("docProps/core.xml", core_xml(title, company_name)),
        ("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats'
         '.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews>'
         f'<workbookView/></bookViews><sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="191029" '
         'fullCalcOnLoad="1"/></workbook>'),
        ("xl/_rels/workbook.xml.rels", rels([f'<Relationship Id="rId1" Type="{REL}worksheet" Target="worksheets/sheet1.xml"/>',
                                             f'<Relationship Id="rId2" Type="{REL}styles" Target="styles.xml"/>'])),
        ("xl/styles.xml", st.xml()),
        ("xl/worksheets/sheet1.xml", sheet_xml),
    ] + files
    if sheet_rels:
        files.append(("xl/worksheets/_rels/sheet1.xml.rels", rels(sheet_rels)))
    return zip_files(files)


def drawing_xml(col, row, wpx, hpx, pic_id=2):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/'
            'drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://'
            'schemas.openxmlformats.org/officeDocument/2006/relationships"><xdr:oneCellAnchor><xdr:from>'
            f'<xdr:col>{col - 1}</xdr:col><xdr:colOff>57150</xdr:colOff><xdr:row>{row - 1}</xdr:row><xdr:rowOff>38100</xdr:rowOff>'
            f'</xdr:from><xdr:ext cx="{wpx * 9525}" cy="{hpx * 9525}"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{pic_id}" name="Logo"/>'
            '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rId1"/>'
            '<a:stretch><a:fillRect/></a:stretch></xdr:blipFill><xdr:spPr><a:xfrm><a:off x="0" y="0"/>'
            f'<a:ext cx="{wpx * 9525}" cy="{hpx * 9525}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
            '</xdr:pic><xdr:clientData/></xdr:oneCellAnchor></xdr:wsDr>')


def wm_vml_parts(wpt=380, hpt=380):
    """Excel's own watermark: a picture in the page header (Page Layout view, print and PDF)."""
    return [("xl/drawings/_rels/vmlDrawingHF1.vml.rels",
             rels([f'<Relationship Id="rId1" Type="{REL}image" Target="../media/watermark.png"/>'])),
            ("xl/drawings/vmlDrawingHF1.vml",
             '<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
             'xmlns:x="urn:schemas-microsoft-com:office:excel"><o:shapelayout v:ext="edit"><o:idmap v:ext="edit" data="1"/>'
             f'</o:shapelayout>{SHAPETYPE75}<v:shape id="CH" o:spid="_x0000_s1025" type="#_x0000_t75" style="position:absolute;'
             f'margin-left:0;margin-top:0;width:{wpt}pt;height:{hpt}pt;z-index:1"><v:imagedata o:relid="rId1" o:title="watermark"/>'
             '<o:lock v:ext="edit" rotation="t"/></v:shape></xml>')]


# ------------------------------------------------------------ Sheet -> HTML (print / PDF)
def sheet_html(m, st, *, cols, rtl, title, logo=None, wm=None, font="Arial"):
    covered = set()
    span = {}
    for a, b, c, d in m.merges:
        span[(a, b)] = (c - a + 1, d - b + 1)
        for r in range(a, c + 1):
            for k in range(b, d + 1):
                if (r, k) != (a, b):
                    covered.add((r, k))
    logo_at = None
    if logo:  # the logo sits inside whichever (merged) cell covers its anchor
        for (a, b), (rs, cs) in span.items():
            if a <= logo["row"] < a + rs and b <= logo["col"] < b + cs:
                logo_at = (a, b)
        logo_at = logo_at or (logo["row"], logo["col"])
    ncols = len(cols)
    fmt_money = st.money

    def num(v, blank):
        if v in ("", None):
            return ""
        v = float(v)
        if blank and v == 0:
            return ""
        s = f"{abs(v):,.0f}" if fmt_money == "#,##0" else f"{abs(v):,.2f}"
        return ("-" if v < 0 else "") + s

    css_cls = {}
    rules = []
    for idx, dfn in st.defs.items():
        c = f"s{idx}"
        css_cls[idx] = c
        bd = {0: "none", 1: "1px solid #000", 2: "2px solid #000", 3: "1px solid #A6A6A6"}[dfn["border"]]
        ha = {"center": "center", "right": "right", "left": "left"}[dfn["h"]]
        rules.append(f".{c}{{font-weight:{700 if dfn['b'] else 400};font-size:{dfn['sz']}pt;color:{dfn['color'] or '#000'};"
                     f"background:{dfn['fill'] or 'transparent'};border:{bd};text-align:{ha};"
                     f"white-space:{'pre-wrap' if dfn['wrap'] else 'pre'}}}")
    body = []
    maxr = max(m.rows) if m.rows else 0
    for r in range(1, maxr + 1):
        cells = m.rows.get(r, {})
        h = m.heights.get(r, 15)
        tds = []
        for c in range(1, ncols + 1):
            if (r, c) in covered:
                continue
            x = cells.get(c)
            rs, cs = span.get((r, c), (1, 1))
            attrs = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{cs}"' if cs > 1 else "")
            if not x:
                tds.append(f"<td{attrs}></td>")
                continue
            dfn = st.defs.get(x["s"], {})
            if x["rich"]:
                inner = "".join(f'<div style="font-size:{t.get("sz", 10)}pt;font-weight:{700 if t.get("b") else 400}">'
                                f'{E(t["t"].strip())}</div>' for t in x["rich"])
            elif dfn.get("num") in ("money", "moneyBlank") and x["v"] not in ("", None):
                inner = E(num(x["v"], dfn.get("num") == "moneyBlank"))
            elif isinstance(x["v"], float):
                inner = E(f"{x['v']:g}")
            else:
                inner = E(x["v"]).replace("\n", "<br>")
            if logo_at == (r, c):
                img = logo["img"]
                inner = (f'<img class="xl-logo" src="data:{img["mime"]};base64,{img["data"]}" style="height:{logo["px"]}px" alt="">'
                         + inner)
            tds.append(f'<td class="{css_cls.get(x["s"], "")}"{attrs} dir="auto">{inner}</td>')
        body.append(f'<tr style="height:{h * 1.333:.0f}px">{"".join(tds)}</tr>')
    tw = sum(cols)
    colgroup = "".join(f'<col style="width:{w / tw * 100:.2f}%">' for w in cols)
    wm_html = (f'<img class="wm" src="data:image/png;base64,{wm["data"]}" alt="">' if wm else "")
    return (f'<!doctype html><html lang="{"ar" if rtl else "en"}" dir="{"rtl" if rtl else "ltr"}"><head><meta charset="utf-8">'
            f'<title>{E(title)}</title><style>@page{{size:A4;margin:10mm 7mm}}body{{margin:0;font-family:"{E(font)}",Arial,Tahoma,'
            'sans-serif;background:#fff;color:#000}.page{position:relative}table{border-collapse:collapse;table-layout:fixed;'
            'width:100%;position:relative;z-index:1}td{padding:1px 4px;vertical-align:middle;overflow:hidden}'
            '.xl-logo{float:left;margin:2px 4px}'
            '.wm{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:62%;max-width:560px;z-index:0;'
            'pointer-events:none}tr{page-break-inside:avoid}.bar{position:sticky;top:0;background:#f3f3f3;padding:8px;'
            'text-align:center;z-index:5;font:13px Arial}@media print{.bar{display:none}}'
            + "".join(rules) + '</style></head><body>'
            '<div class="bar"><button onclick="print()">Print / Save as PDF</button></div>'
            f'{wm_html}<div class="page"><table><colgroup>{colgroup}</colgroup>{"".join(body)}</table></div>'
            '<script>addEventListener("load",()=>setTimeout(()=>print(),350))</script></body></html>')


# ---------------------------------------------------------------- BOQ / extract layout
def _lk(ln):
    return (ln.get("category") or "", ln.get("item_no") or "", (ln.get("description") or "").strip())


def _cats(lines):
    out = []
    for ln in lines:
        c = ln.get("category") or ""
        if c not in out:
            out.append(c)
    return out


def boq_model(db, kind, doc):
    """kind: 'boq' (quotation) or 'invoice' (extract against a contract BOQ, or standalone)."""
    b = branding.get(db)
    co = company(db)
    inv = kind == "invoice"
    contract = acc.get_boq(db, doc["boq_id"]) if inv and doc.get("boq_id") else None
    exq = {}
    if inv:
        for ln in doc["lines"]:
            exq[_lk(ln)] = exq.get(_lk(ln), 0) + (ln["qty"] or 0)
    if contract:
        keys = {_lk(z) for z in contract["lines"]}
        extra = [dict(ln, qty=0) for ln in doc["lines"] if _lk(ln) not in keys]
        lines = contract["lines"] + extra
    elif inv:  # standalone invoice: contract quantity = invoiced quantity
        lines = doc["lines"]
    else:
        lines = doc["lines"]
    cats = _cats(lines)
    val = (lambda ln: r2(exq.get(_lk(ln), 0) * ln["price"])) if inv else (lambda ln: r2(ln["qty"] * ln["price"]))
    whole = all(float(r2(ln["price"])).is_integer() and float(r2(ln["qty"] * ln["price"])).is_integer()
                and float(val(ln)).is_integer() for ln in lines)
    st = Styles(b, "#,##0" if whole else "#,##0.00")
    S = st.S
    N = 9 if inv else 7
    col = {"q": 4, "e": 5, "pr": 6, "t": 7, "a": 8, "n": 9} if inv else {"q": 4, "pr": 5, "t": 6, "n": 7}
    m = Sheet()
    client = doc.get("client") or doc.get("customer") or doc.get("bu_customer") or doc.get("bu_name") or ""
    location = doc.get("location") or doc.get("bu_location") or (contract or {}).get("location") or ""
    meta = [("التاريخ", dmy(doc.get("date"))), ("المشروع", client), ("الموقع", location),
            ("المستخلص" if inv else "الاصدار", doc.get("number") if inv else (doc.get("rev") or ""))]
    for i, (k, v) in enumerate(meta):
        m.cell(1 + i, 1, k, S["label"])
        m.cell(1 + i, 2, v, S["value"])
        m.ht(1 + i, 28.5 if i == 3 else 17)
    for rr in range(1, 5):
        for k in range(3, N + 1):
            m.cell(rr, k, "", S["company"])
    m.cell(1, 3, "", S["company"], rich=[{"t": co["name"], "b": 1, "sz": 20}] +
           ([{"t": "\n" + co["legal"], "b": 1, "sz": 10}] if co["legal"] else []))
    m.merges.append((1, 3, 4, N))
    r = 6
    # summary header
    m.band(r, 1, col["t"] - 1, "إجمالي الأعمال", S["barDark"])
    if inv:
        m.cell(r, col["t"], "إجمالي\nالمقايسة", S["barDark"])
        m.cell(r, col["a"], "إجمالي\nالفعلي", S["barDark"])
        m.cell(r, N, "", S["barDark"])
    else:
        m.band(r, col["t"], N, "إجمالي المقايسة", S["barDark"])
    m.ht(r, 31.5)
    r += 1
    m.band(r, 1, col["t"] - 1, "الأعمال الداخلية", S["barAccent"])
    if inv:
        for k in (col["t"], col["a"], N):
            m.cell(r, k, "", S["barAccent"])
    else:
        m.band(r, col["t"], N, "", S["barAccent"])
    r += 1
    sum_rows = {}
    for c in cats:
        sum_rows[c] = r
        r += 1
    sum_tot = r
    r += 1
    notes = [x.strip() for x in (b["boq_notes"] or "").split("\n") if x.strip()] if not inv else []
    for t in notes:
        m.band(r, 1, N, t, S["note"])
        m.ht(r, 16)
        r += 1
    r += 2
    m.band(r, 1, N, f"CLIENT: {client}          LOCATION: {location}", S["client"])
    m.ht(r, 18)
    r += 2
    cat_tot = {}
    heads = (["م", "البيان", "الوحدة", "الكمية\nبالعقد", "المنفذ\nفعلي", "السعر", "الإجمالي\nبالعقد", "الإجمالي\nالفعلي", "ملاحظات"]
             if inv else ["م", "البيان", "الوحدة", "الكمية", "السعر", "الإجمالي", "ملاحظات"])
    for i, c in enumerate(cats):
        m.band(r, 1, N, f"{i + 1}- {c}" if c else f"{i + 1}-", S["secBar"])
        m.ht(r, 18)
        r += 1
        for k, h in enumerate(heads):
            m.cell(r, k + 1, h, S["colHead"])
        m.ht(r, 26.25)
        r += 1
        first, sub = r, None
        for ln in [x for x in lines if (x.get("category") or "") == c]:
            if (ln.get("sub") or "") and ln.get("sub") != sub:
                sub = ln["sub"]
                m.band(r, 1, N, sub, S["subBar"])
                r += 1
            need = not (float(ln["qty"] or 0) > 0)
            m.cell(r, 1, ln.get("item_no") or "", S["itemNo"])
            m.cell(r, 2, ln["description"], S["itemDesc"])
            m.cell(r, 3, ln.get("unit") or "", S["itemC"])
            m.cell(r, col["q"], "" if need else float(ln["qty"]), S["itemNeed"] if need else S["itemC"])
            m.cell(r, col["pr"], float(ln["price"] or 0), S["itemNum"])
            m.cell(r, col["t"], r2(ln["qty"] * ln["price"]), S["itemNum"], f"{ref(r, col['q'])}*{ref(r, col['pr'])}")
            if inv:
                e = exq.get(_lk(ln), 0)
                m.cell(r, col["e"], e or "", S["itemC"])
                m.cell(r, col["a"], val(ln), S["itemNumB"], f"{ref(r, col['e'])}*{ref(r, col['pr'])}")
            m.cell(r, col["n"], ln.get("notes") or "", S["itemC"])
            m.ht(r, max(15.75, -(-len(ln["description"] or "") // 95) * 12 + 4))
            r += 1
        last = r - 1
        in_cat = [x for x in lines if (x.get("category") or "") == c]
        m.band(r, 1, col["t"] - 1, "إجمالي " + c, S["secTotL"])
        m.cell(r, col["t"], r2(sum(x["qty"] * x["price"] for x in in_cat)), S["secTotN"],
               f"SUM({ref(first, col['t'])}:{ref(last, col['t'])})" if last >= first else "0")
        if inv:
            m.cell(r, col["a"], r2(sum(val(x) for x in in_cat)), S["secTotN"],
                   f"SUM({ref(first, col['a'])}:{ref(last, col['a'])})" if last >= first else "0")
        m.cell(r, N, "", S["secTotL"])
        m.ht(r, 18)
        cat_tot[c] = r
        r += 2
    for i, c in enumerate(cats):
        sr = sum_rows[c]
        in_cat = [x for x in lines if (x.get("category") or "") == c]
        tot = r2(sum(x["qty"] * x["price"] for x in in_cat))
        hi = not tot
        m.cell(sr, 1, i + 1, S["sumNoHi" if hi else "sumNo"])
        m.band(sr, 2, col["t"] - 1, "إجمالي " + c, S["sumDescHi" if hi else "sumDesc"])
        if inv:
            m.cell(sr, col["t"], tot, S["sumNumHi" if hi else "sumNum"], ref(cat_tot[c], col["t"]))
            m.cell(sr, col["a"], r2(sum(val(x) for x in in_cat)), S["sumNumHi" if hi else "sumNum"], ref(cat_tot[c], col["a"]))
            m.cell(sr, N, "", S["sumDescHi" if hi else "sumDesc"])
        else:
            m.band(sr, col["t"], N, tot, S["sumNumHi" if hi else "sumNum"], ref(cat_tot[c], col["t"]))
        m.ht(sr, 15)
    rng = (lambda k: f"SUM({ref(sum_rows[cats[0]], k)}:{ref(sum_rows[cats[-1]], k)})" if cats else "0")
    m.band(sum_tot, 1, col["t"] - 1, "إجمالي الأعمال الداخلية", S["totL"])
    total = r2(sum(x["qty"] * x["price"] for x in lines))
    if inv:
        m.cell(sum_tot, col["t"], total, S["totN"], rng(col["t"]))
        m.cell(sum_tot, col["a"], r2(sum(val(x) for x in lines)), S["totN"], rng(col["a"]))
        m.cell(sum_tot, N, "", S["totL"])
    else:
        m.band(sum_tot, col["t"], N, total, S["totN"], rng(col["t"]))
    m.ht(sum_tot, 20)
    cols = [8, 70, 9, 8, 8, 10, 15.3, 12.6, 14.7] if inv else [8, 72, 9, 9, 11, 15, 16]
    return m, st, cols, N, client


def boq_file(db, boq_id, fmt="xlsx"):
    d = acc.get_boq(db, boq_id)
    if not d:
        raise ValidationError("BOQ not found")
    return _boq_like(db, "boq", d, fmt, f"{d['client'] or d['bu_name'] or ''}_BOQ {d['rev'] or ''}".strip("_ "))


def invoice_file(db, xid, fmt="xlsx"):
    x = acc.get_extract(db, xid)
    if not x:
        raise ValidationError("Invoice not found")
    who = x.get("customer") or x.get("bu_customer") or x.get("bu_name") or ""
    return _boq_like(db, "invoice", x, fmt, f"{who}_Inv. {x['number']}".strip("_ "))


def _boq_like(db, kind, d, fmt, name):
    m, st, cols, N, client = boq_model(db, kind, d)
    fkind = f"{kind}_{'xlsx' if fmt == 'xlsx' else 'pdf'}"
    logo_img = branding.logo_for(db, fkind)
    wm = branding.watermark_for(db, fkind)
    logo = {"img": logo_img, "col": N, "row": 1, "px": 88} if logo_img else None
    b = branding.get(db)
    co = company(db)
    title = f"{d.get('number') or d.get('title') or ''}"
    if fmt == "xlsx":
        data = write_xlsx(m, st, sheet="Invoice" if kind == "invoice" else "BOQ", rtl=True, cols=cols, title=title,
                          company_name=co["name"], footer=b["footer"], logo=logo, wm=wm)
        return data, safe_name(name) + ".xlsx"
    return sheet_html(m, st, cols=cols, rtl=True, title=name, logo=logo, wm=wm, font=b["font"]).encode("utf-8"), None


# ====================================================================== OFFERS
def offer_totals(sec):
    if not sec:
        return {"total": 0, "discount": 0, "vat": 0, "net": 0}
    total = r2(sum(float(i.get("amount") or 0) for i in sec.get("items") or []))
    discount = r2(sec.get("discount"))
    vat = r2((total - discount) * float(sec.get("vat") or 0) / 100)
    return {"total": total, "discount": discount, "vat": vat, "net": r2(total - discount + vat)}


def tiers(s):
    """A packages section's tiers (named ones only), each with its inclusions as a list of lines."""
    out = []
    for x in (s or {}).get("tiers") or []:
        if str(x.get("name") or "").strip():
            out.append({"name": str(x["name"]).strip(), "price": float(x.get("price") or 0),
                        "lines": [ln.strip(" \t•-*") for ln in str(x.get("includes") or "").split("\n") if ln.strip(" \t•-*")]})
    return out


def chosen_tier(s):
    try:
        ch = int((s or {}).get("chosen", -1))
    except (TypeError, ValueError):
        return None
    ts = tiers(s)
    return ts[ch] if 0 <= ch < len(ts) else None


def pricing(o):
    """The offer's price: its pricing section, or — for package offers — the package the client chose."""
    sec = next((s for s in o.get("sections") or [] if s.get("kind") == "pricing"), None)
    if sec:
        return sec
    pk = next((s for s in o.get("sections") or [] if s.get("kind") == "packages"), None)
    t = chosen_tier(pk)
    return {"items": [{"desc": t["name"], "amount": t["price"]}], "discount": 0, "vat": float(pk.get("vat") or 0)} if t else None


def packages_open(o):
    """Package offer where the client has not chosen yet: payment terms show percentages only."""
    return pricing(o) is None and any(s.get("kind") == "packages" for s in o.get("sections") or [])


def offer_ctx(db, o):
    co = company(db)
    lang = "ar" if o.get("lang") == "ar" else "en"
    t = offer_totals(pricing(o))
    try:
        vd = _dt.date.fromisoformat(str(o.get("date") or acc.today())[:10]) + _dt.timedelta(days=co["validity"])
    except ValueError:
        vd = _dt.date.today()
    cur = co["currency"]
    return {"lang": lang, "client": o.get("client") or "", "project": o.get("project") or "",
            "date": doc_date(o.get("date"), lang), "company": co["name"], "validUntil": doc_date(vd.isoformat(), lang),
            "validityDays": str(co["validity"]), "total": doc_money(t["total"], lang, cur),
            "discount": doc_money(t["discount"], lang, cur), "net": doc_money(t["net"], lang, cur), "_cur": cur}


def fill(tx, cx):
    return re.sub(r"\{(\w+)\}", lambda mm: str(cx[mm.group(1)]) if mm.group(1) in cx else mm.group(0), str(tx or ""))


def offer_blocks(db, o):
    """Offer -> layout blocks, following the company's design-package template."""
    cx = offer_ctx(db, o)
    lang, L, b = cx["lang"], DOC_LABELS[cx["lang"]], branding.get(db)
    cur_code = cx["_cur"]
    mny = lambda n: doc_money(n, lang, cur_code)  # noqa: E731
    cur = AR_SYMBOL.get(cur_code, cur_code) if lang == "ar" else cur_code
    net = offer_totals(pricing(o))["net"]
    no_amounts = packages_open(o)
    T = fill(o.get("title"), cx)
    k = T.find(":")
    out = [{"t": "head"}, {"t": "title", "lead": T[:k + 1] if k >= 0 else T, "rest": T[k + 1:] if k >= 0 else ""}]
    if o.get("intro"):
        out.append({"t": "p", "text": fill(o["intro"], cx), "before": 188})
    for s in o.get("sections") or []:
        table = s.get("style") == "table"
        kind = s.get("kind")
        if kind == "bank":
            bank = bank_for(db, s)
            rows = [[a, v] for a, v in [[L["bank"], bank["bank"]], [L["acct"], bank["acct"]], [L["holder"], bank["holder"]],
                                        [L["swift"], bank["swift"]], [L["iban"], bank["iban"]]] if v]
            if not rows:
                continue
            if s.get("heading"):
                out.append({"t": "h", "text": fill(s["heading"], cx)})
            if table:
                out.append({"t": "kv", "rows": rows})
            else:
                out.append({"t": "p", "before": 24 if s.get("heading") else 246, "text": "\n".join(
                    f"{a}: {v}" if lang == "ar" else f"{a} {v}" if i == 0 else f"{a}: {v}" if i == 1 else f"{a} : {v}"
                    for i, (a, v) in enumerate(rows))})
            continue
        if s.get("heading"):
            out.append({"t": "h", "text": fill(s["heading"], cx)})
        if kind in ("text", "signature") and s.get("body"):
            out.append({"t": "p", "text": fill(s["body"], cx), "bold": bool(s.get("bold")), "before": 24 if s.get("heading") else 120})
        if kind == "pricing":
            t, items, words = offer_totals(s), s.get("items") or [], s.get("words") is not False
            if table:
                foot = []
                if t["discount"]:
                    foot += [[L["total"], mny(t["total"])], [L["discount"], "(" + mny(t["discount"]) + ")"]]
                if t["vat"]:
                    foot.append([f"{L['vat']} {s.get('vat')}%", mny(t["vat"])])
                foot.append([L["pay"] if (t["discount"] or t["vat"]) else L["total"], mny(t["net"])])
                out.append({"t": "table", "widths": [68, 32], "head": [L["desc"], f"{L['amount']} ({cur})"],
                            "align": ["left", "right"], "rows": [[fill(i.get("desc"), cx), mny(i.get("amount"))] for i in items],
                            "foot": foot})
            else:
                def line(tx, tone=None):
                    out.append({"t": "p", "text": tx, "tone": tone, "indent": 24})
                for n, i in enumerate(items):
                    line(f"{n + 1}.{' ' if lang == 'ar' else ''}{fill(i.get('desc'), cx)} = {mny(i.get('amount'))}")
                if len(items) > 1:
                    line(f"{L['total']} = {mny(t['total'])}")
                if words and (items or t["total"]):
                    line(" " + money_words(t["total"], lang, b))
                if t["discount"]:
                    line(f"{L['discount']} = {mny(t['discount'])}", "accent")
                if t["vat"]:
                    line(f"{L['vat']} {s.get('vat')}% = {mny(t['vat'])}")
                if t["discount"] or t["vat"]:
                    line(f" {L['pay']} = {mny(t['net'])}")
                    if words:
                        line(" " + money_words(t["net"], lang, b))
        if kind == "packages":
            ts, ch = tiers(s), chosen_tier(s)
            pick = ch and next(j for j, t in enumerate(ts) if t == ch)
            if ts:
                names = [("★ " if ch and j == pick else "") + fill(t["name"], cx) for j, t in enumerate(ts)]
                if table:     # comparison: one column per package, a tick for what each includes
                    feats = []
                    for t in ts:
                        feats += [ln for ln in t["lines"] if ln not in feats]
                    n = len(ts)
                    first = 40 if n <= 3 else 30
                    out.append({"t": "table", "widths": [first] + [round((100 - first) / n, 2)] * n, "head": [L["pk_inc"]] + names,
                                "align": ["left"] + ["center"] * n,
                                "rows": [[fill(f, cx)] + ["✓" if f in t["lines"] else "—" for t in ts] for f in feats],
                                "foot": [[L["pk_price"]] + [mny(t["price"]) for t in ts]]})
                else:
                    for j, t in enumerate(ts):
                        out.append({"t": "p", "text": f"{names[j]} — {mny(t['price'])}", "bold": True, "before": 140 if j else 40})
                        for ln in t["lines"]:
                            out.append({"t": "p", "text": "• " + fill(ln, cx), "indent": 24})
                if float(s.get("vat") or 0):
                    out.append({"t": "p", "text": (f"الأسعار غير شاملة ضريبة القيمة المضافة {s.get('vat'):g}%" if lang == "ar"
                                                   else f"Prices exclude VAT {float(s.get('vat')):g}%."), "before": 60})
        if kind == "payment":
            ms = s.get("milestones") or []
            amt = lambda x: "—" if no_amounts else mny(net * float(x.get("pct") or 0) / 100)  # noqa: E731
            if table:
                tot = sum(float(x.get("pct") or 0) for x in ms)
                out.append({"t": "table", "widths": [56, 12, 32], "head": [L["milestone"], "%", f"{L['amount']} ({cur})"],
                            "align": ["left", "center", "right"],
                            "rows": [[fill(x.get("label"), cx), f"{float(x.get('pct') or 0):g}%", amt(x)] for x in ms],
                            "foot": [[L["total"], f"{tot:g}%", "—" if no_amounts else mny(net * tot / 100)]]})
            else:
                out.append({"t": "tabs", "rows": [[f"{float(x.get('pct') or 0):g}% {fill(x.get('label'), cx)}", "" if no_amounts else amt(x)] for x in ms]})
    if o.get("closing"):
        out.append({"t": "p", "text": fill(o["closing"], cx), "before": 240})
    return out, cx


LTR_SEG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.,%@/_+&'’\-]|\s(?=[A-Za-z0-9]))*")
AR_SEG = re.compile(r"[؀-ۿ]+(?:[\s٠-٩\d.,،؛:()\-]*[؀-ۿ]+)*|[^؀-ۿ]+")


def _first_rtl(t):
    mm = re.search(r"[A-Za-z؀-ۿ]", str(t or ""))
    return bool(mm and is_ar(mm.group(0)))


def offer_docx(db, o, kind="offer_docx"):
    blocks, cx = offer_blocks(db, o)
    rtl_doc = cx["lang"] == "ar"
    L = DOC_LABELS[cx["lang"]]
    bidi_t = "<w:bidiVisual/>" if rtl_doc else ""
    b = branding.get(db)
    c = b["contact"]
    W = 11086
    tone = lambda t: b["dark"] if t == "dark" else b["accent"] if t == "accent" else b["text"]  # noqa: E731
    files, doc_rels, hdr_rels = [], [], []

    def rpr(o_, rtl):
        f = o_.get("font")
        sz = round((o_.get("size") or 12) * 2)
        return ("<w:rPr>" + (f'<w:rFonts w:ascii="{X(f)}" w:eastAsia="{X(f)}" w:hAnsi="{X(f)}" w:cs="{X(f)}"/>' if f else "")
                + ("<w:b/><w:bCs/>" if o_.get("bold") else "") + f'<w:color w:val="{hex6(o_.get("color") or b["text"])}"/>'
                + (f'<w:spacing w:val="{o_["sp"]}"/>' if o_.get("sp") else "") + f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>'
                + ("<w:rtl/>" if rtl else "") + "</w:rPr>")

    def dir_rtl(t):
        return (not re.match(r"^[^A-Za-z؀-ۿ]*[A-Za-z]", str(t or ""))) if rtl_doc else _first_rtl(t)

    def run(tx, o_=None, rtl_para=None):
        o_ = o_ or {}
        s = "" if tx is None else str(tx)
        segs = []
        if rtl_para is None:
            rtl_para = dir_rtl(s)
        if rtl_doc and rtl_para:
            i = 0
            for mm in LTR_SEG.finditer(s):
                if mm.start() > i:
                    segs.append((s[i:mm.start()], True))
                segs.append((mm.group(0), False))
                i = mm.end()
            if i < len(s):
                segs.append((s[i:], True))
        else:
            segs = [(seg, is_ar(seg)) for seg in (AR_SEG.findall(s) or [""])]
        if not segs:
            segs = [("", rtl_doc)]
        return "".join(f'<w:r>{rpr(o_, rtl)}<w:t xml:space="preserve">{X(seg)}</w:t></w:r>' for seg, rtl in segs)

    def para(inner, keep=False, bdr="", tabs="", rtl=None, before=0, after=0, line=288, indent=0, jc=None):
        rtl = rtl_doc if rtl is None else rtl
        return ("<w:p><w:pPr>" + ("<w:keepNext/>" if keep else "") + bdr + (f"<w:tabs>{tabs}</w:tabs>" if tabs else "")
                + ("<w:bidi/>" if rtl else "") + f'<w:spacing w:before="{before}" w:after="{after}" w:line="{line}" w:lineRule="exact"/>'
                + (f'<w:ind w:left="{indent}"/>' if indent else "") + (f'<w:jc w:val="{jc}"/>' if jc else "") + f"</w:pPr>{inner}</w:p>")

    nobdr = "<w:tblBorders>" + "".join(f'<w:{k} w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
                                       for k in ("top", "left", "bottom", "right", "insideH", "insideV")) + "</w:tblBorders>"

    def inline_img(rid, wpx, hpx, pid, name):
        return (f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{wpx * 9525}" cy="{hpx * 9525}"/>'
                f'<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{pid}" name="{name}"/><wp:cNvGraphicFramePr><a:graphicFrameLocks '
                'noChangeAspect="1"/></wp:cNvGraphicFramePr><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/'
                f'2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="{pid}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>'
                f'<a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/>'
                f'<a:ext cx="{wpx * 9525}" cy="{hpx * 9525}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
                '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')

    def tcell(inner, w, fill_=None, mar=None, v="center"):
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>' + (f'<w:shd w:val="clear" w:color="auto" w:fill="{hex6(fill_)}"/>' if fill_ else "")
                + ('<w:tcMar><w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/>'
                   '<w:right w:w="0" w:type="dxa"/></w:tcMar>' if mar == 0 else "") + f'<w:vAlign w:val="{v}"/></w:tcPr>{inner}</w:tc>')

    logo = branding.logo_for(db, kind)
    body = []
    for x in blocks:
        t = x["t"]
        if t == "head":
            if logo:
                ext = "png" if logo["mime"] == "image/png" else "jpeg"
                files.append((f"word/media/logo.{ext}", b64bytes(logo["data"])))
                doc_rels.append(f'<Relationship Id="rIdLogo" Type="{REL}image" Target="media/logo.{ext}"/>')
                h = min(int(b["logo_height"]), 170)
                w = round(h * (logo["w"] or 1) / (logo["h"] or 1))
                if w > 330:
                    w, h = 330, round(330 * (logo["h"] or 1) / (logo["w"] or 1))
                left = f'<w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr>{inline_img("rIdLogo", w, h, 1, "Logo")}</w:p>'
            else:
                left = para(run(cx["company"], {"size": 20, "bold": True, "color": b["dark"], "font": b["font"]}), line=440)
            body.append(f'<w:tbl><w:tblPr>{bidi_t}<w:tblW w:w="{W}" w:type="dxa"/>{nobdr}<w:tblLayout w:type="fixed"/><w:tblCellMar>'
                        '<w:left w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>'
                        f'<w:gridCol w:w="5200"/><w:gridCol w:w="{W - 5200}"/></w:tblGrid><w:tr><w:trPr><w:trHeight w:hRule="atLeast" '
                        f'w:val="1860"/></w:trPr>{tcell(left, 5200, v="bottom", mar=0)}'
                        f'{tcell(para(run(cx["date"], {"size": 14, "color": b["dark"], "sp": 9}), line=344, jc="right"), W - 5200, v="bottom", mar=0)}'
                        '</w:tr></w:tbl>')
        elif t == "title":
            bd = (f'<w:pBdr><w:top w:val="single" w:sz="12" w:space="3" w:color="{hex6(b["text"])}"/><w:bottom w:val="single" '
                  f'w:sz="12" w:space="3" w:color="{hex6(b["text"])}"/></w:pBdr>')
            body.append(para(run(x["lead"], {"size": 14, "color": b["dark"], "sp": 6}) + run(x["rest"], {"size": 14, "color": b["text"], "sp": 6}),
                             bdr=bd, before=134, after=60, line=344, rtl=dir_rtl(x["lead"] + x["rest"])))
        elif t == "h":
            body.append(para(run(x["text"], {"size": 14, "color": b["dark"], "sp": 6}), before=200, line=344, keep=True, rtl=dir_rtl(x["text"])))
        elif t == "p":
            for i, ln in enumerate(str(x.get("text") or "").split("\n")):
                body.append(para(run(ln, {"size": 11 if x.get("bold") else 12, "bold": x.get("bold"), "color": tone(x.get("tone")),
                                          "sp": 0 if x.get("bold") else 4}),
                                 before=0 if i else (x.get("before") or 0), indent=x.get("indent") or 0, rtl=dir_rtl(ln)))
        elif t == "tabs":
            for i, rw in enumerate(x["rows"]):
                body.append(para(run(rw[0], {"color": b["text"], "sp": 4}) + f'<w:r>{rpr({"color": b["text"]}, False)}<w:tab/></w:r>'
                                 + run(rw[1], {"color": b["text"], "sp": 4}), tabs='<w:tab w:val="left" w:pos="3880"/>',
                                 before=0 if i else 48))
        elif t in ("table", "kv"):
            tb = {"widths": [32, 68], "rows": x["rows"]} if t == "kv" else x
            TW = W - 200
            ws = [round(TW * w / 100) for w in tb["widths"]]
            al = lambda i: {"right": "right", "center": "center"}.get((tb.get("align") or [None] * 9)[i])  # noqa: E731
            bdr = "<w:tblBorders>" + "".join(f'<w:{k} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
                                             for k in ("top", "left", "bottom", "right", "insideH", "insideV")) + "</w:tblBorders>"

            def tr(cells, bold=False, fill_=None, color=None):
                return ("<w:tr><w:trPr><w:cantSplit/></w:trPr>" + "".join(
                    tcell(para(run(v, {"size": 11, "bold": bold or (t == "kv" and i == 0), "color": color or b["text"]}),
                               line=276, jc=al(i), rtl=dir_rtl(v)), ws[i],
                          fill_=fill_ or ("#F2F2F2" if t == "kv" and i == 0 else None)) for i, v in enumerate(cells)) + "</w:tr>")
            foot = tb.get("foot") or []
            body.append(f'<w:tbl><w:tblPr>{bidi_t}<w:tblW w:w="{TW}" w:type="dxa"/><w:tblInd w:w="20" w:type="dxa"/>{bdr}'
                        '<w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
                        '<w:bottom w:w="40" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>'
                        + "".join(f'<w:gridCol w:w="{w}"/>' for w in ws) + "</w:tblGrid>"
                        + (tr(tb["head"], True, b["dark"], "#FFFFFF") if tb.get("head") else "")
                        + "".join(tr(rw) for rw in tb["rows"])
                        + "".join(tr(rw, True, b["dark"] if j == len(foot) - 1 else "#F2F2F2", "#FFFFFF" if j == len(foot) - 1 else None)
                                  for j, rw in enumerate(foot)) + "</w:tbl>")
            body.append(para("", line=120))

    NSX = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
           'officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
           'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/'
           '2006/picture" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
           'xmlns:w10="urn:schemas-microsoft-com:office:word"')
    wm_xml = ""
    wm = branding.watermark_for(db, kind)
    if wm:
        files.append(("word/media/watermark.png", b64bytes(wm["data"])))
        hdr_rels.append(f'<Relationship Id="rIdWm" Type="{REL}image" Target="media/watermark.png"/>')
        wpt = 420 if b["wm"]["mode"] == "text" else 325
        hpt = round(wpt * (wm["h"] or 1) / (wm["w"] or 1))
        wm_xml = (f'<w:r><w:pict>{SHAPETYPE75}<v:shape id="WordPictureWatermark1" o:spid="_x0000_s2049" type="#_x0000_t75" '
                  f'style="position:absolute;margin-left:0;margin-top:0;width:{wpt}pt;height:{hpt}pt;z-index:-251656192;'
                  'mso-position-horizontal:center;mso-position-horizontal-relative:margin;mso-position-vertical:center;'
                  'mso-position-vertical-relative:margin" o:allowincell="f"><v:imagedata r:id="rIdWm" o:title="watermark"/>'
                  '<w10:wrap anchorx="margin" anchory="margin"/></v:shape></w:pict></w:r>')
    header = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {NSX}><w:p><w:pPr><w:spacing w:after="0"/></w:pPr>'
              f'{wm_xml}</w:p></w:hdr>')
    has_contact = any(c.get(k) for k in ("name", "title", "phones", "email", "address", "website"))
    sig = {"font": b["sig_font"], "color": b["dark"]}
    if has_contact:
        phones = [p.strip() for p in re.split(r"[\n,;]+", c.get("phones") or "") if p.strip()]
        addr = [a.strip() for a in (c.get("address") or "").split("\n") if a.strip()]
        left = (para("", line=164) + "".join(
            para((run(L["mob"], dict(sig, size=9, sp=10)) if i == 0 else "") + f'<w:r>{rpr(dict(sig, size=9), False)}<w:tab/></w:r>'
                 + run(ph, dict(sig, size=9, sp=16)), tabs='<w:tab w:val="left" w:pos="840"/>', line=220) for i, ph in enumerate(phones))) \
            if phones else para("")
        right = "".join([para(run(c["email"], dict(sig, size=8, sp=8)), line=260, jc="right") if c.get("email") else ""]
                        + [para(run(a, dict(sig, size=6.5, sp=8)), line=190, jc="right", rtl=dir_rtl(a)) for a in addr]
                        + [para(run(c["website"], dict(sig, size=8, sp=8)), line=220, jc="right") if c.get("website") else ""]) or para("")
        footer_body = (
            para(run(c.get("name"), dict(sig, size=15, sp=20)) + (f'<w:r>{rpr(dict(sig, size=11.5), False)}<w:tab/></w:r>'
                                                                  + run(L["to"] + " " + cx["client"], dict(sig, size=11.5, sp=10)) if cx["client"] else ""),
                 bdr=f'<w:pBdr><w:top w:val="single" w:sz="12" w:space="3" w:color="{hex6(b["dark"])}"/></w:pBdr>',
                 tabs=f'<w:tab w:val="right" w:pos="{W - 150}"/>', line=360)
            + para(run(c.get("title"), dict(sig, size=10.5, sp=14)), line=220)
            + para("", bdr=f'<w:pBdr><w:bottom w:val="single" w:sz="12" w:space="3" w:color="{hex6(b["dark"])}"/></w:pBdr>', before=940, line=20)
            + para("", line=120)
            + f'<w:tbl><w:tblPr>{bidi_t}<w:tblW w:w="{W}" w:type="dxa"/>{nobdr}<w:tblLayout w:type="fixed"/><w:tblCellMar>'
              '<w:left w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid><w:gridCol w:w="3600"/>'
              f'<w:gridCol w:w="3400"/><w:gridCol w:w="{W - 7000}"/></w:tblGrid><w:tr>{tcell(left, 3600, v="top", mar=0)}'
              f'{tcell(para(""), 3400, mar=0)}{tcell(right, W - 7000, v="top", mar=0)}</w:tr></w:tbl>')
    else:
        footer_body = para(run("   ·   ".join(x for x in (cx["company"], b["footer"]) if x), {"size": 8, "color": "#7A7A7A"}),
                           jc="center", line=240,
                           bdr=f'<w:pBdr><w:top w:val="single" w:sz="6" w:space="4" w:color="{hex6(b["text"])}"/></w:pBdr>')
    footer = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {NSX}>{footer_body}</w:ftr>'
    bottom = 3300 if has_contact else 1134
    doc_rels += [f'<Relationship Id="rIdStyles" Type="{REL}styles" Target="styles.xml"/>',
                 f'<Relationship Id="rIdHdr" Type="{REL}header" Target="header1.xml"/>',
                 f'<Relationship Id="rIdFtr" Type="{REL}footer" Target="footer1.xml"/>']
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NSX}><w:body>{"".join(body)}<w:sectPr>'
           '<w:headerReference w:type="default" r:id="rIdHdr"/><w:footerReference w:type="default" r:id="rIdFtr"/>'
           f'<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="560" w:right="300" w:bottom="{bottom}" w:left="520" w:header="0" '
           'w:footer="260" w:gutter="0"/><w:cols w:space="720"/></w:sectPr></w:body></w:document>')
    F = X(b["font"] or "Arial")
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/'
              f'wordprocessingml/2006/main"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="{F}" w:eastAsia="{F}" w:hAnsi="{F}" '
              f'w:cs="{F}"/><w:sz w:val="24"/><w:szCs w:val="24"/><w:lang w:val="en-US" w:eastAsia="en-US" w:bidi="ar-EG"/></w:rPr>'
              '</w:rPrDefault><w:pPrDefault/></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name '
              'w:val="Normal"/><w:qFormat/></w:style><w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val='
              '"Normal Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" '
              'w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
              '</w:styles>')
    parts = [
        ("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/'
         'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
         '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Default '
         'Extension="jpeg" ContentType="image/jpeg"/><Override PartName="/word/document.xml" ContentType="application/vnd.'
         'openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType='
         '"application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/header1.xml" '
         'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/><Override PartName="/word/'
         'footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/><Override '
         'PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>'),
        ("_rels/.rels", rels([f'<Relationship Id="rId1" Type="{REL}officeDocument" Target="word/document.xml"/>',
                              '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
                              'core-properties" Target="docProps/core.xml"/>'])),
        ("docProps/core.xml", core_xml(fill(o.get("title"), cx), cx["company"])),
        ("word/document.xml", doc), ("word/styles.xml", styles), ("word/header1.xml", header), ("word/footer1.xml", footer),
        ("word/_rels/document.xml.rels", rels(doc_rels)),
    ] + files
    if hdr_rels:
        parts.append(("word/_rels/header1.xml.rels", rels(hdr_rels)))
    return zip_files(parts)


OFFER_CSS = """
body{margin:0;background:#fff}
.odoc{position:relative;overflow:hidden;background:#fff;color:#111;padding:26px 22px 20px;font-size:12pt;display:flex;
  flex-direction:column;min-height:1000px;box-sizing:border-box}
.odoc .doc-body{position:relative;z-index:1;flex:1}
.odoc img.wm{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:62%;max-width:560px;pointer-events:none;z-index:0}
.o-head{display:flex;justify-content:space-between;align-items:flex-end;min-height:124px}
.o-date{font-size:14pt;letter-spacing:.4px}
.o-title{border-top:1.5pt solid;border-bottom:1.5pt solid;padding:3pt 0;margin:6pt 0 4pt;font-size:14pt;letter-spacing:.3px}
.o-h{font-size:14pt;margin-top:10pt;line-height:17.2pt;letter-spacing:.3px}
.o-p{line-height:14.4pt;letter-spacing:.2px;white-space:pre-wrap}
.o-p.b{font-weight:700;font-size:11pt;letter-spacing:0}
.o-tab{display:inline-block;min-width:194pt}
.o-t{width:calc(100% - 10px);margin:6px 0 8px;font-size:11pt;border-collapse:collapse}
.o-t td,.o-t th{border:1px solid #BFBFBF;padding:3pt 6pt}.o-t th{color:#fff}
.o-foot{position:relative;z-index:1;margin-top:28px}
.o-foot.simple{border-top:1px solid;padding-top:5px;text-align:center;font-size:8pt;color:#777}
.o-sig{border-top:1.5pt solid;padding-top:3pt;display:flex;justify-content:space-between;align-items:flex-start}
.o-rule{border-bottom:1.5pt solid;margin-top:46pt}
.o-contact{display:flex;justify-content:space-between;margin-top:8pt;line-height:1.35}
.bar{position:sticky;top:0;background:#f3f3f3;padding:8px;text-align:center;z-index:5;font:13px Arial}
@page{size:A4;margin:12mm 10mm}
@media print{.bar{display:none}.odoc{padding:0;min-height:0}.odoc img.wm{position:fixed}}
"""


def offer_html(db, o, kind="offer_pdf", auto_print=True, preview=False):
    blocks, cx = offer_blocks(db, o)
    rtl_doc = cx["lang"] == "ar"
    L = DOC_LABELS[cx["lang"]]
    b = branding.get(db)
    c = b["contact"]
    ta = lambda a: "end" if a == "right" else "center" if a == "center" else "start"  # noqa: E731
    tone = lambda t: b["dark"] if t == "dark" else b["accent"] if t == "accent" else b["text"]  # noqa: E731
    logo = branding.logo_for(db, kind)
    out = []
    for x in blocks:
        t = x["t"]
        if t == "head":
            left = (f'<img src="data:{logo["mime"]};base64,{logo["data"]}" alt="" style="max-height:{min(int(b["logo_height"]), 170)}px;'
                    'max-width:330px">' if logo else
                    f'<div style="font-size:20pt;font-weight:700;color:{b["dark"]}">{E(cx["company"])}</div>')
            out.append(f'<div class="o-head"><div>{left}</div><div class="o-date" style="color:{b["dark"]}">{E(cx["date"])}</div></div>')
        elif t == "title":
            out.append(f'<div class="o-title" dir="auto" style="border-color:{b["text"]}"><span style="color:{b["dark"]}">'
                       f'{E(x["lead"])}</span><span style="color:{b["text"]}">{E(x["rest"])}</span></div>')
        elif t == "h":
            out.append(f'<div class="o-h" dir="auto" style="color:{b["dark"]}">{E(x["text"])}</div>')
        elif t == "p":
            for i, ln in enumerate(str(x.get("text") or "").split("\n")):
                st = f"color:{tone(x.get('tone'))};" + ("" if i else f"margin-top:{(x.get('before') or 0) / 20}pt;") + \
                     (f"padding-inline-start:{x['indent'] / 20}pt" if x.get("indent") else "")
                out.append(f'<div class="o-p{" b" if x.get("bold") else ""}" dir="auto" style="{st}">{E(ln) or "&nbsp;"}</div>')
        elif t == "tabs":
            out.append('<div style="margin-top:2.4pt">' + "".join(
                f'<div class="o-p" style="color:{b["text"]}"><span class="o-tab">{E(r[0])}</span>{E(r[1])}</div>' for r in x["rows"]) + "</div>")
        elif t in ("table", "kv"):
            tb = {"widths": [32, 68], "rows": x["rows"]} if t == "kv" else x
            al = tb.get("align") or [None] * 9
            foot = tb.get("foot") or []
            out.append('<table class="o-t"><colgroup>' + "".join(f'<col style="width:{w}%">' for w in tb["widths"]) + "</colgroup>"
                       + ("<tr>" + "".join(f'<th style="background:{b["dark"]};text-align:{ta(al[i])}">{E(h)}</th>'
                                           for i, h in enumerate(tb["head"])) + "</tr>" if tb.get("head") else "")
                       + "".join("<tr>" + "".join(
                           f'<td dir="auto" style="color:{b["text"]};text-align:{ta(al[i])};'
                           f'{"background:#F2F2F2;font-weight:700" if t == "kv" and not i else ""}">{E(v)}</td>'
                           for i, v in enumerate(r)) + "</tr>" for r in tb["rows"])
                       + "".join("<tr>" + "".join(
                           f'<td style="font-weight:700;text-align:{ta(al[i])};'
                           f'{"background:" + b["dark"] + ";color:#fff" if j == len(foot) - 1 else "background:#F2F2F2"}">{E(v)}</td>'
                           for i, v in enumerate(r)) + "</tr>" for j, r in enumerate(foot)) + "</table>")
    phones = [p.strip() for p in re.split(r"[\n,;]+", c.get("phones") or "") if p.strip()]
    if any(c.get(k) for k in ("name", "title", "phones", "email", "address", "website")):
        foot = (f'<div class="o-foot" style="font-family:\'{E(b["sig_font"])}\',Georgia,serif;color:{b["dark"]}">'
                f'<div class="o-sig" style="border-color:{b["dark"]}"><div><div style="font-size:15pt;letter-spacing:1px">{E(c.get("name"))}</div>'
                f'<div style="font-size:10.5pt">{E(c.get("title"))}</div></div>'
                + (f'<div style="font-size:11.5pt">{E(L["to"])} {E(cx["client"])}</div>' if cx["client"] else "") + "</div>"
                f'<div class="o-rule" style="border-color:{b["dark"]}"></div><div class="o-contact"><div style="font-size:9pt">'
                + "".join(f'<div><span style="display:inline-block;width:42pt">{"" if i else E(L["mob"])}</span>{E(p)}</div>'
                          for i, p in enumerate(phones)) + '</div><div style="text-align:end">'
                + (f'<div style="font-size:8pt">{E(c.get("email"))}</div>' if c.get("email") else "")
                + "".join(f'<div style="font-size:6.5pt" dir="auto">{E(a)}</div>' for a in (c.get("address") or "").split("\n") if a.strip())
                + (f'<div style="font-size:8pt">{E(c.get("website"))}</div>' if c.get("website") else "") + "</div></div></div>")
    elif cx["company"] or b["footer"]:
        foot = f'<div class="o-foot simple" style="border-color:{b["text"]}">{E("   ·   ".join(v for v in (cx["company"], b["footer"]) if v))}</div>'
    else:
        foot = ""
    wm = branding.watermark_for(db, kind)
    wm_html = f'<img class="wm" src="data:image/png;base64,{wm["data"]}" alt="">' if wm else ""
    return (f'<!doctype html><html lang="{cx["lang"]}" dir="{"rtl" if rtl_doc else "ltr"}"><head><meta charset="utf-8">'
            f'<title>{E(fill(o.get("title"), cx))}</title><style>{OFFER_CSS}</style></head><body>'
            + ("" if preview else '<div class="bar"><button onclick="print()">Print / Save as PDF</button></div>')
            + f'<div class="odoc" style="font-family:\'{E(b["font"])}\',Arial,sans-serif">{wm_html}<div class="doc-body">{"".join(out)}</div>{foot}</div>'
            + ('<script>addEventListener("load",()=>setTimeout(()=>print(),350))</script>' if auto_print and not preview else "")
            + "</body></html>")


# ------------------------------------------------------------------ offer storage
def _content(d):
    lang = "ar" if d.get("lang") == "ar" else "en"
    secs = []
    for s in d.get("sections") or []:
        if not isinstance(s, dict) or s.get("kind") not in SECTION_KINDS:
            continue
        x = {"kind": s["kind"], "heading": text(s.get("heading"), maxlen=300), "style": "table" if s.get("style") == "table" else "lines"}
        if s["kind"] in ("text", "signature"):
            x.update(body=text(s.get("body"), maxlen=8000), bold=bool(s.get("bold")))
        if s["kind"] == "pricing":
            x.update(items=[{"desc": text(i.get("desc"), maxlen=1000), "amount": acc.num(i.get("amount"), "amount")}
                            for i in (s.get("items") or [])], discount=acc.num(s.get("discount"), "discount"),
                     vat=acc.num(s.get("vat"), "VAT %"), words=s.get("words") is not False)
        if s["kind"] == "packages":
            ts = [t for t in (s.get("tiers") or []) if isinstance(t, dict)][:MAX_TIERS]
            x.update(tiers=[{"name": text(t.get("name"), maxlen=120), "price": acc.num(t.get("price"), "price"),
                             "includes": text(t.get("includes"), maxlen=4000)} for t in ts],
                     vat=acc.num(s.get("vat"), "VAT %"))
            try:
                ch = int(s.get("chosen", -1))
            except (TypeError, ValueError):
                ch = -1
            x["chosen"] = ch if 0 <= ch < len(x["tiers"]) else -1
        if s["kind"] == "bank":
            pick = text(s.get("bank_id"), maxlen=40)
            if pick:
                x["bank_id"] = pick
            if pick == "custom":
                x["bank_details"] = clean_bank(s.get("bank_details"))
        if s["kind"] == "payment":
            x["milestones"] = [{"label": text(m.get("label"), maxlen=500), "pct": acc.num(m.get("pct"), "%")}
                               for m in (s.get("milestones") or [])]
        secs.append(x)
    return {"lang": lang, "title": text(d.get("title"), maxlen=500), "intro": text(d.get("intro"), maxlen=4000),
            "sections": secs, "closing": text(d.get("closing"), maxlen=4000)}


def _row_offer(r):
    o = dict(r)
    o.update(json.loads(o.pop("content") or "{}"))
    o["net"] = offer_totals(pricing(o))["net"]
    return o


def list_offer_types(db):
    return [dict({"id": r["id"], "name": r["name"]}, **json.loads(r["content"] or "{}")) for r in
            db.q("SELECT * FROM offer_types ORDER BY id")]


def save_offer_type(db, d, tid=None):
    name = text(d.get("name"), "name", required=True, maxlen=200)
    content = json.dumps(_content(d), ensure_ascii=False)
    if tid:
        db.x("UPDATE offer_types SET name=?, content=? WHERE id=?", (name, content, tid))
        return tid
    return db.x("INSERT INTO offer_types(name, content) VALUES (?,?)", (name, content)).lastrowid


def next_offer_number(db):
    prefix = (db.setting("offer_prefix") or "OFR-").replace("{YYYY}", acc.today()[:4])
    digits = int(float(db.setting("offer_digits") or 4))
    n = 0
    for r in db.q("SELECT number FROM offers WHERE number LIKE ?", (prefix + "%",)):
        tail = r["number"][len(prefix):]
        if tail.isdigit():
            n = max(n, int(tail))
    return f"{prefix}{n + 1:0{digits}d}"


def list_offers(db, type_id=None, q=None, status=None):
    w, p = ["1=1"], []
    if type_id:
        w.append("o.type_id=?")
        p.append(int(type_id))
    if status:
        w.append("o.status=?")
        p.append(status)
    if q:
        w.append("(o.client LIKE ? OR o.project LIKE ? OR o.number LIKE ?)")
        p += [f"%{q}%"] * 3
    rows = db.q(f"SELECT o.*, t.name AS type_name, u.code AS bu_code FROM offers o LEFT JOIN offer_types t ON t.id=o.type_id "
                f"LEFT JOIN business_units u ON u.id=o.bu_id WHERE {' AND '.join(w)} ORDER BY o.date DESC, o.id DESC LIMIT 1000", p)
    return [_row_offer(r) for r in rows]


def get_offer(db, oid):
    r = db.one("SELECT o.*, t.name AS type_name, u.code AS bu_code FROM offers o LEFT JOIN offer_types t ON t.id=o.type_id "
               "LEFT JOIN business_units u ON u.id=o.bu_id WHERE o.id=?", (oid,))
    if not r:
        raise ValidationError("Offer not found")
    return _row_offer(r)


def save_offer(db, d, oid=None):
    status = d.get("status") if d.get("status") in OFFER_STATUS else "Draft"
    vals = (int(d["type_id"]) if d.get("type_id") else None, acc.clean_date(d.get("date") or acc.today()),
            text(d.get("client"), maxlen=300), text(d.get("project"), maxlen=300), acc.bu_id_or_none(db, d.get("bu_id")),
            status, json.dumps(_content(d), ensure_ascii=False))
    if oid:
        if not db.one("SELECT 1 FROM offers WHERE id=?", (oid,)):
            raise ValidationError("Offer not found")
        db.x("UPDATE offers SET type_id=?, date=?, client=?, project=?, bu_id=?, status=?, content=? WHERE id=?", vals + (oid,))
        return oid
    number = text(d.get("number")) or next_offer_number(db)
    return db.x("INSERT INTO offers(number, type_id, date, client, project, bu_id, status, content) VALUES (?,?,?,?,?,?,?,?)",
                (number,) + vals).lastrowid


def offer_filename(o):
    name = o.get("type_name") or "Offer"
    if o.get("project"):
        name += f" - {o['project']}"
    if o.get("client"):
        name += f" ({o['client']})"
    return safe_name(name)


# ------------------------------------------------ logo / watermark on other generated Excel files
def inject_xlsx_images(data, logo=None, wm=None, sheet_name=None, col=None, row=1, px=70):
    """Add a logo (top corner) and/or a header watermark to an existing .xlsx (e.g. one written by openpyxl)."""
    if not logo and not wm:
        return data
    zin = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zin.read(n) for n in zin.namelist()}
    wb = files["xl/workbook.xml"].decode("utf-8")
    wbrels = files["xl/_rels/workbook.xml.rels"].decode("utf-8")
    sheets = re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', wb) or \
        [(a, b) for b, a in re.findall(r'<sheet [^>]*r:id="([^"]+)"[^>]*name="([^"]+)"', wb)]
    rid = next((r for n, r in sheets if sheet_name and _html.unescape(n) == sheet_name), sheets[0][1] if sheets else None)
    m = re.search(r'<Relationship [^>]*Id="%s"[^>]*Target="([^"]+)"' % rid, wbrels) or \
        re.search(r'<Relationship [^>]*Target="([^"]+)"[^>]*Id="%s"' % rid, wbrels)
    target = m.group(1).lstrip("/")
    path = target if target.startswith("xl/") else "xl/" + target
    sxml = files[path].decode("utf-8")
    rels_path = path.rsplit("/", 1)[0] + "/_rels/" + path.rsplit("/", 1)[1] + ".rels"
    srels = files.get(rels_path, rels([]).encode()).decode("utf-8")
    ct = files["[Content_Types].xml"].decode("utf-8")
    n = 1
    while f"xl/drawings/drawing{n}.xml" in files or f"xl/drawings/vmlDrawingHF{n}.vml" in files:
        n += 1
    add, tail, tail_d = [], "", ""

    def new_rel(typ, tgt):
        i = 1
        while f'Id="rIdB{i}"' in srels:
            i += 1
        return f"rIdB{i}", f'<Relationship Id="rIdB{i}" Type="{REL}{typ}" Target="{tgt}"/>'
    if logo and "<drawing " not in sxml:
        ext = "png" if logo["mime"] == "image/png" else "jpeg"
        wpx = round(px * (logo["w"] or 1) / (logo["h"] or 1))
        files[f"xl/media/beyond_logo{n}.{ext}"] = b64bytes(logo["data"])
        files[f"xl/drawings/drawing{n}.xml"] = drawing_xml(col or 1, row, wpx, px, 1024 + n).encode()
        files[f"xl/drawings/_rels/drawing{n}.xml.rels"] = rels(
            [f'<Relationship Id="rId1" Type="{REL}image" Target="../media/beyond_logo{n}.{ext}"/>']).encode()
        r_id, rel_xml = new_rel("drawing", f"../drawings/drawing{n}.xml")
        srels = srels.replace("</Relationships>", rel_xml + "</Relationships>")
        tail_d = f'<drawing r:id="{r_id}"/>'
        add.append(f'<Override PartName="/xl/drawings/drawing{n}.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>')
    if wm and "<legacyDrawingHF" not in sxml:
        files[f"xl/media/beyond_wm{n}.png"] = b64bytes(wm["data"])
        for name, body in wm_vml_parts():
            name = name.replace("vmlDrawingHF1", f"vmlDrawingHF{n}")
            files[name] = body.replace("watermark.png", f"beyond_wm{n}.png").encode()
        r_id, rel_xml = new_rel("vmlDrawing", f"../drawings/vmlDrawingHF{n}.vml")
        srels = srels.replace("</Relationships>", rel_xml + "</Relationships>")
        tail += f'<legacyDrawingHF r:id="{r_id}"/>'
        if "<headerFooter" in sxml:
            if "<oddHeader>" in sxml:
                sxml = re.sub(r"<oddHeader>(.*?)</oddHeader>", lambda mm: "<oddHeader>&amp;C&amp;G" + re.sub(r"&amp;C[^&]*", "", mm.group(1)) + "</oddHeader>", sxml)
            else:
                sxml = re.sub(r"<headerFooter([^>]*)/>", r"<headerFooter\1><oddHeader>&amp;C&amp;G</oddHeader></headerFooter>", sxml)
                sxml = re.sub(r"<headerFooter([^>]*)>(?!<oddHeader)", r"<headerFooter\1><oddHeader>&amp;C&amp;G</oddHeader>", sxml, count=1)
        else:
            hf = "<headerFooter><oddHeader>&amp;C&amp;G</oddHeader></headerFooter>"
            anchor = re.search(r"<(rowBreaks|colBreaks|customProperties|cellWatches|ignoredErrors|smartTags|drawing|legacyDrawing|"
                               r"legacyDrawingHF|picture|oleObjects|controls|webPublishItems|tableParts|extLst)\b", sxml)
            sxml = sxml[:anchor.start()] + hf + sxml[anchor.start():] if anchor else sxml.replace("</worksheet>", hf + "</worksheet>")
    if 'Extension="vml"' not in ct:
        add.append('<Default Extension="vml" ContentType="application/vnd.openxmlformats-officedocument.vmlDrawing"/>')
    for ext, mime in (("png", "image/png"), ("jpeg", "image/jpeg")):
        if f'Extension="{ext}"' not in ct:
            add.append(f'<Default Extension="{ext}" ContentType="{mime}"/>')
    files["[Content_Types].xml"] = ct.replace("</Types>", "".join(add) + "</Types>").encode()
    if "xmlns:r=" not in sxml[:600]:
        sxml = sxml.replace("<worksheet ", '<worksheet xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ', 1)
    # schema order: ... headerFooter, drawing, legacyDrawing, legacyDrawingHF, picture, ..., tableParts, extLst
    for piece, after in ((tail_d, r"<(legacyDrawing|legacyDrawingHF|picture|oleObjects|controls|webPublishItems|tableParts|extLst)\b"),
                         (tail, r"<(picture|oleObjects|controls|webPublishItems|tableParts|extLst)\b")):
        if piece:
            anchor = re.search(after, sxml)
            sxml = sxml[:anchor.start()] + piece + sxml[anchor.start():] if anchor else sxml.replace("</worksheet>", piece + "</worksheet>")
    files[path] = sxml.encode("utf-8")
    files[rels_path] = srels.encode("utf-8")
    return zip_files(list(files.items()))


def report_brand(db, fmt):
    kind = {"pdf": "report_pdf", "html": "report_pdf", "xlsx": "report_xlsx", "docx": "report_docx"}.get(fmt)
    return (branding.logo_for(db, kind), branding.watermark_for(db, kind)) if kind else (None, None)
