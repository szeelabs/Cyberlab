#!/bin/sh
# CyberLab remote desktop: starts this machine's desktop once, then speaks VNC on
# stdin/stdout (x11vnc -inetd). CyberLab runs it through `docker exec`, so no network
# port is ever opened; access is controlled by CyberLab's single-use tickets.
set -u
export DISPLAY=:1
GEOMETRY="${CYBERLAB_DESKTOP_GEOMETRY:-1280x800}"
running() { kill -0 "$( { tr -d ' ' < /tmp/.X1-lock; } 2>/dev/null)" 2>/dev/null; }
if ! running; then
  rm -f /tmp/.X1-lock /tmp/.X11-unix/X1
  nohup setsid Xvfb :1 -screen 0 "${GEOMETRY}x24" -nolisten tcp \
    >/tmp/xvfb.log 2>&1 &
  i=0
  while [ ! -e /tmp/.X11-unix/X1 ] && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
  if ! running; then
    echo "cyberlab-desktop: the virtual screen (Xvfb) did not start:" >&2
    tail -n 20 /tmp/xvfb.log >&2
    exit 1
  fi
  nohup setsid openbox >/tmp/openbox.log 2>&1 &
  (cd "$HOME" && nohup setsid xterm -geometry 100x30+40+40 -bg '#0e1311' -fg '#dde7e2' \
    -cr '#3ecf8e' -title 'CyberLab terminal' >/tmp/xterm.log 2>&1 &)
fi
# x11vnc -inetd reads and writes one two-way socket; socat turns docker exec's two pipes
# into one.
exec socat STDIO EXEC:"x11vnc -inetd -nopw -norc -quiet -shared -noxdamage -o /tmp/x11vnc.log"
