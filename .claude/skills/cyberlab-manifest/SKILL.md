---
name: cyberlab-manifest
description: Write, fix or explain a CyberLab Universe Manifest (cyberlab.dev/v1alpha1) - the YAML file that describes a lab's machines, operating systems, tool packs, resources, network and desktop. Use when asked to create or change a lab, a manifest, a *.universe.yaml file, or when a manifest fails validation.
---

# Writing CyberLab Universe Manifests

A manifest describes a lab: which machines, on which operating system, with which tools,
how big, and what network they may use. It is configuration only: never put secrets in it.
Full reference: `docs/07-universe-manifest.md`. Schema: `spec/universe-manifest.schema.json`.

## Steps
1. **Use real registry IDs only.** List them (from `backend/`): `cyberlab registry`.
   Operating systems are in `registry/base-images.yaml`, tool packs in `registry/toolpacks/`.
   A tool pack only works on the systems in its `compatibleBases`.
2. **Write the manifest** (shape below). Keep it as small as the request allows.
3. **Validate it** and fix every `error` finding:
   `cyberlab manifest validate path/to/lab.universe.yaml`
   `approval` findings are allowed but the user must approve them in the web app before
   anything starts; tell the user which ones and why.
4. Tell the user what each machine is for, in plain words.

## Shape
```yaml
apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata:
  name: web-practice              # lowercase letters, digits, dashes
  description: Practise web testing against a target in my own lab.
spec:
  environments:
    - name: attacker              # unique, lowercase, dashes
      baseImage: kali-rolling
      resources: {cpu: 1, memoryMiB: 2048, storageGiB: 8}
      toolPacks: [web-appsec, network-analysis]
      network: {mode: lab}
      lifecycle: {idleTimeoutMinutes: 60}
    - name: target
      baseImage: debian-12
      resources: {cpu: 0.5, memoryMiB: 1024, storageGiB: 4}
      network: {mode: lab}
```

## Rules that matter
* **Network: safest that works.** `isolated` (no network) or `lab` (only this lab's
  machines; the default) for almost everything. `restricted-egress` with exact hostnames in
  `allowedEgress` when specific sites are needed. `internet` only when clearly required: it
  needs the user's approval.
* **Authorized practice only.** Labs attack machines inside the lab, CTFs, or systems the
  user has written permission to test. Never design a lab aimed at real third-party
  systems; say so kindly and suggest a practice target instead. There is no field for
  external targets, by design.
* **Sizes:** at least the operating system's and each tool pack's `minResources`. Kali and
  BlackArch need 1 CPU and 1 GiB. Common sizes: small 0.5 CPU / 1024 MiB / 4 GiB, medium
  1 / 2048 / 8, large 2 / 4096 / 16.
* **Remote desktop:** `access: {desktop: true}`, only on systems with `supportsDesktop: true`,
  and add about 1 GiB of memory (medium or larger).
* **Lifecycle:** `idleTimeoutMinutes` (stop when unused), `ttlHours` (delete after),
  `ephemeral: true` (throwaway: files deleted too).
* High-risk tool packs need approval; mention it.

## Do not
* Invent operating systems, tool packs or fields (unknown fields are rejected).
* Use wildcards, IP addresses or URLs in `allowedEgress`; only exact hostnames.
* Put credentials, tokens or personal data anywhere in the file.
