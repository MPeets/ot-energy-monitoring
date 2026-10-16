"""A simulated field device: python -m fleet.device

Serves its register map over Modbus TCP, the way the controller in a turbine,
inverter, battery or substation bay would.

Environment:
  DEVICE_TYPE    turbine, inverter, battery or substation
  DEVICE_ID      name used in logs and scenario files, e.g. wt-01
  SITE           site the device belongs to, e.g. wind-a (devices on one site share weather)
  MODBUS_PORT    default 502, the standard Modbus TCP port
  SCENARIO_DIR   default /scenarios, see fleet/scenarios.py
"""
from __future__ import annotations

import asyncio
import os
import time

from . import modbus, models
from .registers import RegisterBank, load_map
from .scenarios import ScenarioWatcher


async def main() -> None:
    device_type = os.environ["DEVICE_TYPE"]
    device_id = os.environ.get("DEVICE_ID", device_type)
    site = os.environ.get("SITE", device_id)
    port = int(os.environ.get("MODBUS_PORT", "502"))
    scenario_dir = os.environ.get("SCENARIO_DIR", "/scenarios")

    def log(message: str) -> None:
        print(f"{device_id}: {message}", flush=True)

    register_map = load_map(device_type)
    bank = RegisterBank(register_map.registers)
    model = models.create(device_type, device_id, bank, register_map, site)
    scenarios = ScenarioWatcher(scenario_dir, device_id, model.SCENARIO_KEYS, log)

    # Fill every register before accepting connections. A collector that
    # connects during startup must never read 0 Hz or 0 V as if it were real.
    model.update(time.time(), scenarios.poll())

    server = await modbus.start_server(bank, port=port, log=log)
    log(f"{device_type} at {site} serving Modbus TCP on port {port}, register map v{register_map.version}")

    async with server:
        while True:
            await asyncio.sleep(1)
            model.update(time.time(), scenarios.poll())


if __name__ == "__main__":
    asyncio.run(main())