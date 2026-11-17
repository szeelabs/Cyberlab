import { api, type Advice } from "../api";
import { ErrorNotice, Pill, useLoad } from "../ui";

const KIND: Record<Advice["severity"], string> = { problem: "bad", warning: "warn", info: "stopped", ok: "ok" };
const LABEL: Record<Advice["severity"], string> = { problem: "problem", warning: "warning", info: "info", ok: "ok" };

type Props = { workspaceId: string; environmentId: string; name: string; onClose: () => void };

/** "What's wrong?": plain-language advice for one machine (rule-based, no AI). */
export function Troubleshoot({ workspaceId, environmentId, name, onClose }: Props) {
  const report = useLoad(() => api.diagnose(workspaceId, environmentId), [workspaceId, environmentId]);
  return (
    <section className="panel" aria-label={`What's wrong: ${name}`}>
      <div className="panel-head">
        <h2>What's wrong with <span className="mono">{name}</span>?</h2>
        <span className="spacer" />
        <button onClick={() => void report.reload()} disabled={report.loading}>Check again</button>
        <button onClick={onClose}>Close</button>
      </div>
      <ErrorNotice error={report.error} />
      {!report.data && !report.error && <div className="muted">Checking…</div>}
      {report.data && (
        <ul className="findings" aria-label="Advice">
          {report.data.advice.map((a) => (
            <li key={a.code} style={{ display: "grid", gap: 4, alignItems: "start" }}>
              <div className="row">
                <Pill kind={KIND[a.severity]}>{LABEL[a.severity]}</Pill>
                <strong>{a.title}</strong>
              </div>
              <span>{a.explanation}</span>
              {a.fix && <span className="muted"><strong>What to do:</strong> {a.fix}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
