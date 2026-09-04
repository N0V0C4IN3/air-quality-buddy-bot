import pytest

from alerting import AlertGate
from common.air_quality import Level
from common.alerts import Alert


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def alert(level, ts=0.0):
    return Alert(level=level, pm25=40.0, pm10=10.0, ts=ts)


@pytest.fixture
def gate_and_sent():
    sent = []
    clock = FakeClock()
    gate = AlertGate(sent.append, cooldown_seconds=1800, clock=clock)
    return gate, sent, clock


def test_first_alert_is_published(gate_and_sent):
    gate, sent, _ = gate_and_sent
    assert gate.submit(alert(Level.WARN)) is True
    assert len(sent) == 1


def test_same_level_is_suppressed_during_cooldown(gate_and_sent):
    gate, sent, clock = gate_and_sent
    gate.submit(alert(Level.WARN))

    clock.advance(1799)
    assert gate.submit(alert(Level.WARN)) is False
    assert len(sent) == 1


def test_same_level_publishes_again_after_cooldown(gate_and_sent):
    gate, sent, clock = gate_and_sent
    gate.submit(alert(Level.WARN))

    clock.advance(1800)
    assert gate.submit(alert(Level.WARN)) is True
    assert len(sent) == 2


def test_escalation_is_never_suppressed(gate_and_sent):
    gate, sent, clock = gate_and_sent
    gate.submit(alert(Level.WARN))

    clock.advance(1)
    assert gate.submit(alert(Level.ERR)) is True
    assert [a.level for a in sent] == [Level.WARN, Level.ERR]


def test_err_does_not_re_escalate_to_itself(gate_and_sent):
    gate, sent, clock = gate_and_sent
    gate.submit(alert(Level.ERR))

    clock.advance(1)
    assert gate.submit(alert(Level.ERR)) is False


def test_de_escalation_stays_quiet_until_cooldown(gate_and_sent):
    """err -> warn is not news; it waits like any repeat."""
    gate, sent, clock = gate_and_sent
    gate.submit(alert(Level.ERR))

    clock.advance(1)
    assert gate.submit(alert(Level.WARN)) is False


def test_ok_readings_never_publish(gate_and_sent):
    gate, sent, _ = gate_and_sent
    assert gate.submit(alert(Level.OK)) is False
    assert sent == []


def test_returning_to_ok_rearms_the_gate(gate_and_sent):
    """Air cleared then went bad again — that is worth saying immediately."""
    gate, sent, clock = gate_and_sent
    gate.submit(alert(Level.WARN))

    clock.advance(1)
    gate.submit(alert(Level.OK))
    assert gate.submit(alert(Level.WARN)) is True
    assert len(sent) == 2


def test_zero_cooldown_publishes_every_alerting_reading():
    sent = []
    clock = FakeClock()
    gate = AlertGate(sent.append, cooldown_seconds=0, clock=clock)

    for _ in range(3):
        assert gate.submit(alert(Level.WARN)) is True
    assert len(sent) == 3


def test_publishes_the_alert_it_was_given():
    sent = []
    gate = AlertGate(sent.append, cooldown_seconds=0, clock=FakeClock())
    original = alert(Level.ERR, ts=1_700_000_000.0)

    gate.submit(original)

    assert sent == [original]
    assert sent[0].routing_key == "alerts.err"


def test_a_failing_publish_does_not_start_the_cooldown():
    """If the broker was down, the next reading must still try."""
    attempts = []

    def flaky(alert):
        attempts.append(alert)
        raise RuntimeError("broker down")

    gate = AlertGate(flaky, cooldown_seconds=1800, clock=FakeClock())

    with pytest.raises(RuntimeError):
        gate.submit(alert(Level.WARN))
    with pytest.raises(RuntimeError):
        gate.submit(alert(Level.WARN))

    assert len(attempts) == 2
