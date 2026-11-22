import { useState } from "react";

import { AuthError, updatePassword, type AuthConfig, type Session } from "../auth";

type Props = { cfg: AuthConfig; session: Session; onDone: () => void };

/** Shown after following the link in a password-reset email. */
export function ResetPassword({ cfg, session, onDone }: Props) {
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== again) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await updatePassword(cfg, password);
      onDone();
    } catch (err) {
      setError(err instanceof AuthError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="signin">
      <section className="panel" aria-label="Choose a new password">
        <div className="brand" style={{ fontSize: 22 }}>Cyber<span>Lab</span></div>
        <h1>Choose a new password</h1>
        <p className="muted small">For {session.email || "your account"}.</p>
        <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
          <label className="field">
            New password
            <input type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label className="field">
            New password again
            <input type="password" autoComplete="new-password" required minLength={8} value={again} onChange={(e) => setAgain(e.target.value)} />
          </label>
          <span className="muted small">At least 8 characters.</span>
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Please wait…" : "Save new password"}
          </button>
        </form>
        {error && <div className="notice error" role="alert">{error}</div>}
      </section>
    </div>
  );
}
