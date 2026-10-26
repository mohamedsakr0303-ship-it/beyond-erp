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


if __name__ == "__main__":
    unittest.main()
