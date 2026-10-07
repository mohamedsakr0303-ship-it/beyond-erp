"""Dashboards per group of users.

Every user belongs to a group (Management, Sales, Accounts & finance, …). Each group has its own dashboard: a list of
widgets, their order and size, and options such as a sales target. Only super administrators change them. A widget
shows only to people allowed to see that part of the ERP — unless a super administrator ticked "show even without
access" for that widget in that group (checked here, on the server, for every request).
"""
import datetime as _dt
import json

from . import accounting as acc
from .accounting import ValidationError, text
from .db import to_major

# ------------------------------------------------------------------ groups
DEFAULT_GROUPS = [   # key, English, Arabic, widgets
    ("management", "Management", "الإدارة",
     ["revenue", "costs", "net_profit", "cash", "money_position", "attention", "monthly_chart", "top_projects",
      "sales_target", "installments_overdue", "payables_due", "partner_balances"]),
    ("finance", "Accounts & finance", "الحسابات والمالية",
     ["cash", "net_profit", "receivables_top", "payables_due", "money_position", "attention", "monthly_chart",
      "loans_due", "recurring_due", "cheques_due", "books_check"]),
    ("sales", "Sales", "المبيعات",
     ["sales_target", "installments_upcoming", "installments_overdue", "cheques_due", "recent_sales", "offers_open",
      "receivables_top", "quick_links"]),
    ("projects", "Projects & engineering", "المشروعات والهندسة",
     ["active_projects", "top_projects", "bu_table", "rentals_ongoing", "po_open", "custody_open", "quick_links"]),
    ("purchasing", "Purchasing & stores", "المشتريات والمخازن",
     ["po_open", "payables_due", "custody_open", "inventory_value", "quick_links"]),
    ("hr", "HR", "الموارد البشرية", ["headcount", "payroll_status", "custody_open", "quick_links"]),
    ("general", "General", "عام", ["quick_links", "attention"]),
]
FALLBACK = "general"


def seed(db):
    """Create the starting groups (once)."""
    if db.one("SELECT 1 FROM user_groups"):
        return
    for i, (key, en, ar, ws) in enumerate(DEFAULT_GROUPS):
        db.conn.execute("INSERT INTO user_groups(key,name,name_ar,layout,sort) VALUES (?,?,?,?,?)",
                        (key, en, ar, json.dumps([{"w": w} for w in ws]), i))


def groups(db):
    seed(db)
    out = []
    for g in db.q("SELECT * FROM user_groups ORDER BY sort, id"):
        out.append({"key": g["key"], "name": g["name"], "name_ar": g["name_ar"], "layout": _clean_layout(g["layout"]),
                    "users": db.one("SELECT COUNT(*) n FROM users WHERE group_key=? AND active=1", (g["key"],))["n"]})
    return out


def group_of(db, user):
    seed(db)
    key = (user or {}).get("group_key") or ""
    if not key or not db.one("SELECT 1 FROM user_groups WHERE key=?", (key,)):
        key = "management" if (user or {}).get("is_super") else FALLBACK
    return key


def _clean_layout(raw):
    try:
        items = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        items = []
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, str):
            it = {"w": it}
        if not isinstance(it, dict) or it.get("w") not in WIDGETS:
            continue
        e = {"w": it["w"]}
        if it.get("size") in (1, 2, 4):
            e["size"] = it["size"]
        if it.get("grant"):
            e["grant"] = True
        opts = it.get("opts") if isinstance(it.get("opts"), dict) else {}
        allowed = WIDGETS[it["w"]].get("opts", {})
        e["opts"] = {k: v for k, v in opts.items() if k in allowed}
        out.append(e)
    return out


def save_group(db, key, d):
    key = text(key, "group")
    name = text(d.get("name"), "name", required=not db.one("SELECT 1 FROM user_groups WHERE key=?", (key,)))
    layout = _clean_layout(d.get("layout") if "layout" in d else None) if "layout" in d else None
    if db.one("SELECT 1 FROM user_groups WHERE key=?", (key,)):
        if name:
            db.conn.execute("UPDATE user_groups SET name=?, name_ar=? WHERE key=?", (name, text(d.get("name_ar")), key))
        if layout is not None:
            db.conn.execute("UPDATE user_groups SET layout=? WHERE key=?", (json.dumps(layout), key))
    else:
        base = d.get("copy_from")
        if layout is None and base:
            g = db.one("SELECT layout FROM user_groups WHERE key=?", (base,))
            layout = _clean_layout(g["layout"]) if g else []
        n = db.one("SELECT COALESCE(MAX(sort),0)+1 AS n FROM user_groups")["n"]
        db.conn.execute("INSERT INTO user_groups(key,name,name_ar,layout,sort) VALUES (?,?,?,?,?)",
                        (key, name, text(d.get("name_ar")), json.dumps(layout or []), n))
    return key


def new_key(db, name):
    base = "".join(c if c.isalnum() else "_" for c in (name or "group").lower()).strip("_")[:24] or "group"
    key, i = base, 1
    while db.one("SELECT 1 FROM user_groups WHERE key=?", (key,)):
        i += 1
        key = f"{base}_{i}"
    return key


def delete_group(db, key):
    if key == FALLBACK:
        raise ValidationError("The General group is everyone's fallback and cannot be deleted")
    db.conn.execute("UPDATE users SET group_key='' WHERE group_key=?", (key,))
    db.conn.execute("DELETE FROM user_groups WHERE key=?", (key,))


# ------------------------------------------------------------------ widgets
CATS = {"overview": ("Money & overview", "المال ونظرة عامة"), "sales": ("Sales & collections", "المبيعات والتحصيل"),
        "projects": ("Projects", "المشروعات"), "purchasing": ("Purchasing & stores", "المشتريات والمخازن"),
        "hr": ("HR", "الموارد البشرية"), "general": ("General", "عام")}


def _period(q):
    return q.get("date_from") or acc.today()[:4] + "-01-01", q.get("date_to") or None


class Ctx:
    """What a widget needs: the books, the request, and a shared copy of the money dashboard (computed once)."""
    def __init__(self, db, user, q, can):
        self.db, self.user, self.q, self.can = db, user, q, can
        self._dash = None

    @property
    def dash(self):
        if self._dash is None:
            f, t = _period(self.q)
            self._dash = acc.dashboard(self.db, f, t, self.q.get("bu_id") or None)
        return self._dash


def _sales_total(db, date_from, date_to):
    """Sales invoiced in a period: revenue credited by sales invoices (projects, shop orders, rentals, services)."""
    r = db.one("""SELECT COALESCE(SUM(l.credit-l.debit),0) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                  JOIN accounts a ON a.code=l.account WHERE e.status='posted' AND e.kind='Sales Invoice' AND a.type='Revenue'
                  AND e.date>=? AND e.date<=?""", (date_from, date_to))
    return to_major(r["n"])


def _period_range(kind):
    today = _dt.date.fromisoformat(acc.today())
    if kind == "quarter":
        m = (today.month - 1) // 3 * 3 + 1
        start = today.replace(month=m, day=1)
        end = (start.replace(month=m + 2, day=28) + _dt.timedelta(days=4)).replace(day=1) - _dt.timedelta(days=1)
    elif kind == "year":
        start, end = today.replace(month=1, day=1), today.replace(month=12, day=31)
    else:
        start = today.replace(day=1)
        end = (start.replace(day=28) + _dt.timedelta(days=4)).replace(day=1) - _dt.timedelta(days=1)
    return start.isoformat(), end.isoformat(), today.isoformat()


def w_kpi(key, label_en, label_ar):
    def fn(c, o):
        d = c.dash
        ser = d.get("series") or []
        if key == "costs":
            return {"value": round(sum(s["costs"] for s in ser), 2), "series": [s["costs"] for s in ser], "label": [label_en, label_ar]}
        if key == "net_profit":
            return {"value": d["net_profit"], "series": [s["revenue"] - s["costs"] for s in ser], "chip": d["margins"]["net"],
                    "label": [label_en, label_ar]}
        if key == "cash":
            return {"value": d["cash"], "floor": d["cash_position"], "label": [label_en, label_ar]}
        return {"value": d["revenue"], "series": [s["revenue"] for s in ser], "label": [label_en, label_ar]}
    return fn


def w_money_position(c, o):
    d = c.dash
    return {k: d.get(k, 0) for k in ("receivable", "payable", "advances", "accrued", "inventory", "ebitda")} | {"margins": d["margins"],
                                                                                                            "cash_position": d["cash_position"]}


def w_monthly(c, o):
    return {"series": c.dash.get("series") or []}


def w_bu_table(c, o):
    return {"units": c.dash.get("units") or []}


def w_top_projects(c, o):
    rows = sorted(c.dash.get("units") or [], key=lambda b: -(b.get("net_profit") or 0))[: int(o.get("count") or 5)]
    return {"rows": [{"title": b["code"], "sub": b["name"], "value": b["net_profit"], "href": f"#/bu/{b['id']}",
                      "level": "bad" if (b.get("net_profit") or 0) < 0 else "ok"} for b in rows]}


def w_active_projects(c, o):
    rows = [b for b in (c.dash.get("units") or []) if b.get("status") not in ("Completed", "Closed")]
    return {"value": len(rows), "sub": [f"{sum(1 for b in rows if (b.get('net_profit') or 0) < 0)} at a loss",
                                        f"{sum(1 for b in rows if (b.get('net_profit') or 0) < 0)} بخسارة"],
            "href": "#/bus", "count": True}


def w_attention(c, o):
    return {"rows": alerts(c.db, c.can)}


def w_books_check(c, o):
    return {"checks": acc.audit(c.db)}


def w_sales_target(c, o):
    period = o.get("period") or "month"
    start, end, today = _period_range(period)
    target = float(o.get("target") or 0)
    if o.get("mine"):
        # the viewer's own sales: shop orders that name them as the salesperson
        names = sorted({x for x in ((c.user or {}).get("full_name"), (c.user or {}).get("username")) if x})
        rows = c.db.q("SELECT price FROM sales_orders WHERE status IN ('confirmed','delivered') AND date>=? AND date<=? AND salesperson IN (%s)"
                      % ",".join("?" * len(names)), [start, end, *names]) if names else []
        done = to_major(sum(x["price"] for x in rows))
    else:
        done = _sales_total(c.db, start, end)
    span = (_dt.date.fromisoformat(end) - _dt.date.fromisoformat(start)).days + 1
    gone = (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(start)).days + 1
    return {"done": done, "target": target, "period": period, "from": start, "to": end, "mine": bool(o.get("mine")),
            "expected": round(target * gone / span, 2) if target else 0, "days_left": max(span - gone, 0)}


def w_installments_upcoming(c, o):
    from . import sales
    days = int(o.get("days") or 30)
    upto = (_dt.date.fromisoformat(acc.today()) + _dt.timedelta(days=days)).isoformat()
    r = sales.installments(c.db, status="open", date_from=acc.today(), date_to=upto)
    return {"days": days, "total": r["totals"]["left"],
            "rows": [{"title": x["customer"], "sub": f"{x['code']} · {x['due']}", "value": x["left"], "href": f"#/sale/{x['sale_id']}",
                      "level": "warn" if x["due"] <= acc.today() else "info"} for x in r["rows"][:12]]}


def w_installments_overdue(c, o):
    from . import sales
    r = sales.installments(c.db, status="overdue")
    return {"value": r["totals"]["left"], "count": len(r["rows"]), "customers": len({x["customer"] for x in r["rows"]}),
            "rows": [{"title": x["customer"], "sub": f"{x['code']} · {x['due']}", "value": x["left"], "href": f"#/sale/{x['sale_id']}",
                      "level": "bad"} for x in r["rows"][:8]], "href": "#/installments?status=overdue"}


def w_cheques_due(c, o):
    from . import sales
    upto = (_dt.date.fromisoformat(acc.today()) + _dt.timedelta(days=int(o.get("days") or 14))).isoformat()
    rows = sales.cheques_due(c.db, upto)
    return {"rows": [{"title": f"{x['customer']} — {x.get('number') or ''}".strip(" —"), "sub": f"{x['code']} · {x['due']}",
                      "value": to_major(x["amount"]), "href": f"#/sale/{x['sale_id']}",
                      "level": "warn" if x["due"] <= acc.today() else "info"} for x in rows[:10]],
            "total": to_major(sum(x["amount"] for x in rows))}


def w_recent_sales(c, o):
    rows = c.db.q("SELECT id, code, date, customer, status, price, salesperson FROM sales_orders WHERE status<>'cancelled' "
                  "ORDER BY date DESC, id DESC LIMIT ?", (int(o.get("count") or 8),))
    return {"rows": [{"title": x["customer"], "sub": f"{x['code']} · {x['date']}" + (f" · {x['salesperson']}" if x["salesperson"] else ""),
                      "value": to_major(x["price"]), "href": f"#/sale/{x['id']}", "level": "info" if x["status"] == "draft" else "ok"}
                     for x in rows]}


def w_offers_open(c, o):
    rows = c.db.q("SELECT id, number, date, client, project, status FROM offers WHERE status NOT IN ('Accepted','Rejected','Cancelled') "
                  "ORDER BY date DESC LIMIT 10")
    return {"rows": [{"title": x["client"] or x["number"], "sub": f"{x['number']} · {x['date']} · {x['status']}", "href": f"#/offer/{x['id']}",
                      "level": "info"} for x in rows]}


def w_receivables_top(c, o):
    from . import statements
    ag = statements.aging(c.db, side="receivable")
    rows = [r for r in ag["rows"] if r["balance"] > 0][: int(o.get("count") or 8)]
    return {"total": ag["receivable"]["balance"], "rows": [{"title": r["party"], "sub": f"{r['last']}", "value": r["balance"],
                                                            "href": "#/statement?" + _q(r["party"]),
                                                            "level": "bad" if sum(r["aging"][2:]) > 0 else "info"} for r in rows]}


def w_payables_due(c, o):
    from . import statements
    ag = statements.aging(c.db, side="payable")
    rows = [r for r in ag["rows"] if r["balance"] < 0][: int(o.get("count") or 8)]
    return {"total": -ag["payable"]["balance"], "rows": [{"title": r["party"], "sub": r["last"], "value": -r["balance"],
                                                          "href": "#/statement?" + _q(r["party"]),
                                                          "level": "warn" if sum(r["aging"][2:]) else "info"} for r in rows]}


def _q(party):
    from urllib.parse import urlencode
    return urlencode({"party": party})


def w_po_open(c, o):
    from . import purchasing
    r = purchasing.listing(c.db)
    rows = [x for x in r["rows"] if x["status"] in ("draft", "approved", "partial")][:10]
    return {"to_approve": r.get("to_approve", 0), "open_value": r.get("open_value", 0),
            "rows": [{"title": x["supplier"], "sub": f"{x['code']} · {x['status']}" + (" · overdue" if x.get("overdue") else ""),
                      "value": x["grand_total"], "href": f"#/po/{x['id']}", "level": "warn" if x["status"] == "draft" or x.get("overdue") else "info"}
                     for x in rows]}


def w_custody_open(c, o):
    from . import custody
    r = custody.register(c.db, state="open")
    rows = [x for x in r["rows"] if x["status"] == "open"][:10]
    return {"rows": [{"title": x["holder"], "sub": f"{x['code']} · {x['purpose'] or ''}".strip(" ·"), "value": x["balance"],
                      "href": f"#/custody/{x['id']}", "level": "warn" if x.get("due_date") and x["due_date"] < acc.today() else "info"}
                     for x in rows]}


def w_rentals(c, o):
    from . import rentals
    r = rentals.ongoing(c.db)
    return {"monthly": r.get("monthly", 0), "rows": [{"title": x["client"], "sub": f"{x['code']} · {x.get('site') or ''}".strip(" ·"),
                                                       "value": x["owed"], "href": f"#/rental/{x['id']}", "level": "warn" if x.get("to_bill") else "ok"}
                                                      for x in r["rows"][:10]]}


def w_loans_due(c, o):
    from . import loans
    r = loans.register(c.db, state="active")
    rows = sorted([x for x in r["rows"] if x.get("next_due")], key=lambda x: x["next_due"])[:8]
    return {"rows": [{"title": f"{x['party']}", "sub": f"{x['code']} · {x['next_due']}" + (" · " + ("we pay" if x["direction"] == "borrowed" else "we collect")),
                      "value": x["next_amount"], "href": f"#/loan/{x['id']}", "level": "bad" if x["overdue"] else "info"} for x in rows]}


def w_recurring_due(c, o):
    from . import recurring
    due = recurring.due_list(c.db, acc.today(), lambda m: c.can(m, "add"))
    return {"value": len(due), "count": True, "href": "#/recurring", "sub": ["entries waiting", "قيود بانتظار التسجيل"]}


def w_partner_balances(c, o):
    rep = acc.partner_report(c.db)
    return {"rows": [{"title": p["name"], "sub": f"{round((p['default_share'] or 0) * 100, 2)}%", "value": p["accounts"]["capital"] + p["accounts"]["current"],
                      "href": "#/partners", "level": "ok"} for p in rep["partners"] if p["active"]]}


def w_inventory(c, o):
    return {"value": c.dash.get("inventory", 0), "href": "#/stock"}


def w_headcount(c, o):
    n = c.db.one("SELECT COUNT(*) n FROM employees WHERE status='active'")["n"]
    basic = c.db.one("SELECT COALESCE(SUM(basic),0) s FROM employees WHERE status='active'")["s"]
    return {"value": n, "count": True, "sub": [f"monthly basic {to_major(basic):,.2f}", f"الأساسي الشهري {to_major(basic):,.2f}"], "href": "#/employees"}


def w_payroll_status(c, o):
    last = c.db.one("SELECT * FROM payroll_runs ORDER BY period DESC LIMIT 1")
    this = acc.today()[:7]
    return {"last": dict(last) if last else None, "this_month": this,
            "this_done": bool(c.db.one("SELECT 1 FROM payroll_runs WHERE period=? AND status='posted'", (this,))), "href": "#/payroll"}


def w_quick_links(c, o):
    return {}   # drawn in the page from the screens this person may open


# id: (en, ar, category, permission (any of), size, function, {option: (en, ar, kind)})
_W = [
    ("revenue", "Revenue", "الإيرادات", "overview", ("reports", "dashboard"), 1, w_kpi("revenue", "Revenue", "الإيرادات"), {}),
    ("costs", "Costs", "التكاليف", "overview", ("reports", "dashboard"), 1, w_kpi("costs", "Costs", "التكاليف"), {}),
    ("net_profit", "Net profit", "صافي الربح", "overview", ("reports", "dashboard"), 1, w_kpi("net_profit", "Net profit", "صافي الربح"), {}),
    ("cash", "Cash & bank", "النقدية والبنوك", "overview", ("assets.cash", "dashboard"), 1, w_kpi("cash", "Cash & bank", "النقدية والبنوك"), {}),
    ("money_position", "Money position", "المركز المالي", "overview", ("reports", "dashboard"), 2, w_money_position, {}),
    ("monthly_chart", "Revenue vs costs by month", "الإيرادات مقابل التكاليف شهرياً", "overview", ("reports", "dashboard"), 2, w_monthly, {}),
    ("attention", "Needs attention", "يحتاج متابعة", "general", (), 2, w_attention, {}),
    ("books_check", "Books check", "فحص الدفاتر", "overview", ("reports",), 2, w_books_check, {}),
    ("partner_balances", "Partners' balances", "أرصدة الشركاء", "overview", ("partners.equity",), 2, w_partner_balances, {}),
    ("loans_due", "Loan instalments due", "أقساط القروض المستحقة", "overview", ("accounting.loans",), 2, w_loans_due, {}),
    ("recurring_due", "Recurring entries due", "قيود متكررة مستحقة", "overview", ("accounting.recurring",), 1, w_recurring_due, {}),
    ("sales_target", "Sales target", "المستهدف البيعي", "sales", ("sales.invoices", "shop.orders"), 2, w_sales_target,
     {"target": ("Target amount", "قيمة المستهدف", "number"), "period": ("Period", "الفترة", "period"),
      "mine": ("Only the viewer's own sales (salesperson)", "مبيعات المستخدم نفسه فقط (مندوب المبيعات)", "check")}),
    ("installments_upcoming", "Upcoming instalments", "أقساط قادمة", "sales", ("shop.installments",), 2, w_installments_upcoming,
     {"days": ("Days ahead", "عدد الأيام القادمة", "number")}),
    ("installments_overdue", "Overdue instalments", "أقساط متأخرة", "sales", ("shop.installments",), 2, w_installments_overdue, {}),
    ("cheques_due", "Customer cheques due", "شيكات العملاء المستحقة", "sales", ("shop.installments",), 2, w_cheques_due,
     {"days": ("Days ahead", "عدد الأيام القادمة", "number")}),
    ("recent_sales", "Latest sales", "أحدث المبيعات", "sales", ("shop.orders",), 2, w_recent_sales, {"count": ("How many", "العدد", "number")}),
    ("offers_open", "Open offers", "عروض الأسعار المفتوحة", "sales", ("sales.offers",), 2, w_offers_open, {}),
    ("receivables_top", "Who owes us most", "أكبر المديونيات لنا", "sales", ("sales.receivables",), 2, w_receivables_top,
     {"count": ("How many", "العدد", "number")}),
    ("active_projects", "Active projects", "المشروعات الجارية", "projects", ("business_units.units",), 1, w_active_projects, {}),
    ("top_projects", "Most profitable projects", "أكثر المشروعات ربحاً", "projects", ("business_units.profitability",), 2, w_top_projects,
     {"count": ("How many", "العدد", "number")}),
    ("bu_table", "Business units table", "جدول وحدات الأعمال", "projects", ("business_units.profitability",), 4, w_bu_table, {}),
    ("rentals_ongoing", "Ongoing rentals", "الإيجارات الجارية", "projects", ("rentals.contracts",), 2, w_rentals, {}),
    ("po_open", "Open purchase orders", "أوامر الشراء المفتوحة", "purchasing", ("purchasing.orders",), 2, w_po_open, {}),
    ("payables_due", "What we owe suppliers", "المستحق للموردين", "purchasing", ("purchasing.payables",), 2, w_payables_due,
     {"count": ("How many", "العدد", "number")}),
    ("custody_open", "Open cash custody", "العهد المفتوحة", "purchasing", ("purchasing.custody",), 2, w_custody_open, {}),
    ("inventory_value", "Stock value", "قيمة المخزون", "purchasing", ("stock.balances",), 1, w_inventory, {}),
    ("headcount", "Employees", "الموظفون", "hr", ("payroll.employees",), 1, w_headcount, {}),
    ("payroll_status", "This month's payroll", "رواتب هذا الشهر", "hr", ("payroll.runs",), 1, w_payroll_status, {}),
    ("quick_links", "Quick links", "روابط سريعة", "general", (), 2, w_quick_links, {}),
]
WIDGETS = {w[0]: {"id": w[0], "en": w[1], "ar": w[2], "cat": w[3], "perm": w[4], "size": w[5], "fn": w[6], "opts": w[7]} for w in _W}


def catalog():
    return [{k: v for k, v in w.items() if k != "fn"} | {"opts": {k: list(v) for k, v in w["opts"].items()}} for w in WIDGETS.values()]


def allowed(w, can):
    return not w["perm"] or any(can(p) for p in w["perm"])


def view(db, user, q, can, group=None):
    """The dashboard of a group as this person may see it: the layout and each widget's figures. Only super
    administrators may look at another group's dashboard."""
    is_super = bool((user or {}).get("is_super"))
    key = group if (group and is_super) else group_of(db, user)
    g = db.one("SELECT * FROM user_groups WHERE key=?", (key,))
    if not g:
        raise ValidationError("Unknown group")
    layout = _clean_layout(g["layout"])
    c = Ctx(db, user, q, can)
    out = []
    for e in layout:
        w = WIDGETS[e["w"]]
        item = {**e, "size": e.get("size") or w["size"], "en": w["en"], "ar": w["ar"], "cat": w["cat"]}
        if not (allowed(w, can) or e.get("grant") or (is_super and group)):
            item["hidden"] = True     # not for this person (their permissions) — no figures are sent
        else:
            try:
                item["data"] = w["fn"](c, e.get("opts") or {})
            except Exception as ex:   # one widget never breaks the page
                item["error"] = str(ex)[:200]
        out.append(item)
    return {"group": key, "name": g["name"], "name_ar": g["name_ar"], "widgets": [x for x in out if not x.get("hidden")]
            if not is_super else out}


# ------------------------------------------------------------------ alerts (the bell and the "Needs attention" widget)
def alerts(db, can):
    from . import assets, recurring, rentals, sales
    out = []

    def add(level, en, ar, href, n=1):
        out.append({"level": level, "en": en, "ar": ar, "href": href, "count": n})
    if can("rentals.contracts"):
        for r in rentals.ongoing(db)["rows"]:
            if r.get("to_bill"):
                add("warn", f"{r['code']} — bill from {r['next_from']}", f"{r['code']} — فوترة من {r['next_from']}", f"#/rental/{r['id']}")
    if can("shop.installments") or can("shop.orders"):
        o = sales.overdue_summary(db)
        if o:
            add("bad", f"Overdue installments: {o['count']}", f"أقساط متأخرة: {o['count']}", "#/installments?status=overdue", o["count"])
        c = len(sales.cheques_due(db))
        if c:
            add("warn", f"Customer cheques due: {c}", f"شيكات عملاء مستحقة: {c}", "#/installments", c)
    if can("accounting.recurring"):
        due = recurring.due_list(db, acc.today(), lambda m: can(m, "add"))
        if due:
            add("info", f"Recurring entries due: {len(due)}", f"قيود متكررة مستحقة: {len(due)}", "#/recurring", len(due))
    if can("assets.depreciation", "add"):
        d = assets.due(db)
        if d:
            add("warn", f"Depreciation not booked up to {d['through']}", f"لم يُحتسب الإهلاك حتى {d['through']}", "#/depreciation", d["count"])
    if can("reports"):
        bad = [a for a in acc.audit(db) if not a["ok"]]
        if bad:
            add("bad", f"Books check: {len(bad)} to review", f"فحص الدفاتر: {len(bad)} للمراجعة", "#/dashboard", len(bad))
    return out
