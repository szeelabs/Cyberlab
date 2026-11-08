import pytest
from fastapi.testclient import TestClient

from cyberlab.execution.docker import DockerAdapter, FileOpError, RunResult
from cyberlab.execution.paths import UnsafePath, workspace_path
from cyberlab.main import create_app

from .conftest import auth
from .test_environments_api import MINIMAL, YAML, FakeProvider


# ---------------------------------------------------------------- path rules
@pytest.mark.parametrize(
    "rel,expected",
    [
        ("notes.txt", "/workspace/notes.txt"),
        ("a/b/c.pcap", "/workspace/a/b/c.pcap"),
        ("./a//b/./c", "/workspace/a/b/c"),
        ("dir/", "/workspace/dir"),
        ("file with spaces & $(odd).txt", "/workspace/file with spaces & $(odd).txt"),
    ],
)
def test_valid_paths(rel, expected):
    assert workspace_path(rel) == expected


@pytest.mark.parametrize(
    "rel",
    [
        "../etc/passwd",
        "a/../../x",
        "/etc/passwd",
        "-rf",
        "a/-rf",
        "a\\b",
        "a\nb",
        "a\x00b",
        "",
        ".",
        "x" * 2000,
    ],
)
def test_unsafe_paths(rel):
    with pytest.raises(UnsafePath):
        workspace_path(rel)


def test_root_only_when_allowed():
    assert workspace_path("", allow_root=True) == "/workspace"
    with pytest.raises(UnsafePath):
        workspace_path("")


# ---------------------------------------------------------------- adapter commands
class Recorder:
    def __init__(self, responses=None):
        self.calls, self.inputs, self.responses = [], [], responses or {}

    def __call__(self, args, timeout, input=None, binary=False):
        self.calls.append(args)
        self.inputs.append(input)
        for key, res in self.responses.items():
            if key in " ".join(args):
                return res
        return RunResult(0, "", "", data=b"")


def test_commands_never_use_a_shell_with_the_path():
    rec = Recorder()
    a = DockerAdapter(rec)
    a.write_file("abc123", "/workspace/x; rm -rf ~/y.txt", b"\x00\x01")
    a.delete_path("abc123", "/workspace/d", recursive=True)
    mkdir, write, rm = rec.calls
    # the odd parent folder name is passed as a plain argument, never executed
    assert mkdir[-4:] == ["mkdir", "-p", "--", "/workspace/x; rm -rf ~"]
    assert write[-5:] == ["sh", "-c", 'cat > "$1"', "sh", "/workspace/x; rm -rf ~/y.txt"]
    assert "--interactive" in write and rec.inputs[1] == b"\x00\x01"
    assert rm[-4:] == ["rm", "-r", "--", "/workspace/d"]


def test_listing_handles_odd_names():
    data = b"d\t4096\t1.5\tsub\0f\t3\t2.0\tname\twith\ttabs\0l\t9\t3.0\tlink\0"
    a = DockerAdapter(Recorder({"find": RunResult(0, "", "", data=data)}))
    entries = a.list_files("abc123", "/workspace")
    assert entries[0] == {"name": "sub", "type": "directory", "size": 4096, "modified": 1.5}
    assert {e["name"] for e in entries} == {"sub", "name\twith\ttabs", "link"}


@pytest.mark.parametrize(
    "stderr,kind",
    [
        ("find: '/workspace/x': No such file or directory", "not_found"),
        ("Error response from daemon: container abc is not running", "not_running"),
        ("cat: /workspace/d: Is a directory", "is_directory"),
        ("sh: 1: cannot create /workspace/a: Permission denied", "forbidden"),
        ("cat: write error: No space left on device", "no_space"),
    ],
)
def test_errors_are_classified(stderr, kind):
    a = DockerAdapter(Recorder({"find": RunResult(1, "", stderr)}))
    with pytest.raises(FileOpError) as exc:
        a.list_files("abc123", "/workspace/x")
    assert exc.value.kind == kind


def test_large_files_refused_before_reading():
    rec = Recorder({"stat": RunResult(0, "regular file\t999999", "")})
    with pytest.raises(FileOpError) as exc:
        DockerAdapter(rec).read_file("abc123", "/workspace/big.bin", max_bytes=1000)
    assert exc.value.kind == "too_large"
    assert not [c for c in rec.calls if "cat" in c]


# ---------------------------------------------------------------- API (in-memory files)
class FileProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.files: dict[str, bytes] = {}

    def list_files(self, ref, path):
        if path != "/workspace" and not any(f.startswith(path + "/") for f in self.files):
            if path in self.files:
                raise FileOpError("not_a_directory", "not a directory")
            raise FileOpError("not_found", "does not exist")
        names = {f[len(path) + 1 :].split("/")[0] for f in self.files if f.startswith(path + "/")}
        return [
            {
                "name": n,
                "type": "directory" if f"{path}/{n}" not in self.files else "file",
                "size": len(self.files.get(f"{path}/{n}", b"")),
                "modified": 0.0,
            }
            for n in sorted(names)
        ]

    def read_file(self, ref, path, max_bytes):
        if path not in self.files:
            raise FileOpError("not_found", "does not exist")
        if len(self.files[path]) > max_bytes:
            raise FileOpError("too_large", "too large")
        return self.files[path]

    def write_file(self, ref, path, data):
        self.files[path] = data

    def delete_path(self, ref, path, *, recursive):
        if path not in self.files:
            raise FileOpError("not_found", "does not exist")
        del self.files[path]


@pytest.fixture
def provider():
    return FileProvider()


@pytest.fixture
def client(settings, provider):
    app = create_app(settings.model_copy(update={"max_file_bytes": 1024}))
    app.state.provider = provider
    with TestClient(app) as c:
        yield c


@pytest.fixture
def base(client):
    ws = client.post("/v1/workspaces", json={"name": "f"}, headers=auth()).json()["id"]
    client.put(f"/v1/workspaces/{ws}/manifest", content=MINIMAL, headers=YAML | auth())
    (env,) = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
        "environments"
    ]
    return f"/v1/workspaces/{ws}/environments/{env['id']}/files"


def test_upload_list_download_delete(client, base, provider):
    blob = bytes(range(256))
    r = client.put(
        f"{base}/content", params={"path": "captures/a.bin"}, content=blob, headers=auth()
    )
    assert r.status_code == 201 and r.json() == {"path": "captures/a.bin", "bytes": 256}
    assert provider.files["/workspace/captures/a.bin"] == blob
    listing = client.get(base, headers=auth()).json()
    assert listing["entries"][0]["name"] == "captures"
    assert client.get(base, params={"path": "captures"}, headers=auth()).json()["entries"][0] == {
        "name": "a.bin",
        "type": "file",
        "size": 256,
        "modified": 0.0,
    }
    dl = client.get(f"{base}/content", params={"path": "captures/a.bin"}, headers=auth())
    assert dl.content == blob and "attachment" in dl.headers["content-disposition"]
    assert client.delete(base, params={"path": "captures/a.bin"}, headers=auth()).status_code == 204
    assert (
        client.get(f"{base}/content", params={"path": "captures/a.bin"}, headers=auth()).status_code
        == 404
    )
    actions = [e["action"] for e in client.get("/v1/audit", headers=auth()).json()]
    assert {"file.upload", "file.download", "file.delete"} <= set(actions)


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/shadow", "a/../../b", "-rf"])
def test_traversal_refused(client, base, path):
    for method, url in [("get", f"{base}/content"), ("delete", base)]:
        r = getattr(client, method)(url, params={"path": path}, headers=auth())
        assert r.status_code == 400, (method, path)
    r = client.put(f"{base}/content", params={"path": path}, content=b"x", headers=auth())
    assert r.status_code == 400


def test_cannot_delete_workspace_root(client, base):
    for p in ["", ".", "./"]:
        assert client.delete(base, params={"path": p}, headers=auth()).status_code == 400


def test_upload_size_limit(client, base):
    r = client.put(f"{base}/content", params={"path": "big"}, content=b"x" * 2048, headers=auth())
    assert r.status_code == 413
    # the general API body limit (256 KiB) does not block legitimate uploads
    ok = client.put(f"{base}/content", params={"path": "ok"}, content=b"x" * 1000, headers=auth())
    assert ok.status_code == 201


def test_requires_running_environment_and_owner(client, base, provider):
    assert client.get(base, headers=auth("mallory")).status_code == 404
    env_id = base.split("/")[-2]
    client.post(base.removesuffix("/files") + "/stop", headers=auth())
    r = client.get(base, headers=auth())
    assert r.status_code == 409 and "start the environment" in r.json()["detail"]
    assert env_id


def test_errors_map_to_statuses(client, base):
    assert client.get(base, params={"path": "nope"}, headers=auth()).status_code == 404
    client.put(f"{base}/content", params={"path": "f.txt"}, content=b"x", headers=auth())
    assert client.get(base, params={"path": "f.txt"}, headers=auth()).status_code == 409
