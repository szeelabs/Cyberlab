import { useState } from "react";

import { api, type ArchitectDraft } from "../api";
import { ErrorNotice, Pill, useAction, useLoad } from "../ui";

const EXAMPLES = [
  "An attacker and a target to practise web hacking on Parrot OS",
  "Packet analysis on Kali with no internet",
  "A throwaway forensics box with no network",
];

type Props = { onUse: (yaml: string) => void; onCancel: () => void };

/** "Describe your lab": plain words in, a draft manifest out. Nothing runs from here. */
export function Architect({ onUse, onCancel }: Props) {
  const status = useLoad(() => api.ai());
  const [prompt, setPrompt] = useState("");
  const [draft, setDraft] = useState<ArchitectDraft | null>(null);
  const act = useAction();
  const off = status.data?.status === "OFF";

  const run = () => act.run(async () => { setDraft(null); setDraft(await api.draftLab(prompt)); });

  return (
    <section className="panel" aria-label="Describe your lab">
      <div className="panel-head">
        <h2>Describe your lab</h2>
        {status.data && (
          <Pill kind={off ? "warn" : "ok"}>{off ? "off" : status.data.provider === "rules" ? "built-in planner" : "AI"}</Pill>
        )}
        <span className="spacer" />
        <button onClick={onCancel}>Back to the editor</button>
      </div>
      <p className="muted small">
        Say what you want to practise. CyberLab drafts a lab for you; you check it, save it and
        approve it before anything starts. {status.data?.detail}
      </p>
      <textarea
        aria-label="What do you want to practise?"
        rows={3}
        style={{ width: "100%" }}
        maxLength={2000}
        value={prompt}
        placeholder={EXAMPLES[0]}
        onChange={(e) => setPrompt(e.target.value)}
      />
      <div className="row small">
        <span className="muted">Try:</span>
        {EXAMPLES.map((ex) => (
          <button key={ex} className="link" onClick={() => setPrompt(ex)}>{ex}</button>
        ))}
      </div>
      <div className="row">
        <span className="spacer" />
        <button className="primary" onClick={run} disabled={act.busy || off || prompt.trim().length < 3}>
          {act.busy ? "Drafting…" : "Draft my lab"}
        </button>
      </div>
      <ErrorNotice error={act.error ?? status.error} />

      {draft?.refused && (
        <div className="notice error" role="alert" aria-label="Not drafted">
          <strong>Not drafted.</strong> {draft.reason}
        </div>
      )}
      {draft && !draft.refused && (
        <div className="panel" style={{ background: "var(--panel-2)" }} role="group" aria-label="Draft">
          <div className="panel-head">
            <h3>Draft</h3>
            <Pill kind={draft.plan.valid ? (draft.plan.requires_approval ? "approval" : "ok") : "bad"}>
              {draft.plan.valid ? (draft.plan.requires_approval ? "needs your approval" : "passes checks") : "has problems"}
            </Pill>
            <span className="muted small">
              {draft.plan.total_cpu} CPU · {draft.plan.total_memory_mib} MiB RAM · {draft.plan.total_storage_gib} GiB disk
            </span>
          </div>
          <p>{draft.explanation}</p>
          {draft.plan.findings.length > 0 && (
            <ul className="findings">
              {draft.plan.findings.map((f, i) => (
                <li key={i}><Pill kind={f.severity}>{f.severity}</Pill><span>{f.message}</span></li>
              ))}
            </ul>
          )}
          <pre className="mono small" aria-label="Draft manifest" style={{ overflow: "auto", maxHeight: 260 }}>{draft.manifestYaml}</pre>
          <div className="row">
            <span className="muted small">Using it only fills in the editor. You still save and approve it.</span>
            <span className="spacer" />
            <button className="primary" onClick={() => onUse(draft.manifestYaml)}>Use this draft</button>
          </div>
        </div>
      )}
    </section>
  );
}
