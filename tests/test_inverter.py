import struct
import unittest
from datetime import datetime, timezone

from fleet import environment, modbus, models
from fleet.models.base import GRID_FREQUENCY, GRID_UNDERVOLTAGE, RECONNECT_DELAY_S, GridProtection
from fleet.models.inverter import (
    CURTAILED, FAULT, FAULT_CODES, MAINTENANCE, PRODUCING, STANDBY, expected_power,
)
from fleet.registers import RegisterBank, load_map
from fleet.scenarios import Scenario

NOW = 1_800_000_000
RATED, DC = 2500, 3000


def utc(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def make_inverter(device_id: str = "inv-01", site: str = "solar-b"):
    register_map = load_map("inverter")
    bank = RegisterBank(register_map.registers)
    return models.create("inverter", device_id, bank, register_map, site), bank


def run(device, bank, now=NOW, **conditions):
    device.update(now, Scenario(device=conditions))
    return bank.get("state"), bank.get("active_power"), bank.get("fault_code")


class SunTest(unittest.TestCase):
    def test_midsummer_noon_is_as_high_as_the_sun_gets_here(self):
        # 90 - 58 (latitude) + 23.44 (tilt of the Earth's axis)
        self.assertAlmostEqual(environment.sun_elevation(utc("2026-06-21T10:20:00")), 55.44, delta=1)

    def test_night_means_no_light(self):
        night = utc("2026-10-16T22:00:00")
        self.assertLess(environment.sun_elevation(night), 0)
        self.assertEqual(environment.irradiance("solar-b", night), 0)

    def test_clouds_never_make_it_brighter_than_clear_sky(self):
        for t in range(0, 24 * 3600, 300):
            moment = utc("2026-06-21T00:00:00") + t
            clear = environment.clear_sky_irradiance(environment.sun_elevation(moment))
            self.assertLessEqual(environment.irradiance("solar-b", moment), clear)


class InverterTest(unittest.TestCase):
    def setUp(self):
        self.device, self.bank = make_inverter()

    def test_reports_nameplate_and_module_capacity(self):
        self.assertEqual(self.bank.get("rated_power"), RATED)
        self.assertEqual(self.bank.get("dc_capacity"), DC)

    def test_produces_what_the_irradiance_predicts(self):
        state, power, fault = run(self.device, self.bank, irradiance=500)
        self.assertEqual((state, fault), (PRODUCING, 0))
        self.assertAlmostEqual(power, expected_power(500, DC), delta=0.02 * expected_power(500, DC))

    def test_bright_sun_clips_at_the_rating_and_that_is_not_curtailment(self):
        self.assertEqual(run(self.device, self.bank, irradiance=1000)[:2], (PRODUCING, RATED))

    def test_night_is_standby_not_a_fault(self):
        self.assertEqual(run(self.device, self.bank, irradiance=0), (STANDBY, 0, 0))

    def test_power_limit_curtails(self):
        modbus.handle_request(struct.pack(">BHH", 0x06, 0, 200), self.bank)  # 20 %
        self.assertEqual(run(self.device, self.bank, irradiance=800)[:2], (CURTAILED, RATED * 0.2))

    def test_fault_code_from_a_scenario(self):
        self.assertEqual(run(self.device, self.bank, irradiance=600, fault_code=201), (FAULT, 0, 201))

    def test_maintenance(self):
        self.assertEqual(run(self.device, self.bank, irradiance=600, maintenance=True), (MAINTENANCE, 0, 0))

    def test_degraded_park_produces_less_without_a_fault_code(self):
        state, power, fault = run(self.device, self.bank, irradiance=500, performance=0.7)
        self.assertEqual((state, fault), (PRODUCING, 0))
        self.assertLess(power, 0.75 * expected_power(500, DC))

    def test_undervoltage_trips_and_reconnects_after_the_delay(self):
        self.assertEqual(run(self.device, self.bank, irradiance=600, voltage_pu={"l1": 0.8}), (FAULT, 0, GRID_UNDERVOLTAGE))
        self.assertEqual(run(self.device, self.bank, NOW + 1, irradiance=600)[0], FAULT)  # grid back, still waiting
        self.assertEqual(run(self.device, self.bank, NOW + 1 + RECONNECT_DELAY_S, irradiance=600)[0], PRODUCING)

    def test_both_inverters_in_a_park_read_the_same_pyranometer(self):
        other, other_bank = make_inverter("inv-02")
        moment = utc("2026-06-21T10:00:00")
        self.device.update(moment, Scenario())
        other.update(moment, Scenario())
        self.assertEqual(self.bank.get("irradiance"), other_bank.get("irradiance"))
        self.assertGreater(self.bank.get("irradiance"), 100)


class GridProtectionTest(unittest.TestCase):
    def test_stays_connected_inside_the_settings(self):
        protection = GridProtection()
        self.assertEqual(protection.check(NOW, 49.0, [0.9, 1.0, 1.1]), 0)

    def test_frequency_outside_the_band_trips(self):
        self.assertEqual(GridProtection().check(NOW, 47.4, [1, 1, 1]), GRID_FREQUENCY)
        self.assertEqual(GridProtection().check(NOW, 51.6, [1, 1, 1]), GRID_FREQUENCY)

    def test_a_new_excursion_restarts_the_reconnect_timer(self):
        protection = GridProtection()
        protection.check(NOW, 47.0, [1, 1, 1])
        protection.check(NOW + 30, 50.0, [1, 1, 1])
        protection.check(NOW + 40, 47.0, [1, 1, 1])  # grid wobbles again
        # 60 s after the first excursion ended, but only 55 s after the second one did
        self.assertEqual(protection.check(NOW + 95, 50.0, [1, 1, 1]), GRID_FREQUENCY)
        self.assertEqual(protection.check(NOW + 95 + RECONNECT_DELAY_S, 50.0, [1, 1, 1]), 0)


class RegisterMapContractTest(unittest.TestCase):
    def test_labels_in_the_map_match_the_model(self):
        registers = load_map("inverter").registers
        states = {PRODUCING: "producing", STANDBY: "standby", CURTAILED: "curtailed", FAULT: "fault", MAINTENANCE: "maintenance"}
        self.assertEqual(registers["state"].values, {str(k): v for k, v in states.items()})
        self.assertEqual(registers["fault_code"].values, {str(k): v for k, v in FAULT_CODES.items()})


if __name__ == "__main__":
    unittest.main()