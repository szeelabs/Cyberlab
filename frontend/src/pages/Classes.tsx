import { useEffect, useState } from "react";

import { api, type ClassProgress } from "../api";
import { ErrorNotice, Pill, timeAgo, useAction, useLoad } from "../ui";

function Progress({ p }: { p: ClassProgress }) {
  if (!p.started) return <span className="muted small">not started</span>;
  const pct = p.total ? (p.solved / p.total) * 100 : 0;
  return (
    <div style={{ display: "grid", gap: 4, minWidth: 140 }}>
      <div className="row small">
        {p.completed ? <Pill kind="ok">✓ completed</Pill> : <span>{p.solved} / {p.total} solved</span>}
      </div>
      <div className="bar progress-bar" role="progressbar" aria-valuenow={p.solved} aria-valuemax={p.total}>
        <div style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** Classes: join one with a code, or teach one. */
export function Classes() {
  const data = useLoad(() => api.classes());
  const labs = useLoad(() => api.labs());
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [labId, setLabId] = useState("");
  const act = useAction();

  const run = (fn: () => Promise<unknown>) => act.run(async () => { await fn(); await data.reload(); });
  const start = (id: string) =>
    act.run(async () => {
      const r = await api.startClassLab(id);
      window.location.hash = `#/workspace/${r.workspaceId}/lab`;
    });

  return (
    <>
      <h1>Classes</h1>
      <p className="muted">
        Teachers run one lab for a whole group: create a class, share its code, and follow everyone's progress live.
        Every student gets their own machines and their own answers.
      </p>
      <ErrorNotice error={data.error ?? act.error} />
      <div className="grid">
        <section className="panel" aria-label="Join a class">
          <h2>Join a class</h2>
          <form className="row" onSubmit={(e) => { e.preventDefault(); void run(() => api.joinClass(code.trim())); setCode(""); }}>
            <input aria-label="Class code" placeholder="ABCD-EFGH" value={code} onChange={(e) => setCode(e.target.value)} required minLength={8} maxLength={20} />
            <button className="primary" type="submit" disabled={act.busy}>Join</button>
          </form>
          {data.data?.joined.length === 0 && <div className="empty">Ask your teacher for the class code.</div>}
          {data.data?.joined.map((c) => (
            <div key={c.id} className="row class-row">
              <div style={{ flex: 1 }}>
                <b>{c.name}</b>
                <div className="muted small">{c.labTitle}</div>
              </div>
              <Progress p={c.progress} />
              <button className="primary" disabled={act.busy} onClick={() => void start(c.id)}>
                {c.workspaceId ? "Continue" : "Start my lab"}
              </button>
            </div>
          ))}
        </section>

        <section className="panel" aria-label="Teach a class">
          <h2>Teach a class</h2>
          <form
            style={{ display: "grid", gap: 8 }}
            onSubmit={(e) => {
              e.preventDefault();
              void run(() => api.createClass(name.trim(), labId));
              setName("");
            }}
          >
            <label className="field">
              Class name
              <input aria-label="Class name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} placeholder="Security 101, group A" />
            </label>
            <label className="field">
              Lab
              <select aria-label="Lab for the class" value={labId} onChange={(e) => setLabId(e.target.value)} required>
                <option value="">Choose a lab…</option>
                {labs.data?.map((l) => (
                  <option key={l.id} value={l.id}>{l.title} ({l.level})</option>
                ))}
              </select>
            </label>
            <div className="row">
              <button className="primary" type="submit" disabled={act.busy}>Create class</button>
            </div>
          </form>
          {data.data?.teaching.map((c) => (
            <div key={c.id} className="row class-row">
              <div style={{ flex: 1 }}>
                <a href={`#/classes/${c.id}`}><b>{c.name}</b></a>
                <div className="muted small">{c.labTitle} · {c.members} students</div>
              </div>
              <code className="join-code" aria-label={`Code for ${c.name}`}>{c.joinCode}</code>
              {!c.open && <Pill kind="warn">closed</Pill>}
            </div>
          ))}
        </section>
      </div>
    </>
  );
}

/** The teacher's live view of one class. */
export function ClassRoster({ id }: { id: string }) {
  const data = useLoad(() => api.classRoster(id), [id]);
  const act = useAction();
  const [armed, setArmed] = useState<string | null>(null);

  useEffect(() => {
    const t = setInterval(() => void data.reload(), 10_000); // live progress
    return () => clearInterval(t);
  }, [data.reload]);

  if (data.error) return <ErrorNotice error={data.error} />;
  if (!data.data) return <div className="muted">Loading…</div>;
  const c = data.data;
  const run = (fn: () => Promise<unknown>) => act.run(async () => { await fn(); await data.reload(); });
  return (
    <>
      <div className="panel-head">
        <div>
          <div className="muted small"><a href="#/classes">Classes</a> /</div>
          <h1>{c.name}</h1>
          <div className="muted">{c.labTitle}</div>
        </div>
        <span className="spacer" />
        <div style={{ textAlign: "right" }}>
          <div className="muted small">Students join with</div>
          <code className="join-code big" aria-label="Join code">{c.joinCode}</code>
        </div>
      </div>
      <ErrorNotice error={act.error} />
      <section className="panel" aria-label="Students">
        <div className="panel-head">
          <h2>Students</h2>
          <span className="muted small">{c.roster.length} joined · {c.completed} completed · updates every 10 s</span>
          <span className="spacer" />
          <button disabled={act.busy} onClick={() => void run(() => api.setClassOpen(id, !c.open))}>
            {c.open ? "Close to new students" : "Open to new students"}
          </button>
        </div>
        {c.roster.length === 0 ? (
          <div className="empty">No students yet. Share the code {c.joinCode}: they enter it on the Classes page.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Student</th><th>Progress</th><th>Machines</th><th>Last answer</th><th /></tr>
              </thead>
              <tbody>
                {c.roster.map((r) => (
                  <tr key={r.memberId}>
                    <td>{r.student}<div className="muted small">joined {timeAgo(r.joinedAt)}</div></td>
                    <td><Progress p={r.progress} /></td>
                    <td className="small">
                      {r.progress.machines?.length ? r.progress.machines.map((m) => <div key={m.name}><Pill kind={m.state}>{m.name}</Pill></div>) : <span className="muted">-</span>}
                    </td>
                    <td className="muted small">{r.progress.lastSolvedAt ? timeAgo(r.progress.lastSolvedAt) : "-"}</td>
                    <td>
                      {r.progress.started &&
                        (armed === r.memberId ? (
                          <button className="danger" disabled={act.busy} onClick={() => { setArmed(null); void run(() => api.resetStudent(id, r.memberId)); }}>
                            Confirm reset
                          </button>
                        ) : (
                          <button className="danger" onClick={() => setArmed(r.memberId)}>Reset lab</button>
                        ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <p className="muted small">
        You see progress only: students' files, terminals and answers stay private. Reset removes a student's lab machines
        and files so they can start again with fresh answers.
      </p>
    </>
  );
}
