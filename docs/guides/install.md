# Install CyberLab on a server (one command)

For a server you control: a free cloud VM, a spare PC with Linux, or a school server. Your
own laptop needs nothing; people use CyberLab from their browser.

> **Read this first.** CyberLab starts lab machines through Docker, and anything that
> controls Docker effectively has root on that machine. Use a server (or VM) dedicated to
> CyberLab.

## Private install (only you, through an SSH tunnel)
On the server:
```bash
git clone https://github.com/szeelabs/Cyberlab.git
```
```bash
cd Cyberlab && sudo ./scripts/install.sh
```
*What it does:* installs Docker from your distribution if it's missing, builds CyberLab
from this folder, keeps all data in the Docker volume `cyberlab-data`, and starts CyberLab
so it comes back after a reboot. It listens **only on the server itself**
(`127.0.0.1:8000`), so it needs no sign-in. The first build takes a few minutes.

From your own computer, open a tunnel, then browse to http://127.0.0.1:8000/app/:
```bash
ssh -L 8000:127.0.0.1:8000 you@your-server
```

## Public install (HTTPS and real sign-in)
For a class or a team. You need a domain name pointing at the server (an `A` record),
ports 80 and 443 open, and a free Supabase project for sign-in
([sign-in guide](sign-in.md)):
```bash
sudo ./scripts/install.sh --domain lab.example.org \
  --supabase-url https://abcdefghijklmnop.supabase.co \
  --supabase-key sb_publishable_...
```
Caddy, a small web server, gets an HTTPS certificate automatically and forwards to
CyberLab, which is not reachable any other way. The installer refuses a public install
without sign-in. Then set Supabase's **Site URL** and **Redirect URLs** to
`https://lab.example.org/app/`.

## Everyday tasks
| | |
|---|---|
| Update | `git pull && sudo ./scripts/install.sh` (same options as before); data is kept |
| Logs | `sudo docker logs -f cyberlab` |
| Stop and remove, keep data | `sudo ./scripts/install.sh --uninstall` |
| Remove everything, including all lab machines and data | `sudo ./scripts/install.sh --uninstall --purge` |
| Docker Hub download limits | add `--mirror mirror.gcr.io` |

Settings are written to `/etc/cyberlab/cyberlab.env` (readable by root only). Rerun the
installer to change them.

## Tested
The CI job `installer` runs all of this on a fresh Ubuntu server for every change: private
install, a real lab started through the API, an update that keeps the data, a public
install behind Caddy with HTTPS (local certificate) where the API refuses requests without
sign-in and CyberLab is reachable only through Caddy, the refusal of a public install
without sign-in, and a full uninstall.
