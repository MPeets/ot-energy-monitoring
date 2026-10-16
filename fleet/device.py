"""A simulated field device: python -m fleet.device

Serves its register map over Modbus TCP, the way the controller in a turbine,
inverter, battery or substation bay would.

Environment:
  DEVICE_TYPE   turbine, inverter, battery or substation
  DEVICE_ID     name used in logs, e.g. wt-01
  MODBUS_PORT   default 502, the standard Modbus TCP port
"""
from __future__ import annotations

import asyncio
import os
import time

from . import modbus
from .registers import RegisterBank, load_map


async def main() -> None:
    device_type = os.environ["DEVICE_TYPE"]
    device_id = os.environ.get("DEVICE_ID", device_type)
    port = int(os.environ.get("MODBUS_PORT", "502"))

    def log(message: str) -> None:
        print(f"{device_id}: {message}", flush=True)

    register_map = load_map(device_type)
    bank = RegisterBank(register_map.registers)
    bank.set("device_type", register_map.type_code)
    bank.set("map_version", register_map.version)

    server = await modbus.start_server(bank, port=port, log=log)
    log(f"{device_type} serving Modbus TCP on port {port}, register map v{register_map.version}")

    started = time.monotonic()
    async with server:
        while True:
            bank.set("heartbeat", time.monotonic() - started)
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())