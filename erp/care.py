"""Healthcare: clinics, medical centres, labs and radiology, and home healthcare — in the books.

How Egyptian providers work, and what this follows:
- Patients pay cash, or are covered by an insurer / TPA (the networks private insurers use) for a share of each covered
  service at the contracted price; the rest is the patient's co-pay at the desk. The insurer's share becomes a
  receivable that is claimed monthly; the claim comes back paid with some lines rejected (written off here).
- Doctors, nurses and therapists are often paid a share of what they deliver (a percentage per service) rather than,
  or on top of, a salary: that share is booked as a cost and owed to them (paid from Payments, or through payroll).
- Every visit can use consumables (gloves, syringes, dressing kits…) taken from stock at cost.
- Home healthcare adds the patient's address and district for routing, a travel fee, check-in / check-out times (the
  visit really happened, how long a nursing shift lasted), and prepaid packages (12 physiotherapy sessions, a month
  of nursing): the money is held as prepaid until each session is given.
- Medical services are generally VAT-exempt in Egypt: invoices carry no VAT unless an account is set otherwise.

Clinical records stay out on purpose: this is the business side (who was seen, by whom, for what, and who pays).
Only short alerts (allergies) and visit notes are kept, visible to people with access to Healthcare."""
import json

from . import accounting as acc
from .accounting import ValidationError, clean_date, now_iso, text, to_major, to_minor

CATEGORIES = ("consultation", "procedure", "lab", "radiology", "nursing", "physiotherapy", "home_visit", "package", "other")
STATUSES = ("booked", "arrived", "done", "no_show", "cancelled")
# code, name, Arabic, type, subtype — added when missing (by the healthcare business types, or on first billing)
ACCOUNTS = [("1412", "Medical Equipment & Devices", "أجهزة ومعدات طبية", "Asset", "fixed_asset"),
            ("1105", "Receivables — Insurance Companies & TPAs", "ذمم شركات التأمين وإدارة النفقات الطبية", "Asset", "receivable"),
            ("2108", "Prepaid Care Packages", "باقات رعاية مدفوعة مقدماً", "Liability", "deferred_revenue"),
            ("2115", "Doctors' & Caregivers' Fees Payable", "مستحقات الأطباء ومقدمي الرعاية", "Liability", "payable"),
            ("4056", "Insurance Claim Rejections", "مرفوضات مطالبات التأمين", "Revenue", "sales_discount"),
            ("4061", "Medical Services Revenue", "إيرادات الخدمات الطبية", "Revenue", "operating_revenue"),
            ("4062", "Home Healthcare Revenue", "إيرادات الرعاية المنزلية", "Revenue", "operating_revenue"),
            ("4063", "Home Visit & Transport Fees", "رسوم الزيارة المنزلية والانتقال", "Revenue", "operating_revenue"),
            ("5090", "Doctors' & Caregivers' Fees", "أتعاب الأطباء ومقدمي الرعاية", "COGS", "cogs"),
            ("5091", "Medical Consumables Used", "مستهلكات طبية", "COGS", "cogs")]
AR, INS, PREPAID, FEES_DUE, REJECT, REV, REV_HOME, REV_TRAVEL, FEES, CONSUMABLES = \
    "1100", "1105", "2108", "2115", "4056", "4061", "4062", "4063", "5090", "5091"


def ensure_accounts(db):
    added = []
    for code, name, ar, typ, sub in ACCOUNTS:
        if not db.one("SELECT 1 FROM accounts WHERE code=?", (code,)):
            acc.save_account(db, {"code": code, "name": name, "name_ar": ar, "type": typ, "subtype": sub})
            added.append(code)
    return added


def _next(db, table, prefix, width=5):
    n = (db.one(f"SELECT MAX(id) m FROM {table}")["m"] or 0) + 1
    while db.one(f"SELECT 1 FROM {table} WHERE {'number' if table in ('care_visits', 'care_claims') else 'code'}=?", (f"{prefix}{n:0{width}d}",)):
        n += 1
    return f"{prefix}{n:0{width}d}"


def _party(db, name, kind="customer"):
    name = text(name, maxlen=200)
    if name and not db.one("SELECT 1 FROM parties WHERE name=?", (name,)):
        db.x("INSERT INTO parties(name,type) VALUES (?,?)", (name, kind))
    return name


def _pct(v, field):
    try:
        p = float(v or 0)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number")
    if not 0 <= p <= 100:
        raise ValidationError(f"{field} is a percentage between 0 and 100")
    return p


# ------------------------------------------------------------------ patients
def save_patient(db, d, pid=None):
    name = text(d.get("name"), "patient name", required=True, maxlen=200)
    payer = "insurer" if d.get("payer") == "insurer" else "self"
    insurer = _party(db, d.get("insurer"), "customer") if payer == "insurer" else ""
    if payer == "insurer" and not insurer:
        raise ValidationError("Name the insurance company / TPA, or set the patient to pay themselves")
    vals = (name, text(d.get("national_id"), maxlen=20), text(d.get("phone"), maxlen=40), text(d.get("phone2"), maxlen=40),
            clean_date(d["dob"]) if d.get("dob") else "", d.get("gender") if d.get("gender") in ("male", "female") else "",
            text(d.get("address"), maxlen=300), text(d.get("zone"), maxlen=80), payer, insurer, text(d.get("policy_no"), maxlen=60),
            _pct(d.get("coverage_pct"), "Coverage") if payer == "insurer" else 0, text(d.get("alerts"), maxlen=500),
            text(d.get("notes"), maxlen=2000), 0 if d.get("active") in (False, 0, "0") else 1)
    if pid:
        if not db.one("SELECT 1 FROM care_patients WHERE id=?", (int(pid),)):
            raise ValidationError("Patient not found")
        db.x("UPDATE care_patients SET name=?, national_id=?, phone=?, phone2=?, dob=?, gender=?, address=?, zone=?, payer=?, insurer=?, "
             "policy_no=?, coverage_pct=?, alerts=?, notes=?, active=? WHERE id=?", vals + (int(pid),))
        return int(pid)
    code = text(d.get("code"), maxlen=30) or _next(db, "care_patients", "P-")
    if db.one("SELECT 1 FROM care_patients WHERE code=?", (code,)):
        raise ValidationError(f"Patient file number {code} is already used")
    return db.x("INSERT INTO care_patients(code,name,national_id,phone,phone2,dob,gender,address,zone,payer,insurer,policy_no,coverage_pct,"
                "alerts,notes,active,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (code,) + vals + (now_iso(),)).lastrowid


def patients(db, q=None, payer=None, insurer=None, active=None):
    w, p = ["1=1"], []
    if q:
        w.append("(name LIKE ? OR code LIKE ? OR phone LIKE ? OR national_id LIKE ? OR policy_no LIKE ?)")
        p += [f"%{q}%"] * 5
    if payer in ("self", "insurer"):
        w.append("payer=?")
        p.append(payer)
    if insurer:
        w.append("insurer=?")
        p.append(insurer)
    if active in ("1", "0"):
        w.append("active=?")
        p.append(int(active))
    rows = db.q(f"""SELECT p.*, (SELECT COUNT(*) FROM care_visits v WHERE v.patient_id=p.id AND v.status='done') visits,
                           (SELECT MAX(date) FROM care_visits v WHERE v.patient_id=p.id AND v.status='done') last_visit
                    FROM care_patients p WHERE {' AND '.join(w)} ORDER BY p.name""", p)
    bal = {r["party"]: r["bal"] for r in db.q("SELECT l.party, SUM(l.debit-l.credit) bal FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                                              "WHERE e.status='posted' AND l.account=? AND l.party<>'' GROUP BY l.party", (AR,))}
    for r in rows:
        r["owes"] = to_major(bal.get(r["name"], 0))
    return rows


def patient(db, pid):
    r = db.one("SELECT * FROM care_patients WHERE id=?", (int(pid),))
    if not r:
        raise LookupError("Patient not found")
    r["visits"] = visits(db, patient_id=r["id"], limit=200)
    r["packages"] = packages(db, patient_id=r["id"])
    r["owes"] = to_major(acc.party_balance(db, AR, r["name"])) if db.one("SELECT 1 FROM accounts WHERE code=?", (AR,)) else 0
    return r


# ------------------------------------------------------------------ services & prices
def save_service(db, d, sid=None):
    name = text(d.get("name"), "service", required=True, maxlen=200)
    cat = d.get("category") if d.get("category") in CATEGORIES else "other"
    cons = []
    for c in (d.get("consumables") or []) if isinstance(d.get("consumables"), list) else []:
        item = text((c or {}).get("item"), maxlen=60)
        qty = float((c or {}).get("qty") or 0)
        if item and qty > 0:
            cons.append({"item": item, "qty": qty})
    vals = (name, text(d.get("name_ar"), maxlen=200), cat, to_minor(acc.num(d.get("price") or 0, "price")),
            to_minor(acc.num(d.get("insurer_price") or 0, "insurer price")), 0 if d.get("covered") in (False, 0, "0") else 1,
            _pct(d.get("provider_pct"), "Provider share"), int(float(d.get("duration_min") or 30)), 1 if d.get("home") in (True, 1, "1") else 0,
            json.dumps(cons), 0 if d.get("active") in (False, 0, "0") else 1)
    if sid:
        db.x("UPDATE care_services SET name=?, name_ar=?, category=?, price=?, insurer_price=?, covered=?, provider_pct=?, duration_min=?, "
             "home=?, consumables=?, active=? WHERE id=?", vals + (int(sid),))
        return int(sid)
    code = text(d.get("code"), maxlen=30) or _next(db, "care_services", "S-", 3)
    if db.one("SELECT 1 FROM care_services WHERE code=?", (code,)):
        raise ValidationError(f"Service code {code} is already used")
    return db.x("INSERT INTO care_services(code,name,name_ar,category,price,insurer_price,covered,provider_pct,duration_min,home,consumables,active) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (code,) + vals).lastrowid


def _svc_out(r):
    r = dict(r)
    r["price"], r["insurer_price"] = to_major(r["price"]), to_major(r["insurer_price"])
    r["consumables"] = json.loads(r["consumables"] or "[]")
    return r


def services(db, active=None):
    w = "WHERE active=1" if active == "1" else ""
    return [_svc_out(r) for r in db.q(f"SELECT * FROM care_services {w} ORDER BY category, name")]


# ------------------------------------------------------------------ visits (clinic appointments and home visits)
def _split(pat, svc, qty, price_minor=None):
    """One line: the price (insurers pay their contracted price), the insurer's share, the patient's co-pay and the
    provider's share."""
    insured = pat["payer"] == "insurer" and svc and svc["covered"] and pat["coverage_pct"] > 0
    unit = price_minor if price_minor is not None else ((svc["insurer_price"] or svc["price"]) if insured else svc["price"]) if svc else 0
    total = int(round(unit * qty))
    ins = int(round(total * pat["coverage_pct"] / 100)) if insured else 0
    prov = int(round(total * (svc["provider_pct"] if svc else 0) / 100))
    return unit, total, ins, total - ins, prov


def save_visit(db, d, user, vid=None):
    pid = int(d.get("patient_id") or 0)
    pat = db.one("SELECT * FROM care_patients WHERE id=?", (pid,))
    if not pat:
        raise ValidationError("Pick the patient")
    if vid:
        old = db.one("SELECT * FROM care_visits WHERE id=?", (int(vid),))
        if not old:
            raise ValidationError("Visit not found")
        if old["entry_id"]:
            raise ValidationError("This visit is already billed — void its invoice first to change it")
    place = "home" if d.get("place") == "home" else "clinic"
    pkg = None
    if str(d.get("package_id") or "").isdigit():
        pkg = db.one("SELECT * FROM care_packages WHERE id=? AND patient_id=?", (int(d["package_id"]), pid))
        if not pkg:
            raise ValidationError("That package is not this patient's")
    lines, raw = [], d.get("lines") if isinstance(d.get("lines"), list) else []
    for ln in raw:
        sid = int((ln or {}).get("service_id") or 0)
        svc = db.one("SELECT * FROM care_services WHERE id=?", (sid,)) if sid else None
        if not svc and not text((ln or {}).get("description")):
            continue
        qty = float(ln.get("qty") or 1)
        if qty <= 0:
            raise ValidationError("Quantities must be more than zero")
        price = to_minor(acc.num(ln["price"], "price")) if ln.get("price") not in (None, "") else None
        if pkg and svc and svc["id"] == pkg["service_id"]:
            price = 0          # covered by the prepaid package
        unit, total, ins, pat_amt, prov = _split(pat, svc, qty, price)
        if pkg and svc and svc["id"] == pkg["service_id"]:
            prov = int(round(pkg["price"] / max(1, pkg["sessions"]) * qty * (svc["provider_pct"] / 100)))
        lines.append((svc["id"] if svc else None, text(ln.get("description") or (svc["name"] if svc else ""), maxlen=200), qty, unit, pat_amt, ins, prov))
    if not lines:
        raise ValidationError("Add at least one service")
    travel = to_minor(acc.num(d.get("travel_fee") or 0, "travel fee")) if place == "home" else 0
    vals = (clean_date(d.get("date")), text(d.get("time"), maxlen=10), pid, text(d.get("provider"), maxlen=120), place,
            acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")),
            text(d.get("address") or (pat["address"] if place == "home" else ""), maxlen=300),
            text(d.get("zone") or (pat["zone"] if place == "home" else ""), maxlen=80),
            float(d.get("hours") or 0), travel, pkg["id"] if pkg else None, text(d.get("notes"), maxlen=4000),
            sum(x[4] for x in lines) + travel, sum(x[5] for x in lines), sum(x[6] for x in lines))
    with db.tx():
        if vid:
            db.x("UPDATE care_visits SET date=?, time=?, patient_id=?, provider=?, place=?, bu_id=?, address=?, zone=?, hours=?, travel_fee=?, "
                 "package_id=?, notes=?, patient_amt=?, insurer_amt=?, provider_amt=? WHERE id=?", vals + (int(vid),))
            db.x("DELETE FROM care_visit_lines WHERE visit_id=?", (int(vid),))
        else:
            vid = db.x("INSERT INTO care_visits(date,time,patient_id,provider,place,bu_id,address,zone,hours,travel_fee,package_id,notes,patient_amt,"
                       "insurer_amt,provider_amt,number,status,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       vals + (_next(db, "care_visits", "V-"), d.get("status") if d.get("status") in STATUSES else "booked", user, now_iso())).lastrowid
        db.conn.executemany("INSERT INTO care_visit_lines(visit_id,service_id,description,qty,price,patient_amt,insurer_amt,provider_amt) VALUES (?,?,?,?,?,?,?,?)",
                            [(int(vid),) + x for x in lines])
    return int(vid)


def set_status(db, vid, status, d=None):
    """Arrived (checked in), done (checked out), no-show or cancelled. Home visits keep the check-in / check-out
    times — the record that the visit happened and how long a shift was."""
    d = d or {}
    v = _visit_row(db, vid)
    if status not in STATUSES:
        raise ValidationError("Unknown status")
    if v["entry_id"] and status != "done":
        raise ValidationError("This visit is already billed")
    now = acc.now_iso()[11:16]
    ci, co = v["check_in"], v["check_out"]
    if status == "arrived":
        ci = text(d.get("time"), maxlen=5) or now
    if status == "done":
        ci = ci or text(d.get("check_in"), maxlen=5)
        co = text(d.get("time") or d.get("check_out"), maxlen=5) or now
    hours = v["hours"]
    if status == "done" and ci and co and not hours:
        try:
            h1, m1 = map(int, ci.split(":"))
            h2, m2 = map(int, co.split(":"))
            mins = (h2 * 60 + m2) - (h1 * 60 + m1)
            hours = round((mins if mins >= 0 else mins + 1440) / 60, 2)      # past midnight: an overnight shift
        except ValueError:
            pass
    vitals = d.get("vitals") if isinstance(d.get("vitals"), dict) else None
    db.x("UPDATE care_visits SET status=?, check_in=?, check_out=?, hours=?, notes=COALESCE(?, notes), vitals=COALESCE(?, vitals) WHERE id=?",
         (status, ci, co, hours, text(d["notes"], maxlen=4000) if "notes" in d else None,
          json.dumps({k: text(x, maxlen=30) for k, x in vitals.items()}) if vitals else None, v["id"]))


def _visit_row(db, vid):
    v = db.one("SELECT * FROM care_visits WHERE id=?", (int(vid),))
    if not v:
        raise LookupError("Visit not found")
    return v


def bill_visit(db, vid, d, user):
    """Bill a done visit, all in one balanced entry:
    the patient's co-pay (paid now in cash / card, or owed), the insurer's share (receivable, claimed later), the
    revenue (clinic, home care, travel fee), and a package session moved from prepaid to revenue. Then, in a second
    entry, the provider's share as a cost owed to them; consumables leave stock at cost."""
    v = _visit_row(db, vid)
    if v["entry_id"]:
        raise ValidationError("This visit is already billed")
    if v["status"] != "done":
        raise ValidationError("Mark the visit as done before billing it")
    ensure_accounts(db)
    pat = db.one("SELECT * FROM care_patients WHERE id=?", (v["patient_id"],))
    lines = db.q("SELECT l.*, s.category, s.consumables, s.home FROM care_visit_lines l LEFT JOIN care_services s ON s.id=l.service_id WHERE visit_id=?", (v["id"],))
    rev_acc = REV_HOME if v["place"] == "home" else REV
    pkg = db.one("SELECT * FROM care_packages WHERE id=?", (v["package_id"],)) if v["package_id"] else None
    pkg_val = 0
    if pkg:
        used = sum(ln["qty"] for ln in lines if ln["service_id"] == pkg["service_id"])
        if pkg["used"] + used > pkg["sessions"] + 1e-9:
            raise ValidationError(f"The package has {pkg['sessions'] - pkg['used']} session(s) left")
        pkg_val = int(round(pkg["price"] / pkg["sessions"] * used))
    date = clean_date(d.get("date") or v["date"])
    paid = min(to_minor(acc.num(d.get("paid") if d.get("paid") not in (None, "") else to_major(v["patient_amt"]), "paid now")), v["patient_amt"])
    cash = acc.money_account(db, d.get("method") or "Cash")
    jl = []
    if paid:
        jl.append({"account": cash, "debit": to_major(paid)})
    if v["patient_amt"] - paid:
        jl.append({"account": AR, "debit": to_major(v["patient_amt"] - paid), "party": pat["name"]})
    if v["insurer_amt"]:
        jl.append({"account": INS, "debit": to_major(v["insurer_amt"]), "party": pat["insurer"]})
    if pkg_val:
        jl.append({"account": PREPAID, "debit": to_major(pkg_val), "party": pat["name"]})
    services_total = v["patient_amt"] + v["insurer_amt"] - v["travel_fee"]
    if services_total + pkg_val:
        jl.append({"account": rev_acc, "credit": to_major(services_total + pkg_val)})
    if v["travel_fee"]:
        jl.append({"account": REV_TRAVEL, "credit": to_major(v["travel_fee"])})
    if not jl:
        raise ValidationError("Nothing to bill on this visit")
    who = pat["name"] + (f" ({pat['insurer']} {pat['coverage_pct']:g}%)" if v["insurer_amt"] else "")
    with db.tx():
        eid = acc._insert_entry(db, date=date, kind="Medical Invoice", memo=f"{v['number']} — {who}", bu_id=v["bu_id"], party=pat["name"],
                                ref=v["number"], lines=jl, user=user, meta={"visit": v["id"]})
        cost_eid = None
        if v["provider_amt"] and v["provider"]:
            cost_eid = acc._insert_entry(db, date=date, kind="Provider Fees", memo=f"{v['number']} — {v['provider']}'s share", bu_id=v["bu_id"],
                                         party=v["provider"], ref=v["number"], user=user, meta={"visit": v["id"]},
                                         lines=[{"account": FEES, "debit": to_major(v["provider_amt"])},
                                                {"account": FEES_DUE, "credit": to_major(v["provider_amt"]), "party": v["provider"]}])
        db.x("UPDATE care_visits SET entry_id=?, cost_entry_id=?, paid=?, billed_at=? WHERE id=?", (eid, cost_eid, paid, now_iso(), v["id"]))
        if pkg:
            used_now = pkg["used"] + sum(ln["qty"] for ln in lines if ln["service_id"] == pkg["service_id"])
            db.x("UPDATE care_packages SET used=?, status=? WHERE id=?", (int(round(used_now)), "used" if used_now >= pkg["sessions"] else pkg["status"], pkg["id"]))
    # consumables: from stock at cost, on the visit's branch (skipped with a note when stock is short)
    short = []
    wh = d.get("warehouse_id") or (db.one("SELECT id FROM warehouses WHERE active=1 ORDER BY id") or {}).get("id")
    if v["bu_id"]:
        for ln in lines:
            for c in json.loads(ln["consumables"] or "[]"):
                it = db.one("SELECT id, name FROM stock_items WHERE sku=? OR name=? ORDER BY sku=? DESC", (c["item"], c["item"], c["item"]))
                if not it or not wh:
                    short.append(f"{c['item']}: not a stock item" if wh else "No warehouse to take consumables from")
                    continue
                try:
                    acc.stock_move(db, {"type": "Issue", "date": date, "item_id": it["id"], "item": it["name"], "qty": c["qty"] * ln["qty"],
                                        "warehouse_id": wh, "bu_id": v["bu_id"], "account": CONSUMABLES}, user)
                except ValidationError as e:
                    short.append(str(e))
    return {"entry_id": eid, "cost_entry_id": cost_eid, "short": short}


def unbill_visit(db, vid, user, reason=""):
    v = _visit_row(db, vid)
    if not v["entry_id"]:
        raise ValidationError("This visit is not billed")
    if v["claim_id"]:
        raise ValidationError("This visit is in an insurance claim — it cannot be unbilled")
    with db.tx():
        for eid in (v["entry_id"], v["cost_entry_id"]):
            if eid:
                acc.void_entry(db, eid, reason or f"Visit {v['number']} unbilled", user)
        if v["package_id"]:
            used = sum(r["qty"] for r in db.q("SELECT qty FROM care_visit_lines l JOIN care_packages p ON p.service_id=l.service_id "
                                              "WHERE l.visit_id=? AND p.id=?", (v["id"], v["package_id"])))
            db.x("UPDATE care_packages SET used=MAX(0, used-?), status='active' WHERE id=?", (int(round(used)), v["package_id"]))
        db.x("UPDATE care_visits SET entry_id=NULL, cost_entry_id=NULL, paid=0, billed_at=NULL WHERE id=?", (v["id"],))


def _visit_out(db, r):
    r = dict(r)
    for k in ("patient_amt", "insurer_amt", "provider_amt", "travel_fee", "paid"):
        r[k] = to_major(r[k])
    r["vitals"] = json.loads(r.get("vitals") or "{}")
    return r


def visits(db, date_from=None, date_to=None, status=None, place=None, provider=None, patient_id=None, bu_id=None, q=None, billed=None, limit=500):
    w, p = ["1=1"], []
    for col, val in (("v.date>=?", date_from), ("v.date<=?", date_to), ("v.status=?", status), ("v.place=?", place),
                     ("v.provider=?", provider), ("v.patient_id=?", patient_id), ("v.bu_id=?", bu_id)):
        if val not in (None, ""):
            w.append(col)
            p.append(val)
    if billed == "1":
        w.append("v.entry_id IS NOT NULL")
    elif billed == "0":
        w.append("v.entry_id IS NULL AND v.status='done'")
    if q:
        w.append("(pt.name LIKE ? OR pt.code LIKE ? OR v.number LIKE ? OR v.provider LIKE ? OR v.zone LIKE ?)")
        p += [f"%{q}%"] * 5
    rows = db.q(f"""SELECT v.*, pt.name patient, pt.code patient_code, pt.phone, pt.payer, pt.insurer, pt.alerts, b.code bu_code, b.name bu_name,
                           (SELECT GROUP_CONCAT(description, ' + ') FROM care_visit_lines l WHERE l.visit_id=v.id) services
                    FROM care_visits v JOIN care_patients pt ON pt.id=v.patient_id LEFT JOIN business_units b ON b.id=v.bu_id
                    WHERE {' AND '.join(w)} ORDER BY v.date DESC, v.time DESC, v.id DESC LIMIT ?""", p + [int(limit)])
    return [_visit_out(db, r) for r in rows]


def visit(db, vid):
    r = db.one("""SELECT v.*, pt.name patient, pt.code patient_code, pt.phone, pt.payer, pt.insurer, pt.coverage_pct, pt.alerts,
                         b.code bu_code, b.name bu_name FROM care_visits v JOIN care_patients pt ON pt.id=v.patient_id
                  LEFT JOIN business_units b ON b.id=v.bu_id WHERE v.id=?""", (int(vid),))
    if not r:
        raise LookupError("Visit not found")
    out = _visit_out(db, r)
    out["lines"] = [{**ln, "price": to_major(ln["price"]), "patient_amt": to_major(ln["patient_amt"]), "insurer_amt": to_major(ln["insurer_amt"]),
                     "provider_amt": to_major(ln["provider_amt"])} for ln in db.q("SELECT * FROM care_visit_lines WHERE visit_id=? ORDER BY id", (int(vid),))]
    return out


# ------------------------------------------------------------------ prepaid packages (home care, physiotherapy courses…)
def sell_package(db, d, user):
    pid = int(d.get("patient_id") or 0)
    pat = db.one("SELECT * FROM care_patients WHERE id=?", (pid,))
    svc = db.one("SELECT * FROM care_services WHERE id=?", (int(d.get("service_id") or 0),))
    if not pat or not svc:
        raise ValidationError("Pick the patient and the service the package covers")
    sessions = int(float(d.get("sessions") or 0))
    if sessions <= 0:
        raise ValidationError("A package needs at least one session")
    price = to_minor(acc.num(d.get("price"), "package price", allow_zero=False))
    paid = min(to_minor(acc.num(d.get("paid") if d.get("paid") not in (None, "") else to_major(price), "paid now")), price)
    date = clean_date(d.get("date"))
    ensure_accounts(db)
    jl = []
    if paid:
        jl.append({"account": acc.money_account(db, d.get("method") or "Cash"), "debit": to_major(paid)})
    if price - paid:
        jl.append({"account": AR, "debit": to_major(price - paid), "party": pat["name"]})
    jl.append({"account": PREPAID, "credit": to_major(price), "party": pat["name"]})
    name = text(d.get("name"), maxlen=200) or f"{sessions} × {svc['name']}"
    with db.tx():
        code = _next(db, "care_packages", "PK-", 4)
        eid = acc._insert_entry(db, date=date, kind="Care Package", memo=f"{code} — {name} — {pat['name']}", party=pat["name"], ref=code,
                                bu_id=acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), lines=jl, user=user)
        return db.x("INSERT INTO care_packages(code,patient_id,service_id,name,sessions,price,start_date,end_date,status,entry_id,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (code, pid, svc["id"], name, sessions, price, date,
                                                       clean_date(d["end_date"]) if d.get("end_date") else "", "active", eid, now_iso())).lastrowid


def packages(db, patient_id=None, status=None):
    w, p = ["1=1"], []
    if patient_id:
        w.append("k.patient_id=?")
        p.append(int(patient_id))
    if status:
        w.append("k.status=?")
        p.append(status)
    out = []
    for r in db.q(f"""SELECT k.*, pt.name patient, s.name service FROM care_packages k JOIN care_patients pt ON pt.id=k.patient_id
                      JOIN care_services s ON s.id=k.service_id WHERE {' AND '.join(w)} ORDER BY k.id DESC""", p):
        r = dict(r)
        r["left"] = r["sessions"] - r["used"]
        r["held"] = to_major(int(round(r["price"] * r["left"] / max(1, r["sessions"]))))   # still prepaid (owed in sessions)
        r["price"] = to_major(r["price"])
        out.append(r)
    return out


# ------------------------------------------------------------------ insurance claims
def make_claim(db, insurer, date_from, date_to, user):
    """Gather the insurer's share of every billed visit in the period not yet claimed, into one claim."""
    insurer = text(insurer, "insurer", required=True)
    rows = db.q("""SELECT v.id, v.insurer_amt FROM care_visits v JOIN care_patients pt ON pt.id=v.patient_id
                   WHERE pt.insurer=? AND v.entry_id IS NOT NULL AND v.claim_id IS NULL AND v.insurer_amt>0 AND v.date BETWEEN ? AND ?""",
                (insurer, clean_date(date_from), clean_date(date_to)))
    if not rows:
        raise ValidationError("No billed, unclaimed visits for this insurer in that period")
    with db.tx():
        cid = db.x("INSERT INTO care_claims(number,insurer,date_from,date_to,claimed,status,submitted) VALUES (?,?,?,?,?,?,?)",
                   (_next(db, "care_claims", "CL-", 4), insurer, clean_date(date_from), clean_date(date_to), sum(r["insurer_amt"] for r in rows),
                    "submitted", acc.today())).lastrowid
        db.conn.executemany("UPDATE care_visits SET claim_id=? WHERE id=?", [(cid, r["id"]) for r in rows])
    return cid


def settle_claim(db, cid, d, user):
    """The insurer's answer: what it paid and what it rejected (written off as a revenue reduction)."""
    c = db.one("SELECT * FROM care_claims WHERE id=?", (int(cid),))
    if not c:
        raise LookupError("Claim not found")
    if c["status"] == "settled":
        raise ValidationError("This claim is already settled")
    paid = to_minor(acc.num(d.get("paid") or 0, "paid"))
    rejected = to_minor(acc.num(d.get("rejected") or 0, "rejected"))
    if paid + rejected != c["claimed"]:
        raise ValidationError(f"Paid + rejected must equal the claimed {to_major(c['claimed']):,.2f}")
    jl = []
    if paid:
        jl.append({"account": acc.money_account(db, d.get("method") or "Bank Transfer"), "debit": to_major(paid)})
    if rejected:
        jl.append({"account": REJECT, "debit": to_major(rejected)})
    jl.append({"account": INS, "credit": to_major(c["claimed"]), "party": c["insurer"]})
    with db.tx():
        eid = acc._insert_entry(db, date=clean_date(d.get("date")), kind="Insurance Claim", party=c["insurer"], ref=c["number"],
                                memo=f"{c['number']} — {c['insurer']} settled" + (f" ({to_major(rejected):,.2f} rejected)" if rejected else ""),
                                lines=jl, user=user)
        db.x("UPDATE care_claims SET paid=?, rejected=?, status='settled', settled=?, entry_id=?, notes=? WHERE id=?",
             (paid, rejected, clean_date(d.get("date")), eid, text(d.get("notes"), maxlen=1000), c["id"]))


def claims(db, insurer=None, status=None):
    w, p = ["1=1"], []
    if insurer:
        w.append("insurer=?")
        p.append(insurer)
    if status:
        w.append("status=?")
        p.append(status)
    out = []
    for r in db.q(f"SELECT c.*, (SELECT COUNT(*) FROM care_visits v WHERE v.claim_id=c.id) visits FROM care_claims c WHERE {' AND '.join(w)} ORDER BY id DESC", p):
        r = dict(r)
        for k in ("claimed", "paid", "rejected"):
            r[k] = to_major(r[k])
        out.append(r)
    return out


def unclaimed(db):
    """Per insurer: billed visits not yet claimed (count, amount, oldest)."""
    return [{**r, "amount": to_major(r["amount"])} for r in db.q(
        """SELECT pt.insurer, COUNT(*) visits, SUM(v.insurer_amt) amount, MIN(v.date) oldest FROM care_visits v
           JOIN care_patients pt ON pt.id=v.patient_id WHERE v.entry_id IS NOT NULL AND v.claim_id IS NULL AND v.insurer_amt>0
           GROUP BY pt.insurer ORDER BY amount DESC""")]


# ------------------------------------------------------------------ reports
def report(db, date_from=None, date_to=None, bu_id=None):
    w, p = ["v.date BETWEEN ? AND ?"], [clean_date(date_from or "2000-01-01"), clean_date(date_to or acc.today())]
    if bu_id:
        w.append("v.bu_id=?")
        p.append(int(bu_id))
    where = " AND ".join(w)
    counts = {r["status"]: r["n"] for r in db.q(f"SELECT status, COUNT(*) n FROM care_visits v WHERE {where} GROUP BY status", p)}
    booked_total = sum(counts.values())
    by_service = db.q(f"""SELECT COALESCE(s.name, l.description) name, s.category, SUM(l.qty) qty, SUM(l.patient_amt+l.insurer_amt) revenue,
                                 SUM(l.provider_amt) provider FROM care_visit_lines l JOIN care_visits v ON v.id=l.visit_id
                          LEFT JOIN care_services s ON s.id=l.service_id WHERE {where} AND v.entry_id IS NOT NULL GROUP BY 1, 2 ORDER BY revenue DESC""", p)
    by_provider = db.q(f"""SELECT v.provider name, COUNT(*) visits, SUM(v.patient_amt+v.insurer_amt) revenue, SUM(v.provider_amt) earned,
                                  SUM(v.hours) hours, SUM(v.place='home') home FROM care_visits v WHERE {where} AND v.entry_id IS NOT NULL
                           GROUP BY v.provider ORDER BY revenue DESC""", p)
    by_zone = db.q(f"""SELECT COALESCE(NULLIF(v.zone,''), '—') name, COUNT(*) visits, SUM(v.travel_fee) travel FROM care_visits v
                       WHERE {where} AND v.place='home' AND v.status='done' GROUP BY 1 ORDER BY visits DESC""", p)
    by_payer = db.q(f"""SELECT CASE WHEN pt.payer='insurer' THEN pt.insurer ELSE 'Self-pay' END name, COUNT(*) visits,
                               SUM(v.patient_amt) patient, SUM(v.insurer_amt) insurer FROM care_visits v JOIN care_patients pt ON pt.id=v.patient_id
                        WHERE {where} AND v.entry_id IS NOT NULL GROUP BY 1 ORDER BY (patient + insurer) DESC""", p)
    money = lambda rows, *ks: [{**r, **{k: to_major(r[k] or 0) for k in ks}} for r in rows]   # noqa: E731
    fees_due = acc.party_balances(db, accounts=[FEES_DUE]) if db.one("SELECT 1 FROM accounts WHERE code=?", (FEES_DUE,)) else []
    ins_due = acc.party_balances(db, accounts=[INS]) if db.one("SELECT 1 FROM accounts WHERE code=?", (INS,)) else []
    for r in ins_due:          # an asset: show what they owe as a positive figure
        r["balance"] = -r["balance"]
    return {"counts": counts, "total": booked_total,
            "no_show_rate": round(100 * counts.get("no_show", 0) / booked_total, 1) if booked_total else 0,
            "by_service": money(by_service, "revenue", "provider"), "by_provider": money(by_provider, "revenue", "earned"),
            "by_zone": money(by_zone, "travel"), "by_payer": money(by_payer, "patient", "insurer"),
            "fees_due": fees_due, "insurers_due": ins_due, "unclaimed": unclaimed(db),
            "packages_held": round(sum(k["held"] for k in packages(db, status="active")), 2)}


# ------------------------------------------------------------------ backup / restore
TABLES = ("care_patients", "care_services", "care_packages", "care_claims", "care_visits", "care_visit_lines")


def export_rows(db):
    return {t: db.q(f"SELECT * FROM {t} ORDER BY id") for t in TABLES}


def import_rows(db, data, mode="append"):
    if not isinstance(data, dict) or not any(data.get(t) for t in TABLES):
        return 0
    if mode != "replace" and db.one("SELECT 1 FROM care_patients"):
        return 0   # books that already have patients keep theirs
    n = 0
    for t in reversed(TABLES):
        db.conn.execute(f"DELETE FROM {t}")
    for t in TABLES:
        cols = [r["name"] for r in db.q(f"PRAGMA table_info({t})")]
        for row in data.get(t) or []:
            if isinstance(row, dict):
                keys = [k for k in cols if k in row]
                db.conn.execute(f"INSERT INTO {t}({','.join(keys)}) VALUES ({','.join('?' * len(keys))})", [row[k] for k in keys])
                n += 1
    return n
