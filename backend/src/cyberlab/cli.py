"""`cyberlab` command-line interface (no server required)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cyberlab.config import QuotaSettings, get_settings
from cyberlab.manifest.models import UniverseManifest
from cyberlab.manifest.policy import plan_from_text
from cyberlab.registry import RegistryError, load_registry

SCHEMA_ID = "https://cyberlab.dev/schemas/universe-manifest/v1alpha1.json"


def manifest_json_schema() -> dict:
    schema = UniverseManifest.model_json_schema(by_alias=True)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "CyberLab Universe Manifest (v1alpha1)",
        **{k: v for k, v in schema.items() if k != "title"},
    }


def _cmd_validate(args: argparse.Namespace) -> int:
    settings = get_settings()
    registry = load_registry(Path(args.registry) if args.registry else settings.registry_dir)
    worst = 0
    for path in args.files:
        text = Path(path).read_text(encoding="utf-8")
        plan = plan_from_text(text, registry, QuotaSettings(), settings.max_manifest_bytes)
        if args.json:
            print(json.dumps({"file": path} | plan.to_dict(), indent=2, default=str))
        else:
            status = "VALID" if plan.valid else "INVALID"
            if plan.valid and plan.requires_approval:
                status += " (requires approval)"
            print(f"{path}: {status}")
            for f in plan.findings:
                loc = f" [{f.path}]" if f.path else ""
                print(f"  {f.severity.value:8} {f.code}{loc}: {f.message}")
            if plan.valid:
                print(
                    f"  estimate: cpu={plan.total_cpu} memoryMiB={plan.total_memory_mib} "
                    f"storageGiB={plan.total_storage_gib}"
                )
        worst = max(worst, 0 if plan.valid else 1)
    return worst


def _cmd_schema(args: argparse.Namespace) -> int:
    text = json.dumps(manifest_json_schema(), indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_registry(args: argparse.Namespace) -> int:
    reg = load_registry(Path(args.registry) if args.registry else get_settings().registry_dir)
    for cat in reg.categories.values():
        packs = [p for p in reg.tool_packs.values() if p.category == cat.id]
        print(f"{cat.id} — {cat.name} ({len(packs)} packs)")
        for p in packs:
            print(f"    {p.id}@{p.version} [risk={p.risk.value}] {p.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cyberlab")
    parser.add_argument("--registry", help="path to registry directory")
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("manifest", help="Universe Manifest tools")
    msub = m.add_subparsers(dest="manifest_command", required=True)
    v = msub.add_parser("validate", help="validate manifest files")
    v.add_argument("files", nargs="+")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=_cmd_validate)
    s = msub.add_parser("schema", help="print the JSON Schema")
    s.add_argument("-o", "--output")
    s.set_defaults(func=_cmd_schema)

    r = sub.add_parser("registry", help="list registry contents")
    r.set_defaults(func=_cmd_registry)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
