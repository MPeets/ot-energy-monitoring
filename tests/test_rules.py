"""The alert rules and the runbook against the collector: every device metric
they name has to be one the collector actually produces. An alert on a metric
nobody collects doesn't error, it just never fires, so a renamed register
would quietly switch an alert off."""
import re
import unittest
from pathlib import Path

from fleet.collector_config import MEASUREMENT, OMITTED, field_name
from fleet.inventory import load_inventory
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


if __name__ == "__main__":
    unittest.main()