import { useState } from "react";

import { api, download, type Lab } from "../api";
import { ErrorNotice, Pill, useAction, useLoad } from "../ui";

const TOPICS: Record<string, string> = {
  linux: "Linux",
  networking: "Networking",
  web: "Web",
  forensics: "Forensics",
  "blue-team": "Defence",
  crypto: "Cryptography",
};
const LEVELS = ["beginner", "intermediate", "advanced"] as const;

/** The catalog of ready-made labs: pick one, press Start, practise. */
export function Labs() {
  const labs = useLoad(() => api.labs());
  const progress = useLoad(() => api.labProgress().catch(() => null));
  const mineList = useLoad(() => api.myLabs().catch(() => [] as Lab[]));
  const [importText, setImportText] = useState("");
  const [showImport, setShowImport] = useState(false);
  const own = useAction();
  const reloadAll = async () => { await Promise.all([labs.reload(), mineList.reload()]); };
  const start = useAction();
  const [starting, setStarting] = useState<string | null>(null);
  const [level, setLevel] = useState<string>("all");
  const [topic, setTopic] = useState<string>("all");

  const onStart = (lab: Lab) => {
    setStarting(lab.id);
    void start
      .run(async () => {
        const run = await api.startLab(lab.id);
        window.location.hash = `#/workspace/${run.workspaceId}/lab`;
      })
      .finally(() => setStarting(null));
  };

  const ownLabs = mineList.data ?? [];
  const all = [...ownLabs.filter((m) => !(labs.data ?? []).some((l) => l.id === m.id)), ...(labs.data ?? [])];
  const shown = all.filter((l) => (level === "all" || l.level === level) && (topic === "all" || l.topic === topic));
  const topics = [...new Set(all.map((l) => l.topic))];
  const mine = progress.data?.labs ?? {};

  return (
    <>
      <div className="panel-head">
        <h1>Ready-made labs</h1>
        <span className="spacer" />
        {progress.data && (
          <span className="muted small" aria-label="Your progress">
            🏆 {progress.data.completed} of {progress.data.available} labs completed
          </span>
        )}
      </div>
      <p className="muted">
        Pick a lab and press <b>Start</b>. CyberLab creates the machines, puts the lab files in place and shows you the
        tasks. Every run has fresh answers. From first steps to multi-step challenges.
      </p>
      <div className="row" role="group" aria-label="Filter labs">
        <select aria-label="Level" value={level} onChange={(e) => setLevel(e.target.value)}>
          <option value="all">All levels</option>
          {LEVELS.map((l) => (
            <option key={l} value={l}>{l[0].toUpperCase() + l.slice(1)}</option>
          ))}
        </select>
        <select aria-label="Topic" value={topic} onChange={(e) => setTopic(e.target.value)}>
          <option value="all">All topics</option>
          {topics.map((t) => (
            <option key={t} value={t}>{TOPICS[t] ?? t}</option>
          ))}
        </select>
        <span className="muted small">{shown.length} labs</span>
      </div>
      <ErrorNotice error={labs.error ?? start.error} />
      <section className="panel" aria-label="Your labs">
        <div className="panel-head">
          <h2>Write your own labs</h2>
          <span className="spacer" />
          <button onClick={() => setShowImport(!showImport)}>{showImport ? "Cancel" : "Import a lab file"}</button>
        </div>
        <p className="muted small">
          Any lab here can be downloaded as a file, changed and imported again. Your labs are checked like the built-in ones
          (no internet, no code), stay private until you share them, and can be used for classes once shared. How to write one:
          docs/guides/labs.md.
        </p>
        {showImport && (
          <form
            style={{ display: "grid", gap: 8 }}
            onSubmit={(e) => {
              e.preventDefault();
              void own.run(async () => {
                await api.importLab(importText);
                setImportText("");
                setShowImport(false);
                await reloadAll();
              });
            }}
          >
            <input
              type="file"
              accept=".yaml,.yml"
              aria-label="Lab file"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void f.text().then(setImportText); }}
            />
            <textarea aria-label="Lab YAML" rows={8} value={importText} onChange={(e) => setImportText(e.target.value)} placeholder="id: my-lab&#10;title: ..." className="mono" />
            <div className="row"><button className="primary" type="submit" disabled={own.busy || !importText.trim()}>Check and import</button></div>
          </form>
        )}
        <ErrorNotice error={own.error} />
        {ownLabs.map((l) => (
          <div key={l.id} className="row class-row">
            <div style={{ flex: 1 }}><b>{l.title}</b> <span className="muted small">{l.shared ? "shared with everyone" : "private"}</span></div>
            <button disabled={own.busy} onClick={() => void own.run(async () => { await api.shareLab(l.id, !l.shared); await reloadAll(); })}>
              {l.shared ? "Stop sharing" : "Share"}
            </button>
            <button className="danger" disabled={own.busy} onClick={() => void own.run(async () => { await api.deleteLab(l.id); await reloadAll(); })}>Delete</button>
          </div>
        ))}
      </section>
      {starting && (
        <div className="notice" role="status">
          Starting your lab… The first time can take a minute or two while the machines are prepared.
        </div>
      )}
      {labs.data && shown.length === 0 && <div className="empty">No labs match these filters.</div>}
      <div className="grid" aria-label="Labs">
        {shown.map((lab) => {
          const p = mine[lab.id];
          return (
            <section key={lab.id} className={`panel lab-card ${p?.completed ? "solved" : ""}`} aria-label={lab.title}>
              <div className="row">
                <Pill kind={lab.level === "beginner" ? "ok" : lab.level === "intermediate" ? "warn" : "bad"}>{lab.level}</Pill>
                <span className="muted small">{TOPICS[lab.topic] ?? lab.topic}</span>
                {lab.source === "community" && <Pill kind="neutral">community</Pill>}
                {lab.source === "mine" && <Pill kind="neutral">yours</Pill>}
                <span className="spacer" />
                {p?.completed ? <Pill kind="ok">✓ completed</Pill> : <span className="muted small">about {lab.minutes} min</span>}
              </div>
              <h2>{lab.title}</h2>
              <p>{lab.summary}</p>
              <div className="small muted">
                You'll practise: {lab.skills.join(", ")}
                <br />
                {lab.tasks.length} tasks · {lab.machines.length === 1 ? "1 machine" : `${lab.machines.length} machines`}
              </div>
              <div className="row">
                {p && !p.completed && (
                  <a href={`#/workspace/${p.workspaceId}/lab`} className="small">
                    Continue ({p.solved}/{p.total} solved)
                  </a>
                )}
                {lab.source !== "mine" && (
                  <button className="link small" onClick={() => void download(api.labFileUrl(lab.id), `${lab.id}.yaml`)}>Lab file</button>
                )}
                <span className="spacer" />
                <button className={p ? "" : "primary"} disabled={start.busy} onClick={() => onStart(lab)}>
                  {starting === lab.id ? "Starting…" : p ? "Start again" : "Start lab"}
                </button>
              </div>
            </section>
          );
        })}
      </div>
    </>
  );
}
