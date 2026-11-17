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


def test_manifest_skill_example_is_valid(registry):
    from cyberlab.config import QuotaSettings
    from cyberlab.manifest.policy import plan_from_text

    skill = (REPO / ".claude" / "skills" / "cyberlab-manifest" / "SKILL.md").read_text()
    front = skill.split("---")[1]
    assert "name: cyberlab-manifest" in front and "description:" in front
    example = re.search(r"```yaml\n(.*?)```", skill, re.S).group(1)
    plan = plan_from_text(example, registry, QuotaSettings(), 100_000)
    assert plan.valid, plan.findings
