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
import tempfile
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
from cyberlab.execution.images import DESKTOP_COMMAND, ImageRecipe, ImageRecipeError, render
from cyberlab.execution.shell import PipeSession, PtyShellSession, ShellSession, StreamSession
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
    data: bytes = b""  # raw stdout when the command was run with binary=True


Runner = Callable[..., RunResult]  # (args, timeout, input=None) -> RunResult


def docker_env(
    docker_host: str | None = None, *, tls_verify: bool = False, cert_path: str | None = None
) -> dict[str, str]:
    """Environment for the docker CLI: which engine to use and how to authenticate to it."""
    env = dict(os.environ)
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    if tls_verify:
        env["DOCKER_TLS_VERIFY"] = "1"
        if cert_path:
            env["DOCKER_CERT_PATH"] = cert_path
    return env


def subprocess_runner(docker_host: str | None = None, *, env: dict | None = None) -> Runner:
    env = env if env is not None else docker_env(docker_host)

    def run(
        args: list[str], timeout: float, input: str | bytes | None = None, binary: bool = False
    ) -> RunResult:
        raw = binary or isinstance(input, bytes)
        try:
            proc = subprocess.run(  # noqa: S603 - fixed binary, argument list, no shell
                args,
                input=input,
                capture_output=True,
                text=not raw,
                timeout=timeout,
                env=env,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProviderError("docker CLI not found on the control-plane host") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"docker command timed out after {timeout:.0f}s") from exc
        if raw:
            err = proc.stderr.decode("utf-8", "replace").strip()
            return RunResult(proc.returncode, "", err, data=proc.stdout)
        return RunResult(proc.returncode, proc.stdout.strip(), proc.stderr.strip())

    return run


Streamer = Callable[..., RunResult]  # (args, timeout, stdout_path=, stdin_path=, max_bytes=)


def subprocess_streamer(docker_host: str | None = None, *, env: dict | None = None) -> Streamer:
    """Run docker with stdout streamed into a file, or stdin streamed from one."""
    env = env if env is not None else docker_env(docker_host)

    def stream(
        args: list[str],
        timeout: float,
        stdout_path=None,
        stdin_path=None,
        max_bytes: int | None = None,
    ) -> RunResult:
        deadline = time.monotonic() + timeout
        stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL  # noqa: SIM115
        # stderr goes to a file so a chatty command can never block on a full pipe
        errfile = tempfile.TemporaryFile()  # noqa: SIM115
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed binary, argument list, no shell
                args, stdin=stdin, stdout=subprocess.PIPE, stderr=errfile, env=env
            )
        except FileNotFoundError as exc:
            raise ProviderError("docker CLI not found on the control-plane host") from exc
        written, too_large = 0, False
        try:
            out = open(stdout_path, "wb") if stdout_path else None  # noqa: SIM115
            try:
                while chunk := proc.stdout.read(1024 * 1024):
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        too_large = True
                        proc.kill()
                        break
                    if out:
                        out.write(chunk)
                    if time.monotonic() > deadline:
                        proc.kill()
                        raise ProviderError(f"docker command timed out after {timeout:.0f}s")
            finally:
                if out:
                    out.close()
            remaining = max(1.0, deadline - time.monotonic())
            proc.wait(timeout=remaining)
            errfile.seek(0)
            err = errfile.read()[-65536:]
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            raise ProviderError(f"docker command timed out after {timeout:.0f}s") from exc
        finally:
            if stdin_path:
                stdin.close()
            errfile.close()
        if too_large:
            return RunResult(-1, "", f"output exceeds {max_bytes} bytes")
        return RunResult(proc.returncode, str(written), err.decode("utf-8", "replace").strip())

    return stream


class FileOpError(ProviderError):
    """A file operation failed; ``kind`` says why (the API maps it to an HTTP status)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _file_error(stderr: str, path: str) -> FileOpError:
    low = stderr.lower()
    rel = path.removeprefix("/workspace/") or "/"
    if "is not running" in low or "no such container" in low:
        return FileOpError("not_running", "environment is not running")
    if "no such file" in low:
        return FileOpError("not_found", f"{rel} does not exist")
    if "not a directory" in low:
        return FileOpError("not_a_directory", f"{rel} is not a directory")
    if "is a directory" in low:
        return FileOpError("is_directory", f"{rel} is a directory")
    if "permission denied" in low:
        return FileOpError("forbidden", f"permission denied for {rel}")
    if "no space left" in low or "disk quota" in low:
        return FileOpError("no_space", "the workspace is full")
    return FileOpError("failed", (stderr.splitlines() or ["file operation failed"])[-1][:200])


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
        streamer: Streamer | None = None,
        env: dict | None = None,
        cost_class: CostClass = CostClass.user_owned,
        allow_paid: bool = False,
    ) -> None:
        self._stream = streamer
        self._egress_ready_timeout = egress_ready_timeout
        self._egress_image = egress_image
        self._docker_host = docker_host
        self._env = env
        self._cost_class = cost_class
        self._allow_paid = allow_paid
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
        if env.access.desktop:
            # --init reaps the desktop's background processes; DISPLAY lets programs started
            # from the terminal (e.g. `wireshark &`) open on the remote desktop.
            args[2:2] = ["--init"]
            args += ["--env", "DISPLAY=:1"]
        for k, v in labels.items():
            args += ["--label", f"{k}={v}"]
        args += [_check("image", image, _IMAGE), "sleep", "infinity"]
        return args

    # ------------------------------------------------------------------ interface
    def status(self) -> ProviderStatus:
        cost = self._cost_class
        if cost == CostClass.paid and not self._allow_paid:
            return ProviderStatus(
                self.name,
                False,
                "not connected: this provider is marked as paid; set "
                "CYBERLAB_ALLOW_PAID_PROVIDERS=true only if you accept its costs",
                cost,
            )
        try:
            res = self._docker("version", "--format", "{{.Server.Version}}", timeout=15, ok=True)
        except ProviderError as exc:
            return ProviderStatus(self.name, False, str(exc), cost)
        if res.returncode != 0 or not res.stdout:
            detail = (res.stderr or "daemon unreachable").splitlines()[-1][:200]
            return ProviderStatus(self.name, False, f"not connected: {detail}", cost)
        return ProviderStatus(self.name, True, f"Docker Engine {res.stdout}", cost)

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
            recipe = render(
                self.resolve_image(request.image),
                request.tool_packs,
                request.family,
                request.source_rewrite,
                desktop=request.environment.access.desktop,
            )
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
        env = self._env if self._env is not None else docker_env(self._docker_host)
        return PtyShellSession(self.shell_argv(provider_ref), env=env)

    def desktop_argv(self, provider_ref: str) -> list[str]:
        _check("container id", provider_ref, _REF)
        return [self._bin, "exec", "--interactive", provider_ref, DESKTOP_COMMAND]

    async def open_desktop(self, provider_ref: str) -> StreamSession:
        """A raw VNC byte stream to the environment's desktop (no network port involved)."""
        env = self._env if self._env is not None else docker_env(self._docker_host)
        return await PipeSession.start(self.desktop_argv(provider_ref), env=env)

    # ------------------------------------------------------------------ files
    # Every operation runs *inside* the environment as its own unprivileged user, with the
    # path as a separate argument, so the API can do nothing the terminal couldn't.
    def _exec(
        self,
        provider_ref: str,
        *argv: str,
        input: bytes | None = None,
        binary: bool = False,
        timeout: float = 120,
    ) -> RunResult:
        _check("container id", provider_ref, _REF)
        args = [
            self._bin,
            "exec",
            *(["--interactive"] if input is not None else []),
            provider_ref,
            *argv,
        ]
        if input is not None:
            return self._run(args, timeout, input=input, binary=True)
        if binary:
            return self._run(args, timeout, binary=True)
        return self._run(args, timeout)

    def list_files(self, provider_ref: str, path: str, limit: int = 1000) -> list[dict]:
        res = self._exec(
            provider_ref,
            "find",
            path,
            "-mindepth",
            "1",
            "-maxdepth",
            "1",
            "-printf",
            "%y\\t%s\\t%T@\\t%f\\0",
            binary=True,
        )
        if res.returncode != 0:
            raise _file_error(res.stderr, path)
        kinds = {"f": "file", "d": "directory", "l": "symlink"}
        entries = []
        for record in res.data.split(b"\0"):
            if not record:
                continue
            kind, size, mtime, name = record.decode("utf-8", "replace").split("\t", 3)
            entries.append(
                {
                    "name": name,
                    "type": kinds.get(kind, "other"),
                    "size": int(size),
                    "modified": float(mtime),
                }
            )
        entries.sort(key=lambda e: (e["type"] != "directory", e["name"]))
        return entries[:limit]

    def read_file(self, provider_ref: str, path: str, max_bytes: int) -> bytes:
        info = self._exec(provider_ref, "stat", "-L", "-c", "%F\t%s", "--", path)
        if info.returncode != 0:
            raise _file_error(info.stderr, path)
        kind, _, size = info.stdout.partition("\t")
        if kind == "directory":
            raise FileOpError("is_directory", f"{path.removeprefix('/workspace/')} is a directory")
        if not kind.startswith("regular"):
            raise FileOpError("failed", "only regular files can be downloaded")
        if int(size or 0) > max_bytes:
            raise FileOpError("too_large", f"file is larger than {max_bytes} bytes")
        res = self._exec(provider_ref, "cat", "--", path, binary=True)
        if res.returncode != 0:
            raise _file_error(res.stderr, path)
        return res.data

    def write_file(self, provider_ref: str, path: str, data: bytes) -> None:
        parent = path.rsplit("/", 1)[0]
        mk = self._exec(provider_ref, "mkdir", "-p", "--", parent)
        if mk.returncode != 0:
            raise _file_error(mk.stderr, parent)
        # Fixed script; the path is passed as $1, never interpolated into it.
        res = self._exec(provider_ref, "sh", "-c", 'cat > "$1"', "sh", path, input=data)
        if res.returncode != 0:
            raise _file_error(res.stderr, path)

    def delete_path(self, provider_ref: str, path: str, *, recursive: bool) -> None:
        res = self._exec(provider_ref, "rm", *(["-r"] if recursive else []), "--", path)
        if res.returncode != 0:
            raise _file_error(res.stderr, path)

    # ------------------------------------------------------------------ snapshots
    def _snapshot_helper(self, volume: str, image: str, *, writable: bool) -> list[str]:
        """A throwaway container that can only see the workspace volume: no network,
        no capabilities, read-only root, running as the environment's user."""
        _check("volume", volume, _REF)
        mode = "" if writable else ":ro"
        return [
            self._bin,
            "run",
            "--rm",
            *(["--interactive"] if writable else []),
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--user",
            self._user,
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--label",
            f"{MANAGED_LABEL}=true",
            "--label",
            "dev.cyberlab.role=snapshot",
            "--volume",
            f"{volume}:/workspace{mode}",
            _check("image", image, _IMAGE),
        ]

    def export_workspace(self, volume: str, image: str, destination, max_bytes: int) -> int:
        """Write a gzip-compressed tar of the volume to ``destination``. Returns its size."""
        if self._stream is None:
            raise ProviderError("snapshots need a streaming runner")
        args = [
            *self._snapshot_helper(volume, image, writable=False),
            "tar",
            "-czf",
            "-",
            "-C",
            "/workspace",
            ".",
        ]
        res = self._stream(args, BUILD_TIMEOUT, stdout_path=destination, max_bytes=max_bytes)
        if res.returncode == -1:
            raise FileOpError("too_large", f"workspace snapshot is larger than {max_bytes} bytes")
        if res.returncode != 0:
            raise _file_error(res.stderr, "/workspace")
        return int(res.stdout or 0)

    def import_workspace(self, volume: str, image: str, source) -> None:
        """Replace the volume's contents with the archive at ``source``.

        Runs in the same locked-down helper, which can only write inside the volume.
        Extraction goes to a staging folder first, so a bad archive changes nothing.
        """
        if self._stream is None:
            raise ProviderError("snapshots need a streaming runner")
        # Extract into a staging folder first; only if that fully succeeds are the old
        # files replaced. A bad archive leaves the workspace exactly as it was.
        script = (
            'set -eu; s=/workspace/.cyberlab-restore; rm -rf "$s"; mkdir "$s"; '
            'if ! tar -xzf - -C "$s" --no-same-owner --no-same-permissions; then '
            "rm -rf \"$s\"; echo 'restore failed: archive could not be extracted' >&2; exit 3; fi; "
            "find /workspace -mindepth 1 -maxdepth 1 ! -name .cyberlab-restore -exec rm -rf {} +; "
            'find "$s" -mindepth 1 -maxdepth 1 -exec mv {} /workspace/ \\;; rmdir "$s"'
        )
        args = [*self._snapshot_helper(volume, image, writable=True), "sh", "-c", script]
        res = self._stream(args, BUILD_TIMEOUT, stdin_path=source)
        if res.returncode != 0:
            raise _file_error(res.stderr or "restore failed", "/workspace")
