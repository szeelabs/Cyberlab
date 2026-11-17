"""Score a planner on the evaluation set (``eval_prompts.yaml``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from cyberlab.architect.core import ArchitectError, ArchitectModel, draft_lab
from cyberlab.yaml_safe import load_yaml_text


@dataclass
class CaseResult:
    prompt: str
    passed: bool
    problems: list[str] = field(default_factory=list)


def load_cases(path: Path | None = None) -> list[dict]:
    text = (
        path.read_text("utf-8")
        if path
        else resources.files("cyberlab.architect").joinpath("eval_prompts.yaml").read_text("utf-8")
    )
    cases = load_yaml_text(text, source="eval_prompts.yaml")
    if not isinstance(cases, list) or not all(
        isinstance(c, dict) and isinstance(c.get("prompt"), str) for c in cases
    ):
        raise ValueError("evaluation file must be a list of {prompt, expect}")
    return cases


def check(result, expect: dict) -> list[str]:
    """Problems with one draft, compared with what the case expects."""
    p: list[str] = []
    if "refused" in expect and result.refused != expect["refused"]:
        p.append("should have been refused" if expect["refused"] else "was refused")
    if result.refused:
        return p
    envs = (result.manifest or {}).get("spec", {}).get("environments", [])
    plan = result.plan or {}
    first = envs[0] if envs else {}
    if expect.get("valid") and not plan.get("valid"):
        p.append("draft does not pass the policy engine")
    if "machines" in expect and len(envs) != expect["machines"]:
        p.append(f"{len(envs)} machines, expected {expect['machines']}")
    if "min_machines" in expect and len(envs) < expect["min_machines"]:
        p.append(f"{len(envs)} machines, expected at least {expect['min_machines']}")
    if "system_any" in expect and first.get("baseImage") not in expect["system_any"]:
        p.append(f"system {first.get('baseImage')!r}, expected one of {expect['system_any']}")
    packs = set(first.get("toolPacks", []))
    missing = set(expect.get("packs_include", [])) - packs
    if missing:
        p.append(f"missing tool packs {sorted(missing)}")
    extra = set(expect.get("packs_exclude", [])) & packs
    if extra:
        p.append(f"unwanted tool packs {sorted(extra)}")
    modes = [e.get("network", {}).get("mode") for e in envs]
    if "network" in expect and any(m != expect["network"] for m in modes):
        p.append(f"network {modes}, expected {expect['network']}")
    if expect.get("no_internet") and "internet" in modes:
        p.append("has full internet access")
    if "desktop" in expect:
        has = bool((first.get("access") or {}).get("desktop"))
        if has != expect["desktop"]:
            p.append("has a desktop" if has else "has no desktop")
    if "approval" in expect and bool(plan.get("requires_approval")) != expect["approval"]:
        p.append("needs approval" if plan.get("requires_approval") else "needs no approval")
    return p


def run(model: ArchitectModel, registry, quotas, cases: list[dict]) -> list[CaseResult]:
    out = []
    for case in cases:
        try:
            result = draft_lab(case["prompt"], registry, quotas, model)
            problems = check(result, case.get("expect") or {})
        except ArchitectError as exc:
            problems = [f"error: {exc}"]
        out.append(CaseResult(case["prompt"], not problems, problems))
    return out
