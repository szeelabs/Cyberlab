"""Registry data model: base images, tool categories and tool packs.

Registry entries are *metadata*. They describe what a pack contains, what it
needs and how risky it is, so the control plane can validate and plan
environments. Building OCI images from packs is Phase 2.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SLUG = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"


class RiskLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class Category(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=SLUG)
    name: str
    description: str


class MinResources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu: float = Field(default=0.5, gt=0)
    memory_mib: int = Field(default=512, gt=0, alias="memoryMiB")
    storage_gib: int = Field(default=1, ge=0, alias="storageGiB")


class BaseImage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=SLUG)
    name: str
    family: Literal["debian", "arch"] = Field(
        description="Package system: debian (apt) or arch (pacman)"
    )
    image: str = Field(description="Upstream OCI image reference")
    description: str = ""
    min_resources: MinResources = Field(default_factory=MinResources, alias="minResources")
    supports_desktop: bool = Field(default=False, alias="supportsDesktop")


APT_PACKAGE = r"^[a-z0-9][a-z0-9+.-]{0,63}$"
COMMAND = r"^[a-zA-Z0-9][a-zA-Z0-9_.+-]{0,63}$"


class ToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    purpose: str
    homepage: str | None = None
    package: str | None = Field(
        default=None, pattern=APT_PACKAGE, description="Debian/Ubuntu package that installs it"
    )
    arch_package: str | None = Field(
        default=None,
        alias="archPackage",
        pattern=APT_PACKAGE,
        description="Arch/BlackArch package name, when it differs from (or replaces) `package`",
    )
    command: str | None = Field(
        default=None, pattern=COMMAND, description="Executable used to smoke-test the install"
    )

    @model_validator(mode="after")
    def _command_with_package(self) -> ToolInfo:
        if self.package and not self.command:
            raise ValueError(f"tool {self.name!r}: a packaged tool needs a smoke-test command")
        return self


class ToolPack(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=SLUG)
    name: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    category: str = Field(pattern=SLUG)
    description: str
    risk: RiskLevel = RiskLevel.low
    requires_isolation: bool = Field(
        default=False,
        alias="requiresIsolation",
        description="Environment must not have internet egress",
    )
    compatible_bases: list[str] = Field(alias="compatibleBases", min_length=1)
    min_resources: MinResources = Field(default_factory=MinResources, alias="minResources")
    tools: list[ToolInfo] = Field(min_length=1)
    status: str = Field(default="metadata-only", pattern=r"^(metadata-only|buildable|published)$")

    @property
    def packages(self) -> list[str]:
        """Distribution packages to install, in a stable order without duplicates."""
        return sorted({t.package for t in self.tools if t.package})

    def packages_for(self, family: str) -> list[str]:
        """Packages for a package system ("debian" or "arch")."""
        if family == "arch":
            return sorted({t.arch_package or t.package for t in self.tools if t.package})
        return self.packages

    @model_validator(mode="after")
    def _status_matches_packages(self) -> ToolPack:
        if self.status == "metadata-only" and self.packages:
            raise ValueError(f"{self.id}: has packages, so status must be 'buildable'")
        if self.status != "metadata-only" and not self.packages:
            raise ValueError(f"{self.id}: status {self.status!r} needs at least one package")
        return self
