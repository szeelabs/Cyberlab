#!/usr/bin/env bash
# Install CyberLab on a Linux server (or a Codespace / any Linux machine) with one command.
#
#   sudo ./scripts/install.sh                      # private: http://127.0.0.1:8000/app/
#   sudo ./scripts/install.sh --domain lab.example.org \
#        --supabase-url https://xxxx.supabase.co --supabase-key sb_publishable_...
#                                                  # public, with HTTPS and real sign-in
#   sudo ./scripts/install.sh --uninstall          # stop and remove (keeps your data)
#   sudo ./scripts/install.sh --uninstall --purge  # ...and delete all CyberLab data
#
# Run it again after `git pull` to update. It builds CyberLab from this folder; nothing is
# downloaded from anywhere except Docker images and the distribution's own packages.
#
# What it does: installs Docker if missing (from the distribution's packages), builds the
# CyberLab image, keeps your data in the Docker volume "cyberlab-data", and starts CyberLab
# so it restarts with the server. Without --domain it listens only on this machine
# (127.0.0.1) and needs no sign-in: reach it through an SSH tunnel
# (ssh -L 8000:127.0.0.1:8000 you@server). With --domain it is public, so real sign-in is
# required, and Caddy gets an HTTPS certificate automatically.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8000
DOMAIN=""
SUPABASE_URL=""
SUPABASE_KEY=""
MIRROR=""
UNINSTALL=0
PURGE=0
CONF_DIR=/etc/cyberlab
NAME=cyberlab
PROXY=cyberlab-proxy
NET=cyberlab-web
IMAGE=cyberlab:local
CADDY_IMAGE=docker.io/library/caddy:2

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:?}"; shift 2 ;;
    --domain) DOMAIN="${2:?}"; shift 2 ;;
    --supabase-url) SUPABASE_URL="${2:?}"; shift 2 ;;
    --supabase-key) SUPABASE_KEY="${2:?}"; shift 2 ;;
    --mirror) MIRROR="${2:?}"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    -h|--help) usage 0 ;;
    *) printf 'unknown option: %s\n\n' "$1" >&2; usage 2 ;;
  esac
done

[[ "$(uname -s)" == Linux ]] || die "this installer is for Linux servers (on Windows, use a Codespace or WSL2)"
[[ $EUID -eq 0 ]] || die "please run with sudo: sudo $0 $*"
[[ "$PORT" =~ ^[0-9]{2,5}$ ]] || die "--port must be a number"
if [[ -n "$DOMAIN" ]]; then
  [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$ ]] || die "--domain must be a plain host name like lab.example.org"
  [[ -n "$SUPABASE_URL" && -n "$SUPABASE_KEY" ]] \
    || die "a public CyberLab needs real sign-in: add --supabase-url and --supabase-key (see docs/guides/sign-in.md)"
fi
[[ -z "$SUPABASE_URL" || "$SUPABASE_URL" =~ ^https://[a-z0-9-]+\.supabase\.co$ ]] \
  || die "--supabase-url should look like https://abcdefghijklmnop.supabase.co"
[[ -z "$MIRROR" || "$MIRROR" =~ ^[a-z0-9.-]+$ ]] || die "--mirror must be a host name like mirror.gcr.io"

if [[ $UNINSTALL == 1 ]]; then
  say "Stopping CyberLab"
  docker rm -f "$NAME" "$PROXY" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  if [[ $PURGE == 1 ]]; then
    say "Deleting CyberLab's data and lab machines"
    docker ps -aq --filter label=dev.cyberlab.managed=true | xargs -r docker rm -f >/dev/null
    docker volume ls -q --filter label=dev.cyberlab.managed=true | xargs -r docker volume rm >/dev/null
    docker network ls -q --filter label=dev.cyberlab.managed=true | xargs -r docker network rm >/dev/null
    docker volume rm cyberlab-data cyberlab-caddy >/dev/null 2>&1 || true
    rm -rf "$CONF_DIR"
  else
    say "Your data is kept (Docker volume cyberlab-data). Add --purge to delete it."
  fi
  exit 0
fi

# ------------------------------------------------------------------ Docker
if ! docker info >/dev/null 2>&1; then
  if command -v apt-get >/dev/null; then
    say "Installing Docker from your distribution's packages"
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io
  elif command -v dnf >/dev/null; then
    say "Installing Docker (moby-engine) from your distribution's packages"
    dnf install -y -q moby-engine || dnf install -y -q docker
  else
    die "please install Docker first (https://docs.docker.com/engine/install/), then run this again"
  fi
  systemctl enable --now docker >/dev/null 2>&1 || service docker start
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
  docker info >/dev/null 2>&1 || die "Docker was installed but isn't running; try: sudo systemctl start docker"
fi

pull_name() { # docker.io/library/x -> mirror/library/x when a mirror is set
  if [[ -n "$MIRROR" && "$1" == docker.io/* ]]; then echo "$MIRROR/${1#docker.io/}"; else echo "$1"; fi
}

# ------------------------------------------------------------------ build
say "Building CyberLab (the first time takes a few minutes)"
BUILD_ARGS=()
if [[ -n "$MIRROR" ]]; then
  # Base images through the mirror, to avoid Docker Hub's download limits.
  sed -e "s#^FROM node:#FROM $MIRROR/library/node:#" -e "s#^FROM python:#FROM $MIRROR/library/python:#" \
    "$ROOT/Dockerfile" > "$ROOT/.Dockerfile.mirror"
  BUILD_ARGS=(-f "$ROOT/.Dockerfile.mirror")
fi
docker build -q "${BUILD_ARGS[@]}" -t "$IMAGE" "$ROOT" >/dev/null
rm -f "$ROOT/.Dockerfile.mirror"

# ------------------------------------------------------------------ settings
mkdir -p "$CONF_DIR"
chmod 700 "$CONF_DIR"
ENV_FILE="$CONF_DIR/cyberlab.env"
{
  echo "# Written by scripts/install.sh; rerun it to change these."
  if [[ -n "$DOMAIN" ]]; then
    echo "CYBERLAB_ENVIRONMENT=production"
    echo "CYBERLAB_AUTH_MODE=jwt"
    echo "CYBERLAB_CORS_ORIGINS=[\"https://$DOMAIN\"]"
    echo "FORWARDED_ALLOW_IPS=*"  # only Caddy can reach this container
  elif [[ -n "$SUPABASE_URL" ]]; then
    echo "CYBERLAB_ENVIRONMENT=development"
    echo "CYBERLAB_AUTH_MODE=jwt"
  else
    echo "CYBERLAB_ENVIRONMENT=development"
    echo "CYBERLAB_AUTH_MODE=dev"
  fi
  if [[ -n "$SUPABASE_URL" ]]; then
    echo "CYBERLAB_AUTH_SUPABASE_URL=$SUPABASE_URL"
    echo "CYBERLAB_AUTH_SUPABASE_PUBLISHABLE_KEY=$SUPABASE_KEY"
  fi
  [[ -n "$MIRROR" ]] && echo "CYBERLAB_IMAGE_MIRROR=$MIRROR"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ------------------------------------------------------------------ start
say "Starting CyberLab"
docker rm -f "$NAME" "$PROXY" >/dev/null 2>&1 || true
docker volume create cyberlab-data >/dev/null
RUN=(docker run -d --name "$NAME" --restart unless-stopped
  --env-file "$ENV_FILE"
  -v /var/run/docker.sock:/var/run/docker.sock
  -v cyberlab-data:/data
  --security-opt no-new-privileges)

if [[ -n "$DOMAIN" ]]; then
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
  "${RUN[@]}" --network "$NET" "$IMAGE" >/dev/null
  docker volume create cyberlab-caddy >/dev/null
  docker run -d --name "$PROXY" --restart unless-stopped --network "$NET" \
    -p 80:80 -p 443:443 -v cyberlab-caddy:/data \
    "$(pull_name "$CADDY_IMAGE")" caddy reverse-proxy --from "$DOMAIN" --to "$NAME:8000" >/dev/null
  URL="https://$DOMAIN/app/"
  CHECK=(docker exec "$NAME" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)")
else
  "${RUN[@]}" -p "127.0.0.1:$PORT:8000" "$IMAGE" >/dev/null
  URL="http://127.0.0.1:$PORT/app/"
  CHECK=(curl -fsS -o /dev/null "http://127.0.0.1:$PORT/healthz")
fi

for _ in $(seq 1 60); do "${CHECK[@]}" >/dev/null 2>&1 && break; sleep 1; done
if ! "${CHECK[@]}" >/dev/null 2>&1; then
  docker logs --tail 30 "$NAME" >&2 || true
  die "CyberLab didn't start; the lines above say why"
fi

say "CyberLab is running: $URL"
if [[ -z "$DOMAIN" ]]; then
  echo "    Private to this machine. From your computer: ssh -L $PORT:127.0.0.1:$PORT you@this-server"
  echo "    then open $URL in your browser."
else
  echo "    In Supabase, set Site URL and Redirect URLs to $URL (docs/guides/sign-in.md)."
fi
echo "    Logs: sudo docker logs -f $NAME    Update: git pull && sudo $0 (same options)"
