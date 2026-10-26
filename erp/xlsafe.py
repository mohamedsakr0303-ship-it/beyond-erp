"""Text that looks like a formula stays text in the files the ERP writes.

A name, memo or description typed as "=…" (or "+…", "-…", "@…") would otherwise open in Excel as a live formula: it
could show something else, call out to a link, or run on the reader's computer. Excel files keep such text as plain
text (the value is unchanged and imports back as it was); CSV files put an apostrophe in front, as Excel does, and the
importer takes it off again.
"""
NOT_XLSX = "This file is not a readable Excel workbook (.xlsx). Open it in Excel, save it as .xlsx or CSV, and choose it again."
RISKY = ("=", "+", "-", "@", "\t", "\r")


def _number(v):
    try:
        float(v)
        return True
    except ValueError:
        return False


def as_text(cell):
    """An openpyxl cell holding text: never a formula."""
    if cell.data_type == "f":
        cell.data_type = "s"
    return cell


def no_formulas(wb):
    """Every cell of a workbook that should hold no formulas at all (reports, lists)."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                as_text(c)
    return wb


def csv_out(v):
    if isinstance(v, str) and v[:1] in RISKY and not _number(v):
        return "'" + v
    return v


def csv_in(v):
    if isinstance(v, str) and v[:1] == "'" and v[1:2] in RISKY and not _number(v[1:]):
        return v[1:]
    return v


def decode_text(raw):
    """A text (CSV) file's contents: Excel's "Unicode Text" (UTF-16), UTF-8, or Arabic Windows; never silently garbled."""
    from .accounting import ValidationError
    encs = ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1256")
    for enc in encs:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text[:2000]:
            return text
    raise ValidationError("This file is neither an Excel workbook nor a text (CSV) file. Save it from Excel as CSV UTF-8 or .xlsx.")
