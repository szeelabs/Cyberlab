"""Live quota accounting across all of a user's environments.

Manifest validation checks one manifest on its own. These checks run when environments
are provisioned or started, and count everything the user already has:

* **CPU and memory** — environments that are running or being provisioned. Stopping an
  environment frees its CPU and memory; starting it again is checked.
* **Environment count and storage** — every environment that isn't destroyed or failed
  (a stopped environment still occupies its disk).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberlab.config import QuotaSettings
from cyberlab.db import EnvironmentRecord, User, Workspace
from cyberlab.execution import EnvState

COMPUTING = {EnvState.provisioning.value, EnvState.running.value}
OCCUPYING = COMPUTING | {EnvState.stopped.value}


@dataclass
class Usage:
    environments: int = 0
    cpu: float = 0.0
    memory_mib: int = 0
    storage_gib: int = 0

    def to_dict(self) -> dict:
        return {
            "environments": self.environments,
            "cpu": round(self.cpu, 3),
            "memoryMiB": self.memory_mib,
            "storageGiB": self.storage_gib,
        }


def _resources(spec: dict) -> tuple[float, int, int]:
    r = (spec or {}).get("resources") or {}
    return float(r.get("cpu", 0)), int(r.get("memoryMiB", 0)), int(r.get("storageGiB", 0))


def usage(db: Session, user: User, *, exclude: set[str] | None = None) -> Usage:
    exclude = exclude or set()
    rows = db.scalars(
        select(EnvironmentRecord)
        .join(Workspace, Workspace.id == EnvironmentRecord.workspace_id)
        .where(Workspace.owner_id == user.id, EnvironmentRecord.state.in_(OCCUPYING))
    )
    u = Usage()
    for rec in rows:
        if rec.id in exclude:
            continue
        cpu, mem, disk = _resources(rec.spec)
        u.environments += 1
        u.storage_gib += disk
        if rec.state in COMPUTING:
            u.cpu += cpu
            u.memory_mib += mem
    return u


def limits(q: QuotaSettings) -> dict:
    return {
        "environments": q.max_environments,
        "cpu": q.max_total_cpu,
        "memoryMiB": q.max_total_memory_mib,
        "storageGiB": q.max_total_storage_gib,
    }


def check_new(
    current: Usage, specs: list[dict], q: QuotaSettings, *, new_environments: bool = True
) -> list[str]:
    """Problems that adding (or starting) these environments would cause. Empty = allowed."""
    cpu = sum(_resources(s)[0] for s in specs)
    mem = sum(_resources(s)[1] for s in specs)
    disk = sum(_resources(s)[2] for s in specs) if new_environments else 0
    count = len(specs) if new_environments else 0
    problems = []
    if current.environments + count > q.max_environments:
        problems.append(
            f"environments: {current.environments} in use + {count} requested > "
            f"limit {q.max_environments}"
        )
    if current.cpu + cpu > q.max_total_cpu + 1e-9:
        problems.append(
            f"cpu: {current.cpu:g} in use + {cpu:g} requested > limit {q.max_total_cpu:g}"
        )
    if current.memory_mib + mem > q.max_total_memory_mib:
        problems.append(
            f"memoryMiB: {current.memory_mib} in use + {mem} requested > "
            f"limit {q.max_total_memory_mib}"
        )
    if current.storage_gib + disk > q.max_total_storage_gib:
        problems.append(
            f"storageGiB: {current.storage_gib} in use + {disk} requested > "
            f"limit {q.max_total_storage_gib}"
        )
    return problems
