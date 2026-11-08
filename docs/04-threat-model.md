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
| **Spoofing** | Forged JWT, `alg: none`, wrong audience | Signature, `exp`, `sub`, audience/issuer checked; `none` refused at config and verify; dev auth refused in production | ✅ tested |
| **Tampering** | Malicious shared manifest (YAML tags, alias bombs, unknown fields like `privileged`) | SafeLoader subclass with aliases rejected; single document; size limits; `extra="forbid"`; no privileged/host options exist in the schema | ✅ tested |
| **Repudiation** | "I didn't start that lab" | Append-only audit log with request IDs | ✅ (export/retention: Phase 4) |
| **Information disclosure** | Reading another user's workspace | Owner-scoped queries, uniform 404; security headers; docs disabled in prod | ✅ tested |
| **DoS / cost abuse** | Huge manifests, many environments, never-stopping labs | Request/manifest size caps; quotas; idle/TTL fields; warnings when no idle shutdown | ✅ validation; enforcement Phase 4 |
| **Elevation of privilege** | Container escape | Non-root, cap-drop ALL, Docker's default seccomp profile, no-new-privileges, read-only rootfs, pids/CPU/memory limits; microVMs before hostile code | ✅ containers (tested on real Docker); microVM ⏳ |
| **Abuse of third parties** | Scanning or attacking external hosts from our IPs | Default network `lab` = Docker `--internal` network (no internet, tested); `internet` needs a recorded approval; `restricted-egress` goes only through the egress proxy (per-environment credentials, exact hostnames, ports 80/443, non-public addresses refused, all decisions logged); high-risk packs need approval; audit | ✅ enforced and tested on real Docker |
| **Terminal hijacking** | A malicious website opens a WebSocket to the user's terminal (cross-site WebSocket hijacking); a leaked or replayed terminal link | Single-use tickets (hash stored, 60 s), sent inside the socket not the URL; cross-site `Origin` rejected; ticket bound to one user and one running environment; session/idle/size limits; content never logged | ✅ tested |
| **Path traversal / injection via the file API** | `../../etc/passwd`, `-rf`, `$(cmd)` in file names | Relative paths only, `..`/absolute/leading `-`/control characters refused; operations run inside the environment as its unprivileged user; paths passed as separate arguments, never through a shell string | ✅ tested (incl. real Docker) |
| **Malicious or corrupted snapshot archive** | Tampered stored snapshot; crafted tar with `../` or absolute paths | SHA-256 verified before restore; extraction in a no-network, no-capability helper that can only write to the volume; staging folder, swapped in only on full success, so a refused archive changes nothing | ✅ tested on real Docker |
| **Supply chain** | Poisoned tool image or package | Tools come only from the base distribution's signed package repositories; recipes are generated from validated registry data (package names checked against a strict pattern), built with no build context; CI builds and smoke-tests every pack. Still to do: pinned base digests, SBOM, scanning, signing | 🟨 partial |
| **Secret leakage** | API keys committed or logged | No secrets in code; `.env` gitignored; `SecretStr`; audit details exclude manifests; GitHub secret scanning | ✅ |
| **AI-driven misuse** | Prompt asks the AI to build an attack environment against a real target | AI only drafts manifests; same policy engine; mandatory human approval; no target fields in the manifest | ⏳ Phase 5 |

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
* No rate limiting yet (add at the reverse proxy or with middleware before public exposure).
* Storage size (`storageGiB`) is not enforced on Docker's default storage driver yet.
* No live quota accounting across running environments yet (Phase 4).
* JWKS path implemented but only the HS256 path is covered by automated tests.
* Egress proxy: TLS SNI is not compared with the CONNECT hostname, so a client could use
  domain fronting through an allowed CDN host. Mitigation would need TLS inspection.
