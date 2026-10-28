"""The public desk, inside the books: what people outside the company sent through its public pages (online booking,
web forms, signing an offer, chatting as a guest — supabase/migrations/*_public_desk.sql) becomes ordinary records.

- A booking becomes an appointment: a healthcare visit (patient found by phone or added) when the company uses
  Healthcare and the visitor picked one of its services, otherwise a follow-up on the customer's timeline. Moving or
  cancelling it on the visitor's page moves or cancels that record.
- A web form becomes a lead: the contact (added when new), the form's answers on their timeline, and a call-back.
- A signature marks the offer Accepted and keeps the signed image with the offer.
- A guest's message opens (or continues) a conversation the team answers from the Online desk screen.

Each cloud row is imported once (its id is kept): the cloud hands each row to one device only, and a row that fails
here stays listed with its error so it can be retried."""
import json

from . import accounting as acc
from .accounting import ValidationError, clean_date, now_iso, text

PAGE_KINDS = ("booking", "form", "sign", "guest")


def _row(db, rid):
    return db.one("SELECT * FROM desk_rows WHERE id=?", (int(rid),))


def _first(db, page_id, ref):
    """The booking a later row (moved, cancelled) belongs to."""
    return db.one("SELECT * FROM desk_rows WHERE page_id=? AND ref=? AND kind='booking' AND state='done' ORDER BY id LIMIT 1",
                  (page_id, ref))


def _care_on(db):
    hidden = json.loads(db.setting("hidden_modules") or "[]")
    return "care" not in hidden and bool(db.one("SELECT 1 FROM care_services LIMIT 1"))


def _split_slot(slot):
    s = text(slot, maxlen=16)
    if len(s) != 16 or s[10] != " ":
        raise ValidationError("The booking has no valid time")
    return clean_date(s[:10]), s[11:]


def _booking(db, r, p, user):
    from . import care, crm
    date, time = _split_slot(r["slot"] or p.get("slot"))
    name = text(p.get("name"), "name", required=True, maxlen=200)
    phone = text(p.get("phone"), maxlen=40)
    notes = "\n".join(x for x in (f"Booked online ({r['ref']})", text(p.get("notes"), maxlen=1000)) if x)
    sid = p.get("service_id")
    if _care_on(db) and str(sid or "").isdigit() and db.one("SELECT 1 FROM care_services WHERE id=?", (int(sid),)):
        pat = db.one("SELECT id FROM care_patients WHERE phone<>'' AND phone=?", (phone,)) if phone else None
        pid = pat["id"] if pat else care.save_patient(db, {"name": name, "phone": phone, "address": p.get("address"), "zone": p.get("zone"),
                                                           "notes": "Added from an online booking"})
        vid = care.save_visit(db, {"date": date, "time": time, "patient_id": pid, "place": "home" if p.get("place") == "home" else "clinic",
                                   "address": p.get("address"), "zone": p.get("zone"), "notes": notes, "provider": text(p.get("provider"), maxlen=120),
                                   "lines": [{"service_id": int(sid), "qty": 1}]}, user)
        return "care_visit", vid
    party = crm._party(db, name)
    if phone and not (db.one("SELECT phone FROM parties WHERE name=?", (party,)) or {}).get("phone"):
        db.x("UPDATE parties SET phone=? WHERE name=?", (phone, party))
    what = text(p.get("service"), maxlen=200) or text(r["page_title"], maxlen=200) or "Appointment"
    fid = crm.save_followup(db, {"title": f"Booked online: {what} at {time}", "due": date, "party": party,
                                 "assigned_to": text(p.get("assign"), maxlen=60),
                                 "notes": "\n".join(x for x in (notes, f"Phone: {phone}" if phone else "", text(p.get("email"), maxlen=200)) if x)}, user)
    return "crm_followup", fid


def _moved(db, r, p, user, cancel):
    from . import care, crm
    b = _first(db, r["page_id"], r["ref"])
    if not b or not b["target_id"]:
        return "", None          # the booking itself never made it into the books (or was removed): nothing to move
    if not cancel:
        _reconfirm(db, r)
    if b["target"] == "care_visit":
        v = db.one("SELECT * FROM care_visits WHERE id=?", (b["target_id"],))
        if v and not v["entry_id"] and v["status"] in ("booked", "arrived"):
            if cancel:
                care.set_status(db, v["id"], "cancelled", {"notes": (v["notes"] or "") + "\nCancelled online by the patient"})
            else:
                date, time = _split_slot(p.get("to"))
                db.x("UPDATE care_visits SET date=?, time=?, notes=? WHERE id=?",
                     (date, time, (v["notes"] or "") + f"\nMoved online from {p.get('from')}", v["id"]))
        return b["target"], b["target_id"]
    f = db.one("SELECT * FROM crm_followups WHERE id=?", (b["target_id"],))
    if f and not f["done"]:
        if cancel:
            db.x("UPDATE crm_followups SET title=?, notes=? WHERE id=?", ("Cancelled: " + f["title"], (f["notes"] or "") + "\nCancelled online", f["id"]))
            crm.done_followup(db, f["id"], user, True, "Cancelled online by the customer")
        else:
            date, time = _split_slot(p.get("to"))
            title = f["title"].rsplit(" at ", 1)[0] + f" at {time}"
            db.x("UPDATE crm_followups SET due=?, title=?, notes=? WHERE id=?", (date, title, (f["notes"] or "") + f"\nMoved online from {p.get('from')}", f["id"]))
    return b["target"], b["target_id"]


def _reconfirm(db, r):
    """A booking the customer moved needs confirming again."""
    b = _first(db, r["page_id"], r["ref"])
    if b and b["confirmed"] == "confirmed":
        db.x("UPDATE desk_rows SET confirmed='' WHERE id=?", (b["id"],))


def _lead(db, r, p, user):
    from . import crm
    fields = p.get("fields") if isinstance(p.get("fields"), dict) else {k: v for k, v in p.items() if k not in ("name",)}
    name = text(p.get("name") or fields.get("name") or fields.get("Name"), "name", required=True, maxlen=200)
    party = crm._party(db, name)
    phone, email = text(p.get("phone") or fields.get("phone"), maxlen=40), text(p.get("email") or fields.get("email"), maxlen=200)
    cur = db.one("SELECT phone, email FROM parties WHERE name=?", (party,)) or {}
    if phone and not cur.get("phone"):
        db.x("UPDATE parties SET phone=? WHERE name=?", (phone, party))
    if email and not cur.get("email"):
        db.x("UPDATE parties SET email=? WHERE name=?", (email, party))
    body = "\n".join(f"{k}: {text(v, maxlen=2000)}" for k, v in fields.items() if str(v or "").strip())
    crm.add_activity(db, {"party": party, "kind": "note", "subject": f"Web form: {r['page_title'] or 'enquiry'}", "body": body}, user, system=True)
    fid = crm.save_followup(db, {"title": f"New web lead — get back to {name}", "due": acc.today(), "party": party,
                                 "assigned_to": text(p.get("assign"), maxlen=60), "notes": body}, user)
    return "crm_followup", fid


def _signature(db, r, p, user):
    from . import attachments, crm, documents
    oid = int(p.get("offer_id") or 0)
    o = documents.get_offer(db, oid)
    who = text(p.get("name"), "name", required=True, maxlen=200)
    img = p.get("image") or ""
    if not str(img).startswith("data:image/png;base64,"):
        raise ValidationError("The signature picture is missing")
    attachments.add(db, "offer", oid, {"data": img, "mime": "image/png", "title": f"Signed by {who} {str(r['at'] or '')[:10]}.png"}, user)
    db.x("UPDATE offers SET status='Accepted' WHERE id=?", (oid,))
    if o.get("client"):
        crm.add_activity(db, {"party": o["client"], "kind": "note", "subject": f"Offer {o['number']} signed online by {who}",
                              "body": text(p.get("comment"), maxlen=2000)}, user, system=True)
    return "offer", oid


def _message(db, r, p, user):
    t = db.one("SELECT * FROM desk_threads WHERE ref=? AND page_id=?", (r["ref"], r["page_id"]))
    body = text(p.get("body"), maxlen=4000)
    if t:
        db.x("UPDATE desk_threads SET last_at=?, last_body=?, unread=unread+1, name=CASE WHEN ?<>'' THEN ? ELSE name END WHERE id=?",
             (r["at"] or now_iso(), body, text(p.get("name"), maxlen=200), text(p.get("name"), maxlen=200), t["id"]))
        return "thread", t["id"]
    tid = db.x("INSERT INTO desk_threads(ref,page_id,page_title,name,phone,last_at,last_body,unread) VALUES (?,?,?,?,?,?,?,1)",
               (r["ref"], r["page_id"], text(r["page_title"], maxlen=200), text(p.get("name"), maxlen=200) or "Guest",
                text(p.get("phone"), maxlen=40), r["at"] or now_iso(), body)).lastrowid
    return "thread", tid


HANDLERS = {"booking": _booking, "lead": _lead, "signature": _signature, "message": _message,
            "reschedule": lambda db, r, p, u: _moved(db, r, p, u, False), "cancel": lambda db, r, p, u: _moved(db, r, p, u, True)}


def import_rows(db, rows, user):
    """Rows claimed from the cloud (desk_claim) into the books. Returns what happened to each."""
    out = []
    for c in rows if isinstance(rows, list) else []:
        if not isinstance(c, dict) or not str(c.get("id") or "").isdigit():
            continue
        rid = int(c["id"])
        if _row(db, rid):
            out.append({"id": rid, "state": "known"})
            continue
        payload = c.get("payload") if isinstance(c.get("payload"), dict) else {}
        db.x("INSERT INTO desk_rows(id,page_id,page_kind,page_title,kind,ref,slot,payload,at,state,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
             (rid, text(c.get("page_id"), maxlen=60), text(c.get("page_kind"), maxlen=20), text(c.get("page_title"), maxlen=200),
              text(c.get("kind"), maxlen=20), text(c.get("ref"), maxlen=40), text(c.get("slot"), maxlen=16),
              json.dumps(payload, ensure_ascii=False), text(c.get("at"), maxlen=40), "new", now_iso()))
        out.append(process(db, rid, user))
    return out


def process(db, rid, user):
    r = _row(db, rid)
    if not r:
        raise ValidationError("Not found")
    h = HANDLERS.get(r["kind"])
    try:
        if not h:
            raise ValidationError(f"Unknown kind {r['kind']}")
        with db.tx():
            target, tid = h(db, r, json.loads(r["payload"] or "{}"), user)
            db.x("UPDATE desk_rows SET state='done', error='', target=?, target_id=? WHERE id=?", (target, tid, rid))
        return {"id": rid, "state": "done", "target": target, "target_id": tid}
    except (ValidationError, LookupError, ValueError, PermissionError) as e:
        db.x("UPDATE desk_rows SET state='error', error=? WHERE id=?", (str(e)[:500], rid))
        return {"id": rid, "state": "error", "error": str(e)}


def rows(db, kind=None, state=None, limit=300):
    w, p = ["1=1"], []
    if kind:
        w.append("kind=?")
        p.append(kind)
    if state:
        w.append("state=?")
        p.append(state)
    out = []
    for r in db.q(f"SELECT * FROM desk_rows WHERE {' AND '.join(w)} ORDER BY id DESC LIMIT ?", p + [int(limit)]):
        r = dict(r)
        r["payload"] = json.loads(r["payload"] or "{}")
        r["payload"].pop("image", None)          # signatures are kept with the offer
        out.append(r)
    return out


def bookings(db, date_from=None):
    """Online bookings with where they are in the books now (for reminders and confirmations)."""
    out = []
    for r in db.q("SELECT * FROM desk_rows WHERE kind='booking' ORDER BY slot DESC, id DESC LIMIT 500"):
        r = dict(r)
        p = json.loads(r["payload"] or "{}")
        last = db.one("SELECT kind, payload FROM desk_rows WHERE page_id=? AND ref=? AND kind IN ('reschedule','cancel') ORDER BY id DESC LIMIT 1",
                      (r["page_id"], r["ref"]))
        slot = r["slot"]
        if last:
            slot = None if last["kind"] == "cancel" else json.loads(last["payload"] or "{}").get("to") or slot
        if date_from and (not slot or slot[:10] < date_from):
            continue
        out.append({"id": r["id"], "ref": r["ref"], "page_id": r["page_id"], "page_title": r["page_title"], "slot": slot,
                    "cancelled": bool(last and last["kind"] == "cancel"), "name": p.get("name", ""), "phone": p.get("phone", ""),
                    "service": p.get("service", ""), "price": p.get("price"), "place": p.get("place", ""), "notes": p.get("notes", ""),
                    "state": r["state"], "error": r["error"], "target": r["target"], "target_id": r["target_id"],
                    "confirmed": r["confirmed"]})
    return out


def threads(db):
    return db.q("SELECT * FROM desk_threads ORDER BY last_at DESC LIMIT 300")


def thread_read(db, tid):
    db.x("UPDATE desk_threads SET unread=0 WHERE id=?", (int(tid),))


def unread(db):
    r = db.one("SELECT COALESCE(SUM(unread),0) n FROM desk_threads")
    e = db.one("SELECT COUNT(*) n FROM desk_rows WHERE state='error'")
    return {"messages": r["n"], "errors": e["n"]}


def mark_confirmed(db, rid, how, user=""):
    """The team confirmed or declined an online booking (the visitor sees it on their page); declining also cancels the
    appointment it became."""
    r = _row(db, rid)
    if not r:
        raise ValidationError("Not found")
    with db.tx():
        db.x("UPDATE desk_rows SET confirmed=? WHERE id=?", (how if how in ("confirmed", "declined") else "", int(rid)))
        if how == "declined" and r["state"] == "done":
            _moved(db, r, {}, user, True)


# ------------------------------------------------------------------ what a page shows (built from the books)
def booking_services(db):
    """Healthcare services for a booking page (when the company uses Healthcare)."""
    if not _care_on(db):
        return []
    return [{"id": s["id"], "name": s["name"], "name_ar": s["name_ar"], "minutes": s["duration_min"], "price": s["price"] / 100,
             "home": bool(s["home"])} for s in db.q("SELECT * FROM care_services WHERE active=1 ORDER BY category, name")]


def busy_slots(db, days=60):
    """Times already taken in the books (visits booked from inside the company), so the page does not offer them."""
    if not _care_on(db):
        return []
    end = (acc._dt.date.fromisoformat(acc.today()) + acc._dt.timedelta(days=days)).isoformat()
    return [f"{r['date']} {r['time']}" for r in db.q("SELECT date, time FROM care_visits WHERE status IN ('booked','arrived') AND time<>'' "
                                                     "AND date BETWEEN ? AND ?", (acc.today(), end))]


def offer_summary(db, oid):
    """An offer as the client sees it on the signing page (no internal notes)."""
    from . import documents
    o = documents.get_offer(db, int(oid))
    secs = []
    for s in o.get("sections") or []:
        if s.get("kind") in ("text", "signature") and s.get("body"):
            secs.append({"heading": s.get("heading", ""), "body": s.get("body", "")})
        elif s.get("kind") == "pricing":
            t = documents.offer_totals(s)
            secs.append({"heading": s.get("heading", ""), "items": [{"desc": i.get("desc", ""), "amount": i.get("amount", 0)} for i in s.get("items") or []],
                         "discount": t["discount"], "vat_pct": s.get("vat") or 0, "vat": t["vat"], "net": t["net"], "total": t["total"]})
    return {"offer_id": o["id"], "number": o["number"], "date": o.get("date"), "client": o.get("client", ""), "project": o.get("project", ""),
            "title": o.get("type_name") or "Offer", "lang": o.get("lang", "en"), "sections": secs, "net": o.get("net"),
            "company": db.setting("company_name") or ""}


TABLES = ("desk_rows", "desk_threads")


def export_rows(db):
    return {t: db.q(f"SELECT * FROM {t} ORDER BY id") for t in TABLES}


def import_backup(db, data, mode="append"):
    if not isinstance(data, dict) or not any(data.get(t) for t in TABLES):
        return 0
    if mode != "replace" and db.one("SELECT 1 FROM desk_rows"):
        return 0
    n = 0
    for t in TABLES:
        db.conn.execute(f"DELETE FROM {t}")
        cols = [r["name"] for r in db.q(f"PRAGMA table_info({t})")]
        for row in data.get(t) or []:
            if isinstance(row, dict):
                keys = [k for k in cols if k in row]
                db.conn.execute(f"INSERT INTO {t}({','.join(keys)}) VALUES ({','.join('?' * len(keys))})", [row[k] for k in keys])
                n += 1
    return n
