# ot-energy-monitoring

A small generation fleet simulated in containers (wind turbines, solar inverters, a battery
and a substation) serving Modbus registers the way real equipment does, monitored with
Prometheus and Grafana across separate OT, DMZ and IT networks. Everything is simulated and
generic.

## Quick start

    cp .env.example .env
    docker compose run --rm unit-tests
    ./scripts/check.sh                  # validate configuration with the pinned tools
    docker compose up -d
    ./scripts/check-segmentation.sh     # prove what each network can and can't reach
    docker compose run --rm ot-workstation dump wt-01

- Prometheus: http://localhost:9090 (the `collector` target should be UP)

## The simulated fleet

| Site | Devices | What they are |
|---|---|---|
| wind-a | wt-01, wt-02, wt-03 | 3.6 MW wind turbines sharing one wind field |
| solar-b | inv-01, inv-02 | 2.5 MW solar inverters, 3 MWp of modules each, one pyranometer |
| bess-c | bess-01 | 5 MW / 10 MWh battery: trading schedule plus frequency response |
| sub-d | sub-01 | 110/20 kV, 25 MVA substation at the point of connection; its RTU sums the feeders |

Each device serves its registers over Modbus TCP on port 502. The register map is in
[fleet/register_map.toml](fleet/register_map.toml), and [scenarios/](scenarios/README.md)
changes conditions in the simulated world.

## Networks

| Network | On it | Purpose |
|---|---|---|
| `ot` | the devices, `modbus-gateway` | Field devices. Internal, no route out |
| `ot-dmz` | `modbus-gateway`, `collector` | Conduit from OT to the DMZ. The gateway passes Modbus reads only |
| `dmz-it` | `collector`, `prometheus` | Conduit from the DMZ to IT. Carries the collector's metrics endpoint, nothing else |
| `it` | `prometheus` | The IT side, where the UIs live. The only network with ports published to the host |

Three services sit on two networks: the gateway, the collector and Prometheus. None of them
routes traffic between its networks; each only speaks its own protocol to the next zone.
`scripts/check-segmentation.sh` tests every path from inside each network.

## Alerts

| Alert | Severity | Means |
|---|---|---|
| CollectorDown | critical | Prometheus can't scrape the collector: no data from anywhere |
| FleetSilent | critical | Every device went quiet at once: suspect the gateway or the conduit |
| SiteSilent | critical | No device at a site answers: you're blind to it |
| DeviceSilent | warning | One device doesn't answer |
| DeviceDataFrozen | warning | A device answers, but its heartbeat stopped: every value it shows is stale |
| RegisterMapMismatch | warning | A device's register map isn't the one the collector decodes |
| Watchdog | none | Always firing, proves alerting works |

"No data" and "no output" are kept apart on purpose: none of the alerts above look at power, so
a calm day or a curtailed park can't trip them, and a silent site never looks like a site making
0 MW. Every alert has unit tests (`prometheus/tests/`), including cases that must not fire, and
a section in [docs/runbook.md](docs/runbook.md).

## Layout

    collector/      Telegraf config; telegraf.d/devices.conf is generated from the inventory
    docs/           runbook
    prometheus/     Prometheus config, alert rules and their unit tests
    fleet/          device simulator, read-only Modbus gateway, register map (standard library only)
    scenarios/      files that inject conditions into the simulated world (faults, grid events)
    scripts/        helper scripts
    tests/          unit tests
    inventory.toml  the asset list: every device, its site and the unit id it's reached by
    
## Status

Work in progress