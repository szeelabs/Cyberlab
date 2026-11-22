#!/bin/sh
# CyberLab remote desktop for a full VM: connects stdin/stdout to QEMU's VNC socket.
sock=/tmp/cyberlab-vm/vnc.sock
i=0
while [ ! -S "$sock" ] && [ "$i" -lt 150 ]; do sleep 0.2; i=$((i + 1)); done
if [ ! -S "$sock" ]; then
  echo "cyberlab-vm: the virtual machine's screen isn't ready:" >&2
  tail -n 5 /tmp/cyberlab-vm/status >&2 2>/dev/null
  exit 1
fi
exec socat STDIO "UNIX-CONNECT:$sock"
