# sensor-pi-to-prom

Reads one temperature/humidity sensor on a Raspberry Pi and exposes it as a
Prometheus endpoint. One image for all supported sensors; pick the sensor with
`SENSOR=`.

Replaces `rpi-bme280-to-prom`, `rpi-htu21-to-prom` and `rpi-dht22-to-prom`.

| `SENSOR` | Bus | Measures | Tested on |
|---|---|---|---|
| `bme280` | I2C, default address `0x76` | temperature, humidity, pressure | Pi 5 |
| `htu21`  | I2C, default address `0x40` | temperature, humidity | Pi 5 |
| `dht22`  | one GPIO (`GPIO_PIN`, BCM number) | temperature, humidity | not yet |

All hardware access goes through the kernel character devices (`/dev/i2c-N`,
`/dev/gpiochipN`) via `smbus2` and `lgpio`, so the same code works on the Pi 5
and older models. The container gets only that one device node, not
`privileged: true`.

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
| `rpi_cpu_temperature_celsius` | disable with `EXPORT_CPU_TEMPERATURE=false` (node-exporter already has `node_thermal_zone_temp`) |

If a read fails, the measurement series are removed until the next good read,
so graphs show a gap instead of a stale value.

Old to new names: `temperature` → `sensor_temperature_celsius`, `humidity` →
`sensor_relative_humidity_percent`, `pressure` (hPa) → `sensor_pressure_pascals`
(Pa, ×100), `cpu_temp` → `rpi_cpu_temperature_celsius`.

### Migrating history from the old exporters

`scripts/migrate-old-metrics.py` copies the old series in VictoriaMetrics into
the new names (adds the `sensor` label, converts hPa to Pa, only copies samples
older than the new series' first sample, leaves the old series alone). Dry run
by default:

```sh
scripts/migrate-old-metrics.py --vm http://<victoriametrics>:8428 \
    --target <scrape-job>=bme280 --target <other-job>=htu21 [--apply]
```

Note that the old exporters rounded values (humidity to whole percent,
temperature to 0.1 °C, pressure to whole hPa), so migrated history is coarser
than new data.

## Configuration

See [`.env.example`](.env.example). Everything is an environment variable:
`SENSOR`, `RUN_INTERVAL_SECONDS` (60), `METRICS_PORT` (8000),
`EXPORT_CPU_TEMPERATURE` (true), `I2C_BUS` (1), `I2C_ADDRESS`, `GPIO_CHIP` (0),
`GPIO_PIN`, `LOG_LEVEL` (INFO).

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
