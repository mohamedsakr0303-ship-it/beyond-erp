// Sign in with username + password.
//
// Supabase accounts sign in by email, so this function looks up the email that belongs to a workspace username
// (public.members.username) with the service key — on the server, so the page never learns anyone's email — and
// then asks Supabase Auth for a normal session. An unknown username gets exactly the same answer as a wrong
// password, so the function cannot be used to find out which usernames or emails exist.
//
// Deployed by .github/workflows/deploy.yml with --no-verify-jwt (people are not signed in yet when they call it).

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
const invalid = () => reply(400, { code: 400, error_code: "invalid_credentials", msg: "Invalid login credentials" });

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return reply(405, { msg: "POST only" });
  let username = "", password = "";
  try {
    const b = await req.json();
    username = String(b.username ?? "").trim().toLowerCase();
    password = String(b.password ?? "");
  } catch {
    return reply(400, { msg: "Send {username, password}" });
  }
  if (!username || !password || username.length > 100) return invalid();

  const found = await fetch(
    `${URL_}/rest/v1/members?select=email&active=is.true&username=ilike.${encodeURIComponent(username.replace(/[%_*,()]/g, ""))}&limit=1`,
    { headers: { apikey: SERVICE, Authorization: `Bearer ${SERVICE}` } },
  );
  const rows = found.ok ? await found.json() : [];
  if (!rows.length) {
    await new Promise((r) => setTimeout(r, 200 + Math.random() * 200)); // same timing as a real attempt
    return invalid();
  }
  const auth = await fetch(`${URL_}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { apikey: ANON, "Content-Type": "application/json" },
    body: JSON.stringify({ email: rows[0].email, password }),
  });
  return new Response(await auth.text(), { status: auth.status, headers: { ...cors, "Content-Type": "application/json" } });
});
