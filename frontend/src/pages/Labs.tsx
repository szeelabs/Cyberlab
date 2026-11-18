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

/** The catalog of ready-made labs: pick one, press Start, practise. */
export function Labs() {
  const labs = useLoad(() => api.labs());
  const start = useAction();
  const [starting, setStarting] = useState<string | null>(null);

  const onStart = (lab: Lab) => {
    setStarting(lab.id);
    void start
      .run(async () => {
        const run = await api.startLab(lab.id);
        window.location.hash = `#/workspace/${run.workspaceId}/lab`;
      })
      .finally(() => setStarting(null));
  };

  return (
    <>
      <h1>Ready-made labs</h1>
      <p className="muted">
        Pick a lab and press <b>Start</b>. CyberLab creates the machines for you, puts the lab files in place and shows
        you the tasks. Every run has fresh answers. Nothing is installed on your computer.
      </p>
      <ErrorNotice error={labs.error ?? start.error} />
      {starting && (
        <div className="notice" role="status">
          Starting your lab… The first time can take a minute or two while the machine is prepared.
        </div>
      )}
      {labs.data && labs.data.length === 0 && <div className="empty">No labs are installed on this server.</div>}
      <div className="grid" aria-label="Labs">
        {labs.data?.map((lab) => (
          <section key={lab.id} className="panel lab-card" aria-label={lab.title}>
            <div className="row">
              <Pill kind={lab.level === "beginner" ? "ok" : lab.level === "intermediate" ? "warn" : "bad"}>{lab.level}</Pill>
              <span className="muted small">{TOPICS[lab.topic] ?? lab.topic}</span>
              <span className="spacer" />
              <span className="muted small">about {lab.minutes} min</span>
            </div>
            <h2>{lab.title}</h2>
            <p>{lab.summary}</p>
            <div className="small muted">
              You'll practise: {lab.skills.join(", ")}
              <br />
              {lab.tasks.length} tasks · {lab.machines.length === 1 ? "1 machine" : `${lab.machines.length} machines`}
            </div>
            <div className="row">
              <span className="spacer" />
              <button className="primary" disabled={start.busy} onClick={() => onStart(lab)}>
                {starting === lab.id ? "Starting…" : "Start lab"}
              </button>
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
