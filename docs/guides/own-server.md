# Run environments on your own server (over SSH)

CyberLab's control plane can run anywhere (a Codespace, a small VM) while the actual
environments run on **a Linux machine you control**: an old PC, a home server, or a free
cloud VM. CyberLab talks to that machine's Docker Engine **over SSH**, so no Docker port is
ever exposed to the network.

> **Read this first.** Anyone who can use a Docker Engine effectively has root on that
> machine. Use a machine (or VM) dedicated to CyberLab, never your personal computer's main
> account, and keep the SSH key secret.

## 1. On the server: install Docker and create a dedicated user
```bash
sudo apt-get update && sudo apt-get install -y docker.io openssh-server
```
```bash
sudo adduser --disabled-password --gecos "" cyberlab
```
```bash
sudo usermod -aG docker cyberlab
```
*What this does:* installs Docker and SSH, and creates a `cyberlab` user that may use Docker
but has no password (it will only log in with a key).

## 2. On the CyberLab control plane: create a key and authorise it
```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/cyberlab_host
```
Copy the contents of `~/.ssh/cyberlab_host.pub` into `/home/cyberlab/.ssh/authorized_keys`
on the server (file permissions `600`, folder `700`, owned by `cyberlab`).

Then trust the server's host key and test the connection:
```bash
ssh-keyscan -H your-server.example.org >> ~/.ssh/known_hosts
```
```bash
ssh -i ~/.ssh/cyberlab_host cyberlab@your-server.example.org docker version
```
*Expected output:* the Docker client and server versions. If you see "permission denied",
the key or its file permissions are wrong.

Add the key to `~/.ssh/config` so Docker uses it:
```
Host your-server.example.org
    User cyberlab
    IdentityFile ~/.ssh/cyberlab_host
```

## 3. Point CyberLab at the server
```bash
export CYBERLAB_EXECUTION_PROVIDER=docker
export CYBERLAB_DOCKER_HOST=ssh://cyberlab@your-server.example.org
export CYBERLAB_EXECUTION_COST_CLASS=user-owned
```
Check it: `GET /v1/system/execution` should report `CONNECTED` with the server's Docker
version.

## Notes
* **Network:** environments' `lab` networks live on that server. `restricted-egress` needs the
  server to reach the allowed hosts; `internet` needs approval as usual.
* **Images:** the first environment with a new tool-pack combination builds its image on the
  server (needs the server to reach Debian/Ubuntu package mirrors).
* **Costs:** if the server costs money, set `CYBERLAB_EXECUTION_COST_CLASS=paid`. CyberLab then
  refuses to use it until you also set `CYBERLAB_ALLOW_PAID_PROVIDERS=true`.
* **TCP instead of SSH:** possible only with TLS client certificates
  (`CYBERLAB_DOCKER_TLS_VERIFY=true`, `CYBERLAB_DOCKER_CERT_PATH=...`). Plain `tcp://` is
  refused in production, because it gives anyone on the network root on the server.
* **Tested:** CyberLab's CI runs the real-Docker test suite through `ssh://` on every pull
  request. A specific cloud provider's VM has not been tested.
