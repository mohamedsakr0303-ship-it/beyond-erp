"""Entries that wait for approval (maker–checker).

Some people's entries are not posted straight away: a user marked "entries need approval" (Users screen) creates and
edits vouchers as drafts. Each draft waits here until someone who may approve it posts it — possibly after editing it —
or sends it back with a reason. Approvers are people with the "Approve & post entries" permission
(accounting.approvals, edit) who may also post that kind of entry; when the maker belongs to a group, approvers of that
same group see it (administrators see everything).

A draft is checked when it is sent (the same posting runs and is rolled back), so mistakes show to the maker, not later.
Posting it runs exactly what the maker's form would have run, under the maker's name, with the approver recorded."""
import json

from .accounting import ValidationError, now_iso, text, to_major, to_minor

STATUSES = ("pending", "approved", "rejected", "withdrawn")


class _DryRun(Exception):
    pass


def needs_approval(db, user):
    """Does this person's work wait for approval? (Administrators never do.)"""
    if not user or user.get("is_admin") or user.get("is_super"):
        return False
    r = db.one("SELECT needs_approval FROM users WHERE id=?", (user["id"],))
    return bool(r and r["needs_approval"])


def check(db, run):
    """Run the posting inside a transaction that is always rolled back — only to surface errors now."""
    try:
        with db.tx():
            run()
            raise _DryRun()
    except _DryRun:
        pass


def _summary(body):
    for k in ("memo", "description", "notes", "title"):
        if str(body.get(k) or "").strip():
            return str(body[k]).strip()[:200]
    return ""


def _amount(dtype, body):
    try:
        if dtype == "journal":
            return sum(float(ln.get("debit") or 0) for ln in body.get("lines") or [])
        if body.get("amount") not in (None, ""):
            return float(body["amount"])
        if body.get("qty") not in (None, "") and body.get("rate") not in (None, ""):
            return float(body["qty"]) * float(body["rate"])
    except (TypeError, ValueError):
        pass
    return 0.0


def submit(db, user, dtype, body, target_id=None, module=""):
    g = db.one("SELECT group_key FROM users WHERE id=?", (user["id"],))
    group = (g["group_key"] if g else "") or ""
    party = text(body.get("party") or body.get("vendor") or body.get("supplier") or body.get("customer") or "", maxlen=200)
    return db.x("INSERT INTO approvals(dtype,target_id,module,payload,summary,party,amount,date,created_by,group_key,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?)",
                (dtype, target_id, module, json.dumps(body, ensure_ascii=False), _summary(body), party,
                 to_minor(_amount(dtype, body)), text(body.get("date") or "", maxlen=20), user["username"], group, now_iso())).lastrowid


def get(db, aid):
    r = db.one("SELECT * FROM approvals WHERE id=?", (int(aid),))
    if not r:
        raise ValidationError("Not found")
    r = dict(r)
    r["payload"] = json.loads(r["payload"] or "{}")
    r["amount"] = to_major(r["amount"] or 0)
    return r


def can_approve(user, can, row):
    """May this person post this draft? Their own drafts never; otherwise the approval permission, the right to post
    that kind of entry, and the same group as the maker (administrators: any group)."""
    if row["created_by"] == user.get("username"):
        return False
    if user.get("is_admin") or user.get("is_super"):
        return True
    if not (can("accounting.approvals", "edit") and can(row["module"] or "accounting.journal", "add")):
        return False
    return not row["group_key"] or row["group_key"] == (user.get("group_key") or "")


def may_see(user, can, row):
    """Looking without approving: administrators everything; the approval permission, within the person's own group."""
    if user.get("is_admin") or user.get("is_super"):
        return True
    return can("accounting.approvals", "view") and (not row["group_key"] or row["group_key"] == (user.get("group_key") or ""))


def listing(db, user, can, q):
    w, p = ["1=1"], []
    st = q.get("status") or "pending"
    if st != "all":
        w.append("status=?")
        p.append(st)
    if q.get("created_by"):
        w.append("created_by=?")
        p.append(q["created_by"])
    if q.get("dtype"):
        w.append("dtype=?")
        p.append(q["dtype"])
    if q.get("date_from"):
        w.append("date>=?")
        p.append(q["date_from"])
    if q.get("date_to"):
        w.append("date<=?")
        p.append(q["date_to"])
    for word in (q.get("q") or "").split():
        w.append("(summary LIKE ? OR party LIKE ? OR created_by LIKE ? OR note LIKE ?)")
        p += [f"%{word}%"] * 4
    rows = []
    for r in db.q(f"SELECT * FROM approvals WHERE {' AND '.join(w)} ORDER BY id DESC LIMIT 500", p):
        r = dict(r)
        mine = r["created_by"] == user.get("username")
        r["can_approve"] = r["status"] == "pending" and can_approve(user, can, r)
        if not (mine or r["can_approve"] or may_see(user, can, r)):
            continue
        r["mine"] = mine
        r["amount"] = to_major(r["amount"] or 0)
        r.pop("payload", None)
        rows.append(r)
    return rows


def waiting_for(db, user, can):
    """Drafts this person can approve now (for the bell and notifications)."""
    rows = [dict(r) for r in db.q("SELECT * FROM approvals WHERE status='pending' ORDER BY id")]
    return [r for r in rows if can_approve(user, can, r)]


def decide(db, aid, user, status, note="", entry_id=None):
    db.x("UPDATE approvals SET status=?, decided_by=?, decided_at=?, note=?, entry_id=? WHERE id=?",
         (status, user["username"], now_iso(), text(note or "", maxlen=500), entry_id, int(aid)))


def update_payload(db, aid, body):
    db.x("UPDATE approvals SET payload=?, summary=?, party=?, date=?, amount=? WHERE id=?",
         (json.dumps(body, ensure_ascii=False), _summary(body),
          text(body.get("party") or body.get("vendor") or body.get("supplier") or body.get("customer") or "", maxlen=200),
          text(body.get("date") or "", maxlen=20), to_minor(_amount(get(db, aid)["dtype"], body)), int(aid)))
