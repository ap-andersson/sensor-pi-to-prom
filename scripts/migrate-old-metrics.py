#!/usr/bin/env python3
"""Copy history from the old rpi-*-to-prom metric names into the new names.

Reads the old series from VictoriaMetrics (/api/v1/export), renames them, adds
the `sensor` label, converts units, and writes them back (/api/v1/import).
Only samples older than the first sample of the matching new series are
copied, so nothing overlaps with data the new exporter already wrote.
Staleness markers (NaN) are dropped. The old series are left untouched.

Dry run by default: prints what it would do and writes the payload to a file.

  scripts/migrate-old-metrics.py --vm http://vm:8428 \\
      --target voicebox-temp-etc=bme280 --target hotbox-temp=htu21 [--apply]

`--target JOB=SENSOR` limits the migration to that scrape job (other exporters
may use the same generic names like `humidity`) and sets the sensor label.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
import urllib.request

# old name -> (new name, factor, gets sensor label)
RENAMES = {
    "temperature": ("sensor_temperature_celsius", 1.0, True),
    "humidity": ("sensor_relative_humidity_percent", 1.0, True),
    "pressure": ("sensor_pressure_pascals", 100.0, True),  # hPa -> Pa
    "cpu_temp": ("rpi_cpu_temperature_celsius", 1.0, False),
}

# ~2-3 MB of JSON per request.
CHUNK_SAMPLES = 100_000


def export(vm: str, selector: str, start: str = "0") -> list[dict]:
    query = urllib.parse.urlencode({"match[]": selector, "start": start})
    with urllib.request.urlopen(f"{vm}/api/v1/export?{query}", timeout=300) as r:
        return [json.loads(line) for line in r if line.strip()]


def selector(name: str, labels: dict[str, str]) -> str:
    inner = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{inner}}}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vm", required=True, help="VictoriaMetrics base URL")
    ap.add_argument("--target", action="append", required=True, metavar="JOB=SENSOR")
    ap.add_argument("--out", default="migration.jsonl", help="payload file (default: %(default)s)")
    ap.add_argument("--apply", action="store_true", help="actually import into VictoriaMetrics")
    ap.add_argument("--import-file", metavar="PATH",
                    help="skip the export step and import an existing (reviewed) payload file")
    args = ap.parse_args()
    vm = args.vm.rstrip("/")

    if args.import_file:
        import_file(vm, args.import_file)
        return 0

    targets = dict(t.split("=", 1) for t in args.target)
    payload: list[str] = []
    total = 0

    for job, sensor in targets.items():
        for old_name, (new_name, factor, sensor_label) in RENAMES.items():
            for series in export(vm, selector(old_name, {"job": job})):
                labels = dict(series["metric"])
                labels.pop("__name__")
                new_labels = dict(labels, **({"sensor": sensor} if sensor_label else {}))

                # First sample the new exporter wrote. Samples sharing a timestamp
                # with the old series come from an earlier run of this script and
                # are ignored, so re-running is safe.
                old_ts = {t for t, v in zip(series["timestamps"], series["values"]) if v is not None}
                existing = export(vm, selector(new_name, new_labels))
                cutoff = min(
                    (t for s in existing for t in s["timestamps"] if t not in old_ts),
                    default=math.inf,
                )

                ts_out, v_out = [], []
                for ts, v in zip(series["timestamps"], series["values"]):
                    if ts < cutoff and v is not None and not math.isnan(v):
                        ts_out.append(ts)
                        v_out.append(v * factor)
                if not ts_out:
                    continue

                print(
                    f"{selector(old_name, labels)}\n"
                    f"  -> {selector(new_name, new_labels)}: {len(ts_out)} samples"
                    f" (of {len(series['values'])}), x{factor:g},"
                    f" cutoff {'none' if cutoff == math.inf else cutoff}"
                )
                payload.append(json.dumps({
                    "metric": {"__name__": new_name, **new_labels},
                    "values": v_out,
                    "timestamps": ts_out,
                }))
                total += len(ts_out)

    with open(args.out, "w") as f:
        f.write("\n".join(payload) + "\n")
    print(f"\n{len(payload)} series, {total} samples written to {args.out}")

    if not args.apply:
        print("Dry run, nothing imported. Re-run with --apply to import.")
        return 0

    import_file(vm, args.out)
    return 0


def import_file(vm: str, path: str) -> None:
    """POST a JSON-lines payload in chunks that stay well below VictoriaMetrics'
    -maxInsertRequestSize (default 32 MB). Re-importing samples that already
    exist is harmless: identical timestamps are deduplicated."""
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            series = json.loads(line)
            ts, vals = series["timestamps"], series["values"]
            for i in range(0, len(ts), CHUNK_SAMPLES):
                chunk = {"metric": series["metric"], "values": vals[i:i + CHUNK_SAMPLES],
                         "timestamps": ts[i:i + CHUNK_SAMPLES]}
                req = urllib.request.Request(
                    f"{vm}/api/v1/import", data=(json.dumps(chunk) + "\n").encode(), method="POST"
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    if r.status not in (200, 204):
                        raise RuntimeError(f"import failed: HTTP {r.status}")
            print(f"Imported {len(ts)} samples into {series['metric']}")
    # Cached query results don't know about backfilled data.
    urllib.request.urlopen(f"{vm}/internal/resetRollupResultCache", timeout=30).read()
    print("Rollup result cache reset.")


if __name__ == "__main__":
    sys.exit(main())
