"""Common interface every sensor driver implements."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

# Measurement keys a driver may return from read(). main.py maps each key to a
# Prometheus gauge, so a driver only declares which of these it supports.
TEMPERATURE = "temperature_celsius"
HUMIDITY = "relative_humidity_percent"
PRESSURE = "pressure_pascals"


class SensorReadError(Exception):
    """A single read failed; the next interval should simply try again."""


class Sensor(ABC):
    #: Value used for the `sensor` label and for SENSOR=...
    name: str
    #: Measurement keys this sensor produces.
    measurements: tuple[str, ...]

    def open(self) -> None:
        """Acquire the bus/GPIO. Called once at startup."""

    @abstractmethod
    def read(self) -> dict[str, float]:
        """Return a fresh reading, or raise SensorReadError."""

    def close(self) -> None:
        """Release the bus/GPIO. Called once at shutdown."""

    def describe(self) -> str:
        """Human-readable summary of how the sensor is wired, for the startup log."""
        return self.name


def env_int(name: str, default: int | None = None) -> int:
    """Read an int env var. Accepts hex (0x76) as well as decimal."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        if default is None:
            raise ValueError(f"environment variable {name} is required")
        return default
    try:
        return int(raw.strip(), 0)
    except ValueError:
        raise ValueError(f"environment variable {name} must be an integer, got {raw!r}") from None
