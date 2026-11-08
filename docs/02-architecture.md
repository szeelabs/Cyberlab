# System Architecture

CyberLab is split into planes that talk only through narrow, typed interfaces. The
**Universe Manifest** is the shared contract: the user, the UI, the AI Lab Architect and the
execution engine all produce or consume the same document.

```
            Browser (Next.js)  ── terminal (xterm.js/WebSocket) ── desktop (Guacamole)
                   │ HTTPS + bearer JWT                    │
┌──────────────────▼────────────────────────────────────────▼──────────────────┐
│ A. UNIVERSE CONTROL PLANE (FastAPI)                                          │
│   identity ─ workspaces ─ manifest revisions ─ policy engine ─ audit log     │
│   approvals ─ quotas ─ session tokens for the gateway                        │
│        │ Plan (validated, approved)                ▲ status/events           │
├────────▼───────────────────────────────────────────┴────────────────────────┤
│ F. RESOURCE INTELLIGENCE   scheduler · idle reaper · TTL · provider select   │
├────────┬──────────────────────────────────────────────────────────────────── ┤
│ B. EXECUTION PLANE   ProviderAdapter interface                               │
│   docker (Phase 2) · user-owned host over SSH · microVM (Firecracker, later) │
│   per-env: container + private lab network + egress proxy + terminal agent   │
├────────┴──────────────────────────────────────────────────────────────────── ┤
│ C. STORAGE PLANE   workspace volumes · snapshots → S3-compatible bucket      │
│ D. TOOL UNIVERSE   registry/ metadata → OCI images (ghcr.io)                 │
│ E. AI LAB ARCHITECT  prompt → draft manifest → policy engine → user approval │
│ G. SECURITY  (cross-cutting) authn/z · isolation · egress policy · audit     │
└─────────────────────────────────────────────────────────────────────────────┘
```

## A. Universe Control Plane (implemented in Phase 1)
* **Identity:** no passwords stored. Bearer JWTs from an external IdP (Supabase Auth by
  default) are verified locally (HS256 secret or JWKS). The `sub` claim maps to a local
  `users` row created on first use.
* **Workspaces:** owned by exactly one user. A request for another user's workspace gets the
  same 404 as a missing one, so IDs cannot be probed.
* **Manifest revisions:** importing a manifest creates an immutable, numbered revision.
  Export returns canonical YAML/JSON. Export → import round-trips exactly (tested).
* **Policy engine** (`manifest/policy.py`): a pure function `evaluate(manifest, registry,
  quotas) -> Plan`. Findings have three severities:
  * `error` blocks the manifest;
  * `approval` is allowed only after explicit user approval (internet egress, high-risk packs);
  * `warning` is advisory (e.g. no idle shutdown).
* **Audit log:** append-only `audit_events` with actor, action, target and request ID.
  Details never contain secrets or full manifests.

## B. Execution Plane (Phase 2)
A `ProviderAdapter` protocol turns an approved Plan into running environments:
`provision(plan) / start / stop / destroy / status / exec_terminal(env)`. The first adapter
targets Docker on a remote host. Each environment gets:
* a non-root user, `--cap-drop=ALL`, `no-new-privileges`, read-only rootfs + writable
  workspace volume, cgroup CPU/memory/pids limits;
* network per manifest mode: `isolated` → `--network none`; `lab` → an internal bridge
  shared only by that universe; `restricted-egress` → lab plus an allow-list egress proxy;
  `internet` → only after approval;
* a terminal reached through the gateway with short-lived, single-environment tokens.
  Containers are never exposed directly.

Docker shares the host kernel, so it is **not** a strong boundary for hostile code. Malware
analysis and untrusted workloads stay disabled until the microVM adapter (Firecracker /
Kata / gVisor) exists. This is why the malware-analysis category has no packs.

## C. Persistent Storage Plane (Phase 3)
Workspace volumes per user; snapshots exported as tarballs to any S3-compatible store
(Cloudflare R2, Backblaze B2, MinIO, AWS S3), encrypted client-side. "Portable universe" =
manifest (configuration) + optional snapshot (data).

## D. Tool Universe
`registry/` is data, not code. A tool pack declares category, risk, isolation needs,
compatible bases, minimum resources and contents. Phase 2 adds a build pipeline that
renders each pack to an OCI image published on ghcr.io with SBOM and signatures (cosign).

## E. AI Lab Architect (Phase 5)
The model **never** provisions anything. Flow: user prompt → model drafts a manifest using
only registry IDs (structured output) → the same policy engine validates it → the UI shows
findings, cost and the approval items → the user approves → provisioning. A provider-neutral
`LLMAdapter` (Anthropic, OpenAI-compatible, local Ollama) keeps the AI layer swappable and
optional.

## F. Resource Intelligence (Phase 4)
The scheduler selects a provider that satisfies a Plan within quota, enforces
`idleTimeoutMinutes` and `ttlHours`, and reaps ephemeral environments. Quotas are already
enforced at validation time (Phase 1).

## G. Security
See `04-threat-model.md`.

## Data model (Phase 1)
`users(id, subject, email)` · `workspaces(id, owner_id, name, description)` ·
`manifest_revisions(id, workspace_id, revision, content JSON, created_by)` ·
`audit_events(id, actor_subject, action, target_type, target_id, detail, request_id)`.
