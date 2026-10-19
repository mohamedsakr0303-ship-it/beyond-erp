"""Customers & deals (CRM) inside the books.

- Contacts are the parties already in the books (customers, suppliers…). Each one gets a timeline: calls, meetings,
  emails, WhatsApp notes and visits logged here, next to the offers, invoices, receipts / payments, sales and deals
  that the rest of the ERP already holds about them.
- Deals move along a pipeline (lead → qualified → proposal → negotiation → won / lost) with an expected value and
  closing date; the board shows each stage's count and value, weighted by probability.
- Follow-ups are dated tasks for a person ("call Ahmed about the revised BOQ"); the ones due appear in that person's
  bell, like entries waiting for approval.
- An offer turns into a project (business unit), its BOQ and a first invoice in one step (see convert_offer)."""
import json

from .accounting import ValidationError, clean_date, now_iso, text, to_major, to_minor, today

STAGES = [("lead", "Lead", "عميل محتمل", 10), ("qualified", "Qualified", "مؤهل", 25), ("proposal", "Proposal sent", "تم إرسال العرض", 50),
          ("negotiation", "Negotiation", "تفاوض", 75), ("won", "Won", "تم الفوز", 100), ("lost", "Lost", "خسارة", 0)]
STAGE_KEYS = [s[0] for s in STAGES]
KINDS = ("call", "meeting", "email", "whatsapp", "visit", "note")


def _party(db, name, required=True):
    name = text(name, "contact", required=required, maxlen=200)
    if name and not db.one("SELECT 1 FROM parties WHERE name=?", (name,)):
        db.x("INSERT INTO parties(name,type) VALUES (?, 'customer')", (name,))     # a new contact, as a customer
    return name


# ------------------------------------------------------------------ deals
def save_deal(db, d, user, did=None):
    title = text(d.get("title"), "title", required=True, maxlen=200)
    party = _party(db, d.get("party"))
    stage = d.get("stage") if d.get("stage") in STAGE_KEYS else "lead"
    prob = d.get("probability")
    prob = int(float(prob)) if prob not in (None, "") else dict((s[0], s[3]) for s in STAGES)[stage]
    if not 0 <= prob <= 100:
        raise ValidationError("Probability is a percentage between 0 and 100")
    vals = (title, party, stage, to_minor(float(d.get("value") or 0)), prob, clean_date(d.get("expected_close")) if d.get("expected_close") else "",
            text(d.get("owner") or user, maxlen=60), text(d.get("source"), maxlen=100), text(d.get("notes"), maxlen=4000),
            int(d["offer_id"]) if str(d.get("offer_id") or "").isdigit() else None)
    if did:
        old = db.one("SELECT * FROM crm_deals WHERE id=?", (int(did),))
        if not old:
            raise ValidationError("Deal not found")
        db.x("UPDATE crm_deals SET title=?, party=?, stage=?, value=?, probability=?, expected_close=?, owner=?, source=?, notes=?, offer_id=? "
             "WHERE id=?", vals + (int(did),))
        if old["stage"] != stage:
            _stage_changed(db, int(did), old["stage"], stage, user, d.get("lost_reason"))
        return int(did)
    did = db.x("INSERT INTO crm_deals(title,party,stage,value,probability,expected_close,owner,source,notes,offer_id,created_by,created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", vals + (user, now_iso())).lastrowid
    add_activity(db, {"party": party, "deal_id": did, "kind": "note", "subject": f"Deal opened: {title}"}, user, system=True)
    return did


def _stage_changed(db, did, old, new, user, reason=None):
    closed = new in ("won", "lost")
    db.x("UPDATE crm_deals SET closed_at=?, lost_reason=? WHERE id=?",
         (now_iso() if closed else None, text(reason, maxlen=300) if new == "lost" else "", did))
    deal = db.one("SELECT title, party FROM crm_deals WHERE id=?", (did,))
    label = dict((s[0], s[1]) for s in STAGES)
    add_activity(db, {"party": deal["party"], "deal_id": did, "kind": "note",
                      "subject": f"{deal['title']}: {label[old]} → {label[new]}" + (f" ({reason})" if new == "lost" and reason else "")}, user, system=True)


def move_deal(db, did, stage, user, reason=None):
    if stage not in STAGE_KEYS:
        raise ValidationError("Unknown stage")
    old = db.one("SELECT stage FROM crm_deals WHERE id=?", (int(did),))
    if not old:
        raise ValidationError("Deal not found")
    if old["stage"] == stage:
        return
    prob = dict((s[0], s[3]) for s in STAGES)[stage]
    db.x("UPDATE crm_deals SET stage=?, probability=? WHERE id=?", (stage, prob, int(did)))
    _stage_changed(db, int(did), old["stage"], stage, user, reason)


def delete_deal(db, did):
    db.x("UPDATE crm_activities SET deal_id=NULL WHERE deal_id=?", (int(did),))
    db.x("UPDATE crm_followups SET deal_id=NULL WHERE deal_id=?", (int(did),))
    db.x("DELETE FROM crm_deals WHERE id=?", (int(did),))


def _deal_out(r):
    r = dict(r)
    r["value"] = to_major(r["value"] or 0)
    r["weighted"] = round(r["value"] * (r["probability"] or 0) / 100, 2)
    return r


def board(db, q):
    w, p = ["1=1"], []
    if q.get("owner"):
        w.append("owner=?")
        p.append(q["owner"])
    if q.get("party"):
        w.append("party=?")
        p.append(q["party"])
    for word in (q.get("q") or "").split():
        w.append("(title LIKE ? OR party LIKE ? OR notes LIKE ?)")
        p += [f"%{word}%"] * 3
    if q.get("closed") != "all":       # closed deals: those of the last 90 days only, unless all are asked for
        w.append("(stage NOT IN ('won','lost') OR closed_at >= date('now','-90 day'))")
    rows = [_deal_out(r) for r in db.q(f"SELECT * FROM crm_deals WHERE {' AND '.join(w)} ORDER BY expected_close='' , expected_close, id DESC", p)]
    nxt = {r["deal_id"]: r for r in db.q("SELECT deal_id, MIN(due) AS due, title FROM crm_followups WHERE done=0 AND deal_id IS NOT NULL GROUP BY deal_id")}
    for r in rows:
        f = nxt.get(r["id"])
        r["next"] = {"due": f["due"], "title": f["title"]} if f else None
    cols = []
    for key, en, ar, prob in STAGES:
        items = [r for r in rows if r["stage"] == key]
        cols.append({"key": key, "en": en, "ar": ar, "probability": prob, "count": len(items),
                     "value": round(sum(r["value"] for r in items), 2), "weighted": round(sum(r["weighted"] for r in items), 2), "deals": items})
    open_ = [r for r in rows if r["stage"] not in ("won", "lost")]
    return {"columns": cols, "open_value": round(sum(r["value"] for r in open_), 2),
            "weighted": round(sum(r["weighted"] for r in open_), 2), "owners": [r["owner"] for r in db.q("SELECT DISTINCT owner FROM crm_deals WHERE owner<>''")]}


# ------------------------------------------------------------------ activities (the timeline's own notes)
def add_activity(db, d, user, system=False):
    kind = d.get("kind") if d.get("kind") in KINDS else "note"
    party = _party(db, d.get("party"))
    subject = text(d.get("subject"), "subject", required=not system and not d.get("body"), maxlen=300)
    at = text(d.get("at"), maxlen=25) or now_iso()
    deal = int(d["deal_id"]) if str(d.get("deal_id") or "").isdigit() else None
    return db.x("INSERT INTO crm_activities(party,deal_id,kind,subject,body,at,created_by,created_at,auto) VALUES (?,?,?,?,?,?,?,?,?)",
                (party, deal, kind, subject, text(d.get("body"), maxlen=4000), at, user, now_iso(), 1 if system else 0)).lastrowid


def delete_activity(db, aid, user, is_admin):
    r = db.one("SELECT created_by, auto FROM crm_activities WHERE id=?", (int(aid),))
    if not r:
        raise ValidationError("Not found")
    if r["auto"] or (r["created_by"] != user and not is_admin):
        raise PermissionError()
    db.x("DELETE FROM crm_activities WHERE id=?", (int(aid),))


# ------------------------------------------------------------------ follow-ups
def save_followup(db, d, user, fid=None):
    title = text(d.get("title"), "what to do", required=True, maxlen=300)
    due = clean_date(d.get("due") or today())
    party = _party(db, d.get("party"), required=False) if d.get("party") else ""
    deal = int(d["deal_id"]) if str(d.get("deal_id") or "").isdigit() else None
    who = text(d.get("assigned_to") or user, maxlen=60)
    if fid:
        db.x("UPDATE crm_followups SET title=?, due=?, party=?, deal_id=?, assigned_to=?, notes=? WHERE id=?",
             (title, due, party, deal, who, text(d.get("notes"), maxlen=2000), int(fid)))
        return int(fid)
    return db.x("INSERT INTO crm_followups(title,due,party,deal_id,assigned_to,notes,done,created_by,created_at) VALUES (?,?,?,?,?,?,0,?,?)",
                (title, due, party, deal, who, text(d.get("notes"), maxlen=2000), user, now_iso())).lastrowid


def done_followup(db, fid, user, done=True, outcome=""):
    f = db.one("SELECT * FROM crm_followups WHERE id=?", (int(fid),))
    if not f:
        raise ValidationError("Not found")
    db.x("UPDATE crm_followups SET done=?, done_at=?, done_by=? WHERE id=?", (1 if done else 0, now_iso() if done else None, user if done else None, int(fid)))
    if done and f["party"]:
        add_activity(db, {"party": f["party"], "deal_id": f["deal_id"], "kind": "note",
                          "subject": f"Done: {f['title']}", "body": text(outcome, maxlen=2000)}, user, system=True)


def followups(db, user, q):
    w, p = ["1=1"], []
    who = q.get("who") or "me"
    if who == "me":
        w.append("f.assigned_to=?")
        p.append(user)
    elif who != "all":
        w.append("f.assigned_to=?")
        p.append(who)
    st = q.get("status") or "open"
    if st == "open":
        w.append("f.done=0")
    elif st == "due":
        w.append("f.done=0 AND f.due<=?")
        p.append(today())
    elif st == "done":
        w.append("f.done=1")
    if q.get("party"):
        w.append("f.party=?")
        p.append(q["party"])
    rows = db.q(f"SELECT f.*, d.title AS deal_title FROM crm_followups f LEFT JOIN crm_deals d ON d.id=f.deal_id WHERE {' AND '.join(w)} "
                f"ORDER BY f.done, f.due, f.id LIMIT 500", p)
    t = today()
    for r in rows:
        r["state"] = "done" if r["done"] else "overdue" if r["due"] < t else "today" if r["due"] == t else "upcoming"
    return rows


def due_for(db, user):
    """This person's follow-ups due today or overdue (for the bell)."""
    return db.q("SELECT id, title, due, party FROM crm_followups WHERE done=0 AND assigned_to=? AND due<=? ORDER BY due, id", (user, today()))


# ------------------------------------------------------------------ contacts and their timeline
def contacts(db, q):
    w, p = ["1=1"], []
    if q.get("type"):
        w.append("p.type IN (?, 'both')")
        p.append(q["type"])
    for word in (q.get("q") or "").split():
        w.append("(p.name LIKE ? OR p.phone LIKE ? OR p.email LIKE ?)")
        p += [f"%{word}%"] * 3
    rows = db.q(f"""SELECT p.id, p.name, p.type, p.phone, p.email,
          (SELECT MAX(at) FROM crm_activities a WHERE a.party=p.name AND a.auto=0) AS last_contact,
          (SELECT MIN(due) FROM crm_followups f WHERE f.party=p.name AND f.done=0) AS next_followup,
          (SELECT COUNT(*) FROM crm_deals d WHERE d.party=p.name AND d.stage NOT IN ('won','lost')) AS open_deals,
          (SELECT COALESCE(SUM(value),0) FROM crm_deals d WHERE d.party=p.name AND d.stage NOT IN ('won','lost')) AS open_value
        FROM parties p WHERE {' AND '.join(w)} ORDER BY COALESCE(last_contact,'') DESC, p.name LIMIT 500""", p)
    for r in rows:
        r["open_value"] = to_major(r["open_value"] or 0)
    return rows


def timeline(db, party, can):
    """Everything about one contact, newest first: what was logged here and what the books hold."""
    party = text(party)
    p = db.one("SELECT * FROM parties WHERE name=?", (party,))
    if not p:
        raise ValidationError("Contact not found")
    ev = []
    for a in db.q("SELECT * FROM crm_activities WHERE party=? ORDER BY at DESC LIMIT 400", (party,)):
        ev.append({"at": a["at"], "kind": a["kind"], "title": a["subject"], "body": a["body"], "by": a["created_by"], "id": a["id"], "auto": bool(a["auto"]),
                   "deal_id": a["deal_id"]})
    for f in db.q("SELECT * FROM crm_followups WHERE party=? ORDER BY due DESC LIMIT 200", (party,)):
        ev.append({"at": f["due"], "kind": "followup", "title": f["title"], "body": f["notes"], "by": f["assigned_to"], "id": f["id"],
                   "done": bool(f["done"])})
    if can("sales.offers"):
        for o in db.q("SELECT id, number, date, status, project FROM offers WHERE client=? ORDER BY date DESC LIMIT 100", (party,)):
            ev.append({"at": o["date"] or "", "kind": "offer", "title": f"Offer {o['number']} — {o['status']}", "body": o["project"] or "",
                       "href": f"#/offer/{o['id']}"})
    if can("sales.invoices") or can("sales.extracts"):
        from . import accounting as acc
        for x in db.q("SELECT id, number, date, status FROM extracts WHERE customer=? ORDER BY date DESC LIMIT 100", (party,)):
            tot = acc.get_extract(db, x["id"])["total"]
            ev.append({"at": x["date"] or "", "kind": "invoice", "title": f"Invoice {x['number']} ({'posted' if x['status'] == 'posted' else 'draft'})",
                       "amount": tot, "href": f"#/invoice/{x['id']}"})
    if can("accounting.journal") or can("sales.receipts") or can("purchasing.payments"):
        for e in db.q("SELECT e.id, e.date, e.kind, e.memo, (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) AS amt FROM journal_entries e "
                      "WHERE e.status='posted' AND (e.party=? OR EXISTS (SELECT 1 FROM journal_lines l WHERE l.entry_id=e.id AND l.party=?)) "
                      "AND e.kind IN ('Receipt Voucher','Payment Voucher','Operating Expense','Direct Cost','Supplier Discount') "
                      "ORDER BY e.date DESC LIMIT 150", (party, party)):
            ev.append({"at": e["date"], "kind": "payment" if e["kind"] in ("Receipt Voucher", "Payment Voucher") else "entry",
                       "title": f"{e['kind']} #{e['id']}", "body": e["memo"] or "", "amount": to_major(e["amt"] or 0), "voucher": e["id"]})
    if can("shop.orders"):
        for s in db.q("SELECT id, code, date, status FROM sales_orders WHERE customer=? ORDER BY date DESC LIMIT 50", (party,)):
            ev.append({"at": s["date"] or "", "kind": "sale", "title": f"Sale {s['code']} — {s['status']}", "href": f"#/sale/{s['id']}"})
    ev.sort(key=lambda x: str(x["at"] or ""), reverse=True)
    deals = [_deal_out(d) for d in db.q("SELECT * FROM crm_deals WHERE party=? ORDER BY id DESC", (party,))]
    return {"contact": p, "events": ev[:600], "deals": deals,
            "open_followups": [f for f in followups(db, "", {"who": "all", "party": party, "status": "open"})]}


# ------------------------------------------------------------------ offer → project, BOQ and first invoice
def offer_lines(o):
    """The offer's priced lines as BOQ lines (its pricing section, or the package the client chose)."""
    from . import documents
    sec = documents.pricing(o)
    if not sec:
        raise ValidationError("This offer has no prices. Complete its pricing, or choose the client's package, first.")
    lines = [{"category": o.get("project") or "", "description": text(i.get("desc"), maxlen=2000) or "Item", "unit": text(i.get("unit")) or "LS",
              "qty": float(i.get("qty") or 1), "price": round(float(i.get("amount") or 0) / float(i.get("qty") or 1), 6)}
             for i in sec.get("items") or [] if text(i.get("desc")) or float(i.get("amount") or 0)]
    if not lines:
        raise ValidationError("The offer's pricing has no lines")
    disc, total = float(sec.get("discount") or 0), sum(ln["qty"] * ln["price"] for ln in lines)
    if disc and total > 0:              # the offer's discount, spread over its lines (a BOQ price is never negative)
        k = max(0.0, (total - disc) / total)
        for ln in lines:
            ln["price"] = round(ln["price"] * k, 6)
    return lines


def convert_offer(db, oid, user, make_bu):
    """One step: the offer's project (business unit, made when missing), a BOQ with the offer's prices, linked back
    to the offer (marked Accepted). The invoice is then started from that BOQ."""
    from . import accounting as acc, documents
    o = documents.get_offer(db, int(oid))
    content = json.loads(db.one("SELECT content FROM offers WHERE id=?", (int(oid),))["content"] or "{}")
    if content.get("boq_id") and db.one("SELECT 1 FROM boqs WHERE id=?", (content["boq_id"],)):   # converted before
        return {"bu_id": o.get("bu_id"), "boq_id": content["boq_id"], "existing": True}
    lines = offer_lines(o)
    bu_id = o.get("bu_id")
    if not bu_id:
        bu_id = make_bu(o)
        db.x("UPDATE offers SET bu_id=? WHERE id=?", (bu_id, int(oid)))
    boq = acc.save_boq(db, {"title": f"{o.get('number') or ''} {o.get('project') or o.get('client') or ''}".strip(), "bu_id": bu_id,
                            "rev": "Contract", "date": today(), "client": o.get("client") or "", "location": o.get("project") or "", "lines": lines})
    content["boq_id"] = boq
    db.x("UPDATE offers SET content=?, status=CASE WHEN status IN ('Draft','Sent') THEN 'Accepted' ELSE status END WHERE id=?",
         (json.dumps(content, ensure_ascii=False), int(oid)))
    for d in db.q("SELECT id, stage FROM crm_deals WHERE offer_id=? AND stage NOT IN ('won','lost')", (int(oid),)):
        move_deal(db, d["id"], "won", user)
    if o.get("client"):
        add_activity(db, {"party": o["client"], "kind": "note", "subject": f"Offer {o.get('number')} accepted — project and BOQ made"}, user, system=True)
    return {"bu_id": bu_id, "boq_id": boq, "existing": False}


# ------------------------------------------------------------------ backups
TABLES = ("crm_deals", "crm_activities", "crm_followups")


def export_rows(db):
    return {t: db.q(f"SELECT * FROM {t} ORDER BY id") for t in TABLES}


def import_rows(db, data, replace=False):
    n = 0
    if replace:
        for t in TABLES:
            db.conn.execute(f"DELETE FROM {t}")
    ids = {}
    for r in data.get("crm_deals") or []:
        cols = ("title", "party", "stage", "value", "probability", "expected_close", "owner", "source", "notes", "created_by", "created_at", "closed_at", "lost_reason")
        new = db.conn.execute(f"INSERT INTO crm_deals({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [r.get(c) for c in cols]).lastrowid
        ids[r.get("id")] = new
        n += 1
    for r in data.get("crm_activities") or []:
        cols = ("party", "kind", "subject", "body", "at", "created_by", "created_at", "auto")
        db.conn.execute(f"INSERT INTO crm_activities({','.join(cols)},deal_id) VALUES ({','.join('?' * len(cols))},?)",
                        [r.get(c) for c in cols] + [ids.get(r.get("deal_id"))])
        n += 1
    for r in data.get("crm_followups") or []:
        cols = ("title", "due", "party", "assigned_to", "notes", "done", "done_at", "done_by", "created_by", "created_at")
        db.conn.execute(f"INSERT INTO crm_followups({','.join(cols)},deal_id) VALUES ({','.join('?' * len(cols))},?)",
                        [r.get(c) for c in cols] + [ids.get(r.get("deal_id"))])
        n += 1
    return n
