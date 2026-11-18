import { useEffect, useState } from "react";

import { api, setAuthHooks } from "./api";
import { freshSession, loadSession, saveSession, signOut, type AuthConfig, type Session } from "./auth";
import { Dashboard } from "./pages/Dashboard";
import { Labs } from "./pages/Labs";
import { Settings } from "./pages/Settings";
import { SignIn } from "./pages/SignIn";
import { Tools } from "./pages/Tools";
import { WorkspacePage } from "./pages/Workspace";
import { ErrorNotice, useRoute } from "./ui";

export function App() {
  const route = useRoute();
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [cfgError, setCfgError] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(loadSession());

  useEffect(() => {
    api.authConfig().then(setCfg).catch((e) => setCfgError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (cfg?.provider !== "supabase") {
      setAuthHooks(null, null);
      return;
    }
    setAuthHooks(
      async () => {
        const s = await freshSession(cfg);
        if (!s) setSession(null);
      },
      () => {
        saveSession(null);
        setSession(null);
      },
    );
  }, [cfg]);

  if (cfgError) return <main className="main"><ErrorNotice error={`CyberLab's server can't be reached: ${cfgError}`} /></main>;
  if (!cfg) return <main className="main muted">Loading…</main>;
  if (cfg.provider === "supabase" && !session) return <SignIn cfg={cfg} onSignedIn={setSession} />;

  const link = (hash: string, label: string, active: boolean) => (
    <a href={hash} className={active ? "active" : ""} aria-current={active ? "page" : undefined}>
      {label}
    </a>
  );
  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/">Cyber<span>Lab</span></a>
        <nav className="nav" aria-label="Main">
          {link("#/", "Workspaces", route.page === "dashboard" || route.page === "workspace")}
          {link("#/labs", "Labs", route.page === "labs")}
          {link("#/tools", "Tool universe", route.page === "tools")}
          {link("#/settings", "Settings", route.page === "settings")}
        </nav>
        {cfg.provider === "supabase" && session && (
          <div className="row small" style={{ marginLeft: "auto" }}>
            <span className="muted" aria-label="Signed in as">{session.email}</span>
            <button onClick={() => void signOut(cfg).then(() => setSession(null))}>Sign out</button>
          </div>
        )}
      </header>
      <main className="main">
        {route.page === "dashboard" && <Dashboard />}
        {route.page === "workspace" && <WorkspacePage key={route.id} id={route.id} tab={route.tab} />}
        {route.page === "labs" && <Labs />}
        {route.page === "tools" && <Tools />}
        {route.page === "settings" && <Settings />}
      </main>
    </div>
  );
}
