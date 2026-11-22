"""Built-in lab content generators.

Each run of a lab gets fresh content, so answers can't be shared or looked up. Generators
are plain Python functions chosen by name from a fixed list: lab files never contain code.

A generator gets a random source, the setup file's path and (if it hides one) a flag, and
returns the files to write (path relative to /workspace -> bytes) plus any answers it made.
"""

from __future__ import annotations

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
    "auth-log": Generator(
        _auth_log, produces=frozenset({"attacker-ip", "attacker-user", "failed-count"})
    ),
}
