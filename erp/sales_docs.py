"""Printable sales documents (print / save as PDF): quotation, sales contract with its payment schedule, customer
statement, and a product brochure with photos and specifications. English or Arabic."""
from . import sales
from .rental_docs import E, _kv, _shell

T = {
    "quote": ("Quotation", "عرض سعر"), "contract": ("Sales contract", "عقد بيع"), "statement": ("Customer statement", "كشف حساب عميل"),
    "no": ("No.", "رقم"), "date": ("Date", "التاريخ"), "customer": ("Customer", "العميل"), "phone": ("Phone", "الهاتف"),
    "project": ("Project", "المشروع"), "delivery": ("Expected delivery", "موعد التسليم المتوقع"), "desc": ("Description", "البيان"),
    "unit": ("Unit", "الوحدة"), "qty": ("Qty", "الكمية"), "price": ("Unit price", "سعر الوحدة"), "disc": ("Discount", "الخصم"),
    "amount": ("Amount", "المبلغ"), "net": ("Net", "الصافي"), "vat": ("VAT", "ضريبة القيمة المضافة"), "wht": ("Tax withheld", "خصم وتحصيل"),
    "pricet": ("Price", "الثمن"), "interest": ("Installment interest", "فوائد التقسيط"), "total": ("Total payable", "إجمالي المستحق"),
    "plan": ("Payment plan", "خطة السداد"), "due": ("Due date", "تاريخ الاستحقاق"), "payment": ("Payment", "الدفعة"),
    "status": ("Status", "الحالة"), "paid": ("Paid", "المسدد"), "left": ("Remaining", "المتبقي"), "terms": ("Terms", "الشروط"),
    "seller": ("For the seller", "عن البائع"), "buyer": ("The buyer", "المشتري"), "name": ("Name", "الاسم"), "signature": ("Signature", "التوقيع"),
    "receipts": ("Payments received", "المدفوعات"), "cheques": ("Cheques", "الشيكات"), "bank": ("Bank", "البنك"), "chq": ("Cheque no.", "رقم الشيك"),
    "balance": ("Balance", "الرصيد"), "in_cheques": ("In cheques not yet cleared", "شيكات لم تُصرف بعد"), "overdue": ("Overdue", "متأخرات"),
    "specs": ("Specifications", "المواصفات"), "from": ("From", "يبدأ من"), "example": ("Example payment plan", "مثال لخطة السداد"),
    "down": ("Down payment", "مقدم"), "inst": ("Installment", "قسط"), "extra": ("Extra payment", "دفعة إضافية"), "bal": ("Balance", "دفعة"),
    "full": ("Full payment", "دفعة كاملة"), "months": ("installments", "قسط"),
}
ST = {"paid": ("Paid", "مسدد"), "cheque": ("By cheque", "بشيك"), "partial": ("Part paid", "جزئي"), "overdue": ("Overdue", "متأخر"),
      "due": ("Due", "مستحق"), "cancelled": ("Cancelled", "ملغي")}
CH = {"pending": ("Pending", "تحت التحصيل"), "cleared": ("Cleared", "تم الصرف"), "bounced": ("Bounced", "مرتد")}


def L(k, ar):
    return T[k][1 if ar else 0]


def M(v):
    return f"{v:,.2f}"


def _label(r, ar):
    if not ar:
        return r["label"] or r["kind"]
    k = {"down": "down", "extra": "extra", "balance": "bal", "installment": "inst"}.get(r["kind"], "inst")
    if r["kind"] == "installment" and "/" in (r["label"] or ""):
        return f"{L('inst', True)} {r['label'].split()[-1]}"
    if r["kind"] == "down" and r["label"] == "Full payment":
        return L("full", True)
    return r["label"] if r["kind"] == "extra" and r["label"] != "Extra payment" else L(k, True)


def _lines(d, ar):
    rows = "".join(f"<tr><td>{E(ln['description'])}</td><td>{E(ln['unit'])}</td><td class='n'>{ln['qty']:g}</td><td class='n'>{M(ln['price'])}</td>"
                   f"<td class='n'>{M(ln['discount']) if ln['discount'] else ''}</td><td class='n'>{M(ln['amount'])}</td></tr>" for ln in d["lines"])
    tot = [(L("net", ar), d["net"])] + ([(L("vat", ar), d["vat"])] if d["vat"] else []) + ([(L("wht", ar), -d["wht"])] if d["wht"] else []) + \
          ([(L("pricet", ar), d["price"])] if d["vat"] or d["wht"] else []) + ([(L("interest", ar), d["markup"])] if d["markup"] else []) + \
          [(L("total", ar), d["total"])]
    rows += "".join(f"<tr class='tot'><td colspan='5'>{E(k)}</td><td class='n'>{M(v)}</td></tr>" for k, v in tot)
    return (f"<table><thead><tr><th>{L('desc', ar)}</th><th>{L('unit', ar)}</th><th class='n'>{L('qty', ar)}</th><th class='n'>{L('price', ar)}</th>"
            f"<th class='n'>{L('disc', ar)}</th><th class='n'>{L('amount', ar)}</th></tr></thead><tbody>{rows}</tbody></table>")


def _schedule(d, ar, status=False):
    head = f"<th>#</th><th>{L('due', ar)}</th><th>{L('payment', ar)}</th><th class='n'>{L('amount', ar)}</th>"
    if status:
        head += f"<th class='n'>{L('paid', ar)}</th><th class='n'>{L('left', ar)}</th><th>{L('status', ar)}</th>"
    rows = ""
    for i, r in enumerate(d["schedule"], start=1):
        rows += f"<tr><td>{i}</td><td>{E(r['due'])}</td><td>{E(_label(r, ar))}</td><td class='n'>{M(r['amount'])}</td>"
        if status:
            rows += (f"<td class='n'>{M(r['paid'] + r['by_cheque'])}</td><td class='n'>{M(r['left'])}</td>"
                     f"<td>{E(ST.get(r['status'], (r['status'],) * 2)[1 if ar else 0])}</td>")
        rows += "</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def _specs(db, d, ar):
    out = ""
    for ln in d["lines"]:
        if not ln.get("product_id"):
            continue
        p = sales.get_product(db, ln["product_id"])
        sp = sales._j(p["specs"], [])
        if sp:
            out += f"<p><b>{E(ln['description'])}</b> — " + " · ".join(f"{E(a)}: {E(b)}" for a, b in sp) + "</p>"
    return f"<h2>{L('specs', ar)}</h2>{out}" if out else ""


def sale_html(db, sid, kind="contract", lang=None):
    ar = (lang or db.setting("doc_lang") or "en") == "ar"
    d = sales.detail(db, sid)
    if kind not in ("quote", "contract", "statement"):
        kind = "contract"
    title = L(kind, ar)
    bu = db.one("SELECT name FROM business_units WHERE id=?", (d["bu_id"],)) if d["bu_id"] else None
    body = f"<h1>{E(title)}</h1>" + _kv([(L("no", ar), E(d["code"])), (L("date", ar), E(d["date"])), (L("customer", ar), E(d["customer"])),
                                          (L("phone", ar), E((d["contact"] or {}).get("phone") or "")), (L("project", ar), E(bu["name"] if bu else "")),
                                          (L("delivery", ar), E(d["delivery_due"] or ""))])
    body += _lines(d, ar) + _specs(db, d, ar)
    if kind == "statement":
        body += f"<h2>{L('plan', ar)}</h2>" + _schedule(d, ar, status=True)
        rec = [e for e in d["entries"] if e["type"] in ("receipt", "clear", "refund")]
        if rec:
            body += (f"<h2>{L('receipts', ar)}</h2><table><thead><tr><th>{L('date', ar)}</th><th>{L('desc', ar)}</th><th class='n'>{L('amount', ar)}</th></tr></thead><tbody>"
                     + "".join(f"<tr><td>{E(e['date'])}</td><td>{E(e['memo'])}</td><td class='n'>{M(-e['amount'] if e['type'] == 'refund' else e['amount'])}</td></tr>" for e in rec)
                     + "</tbody></table>")
        body += _kv([(L("total", ar), M(d["total"])), (L("paid", ar), M(d["paid"])), (L("in_cheques", ar), M(d["in_cheques"])),
                     (L("balance", ar), f"<b>{M(d['balance'])}</b>"), (L("overdue", ar), M(d["overdue"]) if d["overdue"] else "")])
    else:
        body += f"<h2>{L('plan', ar)}</h2>" + _schedule(d, ar)
    if d["cheques"]:
        body += (f"<h2>{L('cheques', ar)}</h2><table><thead><tr><th>{L('chq', ar)}</th><th>{L('bank', ar)}</th><th>{L('due', ar)}</th>"
                 f"<th class='n'>{L('amount', ar)}</th><th>{L('status', ar)}</th></tr></thead><tbody>"
                 + "".join(f"<tr><td>{E(c['number'])}</td><td>{E(c['bank'])}</td><td>{E(c['due'])}</td><td class='n'>{M(c['amount'])}</td>"
                           f"<td>{E(CH.get(c['status'], (c['status'],) * 2)[1 if ar else 0])}</td></tr>" for c in d["cheques"]) + "</tbody></table>")
    if kind == "contract":
        st = sales.settings(db)
        terms = (st["terms_ar"] if ar else st["terms"]) or ""
        if d["notes"]:
            terms += "\n" + d["notes"]
        if terms.strip():
            body += f"<h2>{L('terms', ar)}</h2><div class='pre'>{E(terms)}</div>"
        blk = lambda who, nm: (f"<div><b>{E(L(who, ar))}</b><p>{E(L('name', ar))}: {E(nm) or '.' * 40}</p>"  # noqa: E731
                               f"<p>{E(L('signature', ar))}: {'.' * 40}</p><p>{E(L('date', ar))}: ...... / ...... / ........</p></div>")
        body += f'<div class="sig">{blk("seller", "")}{blk("buyer", d["customer"])}</div>'
    return _shell(db, f"{title} {d['code']}", body, ar, auto_print=False)


def product_html(db, pid, lang=None):
    """A one-page brochure: photos, price, specifications, description and an example payment plan."""
    ar = (lang or db.setting("doc_lang") or "en") == "ar"
    p = sales.product_row(db, sales.get_product(db, pid), media=True)
    name = p["name_ar"] if ar and p["name_ar"] else p["name"]
    photos = [m for m in p["media"] if m["kind"] == "photo" and m["uploaded"]]
    photos.sort(key=lambda m: m["id"] != p["cover"])
    imgs = ""
    for i, m in enumerate(photos[:5]):
        f = sales.media_file(db, m["id"])
        style = "width:100%;max-height:105mm;object-fit:cover" if i == 0 else "width:24%;height:32mm;object-fit:cover;margin:1% .5% 0"
        imgs += f'<img src="{f["data"]}" style="{style}" alt="">'
    body = f"<h1>{E(name)}</h1>{imgs}"
    body += _kv([("Code" if not ar else "الكود", E(p["code"])), ("Price" if not ar else "السعر", f"<b>{M(p['price'])}</b> {E(db.setting('currency') or '')}"),
                 ("Category" if not ar else "التصنيف", E(p["category"])), (L("project", ar), E(p["bu_name"]))])
    if p["specs"]:
        body += f"<h2>{L('specs', ar)}</h2>" + _kv([(a, E(b)) for a, b in p["specs"]])
    if p["description"]:
        body += f"<div class='pre' style='margin-top:8px'>{E(p['description'])}</div>"
    st = sales.settings(db)
    if p["price"] > 0 and st["n"] and st["n"] > 1:
        rows, mk = sales.build_schedule(sales.to_minor(p["price"]), {"type": "installments", "down_pct": st["down_pct"], "n": st["n"],
                                                                     "frequency": st["frequency"], "rate": st["rate"], "round_to": st["round_to"]},
                                        sales._today())
        down = sum(r["amount"] for r in rows if r["kind"] == "down")
        inst = next((r["amount"] for r in rows if r["kind"] == "installment"), 0)
        body += (f"<h2>{L('example', ar)}</h2>" + _kv([(L("down", ar), M(sales.to_major(down))),
                                                        (f"{st['n']} {L('months', ar)}", M(sales.to_major(inst))),
                                                        (L("total", ar), M(sales.to_major(p and sales.to_minor(p['price']) + mk)))]))
    return _shell(db, name, body, ar, auto_print=False)
