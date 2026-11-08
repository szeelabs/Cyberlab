# Technology Decisions

Each decision states what was chosen, why, and what would make us revisit it.

| Area | Decision | Status | Reasoning |
|---|---|---|---|
| Backend | **Python 3.11+ / FastAPI / Pydantic v2** | ✅ adopted | Typed request validation and auto-generated JSON Schema from the same models as the manifest. Most security tooling and AI SDKs are Python-first. |
| Database | **PostgreSQL** (prod), **SQLite** (dev/test) via SQLAlchemy 2.0 | ✅ adopted | Postgres has free tiers (Supabase, Neon) and JSONB for manifests. SQLite means zero local install. Alembic migrations arrive in Phase 3, before the schema changes on real data. |
| Auth | **External IdP + local JWT verification**, Supabase Auth documented default | ✅ verifier adopted; Supabase **not yet tested live** | We store no passwords. Supabase's free tier covers this, and any OIDC provider (Keycloak, Auth0, Zitadel) works by setting the JWKS URL, so there's no lock-in. |
| Manifest format | **YAML/JSON, `apiVersion`/`kind`** (Kubernetes-style), JSON Schema 2020-12 | ✅ adopted | Familiar to people who write it by hand, versionable, and editors can validate it through the schema. |
| Frontend | **Next.js + TypeScript** | ⏳ Phase 6 | Suits a heavy client app; can be hosted for free on Vercel/Cloudflare Pages. Could be swapped for Vite + React if we never use SSR. |
| Execution | **Docker first** (remote host), **microVM later** | ⏳ Phase 2 | Docker is available on free cloud VMs and Codespaces. MicroVMs (Firecracker/Kata) are required before hostile code runs, because containers share the kernel. |
| Terminal | **WebSocket gateway → `docker exec` / SSH**, xterm.js client | ⏳ Phase 2 | No inbound SSH exposure per environment; short-lived tokens per environment. |
| Remote desktop | **Apache Guacamole** (or noVNC for light use) | ⏳ Phase 6 | Guacamole is mature and protocol-agnostic but heavy (Java + guacd), so it's optional, enabled per environment. noVNC is lighter for single sessions. |
| Object storage | **S3 API** (R2 / B2 / MinIO / S3) | ⏳ Phase 3 | One interface, many free or cheap providers, and user-owned buckets. |
| Tool distribution | **OCI images on ghcr.io**, SBOM + cosign | ⏳ Phase 2 | ghcr.io is free for public images. OCI is provider-neutral. |
| AI | **`LLMAdapter` interface**, optional | ⏳ Phase 5 | Provider-independent. The platform must work fully without AI. |
| CI/CD | **GitHub Actions** | ✅ adopted | Free for public repositories. |
| Lint/format | **ruff** | ✅ adopted | One fast tool. Security rules (`S`) enabled. |

## Rejected / deferred
* **Kubernetes now:** too heavy for the free-first target and impossible locally on this
  machine. A Kubernetes adapter can come later behind `ProviderAdapter`.
* **Building our own auth:** risk with no payoff.
* **Local VMs (VirtualBox/Vagrant):** contradict the near-zero local disk constraint.
