#!/usr/bin/env bash
# Add stronger isolation to the Docker Engine that runs CyberLab's lab machines.
#
#   sudo ./scripts/install-isolation.sh            # gVisor (sandbox) and, if KVM exists, Kata (microVM)
#   sudo ./scripts/install-isolation.sh --sandbox  # only gVisor
#   sudo ./scripts/install-isolation.sh --microvm  # only Kata Containers
#
# gVisor ("sandbox") runs each machine on its own kernel layer written in Go, so programs
# inside never talk to the server's kernel directly. Kata Containers ("microvm") runs each
# machine in a real lightweight virtual machine with its own Linux kernel; it needs KVM
# (hardware virtualization), which most bare-metal servers and some cloud VMs have.
# Malware analysis in CyberLab is only allowed in a microVM.
#
# Sources: gVisor from its signed apt repository; Kata from its official GitHub release.
# Docker is restarted at the end (running lab machines stop; start them again afterwards).
set -euo pipefail

WANT_SANDBOX=1
WANT_MICROVM=1
case "${1:-}" in
  --sandbox) WANT_MICROVM=0 ;;
  --microvm) WANT_SANDBOX=0 ;;
  "") ;;
  -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown option: $1" >&2; exit 2 ;;
esac

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "please run with sudo"
command -v docker >/dev/null || die "install Docker first (scripts/install.sh does it for you)"
command -v apt-get >/dev/null || die "this script supports Debian/Ubuntu servers; see docs/guides/isolation.md for others"
ARCH="$(dpkg --print-architecture)"

if [[ $WANT_SANDBOX == 1 ]]; then
  say "Installing gVisor"
  apt-get install -y -q curl gpg >/dev/null
  curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor --yes -o /usr/share/keyrings/gvisor-archive-keyring.gpg
  echo "deb [arch=$ARCH signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
    > /etc/apt/sources.list.d/gvisor.list
  apt-get update -q >/dev/null
  apt-get install -y -q runsc >/dev/null
fi

if [[ $WANT_MICROVM == 1 ]]; then
  if [[ ! -e /dev/kvm ]]; then
    [[ $WANT_SANDBOX == 1 ]] && say "No KVM on this server: skipping microVMs (the sandbox still works)" \
      || die "this server has no KVM (/dev/kvm), which microVMs need"
    WANT_MICROVM=0
  else
    say "Installing Kata Containers (latest release)"
    apt-get install -y -q curl zstd xz-utils python3 >/dev/null
    case "$ARCH" in amd64) KARCH='(amd64|x86_64)' ;; arm64) KARCH='(arm64|aarch64)' ;; *) die "unsupported processor: $ARCH" ;; esac
    AUTH=()
    [[ -n "${GITHUB_TOKEN:-}" ]] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")
    URL="$(curl -fsSL "${AUTH[@]}" https://api.github.com/repos/kata-containers/kata-containers/releases/latest \
      | KARCH="$KARCH" python3 -c 'import json,os,re,sys
assets = json.load(sys.stdin)["assets"]
pat = r"kata-static-.*-" + os.environ["KARCH"] + r"\.tar\.(xz|zst)$"
print(next(a["browser_download_url"] for a in assets if re.match(pat, a["name"])))')"
    TMP="$(mktemp -d)"
    curl -fsSL "$URL" -o "$TMP/kata.tar"
    # Find the shim inside the release instead of assuming the archive's layout.
    SHIM_IN_TAR="$(tar -tf "$TMP/kata.tar" | grep -m1 -E '(^|/)containerd-shim-kata-v2$' || true)"
    if [[ -z "$SHIM_IN_TAR" ]]; then
      echo "Unexpected Kata release layout; first entries:" >&2
      tar -tf "$TMP/kata.tar" | head -40 >&2
      die "couldn't find containerd-shim-kata-v2 in $URL"
    fi
    tar -C / -xf "$TMP/kata.tar"
    rm -rf "$TMP"
    KATA_SHIM="/${SHIM_IN_TAR#./}"
    KATA_SHIM="$(readlink -f "$KATA_SHIM")"
    [[ -x "$KATA_SHIM" ]] || die "the Kata shim isn't executable at $KATA_SHIM"
    echo "    Kata shim: $KATA_SHIM"
    ln -sf "$KATA_SHIM" /usr/local/bin/containerd-shim-kata-v2
    ln -sf "$KATA_SHIM" /usr/bin/containerd-shim-kata-v2
    # Let the Kata shim open /dev/kvm.
    echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' > /etc/udev/rules.d/99-kvm4all.rules
    udevadm control --reload-rules && udevadm trigger --name-match=kvm || true
  fi
fi

say "Registering the runtimes with Docker"
SANDBOX=$WANT_SANDBOX MICROVM=$WANT_MICROVM SHIM="${KATA_SHIM:-}" python3 - <<'PY'
import json, os
path = "/etc/docker/daemon.json"
conf = json.load(open(path)) if os.path.exists(path) and os.path.getsize(path) else {}
runtimes = conf.setdefault("runtimes", {})
if os.environ["SANDBOX"] == "1":
    runtimes["runsc"] = {"path": "/usr/bin/runsc"}
if os.environ["MICROVM"] == "1":
    # The shim's full path, so Docker finds it whatever its search path is.
    runtimes["kata"] = {"runtimeType": os.environ["SHIM"]}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(conf, f, indent=2)
PY
systemctl restart docker
for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done

say "Checking (host kernel: $(uname -r))"
FAILED=0
docker pull -q mirror.gcr.io/library/busybox >/dev/null
check() { # level runtime
  local kernel
  if kernel="$(docker run --rm --runtime "$2" mirror.gcr.io/library/busybox uname -r 2>&1)" \
     && [[ -n "$kernel" && "$kernel" != "$(uname -r)" ]]; then
    echo "    $1 works: its kernel is $kernel"
  else
    echo "    $1 does NOT work yet: $kernel" >&2
    FAILED=1
  fi
}
[[ $WANT_SANDBOX == 1 ]] && check "sandbox (gVisor)" runsc
[[ $WANT_MICROVM == 1 ]] && check "microVM (Kata)" kata
[[ $FAILED == 0 ]] || die "an isolation level failed its check (see above); CyberLab won't offer it until it works"
say "Done. CyberLab offers these isolation levels in Setup now. If CyberLab runs in Docker, restart it: sudo docker restart cyberlab"
