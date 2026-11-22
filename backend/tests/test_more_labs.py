"""The generators behind the second set of labs: every answer must match its content."""

import base64
import gzip
import hashlib
import io
import json
import random
import tarfile

import pytest

from cyberlab.api.labs import _archive
from cyberlab.labs.generators import GENERATORS

SEEDS = [1, 2, 3, 42, 2026]


def run(name, path, seed):
    return GENERATORS[name].run(random.Random(seed), path, None)  # noqa: S311


def rot13(text):
    return text.translate(
        str.maketrans(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
            "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm",
        )
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_crypto_basics(seed):
    out = run("crypto-basics", "puzzles", seed)
    f, a = out.files, out.answers
    assert a["base64-flag"] in base64.b64decode(f["puzzles/message.b64"]).decode()
    hex_text = f["puzzles/message.hex"].decode().strip()
    assert hex_text == hex_text.upper()  # basenc --base16 needs upper case
    assert a["hex-flag"] in bytes.fromhex(hex_text).decode()
    assert a["rot13-flag"] in rot13(f["puzzles/message.rot13"].decode())
    assert a["rot13-flag"] not in f["puzzles/message.rot13"].decode()
    wanted = f["puzzles/WANTED.txt"].decode().split()[-1]
    matches = [
        p
        for p, d in f.items()
        if p.startswith("puzzles/reports/") and hashlib.sha256(d).hexdigest() == wanted
    ]
    assert matches == [f"puzzles/reports/{a['leaked-file']}"]


@pytest.mark.parametrize("seed", SEEDS)
def test_permissions(seed):
    out = run("permissions", "perms", seed)
    assert out.modes["perms/hello.sh"] == 0o644  # not executable yet: that's the task
    assert out.modes["perms/locked.txt"] == 0o000
    risky = [p for p, m in out.modes.items() if m & 0o002]
    assert risky == [f"perms/configs/{out.answers['risky-file']}"]
    assert out.answers["run-flag"] in out.files["perms/hello.sh"].decode()
    assert out.files["perms/hello.sh"].startswith(b"#!/bin/sh\n")


def test_archive_carries_the_permissions():
    out = run("permissions", "perms", 1)
    with tarfile.open(fileobj=io.BytesIO(_archive(out.files, out.modes))) as tar:
        modes = {m.name: m.mode for m in tar.getmembers() if m.isfile()}
    assert modes["perms/locked.txt"] == 0 and modes["perms/hello.sh"] == 0o644


@pytest.mark.parametrize("seed", SEEDS)
def test_access_log(seed):
    out = run("access-log", "access.log", seed)
    lines = out.files["access.log"].decode().splitlines()
    a = out.answers
    not_found = {}
    for line in lines:
        if line.split()[8] == "404":
            not_found[line.split()[0]] = not_found.get(line.split()[0], 0) + 1
    assert max(not_found, key=not_found.get) == a["scanner-ip"]
    agents = {line.split('"')[5] for line in lines if line.startswith(a["scanner-ip"] + " ")}
    assert any(ua.startswith(a["scanner-tool"] + "/") for ua in agents)
    shells = {line.split()[6].split("?")[0] for line in lines if "cmd=" in line}
    assert shells == {a["webshell-path"]}
    assert all(
        line.split()[0].startswith(("192.0.2.", "198.51.100.", "203.0.113.")) for line in lines
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_carving(seed):
    out = run("carving", "evidence.png", seed)
    blob, a = out.files["evidence.png"], out.answers
    assert blob.startswith(b"\x89PNG")
    assert f"note: {a['note-flag']}".encode() in blob
    offset = int(a["gzip-offset"])
    assert blob[offset : offset + 2] == b"\x1f\x8b"
    text = gzip.decompress(blob[offset:]).decode()
    assert blob.count(b"\x1f\x8b") == 1  # no accidental second "archive"
    assert a["carved-flag"] in text
    assert a["carved-flag"].encode() not in blob  # only visible after carving


def routes_of(out):
    services = json.loads(next(iter(out.files.values())))["services"]
    return services


@pytest.mark.parametrize("seed", SEEDS)
def test_web_login_target(seed):
    out = run("web-login-target", ".cyberlab-target.json", seed)
    (svc,) = routes_of(out)
    words = run("wordlist", "passwords.txt", seed).files["passwords.txt"].decode().split()
    a = out.answers
    assert f"our {a['admin-user']} at" in svc["routes"]["/"]["body"]
    logins = [p for p in svc["routes"] if p.startswith("/login?")]
    assert len(logins) == 1 and f"user={a['admin-user']}&" in logins[0]
    assert logins[0].rsplit("password=", 1)[1] in words
    assert a["login-flag"] in svc["routes"][logins[0]]["body"]
    key = svc["routes"]["/static/app.js"]["body"].split("'")[1]
    assert a["key-flag"] in svc["routes"][f"/api/fleet?key={key}"]["body"]
    assert svc["fallbacks"]["/login"]["status"] == 401


@pytest.mark.parametrize("seed", SEEDS)
def test_chain_target(seed):
    out = run("chain-target", ".cyberlab-target.json", seed)
    ssh, web = routes_of(out)
    a = out.answers
    assert web["port"] == int(a["web-port"]) and web["port"] > 1024
    backup = web["routes"]["/"]["body"].split("moved to ")[1].split(" ")[0]
    creds = dict(line.split("=", 1) for line in web["routes"][backup]["body"].splitlines()[1:])
    assert creds["service_user"] == a["service-user"]
    token_route = f"/api/token?user={creds['service_user']}&password={creds['service_password']}"
    token = json.loads(web["routes"][token_route]["body"])["token"]
    assert a["chain-flag"] in web["routes"][f"/api/admin/flag?token={token}"]["body"]


def test_archive_never_carries_special_bits():
    with tarfile.open(fileobj=io.BytesIO(_archive({"a": b"x"}, {"a": 0o4755}))) as tar:
        (m,) = tar.getmembers()
    assert m.mode == 0o755
