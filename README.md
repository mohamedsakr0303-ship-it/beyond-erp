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

## Several companies, one website

One workspace can hold any number of companies, each with its own books and members (shared-schema multi-tenancy:
every shared row carries its company and Supabase's row-level security only lets members of a company read it).
After signing in, someone with one company goes straight in; someone with several picks one (top bar → your name →
Companies to switch). Anyone signed in can **create a company** (they become its administrator) or **ask to join**
one with its **invite code** (Settings → Workspace), which that company's administrator approves. Usernames are
unique across the whole workspace, and an administrator can never reset the password of someone who also belongs to
another company.

## Get the app

On the hosted site open the user menu → **Get the app**:
* **Beyond ERP** (Windows installer / Mac app) — opens the website in its own window: same sign-in, same books.
* **Beyond ERP Offline** (Windows installer / Mac app) — the whole app on the computer; the first launch signs in with the
  workspace account and downloads the shared books.
* The installers (`packaging/`) are rebuilt by GitHub Actions only when the app code changes (`erp/`, `run.py`,
  `packaging/`, `requirements.txt`) or when the workflow is run by hand (Actions → deploy → Run workflow); other
  pushes keep the ones already on the site. This keeps the free 2,000 build minutes a month far from running out.
* **Phone / tablet** — the site is installable (Android: ⋮ → Install app; iPhone: Share → Add to Home Screen), opens
  full-screen and keeps an offline copy of the app.
* **Offline browser copy** — the zip described below.

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
| Fixed assets | register (cost, residual value, useful life, location, serial), purchase booked with VAT / on credit / contributed by a partner, or registered as an existing asset with past depreciation; **depreciation settings**: straight line or declining balance (any factor, e.g. double declining), first month = month bought or the month after, default life / residual % / method per asset account, per-asset override, year-by-year schedule preview, changes re-spread only what is left; **monthly depreciation run** that catches up missed months, with a dashboard reminder; rental equipment follows the same policy; **sale / write-off** with gain or loss; exportable register |
| Loans | loans the company **takes** (banks, others) and **gives** (employees, partners, clients); interest-free, declining-balance, flat or fixed-amount interest; equal installments, equal principal, interest-only (bullet), custom or flexible schedules; repayments / collections split into principal and interest with a suggested amount, accrued or capitalised interest, charges, extra drawdowns, write-off / forgiveness; overdue and next-due tracking; loans already in the books registered with their remaining balance; exportable register and schedule |
| Cash custody (petty cash) | cash given to employees — temporary (settle by a date) or a standing float; **expense reports** with receipts, VAT and several business units settle it; money returned, reimbursed, or deducted from salary; overdue settlements flagged; statement per custody |
| Contacts directory | suppliers, subcontractors, drivers, transport companies, technicians, consultants (or any category you type) with contact person, phones, WhatsApp, email, trade, vehicle/plate and bank details; one-tap call / WhatsApp / email; each contact shows what you owe them, what you bought and their latest vouchers, with Pay / Record expense buttons. Supplier and payee fields everywhere show a searchable drop-down of saved contacts — typing a new name still works, with an option to save it as a contact |
| Sales & showroom | catalog of **items** (optionally sold from stock), **units** (apartments, villas, shops, cars, machines — one of a kind, available / reserved / sold / delivered) and **services**, with specifications and **media**: photos (resized on upload), video clips (upload, or YouTube / Vimeo / Drive links), **3D models** (.glb / .gltf exported from 3ds Max, Blender, SketchUp… — rotate, zoom and AR on phones — or Sketchfab links) and 360° tours; full-screen presentation for clients; printable brochure. **Sales contracts** paid in full, deferred, in **installments** (down payment, extra payments such as on delivery, monthly / quarterly / yearly, optional flat interest) or on a custom schedule; revenue on sale or on delivery (advances until handover); invoice is a normal sales invoice (VAT / e-invoice); **post-dated cheques** (under collection → cleared / bounced); refunds with a kept cancellation charge; cancel; quotation / contract / customer statement; **collections follow-up** with overdue days and WhatsApp reminders; dashboard alerts |
| Bank reconciliation | import the bank's statement (CSV / Excel / pasted; English or Arabic headings, day/month or month/day dates detected) or reconcile from a paper statement by ticking vouchers; **auto-match** by amount and date (and one deposit to several receipts), references and names; book bank charges / interest straight from a line (VAT on fees), **bank rules** for repeating lines; deposits in transit, outstanding cheques, difference; mark reconciled (locks the matches); printable reconciliation statement |
| Recurring entries | any voucher (expense, direct cost, payroll, payment, receipt, partner drawing, journal voucher) or the monthly depreciation run on a schedule — every N days / weeks / months / years, month end, until a date or N times; **automatic** (posted when the app opens, never twice — also across devices) or **review** (check the amount, post or skip); {month} in texts; "Make recurring" on any voucher; history; dashboard alert |
| Import on every screen | an **⇅ Import** button on each list screen (costs, expenses, payroll, receipts, payments, invoices + lines, BOQs, price list, customers & suppliers / contacts, products, business units, fixed assets, stock items & moves, warehouses, journal, chart of accounts, partners, tax codes): download the **Excel or CSV template** the app fully supports (bilingual headings, example row, drop-downs, a "How to fill" sheet), or import **your own sheet** — heading row found anywhere, columns matched by English / Arabic names, synonyms and spelling, adjustable by hand and remembered; a check run lists every problem with its row number in your file (downloadable), nothing is saved until it passes |
| Equipment rental | scaffolding (rented **by piece, by frame / set, or by square metre**), construction elevators and cradles (manual / motorized); stock bought in lots and tracked **FIFO**; price offer → **client PO required** → contract signed by both parties → **delivery handover with safety checklist**, signed by both → monthly rent invoices counted by day (+ installation, dismantling, transport, materials) → signed return with lost / damaged pieces charged at their loss price and written off at FIFO cost; **per-piece share of a frame or m² rent** by FIFO cost; **scaffold calculator** with editable formulas (length, height, lifts, bays, ties, bracing, spare %); each contract gets its own business unit for profit and loss; **monthly depreciation** of the equipment per stock lot (useful life, residual %, catch-up, write-offs clear their share); **minimum rental period** (early returns charged the missing days); **security deposits** (receive, apply to invoices, refund; optionally required before delivery); printable offer, contract and handover forms (Arabic / English) |
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
