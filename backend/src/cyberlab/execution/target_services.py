"""CyberLab practice services: the program inside a lab's practice target.

Copied into the target's image and run as the unprivileged user. Standard library only.

It is *deliberately leaky*, not exploitable: every answer comes from a fixed table in its
config file, written by the lab when it starts (``/workspace/.cyberlab-target.json``). There
is no code path that runs commands, reads other files or changes state, so the "weakness" a
learner finds is always a planted clue (a banner, a hidden page, a guessable note number).

Config::

    {"services": [
      {"port": 22, "kind": "banner", "banner": "SSH-2.0-OpenSSH_9.6"},
      {"port": 80, "kind": "http", "server": "nginx", "routes": {
          "/": {"status": 200, "type": "text/html", "body": "<h1>hi</h1>"}}}
    ]}
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

CONFIG = os.environ.get("CYBERLAB_TARGET_CONFIG", "/workspace/.cyberlab-target.json")
HOST = os.environ.get("CYBERLAB_TARGET_HOST", "0.0.0.0")  # noqa: S104 - lab network only
MAX_REQUEST = 16 * 1024
TIMEOUT = 10
REASONS = {200: "OK", 301: "Moved Permanently", 403: "Forbidden", 404: "Not Found"}
NOT_FOUND = "<html><body><h1>404 Not Found</h1></body></html>\n"


def load(path: str, wait: float | None = None) -> dict:
    """Wait for the lab to write the config, then read it."""
    deadline = None if wait is None else time.monotonic() + wait
    while not os.path.exists(path):
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError(f"no config at {path}")
        time.sleep(0.5)
    time.sleep(0.2)  # let the writer finish
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def http_response(service: dict, raw: bytes) -> bytes:
    line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split(" ")
    method, target = (parts[0], parts[1]) if len(parts) >= 2 else ("GET", "/")
    route = service.get("routes", {}).get(target)
    if route is None:
        status, ctype, body = 404, "text/html", NOT_FOUND
    else:
        status = int(route.get("status", 200))
        ctype = route.get("type", "text/html")
        body = route.get("body", "")
    data = body.encode()
    head = [
        f"HTTP/1.1 {status} {REASONS.get(status, 'OK')}",
        f"Server: {service.get('server', 'nginx')}",
        f"Content-Type: {ctype}; charset=utf-8",
        f"Content-Length: {len(data)}",
        "Connection: close",
    ]
    if route and route.get("location"):
        head.append(f"Location: {route['location']}")
    out = ("\r\n".join(head) + "\r\n\r\n").encode()
    return out if method == "HEAD" else out + data


async def handle(service: dict, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        if service["kind"] == "banner":
            writer.write(service["banner"].encode() + b"\r\n")
            await writer.drain()
            # Wait briefly for the client to say something, as real services do.
            await asyncio.wait_for(reader.readline(), timeout=3)
        else:
            raw = b""
            while b"\r\n\r\n" not in raw and len(raw) < MAX_REQUEST:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=TIMEOUT)
                if not chunk:
                    break
                raw += chunk
            if raw:
                writer.write(http_response(service, raw))
                await writer.drain()
    except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def serve(config: dict) -> list[asyncio.Server]:
    servers = []
    for service in config["services"]:

        async def on_connect(r, w, s=service):
            await handle(s, r, w)

        servers.append(await asyncio.start_server(on_connect, HOST, int(service["port"])))
    return servers


async def main() -> None:
    config = await asyncio.to_thread(load, CONFIG)
    servers = await serve(config)
    print(f"practice services on ports {[s['port'] for s in config['services']]}", flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
