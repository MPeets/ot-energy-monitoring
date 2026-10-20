# Scenarios

For whole incidents, with what should happen and when, use the script:

    ./scripts/scenario.sh              # list them
    ./scripts/scenario.sh grid-trip    # start one
    ./scripts/scenario.sh reset        # everything back to normal

This page is the raw interface the script builds on. Files here change the simulated physical
world. Every device re-reads them once a second, and deleting a file puts things back to normal.
The folder is mounted read-only into the devices, and the `*.json` files are gitignored.

| File | Applies to |
|---|---|
| `grid.json` | every device, since they all sit on the same grid |
| `<device>.json` | one device, e.g. `wt-01.json` |

## grid.json

| Key | Type | Effect |
|---|---|---|
| `frequency_offset_hz` | number | Shifts grid frequency at every site, e.g. `-0.3` after a large generator trips |

## Any device

| Key | Type | Effect |
|---|---|---|
| `frequency_offset_hz` | number | This device's meter reads off by this much (the grid itself is fine) |
| `voltage_pu` | object | Forces phase voltages in per-unit, e.g. `{"l2": 0.85}` |
| `frozen` | true/false | Keeps answering on Modbus, but every register stops changing (a hung controller) |

## Turbines (wt-*)

| Key | Type | Effect |
|---|---|---|
| `wind_speed` | number | Forces the wind at this turbine, in m/s |
| `performance` | number | 0 to 1. Produces this share of what the wind should give, e.g. `0.6` for iced blades. No fault code |
| `fault` | true/false | Trips the turbine |
| `maintenance` | true/false | Stops it for planned work |

## Solar inverters (inv-*)

| Key | Type | Effect |
|---|---|---|
| `irradiance` | number | Forces the pyranometer reading in W/m2, handy when testing after dark |
| `performance` | number | 0 to 1, e.g. `0.7` for blown string fuses or heavy soiling. No fault code |
| `fault_code` | number | Trips with this code, e.g. `201` (DC insulation fault) |
| `maintenance` | true/false | Stops it for planned work |

## Battery (bess-01)

| Key | Type | Effect |
|---|---|---|
| `power_kw` | number | Overrides the trading schedule. Negative charges, e.g. `-5000`. The battery reports it as its `power_setpoint` |
| `soc` | number | Holds the state of charge at this %, e.g. `50`. Charging stops at 95%, so this keeps a test of charging from depending on how full the battery happens to be |
| `cooling_failure` | true/false | The container's HVAC stops, so cells heat up with current |
| `fault` | true/false | Trips the battery |
| `maintenance` | true/false | Stops it for planned work |

Frequency response always runs on top. The battery management system trips at 55 C and
allows a restart once the cells are below 40 C.

## Substation (sub-01)

| Key | Type | Effect |
|---|---|---|
| `cooling_failure` | true/false | Transformer fans and oil pumps stop: same load, about twice the oil temperature rise |
| `ambient_temperature` | number | Forces the outdoor temperature, in C |
| `load_kw` | number | Forces the transformer load, as if the fleet exported this much |

## Grid protection

Turbines, inverters and the battery disconnect when their terminals see voltage below 0.85 or above
1.15 per-unit, or frequency outside 47.5 to 51.5 Hz, and reconnect 60 seconds after the grid
is back in range. So `{"frequency_offset_hz": -2.6}` in `grid.json` trips the whole fleet,
and `{"voltage_pu": {"l1": 0.8}}` trips one unit.

## Setpoints

Curtailment isn't a scenario. It's a setpoint, written over Modbus the way a park controller does it:

    docker compose run --rm ot-workstation write wt-02 power_limit 0

## Examples

    echo '{"frequency_offset_hz": -0.3}' > scenarios/grid.json    # grid event, 49.7 Hz everywhere
    echo '{"voltage_pu": {"l2": 0.85}}' > scenarios/wt-01.json    # voltage sag on one phase
    echo '{"wind_speed": 2}' > scenarios/wt-03.json               # calm: 0 kW, and that's fine
    echo '{"fault_code": 201}' > scenarios/inv-02.json            # insulation fault, 0 kW, and that's not
    echo '{"power_kw": -5000, "cooling_failure": true}' > scenarios/bess-01.json      # cells heating while charging
    echo '{"cooling_failure": true, "load_kw": 25000}' > scenarios/sub-01.json        # transformer running hot
    echo '{"frozen": true}' > scenarios/inv-02.json               # answers with stale values
    rm -f scenarios/*.json                                        # back to normal

Typos and invalid values are reported in the device log (`docker compose logs <device>`)
instead of being silently ignored.