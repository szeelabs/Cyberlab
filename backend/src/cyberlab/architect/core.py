"""Architect core: the draft format, safety screening, validation and manifest building."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cyberlab.manifest.models import SLUG
from cyberlab.manifest.policy import evaluate, parse_manifest_data

if TYPE_CHECKING:
    from cyberlab.config import QuotaSettings, Settings
    from cyberlab.registry import Registry

MAX_PROMPT_CHARS = 2000

# Same sizes as the web app's beginner form.
SIZES = {
    "small": {"cpu": 0.5, "memoryMiB": 1024, "storageGiB": 4},
    "medium": {"cpu": 1, "memoryMiB": 2048, "storageGiB": 8},
    "large": {"cpu": 2, "memoryMiB": 4096, "storageGiB": 16},
}
NETWORKS = ("isolated", "lab", "restricted-egress", "internet")


class ArchitectError(Exception):
    """The draft could not be produced (bad model output, limits, ...)."""


class ArchitectRefused(ArchitectError):
    """The request is outside what CyberLab will build (e.g. targets real systems)."""


class ArchitectUnavailable(ArchitectError):
    """The planner is off, not configured, or its service could not be reached."""


class ArchitectModel(Protocol):
    name: str

    def draft(self, *, system: str, prompt: str, schema: dict) -> dict: ...


# ---------------------------------------------------------------- draft format
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DraftMachine(_Strict):
    name: str = Field(pattern=SLUG)
    base_image: str = Field(alias="baseImage")
    tool_packs: list[str] = Field(default_factory=list, alias="toolPacks", max_length=10)
    size: Literal["small", "medium", "large"] = "small"
    network: Literal["isolated", "lab", "restricted-egress", "internet"] = "lab"
    allowed_egress: list[str] = Field(default_factory=list, alias="allowedEgress", max_length=20)
    idle_timeout_minutes: int = Field(default=60, alias="idleTimeoutMinutes", ge=5, le=1440)
    ephemeral: bool = False


class DraftPlan(_Strict):
    refused: bool = False
    refusal_reason: str = Field(default="", alias="refusalReason", max_length=1000)
    name: str = Field(default="my-lab", pattern=SLUG)
    description: str = Field(default="", max_length=500)
    explanation: str = Field(default="", max_length=4000)
    environments: list[DraftMachine] = Field(default_factory=list, max_length=6)


def _buildable_packs(registry: Registry) -> list:
    return sorted(
        (p for p in registry.tool_packs.values() if p.status != "metadata-only"),
        key=lambda p: p.id,
    )


def draft_schema(registry: Registry) -> dict:
    """JSON Schema for the model's answer. Registry IDs are enums, so a model constrained
    to it cannot invent operating systems or tool packs."""
    machine = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "lowercase letters, digits, dashes"},
            "baseImage": {"type": "string", "enum": sorted(registry.base_images)},
            "toolPacks": {
                "type": "array",
                "items": {"type": "string", "enum": [p.id for p in _buildable_packs(registry)]},
            },
            "size": {"type": "string", "enum": list(SIZES)},
            "network": {"type": "string", "enum": list(NETWORKS)},
            "allowedEgress": {"type": "array", "items": {"type": "string"}},
            "idleTimeoutMinutes": {"type": "integer"},
            "ephemeral": {"type": "boolean"},
        },
        "required": [
            "name",
            "baseImage",
            "toolPacks",
            "size",
            "network",
            "allowedEgress",
            "idleTimeoutMinutes",
            "ephemeral",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "refused": {"type": "boolean"},
            "refusalReason": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "explanation": {"type": "string"},
            "environments": {"type": "array", "items": machine},
        },
        "required": [
            "refused",
            "refusalReason",
            "name",
            "description",
            "explanation",
            "environments",
        ],
        "additionalProperties": False,
    }


def system_prompt(registry: Registry) -> str:
    bases = "\n".join(
        f"- {b.id}: {b.name} ({b.family}); minimum {b.min_resources.cpu} CPU, "
        f"{b.min_resources.memory_mib} MiB RAM. {b.description}"
        for b in sorted(registry.base_images.values(), key=lambda b: b.id)
    )
    packs = "\n".join(
        f"- {p.id} (risk {p.risk.value}; works on {', '.join(p.compatible_bases)}): "
        f"{p.description} Tools: {', '.join(t.name for t in p.tools)}."
        for p in _buildable_packs(registry)
    )
    sizes = ", ".join(
        f"{k} = {v['cpu']} CPU / {v['memoryMiB']} MiB / {v['storageGiB']} GiB"
        for k, v in SIZES.items()
    )
    return f"""You are the CyberLab Lab Architect. You design practice labs for people learning
cybersecurity, many of them beginners. You only write a draft; the user reviews it, CyberLab's
policy engine checks it, and nothing starts until the user approves it.

Design a lab from the user's description using ONLY these operating systems and tool packs.

Operating systems (baseImage):
{bases}

Tool packs (toolPacks; only on the systems listed for each):
{packs}

Machine sizes: {sizes}. Pick the smallest size that meets the system's minimum.

Network modes, safest first:
- isolated: no network at all.
- lab: only the lab's own machines can talk to each other. Use this by default.
- restricted-egress: lab plus only the exact hostnames in allowedEgress (e.g. a package
  site the user names). Use only when the user needs to reach specific sites.
- internet: full internet; the user must approve it. Use only when clearly required.

Rules:
- Labs are for authorized practice only: attacking machines inside the lab, CTFs, or the
  user's own systems. If the user wants to attack, scan or break into real third-party
  systems, websites, companies or people, set refused=true, explain why in refusalReason
  in one or two kind sentences, suggest a legal practice alternative, and return no
  environments.
- Machine names: lowercase letters, digits and dashes, unique, e.g. attacker, target.
- Lab name: short, lowercase, dashes (e.g. web-practice). Description: one sentence.
- Keep labs small: at most 4 machines unless the user asks for more (never more than 6).
- explanation: 2 to 5 short, simple sentences for a beginner saying what each machine is
  for and why you chose its network. No marketing language.
- Set refusalReason to "" when not refused. Set idleTimeoutMinutes to 60 unless asked."""


# ---------------------------------------------------------------- safety screen
_ATTACK = re.compile(
    r"\b(hack|hacking|attack|attacking|exploit|exploiting|pwn|ddos|dos|brute[- ]?force|"
    r"phish|phishing|crack|cracking|break into|scan|scanning|pentest|pen-test|"
    r"penetration test|deface|take down|steal)\b",
    re.IGNORECASE,
)
_DOMAIN = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})\b", re.I)
_IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
# Reserved for testing, documentation and local use (RFC 2606, 6761, 6762, 8375).
_SAFE_SUFFIXES = (
    ".test",
    ".example",
    ".invalid",
    ".localhost",
    ".local",
    ".lab",
    ".internal",
    ".home.arpa",
    ".lan",
)
_SAFE_DOMAINS = ("example.com", "example.org", "example.net")
# File names look like domains ("notes.txt"); ignore common file extensions.
_FILE_EXT = re.compile(
    r"\.(txt|pcap|pcapng|log|py|sh|md|yaml|yml|json|zip|tar|gz|img|iso|bin|exe|doc|docx|"
    r"pdf|csv|conf|cfg|html|js|php|rules|yar)$",
    re.I,
)


def _real_targets(text: str) -> list[str]:
    out: list[str] = []
    for m in _IPV4.finditer(text):
        try:
            ip = ipaddress.ip_address(m.group(1))
        except ValueError:
            continue
        if ip.is_global:
            out.append(str(ip))
    for m in _DOMAIN.finditer(text):
        host = m.group(1).lower().rstrip(".")
        if _IPV4.fullmatch(host) or _FILE_EXT.search(host):
            continue
        if host.endswith(_SAFE_SUFFIXES) or host in _SAFE_DOMAINS:
            continue
        if any(host.endswith("." + d) for d in _SAFE_DOMAINS):
            continue
        out.append(host)
    return out


def screen_prompt(text: str) -> str | None:
    """Return a refusal reason if the request looks like an attack on real systems.

    A heuristic, applied before any model is called: attack words together with a public
    hostname or public IP address. The model is instructed to refuse such requests too,
    and nothing a draft says can run without the user's approval.
    """
    targets = _real_targets(text)
    if targets and _ATTACK.search(text):
        shown = ", ".join(sorted(set(targets))[:3])
        return (
            f"CyberLab only builds labs where you practise on machines inside the lab. "
            f"Your request mentions {shown} together with attack words, and testing real "
            f"systems needs the owner's written permission. Describe the lab without the "
            f"real address (for example 'a web server I can attack'), and CyberLab will add "
            f"a practice target for you."
        )
    return None


# ---------------------------------------------------------------- building
def plan_to_manifest(plan: DraftPlan, registry: Registry) -> dict:
    """Turn a validated draft into a manifest dict. Raises ArchitectError on bad IDs."""
    if not plan.environments:
        raise ArchitectError("the draft has no machines; try describing what you want to practise")
    envs = []
    seen: set[str] = set()
    for m in plan.environments:
        if m.name in seen:
            raise ArchitectError(f"the draft uses the machine name {m.name!r} twice")
        seen.add(m.name)
        base = registry.base_images.get(m.base_image)
        if base is None:
            raise ArchitectError(f"the draft chose an unknown operating system {m.base_image!r}")
        packs = list(dict.fromkeys(m.tool_packs))
        for pid in packs:
            pack = registry.tool_packs.get(pid)
            if pack is None or pack.status == "metadata-only":
                raise ArchitectError(f"the draft chose an unknown tool pack {pid!r}")
        network: dict = {"mode": m.network}
        if m.network == "restricted-egress":
            network["allowedEgress"] = list(dict.fromkeys(h.lower() for h in m.allowed_egress))
        envs.append(
            {
                "name": m.name,
                "baseImage": base.id,
                "resources": dict(SIZES[m.size]),
                "toolPacks": packs,
                "network": network,
                "lifecycle": {
                    "idleTimeoutMinutes": m.idle_timeout_minutes,
                    "ephemeral": m.ephemeral,
                },
            }
        )
    metadata = {"name": plan.name}
    if plan.description.strip():
        metadata["description"] = plan.description.strip()
    return {
        "apiVersion": "cyberlab.dev/v1alpha1",
        "kind": "Universe",
        "metadata": metadata,
        "spec": {"environments": envs},
    }


@dataclass
class DraftResult:
    provider: str
    refused: bool
    reason: str = ""
    explanation: str = ""
    manifest: dict | None = None
    plan: dict | None = None  # policy engine result (findings, estimate)


def draft_lab(
    prompt: str, registry: Registry, quotas: QuotaSettings, model: ArchitectModel
) -> DraftResult:
    """Draft a lab: screen the request, ask the planner, validate, run the policy engine."""
    prompt = prompt.strip()
    if len(prompt) < 3:
        raise ArchitectError("describe the lab you want in a few words")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ArchitectError(f"please keep the description under {MAX_PROMPT_CHARS} characters")
    reason = screen_prompt(prompt)
    if reason:
        return DraftResult(provider=model.name, refused=True, reason=reason)

    raw = model.draft(system=system_prompt(registry), prompt=prompt, schema=draft_schema(registry))
    try:
        plan = DraftPlan.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ArchitectError(
            f"the planner returned an invalid draft ({where}: {first['msg']})"
        ) from exc
    if plan.refused:
        return DraftResult(
            provider=model.name,
            refused=True,
            reason=plan.refusal_reason or "this request is outside what CyberLab builds",
        )
    manifest = plan_to_manifest(plan, registry)
    parsed, findings = parse_manifest_data(manifest)
    if parsed is None:
        msgs = "; ".join(f.message for f in findings[:3])
        raise ArchitectError(f"the draft is not a valid manifest: {msgs}")
    result = evaluate(parsed, registry, quotas)
    return DraftResult(
        provider=model.name,
        refused=False,
        explanation=plan.explanation,
        manifest=manifest,  # the short form; defaults are filled in when it is saved
        plan=result.to_dict(),
    )


def build_model(settings: Settings, registry: Registry) -> ArchitectModel | None:
    """The configured planner, or None when the feature is off."""
    if settings.ai_provider == "off":
        return None
    if settings.ai_provider == "anthropic":
        from cyberlab.architect.anthropic_model import AnthropicModel

        assert settings.ai_api_key is not None  # enforced by Settings
        return AnthropicModel(
            api_key=settings.ai_api_key.get_secret_value(),
            model=settings.ai_model,
            timeout=settings.ai_timeout_seconds,
        )
    from cyberlab.architect.rules import RulesModel

    return RulesModel(registry)
