"""Wind turbine: power from the wind through a power curve, capped by the
active power limit the park controller writes into a holding register."""
from __future__ import annotations

from .. import environment
from ..scenarios import Scenario, flag, number
from .base import Device

# A generic 3.6 MW class turbine
CUT_IN_MS = 3.0       # starts producing
RATED_WIND_MS = 12.0  # reaches rated power
CUT_OUT_MS = 25.0     # shuts down to protect itself
RESTART_MS = 22.0     # restarts after a storm stop only once wind drops below this

# Values of the state register, see register_map.toml
PRODUCING, LOW_WIND, CURTAILED, STORM_STOP, FAULT, MAINTENANCE = 1, 2, 3, 4, 5, 6


def power_curve(wind_ms: float, rated_kw: float) -> float:
    """Power the turbine can make at this wind speed, in kW.

    Cubic between cut-in and rated wind (power in the wind grows with the cube
    of its speed), flat up to cut-out. Real curves come from the manufacturer,
    measured on 10-minute averages, but they have this shape. The alert rule
    that judges underperformance uses the same formula.
    """
    if wind_ms < CUT_IN_MS or wind_ms >= CUT_OUT_MS:
        return 0.0
    if wind_ms >= RATED_WIND_MS:
        return rated_kw
    return rated_kw * (wind_ms**3 - CUT_IN_MS**3) / (RATED_WIND_MS**3 - CUT_IN_MS**3)


class Turbine(Device):
    NOMINAL_VOLTAGE_V = 690.0
    RATED_POWER_KW = 3600

    SCENARIO_KEYS = {
        **Device.SCENARIO_KEYS,
        "wind_speed": number,   # force the wind at this turbine, m/s
        "performance": number,  # 0..1, e.g. 0.6 for iced blades: produces, but less than it should
        "fault": flag,          # tripped on a fault
        "maintenance": flag,    # stopped for planned work
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.storm_stopped = False
        self.bank.set("power_limit", 100.0)  # no limit until the park controller sets one

    def update(self, now: float, scenario: Scenario) -> None:
        super().update(now, scenario)
        conditions = scenario.device

        # The park's wind plus this turbine's own turbulence
        wind = conditions.get(
            "wind_speed",
            environment.wind_speed(self.site, now) + 0.6 * environment.noise(f"{self.device_id}:turbulence", now, 8),
        )
        wind = max(0.0, wind)
        if wind >= CUT_OUT_MS:
            self.storm_stopped = True
        elif wind < RESTART_MS:
            self.storm_stopped = False

        performance = min(max(conditions.get("performance", 1.0), 0.0), 1.0)
        available = power_curve(wind, self.RATED_POWER_KW) * performance
        limit = self.bank.get("power_limit") / 100 * self.RATED_POWER_KW

        if conditions.get("fault"):
            state, power = FAULT, 0.0
        elif conditions.get("maintenance"):
            state, power = MAINTENANCE, 0.0
        elif self.storm_stopped:
            state, power = STORM_STOP, 0.0
        elif wind < CUT_IN_MS:
            state, power = LOW_WIND, 0.0
        elif limit < available:
            state, power = CURTAILED, limit
        else:
            state, power = PRODUCING, available

        if power > 0:
            # Rotor speeds up with the wind until it reaches its rated speed
            rotor_rpm = min(14.0, 6.0 + (wind - CUT_IN_MS) * 8.0 / (RATED_WIND_MS - CUT_IN_MS))
        elif state in (LOW_WIND, CURTAILED):
            rotor_rpm = 2.0  # blades pitched out, idling
        else:
            rotor_rpm = 0.0  # stopped and braked

        connected = state in (PRODUCING, CURTAILED)
        reactive = 150.0 * environment.noise(f"{self.device_id}:q", now, 60) if connected else 0.0

        self.bank.set("wind_speed", wind)
        self.bank.set("state", state)
        self.bank.set("rotor_speed", rotor_rpm)
        self.bank.set("active_power", power)
        self.bank.set("reactive_power", reactive)