// The AI assistant for companies on the shared workspace (Claude, through the official Anthropic SDK).
//
//   POST {company, base, context, tools, messages}     (signed in; the caller's own token)
//     -> {message}: one Messages API response, exactly as returned (the page runs the tools it asks for, as the
//        signed-in person, and calls again with the results).
//
// The company's settings and provider key live in ai_config, which only this function can read (service key). The
// model, effort, standing instructions, who may use it, the daily limit and whether it may change data come from
// there, never from the browser: the page only sends the conversation, its own description of the ERP (base,
// context) and the tool definitions, which are filtered here to the known tools allowed by the policy.
//
// Deployed by .github/workflows/deploy.yml with --no-verify-jwt (it checks the caller's token itself).
import Anthropic from "npm:@anthropic-ai/sdk";

const URL_ = Deno.env.get("SUPABASE_URL")!;
const SERVICE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const ANON = Deno.env.get("SUPABASE_ANON_KEY")!;

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

async function call(method: string, path: string, body?: unknown, headers: Record<string, string> = svc) {
  const r = await fetch(`${URL_}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { msg: text }; }
  return { ok: r.ok, status: r.status, data };
}

const MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"];

// Any other provider speaks the OpenAI-style "chat completions" API: translate the request there and the reply back,
// so the page always sees the same message shape (text / tool_use blocks). Mirrors to_openai / from_openai in erp/ai.py.
const textOf = (c: any) => typeof c === "string" ? c : (Array.isArray(c) ? c.filter((b: any) => b?.type === "text").map((b: any) => b.text || "").join("\n") : "");
function toOpenAI(p: any) {
  const msgs: any[] = [{ role: "system", content: p.system.map((b: any) => b.text).join("\n\n") }];
  for (const m of p.messages) {
    if (m.role === "user") {
      if (typeof m.content === "string") { msgs.push({ role: "user", content: m.content }); continue; }
      for (const b of m.content || []) if (b?.type === "tool_result")
        msgs.push({ role: "tool", tool_call_id: b.tool_use_id, content: typeof b.content === "string" ? b.content : JSON.stringify(b.content) });
      const t = textOf(m.content); if (t) msgs.push({ role: "user", content: t });
    } else {
      const calls = (Array.isArray(m.content) ? m.content : []).filter((b: any) => b?.type === "tool_use")
        .map((b: any) => ({ id: b.id, type: "function", function: { name: b.name, arguments: JSON.stringify(b.input || {}) } }));
      const reasoning = (Array.isArray(m.content) ? m.content : []).filter((b: any) => b?.type === "reasoning").map((b: any) => b.text || "").join("");
      msgs.push({ role: "assistant", content: textOf(m.content) || (calls.length ? "" : null), ...(calls.length ? { tool_calls: calls } : {}),
                  ...(reasoning ? { reasoning_content: reasoning } : {}) });
    }
  }
  return { model: p.model, messages: msgs, ...(p.tools.length ? {
           tools: p.tools.map((t: any) => ({ type: "function", function: { name: t.name, description: t.description, parameters: t.input_schema } })) } : {}) };
}
function fromOpenAI(r: any) {
  const ch = r?.choices?.[0];
  if (!ch) throw new Error(r?.error ? "The AI provider refused the request: " + (r.error.message || r.error)
    : "The AI provider's reply had no answer in it — check the address and the model name. It said: " + JSON.stringify(r || {}).slice(0, 300));
  const m = ch.message || {}, content: any[] = [];
  if (m.reasoning_content) content.push({ type: "reasoning", text: String(m.reasoning_content) });
  if (m.content) content.push({ type: "text", text: typeof m.content === "string" ? m.content : JSON.stringify(m.content) });
  (m.tool_calls || []).forEach((tc: any, i: number) => { let input = {}; try { input = JSON.parse(tc.function?.arguments || "{}"); } catch { /* bad JSON */ }
    content.push({ type: "tool_use", id: tc.id || "call_" + i, name: tc.function?.name || "", input }); });
  let stop = ({ tool_calls: "tool_use", function_call: "tool_use", length: "max_tokens", content_filter: "refusal" } as any)[ch.finish_reason] || "end_turn";
  if (content.some(b => b.type === "tool_use")) stop = "tool_use";
  if (!content.some(b => b.type === "text" || b.type === "tool_use")) content.push({ type: "text", text: "(The model returned an empty answer.)" });
  return { role: "assistant", content, stop_reason: stop, model: r.model || "" };
}
const TOOLS = ["erp_find", "erp_read", "erp_write"];
const MAX_REQUEST = 30_000_000;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return fail(405, "POST only");
  const raw = await req.text();
  if (raw.length > MAX_REQUEST) return fail(413, "This conversation is too long — start a new one");
  let b: any;
  try { b = JSON.parse(raw); } catch { return fail(400, "Send JSON"); }

  // 1. who is calling, for which company
  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  const me = await call("GET", "/auth/v1/user", undefined, { apikey: ANON, Authorization: `Bearer ${token}` });
  const caller = String(me.data?.email || "").toLowerCase();
  if (!me.ok || !caller) return fail(401, "Sign in to the workspace again");
  const company = String(b.company || "");
  if (!/^[0-9a-f-]{36}$/i.test(company)) return fail(400, "Say which company");
  const inCo = `company_id=eq.${enc(company)}`;
  const mem = await call("GET", `/rest/v1/members?select=is_super&${inCo}&email=eq.${enc(caller)}&active=is.true&pending=is.false`);
  if (!mem.ok || !mem.data.length) return fail(403, "You are not a member of this company");
  const co = await call("GET", `/rest/v1/companies?select=created_by&id=eq.${enc(company)}`);
  const isSuper = !!mem.data[0].is_super || (co.ok && co.data.length && String(co.data[0].created_by || "").toLowerCase() === caller);

  // 2. the company's assistant: switched on, the caller allowed, under the daily limit
  const cfg = await call("GET", `/rest/v1/ai_config?select=api_key,settings&${inCo}`);
  const row = cfg.ok && cfg.data.length ? cfg.data[0] : null;
  const s: any = row?.settings || {};
  if (!row || (!s.enabled && !(b.test && isSuper))) return fail(403, "The AI assistant is switched off for this company");
  const other = s.provider && s.provider !== "anthropic";
  if (!row.api_key && !other) return fail(400, "No AI provider key is installed yet — a super administrator adds it in Settings → AI assistant");
  if (!isSuper && s.access === "list") {
    const p = await call("GET", `/rest/v1/people?select=username&email=eq.${enc(caller)}&limit=1`);
    const username = String(p.ok && p.data.length ? p.data[0].username || "" : "").toLowerCase();
    const users = (Array.isArray(s.users) ? s.users : []).map((u: string) => String(u).toLowerCase());
    if (!username || !users.includes(username)) return fail(403, "The AI assistant is switched off for you — ask a super administrator");
  }
  const day = new Date().toISOString().slice(0, 10);
  const used = await call("GET", `/rest/v1/ai_usage?select=calls&${inCo}&email=eq.${enc(caller)}&day=eq.${day}`);
  const calls = used.ok && used.data.length ? Number(used.data[0].calls) || 0 : 0;
  const limit = Number(s.daily_limit) || 0;
  if (!isSuper && limit && calls >= limit) return fail(429, `Daily limit reached (${limit} requests) — try again tomorrow`);

  // 3. the request: policy from the database, conversation from the page
  const test = !!b.test && isSuper;   // Settings → Test connection (super administrators): one short question, no tools
  let messages = test ? [{ role: "user", content: "Reply with the single word OK." }] : (Array.isArray(b.messages) ? b.messages : []);
  if (!messages.length || messages[0]?.role !== "user" || messages.some((m: any) => !["user", "assistant"].includes(m?.role)))
    return fail(400, "Malformed conversation");
  const model = other ? String(s.model || "") : (MODELS.includes(s.model) ? s.model : "claude-opus-5-5");
  const write = ["off", "confirm", "auto"].includes(s.write) ? s.write : "confirm";
  const tools = test ? [] : (Array.isArray(b.tools) ? b.tools : [])
    .filter((t: any) => t && TOOLS.includes(t.name) && !(t.name === "erp_write" && write === "off"));
  let fixed = String(b.base || "").slice(0, 20000);
  if (s.instructions) fixed += "\n\nStanding instructions from the company's super administrator:\n" + String(s.instructions).slice(0, 8000);
  const params: any = {
    model, max_tokens: 16000, messages, tools,
    system: [{ type: "text", text: fixed, cache_control: { type: "ephemeral" } },
             { type: "text", text: String(b.context || "").slice(0, 20000) || "-" }],
  };
  if (!model.startsWith("claude-haiku")) {
    params.thinking = { type: "adaptive" };
    params.output_config = { effort: ["low", "medium", "high"].includes(s.effort) ? s.effort : "medium" };
    params.betas = ["server-side-fallback-2026-07-01"];
    params.fallbacks = "default";   // a declined request is re-run on Anthropic's recommended fallback model
  }

  if (other) {   // any other provider, at the address the super administrator set
    const base = String(s.base_url || "").replace(/\/+$/, "");
    if (!/^https:\/\//.test(base)) return fail(400, "Set the provider's https address in Settings → AI assistant (local addresses only work from the desktop app)");
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (row.api_key) headers.Authorization = "Bearer " + row.api_key;
    let r: Response;
    try { r = await fetch(base + "/chat/completions", { method: "POST", headers, body: JSON.stringify(toOpenAI(params)) }); }
    catch { return fail(502, "Could not reach the AI provider — try again"); }
    const raw = await r.text();
    let data: any; try { data = JSON.parse(raw); } catch {
      return fail(502, `The address ${base} did not answer like an AI API (it sent back: ${raw.trim().slice(0, 160)}) — check the provider's address in Settings → AI assistant`); }
    if (r.status === 401 || r.status === 403) return fail(400, "The AI provider refused the key — a super administrator should check it in Settings → AI assistant");
    if (r.status === 429) return fail(429, "The AI provider is busy (rate limit) — try again in a minute");
    if (!r.ok) return fail(400, `The AI provider refused the request (${r.status})${data?.error ? ": " + (data.error.message || data.error) : ""}`);
    let message;
    try { message = fromOpenAI(data); } catch (e: any) { return fail(502, e.message); }
    await call("POST", "/rest/v1/ai_usage?on_conflict=company_id,email,day", { company_id: company, email: caller, day, calls: calls + 1 },
               { ...svc, Prefer: "resolution=merge-duplicates" });
    return reply(200, { message });
  }
  params.messages = messages.map((m: any) => Array.isArray(m.content) ? { ...m, content: m.content.filter((x: any) => x?.type !== "reasoning") } : m);
  const client = new Anthropic({ apiKey: row.api_key });
  let message: any;
  try {
    message = await client.beta.messages.create(params);
  } catch (e: any) {
    if (e instanceof Anthropic.AuthenticationError) return fail(400, "The AI provider refused the key — a super administrator should check it in Settings → AI assistant");
    if (e instanceof Anthropic.RateLimitError) return fail(429, "The AI provider is busy (rate limit) — try again in a minute");
    if (e instanceof Anthropic.BadRequestError) return fail(400, `The AI provider refused the request: ${e.message}`);
    if (e instanceof Anthropic.APIConnectionError) return fail(502, "Could not reach the AI provider — try again");
    if (e instanceof Anthropic.APIError) return fail(502, `The AI provider had a problem (${e.status}) — try again`);
    throw e;
  }
  await call("POST", "/rest/v1/ai_usage?on_conflict=company_id,email,day",
             { company_id: company, email: caller, day, calls: calls + 1 },
             { ...svc, Prefer: "resolution=merge-duplicates" });
  return reply(200, { message });
});
