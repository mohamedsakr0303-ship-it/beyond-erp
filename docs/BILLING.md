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
   After that, your user menu shows **Owner console ↗**, which opens the console page (`owner.html`). Everything else is done from there; you can add team members with limited access under **Team**.
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

The console is **a page of its own**: `owner.html` on the same site (for example `https://your-site/owner.html`). It uses the same sign-in as the ERP. People on the team see **Owner console ↗** in their ERP user menu, which opens it in a new tab. Nobody else sees the button, and the page refuses anyone who is not on the team. Every action is checked again in the database.

| Area | What it is for |
|---|---|
| Overview | Expected against received (12 months … 7 days, or your own dates, with an export), totals, the companies falling due next, the latest payments, a quick look at any company, and what is waiting for you |
| Notifications | Worked out from the data and checked every minute: new tickets and replies, visits (requested, accepted, payment to check, in the next 2 days), payments to verify, orders left unpaid for 3 days, trials ending without a subscription, trials that ended unpaid, payment due, customers who stopped paying, renewals coming up, requests not to renew, new companies, companies not subscribed, more people than the plan allows. The bell shows them; the Notifications page filters them. Each person marks them read, dismisses them and chooses which kinds they want. While the console is open they also pop up on the screen with a sound. Tickets, payments and visits also come as phone and computer notifications when the console is closed |
| Customers | Every company, with search, filters (status, plan, archived or not, due dates, last payment dates, joined dates, amounts paid) and sorting on every column, plus a CSV export |
| A company's page | **Subscription**: record a payment (any months, any amount including 0, with add-ons), restart a trial for any number of days, extend the trial or paid period, stop the trial, make free or charge again, a standing discount, dates and plan. **Access**: what it can use (all sections or chosen ones, the AI assistant on/off, online support on/off, number of users), and suspend. **Company**: rename, change owner, archive or bring back, download a backup, and delete for good. **People**: make admin or user, activate or deactivate, approve a request to join, remove, get a password reset link, turn off two-step sign-in, block the person |
| People | Everyone using the workspace, with the companies they own and belong to, last activity, a private note, a company limit per person, and blocking |
| Cancellations & churn | Requests not to renew (with the reason), ended without paying, payment due, never subscribed, suspended |
| Payments to verify | Orders with the screenshots, filters and search. Approve and activate, change the amount first, or decline with a reason |
| All payments | Every payment ever, kept even when a company is deleted, with filters, totals and an export |
| Tickets, Support visits | The support inbox, and visits customers ask for: set the price and propose a time, schedule (date, who goes), confirm the payment or waive it, mark done, or decline with a reason |
| Plans | Design what customers see: name, a line under it, price, users, features (English and Arabic), a badge, a colour, stand out, hidden (offered to nobody, but you can still give it), the sections it includes, AI assistant and online support. A live preview shows the card as customers will see it. You can also duplicate plans, change their order, and delete them |
| Sections, AI & support | Which ERP sections are core (in every plan); which can be bought on their own and at what monthly price (0 = free to add); the AI assistant (everyone free, by plan with an optional paid add-on, or off); online support (everyone free, or by plan with an optional paid add-on); extra user price; each further company % |
| Rules & switches | Billing on/off; maintenance (changes paused for every customer, with your message); accept new customers or not; trial length and plan; whether the trial shows every section; grace days; yearly price; currency; how many companies one person may own; when to warn you; support contacts; visit settings (on/off, usual price, before/after/no charge, terms); the texts of the customers' Subscription page |
| Payment methods, Announcements | As before |
| Team | Who helps you, and which areas each one may use. Only full-access members manage the team, download backups and delete companies |
| Look of this console | Name, line under it, main colour, logo, light/dark, side menu style, spacing, corners. It is shared by the team, and each device can also switch light/dark |
| Activity, Deleted companies | Everything the team did, filterable; and companies deleted, with a way to bring one back from its backup file |

### Looking after customers' data

* **Archive** keeps everything and makes the company read only. It leaves the main lists (use the "Show" filter to see archived ones). It can be undone any time.
* **Download backup** (full-access owners) saves one file with the company's books, people, billing and payments. Keep it safe: it contains the customer's data.
* **Delete for good** needs all of: the company archived first, a backup taken in the last 24 hours, and its name typed. Its payments stay in *All payments* under its name, so revenue figures do not change.
* **Bring back**: Deleted companies → choose the backup file.

### What customers see

* Plans as you designed them, the sections, AI assistant and online support each one includes, and add-ons they can tick per company (sections sold on their own, the AI assistant, online support). Your texts are shown above and below.
* Sections their plan does not include disappear from the menu. Opening one directly says it is not in the plan, with an "Add it to the subscription" button for administrators; their data stays.
* Banners for maintenance, suspension and archiving.
* **Help and support**: tickets (when online support is included; questions about the subscription are always allowed) and **Ask for a visit**. For a visit, they accept or decline your price, pay with a transfer screenshot, and follow its status.

### What is enforced where

* Billing, read only (unpaid, ended, suspended, archived, maintenance), user limits, company limits per person, new customers, blocking a person, online support, visits and the AI assistant are refused by the database or the server functions. Changing the page does not get around them.
* Sections are hidden and refused by the app itself. Someone with technical skills could still reach a hidden section's screens in their own browser, but nothing outside their plan is offered to them.

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
