import json
from datetime import timezone

import pytest

from common.air_quality import Level
from common.alerts import Alert, AlertDecodeError, binding_keys


def make_alert(level=Level.WARN, pm25=41.2, pm10=55.0, ts=1_700_000_000.0):
    return Alert(level=level, pm25=pm25, pm10=pm10, ts=ts)


def test_round_trip_preserves_everything():
    alert = make_alert()
    assert Alert.decode(alert.encode()) == alert


def test_decode_accepts_str_as_well_as_bytes():
    alert = make_alert()
    assert Alert.decode(alert.encode().decode("utf-8")) == alert


@pytest.mark.parametrize(
    "level, expected", [(Level.WARN, "alerts.warn"), (Level.ERR, "alerts.err")]
)
def test_routing_key_grammar(level, expected):
    assert make_alert(level=level).routing_key == expected


def test_consumer_bindings_cover_every_alerting_level():
    """A level the publisher can emit but nothing binds would be dropped silently."""
    keys = set(binding_keys())
    for level in Level:
        if level.is_alerting:
            assert make_alert(level=level).routing_key in keys


def test_observed_at_is_utc_aware():
    at = make_alert(ts=1_700_000_000.0).observed_at
    assert at.tzinfo is not None
    assert at.utcoffset() == timezone.utc.utcoffset(None)
    assert at.timestamp() == 1_700_000_000.0


def test_wire_format_is_the_documented_one():
    """The keys two services agreed on by convention before this module existed."""
    body = json.loads(make_alert().encode())
    assert body == {
        "type": "warn",
        "pm25_value": 41.2,
        "pm10_value": 55.0,
        "unit": "µg/m³",
        "ts": 1_700_000_000.0,
    }


def test_unit_defaults_when_absent_from_the_payload():
    body = json.dumps({"type": "err", "pm25_value": 1, "pm10_value": 2, "ts": 3})
    assert Alert.decode(body).unit == "µg/m³"


@pytest.mark.parametrize(
    "payload, why",
    [
        (b"not json at all", "garbage"),
        (b"[1, 2, 3]", "not an object"),
        (b'{"type": "warn"}', "missing readings"),
        (b'{"pm25_value": 1, "pm10_value": 2, "ts": 3}', "missing level"),
        (b'{"type": "sideways", "pm25_value": 1, "pm10_value": 2, "ts": 3}', "unknown level"),
        (b'{"type": "warn", "pm25_value": "high", "pm10_value": 2, "ts": 3}', "non-numeric"),
    ],
)
def test_malformed_payloads_raise_rather_than_yielding_none(payload, why):
    with pytest.raises(AlertDecodeError):
        Alert.decode(payload)


def test_decode_error_names_the_missing_fields():
    with pytest.raises(AlertDecodeError, match="pm10_value"):
        Alert.decode(b'{"type": "warn", "pm25_value": 1, "ts": 3}')


def test_numeric_strings_are_coerced():
    """Brokers and hand-written test messages often stringify numbers."""
    body = json.dumps({"type": "warn", "pm25_value": "41.2", "pm10_value": "55", "ts": "1700000000"})
    alert = Alert.decode(body)
    assert (alert.pm25, alert.pm10, alert.ts) == (41.2, 55.0, 1_700_000_000.0)
