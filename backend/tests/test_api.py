from .conftest import REPO, auth

EXAMPLE = (REPO / "examples" / "manifests" / "blue-team-lab.universe.yaml").read_text()
YAML = {"Content-Type": "application/yaml"}


def test_health_and_security_headers(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-request-id"]
    assert client.get("/readyz").json()["status"] == "ready"


def test_request_id_sanitized(client):
    r = client.get("/healthz", headers={"X-Request-ID": "abc\r\nx: y"})
    assert "\n" not in r.headers["x-request-id"] and r.headers["x-request-id"] != "abc"


def test_registry_endpoints_are_public(client):
    cats = client.get("/v1/registry/categories").json()
    assert len(cats) == 10 and all("toolPackCount" in c for c in cats)
    packs = client.get("/v1/registry/tool-packs", params={"category": "cryptography"}).json()
    assert [p["id"] for p in packs] == ["crypto"]
    assert client.get("/v1/registry/tool-packs/crypto").json()["compatibleBases"]
    assert client.get("/v1/registry/tool-packs/nope").status_code == 404
    assert len(client.get("/v1/registry/base-images").json()) == 3


def test_validate_requires_auth(client):
    assert client.post("/v1/manifests/validate", content=EXAMPLE, headers=YAML).status_code == 401


def test_validate_manifest(client):
    r = client.post("/v1/manifests/validate", content=EXAMPLE, headers=YAML | auth())
    body = r.json()
    assert r.status_code == 200 and body["valid"] and body["total_cpu"] == 3
    r = client.post("/v1/manifests/validate", content="kind: x", headers=YAML | auth())
    assert r.json()["valid"] is False


def test_validate_rejects_bad_content_type_and_size(client, settings):
    r = client.post(
        "/v1/manifests/validate",
        content=EXAMPLE,
        headers={"Content-Type": "application/xml"} | auth(),
    )
    assert r.status_code == 415
    big = "#" * (settings.max_request_bytes + 1)
    r = client.post("/v1/manifests/validate", content=big, headers=YAML | auth())
    assert r.status_code == 413


def test_workspace_lifecycle_and_portability(client):
    r = client.post("/v1/workspaces", json={"name": "lab-one"}, headers=auth())
    assert r.status_code == 201
    ws = r.json()["id"]
    assert (
        client.post("/v1/workspaces", json={"name": "lab-one"}, headers=auth()).status_code == 409
    )
    assert (
        client.post("/v1/workspaces", json={"name": "Bad Name!"}, headers=auth()).status_code == 422
    )

    assert client.get(f"/v1/workspaces/{ws}/manifest", headers=auth()).status_code == 404
    r = client.put(f"/v1/workspaces/{ws}/manifest", content=EXAMPLE, headers=YAML | auth())
    assert r.status_code == 200 and r.json()["revision"] == 1
    r = client.put(f"/v1/workspaces/{ws}/manifest", content=EXAMPLE, headers=YAML | auth())
    assert r.json()["revision"] == 2
    bad = EXAMPLE.replace("debian-12", "plan9")
    r = client.put(f"/v1/workspaces/{ws}/manifest", content=bad, headers=YAML | auth())
    assert r.status_code == 422

    exported = client.get(f"/v1/workspaces/{ws}/manifest", headers=auth())
    assert exported.headers["content-type"].startswith("application/yaml")
    assert "attachment" in exported.headers["content-disposition"]
    # Export -> import into a second workspace reproduces the same manifest.
    ws2 = client.post("/v1/workspaces", json={"name": "lab-two"}, headers=auth()).json()["id"]
    client.put(f"/v1/workspaces/{ws2}/manifest", content=exported.text, headers=YAML | auth())
    a = client.get(f"/v1/workspaces/{ws}/manifest?format=json", headers=auth()).json()
    b = client.get(f"/v1/workspaces/{ws2}/manifest?format=json", headers=auth()).json()
    assert a == b
    assert client.get(f"/v1/workspaces/{ws}/manifest?revision=1", headers=auth()).status_code == 200

    listed = client.get("/v1/workspaces", headers=auth()).json()
    assert {w["name"] for w in listed} == {"lab-one", "lab-two"}
    assert client.delete(f"/v1/workspaces/{ws}", headers=auth()).status_code == 204
    assert client.get(f"/v1/workspaces/{ws}", headers=auth()).status_code == 404


def test_workspaces_isolated_between_users(client):
    ws = client.post("/v1/workspaces", json={"name": "mine"}, headers=auth("alice")).json()["id"]
    for method, url in [
        ("get", f"/v1/workspaces/{ws}"),
        ("delete", f"/v1/workspaces/{ws}"),
        ("get", f"/v1/workspaces/{ws}/manifest"),
    ]:
        assert getattr(client, method)(url, headers=auth("mallory")).status_code == 404
    r = client.put(f"/v1/workspaces/{ws}/manifest", content=EXAMPLE, headers=YAML | auth("mallory"))
    assert r.status_code == 404
    assert client.get("/v1/workspaces", headers=auth("mallory")).json() == []


def test_audit_log(client):
    ws = client.post("/v1/workspaces", json={"name": "audited"}, headers=auth("bob")).json()["id"]
    client.put(f"/v1/workspaces/{ws}/manifest", content=EXAMPLE, headers=YAML | auth("bob"))
    events = client.get("/v1/audit", headers=auth("bob")).json()
    assert [e["action"] for e in events] == ["manifest.import", "workspace.create"]
    assert all(e["requestId"] for e in events)
    assert client.get("/v1/audit", headers=auth("eve")).json() == []
