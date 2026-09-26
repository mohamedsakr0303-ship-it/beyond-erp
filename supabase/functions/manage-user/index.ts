// Create, update or delete a person's sign-in account from inside Beyond ERP (Users screen).
//
// Only a workspace administrator may call it (checked from their own sign-in token). It uses the project's
// service key — which never leaves Supabase — to create the account already confirmed (no email is sent), set or
// reset the password, block sign-in for inactive users, and record the username in public.members so the person can
// sign in with username + password straight away. People without an email get an internal placeholder address
// (<username>@<PLACEHOLDER_DOMAIN>) that is never written to.
//
// Deployed by .github/workflows/deploy.yml with --no-verify-jwt (it checks the caller's token itself).

const URL_ = Deno.env.get("SUPABASE_URL")!;
const SERVICE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
const PLACEHOLDER_DOMAIN = Deno.env.get("BEYOND_PLACEHOLDER_DOMAIN") || "users.beyond-erp.invalid";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
const fail = (status: number, msg: string) => reply(status, { msg, error: msg });
const svc = { apikey: SERVICE, Authorization: `Bearer ${SERVICE}`, "Content-Type": "application/json" };

async function call(method: string, path: string, body?: unknown, headers: Record<string, string> = svc) {
  const r = await fetch(`${URL_}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { msg: text }; }
  return { ok: r.ok, status: r.status, data };
}
const msgOf = (d: any) => (d && (d.msg || d.message || d.error_description || d.error)) || "Supabase refused the request";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return fail(405, "POST only");

  // 1. who is calling? must be an active workspace administrator
  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  const me = await call("GET", "/auth/v1/user", undefined, { apikey: ANON, Authorization: `Bearer ${token}` });
  const caller = String(me.data?.email || "").toLowerCase();
  if (!me.ok || !caller) return fail(401, "Sign in to the workspace again");
  const adm = await call("GET", `/rest/v1/members?select=email&email=eq.${encodeURIComponent(caller)}&active=is.true&is_admin=is.true`);
  if (!adm.ok || !adm.data.length) return fail(403, "Only workspace administrators can manage sign-in accounts");

  // 2. what to do
  let b: any;
  try { b = await req.json(); } catch { return fail(400, "Send JSON"); }
  const username = String(b.username || "").trim().toLowerCase();
  const email = String(b.email || "").trim().toLowerCase();

  // delete: remove the person's sign-in account and their workspace membership
  if (b.action === "delete") {
    const q = email ? `email=eq.${encodeURIComponent(email)}` : `username=ilike.${encodeURIComponent(username)}`;
    const row = await call("GET", `/rest/v1/members?select=email,is_admin,active&${q}&limit=1`);
    const target: string = row.ok && row.data.length ? row.data[0].email : (email || (username ? `${username}@${PLACEHOLDER_DOMAIN}` : ""));
    if (!target) return fail(400, "Say which account to delete");
    if (target === caller) return fail(400, "You cannot delete your own sign-in account");
    if (row.ok && row.data.length && row.data[0].is_admin && row.data[0].active) {
      const admins = await call("GET", "/rest/v1/members?select=email&is_admin=is.true&active=is.true");
      if (admins.ok && admins.data.length <= 1) return fail(400, "At least one active workspace administrator must remain");
    }
    const id = (await call("POST", "/rest/v1/rpc/auth_user_id", { p_email: target })).data;
    if (id) {
      const r = await call("DELETE", `/auth/v1/admin/users/${id}`);
      if (!r.ok) return fail(400, msgOf(r.data));
    }
    await call("DELETE", `/rest/v1/members?email=eq.${encodeURIComponent(target)}`);
    return reply(200, { deleted: target, account: !!id });
  }
  const password = b.password ? String(b.password) : "";
  const active = b.active !== false;
  if (!/^[a-z0-9][a-z0-9._-]{1,39}$/.test(username)) {
    return fail(400, "Usernames for signing in use 2–40 letters, digits, dots, dashes or underscores (no spaces)");
  }
  if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return fail(400, "Enter a valid email address");
  if (password && password.length < 6) return fail(400, "Passwords need at least 6 characters");
  const wanted = email || `${username}@${PLACEHOLDER_DOMAIN}`;

  // 3. the account this username already has (if any), else the one with this email
  const cur = await call("GET", `/rest/v1/members?select=email,username&username=ilike.${encodeURIComponent(username)}&limit=1`);
  const oldEmail: string = cur.ok && cur.data.length ? cur.data[0].email : "";
  const idOf = async (e: string) => (await call("POST", "/rest/v1/rpc/auth_user_id", { p_email: e })).data || null;
  let id = oldEmail ? await idOf(oldEmail) : null;
  if (!id) id = await idOf(wanted);

  let created = false;
  if (!id) {
    if (!password) return fail(400, "Set a password for this person (they have no sign-in account yet)");
    const r = await call("POST", "/auth/v1/admin/users", {
      email: wanted, password, email_confirm: true, user_metadata: { username, full_name: b.full_name || "" },
    });
    if (!r.ok) {
      return fail(400, email ? msgOf(r.data)
        : `${msgOf(r.data)} — give this person an email address and save again`);
    }
    id = r.data.id;
    created = true;
  } else {
    const upd: any = { ban_duration: active ? "none" : "876000h", user_metadata: { username, full_name: b.full_name || "" } };
    if (password) upd.password = password;
    if (oldEmail && oldEmail !== wanted) { upd.email = wanted; upd.email_confirm = true; }
    const r = await call("PUT", `/auth/v1/admin/users/${id}`, upd);
    if (!r.ok) return fail(400, msgOf(r.data));
  }

  // 4. workspace membership + username (the username moves if someone else had it)
  await call("PATCH", `/rest/v1/members?username=ilike.${encodeURIComponent(username)}&email=neq.${encodeURIComponent(oldEmail || wanted)}`,
    { username: null });
  if (oldEmail && oldEmail !== wanted) {
    await call("PATCH", `/rest/v1/members?email=eq.${encodeURIComponent(oldEmail)}`, { email: wanted });
  }
  const m = await call("POST", "/rest/v1/members?on_conflict=email",
    { email: wanted, username, active, added_by: caller },
    { ...svc, Prefer: "resolution=merge-duplicates,return=minimal" });
  if (!m.ok) return fail(400, msgOf(m.data));
  return reply(200, { email: wanted, username, created, placeholder: !email });
});
