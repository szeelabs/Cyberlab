#!/bin/sh
# CyberLab full virtual machine: boots the ISO or disk in /workspace with QEMU/KVM.
# Runs as the machine's unprivileged user. The screen is a VNC server on a Unix socket in
# /tmp (no network port); CyberLab's remote desktop reaches it through `docker exec`.
set -eu
dir=/tmp/cyberlab-vm
mkdir -p "$dir"
boot="/workspace/${CYBERLAB_VM_BOOT:-boot.iso}"
disk=/workspace/disk.qcow2
log() { echo "cyberlab-vm: $*" | tee -a "$dir/status"; }

# Wait for boot media: a disk from an earlier run, or an ISO whose size has stopped
# changing (an upload or download may still be writing it).
if [ ! -s "$disk" ]; then
  log "waiting for boot media at $boot"
  last=-1
  while :; do
    size=$(stat -c %s "$boot" 2>/dev/null || echo 0)
    if [ "$size" -gt 0 ] && [ "$size" = "$last" ]; then break; fi
    last=$size
    sleep 3
  done
  qemu-img create -f qcow2 "$disk" "${CYBERLAB_VM_DISK_GIB:-16}G" >/dev/null
fi

cdrom=""
[ -s "$boot" ] && cdrom="-drive file=$boot,media=cdrom,readonly=on"
case "${CYBERLAB_VM_NETWORK:-none}" in
  user) net="-nic user,model=virtio-net-pci" ;;
  *) net="-nic none" ;;
esac
log "starting (memory ${CYBERLAB_VM_MEMORY_MIB:-1024} MiB, ${CYBERLAB_VM_CPUS:-1} CPU)"
# -sandbox: QEMU's own seccomp filter, on top of the container's.
# shellcheck disable=SC2086
exec qemu-system-x86_64 \
  -enable-kvm -cpu host -machine q35 \
  -m "${CYBERLAB_VM_MEMORY_MIB:-1024}" -smp "${CYBERLAB_VM_CPUS:-1}" \
  -drive "file=$disk,if=virtio" $cdrom -boot order=cd,menu=off \
  $net \
  -vga std -display none -vnc "unix:$dir/vnc.sock" \
  -usb -device usb-tablet \
  -sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny \
  -name cyberlab-vm -nodefaults -monitor none -serial "file:$dir/serial.log"
