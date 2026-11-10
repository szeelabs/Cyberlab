"""Database migrations (Alembic), shipped inside the package.

``upgrade(engine)`` brings any database to the latest schema. Databases created by older
CyberLab versions with ``create_all`` (before migrations existed) are adopted by stamping
the initial revision, after checking their tables match it.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

HERE = Path(__file__).parent
BASELINE = "0001"
BASELINE_TABLES = {
    "users",
    "workspaces",
    "manifest_revisions",
    "plan_approvals",
    "environments",
    "terminal_tickets",
    "snapshots",
    "audit_events",
}


class MigrationError(RuntimeError):
    pass


def _config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.attributes["engine"] = engine
    return cfg


def head() -> str:
    return ScriptDirectory.from_config(_config_stub()).get_current_head()


def _config_stub() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    return cfg


def current(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def upgrade(engine: Engine, revision: str = "head") -> None:
    cfg = _config(engine)
    tables = set(inspect(engine).get_table_names())
    if current(engine) is None and tables & BASELINE_TABLES:
        missing = BASELINE_TABLES - tables
        if missing:
            raise MigrationError(
                "database has CyberLab tables but no migration history, and is missing "
                f"{sorted(missing)}; it was created by an unknown version. Back it up and "
                "recreate it, or stamp it manually."
            )
        command.stamp(cfg, BASELINE)  # adopt a pre-migrations database
    command.upgrade(cfg, revision)


def downgrade(engine: Engine, revision: str) -> None:
    command.downgrade(_config(engine), revision)
