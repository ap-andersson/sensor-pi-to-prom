"""Raspberry Pi CPU temperature from sysfs (no gpiozero needed)."""

from __future__ import annotations

CPU_THERMAL_ZONE = "/sys/class/thermal/thermal_zone0/temp"


def read_cpu_temperature() -> float | None:
    try:
        with open(CPU_THERMAL_ZONE) as f:
            return int(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None
