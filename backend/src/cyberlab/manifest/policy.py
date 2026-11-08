"""Manifest evaluation: registry resolution, quotas, security policy, estimates.

The evaluator never provisions anything. It produces a ``Plan`` that later
phases (execution engine, AI Lab Architect) use; anything marked
``requires_approval`` must be explicitly approved by the user first.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum

from pydantic import ValidationError

from cyberlab.config import QuotaSettings
from cyberlab.manifest.models import NetworkMode, UniverseManifest
from cyberlab.registry import Registry
from cyberlab.registry.models import RiskLevel
from cyberlab.yaml_safe import ManifestParseError, load_yaml_text

DESKTOP_OVERHEAD_MIB = 1024


class Severity(StrEnum):
    error = "error"
    approval = "approval"  # allowed, but only after explicit user approval
    warning = "warning"


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str
    path: str = ""


@dataclass
class EnvironmentEstimate:
    name: str
    cpu: float
    memory_mib: int
    storage_gib: int


@dataclass
class Plan:
    valid: bool
    findings: list[Finding] = field(default_factory=list)
    environments: list[EnvironmentEstimate] = field(default_factory=list)
    total_cpu: float = 0
    total_memory_mib: int = 0
    total_storage_gib: int = 0
    manifest: dict | None = None

    @property
    def requires_approval(self) -> bool:
        return any(f.severity == Severity.approval for f in self.findings)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["requires_approval"] = self.requires_approval
        return data


def _errors_from_validation(exc: ValidationError) -> list[Finding]:
    out = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"])
        out.append(Finding(Severity.error, "schema", err["msg"], loc))
    return out


def parse_manifest_text(text: str, max_bytes: int) -> tuple[UniverseManifest | None, list[Finding]]:
    if len(text.encode("utf-8")) > max_bytes:
        return None, [Finding(Severity.error, "too-large", f"manifest exceeds {max_bytes} bytes")]
    try:
        raw = load_yaml_text(text, source="manifest")
    except ManifestParseError as exc:
        return None, [Finding(Severity.error, "parse", str(exc))]
    return parse_manifest_data(raw)


def parse_manifest_data(raw: object) -> tuple[UniverseManifest | None, list[Finding]]:
    if not isinstance(raw, dict):
        return None, [Finding(Severity.error, "schema", "manifest must be a mapping")]
    try:
        return UniverseManifest.model_validate(raw), []
    except ValidationError as exc:
        return None, _errors_from_validation(exc)


def evaluate(manifest: UniverseManifest, registry: Registry, quotas: QuotaSettings) -> Plan:
    findings: list[Finding] = []
    estimates: list[EnvironmentEstimate] = []
    envs = manifest.spec.environments

    if len(envs) > quotas.max_environments:
        findings.append(
            Finding(
                Severity.error,
                "quota.environments",
                f"{len(envs)} environments exceed the quota of {quotas.max_environments}",
                "spec.environments",
            )
        )

    for i, env in enumerate(envs):
        p = f"spec.environments.{i}"
        base = registry.base_images.get(env.base_image)
        if base is None:
            findings.append(
                Finding(
                    Severity.error,
                    "registry.base-image",
                    f"unknown base image {env.base_image!r}",
                    f"{p}.baseImage",
                )
            )

        need_cpu = base.min_resources.cpu if base else 0
        need_mem = base.min_resources.memory_mib if base else 0
        need_disk = base.min_resources.storage_gib if base else 0

        for j, pack_id in enumerate(env.tool_packs):
            pp = f"{p}.toolPacks.{j}"
            pack = registry.tool_packs.get(pack_id)
            if pack is None:
                findings.append(
                    Finding(
                        Severity.error, "registry.tool-pack", f"unknown tool pack {pack_id!r}", pp
                    )
                )
                continue
            if base and base.id not in pack.compatible_bases:
                findings.append(
                    Finding(
                        Severity.error,
                        "registry.incompatible",
                        f"tool pack {pack_id!r} does not support base image {base.id!r} "
                        f"(supported: {', '.join(pack.compatible_bases)})",
                        pp,
                    )
                )
            need_cpu = max(need_cpu, pack.min_resources.cpu)
            need_mem = max(need_mem, pack.min_resources.memory_mib)
            need_disk += pack.min_resources.storage_gib
            if pack.requires_isolation and env.network.mode not in (
                NetworkMode.isolated,
                NetworkMode.lab,
            ):
                findings.append(
                    Finding(
                        Severity.error,
                        "security.isolation",
                        f"tool pack {pack_id!r} requires network mode 'isolated' or 'lab'",
                        f"{p}.network.mode",
                    )
                )
            if pack.risk == RiskLevel.high:
                findings.append(
                    Finding(
                        Severity.approval,
                        "security.high-risk-pack",
                        f"tool pack {pack_id!r} is high-risk; use only on systems you are "
                        "authorized to test",
                        pp,
                    )
                )

        if env.access.desktop:
            if base and not base.supports_desktop:
                findings.append(
                    Finding(
                        Severity.error,
                        "access.desktop",
                        f"base image {base.id!r} does not support remote desktop",
                        f"{p}.access",
                    )
                )
            need_mem += DESKTOP_OVERHEAD_MIB

        r = env.resources
        if r.cpu < need_cpu:
            findings.append(
                Finding(
                    Severity.error,
                    "resources.cpu",
                    f"cpu {r.cpu} is below the required minimum {need_cpu}",
                    f"{p}.resources.cpu",
                )
            )
        if r.memory_mib < need_mem:
            findings.append(
                Finding(
                    Severity.error,
                    "resources.memory",
                    f"memoryMiB {r.memory_mib} is below the required minimum {need_mem}",
                    f"{p}.resources.memoryMiB",
                )
            )
        if r.storage_gib < need_disk:
            findings.append(
                Finding(
                    Severity.error,
                    "resources.storage",
                    f"storageGiB {r.storage_gib} is below the required minimum {need_disk}",
                    f"{p}.resources.storageGiB",
                )
            )
        if r.cpu > quotas.max_cpu_per_environment:
            findings.append(
                Finding(
                    Severity.error,
                    "quota.cpu",
                    f"cpu {r.cpu} exceeds per-environment quota {quotas.max_cpu_per_environment}",
                    f"{p}.resources.cpu",
                )
            )
        if r.memory_mib > quotas.max_memory_mib_per_environment:
            findings.append(
                Finding(
                    Severity.error,
                    "quota.memory",
                    f"memoryMiB {r.memory_mib} exceeds per-environment quota "
                    f"{quotas.max_memory_mib_per_environment}",
                    f"{p}.resources.memoryMiB",
                )
            )

        if env.network.mode == NetworkMode.internet:
            findings.append(
                Finding(
                    Severity.approval,
                    "security.internet-egress",
                    "environment requests unrestricted outbound internet access",
                    f"{p}.network.mode",
                )
            )
        lc = env.lifecycle
        if lc.ttl_hours and lc.ttl_hours > quotas.max_ttl_hours:
            findings.append(
                Finding(
                    Severity.error,
                    "quota.ttl",
                    f"ttlHours {lc.ttl_hours} exceeds quota {quotas.max_ttl_hours}",
                    f"{p}.lifecycle.ttlHours",
                )
            )
        if lc.idle_timeout_minutes is None and not lc.ephemeral:
            findings.append(
                Finding(
                    Severity.warning,
                    "resources.no-idle-shutdown",
                    "no idle timeout: this environment will consume resources until stopped",
                    f"{p}.lifecycle",
                )
            )

        estimates.append(EnvironmentEstimate(env.name, r.cpu, r.memory_mib, r.storage_gib))

    total_cpu = sum(e.cpu for e in estimates)
    total_mem = sum(e.memory_mib for e in estimates)
    ws = manifest.spec.workspace
    total_disk = sum(e.storage_gib for e in estimates) + (ws.storage_gib if ws.persistent else 0)

    if total_cpu > quotas.max_total_cpu:
        findings.append(
            Finding(
                Severity.error,
                "quota.total-cpu",
                f"total cpu {total_cpu} exceeds quota {quotas.max_total_cpu}",
            )
        )
    if total_mem > quotas.max_total_memory_mib:
        findings.append(
            Finding(
                Severity.error,
                "quota.total-memory",
                f"total memoryMiB {total_mem} exceeds quota {quotas.max_total_memory_mib}",
            )
        )
    if total_disk > quotas.max_total_storage_gib:
        findings.append(
            Finding(
                Severity.error,
                "quota.total-storage",
                f"total storage {total_disk} GiB exceeds quota {quotas.max_total_storage_gib}",
            )
        )

    return Plan(
        valid=not any(f.severity == Severity.error for f in findings),
        findings=findings,
        environments=estimates,
        total_cpu=total_cpu,
        total_memory_mib=total_mem,
        total_storage_gib=total_disk,
        manifest=manifest.to_portable(),
    )


def plan_from_text(text: str, registry: Registry, quotas: QuotaSettings, max_bytes: int) -> Plan:
    manifest, findings = parse_manifest_text(text, max_bytes)
    if manifest is None:
        return Plan(valid=False, findings=findings)
    return evaluate(manifest, registry, quotas)
