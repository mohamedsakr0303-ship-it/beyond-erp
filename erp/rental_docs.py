"""Printable rental documents (print / save as PDF): price offer, rental contract, delivery and return handovers.

The handover follows the standard scaffolding handover & inspection form: site and parties, the list of pieces with
their condition and loss price, the custody / compensation / no-alteration terms, the safety checklist (yes / no /
notes), the final decision, and signature blocks for both parties. Everything except the price offer is signed.
"""
import html as _html

from . import accounting as acc
from . import branding, rentals
from .db import to_major

E = lambda s: _html.escape("" if s is None else str(s))  # noqa: E731

T = {
    "offer": ("Price offer", "عرض سعر"), "contract": ("Equipment rental contract", "عقد تأجير معدات"),
    "delivery": ("Delivery handover & safety inspection", "محضر تسليم وفحص"), "return": ("Return handover", "محضر استلام مرتجع"),
    "no": ("No.", "رقم"), "date": ("Date", "التاريخ"), "client": ("Client", "العميل"), "site": ("Site / project", "المشروع / الموقع"),
    "location": ("Location", "العنوان"), "valid": ("Valid until", "صالح حتى"), "contact": ("Contact", "المسؤول"),
    "lessor": ("Lessor (rental company)", "الشركة المؤجرة"), "lessee": ("Lessee (client)", "الشركة المستأجرة"),
    "po": ("Client purchase order", "أمر شراء العميل"), "start": ("Start", "البداية"), "duration": ("Expected duration", "المدة المتوقعة"),
    "months": ("months", "شهر"), "rent": ("Monthly rent", "الإيجار الشهري"), "desc": ("Description", "البيان"), "unit": ("Unit", "الوحدة"),
    "qty": ("Qty", "الكمية"), "rate": ("Rate / month", "السعر / شهر"), "monthly": ("Per month", "شهرياً"), "one": ("One-time charges", "مبالغ لمرة واحدة"),
    "amount": ("Amount", "المبلغ"), "total": ("Total", "الإجمالي"), "est": ("Estimated total for", "الإجمالي التقديري لمدة"),
    "vat": ("VAT", "ضريبة القيمة المضافة"), "notes": ("Notes", "ملاحظات"), "terms": ("Terms and conditions", "الشروط والأحكام"),
    "loss_terms": ("Legal and financial terms", "الشروط والأحكام القانونية والمالية"), "pieces": ("Pieces and loss / damage prices", "المكونات وأسعار الفقد / التلف"),
    "piece": ("Piece / component", "اسم القطعة / المكون"), "delivered": ("Qty delivered", "الكمية المسلمة"), "cond": ("Condition", "الحالة"),
    "good": ("Good", "سليمة"), "loss_price": ("Loss / damage price per piece", "سعر الفقد / التلف للقطعة"),
    "returned": ("Returned good", "المرتجع السليم"), "damaged": ("Damaged", "تالف"), "lost": ("Lost", "مفقود"), "charge": ("Charge", "المطالبة"),
    "check": ("Safety inspection checklist", "قائمة فحص السلامة"), "item": ("Inspection item", "بند الفحص الفني والسلامة"),
    "yes": ("Complies (yes)", "مطابق (نعم)"), "nok": ("Does not comply (no)", "غير مطابق (لا)"), "decision": ("Technical decision and acceptance", "القرار الفني واعتماد الاستلام"),
    "safe": ("Safe, to specification and ready for use.", "آمنة ومطابقة للمواصفات وجاهزة للاستخدام."),
    "unsafe": ("Not safe — rejected until the notes are fixed.", "غير آمنة ومرفوضة لحين تعديل الملاحظات."),
    "sig_us": ("For the lessor", "عن الشركة المؤجرة"), "sig_client": ("For the lessee", "عن الشركة المستأجرة"), "name": ("Name", "الاسم"),
    "signature": ("Signature", "التوقيع"), "units_rented": ("Rented", "المؤجر"), "serial": ("Serial", "الرقم التسلسلي"),
    "summary": ("Rented quantities", "الكميات المؤجرة"), "grand": ("Grand total", "الإجمالي العام"), "prepared": ("Prepared by", "إعداد"),
    "cat_scaffolding": ("scaffolding", "سقالات معدنية"), "cat_elevator": ("construction elevator", "مصعد إنشائي"),
    "cat_cradle_manual": ("cradle (manual)", "سقالة معلقة يدوية"), "cat_cradle_motorized": ("cradle (motorized)", "سقالة معلقة كهربائية"),
    "cat_other": ("equipment", "معدات"), "one_when": ("Billed", "تُحاسب"), "w_delivery": ("on delivery", "عند التسليم"),
    "w_return": ("on final return", "عند الإرجاع النهائي"), "w_manual": ("as agreed", "حسب الاتفاق"),
    "rent_basis": ("Rent is billed monthly on the quantity on site, counted by day.", "يُحاسب الإيجار شهرياً على الكمية الموجودة بالموقع باليوم."),
}

CSS = """
@page{size:A4;margin:14mm 12mm}
*{box-sizing:border-box}body{margin:0;color:#222;font:10.5pt/1.45 var(--f),Arial,sans-serif;background:#eee}
.bar{position:sticky;top:0;background:#333;padding:8px;text-align:center;z-index:5}.bar button{font:inherit;padding:6px 16px;cursor:pointer}
.page{background:#fff;max-width:210mm;margin:12px auto;padding:14mm 12mm;position:relative;box-shadow:0 0 12px rgba(0,0,0,.15)}
.wm{position:absolute;inset:0;margin:auto;max-width:70%;max-height:60%;opacity:.07;pointer-events:none}
.hd{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;border-bottom:2px solid var(--d);padding-bottom:8px;margin-bottom:10px}
.hd img{max-height:70px;max-width:220px}.co{font-size:8.5pt;color:#555}.co b{font-size:13pt;color:var(--d)}
h1{font-size:15pt;margin:4px 0 10px;color:var(--d);text-align:center}h2{font-size:11pt;margin:14px 0 6px;color:#fff;background:var(--d);padding:4px 8px}
table{width:100%;border-collapse:collapse;margin:4px 0}th,td{border:1px solid #999;padding:4px 6px;vertical-align:top}th{background:#f0f0f0;font-size:9pt}
td.n,th.n{text-align:end;white-space:nowrap}.kv td:nth-child(odd){background:#f6f6f6;font-weight:700;width:22%}
.tot td{font-weight:700;background:#f6f6f6}.box{display:inline-block;width:12px;height:12px;border:1px solid #333;vertical-align:middle;margin-inline-end:6px;text-align:center;line-height:10px;font-size:10pt}
.sig{display:grid;grid-template-columns:1fr 1fr;gap:28px;margin-top:22px}.sig div{border:1px solid #999;padding:10px;min-height:92px}
.sig p{margin:6px 0}.muted{color:#666;font-size:9pt}.pre{white-space:pre-line}
@media print{body{background:#fff}.bar{display:none}.page{box-shadow:none;margin:0;max-width:none;padding:0}}
"""


def _lbl(k, ar):
    return T[k][1 if ar else 0]


def _money(v, ar=False):
    s = f"{v:,.2f}"
    return s


def _shell(db, title, body, ar, kind="report_pdf", auto_print=True):
    b = branding.get(db)
    logo = branding.logo_for(db, kind)
    wm = branding.watermark_for(db, kind)
    s = lambda k: db.setting(k, "") or ""  # noqa: E731
    co = [s("company_legal_name") or s("company_name"), s("company_address"), " · ".join(x for x in (s("company_phone"), s("company_email")) if x),
          (("الرقم الضريبي: " if ar else "Tax ID: ") + s("company_tax_id")) if s("company_tax_id") else ""]
    img = '<img src="data:%s;base64,%s" alt="">' % (logo["mime"], logo["data"]) if logo else ""
    head = (f'<div class="hd"><div>{img}</div>'
            f'<div class="co" style="text-align:end"><b>{E(co[0])}</b>' + "".join(f"<div>{E(x)}</div>" for x in co[1:] if x) + "</div></div>")
    wm_html = f'<img class="wm" src="data:image/png;base64,{wm["data"]}" alt="">' if wm else ""
    return (f'<!doctype html><html lang="{"ar" if ar else "en"}" dir="{"rtl" if ar else "ltr"}"><head><meta charset="utf-8"><title>{E(title)}</title>'
            f'<style>:root{{--d:{b["dark"]};--f:\'{E(b["font"])}\'}}{CSS}</style></head><body>'
            '<div class="bar"><button onclick="print()">Print / Save as PDF</button></div>'
            f'<div class="page">{wm_html}{head}{body}</div>'
            + ('<script>addEventListener("load",()=>setTimeout(()=>print(),350))</script>' if auto_print else "") + "</body></html>")


def _kv(rows):
    rows = [r for r in rows if r[1] not in (None, "")]
    cells = []
    for i in range(0, len(rows), 2):
        pair = rows[i:i + 2]
        cells.append("<tr>" + "".join(f"<td>{E(k)}</td><td>{v}</td>" for k, v in pair) + ("<td></td><td></td>" if len(pair) == 1 else "") + "</tr>")
    return f'<table class="kv">{"".join(cells)}</table>'


def _sigs(ar, us="", them="", date=""):
    blk = lambda who, name: (f"<div><b>{E(_lbl(who, ar))}</b><p>{E(_lbl('name', ar))}: {E(name) or '.' * 40}</p>"  # noqa: E731
                             f"<p>{E(_lbl('signature', ar))}: {'.' * 40}</p><p>{E(_lbl('date', ar))}: {E(date) or '...... / ...... / ........'}</p></div>")
    return f'<div class="sig">{blk("sig_us", us)}{blk("sig_client", them)}</div>'


def _name(x, ar):
    return (x.get("name_ar") if ar and x.get("name_ar") else x.get("name")) or ""


def _pieces(ln, ar):
    return " · ".join("%s × %s" % (_name(c, ar), format(c["per_unit"] * ln["qty"], ",.0f")) for c in ln["components"])


def _lines_tables(d, ar):
    rent = [ln for ln in d["lines"] if ln["kind"] != "charge"]
    one = [ln for ln in d["lines"] if ln["kind"] == "charge"]
    out = (f'<table><tr><th>#</th><th>{_lbl("desc", ar)}</th><th>{_lbl("unit", ar)}</th><th class="n">{_lbl("qty", ar)}</th>'
           f'<th class="n">{_lbl("rate", ar)}</th><th class="n">{_lbl("monthly", ar)}</th></tr>'
           + "".join(f'<tr><td>{i + 1}</td><td>{E(ln["description"])}' + (f'<div class="muted">{E(_pieces(ln, ar))}</div>' if ln["kind"] in ("kit", "sqm") else "")
                     + f'</td><td>{E(ln["unit"])}</td><td class="n">{ln["qty"]:,.2f}</td><td class="n">{_money(ln["rate"])}</td><td class="n">{_money(ln["monthly"])}</td></tr>'
                     for i, ln in enumerate(rent))
           + f'<tr class="tot"><td colspan="5">{_lbl("rent", ar)}</td><td class="n">{_money(d["quote"]["monthly"])}</td></tr></table>')
    if one:
        out += (f'<h2>{_lbl("one", ar)}</h2><table><tr><th>#</th><th>{_lbl("desc", ar)}</th><th>{_lbl("one_when", ar)}</th><th class="n">{_lbl("qty", ar)}</th>'
                f'<th class="n">{_lbl("amount", ar)}</th><th class="n">{_lbl("total", ar)}</th></tr>'
                + "".join(f'<tr><td>{i + 1}</td><td>{E(ln["description"])}</td><td>{_lbl("w_" + (ln["charge_when"] or "manual"), ar)}</td>'
                          f'<td class="n">{ln["qty"]:,.2f}</td><td class="n">{_money(ln["rate"])}</td><td class="n">{_money(ln["qty"] * ln["rate"])}</td></tr>'
                          for i, ln in enumerate(one))
                + f'<tr class="tot"><td colspan="5">{_lbl("total", ar)}</td><td class="n">{_money(d["quote"]["one_time"])}</td></tr></table>')
    return out


def offer_html(db, rid, lang=None):
    d = rentals.detail(db, rid)
    r = d["rental"]
    ar = (lang or r["lang"]) == "ar"
    vat_rate = acc.tax_code(db, r["vat_code"], "vat")[1] if r["vat_code"] else 0
    q = d["quote"]
    body = (f'<h1>{_lbl("offer", ar)} — {E(r["code"])}</h1>'
            + _kv([(_lbl("client", ar), E(r["client"])), (_lbl("date", ar), E(r["quote_date"])), (_lbl("site", ar), E(r["site"])),
                   (_lbl("valid", ar), E(r["valid_until"])), (_lbl("location", ar), E(r["site_location"])), (_lbl("contact", ar), E(" · ".join(x for x in (r["contact"], r["phone"]) if x)))])
            + f'<h2>{_lbl("summary", ar)}</h2>' + _lines_tables(d, ar)
            + f'<table><tr class="tot"><td>{_lbl("est", ar)} {r["duration_months"]:g} {_lbl("months", ar)}</td><td class="n">{_money(q["estimate"])}</td></tr>'
            + (f'<tr><td>{_lbl("vat", ar)} {vat_rate * 100:g}%</td><td class="n">{_money(q["estimate"] * vat_rate)}</td></tr>'
               f'<tr class="tot"><td>{_lbl("grand", ar)}</td><td class="n">{_money(q["estimate"] * (1 + vat_rate))}</td></tr>' if vat_rate else "")
            + "</table>"
            + f'<p class="muted">{_lbl("rent_basis", ar)}</p>'
            + (f'<h2>{_lbl("notes", ar)}</h2><div class="pre">{E(r["offer_notes"])}</div>' if r["offer_notes"] else ""))
    return _shell(db, f"{_lbl('offer', ar)} {r['code']}", body, ar, "offer_pdf")


def contract_html(db, rid, lang=None):
    d = rentals.detail(db, rid)
    r = d["rental"]
    ar = (lang or r["lang"]) == "ar"
    st = rentals.settings(db)
    s = lambda k: db.setting(k, "") or ""  # noqa: E731
    pieces = {}
    for ln in d["lines"]:
        for c in ln["components"]:
            it = db.one("SELECT * FROM rent_items WHERE id=?", (c["item_id"],))
            pieces[c["item_id"]] = (it, to_major(rentals.loss_price(db, it)))
    body = (f'<h1>{_lbl("contract", ar)} — {E(r["code"])}</h1>'
            + _kv([(_lbl("lessor", ar), E(s("company_legal_name") or s("company_name"))), (_lbl("lessee", ar), E(r["client"])),
                   (_lbl("site", ar), E(r["site"])), (_lbl("location", ar), E(r["site_location"])),
                   (_lbl("po", ar), E(f'{r["po_number"]} — {r["po_date"]}' if r["po_number"] else "")), (_lbl("start", ar), E(r["start_date"])),
                   (_lbl("duration", ar), f'{r["duration_months"]:g} {_lbl("months", ar)}'), (_lbl("date", ar), E(r["signed_on"] or acc.today()))])
            + f'<h2>{_lbl("summary", ar)}</h2>' + _lines_tables(d, ar)
            + f'<h2>{_lbl("pieces", ar)}</h2><table><tr><th>#</th><th>{_lbl("piece", ar)}</th><th>{_lbl("unit", ar)}</th><th class="n">{_lbl("loss_price", ar)}</th></tr>'
            + "".join(f'<tr><td>{i + 1}</td><td>{E(_name(it, ar))}</td><td>{E(it["unit"])}</td><td class="n">{_money(lp)}</td></tr>'
                      for i, (it, lp) in enumerate(pieces.values())) + "</table>"
            + f'<h2>{_lbl("terms", ar)}</h2><div class="pre">{E(st["contract_terms"].get("ar" if ar else "en") or "")}</div>'
            + f'<div class="pre" style="margin-top:6px">{E(st["terms"].get("ar" if ar else "en") or "")}</div>'
            + (f'<h2>{_lbl("notes", ar)}</h2><div class="pre">{E(r["notes"])}</div>' if r["notes"] else "")
            + _sigs(ar, r["signed_us"], r["signed_client"], r["signed_on"]))
    return _shell(db, f"{_lbl('contract', ar)} {r['code']}", body, ar)


def handover_html(db, hid, lang=None):
    h = db.one("SELECT * FROM rental_handovers WHERE id=?", (int(hid),))
    if not h:
        raise acc.ValidationError("Handover not found")
    r = rentals.get(db, h["rental_id"])
    ar = (lang or r["lang"]) == "ar"
    data, checklist = rentals._j(h["data"], {}), rentals._j(h["checklist"], [])
    s = lambda k: db.setting(k, "") or ""  # noqa: E731
    items = [x for x in data.get("items") or [] if x.get("qty") or x.get("damaged") or x.get("lost")]
    # one row per piece (a piece can come from several lines)
    merged = {}
    for x in items:
        m = merged.setdefault(x["item_id"], {**x, "qty": 0, "damaged": 0, "lost": 0, "serial": []})
        m["qty"] += x.get("qty") or 0
        m["damaged"] += x.get("damaged") or 0
        m["lost"] += x.get("lost") or 0
        if x.get("serial"):
            m["serial"].append(x["serial"])
    cats = sorted({(db.one("SELECT category FROM rent_items WHERE id=?", (i,)) or {}).get("category", "other") for i in merged})
    what = " / ".join(_lbl("cat_" + c, ar) for c in cats) if cats else _lbl("cat_other", ar)
    delivery = h["kind"] == "delivery"
    title = f'{_lbl("delivery" if delivery else "return", ar)} — {what}'
    rows = list(merged.values())
    if delivery:
        table = (f'<table><tr><th>#</th><th>{_lbl("piece", ar)}</th><th>{_lbl("unit", ar)}</th><th class="n">{_lbl("delivered", ar)}</th>'
                 f'<th>{_lbl("cond", ar)}</th><th class="n">{_lbl("loss_price", ar)}</th></tr>'
                 + "".join(f'<tr><td>{i + 1}</td><td>{E(_name(x, ar))}' + (f'<div class="muted">{_lbl("serial", ar)}: {E(", ".join(x["serial"]))}</div>' if x["serial"] else "")
                           + f'</td><td>{E(x.get("unit"))}</td><td class="n">{x["qty"]:,.0f}</td><td>{_lbl("good", ar)}</td><td class="n">{_money(x.get("loss_price") or 0)}</td></tr>'
                           for i, x in enumerate(rows))
                 + f'<tr class="tot"><td colspan="3">{_lbl("grand", ar)}</td><td class="n">{sum(x["qty"] for x in rows):,.0f}</td><td colspan="2"></td></tr></table>')
    else:
        charge = sum((x["damaged"] + x["lost"]) * (x.get("loss_price") or 0) for x in rows)
        table = (f'<table><tr><th>#</th><th>{_lbl("piece", ar)}</th><th class="n">{_lbl("returned", ar)}</th><th class="n">{_lbl("damaged", ar)}</th>'
                 f'<th class="n">{_lbl("lost", ar)}</th><th class="n">{_lbl("loss_price", ar)}</th><th class="n">{_lbl("charge", ar)}</th></tr>'
                 + "".join(f'<tr><td>{i + 1}</td><td>{E(_name(x, ar))}</td><td class="n">{x["qty"]:,.0f}</td><td class="n">{x["damaged"]:,.0f}</td>'
                           f'<td class="n">{x["lost"]:,.0f}</td><td class="n">{_money(x.get("loss_price") or 0)}</td>'
                           f'<td class="n">{_money((x["damaged"] + x["lost"]) * (x.get("loss_price") or 0))}</td></tr>' for i, x in enumerate(rows))
                 + f'<tr class="tot"><td colspan="2">{_lbl("grand", ar)}</td><td class="n">{sum(x["qty"] for x in rows):,.0f}</td>'
                 f'<td class="n">{sum(x["damaged"] for x in rows):,.0f}</td><td class="n">{sum(x["lost"] for x in rows):,.0f}</td><td></td><td class="n">{_money(charge)}</td></tr></table>')
    terms = rentals.settings(db)["terms"].get("ar" if ar else "en") or ""
    checks = ""
    if delivery and checklist:
        mark = lambda on: '<span class="box">✓</span>' if on else '<span class="box"></span>'  # noqa: E731
        checks = (f'<h2>{_lbl("check", ar)}</h2><table><tr><th>#</th><th>{_lbl("item", ar)}</th><th>{_lbl("yes", ar)}</th><th>{_lbl("nok", ar)}</th><th>{_lbl("notes", ar)}</th></tr>'
                  + "".join(f'<tr><td>{i + 1}</td><td>{E((c.get("text_ar") if ar else c.get("text")) or c.get("text") or c.get("text_ar"))}</td>'
                            f'<td style="text-align:center">{mark(c.get("ok") == "yes")}</td><td style="text-align:center">{mark(c.get("ok") == "no")}</td><td>{E(c.get("note"))}</td></tr>'
                            for i, c in enumerate(checklist)) + "</table>"
                  + f'<h2>{_lbl("decision", ar)}</h2><p>{"<span class=box>✓</span>" if h["decision"] == "safe" else "<span class=box></span>"}{_lbl("safe", ar)}'
                  f'&nbsp;&nbsp;&nbsp; {"<span class=box>✓</span>" if h["decision"] == "unsafe" else "<span class=box></span>"}{_lbl("unsafe", ar)}</p>')
    body = (f'<h1>{E(title)}</h1>'
            + _kv([(_lbl("site", ar), E(r["site"])), (_lbl("location", ar), E(r["site_location"])),
                   (_lbl("lessor", ar), E(s("company_legal_name") or s("company_name"))), (_lbl("lessee", ar), E(r["client"])),
                   (_lbl("date", ar), E(h["date"])), (_lbl("no", ar), E(h["number"])), (_lbl("po", ar), E(r["po_number"])), (_lbl("contract", ar), E(r["code"]))])
            + f'<h2>{_lbl("pieces", ar)}</h2>' + table
            + (f'<h2>{_lbl("loss_terms", ar)}</h2><div class="pre">{E(terms)}</div>' if delivery else "")
            + checks + (f'<h2>{_lbl("notes", ar)}</h2><div class="pre">{E(h["notes"])}</div>' if h["notes"] else "")
            + _sigs(ar, h["our_name"], h["client_name"], h["date"] if h["signed"] else ""))
    return _shell(db, f"{title} {h['number']}", body, ar)
