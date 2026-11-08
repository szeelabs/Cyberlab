import json

import pytest
from pydantic import ValidationError

from cyberlab.cli import main
from cyberlab.execution.images import ImageRecipeError, render, smoke_test_commands
from cyberlab.registry.models import ToolInfo, ToolPack

BASE = "docker.io/library/debian:12-slim"


def test_recipe_installs_packages_and_creates_user(registry):
    rec = render(BASE, [registry.tool_packs["crypto"]])
    df = rec.dockerfile
    assert df.splitlines()[2] == f"FROM {BASE}"
    assert rec.packages == ("age", "gnupg", "openssl")
    assert "apt-get install -y --no-install-recommends" in df
    assert "rm -rf /var/lib/apt/lists/*" in df
    assert "useradd -u 1000 -g 1000 -d /workspace" in df
    assert df.rstrip().endswith("USER 1000:1000\nWORKDIR /workspace")
    assert 'dev.cyberlab.toolpacks="crypto@0.1.0"' in df


def test_no_packs_means_no_apt(registry):
    df = render(BASE, []).dockerfile
    assert "apt-get" not in df and "useradd" in df


def test_tag_is_deterministic_and_content_addressed(registry):
    a, b = registry.tool_packs["crypto"], registry.tool_packs["osint-dns"]
    assert render(BASE, [a, b]).tag == render(BASE, [b, a]).tag  # order-independent
    assert render(BASE, [a]).tag != render(BASE, [a, b]).tag
    assert render(BASE, [a]).tag != render("docker.io/library/ubuntu:24.04", [a]).tag
    assert render(BASE, [a]).tag.startswith("cyberlab-env:")


def test_shared_packages_are_deduplicated(registry):
    p = registry.tool_packs["crypto"]
    twin = p.model_copy(update={"id": "crypto-twin"})
    assert render(BASE, [p, twin]).packages == ("age", "gnupg", "openssl")


@pytest.mark.parametrize("image", ["", "UPPER/case", "x;rm -rf /", "a b"])
def test_unsafe_base_image_rejected(image):
    with pytest.raises(ImageRecipeError):
        render(image, [])


@pytest.mark.parametrize("bad", ["bad name", "x;rm", "-flag", "UPPER", "a\nRUN evil"])
def test_unsafe_package_names_rejected_by_registry(bad):
    with pytest.raises(ValidationError):
        ToolInfo(name="t", purpose="p", package=bad, command="t")


def test_registry_rules_for_packages():
    with pytest.raises(ValidationError, match="smoke-test command"):
        ToolInfo(name="t", purpose="p", package="curl")
    common = {
        "id": "x",
        "name": "X",
        "version": "0.1.0",
        "category": "cryptography",
        "description": "d",
        "compatibleBases": ["debian-12"],
    }
    with pytest.raises(ValidationError, match="must be 'buildable'"):
        ToolPack.model_validate(
            common
            | {"tools": [{"name": "c", "purpose": "p", "package": "curl", "command": "curl"}]}
        )
    with pytest.raises(ValidationError, match="needs at least one package"):
        ToolPack.model_validate(
            common | {"status": "buildable", "tools": [{"name": "c", "purpose": "p"}]}
        )


def test_unpackaged_tools_are_listed_but_not_smoke_tested(registry):
    forensics = registry.tool_packs["forensics"]
    assert any(t.name == "Volatility 3" and t.package is None for t in forensics.tools)
    assert smoke_test_commands([forensics]) == ["binwalk", "fls"]


def test_every_buildable_pack_has_commands(registry):
    for pack in registry.tool_packs.values():
        if pack.status == "buildable":
            assert smoke_test_commands([pack]), pack.id


def test_cli_dockerfile_and_commands(capsys):
    assert (
        main(
            [
                "image",
                "dockerfile",
                "--base",
                "debian-12",
                "--packs",
                "crypto",
                "--mirror",
                "mirror.gcr.io",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "FROM mirror.gcr.io/library/debian:12-slim" in out
    assert main(["image", "commands", "--base", "debian-12", "--packs", "crypto"]) == 0
    assert capsys.readouterr().out.split() == ["age", "gpg", "openssl"]


def test_cli_rejects_bad_combinations(capsys):
    assert main(["image", "dockerfile", "--base", "debian-12", "--packs", "web-appsec"]) == 2
    assert "not compatible" in capsys.readouterr().err
    assert main(["image", "dockerfile", "--base", "plan9"]) == 2
    assert main(["image", "commands", "--base", "debian-12", "--packs", "nope"]) == 2


def test_cli_matrix(capsys):
    assert main(["image", "matrix", "--bases", "ubuntu-24-04,debian-12"]) == 0
    m = json.loads(capsys.readouterr().out)["include"]
    assert {"base": "debian-12", "pack": "crypto"} in m
    assert {"base": "debian-12", "pack": "web-appsec"} not in m  # not compatible
    assert all(x["base"] in ("ubuntu-24-04", "debian-12") for x in m)
