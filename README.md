# Beyond ERP

A small, complete ERP for a design & contracting business — double-entry accounting, business units
(cost centres), sales invoices / progress billings, bills of quantities, purchasing, multi-warehouse
inventory, payroll, partners' equity and full financial statements — in **English and Arabic**.

It grew out of the *BEYOND Accounting System* Excel workbook and the *Beyond Ledger* artifact, and it
keeps the Excel workbook as a first-class citizen: the app exports a workbook that recalculates every
statement on its own, and imports the same workbook back.

## Shared workspace (Supabase) — the whole team on the same books

Connect the web app, the desktop app and the offline copy to one free **Supabase** project: every person signs in
with their own account, everyone sees the same books, several people can work at the same time, and each user's
permission matrix still applies. Code pushed to GitHub is tested and deployed automatically (database to Supabase,
web app to Cloudflare Pages or GitHub Pages). Step-by-step: **[docs/SUPABASE_SETUP.md](docs/SUPABASE_SETUP.md)**.

## Three ways to run it

| Edition | For | How |
|---|---|---|
| **Desktop** | one PC, or an office network | double-click the start file below |
| **Browser (static site)** | using it from any browser without a server — e.g. **static.app**, Netlify | upload the `web/` folder (or `beyond-erp-web.zip`) |
| **Self-hosted** | a team on your own server | `docker compose up -d` (see Hosting) |

## Start it (Windows · macOS · Linux)

You only need **Python 3.9+** (python.org). Nothing else is required; `openpyxl` is installed
automatically by the launchers and is only used for Excel files.

| OS | Double-click | or in a terminal |
|---|---|---|
| Windows | `start-windows.bat` | `py run.py` |
| macOS | `start-mac.command` (first time: right-click → Open) | `python3 run.py` |
| Linux | `start-linux.sh` | `python3 run.py` |

Your browser opens `http://127.0.0.1:8765`. The first screen creates the administrator account.

```text
python run.py --host 0.0.0.0          # let other computers in the office connect
python run.py --data "D:\Books"       # keep the database in a chosen folder
python run.py import-legacy BEYOND_Accounting_System_V0_6_1.xlsx --replace
python run.py build-workbook BEYOND.xlsx
python run.py reset-password admin
python run.py backup
```

Data lives in one SQLite file in the per-user data folder
(`%APPDATA%\BeyondERP`, `~/Library/Application Support/BeyondERP`, `~/.local/share/BeyondERP`).
A backup is taken automatically once a day and before every "replace" import or maintenance; the newest
14 are kept (configurable).

## What's inside

| Area | Screens |
|---|---|
| Dashboard | KPIs, EBITDA and net margins, 12-month revenue vs costs, cash safety floor, model audit |
| Business units (cost centres) | profitability per unit incl. **shared overhead allocated by a manual % per unit** (one screen to set them all, with an "split evenly across active units" shortcut), drill-down with filterable transactions |
| Offers | design package / execution / supervision offers (or any **offer type** you create): sections you add, reorder and edit (text, pricing with amounts in words, payment terms, bank details, signature), live preview, English or Arabic document → **Word (.docx)** and **PDF** in the BEYOND design-package layout; "create business unit" from an accepted offer |
| Client documents | **Quotation (BOQ)** and **invoice / progress extract** in the house layout (right-to-left, summary with section totals, notes, sections with live formulas; contract vs executed columns on invoices) → **Excel** and **PDF** |
| Sales & receivables | receipt vouchers (revenue / against invoice / client advance), sales invoices & progress billings from a BOQ, receivables |
| Estimating | bills of quantities with cost & margin, price list (unit rates) |
| Purchasing & payables | direct costs (with negotiation discount and deal tracking), operating expenses, payment vouchers, payables per party |
| Inventory | **multiple warehouses** each with its own inventory account; receipts, issues to business units, transfers, count adjustments; moving-average cost |
| Payroll | direct / indirect / administrative labour, wages owed per worker |
| Equity | partners' accounts (capital, current account, drawings, loans, interest owed per partner), ownership per business unit, retained reserve %, cash-floor warning. **Capital contributions** (money, an asset in kind, or converting a loan / current account) and **withdrawals**; **partner loans** with or without interest (daily-balance interest, owed or added to the loan); **profit distributions** paid now or owed; **share transfers** between partners or to a newcomer (replace a partner); **admitting a partner** (capital, bonus or share-premium method, everyone diluted in proportion); **partner exit / buy-out** (settlement paid now or owed, difference borne by the remaining partners); optional **interest on capital** taken from profit before sharing it. Every ownership change is previewed, kept in an ownership history and can be undone (voiding its entry undoes it too) |
| Fixed assets | register (cost, residual value, useful life, location, serial), purchase booked with VAT / on credit / contributed by a partner, or registered as an existing asset with past depreciation; **monthly straight-line depreciation run** that catches up missed months; **sale / write-off** with gain or loss; exportable register |
| Accounting | journal vouchers with a party per line, **entry types** (editable two-line templates: cash ↔ bank, loans, accruals, bad debts, legal reserve… — add your own), void / reverse / edit-with-history, general ledger, **editable chart of accounts** (codes and names, EN + AR; reserves, partners' current accounts, share premium, doubtful-debt allowance, investments, notes receivable / payable, disposal gains and losses) |
| Financial reports | Income Statement (Net sales → Gross profit → **EBITDA → D&A → EBIT → interest → EBT → tax → Net profit**), Balance Sheet, **Cash Flow (indirect method)**, Trial Balance (opening / movement / closing), comparative columns by month / quarter / year, per business unit |
| Exports | every report and every filtered list → **PDF, Excel, Word, CSV**, in English or Arabic |
| Import & export | full Excel workbook (also the import template), empty template, **JSON**, CSV per sheet, legacy BEYOND v0.6–0.9 workbooks; dry-run check; all-or-nothing |
| Tax | **VAT** (input / output) and **withholding tax** (deducted from suppliers / by clients) on purchases, invoices, stock receipts and receipts; editable tax codes with their ETA types; customers & suppliers master with tax IDs and addresses; **tax report** (VAT return figures + withholding schedules by party) |
| E-invoicing (Egypt ETA) | builds ETA v1.0 documents from posted invoices. **Generate for import:** JSON (single / batch) and an Excel batch (Header / Lines / Taxes). **Direct link:** ETA login, signing through your local signing service (USB token), submission, status refresh, cancellation |
| Documents & branding | company **logo** (white background removed automatically), **watermark** (faded logo, separate image or diagonal text; strength, greyscale), colours, document & signature fonts, currency in words (EN/AR), signature & contact block, footer, quotation notes — and a **matrix choosing which generated files get the logo and which get the watermark** (offers Word/PDF, BOQ Excel/PDF, invoices Excel/PDF, statements PDF/Excel/Word, full workbook) |
| System | users with a **permission matrix**: every section and every screen in it set to *No access / View / View + add / Full (edit, void, delete)*, with quick presets — enforced by the server on every request; administrator role; audit log; **rename every menu** (EN and AR); **hide sections or single screens** for everyone; choose and order what appears under **“+ New”** (any document, partner action, asset action or entry type); appearance (app name, sidebar logo, accent colour, sidebar style, density, corners), regional formats (date format, negatives, decimals, Arabic digits); **lock date** ("books closed up to"); hide sections for everyone; backups; database compaction; **Erase data** (administrator, type ERASE; transactions only or full factory reset; automatic backup first) |

### Accounting design

* Every transaction is one balanced journal entry; money is stored in integer piastres, so totals never drift.
* System accounts are found by **role**, not number — you can renumber or rename any account.
* Each account's *statement line* decides where it appears on the Income Statement, Balance Sheet and Cash Flow.
* Corrections keep an audit trail: edits keep history, void leaves a trace, reverse posts the formal correcting entry.
* The model audit checks: trial balance, A = L + E, cash-flow reconciliation, stock = inventory accounts,
  every entry balances, overhead allocation ≤ 100 %.

### E-invoicing setup (Egypt)

1. *Settings → Company*: your 9-digit tax registration number.
2. *Tax & E-invoicing → E-invoicing*: activity code, branch, issuer address, default ETA item code (or set one per
   price-list item), and — for the direct link — the ETA client ID / secret and environment.
3. *Customers & Suppliers*: tax ID (or national ID for individuals) and address for each customer.
4. Post an invoice → its e-invoice panel lists anything missing, previews the document and downloads the JSON.
5. Direct submission needs a **signing service** next to your USB token that accepts
   `POST {"serialized", "sha256", "document"}` and returns `{"signature": "<CAdES-BES base64>"}` (ETA's reference
   signer or an integrator's tool). Test on *pre-production* first; version 0.9 works there without a signature.
   The direct link is not available in the browser edition (browsers block these connections) — use the JSON/Excel export there.

This was built from ETA's published v1.0 document format but has **not yet been tested against the live ETA
system** — run it on pre-production with your credentials before relying on it.

### The Excel workbook

`samples/BEYOND_Accounting_System_V1_0.xlsx` is your V0.6.1 data re-built in the new structure (English, and in the
original's look: "BEYOND DESIGN | SHEET" titles, navy header rows, Arial 11, brown cover with the logo); `samples/BEYOND_Import_Template.xlsx` is the empty template. Data sheets (blue text = input) — including
Tax Codes, Parties and VAT / WHT columns — feed a Trial Balance built from SUMIFS, and the Income Statement,
Balance Sheet, Cash Flow, Tax Summary, business-unit profitability (with the overhead allocation %), partner equity,
payables, quick estimator and model audit all calculate from it. Change the report period on the *Settings* sheet.
Import the file back from *Import & Export*. Every formula is kept far below Excel's 8,192-character limit, and
the builder refuses to write a workbook that would break it (the cause of the earlier "Removed Records" repair).

Changes made while migrating V0.6.1 (reported on import):
* "Advance Payment" cost rows (3 × 10,000) are booked as **supplier advances** (asset 1310), not expenses —
  reclassify them to cost with a journal voucher when the work is delivered.
* Where a row's Total Cost differed from Qty × Unit Cost, Total Cost was kept.
* The **BOQs** sheet becomes the bill of quantities "BOQ — Sammy"; the **Invoice 1** sheet becomes a contract BOQ plus
  the progress billing *Invoice 01* (81,256) for PRJ-2026-002, imported as a **draft** — review and post it.
* Shared overhead starts evenly split across active units, as before; change it under Business Units.
* The 2027 Price Forecast sheet is not carried over (its formulas point at fixed cells of the old layout).

## Tests

```text
python -m unittest discover tests
```

39 tests: partners' equity (transfer, admission, exit, undo, loans and interest, distributions), fixed assets (depreciation, disposal, round trip), entry types and the “+ New” menu, shared workspace (two devices syncing through a stand-in Supabase: simultaneous edits, conflicts, permissions, offline), client documents (BOQ / invoice Excel, offer Word with watermark, Arabic amounts in words), lock date,
view-vs-add-vs-full permissions, erase data, branding, JSON backup of offers & images, posting rules, VAT and withholding tax, e-invoice documents and serialization, the Excel formula-length
guard, legacy BOQ/invoice import, the browser SQL adapter, screen-level permissions,, statement tie-outs, overhead allocation, renumbering, void/reverse, stock limits,
JSON and Excel round-trips, all-or-nothing import, every report in every format and both languages,
passwords, and the HTTP API permission model.

## Why it runs everywhere

* Standard library only (`sqlite3`, `http.server`, `zipfile`); no compiled dependencies, no build step, no internet.
* `pathlib` paths, UTF-8 everywhere, OS-correct data folders, CRLF-safe launchers per OS.
* The UI is plain HTML/CSS/JS served locally with a strict Content-Security-Policy.

## Keeping it lean ("bloat-proof")

* One small dependency, optional. No frameworks, no node_modules, no CDN.
* Every list is paginated server-side (max 500 rows per page); uploads capped at 25 MB.
* Money as integers, indexed queries, one WAL-mode SQLite file.
* Backup rotation, session and audit-log pruning, and a one-click *Compact & optimise* (VACUUM).
* One interchange spec drives Excel, JSON and CSV, so formats cannot drift apart.

## Browser edition (static.app, Netlify, Cloudflare Pages…)

`web/` is a complete static site: the same Python engine runs inside the browser (Pyodide / WebAssembly) on SQLite
compiled to WebAssembly (sql.js). It contains **only .html, .css and .js files, none larger than 3 MB** — the engine's
binaries are packed inside `packs/*.js` — so it works on any static host (whatever file types or MIME types it allows)
and even **opened straight from disk** (double-click `index.html`). ≈19 MB, cached after the first visit.

1. Upload the **contents** of `web/` (or drag `beyond-erp-web.zip`) to static.app / Netlify — keep the `packs` and
   `vendor` folders next to `index.html`. Or unzip it anywhere and open `index.html`.
2. Open the site and create the administrator.
3. Import your data from *Import & Export* (Excel, JSON or CSV).

Important: data is stored **in that browser only** (IndexedDB). Each person/device has its own copy, and clearing
site data erases it — download the JSON or Excel export regularly as your backup. For a shared team database use
the desktop app on your network or the self-hosted edition. Rebuild the folder after code changes with
`python build_web.py --zip`.

## Hosting

`docker compose up -d` runs the app behind Caddy with automatic HTTPS (edit `Caddyfile`). See
[PRODUCT_PLAN.md](PRODUCT_PLAN.md) for the road to one-time licences, self-hosting and a hosted service.
