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
            rfb.scaleViewport = true;
            rfb.background = "#0e1311";
            rfb.addEventListener("connect", () => { setStatus("connected"); rfb?.focus(); });
            rfb.addEventListener("disconnect", () => setStatus((s) => (s === "error" ? s : "closed")));
          }
        };
      } catch (e) {
        setStatus("error");
        setMessage(e instanceof Error ? e.message : String(e));
      }
    })();

    return () => {
      disposed = true;
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
    </section>
  );
}
