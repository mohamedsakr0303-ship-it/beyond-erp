"""Contracts with clients and subcontractors, variation orders, and subcontractors' certificates (extracts).

Client contract: what the client agreed to pay for a project (business unit). Approved variation orders change its
value (and its end date by the days granted); the project's contract value follows the revised value. Progress
billing stays on the Sales invoices / extracts screen; this shows billed against the revised value.

Subcontract: work given to a subcontractor at agreed rates (its lines), with retention, an advance and the advance's
recovery %, and the VAT / withholding tax that applies. The subcontractor is paid through CERTIFICATES (مستخلصات
مقاولي الباطن), each giving the quantity executed TO DATE on every line; the certificate's value is the cumulative
value less the previous certificate's, and posting it books:

    Dr subcontractor costs (the project)      this period's work (less other deductions)
    Dr VAT recoverable                        VAT on it, if any
        Cr subcontractors payable (him)       what he is owed for it
        Cr retention payable (him)            retention % held back
        Cr supplier advances (him)            the advance recovered (up to what is still outstanding)
        Cr withholding tax payable            tax withheld, if any

A certificate goes draft → approved → posted. Advances, payments and the retention release are booked from the
contract (reference = the contract code), so the contract's statement shows certified, retained, advanced, recovered,
paid and still owed.
"""
from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

KINDS = ("client", "sub")
STATUSES = ("draft", "active", "completed", "terminated")


def _role(db, role, fallback=None):
    try:
        return db.acct(role)
    except LookupError:
        if fallback:
            return fallback
        raise ValidationError(f"No account has the '{role}' role — set it in the chart of accounts")


def next_code(db, kind):
    prefix = "SC-" if kind == "sub" else "CC-"
    n = 1
    for r in db.q("SELECT code FROM contracts WHERE code LIKE ?", (prefix + "%",)):
        tail = r["code"][len(prefix):]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"{prefix}{n:04d}"


def _pct(v, field):
    x = num(v or 0, field)
    if x > 1:
        x /= 100
    if x < 0 or x > 1:
        raise ValidationError(f"{field} must be between 0 and 100 %")
    return x


def save(db, d, cid=None):
    kind = d.get("kind") if d.get("kind") in KINDS else "client"
    party = text(d.get("party"), "client" if kind == "client" else "subcontractor", required=True)
    bu = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=(kind == "sub"))
    start = acc.clean_date(d.get("start"), "start") if d.get("start") else ""
    finish = acc.clean_date(d.get("finish"), "finish") if d.get("finish") else ""
    if start and finish and finish < start:
        raise ValidationError("The contract cannot end before it starts")
    lines = []
    for i, ln in enumerate(d.get("lines") or [], start=1):
        if not isinstance(ln, dict) or not text(ln.get("description")):
            continue
        lines.append((text(ln.get("item_no")), text(ln.get("description"), maxlen=1000), text(ln.get("unit")),
                      num(ln.get("qty") or 0, f"line {i} quantity"), to_minor(num(ln.get("rate") or 0, f"line {i} rate"))))
    value = to_minor(num(d.get("value") or 0, "contract value"))
    if not value and lines:
        value = sum(int(round(q * r)) for _, _, _, q, r in lines)
    vals = dict(kind=kind, bu_id=bu, party=party, title=text(d.get("title"), maxlen=300), value=value,
                signed_on=acc.clean_date(d.get("signed_on"), "signed on") if d.get("signed_on") else "", start=start, finish=finish,
                retention_pct=_pct(d.get("retention_pct"), "retention"),
                retention_release=acc.clean_date(d.get("retention_release"), "retention release") if d.get("retention_release") else "",
                advance=to_minor(num(d.get("advance") or 0, "advance")), advance_recovery_pct=_pct(d.get("advance_recovery_pct"), "advance recovery"),
                payment_terms=text(d.get("payment_terms"), maxlen=2000), scope=text(d.get("scope"), maxlen=4000),
                vat_code=acc.tax_code(db, d.get("vat_code"), "vat")[0] if d.get("vat_code") else "",
                wht_code=acc.tax_code(db, d.get("wht_code"), "wht")[0] if d.get("wht_code") else "",
                status=d.get("status") if d.get("status") in STATUSES else "active", notes=text(d.get("notes"), maxlen=4000))
    with db.tx():
        if cid:
            old = get(db, cid)
            if lines and old["certificates"] and any(c["status"] == "posted" for c in old["certificates"]):
                keep = {(ln["description"]) for ln in old["lines"]}
                if not keep <= {x[1] for x in lines}:
                    raise ValidationError("Lines already certified cannot be removed — add new lines instead")
            db.conn.execute(f"UPDATE contracts SET {', '.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [old["id"]])
            cid = old["id"]
            if d.get("lines") is not None:
                existing = {ln["description"]: ln["id"] for ln in old["lines"]}
                seen = set()
                for i, (no, desc, unit, q, r) in enumerate(lines, start=1):
                    if desc in existing:
                        db.conn.execute("UPDATE contract_lines SET seq=?, item_no=?, unit=?, qty=?, rate=? WHERE id=?",
                                        (i, no, unit, q, r, existing[desc]))
                        seen.add(desc)
                    else:
                        db.conn.execute("INSERT INTO contract_lines(contract_id,seq,item_no,description,unit,qty,rate) VALUES (?,?,?,?,?,?,?)",
                                        (cid, i, no, desc, unit, q, r))
                for desc, lid in existing.items():
                    if desc not in seen and not db.one("SELECT 1 FROM sub_cert_lines WHERE line_id=?", (lid,)):
                        db.conn.execute("DELETE FROM contract_lines WHERE id=?", (lid,))
        else:
            code = text(d.get("code")) or next_code(db, kind)
            if db.one("SELECT 1 FROM contracts WHERE code=?", (code,)):
                raise ValidationError(f"Contract {code} already exists")
            cid = db.conn.execute(f"INSERT INTO contracts(code, {', '.join(vals)}) VALUES (?, {', '.join('?' * len(vals))})",
                                  [code] + list(vals.values())).lastrowid
            db.conn.executemany("INSERT INTO contract_lines(contract_id,seq,item_no,description,unit,qty,rate) VALUES (?,?,?,?,?,?,?)",
                                [(cid, i, *ln) for i, ln in enumerate(lines, start=1)])
        _sync_bu(db, cid)
    return cid


def _sync_bu(db, cid):
    """A client contract sets its project's contract value (the revised one, after approved variations)."""
    c = db.one("SELECT * FROM contracts WHERE id=?", (cid,))
    if c and c["kind"] == "client" and c["bu_id"] and c["status"] != "terminated":
        v = client_value(db, c["bu_id"])
        db.conn.execute("UPDATE business_units SET contract_value=? WHERE id=?", (to_minor(v["revised"]), c["bu_id"]))


def get(db, cid):
    c = db.one("SELECT c.*, b.code AS bu_code, b.name AS bu_name FROM contracts c LEFT JOIN business_units b ON b.id=c.bu_id "
               "WHERE c.id=? OR c.code=?", (int(cid) if str(cid).isdigit() else -1, str(cid)))
    if not c:
        raise ValidationError("Contract not found")
    c["lines"] = db.q("SELECT * FROM contract_lines WHERE contract_id=? ORDER BY seq, id", (c["id"],))
    for ln in c["lines"]:
        ln["rate"] = to_major(ln["rate"])
        ln["amount"] = round(ln["qty"] * ln["rate"], 2)
    c["variations"] = db.q("SELECT * FROM variation_orders WHERE contract_id=? ORDER BY date, id", (c["id"],))
    for v in c["variations"]:
        v["amount"] = to_major(v["amount"])
    approved = [v for v in c["variations"] if v["status"] == "approved"]
    c["value"] = to_major(c["value"])
    c["advance"] = to_major(c["advance"])
    c["variations_total"] = round(sum(v["amount"] for v in approved), 2)
    c["revised_value"] = round(c["value"] + c["variations_total"], 2)
    c["extension_days"] = sum(v["days"] for v in approved)
    if c["finish"] and c["extension_days"]:
        import datetime as _dt
        c["revised_finish"] = (_dt.date.fromisoformat(c["finish"]) + _dt.timedelta(days=c["extension_days"])).isoformat()
    else:
        c["revised_finish"] = c["finish"]
    c["certificates"] = [_cert_row(db, c, x) for x in db.q("SELECT * FROM sub_certificates WHERE contract_id=? ORDER BY number", (c["id"],))]
    c["statement"] = statement(db, c)
    return c


# ------------------------------------------------------------------ variation orders
def save_vo(db, cid, d, vid=None):
    c = get(db, cid)
    desc = text(d.get("description"), "description", required=True, maxlen=2000)
    amount = to_minor(num(d.get("amount") or 0, "amount", allow_negative=True))
    days = int(num(d.get("days") or 0, "extension days", allow_negative=True))
    date = acc.clean_date(d.get("date"))
    with db.tx():
        if vid:
            v = db.one("SELECT * FROM variation_orders WHERE id=? AND contract_id=?", (int(vid), c["id"]))
            if not v:
                raise ValidationError("Variation order not found")
            if v["status"] != "pending":
                raise ValidationError("Only a pending variation can be changed")
            db.conn.execute("UPDATE variation_orders SET date=?, description=?, amount=?, days=?, notes=? WHERE id=?",
                            (date, desc, amount, days, text(d.get("notes"), maxlen=2000), v["id"]))
            return v["id"]
        code = text(d.get("code")) or f"VO-{len(c['variations']) + 1:02d}"
        return db.conn.execute("INSERT INTO variation_orders(contract_id,code,date,description,amount,days,status,notes) VALUES (?,?,?,?,?,?,?,?)",
                               (c["id"], code, date, desc, amount, days, "pending", text(d.get("notes"), maxlen=2000))).lastrowid


def decide_vo(db, vid, decision, user=""):
    if decision not in ("approved", "rejected", "pending"):
        raise ValidationError("Approve or reject the variation")
    v = db.one("SELECT * FROM variation_orders WHERE id=?", (int(vid),))
    if not v:
        raise ValidationError("Variation order not found")
    with db.tx():
        db.conn.execute("UPDATE variation_orders SET status=?, decided_by=?, decided_at=? WHERE id=?",
                        (decision, user if decision != "pending" else "", acc.now_iso() if decision != "pending" else "", v["id"]))
        _sync_bu(db, v["contract_id"])


def delete_vo(db, vid):
    v = db.one("SELECT * FROM variation_orders WHERE id=?", (int(vid),))
    if v and v["status"] == "approved":
        raise ValidationError("An approved variation cannot be deleted — reject it first")
    db.x("DELETE FROM variation_orders WHERE id=?", (int(vid),))


def client_value(db, bu_id):
    """The project's client contract value: original, approved variations, revised — and what was billed against it."""
    cs = db.q("SELECT id, value FROM contracts WHERE kind='client' AND bu_id=? AND status<>'terminated'", (int(bu_id),))
    original = sum(c["value"] for c in cs)
    vos = db.one(f"SELECT COALESCE(SUM(amount),0) n FROM variation_orders WHERE status='approved' AND contract_id IN "
                 f"({','.join(str(c['id']) for c in cs) or '0'})")["n"]
    return {"contracts": len(cs), "original": to_major(original), "variations": to_major(vos), "revised": to_major(original + vos)}


# ------------------------------------------------------------------ subcontractor certificates
def _cert_lines(db, cert_id):
    return db.q("SELECT * FROM sub_cert_lines WHERE cert_id=? ORDER BY id", (cert_id,))


def _prev_qty(db, contract_id, number):
    """Cumulative quantity per line on the certificate before this one (posted or approved)."""
    prev = db.one("SELECT id FROM sub_certificates WHERE contract_id=? AND number<? AND status IN ('approved','posted') "
                  "ORDER BY number DESC LIMIT 1", (contract_id, number))
    out = {}
    if prev:
        for ln in _cert_lines(db, prev["id"]):
            out[ln["line_id"] or ln["description"]] = (ln["qty_to_date"], ln["rate"])
    return out


def _cert_row(db, c, x):
    lines = _cert_lines(db, x["id"])
    prev = _prev_qty(db, c["id"], x["number"])
    gross_to_date = prev_gross = 0
    for ln in lines:
        p_qty, _ = prev.get(ln["line_id"] or ln["description"], (0, ln["rate"]))
        ln["qty_prev"] = p_qty
        ln["qty_this"] = round(ln["qty_to_date"] - p_qty, 6)
        ln["value_to_date"] = int(round(ln["qty_to_date"] * ln["rate"]))
        ln["value_this"] = int(round(ln["qty_this"] * ln["rate"]))
        gross_to_date += ln["value_to_date"]
        prev_gross += int(round(p_qty * ln["rate"]))
        ln["rate"] = to_major(ln["rate"])
        ln["value_to_date"], ln["value_this"] = to_major(ln["value_to_date"]), to_major(ln["value_this"])
    this = gross_to_date - prev_gross
    m = _money(db, c, x, this)
    return {**x, "lines": lines, "gross_to_date": to_major(gross_to_date), "previous": to_major(prev_gross),
            "this_period": to_major(this), **{k: to_major(v) for k, v in m.items()}}


def _money(db, c, x, this):
    """The certificate's split (minor units): retention, advance recovered, deductions, VAT, WHT, net payable."""
    retention = int(round(this * (c["retention_pct"] or 0)))
    outstanding = advance_outstanding(db, c["code"], c["party"], before=x["number"])
    recover = max(0, min(int(round(this * (c["advance_recovery_pct"] or 0))), outstanding))
    deductions = x["deductions"] or 0
    base = this - deductions
    vat = int(round(base * acc.tax_code(db, c["vat_code"], "vat")[1])) if c["vat_code"] else 0
    wht = int(round(base * acc.tax_code(db, c["wht_code"], "wht")[1])) if c["wht_code"] else 0
    net = base + vat - retention - recover - wht
    return {"retention": retention, "advance_recovered": recover, "deductions_amt": deductions, "vat": vat, "wht": wht, "net_payable": net}


def advance_outstanding(db, code, party, before=None):
    """Advance paid to the subcontractor on this contract less what certificates recovered (minor units);
    with `before`, only certificates numbered below it count."""
    try:
        acct = db.acct("supplier_advances")
    except LookupError:
        return 0
    n = 0
    for r in db.q(f"""SELECT e.ref, SUM(l.debit-l.credit) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                      WHERE {acc.LIVE} AND l.account=? AND {acc.PARTY_IS} AND (e.ref=? OR e.ref GLOB ?) GROUP BY e.ref""",
                  (acct, party, code, acc.ref_prefix(f"{code}/"))):
        tail = r["ref"][len(code) + 1:]
        if tail.startswith("C") and tail[1:].isdigit() and before is not None and int(tail[1:]) >= before:
            continue
        n += r["n"] or 0
    return max(n, 0)


def save_certificate(db, cid, d, cert_id=None):
    c = get(db, cid)
    if c["kind"] != "sub":
        raise ValidationError("Certificates are for subcontracts — bill the client from Sales → Invoices / extracts")
    by_line = {ln["id"]: ln for ln in c["lines"]}
    rows = []
    for i, ln in enumerate(d.get("lines") or [], start=1):
        if not isinstance(ln, dict):
            continue
        q = num(ln.get("qty_to_date") or 0, f"line {i} quantity to date")
        lid = int(ln["line_id"]) if ln.get("line_id") else None
        if lid and lid not in by_line:
            raise ValidationError(f"Line {i} is not on this contract")
        base = by_line.get(lid)
        desc = base["description"] if base else text(ln.get("description"), f"line {i} description", required=True)
        rate = to_minor(base["rate"]) if base else to_minor(num(ln.get("rate") or 0, f"line {i} rate"))
        rows.append((lid, desc, base["unit"] if base else text(ln.get("unit")), rate, q))
    if not rows:
        raise ValidationError("Enter the quantity executed to date on at least one line")
    date = acc.clean_date(d.get("date"))
    with db.tx():
        if cert_id:
            x = db.one("SELECT * FROM sub_certificates WHERE id=? AND contract_id=?", (int(cert_id), c["id"]))
            if not x:
                raise ValidationError("Certificate not found")
            if x["status"] != "draft":
                raise ValidationError("Only a draft certificate can be changed")
            db.conn.execute("UPDATE sub_certificates SET date=?, period_to=?, deductions=?, notes=? WHERE id=?",
                            (date, acc.clean_date(d.get("period_to"), "period to") if d.get("period_to") else "",
                             to_minor(num(d.get("deductions") or 0, "deductions")), text(d.get("notes"), maxlen=2000), x["id"]))
            db.conn.execute("DELETE FROM sub_cert_lines WHERE cert_id=?", (x["id"],))
            xid = x["id"]
        else:
            if any(x["status"] == "draft" for x in c["certificates"]):
                raise ValidationError("Finish (approve or delete) the draft certificate first")
            number = (max([x["number"] for x in c["certificates"]] or [0])) + 1
            xid = db.conn.execute("INSERT INTO sub_certificates(contract_id,number,date,period_to,deductions,status,notes) VALUES (?,?,?,?,?,?,?)",
                                  (c["id"], number, date, acc.clean_date(d.get("period_to"), "period to") if d.get("period_to") else "",
                                   to_minor(num(d.get("deductions") or 0, "deductions")), "draft", text(d.get("notes"), maxlen=2000))).lastrowid
        db.conn.executemany("INSERT INTO sub_cert_lines(cert_id,line_id,description,unit,rate,qty_to_date) VALUES (?,?,?,?,?,?)",
                            [(xid, *r) for r in rows])
        x = _cert_row(db, c, db.one("SELECT * FROM sub_certificates WHERE id=?", (xid,)))
        if x["this_period"] < 0:
            raise ValidationError("Quantities to date are below the previous certificate — enter cumulative quantities")
    return xid


def approve_certificate(db, cert_id, user=""):
    x = db.one("SELECT * FROM sub_certificates WHERE id=?", (int(cert_id),))
    if not x or x["status"] != "draft":
        raise ValidationError("Only a draft certificate can be approved")
    db.x("UPDATE sub_certificates SET status='approved', approved_by=?, approved_at=? WHERE id=?", (user, acc.now_iso(), x["id"]))


def post_certificate(db, cert_id, user=""):
    x = db.one("SELECT * FROM sub_certificates WHERE id=?", (int(cert_id),))
    if not x or x["status"] != "approved":
        raise ValidationError("Approve the certificate before posting it")
    c = get(db, x["contract_id"])
    row = next(r for r in c["certificates"] if r["id"] == x["id"])
    this = to_minor(row["this_period"])
    m = _money(db, c, x, this)
    base = this - m["deductions_amt"]
    if base <= 0:
        raise ValidationError("Nothing to certify this period")
    party = c["party"]
    lines = [{"account": _role(db, "subcontract_cost", db.acct("materials")), "debit": to_major(base)}]
    if m["vat"]:
        lines.append({"account": db.acct("vat_input"), "debit": to_major(m["vat"])})
    lines.append({"account": _role(db, "sub_payable", db.acct("ap")), "credit": to_major(m["net_payable"]), "party": party})
    if m["retention"]:
        lines.append({"account": _role(db, "retention_payable"), "credit": to_major(m["retention"]), "party": party})
    if m["advance_recovered"]:
        lines.append({"account": _role(db, "supplier_advances"), "credit": to_major(m["advance_recovered"]), "party": party})
    if m["wht"]:
        lines.append({"account": db.acct("wht_payable"), "credit": to_major(m["wht"]), "party": party})
    with db.tx():
        eid = acc._insert_entry(db, date=x["date"], kind="Subcontractor Certificate", bu_id=c["bu_id"], party=party,
                                ref=f"{c['code']}/C{x['number']}", user=user,
                                memo=f"Certificate {x['number']} — {party} ({c['code']}{' · ' + c['title'] if c['title'] else ''})",
                                lines=[ln for ln in lines if (ln.get("debit") or ln.get("credit"))],
                                meta={"contract": c["code"], "certificate": x["number"], **{k: to_major(v) for k, v in m.items()}})
        db.conn.execute("UPDATE sub_certificates SET status='posted', entry_id=? WHERE id=?", (eid, x["id"]))
    return eid


def reopen_certificate(db, cert_id, user=""):
    """Back to draft: a posted certificate's entry is voided (kept in the audit trail). Only the latest one."""
    x = db.one("SELECT * FROM sub_certificates WHERE id=?", (int(cert_id),))
    if not x:
        raise ValidationError("Certificate not found")
    if db.one("SELECT 1 FROM sub_certificates WHERE contract_id=? AND number>?", (x["contract_id"], x["number"])):
        raise ValidationError("Only the latest certificate can be reopened")
    if x["status"] == "posted":
        c = get(db, x["contract_id"])
        row = next(r for r in c["certificates"] if r["id"] == x["id"])
        st = c["statement"]
        if st["retention_held"] + 0.005 < row["retention"] or st["payable"] + 0.005 < row["net_payable"]:
            raise ValidationError("Part of this certificate was already paid or its retention released — void that first")
    with db.tx():
        if x["entry_id"] and db.one("SELECT 1 FROM journal_entries WHERE id=? AND status='posted'", (x["entry_id"],)):
            acc.void_entry(db, x["entry_id"], "Certificate reopened", user)
        db.conn.execute("UPDATE sub_certificates SET status='draft', entry_id=NULL, approved_by='', approved_at='' WHERE id=?", (x["id"],))


def delete_certificate(db, cert_id):
    x = db.one("SELECT * FROM sub_certificates WHERE id=?", (int(cert_id),))
    if x and x["status"] != "draft":
        raise ValidationError("Only a draft certificate can be deleted — reopen it first")
    db.x("DELETE FROM sub_certificates WHERE id=?", (int(cert_id),))


# ------------------------------------------------------------------ money: advance, payments, retention release
def pay(db, cid, d, user=""):
    """kind: advance (paid before work, recovered from certificates) | payment (against what is owed) | release
    (retention handed back — moves it to payable, then pay it)."""
    c = get(db, cid)
    if c["kind"] != "sub":
        raise ValidationError("Payments here are for subcontracts; client receipts go through Sales → Receipts")
    kind = d.get("kind") or "payment"
    amount = num(d.get("amount"), "amount", allow_zero=False)
    date = acc.clean_date(d.get("date"))
    party = c["party"]
    if kind == "advance":
        money = acc.money_account(db, d.get("method"), d.get("paid_from"))
        return acc.post_entry(db, date=date, kind="Payment Voucher", bu_id=c["bu_id"], party=party, ref=f"{c['code']}/ADV",
                              memo=f"Advance to {party} ({c['code']})", user=user,
                              lines=[{"account": _role(db, "supplier_advances"), "debit": amount, "party": party},
                                     {"account": money, "credit": amount}])
    if kind == "release":
        held = to_minor(c["statement"]["retention_held"])
        if to_minor(amount) > held:
            raise ValidationError(f"Only {to_major(held):,.2f} retention is held")
        return acc.post_entry(db, date=date, kind="Journal Voucher", bu_id=c["bu_id"], party=party, ref=f"{c['code']}/RET",
                              memo=f"Retention released to {party} ({c['code']})", user=user,
                              lines=[{"account": _role(db, "retention_payable"), "debit": amount, "party": party},
                                     {"account": _role(db, "sub_payable", db.acct("ap")), "credit": amount, "party": party}])
    if kind == "payment":
        return acc.record_payment(db, {"date": date, "against": _role(db, "sub_payable", db.acct("ap")), "party": party,
                                       "amount": amount, "method": d.get("method"), "paid_from": d.get("paid_from"),
                                       "bu_id": c["bu_id"], "ref": f"{c['code']}/PAY",
                                       "memo": text(d.get("memo")) or f"Payment to {party} ({c['code']})",
                                       "allow_overpay": d.get("allow_overpay")}, user)
    raise ValidationError("Unknown payment type")


def statement(db, c):
    code, party = c["code"], c["party"]

    def bal(role, sign=-1, refs=None):
        try:
            a = db.acct(role)
        except LookupError:
            return 0
        r = db.one(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                       WHERE {acc.LIVE} AND l.account=? AND {acc.PARTY_IS} AND (e.ref=? OR e.ref GLOB ?)""", (a, party, code, acc.ref_prefix(f"{code}/")))
        return sign * (r["n"] or 0)
    certified = sum(to_minor(x["this_period"]) for x in c.get("certificates") or [] if x["status"] == "posted") \
        if isinstance(c.get("certificates"), list) else 0
    paid = db.one(f"""SELECT COALESCE(SUM(l.debit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                      WHERE {acc.LIVE} AND e.kind='Payment Voucher' AND {acc.PARTY_IS} AND e.ref=?""", (party, f"{code}/PAY"))["n"]
    advance_paid = db.one(f"""SELECT COALESCE(SUM(l.debit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                              WHERE {acc.LIVE} AND {acc.PARTY_IS} AND e.ref=? AND l.debit>0""", (party, f"{code}/ADV"))["n"]
    return {"certified": to_major(certified), "retention_held": to_major(bal("retention_payable")),
            "advance_paid": to_major(advance_paid), "advance_outstanding": to_major(advance_outstanding(db, code, party)),
            "paid": to_major(paid), "payable": to_major(bal("sub_payable"))}


# ------------------------------------------------------------------ registers & alerts
def listing(db, kind=None, bu_id=None, status=None, q=None):
    w, p = ["1=1"], []
    if kind in KINDS:
        w.append("c.kind=?")
        p.append(kind)
    if bu_id:
        w.append("c.bu_id=?")
        p.append(int(bu_id))
    if status:
        w.append("c.status=?")
        p.append(status)
    if q:
        w.append("(c.code LIKE ? OR c.party LIKE ? OR c.title LIKE ?)")
        p += [f"%{q}%"] * 3
    out = []
    today = acc.today()
    for r in db.q(f"SELECT c.id FROM contracts c WHERE {' AND '.join(w)} ORDER BY c.kind, c.code", p):
        c = get(db, r["id"])
        due_soon = bool(c["status"] == "active" and c["revised_finish"] and c["revised_finish"] <= _plus(today, 30))
        out.append({k: c[k] for k in ("id", "code", "kind", "party", "title", "bu_code", "bu_name", "status", "start", "finish",
                                       "revised_finish", "value", "variations_total", "revised_value", "retention_pct")}
                   | {"pending_vos": sum(1 for v in c["variations"] if v["status"] == "pending"),
                      "draft_certs": sum(1 for x in c["certificates"] if x["status"] in ("draft", "approved")),
                      "certified": c["statement"]["certified"], "payable": c["statement"]["payable"],
                      "retention_held": c["statement"]["retention_held"],
                      "expired": bool(c["status"] == "active" and c["revised_finish"] and c["revised_finish"] < today),
                      "ending_soon": due_soon})
    return out


def _plus(d, days):
    import datetime as _dt
    return (_dt.date.fromisoformat(d) + _dt.timedelta(days=days)).isoformat()


def alerts(db, today=None):
    today = today or acc.today()
    rows = listing(db)
    return {"ending_soon": [r for r in rows if r["ending_soon"] and not r["expired"]],
            "expired": [r for r in rows if r["expired"]],
            "pending_vos": sum(r["pending_vos"] for r in rows),
            "certs_to_approve": db.one("SELECT COUNT(*) n FROM sub_certificates WHERE status='draft'")["n"],
            "certs_to_post": db.one("SELECT COUNT(*) n FROM sub_certificates WHERE status='approved'")["n"],
            "retention_due": [r for r in rows if r["retention_held"] and
                              (db.one("SELECT retention_release FROM contracts WHERE id=?", (r["id"],))["retention_release"] or "9999") <= today]}


# ------------------------------------------------------------------ backup / restore
def export_rows(db):
    out = []
    for r in db.q("SELECT id FROM contracts ORDER BY id"):
        c = get(db, r["id"])
        out.append({k: c[k] for k in ("code", "kind", "bu_code", "party", "title", "value", "signed_on", "start", "finish", "retention_pct",
                                       "retention_release", "advance", "advance_recovery_pct", "payment_terms", "scope", "vat_code",
                                       "wht_code", "status", "notes")}
                   | {"lines": [{k: ln[k] for k in ("item_no", "description", "unit", "qty", "rate")} for ln in c["lines"]],
                      "variations": [{k: v[k] for k in ("code", "date", "description", "amount", "days", "status", "decided_by",
                                                        "decided_at", "notes")} for v in c["variations"]],
                      "certificates": [{"number": x["number"], "date": x["date"], "period_to": x["period_to"], "deductions": to_major(x["deductions"]),
                                        "status": x["status"], "approved_by": x["approved_by"], "approved_at": x["approved_at"], "notes": x["notes"],
                                        "lines": [{"description": ln["description"], "unit": ln["unit"], "rate": ln["rate"],
                                                   "qty_to_date": ln["qty_to_date"]} for ln in x["lines"]]}
                                       for x in c["certificates"]]})
    return out


def import_rows(db, rows, counts):
    """JSON restore: contracts with lines, variations and certificates; posted certificates are linked back to their
    restored entries (by reference) instead of being posted twice."""
    n = 0
    for c in rows or []:
        if db.one("SELECT 1 FROM contracts WHERE code=?", (c.get("code"),)):
            continue
        cid = save(db, c)
        for v in c.get("variations") or []:
            vid = save_vo(db, cid, v)
            db.conn.execute("UPDATE variation_orders SET code=?, status=?, decided_by=?, decided_at=? WHERE id=?",
                            (v.get("code") or "", v.get("status") or "pending", v.get("decided_by") or "", v.get("decided_at") or "", vid))
        import_certificates(db, cid, c["code"], c.get("certificates") or [])
        _sync_bu(db, cid)
        n += 1
    if n:
        counts["Contracts"] = n


def import_certificates(db, cid, code, certs):
    """Certificate records as exported; a posted one is linked back to its restored entry (by reference)."""
    lines = {ln["description"]: ln["id"] for ln in db.q("SELECT id, description FROM contract_lines WHERE contract_id=?", (cid,))}
    for x in sorted(certs or [], key=lambda x: x.get("number") or 0):
        xid = db.conn.execute("INSERT INTO sub_certificates(contract_id,number,date,period_to,deductions,status,approved_by,approved_at,notes) "
                              "VALUES (?,?,?,?,?,?,?,?,?)",
                              (cid, x.get("number"), x.get("date"), x.get("period_to") or "", to_minor(num(x.get("deductions") or 0)),
                               x.get("status") or "draft", x.get("approved_by") or "", x.get("approved_at") or "", x.get("notes") or "")).lastrowid
        for ln in x.get("lines") or []:
            db.conn.execute("INSERT INTO sub_cert_lines(cert_id,line_id,description,unit,rate,qty_to_date) VALUES (?,?,?,?,?,?)",
                            (xid, lines.get(ln.get("description")), ln.get("description"), ln.get("unit") or "",
                             to_minor(num(ln.get("rate") or 0)), ln.get("qty_to_date") or 0))
        if x.get("status") == "posted":
            e = db.one("SELECT id FROM journal_entries WHERE ref=? AND status='posted' ORDER BY id DESC LIMIT 1", (f"{code}/C{x.get('number')}",))
            db.conn.execute("UPDATE sub_certificates SET entry_id=? WHERE id=?", (e["id"] if e else None, xid))
