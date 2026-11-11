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


def test_detection_engineering_not_offered_on_debian(registry):
    # Found by CI: Suricata has no installation candidate in Debian 12 (bookworm) main.
    assert registry.tool_packs["detection-engineering"].compatible_bases == ["ubuntu-24-04"]


def test_package_installs_cannot_hang_forever(registry):
    # Found in CI: a stalled Ubuntu mirror connection hung a build for 10+ minutes.
    df = render(BASE, [registry.tool_packs["crypto"]]).dockerfile
    assert 'Acquire::http::Timeout \\"30\\"' not in df  # rendered for the shell, not escaped
    assert "Acquire::Retries" in df and "Acquire::http::Timeout" in df
    assert df.index("80cyberlab-network") < df.index("apt-get update")


ARCH_BASE = "docker.io/blackarchlinux/blackarch:latest"


def test_arch_recipe_uses_pacman_and_arch_names(registry):
    rec = render(ARCH_BASE, [registry.tool_packs["network-analysis"]], "arch")
    df = rec.dockerfile
    assert "pacman -Syu --noconfirm --needed" in df  # full upgrade, no partial upgrades
    assert "wireshark-cli" in rec.packages and "tshark" not in rec.packages
    assert "apt-get" not in df and "DEBIAN_FRONTEND" not in df
    assert "pacman -Scc" in df and "useradd -u 1000" in df
    # same packs, other package system: a different image
    assert rec.tag != render(ARCH_BASE, [registry.tool_packs["network-analysis"]]).tag


def test_arch_no_packs_means_no_pacman():
    df = render(ARCH_BASE, [], "arch").dockerfile
    assert "pacman" not in df and "useradd" in df


def test_unknown_package_family_rejected(registry):
    with pytest.raises(ImageRecipeError, match="package family"):
        render(BASE, [], "rpm")


def test_new_security_systems_in_registry(registry):
    parrot, blackarch = registry.base_images["parrot-core"], registry.base_images["blackarch"]
    assert parrot.family == "debian" and parrot.image == "docker.io/parrotsec/core:latest"
    assert blackarch.family == "arch"
    assert "blackarch" in registry.tool_packs["web-appsec"].compatible_bases
    # Suricata has not been verified on either, so detection-engineering stays Ubuntu-only
    assert "blackarch" not in registry.tool_packs["detection-engineering"].compatible_bases


def test_cli_dockerfile_for_blackarch(capsys):
    assert main(["image", "dockerfile", "--base", "blackarch", "--packs", "osint-dns"]) == 0
    out = capsys.readouterr().out
    assert "FROM docker.io/blackarchlinux/blackarch:latest" in out and "      bind \\" in out


def test_parrot_uses_its_direct_server(capsys, registry):
    # Found in CI: Parrot's redirector picked an unreachable mirror and the build failed.
    assert main(["image", "dockerfile", "--base", "parrot-core", "--packs", "crypto"]) == 0
    out = capsys.readouterr().out
    rewrite = "s#https://deb.parrot.sh/parrot#https://deb.parrot.sh/direct/parrot#g"
    assert rewrite in out and out.index(rewrite) < out.index("apt-get update")
    assert "sed -i" not in render(BASE, [registry.tool_packs["crypto"]]).dockerfile


@pytest.mark.parametrize(
    "rewrite, family",
    [
        (("https://a.example/x", "http://plain.example/x"), "debian"),  # not https
        (("https://a.example/x", "https://b.example/x#;rm -rf /"), "debian"),  # injection
        (("https://a.example/x", "https://b.example/x"), "arch"),  # apt only
    ],
)
def test_bad_source_rewrites_rejected(registry, rewrite, family):
    with pytest.raises(ImageRecipeError, match="source_rewrite"):
        render(BASE, [registry.tool_packs["crypto"]], family, rewrite)


def test_registry_rejects_unsafe_source_rewrite():
    from cyberlab.registry.models import BaseImage

    with pytest.raises(ValidationError):
        BaseImage.model_validate(
            {
                "id": "x",
                "name": "X",
                "family": "debian",
                "image": "docker.io/library/debian:12",
                "sourceRewrite": {"replace": "https://a.example", "with": "https://b'; id #"},
            }
        )


def test_desktop_recipe_installs_desktop_and_launcher(registry):
    import base64

    from cyberlab.execution.images import DESKTOP_COMMAND, desktop_script

    plain = render("docker.io/library/ubuntu:24.04", [])
    rec = render("docker.io/library/ubuntu:24.04", [], desktop=True)
    assert rec.tag != plain.tag
    for pkg in ("xvfb", "x11vnc", "openbox", "xterm", "socat"):
        assert pkg in rec.packages
    (line,) = [x for x in rec.dockerfile.splitlines() if DESKTOP_COMMAND in x]
    encoded = line.split()[2]
    assert base64.b64decode(encoded).decode() == desktop_script()
    assert "x11vnc -inetd" in desktop_script() and "-nolisten tcp" in desktop_script()
    assert rec.dockerfile.index(DESKTOP_COMMAND) < rec.dockerfile.index("USER 1000:1000")


def test_desktop_recipe_on_arch_uses_arch_names():
    rec = render("docker.io/blackarchlinux/blackarch:latest", [], "arch", desktop=True)
    assert "xorg-server-xvfb" in rec.packages and "xvfb" not in rec.packages
    assert "pacman -Syu" in rec.dockerfile


def test_desktop_script_opens_no_network_port():
    from cyberlab.execution.images import desktop_script

    script = desktop_script()
    assert "-rfbport" not in script and "-listen" not in script
    assert "-nolisten tcp" in script  # the X server itself is not reachable over the network
