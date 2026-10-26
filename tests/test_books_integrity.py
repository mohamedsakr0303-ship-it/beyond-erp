"""Independent checks of the books: worked out from the journal lines alone, then compared with the statements.

Run on demo companies of several business types; nothing here uses the report code to check the report code.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from erp import accounting as acc  # noqa: E402
from erp import demo, industries, statements  # noqa: E402
from erp.db import Database  # noqa: E402

LIVE = acc.LIVE
J = f"FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account WHERE {LIVE}"
BS_SUBTYPES = set(acc.BS_CURRENT_ASSETS + acc.BS_NONCURRENT_ASSETS + acc.BS_CURRENT_LIABILITIES + acc.BS_NONCURRENT_LIABILITIES
                  + acc.BS_EQUITY)


class BooksIntegrity(unittest.TestCase):
    KINDS = (None, "scaffolding", "sales", "homecare")

    @classmethod
    def setUpClass(cls):
        cls.books = {}
        for kind in cls.KINDS:
            db = Database(":memory:")
            if kind:
                industries.apply(db, kind, True)
            demo.load(db, "t", industry=kind)
            cls.books[kind] = db

    def each(self):
        for kind, db in self.books.items():
            with self.subTest(business=kind or "default"):
                yield db

    def test_every_posted_entry_balances_in_whole_minor_units(self):
        for db in self.each():
            self.assertEqual(db.q(f"""SELECT e.id FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id WHERE {LIVE}
                                      GROUP BY e.id HAVING SUM(l.debit)<>SUM(l.credit)"""), [])
            self.assertEqual(db.q("""SELECT id FROM journal_lines WHERE typeof(debit)<>'integer' OR typeof(credit)<>'integer'
                                     OR debit<0 OR credit<0 OR (debit>0 AND credit>0)"""), [])
            self.assertEqual(db.q("SELECT l.id FROM journal_lines l LEFT JOIN accounts a ON a.code=l.account WHERE a.code IS NULL"), [])
            self.assertEqual(db.q(f"""SELECT e.id FROM journal_entries e WHERE {LIVE}
                                      AND NOT EXISTS (SELECT 1 FROM journal_lines l WHERE l.entry_id=e.id)"""), [])

    def test_statements_agree_with_the_ledger(self):
        for db in self.each():
            by = {r["type"]: r["n"] for r in db.q(f"SELECT a.type, SUM(l.debit-l.credit) n {J} GROUP BY a.type")}
            self.assertEqual(sum(by.values()), 0)
            profit = -(by.get("Revenue", 0) + by.get("COGS", 0) + by.get("Expense", 0)) / 100
            self.assertTrue(acc.trial_balance(db)["balanced"])
            bs = acc.balance_sheet(db)
            self.assertTrue(bs["balanced"], (bs["total_assets"], bs["total_liabilities_equity"]))
            self.assertAlmostEqual(bs["total_assets"] - bs["total_liabilities"], (by.get("Asset", 0) + by.get("Liability", 0)) / 100, 2)
            self.assertAlmostEqual(acc.income_statement(db)["net_profit"], profit, 2)
            self.assertAlmostEqual(sum(x["amount"] for x in bs["equity"] if x["subtype"] in ("earnings", "earnings_closed")), profit, 2)

    def test_no_balance_falls_outside_the_balance_sheet(self):
        for db in self.each():
            lost = [r for r in db.q(f"""SELECT a.code, a.subtype, SUM(l.debit-l.credit) n {J} AND a.type IN ('Asset','Liability','Equity')
                                        GROUP BY a.code HAVING n<>0""") if r["subtype"] not in BS_SUBTYPES]
            self.assertEqual(lost, [])

    def test_aging_ties_to_the_ledger(self):
        """Every contact's balance by age adds up to the ledger on those accounts (lines that name a contact)."""
        for db in self.each():
            ag = statements.aging(db)
            codes = statements.accounts(db)
            ledger = db.q(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) n {J} AND {acc.PARTY}<>'' AND e.date<=?
                              AND l.account IN ({",".join("?" * len(codes))})""", (ag["date_to"], *codes))[0]["n"] / 100
            self.assertAlmostEqual(ag["receivable"]["balance"] + ag["payable"]["balance"], ledger, 2)
            for side in ("receivable", "payable"):
                self.assertAlmostEqual(sum(ag[side]["aging"]), ag[side]["balance"], 2)



class CustomerStatement(unittest.TestCase):
    def test_an_invoice_billed_out_of_unbilled_work_shows_on_the_statement(self):
        """Work booked as done but not invoiced belongs to the business unit; invoicing it moves it to the customer.
        The statement and the aging show what was invoiced and paid, not the unbilled balance."""
        db = Database(":memory:")
        demo.load(db, "t")
        bu = db.one("SELECT id FROM business_units WHERE code='PRJ-D01'")["id"]
        unbilled = db.one(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                               WHERE {LIVE} AND l.account=? AND e.bu_id=?""", (db.acct("unbilled_revenue"), bu))["n"]
        self.assertGreater(unbilled, 1000000)          # the demo has work done, not invoiced, on this unit
        x = acc.save_extract(db, {"bu_id": bu, "number": "INV-T-1", "customer": "Statement Co", "date": acc.today(),
                                  "lines": [{"description": "Works", "qty": 2, "price": 5000}]})
        acc.post_extract(db, x, user="t")
        acc.record_journal(db, {"date": acc.today(), "kind": "Receipt Voucher", "party": "Statement Co", "bu_id": bu,
                                "lines": [{"account": "1020", "debit": 4000}, {"account": "1100", "credit": 4000}]}, "t")
        st = statements.statement(db, "Statement Co")
        self.assertEqual([(r["kind"], r["debit"], r["credit"]) for r in st["rows"]],
                         [("Sales Invoice", 10000.0, 0.0), ("Receipt Voucher", 0.0, 4000.0)])
        self.assertEqual(st["closing"], 6000.0)
        row = next(r for r in statements.aging(db)["rows"] if r["party"] == "Statement Co")
        self.assertEqual(row["balance"], 6000.0)


class SupplierWidget(unittest.TestCase):
    def test_what_we_owe_suppliers_is_trade_payables_only(self):
        from types import SimpleNamespace
        from erp import dashboards
        db = Database(":memory:")
        demo.load(db, "t")
        w = dashboards.w_payables_due(SimpleNamespace(db=db), {"count": 50})
        payables = -sum(r["net"] for r in acc.account_totals(db, date_to=acc.today()) if r["subtype"] == "payable") / 100
        self.assertAlmostEqual(w["total"], payables, 2)
        self.assertNotIn("Demo Bank", [r["title"] for r in w["rows"]])      # a bank loan is not a supplier


class InvoiceNumbers(unittest.TestCase):
    def test_a_suggested_prefix_gets_the_next_number(self):
        db = Database(":memory:")
        demo.load(db, "t")
        self.assertEqual(acc.next_in_series(db, "INV-2030-10-"), "INV-2030-10-001")
        for n in ("INV-2030-10-007", "INV-2030-10-A", "INV-2030-1%-099"):     # a wildcard in a number is taken literally
            db.x("INSERT INTO extracts(bu_id, number, date) VALUES (1, ?, '2030-10-01')", (n,))
        self.assertEqual(acc.next_in_series(db, "INV-2030-10-"), "INV-2030-10-008")


if __name__ == "__main__":
    unittest.main()
