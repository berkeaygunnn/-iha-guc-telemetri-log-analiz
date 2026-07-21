"""Frontend'in App sınıfı için duman (smoke) testleri.

Gerçek bir pencere açıp backend'i çalıştırır, grafik çizimi sırasında
exception fırlamadığını ve çizilen çizgi sayısının beklenen batarya sayısıyla
eştiğini doğrular. CustomTkinter/Tkinter bir display gerektirdiği için CI'da
xvfb altında çalıştırılmalı (bkz. .github/workflows/tests.yml).

Kullanım: python test_smoke.py  (frontend/tests/ içinden)
"""

import sys
import unittest
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = FRONTEND_DIR.parent / "data"
sys.path.insert(0, str(FRONTEND_DIR))

import main as frontend_main


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.app = frontend_main.App()

    def tearDown(self):
        self.app.destroy()

    def _load_and_plot(self, filename: str) -> dict:
        data = self.app._run_backend(str(DATA_DIR / filename))
        self.app._plot_power_data(data)
        return data

    def test_ardupilot_real_flight(self):
        data = self._load_and_plot("ArduCopter-MaxAltFence-00000067.BIN")
        self.assertEqual(len(self.app.ax_voltage.get_lines()), len(data["batteries"]))

    def test_px4_real_multi_battery(self):
        data = self._load_and_plot("px4_sample_log_small.ulg")
        self.assertEqual(len(data["batteries"]), 2)
        self.assertEqual(len(self.app.ax_voltage.get_lines()), 2)
        self.assertEqual(len(self.app.ax_current.get_lines()), 2)

    def test_synthetic_bin_with_heatmap_toggle(self):
        data = self._load_and_plot("synthetic_test_log.BIN")
        self.assertEqual(len(data["motors"]), 4)
        self.app.motor_view_toggle.set("Isı Haritası")
        self.app._on_motor_view_change("Isı Haritası")
        self.app.motor_view_toggle.set("Çizgi Grafiği")
        self.app._on_motor_view_change("Çizgi Grafiği")

    def test_px4_multi_battery_busbar_heatmap_toggle(self):
        """Busbar (toplam akım) panelinin ısı haritası görünümü, batarya
        sayısı kadar satır (ytick) üretmeli ve çizgi görünümüne geri
        dönüldüğünde eski çizgi sayısı korunmalı."""
        data = self._load_and_plot("px4_sample_log_small.ulg")
        self.assertEqual(len(data["batteries"]), 2)
        self.assertEqual(len(self.app.ax_current.get_lines()), 2)

        self.app.battery_view_toggle.set("Isı Haritası")
        self.app._on_battery_view_change("Isı Haritası")
        self.assertEqual(len(self.app.ax_current.get_images()), 1)
        self.assertEqual(
            [t.get_text() for t in self.app.ax_current.get_yticklabels()],
            ["Batarya 1", "Batarya 2"],
        )

        self.app.battery_view_toggle.set("Çizgi Grafiği")
        self.app._on_battery_view_change("Çizgi Grafiği")
        self.assertEqual(len(self.app.ax_current.get_lines()), 2)

    def test_synthetic_bin_shows_motor_imbalance_warnings(self):
        self._load_and_plot("synthetic_test_log.BIN")
        warnings_text = self.app.warnings_label.cget("text")
        self.assertIn("Motor 1", warnings_text)
        self.assertIn("Motor 4", warnings_text)


if __name__ == "__main__":
    unittest.main()
