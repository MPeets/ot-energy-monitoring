"""Battery storage: charges on the midday sun, discharges into the evening,
and on top of that provides frequency containment reserve (FCR), pushing
power into the grid when frequency drops and soaking it up when it rises.

Thermal time constants are shortened so a cooling failure plays out in
minutes instead of hours.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..scenarios import Scenario, flag, number
from .base import Device, GridProtection

# Values of the state register, see register_map.toml
CHARGING, DISCHARGING, FREQUENCY_RESPONSE, FAULT, MAINTENANCE = 1, 2, 3, 5, 6

FCR_CAPACITY_KW = 1000         # reserve sold to the grid operator
FCR_FULL_ACTIVATION_HZ = 0.2   # deviation at which all of it is delivered
CHARGE_EFFICIENCY = 0.95
SOC_MIN, SOC_MAX = 5.0, 95.0   # % kept in reserve at both ends, for cell life and for FCR

CELL_AMBIENT_C = 20.0          # the container's HVAC holds the cells here
HEATING_C_PER_KWS = 1e-5       # heat from current through the cells
COOLING_PER_S = 1 / 300        # HVAC pulling cells back towards ambient
FAILED_COOLING_PER_S = COOLING_PER_S * 0.05
BMS_TRIP_C = 55.0              # battery management system opens the contactors here
BMS_RESET_C = 40.0             # and allows a restart once cells are back below this


def schedule_kw(now: float) -> float:
    """The trading schedule, by UTC hour: soak up midday solar, sell into the evening peak."""
    hour = datetime.fromtimestamp(now, timezone.utc).hour
    if 8 <= hour < 13:
        return -1500.0
    if 15 <= hour < 19:
        return 2000.0
    return 0.0


def fcr_kw(frequency: float) -> float:
    """Frequency containment reserve: proportional, full response at +-200 mHz.
    Below 50 Hz the battery discharges to hold frequency up, above it charges."""
    deviation = (frequency - 50.0) / FCR_FULL_ACTIVATION_HZ
    return -FCR_CAPACITY_KW * min(max(deviation, -1.0), 1.0)


class Battery(Device):
    NOMINAL_VOLTAGE_V = 690.0
    RATED_POWER_KW = 5000
    CAPACITY_KWH = 10000

    SCENARIO_KEYS = {
        **Device.SCENARIO_KEYS,
        "power_kw": number,       # override the schedule, negative charges, e.g. -5000
        "cooling_failure": flag,  # container HVAC stops
        "fault": flag,            # tripped on a fault
        "maintenance": flag,      # stopped for planned work
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.protection = GridProtection()
        self.stored_kwh = 0.5 * self.CAPACITY_KWH
        self.cell_c = CELL_AMBIENT_C
        self.bms_tripped = False
        self.last_update = None
        self.bank.set("energy_capacity", self.CAPACITY_KWH)

    def update(self, now: float, scenario: Scenario) -> None:
        super().update(now, scenario)
        conditions = scenario.device
        dt = 1.0 if self.last_update is None else min(max(now - self.last_update, 0.0), 10.0)
        self.last_update = now
        soc = 100 * self.stored_kwh / self.CAPACITY_KWH

        if self.cell_c >= BMS_TRIP_C:
            self.bms_tripped = True
        elif self.cell_c < BMS_RESET_C:
            self.bms_tripped = False
        tripped = self.protection.check(now, self.frequency, self.phase_pu)

        planned = conditions.get("power_kw", schedule_kw(now))
        # Full: no more charging. Empty: no more discharging. FCR still works the other way.
        if soc >= SOC_MAX:
            planned = max(planned, 0.0)
        if soc <= SOC_MIN:
            planned = min(planned, 0.0)
        power = min(max(planned + fcr_kw(self.frequency), -self.RATED_POWER_KW), self.RATED_POWER_KW)
        if soc >= SOC_MAX:
            power = max(power, 0.0)
        if soc <= SOC_MIN:
            power = min(power, 0.0)

        if conditions.get("fault") or tripped or self.bms_tripped:
            state, power = FAULT, 0.0
        elif conditions.get("maintenance"):
            state, power = MAINTENANCE, 0.0
        elif planned < 0:
            state = CHARGING
        elif planned > 0:
            state = DISCHARGING
        else:
            state = FREQUENCY_RESPONSE

        # Energy: charging loses a little on the way in, discharging on the way out
        if power < 0:
            self.stored_kwh += -power * dt / 3600 * CHARGE_EFFICIENCY
        else:
            self.stored_kwh -= power * dt / 3600 / CHARGE_EFFICIENCY
        self.stored_kwh = min(max(self.stored_kwh, 0.0), self.CAPACITY_KWH)

        # Cells heat with current and are cooled towards ambient by the HVAC
        cooling = FAILED_COOLING_PER_S if conditions.get("cooling_failure") else COOLING_PER_S
        self.cell_c += (HEATING_C_PER_KWS * abs(power) - cooling * (self.cell_c - CELL_AMBIENT_C)) * dt

        self.bank.set("soc", 100 * self.stored_kwh / self.CAPACITY_KWH)
        self.bank.set("state", state)
        self.bank.set("cell_temperature", self.cell_c)
        self.bank.set("active_power", power)
        self.bank.set("reactive_power", 0.0)