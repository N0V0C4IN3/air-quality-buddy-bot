# sampler.py
"""The seam between the sampling loop and the hardware.

Two adapters sit here: `SerialSampler` (SDS011 over pyserial, in sensor.py) and
`FakeSampler`. Which one is in use is decided once, at startup, by
`build_sampler` — never inside a read.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sample:
    """A validated particulate reading in µg/m³."""

    pm25: float
    pm10: float

    @classmethod
    def clean(cls, pm25: float, pm10: float) -> "Sample":
        """Clamp NaN/inf/negative; round to the sensor's useful precision."""

        def _clean(x: float) -> float:
            if x != x or x in (float("inf"), float("-inf")) or x < 0:
                return 0.0
            return round(float(x), 1)

        return cls(pm25=_clean(pm25), pm10=_clean(pm10))


class Sampler(Protocol):
    def read(self) -> Sample:
        """Return one averaged sample, or raise if the sensor could not be read."""

    def close(self) -> None:
        ...


class FakeSampler:
    """Adapter used for DRY_RUN and for tests. No hardware, no serial port."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def read(self) -> Sample:
        sample = Sample.clean(
            max(0.0, self._rng.gauss(8, 3)),
            max(0.0, self._rng.gauss(12, 4)),
        )
        log.debug("Mock reading: PM2.5=%.1f PM10=%.1f", sample.pm25, sample.pm10)
        return sample

    def close(self) -> None:
        return None


def build_sampler(settings) -> Sampler:
    if not settings.uses_hardware:
        log.info("DRY_RUN or no SDS011_PORT configured; using FakeSampler.")
        return FakeSampler()

    from sensor import SerialSampler  # imported late so tests need no pyserial

    return SerialSampler(
        port=settings.sds011_port,
        baud=settings.sds011_baud,
        warmup_seconds=settings.sds011_warmup_seconds,
        read_timeout_s=settings.sds011_read_timeout_s,
        retries=settings.sds011_retries,
        persist_cfg=settings.sds011_persist_cfg,
        readings_per_session=settings.sds011_number_of_readings_per_session,
        interval_between_readings=settings.sds011_interval_between_readings,
    )
