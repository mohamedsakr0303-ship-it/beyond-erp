"""Lines of one BOQ or invoice, read back from an Excel file edited outside the app.

Works with the BOQ / invoice files the app itself produces (section bars "1- Category", sub-section bars, a header row
repeated per section, section totals) and with any plain sheet that has a header row (English or Arabic). A line's
look — bold, italic, underline, text and fill colour, alignment — comes along. Nothing is saved here: the lines go back
to the screen, where they replace the ones shown until the person saves."""
import io
import json
import re
from collections import Counter

from .accounting import ValidationError, line_style

# header words → field (lower case, spaces and line breaks squeezed)
HEADS = {
    "item_no": ("item", "item no", "item no.", "no", "no.", "#", "code", "ref", "م", "رقم", "رقم البند", "البند", "كود"),
    "category": ("category", "section", "group", "القسم", "المجموعة", "الفئة الرئيسية", "التصنيف"),
    "sub": ("sub", "sub-section", "subsection", "sub section", "القسم الفرعي", "فرعي"),
    "description": ("description", "desc", "item description", "works", "work", "البيان", "الوصف", "بيان الأعمال", "الأعمال"),
    "unit": ("unit", "uom", "u.o.m", "الوحدة", "وحدة"),
    "qty": ("qty", "quantity", "qty.", "الكمية", "الكميه", "الكمية بالعقد", "contract qty"),
    "executed": ("executed", "executed qty", "done", "المنفذ", "المنفذ فعلي", "الكمية المنفذة"),
    "price": ("price", "rate", "unit price", "unit rate", "السعر", "الفئة", "سعر الوحدة", "فئة"),
    "cost": ("cost", "unit cost", "التكلفة", "تكلفة الوحدة"),
    "amount": ("amount", "total", "value", "الإجمالي", "الاجمالي", "القيمة", "الإجمالي بالعقد"),
    "pct": ("%", "pct", "percent", "percentage", "النسبة", "نسبة"),
    "notes": ("notes", "remarks", "ملاحظات"),
}
_WORDS = {w: k for k, ws in HEADS.items() for w in ws}
TOTAL_WORDS = ("إجمالي", "اجمالي", "total", "subtotal", "sub-total", "grand total", "المجموع")


def _key(v):
    return re.sub(r"\s+", " ", str(v or "").replace("\n", " ")).strip().lower()


def _num(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("٬", "").strip()
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
    try:
        return float(s.rstrip("%")) / (100 if s.endswith("%") else 1)
    except ValueError:
        return None


def _hex(c):
    try:
        if c is None or c.type != "rgb" or not isinstance(c.rgb, str):
            return None
        v = c.rgb[-6:]
        return "#" + v.lower() if re.match(r"^[0-9A-Fa-f]{6}$", v) else None
    except AttributeError:
        return None


def _look(cell):
    """bold / italic / underline / colours / alignment of a cell, as a line style."""
    f, look = cell.font, {}
    if f is not None:
        if f.b:
            look["b"] = 1
        if f.i:
            look["i"] = 1
        if f.u and f.u != "none":
            look["u"] = 1
        col = _hex(f.color)
        if col and col not in ("#000000",):
            look["color"] = col
    fill = cell.fill
    if fill is not None and getattr(fill, "fill_type", None) == "solid":
        bg = _hex(fill.fgColor)
        if bg and bg not in ("#ffffff",):
            look["bg"] = bg
    h = getattr(cell.alignment, "horizontal", None)
    if h in ("left", "center", "right"):
        look["align"] = h
    return look


def _header(row):
    """{field: column index} when this row looks like a header (description + at least one of qty / price)."""
    out = {}
    for i, v in enumerate(row):
        k = _WORDS.get(_key(v))
        if k and k not in out:
            out[k] = i
    return out if "description" in out and ({"qty", "price", "executed", "amount"} & out.keys()) else None


def parse(data, kind="boq"):
    """xlsx bytes -> {"lines": [...], "sheet": name, "skipped": n}. kind 'invoice': the executed quantity, when the file
    has one (the app's invoice layout), is the line's quantity."""
    try:
        import openpyxl
    except ImportError:
        raise ValidationError("Reading Excel needs openpyxl:  python -m pip install openpyxl") from None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception:  # noqa: BLE001 — anything that is not a readable workbook
        raise ValidationError("This is not an Excel file the app can read (.xlsx)") from None
    best = None
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        rows = list(ws.iter_rows())
        for i, r in enumerate(rows[:200]):
            h = _header([c.value for c in r])
            if h:
                best = best or (ws, rows, i, h)
                break
        if best:
            break
    if not best:
        raise ValidationError("No table found. The sheet needs a header row with at least Description and Qty or Price.")
    ws, rows, hi, h = best
    get = lambda r, k: r[h[k]].value if k in h and h[k] < len(r) else None  # noqa: E731
    lines, category, sub, skipped = [], "", "", 0
    for r in reversed(rows[max(0, hi - 3):hi]):     # the app's own files: the first section's bar sits above the header
        t = [str(c.value).strip() for c in r if isinstance(c.value, str) and str(c.value).strip()]
        m = re.match(r"^\s*\d+\s*[-–.)]\s*(.*)$", t[0]) if len(t) == 1 else None
        if m:
            category = m.group(1).strip()
            break
    for r in rows[hi + 1:]:
        vals = [c.value for c in r]
        if not any(v not in (None, "") for v in vals):
            continue
        if _header(vals):                     # the header repeated under each section
            continue
        desc = str(get(r, "description") or "").strip()
        texts = [str(v).strip() for v in vals if isinstance(v, str) and str(v).strip()]
        nums = [v for v in (get(r, k) for k in ("qty", "executed", "price", "amount")) if _num(v) is not None]
        first = texts[0] if texts else ""
        if any(_key(first).startswith(w) for w in TOTAL_WORDS) or any(_key(desc).startswith(w) for w in TOTAL_WORDS):
            skipped += 1                      # section / grand totals: worked out again by the app
            continue
        if not nums and not get(r, "unit") and texts:
            # a bar across the row: "1- Category" is a section, anything else a sub-section
            t = first
            m = re.match(r"^\s*\d+\s*[-–.)]\s*(.*)$", t)
            if m:
                category, sub = m.group(1).strip(), ""
            elif "category" not in h:
                sub = t
            continue
        if not desc:
            skipped += 1
            continue
        qty = _num(get(r, "executed")) if kind == "invoice" and "executed" in h else _num(get(r, "qty"))
        price = _num(get(r, "price"))
        amount = _num(get(r, "amount"))
        if price is None and amount is not None and qty:
            price = amount / qty
        cell = r[h["description"]]
        ln = {"item_no": str(get(r, "item_no") or "").strip(), "category": str(get(r, "category") or category or "").strip(),
              "sub": str(get(r, "sub") or sub or "").strip(), "description": desc, "unit": str(get(r, "unit") or "").strip(),
              "qty": qty or 0, "price": round(price or 0, 6), "cost": _num(get(r, "cost")) or 0,
              "style": _look(cell)}
        if "pct" in h and _num(get(r, "pct")) is not None:
            p = _num(get(r, "pct"))
            ln["pct"] = p / 100 if p > 1 else p
        if isinstance(ln["item_no"], str) and re.match(r"^\d+\.0$", ln["item_no"]):
            ln["item_no"] = ln["item_no"][:-2]
        lines.append(ln)
    if not lines:
        raise ValidationError("The table has no lines with a description")
    # what most lines share is the sheet's normal look, not a line's own (e.g. every description aligned right)
    common = {}
    for k in ("b", "i", "u", "color", "bg", "align"):
        v, n = Counter(json.dumps(ln["style"].get(k)) for ln in lines).most_common(1)[0]
        if n * 2 > len(lines):
            common[k] = json.loads(v)
    for ln in lines:
        own = {k: v for k, v in ln["style"].items() if common.get(k, "\0") != v}
        ln["style"] = json.loads(line_style(own) or "{}")
    return {"lines": lines, "sheet": ws.title, "skipped": skipped}
