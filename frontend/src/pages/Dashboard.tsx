import { useState } from "react";

import { api } from "../api";
import { ErrorNotice, Meter, Pill, timeAgo, useAction, useLoad } from "../ui";

export function Dashboard() {
  const status = useLoad(() => Promise.all([api.execution(), api.storage()]));
  const usage = useLoad(() => api.usage());
  const list = useLoad(() => api.workspaces());
  const [name, setName] = useState("");
  const create = useAction();

  const onCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const ok = await create.run(async () => {
      const ws = await api.createWorkspace(name.trim());
      window.location.hash = `#/workspace/${ws.id}`;
    });
    if (ok) setName("");
  };

  const [execution, storage] = status.data ?? [null, null];
  return (
    <>
      <h1>Your universe</h1>
      <div className="grid">
        <section className="panel" aria-label="System status">
          <h2>System</h2>
          <ErrorNotice error={status.error} />
          <div className="row">
            <span>Environments</span>
            {execution && <Pill kind={execution.status === "CONNECTED" ? "ok" : "bad"}>{execution.status}</Pill>}
          </div>
          {execution && <div className="muted small">{execution.detail}</div>}
          <div className="row">
            <span>Snapshot storage</span>
            {storage && <Pill kind={storage.status === "CONNECTED" ? "ok" : "bad"}>{storage.status}</Pill>}
            {storage?.encrypted === true && <Pill kind="ok">encrypted</Pill>}
          </div>
          {storage && <div className="muted small">{String(storage.store)}</div>}
        </section>

        <section className="panel" aria-label="Usage">
          <h2>Usage</h2>
          <ErrorNotice error={usage.error} />
          {usage.data && (
            <>
              <Meter label="Environments" used={usage.data.usage.environments} limit={usage.data.limits.environments} />
              <Meter label="CPU (running)" used={usage.data.usage.cpu} limit={usage.data.limits.cpu} />
              <Meter label="Memory (running)" used={usage.data.usage.memoryMiB} limit={usage.data.limits.memoryMiB} unit=" MiB" />
              <Meter label="Storage" used={usage.data.usage.storageGiB} limit={usage.data.limits.storageGiB} unit=" GiB" />
            </>
          )}
        </section>
      </div>

      <section className="panel" aria-label="Workspaces">
        <div className="panel-head">
          <h2>Workspaces</h2>
          <span className="spacer" />
          <form className="row" onSubmit={onCreate}>
            <input
              aria-label="New workspace name"
              placeholder="new-workspace-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              pattern="[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?"
              title="lowercase letters, digits and dashes"
              required
            />
            <button className="primary" type="submit" disabled={create.busy}>
              Create workspace
            </button>
          </form>
        </div>
        <ErrorNotice error={create.error ?? list.error} />
        {list.data && list.data.length === 0 && (
          <div className="empty">No workspaces yet. Create one, then paste a Universe Manifest into it.</div>
        )}
        {list.data && list.data.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Manifest</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((w) => (
                  <tr key={w.id}>
                    <td>
                      <a href={`#/workspace/${w.id}`}>{w.name}</a>
                      {w.description && <div className="muted small">{w.description}</div>}
                    </td>
                    <td>{w.manifest_revision ? `revision ${w.manifest_revision}` : <span className="muted">none yet</span>}</td>
                    <td className="muted">{timeAgo(w.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
