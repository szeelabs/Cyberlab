# Security Threat Model

CyberLab hands users remote computers preloaded with security tooling. The two main risks
are **(1)** users or attackers abusing the platform to harm third parties, and **(2)**
workloads escaping isolation to reach other users or the host. Only authorized testing is
permitted; the platform enforces as much of that technically as it can, and audits the rest.

## Assets
User identities and sessions · workspace data and snapshots · manifests · provider
credentials (cloud keys, SSH keys) · host machines · platform reputation (IP addresses).

## Actors
Legitimate user · malicious user · compromised account · malicious shared manifest ·
malicious workload inside an environment · external internet attacker · supply-chain
attacker (tool images).

## STRIDE summary

| Threat | Example | Mitigation | Status |
|---|---|---|---|
| **Spoofing** | Forged JWT, `alg: none`, wrong audience | Signature, `exp`, `sub`, audience/issuer checked; `none` refused at config and verify; dev auth refused in production | ✅ tested, incl. ES256 via JWKS (forged, cross-project, expired and algorithm-confusion tokens refused) |
| **Tampering** | Malicious shared manifest (YAML tags, alias bombs, unknown fields like `privileged`) | SafeLoader subclass with aliases rejected; single document; size limits; `extra="forbid"`; no privileged/host options exist in the schema | ✅ tested |
| **Repudiation** | "I didn't start that lab" | Append-only audit log with request IDs | ✅ (export/retention: Phase 4) |
| **Information disclosure** | Reading another user's workspace | Owner-scoped queries, uniform 404; security headers; docs disabled in prod | ✅ tested |
| **DoS / cost abuse** | Huge manifests, many environments, never-stopping labs, request floods | Request/manifest size caps; manifest quotas plus live quotas across all of a user's environments; reaper enforces idle/TTL/ephemeral; per-client and per-IP rate limiting | ✅ enforced and tested |
| **Elevation of privilege** | Container escape | Non-root, cap-drop ALL, Docker's default seccomp profile, no-new-privileges, read-only rootfs, pids/CPU/memory limits; optional gVisor sandbox or Kata microVM per machine (own kernel); the malware-analysis pack only in an isolated microVM | ✅ containers, gVisor and Kata (tested on real runtimes in CI) |
| **Abuse of third parties** | Scanning or attacking external hosts from our IPs | Default network `lab` = Docker `--internal` network (no internet, tested); `internet` needs a recorded approval; `restricted-egress` goes only through the egress proxy (per-environment credentials, exact hostnames, ports 80/443, non-public addresses refused, all decisions logged); high-risk packs need approval; audit | ✅ enforced and tested on real Docker |
| **Terminal hijacking** | A malicious website opens a WebSocket to the user's terminal (cross-site WebSocket hijacking); a leaked or replayed terminal link | Single-use tickets (hash stored, 60 s), sent inside the socket not the URL; cross-site `Origin` rejected; ticket bound to one user and one running environment; session/idle/size limits; content never logged | ✅ tested |
| **Remote desktop hijacking or exposure** | A website or another user connects to someone's desktop; a VNC port reachable on the network; a terminal ticket reused for the desktop | No network port at all: VNC runs as `x11vnc -inetd` through `docker exec`, the X server has `-nolisten tcp`; desktop tickets are single-use, purpose-bound (a terminal ticket cannot open a desktop and vice versa), owner-only, `Origin`-checked, sent inside the socket; client frames capped at 1 MiB; desktop runs as the unprivileged user in the same hardened container; screen contents never logged | ✅ tested (incl. real Docker and a real browser) |
| **Path traversal / injection via the file API** | `../../etc/passwd`, `-rf`, `$(cmd)` in file names | Relative paths only, `..`/absolute/leading `-`/control characters refused; operations run inside the environment as its unprivileged user; paths passed as separate arguments, never through a shell string | ✅ tested (incl. real Docker) |
| **Malicious or corrupted snapshot archive** | Tampered stored snapshot; crafted tar with `../` or absolute paths | SHA-256 verified before restore; extraction in a no-network, no-capability helper that can only write to the volume; staging folder, swapped in only on full success, so a refused archive changes nothing | ✅ tested on real Docker |
| **Snapshot data exposed by the storage provider** | A cloud bucket is leaked, misconfigured or read by the provider | Client-side AES-256-GCM before upload (key never sent to the provider); S3 refused without a key; authenticated chunks detect tampering, truncation and splicing | ✅ tested (moto S3) |
| **Malicious import bundle** | Crafted `.cyberlab` file with path-escaping or symlink entries, a decompression bomb, forged snapshots or a manifest that breaks local policy | Strict allow-list of member names and types, never extracted by archive names, compressed bundles refused, size/count limits, checksums checked against the index, manifest re-validated by the local policy engine, nothing started on import | ✅ tested (9 hostile bundles) |
| **Docker API exposure** | Control plane talks to a remote Docker Engine over the network | `ssh://` recommended (key-based, no open Docker port); plain `tcp://` refused in production; TLS client certificates supported; dedicated host user documented | ✅ enforced; SSH path tested in CI |
| **Supply chain** | Poisoned tool image or package | Tools come only from the base distribution's signed package repositories; recipes are generated from validated registry data (package names checked against a strict pattern), built with no build context; CI builds and smoke-tests every pack. Still to do: pinned base digests, SBOM, scanning, signing | 🟨 partial |
| **Secret leakage** | API keys committed or logged | No secrets in code; `.env` gitignored; `SecretStr`; audit details exclude manifests; GitHub secret scanning | ✅ |
| **Lab answers leaked or lab targets abused** | A learner reads answers from the server or the target instead of solving the task; a practice target used as a pivot or exposed outside the lab | Answers fresh per run, stored only as salted SHA-256, compared in constant time, never logged. Practice targets: no terminal, desktop, files, snapshots or installs through the API; only on the internal `lab` network (policy refuses other networks), reached by name from the same workspace only; same hardening as every machine (unprivileged, read-only, no capabilities; the only extra is the per-container setting allowing low ports). The practice program is standard-library Python answering from a fixed table: no commands, no file access from requests, no state changes | ✅ tested (incl. real Docker) |
| **Full virtual machines** | A guest OS attacks the hypervisor; a boot-media link used to reach internal services (SSRF) | VM inside the hardened, unprivileged container with only `/dev/kvm` added; QEMU's seccomp sandbox; VNC on a Unix socket only; network per the machine's mode; approval required. Links: https only, public addresses only (re-checked on every redirect), size cap, optional SHA-256, audited. Remaining risk: KVM itself is attack surface; use a dedicated server | ✅ tested (incl. KVM in CI) |
| **AI-driven misuse** | Prompt asks the AI to build an attack environment against a real target | AI only drafts manifests (no save/approve/provision path); output re-validated against the registry and policy engine; mandatory human approval; no target fields in the manifest; attack words + public host/IP refused before any model call; drafts audited without prompt text; paid AI off unless allowed | ✅ Phase 5 step 1 (screening is a heuristic) |

## Design rules
1. **Default deny on the network.** New environments get `lab` (private) networking.
2. **Approval is explicit and recorded.** `approval` findings must be acknowledged by the
   user; the acknowledgement is audited (approval API: Phase 2).
3. **No hostile code on shared kernels.** Malware analysis stays disabled until microVM isolation exists.
4. **Least privilege for the control plane.** It talks to providers through adapters with
   scoped credentials; the API never shells out with user input.
5. **Nothing paid without consent.** Provider adapters must declare cost class, and paid
   resources need explicit opt-in.

## Known gaps (honest list)
* Rate limiting is per server process (in memory); several processes each enforce their own
  share. A shared store would be needed for one global limit.
* Storage size (`storageGiB`) is not enforced on Docker's default storage driver yet.
* JWKS path implemented but only the HS256 path is covered by automated tests.
* Egress proxy: TLS SNI is not compared with the CONNECT hostname, so a client could use
  domain fronting through an allowed CDN host. Mitigation would need TLS inspection.
