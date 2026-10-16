"""Device models: what each kind of device measures and how it behaves."""
from __future__ import annotations

from ..registers import RegisterBank, RegisterMap
from .base import Device
from .inverter import Inverter
from .turbine import Turbine

# Types without their own model yet use the base one. The rest arrive one commit at a time.
MODELS: dict[str, type[Device]] = {
    "turbine": Turbine,
    "inverter": Inverter,
    "battery": Device,
    "substation": Device,
}


def create(
    device_type: str, device_id: str, bank: RegisterBank, register_map: RegisterMap, site: str | None = None
) -> Device:
    return MODELS[device_type](device_id, bank, register_map, site)