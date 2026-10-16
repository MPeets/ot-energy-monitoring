# Scenarios

Files here change the simulated physical world. Every device re-reads them once a second, and
deleting a file puts things back to normal. The folder is mounted read-only into the devices,
and the `*.json` files are gitignored.

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

## Turbines (wt-*)

| Key | Type | Effect |
|---|---|---|
| `wind_speed` | number | Forces the wind at this turbine, in m/s |
| `performance` | number | 0 to 1. Produces this share of what the wind should give, e.g. `0.6` for iced blades. No fault code |
| `fault` | true/false | Trips the turbine |
| `maintenance` | true/false | Stops it for planned work |

Curtailment isn't a scenario. It's a setpoint, written over Modbus the way a park controller does it:

    docker compose run --rm ot-workstation write wt-02 power_limit 0

## Examples

    echo '{"frequency_offset_hz": -0.3}' > scenarios/grid.json    # grid event, 49.7 Hz everywhere
    echo '{"voltage_pu": {"l2": 0.85}}' > scenarios/wt-01.json    # voltage sag on one phase
    rm -f scenarios/*.json                                        # back to normal
    echo '{"wind_speed": 2}' > scenarios/wt-03.json               # calm: 0 kW, and that's fine

Typos and invalid values are reported in the device log (`docker compose logs <device>`)
instead of being silently ignored.