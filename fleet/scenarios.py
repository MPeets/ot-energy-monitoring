"""Scenario files: how the lab injects conditions into the simulated world.

Each device watches a directory mounted read-only from the host:

  grid.json       conditions every device sees, e.g. a frequency event
  <device>.json   conditions for one device, e.g. a fault or a bad meter

Edit a file and the device picks it up within a second. Delete it and the
device goes back to normal. Keys each file accepts are in scenarios/README.md.

This is the lab's stand-in for the physical world. It's a file on the host,
not a network service, so injecting a fault doesn't open any path into OT.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def phases(value) -> bool:
    return isinstance(value, dict) and set(value) <= {"l1", "l2", "l3"} and all(map(number, value.values()))


# What grid.json accepts. Each device type declares its own keys.
GRID_KEYS = {"frequency_offset_hz": number}


@dataclass
class Scenario:
    grid: dict = field(default_factory=dict)
    device: dict = field(default_factory=dict)


class ScenarioWatcher:
    def __init__(self, directory: str | Path, device_id: str, device_keys: dict, log=print):
        directory = Path(directory)
        self.files = {
            "grid": (directory / "grid.json", GRID_KEYS),
            "device": (directory / f"{device_id}.json", device_keys),
        }
        self.seen = {"grid": None, "device": None}
        self.current = Scenario()
        self.log = log

    def poll(self) -> Scenario:
        """Re-reads the files. Invalid content is logged and the previous scenario kept."""
        for kind, (path, schema) in self.files.items():
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                data = b""
            except OSError as exc:
                self.log(f"cannot read {path.name}: {exc}")
                continue
            if data == self.seen[kind]:
                continue
            first_read = self.seen[kind] is None
            self.seen[kind] = data

            try:
                # utf-8-sig tolerates the byte order mark some Windows editors add
                values = json.loads(data.decode("utf-8-sig")) if data.strip() else {}
            except ValueError as exc:
                self.log(f"{path.name} is not valid UTF-8 JSON ({exc}), keeping the previous scenario")
                continue
            if not isinstance(values, dict):
                self.log(f"{path.name} must contain a JSON object, keeping the previous scenario")
                continue

            accepted = {}
            for key, value in values.items():
                if key not in schema:
                    self.log(f"{path.name}: unknown key {key!r} ignored, expected one of {sorted(schema)}")
                elif not schema[key](value):
                    self.log(f"{path.name}: invalid value for {key!r}: {value!r}")
                else:
                    accepted[key] = value
            setattr(self.current, kind, accepted)
            if accepted or not first_read:
                self.log(f"scenario {path.name}: {accepted or 'cleared'}")
        return self.current