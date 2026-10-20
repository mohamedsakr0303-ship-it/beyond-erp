"""HTTP server + JSON API (stdlib only).

Every route declares the module permission it needs; the admin user bypasses
checks. Sessions are HttpOnly SameSite=Strict cookies and every mutating
request must carry the X-Requested-With header (CSRF guard).
"""
import base64
import datetime as _dt
import json
import mimetypes
mimetypes.add_type("font/woff2", ".woff2")
import os
import re
import sys
import threading
import tempfile
import traceback
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import assets, bankrec, custody, entries, industries, payroll, equity, loans, recurring, rental_docs, rentals, sales, sales_docs, __version__
from . import accounting as acc
from . import auth, branding, cloud, countries, documents, textl10n, einvoice, interchange, looks, reports
from .db import ACTIONS, Database, MODULES, PERM_DEFAULTS, SUBMODULES, SUBTABS, SUBTYPES, to_major, to_minor

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 25 * 1024 * 1024
PAGE_MAX = 500
COOKIE = "beyond_session"

ROUTES = []


def route_module(method, path):
    """The permission section of the API route that handles method + path (None when unknown or open to all)."""
    m_doc = re.match(r"^/api/docs/(\w+)$", path)
    if m_doc and m_doc.group(1) in DOCS:          # one route, the section depends on the document type
        return DOCS[m_doc.group(1)][1]
    for m, rx, module, level, public, fn in ROUTES:
        if m == method and rx.match(path):
            return module
    return None


EXPORT_PATH = re.compile(r"/(export|pdf|xlsx|docx|csv|print|download|workbook|statement-pdf)(/|$)|\.(pdf|xlsx|docx|csv|zip)$")
ACTION_DENIED = {"export": "Exporting and printing are switched off for you on this screen",
                 "import": "Importing is switched off for you on this screen",
                 "delete": "Deleting and voiding are switched off for you on this screen"}


def request_action(method, path, q):
    """The switchable action a request performs on its screen: delete (deleting, voiding, reversing), import, or
    export (downloads, prints, files)."""
    if method == "DELETE" or (method in ("POST", "PUT") and re.search(r"/(void|reverse|cancel-entry)$", path)):
        return "delete"
    if method == "POST" and "/import" in path:
        return "import"
    if method == "GET" and (EXPORT_PATH.search(path) or (q.get("format") or "").lower() in ("pdf", "xlsx", "docx", "csv")):
        return "export"
    return None


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


def push_hint(obj, *items):
    """Ask the signed-in person's app to notify others' devices (the shared workspace's push function sends it, so it
    reaches them even with the app closed): {kind, to: [usernames], title, body, href, tag} now, or with "day" a
    reminder that morning, or {cancel: key}. Without a shared workspace the app ignores it."""
    items = [i for i in items if i and (i.get("to") or i.get("cancel"))]
    if items:
        obj = dict(obj or {})
        obj["_push"] = items
    return obj


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
                "bank_holder", "bank_swift", "bank_iban", "offer_validity_days", "offer_prefix", "offer_digits", "regional",
                "banks", "bank_default", "country", "invoice_style", "doc_lang")
PUBLIC_SETTINGS = COMPANY_KEYS + ("demo_loaded", "desk_site", "workday_location", "menu_labels", "reserve_pct", "legal_reserve_pct", "legal_reserve_cap", "funding_partner", "funding_limit", "report_from", "report_to", "lock_date", "hidden_modules",
                                  "appearance", "po_self_approve_limit", "menu_layout", "form_custom", "duplicate_check", "two_step_required")
JSON_SETTINGS = {"menu_labels": {}, "units": [], "hidden_modules": [], "appearance": {}, "regional": {}, "banks": [],
                 "menu_layout": {}, "form_custom": {}}


def _my_bus(db, user):
    """The projects (business units) this person works with: all of them, or only theirs (erp/scope.py)."""
    from . import scope
    rows = db.q("SELECT id, code, name, status, basis, negotiation, grp FROM business_units ORDER BY code")
    sc = scope.of(db, user)
    return [b for b in rows if b["id"] in sc.ids] if sc else rows


def settings_dict(db):
    s = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM settings")}
    out = {k: s.get(k, "") for k in PUBLIC_SETTINGS}
    for k, empty in JSON_SETTINGS.items():
        try:
            out[k] = json.loads(out[k] or json.dumps(empty))
        except json.JSONDecodeError:
            out[k] = empty
    out["banks"] = documents.banks(db)   # includes the single bank of books from before the list existed
    out["bank_default"] = documents.default_bank(db)["id"]
    out["country_profile"] = countries.profile(db)       # what the country (or its overrides) sets: invoice layout, language
    out["countries"] = countries.listing()
    return out


# changes allowed without a licence: signing in / out, own password and profile, entering the licence, previews (nothing
# saved), chat and the assistant's own conversations (the books stay read-only; the assistant's changes still need one)
LICENCE_FREE = re.compile(r"^/api/(login|logout|me/password|me/profile|licence|chat/.+|ai/.+|[\w-]+-preview(/[\w-]+)?)$")


@route("GET", "/api/licence")
def licence_get(ctx):
    from . import licence
    return ok({**licence.status(ctx.db, None if getattr(ctx.app, "licensing", False) else False),
               "vendor_key": licence.vendor_key() is not None})


@route("POST", "/api/licence")
def licence_set(ctx):
    from . import licence
    if not ctx.user.get("is_super"):
        raise SuperOnly("Only a super administrator can enter the licence")
    try:
        st = licence.activate(ctx.db, ctx.body.get("key") or "")
    except ValueError as e:
        raise acc.ValidationError(str(e))
    ctx.log("licence.activate", (st.get("licence") or {}).get("company", ""))
    return ok(st)


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
            "desktop_app": bool(getattr(sys, "frozen", False)),   # the installed offline app: it checks for updates
            "workspace_preset": bool(workspace_preset() and getattr(ctx.app, "cloud", None) and not ctx.app.cloud.active),
            "app_logo": app_logo and {"mime": app_logo["mime"], "data": app_logo["data"]},
            "app_icon": (lambda a: a and {"mime": a["mime"], "data": a["data"]})(branding.asset(db, "app_icon"))
            if (look["appearance"] or {}).get("app_icon") == "custom" else None,
            "settings": {k: v for k, v in look.items()
                         if k in ("company_name", "company_tagline", "font_family", "font_size", "language", "appearance",
                                  "regional")}}
    base["settings"]["business_type"] = db.setting("business_type") or ""   # the tour offers a tour for this kind of business
    if not user:
        return ok(base)
    from . import demo as _demo
    # a new company's first steps (start empty / with the demo / from an import) until they are done — shown again
    # after signing back in if the setup was left half-way
    base["onboarding"] = bool(user.get("is_admin")) and db.setting("onboarding") != "done" and _demo.is_empty(db)
    if getattr(ctx.app, "licensing", False):
        from . import licence
        lst = licence.status(db)
        base["licence"] = {k: lst.get(k) for k in ("enabled", "state", "can_write", "days_left", "problem", "warning", "updates_until")}
        if lst.get("licence"):
            base["licence"]["licensee"] = lst["licence"].get("company")
    from . import ai, chat
    base["profile"] = chat.profile(db, user["id"])
    base["user_since"] = (db.one("SELECT created_at FROM users WHERE id=?", (user["id"],)) or {}).get("created_at") or ""
    pol = ai.policy(db)
    base["ai"] = {"allowed": ai.allowed(pol, user), "used_today": ai.used_today(db, user), "has_key": ai.has_key(db),
                  "models": ai.MODELS, "providers": ai.PROVIDERS, "policy": pol if user.get("is_super") else {k: pol[k] for k in ("enabled", "name", "avatar", "write", "model", "daily_limit")}}
    sync = getattr(ctx.app, "cloud", None)
    base.update({
        "cloud": {"joined": bool(sync and sync.active)},
        "settings": settings_dict(db),
        "accounts": db.q("SELECT * FROM accounts ORDER BY code"),
        "bus": _my_bus(db, user),
        "warehouses": db.q("SELECT * FROM warehouses ORDER BY id"),
        "partners": db.q("SELECT * FROM partners ORDER BY id"),
        "items": db.q("SELECT * FROM stock_items ORDER BY name"),
        "tax_codes": db.q("SELECT * FROM tax_codes ORDER BY kind, code"),
        "parties": db.q("SELECT id, name, type, category, phone, whatsapp, contact_person, specialty, default_vat, "
                         "default_wht FROM parties ORDER BY name"),
        "contact_categories": list(acc.CONTACT_CATEGORIES),
        "offer_types": [{"id": t["id"], "name": t["name"]} for t in db.q("SELECT id, name FROM offer_types ORDER BY id")],
        "enums": {"billing_bases": acc.BILLING_BASES, "methods": countries.methods(db),
                  "payroll_types": list(acc.PAYROLL_TYPES), "stock_types": acc.STOCK_MOVE_TYPES,
                  "entry_kinds": acc.ENTRY_KINDS + sorted({k for k in entries.kinds(db) if k not in acc.ENTRY_KINDS}),
                  "subtypes": SUBTYPES, "modules": MODULES,
                  "submodules": SUBMODULES, "levels": list(auth.LEVELS), "subtabs": SUBTABS, "actions": list(ACTIONS), "perm_defaults": PERM_DEFAULTS,
                  "doc_kinds": [{"key": k, "en": en, "ar": ar} for k, en, ar, _, _ in branding.DOC_KINDS],
                  "entry_types": entries.for_client(db), "new_menu": entries.menu(db),
                  "menu_items": [{"key": k, "en": en, "ar": ar, "module": m} for k, en, ar, m in entries.MENU_ITEMS],
                  "sheets": [{"name": s["name"], "ar": s["ar"], "module": s["module"]} for s in interchange.SHEETS]},
    })
    base["settings"]["business_type"] = db.setting("business_type") or ""
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
        textl10n.seed_names(ctx.db, b["language"])          # the main store, the general chat room… in that language
    token, user = auth.login(ctx.db, b.get("username"), b.get("password"))
    ctx.db.log(user["username"], "setup", "first administrator created")
    if b.get("demo") in (True, 1, "1", "true", "on"):     # a new, unshared company can start with the demo company
        from . import demo
        kind = b.get("business_type") if b.get("business_type") in demo.INDUSTRY_KEYS else None
        if kind:
            industries.apply(ctx.db, kind, True)
        demo.load(ctx.db, user["username"], industry=kind, lang=b.get("language") if b.get("language") in ("en", "ar") else "en")
    return ctx.with_session(ok({"user": user}), token)


@route("POST", "/api/login", public=True)
def login(ctx):
    try:
        token, user = auth.login(ctx.db, ctx.body.get("username"), ctx.body.get("password"), ctx.body.get("code"))
    except auth.SecondStepNeeded as e:          # right password: now the code from the authenticator app
        return ok({"error": str(e), "two_step": True}, 400)
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
COMPANY_LOOK = ("look", "theme", "custom_themes", "density", "corners", "accent", "sidebar")
LOCKED_LOOK = ("app_name", "logo", "brand_show", "app_icon", "brand_align", "brand_logo_size", "brand_name_align", "brand_name_size",
               "brand_tag_align", "brand_tag_size", "tagline", "tagline_show")


@route("PUT", "/api/settings")
def put_settings(ctx):
    keys = set(ctx.body)
    # arranging the menu and the forms (right-click, drag and drop) is its own permission; menu names also from Settings → Menus
    if keys & {"menu_layout", "form_custom"} and not auth.granted(ctx.user, "arrange_forms"):
        ctx.require("settings.layout", "edit")
    if "form_custom" in keys and not auth.granted(ctx.user, "required_fields"):
        # making a field mandatory (or optional again) is a special right
        req = lambda fc: {(r, k) for r, fs in (fc or {}).items() if isinstance(fs, dict) for k, o in fs.items() if isinstance(o, dict) and o.get("req")}  # noqa: E731
        old = json.loads(ctx.db.setting("form_custom") or "{}") if ctx.db.setting("form_custom") else {}
        if req(old) != req(ctx.body.get("form_custom") if isinstance(ctx.body.get("form_custom"), dict) else {}):
            raise PermissionError("Only a super administrator, or an authorised user, can make fields required.")
    if "menu_labels" in keys and not ctx.can("settings.layout", "edit"):
        ctx.require("settings.menus", "edit")
    if keys & set(COMPANY_KEYS):
        ctx.require("settings.company", "edit")
    if "appearance" in keys:
        ctx.require("settings.branding", "edit")
        if isinstance(ctx.body.get("appearance"), dict):
            ap_new, ap_cur = ctx.body["appearance"], (settings_dict(ctx.db).get("appearance") or {})
            if "look" in ap_new:
                ap_new["look"] = looks.clean_look(ap_new["look"])
            if "custom_themes" in ap_new:
                ap_new["custom_themes"] = looks.clean_themes(ap_new["custom_themes"])
            # the look everyone gets (colours, pictures, density…) is the super administrators' call
            if not ctx.user.get("is_super"):
                for k in COMPANY_LOOK:
                    if k not in ap_new and k in ap_cur:
                        ap_new[k] = ap_cur[k]
                if any(json.dumps(ap_new.get(k), sort_keys=True) != json.dumps(ap_cur.get(k), sort_keys=True) for k in COMPANY_LOOK):
                    raise PermissionError("Only a super administrator can set the look for the whole company")
        if not ctx.user.get("platform_owner") and isinstance(ctx.body.get("appearance"), dict):
            # the BeyondERP name, logo and icon in the app are fixed: only the platform owner changes them
            cur = (settings_dict(ctx.db).get("appearance") or {})
            for k in LOCKED_LOOK:
                if k in cur:
                    ctx.body["appearance"][k] = cur[k]
                else:
                    ctx.body["appearance"].pop(k, None)
    if keys & {"reserve_pct", "legal_reserve_pct", "legal_reserve_cap", "funding_partner", "funding_limit"} and not ctx.can("partners.equity", "edit"):
        ctx.require("settings.company", "edit")
    if keys & {"backup_keep", "lock_date", "po_self_approve_limit", "duplicate_check"}:
        ctx.require("settings.system", "edit")
    if "two_step_required" in keys:
        if not ctx.user.get("is_super"):
            raise PermissionError("Only a super administrator decides who must use two-step sign-in")
        if ctx.body["two_step_required"] not in ("", "admins", "all"):
            raise acc.ValidationError("two_step_required is '', 'admins' or 'all'")
        if ctx.body["two_step_required"] and not ctx.user.get("two_step") and getattr(ctx.app, "static", False) is not True:
            raise PermissionError("Enable two-step sign-in for yourself first (profile), then require it for others.")
    if "hidden_modules" in keys:
        ctx.require("settings.menus", "edit")
    for k, ok_vals in (("country", set(countries.BY_CODE)), ("invoice_style", set(countries.STYLES)), ("doc_lang", {"ar", "en"})):
        if k in keys:
            ctx.body[k] = acc.text(ctx.body[k]).upper() if k == "country" else acc.text(ctx.body[k])
            if ctx.body[k] and ctx.body[k] not in ok_vals:
                raise acc.ValidationError(f"{k}: unknown value '{ctx.body[k]}'")
    allowed = set(PUBLIC_SETTINGS) | {"backup_keep"}
    if ctx.body.get("language") in ("en", "ar") and ctx.body["language"] != (ctx.db.setting("language") or "en"):
        textl10n.seed_names(ctx.db, ctx.body["language"])     # the company's language changed: its starting records follow
    if "banks" in ctx.body:   # several bank accounts; one is the default used on documents
        seen, clean = set(), []
        for b in ctx.body["banks"] if isinstance(ctx.body["banks"], list) else []:
            if not isinstance(b, dict):
                continue
            row = {"id": acc.text(b.get("id"), maxlen=40), "label": acc.text(b.get("label"), maxlen=120),
                   **documents.clean_bank(b)}
            if not any(row[k] for k in documents.BANK_FIELDS):
                continue
            if not row["id"] or row["id"] in seen or row["id"] == "custom":
                row["id"] = "b" + str(len(clean) + 1)
                while row["id"] in seen:
                    row["id"] += "x"
            seen.add(row["id"])
            clean.append(row)
        ctx.body["banks"] = clean
        want = acc.text(ctx.body.get("bank_default", ctx.db.setting("bank_default", "")))
        dflt = next((b for b in clean if b["id"] == want), clean[0] if clean else None)
        ctx.body["bank_default"] = dflt["id"] if dflt else ""
        # older screens and exports read these
        for k, f in (("bank_name", "bank"), ("bank_account", "acct"), ("bank_holder", "holder"),
                     ("bank_swift", "swift"), ("bank_iban", "iban")):
            ctx.body[k] = dflt[f] if dflt else ""
    for k, v in ctx.body.items():
        if k not in allowed:
            continue
        if k in JSON_SETTINGS:
            if not isinstance(v, type(JSON_SETTINGS[k])):
                raise acc.ValidationError(f"{k} has the wrong shape")
            v = json.dumps(v, ensure_ascii=False)
            if len(v) > (200_000 + looks.MAX_IMAGES + looks.MAX_FONT if k == "appearance" else 200_000):   # the company look may carry background pictures
                raise acc.ValidationError(f"{k} is too large")
        if k == "lock_date" and v:
            v = acc.clean_date(v, "lock date")
        if k == "fiscal_year_start":
            v = acc.text(v) or "01-01"
            try:
                _m, _d = (int(x) for x in v.split("-"))
                _dt.date(2001, _m, _d)
            except (ValueError, TypeError):
                raise acc.ValidationError("The financial year start looks like 01-01 (month-day)") from None
            v = f"{_m:02d}-{_d:02d}"
            if v != (ctx.db.setting("fiscal_year_start") or "01-01") and ctx.db.one("SELECT 1 FROM year_closes"):
                raise acc.ValidationError("Closed financial years depend on the current start date. Reopen them before changing it.")
        if k == "font_size":
            v = str(max(11, min(22, int(float(v or 15)))))
        if k in ("reserve_pct", "legal_reserve_pct", "legal_reserve_cap"):
            v = str(max(0.0, min(1.0, acc.num(v, "reserve %"))))
        # JSON settings were size-checked above; cutting them short would leave broken JSON behind
        ctx.db.set_setting(k, v if k in JSON_SETTINGS else acc.text(v, maxlen=20000))
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
    try:
        eid = _dup_checked(ctx, lambda: entries.post(ctx.db, tid, ctx.body, ctx.user["username"]))
    except _Duplicate as e:
        return _dup_reply(e)
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


def _bu_group(ctx, bid):
    """The project's group (several projects under one name), when the form sends one."""
    if "grp" in ctx.body:
        ctx.db.x("UPDATE business_units SET grp=? WHERE id=?", (acc.text(ctx.body.get("grp"), maxlen=80), int(bid)))


def _bu_negotiation(ctx, bid):
    """keep = negotiated discounts are company earnings (client billed on the full price); pass = lower cost."""
    if "negotiation" in ctx.body:
        m = acc.text(ctx.body.get("negotiation")).lower()
        if m and m not in acc.NEGOTIATION_MODES:
            raise acc.ValidationError("Negotiation handling must be 'keep' or 'pass'")
        ctx.db.x("UPDATE business_units SET negotiation=? WHERE id=?", (m, int(bid)))
    if "wip_mode" in ctx.body:
        w = acc.text(ctx.body.get("wip_mode")).lower()
        ctx.db.x("UPDATE business_units SET wip_mode=? WHERE id=?", (w if w in ("cost_plus", "drafts") else "", int(bid)))
    from . import wip
    with ctx.db.tx():
        wip.sync(ctx.db, int(bid))


@route("GET", "/api/bus", "business_units.units")
def bus(ctx):
    q = ctx.q
    rows = acc.bu_summaries(ctx.db, q.get("date_from"), q.get("date_to"), q.get("status"), q.get("q"))
    if q.get("basis"):
        rows = [r for r in rows if r["basis"] == q["basis"]]
    return ok({"rows": rows,
               "shared_overhead": acc.shared_overhead_total(ctx.db, q.get("date_from"), q.get("date_to"))})


@route("POST", "/api/bus", "business_units.units", "add")
def add_bu(ctx):
    v = _bu_values(ctx.body)
    if ctx.db.one("SELECT 1 FROM business_units WHERE code=?", (v[0],)):
        raise acc.ValidationError("Code already exists")
    bid = ctx.db.x(f"INSERT INTO business_units({','.join(BU_FIELDS)}) VALUES ({','.join('?' * len(BU_FIELDS))})",
                   v).lastrowid
    _bu_negotiation(ctx, bid)
    _bu_group(ctx, bid)
    ctx.log("bu.add", v[0])
    return ok({"id": bid})


@route("PUT", "/api/bus/<bid>", "business_units.units", "edit")
def edit_bu(ctx, bid):
    v = _bu_values(ctx.body)
    if ctx.db.one("SELECT 1 FROM business_units WHERE code=? AND id<>?", (v[0], int(bid))):
        raise acc.ValidationError("Code already exists")
    ctx.db.x(f"UPDATE business_units SET {','.join(f + '=?' for f in BU_FIELDS)} WHERE id=?", v + (int(bid),))
    _bu_negotiation(ctx, bid)
    _bu_group(ctx, bid)
    ctx.log("bu.edit", v[0])
    return ok()


@route("PUT", "/api/bus-allocation", "business_units.allocation", "edit")
def bu_allocation(ctx):
    """Set every unit's share of the shared (indirect) operating expenses at once. Total may not exceed 100%."""
    alloc = {int(k): acc.num(v, "allocation") for k, v in (ctx.body.get("allocations") or {}).items()}  # fractions
    if sum(alloc.values()) > 1.00001:
        raise acc.ValidationError(f"Allocations total {sum(alloc.values()) * 100:.2f}%; the total cannot exceed 100%.")
    with ctx.db.tx():
        for bid, v in alloc.items():
            ctx.db.conn.execute("UPDATE business_units SET overhead_pct=? WHERE id=?", (round(v, 6), bid))
    ctx.log("bu.allocation", json.dumps(alloc))
    return ok()


@route("DELETE", "/api/bus/<bid>", "business_units.units", "edit")
def del_bu(ctx, bid):
    if ctx.db.one("SELECT 1 FROM journal_entries WHERE bu_id=? LIMIT 1", (int(bid),)):
        raise acc.ValidationError("This business unit has transactions. Set its status to Closed instead.")
    ctx.db.x("DELETE FROM business_units WHERE id=?", (int(bid),))
    ctx.log("bu.delete", bid)
    return ok()


# ================================================================== journal
def journal_filters(q):
    w, p = ["1=1"], []
    if q.get("status") in ("posted", "void"):
        w.append("e.status=?")
        p.append(q["status"])
    elif q.get("status") == "reversed":      # reversed, or the reversing entry itself
        w.append("(e.reversed_by IS NOT NULL OR e.reversal_of IS NOT NULL)")
    elif q.get("status") == "edited":        # corrected after posting (its earlier versions are kept)
        w.append("e.history IS NOT NULL AND e.history NOT IN ('', '[]')")
    if q.get("created_by"):                 # who entered it (or, for an approved draft, who made it)
        w.append("(e.created_by=? OR json_extract(e.meta,'$.entered_by')=?)")
        p += [q["created_by"], q["created_by"]]
    if q.get("approved_by"):
        w.append("json_extract(e.meta,'$.approved_by')=?")
        p.append(q["approved_by"])
    if q.get("created_from"):             # when it was keyed in (not the entry's own date)
        w.append("substr(e.created_at,1,10)>=?")
        p.append(acc.clean_date(q["created_from"]))
    if q.get("created_to"):
        w.append("substr(e.created_at,1,10)<=?")
        p.append(acc.clean_date(q["created_to"]))
    if q.get("tag"):
        w.append("EXISTS (SELECT 1 FROM entry_tags t WHERE t.entry_id=e.id AND t.tag=?)")
        p.append(q["tag"])
    if q.get("bu_group"):                 # every project in a group
        w.append("e.bu_id IN (SELECT id FROM business_units WHERE grp=?)")
        p.append(q["bu_group"])
    if q.get("has_attachment") in ("1", "yes"):
        w.append("EXISTS (SELECT 1 FROM attachments t WHERE t.owner='entry' AND t.owner_id=e.id)")
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
    for word in (q.get("q") or "").split():   # every word must match something on the entry
        s = f"%{word}%"
        cond = ["e.memo LIKE ?", "e.party LIKE ?", "e.ref LIKE ?", "e.meta LIKE ?", "e.kind LIKE ?", "e.date LIKE ?",
                "CAST(e.id AS TEXT)=?", "e.bu_id IN (SELECT id FROM business_units WHERE code LIKE ? OR name LIKE ?)",
                "EXISTS (SELECT 1 FROM journal_lines l JOIN accounts a ON a.code=l.account WHERE l.entry_id=e.id AND "
                "(l.account LIKE ? OR a.name LIKE ? OR a.name_ar LIKE ? OR l.party LIKE ?))"]
        p += [s, s, s, s, s, s, word.lstrip("#"), s, s, word + "%", s, s, s]
        amt = word.replace(",", "")
        try:
            minor = to_minor(float(amt))
            cond.append("EXISTS (SELECT 1 FROM journal_lines l WHERE l.entry_id=e.id AND (l.debit=? OR l.credit=?))")
            p += [minor, minor]
        except (ValueError, OverflowError):
            pass
        w.append("(" + " OR ".join(cond) + ")")
    if q.get("min_amount"):
        w.append("(SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) >= ?")
        p.append(to_minor(acc.num(q["min_amount"])))
    if q.get("max_amount"):
        w.append("(SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) <= ?")
        p.append(to_minor(acc.num(q["max_amount"])))
    return " AND ".join(w), p


@route("GET", "/api/journal", "accounting.journal")
def journal(ctx):
    w, p = journal_filters(ctx.q)
    if getattr(ctx, "scope", None):         # only the person's projects (counts and pages too)
        w += f" AND e.bu_id IN ({','.join('?' * len(ctx.scope.ids)) or 'NULL'})"
        p = p + sorted(ctx.scope.ids)
    limit, offset = page(ctx.q)
    total = ctx.db.one(f"SELECT COUNT(*) AS n, COALESCE(SUM((SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id)),0) "
                       f"AS amt FROM journal_entries e WHERE {w} AND e.status='posted'", p)
    count = ctx.db.one(f"SELECT COUNT(*) AS n FROM journal_entries e WHERE {w}", p)["n"]
    ids = [r["id"] for r in ctx.db.q(f"SELECT e.id FROM journal_entries e WHERE {w} ORDER BY e.date DESC, e.id DESC "
                                     f"LIMIT ? OFFSET ?", p + [limit, offset])]
    people = sorted({r["u"] for r in ctx.db.q("SELECT DISTINCT created_by AS u FROM journal_entries WHERE created_by<>'' "
                                                "UNION SELECT DISTINCT json_extract(meta,'$.approved_by') FROM journal_entries "
                                                "WHERE json_extract(meta,'$.approved_by') IS NOT NULL") if r["u"]})
    from . import approvals
    drafts = len(approvals.listing(ctx.db, ctx.user, ctx.can, {"status": "pending"}))    # the ones this person may see
    return ok({"rows": [acc.get_entry(ctx.db, i) for i in ids], "total": count, "posted_amount": to_major(total["amt"]),
               "limit": limit, "offset": offset, "people": people, "drafts": drafts,
               "tags": [r["tag"] for r in ctx.db.q("SELECT tag, COUNT(*) n FROM entry_tags GROUP BY tag ORDER BY n DESC, tag LIMIT 200")],
               "bu_groups": [r["grp"] for r in ctx.db.q("SELECT DISTINCT grp FROM business_units WHERE grp<>'' ORDER BY grp")]})


@route("PUT", "/api/journal/<eid>/tags")
def journal_tags(ctx, eid):
    """Tags on an entry: whoever may add that kind of entry (or edit the journal) may label it."""
    e = ctx.db.one("SELECT kind FROM journal_entries WHERE id=?", (int(eid),))
    if not e:
        raise acc.ValidationError("Entry not found")
    if not (ctx.can("accounting.journal", "edit") or ctx.can(KIND_MODULE.get(e["kind"], "accounting.journal"), "add")):
        raise PermissionError()
    return ok({"tags": acc.set_tags(ctx.db, int(eid), ctx.body.get("tags") or [])})


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
    e["doc"] = FORM_DOCS.get(e["kind"])
    e["form"] = acc.doc_form(ctx.db, e) if e["doc"] else None
    if e["meta"].get("form"):
        del e["meta"]["form"]
    if not e["form"]:
        e["doc"] = None
    return ok(e)


def _to_approval(ctx, dtype, body, run, target_id=None, module="accounting.journal"):
    """Someone whose entries need approval: check it now (rolled back), keep it as a draft waiting for an approver."""
    from . import approvals
    approvals.check(ctx.db, run)
    aid = approvals.submit(ctx.db, ctx.user, dtype, body, target_id, module)
    ctx.log("approval.submit", f"{dtype} #{aid}")
    return ok({"id": None, "approval": aid, "pending": True})


class _Duplicate(Exception):
    pass


def _dup_checked(ctx, make):
    """A new entry typed in now: when it looks like one already in the books, nothing is saved and the screen is told
    (HTTP 409 with the look-alikes) so the person can confirm it really is another one (allow_duplicate) or cancel.
    Replays of the shared log, imports and confirmed entries go straight through."""
    from .db import CLOCK
    if CLOCK["at"] is not None or ctx.body.get("allow_duplicate") or ctx.db.setting("duplicate_check") == "off":
        return make()
    with ctx.db.tx():
        eid = make()
        dups = acc.possible_duplicates(ctx.db, eid) if isinstance(eid, int) else []
        if dups:
            raise _Duplicate(dups)
    return eid


def _dup_reply(e):
    return ok({"error": "This looks like an entry that is already in the books", "code": "duplicate", "duplicates": e.args[0]}, 409)


@route("POST", "/api/journal", "accounting.journal", "add")
def journal_add(ctx):
    from . import approvals
    try:
        if approvals.needs_approval(ctx.db, ctx.user):
            return _to_approval(ctx, "journal", ctx.body, lambda: _dup_checked(ctx, lambda: acc.record_journal(ctx.db, ctx.body, ctx.user["username"])))
        eid = _dup_checked(ctx, lambda: acc.record_journal(ctx.db, ctx.body, ctx.user["username"]))
    except _Duplicate as e:
        return _dup_reply(e)
    ctx.log("journal.add", eid)
    return ok({"id": eid})


@route("PUT", "/api/journal/<eid>", "accounting.journal", "add")
def journal_edit(ctx, eid):
    from . import approvals
    if approvals.needs_approval(ctx.db, ctx.user):      # a proposed correction: adding rights are enough, it waits
        return _to_approval(ctx, "journal", ctx.body, lambda: acc.edit_entry(ctx.db, int(eid), ctx.body, ctx.user["username"]), int(eid))
    ctx.require("accounting.journal", "edit")
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
    "supplier_discount": (acc.record_supplier_discount, "purchasing.payments"),
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
    from .db import CLOCK
    kw = {"strict": True} if dtype == "direct_cost" and CLOCK["at"] is None else {}  # typed in now, not a replay
    from . import approvals
    run = (lambda: fn(ctx.db, ctx.body, ctx.user["username"], **kw)) if dtype == "stock_move" else \
        (lambda: _dup_checked(ctx, lambda: fn(ctx.db, ctx.body, ctx.user["username"], **kw)))
    try:
        if approvals.needs_approval(ctx.db, ctx.user):
            return _to_approval(ctx, dtype, ctx.body, run, None, module)
        eid = run()
    except _Duplicate as e:
        return _dup_reply(e)
    _remember_form(ctx.db, dtype, eid, ctx.body)
    ctx.log(f"doc.{dtype}", eid)
    return ok({"id": eid})


FORM_DOCS = {"Direct Cost": "direct_cost", "Operating Expense": "expense", "Payment Voucher": "payment",
             "Supplier Discount": "supplier_discount", "Payroll Accrual": "payroll", "Receipt Voucher": "receipt",
             "Capital Contribution": "partner_tx", "Owner Drawing": "partner_tx"}


def _remember_form(db, dtype, eid, body):
    """Keep what was typed so the entry can be edited later through the same form."""
    if dtype == "stock_move" or not isinstance(eid, int):
        return
    row = db.one("SELECT meta FROM journal_entries WHERE id=?", (eid,))
    if not row:
        return
    meta = json.loads(row["meta"] or "{}")
    meta["form"] = {k: v for k, v in body.items() if (isinstance(v, (str, int, float, bool)) or v is None) and k != "allow_duplicate"}
    db.x("UPDATE journal_entries SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), eid))


@route("PUT", "/api/docs/<dtype>/<eid>")
def edit_doc(ctx, dtype, eid):
    """Correct a document through its own form (deal reference, negotiation, amounts...): same number, new lines,
    the old version kept in its history."""
    if dtype not in DOCS or dtype == "stock_move":
        return ok({"error": "Unknown document"}, 404)
    fn, module = DOCS[dtype]
    from . import approvals
    drafting = approvals.needs_approval(ctx.db, ctx.user)      # a proposed correction: adding rights are enough
    if not (ctx.can(module, "edit") or ctx.can("accounting.journal", "edit") or (drafting and ctx.can(module, "add"))):
        raise PermissionError()
    from .db import CLOCK
    e = acc.get_entry(ctx.db, int(eid))
    if not e or FORM_DOCS.get(e["kind"]) != dtype:
        raise acc.ValidationError("This entry is not a " + dtype.replace("_", " "))
    kw = {"strict": True} if dtype == "direct_cost" and CLOCK["at"] is None else {}
    if drafting:
        return _to_approval(ctx, dtype, ctx.body, lambda: acc.replace_doc(ctx.db, int(eid), fn, ctx.body, ctx.user["username"], **kw),
                            int(eid), module)
    with ctx.db.tx():
        acc.replace_doc(ctx.db, int(eid), fn, ctx.body, ctx.user["username"], **kw)
        _remember_form(ctx.db, dtype, int(eid), ctx.body)
    ctx.log(f"doc.{dtype}.edit", eid)
    return ok({"id": int(eid)})


# ---------------------------------------------------------------- entries waiting for approval (erp/approvals.py)
@route("GET", "/api/approvals")
def approvals_list(ctx):
    from . import approvals
    return ok({"rows": approvals.listing(ctx.db, ctx.user, ctx.can, ctx.q),
               "makers": [r["created_by"] for r in ctx.db.q("SELECT DISTINCT created_by FROM approvals ORDER BY created_by")]})


@route("GET", "/api/approvals/<aid>")
def approval_one(ctx, aid):
    from . import approvals
    r = approvals.get(ctx.db, aid)
    r["can_approve"] = r["status"] == "pending" and approvals.can_approve(ctx.user, ctx.can, r)
    r["mine"] = r["created_by"] == ctx.user["username"]
    if not (r["mine"] or r["can_approve"] or approvals.may_see(ctx.user, ctx.can, r)):
        raise PermissionError()
    if r["target_id"]:
        r["target"] = acc.get_entry(ctx.db, int(r["target_id"]))
    return ok(r)


def _post_approved(ctx, r, body):
    """Run what the maker's form would have run, under the maker's name."""
    maker, dtype, tid = r["created_by"], r["dtype"], r["target_id"]
    if dtype == "journal":
        if tid:
            acc.edit_entry(ctx.db, int(tid), body, maker)
            return int(tid)
        return acc.record_journal(ctx.db, body, maker)
    fn, _module = DOCS[dtype]
    from .db import CLOCK
    kw = {"strict": True} if dtype == "direct_cost" and CLOCK["at"] is None else {}
    if tid:
        acc.replace_doc(ctx.db, int(tid), fn, body, maker, **kw)
        eid = int(tid)
    else:
        eid = fn(ctx.db, body, maker, **kw)
    _remember_form(ctx.db, dtype, eid, body)
    return eid


@route("POST", "/api/approvals/<aid>/approve")
def approval_approve(ctx, aid):
    from . import approvals
    r = approvals.get(ctx.db, aid)
    if r["status"] != "pending":
        raise acc.ValidationError("This draft is no longer waiting for approval")
    if not approvals.can_approve(ctx.user, ctx.can, r):
        raise PermissionError()
    body = ctx.body.get("payload") if isinstance(ctx.body.get("payload"), dict) else r["payload"]
    with ctx.db.tx():
        if body is not r["payload"]:
            approvals.update_payload(ctx.db, aid, body)
        eid = _post_approved(ctx, r, body)
        if isinstance(eid, int):
            row = ctx.db.one("SELECT meta FROM journal_entries WHERE id=?", (eid,))
            if row:
                meta = json.loads(row["meta"] or "{}")
                meta.update(approved_by=ctx.user["username"], entered_by=r["created_by"])
                ctx.db.x("UPDATE journal_entries SET meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), eid))
        approvals.decide(ctx.db, aid, ctx.user, "approved", ctx.body.get("note") or "", eid if isinstance(eid, int) else None)
        if isinstance(eid, int):            # the bills attached to the draft now belong to the entry
            from . import attachments
            attachments.move(ctx.db, "approval", int(aid), "entry", eid)
    ctx.log("approval.approve", f"#{aid} -> {eid}")
    return ok(push_hint({"id": eid}, {"kind": "notify", "to": [r["created_by"]] if r["created_by"] != ctx.user["username"] else [],
                                      "title": "Your entry was approved", "body": f"By {ctx.user['username']}", "href": "#/journal", "tag": f"appr{aid}", "pref": "decisions"}))


@route("POST", "/api/approvals/<aid>/reject")
def approval_reject(ctx, aid):
    from . import approvals
    r = approvals.get(ctx.db, aid)
    if r["status"] != "pending":
        raise acc.ValidationError("This draft is no longer waiting for approval")
    if not approvals.can_approve(ctx.user, ctx.can, r):
        raise PermissionError()
    if not str(ctx.body.get("note") or "").strip():
        raise acc.ValidationError("Say why it is sent back, so it can be fixed")
    approvals.decide(ctx.db, aid, ctx.user, "rejected", ctx.body.get("note"))
    ctx.log("approval.reject", f"#{aid}")
    return ok(push_hint(None, {"kind": "notify", "to": [r["created_by"]] if r["created_by"] != ctx.user["username"] else [],
                               "title": "Your entry was sent back", "body": str(ctx.body.get("note") or "")[:160], "href": "#/approvals", "tag": f"appr{aid}", "pref": "decisions"}))


@route("PUT", "/api/approvals/<aid>")
def approval_update(ctx, aid):
    """The maker fixes their draft (pending, or sent back — it then waits again)."""
    from . import approvals
    r = approvals.get(ctx.db, aid)
    if r["created_by"] != ctx.user["username"] or r["status"] not in ("pending", "rejected"):
        raise PermissionError()
    body = ctx.body.get("payload") if isinstance(ctx.body.get("payload"), dict) else ctx.body
    approvals.update_payload(ctx.db, aid, body)
    ctx.db.x("UPDATE approvals SET status='pending', decided_by=NULL, decided_at=NULL WHERE id=?", (int(aid),))
    return ok()


@route("POST", "/api/approvals/<aid>/withdraw")
def approval_withdraw(ctx, aid):
    from . import approvals
    r = approvals.get(ctx.db, aid)
    if r["created_by"] != ctx.user["username"] or r["status"] not in ("pending", "rejected"):
        raise PermissionError()
    approvals.decide(ctx.db, aid, ctx.user, "withdrawn")
    return ok()


# ---------------------------------------------------------------- customers & deals (erp/crm.py)
# ================================================================= API keys
@route("GET", "/api/api-keys", "settings.api", "edit")
def api_keys_list(ctx):
    return ok({"rows": auth.api_keys(ctx.db), "people": [r["username"] for r in ctx.db.q("SELECT username FROM users WHERE active=1 ORDER BY username")],
               "server": getattr(ctx.app, "static", False) is not True})


@route("POST", "/api/api-keys", "settings.api", "edit")
def api_keys_add(ctx):
    u = ctx.db.one("SELECT id FROM users WHERE username=? AND active=1", (ctx.body.get("username") or ctx.user["username"],))
    if not u:
        raise acc.ValidationError("Choose a person")
    kid, key = auth.create_api_key(ctx.db, u["id"], ctx.body.get("name"), ctx.body.get("read_only", True) is not False, ctx.user["username"])
    ctx.log("api_key.create", f"{kid} {ctx.body.get('name')}")
    return ok({"id": kid, "key": key})


@route("DELETE", "/api/api-keys/<kid>", "settings.api", "edit")
def api_keys_revoke(ctx, kid):
    auth.revoke_api_key(ctx.db, kid)
    ctx.log("api_key.revoke", kid)
    return ok()


@route("GET", "/api/api-docs")
def api_docs(ctx):
    """Every API path the caller may use: method, path, section and what it does (for scripts and AI coding tools)."""
    from . import ai
    return ok({"routes": ai.catalog(ROUTES, ctx.user, ctx.q.get("q"), 120), "auth": "Authorization: Bearer <API key>",
               "note": "Search with ?q=words (best 120 matches). Read-only keys may only GET."})


# ================================================================= knowledge base (kb.py)
@route("GET", "/api/kb", "team.kb")
def kb_list(ctx):
    """The company knowledge base: its own how-tos, policies and answers (search with q). Check it when asked how the company does something."""
    from . import kb
    return ok(kb.listing(ctx.db, ctx.q.get("q"), ctx.q.get("category")))


@route("GET", "/api/kb/<kid>", "team.kb")
def kb_get(ctx, kid):
    """One knowledge base article with its full text."""
    from . import kb
    return ok(kb.get(ctx.db, kid))


# ============================================================= company subscriptions & recurring bills (billsubs.py)
@route("GET", "/api/bills", "purchasing.bills")
def bills_list(ctx):
    from . import billsubs
    return ok(billsubs.listing(ctx.db))


@route("GET", "/api/bills/<sid>", "purchasing.bills")
def bills_get(ctx, sid):
    from . import billsubs
    return ok(billsubs.get(ctx.db, sid))


@route("POST", "/api/bills", "purchasing.bills", "add")
def bills_add(ctx):
    from . import billsubs
    sid = billsubs.save(ctx.db, ctx.body, ctx.user["username"])
    ctx.log("bill.add", ctx.body.get("name"))
    return ok(push_hint({"id": sid}, *billsubs.reminder(ctx.db, sid)))


@route("PUT", "/api/bills/<sid>", "purchasing.bills", "edit")
def bills_edit(ctx, sid):
    from . import billsubs
    billsubs.save(ctx.db, ctx.body, ctx.user["username"], sid)
    ctx.log("bill.edit", sid)
    return ok(push_hint({"id": int(sid)}, *billsubs.reminder(ctx.db, sid)))


@route("DELETE", "/api/bills/<sid>", "purchasing.bills", "edit")
def bills_delete(ctx, sid):
    from . import billsubs
    billsubs.delete(ctx.db, sid)
    ctx.log("bill.delete", sid)
    return ok(push_hint(None, {"cancel": f"bill:{sid}"}))


@route("POST", "/api/bills/<sid>/pay", "purchasing.bills", "add")
def bills_pay(ctx, sid):
    """Record a payment: operating expense(s) by the subscription's allocation; the next due date moves on a cycle."""
    from . import approvals, billsubs
    ctx.require("purchasing.expenses", "add")
    if approvals.needs_approval(ctx.db, ctx.user):
        raise PermissionError("Your entries require approval. Ask a user with posting rights to record this payment.")
    r = billsubs.pay(ctx.db, sid, ctx.body, ctx.user["username"])
    ctx.log("bill.pay", sid)
    return ok(push_hint(r, *billsubs.reminder(ctx.db, sid)))


@route("POST", "/api/kb", "team.kb", "add")
def kb_add(ctx):
    """Write a knowledge base article: {title, body, category, pinned}."""
    from . import kb
    return ok({"id": kb.save(ctx.db, ctx.body, ctx.user)})


@route("PUT", "/api/kb/<kid>", "team.kb", "add")
def kb_edit(ctx, kid):
    """Change a knowledge base article (its writer, or anyone with full rights on the knowledge base)."""
    from . import kb
    return ok({"id": kb.save(ctx.db, ctx.body, ctx.user, kid, any_article=ctx.can("team.kb", "edit"))})


@route("DELETE", "/api/kb/<kid>", "team.kb", "add")
def kb_delete(ctx, kid):
    """Delete a knowledge base article (its writer, or anyone with full rights on the knowledge base)."""
    from . import kb
    kb.delete(ctx.db, kid, ctx.user, any_article=ctx.can("team.kb", "edit"))
    return ok()


# ================================================================= my day (workdays.py)
@route("GET", "/api/my-day", "team.myday")
def my_day(ctx):
    """The signed-in person's working day today (started? ended?) and a draft report of what they did."""
    from . import workdays
    return ok(workdays.mine(ctx.db, ctx.user, ctx.lang))


@route("POST", "/api/presence", "team.myday")
def presence(ctx):
    """The top-bar check-in button: records that the person is active and returns their check-in state."""
    from . import workdays
    workdays.seen(ctx.db, ctx.user["username"])
    cur = workdays.today_of(ctx.db, ctx.user)
    sessions = ctx.db.q("SELECT * FROM workdays WHERE username=? AND date=?", (ctx.user["username"], acc.today()))
    late = workdays.online_not_checked_in(ctx.db, ctx.user["username"])
    return ok({"open": bool(cur and not cur["end_at"]), "since": cur["start_at"] if cur and not cur["end_at"] else None,
               "closed_hours": round(sum(workdays._hours(r) for r in sessions if r["end_at"]), 4), "now": acc.now_iso(),
               "late": late[0]["minutes"] if late else 0, "employee": bool(workdays.employee_of(ctx.db, ctx.user["username"])),
               **{k: v for k, v in workdays.mine_policy(ctx.db, ctx.user["username"]).items()}})


@route("POST", "/api/my-day/start", "team.myday", "add")
def my_day_start(ctx):
    """Start my working day (with lat / lng when location is shared)."""
    from . import workdays
    return ok({"id": workdays.start(ctx.db, ctx.user, ctx.body)})


@route("POST", "/api/my-day/end", "team.myday", "add")
def my_day_end(ctx):
    """End my working day with a short report."""
    from . import workdays
    return ok({"id": workdays.finish(ctx.db, ctx.user, ctx.body)})


@route("DELETE", "/api/workdays/<wid>", "payroll.workdays", "edit")
def workdays_delete(ctx, wid):
    from . import workdays
    r = workdays.delete(ctx.db, wid)
    ctx.log("workday.delete", f"{r['username']} {r['date']}")
    return ok()


@route("GET", "/api/att-policy", "payroll.workdays")
def att_policy_get(ctx):
    from . import hrrequests
    return ok({**hrrequests.policy(ctx.db), "location": ctx.db.setting("workday_location") or "ask",
               "can_locations": auth.granted(ctx.user, "checkin_locations"), "can_policy": ctx.can("payroll.workdays", "edit")})


@route("PUT", "/api/att-policy", "payroll.workdays")
def att_policy_put(ctx):
    """Working hours and leave: edit rights on the check-in log. Sites, who may check in where and the location
    checks: the "Manage check-in locations" special right (super administrators always)."""
    from . import hrrequests
    loc = [k for k in hrrequests.LOCATION_KEYS if k in ctx.body]
    if loc and not auth.granted(ctx.user, "checkin_locations"):
        raise PermissionError("Only users with the right to manage check-in locations can change locations.")
    if [k for k in ctx.body if k not in hrrequests.LOCATION_KEYS] and not ctx.can("payroll.workdays", "edit"):
        raise PermissionError()
    if "location" in ctx.body:
        if ctx.body["location"] not in ("off", "ask", "required"):
            raise acc.ValidationError("Choose a location option.")
        ctx.db.set_setting("workday_location", ctx.body["location"])
    p = hrrequests.save_policy(ctx.db, ctx.body)
    ctx.log("attendance.policy", "")
    return ok(p)


# ================================================================= employee requests (hrrequests.py)
@route("GET", "/api/hr-requests", "team.myday")
def hr_requests_list(ctx):
    from . import hrrequests
    q = ctx.q
    everyone = q.get("all") and ctx.can("payroll.requests")
    rows = hrrequests.listing(ctx.db, None if everyone else ctx.user["username"], q.get("status"), q.get("kind"),
                              q.get("date_from"), q.get("date_to"))
    if everyone and q.get("username"):
        rows = [r for r in rows if r["username"] == q["username"]]
    return ok({"rows": rows, "kinds": {k: v[:2] for k, v in hrrequests.KINDS.items()}, "can_decide": ctx.can("payroll.requests", "edit")})


@route("POST", "/api/hr-requests", "team.myday", "add")
def hr_requests_add(ctx):
    from . import hrrequests
    rid = hrrequests.submit(ctx.db, ctx.user, ctx.body)
    ctx.log("request.add", rid)
    return ok({"id": rid})


@route("POST", "/api/hr-requests/<rid>/cancel", "team.myday", "add")
def hr_requests_cancel(ctx, rid):
    from . import hrrequests
    hrrequests.cancel(ctx.db, ctx.user, rid)
    ctx.log("request.cancel", rid)
    return ok()


@route("POST", "/api/hr-requests/<rid>/decide", "payroll.requests", "edit")
def hr_requests_decide(ctx, rid):
    from . import hrrequests
    r = hrrequests.decide(ctx.db, ctx.user, rid, bool(ctx.body.get("approve")), ctx.body.get("note") or "")
    ctx.log("request." + r["status"], rid)
    return ok(r)


@route("POST", "/api/hr-requests/<rid>/pay", "payroll.requests", "edit")
def hr_requests_pay(ctx, rid):
    from . import hrrequests
    eid = hrrequests.pay_advance(ctx.db, ctx.user, rid, ctx.body)
    ctx.log("request.pay", f"{rid} entry {eid}")
    return ok({"entry_id": eid})


@route("GET", "/api/workdays", "payroll.workdays")
def workdays_team(ctx):
    """The team's working days: who started when and where, hours and daily reports."""
    from . import workdays
    return ok({**workdays.team(ctx.db, ctx.q.get("date_from"), ctx.q.get("date_to"), ctx.q.get("username"), ctx.lang), "flags": workdays.FLAGS,
               "online": workdays.online_not_checked_in(ctx.db),
               "location": ctx.db.setting("workday_location") or "ask"})


# ================================================================= automations (automations.py)
@route("GET", "/api/automations/catalog", "settings.automations")
def automations_catalog(ctx):
    """What automations can do: every trigger and action with its settings, the rule format and an example — read before creating one."""
    from . import automations
    return ok(automations.catalog())


@route("GET", "/api/automations", "settings.automations")
def automations_list(ctx):
    """The company's automation rules (when this happens, do that) and what they did lately."""
    from . import automations
    return ok({"rows": automations.listing(ctx.db), "log": automations.log(ctx.db, limit=100), **automations.catalog(),
               "people": [r["username"] for r in ctx.db.q("SELECT username FROM users WHERE active=1 ORDER BY username")],
               "rooms": [r["name"] for r in ctx.db.q("SELECT name FROM chat_rooms WHERE kind<>'dm' AND archived_at IS NULL AND name<>'' ORDER BY name")]})


@route("POST", "/api/automations", "settings.automations", "add")
def automations_add(ctx):
    """Create an automation rule: {name, trigger, settings, action, params, repeat_days} (see /api/automations/catalog)."""
    from . import automations
    return ok({"id": automations.save(ctx.db, ctx.body, ctx.user)})


@route("PUT", "/api/automations/<rid>", "settings.automations", "edit")
def automations_edit(ctx, rid):
    """Change an automation rule (or switch it on / off with active)."""
    from . import automations
    return ok({"id": automations.save(ctx.db, ctx.body, ctx.user, rid)})


@route("DELETE", "/api/automations/<rid>", "settings.automations", "edit")
def automations_delete(ctx, rid):
    """Delete an automation rule."""
    from . import automations
    automations.delete(ctx.db, rid)
    return ok()


@route("POST", "/api/automations/preview", "settings.automations")
def automations_preview(ctx):
    """What a rule would act on right now, without acting: {trigger, settings}."""
    from . import automations
    return ok({"rows": automations.preview(ctx.db, ctx.body)})


@route("POST", "/api/automations/run", "settings.automations|crm.followups|dashboard|team.alerts")
def automations_run(ctx):
    """Run the signed-in person's own rules and alerts now (the app calls this every few minutes, with its local time)."""
    from . import automations
    only = ctx.body.get("id")
    if only:
        r = ctx.db.one("SELECT created_by FROM automations WHERE id=?", (int(only),))
        if not (ctx.can("settings.automations", "edit") or (r and r["created_by"] == ctx.user["username"])):
            raise PermissionError()
    pushes = []
    if ctx.body.get("all") and not only:      # everyone's alerts this person may also see (and their own rules)
        done = automations.run(ctx.db, local=ctx.body.get("local"), pushes=pushes, viewer=ctx.user)
    else:
        done = automations.run(ctx.db, only=only, owner=None if only else ctx.user["username"], local=ctx.body.get("local"), pushes=pushes)
    if not only and not any(x.get("acted") or x.get("error") for x in done):
        return ok({"_nochange": True, "done": done})      # (the shared workspace then records nothing)
    return ok(push_hint({"done": done}, *pushes))


@route("GET", "/api/automations/forecast", "settings.automations|crm.followups|dashboard|team.alerts")
def automations_forecast(ctx):
    """When each alert this person may see will next come true — the app hands it to the workspace's push service."""
    from . import automations
    return ok(automations.forecast(ctx.db, ctx.user, ctx.q.get("local"), min(int(ctx.q.get("days") or 7), 14)))


# ---------------------------------------------------------------- my alerts: what each person wants to be told about
def _alert_body(ctx):
    """An alert is a rule whose action is "notify". People who manage automations may alert others; everyone else
    alerts themselves, and only about what they are allowed to see."""
    from . import automations
    b = dict(ctx.body or {})
    b["action"] = "notify"
    params = b.get("params") if isinstance(b.get("params"), dict) else {}
    if not ctx.can("settings.automations", "edit"):
        params = {"to": ctx.user["username"]}
    b["params"] = params
    if not automations.allowed(ctx.user, b.get("trigger")):
        raise PermissionError("You do not have access to the data this alert monitors.")
    return b


def _own_alert(ctx, rid):
    r = ctx.db.one("SELECT created_by, action FROM automations WHERE id=?", (int(rid),))
    if not r or r["action"] != "notify" or (r["created_by"] != ctx.user["username"] and not ctx.can("settings.automations", "edit")):
        raise PermissionError()


@route("GET", "/api/my-alerts", "team.alerts")
def my_alerts(ctx):
    from . import automations
    mine = [r for r in automations.listing(ctx.db) if r["action"] == "notify" and r["created_by"] == ctx.user["username"]]
    cat = automations.catalog()
    cat["triggers"] = [t for t in cat["triggers"] if automations.allowed(ctx.user, t["key"])]
    return ok({"rows": mine, "catalog": cat, "feed": automations.feed(ctx.db, ctx.user["username"]),
               "can_others": ctx.can("settings.automations", "edit")})


@route("POST", "/api/my-alerts", "team.alerts", "add")
def my_alert_add(ctx):
    from . import automations
    return ok({"id": automations.save(ctx.db, _alert_body(ctx), ctx.user)})


@route("PUT", "/api/my-alerts/<rid>", "team.alerts", "add")
def my_alert_edit(ctx, rid):
    from . import automations
    _own_alert(ctx, rid)
    return ok({"id": automations.save(ctx.db, _alert_body(ctx), ctx.user, rid)})


@route("DELETE", "/api/my-alerts/<rid>", "team.alerts", "add")
def my_alert_delete(ctx, rid):
    from . import automations
    _own_alert(ctx, rid)
    automations.delete(ctx.db, rid)
    return ok()


@route("POST", "/api/my-alerts/preview", "team.alerts")
def my_alert_preview(ctx):
    from . import automations
    if not automations.allowed(ctx.user, ctx.body.get("trigger")):
        raise PermissionError()
    return ok({"rows": automations.preview(ctx.db, ctx.body)})


@route("POST", "/api/my-alerts/read", "team.alerts")
def my_alerts_read(ctx):
    from . import automations
    ids = ctx.body.get("ids")
    automations.feed_read(ctx.db, ctx.user["username"], [int(i) for i in ids] if isinstance(ids, list) else None)
    return ok()


# ================================================================= the public desk (desk.py): online booking, web forms, e-signing, guests
@route("POST", "/api/desk/import", "crm.desk|care.visits|crm.followups")
def desk_import(ctx):
    """Rows the app claimed from the cloud (desk_claim) go into the books here."""
    from . import desk
    return ok({"rows": desk.import_rows(ctx.db, ctx.body.get("rows") or [], ctx.user["username"])})


@route("POST", "/api/desk/rows/<rid>/retry", "crm.desk", "add")
def desk_retry(ctx, rid):
    from . import desk
    return ok(desk.process(ctx.db, rid, ctx.user["username"]))


@route("GET", "/api/desk", "crm.desk")
def desk_overview(ctx):
    from . import desk
    return ok({"bookings": desk.bookings(ctx.db, ctx.q.get("from")), "threads": desk.threads(ctx.db),
               "rows": desk.rows(ctx.db, state=ctx.q.get("state") or None, kind=ctx.q.get("kind") or None),
               "unread": desk.unread(ctx.db), "services": desk.booking_services(ctx.db), "busy": desk.busy_slots(ctx.db),
               "company": ctx.db.setting("company_name") or ""})


@route("GET", "/api/desk/unread", "crm.desk")
def desk_unread(ctx):
    from . import desk
    return ok(desk.unread(ctx.db))


@route("POST", "/api/desk/threads/<tid>/read", "crm.desk")
def desk_thread_read(ctx, tid):
    from . import desk
    desk.thread_read(ctx.db, tid)
    return ok()


@route("POST", "/api/desk/rows/<rid>/answer", "crm.desk", "add")
def desk_answer(ctx, rid):
    from . import desk
    desk.mark_confirmed(ctx.db, rid, ctx.body.get("how"), ctx.user["username"])
    return ok()


@route("GET", "/api/desk/offer/<oid>", "sales.offers")
def desk_offer(ctx, oid):
    from . import desk
    return ok(desk.offer_summary(ctx.db, oid))


# ================================================================= healthcare (care.py)
@route("GET", "/api/care/patients", "care.patients|care.visits|care.packages")
def care_patients(ctx):
    from . import care
    return ok({"rows": care.patients(ctx.db, ctx.q.get("q"), ctx.q.get("payer"), ctx.q.get("insurer"), ctx.q.get("active"))})


@route("GET", "/api/care/patients/<pid>", "care.patients")
def care_patient(ctx, pid):
    from . import care
    return ok(care.patient(ctx.db, pid))


@route("POST", "/api/care/patients", "care.patients", "add")
def care_patient_add(ctx):
    from . import care
    return ok({"id": care.save_patient(ctx.db, ctx.body)})


@route("PUT", "/api/care/patients/<pid>", "care.patients", "edit")
def care_patient_edit(ctx, pid):
    from . import care
    return ok({"id": care.save_patient(ctx.db, ctx.body, pid)})


@route("GET", "/api/care/services", "care.services|care.visits")
def care_services(ctx):
    from . import care
    return ok({"rows": care.services(ctx.db, ctx.q.get("active")), "categories": list(care.CATEGORIES)})


@route("POST", "/api/care/services", "care.services", "add")
def care_service_add(ctx):
    from . import care
    return ok({"id": care.save_service(ctx.db, ctx.body)})


@route("PUT", "/api/care/services/<sid>", "care.services", "edit")
def care_service_edit(ctx, sid):
    from . import care
    return ok({"id": care.save_service(ctx.db, ctx.body, sid)})


@route("GET", "/api/care/visits", "care.visits")
def care_visits(ctx):
    from . import care
    q = ctx.q
    return ok({"rows": care.visits(ctx.db, q.get("date_from"), q.get("date_to"), q.get("status"), q.get("place"), q.get("provider"),
                                   q.get("patient_id"), q.get("bu_id"), q.get("q"), q.get("billed"))})


@route("GET", "/api/care/visits/<vid>", "care.visits")
def care_visit(ctx, vid):
    from . import care
    return ok(care.visit(ctx.db, vid))


@route("POST", "/api/care/visits", "care.visits", "add")
def care_visit_add(ctx):
    from . import care
    return ok({"id": care.save_visit(ctx.db, ctx.body, ctx.user["username"])})


@route("PUT", "/api/care/visits/<vid>", "care.visits", "add")
def care_visit_edit(ctx, vid):
    from . import care
    return ok({"id": care.save_visit(ctx.db, ctx.body, ctx.user["username"], vid)})


@route("POST", "/api/care/visits/<vid>/status", "care.visits", "add")
def care_visit_status(ctx, vid):
    from . import care
    care.set_status(ctx.db, vid, ctx.body.get("status"), ctx.body)
    return ok()


@route("POST", "/api/care/visits/<vid>/bill", "care.visits", "add")
def care_visit_bill(ctx, vid):
    from . import care
    r = care.bill_visit(ctx.db, vid, ctx.body, ctx.user["username"])
    ctx.log("care.bill", vid)
    return ok(r)


@route("POST", "/api/care/visits/<vid>/void", "care.visits", "edit")
def care_visit_unbill(ctx, vid):
    from . import care
    care.unbill_visit(ctx.db, vid, ctx.user["username"], ctx.body.get("reason") or "")
    ctx.log("care.unbill", vid)
    return ok()


@route("GET", "/api/care/packages", "care.packages|care.visits")
def care_packages(ctx):
    from . import care
    return ok({"rows": care.packages(ctx.db, ctx.q.get("patient_id"), ctx.q.get("status"))})


@route("POST", "/api/care/packages", "care.packages", "add")
def care_package_add(ctx):
    from . import care
    return ok({"id": care.sell_package(ctx.db, ctx.body, ctx.user["username"])})


@route("GET", "/api/care/claims", "care.claims")
def care_claims(ctx):
    from . import care
    return ok({"rows": care.claims(ctx.db, ctx.q.get("insurer"), ctx.q.get("status")), "unclaimed": care.unclaimed(ctx.db)})


@route("POST", "/api/care/claims", "care.claims", "add")
def care_claim_add(ctx):
    from . import care
    return ok({"id": care.make_claim(ctx.db, ctx.body.get("insurer"), ctx.body.get("date_from"), ctx.body.get("date_to"), ctx.user["username"])})


@route("POST", "/api/care/claims/<cid>/settle", "care.claims", "edit")
def care_claim_settle(ctx, cid):
    from . import care
    care.settle_claim(ctx.db, cid, ctx.body, ctx.user["username"])
    ctx.log("care.claim.settle", cid)
    return ok()


@route("GET", "/api/care/report", "care.reports")
def care_report(ctx):
    from . import care
    return ok(care.report(ctx.db, ctx.q.get("date_from"), ctx.q.get("date_to"), ctx.q.get("bu_id")))


@route("GET", "/api/crm/board", "crm.deals")
def crm_board(ctx):
    from . import crm
    return ok(crm.board(ctx.db, ctx.q))


@route("POST", "/api/crm/deals", "crm.deals", "add")
def crm_deal_add(ctx):
    from . import crm
    return ok({"id": crm.save_deal(ctx.db, ctx.body, ctx.user["username"])})


@route("PUT", "/api/crm/deals/<did>", "crm.deals", "add")
def crm_deal_edit(ctx, did):
    from . import crm
    d = ctx.db.one("SELECT owner FROM crm_deals WHERE id=?", (int(did),))
    if d and d["owner"] != ctx.user["username"]:
        ctx.require("crm.deals", "edit")             # someone else's deal: full rights on deals
    if set(ctx.body) <= {"stage", "lost_reason"}:
        crm.move_deal(ctx.db, int(did), ctx.body.get("stage"), ctx.user["username"], ctx.body.get("lost_reason"))
    else:
        crm.save_deal(ctx.db, ctx.body, ctx.user["username"], int(did))
    return ok()


@route("DELETE", "/api/crm/deals/<did>", "crm.deals", "edit")
def crm_deal_del(ctx, did):
    from . import crm
    crm.delete_deal(ctx.db, int(did))
    return ok()


@route("GET", "/api/crm/contacts", "crm.contacts")
def crm_contacts(ctx):
    from . import crm
    return ok({"rows": crm.contacts(ctx.db, ctx.q)})


@route("GET", "/api/crm/timeline", "crm.contacts")
def crm_timeline(ctx):
    from . import crm
    return ok(crm.timeline(ctx.db, ctx.q.get("party"), ctx.can))


@route("POST", "/api/crm/activities", "crm.contacts", "add")
def crm_activity_add(ctx):
    from . import crm
    return ok({"id": crm.add_activity(ctx.db, ctx.body, ctx.user["username"])})


@route("DELETE", "/api/crm/activities/<aid>", "crm.contacts", "add")
def crm_activity_del(ctx, aid):
    from . import crm
    crm.delete_activity(ctx.db, aid, ctx.user["username"], bool(ctx.user.get("is_admin")))
    return ok()


@route("GET", "/api/crm/followups", "crm.followups")
def crm_followups(ctx):
    from . import crm
    q = dict(ctx.q)
    if q.get("who") not in (None, "", "me") and not ctx.can("crm.followups", "edit"):
        q["who"] = "me"                                # other people's follow-ups: full rights only
    return ok({"rows": crm.followups(ctx.db, ctx.user["username"], q),
               "people": [r["username"] for r in ctx.db.q("SELECT username FROM users WHERE active=1 ORDER BY username")]})


def _followup_push(ctx, fid, new=False):
    f = ctx.db.one("SELECT id, title, due, party, assigned_to, created_by, done FROM crm_followups WHERE id=?", (int(fid),))
    if not f:
        return [{"cancel": f"fu:{fid}"}]
    if f["done"]:
        return [{"cancel": f"fu:{fid}"}]
    who = f["assigned_to"] or f["created_by"]
    out = [{"kind": "notify", "key": f"fu:{fid}", "day": f["due"], "to": [who], "title": "Follow-up due today",
            "body": f["title"] + (f" — {f['party']}" if f["party"] else ""), "href": "#/followups", "tag": f"fu{fid}", "pref": "fu_due"}]
    if new and who != ctx.user["username"]:
        out.append({"kind": "notify", "to": [who], "title": f"New follow-up from {ctx.user['username']}",
                    "body": f["title"] + f" · due {f['due']}", "href": "#/followups", "tag": f"fu{fid}", "pref": "fu_new"})
    return out


@route("POST", "/api/crm/followups", "crm.followups", "add")
def crm_followup_add(ctx):
    from . import crm
    fid = crm.save_followup(ctx.db, ctx.body, ctx.user["username"])
    return ok(push_hint({"id": fid}, *_followup_push(ctx, fid, True)))


def _own_followup(ctx, fid):
    f = ctx.db.one("SELECT assigned_to, created_by FROM crm_followups WHERE id=?", (int(fid),))
    if not f:
        raise acc.ValidationError("Not found")
    if ctx.user["username"] not in (f["assigned_to"], f["created_by"]):
        ctx.require("crm.followups", "edit")


@route("PUT", "/api/crm/followups/<fid>", "crm.followups", "add")
def crm_followup_edit(ctx, fid):
    from . import crm
    _own_followup(ctx, fid)
    if "done" in ctx.body and len(ctx.body) <= 2:
        crm.done_followup(ctx.db, fid, ctx.user["username"], bool(ctx.body["done"]), ctx.body.get("outcome") or "")
    else:
        crm.save_followup(ctx.db, ctx.body, ctx.user["username"], int(fid))
    return ok(push_hint(None, *_followup_push(ctx, fid)))


@route("DELETE", "/api/crm/followups/<fid>", "crm.followups", "add")
def crm_followup_del(ctx, fid):
    _own_followup(ctx, fid)
    ctx.db.x("DELETE FROM crm_followups WHERE id=?", (int(fid),))
    return ok(push_hint(None, {"cancel": f"fu:{fid}"}))


def _offer_bu(ctx, o):
    """A business unit (project) for an offer — made from the offer's client, project and value."""
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
    ctx.db.x("UPDATE offers SET bu_id=? WHERE id=?", (bid, int(o["id"])))
    return bid


@route("POST", "/api/offers/<oid>/convert", "sales.offers", "edit")
def offer_convert(ctx, oid):
    """Offer → project (business unit, made if missing) → BOQ with the offer's prices, in one step; the screen then
    opens a new invoice on that BOQ."""
    from . import crm
    ctx.require("estimating.boqs", "add")
    o = documents.get_offer(ctx.db, int(oid))
    if not o.get("bu_id"):
        ctx.require("business_units.units", "add")
    with ctx.db.tx():
        r = crm.convert_offer(ctx.db, int(oid), ctx.user["username"], lambda off: _offer_bu(ctx, off))
    ctx.log("offer.convert", f"{oid} -> boq {r['boq_id']}")
    return ok(r)


@route("GET", "/api/negotiations")
def negotiations(ctx):
    if not ctx.can("purchasing.negotiations"):
        raise PermissionError()
    return ok(acc.negotiations(ctx.db, ctx.q.get("date_from"), ctx.q.get("date_to"), ctx.q.get("bu_id")))


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


KIND_MODULE = {"Revenue Recognition": "shop.subscriptions", "Supplier Discount": "purchasing.payments", "Direct Cost": "purchasing.costs", "Operating Expense": "purchasing.expenses",
               "Payment Voucher": "purchasing.payments", "Salary Advance": "payroll.requests", "Payroll Accrual": "payroll", "Receipt Voucher": "sales.receipts",
               "Sales Invoice": "sales.invoices", "Capital Contribution": "partners.transactions",
               "Owner Drawing": "partners.transactions", "Capital Withdrawal": "partners.transactions",
               "Partner Loan": "partners.transactions", "Partner Loan Repayment": "partners.transactions",
               "Partner Loan Interest": "partners.transactions", "Profit Distribution": "partners.transactions",
               "Share Transfer": "partners.ownership", "Partner Admission": "partners.ownership",
               "Partner Exit": "partners.ownership", "Asset Purchase": "assets.register", "Depreciation": "assets.depreciation",
               "Asset Disposal": "assets.register", "Goods Receipt": "stock.moves", "Material Issue": "stock.moves",
               "Stock Transfer": "stock.moves", "Stock Adjustment": "stock.moves",
               **{k: "accounting.loans" for k in loans.ALL_KINDS}, **{k: "purchasing.custody" for k in custody.ALL_KINDS},
               **{k: "rentals.fleet" for k in rentals.KINDS},
               "Bank Entry": "accounting.bankrec",
               "Subcontractor Certificate": "contracts.certificates", "Equipment Usage": "equipment.usage",
               "Equipment Maintenance": "equipment.maintenance", "E-Receipt": "tax.ereceipt",
               **{k: "shop.orders" for k in ("Installment Interest", "Advance Applied", "Cost of Sales", "Cheque Received",
                                             "Cheque Cleared", "Cheque Bounced", "Sale Refund")}}


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


@route("GET", "/api/supplier-discount", "purchasing.payables")
def supplier_discount_info(ctx):
    return ok(acc.supplier_discount_defaults(ctx.db, acc.text(ctx.q.get("party")), ctx.q.get("account") or None))


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


@route("GET", "/api/extracts-board", "sales.extracts")
def extracts_board(ctx):
    from . import extracts as xt
    q = ctx.q
    return ok(xt.board(ctx.db, q.get("bu_id"), q.get("status"), q.get("q")))


@route("GET", "/api/extracts/<xid>/detail", "sales.extracts")
def extract_detail(ctx, xid):
    from . import extracts as xt
    return ok(xt.detail(ctx.db, int(xid)))


@route("POST", "/api/rentals/<rid>/next-extract", "sales.extracts", "add")
def rental_next_extract(ctx, rid):
    from . import extracts as xt
    xid = xt.next_for_rental(ctx.db, int(rid), ctx.body, ctx.user["username"])
    ctx.log("extract.next", xid)
    return ok({"id": xid})


@route("POST", "/api/extracts/<xid>/reopen")
def extract_reopen(ctx, xid):
    """Adjust an issued invoice / extract: its invoice entry is voided (kept in the audit trail) and it becomes a draft
    again. Not once the tax authority has it: cancel the e-invoice first."""
    if not (ctx.can("sales.invoices", "edit") or ctx.can("sales.extracts", "edit")):
        raise PermissionError("Edit rights on sales invoices are required to reopen an invoice.")
    x = ctx.db.one("SELECT * FROM extracts WHERE id=?", (int(xid),))
    if not x:
        raise acc.ValidationError("Invoice not found")
    st = str(x.get("eta_status") or "").lower()
    if x.get("eta_uuid") and st and not any(w in st for w in ("cancel", "reject", "invalid")):
        raise acc.ValidationError((f"This invoice was submitted to the tax authority (e-invoice status: {x['eta_status']}). Cancel the e-invoice first, then reopen it."))
    if x["status"] == "posted" and x["entry_id"]:
        acc.void_entry(ctx.db, x["entry_id"], ctx.body.get("reason") or "Extract reopened to adjust", ctx.user["username"])
    ctx.log("extract.reopen", xid)
    return ok({"id": int(xid)})


@route("GET", "/api/extracts/<xid>/doc", "sales.extracts")
def extract_doc(ctx, xid):
    """The extract in the client's layout: Excel, or a page that prints to PDF."""
    from . import extracts as xt
    lang = ctx.q.get("lang") or "ar"
    if ctx.q.get("format") in ("pdf", "html"):
        return Reply(xt.html(ctx.db, int(xid), lang), ctype="text/html; charset=utf-8")
    data, name = xt.xlsx(ctx.db, int(xid), lang)
    return download(data, name, XLSX)


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
        raise acc.ValidationError("Posted invoices cannot be deleted. Reverse the invoice entry first.")
    bu = ctx.db.one("SELECT bu_id FROM extracts WHERE id=?", (int(xid),))
    ctx.db.x("DELETE FROM extracts WHERE id=?", (int(xid),))
    if bu:
        from . import wip
        with ctx.db.tx():
            wip.sync(ctx.db, bu["bu_id"])
    return ok()


# ==================================================================== stock
@route("GET", "/api/stock")
def stock(ctx):
    if not (ctx.can("stock.balances") or ctx.can("stock.warehouses") or ctx.can("stock.moves")):
        raise PermissionError()
    return ok({"levels": acc.stock_report(ctx.db, ctx.q.get("warehouse_id"), ctx.q.get("q")),
               "warehouses": ctx.db.q("SELECT * FROM warehouses ORDER BY id"),
               "items": ctx.db.q("SELECT * FROM stock_items ORDER BY name"), "catalog": item_catalog(ctx.db, ctx.q.get("q"))})


def item_catalog(db, q=None):
    """Every item — with stock or not — with its standard cost, selling price, what is on hand and the last price paid."""
    rows = db.q("""SELECT i.*, COALESCE((SELECT SUM(qty) FROM stock_levels WHERE item_id=i.id),0) AS on_hand,
                          COALESCE((SELECT SUM(qty*avg_cost) FROM stock_levels WHERE item_id=i.id),0) AS value_minor,
                          (SELECT rate FROM stock_moves WHERE item_id=i.id AND qty>0 AND kind='Receipt'
                           ORDER BY date DESC, id DESC LIMIT 1) AS last_rate,
                          (SELECT date FROM stock_moves WHERE item_id=i.id AND qty>0 AND kind='Receipt' ORDER BY date DESC, id DESC LIMIT 1) AS last_date
                   FROM stock_items i""" + (" WHERE i.name LIKE ? OR i.sku LIKE ? OR i.category LIKE ?" if q else "") + " ORDER BY i.category, i.name",
                ([f"%{q}%"] * 3) if q else [])
    for r in rows:
        r["std_cost"], r["price"] = to_major(r.get("std_cost") or 0), to_major(r.get("price") or 0)
        r["value"], r["last_rate"] = to_major(r.pop("value_minor") or 0), to_major(r["last_rate"]) if r["last_rate"] is not None else None
    return rows


def _item_vals(b):
    return (acc.text(b.get("name"), "name", required=True), acc.text(b.get("unit")) or "pcs", acc.text(b.get("sku")),
            acc.num(b.get("reorder_level"), "reorder level"), acc.text(b.get("category")),
            to_minor(acc.num(b.get("std_cost"), "standard cost")), to_minor(acc.num(b.get("price"), "selling price")),
            acc.text(b.get("notes"), maxlen=1000))


@route("GET", "/api/items/names")
def item_names(ctx):
    """Item names for pickers (journal item lines): name, unit and the price to start from."""
    if not (ctx.can("accounting.journal", "add") or ctx.can("stock.moves") or ctx.can("stock.balances")):
        raise PermissionError()
    rows = ctx.db.q("""SELECT i.id, i.name, i.unit, i.std_cost,
                              (SELECT rate FROM stock_moves WHERE item_id=i.id AND qty>0 ORDER BY date DESC, id DESC LIMIT 1) AS last_rate
                       FROM stock_items i ORDER BY i.name""")
    return ok({"rows": [{"id": r["id"], "name": r["name"], "unit": r["unit"] or "",
                         "price": to_major(r["std_cost"] or r["last_rate"] or 0)} for r in rows]})


@route("POST", "/api/items", "stock.balances", "add")
def item_add(ctx):
    """An item can exist with nothing in stock — just its prices, for quotations and forecasting."""
    from . import dedupe
    v = _item_vals(ctx.body)
    same = dedupe.existing(ctx.db, v[0])
    if same:
        raise acc.ValidationError(f"{v[0]} already exists" + (f" as “{same['name']}”" if same["name"] != v[0] else ""))
    dedupe.check_sku(ctx.db, v[2])
    iid = ctx.db.x("INSERT INTO stock_items(name,unit,sku,reorder_level,category,std_cost,price,notes) VALUES (?,?,?,?,?,?,?,?)", v).lastrowid
    ctx.log("item.add", v[0])
    return ok({"id": iid})


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
        raise acc.ValidationError("The warehouse has stock history. Deactivate it instead.")
    ctx.db.x("DELETE FROM warehouses WHERE id=?", (int(wid),))
    return ok()


@route("DELETE", "/api/items/<iid>", "stock.balances", "edit")
def item_del(ctx, iid):
    it = ctx.db.one("SELECT name FROM stock_items WHERE id=?", (int(iid),))
    if not it:
        raise acc.ValidationError("Not found")
    if ctx.db.one("SELECT 1 FROM stock_moves WHERE item_id=? LIMIT 1", (int(iid),)):
        raise acc.ValidationError((f"{it['name']} has stock movements. Only items without movements can be deleted; void erroneous movements first."))
    ctx.db.x("DELETE FROM stock_items WHERE id=?", (int(iid),))
    ctx.log("item.delete", it["name"])
    return ok()


@route("PUT", "/api/items/<iid>", "stock.balances", "edit")
def item_edit(ctx, iid):
    from . import dedupe
    b = ctx.body
    dedupe.check_sku(ctx.db, b.get("sku"), int(iid))
    ctx.db.x("UPDATE stock_items SET name=?, unit=?, sku=?, reorder_level=?, category=?, std_cost=?, price=?, notes=? WHERE id=?",
             (*_item_vals(b), int(iid)))
    return ok()


# duplicate items (Stock → Check duplicates)
@route("GET", "/api/items/duplicates", "stock.balances")
def item_dupes(ctx):
    from . import dedupe
    return ok({"groups": dedupe.find(ctx.db)})


@route("GET", "/api/items/similar", "stock.balances")
def item_similar(ctx):
    from . import dedupe
    name = acc.text(ctx.q.get("name"))
    return ok({"rows": dedupe.similar(ctx.db, name) if name else []})


@route("POST", "/api/items/merge", "stock.balances", "edit")
def item_merge(ctx):
    from . import dedupe
    if not auth.action_ok(ctx.user, "stock.balances", "delete"):
        raise PermissionError(ACTION_DENIED["delete"])
    r = dedupe.merge(ctx.db, ctx.body.get("keep"), ctx.body.get("merge"), _u(ctx))
    ctx.log("item.merge", f"{', '.join(r['merged'])} → {r['kept']}")
    return ok(r)


@route("POST", "/api/items/duplicates/ignore", "stock.balances", "edit")
def item_dupes_ignore(ctx):
    from . import dedupe
    return ok(dedupe.ignore(ctx.db, ctx.body.get("ids")))


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
        if "profit_share" in b:  # blank = follow ownership
            ps = None if b.get("profit_share") in (None, "") else min(acc.num(b.get("profit_share"), "profit share"), 100) / 100
        else:
            ps = old["profit_share"]
        salary = to_minor(acc.num(b.get("salary"), "salary")) if b.get("salary") not in (None, "") else \
            (0 if "salary" in b else old["salary"])
        ctx.db.conn.execute("UPDATE partners SET profit_share=?, salary=?, drawings_interest=? WHERE id=?",
                            (ps, salary, rate("drawings_interest") or 0, int(pid)))
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
        raise acc.ValidationError("The partner has transactions and cannot be removed. Clear Active partner instead.")
    ctx.db.x("DELETE FROM partners WHERE id=?", (int(pid),))
    return ok()


# ================================================================== loans
# ============================================================ bank reconciliation
@route("GET", "/api/bank", "accounting.bankrec")
def bank_overview(ctx):
    return ok({**bankrec.overview(ctx.db), "rules": ctx.db.q("SELECT * FROM bank_rules ORDER BY pattern")})


@route("POST", "/api/bank-preview/parse", "accounting.bankrec", "add")
def bank_parse(ctx):
    b = ctx.body
    return ok(bankrec.parse(b.get("data") or b.get("text") or "", b.get("filename") or "", b.get("date_order") or None, b.get("mapping")))


@route("POST", "/api/bank-statements", "accounting.bankrec", "add")
def bank_stmt_add(ctx):
    sid = bankrec.save(ctx.db, ctx.body)
    ctx.log("bank.statement", sid)
    return ok({"id": sid})


@route("GET", "/api/bank-statements/<sid>", "accounting.bankrec")
def bank_stmt_one(ctx, sid):
    return ok(bankrec.detail(ctx.db, sid))


@route("PUT", "/api/bank-statements/<sid>", "accounting.bankrec", "edit")
def bank_stmt_edit(ctx, sid):
    return ok({"id": bankrec.save(ctx.db, ctx.body, sid)})


@route("DELETE", "/api/bank-statements/<sid>", "accounting.bankrec", "edit")
def bank_stmt_del(ctx, sid):
    bankrec.delete(ctx.db, sid)
    ctx.log("bank.statement.delete", sid)
    return ok()


@route("POST", "/api/bank-statements/<sid>/<action>", "accounting.bankrec", "add")
def bank_stmt_action(ctx, sid, action):
    b, db, u = ctx.body, ctx.db, ctx.user["username"]
    if action == "automatch":
        r = {"matched": bankrec.auto_match(db, sid)}
    elif action == "match":
        r = {"id": bankrec.match(db, sid, b.get("line_id"), b.get("entries"))}
    elif action == "unmatch":
        bankrec.unmatch(db, sid, b.get("line_id"))
        r = {}
    elif action == "ignore":
        bankrec.ignore(db, sid, b.get("line_id"), b.get("on", True) not in (False, 0, "0"))
        r = {}
    elif action == "create":
        r = {"id": bankrec.create_entry(db, sid, b.get("line_id"), b, u)}
    elif action == "rules":
        r = {"created": bankrec.apply_rules(db, sid, u)}
    elif action == "tick":
        bankrec.tick(db, sid, b.get("entries"), b.get("on", True) not in (False, 0, "0"))
        r = {}
    elif action == "lines":
        bankrec.save(db, {"lines": b.get("lines")}, sid)
        r = {}
    elif action == "delete-line":
        ctx.require("accounting.bankrec", "edit")
        bankrec.delete_line(db, sid, b.get("line_id"))
        r = {}
    elif action == "reconcile":
        bankrec.reconcile(db, sid, b, u)
        r = {}
    elif action == "reopen":
        ctx.require("accounting.bankrec", "edit")
        bankrec.reopen(db, sid)
        r = {}
    else:
        return ok({"error": "Unknown action"}, 404)
    ctx.log(f"bank.{action}", sid)
    return ok(r)


@route("POST", "/api/bank-rules", "accounting.bankrec", "add")
def bank_rule_add(ctx):
    return ok({"id": bankrec.save_rule(ctx.db, ctx.body)})


@route("PUT", "/api/bank-rules/<rid>", "accounting.bankrec", "edit")
def bank_rule_edit(ctx, rid):
    return ok({"id": bankrec.save_rule(ctx.db, ctx.body, rid)})


@route("DELETE", "/api/bank-rules/<rid>", "accounting.bankrec", "edit")
def bank_rule_del(ctx, rid):
    ctx.db.x("DELETE FROM bank_rules WHERE id=?", (int(rid),))
    return ok()


# ============================================================ recurring entries
# ============================================================ service subscriptions
def _svc():
    from . import services
    return services


@route("GET", "/api/service-plans", "shop.subscriptions.plans")
def svc_plans(ctx):
    return ok({"rows": _svc().plans(ctx.db)})


@route("POST", "/api/service-plans", "shop.subscriptions.plans", "add")
def svc_plan_add(ctx):
    return ok({"id": _svc().save_plan(ctx.db, ctx.body)})


@route("PUT", "/api/service-plans/<pid>", "shop.subscriptions.plans", "edit")
def svc_plan_edit(ctx, pid):
    _svc().save_plan(ctx.db, ctx.body, pid)
    return ok({})


@route("GET", "/api/subscriptions", "shop.subscriptions")
def svc_list(ctx):
    return ok(_svc().listing(ctx.db, ctx.q.get("q"), ctx.q.get("status"), ctx.q.get("kind"), ctx.q.get("customer")))


@route("GET", "/api/subscriptions/<sid>", "shop.subscriptions")
def svc_get(ctx, sid):
    return ok(_svc().detail(ctx.db, sid))


@route("POST", "/api/subscriptions", "shop.subscriptions", "add")
def svc_add(ctx):
    sid = _svc().save(ctx.db, ctx.body, ctx.user["username"])
    ctx.log("subscription.add", sid)
    return ok({"id": sid, "code": _svc().get(ctx.db, sid)["code"]})


@route("PUT", "/api/subscriptions/<sid>", "shop.subscriptions", "edit")
def svc_edit(ctx, sid):
    _svc().save(ctx.db, ctx.body, ctx.user["username"], sid)
    return ok({})


@route("POST", "/api/subscriptions/<sid>/status", "shop.subscriptions", "edit")
def svc_status(ctx, sid):
    _svc().set_status(ctx.db, sid, ctx.body.get("status"), ctx.body, ctx.user["username"])
    ctx.log("subscription." + str(ctx.body.get("status")), sid)
    return ok({})


@route("POST", "/api/subscriptions/<sid>/visits", "shop.subscriptions", "add")
def svc_visit_add(ctx, sid):
    return ok({"id": _svc().save_visit(ctx.db, sid, ctx.body)})


@route("PUT", "/api/subscriptions/<sid>/visits/<vid>", "shop.subscriptions", "edit")
def svc_visit_edit(ctx, sid, vid):
    _svc().save_visit(ctx.db, sid, ctx.body, vid)
    return ok({})


@route("DELETE", "/api/subscriptions/<sid>/visits/<vid>", "shop.subscriptions", "edit")
def svc_visit_del(ctx, sid, vid):
    _svc().delete_visit(ctx.db, sid, vid)
    return ok({})


@route("POST", "/api/subscriptions/<sid>/bill-charges", "shop.subscriptions", "add")
def svc_bill_charges(ctx, sid):
    return ok({"extract_id": _svc().bill_charges(ctx.db, sid, ctx.body.get("date"), ctx.user["username"])})


@route("POST", "/api/subscriptions-run", "shop.subscriptions", "add")
def svc_run(ctx):
    r = _svc().run(ctx.db, ctx.body.get("through"), ctx.user["username"])
    ctx.log("subscriptions.run", f"{r['invoices']} invoices")
    return ok(r)


# ============================================================ purchase orders
@route("GET", "/api/purchase-orders", "purchasing.orders")
def po_list(ctx):
    from . import purchasing
    return ok(purchasing.listing(ctx.db, ctx.q.get("q"), ctx.q.get("status"), ctx.q.get("supplier"), ctx.q.get("bu_id")))


@route("GET", "/api/purchase-orders/<pid>", "purchasing.orders")
def po_get(ctx, pid):
    from . import purchasing
    return ok(purchasing.get(ctx.db, pid))


@route("POST", "/api/purchase-orders", "purchasing.orders", "add")
def po_add(ctx):
    from . import purchasing
    pid = purchasing.save(ctx.db, ctx.body, ctx.user["username"])
    ctx.log("po.add", pid)
    return ok({"id": pid, "code": purchasing.get(ctx.db, pid)["code"]})


@route("PUT", "/api/purchase-orders/<pid>", "purchasing.orders", "add")
def po_edit(ctx, pid):
    from . import purchasing
    purchasing.save(ctx.db, ctx.body, ctx.user["username"], pid)
    return ok({})


@route("POST", "/api/purchase-orders/<pid>/approve", "purchasing.orders", "edit")
def po_approve(ctx, pid):
    from . import purchasing
    purchasing.approve(ctx.db, pid, ctx.user["username"])
    ctx.log("po.approve", pid)
    return ok({})


@route("POST", "/api/purchase-orders/<pid>/status", "purchasing.orders", "edit")
def po_status(ctx, pid):
    from . import purchasing
    purchasing.set_status(ctx.db, pid, ctx.body.get("status"), ctx.body.get("reason") or "")
    ctx.log("po." + str(ctx.body.get("status")), pid)
    return ok({})


@route("POST", "/api/purchase-orders/<pid>/receive", "purchasing.orders", "add")
def po_receive(ctx, pid):
    from . import purchasing
    ids = purchasing.receive(ctx.db, pid, ctx.body, ctx.user["username"])
    ctx.log("po.receive", pid)
    return ok({"entries": ids})


@route("DELETE", "/api/purchase-orders/<pid>", "purchasing.orders", "edit")
def po_delete(ctx, pid):
    from . import purchasing
    po = purchasing.get(ctx.db, pid)
    if po["receipts"]:
        raise acc.ValidationError("Vouchers have been posted against this order. Cancel or close it instead.")
    ctx.db.x("DELETE FROM purchase_orders WHERE id=?", (po["id"],))
    return ok({})


def _att_access(ctx, owner, oid, edit=False):
    """Who may see / change the files of a record: the people who may see / change that record."""
    from . import attachments
    if owner not in attachments.OWNERS:
        raise acc.ValidationError("Unknown record type")
    if owner == "entry":
        e = ctx.db.one("SELECT kind, bu_id FROM journal_entries WHERE id=?", (int(oid) if str(oid).isdigit() else -1,))
        if not e:
            raise acc.ValidationError("Record not found")
        mod = KIND_MODULE.get(e["kind"], "accounting.journal")
        ok_ = (ctx.can("accounting.journal", "edit") or ctx.can(mod, "add")) if edit else \
            (ctx.can("accounting.journal") or ctx.can(mod) or (e["bu_id"] and ctx.can("business_units.units")))
    elif owner == "approval":           # the maker adds files while it waits; the maker and its approvers see them
        from . import approvals
        r = ctx.db.one("SELECT * FROM approvals WHERE id=?", (int(oid) if str(oid).isdigit() else -1,))
        if not r:
            raise acc.ValidationError("Record not found")
        mine = r["created_by"] == ctx.user["username"]
        ok_ = (mine and r["status"] in ("pending", "rejected")) if edit else \
            (mine or approvals.can_approve(ctx.user, ctx.can, r) or approvals.may_see(ctx.user, ctx.can, r))
    else:
        perms = attachments.OWNERS[owner][2]
        ok_ = any(ctx.can(p, "add") if edit else ctx.can(p) for p in perms)
    if not ok_:
        raise PermissionError()


@route("GET", "/api/attachments")
def attachments_list(ctx):
    from . import attachments
    owner, oid = ctx.q.get("owner"), ctx.q.get("id")
    _att_access(ctx, owner, oid)
    return ok({"rows": attachments.listing(ctx.db, owner, oid), "max_mb": attachments.MAX_FILE // 1048576})


@route("POST", "/api/attachments")
def attachments_add(ctx):
    from . import attachments
    b = ctx.body
    _att_access(ctx, b.get("owner"), b.get("id"), edit=True)
    return ok({"id": attachments.add(ctx.db, b.get("owner"), b.get("id"), b, ctx.user["username"])})


@route("GET", "/api/attachments/<aid>")
def attachments_get(ctx, aid):
    from . import attachments
    a = attachments.get(ctx.db, aid)
    _att_access(ctx, a["owner"], a["owner_id"])
    if a["url"] and not a["data"]:
        return ok({"url": a["url"]})
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", a["title"] or "file")
    ext = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(a["mime"], "")
    if ext and not name.lower().endswith(ext):
        name += ext
    data = base64.b64decode(a["data"])
    if ctx.q.get("format") == "json":
        return ok({"data": f"data:{a['mime'] or 'application/octet-stream'};base64,{a['data']}", "mime": a["mime"], "title": a["title"]})
    return download(data, name, a["mime"] or "application/octet-stream")


@route("PUT", "/api/attachments/<aid>")
def attachments_rename(ctx, aid):
    from . import attachments
    a = attachments.get(ctx.db, aid)
    _att_access(ctx, a["owner"], a["owner_id"], edit=True)
    attachments.rename(ctx.db, aid, ctx.body.get("title"))
    return ok({})


@route("DELETE", "/api/attachments/<aid>")
def attachments_delete(ctx, aid):
    from . import attachments
    a = attachments.get(ctx.db, aid)
    _att_access(ctx, a["owner"], a["owner_id"], edit=True)
    attachments.delete(ctx.db, aid)
    ctx.db.log(ctx.user["username"], "attachment", f"removed {a['title']} from {a['owner']} {a['owner_id']}")
    return ok({})


@route("GET", "/api/budgets", "accounting.budgets")
def budgets_grid(ctx):
    from . import budgets
    return ok(budgets.grid(ctx.db, ctx.q.get("year"), ctx.q.get("bu_id", "0") or "0"))


@route("PUT", "/api/budgets", "accounting.budgets", "edit")
def budgets_save(ctx):
    from . import budgets
    b = ctx.body
    return ok({"saved": budgets.save(ctx.db, b.get("year"), b.get("bu_id") or 0, b.get("rows") or [])})


@route("POST", "/api/budgets/seed", "accounting.budgets", "edit")
def budgets_seed(ctx):
    from . import budgets
    b = ctx.body
    return ok({"saved": budgets.seed(ctx.db, b.get("year"), b.get("bu_id") or 0, b.get("from_year"), b.get("growth_pct"),
                                     bool(b.get("replace")))})


@route("GET", "/api/recurring", "accounting.recurring")
def recurring_list(ctx):
    return ok({"rows": recurring.listing(ctx.db),
               "due": recurring.due_list(ctx.db, ctx.q.get("through") or acc.today(), lambda m: ctx.can(m, "add"))})


@route("GET", "/api/recurring/<rid>", "accounting.recurring")
def recurring_one(ctx, rid):
    t = recurring.get(ctx.db, rid)
    return ok({**t, "payload": recurring._j(t["payload"], {}), "history": recurring.history(ctx.db, rid),
               "next_due": recurring.next_due(ctx.db, t)})


@route("POST", "/api/recurring", "accounting.recurring", "add")
def recurring_add(ctx):
    ctx.require(recurring.module_of(ctx.body.get("doc_type") or ""), "add")
    rid = recurring.save(ctx.db, ctx.body)
    ctx.log("recurring.add", rid)
    return ok({"id": rid})


@route("PUT", "/api/recurring/<rid>", "accounting.recurring", "edit")
def recurring_edit(ctx, rid):
    return ok({"id": recurring.save(ctx.db, ctx.body, rid)})


@route("DELETE", "/api/recurring/<rid>", "accounting.recurring", "edit")
def recurring_del(ctx, rid):
    recurring.delete(ctx.db, rid)
    return ok()


@route("POST", "/api/recurring-run", "accounting.recurring", "add")
def recurring_run(ctx):
    r = recurring.run(ctx.db, ctx.body.get("through") or acc.today(), ctx.user["username"], lambda m: ctx.can(m, "add"))
    if r["posted"] or r["errors"]:
        ctx.log("recurring.run", f"{r['posted']} posted, {len(r['errors'])} errors")
    return ok(r)


@route("POST", "/api/recurring/<rid>/<action>", "accounting.recurring", "add")
def recurring_action(ctx, rid, action):
    t = recurring.get(ctx.db, rid)
    ctx.require(recurring.module_of(t["doc_type"]), "add")
    if action == "post":
        return ok({"entries": recurring.post(ctx.db, rid, ctx.body.get("due"), ctx.body.get("overrides"), ctx.user["username"])})
    if action == "skip":
        recurring.skip(ctx.db, rid, ctx.body.get("due"))
        return ok()
    return ok({"error": "Unknown action"}, 404)


# ============================================================ product & unit sales
def _shop(ctx, *screens, level="view"):
    if not any(ctx.can("shop." + x, level) for x in screens):
        raise PermissionError()


@route("GET", "/api/products")
def products_list(ctx):
    _shop(ctx, "products", "orders")
    q = ctx.q
    return ok(sales.list_products(ctx.db, q.get("q"), q.get("kind"), q.get("category"), q.get("status"), q.get("bu_id")))


@route("GET", "/api/products/<pid>")
def product_one(ctx, pid):
    _shop(ctx, "products", "orders")
    return ok(sales.product_row(ctx.db, sales.get_product(ctx.db, pid), media=True))


@route("POST", "/api/products", "shop.products", "add")
def product_add(ctx):
    pid = sales.save_product(ctx.db, ctx.body)
    ctx.log("product.add", pid)
    return ok({"id": pid})


@route("PUT", "/api/products/<pid>", "shop.products", "edit")
def product_edit(ctx, pid):
    return ok({"id": sales.save_product(ctx.db, ctx.body, pid)})


@route("DELETE", "/api/products/<pid>", "shop.products", "edit")
def product_del(ctx, pid):
    sales.delete_product(ctx.db, pid)
    ctx.log("product.delete", pid)
    return ok()


@route("POST", "/api/products/<pid>/media", "shop.products", "add")
def product_media_add(ctx, pid):
    return ok({"id": sales.add_media(ctx.db, pid, ctx.body)})


@route("GET", "/api/product-media/<mid>")
def product_media_get(ctx, mid):
    _shop(ctx, "products", "orders")
    return ok(sales.media_file(ctx.db, mid))


@route("PUT", "/api/product-media/<mid>", "shop.products", "edit")
def product_media_edit(ctx, mid):
    sales.update_media(ctx.db, mid, ctx.body)
    return ok()


@route("DELETE", "/api/product-media/<mid>", "shop.products", "edit")
def product_media_del(ctx, mid):
    sales.delete_media(ctx.db, mid)
    return ok()


@route("GET", "/api/products/<pid>/doc")
def product_doc(ctx, pid):
    _shop(ctx, "products", "orders")
    return Reply(sales_docs.product_html(ctx.db, pid, ctx.q.get("lang")).encode("utf-8"), ctype="text/html; charset=utf-8")


@route("GET", "/api/shop-settings")
def shop_settings(ctx):
    _shop(ctx, "products", "orders", "installments")
    return ok(sales.settings(ctx.db))


@route("PUT", "/api/shop-settings")
def shop_settings_save(ctx):
    if not (ctx.can("shop.orders", "edit") or ctx.can("settings", "edit")):
        raise PermissionError()
    return ok(sales.save_settings(ctx.db, ctx.body))


@route("POST", "/api/sales-preview")
def sales_preview(ctx):
    _shop(ctx, "products", "orders")
    return ok(sales.preview(ctx.db, ctx.body))


@route("GET", "/api/sales")
def sales_list(ctx):
    _shop(ctx, "orders", "installments")
    q = ctx.q
    return ok(sales.register(ctx.db, q.get("q"), q.get("status"), q.get("customer"), q.get("date_from"), q.get("date_to"), q.get("as_of")))


@route("GET", "/api/sales/<sid>")
def sale_one(ctx, sid):
    _shop(ctx, "orders", "installments")
    return ok(sales.detail(ctx.db, sid, ctx.q.get("as_of")))


@route("POST", "/api/sales", "shop.orders", "add")
def sale_add(ctx):
    sid = sales.save(ctx.db, ctx.body)
    ctx.log("sale.add", sid)
    return ok({"id": sid})


@route("PUT", "/api/sales/<sid>", "shop.orders", "edit")
def sale_edit(ctx, sid):
    return ok({"id": sales.save(ctx.db, ctx.body, sid)})


@route("DELETE", "/api/sales/<sid>", "shop.orders", "edit")
def sale_del(ctx, sid):
    sales.delete(ctx.db, sid)
    ctx.log("sale.delete", sid)
    return ok()


@route("POST", "/api/sales/<sid>/<action>")
def sale_action(ctx, sid, action):
    u = ctx.user["username"]
    if action == "collect":
        _shop(ctx, "orders", "installments", level="add")
        r = sales.collect(ctx.db, sid, ctx.body, u)
    elif action in ("confirm", "deliver"):
        _shop(ctx, "orders", level="add")
        r = (sales.confirm if action == "confirm" else sales.deliver)(ctx.db, sid, ctx.body, u)
    elif action in ("refund", "cancel"):
        _shop(ctx, "orders", level="edit")
        r = (sales.refund if action == "refund" else sales.cancel)(ctx.db, sid, ctx.body, u)
    else:
        return ok({"error": "Unknown action"}, 404)
    ctx.log(f"sale.{action}", sid)
    return ok({"result": r})


@route("POST", "/api/sale-cheques/<cid>/<action>")
def sale_cheque(ctx, cid, action):
    _shop(ctx, "orders", "installments", level="add")
    eid = sales.cheque(ctx.db, cid, action, ctx.body, ctx.user["username"])
    ctx.log(f"sale.cheque.{action}", cid)
    return ok({"id": eid})


@route("GET", "/api/sales/<sid>/doc")
def sale_doc(ctx, sid):
    _shop(ctx, "orders", "installments")
    return Reply(sales_docs.sale_html(ctx.db, sid, ctx.q.get("kind") or "contract", ctx.q.get("lang")).encode("utf-8"),
                 ctype="text/html; charset=utf-8")


@route("GET", "/api/installments")
def installments_list(ctx):
    _shop(ctx, "installments", "orders")
    q = ctx.q
    r = sales.installments(ctx.db, q.get("as_of"), q.get("status") or "open", q.get("date_from"), q.get("date_to"), q.get("customer"))
    r["cheques_due"] = [{**c, "amount": c["amount"] / 100} for c in sales.cheques_due(ctx.db, q.get("cheques_upto") or acc.today())]
    return ok(r)


@route("GET", "/api/loans", "accounting.loans")
def loans_list(ctx):
    q = ctx.q
    return ok(loans.register(ctx.db, q.get("q"), q.get("direction"), q.get("state")))


@route("GET", "/api/loans/<lid>", "accounting.loans")
def loan_one(ctx, lid):
    return ok(loans.detail(ctx.db, lid))


@route("GET", "/api/loans/<lid>/suggest", "accounting.loans")
def loan_suggest(ctx, lid):
    return ok(loans.suggest(ctx.db, loans.get(ctx.db, lid), ctx.q.get("date")))


@route("POST", "/api/loans", "accounting.loans", "add")
def loan_add(ctx):
    lid = loans.save(ctx.db, ctx.body, user=ctx.user["username"])
    ctx.log("loan.add", lid)
    return ok({"id": lid})


@route("PUT", "/api/loans/<lid>", "accounting.loans", "edit")
def loan_edit(ctx, lid):
    loans.save(ctx.db, {**ctx.body, "booking": "keep"}, lid, ctx.user["username"])
    ctx.log("loan.edit", lid)
    return ok()


@route("DELETE", "/api/loans/<lid>", "accounting.loans", "edit")
def loan_del(ctx, lid):
    loans.delete(ctx.db, lid)
    ctx.log("loan.delete", lid)
    return ok()


@route("POST", "/api/loans-preview/<action>", "accounting.loans", "add")
def loan_preview(ctx, action):
    if action == "schedule":
        return ok(loans.preview_schedule(ctx.body))
    pl = loans.plan(ctx.db, ctx.body.get("id"), action, ctx.body)
    return ok({"kind": pl["kind"], "date": pl["date"], "memo": pl["memo"], "lines": _named_lines(ctx.db, pl["lines"]),
               "notes": pl["notes"]})


@route("POST", "/api/loans/<lid>/<action>", "accounting.loans", "add")
def loan_action(ctx, lid, action):
    if action in ("close", "reopen"):
        ctx.require("accounting.loans", "edit")
        loans.set_status(ctx.db, lid, action == "close", ctx.body.get("date"))
        ctx.log(f"loan.{action}", lid)
        return ok()
    eid = loans.post(ctx.db, lid, action, ctx.body, ctx.user["username"])
    ctx.log(f"loan.{action}", f"{lid} -> {eid}")
    return ok({"id": eid})


def _named_lines(db, lines):
    names = {a["code"]: a for a in db.q("SELECT code, name, name_ar FROM accounts")}
    return [{**ln, "name": names.get(ln["account"], {}).get("name", ""), "name_ar": names.get(ln["account"], {}).get("name_ar", "")}
            for ln in lines]


# ================================================================ custody
@route("GET", "/api/custody", "purchasing.custody")
def custody_list(ctx):
    q = ctx.q
    return ok(custody.register(ctx.db, q.get("q"), q.get("state"), q.get("holder")))


@route("GET", "/api/custody/<cid>", "purchasing.custody")
def custody_one(ctx, cid):
    return ok(custody.statement(ctx.db, cid))


@route("POST", "/api/custody", "purchasing.custody", "add")
def custody_add(ctx):
    cid = custody.save(ctx.db, ctx.body, user=ctx.user["username"])
    ctx.log("custody.add", cid)
    return ok({"id": cid})


@route("PUT", "/api/custody/<cid>", "purchasing.custody", "edit")
def custody_edit(ctx, cid):
    custody.save(ctx.db, {**ctx.body, "booking": "keep"}, cid, ctx.user["username"])
    ctx.log("custody.edit", cid)
    return ok()


@route("DELETE", "/api/custody/<cid>", "purchasing.custody", "edit")
def custody_del(ctx, cid):
    custody.delete(ctx.db, cid)
    ctx.log("custody.delete", cid)
    return ok()


@route("POST", "/api/custody-preview/<action>", "purchasing.custody", "add")
def custody_preview(ctx, action):
    pl = custody.plan(ctx.db, ctx.body.get("id"), action, ctx.body)
    return ok({"date": pl["date"], "notes": pl["notes"],
               "entries": [{**e, "lines": _named_lines(ctx.db, e["lines"])} for e in pl["entries"]]})


@route("POST", "/api/custody/<cid>/<action>", "purchasing.custody", "add")
def custody_action(ctx, cid, action):
    if action in ("close", "reopen"):
        ctx.require("purchasing.custody", "edit")
        custody.set_status(ctx.db, cid, action == "close", ctx.body.get("date"))
        ctx.log(f"custody.{action}", cid)
        return ok()
    ids = custody.post(ctx.db, cid, action, ctx.body, ctx.user["username"])
    ctx.log(f"custody.{action}", f"{cid} -> {ids}")
    return ok({"ids": ids})


# ================================================================ payroll
@route("GET", "/api/employees", "payroll.employees")
def emp_list(ctx):
    return ok({"rows": payroll.list_employees(ctx.db, ctx.q.get("q"), ctx.q.get("status")), "types": payroll.EMP_TYPES,
               "cost_types": payroll.COST_TYPES, "settings": payroll.settings(ctx.db)})


@route("GET", "/api/employees/<eid>", "payroll.employees")
def emp_one(ctx, eid):
    e = payroll.get_employee(ctx.db, eid)
    e["history"] = [{"period": r["period"], "status": r["status"], **(json.loads(r["result"] or "{}") and
                     {k: json.loads(r["result"])[k] for k in ("gross", "net", "tax", "si_emp")})}
                    for r in ctx.db.q("SELECT r.period, r.status, l.result FROM payroll_lines l JOIN payroll_runs r ON r.id=l.run_id "
                                      "WHERE l.employee_id=? ORDER BY r.period DESC", (e["id"],))]
    return ok(e)


@route("POST", "/api/employees", "payroll.employees", "add")
def emp_add(ctx):
    eid = payroll.save_employee(ctx.db, ctx.body)
    ctx.log("employee.add", eid)
    return ok({"id": eid})


@route("PUT", "/api/employees/<eid>", "payroll.employees", "edit")
def emp_edit(ctx, eid):
    payroll.save_employee(ctx.db, ctx.body, eid)
    ctx.log("employee.edit", eid)
    return ok()


@route("DELETE", "/api/employees/<eid>", "payroll.employees", "edit")
def emp_del(ctx, eid):
    payroll.delete_employee(ctx.db, eid)
    ctx.log("employee.delete", eid)
    return ok()


@route("GET", "/api/payroll/settings", "payroll")
def pr_settings(ctx):
    return ok(payroll.settings(ctx.db))


@route("PUT", "/api/payroll/settings", "payroll.settings", "edit")
def pr_settings_save(ctx):
    s = payroll.save_settings(ctx.db, ctx.body)
    ctx.log("payroll.settings", "")
    return ok(s)


@route("POST", "/api/payroll/preview", "payroll")
def pr_preview(ctx):
    return ok(payroll.preview(ctx.db, ctx.body))


@route("GET", "/api/payroll/runs", "payroll.runs")
def pr_runs(ctx):
    return ok({"rows": payroll.list_runs(ctx.db)})


@route("POST", "/api/payroll/runs", "payroll.runs", "add")
def pr_run_add(ctx):
    rid = payroll.create_run(ctx.db, ctx.body)
    ctx.log("payroll.run", rid)
    return ok({"id": rid})


@route("GET", "/api/payroll/runs/<rid>", "payroll.runs")
def pr_run(ctx, rid):
    return ok({**payroll.get_run(ctx.db, rid), "settings": payroll.settings(ctx.db)})


@route("PUT", "/api/payroll/runs/<rid>", "payroll.runs", "add")
def pr_run_save(ctx, rid):
    payroll.save_lines(ctx.db, rid, ctx.body.get("lines") or [])
    return ok(payroll.get_run(ctx.db, rid))


@route("DELETE", "/api/payroll/runs/<rid>", "payroll.runs", "edit")
def pr_run_del(ctx, rid):
    payroll.delete_run(ctx.db, rid)
    ctx.log("payroll.run.delete", rid)
    return ok()


@route("POST", "/api/payroll/runs/<rid>/<action>", "payroll.runs", "add")
def pr_run_action(ctx, rid, action):
    if action == "post":
        payroll.post_run(ctx.db, rid, ctx.user["username"])
    elif action == "unpost":
        ctx.require("payroll.runs", "edit")
        payroll.unpost_run(ctx.db, rid, ctx.user["username"])
    else:
        return ok({"error": "unknown action"}, 400)
    ctx.log(f"payroll.{action}", rid)
    return ok(payroll.get_run(ctx.db, rid))


@route("GET", "/api/payroll/runs/<rid>/payslip/<eid>", "payroll.runs")
def pr_payslip(ctx, rid, eid):
    if ctx.q.get("print"):
        return Reply(payroll.payslips_html(ctx.db, rid, eid, ctx.q.get("lang") or "en").encode("utf-8"), 200, "text/html; charset=utf-8")
    return ok(payroll.payslip(ctx.db, rid, eid))


@route("GET", "/api/crew", "payroll.crew")
def crew_list(ctx):
    return ok({"rows": payroll.list_crew(ctx.db), "items": payroll.DEFAULT_WORK_ITEMS.get(ctx.db.setting("business_type") or "general",
                                                                                          payroll.DEFAULT_WORK_ITEMS["general"])})


@route("POST", "/api/crew/calc", "payroll.crew")
def crew_calc(ctx):
    return ok(payroll.crew_calc(ctx.body.get("lines") or []))


@route("POST", "/api/crew", "payroll.crew", "add")
def crew_add(ctx):
    return ok({"id": payroll.save_crew(ctx.db, ctx.body)})


@route("PUT", "/api/crew/<cid>", "payroll.crew", "edit")
def crew_edit(ctx, cid):
    payroll.save_crew(ctx.db, ctx.body, cid)
    return ok()


@route("DELETE", "/api/crew/<cid>", "payroll.crew", "edit")
def crew_del(ctx, cid):
    payroll.delete_crew(ctx.db, cid)
    return ok()


@route("POST", "/api/crew/<cid>/pay", "payroll.crew", "add")
def crew_pay(ctx, cid):
    eid = payroll.pay_crew(ctx.db, cid, ctx.body, ctx.user["username"])
    ctx.log("crew.pay", f"{cid} -> {eid}")
    return ok({"id": eid})


# ================================================================ rentals
@route("GET", "/api/rent-items", "rentals.fleet")
def rent_items(ctx):
    q = ctx.q
    return ok(rentals.list_items(ctx.db, q.get("q"), q.get("category")))


@route("GET", "/api/rent-catalog")
def rent_catalog(ctx):
    """What can be put on a quote (contracts or calculator users need it too)."""
    if not (ctx.can("rentals.contracts") or ctx.can("rentals.fleet") or ctx.can("rentals.calculator")):
        raise PermissionError()
    return ok(rentals.list_items(ctx.db, active_only=True))


@route("GET", "/api/rent-items/<iid>", "rentals.fleet")
def rent_item_one(ctx, iid):
    return ok(rentals.item_detail(ctx.db, iid))


@route("POST", "/api/rent-items", "rentals.fleet", "add")
def rent_item_add(ctx):
    return ok({"id": rentals.save_item(ctx.db, ctx.body)})


@route("PUT", "/api/rent-items/<iid>", "rentals.fleet", "edit")
def rent_item_edit(ctx, iid):
    rentals.save_item(ctx.db, ctx.body, iid)
    return ok()


@route("DELETE", "/api/rent-items/<iid>", "rentals.fleet", "edit")
def rent_item_del(ctx, iid):
    rentals.delete_item(ctx.db, iid)
    return ok()


@route("POST", "/api/rent-items/<iid>/lots", "rentals.fleet", "add")
def rent_lot_add(ctx, iid):
    lid = rentals.add_lot(ctx.db, iid, ctx.body, ctx.user["username"])
    ctx.log("rent.lot", lid)
    return ok({"id": lid})


@route("DELETE", "/api/rent-lots/<lid>", "rentals.fleet", "edit")
def rent_lot_del(ctx, lid):
    rentals.delete_lot(ctx.db, int(lid), ctx.user["username"])
    return ok()


@route("POST", "/api/rent-scrap", "rentals.fleet", "edit")
def rent_scrap(ctx):
    return ok(rentals.scrap(ctx.db, ctx.body, ctx.user["username"]))


@route("POST", "/api/rent-kits", "rentals.fleet", "add")
def rent_kit_add(ctx):
    return ok({"id": rentals.save_kit(ctx.db, ctx.body)})


@route("PUT", "/api/rent-kits/<kid>", "rentals.fleet", "edit")
def rent_kit_edit(ctx, kid):
    rentals.save_kit(ctx.db, ctx.body, int(kid))
    return ok()


@route("DELETE", "/api/rent-kits/<kid>", "rentals.fleet", "edit")
def rent_kit_del(ctx, kid):
    rentals.delete_kit(ctx.db, kid)
    return ok()


@route("POST", "/api/rent-depreciation-preview", "rentals.fleet", "add")
def rent_depr_preview(ctx):
    return ok(rentals.plan_depreciation(ctx.db, ctx.body.get("through")))


@route("POST", "/api/rent-depreciation", "rentals.fleet", "add")
def rent_depr_run(ctx):
    eid = rentals.run_depreciation(ctx.db, ctx.body.get("through"), ctx.user["username"])
    ctx.log("rent.depreciation", eid)
    return ok({"id": eid})


@route("POST", "/api/rent-starter", "rentals.fleet", "add")
def rent_starter(ctx):
    return ok(rentals.starter(ctx.db))


@route("GET", "/api/rent-settings")
def rent_settings(ctx):
    if not (ctx.can("rentals.contracts") or ctx.can("rentals.fleet")):
        raise PermissionError()
    return ok(rentals.settings(ctx.db))


@route("PUT", "/api/rent-settings", "rentals.fleet", "edit")
def rent_settings_put(ctx):
    return ok(rentals.save_settings(ctx.db, ctx.body))


def _calc_access(ctx):
    """Equipment calculators: the rental screens; general calculators (any business sum): everyone signed in."""
    return ctx.can("rentals.calculator") or ctx.can("rentals.contracts")


@route("GET", "/api/rent-models")
def rent_models(ctx):
    models = rentals.list_models(ctx.db)
    if not _calc_access(ctx):
        models = [m for m in models if m["category"] == "general"]
    return ok({"models": models, "can_build": auth.granted(ctx.user, "build_calculators")})


def _shape(spec):
    """A calculator's structure without the default values (changing defaults is not rebuilding it)."""
    sp = json.loads(json.dumps(spec or {}))
    for k in ("inputs", "params"):
        for r in sp.get(k) or []:
            if isinstance(r, dict):
                r.pop("value", None)
    return json.dumps(sp, sort_keys=True)


@route("POST", "/api/rent-models")
def rent_model_add(ctx):
    """New calculators (or copies): super administrators, and whoever they allow to build calculators."""
    if not auth.granted(ctx.user, "build_calculators"):
        raise PermissionError("Building calculators is for super administrators and people they allow (Users → special rights)")
    return ok({"id": rentals.save_model(ctx.db, ctx.body)})


@route("PUT", "/api/rent-models/<mid>")
def rent_model_edit(ctx, mid):
    old = ctx.db.one("SELECT spec FROM rent_models WHERE id=?", (int(mid),))
    if not old:
        raise acc.ValidationError("Calculator not found")
    if not auth.granted(ctx.user, "build_calculators"):
        # without the right: only its default values change (Save these settings as defaults), with full rights on calculators
        ctx.require("rentals.calculator", "edit")
        if _shape(json.loads(old["spec"] or "{}")) != _shape(rentals._spec(ctx.body.get("spec") or {})):
            raise PermissionError("Changing a calculator's formulas is for super administrators and people they allow")
    rentals.save_model(ctx.db, ctx.body, mid)
    return ok()


@route("DELETE", "/api/rent-models/<mid>")
def rent_model_del(ctx, mid):
    if not auth.granted(ctx.user, "build_calculators"):
        raise PermissionError()
    rentals.delete_model(ctx.db, mid)
    return ok()


@route("GET", "/api/rent-service-rates")
def rent_service_rates(ctx):
    if not (ctx.can("rentals.calculator") or ctx.can("rentals.contracts")):
        raise PermissionError()
    return ok(rentals.service_rates(ctx.db))


@route("PUT", "/api/rent-service-rates", "rentals.calculator", "edit")
def rent_service_rates_save(ctx):
    return ok(rentals.save_service_rates(ctx.db, ctx.body))


@route("POST", "/api/rent-services-preview")
def rent_services_quote(ctx):
    if not (ctx.can("rentals.calculator") or ctx.can("rentals.contracts")):
        raise PermissionError()
    b = ctx.body
    return ok(rentals.service_quote(ctx.db, acc.num(b.get("area") or 0, "area"), acc.num(b.get("weight") or 0, "weight"),
                                    acc.num(b.get("monthly_rent") or 0, "monthly rent"), b.get("scope") or "both",
                                    b.get("overrides") if isinstance(b.get("overrides"), dict) else None))


@route("POST", "/api/rent-models-preview/eval")
def rent_model_eval(ctx):
    spec = ctx.body.get("spec")
    if not spec and ctx.body.get("model_id"):
        m = ctx.db.one("SELECT spec, category FROM rent_models WHERE id=?", (int(ctx.body["model_id"]),))
        spec = json.loads(m["spec"]) if m else {}
        if m and m["category"] != "general" and not _calc_access(ctx):
            raise PermissionError()
    elif not (_calc_access(ctx) or auth.granted(ctx.user, "build_calculators") or ctx.body.get("general")):
        raise PermissionError()
    if spec and not (_calc_access(ctx) or auth.granted(ctx.user, "build_calculators")):
        spec = {**spec, "components": []}           # a general calculator never looks at equipment costs
    return ok(rentals.evaluate(ctx.db, spec or {}, ctx.body.get("values") or {}))


@route("GET", "/api/rentals", "rentals.contracts")
def rentals_list(ctx):
    return ok(rentals.list_rentals(ctx.db, ctx.q.get("q"), ctx.q.get("status")))


@route("GET", "/api/rentals/<rid>", "rentals.contracts")
def rental_one(ctx, rid):
    return ok(rentals.detail(ctx.db, rid))


@route("POST", "/api/rentals", "rentals.contracts", "add")
def rental_add(ctx):
    rid = rentals.save(ctx.db, ctx.body, user=ctx.user["username"])
    ctx.log("rental.add", rid)
    return ok({"id": rid})


@route("PUT", "/api/rentals/<rid>", "rentals.contracts", "edit")
def rental_edit(ctx, rid):
    rentals.save(ctx.db, ctx.body, rid, ctx.user["username"])
    ctx.log("rental.edit", rid)
    return ok()


@route("DELETE", "/api/rentals/<rid>", "rentals.contracts", "edit")
def rental_del(ctx, rid):
    rentals.delete(ctx.db, rid)
    return ok()


@route("GET", "/api/rentals/<rid>/allocation", "rentals.contracts")
def rental_allocation(ctx, rid):
    return ok(rentals.allocation(ctx.db, rid, ctx.q.get("line")))


@route("GET", "/api/rentals/<rid>/handover-template", "rentals.contracts")
def rental_handover_template(ctx, rid):
    return ok(rentals.handover_template(ctx.db, rid, ctx.q.get("kind") or "delivery"))


@route("GET", "/api/rentals/<rid>/invoice-preview", "rentals.contracts")
def rental_invoice_preview(ctx, rid):
    return ok(rentals.invoice_lines(ctx.db, rid, ctx.q.get("from"), ctx.q.get("to"), ctx.q.get("lang") or "en"))


@route("GET", "/api/rentals/<rid>/doc", "rentals.contracts")
def rental_doc(ctx, rid):
    fn = {"offer": rental_docs.offer_html, "contract": rental_docs.contract_html}.get(ctx.q.get("kind") or "offer")
    if not fn:
        return ok({"error": "Unknown document"}, 404)
    return Reply(fn(ctx.db, rid, ctx.q.get("lang")).encode("utf-8"), ctype="text/html; charset=utf-8")


@route("POST", "/api/rentals/<rid>/handovers", "rentals.contracts", "add")
def rental_handover_add(ctx, rid):
    return ok({"id": rentals.save_handover(ctx.db, rid, ctx.body)})


@route("PUT", "/api/rental-handovers/<hid>", "rentals.contracts", "edit")
def rental_handover_edit(ctx, hid):
    h = ctx.db.one("SELECT rental_id FROM rental_handovers WHERE id=?", (int(hid),))
    if not h:
        raise acc.ValidationError("Handover not found")
    rentals.save_handover(ctx.db, h["rental_id"], ctx.body, hid)
    return ok()


@route("POST", "/api/rental-handovers/<hid>/<what>", "rentals.contracts", "edit")
def rental_handover_action(ctx, hid, what):
    if what == "confirm":
        rentals.confirm_handover(ctx.db, hid, ctx.user["username"])
    elif what == "undo":
        rentals.undo_handover(ctx.db, hid, ctx.user["username"])
    else:
        raise acc.ValidationError("Unknown action")
    ctx.log(f"rental.handover.{what}", hid)
    return ok()


@route("DELETE", "/api/rental-handovers/<hid>", "rentals.contracts", "edit")
def rental_handover_del(ctx, hid):
    rentals.delete_handover(ctx.db, hid)
    return ok()


@route("GET", "/api/rental-handovers/<hid>/doc", "rentals.contracts")
def rental_handover_doc(ctx, hid):
    return Reply(rental_docs.handover_html(ctx.db, hid, ctx.q.get("lang")).encode("utf-8"), ctype="text/html; charset=utf-8")


@route("DELETE", "/api/rental-charges/<cid>", "rentals.contracts", "edit")
def rental_charge_del(ctx, cid):
    rentals.delete_charge(ctx.db, cid)
    return ok()


@route("POST", "/api/rentals/<rid>/<what>", "rentals.contracts", "add")
def rental_action(ctx, rid, what):
    if what == "invoice":
        ctx.require("sales.invoices", "add")
        xid = rentals.create_invoice(ctx.db, rid, ctx.body, ctx.user["username"])
        ctx.log("rental.invoice", f"{rid} -> {xid}")
        return ok({"id": xid})
    if what in ("cancel", "close", "reopen", "unsign"):
        ctx.require("rentals.contracts", "edit")
    rentals.action(ctx.db, rid, what, ctx.body, ctx.user["username"])
    ctx.log(f"rental.{what}", rid)
    return ok()


# ============================================================ fixed assets
@route("GET", "/api/app-update")
def app_update_check(ctx):
    """Is a newer desktop app on the website? (Only the installed offline app asks; the cloud apps check the site.)"""
    from . import appupdate
    return ok(appupdate.check())


@route("POST", "/api/app-update/install")
def app_update_install(ctx):
    """Back up, download the new installer, run it silently and close; the installer reopens the app."""
    from . import appupdate
    if not ctx.user.get("is_admin"):
        raise PermissionError("Ask an administrator to install the update")

    def stop():
        threading.Thread(target=ctx.server.shutdown, daemon=True).start()
        threading.Timer(6, lambda: os._exit(0)).start()
    try:
        return ok(appupdate.install(ctx.server.backup, stop))
    except (OSError, ValueError) as e:
        raise acc.ValidationError(str(e)) from None


@route("GET", "/api/alerts")
def alerts(ctx):
    """What needs attention now, for the bell in the top bar — only what this user may see."""
    from . import approvals, dashboards
    out = dashboards.alerts(ctx.db, ctx.can)
    waiting = approvals.waiting_for(ctx.db, ctx.user, ctx.can)
    if waiting:
        out.insert(0, {"level": "warn", "en": f"Entries waiting for your approval: {len(waiting)}",
                       "ar": f"قيود في انتظار اعتمادك: {len(waiting)}", "href": "#/approvals", "count": len(waiting), "kind": "approvals"})
    sent_back = ctx.db.one("SELECT COUNT(*) n FROM approvals WHERE created_by=? AND status='rejected'", (ctx.user["username"],))["n"]
    if sent_back:
        out.insert(0, {"level": "bad", "en": f"Your entries sent back for changes: {sent_back}",
                       "ar": f"قيود أُعيدت إليك للتعديل: {sent_back}", "href": "#/approvals?status=rejected", "count": sent_back,
                       "kind": "approvals_back"})
    if ctx.can("team.myday", "add"):
        from . import workdays
        me_late = workdays.online_not_checked_in(ctx.db, ctx.user["username"])
        if me_late:
            out.insert(0, {"level": "bad", "en": f"You have been online for {me_late[0]['minutes']} minutes without checking in.",
                           "ar": f"أنت متصل منذ {me_late[0]['minutes']} دقيقة دون تسجيل الحضور.", "href": "#/my-day", "count": 1, "kind": "checkin_self"})
    if ctx.can("payroll.workdays"):
        from . import workdays
        idle = [r for r in workdays.online_not_checked_in(ctx.db) if r["username"] != ctx.user["username"]]
        if idle:
            names = ", ".join(r["name"] for r in idle[:5]) + (f" +{len(idle) - 5}" if len(idle) > 5 else "")
            out.insert(0, {"level": "warn", "en": f"Online without checking in: {names}", "ar": f"متصلون دون تسجيل الحضور: {names}",
                           "href": "#/workdays", "count": len(idle), "kind": "checkin_idle"})
        flagged = workdays.flagged_today(ctx.db)
        if flagged:
            out.insert(0, {"level": "warn", "en": f"Check-ins with location warnings today: {len(flagged)}", "ar": f"تسجيلات حضور بها تحذيرات موقع اليوم: {len(flagged)}",
                           "href": "#/workdays", "count": len(flagged), "kind": "checkin_flags"})
    if ctx.can("payroll.requests", "edit"):
        from . import hrrequests
        n = hrrequests.pending_count(ctx.db)
        if n:
            out.insert(0, {"level": "warn", "en": f"Employee requests awaiting approval: {n}", "ar": f"طلبات موظفين في انتظار الاعتماد: {n}",
                           "href": "#/requests?status=pending", "count": n, "kind": "hr_requests"})
    follow = []
    if ctx.can("crm.followups"):
        from . import crm
        follow = crm.due_for(ctx.db, ctx.user["username"])
        if follow:
            late = sum(1 for f in follow if f["due"] < acc.today())
            out.insert(0, {"level": "bad" if late else "warn", "en": f"Follow-ups due: {len(follow)}" + (f" ({late} overdue)" if late else ""),
                           "ar": f"متابعات مستحقة: {len(follow)}" + (f" ({late} متأخرة)" if late else ""), "href": "#/followups?status=due",
                           "count": len(follow), "kind": "followups"})
    if ctx.can("crm.desk"):
        from . import desk
        u = desk.unread(ctx.db)
        if u["messages"]:
            out.insert(0, {"level": "warn", "en": f"Guest messages waiting on the online desk: {u['messages']}",
                           "ar": f"رسائل زوار في انتظار الرد: {u['messages']}", "href": "#/desk?tab=chats", "count": u["messages"], "kind": "desk"})
        if u["errors"]:
            out.insert(0, {"level": "bad", "en": f"Online desk items that could not be added: {u['errors']}",
                           "ar": f"طلبات من الصفحات العامة لم تُضف: {u['errors']}", "href": "#/desk?tab=other", "count": u["errors"], "kind": "desk_err"})
    if ctx.can("purchasing.bills"):
        from . import billsubs
        bills = billsubs.due_for(ctx.db, ctx.user["username"])
        if bills:
            late = sum(1 for b in bills if b["days_left"] < 0)
            out.insert(0, {"level": "bad" if late else "warn", "en": f"Subscriptions & bills due: {len(bills)}" + (f" ({late} overdue)" if late else ""),
                           "ar": f"اشتراكات وفواتير مستحقة: {len(bills)}" + (f" ({late} متأخرة)" if late else ""), "href": "#/bills",
                           "count": len(bills), "kind": "bills"})
    feed = ctx.db.q("SELECT id, title, body, href FROM alert_feed WHERE username=? AND read_at IS NULL ORDER BY id DESC LIMIT 20",
                    (ctx.user["username"],)) if ctx.can("team.alerts") else []
    for f in reversed(feed):
        out.insert(0, {"level": "warn", "en": f"{f['title']}: {f['body']}", "ar": f"{f['title']}: {f['body']}", "href": f["href"] or "#/alerts",
                       "kind": "feed", "id": f["id"]})
    return ok({"rows": out, "count": len(out), "approvals": [w["id"] for w in waiting], "followups": [f["id"] for f in follow],
               "feed": [f["id"] for f in feed]})


@route("GET", "/api/assets-overview", "assets.overview")
def assets_overview(ctx):
    from . import asset_views
    return ok(asset_views.overview(ctx.db, ctx.q.get("date_to")))


@route("GET", "/api/cash-accounts", "assets.cash")
def cash_accounts(ctx):
    from . import asset_views
    return ok(asset_views.cash_accounts(ctx.db, ctx.q.get("date_to")))


@route("POST", "/api/cash-transfer", "assets.cash", "add")
def cash_transfer(ctx):
    from . import asset_views
    eid = asset_views.transfer(ctx.db, ctx.body, ctx.user["username"])
    ctx.log("cash.transfer", eid)
    return ok({"id": eid})


@route("GET", "/api/depreciation-history", "assets.depreciation")
def depreciation_history(ctx):
    from . import asset_views
    return ok({"rows": asset_views.depreciation_history(ctx.db)})


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


@route("GET", "/api/depreciation-policy")
def depr_policy(ctx):
    if not (ctx.can("assets.register") or ctx.can("settings") or ctx.can("rentals.fleet")):
        raise PermissionError()
    return ok({**assets.policy(ctx.db), "methods": list(assets.METHODS), "conventions": list(assets.CONVENTIONS)})


@route("PUT", "/api/depreciation-policy")
def depr_policy_save(ctx):
    if not (ctx.can("settings", "edit") or ctx.can("assets.depreciation", "edit")):
        raise PermissionError()
    r = assets.save_policy(ctx.db, ctx.body)
    ctx.log("depreciation.policy", json.dumps(r)[:300])
    return ok(r)


@route("GET", "/api/assets/<aid>/schedule", "assets.register")
def asset_schedule(ctx, aid):
    return ok(assets.schedule(ctx.db, assets.get(ctx.db, aid)))


@route("POST", "/api/assets-preview/schedule", "assets.register")
def asset_schedule_preview(ctx):
    return ok(assets.preview_schedule(ctx.db, ctx.body))


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


@route("GET", "/api/equity/exit")
def equity_exit(ctx):
    """What each partner would take out (or owe) if he left today, and the funding partner's money in and out."""
    if not ctx.can("partners.equity"):
        raise PermissionError()
    from . import settlement
    return ok(settlement.exit_view(ctx.db, ctx.q.get("date_to")))


@route("POST", "/api/equity/funding/book", "partners.equity", "edit")
def equity_funding_book(ctx):
    from . import settlement
    eid = settlement.book_funding(ctx.db, ctx.body.get("date"), ctx.user["username"])
    ctx.log("equity.funding", eid)
    return ok({"entry_id": eid})


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
    if not (ctx.can("sales.parties") or ctx.can("sales.invoices") or ctx.can("purchasing") or ctx.can("tax")
            or ctx.can("purchasing.contacts")):
        raise PermissionError()
    w, p = ["1=1"], []
    if ctx.q.get("type"):
        w.append("(type=? OR type='both')")
        p.append(ctx.q["type"])
    if ctx.q.get("category"):
        w.append("category=?")
        p.append(ctx.q["category"].lower())
    if ctx.q.get("contacts"):  # the directory leaves out customers (they live under Sales)
        w.append("NOT (type='customer' AND category IN ('','customer'))")
    if ctx.q.get("q"):
        cols = ("name", "tax_id", "phone", "phone2", "whatsapp", "email", "contact_person", "specialty", "vehicle",
                "category", "governate", "notes")
        w.append("(" + " OR ".join(f"{c} LIKE ?" for c in cols) + ")")
        p += [f"%{ctx.q['q']}%"] * len(cols)
    limit, offset = page(ctx.q)
    rows = ctx.db.q(f"SELECT * FROM parties WHERE {' AND '.join(w)} ORDER BY name LIMIT ? OFFSET ?", p + [limit, offset])
    total = ctx.db.one(f"SELECT COUNT(*) n FROM parties WHERE {' AND '.join(w)}", p)["n"]
    cats = [r["category"] for r in ctx.db.q("SELECT DISTINCT category FROM parties WHERE category<>'' ORDER BY 1")]
    return ok({"rows": rows, "total": total, "categories": cats})


@route("GET", "/api/contacts/<pid>")
def contact_view(ctx, pid):
    if not (ctx.can("purchasing.contacts") or ctx.can("sales.parties")):
        raise PermissionError()
    p = ctx.db.one("SELECT * FROM parties WHERE id=?", (int(pid),))
    if not p:
        raise acc.ValidationError("Contact not found")
    return ok({"contact": p, **acc.contact_summary(ctx.db, p["name"])})


@route("POST", "/api/parties-master")
def party_add(ctx):
    if not (ctx.can("sales.parties", "add") or ctx.can("sales.invoices", "add") or ctx.can("purchasing", "add")
            or ctx.can("purchasing.contacts", "add")):
        raise PermissionError()
    return ok({"id": acc.save_party(ctx.db, ctx.body)})


@route("PUT", "/api/parties-master/<pid>")
def party_edit(ctx, pid):
    if not (ctx.can("sales.parties", "edit") or ctx.can("sales.invoices", "edit") or ctx.can("purchasing", "edit")
            or ctx.can("purchasing.contacts", "edit")):
        raise PermissionError()
    return ok({"id": acc.save_party(ctx.db, ctx.body, int(pid))})


@route("DELETE", "/api/parties-master/<pid>")
def party_del(ctx, pid):
    if not (ctx.can("sales.parties", "edit") or ctx.can("purchasing.contacts", "edit")):
        raise PermissionError()
    p = ctx.db.one("SELECT name FROM parties WHERE id=?", (int(pid),))
    if not p:
        raise acc.ValidationError("Not found")
    x = ctx.db.one("SELECT number FROM extracts WHERE customer=? LIMIT 1", (p["name"],))
    if x:
        raise acc.ValidationError((f"{p['name']} is the customer on invoice {x['number']}. Its tax ID and address are required for that invoice and its e-invoice, so it cannot be deleted."))
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
        raise acc.ValidationError((f"Tax code {code} is used on invoices, vouchers or customers. Clear Active to stop using it; it remains on past documents."))
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
    from .db import ETA_SECRETS, ETA_SETTINGS
    for k, v in ctx.body.items():
        if k not in ETA_SETTINGS or (k in ETA_SECRETS and (v or "").startswith("••")):
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


# ================================================== projects, contracts, procurement, site (erp/projects.py …)
def _u(ctx):
    return ctx.user["username"]


@route("GET", "/api/projects", "projects.overview")
def prj_overview(ctx):
    from . import projects
    return ok(projects.overview(ctx.db))


@route("GET", "/api/projects/<bu>", "projects.overview")
def prj_status(ctx, bu):
    from . import projects
    r = projects.status_report(ctx.db, bu)
    r["phases"] = projects.phases(ctx.db, r["bu"]["id"])
    return ok(r)


@route("POST", "/api/project-phases", "projects.overview", "add")
def prj_phase_add(ctx):
    from . import projects
    return ok({"id": projects.save_phase(ctx.db, ctx.body)})


@route("PUT", "/api/project-phases/<pid>", "projects.overview", "edit")
def prj_phase_edit(ctx, pid):
    from . import projects
    projects.save_phase(ctx.db, ctx.body, pid)
    return ok()


@route("DELETE", "/api/project-phases/<pid>", "projects.overview", "edit")
def prj_phase_del(ctx, pid):
    from . import projects
    projects.delete_phase(ctx.db, pid)
    return ok()


@route("GET", "/api/project-tasks", "projects.tasks")
def prj_tasks(ctx):
    from . import projects
    q = ctx.q
    return ok(projects.tasks(ctx.db, q.get("bu_id"), q.get("assignee"), q.get("status"), q.get("q")))


@route("POST", "/api/project-tasks", "projects.tasks", "add")
def prj_task_add(ctx):
    from . import projects
    return ok({"id": projects.save_task(ctx.db, ctx.body, None, _u(ctx))})


@route("PUT", "/api/project-tasks/<tid>", "projects.tasks", "add")
def prj_task_edit(ctx, tid):
    from . import projects
    projects.save_task(ctx.db, ctx.body, tid, _u(ctx))
    return ok()


@route("DELETE", "/api/project-tasks/<tid>", "projects.tasks", "edit")
def prj_task_del(ctx, tid):
    from . import projects
    projects.delete_task(ctx.db, tid)
    return ok()


# contracts & variation orders
@route("GET", "/api/contracts", "contracts.contracts")
def con_list(ctx):
    from . import contracts
    q = ctx.q
    return ok(contracts.listing(ctx.db, q.get("kind"), q.get("bu_id"), q.get("status"), q.get("q")))


@route("GET", "/api/contracts/<cid>", "contracts.contracts")
def con_get(ctx, cid):
    from . import contracts
    return ok(contracts.get(ctx.db, cid))


@route("POST", "/api/contracts", "contracts.contracts", "add")
def con_add(ctx):
    from . import contracts
    cid = contracts.save(ctx.db, ctx.body)
    ctx.log("contract.add", cid)
    return ok({"id": cid})


@route("PUT", "/api/contracts/<cid>", "contracts.contracts", "edit")
def con_edit(ctx, cid):
    from . import contracts
    contracts.save(ctx.db, ctx.body, cid)
    ctx.log("contract.edit", cid)
    return ok()


@route("DELETE", "/api/contracts/<cid>", "contracts.contracts", "edit")
def con_del(ctx, cid):
    from . import contracts
    c = contracts.get(ctx.db, cid)
    if c["certificates"] or any(v["status"] == "approved" for v in c["variations"]):
        raise acc.ValidationError("This contract has certificates or approved variations. Mark it as terminated instead.")
    ctx.db.x("DELETE FROM contracts WHERE id=?", (c["id"],))
    ctx.log("contract.delete", c["code"])
    return ok()


@route("POST", "/api/contracts/<cid>/variations", "contracts.variations", "add")
def con_vo_add(ctx, cid):
    from . import contracts
    return ok({"id": contracts.save_vo(ctx.db, cid, ctx.body)})


@route("PUT", "/api/variations/<vid>", "contracts.variations", "add")
def con_vo_edit(ctx, vid):
    from . import contracts
    v = ctx.db.one("SELECT contract_id FROM variation_orders WHERE id=?", (int(vid),))
    if not v:
        raise acc.ValidationError("Variation order not found")
    contracts.save_vo(ctx.db, v["contract_id"], ctx.body, vid)
    return ok()


@route("POST", "/api/variations/<vid>/decide", "contracts.variations", "edit")
def con_vo_decide(ctx, vid):
    from . import contracts
    contracts.decide_vo(ctx.db, vid, ctx.body.get("decision"), _u(ctx))
    ctx.log("variation." + str(ctx.body.get("decision")), vid)
    return ok()


@route("DELETE", "/api/variations/<vid>", "contracts.variations", "edit")
def con_vo_del(ctx, vid):
    from . import contracts
    contracts.delete_vo(ctx.db, vid)
    return ok()


@route("GET", "/api/variations", "contracts.variations")
def con_vo_list(ctx):
    rows = ctx.db.q("SELECT v.*, c.code AS contract, c.party, c.kind, b.code AS bu_code FROM variation_orders v JOIN contracts c ON c.id=v.contract_id "
                    "LEFT JOIN business_units b ON b.id=c.bu_id ORDER BY v.date DESC, v.id DESC")
    for r in rows:
        r["amount"] = acc.to_major(r["amount"])
    return ok(rows)


# subcontractor certificates
@route("GET", "/api/sub-certificates", "contracts.certificates")
def cert_list(ctx):
    from . import contracts
    out = []
    for c in ctx.db.q("SELECT id FROM contracts WHERE kind='sub' ORDER BY code"):
        k = contracts.get(ctx.db, c["id"])
        for x in k["certificates"]:
            out.append({"id": x["id"], "contract_id": k["id"], "contract": k["code"], "party": k["party"], "bu_code": k["bu_code"],
                        **{f: x[f] for f in ("number", "date", "period_to", "status", "gross_to_date", "previous", "this_period", "retention",
                                             "advance_recovered", "vat", "wht", "net_payable", "entry_id")}})
    out.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    return ok(out)


@route("POST", "/api/contracts/<cid>/certificates", "contracts.certificates", "add")
def cert_add(ctx, cid):
    from . import contracts
    return ok({"id": contracts.save_certificate(ctx.db, cid, ctx.body)})


@route("PUT", "/api/sub-certificates/<xid>", "contracts.certificates", "add")
def cert_edit(ctx, xid):
    from . import contracts
    x = ctx.db.one("SELECT contract_id FROM sub_certificates WHERE id=?", (int(xid),))
    if not x:
        raise acc.ValidationError("Certificate not found")
    contracts.save_certificate(ctx.db, x["contract_id"], ctx.body, xid)
    return ok()


@route("POST", "/api/sub-certificates/<xid>/<action>", "contracts.certificates", "edit")
def cert_action(ctx, xid, action):
    from . import contracts
    fn = {"approve": contracts.approve_certificate, "post": contracts.post_certificate, "reopen": contracts.reopen_certificate}.get(action)
    if not fn:
        raise acc.ValidationError("Unknown action")
    r = fn(ctx.db, xid, _u(ctx))
    ctx.log("certificate." + action, xid)
    return ok({"entry_id": r} if action == "post" else {})


@route("DELETE", "/api/sub-certificates/<xid>", "contracts.certificates", "edit")
def cert_del(ctx, xid):
    from . import contracts
    contracts.delete_certificate(ctx.db, xid)
    return ok()


@route("POST", "/api/contracts/<cid>/pay", "contracts.certificates", "add")
def con_pay(ctx, cid):
    from . import contracts
    if not ctx.can("purchasing.payments", "add") and ctx.body.get("kind") != "release":
        raise PermissionError("Paying needs 'add' on Purchasing → Payments")
    eid = contracts.pay(ctx.db, cid, ctx.body, _u(ctx))
    ctx.log("contract.pay", f"{cid} {ctx.body.get('kind')}")
    return ok({"entry_id": eid})


# purchase requests & quotation comparison
@route("GET", "/api/purchase-requests", "purchasing.requests")
def prq_list(ctx):
    from . import procurement
    return ok(procurement.listing(ctx.db, ctx.q.get("status"), ctx.q.get("bu_id"), ctx.q.get("q")))


@route("GET", "/api/purchase-requests/<pid>", "purchasing.requests")
def prq_get(ctx, pid):
    from . import procurement
    return ok(procurement.get(ctx.db, pid))


@route("POST", "/api/purchase-requests", "purchasing.requests", "add")
def prq_add(ctx):
    from . import procurement
    pid = procurement.save(ctx.db, ctx.body, _u(ctx))
    if ctx.body.get("submit"):
        procurement.set_status(ctx.db, pid, "submitted", _u(ctx))
    return ok({"id": pid})


@route("PUT", "/api/purchase-requests/<pid>", "purchasing.requests", "add")
def prq_edit(ctx, pid):
    from . import procurement
    procurement.save(ctx.db, ctx.body, _u(ctx), pid)
    return ok()


@route("POST", "/api/purchase-requests/<pid>/status", "purchasing.requests", "add")
def prq_status(ctx, pid):
    from . import procurement
    st = ctx.body.get("status")
    if st in ("approved", "rejected") and not ctx.can("purchasing.requests", "edit"):
        raise PermissionError("Approving needs 'edit' on Purchase requests")
    procurement.set_status(ctx.db, pid, st, _u(ctx))
    ctx.log("pr." + str(st), pid)
    return ok()


@route("POST", "/api/purchase-requests/<pid>/quotes", "purchasing.requests", "add")
def prq_quote_add(ctx, pid):
    from . import procurement
    return ok({"id": procurement.save_quote(ctx.db, pid, ctx.body)})


@route("PUT", "/api/purchase-requests/<pid>/quotes/<qid>", "purchasing.requests", "add")
def prq_quote_edit(ctx, pid, qid):
    from . import procurement
    procurement.save_quote(ctx.db, pid, ctx.body, qid)
    return ok()


@route("DELETE", "/api/purchase-requests/<pid>/quotes/<qid>", "purchasing.requests", "add")
def prq_quote_del(ctx, pid, qid):
    from . import procurement
    procurement.delete_quote(ctx.db, qid)
    return ok()


@route("POST", "/api/purchase-requests/<pid>/award", "purchasing.requests", "edit")
def prq_award(ctx, pid):
    from . import procurement
    if not ctx.can("purchasing.orders", "add"):
        raise PermissionError("Raising orders needs 'add' on Purchase orders")
    codes = procurement.award(ctx.db, pid, ctx.body, _u(ctx))
    ctx.log("pr.award", f"{pid} → {', '.join(codes)}")
    return ok({"orders": codes})


@route("DELETE", "/api/purchase-requests/<pid>", "purchasing.requests", "edit")
def prq_del(ctx, pid):
    from . import procurement
    pr = procurement.get(ctx.db, pid)
    if pr["status"] == "ordered":
        raise acc.ValidationError("Orders have been raised from this request. Cancel those orders instead.")
    ctx.db.x("DELETE FROM purchase_requests WHERE id=?", (pr["id"],))
    return ok()


# stocktakes
@route("GET", "/api/stock-counts", "stock.counts")
def sc_list(ctx):
    from . import stocktake
    return ok(stocktake.listing(ctx.db, ctx.q.get("status")))


@route("GET", "/api/stock-counts/<cid>", "stock.counts")
def sc_get(ctx, cid):
    from . import stocktake
    return ok(stocktake.get(ctx.db, cid))


@route("POST", "/api/stock-counts", "stock.counts", "add")
def sc_start(ctx):
    from . import stocktake
    return ok({"id": stocktake.start(ctx.db, ctx.body, _u(ctx))})


@route("PUT", "/api/stock-counts/<cid>", "stock.counts", "add")
def sc_save(ctx, cid):
    from . import stocktake
    stocktake.save_counts(ctx.db, cid, ctx.body)
    return ok(stocktake.get(ctx.db, cid))


@route("POST", "/api/stock-counts/<cid>/post", "stock.counts", "edit")
def sc_post(ctx, cid):
    from . import stocktake
    n = stocktake.post(ctx.db, cid, _u(ctx))
    ctx.log("stockcount.post", f"{cid}: {n} adjustments")
    return ok({"adjustments": n})


@route("POST", "/api/stock-counts/<cid>/cancel", "stock.counts", "edit")
def sc_cancel(ctx, cid):
    from . import stocktake
    stocktake.cancel(ctx.db, cid)
    return ok()


@route("DELETE", "/api/stock-counts/<cid>", "stock.counts", "edit")
def sc_delete(ctx, cid):
    from . import stocktake
    c = stocktake.delete(ctx.db, cid)
    ctx.log("stockcount.delete", f"{c['code']} ({c['status']}, {c['warehouse']})")
    return ok()


# equipment
@route("GET", "/api/equipment", "equipment.register")
def eq_list(ctx):
    from . import equipment
    return ok(equipment.listing(ctx.db, ctx.q.get("status"), ctx.q.get("q")))


@route("GET", "/api/equipment-report", "equipment.usage")
def eq_report(ctx):
    from . import equipment
    return ok(equipment.report(ctx.db, ctx.q.get("date_from"), ctx.q.get("date_to")))


@route("GET", "/api/equipment/<eid>", "equipment.register")
def eq_get(ctx, eid):
    from . import equipment
    e = equipment.get(ctx.db, eid)
    e["usage"] = equipment.usage(ctx.db, e["id"])[:200]
    e["logs"] = equipment.logs(ctx.db, e["id"])
    return ok(e)


@route("POST", "/api/equipment", "equipment.register", "add")
def eq_add(ctx):
    from . import equipment
    return ok({"id": equipment.save(ctx.db, ctx.body)})


@route("PUT", "/api/equipment/<eid>", "equipment.register", "edit")
def eq_edit(ctx, eid):
    from . import equipment
    equipment.save(ctx.db, ctx.body, eid)
    return ok()


@route("DELETE", "/api/equipment/<eid>", "equipment.register", "edit")
def eq_del(ctx, eid):
    from . import equipment
    equipment.delete(ctx.db, eid)
    return ok()


@route("GET", "/api/equipment-usage", "equipment.usage")
def eq_usage(ctx):
    from . import equipment
    q = ctx.q
    return ok(equipment.usage(ctx.db, q.get("equipment_id"), q.get("bu_id"), q.get("date_from"), q.get("date_to")))


@route("POST", "/api/equipment-usage", "equipment.usage", "add")
def eq_usage_add(ctx):
    from . import equipment
    return ok({"id": equipment.log_usage(ctx.db, ctx.body, _u(ctx))})


@route("DELETE", "/api/equipment-usage/<uid>", "equipment.usage", "edit")
def eq_usage_del(ctx, uid):
    from . import equipment
    equipment.delete_usage(ctx.db, uid, _u(ctx))
    return ok()


@route("GET", "/api/maintenance", "equipment.maintenance")
def eq_maint(ctx):
    from . import equipment
    plans = []
    for e in equipment.listing(ctx.db):
        plans += [{**p, "code": e["code"], "name": e["name"]} for p in e["plans"]]
    return ok({"plans": plans, "logs": equipment.logs(ctx.db, ctx.q.get("equipment_id"))})


@route("POST", "/api/maintenance-plans", "equipment.maintenance", "add")
def eq_plan_add(ctx):
    from . import equipment
    return ok({"id": equipment.save_plan(ctx.db, ctx.body)})


@route("PUT", "/api/maintenance-plans/<pid>", "equipment.maintenance", "edit")
def eq_plan_edit(ctx, pid):
    from . import equipment
    equipment.save_plan(ctx.db, ctx.body, pid)
    return ok()


@route("DELETE", "/api/maintenance-plans/<pid>", "equipment.maintenance", "edit")
def eq_plan_del(ctx, pid):
    from . import equipment
    equipment.delete_plan(ctx.db, pid)
    return ok()


@route("POST", "/api/maintenance-logs", "equipment.maintenance", "add")
def eq_log_add(ctx):
    from . import equipment
    return ok({"id": equipment.log_maintenance(ctx.db, ctx.body, _u(ctx))})


@route("DELETE", "/api/maintenance-logs/<lid>", "equipment.maintenance", "edit")
def eq_log_del(ctx, lid):
    from . import equipment
    equipment.delete_log(ctx.db, lid, _u(ctx))
    return ok()


# attendance
@route("GET", "/api/attendance", "payroll.attendance")
def att_sheet(ctx):
    from . import labour
    return ok(labour.sheet(ctx.db, ctx.q.get("date"), ctx.q.get("bu_id")))


@route("POST", "/api/attendance", "payroll.attendance", "add")
def att_save(ctx):
    from . import labour
    if ctx.body.get("mark_all"):
        n = labour.mark_all(ctx.db, ctx.body.get("date"), ctx.body.get("bu_id"), ctx.body.get("status") or "present")
    else:
        n = labour.save_day(ctx.db, ctx.body, _u(ctx))
    return ok({"saved": n})


@route("DELETE", "/api/attendance/<eid>/<date>", "payroll.attendance", "edit")
def att_delete(ctx, eid, date):
    date = acc.clean_date(date)
    n = ctx.db.x("DELETE FROM attendance WHERE employee_id=? AND date=?", (int(eid), date)).rowcount
    if not n:
        raise acc.ValidationError("No attendance is recorded for this employee on this date.")
    ctx.log("attendance.delete", f"{eid} {date}")
    return ok()


@route("GET", "/api/attendance-summary", "payroll.attendance.summary")
def att_summary(ctx):
    from . import labour
    q = ctx.q
    today = acc.today()
    return ok(labour.summary(ctx.db, q.get("date_from") or today[:8] + "01", q.get("date_to") or today, q.get("bu_id")))


@route("GET", "/api/attendance-records", "payroll.attendance")
def att_records(ctx):
    from . import labour
    q = ctx.q
    return ok(labour.records(ctx.db, q.get("date_from"), q.get("date_to"), q.get("bu_id"), q.get("employee_id")))


@route("POST", "/api/payroll/runs/<rid>/from-attendance", "payroll.runs", "add")
def att_to_payroll(ctx, rid):
    from . import labour
    return ok({"lines": labour.fill_payroll(ctx.db, rid)})


# ETA e-receipts
@route("GET", "/api/ereceipts", "tax.ereceipt")
def er_list(ctx):
    from . import ereceipt
    q = ctx.q
    return ok(ereceipt.listing(ctx.db, q.get("status"), q.get("date_from"), q.get("date_to"), q.get("q")))


@route("GET", "/api/ereceipts/<rid>", "tax.ereceipt")
def er_get(ctx, rid):
    from . import ereceipt
    r = ereceipt.get(ctx.db, rid)
    if r["status"] == "draft":
        r["preview"], r["problems"], _ = ereceipt.build(ctx.db, r, "")
    return ok(r)


@route("POST", "/api/ereceipts", "tax.ereceipt", "add")
def er_add(ctx):
    from . import ereceipt
    rid = ereceipt.save(ctx.db, ctx.body)
    out = {"id": rid}
    if ctx.body.get("issue"):
        out.update(ereceipt.issue(ctx.db, rid, _u(ctx)))
    return ok(out)


@route("PUT", "/api/ereceipts/<rid>", "tax.ereceipt", "add")
def er_edit(ctx, rid):
    from . import ereceipt
    ereceipt.save(ctx.db, ctx.body, rid)
    return ok()


@route("POST", "/api/ereceipts/<rid>/issue", "tax.ereceipt", "add")
def er_issue(ctx, rid):
    from . import ereceipt
    r = ereceipt.issue(ctx.db, rid, _u(ctx))
    ctx.log("ereceipt.issue", rid)
    return ok(r)


@route("DELETE", "/api/ereceipts/<rid>", "tax.ereceipt", "edit")
def er_del(ctx, rid):
    from . import ereceipt
    ereceipt.delete(ctx.db, rid)
    return ok()


@route("GET", "/api/ereceipts-chain", "tax.ereceipt")
def er_chain(ctx):
    from . import ereceipt
    return ok(ereceipt.verify_chain(ctx.db))


@route("GET", "/api/ereceipts-export", "tax.ereceipt")
def er_export(ctx):
    from . import ereceipt
    ids = [int(x) for x in (ctx.q.get("ids") or "").split(",") if x.strip().isdigit()]
    data = json.dumps(ereceipt.export_json(ctx.db, ids or None), ensure_ascii=False, indent=1).encode("utf-8")
    return download(data, f"e-receipts-{acc.today()}.json", "application/json")


@route("POST", "/api/ereceipts-submit", "tax.ereceipt", "edit")
def er_submit(ctx):
    from . import ereceipt
    return ok(ereceipt.submit(ctx.db, ctx.body.get("ids") or None, _u(ctx)))


# ================================================================== bulk actions (erp/bulk.py)
@route("POST", "/api/bulk")
def bulk_action(ctx):
    """Edit / void / reverse / delete many records; each one checked as if done by hand."""
    from . import bulk
    return ok(bulk.run(ctx, ctx.body))


# =============================================================== dashboard
@route("GET", "/api/dashboard/view")
def dashboard_view(ctx):
    """The signed-in person's dashboard (their group's), with each widget's figures. Super administrators may pass
    ?group= to see — and customise — another group's dashboard."""
    from . import dashboards
    r = dashboards.view(ctx.db, ctx.user, ctx.q, ctx.can, ctx.q.get("group") or None)
    r["designer"] = dashboards.designer(ctx.user, ctx.can)
    if r["designer"]:
        r["groups"] = [{k: g[k] for k in ("key", "name", "name_ar", "users")} for g in dashboards.groups(ctx.db)]
        r["catalog"] = dashboards.catalog()
        r["cats"] = dashboards.CATS
    return ok(r)


@route("GET", "/api/dashboard/groups")
def dashboard_groups(ctx):
    """The groups (to put people in when they are created or edited)."""
    from . import dashboards
    if not (ctx.user.get("is_admin") or ctx.can("users.users") or dashboards.designer(ctx.user, ctx.can)):
        raise PermissionError()
    return ok({"groups": [{k: g[k] for k in ("key", "name", "name_ar", "users")} for g in dashboards.groups(ctx.db)]})


def _need_super_dash(ctx):
    from . import dashboards
    if not dashboards.designer(ctx.user, ctx.can):
        raise PermissionError("Only a super administrator, or a user authorised to design dashboards, can change dashboards.")


@route("POST", "/api/dashboard/groups")
def dashboard_group_add(ctx):
    from . import dashboards
    _need_super_dash(ctx)
    name = acc.text(ctx.body.get("name"), "name", required=True)
    key = dashboards.save_group(ctx.db, dashboards.new_key(ctx.db, name), {**ctx.body, "name": name})
    ctx.log("dashboard.group.add", key)
    return ok({"key": key})


@route("PUT", "/api/dashboard/groups/<key>")
def dashboard_group_save(ctx, key):
    from . import dashboards
    _need_super_dash(ctx)
    if not ctx.db.one("SELECT 1 FROM user_groups WHERE key=?", (key,)):
        raise acc.ValidationError("Unknown group")
    dashboards.save_group(ctx.db, key, ctx.body)
    ctx.log("dashboard.group.save", key)
    return ok()


@route("DELETE", "/api/dashboard/groups/<key>")
def dashboard_group_del(ctx, key):
    from . import dashboards
    _need_super_dash(ctx)
    dashboards.delete_group(ctx.db, key)
    ctx.log("dashboard.group.delete", key)
    return ok()


@route("GET", "/api/dashboard", "dashboard")
def dashboard(ctx):
    q = ctx.q
    d = acc.dashboard(ctx.db, q.get("date_from"), q.get("date_to"), q.get("bu_id") or None)
    if ctx.can("accounting.recurring"):
        due = recurring.due_list(ctx.db, acc.today(), lambda m: ctx.can(m, "add"))
        d["recurring_due"] = {"review": sum(1 for x in due if x["mode"] == "review"), "auto": sum(1 for x in due if x["mode"] == "auto"),
                              "errors": sum(1 for x in due if x["last_error"])} if due else None
    if ctx.can("shop.installments") or ctx.can("shop.orders"):
        d["installments_overdue"] = sales.overdue_summary(ctx.db)
        d["cheques_due"] = len(sales.cheques_due(ctx.db))
    if ctx.can("rentals.contracts"):
        d["rentals"] = rentals.ongoing(ctx.db)
    if ctx.can("assets.depreciation", "add"):
        d["depreciation_due"] = assets.due(ctx.db)
        if d["depreciation_due"] is None and ctx.can("rentals.fleet", "edit") and assets.policy(ctx.db)["remind"]:
            last = assets._add_months(acc.today()[:7], -1)
            rp = rentals.plan_depreciation(ctx.db, last)
            d["depreciation_due"] = {"through": last, "count": len(rp["rows"]), "total": rp["total"], "rental": True} if rp["rows"] else None
    return ok(d)


# ================================================================== reports
@route("GET", "/api/reports/<name>")
def report(ctx, name):
    if name == "vouchers":
        table = _vouchers_report(ctx)
    else:
        perm = reports.REPORT_PERMS.get(name, "reports")
        if isinstance(perm, tuple):
            if not any(ctx.can(x) for x in perm):
                raise PermissionError()
        else:
            ctx.require(perm, "view")
        table = reports.build(ctx.db, name, ctx.q)
    # texts the app wrote (memos, kinds) in the report's language
    table = textl10n.table(table, "ar" if (ctx.q.get("lang") or ctx.lang) == "ar" else "en")
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
    lang = "ar" if ctx.q.get("lang") == "ar" else "en"
    if ctx.q.get("files") == "1":     # the workbook plus every attached file, in month folders, as one .zip
        return download(interchange.workbook_zip(ctx.db, lang, secrets=_with_secrets(ctx)), f"BEYOND_ERP_{acc.today()}{'_AR' if lang == 'ar' else ''}_with_files.zip",
                        "application/zip")
    data = workbook.build_workbook(ctx.db, template=False, lang=lang, secrets=_with_secrets(ctx))
    return download(data, f"BEYOND_ERP_{acc.today()}{'_AR' if lang == 'ar' else ''}.xlsx", XLSX)


@route("GET", "/api/templates/workbook", "import_export")
def template_workbook(ctx):
    """The empty workbook — for this company, or (?type=scaffolding…) set up for a business type."""
    from . import workbook
    kind = ctx.q.get("type")
    lang = "ar" if ctx.q.get("lang") == "ar" else "en"
    if kind:
        db2 = Database(":memory:")
        ident = branding.template_identity(ctx.db)
        keys = ("company_name", "company_tagline", "currency", "company_address", "company_tax_id") if ident["own"] else ("currency",)
        for k in keys:
            if ctx.db.setting(k):
                db2.set_setting(k, ctx.db.setting(k))
        industries.apply(db2, kind)
        return download(workbook.build_workbook(db2, template=True, hide=industries.TYPES[kind]["hide"], lang=lang, identity=ident),
                        f"ERP_Template_{kind}{'_AR' if lang == 'ar' else ''}.xlsx", XLSX)
    return download(workbook.build_workbook(ctx.db, template=True, lang=lang), f"BEYOND_ERP_Import_Template{'_AR' if lang == 'ar' else ''}.xlsx", XLSX)


@route("GET", "/api/industries", "settings")
def industries_list(ctx):
    return ok({"types": industries.catalogue(), "current": ctx.db.setting("business_type") or ""})


@route("POST", "/api/industries/apply", "settings.menus", "edit")
def industries_apply(ctx):
    r = industries.apply(ctx.db, ctx.body.get("type"), ctx.body.get("hide_modules", True) not in (False, 0, "0", "false"))
    ctx.log("industry.apply", r["type"])
    return ok(r)


def _with_secrets(ctx):
    """A super administrator's full backup carries the keys of connected tools too (unless they untick it)."""
    return bool(ctx.user.get("is_super")) and ctx.q.get("secrets") != "0"


@route("GET", "/api/export/json", "import_export.export")
def export_json(ctx):
    return download(interchange.to_json(ctx.db, secrets=_with_secrets(ctx)).encode("utf-8"), f"BEYOND_ERP_{acc.today()}.json",
                    "application/json; charset=utf-8")


@route("GET", "/api/export/csv", "import_export")
def export_csv(ctx):
    sheet = ctx.q.get("sheet")
    if sheet not in interchange.SHEET_BY_NAME:
        raise acc.ValidationError("Unknown sheet")
    tpl = ctx.q.get("template") == "1"
    return download(interchange.to_csv(ctx.db, sheet, template=tpl),
                    re.sub(r"\W+", "_", sheet) + ("_template" if tpl else "") + ".csv", "text/csv; charset=utf-8")


# ------------------------------------------------- per-screen templates & imports
def _sheet_can(ctx, name, level="add"):
    from . import importkit
    importkit.sheet(name)
    perm = importkit.SHEET_PERM.get(name)
    if not (ctx.can("import_export.import", "edit") or (perm and ctx.can(perm, level))):
        raise PermissionError()
    if perm and not auth.action_ok(ctx.user, perm, "import"):
        raise PermissionError(ACTION_DENIED["import"])


@route("GET", "/api/import-sheets")
def import_sheets(ctx):
    from . import importkit
    return ok([{**importkit.describe(n), "perm": importkit.SHEET_PERM.get(n, "")} for n in importkit.importable()])


@route("GET", "/api/templates/sheet")
def template_sheet(ctx):
    from . import importkit
    name = ctx.q.get("sheet") or ""
    _sheet_can(ctx, name, "view")
    demo_rows = None
    if ctx.q.get("demo") == "1":       # the layout filled with the demo company, ready to import
        from . import demo
        from .db import Database
        ind = ctx.q.get("industry") if ctx.q.get("industry") in demo.INDUSTRY_KEYS else None
        lang = "ar" if ctx.q.get("lang") == "ar" else "en"
        demo_rows = (demo.all_sheets(lambda: Database(":memory:"), lang) if name == "Bank Statement Lines"
                     else demo.translate_sheets(demo.sheets(ind), lang)).get(name) or []
    fname = re.sub(r"\W+", "_", name).strip("_") + ("_demo" if demo_rows is not None else "_template")
    if (ctx.q.get("format") or "xlsx") == "csv":
        return download(importkit.template_csv(name, demo_rows), fname + ".csv", "text/csv; charset=utf-8")
    return download(importkit.template_xlsx(ctx.db, name, ctx.q.get("lang") or "en", demo_rows), fname + ".xlsx", XLSX)


@route("POST", "/api/onboarding")
def onboarding_done(ctx):
    """The new company's first steps are finished (optionally with a business type set up)."""
    if not ctx.user["is_admin"]:
        raise PermissionError()
    kind = ctx.body.get("business_type")
    if kind:
        industries.apply(ctx.db, kind, True)
    ctx.db.set_setting("onboarding", "done")
    return ok()


@route("GET", "/api/demo")
def demo_info(ctx):
    from . import demo
    return ok({"empty": demo.is_empty(ctx.db), "loaded": ctx.db.setting(demo.TAG) or "", "industries": demo.INDUSTRY_KEYS,
               "names": {k: p["name"] for k, p in demo.PROFILES.items()}, "current": ctx.db.setting("business_type") or ""})


@route("POST", "/api/demo")
def demo_load(ctx):
    """Administrator only: fill the books with the demo company (replace = erase everything first, after a backup)."""
    from . import demo
    if not ctx.user["is_admin"]:
        raise PermissionError()
    # demo data only ever goes into empty books, and only when the person confirmed the warning: a mis-click can never
    # mix invented numbers into real books (to try the demo on books in use: back up, erase in Settings → System, then load)
    if ctx.body.get("replace") in (True, 1, "1", "true"):
        raise acc.ValidationError("The demo company can only be loaded into empty books. Back up, erase the data in "
                                  "Settings → System, then load the demo.")
    if not demo.is_empty(ctx.db):
        raise acc.ValidationError("These books already contain data. The demo company can only be loaded into empty books.")
    if ctx.body.get("confirmed") is not True:
        raise acc.ValidationError("Confirm the warning to load the demo company")
    # with a business type: that type is set up first (its accounts, menus, payroll lists), then only its kind of data
    kind = ctx.body.get("industry") if ctx.body.get("industry") in demo.INDUSTRY_KEYS else None
    if kind:
        industries.apply(ctx.db, kind, True)
    lang = ctx.body.get("lang") if ctx.body.get("lang") in ("en", "ar") else (ctx.db.setting("language") or "en")
    r = demo.load(ctx.db, ctx.user["username"], industry=kind, lang=lang)    # the same data, in the person's language
    ctx.db.set_setting("onboarding", "done")
    ctx.log("demo.load", json.dumps(r["counts"])[:300])
    return ok(r)


@route("GET", "/api/demo/workbook", "import_export")
def demo_workbook(ctx):
    from . import demo
    from .db import Database
    lang = "ar" if ctx.q.get("lang") == "ar" else "en"
    return download(demo.workbook_bytes(lambda: Database(":memory:"), lang), "BEYOND_ERP_Demo_Company.xlsx", XLSX)


@route("POST", "/api/import-preview/analyze")
def import_analyze(ctx):
    from . import importkit
    name = ctx.q.get("sheet") or ""
    _sheet_can(ctx, name)
    return ok(importkit.analyze(ctx.db, name, ctx.raw, ctx.q.get("filename") or "", ctx.q.get("table") or None,
                                ctx.q.get("header_row") if ctx.q.get("header_row") not in (None, "") else None))


def _sheet_import(ctx, dry):
    from . import importkit
    name = ctx.q.get("sheet") or ""
    _sheet_can(ctx, name)
    mapping = json.loads(ctx.q.get("mapping") or "null")
    rows, nums = importkit.read(ctx.db, name, ctx.raw, ctx.q.get("filename") or "", mapping, ctx.q.get("table") or None,
                          ctx.q.get("header_row") if ctx.q.get("header_row") not in (None, "") else None)
    try:
        res = interchange.import_data(ctx.db, {name: rows}, mode="new" if ctx.q.get("only_new") in ("1", "true") else "append",
                                      dry_run=dry, user=ctx.user["username"])
    except interchange.ImportErrors as e:
        from .interchange import FIRST_DATA_ROW
        errs = []
        for x in e.errors:      # row numbers as they are in the user's own file
            i = x.get("row", 0) - FIRST_DATA_ROW
            errs.append({**x, "row": nums[i] if 0 <= i < len(nums) else x.get("row"), "values": rows[i] if 0 <= i < len(rows) else {}})
        return ok({"ok": False, "error": str(e), "errors": errs, "rows": len(rows)}, 400)
    res["rows"] = len(rows)
    if not dry:
        ctx.log("import.sheet", f"{name}: {json.dumps(res['counts'])}")
    return ok(res)


@route("POST", "/api/import-preview/check")
def import_check(ctx):
    return _sheet_import(ctx, True)


@route("POST", "/api/import-sheet")
def import_sheet(ctx):
    return _sheet_import(ctx, False)


def _future_dated(data):
    """Rows booked with a date after today: imported as they are, but listed — a month or year typed wrong leaves them
    out of every 'as of today' balance and aging, which then looks half-settled or reversed."""
    today, out = acc.today(), []
    for sheet, rows in data.items():
        if sheet.startswith("_") or sheet in ("BOQ Lines", "Offers", "Offer Lines", "Budgets", "Business Units") or not isinstance(rows, list):
            continue
        refs = []
        for r in rows:
            d = str((r or {}).get("date") or "")[:10] if isinstance(r, dict) else ""
            if len(d) == 10 and d > today:
                ref = str(r.get("voucher") or r.get("number") or r.get("ref") or d)
                if (ref, d) not in refs:
                    refs.append((ref, d))
        if refs:
            shown = ", ".join(f"{a} ({b})" if a != b else a for a, b in refs[:8]) + (" …" if len(refs) > 8 else "")
            out.append(f"{sheet}: {len(refs)} dated after today ({today}) — {shown}. They are imported as dated, so balances and "
                       "aging as of today leave them out; if a date is a typo, fix it in the file and import again.")
    return out


@route("POST", "/api/import", "import_export.import", "edit")
def do_import(ctx):
    fmt = ctx.q.get("format") or "xlsx"
    raw = ctx.raw
    if not raw:
        raise acc.ValidationError("Upload a file")
    mode = ctx.q.get("mode") or "append"
    dry = ctx.q.get("dry_run") == "1"
    if mode == "replace":
        _need_super(ctx, "replace all the books with an import")
    if mode == "replace" and not dry:
        ctx.server.backup("before-import")
    if fmt == "json":
        data = interchange.read_json(raw)
    elif fmt == "sqlite" or raw[:16] == b"SQLite format 3\x00":     # a "Database backup file"
        if not ctx.user.get("is_super"):
            raise PermissionError("Only a super administrator can restore a database backup file")
        data = interchange.read_sqlite(raw)
    elif fmt == "csv":
        data = interchange.read_csv(ctx.q.get("sheet"), raw)
    elif fmt in ("xlsx", "legacy", "zip") and interchange.is_files_zip(raw):   # a workbook with its files (export "with files")
        data = interchange.read_files_zip(raw, interchange.read_xlsx)
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
        raise acc.ValidationError("format must be xlsx, zip, json, csv, sqlite or legacy")
    if not any(data.values()):
        raise acc.ValidationError("No recognisable sheets or rows found in the file")
    try:
        if not ctx.user.get("is_super") and isinstance(data.get("_extras"), dict):
            data["_extras"].pop("secrets", None)      # keys of connected tools: only a super administrator brings them in
        res = interchange.import_data(ctx.db, data, mode=mode, dry_run=dry, user=ctx.user["username"])
    except interchange.ImportErrors as e:
        return ok({"ok": False, "error": str(e), "errors": e.errors}, 400)
    res["notes"] = res.get("notes", []) + data.get("_notes", []) + _future_dated(data)
    if not dry:
        ctx.db.set_setting("onboarding", "done")
    ctx.log("import" + (".dry" if dry else ""), json.dumps(res["counts"]))
    return ok(res)


# ==================================================================== users
@route("GET", "/api/users", "users.users")
def users(ctx):
    return ok([auth.public_user(u) for u in ctx.db.q("SELECT * FROM users ORDER BY id")])


class SuperOnly(PermissionError):
    pass


def _licence_seat(ctx):
    """Adding / re-activating a user: within the users the licence covers."""
    if not getattr(ctx.app, "licensing", False):
        return
    from . import licence
    st = licence.status(ctx.db)
    if st["state"] == "licensed" and st["users"] >= int(st["licence"].get("users") or 1):
        raise acc.ValidationError(f"The licence covers {st['licence'].get('users')} active users. Deactivate a user or purchase additional users.")


def _need_super(ctx, what):
    """Super administrators only: administrators, erasing and replacing the books."""
    if not ctx.user.get("is_super"):
        raise SuperOnly(f"Only a super administrator can {what}")


@route("POST", "/api/users", "users.users", "add")
def user_add(ctx):
    if not ctx.user.get("is_super"):
        ctx.body.pop("grants", None)          # special rights: given by a super administrator only
    if ctx.body.get("is_admin") or ctx.body.get("is_super"):
        _need_super(ctx, "create administrators")
    _licence_seat(ctx)
    uid = auth.create_user(ctx.db, ctx.body)
    ctx.log("user.add", ctx.body.get("username"))
    return ok({"id": uid})


@route("PUT", "/api/users/<uid>", "users.users", "edit")
def user_edit(ctx, uid):
    target = ctx.db.one("SELECT is_admin, is_super FROM users WHERE id=?", (int(uid),))
    if not target:
        raise acc.ValidationError("User not found")
    b = ctx.body
    if int(uid) != ctx.user["id"]:
        _need_super(ctx, "edit other people's accounts (administrators may add users)")
    elif not ctx.user.get("is_super"):
        b.pop("needs_approval", None)     # nobody lifts their own approval requirement
    if not ctx.user.get("is_super"):
        b.pop("grants", None)             # special rights: given by a super administrator only
        b.pop("projects", None)           # which projects someone sees: set by a super administrator
        b.pop("perms", None)
    raises = bool(b.get("is_admin")) and not target["is_admin"] or bool(b.get("is_super")) and not target["is_super"]
    lowers = ("is_admin" in b and not b["is_admin"] and target["is_admin"]) or ("is_super" in b and not b["is_super"] and target["is_super"])
    if raises or ((target["is_admin"] or target["is_super"]) and int(uid) != ctx.user["id"]) or (lowers and int(uid) != ctx.user["id"]):
        _need_super(ctx, "change administrators")   # an administrator may still edit their own name, email, password
    if ctx.body.get("active") and not ctx.db.one("SELECT active FROM users WHERE id=?", (int(uid),))["active"]:
        _licence_seat(ctx)
    auth.update_user(ctx.db, int(uid), ctx.body, ctx.user["id"])
    ctx.log("user.edit", uid)
    return ok()


@route("DELETE", "/api/users/<uid>", "users.users", "edit")
def user_del(ctx, uid):
    _need_super(ctx, "remove users")
    u = auth.delete_user(ctx.db, int(uid), ctx.user["id"])
    ctx.log("user.delete", u["username"])
    return ok({"username": u["username"], "email": u.get("email") or ""})


@route("PUT", "/api/me/password")
def my_password(ctx):
    u = ctx.db.one("SELECT * FROM users WHERE id=?", (ctx.user["id"],))
    if not auth.check_password(ctx.body.get("current", ""), u["pw_hash"]):
        raise acc.ValidationError("Current password is wrong")
    ctx.db.x("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_password(ctx.body.get("new", "")), u["id"]))
    # every other device signed in as this person is signed out
    ctx.db.x("DELETE FROM sessions WHERE user_id=? AND token<>?", (u["id"], ctx.token or ""))
    return ok()


# ================================================================ people, profiles and chat (erp/chat.py)
@route("GET", "/api/me/profile")
def my_profile(ctx):
    from . import chat
    return ok(chat.profile(ctx.db, ctx.user["id"]))


@route("PUT", "/api/me/profile")
def my_profile_save(ctx):
    from . import chat
    return ok(chat.save_profile(ctx.db, ctx.user["id"], ctx.body))


@route("GET", "/api/people")
def people_list(ctx):
    from . import chat
    return ok(chat.people(ctx.db))


@route("GET", "/api/people/<uid>")
def person(ctx, uid):
    from . import chat
    p = next((x for x in chat.people(ctx.db) if x["id"] == int(uid)), None)
    if not p:
        raise LookupError("No such person")
    prof = chat.profile(ctx.db, p["id"])
    return ok({**p, "bio": prof["bio"], "phone": prof["phone"]})


@route("GET", "/api/chat/rooms")
def chat_rooms(ctx):
    from . import chat
    return ok({"rooms": chat.rooms(ctx.db, ctx.user), "max_mb": chat.MAX_MESSAGE_FILES_MB})


@route("POST", "/api/chat/rooms")
def chat_room_new(ctx):
    from . import chat
    rid = chat.create_room(ctx.db, ctx.user, ctx.body)
    ctx.log("chat.room.create", ctx.body.get("name", ""))
    return ok({"id": rid})


@route("PUT", "/api/chat/rooms/<rid>")
def chat_room_edit(ctx, rid):
    from . import chat
    chat.update_room(ctx.db, ctx.user, rid, ctx.body)
    ctx.log("chat.room.edit", rid)
    return ok({"id": int(rid)})


@route("POST", "/api/chat/rooms/<rid>/archive")
def chat_room_archive(ctx, rid):
    from . import chat
    chat.archive_room(ctx.db, ctx.user, rid, ctx.body.get("archived", True) not in (False, 0, "0", "false"))
    ctx.log("chat.room.archive", rid)
    return ok()


@route("DELETE", "/api/chat/rooms/<rid>")
def chat_room_delete(ctx, rid):
    from . import chat
    r = chat.delete_room(ctx.db, ctx.user, rid)
    ctx.log("chat.room.delete", r["name"])
    return ok()


@route("POST", "/api/chat/dm")
def chat_dm(ctx):
    from . import chat
    return ok({"id": chat.direct(ctx.db, ctx.user, ctx.body.get("user_id") or 0)})


@route("GET", "/api/chat/rooms/<rid>/messages")
def chat_messages(ctx, rid):
    from . import chat
    return ok(chat.messages(ctx.db, ctx.user, rid, ctx.q.get("after"), ctx.q.get("before"), ctx.q.get("limit")))


@route("POST", "/api/chat/rooms/<rid>/messages")
def chat_send(ctx, rid):
    from . import chat
    return ok({"id": chat.send(ctx.db, ctx.user, rid, ctx.body)})


@route("POST", "/api/chat/rooms/<rid>/read")
def chat_read(ctx, rid):
    from . import chat
    chat._need_read(ctx.db, ctx.user, rid)
    chat.mark_read(ctx.db, ctx.user, rid, ctx.body.get("last"))
    return ok()


@route("PUT", "/api/chat/messages/<mid>")
def chat_edit(ctx, mid):
    from . import chat
    chat.edit(ctx.db, ctx.user, mid, ctx.body)
    return ok()


@route("DELETE", "/api/chat/messages/<mid>")
def chat_remove(ctx, mid):
    from . import chat
    chat.remove(ctx.db, ctx.user, mid)
    return ok()


@route("POST", "/api/chat/messages/<mid>/react")
def chat_react(ctx, mid):
    from . import chat
    emoji = chat.react(ctx.db, ctx.user, mid, ctx.body.get("emoji"))
    m = ctx.db.one("SELECT m.room_id, m.body, u.username FROM chat_messages m JOIN users u ON u.id=m.user_id WHERE m.id=?", (int(mid),))
    to = [m["username"]] if emoji and m and m["username"] != ctx.user["username"] else []
    return ok(push_hint({"emoji": emoji}, {"kind": "notify", "to": to, "title": f"{ctx.user['username']} reacted {emoji}",
                                           "body": (m["body"] or "")[:120] if m else "", "href": f"#/chat/{m['room_id']}" if m else "#/chat",
                                           "tag": f"rx{mid}", "pref": "reactions"}))


@route("GET", "/api/chat/files/<fid>")
def chat_file(ctx, fid):
    from . import chat
    name, mime, data = chat.file(ctx.db, ctx.user, fid)
    return download(data, name, mime)


@route("GET", "/api/chat/updates")
def chat_updates(ctx):
    from . import chat
    return ok(chat.updates(ctx.db, ctx.user, ctx.q.get("since"), ctx.q.get("rsince")))


@route("GET", "/api/chat/rooms/<rid>/export")
def chat_export(ctx, rid):
    from . import chat
    data, name = chat.export_zip(ctx.db, ctx.user, rid, ctx.db.setting("company_name") or "")
    ctx.log("chat.export", rid)
    return download(data, name, "application/zip")


# ================================================================ AI assistant (erp/ai.py)
@route("GET", "/api/ai-policy")
def ai_policy_get(ctx):
    from . import ai
    p = ai.policy(ctx.db)
    out = {"policy": p if ctx.user.get("is_super") else {k: p[k] for k in ("enabled", "name", "avatar", "write", "model", "daily_limit")},
           "allowed": ai.allowed(p, ctx.user), "models": [[m, textl10n.text(lbl, ctx.lang)] for m, lbl in ai.MODELS], "providers": ai.PROVIDERS, "used_today": ai.used_today(ctx.db, ctx.user),
           "has_key": ai.has_key(ctx.db)}
    return ok(out)


@route("PUT", "/api/ai-policy")
def ai_policy_put(ctx):
    from . import ai
    p = ai.save_policy(ctx.db, ctx.user, ctx.body)
    ctx.log("ai.policy", "on" if p["enabled"] else "off")
    return ok(p)


@route("PUT", "/api/ai/key")
def ai_key_put(ctx):
    from . import ai
    ai.key_set(ctx.db, ctx.user, ctx.body.get("key"))
    ctx.log("ai.key", "set" if ctx.body.get("key") else "removed")
    return ok({"has_key": ai.has_key(ctx.db)})


@route("GET", "/api/ai/context")
def ai_context(ctx):
    from . import ai
    p = ai.policy(ctx.db)
    if not ai.allowed(p, ctx.user):
        raise PermissionError("The AI assistant is not enabled for you.")
    return ok({"base": ai.base_prompt(p), "context": ai.context(ctx.db, ctx.user), "tools": ai.tools(p),
               "write": p["write"], "name": p["name"]})


@route("GET", "/api/ai/catalog")
def ai_catalog(ctx):
    from . import ai
    return ok(ai.catalog(ROUTES, ctx.user, ctx.q.get("q"), ctx.q.get("limit")))


@route("POST", "/api/ai/complete")
def ai_complete(ctx):
    from . import ai
    if getattr(ctx.app, "static", False):    # browser-only: the page calls the provider itself
        return ok(ai.prepare_for_browser(ctx.db, ctx.user, ctx.body))
    return ok({"message": ai.complete(ctx.db, ctx.user, ctx.body)})


@route("POST", "/api/ai/convert")
def ai_convert(ctx):
    """Browser-only edition with another provider: turn the provider's reply into the assistant's message shape."""
    from . import ai
    return ok({"message": ai.from_openai(ctx.body.get("response"))})


@route("POST", "/api/ai/extract")
def ai_extract(ctx):
    """An Excel / Word / PowerPoint file attached to the assistant, as text it can read."""
    from . import ai
    if not ai.allowed(ai.policy(ctx.db), ctx.user):
        raise PermissionError("The AI assistant is not enabled for you.")
    return ok(ai.extract_text(ctx.body.get("name"), ctx.body.get("data")))


@route("GET", "/api/ai-undo/<change>")
def ai_undo_info(ctx, change):
    from . import aiundo
    return ok(aiundo.info(ctx.db, change))


@route("POST", "/api/ai-undo/<change>")
def ai_undo(ctx, change):
    """Undo one of the assistant's saved changes (or a whole batch): every row back as it was, if nobody changed it since."""
    from . import aiundo
    r = aiundo.undo(ctx.db, change, ctx.user)
    ctx.log("ai.undo", change)
    return ok(r)


@route("POST", "/api/undo/<change>")
def own_undo(ctx, change):
    """Undo a save the person made a moment ago (the "Undo" after saving): every row back as it was, if nobody
    changed it since, within a short while, and never in closed books."""
    from . import aiundo
    if not re.fullmatch(r"u[\w-]{5,59}", change):
        raise acc.ValidationError("Unknown change")
    r = aiundo.undo(ctx.db, change, ctx.user, own=True)
    ctx.log("undo", change)
    return ok(r)


@route("POST", "/api/ai/preview")
def ai_preview(ctx):
    """Run a change the assistant proposes without saving it, and report what it would do (checks, numbers, entries)."""
    from . import ai
    if isinstance(ctx.body.get("changes"), list):          # a batch: checked together, approved (or saved) at once
        ch = []
        for c in ctx.body["changes"][:ai.MAX_BATCH + 1]:
            if not isinstance(c, dict):
                raise acc.ValidationError("Each change needs method, path and body")
            m, pth = str(c.get("method") or "").upper(), str(c.get("path") or "")
            ch.append({"method": m, "path": pth, "body": c.get("body"), "summary": acc.text(c.get("summary"), maxlen=300),
                       "module": route_module(m, urlparse(pth).path)})
        return ok(ai.preview_many(ctx.app, ctx.user, ch))
    method, path = str(ctx.body.get("method") or "").upper(), str(ctx.body.get("path") or "")
    return ok(ai.preview(ctx.app, ctx.user, method, path, ctx.body.get("body"), route_module(method, urlparse(path).path)))


@route("POST", "/api/ai/params")
def ai_params(ctx):
    """The request for the workspace's ai function: same policy, built here so the page cannot change it."""
    from . import ai
    p, params = ai.request_params(ctx.db, ctx.user, ctx.body)
    return ok({"params": params})


# ================================================================= forecasting
# ---------------------------------------------------------------- two-step sign-in (auth.py)
@route("GET", "/api/me/two-step")
def two_step_get(ctx):
    """A new setup key (not kept until a code from the app confirms it) and whether it is on."""
    u = ctx.db.one("SELECT totp_secret FROM users WHERE id=?", (ctx.user["id"],))
    secret = auth.new_totp_secret()
    return ok({"on": bool(u and u["totp_secret"]), "secret": secret, "uri": auth.totp_uri(secret, ctx.user["username"], ctx.db.setting("company_name") or "BeyondERP"),
               "required": ctx.db.setting("two_step_required") or ""})


@route("POST", "/api/me/two-step")
def two_step_on(ctx):
    codes = auth.totp_enable(ctx.db, ctx.user["id"], acc.text(ctx.body.get("secret")), ctx.body.get("code"))
    ctx.log("two-step.on", ctx.user["username"])
    return ok({"on": True, "recovery_codes": codes})


@route("POST", "/api/me/two-step/off")
def two_step_off(ctx):
    from .db import CLOCK
    u = ctx.db.one("SELECT * FROM users WHERE id=?", (ctx.user["id"],))
    if not u["totp_secret"]:
        return ok({"on": False})
    if (ctx.db.setting("two_step_required") == "all" or (ctx.db.setting("two_step_required") == "admins" and u["is_admin"])) and CLOCK["at"] is None:
        raise acc.ValidationError("Two-step sign-in is required for you in this company. A super administrator can change this.")
    if CLOCK["at"] is None and not (auth.totp_ok(u["totp_secret"], ctx.body.get("code")) or auth.check_password(ctx.body.get("password") or "", u["pw_hash"])):
        raise acc.ValidationError("Enter a code from your authenticator app, or your password, to disable it.")
    auth.totp_disable(ctx.db, u["id"])
    ctx.log("two-step.off", u["username"])
    return ok({"on": False})


@route("POST", "/api/users/<uid>/two-step/off", "users.users", "edit")
def two_step_reset(ctx, uid):
    """Someone lost their phone and their recovery codes: a super administrator turns it off so they can set it up again."""
    _need_super(ctx, "turn off someone's two-step sign-in")
    auth.totp_disable(ctx.db, int(uid))
    ctx.log("two-step.reset", uid)
    return ok({"on": False})


# ---------------------------------------------------------------- year-end close (erp/yearend.py)
@route("GET", "/api/year-end", "accounting.yearend")
def year_end_list(ctx):
    from . import yearend
    return ok({"years": yearend.years(ctx.db), "fiscal_year_start": ctx.db.setting("fiscal_year_start") or "01-01",
               "lock_date": ctx.db.setting("lock_date") or ""})


@route("GET", "/api/year-end/<end>", "accounting.yearend")
def year_end_get(ctx, end):
    from . import yearend
    return ok(yearend.preview(ctx.db, acc.clean_date(end), ctx.lang))


@route("POST", "/api/year-end/<end>/close", "accounting.yearend", "edit")
def year_end_close(ctx, end):
    from . import yearend
    r = yearend.close(ctx.db, acc.clean_date(end), ctx.user["username"], ctx.body.get("notes") or "")
    ctx.log("year.close", end)
    return ok(r)


@route("POST", "/api/year-end/<end>/reopen", "accounting.yearend", "edit")
def year_end_reopen(ctx, end):
    if not ctx.user.get("is_super"):
        raise PermissionError("Only a super administrator can reopen a closed year")
    from . import yearend
    r = yearend.reopen(ctx.db, acc.clean_date(end))
    ctx.log("year.reopen", end)
    return ok(r)


@route("GET", "/api/cash-forecast", "reports.cashplan")
def cash_forecast(ctx):
    from . import cashplan
    return ok(cashplan.build(ctx.db, ctx.q.get("days") or 90, ctx.q.get("overdue", "1") != "0"))


@route("GET", "/api/forecast", "reports.forecast")
def forecast_get(ctx):
    from . import forecast
    d = forecast.build(ctx.db)
    if isinstance(d, dict) and isinstance(d.get("sections"), list):   # each part of the forecast is a tab with its own permission
        d["sections"] = [x for x in d["sections"] if ctx.can(f"reports.forecast.{x.get('key')}")]
    return ok(d)


@route("GET", "/api/forecast/outlook", "reports.forecast")
def forecast_outlook(ctx):
    """Expected price movement for some items (?items=1,2) and cost accounts (?accounts=5010,6010)."""
    from . import forecast
    split = lambda k: [x for x in (ctx.q.get(k) or "").split(",") if x.strip()][:200]  # noqa: E731
    items = [i for i in split("items") if i.isdigit()]
    if str(ctx.q.get("entry") or "").isdigit():   # a voucher: the items it moved in, and the cost accounts it charged
        items += [str(r["item_id"]) for r in ctx.db.q("SELECT DISTINCT item_id FROM stock_moves WHERE entry_id=? AND qty>0", (int(ctx.q["entry"]),))]
    return ok(forecast.outlook(ctx.db, items, split("accounts")))


@route("PUT", "/api/forecast", "reports.forecast", "edit")
def forecast_put(ctx):
    from . import forecast
    forecast.save(ctx.db, ctx.body)
    ctx.log("forecast.assumptions", "")
    return ok(forecast.build(ctx.db))


# ================================================================= help & support
@route("GET", "/api/support/tickets")
def support_list(ctx):
    from . import support
    return ok(support.tickets(ctx.db, ctx.user))


@route("POST", "/api/support/tickets")
def support_new(ctx):
    from . import support
    t = support.create(ctx.db, ctx.user, ctx.body)
    ctx.log("support.ticket", f"{t['ref']} {t['kind']}: {t['subject'][:80]}")
    return ok(t)


@route("PUT", "/api/support/tickets/<tid>")
def support_status(ctx, tid):
    from . import support
    support.set_status(ctx.db, ctx.user, tid, ctx.body.get("status"))
    return ok()


@route("GET", "/api/ai/threads")
def ai_threads(ctx):
    from . import ai
    return ok(ai.threads(ctx.db, ctx.user))


@route("GET", "/api/ai/threads/<tid>")
def ai_thread(ctx, tid):
    from . import ai
    return ok(ai.thread(ctx.db, ctx.user, tid))


@route("POST", "/api/ai/threads")
def ai_thread_new(ctx):
    from . import ai
    return ok({"id": ai.save_thread(ctx.db, ctx.user, ctx.body)})


@route("PUT", "/api/ai/threads/<tid>")
def ai_thread_save(ctx, tid):
    from . import ai
    return ok({"id": ai.save_thread(ctx.db, ctx.user, ctx.body, tid)})


@route("DELETE", "/api/ai/threads/<tid>")
def ai_thread_del(ctx, tid):
    from . import ai
    ai.delete_thread(ctx.db, ctx.user, tid)
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


@route("POST", "/api/branding/default-logo", "settings.branding", "edit")
def branding_default_logo(ctx):
    """Back to the BeyondERP mark for the letterhead logo and its faded watermark."""
    from . import brand_assets
    for key, img in (("logo", brand_assets.LOGO), ("watermark", brand_assets.WATERMARK)):
        ctx.db.x("INSERT OR REPLACE INTO assets(key,mime,w,h,data) VALUES (?,?,?,?,?)", (key, img["mime"], img["w"], img["h"], img["data"]))
    ctx.db.x("DELETE FROM assets WHERE key='logo_raw'")
    ctx.log("branding.image", "default logo")
    return ok()


@route("PUT", "/api/branding/image/<key>", "settings.branding", "edit")
def branding_image(ctx, key):
    if key in ("app_logo", "app_icon") and not ctx.user.get("platform_owner"):
        raise PermissionError("The BeyondERP logo and icon cannot be changed. Add your company logo to documents in Settings → Branding.")
    branding.set_asset(ctx.db, key, ctx.body if ctx.body.get("data") else None)
    ctx.log("branding.image", key + ("" if ctx.body.get("data") else " removed"))
    return ok()


def _file_reply(data_name, fmt):
    data, name = data_name
    if fmt in ("pdf", "html"):
        return Reply(data, ctype="text/html; charset=utf-8")
    return download(data, name, XLSX if name.endswith(".xlsx") else DOCX)


@route("POST", "/api/lines/parse")
def lines_parse(ctx):
    """Lines of a BOQ / invoice read from an Excel file edited outside the app (nothing saved: they go back to the
    screen, which shows them in place of the current lines until saved)."""
    kind = "invoice" if ctx.q.get("kind") == "invoice" else "boq"
    if not ctx.can("sales.invoices" if kind == "invoice" else "estimating.boqs", "add"):
        raise PermissionError()
    from . import linesheet
    return ok(linesheet.parse(ctx.raw or b"", kind))


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
    body = ctx.body if ctx.body.get("sections") else {**documents.DEFAULT_OFFER_TYPES[0], **{k: v for k, v in ctx.body.items() if v}}
    content = documents._content(body)
    if ctx.body.get("sample"):   # Settings preview: no real details — placeholder bank instead of the company's
        for s in content["sections"]:
            if s["kind"] == "bank":
                s.update(bank_id="custom", bank_details=documents.SAMPLE_BANK)
    o = dict(content, client=ctx.body.get("client") or "Client Name",
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
ERASE_TRANSACTIONS = ("entry_tags", "workdays", "hr_requests", "presence", "desk_rows", "desk_threads", "care_visit_lines", "care_visits", "care_claims", "care_packages", "crm_followups", "crm_activities", "crm_deals", "approvals", "sub_cert_lines", "sub_certificates", "pr_quote_lines", "pr_quotes", "pr_lines", "purchase_requests", "stock_count_lines",
                      "stock_counts", "equipment_usage", "maintenance_logs", "attendance", "ereceipts", "sub_recognitions", "sub_visits", "sub_invoices", "subscriptions", "po_receipts", "po_lines", "purchase_orders", "bank_lines", "bank_statements", "recurring_runs", "sale_cheques", "sale_installments", "sale_lines", "sales_orders", "stock_moves", "stock_levels", "extract_lines", "extracts", "ownership_changes", "journal_lines",
                      "journal_entries", "offers", "loans", "custodies", "rent_moves", "rental_invoices", "rental_charges",
                      "rental_handovers", "rental_lines", "rentals", "rent_lots")
ERASE_ALL = ERASE_TRANSACTIONS + ("kb_pages", "automation_log", "automations", "care_services", "care_patients", "parties", "stock_items", "boq_lines", "boqs", "partner_shares", "partners", "rates",
                                  "fixed_assets", "business_units", "offer_types", "tax_codes", "assets", "rent_kit_lines", "rent_kits",
                                  "rent_items", "rent_models", "product_media", "products", "bank_rules", "recurring", "budgets", "attachments", "service_plans",
                                  "variation_orders", "contract_lines", "contracts", "maintenance_plans", "equipment", "project_tasks", "project_phases")


@route("POST", "/api/erase")
def erase_data(ctx):
    """Super administrator only. scope 'transactions' keeps master data; 'all' is a factory reset (users are kept)."""
    _need_super(ctx, "erase data")
    if (ctx.body.get("confirm") or "").strip().upper() != "ERASE":
        raise acc.ValidationError('Type ERASE to confirm')
    scope = ctx.body.get("scope") if ctx.body.get("scope") in ("transactions", "all") else None
    if not scope:
        raise acc.ValidationError("Choose what to erase")
    backup = ctx.server.backup("before-erase")
    _erase(ctx.db, scope)
    ctx.log("erase", f"{scope} (backup {backup.name})")
    return ok({"scope": scope, "backup": backup.name})


def _erase(db, scope):
    with db.tx():
        for t in (ERASE_ALL if scope == "all" else ERASE_TRANSACTIONS):
            db.conn.execute(f"DELETE FROM {t}")
        db.conn.execute("UPDATE products SET status='available' WHERE status IN ('reserved','sold','delivered')")
        if scope == "all":
            db.conn.execute("DELETE FROM warehouses")
            db.conn.execute("DELETE FROM accounts")
            db.conn.execute("DELETE FROM settings WHERE key NOT IN ('schema_version')")
            db.seed()
            db._migrate()
            db.seed_documents()
            db.conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('docs_seeded','1')")



# =================================================================== system
@route("GET", "/api/backup", "settings.system", "edit")
def backup_download(ctx):
    # the whole database file: password fingerprints and connected tools' keys included, so super administrators only
    _need_super(ctx, "download the database file")
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
        raise acc.ValidationError((f"{email} has workspace access but no app user has this email. An administrator should add or edit the user in Users and enter this email address."))
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
                raise acc.ValidationError("You are not a member of any company. Use the website to create a company or join one with an invite code.")
            return ok({"choose": live})   # several companies: the screen asks which one, then signs in again
        if not info.get("is_member"):
            raise acc.ValidationError("This account is not a member of this company. Ask its administrator to add you.")
        sync.join()
    except (PermissionError, RuntimeError, cloud.Offline, cloud.NeedsUpdate) as e:
        sync.disconnect()
        raise acc.ValidationError(str(e)) from None
    found = _workspace_login(ctx, name, pw)
    if not found:
        raise acc.ValidationError("Signed in, but no app user matches this account. Ask an administrator to add your email or username in Users.")
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
    """Companies: choose the one this device follows, create one, ask to join one, rename, delete, new invite code."""
    c, b = _cloud(ctx), ctx.body
    act = b.get("action")
    fns = {"choose": lambda: c.choose_company(acc.text(b.get("company"))),
           "create": lambda: c.create_company(acc.text(b.get("name"))),
           "join": lambda: c.join_company(acc.text(b.get("code"))),
           "rename": lambda: c.rename_company(acc.text(b.get("name"))) or {"ok": True},
           "delete": lambda: c.delete_company(acc.text(b.get("company")), acc.text(b.get("name"))) or {"ok": True},
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


# chat between companies and the AI assistant on the shared workspace (supabase/migrations/*_chat_links_ai.sql,
# supabase/functions/ai): the same calls the browser edition makes from bridge.js
CLOUD_RPCS = {"my_super", "set_member_super", "chat_link_list", "chat_link_create", "chat_link_accept", "chat_link_end",
              "chat_link_messages", "chat_link_file", "chat_link_send", "chat_link_edit", "chat_link_read",
              "ai_settings_get", "ai_settings_set", "desk_pages", "desk_page_save", "desk_claim", "desk_reply", "desk_thread",
              "desk_publishers", "desk_my_publish"}


@route("POST", "/api/cloud/call")
def cloud_passthrough(ctx):
    c = _cloud(ctx)
    kind, name, params = ctx.body.get("kind"), ctx.body.get("name"), ctx.body.get("params") or {}
    if kind == "rpc" and name in CLOUD_RPCS:
        return _cloud_call(lambda: {"result": c.rpc(name, {"p_company": c._co(), **params})})
    if kind == "fn" and name == "ai":
        return _cloud_call(lambda: {"result": c.client()._call("POST", "/functions/v1/ai", {**params, "company": c._co()}, c.token())})
    raise acc.ValidationError("Unknown workspace call")


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
# what someone who still has to turn on two-step sign-in (when the company requires it) may do meanwhile
TWO_STEP_FREE = re.compile(r"^/api/(?:me/two-step(?:/off)?|logout|bootstrap|me/profile|app-update|licence)$")


class Ctx:
    def __init__(self, app, db, user, token, q, body, raw, headers):
        self.app, self.db, self.user, self.token, self.q, self.body, self.raw = app, db, user, token, q, body, raw
        self.server = app  # backup()/backups() live on the app object
        self.headers = headers
        # the reader's language (screens send it; downloads say ?lang=)
        self.lang = "ar" if (headers.get("x-lang") or (q or {}).get("lang") or "") == "ar" else "en"

    def can(self, module, level="view"):
        return auth.can(self.user, module, level)

    def require(self, module, level="view"):
        if not self.can(module, level):
            raise PermissionError()
        act = getattr(self, "action", None)
        if act and not auth.action_ok(self.user, module, act):
            raise PermissionError(ACTION_DENIED[act])

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


# what an API key may never do: keys, people, passwords, sessions, erasing / restoring the books, licences, the workspace
API_KEY_BLOCKED = re.compile(r"^/api/(api-keys|users|me/password|me/sessions|login|logout|setup|erase|restore|import|backup|licence|cloud|"
                             r"maintenance|settings$|ai/settings)")


def handle(app, method, target, headers, raw=b"", as_user=None, dry=False):
    """Transport-independent request handler: used by the HTTP server and by the browser build.

    headers: dict with lower-case keys. Returns a Reply.
    as_user: run as this username without a session (replaying a change from the shared workspace).
    dry: validation run inside a transaction the caller rolls back (no backups).
    """
    reply = _handle(app, method, target, headers, raw, as_user, dry)
    lang = headers.get("x-lang") or (parse_qs(urlparse(target).query).get("lang") or [""])[-1]
    if lang == "ar" and isinstance(reply, Reply) and reply.status >= 400 and "json" in (reply.ctype or ""):
        try:                                   # the message in the reader's language
            data = json.loads(reply.body)
            if isinstance(data, dict) and isinstance(data.get("error"), str):
                data["error"] = textl10n.error(data["error"], "ar")
                reply.body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        except ValueError:
            pass
    return reply


def _handle(app, method, target, headers, raw, as_user, dry):
    if dry:
        app = _DryRunApp(app)
    url = urlparse(target)
    db = app.db
    try:
        if len(raw or b"") > MAX_BODY:
            return ok({"error": "Upload too large (max 25 MB)"}, 413)
        api_key = None
        bearer = (headers.get("authorization") or "")
        if bearer.lower().startswith("bearer " + auth.API_PREFIX) and as_user is None and getattr(app, "static", False) is not True:
            # an API key (outside tools): no cookie, so no cross-site risk; it acts as its person, read-only unless allowed
            key_user, api_key = auth.user_for_api_key(db, bearer[7:].strip())
            if not key_user:
                return ok({"error": "Unknown or revoked API key"}, 401)
            if method != "GET" and api_key["read_only"]:
                return ok({"error": "This API key is read-only"}, 403)
            if API_KEY_BLOCKED.match(url.path):
                return ok({"error": "API keys cannot perform this action. Sign in to the application."}, 403)
        elif method != "GET" and headers.get("x-requested-with") != "BeyondERP":
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
        elif api_key:
            user, token = key_user, None
        else:
            user = auth.user_for_token(db, token)
        for m, rx, module, level, public, fn in ROUTES:
            if m != method:
                continue
            match = rx.match(url.path)
            if not match:
                continue
            if not public and not user:
                return ok({"error": "Sign in to continue."}, 401)
            if headers.get("x-ai-call") == "1" and as_user is None:   # a call the AI assistant makes for this person
                from . import ai
                ai.guard(db, user, method, url.path, raw, headers, route_module(method, url.path) or module)   # policy; changes only after check + review
            if module and not auth.can(user, module, level):
                return ok({"error": "You do not have permission for this action."}, 403)
            action = request_action(method, url.path, q)
            if module and not auth.action_ok(user, module, action):
                return ok({"error": ACTION_DENIED[action]}, 403)
            if (method != "GET" and as_user is None and getattr(app, "licensing", False) and not public
                    and not LICENCE_FREE.match(url.path)):
                from . import licence
                st = licence.status(db)
                if not st["can_write"]:
                    return ok({"error": (f"{st.get('problem') or 'No licence'}. The books are read-only; viewing, printing and exporting remain available. Enter a licence key in Settings → System → Licence."), "licence": st["state"]}, 402)
            if (user and not public and as_user is None and not api_key and getattr(app, "static", False) is not True
                    and not user.get("two_step") and not TWO_STEP_FREE.match(url.path)):
                need = db.setting("two_step_required") or ""
                if need == "all" or (need == "admins" and user.get("is_admin")):
                    return ok({"error": "Enable two-step sign-in first (profile → Two-step sign-in).", "two_step_setup": True}, 403)
            from . import scope
            sc = scope.of(db, user) if not public else None
            if sc:                                    # a person limited to some projects
                scope.guard(db, sc, method, url.path, q, body)
            ctx = Ctx(app, db, user, token, q, body, raw, headers)
            ctx.action = action
            ctx.cloud_hook = getattr(app, "cloud", None)
            ctx.scope = sc
            ai_change = (q.get("_ai") or q.get("_undo")) if method != "GET" else None   # the assistant's change, or a person's own save
            if ai_change and re.fullmatch(r"[\w-]{6,60}", ai_change):   # the assistant's change: recorded so it can be undone
                from . import aiundo
                reply = aiundo.capture(db, ai_change, user, lambda: fn(ctx, **match.groupdict()))
            else:
                reply = fn(ctx, **match.groupdict())
            if sc and method == "GET" and isinstance(reply, Reply) and reply.status == 200 and "json" in (reply.ctype or ""):
                reply.body = json.dumps(scope.narrow(sc, url.path, json.loads(reply.body)), ensure_ascii=False, default=str).encode("utf-8")
            if ctx.lang != "en" and method == "GET" and isinstance(reply, Reply) and reply.status == 200 and "json" in (reply.ctype or ""):
                # texts the app wrote into the books (memos, schedule labels…) in the reader's language
                reply.body = json.dumps(textl10n.walk(json.loads(reply.body), ctx.lang), ensure_ascii=False, default=str).encode("utf-8")
            return reply
        return ok({"error": "Not found"}, 404)
    except PermissionError as e:
        return ok({"error": str(e) or "You do not have permission for this action."}, 403)
    except (acc.ValidationError, LookupError, ValueError, json.JSONDecodeError) as e:
        return ok({"error": str(e)}, 400)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return ok({"error": f"Server error: {e.__class__.__name__}"}, 500)


class Handler(BaseHTTPRequestHandler):
    server_version = "BeyondERP"      # no version numbers for whoever is probing
    sys_version = ""
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
        # product media: photos / clips / 3D models are data, blob or https links; players (YouTube, Vimeo, Sketchfab,
        # Drive, virtual tours) are https frames. Scripts stay same-origin only.
        r.headers["Content-Security-Policy"] = ("default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval'; "
                                                "img-src 'self' data: blob: https:; font-src 'self' data: blob:; media-src 'self' data: blob: https:; "
                                                "connect-src 'self' data: blob: https:; frame-src https:; worker-src 'self' blob:; "
                                                "frame-ancestors 'self'")
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
        self.licensing = True     # desktop / self-hosted: needs a licence once the vendor key is in the app (erp/licence.py)
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

    def start_alert_checks(self, every=300):
        """Desktop / self-hosted server on its own (no shared workspace): check every alert and rule every few minutes
        on the server itself, so the bell has them even when nobody has the app open. (With the shared workspace the
        devices and its push service do this, and a change must go through the shared log, so the server stays out.)"""
        import threading
        from . import automations

        def loop():
            import time
            while True:
                time.sleep(every)
                if self.cloud.active:
                    continue
                try:
                    automations.run(self.db, local=_dt.datetime.now().isoformat(timespec="minutes"))
                except Exception:      # noqa: BLE001 — try again next time
                    traceback.print_exc()
        threading.Thread(target=loop, name="alert-checks", daemon=True).start()

    def daily_backup(self):
        today = _dt.date.today().strftime("%Y%m%d")
        if not any(p.name.startswith(f"beyond-erp-{today}") for p in self.backups()):
            self.backup("daily")

