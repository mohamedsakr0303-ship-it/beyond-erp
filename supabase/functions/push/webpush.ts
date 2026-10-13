// Web Push with the platform's own crypto (no npm package): VAPID signing (RFC 8292) and message encryption
// (RFC 8291, aes128gcm). Works the same in Deno (the Supabase function) and Node 22 (the tests).

const te = new TextEncoder();
export const b64u = (b: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(b as ArrayBuffer))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
export const unb64u = (s: string) => {
  const t = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  return Uint8Array.from(atob(t), c => c.charCodeAt(0));
};
const cat = (...parts: Uint8Array[]) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let i = 0;
  for (const p of parts) { out.set(p, i); i += p.length; }
  return out;
};
async function hmac(key: Uint8Array, data: Uint8Array) {
  const k = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, data));
}
// HKDF-Expand for one block (all lengths here are ≤ 32)
const expand = async (prk: Uint8Array, info: Uint8Array, len: number) => (await hmac(prk, cat(info, new Uint8Array([1])))).slice(0, len);

// a new VAPID key pair: the private half as a JWK (kept on the server), the public half as the raw point (base64url)
export async function newKeys() {
  const kp = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  return { private_jwk: await crypto.subtle.exportKey("jwk", kp.privateKey), public_key: b64u(await crypto.subtle.exportKey("raw", kp.publicKey)) };
}

async function vapidHeader(endpoint: string, keys: { private_jwk: JsonWebKey; public_key: string }, subject: string) {
  const aud = new URL(endpoint).origin;
  const head = b64u(te.encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  const body = b64u(te.encode(JSON.stringify({ aud, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: subject })));
  const key = await crypto.subtle.importKey("jwk", keys.private_jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, te.encode(`${head}.${body}`));   // raw r‖s, as JWS wants
  return `vapid t=${head}.${body}.${b64u(sig)}, k=${keys.public_key}`;
}

// the encrypted body for one subscription (one record, aes128gcm)
export async function encrypt(payload: Uint8Array, p256dh: string, auth: string, salt = crypto.getRandomValues(new Uint8Array(16))) {
  const uaPublic = unb64u(p256dh), authSecret = unb64u(auth);
  const eph = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
  const asPublic = new Uint8Array(await crypto.subtle.exportKey("raw", eph.publicKey));
  const ua = await crypto.subtle.importKey("raw", uaPublic, { name: "ECDH", namedCurve: "P-256" }, false, []);
  const shared = new Uint8Array(await crypto.subtle.deriveBits({ name: "ECDH", public: ua } as any, eph.privateKey, 256));
  const ikm = await expand(await hmac(authSecret, shared), cat(te.encode("WebPush: info\0"), uaPublic, asPublic), 32);
  const prk = await hmac(salt, ikm);
  const cek = await expand(prk, te.encode("Content-Encoding: aes128gcm\0"), 16);
  const nonce = await expand(prk, te.encode("Content-Encoding: nonce\0"), 12);
  const key = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, key, cat(payload, new Uint8Array([2]))));
  const rs = new Uint8Array([0, 0, 16, 0]);   // record size 4096
  return cat(salt, rs, new Uint8Array([asPublic.length]), asPublic, ct);
}

export type Sub = { endpoint: string; p256dh: string; auth: string };
// send one message; returns the push service's status (201 = accepted, 404 / 410 = the subscription is gone)
export async function send(sub: Sub, message: unknown, keys: { private_jwk: JsonWebKey; public_key: string }, subject: string,
                           opts: { ttl?: number; urgency?: string; topic?: string; fetchFn?: typeof fetch } = {}) {
  const body = await encrypt(te.encode(JSON.stringify(message)), sub.p256dh, sub.auth);
  const headers: Record<string, string> = {
    "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", TTL: String(opts.ttl ?? 86400),
    Urgency: opts.urgency || "high", Authorization: await vapidHeader(sub.endpoint, keys, subject),
  };
  if (opts.topic) headers.Topic = opts.topic.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 32);
  const r = await (opts.fetchFn || fetch)(sub.endpoint, { method: "POST", headers, body });
  return r.status;
}
