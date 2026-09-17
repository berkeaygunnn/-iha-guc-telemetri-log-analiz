"""flight_series.py için birim testleri.

Tk'ye ihtiyaç YOK (test_smoke.py'nin aksine) - elle kurulmuş sözlüklerle
çalışır, backend'e/gerçek log dosyasına da gerek yok. Bu yüzden hızlı ve
xvfb/display gerektirmeden çalışır.

Kullanım: python test_flight_series.py  (frontend/tests/ içinden)
"""

import sys
import unittest
from pathlib import Path

import numpy as np

FRONTEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRONTEND_DIR))

import flight_series


class BatterySeriesTests(unittest.TestCase):
    def test_converts_lists_to_numpy_arrays(self):
        battery = {
            "id": 1,
            "time_s": [0.0, 1.0, 2.0],
            "voltage_v": [16.8, 16.5, 16.2],
            "current_a": [5.0, 10.0, 15.0],
            "has_current_data": True,
        }
        series = flight_series.battery_series(battery)
        self.assertEqual(series["id"], 1)
        np.testing.assert_array_equal(series["t"], [0.0, 1.0, 2.0])
        np.testing.assert_array_equal(series["voltage_v"], [16.8, 16.5, 16.2])
        np.testing.assert_array_equal(series["current_a"], [5.0, 10.0, 15.0])
        self.assertTrue(series["has_current_data"])

    def test_has_current_data_false_is_preserved(self):
        battery = {
            "id": 2, "time_s": [0.0], "voltage_v": [12.0], "current_a": [0.0],
            "has_current_data": False,
        }
        self.assertFalse(flight_series.battery_series(battery)["has_current_data"])


class MotorSeriesTests(unittest.TestCase):
    def test_converts_lists_including_rpm(self):
        motor = {
            "id": 1,
            "time_s": [0.0, 1.0],
            "current_a": [5.0, 6.0],
            "has_current_data": True,
            "rpm": [3200.0, 3300.0],
            "has_rpm_data": True,
        }
        series = flight_series.motor_series(motor)
        np.testing.assert_array_equal(series["rpm"], [3200.0, 3300.0])
        self.assertTrue(series["has_rpm_data"])

    def test_missing_rpm_fields_default_safely(self):
        """Aşama 1 öncesi üretilmiş eski bir JSON'da rpm/has_rpm_data hiç
        yok - geriye dönük uyumluluk: crash yerine güvenli varsayılan
        (boş dizi, has_rpm_data=False)."""
        motor = {
            "id": 1, "time_s": [0.0, 1.0], "current_a": [5.0, 6.0],
            "has_current_data": True,
        }
        series = flight_series.motor_series(motor)
        self.assertEqual(len(series["rpm"]), 0)
        self.assertFalse(series["has_rpm_data"])


class SampleRateHzTests(unittest.TestCase):
    def test_uniform_sampling(self):
        t = np.arange(0.0, 10.0, 0.1)  # 10 Hz
        self.assertAlmostEqual(flight_series.sample_rate_hz(t), 10.0, places=3)

    def test_irregular_sampling_uses_median_not_mean(self):
        """Log başında tek bir büyük boşluk (0 -> 5) var, geri kalanı 0.1
        adımlarla düzenli. Ortalama bu tek boşluktan çok etkilenirdi; medyan
        etkilenmemeli ve gerçek (baskın) hızı yansıtmalı."""
        t = np.concatenate([[0.0, 5.0], 5.0 + np.arange(0.1, 5.0, 0.1)])
        self.assertAlmostEqual(flight_series.sample_rate_hz(t), 10.0, places=1)

    def test_fewer_than_two_samples_returns_none(self):
        self.assertIsNone(flight_series.sample_rate_hz(np.array([])))
        self.assertIsNone(flight_series.sample_rate_hz(np.array([1.0])))


if __name__ == "__main__":
    unittest.main()
