import math
import tempfile
import unittest
from pathlib import Path

from fleet import environment, models
from fleet.registers import RegisterBank, load_map
from fleet.scenarios import Scenario, ScenarioWatcher

DAY = 24 * 3600
NOW = 1_800_000_000  # a fixed moment, so the tests don't depend on when they run


def make_device(device_type: str = "turbine", device_id: str = "wt-01"):
    register_map = load_map(device_type)
    bank = RegisterBank(register_map.registers)
    return models.create(device_type, device_id, bank, register_map), bank


class GridTest(unittest.TestCase):
    def test_normal_frequency_stays_inside_the_standard_range_all_day(self):
        # The quiet state must be quiet, or the alerts built on it are noise.
        worst = max(abs(environment.grid_frequency(NOW + t) - 50.0) for t in range(0, DAY, 10))
        self.assertLess(worst, 0.050)

    def test_every_device_sees_the_same_grid_frequency(self):
        a, bank_a = make_device("turbine", "wt-01")
        b, bank_b = make_device("substation", "sub-01")
        a.update(NOW, Scenario())
        b.update(NOW, Scenario())
        # Only meter noise (1 mHz each) and register resolution may separate them
        self.assertLessEqual(abs(bank_a.get("frequency") - bank_b.get("frequency")), 0.003)

    def test_normal_voltages_stay_within_two_percent_all_day(self):
        for t in range(0, DAY, 60):
            for pu in environment.phase_voltages_pu("wt-01", NOW + t):
                self.assertLess(abs(pu - 1.0), 0.02)

    def test_phases_are_slightly_unbalanced_like_a_real_feeder(self):
        l1, l2, l3 = environment.phase_voltages_pu("wt-01", NOW)
        self.assertGreater(max(l1, l2, l3) - min(l1, l2, l3), 0.0001)

    def test_noise_is_continuous(self):
        steps = [abs(environment.noise("k", t + 1, 15) - environment.noise("k", t, 15)) for t in range(0, 600)]
        self.assertLess(max(steps), 0.25)


class DeviceTest(unittest.TestCase):
    def test_registers_hold_grid_values_after_an_update(self):
        device, bank = make_device()
        device.update(NOW, Scenario())
        self.assertAlmostEqual(bank.get("frequency"), 50.0, delta=0.05)
        nominal = 690 / math.sqrt(3)
        self.assertAlmostEqual(bank.get("voltage_nominal"), nominal, places=2)
        for phase in ("l1", "l2", "l3"):
            self.assertAlmostEqual(bank.get(f"voltage_{phase}") / nominal, 1.0, delta=0.02)

    def test_grid_and_device_frequency_offsets_add_up(self):
        device, bank = make_device()
        device.update(NOW, Scenario())
        normal = bank.get("frequency")
        device.update(NOW, Scenario(grid={"frequency_offset_hz": -0.25}, device={"frequency_offset_hz": 0.05}))
        self.assertAlmostEqual(bank.get("frequency") - normal, -0.20, delta=0.002)

    def test_forced_phase_voltage_leaves_the_other_phases_alone(self):
        device, bank = make_device()
        device.update(NOW, Scenario(device={"voltage_pu": {"l2": 0.85}}))
        nominal = bank.get("voltage_nominal")
        self.assertAlmostEqual(bank.get("voltage_l2") / nominal, 0.85, places=4)
        self.assertAlmostEqual(bank.get("voltage_l1") / nominal, 1.0, delta=0.02)


class ScenarioWatcherTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.log = []
        device, _ = make_device()
        self.watcher = ScenarioWatcher(self.directory, "wt-01", device.SCENARIO_KEYS, self.log.append)

    def write(self, name: str, text: str, encoding: str = "utf-8"):
        (self.directory / name).write_text(text, encoding=encoding)

    def test_no_files_means_no_scenario_and_no_log_noise(self):
        scenario = self.watcher.poll()
        self.assertEqual((scenario.grid, scenario.device), ({}, {}))
        self.assertEqual(self.log, [])

    def test_grid_and_device_files_are_read_separately(self):
        self.write("grid.json", '{"frequency_offset_hz": -0.3}')
        self.write("wt-01.json", '{"voltage_pu": {"l1": 1.12}}')
        self.write("wt-02.json", '{"frequency_offset_hz": 1}')  # another device's file
        scenario = self.watcher.poll()
        self.assertEqual(scenario.grid, {"frequency_offset_hz": -0.3})
        self.assertEqual(scenario.device, {"voltage_pu": {"l1": 1.12}})

    def test_deleting_a_file_clears_it(self):
        self.write("grid.json", '{"frequency_offset_hz": -0.3}')
        self.watcher.poll()
        (self.directory / "grid.json").unlink()
        self.assertEqual(self.watcher.poll().grid, {})
        self.assertIn("scenario grid.json: cleared", self.log)

    def test_broken_json_keeps_the_previous_scenario(self):
        self.write("grid.json", '{"frequency_offset_hz": -0.3}')
        self.watcher.poll()
        self.write("grid.json", '{"frequency_offset_hz": ')
        self.assertEqual(self.watcher.poll().grid, {"frequency_offset_hz": -0.3})
        self.assertTrue(any("not valid UTF-8 JSON" in line for line in self.log))

    def test_typos_and_bad_values_are_logged_not_applied(self):
        self.write("wt-01.json", '{"frequncy_offset_hz": 1, "voltage_pu": {"l4": 1.0}, "frequency_offset_hz": 0.1}')
        self.assertEqual(self.watcher.poll().device, {"frequency_offset_hz": 0.1})
        self.assertTrue(any("unknown key 'frequncy_offset_hz'" in line for line in self.log))
        self.assertTrue(any("invalid value for 'voltage_pu'" in line for line in self.log))

    def test_grid_file_only_accepts_grid_keys(self):
        self.write("grid.json", '{"voltage_pu": {"l1": 0.5}}')
        self.assertEqual(self.watcher.poll().grid, {})

    def test_unreadable_encoding_is_reported_once(self):
        self.write("grid.json", '{"frequency_offset_hz": 0.2}', encoding="utf-16")
        for _ in range(3):
            self.assertEqual(self.watcher.poll().grid, {})
        self.assertEqual(sum("not valid UTF-8 JSON" in line for line in self.log), 1)

    def test_byte_order_mark_from_windows_editors_is_fine(self):
        self.write("grid.json", '{"frequency_offset_hz": 0.2}', encoding="utf-8-sig")
        self.assertEqual(self.watcher.poll().grid, {"frequency_offset_hz": 0.2})


if __name__ == "__main__":
    unittest.main()