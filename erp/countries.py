"""Countries: a company picks its country (Settings → Company, or the quick setup) and gets the defaults that fit how
business is usually done there. Every default can be overridden in Settings; profiles grow over time.

Only facts are kept here (ISO codes, currencies, the language documents are usually written in). Ways of working are
added per country only when they are known — today Egypt's contract-vs-actual invoices with a fee % per section,
its e-invoicing link and its usual BOQ terms. A company that has not picked a country keeps the layout it always had.
"""
from . import branding

# code, English name, Arabic name, currency, document language
COUNTRIES = [
    ("EG", "Egypt", "مصر", "EGP", "ar"),
    ("SA", "Saudi Arabia", "السعودية", "SAR", "ar"),
    ("AE", "United Arab Emirates", "الإمارات", "AED", "ar"),
    ("KW", "Kuwait", "الكويت", "KWD", "ar"),
    ("QA", "Qatar", "قطر", "QAR", "ar"),
    ("BH", "Bahrain", "البحرين", "BHD", "ar"),
    ("OM", "Oman", "عُمان", "OMR", "ar"),
    ("JO", "Jordan", "الأردن", "JOD", "ar"),
    ("LB", "Lebanon", "لبنان", "LBP", "ar"),
    ("IQ", "Iraq", "العراق", "IQD", "ar"),
    ("LY", "Libya", "ليبيا", "LYD", "ar"),
    ("TN", "Tunisia", "تونس", "TND", "ar"),
    ("DZ", "Algeria", "الجزائر", "DZD", "ar"),
    ("MA", "Morocco", "المغرب", "MAD", "ar"),
    ("SD", "Sudan", "السودان", "SDG", "ar"),
    ("YE", "Yemen", "اليمن", "YER", "ar"),
    ("TR", "Türkiye", "تركيا", "TRY", "en"),
    ("GB", "United Kingdom", "المملكة المتحدة", "GBP", "en"),
    ("IE", "Ireland", "أيرلندا", "EUR", "en"),
    ("DE", "Germany", "ألمانيا", "EUR", "en"),
    ("FR", "France", "فرنسا", "EUR", "en"),
    ("IT", "Italy", "إيطاليا", "EUR", "en"),
    ("ES", "Spain", "إسبانيا", "EUR", "en"),
    ("NL", "Netherlands", "هولندا", "EUR", "en"),
    ("US", "United States", "الولايات المتحدة", "USD", "en"),
    ("CA", "Canada", "كندا", "CAD", "en"),
    ("AU", "Australia", "أستراليا", "AUD", "en"),
    ("IN", "India", "الهند", "INR", "en"),
    ("PK", "Pakistan", "باكستان", "PKR", "en"),
    ("NG", "Nigeria", "نيجيريا", "NGN", "en"),
    ("KE", "Kenya", "كينيا", "KES", "en"),
    ("ZA", "South Africa", "جنوب أفريقيا", "ZAR", "en"),
]
BY_CODE = {c[0]: c for c in COUNTRIES}

# invoice styles: how a progress invoice (extract) is laid out
#   contract_actual — contract quantity and total next to what was executed and its actual total, completion %, and a
#                     fee % per section on top of the actual (cost-plus); the usual layout in Egypt
#   simple          — the executed quantity × price (with completion % when used); fee % per section when set
STYLES = ("contract_actual", "simple")

# what is known per country, beyond the facts in COUNTRIES
PROFILES = {
    "EG": {"invoice_style": "contract_actual", "e_invoice": "eta", "boq_notes": branding.DEFAULT_BOQ_NOTES,
           "methods": ["Instapay"]},
}
# payment methods that exist only in some countries (still accepted everywhere, so older records keep their value)
LOCAL_METHODS = {m for p in PROFILES.values() for m in p.get("methods", [])}


def methods(db):
    """The payment methods offered on screens and templates: the universal ones plus the company country's own."""
    from . import accounting as acc
    code = (db.setting("country", "") or "").upper()
    extra = PROFILES.get(code, {}).get("methods", [])
    return [m for m in acc.PAYMENT_METHODS if m not in LOCAL_METHODS or m in extra]


def listing():
    """For the screens: [{code, name, name_ar, currency, doc_lang, invoice_style}]."""
    return [{"code": c, "name": en, "name_ar": ar, "currency": cur, "doc_lang": lang,
             "invoice_style": PROFILES.get(c, {}).get("invoice_style", "simple")} for c, en, ar, cur, lang in COUNTRIES]


def profile(db):
    """The company's country and what follows from it; settings 'invoice_style' and 'doc_lang' override the country's.
    No country picked: the layout the app always had (contract vs actual, Arabic documents)."""
    code = (db.setting("country", "") or "").upper()
    row = BY_CODE.get(code)
    p = PROFILES.get(code, {})
    base = {"invoice_style": "contract_actual", "doc_lang": "ar"} if not row else \
        {"invoice_style": p.get("invoice_style", "simple"), "doc_lang": row[4]}
    style = db.setting("invoice_style", "") or base["invoice_style"]
    lang = db.setting("doc_lang", "") or base["doc_lang"]
    return {"country": code if row else "", "invoice_style": style if style in STYLES else base["invoice_style"],
            "doc_lang": lang if lang in ("ar", "en") else base["doc_lang"],
            "e_invoice": p.get("e_invoice", "") if row else "",
            "boq_notes": p.get("boq_notes", "") if row else branding.DEFAULT_BOQ_NOTES}
