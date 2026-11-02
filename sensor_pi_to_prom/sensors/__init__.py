"""Sensor driver registry.

Drivers are imported lazily so that selecting one sensor never imports (or
needs working hardware access for) the libraries of the others.
"""

from __future__ import annotations

import importlib

from .base import Sensor

# SENSOR value -> "module:ClassName"
_DRIVERS = {
    "bme280": "bme280:BME280",
    "htu21": "htu21:HTU21",
    "dht22": "dht22:DHT22",
}

AVAILABLE = tuple(sorted(_DRIVERS))


def create(name: str) -> Sensor:
    key = name.strip().lower()
    if key not in _DRIVERS:
        raise ValueError(f"unknown SENSOR {name!r}, expected one of: {', '.join(AVAILABLE)}")
    module_name, class_name = _DRIVERS[key].split(":")
    module = importlib.import_module(f".{module_name}", __name__)
    return getattr(module, class_name).from_env()
