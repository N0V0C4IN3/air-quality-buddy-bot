# config.py
"""One dialect for this service's environment: parsed once, at boot, so a
missing value fails before the bot starts polling rather than on the first alert.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timezone
from zoneinfo import ZoneInfo

from common.air_quality import Thresholds
from common.alerts import binding_keys


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


def _timezone(name: str):
    tz = os.getenv(name)
    if not tz:
        return timezone.utc
    try:
        return ZoneInfo(tz)
    except Exception as e:
        raise ConfigError(f"{name}={tz!r} is not a known timezone: {e}") from e


def _https_url(name: str) -> str:
    """A Mini App URL, or "" when there is none to offer.

    Telegram refuses a web_app button whose URL is not HTTPS, so a plain http
    value here would make every chart card fail to send. Dropping it means the
    dashboard button simply does not appear.
    """
    url = (os.getenv(name) or "").strip()
    if not url:
        return ""
    if not url.startswith("https://"):
        raise ConfigError(f"{name}={url!r} must be an https:// URL for Telegram")
    return url


def _routing_keys() -> list[str]:
    raw = os.getenv("AQ_ROUTING_KEYS")
    if not raw:
        return binding_keys()  # alerts.warn, alerts.err

    parts: list[str] = []
    for chunk in raw.split(","):
        parts.extend(chunk.split())
    keys = [p.strip() for p in parts if p.strip()]
    if not keys:
        raise ConfigError("AQ_ROUTING_KEYS is set but parsed to an empty list")
    return keys


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    database_url: str
    tz: object                       # ZoneInfo or timezone.utc
    timezone_name: str

    thresholds: Thresholds

    # how often the reader is expected to write; drives the stale-sensor card
    reading_interval_seconds: int

    # alerting
    enable_alerts: bool
    alert_cooldown_seconds: int

    # the web dashboard, if one is deployed. Telegram only opens a Mini App
    # over HTTPS, so an http:// value is treated as absent.
    dashboard_url: str

    # redis
    redis_host: str
    redis_port: int

    # broker
    amqp_url: str
    exchange: str
    queue: str
    routing_keys: list[str]
    amqp_prefetch: int
    amqp_retry_delay: float

    log_level: str

    @classmethod
    def load(cls) -> "Settings":
        tz = _timezone("TIMEZONE")
        return cls(
            telegram_token=_require("TELEGRAM_TOKEN"),
            database_url=_require("DATABASE_URL"),
            tz=tz,
            timezone_name=getattr(tz, "key", str(tz)),
            thresholds=Thresholds.from_env(),
            reading_interval_seconds=max(1, int(os.getenv("READ_INTERVAL_SECONDS", "300"))),
            enable_alerts=_bool("ENABLE_ALERTS", True),
            alert_cooldown_seconds=int(os.getenv("ALERT_COOLDOWN_SECONDS", "1800")),
            dashboard_url=_https_url("DASHBOARD_URL"),
            redis_host=_require("REDIS_HOST"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            amqp_url=_require("AMQP_URL"),
            exchange=_require("AQ_EXCHANGE"),
            queue=_require("AQ_QUEUE_REPORTER"),
            routing_keys=_routing_keys(),
            amqp_prefetch=int(os.getenv("AMQP_PREFETCH", "10")),
            amqp_retry_delay=float(os.getenv("AMQP_RETRY_DELAY", "3")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


settings = Settings.load()
