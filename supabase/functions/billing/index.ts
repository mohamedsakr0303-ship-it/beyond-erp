// Paying for a BEYOND ERP subscription by card / mobile wallet through Paymob (Egypt).
//
//   POST {action: "checkout", company, plan, months: 1 | 12, extra_users, return_url}   (signed in, company administrator)
//     -> {url}: open it to pay on Paymob's checkout page.
//     The price is worked out in the database (billing_quote / billing_open_payment), never taken from the browser.
//   POST ?hook=paymob   (Paymob's "transaction processed" callback, no sign-in)
//     The HMAC is checked with the account's HMAC secret; a successful payment of the exact amount extends the
//     company's paid period once (billing_paid is idempotent).
//
// Secrets (Supabase → Edge Functions → Secrets):
//   PAYMOB_SECRET_KEY      Paymob dashboard → Settings → Account info → Secret key
//   PAYMOB_PUBLIC_KEY      … → Public key
//   PAYMOB_HMAC            … → HMAC
//   PAYMOB_INTEGRATION_IDS comma-separated integration ids to offer (card, wallet…), e.g. "4851234,4851235"
//   PAYMOB_BASE            optional, default https://accept.paymob.com
// Without them the checkout answers "card payments are not set up" and customers pay by InstaPay / transfer, which the
// owner records from the app.
//
// Deployed by .github/workflows/deploy.yml with --no-verify-jwt (the webhook carries no token; checkout checks it itself).

const URL_ = Deno.env.get("SUPABASE_URL")!;
const SERVICE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
const PM_BASE = (Deno.env.get("PAYMOB_BASE") || "https://accept.paymob.com").replace(/\/+$/, "");
const PM_SECRET = Deno.env.get("PAYMOB_SECRET_KEY") || "";
const PM_PUBLIC = Deno.env.get("PAYMOB_PUBLIC_KEY") || "";
const PM_HMAC = Deno.env.get("PAYMOB_HMAC") || "";
const PM_METHODS = (Deno.env.get("PAYMOB_INTEGRATION_IDS") || "").split(",").map((x) => x.trim()).filter(Boolean)
  .map((x) => (/^\d+$/.test(x) ? Number(x) : x));

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

// Paymob's HMAC over the transaction callback: these fields, in this order, concatenated, HMAC-SHA512, hex.
const HMAC_FIELDS = ["amount_cents", "created_at", "currency", "error_occured", "has_parent_transaction", "id", "integration_id",
  "is_3d_secure", "is_auth", "is_capture", "is_refunded", "is_standalone_payment", "is_voided", "order.id", "owner", "pending",
  "source_data.pan", "source_data.sub_type", "source_data.type", "success"];
const pick = (o: any, path: string) => path.split(".").reduce((v, k) => (v == null ? v : v[k]), o);
async function hmacHex(secret: string, text: string) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-512" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(text)));
  return Array.from(sig).map((b) => b.toString(16).padStart(2, "0")).join("");
}
function sameHex(a: string, b: string) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

async function webhook(req: Request, url: URL) {
  if (!PM_HMAC) return fail(503, "Paymob HMAC secret not set");
  let b: any;
  try { b = await req.json(); } catch { return fail(400, "Send JSON"); }
  const obj = b?.obj;
  if (!obj || (b.type && b.type !== "TRANSACTION")) return reply(200, { ignored: true });
  const text = HMAC_FIELDS.map((f) => { const v = pick(obj, f); return v === null || v === undefined ? "" : String(v); }).join("");
  const want = await hmacHex(PM_HMAC, text);
  if (!sameHex(want, String(url.searchParams.get("hmac") || "").toLowerCase())) return fail(401, "Bad signature");
  const reference = String(obj.order?.merchant_order_id || obj.special_reference || "");
  if (!reference) return reply(200, { ignored: "no reference" });
  const success = obj.success === true && obj.pending !== true && obj.is_voided !== true && obj.is_refunded !== true;
  const r = await call("POST", "/rest/v1/rpc/billing_paid",
    { p_reference: reference, p_txn: String(obj.id), p_amount_cents: Number(obj.amount_cents), p_success: success });
  // 200 even when we refuse (wrong amount…): Paymob would otherwise retry a payment we will never accept
  return reply(200, r.ok ? r.data : { refused: msgOf(r.data) });
}

async function checkout(req: Request) {
  let b: any;
  try { b = await req.json(); } catch { return fail(400, "Send JSON"); }
  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  const user = { apikey: ANON, Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const me = await call("GET", "/auth/v1/user", undefined, user);
  const email = String(me.data?.email || "").toLowerCase();
  if (!me.ok || !email) return fail(401, "Sign in to the workspace again");
  const company = String(b.company || "");
  if (!/^[0-9a-f-]{36}$/i.test(company)) return fail(400, "Say which company");
  const months = Number(b.months) === 12 ? 12 : 1, extra = Math.max(0, Math.floor(Number(b.extra_users) || 0));
  // the caller's own permissions decide: administrator of the company, plan big enough for its people
  const q = await call("POST", "/rest/v1/rpc/billing_quote", { p_company: company, p_plan: String(b.plan || ""), p_months: months, p_extra_users: extra }, user);
  if (!q.ok) return fail(q.status === 401 ? 401 : 400, msgOf(q.data));
  if (!PM_SECRET || !PM_PUBLIC || !PM_METHODS.length) {
    return fail(503, "Card payments are not set up yet. Pay by InstaPay or bank transfer (see the details on this screen) and we will activate your subscription.");
  }
  const reference = `BEY-${company.slice(0, 8)}-${Date.now().toString(36)}-${crypto.randomUUID().slice(0, 6)}`.toUpperCase();
  const open = await call("POST", "/rest/v1/rpc/billing_open_payment",
    { p_company: company, p_plan: q.data.plan, p_months: months, p_extra_users: extra, p_reference: reference, p_email: email });
  if (!open.ok) return fail(400, msgOf(open.data));
  const amountCents = Math.round(Number(open.data.amount) * 100);
  const back = /^https?:\/\//i.test(String(b.return_url || "")) ? String(b.return_url) : undefined;
  const name = String(b.name || email.split("@")[0]).trim().split(/\s+/);
  const intent = await fetch(`${PM_BASE}/v1/intention/`, {
    method: "POST",
    headers: { Authorization: `Token ${PM_SECRET}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      amount: amountCents, currency: q.data.currency || "EGP", payment_methods: PM_METHODS, special_reference: reference,
      items: [{ name: `BEYOND ERP — ${q.data.plan} (${months === 12 ? "1 year" : "1 month"}${extra ? `, +${extra} users` : ""})`, amount: amountCents, quantity: 1 }],
      billing_data: { first_name: name[0] || "Customer", last_name: name.slice(1).join(" ") || "-", email, phone_number: String(b.phone || "+200000000000") },
      notification_url: `${URL_}/functions/v1/billing?hook=paymob`, redirection_url: back,
      extras: { company, plan: q.data.plan, months },
    }),
  });
  const it: any = await intent.json().catch(() => ({}));
  if (!intent.ok || !it.client_secret) {
    await call("PATCH", `/rest/v1/billing_payments?reference=eq.${encodeURIComponent(reference)}`, { status: "failed", note: JSON.stringify(it).slice(0, 500) });
    return fail(502, "Paymob did not accept the payment request: " + (it.detail || it.message || intent.status));
  }
  return reply(200, { url: `${PM_BASE}/unifiedcheckout/?publicKey=${encodeURIComponent(PM_PUBLIC)}&clientSecret=${encodeURIComponent(it.client_secret)}`,
                      reference, amount: open.data.amount, currency: q.data.currency });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return fail(405, "POST only");
  const url = new URL(req.url);
  try {
    if (url.searchParams.get("hook") === "paymob") return await webhook(req, url);
    return await checkout(req);
  } catch (e) {
    return fail(500, String((e as Error)?.message || e));
  }
});
