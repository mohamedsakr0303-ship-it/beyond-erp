"""BOQ / invoice sheets read by their headings, and the invoice export reading back into the same invoice."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from erp import accounting as a  # noqa: E402
from erp import branding, countries, docsheet, documents, interchange  # noqa: E402
from erp.db import Database  # noqa: E402

try:
    import openpyxl  # noqa: F401
    HAVE_XL = True
except ImportError:
    HAVE_XL = False

HEAD = ["م", "البيان", "الوحدة", "الكمية بالعقد", "المنفذ فعلي", "نسبة التنفيذ", "السعر", "الإجمالي بالعقد", "الإجمالي الفعلي", "ملاحظات"]


class SheetNames(unittest.TestCase):
    def test_kinds(self):
        for t in ("Invoice 01", "invoice 1 site a", "INVOICE-7", "فاتورة 3 - الدور الثاني", "مستخلص 2"):
            self.assertEqual(docsheet.kind_of(t), "invoice", t)
        for t in ("BOQ", "BOQs", "مقايسة الأعمال"):
            self.assertEqual(docsheet.kind_of(t), "boq", t)
        for t in ("Invoice Register", "Invoice Lines", "Dashboard", "Sheet1"):
            self.assertIsNone(docsheet.kind_of(t), t)


class ReadInvoice(unittest.TestCase):
    rows = [
        ["التاريخ", "23/9/2026", None, "Your company"],
        ["المشروع", "Owner"],
        ["الموقع", "Site A"],
        ["المستخلص", "Invoice 01"],
        ["ما تم سداده دفعة 1", 30000],
        ["ما تم سداده دفعة 2", 20000],
        ["إجمالي المدفوع", 50000],
        ["المتبقي سداده", 1],
        ["1- Electrical"],
        HEAD,
        ["1.1", "Cables", "m", 10, 10, None, 100, 1000, 1000],
        ["1.2", "Panels", "no", 4, 2, 0.5, 1000, 4000, 1200, "hand-made actual"],
        ["Extra socket", "Extra socket", "no", None, 3, None, 50, 0, 150],
        ["إجمالي Electrical", None, None, None, None, None, None, 5000, 2350],
        ["نسبة الأتعاب — Electrical", None, None, None, None, 0.1, None, None, 235],
        ["2- HVAC"],
        HEAD,
        ["2.1", "Ducts", "m2", 20, 20, None, 30, 600, 600],
    ]

    def test_read(self):
        r = docsheet.read(self.rows, "Invoice 1 Site A")
        self.assertEqual(r["kind"], "invoice")
        self.assertEqual(r["meta"], {"date": "2026-09-23", "client": "Owner", "location": "Site A", "number": "Invoice 01"})
        self.assertEqual(r["paid"], 50000)
        self.assertEqual(r["fees"], {"Electrical": 0.1})
        self.assertEqual([(ln["category"], ln["description"]) for ln in r["lines"]],
                         [("Electrical", "Cables"), ("Electrical", "Panels"), ("Electrical", "Extra socket"), ("HVAC", "Ducts")])
        panels = r["lines"][1]
        self.assertTrue(panels["adjusted"])                                   # the sheet's own actual wins
        self.assertAlmostEqual(panels["executed"] * panels["pct"] * panels["price"], 1200)
        extra = r["lines"][2]
        self.assertEqual((extra["item_no"], extra["qty"]), ("", 0))          # not in the contract: a variation

    def test_columns_in_any_order_and_english(self):
        rows = [["Project", "Acme"], ["Date: 2026-10-01"],
                ["Description", "Qty", "Unit", "Rate", "Item"],
                ["1- Civil"], ["Concrete", 5, "m3", 900, "1.1"]]
        r = docsheet.read(rows, "BOQ")
        self.assertEqual(r["meta"], {"client": "Acme", "date": "2026-10-01"})
        ln = r["lines"][0]
        self.assertEqual((ln["category"], ln["item_no"], ln["qty"], ln["price"], ln["unit"]), ("Civil", "1.1", 5, 900, "m3"))


class Fees(unittest.TestCase):
    def test_fees_in_and_rows(self):
        self.assertEqual(json.loads(a.fees_in("Electrical=10%; *=12%")), {"Electrical": 0.1, "*": 0.12})
        self.assertEqual(json.loads(a.fees_in({"HVAC": 8})), {"HVAC": 0.08})
        self.assertEqual(a.fees_in(""), "")
        rows = a.fee_rows('{"Electrical": 0.1, "*": 0.05}', [{"category": "Electrical", "amount": 1000},
                                                                {"category": "HVAC", "amount": 600}])
        self.assertEqual([(f["category"], f["amount"]) for f in rows], [("Electrical", 100), ("HVAC", 30)])


@unittest.skipUnless(HAVE_XL, "openpyxl not installed")
class InvoiceRoundTrip(unittest.TestCase):
    def test_export_then_import(self):
        src = Database(":memory:")
        src.x("INSERT INTO business_units(code,name,location,customer) VALUES ('PRJ','Project','Site A','Owner')")
        xid = a.save_extract(src, {"bu_code": "PRJ", "number": "Invoice 01", "date": "2026-10-01", "customer": "Owner",
                                   "prev_paid": 50000, "fees": "Electrical=10%; HVAC=8%",
                                   "lines": [{"category": "Electrical", "item_no": "1.1", "description": "Cables", "unit": "m",
                                              "qty": 10, "price": 100},
                                             {"category": "Electrical", "item_no": "1.2", "description": "Panels", "unit": "no",
                                              "qty": 2, "price": 1000, "pct": 0.5},
                                             {"category": "HVAC", "item_no": "2.1", "description": "Ducts", "unit": "m2",
                                              "qty": 20, "price": 30}]})
        x = a.get_extract(src, xid)
        self.assertEqual((x["works_total"], x["fee_total"], x["total"]), (2600, 200 + 48, 2848))
        data, name = documents.invoice_file(src, xid, "xlsx")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / name
            p.write_bytes(data)
            wb = openpyxl.load_workbook(p)
            self.assertEqual(wb.sheetnames, ["Invoice 01"])                 # re-imports by its name
            self.assertEqual((wb.active.freeze_panes or "A1"), "A1")
            read = interchange.read_xlsx(str(p))
        dst = Database(":memory:")
        dst.x("INSERT INTO business_units(code,name,location,customer) VALUES ('PRJ','Project','Site A','Owner')")
        res = interchange.import_data(dst, read)
        self.assertTrue(any("Invoice 01" in n for n in res.get("notes", [])), res)
        nid = dst.one("SELECT id FROM extracts WHERE number='Invoice 01'")["id"]
        y = a.get_extract(dst, nid)
        self.assertEqual((y["works_total"], y["fee_total"], y["total"]), (2600, 248, 2848))
        self.assertEqual(y["fees_map"], {"Electrical": 0.1, "HVAC": 0.08})
        self.assertEqual(y["prev_paid"], x["prev_paid"])



class Country(unittest.TestCase):
    def test_profiles(self):
        db = Database(":memory:")
        p = countries.profile(db)                    # not picked: the layout the app always had
        self.assertEqual((p["country"], p["invoice_style"], p["doc_lang"]), ("", "contract_actual", "ar"))
        self.assertEqual(branding.get(db)["boq_notes"], branding.DEFAULT_BOQ_NOTES)
        db.set_setting("country", "EG")
        p = countries.profile(db)
        self.assertEqual((p["invoice_style"], p["doc_lang"], p["e_invoice"]), ("contract_actual", "ar", "eta"))
        db.set_setting("country", "GB")
        p = countries.profile(db)
        self.assertEqual((p["invoice_style"], p["doc_lang"], p["e_invoice"]), ("simple", "en", ""))
        self.assertEqual(branding.get(db)["boq_notes"], "")        # no country-specific terms where none are known
        db.set_setting("invoice_style", "contract_actual")
        db.set_setting("doc_lang", "ar")
        p = countries.profile(db)
        self.assertEqual((p["invoice_style"], p["doc_lang"]), ("contract_actual", "ar"))
        self.assertTrue(all(len(c["code"]) == 2 and len(c["currency"]) == 3 for c in countries.listing()))

    def test_extract_save_keeps_fees_when_not_sent(self):
        db = Database(":memory:")
        db.x("INSERT INTO business_units(code,name) VALUES ('P','P')")
        xid = a.save_extract(db, {"bu_code": "P", "number": "I-1", "fees": "*=10%",
                                  "lines": [{"description": "W", "qty": 1, "price": 100}]})
        a.save_extract(db, {"bu_code": "P", "number": "I-1", "lines": [{"description": "W", "qty": 2, "price": 100}]}, xid)
        self.assertEqual(a.get_extract(db, xid)["total"], 220)
        a.save_extract(db, {"bu_code": "P", "number": "I-1", "fees": {}, "lines": [{"description": "W", "qty": 2, "price": 100}]}, xid)
        self.assertEqual(a.get_extract(db, xid)["total"], 200)



class LoanStatus(unittest.TestCase):
    def test_imported_open_loan_counts_in_totals(self):
        from erp import loans
        db = Database(":memory:")
        acc = {k: v for k, v in loans.default_accounts(db, "lent").items() if k in ("account", "interest_account", "accrued_account")}
        loans.import_rows(db, [{**acc, "code": "LN-1", "direction": "lent", "party": "P", "start_date": "2026-10-07",
                                "principal": 18000000, "opening_balance": 16500000, "opening_date": "2026-10-07", "status": "open",
                                "interest_type": "none", "schedule": "[]"}])
        self.assertEqual(db.one("SELECT status FROM loans")["status"], "active")
        db.x("UPDATE loans SET status='open'")                 # as an older import left it
        t = loans.register(db)["totals"]["lent"]
        self.assertEqual((t["count"], t["outstanding"]), (1, 165000))
        self.assertEqual(len(loans.register(db, state="active")["rows"]), 1)



class MyDayAttendance(unittest.TestCase):
    def test_linked_day_fills_attendance_and_delete_clears_it(self):
        from erp import auth, payroll, workdays
        db = Database(":memory:")
        auth.create_user(db, {"username": "worker1", "password": "Pass-word-123"})
        auth.create_user(db, {"username": "boss", "password": "Pass-word-123"})
        eid = payroll.save_employee(db, {"name": "Worker", "username": "worker1"})
        with self.assertRaises(a.ValidationError):
            payroll.save_employee(db, {"name": "Other", "username": "worker1"})          # one employee per user
        u = {"username": "worker1"}
        workdays.start(db, u, {})
        att = db.one("SELECT * FROM attendance WHERE employee_id=?", (eid,))
        self.assertEqual((att["status"], att["notes"]), ("present", workdays.MYDAY))
        wid = workdays.finish(db, u, {"report": "done"})
        self.assertIsNotNone(workdays.mine(db, u)["employee"])
        workdays.start(db, {"username": "boss"}, {})                                      # not on the list: kept, no attendance
        self.assertIsNone(workdays.mine(db, {"username": "boss"})["employee"])
        self.assertEqual(db.one("SELECT COUNT(*) n FROM attendance")["n"], 1)
        workdays.delete(db, wid)
        self.assertIsNone(db.one("SELECT 1 FROM attendance WHERE employee_id=?", (eid,)))
        # a manager's own attendance entry is never changed by a day
        db.x("INSERT INTO attendance(date,employee_id,status,hours,notes) VALUES (?,?,?,?,?)", (a.today(), eid, "leave", 0, "approved leave"))
        workdays.start(db, u, {})
        self.assertEqual(db.one("SELECT status FROM attendance WHERE employee_id=?", (eid,))["status"], "leave")



class UniversalDemoAndMethods(unittest.TestCase):
    def test_local_payment_method_only_in_its_country(self):
        db = Database(":memory:")
        self.assertNotIn("Instapay", countries.methods(db))
        db.set_setting("country", "EG")
        self.assertIn("Instapay", countries.methods(db))
        self.assertIn("Instapay", a.PAYMENT_METHODS)          # older records keep their value

    def test_demo_is_universal_and_translated(self):
        from erp import demo, interchange
        from erp.demo_ar import AR
        choices = {str(x) for sh in interchange.SHEETS for c in sh["cols"] for x in (c.get("choices") or [])}
        self.assertEqual(sorted(set(AR) & choices), [])       # codes and choices are never translated
        text = open(Path(__file__).resolve().parent.parent / "erp" / "demo.py", encoding="utf-8").read()
        for local in ("Cairo", "Giza", "Nile", "Egypt", "Zamalek", "Fahmy", "+20 "):
            self.assertNotIn(local, text)
        en, ar = Database(":memory:"), Database(":memory:")
        demo.load(en)
        demo.load(ar, lang="ar")
        names = lambda db: [r["name"] for r in db.q("SELECT name FROM parties ORDER BY id")]  # noqa: E731
        self.assertEqual(len(names(en)), len(names(ar)))
        self.assertIn("James Carter", names(en))
        self.assertIn(AR["James Carter"], names(ar))
        self.assertEqual(ar.setting("company_name"), AR[en.setting("company_name")])
        for db in (en, ar):
            self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])
        tb = lambda db: round(sum(r["n"] for r in db.q("SELECT SUM(debit-credit) n FROM journal_lines GROUP BY account")), 2)  # noqa: E731
        self.assertEqual(a.income_statement(en)["net_profit"], a.income_statement(ar)["net_profit"])    # the same numbers
        self.assertEqual(tb(en), tb(ar))


if __name__ == "__main__":
    unittest.main()
