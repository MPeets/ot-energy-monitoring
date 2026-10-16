"""The collector config against the inventory and the register map: the
contract between what the devices serve and what the collector reads."""
import tomllib
import unittest
from pathlib import Path

from fleet.collector_config import GATEWAY, OMITTED, UNITS, field_name, field_scale, render
from fleet.inventory import load_inventory
from fleet.registers import load_map

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ROOT / "collector" / "telegraf.d" / "devices.conf"


class CollectorConfigTest(unittest.TestCase):
    def setUp(self):
        self.assets = load_inventory(ROOT / "inventory.toml")
        self.config = tomllib.loads(GENERATED.read_text(encoding="utf-8"))

    def test_committed_config_is_up_to_date(self):
        self.assertEqual(
            GENERATED.read_text(encoding="utf-8"), render(self.assets),
            "collector/telegraf.d/devices.conf is stale, run scripts/generate-collector-config.sh",
        )

    def test_every_device_is_read_through_the_gateway_by_its_unit_id(self):
        inputs = {i["alias"]: i for i in self.config["inputs"]["modbus"]}
        self.assertEqual(set(inputs), {a.id for a in self.assets})
        for asset in self.assets:
            with self.subTest(device=asset.id):
                plugin = inputs[asset.id]
                self.assertEqual(plugin["controller"], GATEWAY)
                for request in plugin["request"]:
                    self.assertEqual(request["slave_id"], asset.unit)
                    self.assertEqual(request["tags"], {"device": asset.id, "device_type": asset.type, "site": asset.site})

    def test_every_register_is_read_with_the_maps_address_type_word_order_and_scale(self):
        inputs = {i["alias"]: i for i in self.config["inputs"]["modbus"]}
        for asset in self.assets:
            fields = {
                (request["register"], f["address"]): (request, f)
                for request in inputs[asset.id]["request"]
                for f in request["fields"]
            }
            for reg in load_map(asset.type).registers.values():
                with self.subTest(device=asset.id, register=reg.name):
                    request, f = fields.pop((reg.register, reg.address))
                    self.assertEqual(request["byte_order"], "ABCD")  # high word first, as the map says
                    self.assertEqual((f["name"], f["type"]), (field_name(reg), reg.type))
                    self.assertEqual(f.get("omit", False), reg.name in OMITTED)
                    if not f.get("omit"):
                        self.assertAlmostEqual(f.get("scale", 1.0), reg.scale * UNITS[reg.unit][1])
            self.assertEqual(fields, {}, f"{asset.id} reads registers that aren't in the map")

    def test_metric_names_use_base_units(self):
        turbine = load_map("turbine").registers
        self.assertEqual(field_name(turbine["active_power"]), "active_power_watts")
        self.assertEqual(field_scale(turbine["active_power"]), 1000.0)
        self.assertEqual(field_name(turbine["power_limit"]), "power_limit_ratio")
        self.assertEqual(field_scale(turbine["power_limit"]), 0.001)
        self.assertEqual(field_name(turbine["state"]), "state")

    def test_gather_statistics_get_the_same_site_labels_as_the_readings(self):
        mapping = self.config["processors"]["enum"][0]["mapping"][0]
        self.assertEqual(mapping["value_mappings"], {a.id: a.site for a in self.assets})


class TelegrafConfTest(unittest.TestCase):
    def test_main_config_parses_and_expires_silent_devices(self):
        config = tomllib.loads((ROOT / "collector" / "telegraf.conf").read_text(encoding="utf-8"))
        output = config["outputs"]["prometheus_client"][0]
        self.assertEqual(output["metric_version"], 2)
        self.assertEqual(output["expiration_interval"], "60s")
        self.assertEqual(config["inputs"]["internal"][0]["namepass"], ["internal_gather"])


if __name__ == "__main__":
    unittest.main()