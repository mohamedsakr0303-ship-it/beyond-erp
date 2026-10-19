"""Reading a BOQ or an invoice from a spreadsheet the way people actually lay them out.

Nothing depends on fixed cells, colours, logos or sheet names beyond their first word, so a sheet can be restyled,
renamed ("Invoice 1 Site A", "فاتورة 3 - الدور الثاني") or extended with extra rows and columns and still import:

* which sheets: a name starting with Invoice / فاتورة / مستخلص is an invoice; BOQ / مقايسة a bill of quantities
  (registers and print sheets are not);
* columns are found by their headings, in Arabic or English, in any order — item no., description, unit, contract
  quantity, executed quantity, completion %, price, contract total, actual total, cost, notes;
* the heading row can repeat under every section (as in the usual Egyptian layout) or appear once;
* a section is a title row such as "1- أعمال الهدم والبناء"; total rows ("إجمالي …", "Total …") are skipped;
* the details at the top (date, project / client, location, revision / invoice no.) are found by their labels,
  wherever the value sits in the same row;
* "paid by the owner" rows above the work (ما تم سداده … / إجمالي المدفوع) and a fee % row per section
  (نسبة الأتعاب / Fee %) are read too.
"""
import datetime
import re

INVOICE_PREFIX = ("invoice", "inv ", "inv.", "فاتوره", "فاتورة", "مستخلص")
BOQ_PREFIX = ("boq", "مقايسه", "مقايسة", "مقايسات")
NOT_DOCS = ("invoice register", "invoice lines", "invoice print", "boq lines", "boq register")


def _n(s):
    """A heading or label, folded for matching (Arabic letter forms, spaces, line breaks, case)."""
    s = str(s or "").translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "ـ": None, "\n": " "}))
    return " ".join(s.lower().split())


def kind_of(title):
    t = _n(title)
    if t in NOT_DOCS:
        return None
    if t.startswith(tuple(_n(p) for p in INVOICE_PREFIX)):
        return "invoice"
    if t.startswith(tuple(_n(p) for p in BOQ_PREFIX)):
        return "boq"
    return None


def _num(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("٫", ".").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    pct = s.endswith("%")
    try:
        x = float(s.rstrip("%").strip())
    except ValueError:
        return None
    return x / 100 if pct else x


def _txt(v):
    return "" if v is None else (" ".join(str(v).split()) if not isinstance(v, (int, float)) else str(v))


# heading → field; checked in this order (the more specific first)
_HEADS = [
    ("actual", lambda h: ("فعلي" in h and ("اجمالي" in h or "قيمه" in h)) or h in ("actual total", "actual amount", "actual value")),
    ("total", lambda h: h.startswith("اجمالي") or h.startswith("الاجمالي") or h in ("total", "amount", "contract total", "value", "القيمه")),
    ("executed", lambda h: "منفذ" in h or "executed" in h or h in ("done", "actual qty", "actual quantity", "الكميه الفعليه")),
    ("pct", lambda h: "نسبه" in h or "%" in h or "completion" in h or "progress" in h),
    ("qty", lambda h: "كميه" in h or "الكميات" in h or h in ("qty", "quantity", "contract qty", "contract quantity", "boq qty")),
    ("price", lambda h: "سعر" in h or "فئه" in h or h in ("rate", "price", "unit price", "unit rate")),
    ("cost", lambda h: "تكلفه" in h or h in ("cost", "unit cost")),
    ("unit", lambda h: h in ("الوحده", "وحده", "unit", "uom", "u.o.m")),
    ("desc", lambda h: h in ("البيان", "بيان", "الوصف", "وصف الاعمال", "بيان الاعمال", "description", "item description", "work")),
    ("item_no", lambda h: h in ("م", "رقم", "البند", "رقم البند", "no", "no.", "item", "item no", "item no.", "#", "s/n", "sn")),
    ("notes", lambda h: "ملاحظ" in h or h in ("notes", "remarks", "note")),
]


def _header_map(row):
    out = {}
    for i, v in enumerate(row):
        h = _n(v)
        if not h:
            continue
        for field, test in _HEADS:
            if field not in out and test(h):
                out[field] = i
                break
    return out if "desc" in out and len({"qty", "price", "total", "executed", "unit"} & set(out)) >= 2 else None


_TOTAL = re.compile(r"^(ال)?ا?جمالي|^total|^sub ?total|^المجموع", re.I)
_SECTION = re.compile(r"^\s*[-–]?\s*(?:\(?\d+(?:\.\d+)*\)?\s*[-–.)]\s*)(.+)$")
_ITEM = re.compile(r"^[A-Za-z]?\d+([.\-/]\d+)*[A-Za-z]?$")
_FEE = re.compile(r"(نسبه الاتعاب|الاتعاب|نسبه الاشراف|fee|management fee|overhead)", re.I)
_PAID = re.compile(r"(سداد|مدفوع|paid|received|دفعه)", re.I)

_META = {"date": ("التاريخ", "تاريخ", "date"),
         "client": ("المشروع", "العميل", "المالك", "project", "client", "customer", "owner"),
         "location": ("الموقع", "location", "site"),
         "number": ("الاصدار", "رقم الفاتوره", "رقم المستخلص", "المستخلص", "revision", "invoice no", "invoice no.", "invoice number")}


def _date(v):
    if isinstance(v, datetime.datetime):
        return v.date().isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    s = _txt(v)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _is_label(v, labels):
    h = _n(v).rstrip(":").strip()
    return h in labels or any(h.startswith(w + " /") or h.startswith(w + " -") or h.startswith(w + ":") or h.endswith("/ " + w)
                              for w in labels)


def _meta(rows, upto):
    """Labels at the top of the sheet → their values (the nearest filled cell in the same row that is not a label)."""
    labels = {w: k for k, ws in _META.items() for w in ws}
    out = {}
    for row in rows[:upto]:
        cells = [(i, v) for i, v in enumerate(row) if _txt(v)]
        for i, v in cells:
            h = _n(v).rstrip(":").strip()
            key = labels.get(h) or next((k for w, k in labels.items() if h.startswith(w + " /") or h.startswith(w + " -")
                                         or h.startswith(w + ":") or h.endswith("/ " + w) or h.endswith("- " + w)), None)
            if not key or key in out:
                continue
            if ":" in str(v) and _txt(str(v).split(":", 1)[1]):     # "Date: 23/9/2026" in one cell
                out[key] = str(v).split(":", 1)[1].strip()
                continue
            # a value may read like a label of the same field ("المشروع | Owner"); labels of other fields are not values
            other_labels = {w: k for w, k in labels.items() if k != key}
            others = sorted(((abs(j - i), w) for j, w in cells if j != i and not _is_label(w, other_labels)), key=lambda x: x[0])
            if others:
                out[key] = others[0][1]
    if "date" in out:
        out["date"] = _date(out["date"])
    for k in ("client", "location", "number"):
        if k in out:
            out[k] = _txt(out[k])
    return out


def read(rows, title=""):
    """rows: the sheet's cell values (row lists). Returns kind, meta, lines, paid (or None) and fee % per section."""
    rows = [list(r) for r in rows]
    kind = kind_of(title)
    cols, cat, sub = None, "", ""
    lines, fees, paid_rows, paid_total = [], {}, [], None
    first_head, pending = None, ""
    for ri, row in enumerate(rows):
        hm = _header_map(row)
        if hm:
            cols = hm
            if first_head is None:
                first_head = ri
                if pending:
                    cat = pending
            continue
        filled = [(i, v) for i, v in enumerate(row) if _txt(v)]
        if not filled:
            continue
        texts = [_txt(v) for _, v in filled if _num(v) is None]
        if cols is None:            # above the first heading: details, summary and what was paid
            label = texts[0] if texts else ""
            m = _SECTION.match(label) if len(texts) == 1 and len(filled) == 1 else None
            pending = m.group(1).strip() if m and not _TOTAL.match(_n(m.group(1))) else pending
            nums = [_num(v) for _, v in filled if _num(v) is not None]
            if label and _PAID.search(_n(label)) and nums:
                if _n(label).startswith(("اجمالي", "الاجمالي", "total")):
                    paid_total = nums[-1]
                elif not _n(label).startswith(("المتبقي", "remaining", "balance")):
                    paid_rows.append((label, nums[-1]))
            continue
        g = lambda f: row[cols[f]] if f in cols and cols[f] < len(row) else None  # noqa: E731
        desc = _txt(g("desc"))
        first = _txt(row[filled[0][0]])
        # a section title: text only, no quantities or prices
        if not any(_num(g(f)) for f in ("qty", "price", "executed", "total", "actual")) and len(texts) <= 2:
            t = desc or first
            if _FEE.search(_n(t)):
                p = _num(g("pct")) if "pct" in cols else next((_num(v) for _, v in filled if _num(v) is not None), None)
                if p is not None:
                    fees[cat] = p if p <= 1 else p / 100
                continue
            if _TOTAL.match(_n(t)):
                continue
            m = _SECTION.match(t)
            if m and not _ITEM.match(t.replace(" ", "")):
                cat, sub = m.group(1).strip(), ""
            elif t and not desc:
                cat, sub = t.strip(), ""
            elif t:
                sub = t
            continue
        if _FEE.search(_n(desc or first)):        # the fee % of this section (label may sit in any column)
            p = _num(g("pct")) if "pct" in cols else None
            if p is None:
                p = next((x for x in (_num(v) for _, v in filled) if x is not None and 0 < x <= 1), None)
            if p is not None:
                fees[cat] = p if p <= 1 else p / 100
            continue
        if not desc or _TOTAL.match(_n(desc)) or _TOTAL.match(_n(first)):
            continue
        qty, price, ex, pct = _num(g("qty")), _num(g("price")), _num(g("executed")), _num(g("pct"))
        if price is None and qty and _num(g("total")) is not None:
            price = _num(g("total")) / qty
        if not price and not qty and not ex:
            continue
        item = _txt(g("item_no")).replace(" ", "")
        if item and not _ITEM.match(item):
            item = ""                          # not an item number: work outside the BOQ (a variation)
        cost = _num(g("cost"))
        tc, ac, adjusted = _num(g("total")), _num(g("actual")), False
        if tc is not None and abs(tc) < 0.005 and qty and price:      # contract total 0: not in the contract (new work)
            qty = 0
        if ac is not None and price is not None:
            got = (ex or 0) * (pct if pct is not None else 1) * (price or 0)
            if abs(got - ac) > 0.005:          # a hand-made actual total: keep the sheet's amount
                adjusted = True
                if ex and price:
                    pct = ac / (ex * price)
                elif (qty or ac) and price:
                    ex = qty or (ac / price)
                    pct = ac / (ex * price) if ex else 1
        lines.append({"category": cat, "sub": sub, "item_no": item, "description": desc, "unit": _txt(g("unit")),
                      "qty": qty or 0, "executed": ex, "pct": pct, "price": price or 0, "cost": cost,
                      "notes": _txt(g("notes")), "actual_cell": ac, "adjusted": adjusted})
    meta = _meta(rows, first_head if first_head is not None else 12)
    paid = paid_total if paid_total is not None else (round(sum(v for _, v in paid_rows), 2) if paid_rows else None)
    if kind is None and lines:
        kind = "invoice" if any(ln["executed"] is not None for ln in lines) else "boq"
    return {"kind": kind, "meta": meta, "lines": lines, "paid": paid, "paid_rows": paid_rows, "fees": fees,
            "has_actual": any(ln["executed"] is not None for ln in lines)}
