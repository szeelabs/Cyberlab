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
More labs at every level (intermediate and advanced, multi-network pivoting), completed
badges, and classroom mode (see `docs/plans/ready-made-labs.md`).
