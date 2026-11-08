"""The egress proxy, run in-process against a local upstream server (no Docker)."""

import asyncio
import base64
import hashlib
import json

import pytest

from cyberlab.egress.proxy import EgressProxy, Policy, split_host_port

TOKEN = "scanner-token-0123456789abcdef"
OTHER = "other-env-token-0123456789abcd"


def h(token):
    return hashlib.sha256(token.encode()).hexdigest()


def auth(token=TOKEN):
    return "Basic " + base64.b64encode(f"env:{token}".encode()).decode()


async def start_upstream():
    async def handler(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        body = b"hello from upstream; got: " + head.split(b"\r\n")[0]
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def start_proxy(upstream_port, allow_private=True, extra_ports=()):
    config = {
        "allowPrivate": allow_private,
        "ports": [80, 443, upstream_port, *extra_ports],
        "clients": {
            h(TOKEN): {"name": "scanner", "hosts": ["localhost"]},
            h(OTHER): {"name": "other", "hosts": ["example.org"]},
        },
    }
    proxy = EgressProxy(Policy(config))
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0, limit=16 * 1024)
    return server, server.sockets[0].getsockname()[1]


async def request(proxy_port, head: str) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(head.encode())
    await writer.drain()
    data = await asyncio.wait_for(reader.read(65536), 5)
    writer.close()
    return data.decode("latin-1")


async def scenario(fn, allow_private=True):
    up, up_port = await start_upstream()
    px, px_port = await start_proxy(up_port, allow_private)
    try:
        return await fn(px_port, up_port)
    finally:
        px.close()
        up.close()


def run(fn, allow_private=True):
    return asyncio.run(scenario(fn, allow_private))


def test_http_forward_allowed(capsys):
    async def fn(px, up):
        return await request(
            px,
            f"GET http://localhost:{up}/index.html HTTP/1.1\r\n"
            f"Host: localhost\r\nProxy-Authorization: {auth()}\r\n\r\n",
        )

    out = run(fn)
    assert out.startswith("HTTP/1.1 200 OK") and "GET /index.html HTTP/1.1" in out
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {"decision": "allow", "client": "scanner", "host": "localhost"}.items() <= logged[
        -1
    ].items()


def test_connect_tunnel_allowed():
    async def fn(px, up):
        reader, writer = await asyncio.open_connection("127.0.0.1", px)
        writer.write(
            f"CONNECT localhost:{up} HTTP/1.1\r\nProxy-Authorization: {auth()}\r\n\r\n".encode()
        )
        await writer.drain()
        established = await reader.readuntil(b"\r\n\r\n")
        writer.write(b"GET /through-tunnel HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(65536), 5)
        writer.close()
        return established.decode(), data.decode()

    established, data = run(fn)
    assert established.startswith("HTTP/1.1 200 Connection Established")
    assert "GET /through-tunnel" in data


@pytest.mark.parametrize(
    "header", [None, auth("wrong-token-xxxxxxxxxxxxxxxx"), "Basic !!!", "Bearer abc"]
)
def test_authentication_required(header, capsys):
    async def fn(px, up):
        extra = f"Proxy-Authorization: {header}\r\n" if header else ""
        return await request(px, f"CONNECT localhost:{up} HTTP/1.1\r\n{extra}\r\n")

    out = run(fn)
    assert out.startswith("HTTP/1.1 407") and "Proxy-Authenticate: Basic" in out
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["reason"] == "auth"


def test_host_not_on_allow_list():
    async def fn(px, up):
        return await request(
            px, f"CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: {auth()}\r\n\r\n"
        )

    assert run(fn).startswith("HTTP/1.1 403")


def test_allow_lists_are_per_environment():
    # "other" may reach example.org but not localhost; "scanner" the reverse.
    async def fn(px, up):
        return await request(
            px, f"CONNECT localhost:{up} HTTP/1.1\r\nProxy-Authorization: {auth(OTHER)}\r\n\r\n"
        )

    assert run(fn).startswith("HTTP/1.1 403")


@pytest.mark.parametrize("target", ["127.0.0.1:443", "[::1]:443", "169.254.169.254:80"])
def test_ip_literals_refused(target, capsys):
    async def fn(px, up):
        return await request(
            px, f"CONNECT {target} HTTP/1.1\r\nProxy-Authorization: {auth()}\r\n\r\n"
        )

    assert run(fn).startswith("HTTP/1.1 403")
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["reason"] == "ip-literal"


def test_port_not_allowed():
    async def fn(px, up):
        return await request(
            px, f"CONNECT localhost:22 HTTP/1.1\r\nProxy-Authorization: {auth()}\r\n\r\n"
        )

    assert run(fn).startswith("HTTP/1.1 403")


def test_allowed_name_resolving_to_private_address_is_refused(capsys):
    # "localhost" is on the list, but it resolves to 127.0.0.1: never reachable for real.
    async def fn(px, up):
        return await request(
            px, f"CONNECT localhost:{up} HTTP/1.1\r\nProxy-Authorization: {auth()}\r\n\r\n"
        )

    assert run(fn, allow_private=False).startswith("HTTP/1.1 403")
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["reason"] == "non-public-address"


@pytest.mark.parametrize(
    "line",
    [
        "GET /relative HTTP/1.1",
        "CONNECT localhost:abc HTTP/1.1",
        "CONNECT localhost:0 HTTP/1.1",
        "GARBAGE",
        "GET ftp://localhost/ HTTP/1.1",
    ],
)
def test_malformed_requests(line):
    async def fn(px, up):
        return await request(px, f"{line}\r\nProxy-Authorization: {auth()}\r\n\r\n")

    assert run(fn).startswith("HTTP/1.1 400")


def test_oversized_header_rejected():
    async def fn(px, up):
        return await request(
            px,
            "GET http://localhost/ HTTP/1.1\r\nX: "
            + "a" * 20000
            + f"\r\nProxy-Authorization: {auth()}\r\n\r\n",
        )

    assert run(fn).startswith("HTTP/1.1 431")


def test_proxy_credentials_not_forwarded_upstream():
    async def fn(px, up):
        captured = {}

        async def handler(reader, writer):
            captured["head"] = (await reader.readuntil(b"\r\n\r\n")).decode()
            writer.write(b"HTTP/1.1 204 No Content\r\n\r\n")
            await writer.drain()
            writer.close()

        srv = await asyncio.start_server(handler, "127.0.0.1", up + 1 if up < 65000 else 0)
        port = srv.sockets[0].getsockname()[1]
        proxy = EgressProxy(
            Policy(
                {
                    "allowPrivate": True,
                    "ports": [port],
                    "clients": {h(TOKEN): {"name": "scanner", "hosts": ["localhost"]}},
                }
            )
        )
        psrv = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
        await request(
            psrv.sockets[0].getsockname()[1],
            f"GET http://localhost:{port}/ HTTP/1.1\r\nHost: localhost\r\n"
            f"Proxy-Authorization: {auth()}\r\nConnection: keep-alive\r\n\r\n",
        )
        srv.close()
        psrv.close()
        return captured["head"]

    head = run(fn)
    assert "Proxy-Authorization" not in head and "Connection: close" in head
    assert TOKEN not in head


def test_split_host_port():
    assert split_host_port("example.com", 80) == ("example.com", 80)
    assert split_host_port("example.com:8443", 443) == ("example.com", 8443)
    assert split_host_port("[2001:db8::1]:443", 443) == ("2001:db8::1", 443)


def test_policy_resolution_uses_real_dns_rules():
    # A name that does not resolve is refused, not passed through.
    policy = Policy({"clients": {}})
    addr, reason = asyncio.run(policy.resolve("does-not-exist.invalid", 443))
    assert addr is None and reason == "dns"
