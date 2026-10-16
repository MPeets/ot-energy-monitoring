#!/usr/bin/env bash
# Regenerates collector/telegraf.d/devices.conf from inventory.toml and the
# register map. Run it after changing either; the unit tests fail until you do.
#
#   ./scripts/generate-collector-config.sh
#
# Runs the generator in the same Python image as everything else, so nothing
# needs installing on the host. Output goes through the host shell, so the
# file ends up owned by you.
set -euo pipefail
cd "$(dirname "$0")/.."

out=collector/telegraf.d/devices.conf
docker compose run --rm --no-deps -T --entrypoint python unit-tests -B -m fleet.collector_config > "$out.tmp"
mv "$out.tmp" "$out"
echo "Updated $out"