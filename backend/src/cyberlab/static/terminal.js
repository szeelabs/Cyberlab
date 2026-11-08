// CyberLab browser terminal (test page). The API token stays in memory only.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const term = new Terminal({ cursorBlink: true, convertEol: false, fontSize: 14,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($("terminal"));
  fit.fit();

  let socket = null;

  function setStatus(text, state) {
    $("status").textContent = text;
    $("status").dataset.state = state;
  }
  function say(text) { $("message").textContent = text; }

  async function api(path, options) {
    const headers = { "Content-Type": "application/json" };
    const token = $("token").value.trim();
    if (token) headers.Authorization = "Bearer " + token;
    const res = await fetch(path, Object.assign({ headers }, options));
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = body.detail;
      throw new Error(typeof d === "string" ? d : (d && (d.detail || d.status)) || res.statusText);
    }
    return body;
  }

  $("load").addEventListener("click", async () => {
    const ws = $("workspace").value.trim();
    if (!ws) { say("Enter a workspace ID first."); return; }
    try {
      const envs = await api(`/v1/workspaces/${encodeURIComponent(ws)}/environments?refresh=true`);
      const select = $("environment");
      select.innerHTML = "";
      const running = envs.filter((e) => e.state === "running");
      for (const e of running) {
        const o = document.createElement("option");
        o.value = e.id;
        o.textContent = `${e.name} (${e.state})`;
        select.appendChild(o);
      }
      if (!running.length) {
        select.innerHTML = '<option value="">No running environments</option>';
        say("This workspace has no running environments. Provision or start one first.");
      } else {
        say(`${running.length} running environment(s) found.`);
      }
    } catch (err) { say("Could not load environments: " + err.message); }
  });

  $("connect").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (socket) return;
    const ws = $("workspace").value.trim();
    const env = $("environment").value;
    if (!ws || !env) { say("Choose a workspace and a running environment."); return; }
    let ticket;
    try {
      ticket = await api(`/v1/workspaces/${encodeURIComponent(ws)}/environments/${encodeURIComponent(env)}/terminal`,
        { method: "POST", body: "{}" });
    } catch (err) { say("Could not open terminal: " + err.message); return; }

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${proto}//${location.host}${ticket.websocketPath}`);
    socket.binaryType = "arraybuffer";
    setStatus("Connecting…", "idle");

    socket.onopen = () => socket.send(JSON.stringify({ type: "auth", ticket: ticket.ticket }));
    socket.onmessage = (ev) => {
      if (typeof ev.data !== "string") { term.write(new Uint8Array(ev.data)); return; }
      const msg = JSON.parse(ev.data);
      if (msg.type === "ready") {
        setStatus("Connected: " + msg.environment, "connected");
        say("");
        $("close").disabled = false;
        sendResize();
        term.focus();
      } else if (msg.type === "error") {
        setStatus("Error", "error");
        say(msg.message);
      } else if (msg.type === "exit") {
        say("Session ended (" + msg.reason + ").");
      }
    };
    socket.onclose = () => {
      socket = null;
      $("close").disabled = true;
      if ($("status").dataset.state !== "error") setStatus("Not connected", "idle");
    };
  });

  $("close").addEventListener("click", () => { if (socket) socket.close(); });

  term.onData((data) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "input", data }));
    }
  });

  function sendResize() {
    fit.fit();
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
    }
  }
  window.addEventListener("resize", sendResize);
})();
