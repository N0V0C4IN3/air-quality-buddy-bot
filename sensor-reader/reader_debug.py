import serial
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")

def read_frame(ser: serial.Serial):
    while True:
        b = ser.read(1)
        if not b:
            return None
        if b[0] != 0xAA:
            continue
        b2 = ser.read(1)
        if not b2 or b2[0] != 0xC0:
            continue
        rest = ser.read(8)
        if len(rest) != 8:
            return None
        return bytes([0xAA, 0xC0]) + rest

def parse_frame(frame: bytes):
    if len(frame) != 10:
        return None
    pm25_raw = int.from_bytes(frame[2:4], "little")
    pm10_raw = int.from_bytes(frame[4:6], "little")
    checksum = sum(frame[2:8]) % 256
    if checksum != frame[8]:
        return None
    return pm25_raw / 10.0, pm10_raw / 10.0

if __name__ == "__main__":
    ser = serial.Serial("/dev/ttyUSB0", baudrate=9600, timeout=2)
    time.sleep(2)  # let sensor spin up

    while True:
        frame = read_frame(ser)
        if not frame:
            logging.warning("No frame read")
            continue

        parsed = parse_frame(frame)
        if not parsed:
            logging.warning(f"Bad frame: {frame.hex(' ')}")
            continue

        pm25, pm10 = parsed
        # Show raw bytes and values side by side
        logging.info(f"Frame: {frame.hex(' ')}")
        logging.info(f"Parsed: PM2.5={pm25:.1f} µg/m³, PM10={pm10:.1f} µg/m³")
        time.sleep(2)
