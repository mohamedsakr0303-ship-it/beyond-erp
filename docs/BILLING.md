# Paid subscriptions (hosted service)

Nothing changes for anyone until **you switch billing on**. The app stays free until then.

## How it works

| Stage | What the customer sees | What they can do |
|---|---|---|
| **Trial** (15 days, on the Business plan) | "Free trial — N days left · Subscribe" bar | Everything |
| **Active** (paid) | Nothing, or "ends in N days" during the last week | Everything |
| **Payment due** (3 grace days after a paid period ends) | Amber bar: "pay within N days" | Everything |
| **Ended** | Red bar: "view, print and export only" | Sign in, view, print, export. **Saving is refused by the server** |

* The trial is counted from when the company was created, or from when you switched billing on, whichever is later. Companies that already exist are not cut off on day one.
* Each payment adds 1 month or 1 year after the current period. Nothing charges a card by itself. If the customer does not pay again, the company ends automatically when the period (plus grace days) is over.
* Data is never deleted. Paying again unlocks it immediately.
* Plans limit the number of active people. Adding, approving or re-activating someone beyond the plan is refused, with a message to add extra users.
* The block is enforced in the database (`append_op` refuses changes), not only in the browser.

## Go-live checklist

1. **Make yourself the platform owner.** Run this once in Supabase → SQL editor:
   ```sql
   insert into public.platform_admins(email) values ('your-sign-in-email@example.com');
   ```
   After that, your user menu shows **Platform billing (owner)**.
2. **Check the plans and prices.** Go to Platform billing → **Plans & prices**. Defaults are below; change them any time. New prices apply to the next payment.
3. **Add manual payment details.** Go to Platform billing → **Trial & payment settings**, and fill in your InstaPay address and bank account. Customers see them on their Subscription page.
4. **Card / wallet payments (Paymob).** Create a Paymob account (accept.paymob.com). Then, in Supabase → Edge Functions → Secrets, add:

   | Secret | Where it comes from |
   |---|---|
   | `PAYMOB_SECRET_KEY` | Paymob dashboard → Settings → Account info → Secret key |
   | `PAYMOB_PUBLIC_KEY` | Paymob dashboard → Settings → Account info → Public key |
   | `PAYMOB_HMAC` | Paymob dashboard → Settings → Account info → HMAC |
   | `PAYMOB_INTEGRATION_IDS` | Comma-separated integration ids (card, mobile wallet…), from Developers → Payment integrations |

   Then set Paymob's transaction processed callback to `https://<project>.supabase.co/functions/v1/billing?hook=paymob`. The checkout also sends this URL with each payment.

   Without these secrets, the Pay button tells customers to use InstaPay or a transfer. You then record those payments yourself.
5. **Switch billing on.** Go to Platform billing → **Go live: switch billing on**. Every company gets its 15-day trial from that moment. Switching off makes the app free again, and nothing is lost.

## Day-to-day

* **Customer paid by InstaPay / transfer / cash:** Platform billing → the company → **Record payment**. Choose the plan and period, and type the reference.
* **Extend a trial, or make a company free** (partner, staff, pilot): use **Trial / free…** on the company.
* The overview shows monthly recurring revenue and how many companies are in trial, paying, payment due, ended or free.

## Pricing (October 2026)

### Why the first prices were too low

The first list (690 / 1,490 / 2,990 EGP a month, 9,900–45,000 EGP desktop) was set against the wrong products.

* **It was anchored to invoicing apps.** Daftra, Qoyod and Zoho sell invoices plus a ledger to shops and freelancers. BEYOND ERP runs a contractor's projects, BOQs and progress billing, equipment rental, real-estate installments, ETA and the full statements. Its real alternatives are Odoo with a partner, a custom-built system, or a local construction ERP.
* **It priced the software, not the result.** A contractor running 3–5 projects loses far more than the subscription to one missed variation, one unbilled rental month or one ETA penalty. Prices should be a small share of the value delivered, not of the cost to host it.
* **It ignored the real competitor's total cost.** In Egypt, Odoo partners quote 125,000–350,000 EGP to implement a small company (more for 20+ users), plus about 710 EGP per user per month. BEYOND ERP needs almost no implementation: it ships with the Egyptian chart of accounts, ETA, the Excel workbook and a demo company. That saving is worth charging for.
* **It gave away services.** Setup, migration from Excel and training were small optional extras. In this market they are often half the revenue, and the main reason customers succeed and stay.
* **Low prices read as low quality.** Finance managers and owners buying an ERP for a contracting company distrust a 690 EGP system. A price close to Daftra's puts the product in the same mental box as Daftra.

### What the market charges

| Product | Price | Notes |
|---|---|---|
| Daftra | 99–799 EGP / month | Invoicing and accounting |
| Optify Flex | 414–1,498 EGP / month (launch offer) | SME ERP |
| Hunt ERP | from 1,000 EGP / month; enterprise on quote | Egyptian ERP |
| Odoo (Egypt price list) | ≈ 475–710 EGP per user / month | Plus implementation of 125,000–350,000 EGP for a small company, 250,000–400,000 EGP for 20–100 users |

### Cloud plans (new defaults)

| Plan | Per month | Users | Companies | For |
|---|---|---|---|---|
| Essentials | 1,900 EGP | 3 | 1 | Offices and traders: accounting, sales, purchasing, ETA, statements of account |
| **Business** (the anchor) | **4,500 EGP** | 8 | 1 | Contractors and fit-out firms: projects, BOQs, progress invoices, stock, POs, budgets, assets, loans, custody, bank reconciliation |
| Pro | 8,900 EGP | 20 | 3 | Groups, rental, showroom and real-estate businesses; priority support |
| Enterprise | from 15,000 EGP | 40+ | any | Dedicated onboarding, SLA, custom reports and integrations |

* Extra user: 350 EGP a month.
* Yearly: pay 10 months, get 12. Push annual contracts: they cut churn and fund growth.
* Eight Business users cost about 5,700 EGP a month on Odoo before any implementation. 4,500 EGP is still clearly cheaper, with the construction features built in.
* Launch offer: first 20 companies get 30 % off the first year in exchange for a case study or reference. This keeps the list price intact.

The new defaults are applied by `supabase/migrations/20261005000000_billing_prices.sql`. They only replace the old defaults when billing is still off and nobody has edited the prices. Prices you already set yourself are kept, and you can change them any time in Platform billing → Plans & prices.

### Offline (desktop) edition — one-time licence

| Licence | One-time | Users | Companies |
|---|---|---|---|
| Desktop Essentials | 35,000 EGP | 3 | 1 |
| Desktop Business (office network) | 85,000 EGP | 8 | 1 |
| Desktop Pro | 160,000 EGP | 20 | 3 |
| Desktop Enterprise | from 300,000 EGP | 40+ | any |

* **Pricing rule:** a licence costs about 1.5–2 years of the cloud plan, because the customer keeps it for good.
* **Maintenance:** 20 % of the licence every year, after the first 12 months, which are included. It covers updates, ETA changes and support. Licences carry `updates_until`, so a new version refuses to unlock without maintenance and the old one keeps working. That turns one-time sales into recurring income.
* **Extra user:** 6,000 EGP, one-time.
* **Issuing licences:** `python run.py licence-issue --key <private key> --company "…" --edition business --users 8 --updates-months 12 --install XXXX-XXXX-XXXX-XXXX`.

### Services (sold with every deal)

| Package | Price | Includes |
|---|---|---|
| Quick start | 15,000 EGP | Company setup, chart of accounts, opening balances from the Excel workbook, 1 day of training |
| Standard onboarding | 40,000 EGP | Everything in Quick start + migration of 1 year of history, projects/BOQs, users and permissions, 3 training days, 30 days of hand-holding |
| Full implementation | 75,000 EGP and up | Several companies or branches, rental and real-estate setup, custom reports and documents, on-site go-live |
| Training | 5,000 EGP per day | On site or online |
| Custom work | 1,200 EGP per hour | Reports, integrations, custom documents |
| Priority support / SLA | 20 % of the subscription | Response within 4 working hours, named contact |

### How to make it worth more

1. **Sell a vertical.** Market it as "the ERP for contractors, scaffolding / equipment rental and real-estate developers in Egypt", not as accounting software. Lead with the BOQ → progress invoice → project profit flow, the scaffold calculator and installments.
2. **Sell outcomes, with numbers.** Examples: "know each project's profit every day", "never miss a rental invoice", "ETA-ready from day one". Track before/after figures with the first customers and publish them as case studies.
3. **Make onboarding part of the product.** The Excel workbook, demo company and import kit make go-live fast. Package them as the paid onboarding service above, instead of giving them away.
4. **Price add-ons separately once customers depend on them.** For example: rental, showroom & installments, service subscriptions, extra companies, attachments storage, and later bank feeds, WhatsApp reminders or mobile apps for site engineers.
5. **Offer annual contracts and an SLA.** Larger firms want a phone number and a response time. Support should be a product, not a favour.
6. **Use partners.** Accountants and audit firms who set it up for their clients should earn 20 % of the first year's subscription. They are the cheapest sales channel in Egypt.
7. **Raise prices with the product.** Re-price every year for new customers. Existing customers keep their price for 12 months, which is also an argument to sign annually.
