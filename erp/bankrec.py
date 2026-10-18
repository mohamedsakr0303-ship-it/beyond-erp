"""Bank reconciliation: match the bank's statement to the books, line by line.

A statement (imported from the bank's CSV / Excel file or its own statement file — MT940, CAMT.053, OFX / QFX, QIF —
pasted, or typed) belongs to one cash / bank account and
holds the bank's lines: date, description, reference, amount (+ money in, − money out) and running balance.
Each bank line is matched to one or more vouchers that moved that account (a deposit of several receipts), or a
voucher is created for it straight from the line (bank charges, interest, transfers not booked yet); rules
remember which account a recurring description goes to. Lines can also be ignored.

The match lives on the voucher (meta "bank": {account: bank line id}) so it survives backups and restores.

Reconciliation as of the statement's last day:
    bank closing balance + deposits in transit − outstanding payments     (vouchers not yet seen by the bank)
  = book balance + bank lines not yet in the books                      (charges, interest… still to record)
A statement with no difference and no open lines can be marked reconciled, which locks its matches.
"""
import base64
import csv
import datetime as _dt
import io
import itertools
import json
import re

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

LIVE = "e.status='posted' AND e.reversed_by IS NULL AND e.reversal_of IS NULL"
KIND = "Bank Entry"
EPS = 0.5


def _j(v, default):
    try:
        return json.loads(v) if isinstance(v, str) and v else (v if v is not None else default)
    except ValueError:
        return default


def bank_accounts(db):
    return db.q("SELECT code, name, name_ar FROM accounts WHERE active=1 AND subtype='cash' ORDER BY code")


def _account(db, code):
    a = db.one("SELECT code, name, subtype FROM accounts WHERE code=?", (text(code),))
    if not a or a["subtype"] != "cash":
        raise ValidationError("Choose a cash / bank account")
    return a["code"]


def book_balance(db, account, date_to):
    r = db.one("SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
               "WHERE e.status='posted' AND l.account=? AND e.date<=?", (account, date_to))
    return r["n"]


# ================================================================== parsing
_HEAD = {
    "date": ("date", "transaction date", "trans date", "value date", "posting date", "book date", "booking date", "التاريخ",
             "تاريخ العملية", "تاريخ القيد", "تاريخ الحركة", "تاريخ المعاملة", "تاريخ الحركه"),
    "description": ("description", "details", "narrative", "narration", "particulars", "transaction details", "transaction",
                    "memo", "remarks", "البيان", "الوصف", "التفاصيل", "بيان العملية", "بيان الحركة", "البيان / الوصف"),
    "ref": ("reference", "ref", "ref.", "ref no", "reference no", "cheque", "cheque no", "check no", "chq no", "doc no",
            "document", "رقم المرجع", "المرجع", "رقم الشيك", "رقم المستند", "مرجع"),
    "out": ("debit", "debits", "withdrawal", "withdrawals", "paid out", "money out", "dr", "مدين", "سحب", "السحب",
            "مسحوبات", "المدين", "خصم", "سحوبات"),
    "in": ("credit", "credits", "deposit", "deposits", "paid in", "money in", "cr", "دائن", "إيداع", "ايداع", "الإيداع",
           "الايداع", "الدائن", "إضافة", "ايداعات", "إيداعات"),
    "amount": ("amount", "value", "transaction amount", "المبلغ", "القيمة", "مبلغ"),
    "balance": ("balance", "running balance", "closing balance", "الرصيد", "رصيد"),
}
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").translate(_AR_DIGITS).strip().lower().replace("_", " "))


def parse_amount(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).translate(_AR_DIGITS).strip().replace(" ", "").replace(" ", "")
    if not s or s in ("-", "—"):
        return None
    neg = s.startswith("(") and s.endswith(")") or s.endswith("-") or s.lower().endswith("dr")
    s = re.sub(r"(?i)(cr|dr|egp|usd|eur|ج\.م|جم)$", "", s.strip("()").rstrip("-"))
    s = re.sub(r"(?i)^(egp|usd|eur|ج\.م)", "", s)
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+,\d{1,2}", s):   # 1.234,50
        s = s.replace(".", "").replace(",", ".")
    s = s.replace(",", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return -abs(x) if neg else x


def parse_date(v, order="dmy"):
    if v is None or v == "":
        return None
    if isinstance(v, _dt.datetime):
        return v.date().isoformat()
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, (int, float)) and 20000 < v < 80000:      # Excel serial date
        return (_dt.date(1899, 12, 30) + _dt.timedelta(days=int(v))).isoformat()
    s = str(v).translate(_AR_DIGITS).strip().split(" ")[0].split("T")[0]
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})[-/. ]([A-Za-z]{3})[A-Za-z]*[-/. ](\d{2,4})", s)
        if m:
            d, mo, y = int(m.group(1)), _MONTHS.get(m.group(2).lower()), int(m.group(3))
        else:
            m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
            if not m:
                return None
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            d, mo = (b, a) if order == "mdy" else (a, b)
        if y < 100:
            y += 2000
    try:
        return _dt.date(y, mo, d).isoformat()
    except (TypeError, ValueError):
        return None


def _table(data, filename=""):
    """Rows (lists of cells) from an uploaded file (data URL / base64) or pasted text."""
    name = (filename or "").lower()
    raw = None
    if isinstance(data, str) and data.startswith("data:"):
        raw = base64.b64decode(data.split(",", 1)[1])
    elif isinstance(data, bytes):
        raw = data
    if raw is not None and (name.endswith((".xlsx", ".xlsm")) or raw[:2] == b"PK"):
        try:
            import openpyxl
        except ImportError:
            raise ValidationError("Excel files need openpyxl — save the statement as CSV instead")
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    if raw is not None:
        for enc in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                textdata = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    else:
        textdata = str(data or "")
    sample = textdata[:4000]
    delim = "\t" if "\t" in sample else (";" if sample.count(";") > sample.count(",") else ",")
    return [r for r in csv.reader(io.StringIO(textdata), delimiter=delim)]


# ------------------------------------------------------- bank file formats (MT940, CAMT.053, OFX / QFX, QIF)
def _text_of(data):
    """The upload as text when it is a text file (None for Excel workbooks)."""
    raw = base64.b64decode(data.split(",", 1)[1]) if isinstance(data, str) and data.startswith("data:") else data
    if isinstance(raw, bytes):
        if raw[:2] == b"PK":
            return None
        for enc in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    return str(raw or "")


def _mt_amount(v):
    return float(v.replace(",", ".")) if v else 0.0


def _mt940(t):
    """SWIFT MT940 (and MT942): :60F: opening, :61: a line (+ :86: its details), :62F: closing."""
    rows, opening, closing, tag, cur = [], None, None, None, None
    fields = []
    for line in t.replace("\r", "").split("\n"):
        m = re.match(r"^:(\d{2}[A-Z]?):(.*)$", line)
        if m:
            fields.append([m.group(1), m.group(2)])
        elif fields and line.strip() not in ("-", "}"):
            fields[-1][1] += "\n" + line
    for tag, val in fields:
        if tag in ("60F", "60M", "62F", "62M"):
            m = re.match(r"([CD])(\d{6})[A-Z]{3}([\d,]+)", val.strip())
            if m:
                amt = _mt_amount(m.group(3)) * (-1 if m.group(1) == "D" else 1)
                if tag.startswith("60") and opening is None:
                    opening = amt
                elif tag.startswith("62"):
                    closing = amt
        elif tag == "61":
            first = val.split("\n")[0]
            m = re.match(r"(\d{6})(\d{4})?(R?[CD])([A-Z])?([\d,]+)[A-Z]([A-Z0-9]{3})?([^/\n]*)(?://(.*))?", first)
            if not m:
                continue
            y, mo, d = 2000 + int(first[:2]), int(first[2:4]), int(first[4:6])
            sign = -1 if m.group(3) in ("D", "RC") else 1
            cur = {"date": _dt.date(y, mo, d).isoformat(), "description": " ".join(val.split("\n")[1:]).strip(),
                   "ref": (m.group(7) or "").strip() if (m.group(7) or "").strip() not in ("NONREF", "") else (m.group(8) or "").strip(),
                   "amount": round(sign * _mt_amount(m.group(5)), 2), "balance": None}
            rows.append(cur)
        elif tag == "86" and cur is not None:
            info = re.sub(r"\?\d{2}", " ", val.replace("\n", "")) if re.search(r"\?\d{2}", val) else val.replace("\n", " ")
            cur["description"] = (re.sub(r"\s+", " ", info).strip() + (" " + cur["description"] if cur["description"] else "")).strip()
    return rows, opening, closing


def _local(el):
    return el.tag.rsplit("}", 1)[-1]


def _find(el, *path):
    for step in path:
        if el is None:
            return None
        el = next((c for c in el if _local(c) == step), None)
    return el


def _ftext(el, *path):
    x = _find(el, *path)
    return (x.text or "").strip() if x is not None and x.text else ""


def _camt(t):
    """ISO 20022 CAMT.053 (statement) / CAMT.052 (report): balances OPBD / CLBD and entries (Ntry)."""
    import xml.etree.ElementTree as ET
    if re.search(r"<!(DOCTYPE|ENTITY)", t[:5000], re.I):
        raise ValidationError("This XML file carries definitions that are not allowed in a bank statement")
    root = ET.fromstring(t.encode("utf-8") if not t.lstrip().startswith("<?xml") else re.sub(r"^\s*<\?xml[^>]*\?>", "", t).encode("utf-8"))
    stmts = [e for e in root.iter() if _local(e) in ("Stmt", "Rpt")]
    rows, opening, closing = [], None, None
    for st in stmts:
        for b in (c for c in st if _local(c) == "Bal"):
            code = _ftext(b, "Tp", "CdOrPrtry", "Cd")
            amt = float(_ftext(b, "Amt") or 0) * (-1 if _ftext(b, "CdtDbtInd") == "DBIT" else 1)
            if code in ("OPBD", "PRCD") and opening is None:
                opening = amt
            elif code == "CLBD" or (code == "CLAV" and closing is None):
                closing = amt
        for n in (c for c in st if _local(c) == "Ntry"):
            if _ftext(n, "Sts") in ("PDNG", "INFO") or _ftext(n, "Sts", "Cd") in ("PDNG", "INFO"):
                continue
            amt = float(_ftext(n, "Amt") or 0) * (-1 if _ftext(n, "CdtDbtInd") == "DBIT" else 1)
            if _ftext(n, "RvslInd").lower() == "true":
                amt = -amt
            day = (_ftext(n, "BookgDt", "Dt") or _ftext(n, "BookgDt", "DtTm") or _ftext(n, "ValDt", "Dt") or _ftext(n, "ValDt", "DtTm"))[:10]
            tx = _find(n, "NtryDtls", "TxDtls")
            words = [w for w in (
                " ".join((u.text or "").strip() for u in (tx.iter() if tx is not None else []) if _local(u) == "Ustrd"),
                _ftext(tx, "RltdPties", "Cdtr", "Nm") if amt < 0 else _ftext(tx, "RltdPties", "Dbtr", "Nm"),
                _ftext(n, "AddtlNtryInf")) if w]
            ref = _ftext(tx, "Refs", "EndToEndId") if tx is not None else ""
            ref = ref if ref and ref != "NOTPROVIDED" else (_ftext(n, "AcctSvcrRef") or _ftext(tx, "Refs", "AcctSvcrRef"))
            if day:
                rows.append({"date": day, "description": " · ".join(words), "ref": ref, "amount": round(amt, 2), "balance": None})
    return rows, opening, closing


def _ofx(t):
    """OFX / QFX (Quicken, Money, many online banks), SGML (1.x) or XML (2.x)."""
    def tag(block, name):
        m = re.search(rf"<{name}>\s*([^<\r\n]*)", block, re.I)
        return m.group(1).strip() if m else ""

    def day(v):
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if re.match(r"\d{8}", v or "") else None
    rows = []
    for b in re.findall(r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>)", t, re.I | re.S):
        d = day(tag(b, "DTPOSTED")) or day(tag(b, "DTUSER"))
        amt = parse_amount(tag(b, "TRNAMT"))
        if d and amt:
            words = [w for w in (tag(b, "NAME"), tag(b, "MEMO")) if w]
            rows.append({"date": d, "description": " · ".join(dict.fromkeys(words)), "ref": tag(b, "CHECKNUM") or tag(b, "REFNUM") or tag(b, "FITID"),
                         "amount": round(amt, 2), "balance": None})
    led = re.search(r"<LEDGERBAL>(.*?)(?:</LEDGERBAL>|<AVAILBAL>|</STMTRS>)", t, re.I | re.S)
    closing = parse_amount(tag(led.group(1), "BALAMT")) if led else None
    return rows, None, closing


def _qif(t):
    """QIF: D date, T amount, P payee, M memo, N number, ^ end of a line."""
    rows, cur = [], {}
    for line in t.replace("\r", "").split("\n"):
        if not line:
            continue
        k, v = line[0], line[1:].strip()
        if k == "^":
            if cur.get("date") and cur.get("amount"):
                rows.append({"date": cur["date"], "description": " · ".join(x for x in (cur.get("payee"), cur.get("memo")) if x),
                             "ref": cur.get("ref", ""), "amount": round(cur["amount"], 2), "balance": None})
            cur = {}
        elif k == "D":
            cur["date"] = parse_date(v.replace("'", "/"), "mdy" if re.match(r"\d{1,2}/\d{1,2}", v) and int(v.split("/")[1]) > 12 else "dmy")
        elif k in ("T", "U"):
            cur["amount"] = parse_amount(v)
        elif k == "P":
            cur["payee"] = v
        elif k == "M":
            cur["memo"] = v
        elif k == "N":
            cur["ref"] = v
    return rows, None, None


def _structured(data, filename=""):
    """A bank statement file in one of the banks' own formats → (format, rows, opening, closing), or None."""
    t = _text_of(data)
    if not t:
        return None
    head = t.lstrip()[:3000]
    name = (filename or "").lower()
    if "BkToCstmrStmt" in head or "BkToCstmrAcctRpt" in head or name.endswith(".camt"):
        fmt, got = "CAMT.053 (ISO 20022)", _camt(t)
    elif re.search(r"<OFX>|OFXHEADER", head, re.I) or name.endswith((".ofx", ".qfx")):
        fmt, got = "OFX", _ofx(t)
    elif re.search(r"(^|\n):20:", head) and ":61:" in t or name.endswith((".sta", ".mt940", ".940")):
        fmt, got = "MT940 (SWIFT)", _mt940(t)
    elif head.startswith("!Type:") or name.endswith(".qif"):
        fmt, got = "QIF", _qif(t)
    else:
        return None
    rows, opening, closing = got
    return fmt, [r for r in rows if r["date"] and r["amount"]], opening, closing


def parse(data, filename="", order=None, mapping=None):
    """Find the header row, map the columns, read the lines. Returns rows + what was detected."""
    try:
        found = None if mapping else _structured(data, filename)
    except (ValueError, IndexError, SyntaxError) as e:     # ElementTree's ParseError is a SyntaxError
        raise ValidationError(f"Could not read this bank file: {e}") from None
    if found:
        fmt, out, opening, closing = found
        if not out:
            raise ValidationError(f"No lines with a date and an amount were found in this {fmt} file")
        out.sort(key=lambda r: r["date"])
        if opening is None and closing is not None:
            opening = round(closing - sum(r["amount"] for r in out), 2)
        if closing is None and opening is not None:
            closing = round(opening + sum(r["amount"] for r in out), 2)
        run = opening
        for r in out:                       # the running balance, from the bank's own opening balance
            if run is not None:
                run = round(run + r["amount"], 2)
                r["balance"] = run
        return {"rows": out, "skipped": 0, "columns": {}, "format": fmt, "date_order": "", "date_from": out[0]["date"], "date_to": out[-1]["date"],
                "opening": round(opening, 2) if opening is not None else None, "closing": round(closing, 2) if closing is not None else None,
                "total_in": round(sum(r["amount"] for r in out if r["amount"] > 0), 2),
                "total_out": round(-sum(r["amount"] for r in out if r["amount"] < 0), 2)}
    rows = [r for r in _table(data, filename) if any(c not in (None, "") for c in r)]
    if not rows:
        raise ValidationError("The file is empty")
    head_i, cols = None, {}
    for i, r in enumerate(rows[:40]):
        found = {}
        for j, c in enumerate(r):
            n = _norm(c)
            for k, names in _HEAD.items():
                if n in names and k not in found:
                    found[k] = j
        if "date" in found and ({"amount"} & found.keys() or {"in", "out"} & found.keys()):
            head_i, cols = i, found
            break
    if mapping:
        cols.update({k: int(v) for k, v in mapping.items() if v not in (None, "")})
        head_i = head_i if head_i is not None else -1
    if head_i is None:
        raise ValidationError("Could not find the column headings (date, description, debit / credit or amount) — "
                              "check the file, or paste the lines with a heading row")
    body = rows[head_i + 1:]
    cell = lambda r, k: r[cols[k]] if k in cols and cols[k] < len(r) else None  # noqa: E731
    if not order:   # day/month or month/day: the first part above 12 settles it
        order = "dmy"
        for r in body:
            v = cell(r, "date")
            m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.]\d{2,4}", str(v or "").translate(_AR_DIGITS).strip().split(" ")[0])
            if m and int(m.group(2)) > 12:
                order = "mdy"
                break
            if m and int(m.group(1)) > 12:
                break
    out, skipped = [], 0
    for r in body:
        d = parse_date(cell(r, "date"), order)
        if not d:
            skipped += 1
            continue
        if "amount" in cols:
            amt = parse_amount(cell(r, "amount"))
        else:
            i_, o_ = parse_amount(cell(r, "in")), parse_amount(cell(r, "out"))
            amt = (abs(i_) if i_ else 0) - (abs(o_) if o_ else 0) if (i_ or o_) else None
        if amt is None or abs(amt) < 0.005:
            skipped += 1
            continue
        bal = parse_amount(cell(r, "balance"))
        out.append({"date": d, "description": str(cell(r, "description") or "").strip()[:500],
                    "ref": str(cell(r, "ref") or "").strip()[:80], "amount": round(amt, 2),
                    "balance": round(bal, 2) if bal is not None else None})
    if not out:
        raise ValidationError("No lines with a date and an amount were found")
    if len(out) > 1 and out[0]["date"] > out[-1]["date"]:   # newest first → oldest first
        out.reverse()
    first, last = out[0], out[-1]
    opening = round(first["balance"] - first["amount"], 2) if first["balance"] is not None else None
    closing = last["balance"]
    return {"rows": out, "skipped": skipped, "columns": {k: (rows[head_i][v] if head_i >= 0 and v < len(rows[head_i]) else v) for k, v in cols.items()},
            "date_order": order, "date_from": first["date"], "date_to": last["date"], "opening": opening, "closing": closing,
            "total_in": round(sum(r["amount"] for r in out if r["amount"] > 0), 2),
            "total_out": round(-sum(r["amount"] for r in out if r["amount"] < 0), 2)}


# =============================================================== statements
def get(db, sid):
    s = db.one("SELECT * FROM bank_statements WHERE id=?", (int(sid),))
    if not s:
        raise ValidationError("Statement not found")
    return s


def _lines_in(lines):
    out = []
    for i, r in enumerate(lines or []):
        if not isinstance(r, dict):
            continue
        d = acc.clean_date(r.get("date"), "line date")
        amt = to_minor(num(r.get("amount"), "amount", allow_negative=True))
        if not amt:
            continue
        bal = r.get("balance")
        out.append({"seq": i, "date": d, "description": text(r.get("description"), maxlen=500), "ref": text(r.get("ref"), maxlen=80),
                    "amount": amt, "balance": to_minor(num(bal, "balance", allow_negative=True)) if bal not in (None, "") else None})
    return out


def save(db, d, sid=None):
    old = get(db, sid) if sid else None
    if old and old["status"] == "reconciled":
        raise ValidationError("This statement is reconciled — reopen it to change it")
    account = _account(db, d.get("account") or (old and old["account"]))
    lines = _lines_in(d.get("lines")) if "lines" in d else None
    df = acc.clean_date(d.get("date_from") or (old and old["date_from"]) or (lines[0]["date"] if lines else None), "statement start")
    dt = acc.clean_date(d.get("date_to") or (old and old["date_to"]) or (lines[-1]["date"] if lines else None), "statement end")
    if dt < df:
        raise ValidationError("The statement ends before it starts")
    opening = to_minor(num(d.get("opening"), "opening balance", allow_negative=True)) if d.get("opening") not in (None, "") else \
        (old["opening"] if old else 0)
    closing = to_minor(num(d.get("closing"), "closing balance", allow_negative=True)) if d.get("closing") not in (None, "") else \
        (old["closing"] if old else opening + sum(x["amount"] for x in lines or []))
    book_from = acc.clean_date(d.get("book_from") or (old and old["book_from"]) or df, "include vouchers from")
    vals = dict(account=account, name=text(d.get("name"), maxlen=120) or (old and old["name"]) or f"{account} {df} → {dt}",
                date_from=df, date_to=dt, opening=opening, closing=closing, book_from=book_from, notes=text(d.get("notes"), maxlen=2000))
    with db.tx():
        if old:
            db.conn.execute(f"UPDATE bank_statements SET {','.join(k + '=?' for k in vals)} WHERE id=?", (*vals.values(), old["id"]))
            sid = old["id"]
        else:
            sid = db.conn.execute(f"INSERT INTO bank_statements({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                                  tuple(vals.values())).lastrowid
        if lines:
            seq0 = (db.one("SELECT MAX(seq) m FROM bank_lines WHERE statement_id=?", (sid,))["m"] or 0) + 1
            db.conn.executemany("INSERT INTO bank_lines(statement_id,seq,date,description,ref,amount,balance,status) VALUES (?,?,?,?,?,?,?,'open')",
                                [(sid, seq0 + x["seq"], x["date"], x["description"], x["ref"], x["amount"], x["balance"]) for x in lines])
    return sid


def delete(db, sid):
    s = get(db, sid)
    if s["status"] == "reconciled":
        raise ValidationError("Reopen the reconciled statement before deleting it")
    with db.tx():
        for ln in db.q("SELECT id FROM bank_lines WHERE statement_id=?", (s["id"],)):
            _unmatch_line(db, s["account"], ln["id"])
        db.conn.execute("DELETE FROM bank_statements WHERE id=?", (s["id"],))


def delete_line(db, sid, lid):
    s = get(db, sid)
    _open(s)
    with db.tx():
        _unmatch_line(db, s["account"], int(lid))
        db.conn.execute("DELETE FROM bank_lines WHERE id=? AND statement_id=?", (int(lid), s["id"]))


# ------------------------------------------------------------------ matching
def book_items(db, account, date_from=None, date_to=None):
    """Vouchers that moved the account: one item per voucher (its net amount on the account)."""
    w, p = [LIVE, "l.account=?"], [account]
    if date_from:
        w.append("e.date>=?")
        p.append(date_from)
    if date_to:
        w.append("e.date<=?")
        p.append(date_to)
    rows = db.q(f"""SELECT e.id, e.date, e.kind, e.memo, e.party, e.ref, e.meta, SUM(l.debit-l.credit) amount
                    FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id WHERE {' AND '.join(w)}
                    GROUP BY e.id HAVING amount<>0 ORDER BY e.date, e.id""", p)
    for r in rows:
        r["bank_line"] = (_j(r.pop("meta"), {}).get("bank") or {}).get(account)
    return rows


def _cutoff(db, account):
    r = db.one("SELECT MIN(book_from) m FROM bank_statements WHERE account=?", (account,))
    return r["m"]


def _set_match(db, eid, account, line_id):
    e = db.one("SELECT meta FROM journal_entries WHERE id=?", (eid,))
    m = _j(e["meta"], {})
    bank = dict(m.get("bank") or {})
    if line_id:
        bank[account] = int(line_id)
    else:
        bank.pop(account, None)
    if bank:
        m["bank"] = bank
    else:
        m.pop("bank", None)
    db.conn.execute("UPDATE journal_entries SET meta=? WHERE id=?", (json.dumps(m, ensure_ascii=False), eid))


def _matched_entries(db, account, line_id):
    return [r["id"] for r in db.q("SELECT id, meta FROM journal_entries WHERE meta LIKE ?", ('%"bank"%',))
            if (_j(r["meta"], {}).get("bank") or {}).get(account) == int(line_id)]


def _unmatch_line(db, account, line_id):
    for eid in _matched_entries(db, account, line_id):
        _set_match(db, eid, account, None)
    db.conn.execute("UPDATE bank_lines SET status='open' WHERE id=? AND status='matched'", (line_id,))


def _open(s):
    if s["status"] == "reconciled":
        raise ValidationError("This statement is reconciled — reopen it first")


def match(db, sid, line_id, entry_ids):
    s = get(db, sid)
    _open(s)
    ln = db.one("SELECT * FROM bank_lines WHERE id=? AND statement_id=?", (int(line_id), s["id"]))
    if not ln:
        raise ValidationError("Bank line not found")
    ids = [int(x) for x in entry_ids or []]
    if not ids:
        raise ValidationError("Pick the voucher(s) for this bank line")
    items = {r["id"]: r for r in book_items(db, s["account"])}
    total = 0
    for i in ids:
        it = items.get(i)
        if not it:
            raise ValidationError(f"Voucher #{i} does not move account {s['account']}")
        if it["bank_line"] and it["bank_line"] != ln["id"]:
            raise ValidationError(f"Voucher #{i} is already matched to another bank line")
        total += it["amount"]
    if abs(total - ln["amount"]) > EPS:
        raise ValidationError(f"The vouchers add up to {to_major(total):,.2f} but the bank line is {to_major(ln['amount']):,.2f}")
    with db.tx():
        _unmatch_line(db, s["account"], ln["id"])
        for i in ids:
            _set_match(db, i, s["account"], ln["id"])
        db.conn.execute("UPDATE bank_lines SET status='matched' WHERE id=?", (ln["id"],))
    return ln["id"]


def unmatch(db, sid, line_id):
    s = get(db, sid)
    _open(s)
    with db.tx():
        _unmatch_line(db, s["account"], int(line_id))
        db.conn.execute("UPDATE bank_lines SET status='open' WHERE id=? AND statement_id=?", (int(line_id), s["id"]))


def ignore(db, sid, line_id, on=True):
    s = get(db, sid)
    _open(s)
    with db.tx():
        _unmatch_line(db, s["account"], int(line_id))
        db.conn.execute("UPDATE bank_lines SET status=? WHERE id=? AND statement_id=?", ("ignored" if on else "open", int(line_id), s["id"]))


def _words(s):
    return {w for w in re.findall(r"[\w؀-ۿ]{3,}", (s or "").lower())}


def auto_match(db, sid, days_before=30, days_after=7):
    """Same amount (or a few vouchers adding up to it) within a date window; references and names break ties."""
    s = get(db, sid)
    _open(s)
    cutoff = _cutoff(db, s["account"])
    free = [r for r in book_items(db, s["account"], cutoff) if not r["bank_line"]]
    lines = db.q("SELECT * FROM bank_lines WHERE statement_id=? AND status='open' ORDER BY date, seq", (s["id"],))
    dd = lambda a, b: (_dt.date.fromisoformat(a) - _dt.date.fromisoformat(b)).days  # noqa: E731
    made = 0
    with db.tx():
        for ln in lines:
            win = [r for r in free if -days_after <= dd(ln["date"], r["date"]) <= days_before]
            hay = (ln["description"] + " " + ln["ref"]).lower()
            hw = _words(hay)

            def score(r):
                sc = -abs(dd(ln["date"], r["date"]))
                ref = (r["ref"] or "").lower()
                if ref and len(ref) >= 3 and ref in hay:
                    sc += 100
                if r["party"] and (r["party"].lower() in hay or _words(r["party"]) & hw):
                    sc += 30
                if _words(r["memo"]) & hw:
                    sc += 5
                return sc
            exact = [r for r in win if abs(r["amount"] - ln["amount"]) <= EPS]
            pick = None
            if exact:
                pick = [max(exact, key=score)]
            else:   # one bank movement for several vouchers (a deposit of several receipts / a batch of payments)
                same = [r for r in win if (r["amount"] > 0) == (ln["amount"] > 0) and abs(dd(ln["date"], r["date"])) <= 5][:14]
                for k in (2, 3):
                    combo = next((c for c in itertools.combinations(same, k) if abs(sum(x["amount"] for x in c) - ln["amount"]) <= EPS), None)
                    if combo:
                        pick = list(combo)
                        break
            if not pick:
                continue
            for r in pick:
                _set_match(db, r["id"], s["account"], ln["id"])
                free.remove(r)
            db.conn.execute("UPDATE bank_lines SET status='matched' WHERE id=?", (ln["id"],))
            made += 1
    return made


# ------------------------------------------------------ vouchers from lines
def rule_for(rules, ln):
    hay = (ln["description"] + " " + ln["ref"]).lower()
    for r in rules:
        if not r["active"]:
            continue
        if r["direction"] == "in" and ln["amount"] < 0 or r["direction"] == "out" and ln["amount"] > 0:
            continue
        if r["pattern"] and r["pattern"].lower() in hay:
            return r
    return None


def create_entry(db, sid, line_id, d, user=""):
    """Book a bank line that is not in the books yet (charges, interest, a transfer…) and match it."""
    s = get(db, sid)
    _open(s)
    ln = db.one("SELECT * FROM bank_lines WHERE id=? AND statement_id=?", (int(line_id), s["id"]))
    if not ln:
        raise ValidationError("Bank line not found")
    if ln["status"] == "matched":
        raise ValidationError("This line is already matched")
    other = acc.account_code(db, d.get("account"), field="account")
    if other == s["account"]:
        raise ValidationError("Choose the other side of the movement, not the bank account itself")
    a = to_major(abs(ln["amount"]))
    party = text(d.get("party"))
    vat_code = text(d.get("vat_code"))
    lines = ([{"account": s["account"], "debit": a}, {"account": other, "credit": a}] if ln["amount"] > 0 else
             [{"account": other, "debit": a}, {"account": s["account"], "credit": a}])
    if vat_code and ln["amount"] < 0:       # a bank fee with VAT: split the input VAT out
        _, rate = acc.tax_code(db, vat_code, "vat")
        vat = round(a - a / (1 + rate), 2)
        lines = [{"account": other, "debit": round(a - vat, 2)}, {"account": db.acct("vat_input"), "debit": vat},
                 {"account": s["account"], "credit": a}]
    with db.tx():
        eid = acc._insert_entry(db, date=ln["date"], kind=KIND, memo=text(d.get("memo")) or ln["description"] or "Bank entry",
                                bu_id=acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), party=party,
                                ref=text(d.get("ref")) or ln["ref"], lines=lines,
                                meta={"bank_statement": s["id"], "vat_code": vat_code, "bank": {s["account"]: ln["id"]}}, user=user)
        db.conn.execute("UPDATE bank_lines SET status='matched' WHERE id=?", (ln["id"],))
        if d.get("remember") and text(d.get("pattern")):
            save_rule(db, {"pattern": d["pattern"], "direction": "in" if ln["amount"] > 0 else "out", "account": other,
                           "party": party, "memo": d.get("memo"), "vat_code": vat_code})
    return eid


def apply_rules(db, sid, user=""):
    s = get(db, sid)
    _open(s)
    rules = db.q("SELECT * FROM bank_rules ORDER BY id")
    n = 0
    for ln in db.q("SELECT * FROM bank_lines WHERE statement_id=? AND status='open' ORDER BY date, seq", (s["id"],)):
        r = rule_for(rules, ln)
        if r:
            create_entry(db, sid, ln["id"], {"account": r["account"], "party": r["party"], "memo": r["memo"] or ln["description"],
                                             "vat_code": r["vat_code"], "bu_id": r["bu_id"]}, user)
            n += 1
    return n


def tick(db, sid, entry_ids, on=True):
    """Paper-statement mode: tick the vouchers the bank shows; each tick becomes a bank line matched to it."""
    s = get(db, sid)
    _open(s)
    items = {r["id"]: r for r in book_items(db, s["account"])}
    with db.tx():
        for i in [int(x) for x in entry_ids or []]:
            it = items.get(i)
            if not it:
                continue
            if on and not it["bank_line"]:
                seq = (db.one("SELECT MAX(seq) m FROM bank_lines WHERE statement_id=?", (s["id"],))["m"] or 0) + 1
                lid = db.conn.execute("INSERT INTO bank_lines(statement_id,seq,date,description,ref,amount,status,ticked) "
                                      "VALUES (?,?,?,?,?,?,'matched',1)",
                                      (s["id"], seq, it["date"], (it["memo"] or it["kind"])[:500], it["ref"] or "", it["amount"])).lastrowid
                _set_match(db, i, s["account"], lid)
            elif not on and it["bank_line"]:
                ln = db.one("SELECT * FROM bank_lines WHERE id=? AND statement_id=?", (it["bank_line"], s["id"]))
                if ln:
                    _unmatch_line(db, s["account"], ln["id"])
                    if ln["ticked"]:
                        db.conn.execute("DELETE FROM bank_lines WHERE id=?", (ln["id"],))


# -------------------------------------------------------------- the summary
def summary(db, s):
    acct, to = s["account"], s["date_to"]
    cutoff = _cutoff(db, acct)
    lines = db.q("SELECT l.* FROM bank_lines l JOIN bank_statements st ON st.id=l.statement_id "
                 "WHERE st.account=? AND l.date<=?", (acct, to))
    line_date = {ln["id"]: ln["date"] for ln in lines}
    all_lines = {ln["id"]: ln for ln in db.q("SELECT l.id, l.date, l.statement_id FROM bank_lines l JOIN bank_statements st ON st.id=l.statement_id "
                                              "WHERE st.account=?", (acct,))}
    items = book_items(db, acct, cutoff, None)
    outstanding_in, outstanding_out, late_booked = [], [], 0
    matched_to = {}
    for it in items:
        if it["bank_line"] in all_lines:
            matched_to.setdefault(it["bank_line"], []).append(it)
        if it["date"] > to:
            if it["bank_line"] in line_date:     # in the bank by the end date, booked later
                late_booked += it["amount"]
            continue
        if it["bank_line"] not in all_lines or all_lines[it["bank_line"]]["date"] > to:
            (outstanding_in if it["amount"] > 0 else outstanding_out).append(it)
    this = [ln for ln in lines if ln["statement_id"] == s["id"]]
    unrecorded = sum(ln["amount"] for ln in lines if ln["status"] == "open")
    book = book_balance(db, acct, to)
    dit = sum(i["amount"] for i in outstanding_in)
    op = -sum(i["amount"] for i in outstanding_out)
    adj_bank = s["closing"] + dit - op
    adj_book = book + unrecorded + late_booked
    stale = [ln["id"] for ln in this if ln["status"] == "matched" and ln["id"] not in matched_to]
    prev = db.one("SELECT closing, date_to FROM bank_statements WHERE account=? AND date_to<? AND id<>? ORDER BY date_to DESC LIMIT 1",
                  (acct, s["date_from"], s["id"]))
    lines_total = sum(ln["amount"] for ln in this if not ln["ticked"])
    has_lines = any(not ln["ticked"] for ln in this)
    warnings = []
    if prev and prev["closing"] != s["opening"]:
        warnings.append(f"The opening balance {to_major(s['opening']):,.2f} differs from the previous statement's closing {to_major(prev['closing']):,.2f}")
    if has_lines and abs(s["opening"] + lines_total - s["closing"]) > EPS:
        warnings.append(f"Opening + lines = {to_major(s['opening'] + lines_total):,.2f}, not the closing balance {to_major(s['closing']):,.2f}")
    if stale:
        warnings.append(f"{len(stale)} matched line(s) lost their voucher (voided or reversed) — match them again")
    return {"closing": to_major(s["closing"]), "book": to_major(book), "deposits_in_transit": to_major(dit), "outstanding_payments": to_major(op),
            "unrecorded": to_major(unrecorded), "late_booked": to_major(late_booked), "adjusted_bank": to_major(adj_bank),
            "adjusted_book": to_major(adj_book), "difference": to_major(adj_bank - adj_book),
            "open_lines": sum(1 for ln in this if ln["status"] == "open"), "stale": stale, "warnings": warnings,
            "outstanding_in": [{**i, "amount": to_major(i["amount"])} for i in outstanding_in],
            "outstanding_out": [{**i, "amount": to_major(i["amount"])} for i in outstanding_out],
            "cutoff": cutoff, "_matched": matched_to}


def detail(db, sid):
    s = get(db, sid)
    sm = summary(db, s)
    matched = sm.pop("_matched")
    lines = []
    rules = db.q("SELECT * FROM bank_rules ORDER BY id")
    for ln in db.q("SELECT * FROM bank_lines WHERE statement_id=? ORDER BY date, seq", (s["id"],)):
        r = rule_for(rules, ln) if ln["status"] == "open" else None
        lines.append({**ln, "amount": to_major(ln["amount"]), "balance": to_major(ln["balance"]) if ln["balance"] is not None else None,
                      "entries": [{"id": e["id"], "date": e["date"], "kind": e["kind"], "memo": e["memo"], "party": e["party"],
                                   "amount": to_major(e["amount"])} for e in matched.get(ln["id"], [])],
                      "rule": {"id": r["id"], "account": r["account"], "pattern": r["pattern"]} if r else None})
    window_to = (_dt.date.fromisoformat(s["date_to"]) + _dt.timedelta(days=31)).isoformat()
    free = [{**r, "amount": to_major(r["amount"])} for r in book_items(db, s["account"], sm["cutoff"], window_to) if not r["bank_line"]]
    acct = db.one("SELECT code, name, name_ar FROM accounts WHERE code=?", (s["account"],))
    return {"statement": {**s, "opening": to_major(s["opening"]), "closing": to_major(s["closing"])}, "account": acct,
            "lines": lines, "book": free, "summary": sm}


def reconcile(db, sid, d=None, user=""):
    s = get(db, sid)
    _open(s)
    sm = summary(db, s)
    if sm["open_lines"]:
        raise ValidationError(f"{sm['open_lines']} bank line(s) are still open — match, book or ignore them")
    if abs(sm["difference"]) > 0.004:
        raise ValidationError(f"There is still a difference of {sm['difference']:,.2f}")
    if sm["stale"]:
        raise ValidationError("Some matches lost their voucher — match them again")
    db.x("UPDATE bank_statements SET status='reconciled', reconciled_on=?, reconciled_by=? WHERE id=?",
         (acc.clean_date((d or {}).get("date") or acc.today()), user, s["id"]))


def reopen(db, sid):
    s = get(db, sid)
    later = db.one("SELECT name FROM bank_statements WHERE account=? AND status='reconciled' AND date_from>? LIMIT 1", (s["account"], s["date_to"]))
    if later:
        raise ValidationError(f"Reopen the later statement {later['name']} first")
    db.x("UPDATE bank_statements SET status='open', reconciled_on='', reconciled_by='' WHERE id=?", (s["id"],))


def overview(db):
    out = []
    for a in db.q("SELECT code, name, name_ar FROM accounts WHERE active=1 AND subtype='cash' ORDER BY code"):
        st = db.q("SELECT * FROM bank_statements WHERE account=? ORDER BY date_to DESC", (a["code"],))
        last = next((x for x in st if x["status"] == "reconciled"), None)
        out.append({**a, "book": to_major(book_balance(db, a["code"], "9999-12-31")), "statements": len(st),
                    "reconciled_to": last["date_to"] if last else None, "open": sum(1 for x in st if x["status"] == "open")})
    sts = db.q("SELECT s.*, (SELECT COUNT(*) FROM bank_lines l WHERE l.statement_id=s.id) n, "
               "(SELECT COUNT(*) FROM bank_lines l WHERE l.statement_id=s.id AND l.status='open') open_lines "
               "FROM bank_statements s ORDER BY s.date_to DESC, s.id DESC")
    return {"accounts": out, "statements": [{**x, "opening": to_major(x["opening"]), "closing": to_major(x["closing"])} for x in sts]}


# -------------------------------------------------------------------- rules
def save_rule(db, d, rid=None):
    pattern = text(d.get("pattern"), "text to look for", required=True, maxlen=120)
    account = acc.account_code(db, d.get("account"), field="account")
    direction = d.get("direction") if d.get("direction") in ("in", "out", "any") else "any"
    vat = text(d.get("vat_code"))
    if vat:
        acc.tax_code(db, vat, "vat")
    vals = (pattern, direction, account, text(d.get("party")), text(d.get("memo"), maxlen=200), vat,
            acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), 0 if d.get("active") in (0, "0", False) else 1)
    with db.tx():
        if rid:
            db.conn.execute("UPDATE bank_rules SET pattern=?,direction=?,account=?,party=?,memo=?,vat_code=?,bu_id=?,active=? WHERE id=?", vals + (int(rid),))
            return int(rid)
        return db.conn.execute("INSERT INTO bank_rules(pattern,direction,account,party,memo,vat_code,bu_id,active) VALUES (?,?,?,?,?,?,?,?)",
                               vals).lastrowid


# ------------------------------------------------------------------- backup
TABLES = ("bank_statements", "bank_lines", "bank_rules")


def export_rows(db):
    out = {t: db.q(f"SELECT * FROM {t} ORDER BY id") for t in TABLES}
    bus = {b["id"]: b["code"] for b in db.q("SELECT id, code FROM business_units")}
    for r in out["bank_rules"]:
        r["bu_code"] = bus.get(r["bu_id"], "")
    return out


def import_rows(db, data, counts):
    if not isinstance(data, dict):
        return
    for t in reversed(TABLES):
        db.conn.execute(f"DELETE FROM {t}")
    bus = {b["code"]: b["id"] for b in db.q("SELECT id, code FROM business_units")}
    for t in TABLES:
        rows = data.get(t) or []
        cols = [c[1] for c in db.conn.execute(f"PRAGMA table_info({t})")]
        for r in rows:
            if not isinstance(r, dict):
                continue
            r = dict(r)
            if t == "bank_rules":
                r["bu_id"] = bus.get(r.get("bu_code"))
            keep = [c for c in cols if c in r]
            db.conn.execute(f"INSERT INTO {t}({','.join(keep)}) VALUES ({','.join('?' * len(keep))})", [r[c] for c in keep])
        if rows:
            counts[t] = len(rows)
