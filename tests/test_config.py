"""Both services parse their whole environment at boot and fail on a missing
required value. The two `config` modules share a name, so each is loaded here by
file path under its own name.
"""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

SENSOR_ENV = {
    "DATABASE_URL": "postgresql://u:p@db/aq",
    "RABBITMQ_HOST": "rabbitmq",
    "RABBITMQ_USER": "guest",
    "RABBITMQ_PASS": "guest",
    "AQ_EXCHANGE": "aq.alerts",
}

REPORTER_ENV = {
    "TELEGRAM_TOKEN": "123:abc",
    "DATABASE_URL": "postgresql://u:p@db/aq",
    "REDIS_HOST": "redis",
    "AMQP_URL": "amqp://guest:guest@rabbitmq:5672/",
    "AQ_EXCHANGE": "aq.alerts",
    "AQ_QUEUE_REPORTER": "reporter-bot-alerts",
}

ALL_KEYS = set(SENSOR_ENV) | set(REPORTER_ENV) | {
    "TIMEZONE", "AQ_ROUTING_KEYS", "REDIS_PORT", "ENABLE_ALERTS", "DRY_RUN",
    "SDS011_PORT", "ALERT_COOLDOWN_SECONDS", "PM25_WARN", "PM10_WARN",
    "PM25_ERR", "PM10_ERR", "RABBITMQ_PORT", "AMQP_PREFETCH", "READ_INTERVAL_SECONDS",
}


def load(service: str, monkeypatch, env: dict):
    """Import a service's config module in isolation, under the given environment."""
    for key in ALL_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    name = f"{service.replace('-', '_')}_config"
    path = ROOT / service / "config.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # `from __future__ import annotations` makes @dataclass resolve annotations
    # through sys.modules[cls.__module__], so register before executing.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)   # executes Settings.load() at import
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


# ---------- sensor-reader ----------

def test_sensor_settings_load(monkeypatch):
    cfg = load("sensor-reader", monkeypatch, SENSOR_ENV)

    assert cfg.settings.database_url == "postgresql://u:p@db/aq"
    assert cfg.settings.rabbitmq_port == 5672          # default
    assert cfg.settings.alert_cooldown_seconds == 1800  # default
    assert cfg.settings.thresholds.pm25_warn == 35.0


@pytest.mark.parametrize("missing", sorted(SENSOR_ENV))
def test_sensor_fails_fast_on_a_missing_required_var(monkeypatch, missing):
    env = {k: v for k, v in SENSOR_ENV.items() if k != missing}
    with pytest.raises(Exception, match=missing):
        load("sensor-reader", monkeypatch, env)


def test_no_port_means_no_hardware(monkeypatch):
    cfg = load("sensor-reader", monkeypatch, SENSOR_ENV)
    assert cfg.settings.uses_hardware is False


def test_dry_run_beats_a_configured_port(monkeypatch):
    cfg = load("sensor-reader", monkeypatch,
               {**SENSOR_ENV, "SDS011_PORT": "/dev/ttyUSB0", "DRY_RUN": "true"})
    assert cfg.settings.uses_hardware is False


def test_hardware_is_used_when_a_port_is_set(monkeypatch):
    cfg = load("sensor-reader", monkeypatch, {**SENSOR_ENV, "SDS011_PORT": "/dev/ttyUSB0"})
    assert cfg.settings.uses_hardware is True


@pytest.mark.parametrize("raw, expected", [("true", True), ("1", True), ("on", True),
                                           ("false", False), ("no", False), ("", False)])
def test_booleans_accept_the_documented_spellings(monkeypatch, raw, expected):
    cfg = load("sensor-reader", monkeypatch, {**SENSOR_ENV, "DRY_RUN": raw})
    assert cfg.settings.dry_run is expected


def test_read_interval_is_never_zero(monkeypatch):
    cfg = load("sensor-reader", monkeypatch, {**SENSOR_ENV, "READ_INTERVAL_SECONDS": "0"})
    assert cfg.settings.interval_seconds >= 1


def test_thresholds_come_from_the_environment(monkeypatch):
    cfg = load("sensor-reader", monkeypatch, {**SENSOR_ENV, "PM25_WARN": "12"})
    assert cfg.settings.thresholds.pm25_warn == 12.0


# ---------- reporter-bot ----------

def test_reporter_settings_load(monkeypatch):
    cfg = load("reporter-bot", monkeypatch, REPORTER_ENV)

    assert cfg.settings.telegram_token == "123:abc"
    assert cfg.settings.redis_port == 6379
    assert isinstance(cfg.settings.redis_port, int)   # was a string before
    assert cfg.settings.enable_alerts is True


@pytest.mark.parametrize("missing", sorted(REPORTER_ENV))
def test_reporter_fails_fast_on_a_missing_required_var(monkeypatch, missing):
    env = {k: v for k, v in REPORTER_ENV.items() if k != missing}
    with pytest.raises(Exception, match=missing):
        load("reporter-bot", monkeypatch, env)


def test_routing_keys_default_to_the_alert_bindings(monkeypatch):
    from common.alerts import binding_keys

    cfg = load("reporter-bot", monkeypatch, REPORTER_ENV)
    assert cfg.settings.routing_keys == binding_keys()


@pytest.mark.parametrize(
    "raw", ["alerts.warn,alerts.err", "alerts.warn alerts.err", " alerts.warn , alerts.err "]
)
def test_routing_keys_accept_commas_and_whitespace(monkeypatch, raw):
    cfg = load("reporter-bot", monkeypatch, {**REPORTER_ENV, "AQ_ROUTING_KEYS": raw})
    assert cfg.settings.routing_keys == ["alerts.warn", "alerts.err"]


def test_blank_routing_keys_are_rejected(monkeypatch):
    with pytest.raises(Exception, match="AQ_ROUTING_KEYS"):
        load("reporter-bot", monkeypatch, {**REPORTER_ENV, "AQ_ROUTING_KEYS": " , "})


def test_missing_timezone_falls_back_to_utc(monkeypatch):
    cfg = load("reporter-bot", monkeypatch, REPORTER_ENV)
    assert cfg.settings.timezone_name in ("UTC", "utc")


def test_unknown_timezone_is_rejected(monkeypatch):
    with pytest.raises(Exception, match="TIMEZONE"):
        load("reporter-bot", monkeypatch, {**REPORTER_ENV, "TIMEZONE": "Mars/Olympus_Mons"})


def test_alerts_can_be_switched_off(monkeypatch):
    cfg = load("reporter-bot", monkeypatch, {**REPORTER_ENV, "ENABLE_ALERTS": "false"})
    assert cfg.settings.enable_alerts is False
