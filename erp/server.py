"""HTTP server + JSON API (stdlib only).

Every route declares the module permission it needs; the admin user bypasses
checks. Sessions are HttpOnly SameSite=Strict cookies and every mutating
request must carry the X-Requested-With header (CSRF guard).
"""
import datetime as _dt
import json
import mimetypes
import os
import re
import tempfile
import traceback
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import assets, entries, equity, __version__
from . import accounting as acc
from . import auth, branding, cloud, documents, einvoice, interchange, reports
from .db import MODULES, SUBMODULES, SUBTYPES, to_major, to_minor

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 25 * 1024 * 1024
PAGE_MAX = 500
COOKIE = "beyond_session"

ROUTES = []


def route(method, pattern, module=None, level="view", public=False):
    rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, module, level, public, fn))
        return fn
    return deco


class Reply:
    def __init__(self, body=b"", status=200, ctype="application/json; charset=utf-8", filename=None, headers=None):
        self.body, self.status, self.ctype, self.filename = body, status, ctype, filename
        self.headers = headers or {}


def ok(obj=None, status=200):
    return Reply(json.dumps(obj if obj is not None else {"ok": True}, ensure_ascii=False, default=str).encode("utf-8"),
                 status)


def download(data, filename, ctype):
    return Reply(data, 200, ctype, filename)


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def page(q):
    limit = min(int(q.get("limit") or 100), PAGE_MAX)
    return limit, max(int(q.get("offset") or 0), 0)


# ================================================================ bootstrap
COMPANY_KEYS = ("company_name", "company_tagline", "company_address", "company_tax_id", "currency", "font_family",
                "font_size", "units", "fiscal_year_start", "language", "company_legal_name", "company_cr_no",
                "company_city", "company_phone", "company_email", "company_website", "bank_name", "bank_account",
                "bank_holder", "bank_swift", "bank_iban", "offer_validity_days", "offer_prefix", "offer_digits", "regional")
PUBLIC_SETTINGS = COMPANY_KEYS + ("menu_labels", "reserve_pct", "report_from", "report_to", "lock_date", "hidden_modules",
                                  "appearance")
JSON_SETTINGS = {"menu_labels": {}, "units": [], "hidden_modules": [], "appearance": {}, "regional": {}}


def settings_dict(db):
    s = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM settings")}
    out = {k: s.get(k, "") for k in PUBLIC_SETTINGS}
    for k, empty in JSON_SETTINGS.items():
        try:
            out[k] = json.loads(out[k] or json.dumps(empty))
        except json.JSONDecodeError:
            out[k] = empty
    return out


@route("GET", "/api/bootstrap", public=True)
def bootstrap(ctx):
    db, user = ctx.db, ctx.user
    look = settings_dict(db)
    app_logo = None
    if (look["appearance"] or {}).get("logo") == "document":
        app_logo = branding.asset(db, "logo")
    elif (look["appearance"] or {}).get("logo") == "custom":
        app_logo = branding.asset(db, "app_logo")
    base = {"version": __version__, "needs_setup": auth.needs_setup(db), "user": user,
            "workspace_preset": bool(workspace_preset() and getattr(ctx.app, "cloud", None) and not ctx.app.cloud.active),
            "app_logo": app_logo and {"mime": app_logo["mime"], "data": app_logo["data"]},
            "settings": {k: v for k, v in look.items()
                         if k in ("company_name", "company_tagline", "font_family", "font_size", "language", "appearance",
                                  "regional")}}
    if not user:
        return ok(base)
    sync = getattr(ctx.app, "cloud", None)
    base.update({
        "cloud": {"joined": bool(sync and sync.active)},
        "settings": settings_dict(db),
        "accounts": db.q("SELECT * FROM accounts ORDER BY code"),
        "bus": db.q("SELECT id, code, name, status FROM business_units ORDER BY code"),
        "warehouses": db.q("SELECT * FROM warehouses ORDER BY id"),
        "partners": db.q("SELECT * FROM partners ORDER BY id"),
        "items": db.q("SELECT * FROM stock_items ORDER BY name"),
        "tax_codes": db.q("SELECT * FROM tax_codes ORDER BY kind, code"),
        "parties": db.q("SELECT id, name, type, default_vat, default_wht FROM parties ORDER BY name"),
        "offer_types": [{"id": t["id"], "name": t["name"]} for t in db.q("SELECT id, name FROM offer_types ORDER BY id")],
        "enums": {"billing_bases": acc.BILLING_BASES, "methods": acc.PAYMENT_METHODS,
                  "payroll_types": list(acc.PAYROLL_TYPES), "stock_types": acc.STOCK_MOVE_TYPES,
                  "entry_kinds": acc.ENTRY_KINDS + sorted({k for k in entries.kinds(db) if k not in acc.ENTRY_KINDS}),
                  "subtypes": SUBTYPES, "modules": MODULES,
                  "submodules": SUBMODULES, "levels": list(auth.LEVELS),
                  "doc_kinds": [{"key": k, "en": en, "ar": ar} for k, en, ar, _, _ in branding.DOC_KINDS],
                  "entry_types": entries.for_client(db), "new_menu": entries.menu(db),
                  "menu_items": [{"key": k, "en": en, "ar": ar, "module": m} for k, en, ar, m in entries.MENU_ITEMS],
                  "sheets": [{"name": s["name"], "ar": s["ar"], "module": s["module"]} for s in interchange.SHEETS]},
    })
    return ok(base)


@route("POST", "/api/setup", public=True)
def setup(ctx):
    if not auth.needs_setup(ctx.db):
        raise acc.ValidationError("Setup already done")
    b = ctx.body
    auth.create_user(ctx.db, b, first=True)
    if b.get("company_name"):
        ctx.db.set_setting("company_name", acc.text(b["company_name"]))
    if b.get("language") in ("en", "ar"):
        ctx.db.set_setting("language", b["language"])
    token, user = auth.login(ctx.db, b.get("username"), b.get("password"))
    ctx.db.log(user["username"], "setup", "first administrator created")
    return ctx.with_session(ok({"user": user}), token)


@route("POST", "/api/login", public=True)
def login(ctx):
    try:
        token, user = auth.login(ctx.db, ctx.body.get("username"), ctx.body.get("password"))
    except acc.ValidationError:
        # a device joined to the shared workspace also takes the workspace password (the one used on the website)
        found = _workspace_login(ctx, acc.text(ctx.body.get("username")), ctx.body.get("password") or "")
        if not found:
            raise
        token, user = found
    ctx.db.log(user["username"], "login")
    return ctx.with_session(ok({"user": user}), token)


def _workspace_login(ctx, name, password):
    sync = getattr(ctx.app, "cloud", None)
    if not (sync and sync.active and name and password):
        return None
    try:
        c = sync.client()
        s = c.sign_in(name.lower(), password) if "@" in name else c.sign_in_username(name, password)
    except Exception:  # wrong password, unknown user, or no connection
        return None
    email = ((s or {}).get("user") or {}).get("email", "").lower()
    if not email:
        return None
    key = cloud.username_for(email) or email
    u = ctx.db.one("SELECT * FROM users WHERE active=1 AND (lower(email)=? OR lower(username)=? OR lower(username)=?) "
                   "ORDER BY lower(email)=? DESC LIMIT 1", (email, key, name.lower(), email))
    if not u:
        return None
    import secrets
    token = secrets.token_urlsafe(32)
    ctx.db.x("INSERT INTO sessions(token,user_id,expires) VALUES (?,?,datetime('now',?))",
             (token, u["id"], f"+{auth.SESSION_DAYS} days"))
    return token, auth.public_user(u)


@route("POST", "/api/logout", public=True)
def logout(ctx):
    auth.logout(ctx.db, ctx.token)
    return ctx.with_session(ok(), "")


# ================================================================= settings
@route("PUT", "/api/settings")
def put_settings(ctx):
    keys = set(ctx.body)
    if "menu_labels" in keys:
        ctx.require("settings.menus", "edit")
    if keys & set(COMPANY_KEYS):
        ctx.require("settings.company", "edit")
    if "appearance" in keys:
        ctx.require("settings.branding", "edit")
    if "reserve_pct" in keys and not ctx.can("partners.equity", "edit"):
        ctx.require("settings.company", "edit")
    if keys & {"backup_keep", "lock_date"}:
        ctx.require("settings.system", "edit")
    if "hidden_modules" in keys:
        ctx.require("settings.menus", "edit")
    allowed = set(PUBLIC_SETTINGS) | {"backup_keep"}
    for k, v in ctx.body.items():
        if k not in allowed:
            continue
        if k in JSON_SETTINGS:
            if not isinstance(v, type(JSON_SETTINGS[k])):
                raise acc.ValidationError(f"{k} has the wrong shape")
            v = json.dumps(v, ensure_ascii=False)
        if k == "lock_date" and v:
            v = acc.clean_date(v, "lock date")
        if k == "font_size":
            v = str(max(11, min(22, int(float(v or 15)))))
        if k == "reserve_pct":
            v = str(max(0.0, min(1.0, acc.num(v, "reserve %"))))
        ctx.db.set_setting(k, acc.text(v, maxlen=20000))
    ctx.log("settings", ", ".join(ctx.body.keys()))
    return ok(settings_dict(ctx.db))


@route("PUT", "/api/entry-types", "settings.menus", "edit")
def put_entry_types(ctx):
    out = entries.save(ctx.db, ctx.body.get("types"))
    ctx.log("settings", "entry types")
    return ok({"types": out})


@route("PUT", "/api/new-menu", "settings.menus", "edit")
def put_new_menu(ctx):
    out = entries.save_menu(ctx.db, ctx.body.get("items"))
    ctx.log("settings", "+ New menu")
    return ok({"items": out})


@route("POST", "/api/entry/<tid>", "accounting.journal", "add")
def post_entry_type(ctx, tid):
    eid = entries.post(ctx.db, tid, ctx.body, ctx.user["username"])
    ctx.log("journal.add", eid)
    return ok({"id": eid})


# ========================================================== chart of accounts
@route("GET", "/api/accounts", "accounting.coa")
def accounts(ctx):
    rows = {r["code"]: r for r in acc.account_totals(ctx.db, date_to=ctx.q.get("date_to"))}
    out = []
    for a in ctx.db.q("SELECT * FROM accounts ORDER BY code"):
        r = rows.get(a["code"], {"net": 0})
        a["balance"] = to_major(r["net"] if a["normal"] == "Debit" else -r["net"])
        a["used"] = bool(ctx.db.one("SELECT 1 FROM journal_lines WHERE account=? LIMIT 1", (a["code"],)))
        out.append(a)
    return ok(out)


@route("POST", "/api/accounts", "accounting.coa", "add")
def add_account(ctx):
    code = acc.save_account(ctx.db, ctx.body)
    ctx.log("account.add", code)
    return ok({"code": code})


@route("PUT", "/api/accounts/<code>", "accounting.coa", "edit")
def edit_account(ctx, code):
    new = acc.save_account(ctx.db, ctx.body, old_code=code)
    ctx.log("account.edit", f"{code} -> {new}")
    return ok({"code": new})


@route("DELETE", "/api/accounts/<code>", "accounting.coa", "edit")
def del_account(ctx, code):
    acc.delete_account(ctx.db, code)
    ctx.log("account.delete", code)
    return ok()


# ============================================================ business units
BU_FIELDS = ("code", "name", "type", "location", "area", "start_date", "status", "basis", "fee_pct",
             "contract_value", "overhead_pct", "notes")


def _bu_values(b):
    return (acc.text(b.get("code"), "code", required=True, maxlen=40), acc.text(b.get("name"), "name", required=True),
            acc.text(b.get("type")), acc.text(b.get("location")), acc.num(b.get("area"), "area"),
            acc.text(b.get("start_date")), acc.text(b.get("status")) or "In Progress",
            b.get("basis") if b.get("basis") in acc.BILLING_BASES else "Cost-Plus %",
            min(acc.num(b.get("fee_pct"), "fee %"), 10), to_minor(acc.num(b.get("contract_value"), "contract value")),
            min(acc.num(b.get("overhead_pct"), "overhead %"), 1), acc.text(b.get("notes"), maxlen=4000))


@route("GET", "/api/bus", "business_units.units")
def bus(ctx):
    q = ctx.q
    return ok({"rows": acc.bu_summaries(ctx.db, q.get("date_from"), q.get("date_to"), q.get("status"), q.get("q")),
               "shared_overhead": acc.shared_overhead_total(ctx.db, q.get("date_from"), q.get("date_to"))})


@route("POST", "/api/bus", "business_units.units", "add")
def add_bu(ctx):
    v = _bu_values(ctx.body)
    if ctx.db.one("SELECT 1 FROM business_units WHERE code=?", (v[0],)):
        raise acc.ValidationError("Code already exists")
    bid = ctx.db.x(f"INSERT INTO business_units({','.join(BU_FIELDS)}) VALUES ({','.join('?' * len(BU_FIELDS))})",
                   v).lastrowid
    ctx.log("bu.add", v[0])
    return ok({"id": bid})


@route("PUT", "/api/bus/<bid>", "business_units.units", "edit")
def edit_bu(ctx, bid):
    v = _bu_values(ctx.body)
    if ctx.db.one("SELECT 1 FROM business_units WHERE code=? AND id<>?", (v[0], int(bid))):
        raise acc.ValidationError("Code already exists")
    ctx.db.x(f"UPDATE business_units SET {','.join(f + '=?' for f in BU_FIELDS)} WHERE id=?", v + (int(bid),))
    ctx.log("bu.edit", v[0])
    return ok()


@route("PUT", "/api/bus-allocation", "business_units.allocation", "edit")
def bu_allocation(ctx):
    """Set every unit's share of the shared (indirect) operating expenses at once. Total may not exceed 100%."""
    alloc = {int(k): acc.num(v, "allocation") for k, v in (ctx.body.get("allocations") or {}).items()}  # fractions
    if sum(alloc.values()) > 1.00001:
        raise acc.ValidationError(f"Allocations add up to {sum(alloc.values()) * 100:.2f}% — the total cannot exceed 100%")
    with ctx.db.tx():
        for bid, v in alloc.items():
            ctx.db.conn.execute("UPDATE business_units SET overhead_pct=? WHERE id=?", (round(v, 6), bid))
    ctx.log("bu.allocation", json.dumps(alloc))
    return ok()


@route("DELETE", "/api/bus/<bid>", "business_units.units", "edit")
def del_bu(ctx, bid):
    if ctx.db.one("SELECT 1 FROM journal_entries WHERE bu_id=? LIMIT 1", (int(bid),)):
        raise acc.ValidationError("This business unit has transactions — set its status to Closed instead")
    ctx.db.x("DELETE FROM business_units WHERE id=?", (int(bid),))
    ctx.log("bu.delete", bid)
    return ok()


# ================================================================== journal
def journal_filters(q):
    w, p = ["1=1"], []
    if q.get("status") in ("posted", "void"):
        w.append("e.status=?")
        p.append(q["status"])
    if q.get("kind"):
        w.append("e.kind=?")
        p.append(q["kind"])
    if q.get("party"):  # the entry's party, or a party named on one of its lines
        w.append("(e.party=? OR EXISTS (SELECT 1 FROM journal_lines l WHERE l.entry_id=e.id AND l.party=?))")
        p += [q["party"], q["party"]]
    if q.get("bu_id"):
        if q["bu_id"] == "none":
            w.append("e.bu_id IS NULL")
        else:
            w.append("e.bu_id=?")
            p.append(int(q["bu_id"]))
    if q.get("date_from"):
        w.append("e.date>=?")
        p.append(acc.clean_date(q["date_from"]))
    if q.get("date_to"):
        w.append("e.date<=?")
        p.append(acc.clean_date(q["date_to"]))
    if q.get("account"):
        a = q["account"]
        if len(a) == 1:
            w.append("EXISTS (SELECT 1 FROM journal_lines l WHERE l.entry_id=e.id AND l.account LIKE ?)")
            p.append(a + "%")
        else:
            w.append("EXISTS (SELECT 1 FROM journal_lines l WHERE l.entry_id=e.id AND l.account=?)")
            p.append(a)
    if q.get("q"):
        w.append("(e.memo LIKE ? OR e.party LIKE ? OR e.ref LIKE ? OR e.meta LIKE ? OR CAST(e.id AS TEXT)=?)")
        s = f"%{q['q']}%"
        p += [s, s, s, s, q["q"].lstrip("#")]
    if q.get("min_amount"):
        w.append("(SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) >= ?")
        p.append(to_minor(acc.num(q["min_amount"])))
    return " AND ".join(w), p


@route("GET", "/api/journal", "accounting.journal")
def journal(ctx):
    w, p = journal_filters(ctx.q)
    limit, offset = page(ctx.q)
    total = ctx.db.one(f"SELECT COUNT(*) AS n, COALESCE(SUM((SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id)),0) "
                       f"AS amt FROM journal_entries e WHERE {w} AND e.status='posted'", p)
    count = ctx.db.one(f"SELECT COUNT(*) AS n FROM journal_entries e WHERE {w}", p)["n"]
    ids = [r["id"] for r in ctx.db.q(f"SELECT e.id FROM journal_entries e WHERE {w} ORDER BY e.date DESC, e.id DESC "
                                     f"LIMIT ? OFFSET ?", p + [limit, offset])]
    return ok({"rows": [acc.get_entry(ctx.db, i) for i in ids], "total": count, "posted_amount": to_major(total["amt"]),
               "limit": limit, "offset": offset})


@route("GET", "/api/bu-journal", "business_units.units")
def bu_journal(ctx):
    """Business-unit transaction list for users who can see business units but not the full journal."""
    if not ctx.q.get("bu_id"):
        raise acc.ValidationError("bu_id is required")
    return journal(ctx)


@route("GET", "/api/journal/<eid>")
def journal_one(ctx, eid):
    e = acc.get_entry(ctx.db, int(eid))
    if not e:
        return ok({"error": "Not found"}, 404)
    if not (ctx.can("accounting.journal") or ctx.can(KIND_MODULE.get(e["kind"], "accounting.journal"))
            or (e["bu_id"] and ctx.can("business_units.units"))):
        raise PermissionError()
    return ok(e)


@route("POST", "/api/journal", "accounting.journal", "add")
def journal_add(ctx):
    eid = acc.record_journal(ctx.db, ctx.body, ctx.user["username"])
    ctx.log("journal.add", eid)
    return ok({"id": eid})


@route("PUT", "/api/journal/<eid>", "accounting.journal", "edit")
def journal_edit(ctx, eid):
    acc.edit_entry(ctx.db, int(eid), ctx.body, ctx.user["username"])
    ctx.log("journal.edit", eid)
    return ok()


@route("POST", "/api/journal/<eid>/void", "accounting.journal", "edit")
def journal_void(ctx, eid):
    acc.void_entry(ctx.db, int(eid), ctx.body.get("reason", ""), ctx.user["username"])
    ctx.log("journal.void", eid)
    return ok()


@route("POST", "/api/journal/<eid>/reverse", "accounting.journal", "edit")
def journal_reverse(ctx, eid):
    rid = acc.reverse_entry(ctx.db, int(eid), ctx.body.get("date"), ctx.user["username"])
    ctx.log("journal.reverse", f"{eid} -> {rid}")
    return ok({"id": rid})


@route("DELETE", "/api/journal/<eid>", "accounting.journal", "edit")
def journal_delete(ctx, eid):
    acc.delete_entry(ctx.db, int(eid))
    ctx.log("journal.delete", eid)
    return ok()


# ================================================================ documents
DOCS = {  # type -> (function, module)
    "direct_cost": (acc.record_project_cost, "purchasing.costs"),
    "expense": (acc.record_expense, "purchasing.expenses"),
    "payment": (acc.record_payment, "purchasing.payments"),
    "payroll": (acc.record_payroll, "payroll"),
    "receipt": (acc.record_collection, "sales.receipts"),
    "partner_tx": (acc.record_partner_tx, "partners.transactions"),
    "stock_move": (acc.stock_move, "stock.moves"),
}


@route("POST", "/api/docs/<dtype>")
def post_doc(ctx, dtype):
    if dtype not in DOCS:
        return ok({"error": "Unknown document"}, 404)
    fn, module = DOCS[dtype]
    ctx.require(module, "add")
    eid = fn(ctx.db, ctx.body, ctx.user["username"])
    ctx.log(f"doc.{dtype}", eid)
    return ok({"id": eid})


@route("GET", "/api/docs/<dtype>")
def list_docs(ctx, dtype):
    """Filtered voucher list for one document type (what each module screen shows)."""
    kinds = {"direct_cost": ["Direct Cost"], "expense": ["Operating Expense"], "payment": ["Payment Voucher"],
             "payroll": ["Payroll Accrual"], "receipt": ["Receipt Voucher"],
             "partner_tx": ["Capital Contribution", "Owner Drawing"],
             "stock_move": ["Goods Receipt", "Material Issue", "Stock Transfer", "Stock Adjustment"]}
    if dtype not in kinds:
        return ok({"error": "Unknown document"}, 404)
    ctx.require(DOCS[dtype][1], "view")
    w, p = journal_filters({k: v for k, v in ctx.q.items() if k != "kind"})
    if ctx.q.get("kind") in kinds[dtype]:
        w += " AND e.kind=?"
        p.append(ctx.q["kind"])
    else:
        w += f" AND e.kind IN ({','.join('?' * len(kinds[dtype]))})"
        p += kinds[dtype]
    limit, offset = page(ctx.q)
    count = ctx.db.one(f"SELECT COUNT(*) AS n FROM journal_entries e WHERE {w}", p)["n"]
    ids = [r["id"] for r in ctx.db.q(f"SELECT e.id FROM journal_entries e WHERE {w} ORDER BY e.date DESC, e.id DESC "
                                     "LIMIT ? OFFSET ?", p + [limit, offset])]
    return ok({"rows": [acc.get_entry(ctx.db, i) for i in ids], "total": count, "limit": limit, "offset": offset})


KIND_MODULE = {"Direct Cost": "purchasing.costs", "Operating Expense": "purchasing.expenses",
               "Payment Voucher": "purchasing.payments", "Payroll Accrual": "payroll", "Receipt Voucher": "sales.receipts",
               "Sales Invoice": "sales.invoices", "Capital Contribution": "partners.transactions",
               "Owner Drawing": "partners.transactions", "Capital Withdrawal": "partners.transactions",
               "Partner Loan": "partners.transactions", "Partner Loan Repayment": "partners.transactions",
               "Partner Loan Interest": "partners.transactions", "Profit Distribution": "partners.transactions",
               "Share Transfer": "partners.ownership", "Partner Admission": "partners.ownership",
               "Partner Exit": "partners.ownership", "Asset Purchase": "assets.register", "Depreciation": "assets.depreciation",
               "Asset Disposal": "assets.register", "Goods Receipt": "stock.moves", "Material Issue": "stock.moves",
               "Stock Transfer": "stock.moves", "Stock Adjustment": "stock.moves"}


def _vouchers_report(ctx):
    """Export of any filtered voucher list. Needs the journal permission, or the module of every listed kind
    (or business-units permission when filtered to one unit)."""
    kinds = [k for k in (ctx.q.get("kinds") or "").split(",") if k]
    allowed = ctx.can("accounting.journal") or \
        (kinds and all(ctx.can(KIND_MODULE.get(k, "accounting.journal")) for k in kinds)) or \
        (ctx.q.get("bu_id") and ctx.can("business_units.units"))
    if not allowed:
        raise PermissionError()
    w, p = journal_filters(ctx.q)
    if kinds:
        w += f" AND e.kind IN ({','.join('?' * len(kinds))})"
        p += kinds
    ids = [r["id"] for r in ctx.db.q(f"SELECT e.id FROM journal_entries e WHERE {w} ORDER BY e.date, e.id LIMIT 20000", p)]
    lang = ctx.q.get("lang") or "en"
    title = ctx.q.get("title") or reports.L("vouchers", lang)
    sub = " · ".join(f"{k}: {v}" for k, v in ctx.q.items() if k in ("date_from", "date_to", "q", "party") and v)
    return reports.vouchers_table(ctx.db, [acc.get_entry(ctx.db, i) for i in ids], title, lang, sub)


@route("GET", "/api/parties")
def parties(ctx):
    if not (ctx.can("purchasing.payables") or ctx.can("purchasing.payments") or ctx.can("payroll")
            or ctx.can("accounting.journal")):
        raise PermissionError()
    kind = ctx.q.get("kind") or "payable"
    subtypes = {"payable": ("payable",), "payroll": ("accrued",), "all": ("payable", "accrued")}.get(kind, ("payable",))
    return ok({"rows": acc.party_balances(ctx.db, subtypes=subtypes, date_to=ctx.q.get("date_to")),
               "names": [r["party"] for r in ctx.db.q("SELECT DISTINCT party FROM journal_entries WHERE party<>'' "
                                                      "ORDER BY party LIMIT 2000")],
               "deals": acc.deals(ctx.db)})


# ================================================================ price list
@route("GET", "/api/rates")
def rates(ctx):
    if not (ctx.can("estimating.prices") or ctx.can("estimating.boqs") or ctx.can("sales.invoices")):
        raise PermissionError()
    w, p = ["1=1"], []
    if ctx.q.get("category"):
        w.append("category=?")
        p.append(ctx.q["category"])
    if ctx.q.get("q"):
        w.append("(description LIKE ? OR category LIKE ? OR item_no LIKE ?)")
        p += [f"%{ctx.q['q']}%"] * 3
    limit, offset = page(ctx.q)
    rows = ctx.db.q(f"SELECT * FROM rates WHERE {' AND '.join(w)} ORDER BY category, id LIMIT ? OFFSET ?",
                    p + [limit, offset])
    for r in rows:
        r["cost"], r["price"] = to_major(r["cost"]), to_major(r["price"])
    return ok({"rows": rows, "total": ctx.db.one(f"SELECT COUNT(*) AS n FROM rates WHERE {' AND '.join(w)}", p)["n"],
               "categories": [r["category"] for r in ctx.db.q("SELECT DISTINCT category FROM rates ORDER BY category")]})


def _rate_vals(b):
    cost, mk = acc.num(b.get("cost"), "cost"), acc.num(b.get("markup"), "markup")
    price = acc.num(b.get("price"), "price") or cost * (1 + mk)
    return (acc.text(b.get("category"), "category", required=True), acc.text(b.get("item_no")), acc.text(b.get("sub")),
            acc.text(b.get("description"), "description", required=True, maxlen=2000), acc.text(b.get("unit")),
            to_minor(cost), mk, to_minor(price), acc.text(b.get("notes")))


@route("POST", "/api/rates", "estimating.prices", "add")
def add_rate(ctx):
    rid = ctx.db.x("INSERT INTO rates(category,item_no,sub,description,unit,cost,markup,price,notes) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", _rate_vals(ctx.body)).lastrowid
    return ok({"id": rid})


@route("PUT", "/api/rates/<rid>", "estimating.prices", "edit")
def edit_rate(ctx, rid):
    ctx.db.x("UPDATE rates SET category=?,item_no=?,sub=?,description=?,unit=?,cost=?,markup=?,price=?,notes=? "
             "WHERE id=?", _rate_vals(ctx.body) + (int(rid),))
    return ok()


@route("DELETE", "/api/rates/<rid>", "estimating.prices", "edit")
def del_rate(ctx, rid):
    ctx.db.x("DELETE FROM rates WHERE id=?", (int(rid),))
    return ok()


# ============================================================ BOQ & invoices
@route("GET", "/api/boqs")
def boqs(ctx):
    if not (ctx.can("estimating.boqs") or ctx.can("sales.invoices")):
        raise PermissionError()
    w, p = ["1=1"], []
    if ctx.q.get("bu_id"):
        w.append("b.bu_id=?")
        p.append(int(ctx.q["bu_id"]))
    if ctx.q.get("q"):
        w.append("(b.title LIKE ? OR b.client LIKE ?)")
        p += [f"%{ctx.q['q']}%"] * 2
    rows = ctx.db.q(f"SELECT b.*, u.code AS bu_code, (SELECT COUNT(*) FROM boq_lines WHERE boq_id=b.id) AS lines, "
                    f"(SELECT COALESCE(SUM(ROUND(qty*price)),0) FROM boq_lines WHERE boq_id=b.id) AS total_minor "
                    f"FROM boqs b LEFT JOIN business_units u ON u.id=b.bu_id WHERE {' AND '.join(w)} "
                    f"ORDER BY b.date DESC, b.id DESC", p)
    for r in rows:
        r["total"] = to_major(r.pop("total_minor"))
    return ok(rows)


@route("GET", "/api/boqs/<bid>")
def boq_one(ctx, bid):
    if not (ctx.can("estimating.boqs") or ctx.can("sales.invoices")):
        raise PermissionError()
    return ok(acc.get_boq(ctx.db, int(bid)))


@route("POST", "/api/boqs", "estimating.boqs", "add")
def boq_add(ctx):
    return ok({"id": acc.save_boq(ctx.db, ctx.body)})


@route("PUT", "/api/boqs/<bid>", "estimating.boqs", "edit")
def boq_edit(ctx, bid):
    return ok({"id": acc.save_boq(ctx.db, ctx.body, int(bid))})


@route("DELETE", "/api/boqs/<bid>", "estimating.boqs", "edit")
def boq_del(ctx, bid):
    ctx.db.x("DELETE FROM boqs WHERE id=?", (int(bid),))
    return ok()


@route("GET", "/api/extracts")
def extracts(ctx):
    if not (ctx.can("sales.invoices") or ctx.can("tax.einvoice")):
        raise PermissionError()
    q = ctx.q
    return ok(acc.list_extracts(ctx.db, q.get("boq_id"), q.get("bu_id"), q.get("status"), q.get("date_from"),
                                q.get("date_to")))


@route("GET", "/api/extracts/<xid>", "sales.invoices")
def extract_one(ctx, xid):
    return ok(acc.get_extract(ctx.db, int(xid)))


@route("POST", "/api/extracts", "sales.invoices", "add")
def extract_add(ctx):
    xid = acc.save_extract(ctx.db, ctx.body)
    if ctx.body.get("post"):
        acc.post_extract(ctx.db, xid, user=ctx.user["username"])
    return ok({"id": xid})


@route("PUT", "/api/extracts/<xid>", "sales.invoices", "edit")
def extract_edit(ctx, xid):
    acc.save_extract(ctx.db, ctx.body, int(xid))
    if ctx.body.get("post"):
        acc.post_extract(ctx.db, int(xid), user=ctx.user["username"])
    return ok({"id": int(xid)})


@route("POST", "/api/extracts/<xid>/post", "sales.invoices", "add")
def extract_post(ctx, xid):
    return ok({"entry_id": acc.post_extract(ctx.db, int(xid), ctx.body.get("date"), ctx.user["username"])})


@route("DELETE", "/api/extracts/<xid>", "sales.invoices", "edit")
def extract_del(ctx, xid):
    x = ctx.db.one("SELECT status FROM extracts WHERE id=?", (int(xid),))
    if x and x["status"] == "posted":
        raise acc.ValidationError("Posted invoices cannot be deleted — reverse the invoice entry first")
    ctx.db.x("DELETE FROM extracts WHERE id=?", (int(xid),))
    return ok()


# ==================================================================== stock
@route("GET", "/api/stock")
def stock(ctx):
    if not (ctx.can("stock.balances") or ctx.can("stock.warehouses") or ctx.can("stock.moves")):
        raise PermissionError()
    return ok({"levels": acc.stock_report(ctx.db, ctx.q.get("warehouse_id"), ctx.q.get("q")),
               "warehouses": ctx.db.q("SELECT * FROM warehouses ORDER BY id"),
               "items": ctx.db.q("SELECT * FROM stock_items ORDER BY name")})


@route("GET", "/api/stock/moves", "stock.moves")
def stock_moves(ctx):
    w, p = ["1=1"], []
    for key, col in (("warehouse_id", "m.warehouse_id"), ("item_id", "m.item_id"), ("bu_id", "m.bu_id"),
                     ("type", "m.kind")):
        if ctx.q.get(key):
            w.append(f"{col}=?")
            p.append(ctx.q[key])
    if ctx.q.get("date_from"):
        w.append("m.date>=?")
        p.append(ctx.q["date_from"])
    if ctx.q.get("date_to"):
        w.append("m.date<=?")
        p.append(ctx.q["date_to"])
    limit, offset = page(ctx.q)
    rows = ctx.db.q(f"SELECT m.*, i.name AS item, i.unit, w.name AS warehouse, u.code AS bu_code FROM stock_moves m "
                    f"JOIN stock_items i ON i.id=m.item_id JOIN warehouses w ON w.id=m.warehouse_id "
                    f"LEFT JOIN business_units u ON u.id=m.bu_id WHERE {' AND '.join(w)} "
                    f"ORDER BY m.date DESC, m.id DESC LIMIT ? OFFSET ?", p + [limit, offset])
    for r in rows:
        r["rate"] = to_major(r["rate"])
        r["value"] = round(r["qty"] * r["rate"], 2)
    return ok({"rows": rows})


@route("POST", "/api/warehouses", "stock.warehouses", "add")
def wh_add(ctx):
    b = ctx.body
    account = acc.account_code(ctx.db, b["account"], subtypes=("inventory",)) if b.get("account") else None
    wid = ctx.db.x("INSERT INTO warehouses(name,location,account) VALUES (?,?,?)",
                   (acc.text(b.get("name"), "name", required=True), acc.text(b.get("location")), account)).lastrowid
    return ok({"id": wid})


@route("PUT", "/api/warehouses/<wid>", "stock.warehouses", "edit")
def wh_edit(ctx, wid):
    b = ctx.body
    account = acc.account_code(ctx.db, b["account"], subtypes=("inventory",)) if b.get("account") else None
    cur = ctx.db.one("SELECT * FROM warehouses WHERE id=?", (int(wid),))
    if cur and (cur["account"] or None) != account and \
            ctx.db.one("SELECT 1 FROM stock_levels WHERE warehouse_id=? AND qty<>0", (int(wid),)):
        raise acc.ValidationError("Move this warehouse's stock out before changing its inventory account")
    ctx.db.x("UPDATE warehouses SET name=?, location=?, account=?, active=? WHERE id=?",
             (acc.text(b.get("name"), "name", required=True), acc.text(b.get("location")), account,
              0 if b.get("active") in (0, False) else 1, int(wid)))
    return ok()


@route("DELETE", "/api/warehouses/<wid>", "stock.warehouses", "edit")
def wh_del(ctx, wid):
    if ctx.db.one("SELECT 1 FROM stock_moves WHERE warehouse_id=? LIMIT 1", (int(wid),)):
        raise acc.ValidationError("Warehouse has stock history — deactivate it instead")
    ctx.db.x("DELETE FROM warehouses WHERE id=?", (int(wid),))
    return ok()


@route("DELETE", "/api/items/<iid>", "stock.balances", "edit")
def item_del(ctx, iid):
    it = ctx.db.one("SELECT name FROM stock_items WHERE id=?", (int(iid),))
    if not it:
        raise acc.ValidationError("Not found")
    if ctx.db.one("SELECT 1 FROM stock_moves WHERE item_id=? LIMIT 1", (int(iid),)):
        raise acc.ValidationError(f"{it['name']} has stock movements — only items without any movements can be deleted "
                                  "(void the movements first if they were a mistake)")
    ctx.db.x("DELETE FROM stock_items WHERE id=?", (int(iid),))
    ctx.log("item.delete", it["name"])
    return ok()


@route("PUT", "/api/items/<iid>", "stock.balances", "edit")
def item_edit(ctx, iid):
    b = ctx.body
    ctx.db.x("UPDATE stock_items SET name=?, unit=?, sku=?, reorder_level=? WHERE id=?",
             (acc.text(b.get("name"), "name", required=True), acc.text(b.get("unit")) or "pcs", acc.text(b.get("sku")),
              acc.num(b.get("reorder_level"), "reorder level"), int(iid)))
    return ok()


# ================================================================= partners
@route("GET", "/api/partners")
def partners(ctx):
    if not (ctx.can("partners.equity") or ctx.can("partners.transactions")):
        raise PermissionError()
    rep = acc.partner_report(ctx.db, ctx.q.get("date_from"), ctx.q.get("date_to"))
    rep["cash_position"] = acc.cash_position(ctx.db)
    rep["shares"] = ctx.db.q("SELECT * FROM partner_shares")
    return ok(rep)


@route("POST", "/api/partners", "partners.equity", "add")
def partner_add(ctx):
    pid = ctx.db.x("INSERT INTO partners(name,default_share) VALUES (?,?)",
                   (acc.text(ctx.body.get("name"), "name", required=True),
                    min(acc.num(ctx.body.get("default_share"), "share"), 1))).lastrowid
    return ok({"id": pid})


@route("PUT", "/api/partners/<pid>", "partners.equity", "edit")
def partner_edit(ctx, pid):
    b = ctx.body
    with ctx.db.tx():
        old = ctx.db.one("SELECT * FROM partners WHERE id=?", (int(pid),))
        if not old:
            raise acc.ValidationError("Partner not found")
        name = acc.text(b.get("name"), "name", required=True)
        if ctx.db.one("SELECT 1 FROM partners WHERE name=? AND id<>?", (name, int(pid))):
            raise acc.ValidationError("Another partner already has this name")
        before = equity.state(ctx.db)
        rate = lambda k: min(acc.num(b.get(k), k), 100) / 100 if b.get(k) not in (None, "") else old[k]  # noqa: E731
        ctx.db.conn.execute("UPDATE partners SET name=?, default_share=?, active=?, joined=?, left_on=?, capital_interest=?, "
                            "loan_rate=?, notes=? WHERE id=?",
                            (name, min(acc.num(b.get("default_share", old["default_share"])), 1),
                             0 if b.get("active") in (0, "0", False, "false") else 1,
                             acc.text(b.get("joined", old["joined"])), acc.text(b.get("left_on", old["left_on"])),
                             rate("capital_interest"), rate("loan_rate"), acc.text(b.get("notes", old["notes"]), maxlen=2000),
                             int(pid)))
        for bu_id, share in (b.get("shares") or {}).items():
            if share in ("", None):
                ctx.db.conn.execute("DELETE FROM partner_shares WHERE partner_id=? AND bu_id=?", (int(pid), int(bu_id)))
            else:
                ctx.db.conn.execute("INSERT OR REPLACE INTO partner_shares(partner_id,bu_id,share) VALUES (?,?,?)",
                                    (int(pid), int(bu_id), min(acc.num(share, "share"), 1)))
        if name != old["name"]:  # the partner's sub-ledger follows the new name
            ctx.db.conn.execute("UPDATE journal_entries SET party=? WHERE party=?", (name, old["name"]))
            ctx.db.conn.execute("UPDATE journal_lines SET party=? WHERE party=?", (name, old["name"]))
        after = equity.state(ctx.db)
        if before["shares"] != after["shares"] or before["bu"] != after["bu"]:
            equity.record_change(ctx.db, acc.today(), "Ownership Change", f"Shares edited for {name}", before, after,
                                 None, ctx.user["username"])
    return ok()


@route("DELETE", "/api/partners/<pid>", "partners.equity", "edit")
def partner_del(ctx, pid):
    p = ctx.db.one("SELECT name FROM partners WHERE id=?", (int(pid),))
    if p and ctx.db.one("SELECT 1 FROM journal_entries e WHERE e.party=? OR EXISTS (SELECT 1 FROM journal_lines l "
                        "WHERE l.entry_id=e.id AND l.party=?) LIMIT 1", (p["name"], p["name"])):
        raise acc.ValidationError("Partner has transactions and cannot be removed — untick “Still a partner” instead")
    ctx.db.x("DELETE FROM partners WHERE id=?", (int(pid),))
    return ok()


# ============================================================ fixed assets
@route("GET", "/api/assets", "assets.register")
def assets_list(ctx):
    return ok(assets.register(ctx.db, ctx.q.get("q"), ctx.q.get("account"), ctx.q.get("state"), ctx.q.get("date_to")))


@route("GET", "/api/assets/<aid>", "assets.register")
def asset_get(ctx, aid):
    return ok(assets._row(ctx.db, assets.get(ctx.db, aid)))


@route("POST", "/api/assets", "assets.register", "add")
def asset_add(ctx):
    if ctx.body.get("pay") not in (None, "", "none"):
        ctx.require("purchasing.costs" if ctx.body.get("pay") != "partner" else "partners.transactions", "add")
    aid = assets.save(ctx.db, ctx.body, user=ctx.user["username"])
    ctx.log("asset.add", aid)
    return ok({"id": aid})


@route("PUT", "/api/assets/<aid>", "assets.register", "edit")
def asset_edit(ctx, aid):
    assets.save(ctx.db, {**ctx.body, "pay": "none"}, aid, ctx.user["username"])
    ctx.log("asset.edit", aid)
    return ok()


@route("DELETE", "/api/assets/<aid>", "assets.register", "edit")
def asset_del(ctx, aid):
    assets.delete(ctx.db, aid)
    ctx.log("asset.delete", aid)
    return ok()


@route("POST", "/api/assets-preview/depreciation", "assets.depreciation", "add")
def depr_preview(ctx):
    return ok(assets.plan_depreciation(ctx.db, ctx.body.get("through"), ctx.body.get("ids")))


@route("POST", "/api/assets/depreciation", "assets.depreciation", "add")
def depr_run(ctx):
    r = assets.run_depreciation(ctx.db, ctx.body.get("through"), ctx.body.get("ids"), ctx.user["username"])
    ctx.log("asset.depreciation", r)
    return ok(r)


@route("POST", "/api/assets-preview/dispose", "assets.register", "edit")
def dispose_preview(ctx):
    pl = assets.plan_disposal(ctx.db, ctx.body.get("id"), ctx.body)
    names = {a["code"]: a for a in ctx.db.q("SELECT code, name, name_ar FROM accounts")}
    return ok({"lines": [{**ln, "name": names[ln["account"]]["name"], "name_ar": names[ln["account"]]["name_ar"]}
                         for ln in pl["lines"]], "notes": pl["notes"], "gain": pl["gain"], "memo": pl["memo"]})


@route("POST", "/api/assets/<aid>/dispose", "assets.register", "edit")
def dispose(ctx, aid):
    eid = assets.dispose(ctx.db, aid, ctx.body, ctx.user["username"])
    ctx.log("asset.dispose", eid)
    return ok({"id": eid})


def _equity_module(action):
    return "partners.ownership" if action in equity.OWNERSHIP_ACTIONS else "partners.transactions"


@route("POST", "/api/equity-preview/<action>")
def equity_preview(ctx, action):
    ctx.require(_equity_module(action), "add")
    return ok(equity.preview(ctx.db, action, ctx.body))


@route("POST", "/api/equity/<action>")
def equity_post(ctx, action):
    ctx.require(_equity_module(action), "add")
    r = equity.post(ctx.db, action, ctx.body, ctx.user["username"])
    ctx.log(f"equity.{action}", r)
    return ok(r)


@route("GET", "/api/equity/history")
def equity_history(ctx):
    if not (ctx.can("partners.equity") or ctx.can("partners.ownership")):
        raise PermissionError()
    return ok({"rows": equity.history(ctx.db)})


@route("PUT", "/api/equity/ownership", "partners.ownership", "edit")
def equity_set(ctx):
    cid = equity.set_ownership(ctx.db, ctx.body, ctx.user["username"])
    ctx.log("equity.ownership", cid)
    return ok({"id": cid})


@route("DELETE", "/api/equity/history/<cid>", "partners.ownership", "edit")
def equity_undo(ctx, cid):
    with ctx.db.tx():
        equity.undo_change(ctx.db, int(cid))
    ctx.log("equity.undo", cid)
    return ok()


# ====================================================== parties & taxes
@route("GET", "/api/parties-master")
def parties_master(ctx):
    if not (ctx.can("sales.parties") or ctx.can("sales.invoices") or ctx.can("purchasing") or ctx.can("tax")):
        raise PermissionError()
    w, p = ["1=1"], []
    if ctx.q.get("type"):
        w.append("(type=? OR type='both')")
        p.append(ctx.q["type"])
    if ctx.q.get("q"):
        w.append("(name LIKE ? OR tax_id LIKE ? OR phone LIKE ?)")
        p += [f"%{ctx.q['q']}%"] * 3
    limit, offset = page(ctx.q)
    rows = ctx.db.q(f"SELECT * FROM parties WHERE {' AND '.join(w)} ORDER BY name LIMIT ? OFFSET ?", p + [limit, offset])
    total = ctx.db.one(f"SELECT COUNT(*) n FROM parties WHERE {' AND '.join(w)}", p)["n"]
    return ok({"rows": rows, "total": total})


@route("POST", "/api/parties-master")
def party_add(ctx):
    if not (ctx.can("sales.parties", "add") or ctx.can("sales.invoices", "add") or ctx.can("purchasing", "add")):
        raise PermissionError()
    return ok({"id": acc.save_party(ctx.db, ctx.body)})


@route("PUT", "/api/parties-master/<pid>")
def party_edit(ctx, pid):
    if not (ctx.can("sales.parties", "edit") or ctx.can("sales.invoices", "edit") or ctx.can("purchasing", "edit")):
        raise PermissionError()
    return ok({"id": acc.save_party(ctx.db, ctx.body, int(pid))})


@route("DELETE", "/api/parties-master/<pid>", "sales.parties", "edit")
def party_del(ctx, pid):
    p = ctx.db.one("SELECT name FROM parties WHERE id=?", (int(pid),))
    if not p:
        raise acc.ValidationError("Not found")
    x = ctx.db.one("SELECT number FROM extracts WHERE customer=? LIMIT 1", (p["name"],))
    if x:
        raise acc.ValidationError(f"{p['name']} is the customer on invoice {x['number']} — its tax ID and address are "
                                  "needed for that invoice and its e-invoice, so it cannot be deleted")
    ctx.db.x("DELETE FROM parties WHERE id=?", (int(pid),))
    ctx.log("party.delete", p["name"])
    return ok()


@route("GET", "/api/tax-codes")
def tax_codes(ctx):
    return ok(ctx.db.q("SELECT * FROM tax_codes ORDER BY kind, code"))


@route("POST", "/api/tax-codes", "tax.codes", "add")
def tax_code_add(ctx):
    return ok({"code": acc.save_tax_code(ctx.db, ctx.body)})


@route("PUT", "/api/tax-codes/<code>", "tax.codes", "edit")
def tax_code_edit(ctx, code):
    return ok({"code": acc.save_tax_code(ctx.db, ctx.body, old_code=code)})


@route("DELETE", "/api/tax-codes/<code>", "tax.codes", "edit")
def tax_code_del(ctx, code):
    db = ctx.db
    used = (db.one("SELECT number FROM extracts WHERE vat_code=? OR wht_code=? LIMIT 1", (code, code))
            or db.one("SELECT name FROM parties WHERE default_vat=? OR default_wht=? LIMIT 1", (code, code))
            or db.one("SELECT id FROM journal_entries WHERE meta LIKE ? OR meta LIKE ? LIMIT 1",
                      (f'%"vat_code": "{code}"%', f'%"wht_code": "{code}"%')))
    if used:
        raise acc.ValidationError(f"Tax code {code} is used on invoices, vouchers or customers — untick Active to stop "
                                  "using it instead (it stays on past documents)")
    db.x("DELETE FROM tax_codes WHERE code=?", (code,))
    ctx.log("tax_code.delete", code)
    return ok()


@route("GET", "/api/tax/summary", "tax.report")
def tax_summary(ctx):
    return ok(acc.tax_summary(ctx.db, ctx.q.get("date_from"), ctx.q.get("date_to")))


@route("GET", "/api/einvoice/settings", "tax.einvoice")
def eta_settings(ctx):
    return ok(einvoice.public_settings(ctx.db))


@route("PUT", "/api/einvoice/settings", "tax.einvoice", "edit")
def eta_settings_save(ctx):
    from .db import ETA_SETTINGS
    for k, v in ctx.body.items():
        if k not in ETA_SETTINGS or (k == "eta_client_secret" and (v or "").startswith("••")):
            continue
        if k == "eta_env" and v not in ("preprod", "prod"):
            raise acc.ValidationError("environment must be preprod or prod")
        if k == "eta_doc_version" and v not in ("0.9", "1.0"):
            raise acc.ValidationError("document version must be 0.9 or 1.0")
        ctx.db.set_setting(k, acc.text(v, maxlen=500))
    ctx.log("eta.settings")
    return ok(einvoice.public_settings(ctx.db))


@route("POST", "/api/einvoice/test", "tax.einvoice", "edit")
def eta_test(ctx):
    return ok(einvoice.test_connection(ctx.db))


@route("GET", "/api/einvoice/<xid>", "sales.invoices")
def eta_document(ctx, xid):
    doc, problems = einvoice.build_document(ctx.db, int(xid))
    if ctx.q.get("format") == "json":
        return download(json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"),
                        f"eta-{doc['internalID']}.json", "application/json; charset=utf-8")
    return ok({"document": doc, "problems": problems, "serialized": einvoice.serialize(doc)})


def _batch_ids(ctx):
    if ctx.q.get("ids"):
        return [int(i) for i in ctx.q["ids"].split(",") if i.strip().isdigit()]
    return [x["id"] for x in acc.list_extracts(ctx.db, bu_id=ctx.q.get("bu_id"), status="posted",
                                                date_from=ctx.q.get("date_from"), date_to=ctx.q.get("date_to"))]


@route("GET", "/api/einvoice-batch", "sales.invoices")
def eta_batch(ctx):
    ids = _batch_ids(ctx)
    if not ids:
        raise acc.ValidationError("No posted invoices match these filters")
    if ctx.q.get("format") == "xlsx":
        return download(einvoice.batch_xlsx(ctx.db, ids), f"eta-batch-{acc.today()}.xlsx", XLSX)
    return download(einvoice.batch_json(ctx.db, ids, signed=ctx.q.get("signed") == "1"),
                    f"eta-batch-{acc.today()}.json", "application/json; charset=utf-8")


@route("POST", "/api/einvoice/submit", "tax.einvoice", "edit")
def eta_submit(ctx):
    ids = [int(i) for i in ctx.body.get("ids") or []]
    if not ids:
        raise acc.ValidationError("Choose invoices to submit")
    return ok(einvoice.submit(ctx.db, ids, ctx.user["username"]))


@route("POST", "/api/einvoice/<xid>/refresh", "tax.einvoice")
def eta_refresh(ctx, xid):
    return ok(einvoice.refresh_status(ctx.db, int(xid)))


@route("POST", "/api/einvoice/<xid>/cancel", "tax.einvoice", "edit")
def eta_cancel(ctx, xid):
    return ok(einvoice.cancel(ctx.db, int(xid), ctx.body.get("reason")))


# =============================================================== dashboard
@route("GET", "/api/dashboard", "dashboard")
def dashboard(ctx):
    q = ctx.q
    return ok(acc.dashboard(ctx.db, q.get("date_from"), q.get("date_to"), q.get("bu_id") or None))


# ================================================================== reports
@route("GET", "/api/reports/<name>")
def report(ctx, name):
    if name == "vouchers":
        table = _vouchers_report(ctx)
    else:
        ctx.require(reports.REPORT_PERMS.get(name, "reports"), "view")
        table = reports.build(ctx.db, name, ctx.q)
    fmt = ctx.q.get("format") or "json"
    company = ctx.db.setting("company_name", "")
    fname = re.sub(r"[^\w\-]+", "_", table["title"])[:60] + f"_{acc.today()}"
    if fmt == "json":
        return ok(table)
    logo, wm = documents.report_brand(ctx.db, fmt)
    if fmt == "xlsx":
        data = documents.inject_xlsx_images(reports.to_xlsx(table, company), logo, wm, col=len(table["columns"]) + 1, px=60)
        return download(data, fname + ".xlsx", XLSX)
    if fmt == "docx":
        return download(reports.to_docx(table, company, logo, wm), fname + ".docx", DOCX)
    if fmt == "csv":
        return download(reports.to_csv(table), fname + ".csv", "text/csv; charset=utf-8")
    if fmt in ("html", "pdf"):
        return Reply(reports.to_html(table, company, auto_print=fmt == "pdf", font=ctx.db.setting("font_family"),
                                     size=int(ctx.db.setting("font_size", "13") or 13) - 2, logo=logo, wm=wm).encode("utf-8"),
                     ctype="text/html; charset=utf-8")
    raise acc.ValidationError("format must be json, xlsx, docx, csv, html or pdf")


# ============================================================ import/export
@route("GET", "/api/export/workbook", "import_export.export")
def export_workbook(ctx):
    from . import workbook
    data = workbook.build_workbook(ctx.db, template=False)
    return download(data, f"BEYOND_ERP_{acc.today()}.xlsx", XLSX)


@route("GET", "/api/templates/workbook", "import_export")
def template_workbook(ctx):
    from . import workbook
    return download(workbook.build_workbook(ctx.db, template=True), "BEYOND_ERP_Import_Template.xlsx", XLSX)


@route("GET", "/api/export/json", "import_export.export")
def export_json(ctx):
    return download(interchange.to_json(ctx.db).encode("utf-8"), f"BEYOND_ERP_{acc.today()}.json",
                    "application/json; charset=utf-8")


@route("GET", "/api/export/csv", "import_export")
def export_csv(ctx):
    sheet = ctx.q.get("sheet")
    if sheet not in interchange.SHEET_BY_NAME:
        raise acc.ValidationError("Unknown sheet")
    tpl = ctx.q.get("template") == "1"
    return download(interchange.to_csv(ctx.db, sheet, template=tpl),
                    re.sub(r"\W+", "_", sheet) + ("_template" if tpl else "") + ".csv", "text/csv; charset=utf-8")


@route("POST", "/api/import", "import_export.import", "edit")
def do_import(ctx):
    fmt = ctx.q.get("format") or "xlsx"
    raw = ctx.raw
    if not raw:
        raise acc.ValidationError("Upload a file")
    mode = ctx.q.get("mode") or "append"
    dry = ctx.q.get("dry_run") == "1"
    if mode == "replace" and not ctx.user["is_admin"]:
        raise PermissionError()
    if mode == "replace" and not dry:
        ctx.server.backup("before-import")
    if fmt == "json":
        data = interchange.read_json(raw)
    elif fmt == "csv":
        data = interchange.read_csv(ctx.q.get("sheet"), raw)
    elif fmt in ("xlsx", "legacy"):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(raw)
            tmp = f.name
        try:
            from . import legacy
            if fmt == "legacy" or legacy.looks_legacy(tmp):
                data = legacy.read_legacy_workbook(tmp)
            else:
                data = interchange.read_xlsx(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)
    else:
        raise acc.ValidationError("format must be xlsx, json, csv or legacy")
    if not any(data.values()):
        raise acc.ValidationError("No recognisable sheets or rows found in the file")
    try:
        res = interchange.import_data(ctx.db, data, mode=mode, dry_run=dry, user=ctx.user["username"])
    except interchange.ImportErrors as e:
        return ok({"ok": False, "error": str(e), "errors": e.errors}, 400)
    res["notes"] = data.get("_notes", [])
    ctx.log("import" + (".dry" if dry else ""), json.dumps(res["counts"]))
    return ok(res)


# ==================================================================== users
@route("GET", "/api/users", "users.users")
def users(ctx):
    return ok([auth.public_user(u) for u in ctx.db.q("SELECT * FROM users ORDER BY id")])


@route("POST", "/api/users", "users.users", "add")
def user_add(ctx):
    if ctx.body.get("is_admin") and not ctx.user["is_admin"]:
        raise PermissionError()
    uid = auth.create_user(ctx.db, ctx.body)
    ctx.log("user.add", ctx.body.get("username"))
    return ok({"id": uid})


@route("PUT", "/api/users/<uid>", "users.users", "edit")
def user_edit(ctx, uid):
    target = ctx.db.one("SELECT is_admin FROM users WHERE id=?", (int(uid),))
    if (ctx.body.get("is_admin") or (target and target["is_admin"])) and not ctx.user["is_admin"]:
        raise PermissionError()
    auth.update_user(ctx.db, int(uid), ctx.body, ctx.user["id"])
    ctx.log("user.edit", uid)
    return ok()


@route("DELETE", "/api/users/<uid>", "users.users", "edit")
def user_del(ctx, uid):
    target = ctx.db.one("SELECT is_admin FROM users WHERE id=?", (int(uid),))
    if target and target["is_admin"] and not ctx.user["is_admin"]:
        raise PermissionError()
    u = auth.delete_user(ctx.db, int(uid), ctx.user["id"])
    ctx.log("user.delete", u["username"])
    return ok({"username": u["username"], "email": u.get("email") or ""})


@route("PUT", "/api/me/password")
def my_password(ctx):
    u = ctx.db.one("SELECT * FROM users WHERE id=?", (ctx.user["id"],))
    if not auth.check_password(ctx.body.get("current", ""), u["pw_hash"]):
        raise acc.ValidationError("Current password is wrong")
    ctx.db.x("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_password(ctx.body.get("new", "")), u["id"]))
    return ok()


@route("GET", "/api/audit", "users.audit")
def audit_log(ctx):
    limit, offset = page(ctx.q)
    w, p = ["1=1"], []
    if ctx.q.get("username"):
        w.append("username=?")
        p.append(ctx.q["username"])
    if ctx.q.get("q"):
        w.append("(action LIKE ? OR detail LIKE ?)")
        p += [f"%{ctx.q['q']}%"] * 2
    return ok(ctx.db.q(f"SELECT * FROM audit_log WHERE {' AND '.join(w)} ORDER BY id DESC LIMIT ? OFFSET ?",
                       p + [limit, offset]))


# ======================================================= branding & documents
@route("GET", "/api/branding", "settings.branding")
def branding_get(ctx):
    return ok(branding.public(ctx.db))


@route("PUT", "/api/branding", "settings.branding", "edit")
def branding_put(ctx):
    b = branding.save(ctx.db, ctx.body)
    ctx.log("branding", ", ".join(ctx.body.keys()))
    return ok(b)


@route("PUT", "/api/branding/image/<key>", "settings.branding", "edit")
def branding_image(ctx, key):
    branding.set_asset(ctx.db, key, ctx.body if ctx.body.get("data") else None)
    ctx.log("branding.image", key + ("" if ctx.body.get("data") else " removed"))
    return ok()


def _file_reply(data_name, fmt):
    data, name = data_name
    if fmt in ("pdf", "html"):
        return Reply(data, ctype="text/html; charset=utf-8")
    return download(data, name, XLSX if name.endswith(".xlsx") else DOCX)


@route("GET", "/api/boqs/<bid>/file")
def boq_document(ctx, bid):
    if not (ctx.can("estimating.boqs") or ctx.can("sales.invoices")):
        raise PermissionError()
    fmt = "pdf" if ctx.q.get("format") in ("pdf", "html") else "xlsx"
    return _file_reply(documents.boq_file(ctx.db, int(bid), fmt), fmt)


@route("GET", "/api/extracts/<xid>/file", "sales.invoices")
def extract_document(ctx, xid):
    fmt = "pdf" if ctx.q.get("format") in ("pdf", "html") else "xlsx"
    return _file_reply(documents.invoice_file(ctx.db, int(xid), fmt), fmt)


@route("GET", "/api/offers", "sales.offers")
def offers_list(ctx):
    q = ctx.q
    return ok({"rows": documents.list_offers(ctx.db, q.get("type_id"), q.get("q"), q.get("status")),
               "types": documents.list_offer_types(ctx.db), "next_number": documents.next_offer_number(ctx.db)})


@route("GET", "/api/offers/<oid>", "sales.offers")
def offer_one(ctx, oid):
    return ok(documents.get_offer(ctx.db, int(oid)))


@route("POST", "/api/offers", "sales.offers", "add")
def offer_add(ctx):
    oid = documents.save_offer(ctx.db, ctx.body)
    ctx.log("offer.add", oid)
    return ok({"id": oid})


@route("PUT", "/api/offers/<oid>", "sales.offers", "edit")
def offer_edit(ctx, oid):
    documents.save_offer(ctx.db, ctx.body, int(oid))
    ctx.log("offer.edit", oid)
    return ok({"id": int(oid)})


@route("DELETE", "/api/offers/<oid>", "sales.offers", "edit")
def offer_del(ctx, oid):
    ctx.db.x("DELETE FROM offers WHERE id=?", (int(oid),))
    ctx.log("offer.delete", oid)
    return ok()


@route("POST", "/api/offers-preview", "sales.offers")
def offer_preview(ctx):
    o = dict(documents._content(ctx.body), client=ctx.body.get("client") or "Mr. Client",
             project=ctx.body.get("project") or "Sample Project", date=ctx.body.get("date") or acc.today())
    return ok({"html": documents.offer_html(ctx.db, o, preview=True)})


@route("GET", "/api/offers/<oid>/file", "sales.offers")
def offer_document(ctx, oid):
    o = documents.get_offer(ctx.db, int(oid))
    if ctx.q.get("format") in ("pdf", "html"):
        return Reply(documents.offer_html(ctx.db, o).encode("utf-8"), ctype="text/html; charset=utf-8")
    return download(documents.offer_docx(ctx.db, o), documents.offer_filename(o) + ".docx", DOCX)


@route("POST", "/api/offers/<oid>/bu", "business_units.units", "add")
def offer_to_bu(ctx, oid):
    o = documents.get_offer(ctx.db, int(oid))
    ctx.require("sales.offers", "edit")
    if o.get("bu_id"):
        raise acc.ValidationError("This offer is already linked to a business unit")
    year = acc.today()[:4]
    n = ctx.db.one("SELECT COUNT(*) AS n FROM business_units WHERE code LIKE ?", (f"PRJ-{year}-%",))["n"] + 1
    code = f"PRJ-{year}-{n:03d}"
    while ctx.db.one("SELECT 1 FROM business_units WHERE code=?", (code,)):
        n += 1
        code = f"PRJ-{year}-{n:03d}"
    bid = ctx.db.x("INSERT INTO business_units(code,name,location,status,basis,contract_value,start_date,customer) "
                   "VALUES (?,?,?,?,?,?,?,?)", (code, o.get("client") or o.get("project") or code, o.get("project") or "",
                                               "In Progress", "Fixed Price", to_minor(o["net"]), acc.today(),
                                               o.get("client") or "")).lastrowid
    ctx.db.x("UPDATE offers SET bu_id=?, status=CASE WHEN status='Draft' THEN 'Accepted' ELSE status END WHERE id=?",
             (bid, int(oid)))
    ctx.log("offer.bu", f"{oid} -> {code}")
    return ok({"id": bid, "code": code})


@route("GET", "/api/offer-types", "sales.offers")
def offer_types(ctx):
    return ok(documents.list_offer_types(ctx.db))


@route("POST", "/api/offer-types", "sales.offers", "add")
def offer_type_add(ctx):
    return ok({"id": documents.save_offer_type(ctx.db, ctx.body)})


@route("PUT", "/api/offer-types/<tid>", "sales.offers", "edit")
def offer_type_edit(ctx, tid):
    return ok({"id": documents.save_offer_type(ctx.db, ctx.body, int(tid))})


@route("DELETE", "/api/offer-types/<tid>", "sales.offers", "edit")
def offer_type_del(ctx, tid):
    ctx.db.x("DELETE FROM offer_types WHERE id=?", (int(tid),))
    return ok()


# ============================================================ erase all data
ERASE_TRANSACTIONS = ("stock_moves", "stock_levels", "extract_lines", "extracts", "ownership_changes", "journal_lines",
                      "journal_entries", "offers")
ERASE_ALL = ERASE_TRANSACTIONS + ("parties", "stock_items", "boq_lines", "boqs", "partner_shares", "partners", "rates",
                                  "fixed_assets", "business_units", "offer_types", "tax_codes", "assets")


@route("POST", "/api/erase")
def erase_data(ctx):
    """Administrator only. scope 'transactions' keeps master data; 'all' is a factory reset (users are kept)."""
    if not ctx.user["is_admin"]:
        raise PermissionError()
    if (ctx.body.get("confirm") or "").strip().upper() != "ERASE":
        raise acc.ValidationError('Type ERASE to confirm')
    scope = ctx.body.get("scope") if ctx.body.get("scope") in ("transactions", "all") else None
    if not scope:
        raise acc.ValidationError("Choose what to erase")
    backup = ctx.server.backup("before-erase")
    db = ctx.db
    with db.tx():
        for t in (ERASE_ALL if scope == "all" else ERASE_TRANSACTIONS):
            db.conn.execute(f"DELETE FROM {t}")
        if scope == "all":
            db.conn.execute("DELETE FROM warehouses")
            db.conn.execute("DELETE FROM accounts")
            db.conn.execute("DELETE FROM settings WHERE key NOT IN ('schema_version')")
            db.seed()
            db._migrate()
            db.seed_documents()
            db.conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('docs_seeded','1')")
    ctx.log("erase", f"{scope} (backup {backup.name})")
    return ok({"scope": scope, "backup": backup.name})


# =================================================================== system
@route("GET", "/api/backup", "settings.system", "edit")
def backup_download(ctx):
    path = ctx.server.backup("download")
    return download(path.read_bytes(), path.name, "application/octet-stream")


@route("POST", "/api/maintenance", "settings.system", "edit")
def maintenance(ctx):
    before = ctx.db.path.stat().st_size if ctx.db.path.exists() else 0
    ctx.server.backup("before-maintenance")
    ctx.db.maintenance()
    after = ctx.db.path.stat().st_size if ctx.db.path.exists() else 0
    return ok({"size_before": before, "size_after": after})


@route("GET", "/api/system", "settings.system")
def system(ctx):
    counts = {t: ctx.db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in
              ("journal_entries", "journal_lines", "business_units", "rates", "stock_moves", "audit_log", "users")}
    return ok({"version": __version__, "db_path": str(ctx.db.path),
               "db_size": ctx.db.path.stat().st_size if ctx.db.path.exists() else 0,
               "backups": [p.name for p in ctx.server.backups()], "counts": counts})


# ============================================================ shared workspace
@route("PUT", "/api/extracts/<xid>/eta-state", "tax.einvoice")
def eta_state(ctx, xid):
    """E-invoice status shared by the device that talked to the tax authority (see cloud.followups)."""
    b = ctx.body
    ctx.db.x("UPDATE extracts SET eta_uuid=?, eta_status=?, eta_submission=?, eta_error=? WHERE id=?",
             tuple(acc.text(b.get(k), maxlen=4000) for k in ("eta_uuid", "eta_status", "eta_submission", "eta_error"))
             + (int(xid),))
    return ok()


@route("POST", "/api/cloud/session", public=True)
def cloud_session(ctx):
    """Browser edition only: the page already signed in to the workspace (Supabase); open the matching app user."""
    if not getattr(ctx.app, "static", False):
        raise PermissionError()
    email = acc.text(ctx.body.get("email")).lower()
    email = cloud.username_for(email) or email   # created in Users without an email: internal address -> username
    u = ctx.db.one("SELECT * FROM users WHERE active=1 AND (lower(email)=? OR lower(username)=?) "
                   "ORDER BY lower(email)=? DESC LIMIT 1", (email, email, email))
    if not u:
        raise acc.ValidationError(f"{email} can use the workspace but no app user has this email yet — an administrator "
                                  "opens Users, edits (or adds) the person and enters this address in Email")
    import secrets
    token = secrets.token_urlsafe(32)
    ctx.db.x("INSERT INTO sessions(token,user_id,expires) VALUES (?,?,datetime('now',?))",
             (token, u["id"], f"+{auth.SESSION_DAYS} days"))
    return ctx.with_session(ok({"user": auth.public_user(u)}), token)


def workspace_preset():
    """The shared workspace this desktop build belongs to (written into the installer by the build), if any."""
    url, key = os.environ.get("BEYOND_SUPABASE_URL", ""), os.environ.get("BEYOND_SUPABASE_ANON_KEY", "")
    if not (url and key):
        try:
            d = json.loads((Path(__file__).resolve().parent / "workspace_preset.json").read_text())
            url, key = d.get("url", ""), d.get("key", "")
        except (OSError, ValueError):
            return None
    return (url, key) if url and key else None


@route("POST", "/api/cloud/first-join", public=True)
def cloud_first_join(ctx):
    """A new desktop install: sign in with the workspace account, download the shared books, open that user."""
    sync, preset = getattr(ctx.app, "cloud", None), workspace_preset()
    if not (sync and preset) or sync.active or not auth.needs_setup(ctx.db):
        raise PermissionError()
    name, pw = acc.text(ctx.body.get("username")), ctx.body.get("password") or ""
    try:
        sync.configure(*preset)
        info = sync.sign_in(name, pw)
        if ctx.body.get("company"):
            info = sync.choose_company(acc.text(ctx.body.get("company")))
        if info.get("needs_company"):
            live = [c for c in info.get("companies") or [] if c.get("active") and not c.get("pending")]
            sync.disconnect()
            if not live:
                raise acc.ValidationError("You are not a member of any company yet — open the website to create one or "
                                          "to join one with its invite code")
            return ok({"choose": live})   # several companies: the screen asks which one, then signs in again
        if not info.get("is_member"):
            raise acc.ValidationError("This account is not a member of this company — ask its administrator to add you")
        sync.join()
    except (PermissionError, RuntimeError, cloud.Offline, cloud.NeedsUpdate) as e:
        sync.disconnect()
        raise acc.ValidationError(str(e)) from None
    found = _workspace_login(ctx, name, pw)
    if not found:
        raise acc.ValidationError("Signed in, but no app user matches this account — ask an administrator to add your "
                                  "email or username in Users")
    token, user = found
    ctx.db.log(user["username"], "login")
    return ctx.with_session(ok({"user": user}), token)


def _cloud(ctx):
    c = getattr(ctx.app, "cloud", None)
    if c is None:
        raise acc.ValidationError("In the browser edition the workspace is managed by the page itself")
    if not ctx.user["is_admin"]:
        raise PermissionError()
    return c


def _cloud_call(fn):
    try:
        return ok(fn())
    except cloud.Offline as e:
        return ok({"error": str(e)}, 503)
    except (PermissionError, RuntimeError, cloud.NeedsUpdate) as e:
        return ok({"error": str(e) or "Not allowed"}, 404 if getattr(e, "status", 0) == 404 else 400)


@route("GET", "/api/cloud-status")
def cloud_status_light(ctx):
    """Sync indicator for every signed-in user (no settings exposed)."""
    c = getattr(ctx.app, "cloud", None)
    s = c.public_status() if c else {}
    return ok({k: s.get(k, "") for k in ("joined", "state", "error", "last_sync")})


@route("GET", "/api/cloud")
def cloud_status(ctx):
    return ok(_cloud(ctx).public_status())


@route("PUT", "/api/cloud/config")
def cloud_config(ctx):
    c = _cloud(ctx)
    c.configure(ctx.body.get("url"), ctx.body.get("key"))
    return ok(c.public_status())


@route("POST", "/api/cloud/signin")
def cloud_signin(ctx):
    c = _cloud(ctx)
    return _cloud_call(lambda: c.sign_in(acc.text(ctx.body.get("email")), ctx.body.get("password") or "",
                                         bool(ctx.body.get("create"))))


@route("GET", "/api/cloud/info")
def cloud_info(ctx):
    c = _cloud(ctx)
    return _cloud_call(c.info)


@route("POST", "/api/cloud/claim")
def cloud_claim(ctx):
    c = _cloud(ctx)
    return _cloud_call(lambda: c.claim(acc.text(ctx.body.get("name")) or None))


@route("POST", "/api/cloud/join")
def cloud_join(ctx):
    c = _cloud(ctx)
    return _cloud_call(lambda: c.join(acc.text(ctx.body.get("company")) or None))


@route("POST", "/api/cloud/company")
def cloud_company(ctx):
    """Companies: choose the one this device follows, create one, ask to join one, rename, new invite code."""
    c, b = _cloud(ctx), ctx.body
    act = b.get("action")
    fns = {"choose": lambda: c.choose_company(acc.text(b.get("company"))),
           "create": lambda: c.create_company(acc.text(b.get("name"))),
           "join": lambda: c.join_company(acc.text(b.get("code"))),
           "rename": lambda: c.rename_company(acc.text(b.get("name"))) or {"ok": True},
           "code": lambda: {"invite_code": c.new_invite_code()},
           "remove": lambda: c.remove_member(acc.text(b.get("email"))) or {"ok": True}}
    if act not in fns:
        return ok({"error": "Unknown action"}, 400)
    return _cloud_call(fns[act])


@route("POST", "/api/cloud/pull")
def cloud_pull(ctx):
    c = _cloud(ctx)
    return _cloud_call(lambda: {"applied": c.pull()})


@route("POST", "/api/cloud/disconnect")
def cloud_disconnect(ctx):
    _cloud(ctx).disconnect()
    return ok()


@route("GET", "/api/cloud/members")
def cloud_members(ctx):
    c = _cloud(ctx)
    return _cloud_call(c.members)


@route("PUT", "/api/cloud/members")
def cloud_member_set(ctx):
    c = _cloud(ctx)
    b = ctx.body
    return _cloud_call(lambda: c.set_member(b.get("email"), b.get("is_admin"), b.get("gateway"), b.get("active", True))
                       or {"ok": True})


@route("PUT", "/api/cloud/account")
def cloud_account(ctx):
    """Create / update a person's workspace sign-in account (supabase/functions/manage-user)."""
    c = _cloud(ctx)
    return _cloud_call(lambda: c.manage_user({k: ctx.body.get(k) for k in ("username", "email", "password", "full_name",
                                                                             "active")}))


@route("POST", "/api/cloud/account/delete")
def cloud_account_delete(ctx):
    """Delete a person's workspace sign-in account (supabase/functions/manage-user)."""
    c = _cloud(ctx)
    return _cloud_call(lambda: c.manage_user({"action": "delete", "username": ctx.body.get("username") or "",
                                              "email": ctx.body.get("email") or ""}))


@route("PUT", "/api/cloud/members/link")
def cloud_member_link(ctx):
    c = _cloud(ctx)
    b = ctx.body
    return _cloud_call(lambda: c.link_member(b.get("email"), b.get("username"), b.get("active", True)) or {"ok": True})


def serve_api(app, method, target, headers, raw=b""):
    """Entry point for every API call: changes go through the shared workspace when this device is connected."""
    sync = getattr(app, "cloud", None)
    if sync is None or not sync.active or method == "GET":
        return handle(app, method, target, headers, raw)
    user = None
    if headers.get("x-requested-with") == "BeyondERP":
        jar = cookies.SimpleCookie(headers.get("cookie") or "")
        user = auth.user_for_token(app.db, headers.get("x-session") or (jar[COOKIE].value if COOKIE in jar else None))
    if user and cloud.replicated(method, target):
        return sync.write(method, target, headers, raw, user)
    reply = handle(app, method, target, headers, raw)
    if user and reply.status < 400:
        try:
            body = json.loads(raw or b"{}") if raw else {}
        except ValueError:
            body = {}
        for m, path, data in cloud.followups(app.db, method, target, body):
            sync.write(m, path, {"x-requested-with": "BeyondERP", "content-type": "application/json"}, data, user)
    return reply


# ================================================================== handler
class Ctx:
    def __init__(self, app, db, user, token, q, body, raw, headers):
        self.app, self.db, self.user, self.token, self.q, self.body, self.raw = app, db, user, token, q, body, raw
        self.server = app  # backup()/backups() live on the app object
        self.headers = headers

    def can(self, module, level="view"):
        return auth.can(self.user, module, level)

    def require(self, module, level="view"):
        if not self.can(module, level):
            raise PermissionError()

    def log(self, action, detail=""):
        self.db.log(self.user["username"] if self.user else "", action, detail)

    def with_session(self, reply, token):
        if self.headers.get("x-beyond-static") == "1":  # browser-only build keeps the token itself
            body = json.loads(reply.body or b"{}")
            body["token"] = token
            reply.body = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            return reply
        c = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict"
        if os.environ.get("BEYOND_ERP_SECURE_COOKIE") == "1":  # set when served over HTTPS (hosting)
            c += "; Secure"
        c += f"; Max-Age={auth.SESSION_DAYS * 86400}" if token else "; Max-Age=0"
        reply.headers["Set-Cookie"] = c
        return reply


class _DryRunApp:
    """Stands in for the app during a dry run: no backups are written."""

    def __init__(self, app):
        self.db = app.db

    def backup(self, reason="auto"):
        return Path("dry-run.sqlite3")

    def backups(self):
        return []


def handle(app, method, target, headers, raw=b"", as_user=None, dry=False):
    """Transport-independent request handler: used by the HTTP server and by the browser build.

    headers: dict with lower-case keys. Returns a Reply.
    as_user: run as this username without a session (replaying a change from the shared workspace).
    dry: validation run inside a transaction the caller rolls back (no backups).
    """
    if dry:
        app = _DryRunApp(app)
    url = urlparse(target)
    db = app.db
    try:
        if len(raw or b"") > MAX_BODY:
            return ok({"error": "Upload too large (max 25 MB)"}, 413)
        if method != "GET" and headers.get("x-requested-with") != "BeyondERP":
            return ok({"error": "Missing X-Requested-With header"}, 403)
        body = {}
        if raw and "json" in (headers.get("content-type") or ""):
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise acc.ValidationError("JSON body must be an object")
        q = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=False).items()}
        jar = cookies.SimpleCookie(headers.get("cookie") or "")
        token = headers.get("x-session") or (jar[COOKIE].value if COOKIE in jar else None)
        if as_user is not None:
            token = None
            as_user = cloud.username_for(as_user) or as_user
            # workspace changes are recorded under the member's email; a gateway relays local usernames
            user = auth.public_user(db.one("SELECT * FROM users WHERE active=1 AND (username=? OR (email<>'' AND lower(email)=lower(?))) "
                                           "ORDER BY username=? DESC LIMIT 1", (as_user, as_user, as_user)))
        else:
            user = auth.user_for_token(db, token)
        for m, rx, module, level, public, fn in ROUTES:
            if m != method:
                continue
            match = rx.match(url.path)
            if not match:
                continue
            if not public and not user:
                return ok({"error": "Please sign in"}, 401)
            if module and not auth.can(user, module, level):
                return ok({"error": "You do not have permission for this"}, 403)
            ctx = Ctx(app, db, user, token, q, body, raw, headers)
            ctx.cloud_hook = getattr(app, "cloud", None)
            return fn(ctx, **match.groupdict())
        return ok({"error": "Not found"}, 404)
    except PermissionError:
        return ok({"error": "You do not have permission for this"}, 403)
    except (acc.ValidationError, LookupError, ValueError, json.JSONDecodeError) as e:
        return ok({"error": str(e)}, 400)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return ok({"error": f"Server error: {e.__class__.__name__}"}, 500)


class Handler(BaseHTTPRequestHandler):
    server_version = "BeyondERP/" + __version__
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet by default
        if self.server.verbose:
            super().log_message(fmt, *args)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def send(self, r: Reply):
        self.send_response(r.status)
        self.send_header("Content-Type", r.ctype)
        self.send_header("Content-Length", str(len(r.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        if r.filename:
            from urllib.parse import quote
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(r.filename)}")
        for k, v in r.headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(r.body)

    def static(self, path):
        rel = path.lstrip("/") or "index.html"
        target = (STATIC / rel).resolve()
        if not str(target).startswith(str(STATIC)) or not target.is_file():
            target = STATIC / "index.html"
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        body = target.read_bytes()
        r = Reply(body, 200, ctype)
        r.headers["Content-Security-Policy"] = ("default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                "script-src 'self'; img-src 'self' data:; frame-ancestors 'self'")
        self.send(r)

    def dispatch(self, method):
        sync = getattr(self.server, "cloud", None)
        if sync is not None and self.path.startswith("/api/") and not self.path.startswith("/api/cloud-status"):
            sync.touch()
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            if method != "GET":
                return self.send(ok({"error": "Not found"}, 404))
            return self.static(url.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self.send(ok({"error": "Upload too large (max 25 MB)"}, 413))
        raw = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        headers.pop("x-session", None)  # the HTTP server uses HttpOnly cookies only
        headers.pop("x-beyond-static", None)
        self.send(serve_api(self.server, method, self.path, headers, raw))


class ERPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, db, backup_dir, verbose=False):
        super().__init__(addr, Handler)
        self.db, self.backup_dir, self.verbose = db, Path(backup_dir), verbose
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.cloud = cloud.CloudSync(self)
        if self.cloud.active:
            self.cloud.start()

    def backups(self):
        return sorted(self.backup_dir.glob("beyond-erp-*.sqlite3"), reverse=True)

    def backup(self, reason="auto"):
        """Consistent online backup, keeping the newest N (bloat-proof rotation)."""
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = self.backup_dir / f"beyond-erp-{stamp}-{reason}.sqlite3"
        self.db.backup_to(dest)
        keep = int(self.db.setting("backup_keep", "14") or 14)
        for old in self.backups()[keep:]:
            old.unlink(missing_ok=True)
        return dest

    def daily_backup(self):
        today = _dt.date.today().strftime("%Y%m%d")
        if not any(p.name.startswith(f"beyond-erp-{today}") for p in self.backups()):
            self.backup("daily")

