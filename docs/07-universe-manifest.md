# Universe Manifest Specification — `cyberlab.dev/v1alpha1`

A Universe Manifest is a single portable YAML (or JSON) document describing a user's
virtual universe: its environments, tool packs, resources, networking, workspace and
preferences. It contains **configuration only**: no secrets and no data. Data travels
separately as snapshots (Phase 3).

* Machine-readable schema: [`spec/universe-manifest.schema.json`](../spec/universe-manifest.schema.json)
* Examples: [`examples/manifests/`](../examples/manifests)
* Validate: `cyberlab manifest validate my.universe.yaml`

```yaml
apiVersion: cyberlab.dev/v1alpha1   # required, exact
kind: Universe                      # required, exact
metadata:
  name: blue-team-lab               # slug: a-z 0-9 -, max 63
  description: optional text
  labels: {track: defensive}        # ≤ 32 string pairs
spec:
  environments:                     # 1..32, names unique
    - name: analyst
      baseImage: ubuntu-24-04       # id from registry/base-images.yaml
      resources: {cpu: 2, memoryMiB: 4096, storageGiB: 10}
      toolPacks: [forensics, crypto]  # ids from registry/toolpacks, no duplicates
      network:
        mode: lab                   # isolated | lab | restricted-egress | internet
        allowedEgress: []           # exact hostnames, only with restricted-egress
      access: {terminal: true, desktop: false}
      lifecycle: {ephemeral: false, idleTimeoutMinutes: 60, ttlHours: null}
  workspace: {persistent: true, storageGiB: 5, snapshotPolicy: manual}  # manual|daily|on-stop
  preferences: {shell: bash, editor: vim, theme: system, timezone: UTC}
```

## Network modes
| Mode | Meaning | Policy |
|---|---|---|
| `isolated` | No network interface beyond loopback | always allowed |
| `lab` *(default)* | Private network shared only by environments in the same universe | always allowed |
| `restricted-egress` | `lab` + outbound only to `allowedEgress` hosts via proxy | hostnames must be exact (no wildcards, IPs or schemes) |
| `internet` | General outbound access | **requires approval** |

## Validation rules (beyond the schema)
| Code | Severity | Rule |
|---|---|---|
| `registry.base-image` / `registry.tool-pack` | error | IDs must exist in the registry |
| `registry.incompatible` | error | Pack must list the base in `compatibleBases` |
| `resources.cpu/memory/storage` | error | ≥ max(base, packs) CPU/RAM; storage ≥ base + Σ packs; desktop adds 1024 MiB |
| `access.desktop` | error | Base must support desktop |
| `security.isolation` | error | Packs with `requiresIsolation` need `isolated` or `lab` |
| `security.high-risk-pack` | approval | Packs with `risk: high` |
| `security.internet-egress` | approval | `network.mode: internet` |
| `quota.*` | error | Per-environment and total quotas (configurable) |
| `resources.no-idle-shutdown` | warning | `idleTimeoutMinutes: null` on a non-ephemeral environment |

## Portability
* Export (`GET /v1/workspaces/{id}/manifest`) returns the canonical form with defaults filled
  in, so an imported manifest behaves the same on any CyberLab instance with the same
  registry version.
* Parsing is hardened: one document only, no anchors/aliases, no language-specific tags,
  unknown fields rejected, 128 KiB limit.

## Versioning
`v1alpha1` may change incompatibly. Breaking changes bump the version (`v1alpha2`, `v1beta1`,
`v1`). The control plane will provide converters once more than one version exists.
