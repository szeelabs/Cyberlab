import json
import re
import tomllib

from cyberlab import __version__

from .conftest import REPO


def test_versions_agree():
    py = tomllib.loads((REPO / "backend" / "pyproject.toml").read_text())["project"]["version"]
    web = json.loads((REPO / "frontend" / "package.json").read_text())["version"]
    assert py == __version__ == web


def test_changelog_has_current_version():
    text = (REPO / "CHANGELOG.md").read_text()
    assert "## [Unreleased]" in text
    assert re.search(rf"^## \[{re.escape(__version__)}\]", text, re.M)
