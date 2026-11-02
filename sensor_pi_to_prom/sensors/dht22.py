"""DHT22 / AM2302 (temperature, humidity) on a single GPIO, via lgpio.

lgpio talks to the kernel GPIO character device (/dev/gpiochipN), so it works
on both the Pi 5 (RP1) and older Pis. On current Raspberry Pi OS kernels the
40-pin header is gpiochip0 on every model, and line numbers are BCM GPIO
numbers. Older Pi 5 kernels exposed the header as gpiochip4 instead; set
GPIO_CHIP=4 if you are on one of those.
"""

from __future__ import annotations

import time

import lgpio

from . import _lg_dht as dht
from .base import HUMIDITY, TEMPERATURE, Sensor, SensorReadError, env_int

_STATUS_TEXT = {
    dht.DHT_TIMEOUT: "timeout (no response from sensor)",
    dht.DHT_BAD_CHECKSUM: "bad checksum",
    dht.DHT_BAD_DATA: "bad data",
}

# The DHT22 needs ~2 s between reads, and single reads fail now and then.
_ATTEMPTS = 3
_RETRY_DELAY_S = 2.0


class DHT22(Sensor):
    name = "dht22"
    measurements = (TEMPERATURE, HUMIDITY)

    def __init__(self, chip: int, pin: int):
        self.chip_number = chip
        self.pin = pin
        self._chip: int | None = None
        self._sensor: dht.sensor | None = None

    @classmethod
    def from_env(cls) -> "DHT22":
        return cls(chip=env_int("GPIO_CHIP", 0), pin=env_int("GPIO_PIN"))

    def describe(self) -> str:
        return f"{self.name} on /dev/gpiochip{self.chip_number} line (BCM GPIO) {self.pin}"

    def open(self) -> None:
        self._chip = lgpio.gpiochip_open(self.chip_number)
        self._sensor = dht.sensor(self._chip, self.pin, dht.DHTXX)

    def read(self) -> dict[str, float]:
        status = dht.DHT_TIMEOUT
        for attempt in range(_ATTEMPTS):
            if attempt:
                time.sleep(_RETRY_DELAY_S)
            _, _, status, temperature, humidity = self._sensor.read()
            if status == dht.DHT_GOOD:
                return {TEMPERATURE: temperature, HUMIDITY: humidity}
        raise SensorReadError(
            f"{_STATUS_TEXT.get(status, f'status {status}')} after {_ATTEMPTS} attempts"
        )

    def close(self) -> None:
        if self._sensor is not None:
            self._sensor.cancel()
            self._sensor = None
        if self._chip is not None:
            lgpio.gpiochip_close(self._chip)
            self._chip = None
