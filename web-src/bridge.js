/* Beyond ERP — browser-only runtime (static hosting).
   Runs the same Python engine as the desktop app inside the browser (Pyodide) on SQLite compiled to
   WebAssembly (sql.js). Data is stored in this browser's IndexedDB; use Import & Export to back it up. */
"use strict";
window.BEYOND_STATIC = true;
(function () {
  const V = "vendor/";
  const say = m => { const el = document.getElementById("bootmsg"); if (el) el.textContent = m; };
  const load = src => new Promise((ok, bad) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = () => bad(new Error("Could not load " + src)); document.head.appendChild(s); });

  // ---------- IndexedDB (one key: the whole database image)
  const idb = () => new Promise((ok, bad) => { const r = indexedDB.open("beyond-erp", 1); r.onupgradeneeded = () => r.result.createObjectStore("kv"); r.onsuccess = () => ok(r.result); r.onerror = () => bad(r.error); });
  async function idbGet(k) { const d = await idb(); return new Promise((ok, bad) => { const q = d.transaction("kv").objectStore("kv").get(k); q.onsuccess = () => ok(q.result); q.onerror = () => bad(q.error); }); }
  async function idbPut(k, v) { const d = await idb(); return new Promise((ok, bad) => { const tx = d.transaction("kv", "readwrite"); tx.objectStore("kv").put(v, k); tx.oncomplete = ok; tx.onerror = () => bad(tx.error); }); }

  // ---------- packed binaries: .wasm/.zip/.whl/.json travel as base64 inside plain .js files, so the site works
  // from any static host (no special file types, every file < 3 MB) and even opened straight from disk (file://).
  const parts = {}, BLOBS = {};
  window.BEYOND_PACK = (name, i, n, b64) => { (parts[name] = parts[name] || { n, list: [] }).list[i] = b64; };
  const b64bytes = s => { const bin = atob(s), a = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i); return a; };
  async function unpackAll() {
    const files = window.BEYOND_PACK_FILES || [];
    for (let i = 0; i < files.length; i++) {
      say(`Loading the ERP engine… ${Math.round(i / files.length * 100)}% (first visit ≈ 19 MB, then cached)`);
      await load(files[i]);
    }
    for (const [name, p] of Object.entries(parts)) {
      if (p.list.length !== p.n || p.list.some(x => x == null)) throw new Error("Incomplete upload: " + name + " — upload every file in the packs folder");
      const chunks = p.list.map(b64bytes), out = new Uint8Array(chunks.reduce((a, c) => a + c.length, 0));
      let o = 0; chunks.forEach(c => { out.set(c, o); o += c.length; });
      BLOBS[name] = out; delete parts[name];
    }
    const realFetch = window.fetch ? window.fetch.bind(window) : null;
    const type = n => n.endsWith(".wasm") ? "application/wasm" : n.endsWith(".json") ? "application/json" : "application/octet-stream";
    window.fetch = (input, init) => {
      const url = typeof input === "string" ? input : (input && (input.url || input.href)) || String(input);
      const name = decodeURIComponent(url.split(/[?#]/)[0].split("/").pop());
      if (BLOBS[name]) return Promise.resolve(new Response(BLOBS[name], { status: 200, headers: { "Content-Type": type(name) } }));
      if (!realFetch) return Promise.reject(new Error("fetch unavailable"));
      return realFetch(input, init);
    };
  }

  window.__beyondReady = (async () => {
    if (!window.indexedDB) console.warn("IndexedDB unavailable — data will not persist");
    await unpackAll();
    say("Loading database engine…");
    await load(V + "sql-wasm.js");
    const SQL = await window.initSqlJs({ locateFile: f => V + f, wasmBinary: BLOBS["sql-wasm.wasm"] });
    let saved = null;
    try { saved = await idbGet("db"); } catch (e) { console.warn("IndexedDB unavailable — data will not persist", e); }
    let db = saved ? new SQL.Database(new Uint8Array(saved)) : new SQL.Database();
    db.run("PRAGMA foreign_keys = ON");

    globalThis.beyondSqlRun = (sql, paramsJson) => {
      try {
        const params = JSON.parse(paramsJson);
        if (params === null) { db.exec(sql); return JSON.stringify({ rows: [], cols: [] }); }
        const st = db.prepare(sql);
        try {
          if (params.length) st.bind(params);
          const rows = [];
          while (st.step()) rows.push(st.get());
          const out = { rows, cols: st.getColumnNames(), changes: db.getRowsModified() };
          if (/^\s*(INSERT|REPLACE)/i.test(sql)) out.lastId = db.exec("SELECT last_insert_rowid()")[0].values[0][0];
          return JSON.stringify(out);
        } finally { st.free(); }
      } catch (e) { return JSON.stringify({ error: String(e && e.message || e) }); }
    };
    const exportDb = () => { const bytes = db.export(); db.run("PRAGMA foreign_keys = ON"); return bytes; };
    globalThis.beyondSqlExport = exportDb;

    let timer = null, dirty = false;
    const persist = async () => { if (!dirty) return; dirty = false; try { await idbPut("db", exportDb()); } catch (e) { console.error("save failed", e); } };
    const schedule = () => { dirty = true; clearTimeout(timer); timer = setTimeout(persist, 300); };
    window.addEventListener("pagehide", persist);
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") persist(); });

    say("Starting the ERP engine…");
    await load(V + "pyodide/pyodide.js");
    await load(V + "pyodide/pyodide.asm.js");
    const py = await window.loadPyodide({ indexURL: new URL(V + "pyodide/", location.href).href });
    const unpack = async (url, fmt, dir) => { const r = await fetch(url); if (!r.ok) throw new Error("Missing " + url); py.unpackArchive(await r.arrayBuffer(), fmt, dir ? { extractDir: dir } : undefined); };
    await unpack("erp.zip", "zip", "/app");
    for (const w of (window.BEYOND_WHEELS || [])) await unpack(V + "wheels/" + w, "wheel");
    say("Starting…");
    py.runPython(`
import os, sys, json
os.environ["BEYOND_ERP_STATIC"] = "1"
os.environ["BEYOND_ERP_NO_WAL"] = "1"
sys.path.insert(0, "/app")
from pathlib import Path
import js
from erp import jsdb
from erp.db import Database
from erp.server import handle

class BrowserApp:
    def __init__(self):
        self.db = Database("/tmp/beyond-erp.sqlite3", connection=jsdb.connect(js.globalThis.beyondSqlRun))
    def backups(self):
        return []
    def backup(self, reason="auto"):
        p = Path("/tmp/beyond-erp-backup.sqlite3")
        self.db.backup_to(p)
        return p

APP = BrowserApp()
APP.static = True
import erp as _erp
js.globalThis.BEYOND_VERSION = _erp.__version__

def _pack(r):
    return [r.status, r.ctype, r.filename or "", r.body, json.dumps(r.headers)]

def beyond_call(method, url, headers_json, body):
    raw = bytes(body.to_py()) if body is not None else b""
    return _pack(handle(APP, method, url, json.loads(headers_json), raw))

# ---- shared workspace helpers (the network part lives in JavaScript: Cloud below)
from erp import cloud as C, auth as A

def cloud_replicated(method, url):
    return C.replicated(method, url)

def cloud_prepare(method, url, headers_json, body):
    h = json.loads(headers_json)
    raw = bytes(body.to_py()) if body is not None else b""
    user = A.user_for_token(APP.db, h.get("x-session"))
    if not user:
        return [_pack(handle(APP, method, url, h, raw)), ""]
    r, op = C.prepare(APP, method, url, h, raw, user)
    return [_pack(r), json.dumps(op) if op else ""]

def cloud_apply(op_json):
    try:
        return _pack(C.apply(APP, json.loads(op_json)))
    except C.NeedsUpdate as e:
        return [0, "", "", str(e).encode(), "{}"]

def cloud_followups(method, url, body_text):
    try:
        body = json.loads(body_text or "{}")
    except ValueError:
        body = {}
    return json.dumps([[m, p, d.decode()] for m, p, d in C.followups(APP.db, method, url, body)])

def cloud_local(key, value=None, write=False):
    if write:
        C.local_set(APP.db, key, value)
        return value
    return C.local_get(APP.db, key)

def cloud_device():
    return C.device_id(APP.db)

def cloud_after_restore(keep_json):
    C.restore_local_rows(APP.db, {"cloud_local": json.loads(keep_json), "sessions": []})

def cloud_ensure_admin(email):
    C.ensure_app_user(APP.db, email, admin=True)
`);
    window.beyondPython = py;  // for diagnostics from the browser console
    const P = n => py.globals.get(n);
    const call = P("beyond_call");
    const unpackReply = out => { const arr = out.toJs(); out.destroy(); return { status: arr[0], ctype: arr[1], filename: arr[2], body: arr[3], headers: JSON.parse(arr[4]) }; };
    const jsonReply = (status, obj) => ({ status, ctype: "application/json", filename: "", body: new TextEncoder().encode(JSON.stringify(obj)), headers: {} });
    const local = (k, v) => v === undefined ? P("cloud_local")(k) : P("cloud_local")(k, v === null ? null : String(v), true);
    const localRun = (method, url, h, raw) => unpackReply(call(method, url, JSON.stringify(h), raw));

    // ======================================================== shared workspace (Supabase) — see erp/cloud.py
    const Cloud = window.BeyondCloud = (() => {
      const LS = "berp.cloud", SES = "berp.cloud.session";
      const remembered = () => { try { return localStorage.getItem("berp.remember") !== "false"; } catch { return true; } };
      const box = k => { try { return k === SES && !remembered() ? sessionStorage : localStorage; } catch { return localStorage; } };
      const get = k => { try { return JSON.parse(box(k).getItem(k) || "null"); } catch { return null; } };
      const put = (k, v) => { try { const b = box(k); v == null ? b.removeItem(k) : b.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } };
      const status = { state: "off", error: "", lastSync: "" };
      const listeners = new Set();
      const emit = () => listeners.forEach(f => { try { f(status); } catch { /* ignore */ } });
      const setStatus = (state, error = "") => { status.state = state; status.error = error; if (state === "ok") status.lastSync = new Date().toISOString(); emit(); };
      const cfg = () => { const c = get(LS) || window.BEYOND_CLOUD_CONFIG || {}; return c.url && c.key ? { url: c.url.replace(/\/+$/, ""), key: c.key, preset: !get(LS) } : null; };
      class CloudError extends Error { constructor(m, code, status) { super(m); this.code = code; this.status = status; } }
      async function req(method, path, body, token) {
        const c = cfg(); if (!c) throw new CloudError("Connect this device to a workspace first (Settings → Workspace)");
        let r;
        try {
          r = await fetch(c.url + path, { method, headers: { apikey: c.key, "Content-Type": "application/json", Authorization: "Bearer " + (token || c.key) },
            body: body === undefined ? undefined : JSON.stringify(body) });
        } catch (e) { throw new CloudError("Cannot reach the workspace — check your internet connection", "offline"); }
        const text = await r.text(); let data = null; try { data = text ? JSON.parse(text) : null; } catch { data = { message: text }; }
        if (!r.ok) { const d = data || {}; throw new CloudError(d.message || d.msg || d.error_description || d.error || ("HTTP " + r.status), d.error_code || (typeof d.code === "string" ? d.code : ""), r.status); }
        return data;
      }
      // Plain-language versions of Supabase's sign-in errors
      const AUTH_HELP = {
        invalid_credentials: "Wrong email or password. No account yet? Choose “Create an account”. Just created one? Open the confirmation email first. Forgot the password? Choose “Forgot password?”.",
        email_not_confirmed: "Please confirm your email first: open the link Supabase sent you (check spam), then sign in. “Resend confirmation email” sends a new one.",
        over_email_send_rate_limit: "Supabase's free email service only sends a few emails per hour. Wait a while and try again, or ask the administrator to create your account in the Supabase dashboard (Authentication → Users → Add user, tick “Auto Confirm User”).",
        user_already_exists: "An account with this email already exists — choose “Sign in” (or “Forgot password?”).",
        weak_password: "Choose a longer password (at least 6 characters).",
        otp_expired: "That email link has expired or was already used. Choose “Resend confirmation email” or “Forgot password?” to get a new one.",
        signup_disabled: "New accounts are switched off for this workspace — ask the administrator to create your account.",
      };
      const friendly = e => { const h = AUTH_HELP[e.code] || (/invalid login credentials/i.test(e.message) && AUTH_HELP.invalid_credentials) ||
        (/email not confirmed/i.test(e.message) && AUTH_HELP.email_not_confirmed) || (/rate limit/i.test(e.message) && AUTH_HELP.over_email_send_rate_limit) ||
        (/already registered/i.test(e.message) && AUTH_HELP.user_already_exists); if (h) e.message = h; return e; };
      // where Supabase's email links should bring people back to (this page)
      const appUrl = () => location.protocol.startsWith("http") ? location.origin + location.pathname : "";
      const withRedirect = path => appUrl() ? path + (path.includes("?") ? "&" : "?") + "redirect_to=" + encodeURIComponent(appUrl()) : path;
      // Links from Supabase emails arrive as #access_token=…&type=signup|recovery (or #error=…): pick them up once
      let pendingAuth = null;
      (() => {
        const hp = new URLSearchParams(location.hash.replace(/^#\/?/, ""));
        if (hp.get("access_token") || hp.get("error_description")) {
          pendingAuth = { access: hp.get("access_token"), refresh: hp.get("refresh_token"), expires: Number(hp.get("expires_in") || 3600),
            type: hp.get("type") || "", error: hp.get("error_description") ? (AUTH_HELP[hp.get("error_code")] || hp.get("error_description")) : "" };
          history.replaceState(null, "", location.pathname + location.search);
        }
      })();
      const saveSession = s => { if (!s || !s.access_token) throw new CloudError("Please confirm your email first: open the link Supabase sent you, then sign in.", "email_not_confirmed");
        put(SES, { access: s.access_token, refresh: s.refresh_token, exp: Date.now() + ((s.expires_in || 3600) - 60) * 1000, email: ((s.user && s.user.email) || (get(SES) || {}).email || "").toLowerCase() }); };
      async function token() {
        const s = get(SES); if (!s) throw new CloudError("Sign in to the workspace", "signed_out");
        if (Date.now() < s.exp) return s.access;
        try { saveSession(await req("POST", "/auth/v1/token?grant_type=refresh_token", { refresh_token: s.refresh })); }
        catch (e) { if (e.code !== "offline") put(SES, null); throw e; }
        return get(SES).access;
      }
      const rpc = async (name, params) => req("POST", "/rest/v1/rpc/" + name, params || {}, await token());
      const select = async (table, q) => req("GET", `/rest/v1/${table}?${q}`, undefined, await token());
      const lastSeq = () => Number(local("last_seq") ?? -1);
      const joined = () => !!cfg() && lastSeq() >= 0;

      // --- copies of the books (gzip + base64 of the SQLite image, without device-only tables)
      const gz = async (bytes, dir) => new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(dir ? new CompressionStream("gzip") : new DecompressionStream("gzip"))).arrayBuffer());
      const toB64 = bytes => { let s = ""; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); return btoa(s); };
      async function makeSnapshot() {
        const copy = new SQL.Database(exportDb()); copy.run("DELETE FROM cloud_local"); copy.run("DELETE FROM sessions"); copy.run("VACUUM");
        const bytes = copy.export(); copy.close(); return toB64(await gz(bytes, true));
      }
      async function restoreSnapshot(b64) {
        const res = db.exec("SELECT key, value FROM cloud_local");
        const keep = res.length ? res[0].values.map(([key, value]) => ({ key, value })) : [];
        const bytes = await gz(b64bytes(b64), false);
        const old = db; db = new SQL.Database(bytes); db.run("PRAGMA foreign_keys = ON"); old.close();
        P("cloud_after_restore")(JSON.stringify(keep));
        dirty = true; await persist();
      }

      // --- sync
      let chain = Promise.resolve();
      const exclusive = fn => { const run = chain.then(fn, fn); chain = run.catch(() => {}); return run; };
      let blocked = "";
      async function pullNow() {
        let n = 0;
        for (;;) {
          const rows = await select("ops", `select=*&seq=gt.${lastSeq()}&order=seq.asc&limit=500`);
          for (const op of rows) {
            if (Number(op.seq) !== lastSeq() + 1) throw new CloudError("The workspace log has a gap — try again in a moment");
            const r = unpackReply(P("cloud_apply")(JSON.stringify(op)));
            if (r.status === 0) { blocked = new TextDecoder().decode(r.body); setStatus("update", `A newer app version (${blocked}) is in use — reload this page to update`); throw new CloudError(status.error, "update"); }
            n++;
          }
          if (rows.length < 500) break;
        }
        if (n) { schedule(); window.dispatchEvent(new CustomEvent("beyond-remote-change", { detail: n })); }
        setStatus("ok");
        return n;
      }
      const fail = e => { setStatus(e.code === "offline" ? "offline" : e.code === "signed_out" || e.status === 401 ? "signed_out" : e.code === "update" ? "update" : "error", e.message); };
      const pull = () => exclusive(() => pullNow().catch(e => { fail(e); throw e; }));
      async function write(method, url, h, raw) {
        return exclusive(async () => {
          try {
            for (let attempt = 0; attempt < 8; attempt++) {
              await pullNow();
              const out = P("cloud_prepare")(method, url, JSON.stringify(h), raw); const pair = out.toJs(); out.destroy();
              const dry = { status: pair[0][0], ctype: pair[0][1], filename: pair[0][2], body: pair[0][3], headers: JSON.parse(pair[0][4]) };
              if (!pair[1]) return dry;
              const op = JSON.parse(pair[1]); op.seq = lastSeq() + 1;
              let stored;
              try {
                stored = await rpc("append_op", { p_seq: op.seq, p_version: op.version, p_app_user: op.app_user, p_device: op.device, p_method: op.method,
                  p_path: op.path, p_body: op.body, p_content_type: op.content_type, p_clock: op.clock });
              } catch (e) { if (e.code === "40001") continue; throw e; }
              op.app_user = (stored && stored.app_user) || op.app_user;
              const r = unpackReply(P("cloud_apply")(JSON.stringify(op)));
              schedule(); setStatus("ok"); maybeSnapshot();
              for (const [m, p, d] of JSON.parse(P("cloud_followups")(method, url, h["content-type"] && raw ? new TextDecoder().decode(raw) : "{}")))
                await write(m, p, { ...h, "content-type": "application/json" }, new TextEncoder().encode(d)).catch(() => {});
              return r;
            }
            return jsonReply(409, { error: "The workspace is very busy — please try again" });
          } catch (e) {
            fail(e);
            const msg = e.code === "offline" ? "You are offline — changes need a connection to the shared workspace (viewing still works)." :
              e.code === "update" ? e.message : e.code === "signed_out" || e.status === 401 ? "Your workspace sign-in expired — sign out and sign in again." : "Workspace: " + e.message;
            return jsonReply(e.code === "offline" ? 503 : 409, { error: msg });
          }
        });
      }
      let snapBusy = false;
      async function maybeSnapshot(force) {
        const seq = lastSeq(), done = Number(local("snap_seq") || 0);
        if (snapBusy || (!force && seq - done < 300)) return;
        snapBusy = true;
        try { await rpc("put_snapshot", { p_seq: seq, p_version: window.BEYOND_VERSION || "", p_data: await makeSnapshot() }); local("snap_seq", seq); }
        catch (e) { local("snap_seq", seq); } finally { snapBusy = false; }
      }
      let timer = null;
      function startPolling() {
        if (timer) return;
        const tick = () => { if (joined() && get(SES) && document.visibilityState === "visible" && !blocked) pull().catch(() => {}); };
        timer = setInterval(tick, 7000);
        document.addEventListener("visibilitychange", tick); window.addEventListener("online", tick); window.addEventListener("focus", tick);
      }

      return {
        CloudError, onStatus: f => { listeners.add(f); f(status); },
        configured: () => !!cfg(), preset: () => { const c = cfg(); return !!(c && c.preset); },
        active: () => joined() && !!get(SES), joined, email: () => (get(SES) || {}).email || "",
        replicated: (m, u) => joined() && P("cloud_replicated")(m, u),
        async status() { const c = cfg() || {}; return { configured: !!cfg(), url: c.url || "", key: c.key || "", preset: !!c.preset, email: (get(SES) || {}).email || "",
          joined: joined(), last_seq: lastSeq(), device: P("cloud_device")(), state: status.state, error: status.error, last_sync: status.lastSync }; },
        async config(url, key) {
          url = String(url || "").trim().replace(/\/+$/, "");
          if (!/^https?:\/\//.test(url) || !String(key || "").trim()) throw new CloudError("Enter the project URL (https://….supabase.co) and the anon public key");
          put(LS, { url, key: String(key).trim() }); return this.status();
        },
        async signin(email, password, create) {
          email = String(email || "").trim().toLowerCase();
          if (create) {
            const r = await this.signup(email, password);
            if (r.confirm) throw new CloudError(r.message, "confirm_sent");
            return rpc("workspace_info");
          }
          if (!email.includes("@")) {   // a username: the workspace's "login" function finds the email on the server
            try { saveSession(await req("POST", "/functions/v1/login", { username: email, password })); }
            catch (e) {
              if (e.status === 404) throw new CloudError("Signing in with a username is not set up on this workspace yet — use your email address (the administrator deploys the “login” function, see the setup guide).");
              if (e.code === "invalid_credentials" || /invalid login/i.test(e.message)) throw new CloudError("Wrong username or password. Forgot the password? Choose “Get help” and use your email address.", "invalid_credentials");
              throw friendly(e);
            }
            return rpc("workspace_info");
          }
          try { saveSession(await req("POST", "/auth/v1/token?grant_type=password", { email, password })); } catch (e) { throw friendly(e); }
          return rpc("workspace_info");
        },
        // Create an account. With "Confirm email" on (Supabase's default) no session comes back: the person must open
        // the emailed link first — it returns to this page and signs them in.
        async signup(email, password) {
          email = String(email || "").trim().toLowerCase();
          let r;
          try { r = await req("POST", withRedirect("/auth/v1/signup"), { email, password }); } catch (e) { throw friendly(e); }
          if (r && r.access_token) { saveSession(r); return { confirm: false }; }
          if (r && Array.isArray(r.identities) && !r.identities.length) throw friendly(new CloudError("", "user_already_exists"));
          return { confirm: true, message: `Almost done: we sent a confirmation email to ${email}. Open the link in it (check spam) — it brings you back here signed in.` +
            (appUrl() ? "" : " Then come back to this window and sign in.") };
        },
        async resend(email) {
          try { await req("POST", withRedirect("/auth/v1/resend"), { type: "signup", email: String(email || "").trim().toLowerCase() }); } catch (e) { throw friendly(e); }
        },
        async recover(email) {
          try { await req("POST", withRedirect("/auth/v1/recover"), { email: String(email || "").trim().toLowerCase() }); } catch (e) { throw friendly(e); }
        },
        async setPassword(password) {
          try { await req("PUT", "/auth/v1/user", { password }, await token()); } catch (e) { throw friendly(e); }
        },
        // A confirmation / password-reset link just brought the person back: finish signing them in.
        async takePendingAuth() {
          const p = pendingAuth; pendingAuth = null;
          if (!p) return null;
          if (p.error) return { error: p.error };
          const u = await req("GET", "/auth/v1/user", undefined, p.access);
          saveSession({ access_token: p.access, refresh_token: p.refresh, expires_in: p.expires, user: u });
          return { type: p.type, info: await rpc("workspace_info") };
        },
        hasPendingAuth: () => !!pendingAuth,
        info: () => rpc("workspace_info"),
        async claim() {
          const info = await rpc("claim_workspace");
          P("cloud_ensure_admin")(info.email);
          await rpc("put_snapshot", { p_seq: 0, p_version: window.BEYOND_VERSION || "", p_data: await makeSnapshot() });
          local("last_seq", 0); local("snap_seq", 0); dirty = true; await persist(); startPolling();
          return rpc("workspace_info");
        },
        async join() {
          const info = await rpc("workspace_info");
          if (!info.is_member) throw new CloudError("This account is not a member of the workspace — ask an administrator to add it");
          const rows = await select("snapshots", "select=seq,version,data&order=seq.desc&limit=1");
          if (!rows.length) throw new CloudError("The workspace has no saved copy yet");
          await exclusive(async () => { await restoreSnapshot(rows[0].data); local("last_seq", rows[0].seq); local("snap_seq", rows[0].seq); await pullNow(); });
          dirty = true; await persist(); startPolling();
          return info;
        },
        pull: () => pull().then(applied => ({ applied })),
        async disconnect() { ["last_seq", "snap_seq"].forEach(k => local(k, null)); put(SES, null); put(LS, null); dirty = true; await persist(); setStatus("off"); },
        signout() { put(SES, null); try { sessionStorage.removeItem(SES); localStorage.removeItem(SES); } catch { /* ignore */ } setStatus("signed_out", ""); },
        // "Remember me": keep the sign-in after the browser closes (on) or forget it (off). Applies to the next sign-in.
        setRemember(on) { try { localStorage.setItem("berp.remember", on ? "true" : "false"); } catch { /* ignore */ } },
        remembered,
        // Which sign-in methods the Supabase project has switched on (Authentication → Sign In / Providers)
        async providers() {
          try { const st = await req("GET", "/auth/v1/settings"); return { google: !!(st && st.external && st.external.google), email: !(st && st.external && st.external.email === false) }; }
          catch { return { google: false, email: true }; }
        },
        // Continue with Google: Supabase sends the person to Google and back here with the sign-in in the address
        oauthUrl(provider) { const c = cfg(); return c && appUrl() ? `${c.url}/auth/v1/authorize?provider=${encodeURIComponent(provider)}&redirect_to=${encodeURIComponent(appUrl())}` : ""; },
        members: () => select("members", "select=*&order=email"),
        setMember: m => rpc("set_member", { p_email: m.email, p_is_admin: !!m.is_admin, p_gateway: !!m.gateway, p_active: m.active !== false }),
        linkMember: m => rpc("link_member", { p_email: m.email, p_username: m.username || "", p_active: m.active !== false }),
        snapshot: () => maybeSnapshot(true),
        // open the app user that matches the signed-in workspace account
        async openSession() {
          const r = localRun("POST", "/api/cloud/session", { "x-requested-with": "BeyondERP", "content-type": "application/json", "x-beyond-static": "1" },
            new TextEncoder().encode(JSON.stringify({ email: (get(SES) || {}).email || "" })));
          dirty = true; await persist();   // keep the session through page reloads
          return r;
        },
        write, startPolling,
      };
    })();

    window.__beyondFetch = async (method, url, headers, body) => {
      const h = {}; Object.entries(headers || {}).forEach(([k, v]) => { h[k.toLowerCase()] = v; });
      h["x-beyond-static"] = "1";
      let raw = null;
      if (body != null) raw = typeof body === "string" ? new TextEncoder().encode(body) : new Uint8Array(body);
      if (method !== "GET" && Cloud.replicated(method, url)) return Cloud.write(method, url, h, raw);
      const r = localRun(method, url, h, raw);
      if (method !== "GET") schedule();
      return r;
    };
    if (Cloud.joined()) { Cloud.startPolling(); Cloud.pull().catch(() => {}); }
    if (!saved) { dirty = true; await persist(); }
    const ov = document.getElementById("boot"); if (ov) ov.remove();
  })().catch(e => {
    const hint = location.protocol === "file:" ? " — if this browser blocks local files, run a tiny server in this folder (see README) or upload the folder to a host." : "";
    say("Could not start: " + (e && e.message || e) + hint); throw e;
  });
})();
