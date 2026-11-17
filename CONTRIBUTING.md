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
Use GitHub Codespaces (recommended, zero local disk) or Python 3.11+ and Node.js 20+ locally.
To just run CyberLab: `./scripts/dev.sh` (see [docs/guides/getting-started.md](docs/guides/getting-started.md)).

Backend:
```bash
cd backend
pip install -e '.[dev]'
ruff check src tests && ruff format src tests
pytest
```
Web app (React, served at `/app`):
```bash
cd frontend
npm ci && npm run typecheck && npm test && npm run build
E2E_PYTHON=../backend/.venv/bin/python npm run e2e      # real browser (Playwright)
E2E_DOCKER=1 E2E_IMAGE_MIRROR=mirror.gcr.io E2E_PYTHON=... npm run e2e   # with real machines
```
Tests that need real Docker: `CYBERLAB_DOCKER_TESTS=1 pytest tests/test_docker_integration.py`
(add `CYBERLAB_DOCKER_BUILD_TESTS=1` where image builds can download packages).

Before a pull request, keep `docs/DEVELOPMENT_STATUS.md` honest: what works, what was
tested and how, and what doesn't work yet.

If you change a database model in `db.py`, add an Alembic migration in
`backend/src/cyberlab/migrations/versions/`; `tests/test_migrations.py` fails until the
migrations match the models.

If you change `manifest/models.py`, regenerate the schema (CI checks for drift):
```bash
cyberlab manifest schema -o ../spec/universe-manifest.schema.json
```

## Adding a tool pack
1. Add `registry/toolpacks/<id>.yaml` (see existing packs; `id` must match the file name).
2. Choose `risk` honestly. `high` triggers an approval requirement. Set
   `requiresIsolation: true` for anything that must never reach the internet.
3. For each tool available as a distribution package, add `package:` (the Debian/Ubuntu/Kali/
   Parrot name) and `command:` (an executable CI checks for). A pack with any packages must
   use `status: buildable`.
4. If the pack lists an Arch-based system (`blackarch`) in `compatibleBases`, every packaged
   tool also needs `archPackage:` (the Arch name, e.g. `tshark` → `wireshark-cli`). The
   registry refuses the pack otherwise, so a wrong guess fails loudly.
5. Only list systems you expect to work: CI builds the pack on Ubuntu, Debian, Parrot OS and
   BlackArch and checks every command, so an unavailable package fails the build.
   Preview a recipe: `cyberlab image dockerfile --base ubuntu-24-04 --packs <id>`
   (add `--desktop` to include the remote desktop).
6. Run `cyberlab registry` and `pytest`. If "Describe your lab" should understand the new
   pack, add its keywords in `backend/src/cyberlab/architect/rules.py` and a case to
   `architect/eval_prompts.yaml`, then run `cyberlab architect eval`.

## Adding an operating system
Add it to `registry/base-images.yaml` with its official container image, `family`
(`debian` for apt, `arch` for pacman) and minimum resources. Set `supportsDesktop: true` only
once the desktop job in CI passes on it. Add it to the `image-plan` and `desktop-images`
CI matrices.

## Pull requests
Small, focused PRs. Describe what changed, how you tested it, and any security impact.

## Releases
Versions follow `MAJOR.MINOR.PATCH` and live in `backend/pyproject.toml`,
`backend/src/cyberlab/__init__.py` and `frontend/package.json` (a test keeps them equal).
Record changes in `CHANGELOG.md`. A maintainer releases by pushing a tag `vX.Y.Z` that
matches the version; the `release` workflow then tests, builds and publishes a GitHub
release with the Python package and the built web app. Nothing is released until a
maintainer pushes a tag.
