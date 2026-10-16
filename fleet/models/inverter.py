"""Solar inverter: turns what the PV modules make from sunlight into AC, capped
by its own rating and by the power limit the park controller writes."""
from __future__ import annotations

from .. import environment
from ..scenarios import Scenario, flag, number
from .base import GRID_FREQUENCY, GRID_OVERVOLTAGE, GRID_UNDERVOLTAGE, Device, GridProtection

# Share of (irradiance x module rating) that reaches the grid after heat,
# wiring and conversion losses. The alert rule for underperformance uses the
# same figure to work out what the park should be making.
PERFORMANCE_RATIO = 0.85
WAKE_UP_WM2 = 20.0  # below this there isn't enough light to bother, the inverter sleeps

# Values of the state and fault_code registers, see register_map.toml
PRODUCING, STANDBY, CURTAILED, FAULT, MAINTENANCE = 1, 2, 3, 5, 6
NO_FAULT, DC_INSULATION, OVERTEMPERATURE = 0, 201, 301
FAULT_CODES = {
    NO_FAULT: "none",
    GRID_OVERVOLTAGE: "grid overvoltage",
    GRID_UNDERVOLTAGE: "grid undervoltage",
    GRID_FREQUENCY: "grid frequency out of range",
    DC_INSULATION: "DC insulation fault",
    OVERTEMPERATURE: "overtemperature",
}


def expected_power(irradiance_wm2: float, dc_capacity_kwp: float) -> float:
    """What a healthy park makes from this much sunlight, in kW, before the inverter's own limit."""
    return irradiance_wm2 / 1000 * dc_capacity_kwp * PERFORMANCE_RATIO


class Inverter(Device):
    NOMINAL_VOLTAGE_V = 630.0
    RATED_POWER_KW = 2500
    # More module capacity than inverter rating, the usual design: on bright
    # days the inverter clips at its rating, which is normal, not a limit
    # anyone imposed.
    DC_CAPACITY_KWP = 3000

    SCENARIO_KEYS = {
        **Device.SCENARIO_KEYS,
        "irradiance": number,   # force the pyranometer reading, W/m2 (handy at night)
        "performance": number,  # 0..1, e.g. 0.7 for blown string fuses or heavy soiling
        "fault_code": number,   # trip with this code, e.g. 201
        "maintenance": flag,    # stopped for planned work
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.protection = GridProtection()
        self.bank.set("dc_capacity", self.DC_CAPACITY_KWP)
        self.bank.set("power_limit", 100.0)

    def update(self, now: float, scenario: Scenario) -> None:
        super().update(now, scenario)
        conditions = scenario.device

        # Both inverters of a park read the same pyranometer
        irradiance = max(0.0, conditions.get("irradiance", environment.irradiance(self.site, now)))
        performance = min(max(conditions.get("performance", 1.0), 0.0), 1.0)
        mppt_jitter = 1 + 0.01 * environment.noise(f"{self.device_id}:mppt", now, 20)
        available = min(
            expected_power(irradiance, self.DC_CAPACITY_KWP) * performance * mppt_jitter, self.RATED_POWER_KW
        )
        limit = self.bank.get("power_limit") / 100 * self.RATED_POWER_KW
        tripped = self.protection.check(now, self.frequency, self.phase_pu)
        forced_fault = int(conditions.get("fault_code", NO_FAULT))

        fault_code = NO_FAULT
        if forced_fault or tripped:
            state, power, fault_code = FAULT, 0.0, forced_fault or tripped
        elif conditions.get("maintenance"):
            state, power = MAINTENANCE, 0.0
        elif irradiance < WAKE_UP_WM2:
            state, power = STANDBY, 0.0
        elif limit < available:
            state, power = CURTAILED, limit
        else:
            state, power = PRODUCING, available

        connected = state in (PRODUCING, CURTAILED)
        reactive = 100.0 * environment.noise(f"{self.device_id}:q", now, 60) if connected else 0.0

        self.bank.set("irradiance", irradiance)
        self.bank.set("state", state)
        self.bank.set("fault_code", fault_code)
        self.bank.set("active_power", power)
        self.bank.set("reactive_power", reactive)