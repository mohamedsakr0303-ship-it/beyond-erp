"""Profit by category: what each line of business earned and what it cost — rent, installation, transport, design fees…

A category is a name and the accounts that belong to it (codes, prefixes 60* or ranges 6010-6040), revenue and costs
together. Its profit is its revenue less its costs; what no category takes stays in "Not in a category" (usually the
company's shared overhead), so the categories always add up to the net profit in the income statement.
Kept in the setting `profit_categories`; until it is set, the suggestions below (from the accounts the company uses).
"""
import json
import re

from . import accounting as acc
from .allocation import account_matches
from .db import to_major

# suggested categories for an equipment-rental company: by account role, then by the industry template's codes
_RENTAL = (("Rent", "الإيجار", ("rental_revenue", "rental_compensation", "rental_loss", "depreciation"), ("5066", "6015")),
           ("Installation & dismantling", "التركيب والفك", ("rental_services", "materials"), ("5065",)),
           ("Transport & delivery", "النقل والتوصيل", ("rental_transport",), ("5067",)))


def _codes(db):
    return {r["code"] for r in db.q("SELECT code FROM accounts")}


def suggested(db):
    """Suggestions from the accounts the company already uses; empty when it does not rent equipment."""
    if not db.one(f"""SELECT 1 FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                      WHERE {acc.LIVE} AND a.role='rental_revenue' LIMIT 1"""):
        return []
    have, out = _codes(db), []
    for name, name_ar, roles, codes in _RENTAL:
        picked = [r["code"] for r in db.q(f"SELECT code FROM accounts WHERE role IN ({','.join('?' * len(roles))}) ORDER BY code", roles)]
        picked += [c for c in codes if c in have and c not in picked]
        if picked:
            out.append({"name": name, "name_ar": name_ar, "accounts": ", ".join(picked)})
    return out


def load(db):
    """The company's categories ({name, name_ar, accounts}), or the suggestions while none are saved."""
    raw = db.setting("profit_categories")
    if raw:
        try:
            v = json.loads(raw)
            if isinstance(v, list) and v:      # saving none goes back to the suggestions
                return v, False
        except ValueError:
            pass
    return suggested(db), True


def clean(v):
    """Checked categories to save: a name each, account scopes the allocation rules understand."""
    if not isinstance(v, list):
        raise acc.ValidationError("profit_categories has the wrong shape")
    out = []
    for c in v[:40]:
        if not isinstance(c, dict):
            continue
        name, scope = acc.text(c.get("name"), maxlen=80), acc.text(c.get("accounts"), maxlen=400)
        if not name and not scope:
            continue
        if not name:
            raise acc.ValidationError("Give each category a name.")
        if not scope:
            raise acc.ValidationError(f"Choose the accounts of {name}.")
        for p in [p.strip() for p in re.split(r"[,;\s]+", scope) if p.strip()]:
            if not re.fullmatch(r"\d+\*?|\d+-\d+", p):
                raise acc.ValidationError(f"Account scope not understood: {p}. Use codes, prefixes (60*) or ranges (6010-6040).")
        out.append({"name": name, "name_ar": acc.text(c.get("name_ar"), maxlen=80), "accounts": scope})
    return out


def compute(db, date_from=None, date_to=None, bu_id=None):
    cats, is_suggested = load(db)
    names_ar = {r["code"]: r["name_ar"] for r in db.q("SELECT code, name_ar FROM accounts")}
    rows = [r for r in acc.account_totals(db, date_from=date_from, date_to=date_to, bu_id=bu_id)
            if r["type"] in ("Revenue", "COGS", "Expense") and (r["dr"] or r["cr"])]
    out = [{"name": c["name"], "name_ar": c.get("name_ar") or "", "accounts_scope": c["accounts"], "accounts": [],
            "revenue": 0.0, "direct": 0.0, "other": 0.0} for c in cats]
    rest = {"name": "Not in a category", "name_ar": "خارج الفئات", "accounts_scope": "", "accounts": [],
            "revenue": 0.0, "direct": 0.0, "other": 0.0}
    for r in rows:
        # an account counts once: in the first category that names it
        tgt = next((o for o, c in zip(out, cats) if account_matches(c["accounts"], r["code"])), rest)
        amt = to_major(-r["net"] if r["type"] == "Revenue" else r["net"])
        k = "revenue" if r["type"] == "Revenue" else ("direct" if r["type"] == "COGS" else "other")
        tgt[k] = round(tgt[k] + amt, 2)
        tgt["accounts"].append({"code": r["code"], "name": r["name"], "name_ar": names_ar.get(r["code"]) or "", "kind": k, "amount": amt})
    for o in out + [rest]:
        o["cost"] = round(o["direct"] + o["other"], 2)
        o["profit"] = round(o["revenue"] - o["cost"], 2)
        o["margin"] = round(o["profit"] / o["revenue"], 4) if o["revenue"] else None
    total = {k: round(sum(o[k] for o in out + [rest]), 2) for k in ("revenue", "direct", "other", "cost", "profit")}
    total["margin"] = round(total["profit"] / total["revenue"], 4) if total["revenue"] else None
    return {"categories": out, "rest": rest, "total": total, "suggested": is_suggested, "defined": cats}


def table(db, date_from=None, date_to=None, bu_id=None, lang="en"):
    from .reports import _row, _subtitle
    ar = lang == "ar"
    T = lambda en, a: a if ar else en  # noqa: E731
    d = compute(db, date_from, date_to, bu_id)
    rows = []
    for o in d["categories"] + ([d["rest"]] if d["rest"]["accounts"] else []):
        rows.append(_row((o["name_ar"] or o["name"]) if ar else o["name"],
                         [o["revenue"], -o["direct"], -o["other"], o["profit"], o["margin"] if o["margin"] is not None else ""], "subtotal"))
        for a in o["accounts"]:
            v = a["amount"] if a["kind"] == "revenue" else -a["amount"]
            rows.append(_row(f"{a['code']} {(a['name_ar'] or a['name']) if ar else a['name']}",
                             [v if a["kind"] == "revenue" else "", v if a["kind"] == "direct" else "",
                              v if a["kind"] == "other" else "", "", ""], "item", 1, a["code"]))
    t = d["total"]
    rows.append(_row(T("NET PROFIT", "صافي الربح"), [t["revenue"], -t["direct"], -t["other"], t["profit"],
                                                    t["margin"] if t["margin"] is not None else ""], "grand"))
    rows.append(_row(T("Each account counts in the first category that lists it. \"Not in a category\" is everything else, usually "
                       "shared overhead; with it the categories add up to the net profit in the income statement.",
                       "يُحسب كل حساب في أول فئة تذكره. «خارج الفئات» هو الباقي، وغالباً مصروفات مشتركة؛ وبه يساوي مجموع الفئات صافي الربح في قائمة الدخل."),
                     ["", "", "", "", ""], "note"))
    if d["suggested"] and d["categories"]:
        rows.append(_row(T("Suggested categories from your accounts. Change them on the Profit by category screen.",
                           "فئات مقترحة من حساباتك. عدّلها من شاشة الربح حسب الفئة."), ["", "", "", "", ""], "note"))
    pers = [("", date_from or "…", acc.clean_date(date_to) if date_to else acc.today())]
    return {"title": T("Profit by category", "الربح حسب الفئة"), "subtitle": _subtitle(db, lang, pers, bu_id),
            "columns": [{"label": T("Category", "الفئة"), "align": "left"},
                        {"label": T("Revenue", "الإيراد"), "align": "right", "fmt": "money"},
                        {"label": T("Direct costs", "التكاليف المباشرة"), "align": "right", "fmt": "money"},
                        {"label": T("Other costs", "تكاليف أخرى"), "align": "right", "fmt": "money"},
                        {"label": T("Profit", "الربح"), "align": "right", "fmt": "money"},
                        {"label": T("Margin", "الهامش"), "align": "right", "fmt": "pct"}],
            "rows": rows, "lang": lang, "data": d}
