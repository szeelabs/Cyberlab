"""Load and cross-check the on-disk registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from cyberlab.registry.models import BaseImage, Category, ToolPack
from cyberlab.yaml_safe import load_yaml_text


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Registry:
    categories: dict[str, Category] = field(default_factory=dict)
    base_images: dict[str, BaseImage] = field(default_factory=dict)
    tool_packs: dict[str, ToolPack] = field(default_factory=dict)


def _read(path: Path) -> object:
    return load_yaml_text(path.read_text(encoding="utf-8"), source=str(path))


def load_registry(root: Path) -> Registry:
    if not root.is_dir():
        raise RegistryError(f"Registry directory not found: {root}")
    try:
        cats = [Category.model_validate(c) for c in _read(root / "categories.yaml")]
        bases = [BaseImage.model_validate(b) for b in _read(root / "base-images.yaml")]
        pack_files = sorted((root / "toolpacks").glob("*.yaml"))
        packs = [ToolPack.model_validate(_read(p)) for p in pack_files]
    except (ValidationError, TypeError, FileNotFoundError) as exc:
        raise RegistryError(f"Invalid registry: {exc}") from exc

    registry = Registry(
        categories={c.id: c for c in cats},
        base_images={b.id: b for b in bases},
        tool_packs={p.id: p for p in packs},
    )
    problems: list[str] = []
    for kind, items, uniq in (
        ("category", cats, registry.categories),
        ("base image", bases, registry.base_images),
        ("tool pack", packs, registry.tool_packs),
    ):
        if len(items) != len(uniq):
            problems.append(f"duplicate {kind} ids")
    for path, pack in zip(pack_files, packs, strict=True):
        if path.stem != pack.id:
            problems.append(f"{path.name}: id {pack.id!r} must match the file name")
        if pack.category not in registry.categories:
            problems.append(f"{pack.id}: unknown category {pack.category!r}")
        for base in pack.compatible_bases:
            if base not in registry.base_images:
                problems.append(f"{pack.id}: unknown compatible base {base!r}")
            elif registry.base_images[base].family == "arch":
                missing = [t.name for t in pack.tools if t.package and not t.arch_package]
                if missing:
                    problems.append(
                        f"{pack.id}: compatible with {base}, so these tools need an "
                        f"archPackage: {', '.join(missing)}"
                    )
    if problems:
        raise RegistryError("; ".join(problems))
    return registry
