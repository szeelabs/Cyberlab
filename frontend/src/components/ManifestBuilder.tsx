import { useState } from "react";

import { api, type ToolPack } from "../api";
import {
  formProblems,
  NETWORKS,
  newEnvironment,
  parseHosts,
  SIZES,
  toYaml,
  type EnvDraft,
  type LabDraft,
  type NetworkMode,
} from "../manifestBuilder";
import { ErrorNotice, Pill, useLoad } from "../ui";

const RISK = { low: "ok", medium: "warn", high: "bad" } as const;

type Props = { onUse: (yaml: string) => void; onCancel: () => void };

/** A beginner-friendly form that writes a Universe Manifest. */
export function ManifestBuilder({ onUse, onCancel }: Props) {
  const registry = useLoad(() => Promise.all([api.baseImages(), api.toolPacks()]));
  const [lab, setLab] = useState<LabDraft>({ name: "my-lab", description: "", environments: [newEnvironment(0)] });
  const [hostText, setHostText] = useState<Record<number, string>>({});
  const problems = formProblems(lab);

  const update = (i: number, patch: Partial<EnvDraft>) =>
    setLab((l) => ({ ...l, environments: l.environments.map((e, j) => (j === i ? { ...e, ...patch } : e)) }));

  if (registry.error) return <ErrorNotice error={registry.error} />;
  if (!registry.data) return <div className="muted">Loading the tool catalogue…</div>;
  const [bases, packs] = registry.data;

  return (
    <section className="panel" aria-label="Build with a form">
      <div className="panel-head">
        <h2>Build with a form</h2>
        <span className="muted small">no YAML needed: CyberLab writes the manifest for you</span>
        <span className="spacer" />
        <button onClick={onCancel}>Back to the editor</button>
      </div>

      <div className="grid">
        <label className="field">
          Lab name
          <input aria-label="Lab name" value={lab.name} onChange={(e) => setLab({ ...lab, name: e.target.value })} />
        </label>
        <label className="field">
          What is it for? (optional)
          <input aria-label="What is it for?" value={lab.description} onChange={(e) => setLab({ ...lab, description: e.target.value })} placeholder="e.g. practise packet analysis" />
        </label>
      </div>

      {lab.environments.map((env, i) => {
        const compatible = packs.filter((p: ToolPack) => p.compatibleBases.includes(env.baseImage) && p.status !== "metadata-only");
        return (
          <div key={i} className="panel" style={{ background: "var(--panel-2)" }} aria-label={`Machine ${i + 1}`} role="group">
            <div className="panel-head">
              <h3>Machine {i + 1}</h3>
              <span className="spacer" />
              {lab.environments.length > 1 && (
                <button className="danger" onClick={() => setLab({ ...lab, environments: lab.environments.filter((_, j) => j !== i) })}>
                  Remove
                </button>
              )}
            </div>
            <div className="grid">
              <label className="field">
                Name
                <input aria-label="Name" value={env.name} onChange={(e) => update(i, { name: e.target.value })} />
              </label>
              <label className="field">
                Operating system
                <select
                  aria-label="Operating system"
                  value={env.baseImage}
                  onChange={(e) => {
                    const base = e.target.value;
                    // keep only the tool packs that work on the new system
                    const ok = new Set(packs.filter((p) => p.compatibleBases.includes(base)).map((p) => p.id));
                    const desktopOk = bases.find((b) => b.id === base)?.supportsDesktop ?? false;
                    update(i, { baseImage: base, toolPacks: env.toolPacks.filter((t) => ok.has(t)), desktop: env.desktop && desktopOk });
                  }}
                >
                  {bases.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                </select>
              </label>
              <label className="field">
                Size
                <select aria-label="Size" value={env.size} onChange={(e) => update(i, { size: e.target.value as EnvDraft["size"] })}>
                  {Object.entries(SIZES).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
                </select>
              </label>
              <label className="field">
                Network
                <select aria-label="Network" value={env.network} onChange={(e) => update(i, { network: e.target.value as NetworkMode })}>
                  {Object.entries(NETWORKS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                </select>
              </label>
            </div>
            {env.network === "restricted-egress" && (
              <label className="field">
                Websites it may reach (exact names, e.g. ghcr.io)
                <input
                  aria-label="Websites it may reach"
                  value={hostText[i] ?? env.allowedEgress.join(", ")}
                  onChange={(e) => { setHostText({ ...hostText, [i]: e.target.value }); update(i, { allowedEgress: parseHosts(e.target.value) }); }}
                />
              </label>
            )}
            <fieldset style={{ border: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
              <legend className="muted small" style={{ marginBottom: 6 }}>Tool packs that work on this system</legend>
              {compatible.length === 0 && <span className="muted small">None available for this system yet.</span>}
              {compatible.map((p) => (
                <label key={p.id} className="row">
                  <input
                    type="checkbox"
                    checked={env.toolPacks.includes(p.id)}
                    onChange={(e) =>
                      update(i, { toolPacks: e.target.checked ? [...env.toolPacks, p.id] : env.toolPacks.filter((t) => t !== p.id) })
                    }
                  />
                  <span className="mono">{p.id}</span>
                  <Pill kind={RISK[p.risk]}>{p.risk}</Pill>
                  <span className="muted small">{p.description}</span>
                </label>
              ))}
            </fieldset>
            <div className="row small">
              <label className="row">
                Stop when unused for
                <input
                  type="number" min={5} max={1440} style={{ width: 80 }}
                  aria-label="Idle minutes"
                  value={env.idleTimeoutMinutes ?? ""}
                  onChange={(e) => update(i, { idleTimeoutMinutes: e.target.value ? Number(e.target.value) : null })}
                />
                minutes
              </label>
              <label className="row">
                Delete after
                <input
                  type="number" min={1} style={{ width: 80 }}
                  aria-label="Time limit hours"
                  value={env.ttlHours ?? ""}
                  placeholder="never"
                  onChange={(e) => update(i, { ttlHours: e.target.value ? Number(e.target.value) : null })}
                />
                hours
              </label>
              <label className="row">
                <input type="checkbox" checked={env.ephemeral} onChange={(e) => update(i, { ephemeral: e.target.checked })} />
                Throwaway (delete its files too)
              </label>
              {bases.find((b) => b.id === env.baseImage)?.supportsDesktop ? (
                <label className="row">
                  <input
                    type="checkbox"
                    checked={env.desktop}
                    onChange={(e) => update(i, { desktop: e.target.checked, size: e.target.checked && env.size === "small" ? "medium" : env.size })}
                  />
                  Remote desktop (a graphical screen in your browser)
                </label>
              ) : (
                <span className="muted">No remote desktop on this system yet.</span>
              )}
            </div>
          </div>
        );
      })}

      <div className="row">
        <button onClick={() => setLab({ ...lab, environments: [...lab.environments, newEnvironment(lab.environments.length)] })}>
          Add another machine
        </button>
        <span className="spacer" />
        <button className="primary" disabled={problems.length > 0} onClick={() => onUse(toYaml(lab))}>
          Use this manifest
        </button>
      </div>
      {problems.length > 0 && (
        <ul className="findings" aria-label="Fix these first">
          {problems.map((p) => <li key={p}><Pill kind="warn">fix</Pill><span>{p}</span></li>)}
        </ul>
      )}
    </section>
  );
}
