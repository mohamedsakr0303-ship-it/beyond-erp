// The link-preview cards (WhatsApp, LinkedIn, Facebook…): web-src/og-image.jpg and og-image-ar.jpg, 1200×630.
// Drawn by the browser with the app's own fonts and logo. Run: NODE_PATH=$(npm root -g) node packaging/share-card/make.js
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const HERE = __dirname, OUT = path.join(HERE, "..", "..", "web-src"), T = fs.readFileSync(path.join(HERE, "card.html"), "utf8");
const cards = [
  ["og-image.jpg", "", "Run the business, not the paperwork.", "Accounting, sales, inventory, projects, HR and payroll in one cloud ERP."],
  ["og-image-ar.jpg", "ar", "أدِر أعمالك، لا أوراقها.", "الحسابات والمبيعات والمخزون والمشروعات والموارد البشرية والرواتب في نظام سحابي واحد."],
];
(async () => {
  const b = await chromium.launch(), p = await b.newPage({ viewport: { width: 1200, height: 630 } });
  for (const [file, cls, slogan, line] of cards) {
    const page = path.join(HERE, `.card-${cls || "en"}.html`);
    fs.writeFileSync(page, T.replace("__CLS__", cls).replace("__S__", slogan).replace("__D__", line));
    await p.goto("file://" + page);
    await p.evaluate(() => document.fonts.ready);
    await p.screenshot({ path: path.join(OUT, file), type: "jpeg", quality: 88 });
    fs.unlinkSync(page);
  }
  await b.close();
})();
