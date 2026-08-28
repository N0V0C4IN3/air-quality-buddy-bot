# config.py
"""One dialect for this service's environment: every variable is parsed here,
once, at boot. A missing required value fails immediately rather than deep
inside SQLAlchemy or on the first alert.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from common.air_quality import Thresholds


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise ConfigError(f"Missing required env var: {name}")
    return v


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class Settings:
    # DB
    database_url: str

    # Sensor
    sds011_port: str | None
    sds011_baud: int

    # Loop
    interval_seconds: int

    # Thresholds
    thresholds: Thresholds

    # App
    log_level: str
    dry_run: bool

    # Sleep -> wake -> read -> sleep cycle
    sds011_warmup_seconds: float
    sds011_read_timeout_s: float
    sds011_retries: int
    sds011_persist_cfg: bool
    sds011_number_of_readings_per_session: int
    sds011_interval_between_readings: float

    # Broker
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_pass: str
    exchange: str
    exchange_type: str
    amqp_heartbeat: int

    # Alerting
    alert_cooldown_seconds: int

    @classmethod
    def load(cls) -> "Settings":
        dry_run = _bool("DRY_RUN", False)
        port = os.getenv("SDS011_PORT") or None
        return cls(
            database_url=_require("DATABASE_URL"),
            sds011_port=port,
            sds011_baud=int(os.getenv("SDS011_BAUD", "9600")),
            interval_seconds=max(1, int(os.getenv("READ_INTERVAL_SECONDS", "30"))),
            thresholds=Thresholds.from_env(),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            dry_run=dry_run,
            sds011_warmup_seconds=float(os.getenv("SDS011_WARMUP_SECONDS", "30")),
            sds011_read_timeout_s=float(os.getenv("SDS011_READ_TIMEOUT_S", "2")),
            sds011_retries=max(1, int(os.getenv("SDS011_RETRIES", "5"))),
            sds011_persist_cfg=_bool("SDS011_PERSIST_CFG", False),
            sds011_number_of_readings_per_session=max(
                1, int(os.getenv("SDS011_NUMBER_OF_READINGS_PER_SESSION", "10"))
            ),
            sds011_interval_between_readings=float(
                os.getenv("SDS011_INTERVAL_BETWEEN_READINGS", "2")
            ),
            rabbitmq_host=_require("RABBITMQ_HOST"),
            rabbitmq_port=int(os.getenv("RABBITMQ_PORT", "5672")),
            rabbitmq_user=_require("RABBITMQ_USER"),
            rabbitmq_pass=_require("RABBITMQ_PASS"),
            exchange=_require("AQ_EXCHANGE"),
            exchange_type=os.getenv("AQ_EXCHANGE_TYPE", "topic"),
            amqp_heartbeat=int(os.getenv("AMQP_HEARTBEAT", "30")),
            alert_cooldown_seconds=int(os.getenv("ALERT_COOLDOWN_SECONDS", "1800")),
        )

    @property
    def uses_hardware(self) -> bool:
        return not self.dry_run and self.sds011_port is not None


settings = Settings.load()
