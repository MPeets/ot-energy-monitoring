# From the lab to real plants

In this lab every device speaks Modbus TCP, and the collector reads each one through a read-only
gateway. That keeps the lab small and every step visible. Real generation sites are built
differently, and these notes are about what would change, and why some things would never be
done the way the lab does them. Everything here is general practice, not a description of any
particular site.

## Where real data comes from

| In the lab | At a real site | Common protocols |
|---|---|---|
| Each turbine answers Modbus | Turbines report to the manufacturer's park SCADA, often with the park controller alongside | OPC UA, IEC 61400-25, or the vendor's own interface |
| Each inverter answers Modbus | Inverters report to a datalogger or the plant controller | Modbus, often with SunSpec register models |
| The battery answers Modbus | The battery management system reports to the site's energy management system | Modbus or OPC UA |
| The substation RTU sums the feeders | Protection and control IEDs report to a station gateway, which usually also talks to the grid operator | IEC 61850 inside the station, IEC 60870-5-104 out of it |

The pattern is the same everywhere: devices already report to a system that's responsible for
them, and that system is where monitoring should read from. A device only has to answer the one
client it was commissioned for.

## Read through the gateway, never from the relay

The lab reads every device itself, each one through the gateway. At a substation that would be
wrong for protection relays, whatever the protocol:

- **A relay's first job is protection.** Its communication capacity is sized for the clients it
  was commissioned with: the station HMI, the gateway, the engineering tool. An IEC 61850 IED
  offers a small, fixed number of client associations and report control blocks. A monitoring
  client can take one that the station needs.
- **Its configuration is under protection change control.** Adding a client means a change to a
  safety function's environment, with testing and a maintenance window, for data the gateway
  already has.
- **Every extra connection is attack surface** on the devices that trip breakers.

So monitoring reads from the station gateway, the substation HMI or the historian, which already
collect everything the relays report. The GOOSE and Sampled Values traffic between IEDs stays on
the station and process buses. Monitoring doesn't join those networks.

## IEC 60870-5-104

The usual way to take data out of a substation, and what a gateway would offer monitoring:

- **Event-driven, not polled.** The gateway sends values spontaneously when they change, and
  answers a general interrogation with everything at once after a connection starts. The load on
  the gateway is far lower than polling.
- **Values carry quality.** Each one can be flagged invalid, not topical (not updated when it
  should have been), substituted, blocked or overflowed. Those map straight onto this lab's
  design: invalid or not topical is "no data", like a silent or frozen device here, and is never
  shown as a valid reading.
- **Values carry their own time.** Measurements and events can carry the source's timestamp.
  Prometheus records when it scraped, which is fine for trends and alerts. The sequence of events
  after a trip belongs in the SCADA's event log, with the device's own timestamps.
- **A monitoring connection of its own.** It's configured on the gateway separately from the
  control centre's connections, and where the gateway allows it, it accepts no commands. Where it
  doesn't, a protocol-aware firewall in front drops command messages. That's the same job this
  lab's gateway does for Modbus, refusing everything but reads.

## Which way connections go

In the lab, each zone reads from the zone below: the collector connects to the gateway, and
Prometheus connects to the collector. Many sites don't allow any connection into OT from outside
at all. Then the collector runs inside OT and pushes outward, to a receiver in the DMZ, and from
there to IT. Where policy requires a data diode, the data goes across it one way, using the diode's
own transfer, and lands on the IT side, for example as Prometheus remote write. The alert rules,
dashboards and tests don't change, only the transport underneath.

The lab's networks follow the zones and conduits of IEC 62443: `ot` and `it` are zones,
`ot-dmz` and `dmz-it` are the conduits, and the collector between them is the DMZ. Each conduit
carries one protocol in one direction. At a site, the zones would come from its own risk
assessment, not from this lab.

## Polling, inventory and change control

- **Rates are agreed, not chosen.** The lab reads every 10 seconds. At a site, how often anything
  reads a device or a gateway is agreed with whoever owns the equipment, and older devices can need
  much slower rates.
- **The inventory comes from the asset register.** Here it's a hand-written file. In practice it's
  generated from the asset register and reviewed in git, so a device added to the site is added to
  monitoring the same way, and nothing ever scans a network to find devices.
- **Planned work is silenced in advance.** A maintenance window becomes an Alertmanager silence
  before the work starts, so the people on call can trust that an alert means something.

## What carries over unchanged

The parts that don't depend on how the data arrives:

- the alert logic: frequency against the SOGL criteria from the median of the fleet's meters,
  voltage per phase in per-unit, output against what the weather and the setpoints allow, oil
  temperature against what the load explains
- keeping "no data" apart from "no output", in alerts and on dashboards
- inhibition, so one cause arrives as one notification
- the offline install, with every file and image verified
- the tests, from the contract tests between layers to the smoke test that follows a fault to its
  notification