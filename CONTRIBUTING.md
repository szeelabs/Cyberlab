# Contributing to CyberLab

Thanks for helping build CyberLab!

## Ground rules
* **Working, tested code only.** Don't add features that only look real. If something is a
  stub, label it as one in code and docs.
* **No secrets** in code, tests, fixtures or issues. Use `.env` (gitignored) and `SecretStr`.
* **Security-relevant changes** (auth, policy engine, YAML parsing, execution flags) need tests
  for the failure path, not just the happy path.
* Only tooling for **authorized** security work belongs in the registry.

## Development
Use GitHub Codespaces (recommended, zero local disk) or Python 3.11+ locally:
```bash
cd backend
pip install -e '.[dev]'
ruff check src tests && ruff format src tests
pytest
```
If you change `manifest/models.py`, regenerate the schema (CI checks for drift):
```bash
cyberlab manifest schema -o ../spec/universe-manifest.schema.json
```

## Adding a tool pack
1. Add `registry/toolpacks/<id>.yaml` (see existing packs; `id` must match the file name).
2. Choose `risk` honestly. `high` triggers an approval requirement. Set
   `requiresIsolation: true` for anything that must never reach the internet.
3. Keep `status: metadata-only` until a build recipe and published image exist.
4. Run `cyberlab registry` and `pytest`.

## Pull requests
Small, focused PRs. Describe what changed, how you tested it, and any security impact.
