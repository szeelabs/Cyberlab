"""Troubleshooter: explain in plain words why an environment is not working, and what to do.

Rule-based, free and offline (no AI). It looks at what CyberLab already knows: the
environment's state and last error, what the provider reports about the container
(e.g. "ran out of memory"), what the reaper did to it, and whether a provider is connected.
Every piece of advice has a short title, an explanation a beginner can follow, and a fix.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

Severity = Literal["ok", "info", "warning", "problem"]
STUCK_AFTER = timedelta(minutes=20)


@dataclass
class Advice:
    severity: Severity
    code: str
    title: str
    explanation: str
    fix: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# Known error messages -> what they mean. Checked in order; the first match wins.
_ERROR_RULES: list[tuple[re.Pattern[str], Advice]] = [
    (
        re.compile(
            r"Unable to locate package|has no installation candidate|target not found", re.I
        ),
        Advice(
            "problem",
            "build.package-missing",
            "A tool is not available for this operating system",
            "While building the machine, a tool pack asked for a package that this "
            "operating system's package repository does not have.",
            "Choose another operating system for this machine, or remove that tool pack. The "
            "beginner form only offers tool packs that work on the chosen system.",
        ),
    ),
    (
        re.compile(
            r"toomanyrequests|429|Quota exceeded|rate.?limit|Could not resolve|Temporary failure "
            r"resolving|Failed to fetch|Unable to connect|Connection timed out|i/o timeout|"
            r"TLS handshake timeout",
            re.I,
        ),
        Advice(
            "problem",
            "build.download-failed",
            "Downloading the operating system or tools failed",
            "The computer that runs your machines could not download what it needed. This is "
            "usually temporary: a busy or unreachable download server, or a download limit.",
            "Wait a few minutes and create the machine again. If it keeps happening, the "
            "server owner can set CYBERLAB_IMAGE_MIRROR=mirror.gcr.io.",
        ),
    ),
    (
        re.compile(r"no matching manifest for|exec format error|doesn't run on this server", re.I),
        Advice(
            "problem",
            "build.wrong-processor",
            "This operating system doesn't run on this server's processor",
            "Computers have different processor types (x86, used by most PCs, or ARM, used by "
            "some free cloud servers and phones). This operating system is not published for "
            "the processor of the server running your machines.",
            "Choose another operating system for this machine in Setup. Ubuntu, Debian, Kali "
            "and Parrot OS run on both types; BlackArch needs x86.",
        ),
    ),
    (
        re.compile(r"internet access was not approved", re.I),
        Advice(
            "problem",
            "approval.internet",
            "Internet access needs your approval",
            "This machine asks for full internet access, which CyberLab only allows after you "
            "approve it, so that nothing reaches the internet by accident.",
            "Open the Setup tab and click 'I approve these', then create the machine again.",
        ),
    ),
    (
        re.compile(
            r"Cannot connect to the Docker daemon|docker: not found|connection refused", re.I
        ),
        Advice(
            "problem",
            "provider.unreachable",
            "The computer that runs machines is not reachable",
            "CyberLab could not talk to Docker, the program that runs the machines.",
            "Start Docker (or check the SSH connection to your own server), then try again.",
        ),
    ),
    (
        re.compile(r"timed out|timeout", re.I),
        Advice(
            "problem",
            "build.timeout",
            "Building the machine took too long",
            "Installing the operating system and tools did not finish in time, often because "
            "the download servers were slow.",
            "Try again. Fewer tool packs per machine also build faster.",
        ),
    ),
]


def explain_error(text: str | None) -> Advice | None:
    if not text:
        return None
    for pattern, advice in _ERROR_RULES:
        if pattern.search(text):
            return advice
    return Advice(
        "problem",
        "error.other",
        "Something went wrong",
        "CyberLab recorded this error: " + text.strip()[:500],
        "Try the action again. If it keeps failing, destroy the machine and create it again.",
    )


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def diagnose(
    *,
    state: str,
    last_error: str | None,
    spec: dict,
    provider_connected: bool,
    provider_detail: str = "",
    runtime: dict | None = None,
    last_reaper_event: dict | None = None,
    updated_at: datetime | None = None,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> list[Advice]:
    """Advice for one environment, most important first. Never empty."""
    now = now or datetime.now(UTC)
    out: list[Advice] = []
    name = spec.get("name", "this machine")

    if not provider_connected:
        out.append(
            Advice(
                "problem",
                "provider.not-connected",
                "Machines cannot run right now",
                "CyberLab is not connected to a computer that runs machines"
                + (f" ({provider_detail})." if provider_detail else "."),
                "The server owner needs to start Docker or set CYBERLAB_EXECUTION_PROVIDER=docker. "
                "Your files and settings are safe in the meantime.",
            )
        )

    runtime = runtime or {}
    if runtime.get("OOMKilled"):
        mem = (spec.get("resources") or {}).get("memoryMiB")
        out.append(
            Advice(
                "problem",
                "runtime.out-of-memory",
                "The machine ran out of memory",
                (
                    "A program inside "
                    + name
                    + " used all of the machine's memory"
                    + (f" ({mem} MiB)" if mem else "")
                    + " and was killed; the machine itself is still on."
                    if state == "running"
                    else f"{name} used all of its memory"
                    + (f" ({mem} MiB)" if mem else "")
                    + " and was stopped."
                )
                + " Programs with windows (the desktop, Wireshark) and big scans use a lot of "
                "memory.",
                "Choose a bigger size (Medium or Large) in Setup, save, then destroy and create "
                "the machine again. Your files are kept if 'keep files' is ticked.",
            )
        )

    if state == "failed":
        out.append(
            explain_error(last_error)
            or Advice(
                "problem",
                "state.failed",
                "The machine failed",
                "CyberLab could not create or run this machine, and no error was recorded.",
                "Destroy it and create it again.",
            )
        )
    elif state == "missing":
        out.append(
            Advice(
                "problem",
                "state.missing",
                "The machine disappeared",
                "CyberLab created this machine, but it no longer exists on the computer that runs "
                "it. It was probably removed outside CyberLab, or that computer was reset.",
                "Destroy it here and create it again. Its files survive only if the computer "
                "still has them.",
            )
        )
    elif state == "provisioning":
        updated = _utc(updated_at)
        if updated and now - updated > STUCK_AFTER:
            out.append(
                Advice(
                    "warning",
                    "state.stuck",
                    "Creating the machine is taking very long",
                    "The first machine with new tool packs or a desktop has to be built, which "
                    "can take several minutes, but this one has been going for over 20 minutes.",
                    "Refresh the list. If it is still not running, destroy it and try again.",
                )
            )
        else:
            out.append(
                Advice(
                    "info",
                    "state.provisioning",
                    "The machine is being created",
                    "The first time, CyberLab builds the operating system with your tools, which "
                    "can take a few minutes. Later machines with the same setup start quickly.",
                    "Wait a moment and refresh.",
                )
            )
    elif state == "stopped":
        reason = (last_reaper_event or {}).get("reason")
        if (last_reaper_event or {}).get("action") == "environment.stop" and reason == "idle":
            idle = (spec.get("lifecycle") or {}).get("idleTimeoutMinutes")
            out.append(
                Advice(
                    "info",
                    "reaper.idle-stop",
                    "Stopped because nobody used it",
                    f"{name} was stopped automatically after {idle or 'some'} minutes without use, "
                    "to save resources. Nothing was deleted.",
                    "Click Start. To keep it running longer, raise 'Stop when unused for' in "
                    "Setup.",
                )
            )
        elif runtime.get("ExitCode") not in (None, 0, 137, 143) and not runtime.get("OOMKilled"):
            out.append(
                Advice(
                    "warning",
                    "runtime.crashed",
                    "The machine stopped unexpectedly",
                    f"Its main process ended with code {runtime.get('ExitCode')}.",
                    "Click Start. If it stops again, destroy it and create it again.",
                )
            )
        else:
            out.append(
                Advice(
                    "info",
                    "state.stopped",
                    "The machine is stopped",
                    "It is switched off and uses no CPU or memory. Its files are kept.",
                    "Click Start to use it again.",
                )
            )
    elif state == "destroyed":
        ev = last_reaper_event or {}
        if ev.get("action") == "environment.destroy":
            why = {
                "ttl": "its time limit ran out",
                "idle": "it was a throwaway machine that was not used",
            }.get(ev.get("reason", ""), "of its lifetime settings")
            out.append(
                Advice(
                    "info",
                    "reaper.destroyed",
                    "Removed automatically",
                    f"{name} was removed because {why}.",
                    "Create the environments again from the Environments tab.",
                )
            )
        else:
            out.append(
                Advice(
                    "info",
                    "state.destroyed",
                    "The machine was removed",
                    "It no longer exists.",
                    "Create the environments again from the Environments tab.",
                )
            )
    elif state == "running" and provider_connected and not runtime.get("OOMKilled"):
        out.append(
            Advice(
                "ok",
                "state.running",
                "Running normally",
                "The machine is on. Open its Terminal"
                + (" or Desktop" if (spec.get("access") or {}).get("desktop") else "")
                + " from the Environments tab.",
            )
        )

    exp = _utc(expires_at)
    if exp and state in ("running", "stopped") and exp - now < timedelta(hours=2):
        left = max(0, int((exp - now).total_seconds() // 60))
        out.append(
            Advice(
                "warning",
                "lifecycle.expires-soon",
                f"It will be deleted in about {left} minutes",
                "This machine has a time limit (ttlHours). When it runs out, CyberLab removes it.",
                "Save anything important (Files tab, or a snapshot) before then.",
            )
        )

    if (
        last_error
        and state not in ("failed",)
        and not any(a.code.startswith("build.") for a in out)
    ):
        advice = explain_error(last_error)
        if advice and advice.code != "error.other":
            out.append(advice)
        elif advice:
            out.append(
                Advice(
                    "warning",
                    "error.last",
                    "The last action reported an error",
                    advice.explanation,
                    advice.fix,
                )
            )

    order = {"problem": 0, "warning": 1, "info": 2, "ok": 3}
    return sorted(out, key=lambda a: order[a.severity])
