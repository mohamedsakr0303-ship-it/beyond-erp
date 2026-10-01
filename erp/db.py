"""SQLite storage: connection handling, schema, migrations and seed data.

Money is stored as INTEGER minor units (piastres/cents) so totals never drift
from floating-point rounding. Use to_minor()/to_major() at the edges.
"""
import json
import os
try:
    import sqlite3
    _NATIVE = (sqlite3.Connection,)
except ImportError:  # browser build (Pyodide core has no sqlite3): uses erp.jsdb over sql.js instead
    sqlite3 = None
    _NATIVE = ()
import sys
import threading
from pathlib import Path

SCHEMA_VERSION = 3

# The engine's clock. Normally real time; when a change from the shared workspace is replayed it is pinned to the
# moment the change was made, so every device computes exactly the same result (see erp/cloud.py).
import datetime as _dt
CLOCK = {"at": None}


def now():
    return CLOCK["at"] or _dt.datetime.now().replace(microsecond=0)


def default_data_dir() -> Path:
    """Per-user data folder that is correct on every OS.

    Override with the BEYOND_ERP_DATA environment variable or --data on the CLI.
    """
    env = os.environ.get("BEYOND_ERP_DATA")
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "BeyondERP"


def to_minor(amount) -> int:
    if amount in (None, ""):
        return 0
    return int(round(float(amount) * 100))


def to_major(minor) -> float:
    return round((minor or 0) / 100.0, 2)


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Chart of accounts. `code` and `name` are freely editable (journal lines follow
-- a code change through ON UPDATE CASCADE). `role` is a stable internal tag the
-- app uses to find system accounts (bank, receivables, ...) whatever they are
-- called; `subtype` decides where the account lands on each financial statement.
CREATE TABLE IF NOT EXISTS accounts (
    code    TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    name_ar TEXT DEFAULT '',
    type    TEXT NOT NULL CHECK (type IN ('Asset','Liability','Equity','Revenue','COGS','Expense')),
    subtype TEXT NOT NULL,
    normal  TEXT NOT NULL CHECK (normal IN ('Debit','Credit')),
    role    TEXT UNIQUE,
    active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS business_units (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT DEFAULT '',
    location TEXT DEFAULT '',
    area REAL DEFAULT 0,
    start_date TEXT DEFAULT '',
    status TEXT DEFAULT 'In Progress',
    basis TEXT DEFAULT 'Cost-Plus %',
    fee_pct REAL DEFAULT 0,
    contract_value INTEGER DEFAULT 0,
    overhead_pct REAL DEFAULT 0,          -- manual share of shared (indirect) overhead, 0..1
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    memo TEXT DEFAULT '',
    bu_id INTEGER REFERENCES business_units(id) ON DELETE RESTRICT,
    party TEXT DEFAULT '',
    ref TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'posted' CHECK (status IN ('posted','void')),
    reversal_of INTEGER REFERENCES journal_entries(id),
    reversed_by INTEGER REFERENCES journal_entries(id),
    meta TEXT DEFAULT '{}',
    created_by TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    history TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_je_date ON journal_entries(date);
CREATE INDEX IF NOT EXISTS ix_je_bu ON journal_entries(bu_id);
CREATE INDEX IF NOT EXISTS ix_je_kind ON journal_entries(kind);
CREATE INDEX IF NOT EXISTS ix_je_party ON journal_entries(party);

CREATE TABLE IF NOT EXISTS journal_lines (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    account TEXT NOT NULL REFERENCES accounts(code) ON UPDATE CASCADE ON DELETE RESTRICT,
    debit INTEGER NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit INTEGER NOT NULL DEFAULT 0 CHECK (credit >= 0),
    party TEXT DEFAULT ''                  -- a line's own party (e.g. each partner in a share transfer); '' = the entry's
);
CREATE INDEX IF NOT EXISTS ix_jl_entry ON journal_lines(entry_id);
CREATE INDEX IF NOT EXISTS ix_jl_account ON journal_lines(account);

CREATE TABLE IF NOT EXISTS partners (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    default_share REAL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,     -- 0 = has left the company (kept for history)
    joined TEXT DEFAULT '',
    left_on TEXT DEFAULT '',
    capital_interest REAL DEFAULT 0,       -- agreed yearly return on capital, paid out of profit before sharing it
    loan_rate REAL DEFAULT 0,              -- yearly interest on the partner's loans to the company (0 = interest-free)
    notes TEXT DEFAULT ''
);
-- Every change of ownership (share transfer, admission, exit, manual change): the shares before and after, so the
-- latest one can be undone (voiding its journal entry undoes it too).
CREATE TABLE IF NOT EXISTS ownership_changes (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    memo TEXT DEFAULT '',
    entry_id INTEGER REFERENCES journal_entries(id) ON DELETE SET NULL,
    before TEXT NOT NULL,
    after TEXT NOT NULL,
    created_by TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS partner_shares (
    partner_id INTEGER NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    bu_id INTEGER NOT NULL REFERENCES business_units(id) ON DELETE CASCADE,
    share REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (partner_id, bu_id)
);

CREATE TABLE IF NOT EXISTS rates (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    item_no TEXT DEFAULT '',
    sub TEXT DEFAULT '',
    description TEXT NOT NULL,
    unit TEXT DEFAULT '',
    cost INTEGER DEFAULT 0,
    markup REAL DEFAULT 0,
    price INTEGER DEFAULT 0,
    notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_rates_cat ON rates(category);

CREATE TABLE IF NOT EXISTS boqs (
    id INTEGER PRIMARY KEY,
    bu_id INTEGER REFERENCES business_units(id) ON DELETE SET NULL,
    title TEXT DEFAULT 'BOQ',
    rev TEXT DEFAULT 'Rev 01',
    date TEXT,
    client TEXT DEFAULT '',
    location TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS boq_lines (
    id INTEGER PRIMARY KEY,
    boq_id INTEGER NOT NULL REFERENCES boqs(id) ON DELETE CASCADE,
    seq INTEGER DEFAULT 0,
    category TEXT DEFAULT '',
    sub TEXT DEFAULT '',
    item_no TEXT DEFAULT '',
    description TEXT DEFAULT '',
    unit TEXT DEFAULT '',
    qty REAL DEFAULT 0,
    price INTEGER DEFAULT 0,
    cost INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS extracts (
    id INTEGER PRIMARY KEY,
    bu_id INTEGER NOT NULL REFERENCES business_units(id) ON DELETE RESTRICT,
    boq_id INTEGER REFERENCES boqs(id) ON DELETE SET NULL,
    number TEXT NOT NULL,
    date TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted')),
    entry_id INTEGER REFERENCES journal_entries(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS extract_lines (
    id INTEGER PRIMARY KEY,
    extract_id INTEGER NOT NULL REFERENCES extracts(id) ON DELETE CASCADE,
    seq INTEGER DEFAULT 0,
    category TEXT DEFAULT '',
    sub TEXT DEFAULT '',
    item_no TEXT DEFAULT '',
    description TEXT DEFAULT '',
    unit TEXT DEFAULT '',
    qty REAL DEFAULT 0,
    price INTEGER DEFAULT 0
);

-- Multi-warehouse stock. Each warehouse may post to its own inventory account.
CREATE TABLE IF NOT EXISTS warehouses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    location TEXT DEFAULT '',
    account TEXT REFERENCES accounts(code) ON UPDATE CASCADE,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    unit TEXT DEFAULT 'pcs',
    sku TEXT DEFAULT '',
    reorder_level REAL DEFAULT 0
);
-- Running balance per item per warehouse (derived from stock_moves; rebuilt on change)
CREATE TABLE IF NOT EXISTS stock_levels (
    item_id INTEGER NOT NULL REFERENCES stock_items(id) ON DELETE CASCADE,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    qty REAL NOT NULL DEFAULT 0,
    avg_cost REAL NOT NULL DEFAULT 0,      -- minor units per unit
    PRIMARY KEY (item_id, warehouse_id)
);
CREATE TABLE IF NOT EXISTS stock_moves (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES stock_items(id) ON DELETE CASCADE,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
    date TEXT NOT NULL,
    qty REAL NOT NULL,                     -- + in, - out
    rate REAL NOT NULL,                    -- minor units per unit
    bu_id INTEGER REFERENCES business_units(id),
    entry_id INTEGER REFERENCES journal_entries(id) ON DELETE CASCADE,
    kind TEXT DEFAULT '',
    note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sm_item ON stock_moves(item_id, warehouse_id);

-- Fixed asset register. Cost, depreciation and disposal are journal entries tagged ref 'FA:<code>...';
-- the register holds only the description and the depreciation policy, so it survives export / import.
CREATE TABLE IF NOT EXISTS fixed_assets (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    account TEXT NOT NULL REFERENCES accounts(code) ON UPDATE CASCADE,        -- cost (asset) account
    accum_account TEXT REFERENCES accounts(code) ON UPDATE CASCADE,           -- accumulated depreciation
    expense_account TEXT REFERENCES accounts(code) ON UPDATE CASCADE,         -- depreciation expense
    acquired TEXT NOT NULL,
    cost INTEGER NOT NULL DEFAULT 0,
    salvage INTEGER NOT NULL DEFAULT 0,
    life_months INTEGER NOT NULL DEFAULT 60,   -- 0 = not depreciated (land)
    depr_start TEXT DEFAULT '',                -- first month depreciated (YYYY-MM); default = month acquired
    opening_depr INTEGER NOT NULL DEFAULT 0,   -- depreciation already taken before it was registered here
    method TEXT DEFAULT '',                    -- '' = company policy, straight_line, declining
    factor REAL DEFAULT 0,                     -- declining balance: 2 = double declining; 0 = company policy
    bu_id INTEGER REFERENCES business_units(id) ON DELETE SET NULL,
    location TEXT DEFAULT '',
    serial TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);

-- Loans the company takes or gives; money movements are journal entries with ref LN:<code>...
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    direction TEXT NOT NULL CHECK (direction IN ('borrowed','lent')),
    party TEXT NOT NULL,
    purpose TEXT DEFAULT '',
    account TEXT NOT NULL REFERENCES accounts(code) ON UPDATE CASCADE,          -- the loan (liability / receivable)
    interest_account TEXT REFERENCES accounts(code) ON UPDATE CASCADE,        -- interest expense / income
    accrued_account TEXT REFERENCES accounts(code) ON UPDATE CASCADE,         -- accrued interest payable / receivable
    start_date TEXT NOT NULL,
    principal INTEGER NOT NULL DEFAULT 0,       -- original amount (schedule base)
    opening_balance INTEGER NOT NULL DEFAULT 0, -- balance already in the books when registered here
    opening_date TEXT DEFAULT '',
    rate REAL NOT NULL DEFAULT 0,               -- annual, 0.12 = 12 %
    interest_type TEXT NOT NULL DEFAULT 'none', -- none | declining | flat | fixed
    fixed_interest INTEGER NOT NULL DEFAULT 0,
    term_months INTEGER NOT NULL DEFAULT 12,
    frequency INTEGER NOT NULL DEFAULT 1,       -- months between installments
    first_due TEXT DEFAULT '',
    schedule_type TEXT NOT NULL DEFAULT 'equal',-- equal | equal_principal | bullet | custom | none
    schedule TEXT DEFAULT '[]',                 -- custom installments [{due, principal, interest}] (minor units)
    bu_id INTEGER REFERENCES business_units(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active',
    closed_on TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);

-- Equipment rental (scaffolding, construction elevators, cradles). Stock is tracked FIFO by purchase lot.
CREATE TABLE IF NOT EXISTS rent_items (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    name_ar TEXT DEFAULT '',
    category TEXT NOT NULL DEFAULT 'scaffolding',   -- scaffolding | elevator | cradle_manual | cradle_motorized | other
    unit TEXT DEFAULT 'piece',
    monthly_rate INTEGER NOT NULL DEFAULT 0,        -- rent per piece per month
    std_cost INTEGER NOT NULL DEFAULT 0,            -- reference cost when no purchase is recorded yet
    loss_price INTEGER NOT NULL DEFAULT 0,          -- 0 = latest cost × (1 + loss markup)
    weight REAL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rent_kits (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    name_ar TEXT DEFAULT '',
    unit TEXT DEFAULT 'frame',
    monthly_rate INTEGER NOT NULL DEFAULT 0,
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rent_kit_lines (
    id INTEGER PRIMARY KEY,
    kit_id INTEGER NOT NULL REFERENCES rent_kits(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES rent_items(id),
    qty REAL NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS rent_lots (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    item_id INTEGER NOT NULL REFERENCES rent_items(id),
    date TEXT NOT NULL,
    qty REAL NOT NULL,
    unit_cost INTEGER NOT NULL DEFAULT 0,
    supplier TEXT DEFAULT '',
    source TEXT DEFAULT 'purchase',                 -- purchase (booked) | opening (already owned)
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rent_models (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_ar TEXT DEFAULT '',
    category TEXT DEFAULT 'scaffolding',
    spec TEXT NOT NULL DEFAULT '{}',                -- inputs, params, derived values, component formulas
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rentals (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'quote',          -- quote | ordered | active | returned | closed | cancelled
    client TEXT NOT NULL,
    contact TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    site TEXT DEFAULT '',
    site_location TEXT DEFAULT '',
    bu_id INTEGER REFERENCES business_units(id) ON DELETE SET NULL,
    quote_date TEXT DEFAULT '',
    valid_until TEXT DEFAULT '',
    po_number TEXT DEFAULT '',
    po_date TEXT DEFAULT '',
    po_notes TEXT DEFAULT '',
    signed_us TEXT DEFAULT '',
    signed_client TEXT DEFAULT '',
    signed_on TEXT DEFAULT '',
    start_date TEXT DEFAULT '',
    duration_months REAL DEFAULT 1,
    vat_code TEXT DEFAULT '',
    wht_code TEXT DEFAULT '',
    offer_notes TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    lang TEXT DEFAULT 'en',
    closed_on TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    created_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rental_lines (
    id INTEGER PRIMARY KEY,
    rental_id INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    seq INTEGER DEFAULT 0,
    kind TEXT NOT NULL,                             -- item | kit | sqm | charge
    item_id INTEGER, kit_id INTEGER, model_id INTEGER,
    description TEXT DEFAULT '',
    unit TEXT DEFAULT '',
    qty REAL NOT NULL DEFAULT 0,
    rate INTEGER NOT NULL DEFAULT 0,                -- monthly per unit (rent) or the amount (charge)
    charge_type TEXT DEFAULT '',
    charge_when TEXT DEFAULT '',
    components TEXT DEFAULT '[]',                   -- [{item_id, per_unit}] pieces per billing unit
    inputs TEXT DEFAULT '{}',
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rental_handovers (
    id INTEGER PRIMARY KEY,
    rental_id INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                             -- delivery | return
    number TEXT DEFAULT '',
    date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    data TEXT DEFAULT '{}',                         -- {units: {line_id: n}, items: [{line_id, item_id, qty, damaged, lost, loss_price}]}
    checklist TEXT DEFAULT '[]',
    decision TEXT DEFAULT '',
    our_name TEXT DEFAULT '',
    client_name TEXT DEFAULT '',
    signed INTEGER NOT NULL DEFAULT 0,
    notes TEXT DEFAULT '',
    confirmed_at TEXT DEFAULT '',
    confirmed_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rent_moves (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    item_id INTEGER NOT NULL REFERENCES rent_items(id),
    lot_id INTEGER NOT NULL REFERENCES rent_lots(id),
    rental_id INTEGER REFERENCES rentals(id) ON DELETE CASCADE,
    handover_id INTEGER REFERENCES rental_handovers(id) ON DELETE CASCADE,
    qty REAL NOT NULL,
    kind TEXT NOT NULL,                             -- out | back | lost | damaged | scrap
    note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_rent_moves_lot ON rent_moves(lot_id);
CREATE INDEX IF NOT EXISTS ix_rent_moves_rental ON rent_moves(rental_id);
CREATE TABLE IF NOT EXISTS rental_charges (
    id INTEGER PRIMARY KEY,
    rental_id INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,                             -- install | dismantle | transport | materials | other | loss | damage
    description TEXT DEFAULT '',
    qty REAL NOT NULL DEFAULT 1,
    amount INTEGER NOT NULL DEFAULT 0,
    line_id INTEGER, handover_id INTEGER,
    account TEXT DEFAULT '',
    extract_id INTEGER REFERENCES extracts(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS rental_invoices (
    id INTEGER PRIMARY KEY,
    rental_id INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    extract_id INTEGER NOT NULL REFERENCES extracts(id) ON DELETE CASCADE,
    period_from TEXT NOT NULL,
    period_to TEXT NOT NULL
);

-- Product & unit sales: a catalog (items, real-estate units, services) with photos / clips / 3D models, sales
-- contracts with payment plans (cash, deferred, installments with interest), post-dated cheques. Money is journal
-- entries with ref SO:<code>; the invoice is an ordinary sales invoice (extract) so VAT / e-invoicing work as usual.
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    name_ar TEXT DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'item',          -- item (stock / quantity), unit (one of a kind: apartment, villa, car), service
    category TEXT DEFAULT '',
    unit TEXT DEFAULT '',
    price INTEGER NOT NULL DEFAULT 0,           -- list price, minor units
    cost INTEGER NOT NULL DEFAULT 0,            -- cost per unit for the cost of sales (when not taken from stock)
    vat_code TEXT DEFAULT '',
    revenue_account TEXT DEFAULT '',
    inventory_account TEXT DEFAULT '',          -- where the cost sits until sold (units / goods not in the stock module)
    bu_id INTEGER REFERENCES business_units(id) ON DELETE SET NULL,   -- project the unit belongs to
    stock_item TEXT DEFAULT '',                 -- sold from the stock module (name of the stock item)
    warehouse_id INTEGER REFERENCES warehouses(id) ON DELETE SET NULL,
    specs TEXT DEFAULT '[]',                    -- [[label, value], ...] area, floor, rooms, colour, size...
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'available',   -- available, reserved, sold, delivered, hidden
    cover_media INTEGER,
    created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS product_media (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'photo',         -- photo, video, model (3D), tour (360 / virtual tour), file
    title TEXT DEFAULT '',
    url TEXT DEFAULT '',                        -- a link (YouTube, Vimeo, Sketchfab, Drive, .mp4, .glb ...)
    mime TEXT DEFAULT '',
    data TEXT DEFAULT '',                       -- an uploaded file, base64
    thumb TEXT DEFAULT '',                      -- small preview (data URL) for lists
    size INTEGER DEFAULT 0,
    seq INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pm_product ON product_media(product_id, seq);
CREATE TABLE IF NOT EXISTS sales_orders (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    date TEXT NOT NULL,
    customer TEXT NOT NULL,
    bu_id INTEGER REFERENCES business_units(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'draft',       -- draft, confirmed, delivered, cancelled
    revenue_on TEXT NOT NULL DEFAULT 'sale',    -- sale: invoiced at once; delivery: payments are advances until handover
    vat_code TEXT DEFAULT '',
    wht_code TEXT DEFAULT '',
    plan TEXT DEFAULT '{}',
    price INTEGER NOT NULL DEFAULT 0,           -- what the customer owes for the goods (net + VAT − WHT)
    markup INTEGER NOT NULL DEFAULT 0,          -- installment interest on top
    delivery_due TEXT DEFAULT '',
    delivered_on TEXT DEFAULT '',
    extract_id INTEGER REFERENCES extracts(id) ON DELETE SET NULL,
    salesperson TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sale_lines (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,
    seq INTEGER DEFAULT 0,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    description TEXT DEFAULT '',
    unit TEXT DEFAULT '',
    qty REAL NOT NULL DEFAULT 1,
    price INTEGER NOT NULL DEFAULT 0,
    discount INTEGER NOT NULL DEFAULT 0         -- on the whole line, minor units
);
CREATE TABLE IF NOT EXISTS sale_installments (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,
    seq INTEGER DEFAULT 0,
    due TEXT NOT NULL,
    amount INTEGER NOT NULL,
    kind TEXT DEFAULT 'installment',            -- down, extra, installment, balance
    label TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sale_cheques (
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,
    number TEXT DEFAULT '',
    bank TEXT DEFAULT '',
    due TEXT NOT NULL,
    amount INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',     -- pending (in hand / under collection), cleared, bounced
    entry_id INTEGER,
    settled_entry INTEGER,
    settled_on TEXT DEFAULT ''
);

-- Bank reconciliation: the bank's statements and lines; a line's matching vouchers carry meta {"bank": {account: line id}}.
CREATE TABLE IF NOT EXISTS bank_statements (
    id INTEGER PRIMARY KEY,
    account TEXT NOT NULL,
    name TEXT DEFAULT '',
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    opening INTEGER NOT NULL DEFAULT 0,
    closing INTEGER NOT NULL DEFAULT 0,
    book_from TEXT DEFAULT '',                 -- vouchers before this are taken as already cleared
    status TEXT NOT NULL DEFAULT 'open',       -- open, reconciled
    reconciled_on TEXT DEFAULT '',
    reconciled_by TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bank_lines (
    id INTEGER PRIMARY KEY,
    statement_id INTEGER NOT NULL REFERENCES bank_statements(id) ON DELETE CASCADE,
    seq INTEGER DEFAULT 0,
    date TEXT NOT NULL,
    description TEXT DEFAULT '',
    ref TEXT DEFAULT '',
    amount INTEGER NOT NULL,                   -- + money in, − money out (minor units)
    balance INTEGER,
    status TEXT NOT NULL DEFAULT 'open',       -- open, matched, ignored
    ticked INTEGER NOT NULL DEFAULT 0          -- made by ticking a voucher (paper statement), not imported
);
CREATE INDEX IF NOT EXISTS ix_bl_stmt ON bank_lines(statement_id, date);
CREATE TABLE IF NOT EXISTS bank_rules (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL,
    direction TEXT DEFAULT 'any',
    account TEXT NOT NULL,
    party TEXT DEFAULT '',
    memo TEXT DEFAULT '',
    vat_code TEXT DEFAULT '',
    bu_id INTEGER REFERENCES business_units(id) ON DELETE SET NULL,
    active INTEGER NOT NULL DEFAULT 1
);

-- Recurring entries: a voucher template and a schedule; each due date is recorded once (posted or skipped).
CREATE TABLE IF NOT EXISTS recurring (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    payload TEXT DEFAULT '{}',
    unit TEXT NOT NULL DEFAULT 'month',
    every INTEGER NOT NULL DEFAULT 1,
    start TEXT NOT NULL,
    end_date TEXT DEFAULT '',
    max_count INTEGER DEFAULT 0,
    month_end INTEGER DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'review',       -- auto, review
    active INTEGER NOT NULL DEFAULT 1,
    last_error TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS recurring_runs (
    id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES recurring(id) ON DELETE CASCADE,
    due TEXT NOT NULL,
    skipped INTEGER NOT NULL DEFAULT 0,
    entries INTEGER DEFAULT 0,
    UNIQUE(template_id, due)
);

-- Cash custody (petty cash advances) held by employees; movements are journal entries with ref CU:<code>...
CREATE TABLE IF NOT EXISTS custodies (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    holder TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'temporary',     -- temporary (settle by a date) | permanent (standing float)
    purpose TEXT DEFAULT '',
    account TEXT NOT NULL REFERENCES accounts(code) ON UPDATE CASCADE,
    float_amount INTEGER NOT NULL DEFAULT 0,    -- limit / standing float
    issued_on TEXT DEFAULT '',
    due_date TEXT DEFAULT '',
    opening_balance INTEGER NOT NULL DEFAULT 0,
    bu_id INTEGER REFERENCES business_units(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'open',
    closed_on TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);

-- Taxes: VAT and withholding (WHT) codes with their Egyptian e-invoice (ETA) tax types
CREATE TABLE IF NOT EXISTS tax_codes (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_ar TEXT DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('vat','wht')),
    rate REAL NOT NULL DEFAULT 0,
    eta_type TEXT DEFAULT '',
    eta_subtype TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1
);

-- Customers & suppliers master (names match the party on vouchers)
CREATE TABLE IF NOT EXISTS parties (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL DEFAULT 'customer' CHECK (type IN ('customer','supplier','both','employee','other')),
    receiver_type TEXT NOT NULL DEFAULT 'B' CHECK (receiver_type IN ('B','P','F')),
    tax_id TEXT DEFAULT '',
    national_id TEXT DEFAULT '',
    country TEXT DEFAULT 'EG',
    governate TEXT DEFAULT '',
    region TEXT DEFAULT '',
    street TEXT DEFAULT '',
    building TEXT DEFAULT '',
    postal_code TEXT DEFAULT '',
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    default_vat TEXT DEFAULT '',
    default_wht TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    category TEXT DEFAULT '',
    contact_person TEXT DEFAULT '',
    phone2 TEXT DEFAULT '',
    whatsapp TEXT DEFAULT '',
    specialty TEXT DEFAULT '',
    vehicle TEXT DEFAULT '',
    bank_details TEXT DEFAULT ''
);

-- This device's link to a shared workspace (Supabase). Never replicated, exported or erased.
CREATE TABLE IF NOT EXISTS cloud_local (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Users, roles & permissions
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT DEFAULT '',
    pw_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    perms TEXT NOT NULL DEFAULT '{}',      -- {"module": "view"|"edit"}
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts TEXT DEFAULT (datetime('now')),
    username TEXT,
    action TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log(ts);

-- Images used on documents and in the app (logo, watermark...). data = base64.
CREATE TABLE IF NOT EXISTS assets (
    key  TEXT PRIMARY KEY,
    mime TEXT NOT NULL DEFAULT 'image/png',
    w    INTEGER DEFAULT 0,
    h    INTEGER DEFAULT 0,
    data TEXT NOT NULL
);

-- Client offers (design packages, execution, supervision...) built from editable offer types.
-- content = {"lang", "title", "intro", "sections": [...], "closing"} (JSON)
CREATE TABLE IF NOT EXISTS offer_types (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL,
    type_id INTEGER REFERENCES offer_types(id) ON DELETE SET NULL,
    date TEXT,
    client TEXT DEFAULT '',
    project TEXT DEFAULT '',
    bu_id INTEGER REFERENCES business_units(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'Draft',
    content TEXT NOT NULL DEFAULT '{}'
);
"""

# (code, name, type, subtype, role)
# A full chart for a design/contracting SME. Subtypes map each account onto the
# income statement (EBITDA → EBIT → EBT → Net), balance sheet and cash flow.
DEFAULT_ACCOUNTS = [
    # ---- Assets
    ("1010", "Cash on Hand", "Asset", "cash", "cash"),
    ("1020", "Bank Account", "Asset", "cash", "bank"),
    ("1030", "Petty Cash", "Asset", "cash", None),
    ("1035", "Cash Custody with Employees", "Asset", "other_current_asset", "custody"),
    ("1100", "Accounts Receivable — Clients", "Asset", "receivable", "ar"),
    ("1120", "Notes Receivable (Cheques Under Collection)", "Asset", "receivable", "notes_receivable"),
    ("1150", "Retention Receivable", "Asset", "receivable", None),
    ("1160", "Employee Advances", "Asset", "other_current_asset", None),
    ("1170", "Due from Partners", "Asset", "other_current_asset", "partner_receivable"),
    ("1175", "Loans Receivable (Short-term)", "Asset", "other_current_asset", "loans_receivable"),
    ("1180", "Unbilled Revenue (Work Done, Not Yet Invoiced)", "Asset", "other_current_asset", None),
    ("1185", "Accrued Interest Receivable", "Asset", "other_current_asset", "interest_receivable"),
    ("1190", "Allowance for Doubtful Accounts", "Asset", "allowance_doubtful", "doubtful_allowance"),
    ("1200", "Materials Inventory", "Asset", "inventory", "inventory"),
    ("1210", "Goods & Units for Sale", "Asset", "inventory", "goods_inventory"),
    ("1300", "Prepaid Expenses", "Asset", "prepaid", None),
    ("1310", "Supplier Advances", "Asset", "prepaid", None),
    ("1320", "VAT Recoverable (Input)", "Asset", "other_current_asset", "vat_input"),
    ("1330", "Withholding Tax Receivable (deducted by clients)", "Asset", "other_current_asset", "wht_receivable"),
    ("1340", "Letters of Guarantee — Cash Margin", "Asset", "other_current_asset", None),
    ("1400", "Furniture & Office Equipment", "Asset", "fixed_asset", "fixed_asset"),
    ("1410", "Tools & Site Equipment", "Asset", "fixed_asset", None),
    ("1415", "Rental Equipment (Scaffolding, Elevators, Cradles)", "Asset", "fixed_asset", "rental_fleet"),
    ("1416", "Accumulated Depreciation — Rental Equipment", "Asset", "accumulated_depreciation", "rental_accum"),
    ("1420", "Vehicles", "Asset", "fixed_asset", None),
    ("1430", "Computers & IT Equipment", "Asset", "fixed_asset", None),
    ("1440", "Land", "Asset", "fixed_asset", None),
    ("1450", "Buildings & Leasehold Improvements", "Asset", "fixed_asset", None),
    ("1460", "Assets Under Construction", "Asset", "fixed_asset", None),
    ("1490", "Accumulated Depreciation", "Asset", "accumulated_depreciation", "accum_depreciation"),
    ("1500", "Software & Licences", "Asset", "intangible", "intangible"),
    ("1510", "Brand, Website & Design Library", "Asset", "intangible", None),
    ("1520", "Goodwill", "Asset", "intangible", None),
    ("1590", "Accumulated Amortization", "Asset", "accumulated_amortization", "accum_amortization"),
    ("1600", "Security Deposits", "Asset", "other_noncurrent_asset", None),
    ("1610", "Loans Receivable (Long-term)", "Asset", "other_noncurrent_asset", "loans_receivable_lt"),
    ("1700", "Long-term Investments", "Asset", "investment", "investments"),
    # ---- Liabilities
    ("2010", "Accounts Payable — Suppliers", "Liability", "payable", "ap"),
    ("2015", "Subcontractors Payable", "Liability", "payable", None),
    ("2020", "Accrued Payroll", "Liability", "accrued", "accrued_payroll"),
    ("2030", "Accrued Expenses", "Liability", "accrued", None),
    ("2040", "Social Insurance Payable", "Liability", "accrued", None),
    ("2050", "Notes Payable (Cheques Issued)", "Liability", "payable", None),
    ("2060", "Accrued Interest Payable", "Liability", "accrued", "accrued_interest"),
    ("2100", "Client Advances (Unearned Revenue)", "Liability", "deferred_revenue", "client_advances"),
    ("2105", "Security Deposits from Clients", "Liability", "other_current_liability", "rental_deposits"),
    ("2106", "Deferred Installment Interest", "Liability", "other_current_liability", "installment_deferred"),
    ("2110", "Retention Payable", "Liability", "other_current_liability", None),
    ("2120", "Profit Distributions Payable to Partners", "Liability", "dividends_payable", "dividends_payable"),
    ("2130", "Due to Departing Partners", "Liability", "other_current_liability", "partner_payable"),
    ("2200", "VAT Payable (Output)", "Liability", "tax_payable", "vat_output"),
    ("2210", "Withholding Tax Payable (deducted from suppliers)", "Liability", "tax_payable", "wht_payable"),
    ("2220", "Income Tax Payable", "Liability", "tax_payable", "income_tax_payable"),
    ("2300", "Short-term Loans", "Liability", "short_term_debt", "short_term_debt"),
    ("2310", "Credit Card Payable", "Liability", "short_term_debt", None),
    ("2400", "Long-term Loans", "Liability", "long_term_debt", "long_term_debt"),
    ("2500", "Partner Loans", "Liability", "long_term_debt", "partner_loans"),
    ("2510", "End-of-Service Provision", "Liability", "other_noncurrent_liability", None),
    # ---- Equity
    ("3010", "Partner Capital", "Equity", "capital", "capital"),
    ("3020", "Partners' Current Accounts", "Equity", "partner_current", "partner_current"),
    ("3030", "Share Premium", "Equity", "other_equity", "share_premium"),
    ("3100", "Retained Earnings", "Equity", "retained_earnings", "retained"),
    ("3110", "Legal Reserve", "Equity", "reserves", "legal_reserve"),
    ("3120", "General Reserve", "Equity", "reserves", None),
    ("3200", "Partner Drawings / Dividends", "Equity", "drawings", "drawings"),
    # ---- Revenue
    ("4010", "Project Revenue — Execution", "Revenue", "operating_revenue", "revenue"),
    ("4020", "Design Fees", "Revenue", "operating_revenue", "design_revenue"),
    ("4030", "Supervision / Management Fees", "Revenue", "operating_revenue", None),
    ("4040", "Furniture & Supply Sales", "Revenue", "operating_revenue", None),
    ("4050", "Sales Discounts & Allowances", "Revenue", "sales_discount", "sales_discount"),
    ("4060", "Gain on Disposal of Fixed Assets", "Revenue", "other_income", "disposal_gain"),
    ("4070", "Rental Income", "Revenue", "other_income", None),
    ("4080", "Discounts Received", "Revenue", "other_income", None),
    ("4085", "Negotiation Earnings", "Revenue", "operating_revenue", "negotiation_income"),
    ("4045", "Equipment Rental Revenue", "Revenue", "operating_revenue", "rental_revenue"),
    ("4025", "Sales of Goods & Units", "Revenue", "operating_revenue", "product_sales"),
    ("4096", "Installment Interest Income", "Revenue", "other_income", "installment_income"),
    ("4046", "Installation, Dismantling & Transport Revenue", "Revenue", "operating_revenue", "rental_services"),
    ("4075", "Compensation for Lost / Damaged Equipment", "Revenue", "other_income", "rental_compensation"),
    ("4090", "Other Income", "Revenue", "other_income", "other_income"),
    ("4095", "Interest Income", "Revenue", "other_income", "interest_income"),
    # ---- Cost of sales
    ("5005", "Cost of Goods & Units Sold", "COGS", "cogs", "cost_of_sales"),
    ("5010", "Materials", "COGS", "cogs", "materials"),
    ("5020", "Subcontractor Costs", "COGS", "cogs", None),
    ("5030", "Direct Labour (billable)", "COGS", "cogs", "direct_labour"),
    ("5040", "Site Preparation", "COGS", "cogs", None),
    ("5050", "Equipment Rental", "COGS", "cogs", None),
    ("5060", "Transport & Hauling", "COGS", "cogs", None),
    ("5070", "Design Outsourcing & Renders", "COGS", "cogs", None),
    ("5080", "Inventory Adjustments", "COGS", "cogs", "stock_adjust"),
    # ---- Operating expenses (above EBITDA)
    ("6010", "Rent", "Expense", "general_admin", None),
    ("6015", "Repairs & Maintenance", "Expense", "general_admin", None),
    ("6020", "Software & Subscriptions", "Expense", "general_admin", None),
    ("6030", "Marketing & Advertising", "Expense", "marketing", None),
    ("6040", "Utilities (Electricity & Water)", "Expense", "utilities", None),
    ("6045", "Telephone & Internet", "Expense", "utilities", None),
    ("6050", "Office & Admin Salaries", "Expense", "employee", "admin_salaries"),
    ("6055", "Social Insurance & Benefits", "Expense", "employee", None),
    ("6060", "Professional Fees (Legal, Audit)", "Expense", "general_admin", None),
    ("6065", "Bank Charges", "Expense", "general_admin", "bank_charges"),
    ("6070", "Project Overhead (non-billable)", "Expense", "other_operating", "project_overhead"),
    ("6075", "Travel & Transport", "Expense", "general_admin", None),
    ("6078", "Fuel & Vehicle Running Costs", "Expense", "general_admin", None),
    ("6080", "Office Supplies", "Expense", "general_admin", None),
    ("6085", "Insurance", "Expense", "general_admin", None),
    ("6088", "Bad Debts Expense", "Expense", "general_admin", "bad_debts"),
    ("6090", "Other Operating Expenses", "Expense", "other_operating", "other_overhead"),
    # ---- Below EBITDA
    ("7010", "Depreciation Expense", "Expense", "depreciation", "depreciation"),
    ("7020", "Amortization Expense", "Expense", "amortization", "amortization"),
    ("7030", "Interest Expense", "Expense", "interest", "interest"),
    ("7040", "Foreign Exchange Loss / (Gain)", "Expense", "other_nonoperating", None),
    ("7045", "Loss on Disposal of Fixed Assets", "Expense", "other_nonoperating", "disposal_loss"),
    ("7046", "Loss on Rental Equipment (Lost / Scrapped)", "Expense", "other_nonoperating", "rental_loss"),
    ("7050", "Income Tax Expense", "Expense", "income_tax", "income_tax"),
    ("7060", "Bad Debts & Loan Write-offs", "Expense", "other_nonoperating", "bad_debt"),
]

SUBTYPES = {
    "Asset": ["cash", "receivable", "allowance_doubtful", "inventory", "prepaid", "other_current_asset", "fixed_asset",
              "accumulated_depreciation", "intangible", "accumulated_amortization", "investment",
              "other_noncurrent_asset"],
    "Liability": ["payable", "accrued", "deferred_revenue", "tax_payable", "dividends_payable", "other_current_liability",
                  "short_term_debt", "long_term_debt", "other_noncurrent_liability"],
    "Equity": ["capital", "partner_current", "reserves", "retained_earnings", "drawings", "other_equity"],
    "Revenue": ["operating_revenue", "sales_discount", "other_income"],
    "COGS": ["cogs"],
    "Expense": ["employee", "general_admin", "marketing", "utilities", "other_operating",
                "depreciation", "amortization", "interest", "other_nonoperating", "income_tax"],
}
ACCOUNT_NAMES_AR = {'1010': 'النقدية بالصندوق', '1020': 'الحساب البنكي', '1030': 'العهد النقدية', '1416': 'مجمع إهلاك معدات التأجير', '2105': 'تأمينات العملاء (ودائع ضمان)', '1415': 'معدات التأجير (سقالات، مصاعد، سقالات معلقة)', '4045': 'إيرادات تأجير المعدات', '4046': 'إيرادات التركيب والفك والنقل', '4075': 'تعويضات المعدات المفقودة / التالفة', '7046': 'خسائر معدات التأجير (فقد / تكهين)', '1035': 'عهد نقدية لدى العاملين', '1175': 'قروض ممنوحة للغير (قصيرة الأجل)', '1185': 'فوائد مستحقة التحصيل', '1610': 'قروض ممنوحة للغير (طويلة الأجل)', '7060': 'ديون معدومة وإعدام قروض', '1100': 'العملاء (المدينون)', '1150': 'محتجزات لدى العملاء (ضمان أعمال)', '1160': 'سلف العاملين', '1200': 'مخزون الخامات', '1300': 'مصروفات مدفوعة مقدماً', '1310': 'دفعات مقدمة للموردين', '1320': 'ضريبة القيمة المضافة (مدخلات)', '1330': 'ضريبة خصم وتحصيل لدى العملاء', '1400': 'أثاث ومعدات مكتبية', '1410': 'عدد ومعدات مواقع', '1420': 'سيارات', '1430': 'أجهزة حاسب ومعدات تقنية', '1490': 'مجمع الإهلاك', '1500': 'برامج وتراخيص', '1510': 'العلامة التجارية والموقع ومكتبة التصميمات', '1590': 'مجمع الاستهلاك', '1600': 'تأمينات لدى الغير', '2010': 'الموردون (الدائنون)', '2015': 'مقاولو الباطن', '2020': 'أجور مستحقة', '2030': 'مصروفات مستحقة', '2040': 'تأمينات اجتماعية مستحقة', '2100': 'دفعات مقدمة من العملاء (إيرادات مؤجلة)', '2110': 'محتجزات مقاولي الباطن', '2200': 'ضريبة القيمة المضافة (مخرجات)', '2210': 'ضريبة الخصم والتحصيل المستحقة (من الموردين)', '2220': 'ضريبة الدخل المستحقة', '2300': 'قروض قصيرة الأجل', '2310': 'بطاقات ائتمان مستحقة', '2400': 'قروض طويلة الأجل', '2500': 'قروض الشركاء', '3010': 'رأس مال الشركاء', '3100': 'الأرباح المحتجزة', '3200': 'مسحوبات الشركاء / توزيعات الأرباح', '4010': 'إيرادات المشروعات — التنفيذ', '4020': 'أتعاب التصميم', '4030': 'أتعاب الإشراف والإدارة', '4040': 'مبيعات أثاث وتوريدات', '4050': 'خصومات ومسموحات المبيعات', '4085': 'أرباح التفاوض', '4090': 'إيرادات أخرى', '4095': 'إيرادات فوائد', '5010': 'خامات', '5020': 'تكاليف مقاولي الباطن', '5030': 'عمالة مباشرة', '5040': 'تجهيز الموقع', '5050': 'إيجار معدات', '5060': 'نقل وتشوين', '5070': 'تصميم خارجي ورندرات', '5080': 'تسويات المخزون', '6010': 'الإيجار', '6020': 'برامج واشتراكات', '6030': 'دعاية وإعلان', '6040': 'كهرباء ومياه', '6045': 'هاتف وإنترنت', '6050': 'رواتب إدارية', '6055': 'تأمينات اجتماعية ومزايا', '6060': 'أتعاب مهنية (قانونية ومراجعة)', '6065': 'مصروفات بنكية', '6070': 'مصروفات المشروعات غير المحملة', '6075': 'انتقالات وسفر', '6080': 'أدوات مكتبية', '6085': 'تأمين', '6090': 'مصروفات تشغيلية أخرى', '7010': 'مصروف الإهلاك', '7020': 'مصروف الاستهلاك', '7030': 'مصروفات الفوائد', '7040': 'خسائر (أرباح) فروق العملة', '7050': 'مصروف ضريبة الدخل'}
ACCOUNT_NAMES_AR.update({'4025': 'مبيعات البضائع والوحدات', '4096': 'إيرادات فوائد التقسيط', '2106': 'فوائد تقسيط مؤجلة',
                         '5005': 'تكلفة البضاعة والوحدات المباعة', '1210': 'بضائع ووحدات بغرض البيع'})
ACCOUNT_NAMES_AR.update({'1120': 'أوراق القبض (شيكات تحت التحصيل)', '1170': 'مستحق من الشركاء', '1180': 'إيرادات مستحقة غير مفوترة (أعمال منفذة)', '1190': 'مخصص الديون المشكوك في تحصيلها', '1340': 'غطاء خطابات الضمان', '1440': 'أراضي', '1450': 'مباني وتحسينات أماكن مستأجرة', '1460': 'مشروعات تحت التنفيذ', '1520': 'الشهرة', '1700': 'استثمارات طويلة الأجل', '2050': 'أوراق الدفع (شيكات صادرة)', '2060': 'فوائد مستحقة', '2120': 'توزيعات أرباح مستحقة للشركاء', '2130': 'مستحق للشركاء المنسحبين', '2510': 'مخصص مكافأة نهاية الخدمة', '3020': 'جاري الشركاء', '3030': 'علاوة إصدار / علاوة حصص', '3110': 'الاحتياطي القانوني', '3120': 'الاحتياطي العام', '4060': 'أرباح بيع أصول ثابتة', '4070': 'إيرادات إيجار', '4080': 'خصم مكتسب', '6015': 'صيانة وإصلاحات', '6078': 'وقود ومصروفات سيارات', '6088': 'ديون معدومة', '7045': 'خسائر بيع أصول ثابتة'})
NEW_IN_V12 = {"1120", "1170", "1180", "1190", "1340", "1440", "1450", "1460", "1520", "1700", "2050", "2060", "2120",
              "2130", "2510", "3020", "3030", "3110", "3120", "4060", "4070", "4080", "6015", "6078", "6088", "7045"}
ROLES_ADDED_V12 = {"2500": "partner_loans", "4095": "interest_income"}

# Contra accounts carry the opposite normal balance of their type.
CONTRA_SUBTYPES = {"accumulated_depreciation", "accumulated_amortization", "allowance_doubtful", "drawings",
                   "sales_discount"}


def normal_for(acc_type: str, subtype: str) -> str:
    debit = acc_type in ("Asset", "COGS", "Expense")
    if subtype in CONTRA_SUBTYPES:
        debit = not debit
    return "Debit" if debit else "Credit"


MODULES = ["dashboard", "business_units", "sales", "shop", "estimating", "purchasing", "stock", "rentals", "payroll", "assets",
           "accounting", "partners", "reports", "tax", "import_export", "settings", "users"]

# Second-level permissions: each screen can have its own level, or inherit its section's.
SUBMODULES = {
    "dashboard": [],
    "business_units": ["units", "allocation", "profitability"],
    "sales": ["offers", "receipts", "invoices", "receivables", "parties"],
    "estimating": ["boqs", "prices"],
    "purchasing": ["costs", "expenses", "payments", "payables", "contacts", "negotiations", "custody"],
    "shop": ["products", "orders", "installments"],
    "stock": ["balances", "moves", "warehouses"],
    "rentals": ["contracts", "fleet", "calculator"],
    "payroll": [],
    "assets": ["register", "depreciation"],
    "accounting": ["journal", "ledger", "coa", "loans", "bankrec", "recurring"],
    "partners": ["equity", "transactions", "ownership"],
    "reports": ["income_statement", "balance_sheet", "cash_flow", "trial_balance"],
    "tax": ["codes", "report", "einvoice"],
    "import_export": ["export", "import"],
    "settings": ["company", "branding", "menus", "system"],
    "users": ["users", "audit"],
}

DEFAULT_SETTINGS = {
    "company_name": "BEYOND DESIGN",
    "company_tagline": "Interior Design & Finishing",
    "company_address": "",
    "company_tax_id": "",
    "currency": "EGP",
    "fiscal_year_start": "01-01",
    "reserve_pct": "0.40",
    "font_family": "system",
    "font_size": "15",
    "menu_labels": "{}",
    "units": json.dumps(["Task", "pcs", "m2", "m.t", "ML", "kg", "ton", "L", "Lump Sum",
                         "عدد", "م٢", "م.ط", "مقطوعية"], ensure_ascii=False),
    "backup_keep": "14",
    "lock_date": "",
    "company_legal_name": "", "company_cr_no": "", "company_city": "", "company_phone": "", "company_email": "",
    "company_website": "",
    "bank_name": "", "bank_account": "", "bank_holder": "", "bank_swift": "", "bank_iban": "",
    "offer_validity_days": "15", "offer_prefix": "OFR-", "offer_digits": "4",
    "hidden_modules": "[]",
    "new_menu": "[]",
    "entry_types": "",
    "appearance": "{}",
    "branding": "{}",
    "regional": "{}",
}


# code, name, name_ar, kind, rate, ETA tax type, ETA subtype.  Rates are editable — confirm current
# rates with your tax adviser (Egyptian VAT Law 67/2016; withholding under Income Tax Law 91/2005).
DEFAULT_TAX_CODES = [
    ("VAT14", "VAT 14% (standard)", "ضريبة القيمة المضافة 14%", "vat", 0.14, "T1", "V009"),
    ("VAT0-EXP", "VAT 0% — exports", "ضريبة القيمة المضافة 0% — صادرات", "vat", 0.0, "T1", "V001"),
    ("VAT-EXEMPT", "VAT exempt", "معفى من ضريبة القيمة المضافة", "vat", 0.0, "T1", "V003"),
    ("WHT1-C", "WHT 1% — contracting", "خصم وتحصيل 1% — مقاولات", "wht", 0.01, "T4", "W001"),
    ("WHT1-S", "WHT 1% — supplies", "خصم وتحصيل 1% — توريدات", "wht", 0.01, "T4", "W002"),
    ("WHT3", "WHT 3% — services", "خصم وتحصيل 3% — خدمات", "wht", 0.03, "T4", "W004"),
    ("WHT5", "WHT 5% — professional fees", "خصم وتحصيل 5% — أتعاب مهنية", "wht", 0.05, "T4", "W010"),
]

# Settings for the Egyptian Tax Authority (ETA) e-invoicing link. Secrets are never sent to the browser.
ETA_SETTINGS = {
    "eta_env": "preprod", "eta_client_id": "", "eta_client_secret": "", "eta_signer_url": "",
    "eta_doc_version": "1.0", "eta_activity_code": "", "eta_branch_id": "0", "eta_governate": "",
    "eta_region": "", "eta_street": "", "eta_building": "", "eta_postal_code": "", "eta_item_prefix": "EG",
    "eta_default_item_code": "", "eta_default_unit": "EA",
}


class Database:
    """Thread-safe wrapper: one SQLite connection guarded by a lock.

    The app targets a single business (small team), so one serialized
    connection is the simplest thing that is fully correct.
    """

    def __init__(self, path, connection=None):
        self.path = Path(path)
        if str(path) != ":memory:" and connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        if connection is not None:  # browser build: sqlite3-compatible adapter over sql.js
            self.conn = connection
        else:
            self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
            self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:" and connection is None and os.environ.get("BEYOND_ERP_NO_WAL") != "1":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self._init()

    def _init(self):
        with self.lock:
            self.conn.executescript(SCHEMA)
            cur = self.conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
            if cur is None:
                self._add_cols("accounts", [("name_ar", "TEXT DEFAULT ''")])
                self.seed()
            self._migrate()
            self.conn.execute("INSERT OR REPLACE INTO settings VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def _add_cols(self, table, cols):
        have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        return have

    def _migrate(self):
        """Additive, idempotent upgrades for databases created by older versions."""
        have = self._add_cols("accounts", [("name_ar", "TEXT DEFAULT ''")])
        if "name_ar" not in have:
            for code, ar in ACCOUNT_NAMES_AR.items():
                self.conn.execute("UPDATE accounts SET name_ar=? WHERE code=? AND name_ar=''", (ar, code))
        self._add_cols("business_units", [("customer", "TEXT DEFAULT ''"), ("negotiation", "TEXT DEFAULT ''")])
        have = self._add_cols("users", [("email", "TEXT DEFAULT ''")])
        if "email" not in have:  # workspace users created before this column used their email as username
            self.conn.execute("UPDATE users SET email=lower(username) WHERE username LIKE '%_@_%.%'")
        self._add_cols("rates", [("eta_code", "TEXT DEFAULT ''")])
        self._add_cols("fixed_assets", [("method", "TEXT DEFAULT ''"), ("factor", "REAL DEFAULT 0")])
        self._add_cols("parties", [(c, "TEXT DEFAULT ''") for c in ("category", "contact_person", "phone2", "whatsapp",
                                                                    "specialty", "vehicle", "bank_details")])
        self._add_cols("extracts", [("customer", "TEXT DEFAULT ''"), ("vat_code", "TEXT DEFAULT ''"),
                                    ("wht_code", "TEXT DEFAULT ''"), ("eta_uuid", "TEXT DEFAULT ''"),
                                    ("eta_status", "TEXT DEFAULT ''"), ("eta_submission", "TEXT DEFAULT ''"),
                                    ("eta_error", "TEXT DEFAULT ''")])
        # tax accounts & roles for books created before taxes existed
        if self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
            for code, name, typ, sub, role in DEFAULT_ACCOUNTS:
                if role in ("vat_input", "vat_output", "wht_payable", "wht_receivable") and not \
                        self.conn.execute("SELECT 1 FROM accounts WHERE role=?", (role,)).fetchone():
                    if self.conn.execute("SELECT 1 FROM accounts WHERE code=?", (code,)).fetchone():
                        self.conn.execute("UPDATE accounts SET role=? WHERE code=? AND role IS NULL", (role, code))
                    else:
                        self.conn.execute("INSERT INTO accounts(code,name,name_ar,type,subtype,normal,role) "
                                          "VALUES (?,?,?,?,?,?,?)", (code, name, ACCOUNT_NAMES_AR.get(code, ""),
                                                                     typ, sub, normal_for(typ, sub), role))
        self._add_cols("journal_lines", [("party", "TEXT DEFAULT ''")])
        self._add_cols("extract_lines", [("account", "TEXT DEFAULT ''")])
        self._add_cols("rent_items", [("life_months", "INTEGER NOT NULL DEFAULT 120"), ("residual_pct", "REAL NOT NULL DEFAULT 0.1")])
        self._add_cols("rent_lots", [("opening_accum", "INTEGER NOT NULL DEFAULT 0"), ("depr_start", "TEXT DEFAULT ''")])
        self._add_cols("rentals", [("min_days", "INTEGER NOT NULL DEFAULT 0"), ("deposit", "INTEGER NOT NULL DEFAULT 0"),
                                   ("deposit_first", "INTEGER NOT NULL DEFAULT 0")])
        self._add_cols("partners", [("active", "INTEGER NOT NULL DEFAULT 1"), ("joined", "TEXT DEFAULT ''"),
                                    ("left_on", "TEXT DEFAULT ''"), ("capital_interest", "REAL DEFAULT 0"),
                                    ("loan_rate", "REAL DEFAULT 0"),
                                    ("notes", "TEXT DEFAULT ''")])
        if self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] and \
                not self.conn.execute("SELECT 1 FROM settings WHERE key='coa_v2'").fetchone():
            self._add_new_accounts()
        self.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('coa_v2','1')")
        # loans & cash custody accounts — books from before they existed (a used code moves to the next free one)
        if self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
            for code, role in (("6065", "bank_charges"),):
                if not self.conn.execute("SELECT 1 FROM accounts WHERE role=?", (role,)).fetchone():
                    self.conn.execute("UPDATE accounts SET role=? WHERE code=? AND role IS NULL", (role, code))
            for code, name, typ, sub, role in DEFAULT_ACCOUNTS:
                if role not in ("custody", "loans_receivable", "interest_receivable", "loans_receivable_lt", "bad_debt", "rental_fleet",
                                "rental_revenue", "rental_services", "rental_compensation", "rental_loss", "rental_accum",
                                "rental_deposits", "product_sales", "installment_income", "installment_deferred",
                                "cost_of_sales", "goods_inventory") or \
                        self.conn.execute("SELECT 1 FROM accounts WHERE role=?", (role,)).fetchone():
                    continue
                c = code
                while self.conn.execute("SELECT 1 FROM accounts WHERE code=?", (c,)).fetchone():
                    c = str(int(c) + 1)
                self.conn.execute("INSERT INTO accounts(code,name,name_ar,type,subtype,normal,role) VALUES (?,?,?,?,?,?,?)",
                                  (c, name, ACCOUNT_NAMES_AR.get(code, ""), typ, sub, normal_for(typ, sub), role))
        # negotiation earnings (what a supplier knocks off, kept by the company) — books from before it existed
        if self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] and \
                not self.conn.execute("SELECT 1 FROM accounts WHERE role='negotiation_income'").fetchone():
            c = "4085"
            while self.conn.execute("SELECT 1 FROM accounts WHERE code=?", (c,)).fetchone():
                c = str(int(c) + 1)
            self.conn.execute("INSERT INTO accounts(code,name,name_ar,type,subtype,normal,role) VALUES (?,?,?,?,?,?,?)",
                              (c, "Negotiation Earnings", "أرباح التفاوض", "Revenue", "operating_revenue", "Credit",
                               "negotiation_income"))
        if not self.conn.execute("SELECT 1 FROM tax_codes").fetchone():
            self.conn.executemany("INSERT INTO tax_codes(code,name,name_ar,kind,rate,eta_type,eta_subtype) "
                                  "VALUES (?,?,?,?,?,?,?)", DEFAULT_TAX_CODES)
        for k, v in ETA_SETTINGS.items():
            self.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
        for k, v in DEFAULT_SETTINGS.items():  # settings added by later versions
            self.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
        if not self.conn.execute("SELECT 1 FROM settings WHERE key='docs_seeded'").fetchone():
            self.seed_documents()
            self.conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('docs_seeded','1')")

    def _add_new_accounts(self):
        """Books created before version 1.2 get the accounts added then (reserves, partner current accounts,
        disposal gains...). A code already in use moves the new account to the next free code; an existing role wins."""
        for code, role in ROLES_ADDED_V12.items():
            if not self.conn.execute("SELECT 1 FROM accounts WHERE role=?", (role,)).fetchone():
                self.conn.execute("UPDATE accounts SET role=? WHERE code=? AND role IS NULL", (role, code))
        for code, name, typ, sub, role in DEFAULT_ACCOUNTS:
            if code not in NEW_IN_V12 or (role and self.conn.execute("SELECT 1 FROM accounts WHERE role=?",
                                                                      (role,)).fetchone()):
                continue
            c = code
            while self.conn.execute("SELECT 1 FROM accounts WHERE code=?", (c,)).fetchone():
                c = str(int(c) + 1)
            self.conn.execute("INSERT INTO accounts(code,name,name_ar,type,subtype,normal,role) VALUES (?,?,?,?,?,?,?)",
                              (c, name, ACCOUNT_NAMES_AR.get(code, ""), typ, sub, normal_for(typ, sub), role))

    def seed_documents(self, offer_types=True, logo=True):
        """Starter offer types and the default letterhead logo + watermark (all replaceable)."""
        from . import brand_assets
        from .documents import DEFAULT_OFFER_TYPES
        if offer_types and not self.conn.execute("SELECT 1 FROM offer_types").fetchone():
            for t in DEFAULT_OFFER_TYPES:
                self.conn.execute("INSERT INTO offer_types(name, content) VALUES (?,?)",
                                  (t["name"], json.dumps({k: v for k, v in t.items() if k != "name"}, ensure_ascii=False)))
        if logo:
            for key, img in (("logo", brand_assets.LOGO), ("watermark", brand_assets.WATERMARK)):
                self.conn.execute("INSERT OR IGNORE INTO assets(key,mime,w,h,data) VALUES (?,?,?,?,?)",
                                  (key, img["mime"], img["w"], img["h"], img["data"]))

    def seed(self):
        for code, name, typ, sub, role in DEFAULT_ACCOUNTS:
            self.conn.execute("INSERT OR IGNORE INTO accounts(code,name,name_ar,type,subtype,normal,role) "
                              "VALUES (?,?,?,?,?,?,?)",
                              (code, name, ACCOUNT_NAMES_AR.get(code, ""), typ, sub, normal_for(typ, sub), role))
        for k, v in DEFAULT_SETTINGS.items():
            self.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
        self.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('coa_v2','1')")
        self.conn.execute("INSERT OR IGNORE INTO warehouses(name, account) VALUES ('Main Store', "
                          "(SELECT code FROM accounts WHERE role='inventory'))")

    # --- helpers -------------------------------------------------------------
    def q(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        with self.lock:
            r = self.conn.execute(sql, params).fetchone()
            return dict(r) if r else None

    def x(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params)

    def tx(self):
        return _Tx(self)

    def setting(self, key, default=None):
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else default

    def set_setting(self, key, value):
        self.x("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, str(value)))

    def acct(self, role):
        """Account code for a system role (e.g. 'bank', 'ar'), whatever it is currently numbered."""
        r = self.one("SELECT code FROM accounts WHERE role=?", (role,))
        if not r:
            raise LookupError(f"No account is assigned the '{role}' role — fix it in Chart of Accounts")
        return r["code"]

    def log(self, username, action, detail=""):
        self.x("INSERT INTO audit_log(ts,username,action,detail) VALUES (?,?,?,?)",
               (now().strftime("%Y-%m-%d %H:%M:%S"), username, action, str(detail)[:500]))

    def backup_to(self, dest):
        if not isinstance(self.conn, _NATIVE):  # browser build: dump the sql.js image
            import js  # type: ignore  # Pyodide only
            Path(dest).write_bytes(bytes(js.globalThis.beyondSqlExport().to_py()))
            return
        with self.lock:
            target = sqlite3.connect(str(dest))
            with target:
                self.conn.backup(target)
            target.close()

    def maintenance(self):
        """Keep the file small: trim old sessions/audit rows, checkpoint WAL, reclaim space."""
        with self.lock:
            self.conn.execute("DELETE FROM sessions WHERE expires < datetime('now')")
            self.conn.execute("DELETE FROM audit_log WHERE ts < datetime('now','-2 years')")
            if isinstance(self.conn, _NATIVE):
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.execute("PRAGMA optimize")
            self.conn.execute("VACUUM")

    def close(self):
        with self.lock:
            self.conn.close()


class _Tx:
    """`with db.tx():` — an atomic transaction that also holds the lock. Nests safely."""

    def __init__(self, db):
        self.db = db
        self.outer = False

    def __enter__(self):
        self.db.lock.acquire()
        self.outer = not self.db.conn.in_transaction
        if self.outer:
            self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.outer:
                self.db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db.lock.release()
        return False
