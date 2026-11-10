# CyberLab — a virtual cybersecurity universe

CyberLab aims to give every user a personal, browser-accessible virtual computer for
**authorized** security learning and work: remote Linux environments, modular tool packs,
isolated lab networks, persistent workspaces and portable configurations. Your physical
machine becomes just a window.

> **Status: Phase 2 in progress.** The control plane, Universe Manifest, registry and
> policy engine work (Phase 1). CyberLab can **start real, hardened Linux containers** on a
> Docker host from an approved manifest, with **tool packs installed**, and you can **use them
> from a browser terminal**, with internet access limited to approved sites when you choose
> `restricted-egress` (Phase 2). There is no storage, AI or full web UI yet. See [docs/DEVELOPMENT_STATUS.md](docs/DEVELOPMENT_STATUS.md).

## What works today
* **Universe Manifest** (`cyberlab.dev/v1alpha1`): a portable YAML/JSON description of
  environments, tool packs, resources, networking, workspace and preferences, with a
  generated [JSON Schema](spec/universe-manifest.schema.json).
* **Tool registry**: 10 categories, 3 base images, 7 tool packs (metadata only, no images published yet).
* **Policy engine**: registry compatibility, minimum resources, quotas, network isolation
  rules, approval requirements (internet egress, high-risk packs) and a resource estimate.
* **API**: workspaces, manifest import/export with revisions, audit log, JWT auth.
* **CLI**: `cyberlab manifest validate`, `cyberlab manifest schema`, `cyberlab registry`.

## Try CyberLab

**In your browser (nothing to install):** [open CyberLab in a GitHub Codespace](https://codespaces.new/szeelabs/Cyberlab).
When it has finished setting up, run:
```bash
./scripts/dev.sh
```
then open the forwarded port **8000** and go to `/app`. GitHub's free Codespaces allowance
applies; stop the Codespace when you're done.

**On any Linux/macOS machine** with Python 3.11+, Node.js 20+ and (optionally) Docker:
```bash
./scripts/dev.sh
```
It installs what's missing, builds the web app, and serves it on http://127.0.0.1:8000/app/.
Without Docker everything works except running environments (shown as NOT CONNECTED).

Development mode has **no sign-in**, so the script only listens on this machine
(`127.0.0.1`). For real deployments, configure sign-in (see `.env.example`).

**Run environments on your own server:** [docs/guides/own-server.md](docs/guides/own-server.md).

## Documentation
| | |
|---|---|
| [00 Assessment](docs/00-assessment.md) | Starting point and gaps |
| [01 Repository structure](docs/01-repository-structure.md) | Where things live |
| [02 Architecture](docs/02-architecture.md) | Planes and interfaces |
| [03 Technology decisions](docs/03-technology-decisions.md) | What we chose, and why |
| [04 Threat model](docs/04-threat-model.md) | Risks and mitigations |
| [05 Free-first infrastructure](docs/05-infrastructure-strategy.md) | Running on free tiers / your own hosts |
| [06 Roadmap](docs/06-roadmap.md) | Phases and next steps |
| [07 Universe Manifest spec](docs/07-universe-manifest.md) | The portable format |
| [Development status](docs/DEVELOPMENT_STATUS.md) | What is built, what isn't, known issues |
| [Learning progress](docs/LEARNING_PROGRESS.md) | Maintainer's learning log |

## API overview (Phase 1)
| Method | Path | Auth |
|---|---|---|
| GET | `/healthz`, `/readyz` | – |
| GET | `/v1/registry/categories`, `/v1/registry/base-images`, `/v1/registry/tool-packs[?category=]`, `/v1/registry/tool-packs/{id}` | – |
| POST | `/v1/manifests/validate` (YAML/JSON body) | ✔ |
| GET | `/v1/me` | ✔ |
| GET | `/v1/me/usage` (resources in use across all your environments, and your limits) | ✔ |
| GET/POST | `/v1/workspaces` | ✔ |
| GET/DELETE | `/v1/workspaces/{id}` | ✔ |
| PUT/GET | `/v1/workspaces/{id}/manifest[?format=json&revision=N]` | ✔ |
| GET | `/v1/audit` | ✔ |
| GET | `/v1/system/execution` (provider CONNECTED / NOT CONNECTED) | ✔ |
| GET | `/v1/workspaces/{id}/plan[?revision=N]` | ✔ |
| POST | `/v1/workspaces/{id}/approvals` | ✔ |
| GET/POST | `/v1/workspaces/{id}/environments[?refresh=true]` | ✔ |
| POST | `/v1/workspaces/{id}/environments/{env}/start`, `/stop` | ✔ |
| DELETE | `/v1/workspaces/{id}/environments/{env}[?keep_workspace=false]` | ✔ |
| POST | `/v1/workspaces/{id}/environments/{env}/terminal` (single-use terminal ticket) | ✔ |
| WS | `/v1/terminal` (first message: `{"type":"auth","ticket":"..."}`) | ticket |
| GET | `/terminal` (browser terminal test page) | – |
| GET | `/v1/workspaces/{id}/egress-log` (allowed/denied outbound requests) | ✔ |
| GET/DELETE | `/v1/workspaces/{id}/environments/{env}/files?path=` (list / delete) | ✔ |
| GET/PUT | `/v1/workspaces/{id}/environments/{env}/files/content?path=` (download / upload) | ✔ |
| POST | `/v1/workspaces/{id}/environments/{env}/snapshots` (save `/workspace`) | ✔ |
| GET / DELETE | `/v1/workspaces/{id}/snapshots[/{snapshot}]` | ✔ |
| POST | `/v1/workspaces/{id}/environments/{env}/restore` (stopped environment) | ✔ |
| GET | `/v1/system/storage` (snapshot store, encryption, CONNECTED / NOT CONNECTED) | ✔ |
| GET | `/v1/workspaces/{id}/export` (download a `.cyberlab` bundle) | ✔ |
| POST | `/v1/workspaces/import[?name=]` (create a workspace from a bundle) | ✔ |

To provision, set `CYBERLAB_EXECUTION_PROVIDER=docker` (local Docker, or a remote host via
`CYBERLAB_DOCKER_HOST=ssh://user@host`). Without it, the API reports **NOT CONNECTED** and
provisions nothing.

## Acceptable use
CyberLab is for learning, defense and **authorized** testing only. Use it only against
systems you own or have written permission to test. See [SECURITY.md](SECURITY.md).

## License
MIT. See [LICENSE](LICENSE). Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).
