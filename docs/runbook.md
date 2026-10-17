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

The rule for every generation alert: **low output means nothing on its own.** Calm, darkness,
curtailment and clipping all look like low output. A unit only counts as underperforming against
what its weather and its power limit allow right now, and a stopped unit is a fault only when the
cause is inside it.

The rule for every thermal alert: **act on the trend, not the trip.** Protection trips at its
limit whether anyone is watching or not. The alerts are there so someone can take load off first,
and the earliest sign is usually heat the load doesn't explain.

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
   100 mHz `bess-01` should be about 500 kW off its setpoint: discharging more when frequency is
   low, charging more when it's high. The gap is
   `fleet_active_power_watts{device="bess-01"} - fleet_power_setpoint_watts{device="bess-01"}`.
   A battery sitting exactly on its setpoint during a deviation isn't delivering the reserve it's
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
2. The battery at full response: the gap between `fleet_active_power_watts{device="bess-01"}`
   and `fleet_power_setpoint_watts{device="bess-01"}` should be its whole 1 MW reserve. Keep an
   eye on `fleet_soc_ratio{device="bess-01"}`.
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
2. If it's the battery, look at the gap between its output and its setpoint
   (`fleet_active_power_watts - fleet_power_setpoint_watts`). Frequency response on a wrong
   reading shows as a steady gap while nothing is happening on the grid.
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

---

## TurbineUnderperforming

**Severity:** warning. **Fires when:** over 10 minutes, a turbine made less than 80% of what the
power curve says its measured wind allows (capped at its power limit), and that has held for
another 10 minutes. A sudden loss takes about 15 minutes to show: 5 to drag the 10 minute
average under 80%, 10 to confirm it. Turbines are judged only while producing or curtailed for
the whole window, and only when they should be making at least a tenth of their rating, so calm,
storm stops, faults, maintenance and the noisy minutes around cut-in never count.

**What it means:** energy is being lost. That's money, not safety, so it's a ticket for the
morning, not a call-out. Except in icing weather: blades carrying ice throw it. Most turbines
stop themselves when they detect ice, so one that keeps running iced is both underperforming and
dangerous to stand under.

**Check first**
1. Its neighbours, in the same wind. `device:performance:ratio_10m{site="wind-a"}` and
   `fleet_wind_speed_meters_per_second{site="wind-a"}`. One turbine low while the others are fine
   is the turbine. All of them low together points at something shared (the weather, the curve,
   the site's power limit), not three turbines failing at once.
2. Its wind reading against theirs. A turbine reporting much more wind than its neighbours while
   making the same power has an anemometer reading high, and the turbine itself is fine.
3. The weather. Below zero and humid means ice.

**Likely causes:** ice or dirt on the blades, pitch or yaw misalignment, a derating the
controller applies without reporting it (temperature, for example), an anemometer reading high.

**Fix:** depends on the cause. Ice usually goes on its own once it's above zero. In the lab,
delete the turbine's `performance` scenario.

**Escalate:** to the turbine's service contractor. Power curve warranties tend to depend on
reporting losses like this promptly, with the data.

---

## InverterUnderperforming

**Severity:** warning. **Fires when:** over 10 minutes, an inverter made less than 80% of what its
irradiance allows, and that has held for another 10 minutes. Expected power is irradiance/1000 ×
module capacity × 0.85, capped at the inverter's power limit (a share of its rating), so clipping
and curtailment don't count. Never judged at night or in the low light of dawn and dusk, when it
should be making less than a tenth of its rating.

**What it means:** part of the array isn't delivering. A step down that stays (to 70%, say)
usually means strings lost. A slow decline over weeks is soiling, which this alert won't catch:
that's for a daily report, not a 10 minute window.

**Check first**
1. The other inverter at the site. They share a pyranometer, so both low together, in the same
   light, points at the pyranometer or the weather (snow), not at two inverters failing at once.
   One alone is that inverter's part of the array.
2. In winter: snow or frost on the modules while the pyranometer has cleared.
3. Its power limit, `fleet_power_limit_ratio{device="<device>"}`. Already in the expected figure,
   but worth knowing if someone changed it.

**Likely causes:** blown string fuses or a failed combiner box, snow or soiling on part of the
array, new shading, a pyranometer out of calibration or knocked out of alignment.

**Fix:** depends on the cause. In the lab, delete the inverter's `performance` scenario.

**Escalate:** to the solar park's maintenance contractor.

---

## TurbineFault

**Severity:** warning. **Fires when:** a turbine has reported state 5 (fault) for 2 minutes.

**What it means:** it's stopped and not producing. Its grid protection reports the same state, so
rule out the grid before treating it as a turbine fault. Real turbine controllers report a status
code that says why they stopped. This lab's turbines don't, so the cause comes from what else is
happening at the same time.

**Check first**
1. Voltage and frequency alerts. `PhaseVoltageCritical` for this turbine, or
   `GridFrequencyCritical`, means the grid tripped it, and it reconnects by itself 60 seconds after
   the grid is back.
2. Other turbines at the site faulted at the same moment? Several at once points at something they
   share: the grid, the site's cable or its substation.
3. The turbine's own fault log (in the lab, `docker compose logs <device>`).

**Likely causes:** anything a turbine protects itself from: pitch, converter, gearbox or generator
temperature, vibration, the grid.

**Fix:** many faults clear with a remote reset once the cause is understood. Don't keep resetting
the same fault without finding out why it trips: that's how small problems become gearbox
replacements. In the lab, delete the turbine's `fault` scenario.

**Escalate:** to the turbine's service contractor if it doesn't reset, or trips again.

---

## InverterFault

**Severity:** warning. **Fires when:** an inverter has reported a fault code of 200 or higher for 2
minutes. The `fault_code` label carries the code.

| Code | Fault | What it usually is |
|---|---|---|
| 201 | DC insulation fault | Insulation between the DC side and earth is too low: a damaged cable, water in a junction box, a cracked module |
| 301 | overtemperature | Clogged filters, a failed fan, or a heatwave |

**What it means:** the inverter stopped on a fault of its own, and its share of the park is
producing nothing. An insulation fault is an earth fault in the array, a shock and fire risk, so
the inverter stays off until someone has found it. Overtemperature often clears once it cools
down, and comes back the next hot afternoon.

**Check first**
1. The code, from the alert or `fleet_fault_code{device="<device>"}`.
2. For 201: did it rain or was there heavy dew? Insulation faults that appear in the morning and
   clear by noon point at moisture getting in somewhere.
3. For 301: is the other inverter running hot too? Then it's the weather or the station's
   ventilation, not this inverter.

**Fix:** 201: find the fault before restarting, and never bypass the insulation monitoring. 301:
clean the filters, check the fans. In the lab, delete the inverter's `fault_code` scenario.

**Escalate:** 201 to the site's electrician or maintenance contractor, as a safety fault. 301 to
the maintenance contractor if it keeps coming back.

---

## InverterGridTrip

**Severity:** info. **Fires when:** an inverter has reported a grid protection trip (codes 101 to
103) for a minute.

| Code | Trip |
|---|---|
| 101 | grid overvoltage |
| 102 | grid undervoltage |
| 103 | grid frequency out of range |

**What it means:** its grid protection opened, which is the protection doing its job. The output
is zero for a reason outside the plant. It reconnects by itself 60 seconds after the grid is back
in range.

**Check first**
1. The voltage and frequency alerts: they say what happened to the grid.
2. Did the other inverter trip too? Both on the same feeder at once is the feeder or the grid.
3. How often it happens. A unit that trips on voltage every sunny afternoon is sitting close to
   its limits: the connection point's voltage, a tap setting or the reactive power setpoint
   needs a look.

**Fix:** none needed for a one-off. Don't reconnect it by hand while the grid is still out of
range.

**Escalate:** if it keeps happening, to the grid operator, with the times of each trip.

---

## TransformerOilHot

**Severity:** warning. **Fires when:** the transformer's top-oil temperature has been above 85 °C
for 5 minutes.

**What it means:** the transformer is running hot. Insulation ages faster the hotter it runs (the
usual rule of thumb is twice as fast for every 6 °C or so at the hottest spot), so hours up here
cost transformer life even if nothing trips.

**Check first**
1. Load or cooling? Compare `device:top_oil_rise_kelvin{device="sub-01"}` with
   `device:top_oil_rise_kelvin:expected{device="sub-01"}`. Close together means it's the load and
   the weather. The actual rise well above the expected one means the cooling, and
   `TransformerCoolingDegraded` will follow.
2. The load, `fleet_transformer_load_ratio{device="sub-01"}`. Above 1.0 it's carrying more than
   its rating.
3. The weather, `fleet_ambient_temperature_celsius{device="sub-01"}`. A heatwave takes margin off
   everything.

**Likely causes:** high export on a hot day, an overload, failing cooling.

**Fix:** take load off by curtailing the parks behind it, and fix the cooling if that's the cause.
Curtailment goes through the park controller on the OT side. This monitoring stack can't write a
setpoint, by design. In the lab, from the OT side:
`docker compose run --rm ot-workstation write wt-01 power_limit 50`.

**Escalate:** to the substation's maintenance contractor if it's the cooling, and to whoever
schedules the fleet if export has to be held down.

---

## TransformerOilCritical

**Severity:** critical. **Fires when:** top oil has been above 95 °C for a minute.

**What it means:** it's close to where the transformer's own temperature protection trips it. If
it trips, every site exporting through it drops off at once: the whole fleet's output gone in one
step, and an imbalance nobody planned for.

**Check first:** the same as `TransformerOilHot`, but don't spend long on it. Taking load off comes
first.

**Fix:** curtail the parks behind it now, through the park controller (in the lab,
`write <device> power_limit <percent>` from the OT workstation). Bringing output back up costs a
little energy. A tripped transformer costs all of it, and maybe the transformer.

**Escalate:** at once, to the duty manager and the substation's owner.

---

## TransformerCoolingDegraded

**Severity:** warning. **Fires when:** for 15 minutes, the top oil has sat more than 10 K further
above the outdoor air than the thermal model says its highest load of the last 30 minutes
explains.

**What it means:** heat is going in but not coming out. Fans, oil pumps or radiators aren't doing
their job. It's the earliest warning there is: on a winter night the oil can sit at 50 °C with the
cooling dead, nowhere near an alarm setting, and it'd only show in the absolute temperature on the
first warm day with a strong wind.

**Check first**
1. `device:top_oil_rise_kelvin{device="sub-01"}` against
   `device:top_oil_rise_kelvin:expected{device="sub-01"}`: how far apart, and since when? A step
   means something stopped, like a fan supply or a pump. A slow drift over weeks means radiators
   clogging or the oil ageing.
2. The ambient reading itself. An outdoor sensor that has failed low, or sits in the sun or next
   to a radiator, fakes this alert.
3. The cooling's own alarms: fan and pump supplies, in the substation's SCADA.

**Likely causes:** a tripped fan or pump supply, failed fan motors, radiators blocked with dirt or
leaves, an ambient sensor reading wrong.

**Fix:** restore the cooling. Until it's back, the transformer's real rating is lower, so plan the
export with that in mind. In the lab, delete `scenarios/sub-01.json`.

**Escalate:** to the substation's maintenance contractor.

---

## BatteryCellTemperatureHigh

**Severity:** warning. **Fires when:** the hottest cell has been above 45 °C for a minute.

**What it means:** the cells are past the top of the range lithium cells are commonly specified to
charge in (one LFP datasheet: 0 to 45 °C charging, -20 to 60 °C discharging), and 10 °C short of
the battery management system's 55 °C trip. Heat ages cells too, so even a battery that never
trips pays for running hot.

**Check first**
1. Is it charging? Then `BatteryHeatingWhileCharging` is probably firing as well, and it's the
   one to work on.
2. The HVAC. In reality, the container's own HVAC alarms. In the lab, `cooling_failure` in its
   scenario.
3. Its power, `fleet_active_power_watts{device="bess-01"}`. Heat comes from current, so full
   power in a heatwave runs hot even with working cooling.

**Fix:** reduce the power and fix the cooling.

**Escalate:** to the battery's maintenance contractor if the HVAC has failed.

---

## BatteryHeatingWhileCharging

**Severity:** critical. **Fires when:** for 2 minutes, the cells have been above 35 °C, climbing
faster than 0.5 °C a minute, while the battery is charging.

**What it means:** with working cooling, cells warm up when current starts flowing and then level
off. These aren't levelling off, and charging keeps pushing energy and heat into cells that are
already warm. Heat is where thermal runaway starts. In the lab, a cooling failure at full charging
power gets from this alert to the BMS trip in about five minutes. A real container is slower, but
the order is the same.

**Check first:** nothing that delays the fix. Stop the charging, then look.

**Fix**
1. Stop charging: put the battery in standby, or allow discharge only. In a real plant that's the
   EMS or SCADA. In the lab,
   `echo '{"power_kw": 0, "cooling_failure": true}' > scenarios/bess-01.json`. Frequency response
   carries on, at a fraction of the current.
2. Then the HVAC: its supply, its alarms, a container door left open.
3. If the cells keep heating with no current flowing, treat it as a fire risk: follow the site's
   emergency procedure and keep people away from the container.

**Escalate:** at once, to the battery's maintenance contractor and the duty manager.

---

## BatteryFault

**Severity:** warning. **Fires when:** the battery has reported state 5 (fault) for 2 minutes.

**What it means:** it's doing nothing at all: no charging, no discharging, no frequency response.
The reserve it was sold for isn't being delivered.

**Check first**
1. Cell temperature, `fleet_cell_temperature_celsius{device="bess-01"}`. Around 55 °C, or coming
   down from there, means the BMS tripped on temperature. It allows a restart once the cells are
   below 40 °C.
2. Voltage and frequency alerts. Grid protection reports the same state, and reconnects by itself
   60 seconds after the grid is back.
3. Neither? Then it's a fault of its own: the battery's event log (in the lab,
   `docker compose logs bess-01`).

**Fix:** after a BMS trip, fix the cooling first, or it trips again on the next charge. In the lab,
delete the battery's scenario file.

**Escalate:** to the battery's maintenance contractor, and tell whoever manages the reserve
contract, so the FCR can be declared unavailable.