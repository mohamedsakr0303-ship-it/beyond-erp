"""Equipment rental: scaffolding (by piece, by frame, by square metre), construction elevators, cradles.

Flow of a rental:  price offer (quote) -> client purchase order (required before anything moves) -> signed contract
-> signed delivery handover with a safety checklist -> monthly rent invoices + one-time charges -> signed return
handover (pieces back good, damaged or lost; lost / damaged charged at their loss price) -> close.

Stock is metal kept for years, bought in lots at different prices, so every piece is tracked FIFO: deliveries take
the oldest lots in the yard first; returns, losses and scrapping settle against the lots the contract holds, oldest
first. The piece cost behind every figure (fleet value, loss write-off, the share of a frame's or a square metre's
rent that each piece earns) is the real cost of those lots.

Money goes through the books: equipment purchases (Dr rental fleet), losses (Dr loss on rental equipment, Cr fleet
at FIFO cost), and rent invoices, which are ordinary sales invoices (receivables, VAT, e-invoicing) on the
contract's own business unit, so each contract has its own profit and loss.
"""
import ast
import datetime as _dt
import json
import math

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

CATEGORIES = ("scaffolding", "elevator", "cradle_manual", "cradle_motorized", "other")
LINE_KINDS = ("item", "kit", "sqm", "charge")
CHARGE_TYPES = ("install", "dismantle", "transport", "materials", "other")
CHARGE_WHEN = ("delivery", "return", "manual")
STATUSES = ("quote", "ordered", "active", "returned", "closed", "cancelled")
KINDS = ("Rental Equipment Purchase", "Rental Equipment Loss", "Rental Equipment Depreciation", "Rental Deposit")
LIVE_REF = f"{acc.LIVE} AND e.reversed_by IS NULL AND e.reversal_of IS NULL"
EPS = 1e-9


def _now():
    return acc.now_iso()


def _j(v, empty):
    try:
        out = json.loads(v) if isinstance(v, str) and v else (v if v is not None else empty)
    except ValueError:
        return empty
    return out if isinstance(out, type(empty)) else empty


def _role_or(db, role, fallback=None):
    r = db.one("SELECT code FROM accounts WHERE role=?", (role,))
    return r["code"] if r else fallback


def _code(db, table, prefix, width=4):
    n = 1
    for r in db.q(f"SELECT code FROM {table} WHERE code LIKE ?", (prefix + "%",)):
        tail = r["code"][len(prefix):]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"{prefix}{n:0{width}d}"


# ================================================================ settings
DEFAULT_TERMS = {
    "en": "Custody: from signing this handover the client is fully responsible for guarding and keeping the equipment "
          "listed above.\nCompensation: any piece lost, missing or damaged through misuse or negligence on site is paid by the "
          "client at the loss price shown for it, immediately or deducted from what is due to them.\nNo alterations: no "
          "welding, cutting or modification of the metal parts is allowed; any piece altered counts as fully damaged and is "
          "compensated in full.",
    "ar": "مسؤولية الحفظ: تقر الشركة المستلمة بمسؤوليتها الكاملة عن حراسة وحفظ المعدات ومكوناتها المذكورة في الجدول أعلاه فور "
          "التوقيع على هذا المحضر.\nالتعويض المالي: في حال فقدان أو ضياع أو تلف أي قطعة من المكونات نتيجة سوء الاستخدام أو الإهمال "
          "في الموقع، يلتزم الطرف المستلم بدفع قيمة القطعة الموضحة في حقل (سعر الفقد/التلف) للشركة المؤجرة فوراً أو خصماً من "
          "مستحقاته.\nتغيير الحالة: لا يحق للطرف المستلم إجراء أي تعديل أو لحام أو قص في المكونات المعدنية، ويعتبر أي تعديل مشوه "
          "للقطعة تلفاً كاملاً يستوجب التعويض المالي الكامل."}
DEFAULT_CONTRACT_TERMS = {
    "en": "Rent is billed monthly on the quantity on site, counted by day (a month = 30 days) from the delivery date up to "
          "and including the return date.\nOne-time charges (installation, dismantling, transport, installation materials) "
          "are billed as stated.\nInvoices are payable on receipt.\nThe client provides safe access and a firm, level "
          "base, and may not move, alter or sub-let the equipment.",
    "ar": "يُحاسب الإيجار شهرياً على الكمية الموجودة بالموقع باليوم (الشهر = 30 يوماً) من تاريخ التسليم حتى تاريخ الإرجاع شاملاً.\n"
          "تُحاسب المبالغ لمرة واحدة (التركيب والفك والنقل ومواد التركيب) كما هو موضح.\nتُسدد الفواتير فور استلامها.\n"
          "يلتزم المستأجر بتوفير وصول آمن وأرضية صلبة مستوية، ولا يحق له نقل المعدات أو تعديلها أو تأجيرها للغير."}
DEFAULT_CHECKLISTS = {
    "scaffolding": [
        ["The ground is firm and carries the scaffold loads without settling.", "الأرضية صلبة وتتحمل أحمال السقالة دون هبوط."],
        ["Base jacks / plates sit straight and level on the ground.", "القواعد المعدنية (الفتيل) مرتكزة باستقامة على الأرضية."],
        ["Uprights are plumb and tied together with braces for stability.", "الأعمدة الرأسية مستقيمة تماماً ومربوطة بالمقصات لضمان الاتزان."],
        ["All clamps and couplers are fitted and fully tightened.", "جميع الكلبسات والملازم مربطة ومحكمة الإغلاق بشكل كامل."],
        ["Walking boards are secured against slipping.", "ألواح المشي الخشبية مؤمنة بشمبر لمنع انزلاقها."],
        ["The scaffold is tied to the building walls to prevent overturning.", "السقالة مثبتة ومربوطة بحوائط المبنى بشكل آمن لمنع الانكفاء."]],
    "elevator": [
        ["Foundation / base is level and built to the manufacturer's drawing.", "القاعدة الخرسانية مستوية ومطابقة لرسومات الشركة المصنعة."],
        ["Mast sections are bolted and tied to the building at the specified spacing.", "قطاعات البرج مربوطة ومثبتة بالمبنى على المسافات المحددة."],
        ["Upper / lower limit switches tested and working.", "مفاتيح النهايات العلوية والسفلية مختبرة وتعمل."],
        ["Overload device and safety (anti-fall) device tested and in date.", "جهاز منع الحمل الزائد وجهاز الأمان ضد السقوط مختبران وساريان."],
        ["Landing gates and door interlocks working.", "أبواب الأدوار وأقفالها التعشيقية تعمل."],
        ["Power supply earthed; emergency stop working.", "التغذية الكهربائية مؤرضة وزر الطوارئ يعمل."],
        ["Rated load sign displayed; operator trained.", "لوحة الحمولة المقررة معلقة والمشغل مدرب."]],
    "cradle_manual": [
        ["Roof suspension (beams / counterweights) set and secured as specified.", "نظام التعليق بالسطح (الكوابيل / الأثقال) مركب ومؤمن حسب المواصفات."],
        ["Wire ropes in good condition and correctly reeved.", "الحبال الصلب بحالة جيدة ومركبة بشكل صحيح."],
        ["Manual winches and brakes working.", "الونشات اليدوية والفرامل تعمل."],
        ["Independent safety line, fall arrester and harness anchorage provided.", "حبل أمان مستقل وجهاز منع السقوط ونقطة تثبيت الحزام متوفرة."],
        ["Platform guardrails and toe boards fitted.", "حواجز وقوائم الحماية بالسلة مركبة."],
        ["Rated load sign displayed.", "لوحة الحمولة المقررة معلقة."]],
    "cradle_motorized": [
        ["Roof suspension (beams / counterweights) set and secured as specified.", "نظام التعليق بالسطح (الكوابيل / الأثقال) مركب ومؤمن حسب المواصفات."],
        ["Wire ropes in good condition and correctly reeved.", "الحبال الصلب بحالة جيدة ومركبة بشكل صحيح."],
        ["Hoists, brakes and secondary (safety) locks tested.", "الموتورات والفرامل وأقفال الأمان الثانوية مختبرة."],
        ["Upper limit switch, overload device and emergency stop working.", "مفتاح النهاية العلوي وجهاز الحمل الزائد وزر الطوارئ تعمل."],
        ["Power supply earthed; control pendant and cables sound.", "التغذية الكهربائية مؤرضة ولوحة التحكم والكابلات سليمة."],
        ["Manual emergency descent tested.", "النزول اليدوي في حالات الطوارئ مختبر."],
        ["Independent safety line, fall arrester and harness anchorage provided.", "حبل أمان مستقل وجهاز منع السقوط ونقطة تثبيت الحزام متوفرة."],
        ["Platform guardrails and toe boards fitted; rated load sign displayed.", "حواجز الحماية مركبة ولوحة الحمولة المقررة معلقة."]],
    "other": [["Equipment inspected and in safe working order.", "تم فحص المعدات وهي بحالة تشغيل آمنة."]],
}


def settings(db):
    return {"terms": _j(db.setting("rental_terms"), {}) or DEFAULT_TERMS,
            "contract_terms": _j(db.setting("rental_contract_terms"), {}) or DEFAULT_CONTRACT_TERMS,
            "checklists": {**DEFAULT_CHECKLISTS, **_j(db.setting("rental_checklists"), {})},
            "loss_markup": float(db.setting("rental_loss_markup", "0.15") or 0.15),
            "day_basis": int(float(db.setting("rental_day_basis", "0") or 0))}   # 0 = calendar month, else N-day month


def save_settings(db, d):
    with db.tx():
        for key, name in (("terms", "rental_terms"), ("contract_terms", "rental_contract_terms")):
            if key in d:
                v = d[key] if isinstance(d[key], dict) else {}
                db.set_setting(name, json.dumps({"en": text(v.get("en"), maxlen=8000), "ar": text(v.get("ar"), maxlen=8000)},
                                                ensure_ascii=False))
        if "checklists" in d:
            out = {}
            for cat, rows in (d["checklists"] or {}).items():
                if cat in CATEGORIES and isinstance(rows, list):
                    out[cat] = [[text(r[0], maxlen=400), text(r[1] if len(r) > 1 else "", maxlen=400)]
                                for r in rows if isinstance(r, (list, tuple)) and r and text(r[0])]
            db.set_setting("rental_checklists", json.dumps(out, ensure_ascii=False))
        if "loss_markup" in d:
            db.set_setting("rental_loss_markup", str(max(0.0, min(5.0, num(d["loss_markup"], "loss markup") / 100))))
        if "day_basis" in d:
            b = int(num(d["day_basis"], "days in a rent month"))
            if b not in (0, 28, 29, 30, 31):
                raise ValidationError("A rent month is the calendar month (0) or 28-31 days")
            db.set_setting("rental_day_basis", str(b))
    return settings(db)


# ================================================================ equipment
def _item_row(db, it, stock=None):
    st = stock if stock is not None else item_stock(db, it["id"])
    return {**it, "residual_pct": round((it["residual_pct"] or 0) * 100, 4), "monthly_rate": to_major(it["monthly_rate"]), "std_cost": to_major(it["std_cost"]),
            "loss_price_set": to_major(it["loss_price"]), "loss_price": to_major(loss_price(db, it)), **st,
            # pricing yardsticks: rent as a share of what the piece cost, and how many months of rent buy it back
            "rent_pct": round(to_major(it["monthly_rate"]) / (st["avg_cost"] or to_major(it["std_cost"])), 4) if (st["avg_cost"] or it["std_cost"]) else 0,
            "payback_months": round((st["avg_cost"] or to_major(it["std_cost"])) / to_major(it["monthly_rate"]), 1) if it["monthly_rate"] else 0}


def get_item(db, iid):
    it = db.one("SELECT * FROM rent_items WHERE id=? OR code=?", (iid if str(iid).isdigit() else -1, text(iid)))
    if not it:
        raise ValidationError("Equipment item not found")
    return it


def _lot_states(db, item_id=None, lot_id=None):
    """Per lot: qty bought, in the yard, out with clients, gone (lost / damaged / scrapped)."""
    w, p = [], []
    if item_id:
        w.append("l.item_id=?")
        p.append(item_id)
    if lot_id:
        w.append("l.id=?")
        p.append(lot_id)
    rows = db.q("SELECT l.*, "
                "COALESCE((SELECT SUM(CASE WHEN kind='out' THEN qty ELSE 0 END) FROM rent_moves m WHERE m.lot_id=l.id),0) AS m_out, "
                "COALESCE((SELECT SUM(CASE WHEN kind='back' THEN qty ELSE 0 END) FROM rent_moves m WHERE m.lot_id=l.id),0) AS m_back, "
                "COALESCE((SELECT SUM(CASE WHEN kind IN ('lost','damaged') THEN qty ELSE 0 END) FROM rent_moves m WHERE m.lot_id=l.id),0) AS m_lost, "
                "COALESCE((SELECT SUM(CASE WHEN kind='scrap' THEN qty ELSE 0 END) FROM rent_moves m WHERE m.lot_id=l.id),0) AS m_scrap "
                "FROM rent_lots l" + (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY l.date, l.id", p)
    dep = lot_depreciation(db)
    for r in rows:
        r["with_clients"] = r["m_out"] - r["m_back"] - r["m_lost"]
        r["in_yard"] = r["qty"] - r["m_out"] + r["m_back"] - r["m_scrap"]
        r["gone"] = r["m_lost"] + r["m_scrap"]
        r["owned"] = r["qty"] - r["gone"]
        d = dep.get(r["code"], {})
        r["accum"] = r["opening_accum"] + d.get("booked", 0) - d.get("out", 0)
        r["depr_through"] = d.get("through") or ""
    return rows


# ---------------------------------------------------------------- depreciation of the rental equipment
def _month(v, field="month"):
    v = text(v)[:7]
    if len(v) != 7 or v[4] != "-" or not v[:4].isdigit() or not v[5:].isdigit() or not 1 <= int(v[5:]) <= 12:
        raise ValidationError(f"{field} must be YYYY-MM")
    return v


def _add_months_m(m, n):
    y, mo = int(m[:4]), int(m[5:]) + n
    y, mo = y + (mo - 1) // 12, (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"


def _month_end(m):
    y, mo = int(m[:4]), int(m[5:])
    return (_dt.date(y + (mo == 12), mo % 12 + 1, 1) - _dt.timedelta(days=1)).isoformat()


def lot_depreciation(db):
    """{lot code: {booked, out, through}} from the books: depreciation runs, and the share cleared when pieces
    are lost, damaged or scrapped. A voided run or write-off simply stops counting."""
    out = {}
    for e in db.q(f"SELECT e.meta FROM journal_entries e WHERE {LIVE_REF} AND e.kind IN (?,?)", (KINDS[2], KINDS[1])):
        m = _j(e["meta"], {})
        for code, (frm, to, amt) in (m.get("rent_dep") or {}).items():
            d = out.setdefault(code, {"booked": 0, "out": 0, "through": ""})
            d["booked"] += int(amt)
            d["through"] = max(d["through"], to)
        for code, amt in (m.get("accum_out") or {}).items():
            out.setdefault(code, {"booked": 0, "out": 0, "through": ""})["out"] += int(amt)
    return out


def plan_depreciation(db, through):
    """Per lot, month by month, by the company's depreciation policy (the rule set on the rental-fleet account, else the
    company default): straight line spreads (pieces still owned × cost × (1 − residual %)) over the useful life;
    declining balance front-loads it. Catches up any months not booked yet and never goes below the residual value."""
    from . import assets
    through = _month(through, "depreciate up to")
    rule = assets.account_rule(db, _role_or(db, "rental_fleet"))
    method, factor = rule["method"], float(rule["factor"] or 2)
    rows = []
    items = {i["id"]: i for i in db.q("SELECT * FROM rent_items")}
    for lot in _lot_states(db):
        it = items.get(lot["item_id"])
        if not it or not it["life_months"] or lot["owned"] <= EPS or not lot["unit_cost"]:
            continue
        start = lot["depr_start"] or lot["date"][:7]
        nxt = _add_months_m(lot["depr_through"], 1) if lot["depr_through"] else start
        if nxt > through:
            continue
        end = _add_months_m(start, it["life_months"] - 1)
        last = min(through, end) if end >= nxt else nxt  # past its life: book what is left in one go
        months = (int(last[:4]) - int(nxt[:4])) * 12 + int(last[5:]) - int(nxt[5:]) + 1
        left = max((int(end[:4]) - int(nxt[:4])) * 12 + int(end[5:]) - int(nxt[5:]) + 1, 1)
        cost = int(round(lot["owned"] * lot["unit_cost"]))
        salvage = int(round(cost * (it["residual_pct"] or 0)))
        amount = sum(assets.month_amounts(cost, salvage, int(round(lot["accum"])), left, months, method, factor,
                                          it["life_months"]))
        if amount <= 0:
            continue
        rows.append({"lot": lot["code"], "item_id": it["id"], "code": it["code"], "name": it["name"], "name_ar": it["name_ar"],
                     "from": nxt, "to": last, "months": months, "amount": amount})
    by_item = {}
    for r in rows:
        g = by_item.setdefault(r["item_id"], {"code": r["code"], "name": r["name"], "name_ar": r["name_ar"], "lots": 0, "amount": 0})
        g["lots"] += 1
        g["amount"] += r["amount"]
    return {"through": through, "rows": rows, "items": [{**g, "amount": to_major(g["amount"])} for g in by_item.values()],
            "total": to_major(sum(r["amount"] for r in rows))}


def run_depreciation(db, through, user=""):
    plan = plan_depreciation(db, through)
    if not plan["rows"]:
        raise ValidationError(f"Nothing to depreciate up to {plan['through']}")
    total = sum(r["amount"] for r in plan["rows"])
    with db.tx():
        return acc._insert_entry(db, date=_month_end(plan["through"]), kind=KINDS[2], memo=f"Depreciation of rental equipment to {plan['through']}",
                                 ref=f"RENT:DEP:{plan['through']}",
                                 lines=[{"account": _role_or(db, "depreciation"), "debit": to_major(total)},
                                        {"account": _role_or(db, "rental_accum"), "credit": to_major(total)}],
                                 meta={"rent_dep": {r["lot"]: [r["from"], r["to"], r["amount"]] for r in plan["rows"]}}, user=user)


def _write_off_lines(db, taken):
    """Pieces leaving for good [(lot, qty)]: their cost leaves the fleet, their share of the depreciation booked is
    cleared, the rest is the loss. -> (lines, meta accum_out, cost)."""
    cost, share = 0, {}
    left = {}
    for lot, q in taken:
        owned = left.get(lot["id"], lot["owned"])
        acc_now = left.get(("a", lot["id"]), lot["accum"])
        part = int(round(acc_now * q / owned)) if owned > EPS else 0
        share[lot["code"]] = share.get(lot["code"], 0) + part
        left[lot["id"]], left[("a", lot["id"])] = owned - q, acc_now - part
        cost += q * lot["unit_cost"]
    cost = int(round(cost))
    cleared = sum(share.values())
    lines = []
    if cleared:
        lines.append({"account": _role_or(db, "rental_accum"), "debit": to_major(cleared)})
    if cost - cleared:
        lines.append({"account": _role_or(db, "rental_loss"), "debit": to_major(cost - cleared)})
    lines.append({"account": _role_or(db, "rental_fleet"), "credit": to_major(cost)})
    return lines, {k: v for k, v in share.items() if v}, cost


def item_stock(db, item_id):
    lots = _lot_states(db, item_id)
    yard = sum(r["in_yard"] for r in lots)
    out = sum(r["with_clients"] for r in lots)
    value = sum(r["owned"] * r["unit_cost"] for r in lots)
    accum = sum(r["accum"] for r in lots)
    return {"accum": to_major(accum), "book_value": to_major(int(round(value - accum))),
            "depr_through": min((r["depr_through"] for r in lots if r["owned"] > EPS), default="") ,
            "in_yard": round(yard, 4), "on_rent": round(out, 4), "owned": round(yard + out, 4),
            "gone": round(sum(r["gone"] for r in lots), 4), "value": to_major(int(round(value))),
            "avg_cost": to_major(int(round(value / (yard + out)))) if yard + out > EPS else 0,
            "last_cost": to_major(lots[-1]["unit_cost"]) if lots else 0,
            "utilisation": round(out / (yard + out), 4) if yard + out > EPS else 0}


def unit_cost_next(db, item_id, qty=1):
    """Cost of the next pieces to go out (FIFO from the yard), else the latest purchase, else the reference cost."""
    need, cost, got = max(qty, EPS), 0.0, 0.0
    for r in _lot_states(db, item_id):
        if r["in_yard"] <= EPS:
            continue
        take = min(r["in_yard"], need - got)
        cost += take * r["unit_cost"]
        got += take
        if got >= need - EPS:
            break
    if got > EPS:
        return cost / got
    last = db.one("SELECT unit_cost FROM rent_lots WHERE item_id=? ORDER BY date DESC, id DESC LIMIT 1", (item_id,))
    if last:
        return float(last["unit_cost"])
    it = db.one("SELECT std_cost FROM rent_items WHERE id=?", (item_id,))
    return float(it["std_cost"] if it else 0)


def loss_price(db, it):
    """Loss / damage price per piece: set on the item, else latest cost × (1 + loss markup)."""
    if it["loss_price"]:
        return it["loss_price"]
    last = db.one("SELECT unit_cost FROM rent_lots WHERE item_id=? ORDER BY date DESC, id DESC LIMIT 1", (it["id"],))
    base = last["unit_cost"] if last else it["std_cost"]
    return int(round(base * (1 + settings(db)["loss_markup"])))


def list_items(db, q=None, category=None, active_only=False):
    w, p = ["1=1"], []
    if q:
        w.append("(code LIKE ? OR name LIKE ? OR name_ar LIKE ?)")
        p += [f"%{q}%"] * 3
    if category in CATEGORIES:
        w.append("category=?")
        p.append(category)
    if active_only:
        w.append("active=1")
    items = [_item_row(db, it) for it in db.q(f"SELECT * FROM rent_items WHERE {' AND '.join(w)} ORDER BY category, code", p)]
    kits = [kit_row(db, k) for k in db.q("SELECT * FROM rent_kits ORDER BY code")]
    tot = {"value": round(sum(i["value"] for i in items), 2), "accum": round(sum(i["accum"] for i in items), 2),
           "book_value": round(sum(i["book_value"] for i in items), 2),
           "on_rent_value": round(sum(i["on_rent"] * i["avg_cost"] for i in items), 2)}
    return {"items": items, "kits": kits, "totals": tot, "next_code": _code(db, "rent_items", "EQ-"),
            "next_kit": _code(db, "rent_kits", "KIT-", 3), "settings": settings(db)}


def save_item(db, d, iid=None):
    old = get_item(db, iid) if iid else None
    code = text(d.get("code"), maxlen=30) or (old["code"] if old else _code(db, "rent_items", "EQ-"))
    cat = d.get("category") or (old and old["category"]) or "scaffolding"
    if cat not in CATEGORIES:
        raise ValidationError("Unknown equipment category")
    vals = (code, text(d.get("name"), "name", required=True), text(d.get("name_ar")), cat, text(d.get("unit")) or "piece",
            to_minor(num(d.get("monthly_rate"), "monthly rent")), to_minor(num(d.get("std_cost"), "reference cost")),
            to_minor(num(d.get("loss_price"), "loss price")), num(d.get("weight"), "weight"),
            0 if d.get("active") in (False, 0, "0", "false") else 1, text(d.get("notes"), maxlen=2000),
            int(num(d.get("life_months") if d.get("life_months") not in (None, "") else (old["life_months"] if old else 120), "useful life (months)")),
            min(max(num(d.get("residual_pct") if d.get("residual_pct") not in (None, "") else
                        ((old["residual_pct"] * 100) if old else 10), "residual value %") / 100, 0), 1))
    if vals[-2] < 0 or vals[-2] > 1200:
        raise ValidationError("Useful life must be 0 (not depreciated) to 1200 months")
    with db.tx():
        dup = db.one("SELECT id FROM rent_items WHERE code=?", (code,))
        if dup and (not old or dup["id"] != old["id"]):
            raise ValidationError(f"Item code {code} is already used")
        if old:
            db.conn.execute("UPDATE rent_items SET code=?, name=?, name_ar=?, category=?, unit=?, monthly_rate=?, std_cost=?, "
                            "loss_price=?, weight=?, active=?, notes=?, life_months=?, residual_pct=? WHERE id=?", vals + (old["id"],))
            return old["id"]
        return db.conn.execute("INSERT INTO rent_items(code,name,name_ar,category,unit,monthly_rate,std_cost,loss_price,weight,"
                               "active,notes,life_months,residual_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", vals).lastrowid


def delete_item(db, iid):
    it = get_item(db, iid)
    if db.one("SELECT 1 FROM rent_lots WHERE item_id=?", (it["id"],)) or \
            db.one("SELECT 1 FROM rent_kit_lines WHERE item_id=?", (it["id"],)):
        raise ValidationError("This item has stock or is part of a frame / kit — deactivate it instead")
    for r in db.q("SELECT components FROM rental_lines"):
        if any(str(c.get("item_id")) == str(it["id"]) for c in _j(r["components"], [])):
            raise ValidationError("This item is used on a rental — deactivate it instead")
    db.x("DELETE FROM rent_items WHERE id=?", (it["id"],))


def item_detail(db, iid):
    it = get_item(db, iid)
    lots = _lot_states(db, it["id"])
    moves = db.q("SELECT m.*, l.code AS lot_code, r.code AS rental_code, r.client FROM rent_moves m JOIN rent_lots l ON l.id=m.lot_id "
                 "LEFT JOIN rentals r ON r.id=m.rental_id WHERE m.item_id=? ORDER BY m.date DESC, m.id DESC LIMIT 300", (it["id"],))
    on_rent = db.q("SELECT r.id, r.code, r.client, r.site, SUM(CASE WHEN m.kind='out' THEN m.qty ELSE -m.qty END) AS held "
                   "FROM rent_moves m JOIN rentals r ON r.id=m.rental_id WHERE m.item_id=? AND m.kind IN ('out','back','lost','damaged') "
                   "GROUP BY r.id HAVING held>0.0000001 ORDER BY r.code", (it["id"],))
    for x in on_rent:
        x["qty"] = x.pop("held")
    return {"item": _item_row(db, it), "lots": [{**l, "unit_cost": to_major(l["unit_cost"]), "accum": to_major(l["accum"]),
                                                  "opening_accum": to_major(l["opening_accum"]),
                                                  "value": to_major(int(round(l["owned"] * l["unit_cost"]))),
                                                  "book_value": to_major(int(round(l["owned"] * l["unit_cost"] - l["accum"])))} for l in lots],
            "moves": moves, "on_rent": on_rent}


def add_lot(db, iid, d, user=""):
    """Stock in: a purchase (booked to the rental fleet account) or stock already owned (opening, nothing posted)."""
    it = get_item(db, iid)
    qty = num(d.get("qty"), "quantity", allow_zero=False)
    if qty < 0:
        raise ValidationError("Quantity must be positive")
    unit_cost = to_minor(num(d.get("unit_cost"), "unit cost"))
    date = acc.clean_date(d.get("date") or acc.today())
    source = d.get("source") or "purchase"
    if source not in ("purchase", "opening"):
        raise ValidationError("Unknown stock source")
    with db.tx():
        code = _code(db, "rent_lots", "LOT-", 5)
        opening_accum = to_minor(num(d.get("opening_accum"), "depreciation already taken")) if source == "opening" else 0
        if opening_accum > qty * unit_cost:
            raise ValidationError("Depreciation already taken cannot be more than the cost")
        start = _month(d.get("depr_start"), "depreciation starts") if d.get("depr_start") else ""
        lid = db.conn.execute("INSERT INTO rent_lots(code,item_id,date,qty,unit_cost,supplier,source,notes,opening_accum,depr_start) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (code, it["id"], date, qty, unit_cost, text(d.get("supplier")), source,
                               text(d.get("notes"), maxlen=1000), opening_accum, start)).lastrowid
        if source == "purchase" and unit_cost:
            fleet = acc.account_code(db, d.get("account") or _role_or(db, "rental_fleet"), types=("Asset",), field="equipment account")
            net = int(round(qty * unit_cost))
            vat_code, vat, wht_code, wht = acc._purchase_taxes(db, d, net)
            due = net + vat - wht
            paid = due if d.get("paid") in (None, "") else to_minor(num(d.get("paid"), "paid now"))
            supplier = text(d.get("supplier"))
            if (paid < due or wht) and not supplier:
                raise ValidationError("Name the supplier so the unpaid balance / withholding can be tracked")
            payable = acc.account_code(db, d.get("payable_account") or db.acct("ap"), types=("Liability",))
            lines = acc._purchase_lines(db, d, fleet, net, vat, wht, payable, paid)
            acc._insert_entry(db, date=date, kind=KINDS[0], memo=f"{it['name']} × {qty:g} ({code})", party=supplier,
                              ref=f"RENT:LOT:{code}", lines=lines,
                              meta={"rent_item": it["code"], "lot": code, "qty": qty, "vat_code": vat_code, "wht_code": wht_code},
                              user=user)
        return lid


def delete_lot(db, lot_id, user=""):
    lot = db.one("SELECT * FROM rent_lots WHERE id=?", (lot_id,))
    if not lot:
        raise ValidationError("Stock lot not found")
    if db.one("SELECT 1 FROM rent_moves WHERE lot_id=?", (lot_id,)):
        raise ValidationError("Pieces from this lot have been rented or scrapped — it cannot be removed")
    if lot_depreciation(db).get(lot["code"], {}).get("booked"):
        raise ValidationError("Depreciation is booked on this lot — void that depreciation run first")
    with db.tx():
        for e in db.q(f"SELECT id FROM journal_entries e WHERE {acc.LIVE} AND e.ref=?", (f"RENT:LOT:{lot['code']}",)):
            acc.void_entry(db, e["id"], "Stock lot removed", user)
        db.conn.execute("DELETE FROM rent_lots WHERE id=?", (lot_id,))


def scrap(db, d, user=""):
    """Pieces in the yard that are scrapped / written off: taken FIFO, their cost leaves the fleet account."""
    it = get_item(db, d.get("item_id") or d.get("item"))
    qty = num(d.get("qty"), "quantity", allow_zero=False)
    date = acc.clean_date(d.get("date") or acc.today())
    with db.tx():
        taken = _fifo_take(db, it["id"], qty, where="yard")
        for lot, take in taken:
            db.conn.execute("INSERT INTO rent_moves(date,item_id,lot_id,qty,kind,note) VALUES (?,?,?,?,?,?)",
                            (date, it["id"], lot["id"], take, "scrap", text(d.get("reason"), maxlen=300)))
        lines, cleared, cost = _write_off_lines(db, taken)
        if cost:
            acc._insert_entry(db, date=date, kind=KINDS[1], memo=f"Scrapped: {it['name']} × {qty:g}" +
                              (f" — {text(d.get('reason'))}" if d.get("reason") else ""), ref=f"RENT:SCRAP:{it['code']}",
                              lines=lines, meta={"rent_item": it["code"], "qty": qty, "accum_out": cleared}, user=user)
    return {"cost": to_major(cost)}


def _fifo_take(db, item_id, qty, where="yard", rental_id=None):
    """[(lot, qty)] oldest first — from the yard, or from what a rental holds."""
    if where == "yard":
        avail = [(r, r["in_yard"]) for r in _lot_states(db, item_id)]
    else:
        held = {r["lot_id"]: r["qty"] for r in _held(db, rental_id, item_id)}
        avail = [(r, held.get(r["id"], 0)) for r in _lot_states(db, item_id)]
    out, need = [], qty
    for lot, have in avail:
        if have <= EPS or need <= EPS:
            continue
        take = min(have, need)
        out.append((lot, take))
        need -= take
    if need > 1e-6:
        it = db.one("SELECT name FROM rent_items WHERE id=?", (item_id,))
        have = qty - need
        raise ValidationError(f"{it['name'] if it else 'Item'}: only {have:g} " +
                              ("in the yard" if where == "yard" else "on this rental") + f", {qty:g} needed")
    return out


def _held(db, rental_id, item_id=None):
    w, p = ["rental_id=?", "kind IN ('out','back','lost','damaged')"], [rental_id]
    if item_id:
        w.append("item_id=?")
        p.append(item_id)
    rows = db.q(f"SELECT item_id, lot_id, SUM(CASE WHEN kind='out' THEN qty ELSE -qty END) AS held FROM rent_moves "
                f"WHERE {' AND '.join(w)} GROUP BY item_id, lot_id HAVING held>0.0000001", p)
    return [{"item_id": r["item_id"], "lot_id": r["lot_id"], "qty": r["held"]} for r in rows]


# ---------------------------------------------------------------- frames / kits
def kit_row(db, k):
    lines = db.q("SELECT kl.item_id, kl.qty, i.code, i.name, i.name_ar, i.unit FROM rent_kit_lines kl JOIN rent_items i "
                 "ON i.id=kl.item_id WHERE kl.kit_id=? ORDER BY kl.id", (k["id"],))
    return {**k, "monthly_rate": to_major(k["monthly_rate"]), "lines": lines}


def save_kit(db, d, kid=None):
    old = db.one("SELECT * FROM rent_kits WHERE id=?", (kid,)) if kid else None
    if kid and not old:
        raise ValidationError("Frame / kit not found")
    code = text(d.get("code"), maxlen=30) or (old["code"] if old else _code(db, "rent_kits", "KIT-", 3))
    lines = []
    for r in d.get("lines") or []:
        if not isinstance(r, dict) or r.get("qty") in (None, "", 0, "0"):
            continue
        lines.append((get_item(db, r.get("item_id") or r.get("item"))["id"], num(r.get("qty"), "quantity", allow_zero=False)))
    if not lines:
        raise ValidationError("Add the pieces that make up one frame / set")
    vals = (code, text(d.get("name"), "name", required=True), text(d.get("name_ar")), text(d.get("unit")) or "frame",
            to_minor(num(d.get("monthly_rate"), "monthly rent")), text(d.get("notes"), maxlen=2000))
    with db.tx():
        dup = db.one("SELECT id FROM rent_kits WHERE code=?", (code,))
        if dup and (not old or dup["id"] != old["id"]):
            raise ValidationError(f"Code {code} is already used")
        if old:
            db.conn.execute("UPDATE rent_kits SET code=?, name=?, name_ar=?, unit=?, monthly_rate=?, notes=? WHERE id=?", vals + (old["id"],))
            kid = old["id"]
            db.conn.execute("DELETE FROM rent_kit_lines WHERE kit_id=?", (kid,))
        else:
            kid = db.conn.execute("INSERT INTO rent_kits(code,name,name_ar,unit,monthly_rate,notes) VALUES (?,?,?,?,?,?)", vals).lastrowid
        db.conn.executemany("INSERT INTO rent_kit_lines(kit_id,item_id,qty) VALUES (?,?,?)", [(kid, i, q) for i, q in lines])
    return kid


def delete_kit(db, kid):
    db.x("DELETE FROM rent_kits WHERE id=?", (int(kid),))


# ================================================================ calculator (scaffold model)
_FUNCS = {"ceil": math.ceil, "floor": math.floor, "round": round, "max": max, "min": min, "abs": abs, "sqrt": math.sqrt}
_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b}
_CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
        ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def safe_eval(expr, names):
    """Arithmetic only: numbers, the model's names, + - * / // % **, comparisons, 'a if cond else b',
    ceil floor round max min abs sqrt."""
    src = str(expr if expr is not None else "").strip() or "0"
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise ValidationError(f"Formula not understood: {src}")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in names:
                return names[n.id]
            raise ValidationError(f"Unknown name '{n.id}' in: {src}")
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow):
                if abs(b) > 10:
                    raise ValidationError("Powers above 10 are not allowed")
                return a ** b
            fn = _BIN.get(type(n.op))
            if not fn:
                raise ValidationError(f"Operator not allowed in: {src}")
            try:
                return fn(a, b)
            except ZeroDivisionError:
                raise ValidationError(f"Division by zero in: {src}")
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return not ev(n.operand)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            args = [ev(a) for a in n.args]
            if not args:
                raise ValidationError(f"{n.func.id}() needs a value")
            return _FUNCS[n.func.id](*args)
        if isinstance(n, ast.IfExp):
            return ev(n.body) if ev(n.test) else ev(n.orelse)
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for op, right in zip(n.ops, n.comparators):
                r = ev(right)
                if not _CMP[type(op)](left, r):
                    return False
                left = r
            return True
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        raise ValidationError(f"Not allowed in a formula: {src}")
    v = ev(tree)
    if isinstance(v, bool):
        v = 1 if v else 0
    if not isinstance(v, (int, float)) or v != v or abs(v) > 1e12:
        raise ValidationError(f"Formula result out of range: {src}")
    return v



_XL_FUNCS = {"ceil": "ROUNDUP({},0)", "floor": "INT({})", "abs": "ABS({})", "sqrt": "SQRT({})"}
_XL_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "^"}
_XL_CMP = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "=", ast.NotEq: "<>"}


def to_excel(expr, refs):
    """The same formula as an Excel expression (no leading '='); refs maps each name to a cell such as $C$5."""
    src = str(expr if expr is not None else "").strip() or "0"
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise ValidationError(f"Formula not understood: {src}")

    def xl(n):
        if isinstance(n, ast.Expression):
            return xl(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return repr(n.value)
        if isinstance(n, ast.Name):
            if n.id not in refs:
                raise ValidationError(f"Unknown name '{n.id}' in: {src}")
            return refs[n.id]
        if isinstance(n, ast.BinOp):
            a, b = xl(n.left), xl(n.right)
            if isinstance(n.op, ast.FloorDiv):
                return f"INT(({a})/({b}))"
            if isinstance(n.op, ast.Mod):
                return f"MOD({a},{b})"
            if type(n.op) not in _XL_OPS:
                raise ValidationError(f"Operator not allowed in: {src}")
            return f"({a}{_XL_OPS[type(n.op)]}{b})"
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return f"(-{xl(n.operand)})" if isinstance(n.op, ast.USub) else xl(n.operand)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return f"NOT({xl(n.operand)})"
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords and n.args:
            args = [xl(a) for a in n.args]
            f = n.func.id
            if f in ("max", "min"):
                return f"{f.upper()}({','.join(args)})"
            if f == "round":
                return f"ROUND({args[0]},{args[1] if len(args) > 1 else 0})"
            return _XL_FUNCS[f].format(args[0])
        if isinstance(n, ast.IfExp):
            return f"IF({xl(n.test)},{xl(n.body)},{xl(n.orelse)})"
        if isinstance(n, ast.Compare):
            parts, left = [], xl(n.left)
            for op, right in zip(n.ops, n.comparators):
                r = xl(right)
                parts.append(f"{left}{_XL_CMP[type(op)]}{r}")
                left = r
            return parts[0] if len(parts) == 1 else f"AND({','.join(parts)})"
        if isinstance(n, ast.BoolOp):
            return f"{'AND' if isinstance(n.op, ast.And) else 'OR'}({','.join(xl(v) for v in n.values)})"
        raise ValidationError(f"Not allowed in a formula: {src}")
    return xl(tree)

FRAME_MODEL = {
    "inputs": [{"key": "length", "label": "Facade length", "label_ar": "طول الواجهة", "unit": "m", "value": 72},
               {"key": "height", "label": "Scaffold height", "label_ar": "ارتفاع السقالة", "unit": "m", "value": 16}],
    "params": [
        {"key": "frame_height", "label": "Frame height (one lift)", "label_ar": "ارتفاع الشباك (دور واحد)", "unit": "m", "value": 2},
        {"key": "bay", "label": "Bay length (brace span)", "label_ar": "طول الباكية (بحر المقص)", "unit": "m", "value": 3},
        {"key": "braces_per_bay", "label": "Braces per bay per lift", "label_ar": "مقصات لكل باكية في كل دور", "unit": "", "value": 1},
        {"key": "board_levels", "label": "Boarded levels (0 = every lift)", "label_ar": "أدوار الألواح (0 = كل الأدوار)", "unit": "", "value": 1},
        {"key": "boards_per_bay", "label": "Boards per bay per boarded level", "label_ar": "ألواح لكل باكية في كل دور ألواح", "unit": "", "value": 1},
        {"key": "tie_v", "label": "Wall ties every (vertical)", "label_ar": "ربط بالحائط كل (رأسياً)", "unit": "m", "value": 4},
        {"key": "tie_h", "label": "Wall ties every (horizontal)", "label_ar": "ربط بالحائط كل (أفقياً)", "unit": "m", "value": 6},
        {"key": "clamps_per_tie", "label": "Fixed clamps per tie", "label_ar": "كلبس ثابت لكل ربطة", "unit": "", "value": 1},
        {"key": "tubes_per_tie", "label": "Tubes per tie", "label_ar": "مواسير لكل ربطة", "unit": "", "value": 0},
        {"key": "extra_tubes", "label": "Extra tubes (rails, bracing)", "label_ar": "مواسير إضافية (درابزين، تدعيم)", "unit": "", "value": 5},
        {"key": "tall_over", "label": "Extra bracing above height", "label_ar": "تدعيم إضافي فوق ارتفاع", "unit": "m", "value": 20},
        {"key": "tall_brace_pct", "label": "Extra bracing when taller", "label_ar": "نسبة التدعيم الإضافي", "unit": "%", "value": 25},
        {"key": "spare_pct", "label": "Spare / waste", "label_ar": "احتياطي / فاقد", "unit": "%", "value": 0}],
    "derived": [
        {"key": "lifts", "expr": "ceil(height / frame_height)", "label": "Lifts", "label_ar": "عدد الأدوار"},
        {"key": "bays", "expr": "ceil(length / bay)", "label": "Bays", "label_ar": "عدد الباكيات"},
        {"key": "lines", "expr": "bays + 1", "label": "Frame lines", "label_ar": "صفوف الشبابيك"},
        {"key": "legs", "expr": "lines * 2", "label": "Legs on the ground", "label_ar": "الأرجل على الأرض"},
        {"key": "levels", "expr": "board_levels if board_levels > 0 else lifts", "label": "Boarded levels", "label_ar": "أدوار الألواح"},
        {"key": "ties", "expr": "ceil(height / tie_v) * (ceil(length / tie_h) + 1)", "label": "Wall ties", "label_ar": "نقاط الربط"},
        {"key": "brace_factor", "expr": "1 + tall_brace_pct / 100 if height > tall_over else 1", "label": "Bracing factor", "label_ar": "معامل التدعيم"},
        {"key": "spare", "expr": "1 + spare_pct / 100", "label": "Spare factor", "label_ar": "معامل الاحتياطي"},
        {"key": "area", "expr": "length * height", "label": "Area (m²)", "label_ar": "المساحة (م²)"}],
    "components": [
        {"item": "SC-FRM2", "expr": "ceil(lines * lifts * spare)"},
        {"item": "SC-BRC3", "expr": "ceil(bays * lifts * braces_per_bay * brace_factor * spare)"},
        {"item": "SC-PLT60", "expr": "ceil(legs * spare)"},
        {"item": "SC-JACK", "expr": "ceil(legs * spare)"},
        {"item": "SC-CLF", "expr": "ceil(ties * clamps_per_tie * spare)"},
        {"item": "SC-CLS", "expr": "ceil((ties * tubes_per_tie + extra_tubes) * 2 * spare)"},
        {"item": "SC-TUB6", "expr": "ceil((ties * tubes_per_tie + extra_tubes) * spare)"},
        {"item": "SC-BRD36", "expr": "ceil(bays * levels * boards_per_bay * spare)"}],
    "area": "area",
}
STARTER_ITEMS = [  # code, name, Arabic, category, unit, reference cost (from the handover form)
    ("SC-FRM2", "Frame 2 m", "شباك 2 متر", "scaffolding", "piece", 940),
    ("SC-BRC3", "Cross brace 3 m", "مقص 3 متر", "scaffolding", "piece", 340),
    ("SC-PLT60", "Base plate 60 cm", "بليتة 60 سم", "scaffolding", "piece", 150),
    ("SC-JACK", "Screw jack", "فتيل", "scaffolding", "piece", 220),
    ("SC-CLF", "Fixed clamp", "كلبس ثابت", "scaffolding", "piece", 110),
    ("SC-CLS", "Swivel clamp", "كلبس متحرك", "scaffolding", "piece", 110),
    ("SC-TUB6", "Tube 2.8 mm × 6 m", "ماسورة 2.8 مم 6 متر", "scaffolding", "piece", 750),
    ("SC-BRD36", "Walking board 3.6 m", "لوح بنط 3.6 متر", "scaffolding", "piece", 700),
    ("EL-HOIST", "Construction elevator (hoist)", "مصعد إنشائي (ونش)", "elevator", "unit", 0),
    ("CR-MAN", "Cradle platform — manual", "سقالة معلقة (كريدل) — يدوي", "cradle_manual", "unit", 0),
    ("CR-MOT", "Cradle platform — motorized", "سقالة معلقة (كريدل) — كهربائي", "cradle_motorized", "unit", 0),
]


def _spec(d):
    """Clean a model spec (inputs, params, derived values, components, area)."""
    def rows(key, fields):
        out = []
        for r in (d.get(key) or []):
            if not isinstance(r, dict):
                continue
            k = text(r.get("key"), maxlen=40)
            if key in ("inputs", "params", "derived") and (not k or not k.isidentifier() or k in _FUNCS):
                raise ValidationError(f"'{k}' is not a valid name (letters, digits and _, not starting with a digit)")
            out.append({f: (num(r.get(f), f) if f == "value" else text(r.get(f), maxlen=300)) for f in fields} | ({"key": k} if k else {}))
        return out
    comps = []
    for r in d.get("components") or []:
        if isinstance(r, dict) and text(r.get("item")):
            comps.append({"item": text(r.get("item"), maxlen=40), "expr": text(r.get("expr"), maxlen=300) or "0",
                          "note": text(r.get("note"), maxlen=200)})
    return {"inputs": rows("inputs", ("label", "label_ar", "unit", "value")),
            "params": rows("params", ("label", "label_ar", "unit", "value")),
            "derived": rows("derived", ("expr", "label", "label_ar")), "components": comps,
            "area": text(d.get("area"), maxlen=200) or "0"}


def evaluate(db, spec, values=None):
    """Run a model with typed values: every derived value and the quantity of every component."""
    spec = _spec(spec)
    values = values or {}
    names = {}
    for r in spec["inputs"] + spec["params"]:
        v = values.get(r["key"], r["value"])
        names[r["key"]] = num(v, r.get("label") or r["key"])
    derived = []
    for r in spec["derived"]:
        names[r["key"]] = safe_eval(r["expr"], names)
        derived.append({**r, "value": round(names[r["key"]], 4)})
    area = safe_eval(spec["area"], names)
    comps = []
    for r in spec["components"]:
        it = db.one("SELECT * FROM rent_items WHERE code=? OR id=?", (r["item"], r["item"] if str(r["item"]).isdigit() else -1))
        qty = max(safe_eval(r["expr"], names), 0)
        row = {"item": r["item"], "expr": r["expr"], "qty": round(qty, 4), "missing": not it}
        if it:
            st = item_stock(db, it["id"])
            cost = unit_cost_next(db, it["id"], qty or 1)
            row.update(item_id=it["id"], name=it["name"], name_ar=it["name_ar"], unit=it["unit"], in_yard=st["in_yard"],
                       short=round(max(qty - st["in_yard"], 0), 4), unit_cost=to_major(int(round(cost))),
                       value=to_major(int(round(cost * qty))), monthly_rate=to_major(it["monthly_rate"]),
                       piece_rent=to_major(int(round(it["monthly_rate"] * qty))), weight=round((it["weight"] or 0) * qty, 2))
        comps.append(row)
    value = round(sum(c.get("value", 0) for c in comps), 2)
    piece_rent = round(sum(c.get("piece_rent", 0) for c in comps), 2)
    return {"values": {k: names[k] for k in (r["key"] for r in spec["inputs"] + spec["params"])}, "derived": derived,
            "area": round(area, 4), "components": comps, "value": value, "piece_rent": piece_rent,
            "value_per_m2": round(value / area, 2) if area else 0, "piece_rent_per_m2": round(piece_rent / area, 2) if area else 0,
            "pieces": round(sum(c["qty"] for c in comps), 2), "weight": round(sum(c.get("weight", 0) for c in comps), 2)}


# ---------------------------------------------------------------- services priced with the rental
# Installation, dismantling and transport are services sold with the rent and expected to make their own profit.
# Each has a client price and our cost, on its own basis:
#   price: per m², per truck trip, per ton, a lump sum, or a number of months of the scaffold's rent
#          (e.g. "installation + dismantling = one month of rent", charged once);
#   cost:  what the crew / truck costs us, per m², trip, ton or lump sum.
# The client may take installation only, dismantling only, both or neither (scope).
SERVICE_DEFAULTS = {
    "truck_tons": 10,
    "services": [
        {"code": "install", "charge_type": "install", "name": "Installation", "name_ar": "التركيب", "basis": "rent_months",
         "price": 0.6, "cost_basis": "m2", "cost": 6, "min_charge": 3000, "when": "delivery"},
        {"code": "dismantle", "charge_type": "dismantle", "name": "Dismantling", "name_ar": "الفك", "basis": "rent_months",
         "price": 0.4, "cost_basis": "m2", "cost": 4, "min_charge": 2000, "when": "return"},
        {"code": "transport_out", "charge_type": "transport", "name": "Transport to site", "name_ar": "النقل إلى الموقع", "basis": "trip",
         "price": 3500, "cost_basis": "trip", "cost": 2200, "min_charge": 0, "when": "delivery"},
        {"code": "transport_back", "charge_type": "transport", "name": "Transport back to the yard", "name_ar": "النقل للمخزن", "basis": "trip",
         "price": 3500, "cost_basis": "trip", "cost": 2200, "min_charge": 0, "when": "return"},
    ],
}
SERVICE_BASES = ("rent_months", "m2", "trip", "ton", "lump")
COST_BASES = ("m2", "trip", "ton", "lump")
SCOPES = {"both": ("install", "dismantle"), "install": ("install",), "dismantle": ("dismantle",), "none": ()}


def service_rates(db):
    try:
        v = json.loads(db.setting("rent_service_rates") or "null")
    except ValueError:
        v = None
    if not (isinstance(v, dict) and v.get("services")):
        return json.loads(json.dumps(SERVICE_DEFAULTS))
    for r in v["services"]:
        r.setdefault("cost_basis", r["basis"] if r.get("basis") in COST_BASES else "m2")
    return v


def save_service_rates(db, d):
    out = {"truck_tons": num(d.get("truck_tons") or 10, "truck capacity (tons)", allow_zero=False), "services": []}
    for i, r in enumerate(d.get("services") or [], start=1):
        if not isinstance(r, dict) or not text(r.get("name")):
            continue
        ct = r.get("charge_type") or "other"
        if ct not in CHARGE_TYPES:
            raise ValidationError(f"Service {i}: unknown type")
        basis, cbasis = r.get("basis") or "m2", r.get("cost_basis") or "m2"
        if basis not in SERVICE_BASES:
            raise ValidationError(f"Service {i}: priced by months of rent, per m², trip, ton or lump sum")
        if cbasis not in COST_BASES:
            raise ValidationError(f"Service {i}: cost per m², trip, ton or lump sum")
        out["services"].append({"code": text(r.get("code"), maxlen=40) or f"svc{i}", "charge_type": ct, "name": text(r.get("name"), maxlen=120),
                                "name_ar": text(r.get("name_ar"), maxlen=120), "basis": basis,
                                "price": num(r.get("price"), f"service {i} price"), "cost_basis": cbasis, "cost": num(r.get("cost"), f"service {i} cost"),
                                "min_charge": num(r.get("min_charge") or 0, f"service {i} minimum"),
                                "when": r.get("when") if r.get("when") in CHARGE_WHEN else "delivery"})
    db.set_setting("rent_service_rates", json.dumps(out, ensure_ascii=False))
    return out


def service_quote(db, area, weight_kg=0, monthly_rent=0, scope="both", overrides=None, rates=None):
    """Services for a scaffold of `area` m², weighing `weight_kg`, renting at `monthly_rent` a month: quantity, client
    price, our cost and profit for each. scope: both | install | dismantle | none (installation / dismantling taken).
    overrides: {code: {qty, price, cost, off}} typed on the quote."""
    rates = rates or service_rates(db)
    if scope not in SCOPES:
        raise ValidationError("Scope must be both, install, dismantle or none")
    trips = max(1, math.ceil((weight_kg or 0) / (rates["truck_tons"] * 1000))) if weight_kg else 1
    tons = round((weight_kg or 0) / 1000, 3)
    measure = {"m2": area or 0, "trip": trips, "ton": tons, "lump": 1}
    lines = []
    for r in rates["services"]:
        o = (overrides or {}).get(r["code"]) or {}
        if o.get("off") or (r["charge_type"] in ("install", "dismantle") and r["charge_type"] not in SCOPES[scope]):
            continue
        if r["basis"] == "rent_months":   # e.g. 0.5 month of rent: one charge of half the monthly rent
            qty, unit_price, unit = 1, round((monthly_rent or 0) * r["price"], 2), f"{r['price']:g} × monthly rent"
        else:
            qty, unit_price, unit = measure[r["basis"]], r["price"], {"m2": "m²", "trip": "trip", "ton": "ton", "lump": "lump sum"}[r["basis"]]
        if o.get("qty") not in (None, ""):
            qty = num(o["qty"], "quantity")
        if o.get("price") not in (None, ""):
            unit_price = num(o["price"], "price")
        amount = round(qty * unit_price, 2)
        floor = r.get("min_charge") or 0
        min_applied = bool(floor and amount < floor)
        if min_applied:
            amount = floor
        cqty = measure[r.get("cost_basis") or "m2"]
        cost = num(o["cost"], "cost") if o.get("cost") not in (None, "") else r["cost"]
        total_cost = round(cqty * cost, 2)
        lines.append({"code": r["code"], "charge_type": r["charge_type"], "when": r["when"], "basis": r["basis"], "unit": unit,
                      "name": r["name"], "name_ar": r.get("name_ar", ""), "qty": round(qty, 3), "price": unit_price,
                      "cost_basis": r.get("cost_basis"), "cost_qty": round(cqty, 3), "cost": cost,
                      "amount": amount, "total_cost": total_cost, "profit": round(amount - total_cost, 2),
                      "margin": round((amount - total_cost) / amount * 100, 1) if amount else 0, "min_applied": min_applied})
    tot = {k: round(sum(x[k] for x in lines), 2) for k in ("amount", "total_cost", "profit")}
    tot["margin"] = round(tot["profit"] / tot["amount"] * 100, 1) if tot["amount"] else 0
    tot["per_m2"] = round(tot["amount"] / area, 2) if area else 0
    return {"area": area, "weight_kg": weight_kg, "tons": tons, "trips": trips, "truck_tons": rates["truck_tons"], "monthly_rent": monthly_rent,
            "scope": scope, "lines": lines, "totals": tot}


def list_models(db):
    return [{**m, "spec": _j(m["spec"], {})} for m in db.q("SELECT * FROM rent_models ORDER BY id")]


def save_model(db, d, mid=None):
    spec = _spec(d.get("spec") or {})
    evaluate(db, spec)   # refuse a model whose formulas do not work
    vals = (text(d.get("name"), "name", required=True), text(d.get("name_ar")), d.get("category") if d.get("category") in CATEGORIES
            else "scaffolding", json.dumps(spec, ensure_ascii=False), text(d.get("notes"), maxlen=2000))
    if mid:
        if not db.one("SELECT 1 FROM rent_models WHERE id=?", (int(mid),)):
            raise ValidationError("Model not found")
        db.x("UPDATE rent_models SET name=?, name_ar=?, category=?, spec=?, notes=? WHERE id=?", vals + (int(mid),))
        return int(mid)
    return db.x("INSERT INTO rent_models(name,name_ar,category,spec,notes) VALUES (?,?,?,?,?)", vals).lastrowid


def delete_model(db, mid):
    db.x("DELETE FROM rent_models WHERE id=?", (int(mid),))


def starter(db):
    """Scaffolding pieces from the standard handover form, elevator / cradle items, a frame set and the calculator."""
    made = {"items": 0, "kits": 0, "models": 0}
    with db.tx():
        for code, name, ar, cat, unit, cost in STARTER_ITEMS:
            if db.one("SELECT 1 FROM rent_items WHERE code=?", (code,)):
                continue
            db.conn.execute("INSERT INTO rent_items(code,name,name_ar,category,unit,monthly_rate,std_cost,loss_price,life_months,residual_pct) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)", (code, name, ar, cat, unit, 0, to_minor(cost), 0, 120, 0.10))
            made["items"] += 1
        if not db.one("SELECT 1 FROM rent_kits WHERE code='KIT-FRAME'"):
            ids = {r["code"]: r["id"] for r in db.q("SELECT id, code FROM rent_items")}
            kid = db.conn.execute("INSERT INTO rent_kits(code,name,name_ar,unit,monthly_rate) VALUES (?,?,?,?,?)",
                                  ("KIT-FRAME", "Frame set (frame + brace + board)", "طقم شباك (شباك + مقص + لوح)", "frame", 0)).lastrowid
            db.conn.executemany("INSERT INTO rent_kit_lines(kit_id,item_id,qty) VALUES (?,?,?)",
                                [(kid, ids["SC-FRM2"], 1), (kid, ids["SC-BRC3"], 1), (kid, ids["SC-BRD36"], 0.25)])
            made["kits"] += 1
        if not db.one("SELECT 1 FROM rent_models"):
            db.conn.execute("INSERT INTO rent_models(name,name_ar,category,spec) VALUES (?,?,?,?)",
                            ("Frame scaffold (façade)", "سقالة شبابيك (واجهة)", "scaffolding", json.dumps(FRAME_MODEL, ensure_ascii=False)))
            made["models"] += 1
    return made


# ================================================================ rentals (quotes → contracts)
def _per_unit(line):
    return {int(c["item_id"]): float(c["per_unit"]) for c in _j(line["components"], []) if c.get("item_id")}


def _clean_lines(db, raw):
    out = []
    for i, r in enumerate(raw or []):
        if not isinstance(r, dict):
            continue
        kind = r.get("kind") or "item"
        if kind not in LINE_KINDS:
            raise ValidationError("Unknown line type")
        qty = num(r.get("qty") if r.get("qty") not in (None, "") else 1, "quantity")
        rate = to_minor(num(r.get("rate"), "rate"))
        row = {"id": int(r["id"]) if str(r.get("id") or "").isdigit() else None, "seq": i, "kind": kind, "item_id": None,
               "kit_id": None, "model_id": None, "description": text(r.get("description"), maxlen=500), "unit": text(r.get("unit")),
               "qty": qty, "rate": rate, "charge_type": "", "charge_when": "", "components": [], "inputs": {},
               "notes": text(r.get("notes"), maxlen=1000),
               "cost": to_minor(num(r.get("cost") or 0, "cost")) if kind == "charge" else 0}   # our cost of a service (crew, truck)
        if kind == "item":
            it = get_item(db, r.get("item_id") or r.get("item"))
            row.update(item_id=it["id"], description=row["description"] or it["name"], unit=row["unit"] or it["unit"],
                       components=[{"item_id": it["id"], "per_unit": 1}])
        elif kind == "kit":
            k = db.one("SELECT * FROM rent_kits WHERE id=? OR code=?", (r.get("kit_id") or -1, text(r.get("kit") or "")))
            if not k:
                raise ValidationError("Choose the frame / kit")
            comps = r.get("components") or [{"item_id": x["item_id"], "per_unit": x["qty"]}
                                             for x in db.q("SELECT item_id, qty FROM rent_kit_lines WHERE kit_id=?", (k["id"],))]
            row.update(kit_id=k["id"], description=row["description"] or k["name"], unit=row["unit"] or k["unit"], components=comps)
        elif kind == "sqm":
            comps = r.get("components") or []
            row.update(unit=row["unit"] or "m²", description=row["description"] or "Scaffolding by square metre",
                       components=comps, inputs=r.get("inputs") if isinstance(r.get("inputs"), dict) else {},
                       model_id=int(r["model_id"]) if str(r.get("model_id") or "").isdigit() else None)
            if not comps:
                raise ValidationError("A square-metre line needs its pieces — work them out with the calculator")
        else:
            ct = r.get("charge_type") or "other"
            if ct not in CHARGE_TYPES:
                raise ValidationError("Unknown charge type")
            when = r.get("charge_when") or ("return" if ct == "dismantle" else "delivery")
            if when not in CHARGE_WHEN:
                raise ValidationError("Unknown charge timing")
            row.update(charge_type=ct, charge_when=when, unit=row["unit"] or "lump sum",
                       description=row["description"] or {"install": "Installation", "dismantle": "Dismantling",
                                                          "transport": "Transport", "materials": "Installation materials"}.get(ct, "Charge"))
        if kind != "charge":
            clean = []
            for c in row["components"]:
                if not isinstance(c, dict) or not c.get("item_id"):
                    continue
                per = num(c.get("per_unit"), "pieces per unit")
                if per > 0:
                    clean.append({"item_id": get_item(db, c["item_id"])["id"], "per_unit": round(per, 12)})
            if not clean:
                raise ValidationError(f"Line {i + 1}: no pieces")
            row["components"] = clean
        out.append(row)
    return out


def get(db, rid):
    r = db.one("SELECT * FROM rentals WHERE id=? OR code=?", (rid if str(rid).isdigit() else -1, text(rid)))
    if not r:
        raise ValidationError("Rental not found")
    return r


def save(db, d, rid=None, user=""):
    """Create or change a quote / contract (header and lines)."""
    old = get(db, rid) if rid else None
    if old and old["status"] in ("closed", "cancelled"):
        raise ValidationError("This rental is closed — reopen it first")
    client = text(d.get("client"), "client", required=True)
    lines = _clean_lines(db, d.get("lines"))
    if not lines:
        raise ValidationError("Add what is being rented")
    vat_code, _ = acc.tax_code(db, d.get("vat_code"), "vat")
    wht_code, _ = acc.tax_code(db, d.get("wht_code"), "wht")
    quote_date = acc.clean_date(d.get("quote_date") or (old and old["quote_date"]) or acc.today(), "offer date")
    head = (client, text(d.get("contact")), text(d.get("phone")), text(d.get("site"), maxlen=300), text(d.get("site_location"), maxlen=300),
            quote_date, acc.clean_date(d.get("valid_until"), "valid until") if d.get("valid_until") else "",
            acc.clean_date(d.get("start_date"), "start") if d.get("start_date") else "",
            num(d.get("duration_months") if d.get("duration_months") not in (None, "") else 1, "expected duration"),
            vat_code, wht_code, text(d.get("offer_notes"), maxlen=4000), text(d.get("notes"), maxlen=4000),
            d.get("lang") if d.get("lang") in ("en", "ar") else (old["lang"] if old else (db.setting("language") or "en")),
            int(num(d.get("min_days"), "minimum rental period (days)")), to_minor(num(d.get("deposit"), "security deposit")),
            1 if d.get("deposit_first") in (True, 1, "1", "true", "on") else 0)
    if head[-3] < 0 or head[-3] > 3650:
        raise ValidationError("The minimum rental period is 0 to 3650 days")
    with db.tx():
        if old:
            rid = old["id"]
            db.conn.execute("UPDATE rentals SET client=?, contact=?, phone=?, site=?, site_location=?, quote_date=?, valid_until=?, "
                            "start_date=?, duration_months=?, vat_code=?, wht_code=?, offer_notes=?, notes=?, lang=?, min_days=?, deposit=?, "
                            "deposit_first=? WHERE id=?", head + (rid,))
            used = {int(x) for h in db.q("SELECT data FROM rental_handovers WHERE rental_id=?", (rid,))
                    for x in (_j(h["data"], {}).get("units") or {}).keys()}
            used |= {r["line_id"] for r in db.q("SELECT DISTINCT line_id FROM rental_charges WHERE rental_id=? AND line_id IS NOT NULL", (rid,))}
            keep = {ln["id"] for ln in lines if ln["id"]}
            gone = [x["id"] for x in db.q("SELECT id FROM rental_lines WHERE rental_id=?", (rid,)) if x["id"] not in keep]
            if set(gone) & used:
                raise ValidationError("A line that was already delivered or charged cannot be removed")
            for g in gone:
                db.conn.execute("DELETE FROM rental_lines WHERE id=?", (g,))
        else:
            code = _code(db, "rentals", "RC-")
            rid = db.conn.execute("INSERT INTO rentals(client,contact,phone,site,site_location,quote_date,valid_until,start_date,"
                                  "duration_months,vat_code,wht_code,offer_notes,notes,lang,min_days,deposit,deposit_first,code,status,"
                                  "created_at,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  head + (code, "quote", _now(), user)).lastrowid
        for ln in lines:
            vals = (ln["seq"], ln["kind"], ln["item_id"], ln["kit_id"], ln["model_id"], ln["description"], ln["unit"], ln["qty"], ln["rate"],
                    ln["charge_type"], ln["charge_when"], json.dumps(ln["components"]), json.dumps(ln["inputs"], ensure_ascii=False), ln["notes"],
                    ln.get("cost", 0))
            if ln["id"] and db.one("SELECT 1 FROM rental_lines WHERE id=? AND rental_id=?", (ln["id"], rid)):
                db.conn.execute("UPDATE rental_lines SET seq=?, kind=?, item_id=?, kit_id=?, model_id=?, description=?, unit=?, qty=?, rate=?, "
                                "charge_type=?, charge_when=?, components=?, inputs=?, notes=?, cost=? WHERE id=?", vals + (ln["id"],))
            else:
                db.conn.execute("INSERT INTO rental_lines(seq,kind,item_id,kit_id,model_id,description,unit,qty,rate,charge_type,charge_when,"
                                "components,inputs,notes,cost,rental_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals + (rid,))
    return rid


def delete(db, rid):
    r = get(db, rid)
    if db.one("SELECT 1 FROM rental_handovers WHERE rental_id=? AND status='confirmed'", (r["id"],)) or \
            db.one("SELECT 1 FROM rental_invoices WHERE rental_id=?", (r["id"],)):
        raise ValidationError("Equipment was delivered or invoiced on this rental — cancel or close it instead")
    db.x("DELETE FROM rentals WHERE id=?", (r["id"],))


def _ensure_bu(db, r):
    if r["bu_id"] and db.one("SELECT 1 FROM business_units WHERE id=?", (r["bu_id"],)):
        return r["bu_id"]
    code = r["code"]
    bu = db.one("SELECT id FROM business_units WHERE code=?", (code,))
    if not bu:
        bid = db.conn.execute("INSERT INTO business_units(code,name,type,location,status,basis,customer) VALUES (?,?,?,?,?,?,?)",
                              (code, f"{r['client']} — {r['site'] or 'rental'}"[:200], "Equipment rental", r["site_location"] or r["site"],
                               "In Progress", "Flexible / Settled", r["client"])).lastrowid
    else:
        bid = bu["id"]
    db.conn.execute("UPDATE rentals SET bu_id=? WHERE id=?", (bid, r["id"]))
    return bid


def action(db, rid, what, d, user=""):
    r = get(db, rid)
    with db.tx():
        if what == "po":   # the client's purchase order: required before delivering
            if r["status"] in ("closed", "cancelled"):
                raise ValidationError("This rental is closed")
            po = text(d.get("po_number"), "purchase order number", required=True, maxlen=80)
            db.conn.execute("UPDATE rentals SET po_number=?, po_date=?, po_notes=?, start_date=COALESCE(NULLIF(start_date,''),?) WHERE id=?",
                            (po, acc.clean_date(d.get("po_date") or acc.today(), "PO date"), text(d.get("po_notes"), maxlen=1000),
                             acc.clean_date(d.get("start_date") or d.get("po_date") or acc.today()), r["id"]))
            _ensure_bu(db, get(db, r["id"]))
        elif what == "sign":   # the contract, signed by both parties
            if not r["po_number"]:
                raise ValidationError("Record the client's purchase order first")
            us, them = text(d.get("signed_us"), "our signatory", required=True), text(d.get("signed_client"), "client's signatory", required=True)
            db.conn.execute("UPDATE rentals SET signed_us=?, signed_client=?, signed_on=? WHERE id=?",
                            (us, them, acc.clean_date(d.get("signed_on") or acc.today()), r["id"]))
        elif what == "unsign":
            db.conn.execute("UPDATE rentals SET signed_us='', signed_client='', signed_on='' WHERE id=?", (r["id"],))
        elif what == "cancel":
            if db.one("SELECT 1 FROM rental_handovers WHERE rental_id=? AND status='confirmed'", (r["id"],)):
                raise ValidationError("Equipment was delivered — return it and close the rental instead")
            db.conn.execute("UPDATE rentals SET status='cancelled', closed_on=? WHERE id=?", (acc.today(), r["id"]))
            return
        elif what == "deposit":
            deposit(db, r, d, user)
        elif what == "close":
            held = _held(db, r["id"])
            if held:
                raise ValidationError("Pieces are still on site — record their return first")
            if deposit_balance(db, r):
                raise ValidationError(f"{to_major(deposit_balance(db, r)):,.2f} of security deposit is still held — refund it or apply it first")
            if db.one("SELECT 1 FROM rental_charges WHERE rental_id=? AND extract_id IS NULL", (r["id"],)):
                raise ValidationError("Some charges are not invoiced yet — invoice them (or delete them) first")
            db.conn.execute("UPDATE rentals SET status='closed', closed_on=? WHERE id=?", (acc.clean_date(d.get("date") or acc.today()), r["id"]))
            return
        elif what == "reopen":
            db.conn.execute("UPDATE rentals SET status='quote', closed_on='' WHERE id=?", (r["id"],))
        elif what == "charge":
            add_charge(db, r, d)
        else:
            raise ValidationError("Unknown action")
        _restatus(db, r["id"])


def _restatus(db, rid):
    r = get(db, rid)
    if r["status"] in ("closed", "cancelled"):
        return
    confirmed = db.q("SELECT kind FROM rental_handovers WHERE rental_id=? AND status='confirmed'", (rid,))
    if any(h["kind"] == "delivery" for h in confirmed):
        st = "returned" if not any(units_on_site(db, rid).values()) and any(h["kind"] == "return" for h in confirmed) else "active"
    else:
        st = "ordered" if r["po_number"] else "quote"
    db.conn.execute("UPDATE rentals SET status=? WHERE id=?", (st, rid))


# ---------------------------------------------------------------- on site
def _events(db, rid, upto=None):
    """[(date, kind, {line_id: units})] of confirmed handovers."""
    out = []
    for h in db.q("SELECT date, kind, data FROM rental_handovers WHERE rental_id=? AND status='confirmed' ORDER BY date, id", (rid,)):
        if upto and h["date"] > upto:
            continue
        out.append((h["date"], h["kind"], {int(k): float(v or 0) for k, v in (_j(h["data"], {}).get("units") or {}).items()}))
    return out


def units_on_site(db, rid, date=None, inclusive_returns=True):
    """Billing units (pieces / frames / m² / units) each rent line has on site on a date."""
    tot = {}
    for when, kind, units in _events(db, rid, date):
        for lid, u in units.items():
            if kind == "return" and not inclusive_returns and date and when >= date:
                continue
            tot[lid] = tot.get(lid, 0) + (u if kind == "delivery" else -u)
    return {k: round(v, 6) for k, v in tot.items()}


def unit_days(db, rid, frm, to, basis=None):
    """{line_id: unit-days} between two dates inclusive — delivered pieces count from the delivery day up to and
    including the return day. With a basis ({} to fill), also {line_id: unit-months}: each day is 1/days-in-its-month
    (calendar month, basis 0) or 1/N of a month."""
    frm_d, to_d = _dt.date.fromisoformat(frm), _dt.date.fromisoformat(to)
    ev = _events(db, rid, to)
    out = {}
    day = frm_d
    while day <= to_d:
        iso = day.isoformat()
        on = {}
        for when, kind, units in ev:
            if kind == "delivery" and when <= iso:
                for lid, u in units.items():
                    on[lid] = on.get(lid, 0) + u
            elif kind == "return" and when < iso:
                for lid, u in units.items():
                    on[lid] = on.get(lid, 0) - u
        month_days = (_dt.date(day.year + (day.month == 12), day.month % 12 + 1, 1) - _dt.timedelta(days=1)).day
        for lid, u in on.items():
            if u > EPS:
                out[lid] = out.get(lid, 0) + u
                if basis is not None:
                    basis[lid] = basis.get(lid, 0) + u / (basis.get("_n") or month_days)
        day += _dt.timedelta(days=1)
    if basis is not None:
        basis.pop("_n", None)
    return out


def detail(db, rid):
    r = get(db, rid)
    lines = db.q("SELECT * FROM rental_lines WHERE rental_id=? ORDER BY seq, id", (r["id"],))
    on = units_on_site(db, r["id"])
    names = {i["id"]: i for i in db.q("SELECT id, code, name, name_ar, unit, category FROM rent_items")}
    delivered = {}
    for when, kind, units in _events(db, r["id"]):
        for lid, u in units.items():
            key = "delivered" if kind == "delivery" else "returned"
            delivered.setdefault(lid, {"delivered": 0, "returned": 0})[key] += u
    out_lines = []
    for ln in lines:
        comps = [{"item_id": c["item_id"], "per_unit": c["per_unit"], **{k: names.get(c["item_id"], {}).get(k) for k in ("code", "name", "name_ar", "unit")}}
                 for c in _j(ln["components"], [])]
        out_lines.append({**ln, "rate": to_major(ln["rate"]), "cost": to_major(ln.get("cost") or 0), "components": comps, "inputs": _j(ln["inputs"], {}),
                          "on_site": round(on.get(ln["id"], 0), 4), **delivered.get(ln["id"], {"delivered": 0, "returned": 0}),
                          "monthly": to_major(int(round(ln["qty"] * ln["rate"]))) if ln["kind"] != "charge" else 0})
    held = {}
    for h in _held(db, r["id"]):
        lot = db.one("SELECT unit_cost FROM rent_lots WHERE id=?", (h["lot_id"],))
        x = held.setdefault(h["item_id"], {"item_id": h["item_id"], "qty": 0, "value": 0, **{k: names.get(h["item_id"], {}).get(k) for k in ("code", "name", "name_ar", "unit")}})
        x["qty"] += h["qty"]
        x["value"] += h["qty"] * lot["unit_cost"]
    for x in held.values():
        x["qty"] = round(x["qty"], 4)
        x["value"] = to_major(int(round(x["value"])))
    handovers = [{**h, "data": _j(h["data"], {}), "checklist": _j(h["checklist"], [])}
                 for h in db.q("SELECT * FROM rental_handovers WHERE rental_id=? ORDER BY date, id", (r["id"],))]
    charges = [{**c, "amount": to_major(c["amount"]), "total": to_major(int(round(c["qty"] * c["amount"])))}
               for c in db.q("SELECT c.*, x.number AS invoice_no FROM rental_charges c LEFT JOIN extracts x ON x.id=c.extract_id "
                             "WHERE c.rental_id=? ORDER BY c.date, c.id", (r["id"],))]
    invoices = db.q("SELECT ri.*, x.number, x.date, x.status FROM rental_invoices ri JOIN extracts x ON x.id=ri.extract_id "
                    "WHERE ri.rental_id=? ORDER BY ri.period_from, ri.id", (r["id"],))
    for inv in invoices:
        inv["total"] = (acc.get_extract(db, inv["extract_id"]) or {}).get("total", 0)
    rent_monthly = sum(ln["on_site"] * ln["rate"] for ln in out_lines if ln["kind"] != "charge")
    quote = {"monthly": round(sum(ln["monthly"] for ln in out_lines), 2),
             "one_time": round(sum(ln["qty"] * ln["rate"] for ln in out_lines if ln["kind"] == "charge"), 2)}
    quote["estimate"] = round(quote["monthly"] * (r["duration_months"] or 0) + quote["one_time"], 2)
    bu = db.one("SELECT code FROM business_units WHERE id=?", (r["bu_id"],)) if r["bu_id"] else None
    deposits = [acc.get_entry(db, e["id"]) for e in db.q(f"SELECT e.id FROM journal_entries e WHERE {LIVE_REF} AND e.ref LIKE ? ORDER BY e.date, e.id",
                                                             (f"RENT:{r['code']}:DEP%",))]
    return {"rental": {**r, "bu_code": bu["code"] if bu else "", "deposit": to_major(r["deposit"]), "deposit_held": to_major(deposit_balance(db, r))},
            "deposits": [{"id": e["id"], "date": e["date"], "type": e["meta"].get("deposit"), "memo": e["memo"], "amount": e["amount"]} for e in deposits], "lines": out_lines, "held": sorted(held.values(), key=lambda x: x["code"] or ""),
            "handovers": handovers, "charges": charges, "invoices": invoices, "quote": quote,
            "on_site_monthly": round(rent_monthly, 2), "next_period": next_period(db, r["id"]),
            "unbilled_charges": round(sum(c["total"] for c in charges if not c["extract_id"]), 2)}


def list_rentals(db, q=None, status=None):
    w, p = ["1=1"], []
    if q:
        w.append("(code LIKE ? OR client LIKE ? OR site LIKE ? OR po_number LIKE ?)")
        p += [f"%{q}%"] * 4
    if status == "open":
        w.append("status IN ('ordered','active','returned')")
    elif status in STATUSES:
        w.append("status=?")
        p.append(status)
    rows = []
    for r in db.q(f"SELECT * FROM rentals WHERE {' AND '.join(w)} ORDER BY id DESC", p):
        on = units_on_site(db, r["id"])
        rate = {ln["id"]: ln["rate"] for ln in db.q("SELECT id, rate FROM rental_lines WHERE rental_id=?", (r["id"],))}
        monthly = sum(u * rate.get(lid, 0) for lid, u in on.items())
        quoted = sum(ln["qty"] * ln["rate"] for ln in db.q("SELECT qty, rate FROM rental_lines WHERE rental_id=? AND kind<>'charge'", (r["id"],)))
        last = db.one("SELECT MAX(period_to) AS d FROM rental_invoices WHERE rental_id=?", (r["id"],))["d"]
        rows.append({**r, "monthly_on_site": to_major(int(round(monthly))), "monthly_quoted": to_major(int(round(quoted))),
                     "billed_to": last or "", "pieces_out": round(sum(h["qty"] for h in _held(db, r["id"])), 2)})
    live = [x for x in rows if x["status"] in ("active", "returned")]
    return {"rows": rows, "totals": {"active": sum(1 for x in rows if x["status"] == "active"),
                                     "quotes": sum(1 for x in rows if x["status"] == "quote"),
                                     "monthly": round(sum(x["monthly_on_site"] for x in live), 2)}}


def ongoing(db, today=None):
    """Contracts with equipment on site, for the dashboard: rent a month, rent earned since the first delivery,
    what the site was invoiced and paid, and the next period to bill."""
    today = today or acc.today()
    out = []
    for r in list_rentals(db, status="open")["rows"]:
        if r["status"] not in ("active", "returned"):
            continue
        first = db.one("SELECT MIN(date) AS d FROM rental_handovers WHERE rental_id=? AND kind='delivery' AND status='confirmed'", (r["id"],))["d"]
        days = max(0, (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(first)).days) if first else 0
        inv = col = 0
        if r["bu_id"]:
            inv = db.one("SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                         "JOIN accounts a ON a.code=l.account WHERE e.status='posted' AND e.bu_id=? AND a.subtype='receivable' AND l.debit>0",
                         (r["bu_id"],))["n"]
            col = db.one("SELECT COALESCE(SUM(l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                         "JOIN accounts a ON a.code=l.account WHERE e.status='posted' AND e.bu_id=? AND a.subtype='receivable' AND l.credit>0",
                         (r["bu_id"],))["n"]
        nxt = next_period(db, r["id"])
        out.append({"id": r["id"], "code": r["code"], "client": r["client"], "site": r["site"], "since": first or "", "days": days,
                    "monthly": r["monthly_on_site"], "rent_to_date": round(r["monthly_on_site"] / 30 * days, 2), "pieces_out": r["pieces_out"],
                    "billed_to": r["billed_to"], "next_from": nxt["from"] if nxt else "", "invoiced": to_major(inv), "collected": to_major(col),
                    "owed": to_major(inv - col), "to_bill": bool(nxt and nxt["from"] <= today)})
    return {"rows": out, "monthly": round(sum(x["monthly"] for x in out), 2), "owed": round(sum(x["owed"] for x in out), 2),
            "to_bill": sum(1 for x in out if x["to_bill"])}


# ---------------------------------------------------------------- allocation (frame / m² rent → per piece)
def allocation(db, rid, line_id):
    """How a frame / square-metre / piece line's rent splits over its pieces, by the pieces' FIFO cost."""
    r = get(db, rid)
    ln = db.one("SELECT * FROM rental_lines WHERE id=? AND rental_id=?", (int(line_id), r["id"]))
    if not ln or ln["kind"] == "charge":
        raise ValidationError("Choose a rent line")
    on = units_on_site(db, r["id"]).get(ln["id"], 0)
    units = on if on > EPS else ln["qty"]
    held = {}
    for h in _held(db, r["id"]):
        lot = db.one("SELECT unit_cost FROM rent_lots WHERE id=?", (h["lot_id"],))
        a = held.setdefault(h["item_id"], [0.0, 0.0])
        a[0] += h["qty"]
        a[1] += h["qty"] * lot["unit_cost"]
    rows = []
    for item_id, per in _per_unit(ln).items():
        it = db.one("SELECT * FROM rent_items WHERE id=?", (item_id,))
        qty = per * units
        cost = held[item_id][1] / held[item_id][0] if item_id in held and held[item_id][0] > EPS else unit_cost_next(db, item_id, qty)
        rows.append({"item_id": item_id, "code": it["code"], "name": it["name"], "name_ar": it["name_ar"], "unit": it["unit"],
                     "per_unit": per, "qty": round(qty, 4), "unit_cost": cost, "list_rate": it["monthly_rate"]})
    rent = units * ln["rate"]
    total_value = sum(x["qty"] * x["unit_cost"] for x in rows)
    basis = "cost"
    if total_value <= EPS:   # no costs yet: fall back to the pieces' own rent rates, else equal shares
        basis = "rate" if any(x["list_rate"] for x in rows) else "equal"
        for x in rows:
            x["unit_cost"] = x["list_rate"] if basis == "rate" else 1
        total_value = sum(x["qty"] * x["unit_cost"] for x in rows)
    day_basis = settings(db)["day_basis"] or 30
    out = []
    for x in rows:
        share = x["qty"] * x["unit_cost"] / total_value if total_value else 0
        monthly = rent * share
        per_piece = monthly / x["qty"] if x["qty"] > EPS else 0
        out.append({**x, "unit_cost": to_major(int(round(x["unit_cost"]))) if basis == "cost" else None,
                    "value": to_major(int(round(x["qty"] * x["unit_cost"]))) if basis == "cost" else None,
                    "share": round(share, 6), "monthly": to_major(int(round(monthly))), "per_piece_month": round(per_piece / 100, 4),
                    "per_piece_day": round(per_piece / day_basis / 100, 4),
                    "yield": round(per_piece / x["unit_cost"], 6) if basis == "cost" and x["unit_cost"] else None,
                    "list_rate": to_major(x["list_rate"])})
    return {"line": {**ln, "rate": to_major(ln["rate"]), "components": _j(ln["components"], [])}, "units": units, "on_site": on > EPS,
            "rent": to_major(int(round(rent))), "value": to_major(int(round(total_value))) if basis == "cost" else None,
            "basis": basis, "yield": round(rent / total_value, 6) if basis == "cost" and total_value else None, "rows": out}


# ---------------------------------------------------------------- handovers
def handover_template(db, rid, kind):
    """A new delivery / return: what is still to deliver (or on site), with its pieces and the safety checklist."""
    r = get(db, rid)
    lines = db.q("SELECT * FROM rental_lines WHERE rental_id=? AND kind<>'charge' ORDER BY seq, id", (r["id"],))
    on = units_on_site(db, r["id"])
    delivered = {}
    for _, k, units in _events(db, r["id"]):
        for lid, u in units.items():
            if k == "delivery":
                delivered[lid] = delivered.get(lid, 0) + u
    held = {}
    for h in _held(db, r["id"]):
        held[h["item_id"]] = held.get(h["item_id"], 0) + h["qty"]
    units, items, cats = {}, [], set()
    for ln in lines:
        u = max(ln["qty"] - delivered.get(ln["id"], 0), 0) if kind == "delivery" else on.get(ln["id"], 0)
        units[str(ln["id"])] = round(u, 4)
        for item_id, per in _per_unit(ln).items():
            it = db.one("SELECT * FROM rent_items WHERE id=?", (item_id,))
            cats.add(it["category"])
            qty = math.ceil(per * u - 1e-4) if kind == "delivery" else min(math.ceil(per * u - 1e-4), held.get(item_id, 0))
            if kind == "return":
                held[item_id] = held.get(item_id, 0) - qty
            items.append({"line_id": ln["id"], "item_id": item_id, "code": it["code"], "name": it["name"], "name_ar": it["name_ar"],
                          "unit": it["unit"], "per_unit": per, "qty": qty, "damaged": 0, "lost": 0, "serial": "",
                          "loss_price": to_major(loss_price(db, it)), "in_yard": item_stock(db, item_id)["in_yard"]})
    st = settings(db)
    checklist = [] if kind == "return" else [{"text": c[0], "text_ar": c[1] if len(c) > 1 else "", "ok": "", "note": ""}
                                             for cat in (sorted(cats) or ["other"]) for c in st["checklists"].get(cat, [])]
    n = db.one("SELECT COUNT(*) AS n FROM rental_handovers WHERE rental_id=? AND kind=?", (r["id"], kind))["n"] + 1
    return {"kind": kind, "date": acc.today(), "number": f"{r['code']}/{'D' if kind == 'delivery' else 'R'}{n}",
            "data": {"units": units, "items": items}, "checklist": checklist, "decision": "", "our_name": r["signed_us"] or "",
            "client_name": r["signed_client"] or "", "signed": 0, "notes": ""}


def _clean_handover(db, r, d):
    kind = d.get("kind")
    if kind not in ("delivery", "return"):
        raise ValidationError("Delivery or return?")
    lines = {ln["id"] for ln in db.q("SELECT id FROM rental_lines WHERE rental_id=? AND kind<>'charge'", (r["id"],))}
    data = d.get("data") or {}
    units = {}
    for k, v in (data.get("units") or {}).items():
        if str(k).isdigit() and int(k) in lines and v not in (None, ""):
            u = num(v, "units")
            if u < 0:
                raise ValidationError("Quantities cannot be negative")
            units[str(int(k))] = u
    items = []
    for x in data.get("items") or []:
        if not isinstance(x, dict) or not x.get("item_id"):
            continue
        it = get_item(db, x["item_id"])
        row = {"line_id": int(x["line_id"]) if str(x.get("line_id") or "").isdigit() else None, "item_id": it["id"], "code": it["code"],
               "name": it["name"], "name_ar": it["name_ar"], "unit": it["unit"], "qty": num(x.get("qty"), "quantity"),
               "damaged": num(x.get("damaged"), "damaged") if kind == "return" else 0, "lost": num(x.get("lost"), "lost") if kind == "return" else 0,
               "loss_price": num(x.get("loss_price") if x.get("loss_price") not in (None, "") else to_major(loss_price(db, it)), "loss price"),
               "serial": text(x.get("serial"), maxlen=300)}
        if min(row["qty"], row["damaged"], row["lost"]) < 0:
            raise ValidationError("Quantities cannot be negative")
        items.append(row)
    checklist = [{"text": text(c.get("text"), maxlen=400), "text_ar": text(c.get("text_ar"), maxlen=400),
                  "ok": c.get("ok") if c.get("ok") in ("yes", "no", "na") else "", "note": text(c.get("note"), maxlen=300)}
                 for c in (d.get("checklist") or []) if isinstance(c, dict) and (c.get("text") or c.get("text_ar"))]
    return kind, {"units": units, "items": items}, checklist


def save_handover(db, rid, d, hid=None):
    r = get(db, rid)
    if hid:
        h = db.one("SELECT * FROM rental_handovers WHERE id=? AND rental_id=?", (int(hid), r["id"]))
        if not h:
            raise ValidationError("Handover not found")
        if h["status"] == "confirmed":
            raise ValidationError("This handover is confirmed — undo it first to change it")
    kind, data, checklist = _clean_handover(db, r, d)
    vals = (kind, text(d.get("number"), maxlen=60) or f"{r['code']}/{kind[0].upper()}", acc.clean_date(d.get("date") or acc.today()),
            json.dumps(data, ensure_ascii=False), json.dumps(checklist, ensure_ascii=False),
            d.get("decision") if d.get("decision") in ("safe", "unsafe") else "", text(d.get("our_name")), text(d.get("client_name")),
            1 if d.get("signed") in (True, 1, "1", "true", "on") else 0, text(d.get("notes"), maxlen=2000))
    if hid:
        db.x("UPDATE rental_handovers SET kind=?, number=?, date=?, data=?, checklist=?, decision=?, our_name=?, client_name=?, signed=?, "
             "notes=? WHERE id=?", vals + (int(hid),))
        return int(hid)
    return db.x("INSERT INTO rental_handovers(kind,number,date,data,checklist,decision,our_name,client_name,signed,notes,rental_id,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'draft')", vals + (r["id"],)).lastrowid


def confirm_handover(db, hid, user=""):
    """Signed by both parties: pieces leave / come back to the yard (FIFO), losses are charged and written off."""
    h = db.one("SELECT * FROM rental_handovers WHERE id=?", (int(hid),))
    if not h:
        raise ValidationError("Handover not found")
    if h["status"] == "confirmed":
        raise ValidationError("Already confirmed")
    r = get(db, h["rental_id"])
    data, checklist = _j(h["data"], {}), _j(h["checklist"], [])
    if not (h["signed"] and h["our_name"] and h["client_name"]):
        raise ValidationError("Both parties must sign the handover — enter both names and tick 'signed'")
    kind, date = h["kind"], h["date"]
    with db.tx():
        if kind == "delivery":
            if r["status"] in ("closed", "cancelled"):
                raise ValidationError("This rental is closed")
            if not r["po_number"]:
                raise ValidationError("No purchase order yet — record the client's PO before delivering anything")
            if not r["signed_on"]:
                raise ValidationError("The contract is not signed yet — record both signatures on the contract first")
            if r["deposit"] and r["deposit_first"] and deposit_balance(db, r) < r["deposit"]:
                raise ValidationError(f"The security deposit ({to_major(r['deposit']):,.2f}) must be received before delivering — "
                                      f"{to_major(deposit_balance(db, r)):,.2f} received so far")
            if any(c["ok"] == "no" for c in checklist) and h["decision"] != "unsafe":
                raise ValidationError("A safety check failed — fix it, or record the handover as 'not safe'")
            if not h["decision"]:
                raise ValidationError("Record the final decision: safe to use, or not safe")
            short = []
            for x in data.get("items") or []:
                if x["qty"] > EPS:
                    try:
                        _fifo_take(db, x["item_id"], x["qty"], "yard")
                    except ValidationError as e:
                        short.append(str(e))
            if short:
                raise ValidationError("Not enough in the yard: " + "; ".join(short))
            for x in data.get("items") or []:
                if x["qty"] > EPS:
                    for lot, take in _fifo_take(db, x["item_id"], x["qty"], "yard"):
                        db.conn.execute("INSERT INTO rent_moves(date,item_id,lot_id,rental_id,handover_id,qty,kind) VALUES (?,?,?,?,?,?,?)",
                                        (date, x["item_id"], lot["id"], r["id"], h["id"], take, "out"))
            if not db.one("SELECT 1 FROM rental_handovers WHERE rental_id=? AND kind='delivery' AND status='confirmed'", (r["id"],)):
                _trigger_charges(db, r, "delivery", date, h["id"])
        else:
            on = units_on_site(db, r["id"], date)
            for lid, u in (data.get("units") or {}).items():
                if u > on.get(int(lid), 0) + 1e-6:
                    raise ValidationError(f"Returning {u:g} but only {on.get(int(lid), 0):g} are on site for that line")
            gone = []
            _minimum_charges(db, r, h, date, data.get("units") or {})
            for x in data.get("items") or []:
                for what, q in (("back", x["qty"]), ("damaged", x["damaged"]), ("lost", x["lost"])):
                    if q <= EPS:
                        continue
                    for lot, take in _fifo_take(db, x["item_id"], q, "rental", r["id"]):
                        db.conn.execute("INSERT INTO rent_moves(date,item_id,lot_id,rental_id,handover_id,qty,kind) VALUES (?,?,?,?,?,?,?)",
                                        (date, x["item_id"], lot["id"], r["id"], h["id"], take, what))
                        if what != "back":
                            gone.append((lot, take))
                    if what != "back":
                        db.conn.execute("INSERT INTO rental_charges(rental_id,date,kind,description,qty,amount,handover_id,account) "
                                        "VALUES (?,?,?,?,?,?,?,?)",
                                        (r["id"], date, "loss" if what == "lost" else "damage",
                                         f"{'Lost' if what == 'lost' else 'Damaged'}: {x['name']}", q, to_minor(x["loss_price"]), h["id"],
                                         _role_or(db, "rental_compensation") or ""))
            if gone:
                lines, cleared, cost = _write_off_lines(db, gone)
                if cost:
                    acc._insert_entry(db, date=date, kind=KINDS[1], memo=f"Pieces lost / damaged on {r['code']} ({r['client']}) — {h['number']}",
                                      bu_id=_ensure_bu(db, r), party=r["client"], ref=f"RENT:{r['code']}:{h['number']}", lines=lines,
                                      meta={"rental": r["code"], "handover": h["number"], "accum_out": cleared}, user=user)
        db.conn.execute("UPDATE rental_handovers SET status='confirmed', confirmed_at=?, confirmed_by=? WHERE id=?", (_now(), user, h["id"]))
        if kind == "return" and not any(v > EPS for v in units_on_site(db, r["id"]).values()):
            _trigger_charges(db, r, "return", date, h["id"])
        _ensure_bu(db, get(db, r["id"]))
        _restatus(db, r["id"])


def undo_handover(db, hid, user=""):
    """Take back the latest confirmed handover of a rental (a mistake): pieces, charges and write-off are reversed."""
    h = db.one("SELECT * FROM rental_handovers WHERE id=?", (int(hid),))
    if not h or h["status"] != "confirmed":
        raise ValidationError("Only a confirmed handover can be undone")
    last = db.one("SELECT id FROM rental_handovers WHERE rental_id=? AND status='confirmed' ORDER BY date DESC, confirmed_at DESC, id DESC LIMIT 1",
                  (h["rental_id"],))
    if last["id"] != h["id"]:
        raise ValidationError("Undo the later handovers first")
    if db.one("SELECT 1 FROM rental_invoices WHERE rental_id=? AND period_to>=?", (h["rental_id"], h["date"])):
        raise ValidationError("Rent is already invoiced past this date — delete that draft invoice (or credit it) first")
    if db.one("SELECT 1 FROM rental_charges WHERE handover_id=? AND extract_id IS NOT NULL", (h["id"],)):
        raise ValidationError("Charges from this handover are already invoiced")
    r = get(db, h["rental_id"])
    with db.tx():
        db.conn.execute("DELETE FROM rent_moves WHERE handover_id=?", (h["id"],))
        db.conn.execute("DELETE FROM rental_charges WHERE handover_id=?", (h["id"],))
        for e in db.q(f"SELECT id FROM journal_entries e WHERE {acc.LIVE} AND e.ref=?", (f"RENT:{r['code']}:{h['number']}",)):
            acc.void_entry(db, e["id"], "Handover undone", user)
        db.conn.execute("UPDATE rental_handovers SET status='draft', confirmed_at='', confirmed_by='' WHERE id=?", (h["id"],))
        _restatus(db, r["id"])


def delete_handover(db, hid):
    h = db.one("SELECT status FROM rental_handovers WHERE id=?", (int(hid),))
    if not h:
        raise ValidationError("Handover not found")
    if h["status"] == "confirmed":
        raise ValidationError("Undo the handover before deleting it")
    db.x("DELETE FROM rental_handovers WHERE id=?", (int(hid),))


# ---------------------------------------------------------------- charges & invoices
def _minimum_charges(db, r, h, date, returned):
    """Pieces returned before the minimum rental period are charged the days they fell short (per line, the units
    returned are matched to the oldest deliveries first)."""
    if not r["min_days"]:
        return
    tranches = {}
    for when, kind, units in _events(db, r["id"]):
        for lid, u in units.items():
            q = tranches.setdefault(lid, [])
            if kind == "delivery":
                q.append([when, u])
            else:
                while u > EPS and q:
                    take = min(q[0][1], u)
                    q[0][1] -= take
                    u -= take
                    if q[0][1] <= EPS:
                        q.pop(0)
    ret = _dt.date.fromisoformat(date)
    for lid, u in returned.items():
        lid, u = int(lid), float(u or 0)
        short = 0.0
        q = tranches.get(lid, [])
        while u > EPS and q:
            take = min(q[0][1], u)
            kept = (ret - _dt.date.fromisoformat(q[0][0])).days + 1
            if kept < r["min_days"]:
                short += take * (r["min_days"] - kept)
            q[0][1] -= take
            u -= take
            if q[0][1] <= EPS:
                q.pop(0)
        ln = db.one("SELECT * FROM rental_lines WHERE id=?", (lid,))
        if short > EPS and ln and ln["rate"]:
            db.conn.execute("INSERT INTO rental_charges(rental_id,date,kind,description,qty,amount,line_id,handover_id,account) VALUES (?,?,?,?,?,?,?,?,?)",
                            (r["id"], date, "minimum", f"Minimum rental period ({r['min_days']} days): {ln['description']} — {short:g} {ln['unit']}-days short",
                             round(short / 30, 4), ln["rate"], None, h["id"], _role_or(db, "rental_revenue") or ""))


def deposit_balance(db, r):
    """Security deposit the company holds for this rental (minor units)."""
    acc_code = _role_or(db, "rental_deposits")
    row = db.one(f"SELECT COALESCE(SUM(l.credit-l.debit),0) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                 f"WHERE {LIVE_REF} AND l.account=? AND e.ref LIKE ?", (acc_code, f"RENT:{r['code']}:DEP%"))
    return row["n"]


def deposit(db, r, d, user=""):
    """receive (Dr cash / Cr client deposits), refund (the reverse) or apply to what the client owes (Dr deposits / Cr receivables)."""
    what = d.get("type")
    if what not in ("receive", "refund", "apply"):
        raise ValidationError("Receive, refund or apply the deposit?")
    amount = to_minor(num(d.get("amount"), "amount", allow_zero=False))
    held = deposit_balance(db, r)
    if what != "receive" and amount > held:
        raise ValidationError(f"Only {to_major(held):,.2f} of deposit is held")
    dep = _role_or(db, "rental_deposits")
    date = acc.clean_date(d.get("date") or acc.today())
    party = r["client"]
    if what == "apply":
        owed = -to_minor(acc.party_balance(db, db.acct("ar"), party))
        if amount > owed:
            raise ValidationError(f"{party} only owes {to_major(max(owed, 0)):,.2f} on invoices — apply at most that, or refund the rest")
        other = {"account": db.acct("ar"), "credit": to_major(amount), "party": party}
        lines = [{"account": dep, "debit": to_major(amount), "party": party}, other]
        memo = f"Security deposit applied to {party}'s invoices ({r['code']})"
    else:
        money = acc.money_account(db, d.get("method"), d.get("paid_from") or d.get("received_into"))
        if what == "receive":
            lines = [{"account": money, "debit": to_major(amount)}, {"account": dep, "credit": to_major(amount), "party": party}]
            memo = f"Security deposit received from {party} ({r['code']})"
        else:
            lines = [{"account": dep, "debit": to_major(amount), "party": party}, {"account": money, "credit": to_major(amount)}]
            memo = f"Security deposit refunded to {party} ({r['code']})"
    return acc._insert_entry(db, date=date, kind=KINDS[3], memo=text(d.get("memo")) or memo, bu_id=_ensure_bu(db, r), party=party,
                             ref=f"RENT:{r['code']}:DEP-{what.upper()}", lines=lines, meta={"rental": r["code"], "deposit": what}, user=user)


def _charge_account(db, kind):
    if kind in ("loss", "damage"):
        return _role_or(db, "rental_compensation")
    if kind in ("rent", "minimum"):
        return _role_or(db, "rental_revenue")
    return _role_or(db, "rental_services")


def _trigger_charges(db, r, when, date, handover_id):
    for ln in db.q("SELECT * FROM rental_lines WHERE rental_id=? AND kind='charge' AND charge_when=?", (r["id"], when)):
        if db.one("SELECT 1 FROM rental_charges WHERE line_id=?", (ln["id"],)):
            continue
        db.conn.execute("INSERT INTO rental_charges(rental_id,date,kind,description,qty,amount,line_id,handover_id,account) VALUES (?,?,?,?,?,?,?,?,?)",
                        (r["id"], date, ln["charge_type"] or "other", ln["description"], ln["qty"], ln["rate"], ln["id"], handover_id,
                         _charge_account(db, ln["charge_type"])))


def add_charge(db, r, d):
    """A one-time charge now: a contract charge line billed manually, or anything extra."""
    if d.get("line_id"):
        ln = db.one("SELECT * FROM rental_lines WHERE id=? AND rental_id=? AND kind='charge'", (int(d["line_id"]), r["id"]))
        if not ln:
            raise ValidationError("Charge line not found")
        if db.one("SELECT 1 FROM rental_charges WHERE line_id=?", (ln["id"],)):
            raise ValidationError("That charge is already added")
        kind, desc, qty, amount, line_id = ln["charge_type"] or "other", ln["description"], ln["qty"], ln["rate"], ln["id"]
    else:
        kind = d.get("kind") if d.get("kind") in CHARGE_TYPES + ("loss", "damage") else "other"
        desc = text(d.get("description"), "description", required=True, maxlen=500)
        qty = num(d.get("qty") or 1, "quantity", allow_zero=False)
        amount = to_minor(num(d.get("amount"), "amount", allow_zero=False))
        line_id = None
    db.conn.execute("INSERT INTO rental_charges(rental_id,date,kind,description,qty,amount,line_id,account) VALUES (?,?,?,?,?,?,?,?)",
                    (r["id"], acc.clean_date(d.get("date") or acc.today()), kind, desc, qty, amount, line_id, _charge_account(db, kind)))


def delete_charge(db, cid):
    c = db.one("SELECT * FROM rental_charges WHERE id=?", (int(cid),))
    if not c:
        raise ValidationError("Charge not found")
    if c["extract_id"]:
        raise ValidationError("This charge is on an invoice — delete the draft invoice first")
    db.x("DELETE FROM rental_charges WHERE id=?", (c["id"],))


def next_period(db, rid):
    """The next rent period to invoice: from the day after the last invoiced period (or the first delivery) to the
    end of that month, capped at the last return when everything is back."""
    last = db.one("SELECT MAX(period_to) AS d FROM rental_invoices WHERE rental_id=?", (rid,))["d"]
    first = db.one("SELECT MIN(date) AS d FROM rental_handovers WHERE rental_id=? AND kind='delivery' AND status='confirmed'", (rid,))["d"]
    if not first:
        return None
    frm = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)) if last else _dt.date.fromisoformat(first)
    end = (_dt.date(frm.year + (frm.month == 12), frm.month % 12 + 1, 1) - _dt.timedelta(days=1))
    if not any(v > EPS for v in units_on_site(db, rid).values()):
        back = db.one("SELECT MAX(date) AS d FROM rental_handovers WHERE rental_id=? AND kind='return' AND status='confirmed'", (rid,))["d"]
        if back and back < end.isoformat():
            end = _dt.date.fromisoformat(back)
    if end < frm:
        end = frm
    return {"from": frm.isoformat(), "to": end.isoformat()}


def _add_month(d):
    y, m = d.year + (d.month == 12), d.month % 12 + 1
    import calendar
    return _dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def anniversary_to(frm):
    """The client's extract periods run day to day: 5/10 → 5/11 is one month (booked to the 4th)."""
    return (_add_month(_dt.date.fromisoformat(frm)) - _dt.timedelta(days=1)).isoformat()


def _extract_months(frm, to):
    """Whole months day-to-day, then the rest of the days over 30 (shown as the % on the extract)."""
    a, b = _dt.date.fromisoformat(frm), _dt.date.fromisoformat(to) + _dt.timedelta(days=1)
    n, cur = 0, a
    while _add_month(cur) <= b:
        cur, n = _add_month(cur), n + 1
    return n + (b - cur).days / 30


def _extract_lines(db, r, frm, to):
    """Lines in the client's extract wording: the pieces on site × the monthly rate × the share of the month."""
    from_s = f"{int(frm[8:])}/{int(frm[5:7])}/{frm[:4]}"
    t2 = (_dt.date.fromisoformat(to) + _dt.timedelta(days=1)).isoformat()
    to_s = f"{int(t2[8:])}/{int(t2[5:7])}/{t2[:4]}"
    months = round(_extract_months(frm, to), 4)
    span = (_dt.date.fromisoformat(to) - _dt.date.fromisoformat(frm)).days + 1
    days = unit_days(db, r["id"], frm, to, {"_n": settings(db)["day_basis"] or 0})
    out = []
    for ln in db.q("SELECT * FROM rental_lines WHERE rental_id=? AND kind<>'charge' ORDER BY seq, id", (r["id"],)):
        ud = days.get(ln["id"], 0)
        if ud <= EPS or not ln["rate"]:
            continue
        units = round(ud / span, 2)
        desc = ln["description"] or ""
        desc = ("بال" + desc) if desc.startswith("شباك") else desc
        out.append({"category": "إيجار", "item_no": "", "unit": "عدد", "description": f"ايجار {desc} مدة {from_s} الي {to_s}",
                    "qty": units, "price": to_major(ln["rate"]), "pct": min(1.0, months) if months <= 1 else 1.0,
                    "account": _role_or(db, "rental_revenue") or ""})
        if months > 1:          # more than a month: the whole months at 100 %, the rest as its share
            out[-1]["qty"] = round(units * months, 2)
    charges = db.q("SELECT * FROM rental_charges WHERE rental_id=? AND extract_id IS NULL AND date<=? ORDER BY date, id", (r["id"], to))
    for c in charges:
        unit = "مق" if "نقل" in (c["description"] or "") else "ق"
        out.append({"category": "مبالغ لمرة واحدة", "item_no": "", "unit": unit, "description": c["description"], "qty": c["qty"],
                    "price": to_major(c["amount"]), "account": c["account"] or _charge_account(db, c["kind"]), "charge_id": c["id"]})
    return out


def invoice_lines(db, rid, frm, to, lang="en", style=""):
    r = get(db, rid)
    frm, to = acc.clean_date(frm, "from"), acc.clean_date(to, "to")
    if to < frm:
        raise ValidationError("The period ends before it starts")
    clash = db.one("SELECT period_from, period_to FROM rental_invoices WHERE rental_id=? AND period_from<=? AND period_to>=?", (r["id"], to, frm))
    if clash:
        raise ValidationError(f"Rent from {clash['period_from']} to {clash['period_to']} is already invoiced")
    if style == "extract":
        out = _extract_lines(db, r, frm, to)
        return {"from": frm, "to": to, "lines": out, "total": round(sum(x["qty"] * x["price"] * x.get("pct", 1) for x in out), 2)}
    ar = lang == "ar"
    months_by = {"_n": settings(db)["day_basis"] or 0}
    days = unit_days(db, r["id"], frm, to, months_by)
    out = []
    for ln in db.q("SELECT * FROM rental_lines WHERE rental_id=? AND kind<>'charge' ORDER BY seq, id", (r["id"],)):
        ud = days.get(ln["id"], 0)
        if ud <= EPS or not ln["rate"]:
            continue
        months = round(months_by.get(ln["id"], 0), 4)
        span = (_dt.date.fromisoformat(to) - _dt.date.fromisoformat(frm)).days + 1
        avg = ud / span
        out.append({"category": "إيجار" if ar else "Rent", "item_no": "", "unit": f"{ln['unit']}-{'شهر' if ar else 'month'}",
                    "description": (f"إيجار {ln['description']} من {frm} إلى {to} — متوسط {avg:,.2f} {ln['unit']} × {span} يوم"
                                    if ar else f"Rent: {ln['description']} {frm} to {to} — average {avg:,.2f} {ln['unit']} × {span} days"),
                    "qty": months, "price": to_major(ln["rate"]), "account": _role_or(db, "rental_revenue") or "", "unit_days": round(ud, 4)})
    charges = db.q("SELECT * FROM rental_charges WHERE rental_id=? AND extract_id IS NULL AND date<=? ORDER BY date, id", (r["id"], to))
    for c in charges:
        out.append({"category": ("تعويض" if ar else "Compensation") if c["kind"] in ("loss", "damage") else
                    ("حد أدنى للإيجار" if ar else "Minimum rent") if c["kind"] == "minimum" else ("مبالغ لمرة واحدة" if ar else "One-time"),
                    "item_no": "", "unit": "", "description": c["description"], "qty": c["qty"], "price": to_major(c["amount"]),
                    "account": c["account"] or _charge_account(db, c["kind"]), "charge_id": c["id"]})
    return {"from": frm, "to": to, "lines": out, "total": round(sum(x["qty"] * x["price"] for x in out), 2)}


def create_invoice(db, rid, d, user=""):
    """A draft sales invoice for a rent period plus the one-time and loss charges not invoiced yet."""
    r = get(db, rid)
    if r["status"] in ("quote", "cancelled"):
        raise ValidationError("Nothing to invoice yet")
    period = invoice_lines(db, r["id"], d.get("from"), d.get("to"), d.get("lang") or r["lang"] or "en", d.get("style") or "")
    if not period["lines"]:
        raise ValidationError("Nothing to invoice for this period")
    with db.tx():
        bu = _ensure_bu(db, r)
        n = db.one("SELECT COUNT(*) AS n FROM rental_invoices WHERE rental_id=?", (r["id"],))["n"] + 1
        number = text(d.get("number")) or f"{r['code']}-INV{n:02d}"
        while db.one("SELECT 1 FROM extracts WHERE number=?", (number,)):
            n += 1
            number = f"{r['code']}-INV{n:02d}"
        xid = acc.save_extract(db, {"bu_id": bu, "number": number, "date": d.get("date") or period["to"], "customer": r["client"],
                                    "vat_code": r["vat_code"], "wht_code": r["wht_code"], "lines": period["lines"]})
        db.conn.execute("INSERT INTO rental_invoices(rental_id,extract_id,period_from,period_to) VALUES (?,?,?,?)",
                        (r["id"], xid, period["from"], period["to"]))
        ids = [x["charge_id"] for x in period["lines"] if x.get("charge_id")]
        for cid in ids:
            db.conn.execute("UPDATE rental_charges SET extract_id=? WHERE id=?", (xid, cid))
        if d.get("post"):
            acc.post_extract(db, xid, user=user)
    return xid


def import_delivery(db, rid, date, our_name, client_name, invoiced_to=None, user=""):
    """A contract that came from Excel already on site: the full signed delivery on `date` (pieces leave the yard),
    and, when rent was already billed on an extract up to `invoiced_to`, that period and the one-time delivery charges
    are linked to the site's latest extract so the app bills from the next day — nothing twice."""
    r = get(db, rid)
    tpl = handover_template(db, r["id"], "delivery")
    tpl.update(date=date, our_name=our_name or "—", client_name=client_name, signed=True, decision="safe",
               checklist=[{**c, "ok": "yes"} for c in tpl.get("checklist") or []], notes="Imported from Excel")
    hid = save_handover(db, r["id"], tpl)
    confirm_handover(db, hid, user)
    if invoiced_to:
        r = get(db, rid)
        bu = _ensure_bu(db, r)
        x = db.one("SELECT id FROM extracts WHERE bu_id=? ORDER BY date DESC, id DESC LIMIT 1", (bu,))
        if not x:
            raise ValidationError(f"{r['code']}: 'Invoiced up to' needs the extract on Sales Invoices (same business unit)")
        db.conn.execute("INSERT INTO rental_invoices(rental_id,extract_id,period_from,period_to) VALUES (?,?,?,?)",
                        (r["id"], x["id"], date, invoiced_to))
        db.conn.execute("UPDATE rental_charges SET extract_id=? WHERE rental_id=? AND extract_id IS NULL", (x["id"], r["id"]))
    return hid


# ---------------------------------------------------------------- backups
TABLES = ("rent_items", "rent_kits", "rent_kit_lines", "rent_lots", "rent_models", "rentals", "rental_lines", "rental_handovers",
          "rent_moves", "rental_charges", "rental_invoices")


def export_rows(db):
    out = {t: db.q(f"SELECT * FROM {t} ORDER BY id") for t in TABLES}
    bus = {b["id"]: b["code"] for b in db.q("SELECT id, code FROM business_units")}
    xs = {x["id"]: x["number"] for x in db.q("SELECT id, number FROM extracts")}
    for r in out["rentals"]:
        r["bu_code"] = bus.get(r["bu_id"], "")
    for t in ("rental_charges", "rental_invoices"):
        for r in out[t]:
            r["extract_number"] = xs.get(r["extract_id"], "")
    return out


def import_rows(db, data, counts):
    """Replace the rental tables with a backup's (business units and invoices are matched by code / number)."""
    if not isinstance(data, dict):
        return
    for t in reversed(TABLES):
        db.conn.execute(f"DELETE FROM {t}")
    bus = {b["code"]: b["id"] for b in db.q("SELECT id, code FROM business_units")}
    xs = {x["number"]: x["id"] for x in db.q("SELECT id, number FROM extracts")}
    for t in TABLES:
        rows = data.get(t) or []
        cols = [c[1] for c in db.conn.execute(f"PRAGMA table_info({t})")]
        for r in rows:
            if not isinstance(r, dict):
                continue
            r = dict(r)
            if t == "rentals":
                r["bu_id"] = bus.get(r.get("bu_code"))
            if t in ("rental_charges", "rental_invoices"):
                r["extract_id"] = xs.get(r.get("extract_number"))
                if t == "rental_invoices" and not r["extract_id"]:
                    continue
            keep = [c for c in cols if c in r]
            db.conn.execute(f"INSERT INTO {t}({','.join(keep)}) VALUES ({','.join('?' * len(keep))})", [r[c] for c in keep])
        if rows:
            counts[t] = len(rows)
