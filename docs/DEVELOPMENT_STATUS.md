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
| Tests | 57 backend tests (screening both ways, bad model output, policy integration, Anthropic request shape and every error path with a fake client, settings guards, API, audit) and a browser test | `backend/tests/test_architect.py`, `frontend/e2e/app.spec.ts` |

**Not tested:** the real Anthropic API. No key was used; every Anthropic test uses a fake
client. Treat the `anthropic` planner as untested until someone runs it with a real key.

## Implemented (Phase 6, step 3 — remote desktop)
| Area | What works | Where |
|---|---|---|
| Desktop in the machine | `access.desktop: true` adds a light desktop to the image: Xvfb (virtual screen, 1280x800), Openbox (window manager, right-click menu), xterm, and x11vnc. A launcher starts it on first use and reuses it after. Machines with a desktop get `--init` and `DISPLAY=:1`, so graphical programs started from the terminal show on the desktop | `execution/desktop.sh`, `execution/images.py`, `execution/docker.py` |
| Connection | `POST .../desktop` gives a single-use desktop ticket; `/v1/desktop` carries VNC over the WebSocket, through `docker exec` to `x11vnc -inetd` (via socat). **No network port is opened**, so it also works on your own server over SSH. Tickets now have a purpose (migration 0003): terminal and desktop tickets are not interchangeable. Desktop sessions count toward the same per-user session limit and keep the machine "in use" for the reaper | `api/desktop.py`, `api/terminal.py`, `migrations/versions/0003_*` |
| Web app | "Desktop" button for machines with a desktop; noVNC viewer (loaded only when opened), full screen; **copy and paste** both ways through a Clipboard box (send text to the desktop, paste there with middle-click or Shift+Insert; text copied on the desktop appears in the box, with "Copy to my computer"); "Remote desktop" checkbox in the beginner form (only on systems that support it, and it asks for Medium or larger); "Describe your lab" understands "desktop"/"graphical" | `components/Desktop.tsx`, `desktopChannel.ts`, `components/ManifestBuilder.tsx`, `architect/rules.py` |
| Systems | Ubuntu 24.04, Kali, Parrot OS and BlackArch (desktop builds and a VNC handshake in a hardened container, in CI job `desktop-images`) | `registry/base-images.yaml`, `.github/workflows/ci.yml` |
| Tests | Backend: desktop API (binary bridge, audit, ticket purpose swap refused, single use, owner only, origin, session limit, open failure, policy), recipe and run args; real Docker: API → WebSocket → VNC handshake twice, desktop runs as `cyberlab`, nothing listens on a TCP port; real browser: open the desktop, check the screen is drawn, type into its terminal and find the created file in Files | `tests/test_desktop.py`, `tests/test_docker_integration.py`, `frontend/e2e/app.spec.ts` |

## Implemented (Phase 5, step 2 — troubleshooter and evaluation set)
| Area | What works | Where |
|---|---|---|
| Troubleshooter | "What's wrong?" on every machine. `GET .../environments/{id}/diagnose` explains in plain words, with a fix: provider not connected, out of memory (also when only a program inside was killed and the machine still runs), build failures (package missing for that system, downloads failed, timeouts), internet not approved, stopped by the idle timer, removed by its time limit, crashed, disappeared, stuck creating, expiring soon. Rule-based, free, no AI. Reads state, the last error, Docker's container state and the reaper's audit records; changes nothing | `troubleshoot.py`, `api/troubleshoot.py`, `components/Troubleshoot.tsx` |
| Evaluation set | 20 learner requests with what any acceptable draft must contain (system, tools, network, desktop, approval, machine count), including refusals both ways. `cyberlab architect eval` scores the configured planner (the paid Claude planner only with `--yes`). The built-in planner passes 20/20, enforced by a test | `architect/eval_prompts.yaml`, `architect/evaluate.py`, `cli.py` |
| Tests | 27 troubleshooter rules and API tests; real Docker: a program eats all memory and the troubleshooter says so; browser test: "What's wrong?" on a running machine; 15 evaluation tests, including bad drafts the checker must catch | `tests/test_troubleshoot.py`, `tests/test_architect_eval.py`, `tests/test_docker_integration.py`, `frontend/e2e` |

## Implemented (Phase 7, step 1 — getting started and releases)
| Area | What works | Where |
|---|---|---|
| Beginner guide | From nothing to a running Kali lab in a Codespace: what to click, what to expect, free-limit habits, what to do when something goes wrong; linked from the README | `docs/guides/getting-started.md` |
| Contributor guide | Web app and real-Docker tests, adding tool packs (Arch names, CI coverage, planner keywords and evaluation cases), adding operating systems, releases | `CONTRIBUTING.md` |
| Versions | `cyberlab --version`; `CHANGELOG.md`; a test keeps the backend and web app versions and the changelog in step | `cli.py`, `tests/test_release.py` |
| Claude Code Skill | `.claude/skills/cyberlab-manifest/SKILL.md`: how to write and validate manifests with real registry IDs, the safest network, sizes, the desktop, and the authorized-practice rule. A test checks its example manifest passes the policy engine | `.claude/skills/`, `tests/test_release.py` |
| Releases | Pushing a tag `vX.Y.Z` runs `release.yml`: checks the tag matches the version, runs lint and tests, builds the web app and the Python package, and publishes a GitHub release with notes from the changelog. **Not run yet**: no tag has been pushed (the maintainer decides when) | `.github/workflows/release.yml` |

## Implemented (ARM servers)
| Area | What works | Where |
|---|---|---|
| Processor types | Each operating system lists its `architectures` (Ubuntu, Debian, Kali, Parrot OS: amd64 and arm64; BlackArch: amd64 only). The Docker adapter reports the server's processor type (`/v1/system/execution` → `architecture`). Creating a machine whose system isn't published for that processor is refused up front with a plain message; the beginner form shows such systems as unavailable; "What's wrong?" explains processor-type errors | `registry/base-images.yaml`, `execution/docker.py`, `api/environments.py`, `components/ManifestBuilder.tsx`, `troubleshoot.py` |
| Tested on real ARM | CI jobs on GitHub's arm64 runners: `arm64-images` builds Ubuntu, Debian, Kali and Parrot OS with every tool pack (and the desktop on the systems that offer it), checks each tool runs as the unprivileged user and completes a VNC handshake; `arm64-integration` runs the whole real-Docker test suite on arm64 | `.github/workflows/ci.yml` |

This is what makes free ARM servers such as Oracle Cloud's Always Free tier (4 ARM cores,
24 GB RAM, 200 GB disk) a supported place to run machines, through the SSH guide. Not tested:
an actual Oracle Cloud account (that needs the user's sign-up).

## Implemented (install your own software)
| Area | What works | Where |
|---|---|---|
| Large uploads | File uploads stream through a temporary file into the machine (never held in memory), up to `CYBERLAB_MAX_UPLOAD_BYTES` (2 GiB). Downloads keep the 25 MiB limit | `api/files.py`, `execution/docker.py`, `config.py` |
| Install a .deb | Upload a `.deb` (e.g. Cisco Packet Tracer, downloaded with your own Cisco account) and click **Install** in the Files tab. The machine's software is read-only, so CyberLab builds a new image (the current one plus the package, installed as root only during that build; the build context holds only the package, read out by the no-network, unprivileged helper), then recreates the machine from it. `/workspace` is kept; the machine still runs unprivileged and read-only. Checks: `.deb` only (file signature checked), Ubuntu/Debian/Kali/Parrot only, not on machines with allow-listed websites (their proxy credentials can't be re-issued per machine yet). Audited with the package's SHA-256 | `api/install.py`, `execution/docker.py` (`install_package`), Files tab |
| Tests | 11 API tests (rebuild + recreate keeping the volume, bad paths, wrong system, build failure leaves the machine untouched, not a .deb, too large, recreate failure, ownership); real Docker: a real `.deb` built with dpkg-deb is uploaded, installed, and its command runs, files kept, still uid 1000 and read-only; browser test: upload, Install, confirm, run it in the terminal | `tests/test_install.py`, `tests/test_files.py`, `tests/test_docker_integration.py`, `frontend/e2e` |

**Not tested:** Cisco Packet Tracer itself (it can only be downloaded with a Cisco account).
Its installer may ask to accept a licence during installation; if it fails non-interactively,
"What's wrong?" and the error message will show it. Installs last until the machine is
destroyed: creating the environments again from the manifest starts without them.

## Implemented (Phase 6, step 4 — real sign-in)
| Area | What works | Where |
|---|---|---|
| Server | `CYBERLAB_AUTH_SUPABASE_URL` + `CYBERLAB_AUTH_SUPABASE_PUBLISHABLE_KEY` turn on Supabase sign-in; the JWKS URL, issuer and algorithms (ES256/RS256) are derived. Public `GET /v1/system/auth` tells the web app; the web app's CSP allows exactly that Supabase address. Guards: Supabase URLs only (or localhost outside production), https in production, the key is required | `config.py`, `api/routes.py`, `main.py` |
| Web app | Sign-in screen (email + password), create account (with the email-confirmation message), plain-language errors, automatic token refresh shortly before expiry, back to sign-in on a revoked session or any 401, signed-in email and Sign out in the header. Talks to Supabase's REST API directly (no extra library). Development mode is unchanged | `src/auth.ts`, `src/pages/SignIn.tsx`, `src/App.tsx` |
| Tests | Backend: ES256 tokens checked through a real local JWKS server: valid token, wrong issuer, wrong audience, expired, other key, unknown key id, `alg: none`, and a hand-forged HS256 token using the public key; settings guards; the public config; the real project's key list. Web: 8 unit tests (incl. the real Supabase error format). Browser: a stand-in for Supabase signing real ES256 tokens and a second CyberLab in JWT mode: wrong password, create account, "confirm your email", confirm, sign in, create a workspace, reload, sign out, API refuses without a token | `tests/test_supabase_auth.py`, `src/auth.test.ts`, `e2e/fake-supabase.mjs`, `e2e/app.spec.ts` |
| Forgotten passwords | **Forgot password?** on the sign-in screen sends a Supabase reset email back to this CyberLab; the link opens **Choose a new password** (token checked with Supabase first, then removed from the address; works in a new tab or one already open); mismatched passwords, reused passwords and expired links get plain messages. Browser test: request, follow the link, set the password, old one refused, new one works, expired link explained | `src/auth.ts`, `src/pages/ResetPassword.tsx`, `src/pages/SignIn.tsx`, `src/App.tsx`, `e2e/fake-supabase.mjs` |
| Guide | Turning it on, Site URL and Redirect URLs for email links, forgotten passwords, free-plan email limits | `docs/guides/sign-in.md` |

The user's Supabase project is connected to Claude (connector) and its address and public key
are known; they are **not** committed to the repository (each CyberLab owner sets their own).

## Implemented (Phase 8, step 1 — ready-made labs)
| Area | What works | Where |
|---|---|---|
| Lab format | `registry/labs/<id>.yaml`: title, level, minutes, topic, skills, introduction, the machines (a normal manifest `spec`), setup files and tasks with hints. Checked at startup: valid manifest, passes the policy engine with no approval needed, only `isolated`/`lab` networks, relative paths inside `/workspace` only, every task's answer placed or generated, known generators only (lab files never contain code). Quotas are the server owner's and are checked when a lab starts | `backend/src/cyberlab/labs/`, `registry/labs/` |
| Fresh answers | Every run gets new random flags (`CL{16 hex}`) and newly generated content: `maze` (a folder tree with one treasure), `app-log` (3000 lines, one flag), `auth-log` (an SSH log with one brute-force attacker; answers: address, account, number of failures; documentation addresses only). Only a SHA-256 of each answer (salted with the run) is stored; compared in constant time; spaces and case ignored | `labs/__init__.py`, `labs/generators.py` |
| API | `GET /v1/labs`, `GET /v1/labs/{id}` (public, no answers or setup), `POST /v1/labs/{id}/start` (one click: workspace, manifest, machines, lab files unpacked as the learner with one `tar` inside the machine; nothing is left behind if limits refuse it; a machine that fails to start is reported), `GET /v1/workspaces/{id}/lab`, `POST /v1/workspaces/{id}/lab/answer`. Audited as `lab.start` / `lab.answer` (task and right/wrong, never the answer). Deleting the workspace deletes its lab run. Migration `0004` | `api/labs.py`, `db.py`, `migrations/versions/0004_lab_runs.py` |
| Web app | **Labs** page (cards: level, topic, time, skills, Start lab); **Lab** tab in lab workspaces (introduction, progress bar, Open terminal, tasks with hints one at a time, answer box, right/wrong messages, a finished message); a pointer on the dashboard | `pages/Labs.tsx`, `components/LabTab.tsx`, `pages/Workspace.tsx` |
| Labs | *First steps in Linux* (4 tasks) and *Log detective: catch the attacker* (3 tasks), both on Ubuntu with no network | `registry/labs/` |
| Tests | Catalog loads and passes policy; 10 kinds of broken lab refused; answers fresh per run and salted; generated logs match their answers; the API (start, files placed, right/wrong, other task's flag refused, private to the owner, no provider, limits, failed machine, delete). Real Docker: the lab files are in the machine, owned by the learner, the flags read with `cat`/`find`/`grep` are accepted, the machine has no network. Real browser: start from the Labs page, wrong answer and hint, read the flag in the terminal, solved | `tests/test_labs.py`, `tests/test_docker_integration.py`, `frontend/e2e/app.spec.ts` |
| Guide | Using labs and writing your own | `docs/guides/labs.md`, `docs/plans/ready-made-labs.md` |

## Implemented (Phase 8, step 2 — practice targets)
| Area | What works | Where |
|---|---|---|
| Practice targets | Manifest `role: target` with the `lab-target` base (`python:3.13-slim`, no package downloads). The image carries CyberLab's practice program (standard-library Python) instead of a shell; it waits for its config, then answers on its ports from a fixed table (banners; HTTP routes incl. query strings, redirects, HEAD, 404). Low ports via the per-container `ip_unprivileged_port_start=0` setting, otherwise the usual hardening. Policy: `lab-target` only for targets, targets only on `lab`, no packs or desktop. The architect and the beginner form never offer `lab-target` | `execution/target_services.py`, `execution/images.py`, `execution/docker.py`, `manifest/models.py`, `manifest/policy.py`, `registry/base-images.yaml` |
| Names on the lab network | Every machine on a network gets its manifest name as a network alias, so `nmap target` / `curl http://target` work | `execution/docker.py` |
| Lockdown | Terminal, desktop, files, snapshots and installs are refused (403) for practice targets; `access.terminal: false` is now honoured for any machine | `api/environments.py` (`_require_workstation`), `api/terminal.py`, `api/files.py`, `api/snapshots.py`, `api/install.py` |
| Labs | *Scan a network with nmap* (the web server's port, SSH version with `-sV`, a hidden high port with `-p-`, its banner) and *Web recon: find the secrets* (page source, `robots.txt`, IDOR); generators `scan-target`, `web-target`; new **Network Basics** tool pack (curl, wget, netcat) | `registry/labs/`, `labs/generators.py`, `registry/toolpacks/net-basics.yaml` |
| Web app | Targets marked as "practice target" with no Terminal button; the Lab tab says to reach them by name; Files/Snapshots don't offer targets | `components/LabTab.tsx`, `pages/Workspace.tsx` |
| Tests | Practice program (banner, HTTP table, traversal is just a 404, HEAD, waiting for config, standard library only); generators match their answers; 5 policy refusals; run args (command, port setting, alias; workstations unchanged); API lockdown (6 endpoints) and `terminal: false`. Real Docker: attacker reaches the target by name on 22/80/31337 with bash only, target unprivileged with no way out. CI (with package downloads): both labs solved end to end with nmap, nc and curl | `tests/test_targets.py`, `tests/test_docker_integration.py` |

## Implemented (Phase 8, step 3 — labs at every level, progress)
| Area | What works | Where |
|---|---|---|
| Labs | 10 labs: 6 beginner, 3 intermediate (*Incident response: a hacked web server*, *Web: weak logins and leaked keys*, *Forensics: what's hiding in this file?*), 1 advanced (*Recon chain*: checkpoints instead of instructions). New generators: `crypto-basics`, `permissions`, `access-log`, `carving`, `web-login-target`, `wordlist`, `chain-target`; setup files can set permission bits; targets can answer unknown addresses on a path with a fixed reply (e.g. 401 for wrong passwords) | `registry/labs/`, `labs/generators.py`, `execution/target_services.py` |
| Progress | `GET /v1/labs/progress`: the user's best run per lab and how many labs are completed. Labs page: level and topic filters, a "completed" badge, Continue (n/m solved), Start again, and a total | `api/labs.py`, `pages/Labs.tsx` |
| Tests | Each new generator's answers checked against its content over 5 seeds; permission bits survive the archive. Real Docker: *Encodings*, *Permissions* and *Incident response* solved with exactly the hinted commands on plain Ubuntu. CI (package downloads): *Web logins*, *Recon chain* and *Forensics* solved end to end with curl, nmap and binwalk. Browser: filters and the progress total | `tests/test_more_labs.py`, `tests/test_labs.py`, `tests/test_docker_integration.py`, `frontend/e2e/app.spec.ts` |

## Implemented (one-command install)
| Area | What works | Where |
|---|---|---|
| Installer | `sudo ./scripts/install.sh`: installs Docker from the distribution if missing, builds CyberLab's image from the checkout, keeps data in the `cyberlab-data` volume, restarts with the server. Private mode listens on 127.0.0.1 only (development sign-in, for SSH tunnels). `--domain` + Supabase values: production mode behind Caddy with automatic HTTPS, CyberLab reachable only through Caddy; refused without sign-in. Settings in `/etc/cyberlab/cyberlab.env` (mode 600). `--mirror`, `--uninstall`, `--purge` | `scripts/install.sh`, `Dockerfile`, `.dockerignore` |
| Tests | CI job `installer` on a fresh Ubuntu runner: private install, web app, a real lab started through the API, rerun keeps data, public mode (HTTPS with a local certificate, 401 without a token, not reachable around Caddy, settings file mode 600), refusal without sign-in, uninstall with purge | `.github/workflows/ci.yml` |
| Guide | `docs/guides/install.md` | |

## Implemented (Phase 9, step 1 — stronger isolation)
| Area | What works | Where |
|---|---|---|
| Isolation levels | Manifest `isolation: container \| sandbox \| microvm`. `sandbox` runs the machine with gVisor (`--runtime runsc`), `microvm` with Kata Containers (`--runtime kata`); all other hardening stays. The Docker adapter reads the engine's runtimes and reports the levels it can run (`/v1/system/execution` → `isolation`); asking for a missing level is refused before anything is created, with a plain message. Runtime names configurable (`CYBERLAB_SANDBOX_RUNTIME`, `CYBERLAB_MICROVM_RUNTIME`) and validated | `manifest/models.py`, `execution/docker.py`, `api/environments.py`, `config.py` |
| Malware Analysis pack | radare2, YARA, UPX, ssdeep on Ubuntu/Kali. Tool packs can declare `minIsolation`; this one needs `microvm` **and** network `isolated`, is high-risk (needs approval), and is never drafted by the AI helper. No samples are shipped | `registry/toolpacks/malware-analysis.yaml`, `manifest/policy.py`, `architect/core.py` |
| Setup | `scripts/install-isolation.sh`: gVisor from its signed apt repository, Kata from its latest official release when KVM exists, registers both with Docker, prints each level's kernel | `scripts/install-isolation.sh`, `docs/guides/isolation.md` |
| Web app | Isolation choice in the beginner form; levels the server lacks are shown as not installed | `components/ManifestBuilder.tsx`, `manifestBuilder.ts` |
| Tests | Policy (malware pack refused in container/sandbox and on a network; allowed in an isolated microVM with approval), AI helper excludes it, runtime detection, run args per level with hardening kept, refusal of missing runtimes, validated names, API refusal and success. CI job `isolation`: the install script on a GitHub runner, then real CyberLab machines in gVisor and in Kata report their own kernel, the right runtime, the unprivileged user, a read-only system, and working files | `tests/test_isolation.py`, `tests/test_docker_integration.py`, `.github/workflows/ci.yml` |

## Implemented (Phase 9, step 2 — full virtual machines)
| Area | What works | Where |
|---|---|---|
| Full VMs | Manifest `vm: {boot, diskGiB}` with base `vm-host` (Debian + QEMU). The machine's launcher waits for the ISO to finish arriving, creates `disk.qcow2`, and boots QEMU with KVM, QEMU's seccomp sandbox, no network port (VNC on a Unix socket in `/tmp`); the remote desktop connects to that socket. Only `/dev/kvm` (and its group) is added to the usual hardening. VM network follows the machine's network mode (`isolated` = none). Policy: `vm-host` ⇔ `vm`, no packs/isolation/role, desktop required, enough storage, approval required. KVM detected on the Docker host (cached); missing KVM refused clearly | `manifest/models.py`, `manifest/policy.py`, `execution/vm.sh`, `execution/vm-desktop.sh`, `execution/images.py`, `execution/docker.py` |
| Boot media from a link | `POST/GET .../vm/media`: the server downloads an ISO into the machine (background, progress), https only, public addresses only with every redirect re-checked, size cap, optional SHA-256, published under its final name only when complete; audited | `api/vm.py` |
| Web app | Beginner form writes the VM block for "Full virtual machine"; Environments: "full virtual machine" label and an **Operating system** panel (link, checksum, progress) | `manifestBuilder.ts`, `components/VmMedia.tsx`, `pages/Workspace.tsx` |
| Tests | Policy (6 refusals + approval), image, launcher scripts, run args (only the KVM device added, memory/CPU/network), KVM probe and cache, unsafe links (http, credentials, private, metadata, loopback), checked redirects, size cap, API (approval, no KVM, download → part file → rename, wrong checksum, ownership). CI (`isolation` job, KVM): the official Alpine ISO is downloaded by the server with its checksum, QEMU runs with KVM as the unprivileged user, and its screen answers a VNC handshake through the desktop channel | `tests/test_vm.py`, `tests/test_docker_integration.py` |
| Guide | `docs/guides/virtual-machines.md` | |

## Implemented (Phase 8, step 5 — classroom mode)
| Area | What works | Where |
|---|---|---|
| Classes | A teacher creates a class for a lab and gets a join code (8 characters without look-alikes, any case and dash accepted). Students join (idempotent; refused when closed, or for the teacher), then **Start my lab**: their own workspace, machines and answers (starting again returns the same one). Teacher's roster: solved/total, completed, machines, last answer; close/open; reset a student's lab (machines and data destroyed, membership kept, fresh start). Teachers never get access to students' workspaces. Deleting a student's workspace keeps the membership. Audited. Migration `0005` | `api/classes.py`, `db.py`, `migrations/versions/0005_classrooms.py` |
| Web app | **Classes** page: join with a code, list of joined classes with progress and Start/Continue; create a class, codes listed; class page with the code, a live student table (refreshes every 10 s), open/close and reset with confirmation | `pages/Classes.tsx`, `App.tsx` |
| Tests | Codes (format, alphabet, uniqueness); whole flow with two students (separate workspaces and answers, a classmate's flag refused, the teacher's roster, the teacher refused from students' workspaces, reset and fresh start, audit); only the teacher manages; closed class, bad code, teacher joining; deleting a student's workspace. Browser: create a class, code, roster, close/open | `tests/test_classes.py`, `frontend/e2e/app.spec.ts` |
| Guide | `docs/guides/classes.md` | |

## Implemented (Phase 8, step 4 — labs written by users)
| Area | What works | Where |
|---|---|---|
| Import and share | `POST /v1/labs/import` (YAML, size-limited, 50 per user) runs the same checks as built-in labs (`parse_lab`: model, policy without approvals, `isolated`/`lab` networks only, safe paths, known generators only) and stores it under its own id (`c-…`). Private by default; `PATCH /v1/labs/{id}` shares it (public catalog, marked `community`); `DELETE` removes it; only the author can change it. `GET /v1/labs/{id}/file` downloads any built-in or shared lab file. Classes accept only built-in or shared labs. Audited | `api/labs.py`, `labs/__init__.py`, `db.py`, `migrations/versions/0006_custom_labs.py` |
| Web app | Labs page: "Write your own labs" (upload or paste, refusals with reasons, Share/Stop sharing, Delete), "yours"/"community" badges, a Lab file link on every built-in and shared lab | `pages/Labs.tsx` |
| Tests | Private → share → catalog → download → another user starts it → only the author changes it → delete; built-in files download and re-import; 6 unsafe or broken labs refused (internet, path escape, unknown generator, malware pack, oversized field, bad YAML); sign-in and size limit; classes need shared labs. Browser: refusal with reason, import, share, download, delete | `tests/test_custom_labs.py`, `frontend/e2e/app.spec.ts` |

## Implemented (Phase 8, step 6 — network segments and pivoting)
| Area | What works | Where |
|---|---|---|
| Segments | `network.segments` in `lab` mode (up to 4 short names, default one shared network): one internal Docker network per segment (`cyberlab-<key>-lab-<segment>`), the machine joins each with its name as alias; removed with the workspace | `manifest/models.py`, `execution/docker.py` |
| Link preview (SSRF) practice service | Practice targets can have a fetcher route that fetches a URL from the target's side: plain http only, only hosts listed in its config (lab machines), 3 s timeouts, 64 KiB cap; closed ports answer "could not connect", so learners can scan through it | `execution/target_services.py` |
| Lab | *Pivoting: reach the hidden network* (advanced): attacker on `dmz`, `jump` on `dmz`+`internal`, `vault` on `internal`; read vault through the preview, find its admin port by scanning through the preview, read the admin flag. Generators `pivot-jump`, `pivot-vault` | `registry/labs/pivot-ssrf.yaml`, `labs/generators.py` |
| Tests | Segment validation; networks created, joined with aliases, removed; the preview's limits (allowed hosts only, http only, closed port); the route on a target; generators. Real Docker: the attacker can't reach vault (not even its name) but can through jump's preview; segment networks are gone afterwards. CI: the lab solved end to end with curl | `tests/test_pivot.py`, `tests/test_docker_integration.py` |

## Not built yet (do not describe these as working)
| Feature | Planned phase |
|---|---|
| Tools without a distro package (ZAP, Volatility 3, Trivy, Grype, osquery): need vendor installs with pinned checksums | 2 |
| Image vulnerability scanning, SBOMs, signing, publishing to ghcr.io; cleanup of old images | 2 |
| Storage size limits (`storageGiB` is validated but not enforced by Docker) | 3 |
| Encryption key rotation | 3-4 |
| Scheduled snapshots (`snapshotPolicy: daily` / `on-stop` in the manifest are not acted on yet) | 3-4 |
| AI Lab Architect extras: other model providers (OpenAI-compatible, Ollama); reading program logs inside machines; scoring Claude on the evaluation set (needs a key) | 5 |
| Sign-in providers other than email (GitHub, Google) | 6 |
| Remote desktop extras: resizing the screen to the browser, sound, a fuller desktop (panel, file manager) | 6 |
| Container images published to a registry (ghcr.io; the installer builds locally instead); a project website; a public demo | 7 |
| Packs for penetration testing, malware analysis, AI security | needs isolation work first |

## Known issues
1. **Fixed:** `backend/pyproject.toml` referenced `../README.md` as the package readme.
   Current setuptools rejects files outside the package directory, so a clean
   `pip install -e .` failed, which would break CI and Codespaces. The line was removed.
2. **Mostly resolved:** the JWKS (ES256) path is tested with a real P-256 key served as a JWKS,
   including forged-token attacks, and CyberLab reads the **real** Supabase project's key list
   (`CYBERLAB_TEST_SUPABASE_URL`). The real project's error format and browser access (CORS)
   were checked. Still untested: a full sign-up with a real email (needs the user's inbox).
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

19. Remote desktop limits: the screen is a fixed 1280x800 (scaled to the browser); the
   desktop keeps running (and using memory) until the machine is stopped, even after the
   browser tab closes. Copy/paste is plain text only (the basic VNC clipboard, Latin-1).
20. Found while building the desktop: noVNC 1.6 cannot be bundled (top-level `await` in its
   CommonJS build); 1.7.0 is used, and the web app now targets ES2022 browsers
   (Chrome/Edge 89+, Firefox 89+, Safari 15+).

21. **Fixed (found by CI):** on BlackArch the desktop never started. Its shell (bash 5.3)
   reports success for `kill -0 ""`, so the launcher thought a screen was already running.
   The launcher now checks for a real process number first; it also stops with a clear
   message if the screen fails to start, and CI prints the desktop's logs on failure.

## Next milestone
1. Merge the troubleshooter / evaluation PR.
2. Try the `anthropic` planner with a real key (user decision: it costs money).
4. Remote desktop extras (clipboard, screen resizing); a supported way to add user-supplied
   installers (e.g. Cisco Packet Tracer) to a machine's image.

## Operating system wish list (requested by the user, not yet built unless marked)
Everything runs on a remote host, so none of it uses laptop disk. The free hosts still have
real limits (for example Codespaces: about 32 GB disk and a monthly free-hours allowance).

| Wish | Status | How it would work / why not |
|---|---|---|
| Kali Linux | **Implemented** (`kali-rolling` base image) | container, browser terminal, remote desktop; all its tool packs (including detection-engineering) are built and smoke-tested in CI |
| Parrot OS | **Implemented** (`parrot-core` base image) | official `parrotsec/core` image (Parrot Security 7, Debian-based); boot and safe user tested locally, tool-pack builds tested in CI |
| BlackArch | **Implemented** (`blackarch` base image) | official `blackarchlinux/blackarch` image; recipes use `pacman` (`family: arch`, per-tool `archPackage`); boot and safe user tested locally, tool-pack builds tested in CI |
| Predator-OS | **Possible as a full VM, not tested yet** | no container image exists (ISO only); boot its official ISO in a full virtual machine (`vm-host`, needs KVM). Full VMs are tested with Alpine's ISO, not Predator-OS itself |
| Android | **Possible as a full VM (Android-x86 / BlissOS ISO), not tested yet** | needs KVM on the server; containers (redroid) would need `--privileged` and kernel modules, which CyberLab refuses |
| Cisco Packet Tracer | **Path built, not tested with Packet Tracer itself** | download the `.deb` with your Cisco NetAcad account, upload it to a machine with a remote desktop (x86 server: Cisco publishes it for amd64 only), click Install. The install feature is tested with a real `.deb`; Packet Tracer itself is not (needs a Cisco account) |
| VMware-style labs | **Implemented** | multi-machine labs on private networks, microVMs, and full virtual machines from any ISO with their screen in the browser (VMware itself is commercial and isn't needed) |
