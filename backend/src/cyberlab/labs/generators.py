"""Built-in lab content generators.

Each run of a lab gets fresh content, so answers can't be shared or looked up. Generators
are plain Python functions chosen by name from a fixed list: lab files never contain code.

A generator gets a random source, the setup file's path and (if it hides one) a flag, and
returns the files to write (path relative to /workspace -> bytes) plus any answers it made.
"""

from __future__ import annotations

import random
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


GENERATORS: dict[str, Generator] = {
    "maze": Generator(_maze, needs_flag=True),
    "app-log": Generator(_app_log, needs_flag=True),
    "auth-log": Generator(
        _auth_log, produces=frozenset({"attacker-ip", "attacker-user", "failed-count"})
    ),
}
