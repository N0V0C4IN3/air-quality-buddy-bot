import os
from dataclasses import dataclass

def _bool(x: str | None, default: bool) -> bool:
    if x is None: return default
    return x.lower() in ("1", "true", "yes", "y", "on")

@dataclass(frozen=True)
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN")
    database_url: str = os.getenv("DATABASE_URL")
    timezone: str = os.getenv("TIMEZONE")
    
    # thresholds for simple /status and alerting
    pm25_warn: float = float(os.getenv("PM25_WARN", "35"))
    pm10_warn: float = float(os.getenv("PM10_WARN", "50"))
    pm25_err: float  = float(os.getenv("PM25_ERR",  "75"))
    pm10_err: float  = float(os.getenv("PM10_ERR",  "100"))

    # alerting scheduler
    alert_check_seconds: int = int(os.getenv("ALERT_CHECK_SECONDS", "300"))  # 5 min
    alert_cooldown_seconds: int = int(os.getenv("ALERT_COOLDOWN_SECONDS", "1800"))  # 30 min
    enable_alerts: bool = _bool(os.getenv("ENABLE_ALERTS"), True)

    redis_host: str = os.getenv("REDIS_HOST")
    redis_port: str = os.getenv("REDIS_PORT")

settings = Settings()
