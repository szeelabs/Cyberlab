# Existing Project Assessment (baseline: commit `fca02c5`)

## What existed
| Item | State |
|---|---|
| `LICENSE` | MIT, 2026 — kept unchanged |
| `.gitignore` | GitHub's standard Python template — kept; CyberLab entries appended |
| Code, docs, CI, configuration | None |

The repository was a fresh initial commit, so there was no existing code to keep or migrate.
That means no legacy constraints, and also nothing already proven. Every integration has to
be built and tested from scratch.

## Notable observations
* The Python `.gitignore` ignores `lib/`, `build/`, `var/`, `dist/`. Directory names were
  chosen to avoid silently ignored source (e.g. no `frontend/lib/` — use `src/lib` with an
  explicit negation when the frontend arrives).
* No secrets, credentials or binaries were present in history.

## Missing (everything), prioritised
1. A contract the whole system can share → **Universe Manifest** spec and validator.
2. A catalogue of what can be provisioned → **registry** of base images and tool packs.
3. Identity, ownership and audit → **control-plane API foundation**.
4. Execution, storage, scheduling, AI and UI → later phases (see `06-roadmap.md`).

## Risks identified up front
* **Scope:** the vision is a whole platform. Mitigation: a strict phase plan, and each phase
  ships something working and tested.
* **Abuse potential:** remote machines running security tooling can be used to attack third
  parties. Mitigation: the policy engine, network modes, approvals and audit from day one
  (see `04-threat-model.md`).
* **Cost:** "free" compute is limited and changes often. Mitigation: provider adapters,
  quotas and user-owned infrastructure (see `05-infrastructure-strategy.md`).
* **Local constraints:** the maintainer's machine has almost no free disk. Mitigation: a
  cloud-first dev workflow (Codespaces/devcontainer), no local Kubernetes, no large images.
