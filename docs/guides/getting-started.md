# Getting started: your first lab in the browser

This guide takes you from nothing to a running Kali Linux machine you can use from your
browser. You install nothing on your computer and it uses none of your disk: everything runs
on a computer GitHub lends you, called a **Codespace**. Your computer is only the screen.

Time needed: about 15 minutes, most of it waiting for downloads.

## What you need
* A free GitHub account (sign up at github.com).
* A modern browser: Chrome, Edge, Firefox (2021 or newer) or Safari 15+.

## About the free limits (read once)
GitHub gives every free account a monthly allowance of Codespace hours and storage. The
exact numbers change, so check *Settings → Billing and plans* on GitHub. Two habits keep you
well inside it:
* **Stop the Codespace when you finish** (step 7). A stopped Codespace costs no hours.
* **Delete Codespaces you no longer need** (github.com/codespaces). Storage counts while they exist.

Nothing in this guide asks for a credit card. If GitHub ever does, stop: CyberLab never needs
paid resources.

## 1. Open CyberLab in a Codespace
1. Open https://codespaces.new/szeelabs/Cyberlab
2. Click **Create codespace**. Leave the machine type at its default.
3. Wait. The first start takes a few minutes: it installs CyberLab and builds the web app.
   You see an editor (VS Code) in your browser with a **Terminal** panel at the bottom.

**What you should see:** the terminal prints
`Start CyberLab with: ./scripts/dev.sh (then open the forwarded port 8000)`.

## 2. Start CyberLab
Click in the terminal panel, type this and press Enter:
```bash
./scripts/dev.sh
```
It prints a few lines ending in `CyberLab is starting: open http://127.0.0.1:8000/app/`.
A small box appears: **Open in Browser**. Click it. (Missed it? Open the **Ports** tab next to
the terminal and click the globe icon next to port 8000, then add `/app/` to the address.)

Leave this terminal running: it *is* CyberLab. To stop it later, press Ctrl+C there.

**What you should see:** the CyberLab dashboard, with "System status" showing the execution
provider as **CONNECTED**.

## 3. Create a workspace
A workspace is one project: its machines, files and snapshots. Type a name like `first-lab`
in **New workspace name** and click **Create workspace**.

## 4. Describe your lab
On the **Setup** tab, click **Describe your lab** and type what you want to practise, for example:

> Packet analysis on Kali with no internet

Click **Draft my lab**. CyberLab writes the plan for you and explains it. Nothing starts yet.
Click **Use this draft**, then **Save**.

Prefer to choose things yourself? **Build with a form** lets you pick the system, tools,
size and network with checkboxes.

## 5. Create the machine
Open the **Environments** tab and click **Create environments**. The first time, CyberLab
downloads the operating system and installs your tools, which takes a few minutes. Later
machines with the same setup start in seconds.

**What you should see:** the machine's state changes to **running**.

## 6. Use it
* **Terminal:** a Linux command line on the machine. Try `whoami` (it answers `cyberlab`, a
  user without admin rights, which keeps the lab safe) and `nmap --version`.
* **Desktop:** if you ticked *Remote desktop*, a full graphical screen in the browser.
* **Files:** upload files to the machine or download your work.
* **Snapshots:** save the machine's files at a point in time and restore them later.
* **What's wrong?** If anything misbehaves, this button explains why in plain words and
  says what to do.

Your lab network is **closed by default**: the machine can only talk to other machines in
the same lab, not the internet. That is deliberate. Practise only on your own lab machines,
CTFs or systems you have written permission to test.

## 7. Finish (important for your free hours)
1. In CyberLab, **Stop** your machine (its files are kept).
2. In the terminal running CyberLab, press **Ctrl+C**.
3. Stop the Codespace: click **Codespaces** at the bottom-left, then **Stop Current Codespace**.

Next time, open github.com/codespaces, start the same Codespace, and run
`./scripts/dev.sh` again. Your workspaces and machines are still there.

## If something goes wrong
| You see | What it means | What to do |
|---|---|---|
| Provider shows **NOT CONNECTED** | Docker isn't running in the Codespace yet | Wait a minute, then restart `./scripts/dev.sh` |
| A machine shows **failed** | Usually a download problem | Click **What's wrong?** on it; it explains and suggests a fix |
| "Open in Browser" never appeared | The notification was missed | Ports tab → port 8000 → globe icon → add `/app/` |
| The page says the web app isn't built | The first build didn't finish | Run `./scripts/dev.sh --rebuild` |

## Want to run it on your own server instead?
See [own-server.md](own-server.md): CyberLab can run machines on a Linux server you own,
reached over SSH.
