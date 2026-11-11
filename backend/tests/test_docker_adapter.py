import json

import pytest

from cyberlab.execution import EnvState, ProviderError, ProvisionRequest
from cyberlab.execution.docker import DockerAdapter, RunResult
from cyberlab.manifest.models import Environment


class FakeRunner:
    """Records docker invocations and returns scripted results."""

    def __init__(self, responses=None):
        self.calls: list[list[str]] = []
        self.inputs: list[str] = []
        self.responses = responses or {}

    def __call__(self, args, timeout, input=None):
        self.calls.append(args)
        if input is not None:
            self.inputs.append(input)
        key = " ".join(args[1:3])
        return self.responses.get(key, RunResult(0, "abc123def456", ""))

    def find(self, sub):
        return [c for c in self.calls if c[1] == sub]


def env(mode="lab", **extra):
    return Environment.model_validate(
        {
            "name": "shell",
            "baseImage": "debian-12",
            "resources": {"cpu": 0.5, "memoryMiB": 512, "storageGiB": 2},
            "network": {"mode": mode, **extra},
        }
    )


def req(mode="lab", internet_approved=False):
    return ProvisionRequest(
        workspace_key="ab12cd34ef56",
        environment=env(mode),
        image="docker.io/library/debian:12-slim",
        internet_approved=internet_approved,
    )


def run_args(runner):
    (call,) = [c for c in runner.find("run") if "--detach" in c]
    return call


def test_container_is_hardened():
    runner = FakeRunner({"inspect --format": RunResult(0, "running", "")})
    result = DockerAdapter(runner).provision(req())
    args = run_args(runner)
    joined = " ".join(args)
    for flag in [
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--read-only",
        "--user 1000:1000",
        "--pids-limit 256",
        "--cpus 0.5",
        "--memory 512m",
        "--memory-swap 512m",
        "--restart no",
        "--volume cyberlab-ab12cd34ef56-shell-workspace:/workspace",
    ]:
        assert flag in joined, flag
    assert "--privileged" not in args and "--publish" not in args and "-p" not in args
    assert args[-3].startswith("cyberlab-env:") and args[-2:] == ["sleep", "infinity"]
    assert result.state == EnvState.running


@pytest.mark.parametrize(
    "mode,network,internal",
    [
        ("isolated", "none", None),
        ("lab", "cyberlab-ab12cd34ef56-lab", True),
        ("restricted-egress", "cyberlab-ab12cd34ef56-lab", True),  # fails closed: no proxy yet
    ],
)
def test_network_modes(mode, network, internal):
    runner = FakeRunner({"network inspect": RunResult(1, "", "not found")})
    extra = {"allowedEgress": ["example.com"]} if mode == "restricted-egress" else {}
    request = ProvisionRequest(
        workspace_key="ab12cd34ef56",
        environment=env(mode, **extra),
        image="docker.io/library/debian:12-slim",
    )
    DockerAdapter(runner).provision(request)
    args = run_args(runner)
    assert args[args.index("--network") + 1] == network
    creates = [c for c in runner.find("network") if c[2] == "create"]
    if internal is None:
        assert not creates
    else:
        assert "--internal" in creates[0]


def test_internet_requires_approval():
    with pytest.raises(ProviderError, match="not approved"):
        DockerAdapter(FakeRunner()).provision(req("internet"))
    runner = FakeRunner({"network inspect": RunResult(1, "", "")})
    DockerAdapter(runner).provision(req("internet", internet_approved=True))
    create = [c for c in runner.find("network") if c[2] == "create"][0]
    assert "--internal" not in create


def test_image_mirror_rewrites_docker_hub_only():
    a = DockerAdapter(FakeRunner(), image_mirror="mirror.gcr.io")
    assert (
        a.resolve_image("docker.io/library/debian:12-slim")
        == "mirror.gcr.io/library/debian:12-slim"
    )
    assert a.resolve_image("ghcr.io/x/y:1") == "ghcr.io/x/y:1"


@pytest.mark.parametrize("bad", ["-rf", "x; rm -rf /", "UPPER", "a b", "../x"])
def test_unsafe_names_rejected(bad):
    with pytest.raises(ProviderError):
        DockerAdapter.names(bad, "shell")
    with pytest.raises(ProviderError):
        DockerAdapter(FakeRunner()).inspect(bad if bad[0] == "-" else "--" + bad)


def test_image_built_only_when_missing(registry):
    packs = (registry.tool_packs["crypto"],)
    request = ProvisionRequest(
        workspace_key="ab12cd34ef56",
        environment=env("lab"),
        image="docker.io/library/debian:12-slim",
        tool_packs=packs,
    )
    runner = FakeRunner({"image inspect": RunResult(1, "", "No such image")})
    DockerAdapter(runner, image_mirror="mirror.gcr.io").provision(request)
    (build,) = runner.find("build")
    assert build[3].startswith("cyberlab-env:") and build[-1] == "-"  # Dockerfile on stdin
    (dockerfile,) = runner.inputs
    assert dockerfile.splitlines()[2] == "FROM mirror.gcr.io/library/debian:12-slim"
    for pkg in ["age", "gnupg", "openssl"]:
        assert f"      {pkg} \\" in dockerfile
    run = run_args(runner)
    assert run[-3] == build[3]  # the container runs the image that was built
    # already built: no rebuild
    runner = FakeRunner()
    DockerAdapter(runner).provision(request)
    assert not runner.find("build")


def test_build_failure_is_reported(registry):
    runner = FakeRunner(
        {
            "image inspect": RunResult(1, "", ""),
            "build --tag": RunResult(1, "", "E: Unable to locate package x"),
        }
    )
    request = ProvisionRequest(
        workspace_key="ab12cd34ef56",
        environment=env("lab"),
        image="docker.io/library/debian:12-slim",
        tool_packs=(registry.tool_packs["crypto"],),
    )
    with pytest.raises(ProviderError, match="Unable to locate package"):
        DockerAdapter(runner).provision(request)
    assert not [c for c in runner.find("run") if "--detach" in c]  # nothing started


def test_errors_surface_as_provider_error():
    runner = FakeRunner({"run --detach": RunResult(125, "", "Error: no space left on device")})
    with pytest.raises(ProviderError, match="no space left"):
        DockerAdapter(runner).provision(req())


def test_status_not_connected():
    runner = FakeRunner({"version --format": RunResult(1, "", "Cannot connect to the daemon")})
    st = DockerAdapter(runner).status()
    assert not st.connected and "not connected" in st.detail


def test_inspect_and_lifecycle_states():
    runner = FakeRunner({"inspect --format": RunResult(0, "exited", "")})
    a = DockerAdapter(runner)
    assert a.stop("abc123") == EnvState.stopped
    assert a.inspect("abc123") == EnvState.stopped
    missing = DockerAdapter(FakeRunner({"inspect --format": RunResult(1, "", "No such")}))
    assert missing.inspect("abc123") == EnvState.missing


def test_destroy_keeps_or_removes_volume():
    runner = FakeRunner({"inspect --format": RunResult(0, "vol-1", "")})
    DockerAdapter(runner).destroy("abc123", remove_workspace_volume=False)
    assert not runner.find("volume")
    runner = FakeRunner({"inspect --format": RunResult(0, "vol-1", "")})
    DockerAdapter(runner).destroy("abc123", remove_workspace_volume=True)
    assert runner.find("volume")[0][-1] == "vol-1"


def test_new_volume_ownership_fixed_with_minimal_privilege():
    runner = FakeRunner({"volume inspect": RunResult(1, "", "no such volume")})
    DockerAdapter(runner).provision(req())
    (helper,) = [c for c in runner.find("run") if "--rm" in c]
    joined = " ".join(helper)
    assert "--cap-drop ALL --cap-add CHOWN" in joined and "--network none" in joined
    assert helper[-3:] == ["chown", "1000:1000", "/workspace"]
    # existing volume: no helper container
    runner = FakeRunner()
    DockerAdapter(runner).provision(req())
    assert not [c for c in runner.find("run") if "--rm" in c]


def test_egress_proxy_is_hardened_and_bridges_two_networks():
    runner = FakeRunner(
        {
            "network inspect": RunResult(1, "", "not found"),
            "logs --tail": RunResult(0, '{"event": "listening"}', ""),
        }
    )
    clients = {"ab" * 32: {"name": "scanner", "hosts": ["ghcr.io"]}}
    addr = DockerAdapter(runner, image_mirror="mirror.gcr.io").configure_egress(
        "ab12cd34ef56", clients
    )
    assert addr == "egress-proxy:3128"
    creates = {c[-1]: c for c in runner.find("network") if c[2] == "create"}
    assert "--internal" in creates["cyberlab-ab12cd34ef56-lab"]
    assert "--internal" not in creates["cyberlab-ab12cd34ef56-egress"]
    (run,) = [c for c in runner.find("run") if "--detach" in c]
    joined = " ".join(run)
    for flag in [
        "--user 65534:65534",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--read-only",
        "--memory 64m",
        "--network cyberlab-ab12cd34ef56-egress",
    ]:
        assert flag in joined, flag
    assert "mirror.gcr.io/library/python:3.13-slim" in run
    config = json.loads(
        next(a for a in run if a.startswith("CYBERLAB_EGRESS_CONFIG=")).split("=", 1)[1]
    )
    assert config["clients"] == clients and config["allowPrivate"] is False
    assert run[-4:-1] == ["python", "-I", "-c"] and "class EgressProxy" in run[-1]
    connect = [c for c in runner.find("network") if c[2] == "connect"][0]
    assert connect[-3:] == [
        "egress-proxy",
        "cyberlab-ab12cd34ef56-lab",
        "cyberlab-ab12cd34ef56-egress-proxy",
    ]


def test_environment_gets_proxy_settings_only_when_restricted():
    url = "http://scanner:tok@egress-proxy:3128"
    request = ProvisionRequest(
        workspace_key="ab12cd34ef56",
        environment=env("restricted-egress", allowedEgress=["ghcr.io"]),
        image="docker.io/library/debian:12-slim",
        egress_proxy_url=url,
    )
    runner = FakeRunner()
    DockerAdapter(runner).provision(request)
    joined = " ".join(run_args(runner))
    assert f"HTTPS_PROXY={url}" in joined and f"http_proxy={url}" in joined
    assert "--network cyberlab-ab12cd34ef56-lab" in joined  # no direct route out
    runner = FakeRunner()
    DockerAdapter(runner).provision(req("lab"))
    assert "PROXY" not in " ".join(run_args(runner))


def test_egress_log_parses_decisions_only():
    lines = "\n".join(
        [
            json.dumps({"event": "listening"}),
            json.dumps({"decision": "deny", "host": "evil.example"}),
            "not json",
        ]
    )
    runner = FakeRunner({"logs --tail": RunResult(0, lines, "")})
    assert DockerAdapter(runner).egress_log("ab12cd34ef56") == [
        {"decision": "deny", "host": "evil.example"}
    ]


def test_workspace_cleanup_removes_proxy_and_networks():
    runner = FakeRunner()
    DockerAdapter(runner).remove_workspace_networks("ab12cd34ef56")
    assert ["docker", "rm", "--force", "cyberlab-ab12cd34ef56-egress-proxy"] in runner.calls
    removed = [c[-1] for c in runner.find("network") if c[2] == "rm"]
    assert removed == [f"cyberlab-ab12cd34ef56-{k}" for k in ("lab", "internet", "egress")]


def test_egress_proxy_startup_failures_are_reported():
    crashed = FakeRunner(
        {
            "logs --tail": RunResult(0, "", "SyntaxError: bad config"),
            "inspect --format": RunResult(0, "exited", ""),
        }
    )
    with pytest.raises(ProviderError, match="exited during startup: SyntaxError"):
        DockerAdapter(crashed).configure_egress("ab12cd34ef56", {})
    slow = FakeRunner(
        {"logs --tail": RunResult(0, "", ""), "inspect --format": RunResult(0, "running", "")}
    )
    with pytest.raises(ProviderError, match="did not become ready"):
        DockerAdapter(slow, egress_ready_timeout=0.3).configure_egress("ab12cd34ef56", {})


def test_arch_family_reaches_the_image_build(registry):
    request = ProvisionRequest(
        workspace_key="ab12cd34ef56",
        environment=env("lab"),
        image="docker.io/blackarchlinux/blackarch:latest",
        family="arch",
        tool_packs=(registry.tool_packs["network-analysis"],),
    )
    runner = FakeRunner({"image inspect": RunResult(1, "", "No such image")})
    DockerAdapter(runner, image_mirror="mirror.gcr.io").provision(request)
    (dockerfile,) = runner.inputs
    assert "FROM mirror.gcr.io/blackarchlinux/blackarch:latest" in dockerfile
    assert "pacman -Syu" in dockerfile and "wireshark-cli" in dockerfile
    assert "apt-get" not in dockerfile
