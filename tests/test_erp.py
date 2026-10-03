"""Beyond ERP test-suite (stdlib unittest — run:  python -m unittest discover tests)."""
import http.cookiejar
import json
import sys
import tempfile
import threading
import datetime
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from erp import accounting as a  # noqa: E402
from erp import assets, auth, entries, equity, interchange, reports  # noqa: E402
from erp.db import Database  # noqa: E402

try:
    import openpyxl  # noqa: F401
    HAVE_XL = True
except ImportError:
    HAVE_XL = False


def seeded():
    db = Database(":memory:")
    db.x("INSERT INTO business_units(code,name,basis,fee_pct,overhead_pct) VALUES ('PRJ-1','Villa','Cost-Plus %',0.25,0.6)")
    db.x("INSERT INTO business_units(code,name,basis,overhead_pct) VALUES ('PRJ-2','Flat','Fixed Price',0.4)")
    db.x("INSERT INTO partners(name,default_share) VALUES ('A',0.5),('B',0.5)")
    db.x("INSERT INTO warehouses(name) VALUES ('Site Store')")
    a.record_partner_tx(db, {"partner": "A", "type": "Capital", "amount": 100000, "date": "2026-01-02"})
    a.record_project_cost(db, {"bu_code": "PRJ-1", "description": "Cement", "vendor": "Sup", "qty": 10, "rate": 500,
                               "paid": 3000, "savings": 200, "date": "2026-02-01"})
    a.record_expense(db, {"description": "Rent", "amount": 5000, "account": "6010", "date": "2026-02-01"})
    a.record_payroll(db, {"bu_code": "PRJ-1", "worker": "Ali", "qty": 5, "rate": 300, "paid": 1000, "date": "2026-02-05"})
    a.record_collection(db, {"bu_code": "PRJ-1", "amount": 20000, "date": "2026-02-10"})
    a.stock_move(db, {"type": "Receipt", "warehouse": "Main Store", "item": "Tiles", "qty": 100, "rate": 50,
                      "paid": 5000, "date": "2026-02-11"})
    a.stock_move(db, {"type": "Transfer", "warehouse": "Main Store", "to_warehouse": "Site Store", "item": "Tiles",
                      "qty": 30, "date": "2026-02-12"})
    a.stock_move(db, {"type": "Issue", "warehouse": "Site Store", "item": "Tiles", "qty": 10, "bu_code": "PRJ-1",
                      "date": "2026-02-13"})
    x = a.save_extract(db, {"bu_code": "PRJ-1", "number": "INV-1", "date": "2026-03-01",
                            "lines": [{"description": "Tiling", "qty": 10, "price": 1000}]})
    a.post_extract(db, x)
    a.record_payment(db, {"party": "Sup", "amount": 1000, "date": "2026-03-02"})
    a.record_journal(db, {"memo": "Depreciation", "date": "2026-03-31",
                          "lines": [{"account": "7010", "debit": 500}, {"account": "1490", "credit": 500}]})
    a.record_journal(db, {"memo": "Tax", "date": "2026-03-31",
                          "lines": [{"account": "7050", "debit": 700}, {"account": "2220", "credit": 700}]})
    a.record_partner_tx(db, {"partner": "B", "type": "Distribution", "amount": 2000, "date": "2026-03-31"})
    return db


class Engine(unittest.TestCase):
    def setUp(self):
        self.db = seeded()

    def test_unbalanced_entry_rejected(self):
        with self.assertRaises(a.ValidationError):
            a.post_entry(self.db, date="2026-01-01", kind="Journal Voucher",
                         lines=[{"account": "1020", "debit": 10}, {"account": "4010", "credit": 9}])

    def test_income_statement_waterfall(self):
        pl = a.income_statement(self.db)
        self.assertEqual(pl["net_sales"], 30000.0)
        self.assertEqual(pl["cost_of_sales"], 4800 + 1500 + 500)
        self.assertEqual(pl["ebitda"], round(pl["gross_profit"] - pl["operating_expenses"], 2))
        self.assertEqual(pl["ebit"], round(pl["ebitda"] - 500, 2))
        self.assertEqual(pl["net_profit"], round(pl["ebt"] - 700, 2))

    def test_statements_tie_out(self):
        for check in a.audit(self.db):
            self.assertTrue(check["ok"], check)
        cf = a.cash_flow(self.db, "2026-02-01", "2026-03-31")
        self.assertTrue(cf["reconciles"])
        self.assertTrue(a.trial_balance(self.db, "2026-02-01", "2026-03-31")["balanced"])

    def test_overhead_allocation(self):
        bus = {b["code"]: b for b in a.bu_summaries(self.db)}
        self.assertEqual(bus["PRJ-1"]["allocated_overhead"], 3000.0)  # 60% of 5,000 shared rent
        self.assertEqual(bus["PRJ-2"]["allocated_overhead"], 2000.0)

    def test_account_renumbering_follows_role(self):
        a.save_account(self.db, {"code": "1025", "name": "Bank CIB", "type": "Asset", "subtype": "cash"}, old_code="1020")
        self.assertEqual(self.db.acct("bank"), "1025")
        a.record_collection(self.db, {"bu_code": "PRJ-2", "amount": 10})
        self.assertTrue(all(c["ok"] for c in a.audit(self.db)))

    def test_system_account_cannot_be_deleted(self):
        with self.assertRaises(a.ValidationError):
            a.delete_account(self.db, "1020")

    def test_void_and_reverse(self):
        e = a.record_expense(self.db, {"description": "Oops", "amount": 99})
        a.void_entry(self.db, e, "typo")
        rid = a.reverse_entry(self.db, a.record_expense(self.db, {"description": "Oops2", "amount": 50}))
        self.assertTrue(rid)
        self.assertTrue(all(c["ok"] for c in a.audit(self.db)))

    def test_stock_cannot_go_negative(self):
        with self.assertRaises(a.ValidationError):
            a.stock_move(self.db, {"type": "Issue", "warehouse": "Site Store", "item": "Tiles", "qty": 1000,
                                   "bu_code": "PRJ-1"})

    def test_payment_limited_to_balance(self):
        with self.assertRaises(a.ValidationError):
            a.record_payment(self.db, {"party": "Sup", "amount": 999999})

    def test_voiding_stock_entry_restores_levels(self):
        before = {(r["name"], r["warehouse"]): r["qty"] for r in a.stock_report(self.db)}
        e = a.stock_move(self.db, {"type": "Issue", "warehouse": "Main Store", "item": "Tiles", "qty": 5,
                                   "bu_code": "PRJ-2"})
        a.void_entry(self.db, e)
        after = {(r["name"], r["warehouse"]): r["qty"] for r in a.stock_report(self.db)}
        self.assertEqual(before, after)


class Interchange(unittest.TestCase):
    def test_json_round_trip_is_exact(self):
        db = seeded()
        a.save_account(db, {"code": "1025", "name": "Bank CIB", "type": "Asset", "subtype": "cash"}, old_code="1020")
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()), mode="replace")
        tb1 = {r["code"]: (r["debit"], r["credit"]) for r in a.trial_balance(db)["rows"]}
        tb2 = {r["code"]: (r["debit"], r["credit"]) for r in a.trial_balance(db2)["rows"]}
        self.assertEqual(tb1, tb2)
        self.assertEqual(db2.acct("bank"), "1025")

    def test_import_is_all_or_nothing(self):
        db = Database(":memory:")
        data = {"Business Units": [{"code": "X", "name": "X"}],
                "Operating Expenses": [{"date": "2026-01-01", "description": "ok", "amount": 5},
                                       {"date": "2026-01-01", "description": "bad", "amount": "abc"}]}
        with self.assertRaises(interchange.ImportErrors) as cm:
            interchange.import_data(db, data)
        self.assertEqual(cm.exception.errors[0]["row"], 6)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM business_units")["n"], 0)

    def test_dry_run_changes_nothing(self):
        db = Database(":memory:")
        interchange.import_data(db, {"Business Units": [{"code": "X", "name": "X"}]}, dry_run=True)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM business_units")["n"], 0)

    def test_csv_template_has_bilingual_headers(self):
        csv = interchange.to_csv(Database(":memory:"), "Direct Costs", template=True).decode("utf-8-sig")
        self.assertIn("Date / التاريخ", csv.splitlines()[0])

    @unittest.skipUnless(HAVE_XL, "openpyxl not installed")
    def test_workbook_round_trip(self):
        from erp import workbook
        db = seeded()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "wb.xlsx"
            p.write_bytes(workbook.build_workbook(db))
            data = interchange.read_xlsx(p)
        db2 = Database(":memory:")
        interchange.import_data(db2, data, mode="replace")
        self.assertEqual(a.income_statement(db)["net_profit"], a.income_statement(db2)["net_profit"])
        self.assertEqual(a.balance_sheet(db)["total_assets"], a.balance_sheet(db2)["total_assets"])


class Reports(unittest.TestCase):
    def test_every_report_every_format(self):
        db = seeded()
        for name in ("income_statement", "balance_sheet", "cash_flow", "trial_balance", "general_ledger", "bu_pnl",
                     "payables", "stock", "receivables", "partner_pack"):
            for lang in ("en", "ar"):
                tb = reports.build(db, name, {"date_from": "2026-01-01", "date_to": "2026-12-31", "compare": "quarter",
                                              "lang": lang})
                self.assertTrue(tb["rows"], name)
                self.assertTrue(reports.to_html(tb).startswith("<!doctype html>"))
                self.assertEqual(reports.to_docx(tb)[:2], b"PK")
                if HAVE_XL:
                    self.assertEqual(reports.to_xlsx(tb)[:2], b"PK")

    def test_partner_pack(self):
        from erp import partner_pack as pp
        self.assertEqual(pp.billed_profit(0, 0, -500), -500)          # no revenue: net as is
        self.assertEqual(pp.billed_profit(1000, 200, -50), -50)        # a loss is never deferred
        self.assertAlmostEqual(pp.billed_profit(1000, 250, 400), 300)  # 25% still in work in progress
        db = seeded()
        d = pp.compute(db, "2026-01-01", "2026-12-31")
        net = a.income_statement(db, "2026-01-01", "2026-12-31")["net_profit"]
        self.assertAlmostEqual(d["billed_total"] + d["wip_profit"], net, places=2)
        self.assertAlmostEqual(sum(p["net_profit"] for p in d["projects"]) + d["company_left"], net, places=2)
        bs = a.balance_sheet(db, "2026-12-31")
        self.assertAlmostEqual(d["bs"]["ta"], bs["total_assets"], places=2)
        self.assertTrue(d["bs"]["balanced"])
        self.assertTrue(d["audit_ok"])
        self.assertEqual(reports.REPORT_PERMS["partner_pack"], "partners.equity")
        tb = reports.build(db, "partner_pack", {"date_from": "2026-01-01", "date_to": "2026-12-31", "lang": "ar"})
        self.assertIn("<table", reports.to_html(tb, auto_print=True))
        self.assertEqual(reports.to_xlsx(tb)[:2], b"PK")

    def test_comparative_columns(self):
        tb = reports.income_statement_table(seeded(), "2026-01-01", "2026-06-30", "month")
        self.assertEqual(len(tb["columns"]), 1 + 6)


class Auth(unittest.TestCase):
    def test_password_hash(self):
        h = auth.hash_password("correct horse")
        self.assertTrue(auth.check_password("correct horse", h))
        self.assertFalse(auth.check_password("wrong", h))

    def test_last_admin_protected(self):
        db = Database(":memory:")
        uid = auth.create_user(db, {"username": "boss", "password": "12345678"}, first=True)
        with self.assertRaises(a.ValidationError):
            auth.update_user(db, uid, {"is_admin": False}, acting_user_id=999)


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from erp.server import ERPServer
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Database(Path(cls.tmp.name) / "t.sqlite3")
        cls.srv = ERPServer(("127.0.0.1", 0), cls.db, Path(cls.tmp.name) / "backups")
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.db.close()
        cls.tmp.cleanup()

    def client(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, op, method, path, body=None, headers=True):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"X-Requested-With": "BeyondERP"} if headers else {})})
        try:
            with op.open(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_supplier_discount_api(self):
        op = self.client()
        if self.call(op, "POST", "/api/setup", {"username": "admin", "password": "secret123"})[0] != 200:
            self.assertEqual(self.call(op, "POST", "/api/login", {"username": "admin", "password": "secret123"})[0], 200)
        bu = self.call(op, "POST", "/api/bus", {"code": "SD1", "name": "Disc"})[1]["id"]
        deal = {"bu_id": bu, "description": "Ceramic", "vendor": "Hani", "qty": 1, "rate": 16000, "savings": 1000,
                "paid": 14000, "deal_ref": "HANI-9", "deal_status": "Settled", "account": "5020"}
        st, r = self.call(op, "POST", "/api/docs/direct_cost", deal)
        self.assertEqual(st, 400)
        self.assertIn("Negotiation discount", r["error"])
        self.assertEqual(self.call(op, "POST", "/api/docs/direct_cost", {**deal, "deal_status": "Open"})[0], 200)
        st, d = self.call(op, "GET", "/api/supplier-discount?party=Hani")
        self.assertEqual((st, d["credit_account"], d["owed"], d["deal_ref"]), (200, "5020", 1000, "HANI-9"))
        self.assertEqual(self.call(op, "POST", "/api/docs/supplier_discount", {"party": "Hani", "amount": 1000})[0], 200)
        self.assertEqual(self.call(op, "GET", "/api/supplier-discount?party=Hani")[1]["owed"], 0)

    def test_flow(self):
        admin = self.client()
        self.assertEqual(self.call(admin, "GET", "/api/journal")[0], 401)
        self.assertEqual(self.call(admin, "POST", "/api/setup", {"username": "admin", "password": "secret123"})[0], 200)
        self.assertEqual(self.call(admin, "POST", "/api/setup", {"username": "x", "password": "secret123"})[0], 400)
        self.assertEqual(self.call(admin, "POST", "/api/bus", {"code": "B1", "name": "Unit"}, headers=False)[0], 403)
        st, r = self.call(admin, "POST", "/api/bus", {"code": "B1", "name": "Unit", "overhead_pct": 0.5})
        self.assertEqual(st, 200)
        st, _ = self.call(admin, "POST", "/api/docs/receipt", {"bu_id": r["id"], "amount": 1000})
        self.assertEqual(st, 200)
        st, tb = self.call(admin, "GET", "/api/reports/trial_balance")
        self.assertEqual(st, 200)
        self.call(admin, "POST", "/api/users", {"username": "clerk", "password": "clerk1234",
                                                "perms": {"sales": "edit"}})
        clerk = self.client()
        self.assertEqual(self.call(clerk, "POST", "/api/login", {"username": "clerk", "password": "clerk1234"})[0], 200)
        self.assertEqual(self.call(clerk, "POST", "/api/docs/receipt", {"bu_id": r["id"], "amount": 5})[0], 200)
        self.assertEqual(self.call(clerk, "POST", "/api/docs/expense", {"description": "x", "amount": 5})[0], 403)
        self.assertEqual(self.call(clerk, "GET", "/api/users")[0], 403)
        self.assertEqual(self.call(clerk, "GET", "/api/reports/vouchers?kinds=Receipt%20Voucher")[0], 200)
        self.assertEqual(self.call(clerk, "GET", "/api/reports/vouchers")[0], 403)
        # screen-level permissions: no Purchasing section, but full access to Direct Costs only
        self.call(admin, "POST", "/api/users", {"username": "buyer", "password": "buyer1234",
                                                "perms": {"purchasing": "none", "purchasing.costs": "edit"}})
        buyer = self.client()
        self.call(buyer, "POST", "/api/login", {"username": "buyer", "password": "buyer1234"})
        self.assertEqual(self.call(buyer, "POST", "/api/docs/direct_cost",
                                   {"bu_id": r["id"], "description": "c", "qty": 1, "rate": 5, "paid": 5})[0], 200)
        self.assertEqual(self.call(buyer, "POST", "/api/docs/expense", {"description": "x", "amount": 5})[0], 403)
        self.assertEqual(self.call(buyer, "GET", "/api/docs/payment")[0], 403)
        # manual shared-overhead allocation, capped at 100%
        self.assertEqual(self.call(admin, "PUT", "/api/bus-allocation", {"allocations": {str(r["id"]): 0.7}})[0], 200)
        self.assertEqual(self.call(admin, "PUT", "/api/bus-allocation", {"allocations": {str(r["id"]): 1.2}})[0], 400)
        # deleting: users (never yourself / the last admin), and only records nothing depends on
        me = self.call(admin, "GET", "/api/bootstrap")[1]["user"]
        self.assertIn("yourself", self.call(admin, "DELETE", f"/api/users/{me['id']}")[1]["error"])
        st, tmp = self.call(admin, "POST", "/api/users", {"username": "temp", "password": "temp12345"})
        self.assertEqual(self.call(admin, "DELETE", f"/api/users/{tmp['id']}")[0], 200)
        self.assertIsNone(self.db.one("SELECT 1 FROM users WHERE username='temp'"))
        self.assertEqual(self.call(admin, "POST", "/api/tax-codes", {"code": "TMP", "name": "t", "kind": "vat", "rate": 0.1})[0], 200)
        self.assertEqual(self.call(admin, "DELETE", "/api/tax-codes/TMP")[0], 200)
        self.call(admin, "POST", "/api/parties-master", {"name": "Cust", "type": "customer", "default_vat": "VAT14"})
        self.assertIn("untick Active", self.call(admin, "DELETE", "/api/tax-codes/VAT14")[1]["error"])
        pid = self.db.one("SELECT id FROM parties WHERE name='Cust'")["id"]
        self.call(admin, "POST", "/api/extracts", {"bu_id": r["id"], "number": "INV-D", "customer": "Cust",
                                                    "lines": [{"description": "x", "qty": 1, "price": 5}]})
        self.assertIn("invoice INV-D", self.call(admin, "DELETE", f"/api/parties-master/{pid}")[1]["error"])
        st, mv = self.call(admin, "POST", "/api/docs/stock_move", {"type": "Receipt", "item": "Paint", "qty": 2, "rate": 10, "warehouse": "Main Store", "date": "2026-05-01", "paid": 20})
        self.assertEqual(st, 200, mv)
        iid = self.db.one("SELECT id FROM stock_items WHERE name='Paint'")["id"]
        self.assertIn("movements", self.call(admin, "DELETE", f"/api/items/{iid}")[1]["error"])
        self.db.x("INSERT INTO stock_items(name) VALUES ('Typo')")
        self.assertEqual(self.call(admin, "DELETE", "/api/items/" + str(self.db.one("SELECT id FROM stock_items WHERE name='Typo'")["id"]))[0], 200)
        # "view + add": may create new records on that screen, but not change or delete them
        self.call(admin, "POST", "/api/users", {"username": "store", "password": "store1234",
                                                "perms": {"stock.moves": "add", "stock.warehouses": "add"}})
        store = self.client()
        self.call(store, "POST", "/api/login", {"username": "store", "password": "store1234"})
        st, w = self.call(store, "POST", "/api/warehouses", {"name": "Yard"})
        self.assertEqual(st, 200)
        self.assertEqual(self.call(store, "PUT", f"/api/warehouses/{w['id']}", {"name": "Yard 2"})[0], 403)
        self.assertEqual(self.call(store, "DELETE", f"/api/warehouses/{w['id']}")[0], 403)
        self.assertEqual(self.call(store, "GET", "/api/stock/moves")[0], 200)
        self.assertEqual(self.call(store, "GET", "/api/accounts")[0], 403)
        # offers, documents and branding
        st, o = self.call(admin, "GET", "/api/offers")
        self.assertEqual(st, 200)
        self.assertTrue(o["types"])
        st, new = self.call(admin, "POST", "/api/offers", {**o["types"][0], "type_id": o["types"][0]["id"], "client": "C"})
        self.assertEqual(st, 200)
        req = urllib.request.Request(self.base + f"/api/offers/{new['id']}/file?format=docx")
        with admin.open(req) as resp:
            self.assertEqual(resp.read()[:2], b"PK")
        st, b = self.call(admin, "GET", "/api/branding")
        self.assertEqual(st, 200)
        self.assertTrue(b["images"]["logo"])
        self.assertEqual(self.call(store, "PUT", "/api/branding", {"dark": "#000000"})[0], 403)
        self.assertEqual(self.call(admin, "PUT", "/api/branding", {"dark": "red"})[0], 400)
        # erase: administrator only, typed confirmation, keeps users
        self.assertEqual(self.call(store, "POST", "/api/erase", {"scope": "all", "confirm": "ERASE"})[0], 403)
        self.assertEqual(self.call(admin, "POST", "/api/erase", {"scope": "all", "confirm": "no"})[0], 400)
        st, er = self.call(admin, "POST", "/api/erase", {"scope": "transactions", "confirm": "ERASE"})
        self.assertEqual(st, 200)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM journal_entries")["n"], 0)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM business_units")["n"], 1)
        self.assertEqual(self.call(admin, "POST", "/api/erase", {"scope": "all", "confirm": "erase"})[0], 200)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM business_units")["n"], 0)
        self.assertTrue(self.db.one("SELECT COUNT(*) n FROM accounts")["n"] > 50)
        self.assertEqual(self.call(store, "GET", "/api/stock/moves")[0], 200)  # users survive


class Workspace(unittest.TestCase):
    """Two desktop servers sharing one workspace through a stand-in Supabase."""

    @classmethod
    def setUpClass(cls):
        from erp.server import ERPServer
        sys.path.insert(0, str(Path(__file__).parent))
        import mock_supabase
        cls.sb = mock_supabase.start()
        cls.sb_url = f"http://127.0.0.1:{cls.sb.server_address[1]}"
        cls.tmp = tempfile.TemporaryDirectory()
        cls.nodes = []
        for name in ("a", "b"):
            db = Database(Path(cls.tmp.name) / f"{name}.sqlite3")
            srv = ERPServer(("127.0.0.1", 0), db, Path(cls.tmp.name) / f"bk-{name}")
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            cls.nodes.append((srv, db, f"http://127.0.0.1:{srv.server_address[1]}"))

    @classmethod
    def tearDownClass(cls):
        for srv, db, _ in cls.nodes:
            srv.cloud.stop()
            srv.shutdown()
            srv.server_close()
            db.close()
        cls.sb.shutdown()
        cls.tmp.cleanup()

    call = Api.call
    client = Api.client

    def node_call(self, i, op, method, path, body=None):
        self.base = self.nodes[i][2]
        return self.call(op, method, path, body)

    def dump(self, db):
        out = {t: db.q(f"SELECT * FROM {t} ORDER BY 1") for t in
               ("business_units", "journal_entries", "journal_lines", "users", "offers", "partners", "accounts",
                "settings", "audit_log", "ownership_changes", "fixed_assets", "partner_shares", "loans", "custodies",
                "rent_items", "rent_lots", "rentals", "rental_lines", "rental_handovers", "rent_moves", "rental_charges",
                "products", "product_media", "sales_orders", "sale_lines", "sale_installments", "sale_cheques", "extracts",
                "bank_statements", "bank_lines", "bank_rules", "recurring", "recurring_runs")}
        for u in out["users"]:
            u.pop("pw_hash")          # passwords stay on the device where they were set
        out["audit_log"] = [r for r in out["audit_log"] if r["action"] not in ("login", "setup")]  # device-local
        for r in out["audit_log"]:
            r.pop("id")
        return out

    def test_two_devices_converge(self):
        a, b = self.client(), self.client()
        # device A: local admin, connects and creates the workspace from its books
        self.node_call(0, a, "POST", "/api/setup", {"username": "admin", "password": "secret123"})
        self.assertEqual(self.node_call(0, a, "POST", "/api/bus", {"code": "PRE", "name": "Before sharing"})[0], 200)
        self.assertEqual(self.node_call(0, a, "PUT", "/api/cloud/config", {"url": self.sb_url, "key": "anon"})[0], 200)
        st, r = self.node_call(0, a, "POST", "/api/cloud/signin", {"email": "Owner@x.com", "password": "pw123456", "create": True})
        self.assertEqual(st, 200, r)
        st, r = self.node_call(0, a, "POST", "/api/cloud/claim", {})
        self.assertEqual(st, 200, r)
        self.assertTrue(r["is_admin"])
        # changes now go through the log
        st, bu = self.node_call(0, a, "POST", "/api/bus", {"code": "B1", "name": "Villa"})
        self.assertEqual(st, 200)
        self.assertEqual(len(self.sb.state.all_ops()), 1)
        self.assertEqual(self.node_call(0, a, "POST", "/api/users", {"username": "clerk", "password": "clerk1234",
                                                                     "perms": {"sales": "edit"}})[0], 200)
        self.assertNotIn("clerk1234", json.dumps(self.sb.state.all_ops()))  # passwords never leave the device
        # sign-in problems get plain answers; with "Confirm email" on, sign-up asks the person to open the email first
        st, r = self.node_call(0, a, "POST", "/api/cloud/signin", {"email": "owner@x.com", "password": "wrong-pass"})
        self.assertEqual(st, 400)
        self.assertIn("Wrong email or password", r["error"])
        self.sb.state.confirm_required = True
        st, r = self.node_call(0, a, "POST", "/api/cloud/signin", {"email": "new@x.com", "password": "pw123456", "create": True})
        self.assertIn("confirmation email", r["error"])
        st, r = self.node_call(0, a, "POST", "/api/cloud/signin", {"email": "new@x.com", "password": "pw123456"})
        self.assertIn("confirm your email", r["error"])
        st, r = self.node_call(0, a, "POST", "/api/cloud/signin", {"email": "owner@x.com", "password": "pw123456", "create": True})
        self.assertIn("already exists", r["error"])
        self.sb.state.confirm_required = False
        self.assertEqual(self.node_call(0, a, "POST", "/api/cloud/signin", {"email": "owner@x.com", "password": "pw123456"})[0], 200)
        # a user saved with an email is linked to the workspace; they can then sign in with their username
        st, r = self.node_call(0, a, "POST", "/api/users", {"username": "ali", "email": "Ali@X.com", "password": "ali12345"})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.node_call(0, a, "POST", "/api/users", {"username": "ali2", "email": "ali@x.com"})[1]["error"],
                         "Another user already has this email address")
        self.assertEqual(self.node_call(0, a, "PUT", "/api/cloud/members/link", {"email": "ali@x.com", "username": "ali"})[0], 200)
        self.assertEqual(self.sb.state.username_of("ali@x.com"), "ali")
        self.sb.state.accounts["ali@x.com"] = "alipass1"
        probe = self.nodes[0][0].cloud.client()
        self.assertEqual(probe.sign_in_username("ALI", "alipass1")["user"]["email"], "ali@x.com")
        # this device takes the workspace password too — no separate local password to remember
        d = self.client()
        st, r = self.node_call(0, d, "POST", "/api/login", {"username": "ali", "password": "alipass1"})
        self.assertEqual((st, r["user"]["username"]), (200, "ali"))
        self.assertEqual(self.node_call(0, d, "POST", "/api/login", {"username": "ali", "password": "nope"})[0], 400)
        with self.assertRaises(RuntimeError):
            probe.sign_in_username("ali", "wrong")
        with self.assertRaises(RuntimeError):
            probe.sign_in_username("nobody", "alipass1")
        # the administrator creates a sign-in account from Users — no email, no confirmation — and it works at once
        self.assertEqual(self.node_call(0, a, "POST", "/api/users", {"username": "sara", "_no_password": True,
                                                                     "perms": {"stock": "view"}})[0], 200)
        st, r = self.node_call(0, a, "PUT", "/api/cloud/account", {"username": "sara", "password": "sarapw1"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["email"], "sara@users.beyond-erp.invalid")
        sess = probe.sign_in_username("sara", "sarapw1")
        self.assertEqual(sess["user"]["email"], "sara@users.beyond-erp.invalid")
        from erp.cloud import username_for
        self.assertEqual(username_for(sess["user"]["email"]), "sara")
        st, r = self.node_call(0, a, "PUT", "/api/cloud/account", {"username": "sara", "active": False})
        with self.assertRaises(RuntimeError):   # untick Active: sign-in blocked
            probe.sign_in_username("sara", "sarapw1")
        # A (admin) adds B's account as a gateway member; B joins and gets A's books
        self.assertEqual(self.node_call(0, a, "PUT", "/api/cloud/members",
                                        {"email": "desk@x.com", "gateway": True})[0], 200)
        self.node_call(1, b, "POST", "/api/setup", {"username": "admin", "password": "secret123"})
        self.node_call(1, b, "PUT", "/api/cloud/config", {"url": self.sb_url, "key": "anon"})
        st, r = self.node_call(1, b, "POST", "/api/cloud/signin", {"email": "desk@x.com", "password": "pw123456", "create": True})
        self.assertEqual(st, 200, r)
        st, r = self.node_call(1, b, "POST", "/api/cloud/join", {})
        self.assertEqual(st, 200, r)
        b = self.client()  # sessions are cleared by joining: sign in again (password hashes travel in the copy)
        self.assertEqual(self.node_call(1, b, "POST", "/api/login", {"username": "admin", "password": "secret123"})[0], 200)
        # both devices write "at the same time"; one gets a conflict, pulls and retries transparently
        results = []

        def w(i, op, code):
            results.append(self.nodes[i] and Api.call(type("C", (), {"base": self.nodes[i][2]})(), op, "POST", "/api/bus",
                                                      {"code": code, "name": code})[0])
        ts = [threading.Thread(target=w, args=(i % 2, (a, b)[i % 2], f"C{i}")) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results, [200] * 8)
        st, r = self.node_call(1, b, "POST", "/api/docs/receipt", {"bu_id": bu["id"], "amount": 1500})
        self.assertEqual(st, 200)
        # a user without permission is refused before anything reaches the log
        c = self.client()
        self.node_call(0, c, "POST", "/api/login", {"username": "clerk", "password": "clerk1234"})
        n = len(self.sb.state.all_ops())
        self.assertEqual(self.node_call(0, c, "POST", "/api/docs/expense", {"description": "x", "amount": 5})[0], 403)
        self.assertEqual(len(self.sb.state.all_ops()), n)
        self.assertEqual(self.node_call(0, c, "POST", "/api/docs/receipt", {"bu_id": bu["id"], "amount": 7})[0], 200)
        # gateway relays B's local user; the log records that user, and both copies end up identical
        self.assertEqual(self.sb.state.all_ops()[-2]["app_user"], "admin")
        # partners' equity and fixed assets replay the same on every device (previews are never logged)
        for name in ("P1", "P2"):
            st, r = self.node_call(0, a, "POST", "/api/partners", {"name": name, "default_share": 0.5})
            self.assertEqual(st, 200, r)
            self.node_call(0, a, "POST", "/api/docs/partner_tx", {"partner": name, "type": "Capital", "amount": 50000})
        n = len(self.sb.state.all_ops())
        self.assertEqual(self.node_call(0, a, "POST", "/api/equity-preview/transfer", {"seller": "P1", "buyer": "P3", "share": 10})[0], 200)
        self.assertEqual(len(self.sb.state.all_ops()), n)
        st, r = self.node_call(1, b, "POST", "/api/equity/transfer", {"seller": "P1", "buyer": "P3", "share": 10})
        self.assertEqual(st, 200, r)
        st, r = self.node_call(0, a, "POST", "/api/assets", {"name": "Laptop", "account": "1430", "cost": 36000,
                                                             "life_months": 36, "acquired": "2026-01-10", "pay": "cash"})
        self.assertEqual(st, 200, r)
        st, r = self.node_call(1, b, "POST", "/api/assets/depreciation", {"through": "2026-03"})
        self.assertEqual(st, 200, r)
        # loans and cash custody too: made on one device, paid / settled on the other
        st, r = self.node_call(0, a, "POST", "/api/loans", {"direction": "borrowed", "party": "CIB", "principal": 60000, "rate": 12,
                                                            "interest_type": "declining", "term_months": 6, "start_date": "2026-01-01"})
        self.assertEqual(st, 200, r)
        self.nodes[1][0].cloud.pull()   # previews run on the local copy
        self.assertEqual(self.node_call(1, b, "POST", "/api/loans-preview/pay", {"id": r["id"], "principal": 100})[0], 200)
        st, r2 = self.node_call(1, b, "POST", f"/api/loans/{r['id']}/pay", {"date": "2026-02-01", "principal": 9752.9, "interest": 600})
        self.assertEqual(st, 200, r2)
        st, r = self.node_call(1, b, "POST", "/api/custody", {"holder": "Samir", "amount": 3000, "issued_on": "2026-02-01"})
        self.assertEqual(st, 200, r)
        st, r2 = self.node_call(0, a, "POST", f"/api/custody/{r['id']}/settle",
                                {"date": "2026-02-05", "lines": [{"description": "Cement", "account": "5010", "amount": 1140, "vat_code": "VAT14"}]})
        self.assertEqual(st, 200, r2)
        # equipment rental: stock bought on one device, rented out and delivered from the other
        self.assertEqual(self.node_call(0, a, "POST", "/api/rent-starter", {})[0], 200)
        self.nodes[1][0].cloud.pull()
        fid = self.nodes[1][1].one("SELECT id FROM rent_items WHERE code='SC-FRM2'")["id"]
        self.assertEqual(self.node_call(1, b, "POST", f"/api/rent-items/{fid}/lots", {"qty": 50, "unit_cost": 900, "source": "opening"})[0], 200)
        st, r = self.node_call(0, a, "POST", "/api/rentals", {"client": "C", "lines": [{"kind": "item", "item_id": fid, "qty": 10, "rate": 60}]})
        self.assertEqual(st, 200, r)
        rid = r["id"]
        self.node_call(0, a, "POST", f"/api/rentals/{rid}/po", {"po_number": "P1"})
        self.node_call(0, a, "POST", f"/api/rentals/{rid}/sign", {"signed_us": "x", "signed_client": "y"})
        self.nodes[1][0].cloud.pull()
        st, t = self.node_call(1, b, "GET", f"/api/rentals/{rid}/handover-template?kind=delivery")
        st, r = self.node_call(1, b, "POST", f"/api/rentals/{rid}/handovers", {**t, "signed": 1, "decision": "safe",
                                                                               "checklist": [dict(c, ok="yes") for c in t["checklist"]]})
        self.assertEqual(st, 200, r)
        st, r = self.node_call(1, b, "POST", f"/api/rental-handovers/{r['id']}/confirm", {})
        self.assertEqual(st, 200, r)
        # a unit sold on installments on one device; cheques cleared and money collected on the other
        st, r = self.node_call(0, a, "POST", "/api/products", {"name": "Flat 4", "kind": "unit", "price": 900000})
        self.assertEqual(st, 200, r)
        self.node_call(0, a, "POST", f"/api/products/{r['id']}/media", {"kind": "photo", "data": "data:image/png;base64,iVBORw0KGgo="})
        bu = self.nodes[0][1].one("SELECT code FROM business_units LIMIT 1")["code"]
        st, r = self.node_call(0, a, "POST", "/api/sales", {"customer": "Hany", "bu_code": bu, "date": "2026-03-01", "lines": [{"product_id": r["id"]}],
                                                           "plan": {"type": "installments", "down_pct": 20, "n": 4, "rate": 12}})
        self.assertEqual(st, 200, r)
        sid = r["id"]
        self.assertEqual(self.node_call(0, a, "POST", f"/api/sales/{sid}/confirm", {"collect": 180000, "method": "Cash"})[0], 200)
        self.nodes[1][0].cloud.pull()
        st, d = self.node_call(1, b, "GET", f"/api/sales/{sid}")
        st, r = self.node_call(1, b, "POST", f"/api/sales/{sid}/collect", {"date": "2026-03-02", "method": "Cheque",
                                                                       "cheques": [{"number": "9", "due": "2026-04-01", "amount": d["schedule"][1]["amount"]}]})
        self.assertEqual(st, 200, r)
        cid = self.nodes[1][1].one("SELECT id FROM sale_cheques")["id"]
        self.assertEqual(self.node_call(1, b, "POST", f"/api/sale-cheques/{cid}/clear", {"date": "2026-04-02"})[0], 200)
        # recurring entries posted on one device are never posted again by the other; a statement matched across both
        st, r = self.node_call(0, a, "POST", "/api/recurring", {"name": "Rent", "doc_type": "expense", "start": "2026-01-31", "month_end": 1,
                                                              "mode": "auto", "payload": {"description": "Rent {month}", "amount": 100, "account": "6010"}})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.node_call(0, a, "POST", "/api/recurring-run", {"through": "2026-03-31"})[1]["posted"], 3)
        self.nodes[1][0].cloud.pull()
        self.assertEqual(self.node_call(1, b, "POST", "/api/recurring-run", {"through": "2026-03-31"})[1]["posted"], 0)
        bank = self.nodes[1][1].acct("bank")
        st, r = self.node_call(1, b, "POST", "/api/bank-statements", {"account": bank, "date_from": "2026-01-01", "date_to": "2026-03-31",
                                                                    "lines": [{"date": "2026-01-31", "description": "Rent", "amount": -100}]})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.node_call(1, b, "POST", f"/api/bank-statements/{r['id']}/automatch", {})[1]["matched"], 1)
        for srv, _, _ in self.nodes:
            srv.cloud.pull()
        self.assertEqual(self.dump(self.nodes[0][1]), self.dump(self.nodes[1][1]))
        self.assertEqual(self.nodes[0][1].one("SELECT COUNT(*) n FROM business_units")["n"], 11)
        # offline: changes are refused with a clear message instead of forking the books
        url = self.sb_url
        self.nodes[1][0].cloud.configure("http://127.0.0.1:9", "anon")
        st, r = self.node_call(1, b, "POST", "/api/bus", {"code": "OFF", "name": "x"})
        self.assertEqual(st, 503)
        self.assertIn("workspace", r["error"])
        self.nodes[1][0].cloud.configure(url, "anon")


class Equity(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.x("INSERT INTO partners(name,default_share) VALUES ('A',0.6),('B',0.4)")
        self.db.x("INSERT INTO business_units(code,name) VALUES ('U1','Unit')")
        self.db.x("INSERT INTO partner_shares(partner_id,bu_id,share) VALUES (1,1,0.5),(2,1,0.5)")
        a.record_partner_tx(self.db, {"partner": "A", "type": "Capital", "amount": 60000, "date": "2026-01-01"})
        a.record_partner_tx(self.db, {"partner": "B", "type": "Capital", "amount": 40000, "date": "2026-01-01"})

    def shares(self):
        return {p["name"]: round(p["default_share"], 6) for p in self.db.q("SELECT * FROM partners")}

    def check_books(self):
        self.assertEqual([c["check"] for c in a.audit(self.db) if not c["ok"]], [])

    def test_transfer_admit_exit_and_undo(self):
        db = self.db
        self.assertEqual(equity.preview(db, "transfer", {"seller": "A", "buyer": "C", "share": 1})["ownership"][-1]["after"], 0.01)
        r = equity.post(db, "transfer", {"seller": "A", "buyer": "C", "share": 30, "price": 99999, "date": "2026-02-01"})
        self.assertEqual(self.shares(), {"A": 0.3, "B": 0.4, "C": 0.3})
        self.assertEqual(equity.balances(db, "C")["capital"], 30000)   # half of A's capital moved with half of A's share
        self.assertEqual(equity.balances(db, "A")["capital"], 30000)
        bu = {r_["partner_id"]: r_["share"] for r_ in db.q("SELECT * FROM partner_shares WHERE bu_id=1")}
        self.assertEqual(bu, {1: 0.25, 2: 0.5, 3: 0.25})            # the unit's own shares move in proportion
        equity.post(db, "admit", {"name": "D", "share": 20, "amount": 50000, "basis": "bonus", "date": "2026-03-01"})
        self.assertAlmostEqual(sum(self.shares().values()), 1)
        self.assertEqual(equity.balances(db, "D")["capital"], 30000)   # 20% × (100,000 + 50,000)
        self.assertEqual(equity.total_capital(db), 150000)
        x = equity.post(db, "exit", {"partner": "B", "settlement": 45000, "paid": 5000, "date": "2026-04-01"})
        self.assertEqual(self.shares()["B"], 0)
        self.assertEqual(db.one("SELECT active FROM partners WHERE name='B'")["active"], 0)
        self.assertEqual(equity.balances(db, "B")["due_to"], 40000)
        self.assertAlmostEqual(sum(self.shares().values()), 1)
        self.check_books()
        with self.assertRaises(a.ValidationError):   # only the latest change can be undone
            equity.undo_change(db, r["change"])
        a.void_entry(db, x["id"], "wrong")              # voiding the exit restores B
        self.assertGreater(self.shares()["B"], 0)
        self.assertEqual(db.one("SELECT active FROM partners WHERE name='B'")["active"], 1)
        self.check_books()

    def test_loans_interest_distribution_and_capital_interest(self):
        db = self.db
        equity.post(db, "loan", {"partner": "A", "amount": 36500, "rate": 10, "date": "2026-01-01"})
        p = equity.preview(db, "interest", {"partner": "A", "date": "2026-01-11"})
        self.assertEqual(p["lines"][0]["debit"], 100.0)                 # 10 days × 36,500 × 10% / 365
        equity.post(db, "interest", {"partner": "A", "date": "2026-01-11"})
        with self.assertRaises(a.ValidationError):
            equity.post(db, "interest", {"partner": "A", "date": "2026-01-11"})   # already booked to that day
        self.assertEqual(equity.balances(db, "A")["interest"], 100)
        equity.post(db, "repay", {"partner": "A", "what": "interest", "amount": 100, "date": "2026-01-12"})
        equity.post(db, "contribute", {"partner": "A", "amount": 36500, "source": "loan", "ownership": "by_capital",
                                       "date": "2026-01-13"})
        self.assertEqual(equity.balances(db, "A")["loan"], 0)
        self.assertEqual(self.shares(), {"A": round(96500 / 136500, 6), "B": round(40000 / 136500, 6)})
        equity.post(db, "distribute", {"amount": 1000, "date": "2026-01-31"})
        self.assertEqual(equity.balances(db, "B")["distributions_payable"], round(1000 * 40000 / 136500, 2))
        db.x("UPDATE partners SET capital_interest=0.1 WHERE name='B'")
        rep = a.partner_report(db, "2026-01-01", "2026-12-31")
        self.assertGreater(next(r for r in rep["partners"] if r["name"] == "B")["capital_interest"], 3900)
        self.check_books()

    def test_same_account_both_sides_refused(self):
        with self.assertRaises(a.ValidationError):
            a.record_journal(self.db, {"date": "2026-01-02", "lines": [{"account": "1020", "debit": 5}, {"account": "1020", "credit": 5}]})
        # the same account is fine when the lines belong to different partners (a capital transfer)
        a.record_journal(self.db, {"date": "2026-01-02", "lines": [{"account": "3010", "debit": 5, "party": "A"},
                                                                    {"account": "3010", "credit": 5, "party": "B"}]})

    def test_old_books_get_the_new_accounts(self):
        db = Database(":memory:")
        db.x("DELETE FROM accounts WHERE code IN ('3020','3110')")
        db.x("INSERT INTO accounts(code,name,type,subtype,normal) VALUES ('3020','Mine','Equity','capital','Credit')")
        db.x("DELETE FROM settings WHERE key='coa_v2'")
        db._migrate()
        self.assertEqual(db.one("SELECT name FROM accounts WHERE code='3020'")["name"], "Mine")
        self.assertEqual(db.acct("partner_current"), "3021")
        self.assertEqual(db.acct("legal_reserve"), "3110")

    def test_json_round_trip_keeps_line_parties_and_history(self):
        equity.post(self.db, "transfer", {"seller": "A", "buyer": "C", "share": 30, "date": "2026-02-01"})
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(self.db).encode()), mode="replace")
        for n in ("A", "B", "C"):
            self.assertEqual(equity.balances(db2, n), equity.balances(self.db, n))
        self.assertEqual(len(equity.history(db2)), 1)


class FixedAssets(unittest.TestCase):
    def test_purchase_depreciation_disposal(self):
        db = Database(":memory:")
        a.record_journal(db, {"date": "2026-01-01", "lines": [{"account": "1020", "debit": 900000}, {"account": "3010", "credit": 900000}]})
        aid = assets.save(db, {"name": "Truck", "account": "1420", "acquired": "2026-01-15", "cost": 600000, "salvage": 60000,
                               "life_months": 60, "pay": "payable", "vendor": "Dealer", "paid": 100000, "vat_code": "VAT14"})
        self.assertEqual(a.party_balance(db, db.acct("ap"), "Dealer"), 584000)   # 600,000 + 14% VAT − 100,000 paid
        r = assets.run_depreciation(db, "2026-04")
        self.assertEqual(r["total"], 36000)                              # 4 months × 9,000
        with self.assertRaises(a.ValidationError):
            assets.run_depreciation(db, "2026-04")
        row = assets.register(db)["rows"][0]
        self.assertEqual((row["accumulated"], row["book_value"], row["booked_through"]), (36000, 564000, "2026-04"))
        a.void_entry(db, r["entries"][0], "redo")                       # voided depreciation is booked again next run
        self.assertEqual(assets.run_depreciation(db, "2026-04")["total"], 36000)
        assets.dispose(db, aid, {"date": "2026-06-20", "proceeds": 500000})   # May + June first, then the sale
        row = assets.register(db)["rows"][0]
        self.assertTrue(row["disposed"])
        self.assertEqual(row["accumulated"], 54000)
        self.assertEqual(next(x for x in a.account_totals(db) if x["code"] == "7045")["net"], 4600000)   # loss 46,000.00
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])
        # land is never depreciated; an existing asset can be registered with its past depreciation
        assets.save(db, {"name": "Plot", "account": "1440", "acquired": "2025-01-01", "cost": 100000, "life_months": 0})
        assets.save(db, {"name": "Desk", "account": "1400", "acquired": "2024-01-01", "cost": 12000, "life_months": 60,
                         "opening_depr": 4800})
        p = assets.plan_depreciation(db, "2026-06")
        self.assertEqual([(x["name"], x["from"]) for x in p["rows"]], [("Desk", "2026-01")])   # 24 months already taken
        # the register and its depreciation survive a JSON round trip
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()), mode="replace")
        self.assertEqual([(r["code"], r["accumulated"], r["disposed"]) for r in assets.register(db2)["rows"]],
                         [(r["code"], r["accumulated"], r["disposed"]) for r in assets.register(db)["rows"]])
        self.assertEqual(reports.build(db, "fixed_assets", {})["rows"][-1]["cells"][5], 112000)


class EntryTypes(unittest.TestCase):
    def test_types_menu_and_posting(self):
        db = Database(":memory:")
        types = entries.load(db)
        self.assertIn("legal_reserve", [t["id"] for t in types])
        cash = entries.resolve_side(db, "subtype:cash")
        self.assertIn("1020", cash["choose"])
        eid = entries.post(db, "cash_transfer", {"amount": 500, "debit_account": "1010", "credit_account": "1020"})
        self.assertEqual(a.get_entry(db, eid)["amount"], 500)
        with self.assertRaises(a.ValidationError):
            entries.post(db, "cash_transfer", {"amount": 500, "debit_account": "1020", "credit_account": "1020"})
        entries.save(db, [{"name": "Pay rent", "kind": "Rent", "debit": "6010", "credit": "role:bank", "party": "required"}])
        with self.assertRaises(a.ValidationError):
            entries.post(db, "pay_rent", {"amount": 100})           # party required
        eid = entries.post(db, "pay_rent", {"amount": 100, "party": "Landlord"})
        self.assertEqual(a.get_entry(db, eid)["kind"], "Rent")
        self.assertNotIn("legal_reserve", [t["id"] for t in entries.load(db)])   # removed built-ins stay removed
        entries.save_menu(db, [{"key": "entry:pay_rent", "show": True}, {"key": "doc:journal", "show": True}, {"key": "bad", "show": True}])
        m = entries.menu(db)
        self.assertEqual([x["key"] for x in m if x["show"]], ["entry:pay_rent", "doc:journal"])


class DepreciationPolicy(unittest.TestCase):
    """Company depreciation rules: method, first month, defaults per asset account; changes apply from now on."""

    def test_policy(self):
        from erp import assets as A
        db = seeded()
        fa = db.acct("fixed_asset")
        A.save_policy(db, {"convention": "next_month", "by_account": {fa: {"life_months": 36, "residual_pct": 10}}})
        aid = A.save(db, {"name": "Laptop", "account": fa, "acquired": "2026-01-15", "cost": 36000})
        x = A.get(db, aid)
        self.assertEqual((x["life_months"], x["salvage"], x["depr_start"]), (36, 360000, "2026-02"))
        self.assertEqual(A._row(db, x)["monthly"], 900)                         # (36000 − 3600) ÷ 36
        # declining: front-loaded, still ends exactly at the residual value
        did = A.save(db, {"name": "Crane", "account": fa, "acquired": "2026-01-01", "cost": 120000, "salvage": 0,
                          "life_months": 60, "depr_start": "2026-01", "depr_method": "declining", "depr_factor": 2})
        sch = A.schedule(db, A.get(db, did))
        self.assertGreater(sch["rows"][0]["depreciation"], sch["rows"][-1]["depreciation"])
        self.assertEqual(sch["rows"][-1]["book_value"], 0)
        self.assertEqual(sch["rows"][-1]["year"], "2030")                        # 60 months from 2026-01 → 2030-12
        self.assertEqual(round(sum(r["depreciation"] for r in sch["rows"]), 2), 120000)
        A.run_depreciation(db, "2026-06", [did])
        st = A._row(db, A.get(db, did))
        self.assertEqual(st["booked_through"], "2026-06")
        # straight line: shortening the life re-spreads only what is left
        A.run_depreciation(db, "2026-06", [aid])                               # 2026-02..06 = 5 × 900
        A.save(db, {**A._row(db, A.get(db, aid)), "life_months": 18, "depr_method": "", "depr_factor": ""}, aid)
        r = A._row(db, A.get(db, aid))
        self.assertEqual((r["accumulated"], r["months_left"]), (4500, 13))
        self.assertEqual(r["monthly"], round((32400 - 4500) / 13, 2))
        last = A.schedule(db, A.get(db, aid))["rows"][-1]
        self.assertEqual(last["book_value"], 3600)
        # company default method flips the assets that follow the policy, not the ones with their own
        A.save_policy(db, {"method": "declining", "factor": 1.5, "convention": "next_month",
                           "by_account": {fa: {"life_months": 36, "residual_pct": 10}}})
        self.assertEqual(A._row(db, A.get(db, aid))["method"], "declining")
        self.assertEqual(A._row(db, A.get(db, did))["factor"], 2)
        with self.assertRaises(a.ValidationError):
            A.save_policy(db, {"method": "sum_of_digits"})
        self.assertTrue(A.preview_schedule(db, {"account": fa, "cost": 1000, "acquired": "2026-03-01"})["rows"])
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])


class ProductSales(unittest.TestCase):
    """Catalog with media, sales on payment plans (installments with interest), cheques, delivery, refund and cancel."""

    def setUp(self):
        from erp import sales
        self.S, self.db = sales, seeded()
        self.unit = sales.save_product(self.db, {"name": "Apartment A-301", "kind": "unit", "price": 2400000, "bu_code": "PRJ-1",
                                                 "specs": [["Area", "150 m²"]], "cost": 1500000, "inventory_account": "1210"})
        self.item = sales.save_product(self.db, {"name": "Tiles box", "kind": "item", "price": 120, "stock_item": "Tiles", "unit": "box"})

    def test_schedule(self):
        rows, markup = self.S.build_schedule(240000000, {"type": "installments", "down_pct": 10, "n": 24, "rate": 10,
                                                          "extras": [{"label": "On delivery", "pct": 10, "due": "2027-06-01"}]}, "2026-03-01")
        self.assertEqual(markup, 38400000)                     # (2.4M − 240k − 240k) × 10% × 2 years
        self.assertEqual(sum(r["amount"] for r in rows), 240000000 + markup)
        self.assertEqual(rows[0]["kind"], "down")
        self.assertEqual([r for r in rows if r["kind"] == "installment"][-1]["due"], "2028-03-01")
        rows, _ = self.S.build_schedule(100000, {"type": "installments", "n": 3, "round_to": 10}, "2026-01-31")
        self.assertEqual([r["amount"] for r in rows], [33000, 33000, 34000])
        self.assertEqual([r["due"] for r in rows], ["2026-02-28", "2026-03-28", "2026-04-28"])  # month-end clamps
        with self.assertRaises(a.ValidationError):
            self.S.build_schedule(1000, {"type": "custom", "rows": [{"due": "2026-01-01", "amount": 5}]}, "2026-01-01")

    def test_installment_sale_on_delivery(self):
        S, db = self.S, self.db
        sid = S.save(db, {"customer": "Mona", "bu_code": "PRJ-1", "date": "2026-03-01", "revenue_on": "delivery", "lines": [{"product_id": self.unit}],
                          "plan": {"type": "installments", "down_pct": 10, "n": 24, "rate": 10, "first_due": "2026-04-01"}})
        self.assertEqual(S.get_product(db, self.unit)["status"], "reserved")
        with self.assertRaises(a.ValidationError):                          # the unit cannot be sold twice
            S.save(db, {"customer": "X", "bu_code": "PRJ-1", "lines": [{"product_id": self.unit}]})
        S.confirm(db, sid, {"collect": 240000, "method": "Bank Transfer"})
        d = S.detail(db, sid, "2026-03-15")
        self.assertEqual((d["status"], d["paid"], d["invoiced"]), ("confirmed", 240000, False))
        self.assertEqual(a.party_balance(db, db.acct("client_advances"), "Mona"), 240000)
        amt = d["schedule"][1]["amount"]
        S.collect(db, sid, {"method": "Cheque", "date": "2026-03-02", "cheques": [{"number": "1", "due": "2026-04-01", "amount": amt},
                                                                                {"number": "2", "due": "2026-05-01", "amount": amt}]})
        c1, c2 = S.detail(db, sid)["cheques"]
        self.assertEqual(S.detail(db, sid, "2026-04-15")["schedule"][1]["status"], "cheque")
        S.cheque(db, c1["id"], "clear", {"date": "2026-04-02"})
        S.cheque(db, c2["id"], "bounce", {"date": "2026-05-03"})
        S.deliver(db, sid, {"date": "2026-07-01"})
        d = S.detail(db, sid, "2026-07-01")
        self.assertEqual((d["status"], d["paid"], d["invoiced"], d["overdue_count"]), ("delivered", 348000, True, 2))
        self.assertAlmostEqual(d["interest_earned"], round(432000 * 348000 / 2832000, 2), places=2)
        self.assertEqual(a.party_balance(db, db.acct("client_advances"), "Mona"), 0)
        self.assertEqual(S.get_product(db, self.unit)["status"], "delivered")
        tb = {r["code"]: r for r in a.trial_balance(db)["rows"]}
        self.assertEqual(tb["5005"]["debit"], 1500000)              # cost of the unit
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])
        self.assertEqual(S.installments(db, "2026-07-01", "overdue")["totals"]["left"], 216000)
        from erp import interchange
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()), mode="replace")
        d2 = S.detail(db2, sid, "2026-07-01")                              # payments, cheques and interest survive a restore
        self.assertEqual((d2["paid"], d2["interest_earned"], d2["overdue_count"], len(d2["entries"])),
                         (d["paid"], d["interest_earned"], 2, len(d["entries"])))

    def test_item_sale_refund_cancel_and_backup(self):
        from erp import interchange, sales_docs
        S, db = self.S, self.db
        sid = S.save(db, {"customer": "Shop client", "bu_code": "PRJ-1", "date": "2026-03-05", "vat_code": "VAT14",
                          "lines": [{"product_id": self.item, "qty": 10, "discount": 50}]})
        self.assertEqual(S.detail(db, sid)["total"], 1311)                  # 10 × 115 + 14% VAT
        S.confirm(db, sid, {"collect": 1311, "method": "Cash"})
        stock = lambda: sum(r["qty"] for r in a.stock_report(db))  # noqa: E731
        self.assertEqual(stock(), 80)
        d = S.detail(db, sid)
        self.assertEqual((d["balance"], d["invoice"]["status"]), (0, "posted"))
        for k in ("quote", "contract", "statement"):
            self.assertIn("SO-0001", sales_docs.sale_html(db, sid, k, "ar"))
        mid = S.add_media(db, self.unit, {"kind": "photo", "data": "data:image/png;base64,iVBORw0KGgo="})
        S.add_media(db, self.unit, {"kind": "model", "url": "https://example.com/flat.glb", "title": "3D"})
        self.assertEqual(S.product_row(db, S.get_product(db, self.unit))["cover"], mid)
        self.assertIn("<img", sales_docs.product_html(db, self.unit))
        with self.assertRaises(a.ValidationError):
            S.cancel(db, sid, {"date": "2026-03-06"})                          # money must be refunded first
        S.refund(db, sid, {"amount": 1000, "keep": 311, "method": "Cash", "date": "2026-03-06"})
        S.cancel(db, sid, {"date": "2026-03-06"})
        self.assertEqual((S.get(db, sid)["status"], stock()), ("cancelled", 90))
        tb = {r["code"]: r for r in a.trial_balance(db)["rows"]}
        self.assertEqual(tb["4090"]["credit"], 311)                  # cancellation charge kept
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])
        s2 = S.save(db, {"customer": "B", "bu_code": "PRJ-1", "lines": [{"product_id": self.unit}], "date": "2026-04-01"})
        S.confirm(db, s2, {})
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()), mode="replace")
        self.assertEqual(S.detail(db2, "SO-0002")["balance"], 2400000)
        self.assertEqual(S.detail(db2, "SO-0001")["paid"], S.detail(db, "SO-0001")["paid"])
        self.assertEqual(S.get_product(db2, "P-0001")["status"], "sold")
        self.assertEqual(len(S.media_list(db2, S.get_product(db2, "P-0001")["id"])), 2)


class LegacyJournalWorkbook(unittest.TestCase):
    """V1.3+ workbooks: the General Journal is the source of truth; archive sheets are ignored; codes are remapped."""

    @unittest.skipUnless(HAVE_XL, "openpyxl not installed")
    def test_v13_journal(self):
        import openpyxl
        import tempfile
        from erp import legacy, interchange
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Master Project Register"
        ws.append(["Project ID", "Client Name", "Project Type", "Location", "Total Area (Sqm)", "Start Date", "Status",
                   "Total Contract Value", "Billing Basis / Fee Type", "Supervision Fee %"])
        ws.append(["PRJ-2026-001", "Villa A", "Villa", "X", 100, "2026-01-01", "In Progress", 0, "Cost-Plus %", 0.25])
        gj = wb.create_sheet("General Journal")
        gj.append(["BEYOND | GENERAL JOURNAL"])
        gj.append(["JE #", "Date", "Source / Type", "Project", "Stage", "Account", "Description", "Vendor / Party",
                   "Deal Ref", "Qty", "Unit", "Rate", "Debit", "Credit"])
        for r in (["JE-1", "2026-01-02", "Partner funding (auto)", None, None, "1020", "Funding", "P1", None, None, None, None, 1000, None],
                  ["JE-1", "2026-01-02", "Partner funding (auto)", None, None, "3150", "Funding", "P1", None, None, None, None, None, 1000],
                  ["JE-2", "2026-01-05", "Project cost", "PRJ-2026-001", "All", "5020", "Works", "Sub", "SUB-1", 1, "Task", 600, 600, None],
                  ["JE-2", "2026-01-05", "Project cost", "PRJ-2026-001", "All", "1020", "Works", "Sub", "SUB-1", None, None, None, None, 400],
                  ["JE-2", "2026-01-05", "Project cost", "PRJ-2026-001", "All", "2010", "Works", "Sub", "SUB-1", None, None, None, None, 200],
                  ["JE-3", "2026-01-06", "Custody", None, None, "1320", "Custody", "Ali", None, None, None, None, 100, 0],
                  ["JE-3", "2026-01-06", "Custody", None, None, "1020", "Custody", "Ali", None, None, None, None, 0, 100],
                  ["JE-4", "2026-01-07", "Accrual (auto)", "PRJ-2026-001", "All", "1150", "Accrued", None, None, None, None, None, 0, None],
                  ["JE-4", "2026-01-07", "Accrual (auto)", "PRJ-2026-001", "All", "4010", "Accrued", None, None, None, None, None, None, 0]):
            gj.append(r)
        arch = wb.create_sheet("Project Costing (COGS)")   # archive: must not be imported again
        arch.append(["Date", "Project ID", "Cost Category", "Item Description", "Vendor/Supplier", "Quantity", "Unit Cost", "Total Cost"])
        arch.append(["2026-01-05", "PRJ-2026-001", "Subcontractor", "Works", "Sub", 1, 600, 600])
        reg = wb.create_sheet("Invoice Register")
        reg.append(["Invoice No", "Date", "Project", "Client", "Status"])
        reg.append(["INV-1", "2026-01-10", "PRJ-2026-001", "Villa A", "Draft"])
        il = wb.create_sheet("Invoice Lines")
        il.append(["Invoice No", "Item No", "Category", "Sub-category", "Description", "Unit", "Contract Qty", "Qty this invoice", "Rate"])
        il.append(["INV-1", "1.1", "Works", None, "Walls", "m2", 10, 4, 50])
        il.append(["INV-1", "V-01", "Works", "Variation", "Extra", "LS", None, 1, 300])
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            wb.save(f.name)
            data = legacy.read_legacy_workbook(f.name)
        db = Database(":memory:")
        interchange.import_data(db, data, mode="replace")
        tb = {r["code"]: round(r["debit"] - r["credit"], 2) for r in a.trial_balance(db)["rows"]}
        self.assertEqual((tb["1020"], tb["3010"], tb["5020"], tb["2010"], tb["1035"]), (500, -1000, 600, -200, 100))
        self.assertNotIn("1320", tb)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM journal_entries")["n"], 3)        # zero auto-accrual skipped
        self.assertEqual(a.get_entry(db, db.one("SELECT id FROM journal_entries WHERE ref='JE-2'")["id"])["meta"]["deal_ref"], "SUB-1")
        self.assertEqual([(x["number"], x["total"]) for x in a.list_extracts(db)], [("INV-1", 500)])
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])

    @unittest.skipUnless(HAVE_XL, "openpyxl not installed")
    def test_v14_invoice_sheet_new_accounts_and_measured_basis(self):
        """V1.4: no Invoice Lines sheet (the register points at 'Invoice 1'), new accounts in the chart, Measured basis."""
        import openpyxl
        import tempfile
        from erp import legacy, interchange
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Master Project Register"
        ws.append(["Project ID", "Client Name", "Project Type", "Location", "Total Area (Sqm)", "Start Date", "Status",
                   "Total Contract Value", "Billing Basis / Fee Type", "Supervision Fee %"])
        ws.append(["PRJ-2026-002", "Villa B", "Villa", "Palm Hills", 950, "2026-09-17", "In Progress", 0, "Measured (BOQ rates)", 0.5757])
        ws.append(["PRJ-2026-003", "Flat C", "Flat", "Cairo", 100, "2026-09-20", "In Progress", 0, "Cost-Plus %", 0.25])
        ca = wb.create_sheet("Cost Allocation")
        ca.append(["BEYOND DESIGN | COST ALLOCATION RULES"])
        ca.append(["Allocation rule", "Billable to client?", "Pooled amount (from journal)", "PRJ-2026-002", "PRJ-2026-003"])
        ca.append(["Even split", "No", 0, 0.75, 0.25])
        coa = wb.create_sheet("Chart of Accounts")
        coa.append(["Code", "Account Name", "Type", "Normal", "Income Statement line"])
        coa.append(["5040", "Site Preparation", "COGS", "Debit", "Cost of sales"])
        coa.append(["5045", "Site Survey", "COGS", "Debit", "Cost of sales"])
        gj = wb.create_sheet("General Journal")
        gj.append(["JE #", "Date", "Source / Type", "Project", "Stage", "Account", "Description", "Vendor / Party",
                   "Deal Ref", "Qty", "Unit", "Rate", "Debit", "Credit", "Note", "Alloc Rule", "Account name", "Entry check", "dist #"])
        for r in (["JE-1", "2026-09-23", "Payroll", "PRJ-2026-002", "All", "5045", "Survey", "Eng", None, 1, "LS", 2900, 2900, None],
                  ["JE-1", "2026-09-23", "Payroll", "PRJ-2026-002", "All", "1020", "Survey", "Eng", None, None, None, None, None, 2900],
                  ["JE-2", "2026-09-29", "Client advance", "PRJ-2026-002", "All", "1020", "Advance", "Client", None, None, None, None, 50000, None],
                  ["JE-2", "2026-09-29", "Client advance", "PRJ-2026-002", "All", "2100", "Advance", "Client", None, None, None, None, None, 50000]):
            gj.append(r)
        reg = wb.create_sheet("Invoice Register")
        reg.append(["Invoice No", "Date", "Project", "Client", "Location", "Type", "Status", "Gross this invoice", "Posted to journal?"])
        reg.append(["Invoice 01", "2026-09-23", "PRJ-2026-002", "Villa B", "Palm Hills", "Interim", "Draft", 3500, "No — draft"])
        inv = wb.create_sheet("Invoice 1")
        inv.append(["BEYOND DESIGN", None, "23/9/2026", "التاريخ"])
        inv.append(["Villa B", "المشروع"])
        inv.append(["Palm Hills", "الموقع"])
        inv.append(["Invoice  01", "الاصدار"])
        inv.append(["1- أعمال الهدم والبناء"])
        inv.append(["م", "البيان", "الوحدة", "الكميه بالعقد", "المنفذ فعلي", "السعر", "الاجمالي بالعقد", "الإجمالي الفعلي"])
        inv.append([1.1, "Walls", "م٢", 10, 4, 500, 5000, 2000])
        inv.append([1.2, "Not started", "م٢", 5, None, 100, 500, None])
        inv.append([None, "أعمال مستجدة: extra slab", "مقطوعية", 1, 1, 1500, 0, 1500])
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            wb.save(f.name)
            data = legacy.read_legacy_workbook(f.name)
        self.assertEqual(data["Business Units"][0]["basis"], "Fixed Price")
        self.assertEqual([b["overhead_pct"] for b in data["Business Units"]], [0.75, 0.25])   # the sheet's first rule
        self.assertEqual([(x["item_no"], x["qty"], x["price"]) for x in data["Invoice Lines"]],
                         [("1.1", 4, 500), ("V-01", 1, 1500)])
        db = Database(":memory:")
        interchange.import_data(db, data, mode="replace")
        tb = {r["code"]: round(r["debit"] - r["credit"], 2) for r in a.trial_balance(db)["rows"]}
        self.assertEqual((tb["5045"], tb["2100"]), (2900, -50000))
        self.assertEqual(db.one("SELECT kind FROM journal_entries WHERE ref='JE-2'")["kind"], "Receipt Voucher")
        self.assertEqual([(x["number"], x["total"]) for x in a.list_extracts(db)], [("Invoice 01", 3500)])


class DemoCompany(unittest.TestCase):
    """The demo company loads in one go through the import sheets and touches every module; its workbook re-imports."""

    def test_demo(self):
        from erp import demo, interchange, sales, bankrec, recurring, loans, custody, rentals
        db = Database(":memory:")
        demo.load(db, "admin")
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])
        kinds = {r["kind"] for r in db.q("SELECT DISTINCT kind FROM journal_entries")}
        for k in ("Direct Cost", "Operating Expense", "Payroll Accrual", "Receipt Voucher", "Payment Voucher", "Sales Invoice",
                  "Goods Receipt", "Material Issue", "Stock Transfer" if False else "Stock Adjustment", "Depreciation", "Loan Received",
                  "Loan Repayment", "Custody Issued", "Custody Settlement", "Rental Deposit", "Cheque Cleared", "Cheque Bounced",
                  "Installment Interest", "Bank Entry", "Partner Loan", "Share Transfer", "Supplier Discount", "Asset Purchase"):
            self.assertIn(k, kinds)
        self.assertTrue(loans.register(db)["rows"] and custody.register(db)["rows"] and rentals.list_rentals(db)["rows"])
        self.assertTrue(any(s["status"] == "reconciled" for s in bankrec.overview(db)["statements"]))
        self.assertTrue(recurring.listing(db) and sales.register(db)["rows"])
        with self.assertRaises(a.ValidationError):
            demo.load(db)                                   # never on top of real books
        if HAVE_XL:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
                f.write(demo.workbook_bytes(lambda: Database(":memory:")))
                f.flush()
                data = interchange.read_xlsx(f.name)
            db2 = Database(":memory:")
            interchange.import_data(db2, data)
            self.assertEqual([c["check"] for c in a.audit(db2) if not c["ok"]], [])
            self.assertTrue(db2.one("SELECT 1 FROM bank_lines"))

        self.assertTrue(db.one("SELECT 1 FROM rent_items WHERE code='SC-FRM2' AND monthly_rate>0"))  # calculator codes priced

    def test_scaffold_calculator_formulas_match_the_app(self):
        """The Excel calculator sheet uses the app's own model, translated formula by formula."""
        import math
        import re
        from erp import rentals
        spec = rentals.FRAME_MODEL
        for vals in ({}, {"height": 24, "spare_pct": 10, "length": 41}, {"board_levels": 0, "tubes_per_tie": 2}):
            names = {r["key"]: vals.get(r["key"], r["value"]) for r in spec["inputs"] + spec["params"]}
            refs = {k: f"V_{k}" for k in names}
            for r in spec["derived"]:
                refs[r["key"]] = f"V_{r['key']}"
                names[r["key"]] = rentals.safe_eval(r["expr"], names)
            env = {f"V_{k}": v for k, v in names.items()}
            env.update(ROUNDUP=lambda x, n: math.ceil(x), IF=lambda c, a, b: a if c else b)
            for r in spec["derived"] + [{"expr": c["expr"]} for c in spec["components"]]:
                xl = re.sub(r"(?<![<>!=])=(?!=)", "==", rentals.to_excel(r["expr"], refs)).replace("<>", "!=").replace("^", "**")
                self.assertAlmostEqual(eval(xl, env), rentals.safe_eval(r["expr"], names), msg=r["expr"])  # noqa: S307
        if HAVE_XL:
            from erp import workbook
            import io as _io2
            import openpyxl as _ox
            wb = _ox.load_workbook(_io2.BytesIO(workbook.build_workbook(Database(":memory:"), template=True)))
            ws = wb["Scaffold Calculator"]
            self.assertIn("SC-FRM2", [c.value for c in ws["A"]])

class StatementsAndBudgets(unittest.TestCase):
    """A contact's statement with aging, and budgets against actual results."""

    def test_statement_and_aging(self):
        from erp import statements, reports
        db2 = Database(":memory:")
        from erp import demo
        demo.load(db2)
        st = statements.statement(db2, "Atlas Builders")
        self.assertAlmostEqual(st["closing"], st["opening"] + st["debits"] - st["credits"], places=2)
        self.assertAlmostEqual(sum(st["aging"]), st["closing"], places=2)
        self.assertEqual(st["closing"], sum(b["balance"] for b in st["balances"]))
        ag = statements.aging(db2)
        self.assertAlmostEqual(sum(ag["receivable"]["aging"]), ag["receivable"]["balance"], places=2)
        self.assertTrue(ag["payable"]["balance"] < 0 < ag["receivable"]["balance"])
        for name, p in (("party_statement", {"party": "Atlas Builders", "lang": "ar"}), ("aging", {"side": "payable"})):
            self.assertTrue(reports.build(db2, name, p)["rows"])
        with self.assertRaises(a.ValidationError):
            statements.statement(db2, "")

    def test_budgets(self):
        from erp import budgets, interchange
        db = Database(":memory:")
        from erp import demo
        demo.load(db)
        y = int(a.today()[:4])
        r = budgets.report(db, y, None, 12)
        rev = next(s for s in r["sections"] if s["type"] == "Revenue")
        self.assertEqual(rev["total"]["year_budget"], 7200000)
        exp = next(s for s in r["sections"] if s["type"] == "Expense")
        rent = next(x for x in exp["rows"] if x["account"] == "6030")
        self.assertEqual(rent["year_budget"], 36000)
        self.assertEqual(rent["variance"], round(36000 - rent["actual"], 2))          # a cost under budget is favourable
        budgets.save(db, y, 0, [{"account": "6020", "total": 1000}])
        g = {x["code"]: x for x in budgets.grid(db, y, 0)["rows"]}
        self.assertEqual(g["6020"]["total"], 1000)
        self.assertTrue(budgets.seed(db, y + 1, 0, y, 10))
        with self.assertRaises(a.ValidationError):
            budgets.save(db, y, 0, [{"account": "1010", "total": 5}])                 # balance-sheet accounts have no budget
        rows = interchange.export_data(db)["Budgets"]
        db2 = Database(":memory:")
        interchange.import_data(db2, {"Budgets": rows})
        self.assertEqual(budgets.export_rows(db2), budgets.export_rows(db))

class Attachments(unittest.TestCase):
    """Files kept with vouchers and records; they survive a JSON backup and restore."""

    def test_attach_and_restore(self):
        import base64
        import json as _json
        from erp import attachments, demo, interchange
        db = Database(":memory:")
        demo.load(db)
        eid = db.one("SELECT id FROM journal_entries WHERE kind='Operating Expense' ORDER BY id LIMIT 1")["id"]
        pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 test").decode()
        a1 = attachments.add(db, "entry", eid, {"data": pdf, "title": "Supplier invoice.pdf"}, "admin")
        attachments.add(db, "loan", db.one("SELECT code FROM loans LIMIT 1")["code"], {"url": "https://example.com/contract", "title": "Contract"})
        attachments.add(db, "party", db.one("SELECT id FROM parties WHERE name='Atlas Builders'")["id"], {"data": pdf})
        self.assertEqual(a.get_entry(db, eid)["files"], 1)
        self.assertEqual(attachments.get(db, a1)["size"], 13)
        attachments.rename(db, a1, "Invoice 77.pdf")
        with self.assertRaises(a.ValidationError):
            attachments.add(db, "entry", eid, {"url": "ftp://x"})
        with self.assertRaises(a.ValidationError):
            attachments.add(db, "entry", 999999, {"data": pdf})
        with self.assertRaises(a.ValidationError):
            attachments.add(db, "entry", eid, {"data": "data:application/pdf;base64," + base64.b64encode(b"x" * (attachments.MAX_FILE + 1)).decode()})
        backup = _json.loads(interchange.to_json(db))
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(_json.dumps(backup).encode()))
        titles = sorted(r["title"] for r in db2.q("SELECT title FROM attachments"))
        self.assertEqual(titles, ["Contract", "File", "Invoice 77.pdf"])
        e2 = db2.one("SELECT owner_id FROM attachments WHERE title='Invoice 77.pdf'")["owner_id"]
        self.assertEqual(db2.one("SELECT memo FROM journal_entries WHERE id=?", (e2,))["memo"],
                         db.one("SELECT memo FROM journal_entries WHERE id=?", (eid,))["memo"])
        attachments.delete(db, a1)
        self.assertEqual(a.get_entry(db, eid)["files"], 0)

class PurchaseOrders(unittest.TestCase):
    """Order -> approve -> receive in parts (vouchers posted) -> close; restore from a JSON backup."""

    def test_po_flow(self):
        import json as _json
        from erp import demo, interchange, purchasing as P
        db = Database(":memory:")
        demo.load(db)
        self.assertEqual(db.one("SELECT status FROM purchase_orders WHERE code='PO-D100'")["status"], "approved")
        db.set_setting("po_self_approve_limit", "10000")
        pid = P.save(db, {"supplier": "Spark Electric", "bu_code": "PRJ-D01", "vat_code": "VAT14",
                          "lines": [{"kind": "cost", "description": "Cable 4 mm", "qty": 10, "unit": "roll", "rate": 1500},
                                    {"kind": "expense", "description": "Site lighting", "qty": 1, "rate": 800}]}, "clerk")
        with self.assertRaises(a.ValidationError):
            P.receive(db, pid, {"lines": [{"line_id": 1, "qty": 1}]})          # not approved yet
        with self.assertRaises(a.ValidationError):
            P.approve(db, pid, "clerk")                                        # above the self-approval limit
        P.approve(db, pid, "boss")
        ln = P.get(db, pid)["lines"]
        with self.assertRaises(a.ValidationError):
            P.receive(db, pid, {"lines": [{"line_id": ln[0]["id"], "qty": 11}]})
        before = a.party_balance(db, db.acct("ap"), "Spark Electric")
        ids = P.receive(db, pid, {"lines": [{"line_id": ln[0]["id"], "qty": 4}]}, "clerk")
        e = a.get_entry(db, ids[0])
        self.assertEqual((e["kind"], e["ref"], e["amount"]), ("Direct Cost", P.get(db, pid)["code"], 6840.0))
        self.assertAlmostEqual(a.party_balance(db, db.acct("ap"), "Spark Electric") - before, 6840.0)
        po = P.get(db, pid)
        self.assertEqual((po["status"], po["open_value"]), ("partial", 9800.0))
        a.void_entry(db, ids[0], "wrong", "boss")                              # a voided receipt is on order again
        self.assertEqual(P.get(db, pid)["open_value"], 15800.0)
        P.set_status(db, pid, "closed", "rest not needed")
        self.assertEqual(P.get(db, pid)["open_value"], 0)
        with self.assertRaises(a.ValidationError):
            P.set_status(db, pid, "cancelled")
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()))
        self.assertEqual(sorted((r["code"], r["status"]) for r in db2.q("SELECT code, status FROM purchase_orders")),
                         sorted((r["code"], r["status"]) for r in db.q("SELECT code, status FROM purchase_orders")))
        live = "SELECT COUNT(*) n FROM po_receipts r JOIN journal_entries e ON e.id=r.entry_id WHERE e.status='posted'"
        self.assertEqual(db2.one(live)["n"], db.one(live)["n"])
        self.assertEqual({r["code"]: r["open_value"] for r in P.listing(db2)["rows"]}, {r["code"]: r["open_value"] for r in P.listing(db)["rows"]})

class ServiceSubscriptions(unittest.TestCase):
    """Maintenance / warranty / software subscriptions: invoices per period, deferred revenue, visits, add-ons."""

    def test_subscription_cycle(self):
        from erp import demo, interchange, services as S
        db = Database(":memory:")
        demo.load(db)
        self.assertTrue(S.listing(db)["mrr"] > 0)
        p = S.save_plan(db, {"name": "Yearly maintenance", "price": 12000, "period_months": 12, "visits": 2, "extra_visit_price": 500})
        m = S.save_plan(db, {"name": "Support", "kind": "software", "price": 1000, "period_months": 1})
        a1 = S.save(db, {"customer": "Laila Samir", "plan_id": p, "covers": "Villa 3", "start": "2025-01-10", "term_months": 12, "auto_renew": 0})
        s2 = S.save(db, {"customer": "Karim Fahmy", "plan_id": m, "start": "2025-01-01"})
        S.save(db, {"customer": "Karim Fahmy", "plan_id": m, "parent_id": s2, "title": "Extra module", "price": 300, "period_months": 1, "start": "2025-01-01"})
        for k in range(3):
            S.save_visit(db, a1, {"date": f"2025-0{3 + k}-05", "description": "Check"})
        third = db.one("SELECT charge FROM sub_visits WHERE sub_id=? ORDER BY date DESC LIMIT 1", (a1,))["charge"]
        self.assertEqual(third, 50000)                                                  # beyond the 2 included visits
        r = S.run(db, "2025-03-31")
        inv = db.q("SELECT x.number FROM sub_invoices i JOIN extracts x ON x.id=i.extract_id WHERE i.sub_id=?", (s2,))
        self.assertEqual(len(inv), 3)                                                   # Jan, Feb, Mar (add-on on the same invoices)
        self.assertEqual(db.one("SELECT COUNT(DISTINCT extract_id) n FROM sub_invoices i JOIN subscriptions s ON s.id=i.sub_id "
                                "WHERE s.customer='Karim Fahmy' AND i.period_from<'2025-04-01'")["n"], 3)
        dfr = db.acct("service_deferred")
        bal = lambda acct: db.one("SELECT COALESCE(SUM(credit-debit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "  # noqa: E731
                                  "WHERE e.status='posted' AND l.account=? AND e.ref LIKE 'SUB-0%'", (acct,))["n"]
        self.assertEqual(bal(dfr), (12000 - 3 * 1000) * 100)                           # 3 of 12 months earned
        self.assertGreaterEqual(r["recognized"], 3)
        S.run(db, "2026-02-01")
        self.assertEqual(S.get(db, a1)["status"], "ended")
        self.assertEqual(db.one("SELECT COUNT(*) n FROM sub_visits WHERE sub_id=? AND charge>0 AND extract_id IS NULL", (a1,))["n"], 0)
        S.set_status(db, s2, "cancelled")
        self.assertEqual({x["status"] for x in db.q("SELECT status FROM subscriptions WHERE parent_id=?", (s2,))}, {"cancelled"})
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()))
        self.assertEqual(S.listing(db2)["mrr"], S.listing(db)["mrr"])
        self.assertEqual(S.listing(db2)["deferred"], S.listing(db)["deferred"])
        self.assertEqual(S.run(db2, a.today())["invoices"], 0)                          # nothing billed twice after a restore

class HostedBilling(unittest.TestCase):
    """Trial -> expired (changes refused) -> paid; mirrors supabase/migrations/*_billing.sql (checked on Postgres too)."""

    def test_trial_expiry_and_payment(self):
        import json as _json
        import urllib.request
        sys.path.insert(0, str(Path(__file__).parent))
        import mock_supabase
        sb = mock_supabase.start()
        base = f"http://127.0.0.1:{sb.server_address[1]}"
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def rpc(fn, body, who):
            r = urllib.request.Request(f"{base}/rest/v1/rpc/{fn}", data=_json.dumps(body).encode(), method="POST",
                                       headers={"Content-Type": "application/json", "Authorization": f"Bearer tok:{who}"})
            try:
                with op.open(r) as x:
                    return x.status, _json.loads(x.read() or b"null")
            except urllib.error.HTTPError as e:
                return e.code, _json.loads(e.read() or b"null")
        st = sb.state
        st.owners.add("boss@x.com")
        cid = rpc("create_company", {"p_name": "Acme"}, "ali@x.com")[1]["id"]
        appended = lambda seq: rpc("append_op", {"p_company": cid, "p_seq": seq, "p_version": "1", "p_app_user": "", "p_device": "t",  # noqa: E731
                                                  "p_method": "POST", "p_path": "/api/x", "p_body": "{}", "p_content_type": "application/json",
                                                  "p_clock": "2026-01-01T00:00:00"}, "ali@x.com")[0]
        self.assertEqual(rpc("billing_status", {"p_company": cid}, "ali@x.com")[1]["status"], "free")
        self.assertEqual(rpc("billing_set_config", {"p": {"enabled": True}}, "ali@x.com")[0], 403)   # customers cannot
        rpc("billing_set_config", {"p": {"enabled": True}}, "boss@x.com")
        s = rpc("billing_status", {"p_company": cid}, "ali@x.com")[1]
        self.assertEqual((s["status"], s["days_left"], s["users_allowed"]), ("trial", 15, 8))
        self.assertEqual(appended(1), 200)
        st.days_shift = 16
        self.assertEqual(rpc("billing_status", {"p_company": cid}, "ali@x.com")[1]["status"], "expired")
        self.assertEqual(appended(2), 400)                                                  # nothing saved once expired
        self.assertEqual(rpc("billing_quote", {"p_company": cid, "p_plan": "business", "p_months": 12, "p_extra_users": 1}, "ali@x.com")[1]["amount"],
                         (4500 + 350) * 10)
        rpc("billing_record_payment", {"p_company": cid, "p_plan": "starter", "p_months": 1, "p_extra_users": 0, "p_amount": None,
                                       "p_reference": "INSTAPAY-1", "p_note": ""}, "boss@x.com")
        s = rpc("billing_status", {"p_company": cid}, "ali@x.com")[1]
        self.assertEqual((s["status"], s["plan"], s["payments"][0]["amount"]), ("active", "starter", 1900))
        self.assertEqual(appended(2), 200)
        sb.shutdown()

class RentalServices(unittest.TestCase):
    """Installation / dismantling (months of rent or per m²) and transport (per trip): price vs our cost, scope."""

    def test_service_quote_and_contract_cost(self):
        from erp import demo, rentals as R
        db = Database(":memory:")
        demo.load(db)
        q = R.service_quote(db, 1152, 23000, 60000, "both")
        by = {x["code"]: x for x in q["lines"]}
        self.assertEqual((by["install"]["amount"], by["dismantle"]["amount"]), (36000, 24000))   # 0.6 + 0.4 = one month of rent
        self.assertEqual(by["install"]["total_cost"], 1152 * 6)                                    # crew paid per m²
        self.assertEqual((q["trips"], by["transport_out"]["amount"]), (3, 3 * 3500))               # 23 t on 10 t trucks
        self.assertEqual({x["code"] for x in R.service_quote(db, 1152, 23000, 60000, "install")["lines"]}, {"install", "transport_out", "transport_back"})
        self.assertEqual({x["charge_type"] for x in R.service_quote(db, 1152, 23000, 60000, "none")["lines"]}, {"transport"})
        small = R.service_quote(db, 40, 500, 2000, "both")
        self.assertTrue({x["code"]: x for x in small["lines"]}["install"]["min_applied"])            # minimum charge
        R.save_service_rates(db, {"truck_tons": 8, "services": [{"code": "inst", "charge_type": "install", "name": "Install",
                                                                 "basis": "m2", "price": 25, "cost_basis": "m2", "cost": 15}]})
        self.assertEqual(R.service_quote(db, 100, 0, 0)["lines"][0]["profit"], 1000)
        with self.assertRaises(a.ValidationError):
            R.service_quote(db, 100, 0, 0, "half")
        rid = db.one("SELECT id FROM rentals ORDER BY id LIMIT 1")["id"]
        d = R.detail(db, rid)
        lines = [{**{k: ln[k] for k in ("id", "kind", "item_id", "kit_id", "model_id", "description", "unit", "qty", "rate",
                                         "charge_type", "charge_when", "inputs")},
                  "components": [{"item_id": c["item_id"], "per_unit": c["per_unit"]} for c in ln["components"]]} for ln in d["lines"]]
        lines.append({"kind": "charge", "charge_type": "install", "charge_when": "delivery", "description": "Installation", "qty": 1,
                      "rate": 30000, "cost": 20736})
        R.save(db, {**{k: d["rental"][k] for k in ("client", "site", "start_date", "duration_months")}, "lines": lines}, rid=rid)
        self.assertEqual(next(ln for ln in R.detail(db, rid)["lines"] if ln["description"] == "Installation" and ln["rate"] == 30000)["cost"], 20736)

class SuperAdmin(unittest.TestCase):
    """Super administrators manage administrators and can erase / replace the books; there is always one."""

    def test_levels(self):
        from erp import auth
        db = Database(":memory:")
        boss = auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        self.assertTrue(auth.public_user(db.one("SELECT * FROM users WHERE id=?", (boss,)))["is_super"])
        adm = auth.create_user(db, {"username": "adm", "password": "pw123456", "is_admin": True})
        clerk = auth.create_user(db, {"username": "clerk", "password": "pw123456"})
        self.assertFalse(auth.public_user(db.one("SELECT * FROM users WHERE id=?", (adm,)))["is_super"])
        with self.assertRaises(a.ValidationError):
            auth.update_user(db, boss, {"is_super": False}, adm)              # the only super administrator
        auth.update_user(db, adm, {"is_super": True}, boss)
        auth.update_user(db, boss, {"is_super": False}, adm)                  # fine now: another one exists
        self.assertTrue(db.one("SELECT is_admin FROM users WHERE id=?", (boss,))["is_admin"])
        with self.assertRaises(a.ValidationError):
            auth.delete_user(db, adm, boss)                                   # last super administrator
        self.assertTrue(clerk)

    def test_server_rules(self):
        import io as _io
        from erp import auth, server
        db = Database(":memory:")
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        auth.create_user(db, {"username": "adm", "password": "pw123456", "is_admin": True})
        clerk = auth.create_user(db, {"username": "clerk", "password": "pw123456"})
        class App:
            pass
        app = App()
        app.db, app.backup = db, (lambda *a_, **k: None)
        tok_adm, _ = auth.login(db, "adm", "pw123456")
        call = lambda m, p, b=None: server.handle(app, m, p, {"x-session": tok_adm, "x-requested-with": "BeyondERP",  # noqa: E731
                                                              "content-type": "application/json"}, json.dumps(b or {}).encode())
        r = call("POST", "/api/erase", {"confirm": "ERASE", "scope": "transactions"})
        self.assertEqual(r.status, 403)
        self.assertIn("super administrator", json.loads(r.body)["error"])
        self.assertEqual(call("POST", "/api/users", {"username": "x", "password": "pw123456", "is_admin": True}).status, 403)
        self.assertEqual(call("PUT", f"/api/users/{clerk}", {"full_name": "Clerk K", "is_admin": False, "active": True}).status, 200)
        boss = db.one("SELECT id FROM users WHERE username='boss'")["id"]
        self.assertEqual(call("PUT", f"/api/users/{boss}", {"active": False}).status, 403)       # admins cannot touch the owner

class OfflineLicence(unittest.TestCase):
    """Signed licences for the desktop edition: trial, read-only without a licence, users / installation / updates."""

    def setUp(self):
        import os
        import secrets as _s
        from erp import licence
        self.seed = _s.token_bytes(32)
        os.environ["BEYOND_LICENCE_PUBLIC_KEY"] = licence.public_from_private(self.seed).hex()

    def tearDown(self):
        import os
        os.environ.pop("BEYOND_LICENCE_PUBLIC_KEY", None)

    def test_lifecycle(self):
        import datetime as _dt
        import secrets as _s
        from erp import auth, licence, server
        db = Database(":memory:")
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        st = licence.status(db)
        self.assertEqual((st["state"], st["can_write"], st["days_left"]), ("trial", True, 15))

        class App:
            pass
        app = App()
        app.db, app.backup, app.licensing = db, (lambda *a_, **k: None), True
        tok, _ = auth.login(db, "boss", "pw123456")
        call = lambda m, p, b=None: server.handle(app, m, p, {"x-session": tok, "x-requested-with": "BeyondERP",  # noqa: E731
                                                              "content-type": "application/json"}, json.dumps(b or {}).encode())
        self.assertEqual(call("POST", "/api/bus", {"code": "P1", "name": "One"}).status, 200)
        db.set_setting("first_run", (_dt.date.today() - _dt.timedelta(days=16)).isoformat())
        self.assertEqual(licence.status(db)["state"], "unlicensed")
        self.assertEqual(call("POST", "/api/bus", {"code": "P2", "name": "Two"}).status, 402)   # read-only
        self.assertEqual(call("GET", "/api/bus").status, 200)                                     # viewing still works
        iid = licence.install_id(db)
        base = {"company": "Acme", "edition": "business", "users": 2, "companies": 1, "updates_until": "2099-01-01", "install": iid}
        self.assertEqual(call("POST", "/api/licence", {"key": "BEYOND1-garbage.x"}).status, 400)
        forged = licence.issue(_s.token_bytes(32), base)                                          # signed with another key
        self.assertEqual(call("POST", "/api/licence", {"key": forged}).status, 400)
        other = licence.issue(self.seed, {**base, "install": "AAAA-BBBB-CCCC-DDDD"})
        self.assertEqual(call("POST", "/api/licence", {"key": other}).status, 400)
        old = licence.issue(self.seed, {**base, "updates_until": "2020-01-01"})
        db.set_setting("licence_key", old)
        self.assertEqual(licence.status(db)["state"], "unlicensed")                               # updates ended before this version
        good = licence.issue(self.seed, base)
        r = call("POST", "/api/licence", {"key": good})
        self.assertEqual((r.status, json.loads(r.body)["state"]), (200, "licensed"))
        self.assertEqual(call("POST", "/api/bus", {"code": "P2", "name": "Two"}).status, 200)
        self.assertEqual(call("POST", "/api/users", {"username": "u2", "password": "pw123456"}).status, 200)
        self.assertEqual(call("POST", "/api/users", {"username": "u3", "password": "pw123456"}).status, 400)  # 2 users bought

    def test_free_without_vendor_key(self):
        import os
        from erp import licence
        os.environ.pop("BEYOND_LICENCE_PUBLIC_KEY", None)
        if licence.PUBLIC_KEY_FILE.exists():
            self.skipTest("a vendor key is installed in this checkout")
        self.assertEqual(licence.status(Database(":memory:"))["state"], "free")

class ImportKit(unittest.TestCase):
    """Per-screen templates, and importing a user's own layout with column matching."""

    def test_own_layout_and_templates(self):
        from erp import importkit, interchange
        db = seeded()
        own = ("مصروفات الشهر\n\nالتاريخ,البيان,المبلغ,المورد,حساب,ملاحظة\n01/10/2026,Internet,٩٠٠,WE,6020,x\n"
               "05/10/2026,Electricity,\"1,250.50\",Elec Co,6040,\n06/10/2026,Bad,abc,,6040,\n").encode()
        an = importkit.analyze(db, "Operating Expenses", own, "mine.csv")
        self.assertEqual((an["header_row"], an["rows"], an["missing"]), (2, 3, []))
        self.assertEqual(an["mapping"], {"0": "date", "1": "description", "2": "amount", "3": "vendor", "4": "account"})
        rows, nums = importkit.read(db, "Operating Expenses", own, "mine.csv")
        self.assertEqual((rows[0]["amount"], rows[1]["amount"], nums), ("900", "1250.50", [4, 5, 6]))
        with self.assertRaises(interchange.ImportErrors) as e:
            interchange.import_data(db, {"Operating Expenses": rows}, dry_run=True)
        self.assertEqual(len(e.exception.errors), 1)
        interchange.import_data(db, {"Operating Expenses": rows[:2]})
        self.assertEqual(db.one("SELECT COUNT(*) n FROM journal_entries WHERE memo LIKE 'Electricity%'")["n"], 1)
        with self.assertRaises(a.ValidationError):
            importkit.read(db, "Operating Expenses", own, "mine.csv", {"0": "date"})     # required columns not matched
        for name in importkit.importable():                                          # every layout has a template
            self.assertTrue(importkit.template_csv(name))
        if HAVE_XL:
            import io
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(importkit.template_xlsx(db, "Products", "ar")))
            self.assertIn("How to fill", wb.sheetnames + ["How to fill"])
            ws = wb.worksheets[0]
            col = {c["key"]: i for i, c in enumerate(importkit.describe("Products")["columns"], start=1)}
            ws.cell(5, col["name"], "Villa 9")
            ws.cell(5, col["price"], 5000000)
            ws.cell(5, col["specs"], "Area: 300 m²; Floor: 2")
            buf = io.BytesIO()
            wb.save(buf)
            self.assertTrue(importkit.analyze(db, "Products", buf.getvalue(), "t.xlsx")["exact"])
            r, _ = importkit.read(db, "Products", buf.getvalue(), "t.xlsx")
            interchange.import_data(db, {"Products": r})
            from erp import sales
            p = sales.product_row(db, sales.get_product(db, "P-0001"))
            self.assertEqual((p["name"], p["price"], p["specs"]), ("Villa 9", 5000000, [["Area", "300 m²"], ["Floor", "2"]]))


class BankReconciliation(unittest.TestCase):
    """Import a statement, match it to the vouchers, book what the bank did on its own, reconcile and lock."""

    def test_parse_formats(self):
        from erp import bankrec as B
        ar = "كشف حساب\nالتاريخ,البيان,مدين,دائن,الرصيد\n٠٥/٠٣/٢٠٢٦,إيداع نقدي,,\"1,500.00\",\"11,500.00\"\n06/03/2026,رسوم,25,,11475\n"
        p = B.parse(ar)
        self.assertEqual([(r["date"], r["amount"]) for r in p["rows"]], [("2026-03-05", 1500), ("2026-03-06", -25)])
        self.assertEqual((p["opening"], p["closing"]), (10000, 11475))
        us = "Date\tDescription\tAmount\n03/25/2026\tPOS\t(12.50)\n03/01/2026\tSalary\t5000\n"
        p = B.parse(us)                                   # month/day detected; newest-first files are flipped
        self.assertEqual([(r["date"], r["amount"]) for r in p["rows"]], [("2026-03-01", 5000), ("2026-03-25", -12.5)])
        self.assertEqual(B.parse_amount("1.234,50"), 1234.5)
        self.assertEqual(B.parse_amount("250.00 DR"), -250)
        if HAVE_XL:
            import base64
            import io
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(["Value Date", "Narrative", "Withdrawals", "Deposits", "Balance"])
            ws.append([datetime.date(2026, 4, 1), "Fee", 10, None, 990])
            buf = io.BytesIO()
            wb.save(buf)
            p = B.parse("data:application/octet-stream;base64," + base64.b64encode(buf.getvalue()).decode(), "s.xlsx")
            self.assertEqual((p["rows"][0]["date"], p["rows"][0]["amount"], p["opening"]), ("2026-04-01", -10, 1000))
        with self.assertRaises(a.ValidationError):
            B.parse("nothing,here\n1,2\n")

    def test_reconcile(self):
        from erp import bankrec as B, interchange
        db = seeded()
        bank = db.acct("bank")
        rows = [{"date": "2026-01-02", "description": "Transfer partner A", "amount": 100000},
                {"date": "2026-02-01", "description": "Office rent", "amount": -5000},
                {"date": "2026-02-03", "description": "CHQ Sup", "amount": -3000},
                {"date": "2026-02-06", "description": "Salary Ali", "amount": -1000},
                {"date": "2026-02-11", "description": "Deposit", "amount": 20000},
                {"date": "2026-02-28", "description": "BANK CHARGES FEB", "amount": -50},
                {"date": "2026-02-28", "description": "Credit interest", "amount": 12}]
        sid = B.save(db, {"account": bank, "date_from": "2026-01-01", "date_to": "2026-02-28", "opening": 0, "closing": 110962, "lines": rows})
        self.assertEqual(B.auto_match(db, sid), 5)
        sm = B.detail(db, sid)["summary"]
        self.assertEqual((sm["outstanding_payments"], sm["unrecorded"], sm["difference"]), (5000, -38, 0))  # tiles not cleared yet
        with self.assertRaises(a.ValidationError):
            B.reconcile(db, sid)                                           # two bank lines still open
        open_ = [ln for ln in B.detail(db, sid)["lines"] if ln["status"] == "open"]
        B.create_entry(db, sid, open_[0]["id"], {"account": db.acct("bank_charges"), "remember": 1, "pattern": "bank charges"})
        B.create_entry(db, sid, open_[1]["id"], {"account": db.acct("interest_income")})
        with self.assertRaises(a.ValidationError):                         # the amounts must agree
            B.match(db, sid, open_[0]["id"], [1])
        B.reconcile(db, sid, {"date": "2026-03-01"})
        with self.assertRaises(a.ValidationError):
            B.unmatch(db, sid, open_[0]["id"])
        sid2 = B.save(db, {"account": bank, "date_from": "2026-03-01", "date_to": "2026-03-31", "opening": 110962, "closing": 102912,
                           "lines": [{"date": "2026-02-28", "description": "Tiles", "amount": -5000},
                                     {"date": "2026-03-03", "description": "Payment Sup", "amount": -1000},
                                     {"date": "2026-03-31", "description": "Bank charges MAR", "amount": -50},
                                     {"date": "2026-03-31", "description": "Drawing B", "amount": -2000}]})
        self.assertEqual((B.auto_match(db, sid2), B.apply_rules(db, sid2)), (3, 1))
        self.assertEqual(B.detail(db, sid2)["summary"]["difference"], 0)
        B.reconcile(db, sid2, {"date": "2026-04-01"})
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])
        # paper statement: tick vouchers
        d3 = B.save(db, {"account": db.acct("cash"), "date_from": "2026-01-01", "date_to": "2026-03-31", "opening": 0, "closing": 0})
        self.assertEqual(B.detail(db, d3)["lines"], [])
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()), mode="replace")
        self.assertEqual(B.get(db2, sid2)["status"], "reconciled")
        self.assertEqual(B.detail(db2, sid2)["summary"]["difference"], 0)
        self.assertEqual(B.detail(db2, sid)["summary"]["open_lines"], 0)


class Recurring(unittest.TestCase):
    """Templates post on schedule exactly once; review entries take a changed amount or are skipped."""

    def test_recurring(self):
        from erp import recurring as R, interchange
        db = seeded()
        t = R.save(db, {"name": "Rent", "doc_type": "expense", "start": "2026-01-31", "month_end": 1, "mode": "auto",
                        "payload": {"description": "Office rent {month}", "amount": 5000, "account": "6010"}})
        self.assertEqual(R.occurrences(R.get(db, t), "2026-05-15"), ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"])
        self.assertEqual(R.run(db, "2026-04-30")["posted"], 4)
        self.assertEqual(R.run(db, "2026-04-30")["posted"], 0)              # never twice
        self.assertEqual([h["vouchers"][0]["memo"] for h in R.history(db, t)][0], "Office rent 2026-04")
        e = R.history(db, t)[0]["vouchers"][0]["id"]
        self.assertEqual(a.get_entry(db, e)["meta"]["form"]["description"], "Office rent 2026-04")   # editable in its form
        el = R.save(db, {"name": "Electricity", "doc_type": "expense", "start": "2026-02-10", "mode": "review",
                         "payload": {"description": "Electricity {month}", "amount": 0, "account": "6040"}})
        self.assertEqual(R.run(db, "2026-03-31")["posted"], 0)              # review ones wait
        R.post(db, el, "2026-02-10", {"amount": 812.5})
        R.skip(db, el, "2026-03-10")
        with self.assertRaises(a.ValidationError):
            R.post(db, el, "2026-03-10")
        with self.assertRaises(a.ValidationError):
            R.post(db, el, "2026-03-11")                                     # not one of its dates
        q = R.save(db, {"name": "Accrual", "doc_type": "journal", "start": "2026-01-31", "unit": "month", "every": 3, "month_end": 1,
                        "max_count": 2, "mode": "auto", "payload": {"memo": "Accrual {month}", "lines": [{"account": "6090", "debit": 100}, {"account": "2030", "credit": 100}]}})
        bad = R.save(db, {"name": "Broken", "doc_type": "payment", "start": "2026-01-01", "mode": "auto", "payload": {"party": "Nobody", "amount": 999999}})
        r = R.run(db, "2026-12-31")
        self.assertEqual((r["posted"], len(r["errors"])), (8 + 2, 1))      # rent May–Dec + 2 accruals; the bad payment stops
        self.assertTrue(R.get(db, bad)["last_error"])
        self.assertEqual(R.run(db, "2026-12-31")["posted"], 0)
        self.assertEqual(len(R.history(db, q)), 2)
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()), mode="replace")
        self.assertEqual(R.run(db2, "2026-12-31")["posted"], 0)
        self.assertEqual(len(R.history(db2, t)), 12)


class Contacts(unittest.TestCase):
    """Suppliers, subcontractors and drivers saved with their contact details; what the books say about each."""

    def test_directory_and_summary(self):
        db = seeded()
        pid = a.save_party(db, {"name": "Osama Driver", "category": "Driver", "phone": "01001234567",
                                "vehicle": "ABC 123", "bank_details": "CIB 1000"})
        p = db.one("SELECT * FROM parties WHERE id=?", (pid,))
        self.assertEqual((p["category"], p["type"], p["vehicle"]), ("driver", "supplier", "ABC 123"))
        cid = a.save_party(db, {"name": "Client Co", "category": "customer"})
        self.assertEqual(db.one("SELECT type FROM parties WHERE id=?", (cid,))["type"], "customer")
        nid = a.save_party(db, {"name": "Plain"})                     # old callers: no category → customer as before
        self.assertEqual(db.one("SELECT type FROM parties WHERE id=?", (nid,))["type"], "customer")
        a.save_party(db, {"name": "Eng. Fathy", "category": "Subcontractor", "type": "both"})
        self.assertEqual(db.one("SELECT type, category FROM parties WHERE name='Eng. Fathy'"), {"type": "both", "category": "subcontractor"})
        a.record_expense(db, {"description": "Trip", "amount": 800, "vendor": "Osama Driver", "paid": 300, "date": "2026-03-01"})
        a.record_payment(db, {"party": "Osama Driver", "amount": 200, "date": "2026-03-05"})
        s = a.contact_summary(db, "Osama Driver")
        self.assertEqual(s["spent"], 800)
        self.assertEqual(sum(b["we_owe"] for b in s["balances"]), 300)
        self.assertEqual((s["vouchers"], len(s["recent"])), (2, 2))
        a.save_party(db, {**p, "name": "Osama the Driver", "type": ""}, pid)  # a rename keeps the history
        self.assertEqual(a.contact_summary(db, "Osama the Driver")["vouchers"], 2)


class SupplierDiscount(unittest.TestCase):
    """A deal settled for less than what is still owed: the rest is closed as a discount, not left in payables."""

    def test_settle_with_discount(self):
        db = seeded()
        deal = {"bu_code": "PRJ-1", "description": "Ceramic", "vendor": "Hani", "qty": 1, "rate": 16000, "savings": 1000,
                "paid": 14000, "deal_ref": "HANI-1", "deal_status": "Settled", "account": "5020", "date": "2026-03-01"}
        with self.assertRaises(a.ValidationError):          # new entries: "Settled" with money still owed is refused
            a.record_project_cost(db, deal, strict=True)
        a.record_project_cost(db, deal)                     # old entries / replays still load
        ap = db.acct("ap")
        self.assertEqual(a.party_balance(db, ap, "Hani"), 1000)
        dflt = a.supplier_discount_defaults(db, "Hani")
        self.assertEqual((dflt["credit_account"], dflt["deal_ref"], dflt["owed"]), ("5020", "HANI-1", 1000))
        cost_before = a.income_statement(db)["net_profit"]
        with self.assertRaises(a.ValidationError):          # cannot discount more than is owed
            a.record_supplier_discount(db, {"party": "Hani", "amount": 1500})
        a.record_supplier_discount(db, {"party": "Hani", "date": "2026-03-02"})
        self.assertEqual(a.party_balance(db, ap, "Hani"), 0)
        self.assertEqual(round(a.income_statement(db)["net_profit"] - cost_before, 2), 1000)
        g = [x for x in a.deals(db) if x["ref"] == "HANI-1"][0]
        self.assertEqual((g["quoted"], g["savings"], g["cost"], g["status"]), (16000, 2000, 14000, "Settled"))


class Negotiations(unittest.TestCase):
    """Negotiated discounts: company earnings (cost stays at full price) or a lower cost, per project and per entry."""

    def test_keep_and_pass(self):
        db = seeded()
        earn = db.acct("negotiation_income")
        self.assertEqual(db.one("SELECT type FROM accounts WHERE code=?", (earn,))["type"], "Revenue")
        base = {"bu_code": "PRJ-1", "description": "Tiles", "vendor": "Hani", "qty": 1, "rate": 15000, "savings": 1000,
                "paid": 14000, "account": "5020", "deal_ref": "D1", "date": "2026-03-01"}
        e1 = a.get_entry(db, a.record_project_cost(db, {**base, "negotiation": "keep"}))
        self.assertEqual({(ln["account"], ln["debit"], ln["credit"]) for ln in e1["lines"]},
                         {("5020", 15000, 0), (earn, 0, 1000), ("1020", 0, 14000)})
        e2 = a.get_entry(db, a.record_project_cost(db, {**base, "deal_ref": "D2"}))          # no choice, no BU setting
        self.assertEqual(e2["meta"]["negotiation"], "pass")
        self.assertIn(("5020", 14000, 0), {(ln["account"], ln["debit"], ln["credit"]) for ln in e2["lines"]})
        db.x("UPDATE business_units SET negotiation='keep' WHERE code='PRJ-1'")               # project default
        e3 = a.get_entry(db, a.record_project_cost(db, {**base, "deal_ref": "D3"}))
        self.assertEqual(e3["meta"]["negotiation"], "keep")
        with self.assertRaises(a.ValidationError):
            a.record_project_cost(db, {**base, "negotiation": "maybe"})
        n = a.negotiations(db)
        self.assertEqual((n["total"]["savings"], n["total"]["kept"], n["total"]["passed"]), (3200, 2000, 1200))
        prj = [g for g in n["by_bu"] if g["bu_code"] == "PRJ-1"][0]
        self.assertEqual((prj["kept"], prj["setting"]), (2000, "keep"))
        b = [x for x in a.bu_summaries(db) if x["code"] == "PRJ-1"][0]
        self.assertEqual(b["savings_kept"], 2000)
        # a late discount on a 'keep' project goes to Negotiation Earnings by default
        a.record_project_cost(db, {**base, "savings": 0, "paid": 14000, "deal_ref": "D4", "negotiation": "keep"})
        d = a.supplier_discount_defaults(db, "Hani")
        self.assertEqual((d["negotiation"], d["credit_account"], d["owed"]), ("keep", earn, 1000))
        a.record_supplier_discount(db, {"party": "Hani", "negotiation": "pass"})
        self.assertEqual(a.party_balance(db, db.acct("ap"), "Hani"), 0)
        self.assertEqual(a.negotiations(db)["rows"][-1]["mode"], "pass")

    def test_edit_through_form_and_search(self):
        from erp.server import ERPServer
        tmp = tempfile.TemporaryDirectory()
        db = Database(Path(tmp.name) / "t.sqlite3")
        srv = ERPServer(("127.0.0.1", 0), db, Path(tmp.name) / "backups")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            api = Api("call")
            api.base = f"http://127.0.0.1:{srv.server_address[1]}"
            op = api.client()
            self.assertEqual(api.call(op, "POST", "/api/setup", {"username": "admin", "password": "secret123"})[0], 200)
            bu = api.call(op, "POST", "/api/bus", {"code": "P1", "name": "Villa", "negotiation": "keep"})[1]["id"]
            deal = {"bu_id": bu, "description": "Ceramic", "vendor": "Hani Ceramic", "qty": 1, "rate": 16000,
                    "savings": 1000, "paid": 14000, "deal_ref": "HANI-1", "deal_status": "Open", "account": "5020"}
            eid = api.call(op, "POST", "/api/docs/direct_cost", deal)[1]["id"]
            e = api.call(op, "GET", f"/api/journal/{eid}")[1]
            self.assertEqual((e["doc"], e["form"]["rate"], e["meta"]["negotiation"]), ("direct_cost", 16000, "keep"))
            # correct it through the form: the real quote was 15,000; settled; the entry keeps its number
            st, r = api.call(op, "PUT", f"/api/docs/direct_cost/{eid}", {**deal, "rate": 15000, "deal_ref": "HANI-2",
                                                                          "deal_status": "Settled", "negotiation": "keep"})
            self.assertEqual((st, r["id"]), (200, eid))
            e = api.call(op, "GET", f"/api/journal/{eid}")[1]
            self.assertEqual((e["ref"], e["meta"]["deal_status"], len(e["history"])), ("HANI-2", "Settled", 1))
            self.assertEqual(api.call(op, "GET", "/api/supplier-discount?party=Hani%20Ceramic")[1]["owed"], 0)
            self.assertEqual(api.call(op, "GET", "/api/negotiations")[1]["total"]["kept"], 1000)
            # search: account name, account code, amount, business unit
            for word in ("Subcontractor", "5020", "14,000", "P1", "earnings"):
                rows = api.call(op, "GET", "/api/journal?q=" + urllib.parse.quote(word))[1]["rows"]
                self.assertIn(eid, [x["id"] for x in rows], word)
            self.assertEqual(api.call(op, "GET", "/api/journal?q=nothing-like-this")[1]["rows"], [])
            # journal editor can fix the reference and deal too
            self.assertEqual(api.call(op, "PUT", f"/api/journal/{eid}", {"ref": "X-9", "deal_ref": "X-9"})[0], 200)
            self.assertEqual(api.call(op, "GET", f"/api/journal/{eid}")[1]["meta"]["deal_ref"], "X-9")
            self.assertEqual(api.call(op, "GET", "/api/reports/negotiations?format=json")[0], 200)
        finally:
            srv.shutdown()
            srv.server_close()
            db.close()
            tmp.cleanup()

    def test_old_books_get_the_account(self):
        db = Database(":memory:")
        db.x("UPDATE accounts SET role=NULL WHERE role='negotiation_income'")
        db.x("DELETE FROM accounts WHERE code='4085'")
        db._migrate()
        self.assertEqual(db.acct("negotiation_income"), "4085")


class Loans(unittest.TestCase):
    """Loans taken and given: schedules, interest, repayments, write-offs, balances from the books."""

    def setUp(self):
        from erp import loans
        self.L = loans
        self.db = Database(":memory:")
        a.record_journal(self.db, {"date": "2026-01-01", "lines": [{"account": "1020", "debit": 1000000}, {"account": "3010", "credit": 1000000}]})

    def test_schedules(self):
        L = self.L
        base = {"principal": 120000, "start_date": "2026-01-01", "term_months": 12}
        eq = L.preview_schedule({**base, "interest_type": "declining", "rate": 12, "schedule_type": "equal"})
        self.assertEqual(len(eq["rows"]), 12)
        self.assertEqual(eq["rows"][0]["interest"], 1200)                        # 1 % of 120,000 in month 1
        self.assertAlmostEqual(eq["totals"]["principal"], 120000, places=2)
        self.assertEqual(eq["rows"][-1]["balance"], 0)
        self.assertLess(abs(eq["rows"][0]["payment"] - eq["rows"][5]["payment"]), 0.02)   # equal installments
        flat = L.preview_schedule({**base, "interest_type": "flat", "rate": 10, "schedule_type": "equal", "frequency": 3})
        self.assertEqual([r["interest"] for r in flat["rows"]], [3000] * 4)     # 12,000 flat over 4 quarters
        bullet = L.preview_schedule({**base, "interest_type": "declining", "rate": 12, "schedule_type": "bullet"})
        self.assertEqual([r["principal"] for r in bullet["rows"][:-1]], [0] * 11)
        self.assertEqual(bullet["rows"][-1]["principal"], 120000)
        fixed = L.preview_schedule({**base, "interest_type": "fixed", "fixed_interest": 1000, "schedule_type": "equal_principal", "term_months": 3})
        self.assertAlmostEqual(fixed["totals"]["interest"], 1000, places=2)
        self.assertEqual(L.preview_schedule({**base, "schedule_type": "none"})["rows"], [])

    def test_borrowed_loan_lifecycle(self):
        L, db = self.L, self.db
        lid = L.save(db, {"direction": "borrowed", "party": "CIB", "principal": 120000, "rate": 12, "interest_type": "declining",
                          "term_months": 12, "start_date": "2026-01-01", "schedule_type": "equal"})
        self.assertEqual(a.party_balance(db, "2300", "CIB"), 120000)              # money received, loan owed
        d = L.detail(db, lid, today="2026-02-15")
        self.assertEqual(d["loan"]["overdue"], d["schedule"][0]["payment"])       # installment 1 due 1 Feb, unpaid
        s = d["suggest"]
        self.assertEqual((s["principal"], s["interest"]), (d["schedule"][0]["principal"], 1200))
        L.post(db, lid, "pay", {"date": "2026-02-01", "principal": s["principal"], "interest": s["interest"]})
        d = L.detail(db, lid, today="2026-02-15")
        self.assertEqual((d["schedule"][0]["state"], d["loan"]["overdue"]), ("paid", 0))
        self.assertEqual(d["loan"]["interest_paid"], 1200)
        # accrue at month end, then a payment clears the accrued interest first
        L.post(db, lid, "interest", {"date": "2026-02-28", "amount": 1000})
        self.assertEqual(L.detail(db, lid)["loan"]["accrued"], 1000)
        L.post(db, lid, "pay", {"date": "2026-03-01", "principal": 9556.47, "interest": 1105.38})
        self.assertEqual(L.detail(db, lid)["loan"]["accrued"], 0)
        self.assertAlmostEqual(next(x for x in a.account_totals(db) if x["code"] == "7030")["net"] / 100, 1200 + 1105.38, places=2)
        with self.assertRaises(a.ValidationError):                                # more principal than is owed
            L.post(db, lid, "pay", {"date": "2026-03-02", "principal": 1e6})
        with self.assertRaises(a.ValidationError):                                # cannot close with a balance
            L.set_status(db, lid, True)
        with self.assertRaises(a.ValidationError):                                # entries exist: void them first
            L.delete(db, lid)
        L.post(db, lid, "writeoff", {"date": "2026-04-01"})                       # the bank forgave the rest
        L.set_status(db, lid, True)
        self.assertEqual(L.get(db, lid)["status"], "closed")
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])

    def test_lent_loan_and_opening_balance(self):
        L, db = self.L, self.db
        lid = L.save(db, {"direction": "lent", "party": "Ahmed", "principal": 24000, "interest_type": "none", "term_months": 6,
                          "start_date": "2026-01-01", "schedule_type": "equal"})
        self.assertEqual(-a.party_balance(db, db.acct("loans_receivable"), "Ahmed"), 24000)
        L.post(db, lid, "pay", {"date": "2026-02-01", "principal": 4000})
        self.assertEqual(L.detail(db, lid, today="2026-02-02")["loan"]["outstanding"], 20000)
        # a loan already in the books: nothing is posted, earlier installments count as paid
        old = L.save(db, {"direction": "borrowed", "party": "NBE", "principal": 60000, "term_months": 12, "interest_type": "none",
                          "start_date": "2025-07-01", "schedule_type": "equal", "booking": "opening", "opening_balance": 30000,
                          "opening_date": "2026-01-01"})
        d = L.detail(db, old, today="2026-01-15")
        self.assertEqual((d["loan"]["outstanding"], d["loan"]["overdue"], d["entries"]), (30000, 0, []))
        self.assertEqual(sum(1 for r in d["schedule"] if r["state"] == "paid"), 6)
        # flexible loan with daily interest: 36,500 at 10 % → 10 a day
        flex = L.save(db, {"direction": "lent", "party": "Partner Co", "principal": 36500, "rate": 10, "interest_type": "declining",
                           "schedule_type": "none", "term_months": 0, "start_date": "2026-03-01"})
        self.assertEqual(L.suggest(db, L.get(db, flex), "2026-03-31")["interest"], 300)

    def test_api_and_backup(self):
        from erp import interchange
        L, db = self.L, self.db
        db.x("INSERT INTO business_units(code,name) VALUES ('P1','Villa')")
        lid = L.save(db, {"direction": "borrowed", "party": "QNB", "principal": 50000, "rate": 8, "interest_type": "flat",
                          "term_months": 10, "start_date": "2026-01-01", "bu_code": "P1"})
        L.post(db, lid, "pay", {"date": "2026-02-01", "principal": 5000, "interest": 333.33})
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()), mode="replace")
        self.assertEqual(L.detail(db2, "LN-0001")["loan"]["outstanding"], 45000)
        self.assertEqual(L.detail(db2, "LN-0001")["loan"]["bu_code"], "P1")
        from erp import reports
        for name, p in (("loans", {}), ("loan_schedule", {"code": "LN-0001"})):
            tb = reports.build(db, name, p)
            self.assertTrue(reports.to_html(tb).startswith("<!doctype html>"))


class Custody(unittest.TestCase):
    """Cash custody: issue, expense reports (split by business unit, with VAT), return, reimburse, deduct, close."""

    def test_custody_lifecycle(self):
        from erp import custody, interchange
        db = Database(":memory:")
        a.record_journal(db, {"date": "2026-01-01", "lines": [{"account": "1010", "debit": 50000}, {"account": "3010", "credit": 50000}]})
        db.x("INSERT INTO business_units(code,name) VALUES ('P1','Villa'),('P2','Flat')")
        acc_c = db.acct("custody")
        cid = custody.save(db, {"holder": "Samir", "amount": 5000, "issued_on": "2026-02-01", "due_date": "2026-02-20", "bu_code": "P1"})
        self.assertEqual(-a.party_balance(db, acc_c, "Samir"), 5000)
        ids = custody.post(db, cid, "settle", {"date": "2026-02-10", "report_no": "R-1", "lines": [
            {"description": "Cement", "account": "5010", "amount": 2280, "vat_code": "VAT14", "supplier": "Hardware"},
            {"description": "Taxi", "account": "6075", "amount": 300, "bu_id": 2}]})
        self.assertEqual(len(ids), 2)                                                  # one entry per business unit
        e = a.get_entry(db, ids[0])
        self.assertIn(("5010", 2000.0), [(ln["account"], ln["debit"]) for ln in e["lines"]])   # net of 14 % VAT
        st = custody.status(db, custody.get(db, cid), today="2026-02-15")
        self.assertEqual((st["balance"], st["spent"], st["overdue"]), (2420, 2580, False))
        self.assertTrue(custody.status(db, custody.get(db, cid), today="2026-03-01")["overdue"])
        with self.assertRaises(a.ValidationError):
            custody.post(db, cid, "return", {"amount": 9999})
        custody.post(db, cid, "return", {"date": "2026-02-11", "amount": 2000})
        custody.post(db, cid, "deduct", {"date": "2026-02-12", "amount": 420})          # kept from salary
        self.assertEqual(-a.party_balance(db, db.acct("accrued_payroll"), "Samir"), 420)
        with self.assertRaises(a.ValidationError):
            custody.delete(db, cid)
        custody.set_status(db, cid, True)
        # a standing float: overspending makes the company owe the holder; the report can top the float back up
        f = custody.save(db, {"holder": "Office", "kind": "permanent", "float_amount": 1000, "issued_on": "2026-03-01"})
        custody.post(db, f, "settle", {"date": "2026-03-05", "lines": [{"description": "Stationery", "account": "6080", "amount": 1200}],
                                       "replenish": True})
        st = custody.status(db, custody.get(db, f))
        self.assertEqual((st["balance"], st["spent"]), (1000, 1200))
        with self.assertRaises(a.ValidationError):
            custody.set_status(db, f, True)
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()), mode="replace")
        self.assertEqual(custody.status(db2, custody.get(db2, "CU-0002"))["balance"], 1000)
        self.assertEqual(len(custody.statement(db2, "CU-0001")["rows"]), 5)


class Rentals(unittest.TestCase):
    """Equipment rental: FIFO stock, frames, the scaffold calculator, PO → signed contract → signed handovers → invoices."""

    def setUp(self):
        from erp import rentals
        self.R = rentals
        self.db = db = Database(":memory:")
        a.record_journal(db, {"date": "2026-01-01", "lines": [{"account": "1020", "debit": 5000000}, {"account": "3010", "credit": 5000000}]})
        rentals.starter(db)
        self.ids = {i["code"]: i["id"] for i in rentals.list_items(db)["items"]}
        rentals.add_lot(db, self.ids["SC-FRM2"], {"qty": 150, "unit_cost": 900, "date": "2025-01-01", "source": "opening"})
        rentals.add_lot(db, self.ids["SC-FRM2"], {"qty": 100, "unit_cost": 980, "date": "2026-01-05", "supplier": "Steel Co", "paid": 0})
        for c, q, cost in (("SC-BRC3", 250, 340), ("SC-PLT60", 80, 150), ("SC-JACK", 80, 220), ("SC-CLF", 100, 110), ("SC-CLS", 30, 110),
                           ("SC-TUB6", 20, 750), ("SC-BRD36", 60, 700), ("EL-HOIST", 1, 450000)):
            rentals.add_lot(db, self.ids[c], {"qty": q, "unit_cost": cost, "date": "2026-01-05", "source": "opening"})

    def test_calculator_and_fifo(self):
        R, db = self.R, self.db
        ev = R.evaluate(db, R.list_models(db)[0]["spec"])
        q = {c["item"]: c["qty"] for c in ev["components"]}
        self.assertEqual((ev["area"], q["SC-FRM2"], q["SC-JACK"], q["SC-CLF"], q["SC-BRD36"]), (1152, 200, 50, 52, 24))  # the handover form
        taller = R.evaluate(db, R.list_models(db)[0]["spec"], {"height": 24})
        self.assertEqual({c["item"]: c["qty"] for c in taller["components"]}["SC-BRC3"], 360)   # 12 lifts, +25 % bracing above 20 m
        for bad in ("__import__('os')", "length.real", "[1][0]", "2 ** 99"):
            with self.assertRaises(a.ValidationError):
                R.safe_eval(bad, {"length": 1})
        self.assertEqual(R.safe_eval("ceil(10 / 3) if h > 5 else 0", {"h": 6}), 4)
        st = R.item_stock(db, self.ids["SC-FRM2"])
        self.assertEqual((st["in_yard"], st["value"]), (250, 233000))
        self.assertEqual(round(R.unit_cost_next(db, self.ids["SC-FRM2"], 160) / 100, 2), 905)     # 150 × 900 + 10 × 980
        self.assertEqual(a.party_balance(db, db.acct("ap"), "Steel Co"), 98000)                    # the purchase is booked
        R.scrap(db, {"item_id": self.ids["SC-FRM2"], "qty": 5, "reason": "bent"})                 # oldest lot first
        self.assertEqual(R.item_detail(db, self.ids["SC-FRM2"])["lots"][0]["gone"], 5)

    def _contract(self):
        R, db = self.R, self.db
        ev = R.evaluate(db, R.list_models(db)[0]["spec"])
        kit = R.list_items(db)["kits"][0]
        rid = R.save(db, {"client": "Sawag", "site": "Agora — building 3", "vat_code": "VAT14", "lines": [
            {"kind": "sqm", "qty": ev["area"], "rate": 9, "components": [{"item_id": c["item_id"], "per_unit": c["qty"] / ev["area"]} for c in ev["components"]]},
            {"kind": "kit", "kit_id": kit["id"], "qty": 20, "rate": 120},
            {"kind": "item", "item_id": self.ids["EL-HOIST"], "qty": 1, "rate": 25000},
            {"kind": "charge", "charge_type": "install", "qty": 1, "rate": 15000},
            {"kind": "charge", "charge_type": "dismantle", "qty": 1, "rate": 10000},
            {"kind": "charge", "charge_type": "transport", "qty": 2, "rate": 3000}]})
        return rid

    def _deliver(self, rid, date="2026-08-01"):
        t = self.R.handover_template(self.db, rid, "delivery")
        t.update(date=date, signed=1, decision="safe", our_name="Hossam", client_name="Karim",
                 checklist=[dict(c, ok="yes") for c in t["checklist"]])
        return self.R.save_handover(self.db, rid, t)

    def test_contract_flow(self):
        R, db = self.R, self.db
        rid = self._contract()
        hid = self._deliver(rid)
        with self.assertRaises(a.ValidationError):                       # no purchase order yet
            R.confirm_handover(db, hid)
        R.action(db, rid, "po", {"po_number": "PO-778", "po_date": "2026-08-01"})
        with self.assertRaises(a.ValidationError):                       # contract not signed yet
            R.confirm_handover(db, hid)
        R.action(db, rid, "sign", {"signed_us": "Hossam", "signed_client": "Karim", "signed_on": "2026-08-01"})
        R.confirm_handover(db, hid)
        d = R.detail(db, rid)
        self.assertEqual(d["rental"]["status"], "active")
        self.assertEqual(sorted(c["kind"] for c in d["charges"]), ["install", "transport"])      # billed on delivery
        frames = R.item_detail(db, self.ids["SC-FRM2"])["lots"]
        self.assertEqual([(l["with_clients"], l["in_yard"]) for l in frames], [(150, 0), (70, 30)])   # FIFO: oldest first
        al = R.allocation(db, rid, d["lines"][0]["id"])                    # m² rent shared by FIFO cost
        self.assertAlmostEqual(sum(x["monthly"] for x in al["rows"]), al["rent"], delta=0.05)
        self.assertEqual(al["basis"], "cost")
        yields = {round(x["yield"], 4) for x in al["rows"]}
        self.assertEqual(len(yields), 1)                                  # every piece earns the same % of its cost
        # August invoice: a full calendar month = 1 month on every line
        p = R.next_period(db, rid)
        self.assertEqual((p["from"], p["to"]), ("2026-08-01", "2026-08-31"))
        xid = R.create_invoice(db, rid, {"from": p["from"], "to": p["to"]})
        x = a.get_extract(db, xid)
        self.assertEqual([ln["qty"] for ln in x["lines"][:3]], [1152, 20, 1])
        self.assertEqual(x["total"], 1152 * 9 + 20 * 120 + 25000 + 15000 + 6000)
        with self.assertRaises(a.ValidationError):                       # the same period twice
            R.create_invoice(db, rid, {"from": "2026-08-15", "to": "2026-09-10"})
        a.post_extract(db, xid)
        e = a.get_entry(db, a.get_extract(db, xid)["entry_id"])
        self.assertEqual({ln["account"] for ln in e["lines"] if ln["credit"]}, {"4045", "4046", "2200"})
        # return the frame sets on 15 Sep: 2 braces lost, 1 frame damaged
        t = R.handover_template(db, rid, "return")
        kl = d["lines"][1]["id"]
        t["date"] = "2026-09-15"
        t["data"]["units"] = {k: (v if int(k) == kl else 0) for k, v in t["data"]["units"].items()}
        items = [x for x in t["data"]["items"] if x["line_id"] == kl]
        for x in items:
            if x["code"] == "SC-BRC3":
                x["qty"], x["lost"] = x["qty"] - 2, 2
            if x["code"] == "SC-FRM2":
                x["qty"], x["damaged"] = x["qty"] - 1, 1
        t["data"]["items"] = items
        t["signed"] = 1
        with self.assertRaises(a.ValidationError):                       # not signed by both parties
            R.confirm_handover(db, R.save_handover(db, rid, {**t, "client_name": ""}))
        R.confirm_handover(db, R.save_handover(db, rid, t))
        loss = next(x for x in a.account_totals(db) if x["code"] == "7046")["net"]
        self.assertEqual(loss, 90000 + 2 * 34000)                         # written off at FIFO cost (frame 900, braces 340)
        sep = R.invoice_lines(db, rid, "2026-09-01", "2026-09-30")
        self.assertEqual([ln["qty"] for ln in sep["lines"] if ln["category"] == "Rent"], [1152, 10, 1])   # sets out 15 of 30 days
        self.assertAlmostEqual(sum(ln["qty"] * ln["price"] for ln in sep["lines"] if ln["category"] == "Compensation"), 2 * 391 + 1127)
        # everything back → dismantling is charged, then the contract can close once invoiced
        t = R.handover_template(db, rid, "return")
        t.update(date="2026-10-10", signed=1)
        R.confirm_handover(db, R.save_handover(db, rid, t))
        d = R.detail(db, rid)
        self.assertEqual(d["rental"]["status"], "returned")
        self.assertIn("dismantle", [c["kind"] for c in d["charges"]])
        with self.assertRaises(a.ValidationError):
            R.action(db, rid, "close", {})
        R.create_invoice(db, rid, {"from": "2026-09-01", "to": "2026-10-10"})
        R.action(db, rid, "close", {})
        self.assertEqual(R.get(db, rid)["status"], "closed")
        self.assertEqual(R.item_stock(db, self.ids["SC-FRM2"])["on_rent"], 0)
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])

    def test_depreciation(self):
        R, db = self.R, self.db
        fid = self.ids["SC-FRM2"]
        # lots: 150 × 900 (Jan 2025) and 100 × 980 (Jan 2026); 10 years to 10 %
        pl = R.plan_depreciation(db, "2026-06")
        lots = {r["lot"]: (r["from"], r["months"], r["amount"]) for r in pl["rows"] if r["item_id"] == fid}
        self.assertEqual(lots["LOT-00001"], ("2025-01", 18, int(round(150 * 90000 * 0.9 / 120 * 18))))
        self.assertEqual(lots["LOT-00002"], ("2026-01", 6, int(round(100 * 98000 * 0.9 / 120 * 6))))
        eid = R.run_depreciation(db, "2026-06")
        with self.assertRaises(a.ValidationError):
            R.run_depreciation(db, "2026-06")                 # nothing left for those months
        st = R.item_stock(db, fid)
        self.assertEqual(st["accum"], (lots["LOT-00001"][2] + lots["LOT-00002"][2]) / 100)
        self.assertEqual(R.plan_depreciation(db, "2026-07")["rows"][0]["months"], 1)   # next month only
        # scrapping 15 pieces of the oldest lot clears 10 % of that lot's depreciation
        R.scrap(db, {"item_id": fid, "qty": 15, "date": "2026-07-01"})
        e = a.get_entry(db, db.q("SELECT id FROM journal_entries WHERE kind='Rental Equipment Loss'")[-1]["id"])
        share = int(round(lots["LOT-00001"][2] * 15 / 150)) / 100
        self.assertIn((db.acct("rental_accum"), share, 0.0), [(ln["account"], ln["debit"], ln["credit"]) for ln in e["lines"]])
        self.assertIn(("1415", 0.0, 13500.0), [(ln["account"], ln["debit"], ln["credit"]) for ln in e["lines"]])
        a.void_entry(db, eid, "redo")                         # a voided run is simply booked again
        self.assertEqual(R.plan_depreciation(db, "2026-06")["rows"][0]["from"], "2025-01")
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])

    def test_minimum_period_and_deposit(self):
        R, db = self.R, self.db
        rid = R.save(db, {"client": "Client X", "min_days": 30, "deposit": 5000, "deposit_first": True,
                          "lines": [{"kind": "item", "item_id": self.ids["SC-FRM2"], "qty": 50, "rate": 60}]})
        R.action(db, rid, "po", {"po_number": "P"})
        R.action(db, rid, "sign", {"signed_us": "a", "signed_client": "b"})
        hid = self._deliver(rid)
        with self.assertRaises(a.ValidationError):            # deposit first
            R.confirm_handover(db, hid)
        R.action(db, rid, "deposit", {"type": "receive", "amount": 5000, "date": "2026-07-30"})
        R.confirm_handover(db, hid)
        t = R.handover_template(db, rid, "return")
        t.update(date="2026-08-10", signed=1, our_name="a", client_name="b")
        t["data"]["units"] = {k: 20 for k in t["data"]["units"]}
        t["data"]["items"][0]["qty"] = 20
        R.confirm_handover(db, R.save_handover(db, rid, t))
        mins = [c for c in R.detail(db, rid)["charges"] if c["kind"] == "minimum"]
        self.assertEqual(len(mins), 1)
        self.assertAlmostEqual(mins[0]["total"], 20 * 20 / 30 * 60, delta=0.05)   # 20 frames kept 10 of 30 days
        xid = R.create_invoice(db, rid, {"from": "2026-08-01", "to": "2026-08-31"})
        a.post_extract(db, xid)
        owed = -a.party_balance(db, db.acct("ar"), "Client X")
        with self.assertRaises(a.ValidationError):            # not more than they owe
            R.action(db, rid, "deposit", {"type": "apply", "amount": owed + 1})
        R.action(db, rid, "deposit", {"type": "apply", "amount": owed})
        self.assertEqual(a.party_balance(db, db.acct("ar"), "Client X"), 0)
        t = R.handover_template(db, rid, "return")
        t.update(date="2026-09-01", signed=1, our_name="a", client_name="b")
        R.confirm_handover(db, R.save_handover(db, rid, t))
        R.create_invoice(db, rid, {"from": "2026-09-01", "to": "2026-09-01"})
        with self.assertRaises(a.ValidationError):            # deposit still held
            R.action(db, rid, "close", {})
        left = R.detail(db, rid)["rental"]["deposit_held"]
        R.action(db, rid, "deposit", {"type": "refund", "amount": left})
        R.action(db, rid, "close", {})
        self.assertEqual(R.get(db, rid)["status"], "closed")
        self.assertEqual([c["check"] for c in a.audit(db) if not c["ok"]], [])

    def test_undo_short_stock_and_backup(self):
        from erp import interchange, rental_docs
        R, db = self.R, self.db
        rid = self._contract()
        R.action(db, rid, "po", {"po_number": "PO-1"})
        R.action(db, rid, "sign", {"signed_us": "A", "signed_client": "B"})
        t = R.handover_template(db, rid, "delivery")
        t.update(signed=1, decision="safe", date="2026-09-01", checklist=[dict(c, ok="yes") for c in t["checklist"]])
        t["data"]["items"][0]["qty"] = 999
        with self.assertRaises(a.ValidationError):                       # more than the yard holds
            R.confirm_handover(db, R.save_handover(db, rid, t))
        t["data"]["items"][0]["qty"] = 200
        t["checklist"][0]["ok"] = "no"
        with self.assertRaises(a.ValidationError):                       # a failed safety check needs 'not safe'
            R.confirm_handover(db, R.save_handover(db, rid, t))
        t["checklist"][0]["ok"] = "yes"
        hid = R.save_handover(db, rid, t)
        R.confirm_handover(db, hid)
        R.undo_handover(db, hid)
        self.assertEqual(R.item_stock(db, self.ids["SC-FRM2"])["on_rent"], 0)
        self.assertEqual(R.detail(db, rid)["charges"], [])
        R.confirm_handover(db, hid)
        for html in (rental_docs.offer_html(db, rid, "ar"), rental_docs.contract_html(db, rid, "en"), rental_docs.handover_html(db, hid, "ar")):
            self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("الأرضية صلبة", rental_docs.handover_html(db, hid, "ar"))
        R.create_invoice(db, rid, {"from": "2026-09-01", "to": "2026-09-30"})
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()), mode="replace")
        self.assertEqual(R.detail(db2, "RC-0001")["rental"]["status"], "active")
        self.assertEqual(R.item_stock(db2, R.get_item(db2, "SC-FRM2")["id"])["on_rent"], 220)
        self.assertEqual(len(R.detail(db2, "RC-0001")["invoices"]), 1)


if __name__ == "__main__":
    unittest.main()


class Companies(unittest.TestCase):
    """One workspace, several companies: separate books and members, invite codes, choosing a company."""

    def test_companies(self):
        import os
        from erp.cloud import Supabase
        from erp.server import ERPServer
        sys.path.insert(0, str(Path(__file__).parent))
        import mock_supabase
        sb = mock_supabase.start()
        url = f"http://127.0.0.1:{sb.server_address[1]}"
        tmp = tempfile.TemporaryDirectory()
        nodes = []
        try:
            for name in ("a", "b"):
                db = Database(Path(tmp.name) / f"{name}.sqlite3")
                srv = ERPServer(("127.0.0.1", 0), db, Path(tmp.name) / f"bk-{name}")
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                nodes.append((srv, db, f"http://127.0.0.1:{srv.server_address[1]}"))
            api = lambda i, op, m, p, b=None: Api.call(type("C", (), {"base": nodes[i][2]})(), op, m, p, b)  # noqa: E731
            a = Api.client(self)
            api(0, a, "POST", "/api/setup", {"username": "admin", "password": "secret123"})
            api(0, a, "PUT", "/api/cloud/config", {"url": url, "key": "anon"})
            self.assertEqual(api(0, a, "POST", "/api/cloud/signin", {"email": "owner@x.com", "password": "pw123456", "create": True})[0], 200)
            st, info = api(0, a, "POST", "/api/cloud/claim", {"name": "Alpha"})
            self.assertEqual((st, info.get("company_name")), (200, "Alpha"), info)
            self.assertEqual(api(0, a, "POST", "/api/bus", {"code": "A1", "name": "Alpha unit"})[0], 200)
            st, beta = api(0, a, "POST", "/api/cloud/company", {"action": "create", "name": "Beta"})
            self.assertEqual(st, 200, beta)
            alpha, beta = sb.state.company_named("Alpha"), beta["id"]
            self.assertEqual(api(0, a, "GET", "/api/cloud/info")[1]["company"], alpha)   # this device stays on Alpha
            # a newcomer signs up and asks to join Alpha with its invite code: pending until an admin approves
            cli = Supabase(url, "anon")
            tok = cli.sign_up("new@y.com", "pw654321")["access_token"]
            code = api(0, a, "GET", "/api/cloud/info")[1]["invite_code"]
            self.assertTrue(cli.rpc("join_company", {"p_code": code}, tok)["pending"])
            with self.assertRaises(RuntimeError):
                cli.rpc("join_company", {"p_code": "WRONG123"}, tok)
            self.assertEqual(cli.select("ops", f"company_id=eq.{alpha}&seq=gt.0", tok), [])     # nothing yet
            pending = [m for m in api(0, a, "GET", "/api/cloud/members")[1] if m["pending"]]
            self.assertEqual([m["email"] for m in pending], ["new@y.com"])
            self.assertEqual(api(0, a, "PUT", "/api/cloud/members", {"email": "new@y.com", "active": True})[0], 200)
            self.assertEqual(len(cli.select("ops", f"company_id=eq.{alpha}&seq=gt.0", tok)), 1)
            self.assertEqual(cli.select("ops", f"company_id=eq.{beta}&seq=gt.0", tok), [])      # Beta stays closed
            with self.assertRaises(PermissionError):
                cli.rpc("append_op", {"p_company": beta, "p_seq": 1, "p_version": "1", "p_app_user": "", "p_device": "x",
                                      "p_method": "POST", "p_path": "/api/bus", "p_body": "{}", "p_content_type": "",
                                      "p_clock": "2026-01-01T00:00:00"}, tok)
            # a fresh offline install: the owner belongs to two companies, so it asks which one to open
            os.environ.update(BEYOND_SUPABASE_URL=url, BEYOND_SUPABASE_ANON_KEY="anon")
            b = Api.client(self)
            st, r = api(1, b, "POST", "/api/cloud/first-join", {"username": "owner@x.com", "password": "pw123456"})
            self.assertEqual(sorted(c["name"] for c in r["choose"]), ["Alpha", "Beta"])
            st, r = api(1, b, "POST", "/api/cloud/first-join", {"username": "owner@x.com", "password": "pw123456",
                                                                "company": beta})
            self.assertEqual(st, 200, r)
            self.assertEqual(nodes[1][0].cloud.company, beta)
            self.assertEqual(nodes[1][1].setting("company_name"), "Beta")              # new company: fresh books
            self.assertIsNone(nodes[1][1].one("SELECT 1 FROM business_units WHERE code='A1'"))
            self.assertEqual(api(1, b, "POST", "/api/bus", {"code": "B1", "name": "Beta unit"})[0], 200)
            self.assertEqual([o["path"] for o in sb.state.ops[beta]], ["/api/bus"])
            self.assertEqual(len(sb.state.ops[alpha]), 1)                                     # Alpha untouched
            nodes[0][0].cloud.pull()
            self.assertIsNone(nodes[0][1].one("SELECT 1 FROM business_units WHERE code='B1'"))
            # usernames are shared by every company: Beta's administrator cannot create or grab Alpha's "ali"
            self.assertEqual(api(0, a, "POST", "/api/cloud/signin", {"email": "owner@x.com", "password": "pw123456"})[0], 200)
            self.assertEqual(api(0, a, "PUT", "/api/cloud/account", {"username": "ali", "password": "alipw123"})[0], 200)  # Alpha's ali
            beta_admin = Supabase(url, "anon")
            btok = beta_admin.sign_in("owner@x.com", "pw123456")["access_token"]
            fn = lambda body: beta_admin._call("POST", "/functions/v1/manage-user", {"company": beta, **body}, btok)  # noqa: E731
            for body in ({"username": "ali"}, {"username": "ali", "password": "other123"},
                         {"username": "ali", "email": "someone@else.com", "password": "other123"}):
                with self.assertRaises(RuntimeError) as cm:
                    fn(body)
                self.assertIn("already taken", str(cm.exception))
                self.assertIn("ali.beta", str(cm.exception))                              # a free name is suggested
            self.assertIsNone(sb.state.members.get((beta, "ali@users.beyond-erp.invalid")))
            self.assertEqual(sb.state.accounts["ali@users.beyond-erp.invalid"], "alipw123")  # Alpha's ali untouched
            self.assertEqual(fn({"username": "ali.beta", "password": "betapw1"})["username"], "ali.beta")
            # adding the very same person on purpose (by their email, no password) is allowed; they keep their password
            self.assertTrue(fn({"username": "ali", "email": "ali@users.beyond-erp.invalid"})["shared"])
            # switching company on a connected device is refused (disconnect first)
            self.assertEqual(api(0, a, "POST", "/api/cloud/company", {"action": "choose", "company": beta})[0], 400)
        finally:
            os.environ.pop("BEYOND_SUPABASE_URL", None)
            os.environ.pop("BEYOND_SUPABASE_ANON_KEY", None)
            for srv, db, _ in nodes:
                srv.cloud.stop()
                srv.shutdown()
                srv.server_close()
                db.close()
            sb.shutdown()
            tmp.cleanup()


class Documents(unittest.TestCase):
    def setUp(self):
        self.db = seeded()

    def test_boq_invoice_offer_files(self):
        import io as _io
        import zipfile as _zip
        from erp import documents
        bid = a.save_boq(self.db, {"title": "Q", "client": "Cl", "lines": [
            {"category": "Demolition", "item_no": "1.1", "description": "Break wall", "unit": "m2", "qty": 10, "price": 100},
            {"category": "Painting", "item_no": "2.1", "description": "Paint", "unit": "m2", "qty": 0, "price": 50}]})
        data, name = documents.boq_file(self.db, bid, "xlsx")
        z = _zip.ZipFile(_io.BytesIO(data))
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("rightToLeft", sheet)
        self.assertIn("SUM(", sheet)
        self.assertIn("xl/media/logo.png", z.namelist())
        html, _ = documents.boq_file(self.db, bid, "pdf")
        self.assertIn("إجمالي الأعمال الداخلية", html.decode())
        x = a.save_extract(self.db, {"bu_code": "PRJ-1", "boq_id": bid, "number": "Inv 2", "date": "2026-04-01",
                                     "lines": [{"category": "Demolition", "item_no": "1.1", "description": "Break wall", "qty": 4, "price": 100}]})
        data, name = documents.invoice_file(self.db, x, "xlsx")
        self.assertTrue(name.endswith("Inv. Inv 2.xlsx"))
        self.assertIn("E", _zip.ZipFile(_io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode())
        t = documents.list_offer_types(self.db)[0]
        oid = documents.save_offer(self.db, dict(t, type_id=t["id"], client="Mr. X", project="Flat"))
        o = documents.get_offer(self.db, oid)
        self.assertEqual(o["net"], 50000)
        docx = _zip.ZipFile(_io.BytesIO(documents.offer_docx(self.db, o)))
        body = docx.read("word/document.xml").decode()
        self.assertIn("Fifty Thousand Egyptian Pounds", body)
        self.assertIn("word/media/watermark.png", docx.namelist())   # offers get the watermark by default
        self.assertEqual(documents.words_ar(60000), "ستون ألف")

    def test_banks_on_offers(self):
        from erp import documents
        self.db.set_setting("bank_name", "Old Bank")                 # books from before the bank list
        self.db.set_setting("bank_account", "111")
        self.assertEqual([(b["id"], b["acct"]) for b in documents.banks(self.db)], [("main", "111")])
        self.db.set_setting("banks", json.dumps([{"id": "cib", "label": "CIB", "bank": "CIB", "acct": "222"},
                                                 {"id": "nbe", "label": "NBE", "bank": "NBE", "acct": "333"}]))
        self.db.set_setting("bank_default", "nbe")
        self.assertEqual(documents.company(self.db)["bank"]["acct"], "333")
        t = documents.list_offer_types(self.db)[0]

        def bank_text(pick, details=None):
            secs = [dict(x) for x in t["sections"]]
            for x in secs:
                if x["kind"] == "bank":
                    x.update(bank_id=pick, **({"bank_details": details} if details else {}))
            oid = documents.save_offer(self.db, dict(t, type_id=t["id"], client="Z", sections=secs))
            blocks, _ = documents.offer_blocks(self.db, documents.get_offer(self.db, oid))
            return json.dumps(blocks, ensure_ascii=False)
        self.assertIn("333", bank_text(""))                               # default
        self.assertIn("222", bank_text("cib"))                            # one of the saved banks
        out = bank_text("custom", {"bank": "QNB", "acct": "999", "evil": "x"})
        self.assertIn("999", out)
        self.assertNotIn("333", out)

    def test_lock_date_and_json_extras(self):
        self.db.set_setting("lock_date", "2026-03-31")
        with self.assertRaises(a.ValidationError):
            a.record_expense(self.db, {"description": "late", "amount": 5, "date": "2026-03-15"})
        a.record_expense(self.db, {"description": "ok", "amount": 5, "date": "2026-04-01"})
        from erp import documents
        t = documents.list_offer_types(self.db)[0]
        documents.save_offer(self.db, dict(t, type_id=t["id"], client="Y"))
        dump = interchange.to_json(self.db).encode()
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(dump), mode="replace")
        self.assertEqual(db2.one("SELECT COUNT(*) n FROM offers")["n"], 1)
        self.assertNotIn("eta_client_secret", dump.decode())


class Taxes(unittest.TestCase):
    def setUp(self):
        self.db = seeded()
        a.save_party(self.db, {"name": "Client Co", "type": "customer", "tax_id": "123456789", "governate": "Cairo",
                               "region": "Nasr City", "street": "Makram Ebeid", "building": "5"})

    def test_purchase_vat_and_withholding(self):
        a.record_project_cost(self.db, {"bu_code": "PRJ-1", "description": "tiles", "vendor": "TileCo", "qty": 1,
                                        "rate": 10000, "vat_code": "VAT14", "wht_code": "WHT1-S", "paid": 5000})
        self.assertEqual(a.party_balance(self.db, "2010", "TileCo"), 10000 + 1400 - 100 - 5000)
        t = a.tax_summary(self.db)
        self.assertEqual(t["input_vat"], 1400.0)
        self.assertEqual(t["wht_payable_balance"], 100.0)
        self.assertTrue(all(c["ok"] for c in a.audit(self.db)))

    def test_invoice_vat_and_client_withholding(self):
        x = a.save_extract(self.db, {"bu_code": "PRJ-1", "number": "INV-T", "customer": "Client Co",
                                     "vat_code": "VAT14", "wht_code": "WHT1-C",
                                     "lines": [{"description": "Work", "qty": 2, "price": 50000}]})
        a.post_extract(self.db, x)
        e = a.get_entry(self.db, self.db.one("SELECT entry_id FROM extracts WHERE id=?", (x,))["entry_id"])
        amounts = {ln["account"]: (ln["debit"], ln["credit"]) for ln in e["lines"]}
        self.assertEqual(amounts["1100"], (113000.0, 0.0))
        self.assertEqual(amounts["1330"], (1000.0, 0.0))
        self.assertEqual(amounts["2200"], (0.0, 14000.0))

    def test_vat_inclusive_cash_receipt(self):
        a.record_collection(self.db, {"bu_code": "PRJ-2", "amount": 11400, "vat_code": "VAT14"})
        self.assertEqual(a.tax_summary(self.db)["output_vat"], 1400.0)

    def test_party_validation(self):
        with self.assertRaises(a.ValidationError):
            a.save_party(self.db, {"name": "Bad", "tax_id": "12"})


class EInvoice(unittest.TestCase):
    def test_document_and_serialization(self):
        from erp import einvoice
        db = seeded()
        a.save_party(db, {"name": "Client Co", "tax_id": "123456789", "governate": "Cairo", "region": "Nasr",
                          "street": "X", "building": "1"})
        for k, v in {"company_tax_id": "987654321", "eta_activity_code": "4330", "eta_governate": "Giza",
                     "eta_region": "Dokki", "eta_street": "Y", "eta_building": "2",
                     "eta_default_item_code": "EG-987654321-1"}.items():
            db.set_setting(k, v)
        x = a.save_extract(db, {"bu_code": "PRJ-1", "number": "INV-E", "customer": "Client Co", "date": "2026-03-01",
                                "vat_code": "VAT14", "wht_code": "WHT1-C",
                                "lines": [{"description": "Work", "qty": 1, "price": 1000, "unit": "م٢"}]})
        a.post_extract(db, x)
        doc, problems = einvoice.build_document(db, x)
        self.assertEqual(problems, [])
        self.assertEqual(doc["totalAmount"], 1130.0)                 # 1000 + 140 VAT − 10 WHT
        self.assertEqual(doc["invoiceLines"][0]["unitType"], "MTK")
        self.assertEqual({t["taxType"] for t in doc["taxTotals"]}, {"T1", "T4"})
        ser = einvoice.serialize({"issuer": {"id": "1"}, "lines": [{"a": 1}, {"a": 2}]})
        self.assertEqual(ser, '"ISSUER""ID""1""LINES""LINES""A""1""LINES""A""2"')
        if HAVE_XL:
            self.assertEqual(einvoice.batch_xlsx(db, [x])[:2], b"PK")

    def test_missing_setup_is_reported(self):
        from erp import einvoice
        db = seeded()
        x = db.one("SELECT id FROM extracts")["id"]
        _, problems = einvoice.build_document(db, x)
        self.assertTrue(any("tax registration" in p for p in problems))


class ExcelSafety(unittest.TestCase):
    @unittest.skipUnless(HAVE_XL, "openpyxl not installed")
    def test_no_formula_exceeds_excel_limit_and_labels_are_english(self):
        import io as _io
        import re
        import openpyxl
        from erp import workbook
        wb = openpyxl.load_workbook(_io.BytesIO(workbook.build_workbook(seeded())))
        longest = max(len(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row
                      if isinstance(c.value, str) and c.value.startswith("="))
        self.assertLess(longest, 8192)
        self.assertFalse(re.search("[؀-ۿ]", str(wb["Income Statement"]["A6"].value)))
        self.assertEqual(wb["Direct Costs"].cell(4, 1).value, "Date")


class StandaloneWorkbook(unittest.TestCase):
    """The Excel workbook keeps its own books: every sheet that moves money posts to its Trial Balance."""

    @unittest.skipUnless(HAVE_XL, "openpyxl not installed")
    def test_new_sheets_and_postings(self):
        import io as _io
        import openpyxl
        from erp import demo, workbook
        wb = openpyxl.load_workbook(_io.BytesIO(demo.workbook_bytes(lambda: Database(":memory:"))))
        for name in ("Receivables", "Budget vs Actual", "Scaffold Calculator"):
            self.assertIn(name, wb.sheetnames)
        posting = {s["name"] for s in interchange.SHEETS if s.get("legs")}
        for name in ("Fixed Assets", "Invoice Lines", "Sales Contracts", "Loans", "Custody Expenses", "Service Subscriptions"):
            self.assertIn(name, posting)
        # the report period is always there, even when the given rows leave it out
        keys = [wb["Settings"].cell(r, 1).value for r in range(5, 8)]
        self.assertIn("report_to", keys)
        sc = wb["Scaffold Calculator"]
        self.assertTrue(any(str(c.value or "").startswith("SERVICES TOTAL") for c in sc["A"]))

    def test_app_export_does_not_book_depreciation_twice(self):
        db = seeded()
        assets.save(db, {"code": "FA-T1", "name": "Truck", "account": "1420", "acquired": "2026-01-10", "cost": 120000,
                         "life_months": 60, "pay": "none"})
        rows = interchange.export_data(db)["Fixed Assets"]
        self.assertTrue(rows and all(r["book_depr"] == "No" for r in rows))

    @unittest.skipUnless(HAVE_XL and __import__("shutil").which("soffice"), "LibreOffice not installed")
    def test_demo_workbook_matches_the_app(self):
        """Recalculate the demo workbook in LibreOffice and compare its Trial Balance with the same rows imported."""
        import subprocess
        import openpyxl
        from erp import demo, recurring, services
        sheets = demo.all_sheets(lambda: Database(":memory:"))
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "demo.xlsx"
            src.write_bytes(demo.workbook_bytes(lambda: Database(":memory:")))
            subprocess.run(["soffice", "--headless", "--convert-to", "xlsx", "--outdir", str(Path(d) / "out"), str(src)],
                           capture_output=True, timeout=600)
            wb = openpyxl.load_workbook(Path(d) / "out" / "demo.xlsx", data_only=True)
        st = {r[0]: r[1] for r in wb["Settings"].iter_rows(min_row=5, values_only=True) if r[0]}
        to = str(st["report_to"])[:10]
        db = Database(":memory:")
        interchange.import_data(db, sheets)
        recurring.run(db, to, "t")
        services.run(db, to, "t")
        app = {r["account"]: (r["dr"] - r["cr"]) / 100 for r in db.q(
            "SELECT l.account, SUM(l.debit) dr, SUM(l.credit) cr FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
            "WHERE e.date<=? AND e.status!='void' GROUP BY l.account", (to,))}
        xl = {str(r[0]): r[7] or 0 for r in wb["Trial Balance"].iter_rows(min_row=5, values_only=True) if r[0]}
        for k in set(app) | set(xl):
            self.assertAlmostEqual(app.get(k, 0), xl.get(k, 0), delta=0.05, msg=f"account {k}")
        self.assertEqual([c.value for c in wb["Model Audit"]["E"] if c.value][-1], "ALL OK")


class Legacy(unittest.TestCase):
    SRC = ROOT.parent / "BEYOND_Accounting_System_V0_6_1.xlsx"

    @unittest.skipUnless(HAVE_XL and SRC.exists(), "legacy workbook not available")
    def test_legacy_boq_and_invoice(self):
        from erp import legacy
        data = legacy.read_legacy_workbook(self.SRC)
        db = Database(":memory:")
        interchange.import_data(db, data, mode="replace")
        self.assertEqual(db.one("SELECT COUNT(*) n FROM boqs")["n"], 2)
        x = a.get_extract(db, db.one("SELECT id FROM extracts")["id"])
        self.assertEqual(x["total"], 81256.0)
        self.assertTrue(all(ln["category"] for ln in x["lines"]))


class BrowserAdapter(unittest.TestCase):
    def test_jsdb_adapter_runs_the_engine(self):
        """Emulate sql.js's JSON contract with real SQLite and run the engine through erp.jsdb."""
        import sqlite3
        from erp import jsdb
        raw = sqlite3.connect(":memory:", isolation_level=None)

        def runner(sql, params_json):
            try:
                params = json.loads(params_json)
                if params is None:
                    raw.executescript(sql)
                    return json.dumps({"rows": [], "cols": []})
                cur = raw.execute(sql, params)
                rows = [list(r) for r in cur.fetchall()]
                out = {"rows": rows, "cols": [d[0] for d in (cur.description or [])], "changes": raw.total_changes}
                if sql.lstrip().upper().startswith(("INSERT", "REPLACE")):
                    out["lastId"] = cur.lastrowid
                return json.dumps(out)
            except sqlite3.Error as e:
                return json.dumps({"error": str(e)})

        db = Database("/tmp/x.sqlite3", connection=jsdb.connect(runner))
        db.x("INSERT INTO business_units(code,name) VALUES ('B','Unit')")
        a.record_collection(db, {"bu_code": "B", "amount": 100})
        a.record_expense(db, {"description": "rent", "amount": 40})
        self.assertEqual(a.income_statement(db)["net_profit"], 60.0)
        self.assertTrue(all(c["ok"] for c in a.audit(db)))
