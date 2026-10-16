#!/usr/bin/env bash
# Validates the configuration with the tools inside the pinned images, so
# there's nothing to install on the host. Grows as alerting is added.
#
#   ./scripts/check.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1  # stops Git Bash on Windows rewriting container paths
cd "$(dirname "$0")/.."

echo "== Prometheus =="
docker compose run --rm --no-deps -T --entrypoint promtool prometheus \
  check config /etc/prometheus/prometheus.yml

echo "All checks passed."