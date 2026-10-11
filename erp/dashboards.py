"""Dashboards per group of users.

Every user belongs to a group (Management, Sales, Accounts & finance, …). Each group has its own dashboard: a list of
widgets, their order and size, options such as a sales target, and each card's design (title, style, colour, chart
style, height) plus the page's own (heading, accent, spacing). Only super administrators change them — and people
they allow ("Design dashboards & groups"). A widget
shows only to people allowed to see that part of the ERP — unless a super administrator ticked "show even without
access" for that widget in that group (checked here, on the server, for every request).
"""
import datetime as _dt
import json
import re

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


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
CARD_STYLES = ("card", "glass", "solid", "outline", "gradient", "plain")


def _hex(v):
    return v.lower() if isinstance(v, str) and _HEX.match(v) else ""


def _link(h):
    """A button's target: a screen of the app (#/…) or a web address (https://…); anything else is dropped."""
    h = str(h or "").strip()[:400]
    return h if h.startswith("#/") or re.match(r"^https?://[^\s\"'<>]+$", h) else ""


def clean_look(lk):
    """A card's design, as the dashboard designer sets it. Unknown keys and values are dropped."""
    lk = lk if isinstance(lk, dict) else {}
    out = {}
    for k, n in (("title", 120), ("title_ar", 120), ("body", 4000), ("body_ar", 4000)):
        v = text(lk.get(k), maxlen=n) if lk.get(k) not in (None, "") else ""
        if v:
            out[k] = v
    for k in ("hide_title", "no_trend"):
        if lk.get(k):
            out[k] = True
    for k, allowed_ in (("style", CARD_STYLES), ("height", ("auto", "short", "tall")), ("chart", ("bars", "lines", "area")),
                        ("align", ("start", "center", "end")), ("font", ("normal", "large", "huge"))):
        if lk.get(k) in allowed_ and lk.get(k) not in ("card", "auto", "bars", "start", "normal"):
            out[k] = lk[k]
    if _hex(lk.get("color")):
        out["color"] = _hex(lk["color"])
    links = []
    for x in (lk.get("links") or [])[:12] if isinstance(lk.get("links"), list) else []:
        if isinstance(x, dict) and _link(x.get("href")) and text(x.get("label"), maxlen=60):
            links.append({"label": text(x.get("label"), maxlen=60), "href": _link(x.get("href"))})
    if links:
        out["links"] = links
    return out


def clean_page(lk):
    """The dashboard page's own design for a group: heading, subtitle, accent, spacing, corners, backdrop."""
    lk = lk if isinstance(lk, dict) else {}
    out = {}
    for k in ("heading", "heading_ar", "subtitle", "subtitle_ar"):
        v = text(lk.get(k), maxlen=200) if lk.get(k) not in (None, "") else ""
        if v:
            out[k] = v
    if _hex(lk.get("accent")):
        out["accent"] = _hex(lk["accent"])
    for k, allowed_ in (("gap", ("compact", "airy")), ("corners", ("soft", "square")), ("backdrop", ("glow", "gradient"))):
        if lk.get(k) in allowed_:
            out[k] = lk[k]
    if lk.get("hide_filters"):
        out["hide_filters"] = True
    return out


def designer(user, can):
    """Who may design dashboards and their groups: super administrators, and whoever they allow
    (Settings → "Design dashboards & groups")."""
    return bool((user or {}).get("is_super")) or bool(can and can("settings.dashboards", "edit"))


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
        lk = clean_look(it.get("look"))
        if lk:
            e["look"] = lk
        out.append(e)
    return out


def save_group(db, key, d):
    key = text(key, "group")
    name = text(d.get("name"), "name", required=not db.one("SELECT 1 FROM user_groups WHERE key=?", (key,)))
    layout = _clean_layout(d.get("layout") if "layout" in d else None) if "layout" in d else None
    if db.one("SELECT 1 FROM user_groups WHERE key=?", (key,)):
        if "look" in d:
            db.conn.execute("UPDATE user_groups SET look=? WHERE key=?", (json.dumps(clean_page(d.get("look"))), key))
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
        look = d.get("look")
        if look is None and base:
            g = db.one("SELECT look FROM user_groups WHERE key=?", (base,))
            look = json.loads(g["look"] or "{}") if g else {}
        db.conn.execute("INSERT INTO user_groups(key,name,name_ar,layout,sort,look) VALUES (?,?,?,?,?,?)",
                        (key, name, text(d.get("name_ar")), json.dumps(layout or []), n, json.dumps(clean_page(look))))
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
        "hr": ("HR", "الموارد البشرية"), "general": ("General", "عام"), "design": ("Text, headings & buttons", "نصوص وعناوين وأزرار")}


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


def w_projects_progress(c, o):
    from . import projects
    rows = [r for r in projects.overview(c.db) if r["status"] not in ("Completed", "Closed") and (r["progress"] is not None or r["open_tasks"])]
    rows.sort(key=lambda r: (r["variance"] if r["variance"] is not None else 0))
    return {"rows": [{"title": r["code"], "sub": (f"{round((r['progress'] or 0) * 100)}% done" + (f" · plan {round(r['planned'] * 100)}%" if r["planned"] is not None else "")
                                                   + (f" · {r['overdue_tasks']} overdue tasks" if r["overdue_tasks"] else "")),
                      "value": r["contract_value"], "href": f"#/project/{r['id']}",
                      "level": "bad" if (r["variance"] or 0) < -0.1 or r["overdue_tasks"] else "warn" if (r["variance"] or 0) < 0 else "ok"}
                     for r in rows[: int(o.get("count") or 8)]]}


def w_contracts_watch(c, o):
    from . import contracts
    a = contracts.alerts(c.db)
    rows = [{"title": r["party"], "sub": f"{r['code']} · {'ended' if r['expired'] else 'ends'} {r['revised_finish']}", "value": r["revised_value"],
             "href": f"#/contract/{r['id']}", "level": "bad" if r["expired"] else "warn"} for r in a["expired"] + a["ending_soon"]]
    return {"pending_vos": a["pending_vos"], "certs": a["certs_to_approve"] + a["certs_to_post"], "rows": rows[:10]}


def w_equipment_due(c, o):
    from . import equipment
    a = equipment.alerts(c.db)
    return {"rows": [{"title": f"{x['code']} — {x['name']}", "sub": x["plan"] + (f" · due {x['due_date']}" if x["due_date"] else "")
                      + (f" · {x['meter']:g}/{x['due_hours']:g} h" if x["due_hours"] else ""), "value": None, "href": "#/maintenance",
                      "level": "bad" if x["state"] == "overdue" else "warn"} for x in a["maintenance_due"][:10]]}


def w_attendance_today(c, o):
    from . import labour
    s = labour.sheet(c.db, acc.today())["summary"]
    return {"value": s["present"] + s["half"], "count": True, "href": "#/attendance",
            "sub": [f"absent {s['absent'] + s['sick']} · leave {s['leave']} · not marked {s['unmarked']}",
                    f"غياب {s['absent'] + s['sick']} · إجازة {s['leave']} · لم يُسجَّل {s['unmarked']}"]}


def w_quick_links(c, o):
    return {}   # drawn in the page from the screens this person may open


def w_design(c, o):
    return {}   # text, headings and buttons: all in the card's design


def w_account_balance(c, o):
    """The balance of one account (or a range by its first digits) as at the dashboard's end date, on its normal side."""
    code = text(o.get("account"))
    if not code:
        return {"value": 0, "sub": ["Choose an account in the card's options", "اختر حساباً من خيارات البطاقة"], "count": False}
    exact = c.db.one("SELECT code, name, name_ar, normal FROM accounts WHERE code=?", (code,))
    like = (code,) if exact else (code + "%",)
    where = "l.account=?" if exact else "l.account LIKE ?"
    to = c.q.get("date_to") or acc.today()
    n = c.db.one(f"SELECT COALESCE(SUM(l.debit-l.credit),0) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                 f"WHERE e.status='posted' AND {where} AND e.date<=?", like + (to,))["n"]
    normal = exact["normal"] if exact else (c.db.one("SELECT normal FROM accounts WHERE code LIKE ? LIMIT 1", (code + "%",)) or {}).get("normal", "Debit")
    value = acc.to_major(n if normal == "Debit" else -n)
    name = [f"{code} {exact['name']}", f"{code} {exact['name_ar'] or exact['name']}"] if exact else [f"Accounts {code}…", f"الحسابات {code}…"]
    return {"value": value, "sub": name, "href": f"#/ledger?account={code}", "money": True}


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
    ("projects_progress", "Projects: progress vs plan", "المشروعات: الإنجاز مقابل الخطة", "projects", ("projects.overview",), 2, w_projects_progress,
     {"count": ("How many", "العدد", "number")}),
    ("contracts_watch", "Contracts to watch", "عقود تحتاج متابعة", "projects", ("contracts.contracts",), 2, w_contracts_watch, {}),
    ("equipment_due", "Equipment maintenance due", "صيانة معدات مستحقة", "projects", ("equipment.maintenance",), 2, w_equipment_due, {}),
    ("attendance_today", "On site today", "الحضور اليوم", "hr", ("payroll.attendance",), 1, w_attendance_today, {}),
    ("quick_links", "Quick links", "روابط سريعة", "general", (), 2, w_quick_links, {}),
    ("account_balance", "Account balance", "رصيد حساب", "overview", ("reports", "accounting.ledger"), 1, w_account_balance,
     {"account": ("Account code (or its first digits)", "كود الحساب (أو أول أرقامه)", "text")}),
    # building blocks: what they show is set in the card's design (any number of each)
    ("text", "Text / note", "نص / ملاحظة", "design", (), 2, w_design, {}),
    ("heading", "Section heading", "عنوان قسم", "design", (), 4, w_design, {}),
    ("links", "Buttons / links", "أزرار وروابط", "design", (), 2, w_design, {}),
]
MULTI = {"text", "heading", "links", "account_balance"}     # may appear more than once on a dashboard
WIDGETS = {w[0]: {"id": w[0], "en": w[1], "ar": w[2], "cat": w[3], "perm": w[4], "size": w[5], "fn": w[6], "opts": w[7]} for w in _W}


def catalog():
    return [{k: v for k, v in w.items() if k != "fn"} | {"opts": {k: list(v) for k, v in w["opts"].items()}, "multi": w["id"] in MULTI}
            for w in WIDGETS.values()]


def allowed(w, can):
    return not w["perm"] or any(can(p) for p in w["perm"])


def view(db, user, q, can, group=None):
    """The dashboard of a group as this person may see it: the layout and each widget's figures. Only designers (super
    administrators, or people they allow) may look at another group's dashboard."""
    is_super = designer(user, can)
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
    return {"group": key, "name": g["name"], "name_ar": g["name_ar"], "look": json.loads(g.get("look") or "{}") if g.get("look") else {},
            "widgets": [x for x in out if not x.get("hidden")] if not is_super else out}


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
    if can("projects.tasks") or can("projects.overview"):
        from . import projects
        p = projects.alerts(db)
        if p["overdue_tasks"]:
            add("warn", f"Overdue project tasks: {p['overdue_tasks']}", f"مهام مشروعات متأخرة: {p['overdue_tasks']}", "#/tasks?status=overdue", p["overdue_tasks"])
        if p["late_phases"]:
            add("warn", f"Project phases past their finish date: {p['late_phases']}", f"مراحل تجاوزت موعد انتهائها: {p['late_phases']}", "#/projects",
                p["late_phases"])
    if can("contracts.contracts") or can("contracts.variations") or can("contracts.certificates"):
        from . import contracts
        c = contracts.alerts(db)
        if c["expired"]:
            add("bad", f"Contracts past their end date: {len(c['expired'])}", f"عقود تجاوزت تاريخ انتهائها: {len(c['expired'])}", "#/contracts", len(c["expired"]))
        if c["ending_soon"]:
            add("warn", f"Contracts ending within 30 days: {len(c['ending_soon'])}", f"عقود تنتهي خلال 30 يوماً: {len(c['ending_soon'])}", "#/contracts",
                len(c["ending_soon"]))
        if c["pending_vos"] and can("contracts.variations", "edit"):
            add("info", f"Variation orders to decide: {c['pending_vos']}", f"أوامر تغيير بانتظار القرار: {c['pending_vos']}", "#/variations", c["pending_vos"])
        if (c["certs_to_approve"] or c["certs_to_post"]) and can("contracts.certificates", "edit"):
            n = c["certs_to_approve"] + c["certs_to_post"]
            add("info", f"Subcontractor certificates to approve / post: {n}", f"مستخلصات مقاولي باطن للاعتماد / الترحيل: {n}", "#/certificates", n)
        if c["retention_due"]:
            add("warn", f"Retention due for release: {len(c['retention_due'])}", f"محتجزات مستحقة الرد: {len(c['retention_due'])}", "#/contracts",
                len(c["retention_due"]))
    if can("purchasing.requests"):
        from . import procurement
        r = procurement.alerts(db)
        if r["to_approve"] and can("purchasing.requests", "edit"):
            add("info", f"Purchase requests to approve: {r['to_approve']}", f"طلبات شراء بانتظار الاعتماد: {r['to_approve']}", "#/purchase-requests?status=submitted",
                r["to_approve"])
        if r["to_order"]:
            add("info", f"Approved requests not ordered yet: {r['to_order']}", f"طلبات معتمدة لم يُصدر لها أمر شراء: {r['to_order']}",
                "#/purchase-requests?status=approved", r["to_order"])
    if can("stock.counts"):
        from . import stocktake
        n = stocktake.alerts(db)["open_counts"]
        if n:
            add("info", f"Stock counts open: {n}", f"جرد مفتوح: {n}", "#/stock-counts", n)
    if can("equipment.maintenance"):
        from . import equipment
        e = equipment.alerts(db)
        if e["overdue"]:
            add("bad", f"Equipment maintenance overdue: {e['overdue']}", f"صيانة معدات متأخرة: {e['overdue']}", "#/maintenance", e["overdue"])
        elif e["maintenance_due"]:
            add("warn", f"Equipment maintenance due soon: {len(e['maintenance_due'])}", f"صيانة معدات قريبة: {len(e['maintenance_due'])}", "#/maintenance",
                len(e["maintenance_due"]))
    if can("tax.ereceipt"):
        from . import ereceipt
        r = ereceipt.alerts(db)
        if r["rejected"]:
            add("bad", f"E-receipts rejected: {r['rejected']}", f"إيصالات إلكترونية مرفوضة: {r['rejected']}", "#/ereceipts?status=rejected", r["rejected"])
        if r["not_sent"]:
            add("warn", f"E-receipts not sent to the tax authority: {r['not_sent']}", f"إيصالات لم تُرسل لمصلحة الضرائب: {r['not_sent']}", "#/ereceipts?status=issued",
                r["not_sent"])
    if can("reports"):
        bad = [a for a in acc.audit(db) if not a["ok"]]
        if bad:
            add("bad", f"Books check: {len(bad)} to review", f"فحص الدفاتر: {len(bad)} للمراجعة", "#/dashboard", len(bad))
    return out
