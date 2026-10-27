# Paid subscriptions (hosted service)

Nothing changes for anyone until **you switch billing on**. The app stays free until then.

## How it works

| Stage | What the customer sees | What they can do |
|---|---|---|
| **Trial** (15 days, on the Business plan, a person's **first company only**) | "Free trial — N days left · Subscribe" bar | Everything |
| **Not subscribed** (any further company a person creates) | Red bar: "not subscribed yet" | Sign in, view, print, export. **Saving is refused by the server** |
| **Active** (paid) | Nothing, or "ends in N days" during the last week | Everything |
| **Payment due** (3 grace days after a paid period ends) | Amber bar: "pay within N days" | Everything |
| **Ended** | Red bar: "view, print and export only" | Sign in, view, print, export. **Saving is refused by the server** |

* **Every plan covers one company** with its own users. A customer subscribes several companies in one order ("adding shirts to a bag"): the first company costs the plan price, each further one adds a set share of it (Plans and settings → *Each further company*, default 100 %). Extra users are bought per company.
* The trial is counted from when the company was created, or from when you switched billing on, whichever is later. Companies that already existed when billing was switched on keep their trial too.
* Each payment adds 1 month or 1 year after the current period. Nothing charges a card by itself. If the customer does not pay again, the company ends automatically when the period (plus grace days) is over.
* Data is never deleted. Paying again unlocks it immediately.
* Plans limit the number of active people. Adding, approving or re-activating someone beyond the plan is refused, with a message to add extra users.
* The block is enforced in the database (`append_op` refuses changes), not only in the browser. Prices are worked out in the database too, never in the browser.

## How a customer pays

1. **Subscription** (user menu): tick the companies, set extra users per company, choose the plan and monthly or yearly. The total is quoted by the server.
2. Choose a **payment method** you set up (InstaPay, wallet, bank, cash, card). An order with a reference (BO-…) opens.
3. Transfer methods show your number or account with copy buttons. The customer sends the money, then **uploads a screenshot** of the transfer (and optionally the transfer reference and amount sent) and presses *Send for verification*.
4. You get a notification. In **Owner console → Payments to verify** you see the screenshot, the amount sent (flagged if it differs from the order) and the companies. **Approve and activate** records one payment per company and activates them all at once; **Not accepted…** asks for a reason the customer sees, and they can send a new proof.
5. Card payments (with Paymob set up) approve the order automatically when Paymob confirms the exact amount.

Screenshots are kept in a private storage bucket (`billing-proofs`): only the customer who uploaded them and your team can open them.

## Go-live checklist

1. **Make yourself the platform owner.** Run this once in Supabase → SQL editor (with your own sign-in email):
   ```sql
   insert into public.platform_admins(email, name) values ('your-sign-in-email@example.com', 'Your name');
   ```
   After that, your user menu shows **Owner console**. Everything else is done from there; you can add team members with limited access under **Team**.
2. **Check the plans and prices.** Owner console → **Plans and settings → Plans**. Defaults are below; change them any time. New prices apply to the next payment.
3. **Add payment methods.** Owner console → **Payment methods → + Payment method**: for example InstaPay with your InstaPay address, a mobile wallet number, a bank account. Tick *Customer uploads a picture of the transfer* for transfers.
4. **Support contacts.** Owner console → Plans and settings → *Trial, prices and support contacts*: WhatsApp, phone, email and working hours shown on customers' Help and support page.
5. **Card / wallet payments (Paymob, optional).** Create a Paymob account (accept.paymob.com). Then, in Supabase → Edge Functions → Secrets, add:

   | Secret | Where it comes from |
   |---|---|
   | `PAYMOB_SECRET_KEY` | Paymob dashboard → Settings → Account info → Secret key |
   | `PAYMOB_PUBLIC_KEY` | Paymob dashboard → Settings → Account info → Public key |
   | `PAYMOB_HMAC` | Paymob dashboard → Settings → Account info → HMAC |
   | `PAYMOB_INTEGRATION_IDS` | Comma-separated integration ids (card, mobile wallet…), from Developers → Payment integrations |

   Then set Paymob's transaction processed callback to `https://<project>.supabase.co/functions/v1/billing?hook=paymob`, and add a payment method of type **Card**.
6. **Deploy the database and functions.** Apply the migrations (`supabase db push`) and deploy the `billing` and `push` edge functions, so the console, orders, support and notifications work.
7. **Switch billing on.** Owner console → Plans and settings → **Switch billing on**. Each person's first company gets its trial from that moment. Switching off makes the app free again, and nothing is lost.

## Billing on, off, per company

* **Billing off** (Plans and settings): the app is free for everyone.
* **Billing on**: from that moment every company that already exists gets a fresh free trial (the trial length setting, 15 days by default); after that, a person's first company gets the trial and later ones need a subscription.
* **Per company**: *Make free* takes one company out of billing for good (your own companies, partners, pilots); *Charge again* puts it back. Or record a payment of 0 for as many months as you like, so it shows as paid.
* Every action is written to the console's Activity tab.

## The owner console

| Tab | What it is for |
|---|---|
| Overview | Expected (renewals falling due) against received, by month or day, with an export; monthly recurring revenue, paying / trial / not paying, payments to verify, open tickets; renewals and trials ending in the next 30 days; a quick look at any company |
| Customers | Every company with owner, status, plan, end date, people, last activity, total paid and open tickets. A company page shows its people, payments, orders and tickets, with these controls: **Record payment** (any number of months, any amount, 0 included), **Restart trial…** (a fresh trial of any length from now), **Extend…** (adds days to the trial, or to the paid period while paid), **Stop trial**, **Make free / Charge again**, **Discount…** (a standing % off every order for that company), **Set dates…** |
| Payments to verify | Orders waiting for your check, with the uploaded screenshots. **Change amount…** sets an agreed total before approving (a one-off discount, or 0); the companies in the order share it and the customer sees the new amount |
| Support | Every ticket from every customer: text, screenshots, screen recordings, technical details and the activity before the report (screens opened, requests and their results, errors — never what was typed or figures). Set status, priority and who handles it; reply with files, or add **internal notes** the customer never sees |
| Cancellations | Companies that asked not to renew (with their reason), whose payment is due, or whose period ended |
| Plans and settings | Billing on/off, trial, grace days, yearly price, extra user, each further company %, currency, support contacts, plans |
| Payment methods | The ways customers can pay, with the details they copy |
| Announcements | A bar at the top of the app (maintenance, new features, price changes) for everyone, companies on trial, paying, or not paying, between two dates |
| Team | People who help you, each with only the areas ticked (overview, customers, seeing payments, approving payments, support, announcements, settings). Only full-access members see this tab; at least one always remains |
| Activity | Who did what in the console: approvals, rejections, payments recorded, settings changed |

Notifications: your team is notified of new tickets, customer replies and payments to verify; the customer is notified of your replies and of an approval or rejection. Who is notified is decided on the server.

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

| Plan | Per month, per company | Users per company | For |
|---|---|---|---|
| Essentials | 1,900 EGP | 3 | Offices and traders: accounting, sales, purchasing, ETA, statements of account |
| **Business** (the anchor) | **4,500 EGP** | 8 | Contractors and fit-out firms: projects, BOQs, progress invoices, stock, POs, budgets, assets, loans, custody, bank reconciliation |
| Pro | 8,900 EGP | 20 | Groups, rental, showroom and real-estate businesses; priority support |
| Enterprise | from 15,000 EGP | 40+ | Dedicated onboarding, SLA, custom reports and integrations |

* Extra user: 350 EGP a month, for the company it is added to.
* Each further company in the same order: the share of the plan price set in the console (default 100 %; lower it to reward groups).
* Yearly: pay 10 months, get 12. Push annual contracts: they cut churn and fund growth.
* Eight Business users cost about 5,700 EGP a month on Odoo before any implementation. 4,500 EGP is still clearly cheaper, with the construction features built in.
* Launch offer: first 20 companies get 30 % off the first year in exchange for a case study or reference. This keeps the list price intact.

The new defaults are applied by `supabase/migrations/20261005000000_billing_prices.sql`. They only replace the old defaults when billing is still off and nobody has edited the prices. Prices you already set yourself are kept, and you can change them any time in Owner console → Plans and settings → Plans. Since the owner console, plans no longer carry a company count; the shipped Pro feature line "Up to 3 companies, …" is reduced to its support part.

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
