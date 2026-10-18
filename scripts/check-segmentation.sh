#!/usr/bin/env bash
# Checks the network segmentation from the inside: from each network, what can
# actually be reached? Needs the stack running:
#
#   docker compose up -d && ./scripts/check-segmentation.sh
#
# Each probe is a throwaway container on one network, trying TCP connections.
# Paths that must be blocked are tried by name and by every IP address the
# target has, since a name that doesn't resolve proves nothing on its own.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # stops Git Bash on Windows rewriting arguments that look like paths
cd "$(dirname "$0")/.."

project="$(sed -n 's/^name: *//p' compose.yaml)"
# Docker on Windows can end lines with CR, hence the tr -d '\r' on everything captured
image="$(docker inspect -f '{{.Config.Image}}' "$(docker compose ps -q wt-01 | tr -d '\r')" | tr -d '\r')"
failed=0

# targets <service> <port>: the service name and each of its IPs, as host:port
targets() {
  local id
  id="$(docker compose ps -q "$1" | tr -d '\r')"
  echo "$1:$2"
  for ip in $(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$id" | tr -d '\r'); do
    echo "$ip:$2"
  done
}

# probe <network> <host:port>...: one line per target, "open" or "closed"
probe() {
  local network="$1"
  shift
  docker run --rm --network "${project}_${network}" --read-only --cap-drop ALL --user 65534:65534 "$image" \
    python -c '
import socket, sys
for target in sys.argv[1:]:
    host, port = target.rsplit(":", 1)
    try:
        socket.create_connection((host, int(port)), timeout=1.5).close()
        print(target, "open")
    except OSError:
        print(target, "closed")
' "$@"
}

# expect <network> <open|closed> <host:port>...
expect() {
  local network="$1" want="$2" status
  shift 2
  while read -r target got; do
    if [[ "$got" == "$want" ]]; then status="ok"; else status="FAIL"; failed=1; fi
    printf '%-5s from %-7s to %-24s %-7s (expected %s)\n' "$status" "$network" "$target" "$got" "$want"
  done < <(probe "$network" "$@" | tr -d '\r')
}

echo "== Reachability =="
# OT: devices talk to each other (the substation RTU polls the feeders), nothing leaves
expect ot     open   wt-01:502
expect ot     closed 1.1.1.1:53
# OT/DMZ conduit: the gateway, never a device directly
expect ot-dmz open   modbus-gateway:502
# shellcheck disable=SC2046  # one argument per target is the point
expect ot-dmz closed $(targets wt-01 502) 1.1.1.1:53
# DMZ/IT conduit: the collector's metrics endpoint, nothing towards OT, and no
# way to raise or silence an alert
expect dmz-it open   collector:9273
# shellcheck disable=SC2046
expect dmz-it closed $(targets modbus-gateway 502) $(targets wt-01 502) $(targets alertmanager 9093) 1.1.1.1:53
# IT: Prometheus and Alertmanager, but no path to the collector, the gateway or a device
expect it     open   prometheus:9090 alertmanager:9093
# shellcheck disable=SC2046
expect it     closed $(targets collector 9273) $(targets modbus-gateway 502) $(targets wt-01 502)

echo "== Conduit policy =="
# The gateway passes reads only. A write from the DMZ must come back refused.
out="$(docker compose run --rm -T dmz-workstation --unit 12 write modbus-gateway power_limit 0 2>&1 || true)"
if grep -q "Illegal Function" <<< "$out"; then
  echo "ok    a setpoint write from the DMZ is refused by the gateway"
else
  echo "FAIL  a setpoint write from the DMZ was not refused:"
  echo "$out"
  failed=1
fi

echo
if (( failed )); then
  echo "Segmentation check FAILED"
  exit 1
fi
echo "Segmentation check passed"