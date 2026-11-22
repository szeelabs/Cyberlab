// Real sign-in with Supabase Auth, over its documented REST API (no extra library).
// The access token is a short-lived JWT that CyberLab's server checks against the
// project's public keys; the refresh token renews it. Both stay in this browser tab only
// (sessionStorage), like the manually pasted token before.

import { setToken } from "./api";

export type AuthConfig = {
  mode: "jwt" | "dev";
  provider: "supabase" | null;
  url: string | null;
  publishableKey: string | null;
};

export type Session = { accessToken: string; refreshToken: string; expiresAt: number; email: string };

const KEY = "cyberlab.session";
const REFRESH_MARGIN_S = 60;

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const store = (): Store | null => {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

export function loadSession(s: Store | null = store()): Session | null {
  try {
    const raw = s?.getItem(KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Session;
    return typeof v.accessToken === "string" && typeof v.refreshToken === "string" ? v : null;
  } catch {
    return null;
  }
}

export function saveSession(session: Session | null, s: Store | null = store()): void {
  try {
    if (session) s?.setItem(KEY, JSON.stringify(session));
    else s?.removeItem(KEY);
  } catch {
    /* storage unavailable: the session lasts until the page is reloaded */
  }
  setToken(session?.accessToken ?? "");
}

export class AuthError extends Error {}

/** Supabase errors come in a few shapes; turn them into one readable sentence. */
export function authMessage(body: unknown, status: number): string {
  const o = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  const raw = [o.error_description, o.msg, o.message, o.error].find((x) => typeof x === "string") as string | undefined;
  if (raw === "Invalid login credentials") return "Wrong email or password.";
  if (raw === "Email not confirmed") return "Please confirm your email first: open the link we sent you, then sign in.";
  if (status === 429) return "Too many attempts. Please wait a minute and try again.";
  if (raw && /same as the old|should be different/i.test(raw)) return "Choose a password you haven't used before.";
  if (raw && /expired|invalid/i.test(raw) && /link|otp|token/i.test(raw))
    return "This link has expired or was already used. Ask for a new one.";
  return raw ?? `Sign-in failed (${status}).`;
}

type Fetch = typeof fetch;

async function call(
  cfg: AuthConfig,
  path: string,
  body: unknown,
  f: Fetch,
  bearer?: string,
  method: "POST" | "PUT" | "GET" = "POST",
): Promise<Record<string, unknown>> {
  if (!cfg.url || !cfg.publishableKey) throw new AuthError("Sign-in is not configured on this server.");
  const headers: Record<string, string> = { apikey: cfg.publishableKey, "Content-Type": "application/json" };
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  let res: Response;
  try {
    res = await f(`${cfg.url}/auth/v1/${path}`, { method, headers, body: method === "GET" ? undefined : JSON.stringify(body) });
  } catch {
    throw new AuthError("Could not reach the sign-in service. Check your connection and try again.");
  }
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) throw new AuthError(authMessage(data, res.status));
  return data;
}

function toSession(data: Record<string, unknown>, now: number, fallbackEmail = ""): Session {
  const user = (data.user ?? {}) as Record<string, unknown>;
  if (typeof data.access_token !== "string" || typeof data.refresh_token !== "string")
    throw new AuthError("The sign-in service sent an unexpected answer.");
  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresAt: now + (typeof data.expires_in === "number" ? data.expires_in : 3600),
    email: typeof user.email === "string" ? user.email : fallbackEmail,
  };
}

const nowS = () => Math.floor(Date.now() / 1000);

export async function signIn(cfg: AuthConfig, email: string, password: string, f: Fetch = fetch): Promise<Session> {
  const data = await call(cfg, "token?grant_type=password", { email, password }, f);
  const session = toSession(data, nowS(), email);
  saveSession(session);
  return session;
}

/** Create an account. Returns a session if no email confirmation is needed, else null. */
export async function signUp(cfg: AuthConfig, email: string, password: string, f: Fetch = fetch): Promise<Session | null> {
  const data = await call(cfg, "signup", { email, password }, f);
  if (typeof data.access_token !== "string") return null; // confirmation email sent
  const session = toSession(data, nowS(), email);
  saveSession(session);
  return session;
}

/** Renew the access token shortly before it expires. Returns the (possibly new) session. */
export async function freshSession(cfg: AuthConfig, f: Fetch = fetch, now = nowS()): Promise<Session | null> {
  const current = loadSession();
  if (!current) return null;
  if (current.expiresAt - now > REFRESH_MARGIN_S) return current;
  try {
    const data = await call(cfg, "token?grant_type=refresh_token", { refresh_token: current.refreshToken }, f);
    const session = toSession(data, now, current.email);
    saveSession(session);
    return session;
  } catch {
    saveSession(null); // the refresh token was revoked or expired: sign in again
    return null;
  }
}

export async function signOut(cfg: AuthConfig, f: Fetch = fetch): Promise<void> {
  const current = loadSession();
  saveSession(null);
  if (current) await call(cfg, "logout", {}, f, current.accessToken).catch(() => undefined);
}

/** Send a password-reset email. Supabase answers the same whether or not the account
 * exists, so this never reveals who has an account. */
export async function requestPasswordReset(cfg: AuthConfig, email: string, returnTo: string, f: Fetch = fetch): Promise<void> {
  await call(cfg, `recover?redirect_to=${encodeURIComponent(returnTo)}`, { email }, f);
}

export type AuthRedirect =
  | { kind: "session"; type: string; session: Session }
  | { kind: "error"; message: string };

/** Links in Supabase emails come back as `#access_token=...&type=recovery...` (or an error).
 * Returns null when the address is an ordinary page of the app. */
export async function readAuthRedirect(cfg: AuthConfig, hash: string, f: Fetch = fetch, now = nowS()): Promise<AuthRedirect | null> {
  const raw = hash.replace(/^#/, "");
  if (!/(^|&)(access_token|error)=/.test(raw)) return null;
  const p = new URLSearchParams(raw);
  if (p.get("error")) {
    const desc = p.get("error_description") ?? p.get("error") ?? "";
    return { kind: "error", message: authMessage({ msg: desc.replace(/\+/g, " ") }, 400) };
  }
  const access = p.get("access_token");
  const refresh = p.get("refresh_token");
  if (!access || !refresh) return { kind: "error", message: "This link is incomplete. Ask for a new one." };
  let email = "";
  try {
    const user = await call(cfg, "user", undefined, f, access, "GET");
    email = typeof user.email === "string" ? user.email : "";
  } catch {
    return { kind: "error", message: "This link has expired or was already used. Ask for a new one." };
  }
  const expiresIn = Number(p.get("expires_in") ?? "3600");
  const session: Session = { accessToken: access, refreshToken: refresh, expiresAt: now + (expiresIn > 0 ? expiresIn : 3600), email };
  saveSession(session);
  return { kind: "session", type: p.get("type") ?? "", session };
}

/** Set a new password for the signed-in user (after following a reset link). */
export async function updatePassword(cfg: AuthConfig, password: string, f: Fetch = fetch): Promise<void> {
  const current = loadSession();
  if (!current) throw new AuthError("Your reset link has expired. Ask for a new one.");
  await call(cfg, "user", { password }, f, current.accessToken, "PUT");
}
