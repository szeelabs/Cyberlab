import { useEffect, useState } from "react";

import { api, ApiError, download, formatBytes, type Environment, type Plan } from "../api";
import { Terminal } from "../components/Terminal";
import { ErrorNotice, Pill, timeAgo, useAction, useLoad } from "../ui";

const STARTER = `apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata:
  name: my-lab
  description: A Debian shell with crypto tools on a private network.
spec:
  environments:
    - name: shell
      baseImage: debian-12
      resources: {cpu: 0.5, memoryMiB: 512, storageGiB: 2}
      toolPacks: [crypto]
      network: {mode: lab}
      lifecycle: {idleTimeoutMinutes: 60}
`;

const TABS = [
  ["setup", "Setup"],
  ["environments", "Environments"],
  ["files", "Files"],
  ["snapshots", "Snapshots"],
] as const;

/** A button that needs a second click to confirm, instead of a blocking browser dialog. */
function ConfirmButton({ label, confirm, onConfirm, disabled }: { label: string; confirm: string; onConfirm: () => void; disabled?: boolean }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);
  return armed ? (
    <button className="danger" disabled={disabled} onClick={() => { setArmed(false); onConfirm(); }}>
      {confirm}
    </button>
  ) : (
    <button className="danger" disabled={disabled} onClick={() => setArmed(true)}>
      {label}
    </button>
  );
}

export function WorkspacePage({ id, tab }: { id: string; tab: string }) {
  const ws = useLoad(() => api.workspace(id), [id]);
  const envs = useLoad(() => api.environments(id), [id]);
  const action = useAction();

  if (ws.error) return <ErrorNotice error={ws.error} />;
  if (!ws.data) return <div className="muted">Loading…</div>;
  return (
    <>
      <div className="panel-head">
        <div>
          <div className="muted small">
            <a href="#/">Workspaces</a> /
          </div>
          <h1>{ws.data.name}</h1>
        </div>
        <span className="spacer" />
        <button disabled={action.busy || !ws.data.manifest_revision} onClick={() => action.run(() => download(api.exportUrl(id), `${ws.data!.name}.cyberlab`))}>
          Export bundle
        </button>
        <ConfirmButton
          label="Delete workspace"
          confirm="Click again to delete"
          disabled={action.busy}
          onConfirm={() => action.run(async () => { await api.deleteWorkspace(id); window.location.hash = "#/"; })}
        />
      </div>
      <ErrorNotice error={action.error} />
      <nav className="tabs" aria-label="Workspace sections">
        {TABS.map(([key, label]) => (
          <button key={key} className={tab === key ? "active" : ""} onClick={() => (window.location.hash = `#/workspace/${id}/${key}`)}>
            {label}
          </button>
        ))}
      </nav>
      {tab === "setup" && <SetupTab id={id} hasManifest={ws.data.manifest_revision !== null} onSaved={() => { void ws.reload(); }} />}
      {tab === "environments" && <EnvironmentsTab id={id} envs={envs.data ?? []} error={envs.error} reload={envs.reload} />}
      {tab === "files" && <FilesTab id={id} envs={envs.data ?? []} />}
      {tab === "snapshots" && <SnapshotsTab id={id} envs={envs.data ?? []} reloadEnvs={envs.reload} />}
    </>
  );
}

// ---------------------------------------------------------------- setup
function Findings({ plan }: { plan: Plan }) {
  return (
    <section className="panel" aria-label="Check result">
      <div className="row">
        <Pill kind={plan.valid ? (plan.requires_approval ? "approval" : "ok") : "bad"}>
          {plan.valid ? (plan.requires_approval ? "valid, needs approval" : "valid") : "has problems"}
        </Pill>
        {plan.valid && (
          <span className="muted small">
            needs {plan.total_cpu} CPU, {plan.total_memory_mib} MiB memory, {plan.total_storage_gib} GiB storage
          </span>
        )}
      </div>
      {plan.findings.length > 0 && (
        <ul className="findings">
          {plan.findings.map((f, i) => (
            <li key={i}>
              <Pill kind={f.severity}>{f.severity}</Pill>
              <span>{f.message}</span>
              {f.path && <code>{f.path}</code>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SetupTab({ id, hasManifest, onSaved }: { id: string; hasManifest: boolean; onSaved: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  // Only ask for a manifest or plan the workspace actually has (no pointless 404s).
  const [saved_, setSaved_] = useState(hasManifest);
  const approval = useLoad(() => (saved_ ? api.plan(id) : Promise.resolve(null)), [id, saved_]);
  const act = useAction();

  useEffect(() => {
    if (!hasManifest) setText(STARTER);
    else api.manifest(id).then(setText).catch(() => setText(STARTER));
  }, [id, hasManifest]);

  const check = () => act.run(async () => { setSaved(null); setPlan(await api.validate(text ?? "")); });
  const save = () =>
    act.run(async () => {
      try {
        const r = await api.saveManifest(id, text ?? "");
        setPlan(r);
        setSaved(`Saved as revision ${r.revision}.`);
        onSaved();
        if (saved_) void approval.reload();
        else setSaved_(true);
      } catch (e) {
        if (e instanceof ApiError && e.status === 422 && e.body && typeof e.body === "object") {
          setPlan((e.body as { detail: Plan }).detail);
          throw new Error("Not saved: fix the problems below first.");
        }
        throw e;
      }
    });
  const approve = () =>
    act.run(async () => {
      const p = approval.data!;
      await api.approve(id, p.revision, p.approvalCodes);
      await approval.reload();
    });

  if (text === null) return <div className="muted">Loading…</div>;
  const needsApproval = approval.data && approval.data.approvalCodes.length > 0 && !approval.data.approved;
  return (
    <>
      <section className="panel" aria-label="Universe Manifest">
        <div className="panel-head">
          <h2>Universe Manifest</h2>
          <span className="muted small">describes the environments, tools, network and limits of this workspace</span>
          <span className="spacer" />
          <button onClick={check} disabled={act.busy}>Check</button>
          <button className="primary" onClick={save} disabled={act.busy}>Save</button>
        </div>
        <textarea className="editor" aria-label="Manifest YAML" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
        <ErrorNotice error={act.error} />
        {saved && <div className="notice ok">{saved}</div>}
      </section>
      {plan && <Findings plan={plan} />}
      {needsApproval && (
        <section className="panel" aria-label="Approval needed">
          <h2>Approval needed</h2>
          <p className="muted">Revision {approval.data!.revision} asks for things that need your explicit OK before anything is created:</p>
          <ul className="findings">
            {approval.data!.findings.filter((f) => f.severity === "approval").map((f, i) => (
              <li key={i}><Pill kind="approval">{f.code}</Pill><span>{f.message}</span></li>
            ))}
          </ul>
          <div><button className="primary" onClick={approve} disabled={act.busy}>I approve these</button></div>
        </section>
      )}
    </>
  );
}

// ---------------------------------------------------------------- environments
function EnvironmentsTab({ id, envs, error, reload }: { id: string; envs: Environment[]; error: string | null; reload: () => Promise<void> }) {
  const act = useAction();
  const [terminal, setTerminal] = useState<Environment | null>(null);
  const [keepData, setKeepData] = useState(true);
  const live = envs.filter((e) => e.state !== "destroyed");
  const run = (fn: () => Promise<unknown>) => act.run(async () => { await fn(); await reload(); });

  return (
    <>
      <section className="panel" aria-label="Environments">
        <div className="panel-head">
          <h2>Environments</h2>
          <span className="spacer" />
          <label className="row small muted">
            <input type="checkbox" checked={keepData} onChange={(e) => setKeepData(e.target.checked)} /> keep files when destroying
          </label>
          <button onClick={() => void reload()} disabled={act.busy}>Refresh</button>
          <button className="primary" disabled={act.busy || live.length > 0} onClick={() => run(() => api.provision(id))}>
            {act.busy ? "Working…" : "Create environments"}
          </button>
        </div>
        <ErrorNotice error={act.error ?? error} />
        {live.length === 0 ? (
          <div className="empty">No environments. Save a manifest in Setup, then create them here. The first one with new tool packs can take a few minutes to build.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Name</th><th>State</th><th>Tools</th><th>Last used</th><th>Expires</th><th /></tr>
              </thead>
              <tbody>
                {live.map((e) => (
                  <tr key={e.id}>
                    <td className="mono">{e.name}</td>
                    <td>
                      <Pill kind={e.state}>{e.state}</Pill>
                      {e.lastError && <div className="small" style={{ color: "var(--bad)" }}>{e.lastError}</div>}
                    </td>
                    <td className="small">
                      {e.toolPacks.length ? e.toolPacks.join(", ") : <span className="muted">base only</span>}
                      {e.toolPacks.length > 0 && !e.toolPacksInstalled && <div className="muted">not installed</div>}
                    </td>
                    <td className="muted small">{timeAgo(e.lastActivityAt)}</td>
                    <td className="muted small">{e.expiresAt ? new Date(`${e.expiresAt}${e.expiresAt.includes("+") ? "" : "Z"}`).toLocaleString() : "never"}</td>
                    <td>
                      <div className="row">
                        {e.state === "running" && <button onClick={() => setTerminal(e)}>Terminal</button>}
                        {e.state === "running" && <button disabled={act.busy} onClick={() => run(() => api.stop(id, e.id))}>Stop</button>}
                        {e.state === "stopped" && <button disabled={act.busy} onClick={() => run(() => api.start(id, e.id))}>Start</button>}
                        <ConfirmButton label="Destroy" confirm="Confirm destroy" disabled={act.busy}
                          onConfirm={() => run(async () => { if (terminal?.id === e.id) setTerminal(null); await api.destroy(id, e.id, keepData); })} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {terminal && <Terminal key={terminal.id} workspaceId={id} environmentId={terminal.id} name={terminal.name} onClose={() => setTerminal(null)} />}
    </>
  );
}

// ---------------------------------------------------------------- files
function FilesTab({ id, envs }: { id: string; envs: Environment[] }) {
  const running = envs.filter((e) => e.state === "running");
  const [envId, setEnvId] = useState("");
  const [path, setPath] = useState("");
  const current = running.find((e) => e.id === envId) ?? running[0];
  const listing = useLoad(() => (current ? api.files(id, current.id, path) : Promise.resolve(null)), [id, current?.id, path]);
  const act = useAction();

  if (!current) return <div className="empty">Start an environment to browse its files.</div>;
  const parts = path ? path.split("/") : [];
  const join = (name: string) => (path ? `${path}/${name}` : name);
  const upload = (file: File) => act.run(async () => { await api.upload(id, current.id, join(file.name), file); await listing.reload(); });

  return (
    <section className="panel" aria-label="Files">
      <div className="panel-head">
        <h2>Files</h2>
        <select aria-label="Environment" value={current.id} onChange={(e) => { setEnvId(e.target.value); setPath(""); }}>
          {running.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select>
        <span className="spacer" />
        <label className="button">
          Upload file
          <input type="file" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
        </label>
      </div>
      <nav className="crumbs" aria-label="Folder">
        <a href="#" onClick={(e) => { e.preventDefault(); setPath(""); }}>/workspace</a>
        {parts.map((p, i) => (
          <span key={i}>/<a href="#" onClick={(e) => { e.preventDefault(); setPath(parts.slice(0, i + 1).join("/")); }}>{p}</a></span>
        ))}
      </nav>
      <ErrorNotice error={act.error ?? listing.error} />
      {listing.data && listing.data.entries.length === 0 && <div className="empty">This folder is empty.</div>}
      {listing.data && listing.data.entries.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Size</th><th>Modified</th><th /></tr></thead>
            <tbody>
              {listing.data.entries.map((f) => (
                <tr key={f.name}>
                  <td className="mono">
                    {f.type === "directory" ? (
                      <a href="#" onClick={(e) => { e.preventDefault(); setPath(join(f.name)); }}>{f.name}/</a>
                    ) : f.name}
                  </td>
                  <td className="muted">{f.type === "directory" ? "" : formatBytes(f.size)}</td>
                  <td className="muted small">{new Date(f.modified * 1000).toLocaleString()}</td>
                  <td>
                    <div className="row">
                      {f.type === "file" && (
                        <button disabled={act.busy} onClick={() => act.run(() => download(api.fileUrl(id, current.id, join(f.name)), f.name))}>Download</button>
                      )}
                      <ConfirmButton label="Delete" confirm="Confirm delete" disabled={act.busy}
                        onConfirm={() => act.run(async () => { await api.deleteFile(id, current.id, join(f.name), f.type === "directory"); await listing.reload(); })} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- snapshots
function SnapshotsTab({ id, envs, reloadEnvs }: { id: string; envs: Environment[]; reloadEnvs: () => Promise<void> }) {
  const list = useLoad(() => api.snapshots(id), [id]);
  const usable = envs.filter((e) => e.state === "running" || e.state === "stopped");
  const [envId, setEnvId] = useState("");
  const [label, setLabel] = useState("");
  const [target, setTarget] = useState("");
  const act = useAction();
  const [done, setDone] = useState<string | null>(null);
  const source = usable.find((e) => e.id === envId) ?? usable[0];
  const restoreTo = usable.find((e) => e.id === target) ?? usable[0];

  return (
    <section className="panel" aria-label="Snapshots">
      <div className="panel-head">
        <h2>Snapshots</h2>
        <span className="spacer" />
        {source && (
          <form className="row" onSubmit={(e) => { e.preventDefault(); void act.run(async () => { setDone(null); await api.snapshot(id, source.id, label); setLabel(""); await list.reload(); }); }}>
            <select aria-label="Environment to snapshot" value={source.id} onChange={(e) => setEnvId(e.target.value)}>
              {usable.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </select>
            <input aria-label="Snapshot label" placeholder="label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={200} />
            <button className="primary" type="submit" disabled={act.busy}>Save snapshot</button>
          </form>
        )}
      </div>
      <ErrorNotice error={act.error ?? list.error} />
      {done && <div className="notice ok">{done}</div>}
      {usable.length > 0 && (
        <div className="row small muted">
          Restore into
          <select aria-label="Restore target" value={restoreTo?.id} onChange={(e) => setTarget(e.target.value)}>
            {usable.map((e) => <option key={e.id} value={e.id}>{e.name} ({e.state})</option>)}
          </select>
          {restoreTo?.state === "running" && <span>stop it first: restoring replaces all its files</span>}
        </div>
      )}
      {list.data && list.data.length === 0 && <div className="empty">No snapshots yet.</div>}
      {list.data && list.data.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Label</th><th>From</th><th>Size</th><th>Saved</th><th /></tr></thead>
            <tbody>
              {list.data.map((s) => (
                <tr key={s.id}>
                  <td>{s.label || <span className="muted">(no label)</span>}</td>
                  <td className="mono">{s.environment}</td>
                  <td className="muted">{formatBytes(s.sizeBytes)}</td>
                  <td className="muted small">{timeAgo(s.createdAt)}</td>
                  <td>
                    <div className="row">
                      <ConfirmButton label="Restore" confirm="Confirm restore" disabled={act.busy || !restoreTo || restoreTo.state !== "stopped"}
                        onConfirm={() => act.run(async () => { await api.restore(id, restoreTo!.id, s.id); setDone(`Restored into ${restoreTo!.name}. Start it to use the files.`); await reloadEnvs(); })} />
                      <ConfirmButton label="Delete" confirm="Confirm delete" disabled={act.busy}
                        onConfirm={() => act.run(async () => { await api.deleteSnapshot(id, s.id); await list.reload(); })} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
