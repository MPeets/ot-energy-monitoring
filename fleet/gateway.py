"""Modbus TCP gateway between the OT network and the DMZ: python -m fleet.gateway

The only thing in the lab attached to both networks. Requests come in from the
DMZ addressed by unit id, the gateway forwards them to that device on the OT
network and relays the answer back.

It passes the two read function codes (03 and 04) and nothing else. Writes and
everything else get exception 01 and a log line, whatever the device behind it
would accept. That turns "the DMZ only reads" into a control enforced at the
boundary instead of a promise the collector is trusted to keep. In a real plant
this is an industrial firewall with Modbus inspection, or a protocol gateway
configured read-only.

Environment:
  INVENTORY      default inventory.toml, the unit id -> device routing table
  MODBUS_PORT    default 502
"""
from __future__ import annotations

import asyncio
import os

from . import modbus
from .inventory import Asset, load_inventory

ALLOWED_FUNCTIONS = {modbus.READ_HOLDING_REGISTERS, modbus.READ_INPUT_REGISTERS}
DEVICE_TIMEOUT_S = 1.0


class Gateway:
    def __init__(self, assets: list[Asset], log=print, timeout: float = DEVICE_TIMEOUT_S):
        self.routes = {asset.unit: asset for asset in assets}
        self.log = log
        self.timeout = timeout
        self.responding: dict[int, bool] = {}

    async def handle(self, client: str, unit: int, pdu: bytes) -> bytes:
        function = pdu[0]
        asset = self.routes.get(unit)
        if asset is None:
            return modbus.exception_response(function, modbus.GATEWAY_PATH_UNAVAILABLE)
        if function not in ALLOWED_FUNCTIONS:
            self.log(f"refused function {function:#04x} from {client} to unit {unit} ({asset.id})")
            return modbus.exception_response(function, modbus.ILLEGAL_FUNCTION)

        try:
            reply = await asyncio.wait_for(self._ask(asset, pdu), self.timeout)
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError):
            self._note(asset, responding=False)
            return modbus.exception_response(function, modbus.GATEWAY_TARGET_FAILED)
        self._note(asset, responding=True)
        return reply

    async def _ask(self, asset: Asset, pdu: bytes) -> bytes:
        """One request to the device on its own connection. Simple, and a dead
        device can't leave a half-used connection behind."""
        host, port = asset.address
        reader, writer = await asyncio.open_connection(host, port)
        try:
            writer.write(modbus.MBAP.pack(1, 0, len(pdu) + 1, asset.unit) + pdu)
            await writer.drain()
            _, protocol, length, _ = modbus.MBAP.unpack(await reader.readexactly(modbus.MBAP.size))
            if protocol != 0 or not 2 <= length <= modbus.MAX_MBAP_LENGTH:
                raise ValueError("not a Modbus TCP reply")
            return await reader.readexactly(length - 1)
        finally:
            writer.close()

    def _note(self, asset: Asset, responding: bool) -> None:
        """Logs changes only, so a dead device is one line, not one per poll."""
        before = self.responding.get(asset.unit)
        self.responding[asset.unit] = responding
        if not responding and before is not False:
            self.log(f"unit {asset.unit} ({asset.id}) is not responding")
        elif responding and before is False:
            self.log(f"unit {asset.unit} ({asset.id}) is responding again")


async def main() -> None:
    port = int(os.environ.get("MODBUS_PORT", "502"))
    assets = load_inventory(os.environ.get("INVENTORY", "inventory.toml"))

    def log(message: str) -> None:
        print(f"gateway: {message}", flush=True)

    gateway = Gateway(assets, log)
    server = await modbus.serve(gateway.handle, port=port, log=log)
    log(f"serving Modbus TCP on port {port}, read-only, routes: "
        + ", ".join(f"{a.unit}->{a.id}" for a in assets))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())