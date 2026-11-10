# Learning Progress

A running record of the learning journey: what has been covered, what is in progress, and
what comes next. Updated when an exercise is completed or a topic is understood.
Status: ⬜ not started · 🟨 in progress / partly explored · ✅ completed with an exercise.

## Starting point (recorded 2026-11-08)
* Level: **beginner** in Linux, networking, operating systems, Python and security.
* Environment explored so far: Windows 11, WSL2 with Ubuntu, Docker Desktop (WSL2
  backend), a look at Parrot OS, and installing and configuring some security tools.
* Constraint: almost no free local disk, so exercises run in **GitHub Codespaces**
  (browser) or in CyberLab once it can provision environments.

## Roadmap and status
Ordered so each stage builds on the previous one.

### Stage 1 — Foundations
| Topic | Status | Notes |
|---|---|---|
| Linux: filesystem layout, navigation, files | 🟨 | Has used WSL2 Ubuntu terminals |
| Linux: users, groups, permissions, ownership | ⬜ | |
| Linux: processes, services, systemd, logs | ⬜ | |
| Linux: package management (apt), environment, PATH | 🟨 | Tool installation explored |
| Shell scripting basics (bash) | ⬜ | |
| Networking: IP, ports, TCP/UDP, DNS, HTTP | ⬜ | |
| Python basics | ⬜ | |
| Git and GitHub basics | 🟨 | Worked with repositories, branches and bundles while setting up CyberLab |
| Containers: what Docker is and how isolation works | 🟨 | Docker Desktop on WSL2 explored |

### Stage 2 — Core security
| Topic | Status |
|---|---|
| Security fundamentals: CIA triad, threat modelling, least privilege | ⬜ |
| Cryptography: hashing, symmetric/asymmetric encryption, TLS | ⬜ |
| Network security: packet capture and analysis (own lab traffic) | ⬜ |
| Web application security: OWASP Top 10 on intentionally vulnerable apps | ⬜ |
| Linux hardening | ⬜ |
| Python for security automation | ⬜ |

### Stage 3 — Specialisation
| Topic | Status |
|---|---|
| Ethical hacking and penetration testing (labs/CTFs only) | ⬜ |
| Vulnerability assessment | ⬜ |
| Defensive security, detection engineering, blue team | ⬜ |
| Incident response | ⬜ |
| Digital forensics | ⬜ |
| OSINT (ethical, public sources) | ⬜ |
| Cloud security | ⬜ |
| Application security | ⬜ |

### Stage 4 — Advanced
| Topic | Status |
|---|---|
| Linux internals and kernel concepts | ⬜ |
| Malware analysis in isolated environments | ⬜ |
| AI security | ⬜ |
| Security automation with AI | ⬜ |
| Security research and responsible disclosure | ⬜ |

## Completed exercises
_None recorded yet._ Add entries as: date · topic · what was done · what was learned.

## Errors encountered and lessons
| Date | Situation | Lesson |
|---|---|---|
| 2026-11-08 | GitHub branch link showed "404 / Ref is invalid" | A branch link only works after the branch has been **pushed**. Pushing needs write permission on the repository (here, via the Claude GitHub App). |
| 2026-11-08 | GitHub Actions page showed deployment templates | The CI workflow lives on the Phase 1 branch, so `main` has none yet. Deployment templates would create paid cloud resources and aren't needed. |

## Next learning step
**Linux, Stage 1: the filesystem and permissions**, practised in a GitHub Codespace
on the CyberLab repository (no local install needed).

## Concepts met while building CyberLab
Real problems from the codebase, which make good lessons later.
| Date | Concept | What happened |
|---|---|---|
| 2026-11-08 | Linux file ownership and permissions | A new Docker volume was owned by `root` (uid 0), so the environment's user (uid 1000) got "Permission denied" writing to `/workspace`. Fixed with `chown` run with only the `CHOWN` capability. |
| 2026-11-08 | Linux capabilities | Root's powers are split into ~40 capabilities. CyberLab drops all of them (`CapEff: 0000000000000000`) and grants only one when needed. |
| 2026-11-08 | Container networks | An `--internal` Docker network lets lab machines see each other but not the internet. |
| 2026-11-08 | Pseudo-terminals (pty) | A browser terminal works because the server gives the shell a *pseudo-terminal*, a fake keyboard-and-screen device. Programs then behave as if a person were typing (colours, line editing, window size via `stty size`). SSH uses the same mechanism. |
| 2026-11-08 | `/etc/passwd` and user IDs | Linux tracks users by number (uid). `/etc/passwd` maps numbers to names. Our container runs as uid 1000, which has no name in that file, so bash shows `I have no name!`. |
| 2026-11-08 | WebSocket security | A WebSocket is a two-way connection between browser and server. Any website could try to open one to your server, so CyberLab checks the `Origin` and requires a single-use ticket. |
| 2026-11-08 | Package managers and Dockerfiles | `apt` installs software from Debian/Ubuntu's signed repositories. A Dockerfile is a recipe (`FROM` a base, `RUN` commands, `USER`) that Docker turns into an image. CyberLab now writes these recipes automatically from the tool pack list. |
| 2026-11-08 | Proxies and SSRF | A *proxy* is a middleman that makes web requests for you, so it can decide which sites are allowed. A classic attack is *SSRF* (server-side request forgery): tricking a server into requesting internal addresses such as `127.0.0.1` or cloud metadata at `169.254.169.254`. CyberLab's proxy blocks every non-public address. |
| 2026-11-08 | Archives and checksums | A *tar* file bundles many files into one; *gzip* compresses it. A *SHA-256 checksum* is a fingerprint of a file's exact bytes: change one byte and it's completely different, which is how CyberLab detects a tampered snapshot. Archive entries like `../escape.txt` are a classic attack (*path traversal*); CyberLab refuses the whole archive. |
| 2026-11-08 | Encryption at rest | *Client-side encryption* means the data is encrypted before it reaches the cloud, so the storage company only sees scrambled bytes. CyberLab uses AES-256-GCM, which both hides the data and detects any change to it (*authenticated encryption*). Losing the key means losing the data, so keys must be backed up. |
| 2026-11-10 | Database migrations | A *migration* is a small, versioned script that changes a database's structure (add a table, a column, ...). The database remembers which ones it has run, so upgrading only applies what's missing and existing data is kept. CyberLab uses Alembic and tests that the migrations always match the code. |
| 2026-11-10 | Rate limiting and token buckets | A *token bucket* gives each client a number of tokens that refill over time; each request spends one. It allows short bursts but stops floods. Attackers try to dodge per-user limits by faking many identities, which is why CyberLab also limits per IP address. |
