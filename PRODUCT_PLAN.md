# Beyond ERP — Product plan (making it sellable)

The goal: one codebase sold three ways, and a product that works for design, fit-out and contracting
firms far beyond BEYOND DESIGN — first in Egypt and the Arabic-speaking market, then wider.

| Edition | Customer | How they pay | How it runs |
|---|---|---|---|
| **Desktop** | 1–5 users in one office | one-time licence + optional yearly updates | installer on one PC; others connect over the office network |
| **Self-hosted** | firms with IT / their own server | yearly licence per company | Docker image on their server or VPS |
| **Cloud (hosted)** | everyone else | monthly / yearly subscription per company | we run it: `company.beyond-erp.com` |

A fourth, zero-cost channel now exists too: the **browser edition** (static site, data in the browser) — ideal for
free trials and demos. The current code already has the hard core that all three share: double-entry engine, statements
(EBITDA/EBIT, cash flow), business units, multi-warehouse stock, permissions, bilingual UI, Excel/JSON
import-export, backups, tests, and a Docker image. What follows is what's still missing.

---

## 1. Must-have before the first sale (all editions)

| # | Item | Why | Effort |
|---|---|---|---|
| 1 | **ETA e-invoice: certify with the live system** — built (document builder, JSON/Excel export, API submission via signing service); still needs pre-production testing with real credentials, e-receipt (B2C POS) support, and credit/debit notes | B2B e-invoicing is mandatory in Egypt | M |
| 2 | ~~VAT & withholding tax on documents~~ — **done** (tax codes, tax lines, tax report). Next: official VAT return form layout and Form 41 withholding export | every VAT-registered customer needs it | S |
| 3 | ~~Customer & supplier master~~ — **done** (tax IDs, addresses, defaults). Next: statements of account + ageing (30/60/90) | collections and supplier follow-up | M |
| 4 | ~~Printable document templates with logo~~ — **done** for offers, BOQ and invoices (Excel/Word/PDF, logo + watermark per file type). Next: receipt & payment voucher layouts | customers judge the product by its printouts | S |
| 5 | **Period close & locking** — lock date done; next: year-end closing entry to retained earnings | auditors require it; protects filed numbers | S |
| 6 | **Onboarding wizard**: company, fiscal year, currency, chart-of-accounts preset (design firm / contractor / trading / services), opening balances, first users | first-hour experience decides churn | M |
| 7 | **Demo company** + sample data | sales demos, trials | S |
| 8 | **Help centre** (EN/AR): short videos + articles per screen; in-app "?" links | reduces support load | M |
| 9 | **Legal**: EULA (desktop/self-hosted), Terms of Service + Privacy Policy + DPA (cloud), compliance with Egypt's Personal Data Protection Law 151/2020 | required to sell | S (lawyer) |
| 10 | **Brand check** — "Beyond" is a common word; check trademark availability in Egypt/GCC before spending on marketing | avoid a forced rename | S |

## 2. Desktop edition (one-time purchase)

* **Installers**: build with PyInstaller (or Briefcase) — Windows `.exe` via Inno Setup, macOS `.dmg`,
  Linux `.AppImage`/`.deb`. Bundles Python, so customers install nothing else.
* **Code signing**: Windows Authenticode certificate (~$200–400/yr); Apple Developer ID + notarization
  ($99/yr). Unsigned installers trigger SmartScreen / Gatekeeper warnings that kill conversions.
* **Licensing**: an offline-verifiable signed licence file (Ed25519: company name, seats, edition,
  update-until date). The app runs fully without internet; after the update window ends it keeps working,
  only new versions stop installing. Add a small vendored Ed25519 verifier (stdlib has none).
* **Updates**: "check for updates" that downloads a signed release; database migrations are already
  additive and idempotent (`Database._migrate`).
* **Tray app / Windows service** so the server keeps running for the office network after the window closes.
* Pricing idea (EGP, validate with 10 interviews): Starter 1 user ≈ 6–9k, Office 5 users ≈ 15–20k,
  + 20 % / year for updates & support.

## 3. Self-hosted edition

* Ready now: `Dockerfile`, `docker-compose.yml` with Caddy for automatic HTTPS, `Secure` cookie flag.
* Add: health/metrics endpoint, structured logs, `BEYOND_ERP_*` env config documented, a restore command,
  and **Litestream** (continuous SQLite replication to S3/B2) as an optional sidecar for off-site backups.
* Licence key checked offline, same as desktop.

## 4. Cloud edition (hosted service)

* **Tenancy**: one SQLite file per company (the app is already single-company per file) — simple,
  isolated, trivially backed up and exported. A thin router maps `company.beyond-erp.com` → its file.
  Move to PostgreSQL only if a single tenant outgrows SQLite (unlikely below ~50 concurrent users).
* **Accounts & security**: sign-up, email verification, password reset by email, **2FA (TOTP)**,
  login rate limiting, session management, optional SSO (Google/Microsoft) later.
* **Billing**: Paymob or Fawry for Egyptian cards/wallets/Fawry cash; Paddle or Lemon Squeezy as
  merchant-of-record for international customers (handles VAT/sales tax). Trials, plan limits (users,
  business units, storage), dunning.
* **Operations**: automated encrypted off-site backups (Litestream → object storage) with point-in-time
  restore, uptime monitoring, error tracking, status page, audit of admin access, data export on demand
  (JSON/Excel already exist) and deletion on cancellation.
* **Hosting cost**: a single 4 GB VPS serves hundreds of small tenants; start with one region close to
  customers (e.g. EU-central or a Middle-East region).
* Pricing idea: per company per month ≈ 900–2,500 EGP by users/features; annual discount 2 months.

## 5. Features a wider audience will expect (roadmap)

**Next (v1.1 – v1.3)**
1. Customer/supplier master, statements, ageing (see §1).
2. Tax engine + ETA e-invoicing (see §1).
3. Document templates & email sending (invoice/receipt as PDF attachment).
4. Fixed-asset register with automatic monthly depreciation/amortization entries.
5. Bank accounts & **bank reconciliation** (import CSV/Excel statements, match).
6. Attachments on vouchers (photos of receipts, contracts) stored beside the database.
7. Budgets vs actual per business unit; job-costing progress (% complete, earned value, WIP).
8. Purchase requests → purchase orders → goods receipt → bill, with approval rules.
9. Recurring entries (rent, subscriptions, payroll) and reminders.

**Later (v2)**
* Multi-currency with revaluation; multi-company consolidation.
* Mobile-friendly PWA for site engineers (receipts, material requests, photos, offline queue).
* Public REST API with tokens + webhooks; Zapier/Make connector.
* More chart-of-accounts presets and languages (French for North Africa, English/Arabic GCC VAT variants).
* Client portal: clients see BOQs, progress billings, and pay online.

## 6. Quality bar for "sellable"

* Keep the **zero-dependency** core and the **bloat rules** (README → "Keeping it lean"): every new list
  paginated, every file format driven by the one interchange spec, integer money, additive migrations.
* Expand tests with every feature (the suite runs in seconds with no setup); add a Playwright UI smoke test
  and run both in CI on Windows, macOS and Linux runners.
* Security review before launch: dependency-free helps, but get an external pentest for the cloud edition.
* Accessibility and RTL review with real Arabic-speaking accountants; have a CPA review the statement
  mappings for EAS (Egyptian Accounting Standards) presentation.
* Performance budget: page < 300 ms with 100k vouchers; exports < 5 s.

## 7. Go-to-market (lightweight)

1. Run BEYOND DESIGN on it for 2–3 months — the best demo is a real firm's numbers.
2. 10 customer interviews with fit-out / contracting / architecture firms; price-test the editions.
3. Partner with 2–3 accounting offices who set up clients (reseller margin 20–30 %).
4. Arabic-first content: short videos on "EBITDA per project", "progress billing from a BOQ",
   "what your cash floor means".
5. Launch Desktop first (no infra), then Cloud once ETA integration and billing are live.
