import asyncio
import struct
import tempfile
import unittest
from pathlib import Path

from fleet import modbus, models
from fleet.gateway import Gateway
from fleet.inventory import Asset, load_inventory
from fleet.registers import RegisterBank, load_map
from fleet.scenarios import Scenario

LOCALHOST = "127.0.0.1"


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # A real turbine behind the gateway, which would accept a power limit write
        register_map = load_map("turbine")
        self.bank = RegisterBank(register_map.registers)
        models.create("turbine", "wt-02", self.bank, register_map, "wind-a").update(1_800_000_000, Scenario())
        self.device = await modbus.start_server(self.bank, LOCALHOST, 0, log=lambda _: None)
        device_port = self.device.sockets[0].getsockname()[1]

        # A device that accepts connections and never answers, like a hung controller
        async def hang(reader, writer):
            await reader.read()  # returns once the gateway gives up and disconnects
            writer.close()
        self.hung = await asyncio.start_server(hang, LOCALHOST, 0)
        hung_port = self.hung.sockets[0].getsockname()[1]

        self.log = []
        assets = [
            Asset("wt-02", 12, "wind-a", "turbine", host=LOCALHOST, port=device_port),
            Asset("wt-03", 13, "wind-a", "turbine", host=LOCALHOST, port=hung_port),
            Asset("inv-01", 21, "solar-b", "inverter", host=LOCALHOST, port=1),  # nothing listens there
        ]
        self.gateway_logic = Gateway(assets, log=self.log.append, timeout=0.3)
        self.gateway = await modbus.serve(self.gateway_logic.handle, LOCALHOST, 0, log=self.log.append)
        self.port = self.gateway.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for server in (self.gateway, self.device, self.hung):
            server.close()

    async def request(self, unit: int, pdu: bytes) -> bytes:
        return await asyncio.to_thread(modbus.transact, LOCALHOST, pdu, port=self.port, unit=unit)

    async def code_of(self, unit: int, pdu: bytes) -> int:
        with self.assertRaises(modbus.ModbusException) as caught:
            await self.request(unit, pdu)
        return caught.exception.code

    async def test_reads_reach_the_device_by_unit_id(self):
        reply = await self.request(12, struct.pack(">BHH", 0x04, 0, 4))
        self.assertEqual(reply[2:], struct.pack(">4H", *self.bank.input[0:4]))

    async def test_writes_are_refused_at_the_boundary_even_though_the_device_would_accept(self):
        code = await self.code_of(12, struct.pack(">BHH", 0x06, 0, 0))  # power_limit = 0 %
        self.assertEqual(code, modbus.ILLEGAL_FUNCTION)
        self.assertAlmostEqual(self.bank.get("power_limit"), 100.0)
        self.assertTrue(any("refused function 0x06" in line and "wt-02" in line for line in self.log))

    async def test_unknown_unit_is_gateway_path_unavailable(self):
        self.assertEqual(await self.code_of(99, struct.pack(">BHH", 0x04, 0, 1)), modbus.GATEWAY_PATH_UNAVAILABLE)

    async def test_dead_and_hung_devices_are_target_failed(self):
        read = struct.pack(">BHH", 0x04, 0, 1)
        self.assertEqual(await self.code_of(21, read), modbus.GATEWAY_TARGET_FAILED)  # connection refused
        self.assertEqual(await self.code_of(13, read), modbus.GATEWAY_TARGET_FAILED)  # timed out

    async def test_a_dead_device_is_logged_once_and_its_recovery_too(self):
        read = struct.pack(">BHH", 0x04, 0, 1)
        for _ in range(3):
            await self.code_of(21, read)
        self.assertEqual(sum("inv-01) is not responding" in line for line in self.log), 1)

        self.gateway_logic.routes[21] = Asset("inv-01", 21, "solar-b", "inverter", host=LOCALHOST,
                                              port=self.device.sockets[0].getsockname()[1])
        await self.request(21, read)
        self.assertTrue(any("inv-01) is responding again" in line for line in self.log))


class InventoryTest(unittest.TestCase):
    def write(self, text: str) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "inventory.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_repo_inventory_is_valid(self):
        assets = load_inventory(Path(__file__).resolve().parent.parent / "inventory.toml")
        self.assertEqual(len(assets), 7)
        self.assertEqual({a.type for a in assets}, {"turbine", "inverter", "battery", "substation"})

    def test_duplicate_unit_ids_are_rejected(self):
        path = self.write('[[device]]\nid="a"\nunit=11\nsite="s"\ntype="turbine"\n'
                          '[[device]]\nid="b"\nunit=11\nsite="s"\ntype="turbine"\n')
        with self.assertRaisesRegex(ValueError, "already taken"):
            load_inventory(path)

    def test_unknown_type_and_bad_unit_ids_are_rejected(self):
        for entry, message in (('unit=11\ntype="toaster"', "unknown type"), ('unit=0\ntype="turbine"', "outside")):
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, message):
                load_inventory(self.write(f'[[device]]\nid="a"\nsite="s"\n{entry}\n'))


if __name__ == "__main__":
    unittest.main()