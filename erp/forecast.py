"""Forecasting: where prices, costs, sales and depreciation are heading over the next quarters.

Nothing is imported here: the forecast reads what the books already hold (the price list, items and what was last paid
for them, a year of spending per account, payroll, the asset register, loan schedules, sales history) and moves it
forward with a few drivers:

* Drivers, per quarter: local prices (CPI), wages, the US dollar against the pound, fuel & energy, global prices.
  Each has its source written next to it. "Fetch latest" fills CPI, the dollar and global prices from the World Bank's
  open data (the page does that — it works in every edition); wages and energy are the company's own judgement and
  start equal to CPI until changed. No number is made up: an empty driver means 0 %.
* Cost categories mix those drivers (how much of a cement bag, a carpenter's day or a marble metre is local material,
  imported content, labour, energy). Imported content moves with the dollar and global prices together.
* Every figure can be overridden for a quarter; overrides are kept and shown as edited.

Assumptions are one setting ("forecast"), so they travel with the books and the shared workspace.
"""
import datetime as _dt
import json

from . import accounting as acc
from .db import to_major

DRIVERS = [("cpi", "Local prices (CPI)", "الأسعار المحلية (التضخم)"), ("wages", "Wages & trades", "الأجور والعمالة"),
           ("fx", "US dollar vs pound", "الدولار مقابل الجنيه"), ("fuel", "Fuel & energy", "الوقود والطاقة"),
           ("global", "Global prices (in dollars)", "الأسعار العالمية (بالدولار)")]
MIX_KEYS = ("local", "imported", "labour", "energy")
PRESETS = {   # quick starting points for a category's mix (shares of local material, imported content, labour, energy)
    "general": {"local": 1}, "local_materials": {"local": .7, "imported": .1, "labour": .05, "energy": .15},
    "imported_materials": {"local": .1, "imported": .75, "labour": .05, "energy": .1}, "labour": {"labour": 1},
    "works": {"local": .3, "imported": .15, "labour": .45, "energy": .1}, "energy": {"local": .1, "labour": .3, "energy": .6},
}
SECTIONS = ("prices", "items", "expenses", "payroll", "depreciation", "loans", "sales")


def quarters(n=4, today=None):
    d = today or _dt.date.fromisoformat(acc.today())
    q, y = (d.month - 1) // 3 + 2, d.year           # starts with the next quarter
    out = []
    for _ in range(n):
        if q > 4:
            q, y = 1, y + 1
        out.append({"key": f"{y}-Q{q}", "label": f"Q{q} {y}", "from": f"{y}-{3 * q - 2:02d}-01",
                    "to": (_dt.date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1) - _dt.timedelta(days=1)).isoformat()})
        q += 1
    return out


def assumptions(db):
    try:
        a = json.loads(db.setting("forecast") or "{}")
    except ValueError:
        a = {}
    a.setdefault("drivers", {})
    a.setdefault("mixes", {})
    a.setdefault("assign", {})
    a.setdefault("overrides", {})
    a.setdefault("horizon", 4)
    return a


def save(db, d):
    cur = assumptions(db)
    out = {"horizon": max(2, min(12, int(d.get("horizon") or cur["horizon"] or 4)))}
    drv = {}
    for k, _en, _ar in DRIVERS:
        x = (d.get("drivers") or cur["drivers"]).get(k) or {}
        drv[k] = {"rates": [float(r or 0) for r in (x.get("rates") or [])][:12], "source": acc.text(x.get("source"), maxlen=300),
                  "as_of": acc.text(x.get("as_of"), maxlen=20)}
    out["drivers"] = drv
    mixes = {}
    for cat, m in (d.get("mixes") if "mixes" in d else cur["mixes"]).items():
        m = {k: max(0.0, float((m or {}).get(k) or 0)) for k in MIX_KEYS}
        tot = sum(m.values())
        if tot:
            mixes[acc.text(cat, maxlen=120)] = {k: round(v / tot, 4) for k, v in m.items()}
    out["mixes"] = mixes
    out["assign"] = {acc.text(k, maxlen=80): acc.text(v, maxlen=120) for k, v in (d.get("assign") if "assign" in d else cur["assign"]).items() if v}
    ov = {}
    for k, row in (d.get("overrides") if "overrides" in d else cur["overrides"]).items():
        clean = {str(int(i)): float(v) for i, v in (row or {}).items() if v not in ("", None)}
        if clean:
            ov[acc.text(k, maxlen=120)] = clean
    out["overrides"] = ov
    db.set_setting("forecast", json.dumps(out, ensure_ascii=False))
    return out


def _driver_rates(a, n):
    out = {}
    for k, _en, _ar in DRIVERS:
        r = list((a["drivers"].get(k) or {}).get("rates") or [])
        out[k] = [(r[i] if i < len(r) else (r[-1] if r else 0.0)) for i in range(n)]   # the last quarter carries on
    return out


def escalation(a, n):
    """Per-quarter growth of each building block, from the drivers."""
    dr = _driver_rates(a, n)
    blocks = {"local": dr["cpi"], "labour": dr["wages"], "energy": dr["fuel"],
              "imported": [(1 + dr["fx"][i]) * (1 + dr["global"][i]) - 1 for i in range(n)]}
    return dr, blocks


def cat_rates(a, blocks, cat, n):
    m = a["mixes"].get(cat) or PRESETS.get(cat) or PRESETS["general"]
    return [sum(m.get(k, 0) * blocks[k][i] for k in MIX_KEYS) for i in range(n)]


def _path(base, rates):
    out, v = [], float(base or 0)
    for r in rates:
        v *= 1 + r
        out.append(round(v, 2))
    return out


def _quarter_actuals(db, accounts_type, last_n=4, today=None):
    """Posted amounts per account for the last n full quarters, oldest first."""
    d = today or _dt.date.fromisoformat(acc.today())
    q, y = (d.month - 1) // 3 + 1, d.year
    spans = []
    for _ in range(last_n):
        q -= 1
        if q < 1:
            q, y = 4, y - 1
        spans.append((f"{y}-{3 * q - 2:02d}-01", (_dt.date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1) - _dt.timedelta(days=1)).isoformat()))
    spans.reverse()
    sign = "l.debit - l.credit" if accounts_type == "Expense" else "l.credit - l.debit"
    rows = db.q(f"""SELECT a.code, a.name, a.name_ar, a.subtype, e.date, SUM({sign}) AS amt FROM journal_lines l
                    JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                    WHERE a.type=? AND e.status='posted' AND e.date BETWEEN ? AND ?
                    GROUP BY a.code, e.date""", (accounts_type, spans[0][0], spans[-1][1]))
    acct = {}
    for r in rows:
        x = acct.setdefault(r["code"], {"code": r["code"], "name": r["name"], "name_ar": r["name_ar"], "subtype": r["subtype"], "q": [0] * last_n})
        for i, (f, t) in enumerate(spans):
            if f <= r["date"] <= t:
                x["q"][i] += r["amt"]
    return list(acct.values()), [f"{f[:7]}" for f, _ in spans]


def _trend(hist, n):
    """Least-squares straight line through the quarters seen, carried forward (never below zero)."""
    while hist and not hist[0]:      # quarters before the account was used do not count
        hist = hist[1:]
    if not hist:
        return [0.0] * n
    if len(hist) < 3:                # too little history for a trend: carry the recent average
        avg = round(sum(hist) / len(hist), 2)
        return [avg] * n
    pts = [(i, v) for i, v in enumerate(hist)]
    m = len(pts)
    if m == 1:
        return [max(0.0, pts[0][1])] * n
    sx = sum(i for i, _ in pts)
    sy = sum(v for _, v in pts)
    sxx = sum(i * i for i, _ in pts)
    sxy = sum(i * v for i, v in pts)
    den = m * sxx - sx * sx
    b = (m * sxy - sx * sy) / den if den else 0
    a0 = (sy - b * sx) / m
    return [max(0.0, round(a0 + b * (m + k), 2)) for k in range(n)]


def build(db, today=None):
    a = assumptions(db)
    n = a["horizon"]
    per = quarters(n, today)
    dr, blocks = escalation(a, n)
    ov = a["overrides"]
    sections = []

    def row(key, label, base, values, cat=None, sub="", unit=""):
        o = ov.get(key) or {}
        vals = [o.get(str(i), v) for i, v in enumerate(values)]
        return {"key": key, "label": label, "sub": sub, "unit": unit, "base": round(float(base or 0), 2), "values": vals,
                "edited": sorted(int(i) for i in o), "cat": cat}

    # 1. the price list (what clients are charged and what it costs), moved by its category
    rates = db.q("SELECT * FROM rates ORDER BY category, item_no, id")
    rr = []
    for r in rates:
        cat = a["assign"].get(f"rate:{r['id']}") or r["category"] or "general"
        g = cat_rates(a, blocks, cat, n)
        rr.append(row(f"rate:{r['id']}:price", f"{r['item_no'] + ' ' if r['item_no'] else ''}{r['description']}", to_major(r["price"]),
                      _path(to_major(r["price"]), g), cat, f"{r['category']} · {acc.text(r['unit'])}", r["unit"]))
    sections.append({"key": "prices", "rows": rr})
    # 2. items: the standard cost, else what was last paid
    items = db.q("""SELECT i.*, (SELECT rate FROM stock_moves WHERE item_id=i.id AND qty>0 AND kind='Receipt' ORDER BY date DESC, id DESC LIMIT 1) AS last_rate
                    FROM stock_items i ORDER BY i.category, i.name""")
    ir = []
    for it in items:
        base = to_major(it.get("std_cost") or 0) or to_major(it["last_rate"] or 0)
        if not base:
            continue
        cat = a["assign"].get(f"item:{it['id']}") or it.get("category") or "general"
        ir.append(row(f"item:{it['id']}", it["name"], base, _path(base, cat_rates(a, blocks, cat, n)), cat, it.get("category") or "", it["unit"]))
    sections.append({"key": "items", "rows": ir})
    # 3. spending per expense account: the last four quarters' average, moved by the account's category
    exp, hist_labels = _quarter_actuals(db, "Expense", 4, today)
    er = []
    for x in sorted(exp, key=lambda x: x["code"]):
        avg = to_major(sum(x["q"]) / 4)
        if not avg:
            continue
        labourish = any(w in (x["subtype"] or "") for w in ("payroll", "labour", "labor", "salar"))
        cat = a["assign"].get(f"acct:{x['code']}") or ("labour" if labourish else "general")
        er.append(row(f"acct:{x['code']}", f"{x['code']} {x['name']}", avg, _path(avg, cat_rates(a, blocks, cat, n)), cat,
                      " · ".join(f"{to_major(v):,.0f}" for v in x["q"])))
    sections.append({"key": "expenses", "rows": er, "history": hist_labels})
    # 4. payroll: active employees' monthly pay (basic + fixed allowances, or daily rate × 26), three months a quarter,
    #    moved by wages — the same people kept on; hires and leavers are the overrides
    pr = []
    for e in db.q("SELECT code, name, emp_type, basic, daily_rate, allowances FROM employees WHERE status='active' ORDER BY code"):
        try:
            allow = sum(int(v or 0) for v in json.loads(e["allowances"] or "{}").values())
        except (ValueError, TypeError):
            allow = 0
        monthly = (e["basic"] + allow) if e["emp_type"] == "monthly" else (e["daily_rate"] * 26 if e["emp_type"] == "daily" else 0)
        if monthly:
            base = to_major(monthly) * 3
            pr.append(row(f"emp:{e['code']}", f"{e['code']} {e['name']}", base, _path(base, dr["wages"]), "labour", e["emp_type"]))
    sections.append({"key": "payroll", "rows": pr})
    # 5. depreciation & amortisation from the asset register: what the policy books, quarter by quarter
    from . import assets
    dep = []
    pol = assets.policy(db)
    for ast in db.q("SELECT * FROM fixed_assets ORDER BY code"):
        st = assets.status(db, ast, pol=pol)
        if st["disposed"] or not st["next_month"]:
            continue
        months = assets.month_amounts(ast["cost"], ast["salvage"], ast["cost"] - round(st["book_value"] * 100), st["months_left"],
                                      n * 3 + 3, st["method"], st["factor"], ast["life_months"])
        start = st["next_month"]
        q = [0.0] * n
        for k, amt in enumerate(months):
            y, mth = int(start[:4]), int(start[5:7]) + k
            y += (mth - 1) // 12
            mth = (mth - 1) % 12 + 1
            key = f"{y}-Q{(mth - 1) // 3 + 1}"
            for i, p in enumerate(per):
                if p["key"] == key:
                    q[i] += to_major(amt)
        if any(q):
            dep.append(row(f"asset:{ast['code']}", f"{ast['code']} {ast['name']}", st["monthly"] * 3, [round(v, 2) for v in q]))
    sections.append({"key": "depreciation", "rows": dep})
    # 6. loan instalments falling due (principal and interest), from each loan's schedule
    from . import loans
    lr = []
    for ln in db.q("SELECT id FROM loans"):
        L = loans.get(db, ln["id"])
        if not L or L.get("status") in ("closed", "written_off"):
            continue
        prin, intr = [0.0] * n, [0.0] * n
        for s in loans.build_schedule(L):
            for i, p in enumerate(per):
                if p["from"] <= s["due"] <= p["to"]:
                    prin[i] += to_major(s["principal"])
                    intr[i] += to_major(s["interest"])
        if any(prin) or any(intr):
            sign = "↓" if L["direction"] == "borrowed" else "↑"
            lr.append(row(f"loan:{L['code']}:p", f"{sign} {L['code']} {L['party']} — principal", 0, prin))
            lr.append(row(f"loan:{L['code']}:i", f"{sign} {L['code']} {L['party']} — interest", 0, intr))
    sections.append({"key": "loans", "rows": lr})
    # 7. sales: each revenue account's last eight quarters, as a trend
    rev, rlabels = _quarter_actuals(db, "Revenue", 8, today)
    sr = []
    for x in sorted(rev, key=lambda x: x["code"]):
        hist = [to_major(v) for v in x["q"]]
        if not any(hist):
            continue
        sr.append(row(f"rev:{x['code']}", f"{x['code']} {x['name']}", sum(hist[-4:]) / 4, _trend(hist, n), None,
                      " · ".join(f"{v:,.0f}" for v in hist)))
    try:   # unit prices in the showroom, moved by their category
        for p in db.q("SELECT id, code, name, category, price, unit FROM products WHERE price>0 ORDER BY category, name"):
            cat = a["assign"].get(f"prod:{p['id']}") or p["category"] or "general"
            sr.append(row(f"prod:{p['id']}", f"{p['code']} {p['name']} — unit price", to_major(p["price"]),
                          _path(to_major(p["price"]), cat_rates(a, blocks, cat, n)), cat, p["category"] or "", p["unit"]))
    except Exception:  # noqa: BLE001 — books without the showroom
        pass
    sections.append({"key": "sales", "rows": sr, "history": rlabels})
    cats = sorted({r["cat"] for s in sections for r in s["rows"] if r.get("cat")} | set(a["mixes"]))
    return {"periods": per, "drivers": [{"key": k, "en": en, "ar": ar, **(a["drivers"].get(k) or {}), "rates": dr[k]} for k, en, ar in DRIVERS],
            "blocks": blocks, "categories": [{"name": c, "mix": a["mixes"].get(c) or PRESETS.get(c) or PRESETS["general"],
                                              "rates": cat_rates(a, blocks, c, n), "set": c in a["mixes"] or c in PRESETS} for c in cats],
            "presets": PRESETS, "horizon": n, "sections": sections}
