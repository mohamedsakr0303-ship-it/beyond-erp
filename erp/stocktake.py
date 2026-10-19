"""Stocktakes (الجرد الدوري): count a warehouse, then post the differences as stock adjustments.

Starting a count takes a snapshot of what the books say each item in the warehouse holds and at what average cost.
People enter what they physically counted (blank = not counted yet — left alone when posting). Posting books one
Stock Adjustment per item that differs, through the usual stock engine (Dr/Cr inventory against the stock-adjustment
account). Movements made while the count was open are respected: the adjustment is the counted − snapshot difference
applied to today's quantity, not a reset to the counted figure.
"""
from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major


def next_code(db):
    n = 1
    for r in db.q("SELECT code FROM stock_counts WHERE code LIKE 'SC-%'"):
        tail = r["code"][3:]
        if tail.isdigit():
            n = max(n, int(tail) + 1)
    return f"SC-{n:04d}"


def start(db, d, user=""):
    wh = acc._warehouse(db, d.get("warehouse_id") or d.get("warehouse"))
    if db.one("SELECT 1 FROM stock_counts WHERE warehouse_id=? AND status='counting'", (wh["id"],)):
        raise ValidationError(f"A count of {wh['name']} is already open. Post or cancel it first.")
    items = db.q("SELECT l.item_id, l.qty, l.avg_cost FROM stock_levels l JOIN stock_items i ON i.id=l.item_id "
                 "WHERE l.warehouse_id=? AND (ABS(l.qty)>1e-9 OR ?) ORDER BY i.name", (wh["id"], 1 if d.get("include_zero") else 0))
    with db.tx():
        code = text(d.get("code")) or next_code(db)
        if db.one("SELECT 1 FROM stock_counts WHERE code=?", (code,)):
            raise ValidationError(f"Count {code} already exists")
        cid = db.conn.execute("INSERT INTO stock_counts(code,date,warehouse_id,status,counted_by,notes) VALUES (?,?,?,?,?,?)",
                              (code, acc.clean_date(d.get("date")), wh["id"], "counting", text(d.get("counted_by")) or user,
                               text(d.get("notes"), maxlen=2000))).lastrowid
        db.conn.executemany("INSERT INTO stock_count_lines(count_id,item_id,system_qty,unit_cost) VALUES (?,?,?,?)",
                            [(cid, r["item_id"], r["qty"], r["avg_cost"]) for r in items])
    return cid


def get(db, cid):
    c = db.one("SELECT c.*, w.name AS warehouse FROM stock_counts c JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=? OR c.code=?",
               (int(cid) if str(cid).isdigit() else -1, str(cid)))
    if not c:
        raise ValidationError("Stock count not found")
    c["lines"] = db.q("SELECT l.*, i.name AS item, i.unit, i.sku FROM stock_count_lines l JOIN stock_items i ON i.id=l.item_id "
                      "WHERE l.count_id=? ORDER BY i.name", (c["id"],))
    over = short = 0
    for ln in c["lines"]:
        ln["unit_cost"] = to_major(ln["unit_cost"])
        ln["diff"] = None if ln["counted"] is None else round(ln["counted"] - ln["system_qty"], 6)
        ln["value"] = None if ln["diff"] is None else round(ln["diff"] * ln["unit_cost"], 2)
        if ln["value"]:
            if ln["value"] > 0:
                over += ln["value"]
            else:
                short += -ln["value"]
    c["counted"] = sum(1 for ln in c["lines"] if ln["counted"] is not None)
    c["differences"] = sum(1 for ln in c["lines"] if ln["diff"])
    c["surplus_value"], c["shortage_value"] = round(over, 2), round(short, 2)
    c["net_value"] = round(over - short, 2)
    return c


def save_counts(db, cid, d):
    """d: {"lines": [{"item": name | "item_id": id, "counted": qty|"" , "note": ""}]}. Items not on the sheet are added."""
    c = get(db, cid)
    if c["status"] != "counting":
        raise ValidationError("This count is closed")
    by_id = {ln["item_id"]: ln for ln in c["lines"]}
    with db.tx():
        for ln in d.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            it = acc._item(db, ln, create=True) if (ln.get("item") or ln.get("item_id")) else None
            if not it:
                continue
            counted = None if ln.get("counted") in (None, "") else num(ln.get("counted"), f"{it['name']} counted")
            if it["id"] in by_id:
                db.conn.execute("UPDATE stock_count_lines SET counted=?, note=? WHERE id=?",
                                (counted, text(ln.get("note")), by_id[it["id"]]["id"]))
            else:
                lvl = acc._level(db, it["id"], c["warehouse_id"])
                cost = lvl["avg_cost"] or acc.to_minor(num(ln.get("unit_cost") or 0, "unit cost"))
                db.conn.execute("INSERT INTO stock_count_lines(count_id,item_id,system_qty,counted,unit_cost,note) VALUES (?,?,?,?,?,?)",
                                (c["id"], it["id"], lvl["qty"], counted, cost, text(ln.get("note"))))


def post(db, cid, user=""):
    c = get(db, cid)
    if c["status"] != "counting":
        raise ValidationError("This count is closed")
    if not c["counted"]:
        raise ValidationError("Nothing has been counted yet")
    n = 0
    with db.tx():
        for ln in c["lines"]:
            if not ln["diff"]:
                continue
            now = acc._level(db, ln["item_id"], c["warehouse_id"])["qty"]
            acc.stock_move(db, {"type": "Adjustment", "warehouse_id": c["warehouse_id"], "item_id": ln["item_id"], "item": ln["item"],
                                "counted": round(now + ln["diff"], 6), "rate": ln["unit_cost"], "date": c["date"],
                                "note": f"Stocktake {c['code']}" + (f" — {ln['note']}" if ln["note"] else "")}, user)
            n += 1
        db.conn.execute("UPDATE stock_counts SET status='posted', posted_by=? WHERE id=?", (user, c["id"]))
    return n


def cancel(db, cid):
    c = get(db, cid)
    if c["status"] != "counting":
        raise ValidationError("Only an open count can be cancelled")
    db.x("UPDATE stock_counts SET status='cancelled' WHERE id=?", (c["id"],))


def delete(db, cid):
    """Delete a count that is still open or was cancelled. A posted count stays: its adjustments are in the books."""
    c = get(db, cid)
    if c["status"] == "posted":
        raise ValidationError("A posted count cannot be deleted; its stock adjustments are recorded in the books.")
    with db.tx():
        db.conn.execute("DELETE FROM stock_count_lines WHERE count_id=?", (c["id"],))
        db.conn.execute("DELETE FROM stock_counts WHERE id=?", (c["id"],))
    return c


def listing(db, status=None):
    out = []
    for r in db.q("SELECT id FROM stock_counts" + (" WHERE status=?" if status else "") + " ORDER BY date DESC, id DESC",
                  (status,) if status else ()):
        c = get(db, r["id"])
        out.append({k: c[k] for k in ("id", "code", "date", "warehouse", "status", "counted_by", "posted_by", "counted", "differences",
                                       "surplus_value", "shortage_value", "net_value")} | {"items": len(c["lines"])})
    return out


def alerts(db):
    return {"open_counts": db.one("SELECT COUNT(*) n FROM stock_counts WHERE status='counting'")["n"]}


def export_rows(db):
    out = []
    for r in db.q("SELECT id FROM stock_counts ORDER BY id"):
        c = get(db, r["id"])
        out.append({k: c[k] for k in ("code", "date", "warehouse", "status", "counted_by", "posted_by", "notes")}
                   | {"lines": [{"item": ln["item"], "system_qty": ln["system_qty"], "counted": ln["counted"], "unit_cost": ln["unit_cost"],
                                 "note": ln["note"]} for ln in c["lines"]]})
    return out


def import_rows(db, rows, counts):
    """Restores count sheets as records (a posted count's adjustments come back with the journal)."""
    n = 0
    for r in rows or []:
        if db.one("SELECT 1 FROM stock_counts WHERE code=?", (r.get("code"),)):
            continue
        wh = db.one("SELECT id FROM warehouses WHERE name=?", (r.get("warehouse") or "",))
        if not wh:
            continue
        cid = db.conn.execute("INSERT INTO stock_counts(code,date,warehouse_id,status,counted_by,posted_by,notes) VALUES (?,?,?,?,?,?,?)",
                              (r["code"], r.get("date") or acc.today(), wh["id"], r.get("status") or "counting", r.get("counted_by") or "",
                               r.get("posted_by") or "", r.get("notes") or "")).lastrowid
        for ln in r.get("lines") or []:
            it = acc._item(db, {"item": ln.get("item")}, create=True)
            db.conn.execute("INSERT INTO stock_count_lines(count_id,item_id,system_qty,counted,unit_cost,note) VALUES (?,?,?,?,?,?)",
                            (cid, it["id"], ln.get("system_qty") or 0, ln.get("counted"), acc.to_minor(ln.get("unit_cost") or 0),
                             ln.get("note") or ""))
        n += 1
    if n:
        counts["Stock counts"] = n
