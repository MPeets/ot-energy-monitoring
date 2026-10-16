# Runbook

One section per alert. Every alert's `runbook` annotation links to its section here, and
`scripts/check.sh` fails if a link points to a section that doesn't exist.

Each entry covers what the alert means, what to check first, likely causes, the fix, and when
to escalate. Commands assume you're in the repository folder on the monitoring host.

The rule for every communications alert: **no data says nothing about the plant.** A silent
site may be producing normally. Before anyone drives out, find out whether the problem is the
plant or the path to it.

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