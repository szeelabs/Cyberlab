# Implementation Roadmap

Each phase ends with working code, tests and docs. Nothing is marked done until it has
been tested.

## Phase 1 — Universe Foundation ✅ (this change)
- [x] Repository structure, CI, devcontainer, contribution and security policies
- [x] Universe Manifest v1alpha1: Pydantic models, generated JSON Schema, examples
- [x] Registry: 10 categories, 3 base images, 7 metadata-only tool packs, cross-validation
- [x] Policy engine: registry resolution, compatibility, minimum resources, quotas,
      isolation/egress rules, approval findings, resource estimate
- [x] API: health/readiness, public registry, manifest validation, workspaces,
      manifest import/export with revisions, audit log, `/v1/me`
- [x] Security foundations: JWT verification, settings guards, hardened YAML, size limits,
      security headers, request IDs, owner isolation
- [x] CLI: `cyberlab manifest validate|schema`, `cyberlab registry`

## Phase 2 — Execution Engine (in progress)
- [x] 1. `ProviderAdapter` protocol + `DockerAdapter` (local or remote via `DOCKER_HOST`) with
      hardening flags; real-Docker integration tests in CI.
- [x] 2. Approval API: `POST /v1/workspaces/{id}/approvals`, per revision, audited.
- [x] 3. Environment lifecycle API: provision / start / stop / destroy / status refresh.
- [x] 4. Tool pack images: Dockerfiles rendered from `registry/toolpacks`, built on the Docker
      host and in CI with smoke tests. Still to do: SBOM, scanning, cosign, ghcr.io publishing.
- [x] 5. Terminal gateway: WebSocket ↔ `docker exec` with single-use, short-lived tickets;
      browser test page at `/terminal`.
- [x] 6. Egress proxy for `restricted-egress` (per-environment allow-lists, audit log).

## Phase 3 — Persistent Universe ✅
- [x] 1. Workspace file API (list, upload, download, delete), audited.
- [x] 2. Snapshots: create, list, restore (integrity-checked, all-or-nothing), delete; local store.
- [x] 3. S3-compatible snapshot storage with client-side AES-256-GCM encryption.
- [x] 4. Export/import bundles (manifest + snapshots) between servers; imports treated as untrusted.
- [x] 5. Alembic migrations, PostgreSQL in CI.
Workspace volumes, file API, snapshots to S3-compatible storage (client-side encryption),
export bundle = manifest + snapshot, restore. Alembic migrations, Postgres in CI.

## Phase 4 — Resource Intelligence (in progress)
- [x] 1. Reaper: idle shutdown, TTL destroy, ephemeral cleanup (audited, `cyberlab reaper run-once`).
- [x] 2. Live quotas across a user's environments; per-client and per-IP rate limiting.
Scheduler, idle reaper and TTL reaper, live quota accounting, provider cost classes, a
user-owned-host adapter, rate limiting, audit export/retention.

## Phase 5 — AI Lab Architect
`LLMAdapter` (Anthropic / OpenAI-compatible / Ollama), prompt → structured manifest
draft restricted to registry IDs, policy engine review, approval UI, a troubleshooting
assistant that reads environment status and logs. Evaluated with a fixture prompt set.

## Phase 6 — Visual Universe
Next.js app: sign-in, workspace "desktop", environment dashboard, xterm.js terminal,
tool-universe explorer, manifest editor with live validation, optional Guacamole/noVNC.

## Phase 7 — Open Source Ecosystem
Install guides (Codespaces, single VM, BYO host), a Claude Code Skill for authoring
manifests, developer docs, a tool pack contribution guide, release automation (tags →
images + changelog).

## Exact next steps
1. Merge Phase 1 and enable branch protection on `main` with CI required.
2. Create a Supabase project (free tier) and run `/v1/me` against a real token to verify
   the JWKS path. Record the result in `03-technology-decisions.md`.
3. Start Phase 2 item 1: `backend/src/cyberlab/execution/` with `ProviderAdapter` and a
   Docker adapter that provisions the `minimal` example on a CI runner.
