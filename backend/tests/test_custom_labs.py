"""Labs written by users: import, share, download, start, and the same safety checks."""

import pytest
import yaml
from fastapi.testclient import TestClient

from cyberlab.main import create_app

from .conftest import REPO, auth
from .test_labs import LabProvider, flag_in

YAML_CT = {"Content-Type": "application/yaml"}

MY_LAB = {
    "id": "my-first-lab",
    "title": "Find the note",
    "level": "beginner",
    "minutes": 10,
    "topic": "linux",
    "summary": "A lab I wrote.",
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
    "setup": [{"environment": "box", "path": "note.txt", "content": "flag: {{flag:read}}"}],
    "tasks": [{"id": "read", "title": "Read it", "text": "`cat note.txt`"}],
}


@pytest.fixture
def client(settings):
    app = create_app(settings)
    app.state.provider = LabProvider()
    with TestClient(app) as c:
        yield c, app.state.provider


def imp(c, data=MY_LAB, user="user-a"):
    return c.post("/v1/labs/import", content=yaml.safe_dump(data), headers=YAML_CT | auth(user))


def test_import_private_then_share(client):
    c, provider = client
    r = imp(c)
    assert r.status_code == 201, r.text
    lab = r.json()
    assert lab["id"].startswith("c-") and lab["title"] == "Find the note" and lab["shared"] is False
    # private: only its author sees and starts it
    assert lab["id"] not in [x["id"] for x in c.get("/v1/labs").json()]
    assert [x["id"] for x in c.get("/v1/labs/mine", headers=auth()).json()] == [lab["id"]]
    assert c.post(f"/v1/labs/{lab['id']}/start", headers=auth("user-b")).status_code == 404
    out = c.post(f"/v1/labs/{lab['id']}/start", headers=auth()).json()
    (files,) = provider.files.values()
    r = c.post(
        f"/v1/workspaces/{out['workspaceId']}/lab/answer",
        json={"task": "read", "answer": flag_in(files["note.txt"])},
        headers=auth(),
    )
    assert r.json()["correct"] is True
    # shared: in everyone's catalog, marked as community, downloadable, startable
    assert c.patch(f"/v1/labs/{lab['id']}", json={"shared": True}, headers=auth()).json()["shared"]
    listed = {x["id"]: x for x in c.get("/v1/labs").json()}
    assert listed[lab["id"]]["source"] == "community"
    assert listed["linux-first-steps"]["source"] == "built-in"
    text = c.get(f"/v1/labs/{lab['id']}/file").text
    assert yaml.safe_load(text)["title"] == "Find the note"
    assert c.post(f"/v1/labs/{lab['id']}/start", headers=auth("user-b")).status_code == 201
    # only the author can change or delete it
    assert (
        c.patch(f"/v1/labs/{lab['id']}", json={"shared": False}, headers=auth("user-b")).status_code
        == 404
    )
    assert c.delete(f"/v1/labs/{lab['id']}", headers=auth("user-b")).status_code == 404
    assert c.delete(f"/v1/labs/{lab['id']}", headers=auth()).status_code == 204
    assert lab["id"] not in [x["id"] for x in c.get("/v1/labs").json()]


def test_built_in_lab_files_can_be_downloaded_and_reimported(client):
    c, _ = client
    text = c.get("/v1/labs/log-detective/file").text
    assert text == (REPO / "registry" / "labs" / "log-detective.yaml").read_text()
    r = c.post("/v1/labs/import", content=text, headers=YAML_CT | auth())
    assert r.status_code == 201 and r.json()["id"] != "log-detective"  # its own id


def bad(**over):
    data = yaml.safe_load(yaml.safe_dump(MY_LAB))
    for k, v in over.items():
        data[k] = v
    return data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            bad(
                spec={
                    "environments": [
                        {**MY_LAB["spec"]["environments"][0], "network": {"mode": "internet"}}
                    ]
                }
            ),
            "isolated' or 'lab",
        ),
        (
            bad(setup=[{"environment": "box", "path": "../../etc/x", "content": "{{flag:read}}"}]),
            "relative",
        ),
        (
            bad(setup=[{"environment": "box", "path": "a", "generator": "os-system"}]),
            "unknown generator",
        ),
        (
            bad(
                spec={
                    "environments": [
                        {**MY_LAB["spec"]["environments"][0], "toolPacks": ["malware-analysis"]}
                    ]
                }
            ),
            "isolation",
        ),
        (bad(title="x" * 500), "title"),
        ("not: [valid", "yaml"),
    ],
)
def test_unsafe_or_broken_labs_are_refused(client, data, message):
    c, _ = client
    body = data if isinstance(data, str) else yaml.safe_dump(data)
    r = c.post("/v1/labs/import", content=body, headers=YAML_CT | auth())
    assert r.status_code == 422, r.text
    assert message.lower() in r.text.lower()
    assert c.get("/v1/labs/mine", headers=auth()).json() == []


def test_import_needs_sign_in_and_size_limit(client):
    c, _ = client
    assert (
        c.post("/v1/labs/import", content=yaml.safe_dump(MY_LAB), headers=YAML_CT).status_code
        == 401
    )
    huge = "# " + "x" * (300 * 1024)
    assert c.post("/v1/labs/import", content=huge, headers=YAML_CT | auth()).status_code == 413


def test_classes_only_use_shared_or_built_in_labs(client):
    c, _ = client
    lab = imp(c, user="teacher").json()
    r = c.post("/v1/classes", json={"name": "x", "labId": lab["id"]}, headers=auth("teacher"))
    assert r.status_code == 409 and "share this lab first" in r.text
    c.patch(f"/v1/labs/{lab['id']}", json={"shared": True}, headers=auth("teacher"))
    r = c.post("/v1/classes", json={"name": "x", "labId": lab["id"]}, headers=auth("teacher"))
    assert r.status_code == 201
