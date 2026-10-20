![CI](https://github.com/MPeets/ot-energy-monitoring/actions/workflows/ci.yml/badge.svg)

# ot-energy-monitoring

A small generation fleet simulated in containers (wind turbines, solar inverters, a battery
and a substation) serving Modbus registers the way real equipment does, monitored with
Prometheus and Grafana across separate OT, DMZ and IT networks. Everything is simulated and
generic.

## What this demonstrates

- **Segmentation, tested:** separate OT, DMZ and IT networks, joined by conduits that each carry
  one protocol. The only way into OT is a gateway that passes Modbus reads and refuses writes,
  proven from inside every network
- **Alerts that know the physics:** output judged against what each unit's own wind or sunlight
  allows, grid frequency against the Continental Europe SOGL criteria from the median of the
  fleet's meters, transformer oil against the IEC 60076-7 thermal model
- **No data is never no output:** silence has alerts and a colour of its own, and mutes everything
  built on the missing data
- **One cause, one page:** routing by severity and inhibition, tested with amtool and a real
  Alertmanager
- **Tested at every layer:** device models, contract tests between layers, alert rule tests
  including the cases that must not fire, an end-to-end smoke test, and twelve rehearsed incidents
- **Offline delivery:** one bundle with every image, verified by content, installed in CI with the
  registries blocked

## Quick start

    cp .env.example .env                # then set GRAFANA_ADMIN_PASSWORD
    docker compose run --rm unit-tests
    ./scripts/check.sh                  # validate configuration with the pinned tools
    docker compose up -d
    ./scripts/smoke-test.sh             # end to end, from a device fault to the receiver and back
    ./scripts/check-segmentation.sh     # prove what each network can and can't reach
    docker compose run --rm ot-workstation dump wt-01

- Grafana: http://localhost:3000 (admin, and the password from `.env`), opens on the fleet overview
- Prometheus: http://localhost:9090 (the `collector` target should be UP)
- Alertmanager: http://localhost:9093
- Notifications: `docker compose logs -f webhook-logger`

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
| `it` | `prometheus`, `alertmanager`, `grafana`, `webhook-logger` | The IT side, where the UIs and alerting live. The only network with ports published to the host |

Three services sit on two networks: the gateway, the collector and Prometheus. None of them
routes traffic between its networks; each only speaks its own protocol to the next zone.
`scripts/check-segmentation.sh` tests every path from inside each network.

## Dashboards

The **fleet overview** is the screen for the control room: export at the connection point, grid
frequency, silent devices, alerts, the battery, then every unit's state, generation, the grid and
heat underneath.

- **Silence is a state of its own.** A unit that stops answering turns purple, "no data", instead
  of leaving a gap or freezing on its last state. It never shows as 0 MW or as a fault.
- **Colours follow cause.** Green runs, blue is held back by a setpoint, grey is the weather, red is
  a fault, yellow is planned work. Curtailment is never red.
- **Possible, expected, actual.** One panel stacks what the weather allows, what the setpoints
  allow and what the fleet makes. Blue between the first two is curtailment, red between the last
  two is lost energy.

Click a unit on the overview's state timeline to drill down to its site:

- **Wind park:** the power curve, every turbine's output against its own anemometer on top of the
  curve the alerts expect. On the curve is healthy, flat under it is a power limit, a cloud under
  it is a loss. Plus each turbine's wind, 10-minute performance and power limit.
- **Solar park:** the same for inverters, against the park's pyranometer, flat at the rating
  where they clip.
- **Battery:** the schedule against the output, and the frequency response the grid asks for
  against what the battery delivers. A bad meter shows as the two coming apart.

`tests/test_dashboards.py` fails if a panel queries a metric nobody collects or records, if a
state in the register map has no colour, if a drawn curve isn't the one the alerts use, or if a
link points at a dashboard that doesn't exist.

## Alerts

Every alert has unit tests (`prometheus/tests/`), including cases that must not fire, and a
section in [docs/runbook.md](docs/runbook.md).

Alertmanager routes them by severity: critical pages on-call, warning goes to the shift, info
goes to the log, and underperformance is held overnight. Inhibition keeps one cause from arriving
as many alerts:

- **No data mutes what depends on it.** CollectorDown and FleetSilent mute the whole fleet,
  SiteSilent mutes its site, DeviceSilent mutes its device, resolved notifications included, so
  going blind never looks like an all-clear.
- **Critical mutes its own warning:** frequency, voltage per phase, transformer oil, battery cells.
- **The grid mutes the trips it caused.** A unit that faults while its voltage is critical was
  tripped by the grid, and the voltage alert stays on through its 60 second reconnect.

`alertmanager/test.sh` checks the routing with amtool, and the inhibition against a real
Alertmanager, from `./scripts/check.sh`.

### Communications

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
0 MW.

### Grid

| Alert | Severity | Means |
|---|---|---|
| GridFrequencyDeviation | warning | More than 100 mHz off 50 Hz for 5 minutes, or outside ±50 mHz for 15 |
| GridFrequencyCritical | critical | More than 200 mHz off for a minute: all frequency containment reserve is in use |
| FrequencyMeterMismatch | warning | One device's frequency disagrees with the rest: a bad meter, not the grid |
| PhaseVoltageOutOfTolerance | warning | A phase more than 5% off nominal for 10 minutes |
| PhaseVoltageCritical | critical | A phase more than 10% off for a minute, close to the protection settings |

The Baltic grids have run synchronously with Continental Europe since February 2025, so the
frequency thresholds are Continental Europe's parameters from the EU System Operation Guideline
(SOGL): the two warning conditions are its alert state, beyond 200 mHz its emergency state.
Frequency is one value across the synchronous area, so the grid alerts use the median of every
meter in the fleet and need at least three. One bad meter can't page anyone, it gets an alert of
its own instead. Voltages are compared in per-unit of each device's nominal, so the same
thresholds work at 690 V and at 110 kV.

### Generation

| Alert | Severity | Means |
|---|---|---|
| TurbineUnderperforming | warning | Under 80% of what the power curve says its wind allows, over 10 minutes |
| InverterUnderperforming | warning | Under 80% of what its irradiance allows, over 10 minutes |
| TurbineFault | warning | A turbine has tripped |
| InverterFault | warning | An inverter stopped on a fault of its own, named from its fault code |
| InverterGridTrip | info | An inverter's grid protection opened: zero output, and not its fault |

Output is never compared with a fixed number. Expected power is what the wind or sunlight allows,
capped at the unit's power limit, so curtailment and clipping lower the expectation instead of
counting as losses. Units are judged only while running for a full 10 minutes and expected to
make at least a tenth of their rating, so a calm night, a storm stop or dusk is never
underperformance. The tests cover each of those cases, and a contract test keeps the rules on the
same power curve and performance ratio as the simulated devices.

### Thermal

| Alert | Severity | Means |
|---|---|---|
| TransformerOilHot | warning | Top oil above 85 °C for 5 minutes |
| TransformerOilCritical | critical | Top oil above 95 °C: close to the transformer's own trip, and the whole export path with it |
| TransformerCoolingDegraded | warning | Oil more than 10 K hotter than its load explains: the cooling has failed, whatever the temperature |
| BatteryCellTemperatureHigh | warning | Cells above 45 °C |
| BatteryHeatingWhileCharging | critical | Cells above 35 °C and still climbing while charging: stop charging before the BMS trips |
| BatteryFault | warning | The battery has tripped, and its frequency reserve with it |

Protection trips at its limit either way, so the thermal alerts look for the cause before the
number gets there. The transformer's expected oil rise comes from the IEC 60076-7 thermal model at
its highest recent load, so failed fans show up on a winter night with the oil at 50 °C, long
before any alarm setting would. The battery alert watches the trend: cells that keep climbing
while charging, where working cooling would have levelled them off.

## Scenarios

`scripts/scenario.sh` plays out incidents on the running lab and says what should happen next:
which alerts, who hears about them, and roughly when.

    ./scripts/scenario.sh               # list them
    ./scripts/scenario.sh grid-trip     # start one
    ./scripts/scenario.sh status        # what's in effect right now
    ./scripts/scenario.sh reset         # everything back to normal
    docker compose logs -f webhook-logger | grep -v watchdog

Some are there for what doesn't happen: a turbine tripped by the grid pages once, for the
voltage, not again for the turbine. A curtailed park sends nothing at all. A failed transformer
fan is caught with the oil still far from any temperature alarm. `tests/test_scenarios.py` keeps
the script honest: every key it writes is one the device accepts, every setpoint is writable, and
every alert and receiver it promises exists.

## Smoke test

`scripts/smoke-test.sh` checks the running lab end to end, in about eight minutes:

- every device in the inventory answers, and every rule evaluates
- Prometheus has an Alertmanager to send to. Without one, alerts fire in Prometheus and go nowhere
- the Watchdog reaches its receiver, and nothing else fires on a healthy fleet
- the dashboards are provisioned
- an insulation fault on an inverter reaches the shift, pages nobody, and its all-clear follows

## Offline install

On a machine with internet access, from a clean commit:

    ./scripts/bundle.sh

Carry `dist/ot-energy-monitoring-<commit>.tar` and its `.sha256` file across, then on the offline
host:

    sha256sum -c ot-energy-monitoring-<commit>.tar.sha256
    tar -xf ot-energy-monitoring-<commit>.tar
    cd ot-energy-monitoring-<commit>
    ./install.sh

The installer checks every file against its checksum and refuses a bundle built for another
platform. It loads the images and checks each one against the manifest, validates the
configuration, and starts the lab without pulling anything. A newer bundle installs over an older
one the same way, and the data volumes carry over.

Images are checked by their platform and filesystem layers, not by image ID. Docker's classic and
containerd image stores give the same image different IDs, so a bundle built on Docker Desktop
would otherwise fail its check on a Linux server that uses the classic store. `tests/test_bundle.py`
keeps every image pinned to the version in `.env.example`, which is where the bundle takes its
versions from.

## CI

GitHub Actions runs three jobs:

| Job | What it runs | When |
|---|---|---|
| Unit tests, configuration and scripts | shellcheck, a check that every script is executable, the unit tests in the pinned Python image with no network, and `./scripts/check.sh` | every push and pull request |
| The running lab, end to end | the whole lab, then `./scripts/smoke-test.sh`, `./scripts/check-segmentation.sh` and the scenario script against a real Docker | every push and pull request |
| Offline bundle and air-gapped install | `./scripts/bundle.sh`, then every image deleted and the registries blocked, then `install.sh`, and the smoke test and segmentation check again on the installed copy | main and manual runs |

Actions are pinned to commit SHAs, the token is read-only, and Dependabot proposes updates to the
pins. `tests/test_ci.py` fails if an action isn't pinned, or if CI stops running one of the lab's
scripts.

## Documentation

- [Architecture](docs/architecture.md): how it fits together, from a register to a notification,
  the design decisions and how it's tested
- [Runbook](docs/runbook.md): what to check first for every alert
- [From the lab to real plants](docs/real-plants.md): where real data comes from, why monitoring
  reads through the station gateway and never from protection relays, IEC 60870-5-104, and
  which way connections go
- [Scenarios](scenarios/README.md): the files that change the simulated world

## Layout

    .github/        CI workflow and Dependabot
    alertmanager/   routing and inhibition, and their tests
    collector/      Telegraf config; telegraf.d/devices.conf is generated from the inventory
    docs/           architecture, runbook, and what changes at a real plant
    grafana/        provisioning and dashboards
    prometheus/     Prometheus config, alert rules and their unit tests
    fleet/          device simulator, read-only Modbus gateway, register map (standard library only)
    scenarios/      files that inject conditions into the simulated world (faults, grid events)
    scripts/        checks, smoke test, scenarios, collector config, offline bundle and installer
    tests/          unit tests
    tools/          webhook-logger, the lab's stand-in for a pager
    inventory.toml  the asset list: every device, its site and the unit id it's reached by

## Status

Complete as a lab. 