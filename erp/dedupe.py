"""Duplicate stock items: find them, merge them, and stop new ones.

The usual master-data method, in four steps:

1. Normalise every name into a matching key (only for comparing — names are shown as typed): Arabic letter forms
   folded (أ إ آ ٱ → ا, ة → ه, ى → ي, ؤ → و, ئ → ي), marks and tatweel removed, Arabic-Indic digits → 0-9, × * x → x,
   inch marks and unit spellings unified (مم / ملي / mm → mm, سم / cm → cm, بوصة / inch / " → in), punctuation and extra
   spaces dropped, words sorted.
2. Block: only items with the same sizes are compared — every number in the name (with its unit) must agree, so
   "pipe 25mm" and "pipe 32mm" are never offered as the same thing, however alike the words are.
3. Score each pair inside a block: same code (SKU) or same key → "certain"; otherwise the closer of the character
   similarity and the word overlap (≥ 0.92 "likely", ≥ 0.85 "possible"). Different units are flagged, not hidden.
4. A person decides: merge into the item to keep (its movements, balances, count lines and material lines move over,
   balances are recomputed, blank details are filled from the merged items, and the merged names are kept as aliases
   so later imports and entries using them land on the kept item) — or mark the group "not duplicates" so it is not
   offered again.

Prevention: a new item whose key matches an existing item (or one of its aliases) is not created — the existing item is
used; and a code (SKU) can belong to one item only.
"""
import difflib
import json
import re
import unicodedata

from .accounting import ValidationError, now_iso, text

_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي", "ـ": None,
                       "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
                       "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
                       "×": "x", "*": "x", "″": '"', "”": '"', "“": '"', "٫": ".", "،": " "})
_UNITS = [(r"(ملي|مللي|مم|ملم|m\.m|mm)", "mm"), (r"(سنتي|سم|cm)", "cm"), (r'(بوصه|بوصة|انش|inch|in|")', "in")]
_NUM = re.compile(r"\d+(?:\.\d+)?")
# colours make different items (red wire ≠ blue wire): a pair whose colours differ is never offered
COLOURS = {"احمر", "ازرق", "اصفر", "اخضر", "اسود", "ابيض", "رصاصي", "رمادي", "بني", "برتقالي", "بنفسجي", "فضي", "ذهبي",
           "بيج", "زيتي", "لبني", "ارث", "red", "blue", "yellow", "green", "black", "white", "grey", "gray", "brown",
           "orange", "purple", "silver", "gold", "beige"}


def colours(name):
    return {w for w in _base(name).split() if w in COLOURS}


def _base(name):
    s = unicodedata.normalize("NFKC", str(name or "")).lower()
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("M"))   # tashkeel and other marks
    s = s.translate(_FOLD)
    s = re.sub(r"(?<=\d)(?=[^\W\d_])|(?<=[^\W\d_])(?=\d)", " ", s)   # 20بار → 20 بار, pn16 → pn 16
    s = re.sub(r"(?<=[^\W\d_x])x(?=\s*\d)", " x", s)          # احمر*80 → احمر x 80 (the colour stays a word)
    s = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "x", s)             # 50 x 32 → 50x32
    s = re.sub(r"(?<=\d)x(?=\d)", " x ", s)                   # numbers apart, so each one is read on its own
    for rx, unit in _UNITS:
        s = re.sub(rf"(?<=\d)\s*{rx}(?![a-z؀-ۿ])", " " + unit, s)
    s = re.sub(r"[^\w.\s]", " ", s)
    return " ".join(s.split())


def signature(name):
    """The sizes in a name (numbers with the unit after them), order kept — items are only compared within one."""
    b = _base(name).split()
    out = []
    for i, w in enumerate(b):
        if _NUM.fullmatch(w):
            unit = b[i + 1] if i + 1 < len(b) and b[i + 1] in ("mm", "cm", "in") else ""
            out.append(f"{float(w):g}{unit}")
    return tuple(out)


def key(name):
    """Matching key: the normalised words, sorted (word order does not matter)."""
    return " ".join(sorted(_base(name).split()))


def sku_key(sku):
    return re.sub(r"[\s\-_./]", "", str(sku or "").translate(_FOLD)).upper()


def similarity(a, b):
    ka, kb = key(a), key(b)
    if ka == kb:
        return 1.0
    seq = difflib.SequenceMatcher(None, _base(a), _base(b)).ratio()
    srt = difflib.SequenceMatcher(None, ka, kb).ratio()
    wa, wb = set(ka.split()), set(kb.split())
    jac = len(wa & wb) / max(len(wa | wb), 1)
    return max(seq, srt, jac)


# ------------------------------------------------------------------ prevention (used when an item is created)
_CACHE = {}


def _index(db):
    sig = db.one("SELECT COUNT(*) n, COALESCE(MAX(id),0) m, COALESCE(SUM(LENGTH(name)),0) l FROM stock_items")
    al = db.one("SELECT COUNT(*) n FROM stock_item_aliases")
    stamp = (sig["n"], sig["m"], sig["l"], al["n"])
    c = _CACHE.get(id(db))
    if not c or c[0] != stamp:
        idx = {}
        for r in db.q("SELECT id, name FROM stock_items ORDER BY id"):
            idx.setdefault((key(r["name"]), signature(r["name"])), r["id"])
        for r in db.q("SELECT alias, item_id FROM stock_item_aliases ORDER BY rowid"):
            idx.setdefault((key(r["alias"]), signature(r["alias"])), r["item_id"])
        c = _CACHE[id(db)] = (stamp, idx)
    return c[1]


def existing(db, name):
    """The item a new name really is (same name once normalised, or a merged alias), or None."""
    a = db.one("SELECT item_id FROM stock_item_aliases WHERE alias=?", (text(name),))
    if a:
        return db.one("SELECT * FROM stock_items WHERE id=?", (a["item_id"],))
    k = (key(name), signature(name))
    iid = _index(db).get(k)
    if not iid:
        return None
    it = db.one("SELECT * FROM stock_items WHERE id=?", (iid,))       # still that name (or one of its aliases)?
    names = [it["name"]] + [r["alias"] for r in db.q("SELECT alias FROM stock_item_aliases WHERE item_id=?", (iid,))] if it else []
    return it if any((key(n), signature(n)) == k for n in names) else None


def check_sku(db, sku, item_id=None):
    """A code belongs to one item only (compared without spaces, dashes and case)."""
    k = sku_key(sku)
    if not k:
        return
    for r in db.q("SELECT id, name, sku FROM stock_items WHERE sku IS NOT NULL AND sku<>''"):
        if r["id"] != item_id and sku_key(r["sku"]) == k:
            raise ValidationError(f"Code {sku} is already used by “{r['name']}” — a code can belong to one item only")


def similar(db, name, limit=8):
    """Items that look like `name` (for a warning before a new item is saved)."""
    sg, out = signature(name), []
    for r in db.q("SELECT id, name, sku, unit FROM stock_items"):
        if signature(r["name"]) != sg:
            continue
        s = similarity(name, r["name"])
        if s >= 0.85:
            out.append({**r, "score": round(s, 2)})
    return sorted(out, key=lambda r: -r["score"])[:limit]


# ------------------------------------------------------------------ the check (run by hand)
def _ignored(db):
    try:
        return {tuple(sorted(g)) for g in json.loads(db.setting("dedupe_ignore") or "[]")}
    except ValueError:
        return set()


def find(db):
    """Groups of items that may be one and the same, strongest first."""
    items = db.q("""SELECT i.id, i.name, i.sku, i.unit, i.category,
                           COALESCE((SELECT SUM(qty) FROM stock_levels WHERE item_id=i.id),0) AS on_hand,
                           (SELECT COUNT(*) FROM stock_moves WHERE item_id=i.id) AS moves,
                           (SELECT rate FROM stock_moves WHERE item_id=i.id AND qty>0 AND kind='Receipt' ORDER BY date DESC, id DESC LIMIT 1) AS last_rate
                    FROM stock_items i ORDER BY i.id""")
    by = {r["id"]: r for r in items}
    for r in items:
        r["_k"], r["_s"], r["_b"] = key(r["name"]), signature(r["name"]), _base(r["name"])
        r["last_rate"] = r["last_rate"] / 100 if r["last_rate"] is not None else None
    pairs = []
    # same code
    codes = {}
    for r in items:
        if sku_key(r["sku"]):
            codes.setdefault(sku_key(r["sku"]), []).append(r["id"])
    for ids in codes.values():
        for i in range(1, len(ids)):
            pairs.append((ids[0], ids[i], 1.0, "same code"))
    # same sizes → compare names
    blocks = {}
    for r in items:
        r["_c"] = colours(r["name"])
        blocks.setdefault(r["_s"], []).append(r)
    for rows in blocks.values():
        for i in range(len(rows)):
            a = rows[i]
            for b in rows[i + 1:]:
                if a["_c"] and b["_c"] and a["_c"] != b["_c"]:
                    continue
                if a["_k"] == b["_k"]:
                    pairs.append((a["id"], b["id"], 1.0, "same name"))
                    continue
                sm = difflib.SequenceMatcher(None, a["_b"], b["_b"])
                if sm.real_quick_ratio() < 0.85 or sm.quick_ratio() < 0.85:
                    wa, wb = set(a["_k"].split()), set(b["_k"].split())
                    if len(wa & wb) / max(len(wa | wb), 1) < 0.85:
                        continue
                wa, wb = set(a["_k"].split()), set(b["_k"].split())
                da, db_ = wa - wb, wb - wa
                if da and db_ and max(difflib.SequenceMatcher(None, x, y).ratio() for x in da for y in db_) < 0.7:
                    continue          # the words that differ are unrelated (elbow ↔ tee): different things
                s = similarity(a["name"], b["name"])
                if s >= 0.85:
                    pairs.append((a["id"], b["id"], round(s, 2), "similar name"))
    # connected groups
    parent = {}

    def root(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    best, groups = {}, {}
    strong = [p for p in pairs if p[2] >= 0.92]           # only strong matches join a group (no chaining through weak ones)
    for a, b, s, why in strong:
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    for a, b, s, why in strong:
        r = root(a)
        groups.setdefault(r, set()).update((a, b))
        if r not in best or s > best[r][0]:
            best[r] = (s, why)
    for a, b, s, why in pairs:                            # weaker ones: each pair on its own
        if s < 0.92 and not (root(a) == root(b) and root(a) in groups):
            groups[("p", a, b)] = {a, b}
            best[("p", a, b)] = (s, why)
    ign = _ignored(db)
    out = []
    for r, ids in groups.items():
        ids = sorted(ids)
        if tuple(ids) in ign:
            continue
        s, why = best[r]
        members = [{k: v for k, v in by[i].items() if not k.startswith("_")} for i in ids]
        if len(ids) == 2:                                # the words that differ, to judge quickly
            wa, wb = set(by[ids[0]]["_k"].split()), set(by[ids[1]]["_k"].split())
            why = why + (f": {' '.join(sorted(wa - wb)) or '—'} ↔ {' '.join(sorted(wb - wa)) or '—'}" if wa != wb else "")
        level = "certain" if s >= 1.0 else "likely" if s >= 0.92 else "possible"
        units = sorted({(m["unit"] or "").strip() for m in members})
        keep = max(members, key=lambda m: (m["moves"], abs(m["on_hand"]), -m["id"]))["id"]
        out.append({"ids": ids, "items": members, "score": s, "level": level, "reason": why,
                    "units_differ": len(units) > 1, "suggest_keep": keep})
    order = {"certain": 0, "likely": 1, "possible": 2}
    return sorted(out, key=lambda g: (order[g["level"]], -g["score"], g["items"][0]["name"]))


def ignore(db, ids):
    ids = sorted({int(i) for i in ids or []})
    if len(ids) < 2:
        raise ValidationError("Choose the items that are not duplicates")
    cur = sorted(_ignored(db) | {tuple(ids)})
    db.set_setting("dedupe_ignore", json.dumps([list(g) for g in cur]))
    return {"ignored": len(cur)}


def merge(db, keep_id, merge_ids, user=""):
    """Move everything of the merged items onto the kept one, keep their names as aliases, then remove them."""
    from .accounting import _recompute_level
    keep = db.one("SELECT * FROM stock_items WHERE id=?", (int(keep_id),))
    if not keep:
        raise ValidationError("Choose the item to keep")
    dups = [db.one("SELECT * FROM stock_items WHERE id=?", (int(i),)) for i in merge_ids or [] if int(i) != keep["id"]]
    dups = [d for d in dups if d]
    if not dups:
        raise ValidationError("Choose at least one item to merge into it")
    with db.tx():
        whs = set()
        for d in dups:
            whs |= {r["warehouse_id"] for r in db.q("SELECT DISTINCT warehouse_id FROM stock_moves WHERE item_id=?", (d["id"],))}
            db.conn.execute("UPDATE stock_moves SET item_id=? WHERE item_id=?", (keep["id"], d["id"]))
            db.conn.execute("UPDATE material_lines SET item_id=? WHERE item_id=?", (keep["id"], d["id"]))
            for ln in db.q("SELECT * FROM stock_count_lines WHERE item_id=?", (d["id"],)):
                mine = db.one("SELECT * FROM stock_count_lines WHERE count_id=? AND item_id=?", (ln["count_id"], keep["id"]))
                if mine:          # both on one count sheet: one line with both quantities
                    counted = None if mine["counted"] is None and ln["counted"] is None else (mine["counted"] or 0) + (ln["counted"] or 0)
                    db.conn.execute("UPDATE stock_count_lines SET system_qty=?, counted=? WHERE id=?",
                                    (mine["system_qty"] + ln["system_qty"], counted, mine["id"]))
                    db.conn.execute("DELETE FROM stock_count_lines WHERE id=?", (ln["id"],))
                else:
                    db.conn.execute("UPDATE stock_count_lines SET item_id=? WHERE id=?", (keep["id"], ln["id"]))
            for tbl, col in (("products", "stock_item"), ("po_lines", "item"), ("pr_lines", "item")):
                db.conn.execute(f"UPDATE {tbl} SET {col}=? WHERE {col}=?", (keep["name"], d["name"]))
            fill = {c: d[c] for c in ("sku", "category", "notes") if not keep[c] and d[c]}
            fill.update({c: d[c] for c in ("std_cost", "price", "reorder_level") if not keep[c] and d[c]})
            if fill:
                db.conn.execute("UPDATE stock_items SET " + ",".join(f"{c}=?" for c in fill) + " WHERE id=?",
                                (*fill.values(), keep["id"]))
                keep = db.one("SELECT * FROM stock_items WHERE id=?", (keep["id"],))
            db.conn.execute("UPDATE stock_item_aliases SET item_id=? WHERE item_id=?", (keep["id"], d["id"]))
            db.conn.execute("INSERT OR REPLACE INTO stock_item_aliases(alias, item_id, merged_at, username) VALUES (?,?,?,?)",
                            (d["name"], keep["id"], now_iso(), user))
            db.conn.execute("DELETE FROM stock_levels WHERE item_id=?", (d["id"],))
            db.conn.execute("DELETE FROM stock_items WHERE id=?", (d["id"],))
        for w in whs | {r["warehouse_id"] for r in db.q("SELECT warehouse_id FROM stock_levels WHERE item_id=?", (keep["id"],))}:
            _recompute_level(db, keep["id"], w)
    return {"kept": keep["name"], "merged": [d["name"] for d in dups]}
