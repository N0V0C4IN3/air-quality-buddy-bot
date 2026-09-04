"""One full sampling cycle — no hardware, no broker, no Postgres.

This is what the Sampler seam bought: read -> store -> offer to the gate,
exercised end to end.
"""
from datetime import datetime, timezone

import pytest

from alerting import AlertGate
from common.air_quality import Level
from common.db import Reading
from main import sample_once
from sampler import Sample


class ScriptedSampler:
    def __init__(self, *samples):
        self.samples = list(samples)
        self.reads = 0

    def read(self):
        self.reads += 1
        return self.samples.pop(0)

    def close(self):
        pass


class ExplodingSampler:
    def read(self):
        raise RuntimeError("sensor unplugged")

    def close(self):
        pass


@pytest.fixture
def sent():
    return []


@pytest.fixture
def gate(sent):
    clock = iter(range(0, 10_000_000, 10_000))
    return AlertGate(sent.append, cooldown_seconds=0, clock=lambda: next(clock))


def rows(db):
    with db.session() as s:
        return s.query(Reading).order_by(Reading.timestamp).all()


def test_reading_is_stored(db, thresholds, gate):
    sampler = ScriptedSampler(Sample(11.0, 22.0))
    at = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)

    sample, level = sample_once(sampler, db, thresholds, gate, now=at)

    assert sample == Sample(11.0, 22.0)
    assert level is Level.OK
    stored = rows(db)
    assert len(stored) == 1
    assert (stored[0].pm25, stored[0].pm10, stored[0].status) == (11.0, 22.0, "ok")


def test_clean_reading_publishes_nothing(db, thresholds, gate, sent):
    sample_once(ScriptedSampler(Sample(5.0, 5.0)), db, thresholds, gate)
    assert sent == []


def test_warn_reading_publishes_an_alert(db, thresholds, gate, sent):
    sample_once(ScriptedSampler(Sample(40.0, 10.0)), db, thresholds, gate)

    assert len(sent) == 1
    assert sent[0].level is Level.WARN
    assert sent[0].routing_key == "alerts.warn"
    assert (sent[0].pm25, sent[0].pm10) == (40.0, 10.0)


def test_err_reading_publishes_an_err_alert(db, thresholds, gate, sent):
    sample_once(ScriptedSampler(Sample(80.0, 10.0)), db, thresholds, gate)
    assert sent[0].level is Level.ERR


def test_stored_status_matches_the_published_level(db, thresholds, gate, sent):
    sample_once(ScriptedSampler(Sample(80.0, 10.0)), db, thresholds, gate)
    assert rows(db)[0].status == sent[0].level.value


def test_alert_timestamp_matches_the_stored_reading(db, thresholds, gate, sent):
    at = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    sample_once(ScriptedSampler(Sample(80.0, 10.0)), db, thresholds, gate, now=at)

    assert sent[0].ts == at.timestamp()
    assert sent[0].observed_at == at


def test_each_cycle_appends_a_row(db, thresholds, gate):
    sampler = ScriptedSampler(Sample(1.0, 1.0), Sample(2.0, 2.0), Sample(3.0, 3.0))
    for _ in range(3):
        sample_once(sampler, db, thresholds, gate)

    assert [r.pm25 for r in rows(db)] == [1.0, 2.0, 3.0]


def test_a_failed_read_stores_nothing_and_propagates(db, thresholds, gate):
    with pytest.raises(RuntimeError, match="sensor unplugged"):
        sample_once(ExplodingSampler(), db, thresholds, gate)

    assert rows(db) == []


def test_timestamp_defaults_to_now_in_utc(db, thresholds, gate):
    before = datetime.now(timezone.utc)
    sample_once(ScriptedSampler(Sample(1.0, 1.0)), db, thresholds, gate)

    stored = rows(db)[0].timestamp
    stored = stored if stored.tzinfo else stored.replace(tzinfo=timezone.utc)
    assert before <= stored <= datetime.now(timezone.utc)
