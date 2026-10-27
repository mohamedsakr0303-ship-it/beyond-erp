"""The owner console's database rules, on a real PostgreSQL with every migration applied (tests/pg).

Pricing of several companies, the trial on a person's first company only, orders paid by transfer with a picture,
staff permissions by area, support tickets and who may see them, and the private file buckets.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER, ALICE, BOB = "owner@platform.test", "alice@a.test", "bob@b.test"


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class OwnerConsole(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        ws = cls.ws
        ws.sql(f"insert into public.platform_admins(email) values ('{OWNER}');")
        cls.owner, cls.alice, cls.bob = ws.as_user(OWNER), ws.as_user(ALICE), ws.as_user(BOB)
        cls.a1 = cls.alice.rpc("create_company", p_name="Alpha One")["id"]          # before billing: keeps a trial
        cls.owner.rpc("create_company", p_name="Platform Co")
        cls.owner.rpc("billing_set_config", p={"enabled": True})
        cls.a2 = cls.alice.rpc("create_company", p_name="Alpha Two")["id"]          # later company of the same person
        cls.b1 = cls.bob.rpc("create_company", p_name="Bravo")["id"]                # a new customer's first company
        cls.b1_start = cls.bob.rpc("billing_status", p_company=cls.b1)["status"]
        cls.method = cls.owner.rpc("console_method_save", p={"kind": "instapay", "name": "InstaPay", "details": "owner@instapay",
                                                             "needs_proof": True})[0]["id"]

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def state(self, who, company):
        return who.rpc("billing_status", p_company=company)

    def staff(self, email, perms):
        self.owner.rpc("console_staff_save", p={"email": email, "perms": perms})
        return self.ws.as_user(email)

    # ------------------------------------------------------------------ trial and pricing
    def test_the_trial_covers_a_persons_first_company_only(self):
        self.assertEqual(self.state(self.alice, self.a1)["status"], "trial")       # existed when billing went live
        self.assertEqual(self.b1_start, "trial")                                    # bob's first company
        st = self.state(self.alice, self.a2)
        self.assertEqual((st["status"], st["can_write"]), ("unpaid", False))
        with self.assertRaisesRegex(pgtest.SQLError, "not in a subscription yet"):
            self.alice.rpc("append_op", p_company=self.a2, p_seq=1, p_version="t", p_app_user="", p_device="d", p_method="POST",
                           p_path="/api/x", p_body="{}", p_content_type="application/json", p_clock="2026-10-27 10:00:00")

    def test_companies_existing_when_this_arrives_keep_their_trial(self):
        carol = self.ws.as_user("carol@example.com")
        c1 = carol.rpc("create_company", p_name="Carol One")["id"]
        c2 = carol.rpc("create_company", p_name="Carol Two")["id"]
        self.assertEqual(self.state(carol, c2)["status"], "unpaid")
        try:      # as when the migration runs with billing already on
            self.ws.sql("update public.billing_config set one_trial_from = now() where id = 1;")
            self.assertEqual(self.state(carol, c2)["status"], "trial")
            c3 = carol.rpc("create_company", p_name="Carol Three")["id"]
            self.assertEqual(self.state(carol, c3)["status"], "unpaid")
            self.assertEqual(self.state(carol, c1)["status"], "trial")
        finally:
            self.ws.sql("update public.billing_config set one_trial_from = null where id = 1;")

    def test_plans_cover_one_company_each(self):
        plans = self.alice.rpc("billing_public")["plans"]
        self.assertEqual([p["code"] for p in plans], ["starter", "business", "pro"])           # order kept
        self.assertFalse([p for p in plans if "companies" in p])
        self.assertFalse([f for p in plans for f in p["features"] if "companies" in f.lower()])
        self.assertIn("Priority support (same business day)", plans[-1]["features"])

    def test_each_further_company_adds_the_set_share_of_the_plan(self):
        lines = [{"company_id": self.a1, "extra_users": 0}, {"company_id": self.a2, "extra_users": 2}]
        q = self.alice.rpc("billing_order_quote", p_plan="business", p_months=1, p_lines=lines)
        self.assertEqual([(x["name"], x["amount"]) for x in q["lines"]], [("Alpha Two", 5200), ("Alpha One", 4500)])
        self.assertEqual(q["amount"], 9700)                                        # each company doubles it
        self.owner.rpc("billing_set_config", p={"extra_company_pct": 50})
        try:
            self.assertEqual(self.alice.rpc("billing_order_quote", p_plan="business", p_months=12, p_lines=lines)["amount"],
                             52000 + 22500)                                        # yearly = 10 months; second company at 50 %
        finally:
            self.owner.rpc("billing_set_config", p={"extra_company_pct": 100})

    def test_only_an_administrator_of_every_company_can_order(self):
        with self.assertRaisesRegex(pgtest.SQLError, "administrators of each company"):
            self.bob.rpc("billing_order_quote", p_plan="business", p_months=1, p_lines=[{"company_id": self.a1}])

    # ------------------------------------------------------------------ paying by transfer
    def test_transfer_with_a_picture_then_approval_activates_every_company(self):
        lines = [{"company_id": self.a2, "extra_users": 0}]
        o = self.alice.rpc("billing_order_open", p_plan="business", p_months=1, p_lines=lines, p_method=self.method)
        self.assertEqual((o["status"], o["amount"], o["method"]["details"]), ("open", 4500, "owner@instapay"))
        with self.assertRaisesRegex(pgtest.SQLError, "picture of the transfer"):
            self.alice.rpc("billing_order_submit", p_order=o["id"], p_proof=[], p_ref="", p_amount=4500, p_note="")
        with self.assertRaisesRegex(pgtest.SQLError, "uploaded by you"):
            self.alice.rpc("billing_order_submit", p_order=o["id"], p_proof=[{"path": f"{BOB}/x.png"}], p_ref="", p_amount=4500, p_note="")
        with self.assertRaisesRegex(pgtest.SQLError, "order not found"):
            self.bob.rpc("billing_order_submit", p_order=o["id"], p_proof=[{"path": f"{BOB}/x.png"}], p_ref="", p_amount=4500, p_note="")
        s = self.alice.rpc("billing_order_submit", p_order=o["id"], p_proof=[{"path": f"{ALICE}/transfer.jpg", "name": "t.jpg"}],
                           p_ref="IPN123", p_amount=4500, p_note="paid today")
        self.assertEqual(s["status"], "submitted")
        self.assertNotIn(o["id"], [x["id"] for x in self.bob.rpc("billing_my_orders")])    # nobody else sees it
        viewer = self.staff("viewer@platform.test", {"billing": True})
        self.assertIn(o["id"], [x["id"] for x in viewer.rpc("console_orders", p_status="submitted")])
        with self.assertRaisesRegex(pgtest.SQLError, "does not have access"):
            viewer.rpc("console_order_decide", p_order=o["id"], p_approve=True, p_note="")
        self.assertEqual(self.owner.rpc("console_order_decide", p_order=o["id"], p_approve=True, p_note="ok")["status"], "approved")
        st = self.state(self.alice, self.a2)
        self.assertEqual((st["status"], st["can_write"], st["plan"]), ("active", True, "business"))
        ov = self.owner.rpc("console_overview", p_from=None, p_to=None, p_by="month")
        self.assertGreaterEqual(sum(x["real"] for x in ov["series"]), 4500)
        self.assertGreaterEqual(ov["mrr"], 4500)

    def test_a_rejection_needs_a_reason_the_customer_sees(self):
        o = self.bob.rpc("billing_order_open", p_plan="starter", p_months=1, p_lines=[{"company_id": self.b1}], p_method=self.method)
        self.bob.rpc("billing_order_submit", p_order=o["id"], p_proof=[{"path": f"{BOB}/p.png"}], p_ref="", p_amount=1, p_note="")
        with self.assertRaisesRegex(pgtest.SQLError, "write the reason"):
            self.owner.rpc("console_order_decide", p_order=o["id"], p_approve=False, p_note="")
        self.owner.rpc("console_order_decide", p_order=o["id"], p_approve=False, p_note="The amount sent was 1 EGP")
        mine = self.bob.rpc("billing_my_orders")
        self.assertEqual((mine[0]["status"], mine[0]["review_note"]), ("rejected", "The amount sent was 1 EGP"))
        self.assertNotIn("reviewed_by", mine[0])

    def test_a_card_payment_of_the_exact_amount_approves_the_order(self):
        card = self.owner.rpc("console_method_save", p={"kind": "card", "name": "Card", "needs_proof": False})
        cid = [m for m in card if m["kind"] == "card"][0]["id"]
        o = self.bob.rpc("billing_order_open", p_plan="starter", p_months=1, p_lines=[{"company_id": self.b1}], p_method=cid)
        svc = self.ws.as_user("", role="service_role")
        with self.assertRaisesRegex(pgtest.SQLError, "amount does not match"):
            svc.rpc("billing_paid", p_reference=o["ref"], p_txn="T1", p_amount_cents=100, p_success=True)
        self.assertEqual(svc.rpc("billing_paid", p_reference=o["ref"], p_txn="T2", p_amount_cents=round(o["amount"] * 100), p_success=True)["status"], "paid")
        self.assertEqual(self.state(self.bob, self.b1)["status"], "active")

    # ------------------------------------------------------------------ support
    def test_support_reaches_the_team_and_stays_private(self):
        t = self.alice.rpc("support_open", p_company=self.a1, p_kind="issue", p_subject="Receipt does not save", p_body="Steps…",
                           p_diag={"version": "1.2"}, p_log=[{"t": "api", "path": "/api/x", "status": 500}],
                           p_files=[{"path": f"{ALICE}/screen.mp4", "name": "screen.mp4", "type": "video/mp4", "size": 1000}], p_app_user="alice")
        self.assertTrue(t["ref"].startswith("BE-"))
        with self.assertRaisesRegex(pgtest.SQLError, "uploaded by you"):
            self.alice.rpc("support_open", p_company=self.a1, p_kind="issue", p_subject="x", p_body="", p_diag={}, p_log=[],
                           p_files=[{"path": f"{BOB}/x.png"}], p_app_user="")
        with self.assertRaisesRegex(pgtest.SQLError, "ticket not found"):
            self.bob.rpc("support_get", p_ticket=t["id"])
        helper = self.staff("help@platform.test", {"support": True})
        inbox = helper.rpc("console_tickets", p_status="")
        self.assertTrue([x for x in inbox if x["id"] == t["id"] and x["unread"]])
        with self.assertRaisesRegex(pgtest.SQLError, "does not have access"):
            helper.rpc("console_orders", p_status="")
        helper.rpc("support_reply", p_ticket=t["id"], p_body="Checking with the developer", p_files=[], p_internal=True)
        helper.rpc("support_reply", p_ticket=t["id"], p_body="Fixed — please try again", p_files=[{"path": "staff/fix.png"}], p_internal=False)
        seen = self.alice.rpc("support_get", p_ticket=t["id"])
        self.assertEqual(seen["status"], "answered")
        self.assertEqual([m["body"] for m in seen["messages"]], ["Fixed — please try again"])    # the internal note stays hidden
        self.assertNotIn("assignee", seen)
        self.alice.rpc("support_reply", p_ticket=t["id"], p_body="Still failing", p_files=[], p_internal=False)
        self.assertEqual(helper.rpc("support_get", p_ticket=t["id"])["status"], "open")
        self.assertEqual([x["ref"] for x in self.alice.rpc("support_mine", p_company=self.a1)], [t["ref"]])

    # ------------------------------------------------------------------ files
    def test_private_buckets_follow_the_same_rules(self):
        put = lambda who, bucket, name: who.execute(f"insert into storage.objects(bucket_id, name) values ('{bucket}', '{name}');")  # noqa: E731
        put(self.alice, "billing-proofs", f"{ALICE}/t.jpg")
        with self.assertRaises(pgtest.SQLError):
            put(self.alice, "billing-proofs", f"{BOB}/t.jpg")
        put(self.alice, "support-files", f"{ALICE}/clip.mp4")
        with self.assertRaises(pgtest.SQLError):
            put(self.alice, "support-files", "staff/x.png")
        names = lambda who, bucket: [r["name"] for r in who.select(f"select name from storage.objects where bucket_id = '{bucket}'")]  # noqa: E731
        self.assertIn(f"{ALICE}/clip.mp4", names(self.owner, "support-files"))
        self.assertNotIn(f"{ALICE}/clip.mp4", names(self.bob, "support-files"))
        self.assertIn(f"{ALICE}/t.jpg", names(self.owner, "billing-proofs"))
        self.assertEqual(names(self.bob, "billing-proofs"), [])

    # ------------------------------------------------------------------ team, notices
    def test_only_full_access_staff_manage_the_team_and_one_always_remains(self):
        limited = self.staff("limited@platform.test", {"overview": True})
        with self.assertRaisesRegex(pgtest.SQLError, "only the platform owner"):
            limited.rpc("console_staff")
        with self.assertRaisesRegex(pgtest.SQLError, "keep full access"):
            self.owner.rpc("console_staff_save", p={"email": OWNER, "perms": {"overview": True}})
        me = limited.rpc("console_me")
        self.assertEqual((me["full"], me["perms"]), (False, {"overview": True}))
        with self.assertRaisesRegex(pgtest.SQLError, "not a member of the platform team"):
            self.bob.rpc("console_me")

    def test_announcements_reach_every_customer(self):
        self.owner.rpc("console_notice_save", p={"title": "Maintenance tonight", "body": "10 pm to 11 pm", "level": "warn"})
        self.assertIn("Maintenance tonight", [n["title"] for n in self.bob.rpc("billing_public")["notices"]])
        self.assertIn("notice.save", [x["action"] for x in self.owner.rpc("console_log", p_limit=50)])


if __name__ == "__main__":
    unittest.main()
