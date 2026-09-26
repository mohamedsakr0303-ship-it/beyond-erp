"""Beyond ERP test-suite (stdlib unittest — run:  python -m unittest discover tests)."""
import http.cookiejar
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from erp import accounting as a  # noqa: E402
from erp import auth, interchange, reports  # noqa: E402
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
                     "payables", "stock", "receivables"):
            for lang in ("en", "ar"):
                tb = reports.build(db, name, {"date_from": "2026-01-01", "date_to": "2026-12-31", "compare": "quarter",
                                              "lang": lang})
                self.assertTrue(tb["rows"], name)
                self.assertTrue(reports.to_html(tb).startswith("<!doctype html>"))
                self.assertEqual(reports.to_docx(tb)[:2], b"PK")
                if HAVE_XL:
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
                "settings", "audit_log")}
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
        self.assertEqual(len(self.sb.state.ops), 1)
        self.assertEqual(self.node_call(0, a, "POST", "/api/users", {"username": "clerk", "password": "clerk1234",
                                                                     "perms": {"sales": "edit"}})[0], 200)
        self.assertNotIn("clerk1234", json.dumps(self.sb.state.ops))  # passwords never leave the device
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
        n = len(self.sb.state.ops)
        self.assertEqual(self.node_call(0, c, "POST", "/api/docs/expense", {"description": "x", "amount": 5})[0], 403)
        self.assertEqual(len(self.sb.state.ops), n)
        self.assertEqual(self.node_call(0, c, "POST", "/api/docs/receipt", {"bu_id": bu["id"], "amount": 7})[0], 200)
        # gateway relays B's local user; the log records that user, and both copies end up identical
        self.assertEqual(self.sb.state.ops[-2]["app_user"], "admin")
        for srv, _, _ in self.nodes:
            srv.cloud.pull()
        self.assertEqual(self.dump(self.nodes[0][1]), self.dump(self.nodes[1][1]))
        self.assertEqual(self.nodes[0][1].one("SELECT COUNT(*) n FROM business_units")["n"], 10)
        # offline: changes are refused with a clear message instead of forking the books
        url = self.sb_url
        self.nodes[1][0].cloud.configure("http://127.0.0.1:9", "anon")
        st, r = self.node_call(1, b, "POST", "/api/bus", {"code": "OFF", "name": "x"})
        self.assertEqual(st, 503)
        self.assertIn("workspace", r["error"])
        self.nodes[1][0].cloud.configure(url, "anon")


if __name__ == "__main__":
    unittest.main()


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
