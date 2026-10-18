"""Undo for the AI assistant's changes.

While a change the assistant makes is saved (its request carries ``?_ai=<change id>``), temporary triggers on this
database connection write down every row it adds, changes or removes — the row as it was and as it became. A batch
shares one change id. "Undo" puts those rows back exactly as they were, newest first, in one transaction — but only
when each of them is still exactly as the change left it (otherwise someone has worked on them since, and the undo is
refused rather than overwrite their work).

In the shared workspace the saved change is replayed on every device the same way, so every device records the same
rows; the undo is an ordinary recorded change (POST /api/ai-undo), so every device reverts them the same way.
Only the person who asked for the change, or a super administrator, can undo it.
"""
import json

from .accounting import ValidationError, now_iso

SKIP = {"sessions", "audit_log", "cloud_local", "ai_threads", "ai_usage", "ai_undo", "alert_feed"}
KEEP = 5000          # changes kept for undo


def _tables(db):
    out = []
    for t in db.q("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        if t["name"] in SKIP or "WITHOUT ROWID" in (t["sql"] or "").upper():
            continue
        cols = [c["name"] for c in db.q(f'PRAGMA table_info("{t["name"]}")')]
        if cols:
            out.append((t["name"], cols))
    return out


def _img(prefix, cols):
    return "json_object(" + ",".join(f"'{c}',quote({prefix}.\"{c}\")" for c in cols) + ")"


def _ensure(db):
    """Temporary tables and triggers for this connection (made once; off unless a change is being recorded)."""
    tables = _tables(db)
    have = db.conn.execute("SELECT COUNT(*) FROM sqlite_temp_master WHERE type='trigger' AND name LIKE 'aicap%'").fetchone()[0]
    if have >= 3 * len(tables):            # (made in a transaction that was rolled back? then they are gone: made again)
        return
    db.conn.execute("CREATE TEMP TABLE IF NOT EXISTS aicap(seq INTEGER PRIMARY KEY, tbl TEXT, op TEXT, rid INTEGER, old TEXT, new TEXT)")
    db.conn.execute("CREATE TEMP TABLE IF NOT EXISTS aicap_on(v INTEGER)")
    if not db.conn.execute("SELECT 1 FROM aicap_on").fetchone():
        db.conn.execute("INSERT INTO aicap_on VALUES (0)")
    on = "WHEN (SELECT v FROM aicap_on)=1"
    for t, cols in tables:
        q = t.replace('"', '')
        db.conn.execute(f'CREATE TEMP TRIGGER IF NOT EXISTS "aicap_i_{q}" AFTER INSERT ON main."{t}" {on} BEGIN '
                        f"INSERT INTO aicap(tbl,op,rid,old,new) VALUES ('{q}','i',NEW.rowid,NULL,{_img('NEW', cols)}); END")
        db.conn.execute(f'CREATE TEMP TRIGGER IF NOT EXISTS "aicap_u_{q}" AFTER UPDATE ON main."{t}" {on} BEGIN '
                        f"INSERT INTO aicap(tbl,op,rid,old,new) VALUES ('{q}','u',NEW.rowid,{_img('OLD', cols)},{_img('NEW', cols)}); END")
        db.conn.execute(f'CREATE TEMP TRIGGER IF NOT EXISTS "aicap_d_{q}" AFTER DELETE ON main."{t}" {on} BEGIN '
                        f"INSERT INTO aicap(tbl,op,rid,old,new) VALUES ('{q}','d',OLD.rowid,{_img('OLD', cols)},NULL); END")


def capture(db, change, user, run):
    """Run a change (the route) while recording the rows it touches under `change`; returns the route's reply."""
    change = str(change)[:60]
    with db.lock:
        _ensure(db)
        db.conn.execute("DELETE FROM aicap")
        db.conn.execute("UPDATE aicap_on SET v=1")
        try:
            reply = run()
        finally:
            db.conn.execute("UPDATE aicap_on SET v=0")
        if getattr(reply, "status", 500) < 400:
            rows = db.conn.execute("SELECT tbl, op, rid, old, new FROM aicap ORDER BY seq").fetchall()
            base = (db.one("SELECT MAX(seq) m FROM ai_undo WHERE change=?", (change,)) or {}).get("m") or 0
            for i, r in enumerate(rows, 1):
                db.x("INSERT INTO ai_undo(change, seq, tbl, op, rid, old, new, username, at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (change, base + i, r[0], r[1], r[2], r[3], r[4], (user or {}).get("username", ""), now_iso()))
        db.conn.execute("DELETE FROM aicap")
    return reply


def _current(db, tbl, rid, cols):
    r = db.one(f'SELECT {_img("t", cols)} img FROM "{tbl}" t WHERE rowid=?', (rid,))
    return r["img"] if r else None


def info(db, change):
    rows = db.q("SELECT tbl, op, COUNT(*) n FROM ai_undo WHERE change=? GROUP BY tbl, op", (str(change),))
    top = db.one("SELECT username, MIN(at) at, MAX(undone_at) undone FROM ai_undo WHERE change=?", (str(change),))
    return {"rows": rows, "by": (top or {}).get("username"), "at": (top or {}).get("at"), "undone": (top or {}).get("undone")}


def undo(db, change, user):
    """Put back every row the change touched — refused if any of them was changed again since."""
    change = str(change)[:60]
    rows = db.q("SELECT * FROM ai_undo WHERE change=? ORDER BY seq", (change,))
    if not rows:
        raise ValidationError("Nothing recorded for this change — it cannot be undone from here")
    if rows[0]["undone_at"]:
        raise ValidationError("This change was already undone")
    if not (user.get("is_super") or rows[0]["username"] == user["username"]):
        raise PermissionError("Only the person who asked for this change, or a super administrator, can undo it")
    cols = dict(_tables(db))
    last = {}
    for r in rows:
        last[(r["tbl"], r["rid"])] = r
    moved = []
    for (tbl, rid), r in last.items():
        if tbl not in cols:
            moved.append(tbl)
            continue
        now = _current(db, tbl, rid, cols[tbl])
        want = r["new"] if r["op"] != "d" else None
        if (now and json.loads(now)) != (want and json.loads(want)):
            moved.append(tbl)
    if moved:
        raise ValidationError(f"{len(moved)} record(s) were changed again after the assistant's change "
                              f"({', '.join(sorted(set(moved))[:5])}) — undo it by hand so their later work is kept")
    import re
    ok_lit = re.compile(r"^(NULL|-?\d+(\.\d+)?([eE][-+]?\d+)?|'(?:[^']|'')*'|X'[0-9A-Fa-f]*')$", re.S)

    def lit(img, tbl):                                     # column -> SQL literal, exactly as quote() wrote it
        o = json.loads(img)
        bad = [c for c, v in o.items() if c not in cols[tbl] or not isinstance(v, str) or not ok_lit.match(v)]
        if bad:
            raise ValidationError("The recorded change is not in the expected form — it cannot be undone from here")
        return o
    with db.tx():
        for r in reversed(rows):
            t, rid = r["tbl"], r["rid"]
            if r["op"] == "i":
                db.conn.execute(f'DELETE FROM "{t}" WHERE rowid=?', (rid,))
            elif r["op"] == "u":
                o = lit(r["old"], t)
                db.conn.execute(f'UPDATE "{t}" SET ' + ",".join(f'"{c}"={v}' for c, v in o.items()) + " WHERE rowid=?", (rid,))
            else:
                o = lit(r["old"], t)
                db.conn.execute(f'INSERT INTO "{t}"(rowid,' + ",".join(f'"{c}"' for c in o) + ") VALUES (?," + ",".join(o.values()) + ")", (rid,))
        db.conn.execute("UPDATE ai_undo SET undone_at=? WHERE change=?", (now_iso(), change))
        old = db.one("SELECT change FROM ai_undo GROUP BY change ORDER BY MIN(id) DESC LIMIT 1 OFFSET ?", (KEEP,))
        if old:
            db.conn.execute("DELETE FROM ai_undo WHERE id <= (SELECT MAX(id) FROM ai_undo WHERE change=?)", (old["change"],))
    return {"undone": len(rows)}
