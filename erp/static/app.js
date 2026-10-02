/* Beyond ERP — single-page UI (vanilla JS, no build step, no external libraries). */
"use strict";
(() => {
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => String(v == null ? "" : v).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// "Remember me" off: the sign-in token lives in sessionStorage, so closing the browser signs the person out
const box = k => { try { return k === "token" && localStorage.getItem("berp.remember") === "false" ? sessionStorage : localStorage; } catch { return localStorage; } };
const store = { get(k, d) { try { const v = box(k).getItem("berp." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
                set(k, v) { try { box(k).setItem("berp." + k, JSON.stringify(v)); if (k === "token" && box(k) === sessionStorage) localStorage.removeItem("berp.token"); } catch { /* private mode */ } } };
const S = { boot: null, lang: "en", H: {}, cache: {} };

// ------------------------------------------------------------------ i18n
function t(key) {
  const custom = (S.boot && S.boot.settings && S.boot.settings.menu_labels) || {};
  if (custom[key] && custom[key][S.lang]) return custom[key][S.lang];
  const v = window.I18N[key];
  return v ? v[S.lang === "ar" ? 1 : 0] : key;
}
const tl = v => (window.I18N[v] ? t(v) : v);           // translate enum values when known
const sub = k => (window.SUBTYPE_LABELS[k] || [k, k])[S.lang === "ar" ? 1 : 0];

// ---------------------------------------------------------------- format
// regional formats (Settings → Company → Regional formats)
const reg = () => (S.boot && S.boot.settings && S.boot.settings.regional) || {};
const AR_DIGITS = "٠١٢٣٤٥٦٧٨٩";
const digits = s => reg().digits === "arabic" ? String(s).replace(/[0-9]/g, d => AR_DIGITS[d]) : String(s);
const money = n => {
  n = Number(n) || 0;
  const dec = reg().decimals === "0" || reg().decimals === 0 ? 0 : 2;
  const s = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  return digits(n < 0 ? (reg().negative === "minus" ? `-${s}` : `(${s})`) : s);
};
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fd = iso => {
  const v = String(iso || ""); const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v); if (!m) return esc(v);
  const f = reg().date_format || "YYYY-MM-DD";
  const out = f === "DD/MM/YYYY" ? `${m[3]}/${m[2]}/${m[1]}` : f === "DD MMM YYYY" ? `${+m[3]} ${MON[+m[2] - 1]} ${m[1]}` : v.slice(0, 10);
  return esc(digits(out));
};
const pct = n => ((Number(n) || 0) * 100).toFixed(1) + "%";
const numf = n => (Number(n) || 0).toLocaleString("en-US", { maximumFractionDigits: 3 });
const today = () => new Date().toISOString().slice(0, 10);
const yearStart = () => today().slice(0, 4) + "-01-01";
const cur = () => (S.boot.settings.currency || "EGP");

// ------------------------------------------------------------------- api
const STATIC = !!window.BEYOND_STATIC;   // browser-only build (static hosting): no server, see bridge.js
async function api(method, path, body, raw) {
  if (STATIC) return staticApi(method, path, body, raw);
  const opt = { method, headers: { "X-Requested-With": "BeyondERP" }, credentials: "same-origin" };
  if (raw) { opt.body = raw; opt.headers["Content-Type"] = "application/octet-stream"; }
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (r.status === 401) { S.boot.user = null; renderAuth(); throw new Error(data && data.error || "Please sign in"); }
  if (!r.ok) { const e = new Error((data && data.error) || `HTTP ${r.status}`); e.data = data; e.status = r.status; throw e; }
  return data;
}
function staticHeaders() { return { "x-requested-with": "BeyondERP", "x-session": store.get("token", "") || "" }; }
async function staticApi(method, path, body, raw) {
  const h = staticHeaders();
  let payload = null;
  if (raw) { payload = raw; h["content-type"] = "application/octet-stream"; }
  else if (body !== undefined) { payload = JSON.stringify(body); h["content-type"] = "application/json"; }
  const r = await window.__beyondFetch(method, path, h, payload);
  let data = null;
  try { data = JSON.parse(new TextDecoder().decode(r.body)); } catch { data = null; }
  if (data && "token" in data) store.set("token", data.token || "");
  if (r.status === 401) { S.boot.user = null; renderAuth(); throw new Error(data && data.error || "Please sign in"); }
  if (r.status >= 400) { const e = new Error((data && data.error) || `HTTP ${r.status}`); e.data = data; e.status = r.status; throw e; }
  return data;
}
// Open an /api/ download or printable page. Server mode: normal navigation. Static mode: via the bridge.
async function openApi(url, newTab) {
  if (!STATIC) { if (newTab) window.open(url, "_blank", "noopener"); else location.href = url; return; }
  const w = newTab ? window.open("", "_blank") : null;
  try {
    const r = await window.__beyondFetch("GET", url, staticHeaders(), null);
    if (r.status >= 400) { let m = ""; try { m = JSON.parse(new TextDecoder().decode(r.body)).error; } catch { /* binary */ } throw new Error(m || `HTTP ${r.status}`); }
    const blob = new Blob([r.body], { type: r.ctype });
    const href = URL.createObjectURL(blob);
    if (r.filename) { const a = document.createElement("a"); a.href = href; a.download = r.filename; document.body.appendChild(a); a.click(); a.remove(); if (w) w.close(); }
    else if (w) w.location = href; else window.open(href, "_blank");
    setTimeout(() => URL.revokeObjectURL(href), 60000);
  } catch (e) { if (w) w.close(); fail(e); }
}
document.addEventListener("click", e => {
  const a = e.target.closest('a[href^="/api/"]');
  if (a && STATIC) { e.preventDefault(); openApi(a.getAttribute("href"), a.target === "_blank"); }
}, true);

function toast(msg, err) {
  const el = document.createElement("div");
  el.className = "toast" + (err ? " err" : "");
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), err ? 6000 : 2600);
}
const fail = e => toast(e.message || String(e), true);
const qs = o => Object.entries(o).filter(([, v]) => v !== "" && v != null && v !== false).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");

// ------------------------------------------------------------ permissions
const LV = ["none", "view", "add", "edit"];
// "section" or "section.screen"; a screen falls back to its section, a section counts if any of its screens is granted
const levelOf = key => {
  const u = S.boot && S.boot.user; if (!u) return "none"; if (u.is_admin) return "edit";
  const p = u.perms;
  if (key.includes(".")) return LV.includes(p[key]) ? p[key] : (p[key.split(".")[0]] || "none");
  let best = p[key] || "none";
  Object.entries(p).forEach(([k, v]) => { if (k.startsWith(key + ".") && LV.indexOf(v) > LV.indexOf(best)) best = v; });
  return best;
};
const can = (mod, lvl = "view") => LV.indexOf(levelOf(mod)) >= LV.indexOf(lvl);

// ---------------------------------------------------------- appearance
function applyLook() {
  const st = S.boot.settings || {};
  S.lang = store.get("lang", st.language || "en");
  document.documentElement.lang = S.lang;
  document.documentElement.dir = S.lang === "ar" ? "rtl" : "ltr";
  const font = store.get("font", "") || st.font_family || "system";
  const size = store.get("fontSize", "") || st.font_size || "15";
  const fam = font === "system" ? 'system-ui,-apple-system,"Segoe UI",Roboto,Tahoma,Arial,sans-serif' : `"${font}",Tahoma,Arial,sans-serif`;
  document.documentElement.style.setProperty("--font", fam);
  document.documentElement.style.setProperty("--fs", size + "px");
  const theme = store.get("theme", "auto");
  if (theme === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", theme);
  const ap = st.appearance || {}, de = document.documentElement;
  ["--brand", "--accent"].forEach(v => de.style.removeProperty(v));
  if (/^#[0-9a-fA-F]{6}$/.test(ap.accent || "")) { de.style.setProperty("--brand", ap.accent); de.style.setProperty("--accent", ap.accent); }
  de.dataset.sidebar = ap.sidebar || "light"; de.dataset.density = ap.density || "comfortable"; de.dataset.corners = ap.corners || "rounded";
  document.title = (ap.app_name || ((st.company_name || "Beyond") + " ERP"));
}
const hiddenMods = () => (S.boot && S.boot.settings && S.boot.settings.hidden_modules) || [];
// hidden for everyone in Settings → Menus: a whole section ("sales") or one screen ("sales.offers")
const shown = key => !hiddenMods().includes(String(key).split(".")[0]) && !hiddenMods().includes(String(key));

// ---------------------------------------------------------------- events
document.addEventListener("click", e => {
  // phones / narrow windows: tapping anywhere outside the open side menu closes it
  const app = $(".app");
  if (app && app.classList.contains("menu") && !e.target.closest(".side") && !e.target.closest("[data-a=menu]")) {
    app.classList.remove("menu"); e.preventDefault(); return;
  }
  if (app && e.target.closest("a.brand")) app.classList.remove("menu");
  const el = e.target.closest("[data-a]");
  if (el && S.H[el.dataset.a]) { e.preventDefault(); S.H[el.dataset.a](el, e); return; }
  const dd = e.target.closest(".dd > .btn");
  $$(".dd.open").forEach(d => { if (!dd || d !== dd.parentElement) d.classList.remove("open"); });
  if (dd) { e.preventDefault(); dd.parentElement.classList.toggle("open"); }
  const row = e.target.closest("tr[data-href]");
  if (row && !e.target.closest("button,a,input,select")) location.hash = row.dataset.href;
});
document.addEventListener("change", e => { const el = e.target.closest("[data-change]"); if (el && S.H[el.dataset.change]) S.H[el.dataset.change](el, e); });
document.addEventListener("input", e => { const el = e.target.closest("[data-input]"); if (el && S.H[el.dataset.input]) S.H[el.dataset.input](el, e); });
document.addEventListener("keydown", e => { if (e.key === "Escape") { closeDrawer(); const app = $(".app"); if (app) app.classList.remove("menu"); } });
window.addEventListener("hashchange", () => { S.navCount = (S.navCount || 0) + 1; route(); });

// ---------------------------------------------------------------- routing
function parseHash() {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, query] = h.split("?");
  const q = {}; new URLSearchParams(query || "").forEach((v, k) => { q[k] = v; });
  return { parts: (path || "dashboard").split("/"), q };
}
function setQ(q) { const { parts } = parseHash(); location.hash = "#/" + parts.join("/") + (qs(q) ? "?" + qs(q) : ""); }

const PAGES = {};
async function route() {
  if (!S.boot || !S.boot.user) return;
  S.H = { ...S.GLOBAL_H };
  const { parts, q } = parseHash();
  // land on the first screen this user may open when the requested one is not permitted
  const menuItems = window.MENU.flatMap(([, items]) => items);
  const here = menuItems.find(i => i[1] === parts.join("/")) || menuItems.find(i => i[1] === parts[0]);
  if (here && (!can(here[2]) || !shown(here[2]))) {
    const first = menuItems.find(i => can(i[2]) && shown(i[2]));
    if (first && first !== here) { location.hash = "#/" + first[1]; return; }
  }
  $(".app") && $(".app").classList.remove("menu");
  renderNav(parts.join("/"));
  const page = PAGES[parts[0]] || PAGES.dashboard;
  // each visit draws into its own box: a slower page still loading when the person moved on draws off-screen
  const host = $("#view"), view = document.createElement("div");
  view.className = "page"; view.innerHTML = `<div class="empty">${t("loading")}</div>`;
  host.replaceChildren(view);
  try { await page(view, parts, q); } catch (e) { view.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; }
  if (parts.length === 1) addImportMenu(view, parts[0]).catch(() => {});
  stackify(view);
  if (view.isConnected) window.scrollTo(0, 0);
}

// Phones show list tables (".tbl.stack") as one card per row: each cell carries its column heading.
function stackify(root) {
  $$(".tbl.stack table", root).forEach(tb => {
    const heads = $$("thead th", tb).map(th => th.textContent.trim());
    $$("tbody tr", tb).forEach(tr => [...tr.children].forEach((td, i) => { if (heads[i] && !td.dataset.l) td.dataset.l = heads[i]; }));
  });
}

// ----------------------------------------------------------------- shell
function renderShell() {
  const st = S.boot.settings;
  $("#root").innerHTML = `<div class="app">
    <aside class="side">${brandBlock(st)}<nav class="nav" id="nav"></nav></aside>
    <div class="main"><header class="top">
      <button class="btn sm menu-btn" data-a="menu" aria-label="Menu">☰</button>
      <button class="btn icon sm top-back" data-a="goback" style="display:none" aria-label="${esc(t("back"))}">${BACK_ICON}</button>
      <div class="ttl-box"><h1 id="ttl"></h1><div class="crumb" id="crumb"></div></div><span class="spacer"></span>
      <div class="dd"><button class="btn pri sm" aria-label="${esc(t("new"))}">+<span class="lbl"> ${t("new")}</span></button><div class="dd-menu" id="newmenu"></div></div>
      <span id="syncdot" class="chip" style="display:none"></span>
      <button class="btn sm" data-a="lang"><span class="lbl">${t("language")}</span><span class="lbl-short">${S.lang === "ar" ? "EN" : "ع"}</span></button>
      <div class="dd"><button class="btn sm user-btn"><span class="avatar">${esc(((S.boot.user.full_name || S.boot.user.username || "?").trim().split(/\s+/).map(w => w[0]).join("").slice(0, 2)).toUpperCase())}</span><span class="lbl">${esc(S.boot.user.full_name || S.boot.user.username)} ▾</span></button><div class="dd-menu">
        <button data-a="theme">${t("theme")}: ${esc(store.get("theme", "auto"))}</button>
        ${cloudOn() ? `<button data-a="coswitch">⇄ ${S.lang === "ar" ? "الشركات" : "Companies"}</button>` : ""}
        <a href="#/settings">${t("m_settings")}</a>${STATIC && /^https?:$/.test(location.protocol) ? `<a href="#/download">⬇ ${S.lang === "ar" ? "تحميل التطبيق" : "Get the app"}</a>` : ""}<button data-a="pw">${t("change_pw")}</button><button data-a="logout">${t("logout")}</button></div></div>
    </header><main class="content" id="view"></main></div></div>`;
  // "+ New": the items chosen in Settings → Menus, in that order, that this user may add
  const items = newMenuItems().filter(i => i.show && i.ok);
  $("#newmenu").innerHTML = items.map(i => `<button data-a="quicknew" data-doc="${esc(i.key)}">${esc(i.label)}</button>`).join("") || `<div class="muted" style="padding:8px">${t("view_only")}</div>`;
}
// Sidebar header: logo and/or name (Settings → Appearance → Sidebar shows), and an optional tagline
function brandBlock(st) {
  const ap = st.appearance || {}, show = ap.brand_show || "logo_name";
  const logo = S.boot.app_logo && show !== "name" ? `<img class="brand-logo ${show === "logo" ? "only" : ""}" src="data:${S.boot.app_logo.mime};base64,${S.boot.app_logo.data}" alt="${esc(ap.app_name || st.company_name || "")}">` : "";
  const name = show !== "logo" || !logo ? `<b>${esc(ap.app_name || st.company_name || "BEYOND")}</b>` : "";
  const tag = ap.tagline_show === false ? "" : (ap.tagline != null ? ap.tagline : st.company_tagline || "");
  return `<a href="#/dashboard" class="brand ${show === "logo" && logo ? "logo-only" : ""}" title="${esc(t("m_dashboard"))}">${logo}${name}${tag ? `<small>${esc(tag)}</small>` : ""}</a>`;
}
// Every possible "+ New" item with its label and whether this user may use it
function newMenuItems() {
  const defs = Object.fromEntries((S.boot.enums.menu_items || []).map(m => [m.key, m]));
  const types = Object.fromEntries((S.boot.enums.entry_types || []).map(e => [e.id, e]));
  const docLabel = { direct_cost: "m_costs", expense: "m_expenses", payment: "m_payments", receipt: "m_receipts", invoice: "m_invoices", payroll: "m_payroll", stock_move: "m_moves", journal: "m_journal", bu: "m_bus", offer: "m_offers" };
  return (S.boot.enums.new_menu || []).map(({ key, show }) => {
    if (key.startsWith("entry:")) { const e = types[key.slice(6)]; if (!e) return null;
      return { key, show: show && e.active !== false, label: (S.lang === "ar" && e.name_ar) || e.name, ok: can("accounting.journal", "add") && shown("accounting.journal"), entry: true }; }
    const d = defs[key]; if (!d) return null;
    const doc = key.startsWith("doc:") ? key.slice(4) : null;
    return { key, show, label: doc ? t(docLabel[doc]) : (S.lang === "ar" ? d.ar : d.en), ok: can(d.module, "add") && shown(d.module) };
  }).filter(Boolean);
}
S.GLOBAL_H = {
  menu: () => $(".app").classList.toggle("menu"),
  lang: () => { store.set("lang", S.lang === "ar" ? "en" : "ar"); applyLook(); renderShell(); route(); },
  theme: () => { const order = ["auto", "light", "dark"]; store.set("theme", order[(order.indexOf(store.get("theme", "auto")) + 1) % 3]); applyLook(); renderShell(); route(); },
  logout: async () => { await api("POST", "/api/logout").catch(() => {}); store.set("token", "");
    if (STATIC && window.BeyondCloud) window.BeyondCloud.signout(); location.hash = ""; S.boot.user = null; renderAuth(); },
  pw: () => drawer(t("change_pw"), form([{ k: "current", l: t("current_pw"), type: "password" }, { k: "new", l: t("new_pw"), type: "password" }]),
    async v => { await api("PUT", "/api/me/password", v); toast(t("saved")); return true; }),
  quicknew: el => newItem(el.dataset.doc),
  coswitch: () => companySwitcher(),
  goback: () => { const h = parseHash().parts; const parent = { bu: "bus", invoice: "invoices", boq: "boqs", offer: "offers", offertypes: "offers", report: "dashboard", loan: "loans", cust: "custody", rental: "rentals", product: "products", sale: "sales", bankstmt: "bankrec" }[h[0]];
    if (S.navCount > 0) history.back(); else location.hash = "#/" + (parent || "dashboard"); },
  closeDrawer: () => closeDrawer(),
};
function renderNav(active) {
  const nav = $("#nav"); if (!nav) return;
  let html = "", title = "", group = "";
  for (const [g, items] of window.MENU) {
    const vis = items.filter(i => can(i[2]) && shown(i[2]));
    if (!vis.length) continue;
    html += `<div class="grp">${esc(t(g))}</div>`;
    for (const [k, r, , ic] of vis) {
      const parent = { bu: "bus", invoice: "invoices", boq: "boqs", offer: "offers", offertypes: "offers", loan: "loans", cust: "custody",
        rental: "rentals", product: "products", sale: "sales", bankstmt: "bankrec" }[active.split("/")[0]];
      const on = active === r || active.startsWith(r + "/") || parent === r;
      if (on) { title = t(k); group = t(g); }
      html += `<a href="#/${r}" class="${on ? "on" : ""}"><span class="ic">${ic}</span>${esc(t(k))}</a>`;
    }
  }
  nav.innerHTML = html;
  $("#ttl").textContent = title || t("m_dashboard");
  $("#crumb").textContent = group;
  // detail pages (a business unit, an invoice, a quotation, an offer…) get a back arrow to the list they came from
  const inMenu = window.MENU.some(([, items]) => items.some(i => i[1] === active));
  const back = $(".top-back"); if (back) back.style.display = inMenu ? "none" : "";
}

// ------------------------------------------------------------- auth pages
// ------------------------------------------------ shared workspace (Supabase) — same calls in both editions
const CloudAPI = STATIC ? {
  status: () => window.BeyondCloud.status(), config: (url, key) => window.BeyondCloud.config(url, key),
  signin: (email, password, create) => window.BeyondCloud.signin(email, password, create), info: () => window.BeyondCloud.info(),
  claim: name => window.BeyondCloud.claim(name), join: () => window.BeyondCloud.join(), pull: () => window.BeyondCloud.pull(),
  disconnect: () => window.BeyondCloud.disconnect(), members: () => window.BeyondCloud.members(), setMember: m => window.BeyondCloud.setMember(m),
  linkMember: m => window.BeyondCloud.linkMember(m),
  manageUser: m => window.BeyondCloud.manageUser(m), deleteAccount: m => window.BeyondCloud.deleteAccount(m),
  companies: () => window.BeyondCloud.companies(), createCompany: n => window.BeyondCloud.createCompany(n), joinCompany: c => window.BeyondCloud.joinCompany(c),
  renameCompany: n => window.BeyondCloud.renameCompany(n), newInviteCode: () => window.BeyondCloud.newInviteCode(), removeMember: e => window.BeyondCloud.removeMember(e),
  chooseCompany: id => window.BeyondCloud.chooseCompany(id),
} : {
  status: () => api("GET", "/api/cloud"), config: (url, key) => api("PUT", "/api/cloud/config", { url, key }),
  signin: (email, password, create) => api("POST", "/api/cloud/signin", { email, password, create }), info: () => api("GET", "/api/cloud/info"),
  claim: name => api("POST", "/api/cloud/claim", { name }), join: () => api("POST", "/api/cloud/join", {}), pull: () => api("POST", "/api/cloud/pull", {}),
  disconnect: () => api("POST", "/api/cloud/disconnect", {}), members: () => api("GET", "/api/cloud/members"), setMember: m => api("PUT", "/api/cloud/members", m),
  linkMember: m => api("PUT", "/api/cloud/members/link", m),
  manageUser: m => api("PUT", "/api/cloud/account", m), deleteAccount: m => api("POST", "/api/cloud/account/delete", m),
  companies: () => api("GET", "/api/cloud/info").then(i => i.companies || []), createCompany: name => api("POST", "/api/cloud/company", { action: "create", name }),
  joinCompany: code => api("POST", "/api/cloud/company", { action: "join", code }), renameCompany: name => api("POST", "/api/cloud/company", { action: "rename", name }),
  newInviteCode: () => api("POST", "/api/cloud/company", { action: "code" }).then(r => r.invite_code), removeMember: email => api("POST", "/api/cloud/company", { action: "remove", email }),
  chooseCompany: id => api("POST", "/api/cloud/company", { action: "choose", company: id }).then(() => false),
};
const cloudOn = () => STATIC ? !!(window.BeyondCloud && window.BeyondCloud.joined()) : !!(S.boot && S.boot.cloud && S.boot.cloud.joined);

// Password field(s) with a "Show password" switch; `confirm` adds a second field that must match (new accounts,
// new passwords). The browser's own validation then refuses to submit until both are the same.
function pwFields(confirm, label, optional, help) {
  const ar = S.lang === "ar", req = optional ? "" : "required";
  return `<label class="f">${esc(label || t("password"))}<input name="password" type="password" autocomplete="${confirm ? "new-password" : "current-password"}" ${req} minlength="6">${help ? `<span class="muted" style="font-weight:400">${esc(help)}</span>` : ""}</label>
    ${confirm ? `<label class="f">${ar ? "تأكيد كلمة المرور" : "Confirm password"}<input name="password2" type="password" autocomplete="new-password" ${req} minlength="6" data-input="pwmatch"></label>
      <div id="pwhint" style="font-size:.8em;min-height:1.1em"></div>` : ""}
    <label class="f chk"><input type="checkbox" data-change="pwshow">${ar ? "إظهار كلمة المرور" : "Show password"}</label>`;
}
function pwWire(root) {
  const ar = S.lang === "ar";
  S.H.pwshow = el => $$("input[name=password], input[name=password2]", root).forEach(i => { i.type = el.checked ? "text" : "password"; });
  S.H.pwmatch = () => {
    const a = $("[name=password]", root), b = $("[name=password2]", root), h = $("#pwhint", root); if (!b) return;
    const same = a.value === b.value || (!b.value && !b.required && !a.value);
    b.setCustomValidity(same ? "" : (ar ? "كلمتا المرور غير متطابقتين" : "The passwords do not match"));
    if (h) { h.textContent = !b.value && !a.value ? "" : !b.value ? (ar ? "اكتب كلمة المرور مرة أخرى" : "Type the password again") : same ? (ar ? "✓ متطابقتان" : "✓ Passwords match") : (ar ? "✗ كلمتا المرور غير متطابقتين" : "✗ The passwords do not match");
      h.className = same ? "bal-ok" : "bal-bad"; }
  };
  const a = $("[name=password]", root); if (a) a.addEventListener("input", () => S.H.pwmatch());
}

// Browser edition connected to a workspace. Start screen: Continue with Google / Sign in with email, remember me,
// "No account? Create one", "Trouble signing in? Get help" (password reset / confirmation email).
const GOOGLE_G = `<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>`;
const MAIL_ICON = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>`;
async function renderCloudAuth(msg, kind = "info", mode = "start") {
  applyLook();
  const ar = S.lang === "ar", C = window.BeyondCloud;
  const prov = S.providers || (S.providers = await C.providers());
  const googleUrl = C.oauthUrl("google");
  const remember = `<label class="f chk" style="justify-content:center;margin:6px 0"><input type="checkbox" id="remember" ${C.remembered() ? "checked" : ""}>${ar ? "تذكرني لتسجيل دخول أسرع" : "Remember me for faster sign in"}</label>`;
  const emailField = mode === "email"
    ? `<label class="f">${ar ? "البريد الإلكتروني أو اسم المستخدم" : "Email or username"}<input name="email" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" required value="${esc(store.get("lastEmail", "") || C.email())}"></label>`
    : `<label class="f">${t("email")}<input name="email" type="email" autocomplete="username" required value="${esc(C.email() || (String(store.get("lastEmail", "")).includes("@") ? store.get("lastEmail", "") : ""))}"></label>`;
  const back = `<a href="#" data-a="mode_start" class="muted" style="font-size:.85em">← ${ar ? "كل طرق تسجيل الدخول" : "All sign-in options"}</a>`;
  const title = { start: ar ? "مرحباً بعودتك" : "Welcome back.", email: ar ? "تسجيل الدخول" : "Sign in",
    signup: ar ? "إنشاء حساب" : "Create your account", help: ar ? "مساعدة في تسجيل الدخول" : "Trouble signing in?" }[mode];
  let body;
  if (mode === "start") body = `
    ${prov.google && googleUrl ? `<button class="btn oauth" type="button" data-a="cgoogle">${GOOGLE_G}<span>${ar ? "تسجيل الدخول بحساب Google" : "Sign in with Google"}</span></button>` : ""}
    <button class="btn oauth" type="button" data-a="mode_email">${MAIL_ICON}<span>${ar ? "الدخول بالبريد أو اسم المستخدم" : "Sign in with email or username"}</span></button>
    ${remember}
    <p class="center">${ar ? "ليس لديك حساب؟" : "No account?"} <a href="#" data-a="mode_signup">${ar ? "أنشئ حساباً" : "Create one"}</a></p>
    <p class="center">${ar ? "مشكلة في تسجيل الدخول؟" : "Forgot your password or trouble signing in?"} <a href="#" data-a="mode_help">${ar ? "احصل على مساعدة" : "Get help"}</a></p>`;
  else if (mode === "email") body = `<form id="lf" class="grid">${emailField}${pwFields(false)}${remember}
      <button class="btn pri" type="submit">${t("sign_in")}</button></form>
    <p class="center">${ar ? "ليس لديك حساب؟" : "No account?"} <a href="#" data-a="mode_signup">${ar ? "أنشئ حساباً" : "Create one"}</a> · <a href="#" data-a="mode_help">${ar ? "نسيت كلمة المرور؟" : "Forgot password?"}</a></p>`;
  else if (mode === "signup") body = `
    ${prov.google && googleUrl ? `<button class="btn oauth" type="button" data-a="cgoogle">${GOOGLE_G}<span>${ar ? "المتابعة بحساب Google" : "Continue with Google"}</span></button>
      <div class="or"><span>${ar ? "أو" : "or"}</span></div>` : ""}
    <form id="lf" class="grid">${emailField}${pwFields(true)}
      <button class="btn pri" type="submit">${ar ? "إنشاء الحساب" : "Create account"}</button></form>
    <p class="center">${ar ? "لديك حساب؟" : "Already have an account?"} <a href="#" data-a="mode_start">${t("sign_in")}</a></p>`;
  else body = `<form id="lf" class="grid"><p class="muted" style="margin:0">${ar ? "اكتب بريدك واختر ما تحتاجه:" : "Enter your email and choose what you need:"}</p>${emailField}
      <button class="btn" type="button" data-a="cforgot">${ar ? "إرسال رابط إعادة تعيين كلمة المرور" : "Email me a password-reset link"}</button>
      <button class="btn" type="button" data-a="cresend">${ar ? "إعادة إرسال رسالة تأكيد الحساب" : "Resend my account confirmation email"}</button></form>
    <p class="muted" style="font-size:.85em">${ar ? "إن كنت تستخدم Google فسجّل الدخول بزر Google — لا توجد كلمة مرور لهذا الحساب هنا." : "Signed up with Google? Use “Sign in with Google” — that account has no password here."}</p>`;
  $("#root").innerHTML = `<div class="login auth">${S.boot.app_logo ? `<img class="login-logo" src="data:${S.boot.app_logo.mime};base64,${S.boot.app_logo.data}" alt="">` : ""}
    <div class="muted center" style="font-weight:600;letter-spacing:.4px">${esc(S.boot.settings.company_name || "Beyond")} ERP</div>
    <h1 class="center">${esc(title)}</h1><div id="lerr">${msg ? `<div class="banner ${kind}">${esc(msg)}</div>` : ""}</div>${body}
    ${mode !== "start" ? `<p class="center">${back}</p>` : ""}
    <div class="row" style="justify-content:space-between;margin-top:14px;font-size:.8em"><a href="#" data-a="lang2">${ar ? "English" : "العربية"}</a>
      <a href="#" data-a="clocal" class="muted">${ar ? "استخدام هذا الجهاز بدون مساحة العمل" : "Use this device without the workspace"}</a></div></div>`;
  const show = (m, k = "err") => { $("#lerr").innerHTML = `<div class="banner ${k}">${esc(m)}</div>`; };
  const needEmail = () => { const e = $("[name=email]"); if (!e || !e.value || !e.checkValidity()) { if (e) e.reportValidity(); return null; } store.set("lastEmail", e.value.trim()); return e.value.trim(); };
  const keepChoice = () => { const r = $("#remember"); C.setRemember(r ? r.checked : true); };
  S.H = {
    lang2: () => { store.set("lang", S.lang === "ar" ? "en" : "ar"); renderCloudAuth("", "info", mode); },
    mode_start: () => renderCloudAuth("", "info", "start"), mode_email: () => { keepChoice(); renderCloudAuth("", "info", "email"); },
    mode_signup: () => renderCloudAuth("", "info", "signup"), mode_help: () => renderCloudAuth("", "info", "help"),
    clocal: () => { store.set("cloudLocal", true); if (C.company()) location.reload(); else renderAuth(); },
    cgoogle: () => { keepChoice(); location.href = googleUrl; },
    cforgot: async () => { const email = needEmail(); if (!email) return;
      try { await C.recover(email); show(ar ? `أرسلنا رابط إعادة تعيين كلمة المرور إلى ${email}. افتحه لاختيار كلمة مرور جديدة.` : `We sent a password-reset link to ${email}. Open it to choose a new password.`, "ok"); } catch (e) { show(e.message); } },
    cresend: async () => { const email = needEmail(); if (!email) return;
      try { await C.resend(email); show(ar ? `أعدنا إرسال رسالة التأكيد إلى ${email}.` : `We sent a new confirmation email to ${email}.`, "ok"); } catch (e) { show(e.message); } },
  };
  const f = $("#lf"); if (!f || mode === "help") return;
  pwWire(f);
  f.addEventListener("submit", async e => {
    e.preventDefault();
    if (mode === "signup") S.H.pwmatch();
    if (!f.reportValidity()) return;
    keepChoice();
    const v = Object.fromEntries(new FormData(f)); store.set("lastEmail", v.email.trim());
    const btn = $("button[type=submit]", f); btn.disabled = true;
    try {
      show(t("loading"), "info");
      if (mode === "signup") {
        const r = await C.signup(v.email, v.password);
        if (r.confirm) return renderCloudAuth(r.message, "ok", "email");
        await cloudEnter(await C.info());
      } else await cloudEnter(await CloudAPI.signin(v.email, v.password, false));
    } catch (x) { show(x.message); } finally { btn.disabled = false; }
  });
}
// A link from a Supabase email (account confirmation or password reset) brought the person back to the app.
async function cloudReturn() {
  const ar = S.lang === "ar";
  let r;
  try { r = await window.BeyondCloud.takePendingAuth(); } catch (e) { return renderCloudAuth(e.message, "err", "start"); }
  if (!r) return false;
  if (r.error) { await renderCloudAuth(r.error, "err", "start"); return true; }
  if (r.type === "recovery") {
    applyLook();
    $("#root").innerHTML = `<div class="login"><h1>${ar ? "كلمة مرور جديدة" : "Choose a new password"}</h1><div id="lerr"></div>
      <form id="lf" class="grid">${pwFields(true, t("new_pw"))}
      <button class="btn pri" type="submit">${t("save")}</button></form></div>`;
    pwWire($("#lf"));
    $("#lf").addEventListener("submit", async e => { e.preventDefault(); S.H.pwmatch(); if (!e.target.reportValidity()) return;
      try { await window.BeyondCloud.setPassword(new FormData(e.target).get("password")); await cloudEnter(r.info); }
      catch (x) { $("#lerr").innerHTML = `<div class="banner err">${esc(x.message)}</div>`; } });
    return true;
  }
  try { await cloudEnter(r.info); } catch (e) { await renderCloudAuth(e.message, "err", "start"); }
  return true;
}
// After a successful workspace sign-in: choose / create / join a company if needed, get its books, open the user.
async function cloudEnter(info) {
  const ar = S.lang === "ar", C = window.BeyondCloud;
  // one company and nothing else pending: open it straight away
  if (info.needs_company && (info.live || []).length === 1 && !(info.companies || []).some(c => c.pending) && C.chooseCompany(info.live[0].id)) return location.reload();
  if (info.needs_company || !info.is_member) return renderCompanyGate(info);
  if (!C.joined()) {
    const box = $("#lerr"); if (box) box.innerHTML = `<div class="banner info">${ar ? "جارٍ تنزيل دفاتر الشركة…" : "Downloading the company's books…"}</div>`;
    await C.join();
  } else await C.pull().catch(() => {});
  const r = await C.openSession();
  const data = JSON.parse(new TextDecoder().decode(r.body));
  if (r.status >= 400) throw new Error(data.error);
  store.set("token", data.token || ""); store.set("cloudLocal", false);
  await boot();
}

// Signed in, but no company open yet: pick one, start a new one, or ask to join one with its invite code.
function renderCompanyGate(info, msg, kind = "err") {
  const ar = S.lang === "ar", C = window.BeyondCloud;
  const live = info.live || (info.companies || []).filter(c => c.active && !c.pending);
  const pending = (info.companies || []).filter(c => c.pending);
  if (!msg && info.pending && info.company_name) { msg = (ar ? "طلبك للانضمام إلى " : "Your request to join ") + info.company_name + (ar ? " بانتظار موافقة المسؤول." : " is waiting for its administrator's approval."); kind = "info"; }
  applyLook();
  $("#root").innerHTML = `<div class="login auth co-gate"><h1 class="center">${live.length ? (ar ? "اختر الشركة" : "Choose a company") : (ar ? "ابدأ" : "Get started")}</h1>
    <p class="muted center">${esc(info.email || C.email())}</p><div id="lerr">${msg ? `<div class="banner ${kind}">${esc(msg)}</div>` : ""}</div>
    ${live.length ? `<div class="co-list">${live.map(c => `<button class="btn oauth" type="button" data-a="copick" data-id="${esc(c.id)}"><span class="co-ic">${esc((c.name || "?").trim().charAt(0).toUpperCase())}</span><span>${esc(c.name)}</span>${c.is_admin ? `<span class="chip">${t("admin")}</span>` : ""}</button>`).join("")}</div>` : ""}
    ${pending.length ? `<div class="banner info">${ar ? "بانتظار الموافقة: " : "Waiting for approval: "}${pending.map(c => esc(c.name)).join(", ")}</div>` : ""}
    <details class="co-more" ${live.length ? "" : "open"}><summary>${ar ? "الانضمام إلى شركة برمز دعوة" : "Join a company with an invite code"}</summary>
      <form id="cojoin" class="row"><input name="code" required autocapitalize="characters" autocomplete="off" placeholder="${ar ? "رمز الدعوة (8 أحرف)" : "Invite code (8 letters)"}" style="flex:1;text-transform:uppercase"><button class="btn pri" type="submit">${ar ? "طلب الانضمام" : "Ask to join"}</button></form>
      <p class="muted" style="font-size:.8em;margin:4px 0 0">${ar ? "اطلب الرمز من مسؤول الشركة (الإعدادات ← مساحة العمل)." : "Get it from the company's administrator (Settings → Workspace)."}</p></details>
    <details class="co-more" ${live.length || pending.length ? "" : "open"}><summary>${ar ? "إنشاء شركة جديدة" : "Create a new company"}</summary>
      <form id="conew" class="grid"><input name="name" required maxlength="120" placeholder="${ar ? "اسم الشركة" : "Company name"}">
        <label class="f chk"><input type="checkbox" name="mine">${ar ? "ابدأ بدفاتر هذا الجهاز (بدلاً من دفاتر فارغة)" : "Start from the books on this device (instead of empty books)"}</label>
        <button class="btn" type="submit">${ar ? "إنشاء — ستكون مسؤولها" : "Create — you become its administrator"}</button></form></details>
    <p class="center" style="margin-top:14px;font-size:.85em"><a href="#" data-a="coout">${t("logout")}</a></p></div>`;
  const show = (m, k = "err") => { $("#lerr").innerHTML = `<div class="banner ${k}">${esc(m)}</div>`; };
  const open = id => { if (C.chooseCompany(id)) location.reload(); else cloudEnter({ ...info, needs_company: false, is_member: true }).catch(e => show(e.message)); };
  S.H = { copick: el => { show(ar ? "جارٍ فتح الشركة…" : "Opening the company…", "info"); open(el.dataset.id); },
    coout: () => { C.signout(); renderCloudAuth(); } };
  $("#cojoin").addEventListener("submit", async e => { e.preventDefault(); const code = new FormData(e.target).get("code").trim();
    try { const r = await C.joinCompany(code); if (r.is_member) return open(r.id);
      renderCompanyGate(await C.info(), (ar ? "تم إرسال طلبك إلى مسؤول " : "Request sent to the administrator of ") + r.name + (ar ? ". ستتمكن من الدخول بعد الموافقة." : ". You can open it once they approve."), "ok"); }
    catch (x) { show(x.message); } });
  $("#conew").addEventListener("submit", async e => { e.preventDefault(); const v = Object.fromEntries(new FormData(e.target)); const btn = $("button", e.target); btn.disabled = true;
    try {
      if (v.mine) { await C.claim(v.name.trim()); return cloudEnter(await C.info()); }
      const c = await C.createCompany(v.name.trim()); open(c.id);
    } catch (x) { show(x.message); btn.disabled = false; } });
}

async function renderAuth() {
  if (STATIC && window.BeyondCloud && window.BeyondCloud.hasPendingAuth()) { store.set("cloudLocal", false); if (await cloudReturn()) return; }
  if (STATIC && window.BeyondCloud && window.BeyondCloud.configured() && !store.get("cloudLocal", false)) {
    // still signed in to the workspace (e.g. after switching company): carry on without asking again
    if (window.BeyondCloud.signedIn()) {
      applyLook(); $("#root").innerHTML = `<div class="login"><div class="empty">${t("loading")}</div><div id="lerr"></div></div>`;
      try { return await cloudEnter(await window.BeyondCloud.info()); } catch (e) { return renderCloudAuth(e.message, "err"); }
    }
    return renderCloudAuth();
  }
  applyLook();
  const setup = S.boot.needs_setup;
  if (setup && S.boot.workspace_preset && !store.get("separateBooks", false)) return renderFirstJoin();
  $("#root").innerHTML = `<div class="login"><h1>${esc(S.boot.settings.company_name || "Beyond")} ERP</h1>
    <p class="muted">${setup ? t("setup_title") : t("sign_in")}</p><div id="lerr"></div>
    <form id="lf" class="grid">${setup ? `<label class="f">${t("company_name")}<input name="company_name" value="${esc(S.boot.settings.company_name || "")}"></label>
      <label class="f">${t("full_name")}<input name="full_name"></label>` : ""}
      <label class="f">${t("username")}<input name="username" autocomplete="username" required></label>
      ${setup ? pwFields(true).replace(/minlength="6"/g, 'minlength="8"') : `<label class="f">${t("password")}<input name="password" type="password" autocomplete="current-password" required minlength="1"></label>
      <label class="f chk"><input type="checkbox" data-change="pwshow">${S.lang === "ar" ? "إظهار كلمة المرور" : "Show password"}</label>`}
      ${setup ? `<label class="f chk"><input type="checkbox" name="demo">${S.lang === "ar" ? "ابدأ بشركة تجريبية (بيانات وهمية لكل الشاشات — للتجربة والتدريب، ويمكن مسحها لاحقاً)" : "Start with a demo company (sample data on every screen — for testing and training; erase it any time)"}</label>` : ""}
      <button class="btn pri" type="submit">${setup ? t("save") : t("sign_in")}</button>
      <button class="btn" type="button" data-a="lang2">${t("language")}</button></form></div>`;
  S.H = { lang2: () => { store.set("lang", S.lang === "ar" ? "en" : "ar"); renderAuth(); } };
  pwWire($("#lf"));
  $("#lf").addEventListener("submit", async e => {
    e.preventDefault();
    if (setup) { S.H.pwmatch(); if (!e.target.reportValidity()) return; }
    const v = Object.fromEntries(new FormData(e.target));
    const sb = $("button[type=submit]", e.target);
    try {
      if (setup) v.language = S.lang;
      if (v.demo) { sb.disabled = true; sb.textContent = S.lang === "ar" ? "جارٍ تجهيز الشركة التجريبية…" : "Setting up the demo company…"; }
      await api("POST", setup ? "/api/setup" : "/api/login", v);
      await boot();
    } catch (err) { sb.disabled = false; sb.textContent = setup ? t("save") : t("sign_in"); $("#lerr").innerHTML = `<div class="banner err">${esc(err.message)}</div>`; }
  });
}

// New desktop install that belongs to a workspace: sign in with the same account as the website, get the shared books.
function renderFirstJoin() {
  const ar = S.lang === "ar";
  $("#root").innerHTML = `<div class="login auth"><h1 class="center">${esc(S.boot.settings.company_name || "Beyond")} ERP</h1>
    <p class="muted center">${ar ? "سجّل الدخول بنفس الحساب المستخدم على الموقع" : "Sign in with the same account you use on the website"}</p><div id="lerr"></div>
    <form id="lf" class="grid"><label class="f">${ar ? "البريد الإلكتروني أو اسم المستخدم" : "Email or username"}<input name="username" autocomplete="username" autocapitalize="none" required></label>
      ${pwFields(false)}<button class="btn pri" type="submit">${t("sign_in")}</button></form>
    <div class="row" style="justify-content:space-between;margin-top:14px;font-size:.8em"><a href="#" data-a="lang2">${ar ? "English" : "العربية"}</a>
      <a href="#" data-a="separate" class="muted">${ar ? "بدء دفاتر منفصلة على هذا الجهاز فقط" : "Start separate books on this computer only"}</a></div></div>`;
  S.H = { lang2: () => { store.set("lang", S.lang === "ar" ? "en" : "ar"); applyLook(); renderFirstJoin(); },
    separate: () => { store.set("separateBooks", true); renderAuth(); } };
  pwWire($("#lf"));
  $("#lf").addEventListener("submit", async e => {
    e.preventDefault(); const btn = $("button[type=submit]", e.target); btn.disabled = true;
    $("#lerr").innerHTML = `<div class="banner info">${ar ? "جارٍ تنزيل الدفاتر المشتركة…" : "Signing in and downloading the shared books…"}</div>`;
    try { await api("POST", "/api/cloud/first-join", Object.fromEntries(new FormData(e.target))); await boot(); }
    catch (err) { $("#lerr").innerHTML = `<div class="banner err">${esc(err.message)}</div>`; btn.disabled = false; }
  });
}

// ---------------------------------------------------------------- drawer
// Side panels: a header with a back arrow, the content, and Save / Cancel always visible at the bottom. On phones the
// system Back button closes the panel (it gets its own history entry) instead of leaving the page.
const BACK_ICON = `<svg class="flip" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>`;
let drawerEntry = null;
function drawer(title, html, onSubmit, opts = {}) {
  closeDrawer(true);
  if (!drawerEntry) { drawerEntry = location.hash; try { history.pushState({ berpDrawer: 1 }, ""); } catch { drawerEntry = null; } }
  const bg = document.createElement("div");
  bg.className = "drawer-bg";
  bg.innerHTML = `<div class="drawer${opts.wide ? " wide" : ""}" role="dialog" aria-label="${esc(title)}"><div class="dhead"><button type="button" class="btn icon" data-a="closeDrawer" aria-label="${esc(t("close"))}">${BACK_ICON}</button><h3>${esc(title)}</h3></div><div id="derr"></div>
    <form id="df">${html}<div class="foot">${opts.extraButtons || ""}<button class="btn" type="button" data-a="closeDrawer">${t(opts.readOnly ? "close" : "cancel")}</button>
    ${opts.readOnly ? "" : `<button class="btn pri" type="submit">${esc(opts.submitLabel || t("save"))}</button>`}</div></form></div>`;
  bg.addEventListener("mousedown", e => { if (e.target === bg) closeDrawer(); });
  document.body.appendChild(bg);
  const f = $("#df", bg);
  f.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = $("button[type=submit]", f); if (btn) btn.disabled = true;
    try { const done = await onSubmit(readForm(f), f); if (done !== false) { closeDrawer(); route(); } }
    catch (err) { $("#derr").innerHTML = `<div class="banner err">${esc(err.message)}</div>`; $("#derr").scrollIntoView({ block: "nearest", behavior: "smooth" }); }
    finally { if (btn) btn.disabled = false; }
  });
  // with a mouse, start typing at once; on a phone that would pop the keyboard over half the form
  const first = $("input:not([type=hidden]),select", f); if (first && window.matchMedia && matchMedia("(pointer: fine)").matches) first.focus();
  if (opts.onOpen) opts.onOpen(f);
  return f;
}
function closeDrawer(keepEntry) {
  $$(".drawer-bg").forEach(d => d.remove());
  if (keepEntry === true || drawerEntry === null) return;
  const was = drawerEntry; drawerEntry = null;
  // drop the panel's history entry — unless the page has moved on meanwhile
  setTimeout(() => { if (location.hash === was && history.state && history.state.berpDrawer) history.back(); }, 0);
}
window.addEventListener("popstate", () => { if (drawerEntry !== null) { drawerEntry = null; $$(".drawer-bg").forEach(d => d.remove()); } });
function readForm(f) {
  const v = {};
  $$("[name]", f).forEach(el => {
    if (el.dataset.skip) return;
    if (el.type === "checkbox") v[el.name] = el.checked;
    else v[el.name] = el.value;
  });
  return v;
}
// field: {k,l,type,opts:[[v,label]],v,span,req,list,step,ph,help,change,input}
function form(fields, cols = 2) {
  return `<div class="grid g${cols}">` + fields.map(f => {
    if (f.html) return `<div class="${f.span || ""}">${f.html}</div>`;
    const attrs = `name="${f.k}" ${f.req ? "required" : ""} ${f.ph ? `placeholder="${esc(f.ph)}"` : ""} ${f.change ? `data-change="${f.change}"` : ""} ${f.input ? `data-input="${f.input}"` : ""} ${f.dis ? "disabled" : ""}`;
    let ctl;
    if (f.type === "select") ctl = `<select ${attrs}>${(f.opts || []).map(o => { const [v, l] = Array.isArray(o) ? o : [o, tl(o)]; return `<option value="${esc(v)}" ${String(v) === String(f.v ?? "") ? "selected" : ""}>${esc(l)}</option>`; }).join("")}</select>`;
    else if (f.type === "textarea") ctl = `<textarea ${attrs} rows="3">${esc(f.v)}</textarea>`;
    else if (f.type === "checkbox") return `<label class="f chk ${f.span || ""}"><input type="checkbox" ${attrs} ${f.v ? "checked" : ""}>${esc(f.l)}</label>`;
    else ctl = `<input type="${f.type || "text"}" ${attrs} value="${esc(f.v ?? "")}" ${f.list ? `list="${f.list}"` : ""} ${f.type === "number" ? `step="${f.step || "any"}" inputmode="decimal" class="num"` : ""}>`;
    return `<label class="f ${f.span || ""}">${esc(f.l)}${ctl}${f.help ? `<span class="muted" style="font-weight:400">${esc(f.help)}</span>` : ""}</label>`;
  }).join("") + `</div>`;
}
const datalist = (id, values) => `<datalist id="${id}">${[...new Set(values)].map(v => `<option value="${esc(v)}">`).join("")}</datalist>`;
// ---------------------------------------------------------- contacts directory helpers
const CONTACT_CATS = { supplier: ["Supplier", "مورد"], subcontractor: ["Subcontractor", "مقاول باطن"], driver: ["Driver", "سائق"],
  transport: ["Transport company", "شركة نقل"], technician: ["Technician", "فني"], consultant: ["Consultant", "استشاري"],
  customer: ["Customer", "عميل"], employee: ["Employee", "موظف"], other: ["Other", "أخرى"] };
const catLabel = c => c ? (CONTACT_CATS[c] ? L2(...CONTACT_CATS[c]) : c.charAt(0).toUpperCase() + c.slice(1)) : "";
const contactSub = p => [catLabel(p.category) || (p.type && p.type !== "other" ? tl(p.type) : ""), p.specialty, p.phone || p.whatsapp].filter(Boolean).join(" · ");
// a name list for an input: saved contacts first (with their category and phone), then other names seen in the books
const partyList = (id, extra = []) => { const dir = S.boot.parties || [], known = new Set(dir.map(p => p.name));
  return `<datalist id="${id}" data-combo="1">${dir.map(p => `<option value="${esc(p.name)}" label="${esc(contactSub(p))}">`).join("")}${[...new Set(extra)].filter(n => n && !known.has(n)).map(n => `<option value="${esc(n)}" data-hist="1">`).join("")}</datalist>`; };
const waNumber = n => { let d = String(n || "").replace(/[^\d+]/g, "").replace(/^\+/, "").replace(/^00/, ""); if (/^0\d{10}$/.test(d)) d = "20" + d.slice(1); return d; };
// Searchable drop-down for name inputs whose <datalist> is marked data-combo. Typing a new name is always allowed.
const combo = { inp: null, box: null, items: [], i: -1 };
function comboClose() { if (combo.box) combo.box.remove(); combo.box = null; combo.inp = null; combo.i = -1; }
function comboPlace() { if (!combo.box || !combo.inp) return; const r = combo.inp.getBoundingClientRect(), below = innerHeight - r.bottom;
  const up = below < 220 && r.top > below; Object.assign(combo.box.style, { left: Math.max(4, r.left) + "px", width: Math.min(innerWidth - 8, Math.max(r.width, 240)) + "px",
    top: up ? "" : r.bottom + 2 + "px", bottom: up ? innerHeight - r.top + 2 + "px" : "", maxHeight: Math.min(320, (up ? r.top : below) - 12) + "px" }); }
function comboRender() { const inp = combo.inp; if (!inp) return; const all = combo.all, qv = inp.value.trim().toLowerCase(), words = qv.split(/\s+/).filter(Boolean);
  combo.items = all.filter(o => words.every(w => (o.v + " " + o.sub).toLowerCase().includes(w))).slice(0, 80); combo.i = -1;
  const exact = all.some(o => o.v.toLowerCase() === qv);
  if (!combo.items.length && !qv) { comboClose(); return; }
  if (!combo.box) { combo.box = document.createElement("div"); combo.box.className = "combo"; combo.box.addEventListener("mousedown", e => e.preventDefault());
    combo.box.addEventListener("click", e => { const it = e.target.closest("[data-ci]"); if (it) comboPick(+it.dataset.ci); else if (e.target.closest(".cnew")) comboClose(); }); document.body.appendChild(combo.box); }
  combo.box.innerHTML = (qv && !exact ? `<div class="cnew">＋ ${esc(L2(`“${inp.value.trim()}” — new name (allowed)`, `«${inp.value.trim()}» — اسم جديد (مسموح)`))}</div>` : "") +
    combo.items.map((o, i) => `<div class="citem${o.hist ? " hist" : ""}" data-ci="${i}"><b>${esc(o.v)}</b>${o.sub ? `<span>${esc(o.sub)}</span>` : o.hist ? `<span>${esc(L2("used before · not in contacts", "مستخدم سابقاً · غير محفوظ"))}</span>` : ""}</div>`).join("");
  comboPlace(); }
function comboPick(i) { const o = combo.items[i], inp = combo.inp; if (!o || !inp) return; inp.value = o.v; comboClose(); combo.quiet = true;
  try { inp.dispatchEvent(new Event("input", { bubbles: true })); inp.dispatchEvent(new Event("change", { bubbles: true })); } finally { combo.quiet = false; } }
document.addEventListener("focusin", e => { const inp = e.target; if (!(inp instanceof HTMLInputElement)) return;
  const id = inp.getAttribute("list") || inp.dataset.combo; if (!id) return;
  const scope = inp.closest(".drawer, form, #view, main") || document;
  const dl = scope.querySelector(`datalist[id="${id}"]`) || document.getElementById(id); if (!dl || !dl.hasAttribute("data-combo")) return;
  inp.dataset.combo = id; inp.removeAttribute("list"); inp.setAttribute("autocomplete", "off");
  combo.all = [...dl.options].map(o => ({ v: o.value, sub: o.label && o.label !== o.value ? o.label : "", hist: !!o.dataset.hist }));
  if (combo.inp !== inp) comboClose(); combo.inp = inp; comboRender(); });
document.addEventListener("focusout", e => { if (e.target === combo.inp) setTimeout(() => { if (document.activeElement !== combo.inp) comboClose(); }, 120); });
document.addEventListener("input", e => { if (combo.quiet) return; if (e.target === combo.inp) comboRender(); else if (e.target.dataset && e.target.dataset.combo && document.activeElement === e.target) e.target.dispatchEvent(new FocusEvent("focusin", { bubbles: true })); });
document.addEventListener("keydown", e => { if (!combo.box || e.target !== combo.inp) return; const n = combo.items.length;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (!n) return; combo.i = (combo.i + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
    $$(".citem", combo.box).forEach((x, i) => x.classList.toggle("on", i === combo.i)); const on = $(".citem.on", combo.box); if (on) on.scrollIntoView({ block: "nearest" }); }
  else if (e.key === "Enter" && combo.i >= 0) { e.preventDefault(); comboPick(combo.i); }
  else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); comboClose(); } }, true);
window.addEventListener("scroll", () => comboPlace(), true); window.addEventListener("resize", () => comboPlace());
// under a name field: offer to save a typed new name as a contact (ticked → saved after the document posts)
function offerContact(f, field, cat) { const el = $(`[name=${field}]`, f); if (!el || !(can("purchasing.contacts", "add") || can("sales.parties", "add"))) return () => null;
  const box = document.createElement("div"); box.className = "newc"; box.hidden = true;
  box.innerHTML = `<label class="f chk" style="margin:0"><input type="checkbox" data-skip="1" class="nc-on">${esc(L2("Save this new name to contacts as", "احفظ هذا الاسم الجديد في جهات الاتصال كـ"))}</label>
    <select data-skip="1" class="nc-cat">${Object.keys(CONTACT_CATS).map(c => `<option value="${c}" ${c === cat ? "selected" : ""}>${esc(catLabel(c))}</option>`).join("")}</select>
    <input data-skip="1" class="nc-phone" placeholder="${esc(L2("Phone (optional)", "الهاتف (اختياري)"))}" inputmode="tel" dir="ltr">`;
  el.closest("label").after(box);
  const sync = () => { const v = el.value.trim().toLowerCase(); box.hidden = !v || (S.boot.parties || []).some(p => p.name.toLowerCase() === v); };
  el.addEventListener("input", () => { if (!el.value.trim() || document.activeElement === el) box.hidden = true; }); el.addEventListener("change", sync); sync();
  $(".nc-phone", box).addEventListener("input", () => { $(".nc-on", box).checked = true; });
  return () => box.hidden || !$(".nc-on", box).checked ? null : { name: el.value.trim(), category: $(".nc-cat", box).value, phone: $(".nc-phone", box).value.trim() }; }
async function saveNewContacts(getters) { let n = 0;
  for (const g of getters) { const c = g && g(); if (!c) continue; try { await api("POST", "/api/parties-master", c); n++; } catch (e) { toast(e.message); } }
  if (n) { await refreshBoot(); toast(L2("Saved to contacts", "حُفظ في جهات الاتصال")); } }

// ------------------------------------------------------------ option lists
const aname = a => (S.lang === "ar" && a.name_ar ? a.name_ar : a.name);
const accts = (types, subtypes) => S.boot.accounts.filter(a => a.active && (!types || types.includes(a.type)) && (!subtypes || subtypes.includes(a.subtype)))
  .map(a => [a.code, `${a.code} — ${aname(a)}`]);
const accByRole = r => (S.boot.accounts.find(a => a.role === r) || {}).code || "";
const buOpts = (blank) => [...(blank ? [["", blank]] : []), ...S.boot.bus.map(b => [b.id, `${b.code} — ${b.name}`])];
const whOpts = () => S.boot.warehouses.filter(w => w.active).map(w => [w.id, w.name]);
const methodOpts = () => S.boot.enums.methods;
const unitsList = () => datalist("units", S.boot.settings.units || []);
const taxOpts = kind => [["", t("no_tax")], ...(S.boot.tax_codes || []).filter(x => x.kind === kind && x.active).map(x => [x.code, `${x.code} — ${S.lang === "ar" && x.name_ar ? x.name_ar : x.name}`])];
const partyDefaults = (f, field) => { const el = $(`[name=${field}]`, f); if (!el) return;
  el.addEventListener("change", () => { const p = (S.boot.parties || []).find(x => x.name === el.value); if (!p) return;
    const v = $("[name=vat_code]", f), w = $("[name=wht_code]", f);
    if (v && p.default_vat && !v.value) v.value = p.default_vat; if (w && p.default_wht && !w.value) w.value = p.default_wht; if (S.H.calc) S.H.calc(); }); };
const rateOf = code => ((S.boot.tax_codes || []).find(x => x.code === code) || { rate: 0 }).rate;

// ---------------------------------------------------------- filter bar
// kinds: dates, asof, bu, q, status, kind:[...], account, party, warehouse, type:[...], compare, detail, allocate, lang
function filterBar(q, fields, extra = "") {
  const parts = [];
  for (const f of fields) {
    if (f === "dates") parts.push(`<label class="f">${t("from")}<input type="date" name="date_from" value="${esc(q.date_from || "")}"></label>`,
      `<label class="f">${t("to")}<input type="date" name="date_to" value="${esc(q.date_to || "")}"></label>`);
    else if (f === "asof") parts.push(`<label class="f">${t("as_of")}<input type="date" name="date_to" value="${esc(q.date_to || today())}"></label>`);
    else if (f === "bu") parts.push(`<label class="f">${t("bu")}<select name="bu_id"><option value="">${t("all_bu")}</option><option value="none" ${q.bu_id === "none" ? "selected" : ""}>${t("shared")}</option>${S.boot.bus.map(b => `<option value="${b.id}" ${String(q.bu_id) === String(b.id) ? "selected" : ""}>${esc(b.code)} — ${esc(b.name)}</option>`).join("")}</select></label>`);
    else if (f === "qv") parts.push(`<label class="f">${t("search").replace("…", "")}<input name="q" value="${esc(q.q || "")}" placeholder="${t("search_v")}" title="${esc(t("search_v"))}"></label>`);
    else if (f === "q") parts.push(`<label class="f">${t("search").replace("…", "")}<input name="q" value="${esc(q.q || "")}" placeholder="${t("search")}"></label>`);
    else if (f === "status") parts.push(`<label class="f">${t("status")}<select name="status"><option value="">${t("all")}</option><option value="posted" ${q.status === "posted" ? "selected" : ""}>${t("invoiced")}</option><option value="void" ${q.status === "void" ? "selected" : ""}>${t("void")}</option></select></label>`);
    else if (Array.isArray(f) && f[0] === "kind") parts.push(`<label class="f">${t("kind")}<select name="kind"><option value="">${t("all")}</option>${f[1].map(k => `<option value="${esc(k)}" ${q.kind === k ? "selected" : ""}>${esc(tl(k))}</option>`).join("")}</select></label>`);
    else if (Array.isArray(f) && f[0] === "select") parts.push(`<label class="f">${esc(f[2])}<select name="${f[1]}"><option value="">${t("all")}</option>${f[3].map(([v, l]) => `<option value="${esc(v)}" ${String(q[f[1]]) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`);
    else if (f === "account") parts.push(`<label class="f">${t("account")}<select name="account"><option value="">${t("all")}</option>${["1", "2", "3", "4", "5", "6", "7"].map(x => `<option value="${x}" ${q.account === x ? "selected" : ""}>${x}000s</option>`).join("")}${S.boot.accounts.map(a => `<option value="${a.code}" ${q.account === a.code ? "selected" : ""}>${esc(a.code)} ${esc(aname(a))}</option>`).join("")}</select></label>`);
    else if (f === "party") parts.push(`<label class="f">${t("party")}<input name="party" value="${esc(q.party || "")}"></label>`);
    else if (f === "warehouse") parts.push(`<label class="f">${t("warehouse")}<select name="warehouse_id"><option value="">${t("all")}</option>${whOpts().map(([v, l]) => `<option value="${v}" ${String(q.warehouse_id) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`);
    else if (f === "compare") parts.push(`<label class="f">${t("compare")}<select name="compare">${["none", "month", "quarter", "year"].map(c => `<option value="${c}" ${(q.compare || "none") === c ? "selected" : ""}>${t("c_" + c)}</option>`).join("")}</select></label>`);
    else if (f === "detail") parts.push(`<label class="f chk"><input type="checkbox" name="detail" ${q.detail !== "0" ? "checked" : ""}>${t("detail")}</label>`);
    else if (f === "allocate") parts.push(`<label class="f chk"><input type="checkbox" name="allocate" ${q.allocate !== "0" ? "checked" : ""}>${t("allocate")}</label>`);
    else if (f === "lang") parts.push(`<label class="f">${t("report_lang")}<select name="lang"><option value="en">English</option><option value="ar" ${(q.lang || S.lang) === "ar" ? "selected" : ""}>العربية</option></select></label>`);
  }
  S.H.fapply = () => {
    const box = $("#fbar"); const v = {};
    $$("[name]", box).forEach(el => { v[el.name] = el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value; });
    setQ(v);
  };
  S.H.fclear = () => setQ({});
  S.H.ftoggle = () => $("#fbar").classList.toggle("open");
  const active = Object.entries(q).filter(([k, v]) => v && !["limit", "offset", "tab", "lang"].includes(k)).length;
  return `<div class="card ${active ? "" : ""}" id="fbar"><button type="button" class="btn sm ftoggle" data-a="ftoggle">⚲ ${t("filters")}${active ? ` <span class="chip">${active}</span>` : ""}</button><div class="filters">${parts.join("")}
    <div class="row"><button class="btn pri" data-a="fapply">${t("apply")}</button><button class="btn" data-a="fclear">${t("clear")}</button></div></div>${extra}</div>`;
}
function exportMenu(name, params) {
  const base = `/api/reports/${name}?` + qs({ ...params, lang: params.lang || S.lang });
  return `<div class="dd"><button class="btn">${t("export")} ▾</button><div class="dd-menu">
    <a href="${base}&format=pdf" target="_blank" rel="noopener">PDF (print)</a><a href="${base}&format=xlsx">Excel (.xlsx)</a>
    <a href="${base}&format=docx">Word (.docx)</a><a href="${base}&format=csv">CSV</a></div></div>`;
}
// Client documents (company layout, logo & watermark per Settings → Documents & branding)
function docMenu(base, formats, extra = "") {
  return `<div class="dd"><button class="btn">⬇ ${t("documents")} ▾</button><div class="dd-menu">${formats.map(([f, l]) =>
    `<a href="${base}?format=${f}" ${f === "pdf" ? 'target="_blank" rel="noopener"' : ""}>${t(l)}</a>`).join("")}${extra}</div></div>`;
}
// Delete after a clear confirmation; the server refuses anything still in use and says why.
async function confirmDelete(what, path, after) {
  if (!confirm((S.lang === "ar" ? "حذف " : "Delete ") + what + (S.lang === "ar" ? "؟ لا يمكن التراجع." : "? This cannot be undone."))) return false;
  try { await api("DELETE", path); if (after) await after(); closeDrawer(); toast(S.lang === "ar" ? "تم الحذف" : "Deleted"); route(); return true; }
  catch (e) { fail(e); return false; }
}
function pager(total, q) {
  const limit = Number(q.limit || 50), offset = Number(q.offset || 0);
  if (total <= limit && !offset) return `<div class="pager">${total} ${t("of")} ${total}</div>`;
  S.H.pg = el => setQ({ ...q, offset: Math.max(0, offset + Number(el.dataset.d) * limit) });
  return `<div class="pager">${t("showing")} ${offset + 1}–${Math.min(offset + limit, total)} ${t("of")} ${total}
    <button class="btn sm" data-a="pg" data-d="-1" ${offset ? "" : "disabled"}>${t("prev")}</button>
    <button class="btn sm" data-a="pg" data-d="1" ${offset + limit < total ? "" : "disabled"}>${t("next")}</button></div>`;
}

// ------------------------------------------------------------- tables
function reportTable(tb) {
  const fmtCell = (v, col, row) => {
    if (v === "" || v == null) return "";
    if (typeof v === "number") {
      if (col.fmt === "pct" || (row.style === "note" && Math.abs(v) <= 10)) return pct(v);
      if (col.fmt === "num") return numf(v);
      return money(v);
    }
    return esc(v);
  };
  return `<div class="tbl"><table><thead><tr>${tb.columns.map(c => `<th class="${c.align === "right" ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr></thead>
    <tbody>${tb.rows.map(r => `<tr class="${r.style}">${r.cells.map((v, i) => `<td class="${tb.columns[i].align === "right" ? "num" : ""} ${typeof v === "number" && v < 0 ? "neg" : ""}" ${i === 0 && r.indent ? `style="padding-inline-start:${10 + 16 * r.indent}px"` : ""}>${fmtCell(v, tb.columns[i], r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function voucherRows(rows) {
  if (!rows.length) return `<div class="empty">${t("nothing")}</div>`;
  return `<div class="tbl stack"><table><thead><tr><th>${t("date")}</th><th>#</th><th>${t("kind")}</th><th>${t("description")}</th><th>${t("party")}</th><th>${t("bu")}</th><th class="num">${t("amount")}</th><th></th></tr></thead><tbody>
    ${rows.map(e => `<tr class="click ${e.status === "void" || e.reversed_by ? "void" : ""}" data-a="openv" data-id="${e.id}"><td>${fd(e.date)}</td><td>${e.id}</td><td><span class="chip">${esc(tl(e.kind))}</span></td>
      <td>${esc(e.memo)}<div class="lines">${e.lines.map(l => `${l.debit ? t("debit") : "&nbsp;&nbsp;" + t("credit")} ${esc(l.account)} ${esc(aname(l))} ${money(l.debit || l.credit)}`).join("<br>")}</div></td>
      <td>${esc(e.party)}</td><td>${esc(e.bu_code || "")}</td><td class="num">${money(e.amount)}</td>
      <td>${e.status === "void" ? `<span class="chip bad">${t("void")}</span>` : ""}${e.reversed_by ? `<span class="chip warn">${t("reverse")}</span>` : ""}${e.history && e.history.length ? `<span class="chip">${t("edited")}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
}
async function openVoucher(id) {
  const e = await api("GET", "/api/journal/" + id).catch(() => null) || (S.cache.v && S.cache.v[id]);
  if (!e) return;
  const live = e.status === "posted" && !e.reversed_by && !e.reversal_of;
  const DOC_MOD = { direct_cost: "purchasing.costs", expense: "purchasing.expenses", payment: "purchasing.payments", supplier_discount: "purchasing.payments", payroll: "payroll", receipt: "sales.receipts", partner_tx: "partners.transactions" };
  const edit = can("accounting.journal", "edit") || (e.doc && can(DOC_MOD[e.doc], "edit"));
  const html = `<div class="row" style="margin-bottom:10px"><span class="chip">${esc(tl(e.kind))}</span><span class="chip">#${e.id}</span><span class="chip">${fd(e.date)}</span>${e.bu_code ? `<span class="chip">${esc(e.bu_code)}</span>` : ""}${e.created_by ? `<span class="chip">${t("created_by")} ${esc(e.created_by)}</span>` : ""}</div>
    <p><b>${esc(e.memo)}</b>${e.party ? ` — ${esc(e.party)}` : ""}${e.ref ? ` · ${esc(e.ref)}` : ""}</p>
    ${e.meta && (e.meta.loan || e.meta.custody) ? `<p><a class="btn sm" href="#/${e.meta.loan ? "loan" : "cust"}/${encodeURIComponent(e.meta.loan || e.meta.custody)}" data-a="vgo">${e.meta.loan ? L2("Open the loan", "فتح القرض") : L2("Open the custody", "فتح العهدة")} ${esc(e.meta.loan || e.meta.custody)} →</a></p>` : ""}
    ${e.meta && e.meta.savings ? `<p class="muted">${t("savings")}: <b>${money(e.meta.savings)}</b>${e.meta.quoted ? ` ${L2("of", "من")} ${money(e.meta.quoted)}` : ""} — ${e.meta.negotiation === "keep" ? L2("company earnings", "أرباح للشركة") : L2("lowered the cost", "خفّض التكلفة")}${e.meta.deal_status ? ` · ${esc(tl(e.meta.deal_status))}` : ""}</p>` : ""}
    <div class="tbl"><table><thead><tr><th>${t("account")}</th><th class="num">${t("debit")}</th><th class="num">${t("credit")}</th></tr></thead><tbody>
    ${e.lines.map(l => `<tr><td>${esc(l.account)} ${esc(aname(l))}</td><td class="num">${l.debit ? money(l.debit) : ""}</td><td class="num">${l.credit ? money(l.credit) : ""}</td></tr>`).join("")}
    <tr class="total"><td>${t("total")}</td><td class="num">${money(e.amount)}</td><td class="num">${money(e.amount)}</td></tr></tbody></table></div>
    ${e.meta && e.meta.void_reason ? `<div class="banner err" style="margin-top:10px">${t("void")}: ${esc(e.meta.void_reason)}</div>` : ""}
    ${e.history && e.history.length ? `<h4>${t("history")}</h4>${e.history.map(h => `<div class="lines">${esc(h.edited_at)} ${esc(h.edited_by || "")}: ${esc(h.memo)} — ${h.lines.map(l => `${l.account} ${l.debit || ""}/${l.credit || ""}`).join(", ")}</div>`).join("")}` : ""}
    <p class="muted" style="font-size:.8em;margin-top:12px">${S.lang === "ar" ? "الإلغاء يستبعد القيد من كل الأرصدة مع إبقاء أثره؛ القيد العكسي هو التصحيح المحاسبي الرسمي." : "Void removes the entry from every total but keeps a visible trace. Reverse posts the formal correcting entry — both stay on record."}</p>`;
  const btns = [];
  if (edit && live) {
    btns.push(`<button class="btn danger" type="button" data-a="vvoid" data-id="${e.id}">${t("void")}</button>`, `<button class="btn" type="button" data-a="vrev" data-id="${e.id}">${t("reverse")}</button>`);
    if (e.doc) btns.push(`<button class="btn pri" type="button" data-a="vform">${t("edit")}</button>`);
    if (!["Goods Receipt", "Material Issue", "Stock Transfer", "Stock Adjustment", "Sales Invoice"].includes(e.kind) && can("accounting.journal", "edit"))
      btns.push(`<button class="btn" type="button" data-a="vedit" data-id="${e.id}">${e.doc ? L2("Edit lines", "تعديل البنود") : t("edit")}</button>`);
  }
  const DUP_MOD = e.doc ? DOC_MOD[e.doc] : "accounting.journal";
  if (e.doc ? can(DUP_MOD, "add") : (can("accounting.journal", "add") && !["Goods Receipt", "Material Issue", "Stock Transfer", "Stock Adjustment", "Sales Invoice"].includes(e.kind)))
    btns.push(`<button class="btn" type="button" data-a="vdup">${t("duplicate")}</button>`);
  const rcOk = can("accounting.recurring", "add") && (e.doc ? !["supplier_discount"].includes(e.doc) : e.kind === "Journal Voucher");
  if (rcOk && live) btns.push(`<button class="btn" type="button" data-a="vrc">↻ ${L2("Make recurring", "اجعله متكرراً")}</button>`);
  S.H.vrc = () => recurringForm(null, e.doc ? { doc_type: e.doc, payload: { ...e.form }, name: e.memo, start: e.date }
    : { doc_type: "journal", name: e.memo, start: e.date, payload: { memo: e.memo, party: e.party, bu_id: e.bu_id || "", lines: e.lines.map(l => ({ account: l.account, debit: l.debit || "", credit: l.credit || "" })) } });
  if (edit && e.status === "void") btns.push(`<button class="btn danger" type="button" data-a="vdel" data-id="${e.id}">${t("delete")}</button>`);
  S.H.vvoid = async () => { const r = prompt(t("void_reason")); if (r === null) return; try { await api("POST", `/api/journal/${e.id}/void`, { reason: r }); closeDrawer(); toast(t("saved")); route(); } catch (x) { fail(x); } };
  S.H.vrev = async () => { try { await api("POST", `/api/journal/${e.id}/reverse`, {}); closeDrawer(); toast(t("posted")); route(); } catch (x) { fail(x); } };
  S.H.vdel = async () => { if (!confirm(t("confirm_delete"))) return; try { await api("DELETE", `/api/journal/${e.id}`); closeDrawer(); route(); } catch (x) { fail(x); } };
  S.H.vedit = () => journalForm(e);
  S.H.vform = () => openDoc(e.doc, null, e);
  S.H.vgo = el => { const h = el.getAttribute("href"); closeDrawer(); location.hash = h; };
  S.H.vdup = () => e.doc ? openDoc(e.doc, null, null, { ...e.form, date: today() }) : journalForm({ ...e, date: today() }, true);
  drawer(`${tl(e.kind)} #${e.id}`, html, null, { readOnly: true, extraButtons: btns.join("") });
}
S.GLOBAL_H.openv = el => openVoucher(el.dataset.id);

// ======================================================= document forms
function paidSplitHelp() { return S.lang === "ar" ? "الجزء غير المدفوع يُسجّل تلقائياً على حساب الدائنين باسم المورد." : "Anything not paid now is booked to payables under the supplier's name."; }
const DOCS = {
  direct_cost: { title: "m_costs", fields: () => [
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "bu_id", l: t("bu"), type: "select", opts: buOpts(), req: 1 },
    { k: "account", l: t("cost_account"), type: "select", opts: accts(["COGS"]), v: accByRole("materials") },
    { k: "description", l: t("description"), req: 1 }, { k: "vendor", l: t("supplier"), list: "parties" },
    { k: "deal_ref", l: t("deal_ref") }, { k: "deal_status", l: t("deal_status"), type: "select", opts: [["", "—"], "Open", "Settled"] },
    { k: "qty", l: t("qty"), type: "number", v: 1, input: "calc" }, { k: "unit", l: t("unit"), list: "units", v: "Task" },
    { k: "rate", l: t("unit_cost"), type: "number", input: "calc", req: 1 }, { k: "savings", l: t("savings"), type: "number", input: "calc" },
    { k: "negotiation", l: L2("Negotiation discount goes to", "خصم التفاوض يذهب إلى"), type: "select", opts: NEGO_OPTS(), change: "calc" },
    { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat"), change: "calc" }, { k: "wht_code", l: t("wht_code"), type: "select", opts: taxOpts("wht"), change: "calc" },
    { k: "paid", l: t("paid_now"), type: "number", input: "calc", help: paidSplitHelp() }, { k: "method", l: t("method"), type: "select", opts: methodOpts() },
    { html: `<div class="banner info" id="calc"></div>`, span: "spanall" }],
    calc: f => { const g = n => Number($(`[name=${n}]`, f).value) || 0; const quoted = g("qty") * g("rate"), tot = quoted - g("savings");
      const vat = Math.round(tot * rateOf($("[name=vat_code]", f).value) * 100) / 100, wht = Math.round(tot * rateOf($("[name=wht_code]", f).value) * 100) / 100;
      const keep = $("[name=negotiation]", f).value === "keep" && g("savings") > 0;
      $("[name=negotiation]", f).closest("label").style.opacity = g("savings") > 0 ? "" : ".55";
      $("#calc", f).innerHTML = `${t("net_cost")}: <b>${money(tot)}</b> · ${t("vat_amt")}: ${money(vat)} · ${t("wht_amt")}: (${money(wht)}) · ${t("unpaid")}: <b>${money(tot + vat - wht - g("paid"))}</b>` +
        (keep ? `<br>${L2("Booked as cost (client billed on)", "تُسجّل كتكلفة (ويُحاسب العميل عليها)")}: <b>${money(quoted)}</b> · ${L2("Negotiation earnings", "أرباح التفاوض")}: <b class="pos">${money(g("savings"))}</b>`
          : g("savings") > 0 ? `<br>${L2("The discount lowers the project cost to", "الخصم يخفّض تكلفة المشروع إلى")} <b>${money(tot)}</b>` : ""); },
    open: (f, edit) => { partyDefaults(f, "vendor"); const bu = $("[name=bu_id]", f), ng = $("[name=negotiation]", f);
      if (!edit) ng.value = buNego(bu.value);
      bu.addEventListener("change", () => { ng.value = buNego(bu.value); S.H.calc && S.H.calc(); }); } },
  supplier_discount: { title: "Supplier Discount", fields: () => [
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "party", l: t("supplier"), list: "parties", req: 1 },
    { k: "against", l: t("pay_against"), type: "select", opts: accts(["Liability"]), v: accByRole("ap") }, { k: "amount", l: L2("Discount amount", "مبلغ الخصم"), type: "number", req: 1 },
    { k: "credit_account", l: L2("Credit account", "الحساب الدائن"), type: "select", opts: accts(["COGS", "Expense", "Revenue"]) },
    { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—") }, { k: "deal_ref", l: t("deal_ref") }, { k: "memo", l: t("memo"), span: "spanall" }] },
  expense: { title: "m_expenses", fields: () => [
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "bu_id", l: t("bu"), type: "select", opts: buOpts(t("shared")), help: t("overhead_note") },
    { k: "account", l: t("expense_account"), type: "select", opts: accts(["Expense"]), v: accByRole("other_overhead") },
    { k: "description", l: t("description"), req: 1 }, { k: "vendor", l: t("supplier"), list: "parties" },
    { k: "amount", l: t("amount"), type: "number", req: 1, input: "calc" }, { k: "paid", l: t("paid_now"), type: "number", ph: S.lang === "ar" ? "فارغ = مدفوع بالكامل" : "blank = paid in full", help: paidSplitHelp(), input: "calc" },
    { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat"), change: "calc" }, { k: "wht_code", l: t("wht_code"), type: "select", opts: taxOpts("wht"), change: "calc" },
    { k: "method", l: t("method"), type: "select", opts: methodOpts() }, { html: `<div class="banner info" id="calc"></div>`, span: "spanall" }],
    calc: f => { const g = n => Number($(`[name=${n}]`, f).value) || 0; const a = g("amount");
      const vat = Math.round(a * rateOf($("[name=vat_code]", f).value) * 100) / 100, wht = Math.round(a * rateOf($("[name=wht_code]", f).value) * 100) / 100;
      $("#calc", f).innerHTML = `${t("vat_amt")}: ${money(vat)} · ${t("wht_amt")}: (${money(wht)}) · ${S.lang === "ar" ? "المستحق للمورد" : "Due to supplier"}: <b>${money(a + vat - wht)}</b>`; },
    open: f => partyDefaults(f, "vendor") },
  payroll: { title: "m_payroll", fields: () => [
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "cost_type", l: t("cost_type"), type: "select", opts: S.boot.enums.payroll_types },
    { k: "bu_id", l: t("bu"), type: "select", opts: buOpts(t("shared")) }, { k: "worker", l: t("worker"), list: "parties", req: 1 },
    { k: "role", l: t("position") }, { k: "period", l: t("period"), v: today().slice(0, 7) },
    { k: "basis", l: t("rate_basis"), type: "select", opts: ["Daily", "Monthly", "Lump Sum", "Hourly"] },
    { k: "qty", l: t("days"), type: "number", v: 1, input: "calc" }, { k: "rate", l: t("rate"), type: "number", req: 1, input: "calc" },
    { k: "paid", l: t("paid_now"), type: "number", input: "calc" }, { k: "method", l: t("method"), type: "select", opts: methodOpts() },
    { html: `<div class="banner info" id="calc"></div>`, span: "spanall" }],
    calc: f => { const g = n => Number($(`[name=${n}]`, f).value) || 0; $("#calc", f).innerHTML = `${t("gross_pay")}: <b>${money(g("qty") * g("rate"))}</b> · ${t("unpaid")}: <b>${money(g("qty") * g("rate") - g("paid"))}</b>`; } },
  payment: { title: "m_payments", fields: pre => [
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "against", l: t("pay_against"), type: "select", opts: accts(["Liability"]), v: (pre && pre.against) || accByRole("ap") },
    { k: "party", l: t("payee"), list: "parties", req: 1, v: pre && pre.party }, { k: "amount", l: t("amount"), type: "number", req: 1, v: pre && pre.amount },
    { k: "method", l: t("method"), type: "select", opts: methodOpts() }, { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—") },
    { k: "ref", l: t("ref") }, { k: "memo", l: t("memo") }, { k: "allow_overpay", l: t("allow_over"), type: "checkbox", span: "spanall" }] },
  receipt: { title: "m_receipts", fields: () => [
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "bu_id", l: t("bu"), type: "select", opts: buOpts(), req: 1 },
    { k: "amount", l: t("amount"), type: "number", req: 1 }, { k: "method", l: t("method"), type: "select", opts: methodOpts() },
    { k: "credit_account", l: t("credit_account"), type: "select", opts: [[accByRole("revenue"), t("cr_revenue")], [accByRole("ar"), t("cr_ar")], [accByRole("client_advances"), t("cr_adv")], ...accts(["Revenue", "Liability"]).filter(a => ![accByRole("revenue"), accByRole("client_advances")].includes(a[0]))] },
    { k: "party", l: t("received_from"), list: "parties" }, { k: "withheld", l: t("withheld"), type: "number" },
    { k: "vat_code", l: t("vat_incl"), type: "select", opts: taxOpts("vat"), help: S.lang === "ar" ? "فقط للإيراد النقدي المباشر؛ ضريبة الفواتير تُحسب في الفاتورة" : "Only for cash revenue booked directly; invoiced VAT is charged on the invoice" },
    { k: "ref", l: t("ref") }, { k: "memo", l: t("memo") }] },
  partner_tx: { title: "m_partners", fields: () => [
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "partner_id", l: t("partner"), type: "select", opts: S.boot.partners.filter(p => p.active !== 0).map(p => [p.id, p.name]), req: 1 },
    { k: "type", l: t("type"), type: "select", opts: [["Distribution", t("drawing")], ["Capital", t("capital")]] },
    { k: "amount", l: t("amount"), type: "number", req: 1 }, { k: "method", l: t("method"), type: "select", opts: methodOpts() },
    { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—") }, { k: "memo", l: t("memo"), span: "spanall" }] },
  stock_move: { title: "m_moves", fields: pre => [
    { k: "type", l: t("type"), type: "select", opts: S.boot.enums.stock_types, v: (pre && pre.type) || "Receipt", change: "stype" },
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "warehouse_id", l: t("warehouse"), type: "select", opts: whOpts(), v: pre && pre.warehouse_id },
    { k: "to_warehouse_id", l: t("to_warehouse"), type: "select", opts: whOpts() },
    { k: "item", l: t("item"), list: "items", req: 1, v: pre && pre.item }, { k: "unit", l: t("unit"), list: "units" },
    { k: "qty", l: t("qty"), type: "number" }, { k: "counted", l: t("counted"), type: "number" },
    { k: "rate", l: t("unit_cost"), type: "number" }, { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—") },
    { k: "account", l: t("cost_account"), type: "select", opts: accts(["COGS", "Expense"]), v: accByRole("materials") },
    { k: "vendor", l: t("supplier"), list: "parties" }, { k: "paid", l: t("paid_now"), type: "number" },
    { k: "method", l: t("method"), type: "select", opts: methodOpts() }, { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat") },
    { k: "wht_code", l: t("wht_code"), type: "select", opts: taxOpts("wht") }, { k: "note", l: t("notes") }],
    open: f => { partyDefaults(f, "vendor"); const sync = () => { const ty = $("[name=type]", f).value; const show = { Receipt: ["qty", "rate", "vendor", "paid", "method", "unit", "vat_code", "wht_code"], Issue: ["qty", "bu_id", "account"], Transfer: ["qty", "to_warehouse_id"], Adjustment: ["counted", "rate"] }[ty];
      ["to_warehouse_id", "qty", "counted", "rate", "bu_id", "account", "vendor", "paid", "method", "unit", "vat_code", "wht_code"].forEach(n => { const el = $(`[name=${n}]`, f).closest("label"); el.style.display = show.includes(n) ? "" : "none"; $(`[name=${n}]`, f).dataset.skip = show.includes(n) ? "" : "1"; }); };
      S.H.stype = sync; sync(); } },
};
// one "+ New" item: doc:<type> | eq:<action> | asset:new | asset:depr | entry:<entry type id>
function newItem(key) {
  const [kind, id] = key.includes(":") ? key.split(/:(.*)/s) : ["doc", key];
  if (kind === "doc") return openDoc(id);
  if (kind === "eq") return id === "drawing" ? openDoc("partner_tx") : equityForm(id);
  if (kind === "asset") return id === "depr" ? deprForm() : assetForm();
  if (kind === "entry") return entryTypeForm(id);
  if (kind === "loan") return id === "pay" ? pickThen("loans", lid => loanAction(lid, "pay")) : loanForm();
  if (kind === "contact") return contactForm({}, cid => contactView(cid));
  if (kind === "sale") return id === "collect" ? pickSale(s => collectForm(s)) : saleForm();
  if (kind === "product") return productForm();
  if (kind === "recurring") return recurringForm();
  if (kind === "bank") return bankImportForm();
  if (kind === "custody") return id === "settle" ? pickThen("custody", c => custodyAction(c, "settle")) : custodyForm();
}
async function openDoc(type, pre, edit, copyVals) {
  if (type === "invoice") { location.hash = "#/invoice/new"; return; }
  if (type === "offer") { location.hash = "#/offers"; return; }
  if (type === "journal") return journalForm();
  if (type === "bu") return buForm();
  const d = DOCS[type]; if (!d) return;
  let names = [];
  try { names = (await api("GET", "/api/parties?kind=all")).names; } catch { /* no permission */ }
  const items = S.boot.items.map(i => i.name);
  const vals = (edit && edit.form) || copyVals;
  const nc = [];
  drawer(t(d.title) + " — " + (edit ? `${t("edit")} #${edit.id}` : t("new")), form(d.fields(pre)) + unitsList() + partyList("parties", names) + datalist("items", items),
    async v => { if (edit) { await api("PUT", `/api/docs/${type}/${edit.id}`, v); toast(t("saved")); } else { await api("POST", "/api/docs/" + type, v); toast(t("posted")); }
      await saveNewContacts(nc); },
    { onOpen: f => { if (d.open) d.open(f, !!edit); if (vals) fillForm(f, vals); if (d.calc) { S.H.calc = () => d.calc(f); d.calc(f); }
      [["vendor", "supplier"], ["party", type === "receipt" ? "customer" : "supplier"], ["worker", "employee"]].forEach(([k, c]) => { if ($(`[name=${k}]`, f) && $(`[name=${k}]`, f).getAttribute("list") === "parties") nc.push(offerContact(f, k, c)); }); } });
}
function fillForm(f, vals) {
  Object.entries(vals).forEach(([k, v]) => { const el = $(`[name="${k}"]`, f); if (!el || v == null) return;
    if (el.type === "checkbox") el.checked = v === true || v === 1 || v === "1" || v === "true";
    else if (el.tagName === "SELECT" && ![...el.options].some(o => o.value === String(v))) return;
    else el.value = v; });
}
function journalLinesEditor(lines) {
  const opts = S.boot.accounts.filter(a => a.active).map(a => `<option value="${a.code}">${esc(a.code)} ${esc(aname(a))}</option>`).join("");
  return `<div class="tbl"><table id="jl"><thead><tr><th>${t("account")}</th><th class="num">${t("debit")}</th><th class="num">${t("credit")}</th><th title="${esc(t("line_party_help"))}">${t("line_party")}</th><th></th></tr></thead><tbody>
    ${lines.map(l => `<tr><td><select data-skip="1" class="ja"><option value="">—</option>${opts.replace(`value="${l.account}"`, `value="${l.account}" selected`)}</select></td>
      <td><input data-skip="1" class="jd num" type="number" step="any" value="${l.debit || ""}" data-input="jbal"></td><td><input data-skip="1" class="jc num" type="number" step="any" value="${l.credit || ""}" data-input="jbal"></td>
      <td><input data-skip="1" class="jp" value="${esc(l.party || "")}" list="jparties" style="min-width:90px"></td>
      <td><button type="button" class="btn sm danger" data-a="jrm">✕</button></td></tr>`).join("")}</tbody></table></div>
    ${partyList("jparties", (S.boot.partners || []).map(p => p.name))}
    <div class="row" style="margin-top:8px"><button type="button" class="btn sm" data-a="jadd">${t("add_line")}</button><span class="spacer"></span><span id="jbal"></span></div>`;
}
function journalForm(e, copy) {
  const lines = e ? e.lines : [{ account: "", debit: "", credit: "" }, { account: "", debit: "", credit: "" }];
  const tpl = Object.fromEntries((S.boot.enums.entry_types || []).filter(x => x.active !== false).map(x => [x.id, x]));
  const html = form([
    { k: "date", l: t("date"), type: "date", v: e ? e.date : today(), req: 1 },
    ...(e ? [] : [{ k: "template", l: t("template"), type: "select", opts: [["", "—"], ...Object.values(tpl).map(x => [x.id, (S.lang === "ar" && x.name_ar) || x.name])], change: "jtpl", help: t("template_help") }]),
    { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—"), v: e ? e.bu_id : "" }, { k: "party", l: t("party"), v: e ? e.party : "" },
    { k: "ref", l: t("ref"), v: e ? e.ref : "" },
    ...(e && (e.kind === "Direct Cost" || e.kind === "Supplier Discount" || (e.meta && e.meta.deal_ref)) ? [{ k: "deal_ref", l: t("deal_ref"), v: e.meta.deal_ref || "" },
      { k: "deal_status", l: t("deal_status"), type: "select", opts: [["", "—"], "Open", "Settled"], v: e.meta.deal_status || "" }] : []),
    { k: "memo", l: t("memo"), v: e ? e.memo : "", span: "spanall" }]) + `<div style="margin-top:12px">${journalLinesEditor(lines)}</div>`;
  const collect = f => $$("#jl tbody tr", f).map(tr => ({ account: $(".ja", tr).value, debit: $(".jd", tr).value, credit: $(".jc", tr).value, party: $(".jp", tr).value }));
  drawer((e && !copy ? t("edit") : t("new")) + " — " + t("m_journal"), html, async (v, f) => {
    const body = { ...v, lines: collect(f) };
    if (e && !copy) await api("PUT", "/api/journal/" + e.id, body); else await api("POST", "/api/journal", { ...body, kind: copy ? (Object.values(tpl).some(x => x.kind === e.kind) ? e.kind : "Journal Voucher") : v.template && tpl[v.template] ? tpl[v.template].kind : "Journal Voucher" });
    toast(t("saved"));
  }, { onOpen: f => {
    const bal = () => { const L = collect(f); const d = L.reduce((s, l) => s + (+l.debit || 0), 0), c = L.reduce((s, l) => s + (+l.credit || 0), 0);
      $("#jbal", f).innerHTML = `${t("debit")} ${money(d)} · ${t("credit")} ${money(c)} — <span class="${Math.abs(d - c) < 0.005 && d > 0 ? "bal-ok" : "bal-bad"}">${Math.abs(d - c) < 0.005 && d > 0 ? t("balanced") : t("unbalanced")}</span>`; };
    S.H.jbal = bal; bal();
    S.H.jadd = () => { const tb = $("#jl tbody", f); tb.insertAdjacentHTML("beforeend", tb.lastElementChild.outerHTML); $$("input", tb.lastElementChild).forEach(i => { i.value = ""; }); $(".ja", tb.lastElementChild).value = ""; bal(); };
    S.H.jrm = el => { if ($$("#jl tbody tr", f).length > 2) el.closest("tr").remove(); bal(); };
    S.H.jtpl = el => { const x = tpl[el.value]; if (!x) return; const rows = $$("#jl tbody tr", f);
      const code = side => side.fixed || side.default || "";
      $(".ja", rows[0]).value = code(x.dr); $(".ja", rows[1]).value = code(x.cr); if (!$("[name=memo]", f).value) $("[name=memo]", f).value = (S.lang === "ar" && x.name_ar) || x.name; };
  } });
}

// ============================================================== pages
PAGES.dashboard = async (view, parts, q) => {
  const d = await api("GET", "/api/dashboard?" + qs({ date_from: q.date_from || yearStart(), date_to: q.date_to || "", bu_id: q.bu_id || "" }));
  const k = (l, v, s, cls = "") => `<div class="kpi"><div class="l">${esc(l)}</div><div class="v ${cls}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
  const max = Math.max(1, ...d.series.map(s => Math.max(s.revenue, s.costs)));
  const cp = d.cash_position;
  view.innerHTML = filterBar({ date_from: q.date_from || yearStart(), ...q }, ["dates", "bu"]) +
    `<div class="kpis">${k(t("cash"), money(d.cash), cur(), d.cash < 0 ? "neg" : "")}${k(t("receivable"), money(d.receivable))}${k(t("payable"), money(d.payable))}
      ${k(t("accrued"), money(d.accrued))}${k(t("advances"), money(d.advances))}${k(t("inventory"), money(d.inventory))}
      ${k(t("revenue"), money(d.revenue))}${k(t("ebitda"), money(d.ebitda), pct(d.margins.ebitda), d.ebitda < 0 ? "neg" : "")}${k(t("net_profit"), money(d.net_profit), pct(d.margins.net), d.net_profit < 0 ? "neg" : "pos")}</div>
    ${S.boot.settings.demo_loaded ? `<div class="banner info">${L2("You are looking at the demo company (invented data).", "هذه بيانات الشركة التجريبية (بيانات وهمية).")} <a href="#/data">${L2("Demo & data", "البيانات التجريبية")} →</a></div>` : ""}
    ${d.recurring_due ? `<div class="banner ${d.recurring_due.errors ? "err" : "info"}">${L2("Recurring entries due", "قيود متكررة مستحقة")}: ${d.recurring_due.review ? `${d.recurring_due.review} ${L2("to review", "للمراجعة")}` : ""}${d.recurring_due.auto ? ` ${d.recurring_due.auto} ${L2("automatic", "تلقائية")}` : ""}${d.recurring_due.errors ? ` · ⚠ ${d.recurring_due.errors} ${L2("failed", "فشلت")}` : ""} · <a href="#/recurring">${L2("Open", "فتح")} →</a></div>` : ""}
    ${d.installments_overdue ? `<div class="banner err">${L2("Overdue installments", "أقساط متأخرة")}: ${money(d.installments_overdue.amount)} (${d.installments_overdue.count} · ${d.installments_overdue.customers} ${L2("customers", "عميل")}) · <a href="#/installments?status=overdue">${L2("Follow up", "متابعة التحصيل")} →</a></div>` : ""}
    ${d.cheques_due ? `<div class="banner warn">${d.cheques_due} ${L2("customer cheques are due — deposit them", "شيكات عملاء مستحقة — قم بإيداعها")} · <a href="#/installments">${L2("Open", "فتح")} →</a></div>` : ""}
    ${d.depreciation_due ? `<div class="banner warn">${L2("Depreciation not booked yet up to", "لم يُحتسب الإهلاك حتى")} ${esc(d.depreciation_due.through)}: ${money(d.depreciation_due.total)} (${d.depreciation_due.count}) · <a href="#/${d.depreciation_due.rental ? "rentfleet" : "assets"}">${L2("Run depreciation", "احتساب الإهلاك")} →</a></div>` : ""}
    <div class="banner ${cp.gap > 0 ? "err" : "ok"}">${t("cash_floor")}: ${money(cp.floor)} (${t("floor_note")}) · ${t("cash")} ${money(cp.cash)}${cp.gap > 0 ? ` · <b>−${money(cp.gap)}</b>` : " ✓"}</div>
    <div class="grid g2"><div class="card"><h2>${t("last12")}</h2><div class="legend"><span class="r">${t("rev")}</span><span class="c">${t("costs")}</span></div>
      <div class="bar">${d.series.map(s => `<div class="m"><div class="b"><i class="r" style="height:${(s.revenue / max * 100).toFixed(1)}%" title="${money(s.revenue)}"></i><i class="c" style="height:${(s.costs / max * 100).toFixed(1)}%" title="${money(s.costs)}"></i></div>${esc(s.month.slice(2))}</div>`).join("") || `<div class="empty">${t("nothing")}</div>`}</div></div>
      <div class="card"><h2>${t("model_audit")}</h2>${d.audit.map(a => `<div class="row" style="justify-content:space-between;padding:3px 0"><span>${esc(a.check)}</span><span class="chip ${a.ok ? "ok" : "bad"}">${a.ok ? "OK" : "CHECK"}</span></div>`).join("")}</div></div>
    <div class="card"><h2>${t("m_bus")}</h2>${buTable(d.units)}</div>`;
};
function buTable(rows) {
  if (!rows.length) return `<div class="empty">${t("nothing")}</div>`;
  return `<div class="tbl stack"><table><thead><tr><th>${t("code")}</th><th>${t("name")}</th><th>${t("basis")}</th><th class="num">${t("rev")}</th><th class="num">${t("direct_costs")}</th><th class="num">${t("direct_overhead")}</th><th class="num">${t("allocated_overhead")}</th><th class="num">${t("net_profit")}</th><th class="num">${t("margin")}</th><th class="num">${t("to_collect")}</th></tr></thead><tbody>
    ${rows.map(b => `<tr class="click" data-href="#/bu/${b.id}"><td><b>${esc(b.code)}</b><div class="lines">${esc(tl(b.status))}</div></td><td>${esc(b.name)}</td><td><span class="chip">${esc(tl(b.basis))}${b.basis === "Cost-Plus %" ? " " + pct(b.fee_pct) : ""}</span></td>
      <td class="num">${money(b.revenue)}</td><td class="num">${money(b.cogs)}</td><td class="num">${money(b.direct_overhead)}</td><td class="num">${money(b.allocated_overhead)}<div class="lines">${pct(b.overhead_pct)}</div></td>
      <td class="num ${b.net_profit < 0 ? "neg" : "pos"}"><b>${money(b.net_profit)}</b></td><td class="num">${pct(b.margin)}</td><td class="num">${money(b.to_collect)}</td></tr>`).join("")}</tbody></table></div>`;
}
PAGES.bus = async (view, parts, q) => {
  const d = await api("GET", "/api/bus?" + qs(q));
  const alloc = d.rows.reduce((s, b) => s + (b.overhead_pct || 0), 0);
  S.H.newbu = () => buForm();
  S.H.alloc = () => {
    const rows = d.rows;
    const active = b => !["Completed", "Closed"].includes(b.status);
    drawer(t("alloc_title"), `<p class="muted">${t("alloc_note")}</p>
      <div class="row" style="margin-bottom:8px"><button type="button" class="btn" data-a="aleven">${t("alloc_even")}</button><button type="button" class="btn" data-a="alclear">${t("alloc_clear")}</button><span class="spacer"></span><b id="altot"></b></div>
      <div class="tbl"><table><thead><tr><th>${t("code")}</th><th>${t("name")}</th><th>${t("status")}</th><th class="num">%</th><th class="num">${t("allocated_overhead")}</th></tr></thead><tbody>
      ${rows.map(b => `<tr><td><b>${esc(b.code)}</b></td><td>${esc(b.name)}</td><td>${esc(tl(b.status))}</td><td><input class="num al" data-id="${b.id}" data-active="${active(b) ? 1 : 0}" type="number" step="0.01" min="0" max="100" value="${+(b.overhead_pct * 100).toFixed(4)}" data-input="altot" style="max-width:110px"></td>
        <td class="num al-amt"></td></tr>`).join("")}</tbody></table></div>`,
    async () => { const allocations = {}; $$(".al").forEach(i => { allocations[i.dataset.id] = (+i.value || 0) / 100; });
      await api("PUT", "/api/bus-allocation", { allocations }); toast(t("saved")); },
    { onOpen: f => {
      S.H.altot = () => { let s = 0; $$(".al", f).forEach(i => { const v = +i.value || 0; s += v; i.closest("tr").querySelector(".al-amt").textContent = money(d.shared_overhead * v / 100); });
        $("#altot", f).innerHTML = `${t("total")}: <span class="${s > 100.0001 ? "bal-bad" : "bal-ok"}">${s.toFixed(2)}%</span> · ${t("shared_pool")}: ${money(d.shared_overhead)}`; };
      S.H.aleven = () => { const act = $$(".al", f).filter(i => i.dataset.active === "1"); const n = act.length || 1;
        $$(".al", f).forEach(i => { i.value = i.dataset.active === "1" ? (Math.floor(10000 / n) / 100).toFixed(2) : 0; });
        if (act.length) act[0].value = (100 - Math.floor(10000 / n) / 100 * (n - 1)).toFixed(2); S.H.altot(); };
      S.H.alclear = () => { $$(".al", f).forEach(i => { i.value = 0; }); S.H.altot(); };
      S.H.altot(); } });
  };
  view.innerHTML = `<div class="toolbar">${can("business_units.units", "add") ? `<button class="btn pri" data-a="newbu">+ ${t("new")}</button>` : ""}
    ${can("business_units.allocation", "edit") ? `<button class="btn" data-a="alloc">${t("alloc_title")}</button>` : ""}<span class="spacer"></span>${can("business_units.profitability") ? exportMenu("bu_pnl", q) : ""}</div>` +
    filterBar(q, ["dates", "q", ["select", "status", t("status"), ["Tendering", "In Progress", "On Hold", "Completed", "Closed"].map(s => [s, tl(s)])]]) +
    `<div class="banner ${alloc > 1.0001 ? "err" : "info"}">${t("shared_pool")}: <b>${money(d.shared_overhead)}</b> · ${t("alloc_total")}: <b>${pct(alloc)}</b> — ${t("overhead_note")}</div>` +
    `<div class="card">${buTable(d.rows)}</div>`;
};
const BANK_F = () => [["label", L2("Name for this account", "اسم مختصر للحساب"), "CIB — EGP"], ["bank", t("bank_name")], ["acct", t("bank_account")], ["holder", t("bank_holder")], ["swift", t("swift")], ["iban", t("iban")]];
const bankName = b => b.label || b.bank || b.acct || "—";
// what happens to a discount negotiated with a supplier (per business unit; each cost can override it)
const NEGO_OPTS = () => [["keep", L2("Company earnings — client billed on the full price", "أرباح للشركة — العميل يُحاسب بالسعر الكامل")],
  ["pass", L2("Lower the cost — the client benefits", "تخفيض التكلفة — لصالح العميل")]];
const buNego = id => ((S.boot.bus.find(b => String(b.id) === String(id)) || {}).negotiation) || "keep";
function buForm(b) {
  b = b || { status: "In Progress", basis: "Cost-Plus %" };
  drawer((b.id ? t("edit") : t("new")) + " — " + t("m_bus"), form([
    { k: "code", l: t("bu_code"), v: b.code || `PRJ-${today().slice(0, 4)}-${String(S.boot.bus.length + 1).padStart(3, "0")}`, req: 1 }, { k: "name", l: t("bu_name"), v: b.name, req: 1 },
    { k: "type", l: t("bu_type"), v: b.type }, { k: "location", l: t("location"), v: b.location },
    { k: "area", l: t("area"), type: "number", v: b.area }, { k: "start_date", l: t("start_date"), type: "date", v: b.start_date },
    { k: "status", l: t("status"), type: "select", opts: ["Tendering", "In Progress", "On Hold", "Completed", "Closed"], v: b.status },
    { k: "basis", l: t("basis"), type: "select", opts: S.boot.enums.billing_bases, v: b.basis },
    { k: "fee_pct", l: t("fee_pct"), type: "number", v: b.fee_pct ? +(b.fee_pct * 100).toFixed(4) : "" },
    { k: "contract_value", l: t("contract_value"), type: "number", v: b.contract_value || "" },
    { k: "negotiation", l: L2("Negotiation discounts", "خصومات التفاوض"), type: "select", opts: NEGO_OPTS(), v: b.negotiation || "keep",
      help: L2("Default for this project's costs. Earnings: the cost stays at the supplier's full price (cost-plus bills the client on it) and the discount shows as Negotiation Earnings.",
        "الافتراضي لتكاليف هذا المشروع. أرباح: تبقى التكلفة بالسعر الكامل (ويُحاسب العميل عليه في التكلفة+) ويظهر الخصم كأرباح تفاوض.") },
    { k: "overhead_pct", l: t("overhead_pct"), type: "number", v: b.overhead_pct ? +(b.overhead_pct * 100).toFixed(4) : "", help: t("overhead_note") },
    { k: "customer", l: t("customer"), v: b.customer, list: "custs" },
    { k: "notes", l: t("notes"), type: "textarea", v: b.notes, span: "spanall" }]) + datalist("custs", (S.boot.parties || []).map(p => p.name)),
  async v => { v.fee_pct = (+v.fee_pct || 0) / 100; v.overhead_pct = (+v.overhead_pct || 0) / 100;
    if (b.id) await api("PUT", "/api/bus/" + b.id, v); else await api("POST", "/api/bus", v); await refreshBoot(); toast(t("saved")); });
}
PAGES.bu = async (view, parts, q) => {
  const id = parts[1];
  const [list, tx] = await Promise.all([api("GET", "/api/bus?" + qs({ date_from: q.date_from, date_to: q.date_to })),
    api("GET", "/api/bu-journal?" + qs({ ...q, bu_id: id, limit: q.limit || 50 }))]);
  const b = list.rows.find(x => String(x.id) === String(id));
  if (!b) { view.innerHTML = `<div class="banner err">Not found</div>`; return; }
  $("#ttl").textContent = `${b.code} — ${b.name}`;
  S.H.edbu = () => buForm(b);
  S.H.delbu = async () => { if (!confirm(t("confirm_delete"))) return; try { await api("DELETE", "/api/bus/" + b.id); await refreshBoot(); location.hash = "#/bus"; } catch (e) { fail(e); } };
  const k = (l, v, cls = "") => `<div class="kpi"><div class="l">${esc(l)}</div><div class="v ${cls}">${money(v)}</div></div>`;
  const rp = r => `#/report/${r}?` + qs({ bu_id: id, date_from: q.date_from || yearStart(), date_to: q.date_to || "" });
  view.innerHTML = `<div class="toolbar">${can("business_units.units", "edit") ? `<button class="btn" data-a="edbu">${t("edit")}</button><button class="btn danger" data-a="delbu">${t("delete")}</button>` : ""}
      ${can("reports.income_statement") ? `<a class="btn" href="${rp("income_statement")}">${t("m_is")}</a><a class="btn" href="${rp("cash_flow")}">${t("m_cf")}</a>` : ""}<span class="spacer"></span>
      ${exportMenu("vouchers", { ...q, bu_id: id, title: `${b.code} — ${t("transactions")}` })}</div>
    <div class="kpis">${k(t("rev"), b.revenue)}${k(t("direct_costs"), b.cogs)}${k(t("direct_overhead"), b.direct_overhead)}${k(t("allocated_overhead"), b.allocated_overhead)}
      ${k(t("net_profit"), b.net_profit, b.net_profit < 0 ? "neg" : "pos")}${k(t("receivable"), b.receivable)}${k(t("expected_billing"), b.expected_billing)}${k(t("to_collect"), b.to_collect)}
      ${b.savings ? `<a class="kpi" href="#/negotiations?bu_id=${b.id}" style="text-decoration:none;color:inherit"><div class="l">${esc(t("m_nego"))}</div><div class="v pos">${money(b.savings_kept)}</div><div class="muted" style="font-size:.78em">${L2("of", "من")} ${money(b.savings)} ${L2("negotiated", "خصومات")}</div></a>` : ""}</div>` +
    filterBar(q, ["dates", ["kind", S.boot.enums.entry_kinds], "qv", "party", "account", "status"]) +
    `<div class="card"><h2>${t("transactions")}</h2>${voucherRows(tx.rows)}${pager(tx.total, { ...q, limit: q.limit || 50 })}</div>`;
};

function voucherPage(dtype, kinds, opts = {}) {
  return async (view, parts, q) => {
    const lq = { ...q, limit: q.limit || 50 };
    const d = await api("GET", `/api/docs/${dtype}?` + qs(lq));
    const mod = { direct_cost: "purchasing.costs", expense: "purchasing.expenses", payment: "purchasing.payments", payroll: "payroll", receipt: "sales.receipts", partner_tx: "partners.transactions", stock_move: "stock.moves" }[dtype];
    S.H.newdoc = () => openDoc(dtype);
    const top = opts.top ? await opts.top(q) : "";
    view.innerHTML = `<div class="toolbar">${can(mod, "add") ? `<button class="btn pri" data-a="newdoc">+ ${t("new")}</button>` : ""}${opts.buttons || ""}<span class="spacer"></span>
      ${exportMenu("vouchers", { ...q, kinds: kinds.join(","), title: t(opts.title) })}</div>${top}` +
      filterBar(q, ["dates", "bu", "qv", "party", ...(kinds.length > 1 ? [["kind", kinds]] : []), "status"]) +
      `<div class="card">${voucherRows(d.rows)}${pager(d.total, lq)}</div>`;
  };
}
PAGES.costs = voucherPage("direct_cost", ["Direct Cost"], { title: "m_costs" });
PAGES.expenses = voucherPage("expense", ["Operating Expense"], { title: "m_expenses" });
PAGES.payments = voucherPage("payment", ["Payment Voucher"], { title: "m_payments" });
PAGES.receipts = voucherPage("receipt", ["Receipt Voucher"], { title: "m_receipts" });
PAGES.payroll = voucherPage("payroll", ["Payroll Accrual"], { title: "m_payroll", top: async () => {
  const p = await api("GET", "/api/parties?kind=payroll");
  S.H.paywk = el => openDoc("payment", { party: el.dataset.p, amount: el.dataset.b, against: el.dataset.acc });
  return p.rows.length ? `<div class="card"><h2>${t("unpaid")}</h2><div class="tbl"><table><thead><tr><th>${t("worker")}</th><th class="num">${t("balance")}</th><th></th></tr></thead><tbody>${p.rows.map(r => `<tr><td>${esc(r.party)}</td><td class="num">${money(r.balance)}</td><td>${can("purchasing", "edit") ? `<button class="btn sm" data-a="paywk" data-p="${esc(r.party)}" data-b="${r.balance}" data-acc="${r.account}">${t("m_payments")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div></div>` : ""; } });

PAGES.payables = async (view, parts, q) => {
  const d = await api("GET", "/api/parties?" + qs({ kind: q.kind || "all", date_to: q.date_to }));
  S.H.pay = el => openDoc("payment", { party: el.dataset.p, amount: el.dataset.b, against: el.dataset.acc });
  S.H.disc = el => supplierDiscountForm(el.dataset.p, el.dataset.acc, +el.dataset.b);
  const tot = d.rows.reduce((s, r) => s + r.balance, 0);
  const owedBy = {}; d.rows.forEach(r => { owedBy[r.party] = (owedBy[r.party] || 0) + r.balance; });
  const ar = S.lang === "ar";
  view.innerHTML = `<div class="toolbar"><span class="spacer"></span>${exportMenu("payables", q)}</div>` +
    filterBar(q, ["asof", ["select", "kind", t("type"), [["payable", t("payable")], ["payroll", t("m_payroll")]]]]) +
    `<div class="card"><h2>${t("m_payables")} — ${money(tot)}</h2>${d.rows.length ? `<div class="tbl stack"><table><thead><tr><th>${t("party")}</th><th>${t("account")}</th><th class="num">${t("amount")}</th><th class="num">${t("paid_now")}</th><th class="num">${t("balance")}</th><th>${t("date")}</th><th></th></tr></thead><tbody>
      ${d.rows.map(r => `<tr class="click" data-href="#/ledger?${qs({ party: r.party, account: r.account })}"><td>${esc(r.party || "—")}</td><td>${esc(r.account)}</td><td class="num">${money(r.owed)}</td><td class="num">${money(r.paid)}</td><td class="num ${r.balance < 0 ? "neg" : ""}"><b>${money(r.balance)}</b></td><td>${fd(r.last_date)}</td>
      <td style="white-space:nowrap">${can("purchasing.payments", "edit") && r.balance > 0 ? `<button class="btn sm" data-a="pay" data-p="${esc(r.party)}" data-b="${r.balance}" data-acc="${r.account}">${t("m_payments")}</button>
        ${(S.boot.accounts.find(x => String(x.code) === String(r.account)) || {}).subtype === "payable" ? `<button class="btn sm" data-a="disc" data-p="${esc(r.party)}" data-b="${r.balance}" data-acc="${r.account}" title="${ar ? "المورد قبل مبلغاً أقل: إقفال الباقي كخصم" : "The supplier accepted less: close the rest as a discount"}">${ar ? "تسوية بخصم" : "Settle with discount"}</button>` : ""}` : ""}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}</div>
    ${d.deals.length ? `<div class="card"><h2>${t("deal_ref")}</h2><div class="tbl"><table><thead><tr><th>${t("deal_ref")}</th><th>${t("supplier")}</th><th class="num">${t("amount")}</th><th class="num">${t("savings")}</th><th class="num">${t("net_cost")}</th><th>${t("status")}</th></tr></thead><tbody>
      ${d.deals.map(g => `<tr><td>${esc(g.ref)}</td><td>${esc(g.vendor)}</td><td class="num">${money(g.quoted)}</td><td class="num">${money(g.savings)}</td><td class="num">${money(g.cost)}</td><td>${esc(g.status)}${g.status === "Settled" && owedBy[g.vendor] > 0 ? ` <span class="chip warn" title="${ar ? "الحالة «تمت التسوية» لكن المورد ما زال له رصيد — سدده أو «تسوية بخصم»" : "Marked Settled, but this supplier is still owed money — pay it or use “Settle with discount”"}">${ar ? "ما زال مستحقاً" : "still owed"} ${money(owedBy[g.vendor])}</span>` : ""}</td></tr>`).join("")}</tbody></table></div></div>` : ""}`;
};
PAGES.receivables = async (view, parts, q) => reportPage(view, "receivables", q, ["dates"]);
PAGES.negotiations = async (view, parts, q) => {
  const d = await api("GET", "/api/negotiations?" + qs(q));
  const k = (l, v, cls = "", sub = "") => `<div class="kpi"><div class="l">${esc(l)}</div><div class="v ${cls}">${money(v)}</div>${sub ? `<div class="muted" style="font-size:.78em">${sub}</div>` : ""}</div>`;
  const setting = m => m === "pass" ? L2("Lower cost", "تخفيض التكلفة") : L2("Company earnings", "أرباح للشركة");
  const canBu = can("business_units.units", "edit");
  S.H.ngbu = el => { const b = S.boot.bus.find(x => String(x.id) === el.dataset.id); if (b) api("GET", "/api/bus").then(r => buForm(r.rows.find(x => x.id === b.id) || b)); };
  view.innerHTML = `<div class="toolbar"><span class="spacer"></span>${exportMenu("negotiations", q)}</div>` +
    `<div class="kpis">${k(L2("Negotiated with suppliers", "إجمالي خصومات التفاوض"), d.total.savings, "", d.total.quoted ? `${L2("of", "من")} ${money(d.total.quoted)} ${L2("quoted", "معروض")}` : "")}
      ${k(L2("Company earnings", "أرباح للشركة"), d.total.kept, "pos", `${L2("account", "حساب")} ${esc(d.account)}`)}${k(L2("Passed to clients (lower cost)", "لصالح العملاء (تكلفة أقل)"), d.total.passed)}</div>` +
    filterBar(q, ["dates", "bu"]) +
    `<div class="card"><h2>${L2("By project", "حسب المشروع")}</h2>${d.by_bu.length ? `<div class="tbl stack"><table><thead><tr><th>${t("bu")}</th><th>${L2("Default handling", "المعالجة الافتراضية")}</th><th class="num">${L2("Quoted", "المعروض")}</th><th class="num">${t("savings")}</th><th class="num">${L2("Company earnings", "أرباح للشركة")}</th><th class="num">${L2("Passed to client", "لصالح العميل")}</th></tr></thead><tbody>
      ${d.by_bu.map(g => `<tr><td>${g.bu_code ? `<a href="#/bu/${g.bu_id}">${esc(g.bu_code)}</a> ${esc(g.bu_name)}` : t("shared")}</td>
        <td>${g.bu_id ? `${esc(setting(g.setting))}${canBu ? ` <button class="btn sm" data-a="ngbu" data-id="${g.bu_id}">${t("edit")}</button>` : ""}` : ""}</td>
        <td class="num">${money(g.quoted)}</td><td class="num">${money(g.savings)}</td><td class="num pos">${money(g.kept)}</td><td class="num">${money(g.passed)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${t("nothing")}</div>`}
      <p class="muted" style="font-size:.85em;margin-top:10px">${L2("Company earnings: the cost stays at the supplier's full price — a cost-plus client is billed on it — and the discount is booked to Negotiation Earnings. Lower cost: the discount reduces the project's cost. Each project has a default (Business units → Edit); each cost can override it, and older entries can be switched by opening them and pressing Edit.",
        "أرباح للشركة: تبقى التكلفة بالسعر الكامل للمورد — ويُحاسب عميل التكلفة+ عليه — ويُسجّل الخصم في أرباح التفاوض. تخفيض التكلفة: الخصم يقلل تكلفة المشروع. لكل مشروع اختيار افتراضي (وحدات الأعمال ← تعديل) ويمكن تغييره لكل تكلفة، ويمكن تحويل القيود القديمة بفتحها ثم «تعديل».")}</p></div>` +
    `<div class="card"><h2>${L2("Every negotiation", "كل المفاوضات")}</h2>${d.rows.length ? `<div class="tbl stack"><table><thead><tr><th>${t("date")}</th><th>#</th><th>${t("bu")}</th><th>${t("supplier")}</th><th>${t("deal_ref")}</th><th>${t("description")}</th><th class="num">${L2("Quoted", "المعروض")}</th><th class="num">${t("savings")}</th><th>${L2("Goes to", "يذهب إلى")}</th></tr></thead><tbody>
      ${d.rows.slice().reverse().map(r => `<tr class="click" data-a="openv" data-id="${r.id}"><td>${fd(r.date)}</td><td>${r.id}</td><td>${esc(r.bu_code)}</td><td>${esc(r.party)}</td><td>${esc(r.deal_ref)}</td><td>${esc(r.memo)}</td>
        <td class="num">${r.quoted ? money(r.quoted) : ""}</td><td class="num">${money(r.savings)}</td><td><span class="chip ${r.mode === "keep" ? "ok" : ""}">${esc(setting(r.mode))}</span></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}</div>`;
};
// The supplier accepted less than what is still owed: clear the rest as a discount (it reduces that purchase's cost)
async function supplierDiscountForm(party, account, balance) {
  const ar = S.lang === "ar";
  let dflt = {}; try { dflt = await api("GET", "/api/supplier-discount?" + qs({ party, account })); } catch { /* defaults below */ }
  drawer(ar ? "تسوية بخصم" : "Settle with discount", `<p class="muted">${ar ? `${esc(party)} له ${money(balance)}. إن قبل مبلغاً أقل، يُقفل الباقي هنا كخصم على تكلفة الشراء (نفس الحساب ووحدة الأعمال) فلا يظهر كمستحق بعد ذلك.`
      : `${esc(party)} is owed ${money(balance)}. If they accepted less, close the rest here as a discount on the purchase's cost (same account and business unit) — it stops showing as owed.`}</p>` + form([
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: ar ? "مبلغ الخصم" : "Discount amount", type: "number", v: balance, req: 1 },
    { k: "negotiation", l: L2("The discount goes to", "الخصم يذهب إلى"), type: "select", opts: NEGO_OPTS(), v: dflt.negotiation || "keep", change: "sdmode" },
    { k: "credit_account", l: ar ? "الحساب الدائن" : "Credit account", type: "select", v: dflt.credit_account, opts: accts(["COGS", "Expense", "Revenue"]),
      help: ar ? "أرباح التفاوض، أو حساب تكلفة آخر شراء من هذا المورد" : "Negotiation Earnings, or the cost account of the last purchase from this supplier" },
    { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—"), v: dflt.bu_id || "" }, { k: "deal_ref", l: t("deal_ref"), v: dflt.deal_ref || "" },
    { k: "memo", l: t("memo"), span: "spanall" }]),
  async v => { await api("POST", "/api/docs/supplier_discount", { ...v, party, against: account }); toast(t("posted")); },
  { onOpen: f => { S.H.sdmode = () => { const m = $("[name=negotiation]", f).value; $("[name=credit_account]", f).value = m === "keep" ? dflt.keep_account : dflt.cost_account; }; } });
}

// ---------------------------------------------------------- invoices & BOQ
PAGES.invoices = async (view, parts, q) => {
  const rows = await api("GET", "/api/extracts?" + qs(q));
  view.innerHTML = `<div class="toolbar">${can("sales.invoices", "add") ? `<a class="btn pri" href="#/invoice/new">+ ${t("new")}</a>` : ""}<span class="spacer"></span>
    ${exportMenu("vouchers", { ...q, kinds: "Sales Invoice", title: t("m_invoices") })}
    <div class="dd"><button class="btn">${t("m_einv")} ▾</button><div class="dd-menu"><a href="/api/einvoice-batch?${qs({ ...q, format: "json" })}">${t("eta_batch_json")}</a><a href="/api/einvoice-batch?${qs({ ...q, format: "xlsx" })}">${t("eta_batch_xlsx")}</a></div></div></div>` +
    filterBar(q, ["dates", "bu", ["select", "status", t("status"), [["draft", t("draft")], ["posted", t("invoiced")]]]]) +
    `<div class="card">${rows.length ? `<div class="tbl"><table><thead><tr><th>${t("invoice_no")}</th><th>${t("bu")}</th><th>${t("boq")}</th><th>${t("date")}</th><th class="num">${t("amount")}</th><th>${t("status")}</th></tr></thead><tbody>
    ${rows.map(x => `<tr class="click" data-href="#/invoice/${x.id}"><td><b>${esc(x.number)}</b><div class="lines">${esc(x.customer || "")}</div></td><td>${esc(x.bu_code)}</td><td>${esc(x.boq_title || t("standalone"))}</td><td>${fd(x.date)}</td><td class="num">${money(x.total)}</td><td><span class="chip ${x.status === "posted" ? "ok" : ""}">${x.status === "posted" ? t("invoiced") : t("draft")}</span> ${x.eta_status ? `<span class="chip">${esc(x.eta_status)}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}</div>`;
};
function linesEditor(lines, withCost, withContract) {
  return `<div class="tbl"><table id="le"><thead><tr><th>${t("item_no")}</th><th>${t("category")}</th><th style="min-width:260px">${t("description")}</th><th>${t("unit")}</th>
    ${withContract ? `<th class="num">${t("contract_qty")}</th><th class="num">${t("billed_before")}</th>` : ""}<th class="num">${withContract ? t("executed") : t("qty")}</th><th class="num">${t("unit_price")}</th>${withCost ? `<th class="num">${t("unit_cost")}</th>` : ""}<th class="num">${t("amount")}</th><th></th></tr></thead><tbody>
    ${lines.map((l, i) => `<tr data-i="${i}"><td><input class="l-item_no" value="${esc(l.item_no)}" style="width:70px"></td><td><input class="l-category" value="${esc(l.category)}"></td>
      <td><input class="l-description" value="${esc(l.description)}"><input type="hidden" class="l-sub" value="${esc(l.sub || "")}"><input type="hidden" class="l-account" value="${esc(l.account || "")}"></td><td><input class="l-unit" value="${esc(l.unit)}" style="width:70px" list="units"></td>
      ${withContract ? `<td class="num">${l.contract_qty == null ? "—" : numf(l.contract_qty)}</td><td class="num">${numf(l.billed_before || 0)}</td>` : ""}
      <td><input class="l-qty num" type="number" step="any" value="${l.qty ?? 0}" data-input="letot" style="width:90px"></td><td><input class="l-price num" type="number" step="any" value="${l.price ?? 0}" data-input="letot" style="width:110px"></td>
      ${withCost ? `<td><input class="l-cost num" type="number" step="any" value="${l.cost ?? 0}" data-input="letot" style="width:110px"></td>` : ""}
      <td class="num l-amt">${money((l.qty || 0) * (l.price || 0))}</td><td><button class="btn sm danger" data-a="lerm">✕</button></td></tr>`).join("")}</tbody></table></div>`;
}
function readLines() {
  return $$("#le tbody tr").map(tr => { const g = c => { const el = $(".l-" + c, tr); return el ? el.value : ""; };
    return { item_no: g("item_no"), category: g("category"), sub: g("sub"), description: g("description"), unit: g("unit"), qty: +g("qty") || 0, price: +g("price") || 0, cost: +g("cost") || 0, account: g("account") }; })
    .filter(l => l.description.trim());
}
function priceSearchBox() {
  S.H.psearch = async el => {
    const v = el.value.trim(); const box = $("#pres");
    if (v.length < 2) { box.innerHTML = ""; return; }
    const r = await api("GET", "/api/rates?" + qs({ q: v, limit: 12 }));
    S.cache.rates = r.rows;
    box.innerHTML = r.rows.map((x, i) => `<div class="row" style="justify-content:space-between;border-top:1px solid var(--line);padding:5px 0"><span><b>${esc(x.item_no)}</b> ${esc(x.description.slice(0, 110))} <span class="muted">${esc(x.category)} · ${esc(x.unit)}</span></span><span>${money(x.price)} <button class="btn sm" data-a="padd" data-i="${i}">+</button></span></div>`).join("") || `<div class="muted">—</div>`;
  };
  return `<input placeholder="${t("add_from_prices")}" data-input="psearch"><div id="pres"></div>`;
}
PAGES.invoice = async (view, parts, q) => {
  const isNew = parts[1] === "new";
  let x = isNew ? { number: "", date: today(), bu_id: q.bu_id || "", boq_id: q.boq_id || "", lines: [], status: "draft", customer: "", vat_code: "", wht_code: "" } : await api("GET", "/api/extracts/" + parts[1]);
  if (isNew && q.dup) { const src = await api("GET", "/api/extracts/" + q.dup);   // a new draft copied from another invoice
    x = { ...x, bu_id: src.bu_id, boq_id: src.boq_id || "", customer: src.customer || "", vat_code: src.vat_code || "", wht_code: src.wht_code || "",
      lines: (src.lines || []).map(({ id, extract_id, ...l }) => l) }; }
  if (isNew && q.boq_id) { const b = await api("GET", "/api/boqs/" + q.boq_id); x.bu_id = b.bu_id; x.lines = b.lines.map(l => ({ ...l, contract_qty: l.qty, qty: 0 })); }
  const boqs = can("estimating.boqs") ? await api("GET", "/api/boqs") : [];
  const locked = x.status === "posted" || !can("sales.invoices", isNew ? "add" : "edit");
  const render = () => {
    const withContract = !!x.boq_id;
    view.innerHTML = `<div class="toolbar"><a class="btn" href="#/invoices">← ${t("m_invoices")}</a><span class="spacer"></span>
        ${!isNew ? docMenu(`/api/extracts/${x.id}/file`, [["xlsx", "excel_doc"], ["pdf", "pdf_print"]], `<a href="/api/reports/vouchers?${qs({ q: x.number, kinds: "Sales Invoice", format: "pdf", lang: S.lang })}" target="_blank" rel="noopener">${S.lang === "ar" ? "القيد المحاسبي (طباعة)" : "Accounting voucher (print)"}</a>`) : ""}
        ${!isNew && can("sales.invoices", "add") ? `<a class="btn" href="#/invoice/new?dup=${x.id}">${t("duplicate")}</a>` : ""}
        ${!locked ? `${!isNew ? `<button class="btn danger" data-a="xdel">${t("delete")}</button>` : ""}<button class="btn" data-a="xsave">${t("save")}</button><button class="btn pri" data-a="xpost">${t("save_post")}</button>` : `<span class="chip ok">${t("invoiced")}</span>`}</div>
      <div class="card">${form([{ k: "bu_id", l: t("bu"), type: "select", opts: buOpts(), v: x.bu_id, dis: locked }, { k: "number", l: t("invoice_no"), v: x.number || `INV-${today().slice(0, 7)}-`, dis: locked },
        { k: "date", l: t("date"), type: "date", v: x.date, dis: locked }, { k: "boq_id", l: t("boq"), type: "select", opts: [["", t("standalone")], ...boqs.map(b => [b.id, `${b.title} (${b.bu_code || ""})`])], v: x.boq_id, dis: locked, change: "xboq" },
        { k: "customer", l: t("customer"), v: x.customer, list: "custs", dis: locked, change: "xcust" }, { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat"), v: x.vat_code, dis: locked, change: "letot" },
        { k: "wht_code", l: t("wht_code"), type: "select", opts: taxOpts("wht"), v: x.wht_code, dis: locked, change: "letot" }], 4)}${datalist("custs", (S.boot.parties || []).filter(p => p.type !== "supplier").map(p => p.name))}</div>
      ${locked ? "" : `<div class="card">${priceSearchBox()}</div>`}
      <div class="card"><h2>${t("lines")}</h2>${linesEditor(x.lines, false, withContract)}
        <div class="row" style="margin-top:10px">${locked ? "" : `<button class="btn sm" data-a="leadd">${t("add_line")}</button>`}<span class="spacer"></span><b id="letot"></b></div></div>${unitsList()}
      ${!isNew && x.status === "posted" && can("sales.invoices") ? `<div class="card" id="etacard"><h2>${t("m_einv")}</h2><div id="etabody" class="muted">${t("loading")}</div></div>` : ""}`;
    if (locked) $$("#le input, #le button").forEach(e => { e.disabled = true; });
    if ($("#etacard")) etaPanel(x);
    S.H.letot();
  };
  const header = () => ({ bu_id: $("[name=bu_id]").value, number: $("[name=number]").value, date: $("[name=date]").value, boq_id: $("[name=boq_id]").value,
    customer: $("[name=customer]").value, vat_code: $("[name=vat_code]").value, wht_code: $("[name=wht_code]").value });
  S.H.letot = () => { let s = 0; $$("#le tbody tr").forEach(tr => { const a = (+$(".l-qty", tr).value || 0) * (+$(".l-price", tr).value || 0); s += a; $(".l-amt", tr).textContent = money(a); });
    const vc = $("[name=vat_code]"), wc = $("[name=wht_code]"); const vat = Math.round(s * rateOf(vc ? vc.value : "") * 100) / 100, wht = Math.round(s * rateOf(wc ? wc.value : "") * 100) / 100;
    const el = $("#letot"); if (el) el.textContent = `${t("total")}: ${money(s)} · ${t("vat_amt")}: ${money(vat)} · ${t("gross")}: ${money(s + vat)} · ${t("wht_amt")}: (${money(wht)}) · ${t("net_receivable")}: ${money(s + vat - wht)} ${cur()}`; };
  S.H.xcust = el => { const p = (S.boot.parties || []).find(z => z.name === el.value); if (!p) return; if (p.default_vat && !$("[name=vat_code]").value) $("[name=vat_code]").value = p.default_vat; if (p.default_wht && !$("[name=wht_code]").value) $("[name=wht_code]").value = p.default_wht; S.H.letot(); };
  S.H.leadd = () => { x = { ...x, ...header(), lines: [...readLines(), { description: "", qty: 1, price: 0 }] }; render(); };
  S.H.lerm = el => { el.closest("tr").remove(); S.H.letot(); };
  S.H.padd = el => { const r = S.cache.rates[+el.dataset.i]; x = { ...x, ...header(), lines: [...readLines(), { item_no: r.item_no, category: r.category, sub: r.sub, description: r.description, unit: r.unit, qty: 1, price: r.price }] }; render(); };
  S.H.xboq = async el => { if (!el.value) return; const b = await api("GET", "/api/boqs/" + el.value); x = { ...x, ...header(), bu_id: b.bu_id, lines: b.lines.map(l => ({ ...l, contract_qty: l.qty, qty: 0 })) }; render(); };
  const save = async post => { const body = { ...header(), lines: readLines(), post };
    const r = isNew ? await api("POST", "/api/extracts", body) : await api("PUT", "/api/extracts/" + x.id, body);
    toast(post ? t("posted") : t("saved")); location.hash = "#/invoice/" + r.id; if (!isNew) route(); };
  S.H.xsave = () => save(false).catch(fail);
  S.H.xpost = () => save(true).catch(fail);
  S.H.xdel = async () => { if (!confirm(t("confirm_delete"))) return; await api("DELETE", "/api/extracts/" + x.id).catch(fail); location.hash = "#/invoices"; };
  render();
};
async function etaPanel(x) {
  const box = $("#etabody"); if (!box) return;
  let d;
  try { d = await api("GET", "/api/einvoice/" + x.id); } catch (e) { box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
  const tx = can("tax.einvoice", "edit");
  const st = x.eta_status ? `<span class="chip ${/valid/i.test(x.eta_status) && !/invalid/i.test(x.eta_status) ? "ok" : /reject|invalid/i.test(x.eta_status) ? "bad" : "warn"}">${esc(x.eta_status)}</span> ${x.eta_uuid ? `<span class="muted">${esc(x.eta_uuid)}</span>` : ""}` : "";
  box.innerHTML = `<div class="row" style="margin-bottom:8px">${t("eta_status")}: ${st || "—"}</div>
    ${d.problems.length ? `<div class="banner err"><b>${t("eta_problems")}:</b><ul>${d.problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul></div>` : `<div class="banner ok">${t("eta_ready")} — ${money(d.document.totalAmount)} ${cur()}</div>`}
    ${x.eta_error ? `<div class="banner err" style="font-size:.8em">${esc(x.eta_error)}</div>` : ""}
    <div class="row"><a class="btn" href="/api/einvoice/${x.id}?format=json">⬇ ${t("eta_json")}</a>
      ${tx && !d.problems.length && !/valid|submitted/i.test(x.eta_status || "") ? `<button class="btn pri" data-a="etasub">${t("eta_submit")}</button>` : ""}
      ${x.eta_uuid ? `<button class="btn" data-a="etaref">${t("eta_refresh")}</button>` : ""}${tx && x.eta_uuid ? `<button class="btn danger" data-a="etacan">${t("eta_cancel")}</button>` : ""}
      <button class="btn" data-a="etaprev">${t("eta_preview")}</button></div>`;
  S.H.etaprev = () => drawer(t("eta_preview"), `<pre style="white-space:pre-wrap;font-size:.75em;direction:ltr;text-align:left">${esc(JSON.stringify(d.document, null, 2))}</pre>`, null, { readOnly: true });
  S.H.etasub = async () => { try { const r = await api("POST", "/api/einvoice/submit", { ids: [x.id] }); toast((r.results[0] || {}).status || "OK"); route(); } catch (e) { fail(e); } };
  S.H.etaref = async () => { try { const r = await api("POST", `/api/einvoice/${x.id}/refresh`, {}); toast(r.status); route(); } catch (e) { fail(e); } };
  S.H.etacan = async () => { const why = prompt(S.lang === "ar" ? "سبب الإلغاء" : "Reason for cancelling"); if (!why) return; try { await api("POST", `/api/einvoice/${x.id}/cancel`, { reason: why }); route(); } catch (e) { fail(e); } };
}

PAGES.boqs = async (view, parts, q) => {
  const rows = await api("GET", "/api/boqs?" + qs(q));
  view.innerHTML = `<div class="toolbar">${can("estimating.boqs", "add") ? `<a class="btn pri" href="#/boq/new">+ ${t("new")}</a>` : ""}</div>` + filterBar(q, ["bu", "q"]) +
    `<div class="card">${rows.length ? `<div class="tbl"><table><thead><tr><th>${t("title")}</th><th>${t("bu")}</th><th>${t("revision")}</th><th>${t("date")}</th><th class="num">${t("lines")}</th><th class="num">${t("budget")}</th></tr></thead><tbody>
    ${rows.map(b => `<tr class="click" data-href="#/boq/${b.id}"><td><b>${esc(b.title)}</b></td><td>${esc(b.bu_code || "")}</td><td>${esc(b.rev)}</td><td>${fd(b.date)}</td><td class="num">${b.lines}</td><td class="num">${money(b.total)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}</div>`;
};
PAGES.boq = async (view, parts, q = {}) => {
  const isNew = parts[1] === "new";
  let b = isNew ? { title: "", rev: "Rev 01", date: today(), bu_id: "", client: "", location: "", lines: [] } : await api("GET", "/api/boqs/" + parts[1]);
  if (isNew && q.dup) { const src = await api("GET", "/api/boqs/" + q.dup);
    b = { ...b, title: src.title + (S.lang === "ar" ? " (نسخة)" : " (copy)"), rev: src.rev, bu_id: src.bu_id || "", client: src.client || "", location: src.location || "",
      lines: (src.lines || []).map(({ id, boq_id, ...l }) => l) }; }
  const ro = !can("estimating.boqs", isNew ? "add" : "edit");
  const render = () => {
    view.innerHTML = `<div class="toolbar"><a class="btn" href="#/boqs">← ${t("m_boqs")}</a><span class="spacer"></span>
      ${!isNew ? docMenu(`/api/boqs/${b.id}/file`, [["xlsx", "excel_doc"], ["pdf", "pdf_print"]]) : ""}
      ${!isNew && can("sales.invoices", "add") ? `<a class="btn" href="#/invoice/new?boq_id=${b.id}">${t("new_invoice_from")}</a>` : ""}
      ${!isNew && can("estimating.boqs", "add") ? `<a class="btn" href="#/boq/new?dup=${b.id}">${t("duplicate")}</a>` : ""}
      ${!ro ? `${!isNew ? `<button class="btn danger" data-a="bdel">${t("delete")}</button>` : ""}<button class="btn pri" data-a="bsave">${t("save")}</button>` : ""}</div>
      <div class="card">${form([{ k: "title", l: t("title"), v: b.title }, { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—"), v: b.bu_id },
        { k: "rev", l: t("revision"), v: b.rev }, { k: "date", l: t("date"), type: "date", v: b.date }, { k: "client", l: t("client"), v: b.client }, { k: "location", l: t("location"), v: b.location }], 3)}</div>
      ${ro ? "" : `<div class="card">${priceSearchBox()}</div>`}
      <div class="card"><h2>${t("lines")}</h2>${linesEditor(b.lines.map(l => ({ ...l })), true, false)}
        <div class="row" style="margin-top:10px">${ro ? "" : `<button class="btn sm" data-a="leadd">${t("add_line")}</button>`}<span class="spacer"></span><b id="letot"></b></div>
        ${!isNew ? `<div class="muted" style="margin-top:6px">${S.lang === "ar" ? "تمت فوترة" : "Billed so far"}: ${money(b.billed)} (${pct(b.total ? b.billed / b.total : 0)})</div>` : ""}</div>${unitsList()}`;
    S.H.letot();
  };
  const header = () => ({ title: $("[name=title]").value, bu_id: $("[name=bu_id]").value, rev: $("[name=rev]").value, date: $("[name=date]").value, client: $("[name=client]").value, location: $("[name=location]").value });
  S.H.letot = () => { let s = 0, c = 0; $$("#le tbody tr").forEach(tr => { const q2 = +$(".l-qty", tr).value || 0; const a = q2 * (+$(".l-price", tr).value || 0); s += a; c += q2 * (+($(".l-cost", tr) || {}).value || 0); $(".l-amt", tr).textContent = money(a); });
    const el = $("#letot"); if (el) el.textContent = `${t("budget")}: ${money(s)} · ${t("cost")}: ${money(c)} · ${t("margin")}: ${pct(s ? 1 - c / s : 0)}`; };
  S.H.leadd = () => { b = { ...b, ...header(), lines: [...readLines(), { description: "", qty: 1, price: 0, cost: 0 }] }; render(); };
  S.H.lerm = el => { el.closest("tr").remove(); S.H.letot(); };
  S.H.padd = el => { const r = S.cache.rates[+el.dataset.i]; b = { ...b, ...header(), lines: [...readLines(), { item_no: r.item_no, category: r.category, sub: r.sub, description: r.description, unit: r.unit, qty: 1, price: r.price, cost: r.cost }] }; render(); };
  S.H.bsave = async () => { try { const body = { ...header(), lines: readLines() }; const r = isNew ? await api("POST", "/api/boqs", body) : await api("PUT", "/api/boqs/" + b.id, body); toast(t("saved")); location.hash = "#/boq/" + r.id; if (!isNew) route(); } catch (e) { fail(e); } };
  S.H.bdel = async () => { if (!confirm(t("confirm_delete"))) return; await api("DELETE", "/api/boqs/" + b.id).catch(fail); location.hash = "#/boqs"; };
  render();
};
PAGES.prices = async (view, parts, q) => {
  const lq = { ...q, limit: q.limit || 100 };
  const d = await api("GET", "/api/rates?" + qs(lq));
  const edit = r => drawer(t("m_prices"), form([{ k: "category", l: t("category"), v: r.category, list: "cats", req: 1 }, { k: "item_no", l: t("item_no"), v: r.item_no }, { k: "sub", l: t("sub"), v: r.sub },
    { k: "unit", l: t("unit"), v: r.unit, list: "units" }, { k: "description", l: t("description"), v: r.description, type: "textarea", span: "spanall", req: 1 },
    { k: "cost", l: t("unit_cost"), type: "number", v: r.cost }, { k: "markup", l: t("markup"), type: "number", v: r.markup != null ? +(r.markup * 100).toFixed(4) : "" },
    { k: "price", l: t("client_price"), type: "number", v: r.price, help: S.lang === "ar" ? "فارغ = التكلفة × (1 + الهامش)" : "Blank = cost × (1 + markup)" }, { k: "notes", l: t("notes"), v: r.notes },
    { k: "eta_code", l: t("eta_code"), v: r.eta_code, ph: "EG-123456789-1001" }]) + datalist("cats", d.categories) + unitsList(),
    async v => { v.markup = (+v.markup || 0) / 100; if (r.id) await api("PUT", "/api/rates/" + r.id, v); else await api("POST", "/api/rates", v); toast(t("saved")); },
    { extraButtons: r.id && can("estimating.prices", "edit") ? `<button type="button" class="btn danger" data-a="rdel">${t("delete")}</button>` : "" });
  S.H.rnew = () => edit({});
  S.H.redit = el => { const r = d.rows.find(x => String(x.id) === el.dataset.id); S.H.rdel = async () => { if (!confirm(t("confirm_delete"))) return; await api("DELETE", "/api/rates/" + r.id); closeDrawer(); route(); }; edit(r); };
  view.innerHTML = `<div class="toolbar">${can("estimating.prices", "add") ? `<button class="btn pri" data-a="rnew">+ ${t("new")}</button>` : ""}</div>` +
    filterBar(q, ["q", ["select", "category", t("category"), d.categories.map(c => [c, c])]]) +
    `<div class="card">${d.rows.length ? `<div class="tbl"><table><thead><tr><th>${t("item_no")}</th><th>${t("category")}</th><th>${t("description")}</th><th>${t("unit")}</th><th class="num">${t("unit_cost")}</th><th class="num">${t("markup")}</th><th class="num">${t("client_price")}</th></tr></thead><tbody>
      ${d.rows.map(r => `<tr class="click" data-a="${can("estimating.prices", "edit") ? "redit" : ""}" data-id="${r.id}"><td>${esc(r.item_no)}</td><td>${esc(r.category)}</td><td style="max-width:520px">${esc(r.description)}</td><td>${esc(r.unit)}</td><td class="num">${money(r.cost)}</td><td class="num">${pct(r.markup)}</td><td class="num"><b>${money(r.price)}</b></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}
      ${pager(d.total, lq)}</div>`;
};

// ------------------------------------------------------------------ stock
PAGES.stock = async (view, parts, q) => {
  const d = await api("GET", "/api/stock?" + qs(q));
  const tot = d.levels.reduce((s, r) => s + r.value, 0);
  const ed = can("stock.moves", "add");
  for (const ty of ["Receipt", "Issue", "Transfer", "Adjustment"]) S.H["sm" + ty] = el => openDoc("stock_move", { type: ty, item: el && el.dataset && el.dataset.item, warehouse_id: el && el.dataset && el.dataset.wh });
  S.H.itemdel = el => confirmDelete(el.dataset.n, "/api/items/" + el.dataset.id, refreshBoot);
  view.innerHTML = `<div class="toolbar">${ed ? ["Receipt", "Issue", "Transfer", "Adjustment"].map(ty => `<button class="btn ${ty === "Receipt" ? "pri" : ""}" data-a="sm${ty}">${tl(ty)}</button>`).join("") : ""}<span class="spacer"></span>${exportMenu("stock", q)}</div>` +
    filterBar(q, ["warehouse", "q"]) +
    `<div class="card"><h2>${t("inventory")} — ${money(tot)} ${cur()}</h2>${d.levels.length ? `<div class="tbl"><table><thead><tr><th>${t("item")}</th><th>${t("warehouse")}</th><th>${t("unit")}</th><th class="num">${t("on_hand")}</th><th class="num">${t("avg_cost")}</th><th class="num">${t("value")}</th><th></th></tr></thead><tbody>
      ${d.levels.map(r => `<tr><td>${esc(r.name)} ${r.low ? `<span class="chip warn">${t("reorder")}</span>` : ""}</td><td>${esc(r.warehouse)}</td><td>${esc(r.unit)}</td><td class="num ${r.qty < 0 ? "neg" : ""}">${numf(r.qty)}</td><td class="num">${money(r.avg_cost)}</td><td class="num">${money(r.value)}</td>
      <td>${can("stock.balances", "edit") && !r.qty && !r.value ? `<button class="btn sm danger" data-a="itemdel" data-id="${r.item_id}" data-n="${esc(r.name)}" title="${S.lang === "ar" ? "حذف صنف بدون حركات" : "Delete an item that has no movements"}">✕</button> ` : ""}${ed ? `<button class="btn sm" data-a="smIssue" data-item="${esc(r.name)}" data-wh="${r.warehouse_id}">${tl("Issue")}</button> <button class="btn sm" data-a="smAdjustment" data-item="${esc(r.name)}" data-wh="${r.warehouse_id}">${tl("Adjustment")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}</div>`;
};
PAGES.moves = async (view, parts, q) => {
  const lq = { ...q, limit: q.limit || 100 };
  const d = await api("GET", "/api/stock/moves?" + qs(lq));
  S.H.newdoc = () => openDoc("stock_move");
  view.innerHTML = `<div class="toolbar">${can("stock.moves", "add") ? `<button class="btn pri" data-a="newdoc">+ ${t("new")}</button>` : ""}<span class="spacer"></span>
    ${exportMenu("vouchers", { ...q, kinds: "Goods Receipt,Material Issue,Stock Transfer,Stock Adjustment", title: t("m_moves") })}</div>` +
    filterBar(q, ["dates", "warehouse", "bu", ["select", "type", t("type"), S.boot.enums.stock_types.map(s => [s, tl(s)])]]) +
    `<div class="card">${d.rows.length ? `<div class="tbl"><table><thead><tr><th>${t("date")}</th><th>${t("type")}</th><th>${t("item")}</th><th>${t("warehouse")}</th><th class="num">${t("qty")}</th><th class="num">${t("unit_cost")}</th><th class="num">${t("value")}</th><th>${t("bu")}</th></tr></thead><tbody>
      ${d.rows.map(m => `<tr class="${m.entry_id ? "click" : ""}" ${m.entry_id ? `data-a="openv" data-id="${m.entry_id}"` : ""}><td>${fd(m.date)}</td><td><span class="chip">${esc(tl(m.kind))}</span></td><td>${esc(m.item)}</td><td>${esc(m.warehouse)}</td><td class="num ${m.qty < 0 ? "neg" : ""}">${numf(m.qty)} ${esc(m.unit)}</td><td class="num">${money(m.rate)}</td><td class="num">${money(m.value)}</td><td>${esc(m.bu_code || "")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}${pager(9999, lq).replace(/9999/g, "…")}</div>`;
};
PAGES.warehouses = async (view) => {
  const d = await api("GET", "/api/stock");
  const edit = w => drawer(t("m_warehouses"), form([{ k: "name", l: t("name"), v: w.name, req: 1 }, { k: "location", l: t("location"), v: w.location },
    { k: "account", l: t("inv_account"), type: "select", opts: [["", t("default")], ...accts(["Asset"], ["inventory"])], v: w.account || "" },
    ...(w.id ? [{ k: "active", l: t("active"), type: "checkbox", v: w.active }] : [])]),
  async v => { if (w.id) await api("PUT", "/api/warehouses/" + w.id, v); else await api("POST", "/api/warehouses", v); await refreshBoot(); toast(t("saved")); },
  { extraButtons: w.id && can("stock.warehouses", "edit") ? `<button type="button" class="btn danger" data-a="whdel">${t("delete")}</button>` : "" });
  S.H.whnew = () => edit({});
  S.H.whedit = el => { const w = d.warehouses.find(x => String(x.id) === el.dataset.id); S.H.whdel = async () => { try { await api("DELETE", "/api/warehouses/" + w.id); closeDrawer(); await refreshBoot(); route(); } catch (e) { fail(e); } }; edit(w); };
  const val = {}; d.levels.forEach(l => { val[l.warehouse_id] = (val[l.warehouse_id] || 0) + l.value; });
  view.innerHTML = `<div class="toolbar">${can("stock.warehouses", "add") ? `<button class="btn pri" data-a="whnew">+ ${t("new")}</button>` : ""}</div><div class="card"><div class="tbl"><table><thead><tr><th>${t("name")}</th><th>${t("location")}</th><th>${t("inv_account")}</th><th class="num">${t("value")}</th><th>${t("status")}</th></tr></thead><tbody>
    ${d.warehouses.map(w => `<tr class="click" data-a="${can("stock.warehouses", "edit") ? "whedit" : ""}" data-id="${w.id}"><td><b>${esc(w.name)}</b></td><td>${esc(w.location)}</td><td>${esc(w.account || accByRole("inventory"))}</td><td class="num">${money(val[w.id] || 0)}</td><td>${w.active ? "" : `<span class="chip">${t("no")}</span>`}</td></tr>`).join("")}</tbody></table></div></div>`;
};

// ------------------------------------------------------------- accounting
PAGES.journal = async (view, parts, q) => {
  const lq = { ...q, limit: q.limit || 50 };
  const d = await api("GET", "/api/journal?" + qs(lq));
  S.H.newjv = () => journalForm();
  view.innerHTML = `<div class="toolbar">${can("accounting.journal", "add") ? `<button class="btn pri" data-a="newjv">+ ${t("m_journal")}</button>` : ""}<span class="spacer"></span>
    <span class="chip">${d.total}</span><span class="chip">${money(d.posted_amount)} ${cur()}</span>${exportMenu("vouchers", { ...q, title: t("m_journal") })}</div>` +
    filterBar(q, ["dates", "bu", ["kind", S.boot.enums.entry_kinds], "account", "qv", "party", "status"]) +
    `<div class="card">${voucherRows(d.rows)}${pager(d.total, lq)}</div>`;
};
PAGES.ledger = async (view, parts, q) => reportPage(view, "general_ledger", q, ["dates", "account", "bu", "party", "lang"]);
PAGES.coa = async (view, parts, q) => {
  const rows = await api("GET", "/api/accounts?" + qs(q));
  const types = Object.keys(S.boot.enums.subtypes);
  const edit = a => drawer(t("m_coa"), form([{ k: "code", l: t("code"), v: a.code, req: 1 }, { k: "name", l: t("name") + " (English)", v: a.name, req: 1 },
    { k: "name_ar", l: t("name") + " (العربية)", v: a.name_ar },
    { k: "type", l: t("type"), type: "select", opts: types.map(x => [x, tl(x)]), v: a.type || "Expense", change: "atype", dis: a.used },
    { k: "subtype", l: t("statement_line"), type: "select", opts: [], v: a.subtype },
    { k: "active", l: t("active"), type: "checkbox", v: a.active !== 0 },
    { html: a.role ? `<div class="banner info">${t("role")}: <b>${esc(a.role)}</b> — ${S.lang === "ar" ? "يمكن تغيير الرقم والاسم بحرية؛ النظام يتبع الدور." : "you can change the code and name freely; the system follows the role."}</div>` : "", span: "spanall" }]),
  async v => { if (a.used) v.type = a.type; if (a.code && !a._new) await api("PUT", "/api/accounts/" + encodeURIComponent(a.code), v); else await api("POST", "/api/accounts", v); await refreshBoot(); toast(t("saved")); },
  { extraButtons: a.code && !a._new && !a.role && !a.used && can("accounting.coa", "edit") ? `<button type="button" class="btn danger" data-a="adel">${t("delete")}</button>` : "",
    onOpen: f => { S.H.atype = () => { const ty = $("[name=type]", f).value; const s = $("[name=subtype]", f); s.innerHTML = S.boot.enums.subtypes[ty].map(x => `<option value="${x}" ${x === a.subtype ? "selected" : ""}>${esc(sub(x))}</option>`).join(""); }; S.H.atype(); } });
  const nextCode = ty => { const cs = rows.filter(a => a.type === ty && /^\d+$/.test(a.code)).map(a => +a.code).sort((x, y) => x - y);
    if (!cs.length) return ""; let c = cs[cs.length - 1] + 5; while (rows.some(a => a.code === String(c))) c++; return String(c); };
  S.H.anew = el => { const ty = (el && el.dataset.type) || "Expense"; edit({ type: ty, active: 1, code: nextCode(ty), _new: 1 }); };
  S.H.coaq = el => { const v = el.value.trim().toLowerCase(); $$(".coa-row", view).forEach(tr => { tr.style.display = !v || tr.dataset.s.includes(v) ? "" : "none"; });
    $$(".coa-card", view).forEach(c => { c.style.display = !v || $$(".coa-row", c).some(tr => tr.style.display !== "none") ? "" : "none"; }); };
  S.H.aedit = el => { const a = rows.find(x => x.code === el.dataset.id); S.H.adel = async () => { try { await api("DELETE", "/api/accounts/" + encodeURIComponent(a.code)); closeDrawer(); await refreshBoot(); route(); } catch (e) { fail(e); } }; edit(a); };
  const ed = can("accounting.coa", "edit");
  const inactive = rows.filter(a => !a.active).length;
  S.H.coainact = el => setQ({ ...q, inactive: el.checked ? "1" : "" });
  const add = can("accounting.coa", "add");
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="anew">+ ${L2("Add account", "إضافة حساب")}</button>` : ""}
    <input type="search" class="coa-q" placeholder="${L2("Find an account — code or name", "ابحث عن حساب — بالكود أو الاسم")}" data-input="coaq" style="min-width:220px;flex:1;max-width:360px">
    ${inactive ? `<label class="f chk"><input type="checkbox" data-change="coainact" ${q.inactive ? "checked" : ""}>${S.lang === "ar" ? "إظهار الحسابات غير النشطة" : "Show inactive accounts"} (${inactive})</label>` : ""}<span class="spacer"></span>${exportMenu("trial_balance", {})}</div>` +
    filterBar(q, ["asof"]) + types.map(ty => `<div class="card coa-card"><div class="row" style="justify-content:space-between;align-items:center"><h2>${esc(tl(ty))}</h2>${add ? `<button class="btn sm" data-a="anew" data-type="${ty}">+ ${L2("Add", "إضافة")} ${esc(tl(ty))}</button>` : ""}</div><div class="tbl"><table><thead><tr><th>${t("code")}</th><th>${t("account")}</th><th>${t("statement_line")}</th><th>${t("normal")}</th><th class="num">${t("balance")}</th></tr></thead><tbody>
    ${rows.filter(a => a.type === ty && (a.active || q.inactive)).map(a => `<tr class="click coa-row ${a.active ? "" : "void"}" data-a="${ed ? "aedit" : ""}" data-id="${esc(a.code)}" data-s="${esc([a.code, a.name, a.name_ar, sub(a.subtype), a.role].join(" ").toLowerCase())}"><td><b>${esc(a.code)}</b></td><td>${esc(aname(a))}${S.lang === "ar" ? "" : a.name_ar ? ` <span class="muted">${esc(a.name_ar)}</span>` : ""} ${a.role ? `<span class="chip">${esc(a.role)}</span>` : ""}</td><td>${esc(sub(a.subtype))}</td><td>${tl(a.normal) === a.normal ? (S.lang === "ar" ? (a.normal === "Debit" ? "مدين" : "دائن") : a.normal) : ""}</td><td class="num ${a.balance < 0 ? "neg" : ""}">${a.balance ? money(a.balance) : "—"}</td></tr>`).join("")}</tbody></table></div></div>`).join("");
};

// ------------------------------------------------------------------ offers
const SECTION_KINDS = ["text", "pricing", "payment", "bank", "signature"];
const OFFER_STATUS = ["Draft", "Sent", "Accepted", "Rejected"];
const offerTotals = sec => { if (!sec) return { total: 0, discount: 0, vat: 0, net: 0 };
  const total = (sec.items || []).reduce((a, i) => a + (+i.amount || 0), 0), discount = +sec.discount || 0, vat = (total - discount) * (+sec.vat || 0) / 100;
  return { total, discount, vat, net: total - discount + vat }; };
const offerContent = o => ({ lang: o.lang === "ar" ? "ar" : "en", title: o.title || "", intro: o.intro || "", closing: o.closing || "", sections: JSON.parse(JSON.stringify(o.sections || [])) });
const statusChip = st => `<span class="chip ${st === "Accepted" ? "ok" : st === "Rejected" ? "bad" : st === "Sent" ? "warn" : ""}">${t("s_" + (st || "Draft"))}</span>`;

PAGES.offers = async (view, parts, q) => {
  const d = await api("GET", "/api/offers?" + qs(q));
  const add = can("sales.offers", "add");
  view.innerHTML = `<div class="toolbar">${add ? d.types.map(ty => `<a class="btn ${ty === d.types[0] ? "pri" : ""}" href="#/offer/new?type=${ty.id}">+ ${esc(ty.name)}</a>`).join("") : ""}
      <span class="spacer"></span><a class="btn" href="#/offertypes">${t("offer_types")} (${d.types.length})</a></div>` +
    filterBar(q, ["q", ["select", "type_id", t("offer_type"), d.types.map(ty => [ty.id, ty.name])], ["select", "status", t("status"), OFFER_STATUS.map(x => [x, t("s_" + x)])]]) +
    `<div class="card">${d.rows.length ? `<div class="tbl"><table><thead><tr><th>#</th><th>${t("offer_type")}</th><th>${t("client")}</th><th>${t("date")}</th><th>${t("status")}</th><th>${t("bu")}</th><th class="num">${t("net_payable")}</th></tr></thead><tbody>
      ${d.rows.map(o => `<tr class="click" data-href="#/offer/${o.id}"><td><b>${esc(o.number)}</b></td><td>${esc(o.type_name || "—")}</td><td dir="auto">${esc(o.client)}<div class="lines" dir="auto">${esc(o.project)}</div></td>
        <td>${fd(o.date)}</td><td>${statusChip(o.status)}</td><td>${esc(o.bu_code || "")}</td><td class="num">${money(o.net)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}</div>`;
};

function offerEditor(view, d, opts) {
  // d: working copy; opts: {isType, title, onSave, extraButtons}
  const boqsP = can("estimating.boqs") ? api("GET", "/api/boqs").catch(() => []) : Promise.resolve([]);
  let timer = null;
  const preview = () => { clearTimeout(timer); timer = setTimeout(async () => {
    try { const r = await api("POST", "/api/offers-preview", d); const fr = $("#opv"); if (fr) fr.srcdoc = r.html; } catch (e) { /* ignore while typing */ } }, 350); };
  const secHtml = (s, i) => {
    const n = d.sections.length;
    const style = (a, b2) => `<label class="f" style="max-width:220px">${t("show_as")}<select data-change="osec" data-i="${i}" data-k="style"><option value="lines">${a}</option><option value="table" ${s.style === "table" ? "selected" : ""}>${b2}</option></select></label>`;
    let body = "";
    if (s.kind === "text" || s.kind === "signature") body = `<textarea rows="${Math.min(10, Math.max(3, String(s.body || "").split("\\n").length + 1))}" dir="auto" data-input="osec" data-i="${i}" data-k="body">${esc(s.body || "")}</textarea>
      <label class="f chk"><input type="checkbox" data-change="osec" data-i="${i}" data-k="bold" data-bool="1" ${s.bold ? "checked" : ""}>${t("bold_text")}</label>`;
    if (s.kind === "pricing") { const tt = offerTotals(s);
      body = `<div class="tbl"><table><thead><tr><th>${t("description")}</th><th class="num" style="width:150px">${t("amount")} (${esc(cur())})</th><th style="width:40px"></th></tr></thead><tbody>
        ${(s.items || []).map((it, j) => `<tr><td><input dir="auto" value="${esc(it.desc)}" data-input="oitem" data-i="${i}" data-j="${j}" data-k="desc"></td><td><input class="num" type="number" step="any" value="${it.amount ?? 0}" data-input="oitem" data-i="${i}" data-j="${j}" data-k="amount"></td><td><button class="btn sm danger" data-a="oitemdel" data-i="${i}" data-j="${j}">✕</button></td></tr>`).join("")}</tbody></table></div>
        <div class="row" style="margin-top:8px"><button class="btn sm" data-a="oitemadd" data-i="${i}">${t("add_line")}</button>
          <select id="oboq${i}" data-change="ofromboq" data-i="${i}" style="max-width:300px"><option value="">${t("from_boq_total")}</option></select></div>
        <div class="grid g4" style="margin-top:8px"><label class="f">${t("discount")}<input class="num" type="number" step="any" value="${s.discount || 0}" data-input="osec" data-i="${i}" data-k="discount" data-num="1"></label>
          <label class="f">${t("vat_pct")}<input class="num" type="number" step="any" value="${s.vat || 0}" data-input="osec" data-i="${i}" data-k="vat" data-num="1"></label>
          ${style(t("as_lines"), t("as_table"))}<label class="f chk"><input type="checkbox" data-change="osec" data-i="${i}" data-k="words" data-bool="1" ${s.words !== false ? "checked" : ""}>${t("words")}</label></div>
        <div class="muted" id="onet${i}">${t("net_payable")}: <b>${money(tt.net)}</b></div>`; }
    if (s.kind === "payment") { const tot = (s.milestones || []).reduce((a, m) => a + (+m.pct || 0), 0);
      body = `<div class="tbl"><table><thead><tr><th>${t("milestone")}</th><th class="num" style="width:110px">%</th><th style="width:40px"></th></tr></thead><tbody>
        ${(s.milestones || []).map((m, j) => `<tr><td><input dir="auto" value="${esc(m.label)}" data-input="oms" data-i="${i}" data-j="${j}" data-k="label"></td><td><input class="num" type="number" step="any" value="${m.pct ?? 0}" data-input="oms" data-i="${i}" data-j="${j}" data-k="pct"></td><td><button class="btn sm danger" data-a="omsdel" data-i="${i}" data-j="${j}">✕</button></td></tr>`).join("")}</tbody></table></div>
        <div class="row" style="margin-top:8px"><button class="btn sm" data-a="omsadd" data-i="${i}">+ ${t("milestone")}</button>${style(t("as_lines"), t("as_table"))}<span id="oms${i}" class="${Math.abs(tot - 100) < 0.001 ? "muted" : "neg"}">${Math.abs(tot - 100) < 0.001 ? t("sum_100") : `${t("sum_not_100")} ${tot}%`}</span></div>`; }
    if (s.kind === "bank") { const bs = S.boot.settings.banks || [], dflt = bs.find(b => b.id === S.boot.settings.bank_default) || bs[0];
      const pick = s.bank_id || "", bd = s.bank_details || {};
      body = `<div class="grid g2"><label class="f">${L2("Bank account", "الحساب البنكي")}<select data-change="obankpick" data-i="${i}">
          <option value="">${L2("Default", "الافتراضي")}${dflt ? ` — ${esc(bankName(dflt))}` : ""}</option>
          ${bs.map(b => `<option value="${esc(b.id)}" ${pick === b.id ? "selected" : ""}>${esc(bankName(b))}</option>`).join("")}
          <option value="custom" ${pick === "custom" ? "selected" : ""}>${L2("Other — enter details for this offer", "أخرى — إدخال بيانات لهذا العرض")}</option></select></label>${style(t("as_lines"), t("as_table"))}</div>
        ${pick === "custom" ? `<div class="grid g3" style="margin-top:8px">${BANK_F().slice(1).map(([k, l]) => `<label class="f">${esc(l)}<input dir="auto" value="${esc(bd[k] || "")}" data-input="obank" data-i="${i}" data-k="${k}"></label>`).join("")}</div>
          ${can("settings.company", "edit") ? `<button class="btn sm" style="margin-top:8px" data-a="obanksave" data-i="${i}">+ ${L2("Save to the company's banks", "حفظ ضمن بنوك الشركة")}</button>` : ""}`
          : `<div class="muted" style="margin-top:6px">${(() => { const b = bs.find(x => x.id === pick) || dflt; return b ? esc([b.bank, b.acct, b.holder, b.iban].filter(Boolean).join(" · ")) : L2("No bank saved yet — choose Other, or add one in Settings → Company.", "لا يوجد بنك محفوظ — اختر «أخرى» أو أضف بنكاً من الإعدادات ← الشركة."); })()}</div>`}`; }
    return `<div class="sec-ed"><div class="row" style="margin-bottom:8px"><span class="chip">${t("sk_" + s.kind)}</span>
      <input style="flex:1;min-width:160px;font-weight:700" dir="auto" placeholder="${t("heading")}" value="${esc(s.heading || "")}" data-input="osec" data-i="${i}" data-k="heading">
      <button class="btn sm" data-a="osecmv" data-i="${i}" data-d="-1" ${i ? "" : "disabled"} title="${t("move_up")}">↑</button>
      <button class="btn sm" data-a="osecmv" data-i="${i}" data-d="1" ${i < n - 1 ? "" : "disabled"} title="${t("move_down")}">↓</button>
      <button class="btn sm danger" data-a="osecdel" data-i="${i}">✕</button></div>${body}</div>`;
  };
  const render = () => {
    const header = opts.isType
      ? [{ k: "name", l: t("name"), v: d.name, req: 1, input: "ofield" }]
      : [{ k: "client", l: t("client"), v: d.client, input: "ofield", list: "ocl" }, { k: "project", l: S.lang === "ar" ? "المشروع / الوحدة" : "Project / unit", v: d.project, input: "ofield" },
         { k: "date", l: t("date"), type: "date", v: d.date, change: "ofield" }, { k: "status", l: t("status"), type: "select", opts: OFFER_STATUS.map(x => [x, t("s_" + x)]), v: d.status, change: "ofield" },
         { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—"), v: d.bu_id || "", change: "ofield" }];
    view.innerHTML = `<div class="toolbar"><a class="btn" href="${opts.back}">← ${esc(opts.backLabel)}</a><span class="spacer"></span>${opts.extraButtons || ""}<button class="btn pri" data-a="osave">${t("save")}</button></div>
      <div class="split"><div>
        <div class="card"><h2>${esc(opts.title)}</h2>${form([...header,
          { k: "lang", l: t("doc_lang"), type: "select", opts: [["en", "English"], ["ar", "العربية"]], v: d.lang || "en", change: "ofield" },
          { k: "title", l: t("doc_title"), v: d.title, input: "ofield", span: "spanall" },
          { k: "intro", l: t("opening"), type: "textarea", v: d.intro, input: "ofield", span: "spanall" }], 3)}
          <div class="muted" style="font-size:.8em;margin-top:6px">${esc(t("placeholders"))}</div>${datalist("ocl", (S.boot.parties || []).filter(p => p.type !== "supplier").map(p => p.name))}</div>
        <div class="card"><h2>${t("sections")}</h2>${d.sections.map(secHtml).join("") || `<div class="empty">${t("nothing")}</div>`}
          <div class="row">${SECTION_KINDS.map(k => `<button class="btn sm" data-a="osecadd" data-k="${k}">+ ${t("sk_" + k)}</button>`).join("")}</div></div>
        <div class="card">${form([{ k: "closing", l: t("closing"), type: "textarea", v: d.closing, input: "ofield", span: "spanall" }], 1)}</div>
      </div><div class="pv"><div class="muted" style="margin-bottom:6px">${t("live_preview")}</div><iframe id="opv" title="preview"></iframe></div></div>`;
    $$("textarea[name]", view).forEach(el => { el.dir = "auto"; });
    boqsP.then(bs => d.sections.forEach((s, i) => { const sel = $("#oboq" + i); if (sel && bs.length) sel.insertAdjacentHTML("beforeend", bs.map(b => `<option value="${b.id}">${esc(b.title)} · ${money(b.total)}</option>`).join("")); else if (sel) sel.remove(); }));
    preview();
  };
  const hints = i => { const s = d.sections[i]; if (!s) return;
    const n = $("#onet" + i); if (n) n.innerHTML = `${t("net_payable")}: <b>${money(offerTotals(s).net)}</b>`;
    const m = $("#oms" + i); if (m) { const tot = (s.milestones || []).reduce((a, x) => a + (+x.pct || 0), 0); m.className = Math.abs(tot - 100) < 0.001 ? "muted" : "neg"; m.textContent = Math.abs(tot - 100) < 0.001 ? t("sum_100") : `${t("sum_not_100")} ${tot}%`; } };
  Object.assign(S.H, {
    ofield: el => { d[el.name] = el.value; preview(); },
    osec: el => { const s = d.sections[+el.dataset.i]; s[el.dataset.k] = el.dataset.bool ? el.checked : el.dataset.num ? (+el.value || 0) : el.value; hints(+el.dataset.i); preview(); },
    oitem: el => { const it = d.sections[+el.dataset.i].items[+el.dataset.j]; it[el.dataset.k] = el.dataset.k === "amount" ? (+el.value || 0) : el.value; hints(+el.dataset.i); preview(); },
    oms: el => { const m = d.sections[+el.dataset.i].milestones[+el.dataset.j]; m[el.dataset.k] = el.dataset.k === "pct" ? (+el.value || 0) : el.value; hints(+el.dataset.i); preview(); },
    oitemadd: el => { d.sections[+el.dataset.i].items.push({ desc: "", amount: 0 }); render(); },
    oitemdel: el => { d.sections[+el.dataset.i].items.splice(+el.dataset.j, 1); render(); },
    omsadd: el => { d.sections[+el.dataset.i].milestones.push({ label: "", pct: 0 }); render(); },
    omsdel: el => { d.sections[+el.dataset.i].milestones.splice(+el.dataset.j, 1); render(); },
    ofromboq: async el => { if (!el.value) return; const b = await api("GET", "/api/boqs/" + el.value);
      d.sections[+el.dataset.i].items.push({ desc: S.lang === "ar" || d.lang === "ar" ? `الأعمال طبقاً للمقايسة ${b.title}` : `Works as per quotation ${b.title}`, amount: b.total }); render(); },
    osecmv: el => { const a = d.sections, x = +el.dataset.i, y = x + +el.dataset.d; if (y < 0 || y >= a.length) return; [a[x], a[y]] = [a[y], a[x]]; render(); },
    osecdel: el => { if (confirm(t("confirm_delete"))) { d.sections.splice(+el.dataset.i, 1); render(); } },
    osecadd: el => { const ar = d.lang === "ar";
      const blank = { text: { heading: ar ? "قسم جديد" : "NEW SECTION", body: "" }, pricing: { heading: ar ? "التكلفة" : "PRICE", items: [{ desc: "", amount: 0 }], discount: 0, vat: 0, words: true },
        payment: { heading: ar ? "شروط الدفع" : "PAYMENT TERMS", milestones: [{ label: ar ? "دفعة مقدمة" : "Advance payment", pct: 50 }, { label: ar ? "عند التسليم" : "On completion", pct: 50 }] },
        bank: { heading: ar ? "بيانات البنك" : "BANK DETAILS" }, signature: { heading: ar ? "القبول" : "ACCEPTANCE", body: ar ? "اسم العميل: ____________________\nالتوقيع: ____________________   التاريخ: __________" : "Client name: ____________________\nSignature: ____________________   Date: __________" } }[el.dataset.k];
      d.sections.push({ kind: el.dataset.k, ...blank }); render(); },
    obankpick: el => { const s = d.sections[+el.dataset.i]; s.bank_id = el.value;
      if (el.value === "custom" && !s.bank_details) { const b = (S.boot.settings.banks || []).find(x => x.id === S.boot.settings.bank_default) || {}; s.bank_details = { bank: b.bank || "", acct: b.acct || "", holder: b.holder || "", swift: b.swift || "", iban: b.iban || "" }; }
      render(); },
    obank: el => { const s = d.sections[+el.dataset.i]; s.bank_details = { ...(s.bank_details || {}), [el.dataset.k]: el.value }; preview(); },
    obanksave: async el => { const s = d.sections[+el.dataset.i], bd = s.bank_details || {};
      if (!Object.values(bd).some(Boolean)) return;
      const id = "b" + Date.now().toString(36); const bs = [...(S.boot.settings.banks || []), { id, label: bd.bank || "", ...bd }];
      try { await api("PUT", "/api/settings", { banks: bs, bank_default: S.boot.settings.bank_default || id }); await refreshBoot();
        const saved = (S.boot.settings.banks || []).find(b => b.acct === bd.acct && b.bank === bd.bank) || {}; s.bank_id = saved.id || ""; delete s.bank_details; toast(t("saved")); render(); } catch (e) { fail(e); } },
    osave: async () => { try { await opts.onSave(d); } catch (e) { fail(e); } },
  });
  render();
}

PAGES.offer = async (view, parts, q) => {
  const id = parts[1];
  if (id === "new" || parts[2] === "edit") {
    let d;
    if (id === "new") {
      const types = await api("GET", "/api/offer-types");
      const ty = types.find(x => String(x.id) === String(q.type)) || types[0] || { sections: [] };
      d = { type_id: ty.id || "", date: today(), client: "", project: "", status: "Draft", bu_id: q.bu_id || "", ...offerContent(ty) };
    } else d = await api("GET", "/api/offers/" + id);
    const tyName = d.type_name || ((await api("GET", "/api/offer-types")).find(x => x.id === d.type_id) || {}).name || t("offer");
    offerEditor(view, d, { title: (id === "new" ? t("new") + " — " : d.number + " — ") + tyName, back: id === "new" ? "#/offers" : "#/offer/" + id, backLabel: t("m_offers"),
      extraButtons: can("sales.offers", "add") ? `<button class="btn" data-a="osavetype">${t("save_as_type")}</button>` : "",
      onSave: async v => { const r = id === "new" ? await api("POST", "/api/offers", v) : await api("PUT", "/api/offers/" + id, v); toast(t("saved")); location.hash = "#/offer/" + r.id; } });
    S.H.osavetype = async () => { const n = prompt(t("name")); if (!n) return;
      try { await api("POST", "/api/offer-types", { name: n, ...offerContent(d) }); toast(t("saved")); await refreshBoot(); } catch (e) { fail(e); } };
    return;
  }
  const o = await api("GET", "/api/offers/" + id);
  $("#ttl").textContent = `${o.number} — ${o.client || ""}`;
  const ed = can("sales.offers", "edit");
  S.H.odel = async () => { if (!confirm(t("confirm_delete"))) return; try { await api("DELETE", "/api/offers/" + o.id); location.hash = "#/offers"; } catch (e) { fail(e); } };
  S.H.odup = async () => { try { const r = await api("POST", "/api/offers", { ...o, number: "", status: "Draft", date: today(), bu_id: "" }); location.hash = `#/offer/${r.id}/edit`; } catch (e) { fail(e); } };
  S.H.obu = async () => { try { const r = await api("POST", `/api/offers/${o.id}/bu`, {}); await refreshBoot(); toast(`${t("saved")} ${r.code}`); route(); } catch (e) { fail(e); } };
  const r = await api("POST", "/api/offers-preview", o);
  view.innerHTML = `<div class="toolbar"><a class="btn" href="#/offers">← ${t("m_offers")}</a>${statusChip(o.status)}<span class="chip">${esc(o.type_name || "")}</span>${o.bu_code ? `<span class="chip">${esc(o.bu_code)}</span>` : ""}
      <span class="chip">${t("net_payable")} ${money(o.net)}</span><span class="spacer"></span>
      <a class="btn pri" href="/api/offers/${o.id}/file?format=docx">⬇ ${t("word_doc")}</a><a class="btn" href="/api/offers/${o.id}/file?format=pdf" target="_blank" rel="noopener">${t("pdf_print")}</a>
      ${ed ? `<a class="btn" href="#/offer/${o.id}/edit">${t("edit")}</a>` : ""}${can("sales.offers", "add") ? `<button class="btn" data-a="odup">${t("duplicate")}</button>` : ""}
      ${ed && !o.bu_id && can("business_units.units", "add") ? `<button class="btn" data-a="obu">${t("create_bu")}</button>` : ""}${ed ? `<button class="btn danger" data-a="odel">${t("delete")}</button>` : ""}</div>
    <div class="card" style="padding:0"><iframe id="oview" title="offer" style="width:100%;height:calc(100vh - 170px);border:0;border-radius:var(--radius)"></iframe></div>`;
  $("#oview").srcdoc = r.html;
};
PAGES.offertypes = async (view) => {
  const types = await api("GET", "/api/offer-types");
  const ed = can("sales.offers", "edit");
  S.H.otdel = async el => { if (!confirm(t("confirm_delete"))) return; try { await api("DELETE", "/api/offer-types/" + el.dataset.id); route(); } catch (e) { fail(e); } };
  S.H.otdup = async el => { const ty = types.find(x => String(x.id) === el.dataset.id); try { await api("POST", "/api/offer-types", { ...ty, name: ty.name + " (copy)" }); route(); } catch (e) { fail(e); } };
  view.innerHTML = `<div class="toolbar"><a class="btn" href="#/offers">← ${t("m_offers")}</a><span class="spacer"></span>${can("sales.offers", "add") ? `<a class="btn pri" href="#/offertype/new">+ ${t("new_offer_type")}</a>` : ""}</div>
    <div class="card"><div class="tbl"><table><thead><tr><th>${t("name")}</th><th>${t("doc_title")}</th><th>${t("sections")}</th><th></th></tr></thead><tbody>
    ${types.map(ty => `<tr><td><b>${esc(ty.name)}</b></td><td dir="auto">${esc(ty.title)}</td><td>${(ty.sections || []).map(s => `<span class="chip">${esc(s.heading || t("sk_" + s.kind))}</span>`).join(" ")}</td>
      <td style="white-space:nowrap">${can("sales.offers", "add") ? `<a class="btn sm" href="#/offer/new?type=${ty.id}">+ ${t("offer")}</a> <button class="btn sm" data-a="otdup" data-id="${ty.id}">${t("duplicate")}</button>` : ""}
      ${ed ? ` <a class="btn sm" href="#/offertype/${ty.id}">${t("edit")}</a> <button class="btn sm danger" data-a="otdel" data-id="${ty.id}">✕</button>` : ""}</td></tr>`).join("")}</tbody></table></div>
    <p class="muted" style="font-size:.85em">${S.lang === "ar" ? "تعديل نوع العرض يغيّر العروض الجديدة فقط؛ العروض المحفوظة تحتفظ بنصها." : "Editing a type changes future offers only; saved offers keep their own text."}</p></div>`;
};
PAGES.offertype = async (view, parts) => {
  const id = parts[1];
  const d = id === "new" ? { name: "", lang: "en", title: "OFFER : {project}", intro: "Dear {client},\n", closing: "Best regards,\n{company}",
    sections: [{ kind: "text", heading: "SCOPE", body: "" }, { kind: "pricing", heading: "PRICE", items: [{ desc: "", amount: 0 }], discount: 0, vat: 0, words: true },
      { kind: "payment", heading: "PAYMENT TERMS", milestones: [{ label: "Advance payment", pct: 50 }, { label: "On completion", pct: 50 }] }, { kind: "bank", heading: "BANK DETAILS" }] }
    : (await api("GET", "/api/offer-types")).find(x => String(x.id) === String(id));
  if (!d) { view.innerHTML = `<div class="banner err">Not found</div>`; return; }
  offerEditor(view, d, { isType: true, title: t("offer_type"), back: "#/offertypes", backLabel: t("offer_types"),
    onSave: async v => { if (!(v.name || "").trim()) throw new Error(t("name") + "?"); if (id === "new") await api("POST", "/api/offer-types", v); else await api("PUT", "/api/offer-types/" + id, v); await refreshBoot(); toast(t("saved")); location.hash = "#/offertypes"; } });
};

// --------------------------------------------------------------- reports
async function reportPage(view, name, q, fields) {
  const params = { date_from: q.date_from ?? (["balance_sheet", "payables", "general_ledger"].includes(name) ? "" : yearStart()), ...q, lang: q.lang || S.lang };
  const tb = await api("GET", `/api/reports/${name}?` + qs(params));
  view.innerHTML = `<div class="toolbar"><span class="spacer"></span><button class="btn" data-a="doprint">${t("print")}</button>${exportMenu(name, params)}</div>` +
    filterBar(params, fields) + `<div class="card"><h2>${esc(tb.title)}</h2><div class="muted" style="margin:-4px 0 10px;font-size:.85em">${esc(tb.subtitle || "")}</div>${reportTable(tb)}</div>`;
  S.H.doprint = () => openApi(`/api/reports/${name}?` + qs({ ...params, format: "pdf" }), true);
}
PAGES.report = async (view, parts, q) => {
  const name = parts[1];
  const f = { income_statement: ["dates", "compare", "bu", "detail", "allocate", "lang"], balance_sheet: ["asof", "compare", "bu", "lang"],
    cash_flow: ["dates", "compare", "bu", "lang"], trial_balance: ["dates", "bu", "lang"], bu_pnl: ["dates", "lang"], tax: ["dates", "lang"] }[name] || ["dates", "lang"];
  if (name === "balance_sheet" && q.compare && q.compare !== "none" && !q.date_from) q.date_from = yearStart();
  return reportPage(view, name, q, name === "balance_sheet" && q.compare && q.compare !== "none" ? ["dates", "compare", "bu", "lang"] : f);
};

// ------------------------------------------------------------ import/export
PAGES.data = async (view) => {
  const sheets = S.boot.enums.sheets;
  const ed = can("import_export.import", "edit");
  view.innerHTML = (STATIC ? `<div class="banner info">${S.lang === "ar" ? "هذه نسخة المتصفح: البيانات محفوظة في هذا المتصفح فقط. نزّل نسخة JSON أو Excel بانتظام كنسخة احتياطية، واستخدمها لنقل البيانات لجهاز آخر." : "Browser edition: your data is stored in this browser only. Download a JSON or Excel copy regularly as your backup — it is also how you move data to another device."}</div>` : "") +
    `<div class="grid g2"><div class="card"><h2>${t("exp_title")}</h2><div class="grid">
      <a class="btn" href="/api/export/workbook">⬇ ${t("full_wb")}</a><a class="btn" href="/api/templates/workbook">⬇ ${t("empty_tpl")}</a>
      <a class="btn" href="/api/export/json">⬇ ${t("json_backup")}</a>${can("settings.system", "edit") ? `<a class="btn" href="/api/backup">⬇ ${t("db_backup")}</a>` : ""}
      <div class="row"><select id="csvs">${sheets.map(s => `<option value="${esc(s.name)}">${esc(S.lang === "ar" ? s.ar : s.name)}</option>`).join("")}</select>
        <button class="btn" data-a="csv">${t("csv_sheet")}</button><button class="btn" data-a="csvt">${t("csv_tpl")}</button></div></div>
      <p class="muted" style="font-size:.85em">${S.lang === "ar" ? "المصنف الكامل هو نفسه قالب الاستيراد: نفس أسماء الأوراق والأعمدة، مع قوائم مالية تُحسب تلقائياً داخل Excel." : "The full workbook is also the import template: same sheet and column names, with statements that calculate inside Excel."}</p></div>
    <div class="card"><h2>${t("imp_title")}</h2>${ed ? `<div class="grid">
      <label class="f">${t("file")}<input type="file" id="impf" accept=".xlsx,.json,.csv"></label>
      <label class="f">${t("format")}<select id="impfmt"><option value="xlsx">Excel (.xlsx)</option><option value="json">JSON</option><option value="csv">CSV</option></select></label>
      <label class="f" id="impsheetl" style="display:none">CSV → <select id="impsheet">${sheets.map(s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("")}</select></label>
      <label class="f">${t("mode")}<select id="impmode"><option value="append">${t("append")}</option>${S.boot.user.is_admin ? `<option value="replace">${t("replace")}</option>` : ""}</select></label>
      <label class="f chk"><input type="checkbox" id="impdry" checked>${t("dry_run")}</label>
      <button class="btn pri" data-a="imp">${t("import_btn")}</button></div><div id="impres" style="margin-top:12px"></div>` : `<div class="muted">${t("view_only")}</div>`}</div></div>`;
  await impSheets();
  const tplCard = document.createElement("div"); tplCard.className = "card";
  tplCard.innerHTML = `<h2>${L2("Templates for every screen", "قوالب لكل شاشة")}</h2><p class="muted" style="margin-top:0">${L2("Each screen also has an ⇅ Import button. Download the layout the app fully supports, or import your own sheet and match its columns.", "كل شاشة بها زر ⇅ استيراد. حمّل التنسيق المدعوم بالكامل، أو استورد ملفك الخاص وطابق أعمدته.")}</p>
    <div class="tbl stack"><table><tbody>${(S.cache.impSheets || []).filter(x => x.name !== "Settings").map(x => `<tr><td><b>${esc(sheetLabel(x.name))}</b><div class="lines">${x.columns.filter(c => c.required).map(c => esc(S.lang === "ar" ? c.ar : c.en)).join(" · ")}</div></td>
      <td><a class="btn sm" href="${tplUrl(x.name, "xlsx")}">⬇ Excel</a> <a class="btn sm" href="${tplUrl(x.name, "csv")}">⬇ CSV</a> <a class="btn sm" href="${tplUrl(x.name, "csv", 1)}" title="${esc(L2("Filled with the demo company", "مملوء ببيانات الشركة التجريبية"))}">⬇ ${L2("Demo", "تجريبي")}</a>${canImportSheet(x.name) ? ` <button class="btn sm" data-a="impopen" data-sheet="${esc(x.name)}">⇪ ${L2("Import", "استيراد")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  view.appendChild(tplCard);
  let dm = { empty: false, loaded: "" }; try { dm = await api("GET", "/api/demo"); } catch { /* no permission */ }
  const demoCard = document.createElement("div"); demoCard.className = "card";
  demoCard.innerHTML = `<h2>${L2("Demo company", "الشركة التجريبية")}</h2>
    <p class="muted" style="margin-top:0">${L2("“Nile Demo Co.” — about six months of an invented business on every screen: projects (cost-plus, fixed price, flexible) with costs, negotiation discounts, payroll, VAT & withholding, a BOQ and invoices, stock, fixed assets, loans, cash custody, equipment rentals with a signed handover and deposit, unit sales on installments with cheques, recurring entries and bank statements (one reconciled, one to do). Use it to test every function or to train people.", "«شركة النيل التجريبية» — حوالي ستة أشهر من نشاط وهمي على كل الشاشات: مشروعات بتكاليفها وخصومات التفاوض والرواتب والضرائب والمقايسة والفواتير والمخزون والأصول والقروض والعهد وتأجير المعدات بمحضر تسليم وتأمين وبيع وحدات بالتقسيط بشيكات وقيود متكررة وكشوف بنك (واحد مسوّى وواحد للتجربة). استخدمها لتجربة كل الوظائف أو للتدريب.")}</p>
    <div class="row" style="gap:6px;flex-wrap:wrap"><a class="btn" href="/api/demo/workbook">⬇ ${L2("Demo company — Excel workbook (every sheet filled)", "الشركة التجريبية — ملف Excel (كل الأوراق مملوءة)")}</a>
    ${S.boot.user.is_admin ? (dm.empty ? `<button class="btn pri" data-a="demoload">▶ ${L2("Load the demo company into these books", "تحميل الشركة التجريبية في هذه الدفاتر")}</button>`
      : `<button class="btn danger" data-a="demorepl">${L2("Replace everything with the demo company…", "استبدال كل البيانات بالشركة التجريبية…")}</button>`) : ""}</div>
    ${dm.loaded ? `<p class="muted">${L2("Demo data loaded on", "تم تحميل البيانات التجريبية في")} ${fd(dm.loaded)} — ${L2("erase it from Settings → System when you are done.", "امسحها من الإعدادات ← النظام عند الانتهاء.")}</p>` : ""}`;
  view.appendChild(demoCard);
  S.H.demoload = async el => { el.disabled = true; el.textContent = L2("Loading…", "جارٍ التحميل…");
    try { await api("POST", "/api/demo", {}); await refreshBoot(); toast(L2("Demo company loaded", "تم تحميل الشركة التجريبية")); location.hash = "#/dashboard"; } catch (e) { fail(e); el.disabled = false; } };
  S.H.demorepl = async () => { const c = prompt(L2("This ERASES all data in these books (a backup is made first) and loads the demo company. Type DEMO to continue.", "سيتم مسح كل البيانات (بعد عمل نسخة احتياطية) وتحميل الشركة التجريبية. اكتب DEMO للمتابعة."), "");
    if (c == null) return; try { await api("POST", "/api/demo", { replace: true, confirm: c }); await refreshBoot(); toast(L2("Demo company loaded", "تم تحميل الشركة التجريبية")); location.hash = "#/dashboard"; } catch (e) { fail(e); } };
  S.H.impopen = el => importWizard(el.dataset.sheet);
  S.H.csv = () => openApi("/api/export/csv?" + qs({ sheet: $("#csvs").value }));
  S.H.csvt = () => openApi("/api/export/csv?" + qs({ sheet: $("#csvs").value, template: 1 }));
  const fmtSel = $("#impfmt");
  if (fmtSel) {
    fmtSel.addEventListener("change", () => { $("#impsheetl").style.display = fmtSel.value === "csv" ? "" : "none"; });
    $("#impf").addEventListener("change", e => { const n = (e.target.files[0] || {}).name || ""; const ext = n.split(".").pop().toLowerCase();
      if (["json", "csv"].includes(ext)) fmtSel.value = ext; else fmtSel.value = "xlsx"; fmtSel.dispatchEvent(new Event("change")); });
  }
  S.H.imp = async () => {
    const file = $("#impf").files[0]; const out = $("#impres");
    if (!file) { toast(t("file") + "?", true); return; }
    if ($("#impmode").value === "replace" && !$("#impdry").checked && !confirm(S.lang === "ar" ? "سيتم استبدال كل البيانات (مع نسخة احتياطية تلقائية). متابعة؟" : "This replaces ALL data (an automatic backup is taken first). Continue?")) return;
    out.innerHTML = `<div class="muted">${t("loading")}</div>`;
    try {
      const r = await api("POST", "/api/import?" + qs({ format: fmtSel.value, mode: $("#impmode").value, dry_run: $("#impdry").checked ? 1 : "", sheet: $("#impsheet").value }), undefined, await file.arrayBuffer());
      out.innerHTML = `<div class="banner ok">${r.dry_run ? (S.lang === "ar" ? "الفحص ناجح — لم يتم تغيير أي شيء. أزل علامة الفحص للاستيراد." : "Check passed — nothing was changed. Untick “check only” to import.") : t("saved")}</div>
        <div class="tbl"><table><tbody>${Object.entries(r.counts).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v}</td></tr>`).join("")}</tbody></table></div>
        ${(r.notes || []).map(n => `<div class="banner info" style="margin-top:6px">${esc(n)}</div>`).join("")}`;
      if (!r.dry_run) await refreshBoot();
    } catch (e) {
      const errs = (e.data && e.data.errors) || [];
      out.innerHTML = `<div class="banner err">${esc(e.message)}</div>${errs.length ? `<div class="tbl"><table><thead><tr><th>Sheet</th><th>Row</th><th>Problem</th></tr></thead><tbody>${errs.map(x => `<tr><td>${esc(x.sheet)}</td><td>${x.row}</td><td>${esc(x.error)}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
    }
  };
};

// ------------------------------------------------------ parties & taxes
// one form for every party: customers (tax & e-invoice details) and contacts (suppliers, subcontractors, drivers…)
function contactForm(p = {}, after) {
  const canDel = p.id && (can("sales.parties", "edit") || can("purchasing.contacts", "edit"));
  const sec = h => ({ html: `<h4 class="fsec">${esc(h)}</h4>`, span: "spanall" });
  drawer(p.id ? `${p.name}` : L2("New contact", "جهة اتصال جديدة"), form([
    { k: "name", l: t("name"), v: p.name, req: 1 },
    { k: "category", l: L2("Category", "التصنيف"), v: p.category || (p.id ? "" : "supplier"), list: "ccats", ph: L2("supplier, subcontractor, driver…", "مورد، مقاول باطن، سائق…") },
    { k: "contact_person", l: L2("Contact person", "الشخص المسؤول"), v: p.contact_person }, { k: "specialty", l: L2("Trade / specialty", "النشاط / التخصص"), v: p.specialty, ph: L2("e.g. steel, plumbing, aluminium", "مثال: حديد، سباكة، ألوميتال") },
    { k: "phone", l: t("phone"), v: p.phone }, { k: "phone2", l: L2("Other phone", "هاتف آخر"), v: p.phone2 },
    { k: "whatsapp", l: "WhatsApp", v: p.whatsapp, ph: L2("blank = same as phone", "فارغ = نفس الهاتف") }, { k: "email", l: t("email"), v: p.email, type: "email" },
    { k: "vehicle", l: L2("Vehicle / plate (drivers)", "المركبة / رقم اللوحة (للسائقين)"), v: p.vehicle },
    { k: "type", l: L2("In the books as", "يُعامل محاسبياً كـ"), type: "select", opts: [["", L2("Auto (from category)", "تلقائي (حسب التصنيف)")], ["customer", t("customer")], ["supplier", t("supplier")], ["both", `${t("customer")} + ${t("supplier")}`], ["employee", t("worker")], ["other", "—"]], v: p.id ? p.type : "" },
    { k: "bank_details", l: L2("Bank details (for transfers)", "بيانات البنك (للتحويل)"), type: "textarea", v: p.bank_details, span: "spanall", ph: L2("Bank, account name, account no. / IBAN, InstaPay…", "البنك، اسم الحساب، رقم الحساب / IBAN، إنستاباي…") },
    sec(L2("Address", "العنوان")),
    { k: "governate", l: t("governate"), v: p.governate }, { k: "region", l: t("region"), v: p.region },
    { k: "street", l: t("street"), v: p.street }, { k: "building", l: t("building"), v: p.building },
    sec(L2("Tax & e-invoice", "الضرائب والفاتورة الإلكترونية")),
    { k: "receiver_type", l: t("receiver_type"), type: "select", opts: ["B", "P", "F"].map(x => [x, t("rt_" + x)]), v: p.receiver_type || "B" },
    { k: "tax_id", l: t("tax_id"), v: p.tax_id, ph: "123456789" }, { k: "national_id", l: t("national_id"), v: p.national_id },
    { k: "country", l: t("country"), v: p.country || "EG" }, { k: "postal_code", l: t("postal"), v: p.postal_code },
    { k: "default_vat", l: t("default_vat"), type: "select", opts: taxOpts("vat"), v: p.default_vat }, { k: "default_wht", l: t("default_wht"), type: "select", opts: taxOpts("wht"), v: p.default_wht },
    { k: "notes", l: t("notes"), type: "textarea", v: p.notes, span: "spanall" }]) + datalist("ccats", [...Object.keys(CONTACT_CATS), ...(S.cache.ccats || [])].map(catLabel)),
  async v => { const lab = Object.fromEntries(Object.keys(CONTACT_CATS).flatMap(c => [[CONTACT_CATS[c][0].toLowerCase(), c], [CONTACT_CATS[c][1], c]]));
    v.category = lab[(v.category || "").trim().toLowerCase()] || lab[(v.category || "").trim()] || v.category;
    const r = p.id ? await api("PUT", "/api/parties-master/" + p.id, v) : await api("POST", "/api/parties-master", v);
    await refreshBoot(); toast(t("saved")); if (after) { await after(r.id); return false; } },
  { extraButtons: canDel ? `<button type="button" class="btn danger" data-a="pmdel">${t("delete")}</button>` : "",
    onOpen: f => { const c = $("[name=category]", f); if (c.value) c.value = catLabel(c.value); } });
  S.H.pmdel = () => confirmDelete(p.name, "/api/parties-master/" + p.id, refreshBoot);
}
PAGES.parties = async (view, parts, q) => {
  const lq = { ...q, limit: q.limit || 100 };
  const d = await api("GET", "/api/parties-master?" + qs(lq));
  const ed = can("sales.parties", "edit") || can("sales.invoices", "edit") || can("purchasing", "edit");
  const add = ed || can("sales.parties", "add") || can("sales.invoices", "add") || can("purchasing", "add");
  S.cache.ccats = d.categories;
  S.H.pnew = () => contactForm({ category: "customer", type: "customer" });
  S.H.pedit = el => contactForm(d.rows.find(x => String(x.id) === el.dataset.id));
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="pnew">+ ${t("new")}</button>` : ""}<span class="spacer"></span>${can("purchasing.contacts") ? `<a class="btn" href="#/contacts">☏ ${L2("Contacts directory", "دليل جهات الاتصال")}</a>` : ""}</div>` +
    filterBar(q, ["q", ["select", "type", t("type"), [["customer", t("customer")], ["supplier", t("supplier")]]]]) +
    `<div class="card">${d.rows.length ? `<div class="tbl"><table><thead><tr><th>${t("name")}</th><th>${t("type")}</th><th>${L2("Category", "التصنيف")}</th><th>${t("tax_id")}</th><th>${t("governate")}</th><th>${t("phone")}</th><th>${t("default_vat")}</th><th>${t("default_wht")}</th></tr></thead><tbody>
      ${d.rows.map(p => `<tr class="click" data-a="${ed ? "pedit" : ""}" data-id="${p.id}"><td><b>${esc(p.name)}</b></td><td>${esc(p.type)}</td><td>${esc(catLabel(p.category))}</td><td>${esc(p.tax_id || p.national_id)}</td><td>${esc(p.governate)}</td><td>${esc(p.phone)}</td><td>${esc(p.default_vat)}</td><td>${esc(p.default_wht)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}${pager(d.total, lq)}</div>`;
};
// ------------------------------------------------------ contacts directory
const contactLinks = p => { const wa = waNumber(p.whatsapp || p.phone);
  return `${p.phone ? `<a class="btn sm" href="tel:${esc(p.phone.replace(/\s/g, ""))}" title="${esc(p.phone)}">📞 ${esc(p.phone)}</a>` : ""}${wa ? `<a class="btn sm" href="https://wa.me/${wa}" target="_blank" rel="noopener">WhatsApp</a>` : ""}${p.email ? `<a class="btn sm" href="mailto:${esc(p.email)}">✉ ${esc(L2("Email", "بريد"))}</a>` : ""}`; };
PAGES.contacts = async (view, parts, q) => {
  const lq = { ...q, limit: q.limit || 200 };
  const d = await api("GET", "/api/parties-master?" + qs(lq));
  const all = q.category ? null : d;
  S.cache.ccats = d.categories;
  const add = can("purchasing.contacts", "add") || can("sales.parties", "add");
  S.H.cnew = () => contactForm({}, id => contactView(id));
  S.H.copen = el => contactView(el.dataset.id);
  const cats = [...new Set([...Object.keys(CONTACT_CATS).filter(c => c !== "customer" && c !== "employee" && c !== "other"), ...d.categories])];
  const chip = (c, l) => `<a class="chip${(q.category || "") === c ? " on" : ""}" href="#/contacts${c ? "?category=" + encodeURIComponent(c) : ""}">${esc(l)}</a>`;
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="cnew">+ ${L2("New contact", "جهة اتصال جديدة")}</button>` : ""}<span class="spacer"></span><a class="btn" href="#/payables">${t("m_payables")}</a></div>
    <div class="chips">${chip("", L2("All", "الكل"))}${cats.map(c => chip(c, catLabel(c))).join("")}</div>` +
    filterBar(q, ["q"]) +
    (d.rows.length ? `<div class="cgrid">${d.rows.map(p => `<div class="card ccard click" data-a="copen" data-id="${p.id}">
        <div class="row" style="gap:6px;align-items:baseline"><b>${esc(p.name)}</b>${p.category || p.type !== "other" ? `<span class="chip">${esc(p.category ? catLabel(p.category) : tl(p.type))}</span>` : ""}</div>
        <div class="muted">${esc([p.contact_person, p.specialty, p.vehicle, p.governate].filter(Boolean).join(" · ")) || "&nbsp;"}</div>
        <div class="row clinks" style="gap:6px;flex-wrap:wrap;margin-top:8px">${contactLinks(p) || `<span class="muted">${esc(L2("No phone saved", "لا يوجد هاتف"))}</span>`}</div></div>`).join("")}</div>${pager(d.total, lq)}`
      : `<div class="card"><div class="empty">${q.q || q.category ? t("nothing") : L2("No contacts yet. Save your suppliers, subcontractors, drivers and technicians here with their phones and bank details — they then appear in the supplier / payee drop-downs when you record an expense or a payment. You can still type a new name there at any time.", "لا توجد جهات اتصال بعد. احفظ هنا الموردين ومقاولي الباطن والسائقين والفنيين بأرقامهم وبيانات البنك — وستظهر في قائمة المورد / المستفيد عند تسجيل مصروف أو سند صرف. ويمكنك دائماً كتابة اسم جديد هناك.")}</div></div>`);
  // links inside a card must not open the card
  $$(".clinks a", view).forEach(a => a.addEventListener("click", e => e.stopPropagation()));
};
async function contactView(id) {
  const d = await api("GET", "/api/contacts/" + id), p = d.contact;
  const owe = d.balances.reduce((a, b) => a + b.we_owe, 0), owed = d.balances.reduce((a, b) => a + b.owes_us, 0);
  const ap = d.balances.filter(b => b.we_owe > 0).sort((a, b) => b.we_owe - a.we_owe)[0];
  const ed = can("purchasing.contacts", "edit") || can("sales.parties", "edit");
  const row = (l, v, ltr) => v ? `<tr><th style="width:38%">${esc(l)}</th><td ${ltr ? 'dir="ltr"' : ""} style="white-space:pre-wrap">${esc(v)}</td></tr>` : "";
  const btns = [ed ? `<button type="button" class="btn" data-a="cvedit">${t("edit")}</button>` : "",
    can("purchasing.expenses", "add") ? `<button type="button" class="btn" data-a="cvexp">+ ${t("m_expenses")}</button>` : "",
    can("purchasing.costs", "add") ? `<button type="button" class="btn" data-a="cvcost">+ ${t("m_costs")}</button>` : "",
    can("purchasing.payments", "add") ? `<button type="button" class="btn pri" data-a="cvpay">${L2("Pay", "صرف دفعة")}</button>` : ""].join("");
  drawer(p.name, `<div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:10px">${p.category ? `<span class="chip">${esc(catLabel(p.category))}</span>` : ""}${contactLinks(p)}
      ${p.bank_details ? `<button type="button" class="btn sm" data-a="cvbank">⧉ ${esc(L2("Copy bank details", "نسخ بيانات البنك"))}</button>` : ""}</div>
    <div class="kpis">${kpiBox(L2("We owe them", "مستحق لهم"), money(owe), owe > 0 ? "neg" : "")}${kpiBox(L2("They owe us", "مستحق لنا عليهم"), money(owed))}${kpiBox(L2("Bought from them", "مشتريات منهم"), money(d.spent))}${d.earned ? kpiBox(L2("Sold to them", "مبيعات لهم"), money(d.earned)) : ""}${kpiBox(L2("Vouchers", "القيود"), numf(d.vouchers))}</div>
    <div class="tbl"><table><tbody>${row(L2("Contact person", "الشخص المسؤول"), p.contact_person)}${row(L2("Trade / specialty", "النشاط / التخصص"), p.specialty)}${row(t("phone"), [p.phone, p.phone2].filter(Boolean).join(" / "), 1)}${row("WhatsApp", p.whatsapp, 1)}${row(t("email"), p.email, 1)}
      ${row(L2("Vehicle / plate", "المركبة / اللوحة"), p.vehicle)}${row(L2("Address", "العنوان"), [p.street, p.building, p.region, p.governate].filter(Boolean).join("، "))}${row(L2("Bank details", "بيانات البنك"), p.bank_details)}${row(t("tax_id"), p.tax_id || p.national_id, 1)}${row(t("notes"), p.notes)}</tbody></table></div>
    ${d.balances.length ? `<h4 class="fsec">${esc(L2("Open balances", "الأرصدة المفتوحة"))}</h4><div class="tbl"><table><thead><tr><th>${t("account")}</th><th class="num">${L2("We owe", "لهم")}</th><th class="num">${L2("Owes us", "لنا عليهم")}</th></tr></thead><tbody>${d.balances.map(b => `<tr><td>${esc(b.account)} — ${esc(aname(b))}</td><td class="num">${b.we_owe ? money(b.we_owe) : ""}</td><td class="num">${b.owes_us ? money(b.owes_us) : ""}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <h4 class="fsec">${esc(L2("Latest vouchers", "أحدث القيود"))}${d.first ? ` <span class="muted" style="font-weight:400">· ${fd(d.first)} → ${fd(d.last)}</span>` : ""}</h4>
    ${d.recent.length ? `<div class="tbl"><table><tbody>${d.recent.map(e => `<tr class="click" data-a="openv" data-id="${e.id}"><td>${fd(e.date)}</td><td>${esc(tl(e.kind))}</td><td>${esc(e.memo)}</td><td class="num">${money(e.amount)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${esc(L2("Nothing recorded with this name yet.", "لا توجد قيود بهذا الاسم بعد."))}</div>`}`,
  null, { readOnly: true, wide: true, extraButtons: btns });
  Object.assign(S.H, { cvedit: () => contactForm(p, cid => contactView(cid)), cvexp: () => openDoc("expense", null, null, { vendor: p.name }), cvcost: () => openDoc("direct_cost", null, null, { vendor: p.name }),
    cvpay: () => openDoc("payment", { party: p.name, amount: ap ? ap.we_owe : "", against: ap ? ap.account : "" }),
    cvbank: async () => { try { await navigator.clipboard.writeText(`${p.name}\n${p.bank_details}`); toast(L2("Copied", "تم النسخ")); } catch { toast(p.bank_details); } } });
}
PAGES.taxcodes = async (view) => {
  const rows = await api("GET", "/api/tax-codes");
  const ed = can("tax.codes", "edit");
  const edit = c => drawer(t("m_taxcodes"), form([{ k: "code", l: t("code"), v: c.code, req: 1 }, { k: "kind", l: t("kind_tax"), type: "select", opts: [["vat", t("vat_code")], ["wht", t("wht_code")]], v: c.kind || "vat" },
    { k: "name", l: t("name") + " (English)", v: c.name, req: 1 }, { k: "name_ar", l: t("name") + " (العربية)", v: c.name_ar },
    { k: "rate", l: t("rate_pct"), type: "number", v: c.rate != null ? +(c.rate * 100).toFixed(4) : "" }, { k: "eta_type", l: t("eta_type"), v: c.eta_type, ph: "T1 / T4" },
    { k: "eta_subtype", l: t("eta_sub"), v: c.eta_subtype, ph: "V009 / W001" }, { k: "active", l: t("active"), type: "checkbox", v: c.active !== 0 }]),
  async v => { v.rate = (+v.rate || 0) / 100; if (c.code) await api("PUT", "/api/tax-codes/" + encodeURIComponent(c.code), v); else await api("POST", "/api/tax-codes", v); await refreshBoot(); toast(t("saved")); },
  { extraButtons: c.code && ed ? `<button type="button" class="btn danger" data-a="tdel">${t("delete")}</button>` : "" });
  S.H.tdel = () => {};
  S.H.tnew = () => edit({ active: 1 });
  S.H.tedit = el => { const c = rows.find(x => x.code === el.dataset.id); edit(c);
    S.H.tdel = () => confirmDelete(c.code, "/api/tax-codes/" + encodeURIComponent(c.code), refreshBoot); };
  view.innerHTML = `<div class="toolbar">${can("tax.codes", "add") ? `<button class="btn pri" data-a="tnew">+ ${t("new")}</button>` : ""}</div>
    <div class="banner info">${S.lang === "ar" ? "النسب قابلة للتعديل — راجع النسب السارية مع مستشارك الضريبي." : "Rates are editable — confirm current rates with your tax adviser."}</div>
    <div class="card"><div class="tbl"><table><thead><tr><th>${t("code")}</th><th>${t("name")}</th><th>${t("kind_tax")}</th><th class="num">${t("rate_pct")}</th><th>${t("eta_type")}</th><th>${t("eta_sub")}</th></tr></thead><tbody>
    ${rows.map(c => `<tr class="click ${c.active ? "" : "void"}" data-a="${ed ? "tedit" : ""}" data-id="${esc(c.code)}"><td><b>${esc(c.code)}</b></td><td>${esc(S.lang === "ar" && c.name_ar ? c.name_ar : c.name)}</td><td>${c.kind === "vat" ? t("vat_code") : t("wht_code")}</td><td class="num">${pct(c.rate)}</td><td>${esc(c.eta_type)}</td><td>${esc(c.eta_subtype)}</td></tr>`).join("")}</tbody></table></div></div>`;
};
PAGES.einvoice = async (view, parts, q) => {
  const [st, inv] = await Promise.all([api("GET", "/api/einvoice/settings"), can("sales.invoices") || can("tax.einvoice") ? api("GET", "/api/extracts?" + qs(q)) : []]);
  const drafts = inv.filter(x => x.status !== "posted").length;
  const ed = can("tax.einvoice", "edit");
  S.H.etasave = async () => { try { await api("PUT", "/api/einvoice/settings", readForm($("#etaset"))); toast(t("saved")); route(); } catch (e) { fail(e); } };
  S.H.etatest = async () => { try { await api("POST", "/api/einvoice/test", {}); toast("✓ ETA"); } catch (e) { fail(e); } };
  S.H.etasel = async () => { const ids = $$("input.etachk:checked").map(i => +i.value); if (!ids.length) return;
    try { const r = await api("POST", "/api/einvoice/submit", { ids }); toast(r.results.map(x => `#${x.id} ${x.status}`).join(" · ")); route(); } catch (e) { fail(e); } };
  view.innerHTML = `<div class="grid g2"><div class="card"><h2>${t("eta_batch_json").split("—")[0]}</h2>
      <p class="muted">${S.lang === "ar" ? "الطريقة ١ — التوليد للاستيراد: نزّل المستندات بصيغة المنظومة (JSON) أو ملف Excel، ثم ارفعها عبر بوابة المنظومة أو سلّمها لمزود الخدمة." : "Option 1 — generate for import: download the documents in ETA format (JSON) or an Excel batch, then upload them through the ETA portal or hand them to your integrator."}</p>
      ${filterBar(q, ["dates", "bu"])}
      <div class="row"><a class="btn" href="/api/einvoice-batch?${qs({ ...q, format: "json" })}">⬇ ${t("eta_batch_json")}</a><a class="btn" href="/api/einvoice-batch?${qs({ ...q, format: "xlsx" })}">⬇ ${t("eta_batch_xlsx")}</a></div></div>
    <div class="card" id="etaset"><h2>${t("eta_setup")}</h2><p class="muted">${S.lang === "ar" ? "الطريقة ٢ — الربط المباشر: يرسل النظام الفواتير الموقعة إلى المنظومة ويتابع حالتها. التوقيع يتم عبر خدمة توقيع محلية متصلة بالتوكن." : "Option 2 — direct link: the app signs through your local signing service (USB token) and submits to the ETA API, then tracks status."}</p>
      ${form([{ k: "eta_env", l: t("eta_env"), type: "select", opts: [["preprod", "Pre-production (testing)"], ["prod", "Production"]], v: st.eta_env },
        { k: "eta_doc_version", l: t("eta_version"), type: "select", opts: [["1.0", "1.0 (signed)"], ["0.9", "0.9 (unsigned — pre-production only)"]], v: st.eta_doc_version },
        { k: "eta_client_id", l: t("eta_client_id"), v: st.eta_client_id }, { k: "eta_client_secret", l: t("eta_client_secret"), type: "password", v: st.eta_client_secret },
        { k: "eta_signer_url", l: t("eta_signer"), v: st.eta_signer_url, ph: "http://127.0.0.1:18088/sign", span: "spanall" },
        { k: "eta_activity_code", l: t("eta_activity"), v: st.eta_activity_code, ph: "4330" }, { k: "eta_branch_id", l: t("eta_branch"), v: st.eta_branch_id },
        { k: "eta_default_item_code", l: t("eta_item_code"), v: st.eta_default_item_code, ph: "EG-123456789-1001" }, { k: "eta_default_unit", l: t("eta_unit"), v: st.eta_default_unit },
        { html: `<b>${t("eta_issuer")}</b>`, span: "spanall" }, { k: "eta_governate", l: t("governate"), v: st.eta_governate }, { k: "eta_region", l: t("region"), v: st.eta_region },
        { k: "eta_street", l: t("street"), v: st.eta_street }, { k: "eta_building", l: t("building"), v: st.eta_building }, { k: "eta_postal_code", l: t("postal"), v: st.eta_postal_code }])}
      ${ed ? `<div class="row" style="margin-top:10px"><button class="btn pri" data-a="etasave">${t("save")}</button><button class="btn" data-a="etatest">${t("eta_test")}</button></div>` : ""}
      <p class="muted" style="font-size:.8em">${S.lang === "ar" ? "رقم التسجيل الضريبي للشركة يُضبط في الإعدادات ← الشركة." : "Your company's tax registration number is set in Settings → Company."}</p></div></div>
    <div class="card"><h2>${t("m_invoices")}</h2>${inv.length ? `<div class="tbl"><table><thead><tr><th></th><th>${t("invoice_no")}</th><th>${t("customer")}</th><th>${t("date")}</th><th class="num">${t("amount")}</th><th>${t("eta_status")}</th></tr></thead><tbody>
      ${inv.map(x => { const draft = x.status !== "posted"; return `<tr class="${draft ? "void" : ""}"><td>${ed ? `<input type="checkbox" class="etachk" value="${x.id}" ${draft || /valid|submitted/i.test(x.eta_status || "") ? "disabled" : ""}>` : ""}</td><td><a href="#/invoice/${x.id}">${esc(x.number)}</a></td><td>${esc(x.customer)}</td><td>${fd(x.date)}</td><td class="num">${money(x.total)}</td>
        <td>${draft ? `<span class="chip warn">${L2("Draft — post it first", "مسودة — يجب ترحيلها أولاً")}</span> <a class="btn sm" href="#/invoice/${x.id}">${L2("Open", "فتح")}</a>` : esc(x.eta_status || "—")}</td></tr>`; }).join("")}</tbody></table></div>
      ${drafts ? `<p class="muted" style="margin-top:8px">${L2("The Tax Authority only accepts issued invoices: open a draft and press “Save & post”, then it can be sent or included in the batch files.", "منظومة الضرائب تقبل الفواتير المُصدرة فقط: افتح المسودة واضغط «حفظ وترحيل»، بعدها يمكن إرسالها أو إضافتها لملفات الدفعة.")}</p>` : ""}
      ${ed ? `<div class="row" style="margin-top:10px"><button class="btn pri" data-a="etasel">${t("eta_submit")}</button></div>` : ""}` : `<div class="empty">${t("nothing")}</div>`}</div>`;
  if (!ed) $$("#etaset input, #etaset select").forEach(e => { e.disabled = true; });
};

// --------------------------------------------------------------- settings
// Image helpers (browser canvas): the server only stores and embeds the results.
const Img = {
  load: src => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error(S.lang === "ar" ? "تعذرت قراءة الصورة" : "That file is not an image this browser can read")); i.src = src; }),
  src: im => `data:${im.mime};base64,${im.data}`,
  out: (cv, png = true) => { const url = cv.toDataURL(png ? "image/png" : "image/jpeg", 0.9); return { mime: png ? "image/png" : "image/jpeg", w: cv.width, h: cv.height, data: url.split(",")[1] }; },
  async fit(file, max = 900) {
    const url = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => rej(r.error); r.readAsDataURL(file); });
    const img = await Img.load(url), k = Math.min(1, max / Math.max(img.width, img.height));
    const cv = document.createElement("canvas"); cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k);
    const g = cv.getContext("2d"), png = /png|gif|webp|svg/i.test(file.type);
    if (!png) { g.fillStyle = "#fff"; g.fillRect(0, 0, cv.width, cv.height); }
    g.drawImage(img, 0, 0, cv.width, cv.height); return Img.out(cv, png);
  },
  async clean(im) {   // near-white paper becomes transparent (soft edge), coloured ink is kept
    const img = await Img.load(Img.src(im)), cv = document.createElement("canvas"); cv.width = img.width; cv.height = img.height;
    const g = cv.getContext("2d"); g.drawImage(img, 0, 0); const px = g.getImageData(0, 0, cv.width, cv.height), d = px.data;
    for (let i = 0; i < d.length; i += 4) { const mn = Math.min(d[i], d[i + 1], d[i + 2]), mx = Math.max(d[i], d[i + 1], d[i + 2]); if (mx - mn > 24) continue;
      d[i + 3] = Math.round(d[i + 3] * (mn >= 246 ? 0 : mn <= 226 ? 1 : (246 - mn) / 20)); }
    g.putImageData(px, 0, 0); return Img.out(cv);
  },
  async watermark(b, images, company) {   // faded (and greyed) logo / image, or diagonal text — transparent PNG
    const w = b.wm, op = Math.max(0.03, Math.min(0.6, Number(w.opacity) || 0.16));
    if (w.mode === "text") {
      const text = (w.text || company || "").trim(); if (!text) return null;
      const W = 1200, cv = document.createElement("canvas"); cv.width = W; cv.height = W; const g = cv.getContext("2d");
      g.translate(W / 2, W / 2); g.rotate(-Math.PI / 4); let size = 170; g.font = `bold ${size}px Arial, sans-serif`;
      while (g.measureText(text).width > W * 1.2 && size > 30) { size -= 6; g.font = `bold ${size}px Arial, sans-serif`; }
      g.globalAlpha = op; g.fillStyle = w.grey ? "#808080" : b.dark; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, 0, 0); return Img.out(cv);
    }
    const src = w.mode === "image" ? images.wm_image : w.mode === "logo" ? images.logo : null; if (!src) return null;
    const img = await Img.load(Img.src(src)), W = 900, H = Math.round(900 * img.height / img.width);
    const cv = document.createElement("canvas"); cv.width = W; cv.height = H; const g = cv.getContext("2d"); g.drawImage(img, 0, 0, W, H);
    const px = g.getImageData(0, 0, W, H), d = px.data;
    for (let i = 0; i < d.length; i += 4) { const ink = Math.min(1, (255 - Math.min(d[i], d[i + 1], d[i + 2])) / 110);
      if (w.grey) { const y = Math.round(0.3 * d[i] + 0.59 * d[i + 1] + 0.11 * d[i + 2]); d[i] = d[i + 1] = d[i + 2] = Math.min(200, y); }
      d[i + 3] = Math.round((d[i + 3] / 255) * ink * op * 255); }
    g.putImageData(px, 0, 0); return Img.out(cv);
  },
};
const FONTS = ["system", "Arial", "Tahoma", "Segoe UI", "Calibri", "Verdana", "Helvetica", "Times New Roman", "Georgia", "Noto Sans Arabic", "Noto Naskh Arabic", "Cairo", "Traditional Arabic", "Courier New"];
const DOC_FONTS = ["Arial", "Calibri", "Tahoma", "Segoe UI", "Times New Roman", "Georgia", "Cairo", "Traditional Arabic", "Bodoni MT Black", "Bodoni MT", "Garamond", "Century Gothic"];

PAGES.settings = async (view, parts, q) => {
  const tabs = [["company", "tab_company", can("settings.company")], ["docs", "tab_docs", can("settings.branding")], ["look", "tab_look", true],
    ["menus", "tab_menus", can("settings.menus")], ["system", "tab_system", can("settings.system") || S.boot.user.is_admin],
    ["workspace", "tab_workspace", S.boot.user.is_admin || cloudOn()]].filter(x => x[2]);
  const tab = tabs.find(x => x[0] === q.tab) ? q.tab : tabs[0][0];
  const head = `<div class="tabs">${tabs.map(([k, l]) => `<a href="#/settings?tab=${k}" class="${k === tab ? "on" : ""}">${t(l)}</a>`).join("")}</div>`;
  const st = S.boot.settings;
  if (tab === "company") {
    const ed = can("settings.company", "edit"), rg = st.regional || {};
    view.innerHTML = head + `<div id="cocard"><div class="card"><h2>${t("company")}</h2>${form([
      { k: "company_name", l: t("company_name"), v: st.company_name }, { k: "company_legal_name", l: t("legal_name"), v: st.company_legal_name },
      { k: "company_tagline", l: t("tagline"), v: st.company_tagline }, { k: "company_tax_id", l: t("tax_id"), v: st.company_tax_id },
      { k: "company_cr_no", l: t("cr_no"), v: st.company_cr_no }, { k: "company_address", l: t("address"), v: st.company_address },
      { k: "company_city", l: t("city"), v: st.company_city }, { k: "company_phone", l: t("phone"), v: st.company_phone },
      { k: "company_email", l: t("email"), v: st.company_email }, { k: "company_website", l: t("website"), v: st.company_website },
      { k: "currency", l: t("currency"), v: st.currency }, { k: "language", l: t("default_lang"), type: "select", opts: [["en", "English"], ["ar", "العربية"]], v: st.language || "en" },
      { k: "font_family", l: t("font_family") + " (" + t("default") + ")", type: "select", opts: FONTS.map(f => [f, f]), v: st.font_family || "system" },
      { k: "font_size", l: t("font_size") + " (" + t("default") + ")", type: "number", v: st.font_size || 15 },
      { k: "units", l: t("units"), v: (st.units || []).join(", "), span: "span2" }], 4)}</div>
      <div class="card"><h2>${t("bank_details")}</h2><p class="muted" style="margin-top:-4px">${L2("Add every account you use. The default one goes on new offers; each offer can pick another or enter its own.", "أضف كل الحسابات التي تستخدمها. الافتراضي يظهر في العروض الجديدة، ويمكن لكل عرض اختيار حساب آخر أو إدخال بيانات خاصة.")}</p><div id="banks"></div>
        ${ed ? `<button class="btn sm" type="button" data-a="bankadd">+ ${L2("Add bank account", "إضافة حساب بنكي")}</button>` : ""}</div>
      <div class="card"><h2>${t("numbering")}</h2>${form([{ k: "offer_prefix", l: t("offer_prefix"), v: st.offer_prefix || "OFR-", help: "{YYYY} = year" },
        { k: "offer_digits", l: t("offer_digits"), type: "number", v: st.offer_digits || 4 }, { k: "offer_validity_days", l: t("offer_validity"), type: "number", v: st.offer_validity_days || 15 }], 3)}</div>
      <div class="card" id="regcard"><h2>${t("regional")}</h2>${form([
        { k: "date_format", l: t("date_fmt"), type: "select", opts: [["YYYY-MM-DD", "2026-07-21"], ["DD/MM/YYYY", "21/07/2026"], ["DD MMM YYYY", "21 Jul 2026"]], v: rg.date_format || "YYYY-MM-DD" },
        { k: "negative", l: t("neg_style"), type: "select", opts: [["paren", "(1,234.00)"], ["minus", "-1,234.00"]], v: rg.negative || "paren" },
        { k: "decimals", l: t("decimals"), type: "select", opts: [["2", "1,234.56"], ["0", "1,235"]], v: String(rg.decimals ?? "2") },
        { k: "digits", l: t("digits"), type: "select", opts: [["latin", t("dg_latin")], ["arabic", t("dg_arabic")]], v: rg.digits || "latin" }], 4)}</div>
      ${ed ? `<div class="row"><button class="btn pri" data-a="cosave">${t("save")}</button></div>` : ""}</div>`;
    let banks = (st.banks || []).map(b => ({ ...b })), bankDefault = st.bank_default || (banks[0] || {}).id || "";
    const drawBanks = () => { $("#banks").innerHTML = banks.length ? banks.map((b, i) => `<div class="sec-ed" style="margin-bottom:10px"><div class="row" style="margin-bottom:6px">
        <label class="f chk" style="margin:0"><input type="radio" name="bank_def_pick" data-skip="1" data-change="bankdef" data-i="${i}" ${b.id === bankDefault ? "checked" : ""}>${L2("Default", "الافتراضي")}</label><span class="spacer"></span>
        ${ed ? `<button class="btn sm danger" type="button" data-a="bankdel" data-i="${i}">✕</button>` : ""}</div>
        <div class="grid g3">${BANK_F().map(([k, l, ph]) => `<label class="f">${esc(l)}<input dir="auto" data-skip="1" value="${esc(b[k] || "")}" ${ph ? `placeholder="${esc(ph)}"` : ""} data-input="bankf" data-i="${i}" data-k="${k}"></label>`).join("")}</div></div>`).join("")
        : `<div class="empty">${L2("No bank accounts yet.", "لا توجد حسابات بنكية بعد.")}</div>`;
      if (!ed) $$("#banks input").forEach(e => { e.disabled = true; }); };
    S.H.bankadd = () => { const id = "b" + Date.now().toString(36); banks.push({ id, label: "", bank: "", acct: "", holder: "", swift: "", iban: "" }); if (!bankDefault) bankDefault = id; drawBanks(); };
    S.H.bankdel = el => { const [gone] = banks.splice(+el.dataset.i, 1); if (gone.id === bankDefault) bankDefault = (banks[0] || {}).id || ""; drawBanks(); };
    S.H.bankdef = el => { bankDefault = banks[+el.dataset.i].id; };
    S.H.bankf = el => { banks[+el.dataset.i][el.dataset.k] = el.value; };
    drawBanks();
    if (!ed) $$("#cocard input, #cocard select").forEach(e => { e.disabled = true; });
    S.H.cosave = async () => { const v = readForm($("#cocard")); const regional = {}; v.banks = banks; v.bank_default = bankDefault;
      ["date_format", "negative", "decimals", "digits"].forEach(k => { regional[k] = v[k]; delete v[k]; });
      v.regional = regional; v.units = v.units.split(",").map(x => x.trim()).filter(Boolean);
      try { await api("PUT", "/api/settings", v);
        if (cloudOn() && v.company_name) await CloudAPI.renameCompany(v.company_name.trim()).catch(() => {});  // the name people pick at sign-in
        await refreshBoot(); applyLook(); renderShell(); toast(t("saved")); route(); } catch (e) { fail(e); } };
    return;
  }
  if (tab === "docs") return brandingTab(view, head);
  if (tab === "look") {
    const ap = st.appearance || {}, ed = can("settings.branding", "edit");
    view.innerHTML = head + `<div class="card"><h2>${t("my_view")}</h2><div class="grid g4">
        <label class="f">${t("language")}<select id="my_lang"><option value="en">English</option><option value="ar" ${S.lang === "ar" ? "selected" : ""}>العربية</option></select></label>
        <label class="f">${t("font_family")}<select id="my_font"><option value="">${t("default")}</option>${FONTS.map(f => `<option ${store.get("font", "") === f ? "selected" : ""}>${f}</option>`).join("")}</select></label>
        <label class="f">${t("font_size")} <input id="my_size" type="range" min="11" max="22" value="${store.get("fontSize", "") || st.font_size || 15}"></label>
        <label class="f">${t("theme")}<select id="my_theme">${["auto", "light", "dark"].map(x => `<option ${store.get("theme", "auto") === x ? "selected" : ""}>${x}</option>`).join("")}</select></label></div>
        <div class="row" style="margin-top:10px"><button class="btn" data-a="mysave">${t("save")}</button><button class="btn" data-a="myreset">${t("clear")}</button></div></div>
      ${can("settings.branding") ? `<div class="card" id="apcard"><h2>${t("appearance")} — ${S.lang === "ar" ? "لكل المستخدمين" : "for everyone"}</h2>${form([
        { k: "app_name", l: t("app_name"), v: ap.app_name || "", ph: (st.company_name || "BEYOND") },
        { k: "logo", l: t("app_logo"), type: "select", opts: [["", t("al_none")], ["document", t("al_doc")], ["custom", t("al_custom")]], v: ap.logo || "" },
        { k: "brand_show", l: S.lang === "ar" ? "أعلى القائمة الجانبية يعرض" : "Sidebar top shows", type: "select", v: ap.brand_show || "logo_name",
          opts: [["logo_name", S.lang === "ar" ? "الشعار والاسم" : "Logo and name"], ["logo", S.lang === "ar" ? "الشعار فقط" : "Logo only"], ["name", S.lang === "ar" ? "الاسم فقط" : "Name only"]] },
        { k: "tagline", l: S.lang === "ar" ? "الوصف تحت الاسم" : "Tagline under the name", v: ap.tagline != null ? ap.tagline : (st.company_tagline || ""), ph: S.lang === "ar" ? "مثال: تصميم وتشطيب داخلي" : "e.g. Interior Design & Finishing" },
        { k: "tagline_show", l: S.lang === "ar" ? "إظهار الوصف" : "Show the tagline", type: "checkbox", v: ap.tagline_show !== false },
        { html: `<label class="f">${t("accent_col")}<span class="swatch"><input type="color" name="accent" value="${esc(ap.accent || "#1f3a5f")}"><button type="button" class="btn sm" data-a="apnoacc">${t("default")}</button></span></label>` },
        { k: "sidebar", l: t("sidebar"), type: "select", opts: [["light", t("sb_light")], ["dark", t("sb_dark")], ["brand", t("sb_brand")]], v: ap.sidebar || "light" },
        { k: "density", l: t("density"), type: "select", opts: [["comfortable", t("d_comfortable")], ["compact", t("d_compact")]], v: ap.density || "comfortable" },
        { k: "corners", l: t("corners"), type: "select", opts: [["rounded", t("cr_rounded")], ["square", t("cr_square")]], v: ap.corners || "rounded" },
        { html: `<label class="f">${t("app_logo")} — ${t("al_custom")}<input type="file" id="aplogo" accept="image/*"></label>` }], 3)}
        ${ed ? `<div class="row" style="margin-top:10px"><button class="btn pri" data-a="apsave">${t("save")}</button></div>` : ""}</div>` : ""}`;
    S.H.mysave = () => { store.set("lang", $("#my_lang").value); store.set("font", $("#my_font").value); store.set("fontSize", $("#my_size").value); store.set("theme", $("#my_theme").value); applyLook(); renderShell(); route(); };
    S.H.myreset = () => { ["font", "fontSize"].forEach(k => store.set(k, "")); store.set("theme", "auto"); applyLook(); renderShell(); route(); };
    let noAccent = !ap.accent;
    S.H.apnoacc = () => { noAccent = true; toast(t("default")); };
    const ac = $("[name=accent]"); if (ac) ac.addEventListener("input", () => { noAccent = false; });
    S.H.apsave = async () => { const v = readForm($("#apcard")); if (noAccent) v.accent = "";
      try { const f = $("#aplogo").files[0]; if (f) await api("PUT", "/api/branding/image/app_logo", await Img.fit(f, 300));
        await api("PUT", "/api/settings", { appearance: v }); await refreshBoot(); applyLook(); renderShell(); route(); toast(t("saved")); } catch (e) { fail(e); } };
    return;
  }
  if (tab === "workspace") return workspaceTab(view, head);
  if (tab === "menus") {
    const menuKeys = window.MENU.flatMap(([g, items]) => [g, ...items.map(i => i[0])]);
    const labels = st.menu_labels || {}, ed = can("settings.menus", "edit");
    view.innerHTML = head + entryOptionCards() + `<div class="card"><h2>${t("menu_names")}</h2><p class="muted">${t("menu_note")}</p><div class="tbl"><table><thead><tr><th>Key</th><th>English</th><th>العربية</th><th>Custom English</th><th>مخصص عربي</th></tr></thead><tbody>
      ${menuKeys.map(k => `<tr><td class="muted">${k}</td><td>${esc(window.I18N[k][0])}</td><td>${esc(window.I18N[k][1])}</td><td><input data-ml="${k}" data-l="en" value="${esc((labels[k] || {}).en || "")}" ${ed ? "" : "disabled"}></td><td><input data-ml="${k}" data-l="ar" value="${esc((labels[k] || {}).ar || "")}" dir="rtl" ${ed ? "" : "disabled"}></td></tr>`).join("")}</tbody></table></div>
      ${ed ? `<div class="row" style="margin-top:10px"><button class="btn pri" data-a="mlsave">${t("save")}</button></div>` : ""}</div>`;
    entryOptionWire(view);
    S.H.mlsave = async () => { const out = {}; $$("[data-ml]").forEach(i => { if (i.value.trim()) { out[i.dataset.ml] = out[i.dataset.ml] || {}; out[i.dataset.ml][i.dataset.l] = i.value.trim(); } });
      try { await api("PUT", "/api/settings", { menu_labels: out }); await refreshBoot(); renderShell(); route(); toast(t("saved")); } catch (e) { fail(e); } };
    return;
  }
  // system & data
  const sys = can("settings.system") ? await api("GET", "/api/system").catch(() => null) : null;
  const ed = can("settings.system", "edit");
  view.innerHTML = head + (sys ? `<div class="card"><h2>${t("system_info")}</h2><div class="grid g2"><div>Version ${esc(sys.version)}<br><span class="muted">${esc(sys.db_path)}</span><br>${(sys.db_size / 1048576).toFixed(2)} MB</div>
      <div>${Object.entries(sys.counts).map(([k, v]) => `${esc(k)}: ${v}`).join(" · ")}<br><span class="muted">${sys.backups.length} backups: ${esc(sys.backups.slice(0, 3).join(", "))}</span></div></div>
      ${ed ? `<div class="row" style="margin-top:10px"><label class="f">${t("backups_keep")}<input id="bkeep" type="number" value="${esc(st.backup_keep || 14)}" style="max-width:100px"></label><button class="btn" data-a="maint">${t("maintenance")}</button><a class="btn" href="/api/backup">⬇ ${t("db_backup")}</a></div>` : ""}</div>
    <div class="card" id="lockcard"><h2>${t("lock_date")}</h2><div class="row"><input type="date" name="lock_date" value="${esc(st.lock_date || "")}" style="max-width:200px" ${ed ? "" : "disabled"}>${ed ? `<button class="btn" data-a="locksave">${t("save")}</button>` : ""}</div><p class="muted">${t("lock_note")}</p></div>
    <div class="card"><h2>${t("hidden_mods")}</h2><p class="muted">${t("hidden_moved")} <a href="#/settings?tab=menus">${t("tab_menus")}</a></p></div>` : "") +
    (S.boot.user.is_admin ? `<div class="card danger-zone" id="erasecard"><h2 class="neg">${t("erase_title")}</h2>
      <div class="banner ${STATIC ? "err" : "info"}">${STATIC ? t("erase_static") + ` <a href="/api/export/json">⬇ JSON</a>` : t("erase_note")}</div>
      <label class="f chk"><input type="radio" name="escope" value="transactions" checked>${t("erase_tx")}</label>
      <label class="f chk"><input type="radio" name="escope" value="all">${t("erase_all")}</label>
      <div class="row" style="margin-top:10px"><input id="econfirm" placeholder="${t("erase_type")}" style="max-width:220px" autocomplete="off"><button class="btn danger" data-a="erase">${t("erase_btn")}</button></div></div>`
      : `<div class="card"><h2>${t("erase_title")}</h2><div class="muted">${t("admin_only")}</div></div>`);
  S.H.maint = async () => { try { await api("PUT", "/api/settings", { backup_keep: $("#bkeep").value }); const r = await api("POST", "/api/maintenance", {}); toast(`${(r.size_before / 1048576).toFixed(2)} → ${(r.size_after / 1048576).toFixed(2)} MB`); route(); } catch (e) { fail(e); } };
  S.H.locksave = async () => { try { await api("PUT", "/api/settings", { lock_date: $("[name=lock_date]").value }); await refreshBoot(); toast(t("saved")); } catch (e) { fail(e); } };
  S.H.erase = async () => {
    const scope = ($("input[name=escope]:checked") || {}).value, word = $("#econfirm").value;
    if (word.trim().toUpperCase() !== "ERASE") { toast(t("erase_type"), true); return; }
    if (!confirm(scope === "all" ? t("erase_all") + "?" : t("erase_tx") + "?")) return;
    try { const r = await api("POST", "/api/erase", { scope, confirm: word }); await refreshBoot(); renderShell(); toast(`${t("erase_done")} ${r.backup}`); location.hash = "#/dashboard"; } catch (e) { fail(e); }
  };
};

async function brandingTab(view, head) {
  const b = await api("GET", "/api/branding");
  const ed = can("settings.branding", "edit");
  const imgs = b.images;
  const imgTag = im => im ? `<img src="${Img.src(im)}" alt="">` : `<span class="muted">—</span>`;
  const col = (k, l) => `<label class="f">${t(l)}<span class="swatch"><input type="color" name="${k}" value="${esc(b[k])}"><code>${esc(b[k])}</code></span></label>`;
  view.innerHTML = head + `<div class="split"><div id="brcard">
    <div class="card"><h2>${t("logo")}</h2><div class="imgbox" id="logobox">${imgTag(imgs.logo)}</div>
      <div class="grid g3" style="margin-top:10px"><label class="f">${t("upload")}<input type="file" id="logofile" accept="image/*"></label>
        <label class="f chk"><input type="checkbox" name="clean_bg" ${b.clean_bg ? "checked" : ""}>${t("clean_bg")}</label>
        <label class="f">${t("logo_height")}<input type="number" name="logo_height" min="40" max="170" value="${esc(b.logo_height)}"></label></div>
      ${ed && imgs.logo ? `<button class="btn sm danger" data-a="logorm" style="margin-top:8px">${t("remove")}</button>` : ""}</div>
    <div class="card"><h2>${t("watermark")}</h2><div class="grid g3">
      <label class="f">${t("watermark")}<select name="wm.mode">${["none", "logo", "image", "text"].map(m => `<option value="${m}" ${b.wm.mode === m ? "selected" : ""}>${t("wm_" + m)}</option>`).join("")}</select></label>
      <label class="f">${t("wm_strength")} <input type="range" name="wm.opacity" min="4" max="40" value="${Math.round(b.wm.opacity * 100)}"></label>
      <label class="f chk"><input type="checkbox" name="wm.grey" ${b.wm.grey ? "checked" : ""}>${t("wm_grey")}</label>
      <label class="f span2">${t("wm_textv")}<input name="wm.text" value="${esc(b.wm.text)}" placeholder="${esc(S.boot.settings.company_name || "")}"></label>
      <label class="f">${t("wm_upload")}<input type="file" id="wmfile" accept="image/*"></label></div>
      <div class="imgbox" style="margin-top:10px">${imgTag(imgs.watermark)}${imgs.wm_image ? imgTag(imgs.wm_image) : ""}</div></div>
    <div class="card"><h2>${t("apply_to")}</h2><div class="tbl"><table class="perm"><thead><tr><th>${t("file_kind")}</th><th class="c">${t("logo")}</th><th class="c">${t("watermark")}</th></tr></thead><tbody>
      ${b.kinds.map(k => `<tr><td>${esc(S.lang === "ar" ? k.ar : k.en)}</td><td class="c"><input type="checkbox" data-apply="${k.key}" data-f="logo" ${b.apply[k.key].logo ? "checked" : ""}></td><td class="c"><input type="checkbox" data-apply="${k.key}" data-f="wm" ${b.apply[k.key].wm ? "checked" : ""}></td></tr>`).join("")}
      </tbody></table></div></div>
    <div class="card"><h2>${t("colours")}</h2><div class="grid g4">${col("dark", "c_dark")}${col("accent", "c_accent")}${col("light", "c_light")}${col("text", "c_text")}</div>
      <div class="grid g4" style="margin-top:10px"><label class="f">${t("doc_font")}<input name="font" list="dfonts" value="${esc(b.font)}"></label><label class="f">${t("sig_font")}<input name="sig_font" list="dfonts" value="${esc(b.sig_font)}"></label>
        <label class="f">${t("cur_words")} (EN)<input name="cur_words" value="${esc(b.cur_words)}"></label><label class="f">${t("cur_fraction")} (EN)<input name="cur_fraction" value="${esc(b.cur_fraction)}"></label>
        <label class="f">${t("cur_words")} (ع)<input name="cur_words_ar" dir="rtl" value="${esc(b.cur_words_ar)}"></label><label class="f">${t("cur_fraction")} (ع)<input name="cur_fraction_ar" dir="rtl" value="${esc(b.cur_fraction_ar)}"></label></div>
      ${datalist("dfonts", DOC_FONTS)}</div>
    <div class="card"><h2>${t("contact_block")}</h2><div class="grid g2">
      <label class="f">${t("c_name")}<input name="contact.name" value="${esc(b.contact.name)}"></label><label class="f">${t("c_title")}<input name="contact.title" value="${esc(b.contact.title)}"></label>
      <label class="f">${t("phones")}<textarea name="contact.phones" rows="2">${esc(b.contact.phones)}</textarea></label><label class="f">${t("address")}<textarea name="contact.address" rows="2" dir="auto">${esc(b.contact.address)}</textarea></label>
      <label class="f">${t("email")}<input name="contact.email" value="${esc(b.contact.email)}"></label><label class="f">${t("website")}<input name="contact.website" value="${esc(b.contact.website)}"></label>
      <label class="f spanall">${t("footer_line")}<input name="footer" value="${esc(b.footer)}"></label></div></div>
    <div class="card"><h2>${t("boq_notes")}</h2><textarea name="boq_notes" rows="6" dir="rtl">${esc(b.boq_notes)}</textarea></div>
    ${ed ? `<div class="row"><button class="btn pri" data-a="brsave">${t("save")}</button></div>` : ""}</div>
    <div class="pv">${can("sales.offers") ? `<div class="row" style="margin-bottom:6px"><span class="muted">${t("preview")}</span><span class="spacer"></span><select id="pvtype"></select></div><iframe id="brpv" title="preview"></iframe>` : ""}</div></div>`;
  if (!ed) $$("#brcard input, #brcard select, #brcard textarea").forEach(e => { e.disabled = true; });
  let logoRaw = imgs.logo_raw || null, logo = imgs.logo || null, wmImage = imgs.wm_image || null, dirtyImgs = false;
  const collect = () => { const v = { contact: {}, wm: {}, apply: {} };
    $$("#brcard [name]").forEach(el => { const val = el.type === "checkbox" ? el.checked : el.value; const [a, c] = el.name.split(".");
      if (c) v[a][c] = val; else v[a] = val; });
    v.wm.opacity = (+v.wm.opacity || 16) / 100; v.logo_height = +v.logo_height || 150;
    $$("[data-apply]").forEach(el => { v.apply[el.dataset.apply] = v.apply[el.dataset.apply] || {}; v.apply[el.dataset.apply][el.dataset.f] = el.checked; });
    return v; };
  const pvShow = async () => { const fr = $("#brpv"); if (!fr) return; const sel = $("#pvtype");
    const types = S.cache.otypes || (S.cache.otypes = await api("GET", "/api/offer-types").catch(() => []));
    if (sel && !sel.options.length) sel.innerHTML = types.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join("");
    const ty = types.find(x => String(x.id) === (sel && sel.value)) || types[0]; if (!ty) return;
    const r = await api("POST", "/api/offers-preview", { ...ty, client: "Mr. Client", project: "Sample Project" }); fr.srcdoc = r.html; };
  S.H.pvtype = pvShow; if ($("#pvtype")) $("#pvtype").setAttribute("data-change", "pvtype");
  $("#logofile").addEventListener("change", async e => { const f = e.target.files[0]; if (!f) return;
    try { logoRaw = await Img.fit(f, 900); logo = $("[name=clean_bg]").checked ? await Img.clean(logoRaw) : logoRaw; dirtyImgs = true; $("#logobox").innerHTML = imgTag(logo); } catch (x) { fail(x); } });
  $("[name=clean_bg]").addEventListener("change", async e => { if (!logoRaw) return; logo = e.target.checked ? await Img.clean(logoRaw) : logoRaw; dirtyImgs = true; $("#logobox").innerHTML = imgTag(logo); });
  $("#wmfile").addEventListener("change", async e => { const f = e.target.files[0]; if (!f) return; try { wmImage = await Img.fit(f, 900); dirtyImgs = true; } catch (x) { fail(x); } });
  S.H.logorm = async () => { if (!confirm(t("confirm_delete"))) return; try { await api("PUT", "/api/branding/image/logo", {}); await api("PUT", "/api/branding/image/logo_raw", {}); route(); } catch (e) { fail(e); } };
  S.H.brsave = async () => {
    const v = collect();
    try {
      if (dirtyImgs) {
        if (logo) await api("PUT", "/api/branding/image/logo", logo);
        if (logoRaw) await api("PUT", "/api/branding/image/logo_raw", logoRaw);
        if (wmImage) await api("PUT", "/api/branding/image/wm_image", wmImage);
      }
      const saved = await api("PUT", "/api/branding", v);
      const wm = await Img.watermark(saved, { logo, wm_image: wmImage }, S.boot.settings.company_name);
      await api("PUT", "/api/branding/image/watermark", wm || {});
      toast(t("saved")); route();
    } catch (e) { fail(e); }
  };
  pvShow().catch(() => {});
}

// ------------------------------------------------------------------ users
// Permission matrix: every section row sets all of its screens at once; each screen can then be set on its own.
function permMatrix(perms) {
  const mods = S.boot.enums.modules, subs = S.boot.enums.submodules || {}, L = ["none", "view", "add", "edit"];
  const lbl = { none: t("perm_none"), view: t("perm_view"), add: t("perm_add"), edit: t("perm_full") };
  const eff = (m, s) => { const k = `${m}.${s}`; return L.includes(perms[k]) ? perms[k] : (perms[m] || "none"); };
  const radio = (name, key, v, cls) => L.map(l => `<td class="c"><input type="radio" name="${name}" value="${l}" data-pk="${key}" class="${cls}" ${v === l ? "checked" : ""} aria-label="${esc(lbl[l])}"></td>`).join("");
  return `<div class="row" style="margin:6px 0 10px"><b>${t("preset")}:</b>${["none", "view", "add", "edit"].map(l => `<button type="button" class="btn sm" data-a="pmall" data-l="${l}">${esc({ none: t("pr_none"), view: t("pr_view"), add: t("pr_add"), edit: t("pr_full") }[l])}</button>`).join("")}</div>
    <p class="muted" style="font-size:.82em;margin:0 0 8px">${t("perm_help")}</p>
    <div class="tbl"><table class="perm"><thead><tr><th>${t("module")} / ${t("screen")}</th>${L.map(l => `<th class="c">${esc(lbl[l])}</th>`).join("")}</tr></thead><tbody>
    ${mods.map(m => { const ss = subs[m] || [];
      const secVal = ss.length ? (ss.every(s => eff(m, s) === eff(m, ss[0])) ? eff(m, ss[0]) : "") : (perms[m] || "none");
      return `<tr class="sec"><td>${esc(t("mod_" + m))}${ss.length ? ` <span class="eff">(${ss.length})</span>` : ""}</td>${radio("ps_" + m, m, secVal, "psec")}</tr>` +
        ss.map(s => `<tr><td class="scr">${esc(t(window.SUBPERM[`${m}.${s}`] || s))}</td>${radio(`pp_${m}_${s}`, `${m}.${s}`, eff(m, s), "pscr")}</tr>`).join(""); }).join("")}
    </tbody></table></div>`;
}
function readPerms(f) {
  const mods = S.boot.enums.modules, subs = S.boot.enums.submodules || {}, perms = {};
  mods.forEach(m => {
    const ss = subs[m] || [];
    if (!ss.length) { const r = $(`input[name=ps_${m}]:checked`, f); perms[m] = r ? r.value : "none"; return; }
    const vals = ss.map(s => { const r = $(`input[name=pp_${m}_${s}]:checked`, f); return r ? r.value : "none"; });
    // section level = the highest screen level (for menus); every screen is stored explicitly
    const L = ["none", "view", "add", "edit"]; perms[m] = vals.reduce((a, v) => L.indexOf(v) > L.indexOf(a) ? v : a, "none");
    ss.forEach((s, i) => { perms[`${m}.${s}`] = vals[i]; });
  });
  return perms;
}
function wirePerms(f) {
  const subs = S.boot.enums.submodules || {};
  f.addEventListener("change", e => {
    const el = e.target;
    if (el.classList.contains("psec")) { const m = el.dataset.pk; (subs[m] || []).forEach(s => { const r = $(`input[name=pp_${m}_${s}][value=${el.value}]`, f); if (r) r.checked = true; }); }
    if (el.classList.contains("pscr")) { const m = el.dataset.pk.split(".")[0]; const vals = (subs[m] || []).map(s => ($(`input[name=pp_${m}_${s}]:checked`, f) || {}).value);
      $$(`input[name=ps_${m}]`, f).forEach(r => { r.checked = vals.every(v => v === vals[0]) && r.value === vals[0]; }); }
  });
  S.H.pmall = el => { $$("input.psec, input.pscr", f).forEach(r => { r.checked = r.value === el.dataset.l; }); };
}
PAGES.users = async (view) => {
  const rows = await api("GET", "/api/users");
  const ed = can("users.users", "edit");
  const ar = S.lang === "ar";
  const edit = u => drawer(t("m_users") + (u.username ? " — " + u.username : ""), form([{ k: "username", l: t("username"), v: u.username, req: 1, dis: !!u.id,
    help: cloudOn() && !u.id ? (S.lang === "ar" ? "اسم الدخول فريد في كل الشركات — لو كان مستخدماً في شركة أخرى سنقترح اسماً آخر (مثل ahmed.beyond)." : "Sign-in names are unique across all companies — if it is taken in another company you'll be offered another (e.g. ahmed.beyond).") : "" }, { k: "full_name", l: t("full_name"), v: u.full_name },
    { k: "email", l: t("email") + (cloudOn() ? (ar ? " (اختياري)" : " (optional)") : ""), type: "email", v: u.email, span: "spanall", help: cloudOn()
      ? (ar ? "اتركه فارغاً ليسجل الدخول باسم المستخدم وكلمة المرور فقط. أضفه إن أراد الدخول بحساب Google أو استعادة كلمة المرور بنفسه." : "Leave it blank and they simply sign in with username + password. Add it if they want Google sign-in or to reset their own password by email.")
      : (ar ? "اختياري — يُستخدم لتسجيل الدخول إلى مساحة العمل المشتركة عند تفعيلها." : "Optional — used for signing in to the shared workspace when you connect one.") },
    { html: pwFields(true, (cloudOn() ? (ar ? "كلمة مرور الدخول" : "Sign-in password") : t("password")) + (u.id ? (ar ? " (اتركها فارغة للإبقاء)" : " (leave blank to keep)") : ""), !!u.id,
        cloudOn() ? (ar ? "يسجل بها الدخول فوراً — بدون رسائل تأكيد." : "They can sign in with it right away — no confirmation email.") : ""), span: "spanall" },
    ...(S.boot.user.is_admin ? [{ k: "is_admin", l: t("admin") + (S.lang === "ar" ? " (كل الصلاحيات)" : " (everything, ignores the matrix)"), type: "checkbox", v: u.is_admin }] : []),
    ...(u.id ? [{ k: "active", l: t("active") + (cloudOn() ? (ar ? " (إلغاء التحديد يمنع تسجيل الدخول)" : " (untick to block sign-in)") : ""), type: "checkbox", v: u.active !== false }] : []),
    { html: `<h4 style="margin:8px 0 0">${t("perm_matrix")}</h4>${permMatrix(u.perms || {})}`, span: "spanall" }]),
  async (v, f) => { const perms = readPerms(f);
    const body = { username: v.username || u.username, full_name: v.full_name, email: v.email, password: v.password, is_admin: v.is_admin, active: v.active, perms, _no_password: !v.password };
    if (u.id) await api("PUT", "/api/users/" + u.id, body); else await api("POST", "/api/users", body);
    if (!cloudOn()) { toast(t("saved")); return; }
    // shared workspace: create / update their sign-in account so they can sign in with username + password now
    try {
      const r = await CloudAPI.manageUser({ username: body.username, email: v.email, password: v.password, full_name: v.full_name, active: v.active !== false });
      toast(u.id ? t("saved") : (ar ? `تم — يمكن لـ ${body.username} تسجيل الدخول الآن باسم المستخدم وكلمة المرور.` : `Done — ${body.username} can sign in now with their username and password.`));
      return r;
    } catch (e) {
      if (e.status === 404 && v.email) {   // sign-in function not deployed yet: at least let the email into the workspace
        await CloudAPI.linkMember({ email: v.email, username: body.username, active: v.active !== false }).catch(() => {});
        toast(ar ? "تم الحفظ. إنشاء حسابات الدخول من هنا غير مفعّل بعد — يُنشئ الشخص حسابه بنفسه ببريده." : "Saved. Creating sign-in accounts from here isn't set up yet — the person creates their own account with that email.", true);
      } else toast((ar ? "تم حفظ المستخدم، لكن لم يُنشأ حساب الدخول: " : "User saved, but the sign-in account was not set: ") + e.message, true);
    } },
  { onOpen: f => { wirePerms(f); pwWire(f); },
    extraButtons: u.id && ed && u.id !== S.boot.user.id && (!u.is_admin || S.boot.user.is_admin) ? `<button type="button" class="btn danger" data-a="udel">${t("delete")}</button>` : "" });
  S.H.udel = () => {};
  S.H.unew = () => edit({ perms: { dashboard: "view" } });
  S.H.uedit = el => { const u = rows.find(x => String(x.id) === el.dataset.id);
    edit(u);
    S.H.udel = () => confirmDelete(`${u.username}${u.full_name ? " (" + u.full_name + ")" : ""}` + (cloudOn() ? (ar ? " وحساب دخوله" : " and their sign-in account") : ""), "/api/users/" + u.id,
      async () => { if (cloudOn()) await CloudAPI.deleteAccount({ username: u.username, email: u.email }).catch(e => { if (e.status !== 404) toast((ar ? "حُذف المستخدم، لكن حساب الدخول لم يُحذف: " : "User deleted, but their sign-in account was not: ") + e.message, true); }); }); };
  const L = ["none", "view", "add", "edit"];
  const summary = u => {
    if (u.is_admin) return `<span class="chip ok">${t("admin")}</span>`;
    const subs = S.boot.enums.submodules || {};
    return S.boot.enums.modules.map(m => { const ss = subs[m] || [];
      if (!ss.length) return (u.perms[m] || "none") === "none" ? "" : `<span class="chip">${esc(t("mod_" + m))}: ${esc(t("perm_" + (u.perms[m] === "edit" ? "full" : u.perms[m])))}</span>`;
      const vals = ss.map(s => L.includes(u.perms[`${m}.${s}`]) ? u.perms[`${m}.${s}`] : (u.perms[m] || "none"));
      if (vals.every(v => v === "none")) return "";
      if (vals.every(v => v === vals[0])) return `<span class="chip">${esc(t("mod_" + m))}: ${esc(t("perm_" + (vals[0] === "edit" ? "full" : vals[0])))}</span>`;
      return `<span class="chip warn">${esc(t("mod_" + m))}: ${ss.map((s, i) => vals[i] === "none" ? "" : `${esc(t(window.SUBPERM[`${m}.${s}`] || s))} ${esc({ view: "👁", add: "+", edit: "✎" }[vals[i]])}`).filter(Boolean).join(", ")}</span>`; }).join(" ");
  };
  view.innerHTML = `<div class="toolbar">${can("users.users", "add") ? `<button class="btn pri" data-a="unew">+ ${t("new")}</button>` : ""}</div><div class="card"><div class="tbl"><table><thead><tr><th>${t("username")}</th><th>${t("full_name")}</th><th>${t("email")}</th><th>${t("perm_matrix")}</th><th>${t("status")}</th></tr></thead><tbody>
    ${rows.map(u => `<tr class="click ${u.active ? "" : "void"}" data-a="${ed ? "uedit" : ""}" data-id="${u.id}"><td><b>${esc(u.username)}</b></td><td>${esc(u.full_name)}</td><td>${esc(u.email || "")}</td><td>${summary(u)}</td><td>${u.active ? "" : `<span class="chip bad">${t("no")}</span>`}</td></tr>`).join("")}</tbody></table></div>
    <p class="muted" style="font-size:.82em">👁 ${t("perm_view")} · + ${t("perm_add")} · ✎ ${t("perm_full")}</p></div>`;
};
PAGES.audit = async (view, parts, q) => {
  const lq = { ...q, limit: q.limit || 100 };
  const rows = await api("GET", "/api/audit?" + qs(lq));
  view.innerHTML = filterBar(q, ["q", ["select", "username", t("username"), [...new Set(rows.map(r => r.username))].map(u => [u, u])]]) +
    `<div class="card"><div class="tbl"><table><thead><tr><th>Time</th><th>${t("username")}</th><th>Action</th><th>Detail</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td>${esc(r.ts)}</td><td>${esc(r.username)}</td><td>${esc(r.action)}</td><td>${esc(r.detail)}</td></tr>`).join("")}</tbody></table></div>${pager(rows.length < lq.limit ? (Number(lq.offset || 0) + rows.length) : 99999, lq).replace("99999", "…")}</div>`;
};

// ------------------------------------------------------------ workspace
const SYNC_LABEL = { ok: ["● Synced", "● متزامن", "ok"], offline: ["● Offline — view only", "● غير متصل — عرض فقط", "bad"],
  signed_out: ["● Sign in to the workspace", "● سجّل الدخول لمساحة العمل", "warn"], update: ["● Update available — reload", "● يوجد تحديث — أعد التحميل", "warn"],
  error: ["● Sync problem", "● مشكلة في المزامنة", "bad"] };
function syncDot(st) {
  const el = $("#syncdot"); if (!el) return;
  const l = cloudOn() && SYNC_LABEL[st.state]; if (!l) { el.style.display = "none"; return; }
  el.style.display = ""; el.className = "chip " + l[2]; el.textContent = l[S.lang === "ar" ? 1 : 0]; el.title = st.error || st.last_sync || st.lastSync || "";
}
if (STATIC) window.addEventListener("beyond-remote-change", () => {
  // someone else changed the books: refresh the screen unless the person is typing in a form
  if ($(".drawer-bg") || (document.activeElement && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && document.activeElement.closest("#view"))) {
    toast(S.lang === "ar" ? "تم تحديث البيانات من مستخدم آخر" : "Updated by someone else — refresh to see the latest"); return; }
  refreshBoot().then(() => route()).catch(() => {});
});

PAGES.workspace = async (view) => view.innerHTML = "";  // (tab under Settings)

// Top bar → Companies: open another company (browser edition: reloads with that company's books), create, join.
async function companySwitcher() {
  const ar = S.lang === "ar";
  let cos = [], cur = "";
  try { cos = await CloudAPI.companies(); cur = STATIC ? window.BeyondCloud.company() : ((await CloudAPI.status()).company || ""); } catch (e) { return fail(e); }
  const live = cos.filter(c => c.active && !c.pending), pending = cos.filter(c => c.pending);
  drawer(ar ? "الشركات" : "Companies", `<div class="co-list">${live.map(c => `<button class="btn oauth ${c.id === cur ? "on" : ""}" type="button" data-a="coopen" data-id="${esc(c.id)}" ${c.id === cur ? "disabled" : ""}>
      <span class="co-ic">${esc((c.name || "?").trim().charAt(0).toUpperCase())}</span><span>${esc(c.name)}</span>${c.id === cur ? `<span class="chip ok">${ar ? "مفتوحة" : "open"}</span>` : ""}${c.is_admin ? `<span class="chip">${t("admin")}</span>` : ""}</button>`).join("")}</div>
    ${pending.length ? `<div class="banner info">${ar ? "بانتظار الموافقة: " : "Waiting for approval: "}${pending.map(c => esc(c.name)).join(", ")}</div>` : ""}
    ${!STATIC ? `<p class="muted" style="font-size:.85em">${ar ? "هذا البرنامج يتابع شركة واحدة. لفتح شركة أخرى: الإعدادات ← مساحة العمل ← فصل هذا الجهاز، ثم سجّل الدخول واختر الشركة." : "This app follows one company. To open another: Settings → Workspace → Disconnect this device, then sign in and choose it."}</p>` : ""}
    <h4>${ar ? "الانضمام برمز دعوة" : "Join with an invite code"}</h4><div class="row"><input id="cocode" placeholder="${ar ? "رمز الدعوة" : "Invite code"}" style="flex:1;text-transform:uppercase"><button class="btn" type="button" data-a="cojoin2">${ar ? "طلب الانضمام" : "Ask to join"}</button></div>
    <h4>${ar ? "شركة جديدة" : "New company"}</h4><div class="row"><input id="coname" placeholder="${ar ? "اسم الشركة" : "Company name"}" style="flex:1"><button class="btn" type="button" data-a="conew2">${ar ? "إنشاء" : "Create"}</button></div>
    <p class="muted" style="font-size:.8em">${ar ? "الشركة الجديدة تبدأ بدفاتر فارغة وتكون أنت مسؤولها. كل شركة لها بياناتها وأعضاؤها." : "A new company starts with empty books and you as its administrator. Every company has its own books and members."}</p>`,
  null, { readOnly: true });
  const openCo = id => { if (!STATIC) return toast(ar ? "افصل هذا الجهاز أولاً (الإعدادات ← مساحة العمل)" : "Disconnect this device first (Settings → Workspace)", true);
    if (CloudAPI.chooseCompany(id)) { store.set("token", ""); location.reload(); } };
  S.H.coopen = el => openCo(el.dataset.id);
  S.H.cojoin2 = async () => { try { const r = await CloudAPI.joinCompany($("#cocode").value.trim()); toast(r.is_member ? (ar ? "أنت عضو بالفعل" : "You are already a member") : (ar ? "تم إرسال الطلب" : "Request sent — waiting for approval")); companySwitcher(); } catch (e) { fail(e); } };
  S.H.conew2 = async () => { const n = $("#coname").value.trim(); if (!n) return;
    try { const c = await CloudAPI.createCompany(n); toast(t("saved")); if (STATIC && confirm(ar ? "فتح الشركة الجديدة الآن؟" : "Open the new company now?")) openCo(c.id); else companySwitcher(); } catch (e) { fail(e); } };
}
async function workspaceTab(view, head) {
  const ar = S.lang === "ar";
  let st; try { st = await CloudAPI.status(); } catch (e) { view.innerHTML = head + `<div class="banner err">${esc(e.message)}</div>`; return; }
  let info = null; if (st.configured && st.email) info = await CloudAPI.info().catch(e => ({ error: e.message }));
  const admin = S.boot.user.is_admin;
  const members = info && info.is_admin ? await CloudAPI.members().catch(() => []) : [];
  view.innerHTML = head + `<div class="card"><h2>${ar ? "مساحة العمل المشتركة (Supabase)" : "Shared workspace (Supabase)"}</h2>
    <p class="muted">${ar ? "اربط هذا الجهاز بمشروع Supabase ليعمل كل الفريق على نفس الدفاتر في نفس الوقت. كل تعديل يُسجل في Supabase بالترتيب وتطبقه كل الأجهزة، وصلاحيات كل مستخدم تُراجع عند كل تعديل." :
      "Connect this device to a Supabase project so the whole team works on the same books at the same time. Every change is recorded in Supabase in order and applied by every device; each user's permissions are re-checked on every change."}</p>
    <div class="grid g4"><div><b>${ar ? "الحالة" : "Status"}</b><br>${st.joined ? `<span class="chip ok">${ar ? "متصل" : "Connected"}</span>` : `<span class="chip">${ar ? "غير متصل" : "Not connected"}</span>`} ${st.state && st.state !== "off" ? `<span class="chip">${esc(st.state)}</span>` : ""}</div>
      <div><b>${ar ? "الحساب" : "Account"}</b><br>${esc(st.email || "—")}</div><div><b>${ar ? "آخر تغيير مطبق" : "Last change applied"}</b><br>#${st.last_seq >= 0 ? st.last_seq : "—"}</div>
      <div><b>${ar ? "الجهاز" : "Device"}</b><br><code>${esc(st.device || "")}</code></div></div>
    ${st.error ? `<div class="banner err" style="margin-top:8px">${esc(st.error)}</div>` : ""}
    ${info && info.error ? `<div class="banner err" style="margin-top:8px">${esc(info.error)}</div>` : ""}</div>
  ${info && info.company ? `<div class="card" id="wsco"><h2>${ar ? "الشركة" : "Company"}</h2><div class="grid g2">
      <label class="f">${ar ? "اسم الشركة في مساحة العمل" : "Company name in the workspace"}<input id="wsconame" value="${esc(info.company_name || "")}" ${info.is_admin ? "" : "disabled"}></label>
      ${info.is_admin ? `<label class="f">${ar ? "رمز الدعوة" : "Invite code"}<span class="row"><input value="${esc(info.invite_code || "")}" readonly style="max-width:160px;font-family:monospace;letter-spacing:2px"><button class="btn sm" type="button" data-a="wscocopy" data-v="${esc(info.invite_code || "")}">${ar ? "نسخ" : "Copy"}</button><button class="btn sm" type="button" data-a="wsconew">${ar ? "رمز جديد" : "New code"}</button></span>
        <span class="muted" style="font-weight:400">${ar ? "من يسجل الدخول ويدخل هذا الرمز يطلب الانضمام، وتوافق أنت عليه من قائمة الأعضاء. «رمز جديد» يلغي الرمز القديم." : "Anyone who signs in and enters this code asks to join; you approve them in the member list below. “New code” cancels the old one."}</span></label>` : ""}</div>
      ${info.is_admin ? `<div class="row" style="margin-top:8px"><button class="btn" data-a="wscoren">${ar ? "حفظ الاسم" : "Save name"}</button><button class="btn" data-a="coswitch">⇄ ${ar ? "الشركات" : "Companies"}</button></div>` : `<div class="row" style="margin-top:8px"><button class="btn" data-a="coswitch">⇄ ${ar ? "الشركات" : "Companies"}</button></div>`}</div>` : ""}
  ${admin ? `<div class="card" id="wscfg"><h2>1 · ${ar ? "رابط المشروع" : "Project link"}</h2>
    ${st.preset ? `<p class="muted">${ar ? "هذه النسخة مربوطة مسبقاً بمشروع الشركة." : "This copy of the app comes pre-linked to your company's project."}</p>` : ""}
    ${form([{ k: "url", l: "Project URL", v: st.url, ph: "https://abcdefgh.supabase.co" }, { k: "key", l: ar ? "المفتاح العام (anon public key)" : "anon public key", v: st.key, ph: "eyJhbGciOi…" }], 2)}
    <div class="row" style="margin-top:8px"><button class="btn" data-a="wssave">${t("save")}</button><span class="muted" style="font-size:.85em">Supabase → Project Settings → API</span></div></div>
  <div class="card" id="wsin"><h2>2 · ${S.wsCreate ? (ar ? "إنشاء حساب" : "Create an account") : (ar ? "تسجيل الدخول" : "Sign in")}</h2>
    <div class="grid g2"><label class="f">${t("email")}<input name="email" type="email" value="${esc(st.email || "")}"></label><div></div>
      ${pwFields(!!S.wsCreate)}</div>
    <div class="row" style="margin-top:8px">${S.wsCreate ? `<button class="btn pri" data-a="wsup">${ar ? "إنشاء الحساب" : "Create account"}</button><a href="#" data-a="wsmode">${ar ? "لدي حساب — تسجيل الدخول" : "I have an account — sign in"}</a>`
      : `<button class="btn" data-a="wsin">${t("sign_in")}</button><a href="#" data-a="wsmode">${ar ? "ليس لديك حساب؟ إنشاء حساب" : "No account yet? Create one"}</a>`}</div></div>
  <div class="card"><h2>3 · ${ar ? "الشركة" : "Company"}</h2>${info && !info.error ? `
    ${info.needs_company ? `${(info.companies || []).filter(c => c.active && !c.pending).length ? `<p>${ar ? "اختر الشركة التي يتابعها هذا الجهاز:" : "Choose the company this device follows:"}</p><div class="row">${(info.companies || []).filter(c => c.active && !c.pending).map(c => `<button class="btn" data-a="wschoose" data-id="${esc(c.id)}">${esc(c.name)}</button>`).join("")}</div>` : ""}
      <p style="margin-top:10px">${ar ? "أو ابدأ شركة جديدة من دفاتر هذا الجهاز (تصبح مسؤولها):" : "Or start a new company from this device's books (you become its administrator):"}</p><button class="btn pri" data-a="wsclaim">${ar ? "إنشاء شركة من هذا الجهاز" : "Create a company from this device"}</button> <button class="btn" data-a="coswitch">⇄ ${ar ? "الانضمام برمز / شركة جديدة فارغة" : "Join with a code / new empty company"}</button>` : ""}
    ${!info.needs_company && !info.is_member ? `<div class="banner err">${info.pending ? (ar ? "طلب الانضمام بانتظار موافقة المسؤول." : "Your request to join is waiting for the administrator.") : (ar ? "هذا الحساب ليس عضواً. اطلب من المسؤول إضافته." : "This account is not a member yet — ask an administrator to add it.")}</div>` : ""}
    ${info.is_member ? `<div class="row">${!st.joined ? `<button class="btn pri" data-a="wsjoin">${ar ? "الانضمام (يستبدل بيانات هذا الجهاز)" : "Join (replaces this device's books)"}</button>` : `<button class="btn" data-a="wspull">${ar ? "مزامنة الآن" : "Sync now"}</button>`}
      <button class="btn danger" data-a="wsoff">${ar ? "فصل هذا الجهاز" : "Disconnect this device"}</button></div>
      ${!STATIC && !info.gateway ? `<p class="muted" style="font-size:.85em;margin-top:8px">${ar ? "لخادم مكتبي يخدم عدة مستخدمين محليين: فعّل «بوابة» لهذا الحساب في قائمة الأعضاء حتى تُسجل التعديلات بأسماء المستخدمين الفعليين." : "Desktop server for several local users: tick “gateway” for this account in the member list so changes are recorded under each local user rather than this account."}</p>` : ""}` : ""}`
    : `<div class="muted">${ar ? "سجّل الدخول أولاً." : "Sign in first."}</div>`}</div>` : `<div class="banner info">${t("admin_only")}</div>`}
  ${info && info.is_admin ? `<div class="card"><h2>${ar ? "الأعضاء" : "Members"}</h2><p class="muted" style="font-size:.85em">${ar ? "كل عضو يسجل الدخول ببريده. أضف له أيضاً مستخدماً في التطبيق بنفس البريد كاسم مستخدم لتحديد صلاحياته (يتم ذلك تلقائياً هنا)." : "Each member signs in with their email. Adding a member here also creates their app user (username = email) — set their permissions under Users."}</p>
    <div class="tbl"><table><thead><tr><th>${t("email")}</th><th>${t("username")}</th><th>${t("admin")}</th><th>Gateway</th><th>${t("active")}</th><th></th></tr></thead><tbody>
    ${members.filter(m => m.pending).map(m => `<tr class="pending"><td>${esc(m.email)}<div class="lines">${ar ? "يطلب الانضمام" : "asks to join"}</div></td><td>${esc(m.username || "—")}</td><td colspan="3"></td>
      <td style="white-space:nowrap"><button class="btn sm pri" data-a="wsapprove" data-e="${esc(m.email)}">${ar ? "موافقة" : "Approve"}</button> <button class="btn sm danger" data-a="wsdecline" data-e="${esc(m.email)}">${ar ? "رفض" : "Decline"}</button></td></tr>`).join("")}
    ${members.filter(m => !m.pending).map(m => `<tr><td>${esc(m.email)}</td><td>${esc(m.username || "—")}</td><td><input type="checkbox" data-mf="is_admin" data-e="${esc(m.email)}" ${m.is_admin ? "checked" : ""}></td><td><input type="checkbox" data-mf="gateway" data-e="${esc(m.email)}" ${m.gateway ? "checked" : ""}></td>
      <td><input type="checkbox" data-mf="active" data-e="${esc(m.email)}" ${m.active ? "checked" : ""}></td><td style="white-space:nowrap"><button class="btn sm" data-a="wsmsave" data-e="${esc(m.email)}">${t("save")}</button>
        ${m.email !== (info.email || "") ? ` <button class="btn sm danger" data-a="wsmdel" data-e="${esc(m.email)}" title="${ar ? "حذف حساب الدخول" : "Delete the sign-in account"}">✕</button>` : ""}</td></tr>`).join("")}
    <tr><td><input id="wsnew" type="email" placeholder="name@company.com"></td><td><input id="wsnewuser" placeholder="${t("username")}"></td><td><input type="checkbox" id="wsnewadm"></td><td><input type="checkbox" id="wsnewgw"></td><td></td><td><button class="btn sm pri" data-a="wsmadd">+ ${t("new")}</button></td></tr></tbody></table></div></div>` : ""}`;
  const run = async (fn, okMsg) => { try { await fn(); if (okMsg) toast(okMsg); await refreshBoot(); renderShell(); route(); } catch (e) { fail(e); } };
  S.H.wssave = () => { const v = readForm($("#wscfg")); run(() => CloudAPI.config(v.url, v.key), t("saved")); };
  if ($("#wsin")) pwWire($("#wsin"));
  S.H.wsmode = () => { S.wsCreate = !S.wsCreate; route(); };
  S.H.wsin = () => { const v = readForm($("#wsin")); run(() => CloudAPI.signin(v.email, v.password, false)); };
  S.H.wsup = () => { const box = $("#wsin"); S.H.pwmatch(); const bad = $$("input", box).find(i => !i.checkValidity()); if (bad) { bad.reportValidity(); return; }
    const v = readForm(box); run(async () => { await CloudAPI.signin(v.email, v.password, true); S.wsCreate = false; }); };
  S.H.wsclaim = () => { const name = prompt(ar ? "اسم الشركة الجديدة:" : "Name of the new company:", S.boot.settings.company_name || ""); if (!name) return;
    run(() => CloudAPI.claim(name.trim()), t("saved")); };
  S.H.wschoose = el => run(async () => { await CloudAPI.chooseCompany(el.dataset.id); await CloudAPI.join(); if (!STATIC) { toast(ar ? "تم الانضمام — سجّل الدخول مجدداً" : "Joined — please sign in again"); setTimeout(() => location.reload(), 1200); } });
  S.H.wscoren = () => run(() => CloudAPI.renameCompany($("#wsconame").value.trim()), t("saved"));
  S.H.wsconew = () => confirm(ar ? "إنشاء رمز دعوة جديد؟ الرمز القديم يتوقف عن العمل." : "Make a new invite code? The old one stops working.") && run(() => CloudAPI.newInviteCode(), t("saved"));
  S.H.wscocopy = async el => { try { await navigator.clipboard.writeText(el.dataset.v); toast(ar ? "تم النسخ" : "Copied"); } catch { prompt("", el.dataset.v); } };
  S.H.wsapprove = el => run(async () => { const email = el.dataset.e; await CloudAPI.setMember({ email, is_admin: false, gateway: false, active: true });
    const username = email.split("@")[0].toLowerCase().replace(/[^a-z0-9._-]/g, "");
    await api("POST", "/api/users", { username, email, full_name: email.split("@")[0], _no_password: true, perms: { dashboard: "view" } }).catch(() => {}); },
    ar ? "تمت الموافقة — حدد صلاحياته من المستخدمين" : "Approved — set their permissions under Users");
  S.H.wsdecline = el => confirm((ar ? "رفض طلب " : "Decline the request from ") + el.dataset.e + "?") && run(() => CloudAPI.removeMember(el.dataset.e), t("saved"));
  S.H.wsjoin = () => confirm(ar ? "سيتم استبدال بيانات هذا الجهاز ببيانات مساحة العمل. متابعة؟" : "This device's books will be replaced by the workspace's. Continue?") && run(async () => {
    await CloudAPI.join(); if (!STATIC) { toast(ar ? "تم الانضمام — سجّل الدخول مجدداً" : "Joined — please sign in again"); setTimeout(() => location.reload(), 1200); } });
  S.H.wspull = () => run(() => CloudAPI.pull(), ar ? "تمت المزامنة" : "Synced");
  S.H.wsoff = () => confirm(ar ? "فصل هذا الجهاز؟ تبقى البيانات الحالية عليه." : "Disconnect this device? Its current books stay on it.") && run(() => CloudAPI.disconnect(), t("saved"));
  const mrow = e => ({ email: e, is_admin: $(`[data-mf=is_admin][data-e="${e}"]`).checked, gateway: $(`[data-mf=gateway][data-e="${e}"]`).checked, active: $(`[data-mf=active][data-e="${e}"]`).checked });
  S.H.wsmsave = el => run(() => CloudAPI.setMember(mrow(el.dataset.e)), t("saved"));
  S.H.wsmdel = el => confirm((ar ? "حذف حساب الدخول لـ " : "Delete the sign-in account of ") + el.dataset.e + (ar ? "؟ لن يتمكن من الدخول. (مستخدم التطبيق يبقى — احذفه من المستخدمين إن أردت.)" : "? They can no longer sign in. (Their app user stays — delete it under Users if you want.)"))
    && run(() => CloudAPI.deleteAccount({ email: el.dataset.e }), ar ? "تم الحذف" : "Deleted");
  S.H.wsmadd = () => { const email = $("#wsnew").value.trim().toLowerCase(); if (!email) return;
    run(async () => { await CloudAPI.setMember({ email, is_admin: $("#wsnewadm").checked, gateway: $("#wsnewgw").checked, active: true });
      const username = ($("#wsnewuser").value.trim() || email.split("@")[0]).toLowerCase();
      await api("POST", "/api/users", { username, email, full_name: email.split("@")[0], is_admin: $("#wsnewadm").checked, _no_password: true, perms: { dashboard: "view" } }).catch(() => {});
      await CloudAPI.linkMember({ email, username, active: true }).catch(() => {}); }, t("saved")); };
}

// ------------------------------------------------------------------ boot
async function refreshBoot() { S.boot = await api("GET", "/api/bootstrap"); }
// Companies created by the upgrade are called "My company": an administrator's app renames it to the company name
// in Settings → Company the first time it opens.
async function fixCompanyName() {
  if (!cloudOn() || !S.boot.user.is_admin) return;
  try {
    const info = await CloudAPI.info(), name = (S.boot.settings.company_name || "").trim();
    if (info && info.is_admin && info.company_name === "My company" && name && name !== "My company") await CloudAPI.renameCompany(name);
  } catch { /* not an administrator of the workspace company, or offline */ }
}
async function boot() {
  try { await refreshBoot(); } catch (e) { $("#root").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
  applyLook();
  if (!S.boot.user) return renderAuth();
  renderShell();
  route();
  fixCompanyName();
  setTimeout(runRecurringOnOpen, 1500);
  if (STATIC && window.BeyondCloud) window.BeyondCloud.onStatus(syncDot);
  else if (S.boot.cloud && S.boot.cloud.joined) {
    const poll = () => api("GET", "/api/cloud-status").then(syncDot).catch(() => {});
    poll(); clearInterval(S.syncTimer); S.syncTimer = setInterval(poll, 15000);
  }
}
// ================================================== partners' equity, fixed assets, entry types

const L2 = (en, ar) => (S.lang === "ar" ? ar : en);
const activePartners = () => (S.boot.partners || []).filter(p => p.active !== 0);
const pctIn = v => (v == null || v === "" ? "" : +(v * 100).toFixed(4));
const debounce = (fn, ms = 350) => { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; };

// show only some fields of a drawer form; hidden ones are not submitted
function showFields(f, keys, all) {
  all.forEach(n => { const el = $(`[name=${n}]`, f); if (!el) return; const on = keys.includes(n);
    el.closest("label").style.display = on ? "" : "none"; el.dataset.skip = on ? "" : "1"; });
}

// preview of what will be booked: journal lines, ownership before → after, notes
function planHtml(p) {
  if (!p) return "";
  const lines = p.lines && p.lines.length ? `<div class="tbl"><table><thead><tr><th>${t("account")}</th><th>${t("party")}</th><th class="num">${t("debit")}</th><th class="num">${t("credit")}</th></tr></thead><tbody>
    ${p.lines.map(l => `<tr><td>${esc(l.account)} ${esc(aname(l))}</td><td>${esc(l.party || "")}</td><td class="num">${l.debit ? money(l.debit) : ""}</td><td class="num">${l.credit ? money(l.credit) : ""}</td></tr>`).join("")}</tbody></table></div>`
    : `<div class="muted">${L2("No journal entry — only the ownership changes.", "لا يوجد قيد — تتغير الملكية فقط.")}</div>`;
  const own = p.ownership ? `<h4 style="margin:12px 0 6px">${L2("Ownership", "الملكية")}</h4><div class="tbl"><table><thead><tr><th>${t("partner")}</th><th class="num">${L2("Before", "قبل")}</th><th class="num">${L2("After", "بعد")}</th></tr></thead><tbody>
    ${p.ownership.map(o => `<tr><td>${esc(o.partner)}</td><td class="num">${pct(o.before)}</td><td class="num ${o.after > o.before + 1e-9 ? "pos" : o.after < o.before - 1e-9 ? "neg" : ""}"><b>${pct(o.after)}</b></td></tr>`).join("")}
    <tr class="total"><td>${t("total")}</td><td class="num">${pct(p.ownership.reduce((a, o) => a + o.before, 0))}</td><td class="num">${pct(p.ownership.reduce((a, o) => a + o.after, 0))}</td></tr></tbody></table></div>` : "";
  const notes = (p.notes || []).map(n => `<div class="banner info" style="margin-top:8px">${esc(n)}</div>`).join("");
  return `<h4 style="margin:4px 0 6px">${L2("What will be booked", "ما سيتم تسجيله")}</h4>${lines}${own}${notes}`;
}

// ============================================================ partners' equity
// per-partner inputs (shares in %, or amounts), collected into {partner id: value}
const shareBlock = (label, partners, vals = {}, cls = "eqsh", unit = "%") => `<div class="spanall eqblock" data-block="${cls}"><h4 style="margin:6px 0">${esc(label)}</h4><div class="grid g4">
  ${partners.map(p => `<label class="f">${esc(p.name)} ${unit}<input class="${cls} num" data-pid="${p.id}" type="number" step="any" data-skip="1" value="${vals[p.id] ?? ""}"></label>`).join("")}</div></div>`;
const collectBlock = (f, cls) => Object.fromEntries($$("." + cls, f).filter(i => i.value !== "").map(i => [i.dataset.pid, i.value]));

const EQ = {
  contribute: { title: ["Capital contribution", "زيادة رأس المال (مساهمة شريك)"], mod: "partners.transactions",
    fields: (d, pre) => [
      { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "partner", l: t("partner"), type: "select", opts: activePartners().map(p => [p.id, p.name]), v: pre.partner },
      { k: "amount", l: t("amount"), type: "number", req: 1 },
      { k: "source", l: L2("Paid in as", "طريقة السداد"), type: "select", change: "eqsync", opts: [["cash", L2("Money (cash / bank)", "نقدية (خزينة / بنك)")], ["asset", L2("An asset (in kind)", "أصل عيني")],
        ["loan", L2("Converting the partner's loan to capital", "تحويل قرض الشريك إلى رأس مال")], ["current", L2("From the partner's current account", "من جاري الشريك")]] },
      { k: "method", l: t("method"), type: "select", opts: methodOpts() },
      { k: "asset_account", l: L2("Asset account", "حساب الأصل"), type: "select", opts: accts(["Asset"], ["fixed_asset", "intangible", "investment", "inventory", "other_noncurrent_asset"]) },
      { k: "ownership", l: L2("Ownership after this", "الملكية بعد ذلك"), type: "select", change: "eqsync", opts: [["keep", L2("Keep the current shares", "بدون تغيير")],
        ["by_capital", L2("Follow each partner's capital", "حسب رأس مال كل شريك")], ["set", L2("Type the new shares", "إدخال النسب الجديدة")]],
        help: L2("Investing more does not change the shares by itself — choose how they should change.", "زيادة المساهمة لا تغيّر النسب تلقائياً — اختر كيف تتغير.") },
      { html: shareBlock(L2("New shares", "النسب الجديدة"), activePartners(), Object.fromEntries(activePartners().map(p => [p.id, pctIn(p.default_share)]))), span: "spanall" },
      { k: "memo", l: t("memo"), span: "spanall" }],
    sync: v => ["date", "partner", "amount", "source", "ownership", "memo", ...(v.source === "cash" ? ["method"] : []), ...(v.source === "asset" ? ["asset_account"] : [])],
    blocks: v => (v.ownership === "set" ? ["eqsh"] : []), body: (v, f) => ({ ...v, shares: collectBlock(f, "eqsh") }) },
  withdraw: { title: ["Capital withdrawal", "تخفيض رأس المال (سحب شريك)"], mod: "partners.transactions",
    fields: (d, pre) => [
      { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "partner", l: t("partner"), type: "select", opts: activePartners().map(p => [p.id, p.name]), v: pre.partner },
      { k: "amount", l: t("amount"), type: "number", req: 1, help: L2("A return of capital — profit paid out is a drawing or a distribution.", "استرداد جزء من رأس المال — أما صرف الأرباح فهو مسحوبات أو توزيع.") },
      { k: "to", l: L2("Paid", "السداد"), type: "select", change: "eqsync", opts: [["cash", L2("Now (cash / bank)", "الآن (خزينة / بنك)")], ["current", L2("To the partner's current account", "إلى جاري الشريك")], ["payable", L2("Owed to the partner (pay later)", "مستحق للشريك (يُسدد لاحقاً)")]] },
      { k: "method", l: t("method"), type: "select", opts: methodOpts() },
      { k: "ownership", l: L2("Ownership after this", "الملكية بعد ذلك"), type: "select", change: "eqsync", opts: [["keep", L2("Keep the current shares", "بدون تغيير")], ["by_capital", L2("Follow each partner's capital", "حسب رأس مال كل شريك")], ["set", L2("Type the new shares", "إدخال النسب الجديدة")]] },
      { html: shareBlock(L2("New shares", "النسب الجديدة"), activePartners(), Object.fromEntries(activePartners().map(p => [p.id, pctIn(p.default_share)]))), span: "spanall" },
      { k: "memo", l: t("memo"), span: "spanall" }],
    sync: v => ["date", "partner", "amount", "to", "ownership", "memo", ...(v.to === "cash" ? ["method"] : [])],
    blocks: v => (v.ownership === "set" ? ["eqsh"] : []), body: (v, f) => ({ ...v, shares: collectBlock(f, "eqsh") }) },
  loan: { title: ["Loan from a partner", "قرض من شريك"], mod: "partners.transactions",
    fields: (d, pre) => [
      { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "partner", l: t("partner"), type: "select", opts: activePartners().map(p => [p.id, p.name]), v: pre.partner, change: "eqrate" },
      { k: "amount", l: t("amount"), type: "number", req: 1 }, { k: "method", l: t("method"), type: "select", opts: methodOpts() },
      { k: "rate", l: L2("Interest % a year (0 = interest-free)", "الفائدة % سنوياً (0 = بدون فائدة)"), type: "number", v: pctIn((activePartners()[0] || {}).loan_rate) || 0,
        help: L2("A loan is money the company owes back — it does not buy ownership. Interest is an expense.", "القرض دين على الشركة ولا يمنح ملكية. الفائدة مصروف.") },
      { k: "memo", l: t("memo"), span: "spanall" }] },
  repay: { title: ["Repay a partner's loan / interest", "سداد قرض شريك / فوائده"], mod: "partners.transactions",
    fields: (d, pre) => [
      { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "partner", l: t("partner"), type: "select", opts: (d.partners || []).filter(p => p.accounts.loan || p.accounts.interest).map(p => [p.id, `${p.name} — ${L2("loan", "قرض")} ${money(p.accounts.loan)} · ${L2("interest", "فوائد")} ${money(p.accounts.interest)}`]), v: pre.partner },
      { k: "what", l: L2("Paying", "سداد"), type: "select", opts: [["principal", L2("The loan (principal)", "أصل القرض")], ["interest", L2("Interest owed", "الفوائد المستحقة")]] },
      { k: "amount", l: t("amount"), type: "number", req: 1 }, { k: "method", l: t("method"), type: "select", opts: methodOpts() },
      { k: "memo", l: t("memo"), span: "spanall" }] },
  interest: { title: ["Interest on a partner's loan", "فوائد قرض شريك"], mod: "partners.transactions",
    fields: (d, pre) => [
      { k: "date", l: L2("Up to (date)", "حتى تاريخ"), type: "date", v: today(), req: 1 },
      { k: "partner", l: t("partner"), type: "select", opts: (d.partners || []).filter(p => p.accounts.loan).map(p => [p.id, `${p.name} — ${money(p.accounts.loan)} @ ${pct(p.loan_rate)}`]), v: pre.partner, change: "eqrate" },
      { k: "rate", l: L2("Interest % a year", "الفائدة % سنوياً"), type: "number" },
      { k: "add_to_loan", l: L2("Add the interest to the loan (instead of owing it separately)", "إضافة الفائدة إلى أصل القرض"), type: "checkbox", span: "spanall" },
      { k: "memo", l: t("memo"), span: "spanall" }] },
  distribute: { title: ["Profit distribution", "توزيع أرباح"], mod: "partners.transactions",
    fields: d => [
      { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: L2("Total to distribute", "إجمالي التوزيع"), type: "number", help: L2("Split by the partners' shares — or type an amount per partner below.", "يُقسم حسب نسب الشركاء — أو أدخل مبلغاً لكل شريك بالأسفل.") },
      { k: "pay_now", l: L2("Pay it now (otherwise it is owed to the partners)", "صرفها الآن (وإلا تُسجل مستحقة للشركاء)"), type: "checkbox", change: "eqsync", span: "spanall" },
      { k: "method", l: t("method"), type: "select", opts: methodOpts() },
      { html: shareBlock(L2("Or an amount per partner", "أو مبلغ لكل شريك"), activePartners(), {}, "eqamt", ""), span: "spanall" },
      { k: "memo", l: t("memo"), span: "spanall" }],
    sync: v => ["date", "amount", "pay_now", "memo", ...(v.pay_now ? ["method"] : [])], blocks: () => ["eqamt"],
    body: (v, f) => ({ ...v, amounts: collectBlock(f, "eqamt") }) },
  transfer: { title: ["Transfer / sell a share", "تنازل / بيع حصة"], mod: "partners.ownership",
    fields: (d, pre) => [
      { k: "date", l: t("date"), type: "date", v: today(), req: 1 },
      { k: "seller", l: L2("From (seller)", "من (البائع)"), type: "select", opts: activePartners().map(p => [p.id, `${p.name} — ${pct(p.default_share)}`]), v: pre.seller },
      { k: "buyer", l: L2("To (buyer) — a partner or a new name", "إلى (المشتري) — شريك أو اسم جديد"), list: "eqpartners", req: 1, v: pre.buyer },
      { k: "share", l: L2("Share of the company transferred (%)", "النسبة المتنازل عنها من الشركة (%)"), type: "number" },
      { k: "all", l: L2("The seller's whole share (replace this partner)", "كامل حصة البائع (استبدال الشريك)"), type: "checkbox", change: "eqsync", v: pre.all },
      { k: "price", l: L2("Price agreed (for the record)", "الثمن المتفق عليه (للتوثيق)"), type: "number", help: L2("Paid between the two partners — it does not go through the company.", "يُدفع بين الشريكين مباشرة ولا يمر بحسابات الشركة.") },
      { k: "memo", l: t("memo"), span: "spanall" }],
    sync: v => ["date", "seller", "buyer", "all", "price", "memo", ...(v.all ? [] : ["share"])] },
  admit: { title: ["Admit a new partner", "دخول شريك جديد"], mod: "partners.ownership",
    fields: () => [
      { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "name", l: L2("New partner's name", "اسم الشريك الجديد"), req: 1 },
      { k: "share", l: L2("Their share of the company (%)", "نسبته من الشركة (%)"), type: "number", req: 1, help: L2("Everyone else is diluted in proportion.", "تنخفض نسب الباقين بنفس النسبة.") },
      { k: "amount", l: L2("Amount invested (0 if none)", "المبلغ المستثمر (0 إن لم يوجد)"), type: "number" },
      { k: "source", l: L2("Paid in as", "طريقة السداد"), type: "select", change: "eqsync", opts: [["cash", L2("Money (cash / bank)", "نقدية")], ["asset", L2("An asset (in kind)", "أصل عيني")]] },
      { k: "method", l: t("method"), type: "select", opts: methodOpts() },
      { k: "asset_account", l: L2("Asset account", "حساب الأصل"), type: "select", opts: accts(["Asset"], ["fixed_asset", "intangible", "investment", "inventory", "other_noncurrent_asset"]) },
      { k: "basis", l: L2("If they pay more (or less) than the share's book value", "إذا دفع أكثر (أو أقل) من القيمة الدفترية للحصة"), type: "select", span: "spanall", opts: [
        ["capital", L2("Credit all of it to the new partner's capital", "يُضاف كله لرأس مال الشريك الجديد")],
        ["bonus", L2("Bonus method — the difference goes to the existing partners by their shares", "طريقة المكافأة — الفرق للشركاء الحاليين بنسبهم")],
        ["premium", L2("Share premium — the extra goes to a share premium reserve", "علاوة إصدار — الزيادة إلى احتياطي علاوة الإصدار")]] },
      { k: "memo", l: t("memo"), span: "spanall" }],
    sync: v => ["date", "name", "share", "amount", "source", "basis", "memo", ...(v.source === "cash" ? ["method"] : ["asset_account"])] },
  exit: { title: ["Partner exit / buy-out", "خروج شريك"], mod: "partners.ownership",
    fields: (d, pre) => [
      { k: "date", l: t("date"), type: "date", v: today(), req: 1 },
      { k: "partner", l: L2("Partner leaving", "الشريك المنسحب"), type: "select", opts: (d.partners || []).filter(p => p.active).map(p => [p.id, `${p.name} — ${L2("capital", "رأس المال")} ${money(p.accounts.capital)} · ${L2("current", "جاري")} ${money(p.accounts.current)}`]), v: pre.partner },
      { k: "settlement", l: L2("Agreed settlement (blank = book value)", "مبلغ التسوية المتفق عليه (فارغ = القيمة الدفترية)"), type: "number" },
      { k: "paid", l: L2("Paid now (blank = all of it)", "المدفوع الآن (فارغ = كامل المبلغ)"), type: "number", help: L2("The rest is owed to them under “Due to departing partners”.", "الباقي يُسجل مستحقاً له في حساب «مستحق للشركاء المنسحبين».") },
      { k: "method", l: t("method"), type: "select", opts: methodOpts() },
      { html: shareBlock(L2("Remaining partners' new shares (optional — blank = in proportion)", "النسب الجديدة للشركاء الباقين (اختياري — فارغ = بالتناسب)"), activePartners()), span: "spanall" },
      { k: "memo", l: t("memo"), span: "spanall" }],
    body: (v, f) => ({ ...v, shares: collectBlock(f, "eqsh") }) },
};

async function equityForm(action, pre = {}) {
  if (action === "set") return ownershipForm();
  const def = EQ[action]; if (!def) return;
  let d = { partners: [] };
  try { d = await api("GET", "/api/partners"); } catch { /* no permission to view the report — lists stay short */ }
  const partnerNames = (S.boot.partners || []).map(p => p.name);
  const html = form(def.fields(d, pre)) + datalist("eqpartners", partnerNames) + `<div id="eqplan" style="margin-top:14px"></div>`;
  drawer(L2(...def.title), html, async (v, f) => {
    const r = await api("POST", "/api/equity/" + action, def.body ? def.body(v, f) : v);
    await refreshBoot(); renderShell(); toast(t("posted"));
    return r;
  }, { submitLabel: L2("Book it", "تسجيل"), onOpen: f => {
    const all = $$("[name]", f).map(el => el.name);
    const vals = () => readForm(f);
    const sync = () => {
      const v = vals();
      if (def.sync) showFields(f, def.sync(v), all);
      $$(".eqblock", f).forEach(b => { b.style.display = !def.blocks || def.blocks(v).includes(b.dataset.block) ? "" : "none"; });
      preview();
    };
    const preview = debounce(async () => {
      const v = readForm(f); const box = $("#eqplan", f); if (!box) return;
      try { box.innerHTML = planHtml(await api("POST", "/api/equity-preview/" + action, def.body ? def.body(v, f) : v)); }
      catch (e) { box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; }
    });
    S.H.eqsync = sync;
    S.H.eqrate = () => { const pid = $("[name=partner]", f).value, r = $("[name=rate]", f);
      const p = (S.boot.partners || []).find(x => String(x.id) === String(pid)); if (r && p) r.value = pctIn(p.loan_rate) || 0; preview(); };
    f.addEventListener("input", preview); f.addEventListener("change", preview);
    if ($("[name=rate]", f) && action !== "loan") S.H.eqrate();
    sync();
  } });
}

function ownershipForm() {
  const ps = activePartners();
  drawer(L2("Change the shares", "تعديل نسب الملكية"), form([
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 },
    { k: "all_units", l: L2("Apply to every business unit too", "تطبيق على كل وحدات الأعمال أيضاً"), type: "checkbox" },
    { html: shareBlock(L2("New shares", "النسب الجديدة"), ps, Object.fromEntries(ps.map(p => [p.id, pctIn(p.default_share)]))), span: "spanall" },
    { html: `<div id="shtot" class="muted"></div><p class="muted">${L2("Use this for a new partnership agreement. Money moving between partners is a share transfer instead.", "استخدمه عند تعديل عقد الشركة. أما انتقال الحصص بمقابل فهو «تنازل عن حصة».")}</p>`, span: "spanall" },
    { k: "memo", l: t("memo"), span: "spanall" }]),
  async (v, f) => { await api("PUT", "/api/equity/ownership", { ...v, shares: collectBlock(f, "eqsh") }); await refreshBoot(); toast(t("saved")); },
  { onOpen: f => { const tot = () => { const s = $$(".eqsh", f).reduce((a, i) => a + (+i.value || 0), 0);
    $("#shtot", f).innerHTML = `${t("total")}: <b class="${Math.abs(s - 100) < 0.01 ? "bal-ok" : "bal-bad"}">${s.toFixed(2)}%</b>`; };
    f.addEventListener("input", tot); tot(); } });
}

PAGES.partners = async (view, parts, q) => {
  const [d, hist] = await Promise.all([api("GET", "/api/partners?" + qs(q)),
    (can("partners.equity") || can("partners.ownership")) ? api("GET", "/api/equity/history").catch(() => ({ rows: [] })) : { rows: [] }]);
  const ed = can("partners.equity", "edit"), tx = can("partners.transactions", "add"), own = can("partners.ownership", "add");
  const shareOf = (pid, bid, def) => { const s = d.shares.find(x => x.partner_id === pid && x.bu_id === bid); return s ? s.share : def; };
  const edit = p => drawer(t("partner"), form([{ k: "name", l: t("name"), v: p.name, req: 1 },
    { k: "default_share", l: t("default_share"), type: "number", v: p.default_share != null ? pctIn(p.default_share) : "", help: p.id ? L2("Changes are kept in the ownership history.", "التغييرات تُحفظ في سجل الملكية.") : "" },
    { k: "active", l: L2("Still a partner", "شريك حالي"), type: "checkbox", v: p.active !== 0 },
    { k: "joined", l: L2("Joined", "تاريخ الانضمام"), type: "date", v: p.joined }, { k: "left_on", l: L2("Left", "تاريخ الخروج"), type: "date", v: p.left_on },
    { k: "capital_interest", l: L2("Interest on capital % a year", "فائدة رأس المال % سنوياً"), type: "number", v: pctIn(p.capital_interest_rate ?? p.capital_interest) || "",
      help: L2("Agreed return on their capital, taken from profit before the rest is shared (not an expense).", "عائد متفق عليه على رأس المال يُخصم من الربح قبل توزيع الباقي (ليس مصروفاً).") },
    { k: "loan_rate", l: L2("Interest on their loans % a year", "فائدة قروضه % سنوياً"), type: "number", v: pctIn(p.loan_rate) || "" },
    { k: "notes", l: t("notes"), type: "textarea", v: p.notes, span: "spanall" },
    { html: `<h4>${t("ownership")}</h4>`, span: "spanall" }, ...d.units.map(u => ({ k: "s_" + u.id, l: u.code + " %", type: "number", v: p.id ? pctIn(shareOf(p.id, u.id, p.default_share)) : "" }))]),
  async v => { const body = { ...v, default_share: (+v.default_share || 0) / 100, shares: {} };
    d.units.forEach(u => { body.shares[u.id] = v["s_" + u.id] === "" ? "" : (+v["s_" + u.id] || 0) / 100; delete body["s_" + u.id]; });
    if (p.id) await api("PUT", "/api/partners/" + p.id, body); else { const r = await api("POST", "/api/partners", body); await api("PUT", "/api/partners/" + r.id, body); }
    await refreshBoot(); toast(t("saved")); },
  { extraButtons: p.id && ed ? `<button type="button" class="btn danger" data-a="pdel">${t("delete")}</button>` : "" });
  S.H.pnew = () => edit({ active: 1, joined: today() });
  S.H.pedit = el => { const p = d.partners.find(x => String(x.id) === el.dataset.id); edit({ ...p });
    S.H.pdel = () => confirmDelete(p.name, "/api/partners/" + p.id, refreshBoot); };
  S.H.eqa = el => equityForm(el.dataset.act, el.dataset.pid ? { partner: el.dataset.pid } : {});
  S.H.eqdraw = () => openDoc("partner_tx");
  S.H.eqreplace = () => equityForm("transfer", { all: true });
  S.H.equndo = async el => { if (!confirm(L2("Undo this ownership change? The shares go back to what they were before.", "التراجع عن تغيير الملكية هذا؟ تعود النسب كما كانت."))) return;
    try { await api("DELETE", "/api/equity/history/" + el.dataset.id); await refreshBoot(); toast(t("saved")); route(); } catch (e) { fail(e); } };
  S.H.rsave = async () => { try { await api("PUT", "/api/settings", { reserve_pct: (+$("#rpct").value || 0) / 100 }); await refreshBoot(); route(); } catch (e) { fail(e); } };
  const act = (a, l) => `<button data-a="eqa" data-act="${a}">${esc(l)}</button>`;
  const txMenu = tx ? `<div class="dd"><button class="btn pri">+ ${L2("Partner transaction", "حركة شريك")} ▾</button><div class="dd-menu">
      ${act("contribute", L2("Capital contribution", "زيادة رأس المال"))}${act("withdraw", L2("Capital withdrawal", "تخفيض رأس المال"))}
      <button data-a="eqdraw">${L2("Drawing (on account of profit)", "مسحوبات (تحت حساب الأرباح)")}</button>${act("distribute", L2("Profit distribution", "توزيع أرباح"))}
      ${act("loan", L2("Loan from a partner", "قرض من شريك"))}${act("interest", L2("Interest on a partner's loan", "فوائد قرض شريك"))}${act("repay", L2("Repay a loan / interest", "سداد قرض / فوائد"))}</div></div>` : "";
  const ownMenu = own ? `<div class="dd"><button class="btn">${L2("Ownership", "الملكية")} ▾</button><div class="dd-menu">
      ${act("transfer", L2("Transfer / sell a share", "تنازل / بيع حصة"))}<button data-a="eqreplace">${L2("Replace a partner (whole share)", "استبدال شريك (كامل الحصة)")}</button>
      ${act("admit", L2("Admit a new partner", "دخول شريك جديد"))}${act("exit", L2("Partner exit / buy-out", "خروج شريك"))}${act("set", L2("Change the shares directly", "تعديل النسب مباشرة"))}</div></div>` : "";
  const cp = d.cash_position, anyInt = d.partners.some(p => p.capital_interest);
  const acctRow = p => { const a = p.accounts; return `<tr class="click ${p.active ? "" : "void"}" data-a="${ed ? "pedit" : ""}" data-id="${p.id}">
      <td><b>${esc(p.name)}</b>${p.active ? "" : ` <span class="chip">${L2("former partner", "شريك سابق")}${p.left_on ? " · " + fd(p.left_on) : ""}</span>`}${p.joined ? `<div class="lines">${L2("since", "منذ")} ${fd(p.joined)}</div>` : ""}</td>
      <td class="num"><b>${pct(p.default_share)}</b></td><td class="num">${money(a.capital)}</td><td class="num">${money(a.current)}</td><td class="num">${money(a.drawings)}</td>
      <td class="num">${money(a.loan)}${p.loan_rate ? `<div class="lines">@ ${pct(p.loan_rate)}</div>` : ""}</td><td class="num">${money(a.interest)}</td><td class="num">${money(a.distributions_payable + a.due_to)}</td></tr>`; };
  const tot = k => d.partners.reduce((s, p) => s + (p.accounts[k] || 0), 0);
  view.innerHTML = `<div class="toolbar">${txMenu}${ownMenu}${ed ? `<button class="btn" data-a="pnew">+ ${t("partner")}</button>` : ""}<span class="spacer"></span>
      ${exportMenu("vouchers", { kinds: "Capital Contribution,Capital Withdrawal,Owner Drawing,Profit Distribution,Partner Loan,Partner Loan Repayment,Partner Loan Interest,Share Transfer,Partner Admission,Partner Exit", title: t("m_partners") })}</div>` + filterBar(q, ["dates"]) +
    `<div class="card"><h2>${L2("Partners' accounts", "حسابات الشركاء")}</h2><div class="tbl"><table><thead><tr><th>${t("partner")}</th><th class="num">${L2("Ownership", "الملكية")}</th><th class="num">${L2("Capital", "رأس المال")}</th><th class="num">${L2("Current account", "جاري الشريك")}</th>
      <th class="num">${L2("Drawings", "المسحوبات")}</th><th class="num">${L2("Loans to the company", "قروض للشركة")}</th><th class="num">${L2("Interest owed", "فوائد مستحقة")}</th><th class="num">${L2("Owed to them", "مستحقات لهم")}</th></tr></thead><tbody>
      ${d.partners.map(acctRow).join("")}
      <tr class="total"><td>${t("total")}</td><td class="num ${Math.abs(d.default_check - 1) < 0.001 ? "pos" : "neg"}">${pct(d.default_check)}</td><td class="num">${money(tot("capital"))}</td><td class="num">${money(tot("current"))}</td><td class="num">${money(tot("drawings"))}</td><td class="num">${money(tot("loan"))}</td><td class="num">${money(tot("interest"))}</td><td class="num">${money(tot("distributions_payable") + tot("due_to"))}</td></tr></tbody></table></div>
      <p class="muted" style="font-size:.85em;margin:8px 0 0">${L2("Capital = what each partner invested (changes only with contributions, withdrawals, transfers, admissions and exits). Loans are debts the company owes them. Drawings are taken on account of profit.", "رأس المال = ما استثمره كل شريك (يتغير فقط بالزيادة أو التخفيض أو التنازل أو الدخول أو الخروج). القروض ديون على الشركة. المسحوبات تحت حساب الأرباح.")}</p></div>
    <div class="card"><h2>${t("reserve_pct")}</h2><div class="row"><input id="rpct" type="number" step="1" min="0" max="100" value="${+(d.reserve_pct * 100).toFixed(2)}" style="max-width:120px" ${ed ? "" : "disabled"}>
      ${ed ? `<button class="btn" data-a="rsave">${t("save")}</button>` : ""}<span class="muted">${t("company_result")}: ${money(d.company_result)}</span></div>
      <div class="banner ${cp.gap > 0 ? "err" : "ok"}" style="margin-top:10px">${t("cash_floor")}: ${money(cp.floor)} · ${t("cash")}: ${money(cp.cash)}${cp.gap > 0 ? ` — ${L2("nothing is safely distributable until this gap closes", "لا يوجد ما يمكن توزيعه بأمان حتى تُسد الفجوة")} (${money(cp.gap)})` : " ✓"}</div></div>
    <div class="card"><h2>${L2("Profit shares", "حصص الأرباح")}</h2><div class="tbl"><table><thead><tr><th>${t("partner")}</th>${d.units.map(u => `<th class="num">${esc(u.code)}</th>`).join("")}${anyInt ? `<th class="num">${L2("Interest on capital", "فائدة رأس المال")}</th>` : ""}<th class="num">${t("profit_share")}</th><th class="num">${t("distributable")}</th><th class="num">${t("drawn")}</th><th class="num">${t("balance")}</th></tr></thead><tbody>
      ${d.partners.filter(p => p.active || p.profit_share || p.drawn).map(p => `<tr class="click" data-a="${ed ? "pedit" : ""}" data-id="${p.id}"><td>${esc(p.name)}<div class="lines">${t("default")} ${pct(p.default_share)}</div></td>${d.units.map(u => `<td class="num">${pct(p.units[u.code].share)}<div class="lines">${money(p.units[u.code].profit)}</div></td>`).join("")}
        ${anyInt ? `<td class="num">${money(p.capital_interest)}</td>` : ""}<td class="num">${money(p.profit_share)}</td><td class="num">${money(p.distributable)}</td><td class="num">${money(p.drawn)}</td><td class="num ${p.balance < 0 ? "neg" : "pos"}"><b>${money(p.balance)}</b></td></tr>`).join("")}
      <tr class="total"><td>${t("total")}</td>${d.units.map(u => `<td class="num ${Math.abs(d.share_checks[u.code] - 1) < 0.001 ? "pos" : "neg"}">${pct(d.share_checks[u.code])}</td>`).join("")}${anyInt ? `<td class="num">${money(d.partners.reduce((s, p) => s + p.capital_interest, 0))}</td>` : ""}<td class="num">${money(d.partners.reduce((s, p) => s + p.profit_share, 0))}</td><td class="num">${money(d.partners.reduce((s, p) => s + p.distributable, 0))}</td><td class="num">${money(d.partners.reduce((s, p) => s + p.drawn, 0))}</td><td class="num">${money(d.partners.reduce((s, p) => s + p.balance, 0))}</td></tr>
      </tbody></table></div></div>
    ${hist.rows.length ? `<div class="card"><h2>${L2("Ownership history", "سجل الملكية")}</h2><div class="tbl"><table><thead><tr><th>${t("date")}</th><th>${t("kind")}</th><th>${t("description")}</th><th>${L2("Shares", "النسب")}</th><th></th></tr></thead><tbody>
      ${hist.rows.map(h => `<tr><td>${fd(h.date)}</td><td><span class="chip">${esc(tl(h.kind))}</span></td><td>${esc(h.memo)}${h.entry_id ? ` · <a href="#" data-a="openv" data-id="${h.entry_id}">#${h.entry_id}</a>` : ""}${h.created_by ? `<div class="lines">${esc(h.created_by)}</div>` : ""}</td>
        <td>${h.moves.map(m => `<bdi>${esc(m.partner)}</bdi> <span dir="ltr">${pct(m.before)} → <b>${pct(m.after)}</b></span>`).join("<br>")}</td>
        <td>${h.latest && own ? `<button class="btn sm" data-a="equndo" data-id="${h.id}">${L2("Undo", "تراجع")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div>
      <p class="muted" style="font-size:.85em">${L2("Only the latest change can be undone. A change booked with a journal entry is also undone by voiding or reversing that entry.", "يمكن التراجع عن آخر تغيير فقط. التغيير المرتبط بقيد يُلغى أيضاً بإلغاء القيد أو عكسه.")}</p></div>` : ""}`;
};

// ================================================================= fixed assets
const assetAccounts = () => accts(["Asset"], ["fixed_asset", "intangible", "investment", "other_noncurrent_asset"]);
// ================================================================== loans
const LOAN_O = {
  dir: () => [["borrowed", L2("The company borrowed — we owe it", "الشركة اقترضت — مبلغ علينا")], ["lent", L2("The company lent — it is owed to us", "الشركة أقرضت — مبلغ لنا")]],
  itype: () => [["none", L2("Interest-free", "بدون فوائد")], ["declining", L2("Annual rate on the remaining balance (declining)", "نسبة سنوية على الرصيد المتبقي (متناقصة)")],
    ["flat", L2("Flat annual rate on the full amount", "نسبة سنوية ثابتة على أصل القرض")], ["fixed", L2("A fixed total interest amount", "مبلغ فوائد إجمالي ثابت")]],
  sched: () => [["equal", L2("Equal installments", "أقساط متساوية")], ["equal_principal", L2("Equal principal + interest on the balance", "أصل متساوٍ + فائدة على الرصيد")],
    ["bullet", L2("Interest only — principal at the end", "فوائد فقط — الأصل في النهاية")], ["custom", L2("Custom installments (type them in)", "أقساط مخصصة (إدخال يدوي)")],
    ["none", L2("Flexible — no fixed schedule", "مرن — بدون جدول سداد")]],
  freq: () => [["1", L2("Monthly", "شهري")], ["2", L2("Every 2 months", "كل شهرين")], ["3", L2("Quarterly", "ربع سنوي")], ["4", L2("Every 4 months", "كل 4 أشهر")],
    ["6", L2("Every 6 months", "نصف سنوي")], ["12", L2("Yearly", "سنوي")]],
  state: s => ({ paid: ["ok", L2("Paid", "مسدد")], partial: ["warn", L2("Part paid", "مسدد جزئياً")], open: ["", L2("Upcoming", "قادم")], overdue: ["bad", L2("Overdue", "متأخر")] }[s] || ["", s]),
};
const moneyAccts = () => accts(null, ["cash"]);
const loanWho = dir => dir === "borrowed" ? L2("Lender", "المُقرض") : L2("Borrower", "المقترض");
const kpiBox = (l, v, cls = "", sub = "") => `<div class="kpi"><div class="l">${esc(l)}</div><div class="v ${cls}">${typeof v === "number" ? money(v) : v}</div>${sub ? `<div class="muted" style="font-size:.78em">${sub}</div>` : ""}</div>`;
function previewBox(res) {
  const ents = res.entries || [res];
  return ents.map(e => `<div class="tbl" style="margin-top:6px"><table><thead><tr><th>${t("account")}</th><th class="num">${t("debit")}</th><th class="num">${t("credit")}</th></tr></thead><tbody>
    ${(e.lines || []).map(l => `<tr><td>${esc(l.account)} ${esc(S.lang === "ar" && l.name_ar ? l.name_ar : l.name || "")}${l.party ? ` <span class="muted">· ${esc(l.party)}</span>` : ""}</td><td class="num">${l.debit ? money(l.debit) : ""}</td><td class="num">${l.credit ? money(l.credit) : ""}</td></tr>`).join("")}</tbody></table></div>`).join("")
    + (res.notes || []).map(n => `<div class="muted" style="margin-top:4px">${esc(n)}</div>`).join("");
}
// live preview of what an action will post
function wirePreview(f, url, body) {
  let tm;
  const run = async () => { const box = $("#lpv", f); if (!box) return;
    try { box.innerHTML = previewBox(await api("POST", url, body())); } catch (e) { box.innerHTML = `<div class="muted">${esc(e.message)}</div>`; } };
  f.addEventListener("input", () => { clearTimeout(tm); tm = setTimeout(run, 350); });
  f.addEventListener("change", () => { clearTimeout(tm); tm = setTimeout(run, 150); });
  run();
}

PAGES.loans = async (view, parts, q) => {
  const d = await api("GET", "/api/loans?" + qs(q));
  const add = can("accounting.loans", "add");
  S.H.lnnew = () => loanForm();
  const B = d.totals.borrowed, Lt = d.totals.lent;
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="lnnew">+ ${L2("New loan", "قرض جديد")}</button>` : ""}<span class="spacer"></span>${exportMenu("loans", q)}</div>
    <div class="kpis">${kpiBox(L2("The company owes", "على الشركة"), B.outstanding, "", `${B.count} ${L2("loans", "قرض")}${B.accrued ? ` · ${L2("accrued interest", "فوائد مستحقة")} ${money(B.accrued)}` : ""}`)}
      ${kpiBox(L2("Due now (we pay)", "مستحق الآن (سداد)"), B.due_now, B.overdue ? "neg" : "", B.overdue ? `${L2("overdue", "متأخر")} ${money(B.overdue)}` : "")}
      ${kpiBox(L2("Owed to the company", "للشركة"), Lt.outstanding, "pos", `${Lt.count} ${L2("loans", "قرض")}${Lt.accrued ? ` · ${L2("interest receivable", "فوائد مستحقة التحصيل")} ${money(Lt.accrued)}` : ""}`)}
      ${kpiBox(L2("Due now (we collect)", "مستحق الآن (تحصيل)"), Lt.due_now, Lt.overdue ? "neg" : "", Lt.overdue ? `${L2("overdue", "متأخر")} ${money(Lt.overdue)}` : "")}</div>` +
    filterBar(q, ["q", ["select", "direction", L2("Type", "النوع"), [["borrowed", L2("Borrowed", "مأخوذة")], ["lent", L2("Given", "ممنوحة")]]],
      ["select", "state", t("status"), [["active", L2("Active", "نشط")], ["overdue", L2("Overdue", "متأخر")], ["closed", L2("Closed", "مغلق")]]]]) +
    `<div class="card">${d.rows.length ? `<div class="tbl stack"><table><thead><tr><th>${t("code")}</th><th>${L2("Lender / borrower", "الجهة")}</th><th>${L2("Type", "النوع")}</th><th>${L2("Start", "البداية")}</th><th>${L2("Interest", "الفائدة")}</th><th class="num">${t("amount")}</th><th class="num">${L2("Outstanding", "المتبقي")}</th><th>${L2("Next installment", "القسط القادم")}</th><th>${t("status")}</th></tr></thead><tbody>
      ${d.rows.map(r => `<tr class="click ${r.status === "closed" ? "void" : ""}" data-href="#/loan/${r.id}"><td><b>${esc(r.code)}</b></td><td>${esc(r.party)}${r.purpose ? `<div class="lines">${esc(r.purpose)}</div>` : ""}</td>
        <td><span class="chip ${r.direction === "borrowed" ? "" : "ok"}">${r.direction === "borrowed" ? L2("Borrowed", "مأخوذ") : L2("Given", "ممنوح")}</span></td><td>${fd(r.start_date)}</td>
        <td>${r.interest_type === "none" ? L2("None", "بدون") : r.interest_type === "fixed" ? money(r.fixed_interest) : pct(r.rate) + (r.interest_type === "flat" ? " " + L2("flat", "ثابتة") : "")}</td>
        <td class="num">${money(r.principal || r.opening_balance)}</td><td class="num"><b>${money(r.outstanding)}</b>${r.accrued ? `<div class="lines">+ ${money(r.accrued)} ${L2("interest", "فوائد")}</div>` : ""}</td>
        <td>${r.next_due ? `${fd(r.next_due)} · ${money(r.next_amount)}` : "—"}</td>
        <td>${r.status === "closed" ? `<span class="chip">${L2("Closed", "مغلق")}</span>` : r.overdue ? `<span class="chip bad">${L2("Overdue", "متأخر")} ${money(r.overdue)}</span>` : r.settled ? `<span class="chip ok">${L2("Settled — close it", "مسدد — أغلقه")}</span>` : `<span class="chip ok">${L2("Active", "نشط")}</span>`}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("No loans yet. Add a bank loan the company took, or money the company lent to someone — with or without interest — and track every installment.", "لا توجد قروض بعد. أضف قرضاً أخذته الشركة من بنك أو مبلغاً أقرضته الشركة لأحد — بفائدة أو بدونها — وتابع كل قسط.")}</div>`}</div>`;
};

PAGES.loan = async (view, parts) => {
  const d = await api("GET", "/api/loans/" + encodeURIComponent(parts[1]));
  const L = d.loan, B = L.direction === "borrowed";
  $("#ttl").textContent = `${L.code} — ${L.party}`;
  const add = can("accounting.loans", "add"), ed = can("accounting.loans", "edit"), live = L.status === "active";
  Object.assign(S.H, { lnpay: () => loanAction(d, "pay"), lnint: () => loanAction(d, "interest"), lnfee: () => loanAction(d, "fee"), lndraw: () => loanAction(d, "draw"),
    lnoff: () => loanAction(d, "writeoff"), lnedit: () => loanForm(d),
    lnclose: async () => { try { await api("POST", `/api/loans/${L.id}/close`, {}); toast(t("saved")); route(); } catch (e) { fail(e); } },
    lnreopen: async () => { try { await api("POST", `/api/loans/${L.id}/reopen`, {}); route(); } catch (e) { fail(e); } },
    lndel: () => confirmDelete(L.code, "/api/loans/" + L.id, async () => { location.hash = "#/loans"; }) });
  const row = (l, v) => v === "" || v == null ? "" : `<tr><td class="muted">${esc(l)}</td><td>${v}</td></tr>`;
  const acc = c => `${esc(c)} ${esc(aname(S.boot.accounts.find(x => x.code === c) || {}))}`;
  const interest = L.interest_type === "none" ? L2("Interest-free", "بدون فوائد") : L.interest_type === "fixed" ? `${money(L.fixed_interest)} ${L2("in total", "إجمالي")}`
    : `${pct(L.rate)} ${L2("a year", "سنوياً")} — ${L.interest_type === "flat" ? L2("flat on the full amount", "ثابتة على أصل القرض") : L2("on the remaining balance", "على الرصيد المتبقي")}`;
  const sched = (LOAN_O.sched().find(x => x[0] === L.schedule_type) || [])[1];
  const freq = (LOAN_O.freq().find(x => x[0] === String(L.frequency)) || [])[1];
  view.innerHTML = `<div class="toolbar"><a class="btn" href="#/loans">← ${t("m_loans")}</a>
      ${live && add ? `<button class="btn pri" data-a="lnpay">${B ? L2("Record a repayment", "تسجيل سداد") : L2("Record a collection", "تسجيل تحصيل")}</button>
        <button class="btn" data-a="lnint">${L2("Book interest", "تسجيل فوائد")}</button><button class="btn" data-a="lndraw">${B ? L2("Receive more", "استلام دفعة إضافية") : L2("Lend more", "صرف دفعة إضافية")}</button>
        <button class="btn" data-a="lnfee">${L2("Charges / fees", "مصاريف / رسوم")}</button><button class="btn" data-a="lnoff">${B ? L2("Lender forgave it", "إعفاء من المقرض") : L2("Write off", "إعدام القرض")}</button>` : ""}
      <span class="spacer"></span>
      ${ed ? `<button class="btn" data-a="lnedit">${L2("Edit terms", "تعديل الشروط")}</button>${live ? `<button class="btn" data-a="lnclose">${L2("Close loan", "إغلاق القرض")}</button>` : `<button class="btn" data-a="lnreopen">${L2("Reopen", "إعادة فتح")}</button>`}
        ${!d.entries.length ? `<button class="btn danger" data-a="lndel">${t("delete")}</button>` : ""}` : ""}
      ${exportMenu("loan_schedule", { code: L.code })}</div>
    ${L.settled && live ? `<div class="banner info">${L2("Fully repaid — you can close this loan.", "تم السداد بالكامل — يمكنك إغلاق القرض.")}</div>` : ""}
    <div class="kpis">${kpiBox(B ? L2("Still owed by the company", "المتبقي على الشركة") : L2("Still owed to the company", "المتبقي للشركة"), L.outstanding, B ? "" : "pos", `${L2("of", "من")} ${money(L.drawn)} · ${Math.round(L.progress * 100)}% ${L2("repaid", "مسدد")}`)}
      ${kpiBox(L2("Accrued interest", "فوائد مستحقة"), L.accrued, "", L.interest_to_date ? `${L2("not booked yet", "لم تُسجل بعد")}: ${money(L.interest_to_date)}` : "")}
      ${kpiBox(L2("Overdue", "متأخرات"), L.overdue, L.overdue ? "neg" : "")}
      ${kpiBox(L2("Next installment", "القسط القادم"), L.next_due ? money(L.next_amount) : "—", "", L.next_due ? fd(L.next_due) : "")}
      ${kpiBox(B ? L2("Interest paid", "فوائد مدفوعة") : L2("Interest collected", "فوائد محصلة"), L.interest_paid, "", `${L2("booked in total", "المسجل إجمالاً")}: ${money(L.interest_recognized)}`)}</div>
    <div class="card"><h2>${L2("Terms", "الشروط")}</h2><div class="tbl"><table><tbody>
      ${row(L2("Type", "النوع"), B ? L2("Borrowed by the company", "قرض على الشركة") : L2("Lent by the company", "قرض ممنوح من الشركة"))}
      ${row(loanWho(L.direction), esc(L.party))}${row(L2("Purpose", "الغرض"), esc(L.purpose))}
      ${row(t("amount"), money(L.principal || L.opening_balance))}${L.opening_balance ? row(L2("Balance when registered", "الرصيد عند التسجيل"), `${money(L.opening_balance)} · ${fd(L.opening_date)}`) : ""}
      ${row(L2("Start", "البداية"), fd(L.start_date))}${row(L2("Interest", "الفائدة"), interest)}
      ${row(L2("Repayment", "السداد"), `${esc(sched || "")}${L.schedule_type !== "none" && L.schedule_type !== "custom" ? ` · ${esc(freq || "")} · ${L.term_months} ${L2("months", "شهر")}` : ""}`)}
      ${row(L2("Final due date", "تاريخ الاستحقاق النهائي"), L.maturity ? fd(L.maturity) : "")}
      ${row(L2("Loan account", "حساب القرض"), acc(L.account))}${row(L2("Interest account", "حساب الفوائد"), acc(L.interest_account))}${row(L2("Accrued interest account", "حساب الفوائد المستحقة"), acc(L.accrued_account))}
      ${L.bu_code ? row(t("bu"), esc(L.bu_code)) : ""}${row(t("notes"), esc(L.notes))}${L.status === "closed" ? row(L2("Closed on", "أُغلق في"), fd(L.closed_on)) : ""}</tbody></table></div></div>
    <div class="card"><h2>${L2("Repayment schedule", "جدول السداد")}</h2>${d.schedule.length ? `<div class="tbl stack"><table><thead><tr><th>#</th><th>${L2("Due", "الاستحقاق")}</th><th class="num">${L2("Principal", "الأصل")}</th><th class="num">${L2("Interest", "الفائدة")}</th><th class="num">${L2("Installment", "القسط")}</th><th class="num">${L2("Balance after", "الرصيد بعده")}</th><th>${t("status")}</th></tr></thead><tbody>
      ${d.schedule.map(r => { const [c, lbl] = LOAN_O.state(r.state); return `<tr class="${r.state === "paid" ? "void" : ""}"><td>${r.n}</td><td>${fd(r.due)}</td><td class="num">${money(r.principal)}</td><td class="num">${money(r.interest)}</td><td class="num"><b>${money(r.payment)}</b></td><td class="num">${money(r.balance)}</td>
        <td><span class="chip ${c}">${esc(lbl)}</span>${r.state === "partial" || r.state === "overdue" ? `<div class="lines">${L2("left", "المتبقي")} ${money(r.left)}</div>` : ""}</td></tr>`; }).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("Flexible loan — no fixed installments. Record payments whenever they happen; interest is worked out on the balance day by day.", "قرض مرن — بدون أقساط ثابتة. سجّل الدفعات وقت حدوثها وتُحسب الفوائد على الرصيد يومياً.")}</div>`}</div>
    <div class="card"><h2>${L2("Movements", "الحركات")}</h2>${voucherRows(d.entries)}</div>`;
};

async function loanForm(d) {
  const L = d ? d.loan : null, ed = !!L, hasEntries = d && d.entries.length;
  const accOpts = (dir, which) => which === "account" ? accts(dir === "borrowed" ? ["Liability"] : ["Asset"]) : which === "interest" ? accts(dir === "borrowed" ? ["Expense", "COGS"] : ["Revenue"]) : accts(dir === "borrowed" ? ["Liability"] : ["Asset"]);
  const dflt = (dir, term) => dir === "borrowed" ? { account: accByRole(+term > 12 ? "long_term_debt" : "short_term_debt"), interest_account: accByRole("interest"), accrued_account: accByRole("accrued_interest") }
    : { account: accByRole(+term > 12 ? "loans_receivable_lt" : "loans_receivable") || accByRole("loans_receivable"), interest_account: accByRole("interest_income"), accrued_account: accByRole("interest_receivable") };
  const dir0 = L ? L.direction : "borrowed", d0 = dflt(dir0, L ? L.term_months : 12);
  let rows = (d && d.custom_schedule || []).map(r => ({ due: r.due, principal: r.principal / 100, interest: r.interest / 100 }));
  const names = [...new Set([...(S.boot.parties || []).map(p => p.name), ...(S.boot.partners || []).map(p => p.name)])];
  const html = form([
    { k: "direction", l: L2("Loan", "القرض"), type: "select", opts: LOAN_O.dir(), v: dir0, change: "lnsync", dis: hasEntries, span: "spanall" },
    { k: "party", l: `${L2("Lender / borrower", "الجهة (المقرض / المقترض)")}`, v: L ? L.party : "", list: "lnparties", req: 1 },
    { k: "purpose", l: L2("Purpose", "الغرض"), v: L ? L.purpose : "", ph: L2("e.g. equipment finance, employee advance", "مثال: تمويل معدات، سلفة موظف") },
    { k: "principal", l: L2("Loan amount", "مبلغ القرض"), type: "number", v: L ? (L.principal || "") : "", req: !ed },
    { k: "start_date", l: L2("Start date", "تاريخ البداية"), type: "date", v: L ? L.start_date : today(), req: 1 },
    { k: "interest_type", l: L2("Interest", "الفائدة"), type: "select", opts: LOAN_O.itype(), v: L ? L.interest_type : "none", change: "lnsync" },
    { k: "rate", l: L2("Interest rate % a year", "نسبة الفائدة % سنوياً"), type: "number", v: L ? (L.rate_pct || "") : "" },
    { k: "fixed_interest", l: L2("Total interest amount", "إجمالي مبلغ الفوائد"), type: "number", v: L ? (L.fixed_interest || "") : "" },
    { k: "schedule_type", l: L2("Repayment", "طريقة السداد"), type: "select", opts: LOAN_O.sched(), v: L ? L.schedule_type : "equal", change: "lnsync" },
    { k: "term_months", l: L2("Term (months)", "المدة (بالأشهر)"), type: "number", v: L ? L.term_months : 12, change: "lnsync" },
    { k: "frequency", l: L2("Installments every", "الأقساط كل"), type: "select", opts: LOAN_O.freq(), v: L ? String(L.frequency) : "1" },
    { k: "first_due", l: L2("First installment on", "أول قسط في"), type: "date", v: L ? L.first_due : "", help: L2("Blank = one period after the start", "فارغ = بعد فترة من البداية") },
    ...(ed ? (L.opening_balance ? [{ k: "opening_balance", l: L2("Balance when registered", "الرصيد عند التسجيل"), type: "number", v: L.opening_balance }, { k: "opening_date", l: L2("As of", "في تاريخ"), type: "date", v: L.opening_date }] : [])
      : [{ k: "booking", l: L2("The money", "المبلغ"), type: "select", change: "lnsync", span: "spanall", opts: [["money", L2("Moved now — book it in cash / bank", "تم استلامه / صرفه الآن — يُسجل في الخزينة / البنك")],
          ["opening", L2("Already in the books — just track the remaining balance", "مسجل مسبقاً في الدفاتر — متابعة الرصيد المتبقي فقط")], ["none", L2("Record the money later (Receive / Lend more)", "يُسجل المبلغ لاحقاً")]] },
        { k: "paid_from", l: L2("Cash / bank account", "حساب الخزينة / البنك"), type: "select", opts: moneyAccts(), v: accByRole("bank") },
        { k: "fees", l: L2("Fees deducted from the amount", "رسوم مخصومة من المبلغ"), type: "number" },
        { k: "opening_balance", l: L2("Balance still outstanding", "الرصيد المتبقي"), type: "number" }, { k: "opening_date", l: L2("As of", "في تاريخ"), type: "date", v: today() }]),
    { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—"), v: L ? L.bu_id || "" : "" },
    { k: "code", l: t("code"), v: L ? L.code : "", ph: L2("automatic", "تلقائي") },
    { k: "account", l: L2("Loan account", "حساب القرض"), type: "select", opts: accOpts(dir0, "account"), v: L ? L.account : d0.account },
    { k: "interest_account", l: L2("Interest account", "حساب الفوائد"), type: "select", opts: accOpts(dir0, "interest"), v: L ? L.interest_account : d0.interest_account },
    { k: "accrued_account", l: L2("Accrued interest account", "حساب الفوائد المستحقة"), type: "select", opts: accOpts(dir0, "accrued"), v: L ? L.accrued_account : d0.accrued_account },
    { k: "notes", l: t("notes"), type: "textarea", v: L ? L.notes : "", span: "spanall" },
    { html: `<div id="lncustom"></div><h4 style="margin:10px 0 0">${L2("Schedule preview", "معاينة جدول السداد")}</h4><div id="lnprev" class="muted">—</div>`, span: "spanall" }])
    + partyList("lnparties", names);
  const collect = f => { const v = readForm(f); if (v.schedule_type === "custom") v.schedule = rows.filter(r => r.due); return v; };
  drawer(ed ? `${t("edit")} — ${L.code}` : L2("New loan", "قرض جديد"), html, async (v, f) => {
    const body = collect(f);
    if (ed) { await api("PUT", "/api/loans/" + L.id, body); toast(t("saved")); }
    else { const r = await api("POST", "/api/loans", body); toast(t("saved")); closeDrawer(); location.hash = "#/loan/" + r.id; return false; }
  }, { onOpen: f => {
    let tm;
    const show = (k, on) => { const el = $(`[name=${k}]`, f); if (el) { el.closest("label").style.display = on ? "" : "none"; el.dataset.skip = on ? "" : "1"; } };
    const drawCustom = () => { const box = $("#lncustom", f); const on = $("[name=schedule_type]", f).value === "custom";
      box.innerHTML = on ? `<h4 style="margin:10px 0 6px">${L2("Installments", "الأقساط")}</h4><div class="tbl"><table><thead><tr><th>${L2("Due", "الاستحقاق")}</th><th class="num">${L2("Principal", "الأصل")}</th><th class="num">${L2("Interest", "الفائدة")}</th><th></th></tr></thead><tbody>
        ${rows.map((r, i) => `<tr><td><input type="date" data-skip="1" value="${esc(r.due || "")}" data-input="lncrow" data-i="${i}" data-k="due"></td><td><input class="num" type="number" step="any" data-skip="1" value="${r.principal ?? ""}" data-input="lncrow" data-i="${i}" data-k="principal"></td>
          <td><input class="num" type="number" step="any" data-skip="1" value="${r.interest ?? ""}" data-input="lncrow" data-i="${i}" data-k="interest"></td><td><button type="button" class="btn sm danger" data-a="lncdel" data-i="${i}">✕</button></td></tr>`).join("")}</tbody></table></div>
        <div class="row" style="margin-top:6px"><button type="button" class="btn sm" data-a="lncadd">+ ${L2("Installment", "قسط")}</button><button type="button" class="btn sm" data-a="lncfill">${L2("Fill from equal installments", "تعبئة من أقساط متساوية")}</button></div>` : ""; };
    const preview = async () => { const box = $("#lnprev", f); try { const r = await api("POST", "/api/loans-preview/schedule", collect(f));
      box.innerHTML = r.rows.length ? `<div class="tbl"><table><thead><tr><th>#</th><th>${L2("Due", "الاستحقاق")}</th><th class="num">${L2("Principal", "الأصل")}</th><th class="num">${L2("Interest", "الفائدة")}</th><th class="num">${L2("Installment", "القسط")}</th><th class="num">${L2("Balance", "الرصيد")}</th></tr></thead><tbody>
        ${r.rows.map(x => `<tr><td>${x.n}</td><td>${fd(x.due)}</td><td class="num">${money(x.principal)}</td><td class="num">${money(x.interest)}</td><td class="num">${money(x.payment)}</td><td class="num">${money(x.balance)}</td></tr>`).join("")}
        <tr class="total"><td colspan="2">${t("total")}</td><td class="num">${money(r.totals.principal)}</td><td class="num">${money(r.totals.interest)}</td><td class="num">${money(r.totals.payment)}</td><td></td></tr></tbody></table></div>`
        : `<span class="muted">${$("[name=schedule_type]", f).value === "none" ? L2("Flexible — no installments.", "مرن — بدون أقساط.") : L2("Enter the amount and term to see the installments.", "أدخل المبلغ والمدة لعرض الأقساط.")}</span>`; } catch (e) { box.textContent = e.message; } };
    S.H.lnsync = el => { const g = k => ($(`[name=${k}]`, f) || {}).value;
      const it = g("interest_type"), st = g("schedule_type"), bk = g("booking");
      show("rate", it === "declining" || it === "flat"); show("fixed_interest", it === "fixed");
      show("frequency", st !== "none"); show("first_due", st !== "none");
      if (!ed) { show("paid_from", bk === "money"); show("fees", bk === "money"); show("opening_balance", bk === "opening"); show("opening_date", bk === "opening"); }
      if (el && (el.name === "direction" || el.name === "term_months")) { const dir = g("direction"), dd = dflt(dir, g("term_months"));
        [["account", "account"], ["interest_account", "interest"], ["accrued_account", "accrued"]].forEach(([k, w]) => { const s = $(`[name=${k}]`, f);
          if (el.name === "direction") s.innerHTML = accOpts(dir, w).map(([c, n]) => `<option value="${esc(c)}">${esc(n)}</option>`).join("");
          if (!ed || el.name === "direction") s.value = dd[k] || s.value; }); }
      if (!el || el.name === "schedule_type") drawCustom(); };
    S.H.lncrow = el => { rows[+el.dataset.i][el.dataset.k] = el.value; clearTimeout(tm); tm = setTimeout(preview, 300); };
    S.H.lncadd = () => { const last = rows[rows.length - 1]; rows.push({ due: last && last.due ? last.due : $("[name=start_date]", f).value, principal: "", interest: "" }); drawCustom(); };
    S.H.lncdel = el => { rows.splice(+el.dataset.i, 1); drawCustom(); preview(); };
    S.H.lncfill = async () => { const v = { ...readForm(f), schedule_type: "equal" }; const r = await api("POST", "/api/loans-preview/schedule", v);
      rows = r.rows.map(x => ({ due: x.due, principal: x.principal, interest: x.interest })); drawCustom(); preview(); };
    f.addEventListener("input", e => { if (!e.target.dataset.input || e.target.dataset.input !== "lncrow") { clearTimeout(tm); tm = setTimeout(preview, 350); } });
    f.addEventListener("change", () => { clearTimeout(tm); tm = setTimeout(preview, 150); });
    S.H.lnsync(); preview(); } });
}

async function loanAction(d, action) {
  if (typeof d !== "object" || !d.loan) d = await api("GET", "/api/loans/" + d);
  const L = d.loan, B = L.direction === "borrowed", sg = d.suggest;
  const acc = (types) => accts(types);
  const titles = { pay: B ? L2("Repayment", "سداد قسط / دفعة") : L2("Collection", "تحصيل قسط / دفعة"), interest: L2("Book interest", "تسجيل فوائد"), draw: B ? L2("Money received", "استلام مبلغ القرض") : L2("Money lent", "صرف مبلغ القرض"),
    fee: L2("Charges / fees", "مصاريف / رسوم"), writeoff: B ? L2("Loan forgiven by the lender", "إعفاء من المقرض") : L2("Write off the loan", "إعدام القرض") };
  const money_ = (lbl) => [{ k: "paid_from", l: lbl || (B ? L2("Paid from", "دُفع من") : L2("Received into", "استُلم في")), type: "select", opts: moneyAccts(), v: accByRole("bank") }];
  const fields = {
    pay: [{ k: "date", l: t("date"), type: "date", v: today(), change: "lnsug", req: 1 },
      { html: `<div class="muted" id="lnsugtxt"></div>`, span: "spanall" },
      { k: "principal", l: L2("Principal", "من أصل القرض"), type: "number", v: sg.principal || "", help: `${L2("Outstanding", "المتبقي")}: ${money(L.outstanding)}` },
      { k: "interest", l: L2("Interest", "الفوائد"), type: "number", v: sg.interest || "", help: L.accrued ? `${L2("Accrued", "مستحقة")}: ${money(L.accrued)}` : "" },
      { k: "fees", l: B ? L2("Late fee / charges", "غرامة تأخير / مصاريف") : L2("Late fee charged", "غرامة تأخير"), type: "number" }, ...money_(),
      { k: "memo", l: t("memo"), span: "spanall" }],
    interest: [{ k: "date", l: L2("Interest up to", "الفوائد حتى"), type: "date", v: today(), change: "lnsug", req: 1 },
      { k: "amount", l: t("amount"), type: "number", v: sg.accrue || "", req: 1, help: L2("Suggested: interest earned so far that is not booked yet", "المقترح: الفوائد المستحقة حتى التاريخ ولم تُسجل بعد") },
      { k: "add_to_loan", l: L2("Add it to the loan balance (capitalise) instead of accruing it", "إضافتها إلى رصيد القرض (رسملة) بدلاً من تسجيلها كمستحقة"), type: "checkbox", span: "spanall" },
      { k: "memo", l: t("memo"), span: "spanall" }],
    draw: [{ k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: t("amount"), type: "number", req: 1 },
      { k: "fees", l: L2("Fees deducted", "رسوم مخصومة"), type: "number" }, ...money_(B ? L2("Received into", "استُلم في") : L2("Paid from", "دُفع من")), { k: "memo", l: t("memo"), span: "spanall" }],
    fee: [{ k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: t("amount"), type: "number", req: 1 },
      { k: "fee_account", l: t("account"), type: "select", opts: acc(B ? ["Expense"] : ["Revenue"]), v: B ? accByRole("bank_charges") : accByRole("other_income") },
      { k: "add_to_loan", l: B ? L2("Added to what we owe (not paid now)", "تُضاف إلى المبلغ المستحق علينا (لم تُدفع الآن)") : L2("Added to what they owe (not collected now)", "تُضاف إلى المبلغ المستحق لنا (لم تُحصّل الآن)"), type: "checkbox", span: "spanall" },
      ...money_(), { k: "memo", l: t("memo"), span: "spanall" }],
    writeoff: [{ k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: L2("Principal written off", "الأصل المعدوم"), type: "number", v: L.outstanding },
      { k: "account", l: t("account"), type: "select", opts: acc(B ? ["Revenue"] : ["Expense"]), v: B ? accByRole("other_income") : accByRole("bad_debt") },
      { k: "include_accrued", l: L2("Include the accrued interest", "تشمل الفوائد المستحقة"), type: "checkbox", v: true, span: "spanall" }, { k: "memo", l: t("memo"), span: "spanall" }],
  }[action];
  drawer(`${titles[action]} — ${L.code} · ${L.party}`, form(fields) + `<h4 style="margin:12px 0 0">${L2("Entry", "القيد")}</h4><div id="lpv" class="muted">—</div>`,
    async v => { await api("POST", `/api/loans/${L.id}/${action}`, v); toast(t("posted")); },
    { submitLabel: t("post"), onOpen: f => {
      const txt = s => s.basis === "schedule" ? `${L2("Installment", "القسط")} ${s.installments.join(", ")} — ${money(s.total)}` : `${L2("Interest to date", "الفوائد حتى التاريخ")}: ${money(s.interest)}`;
      if ($("#lnsugtxt", f)) $("#lnsugtxt", f).textContent = txt(sg);
      S.H.lnsug = async el => { try { const s = await api("GET", `/api/loans/${L.id}/suggest?date=${el.value}`);
        if (action === "pay") { $("[name=principal]", f).value = s.principal || ""; $("[name=interest]", f).value = s.interest || ""; $("#lnsugtxt", f).textContent = txt(s); }
        else $("[name=amount]", f).value = s.accrue || ""; f.dispatchEvent(new Event("change")); } catch (e) { fail(e); } };
      wirePreview(f, `/api/loans-preview/${action}`, () => ({ ...readForm(f), id: L.id })); } });
}

// ============================================================ cash custody
const CU_KIND = () => [["temporary", L2("Temporary — for a task, settle by a date", "مؤقتة — لمهمة وتُسوى في موعد")], ["permanent", L2("Permanent float — topped up after each settlement", "مستديمة — تُعاد تغذيتها بعد كل تسوية")]];
const custodyHolders = () => [...new Set([...(S.boot.parties || []).map(p => p.name), ...(S.cache.cuHolders || [])])];

PAGES.custody = async (view, parts, q) => {
  const d = await api("GET", "/api/custody?" + qs(q));
  S.cache.cuHolders = d.holders;
  const add = can("purchasing.custody", "add");
  S.H.cunew = () => custodyForm();
  const T = d.totals;
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="cunew">+ ${L2("Give custody", "صرف عهدة")}</button>` : ""}<span class="spacer"></span>${exportMenu("custody", q)}</div>
    <div class="kpis">${kpiBox(L2("Cash held by employees", "نقدية لدى العاملين"), T.balance, "", `${T.open} ${L2("open", "مفتوحة")}`)}
      ${kpiBox(L2("Overdue settlements", "تسويات متأخرة"), String(T.overdue), T.overdue ? "neg" : "")}
      ${kpiBox(L2("Owed back to employees", "مستحق للعاملين"), T.owed_to_holders, T.owed_to_holders ? "neg" : "", L2("spent more than they were given", "صرفوا أكثر مما استلموا"))}</div>` +
    filterBar(q, ["q", ["select", "state", t("status"), [["open", L2("Open", "مفتوحة")], ["overdue", L2("Overdue", "متأخرة")], ["closed", L2("Closed", "مغلقة")]]],
      ["select", "holder", L2("Held by", "صاحب العهدة"), d.holders.map(h => [h, h])]]) +
    `<div class="card">${d.rows.length ? `<div class="tbl stack"><table><thead><tr><th>${t("code")}</th><th>${L2("Held by", "صاحب العهدة")}</th><th>${L2("Type", "النوع")}</th><th>${L2("Given on", "تاريخ الصرف")}</th><th class="num">${L2("Given", "المصروف له")}</th><th class="num">${L2("Spent", "المصروفات")}</th><th class="num">${L2("Balance", "الرصيد")}</th><th>${t("status")}</th></tr></thead><tbody>
      ${d.rows.map(r => `<tr class="click ${r.status === "closed" ? "void" : ""}" data-href="#/cust/${r.id}"><td><b>${esc(r.code)}</b></td><td>${esc(r.holder)}${r.purpose ? `<div class="lines">${esc(r.purpose)}</div>` : ""}</td>
        <td>${r.kind === "permanent" ? L2("Permanent", "مستديمة") : L2("Temporary", "مؤقتة")}</td><td>${fd(r.issued_on)}${r.due_date ? `<div class="lines">${L2("settle by", "تُسوى قبل")} ${fd(r.due_date)}</div>` : ""}</td>
        <td class="num">${money(r.issued)}</td><td class="num">${money(r.spent)}</td><td class="num"><b class="${r.balance < 0 ? "neg" : ""}">${money(r.balance)}</b></td>
        <td>${r.status === "closed" ? `<span class="chip">${L2("Closed", "مغلقة")}</span>` : r.overdue ? `<span class="chip bad">${L2("Overdue", "متأخرة")}</span>` : r.balance < 0 ? `<span class="chip warn">${L2("Owed to holder", "مستحق له")}</span>` : !r.balance && r.issued ? `<span class="chip ok">${L2("Settled — close it", "مسواة — أغلقها")}</span>` : `<span class="chip ok">${L2("Open", "مفتوحة")}</span>`}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("No custody yet. Give an employee cash to buy things for the company, then record their expense report (receipts) to settle it — the rest is returned, reimbursed or deducted.", "لا توجد عهد بعد. اصرف لموظف نقدية للشراء للشركة، ثم سجّل تقرير المصروفات (الفواتير) لتسويتها — والباقي يُرد أو يُعوّض أو يُخصم.")}</div>`}</div>
    ${d.by_holder.length > 1 ? `<div class="card"><h2>${L2("By employee", "حسب الموظف")}</h2><div class="tbl"><table><tbody>${d.by_holder.map(h => `<tr><td>${esc(h.holder)}</td><td class="num">${money(h.balance)}</td></tr>`).join("")}</tbody></table></div></div>` : ""}`;
};

PAGES.cust = async (view, parts) => {
  const d = await api("GET", "/api/custody/" + encodeURIComponent(parts[1]));
  const C = d.custody, add = can("purchasing.custody", "add"), ed = can("purchasing.custody", "edit"), open = C.status === "open";
  $("#ttl").textContent = `${C.code} — ${C.holder}`;
  Object.assign(S.H, { cuset: () => custodyAction(C, "settle"), cuiss: () => custodyAction(C, "issue"), curet: () => custodyAction(C, "return"), cuded: () => custodyAction(C, "deduct"),
    cuedit: () => custodyForm(C), cuclose: async () => { try { await api("POST", `/api/custody/${C.id}/close`, {}); toast(t("saved")); route(); } catch (e) { fail(e); } },
    cureopen: async () => { try { await api("POST", `/api/custody/${C.id}/reopen`, {}); route(); } catch (e) { fail(e); } },
    cudel: () => confirmDelete(C.code, "/api/custody/" + C.id, async () => { location.hash = "#/custody"; }) });
  const bal = C.balance;
  view.innerHTML = `<div class="toolbar"><a class="btn" href="#/custody">← ${t("m_custody")}</a>
      ${open && add ? `<button class="btn pri" data-a="cuset">${L2("Expense report (settle)", "تسوية (تقرير مصروفات)")}</button>
        <button class="btn" data-a="cuiss">${bal < 0 ? L2("Reimburse", "تعويض") : L2("Top up", "زيادة العهدة")}</button>
        ${bal > 0 ? `<button class="btn" data-a="curet">${L2("Money returned", "رد نقدية")}</button><button class="btn" data-a="cuded">${L2("Deduct / write off", "خصم / إعدام")}</button>` : ""}` : ""}
      <span class="spacer"></span>
      ${ed ? `<button class="btn" data-a="cuedit">${t("edit")}</button>${open ? `<button class="btn" data-a="cuclose">${L2("Close custody", "إغلاق العهدة")}</button>` : `<button class="btn" data-a="cureopen">${L2("Reopen", "إعادة فتح")}</button>`}
        ${d.rows.filter(r => r.id).length ? "" : `<button class="btn danger" data-a="cudel">${t("delete")}</button>`}` : ""}
      ${exportMenu("custody_statement", { code: C.code })}</div>
    ${C.overdue ? `<div class="banner err">${L2("Settlement is overdue — it was due", "التسوية متأخرة — كان موعدها")} ${fd(C.due_date)}.</div>` : ""}
    ${bal < 0 && open ? `<div class="banner info">${L2("The company owes", "الشركة مدينة لـ")} ${esc(C.holder)} ${money(-bal)} — ${L2("they spent more than they were given. Use Reimburse.", "صرف أكثر مما استلم. استخدم «تعويض».")}</div>` : ""}
    ${!bal && open && C.issued ? `<div class="banner info">${L2("Balance is zero — you can close this custody.", "الرصيد صفر — يمكنك إغلاق العهدة.")}</div>` : ""}
    <div class="kpis">${kpiBox(L2("Balance with", "الرصيد لدى") + " " + C.holder, bal, bal < 0 ? "neg" : "", C.float_amount ? `${L2("limit", "الحد")} ${money(C.float_amount)}${C.to_replenish ? ` · ${L2("to top up", "للتغذية")} ${money(C.to_replenish)}` : ""}` : "")}
      ${kpiBox(L2("Given", "المصروف له"), C.issued)}${kpiBox(L2("Spent (expense reports)", "المصروفات (تسويات)"), C.spent)}${kpiBox(L2("Returned / deducted", "المرتجع / المخصوم"), C.returned + C.deducted)}
      ${kpiBox(L2("Days open", "أيام مفتوحة"), String(C.days_open), C.overdue ? "neg" : "", C.due_date ? `${L2("settle by", "تُسوى قبل")} ${fd(C.due_date)}` : "")}</div>
    <div class="card"><h2>${L2("Statement", "كشف الحساب")}</h2><div class="tbl stack"><table><thead><tr><th>${t("date")}</th><th>${L2("Type", "النوع")}</th><th>${t("description")}</th><th class="num">${L2("Given", "صرف")}</th><th class="num">${L2("Spent / returned", "مصروف / مرتجع")}</th><th class="num">${L2("Balance", "الرصيد")}</th></tr></thead><tbody>
      ${d.rows.map(r => `<tr class="${r.id ? "click" : ""}" ${r.id ? `data-a="openv" data-id="${r.id}"` : ""}><td>${fd(r.date)}</td><td><span class="chip">${esc(tl(r.kind))}</span></td><td>${esc(r.memo)}${r.lines && r.lines.length ? `<div class="lines">${r.lines.map(x => `${esc(x.description)}${x.supplier ? " · " + esc(x.supplier) : ""}${x.receipt ? " · #" + esc(x.receipt) : ""} — ${money(x.amount)}`).join("<br>")}</div>` : ""}</td>
        <td class="num">${r.in ? money(r.in) : ""}</td><td class="num">${r.out ? money(r.out) : ""}</td><td class="num"><b>${money(r.balance)}</b></td></tr>`).join("") || `<tr><td colspan="6" class="muted">${t("nothing")}</td></tr>`}</tbody></table></div></div>
    <div class="card"><h2>${L2("Details", "البيانات")}</h2><div class="tbl"><table><tbody>
      <tr><td class="muted">${L2("Type", "النوع")}</td><td>${C.kind === "permanent" ? L2("Permanent float", "مستديمة") : L2("Temporary", "مؤقتة")}</td></tr>
      ${C.purpose ? `<tr><td class="muted">${L2("Purpose", "الغرض")}</td><td>${esc(C.purpose)}</td></tr>` : ""}
      <tr><td class="muted">${t("account")}</td><td>${esc(C.account)} ${esc(aname(S.boot.accounts.find(x => x.code === C.account) || {}))}</td></tr>
      ${C.bu_code ? `<tr><td class="muted">${t("bu")}</td><td>${esc(C.bu_code)}</td></tr>` : ""}${C.notes ? `<tr><td class="muted">${t("notes")}</td><td>${esc(C.notes)}</td></tr>` : ""}</tbody></table></div></div>`;
};

async function custodyForm(C) {
  const ed = !!C;
  if (!S.cache.cuHolders) { try { S.cache.cuHolders = (await api("GET", "/api/custody")).holders; } catch { S.cache.cuHolders = []; } }
  const custodyAccts = accts(["Asset"]).filter(([c]) => { const a = S.boot.accounts.find(x => x.code === c) || {}; return ["other_current_asset", "prepaid", "cash", "receivable"].includes(a.subtype); });
  const html = form([
    { k: "holder", l: L2("Employee holding it", "الموظف صاحب العهدة"), v: C ? C.holder : "", list: "cuholders", req: 1 },
    { k: "kind", l: L2("Type", "النوع"), type: "select", opts: CU_KIND(), v: C ? C.kind : "temporary", change: "cusync" },
    { k: "purpose", l: L2("Purpose", "الغرض"), v: C ? C.purpose : "", ph: L2("e.g. site purchases for Villa", "مثال: مشتريات موقع الفيلا"), span: "spanall" },
    ...(ed ? [] : [{ k: "booking", l: L2("The money", "المبلغ"), type: "select", change: "cusync", span: "spanall", opts: [["money", L2("Handed over now — from cash / bank", "يُصرف الآن — من الخزينة / البنك")],
      ["opening", L2("Already with the employee (in the books) — track it from here", "لدى الموظف بالفعل (مسجل بالدفاتر) — متابعته من الآن")], ["none", L2("Give it later", "يُصرف لاحقاً")]] },
      { k: "amount", l: t("amount"), type: "number" }, { k: "paid_from", l: L2("Paid from", "دُفع من"), type: "select", opts: moneyAccts(), v: accByRole("cash") }]),
    { k: "float_amount", l: L2("Limit / standing float", "الحد / قيمة العهدة المستديمة"), type: "number", v: C ? (C.float_amount || "") : "", help: L2("Optional. For a permanent float this is the amount it is topped back up to.", "اختياري. للعهدة المستديمة هو المبلغ الذي تُعاد تغذيتها إليه.") },
    { k: "issued_on", l: L2("Given on", "تاريخ الصرف"), type: "date", v: C ? C.issued_on : today(), req: 1 },
    { k: "due_date", l: L2("Settle by", "تُسوى قبل"), type: "date", v: C ? C.due_date : "" },
    { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—"), v: C ? C.bu_id || "" : "", help: L2("Default for its expense reports", "الافتراضي لتقارير المصروفات") },
    { k: "account", l: t("account"), type: "select", opts: custodyAccts, v: C ? C.account : accByRole("custody") },
    { k: "code", l: t("code"), v: C ? C.code : "", ph: L2("automatic", "تلقائي") },
    { k: "notes", l: t("notes"), type: "textarea", v: C ? C.notes : "", span: "spanall" }]) + datalist("cuholders", custodyHolders());
  drawer(ed ? `${t("edit")} — ${C.code}` : L2("Give cash custody", "صرف عهدة نقدية"), html, async v => {
    if (ed) { await api("PUT", "/api/custody/" + C.id, v); toast(t("saved")); }
    else { const r = await api("POST", "/api/custody", v); toast(t("saved")); closeDrawer(); location.hash = "#/cust/" + r.id; return false; }
  }, { onOpen: f => { S.H.cusync = () => { const g = k => ($(`[name=${k}]`, f) || {}).value; if (ed) return; const bk = g("booking");
      const show = (k, on) => { const el = $(`[name=${k}]`, f); el.closest("label").style.display = on ? "" : "none"; el.dataset.skip = on ? "" : "1"; };
      show("amount", bk !== "none"); show("paid_from", bk === "money"); }; S.H.cusync(); } });
}

async function custodyAction(C, action) {
  if (typeof C !== "object") C = (await api("GET", "/api/custody/" + C)).custody;
  const bal = C.balance;
  const titles = { issue: bal < 0 ? L2("Reimburse", "تعويض") : L2("Top up custody", "زيادة العهدة"), settle: L2("Expense report — settle custody", "تقرير مصروفات — تسوية العهدة"),
    return: L2("Money returned", "رد نقدية من العهدة"), deduct: L2("Deduct / write off the balance", "خصم / إعدام رصيد العهدة") };
  const expAccts = accts(["Expense", "COGS", "Asset"]).filter(([c]) => { const a = S.boot.accounts.find(x => x.code === c) || {}; return a.type !== "Asset" || ["inventory", "prepaid", "fixed_asset", "other_current_asset"].includes(a.subtype); });
  let rows = [{ date: today(), description: "", account: accByRole("other_overhead"), bu_id: C.bu_id || "", amount: "", vat_code: "", supplier: "", receipt: "" }];
  const fields = {
    issue: [{ k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: t("amount"), type: "number", v: bal < 0 ? -bal : (C.to_replenish || ""), req: 1 },
      { k: "paid_from", l: L2("Paid from", "دُفع من"), type: "select", opts: moneyAccts(), v: accByRole("cash") }, { k: "memo", l: t("memo"), span: "spanall" }],
    return: [{ k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: t("amount"), type: "number", v: Math.max(bal, 0) || "", req: 1 },
      { k: "received_into", l: L2("Received into", "استُلم في"), type: "select", opts: moneyAccts(), v: accByRole("cash") }, { k: "memo", l: t("memo"), span: "spanall" }],
    deduct: [{ k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: t("amount"), type: "number", v: Math.max(bal, 0) || "", req: 1 },
      { k: "account", l: L2("Deduct to", "يُخصم على"), type: "select", opts: [[accByRole("accrued_payroll"), L2("From the employee's salary (accrued payroll)", "من راتب الموظف (أجور مستحقة)")], ...accts(["Expense"])], v: accByRole("accrued_payroll"),
        help: L2("Salary: reduces what the company owes them. An expense account: the company absorbs it.", "الراتب: يقلل المستحق له. حساب مصروف: تتحملها الشركة.") }, { k: "memo", l: t("memo"), span: "spanall" }],
    settle: [{ k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "report_no", l: L2("Report no.", "رقم التقرير") }, { k: "memo", l: t("memo"), span: "spanall" },
      { html: `<div id="culines"></div>`, span: "spanall" },
      ...(C.kind === "permanent" ? [{ k: "replenish", l: L2("Top the float back up by the same amount", "إعادة تغذية العهدة بنفس المبلغ"), type: "checkbox", v: true, span: "spanall" },
        { k: "paid_from", l: L2("Top up from", "التغذية من"), type: "select", opts: moneyAccts(), v: accByRole("cash") }] : [])],
  }[action];
  const collect = f => ({ ...readForm(f), id: C.id, lines: action === "settle" ? rows : undefined });
  drawer(`${titles[action]} — ${C.code} · ${C.holder}`, form(fields) + `<h4 style="margin:12px 0 0">${L2("Entry", "القيد")}</h4><div id="lpv" class="muted">—</div>`,
    async (v, f) => { await api("POST", `/api/custody/${C.id}/${action}`, collect(f)); toast(t("posted")); },
    { submitLabel: t("post"), wide: action === "settle", onOpen: f => {
      if (action === "settle") {
        const draw = () => { const tot = rows.reduce((s, r) => s + (+r.amount || 0), 0);
          $("#culines", f).innerHTML = `<div class="tbl"><table><thead><tr><th>${t("date")}</th><th style="min-width:160px">${t("description")}</th><th>${t("account")}</th><th>${t("bu")}</th><th class="num">${L2("Amount paid (incl. VAT)", "المبلغ المدفوع (شامل الضريبة)")}</th><th>${t("vat_code")}</th><th>${L2("Supplier", "المورد")}</th><th>${L2("Receipt no.", "رقم الإيصال")}</th><th></th></tr></thead><tbody>
            ${rows.map((r, i) => `<tr><td><input type="date" data-skip="1" value="${esc(r.date)}" data-input="culn" data-i="${i}" data-k="date" style="width:140px"></td>
              <td><input data-skip="1" value="${esc(r.description)}" data-input="culn" data-i="${i}" data-k="description" dir="auto" style="min-width:170px"></td>
              <td><select data-skip="1" data-change="culn" data-i="${i}" data-k="account" style="min-width:200px">${expAccts.map(([c, n]) => `<option value="${esc(c)}" ${c === r.account ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></td>
              <td><select data-skip="1" data-change="culn" data-i="${i}" data-k="bu_id" style="min-width:150px">${buOpts("—").map(([v, n]) => `<option value="${esc(v)}" ${String(v) === String(r.bu_id) ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></td>
              <td><input class="num" type="number" step="any" data-skip="1" value="${esc(r.amount)}" data-input="culn" data-i="${i}" data-k="amount" style="width:110px"></td>
              <td><select data-skip="1" data-change="culn" data-i="${i}" data-k="vat_code" style="min-width:120px">${taxOpts("vat").map(([v, n]) => `<option value="${esc(v)}" ${v === r.vat_code ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></td>
              <td><input data-skip="1" value="${esc(r.supplier)}" data-input="culn" data-i="${i}" data-k="supplier" list="cusup" style="width:120px"></td>
              <td><input data-skip="1" value="${esc(r.receipt)}" data-input="culn" data-i="${i}" data-k="receipt" style="width:90px"></td>
              <td><button type="button" class="btn sm danger" data-a="culdel" data-i="${i}">✕</button></td></tr>`).join("")}</tbody></table></div>
            ${partyList("cusup")}
            <div class="row" style="margin-top:6px"><button type="button" class="btn sm" data-a="culadd">+ ${L2("Expense", "مصروف")}</button><span class="spacer"></span>
              <span id="cutot">${L2("Report total", "إجمالي التقرير")}: <b>${money(tot)}</b> · ${L2("custody balance", "رصيد العهدة")} ${money(bal)} → <b class="${bal - tot < 0 ? "neg" : ""}">${money(bal - tot)}</b></span></div>`; };
        const tot = () => { const s = rows.reduce((a, r) => a + (+r.amount || 0), 0); const el = $("#cutot", f);
          if (el) el.innerHTML = `${L2("Report total", "إجمالي التقرير")}: <b>${money(s)}</b> · ${L2("custody balance", "رصيد العهدة")} ${money(bal)} → <b class="${bal - s < 0 ? "neg" : ""}">${money(bal - s)}</b>`; };
        S.H.culn = el => { rows[+el.dataset.i][el.dataset.k] = el.value; tot(); };
        S.H.culadd = () => { const last = rows[rows.length - 1] || {}; rows.push({ date: last.date || today(), description: "", account: last.account || accByRole("other_overhead"), bu_id: last.bu_id ?? (C.bu_id || ""), amount: "", vat_code: "", supplier: "", receipt: "" }); draw(); };
        S.H.culdel = el => { rows.splice(+el.dataset.i, 1); if (!rows.length) S.H.culadd(); else draw(); f.dispatchEvent(new Event("change")); };
        draw();
      }
      wirePreview(f, `/api/custody-preview/${action}`, () => collect(f)); } });
}
async function pickThen(kind, fn) {
  const isLoan = kind === "loans";
  const d = await api("GET", isLoan ? "/api/loans?state=active" : "/api/custody?state=open");
  if (!d.rows.length) { toast(isLoan ? L2("No active loans yet", "لا توجد قروض نشطة") : L2("No open custody yet", "لا توجد عهد مفتوحة")); return isLoan ? loanForm() : custodyForm(); }
  drawer(isLoan ? L2("Which loan?", "أي قرض؟") : L2("Which custody?", "أي عهدة؟"), form([{ k: "pick", l: isLoan ? t("m_loans") : t("m_custody"), type: "select", span: "spanall",
    opts: d.rows.map(r => [r.id, isLoan ? `${r.code} — ${r.party} · ${money(r.outstanding)}${r.next_due ? ` · ${L2("next", "القادم")} ${fd(r.next_due)}` : ""}` : `${r.code} — ${r.holder} · ${money(r.balance)}`]) }]),
    async v => { const r = d.rows.find(x => String(x.id) === String(v.pick)); await fn(isLoan ? r.id : r); return false; }, { submitLabel: L2("Continue", "متابعة") });
}

// ================================================================ equipment rental
const RENT_CAT = () => [["scaffolding", L2("Scaffolding", "سقالات")], ["elevator", L2("Construction elevator", "مصعد إنشائي")],
  ["cradle_manual", L2("Cradle — manual", "سقالة معلقة يدوية")], ["cradle_motorized", L2("Cradle — motorized", "سقالة معلقة كهربائية")], ["other", L2("Other", "أخرى")]];
const RENT_ST = s => ({ quote: ["", L2("Price offer", "عرض سعر")], ordered: ["warn", L2("PO received", "أمر شراء مستلم")], active: ["ok", L2("On site", "بالموقع")],
  returned: ["", L2("Returned", "تم الإرجاع")], closed: ["", L2("Closed", "مغلق")], cancelled: ["bad", L2("Cancelled", "ملغي")] }[s] || ["", s]);
const CHG_T = () => [["install", L2("Installation", "التركيب")], ["dismantle", L2("Dismantling", "الفك")], ["transport", L2("Transport", "النقل")],
  ["materials", L2("Installation materials", "مواد التركيب")], ["other", L2("Other", "أخرى")]];
const CHG_W = () => [["delivery", L2("On first delivery", "عند أول تسليم")], ["return", L2("On final return", "عند الإرجاع النهائي")], ["manual", L2("When I add it", "يدوياً")]];
const nm = x => (S.lang === "ar" && x.name_ar ? x.name_ar : x.name) || "";
const openDocTab = url => openApi(url, true);
async function rentCatalog() { if (!S.cache.rentCat) S.cache.rentCat = await api("GET", "/api/rent-catalog"); return S.cache.rentCat; }

PAGES.rentals = async (view, parts, q) => {
  const d = await api("GET", "/api/rentals?" + qs(q));
  S.H.rnew = () => rentalForm();
  view.innerHTML = `<div class="toolbar">${can("rentals.contracts", "add") ? `<button class="btn pri" data-a="rnew">+ ${L2("New price offer", "عرض سعر جديد")}</button>` : ""}<span class="spacer"></span>${exportMenu("rentals", q)}</div>
    <div class="kpis">${kpiBox(L2("On site now", "بالموقع حالياً"), String(d.totals.active), "", L2("contracts", "عقود"))}${kpiBox(L2("Rent / month on site", "الإيجار الشهري بالموقع"), d.totals.monthly, "pos")}
      ${kpiBox(L2("Open price offers", "عروض أسعار مفتوحة"), String(d.totals.quotes))}</div>` +
    filterBar(q, ["q", ["select", "status", t("status"), [["open", L2("Open contracts", "العقود المفتوحة")], ...["quote", "ordered", "active", "returned", "closed", "cancelled"].map(s => [s, RENT_ST(s)[1]])]]]) +
    `<div class="card">${d.rows.length ? `<div class="tbl stack"><table><thead><tr><th>${t("code")}</th><th>${t("client")}</th><th>${L2("Site", "الموقع")}</th><th>${t("status")}</th><th>${L2("PO", "أمر الشراء")}</th><th class="num">${L2("Rent / month", "الإيجار الشهري")}</th><th class="num">${L2("Pieces out", "قطع بالموقع")}</th><th>${L2("Invoiced to", "تمت الفوترة حتى")}</th></tr></thead><tbody>
      ${d.rows.map(r => { const [c, l] = RENT_ST(r.status); return `<tr class="click ${["closed", "cancelled"].includes(r.status) ? "void" : ""}" data-href="#/rental/${r.id}"><td><b>${esc(r.code)}</b></td><td>${esc(r.client)}</td><td>${esc(r.site)}</td><td><span class="chip ${c}">${esc(l)}</span></td>
        <td>${esc(r.po_number || "—")}</td><td class="num">${money(r.status === "quote" ? r.monthly_quoted : r.monthly_on_site)}</td><td class="num">${numf(r.pieces_out)}</td><td>${r.billed_to ? fd(r.billed_to) : "—"}</td></tr>`; }).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("No rentals yet. Start with a price offer: scaffolding by piece, frame or square metre, elevators, cradles — plus installation, dismantling and transport. Set up your equipment first under Equipment & stock.", "لا توجد عقود بعد. ابدأ بعرض سعر: سقالات بالقطعة أو الشباك أو المتر المربع، مصاعد، سقالات معلقة — مع التركيب والفك والنقل. جهّز معداتك أولاً من «المعدات والمخزون».")}</div>`}</div>`;
};

PAGES.rental = async (view, parts) => {
  const d = await api("GET", "/api/rentals/" + encodeURIComponent(parts[1]));
  const R = d.rental, add = can("rentals.contracts", "add"), ed = can("rentals.contracts", "edit"), done = ["closed", "cancelled"].includes(R.status);
  $("#ttl").textContent = `${R.code} — ${R.client}`;
  const onSite = d.lines.some(l => l.on_site > 0), delivered = d.handovers.some(h => h.kind === "delivery" && h.status === "confirmed");
  const act = (w, body = {}) => async () => { try { await api("POST", `/api/rentals/${R.id}/${w}`, body); toast(t("saved")); S.cache.rentCat = null; route(); } catch (e) { fail(e); } };
  Object.assign(S.H, {
    redit: () => rentalForm(d), rpo: () => rentPO(R), rsign: () => rentSign(R), rdel: () => handoverForm(d, "delivery"), rret: () => handoverForm(d, "return"),
    rchg: () => rentCharge(d), rinv: () => rentInvoice(d), rclose: act("close"), rreopen: act("reopen"), runsign: act("unsign"),
    rcancel: async () => { if (confirm(L2("Cancel this rental?", "إلغاء هذا العقد؟"))) await act("cancel")(); },
    rdelete: () => confirmDelete(R.code, "/api/rentals/" + R.id, async () => { location.hash = "#/rentals"; }),
    rdoc: el => openDocTab(`/api/rentals/${R.id}/doc?kind=${el.dataset.k}&lang=${el.dataset.l}`),
    ralloc: el => rentAllocation(R, +el.dataset.id),
    rhopen: el => { const h = d.handovers.find(x => x.id === +el.dataset.id); handoverForm(d, h.kind, h); },
    rhprint: el => openDocTab(`/api/rental-handovers/${el.dataset.id}/doc?lang=${el.dataset.l || R.lang}`),
    rchdel: async el => { if (!confirm(t("confirm_delete"))) return; try { await api("DELETE", "/api/rental-charges/" + el.dataset.id); route(); } catch (e) { fail(e); } },
    rbill: async el => { try { await api("POST", `/api/rentals/${R.id}/charge`, { line_id: +el.dataset.id }); toast(t("saved")); route(); } catch (e) { fail(e); } },
    rdep: el => rentDeposit(R, el.dataset.k) });
  const docs = k => `<div class="dd"><button class="btn">${k === "offer" ? L2("Print offer", "طباعة العرض") : L2("Print contract", "طباعة العقد")} ▾</button><div class="dd-menu">
    <button data-a="rdoc" data-k="${k}" data-l="ar">العربية</button><button data-a="rdoc" data-k="${k}" data-l="en">English</button></div></div>`;
  const steps = ["quote", "ordered", "active", "returned", "closed"], cur = steps.indexOf(R.status);
  const q = d.quote;
  view.innerHTML = `<div class="toolbar"><a class="btn" href="#/rentals">← ${t("m_rentals")}</a>
      ${!done && ed ? `<button class="btn" data-a="redit">${R.status === "quote" ? L2("Edit offer", "تعديل العرض") : L2("Edit contract lines", "تعديل بنود العقد")}</button>` : ""}${docs("offer")}
      ${!done && add && !R.po_number ? `<button class="btn pri" data-a="rpo">${L2("Record client PO", "تسجيل أمر شراء العميل")}</button>` : ""}
      ${R.po_number ? docs("contract") : ""}${!done && add && R.po_number && !R.signed_on ? `<button class="btn pri" data-a="rsign">${L2("Contract signed", "توقيع العقد")}</button>` : ""}
      ${!done && add && R.signed_on ? `<button class="btn ${onSite ? "" : "pri"}" data-a="rdel">+ ${L2("Delivery", "تسليم")}</button>` : ""}
      ${!done && add && onSite ? `<button class="btn" data-a="rret">+ ${L2("Return", "إرجاع")}</button>` : ""}
      ${!done && add && R.po_number ? `<button class="btn" data-a="rchg">+ ${L2("Charge", "مبلغ إضافي")}</button>` : ""}
      ${add && delivered && can("sales.invoices", "add") ? `<button class="btn" data-a="rinv">${L2("Invoice", "فاتورة")}</button>` : ""}
      ${add && R.po_number ? `<div class="dd"><button class="btn">${L2("Deposit", "التأمين")} ▾</button><div class="dd-menu"><button data-a="rdep" data-k="receive">${L2("Receive deposit", "استلام تأمين")}</button>
        ${R.deposit_held > 0 ? `<button data-a="rdep" data-k="apply">${L2("Apply to invoices", "خصم من الفواتير")}</button><button data-a="rdep" data-k="refund">${L2("Refund deposit", "رد التأمين")}</button>` : ""}</div></div>` : ""}
      <span class="spacer"></span>
      ${ed && !done && R.status === "returned" ? `<button class="btn" data-a="rclose">${L2("Close contract", "إغلاق العقد")}</button>` : ""}
      ${ed && !done && !delivered ? `<button class="btn" data-a="rcancel">${L2("Cancel", "إلغاء")}</button>` : ""}
      ${ed && done ? `<button class="btn" data-a="rreopen">${L2("Reopen", "إعادة فتح")}</button>` : ""}
      ${ed && !delivered && !d.invoices.length ? `<button class="btn danger" data-a="rdelete">${t("delete")}</button>` : ""}</div>
    <div class="row" style="gap:6px;margin-bottom:10px;flex-wrap:wrap">${R.status === "cancelled" ? `<span class="chip bad">${RENT_ST("cancelled")[1]}</span>` :
      steps.map((s, i) => `<span class="chip ${i < cur ? "ok" : i === cur ? "warn" : ""}">${i < cur ? "✓ " : ""}${esc(RENT_ST(s)[1])}</span>`).join("<span class='muted'>→</span>")}
      ${R.po_number && !R.signed_on && !done ? `<span class="chip bad">${L2("Contract not signed — deliveries need both signatures", "العقد غير موقّع — التسليم يحتاج توقيع الطرفين")}</span>` : ""}</div>
    <div class="kpis">${kpiBox(L2("Rent / month on site", "الإيجار الشهري بالموقع"), d.on_site_monthly, "pos", R.status === "quote" ? `${L2("offer", "العرض")}: ${money(q.monthly)}` : "")}
      ${kpiBox(L2("One-time charges (offer)", "مبالغ لمرة واحدة"), q.one_time)}${kpiBox(L2("Not invoiced yet", "لم تُفوتر بعد"), d.unbilled_charges, d.unbilled_charges ? "neg" : "", L2("charges", "مبالغ"))}
      ${kpiBox(L2("Pieces on site", "القطع بالموقع"), numf(d.held.reduce((s, h) => s + h.qty, 0)), "", `${L2("value", "القيمة")} ${money(d.held.reduce((s, h) => s + h.value, 0))}`)}
      ${R.deposit || R.deposit_held ? kpiBox(L2("Security deposit held", "التأمين المحتفظ به"), R.deposit_held, R.deposit && R.deposit_held < R.deposit && !done ? "neg" : "", R.deposit ? `${L2("required", "المطلوب")} ${money(R.deposit)}${R.deposit_first ? " · " + L2("before delivery", "قبل التسليم") : ""}` : "") : ""}</div>
    <div class="card"><div class="tbl"><table><tbody>
      <tr><td class="muted">${t("client")}</td><td>${esc(R.client)}${R.contact || R.phone ? ` · ${esc([R.contact, R.phone].filter(Boolean).join(" · "))}` : ""}</td><td class="muted">${L2("Site", "الموقع")}</td><td>${esc([R.site, R.site_location].filter(Boolean).join(" — "))}</td></tr>
      <tr><td class="muted">${L2("Offer date", "تاريخ العرض")}</td><td>${fd(R.quote_date)}${R.valid_until ? ` · ${L2("valid until", "صالح حتى")} ${fd(R.valid_until)}` : ""}</td><td class="muted">${L2("Client PO", "أمر الشراء")}</td><td>${R.po_number ? `${esc(R.po_number)} · ${fd(R.po_date)}` : "—"}</td></tr>
      <tr><td class="muted">${L2("Contract signed", "توقيع العقد")}</td><td>${R.signed_on ? `${fd(R.signed_on)} · ${esc(R.signed_us)} / ${esc(R.signed_client)}${ed && !delivered ? ` <button class="btn sm" data-a="runsign">${t("edit")}</button>` : ""}` : "—"}</td>
        <td class="muted">${t("bu")}</td><td>${R.bu_id ? `<a href="#/bu/${R.bu_id}">${esc(R.bu_code)}</a> <span class="muted">${L2("(profit & loss of this contract)", "(أرباح وخسائر هذا العقد)")}</span>` : "—"}</td></tr>
      ${R.min_days ? `<tr><td class="muted">${L2("Minimum rental period", "الحد الأدنى لمدة الإيجار")}</td><td>${R.min_days} ${L2("days", "يوم")}</td><td></td><td></td></tr>` : ""}</tbody></table></div></div>
    <div class="card"><h2>${L2("What is rented", "المؤجر")}</h2><div class="tbl stack"><table><thead><tr><th>${t("description")}</th><th>${L2("Type", "النوع")}</th><th class="num">${L2("Planned", "المخطط")}</th><th class="num">${L2("Rate", "السعر")}</th><th class="num">${L2("On site", "بالموقع")}</th><th class="num">${L2("Delivered / returned", "مسلّم / مرتجع")}</th><th></th></tr></thead><tbody>
      ${d.lines.map(l => `<tr><td>${esc(l.description)}${l.kind === "kit" || l.kind === "sqm" ? `<div class="lines">${esc(l.components.map(c => `${nm(c)} × ${numf(c.per_unit * l.qty)}`).join(" · "))}</div>` : ""}</td>
        <td>${{ item: L2("Per piece / unit", "بالقطعة / الوحدة"), kit: L2("Per frame / set", "بالشباك / الطقم"), sqm: L2("Per m²", "بالمتر المربع"), charge: esc((CHG_T().find(x => x[0] === l.charge_type) || [])[1] || "") }[l.kind]}</td>
        <td class="num">${numf(l.qty)} ${esc(l.unit)}</td><td class="num">${money(l.rate)}${l.kind === "charge" ? "" : `<div class="lines">/ ${L2("month", "شهر")}</div>`}</td>
        <td class="num">${l.kind === "charge" ? "" : `<b>${numf(l.on_site)}</b>`}</td><td class="num">${l.kind === "charge" ? (CHG_W().find(x => x[0] === l.charge_when) || [])[1] || "" : `${numf(l.delivered)} / ${numf(l.returned)}`}</td>
        <td>${l.kind !== "charge" ? `<button class="btn sm" data-a="ralloc" data-id="${l.id}">${L2("Per-piece rent", "إيجار القطعة")}</button>` : !d.charges.some(c => c.line_id === l.id) && add && R.po_number && !done ? `<button class="btn sm" data-a="rbill" data-id="${l.id}">${L2("Charge now", "احتساب الآن")}</button>` : `<span class="chip ok">${L2("charged", "محتسب")}</span>`}</td></tr>`).join("")}</tbody></table></div></div>
    ${d.held.length ? `<div class="card"><h2>${L2("Pieces on site", "القطع بالموقع")}</h2><div class="tbl"><table><thead><tr><th>${L2("Piece", "القطعة")}</th><th class="num">${t("qty")}</th><th class="num">${L2("Cost (FIFO)", "التكلفة (الوارد أولاً)")}</th></tr></thead><tbody>
      ${d.held.map(h => `<tr><td>${esc(h.code)} — ${esc(nm(h))}</td><td class="num">${numf(h.qty)}</td><td class="num">${money(h.value)}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
    <div class="card"><h2>${L2("Handovers (signed by both parties)", "محاضر التسليم والاستلام (بتوقيع الطرفين)")}</h2>${d.handovers.length ? `<div class="tbl stack"><table><thead><tr><th>${t("date")}</th><th>#</th><th>${L2("Type", "النوع")}</th><th>${t("status")}</th><th>${L2("Signed", "التوقيع")}</th><th></th></tr></thead><tbody>
      ${d.handovers.map(h => `<tr><td>${fd(h.date)}</td><td>${esc(h.number)}</td><td>${h.kind === "delivery" ? L2("Delivery", "تسليم") : L2("Return", "إرجاع")}</td>
        <td><span class="chip ${h.status === "confirmed" ? "ok" : "warn"}">${h.status === "confirmed" ? L2("Confirmed", "معتمد") : L2("Draft", "مسودة")}</span>${h.decision === "unsafe" ? ` <span class="chip bad">${L2("not safe", "غير آمن")}</span>` : ""}</td>
        <td>${h.signed ? `✓ ${esc(h.our_name)} / ${esc(h.client_name)}` : `<span class="muted">—</span>`}</td>
        <td style="white-space:nowrap"><button class="btn sm" data-a="rhopen" data-id="${h.id}">${h.status === "draft" ? t("edit") : L2("View", "عرض")}</button> <button class="btn sm" data-a="rhprint" data-id="${h.id}" data-l="ar">🖶 ع</button> <button class="btn sm" data-a="rhprint" data-id="${h.id}" data-l="en">🖶 EN</button></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${R.signed_on ? L2("No deliveries yet.", "لا يوجد تسليم بعد.") : L2("Deliveries start after the client's PO and the signed contract.", "يبدأ التسليم بعد أمر شراء العميل وتوقيع العقد.")}</div>`}</div>
    ${d.charges.length ? `<div class="card"><h2>${L2("Charges", "المبالغ الإضافية")}</h2><div class="tbl stack"><table><thead><tr><th>${t("date")}</th><th>${t("description")}</th><th class="num">${t("qty")}</th><th class="num">${t("amount")}</th><th class="num">${t("total")}</th><th>${L2("Invoice", "الفاتورة")}</th><th></th></tr></thead><tbody>
      ${d.charges.map(c => `<tr><td>${fd(c.date)}</td><td>${esc(c.description)}</td><td class="num">${numf(c.qty)}</td><td class="num">${money(c.amount)}</td><td class="num">${money(c.total)}</td>
        <td>${c.extract_id ? `<a href="#/invoice/${c.extract_id}">${esc(c.invoice_no)}</a>` : `<span class="chip warn">${L2("not invoiced", "لم تُفوتر")}</span>`}</td><td>${!c.extract_id && ed ? `<button class="btn sm danger" data-a="rchdel" data-id="${c.id}">✕</button>` : ""}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
    ${d.deposits.length ? `<div class="card"><h2>${L2("Security deposit", "التأمين")}</h2><div class="tbl"><table><tbody>
      ${d.deposits.map(x => `<tr class="click" data-a="openv" data-id="${x.id}"><td>${fd(x.date)}</td><td>${esc({ receive: L2("Received", "استلام"), refund: L2("Refunded", "رد"), apply: L2("Applied to invoices", "خصم من الفواتير") }[x.type] || "")}</td><td>${esc(x.memo)}</td><td class="num">${money(x.amount)}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
    ${d.invoices.length ? `<div class="card"><h2>${L2("Invoices", "الفواتير")}</h2><div class="tbl"><table><thead><tr><th>${L2("Invoice", "الفاتورة")}</th><th>${L2("Rent period", "فترة الإيجار")}</th><th>${t("status")}</th><th class="num">${t("amount")}</th></tr></thead><tbody>
      ${d.invoices.map(x => `<tr class="click" data-href="#/invoice/${x.extract_id}"><td><b>${esc(x.number)}</b></td><td>${fd(x.period_from)} → ${fd(x.period_to)}</td><td><span class="chip ${x.status === "posted" ? "ok" : "warn"}">${x.status === "posted" ? t("invoiced") : t("draft")}</span></td><td class="num">${money(x.total)}</td></tr>`).join("")}</tbody></table></div></div>` : ""}`;
};

// price offer / contract lines editor, with the scaffold calculator inline for square-metre lines
async function rentalForm(d, pre) {
  const R = d ? d.rental : {}, cat = await rentCatalog();
  const models = (await api("GET", "/api/rent-models")).models;
  let lines = d ? d.lines.map(l => ({ id: l.id, kind: l.kind, item_id: l.item_id, kit_id: l.kit_id, model_id: l.model_id, description: l.description, unit: l.unit, qty: l.qty, rate: l.rate,
    charge_type: l.charge_type, charge_when: l.charge_when, components: l.components.map(c => ({ item_id: c.item_id, per_unit: c.per_unit })), inputs: l.inputs })) : (pre && pre.lines) || [];
  const itemById = id => cat.items.find(i => i.id === +id) || {};
  const html = form([
    { k: "client", l: t("client"), v: R.client || (pre && pre.client) || "", list: "rcl", req: 1 }, { k: "contact", l: L2("Contact person", "المسؤول"), v: R.contact || "" },
    { k: "phone", l: t("phone"), v: R.phone || "" }, { k: "site", l: L2("Project / site", "المشروع / الموقع"), v: R.site || "" },
    { k: "site_location", l: L2("Location", "العنوان"), v: R.site_location || "" }, { k: "quote_date", l: L2("Offer date", "تاريخ العرض"), type: "date", v: R.quote_date || today() },
    { k: "valid_until", l: L2("Valid until", "صالح حتى"), type: "date", v: R.valid_until || "" }, { k: "duration_months", l: L2("Expected duration (months)", "المدة المتوقعة (شهور)"), type: "number", v: R.duration_months ?? 1 },
    { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat"), v: R.vat_code || "" }, { k: "wht_code", l: t("wht_code"), type: "select", opts: taxOpts("wht"), v: R.wht_code || "" },
    { k: "lang", l: L2("Documents in", "لغة المستندات"), type: "select", opts: [["ar", "العربية"], ["en", "English"]], v: R.lang || S.boot.settings.language || "ar" },
    { k: "min_days", l: L2("Minimum rental period (days)", "الحد الأدنى لمدة الإيجار (أيام)"), type: "number", v: R.min_days || 0, help: L2("0 = none. Pieces returned sooner are charged the missing days.", "0 = بدون. القطع المرتجعة قبل ذلك تُحاسب على الأيام الناقصة.") },
    { k: "deposit", l: L2("Security deposit", "مبلغ التأمين"), type: "number", v: R.deposit || "" },
    { k: "deposit_first", l: L2("Deposit must be received before the first delivery", "يجب استلام التأمين قبل أول تسليم"), type: "checkbox", v: !!R.deposit_first, span: "spanall" },
    { html: `<div id="rlines"></div>`, span: "spanall" },
    { k: "offer_notes", l: L2("Notes on the offer", "ملاحظات العرض"), type: "textarea", v: R.offer_notes || "", span: "spanall" }, { k: "notes", l: L2("Internal notes", "ملاحظات داخلية"), type: "textarea", v: R.notes || "", span: "spanall" }])
    + datalist("rcl", (S.boot.parties || []).map(p => p.name));
  drawer(d ? `${t("edit")} — ${R.code}` : L2("New price offer", "عرض سعر جديد"), html, async (v) => {
    const body = { ...v, lines: lines.map(l => ({ ...l })) };
    if (d) { await api("PUT", "/api/rentals/" + R.id, body); toast(t("saved")); }
    else { const r = await api("POST", "/api/rentals", body); toast(t("saved")); closeDrawer(); location.hash = "#/rental/" + r.id; return false; }
  }, { wide: true, onOpen: f => {
    let calc = null, tm;
    const draw = () => {
      const tot = lines.filter(l => l.kind !== "charge").reduce((s, l) => s + (+l.qty || 0) * (+l.rate || 0), 0), one = lines.filter(l => l.kind === "charge").reduce((s, l) => s + (+l.qty || 0) * (+l.rate || 0), 0);
      $("#rlines", f).innerHTML = `<h4 style="margin:6px 0">${L2("What is rented", "المؤجر")}</h4><div class="tbl"><table><thead><tr><th>${L2("Type", "النوع")}</th><th style="min-width:200px">${t("description")}</th><th>${t("unit")}</th><th class="num">${t("qty")}</th><th class="num">${L2("Rate / month (or amount)", "السعر شهرياً (أو المبلغ)")}</th><th class="num">${t("total")}</th><th></th></tr></thead><tbody>
        ${lines.map((l, i) => `<tr><td>${{ item: L2("Piece / unit", "قطعة / وحدة"), kit: L2("Frame / set", "شباك / طقم"), sqm: L2("m²", "م²"), charge: L2("One-time", "لمرة واحدة") }[l.kind]}
            ${l.kind === "charge" ? `<div><select data-skip="1" data-change="rln" data-i="${i}" data-k="charge_type">${CHG_T().map(([v, n]) => `<option value="${v}" ${v === l.charge_type ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
              <select data-skip="1" data-change="rln" data-i="${i}" data-k="charge_when">${CHG_W().map(([v, n]) => `<option value="${v}" ${v === l.charge_when ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div>` : ""}</td>
          <td><input data-skip="1" dir="auto" value="${esc(l.description || "")}" data-input="rln" data-i="${i}" data-k="description" style="min-width:190px">
            ${l.kind === "kit" || l.kind === "sqm" ? `<div class="lines">${esc(l.components.map(c => `${nm(itemById(c.item_id))} × ${numf(Math.ceil(c.per_unit * (+l.qty || 0) - 1e-4))}`).join(" · "))}</div>` : ""}</td>
          <td><input data-skip="1" value="${esc(l.unit || "")}" data-input="rln" data-i="${i}" data-k="unit" style="width:80px"></td>
          <td><input data-skip="1" class="num" type="number" step="any" value="${l.qty ?? ""}" data-input="rln" data-i="${i}" data-k="qty" style="width:90px"></td>
          <td><input data-skip="1" class="num" type="number" step="any" value="${l.rate ?? ""}" data-input="rln" data-i="${i}" data-k="rate" style="width:110px"></td>
          <td class="num" data-tot="${i}">${money((+l.qty || 0) * (+l.rate || 0))}</td><td><button type="button" class="btn sm danger" data-a="rlndel" data-i="${i}">✕</button></td></tr>`).join("") || `<tr><td colspan="7" class="muted">${L2("Add lines below.", "أضف البنود من الأسفل.")}</td></tr>`}</tbody></table></div>
        <div class="row" style="margin-top:6px;flex-wrap:wrap;gap:6px">
          <select data-skip="1" id="rpick" style="max-width:260px"><option value="">${L2("+ Piece / unit…", "+ قطعة / وحدة…")}</option>${cat.items.map(i => `<option value="${i.id}">${esc(i.code)} — ${esc(nm(i))} (${money(i.monthly_rate)})</option>`).join("")}</select>
          <select data-skip="1" id="rkpick" style="max-width:220px"><option value="">${L2("+ Frame / set…", "+ شباك / طقم…")}</option>${cat.kits.map(k => `<option value="${k.id}">${esc(k.code)} — ${esc(nm(k))} (${money(k.monthly_rate)})</option>`).join("")}</select>
          <button type="button" class="btn sm" data-a="rcalc">+ ${L2("By m² (calculator)", "بالمتر المربع (الحاسبة)")}</button><button type="button" class="btn sm" data-a="rcharge">+ ${L2("One-time charge", "مبلغ لمرة واحدة")}</button>
          <span class="spacer"></span><span>${L2("Monthly rent", "الإيجار الشهري")}: <b id="rmt">${money(tot)}</b> · ${L2("one-time", "لمرة واحدة")}: <b id="rot">${money(one)}</b></span></div>
        <div id="rcalcbox"></div>`;
      drawCalc(); };
    const totals = () => { const tot = lines.filter(l => l.kind !== "charge").reduce((s, l) => s + (+l.qty || 0) * (+l.rate || 0), 0), one = lines.filter(l => l.kind === "charge").reduce((s, l) => s + (+l.qty || 0) * (+l.rate || 0), 0);
      if ($("#rmt", f)) $("#rmt", f).textContent = money(tot); if ($("#rot", f)) $("#rot", f).textContent = money(one);
      lines.forEach((l, i) => { const c = $(`[data-tot="${i}"]`, f); if (c) c.textContent = money((+l.qty || 0) * (+l.rate || 0)); }); };
    const drawCalc = () => { const box = $("#rcalcbox", f); if (!box) return; if (!calc) { box.innerHTML = ""; return; }
      const m = models.find(x => x.id === +calc.model_id) || models[0]; if (!m) { box.innerHTML = `<div class="banner info">${L2("No calculator model yet — open Scaffold calculator (or Equipment & stock → Starter set).", "لا يوجد نموذج حاسبة بعد — افتح حاسبة السقالات (أو المعدات والمخزون ← المجموعة الأساسية).")}</div>`; return; }
      calc.model_id = m.id; const sp = m.spec;
      box.innerHTML = `<div class="sec-ed" style="margin-top:10px"><div class="row" style="gap:8px;flex-wrap:wrap"><b>${L2("Scaffold calculator", "حاسبة السقالات")}</b>
        <select data-skip="1" data-change="rcm">${models.map(x => `<option value="${x.id}" ${x.id === m.id ? "selected" : ""}>${esc(nm(x))}</option>`).join("")}</select>
        ${[...sp.inputs, ...sp.params].map(p => `<label class="f" style="max-width:150px">${esc(S.lang === "ar" && p.label_ar ? p.label_ar : p.label)}${p.unit ? ` (${esc(p.unit)})` : ""}<input data-skip="1" class="num" type="number" step="any" value="${calc.values[p.key] ?? p.value}" data-input="rcv" data-k="${p.key}"></label>`).join("")}</div>
        <div id="rcres" class="muted">…</div><div class="row" style="margin-top:6px"><label class="f" style="max-width:160px">${L2("Rent per m² / month", "إيجار المتر شهرياً")}<input data-skip="1" class="num" type="number" step="any" id="rcrate" value="${calc.rate ?? ""}"></label>
        <button type="button" class="btn pri sm" data-a="rcadd">${L2("Add to the offer", "إضافة للعرض")}</button><button type="button" class="btn sm" data-a="rcx">${t("cancel")}</button></div></div>`;
      evalCalc(); };
    const evalCalc = async () => { const box = $("#rcres", f); if (!box || !calc) return;
      try { const r = await api("POST", "/api/rent-models-preview/eval", { model_id: calc.model_id, values: calc.values }); calc.result = r;
        box.innerHTML = `<div class="tbl" style="margin-top:6px"><table><thead><tr><th>${L2("Piece", "القطعة")}</th><th class="num">${t("qty")}</th><th class="num">${L2("In the yard", "بالمخزن")}</th><th class="num">${L2("Value", "القيمة")}</th></tr></thead><tbody>
          ${r.components.map(c => `<tr><td>${c.missing ? `<span class="neg">${esc(c.item)} — ${L2("not in your equipment", "غير موجود بالمعدات")}</span>` : `${esc(c.item)} — ${esc(nm(c))}`}</td><td class="num"><b>${numf(c.qty)}</b></td><td class="num ${c.short ? "neg" : ""}">${c.missing ? "" : numf(c.in_yard)}${c.short ? ` (${L2("short", "عجز")} ${numf(c.short)})` : ""}</td><td class="num">${c.missing ? "" : money(c.value)}</td></tr>`).join("")}</tbody></table></div>
          <div>${L2("Area", "المساحة")}: <b>${numf(r.area)} m²</b> · ${L2("equipment value", "قيمة المعدات")} ${money(r.value)} (${money(r.value_per_m2)} / m²)${r.piece_rent ? ` · ${L2("by piece rates", "بأسعار القطع")} ${money(r.piece_rent_per_m2)} / m²` : ""}</div>`;
      } catch (e) { box.innerHTML = `<span class="neg">${esc(e.message)}</span>`; } };
    Object.assign(S.H, {
      rln: el => { const l = lines[+el.dataset.i]; l[el.dataset.k] = el.value; totals(); },
      rlndel: el => { lines.splice(+el.dataset.i, 1); draw(); },
      rcharge: () => { lines.push({ kind: "charge", charge_type: "install", charge_when: "delivery", description: L2("Installation", "التركيب"), unit: L2("lump sum", "مقطوعية"), qty: 1, rate: "" }); draw(); },
      rcalc: () => { calc = { model_id: (models[0] || {}).id, values: {}, rate: "" }; drawCalc(); $("#rcalcbox", f).scrollIntoView({ behavior: "smooth", block: "nearest" }); },
      rcm: el => { calc.model_id = +el.value; calc.values = {}; drawCalc(); },
      rcv: el => { calc.values[el.dataset.k] = el.value; clearTimeout(tm); tm = setTimeout(evalCalc, 350); },
      rcx: () => { calc = null; drawCalc(); },
      rcadd: () => { const r = calc && calc.result; if (!r || !r.area) return toast(L2("Enter the size first", "أدخل المقاسات أولاً"));
        const comps = r.components.filter(c => !c.missing && c.qty > 0).map(c => ({ item_id: c.item_id, per_unit: c.qty / r.area }));
        const vals = r.values, m = models.find(x => x.id === +calc.model_id);
        lines.push({ kind: "sqm", model_id: calc.model_id, description: `${nm(m)} — ${Object.values(vals).slice(0, 2).map(numf).join(" × ")} m`, unit: "m²", qty: r.area, rate: $("#rcrate", f).value || "", components: comps, inputs: vals });
        calc = null; draw(); } });
    f.addEventListener("change", e => { if (e.target.id === "rpick" && e.target.value) { const it = itemById(e.target.value); lines.push({ kind: "item", item_id: it.id, description: nm(it), unit: it.unit, qty: 1, rate: it.monthly_rate || "", components: [{ item_id: it.id, per_unit: 1 }] }); draw(); }
      if (e.target.id === "rkpick" && e.target.value) { const k = cat.kits.find(x => x.id === +e.target.value); lines.push({ kind: "kit", kit_id: k.id, description: nm(k), unit: k.unit, qty: 1, rate: k.monthly_rate || "", components: k.lines.map(x => ({ item_id: x.item_id, per_unit: x.qty })) }); draw(); } });
    draw(); } });
}

function rentPO(R) {
  drawer(`${L2("Client purchase order", "أمر شراء العميل")} — ${R.code}`, `<p class="muted">${L2("Nothing is delivered before the client's purchase order is recorded.", "لا يتم أي تسليم قبل تسجيل أمر شراء العميل.")}</p>` + form([
    { k: "po_number", l: L2("PO number", "رقم أمر الشراء"), req: 1 }, { k: "po_date", l: t("date"), type: "date", v: today(), req: 1 },
    { k: "start_date", l: L2("Rental starts", "بداية الإيجار"), type: "date", v: R.start_date || today() }, { k: "po_notes", l: t("notes"), span: "spanall" }]),
    async v => { await api("POST", `/api/rentals/${R.id}/po`, v); toast(t("saved")); });
}
function rentSign(R) {
  drawer(`${L2("Contract signed", "توقيع العقد")} — ${R.code}`, `<p class="muted">${L2("Print the contract, have both parties sign it, then record the signatories here.", "اطبع العقد ووقّعه من الطرفين ثم سجّل أسماء الموقعين هنا.")}</p>` + form([
    { k: "signed_us", l: L2("Signed for us by", "وقّع عن الشركة المؤجرة"), req: 1, v: S.boot.user.full_name || "" }, { k: "signed_client", l: L2("Signed for the client by", "وقّع عن العميل"), req: 1 },
    { k: "signed_on", l: t("date"), type: "date", v: today(), req: 1 }]),
    async v => { await api("POST", `/api/rentals/${R.id}/sign`, v); toast(t("saved")); });
}
function rentDeposit(R, type) {
  const title = { receive: L2("Receive security deposit", "استلام مبلغ التأمين"), refund: L2("Refund security deposit", "رد مبلغ التأمين"), apply: L2("Apply the deposit to invoices", "خصم التأمين من الفواتير") }[type];
  const dflt = type === "receive" ? Math.max((R.deposit || 0) - (R.deposit_held || 0), 0) : R.deposit_held;
  drawer(`${title} — ${R.code}`, `<p class="muted">${type === "apply" ? L2("Reduces what the client owes on their invoices; the rest stays held.", "يقلل المستحق على العميل في الفواتير؛ والباقي يظل محتفظاً به.")
      : type === "receive" ? L2("Held as a liability (2105 Security Deposits from Clients) until it is refunded or applied.", "يُسجل كالتزام (2105 تأمينات العملاء) حتى يُرد أو يُخصم.") : L2("Paid back to the client.", "يُرد للعميل.")}</p>` +
    form([{ k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: t("amount"), type: "number", v: dflt || "", req: 1, help: `${L2("Held now", "المحتفظ به")}: ${money(R.deposit_held)}` },
      ...(type === "apply" ? [] : [{ k: "paid_from", l: type === "receive" ? L2("Received into", "استُلم في") : L2("Paid from", "دُفع من"), type: "select", opts: moneyAccts(), v: accByRole("bank") }]),
      { k: "memo", l: t("memo"), span: "spanall" }]),
    async v => { await api("POST", `/api/rentals/${R.id}/deposit`, { ...v, type }); toast(t("posted")); });
}
function rentCharge(d) {
  const R = d.rental;
  drawer(`${L2("Add a charge", "إضافة مبلغ")} — ${R.code}`, form([
    { k: "kind", l: L2("Type", "النوع"), type: "select", opts: [...CHG_T(), ["damage", L2("Damage", "تلف")], ["loss", L2("Loss", "فقد")]] },
    { k: "description", l: t("description"), req: 1, span: "spanall" }, { k: "qty", l: t("qty"), type: "number", v: 1 }, { k: "amount", l: t("amount"), type: "number", req: 1 },
    { k: "date", l: t("date"), type: "date", v: today() }]),
    async v => { await api("POST", `/api/rentals/${R.id}/charge`, v); toast(t("saved")); });
}
async function rentInvoice(d) {
  const R = d.rental, p = d.next_period || { from: today(), to: today() };
  drawer(`${L2("Invoice rent", "فاتورة إيجار")} — ${R.code}`, `<p class="muted">${L2("Rent is worked out day by day on the quantity on site; one-time and loss charges not invoiced yet are added. The invoice is created as a draft — check it, then Save & post (and send it to the Tax Authority from there).", "يُحسب الإيجار يومياً على الكمية بالموقع وتُضاف المبالغ لمرة واحدة والتعويضات غير المفوترة. تُنشأ الفاتورة كمسودة — راجعها ثم احفظ ورحّل (ومنها تُرسل لمنظومة الضرائب).")}</p>` +
    form([{ k: "from", l: L2("Rent from", "الإيجار من"), type: "date", v: p.from, req: 1 }, { k: "to", l: L2("to", "إلى"), type: "date", v: p.to, req: 1 },
      { k: "date", l: L2("Invoice date", "تاريخ الفاتورة"), type: "date", v: p.to }, { k: "number", l: t("invoice_no"), ph: L2("automatic", "تلقائي") }]) + `<div id="rivp" class="muted" style="margin-top:8px">…</div>`,
    async v => { const r = await api("POST", `/api/rentals/${R.id}/invoice`, v); toast(t("saved")); closeDrawer(); location.hash = "#/invoice/" + r.id; return false; },
    { submitLabel: L2("Create draft invoice", "إنشاء مسودة الفاتورة"), onOpen: f => { let tm;
      const run = async () => { const v = readForm(f); try { const r = await api("GET", `/api/rentals/${R.id}/invoice-preview?` + qs({ from: v.from, to: v.to, lang: R.lang }));
        $("#rivp", f).innerHTML = r.lines.length ? `<div class="tbl"><table><thead><tr><th>${t("description")}</th><th class="num">${t("qty")}</th><th class="num">${L2("Price", "السعر")}</th><th class="num">${t("amount")}</th></tr></thead><tbody>
          ${r.lines.map(l => `<tr><td>${esc(l.description)}</td><td class="num">${numf(l.qty)}</td><td class="num">${money(l.price)}</td><td class="num">${money(l.qty * l.price)}</td></tr>`).join("")}
          <tr class="total"><td colspan="3">${t("total")} (${L2("before tax", "قبل الضريبة")})</td><td class="num">${money(r.total)}</td></tr></tbody></table></div>` : L2("Nothing to invoice for this period.", "لا يوجد ما يُفوتر لهذه الفترة."); }
        catch (e) { $("#rivp", f).innerHTML = `<span class="neg">${esc(e.message)}</span>`; } };
      f.addEventListener("change", () => { clearTimeout(tm); tm = setTimeout(run, 150); }); run(); } });
}
async function rentAllocation(R, lineId) {
  const a = await api("GET", `/api/rentals/${R.id}/allocation?line=${lineId}`);
  drawer(`${L2("Rent per piece", "إيجار القطعة")} — ${a.line.description}`, `<p class="muted">${a.basis === "cost" ? L2("The line's rent is shared over its pieces by their FIFO cost — every piece earns the same % of what it cost.", "يُوزَّع إيجار البند على قطعه حسب تكلفتها بطريقة الوارد أولاً يُصرف أولاً — كل قطعة تحقق نفس النسبة من تكلفتها.")
      : L2("No purchase costs recorded yet, so the rent is shared by the pieces' own rent rates (or equally).", "لا توجد تكاليف شراء بعد، فيُوزَّع الإيجار حسب أسعار إيجار القطع (أو بالتساوي).")}</p>
    <div class="kpis">${kpiBox(L2("Rent / month", "الإيجار الشهري"), a.rent, "pos", `${numf(a.units)} ${esc(a.line.unit)} × ${money(a.line.rate)}${a.on_site ? "" : " — " + L2("planned", "مخطط")}`)}
      ${a.value != null ? kpiBox(L2("Pieces' cost (FIFO)", "تكلفة القطع"), a.value) + kpiBox(L2("Monthly return on cost", "العائد الشهري على التكلفة"), pct(a.yield), "pos") : ""}</div>
    <div class="tbl"><table><thead><tr><th>${L2("Piece", "القطعة")}</th><th class="num">${t("qty")}</th>${a.value != null ? `<th class="num">${L2("Unit cost", "تكلفة القطعة")}</th>` : ""}<th class="num">${L2("Share", "النسبة")}</th><th class="num">${L2("Rent / month", "الإيجار الشهري")}</th><th class="num">${L2("Per piece / month", "للقطعة شهرياً")}</th><th class="num">${L2("Per piece / day", "للقطعة يومياً")}</th><th class="num">${L2("Piece list rate", "سعر القطعة المعتاد")}</th></tr></thead><tbody>
      ${a.rows.map(x => `<tr><td>${esc(x.code)} — ${esc(nm(x))}</td><td class="num">${numf(x.qty)}</td>${a.value != null ? `<td class="num">${money(x.unit_cost)}</td>` : ""}<td class="num">${pct(x.share)}</td><td class="num">${money(x.monthly)}</td><td class="num"><b>${money(x.per_piece_month)}</b></td><td class="num">${money(x.per_piece_day)}</td><td class="num">${x.list_rate ? money(x.list_rate) : "—"}</td></tr>`).join("")}</tbody></table></div>`,
    null, { readOnly: true, wide: true });
}

async function handoverForm(d, kind, h) {
  const R = d.rental, tpl = h ? { ...h } : await api("GET", `/api/rentals/${R.id}/handover-template?kind=${kind}`);
  const locked = h && h.status === "confirmed", del = kind === "delivery";
  const data = JSON.parse(JSON.stringify(tpl.data)), checklist = JSON.parse(JSON.stringify(tpl.checklist || []));
  const lineById = id => d.lines.find(l => l.id === +id) || {};
  const html = form([{ k: "number", l: L2("No.", "رقم المحضر"), v: tpl.number }, { k: "date", l: t("date"), type: "date", v: tpl.date, req: 1 }]) + `<div id="rho"></div>` + form([
    ...(del ? [{ k: "decision", l: L2("Final decision", "القرار الفني"), type: "select", opts: [["", "—"], ["safe", L2("Safe — ready for use", "آمنة وجاهزة للاستخدام")], ["unsafe", L2("Not safe — rejected until fixed", "غير آمنة — مرفوضة لحين التعديل")]], v: tpl.decision }] : []),
    { k: "our_name", l: L2("Signed for us by", "عن الشركة المؤجرة"), v: tpl.our_name }, { k: "client_name", l: L2("Signed for the client by", "عن العميل"), v: tpl.client_name },
    { k: "signed", l: L2("Both parties signed the printed form", "وقّع الطرفان على المحضر المطبوع"), type: "checkbox", v: !!tpl.signed, span: "spanall" },
    { k: "notes", l: t("notes"), type: "textarea", v: tpl.notes || "", span: "spanall" }]);
  const title = `${del ? L2("Delivery", "تسليم") : L2("Return", "إرجاع")} — ${R.code} · ${R.client}`;
  const body = f => ({ ...readForm(f), kind, data, checklist });
  const save = async f => h ? (await api("PUT", "/api/rental-handovers/" + h.id, body(f)), h.id) : (await api("POST", `/api/rentals/${R.id}/handovers`, body(f))).id;
  drawer(title, html, locked ? null : async (v, f) => { const id = await save(f); toast(t("saved")); h = h || { id }; },
    { wide: true, readOnly: locked, submitLabel: L2("Save draft", "حفظ كمسودة"),
      extraButtons: locked ? (can("rentals.contracts", "edit") ? `<button type="button" class="btn danger" data-a="rhundo">${L2("Undo (mistake)", "تراجع (خطأ)")}</button>` : "")
        : `${h ? `<button type="button" class="btn danger" data-a="rhdel">${t("delete")}</button>` : ""}<button type="button" class="btn" data-a="rhprint2">🖶 ${L2("Print", "طباعة")}</button><button type="button" class="btn pri" data-a="rhconf">${L2("Save & confirm", "حفظ واعتماد")}</button>`,
      onOpen: f => {
        const draw = () => {
          const lines = Object.keys(data.units);
          $("#rho", f).innerHTML = `<h4 style="margin:10px 0 4px">${del ? L2("Quantities delivered (rent starts)", "الكميات المسلمة (يبدأ الإيجار)") : L2("Quantities returned (rent stops)", "الكميات المرتجعة (يتوقف الإيجار)")}</h4>
            <div class="tbl"><table><thead><tr><th>${t("description")}</th><th class="num">${L2("Units", "الوحدات")}</th><th class="muted">${del ? L2("planned / delivered", "مخطط / مسلّم") : L2("on site", "بالموقع")}</th></tr></thead><tbody>
            ${lines.map(id => { const l = lineById(id); return `<tr><td>${esc(l.description)}</td><td><input data-skip="1" class="num" type="number" step="any" value="${data.units[id]}" data-input="rhu" data-id="${id}" style="width:100px" ${locked ? "disabled" : ""}> ${esc(l.unit)}</td>
              <td class="muted">${del ? `${numf(l.qty)} / ${numf(l.delivered)}` : numf(l.on_site)}</td></tr>`; }).join("")}</tbody></table></div>
            <h4 style="margin:10px 0 4px">${L2("Pieces", "القطع")}</h4><div class="tbl"><table><thead><tr><th>${L2("Piece", "القطعة")}</th><th class="num">${del ? L2("Qty delivered", "الكمية المسلمة") : L2("Returned good", "مرتجع سليم")}</th>
              ${del ? `<th class="num">${L2("In the yard", "بالمخزن")}</th>` : `<th class="num">${L2("Damaged", "تالف")}</th><th class="num">${L2("Lost", "مفقود")}</th>`}<th class="num">${L2("Loss / damage price", "سعر الفقد / التلف")}</th><th>${L2("Serial no.", "الرقم التسلسلي")}</th></tr></thead><tbody>
            ${data.items.map((x, i) => `<tr><td>${esc(x.code)} — ${esc(nm(x))}<div class="lines">${esc(lineById(x.line_id).description || "")}</div></td>
              <td><input data-skip="1" class="num" type="number" step="any" value="${x.qty}" data-input="rhi" data-i="${i}" data-k="qty" style="width:90px" ${locked ? "disabled" : ""}></td>
              ${del ? `<td class="num ${x.in_yard != null && x.qty > x.in_yard ? "neg" : ""}">${x.in_yard != null ? numf(x.in_yard) : ""}</td>` : ["damaged", "lost"].map(k => `<td><input data-skip="1" class="num" type="number" step="any" value="${x[k] || 0}" data-input="rhi" data-i="${i}" data-k="${k}" style="width:80px" ${locked ? "disabled" : ""}></td>`).join("")}
              <td><input data-skip="1" class="num" type="number" step="any" value="${x.loss_price}" data-input="rhi" data-i="${i}" data-k="loss_price" style="width:100px" ${locked ? "disabled" : ""}></td>
              <td><input data-skip="1" value="${esc(x.serial || "")}" data-input="rhi" data-i="${i}" data-k="serial" style="width:110px" ${locked ? "disabled" : ""}></td></tr>`).join("")}</tbody></table></div>
            ${!del ? `<div id="rhchg" class="muted"></div>` : ""}
            ${del && checklist.length ? `<h4 style="margin:10px 0 4px">${L2("Safety inspection checklist", "قائمة فحص السلامة")}</h4><div class="tbl"><table><thead><tr><th>${L2("Inspection item", "بند الفحص")}</th><th>${L2("Complies?", "مطابق؟")}</th><th>${t("notes")}</th></tr></thead><tbody>
              ${checklist.map((c, i) => `<tr><td dir="auto">${esc(S.lang === "ar" && c.text_ar ? c.text_ar : c.text)}</td><td><select data-skip="1" data-change="rhc" data-i="${i}" data-k="ok" ${locked ? "disabled" : ""}><option value="">—</option><option value="yes" ${c.ok === "yes" ? "selected" : ""}>${L2("Yes", "نعم")}</option><option value="no" ${c.ok === "no" ? "selected" : ""}>${L2("No", "لا")}</option><option value="na" ${c.ok === "na" ? "selected" : ""}>${L2("N/A", "لا ينطبق")}</option></select></td>
                <td><input data-skip="1" value="${esc(c.note || "")}" data-input="rhc" data-i="${i}" data-k="note" ${locked ? "disabled" : ""}></td></tr>`).join("")}</tbody></table></div>
              ${!locked ? `<button type="button" class="btn sm" data-a="rhall">${L2("All checks: yes", "كل البنود: نعم")}</button>` : ""}` : ""}`;
          chg(); };
        const chg = () => { const el = $("#rhchg", f); if (!el) return; const c = data.items.reduce((s, x) => s + ((+x.damaged || 0) + (+x.lost || 0)) * (+x.loss_price || 0), 0);
          el.textContent = c ? `${L2("Charged to the client for lost / damaged pieces", "يُطالب العميل عن القطع المفقودة / التالفة")}: ${money(c)}` : ""; };
        Object.assign(S.H, {
          rhu: el => { const id = el.dataset.id, u = +el.value || 0; data.units[id] = el.value; const l = lineById(id);
            data.items.forEach((x, i) => { if (+x.line_id === +id) { const c = (l.components || []).find(c => c.item_id === x.item_id); if (c) { x.qty = Math.max(Math.ceil(c.per_unit * u - 1e-4) - (+x.damaged || 0) - (+x.lost || 0), 0); const inp = $(`[data-input=rhi][data-i="${i}"][data-k=qty]`, f); if (inp) inp.value = x.qty; } } }); },
          rhi: el => { const i = +el.dataset.i, x = data.items[i], k = el.dataset.k;
            if (k === "damaged" || k === "lost") {   // what comes back damaged or not at all is taken off "returned good"
              const total = (+x.qty || 0) + (+x.damaged || 0) + (+x.lost || 0); x[k] = el.value;
              x.qty = Math.max(total - (+x.damaged || 0) - (+x.lost || 0), 0); const q = $(`[data-input=rhi][data-i="${i}"][data-k=qty]`, f); if (q) q.value = x.qty;
            } else x[k] = el.value; chg(); },
          rhc: el => { checklist[+el.dataset.i][el.dataset.k] = el.value; },
          rhall: () => { checklist.forEach(c => { c.ok = "yes"; }); draw(); },
          rhconf: async () => { if (!confirm(del ? L2("Confirm the delivery? Pieces leave the yard and rent starts.", "اعتماد التسليم؟ تخرج القطع من المخزن ويبدأ الإيجار.") : L2("Confirm the return? Pieces come back; lost / damaged are charged.", "اعتماد الإرجاع؟ تعود القطع ويُطالب بالمفقود / التالف."))) return;
            try { const id = await save(f); h = h || { id }; await api("POST", `/api/rental-handovers/${id}/confirm`, {}); toast(t("saved")); closeDrawer(); S.cache.rentCat = null; route(); }
            catch (e) { $("#derr").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; $("#derr").scrollIntoView({ block: "nearest" }); } },
          rhprint2: async () => { try { const id = await save(f); h = h || { id }; openDocTab(`/api/rental-handovers/${id}/doc?lang=${R.lang}`); } catch (e) { fail(e); } },
          rhdel: async () => { if (!confirm(t("confirm_delete"))) return; try { await api("DELETE", "/api/rental-handovers/" + h.id); closeDrawer(); route(); } catch (e) { fail(e); } },
          rhundo: async () => { if (!confirm(L2("Undo this handover? Its pieces, charges and write-off are reversed.", "التراجع عن هذا المحضر؟ تُعكس حركات القطع والمطالبات والإعدام."))) return;
            try { await api("POST", `/api/rental-handovers/${h.id}/undo`, {}); toast(t("saved")); closeDrawer(); route(); } catch (e) { fail(e); } } });
        draw(); } });
}

// ------------------------------------------------------------ equipment & stock
PAGES.rentfleet = async (view, parts, q) => {
  const d = await api("GET", "/api/rent-items?" + qs(q));
  const add = can("rentals.fleet", "add"), ed = can("rentals.fleet", "edit");
  S.cache.rentCat = null;
  Object.assign(S.H, { rdep2: () => rentDeprForm(), rinew: () => rentItemForm(), rknew: () => rentKitForm(null, d), rkopen: el => rentKitForm(d.kits.find(k => k.id === +el.dataset.id), d),
    riopen: el => rentItemView(+el.dataset.id), rset: () => rentSettingsForm(d.settings), fapol: () => deprPolicyForm(),
    rstart: async () => { try { const r = await api("POST", "/api/rent-starter", {}); toast(`${r.items} ${L2("items", "صنف")} · ${r.kits} ${L2("sets", "طقم")} · ${r.models} ${L2("calculator", "حاسبة")}`); route(); } catch (e) { fail(e); } } });
  const util = d.items.reduce((s, i) => s + i.on_rent * i.avg_cost, 0);
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="rinew">+ ${L2("Equipment item", "صنف معدات")}</button><button class="btn" data-a="rknew">+ ${L2("Frame / set", "شباك / طقم")}</button>` : ""}
      ${add && !d.items.some(i => i.code === "SC-FRM2") ? `<button class="btn" data-a="rstart">${L2("Add scaffolding starter set", "إضافة المجموعة الأساسية للسقالات")}</button>` : ""}
      ${ed ? `<button class="btn" data-a="rset">${L2("Terms & checklists", "الشروط وقوائم الفحص")}</button>` : ""}${add && d.items.length ? `<button class="btn" data-a="rdep2">${L2("Run depreciation", "احتساب الإهلاك")}</button>` : ""}${canPolicy() ? `<button class="btn" data-a="fapol">⚙ ${L2("Depreciation settings", "إعدادات الإهلاك")}</button>` : ""}<span class="spacer"></span>${exportMenu("rent_fleet", {})}</div>
    <div class="kpis">${kpiBox(L2("Equipment cost (FIFO)", "تكلفة المعدات (الوارد أولاً)"), d.totals.value)}${kpiBox(L2("Accumulated depreciation", "مجمع الإهلاك"), d.totals.accum)}${kpiBox(L2("Book value", "القيمة الدفترية"), d.totals.book_value, "pos")}${kpiBox(L2("Out on rent", "مؤجر حالياً"), d.totals.on_rent_value, "pos", d.totals.value ? pct(d.totals.on_rent_value / d.totals.value) + " " + L2("of the fleet", "من المعدات") : "")}</div>
    ${RENT_CAT().map(([c, label]) => { const rows = d.items.filter(i => i.category === c); return rows.length ? `<div class="card"><h2>${esc(label)}</h2><div class="tbl stack"><table><thead><tr><th>${t("code")}</th><th>${L2("Item", "الصنف")}</th><th class="num">${L2("In the yard", "بالمخزن")}</th><th class="num">${L2("On rent", "مؤجر")}</th><th class="num">${L2("Use", "التشغيل")}</th><th class="num">${L2("Avg cost (FIFO)", "متوسط التكلفة")}</th><th class="num">${L2("Book value", "القيمة الدفترية")}</th><th class="num">${L2("Rent / month", "الإيجار الشهري")}</th><th class="num">${L2("Loss price", "سعر الفقد")}</th></tr></thead><tbody>
      ${rows.map(i => `<tr class="click ${i.active ? "" : "void"}" data-a="riopen" data-id="${i.id}"><td><b>${esc(i.code)}</b></td><td>${esc(nm(i))}<div class="lines">${esc(i.unit)}</div></td><td class="num">${numf(i.in_yard)}</td><td class="num">${numf(i.on_rent)}</td><td class="num">${pct(i.utilisation)}</td>
        <td class="num">${money(i.avg_cost || i.std_cost)}</td><td class="num">${money(i.book_value)}${i.accum ? `<div class="lines">${L2("cost", "التكلفة")} ${money(i.value)}</div>` : ""}</td><td class="num">${money(i.monthly_rate)}</td><td class="num">${money(i.loss_price)}</td></tr>`).join("")}</tbody></table></div></div>` : ""; }).join("")}
    ${!d.items.length ? `<div class="card"><div class="empty">${L2("No equipment yet. Add your pieces (frames, braces, clamps, boards…), elevators and cradles — or add the scaffolding starter set taken from the standard handover form, then record your stock with its cost.", "لا توجد معدات بعد. أضف القطع (شبابيك، مقصات، كلبسات، ألواح…) والمصاعد والسقالات المعلقة — أو أضف المجموعة الأساسية المأخوذة من محضر التسليم القياسي، ثم سجّل المخزون بتكلفته.")}</div></div>` : ""}
    <div class="card"><h2>${L2("Frames / sets (rented as one unit)", "الشبابيك / الأطقم (تؤجر كوحدة)")}</h2>${d.kits.length ? `<div class="tbl"><table><thead><tr><th>${t("code")}</th><th>${L2("Name", "الاسم")}</th><th>${L2("Pieces in one", "القطع في الوحدة")}</th><th class="num">${L2("Rent / month", "الإيجار الشهري")}</th></tr></thead><tbody>
      ${d.kits.map(k => `<tr class="click" data-a="rkopen" data-id="${k.id}"><td><b>${esc(k.code)}</b></td><td>${esc(nm(k))}<div class="lines">${L2("per", "لكل")} ${esc(k.unit)}</div></td><td>${esc(k.lines.map(l => `${nm(l)} × ${numf(l.qty)}`).join(" · "))}</td><td class="num">${money(k.monthly_rate)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("A frame (or any set) is a group of pieces rented at one price — its rent is shared over the pieces by their cost.", "الشباك (أو أي طقم) مجموعة قطع تؤجر بسعر واحد — ويُوزع إيجارها على القطع حسب تكلفتها.")}</div>`}</div>`;
};

function rentDeprForm() {
  const prev = (() => { const d = new Date(); d.setDate(0); return d.toISOString().slice(0, 7); })();
  drawer(L2("Depreciation of rental equipment", "إهلاك معدات التأجير"), `<p class="muted">${L2("Straight line per stock lot: (pieces still owned × cost × (1 − residual %)) ÷ useful life, catching up any months not booked yet. One entry: Dr depreciation expense / Cr 1416 accumulated depreciation.", "قسط ثابت لكل دفعة: (القطع المملوكة × التكلفة × (1 − نسبة الخردة)) ÷ العمر الإنتاجي، مع احتساب أي أشهر لم تُسجل. قيد واحد: من ح/ مصروف الإهلاك إلى ح/ 1416 مجمع الإهلاك.")}</p>` +
    form([{ k: "through", l: L2("Depreciate up to (month)", "الإهلاك حتى شهر"), type: "month", v: prev, req: 1 }]) + `<div id="rdpv" class="muted">…</div>`,
    async v => { await api("POST", "/api/rent-depreciation", v); toast(t("posted")); },
    { submitLabel: t("post"), onOpen: f => { const run = async () => { try { const r = await api("POST", "/api/rent-depreciation-preview", readForm(f));
        $("#rdpv", f).innerHTML = r.items.length ? `<div class="tbl"><table><thead><tr><th>${L2("Item", "الصنف")}</th><th class="num">${L2("Lots", "الدفعات")}</th><th class="num">${t("amount")}</th></tr></thead><tbody>
          ${r.items.map(x => `<tr><td>${esc(x.code)} — ${esc(nm(x))}</td><td class="num">${x.lots}</td><td class="num">${money(x.amount)}</td></tr>`).join("")}<tr class="total"><td colspan="2">${t("total")}</td><td class="num">${money(r.total)}</td></tr></tbody></table></div>`
          : L2("Nothing to depreciate up to that month.", "لا يوجد ما يُهلك حتى هذا الشهر."); } catch (e) { $("#rdpv", f).innerHTML = `<span class="neg">${esc(e.message)}</span>`; } };
      f.addEventListener("change", run); run(); } });
}
function rentItemForm(it) {
  it = it || { category: "scaffolding", unit: "piece", active: 1 };
  drawer(it.id ? `${t("edit")} — ${it.code}` : L2("New equipment item", "صنف معدات جديد"), form([
    { k: "name", l: L2("Name", "الاسم"), v: it.name, req: 1 }, { k: "name_ar", l: L2("Arabic name", "الاسم بالعربية"), v: it.name_ar },
    { k: "category", l: L2("Category", "الفئة"), type: "select", opts: RENT_CAT(), v: it.category }, { k: "unit", l: t("unit"), v: it.unit, ph: "piece / unit / m" },
    { k: "monthly_rate", l: L2("Rent per piece / month", "إيجار القطعة شهرياً"), type: "number", v: it.monthly_rate || "" },
    { k: "std_cost", l: L2("Reference cost (until a purchase is recorded)", "التكلفة المرجعية (لحين تسجيل شراء)"), type: "number", v: it.std_cost || "" },
    { k: "loss_price", l: L2("Loss / damage price (blank = latest cost + markup)", "سعر الفقد / التلف (فارغ = آخر تكلفة + النسبة)"), type: "number", v: it.loss_price_set || "" },
    { k: "life_months", l: L2("Useful life (months, 0 = not depreciated)", "العمر الإنتاجي (أشهر، 0 = بدون إهلاك)"), type: "number", v: it.life_months ?? 120 },
    { k: "residual_pct", l: L2("Residual (scrap) value %", "قيمة الخردة %"), type: "number", v: it.residual_pct ?? 10 },
    { k: "code", l: t("code"), v: it.code || "", ph: L2("automatic", "تلقائي") }, { k: "active", l: t("active"), type: "checkbox", v: it.active !== 0 },
    { k: "notes", l: t("notes"), type: "textarea", v: it.notes || "", span: "spanall" }]),
    async v => { if (it.id) await api("PUT", "/api/rent-items/" + it.id, v); else await api("POST", "/api/rent-items", v); toast(t("saved")); });
}
async function rentItemView(id) {
  const d = await api("GET", "/api/rent-items/" + id), it = d.item, ed = can("rentals.fleet", "edit"), add = can("rentals.fleet", "add");
  const K = { out: L2("Out to site", "خروج للموقع"), back: L2("Back to yard", "عودة للمخزن"), lost: L2("Lost", "مفقود"), damaged: L2("Damaged", "تالف"), scrap: L2("Scrapped", "تكهين") };
  drawer(`${it.code} — ${nm(it)}`, `<div class="kpis">${kpiBox(L2("In the yard", "بالمخزن"), numf(it.in_yard))}${kpiBox(L2("On rent", "مؤجر"), numf(it.on_rent))}${kpiBox(L2("Book value", "القيمة الدفترية"), it.book_value, "", `${L2("cost", "التكلفة")} ${money(it.value)} · ${L2("depreciation", "الإهلاك")} ${money(it.accum)}`)}${kpiBox(L2("Loss price", "سعر الفقد"), it.loss_price)}</div>
    <p class="muted">${it.life_months ? `${L2("Depreciated over", "يُهلك على")} ${it.life_months} ${L2("months to", "شهراً حتى")} ${it.residual_pct}%${it.depr_through ? ` · ${L2("booked to", "محتسب حتى")} ${esc(it.depr_through)}` : ""}` : L2("Not depreciated.", "بدون إهلاك.")}</p>
    <h4>${L2("Stock lots — oldest go out first", "دفعات المخزون — الأقدم يخرج أولاً")}</h4><div class="tbl"><table><thead><tr><th>${L2("Lot", "الدفعة")}</th><th>${t("date")}</th><th class="num">${L2("Bought", "المشترى")}</th><th class="num">${L2("Unit cost", "تكلفة القطعة")}</th><th class="num">${L2("In the yard", "بالمخزن")}</th><th class="num">${L2("With clients", "لدى العملاء")}</th><th class="num">${L2("Gone", "مستبعد")}</th><th class="num">${L2("Depreciation", "الإهلاك")}</th><th class="num">${L2("Book value", "القيمة الدفترية")}</th><th></th></tr></thead><tbody>
      ${d.lots.map(l => `<tr><td>${esc(l.code)}${l.supplier ? `<div class="lines">${esc(l.supplier)}</div>` : ""}${l.source === "opening" ? `<div class="lines">${L2("already owned", "مملوك سابقاً")}</div>` : ""}</td><td>${fd(l.date)}</td><td class="num">${numf(l.qty)}</td><td class="num">${money(l.unit_cost)}</td><td class="num">${numf(l.in_yard)}</td><td class="num">${numf(l.with_clients)}</td><td class="num">${numf(l.gone)}</td>
        <td class="num">${money(l.accum)}${l.depr_through ? `<div class="lines">${esc(l.depr_through)}</div>` : ""}</td><td class="num">${money(l.book_value)}</td>
        <td>${ed && !l.m_out && !l.m_scrap && !l.depr_through ? `<button type="button" class="btn sm danger" data-a="rlotdel" data-id="${l.id}">✕</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="10" class="muted">${L2("No stock recorded yet.", "لا يوجد مخزون مسجل بعد.")}</td></tr>`}</tbody></table></div>
    ${d.on_rent.length ? `<h4>${L2("On rent now", "مؤجر حالياً")}</h4><div class="tbl"><table><tbody>${d.on_rent.map(r => `<tr><td><a href="#/rental/${r.id}" data-a="vgo2">${esc(r.code)}</a> ${esc(r.client)} — ${esc(r.site)}</td><td class="num">${numf(r.qty)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    ${d.moves.length ? `<h4>${L2("Movements", "الحركات")}</h4><div class="tbl"><table><tbody>${d.moves.slice(0, 60).map(m => `<tr><td>${fd(m.date)}</td><td>${esc(K[m.kind] || m.kind)}</td><td>${esc(m.rental_code || m.note || "")}</td><td>${esc(m.lot_code)}</td><td class="num">${numf(m.qty)}</td></tr>`).join("")}</tbody></table></div>` : ""}`,
    null, { readOnly: true, wide: true, extraButtons: [add ? `<button type="button" class="btn pri" data-a="rlot">+ ${L2("Stock in", "إدخال مخزون")}</button>` : "", ed && it.in_yard > 0 ? `<button type="button" class="btn" data-a="rscrap">${L2("Scrap", "تكهين")}</button>` : "",
      ed ? `<button type="button" class="btn" data-a="riedit">${t("edit")}</button>` : "", ed && !d.lots.length ? `<button type="button" class="btn danger" data-a="ridel">${t("delete")}</button>` : ""].join("") });
  Object.assign(S.H, { riedit: () => rentItemForm(it), rlot: () => rentLotForm(it), rscrap: () => rentScrapForm(it),
    ridel: () => confirmDelete(it.code, "/api/rent-items/" + it.id), vgo2: el => { const hh = el.getAttribute("href"); closeDrawer(); location.hash = hh; },
    rlotdel: async el => { if (!confirm(t("confirm_delete"))) return; try { await api("DELETE", "/api/rent-lots/" + el.dataset.id); rentItemView(id); } catch (e) { fail(e); } } });
}
function rentLotForm(it) {
  drawer(`${L2("Stock in", "إدخال مخزون")} — ${it.code} ${nm(it)}`, `<p class="muted">${L2("Each purchase is a lot with its own cost; pieces go out oldest lot first (FIFO).", "كل عملية شراء دفعة بتكلفتها؛ تخرج القطع من الأقدم أولاً.")}</p>` + form([
    { k: "source", l: L2("Source", "المصدر"), type: "select", change: "rlsrc", opts: [["purchase", L2("Bought now — book it", "شراء الآن — يُسجل محاسبياً")], ["opening", L2("Already owned (in the books)", "مملوك سابقاً (مسجل بالدفاتر)")]] },
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "qty", l: t("qty"), type: "number", req: 1 }, { k: "unit_cost", l: L2("Cost per piece", "تكلفة القطعة"), type: "number", v: it.last_cost || it.std_cost || "", req: 1 },
    { k: "supplier", l: t("supplier"), list: "parties" }, { k: "paid", l: t("paid_now"), type: "number", ph: L2("blank = paid in full", "فارغ = مدفوع بالكامل") },
    { k: "paid_from", l: L2("Paid from", "دُفع من"), type: "select", opts: moneyAccts(), v: accByRole("bank") }, { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat") },
    { k: "opening_accum", l: L2("Depreciation already taken (total for this lot)", "الإهلاك المحتسب سابقاً (إجمالي الدفعة)"), type: "number" },
    { k: "depr_start", l: L2("Depreciation starts (month)", "بداية الإهلاك (شهر)"), type: "month", help: L2("Blank = the month of the date above", "فارغ = شهر التاريخ أعلاه") },
    { k: "notes", l: t("notes"), span: "spanall" }]) + partyList("parties"),
    async v => { await api("POST", `/api/rent-items/${it.id}/lots`, v); toast(t("saved")); route(); rentItemView(it.id); return false; },
    { onOpen: f => { S.H.rlsrc = () => { const buy = $("[name=source]", f).value === "purchase"; ["supplier", "paid", "paid_from", "vat_code"].forEach(k => { const el = $(`[name=${k}]`, f); el.closest("label").style.display = buy ? "" : "none"; el.dataset.skip = buy ? "" : "1"; });
      ["opening_accum"].forEach(k => { const el = $(`[name=${k}]`, f); el.closest("label").style.display = buy ? "none" : ""; el.dataset.skip = buy ? "1" : ""; }); }; S.H.rlsrc(); } });
}
function rentScrapForm(it) {
  drawer(`${L2("Scrap pieces", "تكهين قطع")} — ${it.code} ${nm(it)}`, `<p class="muted">${L2("Pieces in the yard that are no longer usable. Their FIFO cost is written off.", "قطع بالمخزن لم تعد صالحة. تُستبعد تكلفتها بطريقة الوارد أولاً.")}</p>` + form([
    { k: "qty", l: t("qty"), type: "number", req: 1 }, { k: "date", l: t("date"), type: "date", v: today() }, { k: "reason", l: L2("Reason", "السبب"), span: "spanall" }]),
    async v => { await api("POST", "/api/rent-scrap", { ...v, item_id: it.id }); toast(t("posted")); route(); rentItemView(it.id); return false; });
}
function rentKitForm(k, d) {
  k = k || { unit: "frame", lines: [] };
  let rows = k.lines.map(l => ({ item_id: l.item_id, qty: l.qty }));
  drawer(k.id ? `${t("edit")} — ${k.code}` : L2("New frame / set", "شباك / طقم جديد"), form([
    { k: "name", l: L2("Name", "الاسم"), v: k.name, req: 1 }, { k: "name_ar", l: L2("Arabic name", "الاسم بالعربية"), v: k.name_ar },
    { k: "unit", l: t("unit"), v: k.unit }, { k: "monthly_rate", l: L2("Rent per unit / month", "الإيجار للوحدة شهرياً"), type: "number", v: k.monthly_rate || "", input: "rkcalc" },
    { k: "code", l: t("code"), v: k.code || "", ph: L2("automatic", "تلقائي") }, { html: `<div id="rkl"></div>`, span: "spanall" }]),
    async v => { const body = { ...v, lines: rows }; if (k.id) await api("PUT", "/api/rent-kits/" + k.id, body); else await api("POST", "/api/rent-kits", body); toast(t("saved")); },
    { extraButtons: k.id && can("rentals.fleet", "edit") ? `<button type="button" class="btn danger" data-a="rkdel">${t("delete")}</button>` : "", onOpen: f => {
      const draw = () => { const rate = +$("[name=monthly_rate]", f).value || 0, cost = r => { const i = d.items.find(x => x.id === +r.item_id) || {}; return i.avg_cost || i.last_cost || i.std_cost || 0; };
        const val = rows.reduce((s, r) => s + (+r.qty || 0) * cost(r), 0);
        $("#rkl", f).innerHTML = `<h4 style="margin:8px 0 4px">${L2("Pieces in one unit — and the rent each piece earns", "القطع في الوحدة — وإيجار كل قطعة")}</h4><div class="tbl"><table><thead><tr><th>${L2("Piece", "القطعة")}</th><th class="num">${t("qty")}</th><th class="num">${L2("Cost", "التكلفة")}</th><th class="num">${L2("Per piece / month", "للقطعة شهرياً")}</th><th></th></tr></thead><tbody>
          ${rows.map((r, i) => `<tr><td><select data-skip="1" data-change="rkrow" data-i="${i}" data-k="item_id">${d.items.map(x => `<option value="${x.id}" ${x.id === +r.item_id ? "selected" : ""}>${esc(x.code)} — ${esc(nm(x))}</option>`).join("")}</select></td>
            <td><input data-skip="1" class="num" type="number" step="any" value="${r.qty}" data-input="rkrow" data-i="${i}" data-k="qty" style="width:90px"></td><td class="num">${money(cost(r))}</td>
            <td class="num">${val ? money(rate * cost(r) / val) : "—"}</td><td><button type="button" class="btn sm danger" data-a="rkrdel" data-i="${i}">✕</button></td></tr>`).join("")}</tbody></table></div>
          <div class="row" style="margin-top:6px"><button type="button" class="btn sm" data-a="rkradd">+ ${L2("Piece", "قطعة")}</button><span class="spacer"></span><span class="muted">${val ? `${L2("Pieces' cost", "تكلفة القطع")} ${money(val)} · ${L2("monthly return", "العائد الشهري")} ${pct(rate / val)}` : ""}</span></div>`; };
      Object.assign(S.H, { rkrow: el => { rows[+el.dataset.i][el.dataset.k] = el.value; if (el.dataset.k === "item_id") draw(); }, rkradd: () => { rows.push({ item_id: (d.items[0] || {}).id, qty: 1 }); draw(); },
        rkrdel: el => { rows.splice(+el.dataset.i, 1); draw(); }, rkcalc: () => draw(),
        rkdel: () => confirmDelete(k.code, "/api/rent-kits/" + k.id) });
      f.addEventListener("change", e => { if (e.target.dataset.k === "qty") draw(); }); draw(); } });
}
function rentSettingsForm(st) {
  const cl = st.checklists || {};
  drawer(L2("Rental terms & safety checklists", "شروط التأجير وقوائم فحص السلامة"), form([
    { k: "loss_markup", l: L2("Loss price = latest cost + %", "سعر الفقد = آخر تكلفة + %"), type: "number", v: +(st.loss_markup * 100).toFixed(2) },
    { k: "day_basis", l: L2("A rent month is", "شهر الإيجار"), type: "select", opts: [["0", L2("The calendar month (a full month = 1 month)", "الشهر الميلادي (الشهر الكامل = شهر)")], ["30", L2("30 days", "30 يوماً")]], v: String(st.day_basis || 0) },
    { k: "terms_ar", l: L2("Handover terms (Arabic)", "شروط المحضر (عربي)"), type: "textarea", v: st.terms.ar || "", span: "spanall" }, { k: "terms_en", l: L2("Handover terms (English)", "شروط المحضر (إنجليزي)"), type: "textarea", v: st.terms.en || "", span: "spanall" },
    { k: "ct_ar", l: L2("Contract terms (Arabic)", "شروط العقد (عربي)"), type: "textarea", v: st.contract_terms.ar || "", span: "spanall" }, { k: "ct_en", l: L2("Contract terms (English)", "شروط العقد (إنجليزي)"), type: "textarea", v: st.contract_terms.en || "", span: "spanall" },
    ...RENT_CAT().map(([c, label]) => ({ k: "cl_" + c, l: `${L2("Safety checklist", "قائمة الفحص")} — ${label} (${L2("one per line: English | Arabic", "بند في كل سطر: إنجليزي | عربي")})`, type: "textarea", span: "spanall",
      v: (cl[c] || []).map(r => `${r[0]} | ${r[1] || ""}`).join("\n") }))]),
    async v => { const checklists = {}; RENT_CAT().forEach(([c]) => { checklists[c] = String(v["cl_" + c] || "").split("\n").map(s => s.split("|").map(x => x.trim())).filter(r => r[0]); });
      await api("PUT", "/api/rent-settings", { loss_markup: v.loss_markup, day_basis: v.day_basis, terms: { ar: v.terms_ar, en: v.terms_en }, contract_terms: { ar: v.ct_ar, en: v.ct_en }, checklists }); toast(t("saved")); },
    { wide: true });
}

// ------------------------------------------------------------ scaffold calculator
PAGES.rentcalc = async (view, parts, q) => {
  let { models } = await api("GET", "/api/rent-models");
  const ed = can("rentals.calculator", "edit"), add = can("rentals.calculator", "add");
  if (!models.length) {
    S.H.rstart2 = async () => { try { await api("POST", "/api/rent-starter", {}); route(); } catch (e) { fail(e); } };
    view.innerHTML = `<div class="card"><div class="empty">${L2("No calculator model yet. Add the scaffolding starter set: the pieces from the standard handover form and a frame-scaffold model you can tune.", "لا يوجد نموذج حاسبة بعد. أضف المجموعة الأساسية: القطع من محضر التسليم القياسي ونموذج سقالة شبابيك قابل للتعديل.")}</div>
      ${can("rentals.fleet", "add") ? `<div class="row"><button class="btn pri" data-a="rstart2">${L2("Add scaffolding starter set", "إضافة المجموعة الأساسية للسقالات")}</button></div>` : ""}</div>`; return; }
  let m = models.find(x => String(x.id) === String(q.model)) || models[0], values = {}, res = null, tm;
  const lab = p => esc(S.lang === "ar" && p.label_ar ? p.label_ar : p.label || p.key);
  const draw = () => {
    const sp = m.spec;
    view.innerHTML = `<div class="toolbar"><select data-change="rcmodel">${models.map(x => `<option value="${x.id}" ${x.id === m.id ? "selected" : ""}>${esc(nm(x))}</option>`).join("")}</select>
        ${ed ? `<button class="btn" data-a="rcedit">${L2("Edit formulas", "تعديل المعادلات")}</button><button class="btn" data-a="rcsave">${L2("Save these settings as the model's defaults", "حفظ الإعدادات كافتراضي للنموذج")}</button>` : ""}
        ${add ? `<button class="btn" data-a="rccopy">${L2("Copy model", "نسخ النموذج")}</button>` : ""}<span class="spacer"></span>
        ${can("rentals.contracts", "add") ? `<button class="btn pri" data-a="rcoffer">${L2("Start a price offer from this", "بدء عرض سعر من هذا")}</button>` : ""}</div>
      <div class="grid g2"><div class="card"><h2>${L2("Size", "المقاسات")}</h2><div class="grid g2">${sp.inputs.map(p => `<label class="f">${lab(p)}${p.unit ? ` (${esc(p.unit)})` : ""}<input class="num" type="number" step="any" value="${values[p.key] ?? p.value}" data-input="rcval" data-k="${p.key}" style="font-size:1.2em"></label>`).join("")}</div>
          <h2 style="margin-top:14px">${L2("Settings you can tweak", "إعدادات قابلة للتعديل")}</h2><div class="grid g2">${sp.params.map(p => `<label class="f">${lab(p)}${p.unit ? ` (${esc(p.unit)})` : ""}<input class="num" type="number" step="any" value="${values[p.key] ?? p.value}" data-input="rcval" data-k="${p.key}"></label>`).join("")}</div></div>
        <div class="card"><h2>${L2("Worked out", "المحسوب")}</h2><div id="rcder" class="muted">…</div>
          <h2 style="margin-top:14px">${L2("Pricing", "التسعير")}</h2><label class="f" style="max-width:220px">${L2("Target monthly return on equipment cost %", "العائد الشهري المستهدف على تكلفة المعدات %")}<input class="num" type="number" step="any" id="rcpct" value="${values._pct ?? 3.5}" data-input="rcpctv"></label><div id="rcprice"></div></div></div>
      <div class="card"><h2>${L2("Pieces needed", "القطع المطلوبة")}</h2><div id="rcres">…</div></div>`;
    run(); };
  const run = async () => { try { res = await api("POST", "/api/rent-models-preview/eval", { spec: m.spec, values });
      $("#rcder").innerHTML = `<div class="tbl"><table><tbody>${res.derived.map(x => `<tr><td>${esc(S.lang === "ar" && x.label_ar ? x.label_ar : x.label || x.key)}</td><td class="num"><b>${numf(x.value)}</b></td><td class="muted" style="font-size:.8em">${esc(x.expr)}</td></tr>`).join("")}</tbody></table></div>`;
      $("#rcres").innerHTML = `<div class="tbl stack"><table><thead><tr><th>${L2("Piece", "القطعة")}</th><th class="num">${t("qty")}</th><th class="num">${L2("Per m²", "لكل م²")}</th><th class="num">${L2("In the yard", "بالمخزن")}</th><th class="num">${L2("Unit cost", "تكلفة القطعة")}</th><th class="num">${L2("Value", "القيمة")}</th><th class="muted">${L2("Formula", "المعادلة")}</th></tr></thead><tbody>
        ${res.components.map(c => `<tr><td>${c.missing ? `<span class="neg">${esc(c.item)} — ${L2("not in your equipment list", "غير موجود بقائمة المعدات")}</span>` : `${esc(c.item)} — ${esc(nm(c))}`}</td><td class="num"><b>${numf(c.qty)}</b></td><td class="num">${res.area ? (c.qty / res.area).toFixed(3) : ""}</td>
          <td class="num ${c.short ? "neg" : ""}">${c.missing ? "" : numf(c.in_yard)}${c.short ? `<div class="lines">${L2("short", "عجز")} ${numf(c.short)}</div>` : ""}</td><td class="num">${c.missing ? "" : money(c.unit_cost)}</td><td class="num">${c.missing ? "" : money(c.value)}</td><td class="muted" style="font-size:.8em">${esc(c.expr)}</td></tr>`).join("")}
        <tr class="total"><td>${t("total")}</td><td class="num">${numf(res.pieces)}</td><td></td><td></td><td></td><td class="num">${money(res.value)}</td><td></td></tr></tbody></table></div>`;
      price();
    } catch (e) { $("#rcres").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; } };
  const price = () => { if (!res) return; const p = (+$("#rcpct").value || 0) / 100;
    $("#rcprice").innerHTML = `<div class="tbl" style="margin-top:8px"><table><tbody><tr><td>${L2("Area", "المساحة")}</td><td class="num"><b>${numf(res.area)} m²</b></td></tr>
      <tr><td>${L2("Equipment cost", "تكلفة المعدات")}</td><td class="num">${money(res.value)} <span class="muted">(${money(res.value_per_m2)} / m²)</span></td></tr>
      <tr><td>${L2("Rent per m² / month at that return", "إيجار المتر شهرياً بهذا العائد")}</td><td class="num"><b>${money(res.value_per_m2 * p)}</b></td></tr>
      <tr><td>${L2("Monthly rent for this scaffold", "الإيجار الشهري لهذه السقالة")}</td><td class="num"><b>${money(res.value * p)}</b></td></tr>
      ${res.piece_rent ? `<tr><td>${L2("Same pieces at their per-piece rates", "نفس القطع بأسعار القطعة")}</td><td class="num">${money(res.piece_rent)} <span class="muted">(${money(res.piece_rent_per_m2)} / m²)</span></td></tr>` : ""}</tbody></table></div>`; };
  Object.assign(S.H, {
    rcmodel: el => { m = models.find(x => x.id === +el.value); values = {}; draw(); },
    rcval: el => { values[el.dataset.k] = el.value; clearTimeout(tm); tm = setTimeout(run, 300); },
    rcpctv: el => { values._pct = el.value; price(); },
    rcsave: async () => { const sp = JSON.parse(JSON.stringify(m.spec)); [...sp.inputs, ...sp.params].forEach(p => { if (values[p.key] !== undefined && values[p.key] !== "") p.value = +values[p.key]; });
      try { await api("PUT", "/api/rent-models/" + m.id, { name: m.name, name_ar: m.name_ar, category: m.category, spec: sp }); m.spec = sp; toast(t("saved")); } catch (e) { fail(e); } },
    rccopy: async () => { try { const r = await api("POST", "/api/rent-models", { name: m.name + " (copy)", name_ar: (m.name_ar || "") + " (نسخة)", category: m.category, spec: m.spec }); location.hash = "#/rentcalc?model=" + r.id; } catch (e) { fail(e); } },
    rcedit: () => rentModelEditor(m, async () => { models = (await api("GET", "/api/rent-models")).models; m = models.find(x => x.id === m.id) || models[0]; draw(); }),
    rcoffer: () => { if (!res || !res.area) return; const comps = res.components.filter(c => !c.missing && c.qty > 0).map(c => ({ item_id: c.item_id, per_unit: c.qty / res.area }));
      rentalForm(null, { lines: [{ kind: "sqm", model_id: m.id, description: `${nm(m)} — ${Object.values(res.values).slice(0, 2).map(numf).join(" × ")} m`, unit: "m²", qty: res.area,
        rate: +(res.value_per_m2 * ((+$("#rcpct").value || 0) / 100)).toFixed(2) || "", components: comps, inputs: res.values }] }); } });
  draw();
};

function rentModelEditor(m, after) {
  const sp = JSON.parse(JSON.stringify(m.spec));
  const sec = (key, cols) => `<h4 style="margin:10px 0 4px">${{ inputs: L2("Size inputs", "مدخلات المقاسات"), params: L2("Settings", "الإعدادات"), derived: L2("Worked-out values (formulas)", "القيم المحسوبة (معادلات)"), components: L2("Pieces (formulas)", "القطع (معادلات)") }[key]}</h4>
    <div class="tbl"><table><thead><tr>${cols.map(c => `<th>${esc(c[1])}</th>`).join("")}<th></th></tr></thead><tbody>
    ${sp[key].map((r, i) => `<tr>${cols.map(([k]) => `<td><input data-skip="1" value="${esc(r[k] ?? "")}" data-input="rme" data-s="${key}" data-i="${i}" data-k="${k}" ${k === "expr" ? 'style="min-width:260px;font-family:monospace"' : k === "value" ? 'class="num" style="width:80px"' : 'style="width:130px"'}></td>`).join("")}
      <td><button type="button" class="btn sm danger" data-a="rmedel" data-s="${key}" data-i="${i}">✕</button></td></tr>`).join("")}</tbody></table></div>
    <button type="button" class="btn sm" data-a="rmeadd" data-s="${key}">+ ${L2("Row", "سطر")}</button>`;
  const html = () => `<p class="muted">${L2("Names are what formulas use (letters, digits, _). Formulas can use + − × ÷, brackets, comparisons, 'a if condition else b' and ceil, floor, round, min, max, sqrt. Each piece's formula gives its quantity; 'item' is the equipment code.", "الأسماء هي ما تستخدمه المعادلات (حروف إنجليزية وأرقام و _). يمكن استخدام + − × ÷ والأقواس والمقارنات و 'a if شرط else b' و ceil, floor, round, min, max, sqrt. معادلة كل قطعة تعطي كميتها؛ 'item' هو كود الصنف.")}</p>`
    + form([{ k: "name", l: L2("Model name", "اسم النموذج"), v: m.name, req: 1 }, { k: "name_ar", l: L2("Arabic name", "الاسم بالعربية"), v: m.name_ar }])
    + `<div id="rmeb">${body()}</div>`;
  const body = () => sec("inputs", [["key", L2("Name", "الاسم")], ["label", L2("Label", "العنوان")], ["label_ar", "عربي"], ["unit", t("unit")], ["value", L2("Default", "الافتراضي")]])
    + sec("params", [["key", L2("Name", "الاسم")], ["label", L2("Label", "العنوان")], ["label_ar", "عربي"], ["unit", t("unit")], ["value", L2("Default", "الافتراضي")]])
    + sec("derived", [["key", L2("Name", "الاسم")], ["expr", L2("Formula", "المعادلة")], ["label", L2("Label", "العنوان")], ["label_ar", "عربي"]])
    + sec("components", [["item", L2("Item code", "كود الصنف")], ["expr", L2("Quantity formula", "معادلة الكمية")], ["note", t("notes")]])
    + `<label class="f" style="margin-top:10px">${L2("Area formula (m²)", "معادلة المساحة (م²)")}<input data-skip="1" value="${esc(sp.area)}" data-input="rmearea" style="font-family:monospace"></label>`;
  drawer(`${L2("Calculator formulas", "معادلات الحاسبة")} — ${nm(m)}`, html(), async v => { await api("PUT", "/api/rent-models/" + m.id, { name: v.name, name_ar: v.name_ar, category: m.category, spec: sp }); toast(t("saved")); closeDrawer(true); await after(); return false; },
    { wide: true, extraButtons: models_del(m), onOpen: f => {
      const redraw = () => { $("#rmeb", f).innerHTML = body(); };
      Object.assign(S.H, { rme: el => { const r = sp[el.dataset.s][+el.dataset.i]; r[el.dataset.k] = el.dataset.k === "value" ? +el.value : el.value; },
        rmeadd: el => { const s = el.dataset.s; sp[s].push(s === "components" ? { item: "", expr: "0" } : s === "derived" ? { key: "", expr: "0", label: "" } : { key: "", label: "", value: 0 }); redraw(); },
        rmedel: el => { sp[el.dataset.s].splice(+el.dataset.i, 1); redraw(); }, rmearea: el => { sp.area = el.value; },
        rmdel: async () => { if (!confirm(t("confirm_delete"))) return; try { await api("DELETE", "/api/rent-models/" + m.id); closeDrawer(); location.hash = "#/rentcalc"; route(); } catch (e) { fail(e); } } }); } });
}
const models_del = m => can("rentals.calculator", "edit") ? `<button type="button" class="btn danger" data-a="rmdel">${t("delete")}</button>` : "";

PAGES.assets = async (view, parts, q) => {
  const d = await api("GET", "/api/assets?" + qs(q));
  const add = can("assets.register", "add"), dep = can("assets.depreciation", "add");
  S.H.fanew = () => assetForm();
  S.H.fadep = () => deprForm();
  S.H.faopen = el => assetView(el.dataset.id);
  S.H.fapol = () => deprPolicyForm();
  const k = (l, v) => `<div class="kpi"><div class="l">${esc(l)}</div><div class="v">${money(v)}</div></div>`;
  const stat = r => r.disposed ? `<span class="chip bad">${L2("disposed", "مستبعد")}</span>` : r.fully_depreciated ? `<span class="chip ok">${L2("fully depreciated", "مهلك بالكامل")}</span>` : !r.life_months ? `<span class="chip">${L2("not depreciated", "لا يُهلك")}</span>` : "";
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="fanew">+ ${L2("New asset", "أصل جديد")}</button>` : ""}${dep ? `<button class="btn" data-a="fadep">${L2("Run depreciation", "احتساب الإهلاك")}</button>` : ""}${canPolicy() ? `<button class="btn" data-a="fapol">⚙ ${L2("Depreciation settings", "إعدادات الإهلاك")}</button>` : ""}<span class="spacer"></span>${exportMenu("fixed_assets", q)}</div>` +
    filterBar(q, ["q", ["select", "state", t("status"), [["active", L2("In use", "قيد الاستخدام")], ["disposed", L2("Disposed", "مستبعد")]]]]) +
    `<div class="kpis">${k(L2("Cost", "التكلفة"), d.totals.cost)}${k(L2("Accumulated depreciation", "مجمع الإهلاك"), d.totals.accumulated)}${k(L2("Book value", "القيمة الدفترية"), d.totals.book_value)}</div>` +
    `<div class="card">${d.rows.length ? `<div class="tbl stack"><table><thead><tr><th>${t("code")}</th><th>${L2("Asset", "الأصل")}</th><th>${t("account")}</th><th>${L2("Acquired", "تاريخ الشراء")}</th><th class="num">${L2("Cost", "التكلفة")}</th><th class="num">${L2("Accumulated", "المجمع")}</th><th class="num">${L2("Book value", "القيمة الدفترية")}</th><th class="num">${L2("Monthly", "شهرياً")}</th><th>${L2("Booked to", "محتسب حتى")}</th><th></th></tr></thead><tbody>
      ${d.rows.map(r => `<tr class="click ${r.disposed ? "void" : ""}" data-a="faopen" data-id="${r.id}"><td><b>${esc(r.code)}</b></td><td>${esc(r.name)}${r.location || r.serial ? `<div class="lines">${esc([r.location, r.serial].filter(Boolean).join(" · "))}</div>` : ""}</td>
        <td>${esc(r.account)} ${esc(aname(S.boot.accounts.find(a => a.code === r.account) || {}))}</td><td>${fd(r.acquired)}</td><td class="num">${money(r.cost)}</td><td class="num">${money(r.accumulated)}</td><td class="num"><b>${money(r.book_value)}</b></td>
        <td class="num">${r.life_months ? money(r.monthly) : "—"}</td><td>${esc(r.booked_through || "—")}</td><td>${stat(r)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("No assets yet. Add your vehicles, equipment, furniture, computers, software… and the app works out the depreciation every month.", "لا توجد أصول بعد. أضف السيارات والمعدات والأثاث والأجهزة والبرامج… وسيحسب البرنامج الإهلاك شهرياً.")}</div>`}</div>`;
};

async function assetView(id) {
  const a = await api("GET", "/api/assets/" + id);
  const edit = can("assets.register", "edit");
  const row = (l, v) => `<tr><td class="muted">${esc(l)}</td><td>${v}</td></tr>`;
  const html = `<div class="tbl"><table><tbody>
    ${row(L2("Account", "الحساب"), `${esc(a.account)} ${esc(aname(S.boot.accounts.find(x => x.code === a.account) || {}))}`)}
    ${row(L2("Acquired", "تاريخ الشراء"), fd(a.acquired))}${row(L2("Cost", "التكلفة"), money(a.cost))}${row(L2("Residual value", "القيمة التخريدية"), money(a.salvage))}
    ${row(L2("Useful life", "العمر الإنتاجي"), a.life_months ? `${a.life_months} ${L2("months", "شهر")} (${+(a.life_months / 12).toFixed(2)} ${L2("years", "سنة")}) · ${L2("to", "حتى")} ${esc(a.end_month || "")}` : L2("Not depreciated", "لا يُهلك"))}
    ${row(L2("Method", "الطريقة"), esc(deprMethodLabel(a.method, a.factor)) + (a.own_method ? "" : ` <span class="muted">(${esc(L2("company policy", "سياسة الشركة"))})</span>`))}
    ${row(L2("Next month's depreciation", "إهلاك الشهر القادم"), money(a.monthly))}${row(L2("Accumulated", "المجمع"), money(a.accumulated))}${row(L2("Book value", "القيمة الدفترية"), `<b>${money(a.book_value)}</b>`)}
    ${row(L2("Booked to", "محتسب حتى"), esc(a.booked_through || "—"))}${a.bu_code ? row(t("bu"), esc(a.bu_code)) : ""}${a.location ? row(L2("Location", "الموقع"), esc(a.location)) : ""}${a.serial ? row(L2("Serial / plate", "الرقم / اللوحة"), esc(a.serial)) : ""}${a.notes ? row(t("notes"), esc(a.notes)) : ""}
    ${a.purchase_entry ? row(L2("Purchase", "الشراء"), `<a href="#" data-a="openv" data-id="${a.purchase_entry}">#${a.purchase_entry}</a>`) : ""}
    ${a.disposal_entry ? row(L2("Disposal", "الاستبعاد"), `<a href="#" data-a="openv" data-id="${a.disposal_entry}">#${a.disposal_entry}</a>`) : ""}</tbody></table></div>
    ${a.depreciation.length ? `<h4>${L2("Depreciation booked", "الإهلاك المسجل")}</h4><div class="tbl"><table><thead><tr><th>#</th><th>${L2("Months", "الأشهر")}</th><th class="num">${t("amount")}</th></tr></thead><tbody>
      ${a.depreciation.map(x => `<tr class="click" data-a="openv" data-id="${x.id}"><td>${x.id}</td><td>${esc(x.from)}${x.to !== x.from ? " → " + esc(x.to) : ""}</td><td class="num">${money(x.amount)}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
  const btns = [];
  if (edit) btns.push(`<button type="button" class="btn" data-a="faedit">${t("edit")}</button>`);
  if (edit && !a.disposed) btns.push(`<button type="button" class="btn" data-a="fadisp">${L2("Sell / write off", "بيع / استبعاد")}</button>`);
  if (edit && !a.purchase_entry && !a.depreciation.length && !a.disposal_entry) btns.push(`<button type="button" class="btn danger" data-a="fadel">${t("delete")}</button>`);
  drawer(`${a.code} — ${a.name}`, html + (a.life_months ? `<h4>${L2("Depreciation schedule", "جدول الإهلاك")}</h4><div id="fasch" class="muted">${t("loading")}</div>` : ""), null, { readOnly: true, wide: true, extraButtons: btns.join(""),
    onOpen: async f => { const box = $("#fasch", f); if (!box) return; try { box.innerHTML = schedHtml(await api("GET", `/api/assets/${a.id}/schedule`)); } catch (e) { box.textContent = e.message; } } });
  S.H.faedit = () => assetForm(a);
  S.H.fadisp = () => disposeForm(a);
  S.H.fadel = () => confirmDelete(a.name, "/api/assets/" + a.id);
}

function assetForm(a) {
  const isNew = !a; a = a || {};
  const pays = [["none", L2("Already on the books (opening balance) — just register it", "مسجل بالفعل (رصيد افتتاحي) — تسجيل فقط")], ["cash", L2("Paid in full now", "مدفوع بالكامل الآن")],
    ["payable", L2("Bought on credit / partly paid", "شراء آجل / دفع جزئي")], ["partner", L2("Contributed by a partner (adds to their capital)", "مقدم من شريك (يضاف لرأس ماله)")]];
  const fields = [
    { k: "code", l: t("code"), v: a.code || "", ph: L2("automatic", "تلقائي") }, { k: "name", l: L2("Asset", "الأصل"), v: a.name, req: 1 },
    { k: "account", l: L2("Asset account", "حساب الأصل"), type: "select", opts: assetAccounts(), v: a.account || accByRole("fixed_asset") },
    { k: "acquired", l: L2("Date acquired", "تاريخ الشراء"), type: "date", v: a.acquired || today(), req: 1 },
    { k: "cost", l: L2("Cost (before VAT)", "التكلفة (قبل الضريبة)"), type: "number", v: a.cost, req: 1 },
    { k: "salvage", l: L2("Residual value at the end", "القيمة التخريدية في النهاية"), type: "number", v: a.salvage || "" },
    { k: "life_months", l: L2("Useful life (months)", "العمر الإنتاجي (بالأشهر)"), type: "number", v: a.life_months ?? 60, input: "falife", help: L2("60 = 5 years · 0 = never depreciated (land)", "60 = 5 سنوات · 0 = لا يُهلك (أراضي)") },
    { k: "depr_start", l: L2("Depreciate from (month)", "بداية الإهلاك (شهر)"), type: "month", v: a.depr_start || "" },
    { k: "opening_depr", l: L2("Depreciation already taken before (if any)", "إهلاك سابق قبل التسجيل (إن وجد)"), type: "number", v: a.opening_depr || "" },
    { k: "depr_method", l: L2("Depreciation method", "طريقة الإهلاك"), type: "select", opts: [["", L2("Company policy", "حسب سياسة الشركة")], ["straight_line", L2("Straight line", "القسط الثابت")], ["declining", L2("Declining balance", "القسط المتناقص")]], v: a.own_method || "", change: "fameth" },
    { k: "depr_factor", l: L2("Declining factor", "معامل التناقص"), type: "number", v: a.own_factor || "", ph: "2 = " + L2("double declining", "متناقص مضاعف") },
    { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—"), v: a.bu_id || "" },
    { k: "location", l: L2("Location / holder", "الموقع / العهدة"), v: a.location }, { k: "serial", l: L2("Serial / plate no.", "الرقم التسلسلي / اللوحة"), v: a.serial },
    { k: "notes", l: t("notes"), type: "textarea", v: a.notes, span: "spanall" }];
  if (isNew) fields.push(
    { html: `<h4 style="margin:6px 0 0">${L2("How it was paid", "طريقة السداد")}</h4>`, span: "spanall" },
    { k: "pay", l: L2("Booking", "التسجيل"), type: "select", opts: pays, change: "fapay", span: "spanall" },
    { k: "vendor", l: t("supplier"), list: "faparties" }, { k: "paid", l: t("paid_now"), type: "number" },
    { k: "method", l: t("method"), type: "select", opts: methodOpts() },
    { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat") }, { k: "wht_code", l: t("wht_code"), type: "select", opts: taxOpts("wht") },
    { k: "partner", l: t("partner"), type: "select", opts: activePartners().map(p => [p.id, p.name]) });
  drawer((isNew ? L2("New asset", "أصل جديد") : t("edit")) + (a.code ? " — " + a.code : ""), form(fields) + partyList("faparties") + `<div id="falife" class="muted"></div><h4>${L2("Depreciation schedule", "جدول الإهلاك")}</h4><div id="fasch" class="muted"></div>`,
    async v => { if (!v.depr_start) delete v.depr_start; if (isNew) await api("POST", "/api/assets", v); else await api("PUT", "/api/assets/" + a.id, v); toast(t("saved")); },
    { onOpen: async f => {
      S.H.falife = () => { const m = +$("[name=life_months]", f).value || 0, c = +$("[name=cost]", f).value || 0, s = +$("[name=salvage]", f).value || 0;
        $("#falife", f).textContent = m ? `${+(m / 12).toFixed(2)} ${L2("years", "سنة")} · ${L2("monthly depreciation", "الإهلاك الشهري")} ${money((c - s) / m)}` : L2("Not depreciated", "لا يُهلك"); };
      f.addEventListener("input", S.H.falife); S.H.falife();
      const sch = debounce(async () => { const box = $("#fasch", f), v = readForm(f); if (!v.cost) { box.textContent = L2("Enter the cost to see the schedule.", "أدخل التكلفة لعرض الجدول."); return; }
        try { box.innerHTML = schedHtml(isNew ? await api("POST", "/api/assets-preview/schedule", v) : await api("POST", "/api/assets-preview/schedule", { ...v, id: a.id, depr_start: v.depr_start || a.depr_start })); } catch (e) { box.textContent = e.message; } });
      f.addEventListener("input", sch); f.addEventListener("change", sch); sch();
      S.H.fameth = () => { const m = $("[name=depr_method]", f).value; showFields(f, m === "straight_line" ? [] : ["depr_factor"], ["depr_factor"]); }; S.H.fameth();
      const pol = await deprPolicy(), fill = () => { if (!isNew) return; const r = { ...pol, ...(pol.by_account[$("[name=account]", f).value] || {}) };
        $("[name=life_months]", f).value = r.life_months; $("[name=salvage]", f).placeholder = r.residual_pct ? `${r.residual_pct}% ${L2("of cost", "من التكلفة")}` : "0";
        $("[name=depr_start]", f).placeholder = pol.convention === "next_month" ? L2("month after purchase", "الشهر التالي للشراء") : L2("month of purchase", "شهر الشراء"); S.H.falife(); sch(); };
      $("[name=account]", f).addEventListener("change", fill); fill();
      if (isNew) { S.H.fapay = () => { const p = $("[name=pay]", f).value;
        showFields(f, { none: [], cash: ["method", "vat_code", "wht_code", "vendor"], payable: ["vendor", "paid", "method", "vat_code", "wht_code"], partner: ["partner"] }[p], ["vendor", "paid", "method", "vat_code", "wht_code", "partner"]); };
        S.H.fapay(); }
      const acc = $("[name=account]", f); acc.addEventListener("change", () => { const x = S.boot.accounts.find(y => y.code === acc.value); if (x && x.subtype === "fixed_asset" && /land|أراض/i.test(x.name + x.name_ar)) $("[name=life_months]", f).value = 0; S.H.falife(); });
    } });
}

const canPolicy = () => can("settings", "edit") || can("assets.depreciation", "edit");
const deprPolicy = async () => { try { return await api("GET", "/api/depreciation-policy"); } catch { return { method: "straight_line", factor: 2, convention: "same_month", life_months: 60, residual_pct: 0, by_account: {} }; } };
const deprMethodLabel = (m, f) => m === "declining" ? `${L2("Declining balance", "القسط المتناقص")} ×${+f || 2}` : L2("Straight line", "القسط الثابت");
const schedHtml = d => d.rows.length ? `<div class="muted" style="margin-bottom:4px">${esc(deprMethodLabel(d.method, d.factor))}${d.start ? ` · ${L2("from", "من")} ${esc(d.start)} ${L2("to", "إلى")} ${esc(d.end_month || "")}` : ""}</div><div class="tbl"><table><thead><tr><th>${L2("Year", "السنة")}</th><th class="num">${L2("Depreciation", "الإهلاك")}</th><th class="num">${L2("Accumulated", "المجمع")}</th><th class="num">${L2("Book value", "القيمة الدفترية")}</th><th></th></tr></thead><tbody>
  ${d.rows.map(r => `<tr><td>${esc(r.year)}</td><td class="num">${money(r.depreciation)}</td><td class="num">${money(r.accumulated)}</td><td class="num"><b>${money(r.book_value)}</b></td><td>${r.booked && r.planned ? `<span class="chip">${L2("part booked", "محتسب جزئياً")}</span>` : r.booked ? `<span class="chip ok">${L2("booked", "محتسب")}</span>` : `<span class="chip">${L2("planned", "مخطط")}</span>`}</td></tr>`).join("")}</tbody></table></div>` : `<div class="muted">${L2("Nothing to depreciate.", "لا يوجد إهلاك.")}</div>`;
async function deprPolicyForm() {
  const p = await deprPolicy();
  const rentAcc = accByRole("rental_fleet");
  const deprAccts = accts(["Asset"], ["fixed_asset", "intangible"]);
  const rows = [...deprAccts, ...(rentAcc && !deprAccts.some(([c]) => c === rentAcc) ? [[rentAcc, `${rentAcc} — ${aname(S.boot.accounts.find(a => a.code === rentAcc) || {})}`]] : [])];
  const mopts = sel => [["", "—"], ["straight_line", L2("Straight line", "القسط الثابت")], ["declining", L2("Declining balance", "القسط المتناقص")]].map(([v, l]) => `<option value="${v}" ${v === (sel || "") ? "selected" : ""}>${esc(l)}</option>`).join("");
  drawer(L2("Depreciation settings", "إعدادات الإهلاك"), form([
    { html: `<p class="muted" style="margin:0">${L2("These are the company's rules. New assets take them (you can still change any asset), and every depreciation run follows them. Changing a rule never touches depreciation already booked — the rest of the life is re-spread from the next run.", "هذه قواعد الشركة. الأصول الجديدة تأخذها (ويمكنك تعديل أي أصل)، وكل احتساب للإهلاك يتبعها. تغيير القاعدة لا يمس الإهلاك المسجل — ويُعاد توزيع الباقي من الاحتساب التالي.")}</p>`, span: "spanall" },
    { k: "method", l: L2("Default method", "الطريقة الافتراضية"), type: "select", opts: [["straight_line", L2("Straight line — the same every month", "القسط الثابت — نفس المبلغ كل شهر")], ["declining", L2("Declining balance — more in the early years", "القسط المتناقص — أكثر في السنوات الأولى")]], v: p.method, change: "dpm" },
    { k: "factor", l: L2("Declining factor", "معامل التناقص"), type: "number", v: p.factor, help: L2("2 = double declining (common) · 1.5 = 150% · switches to straight line when that is higher, so the asset still ends at its residual value", "2 = متناقص مضاعف (الأشهر) · 1.5 = 150% · يتحول للقسط الثابت عندما يكون أعلى، فينتهي الأصل عند قيمته التخريدية") },
    { k: "convention", l: L2("First month depreciated", "أول شهر يُهلك"), type: "select", opts: [["same_month", L2("The month it was bought (full month)", "شهر الشراء (شهر كامل)")], ["next_month", L2("The month after it was bought", "الشهر التالي للشراء")]], v: p.convention },
    { k: "life_months", l: L2("Default useful life (months)", "العمر الإنتاجي الافتراضي (شهور)"), type: "number", v: p.life_months, help: L2("60 = 5 years", "60 = 5 سنوات") },
    { k: "residual_pct", l: L2("Default residual value (% of cost)", "القيمة التخريدية الافتراضية (% من التكلفة)"), type: "number", v: p.residual_pct },
    { k: "remind", l: L2("Remind me on the dashboard when last month's depreciation is not booked", "ذكّرني في لوحة التحكم إذا لم يُحتسب إهلاك الشهر الماضي"), type: "checkbox", v: p.remind, span: "spanall" },
    { html: `<h4 class="fsec">${L2("Per asset account (blank = default above)", "حسب حساب الأصل (فارغ = الافتراضي أعلاه)")}</h4>
      <div class="tbl"><table id="dpacc"><thead><tr><th>${t("account")}</th><th>${L2("Life (months)", "العمر (شهور)")}</th><th>${L2("Residual %", "التخريدية %")}</th><th>${L2("Method", "الطريقة")}</th></tr></thead><tbody>
      ${rows.map(([c, l]) => { const r = p.by_account[c] || {}; return `<tr data-code="${esc(c)}"><td>${esc(l)}${c === rentAcc ? `<div class="lines">${L2("rental equipment: life & residual are set per item; the method applies", "معدات التأجير: العمر والتخريدية لكل صنف؛ وتُطبق الطريقة")}</div>` : ""}</td>
        <td><input data-skip="1" class="num" type="number" step="1" data-k="life_months" value="${r.life_months ?? ""}" style="width:90px" ${c === rentAcc ? "disabled" : ""}></td>
        <td><input data-skip="1" class="num" type="number" step="any" data-k="residual_pct" value="${r.residual_pct ?? ""}" style="width:80px" ${c === rentAcc ? "disabled" : ""}></td>
        <td><select data-skip="1" data-k="method">${mopts(r.method)}</select></td></tr>`; }).join("")}</tbody></table></div>`, span: "spanall" }]),
  async (v, f) => { v.by_account = {};
    $$("#dpacc tbody tr", f).forEach(tr => { const r = {}; $$("[data-k]", tr).forEach(el => { if (el.value !== "") r[el.dataset.k] = el.value; }); if (Object.keys(r).length) v.by_account[tr.dataset.code] = r; });
    await api("PUT", "/api/depreciation-policy", v); toast(t("saved")); },
  { wide: true, onOpen: f => { S.H.dpm = () => showFields(f, $("[name=method]", f).value === "declining" ? ["factor"] : [], ["factor"]); S.H.dpm(); } });
}
function deprForm() {
  const month = today().slice(0, 7);
  drawer(L2("Run depreciation", "احتساب الإهلاك"), form([
    { k: "through", l: L2("Depreciate up to (month)", "حتى شهر"), type: "month", v: month, req: 1, change: "dprev",
      help: L2("Books every month not booked yet, one entry per asset, dated the last day of the month.", "يسجل كل شهر لم يُحتسب بعد، قيد لكل أصل بتاريخ آخر يوم في الشهر.") }]) + `<div id="dplan" style="margin-top:12px"></div>`,
  async v => { const r = await api("POST", "/api/assets/depreciation", v); toast(`${r.count} × ${t("posted")} — ${money(r.total)}`); },
  { submitLabel: L2("Book depreciation", "تسجيل الإهلاك"), onOpen: f => {
    S.H.dprev = async () => { const box = $("#dplan", f);
      try { const p = await api("POST", "/api/assets-preview/depreciation", readForm(f));
        box.innerHTML = p.rows.length ? `<div class="tbl"><table><thead><tr><th>${L2("Asset", "الأصل")}</th><th>${L2("Months", "الأشهر")}</th><th class="num">${t("amount")}</th></tr></thead><tbody>
          ${p.rows.map(r => `<tr><td>${esc(r.code)} ${esc(r.name)}</td><td>${esc(r.from)}${r.to !== r.from ? " → " + esc(r.to) : ""} (${r.months})</td><td class="num">${money(r.amount)}</td></tr>`).join("")}
          <tr class="total"><td>${t("total")}</td><td></td><td class="num">${money(p.total)}</td></tr></tbody></table></div>` : `<div class="banner ok">${L2("Everything is depreciated up to this month.", "تم احتساب الإهلاك حتى هذا الشهر.")}</div>`;
      } catch (e) { box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; } };
    $("[name=through]", f).addEventListener("input", S.H.dprev); S.H.dprev(); } });
}

function disposeForm(a) {
  drawer(L2("Sell / write off", "بيع / استبعاد") + " — " + a.code, form([
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 },
    { k: "proceeds", l: L2("Sale price (0 = scrapped / written off)", "سعر البيع (0 = تكهين / استبعاد)"), type: "number", v: 0 },
    { k: "method", l: t("method"), type: "select", opts: methodOpts() },
    { k: "on_credit", l: L2("Not paid yet (the buyer owes it)", "لم يُسدد بعد (مستحق على المشتري)"), type: "checkbox" },
    { k: "buyer", l: L2("Buyer", "المشتري"), list: "faparties" },
    { k: "depreciate_first", l: L2("Book depreciation up to this month first", "احتساب الإهلاك حتى هذا الشهر أولاً"), type: "checkbox", v: true },
    { k: "memo", l: t("memo"), span: "spanall" }]) + partyList("faparties") + `<div id="dsplan" style="margin-top:12px"></div>`,
  async v => { await api("POST", `/api/assets/${a.id}/dispose`, v); toast(t("posted")); },
  { submitLabel: L2("Book it", "تسجيل"), onOpen: f => {
    const prev = debounce(async () => { const box = $("#dsplan", f);
      try { box.innerHTML = planHtml(await api("POST", "/api/assets-preview/dispose", { ...readForm(f), id: a.id })); }
      catch (e) { box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; } });
    f.addEventListener("input", prev); f.addEventListener("change", prev); prev(); } });
}

// ================================================================ product & unit sales
const P_KINDS = () => [["item", L2("Item / goods", "صنف / بضاعة")], ["unit", L2("Unit (one of a kind: apartment, villa, car…)", "وحدة (شقة، فيلا، سيارة…)")], ["service", L2("Service", "خدمة")]];
const P_ST = { available: ["Available", "متاح", "ok"], reserved: ["Reserved", "محجوز", "warn"], sold: ["Sold", "مباع", "bad"], delivered: ["Delivered", "تم التسليم", ""], hidden: ["Hidden", "مخفي", ""] };
const pStatus = s => { const x = P_ST[s] || [s, s, ""]; return `<span class="chip ${x[2]}">${esc(L2(x[0], x[1]))}</span>`; };
const S_ST = { draft: ["Draft", "مسودة", ""], confirmed: ["Confirmed", "مؤكد", "ok"], delivered: ["Delivered", "تم التسليم", "ok"], cancelled: ["Cancelled", "ملغي", "bad"] };
const sStatus = s => { const x = S_ST[s] || [s, s, ""]; return `<span class="chip ${x[2]}">${esc(L2(x[0], x[1]))}</span>`; };
const I_ST = { paid: ["Paid", "مسدد", "ok"], cheque: ["By cheque", "بشيك", "ok"], partial: ["Part paid", "جزئي", "warn"], overdue: ["Overdue", "متأخر", "bad"], due: ["Due", "مستحق", ""], cancelled: ["Cancelled", "ملغي", ""] };
const iStatus = s => { const x = I_ST[s] || [s, s, ""]; return `<span class="chip ${x[2]}">${esc(L2(x[0], x[1]))}</span>`; };
const C_ST = { pending: ["Under collection", "تحت التحصيل", "warn"], cleared: ["Cleared", "تم الصرف", "ok"], bounced: ["Bounced", "مرتد", "bad"] };
const PLAN_T = () => [["full", L2("Paid in full", "كاش / دفعة واحدة")], ["deferred", L2("Deferred (one later payment)", "آجل (دفعة واحدة لاحقاً)")], ["installments", L2("Installments", "تقسيط")], ["custom", L2("Custom schedule", "جدول مخصص")]];
const FREQS = () => [["monthly", L2("Monthly", "شهري")], ["quarterly", L2("Quarterly", "ربع سنوي")], ["semiannual", L2("Every 6 months", "نصف سنوي")], ["annual", L2("Yearly", "سنوي")]];
const instLabel = r => { if (S.lang !== "ar") return r.label || r.kind;
  if (r.kind === "installment" && /\d+\/\d+/.test(r.label || "")) return `قسط ${(r.label.match(/\d+\/\d+/) || [""])[0]}`;
  if (r.kind === "down") return r.label === "Full payment" ? "دفعة كاملة" : "مقدم"; if (r.kind === "balance") return "دفعة";
  return r.label && r.label !== "Extra payment" ? r.label : "دفعة إضافية"; };
const pname = p => (S.lang === "ar" && p.name_ar ? p.name_ar : p.name);
const SPEC_PRESETS = { unit: [["Area", "المساحة"], ["Floor", "الدور"], ["Bedrooms", "غرف النوم"], ["Bathrooms", "الحمامات"], ["View", "الإطلالة"], ["Finishing", "التشطيب"], ["Delivery", "الاستلام"]],
  item: [["Brand", "الماركة"], ["Model", "الموديل"], ["Size", "المقاس"], ["Colour", "اللون"], ["Material", "الخامة"], ["Warranty", "الضمان"]], service: [["Duration", "المدة"], ["Includes", "يشمل"]] };

// --- media helpers: uploaded files travel as data URLs; players for links
const dataToBlobUrl = (dataUrl) => { const m = /^data:([^;]*);base64,(.*)$/s.exec(dataUrl || ""); if (!m) return dataUrl;
  const bin = atob(m[2]), u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
  return URL.createObjectURL(new Blob([u], { type: m[1] || "application/octet-stream" })); };
let mvLoading = null;
const loadModelViewer = () => mvLoading || (mvLoading = new Promise((res, rej) => { if (customElements.get("model-viewer")) return res();
  const sc = document.createElement("script"); sc.src = "vendor/model-viewer-4.3.1.min.js"; sc.onload = () => res(); sc.onerror = () => { mvLoading = null; rej(new Error("3D viewer could not load")); }; document.head.appendChild(sc); }));
function embedUrl(url) {
  let m;
  if ((m = /(?:youtube\.com\/(?:watch\?(?:.*&)?v=|shorts\/|embed\/)|youtu\.be\/)([\w-]{6,})/i.exec(url))) return { frame: `https://www.youtube-nocookie.com/embed/${m[1]}?rel=0` };
  if ((m = /vimeo\.com\/(?:video\/)?(\d+)/i.exec(url))) return { frame: `https://player.vimeo.com/video/${m[1]}` };
  if ((m = /drive\.google\.com\/file\/d\/([\w-]+)/i.exec(url))) return { frame: `https://drive.google.com/file/d/${m[1]}/preview` };
  if ((m = /sketchfab\.com\/(?:3d-models\/[^/?#]*-|models\/)([0-9a-f]{32})/i.exec(url))) return { frame: `https://sketchfab.com/models/${m[1]}/embed?autostart=1` };
  if (/\.(glb|gltf)(\?|#|$)/i.test(url)) return { model: url };
  if (/\.(mp4|webm|ogg|mov)(\?|#|$)/i.test(url)) return { video: url };
  if (/\.(jpe?g|png|webp|gif|avif)(\?|#|$)/i.test(url)) return { img: url };
  return { frame: url };
}
async function mediaStage(box, m) {
  box.innerHTML = `<div class="empty">${t("loading")}</div>`;
  try {
    let src = m.url, kind = m.kind;
    if (m.uploaded) { const f = await api("GET", "/api/product-media/" + m.id); src = f.data; }
    let e = m.uploaded ? null : embedUrl(src);
    const isModel = kind === "model" && (m.uploaded || (e && e.model));
    if (kind === "photo" && (m.uploaded || (e && e.img))) box.innerHTML = `<img src="${esc(src)}" alt="${esc(m.title || "")}">`;
    else if (kind === "video" && m.uploaded) box.innerHTML = `<video src="${esc(dataToBlobUrl(src))}" controls playsinline preload="metadata"></video>`;
    else if (isModel) {
      await loadModelViewer();
      const u = m.uploaded ? dataToBlobUrl(src) : e.model;
      box.innerHTML = `<model-viewer src="${esc(u)}" camera-controls touch-action="pan-y" auto-rotate shadow-intensity="1" ar exposure="1" alt="${esc(m.title || "3D")}"></model-viewer>`;
    } else if (e && e.video) box.innerHTML = `<video src="${esc(e.video)}" controls playsinline preload="metadata"></video>`;
    else if (e && e.img) box.innerHTML = `<img src="${esc(e.img)}" alt="">`;
    else if (e && e.frame) box.innerHTML = `<iframe src="${esc(e.frame)}" allow="autoplay; fullscreen; xr-spatial-tracking; accelerometer; gyroscope" allowfullscreen loading="lazy" referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
    else box.innerHTML = `<a class="btn" href="${esc(src)}" download>${esc(m.title || L2("Download", "تحميل"))}</a>`;
  } catch (err) { box.innerHTML = `<div class="banner err">${esc(err.message)}</div>`; }
}
const MEDIA_ICON = { photo: "📷", video: "🎬", model: "🧊", tour: "🌐", file: "📄" };
const mediaThumb = (m, on) => `<button type="button" class="mthumb${on ? " on" : ""}" data-a="pmsel" data-id="${m.id}" title="${esc(m.title || m.kind)}">${m.thumb || (m.kind === "photo" && !m.uploaded && m.url) ? `<img src="${esc(m.thumb || m.url)}" alt="">` : `<span>${MEDIA_ICON[m.kind] || "•"}</span>`}</button>`;
// shrink photos before upload (they sync to every device): long side 1600 px + a 360 px thumbnail
function shrinkImage(file, max, q) {
  return new Promise((res, rej) => { const img = new Image(), u = URL.createObjectURL(file);
    img.onload = () => { const r = Math.min(1, max / Math.max(img.width, img.height)), c = document.createElement("canvas");
      c.width = Math.round(img.width * r); c.height = Math.round(img.height * r); c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(u); res(c.toDataURL("image/jpeg", q)); };
    img.onerror = () => { URL.revokeObjectURL(u); rej(new Error(L2("Not an image this browser can read", "صيغة صورة غير مدعومة"))); }; img.src = u; });
}
const readDataUrl = file => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => rej(r.error); r.readAsDataURL(file); });
const MAX_UP = 8 * 1024 * 1024;

// ------------------------------------------------------------------ showroom
PAGES.products = async (view, parts, q) => {
  const d = await api("GET", "/api/products?" + qs(q));
  const add = can("shop.products", "add"), sell = can("shop.orders", "add");
  S.cache.pcats = d.categories;
  Object.assign(S.H, { pnewp: () => productForm(), pset: () => shopSettingsForm(), pcalc: () => planCalculator() });
  const st = d.unit_status || {};
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="pnewp">+ ${L2("Product / unit", "منتج / وحدة")}</button>` : ""}
      ${sell ? `<a class="btn" href="#/sales">${t("m_sales_orders")}</a>` : ""}<button class="btn" data-a="pcalc">${L2("Payment plan calculator", "حاسبة التقسيط")}</button>
      ${can("shop.orders", "edit") ? `<button class="btn" data-a="pset">⚙ ${L2("Sales settings", "إعدادات المبيعات")}</button>` : ""}<span class="spacer"></span>${exportMenu("products", q)}</div>` +
    filterBar(q, ["q", ["select", "kind", L2("Type", "النوع"), P_KINDS().map(([v, l]) => [v, l.split(" (")[0]])], ["select", "category", L2("Category", "التصنيف"), d.categories.map(c => [c, c])],
      ["select", "status", t("status"), Object.keys(P_ST).map(k => [k, L2(P_ST[k][0], P_ST[k][1])])], "bu"]) +
    (Object.keys(st).length ? `<div class="kpis">${["available", "reserved", "sold", "delivered"].map(k => kpiBox(L2(P_ST[k][0] + " units", "وحدات " + P_ST[k][1]), numf(st[k] || 0))).join("")}</div>` : "") +
    (d.rows.length ? `<div class="pgrid">${d.rows.map(p => `<a class="card pcard" href="#/product/${p.id}">
        <div class="pimg">${p.cover_thumb ? `<img src="${esc(p.cover_thumb)}" alt="" loading="lazy">` : `<span>${p.kind === "unit" ? "⌂" : p.kind === "service" ? "✦" : "▣"}</span>`}${p.kind === "unit" || p.status === "hidden" ? `<div class="pst">${pStatus(p.status)}</div>` : ""}</div>
        <div class="pbody"><b>${esc(pname(p))}</b><div class="muted">${esc([p.code, p.category, p.bu_code].filter(Boolean).join(" · "))}</div>
        <div class="row" style="justify-content:space-between;align-items:baseline;margin-top:6px"><span class="pprice">${money(p.price)}</span>
        <span class="muted">${Object.entries(p.media_counts || {}).map(([k, n]) => `${MEDIA_ICON[k] || ""}${n}`).join(" ")}${p.in_stock != null ? ` · ${L2("stock", "مخزون")} ${numf(p.in_stock)}` : ""}</span></div></div></a>`).join("")}</div>`
      : `<div class="card"><div class="empty">${L2("No products yet. Add what you sell — items, apartments / villas / shops, cars, machines or services — with photos, video clips and 3D models, then sell them for cash, on deferred payment or in installments.", "لا توجد منتجات بعد. أضف ما تبيعه — أصناف، شقق / فيلات / محلات، سيارات، معدات أو خدمات — مع الصور والفيديو والنماذج ثلاثية الأبعاد، ثم بعها كاش أو آجل أو بالتقسيط.")}</div></div>`);
};

PAGES.product = async (view, parts) => {
  const p = await api("GET", "/api/products/" + encodeURIComponent(parts[1]));
  const ed = can("shop.products", "edit"), add = can("shop.products", "add"), sell = can("shop.orders", "add");
  const media = p.media;
  let cur = media.find(m => m.id === p.cover) || media[0];
  const sellable = p.kind !== "unit" || ["available", "reserved"].includes(p.status) && !p.sale;
  view.innerHTML = `<div class="toolbar"><a class="btn" href="#/products">← ${t("m_products")}</a>
      ${sell && sellable ? `<button class="btn pri" data-a="psell">${L2("Sell", "بيع")}</button>` : ""}${p.sale ? `<a class="btn" href="#/sale/${encodeURIComponent(p.sale)}">${L2("Sale", "البيع")} ${esc(p.sale)} →</a>` : ""}
      <button class="btn" data-a="pplan">${L2("Payment plan", "خطة السداد")}</button>${ed ? `<button class="btn" data-a="pedit">${t("edit")}</button>` : ""}
      ${add ? `<button class="btn" data-a="padd">+ ${L2("Photos / video / 3D", "صور / فيديو / 3D")}</button>` : ""}
      <a class="btn" href="/api/products/${p.id}/doc?lang=${S.lang}" target="_blank" rel="noopener">⎙ ${L2("Brochure", "بروشور")}</a><span class="spacer"></span>
      ${ed ? `<button class="btn danger" data-a="pdel">${t("delete")}</button>` : ""}</div>
    <div class="pview"><div class="pmedia card">${media.length ? `<div class="stage" id="pstage"></div>
        <div class="row" style="gap:6px;margin-top:6px"><div class="mstrip">${media.map(m => mediaThumb(m, cur && m.id === cur.id)).join("")}</div><span class="spacer"></span>
        <button type="button" class="btn sm" data-a="pfull" title="${esc(L2("Full screen — show the client", "ملء الشاشة — اعرض على العميل"))}">⛶</button></div>
        <div id="pmtools" class="row muted" style="gap:6px;margin-top:6px;font-size:.85em"></div>`
      : `<div class="empty">${L2("No photos, clips or 3D models yet.", "لا توجد صور أو فيديو أو نماذج 3D بعد.")}${add ? ` <button class="btn sm" data-a="padd">+ ${L2("Add", "إضافة")}</button>` : ""}</div>`}</div>
      <div class="pinfo card"><h2 style="margin-top:0">${esc(pname(p))}</h2><div class="row" style="gap:6px;flex-wrap:wrap">${pStatus(p.status)}<span class="chip">${esc((P_KINDS().find(k => k[0] === p.kind) || ["", p.kind])[1].split(" (")[0])}</span>${p.category ? `<span class="chip">${esc(p.category)}</span>` : ""}</div>
        <div class="pprice big">${money(p.price)} <span class="muted">${esc(cur$())}</span></div>
        <div class="tbl"><table><tbody>${[[t("code"), p.code], [L2("Project", "المشروع"), p.bu_code ? `${p.bu_code} — ${p.bu_name}` : ""], [t("unit"), p.unit],
          ...(p.in_stock != null ? [[L2("In stock", "بالمخزن"), numf(p.in_stock)]] : []), ...p.specs].filter(r => r[1] !== "" && r[1] != null).map(([k, v]) => `<tr><th style="width:40%">${esc(k)}</th><td>${esc(v)}</td></tr>`).join("")}</tbody></table></div>
        ${p.description ? `<p class="pre" style="white-space:pre-line">${esc(p.description)}</p>` : ""}<div id="pplanbox"></div></div></div>`;
  const stage = $("#pstage", view), tools = $("#pmtools", view);
  const show = m => { cur = m; $$(".mthumb", view).forEach(b => b.classList.toggle("on", +b.dataset.id === m.id)); mediaStage(stage, m);
    if (tools) tools.innerHTML = `${MEDIA_ICON[m.kind] || ""} ${esc(m.title || "")}${m.url ? ` <a href="${esc(m.url)}" target="_blank" rel="noopener">${L2("open link", "فتح الرابط")} ↗</a>` : ""}<span class="spacer"></span>
      ${ed ? `${m.kind === "photo" && m.id !== p.cover ? `<button class="btn sm" data-a="pmcover">${L2("Make cover", "صورة الغلاف")}</button>` : ""}<button class="btn sm" data-a="pmmove" data-d="up">◀</button><button class="btn sm" data-a="pmmove" data-d="down">▶</button>
      <button class="btn sm" data-a="pmren">${L2("Rename", "تسمية")}</button><button class="btn sm danger" data-a="pmdel">${t("delete")}</button>` : ""}`; };
  Object.assign(S.H, {
    pmsel: el => show(media.find(m => m.id === +el.dataset.id)),
    pfull: () => { const s = $("#pstage", view); (s.requestFullscreen || s.webkitRequestFullscreen || (() => {})).call(s); },
    psell: () => saleForm(null, { lines: [{ product_id: p.id, qty: 1, price: p.price, unit: p.unit }], bu_id: p.bu_id || "" }),
    pplan: () => planCalculator(p), pedit: () => productForm(p), padd: () => mediaForm(p),
    pdel: () => confirmDelete(`${p.code} — ${p.name}`, "/api/products/" + p.id, async () => { location.hash = "#/products"; }),
    pmcover: async () => { await api("PUT", "/api/product-media/" + cur.id, { cover: 1 }); toast(t("saved")); route(); },
    pmmove: async el => { await api("PUT", "/api/product-media/" + cur.id, { move: el.dataset.d }); route(); },
    pmren: async () => { const v = prompt(L2("Title", "العنوان"), cur.title || ""); if (v == null) return; await api("PUT", "/api/product-media/" + cur.id, { title: v }); route(); },
    pmdel: async () => { if (!confirm(L2("Delete this file?", "حذف هذا الملف؟"))) return; await api("DELETE", "/api/product-media/" + cur.id); route(); } });
  if (cur) show(cur);
  const keys = e => { if (!view.isConnected) return document.removeEventListener("keydown", keys); if ($(".drawer-bg") || /INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || "")) return;
    const i = media.indexOf(cur); if (e.key === "ArrowRight" || e.key === "ArrowLeft") { const n = (i + ((e.key === "ArrowRight") !== (document.dir === "rtl") ? 1 : -1) + media.length) % media.length; if (media[n]) show(media[n]); } };
  document.addEventListener("keydown", keys);
  if (p.price > 0) planBox($("#pplanbox", view), p);
};
const cur$ = () => (S.boot.settings && S.boot.settings.currency) || "";

// a quick installment example on the product page (company defaults; full calculator a click away)
async function planBox(box, p) {
  try { const st = await api("GET", "/api/shop-settings"); if (!(st.n > 1)) return;
    const r = await api("POST", "/api/sales-preview", { lines: [{ product_id: p.id, qty: 1 }], vat_code: p.vat_code, date: today(), plan: { type: "installments", down_pct: st.down_pct, n: st.n, frequency: st.frequency, rate: st.rate, round_to: st.round_to } });
    const inst = r.schedule.find(x => x.kind === "installment"), down = r.schedule.filter(x => x.kind === "down").reduce((a, x) => a + x.amount, 0);
    box.innerHTML = `<div class="banner info">${L2("Installments", "بالتقسيط")}: ${L2("down", "مقدم")} <b>${money(down)}</b> + ${st.n} × <b>${money(inst ? inst.amount : 0)}</b> ${(FREQS().find(f => f[0] === st.frequency) || ["", ""])[1]}${st.rate ? ` · ${L2("interest", "فائدة")} ${st.rate}%/${L2("yr", "سنة")}` : ""} · ${L2("total", "الإجمالي")} ${money(r.total)}</div>`;
  } catch { /* no permission or no price */ }
}

function productForm(p) {
  const isNew = !p; p = p || { kind: "unit", status: "available", specs: [] };
  let specs = (p.specs || []).map(x => [...x]);
  const specRows = () => specs.map((r, i) => `<tr><td><input data-skip="1" value="${esc(r[0])}" data-input="pspec" data-i="${i}" data-k="0" list="pspecl"></td><td><input data-skip="1" value="${esc(r[1])}" data-input="pspec" data-i="${i}" data-k="1"></td><td><button type="button" class="btn sm" data-a="pspecdel" data-i="${i}">✕</button></td></tr>`).join("");
  const revAccts = [["", L2("Default — Sales of Goods & Units", "الافتراضي — مبيعات البضائع والوحدات")], ...accts(["Revenue"])];
  const invAccts = [["", L2("— no cost booked —", "— بدون تكلفة —")], ...accts(["Asset"], ["inventory", "other_current_asset", "fixed_asset"])];
  drawer(isNew ? L2("New product / unit", "منتج / وحدة جديدة") : `${t("edit")} — ${p.code}`, form([
    { k: "kind", l: L2("Type", "النوع"), type: "select", opts: P_KINDS(), v: p.kind, change: "pkind", span: "spanall" },
    { k: "name", l: t("name"), v: p.name, req: 1 }, { k: "name_ar", l: t("name") + " (العربية)", v: p.name_ar },
    { k: "code", l: t("code"), v: p.code, ph: L2("automatic", "تلقائي") }, { k: "category", l: L2("Category", "التصنيف"), v: p.category, list: "pcatl", ph: L2("Apartments, Villas, Furniture…", "شقق، فيلات، أثاث…") },
    { k: "price", l: L2("Price", "السعر"), type: "number", v: p.price, req: 1 }, { k: "unit", l: t("unit"), v: p.unit, list: "units", ph: "m², pcs, set" },
    { k: "status", l: t("status"), type: "select", opts: Object.keys(P_ST).map(k => [k, L2(P_ST[k][0], P_ST[k][1])]), v: p.status },
    { k: "bu_id", l: L2("Project / business unit", "المشروع / وحدة الأعمال"), type: "select", opts: buOpts("—"), v: p.bu_id || "" },
    { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat"), v: p.vat_code },
    { k: "revenue_account", l: L2("Revenue account", "حساب الإيراد"), type: "select", opts: revAccts, v: p.revenue_account || "" },
    { k: "stock_item", l: L2("Sold from stock item", "يُصرف من صنف المخزون"), v: p.stock_item, list: "pstock", help: L2("Selling it issues the stock and books its cost", "البيع يصرف من المخزن ويسجل التكلفة") },
    { k: "warehouse_id", l: t("warehouse"), type: "select", opts: [["", L2("any with stock", "أي مخزن به رصيد")], ...whOpts()], v: p.warehouse_id || "" },
    { k: "cost", l: L2("Cost per unit", "تكلفة الوحدة"), type: "number", v: p.cost || "", help: L2("Booked as cost of sales when sold (units and goods not in the stock module)", "تُسجل كتكلفة مبيعات عند البيع (للوحدات والبضائع غير المسجلة بالمخزن)") },
    { k: "inventory_account", l: L2("…taken out of", "…تُخصم من حساب"), type: "select", opts: invAccts, v: p.inventory_account || "" },
    { html: `<h4 class="fsec">${L2("Specifications", "المواصفات")}</h4><div class="tbl"><table id="pspect"><tbody>${specRows()}</tbody></table></div>
      <div class="row" style="gap:6px;margin-top:6px"><button type="button" class="btn sm" data-a="pspecadd">+ ${L2("Line", "سطر")}</button><button type="button" class="btn sm" data-a="pspecpre">${L2("Suggested fields", "حقول مقترحة")}</button></div>`, span: "spanall" },
    { k: "description", l: L2("Description", "الوصف"), type: "textarea", v: p.description, span: "spanall" }])
    + datalist("pcatl", S.cache.pcats || []) + datalist("pstock", S.boot.items.map(i => i.name)) + unitsList()
    + datalist("pspecl", Object.values(SPEC_PRESETS).flat().map(x => L2(x[0], x[1]))),
  async v => { v.specs = specs.filter(r => r[0]); const r = isNew ? await api("POST", "/api/products", v) : await api("PUT", "/api/products/" + p.id, v);
    toast(t("saved")); if (isNew) { location.hash = "#/product/" + r.id; closeDrawer(); return false; } },
  { wide: true, onOpen: f => {
    const redraw = () => { $("#pspect tbody", f).innerHTML = specRows(); };
    S.H.pspec = el => { specs[+el.dataset.i][+el.dataset.k] = el.value; };
    S.H.pspecadd = () => { specs.push(["", ""]); redraw(); };
    S.H.pspecdel = el => { specs.splice(+el.dataset.i, 1); redraw(); };
    S.H.pspecpre = () => { const have = new Set(specs.map(r => r[0])); (SPEC_PRESETS[$("[name=kind]", f).value] || []).forEach(x => { const l = L2(x[0], x[1]); if (!have.has(l)) specs.push([l, ""]); }); redraw(); };
    S.H.pkind = () => { const k = $("[name=kind]", f).value; showFields(f, k === "item" ? ["stock_item", "warehouse_id", "cost", "inventory_account", "unit"] : k === "unit" ? ["cost", "inventory_account", "status"] : ["unit"], ["stock_item", "warehouse_id", "cost", "inventory_account", "unit", "status"]);
      if (isNew && !specs.length) S.H.pspecpre(); };
    S.H.pkind(); } });
}

function mediaForm(p) {
  drawer(L2("Add photos, clips & 3D models", "إضافة صور وفيديو ونماذج 3D") + " — " + p.code, form([
    { k: "kind", l: L2("What", "النوع"), type: "select", span: "spanall", change: "pmk", opts: [["photo", L2("Photos (several at once)", "صور (أكثر من صورة)")], ["video", L2("Video clip", "فيديو")], ["model", L2("3D model", "نموذج ثلاثي الأبعاد")], ["tour", L2("360° / virtual tour (link)", "جولة افتراضية 360° (رابط)")], ["file", L2("Other file (PDF plan, brochure…)", "ملف آخر (مخطط PDF، بروشور…)")]] },
    { k: "title", l: L2("Title (optional)", "العنوان (اختياري)"), span: "spanall" },
    { html: `<label class="f" id="pmfile">${L2("Upload from this device", "رفع من الجهاز")}<input type="file" data-skip="1" id="pmin" multiple></label>`, span: "spanall" },
    { k: "url", l: L2("…or paste a link", "…أو الصق رابطاً"), span: "spanall", ph: "https://" },
    { html: `<div class="muted" id="pmhelp" style="font-size:.88em"></div><div id="pmprog"></div>`, span: "spanall" }]),
  async v => { const files = [...$("#pmin").files]; const prog = $("#pmprog");
    if (!files.length && !v.url) throw new Error(L2("Choose a file or paste a link", "اختر ملفاً أو الصق رابطاً"));
    let n = 0;
    for (const file of files) { prog.textContent = `${++n} / ${files.length} …`;
      let body = { kind: v.kind, title: v.title || (files.length > 1 ? "" : file.name.replace(/\.[^.]+$/, "")) };
      if (v.kind === "photo") { body.data = await shrinkImage(file, 1600, 0.82); body.thumb = await shrinkImage(file, 360, 0.7); }
      else { if (file.size > MAX_UP) throw new Error(`${file.name}: ${(file.size / 1048576).toFixed(1)} MB — ${L2("the limit is 8 MB; upload it to YouTube / Sketchfab / Drive and paste the link", "الحد الأقصى 8 ميجابايت؛ ارفعه على يوتيوب / Sketchfab / درايف والصق الرابط")}`);
        body.data = await readDataUrl(file); if (/\.glb$/i.test(file.name)) body.data = body.data.replace(/^data:[^;]*;/, "data:model/gltf-binary;"); if (/\.gltf$/i.test(file.name)) body.data = body.data.replace(/^data:[^;]*;/, "data:model/gltf+json;"); }
      await api("POST", `/api/products/${p.id}/media`, body); }
    if (v.url) await api("POST", `/api/products/${p.id}/media`, { kind: v.kind, title: v.title, url: v.url });
    toast(t("saved")); },
  { submitLabel: L2("Add", "إضافة"), onOpen: f => {
    S.H.pmk = () => { const k = $("[name=kind]", f).value, inp = $("#pmin", f);
      inp.accept = { photo: "image/*", video: "video/*", model: ".glb,.gltf,model/gltf-binary", tour: "", file: "" }[k]; $("#pmfile", f).style.display = k === "tour" ? "none" : "";
      $("#pmhelp", f).innerHTML = { photo: L2("Photos are resized to 1600 px before upload so they stay light on every device.", "تُصغّر الصور إلى 1600 بكسل قبل الرفع لتبقى خفيفة على كل الأجهزة."),
        video: L2("Short clips up to 8 MB can be uploaded. For longer videos paste a YouTube, Vimeo or Google Drive link — they play right here.", "يمكن رفع مقاطع قصيرة حتى 8 ميجابايت. للفيديو الأطول الصق رابط يوتيوب أو Vimeo أو جوجل درايف — ويُعرض هنا مباشرة."),
        model: L2("3ds Max, Blender, SketchUp, Revit…: export the model as <b>glTF binary (.glb)</b> (3ds Max: File → Export → glTF, or the Babylon.js exporter) and upload it here (up to 8 MB) — it can be rotated, zoomed and viewed in AR on phones. Or upload it to <b>Sketchfab</b> (accepts .max/.fbx/.obj) and paste the link.", "3ds Max أو Blender أو SketchUp أو Revit…: صدّر النموذج بصيغة <b>glTF binary (.glb)</b> (في 3ds Max: File → Export → glTF أو إضافة Babylon.js) وارفعه هنا (حتى 8 ميجابايت) — يمكن تدويره وتكبيره وعرضه بالواقع المعزز على الموبايل. أو ارفعه على <b>Sketchfab</b> (يقبل max/fbx/obj) والصق الرابط."),
        tour: L2("Paste a Matterport, Kuula, Google Street View or any 360° tour link.", "الصق رابط جولة Matterport أو Kuula أو Google Street View أو أي جولة 360°."),
        file: L2("PDF floor plans, price lists, brochures (up to 8 MB).", "مخططات PDF وقوائم أسعار وبروشورات (حتى 8 ميجابايت).") }[k]; };
    S.H.pmk(); } });
}

function shopSettingsForm() {
  api("GET", "/api/shop-settings").then(st => drawer(L2("Sales settings", "إعدادات المبيعات"), form([
    { html: `<p class="muted" style="margin:0">${L2("Defaults for new sales and the installment example shown on every product.", "القيم الافتراضية للمبيعات الجديدة ومثال التقسيط المعروض على كل منتج.")}</p>`, span: "spanall" },
    { k: "down_pct", l: L2("Default down payment %", "المقدم الافتراضي %"), type: "number", v: st.down_pct }, { k: "n", l: L2("Default number of installments", "عدد الأقساط الافتراضي"), type: "number", v: st.n },
    { k: "frequency", l: L2("Every", "كل"), type: "select", opts: FREQS(), v: st.frequency }, { k: "rate", l: L2("Installment interest % per year (flat)", "فائدة التقسيط % سنوياً (ثابتة)"), type: "number", v: st.rate },
    { k: "round_to", l: L2("Round installments to", "تقريب القسط إلى"), type: "number", v: st.round_to, help: L2("e.g. 10 or 100 — the last installment takes the difference", "مثال 10 أو 100 — والقسط الأخير يأخذ الفرق") },
    { k: "revenue_on", l: L2("Recognise the sale (revenue)", "تسجيل الإيراد"), type: "select", opts: [["sale", L2("When sold", "عند البيع")], ["delivery", L2("On delivery / handover (off-plan, made to order)", "عند التسليم (بيع على الخريطة / حسب الطلب)")]], v: st.revenue_on },
    { k: "terms", l: L2("Contract terms (English)", "شروط العقد (إنجليزي)"), type: "textarea", v: st.terms, span: "spanall" },
    { k: "terms_ar", l: L2("Contract terms (Arabic)", "شروط العقد (عربي)"), type: "textarea", v: st.terms_ar, span: "spanall" }]),
  async v => { await api("PUT", "/api/shop-settings", v); toast(t("saved")); })).catch(fail);
}

// -------------------------------------------------------- schedule preview
const schedTable = (rows, opts = {}) => `<div class="tbl"><table><thead><tr><th>#</th><th>${L2("Due", "الاستحقاق")}</th><th>${L2("Payment", "الدفعة")}</th><th class="num">${t("amount")}</th>${opts.status ? `<th class="num">${L2("Paid", "المسدد")}</th><th class="num">${L2("Left", "المتبقي")}</th><th>${t("status")}</th><th></th>` : ""}</tr></thead><tbody>
  ${rows.map((r, i) => `<tr><td>${i + 1}</td><td>${fd(r.due)}</td><td>${esc(instLabel(r))}</td><td class="num">${money(r.amount)}</td>${opts.status ? `<td class="num">${money(r.paid + r.by_cheque)}</td><td class="num">${r.left ? money(r.left) : ""}</td><td>${iStatus(r.status)}${r.days_late ? ` <span class="muted">${r.days_late} ${L2("days", "يوم")}</span>` : ""}</td><td>${opts.pay && r.left > 0 ? `<button class="btn sm" data-a="spay" data-id="${r.id}">${L2("Pay", "سداد")}</button>` : ""}</td>` : ""}</tr>`).join("")}</tbody></table></div>`;
const planFields = (pl, st) => [
  { k: "plan_type", l: L2("Payment", "طريقة السداد"), type: "select", opts: PLAN_T(), v: pl.type || "full", change: "plt" },
  { k: "plan_due", l: L2("Pay on", "تاريخ السداد"), type: "date", v: pl.due || "" },
  { k: "plan_down_pct", l: L2("Down payment %", "المقدم %"), type: "number", v: pl.down_pct ?? st.down_pct },
  { k: "plan_down", l: L2("…or down payment amount", "…أو مبلغ المقدم"), type: "number", v: pl.down ?? "" },
  { k: "plan_n", l: L2("Number of installments", "عدد الأقساط"), type: "number", v: pl.n ?? st.n },
  { k: "plan_frequency", l: L2("Every", "كل"), type: "select", opts: FREQS(), v: pl.frequency || st.frequency },
  { k: "plan_first_due", l: L2("First installment", "أول قسط"), type: "date", v: pl.first_due || "" },
  { k: "plan_rate", l: L2("Interest % per year (flat)", "الفائدة % سنوياً (ثابتة)"), type: "number", v: pl.rate ?? st.rate },
  { k: "plan_round_to", l: L2("Round installments to", "تقريب القسط إلى"), type: "number", v: pl.round_to ?? st.round_to }];
const PLAN_SHOW = { full: [], deferred: ["plan_due"], installments: ["plan_down_pct", "plan_down", "plan_n", "plan_frequency", "plan_first_due", "plan_rate", "plan_round_to"], custom: [] };
const PLAN_ALL = ["plan_due", "plan_down_pct", "plan_down", "plan_n", "plan_frequency", "plan_first_due", "plan_rate", "plan_round_to"];
function readPlan(f, extras, custom) {
  const g = n => { const el = $(`[name=${n}]`, f); return el ? el.value : ""; };
  const ty = g("plan_type");
  const pl = { type: ty };
  if (ty === "deferred") pl.due = g("plan_due");
  if (ty === "installments") Object.assign(pl, { down_pct: g("plan_down_pct"), down: g("plan_down"), n: g("plan_n"), frequency: g("plan_frequency"), first_due: g("plan_first_due"), rate: g("plan_rate"), round_to: g("plan_round_to"), extras: extras.filter(x => x.amount || x.pct) });
  if (ty === "custom") pl.rows = custom.filter(r => r.due && r.amount);
  return pl;
}
const extrasHtml = ex => `<div class="tbl"><table><thead><tr><th>${L2("Extra payment", "دفعة إضافية")}</th><th>%</th><th>${L2("or amount", "أو مبلغ")}</th><th>${L2("Due", "الاستحقاق")}</th><th></th></tr></thead><tbody>
  ${ex.map((x, i) => `<tr><td><input data-skip="1" value="${esc(x.label || "")}" data-input="plx" data-i="${i}" data-k="label" placeholder="${esc(L2("e.g. On delivery", "مثال: عند الاستلام"))}"></td><td><input data-skip="1" class="num" type="number" step="any" value="${x.pct ?? ""}" data-input="plx" data-i="${i}" data-k="pct" style="width:70px"></td>
    <td><input data-skip="1" class="num" type="number" step="any" value="${x.amount ?? ""}" data-input="plx" data-i="${i}" data-k="amount" style="width:110px"></td><td><input data-skip="1" type="date" value="${esc(x.due || "")}" data-input="plx" data-i="${i}" data-k="due"></td><td><button type="button" class="btn sm" data-a="plxdel" data-i="${i}">✕</button></td></tr>`).join("")}</tbody></table></div>
  <button type="button" class="btn sm" data-a="plxadd" style="margin-top:4px">+ ${L2("Extra payment (e.g. on delivery)", "دفعة إضافية (مثل الاستلام)")}</button>`;
const customHtml = rows => `<div class="tbl"><table><thead><tr><th>${L2("Due", "الاستحقاق")}</th><th>${L2("Payment", "الدفعة")}</th><th>${t("amount")}</th><th></th></tr></thead><tbody>
  ${rows.map((x, i) => `<tr><td><input data-skip="1" type="date" value="${esc(x.due || "")}" data-input="plc" data-i="${i}" data-k="due"></td><td><input data-skip="1" value="${esc(x.label || "")}" data-input="plc" data-i="${i}" data-k="label"></td>
    <td><input data-skip="1" class="num" type="number" step="any" value="${x.amount ?? ""}" data-input="plc" data-i="${i}" data-k="amount" style="width:120px"></td><td><button type="button" class="btn sm" data-a="plcdel" data-i="${i}">✕</button></td></tr>`).join("")}</tbody></table></div>
  <button type="button" class="btn sm" data-a="plcadd" style="margin-top:4px">+ ${L2("Payment", "دفعة")}</button>`;

// wires a plan section inside a form; preview(body) is called whenever something changes
function wirePlan(f, pl, preview) {
  let extras = (pl.extras || []).map(x => ({ ...x })), custom = (pl.rows || []).map(x => ({ ...x }));
  const redraw = () => { $("#plx", f).innerHTML = extrasHtml(extras); $("#plc", f).innerHTML = customHtml(custom); };
  const sync = () => { const ty = $("[name=plan_type]", f).value; showFields(f, PLAN_SHOW[ty], PLAN_ALL);
    $("#plx", f).style.display = ty === "installments" ? "" : "none"; $("#plc", f).style.display = ty === "custom" ? "" : "none"; $("#plcust", f).style.display = ty === "installments" || ty === "deferred" ? "" : "none"; };
  Object.assign(S.H, {
    plt: () => { sync(); preview(); },
    plx: el => { extras[+el.dataset.i][el.dataset.k] = el.value; preview(); }, plxadd: () => { extras.push({ label: "", pct: "", amount: "", due: "" }); redraw(); },
    plxdel: el => { extras.splice(+el.dataset.i, 1); redraw(); preview(); },
    plc: el => { custom[+el.dataset.i][el.dataset.k] = el.value; preview(); }, plcadd: () => { custom.push({ due: "", label: "", amount: "" }); redraw(); },
    plcdel: el => { custom.splice(+el.dataset.i, 1); redraw(); preview(); },
    plcust: () => { const rows = (S.cache.lastSched || []); custom = rows.map(r => ({ due: r.due, label: instLabel(r), amount: r.amount, kind: r.kind })); $("[name=plan_type]", f).value = "custom"; redraw(); sync(); preview(); } });
  redraw(); sync();
  return () => readPlan(f, extras, custom);
}
const planSection = (pl, st) => [{ html: `<h4 class="fsec">${L2("Payment plan", "خطة السداد")}</h4>`, span: "spanall" }, ...planFields(pl, st),
  { html: `<div id="plx"></div><div id="plc"></div><button type="button" class="btn sm" id="plcust" data-a="plcust" style="margin-top:6px">${L2("Edit the dates / amounts by hand", "تعديل التواريخ / المبالغ يدوياً")}</button>`, span: "spanall" },
  { html: `<div id="plprev" style="margin-top:8px"></div>`, span: "spanall" }];
const previewHtml = r => `<div class="kpis">${kpiBox(L2("Price", "الثمن"), money(r.price))}${r.markup ? kpiBox(L2("Installment interest", "فوائد التقسيط"), money(r.markup)) : ""}${kpiBox(L2("Total payable", "إجمالي المستحق"), money(r.total), "pos")}</div>${schedTable(r.schedule)}`;

async function planCalculator(p) {
  const st = await api("GET", "/api/shop-settings");
  let getPlan;
  drawer(L2("Payment plan calculator", "حاسبة التقسيط") + (p ? ` — ${p.code}` : ""), form([
    { k: "price", l: L2("Price", "السعر"), type: "number", v: p ? p.price : "", req: 1 }, { k: "date", l: t("date"), type: "date", v: today() },
    ...planSection({ type: "installments" }, st)]),
  null, { wide: true, readOnly: true, extraButtons: p && can("shop.orders", "add") ? `<button type="button" class="btn pri" data-a="pcsell">${L2("Sell with this plan", "بيع بهذه الخطة")}</button>` : "",
    onOpen: f => {
      const prev = debounce(async () => { const v = readForm(f), box = $("#plprev", f); if (!(+v.price > 0)) { box.innerHTML = ""; return; }
        try { const r = await api("POST", "/api/sales-preview", { date: v.date, lines: [{ description: "x", qty: 1, price: v.price }], plan: getPlan() }); S.cache.lastSched = r.schedule; box.innerHTML = previewHtml(r); }
        catch (e) { box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; } }, 300);
      getPlan = wirePlan(f, { type: "installments" }, prev);
      f.addEventListener("input", prev); f.addEventListener("change", prev); prev();
      S.H.pcsell = () => saleForm(null, { lines: [{ product_id: p.id, qty: 1, price: readForm(f).price, unit: p.unit }], bu_id: p.bu_id || "", plan: getPlan(), date: readForm(f).date }); } });
}

// ------------------------------------------------------------------ sales
PAGES.sales = async (view, parts, q) => {
  const d = await api("GET", "/api/sales?" + qs(q));
  const add = can("shop.orders", "add");
  Object.assign(S.H, { snew: () => saleForm(), pset: () => shopSettingsForm() });
  const T = d.totals;
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="snew">+ ${L2("New sale", "بيع جديد")}</button>` : ""}<a class="btn" href="#/products">${t("m_products")}</a><a class="btn" href="#/installments">${t("m_installments")}</a>
      ${can("shop.orders", "edit") ? `<button class="btn" data-a="pset">⚙ ${L2("Sales settings", "إعدادات المبيعات")}</button>` : ""}<span class="spacer"></span>${exportMenu("sales", q)}</div>` +
    filterBar(q, ["q", "dates", ["select", "status", t("status"), Object.keys(S_ST).map(k => [k, L2(S_ST[k][0], S_ST[k][1])])]]) +
    `<div class="kpis">${kpiBox(L2("Sales (open & delivered)", "المبيعات (المفتوحة والمسلمة)"), T.total, "", `${T.count}`)}${kpiBox(L2("Collected", "المحصل"), T.paid, "pos")}${kpiBox(L2("In cheques", "بشيكات"), T.in_cheques)}${kpiBox(L2("Still to collect", "المتبقي للتحصيل"), T.balance)}${kpiBox(L2("Overdue", "متأخرات"), T.overdue, T.overdue > 0 ? "neg" : "")}</div>
    <div class="card">${d.rows.length ? `<div class="tbl stack"><table><thead><tr><th>${L2("Sale", "البيع")}</th><th>${t("date")}</th><th>${L2("Customer", "العميل")}</th><th>${t("status")}</th><th class="num">${L2("Total", "الإجمالي")}</th><th class="num">${L2("Paid", "المسدد")}</th><th class="num">${L2("Balance", "المتبقي")}</th><th class="num">${L2("Overdue", "متأخر")}</th><th>${L2("Next due", "القسط القادم")}</th></tr></thead><tbody>
      ${d.rows.map(r => `<tr class="click ${r.status === "cancelled" ? "void" : ""}" data-a="sgo" data-id="${r.id}"><td><b>${esc(r.code)}</b></td><td>${fd(r.date)}</td><td>${esc(r.customer)}</td><td>${sStatus(r.status)}</td><td class="num">${money(r.total)}</td><td class="num">${money(r.paid)}</td><td class="num">${money(r.balance)}</td><td class="num ${r.overdue ? "neg" : ""}">${r.overdue ? money(r.overdue) : ""}</td><td>${r.next_due && r.status !== "cancelled" ? `${fd(r.next_due)} · ${money(r.next_amount)}` : ""}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("No sales yet. Pick a product from the showroom and press Sell, or start a new sale here.", "لا توجد مبيعات بعد. اختر منتجاً من المعرض واضغط بيع، أو ابدأ بيعاً جديداً من هنا.")}</div>`}</div>`;
  S.H.sgo = el => { location.hash = "#/sale/" + el.dataset.id; };
};

async function saleForm(s, pre = {}) {
  const isNew = !s; s = s || {};
  const [st, cat] = await Promise.all([api("GET", "/api/shop-settings"), api("GET", "/api/products")]);
  const prods = cat.rows;
  let lines = (s.lines || pre.lines || [{ qty: 1 }]).map(l => ({ product_id: l.product_id || "", description: l.description || "", qty: l.qty ?? 1, unit: l.unit || "", price: l.price ?? "", discount: l.discount || "" }));
  const pl = s.plan || pre.plan || { type: "full" };
  const popt = sel => `<option value="">— ${L2("free text", "بند حر")} —</option>` + prods.filter(p => p.kind !== "unit" || ["available", "reserved"].includes(p.status) && (!p.sale || p.sale === s.code) || String(p.id) === String(sel))
    .map(p => `<option value="${p.id}" ${String(p.id) === String(sel) ? "selected" : ""}>${esc(p.code)} — ${esc(pname(p))} · ${money(p.price)}</option>`).join("");
  const lineRows = () => lines.map((l, i) => `<tr><td><select data-skip="1" data-change="slp" data-i="${i}" style="max-width:220px">${popt(l.product_id)}</select><input data-skip="1" value="${esc(l.description)}" data-input="sl" data-i="${i}" data-k="description" placeholder="${esc(L2("description", "البيان"))}" style="margin-top:4px"></td>
    <td><input data-skip="1" class="num" type="number" step="any" value="${l.qty}" data-input="sl" data-i="${i}" data-k="qty" style="width:70px"></td><td><input data-skip="1" value="${esc(l.unit)}" data-input="sl" data-i="${i}" data-k="unit" style="width:60px"></td>
    <td><input data-skip="1" class="num" type="number" step="any" value="${l.price}" data-input="sl" data-i="${i}" data-k="price" style="width:110px"></td><td><input data-skip="1" class="num" type="number" step="any" value="${l.discount}" data-input="sl" data-i="${i}" data-k="discount" style="width:90px"></td>
    <td><button type="button" class="btn sm" data-a="sldel" data-i="${i}">✕</button></td></tr>`).join("");
  let getPlan;
  const nc = [];
  drawer(isNew ? L2("New sale", "بيع جديد") : `${t("edit")} — ${s.code}`, form([
    { k: "customer", l: L2("Customer", "العميل"), v: s.customer || pre.customer, req: 1, list: "scust" }, { k: "date", l: t("date"), type: "date", v: s.date || pre.date || today(), req: 1 },
    { k: "bu_id", l: L2("Project / business unit", "المشروع / وحدة الأعمال"), type: "select", opts: buOpts(), v: s.bu_id || pre.bu_id || "", req: 1 },
    { k: "revenue_on", l: L2("Recognise the sale", "تسجيل الإيراد"), type: "select", opts: [["sale", L2("Now (when sold)", "الآن (عند البيع)")], ["delivery", L2("On delivery / handover", "عند التسليم")]], v: s.revenue_on || st.revenue_on },
    { k: "delivery_due", l: L2("Expected delivery", "موعد التسليم المتوقع"), type: "date", v: s.delivery_due || "" }, { k: "salesperson", l: L2("Salesperson", "مندوب المبيعات"), v: s.salesperson || "" },
    { html: `<h4 class="fsec">${L2("What is sold", "المبيعات")}</h4><div class="tbl"><table id="slt"><thead><tr><th>${L2("Product / description", "المنتج / البيان")}</th><th>${t("qty")}</th><th>${t("unit")}</th><th>${L2("Price", "السعر")}</th><th>${L2("Discount", "الخصم")}</th><th></th></tr></thead><tbody>${lineRows()}</tbody></table></div>
      <button type="button" class="btn sm" data-a="sladd" style="margin-top:4px">+ ${L2("Line", "سطر")}</button>`, span: "spanall" },
    { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat"), v: s.vat_code || "" }, { k: "wht_code", l: t("wht_code"), type: "select", opts: taxOpts("wht"), v: s.wht_code || "" },
    ...planSection(pl, st),
    { k: "notes", l: t("notes"), type: "textarea", v: s.notes || "", span: "spanall" }]) + partyList("scust"),
  async v => { const body = { ...v, lines: lines.filter(l => l.product_id || l.description), plan: getPlan() };
    const r = isNew ? await api("POST", "/api/sales", body) : await api("PUT", "/api/sales/" + s.id, body);
    await saveNewContacts(nc); toast(t("saved")); location.hash = "#/sale/" + r.id; closeDrawer(); return false; },
  { wide: true, submitLabel: L2("Save draft", "حفظ كمسودة"), onOpen: f => {
    const prev = debounce(async () => { const v = readForm(f), box = $("#plprev", f);
      try { const r = await api("POST", "/api/sales-preview", { date: v.date, vat_code: v.vat_code, wht_code: v.wht_code, lines: lines.filter(l => l.product_id || l.description).map(l => ({ ...l, description: l.description || "x" })), plan: getPlan() });
        S.cache.lastSched = r.schedule; box.innerHTML = previewHtml(r); } catch (e) { box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; } }, 300);
    const redraw = () => { $("#slt tbody", f).innerHTML = lineRows(); prev(); };
    Object.assign(S.H, { sl: el => { lines[+el.dataset.i][el.dataset.k] = el.value; prev(); }, sladd: () => { lines.push({ product_id: "", description: "", qty: 1, unit: "", price: "", discount: "" }); redraw(); },
      sldel: el => { lines.splice(+el.dataset.i, 1); if (!lines.length) lines.push({ qty: 1, description: "", price: "" }); redraw(); },
      slp: el => { const p = prods.find(x => String(x.id) === el.value), l = lines[+el.dataset.i]; l.product_id = el.value;
        if (p) { l.description = `${p.name} (${p.code})`; l.price = p.price; l.unit = p.unit; l.qty = 1; if (p.vat_code && !$("[name=vat_code]", f).value) $("[name=vat_code]", f).value = p.vat_code; if (p.bu_id && !$("[name=bu_id]", f).value) $("[name=bu_id]", f).value = p.bu_id; }
        redraw(); } });
    getPlan = wirePlan(f, pl, prev);
    nc.push(offerContact(f, "customer", "customer"));
    f.addEventListener("change", prev); prev(); } });
}

PAGES.sale = async (view, parts) => {
  const s = await api("GET", "/api/sales/" + encodeURIComponent(parts[1]) + "?as_of=" + today());
  const add = can("shop.orders", "add"), ed = can("shop.orders", "edit"), coll = add || can("shop.installments", "add");
  const live = ["confirmed", "delivered"].includes(s.status);
  const steps = [["draft", L2("Draft", "مسودة")], ["confirmed", L2("Confirmed", "مؤكد")], ["delivered", L2("Delivered", "تم التسليم")]];
  const at = s.status === "cancelled" ? -1 : steps.findIndex(x => x[0] === s.status);
  const wa = waNumber((s.contact || {}).whatsapp || (s.contact || {}).phone);
  const remind = s.overdue > 0 || s.next_due ? encodeURIComponent(S.lang === "ar"
    ? `السيد/ة ${s.customer}، نذكركم ${s.overdue > 0 ? `بمتأخرات بقيمة ${money(s.overdue)}` : `بالقسط المستحق ${fd(s.next_due)} بقيمة ${money(s.next_amount)}`} — ${s.code}. شكراً لكم.`
    : `Dear ${s.customer}, a reminder ${s.overdue > 0 ? `of ${money(s.overdue)} overdue` : `of the installment of ${money(s.next_amount)} due ${fd(s.next_due)}`} on ${s.code}. Thank you.`) : "";
  const doc = k => `/api/sales/${s.id}/doc?kind=${k}&lang=${S.lang}`;
  view.innerHTML = `<div class="toolbar"><a class="btn" href="#/sales">← ${t("m_sales_orders")}</a>
      ${s.status === "draft" && ed ? `<button class="btn" data-a="sedit">${t("edit")}</button>` : ""}${s.status === "draft" && add ? `<button class="btn pri" data-a="sconf">${L2("Confirm sale", "تأكيد البيع")}</button>` : ""}
      ${live && coll && s.uncovered > 0 ? `<button class="btn pri" data-a="spay">${L2("Receive payment", "تحصيل دفعة")}</button><button class="btn" data-a="schq">${L2("Receive cheques", "استلام شيكات")}</button>` : ""}
      ${s.status === "confirmed" && add ? `<button class="btn" data-a="sdel2">${L2("Deliver / hand over", "تسليم")}</button>` : ""}
      <div class="dd"><button class="btn">⎙ ${t("documents")} ▾</button><div class="dd-menu"><a href="${doc("quote")}" target="_blank" rel="noopener">${L2("Quotation", "عرض سعر")}</a><a href="${doc("contract")}" target="_blank" rel="noopener">${L2("Sales contract", "عقد البيع")}</a><a href="${doc("statement")}" target="_blank" rel="noopener">${L2("Customer statement", "كشف حساب العميل")}</a></div></div>
      ${wa && remind && live ? `<a class="btn" href="https://wa.me/${wa}?text=${remind}" target="_blank" rel="noopener">WhatsApp ${L2("reminder", "تذكير")}</a>` : ""}
      <span class="spacer"></span>${live && ed && s.paid > 0 ? `<button class="btn" data-a="sref">${L2("Refund", "رد مبلغ")}</button>` : ""}
      ${ed && s.status !== "cancelled" ? `<button class="btn danger" data-a="scan">${s.status === "draft" ? t("delete") : L2("Cancel sale", "إلغاء البيع")}</button>` : ""}</div>
    <div class="row steps" style="gap:6px;flex-wrap:wrap;margin-bottom:10px">${s.status === "cancelled" ? sStatus("cancelled") : steps.map((x, i) => `<span class="chip ${i <= at ? "ok" : ""}">${i <= at ? "✓ " : ""}${esc(x[1])}</span>`).join("→")}
      <span class="muted">${esc(s.code)} · ${fd(s.date)} · <b>${esc(s.customer)}</b>${s.bu_code ? ` · ${esc(s.bu_code)}` : ""}${s.revenue_on === "delivery" ? ` · ${L2("revenue on delivery", "الإيراد عند التسليم")}` : ""}${s.invoice ? ` · ${L2("invoice", "فاتورة")} <a href="#/invoice/${s.invoice.id}">${esc(s.invoice.number)}</a>` : ""}</span></div>
    <div class="kpis">${kpiBox(L2("Total payable", "إجمالي المستحق"), s.total, "", s.markup ? `${L2("incl. interest", "شامل فوائد")} ${money(s.markup)}` : "")}${kpiBox(L2("Paid", "المسدد"), s.paid, "pos")}${kpiBox(L2("In cheques", "بشيكات"), s.in_cheques)}${kpiBox(L2("Balance", "المتبقي"), s.balance)}${kpiBox(L2("Overdue", "متأخرات"), s.overdue, s.overdue > 0 ? "neg" : "", s.overdue_count ? `${s.overdue_count}` : "")}</div>
    <div class="card"><h2>${L2("Sold", "المبيعات")}</h2><div class="tbl"><table><thead><tr><th>${L2("Description", "البيان")}</th><th class="num">${t("qty")}</th><th class="num">${L2("Price", "السعر")}</th><th class="num">${L2("Discount", "الخصم")}</th><th class="num">${t("amount")}</th></tr></thead><tbody>
      ${s.lines.map(l => `<tr><td>${l.product_id ? `<a href="#/product/${l.product_id}">${esc(l.description)}</a>` : esc(l.description)}</td><td class="num">${numf(l.qty)} ${esc(l.unit)}</td><td class="num">${money(l.price)}</td><td class="num">${l.discount ? money(l.discount) : ""}</td><td class="num">${money(l.amount)}</td></tr>`).join("")}
      ${s.vat ? `<tr><td colspan="4">${t("vat_amt")}</td><td class="num">${money(s.vat)}</td></tr>` : ""}${s.wht ? `<tr><td colspan="4">${t("wht_amt")}</td><td class="num">(${money(s.wht)})</td></tr>` : ""}
      ${s.markup ? `<tr><td colspan="4">${L2("Installment interest", "فوائد التقسيط")}</td><td class="num">${money(s.markup)}</td></tr>` : ""}<tr class="total"><td colspan="4">${L2("Total payable", "إجمالي المستحق")}</td><td class="num">${money(s.total)}</td></tr></tbody></table></div></div>
    <div class="card"><h2>${L2("Payment schedule", "جدول السداد")}</h2>${schedTable(s.schedule, { status: 1, pay: live && coll })}</div>
    ${s.cheques.length ? `<div class="card"><h2>${L2("Cheques", "الشيكات")}</h2><div class="tbl stack"><table><thead><tr><th>${L2("Cheque no.", "رقم الشيك")}</th><th>${L2("Bank", "البنك")}</th><th>${L2("Due", "الاستحقاق")}</th><th class="num">${t("amount")}</th><th>${t("status")}</th><th></th></tr></thead><tbody>
      ${s.cheques.map(c => `<tr><td>${esc(c.number)}</td><td>${esc(c.bank)}</td><td>${fd(c.due)}</td><td class="num">${money(c.amount)}</td><td><span class="chip ${(C_ST[c.status] || [])[2] || ""}">${esc(L2(...(C_ST[c.status] || [c.status, c.status]).slice(0, 2)))}</span>${c.settled_on ? ` <span class="muted">${fd(c.settled_on)}</span>` : ""}</td>
        <td>${c.status === "pending" && coll ? `<button class="btn sm" data-a="chclr" data-id="${c.id}">${L2("Cleared", "تم الصرف")}</button><button class="btn sm danger" data-a="chbnc" data-id="${c.id}">${L2("Bounced", "مرتد")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
    ${s.entries.length ? `<div class="card"><h2>${L2("Vouchers", "القيود")}</h2><div class="tbl"><table><tbody>${s.entries.map(e => `<tr class="click" data-a="openv" data-id="${e.id}"><td>${fd(e.date)}</td><td>${esc(tl(e.kind))}</td><td>${esc(e.memo)}</td><td class="num">${e.amount ? money(e.amount) : ""}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
    ${s.notes ? `<div class="card"><h2>${t("notes")}</h2><p style="white-space:pre-line">${esc(s.notes)}</p></div>` : ""}`;
  const act = (a, body) => api("POST", `/api/sales/${s.id}/${a}`, body);
  Object.assign(S.H, {
    sedit: () => saleForm(s), sconf: () => confirmSaleForm(s), spay: el => collectForm(s, el && el.dataset.id ? s.schedule.find(r => String(r.id) === el.dataset.id) : null),
    schq: () => chequesForm(s), sref: () => refundForm(s),
    sdel2: () => drawer(L2("Deliver / hand over", "تسليم") + " — " + s.code, form([{ k: "date", l: t("date"), type: "date", v: today(), req: 1 },
      { html: `<p class="muted">${s.invoiced ? L2("The sale is already invoiced; this records the handover.", "تم إصدار الفاتورة بالفعل؛ هذا يسجل التسليم.") : L2("The sales invoice is issued now, the money received so far moves from advances to the invoice, and the cost of what was sold is booked.", "تصدر فاتورة البيع الآن، وتُنقل المبالغ المحصلة من الدفعات المقدمة إلى الفاتورة، وتُسجل تكلفة المبيعات.")}</p>`, span: "spanall" }]),
      async v => { await act("deliver", v); toast(t("posted")); }, { submitLabel: L2("Deliver", "تسليم") }),
    scan: async () => { if (s.status === "draft") return confirmDelete(s.code, "/api/sales/" + s.id, async () => { location.hash = "#/sales"; });
      const reason = prompt(L2("Cancel this sale? Units go back to available and the invoice is reversed. Reason:", "إلغاء هذا البيع؟ ستعود الوحدات متاحة وتُعكس الفاتورة. السبب:"), ""); if (reason == null) return;
      try { await act("cancel", { date: today(), reason }); toast(t("saved")); route(); } catch (e) { fail(e); } },
    chclr: el => chequeAction(el.dataset.id, "clear"), chbnc: el => chequeAction(el.dataset.id, "bounce") });
};

function confirmSaleForm(s) {
  const first = s.schedule[0];
  drawer(L2("Confirm sale", "تأكيد البيع") + " — " + s.code, form([
    { k: "date", l: t("date"), type: "date", v: s.date, req: 1 },
    { html: `<p class="muted">${s.revenue_on === "sale" ? L2("The sales invoice is issued now (VAT and e-invoice as usual)", "تصدر فاتورة البيع الآن (الضريبة والفاتورة الإلكترونية كالمعتاد)") : L2("Payments are kept as client advances until delivery; the invoice is issued on delivery.", "تُسجل المدفوعات كدفعات مقدمة حتى التسليم، وتصدر الفاتورة عند التسليم.")}. ${L2("Units on the sale are marked sold.", "الوحدات تصبح مباعة.")}</p>`, span: "spanall" },
    { k: "collect", l: L2("Received now (down payment)", "المحصل الآن (المقدم)"), type: "number", v: first && first.due <= today() ? first.amount : "" },
    { k: "method", l: t("method"), type: "select", opts: methodOpts() }, { k: "ref", l: t("ref") }]),
  async v => { await api("POST", `/api/sales/${s.id}/confirm`, v); toast(t("posted")); }, { submitLabel: L2("Confirm", "تأكيد") });
}
function collectForm(s, row) {
  drawer(L2("Receive payment", "تحصيل دفعة") + " — " + s.code, form([
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: t("amount"), type: "number", req: 1, v: row ? row.left : (s.schedule.find(r => r.left > 0) || {}).left || "" },
    { k: "method", l: t("method"), type: "select", opts: methodOpts(), change: "cmeth" },
    { k: "postdated", l: L2("Post-dated cheque (counts as paid only when it clears)", "شيك آجل (لا يُعتبر مسدداً إلا بعد صرفه)"), type: "checkbox", span: "spanall", change: "cmeth" },
    { k: "cheque_no", l: L2("Cheque no.", "رقم الشيك") }, { k: "bank", l: L2("Bank", "البنك") }, { k: "cheque_due", l: L2("Cheque date", "تاريخ الشيك"), type: "date", v: row ? row.due : "" },
    { k: "ref", l: t("ref") }, { k: "memo", l: t("memo") }]),
  async v => { await api("POST", `/api/sales/${s.id}/collect`, v); toast(t("posted")); },
  { submitLabel: L2("Receive", "تحصيل"), onOpen: f => { S.H.cmeth = () => { const chq = $("[name=method]", f).value === "Cheque"; showFields(f, chq ? ["postdated", ...($("[name=postdated]", f).checked ? ["cheque_no", "bank", "cheque_due"] : ["cheque_no"])] : [], ["postdated", "cheque_no", "bank", "cheque_due"]); }; S.H.cmeth(); } });
}
function chequesForm(s) {
  const open = s.schedule.filter(r => r.left > 0);
  let rows = open.map((r, i) => ({ due: r.due, amount: r.left, number: "", bank: "", on: true, label: instLabel(r) }));
  const body = () => rows.map((r, i) => `<tr><td><input type="checkbox" data-skip="1" data-change="chq" data-i="${i}" data-k="on" ${r.on ? "checked" : ""}></td><td>${esc(r.label)}</td><td><input data-skip="1" type="date" value="${esc(r.due)}" data-input="chq" data-i="${i}" data-k="due"></td>
    <td><input data-skip="1" class="num" type="number" step="any" value="${r.amount}" data-input="chq" data-i="${i}" data-k="amount" style="width:110px"></td><td><input data-skip="1" value="${esc(r.number)}" data-input="chq" data-i="${i}" data-k="number" style="width:100px"></td><td><input data-skip="1" value="${esc(r.bank)}" data-input="chq" data-i="${i}" data-k="bank" style="width:100px"></td></tr>`).join("");
  drawer(L2("Receive post-dated cheques", "استلام شيكات آجلة") + " — " + s.code, form([
    { k: "date", l: L2("Received on", "تاريخ الاستلام"), type: "date", v: today(), req: 1 }, { k: "bank", l: L2("Bank (all cheques)", "البنك (لكل الشيكات)") },
    { k: "first_no", l: L2("First cheque number (numbers the rest)", "رقم أول شيك (يرقم الباقي)"), type: "number" },
    { html: `<div class="tbl"><table id="chqt"><thead><tr><th></th><th>${L2("Payment", "الدفعة")}</th><th>${L2("Cheque date", "تاريخ الشيك")}</th><th>${t("amount")}</th><th>${L2("No.", "رقم")}</th><th>${L2("Bank", "البنك")}</th></tr></thead><tbody>${body()}</tbody></table></div>
      <p class="muted">${L2("Each cheque sits in Notes Receivable until you mark it cleared (money in the bank) or bounced (back on the customer's balance).", "كل شيك يُسجل في أوراق القبض حتى تحدده كـ «تم الصرف» (دخل البنك) أو «مرتد» (يعود على رصيد العميل).")}</p>`, span: "spanall" }]),
  async v => { const cheques = rows.filter(r => r.on && +r.amount > 0).map(r => ({ due: r.due, amount: r.amount, number: r.number, bank: r.bank || v.bank }));
    if (!cheques.length) throw new Error(L2("Tick at least one cheque", "حدد شيكاً واحداً على الأقل"));
    await api("POST", `/api/sales/${s.id}/collect`, { date: v.date, method: "Cheque", cheques }); toast(`${cheques.length} ${L2("cheques recorded", "شيك مسجل")}`); },
  { wide: true, submitLabel: L2("Record cheques", "تسجيل الشيكات"), onOpen: f => {
    S.H.chq = el => { rows[+el.dataset.i][el.dataset.k] = el.type === "checkbox" ? el.checked : el.value; };
    $("[name=first_no]", f).addEventListener("input", e => { const n = parseInt(e.target.value, 10); let k = 0; rows.forEach(r => { if (r.on) r.number = isNaN(n) ? "" : String(n + k++); }); $("#chqt tbody", f).innerHTML = body(); });
    $("[name=bank]", f).addEventListener("input", e => { rows.forEach(r => { r.bank = e.target.value; }); $("#chqt tbody", f).innerHTML = body(); }); } });
}
function chequeAction(id, action) {
  drawer(action === "clear" ? L2("Cheque cleared", "تم صرف الشيك") : L2("Cheque bounced", "شيك مرتد"), form([{ k: "date", l: t("date"), type: "date", v: today(), req: 1 },
    ...(action === "clear" ? [{ k: "received_into", l: L2("Into account", "إلى حساب"), type: "select", opts: accts(["Asset"], ["cash"]), v: accByRole("bank") }] : [
      { html: `<p class="muted">${L2("The amount goes back onto the customer's balance and the installment shows as unpaid again.", "يعود المبلغ على رصيد العميل ويظهر القسط غير مسدد.")}</p>`, span: "spanall" }])]),
  async v => { await api("POST", `/api/sale-cheques/${id}/${action}`, v); toast(t("posted")); }, { submitLabel: t("save") });
}
function refundForm(s) {
  drawer(L2("Refund", "رد مبلغ") + " — " + s.code, form([{ k: "date", l: t("date"), type: "date", v: today(), req: 1 },
    { k: "amount", l: L2("Paid back to the customer", "المبلغ المردود للعميل"), type: "number", v: s.paid },
    { k: "keep", l: L2("Kept (cancellation charge — other income)", "المحتجز (غرامة إلغاء — إيرادات أخرى)"), type: "number", v: "" },
    { k: "method", l: t("method"), type: "select", opts: methodOpts() }, { k: "memo", l: t("memo"), span: "spanall" },
    { html: `<p class="muted">${L2("Paid so far", "المسدد حتى الآن")}: <b>${money(s.paid)}</b>. ${L2("To cancel, refund (or keep) everything paid, then press Cancel sale.", "للإلغاء: رد (أو احتجز) كل المسدد ثم اضغط إلغاء البيع.")}</p>`, span: "spanall" }]),
  async v => { await api("POST", `/api/sales/${s.id}/refund`, v); toast(t("posted")); }, { submitLabel: L2("Refund", "رد") });
}

PAGES.installments = async (view, parts, q) => {
  const lq = { status: "open", ...q };
  const d = await api("GET", "/api/installments?" + qs(lq));
  const coll = can("shop.installments", "add") || can("shop.orders", "add");
  const T = d.totals;
  const msg = r => encodeURIComponent(S.lang === "ar" ? `السيد/ة ${r.customer}، نذكركم بالقسط المستحق ${fd(r.due)} بقيمة ${money(r.left)} — ${r.code}. شكراً لكم.` : `Dear ${r.customer}, a reminder of the installment of ${money(r.left)} due ${fd(r.due)} on ${r.code}. Thank you.`);
  view.innerHTML = `<div class="toolbar"><a class="btn" href="#/sales">${t("m_sales_orders")}</a><span class="spacer"></span>${exportMenu("installments", lq)}</div>` +
    filterBar(lq, ["q", "dates", ["select", "status", t("status"), [["open", L2("Not paid yet", "غير مسدد")], ["overdue", L2("Overdue", "متأخر")], ["due", L2("Due", "مستحق")], ["partial", L2("Part paid", "جزئي")], ["cheque", L2("By cheque", "بشيك")], ["paid", L2("Paid", "مسدد")], ["all", t("all")]]]]) +
    `<div class="kpis">${kpiBox(L2("Installments listed", "الأقساط المعروضة"), T.amount, "", `${d.rows.length}`)}${kpiBox(L2("Still owed", "المتبقي"), T.left)}${kpiBox(L2("Overdue", "متأخرات"), T.overdue, T.overdue > 0 ? "neg" : "")}${kpiBox(L2("Covered by cheques", "مغطى بشيكات"), T.by_cheque)}</div>
    ${d.cheques_due.length ? `<div class="card"><h2>${L2("Cheques due — deposit them", "شيكات مستحقة — قم بإيداعها")}</h2><div class="tbl stack"><table><tbody>${d.cheques_due.map(c => `<tr><td>${fd(c.due)}</td><td><a href="#/sale/${c.sale_id}">${esc(c.code)}</a> · ${esc(c.customer)}</td><td>${esc(c.number)} ${esc(c.bank)}</td><td class="num">${money(c.amount)}</td>
      <td>${coll ? `<button class="btn sm" data-a="chclr" data-id="${c.id}">${L2("Cleared", "تم الصرف")}</button><button class="btn sm danger" data-a="chbnc" data-id="${c.id}">${L2("Bounced", "مرتد")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
    <div class="card">${d.rows.length ? `<div class="tbl stack"><table><thead><tr><th>${L2("Due", "الاستحقاق")}</th><th>${L2("Sale", "البيع")}</th><th>${L2("Customer", "العميل")}</th><th>${L2("Payment", "الدفعة")}</th><th class="num">${t("amount")}</th><th class="num">${L2("Left", "المتبقي")}</th><th>${t("status")}</th><th></th></tr></thead><tbody>
      ${d.rows.filter(r => !q.q || (r.customer + r.code).toLowerCase().includes(q.q.toLowerCase())).map(r => { const wa = waNumber(r.whatsapp || r.phone);
        return `<tr><td>${fd(r.due)}</td><td><a href="#/sale/${r.sale_id}">${esc(r.code)}</a></td><td>${esc(r.customer)}${r.phone ? `<div class="lines"><a href="tel:${esc(r.phone)}">${esc(r.phone)}</a></div>` : ""}</td><td>${esc(instLabel(r))}</td><td class="num">${money(r.amount)}</td><td class="num">${r.left ? money(r.left) : ""}</td><td>${iStatus(r.status)}${r.days_late ? ` <span class="muted">${r.days_late} ${L2("days", "يوم")}</span>` : ""}</td>
        <td>${r.left > 0 && coll ? `<button class="btn sm" data-a="ipay" data-sid="${r.sale_id}" data-id="${r.id}">${L2("Collect", "تحصيل")}</button>` : ""}${r.left > 0 && wa ? `<a class="btn sm" href="https://wa.me/${wa}?text=${msg(r)}" target="_blank" rel="noopener">WhatsApp</a>` : ""}</td></tr>`; }).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("Nothing here.", "لا يوجد.")}</div>`}</div>`;
  Object.assign(S.H, { ipay: async el => { const s = await api("GET", "/api/sales/" + el.dataset.sid); collectForm(s, s.schedule.find(r => String(r.id) === el.dataset.id)); },
    chclr: el => chequeAction(el.dataset.id, "clear"), chbnc: el => chequeAction(el.dataset.id, "bounce") });
};
async function pickSale(fn) {
  const d = await api("GET", "/api/sales?status=confirmed");
  const d2 = await api("GET", "/api/sales?status=delivered");
  const rows = [...d.rows, ...d2.rows].filter(r => r.uncovered > 0);
  if (!rows.length) { toast(L2("No open sales with money to collect", "لا توجد مبيعات بها مبالغ للتحصيل")); return; }
  drawer(L2("Which sale?", "أي بيع؟"), form([{ k: "pick", l: t("m_sales_orders"), type: "select", span: "spanall", opts: rows.map(r => [r.id, `${r.code} — ${r.customer} · ${money(r.balance)}${r.next_due ? ` · ${fd(r.next_due)}` : ""}`]) }]),
    async v => { const s = await api("GET", "/api/sales/" + v.pick); fn(s); return false; }, { submitLabel: L2("Continue", "متابعة") });
}

// ================================================================ bank reconciliation
const bankAccts = () => accts(["Asset"], ["cash"]);
const BL_ST = { open: ["Open", "مفتوح", "warn"], matched: ["Matched", "مطابق", "ok"], ignored: ["Ignored", "متجاهل", ""] };
const blStatus = s => { const x = BL_ST[s] || [s, s, ""]; return `<span class="chip ${x[2]}">${esc(L2(x[0], x[1]))}</span>`; };
PAGES.bankrec = async (view) => {
  const d = await api("GET", "/api/bank");
  const add = can("accounting.bankrec", "add");
  Object.assign(S.H, { brimp: el => bankImportForm(el && el.dataset.acc), brpaper: el => bankPaperForm(el && el.dataset.acc, d), brrules: () => bankRulesForm(d.rules),
    brgo: el => { location.hash = "#/bankstmt/" + el.dataset.id; } });
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="brimp">⇪ ${L2("Import bank statement", "استيراد كشف حساب البنك")}</button><button class="btn" data-a="brpaper">${L2("Reconcile from a paper statement", "تسوية من كشف ورقي")}</button>` : ""}
      <button class="btn" data-a="brrules">${L2("Bank rules", "قواعد البنك")} (${d.rules.length})</button></div>
    <div class="cgrid">${d.accounts.map(a => `<div class="card"><b>${esc(a.code)} — ${esc(aname(a))}</b>
      <div class="kpis" style="margin:8px 0 6px">${kpiBox(L2("Book balance", "الرصيد الدفتري"), a.book)}</div>
      <div class="muted">${a.reconciled_to ? `${L2("Reconciled to", "تمت التسوية حتى")} <b>${fd(a.reconciled_to)}</b>` : L2("Never reconciled", "لم تتم تسوية بعد")}${a.open ? ` · ${a.open} ${L2("open", "مفتوحة")}` : ""}</div>
      ${add ? `<div class="row" style="gap:6px;margin-top:8px"><button class="btn sm" data-a="brimp" data-acc="${esc(a.code)}">⇪ ${L2("Import", "استيراد")}</button><button class="btn sm" data-a="brpaper" data-acc="${esc(a.code)}">${L2("Paper statement", "كشف ورقي")}</button></div>` : ""}</div>`).join("")}</div>
    <div class="card"><h2>${L2("Statements", "الكشوف")}</h2>${d.statements.length ? `<div class="tbl stack"><table><thead><tr><th>${t("account")}</th><th>${L2("Period", "الفترة")}</th><th>${L2("Name", "الاسم")}</th><th class="num">${L2("Opening", "الافتتاحي")}</th><th class="num">${L2("Closing", "الختامي")}</th><th class="num">${L2("Lines", "الحركات")}</th><th>${t("status")}</th></tr></thead><tbody>
      ${d.statements.map(s => `<tr class="click" data-a="brgo" data-id="${s.id}"><td>${esc(s.account)}</td><td>${fd(s.date_from)} → ${fd(s.date_to)}</td><td>${esc(s.name)}</td><td class="num">${money(s.opening)}</td><td class="num">${money(s.closing)}</td><td class="num">${s.n}${s.open_lines ? ` <span class="chip warn">${s.open_lines} ${L2("open", "مفتوح")}</span>` : ""}</td>
        <td>${s.status === "reconciled" ? `<span class="chip ok">✓ ${L2("Reconciled", "تمت التسوية")} ${fd(s.reconciled_on)}</span>` : `<span class="chip">${L2("In progress", "جارية")}</span>`}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("Download your statement from the bank's website (CSV or Excel) and import it — the app matches each bank line to your vouchers, books the bank charges and interest, and shows what is still in transit. No file? Use a paper statement and tick the vouchers the bank shows.", "حمّل كشف الحساب من موقع البنك (CSV أو Excel) واستورده — سيطابق البرنامج كل حركة بنكية مع قيودك، ويسجل العمولات والفوائد، ويعرض ما زال معلقاً. لا يوجد ملف؟ استخدم الكشف الورقي وحدد القيود الظاهرة به.")}</div>`}</div>`;
};

function bankImportForm(account) {
  let parsed = null;
  drawer(L2("Import bank statement", "استيراد كشف حساب البنك"), form([
    { k: "account", l: L2("Bank / cash account", "حساب البنك / النقدية"), type: "select", opts: bankAccts(), v: account || accByRole("bank"), req: 1 },
    { k: "date_order", l: L2("Dates are written", "صيغة التاريخ"), type: "select", opts: [["", L2("Detect", "تلقائي")], ["dmy", "31/12/2026"], ["mdy", "12/31/2026"]] },
    { html: `<label class="f">${L2("Statement file (CSV or Excel)", "ملف الكشف (CSV أو Excel)")}<input type="file" id="brfile" data-skip="1" accept=".csv,.txt,.xlsx,.xlsm,text/csv"></label>`, span: "spanall" },
    { k: "paste", l: L2("…or paste the lines (copied from Excel / the bank's page, with the heading row)", "…أو الصق الحركات (منسوخة من Excel أو صفحة البنك مع صف العناوين)"), type: "textarea", span: "spanall" },
    { html: `<div id="brprev"></div>`, span: "spanall" }]),
  async v => { if (!parsed) { await read(); return false; }
    const r = await api("POST", "/api/bank-statements", { account: v.account, name: v.name, date_from: v.date_from, date_to: v.date_to, opening: v.opening, closing: v.closing, book_from: v.book_from, lines: parsed.rows });
    try { const m = await api("POST", `/api/bank-statements/${r.id}/automatch`, {}); toast(`${m.matched} ${L2("lines matched automatically", "حركة طوبقت تلقائياً")}`); } catch (e) { fail(e); }
    location.hash = "#/bankstmt/" + r.id; closeDrawer(); return false; },
  { wide: true, submitLabel: L2("Read the file", "قراءة الملف"), onOpen: f => {
    const read = async () => { const box = $("#brprev", f), file = $("#brfile", f).files[0], v = readForm(f);
      try { const body = file ? { data: await readDataUrl(file), filename: file.name } : { text: v.paste };
        if (!file && !(v.paste || "").trim()) throw new Error(L2("Choose the file or paste the lines", "اختر الملف أو الصق الحركات"));
        parsed = await api("POST", "/api/bank-preview/parse", { ...body, date_order: v.date_order });
        const p = parsed;
        box.innerHTML = `<div class="banner ok">${p.rows.length} ${L2("lines read", "حركة")}${p.skipped ? ` · ${p.skipped} ${L2("rows skipped (no date / amount)", "صف تم تجاهله (بدون تاريخ / مبلغ)")}` : ""} · ${L2("in", "وارد")} ${money(p.total_in)} · ${L2("out", "منصرف")} ${money(p.total_out)}</div>` +
          form([{ k: "name", l: L2("Statement name", "اسم الكشف"), v: `${v.account} ${p.date_from.slice(0, 7)}` }, { k: "date_from", l: t("from"), type: "date", v: p.date_from }, { k: "date_to", l: t("to"), type: "date", v: p.date_to },
            { k: "opening", l: L2("Opening balance (per bank)", "الرصيد الافتتاحي (حسب البنك)"), type: "number", v: p.opening ?? "" }, { k: "closing", l: L2("Closing balance (per bank)", "الرصيد الختامي (حسب البنك)"), type: "number", v: p.closing ?? "" },
            { k: "book_from", l: L2("Look at vouchers from", "مراجعة القيود من"), type: "date", v: p.date_from, help: L2("First reconciliation: older vouchers are treated as already cleared", "أول تسوية: القيود الأقدم تعتبر مسواة") }], 3) +
          `<div class="tbl" style="max-height:300px;overflow:auto"><table><thead><tr><th>${t("date")}</th><th>${L2("Description", "البيان")}</th><th>${t("ref")}</th><th class="num">${t("amount")}</th><th class="num">${L2("Balance", "الرصيد")}</th></tr></thead><tbody>
          ${p.rows.slice(0, 200).map(r => `<tr><td>${fd(r.date)}</td><td>${esc(r.description)}</td><td>${esc(r.ref)}</td><td class="num ${r.amount < 0 ? "neg" : "pos"}">${money(r.amount)}</td><td class="num">${r.balance != null ? money(r.balance) : ""}</td></tr>`).join("")}</tbody></table></div>`;
        $("button[type=submit]", f).textContent = L2("Import & match", "استيراد ومطابقة");
      } catch (e) { parsed = null; box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; } };
    S.H.brread = read; $("#brfile", f).addEventListener("change", read); } });
  async function read() { return S.H.brread(); }
}
function bankPaperForm(account, d) {
  const acc0 = account || accByRole("bank");
  const prev = (d.statements || []).filter(s => s.account === acc0).sort((a, b) => b.date_to.localeCompare(a.date_to))[0];
  drawer(L2("Reconcile from a paper statement", "تسوية من كشف ورقي"), form([
    { k: "account", l: L2("Bank / cash account", "حساب البنك / النقدية"), type: "select", opts: bankAccts(), v: acc0, req: 1 },
    { k: "date_from", l: t("from"), type: "date", v: prev ? addDays(prev.date_to, 1) : today().slice(0, 8) + "01", req: 1 }, { k: "date_to", l: t("to"), type: "date", v: today(), req: 1 },
    { k: "opening", l: L2("Opening balance (per bank)", "الرصيد الافتتاحي (حسب البنك)"), type: "number", v: prev ? prev.closing : "" },
    { k: "closing", l: L2("Closing balance (per bank)", "الرصيد الختامي (حسب البنك)"), type: "number", req: 1 },
    { html: `<p class="muted">${L2("Next you tick each voucher that appears on the statement and add the bank's own lines (charges, interest).", "بعد ذلك حدد كل قيد ظاهر في الكشف وأضف حركات البنك الخاصة (عمولات، فوائد).")}</p>`, span: "spanall" }]),
  async v => { const r = await api("POST", "/api/bank-statements", { ...v, name: `${v.account} ${v.date_to.slice(0, 7)}` }); location.hash = "#/bankstmt/" + r.id; closeDrawer(); return false; });
}
const addDays = (d, n) => { const x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };

PAGES.bankstmt = async (view, parts) => {
  const d = await api("GET", "/api/bank-statements/" + parts[1]);
  const st = d.statement, sm = d.summary, locked = st.status === "reconciled";
  const add = can("accounting.bankrec", "add") && !locked, ed = can("accounting.bankrec", "edit");
  const paper = !d.lines.some(l => !l.ticked);
  let sel = null; const picked = new Set();
  const act = (a, b = {}) => api("POST", `/api/bank-statements/${st.id}/${a}`, b);
  const run = async (a, b, msg) => { try { const r = await act(a, b); if (msg) toast(msg(r)); route(); } catch (e) { fail(e); } };
  const ok0 = Math.abs(sm.difference) < 0.005;
  const bookRow = (b) => `<tr class="bk" data-id="${b.id}" data-amt="${b.amount}"><td>${add ? `<input type="checkbox" data-change="brpick" data-id="${b.id}" ${picked.has(b.id) ? "checked" : ""}>` : ""}</td><td>${fd(b.date)}</td>
    <td><a href="#" data-a="openv" data-id="${b.id}">#${b.id}</a> ${esc(tl(b.kind))}<div class="lines">${esc([b.party, b.memo].filter(Boolean).join(" · "))}</div></td><td class="num ${b.amount < 0 ? "neg" : "pos"}">${money(b.amount)}</td></tr>`;
  view.innerHTML = `<div class="toolbar"><a class="btn" href="#/bankrec">← ${t("m_bankrec")}</a>
      ${add && !paper ? `<button class="btn pri" data-a="brauto">${L2("Auto-match", "مطابقة تلقائية")}</button><button class="btn" data-a="brrule">${L2("Apply bank rules", "تطبيق قواعد البنك")}</button>` : ""}
      ${add ? `<button class="btn" data-a="brline">+ ${L2("Bank line", "حركة بنكية")}</button>` : ""}
      <a class="btn" href="/api/reports/bank_reconciliation?id=${st.id}&format=pdf&lang=${S.lang}" target="_blank" rel="noopener">⎙ ${L2("Reconciliation statement", "مذكرة التسوية")}</a>
      <span class="spacer"></span>${!locked && can("accounting.bankrec", "add") ? `<button class="btn ${ok0 && !sm.open_lines ? "pri" : ""}" data-a="brdone" ${ok0 && !sm.open_lines ? "" : "disabled"}>✓ ${L2("Mark reconciled", "اعتماد التسوية")}</button>` : ""}
      ${locked && ed ? `<button class="btn" data-a="brreopen">${L2("Reopen", "إعادة فتح")}</button>` : ""}${!locked && ed ? `<button class="btn" data-a="bredit">${t("edit")}</button><button class="btn danger" data-a="brdel">${t("delete")}</button>` : ""}</div>
    <div class="muted" style="margin-bottom:8px">${esc(d.account.code)} — ${esc(aname(d.account))} · ${fd(st.date_from)} → ${fd(st.date_to)} · ${esc(st.name)} ${locked ? `<span class="chip ok">✓ ${L2("Reconciled", "تمت التسوية")} ${fd(st.reconciled_on)}</span>` : ""}</div>
    ${sm.warnings.map(w => `<div class="banner warn">${esc(w)}</div>`).join("")}
    <div class="kpis">${kpiBox(L2("Bank closing balance", "رصيد البنك الختامي"), sm.closing)}${kpiBox(L2("+ Deposits in transit", "+ إيداعات بالطريق"), sm.deposits_in_transit)}${kpiBox(L2("− Outstanding payments", "− مدفوعات معلقة"), sm.outstanding_payments)}
      ${kpiBox(L2("Book balance", "الرصيد الدفتري"), sm.book)}${kpiBox(L2("Bank items not in books", "حركات لم تُسجل"), sm.unrecorded)}${kpiBox(L2("Difference", "الفرق"), sm.difference, ok0 ? "pos" : "neg", ok0 ? "✓" : "")}</div>
    <div class="recon">
      <div class="card"><h2>${L2("Bank statement", "كشف البنك")} <span class="muted" style="font-weight:400">(${d.lines.length})</span></h2>
        <div class="row" style="gap:6px;margin-bottom:6px"><label class="chk"><input type="checkbox" id="bronly" ${S.cache.bronly !== false ? "checked" : ""}> ${L2("Open lines only", "المفتوحة فقط")}</label></div>
        <div class="tbl"><table id="brlines"><tbody>${d.lines.map(l => `<tr class="bl ${l.status}" data-id="${l.id}" data-amt="${l.amount}"><td>${fd(l.date)}</td>
          <td>${esc(l.description)}${l.ref ? ` <span class="muted">${esc(l.ref)}</span>` : ""}${l.entries.length ? `<div class="lines">↔ ${l.entries.map(e => `<a href="#" data-a="openv" data-id="${e.id}">#${e.id}</a> ${esc(e.memo || tl(e.kind))}`).join(" · ")}</div>` : ""}${l.rule ? `<div class="lines">⚙ ${L2("rule", "قاعدة")}: ${esc(l.rule.pattern)} → ${esc(l.rule.account)}</div>` : ""}</td>
          <td class="num ${l.amount < 0 ? "neg" : "pos"}">${money(l.amount)}</td><td>${blStatus(l.status)}${add ? (l.status === "open" ? `<div class="row" style="gap:4px;margin-top:4px"><button class="btn sm" data-a="brsel" data-id="${l.id}">${L2("Match", "مطابقة")}</button><button class="btn sm" data-a="brbook" data-id="${l.id}">${L2("Book it", "تسجيل")}</button><button class="btn sm" data-a="brign" data-id="${l.id}">${L2("Ignore", "تجاهل")}</button></div>`
            : `<div style="margin-top:4px"><button class="btn sm" data-a="brun" data-id="${l.id}">${l.status === "ignored" ? L2("Restore", "استرجاع") : L2("Unmatch", "إلغاء المطابقة")}</button></div>`) : ""}</td></tr>`).join("") || `<tr><td class="empty">${paper ? L2("Tick the vouchers on the right that appear on the statement; add the bank's own lines with + Bank line.", "حدد القيود الظاهرة في الكشف من اليمين؛ وأضف حركات البنك الخاصة بزر + حركة بنكية.") : ""}</td></tr>`}</tbody></table></div></div>
      <div class="card"><h2>${L2("Vouchers not yet cleared", "قيود لم تُسوَّ بعد")} <span class="muted" style="font-weight:400">(${d.book.length})</span></h2>
        <div id="brbar" class="banner info" style="display:none"></div>
        <div class="tbl"><table id="brbook"><tbody>${d.book.map(bookRow).join("") || `<tr><td class="empty">${L2("All vouchers are matched.", "كل القيود مطابقة.")}</td></tr>`}</tbody></table></div>
        ${paper && add && d.book.length ? `<button class="btn" data-a="brtick" style="margin-top:8px">✓ ${L2("Tick selected as cleared", "تحديد المختار كمسوّى")}</button>` : ""}</div></div>`;
  const only = () => { const on = $("#bronly", view).checked; S.cache.bronly = on; $$("#brlines tr.bl", view).forEach(tr => { tr.style.display = on && !tr.classList.contains("open") && !paper ? "none" : ""; }); };
  $("#bronly", view).addEventListener("change", only); only();
  const bar = () => { const box = $("#brbar", view); const tot = [...picked].reduce((a, id) => a + (+($(`tr.bk[data-id="${id}"]`, view) || { dataset: { amt: 0 } }).dataset.amt), 0);
    if (!sel && !picked.size) { box.style.display = "none"; return; }
    const ln = sel && d.lines.find(l => l.id === sel);
    box.style.display = ""; box.innerHTML = `${ln ? `${L2("Bank line", "حركة البنك")}: <b>${money(ln.amount)}</b> · ` : ""}${L2("Selected vouchers", "القيود المختارة")}: <b>${money(tot)}</b> (${picked.size})
      ${ln && picked.size ? (Math.abs(tot - ln.amount) < 0.005 ? ` <button class="btn sm pri" data-a="brmatch">${L2("Match", "مطابقة")}</button>` : ` <span class="neg">${L2("difference", "فرق")} ${money(ln.amount - tot)}</span>`) : ""} <button class="btn sm" data-a="brclear">✕</button>`; };
  const highlight = () => { $$("tr.bl", view).forEach(tr => tr.classList.toggle("sel", +tr.dataset.id === sel));
    const ln = sel && d.lines.find(l => l.id === sel);
    const rows = $$("#brbook tr.bk", view);
    rows.forEach(tr => tr.classList.toggle("hit", !!ln && Math.abs(+tr.dataset.amt - ln.amount) < 0.005));
    if (ln) { const tb = $("#brbook tbody", view); rows.sort((a, b) => (Math.abs(+a.dataset.amt - ln.amount) < 0.005 ? 0 : 1) - (Math.abs(+b.dataset.amt - ln.amount) < 0.005 ? 0 : 1)).forEach(tr => tb.appendChild(tr)); }
    bar(); };
  Object.assign(S.H, {
    brsel: el => { sel = +el.dataset.id; picked.clear(); $$("#brbook input[type=checkbox]", view).forEach(c => { c.checked = false; }); highlight();
      const hit = $("#brbook tr.hit input", view); if (hit && $$("#brbook tr.hit", view).length === 1) { hit.checked = true; picked.add(+hit.dataset.id); bar(); }
      const card = $("#brbook", view); if (card && matchMedia("(max-width:900px)").matches) card.scrollIntoView({ behavior: "smooth", block: "start" }); },
    brpick: el => { const id = +el.dataset.id; if (el.checked) picked.add(id); else picked.delete(id); bar(); },
    brclear: () => { sel = null; picked.clear(); $$("#brbook input[type=checkbox]", view).forEach(c => { c.checked = false; }); highlight(); },
    brmatch: () => run("match", { line_id: sel, entries: [...picked] }, () => L2("Matched", "تمت المطابقة")),
    brun: el => { const l = d.lines.find(x => x.id === +el.dataset.id); run(l.status === "ignored" ? "ignore" : "unmatch", { line_id: l.id, on: false }); },
    brign: el => run("ignore", { line_id: +el.dataset.id, on: true }),
    brbook: el => bankEntryForm(st, d.lines.find(x => x.id === +el.dataset.id)),
    brauto: () => run("automatch", {}, r => `${r.matched} ${L2("lines matched", "حركة طوبقت")}`),
    brrule: () => run("rules", {}, r => `${r.created} ${L2("vouchers booked from rules", "قيد سُجل من القواعد")}`),
    brtick: () => run("tick", { entries: [...picked], on: true }, () => L2("Ticked", "تم التحديد")),
    brline: () => bankLineForm(st),
    brdone: () => run("reconcile", { date: today() }, () => L2("Reconciled", "تمت التسوية")),
    brreopen: () => run("reopen", {}),
    bredit: () => drawer(t("edit"), form([{ k: "name", l: L2("Name", "الاسم"), v: st.name }, { k: "date_from", l: t("from"), type: "date", v: st.date_from }, { k: "date_to", l: t("to"), type: "date", v: st.date_to },
      { k: "opening", l: L2("Opening balance", "الرصيد الافتتاحي"), type: "number", v: st.opening }, { k: "closing", l: L2("Closing balance", "الرصيد الختامي"), type: "number", v: st.closing },
      { k: "book_from", l: L2("Look at vouchers from", "مراجعة القيود من"), type: "date", v: st.book_from }]), async v => { await api("PUT", "/api/bank-statements/" + st.id, v); toast(t("saved")); }),
    brdel: () => confirmDelete(st.name, "/api/bank-statements/" + st.id, async () => { location.hash = "#/bankrec"; }) });
};
function bankEntryForm(st, ln) {
  const out = ln.amount < 0;
  const words = (ln.description || "").replace(/[0-9/.\-:]+/g, " ").trim().split(/\s+/).slice(0, 3).join(" ");
  drawer(L2("Book this bank line", "تسجيل هذه الحركة") + ` — ${money(ln.amount)}`, form([
    { html: `<p class="muted" style="margin:0">${fd(ln.date)} · ${esc(ln.description)} ${esc(ln.ref || "")}</p>`, span: "spanall" },
    { k: "account", l: out ? L2("Paid for (debit)", "مدفوع مقابل (مدين)") : L2("Received for (credit)", "مقبوض مقابل (دائن)"), type: "select", span: "spanall",
      opts: S.boot.accounts.filter(a => a.active && a.code !== st.account).map(a => [a.code, `${a.code} — ${aname(a)}`]), v: (ln.rule && ln.rule.account) || accByRole(out ? "bank_charges" : "interest_income") },
    { k: "party", l: t("party"), list: "brparty" }, { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—") },
    ...(out ? [{ k: "vat_code", l: L2("VAT included (bank fees)", "شامل ضريبة (عمولات)"), type: "select", opts: taxOpts("vat") }] : []),
    { k: "memo", l: t("memo"), v: ln.description, span: "spanall" },
    { k: "remember", l: L2("Always book lines containing this text the same way (bank rule)", "سجّل دائماً الحركات التي تحتوي هذا النص بنفس الطريقة (قاعدة)"), type: "checkbox", change: "brrem", span: "spanall" },
    { k: "pattern", l: L2("Text to look for", "النص المطلوب"), v: words }]) + partyList("brparty"),
  async v => { await api("POST", `/api/bank-statements/${st.id}/create`, { ...v, line_id: ln.id }); toast(t("posted")); },
  { submitLabel: L2("Book & match", "تسجيل ومطابقة"), onOpen: f => { S.H.brrem = () => showFields(f, $("[name=remember]", f).checked ? ["pattern"] : [], ["pattern"]); S.H.brrem(); } });
}
function bankLineForm(st) {
  drawer(L2("Add a bank line", "إضافة حركة بنكية"), form([{ k: "date", l: t("date"), type: "date", v: st.date_to, req: 1 }, { k: "description", l: L2("Description", "البيان"), req: 1 },
    { k: "ref", l: t("ref") }, { k: "amount", l: L2("Amount (+ in / − out)", "المبلغ (+ وارد / − منصرف)"), type: "number", req: 1, step: "any" }]),
  async v => { await api("POST", `/api/bank-statements/${st.id}/lines`, { lines: [v] }); toast(t("saved")); });
}
function bankRulesForm(rules) {
  const edit = r => drawer(r.id ? t("edit") : L2("New bank rule", "قاعدة جديدة"), form([
    { k: "pattern", l: L2("When the bank's description contains", "عندما يحتوي بيان البنك على"), v: r.pattern, req: 1, span: "spanall" },
    { k: "direction", l: L2("For", "لـ"), type: "select", opts: [["any", L2("money in or out", "وارد أو منصرف")], ["out", L2("money out", "منصرف")], ["in", L2("money in", "وارد")]], v: r.direction || "out" },
    { k: "account", l: L2("Book it to", "سجّل على"), type: "select", opts: S.boot.accounts.filter(a => a.active).map(a => [a.code, `${a.code} — ${aname(a)}`]), v: r.account || accByRole("bank_charges") },
    { k: "party", l: t("party"), v: r.party, list: "brparty" }, { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat"), v: r.vat_code },
    { k: "memo", l: t("memo"), v: r.memo }, { k: "active", l: t("active"), type: "checkbox", v: r.active !== 0 }]) + partyList("brparty"),
  async v => { if (r.id) await api("PUT", "/api/bank-rules/" + r.id, v); else await api("POST", "/api/bank-rules", v); toast(t("saved")); },
  { extraButtons: r.id && can("accounting.bankrec", "edit") ? `<button type="button" class="btn danger" data-a="brrdel">${t("delete")}</button>` : "" });
  S.H.brrnew = () => { edit({}); S.H.brrdel = () => {}; };
  S.H.brredit = el => { const r = rules.find(x => x.id === +el.dataset.id); edit(r); S.H.brrdel = () => confirmDelete(r.pattern, "/api/bank-rules/" + r.id); };
  drawer(L2("Bank rules", "قواعد البنك"), `<p class="muted">${L2("Lines the bank repeats every month (fees, SMS charges, interest, standing orders) are booked in one click with a rule.", "الحركات المتكررة كل شهر (عمولات، رسائل SMS، فوائد، أوامر مستديمة) تُسجل بضغطة واحدة بقاعدة.")}</p>
    <div class="tbl"><table><tbody>${rules.map(r => `<tr class="click" data-a="brredit" data-id="${r.id}"><td>“${esc(r.pattern)}”</td><td>${esc(r.direction)}</td><td>→ ${esc(r.account)} ${esc(aname(S.boot.accounts.find(a => a.code === r.account) || {}))}</td><td>${r.active ? "" : `<span class="chip">${L2("off", "متوقفة")}</span>`}</td></tr>`).join("") || `<tr><td class="empty">${t("nothing")}</td></tr>`}</tbody></table></div>`,
  null, { readOnly: true, extraButtons: can("accounting.bankrec", "add") ? `<button type="button" class="btn pri" data-a="brrnew">+ ${L2("Rule", "قاعدة")}</button>` : "" });
}

// ================================================================ recurring entries
const RC_TYPES = () => [["expense", t("m_expenses")], ["direct_cost", t("m_costs")], ["payroll", t("m_payroll")], ["payment", t("m_payments")], ["receipt", t("m_receipts")],
  ["partner_tx", L2("Partner drawing / capital", "مسحوبات / رأس مال شريك")], ["journal", L2("Journal voucher", "قيد يومية")], ["depreciation", L2("Depreciation run (fixed assets)", "احتساب الإهلاك (الأصول الثابتة)")]];
const RC_UNITS = () => [["day", L2("day(s)", "يوم")], ["week", L2("week(s)", "أسبوع")], ["month", L2("month(s)", "شهر")], ["year", L2("year(s)", "سنة")]];
const rcType = k => (RC_TYPES().find(x => x[0] === k) || [k, k])[1];
const RC_UNIT_TXT = { day: [["day", "days"], ["يوم", "أيام"]], week: [["week", "weeks"], ["أسبوع", "أسابيع"]], month: [["month", "months"], ["شهر", "أشهر"]], year: [["year", "years"], ["سنة", "سنوات"]] };
const rcSched = r => { const u = RC_UNIT_TXT[r.unit] || [[r.unit, r.unit], [r.unit, r.unit]], pl = r.every > 1 ? 1 : 0;
  return `${L2("every", "كل")} ${r.every > 1 ? r.every + " " : ""}${S.lang === "ar" ? u[1][pl] : u[0][pl]}${r.month_end ? ` · ${L2("month end", "آخر الشهر")}` : ""}`; };
const fillMonth = (v, due) => typeof v === "string" ? v.replace(/\{month\}/g, due.slice(0, 7)).replace(/\{year\}/g, due.slice(0, 4)).replace(/\{date\}/g, due) : v;

PAGES.recurring = async (view) => {
  const d = await api("GET", "/api/recurring");
  const add = can("accounting.recurring", "add");
  const autoDue = d.due.filter(x => x.mode === "auto" && !x.last_error);
  Object.assign(S.H, { rcnew: () => recurringForm(), rcopen: el => recurringView(+el.dataset.id),
    rcrun: async () => { try { const r = await api("POST", "/api/recurring-run", { through: today() }); toast(`${r.posted} ${L2("posted", "قيد")}${r.errors.length ? ` · ${r.errors.length} ${L2("failed", "فشل")}` : ""}`); route(); } catch (e) { fail(e); } },
    rcpost: el => { const x = d.due[+el.dataset.i]; recurringPostForm(d.rows.find(r => r.id === x.id), x.due); },
    rcskip: async el => { const x = d.due[+el.dataset.i]; if (!confirm(L2(`Skip ${x.name} due ${x.due}?`, `تخطي ${x.name} المستحق ${x.due}؟`))) return; try { await api("POST", `/api/recurring/${x.id}/skip`, { due: x.due }); route(); } catch (e) { fail(e); } } });
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="rcnew">+ ${L2("Recurring entry", "قيد متكرر")}</button>` : ""}${add && autoDue.length ? `<button class="btn" data-a="rcrun">▶ ${L2("Post automatic ones now", "ترحيل التلقائية الآن")} (${autoDue.length})</button>` : ""}</div>
    <div class="card"><h2>${L2("Due now", "المستحق الآن")} <span class="muted" style="font-weight:400">(${d.due.length})</span></h2>${d.due.length ? `<div class="tbl stack"><table><thead><tr><th>${L2("Due", "الاستحقاق")}</th><th>${L2("Entry", "القيد")}</th><th>${L2("Type", "النوع")}</th><th class="num">${t("amount")}</th><th>${L2("Mode", "الطريقة")}</th><th></th></tr></thead><tbody>
      ${d.due.map((x, i) => `<tr><td>${fd(x.due)}</td><td><b>${esc(fillMonth(x.name, x.due))}</b>${x.last_error ? `<div class="neg" style="font-size:.85em">⚠ ${esc(x.last_error)}</div>` : ""}</td><td>${esc(rcType(x.doc_type))}</td><td class="num">${x.amount ? money(x.amount) : "—"}</td>
        <td>${x.mode === "auto" ? `<span class="chip ok">${L2("automatic", "تلقائي")}</span>` : `<span class="chip warn">${L2("review", "مراجعة")}</span>`}</td>
        <td>${add ? `<button class="btn sm pri" data-a="rcpost" data-i="${i}">${L2("Review & post", "مراجعة وترحيل")}</button><button class="btn sm" data-a="rcskip" data-i="${i}">${L2("Skip", "تخطي")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("Nothing due.", "لا يوجد مستحق.")}</div>`}</div>
    <div class="card"><h2>${L2("Recurring entries", "القيود المتكررة")}</h2>${d.rows.length ? `<div class="tbl stack"><table><thead><tr><th>${L2("Name", "الاسم")}</th><th>${L2("Type", "النوع")}</th><th class="num">${t("amount")}</th><th>${L2("Schedule", "التكرار")}</th><th>${L2("Next", "التالي")}</th><th>${L2("Mode", "الطريقة")}</th><th class="num">${L2("Posted", "مرحّل")}</th></tr></thead><tbody>
      ${d.rows.map(r => `<tr class="click ${r.active ? "" : "void"}" data-a="rcopen" data-id="${r.id}"><td><b>${esc(r.name)}</b></td><td>${esc(rcType(r.doc_type))}</td><td class="num">${r.amount ? money(r.amount) : "—"}</td><td>${esc(rcSched(r))}</td><td>${r.next_due ? fd(r.next_due) : "—"}</td>
        <td>${r.mode === "auto" ? L2("automatic", "تلقائي") : L2("review", "مراجعة")}</td><td class="num">${r.posted}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${L2("Rent, salaries, subscriptions, insurance, loan installments, monthly depreciation… set them once and they post themselves on their dates (or wait for you to check the amount). Tip: open any voucher and press “Make recurring”.", "الإيجار، الرواتب، الاشتراكات، التأمين، أقساط القروض، الإهلاك الشهري… أدخلها مرة واحدة وتُرحّل تلقائياً في مواعيدها (أو تنتظر مراجعتك للمبلغ). نصيحة: افتح أي قيد واضغط «اجعله متكرراً».")}</div>`}</div>`;
};

function rcDocFields(type, payload) {
  if (type === "depreciation") return `<p class="muted">${L2("Books the month's depreciation for every fixed asset, by your depreciation settings.", "يسجل إهلاك الشهر لكل الأصول الثابتة حسب إعدادات الإهلاك.")}</p>`;
  if (type === "journal") {
    const lines = (payload.lines && payload.lines.length ? payload.lines : [{}, {}]);
    const opts = S.boot.accounts.filter(a => a.active).map(a => [a.code, `${a.code} — ${aname(a)}`]);
    return form([{ k: "memo", l: t("memo"), v: payload.memo || "", ph: L2("e.g. Accrued rent {month}", "مثال: إيجار مستحق {month}"), span: "spanall" }, { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—"), v: payload.bu_id || "" }, { k: "party", l: t("party"), v: payload.party || "", list: "rcparty" }]) +
      `<div class="tbl"><table id="rcjl"><thead><tr><th>${t("account")}</th><th>${t("debit")}</th><th>${t("credit")}</th></tr></thead><tbody>${lines.map(l => `<tr><td><select data-skip="1" class="rja"><option value="">—</option>${opts.map(([c, n]) => `<option value="${c}" ${c === l.account ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></td>
        <td><input data-skip="1" class="rjd num" type="number" step="any" value="${l.debit || ""}"></td><td><input data-skip="1" class="rjc num" type="number" step="any" value="${l.credit || ""}"></td></tr>`).join("")}</tbody></table></div>
      <button type="button" class="btn sm" data-a="rcjadd" style="margin-top:4px">+ ${L2("Line", "سطر")}</button>`;
  }
  const d = DOCS[type];
  return form(d.fields(payload).filter(x => x.k !== "date").map(x => x.k && payload[x.k] != null ? { ...x, v: payload[x.k] } : x)) + unitsList() + partyList("parties") + datalist("items", S.boot.items.map(i => i.name));
}
function rcReadPayload(f, type) {
  if (type === "depreciation") return {};
  if (type === "journal") {
    const v = readForm($("#rcdoc", f) || f);
    const lines = $$("#rcjl tbody tr", f).map(tr => ({ account: $(".rja", tr).value, debit: $(".rjd", tr).value, credit: $(".rjc", tr).value })).filter(l => l.account && (+l.debit || +l.credit));
    return { memo: v.memo, bu_id: v.bu_id, party: v.party, lines };
  }
  const v = readForm($("#rcdoc", f));
  delete v.date; return v;
}
function rcWireDoc(f, type) {
  const box = $("#rcdoc", f);
  if (type === "journal") { S.H.rcjadd = () => { const tb = $("#rcjl tbody", f), tr = tb.lastElementChild.cloneNode(true); $$("input", tr).forEach(i => { i.value = ""; }); tb.appendChild(tr); }; return; }
  const d = DOCS[type]; if (!d) return;
  if (d.open) try { d.open(box, false); } catch { /* optional helpers */ }
  if (d.calc) { S.H.calc = () => d.calc(box); d.calc(box); }
}
function recurringForm(r, pre = {}) {
  const isNew = !r; r = r || {};
  let type = r.doc_type || pre.doc_type || "expense", payload = r.payload || pre.payload || {};
  drawer(isNew ? L2("New recurring entry", "قيد متكرر جديد") : `${t("edit")} — ${r.name}`, form([
    { k: "rc_name", l: L2("Name", "الاسم"), v: r.name || pre.name || "", req: 1, ph: L2("e.g. Office rent", "مثال: إيجار المكتب") },
    { k: "rc_doc_type", l: L2("Posts a", "يرحّل"), type: "select", opts: RC_TYPES(), v: type, change: "rctype" },
    { k: "rc_every", l: L2("Every", "كل"), type: "number", v: r.every || 1 }, { k: "rc_unit", l: L2("Unit", "الوحدة"), type: "select", opts: RC_UNITS(), v: r.unit || "month" },
    { k: "rc_start", l: L2("First date", "أول تاريخ"), type: "date", v: r.start || pre.start || today(), req: 1 },
    { k: "rc_month_end", l: L2("Always the last day of the month", "دائماً آخر يوم في الشهر"), type: "checkbox", v: !!r.month_end },
    { k: "rc_end_date", l: L2("Until (optional)", "حتى (اختياري)"), type: "date", v: r.end_date || "" }, { k: "rc_max_count", l: L2("…or number of times", "…أو عدد المرات"), type: "number", v: r.max_count || "" },
    { k: "rc_mode", l: L2("When due", "عند الاستحقاق"), type: "select", opts: [["auto", L2("Post it automatically", "رحّله تلقائياً")], ["review", L2("Let me check the amount first", "انتظر مراجعتي للمبلغ")]], v: r.mode || "auto" },
    { k: "rc_active", l: t("active"), type: "checkbox", v: r.active !== 0 },
    { html: `<h4 class="fsec">${L2("The voucher", "القيد")} <span class="muted" style="font-weight:400;text-transform:none">— ${L2("text may use {month}, {year}, {date}", "يمكن استخدام {month} و{year} و{date} في النص")}</span></h4><div id="rcdoc">${rcDocFields(type, payload)}</div>`, span: "spanall" }]) + partyList("rcparty"),
  async v => { const body = { name: v.rc_name, doc_type: type, every: v.rc_every, unit: v.rc_unit, start: v.rc_start, month_end: v.rc_month_end, end_date: v.rc_end_date, max_count: v.rc_max_count,
      mode: v.rc_mode, active: v.rc_active, payload: rcReadPayload($("#df"), type) };
    if (isNew) await api("POST", "/api/recurring", body); else await api("PUT", "/api/recurring/" + r.id, body); toast(t("saved")); },
  { wide: true, onOpen: f => {
    $("#rcdoc", f).querySelectorAll("[name]").forEach(el => { el.dataset.rcdoc = "1"; });
    S.H.rctype = () => { type = $("[name=rc_doc_type]", f).value; payload = {}; $("#rcdoc", f).innerHTML = rcDocFields(type, payload); rcWireDoc(f, type); };
    rcWireDoc(f, type); } });
}
// the drawer's own fields (rc_*) and the voucher's share one form — keep the voucher's out of the template's
async function recurringView(id) {
  const r = await api("GET", "/api/recurring/" + id);
  const ed = can("accounting.recurring", "edit");
  drawer(r.name, `<div class="kpis">${kpiBox(L2("Next", "التالي"), r.next_due ? fd(r.next_due) : "—")}${kpiBox(L2("Schedule", "التكرار"), esc(rcSched(r)))}${kpiBox(L2("Mode", "الطريقة"), r.mode === "auto" ? L2("automatic", "تلقائي") : L2("review", "مراجعة"))}</div>
    ${r.last_error ? `<div class="banner err">⚠ ${esc(r.last_error)} — ${L2("fix the entry and save it to resume", "صحّح القيد واحفظه للاستئناف")}</div>` : ""}
    <h4 class="fsec">${L2("History", "السجل")}</h4><div class="tbl"><table><tbody>${r.history.map(h => `<tr><td>${fd(h.due)}</td><td>${h.skipped ? `<span class="chip">${L2("skipped", "تم التخطي")}</span>` : h.vouchers.map(v => `<a href="#" data-a="openv" data-id="${v.id}">#${v.id}</a> ${esc(v.memo)}`).join("<br>") || `<span class="muted">${L2("nothing to book", "لا يوجد ما يُسجل")}</span>`}</td></tr>`).join("") || `<tr><td class="empty">${L2("Not posted yet.", "لم يُرحّل بعد.")}</td></tr>`}</tbody></table></div>`,
  null, { readOnly: true, extraButtons: (ed ? `<button type="button" class="btn danger" data-a="rcdel">${t("delete")}</button><button type="button" class="btn pri" data-a="rcedit">${t("edit")}</button>` : "") });
  S.H.rcedit = () => recurringForm(r);
  S.H.rcdel = () => confirmDelete(r.name, "/api/recurring/" + r.id);
}
function recurringPostForm(r, due) {
  const payload = Object.fromEntries(Object.entries(r.payload || {}).map(([k, v]) => [k, Array.isArray(v) ? v : fillMonth(v, due)]));
  drawer(`${fillMonth(r.name, due)} — ${fd(due)}`, `<p class="muted">${L2("Check or change the amounts for this date only; the template stays as it is.", "راجع أو عدّل المبالغ لهذا التاريخ فقط؛ ويبقى القالب كما هو.")}</p><div id="rcdoc">${rcDocFields(r.doc_type, payload)}</div>`,
  async () => { await api("POST", `/api/recurring/${r.id}/post`, { due, overrides: rcReadPayload($("#df"), r.doc_type) }); toast(t("posted")); },
  { wide: true, submitLabel: L2("Post", "ترحيل"), onOpen: f => rcWireDoc(f, r.doc_type) });
}
// open: post automatic recurring entries that fell due (once per day per device)
async function runRecurringOnOpen() {
  if (!can("accounting.recurring", "add")) return;
  const key = "berp.rcrun", day = today();
  try { if (localStorage.getItem(key) === day) return; } catch { /* private mode */ }
  try { const d = await api("GET", "/api/recurring");
    if (d.due.some(x => x.mode === "auto" && !x.last_error)) {
      const r = await api("POST", "/api/recurring-run", { through: day });
      if (r.posted) toast(`${r.posted} ${L2("recurring entries posted", "قيد متكرر تم ترحيله")}`);
      if (r.errors.length) toast(`${r.errors.length} ${L2("recurring entries could not post — see Recurring entries", "قيد متكرر لم يُرحّل — راجع القيود المتكررة")}`, true);
    }
    try { localStorage.setItem(key, day); } catch { /* ignore */ }
  } catch { /* no permission / offline */ }
}

// ================================================================ per-screen import & templates
// which import layouts belong to which screen
const SCREEN_SHEETS = { costs: ["Direct Costs"], expenses: ["Operating Expenses"], payroll: ["Payroll"], receipts: ["Receipt Vouchers"],
  payments: ["Payment Vouchers"], invoices: ["Sales Invoices", "Invoice Lines"], boqs: ["BOQ Lines"], prices: ["Price List"],
  parties: ["Parties"], contacts: ["Parties"], bus: ["Business Units"], assets: ["Fixed Assets"], stock: ["Stock Items", "Stock Moves"],
  moves: ["Stock Moves"], warehouses: ["Warehouses"], journal: ["Journal Vouchers"], coa: ["Chart of Accounts"],
  partners: ["Partners", "Partner Shares", "Partner Transactions"], taxcodes: ["Tax Codes"], products: ["Products"],
  loans: ["Loans", "Loan Transactions"], custody: ["Cash Custody", "Custody Expenses"], rentfleet: ["Rental Equipment", "Rental Stock Lots"],
  rentals: ["Rental Contracts"], sales: ["Sales Contracts"], bankrec: ["Bank Statement Lines"], recurring: ["Recurring Entries"] };
const sheetLabel = n => { const s = (S.cache.impSheets || []).find(x => x.name === n); return s && S.lang === "ar" && s.ar ? s.ar : n; };
const canImportSheet = n => { const s = (S.cache.impSheets || []).find(x => x.name === n); return can("import_export.import", "edit") || (s && s.perm && can(s.perm, "add")); };
const tplUrl = (n, fmt, demo) => "/api/templates/sheet?" + qs({ sheet: n, format: fmt, lang: S.lang, demo: demo ? 1 : "" });
async function impSheets() { if (!S.cache.impSheets) { try { S.cache.impSheets = await api("GET", "/api/import-sheets"); } catch { S.cache.impSheets = []; } } return S.cache.impSheets; }
// adds "⇅ Import" to the screen's toolbar: templates (Excel / CSV) and an import that accepts your own layout
async function addImportMenu(view, screen) {
  const names = SCREEN_SHEETS[screen]; if (!names || !view.isConnected) return;
  await impSheets();
  const bar = $(".toolbar", view); if (!bar || $(".impdd", bar)) return;
  const usable = names.filter(n => (S.cache.impSheets || []).some(s => s.name === n));
  if (!usable.length) return;
  const dd = document.createElement("div"); dd.className = "dd impdd";
  dd.innerHTML = `<button class="btn" type="button">⇅ ${L2("Import", "استيراد")} ▾</button><div class="dd-menu">${usable.map(n => `
    ${usable.length > 1 ? `<div class="muted" style="padding:6px 10px 2px;font-size:.78em;font-weight:700">${esc(sheetLabel(n))}</div>` : ""}
    ${canImportSheet(n) ? `<button type="button" data-a="impopen" data-sheet="${esc(n)}">⇪ ${L2("Import from Excel / CSV…", "استيراد من Excel / CSV…")}</button>` : ""}
    <a href="${tplUrl(n, "xlsx")}">⬇ ${L2("Excel template", "قالب Excel")}</a><a href="${tplUrl(n, "csv")}">⬇ ${L2("CSV template", "قالب CSV")}</a>
    <a href="${tplUrl(n, "xlsx", 1)}">⬇ ${L2("Demo file (Excel)", "ملف تجريبي (Excel)")}</a><a href="${tplUrl(n, "csv", 1)}">⬇ ${L2("Demo file (CSV)", "ملف تجريبي (CSV)")}</a>`).join("")}</div>`;
  const sp = $(".spacer", bar); if (sp) sp.after(dd); else bar.appendChild(dd);
  S.H.impopen = el => importWizard(el.dataset.sheet);
}
const mapKey = (sheet, headers) => "berp.map:" + sheet + ":" + headers.join("|").slice(0, 400);
function importWizard(sheet) {
  let file = null, an = null, checked = false, check, params;
  const sheetInfo = () => (S.cache.impSheets || []).find(s => s.name === sheet) || { columns: [] };
  drawer(`${L2("Import", "استيراد")} — ${sheetLabel(sheet)}`, `
    <p class="muted" style="margin-top:0">${L2("Use the app's template, or your own sheet: its columns are matched by name (English or Arabic) and you can fix the matching below. A check runs first and lists every problem with its row number — nothing is saved until you press Import.", "استخدم قالب البرنامج أو ملفك الخاص: تتم مطابقة الأعمدة بالاسم (عربي أو إنجليزي) ويمكنك تعديل المطابقة بالأسفل. يتم الفحص أولاً ويعرض كل خطأ برقم الصف — ولا يُحفظ شيء حتى تضغط استيراد.")}</p>
    <div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:8px"><a class="btn sm" href="${tplUrl(sheet, "xlsx")}">⬇ ${L2("Excel template", "قالب Excel")}</a><a class="btn sm" href="${tplUrl(sheet, "csv")}">⬇ ${L2("CSV template", "قالب CSV")}</a></div>
    <label class="f">${L2("Your file (Excel or CSV)", "ملفك (Excel أو CSV)")}<input type="file" id="impwf" data-skip="1" accept=".xlsx,.xlsm,.csv,.txt"></label>
    <div id="impwa"></div><div id="impwr"></div>`,
  async () => { if (!an) throw new Error(L2("Choose a file first", "اختر ملفاً أولاً")); if (!checked) { await check(); return false; }
    const r = await api("POST", "/api/import-sheet?" + qs(params()), undefined, await file.arrayBuffer());
    toast(`${Object.values(r.counts || {}).reduce((a, b) => a + b, 0)} ${L2("imported", "تم استيرادها")}`); S.cache.impSheets = null; await refreshBoot(); },
  { wide: true, submitLabel: L2("Check", "فحص"), onOpen: f => {
    const btn = () => $("button[type=submit]", f);
    const mapping = () => { const m = {}; $$("#impwm select", f).forEach(sel => { if (sel.value !== "") m[sel.value] = sel.dataset.key; }); return m; };
    params = () => ({ sheet, filename: file.name, table: an.table, header_row: an.header_row, mapping: JSON.stringify(mapping()) });
    const draw = () => {
      const cols = an.columns, used = mapping();
      $("#impwa", f).innerHTML = `${an.tables.length > 1 ? `<label class="f">${L2("Sheet in your file", "الورقة في ملفك")}<select id="impwt" data-skip="1">${an.tables.map(t => `<option ${t === an.table ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>` : ""}
        <div class="banner ${an.missing.length ? "warn" : "ok"}">${an.rows} ${L2("rows found", "صف")} · ${L2("heading row", "صف العناوين")} ${an.header_row + 1}${an.table !== "CSV" ? ` · “${esc(an.table)}”` : ""} — ${an.exact ? L2("your file matches the app's layout exactly.", "ملفك مطابق تماماً لتنسيق البرنامج.") : an.missing.length ? L2("match the required columns marked ★ below.", "طابق الأعمدة المطلوبة المعلّمة ★ بالأسفل.") : L2("columns matched — check the matching below.", "تمت مطابقة الأعمدة — راجعها بالأسفل.")}</div>
        <label class="f" style="max-width:220px">${L2("Heading row", "صف العناوين")}<input type="number" id="impwh" data-skip="1" min="1" value="${an.header_row + 1}"></label>
        <div class="tbl"><table id="impwm"><thead><tr><th>${L2("App column", "عمود البرنامج")}</th><th>${L2("Your column", "عمودك")}</th><th>${L2("First value", "أول قيمة")}</th><th>${L2("Format", "الصيغة")}</th></tr></thead><tbody>
        ${cols.map(c => { const sel = Object.keys(an.mapping).find(i => an.mapping[i] === c.key); const v = sel != null && an.sample[0] ? an.sample[0][+sel] : "";
          return `<tr class="${c.required && sel == null ? "miss" : ""}"><td>${c.required ? "★ " : ""}<b>${esc(S.lang === "ar" ? c.ar : c.en)}</b>${c.note ? `<div class="lines">${esc(c.note)}</div>` : ""}</td>
          <td><select data-skip="1" data-key="${esc(c.key)}" data-change="impwsel"><option value="">— ${L2("not in my file", "غير موجود في ملفي")} —</option>${an.headers.map((h, i) => `<option value="${i}" ${String(i) === String(sel) ? "selected" : ""}>${esc(h)}</option>`).join("")}</select></td>
          <td class="muted">${esc(v || "")}</td><td class="muted" style="font-size:.82em">${esc(c.format)}${c.choices.length ? `: ${esc(c.choices.join(", "))}` : ""}${c.default_role ? ` · ${L2("blank = default", "فارغ = الافتراضي")}` : ""}</td></tr>`; }).join("")}</tbody></table></div>
        ${an.headers.some((h, i) => !(String(i) in an.mapping)) ? `<p class="muted" style="font-size:.85em">${L2("Not used from your file", "أعمدة لن تُستخدم من ملفك")}: ${an.headers.filter((h, i) => !Object.keys(mapping()).includes(String(i))).map(esc).join(" · ")}</p>` : ""}`;
      const t = $("#impwt", f); if (t) t.addEventListener("change", () => analyze({ table: t.value }));
      $("#impwh", f).addEventListener("change", e => analyze({ table: an.table, header_row: Math.max(0, (+e.target.value || 1) - 1) }));
      checked = false; btn().textContent = L2("Check", "فحص"); $("#impwr", f).innerHTML = "";
    };
    S.H.impwsel = el => { an.mapping = Object.fromEntries(Object.entries(mapping()).map(([i, k]) => [i, k])); checked = false; btn().textContent = L2("Check", "فحص");
      el.closest("tr").classList.toggle("miss", el.value === "" && an.columns.find(c => c.key === el.dataset.key).required); $("#impwr", f).innerHTML = ""; };
    const analyze = async (extra = {}) => { const box = $("#impwa", f); box.innerHTML = `<div class="muted">${t("loading")}</div>`;
      try { an = await api("POST", "/api/import-preview/analyze?" + qs({ sheet, filename: file.name, ...extra }), undefined, await file.arrayBuffer());
        try { const saved = JSON.parse(localStorage.getItem(mapKey(sheet, an.headers)) || "null"); if (saved && !an.exact) { an.mapping = saved; an.missing = an.columns.filter(c => c.required && !Object.values(saved).includes(c.key)).map(c => c.key); } } catch { /* none */ }
        draw(); } catch (e) { an = null; box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; } };
    check = async () => { const out = $("#impwr", f);
      try { const r = await api("POST", "/api/import-preview/check?" + qs(params()), undefined, await file.arrayBuffer());
        try { localStorage.setItem(mapKey(sheet, an.headers), JSON.stringify(mapping())); } catch { /* private mode */ }
        out.innerHTML = `<div class="banner ok">✓ ${r.rows} ${L2("rows checked — no problems. Press Import to save them.", "صف تم فحصها — لا توجد أخطاء. اضغط استيراد للحفظ.")}</div>`;
        checked = true; btn().textContent = L2("Import", "استيراد");
      } catch (e) { const errs = (e.data && e.data.errors) || [];
        out.innerHTML = `<div class="banner err">${esc(e.message)}</div>${errs.length ? `<div class="tbl" style="max-height:300px;overflow:auto"><table><thead><tr><th>${L2("Row in your file", "الصف في ملفك")}</th><th>${L2("Problem", "المشكلة")}</th></tr></thead><tbody>
          ${errs.map(x => `<tr><td>${x.row}</td><td>${esc(x.error)}</td></tr>`).join("")}</tbody></table></div>
          <div class="row" style="gap:6px;margin-top:6px"><button type="button" class="btn sm" data-a="impwdl">⬇ ${L2("Download the problem list (CSV)", "تحميل قائمة الأخطاء (CSV)")}</button><a class="btn sm" href="${tplUrl(sheet, "xlsx")}">⬇ ${L2("The layout the app fully supports", "التنسيق المدعوم بالكامل")}</a></div>` : ""}`;
        S.H.impwdl = () => { const keys = [...new Set(errs.flatMap(x => Object.keys(x.values || {})))];
          const rows = [["row", "problem", ...keys], ...errs.map(x => [x.row, x.error, ...keys.map(k => (x.values || {})[k] ?? "")])];
          const csvText = "﻿" + rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\r\n");
          const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csvText], { type: "text/csv" })); a.download = `${sheet.replace(/\W+/g, "_")}_problems.csv`; document.body.appendChild(a); a.click(); a.remove(); };
        checked = false; } };
    $("#impwf", f).addEventListener("change", e => { file = e.target.files[0]; if (file) analyze(); });
  } });
}

// ================================================================= entry types
function entryTypeForm(id) {
  const e = (S.boot.enums.entry_types || []).find(x => x.id === id); if (!e) return;
  const opts = side => side.choose.map(c => { const a = S.boot.accounts.find(x => x.code === c) || { code: c, name: c }; return [c, `${c} — ${aname(a)}`]; });
  const fixed = side => { const a = S.boot.accounts.find(x => x.code === side.fixed) || {}; return `${side.fixed} — ${aname(a)}`; };
  const fields = [{ k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "amount", l: t("amount"), type: "number", req: 1 },
    e.dr.fixed ? { html: `<label class="f">${t("debit")}<input value="${esc(fixed(e.dr))}" disabled></label>` } : { k: "debit_account", l: t("debit"), type: "select", opts: opts(e.dr), v: e.dr.default },
    e.cr.fixed ? { html: `<label class="f">${t("credit")}<input value="${esc(fixed(e.cr))}" disabled></label>` } : { k: "credit_account", l: t("credit"), type: "select", opts: opts(e.cr), v: e.cr.default }];
  if (e.party !== "none") fields.push({ k: "party", l: t("party"), list: "etparties", req: e.party === "required" });
  if (e.bu !== "none") fields.push({ k: "bu_id", l: t("bu"), type: "select", opts: buOpts(e.bu === "required" ? "" : "—"), req: e.bu === "required" });
  fields.push({ k: "ref", l: t("ref") }, { k: "memo", l: t("memo"), v: (S.lang === "ar" && e.name_ar) || e.name, span: "spanall" });
  drawer((S.lang === "ar" && e.name_ar) || e.name, form(fields) + partyList("etparties", (S.boot.partners || []).map(p => p.name)),
    async v => { await api("POST", "/api/entry/" + e.id, v); toast(t("posted")); });
}

// Settings → Menus: hide sections & screens, the "+ New" menu, entry types
function specOpts(cur) {
  const o = [["", L2("Choose when entering — any account", "يُختار عند الإدخال — أي حساب")]];
  Object.keys(S.boot.enums.subtypes).forEach(ty => o.push([`type:${ty}`, `${L2("Choose among", "يُختار من")}: ${tl(ty)}`]));
  Object.entries(S.boot.enums.subtypes).forEach(([, subs]) => subs.forEach(s => o.push([`subtype:${s}`, `${L2("Choose among", "يُختار من")}: ${sub(s)}`])));
  if (cur && !o.some(x => x[0] === cur)) {
    if (cur.startsWith("role:")) { const a = S.boot.accounts.find(x => x.role === cur.slice(5)); o.push([cur, a ? `${a.code} — ${aname(a)}` : cur]); }
    else if (cur.startsWith("subtype:")) o.push([cur, `${L2("Choose among", "يُختار من")}: ${cur.slice(8).split(",").map(sub).join(" / ")}`]);
    else if (cur.startsWith("type:")) o.push([cur, `${L2("Choose among", "يُختار من")}: ${cur.slice(5).split(",").map(tl).join(" / ")}`]);
  }
  S.boot.accounts.filter(a => a.active).forEach(a => o.push([a.code, `${a.code} — ${aname(a)}`]));
  return o.map(([v, l]) => `<option value="${esc(v)}" ${v === (cur || "") ? "selected" : ""}>${esc(l)}</option>`).join("");
}
const reqOpts = cur => [["none", L2("Not asked", "لا يُطلب")], ["optional", L2("Optional", "اختياري")], ["required", L2("Required", "إلزامي")]]
  .map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`).join("");
const etRow = e => `<tr data-id="${esc(e.id || "")}"><td><input class="et-name" value="${esc(e.name || "")}" style="min-width:160px"><input class="et-name-ar" dir="rtl" value="${esc(e.name_ar || "")}" placeholder="العربية" style="min-width:160px;margin-top:4px"></td>
  <td><input class="et-kind" list="etkinds" value="${esc(e.kind || "Journal Voucher")}" style="min-width:130px"></td>
  <td><select class="et-debit" style="min-width:190px;max-width:260px">${specOpts(e.debit)}</select></td><td><select class="et-credit" style="min-width:190px;max-width:260px">${specOpts(e.credit)}</select></td>
  <td><select class="et-party">${reqOpts(e.party || "optional")}</select></td><td><select class="et-bu">${reqOpts(e.bu || "optional")}</select></td>
  <td class="center"><input type="checkbox" class="et-active" ${e.active !== false ? "checked" : ""}></td><td><button type="button" class="btn sm danger" data-a="etdel">✕</button></td></tr>`;

function entryOptionCards() {
  const ed = can("settings.menus", "edit"), hid = S.boot.settings.hidden_modules || [];
  const subs = S.boot.enums.submodules;
  const secs = S.boot.enums.modules.filter(m => !["settings", "users", "dashboard"].includes(m));
  const hideCard = `<div class="card"><h2>${L2("Show or hide sections and screens", "إظهار أو إخفاء الأقسام والشاشات")}</h2><p class="muted">${t("hidden_note")}</p><div class="grid g4">
    ${secs.map(m => `<div><label class="f chk"><input type="checkbox" class="hidmod" value="${m}" ${hid.includes(m) ? "" : "checked"} ${ed ? "" : "disabled"}><b>${t("mod_" + m)}</b></label>
      ${(subs[m] || []).map(sc => `<label class="f chk" style="margin-inline-start:22px"><input type="checkbox" class="hidmod" value="${m}.${sc}" ${hid.includes(m + "." + sc) ? "" : "checked"} ${ed ? "" : "disabled"}>${esc(t(window.SUBPERM[m + "." + sc] || sc))}</label>`).join("")}</div>`).join("")}</div>
    ${ed ? `<div class="row" style="margin-top:10px"><button class="btn" data-a="hidsave">${t("save")}</button></div>` : ""}</div>`;
  const items = newMenuItems();
  const menuCard = `<div class="card"><h2>${L2("“+ New” menu", "قائمة «+ جديد»")}</h2><p class="muted">${L2("Tick what appears under “+ New” and put it in order. Entry types you add below can be put here too. People only see what their permissions allow.", "اختر ما يظهر تحت «+ جديد» ورتّبه. يمكن إضافة أنواع القيود التي تنشئها بالأسفل أيضاً. كل مستخدم يرى ما تسمح به صلاحياته فقط.")}</p>
    <div class="tbl"><table id="nmtab"><tbody>${items.map(i => `<tr data-key="${esc(i.key)}"><td style="width:36px"><input type="checkbox" class="nm-show" ${i.show ? "checked" : ""} ${ed ? "" : "disabled"}></td><td>${esc(i.label)}${i.entry ? ` <span class="chip">${L2("entry type", "نوع قيد")}</span>` : ""}</td>
      <td style="width:90px">${ed ? `<button type="button" class="btn sm" data-a="nmup">↑</button><button type="button" class="btn sm" data-a="nmdown">↓</button>` : ""}</td></tr>`).join("")}</tbody></table></div>
    ${ed ? `<div class="row" style="margin-top:10px"><button class="btn" data-a="nmsave">${t("save")}</button><button class="btn" data-a="nmreset">${L2("Back to the default", "الافتراضي")}</button></div>` : ""}</div>`;
  const types = S.boot.enums.entry_types || [];
  const typeCard = `<div class="card"><h2>${L2("Entry types", "أنواع القيود")}</h2><p class="muted">${L2("Two-line entries you use often (“Pay rent”, “Move cash to the bank”…). Each side is a fixed account or chosen when entering. They appear as templates in Journal vouchers and can be added to “+ New”.", "قيود من سطرين تُستخدم كثيراً («سداد الإيجار»، «إيداع نقدية بالبنك»…). كل طرف حساب ثابت أو يُختار عند الإدخال. تظهر كقوالب في قيود اليومية ويمكن إضافتها إلى «+ جديد».")}</p>
    <div class="tbl"><table id="ettab"><thead><tr><th>${t("name")}</th><th>${L2("Voucher type", "نوع القيد")}</th><th>${t("debit")}</th><th>${t("credit")}</th><th>${t("party")}</th><th>${t("bu")}</th><th>${t("active")}</th><th></th></tr></thead>
      <tbody>${types.map(etRow).join("")}</tbody></table></div>${datalist("etkinds", S.boot.enums.entry_kinds)}
    ${ed ? `<div class="row" style="margin-top:10px"><button class="btn" data-a="etadd">+ ${L2("Add entry type", "إضافة نوع قيد")}</button><span class="spacer"></span><button class="btn pri" data-a="etsave">${t("save")}</button></div>` : ""}</div>`;
  return hideCard + menuCard + typeCard;
}
function entryOptionWire(view) {
  const after = async () => { await refreshBoot(); renderShell(); route(); toast(t("saved")); };
  S.H.hidsave = async () => { try { await api("PUT", "/api/settings", { hidden_modules: $$(".hidmod", view).filter(i => !i.checked).map(i => i.value) }); await after(); } catch (e) { fail(e); } };
  S.H.nmup = el => { const tr = el.closest("tr"); if (tr.previousElementSibling) tr.parentNode.insertBefore(tr, tr.previousElementSibling); };
  S.H.nmdown = el => { const tr = el.closest("tr"); if (tr.nextElementSibling) tr.parentNode.insertBefore(tr.nextElementSibling, tr); };
  S.H.nmsave = async () => { try { await api("PUT", "/api/new-menu", { items: $$("#nmtab tr", view).map(tr => ({ key: tr.dataset.key, show: $(".nm-show", tr).checked })) }); await after(); } catch (e) { fail(e); } };
  S.H.nmreset = async () => { try { await api("PUT", "/api/new-menu", { items: [] }); await after(); } catch (e) { fail(e); } };
  S.H.etadd = () => { $("#ettab tbody", view).insertAdjacentHTML("beforeend", etRow({ kind: "Journal Voucher", debit: "type:Expense", credit: "subtype:cash", party: "optional", bu: "optional" })); };
  S.H.etdel = el => { if (confirm(L2("Remove this entry type? Entries already booked with it stay.", "حذف نوع القيد؟ القيود المسجلة به تبقى."))) el.closest("tr").remove(); };
  S.H.etsave = async () => {
    const types = $$("#ettab tbody tr", view).map(tr => ({ id: tr.dataset.id, name: $(".et-name", tr).value.trim(), name_ar: $(".et-name-ar", tr).value.trim(), kind: $(".et-kind", tr).value.trim(),
      debit: $(".et-debit", tr).value, credit: $(".et-credit", tr).value, party: $(".et-party", tr).value, bu: $(".et-bu", tr).value, active: $(".et-active", tr).checked })).filter(x => x.name);
    try { await api("PUT", "/api/entry-types", { types }); await after(); } catch (e) { fail(e); }
  };
}

// ======================================================== get the app (hosted site)
// Downloads built by GitHub Actions (downloads/beyond-downloads.json) and installing the site on a phone.
let installPrompt = null;
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); installPrompt = e; });
PAGES.download = async view => {
  let d = { files: [] };
  try { const r = await fetch("downloads/beyond-downloads.json", { cache: "no-store" }); if (r.ok) d = await r.json(); } catch { /* none built yet */ }
  const f = k => (d.files || []).find(x => x.kind === k);
  const mb = x => `${(x.size / 1048576).toFixed(1)} MB`;
  const cfg = window.BEYOND_CLOUD_CONFIG || {};
  const card = (title, body) => `<div class="card"><h2>${title}</h2>${body}</div>`;
  const dl = (x, label) => x ? `<a class="btn ${x.kind.includes("offline") ? "" : "pri"}" href="downloads/${encodeURIComponent(x.file)}" download>⬇ ${esc(label)}</a> <span class="muted">${mb(x)} · ${L2("version", "إصدار")} ${esc(d.version || "")}</span>`
    : `<div class="muted">${L2("Not built yet — it appears here after the next update is published.", "لم يُجهز بعد — يظهر هنا بعد نشر التحديث القادم.")}</div>`;
  S.H.pwainstall = async () => { if (!installPrompt) return; installPrompt.prompt(); await installPrompt.userChoice.catch(() => {}); installPrompt = null; route(); };
  S.H.copycfg = async el => { try { await navigator.clipboard.writeText(el.dataset.v); toast(L2("Copied", "تم النسخ")); } catch { prompt("", el.dataset.v); } };
  const installed = window.matchMedia && window.matchMedia("(display-mode: standalone)").matches;
  view.innerHTML =
    card(L2("Phone or tablet (Android / iPhone)", "الموبايل أو التابلت (أندرويد / آيفون)"), installed ? `<div class="banner ok">${L2("You are using the installed app ✓", "أنت تستخدم التطبيق المثبت ✓")}</div>` :
      `${installPrompt ? `<p><button class="btn pri" data-a="pwainstall">${L2("Install Beyond ERP", "تثبيت Beyond ERP")}</button></p>` : ""}
      <p><b>Android (Chrome):</b> ${L2("open this site → menu ⋮ → “Install app” (or “Add to Home screen”).", "افتح الموقع ← القائمة ⋮ ← «تثبيت التطبيق» (أو «إضافة إلى الشاشة الرئيسية»).")}</p>
      <p><b>iPhone / iPad (Safari):</b> ${L2("open this site → Share □↑ → “Add to Home Screen”.", "افتح الموقع ← زر المشاركة □↑ ← «إضافة إلى الشاشة الرئيسية».")}</p>
      <p class="muted">${L2("It opens full-screen like an app, signs in to the same workspace and keeps working without a connection (changes need the connection to be saved).", "يفتح بملء الشاشة كتطبيق، ويدخل لنفس مساحة العمل، ويعمل بدون إنترنت للعرض (الحفظ يحتاج اتصالاً).")}</p>`) +
    card(L2("Computer app — connected (recommended)", "برنامج الكمبيوتر — متصل (موصى به)"), `<p>${L2("Opens this website in its own clean window with the Beyond icon. Same sign-in, same books: anything done in it is on the website and every phone instantly. Needs internet.", "يفتح هذا الموقع في نافذة مستقلة بأيقونة Beyond. نفس تسجيل الدخول ونفس الدفاتر: أي تعديل يظهر فوراً على الموقع وكل الموبايلات. يحتاج إنترنت.")}</p>
      <p><b>Windows</b> — ${dl(f("windows"), L2("Download the installer (.exe)", "تحميل برنامج التثبيت (.exe)"))}</p>
      <p><b>Mac</b> — ${dl(f("mac"), L2("Download for Mac (.zip)", "تحميل للماك (.zip)"))}</p>`) +
    card(L2("Computer app — offline", "برنامج الكمبيوتر — بدون إنترنت"), `<p>${L2("The whole app runs on the computer and works without internet. The first time, sign in with your usual account: it downloads the shared books and keeps them in step whenever it is online (changes need the connection; without it the books are read-only). You can also keep completely separate books on that computer.", "البرنامج كاملاً يعمل على الكمبيوتر وبدون إنترنت. أول مرة سجّل الدخول بحسابك المعتاد: ينزّل الدفاتر المشتركة ويحدّثها عند الاتصال (التعديل يحتاج اتصالاً؛ بدونه للعرض فقط). ويمكن أيضاً عمل دفاتر منفصلة تماماً على هذا الكمبيوتر.")}</p>
      <p><b>Windows</b> — ${dl(f("windows_offline"), L2("Download the installer (.exe)", "تحميل برنامج التثبيت (.exe)"))}</p>
      <p><b>Mac</b> — ${dl(f("mac_offline"), L2("Download for Mac (.zip)", "تحميل للماك (.zip)"))}</p>
      <p class="muted">${L2("Windows may say “Windows protected your PC” (the installers are not signed): More info → Run anyway. Mac: unzip, move to Applications, the first time right-click → Open. Mac apps need an Apple-chip Mac (M1 or newer).", "قد يظهر «Windows protected your PC» لأن البرامج غير موقّعة: More info ثم Run anyway. الماك: فك الضغط وانقل للتطبيقات وأول مرة زر أيمن ← فتح. نسخ الماك تحتاج ماك بمعالج Apple (M1 أو أحدث).")}</p>`) +
    card(L2("Offline browser copy", "نسخة المتصفح بدون إنترنت"), `${dl(f("web"), L2("Download the offline copy (.zip)", "تحميل النسخة (.zip)"))}
      <p class="muted">${L2("Unzip anywhere and open index.html — no installation. Its data stays in that browser.", "فك الضغط في أي مكان وافتح index.html — بدون تثبيت. بياناتها تبقى في هذا المتصفح.")}</p>`) +
    (cfg.url ? card(L2("Connect the desktop app to this workspace", "ربط برنامج الكمبيوتر بمساحة العمل"), `<p>${L2("In the desktop app: Settings → Workspace (cloud) → paste these two values, then sign in with your usual email or username and password.", "في برنامج الكمبيوتر: الإعدادات ← مساحة العمل ← الصق القيمتين ثم سجّل الدخول بنفس البريد أو اسم المستخدم وكلمة المرور.")}</p>
      <div class="grid g2"><label class="f">${L2("Workspace URL", "رابط مساحة العمل")}<input value="${esc(cfg.url)}" readonly></label><div class="row" style="align-items:end"><button class="btn" data-a="copycfg" data-v="${esc(cfg.url)}">${L2("Copy", "نسخ")}</button></div>
      <label class="f">${L2("Public key", "المفتاح العام")}<input value="${esc(cfg.key)}" readonly></label><div class="row" style="align-items:end"><button class="btn" data-a="copycfg" data-v="${esc(cfg.key)}">${L2("Copy", "نسخ")}</button></div></div>`) : "");
  $("#ttl").textContent = L2("Get the app", "تحميل التطبيق");
};

(window.__beyondReady || Promise.resolve()).then(boot, () => {});
})();
