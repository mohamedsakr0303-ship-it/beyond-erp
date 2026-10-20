"""Employee requests: leave, sick leave, permissions (late arrival, early departure, off-site work), overtime and
salary advances. Employees submit them from Attendance; managers with approval rights approve or reject them.

On approval:
  * annual / other leave → the days are recorded as leave in Time & Attendance
  * sick leave           → the days are recorded as sick
  * unpaid leave         → the days are recorded as absent (deducted by the payroll run for monthly staff)
  * overtime             → the hours are added to that day's overtime
  * late / early / off-site → recorded; the day is no longer reported as late or early
  * salary advance       → approved for payment; paying it posts Dr Employee advances / Cr the paying account.
                           It is recovered through the payroll run (Advance recovery).
Rejecting or cancelling an approved request removes the attendance it created."""
import datetime as _dt

from . import accounting as acc
from .accounting import ValidationError, now_iso, num, text
from .db import to_major, to_minor

KINDS = {   # kind: (English, Arabic, needs a date range, needs times, needs hours, needs amount)
    "annual":   ("Annual leave", "إجازة سنوية", 1, 0, 0, 0),
    "sick":     ("Sick leave", "إجازة مرضية", 1, 0, 0, 0),
    "unpaid":   ("Unpaid leave", "إجازة بدون أجر", 1, 0, 0, 0),
    "other":    ("Other leave", "إجازة أخرى", 1, 0, 0, 0),
    "late":     ("Late arrival", "إذن تأخير", 0, 1, 0, 0),
    "early":    ("Early departure", "إذن انصراف مبكر", 0, 1, 0, 0),
    "offsite":  ("Off-site work", "عمل خارج الموقع", 1, 0, 0, 0),
    "overtime": ("Overtime", "عمل إضافي", 0, 0, 1, 0),
    "advance":  ("Salary advance", "سلفة على الراتب", 0, 0, 0, 1),
}
LEAVE_STATUS = {"annual": "leave", "other": "leave", "sick": "sick", "unpaid": "absent"}
STATUSES = ("pending", "approved", "rejected", "cancelled")
POLICY = {"start": "09:00", "end": "17:00", "grace": 10, "weekend": [], "leave_days": 21, "sites": [], "anywhere": [],
          "user_sites": {}, "spoof": "flag", "netcheck": True, "online_alert": 30}


def policy(db):
    import json
    try:
        p = json.loads(db.setting("att_policy") or "{}")
    except ValueError:
        p = {}
    return {**POLICY, **(p if isinstance(p, dict) else {})}


def save_policy(db, d):
    import json
    p = policy(db)
    for k in ("start", "end"):
        if k in d:
            v = text(d[k])
            if v and not _hhmm_ok(v):
                raise ValidationError("Enter times as HH:MM.")
            p[k] = v
    if "grace" in d:
        p["grace"] = max(0, int(num(d["grace"] or 0, "grace period")))
    if "leave_days" in d:
        p["leave_days"] = max(0, num(d["leave_days"] or 0, "annual leave days"))
    if "spoof" in d:
        if d["spoof"] not in ("off", "flag", "block"):
            raise ValidationError("Choose how to handle suspicious locations.")
        p["spoof"] = d["spoof"]
    if "netcheck" in d:
        p["netcheck"] = bool(d["netcheck"])
    if "online_alert" in d:
        p["online_alert"] = max(0, int(num(d["online_alert"] or 0, "alert after (minutes)")))
    if "weekend" in d:
        p["weekend"] = sorted({int(x) for x in d["weekend"] or [] if str(x).isdigit() and 0 <= int(x) <= 6})
    if "anywhere" in d:
        p["anywhere"] = sorted({text(u) for u in d["anywhere"] or [] if text(u)})
    if "sites" in d:
        sites = []
        for i, s in enumerate(d["sites"] or [], start=1):
            if not isinstance(s, dict) or not text(s.get("name")):
                continue
            try:
                lat, lng, rad = float(s["lat"]), float(s["lng"]), float(s.get("radius") or 150)
            except (KeyError, TypeError, ValueError):
                raise ValidationError(f"Site {i}: enter the latitude and longitude.") from None
            if not (-90 <= lat <= 90 and -180 <= lng <= 180) or not 10 <= rad <= 100000:
                raise ValidationError(f"Site {i}: the location or radius is out of range.")
            sites.append({"name": text(s["name"], maxlen=80), "lat": round(lat, 6), "lng": round(lng, 6), "radius": round(rad)})
        p["sites"] = sites
    if "user_sites" in d:
        p["user_sites"] = {text(u): [text(n) for n in names if text(n)] for u, names in (d["user_sites"] or {}).items()
                           if text(u) and isinstance(names, list)}
    names = {x["name"] for x in p["sites"]}
    p["user_sites"] = {u: [n for n in v if n in names] for u, v in p["user_sites"].items() if u not in p["anywhere"]}
    p["user_sites"] = {u: v for u, v in p["user_sites"].items() if v}
    db.set_setting("att_policy", json.dumps(p, ensure_ascii=False))
    return p


LOCATION_KEYS = ("location", "sites", "anywhere", "user_sites", "spoof", "netcheck")


def sites_for(pol, username):
    """The sites this person may check in at: none required (anywhere), their own selection, or all company sites."""
    if username in pol["anywhere"]:
        return []
    mine = set(pol.get("user_sites", {}).get(username) or [])
    return [x for x in pol["sites"] if x["name"] in mine] if mine else pol["sites"]


def _hhmm_ok(v):
    try:
        _dt.time.fromisoformat(v)
        return len(v) == 5
    except ValueError:
        return False


def _days(a, b, weekend=()):
    d, b = _dt.date.fromisoformat(a), _dt.date.fromisoformat(b)
    out = []
    while d <= b:
        if d.weekday() not in weekend:
            out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


def _wd(weekend):
    """Settings store weekend days as 0 = Sunday … 6 = Saturday; Python's weekday() is 0 = Monday."""
    return {(w - 1) % 7 for w in weekend}


def _row(db, r, pol=None):
    k = KINDS.get(r["kind"], (r["kind"], r["kind"]))
    r["kind_en"], r["kind_ar"] = k[0], k[1]
    r["amount"] = to_major(r["amount"] or 0)
    if r["kind"] in LEAVE_STATUS or r["kind"] == "offsite":
        r["days"] = len(_days(r["date_from"], r["date_to"] or r["date_from"], _wd((pol or policy(db))["weekend"])))
    return r


def get(db, rid):
    r = db.one("SELECT r.*, e.code AS emp_code, e.name AS emp_name FROM hr_requests r LEFT JOIN employees e ON e.id=r.employee_id "
               "WHERE r.id=?", (int(rid),))
    if not r:
        raise ValidationError("Request not found.")
    return _row(db, r)


def listing(db, username=None, status=None, kind=None, date_from=None, date_to=None):
    w, p = ["1=1"], []
    for col, v in (("r.username", username), ("r.status", status), ("r.kind", kind)):
        if v:
            w.append(f"{col}=?")
            p.append(v)
    if date_from:
        w.append("COALESCE(NULLIF(r.date_to,''), r.date_from)>=?")
        p.append(date_from)
    if date_to:
        w.append("r.date_from<=?")
        p.append(date_to)
    pol = policy(db)
    rows = db.q(f"SELECT r.*, e.code AS emp_code, e.name AS emp_name FROM hr_requests r LEFT JOIN employees e ON e.id=r.employee_id "
                f"WHERE {' AND '.join(w)} ORDER BY r.status='pending' DESC, r.id DESC LIMIT 500", p)
    return [_row(db, r, pol) for r in rows]


def balance(db, username, year=None):
    """Annual leave for the year: entitlement, taken (approved), requested (pending) and remaining."""
    pol = policy(db)
    year = year or acc.today()[:4]
    wk = _wd(pol["weekend"])
    used = pending = 0
    for r in db.q("SELECT date_from, date_to, status FROM hr_requests WHERE username=? AND kind='annual' AND status IN ('approved','pending') "
                  "AND date_from<=? AND COALESCE(NULLIF(date_to,''), date_from)>=?", (username, f"{year}-12-31", f"{year}-01-01")):
        n = sum(1 for d in _days(r["date_from"], r["date_to"] or r["date_from"], wk) if d[:4] == year)
        if r["status"] == "approved":
            used += n
        else:
            pending += n
    ent = float(pol["leave_days"] or 0)
    return {"year": year, "entitlement": ent, "taken": used, "pending": pending, "remaining": round(ent - used, 2)}


def advance_balance(db, employee_name):
    """Unrecovered salary advances for an employee, from the books."""
    acct = _role(db)
    if not acct:
        return 0.0
    r = db.one("SELECT COALESCE(SUM(l.debit-l.credit),0) b FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
               "WHERE e.status='posted' AND l.account=? AND l.party=?", (acct, employee_name))
    return to_major(r["b"])


def submit(db, user, d):
    from .workdays import employee_of
    kind = text(d.get("kind"))
    if kind not in KINDS:
        raise ValidationError("Choose the request type.")
    _, _, rng, times, hours_req, amt = KINDS[kind]
    emp = employee_of(db, user["username"])
    date_from = acc.clean_date(d.get("date_from") or acc.today())
    date_to = acc.clean_date(d.get("date_to")) if rng and d.get("date_to") else (date_from if rng else "")
    if date_to and date_to < date_from:
        raise ValidationError("The end date is before the start date.")
    time_from, time_to = text(d.get("time_from")), text(d.get("time_to"))
    if times:
        if not time_from and not time_to:
            raise ValidationError("Enter the time.")
        for v in (time_from, time_to):
            if v and not _hhmm_ok(v):
                raise ValidationError("Enter times as HH:MM.")
        if time_from and time_to and time_to <= time_from:
            raise ValidationError("The end time must be after the start time.")
    hours = num(d.get("hours") or 0, "hours") if hours_req else 0
    if hours_req and not 0 < hours <= 24:
        raise ValidationError("Enter the hours (up to 24).")
    amount = to_minor(num(d.get("amount") or 0, "amount")) if amt else 0
    if amt and amount <= 0:
        raise ValidationError("Enter the amount.")
    if rng:
        clash = db.one("SELECT id FROM hr_requests WHERE username=? AND status IN ('pending','approved') AND kind IN ('annual','sick','unpaid','other') "
                       "AND date_from<=? AND COALESCE(NULLIF(date_to,''), date_from)>=?", (user["username"], date_to, date_from)) if kind in LEAVE_STATUS else None
        if clash:
            raise ValidationError(f"These dates overlap leave request #{clash['id']}.")
    rid = db.x("INSERT INTO hr_requests(username, employee_id, kind, date_from, date_to, time_from, time_to, hours, amount, reason, status, created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (user["username"], emp["id"] if emp else None, kind, date_from, date_to or "", time_from, time_to, hours, amount,
                text(d.get("reason"), maxlen=1000), "pending", now_iso())).lastrowid
    return rid


def cancel(db, user, rid):
    r = get(db, rid)
    if r["username"] != user["username"]:
        raise PermissionError("You can cancel only your own requests.")
    if r["status"] != "pending":
        raise ValidationError("Only a pending request can be cancelled.")
    db.x("UPDATE hr_requests SET status='cancelled', decided_at=? WHERE id=?", (now_iso(), r["id"]))
    return r


def _note(rid):
    return f"Request #{rid}"


def _apply(db, r):
    """Record an approved request in Time & Attendance."""
    if not r["employee_id"]:
        return
    emp = db.one("SELECT id, bu_id FROM employees WHERE id=?", (r["employee_id"],))
    if not emp:
        return
    if r["kind"] in LEAVE_STATUS:
        status = LEAVE_STATUS[r["kind"]]
        for day in _days(r["date_from"], r["date_to"] or r["date_from"], _wd(policy(db)["weekend"])):
            db.x("""INSERT INTO attendance(date, employee_id, bu_id, status, hours, notes) VALUES (?,?,?,?,0,?)
                    ON CONFLICT(date, employee_id) DO UPDATE SET status=excluded.status, hours=0, notes=excluded.notes""",
                 (day, emp["id"], None, status, _note(r["id"])))
    elif r["kind"] == "overtime":
        row = db.one("SELECT id, overtime FROM attendance WHERE date=? AND employee_id=?", (r["date_from"], emp["id"]))
        if row:
            db.x("UPDATE attendance SET overtime=overtime+? WHERE id=?", (r["hours"], row["id"]))
        else:
            from .labour import _hours_day
            db.x("INSERT INTO attendance(date, employee_id, bu_id, status, hours, overtime, notes) VALUES (?,?,?,?,?,?,?)",
                 (r["date_from"], emp["id"], emp["bu_id"], "present", _hours_day(db), r["hours"], _note(r["id"])))


def _unapply(db, r):
    if not r["employee_id"]:
        return
    if r["kind"] in LEAVE_STATUS:
        db.x("DELETE FROM attendance WHERE employee_id=? AND notes=?", (r["employee_id"], _note(r["id"])))
    elif r["kind"] == "overtime":
        row = db.one("SELECT id, overtime, notes FROM attendance WHERE date=? AND employee_id=?", (r["date_from"], r["employee_id"]))
        if row and row["notes"] == _note(r["id"]):
            db.x("DELETE FROM attendance WHERE id=?", (row["id"],))
        elif row:
            db.x("UPDATE attendance SET overtime=MAX(0, overtime-?) WHERE id=?", (r["hours"], row["id"]))


def _tell(db, username, title, body, rid):
    try:
        db.x("INSERT INTO alert_feed(username, title, body, href, level, at) VALUES (?,?,?,?,?,?)",
             (username, title, body, f"#/my-day?tab=requests&id={rid}", "warn", now_iso()))
    except Exception:       # noqa: BLE001 — books without the alert feed
        pass


def decide(db, user, rid, approve, note=""):
    r = get(db, rid)
    if r["username"] == user["username"] and not user.get("is_super"):
        raise PermissionError("You cannot approve or reject your own request.")
    new = "approved" if approve else "rejected"
    if r["status"] == new:
        return r
    if r["status"] == "cancelled":
        raise ValidationError("This request was cancelled.")
    if not approve and r["entry_id"]:
        raise ValidationError("This advance has been paid. Void the payment entry first.")
    with db.tx():
        if r["status"] == "approved":
            _unapply(db, r)
        db.x("UPDATE hr_requests SET status=?, decided_by=?, decided_at=?, decision_note=? WHERE id=?",
             (new, user["username"], now_iso(), text(note, maxlen=500), r["id"]))
        r["status"] = new
        if approve:
            _apply(db, r)
    word = "approved" if approve else "rejected"
    _tell(db, r["username"], f"Request {word}", f"{r['kind_en']} #{r['id']}" + (f": {note}" if note else ""), r["id"])
    return get(db, rid)


def pay_advance(db, user, rid, d):
    r = get(db, rid)
    if r["kind"] != "advance" or r["status"] != "approved":
        raise ValidationError("Only an approved salary advance can be paid.")
    if r["entry_id"] and db.one("SELECT 1 FROM journal_entries WHERE id=? AND status='posted'", (r["entry_id"],)):
        raise ValidationError("This advance has already been paid.")
    if not r["emp_name"]:
        raise ValidationError("Link the user to an employee (Employees → App user) before paying the advance.")
    date = acc.clean_date(d.get("date") or acc.today())
    adv = _role(db)
    if not adv:
        raise ValidationError("No account has the 'employee_advances' role. Assign it in the chart of accounts.")
    src = acc.money_account(db, d.get("method"), d.get("paid_from"))
    with db.tx():
        eid = acc._insert_entry(db, date=date, kind="Salary Advance", memo=f"Salary advance — request #{r['id']}", party=r["emp_name"],
                                ref=f"REQ-{r['id']}", user=user["username"],
                                lines=[{"account": adv, "debit": r["amount"], "party": r["emp_name"]},
                                       {"account": src, "credit": r["amount"]}])
        db.x("UPDATE hr_requests SET entry_id=? WHERE id=?", (eid, r["id"]))
    return eid


def _role(db):
    r = db.one("SELECT code FROM accounts WHERE role='employee_advances'")
    return r["code"] if r else None


def pending_count(db):
    return db.one("SELECT COUNT(*) n FROM hr_requests WHERE status='pending'")["n"]


def excused(db, username, date):
    """Approved late / early / off-site permissions covering a day: {"late", "early", "offsite"}."""
    out = set()
    for r in db.q("SELECT kind FROM hr_requests WHERE username=? AND status='approved' AND kind IN ('late','early','offsite') "
                  "AND date_from<=? AND COALESCE(NULLIF(date_to,''), date_from)>=?", (username, date, date)):
        out.add(r["kind"])
    return out


TABLES = ("hr_requests",)
