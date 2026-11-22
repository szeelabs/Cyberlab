# Ready-made labs

![Labs](../images/labs.png)

Open **Labs** in the top menu, pick a lab and press **Start lab**. CyberLab:

1. creates a workspace named after the lab,
2. starts the lab's machines (the first time takes a minute or two while the machine is prepared),
3. puts the lab's files inside them, and
4. opens the **Lab** tab with the tasks.

Open the terminal from the Lab tab, solve a task, paste the answer and press **Check**. Stuck?
**Show a hint** reveals one hint at a time. Every run makes **fresh answers**, so they can't be
copied from someone else (or from this repository). When you're done, destroy the machines
in **Environments** to free your resources, or delete the workspace.

![A lab in progress](../images/lab-tab.png)

## Labs included
| Lab | Level | You practise |
|---|---|---|
| First steps in Linux | beginner | `ls`, `cd`, `cat`, hidden files, `find`, `grep` |
| Log detective: catch the attacker | beginner | reading an SSH log with `grep`, `awk`, `sort`, `uniq`, `wc` |
| Scan a network with nmap | beginner | `nmap`, all-port scans, service versions, banners, `nc` |
| Web recon: find the secrets | beginner | `curl`, page source, `robots.txt`, IDOR |
| Encodings and hashes | beginner | `base64`, hex, ROT13, `sha256sum` |
| Linux file permissions | beginner | `ls -l`, `chmod`, rwx, `find -perm` |
| Incident response: a hacked web server | intermediate | access logs, `awk`, `sort`, `uniq`, indicators of compromise |
| Web: weak logins and leaked keys | intermediate | wordlists, password guessing, secrets in JavaScript |
| Forensics: what's hiding in this file? | intermediate | `grep -a`, `binwalk`, carving with `tail`/`dd`, `gunzip` |
| Recon chain: from a port to admin | advanced | a multi-step chain with checkpoints instead of instructions |

The **Labs** page filters by level and topic, marks labs you've completed, and offers
**Continue** for one you've started.

Labs never reach the internet. Labs without a target have no network at all; labs with a
**practice target** put your machine and the target on a private network of their own, so
you reach it simply by its name (`target`). Lab logs use only addresses reserved for
documentation (RFC 5737), never a real computer.

### Practice targets
A practice target is a small machine running CyberLab's own practice program. It is
*deliberately leaky*, not exploitable: every answer comes from a fixed table the lab
writes when it starts (banners, pages, hidden paths, other users' notes). You can't open
a terminal, desktop or files on a target, just like a real remote computer: you reach it
over the lab network from your own machine.

## Write your own lab
A lab is one YAML file in `registry/labs/<id>.yaml`. CyberLab checks every lab when it
starts and refuses broken ones with a clear message.

```yaml
id: my-first-lab                 # must match the file name
title: My first lab
level: beginner                  # beginner | intermediate | advanced
minutes: 15
topic: linux                     # linux | networking | web | forensics | blue-team | crypto
summary: One sentence shown on the card.
skills: [cat]
introduction: |
  What the learner should know before starting.
spec:                            # the machines: the normal Universe Manifest "spec"
  environments:
    - name: student
      baseImage: ubuntu-24-04
      resources: {cpu: 0.5, memoryMiB: 512, storageGiB: 2}
      network: {mode: isolated}  # labs may use only "isolated" or "lab"
setup:                           # files put into /workspace when the lab starts
  - environment: student
    path: note.txt
    content: "The flag is {{flag:read-it}}"   # replaced by a fresh random flag
tasks:
  - id: read-it
    title: Read the note
    text: Print note.txt with `cat note.txt`.
    hints: ["`cat` prints a file."]
```

Instead of `content`, a setup file can use a built-in **generator** that makes fresh
content for every run:

| Generator | Makes | Answers it provides |
|---|---|---|
| `maze` | a folder of folders with one `treasure.txt` (needs `flagTask`) | the flag |
| `app-log` | a 3000-line application log with one flag line (needs `flagTask`) | the flag |
| `auth-log` | an SSH log with one brute-force attacker | `attacker-ip`, `attacker-user`, `failed-count` |
| `scan-target` | a target config: FTP, SSH and web on 21/22/80, plus a banner on a random high port | `open-ports`, `ssh-version`, `hidden-port`, `banner-flag` |
| `web-target` | a target config: a small web app with a comment, a `robots.txt` path and an IDOR | `source-flag`, `robots-flag`, `idor-flag` |
| `crypto-basics` | a folder with base64, hex and ROT13 messages, and 40 reports, one named by its SHA-256 | `base64-flag`, `hex-flag`, `rot13-flag`, `leaked-file` |
| `permissions` | a non-executable script, a locked file (mode 000) and one world-writable config | `run-flag`, `locked-flag`, `risky-file` |
| `access-log` | a web access log with a scanner, a SQL injection try and a web shell | `scanner-ip`, `scanner-tool`, `webshell-path` |
| `carving` | a "PNG" with a text note inside and gzip data appended | `note-flag`, `gzip-offset`, `carved-flag` |
| `web-login-target` | a target: login with a password from the wordlist, an API key in JavaScript | `admin-user`, `login-flag`, `key-flag` |
| `wordlist` | a small list of common passwords | (none) |
| `chain-target` | a target: web app on a random port, a backup in a comment, a token API | `web-port`, `service-user`, `chain-flag` |

A setup file with `content` can also set `mode: "0755"` (or any permission bits).

A practice target in a lab looks like this (the generator's file goes into the target):

```yaml
    - name: target
      role: target
      baseImage: lab-target
      resources: {cpu: 0.25, memoryMiB: 128, storageGiB: 1}
      network: {mode: lab}
      access: {terminal: false}
setup:
  - environment: target
    path: .cyberlab-target.json
    generator: web-target
```

A task's `answer` is `flag` (the default) or the name of a generated answer, for example
`answer: attacker-ip`. Generators are Python functions in
`backend/src/cyberlab/labs/generators.py`; lab files can never contain code.

Check your lab with the tests: `cd backend && pytest tests/test_labs.py`.

## Coming next
Multi-network pivoting labs, classroom mode, and sharing community labs (see
`docs/plans/ready-made-labs.md`).
