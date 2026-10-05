"""The Assets section: everything the company owns, by class, with the screen that manages each class;
cash & bank accounts (balances, movements, transfers); and the depreciation history."""
from . import accounting as acc

# (key, English, Arabic, screen route, test(account) -> bool) — first match wins
CLASSES = [
    ("cash", "Cash & bank", "النقدية والبنوك", "cash", lambda a: a["subtype"] == "cash"),
    ("custody", "Cash with staff (custody)", "العهد النقدية", "custody", lambda a: a["role"] == "custody"),
    ("receivable", "Clients owe us (receivables)", "المدينون (العملاء)", "receivables", lambda a: a["subtype"] in ("receivable", "allowance_doubtful")),
    ("loans", "Loans we gave", "قروض ممنوحة", "loans", lambda a: (a["role"] or "").startswith(("loans_receivable", "interest_receivable"))),
    ("advances", "Employee advances", "سلف العاملين", "employees", lambda a: a["role"] == "employee_advances"),
    ("inventory", "Inventory & stock", "المخزون", "stock", lambda a: a["subtype"] == "inventory"),
    ("prepaid", "Prepaid expenses & other current assets", "مصروفات مقدمة وأصول متداولة أخرى", "ledger",
     lambda a: a["subtype"] in ("prepaid", "other_current_asset")),
    ("fleet", "Rental equipment (fleet)", "معدات التأجير", "rentfleet", lambda a: a["role"] in ("rental_fleet", "rental_fleet_dep")),
    ("fixed", "Fixed assets (net of depreciation)", "الأصول الثابتة (بعد الإهلاك)", "assets",
     lambda a: a["subtype"] in ("fixed_asset", "accumulated_depreciation", "intangible", "accumulated_amortization")),
    ("long", "Investments & other long-term assets", "استثمارات وأصول طويلة الأجل أخرى", "ledger",
     lambda a: a["subtype"] in ("investment", "other_noncurrent_asset")),
]


def overview(db, date_to=None):
    date_to = date_to or acc.today()
    rows = [r for r in acc.account_totals(db, date_to=date_to) if r["type"] == "Asset" and (r["dr"] or r["cr"])]
    names = {a["code"]: a for a in db.q("SELECT code, name_ar FROM accounts")}
    out = {k: {"key": k, "en": en, "ar": ar, "route": route, "amount": 0.0, "accounts": []} for k, en, ar, route, _ in CLASSES}
    for r in rows:
        k = next((k for k, *_, test in CLASSES if test(r)), "long")
        amt = acc.to_major(r["dr"] - r["cr"])
        out[k]["amount"] = round(out[k]["amount"] + amt, 2)
        out[k]["accounts"].append({"code": r["code"], "name": r["name"], "name_ar": (names.get(r["code"]) or {}).get("name_ar") or "",
                                   "amount": amt})
    classes = list(out.values())
    return {"date_to": date_to, "classes": classes, "total": round(sum(c["amount"] for c in classes), 2)}


def cash_accounts(db, date_to=None, limit=25):
    date_to = date_to or acc.today()
    accts = db.q("SELECT code, name, name_ar, role FROM accounts WHERE type='Asset' AND subtype='cash' AND active=1 ORDER BY code")
    tot = {r["code"]: r for r in acc.account_totals(db, date_to=date_to)}
    for a in accts:
        t = tot.get(a["code"]) or {"dr": 0, "cr": 0}
        a["balance"] = acc.to_major(t["dr"] - t["cr"])
        last = db.one("SELECT MAX(e.date) AS d, COUNT(*) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                      "WHERE l.account=? AND e.status!='void' AND e.date<=?", (a["code"], date_to))
        a["last"], a["moves"] = last["d"], last["n"]
    codes = [a["code"] for a in accts]
    recent = []
    if codes:
        q = ",".join("?" * len(codes))
        recent = [{"date": r["date"], "id": r["id"], "kind": r["kind"], "memo": r["memo"], "account": r["account"],
                   "in": acc.to_major(r["debit"]), "out": acc.to_major(r["credit"])}
                  for r in db.q(f"SELECT e.id, e.date, e.kind, e.memo, l.account, l.debit, l.credit FROM journal_lines l "
                                f"JOIN journal_entries e ON e.id=l.entry_id WHERE l.account IN ({q}) AND e.status!='void' "
                                f"AND e.date<=? ORDER BY e.date DESC, e.id DESC LIMIT ?", (*codes, date_to, int(limit)))]
    return {"date_to": date_to, "accounts": accts, "total": round(sum(a["balance"] for a in accts), 2), "recent": recent}


def transfer(db, d, user=""):
    """Move money between two cash / bank accounts (e.g. cash → InstaPay)."""
    src, dst = acc.text(d.get("from")), acc.text(d.get("to"))
    ok = {r["code"] for r in db.q("SELECT code FROM accounts WHERE type='Asset' AND subtype='cash' AND active=1")}
    if src not in ok or dst not in ok:
        raise acc.ValidationError("Choose two cash / bank accounts")
    if src == dst:
        raise acc.ValidationError("Choose two different accounts")
    amount = acc.to_major(acc.to_minor(d.get("amount")))
    if amount <= 0:
        raise acc.ValidationError("Amount must be more than zero")
    fee = acc.to_major(acc.to_minor(d.get("fee") or 0))
    lines = [{"account": dst, "debit": amount}, {"account": src, "credit": round(amount + fee, 2)}]
    if fee:
        fee_acct = acc.text(d.get("fee_account")) or (db.one("SELECT code FROM accounts WHERE role='bank_charges'") or {}).get("code")
        if not fee_acct:
            raise acc.ValidationError("Choose the account for the transfer fee")
        lines.insert(1, {"account": fee_acct, "debit": fee})
    return acc.post_entry(db, date=d.get("date") or acc.today(), kind="Cash Transfer", memo=acc.text(d.get("memo")) or "Transfer between accounts",
                          ref=acc.text(d.get("ref")), lines=lines, user=user)


def depreciation_history(db, limit=200):
    rows = db.q("SELECT e.id, e.date, e.memo, e.ref, (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) AS amt "
                "FROM journal_entries e WHERE e.kind='Depreciation' AND e.status!='void' ORDER BY e.date DESC, e.id DESC LIMIT ?", (int(limit),))
    return [{"id": r["id"], "date": r["date"], "memo": r["memo"], "ref": r["ref"], "amount": acc.to_major(r["amt"] or 0)} for r in rows]
