# Technology Decisions

Each decision states what was chosen, why, and what would make us revisit it.

| Area | Decision | Status | Reasoning |
|---|---|---|---|
| Backend | **Python 3.11+ / FastAPI / Pydantic v2** | ✅ adopted | Typed request validation and auto-generated JSON Schema from the same models as the manifest. Most security tooling and AI SDKs are Python-first. |
| Database | **PostgreSQL** (prod), **SQLite** (dev/test) via SQLAlchemy 2.0 | ✅ adopted | Postgres has free tiers (Supabase, Neon) and JSONB for manifests. SQLite means zero local install. Alembic migrations (Phase 3) with a drift test; PostgreSQL tested in CI. |
| Auth | **External IdP + local JWT verification**, Supabase Auth documented default | ✅ verifier adopted; Supabase **not yet tested live** | We store no passwords. Supabase's free tier covers this, and any OIDC provider (Keycloak, Auth0, Zitadel) works by setting the JWKS URL, so there's no lock-in. |
| Manifest format | **YAML/JSON, `apiVersion`/`kind`** (Kubernetes-style), JSON Schema 2020-12 | ✅ adopted | Familiar to people who write it by hand, versionable, and editors can validate it through the schema. |
| Frontend | **Vite + React + TypeScript**, served by the control plane at `/app` | ✅ adopted (Phase 6) | See below: changed from Next.js. |
| Execution | **Docker first** (remote host), **microVM later** | ⏳ Phase 2 | Docker is available on free cloud VMs and Codespaces. MicroVMs (Firecracker/Kata) are required before hostile code runs, because containers share the kernel. |
| Terminal | **WebSocket gateway → `docker exec`** on a pty, xterm.js client (vendored) | ✅ adopted | No inbound SSH exposure per environment; short-lived tokens per environment. |
| Remote desktop | **Apache Guacamole** (or noVNC for light use) | ⏳ Phase 6 | Guacamole is mature and protocol-agnostic but heavy (Java + guacd), so it's optional, enabled per environment. noVNC is lighter for single sessions. |
| Object storage | **S3 API** (R2 / B2 / MinIO / S3) | ⏳ Phase 3 | One interface, many free or cheap providers, and user-owned buckets. |
| Tool distribution | **OCI images** built from generated Dockerfiles (distro packages); ghcr.io + SBOM + cosign later | 🟨 built per host and in CI; publishing ⏳ | ghcr.io is free for public images. OCI is provider-neutral. |
| AI | **`LLMAdapter` interface**, optional | ⏳ Phase 5 | Provider-independent. The platform must work fully without AI. |
| CI/CD | **GitHub Actions** | ✅ adopted | Free for public repositories. |
| Lint/format | **ruff** | ✅ adopted | One fast tool. Security rules (`S`) enabled. |

## Web app: Vite + React instead of Next.js
The plan said Next.js, with Vite + React as the fallback "if we never use SSR". CyberLab
doesn't need server-side rendering, and Next.js's static export relies on **inline
scripts**, which the strict Content-Security-Policy (`script-src 'self'`, our main XSS
defence) forbids. Vite builds plain static files with no inline code, the control plane
serves them at `/app` (no second server to run, which suits free hosting), and the build is
small (~130 KB gzipped). The browser tests fail on any CSP violation, so this stays true.

## Egress proxy: small custom proxy instead of Squid or tinyproxy
CyberLab needs **per-environment** allow-lists with credentials, a refusal of names that
resolve to private addresses (to stop requests reaching internal infrastructure or cloud
metadata), and machine-readable logs. Squid can do most of this but is large to configure
and audit. tinyproxy has no per-client allow-lists. A ~250-line, standard-library-only
Python proxy (`backend/src/cyberlab/egress/proxy.py`) does exactly this, runs on the
official `python` image without a build, and is unit-tested in-process. Revisit if
performance or protocol coverage (e.g. HTTP/2 to the proxy) becomes a need.

## Snapshot encryption: chunked AES-256-GCM (STREAM) with `cryptography`
Snapshots can be gigabytes, so they're encrypted in 1 MiB chunks with per-chunk nonces and a
"last chunk" flag (the STREAM construction), which detects truncation and reordering while
streaming. `cryptography` is already a dependency (via PyJWT). Considered: `age` (external
binary, no Python API in the standard tooling), server-side S3 encryption (the provider holds
the key, which defeats the point). S3 storage is tested against moto's S3 server because
MinIO's community images were not reachable from the build environment.

## Parrot OS and BlackArch: official container images, two package systems
*Status: implemented.* The user asked for these by name. Both publish official container
images (`parrotsec/core`, `blackarchlinux/blackarch`), so they run the same way as Kali: a
hardened container with the `cyberlab` user, reached from the browser terminal, using no
laptop disk. Parrot is Debian-based (apt). BlackArch is Arch-based, so base images now carry a
`family` (`debian` or `arch`) and the recipe uses `pacman -Syu --needed` for Arch (Arch does
not support partial upgrades). Package names differ (`tshark` is `wireshark-cli` on Arch),
so each tool lists an explicit `archPackage`; the registry refuses an Arch-compatible pack
that leaves one out, so a wrong guess fails loudly instead of silently. Parrot's minimal
`core` image was chosen over the full security image to keep downloads small: users add
only the tool packs they need. VMware is not offered: a hypervisor cannot run inside a
container, and CyberLab's own machines and lab networks already fill that role.

## AI Lab Architect: free keyword planner by default, Claude opt-in
*Status: implemented (Claude path tested with a fake client only).* The user has no budget,
so the default planner is a small keyword matcher: free, offline, predictable, and honest
about being "no AI". Claude is used through the official `anthropic` SDK with structured
outputs: the JSON Schema lists the registry's operating systems and tool packs as enums, so
the model cannot invent them, and the server re-validates anyway. Effort is set to `medium`
explicitly, and the server-side refusal fallback (`fallbacks: "default"`) is on, because
security topics can trip safety classifiers. Using it needs a key **and**
`CYBERLAB_ALLOW_PAID_PROVIDERS=true`, the same opt-in as paid execution. The architect only
returns drafts: there is deliberately no code path from a draft to saving, approving or
provisioning.

## Rejected / deferred
* **Kubernetes now:** too heavy for the free-first target and impossible locally on this
  machine. A Kubernetes adapter can come later behind `ProviderAdapter`.
* **Building our own auth:** risk with no payoff.
* **Local VMs (VirtualBox/Vagrant):** contradict the near-zero local disk constraint.
