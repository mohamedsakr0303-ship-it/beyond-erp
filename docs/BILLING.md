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

## Pricing (recommendation, October 2026)

### What the market charges (per month)

| Product | Price | Notes |
|---|---|---|
| Daftra | $9.99 / $19.95 / $40 (≈ 520 / 1,045 / 2,090 EGP) | Extra user $6 |
| Zemam ERP | ≈ 1,500 EGP | 3 users, 1 branch |
| Wafeq | $26–55 (≈ 1,360–2,880 EGP) | |
| Qoyod (KSA) | SAR 120–330 (≈ 1,670–4,600 EGP) | Up to 5 users |
| Odoo (Egypt price list) | Standard ≈ $9 per user (≈ 475 EGP); Custom ≈ $13.6 per user (≈ 710 EGP) | Implementation is extra |
| QuickBooks Online | ≈ $30 | |
| Zoho Books | $15–60 | |
| KashBoss | 100–1,000 EGP | |

### Where BEYOND ERP fits

BEYOND ERP covers more than a basic invoicing package. It includes:
* project / business-unit costing, BOQs and progress invoices
* equipment rental with the scaffold calculator
* showroom, installments and service subscriptions
* ETA e-invoicing, budgets, purchase orders, loans, custody and bank reconciliation

It also costs far less than Odoo with an implementation partner.

### Default plans

| Plan | Price per month | Users | Companies | Who it is for |
|---|---|---|---|---|
| Starter | 690 EGP | 2 | 1 | Small offices: accounting, sales, purchasing, ETA |
| **Business** (anchor) | **1,490 EGP** | 5 | 1 | Contractors and fit-out firms: projects, BOQs, stock, POs, budgets, assets |
| Pro | 2,990 EGP | 15 | 3 | Groups, rental and showroom businesses, priority support |

* Extra user: 150 EGP per month.
* Yearly: pay 10 months, get 12.
* Larger clients get custom pricing.

At launch, consider a "founding customer" offer (for example 30 % off the first year for the first 20 companies). It brings in references and feedback.

## Offline (desktop) edition — one-time purchase (recommendation)

| Product | What buyers pay | Notes |
|---|---|---|
| DEXEF | 4,000–15,000 EGP | Perpetual licences |
| Typical Egyptian desktop accounting packages | ≈ 5,000–30,000 EGP | Usually with paid yearly updates |

A perpetual licence normally costs about 1.3–1.5 years of the equivalent cloud subscription. Because ETA e-invoicing rules keep changing, desktop buyers need updates, so sell them as a yearly maintenance plan rather than free forever.

| Licence | One-time | Users | Companies | Includes |
|---|---|---|---|---|
| Desktop Starter | 9,900 EGP | 2 | 1 | Core accounting, sales, purchasing, ETA |
| Desktop Business (office network) | 24,000 EGP | 5 | 1 | Everything in the Business plan |
| Desktop Pro | 45,000 EGP | 15 | 3 | All modules |

* **Updates and support:** every licence includes 12 months. After that, an optional yearly maintenance plan at 20 % of the licence (≈ 2,000 / 4,800 / 9,000 EGP) covers updates, ETA changes and support. Without it the software keeps working on the last version received.
* **Extra users:** 1,500 EGP each, one-time.
* **Services, sold separately (good margin):**

  | Service | Price |
  |---|---|
  | Setup and data migration from Excel / another system | 3,000–10,000 EGP |
  | On-site training | 2,500 EGP per day |

* The desktop app does not check a licence key yet: anyone with the installer can run it. Licence activation (a key tied to the company name and number of users) can be added before you sell desktop copies.
