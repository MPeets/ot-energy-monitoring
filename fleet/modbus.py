"""Modbus TCP, only as much of it as this lab needs.

Devices answer function codes 03 (read holding registers), 04 (read input
registers) and 06 (write single register). A write is only accepted for a
setpoint the register map marks writable, such as a turbine's power limit,
which is how a park controller curtails it. Anything else gets exception 01,
Illegal Function.

That write path exists on purpose, the way it does on real equipment. Keeping
monitoring away from it is the network's job, not the device's.

Client side is a small blocking reader for the engineering workstation CLI
and the tests.

Standard library only, so nothing has to be downloaded into an offline network.
"""
from __future__ import annotations

import asyncio
import socket
import struct

READ_HOLDING_REGISTERS = 0x03
READ_INPUT_REGISTERS = 0x04
WRITE_SINGLE_REGISTER = 0x06

ILLEGAL_FUNCTION = 0x01
ILLEGAL_DATA_ADDRESS = 0x02
ILLEGAL_DATA_VALUE = 0x03

EXCEPTION_NAMES = {
    ILLEGAL_FUNCTION: "Illegal Function",
    ILLEGAL_DATA_ADDRESS: "Illegal Data Address",
    ILLEGAL_DATA_VALUE: "Illegal Data Value",
}

MAX_READ_REGISTERS = 125  # protocol limit for a single read request

# MBAP header: transaction id, protocol id (always 0), byte count of what
# follows (unit id + PDU), unit id
MBAP = struct.Struct(">HHHB")
MAX_MBAP_LENGTH = 254  # unit id + the largest PDU the spec allows (253 bytes)


class ModbusException(Exception):
    """The device answered, but with an exception response instead of data."""

    def __init__(self, function: int, code: int):
        self.function = function
        self.code = code
        name = EXCEPTION_NAMES.get(code, "Unknown")
        super().__init__(f"function {function:#04x} refused: exception {code:#04x} ({name})")


def exception_response(function: int, code: int) -> bytes:
    return bytes([function | 0x80, code])


def handle_request(pdu: bytes, bank, on_write=None) -> bytes:
    """Answers one request PDU (function code + data) from a device's register tables.

    on_write(register, value) is called after a write is accepted.
    """
    function = pdu[0]
    if function not in (READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS, WRITE_SINGLE_REGISTER):
        return exception_response(function, ILLEGAL_FUNCTION)
    if len(pdu) != 5:
        return exception_response(function, ILLEGAL_DATA_VALUE)

    if function == WRITE_SINGLE_REGISTER:
        address, raw = struct.unpack(">HH", pdu[1:])
        try:
            register, value = bank.write(address, raw)
        except ModbusException as exc:
            return exception_response(function, exc.code)
        if on_write:
            on_write(register, value)
        return pdu  # a successful write echoes the request

    table = bank.holding if function == READ_HOLDING_REGISTERS else bank.input
    start, count = struct.unpack(">HH", pdu[1:])
    if not 1 <= count <= MAX_READ_REGISTERS:
        return exception_response(function, ILLEGAL_DATA_VALUE)
    if start + count > len(table):
        return exception_response(function, ILLEGAL_DATA_ADDRESS)
    return struct.pack(f">BB{count}H", function, 2 * count, *table[start:start + count])


async def start_server(bank, host: str = "0.0.0.0", port: int = 502, log=print) -> asyncio.Server:
    """Serves the bank's registers over Modbus TCP until the server is closed."""

    async def session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        client = peer[0] if peer else "?"
        try:
            while True:
                header = await reader.readexactly(MBAP.size)
                transaction, protocol, length, unit = MBAP.unpack(header)
                if protocol != 0 or not 2 <= length <= MAX_MBAP_LENGTH:
                    log(f"dropped connection from {client}: not a Modbus TCP frame")
                    break
                pdu = await reader.readexactly(length - 1)
                reply = handle_request(
                    pdu, bank, on_write=lambda reg, value: log(f"{client} set {reg.name} = {value:g} {reg.unit}")
                )
                if reply[0] & 0x80 and (reply[1] == ILLEGAL_FUNCTION or pdu[0] == WRITE_SINGLE_REGISTER):
                    log(f"refused function {pdu[0]:#04x} from {client}: {EXCEPTION_NAMES.get(reply[1], reply[1])}")
                writer.write(MBAP.pack(transaction, 0, len(reply) + 1, unit) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass  # client disconnected
        finally:
            writer.close()

    return await asyncio.start_server(session, host, port)


def transact(host: str, pdu: bytes, *, port: int = 502, unit: int = 1, timeout: float = 3.0) -> bytes:
    """Sends one request PDU and returns the response PDU. Raises ModbusException on an exception reply."""
    transaction = 1
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(MBAP.pack(transaction, 0, len(pdu) + 1, unit) + pdu)
        reply_transaction, protocol, length, reply_unit = MBAP.unpack(_recv_exactly(sock, MBAP.size))
        if reply_transaction != transaction or protocol != 0 or reply_unit != unit or length < 2:
            raise ValueError("malformed Modbus TCP reply")
        reply = _recv_exactly(sock, length - 1)
    if reply[0] & 0x80:
        raise ModbusException(reply[0] & 0x7F, reply[1])
    return reply


def read_registers(host: str, function: int, start: int, count: int, **kwargs) -> list[int]:
    reply = transact(host, struct.pack(">BHH", function, start, count), **kwargs)
    if reply[0] != function or reply[1] != 2 * count or len(reply) != 2 + 2 * count:
        raise ValueError("reply does not match the request")
    return list(struct.unpack(f">{count}H", reply[2:]))


def _recv_exactly(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("device closed the connection")
        data += chunk
    return data