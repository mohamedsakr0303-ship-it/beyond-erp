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
  if (!r.ok) { const e = new Error((data && data.error) || `HTTP ${r.status}`); e.data = data; throw e; }
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
  if (r.status >= 400) { const e = new Error((data && data.error) || `HTTP ${r.status}`); e.data = data; throw e; }
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
const shown = key => !hiddenMods().includes(String(key).split(".")[0]);

// ---------------------------------------------------------------- events
document.addEventListener("click", e => {
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
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
window.addEventListener("hashchange", () => route());

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
  const view = $("#view");
  view.innerHTML = `<div class="empty">${t("loading")}</div>`;
  try { await page(view, parts, q); } catch (e) { view.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}

// ----------------------------------------------------------------- shell
function renderShell() {
  const st = S.boot.settings;
  $("#root").innerHTML = `<div class="app">
    <aside class="side"><div class="brand">${S.boot.app_logo ? `<img class="brand-logo" src="data:${S.boot.app_logo.mime};base64,${S.boot.app_logo.data}" alt="">` : ""}<b>${esc((st.appearance || {}).app_name || st.company_name || "BEYOND")}</b><small>${esc(st.company_tagline || "")}</small></div><nav class="nav" id="nav"></nav></aside>
    <div class="main"><header class="top">
      <button class="btn sm menu-btn" data-a="menu">☰</button>
      <div><h1 id="ttl"></h1><div class="crumb" id="crumb"></div></div><span class="spacer"></span>
      <div class="dd"><button class="btn pri sm">+ ${t("new")}</button><div class="dd-menu" id="newmenu"></div></div>
      <span id="syncdot" class="chip" style="display:none"></span>
      <button class="btn sm" data-a="lang">${t("language")}</button>
      <div class="dd"><button class="btn sm">${esc(S.boot.user.full_name || S.boot.user.username)} ▾</button><div class="dd-menu">
        <button data-a="theme">${t("theme")}: ${esc(store.get("theme", "auto"))}</button>
        <a href="#/settings">${t("m_settings")}</a><button data-a="pw">${t("change_pw")}</button><button data-a="logout">${t("logout")}</button></div></div>
    </header><main class="content" id="view"></main></div></div>`;
  const nm = [];
  if (can("purchasing.costs", "add")) nm.push(["costs", "m_costs", "direct_cost"]);
  if (can("purchasing.expenses", "add")) nm.push(["expenses", "m_expenses", "expense"]);
  if (can("purchasing.payments", "add")) nm.push(["payments", "m_payments", "payment"]);
  if (can("sales.receipts", "add")) nm.push(["receipts", "m_receipts", "receipt"]);
  if (can("sales.invoices", "add")) nm.push(["invoices", "m_invoices", "invoice"]);
  if (can("payroll", "add")) nm.push(["payroll", "m_payroll", "payroll"]);
  if (can("stock.moves", "add")) nm.push(["stock.moves", "m_moves", "stock_move"]);
  if (can("accounting.journal", "add")) nm.push(["journal", "m_journal", "journal"]);
  if (can("business_units.units", "add")) nm.push(["bus", "m_bus", "bu"]);
  if (can("sales.offers", "add")) nm.push(["offers", "m_offers", "offer"]);
  $("#newmenu").innerHTML = nm.map(([, k, d]) => `<button data-a="quicknew" data-doc="${d}">${t(k)}</button>`).join("") || `<div class="muted" style="padding:8px">${t("view_only")}</div>`;
}
S.GLOBAL_H = {
  menu: () => $(".app").classList.toggle("menu"),
  lang: () => { store.set("lang", S.lang === "ar" ? "en" : "ar"); applyLook(); renderShell(); route(); },
  theme: () => { const order = ["auto", "light", "dark"]; store.set("theme", order[(order.indexOf(store.get("theme", "auto")) + 1) % 3]); applyLook(); renderShell(); route(); },
  logout: async () => { await api("POST", "/api/logout").catch(() => {}); store.set("token", "");
    if (STATIC && window.BeyondCloud) window.BeyondCloud.signout(); location.hash = ""; S.boot.user = null; renderAuth(); },
  pw: () => drawer(t("change_pw"), form([{ k: "current", l: t("current_pw"), type: "password" }, { k: "new", l: t("new_pw"), type: "password" }]),
    async v => { await api("PUT", "/api/me/password", v); toast(t("saved")); return true; }),
  quicknew: el => openDoc(el.dataset.doc),
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
      const on = active === r || active.startsWith(r + "/") || (r === "bus" && active.startsWith("bu/")) ||
        (r === "invoices" && active.startsWith("invoice/")) || (r === "boqs" && active.startsWith("boq/")) ||
        (r === "offers" && (active.startsWith("offer/") || active.startsWith("offertype")));
      if (on) { title = t(k); group = t(g); }
      html += `<a href="#/${r}" class="${on ? "on" : ""}"><span class="ic">${ic}</span>${esc(t(k))}</a>`;
    }
  }
  nav.innerHTML = html;
  $("#ttl").textContent = title || t("m_dashboard");
  $("#crumb").textContent = group;
}

// ------------------------------------------------------------- auth pages
// ------------------------------------------------ shared workspace (Supabase) — same calls in both editions
const CloudAPI = STATIC ? {
  status: () => window.BeyondCloud.status(), config: (url, key) => window.BeyondCloud.config(url, key),
  signin: (email, password, create) => window.BeyondCloud.signin(email, password, create), info: () => window.BeyondCloud.info(),
  claim: () => window.BeyondCloud.claim(), join: () => window.BeyondCloud.join(), pull: () => window.BeyondCloud.pull(),
  disconnect: () => window.BeyondCloud.disconnect(), members: () => window.BeyondCloud.members(), setMember: m => window.BeyondCloud.setMember(m),
  linkMember: m => window.BeyondCloud.linkMember(m),
} : {
  status: () => api("GET", "/api/cloud"), config: (url, key) => api("PUT", "/api/cloud/config", { url, key }),
  signin: (email, password, create) => api("POST", "/api/cloud/signin", { email, password, create }), info: () => api("GET", "/api/cloud/info"),
  claim: () => api("POST", "/api/cloud/claim", {}), join: () => api("POST", "/api/cloud/join", {}), pull: () => api("POST", "/api/cloud/pull", {}),
  disconnect: () => api("POST", "/api/cloud/disconnect", {}), members: () => api("GET", "/api/cloud/members"), setMember: m => api("PUT", "/api/cloud/members", m),
  linkMember: m => api("PUT", "/api/cloud/members/link", m),
};
const cloudOn = () => STATIC ? !!(window.BeyondCloud && window.BeyondCloud.joined()) : !!(S.boot && S.boot.cloud && S.boot.cloud.joined);

// Password field(s) with a "Show password" switch; `confirm` adds a second field that must match (new accounts,
// new passwords). The browser's own validation then refuses to submit until both are the same.
function pwFields(confirm, label) {
  const ar = S.lang === "ar";
  return `<label class="f">${esc(label || t("password"))}<input name="password" type="password" autocomplete="${confirm ? "new-password" : "current-password"}" required minlength="6"></label>
    ${confirm ? `<label class="f">${ar ? "تأكيد كلمة المرور" : "Confirm password"}<input name="password2" type="password" autocomplete="new-password" required minlength="6" data-input="pwmatch"></label>
      <div id="pwhint" style="font-size:.8em;min-height:1.1em"></div>` : ""}
    <label class="f chk"><input type="checkbox" data-change="pwshow">${ar ? "إظهار كلمة المرور" : "Show password"}</label>`;
}
function pwWire(root) {
  const ar = S.lang === "ar";
  S.H.pwshow = el => $$("input[name=password], input[name=password2]", root).forEach(i => { i.type = el.checked ? "text" : "password"; });
  S.H.pwmatch = () => {
    const a = $("[name=password]", root), b = $("[name=password2]", root), h = $("#pwhint", root); if (!b) return;
    const same = !b.value || a.value === b.value;
    b.setCustomValidity(same ? "" : (ar ? "كلمتا المرور غير متطابقتين" : "The passwords do not match"));
    if (h) { h.textContent = !b.value ? "" : same ? (ar ? "✓ متطابقتان" : "✓ Passwords match") : (ar ? "✗ كلمتا المرور غير متطابقتين" : "✗ The passwords do not match");
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
    clocal: () => { store.set("cloudLocal", true); renderAuth(); },
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
// After a successful workspace sign-in: create / join the workspace if needed, then open the matching app user.
async function cloudEnter(info) {
  const ar = S.lang === "ar", C = window.BeyondCloud;
  if (!info.is_member) {
    if (!info.has_members) {
      if (!confirm(ar ? "مساحة العمل فارغة. إنشاؤها الآن من بيانات هذا الجهاز؟ ستصبح أنت المسؤول." : "This workspace is empty. Create it now from this device's books? You become its administrator.")) return;
      await C.claim();
    } else throw new Error(ar ? "حسابك ليس عضواً في مساحة العمل — اطلب من المسؤول إضافتك." : "Your account is not a member of this workspace — ask an administrator to add you.");
  } else if (!C.joined()) {
    $("#lerr").innerHTML = `<div class="banner info">${ar ? "جارٍ تنزيل البيانات المشتركة…" : "Downloading the shared books…"}</div>`;
    await C.join();
  } else await C.pull().catch(() => {});
  const r = await C.openSession();
  const data = JSON.parse(new TextDecoder().decode(r.body));
  if (r.status >= 400) throw new Error(data.error);
  store.set("token", data.token || ""); store.set("cloudLocal", false);
  await boot();
}

async function renderAuth() {
  if (STATIC && window.BeyondCloud && window.BeyondCloud.hasPendingAuth()) { store.set("cloudLocal", false); if (await cloudReturn()) return; }
  if (STATIC && window.BeyondCloud && window.BeyondCloud.configured() && !store.get("cloudLocal", false)) return renderCloudAuth();
  applyLook();
  const setup = S.boot.needs_setup;
  $("#root").innerHTML = `<div class="login"><h1>${esc(S.boot.settings.company_name || "Beyond")} ERP</h1>
    <p class="muted">${setup ? t("setup_title") : t("sign_in")}</p><div id="lerr"></div>
    <form id="lf" class="grid">${setup ? `<label class="f">${t("company_name")}<input name="company_name" value="${esc(S.boot.settings.company_name || "")}"></label>
      <label class="f">${t("full_name")}<input name="full_name"></label>` : ""}
      <label class="f">${t("username")}<input name="username" autocomplete="username" required></label>
      ${setup ? pwFields(true).replace(/minlength="6"/g, 'minlength="8"') : `<label class="f">${t("password")}<input name="password" type="password" autocomplete="current-password" required minlength="1"></label>
      <label class="f chk"><input type="checkbox" data-change="pwshow">${S.lang === "ar" ? "إظهار كلمة المرور" : "Show password"}</label>`}
      <button class="btn pri" type="submit">${setup ? t("save") : t("sign_in")}</button>
      <button class="btn" type="button" data-a="lang2">${t("language")}</button></form></div>`;
  S.H = { lang2: () => { store.set("lang", S.lang === "ar" ? "en" : "ar"); renderAuth(); } };
  pwWire($("#lf"));
  $("#lf").addEventListener("submit", async e => {
    e.preventDefault();
    if (setup) { S.H.pwmatch(); if (!e.target.reportValidity()) return; }
    const v = Object.fromEntries(new FormData(e.target));
    try {
      if (setup) v.language = S.lang;
      await api("POST", setup ? "/api/setup" : "/api/login", v);
      await boot();
    } catch (err) { $("#lerr").innerHTML = `<div class="banner err">${esc(err.message)}</div>`; }
  });
}

// ---------------------------------------------------------------- drawer
function drawer(title, html, onSubmit, opts = {}) {
  closeDrawer();
  const bg = document.createElement("div");
  bg.className = "drawer-bg";
  bg.innerHTML = `<div class="drawer" role="dialog" aria-label="${esc(title)}"><h3>${esc(title)}</h3><div id="derr"></div>
    <form id="df">${html}<div class="foot">${opts.extraButtons || ""}<button class="btn" type="button" data-a="closeDrawer">${t(opts.readOnly ? "close" : "cancel")}</button>
    ${opts.readOnly ? "" : `<button class="btn pri" type="submit">${esc(opts.submitLabel || t("save"))}</button>`}</div></form></div>`;
  bg.addEventListener("mousedown", e => { if (e.target === bg) closeDrawer(); });
  document.body.appendChild(bg);
  const f = $("#df", bg);
  f.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = $("button[type=submit]", f); if (btn) btn.disabled = true;
    try { const done = await onSubmit(readForm(f), f); if (done !== false) { closeDrawer(); route(); } }
    catch (err) { $("#derr").innerHTML = `<div class="banner err">${esc(err.message)}</div>`; }
    finally { if (btn) btn.disabled = false; }
  });
  const first = $("input:not([type=hidden]),select", f); if (first) first.focus();
  if (opts.onOpen) opts.onOpen(f);
  return f;
}
function closeDrawer() { $$(".drawer-bg").forEach(d => d.remove()); }
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
  return `<div class="card" id="fbar"><div class="filters">${parts.join("")}
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
  return `<div class="tbl"><table><thead><tr><th>${t("date")}</th><th>#</th><th>${t("kind")}</th><th>${t("description")}</th><th>${t("party")}</th><th>${t("bu")}</th><th class="num">${t("amount")}</th><th></th></tr></thead><tbody>
    ${rows.map(e => `<tr class="click ${e.status === "void" || e.reversed_by ? "void" : ""}" data-a="openv" data-id="${e.id}"><td>${fd(e.date)}</td><td>${e.id}</td><td><span class="chip">${esc(tl(e.kind))}</span></td>
      <td>${esc(e.memo)}<div class="lines">${e.lines.map(l => `${l.debit ? t("debit") : "&nbsp;&nbsp;" + t("credit")} ${esc(l.account)} ${esc(aname(l))} ${money(l.debit || l.credit)}`).join("<br>")}</div></td>
      <td>${esc(e.party)}</td><td>${esc(e.bu_code || "")}</td><td class="num">${money(e.amount)}</td>
      <td>${e.status === "void" ? `<span class="chip bad">${t("void")}</span>` : ""}${e.reversed_by ? `<span class="chip warn">${t("reverse")}</span>` : ""}${e.history && e.history.length ? `<span class="chip">${t("edited")}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
}
async function openVoucher(id) {
  const e = await api("GET", "/api/journal/" + id).catch(() => null) || (S.cache.v && S.cache.v[id]);
  if (!e) return;
  const live = e.status === "posted" && !e.reversed_by && !e.reversal_of;
  const edit = can("accounting.journal", "edit");
  const html = `<div class="row" style="margin-bottom:10px"><span class="chip">${esc(tl(e.kind))}</span><span class="chip">#${e.id}</span><span class="chip">${fd(e.date)}</span>${e.bu_code ? `<span class="chip">${esc(e.bu_code)}</span>` : ""}${e.created_by ? `<span class="chip">${t("created_by")} ${esc(e.created_by)}</span>` : ""}</div>
    <p><b>${esc(e.memo)}</b>${e.party ? ` — ${esc(e.party)}` : ""}${e.ref ? ` · ${esc(e.ref)}` : ""}</p>
    <div class="tbl"><table><thead><tr><th>${t("account")}</th><th class="num">${t("debit")}</th><th class="num">${t("credit")}</th></tr></thead><tbody>
    ${e.lines.map(l => `<tr><td>${esc(l.account)} ${esc(aname(l))}</td><td class="num">${l.debit ? money(l.debit) : ""}</td><td class="num">${l.credit ? money(l.credit) : ""}</td></tr>`).join("")}
    <tr class="total"><td>${t("total")}</td><td class="num">${money(e.amount)}</td><td class="num">${money(e.amount)}</td></tr></tbody></table></div>
    ${e.meta && e.meta.void_reason ? `<div class="banner err" style="margin-top:10px">${t("void")}: ${esc(e.meta.void_reason)}</div>` : ""}
    ${e.history && e.history.length ? `<h4>${t("history")}</h4>${e.history.map(h => `<div class="lines">${esc(h.edited_at)} ${esc(h.edited_by || "")}: ${esc(h.memo)} — ${h.lines.map(l => `${l.account} ${l.debit || ""}/${l.credit || ""}`).join(", ")}</div>`).join("")}` : ""}
    <p class="muted" style="font-size:.8em;margin-top:12px">${S.lang === "ar" ? "الإلغاء يستبعد القيد من كل الأرصدة مع إبقاء أثره؛ القيد العكسي هو التصحيح المحاسبي الرسمي." : "Void removes the entry from every total but keeps a visible trace. Reverse posts the formal correcting entry — both stay on record."}</p>`;
  const btns = [];
  if (edit && live) {
    btns.push(`<button class="btn danger" type="button" data-a="vvoid" data-id="${e.id}">${t("void")}</button>`, `<button class="btn" type="button" data-a="vrev" data-id="${e.id}">${t("reverse")}</button>`);
    if (!["Goods Receipt", "Material Issue", "Stock Transfer", "Stock Adjustment", "Sales Invoice"].includes(e.kind)) btns.push(`<button class="btn" type="button" data-a="vedit" data-id="${e.id}">${t("edit")}</button>`);
  }
  if (edit && e.status === "void") btns.push(`<button class="btn danger" type="button" data-a="vdel" data-id="${e.id}">${t("delete")}</button>`);
  S.H.vvoid = async () => { const r = prompt(t("void_reason")); if (r === null) return; try { await api("POST", `/api/journal/${e.id}/void`, { reason: r }); closeDrawer(); toast(t("saved")); route(); } catch (x) { fail(x); } };
  S.H.vrev = async () => { try { await api("POST", `/api/journal/${e.id}/reverse`, {}); closeDrawer(); toast(t("posted")); route(); } catch (x) { fail(x); } };
  S.H.vdel = async () => { if (!confirm(t("confirm_delete"))) return; try { await api("DELETE", `/api/journal/${e.id}`); closeDrawer(); route(); } catch (x) { fail(x); } };
  S.H.vedit = () => journalForm(e);
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
    { k: "vat_code", l: t("vat_code"), type: "select", opts: taxOpts("vat"), change: "calc" }, { k: "wht_code", l: t("wht_code"), type: "select", opts: taxOpts("wht"), change: "calc" },
    { k: "paid", l: t("paid_now"), type: "number", input: "calc", help: paidSplitHelp() }, { k: "method", l: t("method"), type: "select", opts: methodOpts() },
    { html: `<div class="banner info" id="calc"></div>`, span: "spanall" }],
    calc: f => { const g = n => Number($(`[name=${n}]`, f).value) || 0; const tot = g("qty") * g("rate") - g("savings");
      const vat = Math.round(tot * rateOf($("[name=vat_code]", f).value) * 100) / 100, wht = Math.round(tot * rateOf($("[name=wht_code]", f).value) * 100) / 100;
      $("#calc", f).innerHTML = `${t("net_cost")}: <b>${money(tot)}</b> · ${t("vat_amt")}: ${money(vat)} · ${t("wht_amt")}: (${money(wht)}) · ${t("unpaid")}: <b>${money(tot + vat - wht - g("paid"))}</b>`; },
    open: f => partyDefaults(f, "vendor") },
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
    { k: "date", l: t("date"), type: "date", v: today(), req: 1 }, { k: "partner_id", l: t("partner"), type: "select", opts: S.boot.partners.map(p => [p.id, p.name]), req: 1 },
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
async function openDoc(type, pre) {
  if (type === "invoice") { location.hash = "#/invoice/new"; return; }
  if (type === "offer") { location.hash = "#/offers"; return; }
  if (type === "journal") return journalForm();
  if (type === "bu") return buForm();
  const d = DOCS[type]; if (!d) return;
  let names = [];
  try { names = (await api("GET", "/api/parties?kind=all")).names; } catch { /* no permission */ }
  const items = S.boot.items.map(i => i.name);
  drawer(t(d.title) + " — " + t("new"), form(d.fields(pre)) + unitsList() + datalist("parties", names) + datalist("items", items),
    async v => { await api("POST", "/api/docs/" + type, v); toast(t("posted")); },
    { onOpen: f => { if (d.calc) { S.H.calc = () => d.calc(f); d.calc(f); } if (d.open) d.open(f); } });
}
function journalLinesEditor(lines) {
  const opts = S.boot.accounts.filter(a => a.active).map(a => `<option value="${a.code}">${esc(a.code)} ${esc(aname(a))}</option>`).join("");
  return `<div class="tbl"><table id="jl"><thead><tr><th>${t("account")}</th><th class="num">${t("debit")}</th><th class="num">${t("credit")}</th><th></th></tr></thead><tbody>
    ${lines.map(l => `<tr><td><select data-skip="1" class="ja">${opts.replace(`value="${l.account}"`, `value="${l.account}" selected`)}</select></td>
      <td><input data-skip="1" class="jd num" type="number" step="any" value="${l.debit || ""}" data-input="jbal"></td><td><input data-skip="1" class="jc num" type="number" step="any" value="${l.credit || ""}" data-input="jbal"></td>
      <td><button type="button" class="btn sm danger" data-a="jrm">✕</button></td></tr>`).join("")}</tbody></table></div>
    <div class="row" style="margin-top:8px"><button type="button" class="btn sm" data-a="jadd">${t("add_line")}</button><span class="spacer"></span><span id="jbal"></span></div>`;
}
function journalForm(e) {
  const lines = e ? e.lines : [{ account: "", debit: "", credit: "" }, { account: "", debit: "", credit: "" }];
  const tpl = S.boot.enums.journal_templates;
  const html = form([
    { k: "date", l: t("date"), type: "date", v: e ? e.date : today(), req: 1 },
    ...(e ? [] : [{ k: "template", l: t("template"), type: "select", opts: [["", "—"], ...Object.keys(tpl).map(k => [k, k])], change: "jtpl" }]),
    { k: "bu_id", l: t("bu"), type: "select", opts: buOpts("—"), v: e ? e.bu_id : "" }, { k: "party", l: t("party"), v: e ? e.party : "" },
    { k: "memo", l: t("memo"), v: e ? e.memo : "", span: "spanall" }]) + `<div style="margin-top:12px">${journalLinesEditor(lines)}</div>`;
  const collect = f => $$("#jl tbody tr", f).map(tr => ({ account: $(".ja", tr).value, debit: $(".jd", tr).value, credit: $(".jc", tr).value }));
  drawer((e ? t("edit") : t("new")) + " — " + t("m_journal"), html, async (v, f) => {
    const body = { ...v, lines: collect(f) };
    if (e) await api("PUT", "/api/journal/" + e.id, body); else await api("POST", "/api/journal", { ...body, kind: v.template ? "Journal Voucher" : "Journal Voucher" });
    toast(t("saved"));
  }, { onOpen: f => {
    const bal = () => { const L = collect(f); const d = L.reduce((s, l) => s + (+l.debit || 0), 0), c = L.reduce((s, l) => s + (+l.credit || 0), 0);
      $("#jbal", f).innerHTML = `${t("debit")} ${money(d)} · ${t("credit")} ${money(c)} — <span class="${Math.abs(d - c) < 0.005 && d > 0 ? "bal-ok" : "bal-bad"}">${Math.abs(d - c) < 0.005 && d > 0 ? t("balanced") : t("unbalanced")}</span>`; };
    S.H.jbal = bal; bal();
    S.H.jadd = () => { const tb = $("#jl tbody", f); tb.insertAdjacentHTML("beforeend", tb.lastElementChild.outerHTML); $$("input", tb.lastElementChild).forEach(i => { i.value = ""; }); bal(); };
    S.H.jrm = el => { if ($$("#jl tbody tr", f).length > 2) el.closest("tr").remove(); bal(); };
    S.H.jtpl = el => { const x = tpl[el.value]; if (!x) return; const rows = $$("#jl tbody tr", f);
      const code = r => accByRole(r) || (S.boot.accounts.find(a => a.subtype === r) || {}).code || "";
      $(".ja", rows[0]).value = code(x[0]); $(".ja", rows[1]).value = code(x[1]); if (!$("[name=memo]", f).value) $("[name=memo]", f).value = el.value; };
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
    <div class="banner ${cp.gap > 0 ? "err" : "ok"}">${t("cash_floor")}: ${money(cp.floor)} (${t("floor_note")}) · ${t("cash")} ${money(cp.cash)}${cp.gap > 0 ? ` · <b>−${money(cp.gap)}</b>` : " ✓"}</div>
    <div class="grid g2"><div class="card"><h2>${t("last12")}</h2><div class="legend"><span class="r">${t("rev")}</span><span class="c">${t("costs")}</span></div>
      <div class="bar">${d.series.map(s => `<div class="m"><div class="b"><i class="r" style="height:${(s.revenue / max * 100).toFixed(1)}%" title="${money(s.revenue)}"></i><i class="c" style="height:${(s.costs / max * 100).toFixed(1)}%" title="${money(s.costs)}"></i></div>${esc(s.month.slice(2))}</div>`).join("") || `<div class="empty">${t("nothing")}</div>`}</div></div>
      <div class="card"><h2>${t("model_audit")}</h2>${d.audit.map(a => `<div class="row" style="justify-content:space-between;padding:3px 0"><span>${esc(a.check)}</span><span class="chip ${a.ok ? "ok" : "bad"}">${a.ok ? "OK" : "CHECK"}</span></div>`).join("")}</div></div>
    <div class="card"><h2>${t("m_bus")}</h2>${buTable(d.units)}</div>`;
};
function buTable(rows) {
  if (!rows.length) return `<div class="empty">${t("nothing")}</div>`;
  return `<div class="tbl"><table><thead><tr><th>${t("code")}</th><th>${t("name")}</th><th>${t("basis")}</th><th class="num">${t("rev")}</th><th class="num">${t("direct_costs")}</th><th class="num">${t("direct_overhead")}</th><th class="num">${t("allocated_overhead")}</th><th class="num">${t("net_profit")}</th><th class="num">${t("margin")}</th><th class="num">${t("to_collect")}</th></tr></thead><tbody>
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
      ${k(t("net_profit"), b.net_profit, b.net_profit < 0 ? "neg" : "pos")}${k(t("receivable"), b.receivable)}${k(t("expected_billing"), b.expected_billing)}${k(t("to_collect"), b.to_collect)}</div>` +
    filterBar(q, ["dates", ["kind", S.boot.enums.entry_kinds], "q", "party", "account", "status"]) +
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
      filterBar(q, ["dates", "bu", "q", "party", ...(kinds.length > 1 ? [["kind", kinds]] : []), "status"]) +
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
  const tot = d.rows.reduce((s, r) => s + r.balance, 0);
  view.innerHTML = `<div class="toolbar"><span class="spacer"></span>${exportMenu("payables", q)}</div>` +
    filterBar(q, ["asof", ["select", "kind", t("type"), [["payable", t("payable")], ["payroll", t("m_payroll")]]]]) +
    `<div class="card"><h2>${t("m_payables")} — ${money(tot)}</h2>${d.rows.length ? `<div class="tbl"><table><thead><tr><th>${t("party")}</th><th>${t("account")}</th><th class="num">${t("amount")}</th><th class="num">${t("paid_now")}</th><th class="num">${t("balance")}</th><th>${t("date")}</th><th></th></tr></thead><tbody>
      ${d.rows.map(r => `<tr class="click" data-href="#/ledger?${qs({ party: r.party, account: r.account })}"><td>${esc(r.party || "—")}</td><td>${esc(r.account)}</td><td class="num">${money(r.owed)}</td><td class="num">${money(r.paid)}</td><td class="num ${r.balance < 0 ? "neg" : ""}"><b>${money(r.balance)}</b></td><td>${fd(r.last_date)}</td>
      <td>${can("purchasing.payments", "edit") && r.balance > 0 ? `<button class="btn sm" data-a="pay" data-p="${esc(r.party)}" data-b="${r.balance}" data-acc="${r.account}">${t("m_payments")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}</div>
    ${d.deals.length ? `<div class="card"><h2>${t("deal_ref")}</h2><div class="tbl"><table><thead><tr><th>${t("deal_ref")}</th><th>${t("supplier")}</th><th class="num">${t("amount")}</th><th class="num">${t("savings")}</th><th class="num">${t("net_cost")}</th><th>${t("status")}</th></tr></thead><tbody>
      ${d.deals.map(g => `<tr><td>${esc(g.ref)}</td><td>${esc(g.vendor)}</td><td class="num">${money(g.quoted)}</td><td class="num">${money(g.savings)}</td><td class="num">${money(g.cost)}</td><td>${esc(g.status)}</td></tr>`).join("")}</tbody></table></div></div>` : ""}`;
};
PAGES.receivables = async (view, parts, q) => reportPage(view, "receivables", q, ["dates"]);

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
      <td><input class="l-description" value="${esc(l.description)}"><input type="hidden" class="l-sub" value="${esc(l.sub || "")}"></td><td><input class="l-unit" value="${esc(l.unit)}" style="width:70px" list="units"></td>
      ${withContract ? `<td class="num">${l.contract_qty == null ? "—" : numf(l.contract_qty)}</td><td class="num">${numf(l.billed_before || 0)}</td>` : ""}
      <td><input class="l-qty num" type="number" step="any" value="${l.qty ?? 0}" data-input="letot" style="width:90px"></td><td><input class="l-price num" type="number" step="any" value="${l.price ?? 0}" data-input="letot" style="width:110px"></td>
      ${withCost ? `<td><input class="l-cost num" type="number" step="any" value="${l.cost ?? 0}" data-input="letot" style="width:110px"></td>` : ""}
      <td class="num l-amt">${money((l.qty || 0) * (l.price || 0))}</td><td><button class="btn sm danger" data-a="lerm">✕</button></td></tr>`).join("")}</tbody></table></div>`;
}
function readLines() {
  return $$("#le tbody tr").map(tr => { const g = c => { const el = $(".l-" + c, tr); return el ? el.value : ""; };
    return { item_no: g("item_no"), category: g("category"), sub: g("sub"), description: g("description"), unit: g("unit"), qty: +g("qty") || 0, price: +g("price") || 0, cost: +g("cost") || 0 }; })
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
  if (isNew && q.boq_id) { const b = await api("GET", "/api/boqs/" + q.boq_id); x.bu_id = b.bu_id; x.lines = b.lines.map(l => ({ ...l, contract_qty: l.qty, qty: 0 })); }
  const boqs = can("estimating.boqs") ? await api("GET", "/api/boqs") : [];
  const locked = x.status === "posted" || !can("sales.invoices", isNew ? "add" : "edit");
  const render = () => {
    const withContract = !!x.boq_id;
    view.innerHTML = `<div class="toolbar"><a class="btn" href="#/invoices">← ${t("m_invoices")}</a><span class="spacer"></span>
        ${!isNew ? docMenu(`/api/extracts/${x.id}/file`, [["xlsx", "excel_doc"], ["pdf", "pdf_print"]], `<a href="/api/reports/vouchers?${qs({ q: x.number, kinds: "Sales Invoice", format: "pdf", lang: S.lang })}" target="_blank" rel="noopener">${S.lang === "ar" ? "القيد المحاسبي (طباعة)" : "Accounting voucher (print)"}</a>`) : ""}
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
PAGES.boq = async (view, parts) => {
  const isNew = parts[1] === "new";
  let b = isNew ? { title: "", rev: "Rev 01", date: today(), bu_id: "", client: "", location: "", lines: [] } : await api("GET", "/api/boqs/" + parts[1]);
  const ro = !can("estimating.boqs", isNew ? "add" : "edit");
  const render = () => {
    view.innerHTML = `<div class="toolbar"><a class="btn" href="#/boqs">← ${t("m_boqs")}</a><span class="spacer"></span>
      ${!isNew ? docMenu(`/api/boqs/${b.id}/file`, [["xlsx", "excel_doc"], ["pdf", "pdf_print"]]) : ""}
      ${!isNew && can("sales.invoices", "add") ? `<a class="btn" href="#/invoice/new?boq_id=${b.id}">${t("new_invoice_from")}</a>` : ""}
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
  view.innerHTML = `<div class="toolbar">${ed ? ["Receipt", "Issue", "Transfer", "Adjustment"].map(ty => `<button class="btn ${ty === "Receipt" ? "pri" : ""}" data-a="sm${ty}">${tl(ty)}</button>`).join("") : ""}<span class="spacer"></span>${exportMenu("stock", q)}</div>` +
    filterBar(q, ["warehouse", "q"]) +
    `<div class="card"><h2>${t("inventory")} — ${money(tot)} ${cur()}</h2>${d.levels.length ? `<div class="tbl"><table><thead><tr><th>${t("item")}</th><th>${t("warehouse")}</th><th>${t("unit")}</th><th class="num">${t("on_hand")}</th><th class="num">${t("avg_cost")}</th><th class="num">${t("value")}</th><th></th></tr></thead><tbody>
      ${d.levels.map(r => `<tr><td>${esc(r.name)} ${r.low ? `<span class="chip warn">${t("reorder")}</span>` : ""}</td><td>${esc(r.warehouse)}</td><td>${esc(r.unit)}</td><td class="num ${r.qty < 0 ? "neg" : ""}">${numf(r.qty)}</td><td class="num">${money(r.avg_cost)}</td><td class="num">${money(r.value)}</td>
      <td>${ed ? `<button class="btn sm" data-a="smIssue" data-item="${esc(r.name)}" data-wh="${r.warehouse_id}">${tl("Issue")}</button> <button class="btn sm" data-a="smAdjustment" data-item="${esc(r.name)}" data-wh="${r.warehouse_id}">${tl("Adjustment")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}</div>`;
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

// --------------------------------------------------------------- partners
PAGES.partners = async (view, parts, q) => {
  const d = await api("GET", "/api/partners?" + qs(q));
  const ed = can("partners.equity", "edit");
  const shareOf = (pid, bid, def) => { const s = d.shares.find(x => x.partner_id === pid && x.bu_id === bid); return s ? s.share : def; };
  const edit = p => drawer(t("partner"), form([{ k: "name", l: t("name"), v: p.name, req: 1 }, { k: "default_share", l: t("default_share"), type: "number", v: p.default_share != null ? +(p.default_share * 100).toFixed(4) : "" },
    { html: `<h4>${t("ownership")}</h4>`, span: "spanall" }, ...d.units.map(u => ({ k: "s_" + u.id, l: u.code + " %", type: "number", v: p.id ? +(shareOf(p.id, u.id, p.default_share) * 100).toFixed(4) : "" }))]),
  async v => { const body = { name: v.name, default_share: (+v.default_share || 0) / 100, shares: {} };
    d.units.forEach(u => { body.shares[u.id] = v["s_" + u.id] === "" ? "" : (+v["s_" + u.id] || 0) / 100; });
    if (p.id) await api("PUT", "/api/partners/" + p.id, body); else { const r = await api("POST", "/api/partners", body); await api("PUT", "/api/partners/" + r.id, body); }
    await refreshBoot(); toast(t("saved")); });
  S.H.pnew = () => edit({});
  S.H.pedit = el => edit({ ...d.partners.find(x => String(x.id) === el.dataset.id) });
  S.H.ptx = () => openDoc("partner_tx");
  S.H.rsave = async () => { try { await api("PUT", "/api/settings", { reserve_pct: (+$("#rpct").value || 0) / 100 }); await refreshBoot(); route(); } catch (e) { fail(e); } };
  const cp = d.cash_position;
  view.innerHTML = `<div class="toolbar">${can("partners.transactions", "add") ? `<button class="btn pri" data-a="ptx">+ ${t("drawing")} / ${t("capital")}</button>` : ""}${ed ? `<button class="btn" data-a="pnew">+ ${t("partner")}</button>` : ""}<span class="spacer"></span>
      ${exportMenu("vouchers", { kinds: "Owner Drawing,Capital Contribution", title: t("m_partners") })}</div>` + filterBar(q, ["dates"]) +
    `<div class="card"><h2>${t("reserve_pct")}</h2><div class="row"><input id="rpct" type="number" step="1" min="0" max="100" value="${+(d.reserve_pct * 100).toFixed(2)}" style="max-width:120px" ${ed ? "" : "disabled"}>
      ${ed ? `<button class="btn" data-a="rsave">${t("save")}</button>` : ""}<span class="muted">${t("company_result")}: ${money(d.company_result)}</span></div>
      <div class="banner ${cp.gap > 0 ? "err" : "ok"}" style="margin-top:10px">${t("cash_floor")}: ${money(cp.floor)} · ${t("cash")}: ${money(cp.cash)}${cp.gap > 0 ? ` — ${S.lang === "ar" ? "لا يوجد ما يمكن توزيعه بأمان حتى تُسد الفجوة" : "nothing is safely distributable until this gap closes"} (${money(cp.gap)})` : " ✓"}</div></div>
    <div class="card"><div class="tbl"><table><thead><tr><th>${t("partner")}</th>${d.units.map(u => `<th class="num">${esc(u.code)}</th>`).join("")}<th class="num">${t("profit_share")}</th><th class="num">${t("distributable")}</th><th class="num">${t("drawn")}</th><th class="num">${t("balance")}</th></tr></thead><tbody>
      ${d.partners.map(p => `<tr class="click" data-a="${ed ? "pedit" : ""}" data-id="${p.id}"><td>${esc(p.name)}<div class="lines">${t("default")} ${pct(p.default_share)}</div></td>${d.units.map(u => `<td class="num">${pct(p.units[u.code].share)}<div class="lines">${money(p.units[u.code].profit)}</div></td>`).join("")}
        <td class="num">${money(p.profit_share)}</td><td class="num">${money(p.distributable)}</td><td class="num">${money(p.drawn)}</td><td class="num ${p.balance < 0 ? "neg" : "pos"}"><b>${money(p.balance)}</b></td></tr>`).join("")}
      <tr class="total"><td>${t("total")}</td>${d.units.map(u => `<td class="num ${Math.abs(d.share_checks[u.code] - 1) < 0.001 ? "pos" : "neg"}">${pct(d.share_checks[u.code])}</td>`).join("")}<td class="num">${money(d.partners.reduce((s, p) => s + p.profit_share, 0))}</td><td class="num">${money(d.partners.reduce((s, p) => s + p.distributable, 0))}</td><td class="num">${money(d.partners.reduce((s, p) => s + p.drawn, 0))}</td><td class="num">${money(d.partners.reduce((s, p) => s + p.balance, 0))}</td></tr>
      </tbody></table></div></div>`;
};

// ------------------------------------------------------------- accounting
PAGES.journal = async (view, parts, q) => {
  const lq = { ...q, limit: q.limit || 50 };
  const d = await api("GET", "/api/journal?" + qs(lq));
  S.H.newjv = () => journalForm();
  view.innerHTML = `<div class="toolbar">${can("accounting.journal", "add") ? `<button class="btn pri" data-a="newjv">+ ${t("m_journal")}</button>` : ""}<span class="spacer"></span>
    <span class="chip">${d.total}</span><span class="chip">${money(d.posted_amount)} ${cur()}</span>${exportMenu("vouchers", { ...q, title: t("m_journal") })}</div>` +
    filterBar(q, ["dates", "bu", ["kind", S.boot.enums.entry_kinds], "account", "q", "party", "status"]) +
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
  async v => { if (a.used) v.type = a.type; if (a.code) await api("PUT", "/api/accounts/" + encodeURIComponent(a.code), v); else await api("POST", "/api/accounts", v); await refreshBoot(); toast(t("saved")); },
  { extraButtons: a.code && !a.role && !a.used && can("accounting.coa", "edit") ? `<button type="button" class="btn danger" data-a="adel">${t("delete")}</button>` : "",
    onOpen: f => { S.H.atype = () => { const ty = $("[name=type]", f).value; const s = $("[name=subtype]", f); s.innerHTML = S.boot.enums.subtypes[ty].map(x => `<option value="${x}" ${x === a.subtype ? "selected" : ""}>${esc(sub(x))}</option>`).join(""); }; S.H.atype(); } });
  S.H.anew = () => edit({ type: "Expense", active: 1 });
  S.H.aedit = el => { const a = rows.find(x => x.code === el.dataset.id); S.H.adel = async () => { try { await api("DELETE", "/api/accounts/" + encodeURIComponent(a.code)); closeDrawer(); await refreshBoot(); route(); } catch (e) { fail(e); } }; edit(a); };
  const ed = can("accounting.coa", "edit");
  view.innerHTML = `<div class="toolbar">${can("accounting.coa", "add") ? `<button class="btn pri" data-a="anew">+ ${t("new")}</button>` : ""}<span class="spacer"></span>${exportMenu("trial_balance", {})}</div>` +
    filterBar(q, ["asof"]) + types.map(ty => `<div class="card"><h2>${esc(tl(ty))}</h2><div class="tbl"><table><thead><tr><th>${t("code")}</th><th>${t("account")}</th><th>${t("statement_line")}</th><th>${t("normal")}</th><th class="num">${t("balance")}</th></tr></thead><tbody>
    ${rows.filter(a => a.type === ty).map(a => `<tr class="click ${a.active ? "" : "void"}" data-a="${ed ? "aedit" : ""}" data-id="${esc(a.code)}"><td><b>${esc(a.code)}</b></td><td>${esc(aname(a))}${S.lang === "ar" ? "" : a.name_ar ? ` <span class="muted">${esc(a.name_ar)}</span>` : ""} ${a.role ? `<span class="chip">${esc(a.role)}</span>` : ""}</td><td>${esc(sub(a.subtype))}</td><td>${tl(a.normal) === a.normal ? (S.lang === "ar" ? (a.normal === "Debit" ? "مدين" : "دائن") : a.normal) : ""}</td><td class="num ${a.balance < 0 ? "neg" : ""}">${a.balance ? money(a.balance) : "—"}</td></tr>`).join("")}</tbody></table></div></div>`).join("");
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
    if (s.kind === "bank") body = `<div class="row">${style(t("as_lines"), t("as_table"))}<span class="muted">${S.lang === "ar" ? "تُستخدم بيانات البنك من الإعدادات ← الشركة، ويُخفى القسم إن كانت فارغة." : "Uses the bank details in Settings → Company; hidden when they are empty."}</span></div>`;
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
PAGES.parties = async (view, parts, q) => {
  const lq = { ...q, limit: q.limit || 100 };
  const d = await api("GET", "/api/parties-master?" + qs(lq));
  const ed = can("sales.parties", "edit") || can("sales.invoices", "edit") || can("purchasing", "edit");
  const add = ed || can("sales.parties", "add") || can("sales.invoices", "add") || can("purchasing", "add");
  const edit = p => drawer(t("m_parties"), form([{ k: "name", l: t("name"), v: p.name, req: 1 },
    { k: "type", l: t("type"), type: "select", opts: [["customer", t("customer")], ["supplier", t("supplier")], ["both", `${t("customer")} + ${t("supplier")}`], ["employee", t("worker")], ["other", "—"]], v: p.type },
    { k: "receiver_type", l: t("receiver_type"), type: "select", opts: ["B", "P", "F"].map(x => [x, t("rt_" + x)]), v: p.receiver_type || "B" },
    { k: "tax_id", l: t("tax_id"), v: p.tax_id, ph: "123456789" }, { k: "national_id", l: t("national_id"), v: p.national_id },
    { k: "country", l: t("country"), v: p.country || "EG" }, { k: "governate", l: t("governate"), v: p.governate }, { k: "region", l: t("region"), v: p.region },
    { k: "street", l: t("street"), v: p.street }, { k: "building", l: t("building"), v: p.building }, { k: "postal_code", l: t("postal"), v: p.postal_code },
    { k: "email", l: t("email"), v: p.email, type: "email" }, { k: "phone", l: t("phone"), v: p.phone },
    { k: "default_vat", l: t("default_vat"), type: "select", opts: taxOpts("vat"), v: p.default_vat }, { k: "default_wht", l: t("default_wht"), type: "select", opts: taxOpts("wht"), v: p.default_wht },
    { k: "notes", l: t("notes"), type: "textarea", v: p.notes, span: "spanall" }]),
  async v => { if (p.id) await api("PUT", "/api/parties-master/" + p.id, v); else await api("POST", "/api/parties-master", v); await refreshBoot(); toast(t("saved")); });
  S.H.pnew = () => edit({});
  S.H.pedit = el => edit(d.rows.find(x => String(x.id) === el.dataset.id));
  view.innerHTML = `<div class="toolbar">${add ? `<button class="btn pri" data-a="pnew">+ ${t("new")}</button>` : ""}</div>` +
    filterBar(q, ["q", ["select", "type", t("type"), [["customer", t("customer")], ["supplier", t("supplier")]]]]) +
    `<div class="card">${d.rows.length ? `<div class="tbl"><table><thead><tr><th>${t("name")}</th><th>${t("type")}</th><th>${t("tax_id")}</th><th>${t("governate")}</th><th>${t("phone")}</th><th>${t("default_vat")}</th><th>${t("default_wht")}</th></tr></thead><tbody>
      ${d.rows.map(p => `<tr class="click" data-a="${ed ? "pedit" : ""}" data-id="${p.id}"><td><b>${esc(p.name)}</b></td><td>${esc(p.type)}</td><td>${esc(p.tax_id || p.national_id)}</td><td>${esc(p.governate)}</td><td>${esc(p.phone)}</td><td>${esc(p.default_vat)}</td><td>${esc(p.default_wht)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">${t("nothing")}</div>`}${pager(d.total, lq)}</div>`;
};
PAGES.taxcodes = async (view) => {
  const rows = await api("GET", "/api/tax-codes");
  const ed = can("tax.codes", "edit");
  const edit = c => drawer(t("m_taxcodes"), form([{ k: "code", l: t("code"), v: c.code, req: 1 }, { k: "kind", l: t("kind_tax"), type: "select", opts: [["vat", t("vat_code")], ["wht", t("wht_code")]], v: c.kind || "vat" },
    { k: "name", l: t("name") + " (English)", v: c.name, req: 1 }, { k: "name_ar", l: t("name") + " (العربية)", v: c.name_ar },
    { k: "rate", l: t("rate_pct"), type: "number", v: c.rate != null ? +(c.rate * 100).toFixed(4) : "" }, { k: "eta_type", l: t("eta_type"), v: c.eta_type, ph: "T1 / T4" },
    { k: "eta_subtype", l: t("eta_sub"), v: c.eta_subtype, ph: "V009 / W001" }, { k: "active", l: t("active"), type: "checkbox", v: c.active !== 0 }]),
  async v => { v.rate = (+v.rate || 0) / 100; if (c.code) await api("PUT", "/api/tax-codes/" + encodeURIComponent(c.code), v); else await api("POST", "/api/tax-codes", v); await refreshBoot(); toast(t("saved")); });
  S.H.tnew = () => edit({ active: 1 });
  S.H.tedit = el => edit(rows.find(x => x.code === el.dataset.id));
  view.innerHTML = `<div class="toolbar">${can("tax.codes", "add") ? `<button class="btn pri" data-a="tnew">+ ${t("new")}</button>` : ""}</div>
    <div class="banner info">${S.lang === "ar" ? "النسب قابلة للتعديل — راجع النسب السارية مع مستشارك الضريبي." : "Rates are editable — confirm current rates with your tax adviser."}</div>
    <div class="card"><div class="tbl"><table><thead><tr><th>${t("code")}</th><th>${t("name")}</th><th>${t("kind_tax")}</th><th class="num">${t("rate_pct")}</th><th>${t("eta_type")}</th><th>${t("eta_sub")}</th></tr></thead><tbody>
    ${rows.map(c => `<tr class="click ${c.active ? "" : "void"}" data-a="${ed ? "tedit" : ""}" data-id="${esc(c.code)}"><td><b>${esc(c.code)}</b></td><td>${esc(S.lang === "ar" && c.name_ar ? c.name_ar : c.name)}</td><td>${c.kind === "vat" ? t("vat_code") : t("wht_code")}</td><td class="num">${pct(c.rate)}</td><td>${esc(c.eta_type)}</td><td>${esc(c.eta_subtype)}</td></tr>`).join("")}</tbody></table></div></div>`;
};
PAGES.einvoice = async (view, parts, q) => {
  const [st, inv] = await Promise.all([api("GET", "/api/einvoice/settings"), can("sales.invoices") ? api("GET", "/api/extracts?" + qs({ ...q, status: "posted" })) : []]);
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
      ${inv.map(x => `<tr><td>${ed ? `<input type="checkbox" class="etachk" value="${x.id}" ${/valid|submitted/i.test(x.eta_status || "") ? "disabled" : ""}>` : ""}</td><td><a href="#/invoice/${x.id}">${esc(x.number)}</a></td><td>${esc(x.customer)}</td><td>${fd(x.date)}</td><td class="num">${money(x.total)}</td><td>${esc(x.eta_status || "—")}</td></tr>`).join("")}</tbody></table></div>
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
      <div class="card"><h2>${t("bank_details")}</h2>${form([{ k: "bank_name", l: t("bank_name"), v: st.bank_name }, { k: "bank_account", l: t("bank_account"), v: st.bank_account },
        { k: "bank_holder", l: t("bank_holder"), v: st.bank_holder }, { k: "bank_swift", l: t("swift"), v: st.bank_swift }, { k: "bank_iban", l: t("iban"), v: st.bank_iban, span: "span2" }], 3)}</div>
      <div class="card"><h2>${t("numbering")}</h2>${form([{ k: "offer_prefix", l: t("offer_prefix"), v: st.offer_prefix || "OFR-", help: "{YYYY} = year" },
        { k: "offer_digits", l: t("offer_digits"), type: "number", v: st.offer_digits || 4 }, { k: "offer_validity_days", l: t("offer_validity"), type: "number", v: st.offer_validity_days || 15 }], 3)}</div>
      <div class="card" id="regcard"><h2>${t("regional")}</h2>${form([
        { k: "date_format", l: t("date_fmt"), type: "select", opts: [["YYYY-MM-DD", "2026-07-21"], ["DD/MM/YYYY", "21/07/2026"], ["DD MMM YYYY", "21 Jul 2026"]], v: rg.date_format || "YYYY-MM-DD" },
        { k: "negative", l: t("neg_style"), type: "select", opts: [["paren", "(1,234.00)"], ["minus", "-1,234.00"]], v: rg.negative || "paren" },
        { k: "decimals", l: t("decimals"), type: "select", opts: [["2", "1,234.56"], ["0", "1,235"]], v: String(rg.decimals ?? "2") },
        { k: "digits", l: t("digits"), type: "select", opts: [["latin", t("dg_latin")], ["arabic", t("dg_arabic")]], v: rg.digits || "latin" }], 4)}</div>
      ${ed ? `<div class="row"><button class="btn pri" data-a="cosave">${t("save")}</button></div>` : ""}</div>`;
    if (!ed) $$("#cocard input, #cocard select").forEach(e => { e.disabled = true; });
    S.H.cosave = async () => { const v = readForm($("#cocard")); const regional = {};
      ["date_format", "negative", "decimals", "digits"].forEach(k => { regional[k] = v[k]; delete v[k]; });
      v.regional = regional; v.units = v.units.split(",").map(x => x.trim()).filter(Boolean);
      try { await api("PUT", "/api/settings", v); await refreshBoot(); applyLook(); renderShell(); toast(t("saved")); route(); } catch (e) { fail(e); } };
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
    view.innerHTML = head + `<div class="card"><h2>${t("menu_names")}</h2><p class="muted">${t("menu_note")}</p><div class="tbl"><table><thead><tr><th>Key</th><th>English</th><th>العربية</th><th>Custom English</th><th>مخصص عربي</th></tr></thead><tbody>
      ${menuKeys.map(k => `<tr><td class="muted">${k}</td><td>${esc(window.I18N[k][0])}</td><td>${esc(window.I18N[k][1])}</td><td><input data-ml="${k}" data-l="en" value="${esc((labels[k] || {}).en || "")}" ${ed ? "" : "disabled"}></td><td><input data-ml="${k}" data-l="ar" value="${esc((labels[k] || {}).ar || "")}" dir="rtl" ${ed ? "" : "disabled"}></td></tr>`).join("")}</tbody></table></div>
      ${ed ? `<div class="row" style="margin-top:10px"><button class="btn pri" data-a="mlsave">${t("save")}</button></div>` : ""}</div>`;
    S.H.mlsave = async () => { const out = {}; $$("[data-ml]").forEach(i => { if (i.value.trim()) { out[i.dataset.ml] = out[i.dataset.ml] || {}; out[i.dataset.ml][i.dataset.l] = i.value.trim(); } });
      try { await api("PUT", "/api/settings", { menu_labels: out }); await refreshBoot(); renderShell(); route(); toast(t("saved")); } catch (e) { fail(e); } };
    return;
  }
  // system & data
  const sys = can("settings.system") ? await api("GET", "/api/system").catch(() => null) : null;
  const ed = can("settings.system", "edit"), hid = st.hidden_modules || [];
  view.innerHTML = head + (sys ? `<div class="card"><h2>${t("system_info")}</h2><div class="grid g2"><div>Version ${esc(sys.version)}<br><span class="muted">${esc(sys.db_path)}</span><br>${(sys.db_size / 1048576).toFixed(2)} MB</div>
      <div>${Object.entries(sys.counts).map(([k, v]) => `${esc(k)}: ${v}`).join(" · ")}<br><span class="muted">${sys.backups.length} backups: ${esc(sys.backups.slice(0, 3).join(", "))}</span></div></div>
      ${ed ? `<div class="row" style="margin-top:10px"><label class="f">${t("backups_keep")}<input id="bkeep" type="number" value="${esc(st.backup_keep || 14)}" style="max-width:100px"></label><button class="btn" data-a="maint">${t("maintenance")}</button><a class="btn" href="/api/backup">⬇ ${t("db_backup")}</a></div>` : ""}</div>
    <div class="card" id="lockcard"><h2>${t("lock_date")}</h2><div class="row"><input type="date" name="lock_date" value="${esc(st.lock_date || "")}" style="max-width:200px" ${ed ? "" : "disabled"}>${ed ? `<button class="btn" data-a="locksave">${t("save")}</button>` : ""}</div><p class="muted">${t("lock_note")}</p></div>
    <div class="card"><h2>${t("hidden_mods")}</h2><p class="muted">${t("hidden_note")}</p><div class="grid g4">${S.boot.enums.modules.filter(m => !["settings", "users", "dashboard"].includes(m)).map(m => `<label class="f chk"><input type="checkbox" class="hidmod" value="${m}" ${hid.includes(m) ? "checked" : ""} ${ed ? "" : "disabled"}>${t("mod_" + m)}</label>`).join("")}</div>
      ${ed ? `<div class="row" style="margin-top:10px"><button class="btn" data-a="hidsave">${t("save")}</button></div>` : ""}</div>` : "") +
    (S.boot.user.is_admin ? `<div class="card danger-zone" id="erasecard"><h2 class="neg">${t("erase_title")}</h2>
      <div class="banner ${STATIC ? "err" : "info"}">${STATIC ? t("erase_static") + ` <a href="/api/export/json">⬇ JSON</a>` : t("erase_note")}</div>
      <label class="f chk"><input type="radio" name="escope" value="transactions" checked>${t("erase_tx")}</label>
      <label class="f chk"><input type="radio" name="escope" value="all">${t("erase_all")}</label>
      <div class="row" style="margin-top:10px"><input id="econfirm" placeholder="${t("erase_type")}" style="max-width:220px" autocomplete="off"><button class="btn danger" data-a="erase">${t("erase_btn")}</button></div></div>`
      : `<div class="card"><h2>${t("erase_title")}</h2><div class="muted">${t("admin_only")}</div></div>`);
  S.H.maint = async () => { try { await api("PUT", "/api/settings", { backup_keep: $("#bkeep").value }); const r = await api("POST", "/api/maintenance", {}); toast(`${(r.size_before / 1048576).toFixed(2)} → ${(r.size_after / 1048576).toFixed(2)} MB`); route(); } catch (e) { fail(e); } };
  S.H.locksave = async () => { try { await api("PUT", "/api/settings", { lock_date: $("[name=lock_date]").value }); await refreshBoot(); toast(t("saved")); } catch (e) { fail(e); } };
  S.H.hidsave = async () => { try { await api("PUT", "/api/settings", { hidden_modules: $$(".hidmod:checked").map(i => i.value) }); await refreshBoot(); renderShell(); route(); toast(t("saved")); } catch (e) { fail(e); } };
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
  const edit = u => drawer(t("m_users") + (u.username ? " — " + u.username : ""), form([{ k: "username", l: t("username"), v: u.username, req: 1, dis: !!u.id }, { k: "full_name", l: t("full_name"), v: u.full_name },
    { k: "email", l: t("email"), type: "email", v: u.email, span: "spanall", help: cloudOn()
      ? (ar ? "البريد الذي يسجل به هذا الشخص في مساحة العمل (Google أو البريد). عند الحفظ يُضاف كعضو تلقائياً ويمكنه أيضاً الدخول باسم المستخدم." : "The address this person signs in with on the shared workspace (Google or email). Saving adds it to the workspace automatically; they can then also sign in with their username.")
      : (ar ? "اختياري — يُستخدم لتسجيل الدخول إلى مساحة العمل المشتركة عند تفعيلها." : "Optional — used for signing in to the shared workspace when you connect one.") },
    { k: "password", l: t("password") + (u.id ? " (" + (S.lang === "ar" ? "اتركه فارغاً للإبقاء" : "leave blank to keep") + ")" : ""), type: "password", req: !u.id && !cloudOn(), help: cloudOn() ? (S.lang === "ar" ? "مساحة العمل: يسجل الدخول بحسابه في مساحة العمل (بريده أو اسم المستخدم + كلمة مرور حسابه). كلمة المرور هنا لهذا الجهاز فقط (اختيارية)." : "Workspace: they sign in with their workspace account (their email or this username + that account's password). A password here is for this device only (optional).") : "" },
    ...(S.boot.user.is_admin ? [{ k: "is_admin", l: t("admin") + (S.lang === "ar" ? " (كل الصلاحيات)" : " (everything, ignores the matrix)"), type: "checkbox", v: u.is_admin }] : []),
    ...(u.id ? [{ k: "active", l: t("active"), type: "checkbox", v: u.active !== false }] : []),
    { html: `<h4 style="margin:8px 0 0">${t("perm_matrix")}</h4>${permMatrix(u.perms || {})}`, span: "spanall" }]),
  async (v, f) => { const perms = readPerms(f);
    const body = { username: v.username || u.username, full_name: v.full_name, email: v.email, password: v.password, is_admin: v.is_admin, active: v.active, perms, _no_password: !v.password };
    if (u.id) await api("PUT", "/api/users/" + u.id, body); else await api("POST", "/api/users", body);
    toast(t("saved"));
    if (cloudOn() && v.email) {   // let this person into the shared workspace with that email (and username)
      try { await CloudAPI.linkMember({ email: v.email, username: body.username, active: v.active !== false }); }
      catch (e) { toast((ar ? "تم حفظ المستخدم، لكن تعذرت إضافته لمساحة العمل: " : "User saved, but not added to the workspace: ") + e.message, true); }
    } },
  { onOpen: f => wirePerms(f) });
  S.H.unew = () => edit({ perms: { dashboard: "view" } });
  S.H.uedit = el => edit(rows.find(x => String(x.id) === el.dataset.id));
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
  ${admin ? `<div class="card" id="wscfg"><h2>1 · ${ar ? "رابط المشروع" : "Project link"}</h2>
    ${st.preset ? `<p class="muted">${ar ? "هذه النسخة مربوطة مسبقاً بمشروع الشركة." : "This copy of the app comes pre-linked to your company's project."}</p>` : ""}
    ${form([{ k: "url", l: "Project URL", v: st.url, ph: "https://abcdefgh.supabase.co" }, { k: "key", l: ar ? "المفتاح العام (anon public key)" : "anon public key", v: st.key, ph: "eyJhbGciOi…" }], 2)}
    <div class="row" style="margin-top:8px"><button class="btn" data-a="wssave">${t("save")}</button><span class="muted" style="font-size:.85em">Supabase → Project Settings → API</span></div></div>
  <div class="card" id="wsin"><h2>2 · ${S.wsCreate ? (ar ? "إنشاء حساب" : "Create an account") : (ar ? "تسجيل الدخول" : "Sign in")}</h2>
    <div class="grid g2"><label class="f">${t("email")}<input name="email" type="email" value="${esc(st.email || "")}"></label><div></div>
      ${pwFields(!!S.wsCreate)}</div>
    <div class="row" style="margin-top:8px">${S.wsCreate ? `<button class="btn pri" data-a="wsup">${ar ? "إنشاء الحساب" : "Create account"}</button><a href="#" data-a="wsmode">${ar ? "لدي حساب — تسجيل الدخول" : "I have an account — sign in"}</a>`
      : `<button class="btn" data-a="wsin">${t("sign_in")}</button><a href="#" data-a="wsmode">${ar ? "ليس لديك حساب؟ إنشاء حساب" : "No account yet? Create one"}</a>`}</div></div>
  <div class="card"><h2>3 · ${ar ? "الانضمام" : "Join"}</h2>${info && !info.error ? `
    ${!info.is_member && !info.has_members ? `<p>${ar ? "مساحة العمل فارغة. أنشئها من دفاتر هذا الجهاز لتصبح المسؤول." : "The workspace is empty. Create it from this device's books — you become its administrator."}</p><button class="btn pri" data-a="wsclaim">${ar ? "إنشاء مساحة العمل من هذا الجهاز" : "Create the workspace from this device"}</button>` : ""}
    ${!info.is_member && info.has_members ? `<div class="banner err">${ar ? "هذا الحساب ليس عضواً. اطلب من المسؤول إضافته." : "This account is not a member yet — ask an administrator to add it."}</div>` : ""}
    ${info.is_member ? `<div class="row">${!st.joined ? `<button class="btn pri" data-a="wsjoin">${ar ? "الانضمام (يستبدل بيانات هذا الجهاز)" : "Join (replaces this device's books)"}</button>` : `<button class="btn" data-a="wspull">${ar ? "مزامنة الآن" : "Sync now"}</button>`}
      <button class="btn danger" data-a="wsoff">${ar ? "فصل هذا الجهاز" : "Disconnect this device"}</button></div>
      ${!STATIC && !info.gateway ? `<p class="muted" style="font-size:.85em;margin-top:8px">${ar ? "لخادم مكتبي يخدم عدة مستخدمين محليين: فعّل «بوابة» لهذا الحساب في قائمة الأعضاء حتى تُسجل التعديلات بأسماء المستخدمين الفعليين." : "Desktop server for several local users: tick “gateway” for this account in the member list so changes are recorded under each local user rather than this account."}</p>` : ""}` : ""}`
    : `<div class="muted">${ar ? "سجّل الدخول أولاً." : "Sign in first."}</div>`}</div>` : `<div class="banner info">${t("admin_only")}</div>`}
  ${info && info.is_admin ? `<div class="card"><h2>${ar ? "الأعضاء" : "Members"}</h2><p class="muted" style="font-size:.85em">${ar ? "كل عضو يسجل الدخول ببريده. أضف له أيضاً مستخدماً في التطبيق بنفس البريد كاسم مستخدم لتحديد صلاحياته (يتم ذلك تلقائياً هنا)." : "Each member signs in with their email. Adding a member here also creates their app user (username = email) — set their permissions under Users."}</p>
    <div class="tbl"><table><thead><tr><th>${t("email")}</th><th>${t("username")}</th><th>${t("admin")}</th><th>Gateway</th><th>${t("active")}</th><th></th></tr></thead><tbody>
    ${members.map(m => `<tr><td>${esc(m.email)}</td><td>${esc(m.username || "—")}</td><td><input type="checkbox" data-mf="is_admin" data-e="${esc(m.email)}" ${m.is_admin ? "checked" : ""}></td><td><input type="checkbox" data-mf="gateway" data-e="${esc(m.email)}" ${m.gateway ? "checked" : ""}></td>
      <td><input type="checkbox" data-mf="active" data-e="${esc(m.email)}" ${m.active ? "checked" : ""}></td><td><button class="btn sm" data-a="wsmsave" data-e="${esc(m.email)}">${t("save")}</button></td></tr>`).join("")}
    <tr><td><input id="wsnew" type="email" placeholder="name@company.com"></td><td><input id="wsnewuser" placeholder="${t("username")}"></td><td><input type="checkbox" id="wsnewadm"></td><td><input type="checkbox" id="wsnewgw"></td><td></td><td><button class="btn sm pri" data-a="wsmadd">+ ${t("new")}</button></td></tr></tbody></table></div></div>` : ""}`;
  const run = async (fn, okMsg) => { try { await fn(); if (okMsg) toast(okMsg); await refreshBoot(); renderShell(); route(); } catch (e) { fail(e); } };
  S.H.wssave = () => { const v = readForm($("#wscfg")); run(() => CloudAPI.config(v.url, v.key), t("saved")); };
  if ($("#wsin")) pwWire($("#wsin"));
  S.H.wsmode = () => { S.wsCreate = !S.wsCreate; route(); };
  S.H.wsin = () => { const v = readForm($("#wsin")); run(() => CloudAPI.signin(v.email, v.password, false)); };
  S.H.wsup = () => { const box = $("#wsin"); S.H.pwmatch(); const bad = $$("input", box).find(i => !i.checkValidity()); if (bad) { bad.reportValidity(); return; }
    const v = readForm(box); run(async () => { await CloudAPI.signin(v.email, v.password, true); S.wsCreate = false; }); };
  S.H.wsclaim = () => confirm(ar ? "إنشاء مساحة العمل من دفاتر هذا الجهاز؟" : "Create the workspace from this device's books?") && run(() => CloudAPI.claim(), t("saved"));
  S.H.wsjoin = () => confirm(ar ? "سيتم استبدال بيانات هذا الجهاز ببيانات مساحة العمل. متابعة؟" : "This device's books will be replaced by the workspace's. Continue?") && run(async () => {
    await CloudAPI.join(); if (!STATIC) { toast(ar ? "تم الانضمام — سجّل الدخول مجدداً" : "Joined — please sign in again"); setTimeout(() => location.reload(), 1200); } });
  S.H.wspull = () => run(() => CloudAPI.pull(), ar ? "تمت المزامنة" : "Synced");
  S.H.wsoff = () => confirm(ar ? "فصل هذا الجهاز؟ تبقى البيانات الحالية عليه." : "Disconnect this device? Its current books stay on it.") && run(() => CloudAPI.disconnect(), t("saved"));
  const mrow = e => ({ email: e, is_admin: $(`[data-mf=is_admin][data-e="${e}"]`).checked, gateway: $(`[data-mf=gateway][data-e="${e}"]`).checked, active: $(`[data-mf=active][data-e="${e}"]`).checked });
  S.H.wsmsave = el => run(() => CloudAPI.setMember(mrow(el.dataset.e)), t("saved"));
  S.H.wsmadd = () => { const email = $("#wsnew").value.trim().toLowerCase(); if (!email) return;
    run(async () => { await CloudAPI.setMember({ email, is_admin: $("#wsnewadm").checked, gateway: $("#wsnewgw").checked, active: true });
      const username = ($("#wsnewuser").value.trim() || email.split("@")[0]).toLowerCase();
      await api("POST", "/api/users", { username, email, full_name: email.split("@")[0], is_admin: $("#wsnewadm").checked, _no_password: true, perms: { dashboard: "view" } }).catch(() => {});
      await CloudAPI.linkMember({ email, username, active: true }).catch(() => {}); }, t("saved")); };
}

// ------------------------------------------------------------------ boot
async function refreshBoot() { S.boot = await api("GET", "/api/bootstrap"); }
async function boot() {
  try { await refreshBoot(); } catch (e) { $("#root").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
  applyLook();
  if (!S.boot.user) return renderAuth();
  renderShell();
  route();
  if (STATIC && window.BeyondCloud) window.BeyondCloud.onStatus(syncDot);
  else if (S.boot.cloud && S.boot.cloud.joined) {
    const poll = () => api("GET", "/api/cloud-status").then(syncDot).catch(() => {});
    poll(); clearInterval(S.syncTimer); S.syncTimer = setInterval(poll, 15000);
  }
}
(window.__beyondReady || Promise.resolve()).then(boot, () => {});
})();
