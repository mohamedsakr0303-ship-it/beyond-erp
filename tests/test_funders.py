"""Several funding partners: each with his own account beside the main one (or a bank), money through each account in
his name, moving his balance to and from capital, distributions paid through a funding partner's account, the
distribution rule when one of them should be repaid first, and the books travelling through import / export."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from erp import accounting as a  # noqa: E402
from erp import cashplan, equity, funders, fundsched, interchange, partner_pack, settlement  # noqa: E402
from erp.db import Database  # noqa: E402


def books(main=True):
    db = Database(":memory:")
    db.x("INSERT INTO business_units(code,name,basis,status,start_date,overhead_pct) VALUES ('P-1','P-1','Fixed Price','In Progress','2026-01-01',1)")
    db.x("INSERT INTO partners(name,default_share) VALUES ('Main',0.4),('Second',0.3),('Third',0.3)")
    if main:
        db.set_setting("funding_partner", "Main")
        settlement.apply_funding_mode(db, "capital")
    return db


def line(db, eid, code):
    return db.one("SELECT debit, credit, party FROM journal_lines WHERE entry_id=? AND account=?", (eid, code))


class SeveralFunders(unittest.TestCase):
    def test_each_gets_his_own_account_and_mode(self):
        db = books()
        out = funders.save_extra(db, [{"name": "Second", "mode": "facility", "limit": 5000, "repay_pct": 0.1},
                                      {"name": "third", "mode": "capital"}])
        self.assertEqual([f["name"] for f in out], ["Second", "Third"])
        acc2 = db.one("SELECT * FROM accounts WHERE code=?", (out[0]["account"],))
        acc3 = db.one("SELECT * FROM accounts WHERE code=?", (out[1]["account"],))
        self.assertEqual((acc2["type"], acc2["subtype"], acc2["role"]), ("Liability", "short_term_debt", "funding_partner_2"))
        self.assertEqual((acc3["type"], acc3["subtype"], acc3["role"]), ("Equity", "capital", "funding_partner_3"))
        self.assertEqual(db.one("SELECT code FROM accounts WHERE role='funding_partner'")["code"], funders.primary(db)["account"])
        with self.assertRaisesRegex(a.ValidationError, "main funding partner"):
            funders.save_extra(db, [{"name": "Main"}])
        with self.assertRaisesRegex(a.ValidationError, "not a partner"):
            funders.save_extra(db, [{"name": "Stranger"}])
        # switching his money to capital moves the account; taking him off keeps it (inactive), with its history
        funders.save_extra(db, [{"name": "Second", "mode": "capital"}])
        self.assertEqual(db.one("SELECT type FROM accounts WHERE code=?", (out[0]["account"],))["type"], "Equity")
        self.assertFalse(next(f for f in funders.extra(db) if f["name"] == "Third")["active"])
        self.assertTrue(db.one("SELECT 1 FROM accounts WHERE code=?", (out[1]["account"],)))

    def test_money_through_his_account_is_his_and_the_bank_still_works(self):
        db = books(main=False)                       # a bank, and a funding partner beside it
        f = funders.save_extra(db, [{"name": "Second", "mode": "facility"}])[0]
        self.assertEqual(a.money_account(db, "bank"), db.acct("bank"))       # no main funder: the bank by default
        self.assertEqual(a.money_account(db, None, f["account"]), f["account"])
        a.record_project_cost(db, {"bu_code": "P-1", "vendor": "Sup", "description": "Works", "qty": 1, "rate": 700,
                                   "paid": 700, "paid_from": f["account"], "date": "2026-02-02"})
        a.record_project_cost(db, {"bu_code": "P-1", "vendor": "Sup", "description": "Works", "qty": 1, "rate": 300,
                                   "paid": 300, "paid_from": db.acct("bank"), "date": "2026-02-03"})
        party = db.one("SELECT DISTINCT party FROM journal_lines WHERE account=?", (f["account"],))["party"]
        self.assertEqual(party, "Second")
        self.assertEqual(funders.balance(db, f), 700)                      # the company owes him what he paid
        bs = a.balance_sheet(db)
        self.assertTrue(bs["balanced"])
        self.assertEqual(next(x for x in bs["current_liabilities"] if x["code"] == f["account"])["amount"], 700)
        ov = {x["name"]: x for x in funders.overview(db)}
        self.assertEqual((ov["Second"]["owed_to_him"], ov["Second"]["held_by_him"]), (700, 0))

    def test_moving_his_balance_to_and_from_capital(self):
        db = books()
        f = funders.save_extra(db, [{"name": "Second", "mode": "facility"}])[0]
        a.record_project_cost(db, {"bu_code": "P-1", "vendor": "Sup", "description": "Works", "qty": 1, "rate": 1000,
                                   "paid": 1000, "paid_from": f["account"], "date": "2026-02-02"})
        pv = equity.preview(db, "funder_capital", {"funder": "Second", "direction": "to_capital", "amount": 1500, "date": "2026-02-05"})
        self.assertTrue(any("goes beyond" in n for n in pv["notes"]))                      # more than he is owed: a warning
        r = equity.post(db, "funder_capital", {"funder": "Second", "direction": "to_capital", "amount": 600, "date": "2026-02-05"})
        self.assertEqual(line(db, r["id"], f["account"])["debit"], 60000)
        cap = line(db, r["id"], db.acct("capital"))
        self.assertEqual((cap["credit"], cap["party"]), (60000, "Second"))
        self.assertEqual(funders.balance(db, f), 400)
        self.assertEqual(equity.balances(db, "Second")["capital"], 600)
        with self.assertRaisesRegex(a.ValidationError, "capital is only"):
            equity.post(db, "funder_capital", {"funder": "Second", "direction": "from_capital", "amount": 601, "date": "2026-02-06"})
        equity.post(db, "funder_capital", {"funder": "Second", "direction": "from_capital", "amount": 100, "date": "2026-02-06"})
        self.assertEqual((funders.balance(db, f), equity.balances(db, "Second")["capital"]), (500, 500))
        # shares can follow capital when chosen
        equity.post(db, "funder_capital", {"funder": "Second", "direction": "to_capital", "amount": 100, "date": "2026-02-07",
                                           "ownership": "by_capital"})
        self.assertAlmostEqual(equity.state(db)["shares"][str(db.one("SELECT id FROM partners WHERE name='Second'")["id"])], 1.0, 4)
        self.assertTrue(a.balance_sheet(db)["balanced"])

    def _profit(self, db, pay_from):
        a.record_journal(db, {"memo": "Invoice", "date": "2026-02-10", "bu_code": "P-1",
                              "lines": [{"account": db.acct("ar"), "debit": 10000}, {"account": "4010", "credit": 10000}]})
        a.record_journal(db, {"memo": "Client pays", "date": "2026-02-11", "bu_code": "P-1",
                              "lines": [{"account": pay_from, "debit": 2000}, {"account": db.acct("ar"), "credit": 2000}]})
        a.record_project_cost(db, {"bu_code": "P-1", "vendor": "Sup", "description": "Works", "qty": 1, "rate": 3000,
                                   "paid": 3000, "paid_from": funders.primary(db)["account"], "date": "2026-02-12"})

    def test_distributing_through_the_funding_partners_account(self):
        db = books()
        main = funders.primary(db)["account"]
        db.set_setting("reserve_pct", "0.2")
        db.set_setting("buffer_months", "0")
        self._profit(db, main)
        d = partner_pack.compute(db, None, "2026-02-20")["dist"]
        self.assertIn("DO NOT DISTRIBUTE", d["verdict"][0])         # the money is all with him: nothing goes out from cash
        self.assertEqual(d["max_safe"], 0)
        db.set_setting("distribute_from_funder", "1")
        d = partner_pack.compute(db, None, "2026-02-20")["dist"]
        self.assertIn("through Main's account", d["verdict"][0])
        self.assertGreater(d["through_funder"], 0)
        self.assertEqual(d["max_safe"], d["through_funder"])
        self.assertAlmostEqual(d["through_funder"], max(0.0, d["owed_to_partners"] - d["uncovered_commitments"]), 2)
        self.assertEqual(d["uncovered_commitments"], 0)                     # the reserve is not taken off twice
        before = d["through_funder"]
        a.record_project_cost(db, {"bu_code": "P-1", "vendor": "Sup2", "description": "Unpaid works", "qty": 1, "rate": 500,
                                   "paid": 0, "date": "2026-02-13"})        # a supplier still owed: a commitment
        d = partner_pack.compute(db, None, "2026-02-20")["dist"]
        self.assertEqual(d["uncovered_commitments"], 500)
        self.assertLess(d["through_funder"], before)
        # paid through his account: the partners' drawings against his account
        r = equity.post(db, "distribute", {"amount": 1000, "pay_now": True, "cash_account": main, "date": "2026-02-21"})
        self.assertEqual(line(db, r["id"], main)["credit"], 100000)
        pv = equity.preview(db, "distribute", {"amount": 1000, "pay_now": True, "cash_account": main, "date": "2026-02-21"})
        self.assertTrue(any("Paid through Main's account" in n for n in pv["notes"]))
        self.assertTrue(a.balance_sheet(db)["balanced"])

    def test_another_funding_partner_to_be_repaid_first_holds_distributions(self):
        db = books(main=False)
        f = funders.save_extra(db, [{"name": "Second", "mode": "facility", "repay_first": True}])[0]
        db.set_setting("buffer_months", "0")
        a.record_journal(db, {"memo": "Invoice", "date": "2026-02-10", "bu_code": "P-1",
                              "lines": [{"account": db.acct("bank"), "debit": 10000}, {"account": "4010", "credit": 10000}]})
        a.record_project_cost(db, {"bu_code": "P-1", "vendor": "Sup", "description": "Works", "qty": 1, "rate": 2000,
                                   "paid": 2000, "paid_from": f["account"], "date": "2026-02-12"})
        d = partner_pack.compute(db, None, "2026-02-20")["dist"]
        self.assertIn("repay Second first", d["verdict"][0])
        self.assertEqual(d["other_funders"][0]["balance"], 2000)
        funders.save_extra(db, [{"name": "Second", "mode": "facility", "repay_first": False}])
        self.assertGreater(partner_pack.compute(db, None, "2026-02-20")["dist"]["max_safe"], 0)

    def test_they_travel_through_export_and_import(self):
        db = books()
        f = funders.save_extra(db, [{"name": "Second", "mode": "facility", "limit": 900, "distribute_from": True}])[0]
        a.record_project_cost(db, {"bu_code": "P-1", "vendor": "Sup", "description": "Works", "qty": 1, "rate": 250,
                                   "paid": 250, "paid_from": f["account"], "date": "2026-02-02"})
        db.set_setting("distribute_from_funder", "1")
        dst = Database(":memory:")
        interchange.import_data(dst, interchange.read_json(interchange.to_json(db).encode()), mode="replace", user="t")
        g = funders.by_name(dst, "Second")
        self.assertEqual((g["account"], g["mode"], g["limit"], g["distribute_from"]), (f["account"], "facility", 900, True))
        self.assertEqual(funders.balance(dst, g), 250)
        self.assertEqual(dst.setting("distribute_from_funder"), "1")
        self.assertEqual(funders.primary(dst)["name"], "Main")


    def test_the_repayment_schedule_covers_every_funding_partner(self):
        db = books()
        db.set_setting("funding_repay_pct", "0.5")
        f = funders.save_extra(db, [{"name": "Second", "mode": "facility", "repay_pct": 0.25}])[0]
        main = funders.primary(db)["account"]
        a.record_project_cost(db, {"bu_code": "P-1", "vendor": "Sup", "description": "Works", "qty": 1, "rate": 1000,
                                   "paid": 1000, "paid_from": main, "date": "2026-02-02"})
        a.record_project_cost(db, {"bu_code": "P-1", "vendor": "Sup", "description": "Works", "qty": 1, "rate": 400,
                                   "paid": 400, "paid_from": f["account"], "date": "2026-02-03"})
        a.record_journal(db, {"memo": "Invoice", "date": "2026-02-10", "bu_code": "P-1",
                              "lines": [{"account": db.acct("ar"), "debit": 6000}, {"account": "4010", "credit": 6000}]})
        d = fundsched.build(db, "2026-02-20", {"months": 6})
        self.assertEqual(d["partner"], "Main")                                  # the main one by default
        self.assertEqual({x["name"]: (x["pct"], x["owed"]) for x in d["funders"]}, {"Main": (0.5, 1000.0), "Second": (0.25, 400.0)})
        first = d["rows"][0]                                                    # customers owe 6,000: collected this month
        self.assertEqual(first["in"], 6000)
        self.assertEqual(first["to"], {"Main": 1000.0, "Second": 400.0})        # each from his own share, capped at what he is owed
        self.assertEqual(first["partners"], 4600.0)                              # what is left after both
        self.assertEqual((first["to_funder"], first["closing"]), (1000.0, 0.0))
        d2 = fundsched.build(db, "2026-02-20", {"months": 6}, funder="second")
        self.assertEqual((d2["partner"], d2["pct"], d2["owed"], d2["rows"][0]["to_funder"]), ("Second", 0.25, 400.0, 400.0))
        self.assertTrue(d2["repaid_in"])
        t = fundsched.table(db, "2026-02-20", funder="Second")
        self.assertIn("Second", t["subtitle"])
        self.assertTrue(any("Main" in r[0] if isinstance(r, (list, tuple)) else "Main" in str(r) for r in t["rows"]))

    def test_other_funding_partners_count_in_cash_and_limit_warnings(self):
        db = books(main=False)
        f = funders.save_extra(db, [{"name": "Second", "mode": "facility", "limit": 1000}])[0]
        a.record_project_cost(db, {"bu_code": "P-1", "vendor": "Sup", "description": "Works", "qty": 1, "rate": 950,
                                   "paid": 950, "paid_from": f["account"], "date": "2026-02-02"})
        m = funders.others_money(db)
        self.assertEqual((m["held"], m["room"]), (0.0, 50.0))
        self.assertEqual(cashplan.build(db, 30)["cash_now"], round(cashplan.cash_now(db) + 50, 2))
        self.assertEqual(funders.limit_states(db)[0]["state"], "near")
        flags = a.review_flags(db)
        self.assertTrue(any("Second's funding is near" in x["flag"] for x in flags))


if __name__ == "__main__":
    unittest.main()
