"""Extracts (مستخلصات): progress billings in the client's layout, and their follow-up.

An extract is a sales invoice (the extracts table) with a few more fields: the project and work titles, the extract
number on its site, what the client paid before it (worked out from the books, or typed), deductions, running or
final. Follow-up matches the site's collections to its issued extracts, oldest first: each extract shows what is
collected and what is still owed. For a rental contract the next extract is filled in from the contract (the next
rent period at the contract's rates, plus one-time charges not billed yet) in the client's wording."""
import datetime as _dt
import io

from . import accounting as acc
from .accounting import ValidationError, text, to_major, to_minor
from .rentals import EPS


def _ar_date(d):
    if not d:
        return ""
    y, m, dd = str(d)[:10].split("-")
    return f"{int(dd)}/{int(m)}/{y}"


def _collections(db, bu_id, upto=None, before=None):
    """What the site's client paid: credits to receivables on the site's entries (receipt vouchers against invoices)."""
    ar = db.acct("ar")
    w, p = ["e.status='posted'", "e.bu_id=?", "l.account=?"], [bu_id, ar]
    if upto:
        w.append("e.date<=?")
        p.append(upto)
    if before:
        w.append("e.date<?")
        p.append(before)
    r = db.one(f"SELECT COALESCE(SUM(l.credit),0) AS c FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
               f"WHERE {' AND '.join(w)} AND e.kind<>'Sales Invoice'", p)
    return to_major(r["c"])


def prev_paid(db, x):
    """'Less paid before' on the extract: the amount typed, else what the site's client paid before its date."""
    if x.get("prev_paid") is not None:
        return to_major(x["prev_paid"]), False
    return _collections(db, x["bu_id"], before=x["date"]) if x.get("date") else 0.0, True


def _earlier(db, x):
    """The site's issued extracts before this one (by date, then number)."""
    return db.q("SELECT id FROM extracts WHERE bu_id=? AND id<>? AND status='posted' AND (date<? OR (date=? AND id<?)) "
                "ORDER BY date, id", (x["bu_id"], x["id"], x["date"] or "", x["date"] or "", x["id"]))


def detail(db, xid):
    x = acc.get_extract(db, xid)
    if not x:
        raise ValidationError("Extract not found")
    x["prev"], x["prev_auto"] = prev_paid(db, x)
    x["deductions"] = to_major(x.get("deductions") or 0)
    # a running extract (جاري) lists every work to date: the earlier issued extracts of the site, then this one
    x["earlier"] = []
    for e in _earlier(db, x):
        ex = acc.get_extract(db, e["id"])
        x["earlier"].append({"id": ex["id"], "number": ex["number"], "seq_no": ex["seq_no"], "lines": ex["lines"],
                             "total": ex["total"], "vat": ex["vat"], "wht": ex["wht"], "receivable": ex["receivable"]})
    cum = lambda k: round(sum(e[k] for e in x["earlier"]) + x[k], 2)  # noqa: E731
    x["works_to_date"], x["vat_to_date"], x["wht_to_date"], x["receivable_to_date"] = cum("total"), cum("vat"), cum("wht"), cum("receivable")
    x["due"] = round(x["receivable_to_date"] - x["prev"] - x["deductions"], 2)
    link = db.one("SELECT ri.rental_id, ri.period_from, ri.period_to, r.code AS rental_code FROM rental_invoices ri "
                  "JOIN rentals r ON r.id=ri.rental_id WHERE ri.extract_id=? ORDER BY ri.id LIMIT 1", (xid,))
    x["rental"] = link
    x["others"] = [{"id": o["id"], "number": o["number"], "seq_no": o["seq_no"], "date": o["date"], "status": o["status"]}
                   for o in db.q("SELECT id, number, seq_no, date, status FROM extracts WHERE bu_id=? AND id<>? ORDER BY date, id",
                                 (x["bu_id"], xid))]
    st = {r["id"]: r for r in board(db, bu_id=x["bu_id"])["rows"]}.get(xid, {})
    x["collected"], x["owed"], x["pay_state"] = st.get("collected", 0), st.get("owed", 0), st.get("pay_state", "draft")
    return x


def board(db, bu_id=None, status=None, q=None):
    """Every extract with its follow-up, and every site with what was billed, collected and is still owed."""
    rows = db.q("SELECT x.*, u.code AS bu_code, u.name AS bu_name, u.customer AS bu_customer, "
                "(SELECT COALESCE(SUM(ROUND(qty*price*COALESCE(pct,1))),0) FROM extract_lines WHERE extract_id=x.id) AS works, "
                "(SELECT r.code FROM rental_invoices ri JOIN rentals r ON r.id=ri.rental_id WHERE ri.extract_id=x.id LIMIT 1) AS rental_code, "
                "(SELECT ri.rental_id FROM rental_invoices ri WHERE ri.extract_id=x.id LIMIT 1) AS rental_id, "
                "(SELECT MIN(ri.period_from) FROM rental_invoices ri WHERE ri.extract_id=x.id) AS period_from, "
                "(SELECT MAX(ri.period_to) FROM rental_invoices ri WHERE ri.extract_id=x.id) AS period_to "
                "FROM extracts x JOIN business_units u ON u.id=x.bu_id ORDER BY x.date, x.id")
    sites, out = {}, []
    for r in rows:
        x = {**r, "works": to_major(r["works"])}
        x.update(acc.invoice_taxes(db, {"total": x["works"], "vat_code": r["vat_code"], "wht_code": r["wht_code"], "customer": r["customer"]}))
        x["prev"], x["prev_auto"] = prev_paid(db, x)
        x["deductions"] = to_major(r["deductions"] or 0)
        x["receivable_to_date"] = round(sum(y["receivable"] for y in out if y["bu_id"] == r["bu_id"] and y["status"] == "posted")
                                        + x["receivable"], 2)
        x["due"] = round(x["receivable_to_date"] - x["prev"] - x["deductions"], 2)
        s = sites.setdefault(r["bu_id"], {"bu_id": r["bu_id"], "bu_code": r["bu_code"], "bu_name": r["bu_name"],
                                          "client": r["customer"] or r["bu_customer"] or "", "billed": 0.0, "drafts": 0,
                                          "extracts": 0, "last": None, "rental_id": None, "rental_code": None})
        s["extracts"] += 1
        s["last"] = r["date"]
        if r["rental_id"]:
            s["rental_id"], s["rental_code"] = r["rental_id"], r["rental_code"]
        if r["status"] == "posted":
            s["billed"] = round(s["billed"] + x["receivable"], 2)
        else:
            s["drafts"] += 1
        out.append(x)
    # collections matched to the issued extracts of each site, oldest first
    for bid, s in sites.items():
        left = s["collected"] = _collections(db, bid)
        for x in (y for y in out if y["bu_id"] == bid):
            if x["status"] != "posted":
                x["collected"], x["owed"], x["pay_state"] = 0.0, 0.0, "draft"
                continue
            take = min(left, x["receivable"])
            left = round(left - take, 2)
            x["collected"], x["owed"] = round(take, 2), round(x["receivable"] - take, 2)
            x["pay_state"] = "paid" if x["owed"] <= EPS else "part" if take > EPS else "unpaid"
        s["owed"] = round(s["billed"] - min(s["collected"], s["billed"]), 2)
        s["advance"] = round(max(0, s["collected"] - s["billed"]), 2)
    # rental contracts with nothing billed yet still show as sites to bill
    from . import rentals
    for r in rentals.list_rentals(db)["rows"]:
        if r["status"] not in ("active", "returned") or any(s["rental_id"] == r["id"] for s in sites.values()):
            continue
        bu = db.one("SELECT u.id, u.code, u.name FROM rentals x JOIN business_units u ON u.id=x.bu_id WHERE x.id=?", (r["id"],))
        key = bu["id"] if bu else f"r{r['id']}"
        if key in sites:
            sites[key]["rental_id"], sites[key]["rental_code"] = r["id"], r["code"]
            continue
        sites[key] = {"bu_id": bu["id"] if bu else None, "bu_code": bu["code"] if bu else "", "bu_name": bu["name"] if bu else r["site"],
                      "client": r["client"], "billed": 0.0, "collected": 0.0, "owed": 0.0, "advance": 0.0, "drafts": 0,
                      "extracts": 0, "last": None, "rental_id": r["id"], "rental_code": r["code"]}
    for s in sites.values():
        if s.get("rental_id"):
            s["next"] = rentals.next_period(db, s["rental_id"])
    if bu_id:
        out = [x for x in out if x["bu_id"] == int(bu_id)]
    if status == "draft":
        out = [x for x in out if x["status"] != "posted"]
    elif status in ("paid", "part", "unpaid"):
        out = [x for x in out if x["pay_state"] == status]
    if q:
        ql = str(q).lower()
        out = [x for x in out if ql in " ".join(str(x.get(k) or "") for k in ("number", "bu_code", "bu_name", "customer", "work", "project")).lower()]
    out.sort(key=lambda x: (x["date"] or "", x["id"]), reverse=True)
    t = lambda k: round(sum(s.get(k, 0) for s in sites.values()), 2)  # noqa: E731
    return {"rows": out, "sites": sorted(sites.values(), key=lambda s: s["last"] or "", reverse=True),
            "totals": {"billed": t("billed"), "collected": round(sum(min(s.get("collected", 0), s["billed"]) for s in sites.values()), 2),
                       "owed": t("owed"), "drafts": sum(1 for x in out if x["status"] != "posted")}}


def next_for_rental(db, rid, d=None, user=""):
    """A draft extract for a rental contract: the next rent period at the contract's rates plus the one-time
    charges not billed yet, in the client's wording ('ايجار بالشباك شامل مستلزماته مدة … الي …')."""
    from . import rentals
    d = dict(d or {})
    nxt = rentals.next_period(db, rid)
    if not nxt and not (d.get("from") and d.get("to")):
        raise ValidationError("Nothing delivered on this contract yet")
    d.setdefault("from", nxt["from"])
    d.setdefault("to", rentals.anniversary_to(d["from"]))
    d["lang"] = d.get("lang") or "ar"
    d.setdefault("date", acc.today())
    if not d.get("number"):     # follow the site's last number: EXT-B3-1 → EXT-B3-2
        import re
        r0 = rentals.get(db, rid)
        last = db.one("SELECT x.number FROM extracts x JOIN rental_invoices ri ON ri.extract_id=x.id WHERE ri.rental_id=? "
                      "ORDER BY x.date DESC, x.id DESC LIMIT 1", (rid,)) or \
            (db.one("SELECT number FROM extracts WHERE bu_id=? ORDER BY date DESC, id DESC LIMIT 1", (r0["bu_id"],)) if r0.get("bu_id") else None)
        m = re.match(r"^(.*?)(\d+)$", last["number"]) if last else None
        if m:
            k = int(m.group(2)) + 1
            while db.one("SELECT 1 FROM extracts WHERE number=?", (f"{m.group(1)}{k}",)):
                k += 1
            d["number"] = f"{m.group(1)}{k}"
    xid = rentals.create_invoice(db, rid, {**d, "style": "extract"}, user=user)
    r = rentals.get(db, rid)
    x = db.one("SELECT bu_id FROM extracts WHERE id=?", (xid,))
    prev = db.one("SELECT project, work FROM extracts WHERE bu_id=? AND id<>? AND (project<>'' OR work<>'') ORDER BY date DESC, id DESC LIMIT 1",
                  (x["bu_id"], xid))
    db.x("UPDATE extracts SET project=?, work=? WHERE id=?",
         (d.get("project") or (prev["project"] if prev else "") or r.get("site_location") or "",
          d.get("work") or (prev["work"] if prev else "") or r.get("site") or "", xid))
    return xid


# ------------------------------------------------------------------ the document, as the client receives it
LABELS = {
    "ar": {"title": "مستخلـــــــص  ({k})  رقم ( {n} )", "running": "جارى", "final": "ختامى", "contractor": "أسم المقاول  :  {c}",
           "heads": ["بند", "بيان الاعمال المنفذة", "الوحدة", "النسبة", "الكمية", "الفئة", "الإجمالي"], "notes": "الملاحظات",
           "total": "اجمالى قيمة الاعمال", "vat": "ضريبة القيمة المضافة", "wht": "خصم من المنبع", "prev": "خصم ماسبق صرفه",
           "ded": "خصومات", "due": "المستحق", "issued": "تحريرا فى :  {d}", "sign": "التوقيع"},
    "en": {"title": "{k} EXTRACT No. ( {n} )", "running": "INTERIM", "final": "FINAL", "contractor": "Contractor:  {c}",
           "heads": ["Item", "Description of works done", "Unit", "%", "Qty", "Rate", "Total"], "notes": "Notes",
           "total": "Total value of works", "vat": "VAT", "wht": "Withholding tax", "prev": "Less paid before",
           "ded": "Deductions", "due": "AMOUNT DUE", "issued": "Issued on:  {d}", "sign": "Signature"},
}


def _doc(db, xid, lang):
    x = detail(db, xid)
    L = LABELS["ar" if lang == "ar" else "en"]
    co = db.setting("extract_contractor") or db.setting("company_name") or ""
    lines = [ln for e in x["earlier"] for ln in e["lines"]] + x["lines"]
    rows = [(i + 1, ln["description"], ln["unit"], ln.get("pct", 1) if ln.get("pct") is not None else 1, ln["qty"], ln["price"], ln["amount"])
            for i, ln in enumerate(lines)]
    sums = [(L["total"], x["works_to_date"])]
    if x["vat_to_date"]:
        sums.append((L["vat"], x["vat_to_date"]))
    if x["wht_to_date"]:
        sums.append((L["wht"], -x["wht_to_date"]))
    sums += [(L["prev"], x["prev"]), (L["ded"], x["deductions"] or None)]
    return x, L, co, rows, sums


def xlsx(db, xid, lang="ar"):
    """The extract as an Excel sheet laid out like the client's (boxes, items, totals, previous payments, due)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    x, L, co, rows, sums = _doc(db, xid, lang)
    ar = lang == "ar"
    wb = Workbook()
    ws = wb.active
    ws.title = (f"مستخلص {x['seq_no'] or ''}" if ar else f"Extract {x['seq_no'] or ''}").strip()[:31]
    ws.sheet_view.rightToLeft = ar
    ws.sheet_view.showGridLines = False
    med, thin = Side(style="medium", color="000000"), Side(style="thin", color="000000")
    box, cell_b = Border(left=med, right=med, top=med, bottom=med), Border(left=thin, right=thin, top=thin, bottom=thin)
    AF = lambda size=11, bold=True: Font(name="Arial", size=size, bold=bold)  # noqa: E731
    ctr = Alignment(horizontal="center", vertical="center", wrap_text=True)
    side = Alignment(horizontal="right" if ar else "left", vertical="center", wrap_text=True)
    for c, w in zip("ABCDEFG", (5, 46, 6, 9, 10, 11, 13)):
        ws.column_dimensions[c].width = w
    ws.merge_cells("A2:D3")
    ws["A2"] = L["title"].format(k=L["final"] if x["final"] else L["running"], n=x["seq_no"] or "")
    ws.merge_cells("E2:G3")
    ws["E2"] = x.get("project") or ""
    ws.merge_cells("A4:C4")
    ws["A4"] = L["contractor"].format(c=co)
    ws.merge_cells("D4:G4")
    ws["D4"] = x.get("work") or x.get("bu_name") or ""
    for a, f, al in (("A2", AF(14), ctr), ("E2", AF(14), ctr), ("A4", AF(10), side), ("D4", AF(12), ctr)):
        ws[a].font, ws[a].alignment = f, al
    for rng in ("A2:D3", "E2:G3", "A4:C4", "D4:G4"):
        for row in ws[rng]:
            for c in row:
                c.border = box
    for i, h in enumerate(L["heads"], start=1):
        c = ws.cell(6, i, h)
        c.font, c.alignment, c.border = AF(10 if i != 2 else 13), ctr, cell_b
    first, n = 7, max(18, len(rows) + 2)
    for k in range(n):
        r = first + k
        if k < len(rows):
            for i, v in enumerate(rows[k][:6], start=1):
                ws.cell(r, i, v)
        ws.cell(r, 7, f"=ROUND(N(D{r})*N(E{r})*N(F{r}),2)")
        for i in range(1, 8):
            c = ws.cell(r, i)
            c.border, c.font, c.alignment = cell_b, AF(11 if i == 2 else 10, bold=i == 2), side if i == 2 else ctr
        ws.cell(r, 4).number_format, ws.cell(r, 5).number_format = "0.0%", "#,##0.0"
        ws.cell(r, 6).number_format = ws.cell(r, 7).number_format = "#,##0.00"
    last = first + n - 1
    s0 = last + 2
    ws.merge_cells(f"A{s0}:D{s0 + len(sums) - 1}")
    ws[f"A{s0}"] = L["notes"]
    ws[f"A{s0}"].font, ws[f"A{s0}"].alignment = AF(12), Alignment(horizontal="center", vertical="top")
    for i, (lab, v) in enumerate(sums):
        r = s0 + i
        ws.merge_cells(f"E{r}:F{r}")
        ws[f"E{r}"] = lab
        ws[f"E{r}"].font, ws[f"E{r}"].alignment = AF(9), side
        ws[f"G{r}"] = f"=SUM(G{first}:G{last})" if i == 0 else v
        ws[f"G{r}"].number_format, ws[f"G{r}"].font, ws[f"G{r}"].alignment = "#,##0.00", AF(10), ctr
    due = s0 + len(sums)
    ws.merge_cells(f"A{due}:F{due}")
    ws[f"A{due}"] = L["due"]
    ws[f"A{due}"].font, ws[f"A{due}"].alignment = AF(12), ctr
    n_vat, n_wht = (1 if x["vat_to_date"] else 0), (1 if x["wht_to_date"] else 0)
    plus = "+".join([f"G{s0}"] + [f"N(G{s0 + 1 + j})" for j in range(n_vat + n_wht)])
    ws[f"G{due}"] = f"={plus}-N(G{due - 2})-N(G{due - 1})"
    ws[f"G{due}"].font, ws[f"G{due}"].number_format, ws[f"G{due}"].alignment = AF(11), "#,##0.00", ctr
    for row in ws[f"A{s0}:G{due}"]:
        for c in row:
            c.border = cell_b
    for k, v in enumerate((L["issued"].format(d=_ar_date(x["date"])), x.get("notes") or "", L["sign"])):
        ws[f"B{due + 2 + k}"] = v
        ws[f"B{due + 2 + k}"].font, ws[f"B{due + 2 + k}"].alignment = AF(9), side
    ws.page_setup.orientation, ws.page_setup.paperSize = "portrait", ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1
    ws.print_area = f"A1:G{due + 5}"
    buf = io.BytesIO()
    wb.save(buf)
    name = f"{'مستخلص' if ar else 'Extract'}_{x.get('work') or x['bu_code']}_{x['seq_no'] or x['number']}"
    return buf.getvalue(), name.replace("/", "-").replace(" ", "_")[:80] + ".xlsx"


def html(db, xid, lang="ar"):
    """The same layout as a page that prints to PDF (A4, auto print)."""
    from html import escape as E
    x, L, co, rows, sums = _doc(db, xid, lang)
    ar = lang == "ar"
    fmt = lambda v: "" if v in (None, "") else f"{v:,.2f}"  # noqa: E731
    body = "".join(f"<tr><td>{i}</td><td class='d'>{E(str(dsc))}</td><td>{E(str(u or ''))}</td><td>{p * 100:.1f}%</td>"
                   f"<td>{q:,.1f}</td><td>{fmt(pr)}</td><td>{fmt(a)}</td></tr>" for i, dsc, u, p, q, pr, a in rows)
    body += "<tr><td>&nbsp;</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>" * max(0, 10 - len(rows))
    tot = "".join(f"<tr><td class='lab'>{E(lab)}</td><td>{fmt(v)}</td></tr>" for lab, v in sums)
    title = L["title"].format(k=L["final"] if x["final"] else L["running"], n=x["seq_no"] or "")
    page = f"""<!doctype html><html lang="{'ar' if ar else 'en'}" dir="{'rtl' if ar else 'ltr'}"><head><meta charset="utf-8">
<title>{E(title)}</title><style>
@page{{size:A4;margin:14mm}} body{{font-family:Arial,Tahoma,sans-serif;color:#000;margin:0}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #000;padding:5px 6px;text-align:center;font-size:12px}}
.top td{{border:2px solid #000;font-weight:700;font-size:16px;padding:14px}} .sub td{{border:2px solid #000;font-weight:700;padding:8px}}
th{{font-size:12px;height:34px}} td.d{{text-align:{'right' if ar else 'left'};font-weight:700;font-size:13px}}
.sum{{margin-top:14px}} .sum td.lab{{text-align:{'right' if ar else 'left'};font-size:11px}} .due td{{font-weight:700;font-size:14px}}
.foot{{margin-top:14px;font-size:12px;font-weight:700;line-height:2}}
</style></head><body onload="setTimeout(()=>print(),300)">
<table class="top"><tr><td style="width:58%">{E(title)}</td><td>{E(x.get('project') or '')}</td></tr></table>
<table class="sub"><tr><td style="width:45%;text-align:{'right' if ar else 'left'}">{E(L['contractor'].format(c=co))}</td><td>{E(x.get('work') or x.get('bu_name') or '')}</td></tr></table>
<br><table><tr>{''.join(f'<th>{E(h)}</th>' for h in L['heads'])}</tr>{body}</table>
<table class="sum"><tr><td rowspan="{len(sums) + 1}" style="width:55%;vertical-align:top;font-weight:700">{E(L['notes'])}</td>
{tot[4:]}<tr class="due"><td class='lab'>{E(L['due'])}</td><td>{fmt(x['due'])}</td></tr></table>
<div class="foot">{E(L['issued'].format(d=_ar_date(x['date'])))}<br>{E(x.get('notes') or '')}<br>{E(L['sign'])}</div></body></html>"""
    return page.encode("utf-8")
