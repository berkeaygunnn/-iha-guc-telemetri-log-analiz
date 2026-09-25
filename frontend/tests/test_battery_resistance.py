"""battery_resistance.py için birim testleri. Tk'ye gerek yok."""

import sys
import unittest
from pathlib import Path

import numpy as np

FRONTEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRONTEND_DIR))

import battery_resistance


def _synthetic_battery(battery_id=1, n=200, resistance_ohm=0.05, noise_std=0.01, seed=3):
    """Bilinen bir iç dirençle (V = V0 - R*I) sentetik bir batarya serisi
    üretir; akım hover/yük değişimini taklit eden bir profille dalgalanır ki
    "belirgin değişen örnekleri seç" filtresi anlamlı bir alt küme bulsun."""
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float) * 0.1
    current_a = 10.0 + 8.0 * np.sin(2 * np.pi * 0.05 * t) + rng.normal(0, 0.3, n)
    voltage_v = 16.8 - resistance_ohm * current_a + rng.normal(0, noise_std, n)
    return {
        "id": battery_id, "time_s": t.tolist(), "voltage_v": voltage_v.tolist(),
        "current_a": current_a.tolist(), "has_current_data": True,
    }


class EstimateBatteryResistanceTests(unittest.TestCase):
    def test_recovers_known_resistance_within_tolerance(self):
        battery = _synthetic_battery(resistance_ohm=0.05)  # 50 mOhm
        estimate, reason = battery_resistance.estimate_battery_resistance(battery)
        self.assertIsNone(reason)
        self.assertIsNotNone(estimate)
        self.assertAlmostEqual(estimate.resistance_mohm, 50.0, delta=8.0)
        self.assertGreater(estimate.r_squared, 0.5)

    def test_low_noise_gives_higher_confidence_than_high_noise(self):
        clean = _synthetic_battery(resistance_ohm=0.05, noise_std=0.005, seed=1)
        noisy = _synthetic_battery(resistance_ohm=0.05, noise_std=0.5, seed=1)
        est_clean, _ = battery_resistance.estimate_battery_resistance(clean)
        est_noisy, _ = battery_resistance.estimate_battery_resistance(noisy)
        self.assertGreater(est_clean.r_squared, est_noisy.r_squared)

    def test_no_current_data_gives_clear_reason(self):
        battery = _synthetic_battery()
        battery["has_current_data"] = False
        estimate, reason = battery_resistance.estimate_battery_resistance(battery)
        self.assertIsNone(estimate)
        self.assertEqual(reason, "hesaplanamadı: akım sensörü verisi yok")

    def test_too_short_log_gives_clear_reason(self):
        battery = _synthetic_battery(n=3)
        estimate, reason = battery_resistance.estimate_battery_resistance(battery)
        self.assertIsNone(estimate)
        self.assertEqual(reason, "hesaplanamadı: log çok kısa")

    def test_flat_current_gives_clear_reason(self):
        battery = _synthetic_battery()
        battery["current_a"] = [10.0] * len(battery["current_a"])  # hiç değişmiyor
        estimate, reason = battery_resistance.estimate_battery_resistance(battery)
        self.assertIsNone(estimate)
        self.assertEqual(reason, "hesaplanamadı: akım yeterince değişmiyor")

    def test_does_not_raise_on_malformed_input(self):
        """Çökme yerine her zaman (None, sebep) ya da (estimate, None) döner."""
        estimate, reason = battery_resistance.estimate_battery_resistance(
            {"id": 1, "time_s": [], "voltage_v": [], "current_a": [], "has_current_data": True}
        )
        self.assertIsNone(estimate)
        self.assertIsNotNone(reason)


class EstimateWorstBatteryResistanceTests(unittest.TestCase):
    def test_picks_highest_resistance_among_multiple(self):
        weak = _synthetic_battery(battery_id=1, resistance_ohm=0.15, seed=5)
        strong = _synthetic_battery(battery_id=2, resistance_ohm=0.02, seed=6)
        estimate, reason = battery_resistance.estimate_worst_battery_resistance([strong, weak])
        self.assertIsNone(reason)
        self.assertEqual(estimate.battery_id, 1)

    def test_empty_list_gives_clear_reason(self):
        estimate, reason = battery_resistance.estimate_worst_battery_resistance([])
        self.assertIsNone(estimate)
        self.assertIn("hesaplanamadı", reason)

    def test_all_unusable_batteries_gives_clear_reason(self):
        battery = _synthetic_battery()
        battery["has_current_data"] = False
        estimate, reason = battery_resistance.estimate_worst_battery_resistance([battery])
        self.assertIsNone(estimate)
        self.assertEqual(reason, "hesaplanamadı: akım sensörü verisi yok")


if __name__ == "__main__":
    unittest.main()
