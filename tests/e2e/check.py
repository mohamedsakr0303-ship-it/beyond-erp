"""Figures for the end-to-end tests, read straight from the database (not through the app's API or reports).

    python3 tests/e2e/check.py <data dir> <query> [args…]   → JSON on stdout
"""
import json
import sqlite3
import sys
from pathlib import Path

LIVE = "e.status='posted'"
PARTY = "COALESCE(NULLIF(l.party,''), e.party)"


def main(data_dir, name, *args):
    db = sqlite3.connect(Path(data_dir) / "beyond-erp.sqlite3")
    db.row_factory = sqlite3.Row
    q = lambda sql, p=(): [dict(r) for r in db.execute(sql, p).fetchall()]  # noqa: E731
    if name == "party_balance":        # debit − credit on receivable / payable accounts, in major units
        r = q(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                  JOIN accounts a ON a.code=l.account WHERE {LIVE} AND a.subtype IN ('receivable','payable') AND {PARTY}=?""", args)
        return r[0]["n"] / 100
    if name == "entries":              # posted entries of a kind with a reference
        return q(f"SELECT e.id, e.kind, e.ref, e.bu_id FROM journal_entries e WHERE {LIVE} AND e.kind=? AND e.ref=?", args)
    if name == "stock":                # quantity and value of an item per warehouse
        return q("""SELECT w.name warehouse, s.qty, s.avg_cost FROM stock_levels s JOIN stock_items i ON i.id=s.item_id
                    JOIN warehouses w ON w.id=s.warehouse_id WHERE i.name=? ORDER BY w.id""", args)
    if name == "bu_debits":            # debits on an account for a business unit
        r = q(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                  WHERE {LIVE} AND e.bu_id=? AND l.account=?""", (int(args[0]), args[1]))
        return r[0]["n"] / 100
    if name == "balanced":             # every posted entry balances
        return not q(f"""SELECT e.id FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id WHERE {LIVE}
                         GROUP BY e.id HAVING SUM(l.debit)<>SUM(l.credit)""")
    if name == "sql":                  # a read-only query
        if not args[0].lstrip().upper().startswith("SELECT"):
            raise SystemExit("read-only queries only")
        return q(args[0], args[1:])
    raise SystemExit(f"unknown query {name}")


if __name__ == "__main__":
    print(json.dumps(main(*sys.argv[1:]), default=str))
