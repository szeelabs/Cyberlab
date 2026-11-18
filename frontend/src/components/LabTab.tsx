import { useState } from "react";

import { api, type Environment, type LabRun, type LabTask } from "../api";
import { ErrorNotice, Pill, useAction } from "../ui";
import { Terminal } from "./Terminal";

/** Text with `inline code` shown as code. React escapes everything, so this can't inject HTML. */
export function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split(/(`[^`]+`)/g).map((part, i) =>
        part.startsWith("`") && part.endsWith("`") && part.length > 2 ? <code key={i}>{part.slice(1, -1)}</code> : <span key={i}>{part}</span>,
      )}
    </>
  );
}

function TaskCard({ wsId, task, index, solved, onRun }: { wsId: string; task: LabTask; index: number; solved: boolean; onRun: (r: LabRun) => void }) {
  const [value, setValue] = useState("");
  const [hints, setHints] = useState(0);
  const [result, setResult] = useState<"right" | "wrong" | null>(null);
  const check = useAction();

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    void check.run(async () => {
      const r = await api.answer(wsId, task.id, value);
      setResult(r.correct ? "right" : "wrong");
      onRun(r);
    });
  };

  return (
    <section className={`panel lab-task ${solved ? "solved" : ""}`} aria-label={`Task ${index + 1}: ${task.title}`}>
      <div className="row">
        <h3 style={{ margin: 0 }}>
          {index + 1}. {task.title}
        </h3>
        <span className="spacer" />
        {solved ? <Pill kind="ok">solved</Pill> : <Pill kind="neutral">to do</Pill>}
      </div>
      <div className="lab-text">
        {task.text.split(/\n{2,}/).map((p, i) => (
          <p key={i}>
            <Rich text={p} />
          </p>
        ))}
      </div>
      {hints > 0 && (
        <ul className="hints">
          {task.hints.slice(0, hints).map((h, i) => (
            <li key={i}>
              💡 <Rich text={h} />
            </li>
          ))}
        </ul>
      )}
      {!solved && (
        <form className="row" onSubmit={submit}>
          <input
            aria-label={`Answer for ${task.title}`}
            placeholder={task.answerFormat}
            value={value}
            onChange={(e) => { setValue(e.target.value); setResult(null); }}
            required
            maxLength={200}
            style={{ flex: 1, minWidth: 180 }}
          />
          <button className="primary" type="submit" disabled={check.busy}>Check</button>
          {hints < task.hints.length && (
            <button type="button" onClick={() => setHints(hints + 1)}>
              {hints === 0 ? "Show a hint" : "Another hint"}
            </button>
          )}
        </form>
      )}
      {result === "wrong" && <div className="notice error" role="alert">Not quite. Check for typos, or open a hint.</div>}
      {result === "right" && <div className="notice ok" role="status">Correct! Well done. 🎉</div>}
      <ErrorNotice error={check.error} />
    </section>
  );
}

/** The Lab tab of a workspace started from a ready-made lab. */
export function LabTab({ wsId, run, onRun, envs }: { wsId: string; run: LabRun; onRun: (r: LabRun) => void; envs: Environment[] }) {
  const [terminal, setTerminal] = useState<Environment | null>(null);
  const { lab, progress } = run;
  const done = progress.solved === progress.total;
  const live = envs.filter((e) => e.state !== "destroyed");

  return (
    <>
      <section className="panel" aria-label="Lab">
        <div className="row">
          <h2 style={{ margin: 0 }}>{lab.title}</h2>
          <span className="spacer" />
          <span className="small muted">
            {progress.solved} of {progress.total} solved
          </span>
        </div>
        <div className="bar" role="progressbar" aria-label="Lab progress" aria-valuenow={progress.solved} aria-valuemax={progress.total}>
          <div style={{ width: `${(progress.solved / progress.total) * 100}%` }} />
        </div>
        {done && (
          <div className="notice ok" role="status">
            🏆 You solved every task in this lab! When you're finished, destroy its machines in Environments to free
            your resources.
          </div>
        )}
        {run.setupError && (
          <div className="notice error" role="alert">
            The lab could not be fully prepared: {run.setupError}. Try "What's wrong?" in Environments, or start the lab
            again from Labs.
          </div>
        )}
        <div className="lab-text">
          {lab.introduction.split(/\n{2,}/).map((p, i) => (
            <p key={i}>
              <Rich text={p} />
            </p>
          ))}
        </div>
        <div className="row">
          {live.map((e) =>
            e.state === "running" ? (
              <button key={e.id} className="primary" onClick={() => setTerminal(e)}>
                Open terminal: {e.name}
              </button>
            ) : (
              <span key={e.id} className="small muted">
                {e.name} is {e.state}. Start it in Environments.
              </span>
            ),
          )}
        </div>
      </section>
      {terminal && <Terminal key={terminal.id} workspaceId={wsId} environmentId={terminal.id} name={terminal.name} onClose={() => setTerminal(null)} />}
      {lab.tasks.map((t, i) => (
        <TaskCard key={t.id} wsId={wsId} task={t} index={i} solved={t.id in run.solved} onRun={onRun} />
      ))}
    </>
  );
}
