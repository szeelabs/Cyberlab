"""Execution plane interface.

A ``ProviderAdapter`` turns one environment of an approved, validated manifest into
something running somewhere (a local or remote Docker host today; microVMs, user-owned
hosts or cloud VMs later). The control plane only talks to this interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from cyberlab.execution.shell import ShellSession
from cyberlab.manifest.models import Environment
from cyberlab.registry.models import ToolPack


class EnvState(StrEnum):
    provisioning = "provisioning"
    running = "running"
    stopped = "stopped"
    failed = "failed"
    destroyed = "destroyed"
    missing = "missing"  # the provider no longer knows this environment


class CostClass(StrEnum):
    free = "free"
    user_owned = "user-owned"
    paid = "paid"


class ProviderError(RuntimeError):
    """A provider operation failed. The message is safe to show to the user."""


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    connected: bool
    detail: str
    cost_class: CostClass


@dataclass(frozen=True)
class ProvisionRequest:
    workspace_key: str  # short, slug-safe identifier of the workspace
    environment: Environment
    image: str  # base OCI image reference resolved from the registry
    tool_packs: tuple[ToolPack, ...] = ()
    internet_approved: bool = False
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProvisionResult:
    provider_ref: str  # provider-specific id (e.g. container id)
    state: EnvState
    resource_names: dict[str, str] = field(default_factory=dict)


class ProviderAdapter(Protocol):
    name: str

    def status(self) -> ProviderStatus: ...

    def provision(self, request: ProvisionRequest) -> ProvisionResult: ...

    def start(self, provider_ref: str) -> EnvState: ...

    def stop(self, provider_ref: str) -> EnvState: ...

    def destroy(self, provider_ref: str, *, remove_workspace_volume: bool) -> EnvState: ...

    def inspect(self, provider_ref: str) -> EnvState: ...

    async def open_shell(self, provider_ref: str) -> ShellSession:
        """Open an interactive shell in a running environment (browser terminal)."""
        ...
