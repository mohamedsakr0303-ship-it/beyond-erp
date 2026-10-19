# BeyondERP

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
* **BeyondERP** (Windows installer / Mac app) — opens the website in its own window: same sign-in, same books.
* **BeyondERP Offline** (Windows installer / Mac app) — the whole app on the computer. **Not public**, and not built by
  GitHub Actions while this repository is public (anything a public repository publishes is public too): build it on your
  own computer with `pyinstaller packaging/beyond_erp.spec` and keep it private. Older builds stay on the `desktop-latest`
  release — keep that release a draft so only the owner sees it.
* The connected installers (`packaging/`) are rebuilt by GitHub Actions only when the workflow is run by hand (Actions → deploy → Run workflow); other
  pushes keep the ones already on the site. This keeps the free 2,000 build minutes a month far from running out.
* **Phone / tablet** — the site is installable (Android: ⋮ → Install app; iPhone: Share → Add to Home Screen), opens
  full-screen and keeps an offline copy of the app.
* **Offline browser copy** — no longer published; `python build_web.py --zip` still makes one for your own use.

## Device permissions (all optional)

BeyondERP runs without any of them. The first time someone reaches the ERP screens on a device (never on the welcome
page or the sign-in) a small card offers the ones that device can use; each feature also asks again when it is used.
Everyone can see and change theirs in **Settings → Device permissions** (also in the profile menu).

| Permission | What uses it | Phone | Computer browser | Desktop app |
|---|---|:-:|:-:|:-:|
| Notifications | chat messages, approvals, follow-ups, bookings and desk messages while the app is in the background | ✓ (iPhone: installed app only) | ✓ | ✓ |
| Camera | chat camera: photo or video clip | ✓ | ✓ | ✓ |
| Microphone | voice messages; sound in video clips | ✓ | ✓ | ✓ |
| Location | "My day" start / end, only when the company asks for it | ✓ | ✓ | ✓ |
| Clipboard (paste) | "Paste" in right-click menus (Ctrl+V works without it) | — | ✓ (Chrome / Edge) | ✓ |
| Storage (keep data on this device) | stops the browser clearing saved data when space is low (vital for the browser-only edition); saving itself needs no permission | ✓ | ✓ | ✓ |
| Install the app | own icon and window, offline; needed for notifications on iPhone | ✓ | ✓ | (already) |

No permission needed: alert sounds and icon badges, vibration (Android), attaching / pasting / dropping files, copying,
downloads, printing, full screen.

### Notifications when the app is closed (Web Push)

On the hosted app with the shared workspace, each device that allows it (Settings → Device permissions →
"Notifications when the app is closed") gets notifications like WhatsApp even when BeyondERP is closed: chat
messages (following each person's all / mentions / rooms, preview and quiet-hours settings), chat between
companies, reactions, entries waiting for approval and the decision on them, follow-ups assigned to someone and a
reminder at 9:00 on the day they are due, and bookings / enquiries / messages / signatures from the public pages.
The `push` function (supabase/functions/push) signs and encrypts them itself; its keys are made on first use and kept
server-side. `.github/workflows/reminders.yml` sends due reminders every 10 minutes. iPhone needs the app added to the
Home Screen (iOS 16.4+).

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
| People & chat | **profiles** (picture, job title, department, status) with personal preferences (menus open on click or hover, notifications) · **chat**: General for everyone, rooms the super administrator creates for chosen people (partners, accountants, engineers, sales…), direct messages; emoji, any file (pick, drag in or paste pictures), @mentions and @all, edit or take back a message until someone has seen it, "seen" marks; the super administrator renames rooms, changes members, archives (kept, read-only), deletes and **exports any room as a zip** (HTML + text transcript, JSON, every file) · **notifications**: every message / @mentions & direct messages / chosen rooms only / none; sound, vibrate (Android) or silent; pop-ups on computers and installed phone apps while the app is open · **chat between companies** (shared workspace): a super administrator gives a one-time code (48 h) to the other company's super administrator; either side can end it at any time, the history stays readable for both |
| AI assistant | **Claude** (Anthropic, official SDK) **or any other provider** — OpenAI, Google Gemini, Mistral, DeepSeek, xAI, Groq, OpenRouter, Together, local models through Ollama / LM Studio, or any OpenAI-compatible address — installed by a super administrator in Settings → AI assistant (on/off switch): provider and model, thoroughness, standing instructions, who may use it, read-only or change-with-review, sections it must never use, daily limit per person. **Every change it proposes is first run by the ERP without saving** and checked — the ERP accepts it, every journal entry balances, the trial balance and the books checks still hold, and the books still export to the Excel sheets and import back with the same balances — then shown to the person with the effect on the key figures and the entries it will post; nothing is saved until they press *Save and post*, a failed check blocks it, and the ERP refuses any change from the assistant that was not reviewed this way or whose books changed since. It works only through the asking person's own access (every call is a normal API call with their session, and user management, sign-in, licences, backups and its own settings are always off-limits), so nobody can use it to change data they could not change themselves. The provider key stays on the server (desktop), in Supabase behind the `ai` function (shared workspace), or in the browser that installed it (browser-only edition); conversations stay on the device |
| AI assistant — changing data | the super administrator chooses: **never** (read and advise), **each change** checked then approved, **checked together** (all the changes of one request — e.g. a whole stock count — run without saving, the combined outcome shown, approved at once), or **saved directly** once the checks pass. In every mode the ERP checks first (accepted, entries balance, trial balance, books checks, Excel round trip) and blocks what fails; every saved change — or whole batch — has **Undo**, which puts every record back exactly as it was (refused if someone changed those records since), on every device of the shared workspace |
| Business units (cost centres) | profitability per unit incl. **shared overhead allocated by a manual % per unit** (one screen to set them all, with an "split evenly across active units" shortcut), drill-down with filterable transactions. Billing basis: cost-plus %, fixed price, flexible / settled, or **live fee** — nothing billed ahead: the project shows what the client has actually paid so far (receipts, advances, cash sales and cleared cheques, less refunds) against its actual costs so far, as a live fee % and margin, with an optional target fee to compare |
| Projects & contracts | **project progress**: phases with a weight, planned dates and % complete → the project's progress against the plan for today, late phases, earned value (contract × % complete), invoiced %, work done not invoiced, costs and profit; **tasks** with who is responsible, due date, priority and status (overdue ones raise alerts) · **contracts register**: client contracts and subcontracts with a bill of quantities, retention (and when it is due back), advance and its recovery %, VAT / withholding, payment terms; **variation orders** (pending → approved / rejected) change the contract value and end date, and the project's contract value follows · **subcontractor payment certificates** (مستخلصات مقاولي الباطن): cumulative quantity to date per line, approved then posted — cost to the project, owed to the subcontractor, retention held, advance recovered (never more than paid), VAT and withholding; advances, payments and retention release from the contract; reopening is blocked once part of it was paid or released |
| Site | **purchase requisitions** (site / store asks → approval) with **supplier quotes compared side by side** (lowest per line, best single supplier, delivery, VAT) and award → the purchase orders are raised for you · **physical stock counts** (snapshot, count, post only the differences as adjustments; movements during the count respected) · **plant & equipment**: register (linked to its fixed asset), working hours charged to projects at an hourly rate against an equipment running-costs clearing account, fuel straight to the project, **maintenance plans** every N days and/or N working hours with due / overdue alerts, maintenance history · **time & attendance**: daily sheet per worker (present / half day / absent / leave / sick, project, hours, overtime, output), productivity per hour, labour cost per project, and **fills the month's payroll run** |
| E-receipts (Egypt ETA, B2C) | point-of-sale receipts in the ETA e-receipt v1.2 structure; each one's UUID is the SHA-256 of its content and it carries the previous receipt's UUID (chain check on screen); issuing posts the sale (or a return); JSON export or direct submission with the POS's own credentials (kept on the server). Validate on the ETA pre-production system with your registered POS before going live |
| Offers | design package / execution / supervision offers (or any **offer type** you create): sections you add, reorder and edit (text, pricing with amounts in words, payment terms, bank details, signature), live preview, English or Arabic document → **Word (.docx)** and **PDF** in the BEYOND design-package layout; "create business unit" from an accepted offer |
| Client documents | **Quotation (BOQ)** and **invoice / progress extract** in the house layout (right-to-left, summary with section totals, notes, sections with live formulas; contract vs executed columns on invoices) → **Excel** and **PDF** |
| Sales & receivables | receipt vouchers (revenue / against invoice / client advance), sales invoices & progress billings from a BOQ, receivables |
| Estimating | bills of quantities with cost & margin, price list (unit rates) |
| Purchasing & payables | direct costs (with negotiation discount and deal tracking), operating expenses, payment vouchers, payables per party |
| Inventory | **multiple warehouses** each with its own inventory account; receipts, issues to business units, transfers, count adjustments; moving-average cost · **item catalog with prices**: an item can be kept with zero quantity just for its standard cost and selling price (last price paid shown too); stock enters only through receipts / stock moves, never from a journal voucher, so the asset is never counted twice |
| Payroll | direct / indirect / administrative labour, wages owed per worker |
| Equity | partners' accounts (capital, current account, drawings, loans, interest owed per partner), ownership per business unit, retained reserve %, cash-floor warning. **Capital contributions** (money, an asset in kind, or converting a loan / current account) and **withdrawals**; **partner loans** with or without interest (daily-balance interest, owed or added to the loan); **profit distributions** paid now or owed; **share transfers** between partners or to a newcomer (replace a partner); **admitting a partner** (capital, bonus or share-premium method, everyone diluted in proportion); **partner retirement / buy-out** (settlement paid now or owed, difference borne by the remaining partners); **Partners' Equity Statement** — each partner's equity interest (paid-in capital, current account, share of undistributed invoiced profit and of the retained reserve, balances due to / from the partner), with the funding partner's current-account movements; optional **interest on capital** taken from profit before sharing it. Every ownership change is previewed, kept in an ownership history and can be undone (voiding its entry undoes it too) |
| Fixed assets | register (cost, residual value, useful life, location, serial), purchase booked with VAT / on credit / contributed by a partner, or registered as an existing asset with past depreciation; **depreciation settings**: straight line or declining balance (any factor, e.g. double declining), first month = month bought or the month after, default life / residual % / method per asset account, per-asset override, year-by-year schedule preview, changes re-spread only what is left; **monthly depreciation run** that catches up missed months, with a dashboard reminder; rental equipment follows the same policy; **sale / write-off** with gain or loss; exportable register |
| Loans | loans the company **takes** (banks, others) and **gives** (employees, partners, clients); interest-free, declining-balance, flat or fixed-amount interest; equal installments, equal principal, interest-only (bullet), custom or flexible schedules; repayments / collections split into principal and interest with a suggested amount, accrued or capitalised interest, charges, extra drawdowns, write-off / forgiveness; overdue and next-due tracking; loans already in the books registered with their remaining balance; exportable register and schedule |
| Cash custody (petty cash) | cash given to employees — temporary (settle by a date) or a standing float; **expense reports** with receipts, VAT and several business units settle it; money returned, reimbursed, or deducted from salary; overdue settlements flagged; statement per custody |
| Contacts directory | suppliers, subcontractors, drivers, transport companies, technicians, consultants (or any category you type) with contact person, phones, WhatsApp, email, trade, vehicle/plate and bank details; one-tap call / WhatsApp / email; each contact shows what you owe them, what you bought and their latest vouchers, with Pay / Record expense buttons. Supplier and payee fields everywhere show a searchable drop-down of saved contacts — typing a new name still works, with an option to save it as a contact |
| Sales & showroom | catalog of **items** (optionally sold from stock), **units** (apartments, villas, shops, cars, machines — one of a kind, available / reserved / sold / delivered) and **services**, with specifications and **media**: photos (resized on upload), video clips (upload, or YouTube / Vimeo / Drive links), **3D models** (.glb / .gltf exported from 3ds Max, Blender, SketchUp… — rotate, zoom and AR on phones — or Sketchfab links) and 360° tours; full-screen presentation for clients; printable brochure. **Sales contracts** paid in full, deferred, in **installments** (down payment, extra payments such as on delivery, monthly / quarterly / yearly, optional flat interest) or on a custom schedule; revenue on sale or on delivery (advances until handover); invoice is a normal sales invoice (VAT / e-invoice); **post-dated cheques** (under collection → cleared / bounced); refunds with a kept cancellation charge; cancel; quotation / contract / customer statement; **collections follow-up** with overdue days and WhatsApp reminders; dashboard alerts |
| Bank reconciliation | import the bank's statement (CSV / Excel / pasted; English or Arabic headings, day/month or month/day dates detected — or the bank's own statement file: **MT940**, **CAMT.053** (ISO 20022 XML), **OFX / QFX** or **QIF**, with the opening and closing balances read from the file) or reconcile from a paper statement by ticking vouchers; **auto-match** by amount and date (and one deposit to several receipts), references and names; book bank charges / interest straight from a line (VAT on fees), **bank rules** for repeating lines; deposits in transit, outstanding cheques, difference; mark reconciled (locks the matches); printable reconciliation statement |
| Recurring entries | any voucher (expense, direct cost, payroll, payment, receipt, partner drawing, journal voucher) or the monthly depreciation run on a schedule — every N days / weeks / months / years, month end, until a date or N times; **automatic** (posted when the app opens, never twice — also across devices) or **review** (check the amount, post or skip); {month} in texts; "Make recurring" on any voucher; history; dashboard alert |
| Bulk actions | tick rows (or **select all on the page**) on the journal, every voucher list (costs, expenses, payments, receipts, wage accruals), a business unit's transactions, business units, project progress, tasks, employees, contracts, purchase requisitions and orders, equipment, customers & suppliers, invoices and e-receipts, then **edit** (only the fields you fill in change; entries keep their history), **void** (with a reason), **reverse**, **delete**, approve / post / issue — each record is checked exactly as if done by hand, and anything that could not be done is listed with the reason; choose **10 / 25 / 50 / 100 rows per page** (remembered) |
| Package offers | an offer can be priced without any BOQ: a **Packages** section shows 2–6 tiers (suggested *Essential · Signature · Prestige*, all renamable) side by side with what each includes (✓ / —) and its price; mark the one the client chose and it becomes the offer's value and drives the payment terms (until then they show percentages only); a ready *Design Packages* offer type comes with new books |
| Welcome & tour | a new company's creator is offered a **quick setup** (name, slogan, logo, currency, financial year, partners and shares, the funding partner, reserve %) and a **guided tour** of the app — both optional; later from Settings → Company (Quick setup) and the user menu (Take the tour) |
| Help & support | user menu → *Help & support*: report a problem, ask a question or suggest something; each report gets a reference (BE-yymmdd-nnnn), is kept in the books (people see their own, administrators the whole company's, with sent / answered / solved) and is sent on WhatsApp — e-mail once the support address exists — with optional technical details (version, edition, screen, recent errors; never passwords or figures) |
| Universal templates | Excel import templates carry the BeyondERP logo with *Company Name* / *Company Slogan* and none of the company's details (placeholders are never imported over real ones); tick *Excel import templates* in Settings → Documents & branding to brand them with your own logo, name and slogan |
| In-app dialogs | every confirmation and question (delete, void reason, replace on import…) opens in the app's own themed dialog — never the browser's pop-up |
| Import on every screen | an **⇅ Import** button on each list screen (costs, expenses, payroll, receipts, payments, invoices + lines, BOQs, price list, customers & suppliers / contacts, products, business units, fixed assets, stock items & moves, warehouses, journal, chart of accounts, partners, tax codes): download the **Excel or CSV template** the app fully supports (bilingual headings, example row, drop-downs, a "How to fill" sheet), or import **your own sheet** — heading row found anywhere, columns matched by English / Arabic names, synonyms and spelling, adjustable by hand and remembered; a check run lists every problem with its row number in your file (downloadable), nothing is saved until it passes |
| Import for every module | layouts (Excel / CSV, standalone or as sheets of the full-ERP workbook) also for **loans & repayments, cash custody & expense reports, rental equipment, stock lots & contracts, sales contracts with payment plans, bank statement lines, recurring entries, project phases & tasks, contracts, variation orders, subcontractor certificates & payments, purchase requisitions & supplier quotes, stock counts, equipment, maintenance plans & log, equipment usage, attendance and e-receipts** — rows create new records through the module itself, so their money entries and schedules are exactly what the screen makes |
| Demo company | "Sample Company": about six months of invented data on every screen (projects, costs, negotiation, payroll, taxes, BOQ & invoices, stock, assets, loans, custody, rentals with handover & deposit, unit sales on installments with cleared / bounced cheques, recurring entries, a reconciled and an open bank statement); offered when creating a new company, loadable / replaceable from Settings → Data, downloadable as a filled Excel workbook and as a demo file per screen |
| Equipment rental | scaffolding (rented **by piece, by frame / set, or by square metre**), construction elevators and cradles (manual / motorized); stock bought in lots and tracked **FIFO**; price offer → **client PO required** → contract signed by both parties → **delivery handover with safety checklist**, signed by both → monthly rent invoices counted by day (+ installation, dismantling, transport, materials) → signed return with lost / damaged pieces charged at their loss price and written off at FIFO cost; **per-piece share of a frame or m² rent** by FIFO cost; **scaffold calculator** with editable formulas (length, height, lifts, bays, ties, bracing, spare %); each contract gets its own business unit for profit and loss; **monthly depreciation** of the equipment per stock lot (useful life, residual %, catch-up, write-offs clear their share); **minimum rental period** (early returns charged the missing days); **security deposits** (receive, apply to invoices, refund; optionally required before delivery); printable offer, contract and handover forms (Arabic / English) |
| Accounting | journal vouchers with a party per line, **entry types** (editable two-line templates: cash ↔ bank, loans, accruals, bad debts, legal reserve… — add your own), void / reverse / edit-with-history, general ledger, **editable chart of accounts** (codes and names, EN + AR; reserves, partners' current accounts, share premium, doubtful-debt allowance, investments, notes receivable / payable, disposal gains and losses) |
| Financial reports | Income Statement (Net sales → Gross profit → **EBITDA → D&A → EBIT → interest → EBT → tax → Net profit**), Balance Sheet, **Cash Flow (indirect method)**, Trial Balance (opening / movement / closing), comparative columns by month / quarter / year, per business unit |
| Exports | every report and every filtered list → **PDF, Excel, Word, CSV**, in English or Arabic |
| Import & export | full Excel workbook (also the import template), empty template, **JSON**, CSV per sheet, legacy BEYOND v0.6–0.9 workbooks; dry-run check; all-or-nothing |
| Full backup | the **JSON** backup, the full **Excel workbook** and the **Excel + files zip** each carry everything: the books and every module, settings, looks, logos and letterheads, files attached to entries, every chat (rooms, new rooms people create, direct conversations — with photos, voice notes, reactions and read marks), profiles, the people with their roles and permissions, approval requests, CRM, healthcare, online desk, knowledge base, automations, working days, the activity log, support reports and AI assistant conversations. A **super administrator's** backup also keeps the **keys of connected tools** — API keys made for outside tools (the same key keeps working after a restore), the e-invoice / e-receipt (ETA) credentials and, on the desktop app, the AI provider key — unless they untick it; nobody else's backup or import carries them. A full restore brings back people who are not in the books yet **without a password**: an administrator sets one in Users, or they sign in through the shared workspace. Never in a backup: passwords and sign-in sessions. On the hosted app the AI and ETA secrets live in the workspace server, not in the books |
| Subscriptions & recurring bills | Procurement → **Subscriptions & Recurring Bills**: what the company pays every week / month / quarter / year (software, internet, services…) with its next due date, price note in its own currency (e.g. "USD 200"), expense account and **who carries the cost** — shared overhead (spread over projects by their %), **company cost not allocated to projects**, one project, or **split between projects by %**. "Paid" books the expense(s) and moves the due date one cycle on; monthly / yearly totals; payment history. **Reminders** N days before, at the time you choose, to the people you choose — in the bell and as phone / computer notifications even with the app closed (shared workspace) |
| My alerts | Home → **My Alerts**: each person picks what they want to be told about — attendance not recorded by a time, absences today, field team not started "My day", subscriptions due, customer cheques and loan instalments due, customers overdue, cash & bank below an amount, approvals waiting, live-fee projects whose costs pass the client's payments, budgets passed X%, payroll not posted by day N, maintenance due, low stock, contracts ending — with one-tap suggestions, an optional "check after" time, repeat interval and an on/off switch. Alerts land in the bell and a "Recent alerts" list, and on all their devices; only about what each person may see. People who manage automations can alert others. **Alerts reach people with their app closed**: any member's app checks everyone's alerts it may see shortly after each change and every few minutes, so an alert someone's change makes true lands in its people's bell at once; with the shared workspace each alert's next due time also goes to the push service, which rings phones at that time even when nobody has the app open (and withdraws it when a later change makes it untrue — a bill paid, attendance recorded). The desktop / self-hosted server checks them itself every 5 minutes |
| Database backup file | the whole database as one **.sqlite3** file (super administrators): download it from Import & export, and restore it from **Import data → Database backup file** — on the desktop, in the browser and in the shared workspace (the restore reaches every device); a file from an older version is upgraded first |
| What can be imported | full Excel workbook (EN/AR) · Excel + attached files (.zip) · JSON backup · database backup file (.sqlite3) · the empty or demo-filled import template · the template of each of the 55 kinds of records (Excel or CSV) · your own sheets from any screen's ⇅ Import (columns matched by English / Arabic names) · a Google Sheets link · older BEYOND workbooks v0.6 – v1.4 |
| Tax | **VAT** (input / output) and **withholding tax** (deducted from suppliers / by clients) on purchases, invoices, stock receipts and receipts; editable tax codes with their ETA types; customers & suppliers master with tax IDs and addresses; **tax report** (VAT return figures + withholding schedules by party) |
| E-invoicing (Egypt ETA) | builds ETA v1.0 documents from posted invoices. **Generate for import:** JSON (single / batch) and an Excel batch (Header / Lines / Taxes). **Direct link:** ETA login, signing through your local signing service (USB token), submission, status refresh, cancellation |
| Documents & branding | company **logo** (white background removed automatically), **watermark** (faded logo, separate image or diagonal text; strength, greyscale), colours, document & signature fonts, currency in words (EN/AR), signature & contact block, footer, quotation notes — and a **matrix choosing which generated files get the logo and which get the watermark** (offers Word/PDF, BOQ Excel/PDF, invoices Excel/PDF, statements PDF/Excel/Word, full workbook) |
| Forecasting | *Financial Reports → Forecasting*: nothing is imported — it reads the books and moves them forward quarter by quarter (2–12 quarters). **Drivers** (Egypt CPI, wages, USD/EGP, fuel & energy, global dollar prices) are filled from **World Bank open data** with one click (source and date kept; wages and fuel start equal to CPI) and can be edited. Each category (price-list category or item category) is a **mix** of local material, imported content, labour and energy (quick presets), giving its own escalation. Sections: client price list, item unit costs, spending per account (direct costs — materials, subcontractors, labour, plant — and overheads), payroll, depreciation & amortisation, loan instalments due, sales (revenue trend and unit prices). Any cell can be overridden; overrides stay marked and survive refreshes. **Price outlook**: every purchase voucher (direct cost, expense, goods receipt) and the *Items & prices* list show where that price is heading (↑ +13.9% by Q4 2027) |
| Arranging the menu | drag screens and sections (or right-click: rename, move, reorder, duplicate, hide); right-click → **Lock in place** keeps a screen or a whole section (with its screens) from being dragged, moved, renamed or hidden until it is unlocked, and **Lock the whole menu** freezes everything — for people with “Arrange menus & forms” |
| Two-step sign-in | anyone turns it on in their profile: after the password, a 6-digit code from an authenticator app (QR code or key; 10 one-time recovery codes on the desktop edition). On the shared workspace it uses Supabase's own two-step sign-in and the database refuses the books to a session that has not passed it. A super administrator can require it for administrators or everyone (Settings → System) and turn it off for someone who lost their phone (Users); `python run.py reset-two-step <user>` on the computer that holds the books. Forgotten passwords: email accounts get a reset link; username-only accounts get a new password from a super administrator (or `python run.py reset-password <user>`) |
| Project-level access | in Users a super administrator can limit a person to **chosen projects**: they then see only those projects' entries, documents, tasks and reports (a report runs for one of their projects), record only on them (an expense with no project would be company overhead, so it is refused), and never open company-wide money — cash & bank, bank reconciliation, taxes, partners & equity, loans, payroll runs, forecasts, year-end, full exports. On top of the permission grid; super administrators are never limited |
| Year-end close | Accounting → **Year-End Close**: every financial year (from the financial year start in Settings → Company) with its profit and a checklist read from the books — trial balance, entries waiting for approval, banks reconciled to the year end, depreciation, payroll and recurring entries booked, draft invoices, cash below zero, the year before closed. Closing locks the year (lock date) and shows its profit as **retained earnings — closed years** on the balance sheet; no closing entry is posted, so the year's income statement, project reports and budgets keep their real figures. A super administrator can reopen the last closed year |
| Cash forecast | Reports → **Cash Forecast (30/60/90 days)**: cash & bank today plus what the books date to come in and go out — customer installments and cheques, loans lent and borrowed, subscriptions & bills (every cycle), recurring entries, monthly payroll (last posted month's net pay) and purchase orders on their expected delivery date — week by week with the running balance, the lowest point (a warning if it goes below zero) and every item listed. Late amounts can be counted as collected / paid today or left out; balances with no due date (customers, suppliers, taxes) are shown beside it, never spread over invented dates |
| Duplicate warning | an expense, cost, payment, receipt or journal voucher that looks like one already in the books (same type and supplier / customer — or the same description — and amount within 3 days, or the same supplier and reference number) is not saved until the person confirms it is another one; shows the look-alikes with their dates, amounts and who entered them. Can be switched off in Settings → System |
| Searchable pickers | every long account / party / item list becomes a search box: type **5** or **50** to list only accounts in that code range, or words (*payables*, *bank*, Arabic too) to match names, type and sub-type; parties and items narrow as you type |
| Import new rows only | the default import mode: rows already in the books (same key, or for vouchers the same date and lines) are skipped, so a spreadsheet you kept adding to can be re-imported without replacing edits made in the app since |
| App updates | when a new version is published, every open app (website, installed phone / PC app) shows **"A new version is ready — Update now / Later"** within minutes; Update now waits for any save in progress, then reloads into the new version — books, sign-in, drafts and settings are kept. The installed offline desktop app checks the website too: Update now makes a backup, downloads the installer, installs it silently and reopens (the data folder is never touched). **Check for updates** (user menu, and Settings → System & data) asks right away and offers the update even after "Later". The offline desktop app never updates by itself. The hosted apps keep their version while open, but load the newest one at the next start, because everyone in a shared workspace must run the same version |
| Materials | **Journal vouchers take item lines**: item (pick one or type a new one), unit, quantity × unit price = total, and where it goes — **into a store** (the stock goes up on the same voucher, so the asset is counted once) or **used on the project** (materials cost); then enter only who was paid or is owed. **Projects → Materials Used**: every material that went into a project — issued from a store, bought for it, or booked as a direct cost — filtered by dates, project, material, any text and how it came; total cost on top, and the total quantity and average unit cost when one material is picked; click a row for its voucher; print / export |
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
* The 2027 Price Forecast sheet is not carried over (its formulas point at fixed cells of the old layout) — the app's *Forecasting* screen does the same job from live data.

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

## Protection of the code

* The repository is private. The website ships the ERP engine as Python **bytecode only** (no source, comments or
  docstrings — `packaging/compile_web_py.py`, built with Python 3.12 to match Pyodide) and the app's scripts and styles
  **minified** with a copyright banner (`build_web.py`, esbuild). Anything a browser runs can still be inspected with
  enough effort; this removes the readable source and makes copying costly, and the licence makes it unlawful.
* Public downloads are only the connected desktop app (a small launcher that opens the website). The offline editions
  live on the private release.
* Security headers on the site (no framing, HTTPS only, no sniffing, strict referrers), sign-in lockout after 5 wrong
  passwords for 15 minutes, other devices signed out on a password change, no version numbers in server replies.
* `LICENSE` (proprietary, all rights reserved) — also shown and accepted in the Windows installers (`packaging/EULA.txt`).
