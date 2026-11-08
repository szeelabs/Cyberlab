import json
from pathlib import Path

import pytest

from cyberlab.cli import main, manifest_json_schema
from cyberlab.manifest.policy import Severity, plan_from_text
from cyberlab.yaml_safe import ManifestParseError, load_yaml_text

from .conftest import REPO

EXAMPLES = sorted((REPO / "examples" / "manifests").glob("*.yaml"))
MAX = 128 * 1024


def manifest(env_overrides="", extra_env="", spec_extra=""):
    return f"""
apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {{name: t}}
spec:
  environments:
    - name: a
      baseImage: ubuntu-24-04
      resources: {{cpu: 2, memoryMiB: 4096, storageGiB: 10}}
{env_overrides}
{extra_env}
{spec_extra}
"""


def codes(plan, severity=None):
    return {f.code for f in plan.findings if severity is None or f.severity == severity}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_are_valid(path, registry, quotas):
    plan = plan_from_text(path.read_text(), registry, quotas, MAX)
    assert plan.valid, plan.findings


def test_committed_schema_matches_models():
    committed = json.loads((REPO / "spec" / "universe-manifest.schema.json").read_text())
    assert committed == manifest_json_schema(), "run: cyberlab manifest schema -o spec/..."


def test_examples_validate_against_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = manifest_json_schema()
    for path in EXAMPLES:
        jsonschema.validate(load_yaml_text(path.read_text()), schema)


def test_yaml_aliases_rejected():
    with pytest.raises(ManifestParseError, match="aliases"):
        load_yaml_text("a: &x [1, 2]\nb: *x\n")


def test_yaml_python_tags_rejected():
    with pytest.raises(ManifestParseError):
        load_yaml_text("a: !!python/object/apply:os.system ['true']\n")


def test_multiple_documents_rejected():
    with pytest.raises(ManifestParseError, match="exactly one"):
        load_yaml_text("a: 1\n---\nb: 2\n")


def test_size_limit(registry, quotas):
    plan = plan_from_text("a" * 200, registry, quotas, max_bytes=100)
    assert not plan.valid and codes(plan) == {"too-large"}


def test_unknown_fields_rejected(registry, quotas):
    plan = plan_from_text(manifest("      privileged: true"), registry, quotas, MAX)
    assert not plan.valid and "schema" in codes(plan)


def test_wrong_api_version(registry, quotas):
    text = manifest().replace("v1alpha1", "v9")
    assert not plan_from_text(text, registry, quotas, MAX).valid


def test_unknown_pack_and_base(registry, quotas):
    text = manifest("      toolPacks: [does-not-exist]").replace("ubuntu-24-04", "plan9")
    plan = plan_from_text(text, registry, quotas, MAX)
    assert {"registry.tool-pack", "registry.base-image"} <= codes(plan, Severity.error)


def test_incompatible_pack(registry, quotas):
    text = manifest("      toolPacks: [web-appsec]").replace("ubuntu-24-04", "debian-12")
    assert "registry.incompatible" in codes(plan_from_text(text, registry, quotas, MAX))


def test_below_minimum_resources(registry, quotas):
    text = manifest("      toolPacks: [forensics]").replace("memoryMiB: 4096", "memoryMiB: 600")
    assert "resources.memory" in codes(plan_from_text(text, registry, quotas, MAX))


def test_quota_exceeded(registry, quotas):
    text = manifest().replace("cpu: 2", "cpu: 16")
    plan = plan_from_text(text, registry, quotas, MAX)
    assert {"quota.cpu", "quota.total-cpu"} <= codes(plan)


def test_too_many_environments(registry, quotas):
    envs = "\n".join(
        f"    - {{name: e{i}, baseImage: debian-12, resources: {{cpu: 0.25, memoryMiB: 256, "
        f"storageGiB: 1}}}}"
        for i in range(5)
    )
    plan = plan_from_text(manifest(extra_env=envs), registry, quotas, MAX)
    assert "quota.environments" in codes(plan)


def test_duplicate_environment_names(registry, quotas):
    dup = (
        "    - {name: a, baseImage: debian-12, "
        "resources: {cpu: 0.5, memoryMiB: 512, storageGiB: 2}}"
    )
    assert not plan_from_text(manifest(extra_env=dup), registry, quotas, MAX).valid


def test_internet_requires_approval(registry, quotas):
    plan = plan_from_text(manifest("      network: {mode: internet}"), registry, quotas, MAX)
    assert plan.valid and plan.requires_approval
    assert "security.internet-egress" in codes(plan, Severity.approval)


def test_egress_list_needs_restricted_mode(registry, quotas):
    text = manifest("      network: {mode: lab, allowedEgress: [example.com]}")
    assert not plan_from_text(text, registry, quotas, MAX).valid


@pytest.mark.parametrize("host", ["*", "*.example.com", "10.0.0.1", "http://x.com", "localhost"])
def test_egress_hosts_must_be_exact(host, registry, quotas):
    text = manifest(f"      network: {{mode: restricted-egress, allowedEgress: ['{host}']}}")
    assert not plan_from_text(text, registry, quotas, MAX).valid


def test_desktop_unsupported_base(registry, quotas):
    text = manifest("      access: {desktop: true}").replace("ubuntu-24-04", "debian-12")
    assert "access.desktop" in codes(plan_from_text(text, registry, quotas, MAX))


def test_desktop_adds_memory_overhead(registry, quotas):
    text = manifest("      access: {desktop: true}").replace("memoryMiB: 4096", "memoryMiB: 1024")
    assert "resources.memory" in codes(plan_from_text(text, registry, quotas, MAX))


def test_no_idle_timeout_warns(registry, quotas):
    text = manifest("      lifecycle: {idleTimeoutMinutes: null}")
    plan = plan_from_text(text, registry, quotas, MAX)
    assert plan.valid and "resources.no-idle-shutdown" in codes(plan, Severity.warning)


def test_isolation_required_pack(registry, quotas):
    pack = registry.tool_packs["forensics"].model_copy(update={"requires_isolation": True})
    reg = type(registry)(
        registry.categories, registry.base_images, {**registry.tool_packs, "forensics": pack}
    )
    text = manifest("      toolPacks: [forensics]\n      network: {mode: internet}")
    assert "security.isolation" in codes(plan_from_text(text, reg, quotas, MAX))


def test_estimate_totals(registry, quotas):
    plan = plan_from_text(
        manifest(spec_extra="  workspace: {storageGiB: 3}"), registry, quotas, MAX
    )
    assert plan.total_cpu == 2 and plan.total_memory_mib == 4096
    assert plan.total_storage_gib == 13


def test_portable_roundtrip(registry, quotas):
    plan = plan_from_text(EXAMPLES[0].read_text(), registry, quotas, MAX)
    again = plan_from_text(json.dumps(plan.manifest), registry, quotas, MAX)
    assert again.manifest == plan.manifest


def test_cli_validate_exit_codes(tmp_path: Path, capsys):
    assert main(["manifest", "validate", str(EXAMPLES[0])]) == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text("kind: Universe\n")
    assert main(["manifest", "validate", str(bad)]) == 1
    assert "INVALID" in capsys.readouterr().out
