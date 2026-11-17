import { useState } from "react";

import { AuthError, signIn, signUp, type AuthConfig, type Session } from "../auth";

type Props = { cfg: AuthConfig; onSignedIn: (s: Session) => void };

/** Email + password sign-in and account creation (Supabase Auth). */
export function SignIn({ cfg, onSignedIn }: Props) {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "signin") {
        onSignedIn(await signIn(cfg, email.trim(), password));
      } else {
        const s = await signUp(cfg, email.trim(), password);
        if (s) onSignedIn(s);
        else {
          setNotice(`Account created. We sent a confirmation link to ${email.trim()}: open it, then sign in here.`);
          setMode("signin");
        }
      }
    } catch (err) {
      setError(err instanceof AuthError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="signin">
      <section className="panel" aria-label={mode === "signin" ? "Sign in" : "Create account"}>
        <div className="brand" style={{ fontSize: 22 }}>Cyber<span>Lab</span></div>
        <h1>{mode === "signin" ? "Sign in" : "Create your account"}</h1>
        <p className="muted small">
          Your own Linux machines for ethical hacking practice, in the browser. Nothing is installed on your computer.
        </p>
        <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
          <label className="field">
            Email
            <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label className="field">
            Password
            <input
              type="password"
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              required
              minLength={mode === "signup" ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {mode === "signup" && <span className="muted small">At least 8 characters.</span>}
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}
          </button>
        </form>
        {error && <div className="notice error" role="alert">{error}</div>}
        {notice && <div className="notice ok" role="status">{notice}</div>}
        <div className="row small">
          {mode === "signin" ? (
            <>New here? <button className="link" onClick={() => { setMode("signup"); setError(null); }}>Create an account</button></>
          ) : (
            <>Already have an account? <button className="link" onClick={() => { setMode("signin"); setError(null); }}>Sign in</button></>
          )}
        </div>
        <p className="muted small">Practise only on your own lab machines, CTFs, or systems you have written permission to test.</p>
      </section>
    </div>
  );
}
