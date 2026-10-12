"""My day: field staff start and end their working day from the phone (with the place, when the company asks for it
and the person allows it), and close it with a short daily report the app drafts from what they did in it.

Managers see the team's days: who started, where and when, hours, and each day's report."""
from . import accounting as acc
from .accounting import ValidationError, now_iso, text

LOCATION = ("off", "ask", "required")


def _loc(d, need):
    lat, lng = d.get("lat"), d.get("lng")
    if lat in (None, "") or lng in (None, ""):
        if need == "required":
            raise ValidationError("This company asks for your location when you start or end the day — allow location for this app and try again")
        return None, None, None
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        raise ValidationError("Location not understood") from None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValidationError("Location not understood")
    return round(lat, 6), round(lng, 6), round(float(d.get("accuracy") or 0), 1)


def today_of(db, user):
    return db.one("SELECT * FROM workdays WHERE username=? AND date=? ORDER BY id DESC LIMIT 1", (user["username"], acc.today()))


def start(db, user, d):
    if (cur := today_of(db, user)) and not cur["end_at"]:
        raise ValidationError("Your day is already started")
    need = db.setting("workday_location") or "ask"
    lat, lng, accu = _loc(d, need)
    return db.x("INSERT INTO workdays(username, date, start_at, start_lat, start_lng, start_acc, note) VALUES (?,?,?,?,?,?,?)",
                (user["username"], acc.today(), now_iso(), lat, lng, accu, text(d.get("note"), maxlen=500))).lastrowid


def finish(db, user, d):
    cur = today_of(db, user)
    if not cur or cur["end_at"]:
        raise ValidationError("Start your day first")
    need = db.setting("workday_location") or "ask"
    lat, lng, accu = _loc(d, need)
    db.x("UPDATE workdays SET end_at=?, end_lat=?, end_lng=?, end_acc=?, report=? WHERE id=?",
         (now_iso(), lat, lng, accu, text(d.get("report"), maxlen=4000), cur["id"]))
    return cur["id"]


def activity(db, username, date=None):
    """What a person did on a day, from the books — the draft of their daily report."""
    date = date or acc.today()
    like = date + "%"
    out = []
    n = db.one("SELECT COUNT(*) n FROM journal_entries WHERE created_by=? AND created_at LIKE ?", (username, like))["n"]
    if n:
        out.append(f"Entries recorded: {n}")
    acts = db.q("SELECT kind, subject, party FROM crm_activities WHERE created_by=? AND created_at LIKE ? AND auto=0 ORDER BY id", (username, like))
    if acts:
        out.append(f"Customer contacts logged: {len(acts)}" + "".join(f"\n  - {a['kind']}: {a['subject']} ({a['party']})" for a in acts[:12]))
    fu = db.q("SELECT title, party FROM crm_followups WHERE done_by=? AND done_at LIKE ?", (username, like))
    if fu:
        out.append(f"Follow-ups done: {len(fu)}" + "".join(f"\n  - {f['title']}" for f in fu[:12]))
    try:
        vis = db.q("SELECT v.number, v.status, p.name, v.place FROM care_visits v JOIN care_patients p ON p.id=v.patient_id "
                   "WHERE v.date=? AND (v.provider=? OR v.created_by=?) AND v.status='done'", (date, username, username))
        if vis:
            out.append(f"Visits done: {len(vis)}" + "".join(f"\n  - {v['number']} {v['name']}{' (home)' if v['place'] == 'home' else ''}" for v in vis[:12]))
    except Exception:       # noqa: BLE001 — books without the healthcare tables
        pass
    return "\n".join(out)


def mine(db, user):
    cur = today_of(db, user)
    return {"today": cur, "location": db.setting("workday_location") or "ask", "draft": activity(db, user["username"])}


def team(db, date_from=None, date_to=None, username=None):
    w, p = ["date BETWEEN ? AND ?"], [date_from or acc.today(), date_to or acc.today()]
    if username:
        w.append("username=?")
        p.append(username)
    rows = db.q(f"SELECT * FROM workdays WHERE {' AND '.join(w)} ORDER BY date DESC, start_at", p)
    for r in rows:
        r["hours"] = None
        if r["end_at"]:
            a, b = acc._dt.datetime.fromisoformat(r["start_at"][:19]), acc._dt.datetime.fromisoformat(r["end_at"][:19])
            r["hours"] = round((b - a).total_seconds() / 3600, 2)
    started = {r["username"] for r in rows if r["date"] == acc.today()}
    # people who usually start their day here (any day in the last two weeks) but have not today
    usual = {r["username"] for r in db.q("SELECT DISTINCT username FROM workdays WHERE date>=?",
                                        ((acc._dt.date.fromisoformat(acc.today()) - acc._dt.timedelta(days=14)).isoformat(),))}
    absent = sorted(usual - started) if (date_to or acc.today()) >= acc.today() else []
    return {"rows": rows, "not_started": absent}


TABLES = ("workdays",)
