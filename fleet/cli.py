"""Engineering workstation tool for talking to devices over Modbus TCP.

  python -m fleet.cli dump wt-01                    every register, decoded by name
  python -m fleet.cli read wt-01 input 0 4          raw registers
  python -m fleet.cli write wt-02 power_limit 50    change a setpoint, in engineering units
"""
from __future__ import annotations

import argparse
import math
import struct
import sys

from . import modbus
from .registers import RegisterMap, decode, device_type_for_code, encode, load_map

FUNCTIONS = {"input": modbus.READ_INPUT_REGISTERS, "holding": modbus.READ_HOLDING_REGISTERS}


def format_value(reg, value: float) -> str:
    if reg.type == "FLOAT32":
        return f"{value:.2f}"
    decimals = max(0, -math.floor(math.log10(reg.scale)))
    return f"{value:.{decimals}f}"


def identify(host: str, connection: dict) -> RegisterMap:
    """The identity block says which register map the rest of the device follows."""
    identity = modbus.read_registers(host, modbus.READ_INPUT_REGISTERS, 0, 4, **connection)
    register_map = load_map(device_type_for_code(identity[2]))
    print(f"{host}: {register_map.device_type}, register map v{identity[3]}")
    if identity[3] != register_map.version:
        print(f"warning: this tool knows register map v{register_map.version}, values may be wrong")
    return register_map


def dump(args) -> None:
    connection = {"port": args.port, "unit": args.unit}
    register_map = identify(args.host, connection)
    for reg in sorted(register_map.registers.values(), key=lambda r: (r.register, r.address)):
        words = modbus.read_registers(args.host, FUNCTIONS[reg.register], reg.address, reg.width, **connection)
        value = decode(reg, words)
        label = f"({reg.values.get(str(int(value)), 'unknown')})" if reg.values else reg.unit
        if reg.writable:
            label += "  [writable]"
        print(f"  {reg.register:<8} {reg.address:>3}  {reg.name:<24} {format_value(reg, value):>12} {label}".rstrip())


def read(args) -> None:
    words = modbus.read_registers(
        args.host, FUNCTIONS[args.table], args.start, args.count, port=args.port, unit=args.unit
    )
    for offset, word in enumerate(words):
        print(f"  {args.table:<8} {args.start + offset:>3}  {word:>5}  {word:#06x}")


def write(args) -> None:
    connection = {"port": args.port, "unit": args.unit}
    register_map = identify(args.host, connection)
    reg = register_map.registers.get(args.name)
    if reg is None or not reg.writable:
        writable = sorted(r.name for r in register_map.registers.values() if r.writable)
        raise ValueError(f"{args.name!r} is not a writable register on this device, writable: {writable}")
    (raw,) = encode(reg, args.value)
    modbus.transact(args.host, struct.pack(">BHH", modbus.WRITE_SINGLE_REGISTER, reg.address, raw), **connection)
    print(f"  {reg.name} = {format_value(reg, decode(reg, (raw,)))} {reg.unit}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="fleet.cli", description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit", type=int, default=1, help="Modbus unit id")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("dump", help="read and decode every register of a device")
    p.add_argument("host")
    p.set_defaults(run=dump)

    p = commands.add_parser("read", help="read raw registers")
    p.add_argument("host")
    p.add_argument("table", choices=sorted(FUNCTIONS))
    p.add_argument("start", type=int)
    p.add_argument("count", type=int)
    p.set_defaults(run=read)

    p = commands.add_parser("write", help="write a setpoint by name, in engineering units")
    p.add_argument("host")
    p.add_argument("name")
    p.add_argument("value", type=float)
    p.set_defaults(run=write)

    args = parser.parse_args()
    try:
        args.run(args)
    except modbus.ModbusException as exc:
        print(f"{args.host}: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"{args.host}: cannot reach device: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"{args.host}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())