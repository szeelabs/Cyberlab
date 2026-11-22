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
          "/": {"status": 200, "type": "text/html", "body": "<h1>hi</h1>"}},
       "fallbacks": {"/login": {"status": 401, "body": "wrong password"}}}
    ]}
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from urllib.parse import parse_qs, urlsplit

CONFIG = os.environ.get("CYBERLAB_TARGET_CONFIG", "/workspace/.cyberlab-target.json")
HOST = os.environ.get("CYBERLAB_TARGET_HOST", "0.0.0.0")  # noqa: S104 - lab network only
MAX_REQUEST = 16 * 1024
TIMEOUT = 10
REASONS = {
    200: "OK",
    301: "Moved Permanently",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    502: "Bad Gateway",
}
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


async def respond(service: dict, raw: bytes) -> bytes:
    line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split(" ")
    target = parts[1] if len(parts) >= 2 else "/"
    path, _, query = target.partition("?")
    fetcher = service.get("fetchers", {}).get(path)
    if fetcher:
        values = parse_qs(query).get(fetcher.get("param", "url"), [])
        if values:
            status, text = await fetch(values[0], [h.lower() for h in fetcher.get("allow", [])])
            route = {"status": status, "type": "text/plain", "body": text}
            return http_response({**service, "routes": {target: route}}, raw)
    return http_response(service, raw)


def http_response(service: dict, raw: bytes) -> bytes:
    line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split(" ")
    method, target = (parts[0], parts[1]) if len(parts) >= 2 else ("GET", "/")
    route = service.get("routes", {}).get(target)
    if route is None and target.split("?")[0] in service.get("fallbacks", {}):
        # e.g. a login page answering "wrong password" for every guess but the right one
        route = service["fallbacks"][target.split("?")[0]]
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


FETCH_LIMIT = 64 * 1024


async def fetch(url: str, allow: list[str]) -> tuple[int, str]:
    """The deliberately unsafe "link preview" (SSRF) of some practice targets: it fetches a
    URL *from the target's point of view*. Limited to plain http and to the lab machines
    named in the config, and the lab network has no internet anyway."""
    u = urlsplit(url)
    host = (u.hostname or "").lower()
    if u.scheme != "http" or host not in allow:
        return 400, "Preview only works for http links to our own servers.\n"
    port = u.port or 80
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
    except (OSError, TimeoutError):
        return 502, f"Preview failed: could not connect to {host}:{port}\n"
    try:
        writer.write(f"GET {path} HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
        await writer.drain()
        raw = b""
        while len(raw) < FETCH_LIMIT:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=3)
            if not chunk:
                break
            raw += chunk
    except (OSError, TimeoutError):
        return 502, f"Preview failed: {host}:{port} did not answer\n"
    finally:
        writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    if not head.startswith(b"HTTP/"):
        body = raw  # not a web server: show what it sent
    return 200, f"Preview of {url}:\n" + body[:FETCH_LIMIT].decode("utf-8", "replace")


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
                writer.write(await respond(service, raw))
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
