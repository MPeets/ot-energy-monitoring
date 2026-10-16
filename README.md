# ot-energy-monitoring

A small generation fleet simulated in containers (wind turbines, solar inverters, a battery
and a substation) serving Modbus registers the way real equipment does, monitored with
Prometheus and Grafana across separate OT, DMZ and IT networks. Everything is simulated and
generic.

## Quick start

    cp .env.example .env
    docker compose run --rm unit-tests
    docker compose up -d
    docker compose run --rm ot-workstation dump wt-01

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
| `ot-dmz` | `modbus-gateway` | Conduit from OT to the DMZ. The gateway passes Modbus reads only |

## Layout

    fleet/          device simulator, read-only Modbus gateway, register map (standard library only)
    scenarios/      files that inject conditions into the simulated world (faults, grid events)
    tests/          unit tests
    inventory.toml  the asset list: every device, its site and the unit id it's reached by
    
## Status

Work in progress