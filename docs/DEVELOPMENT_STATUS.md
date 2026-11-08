# Development Status

The single source of truth for what is **built**, what is **not**, and what comes next.
Update it whenever status changes. Only mark something done after it has been tested.

_Last verified: 2026-11-08, on a fresh clone of `szeelabs/Cyberlab` branch
`claude/dazzling-brahmagupta-ds29wi`, Python 3.13._

## Verification results
| Check | Result |
|---|---|
| `pip install -e '.[dev]'` on a clean clone | ❌ **failed** on commit `eb60b1d` → fixed (see Known issues #1), ✅ after fix |
| Lint (`ruff check`, `ruff format --check`) | ✅ clean |
| Example manifests validate | ✅ 3/3 valid |
| Committed JSON Schema matches models | ✅ in sync |
| Test suite | ✅ 55 passed, 0 failed |
| Live server smoke test (`/healthz`, `/readyz`, create workspace, validate manifest) | ✅ |
| GitHub Actions CI | ⏳ has never run: the workflow only triggers on PRs and on `main` |

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

## Not built yet (do not describe these as working)
| Feature | Planned phase |
|---|---|
| Remote Linux environment provisioning (Docker adapter) | 2 |
| Approval endpoint (Phase 1 only *computes* what needs approval) | 2 |
| Environment lifecycle API (start/stop/destroy/status) | 2 |
| Browser terminal (WebSocket gateway) | 2 |
| Tool pack image builds (all packs are `metadata-only`) | 2 |
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
5. The Phase 1 work is on a branch; `main` still holds only the initial commit.

## Next milestone
**Merge Phase 1 into `main` with CI passing** (open a pull request, let GitHub Actions run,
fix anything it finds). Only then start **Phase 2, step 1**: a `ProviderAdapter` interface
plus a Docker adapter that provisions the `minimal` example in CI. Requires user approval.
