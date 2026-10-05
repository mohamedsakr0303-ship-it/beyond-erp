# Payroll, crew pay and business-type templates

## How the design was chosen

**Payroll follows Odoo's model.** Odoo separates the employee contract from a *salary structure* (an ordered list of salary rules) and groups rules into categories: Basic, Allowance, Gross, Deduction, Net and Company contribution. Rules can be fixed amounts, percentages or formulas, and variable items are entered on each payslip. This module keeps that shape:

- **Employee (the contract):** monthly, daily, hourly or piece-rate pay. Fixed allowances. Insured and taxed flags. A cost type (direct labour, site overhead or admin) and a business unit.
- **Pay items (components):** earnings or deductions, either fixed on the employee or entered on each monthly run. Each has a taxable and an insurable flag. You can rename them, switch them off or add new ones.
- **Statutory rules as settings, not code.** Every rate and limit is editable.
- **A monthly run (payslip batch):** draft, then recalculate, then post. Reopening a posted run voids its entries, which stay in the audit trail.

**Egyptian defaults (check with your accountant — rules change):**

- **Social insurance (Law 148/2019):** the employee pays 11% and the employer 18.75% of the insurable wage. From 1 January 2026 the insurable wage is between EGP 2,700 and 16,700 a month.
- **Salary tax (Law 7/2024):**
  - personal exemption of EGP 20,000 a year;
  - bands of 0% up to 40,000, then 10%, 15%, 20%, 22.5%, 25% and 27.5% above 1.2 million;
  - the employee's insurance share is deducted before tax.
- **High-income rule.** Above 600,000 a year, tax starts from a higher band from the first pound. Sources disagree on whether, and how, this still applies after 2024. It is therefore a switchable table in the settings.
- **Martyrs Fund:** 0.05% of gross pay.
- **Overtime:** day 1.35, night 1.70 and rest day or public holiday 2.0, all times the hourly rate. The hourly rate is monthly basic ÷ 30 ÷ 8.

**Rounding.** Amounts are rounded to the piaster with halves rounded up, the same as Excel's `ROUND`. The app and the workbook therefore agree exactly.

**Posting: one entry per employee, in that employee's business unit.**

| Debit | Credit |
|---|---|
| Salary cost (gross − penalties) | Social insurance payable (both shares) |
| Employer insurance | Salary tax payable |
| | Martyrs Fund payable |
| | Employee advances (amount recovered) |
| | Accrued payroll (the net pay) |

The net is then paid with a payment voucher, or with "paid now" on the run line.

## Crew pay (measured work)

Crews and subcontractors are often paid per measured unit, separately from what the client is charged. For example, scaffold frames: number of frames × frame width × frame height = m² installed, multiplied by the rate per m².

The calculator multiplies count × width × height × depth, where a blank dimension counts as 1. It **never posts anything and never feeds another sheet**. When you pay, record the amount you actually paid: use "Record payment" in the app, or an Operating Expense, Payroll or Journal Voucher row in Excel.

## Scaffolding rental practice (Avontus Quantify and similar systems)

These practices are already in the app:

- Stock is tracked by where it is: the yard or each contract's site. It moves through signed delivery and return handovers, and the return count records good, damaged and lost pieces.
- Lost and damaged pieces are charged from a price list. In Rawasi's handover form that price is the purchase price + 15%.
- Rent is billed per period with a minimum.
- Installation, dismantling and transport are separate services, each with its own client price and its own cost.
- Pieces are bought as purchase lots, so cost and depreciation are tracked.

New in this release:

- **Rent as a percentage of cost** and **months to pay a piece back**, on the fleet screen and report. In Rawasi's Building Calculator, a target payback (months) can set the rent.
- **Utilisation** (pieces out ÷ pieces owned), shown per piece and for the whole fleet.

## Business-type templates

Settings → Business type & templates offers seven types:

- general business;
- interior design and finishing;
- contracting and construction;
- real-estate development;
- trading and sales;
- investment and holding;
- scaffolding and equipment rental.

Applying a type never deletes anything. It:

- adds the accounts that kind of business needs (for example development WIP, investment income, or crew and repair costs for scaffolding);
- hides the sections it does not use (hidden, not deleted);
- sets payroll departments, job titles and pay items;
- sets the crew calculator's work items.

Each type has a ready Excel template at `/api/templates/workbook?type=<type>`.

## Sources

- Odoo payroll: [salary structures and rules](https://www.braincuber.com/tutorial/salary-structure-rules-odoo-18) · [setup guide](https://ecosire.com/blog/odoo-payroll-setup-guide)
- Scaffolding rental management: [Avontus Quantify](https://www.avontus.com/quantify/) · [must-have features](https://www.avontus.com/blog/must-haves-scaffold-rental-management-software/)
- Egypt:
  - [2026 insurable wage limits (Mercans)](https://mercans.com/resources/statutory-alerts/egypt-minimum-and-maximum-insurable-wage-limits-increase-for-social-insurance-from-2026/)
  - [personal income tax 2026 (Andersen)](https://eg.andersen.com/personal-income-tax/)
  - [PwC tax summaries](https://taxsummaries.pwc.com/egypt/individual/taxes-on-personal-income)
  - [salary tax and social insurance 2026 (AnooreHR)](https://anoorehr.com/blog/egypt-salary-tax-social-insurance-2026)
