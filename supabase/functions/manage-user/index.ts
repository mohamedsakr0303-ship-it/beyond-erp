// Create, update or delete a person's sign-in account from inside Beyond ERP (Users screen).
//
// Only an administrator of the company named in the request may call it (checked from their own sign-in token). It
// uses the project's service key — which never leaves Supabase — to create the account already confirmed (no email
// is sent), set or reset the password, block sign-in for inactive users, and record the username (people.username,
// unique across every company) and the company membership, so the person can sign in with username + password
// straight away. People without an email get an internal placeholder address (<username>@<PLACEHOLDER_DOMAIN>).
//
// An account that also belongs to another company is never changed from here (another company's administrator must
// not be able to reset its password or take its username); it is only added to / removed from this company.
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
const enc = encodeURIComponent;

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

  let b: any;
  try { b = await req.json(); } catch { return fail(400, "Send JSON"); }

  // 1. who is calling, for which company? must be an active administrator of it
  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  const me = await call("GET", "/auth/v1/user", undefined, { apikey: ANON, Authorization: `Bearer ${token}` });
  const caller = String(me.data?.email || "").toLowerCase();
  if (!me.ok || !caller) return fail(401, "Sign in to the workspace again");
  // two-step sign-in on (an authenticator is set up): only a session that passed it may act
  const aal = (() => { try { return JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).aal || ""; } catch { return ""; } })();
  if (aal !== "aal2" && (me.data?.factors || []).some((f: any) => f.status === "verified")) return fail(401, "Finish two-step sign-in first");
  let company = String(b.company || "");
  if (!company) {  // app versions before 1.3: the caller's only company
    const mine = await call("GET", `/rest/v1/members?select=company_id&email=eq.${enc(caller)}&active=is.true&pending=is.false`);
    if (mine.ok && mine.data.length === 1) company = mine.data[0].company_id;
  }
  if (!/^[0-9a-f-]{36}$/i.test(company)) return fail(400, "Say which company");
  const inCo = `company_id=eq.${enc(company)}`;
  const adm = await call("GET", `/rest/v1/members?select=email,is_super&${inCo}&email=eq.${enc(caller)}&active=is.true&pending=is.false&is_admin=is.true`);
  if (!adm.ok || !adm.data.length) return fail(403, "Only administrators of this company can manage sign-in accounts");
  // administrators add people; changing or removing someone's account is for the company's super administrators
  const isSuper = !!adm.data[0].is_super;

  const username = String(b.username || "").trim().toLowerCase();
  // a username may be the person's email address: they then sign in with that address (sign-in treats "@" as an email)
  const userIsEmail = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(username);
  const email = String(b.email || "").trim().toLowerCase() || (userIsEmail ? username : "");
  const idOf = async (e: string) => (await call("POST", "/rest/v1/rpc/auth_user_id", { p_email: e })).data || null;
  const person = async (q: string) => { const r = await call("GET", `/rest/v1/people?select=email,username&${q}&limit=1`); return r.ok && r.data.length ? r.data[0] : null; };
  const otherCompanies = async (e: string) => {
    const r = await call("GET", `/rest/v1/members?select=company_id&email=eq.${enc(e)}&company_id=neq.${enc(company)}`);
    return r.ok ? r.data.length : 0;
  };

  // delete: remove the person from this company; their sign-in account goes too unless another company still has them
  if (b.action === "delete") {
    const p = email ? await person(`email=eq.${enc(email)}`) : (username ? await person(`username=ilike.${enc(username)}`) : null);
    const target: string = p ? p.email : (email || (username ? `${username}@${PLACEHOLDER_DOMAIN}` : ""));
    if (!target) return fail(400, "Say which account to delete");
    if (target === caller) return fail(400, "You cannot delete your own sign-in account");
    if (!isSuper) return fail(403, "Only a super administrator can remove people");
    const row = await call("GET", `/rest/v1/members?select=is_admin,active&${inCo}&email=eq.${enc(target)}`);
    if (row.ok && row.data.length && row.data[0].is_admin && row.data[0].active) {
      const admins = await call("GET", `/rest/v1/members?select=email&${inCo}&is_admin=is.true&active=is.true&pending=is.false`);
      if (admins.ok && admins.data.length <= 1) return fail(400, "At least one active administrator must remain");
    }
    await call("DELETE", `/rest/v1/members?${inCo}&email=eq.${enc(target)}`);
    let account = false;
    if (!(await otherCompanies(target))) {
      const id = await idOf(target);
      if (id) {
        const r = await call("DELETE", `/auth/v1/admin/users/${id}`);
        if (!r.ok) return fail(400, msgOf(r.data));
        account = true;
      }
      await call("DELETE", `/rest/v1/people?email=eq.${enc(target)}`);
    }
    return reply(200, { deleted: target, account });
  }

  // two-step sign-in: someone lost their phone and recovery codes — a super administrator removes their authenticator
  // so they sign in with the password and set it up again
  if (b.action === "mfa_off") {
    if (!isSuper) return fail(403, "Only a super administrator can turn off someone's two-step sign-in");
    const p = email ? await person(`email=eq.${enc(email)}`) : (username ? await person(`username=ilike.${enc(username)}`) : null);
    const target: string = p ? p.email : (email || (username ? `${username}@${PLACEHOLDER_DOMAIN}` : ""));
    const here = await call("GET", `/rest/v1/members?select=email&${inCo}&email=eq.${enc(target)}`);
    if (!target || !here.ok || !here.data.length) return fail(404, "This person is not in this company");
    if (await otherCompanies(target)) return fail(403, "This account also belongs to another company — its owner turns two-step sign-in off from their own profile");
    const id = await idOf(target);
    if (!id) return reply(200, { removed: 0 });
    const fs = await call("GET", `/auth/v1/admin/users/${id}/factors`);
    let removed = 0;
    for (const f of (fs.ok && Array.isArray(fs.data) ? fs.data : [])) {
      const r = await call("DELETE", `/auth/v1/admin/users/${id}/factors/${f.id}`);
      if (r.ok) removed++;
    }
    return reply(200, { removed });
  }

  const password = b.password ? String(b.password) : "";
  const active = b.active !== false;
  if (userIsEmail) {
    if (email !== username) return fail(400, "When the username is an email address, the email must be that same address (or left blank)");
  } else if (!/^[a-z0-9][a-z0-9._-]{1,39}$/.test(username)) {
    return fail(400, "Usernames for signing in use 2–40 letters, digits, dots, dashes or underscores (no spaces) — or an email address");
  }
  if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return fail(400, "Enter a valid email address");
  if (password && password.length < 6) return fail(400, "Passwords need at least 6 characters");
  const wanted = email || `${username}@${PLACEHOLDER_DOMAIN}`;

  // 2. who is this? Usernames are shared by every company, so a username that belongs to someone outside this company
  // is refused — unless the administrator adds that very person on purpose, by their email (then no password).
  const inThis = async (e: string) => {
    const r = await call("GET", `/rest/v1/members?select=email&${inCo}&email=eq.${enc(e)}`);
    return r.ok && r.data.length > 0;
  };
  const suggest = async () => {
    const c = await call("GET", `/rest/v1/companies?select=name&id=eq.${enc(company)}`);
    const tag = String(c.ok && c.data.length ? c.data[0].name : "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, " ").trim().split(" ")[0] || "co";
    for (const cand of [`${username}.${tag}`, `${username}.${tag}2`, `${username}${Math.floor(Math.random() * 90 + 10)}`]) {
      if (!(await person(`username=ilike.${enc(cand)}`))) return cand;
    }
    return `${username}.${tag}${Date.now() % 1000}`;
  };
  const byName = await person(`username=ilike.${enc(username)}`);
  if (byName && !(await inThis(byName.email)) && !(email && email === byName.email && !password)) {
    return fail(409, `The username “${username}” is already taken by someone in another company (usernames are shared by all companies). ` +
      `Choose another one, for example “${await suggest()}”.`);
  }
  const byEmail = email ? await person(`email=eq.${enc(email)}`) : null;
  if (byEmail && byEmail.username && byEmail.username !== username && !(await inThis(email))) {
    return fail(409, `This email already has a sign-in account with the username “${byEmail.username}” — use that username to add them.`);
  }
  const oldEmail: string = byName ? byName.email : "";
  const target = oldEmail || wanted;
  let id = await idOf(target);
  if (!id && target !== wanted) id = await idOf(wanted);
  const shared = id ? await otherCompanies(target) : 0;

  let created = false;
  if (!id) {
    if (!password) return fail(400, "Set a password for this person (they have no sign-in account yet)");
    const r = await call("POST", "/auth/v1/admin/users", {
      email: wanted, password, email_confirm: true, user_metadata: { username, full_name: b.full_name || "" },
    });
    if (!r.ok) return fail(400, email ? msgOf(r.data) : `${msgOf(r.data)} — give this person an email address and save again`);
    id = r.data.id;
    created = true;
  } else if (shared) {
    // also a member of another company: leave the account alone (their own password), just add them here
    if (password) {
      return fail(400, "This person already has a sign-in account (used by another company too). They keep their own password — leave the password empty to add them.");
    }
  } else {
    if (!isSuper && target !== caller && (await inThis(target))) return fail(403, "Only a super administrator can change someone's sign-in account — administrators may add people");
    const upd: any = { ban_duration: active ? "none" : "876000h", user_metadata: { username, full_name: b.full_name || "" } };
    if (password) upd.password = password;
    if (oldEmail && oldEmail !== wanted) { upd.email = wanted; upd.email_confirm = true; }
    const r = await call("PUT", `/auth/v1/admin/users/${id}`, upd);
    if (!r.ok) return fail(400, msgOf(r.data));
  }

  // 3. person (username) + membership of this company
  const finalEmail = shared ? target : wanted;
  if (!shared && oldEmail && oldEmail !== wanted) {
    await call("PATCH", `/rest/v1/people?email=eq.${enc(oldEmail)}`, { email: wanted });
    await call("PATCH", `/rest/v1/members?email=eq.${enc(oldEmail)}`, { email: wanted });
  }
  const pr = await call("POST", "/rest/v1/people?on_conflict=email", { email: finalEmail, username: shared ? undefined : username },
    { ...svc, Prefer: "resolution=merge-duplicates,return=minimal" });
  if (!pr.ok) return fail(400, /duplicate|unique/i.test(msgOf(pr.data)) ? `The username ${username} is already used by another account — choose another` : msgOf(pr.data));
  const m = await call("POST", "/rest/v1/members?on_conflict=company_id,email",
    { company_id: company, email: finalEmail, active, pending: false, added_by: caller },
    { ...svc, Prefer: "resolution=merge-duplicates,return=minimal" });
  if (!m.ok) return fail(400, msgOf(m.data));
  return reply(200, { email: finalEmail, username, created, placeholder: !email && !shared, shared: !!shared });
});
