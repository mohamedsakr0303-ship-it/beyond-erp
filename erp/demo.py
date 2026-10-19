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


def sheets(industry=None):
    """Every import layout filled with the demo company (dates end today); with a business type, only what that kind of
    business does (see PROFILES)."""
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
                     {"key": "default:company_address", "value": "12 Demo Street, East District"},
                     {"key": "default:company_phone", "value": "+20 100 000 0000"},
                     {"key": "default:company_legal_name", "value": "Nile Demo Co. (fictitious)"},
                     {"key": "default:company_tax_id", "value": "999888777"}]
    S["Business Units"] = [
        {"code": "PRJ-D01", "name": "Villa 7 — Mr. Karim Fahmy", "type": "Villa", "location": "East District", "area": 420,
         "start_date": d(0, 3), "status": "In Progress", "basis": "Cost-Plus %", "fee_pct": 0.25, "overhead_pct": 0.35,
         "customer": "Karim Fahmy"},
        {"code": "PRJ-D02", "name": "Apartment 14 — Ms. Laila Samir", "type": "Apartment", "location": "West District", "area": 180,
         "start_date": d(1, 1), "status": "In Progress", "basis": "Fixed Price", "contract_value": 950000, "overhead_pct": 0.3,
         "customer": "Laila Samir"},
        {"code": "PRJ-D03", "name": "Office fit-out — Delta Tech", "type": "Office", "location": "Business Park", "area": 600,
         "start_date": d(2, 10), "status": "In Progress", "basis": "Flexible / Settled", "overhead_pct": 0.2,
         "customer": "Delta Tech Solutions"},
        {"code": "SALES-01", "name": "Harbour Residence — units for sale", "type": "Real estate", "location": "North District",
         "start_date": d(0, 1), "status": "In Progress", "basis": "Fixed Price", "overhead_pct": 0.15}]
    S["Parties"] = [
        {"name": "Karim Fahmy", "type": "customer", "category": "customer", "receiver_type": "P", "national_id": "28501011234567",
         "phone": "01000000101", "governate": "Metro", "region": "East District"},
        {"name": "Laila Samir", "type": "customer", "category": "customer", "receiver_type": "P", "phone": "01000000102"},
        {"name": "Delta Tech Solutions", "type": "customer", "category": "customer", "receiver_type": "B", "tax_id": "100200300",
         "governate": "Metro", "region": "Business Park", "street": "Building B2", "building": "2", "default_vat": "VAT14",
         "default_wht": "WHT1-C", "email": "finance@deltatech.example"},
        {"name": "Atlas Builders", "type": "customer", "category": "customer", "receiver_type": "B", "tax_id": "200300400",
         "phone": "01000000104"},
        {"name": "El-Nour Building Materials", "type": "supplier", "category": "supplier", "phone": "01000000201",
         "specialty": "Cement, sand, bricks", "bank_details": "Demo Bank — 1000200030004", "default_vat": "VAT14"},
        {"name": "Tiles & Ceramics Supplier", "type": "supplier", "category": "supplier", "phone": "01000000202", "specialty": "Ceramics & porcelain"},
        {"name": "Hassan Aluminium", "type": "supplier", "category": "subcontractor", "phone": "01000000203", "specialty": "Aluminium & glass"},
        {"name": "Eng. Fathy Plumbing", "type": "supplier", "category": "subcontractor", "phone": "01000000204", "specialty": "Plumbing"},
        {"name": "Osama Mahmoud", "type": "supplier", "category": "driver", "phone": "01000000205", "vehicle": "Jumbo — DEMO 123"},
        {"name": "Spark Electric", "type": "supplier", "category": "technician", "phone": "01000000206", "specialty": "Electrical"},
        {"name": "Steelform Scaffolding", "type": "supplier", "category": "supplier", "phone": "01000000207"},
        {"name": "City Power Co.", "type": "supplier", "category": "supplier"},
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
    S["Warehouses"] = [{"name": "Site Store — Villa 7", "location": "East District"}]
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
                       "location": "West District", "category": c, "item_no": n, "description": t, "unit": u, "qty": q, "price": p}
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
        {"date": d(2, 3), "bu_code": "PRJ-D01", "account": "5010", "description": "Porcelain 60x60", "vendor": "Tiles & Ceramics Supplier", "deal_ref": "CLEO-01", "qty": 260, "unit": "m2", "rate": 610, "paid": 120000, "vat_code": "VAT14"},
        {"date": d(2, 18), "bu_code": "PRJ-D01", "account": "5020", "description": "Aluminium windows", "vendor": "Hassan Aluminium", "deal_ref": "HASSAN-01", "qty": 1, "unit": "LS", "rate": 135000, "savings": 10000, "paid": 60000, "wht_code": "WHT1-S"},
        {"date": d(3, 9), "bu_code": "PRJ-D01", "account": "5020", "description": "Electrical works", "vendor": "Spark Electric", "qty": 1, "unit": "LS", "rate": 72000, "paid": 72000},
        {"date": d(1, 8), "bu_code": "PRJ-D02", "account": "5040", "description": "Demolition", "vendor": "Osama Mahmoud", "qty": 120, "unit": "m2", "rate": 110, "paid": 13200, "method": "Cash"},
        {"date": d(1, 20), "bu_code": "PRJ-D02", "account": "5010", "description": "Bricks & mortar", "vendor": "El-Nour Building Materials", "qty": 1, "unit": "Lot", "rate": 38000, "paid": 38000},
        {"date": d(2, 15), "bu_code": "PRJ-D02", "account": "5030", "description": "Plaster crew", "vendor": "Site crew", "qty": 20, "unit": "day", "rate": 850, "paid": 17000, "method": "Cash"},
        {"date": d(3, 20), "bu_code": "PRJ-D02", "account": "5010", "description": "Porcelain for living room", "vendor": "Tiles & Ceramics Supplier", "qty": 160, "unit": "m2", "rate": 600, "paid": 0, "vat_code": "VAT14"},
        {"date": d(2, 22), "bu_code": "PRJ-D03", "account": "5020", "description": "Gypsum partitions", "vendor": "Hassan Aluminium", "qty": 1, "unit": "LS", "rate": 180000, "paid": 100000},
        {"date": d(3, 5), "bu_code": "PRJ-D03", "account": "5060", "description": "Transport of materials", "vendor": "Osama Mahmoud", "qty": 4, "unit": "trip", "rate": 1500, "paid": 6000, "method": "Cash"},
        {"date": d(4, 2), "bu_code": "PRJ-D03", "account": "5050", "description": "Scissor lift rental", "vendor": "Steelform Scaffolding", "qty": 1, "unit": "month", "rate": 14000, "paid": 14000}]
    S["Operating Expenses"] = (
        [{"date": eom(m), "account": "6010", "description": f"Office rent {d(m, 1)[:7]}", "vendor": "Demo Properties", "amount": 25000}
         for m in range(0, 4)] +
        [{"date": d(m, 17), "account": "6020", "description": "Design software subscription", "vendor": "SoftDemo", "amount": 2200,
          "method": "Credit Card"} for m in range(0, 5)] +
        [{"date": d(m, 12), "account": "6040", "description": f"Electricity {d(m, 1)[:7]}", "vendor": "City Power Co.",
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
         "vendor": "Tiles & Ceramics Supplier", "paid": 120000},
        {"date": d(1, 4), "type": "Receipt", "warehouse": "Main Store", "item": "Cement bag 50kg", "unit": "bag", "qty": 200, "rate": 95,
         "vendor": "El-Nour Building Materials", "paid": 19000},
        {"date": d(1, 5), "type": "Receipt", "warehouse": "Main Store", "item": "Bathroom set (basin + WC)", "unit": "set", "qty": 10,
         "rate": 6200, "vendor": "Tiles & Ceramics Supplier", "paid": 62000},
        {"date": d(1, 5), "type": "Receipt", "warehouse": "Main Store", "item": "Wall paint 18L", "unit": "drum", "qty": 20, "rate": 1150,
         "vendor": "El-Nour Building Materials", "paid": 23000},
        {"date": d(1, 18), "type": "Transfer", "warehouse": "Main Store", "to_warehouse": "Site Store — Villa 7", "item": "Cement bag 50kg", "qty": 80},
        {"date": d(1, 20), "type": "Issue", "warehouse": "Site Store — Villa 7", "item": "Cement bag 50kg", "qty": 60, "bu_code": "PRJ-D01", "account": "5010"},
        {"date": d(2, 10), "type": "Issue", "warehouse": "Main Store", "item": "Wall paint 18L", "qty": 6, "bu_code": "PRJ-D02", "account": "5010"},
        {"date": d(3, 28), "type": "Adjustment", "warehouse": "Main Store", "item": "Cement bag 50kg", "counted": 115, "qty": 1,
         "note": "Monthly count — 5 bags damaged"}]
    S["Journal Vouchers"] = [
        {"voucher": "JV-D00", "date": d(0, 1), "kind": "Journal Voucher", "memo": "Harbour Residence units completed — held for sale at cost",
         "account": "1210", "debit": 9400000},
        {"voucher": "JV-D00", "date": d(0, 1), "kind": "Journal Voucher", "party": "Demo Bank", "memo": "Development finance for Harbour Residence",
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
    S["Service Plans"] = [
        {"code": "SP-D01", "name": "Apartment maintenance — yearly", "name_ar": "صيانة شقة — سنوي", "kind": "maintenance", "price": 12000,
         "period_months": 12, "visits": 4, "extra_visit_price": 900, "vat_code": "VAT14",
         "description": "4 preventive visits a year: plumbing, electrical, AC filters; emergency calls at the extra-visit price"},
        {"code": "SP-D02", "name": "Apartment maintenance — monthly", "name_ar": "صيانة شقة — شهري", "kind": "maintenance", "price": 1200,
         "period_months": 1, "visits": 1, "extra_visit_price": 900, "vat_code": "VAT14"},
        {"code": "SP-D03", "name": "Extended warranty — kitchen appliances (2 years)", "name_ar": "ضمان ممتد — أجهزة المطبخ (سنتان)",
         "kind": "warranty", "price": 4800, "period_months": 24, "vat_code": "VAT14"},
        {"code": "SP-D04", "name": "Software support & hosting", "name_ar": "دعم فني واستضافة البرنامج", "kind": "software", "price": 2500,
         "period_months": 1, "vat_code": "VAT14"},
        {"code": "SP-D05", "name": "Extra module (add-on)", "name_ar": "موديول إضافي", "kind": "addon", "price": 600, "period_months": 1,
         "vat_code": "VAT14"}]
    S["Service Subscriptions"] = [
        {"code": "SUB-D01", "customer": "Tamer Nabil", "plan": "SP-D01", "covers": "Unit A-204", "start": d(1, 1),
         "term_months": 12, "auto_renew": "Yes"},
        {"code": "SUB-D02", "customer": "Laila Samir", "plan": "SP-D02", "covers": "Apartment 14, Zamalek", "start": d(2, 1)},
        {"code": "SUB-D03", "customer": "Tamer Nabil", "plan": "SP-D03", "covers": "Oven + hob, serials OV-88231 / HB-11873", "start": d(1, 15),
         "term_months": 24, "auto_renew": "No"},
        {"code": "SUB-D04", "customer": "Delta Tech Solutions", "plan": "SP-D04", "title": "ERP support & hosting", "covers": "BEYOND ERP — 10 users",
         "start": d(3, 1)},
        {"code": "SUB-D05", "customer": "Delta Tech Solutions", "plan": "SP-D05", "parent": "SUB-D04", "title": "Extra module — Rentals",
         "covers": "Rentals module", "start": d(4, 1)}]
    S["Purchase Orders"] = [{"code": "PO-D100", "date": d(4, 2), "supplier": "Tiles & Ceramics Supplier", "bu_code": "PRJ-D01",
                             "expected": d(4, 20), "vat_code": "VAT14", "kind": "cost", "description": desc, "account": "5010",
                             "qty": q, "unit": u, "rate": r, "terms": "Ex-works, 50 % advance", "approved": "Yes"}
                            for desc, q, u, r in (("Porcelain tiles 60×60", 320, "m2", 410), ("Tile skirting", 140, "m", 85))]
    S["Budgets"] = [{"year": today.year, "account": acct, "total": tot, "notes": note} for acct, tot, note in (
        ("4010", 4200000, "Contract revenue target"), ("4025", 3000000, "Unit sales"), ("5005", 2200000, ""),
        ("5010", 900000, ""), ("6010", 300000, "Salaries"), ("6020", 24000, "Internet & phones"), ("6040", 30000, "Electricity"))] + [
        {"year": today.year, "account": "6030", "bu_code": "", **{f"m{i}": 3000 for i in range(1, 13)}, "notes": "Office rent"}]
    S["Recurring Entries"] = [
        {"name": "Office rent {month}", "doc_type": "expense", "start": eom(4), "every": 1, "unit": "month", "month_end": "Yes",
         "mode": "auto", "amount": 25000, "description": "Office rent {month}", "account": "6010", "party": "Demo Properties"},
        {"name": "Electricity", "doc_type": "expense", "start": d(5, 12), "unit": "month", "mode": "review", "amount": 2600,
         "description": "Electricity {month}", "account": "6040", "party": "City Power Co."},
        {"name": "Monthly depreciation", "doc_type": "depreciation", "start": eom(5), "unit": "month", "month_end": "Yes", "mode": "auto"},
        {"name": "Accrued audit fee", "doc_type": "journal", "start": eom(4), "unit": "month", "every": 3, "month_end": "Yes", "mode": "auto",
         "amount": 4000, "description": "Audit fee accrual {month}", "account": "6060", "credit_account": "2030"}]
    # ---- projects, contracts & the site
    S["Employees"] = [
        {"code": "EMP-D01", "name": "Mahmoud Saber", "job_title": "Mason", "department": "Execution / Site", "emp_type": "daily",
         "cost_type": "Direct Labour (Cost of Revenue)", "bu_code": "PRJ-D01", "hire_date": d(0, 3), "daily_rate": 450, "insured": "No", "method": "Cash"},
        {"code": "EMP-D02", "name": "Ahmed Ragab", "job_title": "Plasterer", "department": "Execution / Site", "emp_type": "daily",
         "cost_type": "Direct Labour (Cost of Revenue)", "bu_code": "PRJ-D01", "hire_date": d(0, 3), "daily_rate": 400, "insured": "No", "method": "Cash"},
        {"code": "EMP-D03", "name": "Youssef Ali", "job_title": "Electrician", "department": "Execution / Site", "emp_type": "hourly",
         "cost_type": "Direct Labour (Cost of Revenue)", "bu_code": "PRJ-D02", "hire_date": d(1, 1), "hourly_rate": 60, "insured": "No", "method": "Cash"},
        {"code": "EMP-D04", "name": "Nadia Hosny", "job_title": "Site Engineer", "department": "Execution / Site", "emp_type": "monthly",
         "cost_type": "Indirect Labour (Cost-Centre Overhead)", "bu_code": "PRJ-D01", "hire_date": d(0, 1), "basic": 18000,
         "al_transport": 1500, "insured": "Yes", "method": "Bank Transfer"}]
    S["Project Phases"] = [{"bu_code": "PRJ-D01", "seq": i + 1, "name": n, "weight": w, "start": a, "finish": b, "progress": p}
                           for i, (n, w, a, b, p) in enumerate((("Site preparation & demolition", 5, d(0, 3), d(0, 15), 1),
                                                                ("Masonry & plastering", 25, d(0, 15), d(2, 10), 1),
                                                                ("Plumbing & electrical", 20, d(1, 5), d(3, 20), 0.8),
                                                                ("Flooring & tiling", 25, d(2, 1), d(4, 15), 0.5),
                                                                ("Painting & finishing", 15, d(4, 1), d(5, 25), 0.1),
                                                                ("Handover & snagging", 10, d(5, 20), d(5, 28), 0)))] + \
                          [{"bu_code": "PRJ-D02", "seq": i + 1, "name": n, "weight": w, "start": a, "finish": b, "progress": p}
                           for i, (n, w, a, b, p) in enumerate((("Demolition", 10, d(1, 1), d(1, 10), 1), ("Electrical", 30, d(1, 10), d(3, 1), 0.9),
                                                                ("Finishes", 60, d(2, 15), d(5, 25), 0.35)))]
    S["Project Tasks"] = [
        {"bu_code": "PRJ-D01", "phase": "Flooring & tiling", "title": "Approve tile samples with the client", "assignee": "Nadia Hosny", "due": d(3, 25),
         "priority": "high", "status": "done", "done_at": d(3, 24)},
        {"bu_code": "PRJ-D01", "phase": "Plumbing & electrical", "title": "Pressure-test the water lines", "assignee": "Nadia Hosny", "due": d(4, 5),
         "priority": "high", "status": "todo"},
        {"bu_code": "PRJ-D01", "phase": "Painting & finishing", "title": "Order paint (colour schedule)", "assignee": "Sara Kamal", "due": d(5, 28),
         "status": "doing"},
        {"bu_code": "PRJ-D02", "phase": "Finishes", "title": "Kitchen measurements for the joiner", "assignee": "Youssef Ali", "due": d(5, 26), "status": "todo"},
        {"title": "Renew the contractors' union membership", "assignee": "Sara Kamal", "due": d(5, 30), "priority": "low", "status": "todo"}]
    S["Contracts"] = (
        [{"code": "CC-D001", "kind": "client", "party": "Laila Samir", "bu_code": "PRJ-D02", "title": "Apartment 14 — full finishing",
          "value": 950000, "signed_on": d(0, 25), "start": d(1, 1), "finish": d(5, 28), "retention_pct": 0.05, "retention_release": d(5, 28),
          "payment_terms": "Monthly progress invoices, 5 % retention released on handover", "status": "active"},
         {"code": "CC-D002", "kind": "client", "party": "Delta Tech Solutions", "bu_code": "PRJ-D03", "title": "Office fit-out",
          "value": 1800000, "signed_on": d(2, 1), "start": d(2, 10), "finish": d(5, 30), "vat_code": "VAT14", "status": "active"}] +
        [{"code": "SC-D001", "kind": "sub", "party": "Eng. Fathy Plumbing", "bu_code": "PRJ-D01", "title": "Plastering — Villa 7",
          "signed_on": d(0, 20), "start": d(1, 1), "finish": d(5, 15), "retention_pct": 0.05, "retention_release": d(5, 20),
          "advance": 20000, "advance_recovery_pct": 0.2, "wht_code": "WHT1-S", "payment_terms": "Monthly certificates, paid within 15 days",
          "status": "active", "item_no": no, "description": desc, "unit": u, "qty": q, "rate": r} if i == 0 else
         {"code": "SC-D001", "kind": "sub", "party": "Eng. Fathy Plumbing", "item_no": no, "description": desc, "unit": u, "qty": q, "rate": r}
         for i, (no, desc, u, q, r) in enumerate((("1", "Internal plaster", "m2", 1200, 65), ("2", "External plaster", "m2", 450, 85),
                                                  ("3", "Gypsum ceilings", "m2", 300, 160)))])
    S["Variation Orders"] = [
        {"contract": "CC-D001", "code": "VO-01", "date": d(3, 5), "description": "Extra built-in wardrobe in the master bedroom", "amount": 45000,
         "days": 10, "status": "approved", "decided_by": "Laila Samir"},
        {"contract": "CC-D001", "code": "VO-02", "date": d(5, 2), "description": "Upgrade to imported sanitary ware", "amount": 28000, "status": "pending"},
        {"contract": "SC-D001", "code": "VO-01", "date": d(3, 10), "description": "Plaster for the added roof room", "amount": 9500, "days": 5,
         "status": "approved", "decided_by": "admin"}]
    S["Subcontractor Certificates"] = (
        [{"contract": "SC-D001", "number": 1, "date": eom(2), "period_to": eom(2), "description": dsc, "qty_to_date": q, "status": "posted"}
         for dsc, q in (("Internal plaster", 500), ("External plaster", 100))] +
        [{"contract": "SC-D001", "number": 2, "date": eom(4), "period_to": eom(4), "description": dsc, "qty_to_date": q, "status": "approved"}
         for dsc, q in (("Internal plaster", 950), ("External plaster", 300), ("Gypsum ceilings", 80))])
    S["Subcontract Payments"] = [{"contract": "SC-D001", "date": d(1, 2), "kind": "advance", "amount": 20000, "method": "Bank Transfer"},
                                 {"contract": "SC-D001", "date": d(3, 10), "kind": "payment", "amount": 20000, "method": "Bank Transfer"}]
    S["Purchase Requests"] = (
        [{"code": "PR-D001", "date": d(4, 20), "requested_by": "Nadia Hosny", "bu_code": "PRJ-D01", "needed_by": d(5, 5), "status": "approved",
          "kind": "cost", "description": "Wall paint 18L (white)", "qty": 30, "unit": "drum", "est_rate": 1450, "notes": "Painting phase"},
         {"code": "PR-D001", "date": d(4, 20), "kind": "cost", "description": "Primer 18L", "qty": 12, "unit": "drum", "est_rate": 1100}] +
        [{"code": "PR-D002", "date": d(5, 18), "requested_by": "Mahmoud Saber", "warehouse": "Main Store", "needed_by": d(5, 30), "status": "submitted",
          "kind": "stock", "description": "Cement bag 50kg", "qty": 150, "unit": "bag", "est_rate": 100}])
    S["Supplier Quotes"] = [
        {"request": "PR-D001", "supplier": "El-Nour Building Materials", "date": d(4, 22), "delivery_days": 3, "description": "Wall paint 18L (white)", "rate": 1420},
        {"request": "PR-D001", "supplier": "El-Nour Building Materials", "description": "Primer 18L", "rate": 1150},
        {"request": "PR-D001", "supplier": "Colours House", "date": d(4, 23), "delivery_days": 7, "terms": "50 % advance", "description": "Wall paint 18L (white)", "rate": 1380},
        {"request": "PR-D001", "supplier": "Colours House", "description": "Primer 18L", "rate": 1060}]
    S["Equipment"] = [
        {"code": "EQ-D01", "name": "Concrete mixer 350 L", "category": "Concrete mixer", "serial": "MX-3350-17", "hourly_rate": 120,
         "meter_start": 1450, "bu_code": "PRJ-D01", "status": "in_use"},
        {"code": "EQ-D02", "name": "Diesel generator 30 kVA", "category": "Generator", "serial": "GEN-30-552", "hourly_rate": 90,
         "meter_start": 3200, "status": "available"},
        {"code": "EQ-D03", "name": "Site pickup truck", "category": "Truck", "serial": "DEMO 456", "asset_code": "FA-D01", "hourly_rate": 150,
         "meter_start": 0, "status": "available"}]
    S["Maintenance Plans"] = [
        {"equipment": "EQ-D01", "title": "Greasing & drum check", "every_hours": 100, "start_date": d(0, 3)},
        {"equipment": "EQ-D02", "title": "Oil & filter change", "every_hours": 250, "every_days": 90, "start_date": d(0, 1)},
        {"equipment": "EQ-D03", "title": "Service (10,000 km)", "every_days": 120, "start_date": d(0, 5)}]
    S["Equipment Usage"] = [
        {"equipment": "EQ-D01", "date": d(1, 18), "bu_code": "PRJ-D01", "hours": 26, "operator": "Mahmoud Saber", "fuel_qty": 40, "fuel_cost": 520, "method": "Cash"},
        {"equipment": "EQ-D01", "date": d(3, 8), "bu_code": "PRJ-D01", "hours": 34, "operator": "Mahmoud Saber", "fuel_qty": 55, "fuel_cost": 715, "method": "Cash"},
        {"equipment": "EQ-D02", "date": d(2, 12), "bu_code": "PRJ-D02", "hours": 60, "fuel_qty": 210, "fuel_cost": 2730, "method": "Cash"},
        {"equipment": "EQ-D03", "date": d(4, 3), "bu_code": "PRJ-D03", "hours": 12, "operator": "Driver", "fuel_qty": 60, "fuel_cost": 780, "method": "Cash"}]
    S["Maintenance Log"] = [
        {"equipment": "EQ-D02", "date": d(2, 20), "plan": "Oil & filter change", "kind": "service", "description": "Oil, oil filter, fuel filter",
         "hours_at": 3260, "cost": 1850, "vendor": "Power Gen Services", "method": "Cash"},
        {"equipment": "EQ-D01", "date": d(3, 15), "kind": "repair", "description": "Replaced drum bearing", "cost": 2400, "vendor": "Ali Workshop",
         "on_credit": "Yes"}]
    S["Attendance"] = []
    for k in range(1, 6):
        day = d(5, k)
        S["Attendance"] += [
            {"date": day, "employee": "EMP-D01", "status": "present", "bu_code": "PRJ-D01", "hours": 8, "overtime": 2 if k == 2 else 0,
             "qty": 18 + k, "unit": "m2", "task": "Block work"},
            {"date": day, "employee": "EMP-D02", "status": "absent" if k == 3 else "present", "bu_code": "PRJ-D01", "hours": 8,
             "qty": 0 if k == 3 else 30, "unit": "m2", "task": "Plaster"},
            {"date": day, "employee": "EMP-D03", "status": "half" if k == 5 else "present", "bu_code": "PRJ-D02", "hours": 4 if k == 5 else 9,
             "task": "Electrical second fix"},
            {"date": day, "employee": "EMP-D04", "status": "leave" if k == 4 else "present", "bu_code": "PRJ-D01", "hours": 8}]
    S["E-Receipts"] = [
        {"number": "R-D000001", "date": d(5, 3), "time": "10:15", "kind": "sale", "buyer_type": "P", "buyer_name": "Walk-in customer",
         "payment_method": "C", "description": "Tile adhesive 25 kg", "code": "EG-999888777-TA25", "unit": "EA", "qty": 4, "price": 180,
         "vat_code": "VAT14", "issue": "Yes"},
        {"number": "R-D000001", "date": d(5, 3), "description": "Grout 5 kg", "code": "EG-999888777-GR05", "unit": "EA", "qty": 2, "price": 95, "vat_code": "VAT14"},
        {"number": "R-D000002", "date": d(5, 4), "time": "12:40", "kind": "sale", "buyer_type": "P", "buyer_name": "Hany Adel", "payment_method": "V",
         "description": "Wall paint 18L", "code": "EG-999888777-PN18", "unit": "EA", "qty": 1, "price": 1650, "discount": 50, "vat_code": "VAT14",
         "issue": "Yes"},
        {"number": "R-D000003", "date": d(5, 5), "time": "09:05", "kind": "return", "ref_number": "R-D000001", "buyer_type": "P",
         "payment_method": "C", "description": "Grout 5 kg", "code": "EG-999888777-GR05", "unit": "EA", "qty": 1, "price": 95, "vat_code": "VAT14",
         "issue": "Yes"}]
    if industry in PROFILES:
        S = _trim(S, industry, d, eom)
    return S


# ---------------------------------------------------------------- one demo per business type
# The full demo touches every screen; a company that picked a business type sees only what that kind of business does:
# its own projects / contracts / units / rentals / shop, plus what every company has (partners, bank, payroll, expenses,
# loans, taxes). Rows are trimmed by business unit and by code, and whatever pointed at a trimmed row goes with it.
_GROUP = {"Contracts": "code", "Purchase Requests": "code", "Purchase Orders": "code", "Sales Contracts": "code",
          "Rental Contracts": "ref", "BOQ Lines": "title", "Journal Vouchers": "voucher", "E-Receipts": "number"}
_REFS = [("Invoice Lines", "number", "Sales Invoices", "number"), ("Sales Invoices", "boq_title", "BOQ Lines", "title"),
         ("Loan Transactions", "loan", "Loans", "code"), ("Custody Expenses", "custody", "Cash Custody", "code"),
         ("Variation Orders", "contract", "Contracts", "code"), ("Subcontractor Certificates", "contract", "Contracts", "code"),
         ("Subcontract Payments", "contract", "Contracts", "code"), ("Supplier Quotes", "request", "Purchase Requests", "code"),
         ("Maintenance Plans", "equipment", "Equipment", "code"), ("Equipment Usage", "equipment", "Equipment", "code"),
         ("Maintenance Log", "equipment", "Equipment", "code"), ("Attendance", "employee", "Employees", "code"),
         ("Service Subscriptions", "plan", "Service Plans", "code"), ("Service Subscriptions", "parent", "Service Subscriptions", "code"),
         ("Rental Stock Lots", "item", "Rental Equipment", "code"), ("Sales Contracts", "product", "Products", "code"),
         ("Project Tasks", "phase", "Project Phases", "name")]
_PROJECT_SHEETS = ["BOQ Lines", "Sales Invoices", "Invoice Lines", "Price List", "Contracts", "Variation Orders",
                   "Subcontractor Certificates", "Subcontract Payments", "Project Phases", "Project Tasks", "Purchase Requests",
                   "Supplier Quotes", "Purchase Orders"]
_RENTAL_SHEETS = ["Rental Equipment", "Rental Stock Lots", "Rental Contracts"]
_SHOP_SHEETS = ["Products", "Sales Contracts", "Service Plans", "Service Subscriptions", "E-Receipts"]
_SITE_SHEETS = ["Equipment", "Maintenance Plans", "Equipment Usage", "Maintenance Log", "Employees", "Attendance"]
_STOCK_SHEETS = ["Warehouses", "Stock Items", "Stock Moves"]

_CARE_ITEMS = (("GLOVES", "Examination gloves (pair)", "pair", 100), ("SYRINGE", "Syringe 5 ml", "pcs", 100),
               ("CANNULA", "IV cannula 22G", "pcs", 40), ("DRESS", "Sterile dressing pack", "pack", 40))
_CARE_DROP = (_PROJECT_SHEETS + _RENTAL_SHEETS + _SHOP_SHEETS + _SITE_SHEETS +
              ["Direct Costs", "Receipt Vouchers", "Cash Custody", "Custody Expenses"])
PROFILES = {
    "interior": {"name": "Nile Interiors (demo)", "tagline": "Interior design · Fit-out · Finishing",
                 "bus": {"PRJ-D01", "PRJ-D02", "PRJ-D03"}, "drop": _RENTAL_SHEETS + _SHOP_SHEETS + ["Equipment", "Maintenance Plans",
                                                                                                   "Equipment Usage", "Maintenance Log"],
                 "codes": {"Journal Vouchers": {"JV-D0C", "JV-D01", "JV-D02"}},
                 "budget": {"4010", "5010", "6010", "6020", "6040", "6030"}},
    "contracting": {"name": "Nile Contracting (demo)", "tagline": "General contracting · Construction",
                    "bus": {"PRJ-D01", "PRJ-D02", "PRJ-D03"}, "drop": _RENTAL_SHEETS + _SHOP_SHEETS,
                    "codes": {"Journal Vouchers": {"JV-D0C", "JV-D01", "JV-D02"}},
                    "budget": {"4010", "5010", "6010", "6020", "6040", "6030"}},
    "development": {"name": "Nile Developments (demo)", "tagline": "Real-estate development · Units for sale",
                    "bus": {"SALES-01"}, "drop": _PROJECT_SHEETS + _RENTAL_SHEETS + _SITE_SHEETS + _STOCK_SHEETS + ["E-Receipts"],
                    "codes": {"Products": {"U-A101", "U-A204", "U-V01", "U-S03"}, "Service Plans": {"SP-D01", "SP-D02", "SP-D03"},
                              "Journal Vouchers": {"JV-D00", "JV-D0C", "JV-D01"}, "Fixed Assets": {"FA-D01", "FA-D03"}},
                    "budget": {"4025", "6010", "6020", "6040", "6030"}},
    "sales": {"name": "Nile Trading (demo)", "tagline": "Building materials · Showroom & retail",
              "bus": {"SALES-01"}, "drop": _PROJECT_SHEETS[:-3] + ["Supplier Quotes"] + _RENTAL_SHEETS + _SITE_SHEETS,
              "codes": {"Products": {"G-TILE", "G-SAN", "S-DES"}, "Service Plans": {"SP-D03"}, "Purchase Requests": {"PR-D002"},
                        "Warehouses": set(), "Journal Vouchers": {"JV-D0C", "JV-D01"},
                        "Fixed Assets": {"FA-D01", "FA-D03"}},
              "rename": {"SALES-01": ("Showroom — Uptown", "Branch")},
              "budget": {"4025", "6010", "6020", "6040", "6030"}},
    "scaffolding": {"name": "Nile Scaffolding (demo)", "tagline": "Scaffolding · Hoists · Cradles — rental",
                    "bus": set(), "drop": _PROJECT_SHEETS + _SHOP_SHEETS + _STOCK_SHEETS + ["Employees", "Attendance"],
                    "codes": {"Equipment": {"EQ-D02", "EQ-D03"}, "Fixed Assets": {"FA-D01", "FA-D03"},
                              "Journal Vouchers": {"JV-D0C", "JV-D01", "JV-S01", "JV-S02", "JV-S03"}},
                    "budget": {"6010", "6020", "6040", "6030"}},
    "investment": {"name": "Nile Holdings (demo)", "tagline": "Investments · Holdings",
                   "bus": set(), "drop": _PROJECT_SHEETS + _RENTAL_SHEETS + _SHOP_SHEETS + _SITE_SHEETS + _STOCK_SHEETS +
                   ["Cash Custody", "Custody Expenses"],
                   "codes": {"Fixed Assets": {"FA-D03"}, "Journal Vouchers": {"JV-D01", "JV-I01", "JV-I02", "JV-I03", "JV-I04", "JV-I05", "JV-I06"}},
                   "budget": {"6010", "6040"}},
    "general": {"name": "Nile Services (demo)", "tagline": "Services · Trading",
                "bus": {"PRJ-D03"}, "drop": _RENTAL_SHEETS + _SHOP_SHEETS + _SITE_SHEETS + ["BOQ Lines", "Price List", "Contracts",
                                                                                           "Variation Orders", "Project Phases", "Project Tasks"],
                "codes": {"Journal Vouchers": {"JV-D0C", "JV-D01"}},
                "rename": {"PRJ-D03": ("Client work — Delta Tech", "Client account")},
                "budget": {"6010", "6020", "6040", "6030"}},
    "healthcare": {"name": "Nile Care Clinics (demo)", "tagline": "Outpatient clinics · Lab · Radiology · Physiotherapy",
                   "bus": {"CL-SOUTH", "CL-UPTOWN"}, "drop": _CARE_DROP, "codes": {"Journal Vouchers": {"JV-D0C", "JV-D01"}},
                   "budget": {"6010", "6020", "6040", "6030"}},
    "homecare": {"name": "Nile Home Care (demo)", "tagline": "Doctors, nurses & physiotherapy at home · Elderly care",
                 "bus": {"HC-EAST", "HC-WEST"}, "drop": _CARE_DROP, "codes": {"Journal Vouchers": {"JV-D0C", "JV-D01"}},
                 "budget": {"6010", "6020", "6040", "6030"}},
}
INDUSTRY_KEYS = list(PROFILES)


def _extra(S, key, d, eom):
    """What only that business does, added to the shared rows."""
    if key in ("healthcare", "homecare"):
        home = key == "homecare"
        S["Business Units"] = [{"code": c, "name": n, "type": "Branch", "location": loc, "start_date": d(0, 1), "status": "In Progress"}
                               for c, n, loc in ((("HC-EAST", "Home care — East", "South District office"), ("HC-WEST", "Home care — West", "Central District"))
                                                 if home else (("CL-SOUTH", "Clinic — South District", "South District"), ("CL-UPTOWN", "Clinic — Uptown", "Uptown")))]
        S["Warehouses"] = [{"name": "Main Store", "location": "South District"}]
        S["Stock Items"] = [{"name": n, "unit": u, "sku": k, "reorder_level": r} for k, n, u, r in _CARE_ITEMS]
        S["Stock Moves"] = [{"date": d(m, 1), "type": "Receipt", "warehouse": "Main Store", "item": n, "unit": u, "qty": q, "rate": rt,
                             "vendor": "Pharma Supplies Co. (demo)", "paid": round(q * rt * (1 if m == 0 else 0.5), 2)}
                            for m in (0, 3) for (k, n, u, _), q, rt in zip(_CARE_ITEMS, (400, 300, 120, 150), (6, 4, 18, 35))]
        S["Fixed Assets"] = ([{"code": "FA-H01", "name": "Nurses' car — Renault Logan", "account": "1420", "acquired": d(0, 4), "cost": 820000,
                               "salvage": 150000, "life_months": 60},
                              {"code": "FA-H02", "name": "Portable patient monitors ×3", "account": "1412", "acquired": d(0, 6), "cost": 135000, "life_months": 48},
                              {"code": "FA-H03", "name": "Hospital beds & oxygen concentrators (loan pool)", "account": "1412", "acquired": d(1, 2),
                               "cost": 210000, "life_months": 60}] if home else
                             [{"code": "FA-H01", "name": "Ultrasound machine", "account": "1412", "acquired": d(0, 4), "cost": 950000, "salvage": 100000,
                               "life_months": 84},
                              {"code": "FA-H02", "name": "Clinic furniture & examination couches", "account": "1400", "acquired": d(0, 2), "cost": 160000,
                               "life_months": 84},
                              {"code": "FA-H03", "name": "Lab analyser (CBC)", "account": "1412", "acquired": d(1, 2), "cost": 380000, "life_months": 60}])
        for r in S["Operating Expenses"] + S["Recurring Entries"]:
            if r.get("account") == "6010":
                r.update(amount=30000 if not home else 12000, description=r["description"].replace(
                    "Office rent", "Clinic rent" if not home else "Office & dispatch rent"))
                if "name" in r:
                    r["name"] = r["name"].replace("Office rent", "Clinic rent" if not home else "Office & dispatch rent")
            if r.get("account") == "6020":
                r.update(description="Clinic management & booking software")
        for r in S["Journal Vouchers"]:
            if r["voucher"] == "JV-D0C":
                r.update({"memo": "Cash withdrawn for the reception cash box", **{k: 30000 for k in ("debit", "credit") if r.get(k)}})
        S["Payroll"] += [{**r, "worker": w, "role": role, "rate": rate, "paid": rate}
                         for r in list(S["Payroll"]) for w, role, rate in ((("Reem Khaled", "Receptionist", 7500),) if not home else
                                                                             (("Reem Khaled", "Care coordinator (dispatch)", 9000),))]
    if key == "investment":
        S["Journal Vouchers"] += [
            {"voucher": "JV-I01", "date": d(0, 10), "kind": "Journal Voucher", "memo": "Treasury bills — 182 days", "account": "1720", "debit": 1200000},
            {"voucher": "JV-I01", "date": d(0, 10), "kind": "Journal Voucher", "memo": "Treasury bills — 182 days", "account": "1020", "credit": 1200000},
            {"voucher": "JV-I02", "date": d(1, 6), "kind": "Journal Voucher", "memo": "Listed shares — 20,000 × 32.50", "account": "1705", "debit": 650000},
            {"voucher": "JV-I02", "date": d(1, 6), "kind": "Journal Voucher", "memo": "Listed shares — 20,000 × 32.50", "account": "1020", "credit": 650000},
            {"voucher": "JV-I03", "date": d(3, 20), "kind": "Journal Voucher", "memo": "Dividend received", "account": "1020", "debit": 36000},
            {"voucher": "JV-I03", "date": d(3, 20), "kind": "Journal Voucher", "memo": "Dividend received", "account": "4091", "credit": 36000},
            {"voucher": "JV-I04", "date": eom(4), "kind": "Journal Voucher", "memo": "Shares revalued to market (34.10)", "account": "1705", "debit": 32000},
            {"voucher": "JV-I04", "date": eom(4), "kind": "Journal Voucher", "memo": "Shares revalued to market (34.10)", "account": "4093", "credit": 32000}]
    if key == "investment":
        S["Journal Vouchers"] += [
            {"voucher": "JV-I05", "date": eom(4), "kind": "Journal Voucher", "memo": "Treasury-bill interest earned to date", "account": "1720", "debit": 118000},
            {"voucher": "JV-I05", "date": eom(4), "kind": "Journal Voucher", "memo": "Treasury-bill interest earned to date", "account": "4095", "credit": 118000},
            {"voucher": "JV-I06", "date": d(4, 9), "kind": "Journal Voucher", "memo": "Sold 5,000 shares × 36.00", "account": "1020", "debit": 180000},
            {"voucher": "JV-I06", "date": d(4, 9), "kind": "Journal Voucher", "memo": "Sold 5,000 shares × 36.00 — cost", "account": "1705", "credit": 162500},
            {"voucher": "JV-I06", "date": d(4, 9), "kind": "Journal Voucher", "memo": "Sold 5,000 shares × 36.00 — gain", "account": "4092", "credit": 17500}]
        for r in S["Operating Expenses"] + S["Recurring Entries"]:
            if r.get("account") == "6010":
                r.update(amount=8000)
    if key == "sales":
        buyers = ["Atlas Builders", "Mona Adel", "Tamer Nabil", "Karim Fahmy", "Delta Tech Solutions"]
        for m in range(1, 6):
            S["Stock Moves"] += [
                {"date": d(m, 2), "type": "Receipt", "warehouse": "Main Store", "item": "Ceramic tiles 60x60", "unit": "m2", "qty": 600,
                 "rate": 300, "vendor": "Tiles & Ceramics Supplier", "paid": 180000},
                {"date": d(m, 2), "type": "Receipt", "warehouse": "Main Store", "item": "Bathroom set (basin + WC)", "unit": "set", "qty": 8,
                 "rate": 6200, "vendor": "Tiles & Ceramics Supplier", "paid": 49600}]
            for j in range(2):
                b = buyers[(m * 2 + j) % len(buyers)]
                code = f"SO-T{m}{j}"
                tiles, sets = 230 + 30 * m - 40 * j, 2 + j
                S["Sales Contracts"] += [      # paid in full, except the last order of each month (on account)
                    {"code": code, "date": d(m, 8 + j * 10), "customer": b, "bu_code": "SALES-01", "product": "G-TILE", "qty": tiles,
                     "vat_code": "VAT14", "plan": "full", "confirm": "Yes",
                     "collected": 0 if j else round((tiles * 420 + sets * 9500) * 1.14, 2)},
                    {"code": code, "date": d(m, 8 + j * 10), "customer": b, "bu_code": "SALES-01", "product": "G-SAN", "qty": sets}]
        for r in S["Operating Expenses"] + S["Recurring Entries"]:      # a showroom, not a design office
            if r.get("account") == "6010":
                r.update(amount=15000, description=r["description"].replace("Office rent", "Showroom rent"))
    if key == "scaffolding":
        for r in S["Rental Contracts"]:      # the hoist + cradle contract went ahead: ordered and signed
            if r["ref"] == "R2" and r.get("site"):
                r.update(start_date=d(1, 15), quote_date=d(1, 5), duration_months=6, po_number="PO-DT-310",
                         signed_client="Eng. Samy (Delta Tech)", signed_us="Hossam Adel")
        S["Rental Contracts"] += (
            [{"ref": "R3", "client": "Atlas Builders", "site": "Villas cluster C", "site_location": "West District", "quote_date": d(0, 5),
              "start_date": d(0, 12), "duration_months": 8, "min_days": 30, "line_kind": "item", "item": it, "qty": q, "rate": rt,
              "po_number": "PO-ATL-52", "signed_client": "Eng. Hany (Atlas)", "signed_us": "Hossam Adel"}
             for it, q, rt in (("SC-FRM2", 150, 60), ("SC-BRD36", 60, 25), ("SC-CLF", 250, 5), ("SC-CLS", 250, 5), ("SC-TUB6", 100, 30))] +
            [{"ref": "R4", "client": "Delta Tech Solutions", "site": "Branch façade", "site_location": "South District", "quote_date": d(0, 15),
              "start_date": d(0, 20), "duration_months": 6, "line_kind": "item", "item": "CR-MOT", "qty": 1, "rate": 12000,
              "po_number": "PO-DT-288", "signed_client": "Eng. Samy (Delta Tech)", "signed_us": "Hossam Adel"}])
        for r in S["Operating Expenses"] + S["Recurring Entries"]:      # a yard, not a design office
            if r.get("account") == "6010":
                r.update(amount=12000, description=r["description"].replace("Office rent", "Yard & office rent"))
        S["Journal Vouchers"] += [r for v, day, acct, memo, amt in (
            ("JV-S01", d(3, 2), "5065", "Installation crew — Atlas Tower B", 9000),
            ("JV-S02", d(3, 2), "5067", "Transport to Tower B — 2 trips", 4000),
            ("JV-S03", d(4, 12), "5066", "Frames repaired & painted", 3500))
            for r in ({"voucher": v, "date": day, "kind": "Journal Voucher", "memo": memo, "account": acct, "debit": amt},
                      {"voucher": v, "date": day, "kind": "Journal Voucher", "memo": memo, "account": "1010", "credit": amt})]


def _trim(S, key, d, eom):
    p = PROFILES[key]
    _extra(S, key, d, eom)
    for name in p["drop"]:
        S.pop(name, None)
    for name, keep in p.get("codes", {}).items():
        if name in S:
            field = _GROUP.get(name, "code")
            S[name] = [r for r in S[name] if r.get(field, r.get("code")) in keep]
    # by business unit — a row (or a group of rows sharing a code) for a unit this business does not have goes
    bus = p["bus"]
    for name, rows in list(S.items()):
        if name == "Business Units":
            S[name] = [r for r in rows if r["code"] in bus]
            continue
        g = _GROUP.get(name)
        bad = {r.get(g) for r in rows if g and r.get("bu_code") and r["bu_code"] not in bus}
        S[name] = [r for r in rows if not (r.get("bu_code") and r["bu_code"] not in bus) and not (g and r.get(g) in bad)]
    S["Stock Moves"] = [r for r in S.get("Stock Moves", []) if r.get("type") != "Transfer" or r.get("to_warehouse") in
                        {w["name"] for w in S.get("Warehouses", [])}] if "Stock Moves" in S else None
    if S["Stock Moves"] is None:
        S.pop("Stock Moves")
    # whatever pointed at a removed row goes with it (twice: chains such as quotes → requests → units)
    for _ in range(2):
        for child, field, parent, pfield in _REFS:
            if child not in S:
                continue
            have = {r.get(pfield) for r in S.get(parent, [])}
            S[child] = [r for r in S[child] if not r.get(field) or r[field] in have]
    for code, (name, typ) in p.get("rename", {}).items():
        for r in S.get("Business Units", []):
            if r["code"] == code:
                r.update(name=name, type=typ)
    # paying a supplier only makes sense if something was bought from them
    bought = {r.get("vendor") or r.get("supplier") for n in ("Direct Costs", "Stock Moves", "Operating Expenses", "Rental Stock Lots",
                                                              "Purchase Orders") for r in S.get(n, [])}
    if "Payment Vouchers" in S:
        S["Payment Vouchers"] = [r for r in S["Payment Vouchers"] if r["party"] in bought]
    used = {str(v) for name, rows in S.items() if name not in ("Parties",) for r in rows for v in r.values() if isinstance(v, str)}
    S["Parties"] = [r for r in S.get("Parties", []) if r["name"] in used]
    S["Budgets"] = [r for r in S.get("Budgets", []) if r["account"] in p["budget"]]
    S["Settings"] = [{**r, "value": p["name"]} if r["key"] == "default:company_name" else
                     {**r, "value": p["tagline"]} if r["key"] == "default:company_tagline" else
                     {**r, "value": p["name"].replace(" (demo)", "") + " (fictitious)"} if r["key"] == "default:company_legal_name" else r
                     for r in S["Settings"]]
    return {k: v for k, v in S.items() if v}


# what a new company already has before anyone types anything (chart of accounts, default warehouse, tax codes, the
# general chat room…) and the system's own records; every other table holding a row means the books are in use
SETUP_TABLES = {"settings", "accounts", "warehouses", "tax_codes", "assets", "offer_types", "chat_rooms", "chat_members", "users",
                "sessions", "audit_log", "user_profiles", "user_groups", "cloud_local", "ai_threads", "ai_usage", "support_tickets",
                "rent_models"}


def is_empty(db):
    """True only for a clean slate: no business data anywhere (entries, parties, items, documents, people, chat…)."""
    for r in db.q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if r["name"] not in SETUP_TABLES and db.one(f'SELECT 1 FROM "{r["name"]}" LIMIT 1'):
            return False
    return True


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


# ---- healthcare / home healthcare: patients, services, six months of visits billed, claims, packages (all invented)
_INSURERS = (("Nile Health TPA (demo)", 80), ("Delta Medical Insurance (demo)", 90), ("Delta Tech Solutions", 100))
_CARE_SERVICES = {
    "healthcare": [("Consultation — general practitioner", "كشف ممارس عام", "consultation", 400, 300, 40, 20, 0, []),
                   ("Consultation — cardiology", "كشف قلب", "consultation", 700, 550, 50, 30, 0, []),
                   ("Follow-up consultation", "استشارة متابعة", "consultation", 200, 150, 40, 15, 0, []),
                   ("Complete blood count (CBC)", "صورة دم كاملة", "lab", 150, 120, 0, 10, 0, [("SYRINGE", 1), ("GLOVES", 1)]),
                   ("Abdominal ultrasound", "سونار على البطن", "radiology", 600, 450, 30, 20, 0, [("GLOVES", 1)]),
                   ("Physiotherapy session", "جلسة علاج طبيعي", "physiotherapy", 350, 280, 50, 45, 0, []),
                   ("Wound dressing", "غيار على جرح", "nursing", 250, 200, 30, 20, 0, [("DRESS", 1), ("GLOVES", 2)]),
                   ("IV cannula & injection", "تركيب كانيولا وحقن", "nursing", 150, 120, 30, 15, 0, [("CANNULA", 1), ("SYRINGE", 1), ("GLOVES", 1)])],
    "homecare": [("Doctor home visit", "زيارة طبيب منزلية", "home_visit", 1200, 900, 60, 45, 1, []),
                 ("Nurse visit — injection / cannula", "زيارة تمريض — حقن / كانيولا", "nursing", 350, 300, 50, 30, 1, [("CANNULA", 1), ("SYRINGE", 1), ("GLOVES", 1)]),
                 ("Wound dressing at home", "غيار على جرح بالمنزل", "nursing", 450, 380, 50, 40, 1, [("DRESS", 1), ("GLOVES", 2)]),
                 ("Physiotherapy session at home", "جلسة علاج طبيعي بالمنزل", "physiotherapy", 500, 420, 55, 60, 1, []),
                 ("Sample collection at home (lab)", "سحب عينة بالمنزل", "lab", 300, 250, 20, 20, 1, [("SYRINGE", 1), ("GLOVES", 1)]),
                 ("Elderly-care nurse — 12-hour shift", "ممرضة رعاية مسنين — وردية 12 ساعة", "nursing", 900, 0, 70, 720, 1, []),
                 ("Elderly-care nurse — 24-hour shift", "ممرضة رعاية مسنين — 24 ساعة", "nursing", 1600, 0, 70, 1440, 1, [])]}
_CARE_PEOPLE = {"healthcare": ["Dr. Ahmed Fathy (GP)", "Dr. Nadia Samir (Cardiology)", "Dr. Karim Lotfy (Radiology)", "PT Yasmin Adel", "Nurse Hoda Salem"],
                "homecare": ["Dr. Ahmed Fathy", "Nurse Hoda Salem", "Nurse Mona Ibrahim", "PT Yasmin Adel", "Carer Samah Ali"]}
_PATIENTS = [("Mahmoud Abdelaziz", "male", "South District", 0, "Diabetic — type 2"), ("Fatma Hassan", "female", "Uptown", 1, ""),
             ("Youssef Nabil", "male", "Airport District", -1, ""), ("Hala Mostafa", "female", "Central District", 2, "Allergic to penicillin"),
             ("Ibrahim El-Sayed", "male", "Riverside", 0, "Hypertension"), ("Salma Tarek", "female", "East District", -1, ""),
             ("Adel Ramzy", "male", "West District", 1, "Post-surgery — hip replacement"), ("Nermeen Fouad", "female", "South District", -1, ""),
             ("Hassan Mahmoud", "male", "Uptown", 2, ""), ("Aya Gamal", "female", "Airport District", 0, ""),
             ("Sherif Adly", "male", "Central District", -1, "Bedridden — stroke"), ("Laila Kamel", "female", "East District", 1, ""),
             ("Mostafa Zaki", "male", "Riverside", -1, ""), ("Rania Saad", "female", "West District", 0, "Asthma")]


def _care_demo(db, industry, user, a0, today):
    import random
    from . import care
    care.ensure_accounts(db)
    rnd = random.Random(7 if industry == "homecare" else 3)
    home = industry == "homecare"
    bus = [r["id"] for r in db.q("SELECT id FROM business_units ORDER BY code")]
    sv = [care.save_service(db, {"name": n, "name_ar": ar, "category": cat, "price": p, "insurer_price": ip, "covered": ip > 0,
                                 "provider_pct": pct, "duration_min": mins, "home": h, "consumables": [{"item": i, "qty": q} for i, q in cons]})
          for n, ar, cat, p, ip, pct, mins, h, cons in _CARE_SERVICES[industry]]
    pats = []
    for i, (name, g, zone, ins, alert) in enumerate(_PATIENTS):
        yr = 1940 + (i * 7) % 45 if home else 1960 + (i * 5) % 50
        pats.append(care.save_patient(db, {"name": name, "gender": g, "zone": zone, "dob": f"{yr}-{(i % 12) + 1:02d}-{(i * 3) % 27 + 1:02d}",
                                           "phone": f"0100000{3000 + i}", "address": f"{10 + i} Demo Street, {zone}",
                                           "payer": "insurer" if ins >= 0 else "self", "insurer": _INSURERS[ins][0] if ins >= 0 else "",
                                           "coverage_pct": _INSURERS[ins][1] if ins >= 0 else 0, "policy_no": f"POL-{5000 + i}" if ins >= 0 else "",
                                           "alerts": alert}))
    people = _CARE_PEOPLE[industry]
    # who gives which service
    giver = (lambda k: people[[0, 1, 0, 4, 2, 3, 4, 4][k]]) if not home else (lambda k: people[[0, 1, 2, 3, 1, 4, 4][k]])
    fee = lambda zone: 0 if not home else (150 if zone in ("South District", "Central District", "Riverside") else 250)   # noqa: E731
    # prepaid packages: physiotherapy course (and a month of nursing shifts at home)
    physio = sv[5] if not home else sv[3]
    pk = [care.sell_package(db, {"patient_id": pats[6], "service_id": physio, "sessions": 10 if not home else 12,
                                 "price": 3000 if not home else 5400, "date": (today - _dt.timedelta(days=32)).isoformat(),
                                 "name": "Physiotherapy course" + (" at home" if home else "")}, user)]
    if home:
        pk.append(care.sell_package(db, {"patient_id": pats[10], "service_id": sv[5], "sessions": 30, "price": 24000, "paid": 12000,
                                         "date": (today - _dt.timedelta(days=24)).isoformat(), "name": "A month of 12-hour nursing shifts"}, user))
    pk_left = {pk[0]: (pats[6], physio, 10 if not home else 12)}
    if home:
        pk_left[pk[1]] = (pats[10], sv[5], 30)
    pk_start = {pk[0]: today - _dt.timedelta(days=30)}
    if home:
        pk_start[pk[1]] = today - _dt.timedelta(days=22)
    day = a0
    n = 0
    while day <= today + _dt.timedelta(days=3):
        if day.weekday() != 4:                     # closed on Fridays
            for _ in range(rnd.randint(2, 4) if not home else rnd.randint(1, 3)):
                k = rnd.randrange(len(sv) - (2 if home else 0))
                pid = rnd.choice(pats)
                p = db.one("SELECT * FROM care_patients WHERE id=?", (pid,))
                future = day > today
                st = "booked" if future or (day == today and n % 2) else rnd.choices(["done", "no_show", "cancelled"], [88, 8, 4])[0]
                lines = [{"service_id": sv[k], "qty": 1}]
                if not home and k == 0 and rnd.random() < .35:
                    lines.append({"service_id": sv[3], "qty": 1})       # a CBC with the GP visit
                vid = care.save_visit(db, {"date": day.isoformat(), "time": f"{rnd.choice([9, 10, 11, 12, 13, 17, 18, 19]):02d}:{rnd.choice(['00', '30'])}",
                                           "patient_id": pid, "provider": giver(k), "place": "home" if home else "clinic",
                                           "bu_id": bus[0] if (not home and p["zone"] in ("South District", "Central District", "Riverside", "West District", "East District"))
                                           or (home and p["zone"] not in ("Central District", "Riverside", "West District")) else bus[-1],
                                           "travel_fee": fee(p["zone"]), "lines": lines}, user)
                n += 1
                if st != "booked":
                    care.set_status(db, vid, st, {"check_in": "10:00", "time": "10:40"} if st == "done" else {})
                if st == "done":
                    v = care.visit(db, vid)
                    owe = rnd.random() < .08
                    care.bill_visit(db, vid, {"paid": round(v["patient_amt"] / 2) if owe else None, "method": rnd.choice(["Cash", "Cash", "Credit Card"])}, user)
            # package sessions, twice a week
            for kid, (pid, sid, total) in list(pk_left.items()):
                if day >= pk_start[kid] and day <= today and total > 0 and day.weekday() in ((0, 3) if sid == physio else (0, 1, 2, 3, 5, 6)):
                    vid = care.save_visit(db, {"date": day.isoformat(), "time": "08:00" if sid != physio else "16:00", "patient_id": pid,
                                               "provider": giver(5 if sid == physio and not home else 3 if sid == physio else 5),
                                               "place": "home" if home else "clinic", "bu_id": bus[0], "package_id": kid,
                                               "lines": [{"service_id": sid, "qty": 1}]}, user)
                    care.set_status(db, vid, "done", {"check_in": "08:00", "time": "20:00"} if sid != physio else {"check_in": "16:00", "time": "16:50"})
                    care.bill_visit(db, vid, {}, user)
                    pk_left[kid] = (pid, sid, total - 1)
        day += _dt.timedelta(days=1)
    # insurance claims: monthly; the older ones answered (a few lines rejected), last month's still with the insurer
    som = lambda dt: dt.replace(day=1)   # noqa: E731
    month = som(a0)
    while month < som(today):
        end = (month + _dt.timedelta(days=32)).replace(day=1) - _dt.timedelta(days=1)
        for ins, _pct in _INSURERS:
            try:
                cid = care.make_claim(db, ins, month.isoformat(), end.isoformat(), user)
            except ValidationError:
                continue
            if end < som(today) - _dt.timedelta(days=1):
                c = db.one("SELECT claimed FROM care_claims WHERE id=?", (cid,))["claimed"] / 100
                rej = round(c * (0.03 if ins != "Delta Tech Solutions" else 0), 2)
                care.settle_claim(db, cid, {"paid": round(c - rej, 2), "rejected": rej, "method": "Bank Transfer",
                                            "date": min(end + _dt.timedelta(days=40), today).isoformat(),
                                            "notes": "Lines without prior approval" if rej else ""}, user)
        month = end + _dt.timedelta(days=1)
    # doctors' and carers' shares paid monthly up to last month
    for r in db.q("SELECT l.party, SUM(l.credit-l.debit) bal FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                  "WHERE e.status='posted' AND l.account=? AND e.date<? GROUP BY l.party HAVING bal>0", (care.FEES_DUE, som(today).isoformat())):
        acc._insert_entry(db, date=som(today).isoformat(), kind="Payment Voucher", memo=f"Fees paid — {r['party']}", bu_id=None, party=r["party"],
                          ref="", user=user, meta={}, lines=[{"account": care.FEES_DUE, "debit": r["bal"] / 100, "party": r["party"]},
                                                             {"account": acc.money_account(db, "Bank Transfer"), "credit": r["bal"] / 100}])


def load(db, user="", replace=False, industry=None):
    """Fill the books with the demo company — the whole of it, or what one business type does (industry, after
    industries.apply). Refuses on books that already hold data unless replace (wipes first)."""
    from . import (assets, bankrec, documents, interchange, recurring, rentals, sales)
    if not is_empty(db) and not replace:
        raise ValidationError("These books already hold data — the demo only loads into an empty company (start a new company to try it)")
    a0, today = _anchor()
    S = sheets(industry)
    bu_has = lambda code: bool(db.one("SELECT 1 FROM business_units WHERE code=?", (code,)))
    sale_has = lambda code: bool(db.one("SELECT 1 FROM sales_orders WHERE code=?", (code,)))
    prod_has = lambda code: bool(db.one("SELECT 1 FROM products WHERE code=?", (code,)))
    sub_has = lambda code: bool(db.one("SELECT 1 FROM subscriptions WHERE code=?", (code,)))
    with db.tx():
        if industry in ("healthcare", "homecare"):
            from . import care
            care.ensure_accounts(db)
        res = interchange.import_data(db, S, mode="replace" if replace else "append", user=user)
        # ---- steps that only exist on screens
        if bu_has("PRJ-D01"):
            # a negotiation: keep the discount as earnings on the cost-plus villa
            db.conn.execute("UPDATE business_units SET negotiation='keep' WHERE code='PRJ-D01'")
            acc.record_project_cost(db, {"date": S["Direct Costs"][7]["date"], "bu_code": "PRJ-D01", "account": "5010",
                                       "description": "Sanitary ware (negotiated)", "vendor": "Tiles & Ceramics Supplier", "qty": 1,
                                       "rate": 58000, "savings": 6000, "paid": 52000, "negotiation": "keep",
                                       "deal_ref": "CLEO-02", "deal_status": "Settled"}, user)
            # a supplier deal settled for less than owed
            acc.record_supplier_discount(db, {"date": S["Direct Costs"][6]["date"], "party": "Hassan Aluminium", "amount": 2000,
                                            "bu_code": "PRJ-D01", "deal_ref": "HASSAN-01", "memo": "Final account discount"}, user)
        # partner equity events: a partner loan, part repaid, a share transfer
        from . import equity
        pay_dates = [r["date"] for r in S["Payroll"]]
        equity.post(db, "loan", {"partner": "Omar Fouad", "amount": 100000, "rate": 10, "date": pay_dates[2],
                                 "method": "Bank Transfer"}, user)
        equity.post(db, "repay", {"partner": "Omar Fouad", "amount": 20000, "date": pay_dates[3],
                                  "method": "Bank Transfer"}, user)
        equity.post(db, "transfer", {"seller": "Hossam Adel", "buyer": "Sara Kamal", "share": 5, "date": pay_dates[3]}, user)
        # an asset bought for cash, one sold
        assets.save(db, {"name": "ECG device & medical tools" if industry in ("healthcare", "homecare") else "Laser level & tools",
                         "account": "1412" if industry in ("healthcare", "homecare") else "1410", "acquired": pay_dates[1], "cost": 42000,
                         "life_months": 36, "pay": "cash", "method": "Bank Transfer"}, user=user)
        last_month = (today.replace(day=1) - _dt.timedelta(days=1)).isoformat()[:7]
        assets.run_depreciation(db, last_month, user=user)
        try:
            rentals.run_depreciation(db, last_month, user=user)
        except ValidationError:
            pass
        if S.get("Rental Contracts"):
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
            if industry == "scaffolding":
                # a rental yard's demo: R1 billed every month to date, and the hoist + cradle contract delivered and billed too
                def bill_to_date(rid, start):
                    frm = _dt.date.fromisoformat(start)
                    while True:
                        to = (frm.replace(day=1) + _dt.timedelta(days=40)).replace(day=1) - _dt.timedelta(days=1)
                        if to >= today:
                            break
                        try:
                            x = rentals.create_invoice(db, rid, {"from": frm.isoformat(), "to": to.isoformat()}, user)
                            acc.post_extract(db, x if isinstance(x, int) else x["id"], user=user)
                        except ValidationError:
                            break
                        frm = to + _dt.timedelta(days=1)
                bill_to_date(r1, (end + _dt.timedelta(days=1)).isoformat())
                for r2 in db.q("SELECT id, start_date FROM rentals WHERE id<>? AND po_number<>'' ORDER BY start_date", (r1,)):
                    t2 = rentals.handover_template(db, r2["id"], "delivery")
                    t2.update(signed=1, decision="safe", date=r2["start_date"], checklist=[dict(c, ok="yes") for c in t2["checklist"]])
                    rentals.confirm_handover(db, rentals.save_handover(db, r2["id"], t2))
                    bill_to_date(r2["id"], r2["start_date"])
        # sales: cheques for the apartment (one cleared, one bounced), deliver the goods sale, a design offer
        if sale_has("SO-D001"):
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
        for so in (db.q("SELECT code FROM sales_orders WHERE code LIKE 'SO-T%' ORDER BY date") if industry == "sales" else []):
            o = sales.get(db, so["code"])
            sales.deliver(db, o["id"], {"date": o["date"]}, user)
        if sale_has("SO-D002"):
            s2 = sales.get(db, "SO-D002")
            sales.deliver(db, s2["id"], {"date": s2["date"]}, user)
        if prod_has("U-A101"):
            p = sales.get_product(db, "U-A101")
            for col, title in (((180, 140, 100), "Front view"), ((90, 120, 160), "Living room")):
                sales.add_media(db, p["id"], {"kind": "photo", "title": title, "data": _png(col), "thumb": _png(col, 48, 32)})
            sales.add_media(db, p["id"], {"kind": "video", "title": "Walkthrough (link)", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
            sales.add_media(db, p["id"], {"kind": "model", "title": "3D model (link)", "url": "https://modelviewer.dev/shared-assets/models/Astronaut.glb"})
        for code, col in (("U-A204", (120, 160, 120)), ("U-V01", (200, 170, 90)), ("G-TILE", (170, 170, 170)), ("G-SAN", (230, 230, 240))):
            if prod_has(code):
                sales.add_media(db, sales.get_product(db, code)["id"], {"kind": "photo", "data": _png(col), "thumb": _png(col, 48, 32)})
        types = documents.list_offer_types(db) if bu_has("PRJ-D02") else []
        if types:
            ty = types[0]
            documents.save_offer(db, {**{k: v for k, v in ty.items() if k not in ("id", "name")}, "type_id": ty["id"],
                                      "client": "Laila Samir", "project": "Apartment 14 — design package", "date": S["BOQ Lines"][0]["date"],
                                      "status": "Sent"})
        if bu_has("PRJ-D01"):
            # purchase orders: one part received (vouchers posted on credit), one approved and waiting, one draft to approve
            from . import purchasing
            bu = db.one("SELECT code FROM business_units ORDER BY id LIMIT 1")["code"]
            wh = db.one("SELECT name FROM warehouses ORDER BY id LIMIT 1")["name"]
            mid = (today.replace(day=1) - _dt.timedelta(days=20)).isoformat()
            p1 = purchasing.save(db, {"supplier": "El-Nour Building Materials", "date": mid, "bu_code": bu, "warehouse": wh, "vat_code": "VAT14",
                                      "expected": mid, "terms": "Delivery to site within 7 days. Payment 30 days from invoice.",
                                      "lines": [{"kind": "cost", "description": "Cement 50 kg bags", "qty": 200, "unit": "bag", "rate": 115, "account": "5010"},
                                                {"kind": "cost", "description": "Steel rebar 12 mm", "qty": 2, "unit": "ton", "rate": 38500, "account": "5010"},
                                                {"kind": "stock", "description": "Tile adhesive", "qty": 40, "unit": "bag", "rate": 95}]}, "Sara Kamal")
            purchasing.approve(db, p1, "admin")
            l1 = purchasing.get(db, p1)["lines"]
            purchasing.receive(db, p1, {"date": mid, "supplier_ref": "DN-4471", "lines": [{"line_id": l1[0]["id"], "qty": 120},
                                                                                         {"line_id": l1[2]["id"], "qty": 40}]}, "Sara Kamal")
            p2 = purchasing.save(db, {"supplier": "Hassan Aluminium", "date": acc.today(), "bu_code": bu,
                                      "expected": (today + _dt.timedelta(days=14)).isoformat(),
                                      "lines": [{"kind": "cost", "description": "Aluminium windows — supply & install", "qty": 18, "unit": "m2",
                                                 "rate": 3200, "account": "5020"}]}, "Sara Kamal")
            purchasing.approve(db, p2, "admin")
            purchasing.save(db, {"supplier": "Spark Electric", "date": acc.today(), "terms": "Quotation SE-221",
                                 "lines": [{"kind": "expense", "description": "Office UPS replacement", "qty": 1, "rate": 14500, "account": "6090"}]},
                            "Sara Kamal")
        # subscriptions: a maintenance visit (one extra beyond the plan), then bill every period started by today
        from . import services
        if sub_has("SUB-D02") and sub_has("SUB-D01"):
            sub = services.get(db, "SUB-D02")

            def d(month, day):
                y, m = a0.year + (a0.month - 1 + month) // 12, (a0.month - 1 + month) % 12 + 1
                return min(_dt.date(y, m, min(day, calendar.monthrange(y, m)[1])), today).isoformat()
            for k, txt in ((2, "Monthly check — water heater"), (3, "Monthly check"), (3, "Emergency: kitchen leak")):
                services.save_visit(db, sub["id"], {"date": d(k, 20 if "Emergency" in txt else 10), "technician": "Eng. Fathy Plumbing",
                                                    "description": txt, "cost": 350})
            services.save_visit(db, services.get(db, "SUB-D01")["id"], {"date": acc.today(), "status": "scheduled",
                                                                         "technician": "Spark Electric", "description": "Quarterly preventive visit"})
        services.run(db, acc.today(), user)
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
        # a stocktake of the main store: two tiles broken, one bathroom set missing — posted as adjustments
        from . import stocktake
        main = db.one("SELECT id FROM warehouses WHERE name='Main Store'")
        if main and db.one("SELECT 1 FROM stock_moves LIMIT 1"):
            cid = stocktake.start(db, {"warehouse_id": main["id"], "date": acc.today(), "counted_by": "Mahmoud Saber",
                                       "notes": "Quarterly count"}, user)
            short = {"Ceramic tiles 60x60": 2, "Bathroom set (basin + WC)": 1}
            stocktake.save_counts(db, cid, {"lines": [{"item_id": ln["item_id"], "item": ln["item"], "counted": max(ln["system_qty"] - short.get(ln["item"], 0), 0),
                                                       "note": "broken in the store" if ln["item"] in short else ""}
                                                      for ln in stocktake.get(db, cid)["lines"]]})
            stocktake.post(db, cid, user)
        if industry in ("healthcare", "homecare"):
            _care_demo(db, industry, user, a0, today)
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
