"""anomaly_spectral.py için birim testleri. Tk'ye gerek yok."""

import sys
import unittest
from pathlib import Path

import numpy as np

FRONTEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRONTEND_DIR))

import anomaly_spectral


def _make_current_signal(duration_s=10.0, rate_hz=100.0, dc=10.0, ripple_hz=5.0, ripple_amp=2.0):
    t = np.arange(0.0, duration_s, 1.0 / rate_hz)
    values = dc + ripple_amp * np.sin(2 * np.pi * ripple_hz * t)
    return t, values


class DominantFrequencyHzTests(unittest.TestCase):
    def test_recovers_known_ripple_frequency(self):
        t, values = _make_current_signal(ripple_hz=5.0)
        freq = anomaly_spectral.dominant_frequency_hz(t, values, target_rate_hz=100.0)
        self.assertAlmostEqual(freq, 5.0, delta=0.5)

    def test_too_short_signal_returns_none(self):
        t, values = _make_current_signal(duration_s=1.0)  # MIN_DURATION_S=2.0 altında
        self.assertIsNone(anomaly_spectral.dominant_frequency_hz(t, values))


class ExpectedRotorFrequencyHzTests(unittest.TestCase):
    def test_converts_rpm_to_hz(self):
        # 3000 RPM = 50 Hz
        self.assertAlmostEqual(anomaly_spectral.expected_rotor_frequency_hz([3000.0] * 10), 50.0, places=6)

    def test_empty_or_zero_rpm_returns_none(self):
        self.assertIsNone(anomaly_spectral.expected_rotor_frequency_hz([]))
        self.assertIsNone(anomaly_spectral.expected_rotor_frequency_hz([0.0, 0.0]))


class ImbalanceScoreTests(unittest.TestCase):
    def test_matching_rpm_and_current_ripple_gives_high_score(self):
        """Akım 5 Hz'de dalgalanıyor, RPM ortalaması 300 RPM = 5 Hz rotor
        frekansı - bu, pervane dengesizliğinin tipik imzası (1x rotor
        frekansı) ve yüksek skor almalı."""
        t, current = _make_current_signal(ripple_hz=5.0)
        rpm = np.full_like(t, 300.0)  # 300 RPM = 5 Hz
        score = anomaly_spectral.imbalance_score(t, current, rpm=rpm)
        self.assertGreater(score, 0.8)

    def test_mismatched_rpm_and_current_ripple_gives_low_score(self):
        t, current = _make_current_signal(ripple_hz=5.0)
        rpm = np.full_like(t, 3000.0)  # 3000 RPM = 50 Hz -- akımdaki 5 Hz ile uyuşmuyor
        score = anomaly_spectral.imbalance_score(t, current, rpm=rpm)
        self.assertLess(score, 0.5)

    def test_no_rpm_fallback_path_does_not_raise(self):
        t, current = _make_current_signal(ripple_hz=5.0)
        score = anomaly_spectral.imbalance_score(t, current, rpm=None)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_flat_signal_gives_low_score_no_false_positive(self):
        """Dalgalanmasız, sabit akım - dengesizlik yok, skor düşük olmalı."""
        t = np.arange(0.0, 10.0, 0.01)
        current = np.full_like(t, 10.0)
        score = anomaly_spectral.imbalance_score(t, current, rpm=None)
        self.assertLess(score, 0.3)


if __name__ == "__main__":
    unittest.main()
