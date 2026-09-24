import pytest

from common.air_quality import Thresholds
from common.db import Database


@pytest.fixture
def db(tmp_path):
    """A real Database on a temp SQLite file — same interface the services use."""
    database = Database(url=f"sqlite:///{tmp_path / 'test.db'}")
    database.create_all()
    return database


@pytest.fixture
def thresholds():
    return Thresholds(pm25_warn=35, pm10_warn=50, pm25_err=75, pm10_err=100)
