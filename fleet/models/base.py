"""What every simulated device does: identify itself, keep a heartbeat and
measure the grid at its terminals."""
from __future__ import annotations

import math
import time

from .. import environment
from ..registers import RegisterBank, RegisterMap
from ..scenarios import Scenario, number, phases

PHASES = ("l1", "l2", "l3")


class Device:
    # Nominal line-to-line voltage at the device's terminals. Subclasses set their own.
    NOMINAL_VOLTAGE_V = 690.0

    # Keys this device accepts in scenarios/<device>.json
    SCENARIO_KEYS = {
        "frequency_offset_hz": number,  # a meter that reads wrong
        "voltage_pu": phases,           # force phase voltages, e.g. {"l2": 0.85}
    }

    def __init__(self, device_id: str, bank: RegisterBank, register_map: RegisterMap):
        self.device_id = device_id
        self.bank = bank
        self.started = time.monotonic()
        # Registers report phase-to-neutral voltage, so nominal is line voltage / sqrt(3)
        self.nominal_phase_voltage = self.NOMINAL_VOLTAGE_V / math.sqrt(3)

        bank.set("device_type", register_map.type_code)
        bank.set("map_version", register_map.version)
        bank.set("voltage_nominal", self.nominal_phase_voltage)

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
        for phase, pu in zip(PHASES, environment.phase_voltages_pu(self.device_id, now)):
            self.bank.set(f"voltage_{phase}", forced.get(phase, pu) * self.nominal_phase_voltage)