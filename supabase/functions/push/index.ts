// Notifications that reach people's devices even when BeyondERP is closed (Web Push).
//
//   POST {action: "key"}                                   -> {key}: the public signing key devices subscribe with
//   POST {action: "send", company, kind: "chat", room, dm, to: [usernames], mentions: [usernames], title, body, href, tag}
//   POST {action: "send", company, kind: "approval", title, body, href}        -> the company's administrators
//   POST {action: "send", company, kind: "link", link, title, body, href}      -> everyone in the other company
//   POST {action: "send", company, kind: "notify", to: [usernames], title, body, href, tag, pref}  follow-ups, decisions…
//   POST {action: "schedule", company, key, at, to, title, body, href, tag} / {action: "cancel", company, key}  reminders
//   POST {action: "flush"}                                  send the reminders whose time has come (scheduled job)
//   POST {action: "visitor", token}                         a public page received something (no sign-in)
//
// The signing keys are made here the first time and kept in push_config, which only this function reads (service
// key). Senders must be active members of the company; people are only notified inside their own company (or the
// connected one, for chat between companies), within limits, and following the settings each device saved.
// Deployed by .github/workflows/deploy.yml with --no-verify-jwt (it checks the caller's token itself).
import { newKeys, send } from "./webpush.ts";

const URL_ = Deno.env.get("SUPABASE_URL")!;
const SERVICE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
const SUBJECT = Deno.env.get("PUSH_SUBJECT") || "https://beyond-erp.pages.dev/";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
const fail = (status: number, msg: string) => reply(status, { msg, error: msg });
const svc = { apikey: SERVICE, Authorization: `Bearer ${SERVICE}`, "Content-Type": "application/json" };
const enc = encodeURIComponent;
const clip = (s: unknown, n: number) => String(s ?? "").replace(/\s+/g, " ").trim().slice(0, n);

async function call(method: string, path: string, body?: unknown, headers: Record<string, string> = svc) {
  const r = await fetch(`${URL_}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { msg: text }; }
  return { ok: r.ok, status: r.status, data };
}

let KEYS: any = null;
async function keys() {
  if (KEYS) return KEYS;
  const got = await call("GET", "/rest/v1/push_config?select=keys&id=eq.1");
  if (got.ok && got.data.length) return (KEYS = got.data[0].keys);
  const made = await newKeys();
  // two first calls at once: the second insert loses and both read the winner
  await call("POST", "/rest/v1/push_config", { id: 1, keys: made }, { ...svc, Prefer: "resolution=ignore-duplicates" });
  const again = await call("GET", "/rest/v1/push_config?select=keys&id=eq.1");
  return (KEYS = again.data[0].keys);
}

// minutes since midnight in the device's own time zone (tz = getTimezoneOffset(), as the page saved it)
const localMinutes = (tz: number) => { const d = new Date(Date.now() - (Number(tz) || 0) * 60000); return d.getUTCHours() * 60 + d.getUTCMinutes(); };
const hm = (x: string) => { const [h, m] = String(x || "0:0").split(":").map(Number); return (h || 0) * 60 + (m || 0); };
function quiet(p: any) {
  if (!p.quiet) return false;
  const m = localMinutes(p.tz), a = hm(p.quiet_from), b = hm(p.quiet_to);
  return a <= b ? m >= a && m < b : m >= a || m < b;
}
// does this device want this message? (the same rules the open app uses)
function wants(p: any, kind: string, room: number, direct: boolean) {
  if (p.desktop === false || p.push === false) return false;
  if (kind === "chat") {
    const level = p.level || "all";
    return level === "all" || (level === "mentions" && direct) || (level === "rooms" && (direct || (p.rooms || []).includes(room)));
  }
  if (kind === "link") return (p.level || "all") !== "none";
  if (kind === "notify-reactions") return p.reactions !== false && p.alerts !== false;
  return p.alerts !== false;     // approvals, bookings and messages from the public pages
}

async function deliver(company: string | null, emails: string[], msg: any, opts: { kind: string; room?: number; direct?: Set<string> }) {
  if (!emails.length) return 0;
  const list = emails.map(e => `"${e.replace(/"/g, "")}"`).join(",");
  const q = `/rest/v1/push_subs?select=endpoint,email,p256dh,auth,prefs${company ? `&company_id=eq.${enc(company)}` : ""}&email=in.(${enc(list)})&limit=500`;
  const subs = await call("GET", q);
  if (!subs.ok) return 0;
  const k = await keys();
  let sent = 0;
  await Promise.allSettled(subs.data.map(async (s: any) => {
    const p = s.prefs || {}, direct = !!opts.direct?.has(String(s.email).toLowerCase());
    if (!wants(p, opts.kind === "notify" && msg.pref === "reactions" ? "notify-reactions" : opts.kind, opts.room || 0, direct)) return;
    const silent = quiet(p);
    const body = p.preview === false ? (p.lang === "ar" ? "افتح BeyondERP لقراءتها" : "Open BeyondERP to read it") : msg.body;
    const st = await send(s, { ...msg, body, silent }, k, SUBJECT, { topic: msg.tag, urgency: silent ? "low" : "high" }).catch(() => 0);
    if (st === 404 || st === 410) await call("DELETE", `/rest/v1/push_subs?endpoint=eq.${enc(s.endpoint)}`);
    else if (st >= 200 && st < 300) {
      sent++;
      await call("PATCH", `/rest/v1/push_subs?endpoint=eq.${enc(s.endpoint)}`, { last_ok: new Date().toISOString() });
    }
  }));
  return sent;
}

async function countRecent(sender: string, seconds: number) {
  const since = new Date(Date.now() - seconds * 1000).toISOString();
  const r = await fetch(`${URL_}/rest/v1/push_log?select=id&sender=eq.${enc(sender)}&at=gte.${enc(since)}`,
                        { headers: { ...svc, Prefer: "count=exact", Range: "0-0" } });
  const range = r.headers.get("content-range") || "";
  await r.body?.cancel();
  return Number(range.split("/")[1]) || 0;
}
// the page names people by username (it never sees colleagues' emails); find their workspace accounts in this company
async function emailsFor(company: string, usernames: unknown, except = "") {
  const names = [...new Set((Array.isArray(usernames) ? usernames : []).map((e: any) => String(e).toLowerCase().trim()))]
    .filter(e => /^[a-z0-9._@\-\u0600-\u06ff]{1,80}$/.test(e)).slice(0, 500) as string[];
  if (!names.length) return [] as string[];
  const q = names.map(e => `"${e.replace(/"/g, "")}"`).join(",");
  const ppl = await call("GET", `/rest/v1/people?select=email,username&username=in.(${enc(q)})`);
  const cand = [...new Set([...(ppl.data || []).map((x: any) => String(x.email).toLowerCase()), ...names.map(u => u.includes("@") ? u : `${u}@users.beyond-erp.invalid`)])]
    .filter(e => e !== except);
  if (!cand.length) return [] as string[];
  const list = cand.map(e => `"${e.replace(/"/g, "")}"`).join(",");
  const ok = await call("GET", `/rest/v1/members?select=email&company_id=eq.${enc(company)}&active=is.true&pending=is.false&email=in.(${enc(list)})`);
  return (ok.data || []).map((m: any) => String(m.email).toLowerCase()) as string[];
}

const logSend = (company: string | null, sender: string, kind: string, sent: number) =>
  call("POST", "/rest/v1/push_log", { company_id: company, sender, kind, sent });

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return fail(405, "POST only");
  const raw = await req.text();
  if (raw.length > 20000) return fail(413, "Too large");
  let b: any;
  try { b = JSON.parse(raw); } catch { return fail(400, "Send JSON"); }

  if (b.action === "key") return reply(200, { key: (await keys()).public_key });

  // a visitor on a public page sent something: tell the people who look after the pages (no text from the visitor)
  if (b.action === "visitor") {
    const tok = String(b.token || "");
    if (!/^[A-Za-z0-9_-]{8,64}$/.test(tok)) return fail(400, "Bad page");
    const pg = await call("GET", `/rest/v1/public_pages?select=id,company_id,title,kind,active&token=eq.${enc(tok)}`);
    if (!pg.ok || !pg.data.length || !pg.data[0].active) return fail(404, "No such page");
    const page = pg.data[0], who = `page:${page.id}`;
    if (await countRecent(who, 60) >= 1) return reply(200, { sent: 0, note: "already notified a moment ago" });
    // something really arrived just now from this page (not only a call to this function)
    const since = new Date(Date.now() - 120000).toISOString();
    const inb = await call("GET", `/rest/v1/public_inbox?select=id,kind&page_id=eq.${enc(page.id)}&direction=eq.in&created_at=gte.${enc(since)}&limit=1`);
    if (!inb.ok || !inb.data.length) return reply(200, { sent: 0 });
    const kind = String(inb.data[0].kind || b.kind || "");
    const mem = await call("GET", `/rest/v1/members?select=email&company_id=eq.${enc(page.company_id)}&active=is.true&pending=is.false&or=(is_admin.is.true,desk_publish.eq.all)`);
    const emails = (mem.data || []).map((m: any) => String(m.email).toLowerCase());
    const what = { booking: "New booking", reschedule: "Booking moved", cancel: "Booking cancelled", lead: "New enquiry", message: "New message", signature: "Offer signed" }[kind] || "New on your page";
    const sent = await deliver(page.company_id, emails, { title: `${what} · ${clip(page.title, 60)}`, body: "Open BeyondERP to see it", href: "#/desk", tag: `desk-${page.id}`, kind: "desk" }, { kind: "desk" });
    await logSend(page.company_id, who, "visitor", sent);
    return reply(200, { sent });
  }

  // reminders whose time has come (called every few minutes by a scheduled job; also safe for anyone to call)
  if (b.action === "flush") {
    const due = await call("POST", "/rest/v1/rpc/push_due", { p_limit: 200 });
    let sent = 0;
    for (const q of (due.ok ? due.data : []) || []) {
      const m = q.msg || {};
      sent += await deliver(q.company_id, await emailsFor(q.company_id, m.to), { title: m.title, body: m.body, href: m.href, tag: m.tag, kind: "notify" }, { kind: "notify" });
    }
    return reply(200, { due: (due.data || []).length, sent });
  }

  if (!["send", "schedule", "cancel"].includes(b.action)) return fail(400, "Unknown action");
  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  const me = await call("GET", "/auth/v1/user", undefined, { apikey: ANON, Authorization: `Bearer ${token}` });
  const caller = String(me.data?.email || "").toLowerCase();
  if (!me.ok || !caller) return fail(401, "Sign in to the workspace again");
  const company = String(b.company || "");
  if (!/^[0-9a-f-]{36}$/i.test(company)) return fail(400, "Say which company");
  const mem = await call("GET", `/rest/v1/members?select=email&company_id=eq.${enc(company)}&email=eq.${enc(caller)}&active=is.true&pending=is.false`);
  if (!mem.ok || !mem.data.length) return fail(403, "You are not a member of this company");
  if (await countRecent(caller, 3600) >= 600) return fail(429, "Too many notifications sent in the last hour");
  const key = clip(b.key, 60);
  if (b.action === "cancel") {
    if (key) await call("DELETE", `/rest/v1/push_queue?company_id=eq.${enc(company)}&key=eq.${enc(key)}`);
    return reply(200, { ok: true });
  }
  if (b.action === "schedule") {
    const at = new Date(String(b.at || ""));
    if (!key || isNaN(at.getTime()) || at.getTime() > Date.now() + 400 * 86400000) return fail(400, "Say when");
    const to = (Array.isArray(b.to) ? b.to : []).map((x: any) => clip(x, 80)).slice(0, 50);
    await call("POST", "/rest/v1/push_queue?on_conflict=company_id,key", { company_id: company, key, at: at.toISOString(), sender: caller,
      msg: { to, title: clip(b.title, 80), body: clip(b.body, 180), href: String(b.href || "#/").slice(0, 120), tag: clip(b.tag, 40) } },
      { ...svc, Prefer: "resolution=merge-duplicates" });
    return reply(200, { ok: true });
  }

  const msg = { title: clip(b.title, 80), body: clip(b.body, 180), href: String(b.href || "#/").slice(0, 120), tag: clip(b.tag, 40), kind: String(b.kind) };
  let sent = 0;
  if (b.kind === "chat") {
    const emails = await emailsFor(company, b.to, caller);
    if (!emails.length) return reply(200, { sent: 0 });
    const ment = new Set(await emailsFor(company, b.mentions));
    const direct = new Set<string>(b.dm ? emails : emails.filter((e: string) => ment.has(e)));
    sent = await deliver(company, emails, msg, { kind: "chat", room: Number(b.room) || 0, direct });
  } else if (b.kind === "notify") {        // follow-ups, approval decisions, reactions… (named by username)
    sent = await deliver(company, await emailsFor(company, b.to, caller), { ...msg, pref: b.pref === "reactions" ? "reactions" : "" }, { kind: "notify" });
  } else if (b.kind === "approval") {
    const adm = await call("GET", `/rest/v1/members?select=email&company_id=eq.${enc(company)}&active=is.true&pending=is.false&is_admin=is.true`);
    sent = await deliver(company, (adm.data || []).map((m: any) => String(m.email).toLowerCase()).filter((e: string) => e !== caller), msg, { kind: "approval" });
  } else if (b.kind === "link") {
    const l = await call("GET", `/rest/v1/chat_links?select=company_a,company_b,name_a,name_b,status&id=eq.${enc(String(b.link || ""))}`);
    const link = l.ok && l.data.length ? l.data[0] : null;
    if (!link || link.status !== "active" || (link.company_a !== company && link.company_b !== company)) return fail(403, "Not a connection of this company");
    const other = link.company_a === company ? link.company_b : link.company_a;
    const ours = link.company_a === company ? link.name_a : link.name_b;
    const them = await call("GET", `/rest/v1/members?select=email&company_id=eq.${enc(other)}&active=is.true&pending=is.false`);
    sent = await deliver(other, (them.data || []).map((m: any) => String(m.email).toLowerCase()),
                         { ...msg, title: clip(`${msg.title} · ${ours || ""}`, 80), href: "#/chat" }, { kind: "link" });
  } else return fail(400, "Unknown kind");
  await logSend(company, caller, msg.kind, sent);
  return reply(200, { sent });
});
