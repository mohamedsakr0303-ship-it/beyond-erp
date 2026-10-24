"""Cost allocation rules: how shared costs (operating expenses booked without a project) are spread over projects.

A rule says WHICH shared costs it takes and HOW it spreads them:

  Which costs   * entries tagged with the rule (an expense or journal voucher can name its rule), or
                * costs on the rule's accounts (codes, prefixes such as 60*, ranges such as 6010-6040), or
                * everything else, for the default rule.
                Rules are tried in priority order and can be limited to a period (effective from / to).
  How           * fixed %      — a set percentage per project (what is not given out stays with the company)
                * even         — equal shares among the eligible projects
                * driver       — in proportion to a measure of each eligible project over the same month: revenue,
                                 direct cost, labour hours, headcount, area, contract value or expected revenue
                * one project  — all to a single project
                * company only — not allocated (stays as company overhead)
  Eligible      projects running in the month of the cost (started, and not finished before it), optionally limited
                to some statuses, a project group, a list of projects, or all except some.
  Billable      whether the allocated cost can be charged on to clients (cost-plus projects bill it with their fee).

Costs marked "company only" on their entry are never allocated. With no rules at all, the per-project overhead %
(Business units → Allocation) applies, as before. Results are in minor units.
"""
import calendar
import json
import re

from . import accounting as acc
from .accounting import ValidationError, num, text

METHODS = ("fixed", "even", "driver", "single", "none")
DRIVERS = ("revenue", "direct_cost", "labour_hours", "headcount", "area", "contract_value", "expected_revenue")
OPEX = ("employee", "general_admin", "marketing", "utilities", "other_operating")
DEFAULT_STATUSES = ("In Progress",)


# ---------------------------------------------------------------- rules
def _row(r):
    for k in ("split", "include", "exclude", "statuses"):
        try:
            r[k] = json.loads(r[k] or ("{}" if k == "split" else "[]"))
        except ValueError:
            r[k] = {} if k == "split" else []
    r["billable"], r["active"], r["is_default"] = bool(r["billable"]), bool(r["active"]), bool(r["is_default"])
    return r


def rules(db, active_only=False):
    rows = db.q("SELECT * FROM alloc_rules" + (" WHERE active=1" if active_only else "") + " ORDER BY priority, id")
    return [_row(r) for r in rows]


def get(db, rid):
    r = db.one("SELECT * FROM alloc_rules WHERE id=? OR code=?", (int(rid) if str(rid).isdigit() else -1, text(rid)))
    if not r:
        raise ValidationError("Allocation rule not found.")
    return _row(r)


def _codes(db, values):
    known = {b["code"] for b in db.q("SELECT code FROM business_units")}
    out = []
    for v in values or []:
        v = text(v)
        if v and v not in known:
            raise ValidationError(f"Unknown business unit: {v}")
        if v:
            out.append(v)
    return out


def account_matches(scope, code):
    """scope: '6010, 602*, 6030-6060' (blank = any account)."""
    parts = [p.strip() for p in re.split(r"[,;\s]+", scope or "") if p.strip()]
    if not parts:
        return True
    for p in parts:
        if p.endswith("*") and code.startswith(p[:-1]):
            return True
        if "-" in p:
            a, b = (x.strip() for x in p.split("-", 1))
            if a.isdigit() and b.isdigit() and code.isdigit() and int(a) <= int(code) <= int(b):
                return True
        if p == code:
            return True
    return False


def save(db, d, rid=None):
    code = text(d.get("code"), "code", required=True, maxlen=40)
    method = text(d.get("method")) or "fixed"
    if method not in METHODS:
        raise ValidationError("Choose how the rule spreads costs.")
    driver = text(d.get("driver")) if method == "driver" else ""
    if method == "driver" and driver not in DRIVERS:
        raise ValidationError("Choose the measure the rule follows.")
    split = {}
    if method == "fixed":
        for c, v in (d.get("split") or {}).items():
            v = num(v or 0, "percentage")
            v = v / 100 if v > 1 else v
            if v < 0:
                raise ValidationError("A percentage cannot be negative.")
            if v:
                split[_codes(db, [c])[0]] = round(v, 10)
        if sum(split.values()) > 1.00001:
            raise ValidationError(f"The split totals {sum(split.values()) * 100:.2f}%; the total cannot exceed 100%.")
    target = _codes(db, [d.get("target")])[0] if method == "single" and d.get("target") else ""
    if method == "single" and not target:
        raise ValidationError("Choose the project that takes the cost.")
    scope = text(d.get("accounts"), maxlen=400)
    for p in [p.strip() for p in re.split(r"[,;\s]+", scope) if p.strip()]:
        if not re.fullmatch(r"\d+\*?|\d+-\d+", p):
            raise ValidationError(f"Account scope not understood: {p}. Use codes, prefixes (60*) or ranges (6010-6040).")
    df, dt = (acc.clean_date(d[k]) if d.get(k) else "" for k in ("date_from", "date_to"))
    if df and dt and dt < df:
        raise ValidationError("The end date is before the start date.")
    vals = {"code": code, "name": text(d.get("name"), maxlen=120) or code, "method": method, "driver": driver,
            "driver_period": "to_date" if d.get("driver_period") == "to_date" else "month",
            "split": json.dumps(split), "target": target, "include": json.dumps(_codes(db, d.get("include"))),
            "exclude": json.dumps(_codes(db, d.get("exclude"))), "grp": text(d.get("grp"), maxlen=80),
            "statuses": json.dumps([text(s) for s in (d.get("statuses") or []) if text(s)]), "accounts": scope,
            "billable": 1 if d.get("billable") in (True, 1, "1", "yes", "Yes", "true") else 0,
            "priority": int(num(d.get("priority") or 100, "priority")), "is_default": 1 if d.get("is_default") in (True, 1, "1", "yes", "Yes", "true") else 0,
            "active": 0 if d.get("active") in (False, 0, "0", "no", "No", "false") else 1, "date_from": df, "date_to": dt,
            "notes": text(d.get("notes"), maxlen=1000)}
    clash = db.one("SELECT id FROM alloc_rules WHERE code=? AND id<>?", (code, int(rid or 0)))
    if clash:
        raise ValidationError(f"A rule with the code {code} already exists.")
    with db.tx():
        if vals["is_default"]:
            db.conn.execute("UPDATE alloc_rules SET is_default=0 WHERE id<>?", (int(rid or 0),))
        if rid:
            old = get(db, rid)
            db.conn.execute(f"UPDATE alloc_rules SET {', '.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [old["id"]])
            if old["code"] != code:      # entries tagged with the old code follow the rename
                db.conn.execute("UPDATE journal_entries SET meta=json_set(meta,'$.alloc_rule',?) "
                                "WHERE json_extract(meta,'$.alloc_rule')=?", (code, old["code"]))
            return old["id"]
        return db.conn.execute(f"INSERT INTO alloc_rules({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
                               list(vals.values())).lastrowid


def delete(db, rid):
    r = get(db, rid)
    n = db.one("SELECT COUNT(*) n FROM journal_entries WHERE json_extract(meta,'$.alloc_rule')=?", (r["code"],))["n"]
    if n:
        raise ValidationError(f"{n} entries name this rule. Deactivate it instead, or change those entries first.")
    db.x("DELETE FROM alloc_rules WHERE id=?", (r["id"],))
    return r


# ---------------------------------------------------------------- the engine
def _month_bounds(month):
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-01", f"{month}-{calendar.monthrange(y, m)[1]:02d}"


class _Ctx:
    """What the rules need to know about the projects, read once per calculation."""
    def __init__(self, db):
        self.db = db
        self.bus = {b["id"]: b for b in db.q("SELECT * FROM business_units")}
        self.by_code = {b["code"]: b["id"] for b in self.bus.values()}
        self.last = {r["bu_id"]: r["d"] for r in db.q(f"SELECT bu_id, MAX(date) d FROM journal_entries e WHERE {acc.LIVE} "
                                                       "AND bu_id IS NOT NULL GROUP BY bu_id")}
        self._drivers = {}

    def eligible(self, rule, month):
        start, end = _month_bounds(month)
        inc = {self.by_code[c] for c in rule["include"] if c in self.by_code}
        exc = {self.by_code[c] for c in rule["exclude"] if c in self.by_code}
        statuses = set(rule["statuses"])
        out = []
        for bid, b in self.bus.items():
            if (inc and bid not in inc) or bid in exc or (rule["grp"] and (b.get("grp") or "") != rule["grp"]):
                continue
            if statuses and b["status"] not in statuses and not inc:
                continue
            if b["start_date"] and b["start_date"] > end:              # not started yet
                continue
            done = str(b["status"] or "").lower().startswith(acc.DONE_STATUSES)
            if done and (self.last.get(bid) or "") < start:           # finished before this month
                continue
            if not statuses and not inc and str(b["status"] or "").lower().startswith(("tendering", "on hold")):
                continue
            out.append(bid)
        return out

    def driver(self, name, month, to_date):
        key = (name, month, to_date)
        if key in self._drivers:
            return self._drivers[key]
        start, end = _month_bounds(month)
        start = "0000-01-01" if to_date else start
        db, v = self.db, {}
        if name in ("revenue", "direct_cost"):
            t = "Revenue" if name == "revenue" else "COGS"
            sign = "l.credit-l.debit" if name == "revenue" else "l.debit-l.credit"
            v = {r["bu_id"]: r["n"] for r in db.q(
                f"SELECT e.bu_id, SUM({sign}) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                f"JOIN accounts a ON a.code=l.account WHERE {acc.LIVE} AND a.type=? AND e.date BETWEEN ? AND ? "
                "AND e.bu_id IS NOT NULL GROUP BY e.bu_id", (t, start, end))}
        elif name == "labour_hours":
            try:
                v = {r["bu_id"]: r["n"] for r in db.q("SELECT bu_id, SUM(hours+overtime) n FROM attendance WHERE date BETWEEN ? AND ? "
                                                      "AND bu_id IS NOT NULL GROUP BY bu_id", (start, end))}
            except Exception:       # noqa: BLE001 — books without attendance
                v = {}
        elif name == "headcount":
            try:
                v = {r["bu_id"]: r["n"] for r in db.q("SELECT bu_id, COUNT(*) n FROM employees WHERE status='active' AND bu_id IS NOT NULL "
                                                      "GROUP BY bu_id")}
            except Exception:       # noqa: BLE001
                v = {}
        else:
            col = {"area": "area", "contract_value": "contract_value", "expected_revenue": "expected_revenue"}[name]
            v = {bid: (b.get(col) or 0) for bid, b in self.bus.items()}
        self._drivers[key] = {k: max(0.0, float(x or 0)) for k, x in v.items()}
        return self._drivers[key]


def _weights(ctx, rule, month):
    """{bu_id: share of the cost} for one rule in one month; what is not given out stays with the company."""
    m = rule["method"]
    if m == "none":
        return {}
    if m == "single":
        bid = ctx.by_code.get(rule["target"])
        return {bid: 1.0} if bid else {}
    if m == "fixed":
        return {ctx.by_code[c]: v for c, v in rule["split"].items() if c in ctx.by_code}
    elig = ctx.eligible(rule, month)
    if not elig:
        return {}
    if m == "driver":
        vals = ctx.driver(rule["driver"], month, rule.get("driver_period") == "to_date")
        tot = sum(vals.get(b, 0) for b in elig)
        if tot > 0:
            return {b: vals.get(b, 0) / tot for b in elig if vals.get(b, 0)}
    return {b: 1 / len(elig) for b in elig}                     # even (and a driver with nothing to measure)


def _parts(amt, wts):
    """Split a minor-unit amount by weights so the parts add up exactly (largest remainder; no cent made or lost)."""
    sign, amt = (-1 if amt < 0 else 1), abs(amt)
    raw = {b: amt * w for b, w in wts.items()}
    out = {b: int(v) for b, v in raw.items()}
    left = int(round(sum(raw.values()))) - sum(out.values())
    for b in sorted(raw, key=lambda b: raw[b] - out[b], reverse=True)[:max(left, 0)]:
        out[b] += 1
    return {b: sign * v for b, v in out.items()}


LEGACY = {"id": 0, "code": "", "name": "Project overhead %", "method": "fixed", "billable": False}


def _pick(rule_list, by_code, line):
    """The rule for one shared cost line."""
    if line["no_alloc"]:
        return None
    tag = line["rule"]
    if tag and tag in by_code and by_code[tag]["active"]:
        return by_code[tag]
    for r in rule_list:
        if r["is_default"] or not r["active"] or not r["accounts"]:
            continue
        if (r["date_from"] and line["date"] < r["date_from"]) or (r["date_to"] and line["date"] > r["date_to"]):
            continue
        if account_matches(r["accounts"], line["account"]):
            return r
    d = next((r for r in rule_list if r["is_default"] and r["active"] and not (r["date_from"] and line["date"] < r["date_from"])
              and not (r["date_to"] and line["date"] > r["date_to"])), None)
    return d or LEGACY


@acc.memo
def allocate(db, date_from=None, date_to=None):
    """Shared costs spread by the rules. {"by_bu": {bu_id: {"billable", "non_billable"}}, "rules": [...], "company": n,
    "pool": n} — minor units."""
    w, p = acc._where(date_from=date_from, date_to=date_to, bu_id="none")
    lines = db.q(f"""SELECT l.account, e.date, (l.debit-l.credit) AS amt, json_extract(e.meta,'$.alloc_rule') AS rule,
                            COALESCE(json_extract(e.meta,'$.no_alloc'),0) AS no_alloc
                     FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                     WHERE {w} AND (a.subtype IN ({','.join('?' * len(OPEX))}) OR a.type='COGS')""", p + list(OPEX))
    rule_list = rules(db)
    by_code = {r["code"]: r for r in rule_list}
    ctx = _Ctx(db)
    groups = {}                                              # (rule code, month) -> amount
    company = 0
    for ln in lines:
        r = _pick(rule_list, by_code, ln)
        if r is None:
            company += ln["amt"]
            continue
        k = (r["code"], ln["date"][:7])
        groups[k] = groups.get(k, 0) + ln["amt"]
    legacy = {b["id"]: b["overhead_pct"] or 0 for b in ctx.bus.values() if b["overhead_pct"]}
    by_bu, summary = {}, {}
    for (code, month), amt in sorted(groups.items()):
        rule = by_code.get(code) or LEGACY
        wts = legacy if rule is LEGACY else _weights(ctx, rule, month)
        s = summary.setdefault(code, {"code": code, "name": rule["name"], "method": rule["method"], "billable": rule["billable"],
                                      "pooled": 0, "allocated": 0, "split": {}})
        s["pooled"] += amt
        given = 0
        for bid, x in _parts(amt, {b: w for b, w in wts.items() if b in ctx.bus}).items():
            if not x:
                continue
            given += x
            slot = by_bu.setdefault(bid, {"billable": 0, "non_billable": 0})
            slot["billable" if rule["billable"] else "non_billable"] += x
            code_b = ctx.bus[bid]["code"]
            s["split"][code_b] = s["split"].get(code_b, 0) + x
        s["allocated"] += given
        company += amt - given
    return {"by_bu": by_bu, "rules": list(summary.values()), "company": company, "pool": sum(ln["amt"] for ln in lines)}


def preview(db, date_from=None, date_to=None):
    """The allocation in major units, for the screen."""
    from .db import to_major
    a = allocate(db, date_from, date_to)
    return {"pool": to_major(a["pool"]), "company": to_major(a["company"]),
            "rules": [{**r, "pooled": to_major(r["pooled"]), "allocated": to_major(r["allocated"]),
                       "split": {k: to_major(v) for k, v in r["split"].items()}} for r in a["rules"]]}


def import_rows(db, rows):
    """Allocation Rules sheet → rules (by code; existing rules are updated)."""
    n = 0
    for r in rows:
        if not text(r.get("code")):
            continue
        split = {}
        for part in re.split(r"[;,\n]+", text(r.get("split"))):
            if ":" in part or "=" in part:
                c, v = re.split(r"[:=]", part, 1)
                split[c.strip()] = v.strip().rstrip("%")
        listed = lambda k: [x.strip() for x in re.split(r"[;,\n]+", text(r.get(k))) if x.strip()]  # noqa: E731
        d = {**r, "split": split, "include": listed("include"), "exclude": listed("exclude"), "statuses": listed("statuses"),
             "target": text(r.get("target"))}
        old = db.one("SELECT id FROM alloc_rules WHERE code=?", (text(r["code"]),))
        save(db, d, old["id"] if old else None)
        n += 1
    return n


def export_rows(db):
    out = []
    for r in rules(db):
        out.append({"code": r["code"], "name": r["name"], "method": r["method"], "driver": r["driver"],
                    "driver_period": r["driver_period"], "split": "; ".join(f"{k}: {v * 100:g}%" for k, v in r["split"].items()),
                    "target": r["target"], "include": ", ".join(r["include"]), "exclude": ", ".join(r["exclude"]),
                    "grp": r["grp"], "statuses": ", ".join(r["statuses"]), "accounts": r["accounts"],
                    "billable": "Yes" if r["billable"] else "No", "priority": r["priority"],
                    "is_default": "Yes" if r["is_default"] else "No", "active": "Yes" if r["active"] else "No",
                    "date_from": r["date_from"], "date_to": r["date_to"], "notes": r["notes"]})
    return out


TABLES = ("alloc_rules",)
