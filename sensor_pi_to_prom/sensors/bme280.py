"""Bosch BME280 (temperature, humidity, pressure) over I2C."""

from __future__ import annotations

import bme280 as bme280_lib  # PyPI: RPi.bme280
import smbus2

from .base import HUMIDITY, PRESSURE, TEMPERATURE, Sensor, SensorReadError, env_int


class BME280(Sensor):
    name = "bme280"
    measurements = (TEMPERATURE, HUMIDITY, PRESSURE)

    def __init__(self, bus: int, address: int):
        self.bus_number = bus
        self.address = address
        self._bus: smbus2.SMBus | None = None
        self._calibration = None

    @classmethod
    def from_env(cls) -> "BME280":
        return cls(bus=env_int("I2C_BUS", 1), address=env_int("I2C_ADDRESS", 0x76))

    def describe(self) -> str:
        return f"{self.name} on /dev/i2c-{self.bus_number} address 0x{self.address:02x}"

    def open(self) -> None:
        self._bus = smbus2.SMBus(self.bus_number)
        # Calibration constants are burned into the chip; read them once.
        self._calibration = bme280_lib.load_calibration_params(self._bus, self.address)

    def read(self) -> dict[str, float]:
        try:
            data = bme280_lib.sample(self._bus, self.address, self._calibration)
        except OSError as e:
            raise SensorReadError(f"I2C error: {e}") from e
        return {
            TEMPERATURE: data.temperature,
            HUMIDITY: data.humidity,
            PRESSURE: data.pressure * 100.0,  # library returns hPa
        }

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()
            self._bus = None
