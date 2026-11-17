import { beforeEach, describe, expect, it } from "vitest";

import { getToken } from "./api";
import { authMessage, freshSession, loadSession, signIn, signOut, signUp, type AuthConfig } from "./auth";

// A tiny sessionStorage for the node test environment.
const mem = new Map<string, string>();
const storage = {
  getItem: (k: string) => mem.get(k) ?? null,
  setItem: (k: string, v: string) => void mem.set(k, v),
  removeItem: (k: string) => void mem.delete(k),
};
Object.assign(globalThis, { window: { sessionStorage: storage }, sessionStorage: storage });

const cfg: AuthConfig = { mode: "jwt", provider: "supabase", url: "https://abc.supabase.co", publishableKey: "sb_publishable_x" };

type Call = { url: string; init: RequestInit };
function fakeFetch(status: number, body: unknown, calls: Call[] = []) {
  return (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch;
}
const ok = { access_token: "at-1", refresh_token: "rt-1", expires_in: 3600, user: { email: "me@example.com" } };

beforeEach(() => mem.clear());

describe("sign in", () => {
  it("stores the session and the API token, sending the publishable key", async () => {
    const calls: Call[] = [];
    const s = await signIn(cfg, "me@example.com", "pw", fakeFetch(200, ok, calls));
    expect(s.email).toBe("me@example.com");
    expect(getToken()).toBe("at-1");
    expect(loadSession()?.refreshToken).toBe("rt-1");
    expect(calls[0].url).toBe("https://abc.supabase.co/auth/v1/token?grant_type=password");
    expect((calls[0].init.headers as Record<string, string>).apikey).toBe("sb_publishable_x");
  });

  it("explains failures in plain words", async () => {
    await expect(signIn(cfg, "a@b.c", "x", fakeFetch(400, { error_description: "Invalid login credentials" }))).rejects.toThrow(
      "Wrong email or password.",
    );
    expect(authMessage({ msg: "Email not confirmed" }, 400)).toContain("confirm your email");
    // Exactly what the real supabase.co answered to a wrong password (checked 2026-11-17):
    expect(authMessage({ code: 400, error_code: "invalid_credentials", msg: "Invalid login credentials" }, 400)).toBe(
      "Wrong email or password.",
    );
    expect(authMessage({}, 429)).toContain("wait a minute");
    expect(getToken()).toBe("");
  });

  it("network failure is a friendly error", async () => {
    const broken = (async () => { throw new TypeError("Failed to fetch"); }) as unknown as typeof fetch;
    await expect(signIn(cfg, "a@b.c", "x", broken)).rejects.toThrow("Could not reach the sign-in service");
  });
});

describe("sign up", () => {
  it("returns null when an email confirmation is needed", async () => {
    expect(await signUp(cfg, "new@example.com", "longpassword", fakeFetch(200, { id: "u1", email: "new@example.com" }))).toBeNull();
    expect(loadSession()).toBeNull();
  });
  it("signs in straight away when the project needs no confirmation", async () => {
    expect((await signUp(cfg, "me@example.com", "longpassword", fakeFetch(200, ok)))?.accessToken).toBe("at-1");
  });
});

describe("token refresh", () => {
  it("keeps a fresh token and renews one about to expire", async () => {
    await signIn(cfg, "me@example.com", "pw", fakeFetch(200, ok));
    const now = loadSession()!.expiresAt - 3600;
    expect((await freshSession(cfg, fakeFetch(500, {}), now))?.accessToken).toBe("at-1"); // no call needed
    const calls: Call[] = [];
    const renewed = { ...ok, access_token: "at-2", refresh_token: "rt-2" };
    const s = await freshSession(cfg, fakeFetch(200, renewed, calls), loadSession()!.expiresAt - 30);
    expect(s?.accessToken).toBe("at-2");
    expect(getToken()).toBe("at-2");
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ refresh_token: "rt-1" });
  });

  it("a revoked refresh token signs you out", async () => {
    await signIn(cfg, "me@example.com", "pw", fakeFetch(200, ok));
    expect(await freshSession(cfg, fakeFetch(400, { error: "invalid_grant" }), loadSession()!.expiresAt)).toBeNull();
    expect(loadSession()).toBeNull();
    expect(getToken()).toBe("");
  });
});

describe("sign out", () => {
  it("clears the session and tells Supabase, with the user's token", async () => {
    await signIn(cfg, "me@example.com", "pw", fakeFetch(200, ok));
    const calls: Call[] = [];
    await signOut(cfg, fakeFetch(204, {}, calls));
    expect(loadSession()).toBeNull();
    expect(getToken()).toBe("");
    expect(calls[0].url).toBe("https://abc.supabase.co/auth/v1/logout");
    expect((calls[0].init.headers as Record<string, string>).Authorization).toBe("Bearer at-1");
  });
});
