# Development Status

The single source of truth for what is **built**, what is **not**, and what comes next.
Update it whenever status changes. Only mark something done after it has been tested.

_Last verified: 2026-11-08. Phase 1 on a fresh clone of `szeelabs/Cyberlab`; Phase 2 parts 1-2
on a real Docker Engine 29.9 (Linux), Python 3.13, and in headless Chromium. Part 3 package
installs verified on Ubuntu 24.04 locally; every pack on Ubuntu and Debian is built in CI._

## Verification results
| Check | Result |
|---|---|
| `pip install -e '.[dev]'` on a clean clone | ❌ **failed** on commit `eb60b1d` → fixed (see Known issues #1), ✅ after fix |
| Lint (`ruff check`, `ruff format --check`) | ✅ clean |
| Example manifests validate | ✅ 3/3 valid |
| Committed JSON Schema matches models | ✅ in sync |
| Test suite (Phases 1-6) | ✅ backend 287 passed, 3 skipped locally (2 PostgreSQL tests and the package-build test; all run in CI); web app 9 unit tests and 3 browser tests (Chromium, real Docker) passed |
| Egress proxy end to end on real Docker (allowed 200, blocked 403, no credentials 407, no direct route, audit log) | ✅ |
| Real-Docker end-to-end via HTTP API (import → provision → exec → destroy) | ✅ |
| Browser terminal in real Chromium against a real container (no console errors under CSP) | ✅ |
| Live server smoke test (`/healthz`, `/readyz`, create workspace, validate manifest) | ✅ |
| GitHub Actions CI (PR #1, Phase 1) | ✅ passed on Python 3.11 and 3.13 |
| GitHub Actions real-Docker job | ✅ passed on PR #1 (merged); terminal test added since |

## Implemented (Phase 1 — Universe Foundation)
| Area | What works | Where |
|---|---|---|
| Universe Manifest v1alpha1 | YAML/JSON models, strict validation, generated JSON Schema, 3 examples | `backend/src/cyberlab/manifest/`, `spec/`, `examples/` |
| Registry | 10 categories, 5 base images (Ubuntu, Debian, Kali, Parrot OS core, BlackArch), 7 tool packs, cross-checks (Arch bases need `archPackage` names) | `registry/`, `backend/src/cyberlab/registry/` |
| Policy engine | registry/compatibility checks, minimum resources, quotas, network rules, approval findings, resource estimate | `manifest/policy.py` |
| Control-plane API | health/ready, public registry, manifest validation, owner-scoped workspaces, manifest import/export with revisions, audit log, `/v1/me` | `api/routes.py` |
| Security foundations | JWT verification, production config guards, hardened YAML loading, size limits, security headers, request IDs | `auth.py`, `config.py`, `yaml_safe.py`, `main.py` |
| CLI | `cyberlab manifest validate`, `cyberlab manifest schema`, `cyberlab registry` | `cli.py` |
| Tooling | CI workflow, Codespaces devcontainer, docs, CONTRIBUTING, SECURITY | `.github/`, `.devcontainer/`, `docs/` |

## Implemented (Phase 2, part 1 — first real environments)
| Area | What works | Where |
|---|---|---|
| Provider interface | `ProviderAdapter` protocol, `NOT CONNECTED` status when no provider is configured or reachable | `execution/base.py`, `GET /v1/system/execution` |
| Docker adapter | Hardened containers: user 1000, all capabilities dropped, no-new-privileges, read-only root, private `/tmp`, CPU/memory/pids limits, persistent `/workspace` volume; works with local or remote (`DOCKER_HOST`) engines; optional image mirror | `execution/docker.py` |
| Networking | `isolated` → no network; `lab` → private internal network per workspace; `restricted-egress` → lab network plus the egress proxy (Phase 2 part 4); `internet` → only with a recorded approval | `execution/docker.py` |
| Approvals | View plan and required approvals; approve exactly the required items per revision; audited | `GET /plan`, `POST /approvals` |
| Lifecycle | Provision all environments of a revision; start, stop, destroy (keep or delete workspace data); refresh state from Docker; failures recorded, not hidden; last destroy removes networks | `api/environments.py` |

## Implemented (Phase 2, part 2 — browser terminal)
| Area | What works | Where |
|---|---|---|
| Terminal tickets | `POST .../environments/{env}/terminal` issues a single-use ticket (60 s default) for a **running** environment you own; only its SHA-256 hash is stored | `api/terminal.py`, `terminal_tickets` table |
| WebSocket bridge | `/v1/terminal`: ticket sent as the first message (not in the URL); cross-site origins rejected; output as binary frames; input, resize, ping as JSON; idle timeout, max session length, per-user session limit, 16 KiB input frames | `api/terminal.py` |
| Shell sessions | `docker exec -it` on a real pseudo-terminal (pty); resize propagated; bash if present, else sh; starts in `/workspace` as uid 1000 | `execution/shell.py`, `execution/docker.py` |
| Audit | `terminal.ticket`, `terminal.open`, `terminal.close` (reason, duration, byte counts); **terminal content is never logged** | |
| Test page | `/terminal`: xterm.js 5.5 (vendored, MIT) with a strict Content-Security-Policy; API token kept in memory only | `static/` |

## Implemented (Phase 2, part 3 — tool pack images)
| Area | What works | Where |
|---|---|---|
| Installable tools | Each tool lists its Debian/Ubuntu `package` and a smoke-test `command`; packs with packages are `buildable`. Tools without a distro package (OWASP ZAP, Volatility 3, Trivy, Grype, osquery) are listed but **not installed yet** | `registry/toolpacks/*.yaml`, `registry/models.py` |
| Image recipes | "base image + tool packs" → a generated Dockerfile: installs packages, creates the `cyberlab` user (uid/gid 1000, home `/workspace`), `USER 1000`. Tag = hash of the Dockerfile, so images are reused and rebuilt only when the recipe changes | `execution/images.py` |
| Provisioning | Every environment now runs its built image (built once on the Docker host, Dockerfile via stdin, no build context). Build failures are reported and nothing is started. `toolPacksInstalled` and `image` are reported per environment | `execution/docker.py`, `api/environments.py` |
| CLI | `cyberlab image dockerfile`, `image commands`, `image matrix` | `cli.py` |
| CI | `image-plan` builds the matrix from the registry; `toolpack-images` builds every buildable pack on each compatible base (Ubuntu 24.04, Debian 12) and checks every tool command as `cyberlab` in a hardened, network-less container; the real-Docker job also installs a pack end to end | `.github/workflows/ci.yml` |

## Implemented (Phase 2, part 4 — egress proxy)
| Area | What works | Where |
|---|---|---|
| Proxy | Standard-library-only HTTP/HTTPS (`CONNECT`) proxy. Per-environment credentials (only SHA-256 stored), exact-hostname allow-lists, ports 80/443 only, IP literals refused, names resolving to non-public addresses refused (blocks internal network and cloud-metadata access), connects to the checked address, strips proxy credentials, `Connection: close` for plain HTTP, connection/header limits | `egress/proxy.py` |
| Placement | One proxy per workspace, hardened (uid 65534, no capabilities, read-only, 64 MiB). It sits on the private lab network (alias `egress-proxy`) and an outbound network; environments have no route out except through it. CyberLab waits until it is listening; startup failures are reported | `execution/docker.py` |
| Environments | `restricted-egress` environments get `HTTP(S)_PROXY` with their own credentials; other environments get none. If the provider can't run a proxy, those environments fail with a clear error | `api/environments.py` |
| Visibility | Every decision is logged as JSON; `GET /v1/workspaces/{id}/egress-log` shows recent allow/deny decisions with reasons | |

## Implemented (Phase 3, step 1 — workspace files)
| Area | What works | Where |
|---|---|---|
| File API | List a folder, download a file, upload (create/replace, parent folders created), delete (folders need `recursive=true`) in a **running** environment's `/workspace`. Binary-safe. Upload limit `CYBERLAB_MAX_FILE_BYTES` (25 MiB), separate from the small limit on other endpoints | `api/files.py` |
| Safety | Paths must stay inside `/workspace` (`..`, absolute paths, leading `-`, control characters refused). Operations run **inside** the environment as its own user, with the path as a separate argument (never in a shell string), so the API can do nothing the terminal couldn't. Every upload, download and delete is audited | `execution/paths.py`, `execution/docker.py` |

## Implemented (Phase 3, step 2 — snapshots)
| Area | What works | Where |
|---|---|---|
| Create | `POST .../environments/{env}/snapshots` archives `/workspace` (`.tar.gz`) using a throwaway helper container: no network, no capabilities, read-only root, **read-only** view of the workspace, environment's own user. Streamed to disk (never fully in memory), size limit `CYBERLAB_MAX_SNAPSHOT_BYTES`, per-workspace limit `CYBERLAB_MAX_SNAPSHOTS_PER_WORKSPACE`. SHA-256 recorded | `api/snapshots.py`, `execution/docker.py` |
| Restore | `POST .../environments/{env}/restore` into a **stopped** environment (any environment of the same workspace, including a re-provisioned one). Integrity checked first. Extracted into a staging folder and swapped in only if extraction fully succeeds: a corrupt or crafted archive (e.g. `../` members) is refused and the workspace is left untouched | |
| Store | `SnapshotStore` interface; `LocalSnapshotStore` keeps files under `CYBERLAB_SNAPSHOT_DIR` with owner-only permissions and atomic writes. Deleting a snapshot or its workspace deletes the data | `storage/__init__.py` |

## Implemented (Phase 3, step 3 — encrypted S3 snapshot storage)
| Area | What works | Where |
|---|---|---|
| Encryption | With `CYBERLAB_SNAPSHOT_ENCRYPTION_KEY` set (generate: `python -m cyberlab.storage.keygen`), every snapshot is encrypted **before** it is stored: AES-256-GCM in 1 MiB chunks (STREAM construction), header authenticated, so tampering, truncation, reordering and splicing are detected; the wrong key gives a clear error; only fully verified plaintext is ever written out | `storage/crypto.py` |
| S3 store | `CYBERLAB_SNAPSHOT_STORE=s3` stores snapshots in any S3-compatible service (AWS S3, Cloudflare R2, Backblaze B2, MinIO) with streaming multipart transfers. **Refuses to start without an encryption key**: snapshots never leave the server unencrypted | `storage/__init__.py` |
| Status | `GET /v1/system/storage` reports the store, whether it is encrypted, and CONNECTED / NOT CONNECTED | `api/snapshots.py` |
| Safety | Restoring a snapshot that lives in a different store than the one configured gives a clear error | |

Tested against **moto's S3 server** (the real S3 HTTP protocol, in-process). Not yet tested
against a real cloud provider account (none was created).

## Implemented (Phase 3, step 4 — export/import bundles)
| Area | What works | Where |
|---|---|---|
| Export | `GET /v1/workspaces/{id}/export?snapshots=all\|latest\|none` returns one `.cyberlab` file (uncompressed tar): `bundle.json` (workspace, manifest, snapshot index) plus each snapshot, decrypted and checksum-verified so another server with another key can import it | `api/bundles.py` |
| Import | `POST /v1/workspaces/import[?name=]` creates a new workspace from a bundle; snapshots are re-encrypted with this server's key. **Nothing is provisioned or restored automatically** | |
| Safety | Bundles are untrusted: only `bundle.json` and `snapshots/<uuid>.tar.gz`, regular files only, never extracted by archive names, size/count limits (`CYBERLAB_MAX_BUNDLE_BYTES`), compressed bundles refused, every checksum must match the index, and the manifest is re-validated by this server's policy engine and quotas | |

## Implemented (Phase 3, step 5 — database migrations)
| Area | What works | Where |
|---|---|---|
| Migrations | Alembic, shipped inside the package. Baseline `0001` creates today's schema. Startup applies pending migrations (`CYBERLAB_DATABASE_AUTO_MIGRATE`, default on); with it off, CyberLab refuses to start on an out-of-date database. `cyberlab db upgrade` / `cyberlab db current` | `migrations/`, `cli.py` |
| Adoption | Databases created before migrations existed are adopted (stamped) when all their tables are present; a partial unknown database is refused with a clear message | `migrations/__init__.py` |
| Drift check | A test fails if the models and the migrations ever disagree | `tests/test_migrations.py` |
| PostgreSQL | Migrations, downgrade/upgrade and a real API flow run against PostgreSQL 16 (locally and in a CI job) | `.github/workflows/ci.yml` |

## Implemented (Phase 4, step 1 — lifecycle enforcement: the reaper)
| Area | What works | Where |
|---|---|---|
| Idle shutdown | A running environment unused for its `idleTimeoutMinutes` is **stopped** (files kept). Activity = provisioning, starting, an **open terminal** (never idle while open), file operations, snapshots; the timer restarts when the last terminal closes | `reaper.py` |
| TTL | Environments get `expiresAt` from `ttlHours` at provisioning; once expired they are **destroyed** (running or stopped) | |
| Ephemeral | `ephemeral: true` environments are destroyed **with their workspace data** when idle or expired | |
| Operation | Runs every `CYBERLAB_REAPER_INTERVAL_SECONDS` (default 60, 0 = off) inside the server, or `cyberlab reaper run-once`. Failures are recorded and retried next run; every action is audited as `system:reaper`; the last destroy in a workspace removes its networks | `main.py`, `cli.py` |
| Data | Migration `0002` adds `last_activity_at` and `expires_at` (existing environments count as active at upgrade time) | `migrations/versions/0002_*` |

## Implemented (Phase 4, step 2 — live quotas and rate limiting)
| Area | What works | Where |
|---|---|---|
| Live quotas | When provisioning or starting, limits apply across **all** of a user's environments, not one manifest: CPU and memory count running/provisioning environments (stopping frees them, starting again is checked); environment count and storage count everything not destroyed. Refusals list each exceeded limit. `GET /v1/me/usage` shows usage and limits | `quotas.py`, `api/environments.py` |
| Rate limiting | Token bucket per client (bearer-token hash, else IP) **and** per IP (4x allowance, so rotating fake tokens doesn't escape it). `CYBERLAB_RATE_LIMIT_PER_MINUTE` (300) / `_BURST` (60); `429` with `Retry-After`; health checks and static files exempt | `ratelimit.py`, `main.py` |

## Implemented (Phase 4, step 3 — your own server, cost classes)
| Area | What works | Where |
|---|---|---|
| Own server over SSH | `CYBERLAB_DOCKER_HOST=ssh://user@host` runs environments on a machine you control; the terminal, files, snapshots and builds all go through SSH. Step-by-step guide in `docs/guides/own-server.md`. **CI runs the real-Docker suite through `ssh://` on every PR** | `execution/docker.py`, `.github/workflows/ci.yml` |
| Host safety | Only `ssh://`, `unix://`, `tcp://`, `npipe://` hosts accepted; **unencrypted `tcp://` refused in production** (the Docker API is root on that host); TLS via `CYBERLAB_DOCKER_TLS_VERIFY` + `CYBERLAB_DOCKER_CERT_PATH` | `config.py` |
| Cost classes | `CYBERLAB_EXECUTION_COST_CLASS` = `free` / `user-owned` / `paid`; a `paid` provider reports NOT CONNECTED and provisions nothing unless `CYBERLAB_ALLOW_PAID_PROVIDERS=true` | `execution/docker.py` |

## Implemented (Phase 6, step 1 — the web app)
| Area | What works | Where |
|---|---|---|
| App | React + TypeScript, built with Vite, served at `/app` (`/` redirects there); strict Content-Security-Policy with no inline scripts; shows how to build it if it isn't built yet | `frontend/`, `main.py` |
| Dashboard | Execution and storage status (CONNECTED / NOT CONNECTED), usage against limits, workspaces, create a workspace | `pages/Dashboard.tsx` |
| Workspace | **Setup:** manifest editor with Check/Save, findings and estimates, approvals. **Environments:** create, start, stop, destroy (two-step confirm, keep or delete files), browser terminal. **Files:** browse, upload, download, delete. **Snapshots:** save, restore into a stopped environment, delete. Export bundle, delete workspace | `pages/Workspace.tsx`, `components/Terminal.tsx` |
| Tool universe | Categories and tool packs with risk, compatible bases, and which tools are not installable yet | `pages/Tools.tsx` |
| Settings | API token for real sign-in, kept for the browser tab only | `pages/Settings.tsx` |
| Tests | Unit tests (Vitest), and Playwright browser tests against a real control plane: basic flow, and a full lab on real Docker (terminal command, file upload, snapshot, destroy). They fail on any script error, CSP violation or server error. CI job `web` | `frontend/e2e`, `.github/workflows/ci.yml` |

## Implemented (Phase 6, step 2 — beginner form, one-command start)
| Area | What works | Where |
|---|---|---|
| Beginner form | "Build with a form" on the Setup tab: lab name, machines (operating system, size presets, network mode with plain-English choices, allowed websites, only compatible tool packs with their risk, idle/time limits, throwaway). Shows what to fix before generating; generated YAML is written with JSON-quoted strings so no input can break it, then checked by the server automatically | `components/ManifestBuilder.tsx`, `manifestBuilder.ts` |
| One-command start | `./scripts/dev.sh`: installs the backend if needed, builds the web app, detects Docker, keeps data in `./cyberlab-data`, and listens on 127.0.0.1 only (development mode has no sign-in) | `scripts/dev.sh` |
| Codespaces | Dev container installs Python and Node, builds the web app, forwards port 8000; README has an "open in Codespace" link | `.devcontainer/devcontainer.json` |

## Implemented (Phase 5, step 1 — AI Lab Architect: drafts)
| Area | What works | Where |
|---|---|---|
| Draft from plain words | `POST /v1/architect/draft {prompt}` returns a draft manifest (YAML), a beginner explanation and the policy engine's findings. **It never saves, approves or provisions anything**; the user reviews, saves and approves as with any manifest. Audited (`architect.draft`, prompt length only, never the prompt text) | `backend/src/cyberlab/architect/`, `api/architect.py` |
| Planners | `CYBERLAB_AI_PROVIDER=rules` (default): built-in keyword planner, free and offline, **no AI**. `anthropic`: Claude via the official SDK with structured outputs constrained to registry IDs, effort `medium`, server-side refusal fallback; needs `CYBERLAB_AI_API_KEY` **and** `CYBERLAB_ALLOW_PAID_PROVIDERS=true`. `off`: disabled. `GET /v1/system/ai` reports which one | `architect/rules.py`, `architect/anthropic_model.py`, `config.py` |
| Safety | Requests that pair attack words with a public hostname or public IP are refused before any model is called (a heuristic; reserved names like `.lab`, `.test`, `example.com` and private addresses are allowed). The model is told to refuse attacks on real systems. Every draft is re-validated (unknown systems/packs, duplicate names, bad hosts rejected) and checked by the policy engine, so internet access still needs approval | `architect/core.py` |
| Web app | "Describe your lab" on the Setup tab: examples, refusal message, draft with explanation, findings, resources and YAML; "Use this draft" only fills in the editor | `frontend/src/components/Architect.tsx` |
| Tests | 55 backend tests (screening both ways, bad model output, policy integration, Anthropic request shape and every error path with a fake client, settings guards, API, audit) and a browser test | `backend/tests/test_architect.py`, `frontend/e2e/app.spec.ts` |

**Not tested:** the real Anthropic API. No key was used; every Anthropic test uses a fake
client. Treat the `anthropic` planner as untested until someone runs it with a real key.

## Not built yet (do not describe these as working)
| Feature | Planned phase |
|---|---|
| Tools without a distro package (ZAP, Volatility 3, Trivy, Grype, osquery): need vendor installs with pinned checksums | 2 |
| Kali base images are not built in CI (large image, Docker Hub limits) | 2 |
| Image vulnerability scanning, SBOMs, signing, publishing to ghcr.io; cleanup of old images | 2 |
| Remote desktop | 6 |
| Storage size limits (`storageGiB` is validated but not enforced by Docker) | 3 |
| Encryption key rotation | 3-4 |
| Scheduled snapshots (`snapshotPolicy: daily` / `on-stop` in the manifest are not acted on yet) | 3-4 |
| AI Lab Architect: troubleshooting assistant (reads environment status and logs), evaluation with a fixture prompt set, other model providers | 5 |
| Real sign-in in the web app (Supabase/OIDC login). Deferred: it can't be tested without a real provider account, so today the app takes a pasted token | 6 |
| Remote desktop (graphical environments) | 6 |
| Claude Code Skill, release automation | 7 |
| Packs for penetration testing, malware analysis, AI security | needs isolation work first |

## Known issues
1. **Fixed:** `backend/pyproject.toml` referenced `../README.md` as the package readme.
   Current setuptools rejects files outside the package directory, so a clean
   `pip install -e .` failed, which would break CI and Codespaces. The line was removed.
2. Supabase Auth has **not** been tested against a real project. Only the HS256 token path
   has automated tests; the JWKS (asymmetric key) path is untested.
3. The test client prints a Starlette deprecation warning about `httpx`. Harmless for now.
4. **Fixed in Phase 3:** the schema is now managed by Alembic migrations.
5. Work reaches `main` only through reviewed pull requests (PR #1 merged Phase 1 and Phase 2 part 1).
6. Docker Hub rate-limits shared cloud IPs. Use `CYBERLAB_IMAGE_MIRROR=mirror.gcr.io`.
7. **Fixed during Phase 2:** new Docker volumes are owned by root, so the unprivileged user
   couldn't write to `/workspace`. Fixed with a minimal-privilege ownership step and a
   `nocopy` mount (Docker otherwise resets ownership from the image).
8. **Fixed in part 3:** the terminal prompt showed `I have no name!`; images now create a real
   `cyberlab` user.
9. The `/terminal` page is the original test page; the web app at `/app` has its own terminal.
10. The first provision of a new base/pack combination builds an image, which can take minutes;
   the request waits for it. Background provisioning is a later improvement.
11. Image builds need internet access to package mirrors on the Docker host.
12. Found by CI: Suricata is not packaged in Debian 12 (bookworm) main, so the
   detection-engineering pack is offered on Ubuntu 24.04 only.
13. Egress limits: TLS SNI is not checked against the CONNECT host (domain fronting), and
   the proxy needs Docker Hub's `python` image (use `CYBERLAB_IMAGE_MIRROR`).
14. Snapshots of a running environment are taken live (like copying files while they're
   being written); stop the environment first for a perfectly consistent snapshot. Without
   `CYBERLAB_SNAPSHOT_ENCRYPTION_KEY`, local snapshots are stored unencrypted.
15. **Fixed:** a stalled Ubuntu package mirror could hang an image build indefinitely (seen
   twice in CI). Builds now use a 30 s network timeout with 3 retries, and every CI job has a
   time limit.
16. **Fixed (found by the browser tests):** a new user's first requests arriving together
   (the dashboard loads several panels at once) all tried to create the user, and all but one
   failed with a unique-constraint error. Regression test fails 5/5 without the fix.

17. **Fixed (found by CI):** Parrot's `deb.parrot.sh/parrot` address redirects to a random
   mirror, and one was unreachable, so builds failed. Parrot images now use
   `deb.parrot.sh/direct/parrot` (`sourceRewrite` in the registry).
18. The keyword planner is simple: it only knows the words in `architect/rules.py`, and the
   real-target check is a heuristic, not a guarantee. The policy engine and human approval
   remain the actual safety boundary.

## Next milestone
1. Merge the Parrot OS / BlackArch PR and the AI Lab Architect PR.
2. Try the `anthropic` planner with a real key (user decision: it costs money).
3. Phase 5 step 2: troubleshooting assistant, and a fixture prompt set to evaluate drafts.

## Operating system wish list (requested by the user, not yet built unless marked)
Everything runs on a remote host, so none of it uses laptop disk. The free hosts still have
real limits (for example Codespaces: about 32 GB disk and a monthly free-hours allowance).

| Wish | Status | How it would work / why not |
|---|---|---|
| Kali Linux | **Implemented** (`kali-rolling` base image) | container, browser terminal |
| Parrot OS | **Implemented** (`parrot-core` base image) | official `parrotsec/core` image (Parrot Security 7, Debian-based); boot and safe user tested locally, tool-pack builds tested in CI |
| BlackArch | **Implemented** (`blackarch` base image) | official `blackarchlinux/blackarch` image; recipes use `pacman` (`family: arch`, per-tool `archPackage`); boot and safe user tested locally, tool-pack builds tested in CI |
| Predator-OS | Unverified | no official container image found yet; would need checking |
| Android | Planned, later | needs an emulator with KVM (hardware virtualisation), which free cloud hosts usually lack; fits the own-server / microVM work |
| Cisco Packet Tracer | Planned, later, user supplies installer | proprietary and licensed via Cisco NetAcad, so CyberLab cannot ship it; needs the remote desktop (not built yet) |
| VMware | Not planned | it is a commercial hypervisor and cannot run inside a container; CyberLab itself plays VMware's role (machines + lab networks) |
