# Full virtual machines (any ISO)

Most CyberLab machines are Linux containers: fast and light, but they can only run Linux
systems that publish a container image. A **full virtual machine** boots *any* operating
system from an ISO file, the way VMware or VirtualBox do, and shows its screen in your
browser. That is how you run systems like **Android-x86** or **Predator-OS** in CyberLab.

Nothing is stored on your computer: the ISO is downloaded by the server, straight into
the machine.

## What you need
* A CyberLab server with **KVM** (hardware virtualization, `/dev/kvm`): most bare-metal
  servers, and cloud VMs with nested virtualization. The dashboard's **System** panel
  shows whether full VMs are available. Codespaces usually has no KVM.
* Enough room: pick **Medium** or **Large** (an operating system needs memory and disk).

## Step by step
1. **Setup → Build with a form**: choose the operating system **Full virtual machine
   (QEMU/KVM)** and size **Large**. (Or write a manifest; see below.)
2. Save, approve "full virtual machine" (it runs whatever OS you give it), and create it.
3. In **Environments**, click **Operating system** on the machine, paste the ISO's
   download link from the project's official website (and its SHA-256 if published),
   and click **Download into the machine**.
4. When it says **Ready**, click **Desktop**: you see the virtual machine's screen, its
   installer or live system. Your disk (`disk.qcow2`) keeps what you install, also after
   the machine stops.

```yaml
    - name: pc
      baseImage: vm-host
      resources: {cpu: 2, memoryMiB: 4096, storageGiB: 16}
      network: {mode: isolated}         # or lab: reach your other lab machines
      access: {desktop: true}
      vm: {boot: boot.iso, diskGiB: 12}
```

## Where to get operating systems
Always download from the project's own site and compare the checksum.

| System | Official site | Notes |
|---|---|---|
| Android-x86 / BlissOS | android-x86.org, blissos.org | Android for PCs; graphics are software-rendered, so it is slower than a phone |
| Predator-OS | predator-os.ir (see the project's GitBook) | the project publishes ISO images; check its site for the current release and checksums |
| Kali, Parrot (installer ISOs) | kali.org, parrotsec.org | usually the container versions in CyberLab are faster |

**Tested in CI:** booting the official Alpine Linux ISO with KVM and viewing its screen.
The systems in the table above haven't been tested by CyberLab yet: try them and report
what works.

## How it is kept safe
* The VM runs inside the same hardened container as every machine: unprivileged user,
  no capabilities, read-only system. The only addition is the KVM device.
* QEMU runs with its own seccomp sandbox (no spawning programs, no privilege changes).
* The screen is a VNC socket inside the machine (no network port); only CyberLab's
  single-use desktop tickets reach it.
* The VM's network follows the machine's network mode (`isolated` = no network at all).
* Links: only `https`, only public addresses (every redirect is checked again), a size
  limit (8 GiB by default, `CYBERLAB_MAX_VM_MEDIA_BYTES`), optional SHA-256 check. The
  download is audited with its host and checksum.
