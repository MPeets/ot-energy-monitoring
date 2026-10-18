#!/bin/sh
# Tests for alertmanager.yml, run inside the pinned Alertmanager image by
# scripts/check.sh. Routing is checked with amtool. Inhibition can't be, so
# each scenario starts a throwaway Alertmanager on localhost, sends it alerts
# and asks which ones it muted.
set -eu

CONFIG="${CONFIG:-/etc/alertmanager/alertmanager.yml}"
URL=http://127.0.0.1:9094
failed=0

amtool check-config "$CONFIG"

echo "-- routing"
# route <expected receiver> <labels...>
route() {
  want="$1"
  shift
  if amtool config routes test --config.file="$CONFIG" --verify.receivers="$want" "$@" >/dev/null; then
    echo "ok    $* -> $want"
  else
    echo "FAIL  $* should route to $want"
    failed=1
  fi
}

route watchdog alertname=Watchdog severity=none
route oncall   alertname=SiteSilent severity=critical site=wind-a
route oncall   alertname=GridFrequencyCritical severity=critical
route oncall   alertname=BatteryHeatingWhileCharging severity=critical device=bess-01
route team     alertname=DeviceSilent severity=warning device=wt-01
route team     alertname=TransformerCoolingDegraded severity=warning device=sub-01
route team     alertname=TurbineUnderperforming severity=warning device=wt-02
route log      alertname=InverterGridTrip severity=info device=inv-01
route default  alertname=SomethingUnlabelled

echo "-- inhibition"
pid=""
unsettled=0

# scenario <description>: a fresh Alertmanager with no alerts in it
scenario() {
  stop
  echo "$1"
  rm -rf /tmp/am-test
  alertmanager --config.file="$CONFIG" --storage.path=/tmp/am-test --cluster.listen-address= \
    --web.listen-address=127.0.0.1:9094 --log.level=error &
  pid=$!
  until amtool --alertmanager.url="$URL" config show >/dev/null 2>&1; do sleep 1; done
}

stop() {
  if [ -n "$pid" ]; then
    kill "$pid"
    wait "$pid" 2>/dev/null || true
    pid=""
  fi
}

# fire <labels...>: send an alert, as Prometheus would
fire() {
  amtool --alertmanager.url="$URL" alert add "$@" >/dev/null
  unsettled=1
}

# expect <active|muted> <matchers...>
expect() {
  want="$1"
  shift
  if [ "$unsettled" -eq 1 ]; then
    sleep 1  # the inhibitor picks up new alerts asynchronously
    unsettled=0
  fi
  if [ "$(amtool --alertmanager.url="$URL" alert query --inhibited -o json "$@")" != "[]" ]; then
    got=muted
  elif [ "$(amtool --alertmanager.url="$URL" alert query -o json "$@")" != "[]" ]; then
    got=active
  else
    got=missing
  fi
  if [ "$got" = "$want" ]; then
    echo "ok      $want: $*"
  else
    echo "FAIL    $*: $got, expected $want"
    failed=1
  fi
}

scenario "A silent site mutes its own devices, not another site's"
fire alertname=SiteSilent severity=critical site=wind-a
fire alertname=DeviceSilent severity=warning site=wind-a device=wt-01
fire alertname=DeviceSilent severity=warning site=solar-b device=inv-01
expect active alertname=SiteSilent site=wind-a
expect muted  alertname=DeviceSilent device=wt-01
expect active alertname=DeviceSilent device=inv-01

scenario "A silent device mutes the alerts built on its data"
fire alertname=DeviceSilent severity=warning site=solar-b device=inv-02
fire alertname=InverterUnderperforming severity=warning site=solar-b device=inv-02
fire alertname=InverterUnderperforming severity=warning site=solar-b device=inv-01
expect muted  alertname=InverterUnderperforming device=inv-02
expect active alertname=InverterUnderperforming device=inv-01

scenario "Collector down: only the root cause gets through, and the watchdog"
fire alertname=CollectorDown severity=critical
fire alertname=SiteSilent severity=critical site=wind-a
fire alertname=GridFrequencyCritical severity=critical
fire alertname=Watchdog severity=none
expect active alertname=CollectorDown
expect muted  alertname=SiteSilent
expect muted  alertname=GridFrequencyCritical
expect active alertname=Watchdog

scenario "Fleet silent mutes everything that depends on fleet data"
fire alertname=FleetSilent severity=critical
fire alertname=TransformerOilHot severity=warning site=sub-d device=sub-01
expect active alertname=FleetSilent
expect muted  alertname=TransformerOilHot

scenario "Critical voltage mutes its own warning, on that phase, and the trip it caused"
fire alertname=PhaseVoltageCritical severity=critical site=wind-a device=wt-01 phase=l1
fire alertname=PhaseVoltageOutOfTolerance severity=warning site=wind-a device=wt-01 phase=l1
fire alertname=PhaseVoltageOutOfTolerance severity=warning site=wind-a device=wt-01 phase=l2
fire alertname=TurbineFault severity=warning site=wind-a device=wt-01
fire alertname=TurbineFault severity=warning site=wind-a device=wt-02
expect muted  alertname=PhaseVoltageOutOfTolerance phase=l1
expect active alertname=PhaseVoltageOutOfTolerance phase=l2
expect muted  alertname=TurbineFault device=wt-01
expect active alertname=TurbineFault device=wt-02

scenario "Frequency: the 5 minute criterion mutes the 15 minute one, critical mutes both"
fire alertname=GridFrequencyDeviation severity=warning criterion=100mHz-5m
fire alertname=GridFrequencyDeviation severity=warning criterion=50mHz-15m
expect active alertname=GridFrequencyDeviation criterion=100mHz-5m
expect muted  alertname=GridFrequencyDeviation criterion=50mHz-15m
fire alertname=GridFrequencyCritical severity=critical
expect muted  alertname=GridFrequencyDeviation criterion=100mHz-5m

scenario "Heat: critical mutes its warning, but not the cause or the trip"
fire alertname=TransformerOilCritical severity=critical site=sub-d device=sub-01
fire alertname=TransformerOilHot severity=warning site=sub-d device=sub-01
fire alertname=TransformerCoolingDegraded severity=warning site=sub-d device=sub-01
fire alertname=BatteryHeatingWhileCharging severity=critical site=bess-c device=bess-01
fire alertname=BatteryCellTemperatureHigh severity=warning site=bess-c device=bess-01
fire alertname=BatteryFault severity=warning site=bess-c device=bess-01
expect muted  alertname=TransformerOilHot
expect active alertname=TransformerCoolingDegraded
expect muted  alertname=BatteryCellTemperatureHigh
expect active alertname=BatteryFault

stop
[ "$failed" -eq 0 ]