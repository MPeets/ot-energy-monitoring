"""Substation at the point of connection: the 110/20 kV transformer every site
exports through, and the RTU that gathers the feeders' readings.

The RTU polls each feeder's active power over Modbus on the OT network, the
way a substation RTU collects bay values. A feeder that stops answering keeps
its last value, as RTUs do (a real one would also flag it as stale).

The transformer's top-oil temperature follows the simplified thermal model
of IEC 60076-7, with the time constant cut from hours to minutes for the lab.
"""
from __future__ import annotations

import math
import os
import threading
import time

from .. import environment, modbus
from ..registers import decode, load_map
from ..scenarios import Scenario, flag, number
from .base import Device

LOSSES = 0.01                 # share of the fleet's output lost in cables and the transformer
RATED_TOP_OIL_RISE_K = 55.0   # top oil above ambient at rated load, cooling running
LOSS_RATIO = 6.0              # load losses / no-load losses at rated load
OIL_EXPONENT = 0.8
OIL_TIME_CONSTANT_S = 600     # real transformers take hours; minutes keep the demo short
FAILED_COOLING_FACTOR = 2.0   # fans and pumps stopped: same load, roughly twice the rise

# Active power sits at the same address on every device (common block)
ACTIVE_POWER = load_map("substation").registers["active_power"]


def read_active_power(host: str) -> float:
    words = modbus.read_registers(
        host, modbus.READ_INPUT_REGISTERS, ACTIVE_POWER.address, ACTIVE_POWER.width, timeout=1.0
    )
    return decode(ACTIVE_POWER, words)


def top_oil_rise(loading: float, cooling_failed: bool = False) -> float:
    """Steady-state top-oil rise above ambient in K at this loading (1.0 = rated)."""
    rise = RATED_TOP_OIL_RISE_K * ((1 + LOSS_RATIO * loading**2) / (1 + LOSS_RATIO)) ** OIL_EXPONENT
    return rise * FAILED_COOLING_FACTOR if cooling_failed else rise


class FeederPoller:
    """The RTU's polling loop, in a thread so a slow feeder can't stall the
    substation's own Modbus server."""

    def __init__(self, feeders: list[str], read=read_active_power, interval: float = 5.0):
        self.feeders = feeders
        self.read = read
        self.interval = interval
        self.power_kw: dict[str, float] = {}
        self.lock = threading.Lock()

    def poll_once(self) -> None:
        for host in self.feeders:
            try:
                value = self.read(host)
            except (OSError, ValueError, modbus.ModbusException):
                continue  # keep the last value
            with self.lock:
                self.power_kw[host] = value

    def total_kw(self) -> float:
        with self.lock:
            return sum(self.power_kw.values())

    def start(self) -> None:
        def loop():
            while True:
                self.poll_once()
                time.sleep(self.interval)

        threading.Thread(target=loop, name="rtu-poller", daemon=True).start()


class Substation(Device):
    NOMINAL_VOLTAGE_V = 110_000.0
    RATED_POWER_KW = 25_000  # transformer rating, 25 MVA

    SCENARIO_KEYS = {
        **Device.SCENARIO_KEYS,
        "cooling_failure": flag,        # transformer fans and oil pumps stop
        "ambient_temperature": number,  # force the outdoor temperature, degrees C
        "load_kw": number,              # force the transformer load, as if the fleet exported this much
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Comma-separated feeder hosts, e.g. "wt-01,wt-02,inv-01"
        feeders = [name for name in os.environ.get("FEEDERS", "").split(",") if name]
        self.poller = FeederPoller(feeders)
        if feeders:
            self.poller.start()
        self.oil_c = None
        self.last_update = None

    def update(self, now: float, scenario: Scenario) -> None:
        super().update(now, scenario)
        conditions = scenario.device
        dt = 1.0 if self.last_update is None else min(max(now - self.last_update, 0.0), 10.0)
        self.last_update = now

        export_kw = conditions.get("load_kw", self.poller.total_kw() * (1 - LOSSES))
        reactive = 300.0 * environment.noise(f"{self.device_id}:q", now, 60)
        loading = math.hypot(export_kw, reactive) / self.RATED_POWER_KW
        ambient = conditions.get("ambient_temperature", environment.ambient_temperature(now))

        target = ambient + top_oil_rise(loading, conditions.get("cooling_failure", False))
        if self.oil_c is None:
            self.oil_c = target  # start out settled
        else:
            self.oil_c += (target - self.oil_c) * min(dt / OIL_TIME_CONSTANT_S, 1.0)

        self.bank.set("active_power", export_kw)
        self.bank.set("reactive_power", reactive)
        self.bank.set("oil_temperature", self.oil_c)
        self.bank.set("ambient_temperature", ambient)
        self.bank.set("transformer_load", 100 * loading)