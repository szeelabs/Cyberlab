"""Hardened YAML loading.

* Uses ``SafeLoader`` (no arbitrary Python object construction).
* Rejects anchors/aliases, which prevents "billion laughs" expansion attacks.
* Callers enforce byte-size limits before parsing.
"""

from __future__ import annotations

import yaml


class ManifestParseError(ValueError):
    pass


class _NoAliasSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):  # type: ignore[override]
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise yaml.composer.ComposerError(
                None, None, "YAML aliases/anchors are not allowed", event.start_mark
            )
        node = super().compose_node(parent, index)
        if getattr(node, "anchor", None):
            raise yaml.composer.ComposerError(
                None, None, "YAML aliases/anchors are not allowed", node.start_mark
            )
        return node


def load_yaml_text(text: str, *, source: str = "<input>") -> object:
    try:
        docs = list(yaml.load_all(text, Loader=_NoAliasSafeLoader))  # noqa: S506 - safe subclass
    except yaml.YAMLError as exc:
        raise ManifestParseError(f"{source}: invalid YAML: {exc}") from exc
    if len(docs) != 1:
        raise ManifestParseError(f"{source}: expected exactly one YAML document, got {len(docs)}")
    return docs[0]
