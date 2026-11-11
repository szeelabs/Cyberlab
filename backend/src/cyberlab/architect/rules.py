"""Built-in keyword planner: free, offline, no AI.

It recognises common words ("packets", "web", "forensics", "Kali", "two machines", ...) and
fills in a sensible lab. Simple, but predictable, and it needs no account or key.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cyberlab.registry import Registry

# tool pack -> words that suggest it
PACK_WORDS: dict[str, tuple[str, ...]] = {
    "network-analysis": (
        "network",
        "packet",
        "pcap",
        "wireshark",
        "tshark",
        "tcpdump",
        "nmap",
        "traffic",
        "port",
        "sniff",
        "recon",
    ),
    "web-appsec": (
        "web",
        "http",
        "website",
        "mitm",
        "proxy",
        "xss",
        "sql injection",
        "sqli",
        "owasp",
        "api testing",
        "httpie",
    ),
    "forensics": ("forensic", "disk image", "sleuth", "binwalk", "firmware", "evidence", "carving"),
    "crypto": (
        "crypto",
        "encrypt",
        "decrypt",
        "gpg",
        "pgp",
        "openssl",
        "certificate",
        "hash",
        "age",
    ),
    "osint-dns": ("dns", "whois", "osint", "domain", "dig "),
    "vuln-assessment": ("vulnerab", "audit", "harden", "lynis", "compliance", "misconfig"),
    "detection-engineering": (
        "suricata",
        "yara",
        "detection",
        "ids",
        "intrusion",
        "blue team",
        "soc",
        "alert",
    ),
}

BASE_WORDS: dict[str, tuple[str, ...]] = {
    "kali-rolling": ("kali",),
    "parrot-core": ("parrot",),
    "blackarch": ("blackarch", "black arch", "arch linux"),
    "ubuntu-24-04": ("ubuntu",),
    "debian-12": ("debian",),
}
PREFERRED_BASES = ("debian-12", "ubuntu-24-04", "kali-rolling", "parrot-core", "blackarch")
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "a pair of": 2, "couple of": 2}
SIZE_ORDER = ("small", "medium", "large")


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(r"\b" + re.escape(w.strip()), text) for w in words)


def _size_for(min_cpu: float, min_mem: int, wanted: str) -> str:
    from cyberlab.architect.core import SIZES

    for name in SIZE_ORDER[SIZE_ORDER.index(wanted) :]:
        s = SIZES[name]
        if s["cpu"] >= min_cpu and s["memoryMiB"] >= min_mem:
            return name
    return "large"


class RulesModel:
    name = "rules"

    def __init__(self, registry: Registry) -> None:
        self._registry = registry

    def draft(self, *, system: str, prompt: str, schema: dict) -> dict:
        from cyberlab.architect.core import ArchitectError

        machine = schema["properties"]["environments"]["items"]["properties"]
        bases = set(machine["baseImage"]["enum"])
        packs_allowed = set(machine["toolPacks"]["items"]["enum"])
        reg = self._registry
        text = " " + prompt.lower() + " "

        # "no network" describes the network setting, not a wish for network tools
        topic_text = re.sub(
            r"\b(no|without|zero)\s+(network|internet)(\s+access)?\b|\boffline\b", " ", text
        )
        packs = [
            p for p, words in PACK_WORDS.items() if p in packs_allowed and _has(topic_text, words)
        ]
        asked_base = next((b for b, w in BASE_WORDS.items() if b in bases and _has(text, w)), None)

        def fits(base: str) -> bool:
            return all(base in reg.tool_packs[p].compatible_bases for p in packs)

        notes: list[str] = []
        if asked_base and not fits(asked_base):
            dropped = [p for p in packs if asked_base not in reg.tool_packs[p].compatible_bases]
            packs = [p for p in packs if p not in dropped]
            notes.append(
                f"{', '.join(dropped)} does not work on {reg.base_images[asked_base].name} yet, "
                f"so it was left out."
            )
        base = asked_base or next((b for b in PREFERRED_BASES if b in bases and fits(b)), None)
        if base is None:
            raise ArchitectError("no single operating system supports all of those tools")

        if _has(text, ("no network", "offline", "isolated", "air-gapped", "air gapped")):
            network = "isolated"
        elif _has(text, ("internet", "online")):
            network = "internet"
        else:
            network = "lab"
        wanted = (
            "large"
            if _has(text, ("large", "big", "heavy", "powerful"))
            else ("medium" if _has(text, ("medium",)) or len(packs) >= 3 else "small")
        )
        ephemeral = _has(text, ("throwaway", "temporary", "ephemeral", "disposable"))

        two_roles = _has(
            text, ("attacker", "target", "victim", "vulnerable machine", "two machines")
        )
        count = 1
        for word, n in NUMBERS.items():
            if re.search(rf"\b{word}\s+(machines|computers|vms|hosts|boxes)", text):
                count = n
        m = re.search(r"\b([1-6])\s+(machines|computers|vms|hosts|boxes)", text)
        if m:
            count = int(m.group(1))

        def machine_for(name: str, base_id: str, pack_ids: list[str]) -> dict:
            b = reg.base_images[base_id]
            min_cpu = max(
                [b.min_resources.cpu] + [reg.tool_packs[p].min_resources.cpu for p in pack_ids]
            )
            min_mem = max(
                [b.min_resources.memory_mib]
                + [reg.tool_packs[p].min_resources.memory_mib for p in pack_ids]
            )
            return {
                "name": name,
                "baseImage": base_id,
                "toolPacks": pack_ids,
                "size": _size_for(min_cpu, min_mem, wanted),
                "network": network,
                "allowedEgress": [],
                "idleTimeoutMinutes": 60,
                "ephemeral": ephemeral,
            }

        if two_roles or count >= 2:
            if network == "isolated":
                network = "lab"  # machines must be able to reach each other
                notes.append(
                    "Isolated machines cannot reach each other, so they share a lab network."
                )
            envs = [machine_for("attacker", base, packs), machine_for("target", "debian-12", [])]
            for i in range(3, max(count, 2) + 1):
                envs.append(machine_for(f"machine-{i}", "debian-12", []))
        else:
            envs = [machine_for("workstation", base, packs)]

        topic = packs[0] if packs else "practice"
        name = f"{topic}-lab"
        tools = ", ".join(packs) if packs else "no extra tool packs (a clean shell)"
        net_words = {
            "isolated": "has no network at all, the safest option",
            "lab": "can only talk to the other machines in this lab",
            "internet": "has full internet access, which you will need to approve",
        }[network]
        lines = [
            f"{reg.base_images[base].name} with {tools}.",
            f"The lab {net_words}.",
        ]
        if len(envs) > 1:
            lines.insert(
                1,
                "'attacker' has your tools; 'target' is a plain Debian machine to practise on.",
            )
        if ephemeral:
            lines.append("It is throwaway: its files are deleted when it is removed.")
        lines += notes
        lines.append("Drafted by the built-in planner (keyword rules, no AI).")
        return {
            "refused": False,
            "refusalReason": "",
            "name": name,
            "description": prompt.strip().splitlines()[0][:200],
            "explanation": " ".join(lines),
            "environments": envs,
        }
