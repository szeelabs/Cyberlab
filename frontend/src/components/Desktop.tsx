import RFB from "@novnc/novnc";
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { AuthedChannel } from "../desktopChannel";
import { Pill } from "../ui";

type Props = { workspaceId: string; environmentId: string; name: string; onClose: () => void };

/** Remote desktop: single-use ticket, then VNC over an authenticated WebSocket (noVNC). */
export function Desktop({ workspaceId, environmentId, name, onClose }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"connecting" | "connected" | "closed" | "error">("connecting");
  const [message, setMessage] = useState("");
  const [clip, setClip] = useState("");
  const [clipNote, setClipNote] = useState("");
  const rfbRef = useRef<RFB | null>(null);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let rfb: RFB | null = null;
    let disposed = false;

    (async () => {
      try {
        const t = await api.desktopTicket(workspaceId, environmentId);
        if (disposed) return;
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const ws = new WebSocket(`${proto}//${location.host}${t.websocketPath}`);
        socket = ws;
        ws.binaryType = "arraybuffer";
        ws.onopen = () => ws.send(JSON.stringify({ type: "auth", ticket: t.ticket }));
        ws.onclose = () => setStatus((s) => (s === "error" ? s : "closed"));
        ws.onmessage = (ev) => {
          if (typeof ev.data !== "string") return;
          const msg = JSON.parse(ev.data);
          if (msg.type === "error") {
            setStatus("error");
            setMessage(msg.message);
          } else if (msg.type === "ready" && !disposed) {
            // From here on the socket carries VNC only; noVNC takes it over.
            const channel = new AuthedChannel(ws);
            rfb = new RFB(box.current!, channel, { shared: true });
            rfbRef.current = rfb;
            rfb.scaleViewport = true;
            rfb.background = "#0e1311";
            rfb.addEventListener("connect", () => { setStatus("connected"); rfb?.focus(); });
            rfb.addEventListener("disconnect", () => setStatus((s) => (s === "error" ? s : "closed")));
            // Text copied inside the desktop arrives here.
            rfb.addEventListener("clipboard", (e) => {
              setClip((e as CustomEvent<{ text: string }>).detail.text);
              setClipNote("Copied on the desktop.");
            });
          }
        };
      } catch (e) {
        setStatus("error");
        setMessage(e instanceof Error ? e.message : String(e));
      }
    })();

    return () => {
      disposed = true;
      rfbRef.current = null;
      rfb?.disconnect();
      socket?.close();
    };
  }, [workspaceId, environmentId]);

  const kind = { connecting: "warn", connected: "ok", closed: "stopped", error: "bad" }[status];
  return (
    <section className="panel" aria-label={`Desktop: ${name}`}>
      <div className="panel-head">
        <h2 className="mono">{name}</h2>
        <Pill kind={kind}>{status}</Pill>
        {message && <span className="muted small">{message}</span>}
        <span className="spacer" />
        <button onClick={() => box.current?.requestFullscreen?.()} disabled={status !== "connected"}>Full screen</button>
        <button onClick={onClose}>Close desktop</button>
      </div>
      <p className="muted small">
        Right-click the desktop for a menu (new terminal, etc.). Programs you start from this machine's terminal
        (for example <span className="mono">xterm &amp;</span>) also appear here.
      </p>
      <div className="desktop-box" ref={box} data-testid="desktop" />
      <div className="grid" style={{ gridTemplateColumns: "1fr auto", alignItems: "start", marginTop: 10 }}>
        <label className="field">
          Clipboard (shared with the desktop)
          <textarea
            aria-label="Clipboard"
            rows={2}
            maxLength={100_000}
            value={clip}
            placeholder="Paste text here, then send it to the desktop"
            onChange={(e) => { setClip(e.target.value); setClipNote(""); }}
          />
        </label>
        <div style={{ display: "grid", gap: 6, paddingTop: 18 }}>
          <button
            disabled={status !== "connected" || !clip}
            onClick={() => { rfbRef.current?.clipboardPasteFrom(clip); setClipNote("Sent: in the desktop, paste with middle-click or Shift+Insert."); }}
          >
            Send to desktop
          </button>
          <button
            disabled={!clip}
            onClick={() =>
              navigator.clipboard
                ?.writeText(clip)
                .then(() => setClipNote("Copied to your computer."))
                .catch(() => setClipNote("Your browser blocked it: select the text above and copy it."))
            }
          >
            Copy to my computer
          </button>
        </div>
      </div>
      {clipNote && <div className="muted small" role="status">{clipNote}</div>}
    </section>
  );
}
