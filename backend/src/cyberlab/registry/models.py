"""Registry data model: base images, tool categories and tool packs.

Registry entries are *metadata*. They describe what a pack contains, what it
needs and how risky it is, so the control plane can validate and plan
environments. Building OCI images from packs is Phase 2.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

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
    family: str
    image: str = Field(description="Upstream OCI image reference")
    description: str = ""
    min_resources: MinResources = Field(default_factory=MinResources, alias="minResources")
    supports_desktop: bool = Field(default=False, alias="supportsDesktop")


class ToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    purpose: str
    homepage: str | None = None


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
