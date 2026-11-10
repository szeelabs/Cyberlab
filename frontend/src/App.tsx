import { Dashboard } from "./pages/Dashboard";
import { Settings } from "./pages/Settings";
import { Tools } from "./pages/Tools";
import { WorkspacePage } from "./pages/Workspace";
import { useRoute } from "./ui";

export function App() {
  const route = useRoute();
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
          {link("#/tools", "Tool universe", route.page === "tools")}
          {link("#/settings", "Settings", route.page === "settings")}
        </nav>
      </header>
      <main className="main">
        {route.page === "dashboard" && <Dashboard />}
        {route.page === "workspace" && <WorkspacePage key={route.id} id={route.id} tab={route.tab} />}
        {route.page === "tools" && <Tools />}
        {route.page === "settings" && <Settings />}
      </main>
    </div>
  );
}
