# Plan: ready-made labs (Phase 8)

**Why:** people come for *what they can learn*, not for a platform. A beginner should be
able to pick a lab, press one button, and be practising a real skill within a minute, with
clear tasks, hints, and an instant "correct!" when they find the answer.

**Approved by the user:** 2026-11-18 ("start doing that").

## What a lab is
A file in `registry/labs/<id>.yaml` (metadata only, like the rest of the registry):

* title, level, minutes, summary, skills it teaches;
* the machines it needs (a normal Universe Manifest `spec`, so every safety rule, quota
  and policy check still applies; labs never need internet or approval);
* **setup**: files placed into a machine's `/workspace` when the lab starts;
* **tasks**: what to find, the text explaining it, hints, and where the answer comes from.

Answers are **generated fresh for every run** (random flags like `CL{3f9a…}` or generated
data such as an attacker's IP), so they can't be copied from the internet or this repo.
The server keeps only a SHA-256 of each answer and compares in constant time. Content
generators are a fixed list of Python functions, never code inside the YAML.

## Steps
1. **Lab framework + two labs that need no target machine** (this step)
   * Catalog API, one-click start (workspace + manifest + machines + lab files), answer
     checking with progress, rate limit and audit.
   * Web app: a **Labs** page (cards) and a **Lab** tab in the workspace (tasks, hints,
     answer box, progress).
   * Labs: *First steps in Linux* (`ls`, `cat`, hidden files, `find`, `grep`) and
     *Log detective* (blue team: find the attacker in an SSH log with `grep`, `sort`,
     `uniq`, `awk`).
2. **Target machines**: hardened, intentionally vulnerable services written by us (no
   third-party vulnerable images), running only on the private lab network, reachable by
   name (`target`). Labs: *Scan a network with nmap*, *Find the secret in a web app*.
3. **More labs and progress**: a "completed" badge per lab, labs at every level
   (beginner, intermediate, advanced) across Linux, networking, web, forensics, crypto
   and blue team, including multi-network pivoting.
4. **Community labs**: share and import lab files, with the same validation.
5. **Classroom mode**: an instructor starts a lab for a group, sees progress, resets machines.

## Safety
* Lab machines use the safest network that works: `isolated` when there is no target,
  the private `lab` network when there is. No lab ever reaches the internet.
* Targets (step 2) run with the same hardening as every machine (unprivileged,
  read-only root, no capabilities) and are only reachable from the same workspace.
* Answers are never logged; only "task X solved" is audited.
