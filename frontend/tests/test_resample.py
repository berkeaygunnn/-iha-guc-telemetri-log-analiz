"""resample.py için birim testleri. Tk'ye gerek yok."""

import sys
import unittest
from pathlib import Path

import numpy as np

FRONTEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRONTEND_DIR))

import resample


class ResampleUniformTests(unittest.TestCase):
    def test_recovers_irregular_sine_wave_close_to_analytic(self):
        """Düzensiz aralıklarla örneklenmiş bir sinüs dalgasını 50 Hz'e
        yeniden örnekleyip analitik değerle karşılaştırır."""
        rng = np.random.default_rng(42)
        t_irregular = np.sort(rng.uniform(0.0, 10.0, size=2000))
        values = np.sin(2 * np.pi * 1.0 * t_irregular)  # 1 Hz sinüs

        t_uniform, resampled = resample.resample_uniform(t_irregular, values, target_rate_hz=50.0)

        self.assertEqual(len(t_uniform), len(resampled))
        analytic = np.sin(2 * np.pi * 1.0 * t_uniform)
        np.testing.assert_allclose(resampled, analytic, atol=0.05)

    def test_output_rate_matches_target(self):
        t = np.linspace(0.0, 10.0, 37)  # bilerek düzensiz-hisli az sayıda örnek
        values = t * 2.0
        t_uniform, _ = resample.resample_uniform(t, values, target_rate_hz=10.0)
        dt = np.diff(t_uniform)
        np.testing.assert_allclose(dt, 0.1, atol=1e-9)

    def test_zero_samples_returns_unchanged(self):
        t, values = np.array([]), np.array([])
        out_t, out_values = resample.resample_uniform(t, values, target_rate_hz=50.0)
        self.assertEqual(len(out_t), 0)
        self.assertEqual(len(out_values), 0)

    def test_single_sample_returns_unchanged(self):
        t, values = np.array([1.0]), np.array([5.0])
        out_t, out_values = resample.resample_uniform(t, values, target_rate_hz=50.0)
        np.testing.assert_array_equal(out_t, [1.0])
        np.testing.assert_array_equal(out_values, [5.0])


if __name__ == "__main__":
    unittest.main()
