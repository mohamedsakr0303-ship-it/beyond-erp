"""Funding partners: partners whose own account pays and receives money for the company.

The first (main) funding partner is the one the company has always had: settings `funding_partner`, `funding_mode`,
`funding_limit`… and the account with the role `funding_partner` (3015). When he is set, the company's payments go
through his account unless another account is chosen — his account works as the company's bank.

More funding partners can work alongside him (or alongside a bank when there is no main one). Each has his own
account (role `funding_partner_2`, `funding_partner_3`, … — roles are unique per account), his own way of being
treated (capital: his money is his capital; facility: a credit line owed back to him), an optional limit, a share of
each client payment repaid to him, "repay him before any distribution", and "distributions may be paid through his
account". Payments and receipts choose his account explicitly; nothing is routed to him by default.

Money through any funding partner's account is always in his name (the line's party).
"""
import json

from . import accounting as acc
from .db import normal_for

KEY = "funders_extra"
MODES = ("capital", "facility")
ROLE = "funding_partner"


def is_role(role):
    """True for the account role of any funding partner (the main one or another)."""
    return bool(role) and (role == ROLE or str(role).startswith(ROLE + "_"))


# null-safe (accounts without a role are simply not a funding partner's, also under NOT)
ROLE_SQL = f"(COALESCE(role,'')='{ROLE}' OR COALESCE(role,'') LIKE '{ROLE}\\_%' ESCAPE '\\')"


def role_sql(alias="a"):
    """SQL condition: the account (by its alias) belongs to any funding partner."""
    return f"(COALESCE({alias}.role,'')='{ROLE}' OR COALESCE({alias}.role,'') LIKE '{ROLE}\\_%' ESCAPE '\\')"


def _num(v, d=0.0):
    try:
        return float(v) if v not in (None, "") else d
    except (TypeError, ValueError):
        return d


def _yes(v, d=False):
    if v in (None, ""):
        return d
    return str(v).strip().lower() in ("1", "yes", "true", "on")


def primary(db):
    """The main funding partner (his account is the company's bank), or None."""
    name = (db.setting("funding_partner") or "").strip()
    if not name:
        return None
    row = db.one(f"SELECT code FROM accounts WHERE role='{ROLE}'")
    return {"name": name, "account": row["code"] if row else "", "role": ROLE, "main": True,
            "mode": db.setting("funding_mode") or "capital", "limit": _num(db.setting("funding_limit")),
            "repay_pct": _num(db.setting("funding_repay_pct"), None),
            "repay_first": _yes(db.setting("repay_funder_first"), True),
            "distribute_from": _yes(db.setting("distribute_from_funder"), False)}


def extra(db):
    """The other funding partners, as stored (with their accounts)."""
    try:
        rows = json.loads(db.setting(KEY) or "[]")
    except ValueError:
        rows = []
    out = []
    codes = {r["role"]: r["code"] for r in db.q(f"SELECT code, role FROM accounts WHERE role LIKE '{ROLE}\\_%' ESCAPE '\\'")}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not r.get("name") or not r.get("role"):
            continue
        out.append({"name": str(r["name"]), "role": r["role"], "account": codes.get(r["role"], ""), "main": False,
                    "mode": r.get("mode") if r.get("mode") in MODES else "capital", "limit": _num(r.get("limit")),
                    "repay_pct": _num(r.get("repay_pct"), None), "repay_first": _yes(r.get("repay_first"), False),
                    "distribute_from": _yes(r.get("distribute_from"), False), "active": _yes(r.get("active"), True)})
    return out


def all_funders(db, active_only=True):
    p = primary(db)
    return ([p] if p else []) + [f for f in extra(db) if f["active"] or not active_only]


def by_account(db, code):
    return next((f for f in all_funders(db, False) if f["account"] == code), None)


def by_name(db, name):
    name = (name or "").strip().lower()
    return next((f for f in all_funders(db, False) if f["name"].lower() == name), None)


def name_for_account(db, code):
    """Whose money moves through this account (lines on it are always in his name)."""
    f = by_account(db, code)
    return f["name"] if f else ""


def balance(db, f, date_to=None):
    """What the company owes this funding partner on his account (credit-positive), as of a date."""
    if not f or not f.get("account"):
        return 0.0
    from .db import to_major
    w, p = acc._where(date_to=date_to)
    r = db.one(f"""SELECT COALESCE(SUM(l.credit-l.debit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                   WHERE {w} AND l.account=?""", p + [f["account"]])
    return to_major(r["n"])


def _apply_mode(db, code, mode):
    typ, sub = ("Liability", "short_term_debt") if mode == "facility" else ("Equity", "capital")
    db.x("UPDATE accounts SET type=?, subtype=?, normal=? WHERE code=?", (typ, sub, normal_for(typ, sub), code))


def _new_account(db, name, mode, nth):
    role = f"{ROLE}_{nth}"
    code = "3016"
    while db.one("SELECT 1 FROM accounts WHERE code=?", (code,)):
        code = str(int(code) + 1)
    typ, sub = ("Liability", "short_term_debt") if mode == "facility" else ("Equity", "capital")
    db.x("INSERT INTO accounts(code,name,name_ar,type,subtype,normal,role) VALUES (?,?,?,?,?,?,?)",
         (code, f"Funding Partner's Account — {name}", f"حساب الشريك الممول — {name}", typ, sub, normal_for(typ, sub), role))
    return role


def save_extra(db, rows):
    """Store the other funding partners. Each must be an active partner (not the main funding partner); a new one gets
    his own account; changing capital ⇄ facility moves his account between equity and liabilities. A funding partner
    taken off the list keeps his account and its history (switched off, not deleted)."""
    if not isinstance(rows, list):
        raise acc.ValidationError("Send the list of funding partners.")
    partners = {r["name"].lower(): r["name"] for r in db.q("SELECT name FROM partners")}
    main = ((db.setting("funding_partner") or "").strip()).lower()
    old = {f["name"].lower(): f for f in extra(db)}
    seen, out = set(), []
    nth = max([int(f["role"].rsplit("_", 1)[1]) for f in old.values() if f["role"].rsplit("_", 1)[1].isdigit()] + [1])
    with db.tx():
        for r in rows:
            name = partners.get(str(r.get("name") or "").strip().lower())
            if not name:
                raise acc.ValidationError(f"{r.get('name') or 'This person'} is not a partner. Add them under Partners first.")
            if name.lower() == main:
                raise acc.ValidationError(f"{name} is already the main funding partner.")
            if name.lower() in seen:
                raise acc.ValidationError(f"{name} appears twice.")
            seen.add(name.lower())
            mode = r.get("mode") if r.get("mode") in MODES else "capital"
            pct = r.get("repay_pct")
            pct = None if pct in (None, "") else max(0.0, min(1.0, _num(pct)))
            prev = old.get(name.lower())
            if prev and prev["account"]:
                role = prev["role"]
                if prev["mode"] != mode:
                    _apply_mode(db, prev["account"], mode)
                db.x("UPDATE accounts SET active=1 WHERE code=?", (prev["account"],))
            else:
                nth += 1
                role = _new_account(db, name, mode, nth)
            out.append({"name": name, "role": role, "mode": mode, "limit": max(0.0, _num(r.get("limit"))), "repay_pct": pct,
                        "repay_first": _yes(r.get("repay_first")), "distribute_from": _yes(r.get("distribute_from")),
                        "active": _yes(r.get("active"), True)})
        # anyone taken off the list stays in the books, inactive (his account keeps its history)
        for k, f in old.items():
            if k not in seen:
                out.append({**{x: f[x] for x in ("name", "role", "mode", "limit", "repay_pct", "repay_first", "distribute_from")},
                            "active": False})
        db.set_setting(KEY, json.dumps(out, ensure_ascii=False))
    return extra(db)


def overview(db, date_to=None):
    """Every funding partner with what the company owes him (or he holds for it) and the room left under his limit."""
    out = []
    for f in all_funders(db, False):
        bal = balance(db, f, date_to)
        out.append({**f, "balance": bal, "owed_to_him": round(max(bal, 0.0), 2), "held_by_him": round(max(-bal, 0.0), 2),
                    "room": round(f["limit"] - max(bal, 0.0), 2) if f["limit"] else None})
    return out


def others_money(db, date_to=None):
    """What the other funding partners make available to the company: money of the company each one holds (a debit
    balance on his account), and the room left under each facility limit (facility mode with a limit). The main
    funding partner's facility is worked out by settlement.facility."""
    held = room = 0.0
    rows = []
    for f in extra(db):
        if not f["active"] or not f["account"]:
            continue
        bal = balance(db, f, date_to)
        h = max(0.0, -bal)
        r = max(0.0, f["limit"] - max(bal, 0.0)) if f["mode"] == "facility" and f["limit"] else 0.0
        held, room = held + h, room + r
        rows.append({"name": f["name"], "account": f["account"], "balance": bal, "held": round(h, 2), "room": round(r, 2)})
    return {"held": round(held, 2), "room": round(room, 2), "rows": rows}


def limit_states(db, date_to=None):
    """The other funding partners with a limit, and how close each is to it (the same warning levels as the main one)."""
    warn = _num(db.setting("funding_warn"), 0.75)
    crit = _num(db.setting("funding_critical"), 0.9)
    out = []
    for f in extra(db):
        if not f["active"] or not f["limit"]:
            continue
        used_amt = max(0.0, balance(db, f, date_to))
        used = used_amt / f["limit"]
        state = "over" if used >= 1 else "near" if used >= crit else "approaching" if used >= warn else "ok"
        out.append({"name": f["name"], "account": f["account"], "limit": f["limit"], "used": round(used, 4),
                    "fronted": round(used_amt, 2), "left": round(f["limit"] - used_amt, 2), "state": state})
    return out


def facility_accounts(db):
    """Funding partners' accounts that work as a credit line (facility mode): money paid through them is spending."""
    out = {f["account"] for f in extra(db) if f["active"] and f["account"] and f["mode"] == "facility"}
    p = primary(db)
    if p and p["account"] and p["mode"] == "facility":
        out.add(p["account"])
    return out
