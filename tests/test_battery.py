import unittest
from datetime import datetime, timezone

from fleet import models
from fleet.models.battery import (
    BMS_RESET_C, BMS_TRIP_C, CHARGING, DISCHARGING, FAULT, FCR_CAPACITY_KW, FREQUENCY_RESPONSE, MAINTENANCE,
    SOC_MAX, fcr_kw, schedule_kw,
)
from fleet.registers import RegisterBank, load_map
from fleet.scenarios import Scenario


def utc(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


MIDDAY = utc("2026-10-16T10:00:00")   # charging hours
EVENING = utc("2026-10-16T16:00:00")  # discharging hours
NIGHT = utc("2026-10-16T23:00:00")    # frequency response only


def make_battery():
    register_map = load_map("battery")
    bank = RegisterBank(register_map.registers)
    return models.create("battery", "bess-01", bank, register_map, "bess-c"), bank


def run_for(device, start: float, seconds: int, grid=None, **conditions):
    for t in range(seconds):
        device.update(start + t, Scenario(grid=grid or {}, device=conditions))


class FrequencyResponseTest(unittest.TestCase):
    def test_full_response_at_200_mhz_either_way(self):
        self.assertEqual(fcr_kw(49.8), FCR_CAPACITY_KW)  # low frequency: discharge
        self.assertEqual(fcr_kw(50.2), -FCR_CAPACITY_KW)  # high frequency: charge
        self.assertEqual(fcr_kw(49.0), FCR_CAPACITY_KW)  # capped
        self.assertAlmostEqual(fcr_kw(49.95), FCR_CAPACITY_KW / 4)

    def test_battery_discharges_when_the_grid_frequency_drops(self):
        device, bank = make_battery()
        device.update(NIGHT, Scenario(grid={"frequency_offset_hz": -0.3}))
        self.assertEqual(bank.get("state"), FREQUENCY_RESPONSE)
        self.assertAlmostEqual(bank.get("active_power"), FCR_CAPACITY_KW, delta=1)


class ScheduleTest(unittest.TestCase):
    def test_trading_schedule(self):
        self.assertLess(schedule_kw(MIDDAY), 0)
        self.assertGreater(schedule_kw(EVENING), 0)
        self.assertEqual(schedule_kw(NIGHT), 0)

    def test_charging_fills_and_discharging_empties(self):
        device, bank = make_battery()
        run_for(device, MIDDAY, 600)
        self.assertEqual(bank.get("state"), CHARGING)
        self.assertLess(bank.get("active_power"), 0)  # generator convention: negative is drawing from the grid
        after_charging = bank.get("soc")
        self.assertGreater(after_charging, 50)
        run_for(device, EVENING, 600)
        self.assertEqual(bank.get("state"), DISCHARGING)
        self.assertLess(bank.get("soc"), after_charging)

    def test_a_full_battery_stops_charging(self):
        device, bank = make_battery()
        device.stored_kwh = device.CAPACITY_KWH * SOC_MAX / 100
        device.update(MIDDAY, Scenario())
        self.assertGreaterEqual(bank.get("active_power"), 0)
        self.assertEqual(bank.get("state"), FREQUENCY_RESPONSE)

    def test_a_scenario_can_hold_the_charge_so_a_full_battery_still_charges(self):
        device, bank = make_battery()
        device.stored_kwh = device.CAPACITY_KWH * SOC_MAX / 100
        run_for(device, NIGHT, 600, power_kw=-5000, soc=50)
        self.assertEqual(bank.get("state"), CHARGING)
        self.assertLess(bank.get("active_power"), -4000)  # frequency response rides on top
        self.assertAlmostEqual(bank.get("soc"), 50, delta=0.1)

    def test_fault_and_maintenance(self):
        device, bank = make_battery()
        device.update(MIDDAY, Scenario(device={"fault": True}))
        self.assertEqual((bank.get("state"), bank.get("active_power")), (FAULT, 0))
        device.update(MIDDAY + 1, Scenario(device={"maintenance": True}))
        self.assertEqual((bank.get("state"), bank.get("active_power")), (MAINTENANCE, 0))

class SetpointTest(unittest.TestCase):
    def test_setpoint_is_the_schedule_and_the_gap_is_frequency_response(self):
        device, bank = make_battery()
        device.update(EVENING, Scenario(grid={"frequency_offset_hz": -0.1}))
        self.assertEqual(bank.get("power_setpoint"), schedule_kw(EVENING))
        gap = bank.get("active_power") - bank.get("power_setpoint")
        self.assertAlmostEqual(gap, fcr_kw(bank.get("frequency")), delta=6)  # register rounding
        self.assertGreater(gap, 0)  # low frequency: more discharge than planned

    def test_an_override_is_the_setpoint_and_a_trip_shows_as_the_whole_gap(self):
        device, bank = make_battery()
        device.update(NIGHT, Scenario(device={"power_kw": -5000, "fault": True}))
        self.assertEqual(bank.get("power_setpoint"), -5000)
        self.assertEqual(bank.get("active_power"), 0)
        
class ThermalTest(unittest.TestCase):
    def test_cells_stay_cool_at_full_power_with_working_hvac(self):
        device, bank = make_battery()
        run_for(device, MIDDAY, 3600, power_kw=-5000)
        self.assertLess(bank.get("cell_temperature"), 40)

    def test_failed_cooling_makes_cells_climb_while_charging_until_the_bms_trips(self):
        device, bank = make_battery()
        run_for(device, MIDDAY, 300, power_kw=-5000)
        before = bank.get("cell_temperature")
        run_for(device, MIDDAY + 300, 300, power_kw=-5000, cooling_failure=True)
        climb_per_minute = (bank.get("cell_temperature") - before) / 5
        self.assertGreater(climb_per_minute, 1.0)  # fast enough for an alert to see

        hottest = 0.0
        for t in range(1800):
            device.update(MIDDAY + 600 + t, Scenario(device={"power_kw": -5000, "cooling_failure": True}))
            hottest = max(hottest, bank.get("cell_temperature"))
        self.assertGreaterEqual(hottest, BMS_TRIP_C)
        self.assertLess(hottest, BMS_TRIP_C + 1)  # the trip stopped the heating
        self.assertEqual((bank.get("state"), bank.get("active_power")), (FAULT, 0))

    def test_bms_allows_a_restart_only_after_the_cells_cool_down(self):
        device, bank = make_battery()
        device.cell_c = BMS_TRIP_C + 1
        device.update(MIDDAY, Scenario())
        self.assertEqual(bank.get("state"), FAULT)
        device.cell_c = BMS_RESET_C + 5  # cooler, but not cool enough
        device.update(MIDDAY + 1, Scenario())
        self.assertEqual(bank.get("state"), FAULT)
        device.cell_c = BMS_RESET_C - 1
        device.update(MIDDAY + 2, Scenario())
        self.assertEqual(bank.get("state"), CHARGING)


class RegisterMapContractTest(unittest.TestCase):
    def test_state_labels_in_the_map_match_the_model(self):
        labels = load_map("battery").registers["state"].values
        expected = {
            CHARGING: "charging", DISCHARGING: "discharging", FREQUENCY_RESPONSE: "frequency response",
            FAULT: "fault", MAINTENANCE: "maintenance",
        }
        self.assertEqual(labels, {str(code): label for code, label in expected.items()})


if __name__ == "__main__":
    unittest.main()