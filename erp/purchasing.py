"""Purchase orders: what was ordered from a supplier, approved, then received (fully or in parts).

An order books nothing; it is a commitment. Each receipt posts the usual voucher through the same engine as the
screens: a Direct Cost for a business unit, an Operating Expense, or a Goods Receipt into a warehouse. The voucher is
owed to the supplier (accounts payable) unless something is paid on the spot, and carries the order code as its
reference. Status: draft -> approved -> partial -> received; or closed (rest not needed) / cancelled."""
import json

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

STATUSES = ("draft", "approved", "partial", "received", "closed", "cancelled")
LINE_KINDS = ("cost", "expense", "stock")


def next_code(db):
    n = 1
    for r in db.q("SELECT code FROM purchase_orders WHERE code LIKE 'PO-%'"):
        tail = r["code"][3:]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"PO-{n:04d}"


def _clean_lines(db, lines, bu_id, warehouse_id):
    out = []
    for i, ln in enumerate(lines or [], start=1):
        if not isinstance(ln, dict) or not (text(ln.get("description")) or text(ln.get("item"))):
            continue
        kind = ln.get("kind") or ("cost" if bu_id else "expense")
        if kind not in LINE_KINDS:
            raise ValidationError(f"Line {i}: type must be cost, expense or stock")
        qty = num(ln.get("qty", 1) or 1, f"line {i} quantity", allow_zero=False)
        rate = num(ln.get("rate"), f"line {i} unit price")
        account, item = "", ""
        if kind == "cost":
            if not bu_id:
                raise ValidationError(f"Line {i}: a project cost needs the order's business unit")
            account = acc.account_code(db, ln.get("account") or db.acct("materials"), types=("COGS",), field="cost account")
        elif kind == "expense":
            account = acc.account_code(db, ln.get("account") or db.acct("other_overhead"), types=("Expense",), field="expense account")
        else:
            if not warehouse_id:
                raise ValidationError(f"Line {i}: stock lines need a receiving warehouse")
            item = text(ln.get("item") or ln.get("description"), f"line {i} item", required=True)
        out.append({"kind": kind, "description": text(ln.get("description")) or item, "item": item, "account": account,
                    "qty": qty, "unit": text(ln.get("unit")) or "", "rate": rate, "received": float(ln.get("received") or 0)})
    if not out:
        raise ValidationError("Add at least one line")
    return out


def save(db, d, user="", po_id=None):
    """Create or change an order. Only drafts (and approved orders not yet received from) can be changed."""
    supplier = text(d.get("supplier"), "supplier", required=True)
    bu_id = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"))
    wh = d.get("warehouse_id") or d.get("warehouse")
    wh_id = acc._warehouse(db, wh)["id"] if wh else None
    lines = _clean_lines(db, d.get("lines"), bu_id, wh_id)
    vat_code = acc.tax_code(db, d.get("vat_code"), "vat")[0] if d.get("vat_code") else ""
    wht_code = acc.tax_code(db, d.get("wht_code"), "wht")[0] if d.get("wht_code") else ""
    vals = dict(date=acc.clean_date(d.get("date")), supplier=supplier, bu_id=bu_id, warehouse_id=wh_id,
                expected=acc.clean_date(d.get("expected"), "expected date") if d.get("expected") else "",
                vat_code=vat_code, wht_code=wht_code, terms=text(d.get("terms"), maxlen=2000), notes=text(d.get("notes"), maxlen=2000))
    with db.tx():
        if po_id:
            po = get(db, po_id)
            if po["status"] not in ("draft", "approved") or any(ln["received"] for ln in po["lines"]):
                raise ValidationError("Only an order with no receipts can be changed. Close it and raise a new order.")
            db.conn.execute(f"UPDATE purchase_orders SET {', '.join(k + '=?' for k in vals)}, status='draft', approved_by='', "
                            "approved_at='' WHERE id=?", list(vals.values()) + [po["id"]])
            db.conn.execute("DELETE FROM po_lines WHERE po_id=?", (po["id"],))
            pid = po["id"]
        else:
            code = text(d.get("code")) or next_code(db)
            if db.one("SELECT 1 FROM purchase_orders WHERE code=?", (code,)):
                raise ValidationError(f"Order {code} already exists")
            pid = db.conn.execute(f"INSERT INTO purchase_orders(code, {', '.join(vals)}, status, created_by) VALUES "
                                  f"(?, {', '.join('?' * len(vals))}, 'draft', ?)", [code] + list(vals.values()) + [user]).lastrowid
        for i, ln in enumerate(lines, start=1):
            db.conn.execute("INSERT INTO po_lines(po_id,seq,kind,description,item,account,qty,unit,rate) VALUES (?,?,?,?,?,?,?,?,?)",
                            (pid, i, ln["kind"], ln["description"], ln["item"], ln["account"], ln["qty"], ln["unit"], to_minor(ln["rate"])))
    return pid


def get(db, po_id):
    po = db.one("SELECT p.*, u.code AS bu_code, u.name AS bu_name, w.name AS warehouse FROM purchase_orders p "
                "LEFT JOIN business_units u ON u.id=p.bu_id LEFT JOIN warehouses w ON w.id=p.warehouse_id "
                "WHERE p.id=? OR p.code=?", (int(po_id) if str(po_id).isdigit() else -1, str(po_id)))
    if not po:
        raise ValidationError("Purchase order not found")
    lines = db.q("SELECT * FROM po_lines WHERE po_id=? ORDER BY seq", (po["id"],))
    rec = {}
    for r in db.q("SELECT r.line_id, SUM(r.qty) q FROM po_receipts r JOIN journal_entries e ON e.id=r.entry_id "
                  "WHERE r.po_id=? AND e.status='posted' GROUP BY r.line_id", (po["id"],)):
        rec[r["line_id"]] = r["q"] or 0
    total = ordered_open = 0
    for ln in lines:
        ln["rate"] = to_major(ln["rate"])
        ln["received"] = round(rec.get(ln["id"], 0), 6)
        ln["left"] = round(max(ln["qty"] - ln["received"], 0), 6)
        ln["amount"] = round(ln["qty"] * ln["rate"], 2)
        total += ln["amount"]
        ordered_open += ln["left"] * ln["rate"]
    vat_rate = acc.tax_code(db, po["vat_code"], "vat")[1] if po["vat_code"] else 0
    po["lines"] = lines
    po["total"] = round(total, 2)
    po["vat"] = round(total * vat_rate, 2)
    po["grand_total"] = round(po["total"] + po["vat"], 2)
    po["open_value"] = round(ordered_open, 2) if po["status"] in ("approved", "partial") else 0
    po["received_value"] = round(sum(ln["received"] * ln["rate"] for ln in lines), 2)
    po["receipts"] = db.q("SELECT r.date, r.qty, r.entry_id, e.kind, e.status, l.description FROM po_receipts r "
                          "JOIN journal_entries e ON e.id=r.entry_id JOIN po_lines l ON l.id=r.line_id WHERE r.po_id=? "
                          "ORDER BY r.date, r.id", (po["id"],))
    return po


def _refresh_status(db, po_id):
    po = get(db, po_id)
    if po["status"] in ("draft", "closed", "cancelled"):
        return po["status"]
    got = any(ln["received"] for ln in po["lines"])
    done = all(ln["left"] <= 1e-9 for ln in po["lines"])
    st = "received" if done else ("partial" if got else "approved")
    db.conn.execute("UPDATE purchase_orders SET status=? WHERE id=?", (st, po["id"]))
    return st


def approve(db, po_id, user=""):
    po = get(db, po_id)
    if po["status"] != "draft":
        raise ValidationError("Only a draft order can be approved")
    limit = to_minor(num(db.setting("po_self_approve_limit") or 0, "approval limit"))
    if po["created_by"] and po["created_by"] == user and limit and to_minor(po["grand_total"]) > limit:
        raise ValidationError(f"Orders above {to_major(limit):,.2f} need a second person to approve them")
    db.x("UPDATE purchase_orders SET status='approved', approved_by=?, approved_at=datetime('now') WHERE id=?", (user, po["id"]))


def set_status(db, po_id, status, reason=""):
    po = get(db, po_id)
    if status == "cancelled":
        if po["status"] not in ("draft", "approved"):
            raise ValidationError(f"A {po['status']} order cannot be cancelled")
        if po["received_value"]:
            raise ValidationError("Goods have already been received. Close the order instead (remainder not required).")
    elif status == "closed":
        if po["status"] not in ("approved", "partial"):
            raise ValidationError("Only an approved or part-received order can be closed")
    elif status == "draft":
        if po["status"] != "approved" or po["received_value"]:
            raise ValidationError("Only an approved order with nothing received can go back to draft")
    else:
        raise ValidationError("Unknown status")
    db.x("UPDATE purchase_orders SET status=?, notes=CASE WHEN ?<>'' THEN notes || char(10) || ? ELSE notes END, "
         "approved_by=CASE WHEN ?='draft' THEN '' ELSE approved_by END WHERE id=?",
         (status, text(reason), f"[{status}] {text(reason)}", status, po["id"]))


def receive(db, po_id, d, user=""):
    """Record what arrived: {date, lines: [{line_id, qty}], paid, method, supplier_ref}. One voucher per line."""
    po = get(db, po_id)
    if po["status"] not in ("approved", "partial"):
        raise ValidationError("Approve the order before receiving against it" if po["status"] == "draft"
                              else f"This order is {po['status']}")
    date = acc.clean_date(d.get("date"))
    want = {}
    for r in d.get("lines") or []:
        q = num(r.get("qty") or 0, "quantity")
        if q:
            want[int(r.get("line_id"))] = q
    if not want:
        raise ValidationError("Enter the quantity received on at least one line")
    by_id = {ln["id"]: ln for ln in po["lines"]}
    pay_left = to_minor(num(d.get("paid") or 0, "paid now"))
    ref = text(d.get("supplier_ref"))
    ids = []
    with db.tx():
        for lid, q in want.items():
            ln = by_id.get(lid)
            if not ln:
                raise ValidationError("That line is not on this order")
            if q > ln["left"] + 1e-9:
                raise ValidationError(f"{ln['description']}: only {ln['left']:g} still to receive")
            net = to_minor(q * ln["rate"])
            vat = int(round(net * (acc.tax_code(db, po["vat_code"], "vat")[1] if po["vat_code"] else 0)))
            wht = int(round(net * (acc.tax_code(db, po["wht_code"], "wht")[1] if po["wht_code"] else 0)))
            paid = min(pay_left, net + vat - wht)
            pay_left -= paid
            memo = f"{po['code']} — {ln['description']}" + (f" ({ref})" if ref else "")
            common = {"date": date, "vendor": po["supplier"], "vat_code": po["vat_code"], "wht_code": po["wht_code"],
                      "paid": to_major(paid), "method": d.get("method") or "", "paid_from": d.get("paid_from") or ""}
            if ln["kind"] == "cost":
                form = {**common, "bu_id": po["bu_id"], "account": ln["account"], "description": memo, "qty": q, "unit": ln["unit"],
                        "rate": ln["rate"], "deal_ref": po["code"], "deal_status": "Open"}
                eid, doc = acc.record_project_cost(db, form, user), "direct_cost"
            elif ln["kind"] == "expense":
                form = {**common, "bu_id": po["bu_id"] or "", "account": ln["account"], "description": memo, "amount": to_major(net)}
                eid, doc = acc.record_expense(db, form, user), "expense"
            else:
                form = {**common, "type": "Receipt", "warehouse_id": po["warehouse_id"], "item": ln["item"], "unit": ln["unit"],
                        "qty": q, "rate": ln["rate"]}
                eid, doc = acc.stock_move(db, form, user), None
                if isinstance(eid, dict):
                    eid = eid.get("entry_id") or eid.get("id")
            row = db.one("SELECT meta FROM journal_entries WHERE id=?", (eid,))
            meta = json.loads(row["meta"] or "{}")
            meta["po"] = po["code"]
            if doc:
                meta["form"] = {k: v for k, v in form.items() if isinstance(v, (str, int, float, bool)) or v is None}
            db.conn.execute("UPDATE journal_entries SET ref=?, meta=? WHERE id=?", (po["code"], json.dumps(meta, ensure_ascii=False), eid))
            db.conn.execute("INSERT INTO po_receipts(po_id,line_id,date,qty,entry_id) VALUES (?,?,?,?,?)", (po["id"], lid, date, q, eid))
            ids.append(eid)
        _refresh_status(db, po["id"])
    return ids


def listing(db, q=None, status=None, supplier=None, bu_id=None):
    w, p = ["1=1"], []
    if status == "open":
        w.append("p.status IN ('draft','approved','partial')")
    elif status:
        w.append("p.status=?")
        p.append(status)
    if supplier:
        w.append("p.supplier=?")
        p.append(supplier)
    if bu_id:
        w.append("p.bu_id=?")
        p.append(int(bu_id))
    if q:
        w.append("(p.code LIKE ? OR p.supplier LIKE ? OR EXISTS (SELECT 1 FROM po_lines l WHERE l.po_id=p.id AND l.description LIKE ?))")
        p += [f"%{q}%"] * 3
    rows = []
    for r in db.q(f"SELECT p.id FROM purchase_orders p WHERE {' AND '.join(w)} ORDER BY p.date DESC, p.id DESC", p):
        po = get(db, r["id"])
        rows.append({k: po[k] for k in ("id", "code", "date", "supplier", "bu_code", "bu_name", "expected", "status", "total",
                                         "vat", "grand_total", "open_value", "received_value", "created_by", "approved_by")}
                    | {"overdue": bool(po["expected"] and po["expected"] < acc.today() and po["status"] in ("approved", "partial"))})
    commit = {}
    for r in rows:
        if r["open_value"]:
            commit[r["bu_code"] or ""] = round(commit.get(r["bu_code"] or "", 0) + r["open_value"], 2)
    return {"rows": rows, "open_value": round(sum(r["open_value"] for r in rows), 2), "by_bu": commit,
            "to_approve": sum(1 for r in rows if r["status"] == "draft")}


def export_rows(db):
    out = []
    for r in db.q("SELECT id FROM purchase_orders ORDER BY id"):
        po = get(db, r["id"])
        out.append({k: po[k] for k in ("code", "date", "supplier", "bu_code", "warehouse", "expected", "vat_code", "wht_code",
                                        "terms", "notes", "status", "created_by", "approved_by")}
                   | {"approved_at": db.one("SELECT approved_at FROM purchase_orders WHERE id=?", (r["id"],))["approved_at"] or ""}
                   | {"lines": [{k: ln[k] for k in ("kind", "description", "item", "account", "qty", "unit", "rate")} for ln in po["lines"]],
                      "receipts": [{"line": rc["description"], "date": rc["date"], "qty": rc["qty"]} for rc in po["receipts"]
                                   if rc["status"] == "posted"]})
    return out


def import_rows(db, rows):
    """JSON restore: orders come back with their lines; receipts are linked to the restored vouchers by reference."""
    n = 0
    for o in rows or []:
        if db.one("SELECT 1 FROM purchase_orders WHERE code=?", (o.get("code"),)):
            continue
        pid = save(db, {**o, "bu_code": o.get("bu_code"), "warehouse": o.get("warehouse") or None}, o.get("created_by") or "")
        db.conn.execute("UPDATE purchase_orders SET status=?, approved_by=?, approved_at=? WHERE id=?",
                        (o.get("status") or "draft", o.get("approved_by") or "", o.get("approved_at") or "", pid))
        lines = {ln["description"]: ln["id"] for ln in db.q("SELECT id, description FROM po_lines WHERE po_id=?", (pid,))}
        used = set()
        for rc in o.get("receipts") or []:
            e = db.one("SELECT id FROM journal_entries WHERE ref=? AND date=? AND memo LIKE ? AND status='posted' AND id NOT IN "
                       f"({','.join(str(x) for x in used) or '0'}) ORDER BY id LIMIT 1", (o["code"], rc["date"], f"%{rc['line']}%"))
            if e and rc["line"] in lines:
                used.add(e["id"])
                db.conn.execute("INSERT INTO po_receipts(po_id,line_id,date,qty,entry_id) VALUES (?,?,?,?,?)",
                                (pid, lines[rc["line"]], rc["date"], rc["qty"], e["id"]))
        n += 1
    return n
