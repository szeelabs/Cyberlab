"""Classroom mode: a teacher, a join code, and students each with their own lab."""

import re

import pytest
from fastapi.testclient import TestClient

from cyberlab.api.classes import CODE_ALPHABET, new_code
from cyberlab.main import create_app

from .conftest import auth
from .test_labs import LabProvider, flag_in

TEACHER, ALICE, BOB = "teacher", "alice", "bob"


@pytest.fixture
def client(settings):
    app = create_app(settings)
    app.state.provider = LabProvider()
    with TestClient(app) as c:
        yield c, app.state.provider


def make_class(c, lab="linux-first-steps"):
    r = c.post("/v1/classes", json={"name": "Security 101", "labId": lab}, headers=auth(TEACHER))
    assert r.status_code == 201, r.text
    return r.json()


def test_codes_are_readable_and_random():
    codes = {new_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", code)
        assert set(code.replace("-", "")) <= set(CODE_ALPHABET)
        assert not set(code) & set("01IOL")  # no look-alikes


def test_whole_class_flow(client):
    c, provider = client
    cls = make_class(c)
    assert cls["labTitle"] == "First steps in Linux" and cls["open"] and cls["members"] == 0

    # Students join with the code (any case, with or without the dash) and start their copy.
    code = cls["joinCode"]
    assert (
        c.post("/v1/classes/join", json={"code": code.lower()}, headers=auth(ALICE)).status_code
        == 200
    )
    assert (
        c.post(
            "/v1/classes/join", json={"code": code.replace("-", "")}, headers=auth(BOB)
        ).status_code
        == 200
    )
    assert (
        c.post("/v1/classes/join", json={"code": code}, headers=auth(ALICE)).status_code == 200
    )  # idempotent

    a = c.post(f"/v1/classes/{cls['id']}/start", headers=auth(ALICE)).json()
    b = c.post(f"/v1/classes/{cls['id']}/start", headers=auth(BOB)).json()
    assert a["workspaceId"] != b["workspaceId"]
    again = c.post(f"/v1/classes/{cls['id']}/start", headers=auth(ALICE)).json()
    assert again == {"workspaceId": a["workspaceId"], "existing": True}

    # Each student has their own answers: Bob can't use Alice's flag.
    files = list(provider.files.values())
    alice_flag = flag_in(files[0]["welcome.txt"])
    r = c.post(
        f"/v1/workspaces/{a['workspaceId']}/lab/answer",
        json={"task": "read-a-file", "answer": alice_flag},
        headers=auth(ALICE),
    )
    assert r.json()["correct"] is True
    r = c.post(
        f"/v1/workspaces/{b['workspaceId']}/lab/answer",
        json={"task": "read-a-file", "answer": alice_flag},
        headers=auth(BOB),
    )
    assert r.json()["correct"] is False

    # The teacher sees progress, not files.
    roster = c.get(f"/v1/classes/{cls['id']}", headers=auth(TEACHER)).json()
    by = {r["student"]: r["progress"] for r in roster["roster"]}
    assert by[ALICE]["solved"] == 1 and by[BOB]["solved"] == 0 and by[ALICE]["total"] == 4
    assert by[ALICE]["machines"][0]["state"] == "running"
    assert roster["members"] == 2 and len(roster["tasks"]) == 4
    # ...and can't open the students' workspaces.
    assert c.get(f"/v1/workspaces/{a['workspaceId']}/lab", headers=auth(TEACHER)).status_code == 404
    assert (
        c.get(f"/v1/workspaces/{a['workspaceId']}/environments", headers=auth(TEACHER)).status_code
        == 404
    )

    # Students see their classes; the teacher sees the one they teach.
    mine = c.get("/v1/classes", headers=auth(ALICE)).json()
    assert mine["teaching"] == [] and mine["joined"][0]["progress"]["solved"] == 1
    assert c.get("/v1/classes", headers=auth(TEACHER)).json()["teaching"][0]["members"] == 2

    # Reset: Alice's machines and files go; she can start a fresh copy with new answers.
    member = next(r["memberId"] for r in roster["roster"] if r["student"] == ALICE)
    assert c.post(
        f"/v1/classes/{cls['id']}/members/{member}/reset", headers=auth(TEACHER)
    ).json() == {"reset": True}
    assert c.get(f"/v1/workspaces/{a['workspaceId']}", headers=auth(ALICE)).status_code == 404
    roster = c.get(f"/v1/classes/{cls['id']}", headers=auth(TEACHER)).json()
    assert (
        next(r for r in roster["roster"] if r["student"] == ALICE)["progress"]["started"] is False
    )
    fresh = c.post(f"/v1/classes/{cls['id']}/start", headers=auth(ALICE)).json()
    assert fresh["existing"] is False and fresh["workspaceId"] != a["workspaceId"]

    actions = {e["action"] for e in c.get("/v1/audit", headers=auth(TEACHER)).json()}
    assert {"class.create", "class.reset"} <= actions


def test_only_the_teacher_manages_the_class(client):
    c, _ = client
    cls = make_class(c)
    c.post("/v1/classes/join", json={"code": cls["joinCode"]}, headers=auth(ALICE))
    roster = c.get(f"/v1/classes/{cls['id']}", headers=auth(TEACHER)).json()
    member = roster["roster"][0]["memberId"]
    for method, path in [
        ("get", f"/v1/classes/{cls['id']}"),
        ("post", f"/v1/classes/{cls['id']}/members/{member}/reset"),
    ]:
        assert getattr(c, method)(path, headers=auth(ALICE)).status_code == 404
    assert (
        c.patch(f"/v1/classes/{cls['id']}", json={"open": False}, headers=auth(ALICE)).status_code
        == 404
    )
    # someone who never joined can't start the class lab
    assert c.post(f"/v1/classes/{cls['id']}/start", headers=auth(BOB)).status_code == 404


def test_closed_class_and_bad_codes(client):
    c, _ = client
    cls = make_class(c)
    c.post("/v1/classes/join", json={"code": cls["joinCode"]}, headers=auth(ALICE))
    assert (
        c.patch(f"/v1/classes/{cls['id']}", json={"open": False}, headers=auth(TEACHER)).json()[
            "open"
        ]
        is False
    )
    r = c.post("/v1/classes/join", json={"code": cls["joinCode"]}, headers=auth(BOB))
    assert r.status_code == 409 and "closed" in r.text
    # already a member: still fine after closing
    assert (
        c.post("/v1/classes/join", json={"code": cls["joinCode"]}, headers=auth(ALICE)).status_code
        == 200
    )
    assert (
        c.post("/v1/classes/join", json={"code": "ZZZZ-ZZZZ"}, headers=auth(BOB)).status_code == 404
    )
    r = c.post("/v1/classes/join", json={"code": cls["joinCode"]}, headers=auth(TEACHER))
    assert r.status_code == 409  # teachers don't join their own class
    assert (
        c.post(
            "/v1/classes", json={"name": "x", "labId": "nope"}, headers=auth(TEACHER)
        ).status_code
        == 404
    )


def test_deleting_a_student_workspace_keeps_the_membership(client):
    c, _ = client
    cls = make_class(c)
    c.post("/v1/classes/join", json={"code": cls["joinCode"]}, headers=auth(ALICE))
    ws = c.post(f"/v1/classes/{cls['id']}/start", headers=auth(ALICE)).json()["workspaceId"]
    for e in c.get(f"/v1/workspaces/{ws}/environments", headers=auth(ALICE)).json():
        c.delete(f"/v1/workspaces/{ws}/environments/{e['id']}", headers=auth(ALICE))
    assert c.delete(f"/v1/workspaces/{ws}", headers=auth(ALICE)).status_code == 204
    joined = c.get("/v1/classes", headers=auth(ALICE)).json()["joined"]
    assert joined[0]["workspaceId"] is None and joined[0]["progress"]["started"] is False
