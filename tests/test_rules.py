"""The alert rules against the rest of the lab. Every device metric the rules
and the runbook name has to be one the collector produces: an alert on a
metric nobody collects doesn't error, it just never fires, so a renamed
register would quietly switch an alert off. And the expected-power maths and
fault names have to match the simulated devices."""
import re
import unittest
from pathlib import Path

from fleet.collector_config import MEASUREMENT, OMITTED, field_name
from fleet.inventory import load_inventory
from fleet.models import inverter, turbine
from fleet.registers import load_map

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "prometheus" / "rules"
RUNBOOK = ROOT / "docs" / "runbook.md"

# fleet_frequency_hertz, but not fleet:frequency_hertz:median (a recording
# rule) or power-fleet_dmz-it (a Docker network)
METRIC = re.compile(rf"(?<![\w-]){MEASUREMENT}_[a-z0-9_]+")


def collected_metrics() -> set[str]:
    device_types = {asset.type for asset in load_inventory(ROOT / "inventory.toml")}
    return {
        f"{MEASUREMENT}_{field_name(reg)}"
        for device_type in device_types
        for reg in load_map(device_type).registers.values()
        if reg.name not in OMITTED
    }


class RulesTest(unittest.TestCase):
    def test_every_device_metric_in_the_rules_and_runbook_is_collected(self):
        rule_files = sorted(RULES.glob("*.yml"))
        self.assertTrue(rule_files, f"no rule files in {RULES}")
        collected = collected_metrics()
        for path in [*rule_files, RUNBOOK]:
            for name in sorted(set(METRIC.findall(path.read_text(encoding="utf-8")))):
                with self.subTest(file=path.name, metric=name):
                    self.assertTrue(name in collected, f"{path.name} uses {name}, which the collector doesn't produce")

    def test_expected_power_uses_the_same_curve_and_ratio_as_the_devices(self):
        # In a real fleet both sides would read the manufacturer's data. Here
        # the simulator and the alert rules each have a copy, so they're pinned.
        rules = (RULES / "generation.yml").read_text(encoding="utf-8")
        cut_in, rated, cut_out = (f"{v:g}" for v in (turbine.CUT_IN_MS, turbine.RATED_WIND_MS, turbine.CUT_OUT_MS))
        for expected in (
            f"^ 3 - {cut_in} ^ 3) / ({rated} ^ 3 - {cut_in} ^ 3)",  # turbine power curve
            f"< bool {cut_out})",                                     # cut-out wind
            f"/ 1000 * {inverter.PERFORMANCE_RATIO:g} *",             # solar performance ratio
        ):
            with self.subTest(expected=expected):
                self.assertTrue(expected in rules, f"generation.yml doesn't contain {expected!r}")

    def test_every_inverter_fault_code_is_named_in_its_alert(self):
        rules = (RULES / "generation.yml").read_text(encoding="utf-8").replace("else if", "if")
        codes = load_map("inverter").registers["fault_code"].values
        for code, name in codes.items():
            if code != "0":
                with self.subTest(code=code):
                    expected = f"{{{{ if eq $value {code}.0 }}}}{name}"
                    self.assertTrue(expected in rules, f"generation.yml doesn't name code {code} as {name!r}")


if __name__ == "__main__":
    unittest.main()