"""Project-level access: a person who may only see some projects (business units).

A super administrator picks the projects in Users (stored with the person's permissions as "_bus": [codes]; empty =
every project). For such a person, on top of the permission grid:

* lists only hold rows of their projects — anything tied to another project, or to no project (company overhead),
  is left out of the reply (journal, projects, BOQs, invoices, contracts, purchase orders, tasks, materials …);
* one record of another project (an entry, a BOQ, a contract …) is refused;
* reports run for one of their projects (the only one is picked by itself); company-wide reports and money views —
  cash & bank, bank reconciliation, taxes, partners & equity, loans, payroll runs, forecasts, year-end, full exports —
  are refused;
* what they record must be on one of their projects (an expense with no project would be company overhead).

Super administrators are never limited. Checked on every request (also when a change from the shared workspace is
replayed, so every device keeps the same books).
"""
import re

from .accounting import ValidationError

# money and company-wide views a project-limited person never opens
COMPANY_WIDE = re.compile(r"^/api/(?:cash-accounts|assets-overview|bank(?:-statements(?:/.*)?)?|tax/summary|cash-forecast|forecast(?:/.*)?"
                          r"|year-end(?:/.*)?|partners|equity/.*|funding-schedule|loans(?:/.*)?|payroll/runs(?:/.*)?|export/(?:json|workbook|csv)|backup"
                          r"|depreciation-history|budgets)$")
# reports that run for one project
PROJECT_REPORTS = {"income_statement", "balance_sheet", "cash_flow", "trial_balance", "general_ledger", "bu_pnl", "materials_used",
                   "negotiations", "budget", "vouchers", "profit_categories"}
# new entries: must name one of the person's projects
POSTING = re.compile(r"^/api/(?:docs/[\w-]+|journal|entry/[\w-]+)$")
ONE_ENTRY = re.compile(r"^/api/(?:journal|docs/[\w-]+)/(\d+)(?:/.*)?$")
BU_KEYS = ("bu_code", "bu_id", "bu")
# one record (and its files / documents) by address → its table (each has bu_id)
RECORDS = re.compile(r"^/api/(boqs|extracts|offers|sales|rentals|purchase-orders|contracts|purchase-requests|custody|projects)/(\w[\w-]*)(?:/.*)?$")
TABLE = {"boqs": "boqs", "extracts": "extracts", "offers": "offers", "sales": "sales_orders", "rentals": "rentals",
         "purchase-orders": "purchase_orders", "contracts": "contracts", "purchase-requests": "purchase_requests", "custody": "custodies"}


class Scope:
    def __init__(self, db, codes):
        rows = db.q("SELECT id, code FROM business_units")
        want = {str(c).strip().lower() for c in codes}
        self.codes = {r["code"] for r in rows if r["code"].lower() in want}
        self.ids = {r["id"] for r in rows if r["code"] in self.codes}
        self.by_id = {r["id"]: r["code"] for r in rows}
        self.by_code = {r["code"].lower(): r["id"] for r in rows}

    def ok(self, v):
        """Is this project (id or code) one of theirs? None / '' = no project = not theirs."""
        if v in (None, "", 0, "0"):
            return False
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit() and int(v) in self.by_id and v.lower() not in self.by_code):
            return int(v) in self.ids
        return str(v).strip().lower() in {c.lower() for c in self.codes}

    def first(self):
        return min(self.ids) if self.ids else None


def of(db, user):
    """The person's project limit, or None (every project)."""
    if not user or user.get("is_super"):
        return None
    codes = (user.get("perms") or {}).get("_bus") or []
    return Scope(db, codes) if codes else None


def _deny(msg):
    raise PermissionError(msg)


def _bus_in(body):
    """Every project a request body names (top level and its lines / splits / items)."""
    out = []
    if not isinstance(body, dict):
        return out
    for k in BU_KEYS:
        if body.get(k) not in (None, ""):
            out.append(body[k])
    for key in ("lines", "split", "items", "rows"):
        for ln in body.get(key) or []:
            if isinstance(ln, dict):
                out += [ln[k] for k in BU_KEYS if ln.get(k) not in (None, "")]
    return out


def guard(db, sc, method, path, q, body):
    """Check (and narrow) a request before it runs. q may be changed (a report's project)."""
    if COMPANY_WIDE.match(path):
        _deny("This covers the whole company — you can see your projects only")
    for k in ("bu_id", "bu_code", "bu"):
        v = q.get(k)
        if v not in (None, "") and not sc.ok(v):
            _deny("That project is not one of yours")
    m = re.match(r"^/api/reports/([\w-]+)$", path)
    if m:
        if m.group(1) not in PROJECT_REPORTS:
            _deny("This report covers the whole company — you can see your projects only")
        if not q.get("bu_id"):
            if len(sc.ids) == 1:
                q["bu_id"] = str(sc.first())
            else:
                raise ValidationError("Choose one of your permitted projects for this report.")
    if path == "/api/dashboard" and not q.get("bu_id") and sc.ids:
        q["bu_id"] = str(sc.first())
    m = ONE_ENTRY.match(path)
    if m:
        e = db.one("SELECT bu_id FROM journal_entries WHERE id=?", (int(m.group(1)),))
        if e and not sc.ok(e["bu_id"]):
            _deny("This entry is not on one of your projects")
    m = RECORDS.match(path)
    if m:
        kind, key = m.groups()
        if kind == "projects":
            if not sc.ok(key) and not (key.isdigit() and sc.ok(int(key))):
                _deny("This project is not one of yours")
        elif key.isdigit():
            r = db.one(f"SELECT bu_id FROM {TABLE[kind]} WHERE id=?", (int(key),))
            if r and not sc.ok(r["bu_id"]):
                _deny("This is not on one of your projects")
    if method != "GET":
        named = _bus_in(body)
        for v in named:
            if not sc.ok(v):
                _deny("You can only record on your own projects")
        if method == "POST" and POSTING.match(path) and not named:
            raise ValidationError("Choose one of your permitted projects.")


def _keep(sc, row):
    for k in ("bu_code", "bu_id"):
        if k in row:
            return sc.ok(row[k])
    return True


def _walk(sc, x):
    if isinstance(x, list):
        return [_walk(sc, v) for v in x if not (isinstance(v, dict) and not _keep(sc, v))]
    if isinstance(x, dict):
        return {k: _walk(sc, v) for k, v in x.items()}
    return x


def narrow(sc, path, data):
    """Leave other projects (and company-wide rows) out of a reply; refuse one record of another project."""
    if not isinstance(data, (dict, list)):
        return data
    if isinstance(data, dict) and data.get("error"):
        return data
    if path in ("/api/bus", "/api/projects") and isinstance(data, (dict, list)):
        rows = data.get("rows") if isinstance(data, dict) else data
        if isinstance(rows, list):
            rows = [r for r in rows if not isinstance(r, dict) or sc.ok(r.get("code") or r.get("bu_code"))]
            data = {**data, "rows": rows} if isinstance(data, dict) else rows
        if isinstance(data, dict):
            data.pop("shared_overhead", None)
    if path == "/api/accounts" and isinstance(data, list):     # balances are company-wide
        data = [{**r, "balance": None} if isinstance(r, dict) else r for r in data]
    if isinstance(data, dict) and re.match(r"^/api/[\w-]+/[^/]+$", path):
        for k in ("bu_code", "bu_id"):
            if k in data and data[k] not in (None, "") and not sc.ok(data[k]):
                _deny("This is not on one of your projects")
    return _walk(sc, data)


def clean(db, codes):
    """A project list as stored: known codes only."""
    if not isinstance(codes, (list, tuple)):
        return []
    have = {r["code"].lower(): r["code"] for r in db.q("SELECT code FROM business_units")}
    out = []
    for c in codes:
        c = have.get(str(c).strip().lower())
        if c and c not in out:
            out.append(c)
    return out
