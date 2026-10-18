#!/usr/bin/env bash
# Validates the configuration with the tools inside the pinned images, so
# there's nothing to install on the host.
#
#   ./scripts/check.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1  # stops Git Bash on Windows rewriting container paths
cd "$(dirname "$0")/.."

echo "== Prometheus =="
docker compose run --rm --no-deps -T --entrypoint sh prometheus -c '
  set -e
  promtool check config /etc/prometheus/prometheus.yml
  promtool test rules /etc/prometheus/tests/*.yml
'

echo "== Alertmanager =="
# Config, routing, and inhibition against a throwaway Alertmanager inside the container
docker compose run --rm --no-deps -T --entrypoint sh alertmanager /etc/alertmanager/test.sh

echo "== Runbook links =="
# Every runbook link in an alert must point to a heading that exists in docs/runbook.md.
headings="$(grep -E '^## ' docs/runbook.md | sed 's/^## //' | tr -d '\r ' | tr '[:upper:]' '[:lower:]')"
missing=0
while read -r anchor; do
  if grep -qx "$anchor" <<< "$headings"; then
    echo "ok: #$anchor"
  else
    echo "FAIL: docs/runbook.md has no section for #$anchor"
    missing=1
  fi
done < <(grep -ohE 'docs/runbook\.md#[a-z0-9-]+' prometheus/rules/*.yml | cut -d'#' -f2 | sort -u)
(( missing == 0 ))

echo "All checks passed."