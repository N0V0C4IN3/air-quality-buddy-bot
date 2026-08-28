"""SDS011 serial adapter — exercised against a fake port, no hardware."""
import pytest

import sensor
from sampler import Sample
from sensor import SerialSampler, _parse_frame, _read_frame


def frame(pm25: float, pm10: float) -> bytes:
    """Build a valid AA C0 ... AB data frame (values reported in 0.1 µg/m³)."""
    pm25_raw = int(round(pm25 * 10))
    pm10_raw = int(round(pm10 * 10))
    body = pm25_raw.to_bytes(2, "little") + pm10_raw.to_bytes(2, "little") + b"\x00\x01"
    checksum = sum(body) % 256
    return b"\xaa\xc0" + body + bytes([checksum, 0xAB])


class FakeSerial:
    """Feeds a scripted byte stream; empty return means timeout."""

    def __init__(self, stream: bytes = b"", **_kwargs):
        self.stream = bytearray(stream)
        self.written = []
        self.timeout = None
        self.dtr = None
        self.rts = None
        self.is_open = True

    def read(self, n=1):
        chunk, self.stream = bytes(self.stream[:n]), self.stream[n:]
        return chunk

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.written.append(bytes(data))

    def flush(self):
        pass

    def close(self):
        self.is_open = False


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(sensor.time, "sleep", lambda _s: None)


def sampler_over(stream: bytes, monkeypatch, **kwargs) -> SerialSampler:
    port = FakeSerial(stream)
    monkeypatch.setattr(sensor.serial, "Serial", lambda **_kw: port)
    sampler = SerialSampler(
        port="COM-FAKE",
        warmup_seconds=0,
        read_timeout_s=0,
        interval_between_readings=0,
        **kwargs,
    )
    sampler.fake_port = port
    return sampler


# ---------- frame parsing ----------

def test_parse_valid_frame():
    assert _parse_frame(frame(12.5, 30.1)) == (12.5, 30.1)


def test_parse_rejects_bad_checksum():
    bad = bytearray(frame(12.5, 30.1))
    bad[8] ^= 0xFF
    assert _parse_frame(bytes(bad)) is None


@pytest.mark.parametrize("index, value", [(0, 0x00), (1, 0x00), (9, 0x00)])
def test_parse_rejects_wrong_delimiters(index, value):
    bad = bytearray(frame(1.0, 2.0))
    bad[index] = value
    assert _parse_frame(bytes(bad)) is None


def test_parse_rejects_wrong_length():
    assert _parse_frame(frame(1.0, 2.0)[:-1]) is None
    assert _parse_frame(b"") is None


def test_read_frame_finds_a_frame_after_leading_noise():
    port = FakeSerial(b"\x01\x02\xaa\x99" + frame(9.9, 11.1))
    assert _parse_frame(_read_frame(port)) == (9.9, 11.1)


def test_read_frame_returns_none_on_timeout():
    assert _read_frame(FakeSerial(b"")) is None


def test_read_frame_skips_a_corrupt_frame_and_returns_the_next():
    corrupt = bytearray(frame(1.0, 2.0))
    corrupt[8] ^= 0xFF
    port = FakeSerial(bytes(corrupt) + frame(7.7, 8.8))
    assert _parse_frame(_read_frame(port)) == (7.7, 8.8)


# ---------- the read cycle ----------

def test_read_averages_the_session(monkeypatch, no_sleep):
    stream = frame(10.0, 20.0) + frame(20.0, 30.0) + frame(30.0, 40.0)
    sampler = sampler_over(stream, monkeypatch, readings_per_session=3)

    assert sampler.read() == Sample(20.0, 30.0)


def test_mean_is_the_average_of_the_session(monkeypatch, no_sleep):
    stream = frame(10.0, 10.0) + frame(20.0, 20.0)
    sampler = sampler_over(stream, monkeypatch, readings_per_session=2)

    assert sampler.read() == Sample(15.0, 15.0)


def test_mean_divides_by_the_readings_collected_not_the_count_configured(
    monkeypatch, no_sleep
):
    """Regression: the mean used to divide by readings_per_session, so a short
    session would have reported a fraction of the real concentration."""
    sampler = sampler_over(b"", monkeypatch, readings_per_session=4)
    monkeypatch.setattr(sampler, "_collect_session", lambda attempt: [(10.0, 10.0), (20.0, 20.0)])

    assert sampler.read() == Sample(15.0, 15.0)   # not 7.5


def test_read_retries_after_a_timeout(monkeypatch, no_sleep):
    """First attempt starves; the second gets a full session."""
    stream = frame(50.0, 60.0) + frame(50.0, 60.0)
    sampler = sampler_over(stream, monkeypatch, readings_per_session=1, retries=3)

    assert sampler.read() == Sample(50.0, 60.0)


def test_zero_frames_are_discarded_as_warm_up_noise(monkeypatch, no_sleep):
    stream = frame(0.0, 0.0) + frame(5.0, 6.0)
    sampler = sampler_over(stream, monkeypatch, readings_per_session=1, retries=2,
                           extra_settle_s=0)

    assert sampler.read() == Sample(5.0, 6.0)


def test_read_raises_when_no_valid_frame_ever_arrives(monkeypatch, no_sleep):
    sampler = sampler_over(b"", monkeypatch, readings_per_session=1, retries=2)

    with pytest.raises(RuntimeError, match="Failed to read"):
        sampler.read()


def test_sensor_is_put_back_to_sleep_even_when_the_read_fails(monkeypatch, no_sleep):
    sampler = sampler_over(b"", monkeypatch, readings_per_session=1, retries=1)

    with pytest.raises(RuntimeError):
        sampler.read()

    sleep_cmd = bytes([0xAA, 0xB4, 0x06, 0x01, 0x00])
    assert any(w.startswith(sleep_cmd) for w in sampler.fake_port.written)


def test_read_returns_a_cleaned_sample(monkeypatch, no_sleep):
    stream = frame(1.0, 2.0) + frame(1.05, 2.05)
    sampler = sampler_over(stream, monkeypatch, readings_per_session=2)

    sample = sampler.read()
    assert sample == Sample.clean(sample.pm25, sample.pm10)


def test_close_closes_the_port(monkeypatch, no_sleep):
    sampler = sampler_over(b"", monkeypatch)
    sampler.close()
    assert sampler.fake_port.is_open is False
