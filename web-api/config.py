# config.py
"""One dialect for this service's environment: parsed once, at boot, so a
missing value fails before uvicorn binds rather than on the first request.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timezone
from zoneinfo import ZoneInfo

from common.air_quality import Thresholds

from auth import AuthMode
from ranges import MAX_POINTS


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise ConfigError(f"Missing required env var: {name}")
    return v


def _timezone(name: str):
    tz = os.getenv(name)
    if not tz:
        return timezone.utc
    try:
        return ZoneInfo(tz)
    except Exception as e:
        raise ConfigError(f"{name}={tz!r} is not a known timezone: {e}") from e


def _auth_mode() -> AuthMode:
    raw = os.getenv("WEB_AUTH_MODE", "telegram").strip().lower()
    try:
        return AuthMode(raw)
    except ValueError as e:
        modes = ", ".join(m.value for m in AuthMode)
        raise ConfigError(f"WEB_AUTH_MODE={raw!r} is not one of {modes}") from e


@dataclass(frozen=True)
class Settings:
    database_url: str
    tz: object                       # ZoneInfo or timezone.utc

    thresholds: Thresholds
    reading_interval_seconds: int
    retention_days: int
    max_points: int

    # auth
    auth_mode: AuthMode
    telegram_token: str              # verifies Mini App signatures
    access_token: str                # only used by WEB_AUTH_MODE=token
    init_data_max_age: int

    host: str
    port: int
    log_level: str

    @classmethod
    def load(cls) -> "Settings":
        mode = _auth_mode()
        # A signature can only be checked with the token that produced it, so
        # telegram mode is a configuration error without it rather than a
        # runtime surprise on the first visitor. The other two modes never
        # verify a signature they were not given, so the token stays optional
        # there -- a public dashboard should not demand a bot secret.
        telegram_token = (
            _require("TELEGRAM_TOKEN") if mode is AuthMode.TELEGRAM
            else os.getenv("TELEGRAM_TOKEN", "")
        )
        access_token = os.getenv("WEB_ACCESS_TOKEN", "")
        if mode is AuthMode.TOKEN and not access_token:
            raise ConfigError("WEB_AUTH_MODE=token needs WEB_ACCESS_TOKEN")

        return cls(
            database_url=_require("DATABASE_URL"),
            tz=_timezone("TIMEZONE"),
            thresholds=Thresholds.from_env(),
            reading_interval_seconds=max(1, int(os.getenv("READ_INTERVAL_SECONDS", "300"))),
            retention_days=int(os.getenv("PRUNE_MAX_AGE_DAYS", "90")),
            max_points=int(os.getenv("WEB_MAX_POINTS", str(MAX_POINTS))),
            auth_mode=mode,
            telegram_token=telegram_token,
            access_token=access_token,
            init_data_max_age=int(os.getenv("WEB_INIT_DATA_MAX_AGE", "86400")),
            host=os.getenv("WEB_HOST", "0.0.0.0"),
            port=int(os.getenv("WEB_PORT", "8080")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


settings = Settings.load()
