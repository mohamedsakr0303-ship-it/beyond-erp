// Notifications that reach people's devices even when BeyondERP is closed (Web Push).
//
//   POST {action: "key"}                                   -> {key}: the public signing key devices subscribe with
//   POST {action: "send", company, kind: "chat", room, dm, to: [usernames], mentions: [usernames], title, body, href, tag}
//   POST {action: "send", company, kind: "approval", title, body, href}        -> the company's administrators
//   POST {action: "send", company, kind: "link", link, title, body, href}      -> everyone in the other company
//   POST {action: "send", company, kind: "notify", to: [usernames], title, body, href, tag, pref}  follow-ups, decisions, alerts…
//   POST {action: "schedule", …, day, time?, pref}   a reminder that day at that time (bills due, alerts) — or the person's own
//   POST {action: "schedule", company, key, at, to, title, body, href, tag} / {action: "cancel", company, key}  reminders
//   POST {action: "alerts", company, covers: [rule ids], items: [{key, day, time, to, title, body, href, tag}]}
//                                                          the alerts' upcoming times (replace what waited for those rules)
//   POST {action: "flush"}                                  send the reminders whose time has come (scheduled job)
//   POST {action: "visitor", token}                         a public page received something (no sign-in)
//   POST {action: "platform", event: "ticket" | "order" | "visit", id, isNew?, text?}   owner console ⇄ customers (see below)
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
const localDay = (tz: number) => new Date(Date.now() - (Number(tz) || 0) * 60000);
function quiet(p: any) {
  if ((p.quiet_days || []).includes(localDay(p.tz).getUTCDay())) return true;       // a quiet day (e.g. Friday)
  if (!p.quiet) return false;
  const m = localMinutes(p.tz), a = hm(p.quiet_from), b = hm(p.quiet_to);
  return a <= b ? m >= a && m < b : m >= a || m < b;
}
// one of the notification types the person can switch off (older settings had one switch for all of them)
const on = (p: any, k: string) => p[k] ?? (p.alerts !== false);
// does this device want this message? (the same rules the open app uses)
function wants(p: any, kind: string, room: number, direct: boolean) {
  if (p.desktop === false || p.push === false) return false;
  if (kind === "chat") {
    const level = p.level || "all";
    if (level === "none") return false;
    if ((p.muted || []).includes(room) && !direct) return false;      // a muted room: only when I am named in it
    return level === "all" || (level === "mentions" && direct) || (level === "rooms" && (direct || (p.rooms || []).includes(room)));
  }
  if (kind === "link") return p.links !== false;
  if (kind === "approval") return on(p, "approvals");
  if (kind === "desk") return on(p, "desk");
  if (kind.startsWith("notify-")) return kind === "notify-reactions" ? p.reactions !== false : on(p, kind.slice(7));
  return p.alerts !== false;
}

async function deliver(company: string | null, emails: string[], msg: any,
                       opts: { kind: string; room?: number; direct?: Set<string>; ready?: (p: any) => boolean; later?: string[] }) {
  if (!emails.length) return 0;
  const list = emails.map(e => `"${e.replace(/"/g, "")}"`).join(",");
  const q = `/rest/v1/push_subs?select=endpoint,email,p256dh,auth,prefs${company ? `&company_id=eq.${enc(company)}` : ""}&email=in.(${enc(list)})&limit=500`;
  const subs = await call("GET", q);
  if (!subs.ok) return 0;
  const k = await keys();
  let sent = 0;
  await Promise.allSettled(subs.data.map(async (s: any) => {
    const p = s.prefs || {}, direct = !!opts.direct?.has(String(s.email).toLowerCase());
    if (!wants(p, opts.kind === "notify" && msg.pref ? "notify-" + msg.pref : opts.kind, opts.room || 0, direct)) return;
    if (opts.ready && !opts.ready(p)) { opts.later?.push(String(s.email)); return; }      // a reminder before this person's time
    if (Number(p.snooze_until) > Date.now()) { opts.later?.push(String(s.email)); return; }  // paused: reminders wait, the rest is skipped
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
      const m = q.msg || {}, later: string[] = [];
      const emails = Array.isArray(m.emails) ? m.emails : await emailsFor(q.company_id, m.to);
      // ready when that device's own day has come and the reminder's time has passed — the time set on the reminder
      // (a bill due, an alert), else the person's own follow-up time
      const ready = (p: any) => {
        if (!m.day) return true;
        const d = localDay(p.tz).toISOString().slice(0, 10);
        return d > m.day || (d === m.day && localMinutes(p.tz) >= hm(m.time || p.fu_time || "09:00"));
      };
      sent += await deliver(q.company_id, emails, { title: m.title, body: m.body, href: m.href, tag: m.tag, kind: "notify", pref: m.pref || "fu_due" },
                            { kind: "notify", ready, later });
      const stale = m.day && new Date(m.day + "T00:00:00Z").getTime() < Date.now() - 2 * 86400000;
      if (later.length && !stale)                // the others get it at their time: back in the queue for them
        await call("POST", "/rest/v1/push_queue?on_conflict=company_id,key", { company_id: q.company_id, key: q.key, sender: q.sender,
          at: new Date(Date.now() + 9 * 60000).toISOString(), msg: { ...m, emails: [...new Set(later)] } }, { ...svc, Prefer: "resolution=merge-duplicates" });
    }
    return reply(200, { due: (due.data || []).length, sent });
  }

  if (!["send", "schedule", "cancel", "alerts", "platform"].includes(b.action)) return fail(400, "Unknown action");
  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  const me = await call("GET", "/auth/v1/user", undefined, { apikey: ANON, Authorization: `Bearer ${token}` });
  const caller = String(me.data?.email || "").toLowerCase();
  if (!me.ok || !caller) return fail(401, "Sign in to the workspace again");
  // two-step sign-in on (an authenticator is set up): only a session that passed it may act
  const aal = (() => { try { return JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).aal || ""; } catch { return ""; } })();
  if (aal !== "aal2" && (me.data?.factors || []).some((f: any) => f.status === "verified")) return fail(401, "Finish two-step sign-in first");

  // the platform team and its customers (owner console): who is told is worked out here from the ticket or order, never
  // taken from the page — a new ticket, a customer's reply or a payment proof reaches the staff with that permission;
  // the team's reply or decision reaches the customer on all their devices
  if (b.action === "platform") {
    if (await countRecent(caller, 60) >= 10) return reply(200, { sent: 0, note: "too many notifications this minute" });
    const staff = await call("GET", "/rest/v1/platform_admins?select=email,perms&active=is.true");
    const team = (staff.data || []) as any[];
    const can = (area: string) => team.filter((s) => s.perms == null || s.perms?.[area] === true).map((s) => String(s.email).toLowerCase());
    const isStaff = (area: string) => can(area).includes(caller);
    let to: string[] = [], msg: any = null;
    if (b.event === "ticket") {
      const t = await call("GET", `/rest/v1/support_tickets?select=id,ref,subject,email,company_id,status&id=eq.${enc(String(Number(b.id) || 0))}`);
      const tk = t.data?.[0];
      if (!tk) return fail(404, "No such ticket");
      if (isStaff("support")) {
        to = [String(tk.email).toLowerCase()];
        msg = { title: `Support · ${tk.ref}`, body: clip(b.text || "The support team replied", 140), href: `#/support?t=${tk.id}`, tag: `ticket-${tk.id}` };
      } else {
        const mine = String(tk.email).toLowerCase() === caller ||
          (tk.company_id && ((await call("GET", `/rest/v1/members?select=email&company_id=eq.${enc(tk.company_id)}&email=eq.${enc(caller)}&active=is.true&is_admin=is.true`)).data || []).length);
        if (!mine) return fail(403, "Not your ticket");
        to = can("support");
        msg = { title: `${b.isNew ? "New ticket" : "Customer replied"} · ${tk.ref}`, body: clip(tk.subject, 140), href: `owner.html#/ticket/${tk.id}`, tag: `ticket-${tk.id}` };
      }
    } else if (b.event === "order") {
      const o = await call("GET", `/rest/v1/billing_orders?select=id,ref,email,amount,currency,status,review_note&id=eq.${enc(String(Number(b.id) || 0))}`);
      const od = o.data?.[0];
      if (!od) return fail(404, "No such order");
      if (isStaff("approve") || isStaff("billing")) {
        if (!["approved", "rejected"].includes(od.status)) return reply(200, { sent: 0 });
        to = [String(od.email).toLowerCase()];
        msg = od.status === "approved"
          ? { title: "Subscription activated", body: `${od.ref} · ${od.amount} ${od.currency}`, href: "#/subscription", tag: `order-${od.id}` }
          : { title: "Payment not accepted", body: clip(od.review_note || od.ref, 140), href: "#/subscription", tag: `order-${od.id}` };
      } else {
        if (String(od.email).toLowerCase() !== caller || od.status !== "submitted") return fail(403, "Not your order");
        to = can("billing");
        msg = { title: `Payment to verify · ${od.ref}`, body: `${od.amount} ${od.currency} · ${caller}`, href: "owner.html#/payments", tag: `order-${od.id}` };
      }
    } else if (b.event === "visit") {
      const v = await call("GET", `/rest/v1/support_visits?select=id,ref,email,company_id,status,pay_status,reason,price,currency,scheduled_at&id=eq.${enc(String(Number(b.id) || 0))}`);
      const vd = v.data?.[0];
      if (!vd) return fail(404, "No such visit");
      if (isStaff("support")) {
        to = [String(vd.email).toLowerCase()];
        msg = { title: `Support visit · ${vd.ref}`,
          body: clip(vd.status === "quoted" ? `Price: ${vd.price} ${vd.currency}` : vd.status === "scheduled" ? `Scheduled ${String(vd.scheduled_at || "").slice(0, 16).replace("T", " ")}` : `Status: ${vd.status}`, 140),
          href: `#/support?v=${vd.id}`, tag: `visit-${vd.id}` };
      } else {
        const mine = String(vd.email).toLowerCase() === caller ||
          (vd.company_id && ((await call("GET", `/rest/v1/members?select=email&company_id=eq.${enc(vd.company_id)}&email=eq.${enc(caller)}&active=is.true&is_admin=is.true`)).data || []).length);
        if (!mine) return fail(403, "Not your visit");
        to = can("support");
        msg = { title: `${b.isNew ? "Visit requested" : vd.pay_status === "submitted" ? "Visit payment to check" : "Visit updated"} · ${vd.ref}`,
          body: clip(vd.reason, 140), href: `owner.html#/visit/${vd.id}`, tag: `visit-${vd.id}` };
      }
    } else return fail(400, "Unknown event");
    const sent = await deliver(null, to.filter((e) => e !== caller), { ...msg, kind: "notify", pref: "platform" }, { kind: "notify" });
    await logSend(null, caller, "platform", sent);
    return reply(200, { sent });
  }
  const company = String(b.company || "");
  if (!/^[0-9a-f-]{36}$/i.test(company)) return fail(400, "Say which company");
  const mem = await call("GET", `/rest/v1/members?select=email&company_id=eq.${enc(company)}&email=eq.${enc(caller)}&active=is.true&pending=is.false`);
  if (!mem.ok || !mem.data.length) return fail(403, "You are not a member of this company");
  if (await countRecent(caller, 3600) >= 600) return fail(429, "Too many notifications sent in the last hour");
  // alerts people set (erp/automations.py forecast): when each will next come true if nothing changes. A device sends
  // the alerts it checked (covers: rule ids) with their upcoming times; they replace what was waiting for those rules,
  // so an alert that no longer applies (the bill was paid, attendance was recorded) is withdrawn before it rings.
  if (b.action === "alerts") {
    const covers = (Array.isArray(b.covers) ? b.covers : []).map((x: any) => Number(x)).filter((x: number) => Number.isInteger(x) && x > 0).slice(0, 300);
    for (const id of covers) await call("DELETE", `/rest/v1/push_queue?company_id=eq.${enc(company)}&key=like.${enc(`alert:${id}:`)}*`);
    const ok = new Set(covers);
    const rows = (Array.isArray(b.items) ? b.items : []).slice(0, 150).filter((m: any) => {
      const k = String(m && m.key || "").match(/^alert:(\d+):[0-9a-f]{1,32}$/);
      return k && ok.has(Number(k[1])) && /^\d{4}-\d{2}-\d{2}$/.test(String(m.day || "")) && /^\d{1,2}:\d{2}$/.test(String(m.time || ""));
    }).map((m: any) => {
      const start = Date.parse(m.day + "T00:00:00Z") - 14 * 3600000;   // from the earliest time zone's start of that day
      return { company_id: company, key: String(m.key), at: new Date(Math.max(start, Date.now() + 60000)).toISOString(), sender: caller,
               msg: { to: (Array.isArray(m.to) ? m.to : []).map((x: any) => clip(x, 80)).slice(0, 50), title: clip(m.title, 80), body: clip(m.body, 180),
                      href: String(m.href || "#/alerts").slice(0, 120), tag: clip(m.tag, 40), day: m.day, time: m.time, pref: "reminders" } };
    }).filter((r: any) => Date.parse(r.msg.day + "T23:59:59Z") + 14 * 3600000 > Date.now() && Date.parse(r.at) < Date.now() + 15 * 86400000);
    if (rows.length) await call("POST", "/rest/v1/push_queue?on_conflict=company_id,key", rows, { ...svc, Prefer: "resolution=merge-duplicates" });
    return reply(200, { scheduled: rows.length });
  }
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
      msg: { to, title: clip(b.title, 80), body: clip(b.body, 180), href: String(b.href || "#/").slice(0, 120), tag: clip(b.tag, 40),
             day: /^\d{4}-\d{2}-\d{2}$/.test(String(b.day || "")) ? b.day : undefined,
             time: /^\d{1,2}:\d{2}$/.test(String(b.time || "")) ? b.time : undefined,
             pref: ["fu_due", "reminders"].includes(b.pref) ? b.pref : "fu_due" } },
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
    const pref = ["reactions", "decisions", "fu_new", "fu_due", "reminders"].includes(b.pref) ? b.pref : "";
    // an alert can be for the person whose app noticed it too (their other devices); everything else skips the sender
    sent = await deliver(company, await emailsFor(company, b.to, pref === "reminders" ? undefined : caller), { ...msg, pref }, { kind: "notify" });
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
