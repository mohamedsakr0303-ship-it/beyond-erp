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
}
ACTIONS = {
    "followup": ("Create a follow-up for someone (title from the event; due in N days)", {"assign": "username", "due_days": 0, "title": "optional prefix"},
                 "إنشاء متابعة لشخص"),
    "chat": ("Post a message in a team chat room (mention people with @username)", {"room": "room name", "message": "optional text before the event"},
             "إرسال رسالة في غرفة دردشة الفريق"),
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
    settings = {k: d.get("settings", {}).get(k, v) for k, v in TRIGGERS[trig][2].items()} if isinstance(d.get("settings"), dict) else dict(TRIGGERS[trig][2])
    for k, v in settings.items():
        if not isinstance(v, (int, float)) or v < 0 or v > 100000000:
            try:
                settings[k] = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError):
                raise ValidationError(f"{k} must be a number") from None
    params = d.get("params") if isinstance(d.get("params"), dict) else {}
    params = {k: text(params.get(k), maxlen=500) if k != "due_days" else int(params.get(k) or 0) for k in ACTIONS[act][1]}
    if act == "followup" and params.get("assign") and not db.one("SELECT 1 FROM users WHERE username=?", (params["assign"],)):
        raise ValidationError(f"No user called {params['assign']}")
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
def _act(db, rule, ev, user_row):
    from . import chat, crm
    p = json.loads(rule["params"] or "{}")
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


def run(db, only=None, dry=False, owner=None):
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
    for rule in db.q(f"SELECT * FROM automations WHERE {' AND '.join(w)}", p):
        user_row = db.one("SELECT * FROM users WHERE username=?", (rule["created_by"],))
        try:
            events = list(TRIGGERS[rule["trigger"]][0](db, json.loads(rule["settings"] or "{}")))
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
                    kind, tid = _act(db, rule, ev, user_row)
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


TABLES = ("automations", "automation_log")
