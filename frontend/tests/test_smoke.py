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
import time
import types
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
        frontend_main.SETTINGS_PATH = self._tmp_config_dir / "settings.json"
        self.app = frontend_main.App()

    def tearDown(self):
        self.app.destroy()
        shutil.rmtree(self._tmp_config_dir, ignore_errors=True)

    def _load_and_plot(self, filename: str) -> dict:
        data = self.app._run_backend(str(DATA_DIR / filename))
        self.app._plot_power_data(data)
        return data

    def _wait_for_load(self, timeout_s: float = 5.0):
        """_load_file (ve onu çağıran _on_recent_file_selected gibi yollar)
        artık backend'i ayrı bir thread'de çalıştırıyor (bkz. main.py);
        testlerin _poll_backend_result tamamlanana kadar Tk event loop'unu
        döndürmesi gerekiyor, yoksa henüz güncellenmemiş widget'ları kontrol
        edip yanlışlıkla başarısız olurlar."""
        deadline = time.monotonic() + timeout_s
        while self.app._is_loading and time.monotonic() < deadline:
            self.app.update()
        self.app.update()

    def _load_file_and_wait(self, file_path: str, timeout_s: float = 5.0):
        self.app._load_file(file_path)
        self._wait_for_load(timeout_s)

    @staticmethod
    def _data_lines(ax):
        """ax.get_lines(), eşik/dekoratif çizgileri (ör. voltaj sag çizgisi,
        "_" ile başlayan etiket) hariç tutup sadece gerçek veri serilerini
        (batarya/motor) döner."""
        return [line for line in ax.get_lines() if not line.get_label().startswith("_")]

    def test_ardupilot_real_flight(self):
        data = self._load_and_plot("ArduCopter-MaxAltFence-00000067.BIN")
        self.assertEqual(len(self._data_lines(self.app.ax_voltage)), len(data["batteries"]))

    def test_px4_real_multi_battery(self):
        data = self._load_and_plot("px4_sample_log_small.ulg")
        self.assertEqual(len(data["batteries"]), 2)
        self.assertEqual(len(self._data_lines(self.app.ax_voltage)), 2)
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
        self._load_file_and_wait(file_path)

        self.assertEqual(self.app.recent_menu.cget("state"), "normal")
        self.assertIn(file_path, self.app._recent_label_to_path.values())
        # Diske de yazıldığını doğrula (uygulama kapanıp açılınca kalıcı olmalı).
        recent = self.app._load_recent_files()
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["path"], file_path)
        self.assertIsNotNone(recent[0]["last_opened"])
        self.assertIsNotNone(recent[0]["duration_s"])

    def test_recent_file_selection_reloads_it(self):
        file_path = str(DATA_DIR / "synthetic_test_log.BIN")
        self._load_file_and_wait(file_path)
        self.app._on_clear_click()
        self.assertEqual(len(self.app.ax_voltage.get_lines()), 0)

        label = next(iter(self.app._recent_label_to_path))
        self.app._on_recent_file_selected(label)
        self._wait_for_load()
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
        # Ekran geçişi _load_file'ın en başında, arka plan thread'i
        # başlamadan ÖNCE senkron yapılıyor; yine de tutarlılık için
        # yükleme bitene kadar bekliyoruz.
        self._load_file_and_wait(str(DATA_DIR / "synthetic_test_log.BIN"))
        self.assertFalse(self.app.landing_frame.winfo_ismapped())
        self.assertTrue(self.app.analysis_frame.winfo_ismapped())

    def test_recent_sidebar_updates_after_load(self):
        file_path = str(DATA_DIR / "synthetic_test_log.BIN")
        self._load_file_and_wait(file_path)

        # Her satır artık tek bir Canvas (bkz. _build_recent_card): rozet/ad/
        # meta yazısı ayrı widget'lar değil, bu canvas'ın üzerine çizilen
        # metin öğeleri.
        canvases = [
            child for child in self.app._recent_sidebar_list.winfo_children()
            if isinstance(child, frontend_main.tk.Canvas)
        ]
        self.assertEqual(len(canvases), 1)
        text_items = [
            canvases[0].itemcget(item, "text") for item in canvases[0].find_all()
            if canvases[0].type(item) == "text"
        ]
        self.assertIn(Path(file_path).stem, text_items)
        self.assertIn("BIN", text_items)

    def test_file_label_shows_only_filename_not_full_path(self):
        """Tam mutlak yol, toolbar'daki diğer butonların (ör. 'Grafiği
        Kaydet') ekran dışına itilip kesilmesine yol açıyordu; artık sadece
        dosya adı gösteriliyor."""
        file_path = str(DATA_DIR / "synthetic_test_log.BIN")
        self._load_file_and_wait(file_path)
        self.assertEqual(self.app.file_label.cget("text"), "synthetic_test_log.BIN")

    def test_home_button_returns_to_landing_screen(self):
        self._load_file_and_wait(str(DATA_DIR / "synthetic_test_log.BIN"))
        self.assertTrue(self.app.analysis_frame.winfo_ismapped())

        self.app.home_button.invoke()
        self.app.update()
        self.assertTrue(self.app.landing_frame.winfo_ismapped())
        self.assertFalse(self.app.analysis_frame.winfo_ismapped())

    def test_heatmap_toggle_does_not_shift_plot_position(self):
        """Isı haritası colorbar'ı eskiden ana ekseni ax=... ile kucultuyordu,
        bu da cizgi/isi haritasi arasinda gecince panelin sag/sola kaymis
        gibi gorunmesine yol aciyordu. Artik colorbar sabit, ayri bir
        eksene (cax=...) ciziliyor; ana eksenin figur icindeki konumu
        (get_position) degismemeli."""
        self._load_and_plot("px4_hexarotor_flight.ulg")
        current_pos_before = self.app.ax_current.get_position()
        motors_pos_before = self.app.ax_motors.get_position()

        self.app.battery_view_toggle.set("Isı Haritası")
        self.app._on_battery_view_change("Isı Haritası")
        self.app.motor_view_toggle.set("Isı Haritası")
        self.app._on_motor_view_change("Isı Haritası")

        self.assertEqual(self.app.ax_current.get_position().bounds, current_pos_before.bounds)
        self.assertEqual(self.app.ax_motors.get_position().bounds, motors_pos_before.bounds)

        self.app.battery_view_toggle.set("Çizgi Grafiği")
        self.app._on_battery_view_change("Çizgi Grafiği")
        self.app.motor_view_toggle.set("Çizgi Grafiği")
        self.app._on_motor_view_change("Çizgi Grafiği")

        self.assertEqual(self.app.ax_current.get_position().bounds, current_pos_before.bounds)
        self.assertEqual(self.app.ax_motors.get_position().bounds, motors_pos_before.bounds)

    def test_panel_labels_stay_consistent_across_view_modes(self):
        """'Toplam Akım (A)'/'Motor Akımı (A)' etiketleri ısı haritasına
        geçince farklı bir metne ("Busbar"/"Motor") değişiyordu; artık
        iki görünümde de aynı kalmalı."""
        self._load_and_plot("px4_hexarotor_flight.ulg")
        self.assertEqual(self.app.ax_current.get_ylabel(), "Toplam Akım (A)")
        self.assertEqual(self.app.ax_motors.get_ylabel(), "Motor Akımı (A)")

        self.app.battery_view_toggle.set("Isı Haritası")
        self.app._on_battery_view_change("Isı Haritası")
        self.app.motor_view_toggle.set("Isı Haritası")
        self.app._on_motor_view_change("Isı Haritası")

        self.assertEqual(self.app.ax_current.get_ylabel(), "Toplam Akım (A)")
        self.assertEqual(self.app.ax_motors.get_ylabel(), "Motor Akımı (A)")

    def test_navigation_toolbar_is_created(self):
        from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
        self.assertIsInstance(self.app.nav_toolbar, NavigationToolbar2Tk)

    def test_pan_view_persists_until_switching_tools(self):
        """Pan aracı açıkken yapılan sürüklemeler kalıcıdır (fareyi bırakmak
        eski görünüme dönmez); sadece başka bir araca (ör. Yakınlaştır)
        geçildiğinde Pan'a girmeden önceki görünüme dönülür. Gerçek GUI
        event zincirini tetiklemek yerine (event.x/y, canvas etkileşimi
        gerektirdiği için kırılgan olur) `pan()`/`zoom()`'un çağırdığı
        snapshot+restore mantığı doğrudan test ediliyor."""
        self._load_and_plot("synthetic_test_log.BIN")
        toolbar = self.app.nav_toolbar
        original_xlim = self.app.ax_voltage.get_xlim()

        toolbar.pan()  # Pan aracını aç -> o anki görünüm kaydedilir
        self.assertIsNotNone(toolbar._pre_pan_view)

        # Sürükleme simülasyonu: xlim'i gerçek bir pan hareketi gibi değiştir.
        shifted_xlim = (original_xlim[0] + 5, original_xlim[1] + 5)
        self.app.ax_voltage.set_xlim(*shifted_xlim)

        toolbar.pan()  # Pan'ı aynı simgeyle kapat -> görünüm KALICI kalmalı
        self.assertEqual(self.app.ax_voltage.get_xlim(), shifted_xlim)

        toolbar.zoom()  # Başka bir araca geç -> kaydedilen görünüme dönülmeli
        self.assertEqual(self.app.ax_voltage.get_xlim(), original_xlim)

    def test_hover_tooltip_shows_and_hides(self):
        """_on_plot_hover, fare bir çizginin üzerindeyken bir annotation
        çizmeli (ax.texts'e eklenir); fare axes dışına çıkınca (inaxes=None)
        önceki annotation kaldırılmalı. Gerçek bir mouse event'i simüle etmek
        yerine matplotlib'in event nesnesiyle aynı arayüzü (inaxes/xdata/
        ydata) taklit eden basit bir SimpleNamespace kullanılıyor."""
        self._load_and_plot("synthetic_test_log.BIN")
        line = self.app.ax_voltage.get_lines()[0]
        x0, y0 = line.get_xdata()[0], line.get_ydata()[0]

        hover_event = types.SimpleNamespace(inaxes=self.app.ax_voltage, xdata=x0, ydata=y0)
        self.app._on_plot_hover(hover_event)
        self.assertEqual(len(self.app.ax_voltage.texts), 1)

        leave_event = types.SimpleNamespace(inaxes=None, xdata=None, ydata=None)
        self.app._on_plot_hover(leave_event)
        self.assertEqual(len(self.app.ax_voltage.texts), 0)

    def test_export_pdf_creates_file(self):
        self._load_and_plot("synthetic_test_log.BIN")
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = str(Path(tmp_dir) / "rapor.pdf")
            with patch("main.filedialog.asksaveasfilename", return_value=out_path):
                self.app._on_export_pdf_click()
            self.assertTrue(Path(out_path).exists())
            self.assertGreater(Path(out_path).stat().st_size, 0)

    def test_export_csv_creates_file_with_expected_rows(self):
        data = self._load_and_plot("synthetic_test_log.BIN")
        expected_rows = sum(len(b["time_s"]) for b in data["batteries"]) + \
            sum(len(m["time_s"]) for m in data["motors"])
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = str(Path(tmp_dir) / "veri.csv")
            with patch("main.filedialog.asksaveasfilename", return_value=out_path):
                self.app._on_export_csv_click()
            lines = Path(out_path).read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(lines[0], "tip,id,zaman_s,voltaj_v,akim_a")
            self.assertEqual(len(lines) - 1, expected_rows)

    def test_energy_and_power_stats_shown_for_real_flight(self):
        """Gerçek bir uçuş yüklendiğinde enerji/tepe güç/iç direnç
        kutucukları placeholder ('—') yerine gerçek bir değer göstermeli."""
        self._load_and_plot("ArduCopter-MaxAltFence-00000067.BIN")
        self.assertNotEqual(self.app.stat_labels["energy_wh"].cget("text"), "—")
        self.assertIn("Wh", self.app.stat_labels["energy_wh"].cget("text"))
        self.assertIn("W", self.app.stat_labels["peak_power_w"].cget("text"))
        self.assertIn("mΩ", self.app.stat_labels["resistance_est"].cget("text"))

    def test_resistance_estimate_shows_placeholder_when_current_is_constant(self):
        """Akım hemen hemen sabitse (varyasyon eşiğin altında) regresyon
        anlamsızlaşır; kutucuk '—' göstermeli, uydurma bir sayı değil."""
        self.app._update_stats([
            {"id": 1, "time_s": [0.0, 1.0, 2.0], "voltage_v": [16.8, 16.7, 16.6],
             "current_a": [10.0, 10.05, 10.02]},
        ])
        self.assertEqual(self.app.stat_labels["resistance_est"].cget("text"), "—")

    def test_capacity_and_remaining_stats_shown_for_real_flight(self):
        """Bu örnek log CurrTot/RemPct alanlarını içeriyor (bkz.
        shared/power_log_schema.md); kutucuklar '—' değil gerçek bir değer
        göstermeli."""
        self._load_and_plot("ArduCopter-MaxAltFence-00000067.BIN")
        self.assertIn("mAh", self.app.stat_labels["capacity_used"].cget("text"))
        self.assertIn("dk", self.app.stat_labels["remaining_time"].cget("text"))

    def test_capacity_stats_show_placeholder_when_fields_absent(self):
        self.app._update_capacity_stats([
            {"id": 1, "time_s": [0.0, 1.0], "voltage_v": [16.8, 16.5], "current_a": [10.0, 10.0]},
        ])
        self.assertEqual(self.app.stat_labels["capacity_used"].cget("text"), "—")
        self.assertEqual(self.app.stat_labels["remaining_time"].cget("text"), "—")

    def test_negative_current_colors_stat_tile_as_warning(self):
        """min(all_current) < 0 olduğunda 'Akım Aralığı' kartının rengi uyarı
        rengi olmalı; aksi halde birincil metin rengi. CTk widget'ları canlı
        tema geçişi için (açık, koyu) renk ÇİFTİ tutar, o yüzden tekil sabit
        değil UI_* çiftiyle karşılaştırılır."""
        self.app._update_stats([
            {"id": 1, "time_s": [0.0, 1.0], "voltage_v": [16.8, 16.5], "current_a": [10.0, -0.7]},
        ])
        self.assertEqual(
            self.app.stat_labels["current_range"].cget("text_color"),
            frontend_main.UI_COLOR_WARNING,
        )

        self.app._update_stats([
            {"id": 1, "time_s": [0.0, 1.0], "voltage_v": [16.8, 16.5], "current_a": [10.0, 5.0]},
        ])
        self.assertEqual(
            self.app.stat_labels["current_range"].cget("text_color"),
            frontend_main.UI_TEXT_PRIMARY,
        )

    def test_pdf_report_includes_all_stat_tiles(self):
        """STAT_TILE_DEFINITIONS'daki her başlık PDF özet sayfasında da
        görünmeli — bu, geçen turda enerji/kapasite gibi yeni kutucukların
        PDF'e eklenmeyi unutulduğu hatanın regresyon testi."""
        self._load_and_plot("synthetic_test_log.BIN")
        fig = self.app._build_report_summary_figure()
        all_text = " ".join(t.get_text() for t in fig.texts)
        for _key, title in frontend_main.STAT_TILE_DEFINITIONS:
            self.assertIn(title, all_text)

    def test_loading_disables_controls_until_finished(self):
        """_load_file, arka plan thread'i başlatır başlatmaz (Tk event loop
        hiç dönmeden, dolayısıyla ırk koşulu olmadan) _is_loading True ve
        yükle butonu devre dışı olmalı; yükleme bitince ikisi de eski haline
        dönmeli, ilerleme çubuğu da gizlenmiş olmalı. winfo_ismapped()'ın
        pencere gerçekleşme zamanlamasına bağlı olabileceği için (ör. ilk
        update() çağrısı çok uzun sürüp 100ms'lik zamanlayıcıyı erken
        tetikleyebilir) 'hâlâ yükleniyor' anında sadece Tk event loop
        gerektirmeyen düz Python durumu kontrol edilir."""
        self.app._load_file(str(DATA_DIR / "synthetic_test_log.BIN"))
        self.assertTrue(self.app._is_loading)
        self.assertEqual(self.app.load_button.cget("state"), "disabled")

        self._wait_for_load()
        self.assertFalse(self.app._is_loading)
        self.assertEqual(self.app.load_button.cget("state"), "normal")
        self.assertFalse(self.app.loading_progress.winfo_ismapped())

    def test_theme_toggle_switches_live_and_preserves_loaded_data(self):
        """Tema butonu uygulamayı kapatıp açmaz VE widget ağacını yeniden
        KURMAZ: renkleri yerinde değiştirir (CTk widget'ları UI_* renk
        çiftleriyle kurulduğu için ctk otomatik geçiş yapar). Kanıt olarak
        analysis_frame'in AYNI nesne kaldığı doğrulanır (yok edilip yeniden
        yaratılsaydı kimliği değişirdi). Yüklü veri (grafik/istatistikler) ve
        analiz ekranında olunduğu da korunmalı. _load_and_plot yerine
        _load_file_and_wait kullanılıyor çünkü ilki (bilerek) analiz ekranına
        hiç geçmiyor, sadece çizim mantığını izole test ediyor."""
        self._load_file_and_wait(str(DATA_DIR / "synthetic_test_log.BIN"))
        expected_new_theme = "light" if frontend_main.ACTIVE_THEME == "dark" else "dark"
        frame_before = self.app.analysis_frame

        self.app._on_theme_toggle_click()
        self.app.update()

        self.assertEqual(frontend_main._load_settings()["theme"], expected_new_theme)
        self.assertEqual(frontend_main.ACTIVE_THEME, expected_new_theme)
        self.assertEqual(frontend_main.ctk.get_appearance_mode().lower(), expected_new_theme)
        # Ağaç yeniden kurulmadı: aynı frame nesnesi, hâlâ ekranda.
        self.assertIs(self.app.analysis_frame, frame_before)
        self.assertTrue(self.app.analysis_frame.winfo_ismapped())
        self.assertGreater(len(self._data_lines(self.app.ax_voltage)), 0)
        self.assertNotEqual(self.app.stat_labels["energy_wh"].cget("text"), "—")

    def test_theme_toggle_preserves_landing_screen(self):
        """Dosya yüklenmemişken tema değiştirilirse giriş ekranında kalınmalı."""
        self.app.update()
        self.app._on_theme_toggle_click()
        self.app.update()
        self.assertTrue(self.app.landing_frame.winfo_ismapped())
        self.assertFalse(self.app.analysis_frame.winfo_ismapped())

    def test_theme_toggle_cancels_previous_nav_toolbar_loop(self):
        """_style_nav_toolbar kendini sonsuza kadar yeniden zamanlıyor; her
        tema değişiminde eski döngü iptal edilip yenisiyle değiştirilmeli,
        yoksa her toggle'da bir tane daha birikip sonsuza kadar büyüyen bir
        arka plan döngüsü listesi oluşurdu."""
        self._load_and_plot("synthetic_test_log.BIN")
        first_id = self.app._nav_toolbar_after_id
        self.assertIsNotNone(first_id)

        self.app._on_theme_toggle_click()
        self.assertIsNotNone(self.app._nav_toolbar_after_id)
        self.assertNotEqual(first_id, self.app._nav_toolbar_after_id)

        # Art arda birkaç kez değiştirmek de hata vermemeli.
        self.app._on_theme_toggle_click()
        self.app._on_theme_toggle_click()

    def test_voltage_temperature_toggle_shows_temperature_data(self):
        """px4_hexarotor_flight.ulg gerçek (sıfır olmayan) sıcaklık verisi
        içeriyor (bkz. shared/power_log_schema.md temperature_c); Sıcaklık
        görünümüne geçince en az bir çizgi çizilmeli ve panel başlığı
        değişmeli, Voltaj'a dönünce eski haline gelmeli."""
        self._load_and_plot("px4_hexarotor_flight.ulg")

        self.app._on_voltage_view_change("Sıcaklık")
        self.assertIn("°C", self.app.ax_voltage.get_ylabel())
        self.assertGreater(len(self.app.ax_voltage.get_lines()), 0)

        self.app._on_voltage_view_change("Voltaj")
        self.assertIn("V", self.app.ax_voltage.get_ylabel())
        self.assertGreater(len(self._data_lines(self.app.ax_voltage)), 0)

    def test_voltage_temperature_toggle_shows_message_when_no_data(self):
        """synthetic_test_log.BIN'in BAT tanımında Temp alanı yok; Sıcaklık
        görünümü boş grafik yerine açık bir durum mesajı göstermeli."""
        self._load_and_plot("synthetic_test_log.BIN")

        self.app._on_voltage_view_change("Sıcaklık")
        self.assertEqual(len(self.app.ax_voltage.get_lines()), 0)
        texts = [t.get_text() for t in self.app.ax_voltage.texts]
        self.assertTrue(any("sıcaklık verisi yok" in t.lower() for t in texts))

    def test_voltage_sag_threshold_line_is_drawn_per_battery(self):
        data = self._load_and_plot("synthetic_test_log.BIN")
        total_lines = len(self.app.ax_voltage.get_lines())
        data_lines = len(self._data_lines(self.app.ax_voltage))
        self.assertEqual(total_lines - data_lines, len(data["batteries"]))

    def test_current_imbalance_band_only_when_multiple_batteries(self):
        data = self._load_and_plot("synthetic_test_log.BIN")
        has_band = len(self.app.ax_current.patches) >= 1
        self.assertEqual(has_band, len(data["batteries"]) >= 2)


if __name__ == "__main__":
    unittest.main()
