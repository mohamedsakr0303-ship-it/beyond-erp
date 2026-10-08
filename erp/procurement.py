"""Purchase requests (طلبات الشراء) and supplier quotation comparison — the step before a purchase order.

A site or store asks for what it needs (lines with a rough price); the request is submitted and approved; suppliers'
quotes are entered against its lines and compared side by side (lowest per line, best whole offer, delivery, VAT);
the buyer awards each line to a supplier and the purchase orders are raised from that in one go — one order per
supplier, through the same Purchase orders module, which then handles approval, receiving and posting. Nothing here
posts to the books; the request is a record of who asked, who approved and why that supplier won.
"""
from . import accounting as acc
from . import purchasing
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

STATUSES = ("draft", "submitted", "approved", "rejected", "ordered", "cancelled")


def next_code(db):
    n = 1
    for r in db.q("SELECT code FROM purchase_requests WHERE code LIKE 'PR-%'"):
        tail = r["code"][3:]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"PR-{n:04d}"


def save(db, d, user="", pr_id=None):
    bu_id = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"))
    wh = d.get("warehouse_id") or d.get("warehouse")
    wh_id = acc._warehouse(db, wh)["id"] if wh else None
    lines = []
    for i, ln in enumerate(d.get("lines") or [], start=1):
        if not isinstance(ln, dict) or not (text(ln.get("description")) or text(ln.get("item"))):
            continue
        kind = ln.get("kind") or ("stock" if wh_id and not bu_id else "cost" if bu_id else "expense")
        if kind not in purchasing.LINE_KINDS:
            raise ValidationError(f"Line {i}: type must be cost, expense or stock")
        if kind == "cost" and not bu_id:
            raise ValidationError(f"Line {i}: a project cost needs the request's business unit")
        if kind == "stock" and not wh_id:
            raise ValidationError(f"Line {i}: stock lines need a warehouse")
        lines.append(dict(kind=kind, description=text(ln.get("description")) or text(ln.get("item")), item=text(ln.get("item")),
                          account=text(ln.get("account")), qty=num(ln.get("qty", 1) or 1, f"line {i} quantity", allow_zero=False),
                          unit=text(ln.get("unit")), est_rate=to_minor(num(ln.get("est_rate") or ln.get("rate") or 0, f"line {i} price"))))
    if not lines:
        raise ValidationError("Add at least one line")
    vals = dict(date=acc.clean_date(d.get("date")), requested_by=text(d.get("requested_by")) or user, bu_id=bu_id, warehouse_id=wh_id,
                needed_by=acc.clean_date(d.get("needed_by"), "needed by") if d.get("needed_by") else "", notes=text(d.get("notes"), maxlen=2000))
    with db.tx():
        if pr_id:
            pr = get(db, pr_id)
            if pr["status"] not in ("draft", "submitted", "rejected"):
                raise ValidationError("An approved or ordered request cannot be changed")
            db.conn.execute(f"UPDATE purchase_requests SET {', '.join(k + '=?' for k in vals)}, status='draft', approved_by='', approved_at='' "
                            "WHERE id=?", list(vals.values()) + [pr["id"]])
            old = {ln["description"]: ln["id"] for ln in pr["lines"]}
            keep = set()
            for i, ln in enumerate(lines, start=1):
                if ln["description"] in old:
                    lid = old[ln["description"]]
                    db.conn.execute("UPDATE pr_lines SET seq=?, kind=?, item=?, account=?, qty=?, unit=?, est_rate=? WHERE id=?",
                                    (i, ln["kind"], ln["item"], ln["account"], ln["qty"], ln["unit"], ln["est_rate"], lid))
                    keep.add(lid)
                else:
                    db.conn.execute("INSERT INTO pr_lines(pr_id,seq,kind,description,item,account,qty,unit,est_rate) VALUES (?,?,?,?,?,?,?,?,?)",
                                    (pr["id"], i, *ln.values()))
            for lid in set(old.values()) - keep:
                db.conn.execute("DELETE FROM pr_lines WHERE id=?", (lid,))
            return pr["id"]
        code = text(d.get("code")) or next_code(db)
        if db.one("SELECT 1 FROM purchase_requests WHERE code=?", (code,)):
            raise ValidationError(f"Request {code} already exists")
        pid = db.conn.execute(f"INSERT INTO purchase_requests(code, {', '.join(vals)}, status) VALUES (?, {', '.join('?' * len(vals))}, 'draft')",
                              [code] + list(vals.values())).lastrowid
        for i, ln in enumerate(lines, start=1):
            db.conn.execute("INSERT INTO pr_lines(pr_id,seq,kind,description,item,account,qty,unit,est_rate) VALUES (?,?,?,?,?,?,?,?,?)",
                            (pid, i, *ln.values()))
        return pid


def get(db, pr_id):
    pr = db.one("SELECT p.*, u.code AS bu_code, u.name AS bu_name, w.name AS warehouse FROM purchase_requests p "
                "LEFT JOIN business_units u ON u.id=p.bu_id LEFT JOIN warehouses w ON w.id=p.warehouse_id "
                "WHERE p.id=? OR p.code=?", (int(pr_id) if str(pr_id).isdigit() else -1, str(pr_id)))
    if not pr:
        raise ValidationError("Purchase request not found")
    pr["lines"] = db.q("SELECT * FROM pr_lines WHERE pr_id=? ORDER BY seq, id", (pr["id"],))
    for ln in pr["lines"]:
        ln["est_rate"] = to_major(ln["est_rate"])
        ln["est_amount"] = round(ln["qty"] * ln["est_rate"], 2)
    pr["estimate"] = round(sum(ln["est_amount"] for ln in pr["lines"]), 2)
    pr["quotes"] = []
    for qt in db.q("SELECT * FROM pr_quotes WHERE pr_id=? ORDER BY id", (pr["id"],)):
        rates = {r["line_id"]: r for r in db.q("SELECT * FROM pr_quote_lines WHERE quote_id=?", (qt["id"],))}
        qt["rates"] = {str(lid): to_major(r["rate"]) for lid, r in rates.items()}
        qt["awarded"] = [str(lid) for lid, r in rates.items() if r["awarded"]]
        qt["covers"] = sum(1 for ln in pr["lines"] if ln["id"] in rates)
        qt["total"] = round(sum(ln["qty"] * to_major(rates[ln["id"]]["rate"]) for ln in pr["lines"] if ln["id"] in rates), 2)
        vat = acc.tax_code(db, qt["vat_code"], "vat")[1] if qt["vat_code"] else 0
        qt["total_vat"] = round(qt["total"] * (1 + vat), 2)
        pr["quotes"].append(qt)
    pr["comparison"] = compare(pr)
    return pr


def compare(pr):
    """Per line: each supplier's rate, the lowest, and who it's awarded to; plus the best whole offer."""
    rows = []
    for ln in pr["lines"]:
        offers = [(q["supplier"], q["rates"][str(ln["id"])], q["id"]) for q in pr["quotes"] if str(ln["id"]) in q["rates"]]
        low = min(offers, key=lambda o: o[1]) if offers else None
        awarded = next((q["id"] for q in pr["quotes"] if str(ln["id"]) in q["awarded"]), None)
        rows.append({"line_id": ln["id"], "description": ln["description"], "qty": ln["qty"], "unit": ln["unit"],
                     "est_rate": ln["est_rate"], "offers": {str(o[2]): o[1] for o in offers},
                     "lowest_quote": low[2] if low else None, "lowest_rate": low[1] if low else None, "awarded_quote": awarded})
    full = [q for q in pr["quotes"] if q["covers"] == len(pr["lines"])]
    best = min(full, key=lambda q: q["total_vat"]) if full else None
    split = round(sum(r["qty"] * r["lowest_rate"] for r in rows if r["lowest_rate"] is not None), 2)
    return {"lines": rows, "best_full_quote": best["id"] if best else None, "best_full_total": best["total"] if best else None,
            "lowest_split_total": split, "all_lines_quoted": all(r["offers"] for r in rows)}


def set_status(db, pr_id, status, user=""):
    pr = get(db, pr_id)
    allowed = {"submitted": ("draft", "rejected"), "approved": ("submitted", "draft"), "rejected": ("submitted",),
               "cancelled": ("draft", "submitted", "approved", "rejected"), "draft": ("submitted", "rejected", "cancelled")}
    if status not in allowed or pr["status"] not in allowed[status]:
        raise ValidationError(f"A {pr['status']} request cannot be marked {status}")
    if status in ("approved", "rejected"):
        db.x("UPDATE purchase_requests SET status=?, approved_by=?, approved_at=? WHERE id=?", (status, user, acc.now_iso(), pr["id"]))
    else:
        db.x("UPDATE purchase_requests SET status=? WHERE id=?", (status, pr["id"]))


def save_quote(db, pr_id, d, quote_id=None):
    pr = get(db, pr_id)
    if pr["status"] in ("ordered", "cancelled"):
        raise ValidationError("The request is closed")
    supplier = text(d.get("supplier"), "supplier", required=True)
    ids = {str(ln["id"]) for ln in pr["lines"]}
    rates = {}
    for k, v in (d.get("rates") or {}).items():
        if str(k) in ids and v not in (None, ""):
            rates[int(k)] = to_minor(num(v, "quoted price"))
    if not rates:
        raise ValidationError("Enter the supplier's price for at least one line")
    vals = dict(supplier=supplier, date=acc.clean_date(d.get("date")),
                valid_until=acc.clean_date(d.get("valid_until"), "valid until") if d.get("valid_until") else "",
                delivery_days=int(num(d.get("delivery_days") or 0, "delivery days")),
                vat_code=acc.tax_code(db, d.get("vat_code"), "vat")[0] if d.get("vat_code") else "",
                terms=text(d.get("terms"), maxlen=2000), notes=text(d.get("notes"), maxlen=2000))
    with db.tx():
        if quote_id:
            if not db.one("SELECT 1 FROM pr_quotes WHERE id=? AND pr_id=?", (int(quote_id), pr["id"])):
                raise ValidationError("Quote not found")
            db.conn.execute(f"UPDATE pr_quotes SET {', '.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [int(quote_id)])
            db.conn.execute("DELETE FROM pr_quote_lines WHERE quote_id=?", (int(quote_id),))
            qid = int(quote_id)
        else:
            qid = db.conn.execute(f"INSERT INTO pr_quotes(pr_id, {', '.join(vals)}) VALUES (?, {', '.join('?' * len(vals))})",
                                  [pr["id"]] + list(vals.values())).lastrowid
        db.conn.executemany("INSERT INTO pr_quote_lines(quote_id,line_id,rate) VALUES (?,?,?)", [(qid, lid, r) for lid, r in rates.items()])
    return qid


def delete_quote(db, quote_id):
    db.x("DELETE FROM pr_quotes WHERE id=?", (int(quote_id),))


def award(db, pr_id, d, user=""):
    """d: {"awards": {line_id: quote_id}} or {"strategy": "lowest" | "best_full"}. Raises one purchase order per
    supplier (draft, for the usual approval) and marks the request ordered."""
    pr = get(db, pr_id)
    if pr["status"] != "approved":
        raise ValidationError("Approve the request before ordering")
    cmp_ = pr["comparison"]
    awards = {}
    strategy = d.get("strategy")
    if strategy == "lowest":
        awards = {r["line_id"]: r["lowest_quote"] for r in cmp_["lines"] if r["lowest_quote"]}
    elif strategy == "best_full":
        if not cmp_["best_full_quote"]:
            raise ValidationError("No supplier quoted every line")
        awards = {ln["id"]: cmp_["best_full_quote"] for ln in pr["lines"]}
    else:
        awards = {int(k): int(v) for k, v in (d.get("awards") or {}).items() if v}
    quotes = {q["id"]: q for q in pr["quotes"]}
    for lid, qid in awards.items():
        if qid not in quotes or str(lid) not in quotes[qid]["rates"]:
            raise ValidationError("A line is awarded to a supplier who did not quote it")
    if not awards:
        raise ValidationError("Award at least one line")
    by_q = {}
    for ln in pr["lines"]:
        if ln["id"] in awards:
            by_q.setdefault(awards[ln["id"]], []).append(ln)
    codes = []
    with db.tx():
        db.conn.execute("UPDATE pr_quote_lines SET awarded=0 WHERE quote_id IN (SELECT id FROM pr_quotes WHERE pr_id=?)", (pr["id"],))
        for qid, lines in by_q.items():
            q = quotes[qid]
            po = purchasing.save(db, {"supplier": q["supplier"], "date": d.get("date") or acc.today(), "bu_id": pr["bu_id"],
                                      "warehouse_id": pr["warehouse_id"], "vat_code": q["vat_code"],
                                      "expected": pr["needed_by"] or "", "terms": q["terms"],
                                      "notes": f"From purchase request {pr['code']} (quote of {q['date']})",
                                      "lines": [{"kind": ln["kind"], "description": ln["description"], "item": ln["item"],
                                                 "account": ln["account"], "qty": ln["qty"], "unit": ln["unit"],
                                                 "rate": q["rates"][str(ln["id"])]} for ln in lines]}, user)
            codes.append(db.one("SELECT code FROM purchase_orders WHERE id=?", (po,))["code"])
            db.conn.executemany("UPDATE pr_quote_lines SET awarded=1 WHERE quote_id=? AND line_id=?", [(qid, ln["id"]) for ln in lines])
        db.conn.execute("UPDATE purchase_requests SET status='ordered', orders=? WHERE id=?", (", ".join(codes), pr["id"]))
    return codes


def listing(db, status=None, bu_id=None, q=None):
    w, p = ["1=1"], []
    if status:
        w.append("p.status=?")
        p.append(status)
    if bu_id:
        w.append("p.bu_id=?")
        p.append(int(bu_id))
    if q:
        w.append("(p.code LIKE ? OR p.requested_by LIKE ? OR p.notes LIKE ? OR EXISTS (SELECT 1 FROM pr_lines l WHERE l.pr_id=p.id AND l.description LIKE ?))")
        p += [f"%{q}%"] * 4
    out = []
    for r in db.q(f"SELECT p.id FROM purchase_requests p WHERE {' AND '.join(w)} ORDER BY p.date DESC, p.id DESC", p):
        pr = get(db, r["id"])
        out.append({k: pr[k] for k in ("id", "code", "date", "requested_by", "bu_code", "warehouse", "needed_by", "status",
                                        "approved_by", "orders", "estimate")}
                   | {"lines": len(pr["lines"]), "quotes": len(pr["quotes"]),
                      "best": pr["comparison"]["lowest_split_total"] if pr["quotes"] else None})
    return out


def alerts(db, today=None):
    today = today or acc.today()
    return {"to_approve": db.one("SELECT COUNT(*) n FROM purchase_requests WHERE status='submitted'")["n"],
            "to_order": db.one("SELECT COUNT(*) n FROM purchase_requests WHERE status='approved'")["n"],
            "late": db.one("SELECT COUNT(*) n FROM purchase_requests WHERE status IN ('submitted','approved') AND needed_by<>'' AND needed_by<?",
                           (today,))["n"]}


def export_rows(db):
    out = []
    for r in db.q("SELECT id FROM purchase_requests ORDER BY id"):
        pr = get(db, r["id"])
        names = {ln["id"]: ln["description"] for ln in pr["lines"]}
        out.append({k: pr[k] for k in ("code", "date", "requested_by", "bu_code", "warehouse", "needed_by", "status", "approved_by",
                                        "approved_at", "orders", "notes")}
                   | {"lines": [{k: ln[k] for k in ("kind", "description", "item", "account", "qty", "unit", "est_rate")} for ln in pr["lines"]],
                      "quotes": [{k: q[k] for k in ("supplier", "date", "valid_until", "delivery_days", "vat_code", "terms", "notes")}
                                 | {"rates": {names[int(k)]: v for k, v in q["rates"].items() if int(k) in names},
                                    "awarded": [names[int(k)] for k in q["awarded"] if int(k) in names]} for q in pr["quotes"]]})
    return out


def import_rows(db, rows, counts):
    n = 0
    for r in rows or []:
        if db.one("SELECT 1 FROM purchase_requests WHERE code=?", (r.get("code"),)):
            continue
        pid = save(db, {**r, "warehouse": r.get("warehouse") or None}, r.get("requested_by") or "")
        ids = {ln["description"]: ln["id"] for ln in db.q("SELECT id, description FROM pr_lines WHERE pr_id=?", (pid,))}
        for q in r.get("quotes") or []:
            qid = save_quote(db, pid, {**q, "rates": {ids[k]: v for k, v in (q.get("rates") or {}).items() if k in ids}})
            db.conn.executemany("UPDATE pr_quote_lines SET awarded=1 WHERE quote_id=? AND line_id=?",
                                [(qid, ids[k]) for k in q.get("awarded") or [] if k in ids])
        db.x("UPDATE purchase_requests SET status=?, approved_by=?, approved_at=?, orders=? WHERE id=?",
             (r.get("status") or "draft", r.get("approved_by") or "", r.get("approved_at") or "", r.get("orders") or "", pid))
        n += 1
    if n:
        counts["Purchase requests"] = n
