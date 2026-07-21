"""Frontend'in App sınıfı için duman (smoke) testleri.

Gerçek bir pencere açıp backend'i çalıştırır, grafik çizimi sırasında
exception fırlamadığını ve çizilen çizgi sayısının beklenen batarya sayısıyla
eştiğini doğrular. CustomTkinter/Tkinter bir display gerektirdiği için CI'da
xvfb altında çalıştırılmalı (bkz. .github/workflows/tests.yml).

Kullanım: python test_smoke.py  (frontend/tests/ içinden)
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

FRONTEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = FRONTEND_DIR.parent / "data"
sys.path.insert(0, str(FRONTEND_DIR))

import main as frontend_main


class SmokeTests(unittest.TestCase):
    def setUp(self):
        # Son kullanılanlar config'i gerçek kullanıcı home klasörü yerine
        # geçici bir klasöre yazılsın diye (test izolasyonu).
        self._tmp_config_dir = Path(tempfile.mkdtemp())
        frontend_main.RECENT_FILES_PATH = self._tmp_config_dir / "recent_files.json"
        self.app = frontend_main.App()

    def tearDown(self):
        self.app.destroy()
        shutil.rmtree(self._tmp_config_dir, ignore_errors=True)

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

    def test_hexarotor_six_motors_have_distinct_line_styles(self):
        """SERIES_COLORS 8 renge çıkarılmadan önce 4'ten fazla seri (bu logda
        6 motor) aynı renkte çiziliyordu (motor 1/5 ve 2/6 ayırt edilemezdi).
        6 motor 8 renklik paleti aşmadığı için hepsi hem farklı renkte hem de
        düz çizgi stilinde olmalı."""
        data = self._load_and_plot("px4_hexarotor_flight.ulg")
        self.assertEqual(len(data["motors"]), 6)
        lines = self.app.ax_motors.get_lines()
        self.assertEqual(len(lines), 6)
        colors = [line.get_color() for line in lines]
        self.assertEqual(len(set(colors)), 6)  # hepsi farklı renkte
        self.assertTrue(all(line.get_linestyle() == "-" for line in lines))

    def test_load_file_adds_to_recent_menu(self):
        file_path = str(DATA_DIR / "synthetic_test_log.BIN")
        self.app._load_file(file_path)

        self.assertEqual(self.app.recent_menu.cget("state"), "normal")
        self.assertIn(file_path, self.app._recent_label_to_path.values())
        # Diske de yazıldığını doğrula (uygulama kapanıp açılınca kalıcı olmalı).
        recent = self.app._load_recent_files()
        self.assertEqual(recent, [file_path])

    def test_recent_file_selection_reloads_it(self):
        file_path = str(DATA_DIR / "synthetic_test_log.BIN")
        self.app._load_file(file_path)
        self.app._on_clear_click()
        self.assertEqual(len(self.app.ax_voltage.get_lines()), 0)

        label = next(iter(self.app._recent_label_to_path))
        self.app._on_recent_file_selected(label)
        self.assertGreater(len(self.app.ax_voltage.get_lines()), 0)

    def test_clear_button_resets_everything(self):
        self._load_and_plot("synthetic_test_log.BIN")
        self.assertGreater(len(self.app.ax_voltage.get_lines()), 0)

        self.app._on_clear_click()

        self.assertEqual(len(self.app.ax_voltage.get_lines()), 0)
        self.assertEqual(len(self.app.ax_current.get_lines()), 0)
        self.assertEqual(len(self.app.ax_motors.get_lines()), 0)
        self.assertEqual(self.app.warnings_label.cget("text"), "")
        self.assertEqual(self.app.file_label.cget("text"), "Henüz dosya seçilmedi.")
        self.assertEqual(self.app.stat_labels["duration"].cget("text"), "—")

    def test_export_png_creates_file(self):
        self._load_and_plot("synthetic_test_log.BIN")
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = str(Path(tmp_dir) / "grafik.png")
            with patch("main.filedialog.asksaveasfilename", return_value=out_path):
                self.app._on_export_png_click()
            self.assertTrue(Path(out_path).exists())
            self.assertGreater(Path(out_path).stat().st_size, 0)

    def test_app_starts_on_landing_screen(self):
        self.app.update()
        self.assertTrue(self.app.landing_frame.winfo_ismapped())
        self.assertFalse(self.app.analysis_frame.winfo_ismapped())

    def test_loading_file_switches_to_analysis_screen(self):
        self.app._load_file(str(DATA_DIR / "synthetic_test_log.BIN"))
        self.app.update()
        self.assertFalse(self.app.landing_frame.winfo_ismapped())
        self.assertTrue(self.app.analysis_frame.winfo_ismapped())

    def test_recent_sidebar_updates_after_load(self):
        file_path = str(DATA_DIR / "synthetic_test_log.BIN")
        self.app._load_file(file_path)

        sidebar_buttons = [
            child for child in self.app._recent_sidebar_list.winfo_children()
            if isinstance(child, frontend_main.ctk.CTkButton)
        ]
        self.assertEqual(len(sidebar_buttons), 1)
        self.assertEqual(sidebar_buttons[0].cget("text"), Path(file_path).name)


if __name__ == "__main__":
    unittest.main()
