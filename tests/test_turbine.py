import struct
import unittest

from fleet import environment, modbus, models
from fleet.models import turbine
from fleet.models.turbine import CURTAILED, FAULT, LOW_WIND, MAINTENANCE, PRODUCING, STORM_STOP, power_curve
from fleet.registers import RegisterBank, load_map
from fleet.scenarios import Scenario

NOW = 1_800_000_000
RATED = 3600


def make_turbine(device_id: str = "wt-01", site: str = "wind-a"):
    register_map = load_map("turbine")
    bank = RegisterBank(register_map.registers)
    return models.create("turbine", device_id, bank, register_map, site), bank


def run(device, bank, **conditions):
    device.update(NOW, Scenario(device=conditions))
    return bank.get("state"), bank.get("active_power")


def set_limit(bank, percent: float) -> bytes:
    """Writes the power limit the way the park controller does: Modbus function 06."""
    return modbus.handle_request(struct.pack(">BHH", 0x06, 0, round(percent * 10)), bank)


class PowerCurveTest(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(power_curve(2.9, RATED), 0)
        self.assertEqual(power_curve(3.0, RATED), 0)
        self.assertAlmostEqual(power_curve(8.0, RATED), RATED * (512 - 27) / (1728 - 27))
        self.assertEqual(power_curve(12.0, RATED), RATED)
        self.assertEqual(power_curve(24.9, RATED), RATED)
        self.assertEqual(power_curve(25.0, RATED), 0)

    def test_more_wind_never_means_less_power_below_cut_out(self):
        powers = [power_curve(v / 10, RATED) for v in range(0, 250)]
        self.assertEqual(powers, sorted(powers))


class TurbineStateTest(unittest.TestCase):
    def setUp(self):
        self.device, self.bank = make_turbine()

    def test_reports_its_nameplate_and_no_limit_at_start(self):
        self.assertEqual(self.bank.get("rated_power"), RATED)
        self.assertAlmostEqual(self.bank.get("power_limit"), 100.0)

    def test_produces_along_the_curve(self):
        state, power = run(self.device, self.bank, wind_speed=8.0)
        self.assertEqual(state, PRODUCING)
        self.assertAlmostEqual(power, power_curve(8.0, RATED), delta=1)

    def test_low_wind_is_zero_output_but_not_a_fault(self):
        self.assertEqual(run(self.device, self.bank, wind_speed=2.0), (LOW_WIND, 0))

    def test_curtailed_to_zero_is_its_own_state(self):
        self.assertEqual(set_limit(self.bank, 0)[0], 0x06)
        state, power = run(self.device, self.bank, wind_speed=14.0)
        self.assertEqual((state, power), (CURTAILED, 0))
        self.assertAlmostEqual(self.bank.get("wind_speed"), 14.0)
        self.assertEqual(self.bank.get("rotor_speed"), 2.0)  # idling, not braked

    def test_partial_curtailment_caps_output(self):
        set_limit(self.bank, 50)
        self.assertEqual(run(self.device, self.bank, wind_speed=14.0), (CURTAILED, RATED / 2))

    def test_limit_above_what_the_wind_gives_is_not_curtailment(self):
        set_limit(self.bank, 50)
        state, power = run(self.device, self.bank, wind_speed=6.0)
        self.assertEqual(state, PRODUCING)
        self.assertLess(power, RATED / 2)

    def test_out_of_range_limit_is_refused_by_the_device(self):
        self.assertEqual(set_limit(self.bank, 150), bytes([0x86, 0x03]))
        self.assertAlmostEqual(self.bank.get("power_limit"), 100.0)

    def test_fault_and_maintenance_stop_production(self):
        self.assertEqual(run(self.device, self.bank, wind_speed=10.0, fault=True), (FAULT, 0))
        self.assertEqual(run(self.device, self.bank, wind_speed=10.0, maintenance=True), (MAINTENANCE, 0))
        self.assertEqual(self.bank.get("rotor_speed"), 0)

    def test_storm_stop_waits_for_the_wind_to_drop_before_restarting(self):
        self.assertEqual(run(self.device, self.bank, wind_speed=26.0), (STORM_STOP, 0))
        self.assertEqual(run(self.device, self.bank, wind_speed=23.0), (STORM_STOP, 0))
        self.assertEqual(run(self.device, self.bank, wind_speed=21.0), (PRODUCING, RATED))

    def test_degraded_turbine_still_says_producing(self):
        # Iced blades or a pitch problem: no fault code, just less power than
        # the wind should give. Only a comparison against the curve catches it.
        state, power = run(self.device, self.bank, wind_speed=9.0, performance=0.6)
        self.assertEqual(state, PRODUCING)
        self.assertAlmostEqual(power, 0.6 * power_curve(9.0, RATED), delta=1)


class WindTest(unittest.TestCase):
    def test_turbines_in_one_park_see_similar_wind(self):
        a, bank_a = make_turbine("wt-01")
        b, bank_b = make_turbine("wt-02")
        for t in range(0, 6 * 3600, 30):
            a.update(NOW + t, Scenario())
            b.update(NOW + t, Scenario())
            self.assertLess(abs(bank_a.get("wind_speed") - bank_b.get("wind_speed")), 1.3)

    def test_normal_wind_mostly_produces_and_sometimes_reaches_rated(self):
        winds = [environment.wind_speed("wind-a", NOW + t) for t in range(0, 3 * 24 * 3600, 60)]
        self.assertGreater(min(winds), turbine.CUT_IN_MS)
        self.assertLess(max(winds), turbine.RESTART_MS)
        self.assertTrue(any(w > turbine.RATED_WIND_MS for w in winds))


class RegisterMapContractTest(unittest.TestCase):
    def test_state_labels_in_the_map_match_the_model(self):
        labels = load_map("turbine").registers["state"].values
        expected = {
            PRODUCING: "producing", LOW_WIND: "low wind", CURTAILED: "curtailed",
            STORM_STOP: "storm stop", FAULT: "fault", MAINTENANCE: "maintenance",
        }
        self.assertEqual(labels, {str(code): label for code, label in expected.items()})


if __name__ == "__main__":
    unittest.main()