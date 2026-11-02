"""Read one sensor on a fixed interval and expose the values as Prometheus
metrics, and optionally publish them over MQTT (with Home Assistant discovery).

Configuration (environment variables):
  SENSOR                  bme280 | htu21 | dht22               (required)
  RUN_INTERVAL_SECONDS    seconds between reads                (default 60)
  METRICS_PORT            port for /metrics                    (default 8000)
  EXPORT_CPU_TEMPERATURE  also export the Pi's CPU temperature (default true)
  I2C_BUS, I2C_ADDRESS    I2C sensors (defaults: 1, and 0x76 bme280 / 0x40 htu21)
  GPIO_CHIP, GPIO_PIN     dht22 (defaults: chip 0, pin required)
  LOG_LEVEL               DEBUG | INFO | WARNING ...           (default INFO)
  MQTT_*                  optional MQTT publishing, see mqtt.py / README
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

from prometheus_client import (
    GC_COLLECTOR,
    PLATFORM_COLLECTOR,
    PROCESS_COLLECTOR,
    REGISTRY,
    Counter,
    Gauge,
    start_http_server,
)

from . import __version__, sensors
from .cpu import CPU_THERMAL_ZONE, read_cpu_temperature
from .sensors.base import HUMIDITY, PRESSURE, TEMPERATURE, SensorReadError, env_bool, env_int

log = logging.getLogger("sensor_pi_to_prom")

_MEASUREMENT_HELP = {
    TEMPERATURE: "Temperature measured by the sensor, in degrees Celsius.",
    HUMIDITY: "Relative humidity measured by the sensor, in percent.",
    PRESSURE: "Air pressure measured by the sensor, in pascals.",
}


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(message)s",
        stream=sys.stdout,
    )

    try:
        sensor_name = os.getenv("SENSOR", "")
        if not sensor_name:
            raise ValueError(f"environment variable SENSOR is required ({', '.join(sensors.AVAILABLE)})")
        sensor = sensors.create(sensor_name)
        interval = env_int("RUN_INTERVAL_SECONDS", 60)
        port = env_int("METRICS_PORT", 8000)
        export_cpu = env_bool("EXPORT_CPU_TEMPERATURE", True)
        if interval < 1:
            raise ValueError("RUN_INTERVAL_SECONDS must be at least 1")
        mqtt_config = None
        if os.getenv("MQTT_HOST", "").strip():
            from .mqtt import MqttConfig  # paho is only imported when MQTT is used

            mqtt_config = MqttConfig()
    except ValueError as e:
        log.error("Configuration error: %s", e)
        return 2

    stop = threading.Event()

    def handle_signal(signum, _frame):
        log.info("Received %s, shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Only export what this service is about, not Python runtime internals.
    for collector in (GC_COLLECTOR, PLATFORM_COLLECTOR, PROCESS_COLLECTOR):
        REGISTRY.unregister(collector)

    label = {"sensor": sensor.name}
    gauges = {
        m: Gauge(f"sensor_{m}", _MEASUREMENT_HELP[m], ["sensor"]) for m in sensor.measurements
    }
    read_errors = Counter(
        "sensor_read_errors", "Number of failed sensor reads.", ["sensor"]
    ).labels(**label)
    last_success = Gauge(
        "sensor_last_success_timestamp_seconds",
        "Unix time of the last successful sensor read.",
        ["sensor"],
    ).labels(**label)
    cpu_gauge = (
        Gauge("rpi_cpu_temperature_celsius", "Raspberry Pi CPU temperature, in degrees Celsius.")
        if export_cpu
        else None
    )

    mqtt = None
    if mqtt_config is not None:
        from .mqtt import MqttPublisher, ha_measurements

        mqtt_errors = Counter(
            "sensor_mqtt_publish_errors", "Number of MQTT state messages that could not be sent."
        )
        mqtt = MqttPublisher(
            mqtt_config,
            sensor_name=sensor.name,
            measurements=ha_measurements(sensor.measurements, export_cpu),
            interval_s=interval,
            version=__version__,
            on_publish_error=mqtt_errors.inc,
        )
        Gauge("sensor_mqtt_connected", "1 if connected to the MQTT broker.").set_function(
            lambda: 1.0 if mqtt.connected else 0.0
        )

    log.info("Using %s, reading every %ss", sensor.describe(), interval)
    try:
        sensor.open()
    except Exception:
        log.exception("Could not open sensor")
        return 1

    server, server_thread = start_http_server(port)
    log.info("Serving metrics on :%d/metrics", port)
    if mqtt is not None:
        mqtt.start()

    has_values = False
    try:
        while not stop.is_set():
            started = time.monotonic()

            cpu = read_cpu_temperature() if export_cpu else None
            if cpu_gauge is not None:
                if cpu is None:
                    log.debug("CPU temperature unavailable (%s)", CPU_THERMAL_ZONE)
                else:
                    cpu_gauge.set(cpu)

            try:
                values = sensor.read()
            except Exception as e:
                read_errors.inc()
                if isinstance(e, SensorReadError):
                    log.warning("Sensor read failed: %s", e)
                else:
                    log.exception("Unexpected error while reading sensor")
                # Drop the old values so graphs show a gap rather than a stale
                # number; sensor_last_success_timestamp_seconds shows the age.
                if has_values:
                    for gauge in gauges.values():
                        gauge.remove(sensor.name)
                    has_values = False
            else:
                for measurement, value in values.items():
                    gauges[measurement].labels(**label).set(value)
                has_values = True
                last_success.set_to_current_time()
                log.info("Read %s", ", ".join(f"{k}={v:.2f}" for k, v in values.items()))
                if mqtt is not None:
                    if cpu is not None:
                        values = {**values, "cpu_temperature_celsius": cpu}
                    mqtt.publish_state(values)

            stop.wait(max(0.0, interval - (time.monotonic() - started)))
    finally:
        if mqtt is not None:
            mqtt.stop()
        sensor.close()
        server.shutdown()
        server_thread.join(timeout=5)
        log.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
