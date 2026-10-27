"""Owner control on a real PostgreSQL with every migration applied (tests/pg).

What plans include (sections, the assistant, online support), add-ons and their prices, per-company overrides,
suspension, maintenance, blocking a person, limits on new companies, the console's notifications, archiving,
backup / restore / delete of a company, remote care of members, and support visits.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER, HELPER = "owner@platform.test", "helper@platform.test"
OP = dict(p_seq=1, p_version="t", p_app_user="", p_device="d", p_method="POST", p_path="/api/x", p_body="{}",
          p_content_type="application/json", p_clock="2026-10-27 10:00:00")


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class OwnerControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        ws = cls.ws
        ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');"
               f"insert into public.platform_admins(email, name, perms) values ('{HELPER}', 'Helper', '{{\"support\": true}}');")
        cls.owner, cls.helper = ws.as_user(OWNER), ws.as_user(HELPER)
        cls.method = cls.owner.rpc("console_method_save", p={"kind": "instapay", "name": "InstaPay", "details": "pay@x", "needs_proof": True})[0]["id"]

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def setUp(self):
        self.owner.rpc("billing_set_config", p={"enabled": False, "ai_mode": "plan", "support_mode": "free", "trial_full": True,
                                                "maintenance": {"on": False}, "accept_new": True, "max_companies": 0})

    def person(self, email):
        return self.ws.as_user(email)

    def company(self, who, name):
        return who.rpc("create_company", p_name=name)["id"]

    def state(self, who, cid):
        return who.rpc("billing_status", p_company=cid)

    def plans(self, **business):
        cfg = self.owner.rpc("billing_admin_overview")["config"]
        plans = [dict(p, **business) if p["code"] == "business" else p for p in cfg["plans"]]
        self.owner.rpc("billing_set_config", p={"plans": plans})
        return plans

    def pay(self, cid, plan="business", **extra):
        return self.owner.rpc("console_record_payment", p={"company": cid, "plan": plan, "months": 1, "amount": 0, **extra})

    # ------------------------------------------------------------------ what a company may use
    def test_billing_off_gives_everything(self):
        a = self.person("off@x.test")
        cid = self.company(a, "Off Co")
        st = self.state(a, cid)
        self.assertEqual((st["status"], st["modules"], st["ai"], st["support"]), ("free", None, True, True))
        self.owner.rpc("billing_set_config", p={"ai_mode": "off"})
        self.assertFalse(self.state(a, cid)["ai"])
        r = self.ws.as_user("", role="service_role").rpc("beyond_ai_allowed", p_company=cid, p_email="off@x.test")
        self.assertEqual(r["ok"], False)
        with self.assertRaises(pgtest.SQLError):              # only the server's AI function may ask
            a.rpc("beyond_ai_allowed", p_company=cid, p_email="off@x.test")

    def test_plan_sections_add_ons_and_assistant(self):
        self.plans(modules=["sales", "purchasing"], ai=False, support=True)
        self.owner.rpc("billing_set_config", p={"enabled": True, "module_prices": {"stock": 300, "payroll": 500}, "ai_addon": True, "ai_price": 250})
        a = self.person("mods@x.test")
        cid = self.company(a, "Mods Co")
        st = self.state(a, cid)
        self.assertEqual((st["status"], st["modules"], st["ai"]), ("trial", None, True))           # the trial sees everything
        self.owner.rpc("billing_set_config", p={"trial_full": False})
        st = self.state(a, cid)
        self.assertIn("sales", st["modules"]); self.assertIn("accounting", st["modules"]); self.assertNotIn("stock", st["modules"])
        self.assertFalse(st["ai"])
        # a quote with a section and the assistant added on
        q = a.rpc("billing_order_quote", p_plan="business", p_months=1, p_lines=[{"company_id": cid, "modules": ["stock", "sales"], "ai": True}])
        line = q["lines"][0]
        self.assertEqual(line["modules"], ["stock"])                       # sales is already in the plan
        self.assertEqual(line["addons"], 550)
        self.assertEqual(line["amount"], line["base"])
        with self.assertRaisesRegex(pgtest.SQLError, "cannot be added on its own"):
            a.rpc("billing_order_quote", p_plan="business", p_months=1, p_lines=[{"company_id": cid, "modules": ["rentals"]}])
        o = a.rpc("billing_order_open", p_plan="business", p_months=1, p_lines=[{"company_id": cid, "modules": ["stock"], "ai": True}], p_method=self.method)
        self.owner.rpc("console_order_decide", p_order=o["id"], p_approve=True, p_note="")
        st = self.state(a, cid)
        self.assertEqual(st["status"], "active")
        self.assertIn("stock", st["modules"]); self.assertNotIn("payroll", st["modules"])
        self.assertTrue(st["ai"])
        # the owner's override beats the plan
        self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {"modules": "all", "ai": False, "users": 40}})
        st = self.state(a, cid)
        self.assertEqual((st["modules"], st["ai"], st["users_allowed"]), (None, False, 40))
        self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {}})
        self.assertIsNotNone(self.state(a, cid)["modules"])

    def test_hidden_plans_are_not_offered(self):
        self.plans(hidden=True)
        codes = [p["code"] for p in self.person("anyone@x.test").rpc("billing_public")["plans"]]
        self.assertNotIn("business", codes)
        self.plans(hidden=False)

    def test_online_support_for_whom_it_is_included(self):
        self.plans(support=False)
        self.owner.rpc("billing_set_config", p={"enabled": True, "support_mode": "plan", "trial_full": False, "support_addon": True, "support_price": 100})
        a = self.person("sup@x.test")
        cid = self.company(a, "Sup Co")
        self.pay(cid)
        with self.assertRaisesRegex(pgtest.SQLError, "not included"):
            a.rpc("support_open", p_company=cid, p_kind="bug", p_subject="Help", p_body="", p_diag={}, p_log=[], p_files=[], p_app_user="")
        t = a.rpc("support_open", p_company=cid, p_kind="billing", p_subject="About my plan", p_body="", p_diag={}, p_log=[], p_files=[], p_app_user="")
        self.assertTrue(t["ref"].startswith("BE-"))
        q = a.rpc("billing_order_quote", p_plan="business", p_months=1, p_lines=[{"company_id": cid, "support": True}])
        self.assertEqual(q["lines"][0]["addons"], 100)
        self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {"support": True}})
        a.rpc("support_open", p_company=cid, p_kind="bug", p_subject="Help", p_body="", p_diag={}, p_log=[], p_files=[], p_app_user="")
        self.plans(support=True)

    # ------------------------------------------------------------------ switches
    def test_suspend_and_maintenance(self):
        a = self.person("sus@x.test")
        cid = self.company(a, "Sus Co")
        self.owner.rpc("billing_set_company", p_company=cid, p={"suspended": True, "suspend_note": "Unpaid invoice"})
        st = self.state(a, cid)
        self.assertEqual((st["status"], st["can_write"]), ("suspended", False))
        with self.assertRaisesRegex(pgtest.SQLError, "suspended — Unpaid invoice"):
            a.rpc("append_op", p_company=cid, **OP)
        self.owner.rpc("billing_set_company", p_company=cid, p={"suspended": False})
        self.owner.rpc("billing_set_config", p={"maintenance": {"on": True, "message": "Back at 3"}})
        with self.assertRaisesRegex(pgtest.SQLError, "Back at 3"):
            a.rpc("append_op", p_company=cid, **OP)
        self.assertTrue(a.rpc("billing_public")["maintenance"]["on"])
        with self.assertRaises(pgtest.SQLError):               # the helper may not switch maintenance
            self.helper.rpc("billing_set_config", p={"maintenance": {"on": False}})
        self.owner.rpc("billing_set_config", p={"maintenance": {"on": False}})
        self.assertEqual(a.rpc("append_op", p_company=cid, **OP)["seq"], 1)

    def test_blocking_a_person_and_company_limits(self):
        a = self.person("blk@x.test")
        cid = self.company(a, "Blk Co")
        self.owner.rpc("console_person_save", p={"email": "BLK@x.test", "blocked": True})
        self.assertEqual(a.rpc("my_companies"), [])
        with self.assertRaises(pgtest.SQLError):
            a.rpc("billing_status", p_company=cid)
        with self.assertRaisesRegex(pgtest.SQLError, "cannot be blocked"):
            self.owner.rpc("console_person_save", p={"email": HELPER, "blocked": True})
        self.owner.rpc("console_person_save", p={"email": "blk@x.test", "blocked": False})
        self.assertEqual(len(a.rpc("my_companies")), 1)
        # limits
        self.owner.rpc("billing_set_config", p={"max_companies": 2})
        self.company(a, "Blk Two")
        with self.assertRaisesRegex(pgtest.SQLError, "up to 2 companies"):
            self.company(a, "Blk Three")
        self.owner.rpc("console_person_save", p={"email": "blk@x.test", "max_companies": 3})
        self.company(a, "Blk Three")
        self.owner.rpc("billing_set_config", p={"accept_new": False})
        with self.assertRaisesRegex(pgtest.SQLError, "not taking new customers"):
            self.company(self.person("new@x.test"), "New Co")
        self.company(self.owner, "Staff are never limited")
        people = {p["email"]: p for p in self.owner.rpc("console_people")}
        self.assertEqual(people["blk@x.test"]["owns"], 3)
        self.assertEqual(people["blk@x.test"]["max_companies"], 3)

    # ------------------------------------------------------------------ notifications
    def test_notifications_follow_the_data_and_each_reader(self):
        self.owner.rpc("billing_set_config", p={"enabled": True, "trial_days": 2, "notify": {"trial_days": 3}})
        a = self.person("ntf@x.test")
        cid = self.company(a, "Ntf Co")
        kinds = lambda who: {(x["kind"], x["title"]) for x in who.rpc("console_alerts")}  # noqa: E731
        self.assertIn(("trial_ending", "Ntf Co"), kinds(self.owner))
        self.assertIn(("new_company", "Ntf Co"), kinds(self.owner))
        self.assertNotIn(("trial_ending", "Ntf Co"), kinds(self.helper))          # customers are not the helper's area
        t = a.rpc("support_open", p_company=cid, p_kind="bug", p_subject="Broken", p_body="", p_diag={}, p_log=[], p_files=[], p_app_user="")
        self.assertIn(("ticket", t["ref"] + " — Broken"), kinds(self.helper))
        key = next(x["key"] for x in self.owner.rpc("console_alerts") if x["kind"] == "trial_ending" and x["title"] == "Ntf Co")
        self.owner.rpc("console_alert_mark", p_keys=[key], p_state="read")
        self.assertTrue(next(x for x in self.owner.rpc("console_alerts") if x["key"] == key)["read"])
        self.owner.rpc("console_alert_mark", p_keys=[key], p_state="dismissed")
        self.assertFalse([x for x in self.owner.rpc("console_alerts") if x["key"] == key])
        self.helper.rpc("console_my_notify", p={"ticket": False})
        self.assertNotIn("ticket", {x["kind"] for x in self.helper.rpc("console_alerts")})
        self.helper.rpc("console_my_notify", p={})
        self.owner.rpc("billing_set_config", p={"trial_days": 15})

    # ------------------------------------------------------------------ looking after a company's data
    def test_archive_backup_delete_restore(self):
        a = self.person("arc@x.test")
        cid = self.company(a, "Arc Co")
        a.rpc("append_op", p_company=cid, **OP)
        self.pay(cid, amount=500, reference="R1")
        with self.assertRaisesRegex(pgtest.SQLError, "archive the company first"):
            self.owner.rpc("console_company_delete", p_company=cid, p_name="Arc Co")
        st = self.owner.rpc("console_company_archive", p_company=cid, p_archived=True)
        self.assertEqual((st["status"], st["can_write"]), ("archived", False))
        with self.assertRaisesRegex(pgtest.SQLError, "backup"):
            self.owner.rpc("console_company_delete", p_company=cid, p_name="Arc Co")
        with self.assertRaises(pgtest.SQLError):                                  # books leave only with the full owner
            self.helper.rpc("console_company_backup", p_company=cid)
        backup = self.owner.rpc("console_company_backup", p_company=cid)
        self.assertEqual((backup["format"], backup["last_seq"], len(backup["ops"])), ("beyond-company-backup", 1, 1))
        with self.assertRaisesRegex(pgtest.SQLError, "type the company"):
            self.owner.rpc("console_company_delete", p_company=cid, p_name="Arc")
        self.owner.rpc("console_company_delete", p_company=cid, p_name="arc co")
        self.assertEqual(a.rpc("my_companies"), [])
        pays = [p for p in self.owner.rpc("console_payments") if p["reference"] == "R1"]
        self.assertEqual((pays[0]["company"], pays[0]["deleted"], float(pays[0]["amount"])), ("Arc Co", True, 500.0))
        self.assertIn("Arc Co", [d["name"] for d in self.owner.rpc("console_deleted")])
        # bring it back from the file
        self.owner.rpc("console_company_restore", p_backup=json.loads(json.dumps(backup)))
        self.assertEqual([c["name"] for c in a.rpc("my_companies")], ["Arc Co"])
        self.assertEqual(self.ws.sql(f"select count(*) from ops where company_id = '{cid}';").strip(), "1")
        self.assertEqual(self.ws.sql(f"select count(*) from billing_payments where company_id = '{cid}';").strip(), "1")
        with self.assertRaisesRegex(pgtest.SQLError, "still exists"):
            self.owner.rpc("console_company_restore", p_backup=backup)

    def test_remote_care_of_members(self):
        a = self.person("care@x.test")
        cid = self.company(a, "Care Co")
        self.ws.sql(f"insert into members(company_id, email, is_admin, active, pending) values ('{cid}', 'staff1@x.test', false, true, false);")
        with self.assertRaisesRegex(pgtest.SQLError, "owner"):
            self.owner.rpc("console_member_set", p_company=cid, p_email="care@x.test", p={"remove": True})
        self.owner.rpc("console_member_set", p_company=cid, p_email="staff1@x.test", p={"is_admin": True})
        self.owner.rpc("console_company_edit", p_company=cid, p={"owner": "staff1@x.test", "name": "Care Company"})
        c = self.owner.rpc("console_company", p_company=cid)
        self.assertEqual((c["name"], c["created_by"]), ("Care Company", "staff1@x.test"))
        rows = self.owner.rpc("console_member_set", p_company=cid, p_email="care@x.test", p={"remove": True})
        self.assertEqual([m["email"] for m in rows], ["staff1@x.test"])
        with self.assertRaises(pgtest.SQLError):
            self.helper.rpc("console_member_set", p_company=cid, p_email="staff1@x.test", p={"active": False})

    # ------------------------------------------------------------------ support visits
    def test_a_support_visit_from_request_to_done(self):
        self.owner.rpc("billing_set_config", p={"visits": {"on": True, "price": 1500, "pay_when": "before"}})
        a = self.person("vis@x.test")
        cid = self.company(a, "Vis Co")
        with self.assertRaisesRegex(pgtest.SQLError, "phone"):
            a.rpc("visit_request", p_company=cid, p_reason="Printer setup", p_address="Cairo", p_phone="", p_preferred="", p_files=[], p_ticket=None)
        v = a.rpc("visit_request", p_company=cid, p_reason="Printer setup", p_address="Cairo", p_phone="0100", p_preferred="Sunday", p_files=[], p_ticket=None)
        self.assertEqual((v["status"], float(v["price"]), v["pay_when"]), ("requested", 1500.0, "before"))
        self.assertNotIn("staff_note", v)
        self.assertIn("visit", {x["kind"] for x in self.helper.rpc("console_alerts")})
        with self.assertRaises(pgtest.SQLError):                                  # someone else's visit
            self.person("other@x.test").rpc("visit_customer", p_visit=v["id"], p_action="accept", p={})
        self.helper.rpc("console_visit_set", p_visit=v["id"], p={"status": "quoted", "price": 1200, "note": "Sunday 10:00", "staff_note": "bring cable"})
        mine = a.rpc("visit_mine", p_company=cid)[0]
        self.assertEqual((mine["status"], float(mine["price"]), mine["customer_unread"]), ("quoted", 1200.0, True))
        self.assertNotIn("staff_note", mine)
        with self.assertRaisesRegex(pgtest.SQLError, "picture"):
            a.rpc("visit_customer", p_visit=v["id"], p_action="pay", p={"method": self.method, "proof": []})
        a.rpc("visit_customer", p_visit=v["id"], p_action="pay",
              p={"method": self.method, "proof": [{"path": "vis@x.test/1-r.png", "name": "r.png", "type": "image/png", "size": 9}], "ref": "T9"})
        self.assertIn("visit_paid", {x["kind"] for x in self.helper.rpc("console_alerts")})
        with self.assertRaisesRegex(pgtest.SQLError, "approve"):                  # support may not confirm money
            self.helper.rpc("console_visit_set", p_visit=v["id"], p={"pay_status": "paid"})
        self.owner.rpc("console_visit_set", p_visit=v["id"], p={"pay_status": "paid", "status": "scheduled",
                                                                 "scheduled_at": "2026-11-01T10:00:00Z", "technician": "Sam"})
        self.owner.rpc("console_visit_set", p_visit=v["id"], p={"status": "done"})
        done = a.rpc("visit_mine", p_company=cid)[0]
        self.assertEqual((done["status"], done["pay_status"], done["technician"]), ("done", "paid", "Sam"))
        with self.assertRaisesRegex(pgtest.SQLError, "reason"):
            self.owner.rpc("console_visit_set", p_visit=v["id"], p={"status": "rejected"})


if __name__ == "__main__":
    unittest.main()
