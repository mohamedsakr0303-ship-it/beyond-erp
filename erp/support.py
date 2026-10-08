"""Help & support: problem reports, questions and suggestions sent to the BeyondERP team.

A ticket is kept in the books (so the company sees what it reported and when) and sent by the person through
WhatsApp or e-mail with its reference and the technical details that make it reproducible. Tickets are ordinary
records: on the shared workspace every device of the company sees the same list. A central inbox for the team
(tickets straight into the vendor's own console) can be added on the hosted service without changing this format.
"""
import json

from .accounting import ValidationError, now_iso, text

KINDS = ("bug", "question", "suggestion", "billing")
STATUSES = ("sent", "answered", "solved")
CHANNELS = ("whatsapp", "email", "copy")


def _row(r):
    d = dict(r)
    try:
        d["diag"] = json.loads(d.get("diag") or "{}")
    except ValueError:
        d["diag"] = {}
    return d


def tickets(db, user):
    """Everyone sees their own; administrators see the whole company's."""
    q = "SELECT t.*, u.username FROM support_tickets t LEFT JOIN users u ON u.id = t.created_by"
    rows = db.q(q + " ORDER BY t.id DESC LIMIT 200") if user.get("is_admin") else \
        db.q(q + " WHERE t.created_by=? ORDER BY t.id DESC LIMIT 200", (user["id"],))
    return [_row(r) for r in rows]


def create(db, user, d):
    kind = d.get("kind") if d.get("kind") in KINDS else "bug"
    subject = text(d.get("subject"), "subject", required=True, maxlen=200)
    body = text(d.get("body"), maxlen=8000)
    channel = d.get("channel") if d.get("channel") in CHANNELS else "copy"
    diag = d.get("diag") if isinstance(d.get("diag"), dict) else {}
    diag = json.dumps({str(k)[:40]: str(v)[:2000] for k, v in list(diag.items())[:40]}, ensure_ascii=False)
    at = now_iso()
    tid = db.x("INSERT INTO support_tickets(kind, subject, body, diag, channel, status, created_by, created_at, updated_at) "
               "VALUES (?,?,?,?,?,'sent',?,?,?)", (kind, subject, body, diag, channel, user["id"], at, at)).lastrowid
    ref = f"BE-{at[2:4]}{at[5:7]}{at[8:10]}-{tid:04d}"     # same on every device: the date and the record number
    db.x("UPDATE support_tickets SET ref=? WHERE id=?", (ref, tid))
    return _row(db.one("SELECT t.*, u.username FROM support_tickets t LEFT JOIN users u ON u.id=t.created_by WHERE t.id=?", (tid,)))


def set_status(db, user, tid, status):
    t = db.one("SELECT * FROM support_tickets WHERE id=?", (int(tid),))
    if not t:
        raise ValidationError("Ticket not found")
    if not (user.get("is_admin") or t["created_by"] == user["id"]):
        raise PermissionError()
    if status not in STATUSES:
        raise ValidationError("Unknown status")
    db.x("UPDATE support_tickets SET status=?, updated_at=? WHERE id=?", (status, now_iso(), t["id"]))
