#!/usr/bin/env bash
# End-to-end check of the running lab, from a device to the receiver. Run it
# after any change:
#
#   docker compose up -d && ./scripts/smoke-test.sh
#
# Each check retries for up to TIMEOUT seconds (default 180), since targets,
# rules and notifications take a few scrape and evaluation cycles to settle.
# The last check trips an inverter and waits for its notification and then
# its all-clear, so a run takes about eight minutes. Needs bash, curl and
# docker compose, nothing to install.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # stops Git Bash on Windows rewriting container paths
cd "$(dirname "$0")/.."

PROM="${PROM:-http://localhost:9090}"
AM="${AM:-http://localhost:9093}"
GRAFANA="${GRAFANA:-http://localhost:3000}"
TIMEOUT="${TIMEOUT:-180}"

GRAFANA_USER="$(grep -E '^GRAFANA_ADMIN_USER=' .env 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_PW="${GRAFANA_ADMIN_PASSWORD:-$(grep -E '^GRAFANA_ADMIN_PASSWORD=' .env 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)}"

devices="$(grep -c '^\[\[device\]\]' inventory.toml)"

# Single number from an instant PromQL query, without needing jq
prom_value() {
  curl -sfG "$PROM/api/v1/query" --data-urlencode "query=$1" \
    | grep -o '"value":\[[^]]*\]' | head -1 | sed -E 's/.*,"([^"]*)"\]/\1/'
}
prom_equals() { [[ "$(prom_value "$1")" == "$2" ]]; }

rules_healthy() {
  local rules
  rules="$(curl -sf "$PROM/api/v1/rules")"
  grep -q '"health":"ok"' <<< "$rules" && ! grep -q '"health":"err"' <<< "$rules"
}

# Prometheus only sends alerts to an Alertmanager it has discovered and
# considers active. Without one, alerts fire in Prometheus and go nowhere.
alertmanager_active() {
  curl -sf "$PROM/api/v1/alertmanagers" | grep -q '"activeAlertmanagers":\[{'
}

watchdog_in_alertmanager() {
  curl -sfG "$AM/api/v2/alerts" --data-urlencode 'filter=alertname="Watchdog"' | grep -q Watchdog
}

# notified <regex> [since]: the webhook logger printed a matching line since
# then (an RFC 3339 time, by default when this run started)
notified() {
  docker compose logs --no-log-prefix --since "${2:-$started}" webhook-logger 2>/dev/null | tr -d '\r' | grep -Eq "$1"
}
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

dashboard_exists() {
  curl -sf -u "$GRAFANA_USER:$GRAFANA_PW" "$GRAFANA/api/dashboards/uid/$1" > /dev/null
}

# wait_for <description> <seconds> <command...>: retries until the command succeeds
wait_for() {
  local what="$1" limit="$2" start=$SECONDS
  shift 2
  until "$@" > /dev/null 2>&1; do
    if (( SECONDS - start >= limit )); then
      echo "FAIL  $what (gave up after ${limit}s)"
      return 1
    fi
    sleep 5
  done
  echo "ok    $what ($(( SECONDS - start ))s)"
}

started="$(now)"
failed=0
check() { wait_for "$1" "$TIMEOUT" "${@:2}" || failed=1; }

# The fleet has to be in its normal state: the last check injects a fault of
# its own, and "nothing else fires" means nothing.
if compgen -G "scenarios/*.json" > /dev/null || [[ -n "$(docker compose ps --services --status paused | tr -d '\r')" ]]; then
  echo "Scenarios are in effect. Run ./scripts/scenario.sh reset first, then try again."
  exit 1
fi

echo "== Services =="
check "Prometheus is ready"                        curl -sf "$PROM/-/ready"
check "Alertmanager is ready"                      curl -sf "$AM/-/ready"
check "Grafana is healthy"                         curl -sf "$GRAFANA/api/health"

echo "== Data path =="
check "Prometheus scrapes the collector"           prom_equals 'up{job="collector"}' 1
check "all $devices devices in the inventory answer" prom_equals 'count(device:reachable:bool == 1)' "$devices"
check "every rule evaluates without errors"        rules_healthy

echo "== Alerting =="
check "Prometheus has an Alertmanager to send to"  alertmanager_active
check "Watchdog reached Alertmanager"              watchdog_in_alertmanager
check "Watchdog delivered to the receiver"         notified '\[watchdog\] FIRING +Watchdog'
check "nothing else fires on a healthy fleet" \
  prom_equals 'count(ALERTS{alertstate="firing", alertname!="Watchdog"}) or vector(0)' 0

echo "== Dashboards =="
for file in grafana/dashboards/*.json; do
  uid="$(grep -m1 -o '"uid": *"[^"]*"' "$file" | cut -d'"' -f4)"
  check "dashboard $uid is provisioned"            dashboard_exists "$uid"
done

echo "== A fault, from the device to the receiver and back =="
# An insulation fault on inv-02: Modbus register, gateway, collector, rule,
# routing, receiver. Fires after 2 minutes, plus Alertmanager's group_wait.
trap 'rm -f scenarios/inv-02.json' EXIT
injected="$(now)"
printf '%s\n' '{"fault_code": 201}' > scenarios/inv-02.json
if wait_for "an insulation fault on inv-02 reaches the shift" 300 \
    notified '\[team\] FIRING +InverterFault .*device=inv-02' "$injected"; then
  if notified '\[oncall\]'; then
    echo "FAIL  someone was paged for it"
    failed=1
  else
    echo "ok    nobody was paged for it"
  fi
  # Waiting for the all-clear also leaves Alertmanager clean for the next run.
  # It goes out with the group's next update, up to 5 minutes (group_interval).
  cleared="$(now)"
  rm -f scenarios/inv-02.json
  wait_for "the all-clear follows once it's fixed" 420 \
    notified '\[team\] RESOLVED +InverterFault .*device=inv-02' "$cleared" || failed=1
else
  echo "      If a smoke test was interrupted in the last 10 minutes, Alertmanager may"
  echo "      still be holding its alert as already sent. Wait 5 minutes and try again."
  failed=1
fi

echo
if (( failed )); then
  echo "Smoke test FAILED"
  exit 1
fi
echo "Smoke test passed"