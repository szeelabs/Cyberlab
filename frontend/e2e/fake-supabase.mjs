// A small stand-in for Supabase Auth, for the browser tests only. It speaks the same REST
// endpoints the web app uses and signs real ES256 tokens with a fresh P-256 key, published as
// a JWKS, so CyberLab's server verifies them exactly as it would tokens from supabase.co.
import { createServer } from "node:http";
import { generateKeyPairSync, randomBytes, randomUUID, sign } from "node:crypto";

const port = Number(process.argv[2] ?? 8796);
const base = `http://127.0.0.1:${port}`;
const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
const kid = randomUUID();
const users = new Map(); // email -> { id, password, confirmed }
const refresh = new Map(); // refresh token -> email

const b64 = (b) => Buffer.from(b).toString("base64url");
function jwtFor(user, email) {
  const now = Math.floor(Date.now() / 1000);
  const head = b64(JSON.stringify({ alg: "ES256", typ: "JWT", kid }));
  const body = b64(JSON.stringify({ sub: user.id, email, aud: "authenticated", role: "authenticated", iss: `${base}/auth/v1`, iat: now, exp: now + 3600 }));
  const sig = sign("sha256", Buffer.from(`${head}.${body}`), { key: privateKey, dsaEncoding: "ieee-p1363" });
  return `${head}.${body}.${b64(sig)}`;
}
function session(email) {
  const rt = randomBytes(16).toString("hex");
  refresh.set(rt, email);
  return { access_token: jwtFor(users.get(email), email), refresh_token: rt, expires_in: 3600, token_type: "bearer", user: { id: users.get(email).id, email } };
}

createServer(async (req, res) => {
  const cors = {
    "Access-Control-Allow-Origin": req.headers.origin ?? "*",
    "Access-Control-Allow-Headers": "apikey, authorization, content-type, x-client-info",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  };
  const send = (status, body) => {
    res.writeHead(status, { ...cors, "Content-Type": "application/json" });
    res.end(body === undefined ? "" : JSON.stringify(body));
  };
  if (req.method === "OPTIONS") return send(204);
  const url = new URL(req.url, base);
  let data = {};
  if (req.method === "POST") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    data = raw ? JSON.parse(raw) : {};
  }
  if (url.pathname === "/auth/v1/.well-known/jwks.json")
    return send(200, { keys: [{ ...publicKey.export({ format: "jwk" }), kid, alg: "ES256", use: "sig" }] });
  if (url.pathname === "/__confirm") {
    // test helper: what clicking the link in the confirmation email does
    const u = users.get(data.email);
    if (!u) return send(404, { message: "no such user" });
    u.confirmed = true;
    return send(200, {});
  }
  if (req.method === "POST" && !req.headers.apikey) return send(401, { message: "No API key found in request" });
  if (url.pathname === "/auth/v1/signup") {
    if (users.has(data.email)) return send(422, { msg: "User already registered" });
    users.set(data.email, { id: randomUUID(), password: data.password, confirmed: false });
    return send(200, { id: users.get(data.email).id, email: data.email });
  }
  if (url.pathname === "/auth/v1/token" && url.searchParams.get("grant_type") === "password") {
    const u = users.get(data.email);
    if (!u || u.password !== data.password) return send(400, { error: "invalid_grant", error_description: "Invalid login credentials" });
    if (!u.confirmed) return send(400, { error: "invalid_grant", error_description: "Email not confirmed" });
    return send(200, session(data.email));
  }
  if (url.pathname === "/auth/v1/token" && url.searchParams.get("grant_type") === "refresh_token") {
    const email = refresh.get(data.refresh_token);
    if (!email) return send(400, { error: "invalid_grant", error_description: "Invalid Refresh Token" });
    refresh.delete(data.refresh_token);
    return send(200, session(email));
  }
  if (url.pathname === "/auth/v1/logout") return send(204);
  if (url.pathname === "/health") return send(200, { ok: true });
  send(404, { message: "not found" });
}).listen(port, "127.0.0.1");
