import "@xterm/xterm/css/xterm.css";

import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { Pill } from "../ui";

type Props = { workspaceId: string; environmentId: string; name: string; onClose: () => void };

/** A browser terminal: single-use ticket, then a WebSocket to the environment's shell. */
export function Terminal({ workspaceId, environmentId, name, onClose }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"connecting" | "connected" | "closed" | "error">("connecting");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const term = new XTerm({ cursorBlink: true, fontSize: 14, fontFamily: "ui-monospace, Menlo, Consolas, monospace" });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(box.current!);
    fit.fit();
    let socket: WebSocket | null = null;
    let disposed = false;

    const sendResize = () => {
      fit.fit();
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
    };

    (async () => {
      try {
        const t = await api.terminalTicket(workspaceId, environmentId);
        if (disposed) return;
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(`${proto}//${location.host}${t.websocketPath}`);
        socket.binaryType = "arraybuffer";
        socket.onopen = () => socket!.send(JSON.stringify({ type: "auth", ticket: t.ticket }));
        socket.onmessage = (ev) => {
          if (typeof ev.data !== "string") {
            term.write(new Uint8Array(ev.data as ArrayBuffer));
            return;
          }
          const msg = JSON.parse(ev.data);
          if (msg.type === "ready") {
            setStatus("connected");
            sendResize();
            term.focus();
          } else if (msg.type === "error") {
            setStatus("error");
            setMessage(msg.message);
          } else if (msg.type === "exit") {
            setMessage(`Session ended (${msg.reason}).`);
          }
        };
        socket.onclose = () => setStatus((s) => (s === "error" ? s : "closed"));
      } catch (e) {
        setStatus("error");
        setMessage(e instanceof Error ? e.message : String(e));
      }
    })();

    const input = term.onData((data) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input", data }));
    });
    window.addEventListener("resize", sendResize);
    return () => {
      disposed = true;
      input.dispose();
      window.removeEventListener("resize", sendResize);
      socket?.close();
      term.dispose();
    };
  }, [workspaceId, environmentId]);

  const kind = { connecting: "warn", connected: "ok", closed: "stopped", error: "bad" }[status];
  return (
    <section className="panel" aria-label={`Terminal: ${name}`}>
      <div className="panel-head">
        <h2 className="mono">{name}</h2>
        <Pill kind={kind}>{status}</Pill>
        {message && <span className="muted small">{message}</span>}
        <span className="spacer" />
        <button onClick={onClose}>Close terminal</button>
      </div>
      <div className="terminal-box" ref={box} data-testid="terminal" />
    </section>
  );
}
