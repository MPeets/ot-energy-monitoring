"""The scenario script and the smoke test against the rest of the lab.

A scenario file with a key the device doesn't know only gets a line in the
device log, so the scenario quietly does nothing. An expectation naming an
alert or receiver that doesn't exist sends whoever runs it looking for a
notification that will never arrive. Both are easy to get wrong by hand."""
import json
import re
import unittest
from pathlib import Path

from fleet.inventory import load_inventory
from fleet.models import MODELS
from fleet.registers import load_map
from fleet.scenarios import GRID_KEYS

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_SH = (ROOT / "scripts" / "scenario.sh").read_text(encoding="utf-8")
SMOKE_TEST_SH = (ROOT / "scripts" / "smoke-test.sh").read_text(encoding="utf-8")
DEVICES = {asset.id: asset.type for asset in load_inventory(ROOT / "inventory.toml")}


def alert_names() -> set[str]:
    rules = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "prometheus" / "rules").glob("*.yml"))
    return set(re.findall(r"^\s*- alert: (\w+)", rules, re.MULTILINE))


def receiver_names() -> set[str]:
    config = (ROOT / "alertmanager" / "alertmanager.yml").read_text(encoding="utf-8")
    return set(re.findall(r"^\s*- name: (\w+)", config.split("\nreceivers:")[1], re.MULTILINE))


def accepted_keys(file: str) -> dict:
    return GRID_KEYS if file == "grid" else MODELS[DEVICES[file]].SCENARIO_KEYS


def check_condition(test: unittest.TestCase, file: str, text: str) -> None:
    test.assertTrue(file == "grid" or file in DEVICES, f"no device {file}")
    keys = accepted_keys(file)
    for key, value in json.loads(text).items():
        test.assertIn(key, keys)
        test.assertTrue(keys[key](value), f"{key}: {value!r} isn't valid")


class ScenarioScriptTest(unittest.TestCase):
    def test_every_listed_scenario_has_a_function_and_every_function_is_listed(self):
        listing = SCENARIO_SH.split("SCENARIOS=(")[1].split("\n)")[0]
        listed = set(re.findall(r'^\s+"([a-z-]+) ', listing, re.MULTILINE))
        functions = {name.replace("_", "-") for name in re.findall(r"^scenario_(\w+)\(\)", SCENARIO_SH, re.MULTILINE)}
        self.assertTrue(listed)
        self.assertEqual(listed, functions)

    def test_scenario_files_only_use_keys_the_devices_accept(self):
        calls = re.findall(r"^\s+world ([\w-]+) '(.+)'$", SCENARIO_SH, re.MULTILINE)
        self.assertTrue(calls)
        for file, text in calls:
            with self.subTest(file=file, json=text):
                check_condition(self, file, text)

    def test_setpoints_are_writable_registers(self):
        calls = re.findall(r"^\s+setpoints ((?:[\w-]+ \w+ [\d.]+ ?)+)$", SCENARIO_SH, re.MULTILINE)
        self.assertTrue(calls)
        for call in calls:
            words = call.split()
            for device, register in zip(words[0::3], words[1::3]):
                with self.subTest(device=device, register=register):
                    self.assertTrue(load_map(DEVICES[device]).registers[register].writable)

    def test_reset_lifts_the_limit_on_every_type_that_has_one(self):
        types = set(re.search(r'/\^type = "\(([\w|]+)\)"/', SCENARIO_SH).group(1).split("|"))
        with_limit = {t for t in MODELS if "power_limit" in load_map(t).registers}
        self.assertEqual(types, with_limit)

    def test_silenced_services_are_devices(self):
        for call in re.findall(r"^\s+silence (.+)$", SCENARIO_SH, re.MULTILINE):
            for service in call.split():
                self.assertIn(service, DEVICES)

    def test_expected_alerts_and_receivers_exist(self):
        expectations = "".join(re.findall(r"<<'EOF'\n(.*?)\nEOF", SCENARIO_SH, re.DOTALL))
        mentioned = set(re.findall(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b", expectations))
        self.assertTrue(mentioned)
        self.assertLessEqual(mentioned, alert_names())
        self.assertLessEqual(set(re.findall(r"\[(\w+)\] FIRING", expectations)), receiver_names())


class SmokeTestScriptTest(unittest.TestCase):
    def test_its_fault_is_a_valid_scenario_that_the_inverter_fault_alert_catches(self):
        text, file = re.search(r"printf '%s\\n' '(\{.*\})' > scenarios/([\w-]+)\.json", SMOKE_TEST_SH).groups()
        check_condition(self, file, text)
        threshold = re.search(r'fleet_fault_code\{device_type="inverter"\} >= (\d+)\n',
                              (ROOT / "prometheus" / "rules" / "generation.yml").read_text(encoding="utf-8"))
        self.assertGreaterEqual(json.loads(text)["fault_code"], int(threshold.group(1)))

    def test_the_notifications_it_waits_for_name_real_receivers_and_alerts(self):
        waits = re.findall(r"\\\[(\w+)\\\] (?:FIRING|RESOLVED) \+(\w+)", SMOKE_TEST_SH)
        self.assertTrue(waits)
        for receiver, alert in waits:
            self.assertIn(receiver, receiver_names())
            self.assertIn(alert, alert_names())


class ScenarioDocsTest(unittest.TestCase):
    def test_every_key_a_device_accepts_is_documented(self):
        readme = (ROOT / "scenarios" / "README.md").read_text(encoding="utf-8")
        sections = {s.split("\n", 1)[0]: s for s in readme.split("\n## ")[1:]}

        def documented(title: str) -> set[str]:
            return set(re.findall(r"^\| `(\w+)` \|", sections[title], re.MULTILINE))

        titles = {"turbine": "Turbines (wt-*)", "inverter": "Solar inverters (inv-*)",
                  "battery": "Battery (bess-01)", "substation": "Substation (sub-01)"}
        self.assertEqual(set(GRID_KEYS), documented("grid.json"))
        for device_type, title in titles.items():
            with self.subTest(device_type=device_type):
                self.assertEqual(set(MODELS[device_type].SCENARIO_KEYS), documented("Any device") | documented(title))


if __name__ == "__main__":
    unittest.main()