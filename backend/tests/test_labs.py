import io
import re
import tarfile

import pytest
from fastapi.testclient import TestClient

from cyberlab.config import Settings
from cyberlab.labs import Lab, LabError, answer_hash, check_answer, load_labs, new_run
from cyberlab.labs.generators import GENERATORS
from cyberlab.main import create_app
from cyberlab.registry import load_registry

from .conftest import REPO, auth
from .test_environments_api import FakeProvider


class LabProvider(FakeProvider):
    """Remembers the lab files it was asked to unpack, per machine."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.files: dict[str, dict[str, bytes]] = {}

    def extract_files(self, ref, archive):
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for m in tar.getmembers():
                assert not m.name.startswith("/") and ".." not in m.name.split("/")
                if m.isfile():
                    self.files.setdefault(ref, {})[m.name] = tar.extractfile(m).read()


@pytest.fixture
def provider():
    return LabProvider()


@pytest.fixture
def client(settings, provider):
    app = create_app(settings)
    app.state.provider = provider
    with TestClient(app) as c:
        yield c


def start(client, lab="linux-first-steps", user="user-a"):
    r = client.post(f"/v1/labs/{lab}/start", headers=auth(user))
    assert r.status_code == 201, r.text
    return r.json()


def answer(client, ws, task, value, user="user-a"):
    r = client.post(
        f"/v1/workspaces/{ws}/lab/answer", json={"task": task, "answer": value}, headers=auth(user)
    )
    assert r.status_code == 200, r.text
    return r.json()


def flag_in(data: bytes) -> str:
    return re.search(rb"CL\{[0-9a-f]{16}\}", data).group().decode()


# ---------------------------------------------------------------- the catalog


def test_every_lab_in_the_registry_loads_and_passes_policy():
    s = Settings(registry_dir=REPO / "registry")
    labs = load_labs(s.registry_dir, load_registry(s.registry_dir), s.quotas)
    assert {"linux-first-steps", "log-detective"} <= set(labs)
    for lab in labs.values():
        run = new_run(lab)
        assert set(run.answers) == {t.id for t in lab.tasks}
        assert all(run.answers.values())


def test_catalog_is_public_and_hides_where_answers_are(client):
    labs = client.get("/v1/labs").json()
    lab = next(lab for lab in labs if lab["id"] == "linux-first-steps")
    assert lab["level"] == "beginner" and lab["machines"][0]["baseImage"] == "ubuntu-24-04"
    assert [t["id"] for t in lab["tasks"]][:2] == ["read-a-file", "hidden-file"]
    text = client.get("/v1/labs/linux-first-steps").text
    assert (
        "setup" not in text and "flagTask" not in text and "CL{" not in text.replace("CL{...}", "")
    )
    assert client.get("/v1/labs/nope").status_code == 404


def _lab(**over):
    base = {
        "id": "t",
        "title": "T",
        "level": "beginner",
        "minutes": 10,
        "topic": "linux",
        "summary": "s",
        "spec": {
            "environments": [
                {
                    "name": "box",
                    "baseImage": "ubuntu-24-04",
                    "resources": {"cpu": 0.5, "memoryMiB": 512, "storageGiB": 2},
                    "network": {"mode": "isolated"},
                }
            ]
        },
        "setup": [{"environment": "box", "path": "a.txt", "content": "{{flag:one}}"}],
        "tasks": [{"id": "one", "title": "One", "text": "x"}],
    }
    return base | over


def _check(tmp_path, data):
    import yaml

    (tmp_path / "labs").mkdir(exist_ok=True)
    (tmp_path / "labs" / f"{data['id']}.yaml").write_text(yaml.safe_dump(data))
    reg = load_registry(REPO / "registry")
    return load_labs(tmp_path, reg, Settings().quotas)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"setup": [{"environment": "box", "path": "../etc/passwd", "content": "x"}]}, "relative"),
        ({"setup": [{"environment": "box", "path": "/etc/passwd", "content": "x"}]}, "relative"),
        (
            {"setup": [{"environment": "nope", "path": "a", "content": "{{flag:one}}"}]},
            "unknown machine",
        ),
        (
            {"setup": [{"environment": "box", "path": "a", "content": "{{flag:two}}"}]},
            "unknown task",
        ),
        ({"setup": []}, "never placed"),
        (
            {"setup": [{"environment": "box", "path": "a", "generator": "rm-rf"}]},
            "unknown generator",
        ),
        ({"setup": [{"environment": "box", "path": "a", "generator": "maze"}]}, "needs a flagTask"),
        (
            {"tasks": [{"id": "one", "title": "o", "text": "x", "answer": "attacker-ip"}]},
            "no generator",
        ),
        ({"id": "u"}, "must match the file name"),
    ],
)
def test_broken_labs_are_refused(tmp_path, change, message):
    data = _lab(**change)
    if data["id"] == "u":
        import yaml

        (tmp_path / "labs").mkdir()
        (tmp_path / "labs" / "t.yaml").write_text(yaml.safe_dump(data))
        with pytest.raises(LabError, match=message):
            load_labs(tmp_path, load_registry(REPO / "registry"), Settings().quotas)
        return
    with pytest.raises(LabError, match=message):
        _check(tmp_path, data)


def test_labs_may_not_reach_the_internet(tmp_path):
    data = _lab()
    data["spec"]["environments"][0]["network"] = {"mode": "internet"}
    with pytest.raises(LabError, match="isolated' or 'lab"):
        _check(tmp_path, data)


def test_a_valid_custom_lab_loads(tmp_path):
    assert "t" in _check(tmp_path, _lab())


# ---------------------------------------------------------------- answers


def test_answers_are_fresh_each_run_and_only_hashes_compare():
    lab = Lab.model_validate(_lab())
    a, b = new_run(lab).answers["one"], new_run(lab).answers["one"]
    assert a != b and re.fullmatch(r"CL\{[0-9a-f]{16}\}", a)
    h = answer_hash("run-1", a)
    assert check_answer("run-1", f"  {a.upper()} ", h)  # spaces and case don't matter
    assert not check_answer("run-2", a, h)  # salted with the run
    assert not check_answer("run-1", b, h)


def test_auth_log_answers_match_the_log():
    import random

    out = GENERATORS["auth-log"].run(random.Random(7), "auth.log", None)  # noqa: S311
    log = out.files["auth.log"].decode().splitlines()
    ip, user, count = (out.answers[k] for k in ("attacker-ip", "attacker-user", "failed-count"))
    fails = [line for line in log if "Failed password" in line]
    by_ip: dict[str, int] = {}
    for line in fails:
        addr = line.split()[-4]
        by_ip[addr] = by_ip.get(addr, 0) + 1
    assert max(by_ip, key=by_ip.get) == ip and by_ip[ip] == int(count)
    (ok,) = [line for line in log if "Accepted password" in line and f"from {ip} " in line]
    assert f"for {user} from" in ok
    # only documentation address ranges, never a real host
    assert all(a.startswith(("192.0.2.", "198.51.100.", "203.0.113.")) for a in by_ip)


def test_maze_hides_exactly_one_treasure():
    import random

    out = GENERATORS["maze"].run(random.Random(1), "maze", "CL{0123456789abcdef}")  # noqa: S311
    treasures = [p for p in out.files if p.endswith("treasure.txt")]
    assert len(treasures) == 1 and treasures[0].count("/") == 5
    assert b"CL{0123456789abcdef}" in out.files[treasures[0]]
    assert len(out.files) > 100


# ---------------------------------------------------------------- the API


def test_one_click_start_places_files_and_checks_answers(client, provider):
    out = start(client)
    ws = out["workspaceId"]
    assert out["setupError"] is None and out["progress"] == {"solved": 0, "total": 4}
    (ref,) = provider.files
    files = provider.files[ref]
    assert "welcome.txt" in files and ".secret-note.txt" in files and "logs/app.log" in files
    (req,) = provider.requests
    assert req.environment.network.mode == "isolated"

    first = flag_in(files["welcome.txt"])
    assert answer(client, ws, "read-a-file", "CL{wrong}")["correct"] is False
    res = answer(client, ws, "read-a-file", first)
    assert res["correct"] is True and "read-a-file" in res["solved"]
    assert res["progress"] == {"solved": 1, "total": 4}
    # a flag from another task doesn't count
    assert answer(client, ws, "hidden-file", first)["correct"] is False
    assert answer(client, ws, "hidden-file", flag_in(files[".secret-note.txt"]))["correct"]
    assert client.get(f"/v1/workspaces/{ws}/lab", headers=auth()).json()["progress"]["solved"] == 2

    events = client.get("/v1/audit", headers=auth()).json()
    lab_events = [e for e in events if e["action"].startswith("lab.")]
    assert {e["action"] for e in lab_events} == {"lab.start", "lab.answer"}
    assert first not in str(events) and "CL{wrong}" not in str(events)


def test_each_start_is_a_new_workspace_with_new_flags(client, provider):
    a, b = start(client), start(client)
    assert a["workspaceId"] != b["workspaceId"]
    names = {w["name"] for w in client.get("/v1/workspaces", headers=auth()).json()}
    assert {"linux-first-steps", "linux-first-steps-2"} <= names
    fa, fb = (flag_in(f["welcome.txt"]) for f in provider.files.values())
    assert fa != fb
    assert answer(client, b["workspaceId"], "read-a-file", fa)["correct"] is False


def test_log_detective_answers(client, provider):
    out = start(client, "log-detective")
    (files,) = provider.files.values()
    log = files["auth.log"].decode()
    ws = out["workspaceId"]
    fails: dict[str, int] = {}
    for line in log.splitlines():
        if "Failed password" in line:
            fails[line.split()[-4]] = fails.get(line.split()[-4], 0) + 1
    ip = max(fails, key=fails.get)
    assert answer(client, ws, "attacker-ip", ip)["correct"]
    assert answer(client, ws, "failed-count", str(fails[ip]))["correct"]


def test_labs_are_private_to_their_owner(client):
    ws = start(client)["workspaceId"]
    assert client.get(f"/v1/workspaces/{ws}/lab", headers=auth("user-b")).status_code == 404
    r = client.post(
        f"/v1/workspaces/{ws}/lab/answer",
        json={"task": "read-a-file", "answer": "x"},
        headers=auth("user-b"),
    )
    assert r.status_code == 404
    assert client.post("/v1/labs/linux-first-steps/start").status_code == 401


def test_unknown_task_and_workspace_without_lab(client):
    ws = start(client)["workspaceId"]
    r = client.post(
        f"/v1/workspaces/{ws}/lab/answer", json={"task": "nope", "answer": "x"}, headers=auth()
    )
    assert r.status_code == 404
    plain = client.post("/v1/workspaces", json={"name": "plain"}, headers=auth()).json()["id"]
    assert client.get(f"/v1/workspaces/{plain}/lab", headers=auth()).status_code == 404


def test_no_provider_means_nothing_is_created(settings):
    app = create_app(settings)
    app.state.provider = FakeProvider(connected=False)
    with TestClient(app) as c:
        r = c.post("/v1/labs/linux-first-steps/start", headers=auth())
        assert r.status_code == 503
        assert c.get("/v1/workspaces", headers=auth()).json() == []


def test_quota_refusal_leaves_no_empty_workspace(settings):
    tight = settings.quotas.model_copy(update={"max_total_memory_mib": 256})
    app = create_app(settings.model_copy(update={"quotas": tight}))
    app.state.provider = LabProvider()
    with TestClient(app) as c:
        r = c.post("/v1/labs/linux-first-steps/start", headers=auth())
        assert r.status_code in (409, 422), r.text
        assert "memory" in r.text
        assert c.get("/v1/workspaces", headers=auth()).json() == []


def test_machine_that_fails_to_start_is_reported(settings):
    app = create_app(settings)
    app.state.provider = LabProvider(fail_provision=True)
    with TestClient(app) as c:
        out = c.post("/v1/labs/linux-first-steps/start", headers=auth()).json()
        assert "did not start" in out["setupError"] and "image not found" in out["setupError"]


def test_deleting_the_workspace_removes_the_lab_run(client, provider):
    ws = start(client)["workspaceId"]
    for e in client.get(f"/v1/workspaces/{ws}/environments", headers=auth()).json():
        client.delete(f"/v1/workspaces/{ws}/environments/{e['id']}", headers=auth())
    assert client.delete(f"/v1/workspaces/{ws}", headers=auth()).status_code == 204
    assert client.get(f"/v1/workspaces/{ws}/lab", headers=auth()).status_code == 404


def test_progress_shows_the_best_run_per_lab(client, provider):
    assert client.get("/v1/labs/progress", headers=auth()).json()["labs"] == {}
    first = start(client)
    second = start(client)
    files = list(provider.files.values())
    answer(client, second["workspaceId"], "read-a-file", flag_in(files[1]["welcome.txt"]))
    p = client.get("/v1/labs/progress", headers=auth()).json()
    entry = p["labs"]["linux-first-steps"]
    assert entry["workspaceId"] == second["workspaceId"] and entry["solved"] == 1
    assert entry["completed"] is False and p["completed"] == 0
    assert p["available"] >= 2
    # finish the first run completely: it becomes the best one, and the lab counts as done
    f = files[0]
    maze = next(v for k, v in f.items() if k.endswith("treasure.txt"))
    for task, data in [
        ("read-a-file", f["welcome.txt"]),
        ("hidden-file", f[".secret-note.txt"]),
        ("find-a-file", maze),
        ("search-a-file", f["logs/app.log"]),
    ]:
        assert answer(client, first["workspaceId"], task, flag_in(data))["correct"]
    p = client.get("/v1/labs/progress", headers=auth()).json()
    assert p["labs"]["linux-first-steps"]["completed"] is True and p["completed"] == 1
    # progress is private
    assert client.get("/v1/labs/progress", headers=auth("user-b")).json()["labs"] == {}
    assert client.get("/v1/labs/progress").status_code == 401
