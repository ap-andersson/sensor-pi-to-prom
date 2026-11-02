# sensor-pi-to-prom

Reads one temperature/humidity sensor on a Raspberry Pi and exposes it as a
Prometheus endpoint, and optionally publishes it over MQTT with Home Assistant
discovery. One image for all supported sensors; pick the sensor with `SENSOR=`.

Replaces `rpi-bme280-to-prom`, `rpi-htu21-to-prom` and `rpi-dht22-to-prom`.

| `SENSOR` | Bus | Measures | Tested on |
|---|---|---|---|
| `bme280` | I2C, default address `0x76` | temperature, humidity, pressure | Pi 5 |
| `htu21`  | I2C, default address `0x40` | temperature, humidity | Pi 5 |
| `dht22`  | one GPIO (`GPIO_PIN`, BCM number) | temperature, humidity | **untested** |

All hardware access goes through the kernel character devices (`/dev/i2c-N`,
`/dev/gpiochipN`) via `smbus2` and `lgpio`, which should work on older Pi
models as well as the Pi 5. The container gets only that one device node, not
`privileged: true`.

> **Testing status:** only the BME280 and HTU21 have been tested, and only on a
> Raspberry Pi 5 (64-bit Raspberry Pi OS, kernel 6.18). The DHT22 driver and
> other Pi models (Pi 4 and older) have not been tested at all yet.

> **AI assistance:** parts of this project (code, scripts and documentation)
> were created with the help of Claude (Anthropic's AI assistant). Commits
> with AI involvement carry a `Co-Authored-By: Claude` trailer.

## Run

On the Pi, in a folder with `docker-compose.yml`:

```sh
cp .env.example .env   # set SENSOR and SENSOR_DEVICE
docker compose pull && docker compose up -d
curl -s localhost:8000/metrics
```

## Metrics

| Metric | Notes |
|---|---|
| `sensor_temperature_celsius{sensor}` | |
| `sensor_relative_humidity_percent{sensor}` | |
| `sensor_pressure_pascals{sensor}` | bme280 only |
| `sensor_read_errors_total{sensor}` | failed reads (DHT22 counts a read as failed after 3 attempts) |
| `sensor_last_success_timestamp_seconds{sensor}` | alert on `time() - this > 300` to catch a dead sensor |
| `rpi_cpu_temperature_celsius` | disable with `EXPORT_CPU_TEMPERATURE=false` |

If a read fails, the measurement series are removed until the next good read,
so graphs show a gap instead of a stale value.

## MQTT and Home Assistant (optional)

Set `MQTT_HOST` and `MQTT_DEVICE_ID` to also publish every reading over MQTT.
Prometheus keeps working as before, also while the broker is unreachable
(the client reconnects in the background).

| Topic | Retained | Content |
|---|---|---|
| `sensor-pi-to-prom/<device_id>/state` | yes | JSON with the latest reading |
| `sensor-pi-to-prom/<device_id>/availability` | yes | `online` / `offline` (also sent as last will if the container dies) |

```json
{"temperature_celsius": 22.69, "relative_humidity_percent": 27.19, "pressure_pascals": 102335.13,
 "cpu_temperature_celsius": 39.15, "sensor": "bme280", "timestamp": "2026-11-02T12:46:04+00:00"}
```

With `MQTT_HA_DISCOVERY=true` (the default) the service also publishes
[Home Assistant MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)
configs, so a device with temperature, humidity, pressure (shown in hPa) and
CPU temperature entities shows up in Home Assistant automatically. Discovery is
re-sent when Home Assistant restarts. Entities go unavailable if the service
goes offline or no reading arrives for 3 intervals.

Plain MQTT only, no TLS: use it on a trusted network.

Extra metrics when MQTT is enabled: `sensor_mqtt_connected`,
`sensor_mqtt_publish_errors_total`.

To remove a device from Home Assistant again, clear its retained discovery
topics (`homeassistant/sensor/<device_id>/+/config`) by publishing an empty
retained message to each.

## DHT22 notes

DHT22 sensors are known for occasional checksum errors and timeouts. Each
reading is retried up to 3 times (2 s apart) before it counts as failed; the
next interval tries again. If errors are frequent, check the wiring (short
cables, a 4.7–10 kΩ pull-up on the data line) or consider an I2C sensor such
as the BME280.

## Configuration

See [`.env.example`](.env.example). Everything is an environment variable:
`SENSOR`, `RUN_INTERVAL_SECONDS` (60), `METRICS_PORT` (8000),
`EXPORT_CPU_TEMPERATURE` (true), `I2C_BUS` (1), `I2C_ADDRESS`, `GPIO_CHIP` (0),
`GPIO_PIN`, `LOG_LEVEL` (INFO), and the `MQTT_*` settings.

## Development

```sh
scripts/deploy-dev.sh <host>   # rsync to ~/sensor-pi-to-prom, build there, restart
```

The Pi needs a `.env` in that folder first; the script never touches it.
Images for `main` and `v*` tags are built by GitHub Actions on a native arm64
runner and pushed to `ghcr.io/ap-andersson/sensor-pi-to-prom`.

### Adding a sensor

Add `sensor_pi_to_prom/sensors/<name>.py` with a `Sensor` subclass
(`from_env`, `open`, `read`, `close`) and register it in
`sensor_pi_to_prom/sensors/__init__.py`.
