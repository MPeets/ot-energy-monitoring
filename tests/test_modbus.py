import asyncio
import socket
import struct
import unittest

from fleet import modbus
from fleet.registers import Register, RegisterBank, decode, encode, load_map


class FakeBank:
    def __init__(self):
        self.input = list(range(10))
        self.holding = [100 + i for i in range(10)]

    def write(self, address, raw):
        raise modbus.ModbusException(modbus.WRITE_SINGLE_REGISTER, modbus.ILLEGAL_DATA_ADDRESS)


def setpoint_bank() -> RegisterBank:
    return RegisterBank({
        "limit": Register("limit", "holding", 0, "UINT16", scale=0.1, unit="%", writable=True, minimum=0, maximum=100),
        "status": Register("status", "holding", 1, "UINT16"),
    })


def read_pdu(function: int, start: int, count: int) -> bytes:
    return struct.pack(">BHH", function, start, count)


class HandleRequestTest(unittest.TestCase):
    def setUp(self):
        self.bank = FakeBank()

    def test_reads_input_registers(self):
        reply = modbus.handle_request(read_pdu(0x04, 2, 3), self.bank)
        self.assertEqual(reply, bytes([0x04, 6, 0, 2, 0, 3, 0, 4]))

    def test_reads_holding_registers(self):
        reply = modbus.handle_request(read_pdu(0x03, 0, 1), self.bank)
        self.assertEqual(reply, bytes([0x03, 2, 0, 100]))

    def test_unsupported_functions_are_illegal_function(self):
        write_multiple = struct.pack(">BHHBH", 0x10, 0, 1, 2, 0)
        write_coil = struct.pack(">BHH", 0x05, 0, 0xFF00)
        for pdu in (write_multiple, write_coil):
            self.assertEqual(modbus.handle_request(pdu, self.bank), bytes([pdu[0] | 0x80, 0x01]))
        self.assertEqual(self.bank.holding[0], 100)

    def test_write_to_a_writable_setpoint_is_echoed(self):
        bank = setpoint_bank()
        written = []
        pdu = struct.pack(">BHH", 0x06, 0, 500)
        reply = modbus.handle_request(pdu, bank, on_write=lambda reg, value: written.append((reg.name, value)))
        self.assertEqual(reply, pdu)
        self.assertAlmostEqual(bank.get("limit"), 50.0)
        self.assertEqual(written, [("limit", 50.0)])

    def test_write_to_a_read_only_register_is_illegal_address(self):
        bank = setpoint_bank()
        reply = modbus.handle_request(struct.pack(">BHH", 0x06, 1, 7), bank)
        self.assertEqual(reply, bytes([0x86, 0x02]))
        self.assertEqual(bank.holding[1], 0)

    def test_write_outside_the_setpoint_range_is_illegal_value(self):
        bank = setpoint_bank()
        reply = modbus.handle_request(struct.pack(">BHH", 0x06, 0, 1500), bank)  # 150 %
        self.assertEqual(reply, bytes([0x86, 0x03]))
        self.assertEqual(bank.holding[0], 0)

    def test_reading_past_the_table_is_illegal_address(self):
        reply = modbus.handle_request(read_pdu(0x04, 8, 3), self.bank)
        self.assertEqual(reply, bytes([0x84, 0x02]))

    def test_register_count_outside_protocol_limits_is_illegal_value(self):
        for count in (0, 126):
            reply = modbus.handle_request(read_pdu(0x04, 0, count), self.bank)
            self.assertEqual(reply, bytes([0x84, 0x03]))

    def test_truncated_request_is_illegal_value(self):
        self.assertEqual(modbus.handle_request(bytes([0x04, 0, 0]), self.bank), bytes([0x84, 0x03]))


class EncodingTest(unittest.TestCase):
    def test_scaled_integer_round_trip(self):
        reg = Register("frequency", "input", 0, "UINT16", scale=0.001)
        self.assertEqual(encode(reg, 49.987), (49987,))
        self.assertAlmostEqual(decode(reg, (49987,)), 49.987)

    def test_32_bit_values_are_high_word_first(self):
        reg = Register("heartbeat", "input", 0, "UINT32")
        self.assertEqual(encode(reg, 70000), (1, 4464))  # 70000 = 1 * 65536 + 4464

    def test_float32_is_big_endian(self):
        reg = Register("voltage", "input", 0, "FLOAT32")
        self.assertEqual(encode(reg, 50.0), (0x4248, 0x0000))
        self.assertEqual(decode(reg, (0x4248, 0x0000)), 50.0)

    def test_signed_values_survive(self):
        reg = Register("reactive_power", "input", 0, "INT16")
        self.assertEqual(decode(reg, encode(reg, -1500)), -1500)

    def test_out_of_range_values_saturate_instead_of_wrapping(self):
        unsigned = Register("x", "input", 0, "UINT16")
        signed = Register("y", "input", 0, "INT16", scale=0.1)
        self.assertEqual(encode(unsigned, 70000), (65535,))
        self.assertEqual(encode(unsigned, -5), (0,))
        self.assertAlmostEqual(decode(signed, encode(signed, -5000)), -3276.8)


class RegisterBankTest(unittest.TestCase):
    def test_overlapping_registers_are_rejected(self):
        registers = {
            "a": Register("a", "input", 0, "UINT32"),
            "b": Register("b", "input", 1, "UINT16"),
        }
        with self.assertRaisesRegex(ValueError, "overlaps"):
            RegisterBank(registers)

    def test_only_single_holding_registers_can_be_writable(self):
        for reg in (Register("a", "input", 0, "UINT16", writable=True), Register("b", "holding", 0, "UINT32", writable=True)):
            with self.subTest(reg=reg.name), self.assertRaisesRegex(ValueError, "writable"):
                RegisterBank({reg.name: reg})

    def test_same_address_in_different_tables_is_fine(self):
        registers = {
            "a": Register("a", "input", 0, "UINT16"),
            "b": Register("b", "holding", 0, "UINT16"),
        }
        RegisterBank(registers)

    def test_every_device_map_in_the_repo_is_valid(self):
        for device_type in ("turbine", "inverter", "battery", "substation"):
            with self.subTest(device_type=device_type):
                register_map = load_map(device_type)
                bank = RegisterBank(register_map.registers)
                bank.set("device_type", register_map.type_code)
                self.assertEqual(bank.get("device_type"), register_map.type_code)

    def test_unknown_device_type_is_rejected(self):
        with self.assertRaises(ValueError):
            load_map("toaster")


class ServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.log = []
        self.bank = FakeBank()
        self.server = await modbus.start_server(self.bank, "127.0.0.1", 0, log=self.log.append)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def call(self, func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, port=self.port, **kwargs)

    async def test_client_reads_registers_over_tcp(self):
        words = await self.call(modbus.read_registers, "127.0.0.1", modbus.READ_INPUT_REGISTERS, 1, 3)
        self.assertEqual(words, [1, 2, 3])

    async def test_refused_requests_over_tcp_are_logged(self):
        with self.assertRaises(modbus.ModbusException) as caught:
            await self.call(modbus.transact, "127.0.0.1", struct.pack(">BHHBH", 0x10, 0, 1, 2, 0))
        self.assertEqual(caught.exception.code, modbus.ILLEGAL_FUNCTION)
        with self.assertRaises(modbus.ModbusException) as caught:
            await self.call(modbus.transact, "127.0.0.1", struct.pack(">BHH", 0x06, 0, 0))
        self.assertEqual(caught.exception.code, modbus.ILLEGAL_DATA_ADDRESS)
        self.assertTrue(any("refused function 0x10" in line for line in self.log))
        self.assertTrue(any("refused function 0x06" in line and "Illegal Data Address" in line for line in self.log))

    async def test_reply_echoes_the_unit_id(self):
        reply = await self.call(modbus.transact, "127.0.0.1", read_pdu(0x03, 0, 1), unit=7)
        self.assertEqual(reply, bytes([0x03, 2, 0, 100]))

    async def test_non_modbus_traffic_gets_disconnected(self):
        def send_garbage() -> bytes:
            with socket.create_connection(("127.0.0.1", self.port), timeout=3) as sock:
                sock.sendall(b"GET / HTTP/1.1\r\n\r\n")
                return sock.recv(100)

        self.assertEqual(await asyncio.to_thread(send_garbage), b"")
        self.assertTrue(any("not a Modbus TCP frame" in line for line in self.log))


if __name__ == "__main__":
    unittest.main()