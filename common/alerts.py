# alerts.py
"""The alert contract shared by sensor-reader (publish) and reporter-bot (consume).

Owns the payload shape, the meaning of `ts` (epoch seconds, UTC) and the
`alerts.<level>` routing-key grammar. Both brokers stay adapters around this.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from common.air_quality import Level

ROUTING_KEY_PREFIX = "alerts"
DEFAULT_UNIT = "µg/m³"


class AlertDecodeError(ValueError):
    """Payload did not match the contract."""


@dataclass(frozen=True)
class Alert:
    level: Level
    pm25: float
    pm10: float
    ts: float                      # epoch seconds, UTC
    unit: str = DEFAULT_UNIT

    @property
    def routing_key(self) -> str:
        return f"{ROUTING_KEY_PREFIX}.{self.level.value}"

    @property
    def observed_at(self) -> datetime:
        return datetime.fromtimestamp(self.ts, tz=timezone.utc)

    def encode(self) -> bytes:
        return json.dumps(
            {
                "type": self.level.value,
                "pm25_value": self.pm25,
                "pm10_value": self.pm10,
                "unit": self.unit,
                "ts": self.ts,
            }
        ).encode("utf-8")

    @classmethod
    def decode(cls, body: bytes | str) -> "Alert":
        try:
            raw = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise AlertDecodeError(f"alert body is not JSON: {e}") from e
        if not isinstance(raw, dict):
            raise AlertDecodeError(f"alert body is not an object: {type(raw).__name__}")

        missing = [k for k in ("type", "pm25_value", "pm10_value", "ts") if k not in raw]
        if missing:
            raise AlertDecodeError(f"alert is missing {', '.join(missing)}")

        try:
            level = Level(raw["type"])
        except ValueError as e:
            raise AlertDecodeError(f"unknown alert level {raw['type']!r}") from e

        try:
            return cls(
                level=level,
                pm25=float(raw["pm25_value"]),
                pm10=float(raw["pm10_value"]),
                ts=float(raw["ts"]),
                unit=str(raw.get("unit", DEFAULT_UNIT)),
            )
        except (TypeError, ValueError) as e:
            raise AlertDecodeError(f"alert has non-numeric values: {e}") from e


def binding_keys(levels: tuple[Level, ...] = (Level.WARN, Level.ERR)) -> list[str]:
    """Routing keys a consumer should bind to receive the given levels."""
    return [f"{ROUTING_KEY_PREFIX}.{lvl.value}" for lvl in levels]
