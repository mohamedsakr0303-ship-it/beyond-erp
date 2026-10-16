"""Automations: "when this happens, do that" rules a company sets up once — the books then act by themselves.

A rule has a trigger (what to watch, with its settings) and an action (a follow-up for someone, a message in a team
chat room). Rules are checked when the app is open (every few minutes) and each thing a rule finds acts once — the
same overdue customer does not get a new follow-up every hour (automation_log keeps what already fired; a rule can
repeat after a number of days).

The AI assistant can set rules up from a plain description: it reads GET /api/automations/catalog (every trigger and
action with its settings and an example) and creates the rule with POST /api/automations."""
import json

from . import accounting as acc
from .accounting import ValidationError, now_iso, text

_dt = acc._dt


def _days_ago(n):
    return (_dt.date.fromisoformat(acc.today()) - _dt.timedelta(days=int(n))).isoformat()


def _days_ahead(n):
    return (_dt.date.fromisoformat(acc.today()) + _dt.timedelta(days=int(n))).isoformat()


# ------------------------------------------------------------------ what a rule can watch
def _t_receivable_overdue(db, p):
    """Customers owing money older than N days."""
    from . import statements
    n = int(p.get("days") or 30)
    min_amt = float(p.get("min_amount") or 0)
    d = statements.aging(db, side="receivable")
    starts = [b[0] for b in statements.BUCKETS]
    for r in d["rows"]:
        old = sum(v for v, s in zip(r["aging"], starts) if s >= n)
        if old > 0 and old >= min_amt:
            yield {"ref": f"recv:{r['party']}", "party": r["party"], "amount": round(old, 2),
                   "text": f"{r['party']} owes {old:,.2f} older than {n} days (balance {r['balance']:,.2f})"}


def _t_followup_overdue(db, p):
    n = int(p.get("days") or 2)
    for f in db.q("SELECT * FROM crm_followups WHERE done=0 AND due<=?", (_days_ago(n),)):
        yield {"ref": f"fu:{f['id']}", "party": f["party"], "who": f["assigned_to"],
               "text": f"Follow-up “{f['title']}” for {f['party'] or '—'} is {n}+ days overdue (due {f['due']}, {f['assigned_to'] or 'unassigned'})"}


def _t_stock_low(db, p):
    for r in acc.stock_report(db):
        if r.get("low"):
            yield {"ref": f"stock:{r['item_id']}:{r['warehouse_id']}", "text": f"{r['name']} is low in {r['warehouse']}: {r['qty']:g} {r['unit']} left (reorder at {r['reorder_level']:g})"}


def _t_visit_tomorrow(db, p):
    """Healthcare visits booked for tomorrow (reminders)."""
    day = _days_ahead(int(p.get("days_before") or 1))
    for v in db.q("SELECT v.*, pt.name patient, pt.phone FROM care_visits v JOIN care_patients pt ON pt.id=v.patient_id "
                  "WHERE v.date=? AND v.status='booked'", (day,)):
        yield {"ref": f"visit:{v['id']}:{day}", "party": v["patient"],
               "text": f"Remind {v['patient']} ({v['phone'] or 'no phone'}) of their visit on {day} {v['time'] or ''} — {v['number']}"}


def _t_contract_expiring(db, p):
    n = int(p.get("days") or 30)
    for c in db.q("SELECT * FROM contracts WHERE finish<>'' AND finish BETWEEN ? AND ? AND status IN ('draft','active')",
                  (acc.today(), _days_ahead(n))):
        yield {"ref": f"contract:{c['id']}", "party": c["party"] or "", "text": f"Contract {c['code']} ({c['title'] or ''}) ends on {c['finish']}"}


def _t_deal_stalled(db, p):
    n = int(p.get("days") or 14)
    for d in db.q("""SELECT d.* FROM crm_deals d WHERE d.stage NOT IN ('won','lost') AND
                     COALESCE((SELECT MAX(at) FROM crm_activities a WHERE a.deal_id=d.id), d.created_at) <= ?""", (_days_ago(n) + "T99",)):
        yield {"ref": f"deal:{d['id']}", "party": d["party"], "who": d["owner"],
               "text": f"Deal “{d['title']}” with {d['party']} has not moved for {n}+ days (stage {d['stage']})"}


def _t_new_lead(db, p):
    for r in db.q("SELECT * FROM desk_rows WHERE kind='lead' AND state='done' ORDER BY id DESC LIMIT 200"):
        x = json.loads(r["payload"] or "{}")
        yield {"ref": f"lead:{r['id']}", "party": x.get("name", ""), "text": f"New web lead: {x.get('name', '')} {x.get('phone', '')} — {r['page_title']}"}


def _t_installment_overdue(db, p):
    from . import sales
    n = int(p.get("days") or 1)
    for r in sales.installments(db, None, "overdue")["rows"]:
        if r["due"] <= _days_ago(n):
            yield {"ref": f"inst:{r['code']}:{r['due']}", "party": r["customer"],
                   "text": f"Installment of {r['customer']} on {r.get('code', '')} due {r['due']} is unpaid ({float(r.get('left') or 0):,.2f})"}


# ------------------------------------------------------------------ alerts people set for themselves (and for others)
def _local_day(p):
    return (p.get("_local") or acc.today() + "T00:00")[:10]


def _t_attendance_missing(db, p):
    """Active employees with no attendance recorded today (checked after the rule's time)."""
    day = _local_day(p)
    rows = db.q("""SELECT e.code, e.name, e.department FROM employees e WHERE e.status='active'
                   AND (e.hire_date='' OR e.hire_date<=?) AND NOT EXISTS (SELECT 1 FROM attendance a WHERE a.employee_id=e.id AND a.date=?)
                   ORDER BY e.department, e.name""", (day, day))
    if rows:
        names = ", ".join(r["name"] for r in rows[:12]) + (f" and {len(rows) - 12} more" if len(rows) > 12 else "")
        yield {"ref": f"att:{day}", "href": f"#/attendance?date={day}",
               "text": f"Attendance not recorded today for {len(rows)} employee{'s' if len(rows) != 1 else ''}: {names}"}


def _t_absences(db, p):
    """Employees marked absent / sick / on leave today."""
    day = _local_day(p)
    rows = db.q("""SELECT e.name, a.status FROM attendance a JOIN employees e ON e.id=a.employee_id
                   WHERE a.date=? AND a.status IN ('absent','sick','leave') ORDER BY a.status, e.name""", (day,))
    if rows and len(rows) >= int(p.get("min_count") or 1):
        yield {"ref": f"abs:{day}", "href": f"#/attendance?date={day}",
               "text": f"{len(rows)} away today: " + ", ".join(f"{r['name']} ({r['status']})" for r in rows[:15])}


def _t_myday_not_started(db, p):
    """Team members (with "My day") who have not started their working day."""
    day = _local_day(p)
    want = [u.strip() for u in str(p.get("people") or "").split(",") if u.strip()]
    users = db.q("SELECT username, full_name FROM users WHERE active=1" + (f" AND username IN ({','.join('?' * len(want))})" if want else ""), want)
    late = [u for u in users if not db.one("SELECT 1 FROM workdays WHERE username=? AND date=?", (u["username"], day))]
    if want and late:
        yield {"ref": f"myday:{day}", "href": "#/workdays",
               "text": "Not started their day yet: " + ", ".join(u["full_name"] or u["username"] for u in late)}


def _t_bill_due(db, p):
    """Company subscriptions & bills due within N days (or overdue)."""
    from . import billsubs
    n = int(p.get("days") or 3)
    for r in billsubs.listing(db)["rows"]:
        if r["active"] and r["days_left"] <= n:
            yield {"ref": f"bill:{r['id']}:{r['next_due']}", "href": f"#/bills/{r['id']}",
                   "text": f"{r['name']} {'was due' if r['days_left'] < 0 else 'is due'} {r['next_due']}: {r['amount']:,.2f}" + (f" ({r['currency_note']})" if r["currency_note"] else "")}


def _t_cheque_due(db, p):
    """Customer cheques due for deposit within N days."""
    n = int(p.get("days") or 2)
    for c in db.q("""SELECT c.*, s.customer, s.code FROM sale_cheques c JOIN sales_orders s ON s.id=c.sale_id
                     WHERE c.status='pending' AND c.due<=? ORDER BY c.due""", (_days_ahead(n),)):
        yield {"ref": f"chq:{c['id']}", "party": c["customer"], "href": "#/installments",
               "text": f"Cheque {c['number'] or ''} from {c['customer']} ({c['bank'] or ''}) for {acc.to_major(c['amount']):,.2f} is due {c['due']} — deposit it"}


def _t_loan_due(db, p):
    """Loan instalments (borrowed or lent) due within N days."""
    from . import loans
    n = int(p.get("days") or 3)
    until = _days_ahead(n)
    for ln in db.q("SELECT * FROM loans WHERE status='active'") if db.one("SELECT 1 FROM pragma_table_info('loans') WHERE name='status'") else db.q("SELECT * FROM loans"):
        try:
            st = loans.schedule_status(db, ln)
        except Exception:     # noqa: BLE001 — a loan without a schedule
            continue
        for r in st.get("rows") or []:
            if r.get("left") and r["due"] <= until:
                who = "pay" if ln["direction"] == "borrowed" else "collect from"
                yield {"ref": f"loan:{ln['id']}:{r['due']}", "party": ln["party"], "href": f"#/loans/{ln['id']}",
                       "text": f"Loan {ln['code']}: {who} {ln['party']} {acc.to_major(r['left']):,.2f} by {r['due']}"}
                break


def _t_cash_low(db, p):
    """Cash and bank together fall below an amount."""
    floor = float(p.get("below") or 0)
    bal = acc.to_major(db.one("""SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                                  JOIN accounts a ON a.code=l.account WHERE e.status='posted' AND a.subtype='cash'""")["n"])
    if bal < floor:
        yield {"ref": f"cash:{_local_day(p)}", "href": "#/cash", "text": f"Cash & bank are at {bal:,.2f} — below {floor:,.2f}"}


def _t_approvals_waiting(db, p):
    """Entries waiting for approval longer than N hours."""
    h = int(p.get("hours") or 24)
    cut = (acc._dt.datetime.fromisoformat(acc.now_iso()) - acc._dt.timedelta(hours=h)).isoformat(timespec="seconds")
    rows = db.q("SELECT id, summary, created_by FROM approvals WHERE status='pending' AND created_at<>'' AND created_at<=?", (cut,))
    if rows:
        yield {"ref": f"appr:{','.join(str(r['id']) for r in rows[:20])}", "href": "#/approvals",
               "text": f"{len(rows)} entr{'ies' if len(rows) != 1 else 'y'} waiting for approval more than {h} hours"}


def _t_maintenance_due(db, p):
    """Equipment maintenance due or overdue."""
    from . import equipment
    for m in equipment.alerts(db)["maintenance_due"]:
        yield {"ref": f"maint:{m['code']}:{m['plan']}:{m['due_date'] or m['due_hours']}", "href": "#/maintenance",
               "text": f"{m['name']} ({m['code']}): {m['plan']} is {m['state']}" + (f" — due {m['due_date']}" if m["due_date"] else "")}


def _t_budget_over(db, p):
    """A cost account has used more than X% of its budget so far this year."""
    from . import budgets
    limit = float(p.get("pct") or 100)
    rep = budgets.report(db)
    for sec in rep["sections"]:
        if sec["type"] == "Revenue":
            continue
        for r in sec["rows"]:
            if r["used_pct"] is not None and r["used_pct"] >= limit:
                yield {"ref": f"budget:{rep['year']}:{r['account']}:{int(limit)}", "href": "#/budget-report",
                       "text": f"{r['name']} has used {r['used_pct']:g}% of its budget so far ({r['actual']:,.2f} of {r['budget']:,.2f})"}


def _t_payroll_not_run(db, p):
    """This month's payroll is not posted by day N of the month."""
    day = _local_day(p)
    if int(day[8:10]) < int(p.get("day") or 25):
        return
    period = day[:7]
    if not db.one("SELECT 1 FROM payroll_runs WHERE period=? AND status='posted'", (period,)) and db.one("SELECT 1 FROM employees WHERE status='active'"):
        yield {"ref": f"payroll:{period}", "href": "#/payruns", "text": f"Payroll for {period} is not posted yet"}


def _t_project_over_cost(db, p):
    """A live-fee project whose costs have passed what the client has paid (the company is funding it)."""
    for b in acc.bu_summaries(db):
        if b["basis"] == acc.LIVE_FEE and b["status"] not in ("Completed", "Closed") and b["live_gap"] < -float(p.get("by") or 0):
            yield {"ref": f"gap:{b['id']}:{_local_day(p)[:7]}", "href": f"#/bu/{b['id']}",
                   "text": f"{b['code']} {b['name']}: costs are {-b['live_gap']:,.2f} ahead of what the client has paid"}


TRIGGERS = {
    "receivable_overdue": (_t_receivable_overdue, "Customers owing money older than N days", {"days": 30, "min_amount": 0},
                           "حين يتأخر عميل في السداد أكثر من N يوماً"),
    "followup_overdue": (_t_followup_overdue, "A CRM follow-up is N days overdue", {"days": 2}, "حين تتأخر متابعة N يوماً"),
    "stock_low": (_t_stock_low, "A stock item falls to its reorder level", {}, "حين يصل صنف إلى حد إعادة الطلب"),
    "visit_tomorrow": (_t_visit_tomorrow, "Healthcare visits booked N days ahead (reminders)", {"days_before": 1}, "زيارات طبية محجوزة بعد N يوم (تذكير)"),
    "contract_expiring": (_t_contract_expiring, "A contract ends within N days", {"days": 30}, "حين ينتهي عقد خلال N يوماً"),
    "deal_stalled": (_t_deal_stalled, "A deal has not moved for N days", {"days": 14}, "حين لا تتحرك صفقة N يوماً"),
    "new_lead": (_t_new_lead, "A web form brings a new lead", {}, "حين يصل عميل محتمل جديد من نموذج ويب"),
    "installment_overdue": (_t_installment_overdue, "An installment is N days unpaid", {"days": 1}, "حين يتأخر قسط N يوماً"),
    "attendance_missing": (_t_attendance_missing, "Attendance not recorded for employees today", {}, "لم تُسجَّل حضور الموظفين اليوم"),
    "absences": (_t_absences, "Employees absent / sick / on leave today (at least N)", {"min_count": 1}, "موظفون غائبون / مرضى / في إجازة اليوم"),
    "myday_not_started": (_t_myday_not_started, "Chosen people have not started “My day”", {"people": ""}, "أشخاص لم يبدؤوا «يومي» بعد"),
    "bill_due": (_t_bill_due, "A company subscription / bill is due within N days", {"days": 3}, "اشتراك / فاتورة للشركة مستحقة خلال N يوم"),
    "cheque_due": (_t_cheque_due, "A customer cheque is due within N days", {"days": 2}, "شيك عميل يستحق خلال N يوم"),
    "loan_due": (_t_loan_due, "A loan instalment is due within N days", {"days": 3}, "قسط قرض يستحق خلال N يوم"),
    "cash_low": (_t_cash_low, "Cash & bank fall below an amount", {"below": 50000}, "النقدية والبنوك أقل من مبلغ"),
    "approvals_waiting": (_t_approvals_waiting, "Entries wait for approval more than N hours", {"hours": 24}, "قيود تنتظر الاعتماد أكثر من N ساعة"),
    "maintenance_due": (_t_maintenance_due, "Equipment maintenance is due", {}, "صيانة معدة مستحقة"),
    "budget_over": (_t_budget_over, "A cost passes X% of its budget", {"pct": 90}, "تكلفة تتجاوز X% من موازنتها"),
    "payroll_not_run": (_t_payroll_not_run, "This month's payroll is not posted by day N", {"day": 25}, "رواتب الشهر لم تُرحّل حتى يوم N"),
    "project_over_cost": (_t_project_over_cost, "A live-fee project's costs pass what the client paid (by more than N)", {"by": 0}, "تكاليف مشروع بالنسبة الحية تتجاوز مدفوعات العميل"),
}
# what a person must be allowed to see to be alerted about it (checked when the alert is set up and each time it runs)
TRIGGER_PERM = {"receivable_overdue": "sales.receivables", "followup_overdue": "crm.followups", "stock_low": "stock.balances",
                "visit_tomorrow": "care.visits", "contract_expiring": "contracts.contracts", "deal_stalled": "crm.deals", "new_lead": "crm.desk",
                "installment_overdue": "shop.installments", "attendance_missing": "payroll.attendance", "absences": "payroll.attendance",
                "myday_not_started": "payroll.workdays", "bill_due": "purchasing.bills", "cheque_due": "shop.installments",
                "loan_due": "accounting.loans", "cash_low": "assets.cash", "approvals_waiting": "accounting.approvals",
                "maintenance_due": "equipment.maintenance", "budget_over": "reports.budget", "payroll_not_run": "payroll.runs",
                "project_over_cost": "business_units.units"}


def allowed(user, trigger):
    from . import auth
    return bool(user) and auth.can(user, TRIGGER_PERM.get(trigger, "settings.automations"), "view")


# settings that are not numbers (names, times)
TEXT_SETTINGS = {"people", "at"}
ACTIONS = {
    "followup": ("Create a follow-up for someone (title from the event; due in N days)", {"assign": "username", "due_days": 0, "title": "optional prefix"},
                 "إنشاء متابعة لشخص"),
    "chat": ("Post a message in a team chat room (mention people with @username)", {"room": "room name", "message": "optional text before the event"},
             "إرسال رسالة في غرفة دردشة الفريق"),
    "notify": ("Notify people — bell, and phone / computer notification even with the app closed", {"to": "usernames, comma separated (blank = me)"},
               "تنبيه أشخاص — الجرس وإشعار على الهاتف / الكمبيوتر حتى والتطبيق مغلق"),
}


def catalog():
    return {"triggers": [{"key": k, "does": v[1], "does_ar": v[3], "settings": v[2]} for k, v in TRIGGERS.items()],
            "actions": [{"key": k, "does": v[0], "does_ar": v[2], "settings": v[1]} for k, v in ACTIONS.items()],
            "rule": {"name": "text", "trigger": "a trigger key", "settings": "{the trigger's settings}", "action": "an action key",
                     "params": "{the action's settings}", "repeat_days": "0 = act once per item; N = may act again after N days",
                     "active": True},
            "example": {"name": "Chase late payers", "trigger": "receivable_overdue", "settings": {"days": 30, "min_amount": 1000},
                        "action": "followup", "params": {"assign": "sara", "due_days": 0, "title": "Collect"}, "repeat_days": 14}}


# ------------------------------------------------------------------ rules
def _clean(db, d):
    trig, act = d.get("trigger"), d.get("action")
    if trig not in TRIGGERS:
        raise ValidationError("Choose what the rule watches (trigger)")
    if act not in ACTIONS:
        raise ValidationError("Choose what the rule does (action)")
    given = d.get("settings") if isinstance(d.get("settings"), dict) else {}
    settings = {k: given.get(k, v) for k, v in TRIGGERS[trig][2].items()}
    at = text(given.get("at"), maxlen=5)                 # check only after this time of day (e.g. attendance by 09:30)
    if at and not acc.re.match(r"^\d{1,2}:\d{2}$", at):
        raise ValidationError("The time looks like 09:30")
    for k in TEXT_SETTINGS & set(settings):
        settings[k] = text(settings[k], maxlen=500)
    for k, v in settings.items():
        if k in TEXT_SETTINGS:
            continue
        if not isinstance(v, (int, float)) or v < 0 or v > 100000000:
            try:
                settings[k] = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError):
                raise ValidationError(f"{k} must be a number") from None
    if at:
        settings["at"] = at
    params = d.get("params") if isinstance(d.get("params"), dict) else {}
    params = {k: text(params.get(k), maxlen=500) if k != "due_days" else int(params.get(k) or 0) for k in ACTIONS[act][1]}
    if act == "followup" and params.get("assign") and not db.one("SELECT 1 FROM users WHERE username=?", (params["assign"],)):
        raise ValidationError(f"No user called {params['assign']}")
    if act == "notify":
        for u in [x.strip() for x in str(params.get("to") or "").split(",") if x.strip()]:
            if not db.one("SELECT 1 FROM users WHERE username=?", (u,)):
                raise ValidationError(f"No user called {u}")
    if act == "chat":
        if not params.get("room"):
            raise ValidationError("Name the chat room")
        if not db.one("SELECT 1 FROM chat_rooms WHERE name=? AND archived_at IS NULL", (params["room"],)):
            raise ValidationError(f"No chat room called {params['room']}")
    return (text(d.get("name"), "name", required=True, maxlen=200), trig, json.dumps(settings), act, json.dumps(params),
            max(0, int(float(d.get("repeat_days") or 0))), 0 if d.get("active") in (False, 0, "0") else 1)


def save(db, d, user, rid=None):
    vals = _clean(db, d)
    if rid:
        if not db.one("SELECT 1 FROM automations WHERE id=?", (int(rid),)):
            raise ValidationError("Not found")
        db.x("UPDATE automations SET name=?, trigger=?, settings=?, action=?, params=?, repeat_days=?, active=? WHERE id=?", vals + (int(rid),))
        return int(rid)
    return db.x("INSERT INTO automations(name,trigger,settings,action,params,repeat_days,active,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                vals + (user["username"], now_iso())).lastrowid


def delete(db, rid):
    db.x("DELETE FROM automation_log WHERE automation_id=?", (int(rid),))
    db.x("DELETE FROM automations WHERE id=?", (int(rid),))


def listing(db):
    out = []
    for r in db.q("SELECT a.*, (SELECT COUNT(*) FROM automation_log l WHERE l.automation_id=a.id) fired, "
                  "(SELECT MAX(at) FROM automation_log l WHERE l.automation_id=a.id) last_fired FROM automations a ORDER BY a.id"):
        r = dict(r)
        r["settings"], r["params"] = json.loads(r["settings"] or "{}"), json.loads(r["params"] or "{}")
        out.append(r)
    return out


def log(db, rid=None, limit=200):
    w, p = ("WHERE l.automation_id=?", [int(rid)]) if rid else ("", [])
    return db.q(f"SELECT l.*, a.name FROM automation_log l JOIN automations a ON a.id=l.automation_id {w} ORDER BY l.id DESC LIMIT ?", p + [int(limit)])


def preview(db, d):
    """What a rule would act on right now (without acting)."""
    trig = d.get("trigger")
    if trig not in TRIGGERS:
        raise ValidationError("Choose what the rule watches (trigger)")
    settings = {**TRIGGERS[trig][2], **(d.get("settings") if isinstance(d.get("settings"), dict) else {})}
    return [e["text"] for e in list(TRIGGERS[trig][0](db, settings))[:50]]


# ------------------------------------------------------------------ running them
def _act(db, rule, ev, user_row, pushes=None):
    from . import chat, crm
    p = json.loads(rule["params"] or "{}")
    if rule["action"] == "notify":
        to = [u.strip() for u in str(p.get("to") or "").split(",") if u.strip()] or [rule["created_by"]]
        to = [u for u in to if db.one("SELECT 1 FROM users WHERE username=? AND active=1", (u,))]
        href = ev.get("href") or "#/alerts"
        ids = [db.x("INSERT INTO alert_feed(username, title, body, href, level, at) VALUES (?,?,?,?,?,?)",
                    (u, rule["name"][:120], ev["text"][:500], href, "warn", now_iso())).lastrowid for u in to]
        if pushes is not None and to:
            pushes.append({"kind": "notify", "to": to, "title": rule["name"][:80], "body": ev["text"][:180], "href": href,
                           "tag": f"alert{rule['id']}", "pref": "reminders"})
        return "notify", ",".join(map(str, ids))
    if rule["action"] == "followup":
        title = (p.get("title") + ": " if p.get("title") else "") + ev["text"]
        return "followup", crm.save_followup(db, {"title": title[:300], "due": _days_ahead(int(p.get("due_days") or 0)),
                                                   "party": ev.get("party") or "", "assigned_to": p.get("assign") or ev.get("who") or rule["created_by"],
                                                   "notes": f"Created by the automation “{rule['name']}”"}, "automation")
    if rule["action"] == "chat":
        room = db.one("SELECT id FROM chat_rooms WHERE name=? AND archived_at IS NULL", (p.get("room"),))
        if not room:
            raise ValidationError(f"No chat room called {p.get('room')}")
        if not user_row:
            raise ValidationError("The person who made this rule no longer exists")
        body = ((p.get("message") or "").strip() + "\n" if p.get("message") else "") + f"⚙ {rule['name']}: {ev['text']}"
        return "chat", chat.send(db, user_row, room["id"], {"body": body})
    raise ValidationError("Unknown action")


def _public(u):
    from . import auth
    return auth.public_user(dict(u)) if u else None


def feed(db, username, limit=100):
    return db.q("SELECT * FROM alert_feed WHERE username=? ORDER BY id DESC LIMIT ?", (username, int(limit)))


def feed_read(db, username, ids=None):
    if ids:
        db.x(f"UPDATE alert_feed SET read_at=? WHERE username=? AND read_at IS NULL AND id IN ({','.join('?' * len(ids))})",
             [now_iso(), username, *[int(i) for i in ids]])
    else:
        db.x("UPDATE alert_feed SET read_at=? WHERE username=? AND read_at IS NULL", (now_iso(), username))
    if db.one("SELECT COUNT(*) n FROM alert_feed WHERE username=?", (username,))["n"] > 500:   # keep the newest 500 each
        db.x("DELETE FROM alert_feed WHERE username=? AND id NOT IN (SELECT id FROM alert_feed WHERE username=? ORDER BY id DESC LIMIT 500)",
             (username, username))


def run(db, only=None, dry=False, owner=None, local=None, pushes=None):
    """Check every active rule (or one; or the rules one person made — each person's app runs their own rules, so two
    devices do not act on the same thing twice) and act on what is new. Returns what happened."""
    out = []
    w, p = ["active=1"], []
    if only:
        w.append("id=?")
        p.append(int(only))
    if owner:
        w.append("created_by=?")
        p.append(owner)
    local = local if isinstance(local, str) and acc.re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", local) else None
    for rule in db.q(f"SELECT * FROM automations WHERE {' AND '.join(w)}", p):
        user_row = db.one("SELECT * FROM users WHERE username=?", (rule["created_by"],))
        if rule["action"] == "notify" and not allowed(_public(user_row), rule["trigger"]):
            continue                                   # its owner may no longer see what it watches
        settings = json.loads(rule["settings"] or "{}")
        settings["_local"] = local
        if settings.get("at") and not only:            # a rule that checks after a time of day (the person's own clock)
            now_hm = (local or now_iso())[11:16]
            h, m = (int(x) for x in settings["at"].split(":"))
            if now_hm < f"{h:02d}:{m:02d}":
                continue
        try:
            events = list(TRIGGERS[rule["trigger"]][0](db, settings))
        except Exception as e:      # noqa: BLE001 — a broken rule must not stop the others
            db.x("UPDATE automations SET last_error=?, last_run=? WHERE id=?", (str(e)[:300], now_iso(), rule["id"]))
            out.append({"rule": rule["name"], "error": str(e)})
            continue
        n = 0
        for ev in events[:200]:
            last = db.one("SELECT at FROM automation_log WHERE automation_id=? AND ref=? ORDER BY id DESC LIMIT 1", (rule["id"], ev["ref"]))
            if last and (not rule["repeat_days"] or last["at"][:10] > _days_ago(rule["repeat_days"])):
                continue
            if dry:
                out.append({"rule": rule["name"], "would": ev["text"]})
                continue
            try:
                with db.tx():
                    kind, tid = _act(db, rule, ev, user_row, pushes)
                    db.x("INSERT INTO automation_log(automation_id, ref, at, result, target) VALUES (?,?,?,?,?)",
                         (rule["id"], ev["ref"], now_iso(), ev["text"][:500], f"{kind}:{tid}"))
                n += 1
            except (ValidationError, PermissionError, LookupError) as e:
                db.x("UPDATE automations SET last_error=? WHERE id=?", (str(e)[:300], rule["id"]))
                out.append({"rule": rule["name"], "error": str(e)})
                break
        if not dry:
            db.x("UPDATE automations SET last_run=?, last_error=CASE WHEN ?>0 THEN '' ELSE last_error END WHERE id=?", (now_iso(), n, rule["id"]))
        if n:
            out.append({"rule": rule["name"], "acted": n})
    return out


TABLES = ("automations", "automation_log", "alert_feed")
