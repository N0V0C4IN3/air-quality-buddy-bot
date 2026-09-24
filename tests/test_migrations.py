"""Migrations run on container start, so a broken one takes the service down.

These run the real Alembic scripts against a throwaway SQLite file.
"""
import sqlite3

import pytest
from alembic import command
from alembic.config import Config

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic(tmp_path, monkeypatch):
    db_path = tmp_path / "migrations.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg, db_path


def columns(db_path, table):
    with sqlite3.connect(db_path) as conn:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def test_upgrade_head_builds_the_schema(alembic):
    cfg, db_path = alembic
    command.upgrade(cfg, "head")

    assert set(columns(db_path, "chats")) == {"chat_id", "is_subscribed", "chart_theme"}


def test_chart_theme_backfills_existing_rows(alembic):
    """A row written before the column existed must still be valid after."""
    cfg, db_path = alembic
    command.upgrade(cfg, "6977271c47c1")            # before chart_theme
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO chats (chat_id, is_subscribed) VALUES ('7', 1)")

    command.upgrade(cfg, "head")

    with sqlite3.connect(db_path) as conn:
        assert list(conn.execute("SELECT chart_theme FROM chats")) == [("light",)]


# Named revisions, not "-1": a relative step means these tests silently start
# exercising whatever migration was added last.
BEFORE_CHART_THEME = "6977271c47c1"


def test_downgrade_removes_the_column(alembic):
    cfg, db_path = alembic
    command.upgrade(cfg, "head")
    command.downgrade(cfg, BEFORE_CHART_THEME)

    assert "chart_theme" not in columns(db_path, "chats")


def test_upgrade_is_reversible_and_repeatable(alembic):
    cfg, db_path = alembic
    command.upgrade(cfg, "head")
    command.downgrade(cfg, BEFORE_CHART_THEME)
    command.upgrade(cfg, "head")

    assert "chart_theme" in columns(db_path, "chats")


def test_history_has_a_single_head(alembic):
    """Two heads mean `alembic upgrade head` fails on the Pi."""
    from alembic.script import ScriptDirectory

    cfg, _ = alembic
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1


# ---------- readings ----------

READING_COLUMNS = {"id", "pm25", "pm10", "raw_pm25", "raw_pm10", "status", "timestamp"}


def test_upgrade_head_creates_readings(alembic):
    """It used not to. `readings` was built by Database.create_all() at
    sensor-reader startup, so a fresh database had no table until that service
    happened to run - and reporter-bot and web-api both query it."""
    cfg, db_path = alembic
    command.upgrade(cfg, "head")
    assert set(columns(db_path, "readings")) == READING_COLUMNS


def test_readings_is_indexed_on_timestamp(alembic):
    """Every query in the repo filters or orders by it."""
    cfg, db_path = alembic
    command.upgrade(cfg, "head")
    with sqlite3.connect(db_path) as conn:
        names = [r[1] for r in conn.execute("PRAGMA index_list(readings)")]
    assert "ix_readings_timestamp" in names


def test_the_readings_migration_leaves_an_existing_table_alone(alembic):
    """The case every deployed database is in: the table predates the migration.

    It must be adopted, not recreated - recreating it would take the readings
    with it.
    """
    cfg, db_path = alembic
    command.upgrade(cfg, "b3f1a27c94d5")          # the revision before it

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE readings ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, pm25 FLOAT NOT NULL,"
            " pm10 FLOAT NOT NULL, raw_pm25 FLOAT, raw_pm10 FLOAT,"
            " status VARCHAR(16) NOT NULL,"
            " timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO readings (pm25, pm10, status) VALUES (12.5, 20.0, 'ok')"
        )

    command.upgrade(cfg, "head")

    with sqlite3.connect(db_path) as conn:
        rows = list(conn.execute("SELECT pm25, pm10, status FROM readings"))
    assert rows == [(12.5, 20.0, "ok")]


def test_readings_survives_a_repeated_upgrade(alembic):
    cfg, db_path = alembic
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    assert set(columns(db_path, "readings")) == READING_COLUMNS
