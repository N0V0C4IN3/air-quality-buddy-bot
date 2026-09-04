# air_quality.py
"""Maps a reading to an air-quality level.

Single owner of the threshold rules: sensor-reader uses it to decide whether to
publish an alert, reporter-bot uses it to colour the status block. Pure module —
no DB, no broker, no env access beyond `Thresholds.from_env`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class Level(str, Enum):
    OK = "ok"
    WARN = "warn"
    ERR = "err"

    @property
    def emoji(self) -> str:
        return {Level.OK: "🟢", Level.WARN: "🟠", Level.ERR: "🔴"}[self]

    @property
    def label(self) -> str:
        """The word for the level. One owner, so the card, the caption and the
        alert cannot drift apart."""
        return {Level.OK: "Good", Level.WARN: "Elevated", Level.ERR: "High"}[self]

    @property
    def is_alerting(self) -> bool:
        return self is not Level.OK


@dataclass(frozen=True)
class Thresholds:
    pm25_warn: float = 35.0
    pm10_warn: float = 50.0
    pm25_err: float = 75.0
    pm10_err: float = 100.0

    @classmethod
    def from_env(cls) -> "Thresholds":
        return cls(
            pm25_warn=float(os.getenv("PM25_WARN", "35")),
            pm10_warn=float(os.getenv("PM10_WARN", "50")),
            pm25_err=float(os.getenv("PM25_ERR", "75")),
            pm10_err=float(os.getenv("PM10_ERR", "100")),
        )

    def level(self, pm25: float, pm10: float) -> Level:
        if pm25 >= self.pm25_err or pm10 >= self.pm10_err:
            return Level.ERR
        if pm25 >= self.pm25_warn or pm10 >= self.pm10_warn:
            return Level.WARN
        return Level.OK
