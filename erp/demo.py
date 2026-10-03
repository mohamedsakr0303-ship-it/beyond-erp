"""Demo company: fills an empty set of books with a realistic, fictitious business that touches every function, for
testing and orientation.

"Nile Demo Co." does interior design & finishing (cost-plus, fixed-price and flexible projects), rents out scaffolding /
an elevator / a cradle, and sells apartments and goods on installments. About six months of history ending today:
capital, loans (borrowed and lent), cash custody with expense reports, suppliers / subcontractors / drivers, direct costs
with negotiation discounts and unpaid balances, payroll, VAT and withholding, stock receipts / transfers / issues /
counts, fixed assets and rental equipment with depreciation, a BOQ and progress invoices, a design offer, rental
contracts delivered on a signed handover and invoiced, a security deposit, unit sales with cheques (one cleared, one
bounced), recurring entries, and a reconciled bank statement plus an open one.

The data goes in through the very import sheets users fill (so it doubles as a worked example of every layout), then
the steps that only exist on screens (handovers, cheques, depreciation runs…) are done through the modules.
Everything happens in one transaction: it loads completely or not at all. All names are invented.
"""
import base64
import calendar
import datetime as _dt
import struct
import zlib

from . import accounting as acc
from .accounting import ValidationError

TAG = "demo_loaded"


def _anchor():
    t = _dt.date.fromisoformat(acc.today())
    y, m = t.year, t.month - 5
    while m < 1:
        m += 12
        y -= 1
    return _dt.date(y, m, 1), t


def _png(rgb, w=96, h=64):
    """A small solid-colour PNG with a lighter band (a stand-in product photo)."""
    raw = b""
    for yy in range(h):
        c = tuple(min(255, v + 60) for v in rgb) if h // 3 < yy < h // 2 else rgb
        raw += b"\x00" + bytes(c) * w
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def sheets():
    """Every import layout filled with the demo company (dates end today)."""
    a0, today = _anchor()

    def d(month, day):
        y, m = a0.year + (a0.month - 1 + month) // 12, (a0.month - 1 + month) % 12 + 1
        x = _dt.date(y, m, min(day, calendar.monthrange(y, m)[1]))
        return min(x, today).isoformat()

    def eom(month):
        return d(month, 31)

    S = {}
    S["Settings"] = [{"key": "default:company_name", "value": "Nile Demo Co."},
                     {"key": "default:company_tagline", "value": "Interiors · Contracting · Equipment rental · Real estate"},
                     {"key": "default:company_address", "value": "12 Demo Street, New Cairo"},
                     {"key": "default:company_phone", "value": "+20 100 000 0000"},
                     {"key": "default:company_legal_name", "value": "Nile Demo Co. (fictitious)"},
                     {"key": "default:company_tax_id", "value": "999888777"}]
    S["Business Units"] = [
        {"code": "PRJ-D01", "name": "Villa 7 — Mr. Karim Fahmy", "type": "Villa", "location": "New Cairo", "area": 420,
         "start_date": d(0, 3), "status": "In Progress", "basis": "Cost-Plus %", "fee_pct": 0.25, "overhead_pct": 0.35,
         "customer": "Karim Fahmy"},
        {"code": "PRJ-D02", "name": "Apartment 14 — Ms. Laila Samir", "type": "Apartment", "location": "Sheikh Zayed", "area": 180,
         "start_date": d(1, 1), "status": "In Progress", "basis": "Fixed Price", "contract_value": 950000, "overhead_pct": 0.3,
         "customer": "Laila Samir"},
        {"code": "PRJ-D03", "name": "Office fit-out — Delta Tech", "type": "Office", "location": "Smart Village", "area": 600,
         "start_date": d(2, 10), "status": "In Progress", "basis": "Flexible / Settled", "overhead_pct": 0.2,
         "customer": "Delta Tech Solutions"},
        {"code": "SALES-01", "name": "Nile Residence — units for sale", "type": "Real estate", "location": "6th of October",
         "start_date": d(0, 1), "status": "In Progress", "basis": "Fixed Price", "overhead_pct": 0.15}]
    S["Parties"] = [
        {"name": "Karim Fahmy", "type": "customer", "category": "customer", "receiver_type": "P", "national_id": "28501011234567",
         "phone": "01000000101", "governate": "Cairo", "region": "New Cairo"},
        {"name": "Laila Samir", "type": "customer", "category": "customer", "receiver_type": "P", "phone": "01000000102"},
        {"name": "Delta Tech Solutions", "type": "customer", "category": "customer", "receiver_type": "B", "tax_id": "100200300",
         "governate": "Giza", "region": "Smart Village", "street": "Building B2", "building": "2", "default_vat": "VAT14",
         "default_wht": "WHT1-C", "email": "finance@deltatech.example"},
        {"name": "Atlas Builders", "type": "customer", "category": "customer", "receiver_type": "B", "tax_id": "200300400",
         "phone": "01000000104"},
        {"name": "El-Nour Building Materials", "type": "supplier", "category": "supplier", "phone": "01000000201",
         "specialty": "Cement, sand, bricks", "bank_details": "Demo Bank — 1000200030004", "default_vat": "VAT14"},
        {"name": "Cleopatra Tiles Trading", "type": "supplier", "category": "supplier", "phone": "01000000202", "specialty": "Ceramics & porcelain"},
        {"name": "Hassan Aluminium", "type": "supplier", "category": "subcontractor", "phone": "01000000203", "specialty": "Aluminium & glass"},
        {"name": "Eng. Fathy Plumbing", "type": "supplier", "category": "subcontractor", "phone": "01000000204", "specialty": "Plumbing"},
        {"name": "Osama Mahmoud", "type": "supplier", "category": "driver", "phone": "01000000205", "vehicle": "Jumbo — DEMO 123"},
        {"name": "Spark Electric", "type": "supplier", "category": "technician", "phone": "01000000206", "specialty": "Electrical"},
        {"name": "Steelform Scaffolding", "type": "supplier", "category": "supplier", "phone": "01000000207"},
        {"name": "Cairo Power Co.", "type": "supplier", "category": "supplier"},
        {"name": "Mona Adel", "type": "customer", "category": "customer", "receiver_type": "P", "phone": "01000000301"},
        {"name": "Tamer Nabil", "type": "customer", "category": "customer", "receiver_type": "P", "phone": "01000000302"}]
    S["Partners"] = [{"name": "Hossam Adel", "default_share": 0.5, "active": "Yes", "joined": d(0, 1)},
                     {"name": "Sara Kamal", "default_share": 0.3, "active": "Yes", "joined": d(0, 1)},
                     {"name": "Omar Fouad", "default_share": 0.2, "active": "Yes", "joined": d(0, 1)}]
    S["Partner Transactions"] = [
        {"date": d(0, 1), "partner": "Hossam Adel", "type": "Capital Contribution", "amount": 1500000, "method": "Bank Transfer"},
        {"date": d(0, 1), "partner": "Sara Kamal", "type": "Capital Contribution", "amount": 900000, "method": "Bank Transfer"},
        {"date": d(0, 1), "partner": "Omar Fouad", "type": "Capital Contribution", "amount": 600000, "method": "Bank Transfer"},
        {"date": d(4, 28), "partner": "Sara Kamal", "type": "Owner Drawing", "amount": 30000, "method": "Bank Transfer",
         "memo": "Interim drawing"}]
    S["Warehouses"] = [{"name": "Site Store — Villa 7", "location": "New Cairo"}]
    S["Stock Items"] = [{"name": "Ceramic tiles 60x60", "unit": "m2", "sku": "CT-6060", "reorder_level": 50},
                        {"name": "Cement bag 50kg", "unit": "bag", "sku": "CEM-50", "reorder_level": 40},
                        {"name": "Wall paint 18L", "unit": "drum", "sku": "PNT-18", "reorder_level": 5},
                        {"name": "Bathroom set (basin + WC)", "unit": "set", "sku": "SAN-01", "reorder_level": 2}]
    S["Fixed Assets"] = [
        {"code": "FA-D01", "name": "Site pickup truck", "account": "1420", "acquired": d(0, 5), "cost": 780000, "salvage": 120000,
         "life_months": 60, "location": "Yard", "serial": "DEMO 456"},
        {"code": "FA-D02", "name": "Design workstations ×3", "account": "1430", "acquired": d(0, 8), "cost": 165000, "life_months": 36,
         "depr_method": "declining", "depr_factor": 2},
        {"code": "FA-D03", "name": "Office furniture", "account": "1400", "acquired": d(0, 2), "cost": 90000, "salvage": 9000,
         "life_months": 84, "opening_depr": 0}]
    S["Products"] = [
        {"code": "U-A101", "inventory_account": "1210", "name": "Apartment A-101", "kind": "unit", "category": "Apartments", "unit": "unit", "price": 2400000,
         "cost": 1500000, "bu_code": "SALES-01", "specs": "Area: 150 m²; Floor: 1; Bedrooms: 3; Finishing: Semi-finished",
         "description": "Corner apartment with garden view."},
        {"code": "U-A204", "inventory_account": "1210", "name": "Apartment A-204", "kind": "unit", "category": "Apartments", "unit": "unit", "price": 2650000,
         "cost": 1600000, "bu_code": "SALES-01", "specs": "Area: 165 m²; Floor: 2; Bedrooms: 3"},
        {"code": "U-V01", "inventory_account": "1210", "name": "Villa V-01", "kind": "unit", "category": "Villas", "unit": "unit", "price": 7800000, "cost": 5200000,
         "bu_code": "SALES-01", "specs": "Area: 380 m²; Land: 500 m²; Bedrooms: 5", "status": "available"},
        {"code": "U-S03", "inventory_account": "1210", "name": "Shop S-03", "kind": "unit", "category": "Shops", "unit": "unit", "price": 1900000, "cost": 1100000,
         "bu_code": "SALES-01", "specs": "Area: 60 m²; Frontage: 6 m", "status": "hidden"},
        {"code": "G-TILE", "name": "Ceramic tiles 60x60 (retail)", "kind": "item", "category": "Materials", "unit": "m2", "price": 420,
         "stock_item": "Ceramic tiles 60x60", "vat_code": "VAT14", "bu_code": "SALES-01"},
        {"code": "G-SAN", "name": "Bathroom set (retail)", "kind": "item", "category": "Sanitary ware", "unit": "set", "price": 9500,
         "stock_item": "Bathroom set (basin + WC)", "vat_code": "VAT14"},
        {"code": "S-DES", "name": "Interior design consultation", "kind": "service", "category": "Services", "unit": "visit",
         "price": 3500, "vat_code": "VAT14"}]
    S["Price List"] = [
        {"category": "Demolition", "item_no": "1.1", "description": "Wall demolition and debris removal", "unit": "m2", "cost": 120, "markup": 0.25},
        {"category": "Masonry", "item_no": "2.1", "description": "Red brick walls 12 cm", "unit": "m2", "cost": 520, "markup": 0.25},
        {"category": "Plastering", "item_no": "3.1", "description": "Internal plaster", "unit": "m2", "cost": 160, "markup": 0.3},
        {"category": "Flooring", "item_no": "4.1", "description": "Porcelain 60x60 supply & fix", "unit": "m2", "cost": 780, "markup": 0.25},
        {"category": "Painting", "item_no": "5.1", "description": "Plastic paint 3 coats", "unit": "m2", "cost": 95, "markup": 0.35},
        {"category": "Gypsum", "item_no": "6.1", "description": "Gypsum board ceiling", "unit": "m2", "cost": 380, "markup": 0.3},
        {"category": "Electrical", "item_no": "7.1", "description": "Electrical point", "unit": "point", "cost": 450, "markup": 0.3},
        {"category": "Plumbing", "item_no": "8.1", "description": "Plumbing point", "unit": "point", "cost": 650, "markup": 0.3}]
    boq = [("Demolition", "1.1", "Wall demolition and debris removal", "m2", 120, 150),
           ("Masonry", "2.1", "Red brick walls 12 cm", "m2", 85, 650),
           ("Plastering", "3.1", "Internal plaster", "m2", 400, 210),
           ("Flooring", "4.1", "Porcelain 60x60 supply & fix", "m2", 160, 975),
           ("Painting", "5.1", "Plastic paint 3 coats", "m2", 520, 130),
           ("Gypsum", "6.1", "Gypsum board ceiling", "m2", 120, 495),
           ("Electrical", "7.1", "Electrical point", "point", 60, 585),
           ("Plumbing", "8.1", "Plumbing point", "point", 18, 845)]
    S["BOQ Lines"] = [{"title": "BOQ — Apartment 14", "bu_code": "PRJ-D02", "rev": "Rev 01", "date": d(1, 1), "client": "Laila Samir",
                       "location": "Sheikh Zayed", "category": c, "item_no": n, "description": t, "unit": u, "qty": q, "price": p}
                      for c, n, t, u, q, p in boq]
    S["Sales Invoices"] = [
        {"number": "INV-D001", "date": d(2, 28), "bu_code": "PRJ-D02", "boq_title": "BOQ — Apartment 14", "status": "Posted",
         "customer": "Laila Samir"},
        {"number": "INV-D002", "date": d(4, 15), "bu_code": "PRJ-D03", "status": "Posted", "customer": "Delta Tech Solutions",
         "vat_code": "VAT14", "wht_code": "WHT1-C"},
        {"number": "INV-D004", "date": d(3, 28), "bu_code": "PRJ-D01", "status": "Posted", "customer": "Karim Fahmy"},
        {"number": "INV-D003", "date": d(5, 1), "bu_code": "PRJ-D02", "boq_title": "BOQ — Apartment 14", "status": "Draft",
         "customer": "Laila Samir"}]
    S["Invoice Lines"] = [
        {"number": "INV-D001", "category": "Demolition", "item_no": "1.1", "description": "Wall demolition and debris removal", "unit": "m2", "qty": 120, "price": 150},
        {"number": "INV-D001", "category": "Masonry", "item_no": "2.1", "description": "Red brick walls 12 cm", "unit": "m2", "qty": 85, "price": 650},
        {"number": "INV-D001", "category": "Plastering", "item_no": "3.1", "description": "Internal plaster", "unit": "m2", "qty": 250, "price": 210},
        {"number": "INV-D001", "category": "Flooring", "item_no": "4.1", "description": "Porcelain 60x60 supply & fix", "unit": "m2", "qty": 120, "price": 975},
        {"number": "INV-D001", "category": "Gypsum", "item_no": "6.1", "description": "Gypsum board ceiling", "unit": "m2", "qty": 120, "price": 495},
        {"number": "INV-D002", "description": "Fit-out works — stage 1 (partitions & ceilings)", "unit": "LS", "qty": 1, "price": 780000},
        {"number": "INV-D004", "description": "Cost-plus billing: works to date + 25% fee", "unit": "LS", "qty": 1, "price": 1050000},
        {"number": "INV-D002", "description": "Design fee", "unit": "LS", "qty": 1, "price": 60000},
        {"number": "INV-D003", "category": "Plastering", "item_no": "3.1", "description": "Internal plaster", "unit": "m2", "qty": 150, "price": 210},
        {"number": "INV-D003", "category": "Flooring", "item_no": "4.1", "description": "Porcelain 60x60 supply & fix", "unit": "m2", "qty": 80, "price": 975}]
    S["Direct Costs"] = [
        {"date": d(0, 10), "bu_code": "PRJ-D01", "account": "5040", "description": "Site clearing and debris removal", "vendor": "Osama Mahmoud", "qty": 1, "unit": "Task", "rate": 18000, "paid": 18000, "method": "Cash"},
        {"date": d(0, 14), "bu_code": "PRJ-D01", "account": "5010", "description": "Cement, sand & bricks", "vendor": "El-Nour Building Materials", "qty": 1, "unit": "Lot", "rate": 64000, "paid": 40000, "vat_code": "VAT14"},
        {"date": d(0, 20), "bu_code": "PRJ-D01", "account": "5020", "description": "Masonry works", "vendor": "Eng. Fathy Plumbing", "deal_ref": "FATHY-01", "deal_status": "Open", "qty": 1, "unit": "LS", "rate": 85000, "paid": 50000},
        {"date": d(1, 6), "bu_code": "PRJ-D01", "account": "5020", "description": "Plumbing first fix", "vendor": "Eng. Fathy Plumbing", "deal_ref": "FATHY-02", "deal_status": "Settled", "qty": 1, "unit": "LS", "rate": 46000, "savings": 4000, "paid": 42000},
        {"date": d(1, 12), "bu_code": "PRJ-D01", "account": "5030", "description": "Daily labour — plaster", "vendor": "Site crew", "qty": 12, "unit": "day", "rate": 900, "paid": 10800, "method": "Cash"},
        {"date": d(2, 3), "bu_code": "PRJ-D01", "account": "5010", "description": "Porcelain 60x60", "vendor": "Cleopatra Tiles Trading", "deal_ref": "CLEO-01", "qty": 260, "unit": "m2", "rate": 610, "paid": 120000, "vat_code": "VAT14"},
        {"date": d(2, 18), "bu_code": "PRJ-D01", "account": "5020", "description": "Aluminium windows", "vendor": "Hassan Aluminium", "deal_ref": "HASSAN-01", "qty": 1, "unit": "LS", "rate": 135000, "savings": 10000, "paid": 60000, "wht_code": "WHT1-S"},
        {"date": d(3, 9), "bu_code": "PRJ-D01", "account": "5020", "description": "Electrical works", "vendor": "Spark Electric", "qty": 1, "unit": "LS", "rate": 72000, "paid": 72000},
        {"date": d(1, 8), "bu_code": "PRJ-D02", "account": "5040", "description": "Demolition", "vendor": "Osama Mahmoud", "qty": 120, "unit": "m2", "rate": 110, "paid": 13200, "method": "Cash"},
        {"date": d(1, 20), "bu_code": "PRJ-D02", "account": "5010", "description": "Bricks & mortar", "vendor": "El-Nour Building Materials", "qty": 1, "unit": "Lot", "rate": 38000, "paid": 38000},
        {"date": d(2, 15), "bu_code": "PRJ-D02", "account": "5030", "description": "Plaster crew", "vendor": "Site crew", "qty": 20, "unit": "day", "rate": 850, "paid": 17000, "method": "Cash"},
        {"date": d(3, 20), "bu_code": "PRJ-D02", "account": "5010", "description": "Porcelain for living room", "vendor": "Cleopatra Tiles Trading", "qty": 160, "unit": "m2", "rate": 600, "paid": 0, "vat_code": "VAT14"},
        {"date": d(2, 22), "bu_code": "PRJ-D03", "account": "5020", "description": "Gypsum partitions", "vendor": "Hassan Aluminium", "qty": 1, "unit": "LS", "rate": 180000, "paid": 100000},
        {"date": d(3, 5), "bu_code": "PRJ-D03", "account": "5060", "description": "Transport of materials", "vendor": "Osama Mahmoud", "qty": 4, "unit": "trip", "rate": 1500, "paid": 6000, "method": "Cash"},
        {"date": d(4, 2), "bu_code": "PRJ-D03", "account": "5050", "description": "Scissor lift rental", "vendor": "Steelform Scaffolding", "qty": 1, "unit": "month", "rate": 14000, "paid": 14000}]
    S["Operating Expenses"] = (
        [{"date": eom(m), "account": "6010", "description": f"Office rent {d(m, 1)[:7]}", "vendor": "Demo Properties", "amount": 25000}
         for m in range(0, 4)] +
        [{"date": d(m, 17), "account": "6020", "description": "Design software subscription", "vendor": "SoftDemo", "amount": 2200,
          "method": "Credit Card"} for m in range(0, 5)] +
        [{"date": d(m, 12), "account": "6040", "description": f"Electricity {d(m, 1)[:7]}", "vendor": "Cairo Power Co.",
          "amount": 1800 + 150 * m} for m in range(0, 5)] +
        [{"date": d(1, 15), "account": "6030", "description": "Social media campaign", "vendor": "AdsDemo", "amount": 15000},
         {"date": d(3, 25), "account": "6060", "description": "Legal & audit retainer", "vendor": "Demo Accountants", "amount": 12000,
          "paid": 0}])
    S["Payroll"] = (
        [{"date": eom(m), "worker": "Mariam Hany", "role": "Accountant", "cost_type": "Administrative Salaries (Shared Overhead)",
          "period": d(m, 1)[:7], "basis": "Monthly", "qty": 1, "rate": 14000, "paid": 14000} for m in range(0, 5)] +
        [{"date": eom(m), "bu_code": "PRJ-D01", "worker": "Eng. Youssef Ali", "role": "Site engineer",
          "cost_type": "Indirect Labour (Cost-Centre Overhead)", "period": d(m, 1)[:7], "basis": "Monthly", "qty": 1, "rate": 16000,
          "paid": 16000 if m < 4 else 0} for m in range(0, 5)] +
        [{"date": d(2, 20), "bu_code": "PRJ-D02", "worker": "Ahmed Saber", "role": "Painter", "cost_type": "Direct Labour (Cost of Revenue)",
          "basis": "Daily", "qty": 10, "rate": 800, "paid": 8000}])
    S["Receipt Vouchers"] = [
        {"date": d(0, 8), "bu_code": "PRJ-D01", "memo": "Advance payment", "party": "Karim Fahmy", "amount": 300000,
         "credit_account": "2100", "ref": "RV-D001"},
        {"date": d(1, 28), "bu_code": "PRJ-D01", "memo": "Design & supervision fee (cash basis)", "party": "Karim Fahmy", "amount": 180000,
         "ref": "RV-D002"},
        {"date": d(1, 3), "bu_code": "PRJ-D02", "memo": "Advance payment", "party": "Laila Samir", "amount": 150000, "credit_account": "2100"},
        {"date": d(3, 10), "bu_code": "PRJ-D02", "memo": "Invoice INV-D001", "party": "Laila Samir", "amount": 120000, "credit_account": "1100"},
        {"date": d(4, 30), "bu_code": "PRJ-D03", "memo": "Invoice INV-D002 (5% withheld … 1% WHT)", "party": "Delta Tech Solutions",
         "amount": 300000, "credit_account": "1100", "withheld": 4800},
        {"date": d(5, 1), "bu_code": "PRJ-D01", "memo": "Invoice INV-D004", "party": "Karim Fahmy", "amount": 400000,
         "credit_account": "1100"},
        {"date": d(3, 12), "bu_code": "PRJ-D03", "memo": "Design consultation", "party": "Atlas Builders", "amount": 11400,
         "vat_code": "VAT14", "method": "Cash"}]
    S["Payment Vouchers"] = [
        {"date": d(1, 25), "party": "El-Nour Building Materials", "amount": 20000, "memo": "On account"},
        {"date": d(3, 15), "party": "Eng. Fathy Plumbing", "amount": 25000, "memo": "Masonry balance — part"},
        {"date": d(4, 10), "party": "Hassan Aluminium", "amount": 40000, "method": "Cheque", "ref": "CHQ 4411"}]
    S["Stock Moves"] = [
        {"date": d(1, 4), "type": "Receipt", "warehouse": "Main Store", "item": "Ceramic tiles 60x60", "unit": "m2", "qty": 400, "rate": 300,
         "vendor": "Cleopatra Tiles Trading", "paid": 120000},
        {"date": d(1, 4), "type": "Receipt", "warehouse": "Main Store", "item": "Cement bag 50kg", "unit": "bag", "qty": 200, "rate": 95,
         "vendor": "El-Nour Building Materials", "paid": 19000},
        {"date": d(1, 5), "type": "Receipt", "warehouse": "Main Store", "item": "Bathroom set (basin + WC)", "unit": "set", "qty": 10,
         "rate": 6200, "vendor": "Cleopatra Tiles Trading", "paid": 62000},
        {"date": d(1, 5), "type": "Receipt", "warehouse": "Main Store", "item": "Wall paint 18L", "unit": "drum", "qty": 20, "rate": 1150,
         "vendor": "El-Nour Building Materials", "paid": 23000},
        {"date": d(1, 18), "type": "Transfer", "warehouse": "Main Store", "to_warehouse": "Site Store — Villa 7", "item": "Cement bag 50kg", "qty": 80},
        {"date": d(1, 20), "type": "Issue", "warehouse": "Site Store — Villa 7", "item": "Cement bag 50kg", "qty": 60, "bu_code": "PRJ-D01", "account": "5010"},
        {"date": d(2, 10), "type": "Issue", "warehouse": "Main Store", "item": "Wall paint 18L", "qty": 6, "bu_code": "PRJ-D02", "account": "5010"},
        {"date": d(3, 28), "type": "Adjustment", "warehouse": "Main Store", "item": "Cement bag 50kg", "counted": 115, "qty": 1,
         "note": "Monthly count — 5 bags damaged"}]
    S["Journal Vouchers"] = [
        {"voucher": "JV-D00", "date": d(0, 1), "kind": "Journal Voucher", "memo": "Nile Residence units completed — held for sale at cost",
         "account": "1210", "debit": 9400000},
        {"voucher": "JV-D00", "date": d(0, 1), "kind": "Journal Voucher", "party": "Demo Bank", "memo": "Development finance for Nile Residence",
         "account": "2400", "credit": 9400000},
        {"voucher": "JV-D0C", "date": d(0, 2), "kind": "Journal Voucher", "memo": "Cash withdrawn for site expenses", "account": "1010", "debit": 200000},
        {"voucher": "JV-D0C", "date": d(0, 2), "kind": "Journal Voucher", "memo": "Cash withdrawn for site expenses", "account": "1020", "credit": 200000},
        {"voucher": "JV-D01", "date": eom(3), "kind": "Journal Voucher", "memo": "Accrued insurance premium", "account": "6085", "debit": 6000},
        {"voucher": "JV-D01", "date": eom(3), "kind": "Journal Voucher", "memo": "Accrued insurance premium", "account": "2030", "credit": 6000},
        {"voucher": "JV-D02", "date": eom(4), "kind": "Journal Voucher", "bu_code": "PRJ-D01", "memo": "Work done on Villa 7, not yet invoiced",
         "account": "1180", "debit": 90000},
        {"voucher": "JV-D02", "date": eom(4), "kind": "Journal Voucher", "bu_code": "PRJ-D01", "memo": "Work done on Villa 7, not yet invoiced",
         "account": "4010", "credit": 90000}]
    # ---- modules on their own screens
    S["Loans"] = [
        {"code": "LN-D01", "direction": "borrowed", "party": "Demo Bank", "purpose": "Working capital", "start_date": d(0, 15),
         "principal": 600000, "interest_type": "declining", "rate": 18, "term_months": 24, "frequency": 1, "schedule_type": "equal",
         "first_due": d(1, 15)},
        {"code": "LN-D02", "direction": "lent", "party": "Eng. Youssef Ali", "purpose": "Staff loan", "start_date": d(1, 1),
         "principal": 24000, "interest_type": "none", "term_months": 12, "frequency": 1, "schedule_type": "equal", "first_due": d(2, 1),
         "method": "Cash"},
        {"code": "LN-D03", "direction": "borrowed", "party": "Hossam Adel", "purpose": "Partner bridge loan (already in the books)",
         "start_date": d(0, 1), "principal": 200000, "booking": "opening", "opening_balance": 150000, "interest_type": "none",
         "schedule_type": "none", "term_months": 0}]
    S["Loan Transactions"] = (
        [{"loan": "LN-D01", "date": d(m, 15), "action": "pay", "principal": 21000, "interest": 9000 - 300 * (m - 1)} for m in range(1, 5)] +
        [{"loan": "LN-D02", "date": d(m, 1), "action": "pay", "principal": 2000, "method": "Cash"} for m in range(2, 5)])
    S["Cash Custody"] = [
        {"code": "CU-D01", "holder": "Eng. Youssef Ali", "kind": "temporary", "purpose": "Site purchases — Villa 7", "issued_on": d(2, 2),
         "amount": 15000, "due_date": d(3, 2), "bu_code": "PRJ-D01", "method": "Cash"},
        {"code": "CU-D02", "holder": "Mariam Hany", "kind": "permanent", "purpose": "Office petty cash", "issued_on": d(0, 3),
         "amount": 5000, "float_amount": 5000, "method": "Cash"}]
    S["Custody Expenses"] = [
        {"custody": "CU-D01", "report": "1", "date": d(2, 20), "description": "Fittings and fixings", "amount": 4200, "account": "5010",
         "bu_code": "PRJ-D01", "supplier": "Local hardware shop", "receipt": "R-118"},
        {"custody": "CU-D01", "report": "1", "date": d(2, 22), "description": "Taxi for samples", "amount": 350, "account": "6075"},
        {"custody": "CU-D01", "report": "1", "date": d(2, 25), "description": "Grout and silicone", "amount": 2280, "account": "5010",
         "vat_code": "VAT14", "bu_code": "PRJ-D01"},
        {"custody": "CU-D02", "report": "1", "date": d(3, 30), "description": "Office supplies", "amount": 1450, "account": "6080"},
        {"custody": "CU-D02", "report": "1", "date": d(3, 30), "description": "Coffee & hospitality", "amount": 900, "account": "6090"}]
    S["Rental Equipment"] = [
        {"code": "SC-FRM2", "name": "Frame 2 m", "name_ar": "شباك 2 م", "category": "scaffolding", "unit": "piece", "monthly_rate": 60,
         "loss_price": 1100, "std_cost": 900, "weight": 14, "life_months": 120, "residual_pct": 10, "opening_qty": 300,
         "opening_unit_cost": 900, "opening_date": d(0, 1)},
        {"code": "SC-BRC3", "name": "Cross brace 3 m", "name_ar": "مقص 3 متر", "category": "scaffolding", "unit": "piece", "monthly_rate": 20,
         "loss_price": 380, "std_cost": 320, "life_months": 120, "residual_pct": 10, "opening_qty": 500, "opening_unit_cost": 320, "opening_date": d(0, 1)},
        {"code": "SC-BRD36", "name": "Walking board 3.6 m", "name_ar": "لوح بنط 3.6 متر", "category": "scaffolding", "unit": "piece", "monthly_rate": 25,
         "loss_price": 700, "std_cost": 600, "opening_qty": 150, "opening_unit_cost": 600, "opening_date": d(0, 1)},
        {"code": "SC-JACK", "name": "Screw jack", "name_ar": "فتيل", "category": "scaffolding", "unit": "piece", "monthly_rate": 10,
         "loss_price": 260, "std_cost": 220, "opening_qty": 200, "opening_unit_cost": 220, "opening_date": d(0, 1)},
        {"code": "SC-PLT60", "name": "Base plate 60 cm", "name_ar": "بليتة 60 سم", "category": "scaffolding", "unit": "piece", "monthly_rate": 8,
         "loss_price": 180, "std_cost": 150, "opening_qty": 120, "opening_unit_cost": 150, "opening_date": d(0, 1)},
        {"code": "SC-CLF", "name": "Fixed clamp", "name_ar": "كلبس ثابت", "category": "scaffolding", "unit": "piece", "monthly_rate": 5,
         "loss_price": 130, "std_cost": 110, "opening_qty": 300, "opening_unit_cost": 110, "opening_date": d(0, 1)},
        {"code": "SC-CLS", "name": "Swivel clamp", "name_ar": "كلبس متحرك", "category": "scaffolding", "unit": "piece", "monthly_rate": 5,
         "loss_price": 130, "std_cost": 110, "opening_qty": 300, "opening_unit_cost": 110, "opening_date": d(0, 1)},
        {"code": "SC-TUB6", "name": "Tube 2.8 mm × 6 m", "name_ar": "ماسورة 2.8 مم 6 متر", "category": "scaffolding", "unit": "piece",
         "monthly_rate": 30, "loss_price": 850, "std_cost": 750, "weight": 24, "opening_qty": 120, "opening_unit_cost": 750, "opening_date": d(0, 1)},
        {"code": "EL-HOIST", "name": "Construction hoist 1.5 t", "name_ar": "مصعد إنشائي", "category": "elevator", "unit": "unit",
         "monthly_rate": 28000, "loss_price": 0, "std_cost": 480000, "life_months": 120, "residual_pct": 15, "opening_qty": 1,
         "opening_unit_cost": 480000, "opening_date": d(0, 1)},
        {"code": "CR-MOT", "name": "Motorized cradle 6 m", "name_ar": "سقالة معلقة كهربائية", "category": "cradle_motorized", "unit": "unit",
         "monthly_rate": 12000, "std_cost": 190000, "opening_qty": 2, "opening_unit_cost": 190000, "opening_date": d(0, 1)}]
    S["Rental Stock Lots"] = [{"item": "SC-FRM2", "date": d(2, 5), "qty": 100, "unit_cost": 980, "source": "purchase",
                               "supplier": "Steelform Scaffolding", "paid": 60000}]
    S["Rental Contracts"] = (
        [{"ref": "R1", "client": "Atlas Builders", "site": "Tower B", "site_location": "New Capital", "quote_date": d(2, 20),
          "start_date": d(3, 1), "duration_months": 6, "min_days": 30, "deposit": 20000, "line_kind": "item", "item": it, "qty": q, "rate": rt,
          "po_number": "PO-ATL-77", "signed_client": "Eng. Hany (Atlas)", "signed_us": "Hossam Adel"} for it, q, rt in (("SC-FRM2", 200, 60), ("SC-BRC3", 300, 20),
                                                                                          ("SC-BRD36", 80, 25), ("SC-JACK", 100, 10))] +
        [{"ref": "R1", "client": "Atlas Builders", "line_kind": "charge", "charge_type": "install", "description": "Installation", "qty": 1, "rate": 15000},
         {"ref": "R1", "client": "Atlas Builders", "line_kind": "charge", "charge_type": "transport", "description": "Transport", "qty": 2, "rate": 3000},
         {"ref": "R2", "client": "Delta Tech Solutions", "site": "HQ façade", "quote_date": d(4, 20), "duration_months": 3, "line_kind": "item",
          "item": "CR-MOT", "qty": 1, "rate": 12000},
         {"ref": "R2", "client": "Delta Tech Solutions", "line_kind": "item", "item": "EL-HOIST", "qty": 1, "rate": 28000}])
    S["Sales Contracts"] = [
        {"code": "SO-D001", "date": d(1, 10), "customer": "Mona Adel", "bu_code": "SALES-01", "product": "U-A101", "plan": "installments",
         "down_pct": 10, "n": 24, "frequency": "monthly", "first_due": d(2, 10), "interest": 8, "revenue_on": "delivery", "confirm": "Yes",
         "collected": 240000},
        {"code": "SO-D002", "date": d(3, 3), "customer": "Atlas Builders", "bu_code": "SALES-01", "product": "G-TILE", "qty": 120,
         "discount": 2400, "vat_code": "VAT14", "plan": "full", "confirm": "Yes", "collected": 76380},
        {"code": "SO-D002", "date": d(3, 3), "customer": "Atlas Builders", "bu_code": "SALES-01", "product": "G-SAN", "qty": 2},
        {"code": "SO-D003", "date": d(4, 18), "customer": "Tamer Nabil", "bu_code": "SALES-01", "product": "U-A204", "plan": "installments",
         "down_pct": 15, "n": 8, "frequency": "quarterly", "first_due": d(5, 18), "interest": 6, "revenue_on": "sale",
         "confirm": "Yes", "collected": 397500},
        {"code": "SO-D004", "date": d(4, 22), "customer": "Karim Fahmy", "bu_code": "SALES-01", "product": "S-DES", "qty": 2,
         "vat_code": "VAT14", "plan": "deferred", "first_due": d(5, 28), "confirm": "Yes"}]
    S["Budgets"] = [{"year": today.year, "account": acct, "total": tot, "notes": note} for acct, tot, note in (
        ("4010", 4200000, "Contract revenue target"), ("4025", 3000000, "Unit sales"), ("5005", 2200000, ""),
        ("5010", 900000, ""), ("6010", 300000, "Salaries"), ("6020", 24000, "Internet & phones"), ("6040", 30000, "Electricity"))] + [
        {"year": today.year, "account": "6030", "bu_code": "", **{f"m{i}": 3000 for i in range(1, 13)}, "notes": "Office rent"}]
    S["Recurring Entries"] = [
        {"name": "Office rent {month}", "doc_type": "expense", "start": eom(4), "every": 1, "unit": "month", "month_end": "Yes",
         "mode": "auto", "amount": 25000, "description": "Office rent {month}", "account": "6010", "party": "Demo Properties"},
        {"name": "Electricity", "doc_type": "expense", "start": d(5, 12), "unit": "month", "mode": "review", "amount": 2600,
         "description": "Electricity {month}", "account": "6040", "party": "Cairo Power Co."},
        {"name": "Monthly depreciation", "doc_type": "depreciation", "start": eom(5), "unit": "month", "month_end": "Yes", "mode": "auto"},
        {"name": "Accrued audit fee", "doc_type": "journal", "start": eom(4), "unit": "month", "every": 3, "month_end": "Yes", "mode": "auto",
         "amount": 4000, "description": "Audit fee accrual {month}", "account": "6060", "credit_account": "2030"}]
    return S


def is_empty(db):
    return not db.one("SELECT 1 FROM journal_entries") and not db.one("SELECT 1 FROM business_units")


def _bank_lines(db, account, date_from, date_to, skip_last=True):
    """Statement lines as the bank would show them: the book's movements on the account (+ fees and interest)."""
    rows = db.q("""SELECT e.id, e.date, e.memo, e.party, e.ref, SUM(l.debit-l.credit) amt FROM journal_lines l
                   JOIN journal_entries e ON e.id=l.entry_id WHERE e.status='posted' AND l.account=? AND e.date BETWEEN ? AND ?
                   GROUP BY e.id HAVING amt<>0 ORDER BY e.date, e.id""", (account, date_from, date_to))
    if skip_last:     # the last payment is still in transit at the bank
        outs = [r for r in rows if r["amt"] < 0]
        if outs:
            rows = [r for r in rows if r["id"] != outs[-1]["id"]]
    out = [{"date": r["date"], "description": (r["memo"] or r["party"] or "")[:80], "ref": r["ref"] or "", "amount": r["amt"] / 100}
           for r in rows]
    out.append({"date": date_to, "description": "BANK CHARGES", "ref": "", "amount": -85.0})
    out.append({"date": date_to, "description": "CREDIT INTEREST", "ref": "", "amount": 120.0})
    return out


def load(db, user="", replace=False):
    """Fill the books with the demo company. Refuses on books that already hold data unless replace (wipes first)."""
    from . import (assets, bankrec, documents, interchange, recurring, rentals, sales)
    if not is_empty(db) and not replace:
        raise ValidationError("These books already hold data — load the demo into an empty company, or choose to replace everything")
    a0, today = _anchor()
    S = sheets()
    with db.tx():
        res = interchange.import_data(db, S, mode="replace" if replace else "append", user=user)
        # ---- steps that only exist on screens
        # a negotiation: keep the discount as earnings on the cost-plus villa
        db.conn.execute("UPDATE business_units SET negotiation='keep' WHERE code='PRJ-D01'")
        acc.record_project_cost(db, {"date": S["Direct Costs"][7]["date"], "bu_code": "PRJ-D01", "account": "5010",
                                     "description": "Sanitary ware (negotiated)", "vendor": "Cleopatra Tiles Trading", "qty": 1,
                                     "rate": 58000, "savings": 6000, "paid": 52000, "negotiation": "keep",
                                     "deal_ref": "CLEO-02", "deal_status": "Settled"}, user)
        # a supplier deal settled for less than owed
        acc.record_supplier_discount(db, {"date": S["Direct Costs"][6]["date"], "party": "Hassan Aluminium", "amount": 2000,
                                          "bu_code": "PRJ-D01", "deal_ref": "HASSAN-01", "memo": "Final account discount"}, user)
        # partner equity events: a partner loan, part repaid, a share transfer
        from . import equity
        equity.post(db, "loan", {"partner": "Omar Fouad", "amount": 100000, "rate": 10, "date": S["Payroll"][2]["date"],
                                 "method": "Bank Transfer"}, user)
        equity.post(db, "repay", {"partner": "Omar Fouad", "amount": 20000, "date": S["Payroll"][3]["date"],
                                  "method": "Bank Transfer"}, user)
        equity.post(db, "transfer", {"seller": "Hossam Adel", "buyer": "Sara Kamal", "share": 5, "date": S["Payroll"][3]["date"]}, user)
        # an asset bought for cash, one sold
        assets.save(db, {"name": "Laser level & tools", "account": "1410", "acquired": S["Payroll"][1]["date"], "cost": 42000,
                         "life_months": 36, "pay": "cash", "method": "Bank Transfer"}, user=user)
        last_month = (today.replace(day=1) - _dt.timedelta(days=1)).isoformat()[:7]
        assets.run_depreciation(db, last_month, user=user)
        try:
            rentals.run_depreciation(db, last_month, user=user)
        except ValidationError:
            pass
        rentals.starter(db)  # the remaining handover-form pieces, the frame set and the scaffold calculator
        # rental: deliver contract R1 on a signed handover, take the deposit, invoice the first month
        r1 = db.one("SELECT id FROM rentals WHERE client='Atlas Builders' ORDER BY id LIMIT 1")["id"]
        rr = rentals.get(db, r1)
        rentals.deposit(db, rr, {"type": "receive", "amount": 20000, "date": rr["start_date"] or acc.today(), "method": "Bank Transfer"}, user)
        t = rentals.handover_template(db, r1, "delivery")
        t.update(signed=1, decision="safe", date=rr["start_date"], checklist=[dict(c, ok="yes") for c in t["checklist"]])
        rentals.confirm_handover(db, rentals.save_handover(db, r1, t))
        first = rr["start_date"]
        end = (_dt.date.fromisoformat(first).replace(day=1) + _dt.timedelta(days=40)).replace(day=1) - _dt.timedelta(days=1)
        xid = rentals.create_invoice(db, r1, {"from": first, "to": min(end.isoformat(), acc.today())}, user)
        acc.post_extract(db, xid if isinstance(xid, int) else xid["id"], user=user)
        # sales: cheques for the apartment (one cleared, one bounced), deliver the goods sale, a design offer
        s1 = sales.get(db, "SO-D001")
        det = sales.detail(db, s1["id"])
        inst = [r for r in det["schedule"] if r["kind"] == "installment"][:3]
        sales.collect(db, s1["id"], {"date": s1["date"], "method": "Cheque",
                                     "cheques": [{"number": f"30{i}", "bank": "Demo Bank", "due": r["due"], "amount": r["amount"]}
                                                 for i, r in enumerate(inst, 1)]}, user)
        ch = db.q("SELECT id, due FROM sale_cheques WHERE sale_id=? ORDER BY due", (s1["id"],))
        if ch and ch[0]["due"] <= acc.today():
            sales.cheque(db, ch[0]["id"], "clear", {"date": ch[0]["due"]}, user)
        if len(ch) > 1 and ch[1]["due"] <= acc.today():
            sales.cheque(db, ch[1]["id"], "bounce", {"date": ch[1]["due"]}, user)
        s2 = sales.get(db, "SO-D002")
        sales.deliver(db, s2["id"], {"date": s2["date"]}, user)
        p = sales.get_product(db, "U-A101")
        for col, title in (((180, 140, 100), "Front view"), ((90, 120, 160), "Living room")):
            sales.add_media(db, p["id"], {"kind": "photo", "title": title, "data": _png(col), "thumb": _png(col, 48, 32)})
        sales.add_media(db, p["id"], {"kind": "video", "title": "Walkthrough (link)", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        sales.add_media(db, p["id"], {"kind": "model", "title": "3D model (link)", "url": "https://modelviewer.dev/shared-assets/models/Astronaut.glb"})
        for code, col in (("U-A204", (120, 160, 120)), ("U-V01", (200, 170, 90)), ("G-TILE", (170, 170, 170)), ("G-SAN", (230, 230, 240))):
            sales.add_media(db, sales.get_product(db, code)["id"], {"kind": "photo", "data": _png(col), "thumb": _png(col, 48, 32)})
        types = documents.list_offer_types(db)
        if types:
            ty = types[0]
            documents.save_offer(db, {**{k: v for k, v in ty.items() if k not in ("id", "name")}, "type_id": ty["id"],
                                      "client": "Laila Samir", "project": "Apartment 14 — design package", "date": S["BOQ Lines"][0]["date"],
                                      "status": "Sent"})
        # recurring entries due by now
        recurring.run(db, acc.today(), user)
        # bank: last month reconciled, this month open
        bank = db.acct("bank")
        lm_from = (today.replace(day=1) - _dt.timedelta(days=1)).replace(day=1).isoformat()
        lm_to = (today.replace(day=1) - _dt.timedelta(days=1)).isoformat()
        opening = bankrec.book_balance(db, bank, (_dt.date.fromisoformat(lm_from) - _dt.timedelta(days=1)).isoformat()) / 100
        lines = _bank_lines(db, bank, lm_from, lm_to, skip_last=False)
        closing = round(opening + sum(x["amount"] for x in lines), 2)
        sid = bankrec.save(db, {"account": bank, "name": f"Demo Bank {lm_from[:7]}", "date_from": lm_from, "date_to": lm_to,
                                "opening": opening, "closing": closing, "book_from": lm_from, "lines": lines})
        bankrec.auto_match(db, sid)
        for ln in db.q("SELECT id, description FROM bank_lines WHERE statement_id=? AND status='open'", (sid,)):
            if "CHARGES" in ln["description"]:
                bankrec.create_entry(db, sid, ln["id"], {"account": db.acct("bank_charges"), "remember": 1, "pattern": "BANK CHARGES"}, user)
            elif "INTEREST" in ln["description"]:
                bankrec.create_entry(db, sid, ln["id"], {"account": db.acct("interest_income")}, user)
            else:
                bankrec.ignore(db, sid, ln["id"])
        try:
            bankrec.reconcile(db, sid, {"date": lm_to}, user)
        except ValidationError:
            pass
        tm_from = today.replace(day=1).isoformat()
        if tm_from <= acc.today():
            lines = _bank_lines(db, bank, tm_from, acc.today())
            op2 = closing
            bankrec.save(db, {"account": bank, "name": f"Demo Bank {tm_from[:7]}", "date_from": tm_from, "date_to": acc.today(),
                              "opening": op2, "closing": round(op2 + sum(x["amount"] for x in lines), 2), "lines": lines})
        db.conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (TAG, acc.today()))
    return {"counts": res["counts"], "from": a0.isoformat(), "to": today.isoformat()}


def all_sheets(db_factory):
    """Every layout filled, including bank statement lines (the bank's view of the demo's own movements)."""
    S = sheets()
    scratch = db_factory()
    load(scratch)
    st = scratch.one("SELECT * FROM bank_statements WHERE status='open' ORDER BY date_to DESC LIMIT 1")
    if st:
        S["Bank Statement Lines"] = [{"account": st["account"], "statement": st["name"] + " (demo file)", "date": ln["date"],
                                      "description": ln["description"], "ref": ln["ref"], "amount": ln["amount"] / 100,
                                      "opening": st["opening"] / 100 if i == 0 else "", "closing": st["closing"] / 100 if i == 0 else ""}
                                     for i, ln in enumerate(scratch.q("SELECT * FROM bank_lines WHERE statement_id=? ORDER BY date, seq",
                                                                      (st["id"],)))]
    return S


def workbook_bytes(db_factory):
    """The demo as one Excel workbook in the import layout (every sheet filled) — import it from Settings → Data."""
    from . import workbook
    return workbook.build_workbook(db_factory(), template=True, data=all_sheets(db_factory))
