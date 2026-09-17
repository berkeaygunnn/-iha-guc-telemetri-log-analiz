"""anomaly_features.py için birim testleri. Tk'ye gerek yok."""

import sys
import unittest
from pathlib import Path

import numpy as np

FRONTEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRONTEND_DIR))

import anomaly_features


class RmsTests(unittest.TestCase):
    def test_sine_wave_matches_analytic_formula(self):
        """Bir sinüsün RMS'i analitik olarak genlik/sqrt(2)'dir."""
        t = np.linspace(0.0, 10.0, 100000)
        amplitude = 4.0
        values = amplitude * np.sin(2 * np.pi * 3.0 * t)
        self.assertAlmostEqual(anomaly_features.rms(values), amplitude / np.sqrt(2), places=2)

    def test_empty_array_returns_zero(self):
        self.assertEqual(anomaly_features.rms([]), 0.0)

    def test_constant_array_rms_equals_value(self):
        self.assertAlmostEqual(anomaly_features.rms([5.0, 5.0, 5.0]), 5.0, places=6)


class PeakTests(unittest.TestCase):
    def test_finds_largest_magnitude_regardless_of_sign(self):
        self.assertEqual(anomaly_features.peak([1.0, -8.0, 3.0]), 8.0)

    def test_empty_array_returns_zero(self):
        self.assertEqual(anomaly_features.peak([]), 0.0)


class VoltageSagRateTests(unittest.TestCase):
    def test_linear_ramp_has_constant_negative_slope(self):
        """16.8V'tan 15.8V'a 10 saniyede doğrusal düşüş -> dV/dt sabit -0.1."""
        t = np.linspace(0.0, 10.0, 11)
        voltage = 16.8 - 0.1 * t
        rate = anomaly_features.voltage_sag_rate(t, voltage)
        np.testing.assert_allclose(rate, -0.1, atol=1e-9)

    def test_fewer_than_two_samples_returns_zeros(self):
        rate = anomaly_features.voltage_sag_rate([0.0], [16.8])
        np.testing.assert_array_equal(rate, [0.0])


class CurrentRippleTests(unittest.TestCase):
    def test_noisy_signal_has_higher_ripple_than_flat_signal(self):
        rng = np.random.default_rng(1)
        flat = np.full(1000, 10.0)
        noisy = 10.0 + rng.normal(0.0, 2.0, size=1000)
        self.assertLess(anomaly_features.current_ripple(flat), anomaly_features.current_ripple(noisy))

    def test_flat_signal_ripple_is_zero(self):
        self.assertAlmostEqual(anomaly_features.current_ripple([10.0] * 50), 0.0, places=9)

    def test_near_zero_mean_avoids_division_blowup(self):
        self.assertEqual(anomaly_features.current_ripple([1e-9, -1e-9, 2e-9]), 0.0)

    def test_empty_array_returns_zero(self):
        self.assertEqual(anomaly_features.current_ripple([]), 0.0)


if __name__ == "__main__":
    unittest.main()
