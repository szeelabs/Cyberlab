"""Ready-made labs: catalog loading, per-run content and answer checking."""

from __future__ import annotations

import hashlib
import hmac
import random
import secrets
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from cyberlab.config import QuotaSettings
from cyberlab.labs.generators import GENERATORS
from cyberlab.labs.models import PLACEHOLDER, Lab
from cyberlab.manifest.models import API_VERSION, NetworkMode, UniverseManifest
from cyberlab.manifest.policy import Severity, evaluate
from cyberlab.registry import Registry
from cyberlab.yaml_safe import load_yaml_text

__all__ = ["Lab", "LabError", "LabRunContent", "check_answer", "load_labs", "new_run"]


class LabError(Exception):
    pass


def manifest_for(lab: Lab) -> UniverseManifest:
    return UniverseManifest.model_validate(
        {
            "apiVersion": API_VERSION,
            "kind": "Universe",
            "metadata": {"name": lab.id, "description": lab.title},
            "spec": lab.spec.model_dump(mode="json", by_alias=True),
        }
    )


def _problems(lab: Lab, reg: Registry, quotas: QuotaSettings) -> list[str]:
    out = []
    plan = evaluate(manifest_for(lab), reg, quotas)
    for f in plan.findings:
        # Quotas are the server owner's choice and are checked when a lab starts; a lab
        # that doesn't fit them is refused then, with a clear message.
        if f.code.startswith("quota."):
            continue
        if f.severity in (Severity.error, Severity.approval):
            out.append(f"{f.code}: {f.message}")
    for e in lab.spec.environments:
        if e.network.mode not in (NetworkMode.isolated, NetworkMode.lab):
            out.append(f"machine {e.name!r}: labs use only the 'isolated' or 'lab' network")
    produced: set[str] = set()
    flagged = {t for f in lab.setup for _, t in PLACEHOLDER.findall(f.content or "")}
    for f in lab.setup:
        if f.generator is None:
            continue
        gen = GENERATORS.get(f.generator)
        if gen is None:
            out.append(f"setup file {f.path!r}: unknown generator {f.generator!r}")
            continue
        produced |= gen.produces
        if gen.needs_flag and f.flag_task is None:
            out.append(f"setup file {f.path!r}: generator {f.generator!r} needs a flagTask")
        if f.flag_task:
            flagged.add(f.flag_task)
    for t in lab.tasks:
        if t.answer == "flag" and t.id not in flagged:
            out.append(f"task {t.id!r}: its flag is never placed in a setup file")
        if t.answer != "flag" and t.answer not in produced:
            out.append(f"task {t.id!r}: no generator produces the answer {t.answer!r}")
    return out


def load_labs(root: Path, reg: Registry, quotas: QuotaSettings) -> dict[str, Lab]:
    """Load ``<registry>/labs/*.yaml``; every lab must pass validation and policy."""
    labs: dict[str, Lab] = {}
    problems: list[str] = []
    for path in sorted((root / "labs").glob("*.yaml")):
        try:
            lab = Lab.model_validate(load_yaml_text(path.read_text("utf-8"), source=str(path)))
        except (ValidationError, ValueError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        if lab.id != path.stem:
            problems.append(f"{path.name}: id {lab.id!r} must match the file name")
        problems += [f"{path.name}: {p}" for p in _problems(lab, reg, quotas)]
        labs[lab.id] = lab
    if problems:
        raise LabError("; ".join(problems))
    return labs


@dataclass(frozen=True)
class LabRunContent:
    files: dict[str, dict[str, bytes]]  # machine -> {path under /workspace: content}
    answers: dict[str, str]  # task id -> expected answer (plain; hash before storing)


def new_flag() -> str:
    return f"CL{{{secrets.token_hex(8)}}}"


def new_run(lab: Lab) -> LabRunContent:
    """Fresh flags and generated content for one run of a lab."""
    rng = random.Random(secrets.randbits(64))  # noqa: S311 - content variety, not secrecy
    flags = {t.id: new_flag() for t in lab.tasks if t.answer == "flag"}
    produced: dict[str, str] = {}
    files: dict[str, dict[str, bytes]] = {}
    for f in lab.setup:
        target = files.setdefault(f.environment, {})
        if f.generator is not None:
            out = GENERATORS[f.generator].run(rng, f.path, flags.get(f.flag_task or ""))
            target.update(out.files)
            produced.update(out.answers)
        else:
            text = PLACEHOLDER.sub(lambda m: flags[m.group(2)], f.content or "")
            target[f.path] = text.encode()
    answers = {t.id: flags[t.id] if t.answer == "flag" else produced[t.answer] for t in lab.tasks}
    return LabRunContent(files, answers)


def _normalize(answer: str) -> str:
    return " ".join(answer.strip().split()).casefold()


def answer_hash(run_id: str, answer: str) -> str:
    return hashlib.sha256(f"{run_id}:{_normalize(answer)}".encode()).hexdigest()


def check_answer(run_id: str, given: str, expected_hash: str) -> bool:
    return hmac.compare_digest(answer_hash(run_id, given), expected_hash)
