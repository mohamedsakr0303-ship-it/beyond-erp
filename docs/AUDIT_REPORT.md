# Audit report — October 2026

Scope: navigation, responsive layout, import and export, books integrity, security and permissions.
Evidence: automated tests in `tests/`, plus browser sweeps run against a demo company at four screen sizes in English and Arabic.
No production data was used, read or changed.

## 1. System overview

| Part | What it is |
|---|---|
| Server | Python standard library HTTP server (`erp/server.py`, 305 API routes), SQLite storage (`erp/db.py`), 86 modules |
| Screen | One-page app in plain JavaScript (`erp/static/app.js`), menu and labels in `i18n.js`, styles in `app.css` |
| Excel | `openpyxl` for workbooks; reports, documents and extracts also write their own .xlsx |
| Cloud edition | Supabase (13 migrations in `supabase/migrations`) |
| Delivery | GitHub Actions: tests, database migrations, web build, desktop build, pages |
| Dependencies | `openpyxl` (Excel), `anthropic` (optional AI assistant); nothing else outside the standard library |

Import and export paths:

- Whole books: Excel workbook, workbook with attached files (.zip), JSON, database backup file, CSV per sheet.
- Per screen: 58 importable sheets, each with an Excel and a CSV template in English and Arabic, and a demo-filled version.
- Bank statements: CSV, Excel, MT940, CAMT.053, OFX/QFX, QIF.
- Reports: print, PDF, Excel, Word.

## 2. Menu repair

Symptom: on a phone, the side menu showed icons in a row, the account card squashed its name and role together, and the account menu items ran inline.

Cause: the slim desktop menu (icons only) was switched on by the Menu button on a phone turned sideways (wider than 900 px). It was saved and stayed on after the phone was turned upright. Its styles applied at every width, and one phone rule (`display:revert` on everything in the menu) then undid the hidden and flex states.

Fix (`6d25abe`):

- Slim-menu styles apply on wide screens only. The phone drawer always shows the full menu.
- On a touch screen, tapping a section in the slim menu widens the menu and opens that section, because there is no hover there.
- In the phone icon strip, the account card opens the menu first, so its options are not clipped to the 56 px strip.

Verified by screenshots of the same steps before and after: landscape phone → Menu → portrait → open drawer and account menu. Also checked on a desktop (slim menu and fly-out panels unchanged), a tablet, and in Arabic.

## 3. UX changes

- Fields on touch screens use 16 px text in every orientation. iPhone and iPad zoomed the page on every field tap in landscape, and in the team chat box.
- The account menu shows the theme in the reader's language ("Dark mode" / "الوضع الداكن"). It used to show the raw value "dark".
- Import messages say what is wrong with a file and how to fix it:
  - a damaged workbook
  - an old .xls
  - a file that is not a spreadsheet

## 4. Responsive testing

Every menu entry (88 for a super administrator with the scaffolding demo) was opened at:

| Size | Viewport | Result |
|---|---|---|
| Phone portrait | 412 × 915, touch | No script errors, no server errors, no sideways page scroll |
| Phone landscape | 915 × 412, touch | Same |
| Tablet | 768 × 1024, touch | Same |
| Desktop | 1280 × 800 | Same |
| Phone portrait, Arabic | 412 × 915, right to left | Same |
| Desktop, Arabic | 1280 × 800, right to left | Same |

The main "New" form of each screen (43 forms) was opened at 390 × 844 and 844 × 390. Results:

- The save button is present in every form.
- No form scrolls sideways.
- Line-item tables scroll inside their own frame.

## 5. Import and export matrix

| Case | Sheet import | Whole-books import | Result |
|---|---|---|---|
| Template of each of the 58 sheets, English and Arabic, Excel and CSV | ✓ | — | Empty template refused: "No rows to import" |
| Demo-filled layout, 3 business types × every sheet, both languages | ✓ | ✓ | Every row read back |
| CSV template with the example row left in | ✓ | — | **Fixed**: the example row was imported as real data |
| Damaged or cut-off .xlsx | ✓ | ✓ | **Fixed**: was a raw zip error |
| Old .xls | ✓ | — | Refused with how to convert |
| Random binary | ✓ | — | Refused |
| CSV renamed .xlsx | ✓ | — | Read as CSV |
| Wrong headings | ✓ | — | "Match a column of your file to: …" |
| UTF-8, UTF-8 with BOM, Arabic Windows (cp1256), UTF-16 ("Unicode Text") | ✓ | ✓ | **Fixed**: UTF-16 unreadable; whole-books CSV garbled cp1256 |
| Semicolon and tab separated | ✓ | — | Read |
| The same contact twice in a file | ✓ | — | One record |
| Text starting with = + - @ | ✓ | ✓ | **Fixed**: exported as a live formula; now text, round-trips unchanged |
| Journal voucher with another party on a later row | — | ✓ | **Fixed**: the party was dropped |
| Excel, JSON, CSV, .zip and backup round trips | existing tests | existing tests | Same figures after re-import |

Tests: `tests/test_interchange_matrix.py` (new), plus the existing round-trip tests in `tests/test_erp.py`.

## 6. Financial audit

These checks work from the journal lines alone and compare with the statements. They don't call the report code to check itself. They ran on all 10 demo companies; 4 are kept in `tests/test_books_integrity.py`.

| Check | Result |
|---|---|
| Every posted entry balances | Pass |
| Amounts stored in whole minor units (no floating point) | Pass |
| No negative or two-sided lines; no unknown accounts; no posted entry without lines | Pass |
| Ledger nets to zero; trial balance balances | Pass |
| Balance sheet: assets − liabilities equals the ledger; equity earnings equal profit from the ledger | Pass |
| Income statement net profit equals revenue − costs from the ledger | Pass |
| No account with a balance falls outside a balance-sheet section | Pass |
| Aging: every contact's balance adds up to the ledger lines that name a contact; buckets add up | Pass |

Note: the aging nets each contact across all their accounts and lists them by sign. Its "receivable" total is therefore not the receivable account balance. This is by design and was not changed. Accruals and loans with no contact stay out of the aging.

Found later by the end-to-end journeys (D11): unbilled revenue (work done, not invoiced yet) was counted on the customer's statement and aging. An invoice that bills out earlier unbilled work moves the amount from unbilled revenue to receivables, both under the customer's name, so the statement netted it to zero and showed only the payments, as money owed to the customer. Unbilled revenue belongs to the business unit until it is invoiced, so it is now left out of contact statements and aging. How invoices post was not changed.

No accounting treatment was changed.

## 7. Defect register

| # | Severity | Area | Defect | Status |
|---|---|---|---|---|
| D1 | High | Navigation | Phone menu broken after turning the phone (slim menu leaked into the drawer) | Fixed `6d25abe` |
| D2 | Medium | Navigation | Icon-strip account menu clipped | Fixed `6d25abe` |
| D3 | High | Security | Formula injection: text such as `=HYPERLINK(…)` written to Excel and CSV exports as a live formula | Fixed `736c96e` |
| D4 | High | Data | CSV template's example row imported as a real record (e.g. a 100,000 receipt) when rows were added under it | Fixed `736c96e` |
| D5 | Medium | Data | Whole-books CSV import garbled Arabic Windows text; UTF-16 CSV not read | Fixed `736c96e` |
| D6 | Medium | Import | Damaged workbook gave a raw zip error (sheet, whole-books and bank imports) | Fixed `736c96e` |
| D7 | Medium | Data | Journal Vouchers import dropped a party typed on a later row of a voucher | Fixed `17872f0` |
| D8 | Low | Mobile | Field zoom on iPhone/iPad in landscape and in the chat box | Fixed `17872f0` |
| D9 | Low | Language | Account menu showed the theme value "dark" in English in Arabic | Fixed (this commit) |
| D10 | Low | Privacy | Real client and company names in one document and one test | Fixed `fc0d74b`; still in earlier commits (see 11) |
| D11 | High | Finance | Customer statement and aging hid invoices billed out of unbilled work, and showed the customer's payments as money we owe them | Fixed, verified by a journey and a unit test |
| D12 | Medium | Sales | An invoice saved with the suggested number left as is ("INV-2026-10-"); a second one that month was refused as a duplicate | Fixed: the screen's prefix is completed to the next number (INV-2026-10-001); imports keep numbers as given |
| D13 | Medium | Dashboard | "What we owe suppliers" listed bank loans, partner loans and customer advances (10.7 million against 378 thousand of trade payables); the "customers owing money" automation also watched staff advances and loans | Fixed: trade payables and customer receivables only |
| D14 | Low | Navigation | Moving between two screens quickly left an unhandled error from the page transition | Fixed |
| D15 | High | Themes | With a dark-sidebar theme (Rose gold, Forest, Ocean breeze, Sunset glow, or any own theme with a dark sidebar) the account menu and the slim menu's section panels showed white text on a white panel | Fixed: menus opened from a coloured sidebar take its colours. Every theme checked in light and dark, desktop and phone (lowest contrast 5.6:1); covered by a journey |

Checked and not defects:

- The Partners "do not distribute" banner is a deliberate warning.
- The Forecast Save button stays disabled until something changes.
- The aging scope is described in section 6.

## 8. Automated testing

- Full suite: 275 tests, all passed, 1 skipped (about 10 minutes). CI runs it on every push.
- New: `test_interchange_matrix.py` (12 tests) and `test_books_integrity.py` (7 tests).
- End-to-end journeys (`tests/e2e/run.js`, a new `e2e` CI job). Each one goes through the screens in a real browser on fresh demo books and checks the result in the database itself:

| Journey | What is checked |
|---|---|
| Purchase request → approval → two supplier quotes → order to the lowest → part and final delivery → payment | Request status; order goes to the lowest price; supplier owed 5,700 then 9,500 then 0; Save pressed twice makes one payment; supplier statement |
| Invoice → part receipt → balance owed | Invoice number completed; a second invoice gets the next number; customer owes 10,000 then 6,000; statement shows both and "they owe us" |
| Goods receipt → transfer → issue to a project | Quantities 70 and 20 per store; average cost 50.00; supplier paid in full |
| Project budget → actual cost → budget vs actual | Annual total spread over the months; budget saved; actual moves by the cost; every report column for the account |
| Half-filled form left for another screen | Draft listed, continued with its values, saved once, then removed from the list |
| Sales-only user | No purchasing, payments, users or accounts in the menu; purchasing address refused on screen and by the server; refresh keeps the screen |
| Phone turned sideways mid-form | Values kept; Save reachable; saved |
| Dark-sidebar themes | Account menu and slim menu panels readable (contrast at least 4.5:1) on desktop and phone |
| After all journeys | Every posted entry balances |

- Browser sweeps (menu, every page, every form, Arabic) were run with Playwright against a local demo server and are not in CI yet.

## 9. Security and dependencies

- Every write route checks the user's permission, either at the route or inside the function it calls:
  - the AI policy and key need a super administrator
  - the cloud settings need an administrator
  - bulk actions check each record as if done by hand
- No keys, tokens or private keys in the repository; Supabase keys appear only as placeholders.
- Dependencies: `openpyxl`, `anthropic`. CI only: Playwright 1.63.0 (Microsoft, Apache-2.0), pinned, for the end-to-end job; nothing is added to the app.
- Formula injection closed (D3).

## 10. Data impact

- No schema change, migration, deletion or rewrite of stored data.
- CSV exports put an apostrophe before text that starts with = + - @. Excel shows the text as typed; both importers remove the apostrophe.
- A journal voucher file with a different party on a later row now gives that line the party. Totals don't change; contact statements and aging do. Files exported by the app are unaffected.
- A CSV row identical to the template's example is skipped.
- Contact statements and aging no longer include unbilled revenue. Balances shown per customer change where invoices billed out unbilled work; the ledger and every financial statement are unchanged.
- Invoices already saved with a number ending in "-" keep it; only new ones from the screen are completed.

## 11. Outstanding work

- Earlier commits still contain the real names removed in D10. Removing them means rewriting the public history, which needs the owner's decision.
- Add the page and form sweeps (both languages) to CI, next to the journeys.
- The `e2e` CI job does not yet block a deploy; make it required once it has run reliably for a while.
- More journeys: returns and credit notes, cancellations and reversals, partial payments with VAT and withholding, rentals, payroll.
- An accessibility pass: contrast, keyboard order and screen-reader labels.
- The full test suite takes about 10 minutes.

## 12. Readiness assessment

Ready for continued use:

- The phone menu, page layout and forms work at every tested size, in both languages.
- Imports refuse bad files clearly and no longer invent or drop data.
- Exports are safe to open.
- Independent checks find the books consistent.

This rests on the tests and sweeps listed above. It does not mean the system is free of defects. The items in section 11 remain.
