# Runbook

One section per alert. Every alert's `runbook` annotation links to its section here, and
`scripts/check.sh` fails if a link points to a section that doesn't exist.

Each entry covers what the alert means, what to check first, likely causes, the fix, and when
to escalate. Commands assume you're in the repository folder on the monitoring host.

The rule for every communications alert: **no data says nothing about the plant.** A silent
site may be producing normally. Before anyone drives out, find out whether the problem is the
plant or the path to it.

The rule for every grid alert: **the grid isn't ours to fix.** Frequency belongs to the whole
synchronous area and the TSO is on it before we are. What's ours is how the fleet responds, and
whether our own measurements can be trusted.

---

## CollectorDown

**Severity:** critical. **Fires when:** Prometheus hasn't been able to scrape the collector for
a minute.

**What it means:** nothing from the fleet is arriving. Device alerts go quiet along with it,
because their series stop existing instead of reading zero, so the lack of other alerts means
nothing right now.

**Check first**
1. `docker compose ps collector` and `docker compose logs --tail 50 collector`
2. Prometheus UI, **Status > Targets**: `connection refused` means Telegraf isn't listening, a
   timeout points at the DMZ/IT conduit
3. `./scripts/check-segmentation.sh`: does `dmz-it` still reach `collector:9273`?

**Likely causes:** Telegraf failed to start after a config change, ran out of memory, or the
conduit between the DMZ and IT changed.

**Fix:** `docker compose up -d collector`. If it won't start, its log names the bad config line.
Regenerate with `./scripts/generate-collector-config.sh` and run the unit tests before trying again.

**Escalate** to whoever owns the DMZ if the conduit itself changed.

---

## FleetSilent

**Severity:** critical. **Fires when:** the collector is up, but no device has answered for a
minute.

**What it means:** every device went quiet at once. Seven devices on four sites don't fail
together, but the path they share can. Look at the gateway and the OT/DMZ conduit before
looking at any site.

**Check first**
1. `docker compose logs --tail 20 modbus-gateway`: lines saying a unit `is not responding` mean
   the gateway is up but can't reach OT. A stopped container means the gateway itself.
2. `docker compose logs --tail 5 collector`: `connection refused` to `modbus-gateway:502` means
   the gateway is down; exception 11 means it answers but the devices behind it don't
3. `./scripts/check-segmentation.sh`

**Likely causes:** the gateway stopped or is restarting, or something changed on the OT network
or the conduit.

**Fix:** restore the gateway or the conduit.

**Escalate** to the OT network owner. Don't restart things on the OT side to see if it helps.

---

## SiteSilent

**Severity:** critical. **Fires when:** no device at one site has answered for a minute.

**What it means:** you're blind to that site. The values on the dashboard are history, not
status. The site may be producing, curtailed or tripped, and this alert can't tell you which.

**Check first**
1. **Is the site still exporting?** The substation measures what every site sends through it.
   If `fleet_active_power_watts{device="sub-01"}` hasn't dropped by about the site's last
   output, the plant is running and only its communications are down. That one query decides
   whether this is a comms ticket or a call-out.
2. `docker compose logs --tail 20 modbus-gateway`: which units stopped responding, and when?
   Unit ids go by site: 1x wind, 2x solar, 3x storage, 4x substation.
3. Are the other sites fine? Then the fault is between the gateway and this site: its link, its
   switch, or the site's own power.

**Likely causes:** the site's communications link or switch, a power cut at the site (then the
substation export will have dropped too), or maintenance nobody announced.

**Fix:** restore the link. In this lab, `docker compose ps` shows which devices are paused or
stopped.

**Escalate** at once if the substation export dropped at the same moment. Then it's probably a
real outage, and the export drop is the evidence to hand over.

---

## DeviceSilent

**Severity:** warning. **Fires when:** one device hasn't answered for a minute.

**What it means:** one device's data is missing while the rest of its site is visible. When a
whole site goes quiet, `SiteSilent` fires as well and is the one to work on.

**Check first**
1. `docker compose logs --tail 20 modbus-gateway`: is the unit listed as not responding?
2. From the OT side: `docker compose run --rm ot-workstation dump <device>`. If that works, the
   device is fine and the gateway's route is wrong: check its unit id and host in
   `inventory.toml`.
3. `docker compose logs --tail 20 <device>`

**Likely causes:** the device controller restarting, a hung comms card, or a wrong unit id
after an inventory change.

**Fix:** restart the device's comms, or correct the inventory and regenerate the collector config.

**Escalate** to the device's maintenance contractor if it doesn't answer from the OT side either.

---

## DeviceDataFrozen

**Severity:** warning. **Fires when:** a device still answers, but its heartbeat register hasn't
changed for 2 minutes.

**What it means:** everything it reports is stale. The values look plausible, and that's the
danger: anyone reading the dashboard will believe them.

**Check first**
1. Compare it with its neighbours. Same wind or irradiance as the rest of the park? Numbers that
   haven't moved in minutes give it away.
2. `docker compose run --rm ot-workstation dump <device>` twice, ten seconds apart. Does
   `heartbeat` move?
3. `docker compose logs --tail 20 <device>`

**Likely causes:** the controller's application has hung while its comms side keeps answering
from a buffer, or a protocol converter keeps serving cached values after losing the device.

**Fix:** restart the device controller (in the lab, delete its `frozen` scenario). Until the
heartbeat moves, treat its readings as unknown and don't act on them.

**Escalate** to the device's maintenance contractor if a restart doesn't bring it back.

---

## RegisterMapMismatch

**Severity:** warning. **Fires when:** for 5 minutes, a device reports a register map version the
collector config wasn't generated for.

**What it means:** a firmware update or a replaced unit may have moved registers. The collector
still decodes the old layout, so values can be off by a factor of ten, or come from a different
register entirely, without looking obviously wrong.

**Check first:** what changed on the device, and does the vendor's register documentation for
the new version differ from `fleet/register_map.toml`?

**Fix:** update `fleet/register_map.toml` from the vendor documentation, run
`./scripts/generate-collector-config.sh`, run the unit tests (the contract test checks every
register's address, type and scale), and redeploy the collector.

**Escalate** to whoever approved the firmware change. A register map change should have come
with the change request.


---

## GridFrequencyDeviation

**Severity:** warning. **Fires when:** the fleet's median frequency has been more than 100 mHz off
50 Hz for 5 minutes, or outside 49.95 to 50.05 Hz for 15 minutes. The `criterion` label says
which.

**What it means:** these are the two frequency conditions that put Continental Europe in the
alert state under SOGL. The median needs at least three meters agreeing, so this is the grid,
not a measurement: a sustained imbalance the TSOs' restoration reserves haven't corrected yet.
Short excursions are normal (when market schedules step, for example), and the time conditions
filter them out.

**Check first**
1. Is the battery responding? Its frequency response is proportional and full at 200 mHz, so at
   100 mHz `bess-01` should be about 500 kW off its schedule: discharging more when frequency is
   low, charging more when it's high. Check `fleet_active_power_watts{device="bess-01"}`. A
   battery sitting exactly on its schedule during a deviation isn't delivering the reserve it's
   paid for.
2. Its state of charge, `fleet_soc_ratio{device="bess-01"}`. A long low-frequency event drains
   it, a long high one fills it.
3. Output elsewhere. Above the LFSM-O threshold (set nationally, between 50.2 and 50.5 Hz),
   generating units have to reduce output. Less power across the fleet then is the units doing
   what the grid code asks, not a fault.

**Likely causes:** outside the fleet. A large generator or interconnector trip, a forecast error,
not enough restoration reserve.

**Fix:** none on our side. Leave the frequency response alone, it's doing its job.

**Escalate:** the TSO already knows. Tell whoever schedules the fleet, because the battery's output
and state of charge are drifting from the plan.

---

## GridFrequencyCritical

**Severity:** critical. **Fires when:** the median frequency has been more than 200 mHz off 50 Hz
for a minute.

**What it means:** past the maximum steady-state deviation for Continental Europe. Frequency
containment reserve across the synchronous area is fully activated, so there's nothing left in
it for the next disturbance, and frequency this far out puts the system in the emergency state
under SOGL. It's rare and it's big: a system split, or several gigawatts lost at once.

**Check first**
1. Is it real? Look at `fleet_frequency_hertz` per device: every site should show the same step
   at the same moment.
2. The battery at full response: `fleet_active_power_watts{device="bess-01"}` should be its whole
   1 MW reserve off schedule. Keep an eye on `fleet_soc_ratio{device="bess-01"}`.
3. What's still connected. Units disconnect at 47.5 or 51.5 Hz and then report state 5
   (`fleet_state == 5`). Inverters also say why: fault code 103 is grid frequency
   (`fleet_fault_code == 103`).

**Fix:** nothing to switch. Don't reconnect or restart units by hand while frequency is out of
range. Their protection reconnects them 60 seconds after it's back. Don't override the battery.

**Escalate:** to the duty manager, and follow any instruction from the TSO. Keep the data: after a
major event, how units behaved gets investigated, and these records are what you'll be asked for.

---

## FrequencyMeterMismatch

**Severity:** warning. **Fires when:** one device's frequency reading has been more than 20 mHz
from the fleet's median for 5 minutes.

**What it means:** frequency is the same everywhere in the synchronous area, so a device that
disagrees with the rest has a measurement problem. It matters most where something acts on the
reading. The battery's frequency response follows its own meter: one that reads 50 mHz low has
it discharging a quarter of its reserve into a grid that didn't ask for it. 20 mHz is twice the
10 mHz accuracy an FCR provider's frequency measurement has to meet.

**Check first**
1. `fleet_frequency_hertz` for this device next to the others. A steady offset points at the
   meter's calibration, a reading that jumps around at the voltage measurement it's derived from.
2. If it's the battery, compare its output with its schedule. Frequency response on a wrong
   reading shows as a steady offset while nothing is happening on the grid.
3. Was the device replaced, re-flashed or reconfigured recently?

**Likely causes:** meter calibration drift, a faulty voltage transformer or its wiring, a firmware
change in the controller's measurement.

**Fix:** recalibrate or replace the meter (in the lab, delete the device's scenario file). For the
battery, ask whether it should come out of frequency response until then: reserve delivered on a
wrong measurement is reserve delivered wrong.

**Escalate:** to the device's maintenance contractor. For the battery, also to whoever manages its
reserve contract.

---

## PhaseVoltageOutOfTolerance

**Severity:** warning. **Fires when:** a phase voltage has been more than 5% off nominal for 10
minutes.

**What it means:** unlike frequency, voltage is local. It depends on the site, its feeder and the
transformer taps between it and the grid. 5% off is still within what the equipment is built
for, but it's the early warning before units get near their protection settings.

**Check first**
1. One phase or all three? `device:phase_voltage:per_unit{device="<device>"}`. All three together
   points at the grid or a tap changer. One alone points at that phase: a connection, a fuse, or
   the voltage transformer measuring it.
2. One device or the whole site? Every device at the site seeing it points at the site's
   transformer or the grid. Compare the substation's 110 kV side,
   `device:phase_voltage:per_unit{device="sub-01"}`.
3. Reactive power, `fleet_reactive_power_var`. Units injecting reactive power raise the voltage,
   absorbing it lowers it. High voltage while the site injects means a setpoint working against
   the grid.

**Likely causes:** a tap changer stuck or on the wrong step, a wrong reactive power or voltage
setpoint, a loose connection or blown fuse on one phase, a faulty voltage transformer.

**Fix:** depends on the cause: the setpoint, the tap changer or the connection. If only the
measurement is wrong, the voltage is fine and the meter needs fixing.

**Escalate:** to the grid operator if the substation's 110 kV side is out of tolerance too. That
voltage is theirs.

---

## PhaseVoltageCritical

**Severity:** critical. **Fires when:** a phase voltage has been more than 10% off nominal for a
minute.

**What it means:** the unit is close to the protection settings that disconnect it (0.85 and 1.15
pu in this lab) or already past them. If it has tripped, its output is zero because of the grid,
not because anything is wrong with the unit, so it's not a job for the turbine technician.

**Check first**
1. Has it tripped? A tripped unit reports state 5 (`fleet_state == 5`), and an inverter's fault
   code says why: 101 overvoltage, 102 undervoltage.
2. As for `PhaseVoltageOutOfTolerance`: one phase or three, one device or the whole site.
3. All three phases off at the substation is a grid event. Check the frequency alerts and any
   notice from the grid operator.

**Fix:** find the cause before reconnecting anything. Once voltage is back in range, protection
reconnects the units by itself after 60 seconds.

**Escalate:** at once. To whoever is responsible for the site's electrical installation if it's
local, to the grid operator if it's on the 110 kV side.