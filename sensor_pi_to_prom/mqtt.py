"""Optional MQTT publishing, with Home Assistant MQTT discovery.

Enabled when MQTT_HOST is set. Topics (prefix = MQTT_TOPIC_PREFIX):

  <prefix>/<device_id>/state         retained JSON with the latest reading
  <prefix>/<device_id>/availability  retained "online" / "offline" (last will)

With MQTT_HA_DISCOVERY=true (default) one retained discovery config per
measurement is published under <discovery_prefix>/sensor/<device_id>/.../config,
and again whenever Home Assistant announces it has (re)started.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone

import paho.mqtt.client as paho

from .sensors.base import HUMIDITY, PRESSURE, TEMPERATURE, env_bool, env_int

log = logging.getLogger(__name__)

CPU_TEMPERATURE = "cpu_temperature_celsius"

# measurement key -> Home Assistant entity settings
_HA_ENTITIES = {
    TEMPERATURE: {"name": "Temperature", "device_class": "temperature", "unit": "°C", "precision": 1},
    HUMIDITY: {"name": "Humidity", "device_class": "humidity", "unit": "%", "precision": 1},
    # Published in Pa like the Prometheus metric, shown in hPa in Home Assistant.
    PRESSURE: {"name": "Pressure", "device_class": "pressure", "unit": "hPa", "scale": 0.01, "precision": 1},
    CPU_TEMPERATURE: {
        "name": "CPU temperature",
        "device_class": "temperature",
        "unit": "°C",
        "precision": 1,
        "entity_category": "diagnostic",
    },
}

_VALID_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class MqttConfig:
    def __init__(self) -> None:
        self.host = os.getenv("MQTT_HOST", "").strip()
        self.port = env_int("MQTT_PORT", 1883)
        self.username = os.getenv("MQTT_USERNAME") or None
        self.password = os.getenv("MQTT_PASSWORD") or None
        self.device_id = os.getenv("MQTT_DEVICE_ID", "").strip()
        self.device_name = os.getenv("MQTT_DEVICE_NAME", "").strip() or self.device_id
        self.topic_prefix = (os.getenv("MQTT_TOPIC_PREFIX") or "sensor-pi-to-prom").strip().strip("/")
        self.ha_discovery = env_bool("MQTT_HA_DISCOVERY", True)
        self.discovery_prefix = (os.getenv("MQTT_DISCOVERY_PREFIX") or "homeassistant").strip().strip("/")

        if self.enabled:
            if not self.device_id:
                raise ValueError("MQTT_DEVICE_ID is required when MQTT_HOST is set (e.g. the Pi's hostname)")
            if not _VALID_ID.match(self.device_id):
                raise ValueError("MQTT_DEVICE_ID may only contain letters, digits, '_' and '-'")

    @property
    def enabled(self) -> bool:
        return bool(self.host)


class MqttPublisher:
    def __init__(self, config: MqttConfig, sensor_name: str, measurements: tuple[str, ...],
                 interval_s: int, version: str, on_publish_error) -> None:
        self.cfg = config
        self.sensor_name = sensor_name
        self.measurements = measurements
        self.interval_s = interval_s
        self.version = version
        self.on_publish_error = on_publish_error
        self.connected = False
        self._connected_event = threading.Event()

        base = f"{config.topic_prefix}/{config.device_id}"
        self.state_topic = f"{base}/state"
        self.availability_topic = f"{base}/availability"
        self.ha_status_topic = f"{config.discovery_prefix}/status"

        self._client = paho.Client(
            paho.CallbackAPIVersion.VERSION2,
            client_id=f"sensor-pi-to-prom-{config.device_id}",
        )
        if config.username:
            self._client.username_pw_set(config.username, config.password)
        self._client.will_set(self.availability_topic, "offline", qos=1, retain=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def start(self, wait_s: float = 5.0) -> None:
        log.info("MQTT: connecting to %s:%d as device %r", self.cfg.host, self.cfg.port, self.cfg.device_id)
        # connect_async + loop_start: a broker that is down never blocks or
        # crashes the exporter; paho keeps reconnecting in the background.
        self._client.connect_async(self.cfg.host, self.cfg.port, keepalive=60)
        self._client.loop_start()
        # Give the first connection a moment so the first reading is published.
        if not self._connected_event.wait(wait_s):
            log.warning("MQTT: not connected after %.0fs, continuing and retrying in the background", wait_s)

    def stop(self) -> None:
        if self.connected:
            info = self._client.publish(self.availability_topic, "offline", qos=1, retain=True)
            try:
                info.wait_for_publish(timeout=2)
            except (RuntimeError, ValueError):
                pass
        self._client.disconnect()
        self._client.loop_stop()

    # --- paho callbacks (run in paho's network thread) ---

    def _on_connect(self, client, _userdata, _flags, reason_code, _props) -> None:
        if reason_code.is_failure:
            log.warning("MQTT: connection refused: %s", reason_code)
            return
        self.connected = True
        self._connected_event.set()
        log.info("MQTT: connected")
        if self.cfg.ha_discovery:
            client.subscribe(self.ha_status_topic, qos=1)
            self._publish_discovery()
        client.publish(self.availability_topic, "online", qos=1, retain=True)

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _props) -> None:
        if self.connected:
            log.warning("MQTT: disconnected (%s), will reconnect", reason_code)
        self.connected = False

    def _on_message(self, _client, _userdata, msg) -> None:
        if msg.topic == self.ha_status_topic and msg.payload == b"online":
            log.info("MQTT: Home Assistant came online, re-sending discovery")
            self._publish_discovery()

    # --- publishing ---

    def _publish_discovery(self) -> None:
        device = {
            "identifiers": [f"sensor-pi-to-prom_{self.cfg.device_id}"],
            "name": self.cfg.device_name,
            "model": self.sensor_name.upper(),
            "manufacturer": "sensor-pi-to-prom",
            "sw_version": self.version,
        }
        for key in self.measurements:
            entity = _HA_ENTITIES[key]
            value = f"value_json.{key}"
            if "scale" in entity:
                value = f"({value} * {entity['scale']}) | round(2)"
            config = {
                "name": entity["name"],
                "unique_id": f"sensor-pi-to-prom_{self.cfg.device_id}_{key}",
                "state_topic": self.state_topic,
                "value_template": f"{{{{ {value} }}}}",
                "device_class": entity["device_class"],
                "unit_of_measurement": entity["unit"],
                "state_class": "measurement",
                "suggested_display_precision": entity["precision"],
                "availability_topic": self.availability_topic,
                # Go "unavailable" if readings stop (sensor failing) while the
                # service itself is still connected.
                "expire_after": max(3 * self.interval_s, 60),
                "device": device,
            }
            if "entity_category" in entity:
                config["entity_category"] = entity["entity_category"]
            topic = f"{self.cfg.discovery_prefix}/sensor/{self.cfg.device_id}/{key}/config"
            self._client.publish(topic, json.dumps(config), qos=1, retain=True)
        log.info("MQTT: published Home Assistant discovery for %d entities", len(self.measurements))

    def publish_state(self, values: dict[str, float]) -> None:
        if not self.connected:
            # Don't let paho queue up a backlog while the broker is away; the
            # state topic is retained, so the next reading replaces it anyway.
            self.on_publish_error()
            log.warning("MQTT: not connected, skipping publish")
            return
        payload = dict(values)
        payload["sensor"] = self.sensor_name
        payload["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        info = self._client.publish(self.state_topic, json.dumps(payload), qos=1, retain=True)
        if info.rc != paho.MQTT_ERR_SUCCESS:
            self.on_publish_error()
            log.warning("MQTT: publish failed: %s", paho.error_string(info.rc))


def ha_measurements(sensor_measurements: tuple[str, ...], with_cpu: bool) -> tuple[str, ...]:
    return sensor_measurements + ((CPU_TEMPERATURE,) if with_cpu else ())

