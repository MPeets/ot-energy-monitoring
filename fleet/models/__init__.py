"""Device models: what each kind of device measures and how it behaves."""
from __future__ import annotations

from ..registers import RegisterBank, RegisterMap
from .base import Device

# Every type uses the base model for now. The specific models arrive one commit at a time.
MODELS: dict[str, type[Device]] = {
    "turbine": Device,
    "inverter": Device,
    "battery": Device,
    "substation": Device,
}


def create(device_type: str, device_id: str, bank: RegisterBank, register_map: RegisterMap) -> Device:
    return MODELS[device_type](device_id, bank, register_map)