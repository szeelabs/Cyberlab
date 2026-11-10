import os
import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from cyberlab import migrations
from cyberlab.db import Base
from cyberlab.main import create_app

from .conftest import auth
from .test_environments_api import MINIMAL, YAML

POSTGRES = os.environ.get("CYBERLAB_TEST_POSTGRES_URL")


def schema_diff(engine):
    with engine.connect() as conn:
        return compare_metadata(MigrationContext.configure(conn), Base.metadata)


@pytest.fixture
def sqlite_engine(tmp_path):
    return create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")


def test_migrations_match_the_models_exactly(sqlite_engine):
    migrations.upgrade(sqlite_engine)
    assert migrations.current(sqlite_engine) == migrations.head()
    # Any model change without a migration fails here (and in CI).
    assert schema_diff(sqlite_engine) == []


def test_upgrade_is_idempotent(sqlite_engine):
    migrations.upgrade(sqlite_engine)
    migrations.upgrade(sqlite_engine)
    assert migrations.current(sqlite_engine) == migrations.head()


def test_adopts_database_created_before_migrations(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)  # how CyberLab created databases before
    with sqlite_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, subject, created_at) "
                "VALUES ('u1', 'alice', '2026-01-01 00:00:00')"
            )
        )
    migrations.upgrade(sqlite_engine)
    assert migrations.current(sqlite_engine) == migrations.head()
    with sqlite_engine.connect() as conn:  # existing data kept
        assert conn.execute(text("SELECT subject FROM users")).scalar() == "alice"


def test_refuses_unknown_partial_database(sqlite_engine):
    Base.metadata.tables["users"].create(sqlite_engine)
    with pytest.raises(migrations.MigrationError, match="missing"):
        migrations.upgrade(sqlite_engine)


def test_downgrade_and_upgrade_again(sqlite_engine):
    migrations.upgrade(sqlite_engine)
    migrations.downgrade(sqlite_engine, "base")
    assert set(inspect(sqlite_engine).get_table_names()) <= {"alembic_version"}
    migrations.upgrade(sqlite_engine)
    assert schema_diff(sqlite_engine) == []


def test_app_refuses_outdated_database_without_auto_migrate(settings, tmp_path):
    url = f"sqlite:///{tmp_path / 'old.sqlite'}"
    with pytest.raises(RuntimeError, match="cyberlab db upgrade"):
        create_app(
            settings.model_copy(update={"database_url": url, "database_auto_migrate": False})
        )
    migrations.upgrade(create_engine(url))
    create_app(settings.model_copy(update={"database_url": url, "database_auto_migrate": False}))


def test_cli_db_commands(tmp_path, monkeypatch, capsys):
    from cyberlab.cli import main
    from cyberlab.config import get_settings

    monkeypatch.setenv("CYBERLAB_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.sqlite'}")
    get_settings.cache_clear()
    try:
        assert main(["db", "current"]) == 1
        assert "revision: none" in capsys.readouterr().out
        assert main(["db", "upgrade"]) == 0
        assert main(["db", "current"]) == 0
        assert f"revision: {migrations.head()}" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- real PostgreSQL
pg = pytest.mark.skipif(not POSTGRES, reason="set CYBERLAB_TEST_POSTGRES_URL")


@pytest.fixture
def pg_url():
    """A fresh, empty database on the test server for each test."""
    admin = create_engine(POSTGRES, isolation_level="AUTOCOMMIT")
    name = f"cl_{uuid.uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = POSTGRES.rsplit("/", 1)[0] + f"/{name}"
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pg
def test_postgres_migrations_match_models(pg_url):
    engine = create_engine(pg_url)
    migrations.upgrade(engine)
    assert schema_diff(engine) == []
    migrations.downgrade(engine, "base")
    migrations.upgrade(engine)
    assert migrations.current(engine) == migrations.head()
    engine.dispose()


@pg
def test_postgres_api_flow(settings, pg_url):
    """Real requests against PostgreSQL: users, workspaces, revisions, approvals, audit."""
    from .test_environments_api import INTERNET, FakeProvider

    app = create_app(settings.model_copy(update={"database_url": pg_url}))
    app.state.provider = FakeProvider()
    with TestClient(app) as c:
        ws = c.post("/v1/workspaces", json={"name": "pg"}, headers=auth()).json()["id"]
        assert c.post("/v1/workspaces", json={"name": "pg"}, headers=auth()).status_code == 409
        r = c.put(f"/v1/workspaces/{ws}/manifest", content=INTERNET, headers=YAML | auth())
        assert r.status_code == 200
        c.post(
            f"/v1/workspaces/{ws}/approvals",
            json={"revision": 1, "approve": ["security.internet-egress"]},
            headers=auth(),
        )
        (env,) = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
            "environments"
        ]
        assert env["state"] == "running"
        exported = c.get(f"/v1/workspaces/{ws}/manifest?format=json", headers=auth()).json()
        assert exported["metadata"]["name"] == "my-first-universe"
        actions = [e["action"] for e in c.get("/v1/audit", headers=auth()).json()]
        assert actions[:3] == ["environment.provision", "plan.approve", "manifest.import"]
        assert c.get(f"/v1/workspaces/{ws}", headers=auth("mallory")).status_code == 404
    app.state.db.engine.dispose()
    assert MINIMAL
