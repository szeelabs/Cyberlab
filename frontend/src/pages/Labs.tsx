import { useState } from "react";

import { api, type Lab } from "../api";
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

  const all = labs.data ?? [];
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
