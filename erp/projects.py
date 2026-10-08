"""Projects: execution phases with progress, the team's tasks, and a live status report per project.

A project is a business unit. Its phases carry a weight (any scale) and a progress (0..1); the project's progress is
the weighted average. Phases with dates also give a PLANNED progress for today (straight line between start and
finish), so a project shows whether it is ahead or behind. Tasks belong to a project (optionally a phase), have an
assignee, a due date, a priority and a status; overdue tasks raise alerts.

The status report puts it all together with the money: contract value (with approved variations), billed, collected,
costs, profit, work in progress, labour days and equipment hours on site, and the open tasks.
"""
import datetime as _dt

from . import accounting as acc
from .accounting import ValidationError, num, text

PHASE_STATUS = ("planned", "in_progress", "done", "on_hold")
TASK_STATUS = ("todo", "doing", "blocked", "done")
PRIORITIES = ("low", "normal", "high")


def _bu(db, v):
    bid = acc.bu_id_or_none(db, v, required=True)
    return db.one("SELECT * FROM business_units WHERE id=?", (bid,))


def _pct(v, field):
    x = num(v, field)
    if x > 1:
        x /= 100
    if x < 0 or x > 1:
        raise ValidationError(f"{field} must be between 0 and 100 %")
    return x


# ------------------------------------------------------------------ phases
def phases(db, bu_id):
    return db.q("SELECT * FROM project_phases WHERE bu_id=? ORDER BY seq, id", (int(bu_id),))


def save_phase(db, d, pid=None):
    bu = _bu(db, d.get("bu_id") or d.get("bu_code"))
    name = text(d.get("name"), "phase name", required=True, maxlen=200)
    weight = num(d.get("weight") if d.get("weight") not in (None, "") else 1, "weight")
    start = acc.clean_date(d.get("start"), "start") if d.get("start") else ""
    finish = acc.clean_date(d.get("finish"), "finish") if d.get("finish") else ""
    if start and finish and finish < start:
        raise ValidationError("A phase cannot finish before it starts")
    progress = _pct(d.get("progress") or 0, "progress")
    status = d.get("status") if d.get("status") in PHASE_STATUS else ("done" if progress >= 1 else
                                                                     "in_progress" if progress > 0 else "planned")
    if status == "done":
        progress = 1.0
    vals = (bu["id"], int(num(d.get("seq") or 0, "order")), name, weight, start, finish, progress, status,
            text(d.get("notes"), maxlen=2000))
    if pid:
        db.x("UPDATE project_phases SET bu_id=?, seq=?, name=?, weight=?, start=?, finish=?, progress=?, status=?, notes=? "
             "WHERE id=?", vals + (int(pid),))
        return int(pid)
    if not vals[1]:
        vals = (vals[0], (db.one("SELECT COALESCE(MAX(seq),0) n FROM project_phases WHERE bu_id=?", (bu["id"],))["n"] or 0) + 1) + vals[2:]
    return db.x("INSERT INTO project_phases(bu_id,seq,name,weight,start,finish,progress,status,notes) VALUES (?,?,?,?,?,?,?,?,?)",
                vals).lastrowid


def delete_phase(db, pid):
    db.x("DELETE FROM project_phases WHERE id=?", (int(pid),))


def _planned(ph, today):
    """Where the phase should be today if work went in a straight line from start to finish."""
    if not ph["start"] or not ph["finish"]:
        return None
    if today <= ph["start"]:
        return 0.0
    if today >= ph["finish"]:
        return 1.0
    a, b, t = (_dt.date.fromisoformat(x) for x in (ph["start"], ph["finish"], today))
    return round((t - a).days / max((b - a).days, 1), 4)


def progress(db, bu_id, today=None):
    """{actual, planned, phases}: weighted by each phase's weight (equal weights when none are given)."""
    today = today or acc.today()
    ph = phases(db, bu_id)
    if not ph:
        return {"actual": None, "planned": None, "phases": [], "variance": None}
    tw = sum(p["weight"] for p in ph) or len(ph)
    w = lambda p: (p["weight"] if sum(x["weight"] for x in ph) else 1) / tw  # noqa: E731
    actual = sum(w(p) * p["progress"] for p in ph)
    plan = [(_planned(p, today), w(p)) for p in ph]
    planned = sum(v * k for v, k in plan if v is not None) / (sum(k for v, k in plan if v is not None) or 1) \
        if any(v is not None for v, _ in plan) else None
    for p, (pl, _) in zip(ph, plan):
        p["planned"] = pl
        p["late"] = bool(p["finish"] and p["finish"] < today and p["progress"] < 1)
    return {"actual": round(actual, 4), "planned": None if planned is None else round(planned, 4), "phases": ph,
            "variance": None if planned is None else round(actual - planned, 4)}


# ------------------------------------------------------------------ tasks
def tasks(db, bu_id=None, assignee=None, status=None, q=None):
    w, p = ["1=1"], []
    if bu_id:
        w.append("t.bu_id=?")
        p.append(int(bu_id))
    if assignee:
        w.append("t.assignee=?")
        p.append(assignee)
    if status == "open":
        w.append("t.status<>'done'")
    elif status == "overdue":
        w.append("t.status<>'done' AND t.due<>'' AND t.due<?")
        p.append(acc.today())
    elif status:
        w.append("t.status=?")
        p.append(status)
    if q:
        w.append("(t.title LIKE ? OR t.notes LIKE ?)")
        p += [f"%{q}%"] * 2
    rows = db.q(f"""SELECT t.*, b.code AS bu_code, b.name AS bu_name, ph.name AS phase FROM project_tasks t
                    LEFT JOIN business_units b ON b.id=t.bu_id LEFT JOIN project_phases ph ON ph.id=t.phase_id
                    WHERE {' AND '.join(w)}
                    ORDER BY CASE t.status WHEN 'done' THEN 1 ELSE 0 END, CASE WHEN t.due='' THEN 1 ELSE 0 END, t.due,
                             CASE t.priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, t.id""", p)
    today = acc.today()
    for r in rows:
        r["overdue"] = bool(r["status"] != "done" and r["due"] and r["due"] < today)
    return rows


def save_task(db, d, tid=None, user=""):
    title = text(d.get("title"), "task", required=True, maxlen=300)
    bu = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"))
    phase = d.get("phase_id") or None
    if phase:
        ph = db.one("SELECT bu_id FROM project_phases WHERE id=?", (int(phase),))
        if not ph:
            raise ValidationError("That phase does not exist")
        bu = bu or ph["bu_id"]
    status = d.get("status") if d.get("status") in TASK_STATUS else "todo"
    prio = d.get("priority") if d.get("priority") in PRIORITIES else "normal"
    due = acc.clean_date(d.get("due"), "due date") if d.get("due") else ""
    vals = dict(bu_id=bu, phase_id=int(phase) if phase else None, title=title, assignee=text(d.get("assignee"), maxlen=120),
                due=due, priority=prio, status=status, notes=text(d.get("notes"), maxlen=4000))
    if tid:
        old = db.one("SELECT status, done_at FROM project_tasks WHERE id=?", (int(tid),))
        if not old:
            raise ValidationError("Task not found")
        done_at = (old["done_at"] or acc.now_iso()) if status == "done" else ""
        db.x(f"UPDATE project_tasks SET {', '.join(k + '=?' for k in vals)}, done_at=? WHERE id=?",
             list(vals.values()) + [done_at, int(tid)])
        return int(tid)
    return db.x(f"INSERT INTO project_tasks({', '.join(vals)}, created_by, created_at, done_at) VALUES "
                f"({', '.join('?' * len(vals))}, ?, ?, ?)",
                list(vals.values()) + [user, acc.now_iso(), acc.now_iso() if status == "done" else ""]).lastrowid


def delete_task(db, tid):
    db.x("DELETE FROM project_tasks WHERE id=?", (int(tid),))


# ------------------------------------------------------------------ the project at a glance
def status_report(db, bu_id, today=None):
    from . import contracts, wip
    today = today or acc.today()
    bu = _bu(db, bu_id)
    s = next((b for b in acc.bu_summaries(db) if b["id"] == bu["id"]), {})
    pr = progress(db, bu["id"], today)
    tk = tasks(db, bu["id"])
    cv = contracts.client_value(db, bu["id"])
    unb = 0
    try:
        unb = acc.to_major(db.one(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                                      WHERE {acc.LIVE} AND e.bu_id=? AND l.account=?""", (bu["id"], db.acct("unbilled_revenue")))["n"])
    except LookupError:
        pass
    labour = db.one("SELECT COUNT(*) n, COALESCE(SUM(hours),0) h, COALESCE(SUM(overtime),0) ot FROM attendance "
                    "WHERE bu_id=? AND status IN ('present','half')", (bu["id"],))
    eq = db.one("SELECT COALESCE(SUM(hours),0) h FROM equipment_usage WHERE bu_id=?", (bu["id"],))
    billed = s.get("revenue", 0) - unb
    value = cv["revised"] or acc.to_major(bu["contract_value"] or 0)
    earned = round(value * pr["actual"], 2) if value and pr["actual"] is not None else None
    return {"bu": {k: bu[k] for k in ("id", "code", "name", "status", "basis", "start_date")} | {"wip_mode": bu.get("wip_mode") or ""},
            "progress": pr, "contract": cv, "contract_value": value,
            "money": {"revenue": s.get("revenue", 0), "billed": round(billed, 2), "work_in_progress": unb,
                      "collected": s.get("collected", 0), "costs": round(s.get("cogs", 0) + s.get("direct_overhead", 0), 2),
                      "net_profit": s.get("net_profit", 0), "receivable": s.get("receivable", 0),
                      "earned_value": earned, "billed_pct": round(billed / value, 4) if value else None},
            "labour": {"days": labour["n"], "hours": labour["h"], "overtime": labour["ot"]},
            "equipment_hours": eq["h"],
            "tasks": {"open": sum(1 for t in tk if t["status"] != "done"), "overdue": sum(1 for t in tk if t["overdue"]),
                      "done": sum(1 for t in tk if t["status"] == "done"), "rows": [t for t in tk if t["status"] != "done"][:30]},
            "wip_managed": bool(wip.mode(bu))}


def overview(db, today=None):
    """Every project with its progress against plan, contract, billed % and open / overdue tasks."""
    from . import contracts
    today = today or acc.today()
    sums = {b["id"]: b for b in acc.bu_summaries(db)}
    open_t = {r["bu_id"]: r for r in db.q("SELECT bu_id, SUM(CASE WHEN status<>'done' THEN 1 ELSE 0 END) open, "
                                           "SUM(CASE WHEN status<>'done' AND due<>'' AND due<? THEN 1 ELSE 0 END) overdue "
                                           "FROM project_tasks GROUP BY bu_id", (today,))}
    rows = []
    for b in db.q("SELECT * FROM business_units ORDER BY code"):
        pr = progress(db, b["id"], today)
        cv = contracts.client_value(db, b["id"])
        value = cv["revised"] or acc.to_major(b["contract_value"] or 0)
        s = sums.get(b["id"], {})
        t = open_t.get(b["id"], {})
        rows.append({"id": b["id"], "code": b["code"], "name": b["name"], "status": b["status"], "progress": pr["actual"],
                     "planned": pr["planned"], "variance": pr["variance"], "phases": len(pr["phases"]),
                     "late_phases": sum(1 for p in pr["phases"] if p["late"]), "contract_value": value,
                     "revenue": s.get("revenue", 0), "net_profit": s.get("net_profit", 0),
                     "open_tasks": t.get("open") or 0, "overdue_tasks": t.get("overdue") or 0})
    return rows


def alerts(db, today=None):
    today = today or acc.today()
    overdue = db.q("SELECT COUNT(*) n FROM project_tasks WHERE status<>'done' AND due<>'' AND due<?", (today,))[0]["n"]
    late = db.q("SELECT COUNT(*) n FROM project_phases WHERE finish<>'' AND finish<? AND progress<1", (today,))[0]["n"]
    return {"overdue_tasks": overdue, "late_phases": late}


# ------------------------------------------------------------------ backup / restore
def export_rows(db):
    bu = {b["id"]: b["code"] for b in db.q("SELECT id, code FROM business_units")}
    ph = {p["id"]: p for p in db.q("SELECT * FROM project_phases")}
    return {"phases": [{"bu_code": bu.get(p["bu_id"], ""), **{k: p[k] for k in ("seq", "name", "weight", "start", "finish",
                                                                                  "progress", "status", "notes")}}
                       for p in ph.values()],
            "tasks": [{"bu_code": bu.get(t["bu_id"], ""), "phase": ph[t["phase_id"]]["name"] if t["phase_id"] in ph else "",
                       **{k: t[k] for k in ("title", "assignee", "due", "priority", "status", "notes", "created_by",
                                            "created_at", "done_at")}}
                      for t in db.q("SELECT * FROM project_tasks ORDER BY id")]}


def import_rows(db, data, counts):
    n = m = 0
    for p in data.get("phases") or []:
        if p.get("bu_code") and db.one("SELECT 1 FROM business_units WHERE code=?", (p["bu_code"],)):
            save_phase(db, p)
            n += 1
    for t in data.get("tasks") or []:
        bu = db.one("SELECT id FROM business_units WHERE code=?", (t.get("bu_code") or "",))
        ph = db.one("SELECT id FROM project_phases WHERE bu_id=? AND name=?", (bu["id"], t.get("phase"))) if bu and t.get("phase") else None
        tid = save_task(db, {**t, "bu_id": bu["id"] if bu else None, "phase_id": ph["id"] if ph else None}, user=t.get("created_by") or "")
        db.conn.execute("UPDATE project_tasks SET created_at=?, done_at=? WHERE id=?", (t.get("created_at") or "", t.get("done_at") or "", tid))
        m += 1
    if n:
        counts["Project phases"] = n
    if m:
        counts["Project tasks"] = m
