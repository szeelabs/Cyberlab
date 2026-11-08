"""CyberLab egress proxy: the only way out of a ``restricted-egress`` environment.

Standalone and standard-library only: CyberLab runs this file inside a tiny, hardened
container (``python -I -c <source>``) that sits on the workspace's private lab network and
on an outbound network. Environments have no route to the internet themselves.

Rules
* Every client must authenticate (``Proxy-Authorization: Basic``) with its environment's
  token. Only the token's SHA-256 is configured here, so tokens never leave the environment.
* The destination must be a hostname on that environment's allow-list (exact match),
  on an allowed port (default 80 and 443). IP literals are refused.
* The hostname is resolved once; if any address is not public (loopback, private,
  link-local such as cloud metadata, ...) the request is refused, and the connection goes to
  the checked address, so DNS cannot be used to redirect it afterwards.
* Supports ``CONNECT`` (HTTPS) and absolute-URI HTTP requests (forwarded with
  ``Connection: close`` so one connection cannot switch to another host).
* Every decision is logged to stdout as one JSON line (no request bodies or URLs paths).

Configuration (environment variable ``CYBERLAB_EGRESS_CONFIG``, JSON)::

    {"listen": "0.0.0.0:3128",
     "ports": [80, 443],
     "allowPrivate": false,          # tests only
     "clients": {"<sha256 of token>": {"name": "scanner", "hosts": ["ghcr.io"]}}}
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import socket
import sys
import time

MAX_HEAD = 16 * 1024
HEAD_TIMEOUT = 15
CONNECT_TIMEOUT = 15
IDLE_TIMEOUT = 300
MAX_CONNECTIONS = 256


def log(**fields) -> None:
    fields["ts"] = round(time.time(), 3)
    sys.stdout.write(json.dumps(fields, sort_keys=True) + "\n")
    sys.stdout.flush()


class Policy:
    def __init__(self, config: dict) -> None:
        self.ports = set(config.get("ports", [80, 443]))
        self.allow_private = bool(config.get("allowPrivate", False))
        self.clients = {
            h: {"name": c["name"], "hosts": {x.lower().rstrip(".") for x in c["hosts"]}}
            for h, c in config.get("clients", {}).items()
        }

    def client(self, header: str | None) -> dict | None:
        if not header or not header.lower().startswith("basic "):
            return None
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True).decode()
        except (ValueError, UnicodeDecodeError):
            return None
        token = decoded.split(":", 1)[1] if ":" in decoded else decoded
        return self.clients.get(hashlib.sha256(token.encode()).hexdigest())

    def check_target(self, client: dict, host: str, port: int) -> str | None:
        """Return a refusal reason, or None if the destination name/port is allowed."""
        try:
            ipaddress.ip_address(host.strip("[]"))
            return "ip-literal"
        except ValueError:
            pass
        if port not in self.ports:
            return "port"
        if host.lower().rstrip(".") not in client["hosts"]:
            return "not-allowed"
        return None

    async def resolve(self, host: str, port: int) -> tuple[str | None, str | None]:
        """Resolve once; refuse if any address is not public. Returns (address, reason)."""
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            return None, "dns"
        addrs = [i[4][0] for i in infos]
        if not addrs:
            return None, "dns"
        if not self.allow_private:
            for a in addrs:
                ip = ipaddress.ip_address(a.split("%")[0])
                if not ip.is_global:
                    return None, "non-public-address"
        return addrs[0], None


def split_host_port(authority: str, default_port: int) -> tuple[str, int]:
    if authority.startswith("["):  # [IPv6]:port
        host, _, rest = authority[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = authority.rpartition(":") if ":" in authority else (authority, "", "")
    return host, int(port) if port else default_port


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> int:
    total = 0
    try:
        while True:
            data = await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT)
            if not data:
                break
            total += len(data)
            writer.write(data)
            await writer.drain()
    except (TimeoutError, ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001,S110 - already closing
            pass
    return total


class EgressProxy:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.active = 0

    async def respond(self, writer, status: str, extra: str = "") -> None:
        body = status.encode()
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n"
            f"{extra}\r\n".encode()
            + body
        )
        try:
            await writer.drain()
        finally:
            writer.close()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.active >= MAX_CONNECTIONS:
            await self.respond(writer, "503 Service Unavailable")
            return
        self.active += 1
        try:
            await self._handle(reader, writer)
        except (ConnectionError, OSError, asyncio.IncompleteReadError, TimeoutError):
            writer.close()
        finally:
            self.active -= 1

    async def _handle(self, reader, writer) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
        except asyncio.LimitOverrunError:
            await self.respond(writer, "431 Request Header Fields Too Large")
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ")
        except ValueError:
            await self.respond(writer, "400 Bad Request")
            return
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers.append((k.strip(), v.strip()))
        auth = next((v for k, v in headers if k.lower() == "proxy-authorization"), None)
        client = self.policy.client(auth)
        if client is None:
            log(decision="deny", reason="auth", method=method)
            await self.respond(
                writer,
                "407 Proxy Authentication Required",
                'Proxy-Authenticate: Basic realm="cyberlab-egress"\r\n',
            )
            return

        try:
            if method == "CONNECT":
                host, port = split_host_port(target, 443)
                path_rest = None
            elif target.startswith("http://"):
                authority, _, path = target[len("http://") :].partition("/")
                host, port = split_host_port(authority, 80)
                path_rest = "/" + path
            else:
                host = ""
            if method != "CONNECT" and not target.startswith("http://"):
                raise ValueError("unsupported")
            if not host or not 0 < port < 65536:
                raise ValueError("bad target")
        except ValueError:
            log(client=client["name"], decision="deny", reason="unsupported", method=method)
            await self.respond(writer, "400 Bad Request")
            return

        reason = self.policy.check_target(client, host, port)
        address = None
        if reason is None:
            address, reason = await self.policy.resolve(host, port)
        entry = {"client": client["name"], "host": host.lower(), "port": port, "method": method}
        if reason is not None:
            log(decision="deny", reason=reason, **entry)
            await self.respond(writer, "403 Forbidden")
            return
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(address, port), CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError):
            log(decision="error", reason="connect", **entry)
            await self.respond(writer, "502 Bad Gateway")
            return
        log(decision="allow", **entry)

        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            hop = {"proxy-authorization", "proxy-connection", "connection", "keep-alive"}
            out = [f"{method} {path_rest} {version}"]
            out += [f"{k}: {v}" for k, v in headers if k.lower() not in hop]
            out.append("Connection: close")
            up_writer.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
            await up_writer.drain()
        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


async def serve(config: dict) -> None:
    proxy = EgressProxy(Policy(config))
    host, _, port = config.get("listen", "0.0.0.0:3128").rpartition(":")  # noqa: S104
    server = await asyncio.start_server(proxy.handle, host, int(port), limit=MAX_HEAD)
    log(
        event="listening",
        listen=config.get("listen", "0.0.0.0:3128"),  # noqa: S104
        clients=sorted(c["name"] for c in config.get("clients", {}).values()),
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve(json.loads(os.environ["CYBERLAB_EGRESS_CONFIG"])))
