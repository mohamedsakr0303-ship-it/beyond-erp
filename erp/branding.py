"""Company branding for generated files: logo, watermark, colours, fonts, contact block.

Images live in the `assets` table (base64). The browser prepares them (resize, transparent
background, faded watermark) when you upload in Settings -> Documents & branding, so the server
only has to embed them — no imaging library needed in either edition.

`apply` decides, per kind of generated file, whether it carries the logo and/or the watermark.
"""
import copy
import json

from .accounting import ValidationError, text

# (key, English, Arabic, logo by default, watermark by default)
DOC_KINDS = [
    ("offer_docx", "Offers — Word (.docx)", "العروض — Word", True, True),
    ("offer_pdf", "Offers — PDF / print", "العروض — PDF / طباعة", True, True),
    ("boq_xlsx", "Quotations (BOQ) — Excel", "المقايسات — Excel", True, False),
    ("boq_pdf", "Quotations (BOQ) — PDF / print", "المقايسات — PDF / طباعة", True, False),
    ("invoice_xlsx", "Invoices / extracts — Excel", "المستخلصات والفواتير — Excel", True, False),
    ("invoice_pdf", "Invoices / extracts — PDF / print", "المستخلصات والفواتير — PDF / طباعة", True, False),
    ("report_pdf", "Financial statements & lists — PDF / print", "القوائم المالية والكشوف — PDF / طباعة", True, False),
    ("report_xlsx", "Financial statements & lists — Excel", "القوائم المالية والكشوف — Excel", True, False),
    ("report_docx", "Financial statements & lists — Word", "القوائم المالية والكشوف — Word", True, False),
    ("workbook", "Full accounting workbook (Excel)", "مصنف الحسابات الكامل (Excel)", True, False),
    # off = universal templates (BeyondERP logo, "Company Name", "Company Slogan"); on = this company's logo, name and slogan
    ("template", "Excel import templates — use our own logo, name & slogan", "قوالب الاستيراد (Excel) — بشعارنا واسمنا ووصفنا", False, False),
]
GENERIC_NAME, GENERIC_TAGLINE = "Company Name", "Company Slogan"
KIND_KEYS = [k[0] for k in DOC_KINDS]

DEFAULT_BOQ_NOTES = "\n".join([
    "المقايسة تحت العجز و الزيادة و يتم المحاسبة على ما تم تنفيذه فعليا في الطبيعة",
    "الأسعار لتوريدات الأعمال الصحية و الكهرباء طبقا لسعر الصرف اليوم و أي تغيير في سعر الصرف يترتب عليه زيادة في الأسعار",
    "الأسعار غير شاملة ضريبة القيمة المضافة أو التأمينات الاجتماعية",
    "المقايسة غير شاملة لبعض البنود و سيتم تسعيرها لاحقا طبقا لاختيارات المالك",
    "توريدات السيراميك و البورسلين و الأجهزة الصحية (ما عدا بلاط بازلتون الأسطح) تكلفتها مباشرة على المالك مع التزامنا "
    "بالتواجد وقت الاختيار و الشراء و النقل و التشوين",
    "يتم سداد 20% من قيمة المقايسة دفعة مقدمة على أن يتم خصمها بنفس النسبة من المستخلصات المقدمة للمالك طبقا لتقدم الأعمال",
])

DEFAULTS = {
    "dark": "#1B1B1B", "accent": "#C00000", "light": "#D9D9D9", "text": "#505151",
    "font": "Arial", "sig_font": "Bodoni MT Black", "logo_height": 150, "clean_bg": True,
    "cur_words": "Egyptian Pounds", "cur_fraction": "Piasters", "cur_words_ar": "جنيه مصري", "cur_fraction_ar": "قرش",
    "footer": "",
    "contact": {"name": "", "title": "", "phones": "", "email": "", "address": "", "website": ""},
    "wm": {"mode": "logo", "text": "", "opacity": 0.16, "grey": True},
    "apply": {k: {"logo": lg, "wm": wm} for k, _, _, lg, wm in DOC_KINDS},
    "boq_notes": DEFAULT_BOQ_NOTES,
}
ASSET_KEYS = ("logo", "logo_raw", "watermark", "wm_image", "app_logo", "app_icon")
MAX_ASSET = 3 * 1024 * 1024  # base64 characters


def get(db):
    try:
        saved = json.loads(db.setting("branding") or "{}")
    except json.JSONDecodeError:
        saved = {}
    b = copy.deepcopy(DEFAULTS)
    for k, v in saved.items():
        if k in ("contact", "wm") and isinstance(v, dict):
            b[k].update({kk: vv for kk, vv in v.items() if kk in b[k]})
        elif k == "apply" and isinstance(v, dict):
            for kind, flags in v.items():
                if kind in b["apply"] and isinstance(flags, dict):
                    b["apply"][kind].update({f: bool(flags.get(f)) for f in ("logo", "wm") if f in flags})
        elif k in b:
            b[k] = v
    if b["boq_notes"] == DEFAULT_BOQ_NOTES:      # never changed: the usual terms of the company's country
        from . import countries
        b["boq_notes"] = countries.profile(db)["boq_notes"]
    cur = (db.setting("currency", "") or "").upper()
    if "cur_words" not in saved and cur and cur != "EGP":   # amounts in words name the company's own currency
        b.update({"cur_words": cur, "cur_fraction": "", "cur_words_ar": cur, "cur_fraction_ar": ""})
    return b


def save(db, d):
    b = get(db)
    for k, v in (d or {}).items():
        if k not in DEFAULTS:
            continue
        if k in ("contact", "wm", "apply"):
            if not isinstance(v, dict):
                raise ValidationError(f"{k} must be an object")
            if k == "apply":
                for kind, flags in v.items():
                    if kind in b["apply"] and isinstance(flags, dict):
                        b["apply"][kind] = {"logo": bool(flags.get("logo")), "wm": bool(flags.get("wm"))}
            else:
                for kk, vv in v.items():
                    if kk in b[k]:
                        b[k][kk] = vv if isinstance(vv, (bool, int, float)) else text(vv, maxlen=2000)
        elif k in ("dark", "accent", "light", "text"):
            s = text(v)
            if not (len(s) == 7 and s.startswith("#")):
                raise ValidationError(f"{k}: use a colour like #1B1B1B")
            b[k] = s.upper()
        elif k == "logo_height":
            b[k] = max(40, min(170, int(float(v or 150))))
        elif k == "clean_bg":
            b[k] = bool(v)
        else:
            b[k] = text(v, maxlen=6000)
    if b["wm"]["mode"] not in ("none", "logo", "image", "text"):
        b["wm"]["mode"] = "none"
    b["wm"]["opacity"] = max(0.03, min(0.6, float(b["wm"].get("opacity") or 0.16)))
    db.set_setting("branding", json.dumps(b, ensure_ascii=False))
    return b


# ------------------------------------------------------------------ images
def asset(db, key):
    r = db.one("SELECT key, mime, w, h, data FROM assets WHERE key=?", (key,))
    return r if r and r["data"] else None


def set_asset(db, key, img):
    if key not in ASSET_KEYS:
        raise ValidationError("Unknown image")
    if not img:
        db.x("DELETE FROM assets WHERE key=?", (key,))
        return
    data = text(img.get("data"), maxlen=MAX_ASSET)
    mime = img.get("mime") if img.get("mime") in ("image/png", "image/jpeg") else None
    if not data or not mime:
        raise ValidationError("Upload a PNG or JPEG image")
    import base64
    try:
        base64.b64decode(data, validate=True)
    except ValueError:
        raise ValidationError("The image data is damaged") from None
    db.x("INSERT OR REPLACE INTO assets(key,mime,w,h,data) VALUES (?,?,?,?,?)",
         (key, mime, int(img.get("w") or 0), int(img.get("h") or 0), data))


def logo_for(db, kind):
    """The logo to print on this kind of file, or None when switched off / not uploaded."""
    b = get(db)
    if kind and not b["apply"].get(kind, {}).get("logo"):
        return None
    return asset(db, "logo")


def watermark_for(db, kind):
    b = get(db)
    if b["wm"]["mode"] == "none" or not b["apply"].get(kind, {}).get("wm"):
        return None
    return asset(db, "watermark")


def template_identity(db):
    """Who an Excel import template is branded for: the universal BeyondERP identity unless the company switched
    'Excel import templates' on in Settings -> Documents & branding (then its own logo, name and slogan)."""
    from . import brand_assets
    if get(db)["apply"]["template"]["logo"]:
        return {"own": True, "name": db.setting("company_name", "") or GENERIC_NAME,
                "tagline": db.setting("company_tagline", ""), "logo": asset(db, "logo"), "watermark": watermark_for(db, "template")}
    return {"own": False, "name": GENERIC_NAME, "tagline": GENERIC_TAGLINE, "logo": brand_assets.LOGO,
            "watermark": watermark_for(db, "template")}


def public(db):
    """Branding for the settings screen (images included so the browser can preview and re-process them)."""
    out = get(db)
    out["kinds"] = [{"key": k, "en": en, "ar": ar} for k, en, ar, _, _ in DOC_KINDS]
    out["images"] = {k: asset(db, k) for k in ASSET_KEYS}
    return out
