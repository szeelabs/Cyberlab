"""Docker execution adapter.

Drives the ``docker`` CLI with argument lists (never a shell), so it works against a
local daemon or a remote host via ``DOCKER_HOST`` (``ssh://user@host`` or ``tcp://`` with
TLS). Every container is hardened:

* runs as an unprivileged user, all Linux capabilities dropped, ``no-new-privileges``
* read-only root filesystem; writable ``/workspace`` volume and small ``/tmp`` tmpfs
* CPU, memory (no extra swap) and process-count limits from the manifest
* network per manifest mode; internet only when the plan was approved

Containers share the host kernel, so this is not a boundary for hostile code.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

from cyberlab.execution.base import (
    CostClass,
    EnvState,
    ProviderError,
    ProviderStatus,
    ProvisionRequest,
    ProvisionResult,
)
from cyberlab.execution.images import ImageRecipe, ImageRecipeError, render
from cyberlab.execution.shell import PtyShellSession, ShellSession
from cyberlab.manifest.models import NetworkMode

_SAFE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_REF = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:@-]{0,254}$")

MANAGED_LABEL = "dev.cyberlab.managed"
DEFAULT_PIDS_LIMIT = 256
EGRESS_ALIAS = "egress-proxy"
EGRESS_PORT = 3128
DEFAULT_EGRESS_IMAGE = "docker.io/library/python:3.13-slim"
BUILD_TIMEOUT = 1800  # seconds; first build of a large pack can be slow
STATE_MAP = {
    "created": EnvState.stopped,
    "running": EnvState.running,
    "paused": EnvState.stopped,
    "restarting": EnvState.running,
    "exited": EnvState.stopped,
    "dead": EnvState.failed,
    "removing": EnvState.destroyed,
}


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., RunResult]  # (args, timeout, input=None) -> RunResult


def subprocess_runner(docker_host: str | None = None) -> Runner:
    env = dict(os.environ)
    if docker_host:
        env["DOCKER_HOST"] = docker_host

    def run(args: list[str], timeout: float, input: str | None = None) -> RunResult:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed binary, argument list, no shell
                args,
                input=input,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProviderError("docker CLI not found on the control-plane host") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"docker command timed out after {timeout:.0f}s") from exc
        return RunResult(proc.returncode, proc.stdout.strip(), proc.stderr.strip())

    return run


def _check(name: str, value: str, pattern: re.Pattern[str]) -> str:
    if not pattern.match(value):
        raise ProviderError(f"refusing unsafe {name}: {value!r}")
    return value


class DockerAdapter:
    name = "docker"

    def __init__(
        self,
        runner: Runner,
        *,
        binary: str = "docker",
        run_as: str = "1000:1000",
        image_mirror: str | None = None,
        timeout: float = 300,
        docker_host: str | None = None,
        egress_image: str = DEFAULT_EGRESS_IMAGE,
        egress_ready_timeout: float = 30,
    ) -> None:
        self._egress_ready_timeout = egress_ready_timeout
        self._egress_image = egress_image
        self._docker_host = docker_host
        self._run = runner
        self._bin = binary
        self._user = run_as
        self._mirror = image_mirror
        self._timeout = timeout

    # ------------------------------------------------------------------ helpers
    def _docker(
        self, *args: str, timeout: float | None = None, ok: bool = False, input: str | None = None
    ) -> RunResult:
        if input is None:
            res = self._run([self._bin, *args], timeout or self._timeout)
        else:
            res = self._run([self._bin, *args], timeout or self._timeout, input=input)
        if res.returncode != 0 and not ok:
            msg = (res.stderr or res.stdout or "unknown error").splitlines()[-1][:300]
            raise ProviderError(f"docker {args[0]} failed: {msg}")
        return res

    def resolve_image(self, image: str) -> str:
        _check("image", image, _IMAGE)
        if self._mirror and image.startswith("docker.io/"):
            return f"{self._mirror.rstrip('/')}/{image.removeprefix('docker.io/')}"
        return image

    @staticmethod
    def names(workspace_key: str, env_name: str) -> dict[str, str]:
        _check("workspace key", workspace_key, _SAFE)
        _check("environment name", env_name, _SAFE)
        base = f"cyberlab-{workspace_key}"
        return {
            "container": f"{base}-{env_name}",
            "volume": f"{base}-{env_name}-workspace",
            "lab_network": f"{base}-lab",
            "internet_network": f"{base}-internet",
            "egress_network": f"{base}-egress",
            "egress_proxy": f"{base}-egress-proxy",
        }

    def _ensure_network(self, name: str, *, internal: bool, workspace_key: str) -> None:
        if self._docker("network", "inspect", name, ok=True).returncode == 0:
            return
        args = ["network", "create", "--driver", "bridge"]
        if internal:
            args.append("--internal")
        args += [
            "--label",
            f"{MANAGED_LABEL}=true",
            "--label",
            f"dev.cyberlab.workspace={workspace_key}",
            name,
        ]
        self._docker(*args)

    def _prepare_volume(self, volume: str, image: str, workspace_key: str) -> None:
        """Create the workspace volume once and hand /workspace to the unprivileged user.

        New Docker volumes are owned by root. A short-lived helper container with only the
        CHOWN capability and no network fixes ownership, then exits.
        """
        if self._docker("volume", "inspect", volume, ok=True).returncode == 0:
            return
        self._docker(
            "volume",
            "create",
            "--label",
            f"{MANAGED_LABEL}=true",
            "--label",
            f"dev.cyberlab.workspace={workspace_key}",
            volume,
        )
        self._docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "0:0",
            "--volume",
            f"{volume}:/workspace",
            image,
            "chown",
            self._user,
            "/workspace",
            timeout=120,
        )

    def ensure_image(self, recipe: ImageRecipe) -> str:
        """Build the environment image once; later environments reuse it by tag."""
        if self._docker("image", "inspect", recipe.tag, ok=True).returncode == 0:
            return recipe.tag
        # Dockerfile on stdin, no build context: nothing from the host can be copied in.
        self._docker(
            "build",
            "--tag",
            recipe.tag,
            "-",
            input=recipe.dockerfile,
            timeout=BUILD_TIMEOUT,
        )
        return recipe.tag

    def build_run_args(self, request: ProvisionRequest, network: str, image: str) -> list[str]:
        env = request.environment
        n = self.names(request.workspace_key, env.name)
        mem = f"{env.resources.memory_mib}m"
        labels = {
            MANAGED_LABEL: "true",
            "dev.cyberlab.workspace": request.workspace_key,
            "dev.cyberlab.environment": env.name,
            **request.labels,
        }
        args = [
            "run",
            "--detach",
            "--name",
            n["container"],
            "--hostname",
            env.name,
            "--user",
            self._user,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            # Container-private tmpfs, not the host /tmp.
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=64m",  # noqa: S108
            "--pids-limit",
            str(DEFAULT_PIDS_LIMIT),
            "--cpus",
            str(env.resources.cpu),
            "--memory",
            mem,
            "--memory-swap",
            mem,
            "--network",
            network,
            # nocopy: stop Docker resetting /workspace ownership from the image.
            "--volume",
            f"{n['volume']}:/workspace:nocopy",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/workspace",
            *self._proxy_env(request.egress_proxy_url),
            "--restart",
            "no",
        ]
        for k, v in labels.items():
            args += ["--label", f"{k}={v}"]
        args += [_check("image", image, _IMAGE), "sleep", "infinity"]
        return args

    # ------------------------------------------------------------------ interface
    def status(self) -> ProviderStatus:
        try:
            res = self._docker("version", "--format", "{{.Server.Version}}", timeout=15, ok=True)
        except ProviderError as exc:
            return ProviderStatus(self.name, False, str(exc), CostClass.user_owned)
        if res.returncode != 0 or not res.stdout:
            detail = (res.stderr or "daemon unreachable").splitlines()[-1][:200]
            return ProviderStatus(
                self.name, False, f"not connected: {detail}", CostClass.user_owned
            )
        return ProviderStatus(self.name, True, f"Docker Engine {res.stdout}", CostClass.user_owned)

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        env = request.environment
        n = self.names(request.workspace_key, env.name)
        mode = env.network.mode
        if mode == NetworkMode.isolated:
            network = "none"
        elif mode in (NetworkMode.lab, NetworkMode.restricted_egress):
            # restricted-egress has no proxy yet: fail closed (lab only, no egress).
            network = n["lab_network"]
            self._ensure_network(network, internal=True, workspace_key=request.workspace_key)
        elif mode == NetworkMode.internet:
            if not request.internet_approved:
                raise ProviderError("internet access was not approved for this environment")
            network = n["internet_network"]
            self._ensure_network(network, internal=False, workspace_key=request.workspace_key)
        else:  # pragma: no cover - enum is exhaustive
            raise ProviderError(f"unsupported network mode {mode}")

        try:
            recipe = render(self.resolve_image(request.image), request.tool_packs)
        except ImageRecipeError as exc:
            raise ProviderError(str(exc)) from exc
        image = self.ensure_image(recipe)
        self._prepare_volume(n["volume"], image, request.workspace_key)
        res = self._docker(*self.build_run_args(request, network, image))
        ref = res.stdout.splitlines()[-1] if res.stdout else ""
        _check("container id", ref, _REF)
        return ProvisionResult(
            provider_ref=ref,
            state=self.inspect(ref),
            resource_names={**n, "network": network, "image": image},
        )

    def inspect(self, provider_ref: str) -> EnvState:
        _check("container id", provider_ref, _REF)
        res = self._docker(
            "inspect", "--format", "{{.State.Status}}", provider_ref, timeout=30, ok=True
        )
        if res.returncode != 0:
            return EnvState.missing
        return STATE_MAP.get(res.stdout, EnvState.failed)

    def start(self, provider_ref: str) -> EnvState:
        _check("container id", provider_ref, _REF)
        self._docker("start", provider_ref, timeout=60)
        return self.inspect(provider_ref)

    def stop(self, provider_ref: str) -> EnvState:
        _check("container id", provider_ref, _REF)
        self._docker("stop", "--time", "10", provider_ref, timeout=60)
        return self.inspect(provider_ref)

    def destroy(self, provider_ref: str, *, remove_workspace_volume: bool) -> EnvState:
        _check("container id", provider_ref, _REF)
        volume = None
        if remove_workspace_volume:
            res = self._docker(
                "inspect",
                "--format",
                '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Name}}{{end}}{{end}}',
                provider_ref,
                timeout=30,
                ok=True,
            )
            volume = res.stdout if res.returncode == 0 and res.stdout else None
        self._docker("rm", "--force", provider_ref, timeout=60, ok=True)
        if volume:
            self._docker("volume", "rm", _check("volume", volume, _REF), timeout=60, ok=True)
        return EnvState.destroyed

    @staticmethod
    def _proxy_env(url: str | None) -> list[str]:
        if not url:
            return []
        out: list[str] = []
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            out += ["--env", f"{key}={url}"]
        return out + [
            "--env",
            "NO_PROXY=localhost,127.0.0.1",
            "--env",
            "no_proxy=localhost,127.0.0.1",
        ]

    def configure_egress(
        self, workspace_key: str, clients: dict[str, dict], *, allow_private: bool = False
    ) -> str:
        """(Re)start the workspace egress proxy and return its address on the lab network.

        ``clients`` maps the SHA-256 of each environment's proxy token to
        ``{"name": ..., "hosts": [...]}``. The proxy is on the private lab network (alias
        ``egress-proxy``) and on an outbound network; environments only on the lab network.
        """
        n = self.names(workspace_key, "egress")
        lab, out, name = n["lab_network"], n["egress_network"], n["egress_proxy"]
        self._ensure_network(lab, internal=True, workspace_key=workspace_key)
        self._ensure_network(out, internal=False, workspace_key=workspace_key)
        self._docker("rm", "--force", name, timeout=60, ok=True)
        config = json.dumps(
            {
                "listen": f"0.0.0.0:{EGRESS_PORT}",  # noqa: S104 - inside the proxy container
                "ports": [80, 443],
                "allowPrivate": allow_private,
                "clients": clients,
            }
        )
        source = resources.files("cyberlab.egress").joinpath("proxy.py").read_text("utf-8")
        self._docker(
            "run",
            "--detach",
            "--name",
            name,
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--pids-limit",
            "64",
            "--memory",
            "64m",
            "--cpus",
            "0.25",
            "--restart",
            "no",
            "--network",
            out,
            "--label",
            f"{MANAGED_LABEL}=true",
            "--label",
            f"dev.cyberlab.workspace={workspace_key}",
            "--label",
            "dev.cyberlab.role=egress-proxy",
            "--env",
            f"CYBERLAB_EGRESS_CONFIG={config}",
            self.resolve_image(self._egress_image),
            "python",
            "-I",
            "-c",
            source,
        )
        self._docker("network", "connect", "--alias", EGRESS_ALIAS, lab, name)
        self._wait_for_proxy(name)
        return f"{EGRESS_ALIAS}:{EGRESS_PORT}"

    def _wait_for_proxy(self, name: str) -> None:
        """Return once the proxy reports it is listening, so environments never race it."""
        deadline = time.monotonic() + self._egress_ready_timeout
        while True:
            logs = self._docker("logs", "--tail", "20", name, timeout=15, ok=True)
            if '"event": "listening"' in logs.stdout:
                return
            state = self._docker(
                "inspect", "--format", "{{.State.Status}}", name, timeout=15, ok=True
            ).stdout
            if state in ("exited", "dead"):
                detail = (logs.stderr or logs.stdout or "no output").splitlines()[-1][:200]
                raise ProviderError(f"egress proxy exited during startup: {detail}")
            if time.monotonic() >= deadline:
                raise ProviderError("egress proxy did not become ready in time")
            time.sleep(0.25)

    def egress_log(self, workspace_key: str, tail: int = 200) -> list[dict]:
        name = self.names(workspace_key, "egress")["egress_proxy"]
        res = self._docker("logs", "--tail", str(int(tail)), name, timeout=30, ok=True)
        entries = []
        for line in res.stdout.splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and "decision" in entry:
                entries.append(entry)
        return entries

    def remove_workspace_networks(self, workspace_key: str) -> None:
        proxy = self.names(workspace_key, "egress")["egress_proxy"]
        self._docker("rm", "--force", proxy, timeout=60, ok=True)
        for kind in ("lab", "internet", "egress"):
            name = f"cyberlab-{_check('workspace key', workspace_key, _SAFE)}-{kind}"
            self._docker("network", "rm", name, timeout=30, ok=True)

    def shell_argv(self, provider_ref: str) -> list[str]:
        _check("container id", provider_ref, _REF)
        return [
            self._bin,
            "exec",
            "--interactive",
            "--tty",
            "--workdir",
            "/workspace",
            "--env",
            "TERM=xterm-256color",
            provider_ref,
            "sh",
            "-c",
            "if command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh -l; fi",
        ]

    async def open_shell(self, provider_ref: str) -> ShellSession:
        env = dict(os.environ)
        if self._docker_host:
            env["DOCKER_HOST"] = self._docker_host
        return PtyShellSession(self.shell_argv(provider_ref), env=env)
