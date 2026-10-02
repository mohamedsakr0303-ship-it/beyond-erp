"""Product & unit sales: a catalog to sell from, sales contracts, payment plans and collections.

Catalog — items (goods, optionally taken from the stock module), units (one of a kind: an apartment, a villa, a car,
a machine) and services, each with a price, specifications and media: photos, video clips (uploaded or linked:
YouTube, Vimeo, Drive, .mp4), 3D models (.glb / .gltf exported from 3ds Max, Blender, SketchUp…, or a Sketchfab /
web link) and virtual tours.

Sales — a contract for one customer with any number of lines and a payment plan:
  full          paid in full on the sale date
  deferred      one payment on a later date
  installments  down payment (+ optional extra payments, e.g. on delivery) and N equal installments every month /
                quarter / half-year / year, with an optional flat yearly interest on the financed amount
  custom        any schedule typed by hand
Revenue is recognised when the goods are sold, or — for off-plan / made-to-order sales — on delivery: until then the
money received is a client advance. The invoice is an ordinary sales invoice (extract), so VAT and e-invoicing work
as usual. Installment interest is deferred and earned in step with what is actually collected. Post-dated cheques
sit in notes receivable until they clear (or bounce back onto the customer's balance).

Every amount is a journal entry with ref SO:<code>; balances are read back from the ledger, so voiding or reversing
an entry in the journal is reflected everywhere.
"""
import base64
import calendar
import datetime as _dt
import json
import re

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

KINDS = ("item", "unit", "service")
STATUSES = ("available", "reserved", "sold", "delivered", "hidden")
MEDIA_KINDS = ("photo", "video", "model", "tour", "file")
PLAN_TYPES = ("full", "deferred", "installments", "custom")
FREQ = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
MAX_FILE = 8 * 1024 * 1024          # uploaded clip / 3D model; bigger files are better linked (they sync to every device)
LIVE_REF = f"{acc.LIVE} AND e.reversed_by IS NULL AND e.reversal_of IS NULL"
EPS = 0.5                            # half a minor unit
SETTINGS_DEFAULTS = {
    "down_pct": 10, "n": 12, "frequency": "monthly", "rate": 0, "round_to": 1, "revenue_on": "sale",
    "terms": ("1. The installments are due on the dates above; a late installment may carry a delay charge as agreed.\n"
              "2. Ownership passes to the buyer once the full price is paid.\n"
              "3. Post-dated cheques count as paid only when cleared."),
    "terms_ar": ("1. تُستحق الأقساط في التواريخ الموضحة أعلاه، ويجوز احتساب غرامة تأخير حسب الاتفاق.\n"
                 "2. تنتقل الملكية للمشتري بعد سداد كامل الثمن.\n"
                 "3. لا تُعتبر الشيكات الآجلة مسددة إلا بعد صرفها."),
}


def _j(v, default):
    try:
        return json.loads(v) if isinstance(v, str) and v else (v if v is not None else default)
    except ValueError:
        return default


def _today():
    return _dt.date.today().isoformat()


def add_months(d, n):
    d = _dt.date.fromisoformat(d[:10])
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return _dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()


def _role(db, role):
    r = db.one("SELECT code FROM accounts WHERE role=?", (role,))
    if not r:
        raise ValidationError(f"The chart of accounts has no '{role}' account")
    return r["code"]


def settings(db):
    s = _j(db.setting("shop_settings"), {})
    return {**SETTINGS_DEFAULTS, **{k: v for k, v in s.items() if k in SETTINGS_DEFAULTS}}


def save_settings(db, d):
    cur = settings(db)
    out = dict(cur)
    for k in ("down_pct", "n", "rate", "round_to"):
        if d.get(k) not in (None, ""):
            v = num(d.get(k), k)
            if v < 0:
                raise ValidationError(f"{k} cannot be negative")
            out[k] = int(v) if k == "n" else v
    if d.get("frequency") in FREQ:
        out["frequency"] = d["frequency"]
    if d.get("revenue_on") in ("sale", "delivery"):
        out["revenue_on"] = d["revenue_on"]
    for k in ("terms", "terms_ar"):
        if k in d:
            out[k] = text(d.get(k), maxlen=8000)
    db.set_setting("shop_settings", json.dumps(out, ensure_ascii=False))
    return out


def _next(db, table, prefix):
    n = 1
    for r in db.q(f"SELECT code FROM {table} WHERE code LIKE ?", (prefix + "%",)):
        tail = r["code"][len(prefix):]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"{prefix}{n:04d}"


# ================================================================== catalog
def _specs_in(v):
    v = _j(v, [])
    if isinstance(v, dict):
        v = list(v.items())
    out = []
    for r in v or []:
        if isinstance(r, (list, tuple)) and len(r) >= 2 and text(r[0]):
            out.append([text(r[0], maxlen=80), text(r[1], maxlen=300)])
        elif isinstance(r, dict) and text(r.get("label")):
            out.append([text(r.get("label"), maxlen=80), text(r.get("value"), maxlen=300)])
    return out[:60]


def get_product(db, pid):
    p = db.one("SELECT * FROM products WHERE id=? OR code=?", (pid if str(pid).isdigit() else -1, text(pid)))
    if not p:
        raise ValidationError("Product not found")
    return p


def save_product(db, d, pid=None):
    old = get_product(db, pid) if pid else None
    code = text(d.get("code"), maxlen=30) or (old["code"] if old else _next(db, "products", "P-"))
    name = text(d.get("name"), "name", required=True, maxlen=200)
    kind = d.get("kind") or (old["kind"] if old else "item")
    if kind not in KINDS:
        raise ValidationError("Kind must be item, unit or service")
    status = d.get("status") or (old["status"] if old else "available")
    if status not in STATUSES:
        raise ValidationError("Unknown status")
    if old and old["status"] in ("reserved", "sold", "delivered") and status != old["status"] and _open_sale(db, old["id"]):
        raise ValidationError(f"{old['code']} is on sale {_open_sale(db, old['id'])} — cancel that sale to change its status")
    rev = text(d.get("revenue_account"))
    if rev:
        acc.account_code(db, rev, types=("Revenue",), field="revenue account")
    inv = text(d.get("inventory_account"))
    if inv:
        acc.account_code(db, inv, types=("Asset",), field="inventory account")
    vat = text(d.get("vat_code"))
    if vat:
        acc.tax_code(db, vat, "vat")
    wh = d.get("warehouse_id") or None
    if wh and not db.one("SELECT 1 FROM warehouses WHERE id=?", (int(wh),)):
        raise ValidationError("Unknown warehouse")
    stock_item = text(d.get("stock_item"))
    if stock_item and not db.one("SELECT 1 FROM stock_items WHERE name=?", (stock_item,)):
        raise ValidationError(f"No stock item called {stock_item!r}")
    vals = dict(code=code, name=name, name_ar=text(d.get("name_ar"), maxlen=200), kind=kind,
                category=text(d.get("category"), maxlen=80), unit=text(d.get("unit"), maxlen=30),
                price=to_minor(num(d.get("price"), "price")), cost=to_minor(num(d.get("cost"), "cost")), vat_code=vat,
                revenue_account=rev, inventory_account=inv, bu_id=acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")),
                stock_item=stock_item, warehouse_id=int(wh) if wh else None,
                specs=json.dumps(_specs_in(d.get("specs")), ensure_ascii=False),
                description=text(d.get("description"), maxlen=8000), status=status)
    with db.tx():
        dup = db.one("SELECT id FROM products WHERE code=?", (code,))
        if dup and (not old or dup["id"] != old["id"]):
            raise ValidationError(f"Code {code} is already used")
        if old:
            db.conn.execute(f"UPDATE products SET {','.join(k + '=?' for k in vals)} WHERE id=?", (*vals.values(), old["id"]))
            return old["id"]
        vals["created_at"] = text(d.get("created_at"))   # never the clock: every synced device replays this call
        return db.conn.execute(f"INSERT INTO products({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                               tuple(vals.values())).lastrowid


def _open_sale(db, pid):
    r = db.one("SELECT s.code FROM sale_lines l JOIN sales_orders s ON s.id=l.sale_id WHERE l.product_id=? "
               "AND s.status IN ('draft','confirmed','delivered') ORDER BY s.id DESC LIMIT 1", (pid,))
    return r["code"] if r else None


def _stock_qty(db, p):
    if not p["stock_item"]:
        return None
    w, a = ["i.name=?"], [p["stock_item"]]
    if p["warehouse_id"]:
        w.append("l.warehouse_id=?")
        a.append(p["warehouse_id"])
    r = db.one(f"SELECT COALESCE(SUM(l.qty),0) q FROM stock_levels l JOIN stock_items i ON i.id=l.item_id WHERE {' AND '.join(w)}", a)
    return r["q"]


def product_row(db, p, media=False):
    out = {**p, "price": to_major(p["price"]), "cost": to_major(p["cost"]), "specs": _j(p["specs"], [])}
    bu = db.one("SELECT code, name FROM business_units WHERE id=?", (p["bu_id"],)) if p["bu_id"] else None
    out["bu_code"], out["bu_name"] = (bu["code"], bu["name"]) if bu else ("", "")
    out["in_stock"] = _stock_qty(db, p)
    out["sale"] = _open_sale(db, p["id"]) if p["kind"] == "unit" else None
    counts = {r["kind"]: r["n"] for r in db.q("SELECT kind, COUNT(*) n FROM product_media WHERE product_id=? GROUP BY kind", (p["id"],))}
    out["media_counts"] = counts
    cover = p["cover_media"] or (db.one("SELECT id FROM product_media WHERE product_id=? AND kind='photo' ORDER BY seq, id LIMIT 1",
                                        (p["id"],)) or {}).get("id")
    out["cover"] = cover
    t = db.one("SELECT thumb, url, kind FROM product_media WHERE id=?", (cover,)) if cover else None
    out["cover_thumb"] = (t["thumb"] or (t["url"] if t["kind"] == "photo" else "")) if t else ""
    if media:
        out["media"] = media_list(db, p["id"])
    return out


def list_products(db, q=None, kind=None, category=None, status=None, bu_id=None):
    w, a = ["1=1"], []
    if q:
        w.append("(code LIKE ? OR name LIKE ? OR name_ar LIKE ? OR category LIKE ? OR description LIKE ? OR specs LIKE ?)")
        a += [f"%{q}%"] * 6
    for col, v in (("kind", kind), ("category", category), ("bu_id", bu_id)):
        if v:
            w.append(f"{col}=?")
            a.append(v)
    if status:
        w.append("status=?")
        a.append(status)
    else:
        w.append("status<>'hidden'")
    rows = [product_row(db, p) for p in db.q(f"SELECT * FROM products WHERE {' AND '.join(w)} ORDER BY category, code", a)]
    cats = [r["category"] for r in db.q("SELECT DISTINCT category FROM products WHERE category<>'' ORDER BY 1")]
    stat = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM products WHERE kind='unit' GROUP BY status")}
    return {"rows": rows, "categories": cats, "unit_status": stat, "next_code": _next(db, "products", "P-")}


def delete_product(db, pid):
    p = get_product(db, pid)
    if db.one("SELECT 1 FROM sale_lines WHERE product_id=?", (p["id"],)):
        raise ValidationError(f"{p['code']} appears on sales — hide it instead of deleting it")
    db.x("DELETE FROM products WHERE id=?", (p["id"],))


# -------------------------------------------------------------------- media
def media_list(db, pid):
    return db.q("SELECT id, kind, title, url, mime, size, seq, thumb, (data<>'') AS uploaded FROM product_media "
                "WHERE product_id=? ORDER BY seq, id", (pid,))


def add_media(db, pid, d):
    p = get_product(db, pid)
    kind = d.get("kind") or "photo"
    if kind not in MEDIA_KINDS:
        raise ValidationError("Media kind must be photo, video, model, tour or file")
    url, data, mime, size = text(d.get("url"), maxlen=2000), "", text(d.get("mime"), maxlen=100), 0
    raw = d.get("data") or ""
    if raw:
        m = re.match(r"^data:([\w/+.\-]*);base64,(.*)$", raw, re.S)
        if m:
            mime, raw = m.group(1) or mime, m.group(2)
        try:
            size = len(base64.b64decode(raw, validate=False))
        except (ValueError, TypeError):
            raise ValidationError("The file could not be read")
        if size > MAX_FILE:
            raise ValidationError(f"The file is {size / 1048576:.1f} MB — the limit is {MAX_FILE // 1048576} MB. "
                                  "Upload it to YouTube / Sketchfab / Google Drive and add the link instead.")
        data = raw
    elif not url:
        raise ValidationError("Upload a file or paste a link")
    elif not re.match(r"^https?://", url, re.I):
        raise ValidationError("A link must start with http:// or https://")
    seq = (db.one("SELECT MAX(seq) m FROM product_media WHERE product_id=?", (p["id"],))["m"] or 0) + 1
    with db.tx():
        thumb = d.get("thumb") or ""
        if thumb and (not str(thumb).startswith("data:image/") or len(thumb) > 200_000):
            thumb = ""
        mid = db.conn.execute("INSERT INTO product_media(product_id,kind,title,url,mime,data,thumb,size,seq) VALUES (?,?,?,?,?,?,?,?,?)",
                              (p["id"], kind, text(d.get("title"), maxlen=200), url, mime, data, thumb, size, seq)).lastrowid
        if kind == "photo" and not p["cover_media"]:
            db.conn.execute("UPDATE products SET cover_media=? WHERE id=?", (mid, p["id"]))
    return mid


def media_file(db, mid):
    m = db.one("SELECT * FROM product_media WHERE id=?", (int(mid),))
    if not m:
        raise ValidationError("Media not found")
    return {"id": m["id"], "kind": m["kind"], "title": m["title"], "url": m["url"], "mime": m["mime"],
            "data": f"data:{m['mime'] or 'application/octet-stream'};base64,{m['data']}" if m["data"] else ""}


def update_media(db, mid, d):
    m = db.one("SELECT * FROM product_media WHERE id=?", (int(mid),))
    if not m:
        raise ValidationError("Media not found")
    with db.tx():
        if "title" in d:
            db.conn.execute("UPDATE product_media SET title=? WHERE id=?", (text(d.get("title"), maxlen=200), m["id"]))
        if d.get("move") in ("up", "down"):
            rows = db.q("SELECT id FROM product_media WHERE product_id=? ORDER BY seq, id", (m["product_id"],))
            ids = [r["id"] for r in rows]
            i = ids.index(m["id"])
            j = i - 1 if d["move"] == "up" else i + 1
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
            for k, x in enumerate(ids, start=1):
                db.conn.execute("UPDATE product_media SET seq=? WHERE id=?", (k, x))
        if d.get("cover"):
            db.conn.execute("UPDATE products SET cover_media=? WHERE id=?", (m["id"], m["product_id"]))


def delete_media(db, mid):
    m = db.one("SELECT product_id FROM product_media WHERE id=?", (int(mid),))
    if not m:
        raise ValidationError("Media not found")
    with db.tx():
        db.conn.execute("DELETE FROM product_media WHERE id=?", (int(mid),))
        db.conn.execute("UPDATE products SET cover_media=NULL WHERE id=? AND cover_media=?", (m["product_id"], int(mid)))


# ============================================================ payment plans
def build_schedule(price, plan, sale_date):
    """(rows, markup) for a price (minor units) and a plan. Rows: due, amount (minor), kind, label."""
    t = plan.get("type") or "full"
    if t not in PLAN_TYPES:
        raise ValidationError("Payment plan must be full, deferred, installments or custom")
    sale_date = acc.clean_date(sale_date)
    if t == "full":
        return [{"due": sale_date, "amount": price, "kind": "down", "label": "Full payment"}], 0
    if t == "deferred":
        due = acc.clean_date(plan.get("due") or add_months(sale_date, 1), "due date")
        return [{"due": due, "amount": price, "kind": "balance", "label": "Payment"}], 0
    if t == "custom":
        rows = []
        for r in plan.get("rows") or []:
            amt = to_minor(num(r.get("amount"), "installment amount"))
            if amt <= 0:
                continue
            rows.append({"due": acc.clean_date(r.get("due"), "due date"), "amount": amt,
                         "kind": r.get("kind") if r.get("kind") in ("down", "extra", "installment", "balance") else "installment",
                         "label": text(r.get("label"), maxlen=80)})
        if not rows:
            raise ValidationError("Add the payments of the custom plan")
        total = sum(r["amount"] for r in rows)
        if total < price - EPS:
            raise ValidationError(f"The custom payments add up to {to_major(total):,.2f} — less than the price {to_major(price):,.2f}")
        return sorted(rows, key=lambda r: r["due"]), total - price
    # installments
    if plan.get("down") not in (None, ""):
        down = to_minor(num(plan.get("down"), "down payment"))
    else:
        down = int(round(price * num(plan.get("down_pct") or 0, "down payment %") / 100))
    rows = []
    if down:
        rows.append({"due": acc.clean_date(plan.get("down_due") or sale_date), "amount": down, "kind": "down", "label": "Down payment"})
    extras = 0
    for x in plan.get("extras") or []:
        if not isinstance(x, dict):
            continue
        amt = to_minor(num(x.get("amount"), "extra payment")) if x.get("amount") not in (None, "") else \
            int(round(price * num(x.get("pct") or 0, "extra payment %") / 100))
        if amt <= 0:
            continue
        extras += amt
        rows.append({"due": acc.clean_date(x.get("due"), "extra payment date"), "amount": amt, "kind": "extra",
                     "label": text(x.get("label"), maxlen=80) or "Extra payment"})
    financed = price - down - extras
    if financed < -EPS:
        raise ValidationError("The down and extra payments are more than the price")
    n = int(num(plan.get("n") or 1, "number of installments"))
    if not 1 <= n <= 600:
        raise ValidationError("Number of installments must be 1–600")
    every = FREQ.get(plan.get("frequency") or "monthly") or int(num(plan.get("every") or 1, "months between installments"))
    first = acc.clean_date(plan.get("first_due") or add_months(sale_date, every), "first installment date")
    rate = num(plan.get("rate") or 0, "interest %")
    if rate < 0:
        raise ValidationError("Interest cannot be negative")
    markup = int(round(financed * rate / 100 * n * every / 12)) if financed > 0 else 0
    to_pay = financed + markup
    step = max(int(round(num(plan.get("round_to") or 1, "round to") * 100)), 1)
    per = (to_pay // n) // step * step if to_pay > 0 else 0
    for k in range(n):
        amt = per if k < n - 1 else to_pay - per * (n - 1)
        if amt > 0:
            rows.append({"due": add_months(first, every * k), "amount": amt, "kind": "installment",
                         "label": f"Installment {k + 1}/{n}"})
    return sorted(rows, key=lambda r: (r["due"], {"down": 0, "extra": 1}.get(r["kind"], 2))), markup


def _lines_in(db, lines):
    out = []
    for i, ln in enumerate(lines or []):
        if not isinstance(ln, dict):
            continue
        p = get_product(db, ln["product_id"]) if ln.get("product_id") else None
        desc = text(ln.get("description"), maxlen=1000) or (p and f"{p['name']} ({p['code']})") or ""
        if not desc:
            continue
        qty = num(ln.get("qty") if ln.get("qty") not in (None, "") else 1, "quantity")
        if qty <= 0:
            raise ValidationError("Quantity must be more than zero")
        if p and p["kind"] == "unit" and qty != 1:
            raise ValidationError(f"{p['code']} is a single unit — quantity must be 1")
        price = to_minor(num(ln.get("price") if ln.get("price") not in (None, "") else (to_major(p["price"]) if p else 0), "price"))
        disc = to_minor(num(ln.get("discount"), "discount"))
        if disc < 0 or disc > round(qty * price):
            raise ValidationError("The discount must be between zero and the line total")
        out.append({"seq": i, "product_id": p["id"] if p else None, "description": desc,
                    "unit": text(ln.get("unit"), maxlen=30) or (p["unit"] if p else ""), "qty": qty, "price": price, "discount": disc})
    if not out:
        raise ValidationError("Add at least one line")
    return out


def line_net(qty, price, discount):
    """(net unit price, line total) in minor units — the invoice carries the discounted unit price."""
    unit = int(round((int(round(qty * price)) - discount) / qty)) if discount else price
    return unit, int(round(qty * unit))


def _totals(db, lines, vat_code, wht_code):
    net = sum(line_net(ln["qty"], ln["price"], ln["discount"])[1] for ln in lines)
    _, vr = acc.tax_code(db, vat_code, "vat")
    _, wr = acc.tax_code(db, wht_code, "wht")
    vat, wht = int(round(net * vr)), int(round(net * wr))
    return {"net": net, "vat": vat, "wht": wht, "price": net + vat - wht}


def preview(db, d):
    """Totals and payment schedule for an unsaved sale (the form's live preview, also the plan calculator)."""
    lines = _lines_in(db, d.get("lines"))
    t = _totals(db, lines, text(d.get("vat_code")), text(d.get("wht_code")))
    rows, markup = build_schedule(t["price"], d.get("plan") or {}, d.get("date") or _today())
    return {**{k: to_major(v) for k, v in t.items()}, "markup": to_major(markup), "total": to_major(t["price"] + markup),
            "schedule": [{**r, "amount": to_major(r["amount"])} for r in rows]}


# ===================================================================== sales
def get(db, sid):
    s = db.one("SELECT * FROM sales_orders WHERE id=? OR code=?", (sid if str(sid).isdigit() else -1, text(sid)))
    if not s:
        raise ValidationError("Sale not found")
    return s


def _units_of(db, sale_id):
    return [r["product_id"] for r in db.q("SELECT l.product_id FROM sale_lines l JOIN products p ON p.id=l.product_id "
                                          "WHERE l.sale_id=? AND p.kind='unit'", (sale_id,))]


def _set_units(db, ids, status):
    for pid in ids:
        db.conn.execute("UPDATE products SET status=? WHERE id=?", (status, pid))


def save(db, d, sid=None):
    """Create or change a draft sale (units on it are reserved)."""
    old = get(db, sid) if sid else None
    if old and old["status"] != "draft":
        raise ValidationError("Only a draft sale can be changed — cancel it and make a new one")
    code = text(d.get("code"), maxlen=30) or (old["code"] if old else _next(db, "sales_orders", "SO-"))
    customer = text(d.get("customer"), "customer", required=True, maxlen=200)
    date = acc.clean_date(d.get("date") or (old and old["date"]) or _today())
    bu = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
    revenue_on = d.get("revenue_on") or settings(db)["revenue_on"]
    if revenue_on not in ("sale", "delivery"):
        raise ValidationError("Revenue must be recognised on sale or on delivery")
    vat_code, _ = acc.tax_code(db, d.get("vat_code"), "vat")
    wht_code, _ = acc.tax_code(db, d.get("wht_code"), "wht")
    lines = _lines_in(db, d.get("lines"))
    plan = d.get("plan") or {"type": "full"}
    if not isinstance(plan, dict):
        raise ValidationError("Payment plan is not valid")
    t = _totals(db, lines, vat_code, wht_code)
    if t["price"] <= 0:
        raise ValidationError("The sale total must be more than zero")
    rows, markup = build_schedule(t["price"], plan, date)
    units = [ln["product_id"] for ln in lines if ln["product_id"] and get_product(db, ln["product_id"])["kind"] == "unit"]
    if len(set(units)) != len(units):
        raise ValidationError("The same unit is on the sale twice")
    with db.tx():
        dup = db.one("SELECT id FROM sales_orders WHERE code=?", (code,))
        if dup and (not old or dup["id"] != old["id"]):
            raise ValidationError(f"Sale number {code} is already used")
        for pid in units:
            p = get_product(db, pid)
            other = _open_sale(db, pid)
            if p["status"] not in ("available", "reserved") or (other and other != (old and old["code"])):
                raise ValidationError(f"{p['code']} — {p['name']} is {p['status']}" + (f" on {other}" if other else ""))
        vals = dict(code=code, date=date, customer=customer, bu_id=bu, revenue_on=revenue_on, vat_code=vat_code, wht_code=wht_code,
                    plan=json.dumps(plan, ensure_ascii=False), price=t["price"], markup=markup,
                    delivery_due=acc.clean_date(d["delivery_due"]) if d.get("delivery_due") else "",
                    salesperson=text(d.get("salesperson"), maxlen=100), notes=text(d.get("notes"), maxlen=8000))
        if old:
            _set_units(db, _units_of(db, old["id"]), "available")
            db.conn.execute(f"UPDATE sales_orders SET {','.join(k + '=?' for k in vals)} WHERE id=?", (*vals.values(), old["id"]))
            sale_id = old["id"]
            db.conn.execute("DELETE FROM sale_lines WHERE sale_id=?", (sale_id,))
            db.conn.execute("DELETE FROM sale_installments WHERE sale_id=?", (sale_id,))
        else:
            vals["created_at"] = text(d.get("created_at")) or date
            sale_id = db.conn.execute(f"INSERT INTO sales_orders({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                                      tuple(vals.values())).lastrowid
        db.conn.executemany("INSERT INTO sale_lines(sale_id,seq,product_id,description,unit,qty,price,discount) VALUES (?,?,?,?,?,?,?,?)",
                            [(sale_id, ln["seq"], ln["product_id"], ln["description"], ln["unit"], ln["qty"], ln["price"], ln["discount"])
                             for ln in lines])
        db.conn.executemany("INSERT INTO sale_installments(sale_id,seq,due,amount,kind,label) VALUES (?,?,?,?,?,?)",
                            [(sale_id, i, r["due"], r["amount"], r["kind"], r["label"]) for i, r in enumerate(rows, start=1)])
        _set_units(db, units, "reserved")
    return sale_id


def delete(db, sid):
    s = get(db, sid)
    if s["status"] != "draft":
        raise ValidationError("Only a draft can be deleted — cancel a confirmed sale instead")
    with db.tx():
        _set_units(db, _units_of(db, s["id"]), "available")
        db.conn.execute("DELETE FROM sales_orders WHERE id=?", (s["id"],))


# ------------------------------------------------------------ ledger reads
def _entries(db, s):
    out = []
    for e in db.q(f"SELECT e.id, e.date, e.kind, e.memo, e.meta FROM journal_entries e WHERE {LIVE_REF} AND e.ref=? "
                  "ORDER BY e.date, e.id", ("SO:" + s["code"],)):
        m = _j(e["meta"], {})
        out.append({"id": e["id"], "date": e["date"], "kind": e["kind"], "memo": e["memo"], "type": m.get("sale_type", ""),
                    "amount": int(m.get("sale_amount") or 0), "cheque": m.get("cheque"), "method": m.get("method", "")})
    return out


def _invoiced(db, s):
    if not s["extract_id"]:
        return False
    x = db.one("SELECT status FROM extracts WHERE id=?", (s["extract_id"],))
    return bool(x and x["status"] == "posted")


def figures(db, s, ents=None):
    ents = _entries(db, s) if ents is None else ents
    tot = lambda *types: sum(e["amount"] for e in ents if e["type"] in types)  # noqa: E731
    cash = tot("receipt", "clear")
    refunded = tot("refund")
    cheques = tot("cheque") - tot("clear") - tot("bounce")
    total = s["price"] + s["markup"]
    paid = cash - refunded
    return {"price": s["price"], "markup": s["markup"], "total": total, "paid": paid, "in_cheques": cheques,
            "refunded": refunded, "balance": total - paid, "uncovered": total - paid - cheques,
            "interest_earned": tot("interest"), "invoiced": _invoiced(db, s)}


def _schedule(db, s, f, as_of):
    rows = db.q("SELECT * FROM sale_installments WHERE sale_id=? ORDER BY due, seq", (s["id"],))
    cash, chq = max(f["paid"], 0), max(f["in_cheques"], 0)
    out = []
    for r in rows:
        a = r["amount"]
        p = min(a, cash)
        cash -= p
        c = min(a - p, chq)
        chq -= c
        left = a - p - c
        if s["status"] == "cancelled":
            st = "cancelled"
        elif s["status"] == "draft":      # nothing is owed on a draft yet
            st = "due"
        elif left <= EPS and c <= EPS:
            st = "paid"
        elif left <= EPS:
            st = "cheque"
        elif r["due"] < as_of:
            st = "overdue"
        elif p > EPS or c > EPS:
            st = "partial"
        else:
            st = "due"
        out.append({"id": r["id"], "seq": r["seq"], "due": r["due"], "kind": r["kind"], "label": r["label"],
                    "amount": to_major(a), "paid": to_major(p), "by_cheque": to_major(c), "left": to_major(left), "status": st,
                    "days_late": (_dt.date.fromisoformat(as_of) - _dt.date.fromisoformat(r["due"])).days if st == "overdue" else 0})
    return out


def _row(db, s, as_of=None, sched=False):
    as_of = as_of or _today()
    f = figures(db, s)
    sc = _schedule(db, s, f, as_of)
    over = [r for r in sc if r["status"] == "overdue"]
    nxt = next((r for r in sc if r["status"] in ("due", "partial", "overdue")), None)
    bu = db.one("SELECT code FROM business_units WHERE id=?", (s["bu_id"],)) if s["bu_id"] else None
    out = {**{k: v for k, v in s.items() if k not in ("price", "markup")}, "plan": _j(s["plan"], {}),
           "bu_code": bu["code"] if bu else "", **{k: (to_major(v) if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in f.items()},
           "overdue": round(sum(r["left"] for r in over), 2), "overdue_count": len(over),
           "next_due": nxt["due"] if nxt else None, "next_amount": nxt["left"] if nxt else 0}
    if sched:
        out["schedule"] = sc
    return out


def register(db, q=None, status=None, customer=None, date_from=None, date_to=None, as_of=None):
    w, a = ["1=1"], []
    if q:
        w.append("(s.code LIKE ? OR s.customer LIKE ? OR s.notes LIKE ? OR EXISTS (SELECT 1 FROM sale_lines l WHERE l.sale_id=s.id AND l.description LIKE ?))")
        a += [f"%{q}%"] * 4
    for cond, v in (("s.status=?", status), ("s.customer=?", customer), ("s.date>=?", date_from), ("s.date<=?", date_to)):
        if v:
            w.append(cond)
            a.append(v)
    rows = [_row(db, s, as_of) for s in db.q(f"SELECT s.* FROM sales_orders s WHERE {' AND '.join(w)} ORDER BY s.date DESC, s.id DESC", a)]
    live = [r for r in rows if r["status"] in ("confirmed", "delivered")]
    totals = {k: round(sum(r[k] for r in live), 2) for k in ("total", "paid", "balance", "in_cheques", "overdue")}
    totals["count"] = len(live)
    return {"rows": rows, "totals": totals, "next_code": _next(db, "sales_orders", "SO-")}


def detail(db, sid, as_of=None):
    s = get(db, sid)
    out = _row(db, s, as_of, sched=True)
    out["lines"] = []
    for ln in db.q("SELECT l.*, p.code AS product_code, p.kind AS product_kind, p.cover_media AS cover FROM sale_lines l "
                   "LEFT JOIN products p ON p.id=l.product_id WHERE l.sale_id=? ORDER BY l.seq, l.id", (s["id"],)):
        out["lines"].append({**ln, "price": to_major(ln["price"]), "discount": to_major(ln["discount"]),
                             "amount": to_major(line_net(ln["qty"], ln["price"], ln["discount"])[1])})
    t = _totals(db, [{"qty": ln["qty"], "price": to_minor(ln["price"]), "discount": to_minor(ln["discount"])} for ln in out["lines"]],
                s["vat_code"], s["wht_code"])
    out.update(net=to_major(t["net"]), vat=to_major(t["vat"]), wht=to_major(t["wht"]))
    out["cheques"] = [{**c, "amount": to_major(c["amount"])} for c in db.q("SELECT * FROM sale_cheques WHERE sale_id=? ORDER BY due, id", (s["id"],))]
    out["entries"] = [{**e, "amount": to_major(e["amount"])} for e in _entries(db, s)]
    x = db.one("SELECT id, number, status FROM extracts WHERE id=?", (s["extract_id"],)) if s["extract_id"] else None
    out["invoice"] = x
    p = db.one("SELECT phone, whatsapp, email FROM parties WHERE name=?", (s["customer"],))
    out["contact"] = p or {}
    return out


# ------------------------------------------------------------------ posting
def _post(db, s, date, kind, memo, lines, sale_type, amount, user, extra=None):
    return acc._insert_entry(db, date=date, kind=kind, memo=memo, bu_id=s["bu_id"], party=s["customer"], ref="SO:" + s["code"],
                             lines=lines, meta={"sale": s["code"], "sale_type": sale_type, "sale_amount": int(amount), **(extra or {})},
                             user=user)


def _tag(db, eid, s, sale_type, party=False):
    """Link an entry made by another module (the invoice, a stock issue) to the sale."""
    e = db.one("SELECT meta FROM journal_entries WHERE id=?", (eid,))
    m = {**_j(e["meta"], {}), "sale": s["code"], "sale_type": sale_type}
    db.conn.execute("UPDATE journal_entries SET ref=?, meta=?" + (", party=?" if party else "") + " WHERE id=?",
                    ("SO:" + s["code"], json.dumps(m, ensure_ascii=False)) + ((s["customer"],) if party else ()) + (eid,))


def _target(db, s):
    """Where money from the customer is credited: receivables once invoiced, else client advances."""
    return db.acct("ar") if _invoiced(db, s) else _role(db, "client_advances")


def _sync_interest(db, s, date, user):
    """Earn the installment interest in step with what has been collected."""
    s = get(db, s["id"])
    if not s["markup"] or not _invoiced(db, s):
        return
    f = figures(db, s)
    want = int(round(s["markup"] * min(1.0, max(0, f["paid"]) / f["total"]))) if f["total"] else 0
    delta = want - f["interest_earned"]
    if abs(delta) < 1:
        return
    d, c = _role(db, "installment_deferred"), _role(db, "installment_income")
    a = to_major(abs(delta))
    _post(db, s, date, "Installment Interest", f"Installment interest earned — {s['code']}",
          [{"account": d, "debit": a}, {"account": c, "credit": a}] if delta > 0 else [{"account": c, "debit": a}, {"account": d, "credit": a}],
          "interest", delta, user)


def _recognize(db, s, date, user):
    """Invoice the sale (ordinary sales invoice), book the interest receivable, move advances to receivables, cost of sales."""
    lines = db.q("SELECT l.*, p.revenue_account, p.kind AS pkind, p.stock_item, p.warehouse_id, p.cost, p.inventory_account, p.code AS pcode "
                 "FROM sale_lines l LEFT JOIN products p ON p.id=l.product_id WHERE l.sale_id=? ORDER BY l.seq, l.id", (s["id"],))
    default_rev = _role(db, "product_sales")
    xl = []
    for ln in lines:
        rev = ln["revenue_account"] or default_rev
        unit_price = line_net(ln["qty"], ln["price"], ln["discount"])[0]
        xl.append({"description": ln["description"], "unit": ln["unit"], "qty": ln["qty"], "price": to_major(unit_price),
                   "account": rev, "item_no": ln["pcode"] or ""})
    number = s["code"]
    while db.one("SELECT 1 FROM extracts WHERE number=?", (number,)):
        number += "-INV"
    xid = acc.save_extract(db, {"bu_id": s["bu_id"], "number": number, "date": date, "customer": s["customer"],
                                "vat_code": s["vat_code"], "wht_code": s["wht_code"], "lines": xl})
    eid = acc.post_extract(db, xid, date=date, user=user)
    _tag(db, eid, s, "invoice")
    db.conn.execute("UPDATE sales_orders SET extract_id=? WHERE id=?", (xid, s["id"]))
    s = get(db, s["id"])
    ar = db.acct("ar")
    if s["markup"]:
        a = to_major(s["markup"])
        _post(db, s, date, "Installment Interest", f"Installment interest (deferred) — {s['code']}",
              [{"account": ar, "debit": a}, {"account": _role(db, "installment_deferred"), "credit": a}], "markup", s["markup"], user)
    adv = _role(db, "client_advances")
    held = db.one(f"SELECT COALESCE(SUM(l.credit-l.debit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                  f"WHERE {LIVE_REF} AND e.ref=? AND l.account=?", ("SO:" + s["code"], adv))["n"]
    if held > 0:
        _post(db, s, date, "Advance Applied", f"Advances applied to the invoice — {s['code']}",
              [{"account": adv, "debit": to_major(held)}, {"account": ar, "credit": to_major(held)}], "apply", held, user)
    cos = _role(db, "cost_of_sales")
    for ln in lines:
        if ln["stock_item"]:
            wh = ln["warehouse_id"] or (db.one("SELECT l.warehouse_id FROM stock_levels l JOIN stock_items i ON i.id=l.item_id "
                                               "WHERE i.name=? AND l.qty>=? ORDER BY l.qty DESC LIMIT 1", (ln["stock_item"], ln["qty"])) or {}).get("warehouse_id")
            if not wh:
                raise ValidationError(f"Not enough {ln['stock_item']} in stock for {ln['qty']:g}")
            ceid = acc.stock_move(db, {"type": "Issue", "date": date, "warehouse_id": wh, "item": ln["stock_item"], "qty": ln["qty"],
                                       "bu_id": s["bu_id"], "account": cos, "note": s["code"]}, user=user)
            _tag(db, ceid, s, "cogs", party=True)
        elif ln["cost"] and ln["inventory_account"]:
            v = int(round(ln["qty"] * ln["cost"]))
            _post(db, s, date, "Cost of Sales", f"Cost of {ln['description']}"[:300],
                  [{"account": cos, "debit": to_major(v)}, {"account": ln["inventory_account"], "credit": to_major(v)}], "cogs", v, user)
    _sync_interest(db, s, date, user)


def _date(d, s):
    return acc.clean_date(d.get("date") or _today())


def confirm(db, sid, d=None, user=""):
    d = d or {}
    s = get(db, sid)
    if s["status"] != "draft":
        raise ValidationError("This sale is already confirmed")
    date = acc.clean_date(d.get("date") or s["date"])
    with db.tx():
        db.conn.execute("UPDATE sales_orders SET status='confirmed' WHERE id=?", (s["id"],))
        _set_units(db, _units_of(db, s["id"]), "sold")
        s = get(db, s["id"])
        if s["revenue_on"] == "sale":
            _recognize(db, s, date, user)
        if d.get("collect") not in (None, "", 0, "0") and num(d.get("collect"), "amount received") > 0:
            _collect(db, get(db, s["id"]), {**d, "amount": d["collect"], "date": date}, user)
    return s["id"]


def deliver(db, sid, d=None, user=""):
    d = d or {}
    s = get(db, sid)
    if s["status"] != "confirmed":
        raise ValidationError("Only a confirmed sale can be delivered")
    date = _date(d, s)
    with db.tx():
        db.conn.execute("UPDATE sales_orders SET status='delivered', delivered_on=? WHERE id=?", (date, s["id"]))
        _set_units(db, _units_of(db, s["id"]), "delivered")
        if not _invoiced(db, s):
            _recognize(db, get(db, s["id"]), date, user)
    return s["id"]


def _collect(db, s, d, user):
    if s["status"] not in ("confirmed", "delivered"):
        raise ValidationError("Confirm the sale before receiving money on it")
    date = _date(d, s)
    f = figures(db, s)
    target = _target(db, s)
    method = text(d.get("method")) or "Cash"
    cheques = d.get("cheques") or []
    if method == "Cheque" and (d.get("postdated") or cheques) and not cheques:
        cheques = [{"number": d.get("cheque_no"), "bank": d.get("bank"), "due": d.get("cheque_due") or date, "amount": d.get("amount")}]
    made = []
    if cheques:
        total = 0
        for c in cheques:
            amt = to_minor(num(c.get("amount"), "cheque amount", allow_zero=False))
            total += amt
        if total > f["uncovered"] + EPS:
            raise ValidationError(f"The cheques ({to_major(total):,.2f}) are more than what is left to cover ({to_major(f['uncovered']):,.2f})")
        nr = _role(db, "notes_receivable")
        for c in cheques:
            amt = to_minor(num(c.get("amount"), "cheque amount"))
            due = acc.clean_date(c.get("due") or date, "cheque date")
            no, bank = text(c.get("number"), maxlen=40), text(c.get("bank"), maxlen=80)
            cid = db.conn.execute("INSERT INTO sale_cheques(sale_id,number,bank,due,amount,status) VALUES (?,?,?,?,?,'pending')",
                                  (s["id"], no, bank, due, amt)).lastrowid
            eid = _post(db, s, date, "Cheque Received", f"Cheque {no} {bank} due {due} — {s['code']}".replace("  ", " "),
                        [{"account": nr, "debit": to_major(amt)}, {"account": target, "credit": to_major(amt)}], "cheque", amt, user,
                        {"cheque": cid, "method": "Cheque"})
            db.conn.execute("UPDATE sale_cheques SET entry_id=? WHERE id=?", (eid, cid))
            made.append(eid)
    else:
        amt = to_minor(num(d.get("amount"), "amount", allow_zero=False))
        if amt <= 0:
            raise ValidationError("Amount must be more than zero")
        if amt > f["uncovered"] + EPS and not d.get("allow_over"):
            raise ValidationError(f"{to_major(amt):,.2f} is more than what is left to pay ({to_major(f['uncovered']):,.2f})")
        into = acc.money_account(db, method, d.get("received_into"))
        made.append(_post(db, s, date, "Receipt Voucher", text(d.get("memo")) or f"Payment on {s['code']}",
                          [{"account": into, "debit": to_major(amt)}, {"account": target, "credit": to_major(amt)}], "receipt", amt, user,
                          {"method": method, "receipt_ref": text(d.get("ref"))}))
    _sync_interest(db, s, date, user)
    return made


def collect(db, sid, d, user=""):
    s = get(db, sid)
    with db.tx():
        return _collect(db, s, d, user)


def cheque(db, cid, action, d=None, user=""):
    d = d or {}
    c = db.one("SELECT * FROM sale_cheques WHERE id=?", (int(cid),))
    if not c:
        raise ValidationError("Cheque not found")
    s = get(db, c["sale_id"])
    if c["status"] != "pending":
        raise ValidationError(f"This cheque is already {c['status']}")
    date = _date(d, s)
    nr = _role(db, "notes_receivable")
    a = to_major(c["amount"])
    with db.tx():
        if action == "clear":
            into = acc.money_account(db, d.get("method") or "Bank Transfer", d.get("received_into"))
            eid = _post(db, s, date, "Cheque Cleared", f"Cheque {c['number']} cleared — {s['code']}",
                        [{"account": into, "debit": a}, {"account": nr, "credit": a}], "clear", c["amount"], user, {"cheque": c["id"]})
            st = "cleared"
        elif action == "bounce":
            eid = _post(db, s, date, "Cheque Bounced", f"Cheque {c['number']} bounced — {s['code']}",
                        [{"account": _target(db, s), "debit": a}, {"account": nr, "credit": a}], "bounce", c["amount"], user, {"cheque": c["id"]})
            st = "bounced"
        else:
            raise ValidationError("Unknown cheque action")
        db.conn.execute("UPDATE sale_cheques SET status=?, settled_entry=?, settled_on=? WHERE id=?", (st, eid, date, c["id"]))
        _sync_interest(db, s, date, user)
    return eid


def refund(db, sid, d, user=""):
    """Pay money back to the customer (e.g. before cancelling); 'keep' is a cancellation charge kept as income."""
    s = get(db, sid)
    f = figures(db, s)
    back = to_minor(num(d.get("amount"), "amount refunded"))
    keep = to_minor(num(d.get("keep"), "amount kept"))
    if back < 0 or keep < 0 or back + keep <= 0:
        raise ValidationError("Enter the amount paid back and / or kept")
    if back + keep > f["paid"] + EPS:
        raise ValidationError(f"Only {to_major(f['paid']):,.2f} has been paid on this sale")
    date = _date(d, s)
    lines = [{"account": _target(db, s), "debit": to_major(back + keep)}]
    if back:
        lines.append({"account": acc.money_account(db, d.get("method"), d.get("paid_from")), "credit": to_major(back)})
    if keep:
        lines.append({"account": _role(db, "other_income"), "credit": to_major(keep)})
    with db.tx():
        eid = _post(db, s, date, "Sale Refund", text(d.get("memo")) or f"Refund on {s['code']}", lines, "refund", back + keep, user,
                    {"refund": back, "kept": keep, "method": text(d.get("method"))})
        _sync_interest(db, s, date, user)
    return eid


def cancel(db, sid, d=None, user=""):
    d = d or {}
    s = get(db, sid)
    if s["status"] == "draft":
        return delete(db, sid)
    if s["status"] == "cancelled":
        raise ValidationError("Already cancelled")
    f = figures(db, s)
    if f["paid"] > EPS or f["in_cheques"] > EPS:
        raise ValidationError("Money received on this sale must be refunded first (Refund) and pending cheques returned "
                              "(mark them bounced / returned)")
    date = _date(d, s)
    with db.tx():
        for e in reversed(_entries(db, s)):
            if e["type"] in ("invoice", "markup", "apply", "cogs", "interest"):
                acc.reverse_entry(db, e["id"], date=date, user=user)
        if s["extract_id"]:
            x = db.one("SELECT status FROM extracts WHERE id=?", (s["extract_id"],))
            if x and x["status"] == "draft":
                db.conn.execute("UPDATE sales_orders SET extract_id=NULL WHERE id=?", (s["id"],))
                db.conn.execute("DELETE FROM extracts WHERE id=?", (s["extract_id"],))
        db.conn.execute("UPDATE sales_orders SET status='cancelled', notes=notes || ? WHERE id=?",
                        (("\n" + text(d.get("reason"))) if d.get("reason") else "", s["id"]))
        _set_units(db, _units_of(db, s["id"]), "available")
    return s["id"]


# ------------------------------------------------------------ follow-up
def installments(db, as_of=None, status=None, date_from=None, date_to=None, customer=None):
    """Every installment of the open sales with what is paid / covered by cheque / still owed — for collections."""
    as_of = as_of or _today()
    out = []
    phones = {p["name"]: p for p in db.q("SELECT name, phone, whatsapp FROM parties")}
    w, a = ["status IN ('confirmed','delivered')"], []
    if customer:
        w.append("customer=?")
        a.append(customer)
    for s in db.q(f"SELECT * FROM sales_orders WHERE {' AND '.join(w)} ORDER BY code", a):
        f = figures(db, s)
        for r in _schedule(db, s, f, as_of):
            if status == "open" and r["status"] in ("paid", "cheque"):
                continue
            if status and status not in ("open", "all") and r["status"] != status:
                continue
            if date_from and r["due"] < date_from:
                continue
            if date_to and r["due"] > date_to:
                continue
            ph = phones.get(s["customer"], {})
            out.append({**r, "sale_id": s["id"], "code": s["code"], "customer": s["customer"],
                        "phone": ph.get("phone", ""), "whatsapp": ph.get("whatsapp", "")})
    out.sort(key=lambda r: (r["due"], r["code"]))
    tot = {k: round(sum(r[k] for r in out), 2) for k in ("amount", "paid", "by_cheque", "left")}
    tot["overdue"] = round(sum(r["left"] for r in out if r["status"] == "overdue"), 2)
    return {"rows": out, "totals": tot, "as_of": as_of}


def overdue_summary(db, as_of=None):
    r = installments(db, as_of, "overdue")
    return {"count": len(r["rows"]), "amount": r["totals"]["left"],
            "customers": len({x["customer"] for x in r["rows"]})} if r["rows"] else None


def cheques_due(db, upto=None):
    upto = upto or _today()
    return db.q("SELECT c.*, s.code, s.customer FROM sale_cheques c JOIN sales_orders s ON s.id=c.sale_id "
                "WHERE c.status='pending' AND c.due<=? ORDER BY c.due", (upto,))


# ------------------------------------------------------------ backup
TABLES = ("products", "product_media", "sales_orders", "sale_lines", "sale_installments", "sale_cheques")


def export_rows(db):
    out = {t: db.q(f"SELECT * FROM {t} ORDER BY id") for t in TABLES}
    bus = {b["id"]: b["code"] for b in db.q("SELECT id, code FROM business_units")}
    whs = {w["id"]: w["name"] for w in db.q("SELECT id, name FROM warehouses")}
    xs = {x["id"]: x["number"] for x in db.q("SELECT id, number FROM extracts")}
    for r in out["products"]:
        r["bu_code"], r["warehouse"] = bus.get(r["bu_id"], ""), whs.get(r["warehouse_id"], "")
    for r in out["sales_orders"]:
        r["bu_code"], r["extract_number"] = bus.get(r["bu_id"], ""), xs.get(r["extract_id"], "")
    return out


def import_rows(db, data, counts):
    if not isinstance(data, dict):
        return
    for t in reversed(TABLES):
        db.conn.execute(f"DELETE FROM {t}")
    bus = {b["code"]: b["id"] for b in db.q("SELECT id, code FROM business_units")}
    whs = {w["name"]: w["id"] for w in db.q("SELECT id, name FROM warehouses")}
    xs = {x["number"]: x["id"] for x in db.q("SELECT id, number FROM extracts")}
    eids = {e["id"] for e in db.q("SELECT id FROM journal_entries")}
    for t in TABLES:
        rows = data.get(t) or []
        cols = [c[1] for c in db.conn.execute(f"PRAGMA table_info({t})")]
        for r in rows:
            if not isinstance(r, dict):
                continue
            r = dict(r)
            if t == "products":
                r["bu_id"], r["warehouse_id"] = bus.get(r.get("bu_code")), whs.get(r.get("warehouse"))
            if t == "sales_orders":
                r["bu_id"], r["extract_id"] = bus.get(r.get("bu_code")), xs.get(r.get("extract_number"))
            if t == "sale_cheques":
                r["entry_id"] = r.get("entry_id") if r.get("entry_id") in eids else None
                r["settled_entry"] = r.get("settled_entry") if r.get("settled_entry") in eids else None
            keep = [c for c in cols if c in r]
            db.conn.execute(f"INSERT INTO {t}({','.join(keep)}) VALUES ({','.join('?' * len(keep))})", [r[c] for c in keep])
        if rows:
            counts[t] = len(rows)
