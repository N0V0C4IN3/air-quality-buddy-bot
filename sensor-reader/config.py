# config.py
import os
from dataclasses import dataclass
from datetime import timedelta

def _get_bool(val: str | None, default: bool) -> bool:
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "y", "on")

@dataclass(frozen=True)
class Settings:
    # DB
    database_url: str = os.getenv("DATABASE_URL")

    # Sensor
    sds011_port: str | None = os.getenv("SDS011_PORT")      # e.g. "/dev/ttyUSB0"
    sds011_baud: int = int(os.getenv("SDS011_BAUD", "9600"))

    # Loop interval (seconds)
    interval_seconds: int = int(os.getenv("READ_INTERVAL_SECONDS", "30"))

    # Basic thresholds (μg/m³)
    pm25_warn: float = float(os.getenv("PM25_WARN", "35"))
    pm10_warn: float = float(os.getenv("PM10_WARN", "50"))
    pm25_err: float  = float(os.getenv("PM25_ERR",  "75"))
    pm10_err: float  = float(os.getenv("PM10_ERR",  "100"))

    # App
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    dry_run: bool = _get_bool(os.getenv("DRY_RUN"), False)  # if True, don’t touch HW; generate fake data

    # New: sleep/wake/read cycle knobs
    sds011_warmup_seconds: float = float(os.getenv("SDS011_WARMUP_SECONDS", "30"))
    sds011_read_timeout_s: float = float(os.getenv("SDS011_READ_TIMEOUT_S", "2"))
    sds011_retries:        int   = int(os.getenv("SDS011_RETRIES", "5"))
    sds011_persist_cfg:    bool  = _get_bool( os.getenv("SDS011_PERSIST_CFG"),    False)


settings = Settings()
