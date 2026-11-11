"""AI Lab Architect: turn a plain-language description into a draft Universe Manifest.

The architect only ever *drafts*. A draft is checked by the same policy engine as any
manifest, shown to the user, and must be saved and approved by them before anything runs.
No model output is trusted: it is validated against the registry and the manifest schema.

Planners (``CYBERLAB_AI_PROVIDER``):
* ``rules``     - built-in keyword planner. Free, offline, no AI. The default.
* ``anthropic`` - Claude through the official SDK. Needs ``CYBERLAB_AI_API_KEY`` and, because
  it is a paid service, ``CYBERLAB_ALLOW_PAID_PROVIDERS=true``.
* ``off``       - the feature is disabled.
"""

from __future__ import annotations

from cyberlab.architect.core import (
    SIZES,
    ArchitectError,
    ArchitectModel,
    ArchitectRefused,
    ArchitectUnavailable,
    DraftPlan,
    build_model,
    draft_lab,
    draft_schema,
    plan_to_manifest,
    screen_prompt,
    system_prompt,
)
from cyberlab.architect.rules import RulesModel

__all__ = [
    "SIZES",
    "ArchitectError",
    "ArchitectModel",
    "ArchitectRefused",
    "ArchitectUnavailable",
    "DraftPlan",
    "RulesModel",
    "build_model",
    "draft_lab",
    "draft_schema",
    "plan_to_manifest",
    "screen_prompt",
    "system_prompt",
]
