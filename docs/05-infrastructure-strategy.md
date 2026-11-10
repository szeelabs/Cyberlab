# Free-First Infrastructure Strategy

**Principle:** your laptop is a window. Development, builds and environments run remotely.
No provider offers unlimited free CPU, RAM or storage, so CyberLab never promises it.
Free tiers change; check each provider's current terms before relying on one.

## Developing CyberLab with almost no local disk
| Option | What runs where | Cost |
|---|---|---|
| **GitHub Codespaces** (`.devcontainer/` provided) | Everything in the browser | Monthly free allowance for personal accounts, then paid. Set a spending limit of $0 |
| **Claude Code on the web** | Agentic dev in a cloud container | Per your plan |
| GitHub Actions | CI on every push/PR | Free for public repos |

Locally you only need a browser. The backend's test suite needs no Docker, no database
server and no network.

## Running CyberLab (target topology)
| Plane | Free-first option | Notes |
|---|---|---|
| Frontend | Vercel / Cloudflare Pages (hobby tiers) | Static/SSR hosting |
| Control plane API | A small always-free VM, or Fly.io/Render free tiers where available | Lightweight; SQLite is fine for single-user |
| Database | Supabase or Neon free Postgres | Free tiers pause or limit inactive projects |
| Auth | Supabase Auth | Free tier user limits apply |
| Execution | **User-owned host** (any Linux box with Docker reachable over SSH), Oracle Cloud Always Free Arm VM, or Codespaces for single-user labs | The scarce resource. Quotas default to fit ~1 small VM |
| Object storage | Cloudflare R2 / Backblaze B2 free allowances, or MinIO on your host | S3 API |
| Images | ghcr.io public packages | Free for public |

## Rules enforced by design
1. **Providers declare a cost class** (`free`, `user-owned`, `paid`; `CYBERLAB_EXECUTION_COST_CLASS`).
   A `paid` provider is refused unless `CYBERLAB_ALLOW_PAID_PROVIDERS=true`.
2. **Defaults fit free tiers.** Default quotas: 4 environments, 6 vCPU, 12 GiB RAM, 40 GiB
   storage total, configurable via `CYBERLAB_QUOTA_*`.
3. **Everything idles down.** `idleTimeoutMinutes` defaults to 60 and is enforced by the
   reaper (Phase 4): idle environments stop, expired ones are destroyed, ephemeral ones
   take their data with them.
4. **Bring your own infrastructure** is a first-class path: run environments on any Linux
   machine over SSH (`docs/guides/own-server.md`), tested in CI on every pull request.
5. **Nothing is deployed by this repository.** CI only tests. There are no deploy workflows.
