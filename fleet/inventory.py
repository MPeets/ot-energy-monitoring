"""The asset list in inventory.toml, checked before anything uses it.

A wrong unit id here means the gateway routes requests to the wrong device,
and monitoring reports one turbine's numbers under another's name. So
duplicates and typos stop startup instead of producing plausible nonsense.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .registers import MAP_FILE

DEFAULT_PATH = Path("inventory.toml")


@dataclass(frozen=True)
class Asset:
    id: str
    unit: int
    site: str
    type: str
    host: str = ""  # where the gateway finds it on the OT network, defaults to id
    port: int = 502

    @property
    def address(self) -> tuple[str, int]:
        return (self.host or self.id, self.port)


def load_inventory(path: str | Path = DEFAULT_PATH) -> list[Asset]:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    known_types = tomllib.loads(MAP_FILE.read_text(encoding="utf-8"))["device_types"]
    assets = [Asset(**entry) for entry in data.get("device", [])]

    seen_ids, seen_units = set(), set()
    for asset in assets:
        if asset.type not in known_types:
            raise ValueError(f"{asset.id}: unknown type {asset.type!r}, expected one of {sorted(known_types)}")
        if not 1 <= asset.unit <= 247:
            raise ValueError(f"{asset.id}: unit id {asset.unit} outside 1-247")
        if asset.id in seen_ids:
            raise ValueError(f"duplicate device id {asset.id!r}")
        if asset.unit in seen_units:
            raise ValueError(f"{asset.id}: unit id {asset.unit} is already taken")
        seen_ids.add(asset.id)
        seen_units.add(asset.unit)
    if not assets:
        raise ValueError(f"{path} lists no devices")
    return assets