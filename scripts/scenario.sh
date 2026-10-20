#!/usr/bin/env bash
# Plays out an incident on the running lab: sets up the conditions in the
# simulated world, then says what should happen, which alerts, who hears about
# them and roughly when.
#
#   ./scripts/scenario.sh               list the scenarios
#   ./scripts/scenario.sh grid-trip     start one (they add up, two at once is fine)
#   ./scripts/scenario.sh status        what's in effect right now
#   ./scripts/scenario.sh reset         everything back to normal
#
# Watch the notifications arrive with
#   docker compose logs -f webhook-logger | grep -v watchdog
set -euo pipefail
export MSYS_NO_PATHCONV=1  # stops Git Bash on Windows rewriting container paths
cd "$(dirname "$0")/.."

PROM="${PROM:-http://localhost:9090}"

# Name, then what it shows. Listed in this order.
SCENARIOS=(
  "grid-event           grid frequency 150 mHz low: the battery's reserve responds, the shift is told"
  "grid-emergency       grid frequency 300 mHz low: all reserve in use, on-call is paged"
  "bad-meter            the battery's own meter reads 50 mHz high, the grid is fine"
  "voltage-sag          one phase at wt-02 sits 7% low"
  "grid-trip            one phase at wt-01 drops under its trip setting: one page, not two"
  "site-silent          the wind park stops answering: one page, not three"
  "frozen-controller    inv-01 keeps answering, with values that stopped changing"
  "iced-blades          wt-03 makes 60% of what its wind should give"
  "insulation-fault     inv-02 trips on a DC insulation fault"
  "curtailment          the wind park held at 30% in a strong wind: no alert, on purpose"
  "transformer-cooling  the transformer's fans stop at moderate load, long before any temperature alarm"
  "battery-overheat     the battery's HVAC fails while it charges at full power"
)

# world <file> <json>: a condition in the simulated world, see scenarios/README.md
world() {
  printf '%s\n' "$2" > "scenarios/$1.json"
  printf '  scenarios/%-13s %s\n' "$1.json" "$2"
}

# setpoints <device> <register> <value> [...]: written over Modbus from the OT
# workstation, the way a park controller does it. One container for all of them.
setpoints() {
  docker compose run --rm -T --entrypoint sh ot-workstation -c '
    while [ $# -ge 3 ]; do python -B -m fleet.cli write "$1" "$2" "$3" || exit 1; shift 3; done
  ' sh "$@"
}

# silence <service>...: the devices stop answering, as if their link went down
silence() {
  docker compose pause "$@"
}

indent() {
  sed 's/^/  /'
}

expect() {
  echo "Expect, timed from now ($(date +%H:%M:%S)), give or take half a minute:"
  indent
}

scenario_grid_event() {
  world grid '{"frequency_offset_hz": -0.15}'
  expect <<'EOF'
~30 s       battery dashboard: "asked by the grid" and "delivered" both step up to about 750 kW
~6 min      [team] FIRING GridFrequencyDeviation criterion=100mHz-5m
            the 15-minute criterion fires underneath and stays muted: the 5-minute one already says it
after reset RESOLVED within 5 minutes, for both criteria in one notification
EOF
}

scenario_grid_emergency() {
  world grid '{"frequency_offset_hz": -0.3}'
  expect <<'EOF'
~30 s       the battery delivers its whole 1 MW reserve
~2 min      [oncall] FIRING GridFrequencyCritical
            GridFrequencyDeviation never arrives: the critical alert mutes it
EOF
}

scenario_bad_meter() {
  world bess-01 '{"frequency_offset_hz": 0.05}'
  expect <<'EOF'
~30 s       battery dashboard: "delivered" drops to about -250 kW while "asked by the grid" stays near 0,
            and its meter leaves the fleet median
~6 min      [team] FIRING FrequencyMeterMismatch device=bess-01
            no grid alert: the fleet median ignores one meter
EOF
}

scenario_voltage_sag() {
  world wt-02 '{"voltage_pu": {"l2": 0.93}}'
  expect <<'EOF'
~11 min     [team] FIRING PhaseVoltageOutOfTolerance device=wt-02 phase=l2
            wt-02 keeps producing: 0.93 per-unit is well above its 0.85 trip setting
EOF
}

scenario_grid_trip() {
  world wt-01 '{"voltage_pu": {"l1": 0.8}}'
  expect <<'EOF'
~30 s       wt-01 trips and turns red on the dashboards
~2 min      [oncall] FIRING PhaseVoltageCritical device=wt-01 phase=l1
            TurbineFault never arrives: the voltage alert says why wt-01 stopped
after reset wt-01 reconnects 60 s later, and the voltage alert covers the gap
EOF
}

scenario_site_silent() {
  silence wt-01 wt-02 wt-03
  expect <<'EOF'
~1 min      the three turbines turn purple, "no data"
~2.5 min    [oncall] FIRING SiteSilent site=wind-a
            no DeviceSilent for the three turbines: the site alert already says it
EOF
}

scenario_frozen_controller() {
  world inv-01 '{"frozen": true}'
  expect <<'EOF'
~3.5 min    [team] FIRING DeviceDataFrozen device=inv-01
            every value from inv-01 still looks plausible, nothing else fires
EOF
}

scenario_iced_blades() {
  world wt-03 '{"wind_speed": 10, "performance": 0.6}'
  expect <<'EOF'
~30 s       power curve: wt-03's dots sink to 60% of the grey curve
~16 min     [team] FIRING TurbineUnderperforming device=wt-03
            its 10-minute average has to drop under 80% and stay there 10 minutes,
            and between 22:00 and 07:00 Tallinn time it waits for the morning
after reset the all-clear takes up to 10 minutes: the average has to recover first
EOF
}

scenario_insulation_fault() {
  world inv-02 '{"fault_code": 201}'
  expect <<'EOF'
~30 s       inv-02 turns red and drops to 0 kW
~2.5 min    [team] FIRING InverterFault device=inv-02 fault_code=201: inv-02 tripped: DC insulation fault
EOF
}

scenario_curtailment() {
  world wt-01 '{"wind_speed": 12}'
  world wt-02 '{"wind_speed": 12}'
  world wt-03 '{"wind_speed": 12}'
  setpoints wt-01 power_limit 30 wt-02 power_limit 30 wt-03 power_limit 30
  expect <<'EOF'
~30 s       the turbines turn blue, "curtailed", flat at about 1.08 MW each under a 3.6 MW curve,
            and a blue band opens between "possible" and "expected"
nothing     no notification at all: a power limit lowers what's expected, it isn't a loss
EOF
}

scenario_transformer_cooling() {
  world sub-01 '{"cooling_failure": true, "load_kw": 12000}'
  expect <<'EOF'
right away  the oil starts climbing towards twice the rise its load explains
~24 min     [team] FIRING TransformerCoolingDegraded device=sub-01
            the oil is around 55 C, 30 C short of the 85 C warning: no temperature alarm would see it
EOF
}

scenario_battery_overheat() {
  world bess-01 '{"power_kw": -5000, "cooling_failure": true, "soc": 50}'
  expect <<'EOF'
right away  cells climb almost 3 C a minute ("soc" holds the charge at 50%, so it keeps charging)
~8 min      [oncall] FIRING BatteryHeatingWhileCharging device=bess-01
~12 min     the BMS trips at 55 C: the battery turns red and stops
~13 min     [oncall] RESOLVED BatteryHeatingWhileCharging: it isn't charging any more
~15 min     [team] FIRING BatteryFault, and BatteryCellTemperatureHigh, no longer muted by the critical
EOF
}

status() {
  echo "Scenario files:"
  local found=0 file
  for file in scenarios/*.json; do
    [[ -e "$file" ]] || continue
    printf '  %-23s %s\n' "$file" "$(tr -d '\r\n' < "$file")"
    found=1
  done
  (( found )) || echo "  none"

  echo "Paused services:"
  local paused
  paused="$(docker compose ps --services --status paused | tr -d '\r')"
  printf '%s\n' "${paused:-none}" | indent

  echo "Power limits under 100%:"
  local limits
  if limits="$(curl -sfG "$PROM/api/v1/query" --data-urlencode 'query=fleet_power_limit_ratio < 1')"; then
    limits="$(grep -o '"device":"[^"]*"' <<< "$limits" | cut -d'"' -f4 || true)"
    printf '%s\n' "${limits:-none}" | indent
  else
    echo "  unknown, Prometheus isn't reachable at $PROM"
  fi
}

reset() {
  rm -f scenarios/*.json
  echo "Scenario files removed"

  local paused
  paused="$(docker compose ps --services --status paused | tr -d '\r')"
  if [[ -n "$paused" ]]; then
    # shellcheck disable=SC2086  # one argument per service
    docker compose unpause $paused
  fi

  # Every unit back to no power limit
  local args=() id
  while read -r id; do
    args+=("$id" power_limit 100)
  done < <(awk -F'"' '/^id =/ { id = $2 } /^type = "(turbine|inverter)"/ { print id }' inventory.toml)
  setpoints "${args[@]}"
  echo "Back to normal. Alerts resolve over the next few minutes, and their RESOLVED"
  echo "notifications follow within 5 (Alertmanager's group_interval). Wait for those before"
  echo "running the same scenario again: until then Alertmanager counts it as already sent."
}

usage() {
  echo "Usage: $0 <scenario> | status | reset"
  echo
  printf '  %s\n' "${SCENARIOS[@]}"
}

case "${1:-}" in
  "" | -h | --help) usage ;;
  status) status ;;
  reset) reset ;;
  *)
    if ! printf '%s\n' "${SCENARIOS[@]}" | grep -q "^$1 "; then
      echo "Unknown scenario: $1" >&2
      echo >&2
      usage >&2
      exit 1
    fi
    printf '%s\n' "${SCENARIOS[@]}" | grep "^$1 " | sed -E 's/^([^ ]+) +/\1: /'
    "scenario_${1//-/_}"
    ;;
esac