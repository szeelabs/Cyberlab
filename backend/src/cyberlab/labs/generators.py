"""Built-in lab content generators.

Each run of a lab gets fresh content, so answers can't be shared or looked up. Generators
are plain Python functions chosen by name from a fixed list: lab files never contain code.

A generator gets a random source, the setup file's path and (if it hides one) a flag, and
returns the files to write (path relative to /workspace -> bytes) plus any answers it made.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import random
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Generated:
    files: dict[str, bytes]
    answers: dict[str, str] = field(default_factory=dict)
    modes: dict[str, int] = field(default_factory=dict)  # path -> permission bits (default 644)


@dataclass(frozen=True)
class Generator:
    run: Callable[[random.Random, str, str | None], Generated]
    produces: frozenset[str] = frozenset()  # answer names it makes
    needs_flag: bool = False


_WORDS = (
    "alpha bravo cedar delta ember falcon granite harbor indigo jasper kestrel lumen "
    "maple nimbus onyx pepper quartz raven sierra tundra umber violet willow xenon yarrow"
).split()


def _maze(rng: random.Random, path: str, flag: str | None) -> Generated:
    """Nested folders full of decoy files; one treasure.txt holds the flag."""
    files: dict[str, bytes] = {}
    dirs = [path]
    for _ in range(4):
        dirs = [f"{d}/{w}" for d in dirs for w in rng.sample(_WORDS, 3)]
    for d in dirs:
        for name in rng.sample(("notes.txt", "todo.txt", "readme.txt", "list.txt"), 2):
            files[f"{d}/{name}"] = f"Nothing here. Keep looking ({rng.choice(_WORDS)}).\n".encode()
    hidden = rng.choice(dirs)
    files[f"{hidden}/treasure.txt"] = f"You found it! The flag is {flag}\n".encode()
    return Generated(files)


def _app_log(rng: random.Random, path: str, flag: str | None) -> Generated:
    """A long application log; exactly one line contains the flag."""
    start = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
    msgs = (
        "INFO  request served in {n} ms",
        "INFO  user session refreshed",
        "WARN  cache miss for key item-{n}",
        "INFO  health check ok",
        "DEBUG worker {n} idle",
        "ERROR timeout talking to database (attempt {n})",
    )
    lines = []
    for i in range(3000):
        t = start + timedelta(seconds=i * 7)
        lines.append(f"{t:%Y-%m-%d %H:%M:%S} " + rng.choice(msgs).format(n=rng.randint(1, 999)))
    lines.insert(rng.randint(500, 2500), f"{start:%Y-%m-%d} 12:34:56 INFO  secret flag: {flag}")
    return Generated({path: ("\n".join(lines) + "\n").encode()})


def _ip(rng: random.Random) -> str:
    # Documentation ranges only (RFC 5737): never a real host.
    net = rng.choice(("192.0.2", "198.51.100", "203.0.113"))
    return f"{net}.{rng.randint(2, 254)}"


def _auth_log(rng: random.Random, path: str, flag: str | None) -> Generated:
    """An SSH log: normal logins, background noise, and one brute-force attacker who
    eventually gets in. Answers: the attacker's IP, the account they broke into, and how
    many failed passwords they tried."""
    users = ["alice", "bob", "carol", "dave", "erin"]
    attacker = _ip(rng)
    noise = [_ip(rng) for _ in range(6)]
    noise = [ip for ip in noise if ip != attacker] or [_ip(rng)]
    victim = rng.choice(users)
    failures = rng.randint(40, 90)
    t = datetime(2026, 3, 14, 6, 0, tzinfo=UTC)
    events: list[tuple[datetime, str]] = []

    def at(minutes: float, msg: str) -> None:
        events.append((t + timedelta(minutes=minutes), msg))

    pid = rng.randint(1000, 5000)
    for _ in range(60):  # ordinary users logging in
        u, ip = rng.choice(users), rng.choice(noise)
        at(
            rng.uniform(0, 600),
            f"Accepted publickey for {u} from {ip} port {rng.randint(30000, 60000)} ssh2",
        )
    for _ in range(25):  # a few typos from ordinary users
        ip = rng.choice(noise)
        at(
            rng.uniform(0, 600),
            f"Failed password for {rng.choice(users)} from {ip} "
            f"port {rng.randint(30000, 60000)} ssh2",
        )
    start = rng.uniform(200, 400)
    for i in range(failures):
        name = victim if i % 3 else rng.choice(("root", "admin", "test", victim))
        at(
            start + i * 0.05,
            f"Failed password for {name} from {attacker} port {rng.randint(30000, 60000)} ssh2",
        )
    at(
        start + failures * 0.05 + 0.1,
        f"Accepted password for {victim} from {attacker} port {rng.randint(30000, 60000)} ssh2",
    )
    events.sort()
    lines = []
    for when, msg in events:
        pid += rng.randint(1, 9)
        lines.append(f"{when:%b %d %H:%M:%S} webserver sshd[{pid}]: {msg}")
    # Noise addresses fail at most 25 times in total, the attacker at least 40 times.
    return Generated(
        {path: ("\n".join(lines) + "\n").encode()},
        {"attacker-ip": attacker, "attacker-user": victim, "failed-count": str(failures)},
    )


def _flag() -> str:
    return f"CL{{{secrets.token_hex(8)}}}"


def _config(path: str, services: list[dict]) -> bytes:
    return json.dumps({"services": services}, indent=2).encode()


def _scan_target(rng: random.Random, path: str, flag: str | None) -> Generated:
    """Practice services for a port-scanning lab: three common services, and one on a
    high port that a default nmap scan (top 1000 ports) misses."""
    ssh_version = rng.choice(("8.9p1", "9.2p1", "9.6p1", "9.7p1"))
    hidden = rng.randint(20000, 40000)
    secret = _flag()
    services = [
        {"port": 21, "kind": "banner", "banner": "220 (vsFTPd 3.0.5)"},
        {"port": 22, "kind": "banner", "banner": f"SSH-2.0-OpenSSH_{ssh_version} Ubuntu-3ubuntu13"},
        {
            "port": 80,
            "kind": "http",
            "server": "nginx/1.24.0",
            "routes": {"/": {"body": "<html><body><h1>Coming soon</h1></body></html>\n"}},
        },
        {
            "port": hidden,
            "kind": "banner",
            "banner": f"220 backup-agent 2.1 ready (flag: {secret})",
        },
    ]
    return Generated(
        {path: _config(path, services)},
        {
            "open-ports": "3",
            "ssh-version": ssh_version,
            "hidden-port": str(hidden),
            "banner-flag": secret,
        },
    )


def _web_target(rng: random.Random, path: str, flag: str | None) -> Generated:
    """A small web app that leaks three secrets: a comment in the page source, a path
    "hidden" in robots.txt, and another user's note reachable by changing a number (IDOR)."""
    comment, hidden_flag, note_flag = _flag(), _flag(), _flag()
    secret_dir = f"{rng.choice(_WORDS)}-admin-{secrets.token_hex(3)}"
    admin_note = rng.randint(17, 99)
    page = (
        "<!doctype html>\n<html><head><title>Notely</title></head><body>\n"
        "<h1>Notely</h1>\n<p>The simplest place for your notes.</p>\n"
        f"<!-- TODO: remove before launch. Debug key: {comment} -->\n"
        '<p>Your notes: <a href="/api/notes">/api/notes</a></p>\n</body></html>\n'
    )
    routes: dict[str, dict] = {
        "/": {"body": page},
        "/index.html": {"body": page},
        "/robots.txt": {
            "type": "text/plain",
            "body": f"User-agent: *\nDisallow: /{secret_dir}/\n",
        },
        f"/{secret_dir}": {"status": 301, "location": f"/{secret_dir}/", "body": ""},
        f"/{secret_dir}/": {
            "body": f"<html><body><h1>Admin area</h1><p>Flag: {hidden_flag}</p></body></html>\n"
        },
        "/api/notes": {
            "type": "application/json",
            "body": json.dumps({"user": "guest", "notes": [1, 2, 3], "read": "/api/notes?id=1"}),
        },
    }
    texts = {1: "Buy milk", 2: "Call the bank", 3: "Finish the report"}
    for i, text in texts.items():
        routes[f"/api/notes?id={i}"] = {
            "type": "application/json",
            "body": json.dumps({"id": i, "owner": "guest", "text": text}),
        }
    routes[f"/api/notes?id={admin_note}"] = {
        "type": "application/json",
        "body": json.dumps({"id": admin_note, "owner": "admin", "text": f"Flag: {note_flag}"}),
    }
    services = [{"port": 80, "kind": "http", "server": "nginx/1.24.0", "routes": routes}]
    return Generated(
        {path: _config(path, services)},
        {"source-flag": comment, "robots-flag": hidden_flag, "idor-flag": note_flag},
    )


# ---------------------------------------------------------------- crypto and encodings
def _rot13(text: str) -> str:
    out = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr((ord(ch) - 97 + 13) % 26 + 97))
        elif "A" <= ch <= "Z":
            out.append(chr((ord(ch) - 65 + 13) % 26 + 65))
        else:
            out.append(ch)
    return "".join(out)


def _crypto_basics(rng: random.Random, path: str, flag: str | None) -> Generated:
    """Encodings and hashes: base64, hex, ROT13, and finding a file by its SHA-256."""
    b64_flag, hex_flag, rot_flag = _flag(), _flag(), _flag()
    files = {
        f"{path}/message.b64": base64.b64encode(f"The flag is {b64_flag}\n".encode()) + b"\n",
        f"{path}/message.hex": (f"The flag is {hex_flag}\n".encode().hex().upper() + "\n").encode(),
        f"{path}/message.rot13": (_rot13(f"The flag is {rot_flag}") + "\n").encode(),
    }
    names = [f"{rng.choice(_WORDS)}-{i:02d}.txt" for i in range(40)]
    contents = {n: f"report {n} {secrets.token_hex(12)}\n".encode() for n in names}
    wanted = rng.choice(names)
    digest = hashlib.sha256(contents[wanted]).hexdigest()
    for n, data in contents.items():
        files[f"{path}/reports/{n}"] = data
    files[f"{path}/WANTED.txt"] = (
        f"One report was leaked. Its SHA-256 fingerprint is:\n{digest}\n"
    ).encode()
    return Generated(
        files,
        {
            "base64-flag": b64_flag,
            "hex-flag": hex_flag,
            "rot13-flag": rot_flag,
            "leaked-file": wanted,
        },
    )


# ---------------------------------------------------------------- permissions
def _permissions(rng: random.Random, path: str, flag: str | None) -> Generated:
    """A script that isn't executable yet, a locked file, and a world-writable file."""
    run_flag, locked_flag = _flag(), _flag()
    names = [f"{w}.conf" for w in rng.sample(_WORDS, 8)]
    risky = rng.choice(names)
    files = {
        f"{path}/hello.sh": f"#!/bin/sh\necho 'It runs! Flag: {run_flag}'\n".encode(),
        f"{path}/locked.txt": f"You unlocked it. Flag: {locked_flag}\n".encode(),
    }
    modes = {f"{path}/hello.sh": 0o644, f"{path}/locked.txt": 0o000}
    for n in names:
        files[f"{path}/configs/{n}"] = f"# settings for {n}\nenabled=true\n".encode()
        modes[f"{path}/configs/{n}"] = 0o666 if n == risky else 0o640
    return Generated(
        files, {"run-flag": run_flag, "locked-flag": locked_flag, "risky-file": risky}, modes
    )


# ---------------------------------------------------------------- incident response
def _access_log(rng: random.Random, path: str, flag: str | None) -> Generated:
    """A web server access log with normal traffic, a scanner, a SQL injection attempt and
    a web shell upload followed by its use."""
    start = datetime(2026, 5, 2, 9, 0, tzinfo=UTC)
    visitors = [_ip(rng) for _ in range(12)]
    attacker = _ip(rng)
    while attacker in visitors:
        attacker = _ip(rng)
    scanner_agent = rng.choice(("sqlmap/1.8.2#stable", "Nikto/2.5.0", "masscan/1.3"))
    shell = f"/uploads/{rng.choice(_WORDS)}-{secrets.token_hex(2)}.php"
    pages = ["/", "/about", "/products", "/products?id=3", "/contact", "/login", "/static/app.css"]
    agents = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Mozilla/5.0 (X11; Linux x86_64)"]
    lines: list[tuple[datetime, str]] = []

    def hit(t: datetime, ip: str, req: str, status: int, agent: str) -> None:
        stamp = t.strftime("%d/%b/%Y:%H:%M:%S +0000")
        lines.append(
            (
                t,
                f'{ip} - - [{stamp}] "{req} HTTP/1.1" {status} '
                f'{rng.randint(200, 9000)} "-" "{agent}"',
            )
        )

    for _ in range(400):
        hit(
            start + timedelta(seconds=rng.randint(0, 6 * 3600)),
            rng.choice(visitors),
            f"GET {rng.choice(pages)}",
            200,
            rng.choice(agents),
        )
    t = start + timedelta(hours=2, minutes=rng.randint(0, 50))
    for i, probe in enumerate(
        ("/admin", "/.git/config", "/wp-login.php", "/backup.zip", "/phpinfo.php")
    ):
        hit(t + timedelta(seconds=i), attacker, f"GET {probe}", 404, scanner_agent)
    hit(
        t + timedelta(minutes=3), attacker, "GET /products?id=3%27%20OR%201=1--", 500, scanner_agent
    )
    hit(t + timedelta(minutes=9), attacker, "POST /upload", 200, agents[0])
    hit(t + timedelta(minutes=10), attacker, f"GET {shell}?cmd=id", 200, agents[0])
    hit(t + timedelta(minutes=11), attacker, f"GET {shell}?cmd=cat%20/etc/passwd", 200, agents[0])
    lines.sort()
    return Generated(
        {path: ("\n".join(text for _, text in lines) + "\n").encode()},
        {
            "scanner-ip": attacker,
            "scanner-tool": scanner_agent.split("/")[0],
            "webshell-path": shell,
        },
    )


# ---------------------------------------------------------------- forensics
def _carving(rng: random.Random, path: str, flag: str | None) -> Generated:
    """A "photo" with a plain-text note inside it and a compressed file hidden after it."""
    note_flag, hidden_flag = _flag(), _flag()

    def noise(n: int) -> bytes:
        # Random filler that can't be mistaken for a gzip header (1f 8b) by chance.
        return secrets.token_bytes(n).replace(b"\x1f\x8b", b"\x1f\x00")

    head = b"\x89PNG\r\n\x1a\n" + noise(rng.randint(3000, 6000))
    note = f"\x00\x00note: {note_flag}\x00\x00".encode()
    tail = noise(rng.randint(2000, 4000))
    hidden = gzip.compress(f"Carved it out! Flag: {hidden_flag}\n".encode(), mtime=0)
    blob = head + note + tail
    offset = len(blob)
    return Generated(
        {path: blob + hidden},
        {"note-flag": note_flag, "gzip-offset": str(offset), "carved-flag": hidden_flag},
    )


# ---------------------------------------------------------------- web: logins and keys
def _web_login_target(rng: random.Random, path: str, flag: str | None) -> Generated:
    """A login with a default password from a wordlist, and an API key leaked in JavaScript."""
    admin = rng.choice(("admin", "administrator", "root", "operator"))
    words = [
        "123456",
        "password",
        "admin",
        "letmein",
        "qwerty",
        "welcome",
        "monkey",
        "dragon",
        "changeme",
        "default",
        "summer2026",
        "passw0rd",
        "secret",
        "trustno1",
    ]
    password = rng.choice(words[2:])
    login_flag, key_flag = _flag(), _flag()
    api_key = secrets.token_hex(12)
    page = (
        "<!doctype html>\n<html><head><title>FleetDesk</title>"
        '<script src="/static/app.js"></script></head><body>\n'
        "<h1>FleetDesk</h1>\n<p>Login: GET /login?user=NAME&amp;password=PASS</p>\n"
        f"<p>Questions? Ask our {admin} at support@fleetdesk.test</p>\n</body></html>\n"
    )
    js = (
        "// FleetDesk front end\n"
        f"const API_KEY = '{api_key}'; // TODO move to the server\n"
        "fetch('/api/fleet?key=' + API_KEY);\n"
    )
    routes = {
        "/": {"body": page},
        "/static/app.js": {"type": "application/javascript", "body": js},
        f"/login?user={admin}&password={password}": {
            "body": f"<h1>Welcome back, {admin}</h1><p>Flag: {login_flag}</p>\n"
        },
        f"/api/fleet?key={api_key}": {
            "type": "application/json",
            "body": json.dumps({"vehicles": 42, "flag": key_flag}),
        },
    }
    service = {
        "port": 80,
        "kind": "http",
        "server": "nginx/1.24.0",
        "routes": routes,
        "fallbacks": {
            "/login": {"status": 401, "body": "<h1>Wrong user name or password</h1>\n"},
            "/api/fleet": {
                "status": 403,
                "type": "application/json",
                "body": '{"error": "bad key"}',
            },
        },
    }
    return Generated(
        {path: _config(path, [service])},
        {"admin-user": admin, "login-flag": login_flag, "key-flag": key_flag},
    )


def _wordlist(rng: random.Random, path: str, flag: str | None) -> Generated:
    words = [
        "123456",
        "password",
        "admin",
        "letmein",
        "qwerty",
        "welcome",
        "monkey",
        "dragon",
        "changeme",
        "default",
        "summer2026",
        "passw0rd",
        "secret",
        "trustno1",
    ]
    return Generated({path: ("\n".join(words) + "\n").encode()})


# ---------------------------------------------------------------- advanced: a chain
def _chain_target(rng: random.Random, path: str, flag: str | None) -> Generated:
    """Several steps, each giving the next clue: a web app on an unusual port, a backup file
    named in a comment, credentials inside it, and an admin API that needs them."""
    port = rng.randint(8000, 9999)
    backup = f"/{rng.choice(_WORDS)}-backup-{secrets.token_hex(2)}.txt"
    user = rng.choice(("deploy", "svc-backup", "ops"))
    password = f"{rng.choice(_WORDS).capitalize()}{rng.randint(10, 99)}!"
    token = secrets.token_hex(10)
    final = _flag()
    routes = {
        "/": {
            "body": "<html><body><h1>Internal tools</h1>\n"
            f"<!-- nightly backup moved to {backup} -->\n</body></html>\n"
        },
        backup: {
            "type": "text/plain",
            "body": f"# config backup\nservice_user={user}\nservice_password={password}\n",
        },
        f"/api/token?user={user}&password={password}": {
            "type": "application/json",
            "body": json.dumps({"token": token, "expires": 3600}),
        },
        f"/api/admin/flag?token={token}": {
            "type": "application/json",
            "body": json.dumps({"flag": final}),
        },
    }
    services = [
        {"port": 22, "kind": "banner", "banner": "SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13"},
        {
            "port": port,
            "kind": "http",
            "server": "gunicorn",
            "routes": routes,
            "fallbacks": {
                "/api/token": {
                    "status": 401,
                    "type": "application/json",
                    "body": '{"error": "bad credentials"}',
                },
                "/api/admin/flag": {
                    "status": 401,
                    "type": "application/json",
                    "body": '{"error": "token required"}',
                },
            },
        },
    ]
    return Generated(
        {path: _config(path, services)},
        {"web-port": str(port), "service-user": user, "chain-flag": final},
    )


GENERATORS: dict[str, Generator] = {
    "maze": Generator(_maze, needs_flag=True),
    "app-log": Generator(_app_log, needs_flag=True),
    "scan-target": Generator(
        _scan_target,
        produces=frozenset({"open-ports", "ssh-version", "hidden-port", "banner-flag"}),
    ),
    "web-target": Generator(
        _web_target, produces=frozenset({"source-flag", "robots-flag", "idor-flag"})
    ),
    "crypto-basics": Generator(
        _crypto_basics,
        produces=frozenset({"base64-flag", "hex-flag", "rot13-flag", "leaked-file"}),
    ),
    "permissions": Generator(
        _permissions, produces=frozenset({"run-flag", "locked-flag", "risky-file"})
    ),
    "access-log": Generator(
        _access_log, produces=frozenset({"scanner-ip", "scanner-tool", "webshell-path"})
    ),
    "carving": Generator(_carving, produces=frozenset({"note-flag", "gzip-offset", "carved-flag"})),
    "web-login-target": Generator(
        _web_login_target, produces=frozenset({"admin-user", "login-flag", "key-flag"})
    ),
    "wordlist": Generator(_wordlist),
    "chain-target": Generator(
        _chain_target, produces=frozenset({"web-port", "service-user", "chain-flag"})
    ),
    "auth-log": Generator(
        _auth_log, produces=frozenset({"attacker-ip", "attacker-user", "failed-count"})
    ),
}
