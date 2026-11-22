# Stronger isolation: sandboxes and microVMs

Every CyberLab machine is a hardened container: an unprivileged user, a read-only system,
no capabilities, resource limits. That is safe for normal practice. But a container shares
the server's **kernel** (the core of the operating system), so a serious kernel bug could
let a program escape. For riskier work, CyberLab offers two stronger levels:

| Level | What it is | Needs | Cost |
|---|---|---|---|
| `container` (default) | Hardened container on the server's kernel | Docker | fastest |
| `sandbox` | [gVisor](https://gvisor.dev): a kernel written in Go between the machine and the server; programs never call the server's kernel directly | gVisor | a little slower; a few programs behave differently |
| `microvm` | [Kata Containers](https://katacontainers.io): a real lightweight virtual machine with **its own Linux kernel** | Kata + KVM (hardware virtualization) | ~1–2 s more to start, some memory overhead |

Choose it per machine: **Isolation** in the beginner form, or in a manifest:
```yaml
    - name: analysis
      baseImage: ubuntu-24-04
      resources: {cpu: 1, memoryMiB: 1024, storageGiB: 6}
      network: {mode: isolated}
      isolation: microvm
```
The form only offers the levels the server has. If a manifest asks for a level the
server can't run, CyberLab says so before creating anything.

## Add them to your server
On a Debian/Ubuntu server where CyberLab runs (for example after `scripts/install.sh`):
```bash
sudo ./scripts/install-isolation.sh
```
It installs gVisor from its signed repository and, if the server has KVM (`/dev/kvm`),
Kata Containers from its official release, registers both with Docker as `runsc` and
`kata`, restarts Docker and prints the kernel each level reports. Running machines stop
when Docker restarts. If CyberLab itself runs in Docker, restart it afterwards:
`sudo docker restart cyberlab`.

Other runtime names: set `CYBERLAB_SANDBOX_RUNTIME` / `CYBERLAB_MICROVM_RUNTIME`.

**KVM:** most bare-metal servers have it. Cloud VMs need "nested virtualization" (often
off on free tiers). GitHub's Linux runners have it, which is where CyberLab tests microVMs.
Codespaces usually don't, so use `sandbox` there.

## Malware analysis
The **Malware Analysis** tool pack (radare2, YARA, UPX, ssdeep) is allowed only in a
`microvm` machine with network `isolated`, and only after you approve it (high risk). The AI
helper never drafts it. CyberLab ships no malware samples: bring your own from a trusted
source, and only analyze what you are allowed to.

## What is tested
CI job `isolation` runs `scripts/install-isolation.sh` on a GitHub runner, then creates
CyberLab machines in a sandbox and in a microVM and checks: each reports a kernel different
from the server's, Docker used the right runtime, the machine still runs as the
unprivileged user with a read-only system, and files and commands work.
