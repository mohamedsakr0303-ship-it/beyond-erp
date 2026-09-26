"""Read the original BEYOND Accounting System workbook (v0.6 – v0.9 layout) and
convert it into interchange rows, so it goes through the normal import path.

Reads cached values (data_only=True): the workbook must have been saved by
Excel/LibreOffice after its last edit.

Accounting corrections applied on the way in (reported back to the user):
  * "Advance Payment" cost lines are supplier advances (an asset, 1310), not
    expenses — they become journal vouchers Dr Supplier Advances / Cr Bank.
  * When a row's Total Cost disagrees with Qty × Unit Cost, Total Cost wins.
  * Payments above a line's cost are booked as a supplier advance.
"""
import datetime as _dt
import re

from . import accounting as acc

COST_ACCOUNTS = {"materials": "5010", "subcontractor": "5020", "direct labor": "5030", "direct labour": "5030",
                 "site preparation": "5040", "equipment rental": "5050", "transport": "5060"}
OPEX_ACCOUNTS = {"rent": "6010", "software": "6020", "service": "6020", "subscription": "6020", "marketing": "6030",
                 "utilities": "6040", "salaries": "6050", "professional": "6060", "insurance": "6085",
                 "travel": "6075", "office": "6080", "bank": "6065"}
SUPPLIER_ADVANCES = "1310"


def _v(x):
    if isinstance(x, str):
        x = x.strip()
        return x or None
    return x


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _date(x):
    if isinstance(x, _dt.datetime):
        return x.date().isoformat()
    if isinstance(x, _dt.date):
        return x.isoformat()
    s = str(x or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return _dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _headers(ws, needles, scan=40, start=1):
    """Yield (row_index, {header_lower: col}) for every header row containing all needles."""
    for r in range(start, min(ws.max_row, start + scan) + 1):
        vals = [str(_v(c.value) or "").lower() for c in ws[r]]
        if all(any(n in h for h in vals) for n in needles):
            yield r, {h: i for i, h in enumerate(vals) if h}


def _col(h, *needles):
    for n in needles:
        for k, i in h.items():
            if n in k:
                return i
    return None


def _get(row, i):
    return row[i] if i is not None and i < len(row) else None


def looks_legacy(path):
    """True for the original BEYOND workbook layout (and not the new interchange workbook)."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True)
        names = set(wb.sheetnames)
        wb.close()
    except Exception:  # noqa: BLE001 - unreadable files are reported by the normal reader
        return False
    return "Master Project Register" in names and "Business Units" not in names


def read_legacy_workbook(path):
    try:
        import openpyxl
    except ImportError:
        raise acc.ValidationError("Excel import needs openpyxl:  python -m pip install openpyxl") from None
    wb = openpyxl.load_workbook(str(path), data_only=True)
    names = {ws.title.lower(): ws for ws in wb.worksheets}

    def sheet(key):
        return next((ws for k, ws in names.items() if key in k), None)

    data = {k: [] for k in ("Business Units", "Price List", "Partners", "Partner Shares", "Receipt Vouchers",
                            "Direct Costs", "Operating Expenses", "Payroll", "Partner Transactions",
                            "Journal Vouchers", "Settings")}
    notes = []
    jv = [0]

    def journal(date, memo, party, lines, bu=""):
        jv[0] += 1
        for acct, dr, cr in lines:
            data["Journal Vouchers"].append({"voucher": f"LEG-{jv[0]}", "date": date, "kind": "Journal Voucher",
                                             "bu_code": bu, "party": party, "memo": memo, "account": acct,
                                             "debit": dr or "", "credit": cr or ""})

    # ---- business units
    codes = set()
    ws = sheet("project register")
    if ws is not None:
        for hr, h in _headers(ws, ["project id", "client"], scan=10):
            for row in ws.iter_rows(min_row=hr + 1, values_only=True):
                code, name = _v(_get(row, _col(h, "project id"))), _v(_get(row, _col(h, "client")))
                if not code or not name or not str(code).upper().startswith("PRJ"):
                    continue
                basis = _v(_get(row, _col(h, "billing basis")))
                status = _v(_get(row, _col(h, "status")))
                contract = _get(row, _col(h, "contract value"))
                codes.add(code)
                data["Business Units"].append({
                    "code": code, "name": name, "type": "" if _v(_get(row, _col(h, "project type"))) == "TBD"
                    else _v(_get(row, _col(h, "project type"))) or "", "location": _v(_get(row, _col(h, "location"))) or "",
                    "area": _f(_get(row, _col(h, "area"))), "start_date": _date(_get(row, _col(h, "start date"))),
                    "status": "In Progress" if status in (None, "TBD") else status,
                    "basis": basis if basis in acc.BILLING_BASES else "Cost-Plus %",
                    "fee_pct": _f(_get(row, _col(h, "fee %"))),
                    "contract_value": contract if isinstance(contract, (int, float)) else ""})
            break

    # the original workbook shared company overhead evenly across active projects — start from the same split
    active = [b for b in data["Business Units"] if b["status"] not in ("Completed", "Closed")]
    if active:
        base = int(10000 / len(active)) / 10000
        for b in active:
            b["overhead_pct"] = base
        active[0]["overhead_pct"] = round(1 - base * (len(active) - 1), 6)
        notes.append(f"Shared overhead is split evenly across the {len(active)} active business units, as in the "
                     "original workbook — change the percentages any time (Business Units → Shared overhead allocation).")

    # ---- price list
    ws = sheet("rate matrix")
    if ws is not None:
        for hr, h in _headers(ws, ["category", "unit"], scan=10):
            for row in ws.iter_rows(min_row=hr + 1, values_only=True):
                cat, desc = _v(_get(row, _col(h, "category"))), _v(_get(row, _col(h, "subcategory", "specification")))
                if not cat or not desc:
                    continue
                cost, mk = _f(_get(row, _col(h, "historical cost", "avg"))), _f(_get(row, _col(h, "markup")))
                price = _get(row, _col(h, "final client price"))
                note = _v(_get(row, _col(h, "notes")))
                data["Price List"].append({"category": cat, "item_no": str(_v(_get(row, _col(h, "item no"))) or ""),
                                           "description": desc, "unit": _v(_get(row, _col(h, "unit"))) or "",
                                           "cost": cost, "markup": mk,
                                           "price": price if isinstance(price, (int, float)) else cost * (1 + mk),
                                           "notes": "" if not note or str(note).startswith("=") else note})
            break

    # ---- partners & shares
    ws = sheet("partner equity")
    if ws is not None:
        for r in range(1, ws.max_row + 1):
            if _v(ws.cell(r, 1).value) == "Partner Name" and "/" in str(_v(ws.cell(r, 2).value) or ""):
                cols = {}
                for ci in range(2, ws.max_column + 1):
                    m = re.match(r"(PRJ-[\w-]+)", str(_v(ws.cell(r, ci).value) or ""))
                    if m and m.group(1) in codes:
                        cols[ci] = m.group(1)
                rr = r + 1
                while True:
                    name = _v(ws.cell(rr, 1).value)
                    if not name or str(name).lower().startswith("check"):
                        break
                    shares = {code: _f(ws.cell(rr, ci).value) for ci, code in cols.items()}
                    data["Partners"].append({"name": name,
                                             "default_share": round(sum(shares.values()) / len(shares), 6) if shares else 0})
                    for code, s in shares.items():
                        data["Partner Shares"].append({"partner": name, "bu_code": code, "share": s})
                    rr += 1
                break
        for r in range(1, ws.max_row + 1):
            if str(_v(ws.cell(r, 1).value) or "").upper().startswith("DISTRIBUTION LOG"):
                hdr = {str(_v(ws.cell(r + 1, ci).value) or "").lower(): ci for ci in range(1, ws.max_column + 1)}
                ca, cd, cn = hdr.get("amount"), hdr.get("description"), hdr.get("notes")
                rr = r + 2
                while rr <= ws.max_row:
                    name, amt = _v(ws.cell(rr, 2).value), _f(ws.cell(rr, ca).value) if ca else 0
                    if not name and not amt:
                        break
                    if name and amt > 0:
                        bu = _v(ws.cell(rr, cn).value) if cn else None
                        data["Partner Transactions"].append({
                            "date": _date(ws.cell(rr, 1).value), "partner": name, "type": "Owner Drawing",
                            "amount": amt, "bu_code": bu if bu in codes else "",
                            "memo": (_v(ws.cell(rr, cd).value) if cd else None) or "Interim dividend"})
                    rr += 1
                break
            if str(_v(ws.cell(r, 1).value) or "").lower().startswith("reserve %"):
                data["Settings"].append({"key": "reserve_pct", "value": _f(ws.cell(r, 2).value)})

    # ---- receipts (income tracker may contain several stacked tables)
    ws = sheet("income tracker")
    if ws is not None:
        seen = set()
        for hr, h in _headers(ws, ["date", "project id", "amount paid"], scan=ws.max_row):
            if hr in seen:
                continue
            seen.add(hr)
            for row in ws.iter_rows(min_row=hr + 1, values_only=True):
                if str(_v(_get(row, 0)) or "").lower() == "date":
                    break
                code, paid = _v(_get(row, _col(h, "project id"))), _f(_get(row, _col(h, "amount paid")))
                if code not in codes or paid <= 0:
                    continue
                data["Receipt Vouchers"].append({
                    "date": _date(_get(row, _col(h, "date"))), "bu_code": code, "ref": _v(_get(row, _col(h, "invoice"))) or "",
                    "memo": _v(_get(row, _col(h, "milestone", "description"))) or "Client payment",
                    "party": _v(_get(row, _col(h, "client"))) or "", "amount": paid,
                    "method": _v(_get(row, _col(h, "payment method"))) or "Bank Transfer"})

    # ---- direct costs
    ws = sheet("project costing")
    if ws is not None:
        for hr, h in _headers(ws, ["project id", "cost category", "total cost"], scan=10):
            for i, row in enumerate(ws.iter_rows(min_row=hr + 1, values_only=True), start=hr + 1):
                code = _v(_get(row, _col(h, "project id")))
                if code not in codes:
                    continue
                qty = _f(_get(row, _col(h, "quantity"))) or 1
                rate = _f(_get(row, _col(h, "unit cost")))
                total = _get(row, _col(h, "total cost"))
                total = _f(total) if isinstance(total, (int, float)) else qty * rate
                if total <= 0:
                    continue
                savings = max(_f(_get(row, _col(h, "negotiation savings"))), 0)
                paid = max(_f(_get(row, _col(h, "amount paid"))), 0)
                cat = str(_v(_get(row, _col(h, "cost category"))) or "Materials")
                vendor = _v(_get(row, _col(h, "vendor"))) or ""
                desc = _v(_get(row, _col(h, "item description"))) or cat
                date = _date(_get(row, _col(h, "date")))
                if cat.lower().startswith("advance"):
                    party = vendor or f"On-account: {desc}"[:60]
                    journal(date, f"Supplier advance — {desc}", party,
                            [(SUPPLIER_ADVANCES, paid or total, 0), ("1020", 0, paid or total)], code)
                    notes.append(f"Row {i}: '{desc}' ({total:,.2f}) booked as a supplier advance (asset 1310), "
                                 "not an expense. Reclassify to cost with a journal voucher once the work is done.")
                    continue
                gross = total + savings
                if abs(qty * rate - gross) > 0.01:
                    rate = gross / qty
                cost = total
                extra = max(paid - cost, 0)
                if paid < cost and not vendor:
                    vendor = "Unnamed supplier"
                data["Direct Costs"].append({
                    "date": date, "bu_code": code, "account": COST_ACCOUNTS.get(cat.lower(), "5010"),
                    "description": desc, "vendor": vendor, "deal_ref": _v(_get(row, _col(h, "deal reference"))) or "",
                    "deal_status": _v(_get(row, _col(h, "deal status"))) or "", "qty": qty,
                    "unit": _v(_get(row, _col(h, "unit"))) or "", "rate": round(rate, 6), "savings": savings,
                    "paid": min(paid, cost), "method": "Bank Transfer"})
                if extra > 0.005:
                    journal(date, f"Payment above cost — {desc}", vendor or "Unnamed supplier",
                            [(SUPPLIER_ADVANCES, extra, 0), ("1020", 0, extra)], code)
                    notes.append(f"Row {i}: paid {extra:,.2f} more than the cost — kept as a supplier advance.")
            break

    # ---- operating expenses
    ws = sheet("operating expenses")
    if ws is not None:
        for hr, h in _headers(ws, ["date", "expense category", "amount"], scan=10):
            for row in ws.iter_rows(min_row=hr + 1, values_only=True):
                amt = _f(_get(row, _col(h, "amount")))
                if amt <= 0:
                    continue
                cat = str(_v(_get(row, _col(h, "expense category"))) or "Other").lower()
                acct = next((a for k, a in OPEX_ACCOUNTS.items() if k in cat), "6090")
                data["Operating Expenses"].append({
                    "date": _date(_get(row, _col(h, "date"))), "account": acct,
                    "description": _v(_get(row, _col(h, "description"))) or cat,
                    "vendor": _v(_get(row, _col(h, "vendor"))) or "", "amount": amt, "paid": amt,
                    "method": _v(_get(row, _col(h, "payment method"))) or "Bank Transfer"})
            break

    # ---- payroll
    ws = sheet("payroll")
    if ws is not None:
        for hr, h in _headers(ws, ["worker name", "rate"], scan=10):
            for row in ws.iter_rows(min_row=hr + 1, values_only=True):
                worker = _v(_get(row, _col(h, "worker name")))
                qty, rate = _f(_get(row, _col(h, "days/hours"))), _f(_get(row, _col(h, "rate")))
                if not worker or qty <= 0 or rate <= 0:
                    continue
                ct = str(_v(_get(row, _col(h, "cost type"))) or "").lower()
                code = _v(_get(row, _col(h, "project id")))
                if "company" in ct or code not in codes:
                    ctype, code = "Administrative Salaries (Shared Overhead)", ""
                elif "non-billable" in ct or "project opex" in ct:
                    ctype = "Indirect Labour (Cost-Centre Overhead)"
                else:
                    ctype = "Direct Labour (Cost of Revenue)"
                note = _v(_get(row, _col(h, "notes")))
                data["Payroll"].append({
                    "date": _date(_get(row, _col(h, "date"))), "bu_code": code or "", "worker": worker,
                    "role": (_v(_get(row, _col(h, "role"))) or "") + (f" — {note}" if note else ""),
                    "cost_type": ctype, "period": str(_v(_get(row, _col(h, "period"))) or ""),
                    "basis": _v(_get(row, _col(h, "rate basis"))) or "", "qty": qty, "rate": rate,
                    "paid": min(_f(_get(row, _col(h, "total paid"))), qty * rate), "method": "Bank Transfer"})
            break

    _read_boq_and_invoices(wb, data, notes)
    wb.close()
    # BEYOND's own letterhead identity, as the first app version set it; only fills settings that are still empty
    for k, v in (("company_name", "BEYOND DESIGN"), ("company_legal_name", "By SWAG For Contracting"),
                 ("company_tagline", "Interior Design & Finishing · More than spaces"),
                 ("bank_name", "CIB PHAROUS UNIVERSITY BRANCH"), ("bank_account", "100047359559"),
                 ("bank_holder", "MOHAMED ABDEL MOHSEN ABDELKADER"), ("bank_swift", "CIBEEGCX87"),
                 ("bank_iban", "EG790010008700000100018405541")):
        data["Settings"].append({"key": "default:" + k, "value": v})
    data["_notes"] = notes
    return data


_ITEM = re.compile(r"^\d+(\.\d+)+$")
_SECTION = re.compile(r"^-?\s*\d+\s*-\s*(.+)$")


def _meta_value(ws, label_words, rows=range(1, 9)):
    """Value next to a bilingual label such as 'Date / التاريخ' (label on the right, value on the left)."""
    for r in rows:
        cells = [(c.column, _v(c.value)) for c in ws[r] if _v(c.value) not in (None, "")]
        for i, (col, v) in enumerate(cells):
            if any(w in str(v) for w in label_words):
                left = [val for cc, val in cells if cc < col]
                if left:
                    return left[-1] if len(left) == 1 else left[-1]
    return None


def _item_rows(ws, cols):
    """Yield (category, row values) for every priced item row after the first 'م' header."""
    category, started = "", False
    for row in ws.iter_rows(values_only=True):
        first = str(_v(row[0]) or "")
        if first == "م":
            started = True
            continue
        m = _SECTION.match(first)
        if m and not _v(row[1] if len(row) > 1 else None):
            category = m.group(1).strip()
            continue
        if not started:
            continue
        if _ITEM.match(first.replace(" ", "")) and _v(row[1]):
            yield category, row


def _find_bu(data, *hints):
    hints = [str(h).strip().lower() for h in hints if h]
    for b in data["Business Units"]:
        loc, name = str(b.get("location") or "").lower(), str(b.get("name") or "").lower()
        if any(h and (h == loc or h in name) for h in hints):
            return b["code"]
    return ""


def _read_boq_and_invoices(wb, data, notes):
    data.setdefault("BOQ Lines", [])
    data.setdefault("Sales Invoices", [])
    data.setdefault("Invoice Lines", [])
    for ws in wb.worksheets:
        title = ws.title.lower()
        if title.startswith("boq"):
            client = _meta_value(ws, ["Client", "العميل", "المشروع"]) or ""
            location = _meta_value(ws, ["Location", "الموقع"]) or ""
            rev = _meta_value(ws, ["Revision", "الاصدار", "الإصدار"]) or "Rev 01"
            date = _date(_meta_value(ws, ["Date", "التاريخ"]))
            bu = _find_bu(data, location, client)
            n = 0
            for cat, row in _item_rows(ws, 8):
                qty, price = _f(row[3]), _f(row[4])
                cost_total = _f(row[7]) if len(row) > 7 else 0
                data["BOQ Lines"].append({
                    "title": f"BOQ — {client}".strip(" —") or ws.title, "bu_code": bu, "rev": rev, "date": date,
                    "client": client, "location": location, "category": cat, "item_no": str(_v(row[0])),
                    "description": _v(row[1]), "unit": _v(row[2]) or "", "qty": qty, "price": price,
                    "cost": round(cost_total / qty, 6) if qty and cost_total else ""})
                n += 1
            if n:
                notes.append(f"Sheet '{ws.title}': {n} BOQ lines imported as 'BOQ — {client}'"
                             + ("" if bu else " (no matching business unit — link it from the BOQ screen)") + ".")
        elif title.startswith("invoice"):
            client = _meta_value(ws, ["المشروع", "Project", "Client"]) or ""
            location = _meta_value(ws, ["الموقع", "Location"]) or ""
            number = re.sub(r"\s+", " ", str(_meta_value(ws, ["الاصدار", "الإصدار", "Revision"]) or ws.title)).strip()
            date = _date(_meta_value(ws, ["التاريخ", "Date"]))
            bu = _find_bu(data, location, client)
            if not bu:
                notes.append(f"Sheet '{ws.title}': no business unit matches '{client}' / '{location}' — skipped.")
                continue
            boq_title = f"Contract BOQ — {number}"
            contract, executed = 0, 0
            for cat, row in _item_rows(ws, 9):
                cq, ex, price = _f(row[3]), _f(row[4]), _f(row[5])
                base = {"category": cat, "item_no": str(_v(row[0])), "description": _v(row[1]),
                        "unit": _v(row[2]) or "", "price": price}
                data["BOQ Lines"].append({**base, "title": boq_title, "bu_code": bu, "rev": "Contract", "date": date,
                                          "client": client, "location": location, "qty": cq})
                contract += 1
                if ex:
                    data["Invoice Lines"].append({**base, "number": number, "qty": ex})
                    executed += 1
            if executed:
                data["Sales Invoices"].append({"number": number, "date": date, "bu_code": bu,
                                               "boq_title": boq_title, "status": "Draft", "customer": client})
            notes.append(f"Sheet '{ws.title}': contract BOQ ({contract} lines) and progress billing '{number}' "
                         f"({executed} executed lines) imported for {bu} as a DRAFT — open it under Sales Invoices, "
                         "check it and press 'Save & post' to book the receivable and revenue.")
