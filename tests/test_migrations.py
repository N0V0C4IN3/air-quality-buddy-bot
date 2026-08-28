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


def test_downgrade_removes_the_column(alembic):
    cfg, db_path = alembic
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")

    assert "chart_theme" not in columns(db_path, "chats")


def test_upgrade_is_reversible_and_repeatable(alembic):
    cfg, db_path = alembic
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")

    assert "chart_theme" in columns(db_path, "chats")


def test_history_has_a_single_head(alembic):
    """Two heads mean `alembic upgrade head` fails on the Pi."""
    from alembic.script import ScriptDirectory

    cfg, _ = alembic
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
