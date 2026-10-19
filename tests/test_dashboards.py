"""The dashboards against the rest of the lab: every query has to use metrics
the collector produces or the rules record, and the state colours have to
cover every state the register map defines. A panel on a renamed metric just
goes blank, and a state without a colour shows up as a bare number."""
import json
import re
import unittest
from pathlib import Path

from fleet.registers import load_map
from tests.test_rules import METRIC, RULES, collected_metrics

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS = sorted((ROOT / "grafana" / "dashboards").glob("*.json"))

# Recording rules, e.g. device:state, and the series Prometheus makes itself
RECORDED = re.compile(r"\b[a-z_]+:[a-z_0-9]+(?::[a-z_0-9]+)?\b")
BUILT_IN = {"ALERTS", "internal_gather_metrics_gathered"}
NO_DATA = 99  # the state device:state gives a silent unit


def recorded_names() -> set[str]:
    names = set()
    for path in RULES.glob("*.yml"):
        names |= set(re.findall(r"^\s*- record: (\S+)", path.read_text(encoding="utf-8"), re.MULTILINE))
    return names


def panels(dashboard: dict):
    for panel in dashboard["panels"]:
        yield panel
        yield from panel.get("panels", [])  # collapsed rows


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DASHBOARDS, "no dashboards found")
        self.dashboards = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in DASHBOARDS}

    def test_dashboards_have_unique_uids_and_panel_ids(self):
        uids = [d["uid"] for d in self.dashboards.values()]
        self.assertEqual(len(uids), len(set(uids)))
        for name, dashboard in self.dashboards.items():
            ids = [p["id"] for p in panels(dashboard)]
            self.assertEqual(len(ids), len(set(ids)), name)

    def test_every_panel_queries_the_provisioned_prometheus(self):
        for name, dashboard in self.dashboards.items():
            for panel in panels(dashboard):
                if panel.get("type") == "row":
                    continue
                with self.subTest(dashboard=name, panel=panel["title"]):
                    self.assertEqual(panel["datasource"]["uid"], "prometheus")
                    self.assertTrue(panel["targets"])

    def test_every_query_uses_collected_or_recorded_series(self):
        collected, recorded = collected_metrics(), recorded_names()
        for name, dashboard in self.dashboards.items():
            for panel in panels(dashboard):
                for target in panel.get("targets", []):
                    expr = target["expr"]
                    with self.subTest(dashboard=name, panel=panel["title"], query=target["refId"]):
                        for metric in METRIC.findall(expr):
                            self.assertTrue(metric in collected, f"{metric} isn't collected")
                        for rule in RECORDED.findall(expr):
                            self.assertTrue(rule in recorded, f"{rule} isn't a recording rule")
                        names = set(re.findall(r"(?<![\w:])[A-Za-z_]\w*(?=\s*\{)", expr))
                        unknown = names - collected - BUILT_IN
                        self.assertFalse(unknown, f"unknown series {unknown}")

    def test_state_colours_cover_every_state_in_the_register_map(self):
        timelines = [p for d in self.dashboards.values() for p in panels(d) if p["type"] == "state-timeline"]
        self.assertTrue(timelines)
        for panel in timelines:
            overrides = {o["matcher"]["options"]: o for o in panel["fieldConfig"]["overrides"]
                         if o["matcher"]["id"] == "byFrameRefID"}
            for target in panel["targets"]:
                device_type = re.search(r'device_type="(\w+)"', target["expr"]).group(1)
                with self.subTest(panel=panel["title"], device_type=device_type):
                    expected = dict(load_map(device_type).registers["state"].values)
                    expected[str(NO_DATA)] = "no data"
                    mappings = overrides[target["refId"]]["properties"][0]["value"][0]["options"]
                    self.assertEqual({k: v["text"] for k, v in mappings.items()}, expected)

    def test_no_data_state_matches_the_recording_rule(self):
        self.assertIn(f"* 0 + {NO_DATA}", (RULES / "comms.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()