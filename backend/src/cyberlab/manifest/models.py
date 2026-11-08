"""Universe Manifest v1alpha1 — the portable description of a user's universe.

The JSON Schema in ``spec/universe-manifest.schema.json`` is generated from
these models (``cyberlab manifest schema``) and a test keeps them in sync.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

API_VERSION = "cyberlab.dev/v1alpha1"
SLUG = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NetworkMode(StrEnum):
    isolated = "isolated"  # no network at all
    lab = "lab"  # private network shared only with other environments in this manifest
    restricted_egress = "restricted-egress"  # lab + allow-listed outbound hosts
    internet = "internet"  # general outbound internet (requires approval)


class Metadata(_Model):
    name: str = Field(pattern=SLUG)
    description: str = Field(default="", max_length=2000)
    labels: dict[str, str] = Field(default_factory=dict, max_length=32)


class Resources(_Model):
    cpu: float = Field(gt=0, le=64)
    memory_mib: int = Field(alias="memoryMiB", gt=0, le=262144)
    storage_gib: int = Field(alias="storageGiB", ge=1, le=2048)


class Network(_Model):
    mode: NetworkMode = NetworkMode.lab
    allowed_egress: list[str] = Field(default_factory=list, alias="allowedEgress", max_length=50)

    @field_validator("allowed_egress")
    @classmethod
    def _hosts(cls, hosts: list[str]) -> list[str]:
        for host in hosts:
            if not _HOST.match(host):
                raise ValueError(f"allowedEgress entries must be exact hostnames: {host!r}")
        return hosts

    @model_validator(mode="after")
    def _egress_only_when_restricted(self) -> Network:
        if self.allowed_egress and self.mode != NetworkMode.restricted_egress:
            raise ValueError("allowedEgress is only valid with mode 'restricted-egress'")
        return self


class Access(_Model):
    terminal: bool = True
    desktop: bool = False


class Lifecycle(_Model):
    ephemeral: bool = False
    idle_timeout_minutes: int | None = Field(default=60, alias="idleTimeoutMinutes", ge=5, le=1440)
    ttl_hours: int | None = Field(default=None, alias="ttlHours", ge=1)


class Environment(_Model):
    name: str = Field(pattern=SLUG)
    base_image: str = Field(alias="baseImage", pattern=SLUG)
    resources: Resources
    tool_packs: list[str] = Field(default_factory=list, alias="toolPacks", max_length=20)
    network: Network = Field(default_factory=Network)
    access: Access = Field(default_factory=Access)
    lifecycle: Lifecycle = Field(default_factory=Lifecycle)

    @field_validator("tool_packs")
    @classmethod
    def _unique_packs(cls, packs: list[str]) -> list[str]:
        if len(set(packs)) != len(packs):
            raise ValueError("toolPacks must not contain duplicates")
        for p in packs:
            if not re.match(SLUG, p):
                raise ValueError(f"invalid tool pack id {p!r}")
        return packs


class Workspace(_Model):
    persistent: bool = True
    storage_gib: int = Field(default=5, alias="storageGiB", ge=0, le=1024)
    snapshot_policy: Literal["manual", "daily", "on-stop"] = Field(
        default="manual", alias="snapshotPolicy"
    )


class Preferences(_Model):
    shell: Literal["bash", "zsh", "fish", "sh"] = "bash"
    editor: str = Field(default="vim", max_length=32)
    theme: Literal["dark", "light", "system"] = "system"
    timezone: str = Field(default="UTC", max_length=64)


class UniverseSpec(_Model):
    environments: list[Environment] = Field(min_length=1, max_length=32)
    workspace: Workspace = Field(default_factory=Workspace)
    preferences: Preferences = Field(default_factory=Preferences)

    @model_validator(mode="after")
    def _unique_env_names(self) -> UniverseSpec:
        names = [e.name for e in self.environments]
        if len(set(names)) != len(names):
            raise ValueError("environment names must be unique")
        return self


class UniverseManifest(_Model):
    api_version: Literal["cyberlab.dev/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["Universe"]
    metadata: Metadata
    spec: UniverseSpec

    def to_portable(self) -> dict:
        """Serialize to the canonical, portable (camelCase) form."""
        return self.model_dump(mode="json", by_alias=True)
