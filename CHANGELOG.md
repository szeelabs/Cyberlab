# Changelog

All notable changes to CyberLab. Versions follow `MAJOR.MINOR.PATCH`; while the version starts
with `0.`, anything may still change. The release workflow publishes the section whose
heading matches the pushed tag.

## [Unreleased]
### Added
- Ready-made labs: pick a lab, press Start, and practise with tasks, hints and instant
  answer checking; fresh answers for every run. First labs: *First steps in Linux* and
  *Log detective* (find an SSH attacker in a log). Write your own in `registry/labs/`.
- Real sign-in with Supabase: sign-in and create-account screen, automatic token refresh,
  sign out; tokens checked with the project's public keys (ES256).
- Copy and paste between your computer and the remote desktop.
- Install your own software: upload a .deb (for example Cisco Packet Tracer) and click
  Install; the machine is rebuilt with it, keeping your files. Uploads can now be up to
  2 GiB.
- ARM (arm64) servers, such as free cloud ARM machines: Ubuntu, Debian, Kali and Parrot OS
  are built and tested on ARM in CI; systems that can't run on the server's processor are
  shown as unavailable and refused with a clear message.
- Kali is built and tested in CI like the other systems (all tool packs and the desktop);
  the detection-engineering pack (Suricata, YARA) is now offered on Kali.

### Fixed
- Leaving a terminal or desktop while it was closing could leave the machine-side process
  running and the session unaudited; teardown now always completes.

## [0.1.0] - not released yet
The first version. Everything below is built and tested; `docs/DEVELOPMENT_STATUS.md` lists
exactly how, and what does not work yet.

### Machines
- Hardened Linux machines (Ubuntu 24.04, Debian 12, Kali, Parrot OS, BlackArch) on Docker,
  locally or on your own server over SSH, with tool packs for network analysis, web
  application security, forensics, cryptography, OSINT/DNS, vulnerability assessment and
  detection engineering.
- Browser terminal and remote desktop, both with single-use tickets and no open ports.
- Isolated lab networks by default; full internet only after approval; allow-listed
  websites through an egress proxy.
- Workspace files, integrity-checked snapshots (optionally on S3 with client-side
  encryption), and portable export/import bundles.
- Resource limits: quotas, rate limits, idle stop, time limits and throwaway machines.

### Web app
- Dashboard, workspaces, tool universe, beginner form, manifest editor with live checks.
- "Describe your lab": plain words to a draft lab (free built-in planner; Claude optional
  and paid), refusing attacks on real systems.
- "What's wrong?": plain-language troubleshooting for every machine.

### Project
- Universe Manifest `cyberlab.dev/v1alpha1` with a JSON Schema, policy engine, audit log,
  database migrations, CLI, CI on every change, and an evaluation set for lab drafts.
