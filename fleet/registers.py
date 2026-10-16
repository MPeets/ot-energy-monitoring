"""Register maps: where each value lives and how it's encoded.

Device models work in engineering units (volts, watts, degrees). This module
turns those into raw 16-bit registers the way device firmware does, using the
types and scale factors in register_map.toml, and decodes them again for
readers.
"""
from __future__ import annotations

import struct
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .modbus import ILLEGAL_DATA_ADDRESS, ILLEGAL_DATA_VALUE, WRITE_SINGLE_REGISTER, ModbusException

MAP_FILE = Path(__file__).with_name("register_map.toml")
TABLE_SIZE = 100  # registers per table on every simulated device

# struct format and number of 16-bit registers for each type
TYPES = {
    "UINT16": ("H", 1),
    "INT16": ("h", 1),
    "UINT32": ("I", 2),
    "INT32": ("i", 2),
    "FLOAT32": ("f", 2),
}


@dataclass(frozen=True)
class Register:
    name: str
    register: str  # "input" or "holding"
    address: int
    type: str
    scale: float = 1.0
    unit: str = ""
    description: str = ""
    values: dict | None = None    # labels for enumerated values, e.g. states
    writable: bool = False        # setpoints a controller may write
    minimum: float | None = None  # accepted range for writes
    maximum: float | None = None

    @property
    def width(self) -> int:
        return TYPES[self.type][1]


@dataclass(frozen=True)
class RegisterMap:
    device_type: str
    type_code: int
    version: int
    registers: dict[str, Register]


def load_map(device_type: str, path: Path = MAP_FILE) -> RegisterMap:
    """The registers of one kind of device: the common block plus its own."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    codes = data["device_types"]
    if device_type not in codes:
        raise ValueError(f"unknown device type {device_type!r}, expected one of {sorted(codes)}")
    entries = data.get("common", []) + data.get(device_type, [])
    registers = {entry["name"]: Register(**entry) for entry in entries}
    if len(registers) != len(entries):
        raise ValueError(f"duplicate register name in the {device_type} map")
    return RegisterMap(device_type, codes[device_type], data["version"], registers)


def device_type_for_code(code: int, path: Path = MAP_FILE) -> str:
    codes = tomllib.loads(path.read_text(encoding="utf-8"))["device_types"]
    for name, value in codes.items():
        if value == code:
            return name
    raise ValueError(f"unknown device type code {code}")


def encode(reg: Register, value: float) -> tuple[int, ...]:
    """Engineering value -> raw register words, high word first."""
    fmt, width = TYPES[reg.type]
    raw = value / reg.scale
    if reg.type != "FLOAT32":
        bits = 16 * width
        low, high = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if fmt.islower() else (0, (1 << bits) - 1)
        # Firmware saturates at the limits of the type. Wrapping around would
        # turn an overload into a small or negative reading.
        raw = min(max(round(raw), low), high)
    return struct.unpack(f">{width}H", struct.pack(f">{fmt}", raw))


def decode(reg: Register, words) -> float:
    """Raw register words -> engineering value."""
    fmt, width = TYPES[reg.type]
    raw = struct.unpack(f">{fmt}", struct.pack(f">{width}H", *words))[0]
    return raw * reg.scale


class RegisterBank:
    """The input and holding register tables of one device."""

    def __init__(self, registers: dict[str, Register], size: int = TABLE_SIZE):
        self.registers = registers
        self.input = [0] * size
        self.holding = [0] * size

        used = {"input": set(), "holding": set()}
        for reg in registers.values():
            if reg.register not in used or reg.type not in TYPES:
                raise ValueError(f"{reg.name}: unknown register table or type")
            span = set(range(reg.address, reg.address + reg.width))
            if reg.address < 0 or max(span) >= size:
                raise ValueError(f"{reg.name}: address outside the {size}-register table")
            if used[reg.register] & span:
                raise ValueError(f"{reg.name}: overlaps another {reg.register} register")
            if reg.writable and (reg.register != "holding" or reg.width != 1):
                raise ValueError(f"{reg.name}: only single holding registers can be writable")
            used[reg.register] |= span

    def _table(self, reg: Register) -> list[int]:
        return self.input if reg.register == "input" else self.holding

    def set(self, name: str, value: float) -> None:
        reg = self.registers[name]
        self._table(reg)[reg.address:reg.address + reg.width] = encode(reg, value)

    def get(self, name: str) -> float:
        reg = self.registers[name]
        return decode(reg, self._table(reg)[reg.address:reg.address + reg.width])

    def write(self, address: int, raw: int) -> tuple[Register, float]:
        """A Modbus write to one holding register. Only writable setpoints accept it,
        and only within their range, the way device firmware validates setpoints."""
        for reg in self.registers.values():
            if reg.register == "holding" and reg.address == address and reg.writable:
                break
        else:
            raise ModbusException(WRITE_SINGLE_REGISTER, ILLEGAL_DATA_ADDRESS)
        value = decode(reg, (raw,))
        if (reg.minimum is not None and value < reg.minimum) or (reg.maximum is not None and value > reg.maximum):
            raise ModbusException(WRITE_SINGLE_REGISTER, ILLEGAL_DATA_VALUE)
        self.holding[address] = raw
        return reg, value