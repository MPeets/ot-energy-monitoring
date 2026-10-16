"""What every simulated device does: identify itself, keep a heartbeat and
measure the grid at its terminals."""
from __future__ import annotations

import math
import time

from .. import environment
from ..registers import RegisterBank, RegisterMap
from ..scenarios import Scenario, number, phases

PHASES = ("l1", "l2", "l3")

# Grid protection of the generating units. Generic lab settings: real ones come
# from the grid code and each site's connection agreement.
UNDERVOLTAGE_TRIP_PU = 0.85
OVERVOLTAGE_TRIP_PU = 1.15
UNDERFREQUENCY_TRIP_HZ = 47.5
OVERFREQUENCY_TRIP_HZ = 51.5
RECONNECT_DELAY_S = 60

# Trip causes, also used as inverter fault codes
GRID_OVERVOLTAGE, GRID_UNDERVOLTAGE, GRID_FREQUENCY = 101, 102, 103


class GridProtection:
    """Disconnects a unit when voltage or frequency at its terminals leaves the
    protection settings. It reconnects only after the grid has been back in
    range for RECONNECT_DELAY_S, so a wobbling grid doesn't get hammered by
    units switching back on and off."""

    def __init__(self):
        self.cause = 0  # 0 = connected, otherwise why it tripped
        self.healthy_since = None

    def check(self, now: float, frequency: float, phase_pu) -> int:
        if max(phase_pu) > OVERVOLTAGE_TRIP_PU:
            cause = GRID_OVERVOLTAGE
        elif min(phase_pu) < UNDERVOLTAGE_TRIP_PU:
            cause = GRID_UNDERVOLTAGE
        elif not UNDERFREQUENCY_TRIP_HZ <= frequency <= OVERFREQUENCY_TRIP_HZ:
            cause = GRID_FREQUENCY
        else:
            cause = 0

        if cause:
            self.cause, self.healthy_since = cause, None
        elif self.cause:
            if self.healthy_since is None:
                self.healthy_since = now
            if now - self.healthy_since >= RECONNECT_DELAY_S:
                self.cause = 0
        return self.cause


class Device:
    # Nameplate values. Subclasses set their own.
    NOMINAL_VOLTAGE_V = 690.0  # line-to-line, at the device's terminals
    RATED_POWER_KW = 0

    # Keys this device accepts in scenarios/<device>.json
    SCENARIO_KEYS = {
        "frequency_offset_hz": number,  # a meter that reads wrong
        "voltage_pu": phases,           # force phase voltages, e.g. {"l2": 0.85}
    }

    def __init__(self, device_id: str, bank: RegisterBank, register_map: RegisterMap, site: str | None = None):
        self.device_id = device_id
        self.site = site or device_id
        self.bank = bank
        self.started = time.monotonic()
        # Registers report phase-to-neutral voltage, so nominal is line voltage / sqrt(3)
        self.nominal_phase_voltage = self.NOMINAL_VOLTAGE_V / math.sqrt(3)

        bank.set("device_type", register_map.type_code)
        bank.set("map_version", register_map.version)
        bank.set("voltage_nominal", self.nominal_phase_voltage)
        bank.set("rated_power", self.RATED_POWER_KW)

    def update(self, now: float, scenario: Scenario) -> None:
        """Recomputes every register for wall-clock time `now`. Called once a second."""
        self.bank.set("heartbeat", time.monotonic() - self.started)

        frequency = (
            environment.grid_frequency(now)
            + scenario.grid.get("frequency_offset_hz", 0.0)
            + scenario.device.get("frequency_offset_hz", 0.0)
            + 0.001 * environment.noise(f"{self.device_id}:meter", now, 5)  # meter noise
        )
        self.bank.set("frequency", frequency)

        forced = scenario.device.get("voltage_pu", {})
        normal = environment.phase_voltages_pu(self.device_id, now)
        # What this device measures at its terminals, kept for the models that react to it
        self.frequency = frequency
        self.phase_pu = [forced.get(phase, pu) for phase, pu in zip(PHASES, normal)]
        for phase, pu in zip(PHASES, self.phase_pu):
            self.bank.set(f"voltage_{phase}", pu * self.nominal_phase_voltage)