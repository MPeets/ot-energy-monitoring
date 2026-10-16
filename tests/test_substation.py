import math
import unittest

from fleet import modbus, models
from fleet.models.substation import FeederPoller, LOSSES, OIL_TIME_CONSTANT_S, top_oil_rise
from fleet.registers import RegisterBank, load_map
from fleet.scenarios import Scenario

NOW = 1_800_000_000


class FakePoller(FeederPoller):
    def __init__(self, total):
        super().__init__([])
        self.total = total

    def total_kw(self):
        return self.total


def make_substation(fleet_kw: float = 0.0):
    register_map = load_map("substation")
    bank = RegisterBank(register_map.registers)
    device = models.create("substation", "sub-01", bank, register_map, "sub-d")
    device.poller = FakePoller(fleet_kw)
    return device, bank


def run_for(device, seconds: int, **conditions):
    for t in range(seconds):
        device.update(NOW + t, Scenario(device=conditions))


class SubstationTest(unittest.TestCase):
    def test_reports_on_the_110_kv_side(self):
        _, bank = make_substation()
        self.assertAlmostEqual(bank.get("voltage_nominal"), 110_000 / math.sqrt(3), delta=1)

    def test_exports_the_fleet_output_less_losses(self):
        device, bank = make_substation(12_000)
        device.update(NOW, Scenario())
        self.assertAlmostEqual(bank.get("active_power"), 12_000 * (1 - LOSSES), delta=1)
        self.assertAlmostEqual(bank.get("transformer_load"), 100 * 12_000 * (1 - LOSSES) / 25_000, delta=0.5)

    def test_oil_settles_at_ambient_plus_the_rise_for_its_load(self):
        device, bank = make_substation(20_000)
        run_for(device, 6 * OIL_TIME_CONSTANT_S, ambient_temperature=10.0)
        expected = 10.0 + top_oil_rise(20_000 * (1 - LOSSES) / 25_000)
        self.assertAlmostEqual(bank.get("oil_temperature"), expected, delta=1.0)
        self.assertLess(bank.get("oil_temperature"), 80)  # healthy at 80 % load

    def test_failed_cooling_at_full_load_runs_hot_within_minutes(self):
        device, bank = make_substation()
        run_for(device, 60, load_kw=25_000, ambient_temperature=10.0)
        run_for(device, 900, load_kw=25_000, ambient_temperature=10.0, cooling_failure=True)
        self.assertGreater(bank.get("oil_temperature"), 85)

    def test_failed_cooling_at_low_load_is_not_an_emergency(self):
        # Same fault, quiet night: the oil stays well within limits. Alerting on
        # temperature rather than on fan status gets this right.
        device, bank = make_substation()
        run_for(device, 6 * OIL_TIME_CONSTANT_S, load_kw=5_000, ambient_temperature=10.0, cooling_failure=True)
        self.assertLess(bank.get("oil_temperature"), 60)


class FeederPollerTest(unittest.TestCase):
    def test_sums_feeders_and_keeps_the_last_value_of_one_that_goes_quiet(self):
        readings = {"wt-01": 3000.0, "inv-01": 1500.0}

        def read(host):
            if host not in readings:
                raise OSError("no route")
            return readings[host]

        poller = FeederPoller(["wt-01", "inv-01"], read=read)
        poller.poll_once()
        self.assertEqual(poller.total_kw(), 4500.0)
        del readings["wt-01"]  # stops answering
        poller.poll_once()
        self.assertEqual(poller.total_kw(), 4500.0)

    def test_a_feeder_answering_with_a_modbus_exception_is_skipped(self):
        def read(host):
            raise modbus.ModbusException(modbus.READ_INPUT_REGISTERS, modbus.ILLEGAL_DATA_ADDRESS)

        poller = FeederPoller(["wt-01"], read=read)
        poller.poll_once()
        self.assertEqual(poller.total_kw(), 0)


if __name__ == "__main__":
    unittest.main()