import math
from types import SimpleNamespace

import pytest

from sampler import FakeSampler, Sample, build_sampler


@pytest.mark.parametrize(
    "pm25, pm10, expected",
    [
        (12.34, 5.678, Sample(12.3, 5.7)),      # rounded to the sensor's precision
        (-3.0, -0.1, Sample(0.0, 0.0)),         # negatives clamped
        (float("nan"), 5.0, Sample(0.0, 5.0)),  # NaN clamped
        (float("inf"), 5.0, Sample(0.0, 5.0)),
        (float("-inf"), 5.0, Sample(0.0, 5.0)),
        (0.0, 0.0, Sample(0.0, 0.0)),
    ],
)
def test_clean_rejects_impossible_values(pm25, pm10, expected):
    assert Sample.clean(pm25, pm10) == expected


def test_clean_output_is_never_nan_or_negative():
    for bad in (float("nan"), float("-inf"), -99.0):
        sample = Sample.clean(bad, bad)
        assert not math.isnan(sample.pm25) and sample.pm25 >= 0
        assert not math.isnan(sample.pm10) and sample.pm10 >= 0


def test_sample_is_immutable():
    sample = Sample(1.0, 2.0)
    with pytest.raises(Exception):
        sample.pm25 = 5.0


def test_fake_sampler_produces_plausible_readings():
    sampler = FakeSampler(seed=7)
    for _ in range(50):
        sample = sampler.read()
        assert isinstance(sample, Sample)
        assert sample.pm25 >= 0 and sample.pm10 >= 0
        assert sample == Sample.clean(sample.pm25, sample.pm10)  # already clean


def test_fake_sampler_is_reproducible_when_seeded():
    assert [FakeSampler(seed=1).read() for _ in range(1)] == [FakeSampler(seed=1).read()]


def test_fake_sampler_varies_between_reads():
    sampler = FakeSampler(seed=3)
    readings = {sampler.read() for _ in range(20)}
    assert len(readings) > 1


def test_fake_sampler_close_is_harmless():
    assert FakeSampler().close() is None


def _settings(**overrides):
    base = dict(
        dry_run=True,
        sds011_port=None,
        sds011_baud=9600,
        sds011_warmup_seconds=0,
        sds011_read_timeout_s=0,
        sds011_retries=1,
        sds011_persist_cfg=False,
        sds011_number_of_readings_per_session=1,
        sds011_interval_between_readings=0,
    )
    base.update(overrides)
    settings = SimpleNamespace(**base)
    settings.uses_hardware = not settings.dry_run and settings.sds011_port is not None
    return settings


def test_dry_run_gets_the_fake_adapter():
    assert isinstance(build_sampler(_settings(dry_run=True)), FakeSampler)


def test_missing_port_gets_the_fake_adapter():
    """No SDS011_PORT means no hardware, whatever DRY_RUN says."""
    assert isinstance(build_sampler(_settings(dry_run=False, sds011_port=None)), FakeSampler)


def test_the_adapter_is_chosen_once_not_per_read():
    """Regression: read() used to decide mock-vs-serial on every call."""
    sampler = build_sampler(_settings(dry_run=True))
    assert type(sampler.read()) is Sample
    assert isinstance(sampler, FakeSampler)
