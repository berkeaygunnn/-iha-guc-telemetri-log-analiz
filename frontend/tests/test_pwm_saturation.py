"""pwm_saturation.py için birim testleri. Tk'ye gerek yok."""

import sys
import unittest
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRONTEND_DIR))

import pwm_saturation


def _channel(label, values):
    return {"id": 1, "label": label, "time_s": list(range(len(values))), "pwm_us": values}


class ComputeChannelSaturationTests(unittest.TestCase):
    def test_half_saturated_channel_gives_50_percent(self):
        # 10 örnek: 5'i eşiğin altında, 5'i üstünde/eşit.
        values = [1000] * 5 + [1900] * 5
        per_channel, overall = pwm_saturation.compute_channel_saturation(
            [_channel("Kanal 1", values)], threshold_us=1900.0)
        self.assertEqual(len(per_channel), 1)
        self.assertAlmostEqual(per_channel[0].saturated_pct, 50.0)
        self.assertAlmostEqual(overall, 50.0)

    def test_never_saturated_channel_gives_zero(self):
        values = [1000] * 10
        per_channel, overall = pwm_saturation.compute_channel_saturation(
            [_channel("Kanal 1", values)], threshold_us=1900.0)
        self.assertAlmostEqual(per_channel[0].saturated_pct, 0.0)
        self.assertAlmostEqual(overall, 0.0)

    def test_always_saturated_channel_gives_100(self):
        values = [2000] * 10
        per_channel, overall = pwm_saturation.compute_channel_saturation(
            [_channel("Kanal 1", values)], threshold_us=1900.0)
        self.assertAlmostEqual(per_channel[0].saturated_pct, 100.0)
        self.assertAlmostEqual(overall, 100.0)

    def test_overall_is_average_of_channels(self):
        channels = [_channel("Kanal 1", [2000] * 10), _channel("Kanal 2", [1000] * 10)]
        per_channel, overall = pwm_saturation.compute_channel_saturation(channels, threshold_us=1900.0)
        self.assertEqual(len(per_channel), 2)
        self.assertAlmostEqual(overall, 50.0)  # (100 + 0) / 2

    def test_no_pwm_data_returns_clear_empty_result(self):
        per_channel, overall = pwm_saturation.compute_channel_saturation([])
        self.assertEqual(per_channel, [])
        self.assertIsNone(overall)

    def test_channels_with_empty_pwm_us_are_ignored(self):
        per_channel, overall = pwm_saturation.compute_channel_saturation(
            [{"id": 1, "label": "Kanal 1", "time_s": [], "pwm_us": []}])
        self.assertEqual(per_channel, [])
        self.assertIsNone(overall)

    def test_configurable_threshold_changes_result(self):
        values = [1500] * 10
        _, overall_low = pwm_saturation.compute_channel_saturation(
            [_channel("Kanal 1", values)], threshold_us=1000.0)
        _, overall_high = pwm_saturation.compute_channel_saturation(
            [_channel("Kanal 1", values)], threshold_us=1900.0)
        self.assertGreater(overall_low, overall_high)


if __name__ == "__main__":
    unittest.main()
