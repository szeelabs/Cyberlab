import shutil

import pytest

from cyberlab.registry import RegistryError, load_registry

from .conftest import REPO


def test_registry_loads_and_cross_references(registry):
    assert len(registry.categories) == 10
    assert {"ubuntu-24-04", "debian-12", "kali-rolling"} <= set(registry.base_images)
    for pack in registry.tool_packs.values():
        assert pack.category in registry.categories
        assert set(pack.compatible_bases) <= set(registry.base_images)


def test_registry_rejects_unknown_category(tmp_path):
    root = tmp_path / "registry"
    shutil.copytree(REPO / "registry", root)
    (root / "toolpacks" / "bad.yaml").write_text(
        "id: bad\nname: Bad\nversion: 0.1.0\ncategory: nope\ndescription: x\n"
        "compatibleBases: [debian-12]\ntools: [{name: t, purpose: p}]\n"
    )
    with pytest.raises(RegistryError, match="unknown category"):
        load_registry(root)


def test_registry_missing_dir(tmp_path):
    with pytest.raises(RegistryError):
        load_registry(tmp_path / "missing")


def test_registry_rejects_id_filename_mismatch(tmp_path):
    root = tmp_path / "registry"
    shutil.copytree(REPO / "registry", root)
    (root / "toolpacks" / "crypto.yaml").rename(root / "toolpacks" / "renamed.yaml")
    with pytest.raises(RegistryError, match="must match the file name"):
        load_registry(root)
