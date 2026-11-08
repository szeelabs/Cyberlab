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
| Test suite (Phases 1-3) | ✅ 185 passed, 1 skipped locally (the package-install test needs internet for builds; it runs in CI) |
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
| Registry | 10 categories, 3 base images, 7 tool packs (metadata only), cross-checks | `registry/`, `backend/src/cyberlab/registry/` |
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

## Not built yet (do not describe these as working)
| Feature | Planned phase |
|---|---|
| Tools without a distro package (ZAP, Volatility 3, Trivy, Grype, osquery): need vendor installs with pinned checksums | 2 |
| Kali base images are not built in CI (large image, Docker Hub limits) | 2 |
| Image vulnerability scanning, SBOMs, signing, publishing to ghcr.io; cleanup of old images | 2 |
| Remote desktop | 6 |
| Storage size limits (`storageGiB` is validated but not enforced by Docker) | 3 |
| Snapshots, S3-compatible storage, export/restore bundles | 3 |
| Database migrations (Alembic); Postgres tested in CI | 3 |
| Scheduler, idle/TTL shutdown, live quota accounting | 4 |
| Rate limiting | 4 |
| AI Lab Architect | 5 |
| Web UI, virtual desktop | 6 |
| Claude Code Skill, release automation | 7 |
| Packs for penetration testing, malware analysis, AI security | needs isolation work first |

## Known issues
1. **Fixed:** `backend/pyproject.toml` referenced `../README.md` as the package readme.
   Current setuptools rejects files outside the package directory, so a clean
   `pip install -e .` failed, which would break CI and Codespaces. The line was removed.
2. Supabase Auth has **not** been tested against a real project. Only the HS256 token path
   has automated tests; the JWKS (asymmetric key) path is untested.
3. The test client prints a Starlette deprecation warning about `httpx`. Harmless for now.
4. Tables are created at startup (`create_all`); no migrations yet.
5. Work reaches `main` only through reviewed pull requests (PR #1 merged Phase 1 and Phase 2 part 1).
6. Docker Hub rate-limits shared cloud IPs. Use `CYBERLAB_IMAGE_MIRROR=mirror.gcr.io`.
7. **Fixed during Phase 2:** new Docker volumes are owned by root, so the unprivileged user
   couldn't write to `/workspace`. Fixed with a minimal-privilege ownership step and a
   `nocopy` mount (Docker otherwise resets ownership from the image).
8. **Fixed in part 3:** the terminal prompt showed `I have no name!`; images now create a real
   `cyberlab` user.
9. The `/terminal` page is a functional test page, not the final UI (Phase 6).
10. The first provision of a new base/pack combination builds an image, which can take minutes;
   the request waits for it. Background provisioning is a later improvement.
11. Image builds need internet access to package mirrors on the Docker host.
12. Found by CI: Suricata is not packaged in Debian 12 (bookworm) main, so the
   detection-engineering pack is offered on Ubuntu 24.04 only.
13. Egress limits: TLS SNI is not checked against the CONNECT host (domain fronting), and
   the proxy needs Docker Hub's `python` image (use `CYBERLAB_IMAGE_MIRROR`).

## Next milestone
1. PR #5 (workspace files): CI green, then merge.
2. **Phase 3 step 2: snapshots.** Save a copy of an environment's `/workspace` and restore
   it later (stored on the Docker host first, then S3-compatible storage in step 3).
