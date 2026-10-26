// End-to-end journeys through the screens, on fresh demo books.
//
//   node tests/e2e/run.js            (needs Playwright with Chromium; run from the repository root)
//   KEEP=1 node tests/e2e/run.js     keeps the data folder afterwards, to look into a failure
//
// Starts its own server on an empty data folder, loads the demo company, walks each business journey in a real
// browser and checks the result in the database itself (tests/e2e/check.py), not only on screen.
// Screenshots of a failed step go to tests/e2e/out/.
const { chromium } = require("playwright");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const OUT = path.join(__dirname, "out");
const PORT = 8700 + Math.floor(Math.random() * 200);
const BASE = `http://127.0.0.1:${PORT}/`;
const DATA = fs.mkdtempSync(path.join(os.tmpdir(), "berp-e2e-"));
const USER = { username: "tester", password: "Test-pass-123" };
const results = [];
let server;

const sleep = ms => new Promise(r => setTimeout(r, ms));
const db = (name, ...args) => JSON.parse(execFileSync("python3", [path.join(__dirname, "check.py"), DATA, name, ...args.map(String)]).toString());
const near = (a, b) => Math.abs(a - b) < 0.005;
function expect(ok, what) { if (!ok) throw new Error(what); }

async function startServer() {
  server = spawn("python3", ["run.py", "serve", "--data", DATA, "--port", String(PORT), "--no-browser"], { cwd: ROOT, stdio: "ignore" });
  for (let i = 0; i < 60; i++) {
    try { await fetch(BASE); break; } catch { await sleep(500); }
  }
  const r = await fetch(BASE + "api/setup", { method: "POST", headers: { "content-type": "application/json", "X-Requested-With": "BeyondERP" },
    body: JSON.stringify({ ...USER, company_name: "Test Co", language: "en", demo: true }) });
  if (!r.ok) throw new Error("setup failed: " + (await r.text()));
}

async function open(browser, viewport = { width: 1280, height: 800 }, touch = false, who = USER) {
  const ctx = await browser.newContext({ viewport, isMobile: touch, hasTouch: touch });
  const page = await ctx.newPage();
  page.errors = [];
  page.on("pageerror", e => page.errors.push(e.message));
  await page.goto(BASE);
  if (await page.waitForSelector("[data-go]", { timeout: 4000 }).catch(() => null)) await page.click("[data-go]");
  await page.fill("[name=username]", who.username);
  await page.fill("[name=password]", who.password);
  await page.press("[name=password]", "Enter");
  await page.waitForSelector("#nav", { state: "attached" });
  for (let i = 0; i < 8; i++) {           // the first-run welcome: skip it
    const skip = await page.$(".dlg-bg [data-w=skip], .dlg-bg [data-a=skip]");
    if (skip) { await skip.click(); break; }
    await sleep(400);
  }
  await page.evaluate(() => document.querySelectorAll("#nnudge,#tourbar").forEach(n => n.remove()));
  return page;
}

async function go(page, hash) {
  await page.evaluate(h => { location.hash = h; }, hash);
  await page.waitForFunction(h => location.hash === h, hash);
  await page.waitForTimeout(700);
}

// the side panel's Save: waits until it closes (or shows its error)
async function save(page, label = "") {
  await page.click(".drawer .foot button[type=submit]");
  const r = await Promise.race([
    page.waitForSelector(".drawer", { state: "detached", timeout: 10000 }).then(() => "closed"),
    page.waitForFunction(() => (document.querySelector("#derr") || {}).innerText, null, { timeout: 10000 }).then(() => "error")]);
  if (r === "error") throw new Error(`${label}: ${await page.innerText("#derr")}`);
  await page.waitForTimeout(500);
}

// a figure shown in a visible table cell of the screen
async function seen(page, text) { await page.locator(".main td:visible", { hasText: text }).first().waitFor({ timeout: 10000 }); }

async function pick(page, sel, value) { await page.selectOption(sel, String(value)); await page.waitForTimeout(150); }

async function journey(name, browser, fn, viewport, touch) {
  const page = await open(browser, viewport, touch);
  const t0 = Date.now();
  try {
    await fn(page, browser);
    expect(!page.errors.length, "script errors: " + page.errors.join("; "));
    results.push({ name, ok: true, ms: Date.now() - t0 });
  } catch (e) {
    fs.mkdirSync(OUT, { recursive: true });
    const shot = path.join(OUT, name.replace(/\W+/g, "_") + ".png");
    await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
    results.push({ name, ok: false, error: e.message.split("\n")[0], shot });
  }
  await page.context().close();
}

// ------------------------------------------------------------------ journeys
// Purchase request → approval → supplier quotes → order (lowest price) → partial and final delivery → payment
async function purchasing(page) {
  await go(page, "#/purchase-requests");
  await page.click("[data-a=prnew]");
  await pick(page, ".drawer [name=bu_id]", 1);
  await page.fill(".drawer tr.prl [data-f=description]", "E2E Steel bars");
  await page.fill(".drawer tr.prl [data-f=qty]", "10");
  await page.fill(".drawer tr.prl [data-f=unit]", "ton");
  await page.fill(".drawer tr.prl [data-f=est_rate]", "1000");
  await page.click(".drawer .foot button[type=submit]");
  await page.waitForFunction(() => /^#\/purchase-request\/\d+$/.test(location.hash));
  await page.waitForSelector("text=Waiting for approval");
  await page.click("[data-a=prapp]");
  await page.waitForSelector(".toolbar >> text=Approved");
  for (const [supplier, rate] of [["E2E Supplier A", 950], ["E2E Supplier B", 990]]) {
    await page.click("[data-a=qnew]");
    await page.fill(".drawer [name=supplier]", supplier);
    await page.fill(".drawer [data-line]", String(rate));
    await save(page, "quotation " + supplier);
  }
  await go(page, await page.evaluate(() => location.hash));
  await page.click("[data-a=awlow]");
  const poLink = await page.waitForSelector('a[href^="#/po/"]');
  const poHash = await poLink.getAttribute("href");
  const prStatus = db("sql", "SELECT status, orders FROM purchase_requests WHERE orders LIKE ?", "%" + decodeURIComponent(poHash.slice(5)) + "%");
  expect(prStatus.length === 1 && prStatus[0].status === "ordered", "request not marked ordered: " + JSON.stringify(prStatus));
  const po = db("sql", "SELECT id, supplier, status, bu_id FROM purchase_orders WHERE code=?", decodeURIComponent(poHash.slice(5)))[0];
  expect(po && po.supplier === "E2E Supplier A", "order not awarded to the lowest price: " + JSON.stringify(po));

  await go(page, poHash);
  if (await page.$("[data-a=poapp]")) { await page.click("[data-a=poapp]"); await page.waitForSelector("[data-a=porec]"); }
  // part delivery: 6 of 10
  await page.click("[data-a=porec]");
  await page.fill(".drawer [data-line]", "6");
  await page.fill(".drawer [name=supplier_ref]", "E2E-DN-1");
  await save(page, "first delivery");
  expect(near(db("party_balance", "E2E Supplier A"), -5700), "owed to supplier after 6 tons: " + db("party_balance", "E2E Supplier A"));
  await go(page, "#/pos"); await go(page, poHash);
  expect((await page.innerText(".kpis")).includes("5,700.00"), "order page does not show 5,700.00 received");
  // the rest: 4
  await page.click("[data-a=porec]");
  await page.fill(".drawer [data-line]", "4");
  await save(page, "final delivery");
  expect(near(db("party_balance", "E2E Supplier A"), -9500), "owed to supplier after delivery: " + db("party_balance", "E2E Supplier A"));
  const st = db("sql", "SELECT status FROM purchase_orders WHERE id=?", po.id)[0].status;
  expect(st === "received" || st === "closed", "order status after full delivery: " + st);

  // payment, with Save pressed twice quickly: one voucher only
  await go(page, "#/payments");
  await page.click("[data-a=newdoc]");
  await page.fill(".drawer [name=party]", "E2E Supplier A");
  await page.fill(".drawer [name=amount]", "9500");
  await page.fill(".drawer [name=ref]", "E2E-PAY-1");
  await page.dblclick(".drawer .foot button[type=submit]");
  await page.waitForSelector(".drawer", { state: "detached", timeout: 10000 });
  await page.waitForTimeout(800);
  const pays = db("entries", "Payment Voucher", "E2E-PAY-1");
  expect(pays.length === 1, `payment saved ${pays.length} times`);
  expect(near(db("party_balance", "E2E Supplier A"), 0), "supplier balance after payment: " + db("party_balance", "E2E Supplier A"));
  // the supplier's statement on screen agrees
  await go(page, "#/statement?party=" + encodeURIComponent("E2E Supplier A"));
  await seen(page, "9,500.00");
}

// Customer → invoice → part receipt → what the customer still owes
async function sales(page) {
  await go(page, "#/invoice/new");
  await pick(page, "[name=bu_id]", 1);
  await page.fill("[name=customer]", "E2E Customer");
  await page.click("[data-a=leadd]");
  await page.fill("tr[data-i='0'] .l-description", "E2E Works");
  await page.fill("tr[data-i='0'] .l-qty", "2");
  await page.fill("tr[data-i='0'] .l-price", "5000");
  await page.click("[data-a=xpost]");
  await page.waitForFunction(() => /^#\/invoice\/\d+$/.test(location.hash), null, { timeout: 10000 });
  expect(near(db("party_balance", "E2E Customer"), 10000), "customer owes after invoice: " + db("party_balance", "E2E Customer"));
  const nums = db("sql", "SELECT number FROM extracts WHERE customer=? ORDER BY id", "E2E Customer").map(r => r.number);
  expect(/^INV-\d{4}-\d{2}-\d{3}$/.test(nums[0]), "invoice number left unfinished: " + nums[0]);
  // a second invoice with the suggested number untouched gets the next one, not a clash
  await go(page, "#/invoices"); await go(page, "#/invoice/new");
  await pick(page, "[name=bu_id]", 2);
  await page.fill("[name=customer]", "E2E Customer 2");
  await page.click("[data-a=leadd]");
  await page.fill("tr[data-i='0'] .l-description", "E2E Works 2");
  await page.fill("tr[data-i='0'] .l-price", "100");
  await page.click("[data-a=xsave]");
  await page.waitForFunction(() => /^#\/invoice\/\d+$/.test(location.hash), null, { timeout: 10000 });
  const n2 = db("sql", "SELECT number FROM extracts WHERE customer=?", "E2E Customer 2")[0].number;
  expect(n2 !== nums[0] && n2.startsWith(nums[0].slice(0, -3)), `second invoice numbered ${n2} after ${nums[0]}`);

  await go(page, "#/receipts");
  await page.click("[data-a=newdoc]");
  await pick(page, ".drawer [name=bu_id]", 1);
  await page.fill(".drawer [name=amount]", "4000");
  await pick(page, ".drawer [name=credit_account]", "1100");
  await page.fill(".drawer [name=party]", "E2E Customer");
  await page.fill(".drawer [name=ref]", "E2E-RV-1");
  await save(page, "receipt");
  expect(db("entries", "Receipt Voucher", "E2E-RV-1").length === 1, "receipt not posted once");
  expect(near(db("party_balance", "E2E Customer"), 6000), "customer owes after receipt: " + db("party_balance", "E2E Customer"));
  await go(page, "#/statement?party=" + encodeURIComponent("E2E Customer"));
  await seen(page, "6,000.00");
  expect((await page.innerText(".main")).includes("they owe us"), "statement does not say the customer owes us");
}

// Goods received into the main store → transfer to the site → issue to a project → stock on hand and value
async function stock(page) {
  const move = async (type, fields) => {
    await go(page, "#/moves");
    await page.click("[data-a=newdoc]");
    await pick(page, ".drawer [name=type]", type);
    for (const [k, v] of Object.entries(fields)) {
      const el = await page.$(`.drawer [name=${k}]`);
      if ((await el.evaluate(e => e.tagName)) === "SELECT") await pick(page, `.drawer [name=${k}]`, v);
      else await page.fill(`.drawer [name=${k}]`, String(v));
    }
    await save(page, type);
  };
  await move("Receipt", { warehouse_id: 1, item: "E2E Cement", unit: "bag", qty: 100, rate: 50, vendor: "E2E Supplier C", paid: 5000 });
  await move("Transfer", { warehouse_id: 1, to_warehouse_id: 2, item: "E2E Cement", qty: 30 });
  await move("Issue", { warehouse_id: 2, item: "E2E Cement", qty: 10, bu_id: 1, account: "5010" });
  const lv = db("stock", "E2E Cement");
  const by = Object.fromEntries(lv.map(r => [r.warehouse, r]));
  expect(lv.length === 2 && by["Main Store"].qty === 70 && by["Site Store — Villa 7"].qty === 20, "stock levels: " + JSON.stringify(lv));
  expect(lv.every(r => r.avg_cost === 5000), "average cost not 50.00: " + JSON.stringify(lv));
  expect(near(db("party_balance", "E2E Supplier C"), 0), "paid in full, yet supplier balance " + db("party_balance", "E2E Supplier C"));
  await go(page, "#/stock");
  await page.waitForSelector(".main table >> text=E2E Cement");
}

// Project budget → actual cost → budget vs actual report
async function budget(page) {
  const year = new Date().getFullYear();
  const bu = db("sql", "SELECT id FROM business_units WHERE code='PRJ-D03'")[0].id;
  const actual = () => db("sql", `SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                                  WHERE e.status='posted' AND e.bu_id=? AND l.account='5010' AND e.date BETWEEN ? AND ?`,
                                  bu, `${year}-01-01`, `${year}-12-31`)[0].n / 100;
  await go(page, `#/budgets?year=${year}&bu_id=${bu}&all=1`);
  await page.fill('tr[data-acc="5010"] .bt', "24000");
  await page.dispatchEvent('tr[data-acc="5010"] .bt', "change");
  expect(await page.inputValue('tr[data-acc="5010"] .bm[data-m="0"]') === "2000", "annual total not spread over the months");
  await page.click("[data-a=bsave]");
  await page.waitForTimeout(1200);
  const saved = db("sql", "SELECT months FROM budgets WHERE year=? AND bu_id=? AND account='5010'", year, bu);
  expect(saved.length === 1 && JSON.parse(saved[0].months).reduce((a, b) => a + b, 0) === 2400000, "budget not saved: " + JSON.stringify(saved));

  const before = actual();
  await go(page, "#/costs");
  await page.click("[data-a=newdoc]");
  await pick(page, ".drawer [name=bu_id]", bu);
  await pick(page, ".drawer [name=account]", "5010");
  await page.fill(".drawer [name=description]", "E2E Concrete");
  await page.fill(".drawer [name=qty]", "4");
  await page.fill(".drawer [name=unit]", "m3");
  await page.fill(".drawer [name=rate]", "1500");
  await page.fill(".drawer [name=paid]", "6000");
  await save(page, "direct cost");
  const now = actual();
  expect(near(now - before, 6000), `actual cost moved by ${now - before}, not 6,000`);

  await go(page, `#/budget-report?year=${year}&bu_id=${bu}`);
  const fmt = v => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  // columns: budget to date (January to this month), actual to date, variance (+ = under budget for a cost), variance %,
  // used %, full-year budget
  const toDate = 2000 * (new Date().getMonth() + 1);
  const want = [fmt(toDate), fmt(now), fmt(toDate - now), String(Math.round((toDate - now) / toDate * 100)),
                String(Math.round(now / toDate * 100)), "24,000.00"];
  const cells = (await page.locator(".main tr", { hasText: "5010" }).first().locator("td").allInnerTexts()).map(c => c.trim());
  expect(JSON.stringify(cells.slice(1, 7)) === JSON.stringify(want), `report row ${JSON.stringify(cells)}, expected ${JSON.stringify(want)}`);
}

// Leaving a half-filled form keeps it as a draft; it can be continued later and saved
async function draft(page) {
  await go(page, "#/payments");
  await page.click("[data-a=newdoc]");
  await page.type(".drawer [name=party]", "E2E Supplier E");
  await page.type(".drawer [name=amount]", "77");
  await page.type(".drawer [name=ref]", "E2E-DRAFT-1");
  await page.waitForTimeout(900);                       // drafts are kept half a second after typing stops
  await go(page, "#/dashboard");
  await page.waitForSelector("#draftsbtn:not([hidden])", { timeout: 5000 });
  await page.click("#draftsbtn");
  await page.click("[data-a=draftgo]");
  await page.waitForSelector(".drawer [name=amount]");
  await page.waitForTimeout(500);
  expect(await page.inputValue(".drawer [name=amount]") === "77", "draft amount not restored");
  expect(await page.inputValue(".drawer [name=party]") === "E2E Supplier E", "draft payee not restored");
  await page.check(".drawer [name=allow_overpay]");
  await save(page, "payment from a draft");
  expect(db("entries", "Payment Voucher", "E2E-DRAFT-1").length === 1, "payment from the draft not saved once");
  await page.waitForTimeout(300);
  expect(await page.$eval("#draftsbtn", b => b.hidden), "the draft is still listed after saving");
}

// A user with sales rights only: no purchasing in the menu, its address refused on screen and by the server;
// a refresh on an allowed screen stays on it
async function permissions(page, browser) {
  const clerk = { username: "e2e_sales", password: "Sales-pass-123" };
  const made = await page.evaluate(async u => (await fetch("api/users", { method: "POST", headers: { "X-Requested-With": "BeyondERP",
    "content-type": "application/json" }, body: JSON.stringify({ ...u, perms: { sales: "edit", dashboard: "view" } }) })).status, clerk);
  expect(made === 200, "could not create the sales user: " + made);
  const p2 = await open(browser, { width: 1280, height: 800 }, false, clerk);
  try {
    const links = await p2.evaluate(() => [...document.querySelectorAll("#nav a[href^='#/']")].map(a => a.getAttribute("href")));
    expect(links.includes("#/invoices"), "sales user does not see Sales Invoices");
    const leak = links.filter(h => /purchase|#\/pos$|#\/payments|#\/users|#\/coa|#\/journal/.test(h));
    expect(!leak.length, "sales user sees " + leak.join(", "));
    await p2.evaluate(() => { location.hash = "#/purchase-requests"; });      // typed or bookmarked: refused or sent elsewhere
    await p2.waitForTimeout(1500);
    expect(!(await p2.innerText(".main")).includes("PR-D00"), "purchase requests shown to a sales user");
    const status = await p2.evaluate(async () => (await fetch("api/purchase-requests", { headers: { "X-Requested-With": "BeyondERP" } })).status);
    const own = await p2.evaluate(async () => (await fetch("api/extracts", { headers: { "X-Requested-With": "BeyondERP" } })).status);
    expect(own === 200, "the same session reading invoices got " + own);
    expect(status === 403 || status === 401, "server answered " + status + " to a sales user asking for purchase requests");
    await go(p2, "#/invoices");
    await p2.reload();
    await p2.waitForSelector("#nav", { state: "attached" });
    await p2.waitForTimeout(1500);
    expect(await p2.evaluate(() => location.hash) === "#/invoices", "refresh did not stay on the invoices screen");
    expect((await p2.innerText(".main")).includes("INV-"), "invoices not listed after refresh");
    expect(!p2.errors.length, "script errors: " + p2.errors.join("; "));
  } finally { await p2.context().close(); }
}

// A form filled on a phone keeps its values when the phone is turned, and still saves
async function phoneTurn(page) {
  await go(page, "#/payments");
  await page.tap("[data-a=newdoc]");
  await page.fill(".drawer [name=party]", "E2E Supplier D");
  await page.fill(".drawer [name=amount]", "123.45");
  await page.fill(".drawer [name=ref]", "E2E-TURN-1");
  await page.check(".drawer [name=allow_overpay]");
  await page.setViewportSize({ width: 844, height: 390 });
  await page.waitForTimeout(600);
  expect(await page.inputValue(".drawer [name=amount]") === "123.45", "amount lost when the phone turned");
  expect(await page.inputValue(".drawer [name=party]") === "E2E Supplier D", "payee lost when the phone turned");
  const btn = await page.$(".drawer .foot button[type=submit]");
  const box = await btn.boundingBox();
  expect(box && box.width > 0, "Save not reachable in landscape");
  await save(page, "payment in landscape");
  expect(db("entries", "Payment Voucher", "E2E-TURN-1").length === 1, "payment from the turned phone not saved");
}

(async () => {
  let browser;
  try {
    await startServer();
    browser = await chromium.launch();
    await journey("Purchasing: request → approval → quotes → order → deliveries → payment", browser, purchasing);
    await journey("Sales: invoice → receipt → balance owed", browser, sales);
    await journey("Stock: receipt → transfer → issue → levels and value", browser, stock);
    await journey("Budget: project budget → actual cost → budget vs actual", browser, budget);
    await journey("Drafts: a half-filled form left for another screen can be continued and saved", browser, draft);
    await journey("Permissions: a sales-only user cannot reach purchasing; refresh keeps the screen", browser, permissions);
    await journey("Phone: form keeps its values when turned to landscape", browser, phoneTurn, { width: 390, height: 844 }, true);
    expect(db("balanced"), "an entry does not balance after the journeys");
    results.push({ name: "Books still balance after every journey", ok: true });
  } catch (e) {
    results.push({ name: "harness", ok: false, error: e.message });
  } finally {
    if (browser) await browser.close();
    if (server) server.kill();
  }
  for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.ok ? "" : `\n      ${r.error}${r.shot ? `\n      screenshot: ${r.shot}` : ""}`}`);
  const failed = results.filter(r => !r.ok).length;
  console.log(`\n${results.length - failed} passed, ${failed} failed`);
  if (process.env.KEEP) console.log("data kept in " + DATA); else fs.rmSync(DATA, { recursive: true, force: true });
  process.exit(failed ? 1 : 0);
})();
