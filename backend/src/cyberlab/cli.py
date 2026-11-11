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


def _image_inputs(args: argparse.Namespace):
    reg = load_registry(Path(args.registry) if args.registry else get_settings().registry_dir)
    base = reg.base_images.get(args.base)
    if base is None:
        raise RegistryError(f"unknown base image {args.base!r}")
    ids = [p for p in (args.packs or "").split(",") if p]
    missing = [p for p in ids if p not in reg.tool_packs]
    if missing:
        raise RegistryError(f"unknown tool pack(s): {', '.join(missing)}")
    packs = [reg.tool_packs[p] for p in ids]
    incompatible = [p.id for p in packs if base.id not in p.compatible_bases]
    if incompatible:
        raise RegistryError(f"not compatible with {base.id}: {', '.join(incompatible)}")
    return base, packs


def _cmd_image_dockerfile(args: argparse.Namespace) -> int:
    from cyberlab.execution.images import render

    base, packs = _image_inputs(args)
    image = (
        f"{args.mirror.rstrip('/')}/{base.image.removeprefix('docker.io/')}"
        if (args.mirror and base.image.startswith("docker.io/"))
        else base.image
    )
    recipe = render(image, packs, base.family)
    sys.stdout.write(recipe.dockerfile)
    print(f"# tag: {recipe.tag}", file=sys.stderr)
    return 0


def _cmd_image_commands(args: argparse.Namespace) -> int:
    from cyberlab.execution.images import smoke_test_commands

    _, packs = _image_inputs(args)
    print(" ".join(smoke_test_commands(packs)))
    return 0


def _cmd_image_matrix(args: argparse.Namespace) -> int:
    """JSON build matrix: every buildable pack on every requested compatible base."""
    reg = load_registry(Path(args.registry) if args.registry else get_settings().registry_dir)
    bases = [b for b in args.bases.split(",") if b]
    unknown = [b for b in bases if b not in reg.base_images]
    if unknown:
        raise RegistryError(f"unknown base image(s): {', '.join(unknown)}")
    include = [
        {"base": b, "pack": p.id}
        for p in sorted(reg.tool_packs.values(), key=lambda p: p.id)
        if p.status != "metadata-only"
        for b in bases
        if b in p.compatible_bases
    ]
    print(json.dumps({"include": include}))
    return 0


def _cmd_db(args: argparse.Namespace) -> int:
    from sqlalchemy import create_engine

    from cyberlab import migrations

    engine = create_engine(get_settings().database_url)
    if args.db_command == "upgrade":
        migrations.upgrade(engine)
    rev, head = migrations.current(engine), migrations.head()
    print(f"database revision: {rev or 'none'} (latest: {head})")
    return 0 if args.db_command == "upgrade" or rev == head else 1


def _cmd_reaper(args: argparse.Namespace) -> int:
    from cyberlab.main import create_app, reap_now

    app = create_app(get_settings())
    actions = reap_now(app)
    for a in actions:
        status = "ok" if a.ok else f"FAILED: {a.error}"
        print(
            f"{a.action:7} {a.name} ({a.reason}{', data deleted' if a.delete_data else ''})"
            f" {status}"
        )
    if not actions:
        print("nothing to do")
    return 0 if all(a.ok for a in actions) else 1


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

    im = sub.add_parser("image", help="environment image recipes")
    isub = im.add_subparsers(dest="image_command", required=True)
    for name, func, text in [
        ("dockerfile", _cmd_image_dockerfile, "print the Dockerfile for a base + tool packs"),
        ("commands", _cmd_image_commands, "print the smoke-test commands for tool packs"),
    ]:
        c = isub.add_parser(name, help=text)
        c.add_argument("--base", required=True, help="base image id, e.g. debian-12")
        c.add_argument("--packs", default="", help="comma-separated tool pack ids")
        if name == "dockerfile":
            c.add_argument("--mirror", help="registry mirror for docker.io images")
        c.set_defaults(func=func)

    mx = isub.add_parser("matrix", help="JSON CI build matrix of buildable packs")
    mx.add_argument("--bases", required=True, help="comma-separated base image ids")
    mx.set_defaults(func=_cmd_image_matrix)

    db = sub.add_parser("db", help="database migrations")
    dsub = db.add_subparsers(dest="db_command", required=True)
    for name, text in [
        ("upgrade", "apply pending migrations"),
        ("current", "show the schema revision (exit 1 if out of date)"),
    ]:
        dsub.add_parser(name, help=text).set_defaults(func=_cmd_db)

    rp = sub.add_parser("reaper", help="enforce idle timeouts, TTLs and ephemeral cleanup")
    rsub = rp.add_subparsers(dest="reaper_command", required=True)
    rsub.add_parser("run-once", help="run the reaper once now").set_defaults(func=_cmd_reaper)

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
