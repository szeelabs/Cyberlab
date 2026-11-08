"""Workspace path validation for the file API.

Users give paths relative to their environment's ``/workspace``. Anything that could
escape it, or confuse the tools that receive it, is refused before it reaches Docker.
Paths are always passed to commands as separate arguments, never through a shell.
"""

from __future__ import annotations

import posixpath

WORKSPACE = "/workspace"
MAX_PATH = 1024


class UnsafePath(ValueError):
    pass


def workspace_path(relative: str, *, allow_root: bool = False) -> str:
    """Return the absolute path inside the environment for a user-supplied relative path.

    >>> workspace_path("notes/scan.txt")
    '/workspace/notes/scan.txt'
    """
    if not isinstance(relative, str) or len(relative) > MAX_PATH:
        raise UnsafePath("path is too long")
    if any(ord(c) < 32 or c == "\x7f" for c in relative):
        raise UnsafePath("path contains control characters")
    if relative.startswith("/") or relative.startswith("-") or "\\" in relative:
        raise UnsafePath("use a path relative to /workspace")
    parts = [p for p in relative.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise UnsafePath("'..' is not allowed")
    if any(p.startswith("-") for p in parts):
        raise UnsafePath("path components must not start with '-'")
    if not parts:
        if allow_root:
            return WORKSPACE
        raise UnsafePath("a file path is required")
    full = posixpath.join(WORKSPACE, *parts)
    if not full.startswith(WORKSPACE + "/"):  # defence in depth
        raise UnsafePath("path escapes /workspace")
    return full
