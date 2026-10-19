"""Egyptian Tax Authority e-receipts (الإيصال الإلكتروني) — B2C sales at a point of sale.

Each receipt is built in the ETA e-receipt v1.2 structure (header / documentType / seller / buyer / itemData /
totals). Its UUID is the SHA-256 (hex) of the receipt's canonical serialization with the uuid left blank, and it
carries the previous receipt's UUID — the chain the authority checks — so receipts are issued strictly in order and
an issued receipt is never changed (a mistake is corrected with a return receipt referring to it).

Issuing a receipt posts the sale: Dr cash / bank (by payment method)  /  Cr revenue (+ Cr VAT output). A return posts
the reverse. Receipts can be exported (JSON for an integrator / the portal) or submitted directly from the desktop or
self-hosted edition using the POS's own credentials (Settings → E-invoicing → Point of sale). The POS secret and
pre-shared key stay on the server; they are never sent to the browser or exported.

Before going live, validate on the ETA pre-production system with your registered POS: field rules (activity code,
branch, item codes, buyer ID thresholds) are set by the authority and may change.
"""
import datetime as _dt
import hashlib
import json
import re

from . import accounting as acc
from . import einvoice
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

PAYMENT_METHODS = {"C": "Cash", "V": "Visa", "CC": "Cash with contractor", "VC": "Visa with contractor", "VO": "Vouchers",
                   "PR": "Promotion", "GC": "Gift card", "P": "Points", "O": "Others"}
BUYER_TYPES = ("P", "B", "F")


def _r(v, n=5):
    return round(float(v or 0), n)


def settings(db):
    return einvoice.settings(db)


def next_number(db):
    prefix = (db.setting("eta_receipt_prefix") or "R-")
    n = 1
    for r in db.q("SELECT number FROM ereceipts WHERE number LIKE ?", (prefix + "%",)):
        tail = r["number"][len(prefix):]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"{prefix}{n:06d}"


def _clean_lines(db, lines):
    out = []
    for i, ln in enumerate(lines or [], start=1):
        if not isinstance(ln, dict) or not text(ln.get("description")):
            continue
        qty = num(ln.get("qty", 1) or 1, f"line {i} quantity", allow_zero=False)
        price = num(ln.get("price"), f"line {i} price")
        disc = num(ln.get("discount") or 0, f"line {i} discount")
        if disc > qty * price:
            raise ValidationError(f"Line {i}: the discount is more than the line")
        vat = text(ln.get("vat_code")) or db.setting("eta_receipt_vat_code") or ""
        if vat:
            acc.tax_code(db, vat, "vat")
        out.append({"description": text(ln.get("description"), maxlen=500), "code": text(ln.get("code")), "code_type": text(ln.get("code_type")),
                    "internal_code": text(ln.get("internal_code")), "unit": text(ln.get("unit")) or "EA", "qty": qty, "price": price,
                    "discount": disc, "vat_code": vat})
    if not out:
        raise ValidationError("Add at least one item")
    return out


def totals(db, lines):
    sales = disc = net = 0.0
    tax = {}
    items = []
    for ln in lines:
        s = _r(ln["qty"] * ln["price"])
        n = _r(s - ln["discount"])
        t = None
        if ln["vat_code"]:
            tc = db.one("SELECT * FROM tax_codes WHERE code=?", (ln["vat_code"],))
            if tc:
                t = {"taxType": tc["eta_type"] or "T1", "subType": tc["eta_subtype"] or "V009", "rate": _r(tc["rate"] * 100, 2),
                     "amount": _r(n * tc["rate"])}
                tax[t["taxType"]] = _r(tax.get(t["taxType"], 0) + t["amount"])
        items.append({"sales": s, "net": n, "tax": t, "total": _r(n + (t["amount"] if t else 0))})
        sales += s
        disc += ln["discount"]
        net += n
    vat = _r(sum(tax.values()))
    return {"items": items, "sales": _r(sales), "discount": _r(disc), "net": _r(net), "vat": vat, "total": _r(net + vat), "taxes": tax}


def build(db, r, previous_uuid=""):
    """The receipt document (v1.2) and the problems that would stop it being accepted."""
    s = settings(db)
    lines = json.loads(r["lines"]) if isinstance(r["lines"], str) else r["lines"]
    t = totals(db, lines)
    problems = []
    rin = (s.get("company_tax_id") or "").strip()
    if not re.fullmatch(r"\d{9}", rin):
        problems.append("Company tax registration number (9 digits) is missing — Settings → Company")
    if not s.get("eta_activity_code"):
        problems.append("Taxpayer activity code is missing — Settings → E-invoicing")
    if not s.get("eta_pos_serial"):
        problems.append("The POS device serial number is missing — Settings → E-invoicing → Point of sale")
    if r["buyer_type"] == "B" and not re.fullmatch(r"\d{9}", r["buyer_id"] or ""):
        problems.append("A business buyer needs a 9-digit tax registration number")
    if r["kind"] == "return" and not r["ref_uuid"]:
        problems.append("A return must refer to the original receipt")
    item_data = []
    for ln, it in zip(lines, t["items"]):
        code = ln["code"] or s.get("eta_default_item_code") or ""
        if not code:
            problems.append(f"Item '{ln['description'][:40]}' has no ETA item code (or set a default in Settings → E-invoicing)")
        item_data.append({
            "internalCode": ln["internal_code"] or code, "description": ln["description"],
            "itemType": ln["code_type"] or ("GS1" if code.isdigit() else "EGS"), "itemCode": code,
            "unitType": einvoice.UNIT_CODES.get(ln["unit"].strip().lower(), ln["unit"] if ln["unit"].isupper() else s.get("eta_default_unit") or "EA"),
            "quantity": _r(ln["qty"]), "unitPrice": _r(ln["price"]), "netSale": it["net"], "totalSale": it["sales"], "total": it["total"],
            "commercialDiscountData": [{"amount": _r(ln["discount"]), "description": "Discount"}] if ln["discount"] else [],
            "itemDiscountData": [], "valueDifference": 0,
            "taxableItems": [it["tax"]] if it["tax"] else []})
    doc = {
        "header": {"dateTimeIssued": r["issued_at"], "receiptNumber": r["number"], "uuid": "", "previousUUID": previous_uuid,
                   "referenceOldUUID": "", "currency": "EGP", "exchangeRate": 0, "sOrderNameCode": "", "orderdeliveryMode": "",
                   "grossWeight": 0, "netWeight": 0},
        "documentType": {"receiptType": "R" if r["kind"] == "return" else "S", "typeVersion": "1.2"},
        "seller": {"rin": rin, "companyTradeName": s.get("company_name", ""), "branchCode": s.get("eta_pos_branch_code") or "0",
                   "branchAddress": {"country": "EG", "governate": s.get("eta_governate", ""), "regionCity": s.get("eta_region", ""),
                                     "street": s.get("eta_street", ""), "buildingNumber": s.get("eta_building", ""),
                                     "postalCode": s.get("eta_postal_code", ""), "floor": "", "room": "", "landmark": "",
                                     "additionalInformation": ""},
                   "deviceSerialNumber": s.get("eta_pos_serial", ""), "syndicateLicenseNumber": "", "activityCode": s.get("eta_activity_code", "")},
        "buyer": {"type": r["buyer_type"], "id": r["buyer_id"] or "", "name": r["buyer_name"] or "", "mobileNumber": r["buyer_mobile"] or "",
                  "paymentNumber": ""},
        "itemData": item_data,
        "totalSales": t["sales"], "totalCommercialDiscount": t["discount"], "totalItemsDiscount": 0,
        "extraReceiptDiscountData": [], "netAmount": t["net"], "feesAmount": 0, "totalAmount": t["total"],
        "taxTotals": [{"taxType": k, "amount": v} for k, v in t["taxes"].items()],
        "paymentMethod": r["payment_method"], "adjustment": 0,
    }
    if r["kind"] == "return":
        doc["header"]["referenceUUID"] = r["ref_uuid"]
    if r["issued_at"][:10] > acc.today():
        problems.append("The issue date cannot be in the future")
    return doc, sorted(set(problems)), t


def receipt_uuid(doc):
    d = json.loads(json.dumps(doc))
    d["header"]["uuid"] = ""
    return hashlib.sha256(einvoice.serialize(d).encode("utf-8")).hexdigest()


def save(db, d, rid=None):
    """A draft receipt (can be changed until issued)."""
    lines = _clean_lines(db, d.get("lines"))
    kind = "return" if d.get("kind") == "return" else "sale"
    ref_uuid = text(d.get("ref_uuid"))
    if kind == "return":
        src = db.one("SELECT * FROM ereceipts WHERE (uuid=? AND uuid<>'') OR number=?", (ref_uuid, text(d.get("ref_number"))))
        if not src or src["status"] == "draft":
            raise ValidationError("Pick the issued receipt this return refers to")
        ref_uuid = src["uuid"]
    btype = d.get("buyer_type") if d.get("buyer_type") in BUYER_TYPES else "P"
    method = d.get("payment_method") if d.get("payment_method") in PAYMENT_METHODS else "C"
    date = acc.clean_date(d.get("date") or (d.get("issued_at") or "")[:10])
    tm = text(d.get("time")) or _dt.datetime.utcnow().strftime("%H:%M:%S")
    if not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", tm):
        raise ValidationError("Time as HH:MM")
    issued = f"{date}T{tm if len(tm) == 8 else tm + ':00'}Z"
    account = d.get("account") or db.setting("eta_receipt_account") or ""
    account = acc.account_code(db, account, types=("Revenue",)) if account else ""
    vals = dict(issued_at=issued, kind=kind, ref_uuid=ref_uuid, buyer_type=btype, buyer_id=text(d.get("buyer_id")),
                buyer_name=text(d.get("buyer_name")), buyer_mobile=text(d.get("buyer_mobile")), payment_method=method,
                lines=json.dumps(lines, ensure_ascii=False), bu_id=acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), account=account)
    if rid:
        r = get(db, rid)
        if r["status"] != "draft":
            raise ValidationError("An issued receipt cannot be changed. Issue a return instead.")
        db.x(f"UPDATE ereceipts SET {', '.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [r["id"]])
        return r["id"]
    number = text(d.get("number")) or next_number(db)
    if db.one("SELECT 1 FROM ereceipts WHERE number=?", (number,)):
        raise ValidationError(f"Receipt {number} already exists")
    return db.x(f"INSERT INTO ereceipts(number, status, {', '.join(vals)}) VALUES (?, 'draft', {', '.join('?' * len(vals))})",
                [number] + list(vals.values())).lastrowid


def get(db, rid):
    r = db.one("SELECT e.*, b.code AS bu_code FROM ereceipts e LEFT JOIN business_units b ON b.id=e.bu_id WHERE e.id=? OR e.number=?",
               (int(rid) if str(rid).isdigit() else -1, str(rid)))
    if not r:
        raise ValidationError("Receipt not found")
    r["lines"] = json.loads(r["lines"] or "[]")
    t = totals(db, r["lines"])
    r.update(net=t["net"], vat=t["vat"], total=t["total"], discount=t["discount"])
    r["document"] = json.loads(r["document"]) if r["document"] else None
    return r


def _last_uuid(db):
    x = db.one("SELECT uuid FROM ereceipts WHERE uuid<>'' ORDER BY issued_at DESC, id DESC LIMIT 1")
    return x["uuid"] if x else ""


def issue(db, rid, user=""):
    """Fix the receipt: chain it to the previous one, compute its UUID and post the sale (or return)."""
    r = get(db, rid)
    if r["status"] != "draft":
        raise ValidationError("Already issued")
    last = db.one("SELECT issued_at FROM ereceipts WHERE uuid<>'' ORDER BY issued_at DESC, id DESC LIMIT 1")
    if last and last["issued_at"] > r["issued_at"]:
        raise ValidationError(f"Receipts are chained in date order; this one is dated before the last issued receipt ({last['issued_at']}).")
    prev = _last_uuid(db)
    raw = {**r, "lines": r["lines"]}
    doc, problems, t = build(db, raw, prev)
    blocking = [p for p in problems if "future" in p or "return must" in p or "business buyer" in p]
    if blocking:
        raise ValidationError("; ".join(blocking))
    doc["header"]["uuid"] = receipt_uuid(doc)
    revenue = r["account"] or db.acct("revenue")
    money = acc.money_account(db, "cash" if r["payment_method"] in ("C", "CC") else "bank")
    sign = -1 if r["kind"] == "return" else 1
    lines = [{"account": money, ("debit" if sign > 0 else "credit"): t["total"]},
             {"account": revenue, ("credit" if sign > 0 else "debit"): t["net"]}]
    if t["vat"]:
        lines.append({"account": db.acct("vat_output"), ("credit" if sign > 0 else "debit"): t["vat"]})
    with db.tx():
        eid = acc._insert_entry(db, date=r["issued_at"][:10], kind="E-Receipt", bu_id=r["bu_id"], party=r["buyer_name"] or "Walk-in customer",
                                ref=r["number"], user=user, memo=f"{'Return' if sign < 0 else 'Sale'} — e-receipt {r['number']}",
                                lines=[ln for ln in lines if any(ln.get(k) for k in ("debit", "credit"))],
                                meta={"uuid": doc["header"]["uuid"], "payment_method": r["payment_method"]})
        db.conn.execute("UPDATE ereceipts SET status='issued', uuid=?, previous_uuid=?, document=?, entry_id=?, error=? WHERE id=?",
                        (doc["header"]["uuid"], prev, json.dumps(doc, ensure_ascii=False), eid, "; ".join(problems), r["id"]))
    return {"uuid": doc["header"]["uuid"], "warnings": problems}


def delete(db, rid):
    r = get(db, rid)
    if r["status"] != "draft":
        raise ValidationError("An issued receipt is part of the chain and cannot be deleted. Issue a return instead.")
    db.x("DELETE FROM ereceipts WHERE id=?", (r["id"],))


def listing(db, status=None, date_from=None, date_to=None, q=None):
    w, p = ["1=1"], []
    if status:
        w.append("status=?")
        p.append(status)
    if date_from:
        w.append("substr(issued_at,1,10)>=?")
        p.append(date_from)
    if date_to:
        w.append("substr(issued_at,1,10)<=?")
        p.append(date_to)
    if q:
        w.append("(number LIKE ? OR buyer_name LIKE ? OR uuid LIKE ?)")
        p += [f"%{q}%"] * 3
    out = []
    for r in db.q(f"SELECT id FROM ereceipts WHERE {' AND '.join(w)} ORDER BY issued_at DESC, id DESC", p):
        x = get(db, r["id"])
        out.append({k: x[k] for k in ("id", "number", "issued_at", "kind", "buyer_type", "buyer_name", "payment_method", "status", "uuid",
                                       "previous_uuid", "submission_id", "error", "net", "vat", "total", "entry_id", "bu_code")})
    return out


def verify_chain(db):
    """Recompute every issued receipt's UUID and check each points at the one before it."""
    prev, bad = "", []
    for r in db.q("SELECT * FROM ereceipts WHERE uuid<>'' ORDER BY issued_at, id"):
        doc = json.loads(r["document"] or "{}")
        if not doc:
            bad.append({"number": r["number"], "problem": "document missing"})
        elif receipt_uuid(doc) != r["uuid"]:
            bad.append({"number": r["number"], "problem": "UUID does not match its content"})
        if r["previous_uuid"] != prev:
            bad.append({"number": r["number"], "problem": "does not point at the previous receipt"})
        prev = r["uuid"]
    return {"ok": not bad, "problems": bad}


def export_json(db, ids=None):
    rows = db.q("SELECT document FROM ereceipts WHERE uuid<>''" + (f" AND id IN ({','.join('?' * len(ids))})" if ids else "") +
                " ORDER BY issued_at, id", list(ids or []))
    return {"receipts": [json.loads(r["document"]) for r in rows if r["document"]]}


# ------------------------------------------------------------------ direct submission (desktop / self-hosted)
def _pos_token(db):
    s = settings(db)
    need = ("eta_pos_client_id", "eta_pos_client_secret", "eta_pos_serial")
    if not all(s.get(k) for k in need):
        raise ValidationError("Enter the POS client ID, secret and serial (Settings → E-invoicing → Point of sale)")
    id_base, _ = einvoice.ENDPOINTS.get(s.get("eta_env") or "preprod", einvoice.ENDPOINTS["preprod"])
    import urllib.parse
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": s["eta_pos_client_id"],
                                   "client_secret": s["eta_pos_client_secret"]}).encode()
    res = einvoice._http("POST", f"{id_base}/connect/token", body, {
        "Content-Type": "application/x-www-form-urlencoded", "posserial": s["eta_pos_serial"],
        "pososversion": s.get("eta_pos_os_version") or "", "posmodelframework": s.get("eta_pos_model") or "",
        "presharedkey": s.get("eta_pos_presharedkey") or ""})
    if not res.get("access_token"):
        raise ValidationError("E-receipt POS sign-in failed. Check the POS credentials and environment.")
    return res["access_token"]


def submit(db, ids=None, user=""):
    rows = db.q("SELECT * FROM ereceipts WHERE status IN ('issued','invalid')" + (f" AND id IN ({','.join('?' * len(ids))})" if ids else "") +
                " ORDER BY issued_at, id", list(ids or []))
    if not rows:
        raise ValidationError("No issued receipts waiting to be sent")
    token = _pos_token(db)
    res = einvoice._http("POST", f"{einvoice._api_base(db)}/api/v1/receiptsubmissions",
                         json.dumps({"receipts": [json.loads(r["document"]) for r in rows]}, ensure_ascii=False).encode("utf-8"),
                         {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    by_uuid = {r["uuid"]: r for r in rows}
    out = []
    for a in res.get("acceptedDocuments", []):
        r = by_uuid.get(a.get("uuid"))
        if r:
            db.x("UPDATE ereceipts SET status='submitted', submission_id=?, error='' WHERE id=?", (res.get("submissionId", ""), r["id"]))
            out.append({"number": r["number"], "status": "submitted"})
    for j in res.get("rejectedDocuments", []):
        r = by_uuid.get(j.get("uuid"))
        if r:
            err = json.dumps(j.get("error", {}), ensure_ascii=False)[:1500]
            db.x("UPDATE ereceipts SET status='rejected', error=? WHERE id=?", (err, r["id"]))
            out.append({"number": r["number"], "status": "rejected", "error": err})
    db.log(user, "ereceipt.submit", json.dumps(out, ensure_ascii=False)[:500])
    return {"submission_id": res.get("submissionId"), "results": out}


def alerts(db):
    return {"not_sent": db.one("SELECT COUNT(*) n FROM ereceipts WHERE status='issued'")["n"],
            "rejected": db.one("SELECT COUNT(*) n FROM ereceipts WHERE status IN ('rejected','invalid')")["n"],
            "drafts": db.one("SELECT COUNT(*) n FROM ereceipts WHERE status='draft'")["n"]}


def export_rows(db):
    return [{k: r[k] for k in ("number", "issued_at", "kind", "ref_uuid", "buyer_type", "buyer_id", "buyer_name", "buyer_mobile",
                                "payment_method", "lines", "uuid", "previous_uuid", "status", "submission_id", "error", "document", "account")}
            | {"bu_code": r["bu_code"] or ""}
            for r in db.q("SELECT e.*, b.code AS bu_code FROM ereceipts e LEFT JOIN business_units b ON b.id=e.bu_id ORDER BY e.issued_at, e.id")]


def import_rows(db, rows, counts):
    """JSON restore exactly as issued (UUIDs and chain kept; the sale entries come back with the journal)."""
    n = 0
    for r in rows or []:
        if db.one("SELECT 1 FROM ereceipts WHERE number=?", (r.get("number"),)):
            continue
        bu = db.one("SELECT id FROM business_units WHERE code=?", (r.get("bu_code") or "",))
        ent = db.one("SELECT id FROM journal_entries WHERE kind='E-Receipt' AND ref=? AND status='posted' ORDER BY id DESC LIMIT 1",
                     (r.get("number"),))
        db.x("""INSERT INTO ereceipts(number,issued_at,kind,ref_uuid,buyer_type,buyer_id,buyer_name,buyer_mobile,payment_method,lines,uuid,
                previous_uuid,status,submission_id,error,document,account,bu_id,entry_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             tuple(r.get(k) or {"kind": "sale", "buyer_type": "P", "payment_method": "C", "status": "draft", "lines": "[]"}.get(k, "") for k in ("number", "issued_at", "kind", "ref_uuid", "buyer_type", "buyer_id", "buyer_name", "buyer_mobile",
                                            "payment_method", "lines", "uuid", "previous_uuid", "status", "submission_id", "error", "document",
                                            "account")) + (bu["id"] if bu else None, ent["id"] if ent else None))
        n += 1
    if n:
        counts["E-receipts"] = n
