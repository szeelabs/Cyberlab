import { useState } from "react";

import { api, getToken, setToken } from "../api";
import { ErrorNotice, Pill, useAction } from "../ui";

export function Settings() {
  const [token, setValue] = useState(getToken());
  const [who, setWho] = useState<string | null>(null);
  const act = useAction();
  const save = () =>
    act.run(async () => {
      setToken(token.trim());
      const me = await api.me();
      setWho(me.subject);
    });
  return (
    <>
      <h1>Settings</h1>
      <section className="panel" aria-label="Sign-in token">
        <h2>Sign-in token</h2>
        <p className="muted">
          Needed only when this server uses real sign-in (for example Supabase). In local development mode, leave it empty.
          The token is kept for this browser tab only.
        </p>
        <label className="field">
          API token
          <input type="password" value={token} onChange={(e) => setValue(e.target.value)} autoComplete="off" />
        </label>
        <div className="row">
          <button className="primary" onClick={save} disabled={act.busy}>Save and test</button>
          {who && <Pill kind="ok">signed in as {who}</Pill>}
        </div>
        <ErrorNotice error={act.error} />
      </section>
    </>
  );
}
