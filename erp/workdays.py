"""Check-in and check-out: staff record each work session from the phone or browser, as many sessions a day as
needed (the day's hours are their total). The place is recorded when the company asks for it; when the company lists
work sites, check-ins must be within a site's radius unless the person is allowed to work from anywhere. Checking out
can carry a short work summary that the app drafts from the person's activity.

Managers see the team's sessions: who checked in, where and when, hours, late arrivals and early departures."""
import math
from . import accounting as acc
from .accounting import ValidationError, now_iso, text

LOCATION = ("off", "ask", "required")


def _need(db, username):
    """Location rule for this person: off / ask / required, and the sites they must be at (empty = anywhere)."""
    from .hrrequests import policy
    pol = policy(db)
    need = db.setting("workday_location") or "ask"
    sites = [] if username in pol["anywhere"] else pol["sites"]
    return ("required" if sites else need), sites


def _metres(a_lat, a_lng, b_lat, b_lng):
    r = 6371000
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _site(sites, lat, lng, accu):
    """The work site this position is at, or a ValidationError naming the nearest one."""
    if not sites:
        return ""
    best = None
    for s in sites:
        dist = _metres(lat, lng, s["lat"], s["lng"])
        if dist <= s["radius"] + min(accu or 0, 100):
            return s["name"]
        if best is None or dist < best[0]:
            best = (dist, s["name"])
    raise ValidationError(f"You are {best[0] / 1000:.1f} km from {best[1]}. Check in from a work site.")


def _loc(d, need):
    lat, lng = d.get("lat"), d.get("lng")
    if lat in (None, "") or lng in (None, ""):
        if need == "required":
            raise ValidationError("This company requires your location to check in or out. Allow location access for this application and try again.")
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


MYDAY = "My day"          # the note on attendance rows filled from a person's day (a manager's own entry is never changed)


def employee_of(db, username):
    """The employee linked to this app user (Employees → App user), if any."""
    try:
        return db.one("SELECT id, code, name, bu_id FROM employees WHERE username=? AND status='active'", (username,))
    except Exception:       # noqa: BLE001 — books without payroll
        return None


def _hours(r):
    if not r["end_at"]:
        return 0
    a, b = acc._dt.datetime.fromisoformat(r["start_at"][:19]), acc._dt.datetime.fromisoformat(r["end_at"][:19])
    return round((b - a).total_seconds() / 3600, 2)


def sync_attendance(db, username, date):
    """Time & attendance follows the person's days: present, with the hours of the day's start / end."""
    emp = employee_of(db, username)
    if not emp:
        return
    days = db.q("SELECT * FROM workdays WHERE username=? AND date=?", (username, date))
    row = db.one("SELECT id, notes FROM attendance WHERE date=? AND employee_id=?", (date, emp["id"]))
    if row and not (row["notes"] or "").startswith(MYDAY):
        return
    if not days:
        if row:
            db.x("DELETE FROM attendance WHERE id=?", (row["id"],))
        return
    hours = round(sum(_hours(r) for r in days), 2)
    if row:
        db.x("UPDATE attendance SET hours=?, status='present' WHERE id=?", (hours, row["id"]))
    else:
        db.x("INSERT INTO attendance(date,employee_id,bu_id,status,hours,notes) VALUES (?,?,?,?,?,?)",
             (date, emp["id"], emp["bu_id"], "present", hours, MYDAY))


def delete(db, wid):
    r = db.one("SELECT * FROM workdays WHERE id=?", (wid,))
    if not r:
        raise ValidationError("Day not found")
    db.x("DELETE FROM workdays WHERE id=?", (r["id"],))
    sync_attendance(db, r["username"], r["date"])
    return r


def start(db, user, d):
    if (cur := today_of(db, user)) and not cur["end_at"]:
        raise ValidationError("You are already checked in.")
    need, sites = _need(db, user["username"])
    lat, lng, accu = _loc(d, need)
    site = _site(sites, lat, lng, accu) if lat is not None else ""
    wid = db.x("INSERT INTO workdays(username, date, start_at, start_lat, start_lng, start_acc, note, start_site) VALUES (?,?,?,?,?,?,?,?)",
               (user["username"], acc.today(), now_iso(), lat, lng, accu, text(d.get("note"), maxlen=500), site)).lastrowid
    sync_attendance(db, user["username"], acc.today())
    return wid


def finish(db, user, d):
    cur = today_of(db, user)
    if not cur or cur["end_at"]:
        raise ValidationError("Check in first.")
    need, sites = _need(db, user["username"])
    lat, lng, accu = _loc(d, need)
    site = _site(sites, lat, lng, accu) if lat is not None else ""
    db.x("UPDATE workdays SET end_at=?, end_lat=?, end_lng=?, end_acc=?, report=?, end_site=? WHERE id=?",
         (now_iso(), lat, lng, accu, text(d.get("report"), maxlen=4000), site, cur["id"]))
    sync_attendance(db, user["username"], cur["date"])
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


def _late_early(pol, first, last, excused):
    """Minutes late (first check-in after start + grace) and early (last check-out before end), net of permissions."""
    def mins(iso):
        return int(iso[11:13]) * 60 + int(iso[14:16])
    late = early = 0
    if pol.get("start") and first and "late" not in excused and "offsite" not in excused:
        s = int(pol["start"][:2]) * 60 + int(pol["start"][3:5])
        late = max(0, mins(first) - s) if mins(first) - s > int(pol.get("grace") or 0) else 0
    if pol.get("end") and last and "early" not in excused and "offsite" not in excused:
        e = int(pol["end"][:2]) * 60 + int(pol["end"][3:5])
        early = max(0, e - mins(last))
    return late, early


def _now_hours(r):
    """Hours of a session; an open session counts up to now."""
    if r["end_at"]:
        return _hours(r)
    a = acc._dt.datetime.fromisoformat(r["start_at"][:19])
    b = acc._dt.datetime.fromisoformat(now_iso()[:19])
    return round(max(0, (b - a).total_seconds()) / 3600, 2)


def mine(db, user):
    from . import hrrequests
    cur = today_of(db, user)
    sessions = db.q("SELECT * FROM workdays WHERE username=? AND date=? ORDER BY start_at", (user["username"], acc.today()))
    need, sites = _need(db, user["username"])
    emp = employee_of(db, user["username"])
    return {"today": cur, "sessions": sessions, "hours": round(sum(_now_hours(r) for r in sessions), 2),
            "open": bool(cur and not cur["end_at"]), "location": need, "sites": [x["name"] for x in sites],
            "draft": activity(db, user["username"]), "employee": emp, "policy": {k: hrrequests.policy(db)[k] for k in ("start", "end", "grace")},
            "balance": hrrequests.balance(db, user["username"]), "requests": hrrequests.listing(db, username=user["username"])[:50],
            "advance": hrrequests.advance_balance(db, emp["name"]) if emp else 0}


def team(db, date_from=None, date_to=None, username=None):
    w, p = ["date BETWEEN ? AND ?"], [date_from or acc.today(), date_to or acc.today()]
    if username:
        w.append("username=?")
        p.append(username)
    from .hrrequests import excused, policy
    pol = policy(db)
    rows = db.q(f"SELECT * FROM workdays WHERE {' AND '.join(w)} ORDER BY date DESC, start_at", p)
    linked, days = {}, {}
    for r in rows:
        r["hours"] = _hours(r) if r["end_at"] else None
        if r["username"] not in linked:
            linked[r["username"]] = employee_of(db, r["username"])
        e = linked[r["username"]]
        r["employee"] = f"{e['code']} — {e['name']}" if e else ""
        days.setdefault((r["username"], r["date"]), []).append(r)
    for (u, day), rs in days.items():
        first = min(x["start_at"] for x in rs)
        ends = [x["end_at"] for x in rs if x["end_at"]]
        last = max(ends) if ends and all(x["end_at"] for x in rs) else None
        late, early = _late_early(pol, first, last, excused(db, u, day))
        for x in rs:
            x["late"] = late if x["start_at"] == first else 0
            x["early"] = early if last and x["end_at"] == last else 0
    started = {r["username"] for r in rows if r["date"] == acc.today()}
    # people who usually start their day here (any day in the last two weeks) but have not today
    usual = {r["username"] for r in db.q("SELECT DISTINCT username FROM workdays WHERE date>=?",
                                        ((acc._dt.date.fromisoformat(acc.today()) - acc._dt.timedelta(days=14)).isoformat(),))}
    absent = sorted(usual - started) if (date_to or acc.today()) >= acc.today() else []
    return {"rows": rows, "not_started": absent}


TABLES = ("workdays",)
