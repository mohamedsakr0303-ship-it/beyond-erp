"""Menus by industry, menu control per company, hard user limits and the Enterprise plan, on a real PostgreSQL with
every migration applied (tests/pg)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402
from erp import industries  # noqa: E402
from erp.db import MODULES  # noqa: E402

OWNER = "owner@platform.test"
OP = dict(p_seq=1, p_version="t", p_app_user="", p_device="d", p_method="POST", p_path="/api/users", p_body="{}",
          p_content_type="application/json", p_clock="2026-10-30 10:00:00")


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class MenusSeatsEnterprise(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cfg = cls.owner.rpc("billing_admin_overview")["config"]
        cls.base, cls.seeded = cfg["plans"], cfg["core_by_industry"]

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def setUp(self):
        self.owner.rpc("billing_set_config", p={"enabled": False, "trial_full": True, "plans": self.base,
                                                "core_by_industry": self.seeded})

    def plans(self, **business):
        self.owner.rpc("billing_set_config", p={"plans": [dict(p, **business) if p["code"] == "business" else p for p in self.base]})

    def company(self, email, name):
        who = self.ws.as_user(email)
        return who, who.rpc("create_company", p_name=name)["id"]

    def pay(self, cid, plan="business"):
        return self.owner.rpc("console_record_payment", p={"company": cid, "plan": plan, "months": 1, "amount": 0})

    def add(self, who, cid, email):
        return who.rpc("set_member", p_company=cid, p_email=email, p_is_admin=False, p_gateway=False, p_active=True)

    # ------------------------------------------------------------------ starter sections by industry
    def test_the_starter_sections_follow_the_industry_templates(self):
        seeded = self.seeded
        self.assertEqual(sorted(seeded), sorted(industries.ORDER))
        for k, t in industries.TYPES.items():
            self.assertEqual(seeded[k]["modules"], [m for m in MODULES if m not in t["hide"]], k)

    def test_a_company_gets_its_industrys_starter_sections(self):
        self.plans(modules=["sales"])
        self.owner.rpc("billing_set_config", p={"enabled": True, "trial_full": False,
                                                "core_by_industry": {"healthcare": {"name": "Clinics", "modules": ["care", "stock"]}}})
        a, cid = self.company("clinic@x.test", "Clinic")
        self.pay(cid)
        st = a.rpc("billing_status", p_company=cid)
        self.assertNotIn("care", st["modules"])
        st = a.rpc("company_set_industry", p_company=cid, p_industry="healthcare")
        self.assertEqual(st["industry"], "healthcare")
        self.assertIn("care", st["modules"]); self.assertIn("stock", st["modules"]); self.assertIn("sales", st["modules"])
        b, _ = self.company("other@x.test", "Other")
        with self.assertRaises(pgtest.SQLError):                         # only the company's administrators
            b.rpc("company_set_industry", p_company=cid, p_industry="general")
        # the console can set it too, and give one company a section beyond its plan
        self.owner.rpc("billing_set_company", p_company=cid, p={"industry": "", "overrides": {"modules": ["rentals"]}})
        st = a.rpc("billing_status", p_company=cid)
        self.assertNotIn("care", st["modules"]); self.assertIn("rentals", st["modules"])
        with self.assertRaisesRegex(pgtest.SQLError, "starter sections"):
            self.owner.rpc("billing_set_config", p={"core_by_industry": {"general": ["sales"]}})

    # ------------------------------------------------------------------ menus, screens and tabs per plan and company
    def test_switching_off_screens_and_tabs(self):
        self.plans(deny=["sales.offers", "reports.forecast.prices"])
        self.owner.rpc("billing_set_config", p={"enabled": True, "trial_full": False})
        a, cid = self.company("deny@x.test", "Deny Co")
        self.pay(cid)
        self.assertEqual(sorted(a.rpc("billing_status", p_company=cid)["deny"]), ["reports.forecast.prices", "sales.offers"])
        self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {"allow": ["sales.offers"], "deny": ["payroll", "bad key!"]}})
        self.assertEqual(sorted(a.rpc("billing_status", p_company=cid)["deny"]), ["payroll", "reports.forecast.prices"])
        # the same person's other company is managed on its own
        _, cid2 = a, a.rpc("create_company", p_name="Deny Two")["id"]
        self.pay(cid2)
        self.assertNotIn("payroll", a.rpc("billing_status", p_company=cid2)["deny"])
        # a company's own switches apply even when billing is off; the plan's do not
        self.owner.rpc("billing_set_config", p={"enabled": False})
        self.assertEqual(a.rpc("billing_status", p_company=cid)["deny"], ["payroll"])
        with self.assertRaisesRegex(pgtest.SQLError, "must be lists"):
            self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {"deny": "payroll"}})

    # ------------------------------------------------------------------ a set number of users is a hard limit
    def test_no_user_past_the_plan(self):
        self.plans(users=2)
        self.owner.rpc("billing_set_config", p={"enabled": True})
        a, cid = self.company("seats@x.test", "Seats Co")
        self.pay(cid)
        self.add(a, cid, "two@x.test")
        with self.assertRaisesRegex(pgtest.SQLError, "seat_limit"):
            self.add(a, cid, "three@x.test")
        with self.assertRaisesRegex(pgtest.SQLError, "seat_limit"):           # nor a new user in the books
            a.rpc("append_op", p_company=cid, **OP)
        a.rpc("append_op", p_company=cid, **dict(OP, p_path="/api/x"))      # other changes go on
        # a request to join waits, and cannot be approved past the limit
        code = self.ws.sql(f"select invite_code from companies where id = '{cid}'").strip().splitlines()[-1].strip()
        self.ws.as_user("asks@x.test").rpc("join_company", p_code=code)
        with self.assertRaisesRegex(pgtest.SQLError, "seat_limit"):
            self.add(a, cid, "asks@x.test")
        # someone leaves: the seat is free again
        a.rpc("set_member", p_company=cid, p_email="two@x.test", p_is_admin=False, p_gateway=False, p_active=False)
        self.add(a, cid, "asks@x.test")
        # a number set for the company applies even when billing is off; free without one
        self.owner.rpc("billing_set_config", p={"enabled": False})
        self.add(a, cid, "free@x.test")
        self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {"users": 3}})
        with self.assertRaisesRegex(pgtest.SQLError, "seat_limit"):
            self.add(a, cid, "four@x.test")
        self.assertEqual(a.rpc("billing_status", p_company=cid)["users_allowed"], 3)

    # ------------------------------------------------------------------ Enterprise: contact us
    def test_the_enterprise_plan(self):
        self.owner.rpc("billing_set_config", p={"enabled": True, "support_mode": "plan"})
        ent = next(p for p in self.ws.as_user("any@x.test").rpc("billing_public")["plans"] if p["code"] == "enterprise")
        self.assertTrue(ent["contact"])
        a, cid = self.company("big@x.test", "Big Group")
        with self.assertRaisesRegex(pgtest.SQLError, "contact_us"):
            a.rpc("billing_order_quote", p_plan="enterprise", p_months=1, p_lines=[{"company_id": cid}])
        t = a.rpc("enterprise_request", p_company=cid, p={"users": "60", "companies": "4", "phone": "0100", "note": "Two branches"})
        self.assertEqual(t["kind"], "enterprise")
        self.assertIn("Users: 60", t["body"])
        # the platform team sets the deal up: the plan, the agreed users, the agreed price
        self.owner.rpc("console_record_payment", p={"company": cid, "plan": "enterprise", "months": 12, "amount": 90000})
        st = a.rpc("billing_status", p_company=cid)
        self.assertEqual((st["plan"], st["contact"], st["users_allowed"], st["modules"]), ("enterprise", True, None, None))
        self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {"users": 60}})
        self.assertEqual(a.rpc("billing_status", p_company=cid)["users_allowed"], 60)
        # a plan without users is refused unless it is "contact us"
        bad = [dict(p, users=None) if p["code"] == "starter" else p for p in self.base]
        with self.assertRaisesRegex(pgtest.SQLError, "at least one user"):
            self.owner.rpc("billing_set_config", p={"plans": bad})

    def test_the_console_layout_is_a_team_setting(self):
        lay = {"nav": {"order": ["overview", "customers"], "names": {"customers": "Clients"}, "hidden": ["ledger"]}, "locked": True}
        self.assertEqual(self.owner.rpc("console_setting_save", p_key="console_layout", p_value=lay), lay)
        with self.assertRaises(pgtest.SQLError):
            self.ws.as_user("nobody@x.test").rpc("console_setting_save", p_key="console_layout", p_value=lay)


class ConsoleKeepsInStepWithTheApp(unittest.TestCase):
    """The owner console names the ERP's industries and tabs; they must follow erp/industries.py and erp/db.py."""

    def test_industries_and_tabs(self):
        import re
        from erp.db import SUBTABS
        js = (ROOT / "web-src" / "owner.js").read_text(encoding="utf-8")
        order = json.loads(re.search(r"const IND_ORDER = (\[[^\]]*\]);", js).group(1))
        self.assertEqual(order, industries.ORDER)
        tabs = re.search(r"const TABS = \(\) => \(\{(.*?)\}\);", js, re.S).group(1)
        for screen, names in SUBTABS.items():
            self.assertIn(f'"{screen}"', tabs)
            for n in names:
                self.assertIn(f'["{n}"', tabs, f"{screen}.{n}")
        self.assertIn('<script src="i18n.js"></script>', (ROOT / "web-src" / "owner.html").read_text(encoding="utf-8"))


class WorkbooksFollowTheSubscription(unittest.TestCase):
    def test_sheets_of_what_is_switched_off_are_hidden(self):
        from types import SimpleNamespace
        from erp import server
        from erp.db import Database
        db = Database(":memory:")
        db.set_setting("hidden_modules", json.dumps(["care"]))
        ctx = lambda q: SimpleNamespace(q=q, db=db)
        self.assertIsNone(server._hide_with_off(ctx({})))                       # nothing sent: the company's own setting
        self.assertEqual(server._hide_with_off(ctx({"off": "stock,sales.offers,bad key"})), ["care", "stock", "sales.offers"])
        self.assertEqual(server._hide_with_off(ctx({"off": "rentals"}), ["shop"]), ["shop", "rentals"])


if __name__ == "__main__":
    unittest.main()
