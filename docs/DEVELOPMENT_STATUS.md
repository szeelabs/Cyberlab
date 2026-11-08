# Development Status

The single source of truth for what is **built**, what is **not**, and what comes next.
Update it whenever status changes. Only mark something done after it has been tested.

_Last verified: 2026-11-08. Phase 1 on a fresh clone of `szeelabs/Cyberlab`; Phase 2 part 1
on a real Docker Engine 29.9 (Linux), Python 3.13._

## Verification results
| Check | Result |
|---|---|
| `pip install -e '.[dev]'` on a clean clone | ❌ **failed** on commit `eb60b1d` → fixed (see Known issues #1), ✅ after fix |
| Lint (`ruff check`, `ruff format --check`) | ✅ clean |
| Example manifests validate | ✅ 3/3 valid |
| Committed JSON Schema matches models | ✅ in sync |
| Test suite (Phase 1 + 2) | ✅ 83 passed, 0 failed, including 3 against real Docker |
| Real-Docker end-to-end via HTTP API (import → provision → exec → destroy) | ✅ |
| Live server smoke test (`/healthz`, `/readyz`, create workspace, validate manifest) | ✅ |
| GitHub Actions CI (PR #1, Phase 1) | ✅ passed on Python 3.11 and 3.13 |
| GitHub Actions real-Docker job | ⏳ added with Phase 2, not run on GitHub yet |

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
| Networking | `isolated` → no network; `lab` → private internal network per workspace; `restricted-egress` → same as lab (fails closed, no proxy yet); `internet` → only with a recorded approval | `execution/docker.py` |
| Approvals | View plan and required approvals; approve exactly the required items per revision; audited | `GET /plan`, `POST /approvals` |
| Lifecycle | Provision all environments of a revision; start, stop, destroy (keep or delete workspace data); refresh state from Docker; failures recorded, not hidden; last destroy removes networks | `api/environments.py` |

## Not built yet (do not describe these as working)
| Feature | Planned phase |
|---|---|
| Browser terminal (WebSocket gateway) | 2 |
| Tool pack image builds: environments run the **base image only**; packs are recorded but not installed (`toolPacksInstalled: false`) | 2 |
| Remote desktop | 6 |
| Storage size limits (`storageGiB` is validated but not enforced by Docker) | 3 |
| Egress proxy for `restricted-egress` | 2 |
| Persistent workspace storage, snapshots, export bundles | 3 |
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
5. The work is on a branch; `main` still holds only the initial commit until PR #1 is merged.
6. Docker Hub rate-limits shared cloud IPs. Use `CYBERLAB_IMAGE_MIRROR=mirror.gcr.io`.
7. **Fixed during Phase 2:** new Docker volumes are owned by root, so the unprivileged user
   couldn't write to `/workspace`. Fixed with a minimal-privilege ownership step and a
   `nocopy` mount (Docker otherwise resets ownership from the image).

## Next milestone
1. Confirm CI (including the real-Docker job) passes on PR #1, which now carries Phase 1
   and Phase 2 part 1.
2. **User:** review and merge PR #1 into `main`.
3. **Phase 2 part 2: browser terminal**, a WebSocket gateway to `docker exec` with
   short-lived, per-environment tokens.
4. Then tool pack images (build, scan, publish) and the egress proxy.
