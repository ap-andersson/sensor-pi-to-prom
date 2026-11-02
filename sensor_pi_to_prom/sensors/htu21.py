"""HTU21D / SHT21-compatible (temperature, humidity) over I2C, using plain smbus2.

Datasheet: TE Connectivity HTU21D(F), "No Hold Master" measurement mode.
"""

from __future__ import annotations

import time

import smbus2

from .base import HUMIDITY, TEMPERATURE, Sensor, SensorReadError, env_int

_CMD_TEMPERATURE_NO_HOLD = 0xF3
_CMD_HUMIDITY_NO_HOLD = 0xF5
_CMD_SOFT_RESET = 0xFE

# Max conversion times at default resolution (14-bit T: 50 ms, 12-bit RH: 16 ms)
# plus a little margin.
_TEMPERATURE_WAIT_S = 0.055
_HUMIDITY_WAIT_S = 0.020
_SOFT_RESET_WAIT_S = 0.015

# While a conversion is still running the sensor NACKs reads, which shows up as
# OSError. Poll a few times before giving up.
_READ_POLLS = 5
_READ_POLL_INTERVAL_S = 0.010


def _crc8(data: bytes) -> int:
    """CRC-8, polynomial x^8 + x^5 + x^4 + 1 (0x31), init 0x00."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class HTU21(Sensor):
    name = "htu21"
    measurements = (TEMPERATURE, HUMIDITY)

    def __init__(self, bus: int, address: int):
        self.bus_number = bus
        self.address = address
        self._bus: smbus2.SMBus | None = None

    @classmethod
    def from_env(cls) -> "HTU21":
        return cls(bus=env_int("I2C_BUS", 1), address=env_int("I2C_ADDRESS", 0x40))

    def describe(self) -> str:
        return f"{self.name} on /dev/i2c-{self.bus_number} address 0x{self.address:02x}"

    def open(self) -> None:
        self._bus = smbus2.SMBus(self.bus_number)
        self._bus.write_byte(self.address, _CMD_SOFT_RESET)
        time.sleep(_SOFT_RESET_WAIT_S)

    def _measure(self, command: int, wait_s: float) -> int:
        try:
            self._bus.write_byte(self.address, command)
        except OSError as e:
            raise SensorReadError(f"I2C error starting measurement 0x{command:02x}: {e}") from e
        time.sleep(wait_s)

        last_error: OSError | None = None
        for _ in range(_READ_POLLS):
            msg = smbus2.i2c_msg.read(self.address, 3)
            try:
                self._bus.i2c_rdwr(msg)
            except OSError as e:
                last_error = e
                time.sleep(_READ_POLL_INTERVAL_S)
                continue
            data = bytes(msg)
            if _crc8(data[:2]) != data[2]:
                raise SensorReadError(f"CRC mismatch for measurement 0x{command:02x}: {data.hex()}")
            # The two lowest bits are status bits, not part of the value.
            return ((data[0] << 8) | data[1]) & 0xFFFC
        raise SensorReadError(f"no data for measurement 0x{command:02x}: {last_error}")

    def read(self) -> dict[str, float]:
        raw_t = self._measure(_CMD_TEMPERATURE_NO_HOLD, _TEMPERATURE_WAIT_S)
        raw_rh = self._measure(_CMD_HUMIDITY_NO_HOLD, _HUMIDITY_WAIT_S)
        temperature = -46.85 + 175.72 * raw_t / 65536.0
        humidity = -6.0 + 125.0 * raw_rh / 65536.0
        # The formula can go slightly outside 0-100 near saturation.
        humidity = min(max(humidity, 0.0), 100.0)
        return {TEMPERATURE: temperature, HUMIDITY: humidity}

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()
            self._bus = None
