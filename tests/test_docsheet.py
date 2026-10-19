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


if __name__ == "__main__":
    unittest.main()
