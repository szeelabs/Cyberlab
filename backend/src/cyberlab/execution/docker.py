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

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from cyberlab.execution.base import (
    CostClass,
    EnvState,
    ProviderError,
    ProviderStatus,
    ProvisionRequest,
    ProvisionResult,
)
from cyberlab.manifest.models import NetworkMode

_SAFE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_REF = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:@-]{0,254}$")

MANAGED_LABEL = "dev.cyberlab.managed"
DEFAULT_PIDS_LIMIT = 256
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


Runner = Callable[[list[str], float], RunResult]


def subprocess_runner(docker_host: str | None = None) -> Runner:
    env = dict(os.environ)
    if docker_host:
        env["DOCKER_HOST"] = docker_host

    def run(args: list[str], timeout: float) -> RunResult:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed binary, argument list, no shell
                args, capture_output=True, text=True, timeout=timeout, env=env, check=False
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
    ) -> None:
        self._run = runner
        self._bin = binary
        self._user = run_as
        self._mirror = image_mirror
        self._timeout = timeout

    # ------------------------------------------------------------------ helpers
    def _docker(self, *args: str, timeout: float | None = None, ok: bool = False) -> RunResult:
        res = self._run([self._bin, *args], timeout or self._timeout)
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

    def build_run_args(self, request: ProvisionRequest, network: str) -> list[str]:
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
            "--restart",
            "no",
        ]
        for k, v in labels.items():
            args += ["--label", f"{k}={v}"]
        args += [self.resolve_image(request.image), "sleep", "infinity"]
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

        image = self.resolve_image(request.image)
        if self._docker("image", "inspect", image, ok=True).returncode != 0:
            self._docker("pull", "--quiet", image)
        self._prepare_volume(n["volume"], image, request.workspace_key)
        res = self._docker(*self.build_run_args(request, network))
        ref = res.stdout.splitlines()[-1] if res.stdout else ""
        _check("container id", ref, _REF)
        return ProvisionResult(
            provider_ref=ref, state=self.inspect(ref), resource_names={**n, "network": network}
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

    def remove_workspace_networks(self, workspace_key: str) -> None:
        for kind in ("lab", "internet"):
            name = f"cyberlab-{_check('workspace key', workspace_key, _SAFE)}-{kind}"
            self._docker("network", "rm", name, timeout=30, ok=True)
