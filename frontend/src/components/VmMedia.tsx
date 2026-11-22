import { useEffect, useState } from "react";

import { api, formatBytes, type Environment, type VmMedia as Media } from "../api";
import { ErrorNotice, useAction } from "../ui";

/** Give a full virtual machine its operating system: the server downloads an ISO from a link
 * straight into the machine (nothing passes through this computer). */
export function VmMedia({ workspaceId, env, onClose }: { workspaceId: string; env: Environment; onClose: () => void }) {
  const [url, setUrl] = useState("");
  const [sha, setSha] = useState("");
  const [media, setMedia] = useState<Media | null>(null);
  const act = useAction();

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const m = await api.vmMedia(workspaceId, env.id);
        if (!stop) setMedia(m);
        if (!stop && (m.state === "downloading" || m.state === "copying")) setTimeout(() => void tick(), 1500);
      } catch {
        /* shown by the form's own errors */
      }
    };
    void tick();
    return () => {
      stop = true;
    };
  }, [workspaceId, env.id, act.busy]);

  const busy = media?.state === "downloading" || media?.state === "copying";
  const pct = media?.total ? Math.round(((media.bytes ?? 0) / media.total) * 100) : null;
  return (
    <section className="panel" aria-label={`Boot media: ${env.name}`}>
      <div className="panel-head">
        <h2>Operating system for {env.name}</h2>
        <span className="spacer" />
        <button onClick={onClose}>Close</button>
      </div>
      <p className="muted small">
        Paste the download link of an installer or live ISO (for example Android-x86, Predator-OS or any Linux). CyberLab's
        server downloads it straight into this machine, then the machine boots it: open its <b>Desktop</b> to see the
        screen. Use links from the project's official website, and its SHA-256 checksum if it publishes one.
      </p>
      <form
        style={{ display: "grid", gap: 8 }}
        onSubmit={(e) => {
          e.preventDefault();
          void act.run(async () => setMedia(await api.addVmMedia(workspaceId, env.id, url.trim(), sha.trim() || null)));
        }}
      >
        <label className="field">
          Download link (https://…)
          <input aria-label="ISO link" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/something.iso" />
        </label>
        <label className="field">
          SHA-256 checksum (optional, recommended)
          <input aria-label="SHA-256" value={sha} onChange={(e) => setSha(e.target.value)} pattern="[0-9a-fA-F]{64}" placeholder="64 hexadecimal characters" />
        </label>
        <div className="row">
          <button className="primary" type="submit" disabled={act.busy || busy}>
            Download into the machine
          </button>
          <span className="muted small">Or upload a file named {env.vm?.boot ?? "boot.iso"} in the Files tab.</span>
        </div>
      </form>
      <ErrorNotice error={act.error} />
      {media && media.state !== "none" && (
        <div className={`notice ${media.state === "failed" ? "error" : media.state === "ready" ? "ok" : ""}`} role="status">
          {media.state === "downloading" && <>Downloading… {formatBytes(media.bytes ?? 0)}{pct !== null ? ` (${pct}%)` : ""}</>}
          {media.state === "copying" && <>Copying into the machine…</>}
          {media.state === "ready" && <>Ready. The machine is starting it: open <b>Desktop</b> in a few seconds.</>}
          {media.state === "failed" && <>Download failed: {media.error}</>}
        </div>
      )}
    </section>
  );
}
