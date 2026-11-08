# Repository Structure

Paths marked ✅ exist (Phase 1). Others are planned and named here so contributors put work
in the right place.

```
cyberlab/
├── README.md, LICENSE, CONTRIBUTING.md, SECURITY.md, .env.example   ✅
├── .github/workflows/ci.yml          ✅ lint, schema drift check, manifest validation, tests
├── .devcontainer/                    ✅ cloud dev environment (GitHub Codespaces)
├── docs/                             ✅ architecture, decisions, threat model, roadmap
├── spec/
│   └── universe-manifest.schema.json ✅ generated JSON Schema (v1alpha1)
├── examples/manifests/               ✅ example Universe Manifests
├── registry/                         ✅ data-only catalogue
│   ├── categories.yaml
│   ├── base-images.yaml
│   └── toolpacks/*.yaml
├── backend/                          ✅ Python control plane (FastAPI)
│   ├── src/cyberlab/
│   │   ├── config.py                 settings, quotas, security validation
│   │   ├── auth.py                   JWT/OIDC verification
│   │   ├── db.py                     SQLAlchemy models, audit log
│   │   ├── yaml_safe.py              hardened YAML loader
│   │   ├── manifest/                 manifest models + policy/estimate engine
│   │   ├── registry/                 registry models + loader
│   │   ├── api/                      HTTP routes and dependencies
│   │   ├── main.py, cli.py
│   │   ├── execution/                ⏳ Phase 2: provider adapters (docker, ...)
│   │   ├── storage/                  ⏳ Phase 3: S3-compatible workspace storage
│   │   ├── scheduler/                ⏳ Phase 4: idle shutdown, TTL reaper, quotas
│   │   └── architect/                ⏳ Phase 5: AI Lab Architect
│   └── tests/                        ✅
├── gateway/                          ⏳ Phase 2: terminal WebSocket gateway
├── images/                           ⏳ Phase 2: Dockerfiles built from tool packs
├── frontend/                         ⏳ Phase 6: Next.js + TypeScript
└── skills/cyberlab/                  ⏳ Phase 7: Claude Code Skill
```
