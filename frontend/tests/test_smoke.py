"""Frontend'in App sınıfı için duman (smoke) testleri.

Gerçek bir pencere açıp backend'i çalıştırır, grafik çizimi sırasında
exception fırlamadığını ve çizilen çizgi sayısının beklenen batarya sayısıyla
eştiğini doğrular. CustomTkinter/Tkinter bir display gerektirdiği için CI'da
xvfb altında çalıştırılmalı (bkz. .github/workflows/tests.yml).

Kullanım: python test_smoke.py  (frontend/tests/ içinden)
"""

import itertools
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

FRONTEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = FRONTEND_DIR.parent / "data"
sys.path.insert(0, str(FRONTEND_DIR))
# backend testleriyle AYNI sentetik .bin üretici — 3+ batarya gibi fixture'lar
# için byte inşa mantığını burada tekrar yazmamak adına yeniden kullanılıyor.
sys.path.insert(0, str(FRONTEND_DIR.parent / "backend" / "tests"))

import main as frontend_main
import make_synthetic_log


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

    def test_series_palette_is_per_theme(self):
        """SERIES_COLORS tek liste olarak iki temada da kullanılıyordu; koyu
        zemine göre seçilmiş tonlar açık temada soluk kalıp birbirine
        karışıyordu (kullanıcı geri bildirimi). Artık PWM_DEVIATION_COLORS
        deseniyle tema başına ayrı: açık palet daha koyu/doygun, slot
        kimliği korunur (Batarya 1 iki temada da mavi)."""
        light = frontend_main.LIGHT_PALETTE["SERIES_COLORS"]
        dark = frontend_main.DARK_PALETTE["SERIES_COLORS"]
        self.assertEqual(len(light), 8)
        self.assertEqual(len(dark), 8)
        self.assertEqual(len(set(light)), 8)  # açık palette tekrar yok
        self.assertNotEqual(light, dark)  # asıl hata: tek ortak liste

    def test_heatmap_colors_are_per_theme(self):
        """HEATMAP_COLORS'ın düşük ucu (#141a22) koyu yüzeye göre seçilmişti
        ve import zamanında sabitlenip tema geçişinde hiç güncellenmiyordu —
        açık temada ısı haritasının 'düşük' bölgesi siyah bir leke gibi
        görünüyordu. Artık tema başına tanımlı ve toggle'da rebind ediliyor."""
        light = frontend_main.LIGHT_PALETTE["HEATMAP_COLORS"]
        dark = frontend_main.DARK_PALETTE["HEATMAP_COLORS"]
        self.assertEqual(len(light), 3)
        self.assertEqual(len(dark), 3)
        self.assertNotEqual(light[0], "#141a22")  # açık düşük uç koyu-yüzey tonu olamaz

    def test_series_style_follows_rebound_palette(self):
        """_series_style modül globalini ÇAĞRI ANINDA okumalı — tema toggle'ı
        SERIES_COLORS'ı yeniden bağladığında ilk serinin rengi yeni paletten
        gelmeli (gelecekte birinin _series_style içine renkleri sabitlemesine
        karşı bekçi)."""
        original = frontend_main.SERIES_COLORS
        try:
            frontend_main.SERIES_COLORS = frontend_main.LIGHT_PALETTE["SERIES_COLORS"]
            self.assertEqual(frontend_main._series_style(0)[0], "#2a78d6")
            frontend_main.SERIES_COLORS = frontend_main.DARK_PALETTE["SERIES_COLORS"]
            self.assertEqual(frontend_main._series_style(0)[0], "#3987e5")
        finally:
            frontend_main.SERIES_COLORS = original

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

    def test_export_menu_triggers_handler_and_resets_label(self):
        """Dışa aktarma menüsü bir seçim değil eylem listesi: seçilen biçimin
        işleyicisi çalışmalı ve etiket hemen geri dönmeli, yoksa menüde
        "şu an CSV modundayım" gibi yanlış bir izlenim kalır."""
        self._load_and_plot("synthetic_test_log.BIN")
        self.assertEqual(self.app.export_menu.get(), frontend_main.EXPORT_MENU_LABEL)

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = str(Path(tmp_dir) / "veri.csv")
            with patch("main.filedialog.asksaveasfilename", return_value=out_path):
                self.app._on_export_selected("Ham veri (CSV)")
            self.assertTrue(Path(out_path).exists())

        self.assertEqual(self.app.export_menu.get(), frontend_main.EXPORT_MENU_LABEL)

    def test_export_menu_lists_every_supported_format(self):
        values = self.app.export_menu.cget("values")
        self.assertEqual(list(values), list(frontend_main.EXPORT_MENU_ACTIONS))
        for handler in frontend_main.EXPORT_MENU_ACTIONS.values():
            self.assertTrue(hasattr(self.app, handler), handler)

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

    def test_backend_failure_shows_error_status_and_re_enables_controls(self):
        """_poll_backend_result'ın "error" dalı (main.py:2717-2720) hiç test
        edilmemişti — mevcut tüm testler sadece "ok" yolunu sürüyordu.
        Backend gerçekten başarısız olduğunda (bozuk/tanınmayan dosya)
        status_label kırmızı "⚠ Hata: ..." göstermeli ve load_button tekrar
        etkinleşmeli (aksi halde kullanıcı arayüzde sıkışıp kalır).

        recent_menu burada KASITLI olarak kontrol edilmiyor: bu testte hiç
        geçmiş dosya yok, bu yüzden hata sonrası "disabled" kalması zaten
        doğru davranış (_refresh_recent_menu boş listede öyle davranıyor)."""
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(b"bu gecerli bir log dosyasi degil")
            path = Path(f.name)
        try:
            self._load_file_and_wait(str(path))
            self.assertIn("Hata", self.app.status_label.cget("text"))
            self.assertEqual(self.app.status_label.cget("text_color"), frontend_main.UI_COLOR_CRITICAL)
            self.assertEqual(self.app.load_button.cget("state"), "normal")
        finally:
            path.unlink(missing_ok=True)

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

    def test_landing_screen_text_stays_within_window_at_minimum_width(self):
        """Regresyon: footer, "center" çerçevesinden SONRA pack edildiğinde
        (side="bottom"), pack cavity center'ın expand=True ile alacağı payı
        daraltıyordu (700px pencerede center 410 yerine 200px kalıyordu) —
        başlık/açıklama sidebar'ın altına gömülüp pencere kenarından taşıyordu.
        minsize (700px) genişlikte hiçbir metin ne sidebar'ın altında ne de
        pencere dışında kalmamalı (bkz. _build_landing_screen, _on_landing_frame_configure)."""
        self.app.deiconify()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.geometry("700x750")
            self.app.update_idletasks()
            self.app.update()
            if abs(self.app.landing_frame.winfo_width() - 700) <= 4:
                break
        self.app.update()

        window_x = self.app.winfo_rootx()
        window_width = self.app.landing_frame.winfo_width()

        for label in (self.app.landing_title_label, self.app.landing_desc_label,
                      self.app.landing_footer_label):
            left = label.winfo_rootx() - window_x
            right = left + label.winfo_width()
            self.assertGreaterEqual(
                left, frontend_main.LANDING_SIDEBAR_WIDTH,
                f"{label.cget('text')!r} sidebar'ın altına gömülmüş (left={left})")
            self.assertLessEqual(
                right, window_width,
                f"{label.cget('text')!r} pencere kenarından taşıyor (right={right}, width={window_width})")

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

    def _hover_text(self, ax, xdata, ydata):
        """Verilen noktada hover tetikleyip tooltip metnini döndürür
        (tooltip çıkmazsa None)."""
        self.app._on_plot_hover(
            types.SimpleNamespace(inaxes=ax, xdata=xdata, ydata=ydata)
        )
        return self.app._hover_annotation.get_text() if self.app._hover_annotation else None

    def test_hover_tooltip_on_motor_heatmap_names_cell(self):
        """Isı haritasında eksenin hiç çizgisi yok; tooltip hücreyi çizim
        sırasında saklanan ızgaradan okumalı. y=1 ilk satırın merkezidir
        (imshow extent'i 0.5'ten başlıyor)."""
        self._load_and_plot("px4_hexarotor_flight.ulg")
        self.app.motor_view_toggle.set("Isı Haritası")
        self.app._on_motor_view_change("Isı Haritası")
        self.assertEqual(len(self.app.ax_motors.get_lines()), 0)  # okunacak çizgi yok

        first_label = self.app.ax_motors.get_yticklabels()[0].get_text()
        text = self._hover_text(self.app.ax_motors, self.app._motors_heatmap["times"][3], 1.0)

        self.assertIsNotNone(text)
        self.assertIn(first_label, text)
        self.assertIn(" A", text)

    def test_hover_tooltip_on_battery_heatmap_names_cell(self):
        self._load_and_plot("px4_hexarotor_flight.ulg")
        self.app._on_battery_view_change("Isı Haritası")

        text = self._hover_text(self.app.ax_current, self.app._current_heatmap["times"][2], 1.0)

        self.assertIsNotNone(text)
        self.assertIn("Batarya", text)
        self.assertIn(" A", text)

    def test_hover_tooltip_on_heatmap_reports_the_hovered_cells_value(self):
        """Gösterilen sayı, imlecin altındaki satır/sütunun ızgaradaki
        değeriyle birebir aynı olmalı — komşu hücreninki değil."""
        self.app._last_pwm_outputs = [
            {"id": 1, "label": "AUX 1", "time_s": [0.0, 10.0], "pwm_us": [1400.0, 1400.0]},
            {"id": 2, "label": "AUX 2", "time_s": [0.0, 10.0], "pwm_us": [1600.0, 1600.0]},
        ]
        self.app.motor_view_mode = "pwm_deviation"
        self.app._plot_motor_currents([])

        # AUX ortalaması 1500; 2. satır (AUX 2) +100 µs sapmalı olmalı.
        text = self._hover_text(self.app.ax_motors, 0.0, 2.0)
        self.assertIn("AUX 2", text)
        self.assertIn("+100.0 µs sapma", text)
        # Mutlak PWM de yazılıyor: bu görünüm onu skalada gizliyor ama tek
        # hücreye bakarken değerli.
        self.assertIn("(1600 µs)", text)

        text = self._hover_text(self.app.ax_motors, 0.0, 1.0)
        self.assertIn("AUX 1", text)
        self.assertIn("-100.0 µs sapma", text)

    def test_hover_tooltip_on_heatmap_picks_nearest_time_column(self):
        """searchsorted ekleme noktasını verir; düzeltilmezse tooltip hep
        sağdaki sütunu gösterirdi. t=1.0'a en yakın örnek 0.0 değil 2.0."""
        self.app._last_pwm_outputs = [
            {"id": 1, "label": "AUX 1", "time_s": [0.0, 2.0], "pwm_us": [1400.0, 1000.0]},
            {"id": 2, "label": "AUX 2", "time_s": [0.0, 2.0], "pwm_us": [1600.0, 2000.0]},
        ]
        self.app.motor_view_mode = "pwm_deviation"
        self.app._plot_motor_currents([])

        self.assertIn("(1000 µs)", self._hover_text(self.app.ax_motors, 1.1, 1.0))
        self.assertIn("(1400 µs)", self._hover_text(self.app.ax_motors, 0.9, 1.0))

    def test_hover_tooltip_on_heatmap_ignores_rows_outside_the_grid(self):
        """imshow extent'i 0.5..n+0.5; bu aralığın dışına denk gelen bir y
        (ör. eksenin üst boşluğu) tooltip üretmemeli, IndexError de atmamalı."""
        self._load_and_plot("px4_hexarotor_flight.ulg")
        self.app.motor_view_toggle.set("Isı Haritası")
        self.app._on_motor_view_change("Isı Haritası")
        row_count = len(self.app._motors_heatmap["labels"])

        self.assertIsNone(self._hover_text(self.app.ax_motors, 1.0, row_count + 3.0))
        self.assertIsNone(self._hover_text(self.app.ax_motors, 1.0, -2.0))

    def test_hover_heatmap_data_is_cleared_when_returning_to_line_view(self):
        """Isı haritasından çizgi görünümüne dönünce saklanan ızgara
        bırakılmalı; yoksa çizgi grafiğinde eski hücreler gösterilirdi."""
        self._load_and_plot("px4_hexarotor_flight.ulg")
        self.app.motor_view_toggle.set("Isı Haritası")
        self.app._on_motor_view_change("Isı Haritası")
        self.assertIsNotNone(self.app._motors_heatmap)

        self.app._on_motor_view_change("Çizgi Grafiği")
        self.assertIsNone(self.app._motors_heatmap)

    def test_hover_tooltip_unit_follows_the_selected_view(self):
        """Tooltip birimi panelin o anki içeriğine göre değişmeli: sıcaklık
        modunda °C, PWM çizgi modunda µs (ikisi de eskiden V/A yazıyordu)."""
        self._load_and_plot("px4_hexarotor_flight.ulg")

        self.app._on_voltage_view_change("Sıcaklık")
        line = self.app.ax_voltage.get_lines()[0]
        text = self._hover_text(self.app.ax_voltage, line.get_xdata()[0], line.get_ydata()[0])
        self.assertIn("°C", text)

        self.app.motor_view_toggle.set("PWM Çıkışı")
        self.app._on_motor_view_change("PWM Çıkışı")
        line = self.app.ax_motors.get_lines()[0]
        text = self._hover_text(self.app.ax_motors, line.get_xdata()[0], line.get_ydata()[0])
        # Etiket "MAIN 1" gibi harf içerebildiği için metnin tamamında değil,
        # değerin bittiği yerde kontrol ediliyor.
        self.assertTrue(text.endswith(" µs"), text)

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
            sum(len(m["time_s"]) for m in data["motors"]) + \
            sum(len(p["time_s"]) for p in data["pwm_outputs"])
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = str(Path(tmp_dir) / "veri.csv")
            with patch("main.filedialog.asksaveasfilename", return_value=out_path):
                self.app._on_export_csv_click()
            lines = Path(out_path).read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(lines[0], "tip,id,zaman_s,voltaj_v,akim_a,pwm_us")
            self.assertEqual(len(lines) - 1, expected_rows)

    def test_export_csv_includes_pwm_rows(self):
        """PWM kanalları CSV'ye "pwm" tipiyle ve id sütununda sayı yerine
        kanal etiketiyle ("MAIN 2") yazılır."""
        data = self._load_and_plot("px4_ground_rover_flight.ulg")
        self.assertGreater(len(data["pwm_outputs"]), 0)
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = str(Path(tmp_dir) / "veri.csv")
            with patch("main.filedialog.asksaveasfilename", return_value=out_path):
                self.app._on_export_csv_click()
            lines = Path(out_path).read_text(encoding="utf-8").strip().splitlines()
        pwm_lines = [line for line in lines if line.startswith("pwm,")]
        self.assertGreater(len(pwm_lines), 0)
        self.assertIn("MAIN", pwm_lines[0])

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

    # --- Akım sensörü olmayan araçlar (rover) ---------------------------------
    #
    # data/px4_ground_rover_flight.ulg gerçek bir PX4 rover logu: voltajı
    # sağlıklı (11.01-12.10 V) ama akım sensörü hiç bağlı değil (452 örneğin
    # hepsinde current_a = 0.00) ve esc_status konusu logda yok. Eskiden bu
    # "ölçüm yok" durumu "ölçüm sıfır" gibi gösteriliyordu: düz sıfır çizgisi,
    # 0.0 Wh enerji, 0 W tepe güç. Aşağıdaki testler bunun regresyonu.

    def _rover_axis_texts(self, ax) -> str:
        return " ".join(t.get_text() for t in ax.texts).lower()

    def test_rover_log_shows_message_instead_of_zero_current_line(self):
        data = self._load_and_plot("px4_ground_rover_flight.ulg")
        self.assertFalse(data["batteries"][0]["has_current_data"])

        self.assertEqual(len(self.app.ax_current.get_lines()), 0)
        self.assertIn("akım sensörü verisi yok", self._rover_axis_texts(self.app.ax_current))

    def test_rover_log_shows_message_in_motor_panel(self):
        """Bu logda esc_status konusu hiç yok; motor paneli sessizce boş
        kalmak yerine nedenini yazmalı."""
        data = self._load_and_plot("px4_ground_rover_flight.ulg")
        self.assertEqual(len(data["motors"]), 0)

        self.assertEqual(len(self.app.ax_motors.get_lines()), 0)
        self.assertIn("motor (esc) akım verisi yok", self._rover_axis_texts(self.app.ax_motors))

    def test_rover_log_shows_message_in_heatmap_view_too(self):
        """Isı haritası görünümünde ölçümsüz batarya tek renk bir şerit olarak
        çıkıyordu ("hep aynı yük" gibi); orada da mesaj gösterilmeli."""
        self._load_and_plot("px4_ground_rover_flight.ulg")

        self.app.battery_view_toggle.set("Isı Haritası")
        self.app._on_battery_view_change("Isı Haritası")
        self.assertEqual(len(self.app.ax_current.get_images()), 0)
        self.assertIn("akım sensörü verisi yok", self._rover_axis_texts(self.app.ax_current))

        self.app.motor_view_toggle.set("Isı Haritası")
        self.app._on_motor_view_change("Isı Haritası")
        self.assertEqual(len(self.app.ax_motors.get_images()), 0)

    def test_rover_log_current_based_stats_show_placeholder(self):
        """Akıma dayanan kutucuklar 0 değil '—' göstermeli; voltaj/süre gibi
        gerçekten ölçülmüş olanlar ise normal değerlerini korumalı."""
        self._load_and_plot("px4_ground_rover_flight.ulg")

        for key in ("current_range", "energy_wh", "peak_power_w", "resistance_est",
                    "capacity_used", "remaining_time"):
            self.assertEqual(self.app.stat_labels[key].cget("text"), "—", f"{key} '—' olmalıydı")

        self.assertIn("V", self.app.stat_labels["voltage_range"].cget("text"))
        self.assertIn("s", self.app.stat_labels["duration"].cget("text"))

    def test_rover_log_produces_no_false_warnings(self):
        data = self._load_and_plot("px4_ground_rover_flight.ulg")
        self.assertEqual(data["warnings"], [])

    def test_vehicle_type_label_appended_to_file_name(self):
        """_load_and_plot yerine _load_file_and_wait kullanılıyor çünkü
        etiket dosya adının yanına ekleniyor, o da _load_file'da atanıyor."""
        self._load_file_and_wait(str(DATA_DIR / "px4_ground_rover_flight.ulg"))
        self.assertEqual(
            self.app.file_label.cget("text"), "px4_ground_rover_flight.ulg · Rover"
        )

    def test_vehicle_type_label_omitted_when_unknown(self):
        """Sentetik logda araç tipi bilgisi yok ("unknown"); etiket
        uydurulmamalı, sadece dosya adı kalmalı."""
        self._load_file_and_wait(str(DATA_DIR / "synthetic_test_log.BIN"))
        self.assertEqual(self.app.file_label.cget("text"), "synthetic_test_log.BIN")

    # --- Akım sensörü OLAN rover (ArduPilot tarafı) ---------------------------
    #
    # data/Rover-Scripting-00000036.BIN gerçek bir ArduRover logu ve yukarıdaki
    # PX4 rover'ının TERSİ: aynı araç tipi ama akım sensörü var. Bu ikili,
    # panellerin araç tipine göre değil verinin varlığına göre dallanması
    # kararının kanıtı — tek bir rover loguyla iki davranış da doğrulanamazdı.

    ARDUROVER_LOG = "Rover-Scripting-00000036.BIN"

    def test_ardupilot_rover_is_labelled_rover_too(self):
        self._load_file_and_wait(str(DATA_DIR / self.ARDUROVER_LOG))
        self.assertEqual(
            self.app._file_label_text, f"{self.ARDUROVER_LOG} · Rover"
        )

    def test_ardupilot_rover_draws_current_unlike_the_px4_rover(self):
        """Akım paneli burada durum mesajı DEĞİL gerçek bir çizgi göstermeli."""
        self._load_and_plot(self.ARDUROVER_LOG)
        self.assertEqual(len(self.app.ax_current.get_lines()), 1)
        self.assertNotIn("akım sensörü verisi yok",
                         self._rover_axis_texts(self.app.ax_current))

    def test_ardupilot_rover_current_based_stats_have_real_values(self):
        """PX4 rover'ında bu dört kutucuk '—' idi; burada sayı olmalı."""
        self._load_and_plot(self.ARDUROVER_LOG)
        for key in ("current_range", "energy_wh", "peak_power_w", "resistance_est"):
            self.assertNotEqual(self.app.stat_labels[key].cget("text"), "—", key)

    def test_ardupilot_rover_still_has_no_motor_current(self):
        """Akım sensörü olması ESC telemetrisi olduğu anlamına gelmiyor:
        batarya akımı ölçülüyor ama motor paneli yine boş."""
        data = self._load_and_plot(self.ARDUROVER_LOG)
        self.assertEqual(data["motors"], [])
        self.assertIn("motor (esc) akım verisi yok",
                      self._rover_axis_texts(self.app.ax_motors))

    def test_ardupilot_rover_pwm_channels_are_plotted(self):
        """RCOU'dan gelen iki hareketli kanal ("Kanal N" etiketiyle, çünkü
        ArduPilot'ta MAIN/AUX ayrımı yok) çizilmeli."""
        self._load_and_plot(self.ARDUROVER_LOG)
        self.app.motor_view_toggle.set("PWM Çıkışı")
        self.app._on_motor_view_change("PWM Çıkışı")

        lines = self.app.ax_motors.get_lines()
        self.assertEqual([line.get_label() for line in lines], ["Kanal 1", "Kanal 3"])
        self.assertIn("µs", self.app.ax_motors.get_ylabel())

    def test_ardupilot_rover_exports_do_not_crash(self):
        self._load_and_plot(self.ARDUROVER_LOG)
        with tempfile.TemporaryDirectory() as tmp_dir:
            for method, suffix in (("_on_export_png_click", ".png"),
                                   ("_on_export_pdf_click", ".pdf"),
                                   ("_on_export_csv_click", ".csv")):
                out_path = str(Path(tmp_dir) / f"rover{suffix}")
                with patch("main.filedialog.asksaveasfilename", return_value=out_path):
                    getattr(self.app, method)()
                self.assertGreater(Path(out_path).stat().st_size, 0, suffix)

    # --- PWM çıkış görünümü ---------------------------------------------------

    def test_pwm_view_plots_channels_for_rover(self):
        """Rover'da motor akımı hiç yok ama iki hareketli PWM kanalı var;
        "PWM Çıkışı" modu bunları çizmeli. (Hangisinin gaz hangisinin
        direksiyon olduğu logda yazmıyor — bkz. şemadaki "kanal ≠ motor"
        kuralı — o yüzden burada da eşleme varsayılmıyor.)"""
        data = self._load_and_plot("px4_ground_rover_flight.ulg")
        self.assertEqual(len(data["pwm_outputs"]), 2)

        self.app.motor_view_toggle.set("PWM Çıkışı")
        self.app._on_motor_view_change("PWM Çıkışı")

        lines = self.app.ax_motors.get_lines()
        self.assertEqual(len(lines), 2)
        self.assertIn("µs", self.app.ax_motors.get_ylabel())
        self.assertEqual([line.get_label() for line in lines], ["MAIN 2", "MAIN 4"])

    def test_pwm_view_shows_six_motors_for_hexarotor(self):
        """Hexarotor'un 6 motoru AUX grubunda; MAIN'deki iki hareketli kanal
        da (servo/gimbal) ayrıca listelenir. Motor tahmini yapılmadığının,
        kanalların oldukları gibi aktarıldığının kanıtı."""
        self._load_and_plot("px4_hexarotor_flight.ulg")

        self.app.motor_view_toggle.set("PWM Çıkışı")
        self.app._on_motor_view_change("PWM Çıkışı")

        labels = [line.get_label() for line in self.app.ax_motors.get_lines()]
        for channel in range(1, 7):
            self.assertIn(f"AUX {channel}", labels)

    def test_pwm_view_shows_message_when_no_data(self):
        """synthetic_test_log.BIN'de RCOU mesajı yok; panel sessizce boş
        kalmak yerine durum mesajı göstermeli."""
        data = self._load_and_plot("synthetic_test_log.BIN")
        self.assertEqual(data["pwm_outputs"], [])

        self.app.motor_view_toggle.set("PWM Çıkışı")
        self.app._on_motor_view_change("PWM Çıkışı")

        self.assertEqual(len(self.app.ax_motors.get_lines()), 0)
        texts = [t.get_text().lower() for t in self.app.ax_motors.texts]
        self.assertTrue(any("pwm çıkış verisi yok" in t for t in texts))

    def test_switching_across_all_three_motor_views(self):
        """Üç mod arasında ileri geri geçiş hata vermemeli ve her mod kendi
        içeriğini göstermeli (ısı haritası görüntü, diğerleri çizgi)."""
        self._load_and_plot("px4_hexarotor_flight.ulg")

        for label, expect in (
            ("Isı Haritası", "image"), ("PWM Çıkışı", "line"),
            ("Çizgi Grafiği", "line"), ("PWM Çıkışı", "line"), ("Isı Haritası", "image"),
        ):
            self.app.motor_view_toggle.set(label)
            self.app._on_motor_view_change(label)
            if expect == "image":
                self.assertEqual(len(self.app.ax_motors.get_images()), 1, label)
            else:
                self.assertGreater(len(self.app.ax_motors.get_lines()), 0, label)

    def test_pwm_deviation_heatmap_draws_image_and_labels(self):
        """PWM Sapma modu, kanal sayısı kadar satırlı bir ısı haritası
        çizmeli ve panel başlığı sapma birimini söylemeli."""
        data = self._load_and_plot("px4_hexarotor_flight.ulg")

        self.app.motor_view_toggle.set("PWM Sapma")
        self.app._on_motor_view_change("PWM Sapma")

        self.assertEqual(len(self.app.ax_motors.get_images()), 1)
        self.assertIn("Sapma", self.app.ax_motors.get_ylabel())
        self.assertEqual(
            [t.get_text() for t in self.app.ax_motors.get_yticklabels()],
            [o["label"] for o in data["pwm_outputs"]],
        )

    def test_pwm_deviation_compares_only_within_same_output_rail(self):
        """Sapma, kanalın AYNI raydaki (MAIN/AUX) kanalların ortalamasından
        farkı. Burada AUX kanallarından biri kasıtlı olarak yüksek: sadece o
        satır pozitif sapmalı, MAIN grubu bundan etkilenmemeli. Tek kanallı
        grup karşılaştırılamayacağı için nötr (0) kalmalı."""
        self.app._last_pwm_outputs = [
            {"id": 1, "label": "AUX 1", "time_s": [0.0, 1.0], "pwm_us": [1400.0, 1400.0]},
            {"id": 2, "label": "AUX 2", "time_s": [0.0, 1.0], "pwm_us": [1400.0, 1400.0]},
            {"id": 3, "label": "AUX 3", "time_s": [0.0, 1.0], "pwm_us": [1600.0, 1600.0]},
            {"id": 4, "label": "MAIN 1", "time_s": [0.0, 1.0], "pwm_us": [1900.0, 1900.0]},
        ]
        self.app.motor_view_mode = "pwm_deviation"
        self.app._plot_motor_currents([])

        deviation = self.app.ax_motors.get_images()[0].get_array()
        # AUX ortalamasi (1400+1400+1600)/3 = 1466.67
        self.assertAlmostEqual(deviation[0][0], -66.67, places=1)  # AUX 1
        self.assertAlmostEqual(deviation[1][0], -66.67, places=1)  # AUX 2
        self.assertAlmostEqual(deviation[2][0], 133.33, places=1)  # AUX 3 -> tek pozitif
        # MAIN grubunda tek kanal var: karsilastirma anlamsiz, notr kalmali.
        self.assertEqual(deviation[3][0], 0.0)

    def test_pwm_deviation_shows_message_when_no_data(self):
        self._load_and_plot("synthetic_test_log.BIN")

        self.app.motor_view_toggle.set("PWM Sapma")
        self.app._on_motor_view_change("PWM Sapma")

        self.assertEqual(len(self.app.ax_motors.get_images()), 0)
        texts = [t.get_text().lower() for t in self.app.ax_motors.texts]
        self.assertTrue(any("pwm çıkış verisi yok" in t for t in texts))

    def test_pwm_deviation_color_scale_is_symmetric_around_zero(self):
        """0 (sapma yok) her zaman ıraksak skalanın tam ortasındaki nötr griye
        denk gelmeli; asimetrik sınır "az sapma"yı "hiç sapma" gibi gösterirdi."""
        self._load_and_plot("px4_hexarotor_flight.ulg")
        self.app.motor_view_toggle.set("PWM Sapma")
        self.app._on_motor_view_change("PWM Sapma")

        vmin, vmax = self.app.ax_motors.get_images()[0].get_clim()
        self.assertAlmostEqual(vmin, -vmax, places=6)

    def test_pwm_view_does_not_shift_panel_position(self):
        """Isı haritası colorbar'ı için ayrılan sabit eksen sayesinde panel
        konumu görünümler arasında değişmiyordu; PWM modu da bunu bozmamalı."""
        self._load_and_plot("px4_hexarotor_flight.ulg")
        position_before = self.app.ax_motors.get_position().bounds

        self.app.motor_view_toggle.set("PWM Çıkışı")
        self.app._on_motor_view_change("PWM Çıkışı")

        self.assertEqual(self.app.ax_motors.get_position().bounds, position_before)

    # --- Toolbar "Sıfırla" butonu ---------------------------------------------

    # --- Üst toolbar'ın dar ekrana uyumu ---------------------------------------

    def _resize(self, width: int, height: int = 800):
        """Pencereyi yeniden boyutlandırıp Tk'nin yerleşimi tamamlamasını
        bekler.

        `deiconify` + gerçek genişliği bekleme ŞART: pencere haritalanmadan
        tüm widget genişlikleri 1 kalıyor, o hâlde "hiçbir şey taşmıyor"
        testleri hiçbir şey ölçmeden geçerdi."""
        self.app.deiconify()
        # Toolbar/istatistik satırı analiz ekranının içinde; giriş ekranı
        # açıkken paketlenmemiş oluyorlar ve genişlikleri 1 kalıyor.
        # (_load_and_plot, _show_analysis'i çağıran _load_file'ı atlıyor.)
        self.app._show_analysis()
        self.app.geometry(f"{width}x{height}")
        expected = width - 32  # toolbar/stats satırı padx=16 ile paketleniyor
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.update_idletasks()
            self.app.update()
            if abs(self.app.toolbar.winfo_width() - expected) <= 4:
                break
        self.assertAlmostEqual(
            self.app.toolbar.winfo_width(), expected, delta=4,
            msg="pencere istenen genişliğe gelmedi; ölçüm anlamsız olurdu",
        )

    def _toolbar_buttons(self):
        return {
            "home": self.app.home_button, "load": self.app.load_button,
            "recent": self.app.recent_menu, "clear": self.app.clear_button,
            "export": self.app.export_menu, "settings": self.app.settings_button,
            "theme": self.app.theme_button,
        }

    @staticmethod
    def _rects_overlap(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    def _broken_buttons(self):
        """Doğru yerleşmemiş toolbar butonlarının adları.

        Üç bozulma biçimi de aranıyor, çünkü `grid` yer yetmediğinde butonları
        dışarı İTMİYOR: önce sıkıştırıyor, sonra grupları ÜST ÜSTE bindiriyor.
        Sadece "görünür alanın dışında mı" diye baksaydık test, yerleşim
        tamamen bozukken bile geçerdi (ölçüldü)."""
        toolbar = self.app.toolbar
        broken, rects = [], {}
        for name, widget in self._toolbar_buttons().items():
            x = widget.winfo_rootx() - toolbar.winfo_rootx()
            y = widget.winfo_rooty() - toolbar.winfo_rooty()
            rects[name] = (x, y, x + widget.winfo_width(), y + widget.winfo_height())
            if x < 0 or y < 0 or rects[name][2] > toolbar.winfo_width() + 1:
                broken.append(name)
            elif widget.winfo_width() < widget.winfo_reqwidth():
                broken.append(f"{name} (sıkışmış)")
        for first, second in itertools.combinations(sorted(rects), 2):
            if self._rects_overlap(rects[first], rects[second]):
                broken.append(f"{first}+{second} (üst üste)")
        return broken

    def test_toolbar_buttons_stay_visible_down_to_minimum_window_width(self):
        """Asıl hata buydu: 1100px altında "Temizle"/"Dışa Aktar"/"Ayarlar"/
        "Koyu Tema" ekran dışında kalıyor ve tıklanamıyordu. Pencerenin
        izin verilen en dar hâlinde bile hepsi görünür olmalı."""
        self.app._show_analysis()
        self.app._set_file_label("ArduCopter-MaxAltFence-00000067.BIN · Multirotor")
        for width in (1920, 1400, 1200, 1100, 1000, 900, 800, 700):
            self._resize(width)
            self.assertEqual(self._broken_buttons(), [], f"{width}px")

    def test_toolbar_wraps_to_second_row_only_when_too_narrow(self):
        self.app._show_analysis()
        self._resize(1920)
        self.assertFalse(self.app._toolbar_two_rows)
        self._resize(900)
        self.assertTrue(self.app._toolbar_two_rows)
        self._resize(1920)
        self.assertFalse(self.app._toolbar_two_rows)  # geri dönebilmeli

    def test_toolbar_layout_does_not_oscillate_at_the_same_width(self):
        """Yerleşim kararı, o anki moda göre değil HER ZAMAN tek satır
        ihtiyacına göre veriliyor; iki satır modunun (daha dar olan) kendi
        ihtiyacına bakılsaydı toolbar iki mod arasında titrerdi."""
        self.app._show_analysis()
        self._resize(1000)
        first = self.app._toolbar_two_rows
        for _ in range(5):
            self.app.update()
            self.assertEqual(self.app._toolbar_two_rows, first)

    def test_long_file_name_is_shortened_instead_of_pushing_buttons_out(self):
        """Uzunluğu dosya adına bağlı tek bileşen etiket; ölçüldüğünde uzun bir
        ad 409px istiyor ve sağdaki butonları dışarı itiyordu. Artık etiket
        kısalıyor, butonlar yerinde kalıyor."""
        self.app._show_analysis()
        self._resize(1400)
        self.app._set_file_label("A" * 200 + ".BIN")
        self._resize(1400)

        self.assertEqual(self._broken_buttons(), [])
        self.assertLess(len(self.app.file_label.cget("text")), 204)
        self.assertTrue(self.app.file_label.cget("text").endswith("..."))

    def test_short_file_name_is_not_shortened(self):
        self.app._show_analysis()
        self._resize(1920)
        self.app._set_file_label("kisa.BIN")
        self._resize(1920)
        self.assertEqual(self.app.file_label.cget("text"), "kisa.BIN")

    def test_pdf_report_uses_the_full_file_name_not_the_shortened_one(self):
        """Rapor dosya adını etiketten okuyordu; etiket kısaltılabildiği için
        artık saklanan tam metinden okunmalı."""
        self.app._show_analysis()
        long_name = "ArduCopter-MaxAltFence-00000067.BIN · Multirotor"
        self.app._set_file_label(long_name)
        self._resize(700)
        self.assertNotEqual(self.app.file_label.cget("text"), long_name)  # kısaldı
        self.assertEqual(self.app._file_label_text, long_name)

    def test_progress_bar_space_is_released_after_loading(self):
        """İlerleme çubuğu gizlendikten sonra yerleşim onun 132 pikselini
        ayırmayı bırakmalı. Tk geometri hesabını boşta yaptığı için
        pack_forget() hemen etkili olmuyordu; etiket gereksiz yere kısa
        kalıyordu (bkz. _refresh_toolbar_layout)."""
        self.app._show_analysis()
        self._resize(1500)
        self.app._set_file_label("ArduCopter-MaxAltFence-00000067.BIN · Multirotor")
        self._resize(1500)
        text_before = self.app.file_label.cget("text")

        self.app.loading_progress.pack(side="left", padx=(frontend_main.TOOLBAR_GAP, 0))
        self.app._refresh_toolbar_layout()
        for _ in range(4):
            self.app.update()
        self.app.loading_progress.pack_forget()
        self.app._refresh_toolbar_layout()
        for _ in range(4):
            self.app.update()

        self.assertEqual(self.app.file_label.cget("text"), text_before)

    # --- İstatistik satırının dar ekrana uyumu ---------------------------------

    def _clipped_stat_tiles(self):
        """İstatistik satırının görünür alanının dışına taşan kutucuklar."""
        row = self.app.stats_row
        clipped = []
        for tile, (_key, title) in zip(self.app._stat_tiles,
                                       frontend_main.STAT_TILE_DEFINITIONS):
            x = tile.winfo_rootx() - row.winfo_rootx()
            y = tile.winfo_rooty() - row.winfo_rooty()
            if x < 0 or x + tile.winfo_width() > row.winfo_width() + 1:
                clipped.append(title)
            elif y + tile.winfo_height() > row.winfo_height() + 1:
                clipped.append(title + " (dikey)")
        return clipped

    def test_stat_tiles_stay_visible_down_to_minimum_window_width(self):
        """Dokuz kutucuk dar pencereye sığmıyordu: 1000px'de sonuncusu,
        720px'de son üçü kesiliyordu. Artık alt satıra iniyorlar."""
        self._load_and_plot("ArduCopter-MaxAltFence-00000067.BIN")
        for width in (1920, 1400, 1200, 1000, 900, 800, 700):
            self._resize(width)
            self.assertEqual(self._clipped_stat_tiles(), [], f"{width}px")

    def test_stat_tiles_use_one_row_when_there_is_room(self):
        self._load_and_plot("ArduCopter-MaxAltFence-00000067.BIN")
        self._resize(1920)
        self.assertEqual(self.app._stat_columns, len(frontend_main.STAT_TILE_DEFINITIONS))
        self._resize(800)
        self.assertLess(self.app._stat_columns, len(frontend_main.STAT_TILE_DEFINITIONS))
        self._resize(1920)
        self.assertEqual(self.app._stat_columns, len(frontend_main.STAT_TILE_DEFINITIONS))

    def test_stat_tiles_are_spread_evenly_when_wrapped(self):
        """Sığan en fazla sütun 8 olduğunda yerleşim 8+1 oluyordu ve tek
        başına kalan kutucuk hata gibi duruyordu; aynı iki satırda 5+4 hem
        dengeli hem daha dar."""
        widths = [80] * 9
        # 9 kutucuk 8 sütuna sığacak kadar yer var ama 9'a yetmiyor.
        available = 8 * (80 + frontend_main.STATS_TILE_GAP)
        self.assertEqual(self.app._stat_columns_that_fit(widths, available), 5)

    def test_stat_column_count_never_drops_below_one(self):
        """Alan hiçbir sütuna yetmese bile (ör. pencere henüz çizilmemişken
        genişlik 1) yerleşim çökmemeli."""
        self.assertEqual(self.app._stat_columns_that_fit([200] * 9, 1), 1)
        self.assertEqual(self.app._stat_columns_that_fit([200] * 9, 0), 1)

    def test_stat_row_layout_does_not_oscillate_at_the_same_width(self):
        self._load_and_plot("ArduCopter-MaxAltFence-00000067.BIN")
        self._resize(1000)
        first = self.app._stat_columns
        for _ in range(5):
            self.app.update()
            self.assertEqual(self.app._stat_columns, first)

    def test_stat_row_relayouts_after_values_change_width(self):
        """Kutucuk metinleri değişince genişlikleri de değişiyor; yerleşim
        eski genişliklere göre kalırsa dar pencerede yine taşardı. Tk genişlik
        hesabını boşta yaptığı için yenileme after_idle ile (bkz.
        _refresh_stats_layout)."""
        self._resize(900)
        self._load_and_plot("ArduCopter-MaxAltFence-00000067.BIN")
        self._resize(900)
        self.assertEqual(self._clipped_stat_tiles(), [])
        columns_with_values = self.app._stat_columns

        self.app._on_clear_click()  # kutucuklar "—"ye döner, daralırlar
        self._resize(900)
        self.assertEqual(self._clipped_stat_tiles(), [])
        self.assertGreaterEqual(self.app._stat_columns, columns_with_values)

    # --- Uyarı/hata etiketlerinin dar ekrana uyumu ------------------------------

    def test_warning_and_status_wraplength_follows_window_width(self):
        """warnings_label hiç wraplength taşımıyordu, status_label'ınki ise
        sabit 1000px'ti — toolbar/stats-row'daki gibi <Configure>'da yeniden
        hesaplanmıyordu. Dar pencerede ikisi de görünür genişliğe göre
        güncellenmeli (padx=16 iki yandan, bkz. _on_analysis_frame_configure)."""
        self._resize(1400)
        self.assertEqual(self.app.warnings_label.cget("wraplength"), 1400 - 32)
        self.assertEqual(self.app.status_label.cget("wraplength"), 1400 - 32)

        self._resize(700)
        self.assertEqual(self.app.warnings_label.cget("wraplength"), 700 - 32)
        self.assertEqual(self.app.status_label.cget("wraplength"), 700 - 32)

    # --- Araç tipi başına uyarı eşikleri ---------------------------------------

    def _write_vehicle_thresholds(self, vehicle_type: str, values: dict):
        settings = frontend_main._load_settings()
        settings[frontend_main.SETTINGS_VEHICLE_THRESHOLDS_KEY] = {vehicle_type: values}
        frontend_main._save_settings(settings)

    def test_thresholds_fall_back_to_general_when_type_has_no_entry(self):
        self._write_vehicle_thresholds("rover", {
            "voltage_sag_threshold": 0.05,
            "current_imbalance_threshold": 0.05,
            "negative_current_threshold": -0.02,
        })
        general = frontend_main._get_general_thresholds()

        self.assertEqual(frontend_main._get_warning_thresholds("multirotor"), general)
        self.assertEqual(frontend_main._get_warning_thresholds(None), general)
        self.assertEqual(
            frontend_main._get_warning_thresholds("rover")["voltage_sag_threshold"], 0.05
        )

    def test_malformed_vehicle_threshold_entries_are_ignored(self):
        """Ayar dosyası elle düzenlenmiş olabilir; eksik/yanlış tipli kayıtlar
        uygulamayı çökertmemeli, sessizce genel eşiklere düşmeli."""
        settings = frontend_main._load_settings()
        settings[frontend_main.SETTINGS_VEHICLE_THRESHOLDS_KEY] = {
            "rover": {"voltage_sag_threshold": 0.05},          # eksik alanlar
            "vtol": "bozuk",                                    # yanlis tip
            "fixed_wing": {"voltage_sag_threshold": "a",
                           "current_imbalance_threshold": 0.1,
                           "negative_current_threshold": -0.1},  # sayi degil
        }
        frontend_main._save_settings(settings)

        self.assertEqual(frontend_main._get_vehicle_threshold_overrides(), {})
        general = frontend_main._get_general_thresholds()
        self.assertEqual(frontend_main._get_warning_thresholds("rover"), general)

    def test_vehicle_thresholds_are_passed_to_backend(self):
        """Araç tipine özel eşiklerin HEPSİ backend'e geçirilmeli: hangisinin
        geçerli olduğuna backend karar veriyor, çünkü araç tipi ancak log
        ayrıştırıldıktan sonra biliniyor."""
        self._write_vehicle_thresholds("rover", {
            "voltage_sag_threshold": 0.12,
            "current_imbalance_threshold": 0.25,
            "negative_current_threshold": -0.2,
        })
        with patch("main.subprocess.run") as fake_run:
            fake_run.return_value = types.SimpleNamespace(returncode=1, stderr="dur")
            with self.assertRaises(RuntimeError):
                self.app._run_backend(str(DATA_DIR / "synthetic_test_log.BIN"))

        command = fake_run.call_args[0][0]
        self.assertIn("--vehicle-thresholds=rover:0.12:0.25:-0.2", command)
        # Genel eşikler de her zaman geçiriliyor (eşleşen tip yoksa onlar geçerli).
        self.assertTrue(any(a.startswith("--voltage-sag=") for a in command))

    def test_vehicle_specific_threshold_changes_warnings_end_to_end(self):
        """Uçtan uca: hexarotor logu multirotor olarak ayrıştırılıyor; bu tipe
        sıkı bir dengesizlik eşiği tanımlanınca uyarı sayısı artmalı, aynı
        eşik BAŞKA bir tipe tanımlıyken değişmemeli."""
        baseline = len(self.app._run_backend(str(DATA_DIR / "px4_hexarotor_flight.ulg"))["warnings"])

        self._write_vehicle_thresholds("rover", {
            "voltage_sag_threshold": 0.15,
            "current_imbalance_threshold": 0.05,
            "negative_current_threshold": -0.1,
        })
        unrelated = len(self.app._run_backend(str(DATA_DIR / "px4_hexarotor_flight.ulg"))["warnings"])
        self.assertEqual(unrelated, baseline)

        self._write_vehicle_thresholds("multirotor", {
            "voltage_sag_threshold": 0.15,
            "current_imbalance_threshold": 0.05,
            "negative_current_threshold": -0.1,
        })
        strict = len(self.app._run_backend(str(DATA_DIR / "px4_hexarotor_flight.ulg"))["warnings"])
        self.assertGreater(strict, baseline)

    def test_plot_threshold_lines_follow_vehicle_specific_values(self):
        """Grafikteki eşik çizgisi, backend'in o log için kullandığı eşikle
        AYNI olmalı; aksi halde çizgi uyarıyla çelişirdi."""
        self._write_vehicle_thresholds("multirotor", {
            "voltage_sag_threshold": 0.02,  # cok sikí -> cizgi voltaja cok yakin
            "current_imbalance_threshold": 0.20,
            "negative_current_threshold": -0.1,
        })
        self._load_and_plot("px4_hexarotor_flight.ulg")
        self.assertEqual(self.app._last_vehicle_type, "multirotor")

        first_voltage = self.app._last_batteries[0]["voltage_v"][0]
        threshold_lines = [
            line for line in self.app.ax_voltage.get_lines()
            if line.get_label().startswith("_")
        ]
        self.assertTrue(threshold_lines)
        self.assertAlmostEqual(
            threshold_lines[0].get_ydata()[0], first_voltage * (1 - 0.02), places=3
        )

    # --- Çoklu uçuş karşılaştırma ---------------------------------------------

    def test_flight_duration_excludes_boot_time_offset(self):
        """PX4 logları uçuş kontrolcüsünün açılışından beri geçen süreyi
        damgalıyor; ilk damga çıkarılmazsa 90.8 saniyelik bir kayıt "4104.7 s"
        olarak görünüyordu. Aynı hata kalan-süre tahminine de taşınıyordu."""
        data = self._load_and_plot("px4_fixed_wing_flight.ulg")
        duration = frontend_main._flight_duration_seconds(data["batteries"])
        self.assertAlmostEqual(duration, 90.8, places=1)
        self.assertIn("90.8", self.app.stat_labels["duration"].cget("text"))

    def test_summary_metrics_match_stat_row_for_same_flight(self):
        """Karşılaştırma tablosu ile üst istatistik satırı aynı yardımcıları
        kullanır; ikisi farklı sayı gösterirse hangisine güvenileceği belirsiz
        olurdu."""
        data = self._load_and_plot("ArduCopter-MaxAltFence-00000067.BIN")
        metrics = frontend_main._flight_summary_metrics(data)

        self.assertIn(f"{metrics['energy_wh']:.1f}", self.app.stat_labels["energy_wh"].cget("text"))
        self.assertIn(f"{metrics['peak_power_w']:.0f}", self.app.stat_labels["peak_power_w"].cget("text"))
        self.assertEqual(metrics["vehicle"], "Multirotor")
        self.assertGreater(metrics["duration_s"], 0)

    def test_summary_metrics_are_none_without_current_sensor(self):
        """Rover'da akım sensörü yok; akıma dayanan metrikler None olmalı
        (tabloda "—"), 0 değil — sıfır "hiç akım çekilmemiş" gibi okunurdu."""
        data = self._load_and_plot("px4_ground_rover_flight.ulg")
        metrics = frontend_main._flight_summary_metrics(data)

        for key in ("energy_wh", "peak_power_w", "current_min", "capacity_used_mah"):
            self.assertIsNone(metrics[key], key)
            self.assertEqual(frontend_main._format_comparison_value(key, metrics), "—")
        # Voltaj gerçek ölçüm; o gösterilmeye devam etmeli.
        self.assertIn("V", frontend_main._format_comparison_value("voltage_range", metrics))

    def test_comparison_table_has_row_per_metric_and_column_per_flight(self):
        results = []
        for name in ("ArduCopter-MaxAltFence-00000067.BIN", "px4_hexarotor_flight.ulg"):
            data = self.app._run_backend(str(DATA_DIR / name))
            results.append((name, frontend_main._flight_summary_metrics(data)))

        frame = frontend_main.ctk.CTkFrame(self.app)
        self.app._build_comparison_table(frame, results)

        cells = [w for w in frame.winfo_children() if isinstance(w, frontend_main.ctk.CTkLabel)]
        # 1 bos kose + 2 baslik + her metrik icin (1 etiket + 2 deger)
        expected = 1 + len(results) + len(frontend_main.COMPARISON_ROWS) * (1 + len(results))
        self.assertEqual(len(cells), expected)

        texts = [w.cget("text") for w in cells]
        self.assertIn("Enerji Tüketimi", texts)
        self.assertIn("ArduCopter-MaxAltFence-00000067.BIN", texts)

    def test_compare_button_warns_when_history_too_short(self):
        """Tek dosyalık geçmişte karşılaştırma anlamsız; pencere açılmak
        yerine durum çubuğunda uyarı çıkmalı."""
        self._load_file_and_wait(str(DATA_DIR / "synthetic_test_log.BIN"))
        self.assertEqual(len(self.app._load_recent_files()), 1)

        dialogs_before = len([
            w for w in self.app.winfo_children()
            if isinstance(w, frontend_main.ctk.CTkToplevel)
        ])
        self.app._on_compare_click()
        self.app.update()

        dialogs_after = len([
            w for w in self.app.winfo_children()
            if isinstance(w, frontend_main.ctk.CTkToplevel)
        ])
        self.assertEqual(dialogs_after, dialogs_before)
        self.assertIn("en az iki", self.app.status_label.cget("text").lower())

    def test_compare_dialog_lists_recent_files(self):
        for name in ("synthetic_test_log.BIN", "px4_ground_rover_flight.ulg"):
            self._load_file_and_wait(str(DATA_DIR / name))

        self.app._on_compare_click()
        self.app.update()
        dialog = [
            w for w in self.app.winfo_children()
            if isinstance(w, frontend_main.ctk.CTkToplevel)
        ][-1]
        try:
            checkboxes = []

            def walk(widget):
                for child in widget.winfo_children():
                    if isinstance(child, frontend_main.ctk.CTkCheckBox):
                        checkboxes.append(child)
                    walk(child)

            walk(dialog)
            labels = [c.cget("text") for c in checkboxes]
            self.assertEqual(len(checkboxes), 2)
            self.assertIn("px4_ground_rover_flight.ulg", labels)
            self.assertIn("synthetic_test_log.BIN", labels)
        finally:
            dialog.destroy()

    def test_concurrent_backend_calls_do_not_mix_results(self):
        """Aynı anda iki backend çağrısı birbirinin çıktısını bozmamalı.

        Regresyon: eskiden çıktı SABİT bir geçici dosyaya
        ("iha_power_log_output.json") yazılıyordu. Uçuş karşılaştırma arka
        planda çalışırken ana pencereden bir dosya yüklemek iki çağrıyı
        çakıştırmaya yetiyordu; sonuç ya yarım yazılmış dosyanın okunması
        (JSONDecodeError) ya da bir logun verisinin öbürüne ait sanılmasıydı."""
        expected = {
            "px4_ground_rover_flight.ulg": ("rover", 1, 0),
            "px4_fixed_wing_flight.ulg": ("fixed_wing", 1, 0),
        }
        seen = {name: [] for name in expected}
        errors = []

        def worker(name):
            try:
                for _ in range(4):
                    data = self.app._run_backend(str(DATA_DIR / name))
                    seen[name].append((
                        data["meta"]["vehicle_type"],
                        len(data["batteries"]),
                        len(data["motors"]),
                    ))
            except Exception as error:  # JSONDecodeError dahil
                errors.append(f"{name}: {type(error).__name__}: {error}")

        threads = [threading.Thread(target=worker, args=(name,)) for name in expected]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180)

        self.assertEqual(errors, [])
        for name, want in expected.items():
            self.assertEqual(set(seen[name]), {want}, name)

    # --- Özet metriklerin sınır durumları -------------------------------------

    def test_summary_metrics_without_any_samples_show_placeholder(self):
        """Hiç örnek yokken uydurma sayı üretilmemeli; hepsi "—" olmalı.
        "Boş zaman serisi" hali bir kusuru ortaya çıkarmıştı: enerji ve tepe
        güç 0.0 hesaplanıp tabloda "0.00 Wh" görünüyordu, yani "ölçüm yok"
        yine "ölçüm sıfır" gibi okunuyordu."""
        cases = {
            "tamamen boş": {},
            "boş batarya listesi": {"batteries": [], "warnings": []},
            "boş zaman serisi": {"batteries": [
                {"id": 1, "time_s": [], "voltage_v": [], "current_a": [],
                 "capacity_used_mah": None, "remaining_pct": None,
                 "temperature_c": [], "has_current_data": True}], "warnings": []},
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                metrics = frontend_main._flight_summary_metrics(data)
                self.assertIsNone(metrics["energy_wh"])
                self.assertIsNone(metrics["peak_power_w"])
                self.assertIsNone(metrics["voltage_sag_pct"])
                # Her satır çökmeden bir metne dönüşmeli ve "—" olmalı.
                for key, _title in frontend_main.COMPARISON_ROWS:
                    if key == "warning_count":
                        continue  # sayi; 0 gostermesi dogru
                    self.assertEqual(
                        frontend_main._format_comparison_value(key, metrics), "—", key
                    )

    def test_zero_voltage_flight_reports_zero_energy_not_placeholder(self):
        """Sıfır voltajda enerji GERÇEKTEN sıfırdır (0 V x 1 A = 0 W); burada
        "—" göstermek yanlış olurdu. Ölçüm var, sonucu sıfır — yukarıdaki
        "hiç ölçüm yok" durumundan farkı bu."""
        data = {"batteries": [
            {"id": 1, "time_s": [0.0, 10.0], "voltage_v": [0.0, 0.0],
             "current_a": [1.0, 1.0], "capacity_used_mah": None,
             "remaining_pct": None, "temperature_c": [],
             "has_current_data": True}], "warnings": []}
        metrics = frontend_main._flight_summary_metrics(data)

        self.assertEqual(metrics["energy_wh"], 0.0)
        self.assertEqual(frontend_main._format_comparison_value("energy_wh", metrics), "0.00 Wh")
        # Voltaj düşümü oranı ise ilk voltaj 0 iken tanımsız: "—" kalmalı.
        self.assertIsNone(metrics["voltage_sag_pct"])

    def test_single_sample_flight_has_zero_duration_not_crash(self):
        battery = {"id": 1, "time_s": [42.0], "voltage_v": [16.0], "current_a": [10.0],
                   "capacity_used_mah": None, "remaining_pct": None,
                   "temperature_c": [], "has_current_data": True}
        self.assertEqual(frontend_main._flight_duration_seconds([battery]), 0.0)
        self.assertEqual(frontend_main._battery_energy_wh([battery]), 0.0)

    def test_duration_spans_union_of_all_batteries(self):
        """İki batarya farklı aralıklarda örneklenmişse süre, ikisinin
        birleşimi olmalı (en erken başlangıç -> en geç bitiş)."""
        def battery(times):
            return {"id": 1, "time_s": times, "voltage_v": [16.0] * len(times),
                    "current_a": [1.0] * len(times), "capacity_used_mah": None,
                    "remaining_pct": None, "temperature_c": [], "has_current_data": True}
        self.assertEqual(
            frontend_main._flight_duration_seconds([battery([10.0, 20.0]), battery([5.0, 30.0])]),
            25.0,
        )

    def _open_settings_dialog(self):
        """Ayarlar penceresini açar ve içindeki widget'ları döndürür. Pencere
        grab_set() yaptığı için testte hemen serbest bırakılıyor, yoksa
        sonraki update() çağrıları kilitlenebiliyor."""
        self.app._on_settings_click()
        self.app.update()
        dialog = [
            w for w in self.app.winfo_children()
            if isinstance(w, frontend_main.ctk.CTkToplevel)
        ][-1]
        dialog.grab_release()

        option_menus, entry_widgets, buttons = [], [], []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, frontend_main.ctk.CTkOptionMenu):
                    option_menus.append(child)
                elif isinstance(child, frontend_main.ctk.CTkEntry):
                    entry_widgets.append(child)
                elif isinstance(child, frontend_main.ctk.CTkButton):
                    buttons.append(child)
                walk(child)

        walk(dialog)
        return dialog, option_menus[0], entry_widgets, buttons

    def test_settings_dialog_opens_on_loaded_vehicle_type(self):
        """Kullanıcı çoğunlukla az önce baktığı uçuş için eşik ayarlar; seçici
        yüklü logun araç tipiyle açılmalı."""
        self._load_and_plot("px4_ground_rover_flight.ulg")
        dialog, scope_menu, _entries, _buttons = self._open_settings_dialog()
        try:
            self.assertEqual(scope_menu.get(), "Sadece Rover")
        finally:
            dialog.destroy()

    def test_settings_dialog_saves_per_vehicle_entry(self):
        """Bir araç tipi seçiliyken kaydetmek SADECE o tipin kaydını
        oluşturmalı, genel eşiklere dokunmamalı."""
        general_before = frontend_main._get_general_thresholds()
        self._load_and_plot("px4_ground_rover_flight.ulg")
        dialog, _scope_menu, entries, buttons = self._open_settings_dialog()
        try:
            entries[0].delete(0, "end")
            entries[0].insert(0, "9")  # voltaj dusumu %9
            save_button = next(b for b in buttons if b.cget("text") == "Kaydet")
            save_button.invoke()
            self.app.update()
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        overrides = frontend_main._get_vehicle_threshold_overrides()
        self.assertIn("rover", overrides)
        self.assertAlmostEqual(overrides["rover"]["voltage_sag_threshold"], 0.09)
        self.assertEqual(frontend_main._get_general_thresholds(), general_before)

    def test_settings_dialog_general_scope_does_not_create_vehicle_entry(self):
        dialog, scope_menu, entries, buttons = self._open_settings_dialog()
        try:
            self.assertEqual(scope_menu.get(), "Genel (tüm araçlar)")  # dosya yuklu degil
            entries[1].delete(0, "end")
            entries[1].insert(0, "33")  # dengesizlik %33
            next(b for b in buttons if b.cget("text") == "Kaydet").invoke()
            self.app.update()
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        self.assertEqual(frontend_main._get_vehicle_threshold_overrides(), {})
        self.assertAlmostEqual(
            frontend_main._get_general_thresholds()["current_imbalance_threshold"], 0.33
        )

    def test_reset_button_exists_next_to_pan(self):
        self.assertIn("Reset", self.app.nav_toolbar._buttons)

    def test_reset_button_looks_like_other_tool_buttons(self):
        """Buton metin değil ikon göstermeli ve diğer araç butonlarıyla aynı
        ölçüde olmalı — metinli hali toolbar'da sırıtıyordu."""
        reset = self.app.nav_toolbar._buttons["Reset"]
        pan = self.app.nav_toolbar._buttons["Pan"]

        self.assertEqual(reset.cget("text"), "")
        self.assertNotEqual(str(reset.cget("image")), "")
        self.assertEqual(reset.cget("width"), pan.cget("width"))
        self.assertEqual(reset.cget("height"), pan.cget("height"))

    def test_reset_button_has_hover_tooltip(self):
        """İkon tek başına ne işe yaradığını anlatmadığı için ipucu balonu
        şart. Regresyon değeri yüksek: ilk denemede matplotlib'in private
        tooltip yardımcısı kullanılmıştı, o sürümde adı değiştiği için
        tooltip sessizce hiç bağlanmamıştı."""
        reset = self.app.nav_toolbar._buttons["Reset"]
        self.assertIn("<Enter>", reset.bind())

        # Balonu gerçekten aç: metni doğru mu ve ayrılırken kapanıyor mu?
        # Fare olayları ancak widget ekranda haritalandıysa işleniyor, bu
        # yüzden önce analiz ekranına geçiliyor (toolbar orada duruyor).
        self.app._show_analysis()
        self.app.update()
        reset.event_generate("<Enter>")
        self.app.update()
        balloons = [
            child for child in reset.winfo_children()
            if isinstance(child, frontend_main.tk.Toplevel)
        ]
        self.assertEqual(len(balloons), 1)
        texts = [w.cget("text") for w in balloons[0].winfo_children()]
        self.assertTrue(any("Sıfırla" in t for t in texts), texts)

        reset.event_generate("<Leave>")
        self.app.update()
        self.assertEqual(
            [c for c in reset.winfo_children()
             if isinstance(c, frontend_main.tk.Toplevel)],
            [],
        )

    def test_reset_button_restores_original_view(self):
        """Yakınlaştırma sonrası Sıfırla, grafiği ilk haline döndürmeli.
        Not: paneller yeniden kurulurken nav_toolbar.update() navigasyon
        yığınını temizliyor; _plot_power_data sonundaki push_current() bu
        yüzden gerekli (o olmadan matplotlib'in home()'u sessizce hiçbir şey
        yapmazdı)."""
        self._load_and_plot("synthetic_test_log.BIN")
        original_xlim = self.app.ax_voltage.get_xlim()

        zoomed_xlim = (original_xlim[0] + 2, original_xlim[1] - 2)
        self.app.ax_voltage.set_xlim(*zoomed_xlim)
        self.assertEqual(self.app.ax_voltage.get_xlim(), zoomed_xlim)

        self.app.nav_toolbar.reset_view()
        self.assertEqual(self.app.ax_voltage.get_xlim(), original_xlim)

    def test_reset_button_keeps_active_pan_tool_selected(self):
        """Pan açıkken sıfırlamak aracı KAPATMAMALI. İlk sürüm kapatıyordu ve
        kullanıcı incelemeye devam edebilmek için her sıfırlamadan sonra Pan'a
        yeniden basmak zorunda kalıyordu."""
        self._load_and_plot("synthetic_test_log.BIN")
        toolbar = self.app.nav_toolbar
        original_xlim = self.app.ax_voltage.get_xlim()

        toolbar.pan()
        self.assertEqual(toolbar.mode, frontend_main._Mode.PAN)
        self.app.ax_voltage.set_xlim(original_xlim[0] + 5, original_xlim[1] + 5)

        toolbar.reset_view()

        self.assertEqual(toolbar.mode, frontend_main._Mode.PAN)  # araç seçili kaldı
        self.assertEqual(self.app.ax_voltage.get_xlim(), original_xlim)  # görünüm sıfırlandı

    def test_reset_while_panning_updates_pan_restore_point(self):
        """Sıfırladıktan sonra başka bir araca geçilirse, Pan'a girmeden
        önceki ESKİ kaydırmaya değil sıfırlanmış görünüme dönülmeli."""
        self._load_and_plot("synthetic_test_log.BIN")
        toolbar = self.app.nav_toolbar
        original_xlim = self.app.ax_voltage.get_xlim()

        self.app.ax_voltage.set_xlim(original_xlim[0] + 30, original_xlim[1] + 30)
        toolbar.pan()  # kaydırılmış görünüm "geri dönülecek nokta" olarak kaydedilir
        toolbar.reset_view()
        toolbar.zoom()  # başka araca geçiş -> kaydedilen noktaya dönülür

        self.assertEqual(self.app.ax_voltage.get_xlim(), original_xlim)

    def test_home_button_removed_from_plot_toolbar(self):
        """Ev simgesi kaldırıldı; görevini Sıfırla devraldı. Uygulamada zaten
        giriş ekranına dönen ayrı bir "← Ana Sayfa" butonu var, iki ev
        simgesi kafa karıştırıyordu."""
        self.assertNotIn("Home", self.app.nav_toolbar._buttons)
        self.assertIn("Reset", self.app.nav_toolbar._buttons)

    def test_rover_log_exports_do_not_crash(self):
        """Rover logu artık farklı bir çizim yolundan geçiyor (çizgi yerine
        durum mesajı, boş motor listesi); PNG/PDF/CSV dışa aktarımı bundan
        etkilenmemeli. PDF özet sayfası istatistikleri stat_labels'tan
        okuduğu için '—' değerleri de rapora aynen yansımalı."""
        self._load_and_plot("px4_ground_rover_flight.ulg")
        with tempfile.TemporaryDirectory() as tmp_dir:
            for file_name, handler in (
                ("grafik.png", self.app._on_export_png_click),
                ("rapor.pdf", self.app._on_export_pdf_click),
                ("veri.csv", self.app._on_export_csv_click),
            ):
                out_path = str(Path(tmp_dir) / file_name)
                with patch("main.filedialog.asksaveasfilename", return_value=out_path):
                    handler()
                self.assertGreater(Path(out_path).stat().st_size, 0, file_name)

        fig = self.app._build_report_summary_figure()
        all_text = " ".join(t.get_text() for t in fig.texts)
        self.assertIn("Enerji", all_text)
        self.assertIn("—", all_text)

    def test_measured_battery_still_plotted_when_another_has_no_sensor(self):
        """Karışık durum: bir bataryanın sensörü var, diğerinin yok. Ölçümü
        olan çizilmeye devam etmeli, olmayan sessizce atlanmalı — panel
        tamamen mesaja düşmemeli."""
        self.app._plot_battery_currents([
            {"id": 1, "time_s": [0.0, 1.0], "voltage_v": [16.8, 16.5],
             "current_a": [10.0, 12.0], "has_current_data": True},
            {"id": 2, "time_s": [0.0, 1.0], "voltage_v": [16.8, 16.5],
             "current_a": [0.0, 0.0], "has_current_data": False},
        ])
        self.assertEqual(len(self.app.ax_current.get_lines()), 1)
        self.assertEqual(self.app.ax_current.get_lines()[0].get_label(), "Batarya 1")
        self.assertEqual(len(self.app.ax_current.texts), 0)  # "veri yok" mesajı çıkmamalı

    # --- Türkçe/ASCII-dışı karakterli dosya yolu ---------------------------

    def test_loads_a_log_from_a_turkish_character_path(self):
        """Projenin kendi çalışma dizini her gün Türkçe karakter taşıyor
        ("İHA UYG", "istinye üniversitesi") ama hiçbir test bunu GUI'nin
        gerçek subprocess çağrı yolundan (_load_file -> _run_backend ->
        backend .exe) denemiyordu — backend testindeki eşdeğerinden
        (NonAsciiPathTests) AYRI bir kod yolu, çünkü burada Python'ın
        subprocess.run çağrısı ve Tk'nin dosya iş parçacığı da devrede."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="İHA Loğ Testi çğşü "))
        try:
            log_path = tmp_dir / "örnek kayıt İ.BIN"
            log_path.write_bytes(make_synthetic_log.generate())
            self._load_file_and_wait(str(log_path))
            self.assertEqual(self.app.status_label.cget("text"), "")
            self.assertGreater(len(self.app.ax_voltage.get_lines()), 0)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # --- 3+ bataryalı log ----------------------------------------------------

    def _write_three_battery_log(self) -> Path:
        """Şu ana kadar her çoklu-batarya testi tam 2 batarya kullanıyordu;
        dengesizlik bandı, karşılaştırma tablosu ve ısı haritası y-ekseni 3+
        seriyle hiç denenmedi (bkz. proje test kapsamı taraması). Üçüncü
        batarya belirgin şekilde daha az akım çekiyor (genel dengesizlik
        eşiği %20'yi aşsın diye)."""
        out = bytearray()
        out += make_synthetic_log.build_fmt_message(
            make_synthetic_log.BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr"
        )
        currents = {0: 10.0, 1: 10.0, 2: 2.0}
        for t in (0.0, 4.0, 8.0):
            for inst, curr in currents.items():
                out += make_synthetic_log.build_bat_message(t, inst, 16.0, curr)
        path = Path(tempfile.mkdtemp()) / "uc_batarya.BIN"
        path.write_bytes(bytes(out))
        return path

    def test_three_batteries_render_as_three_lines_with_imbalance_band(self):
        path = self._write_three_battery_log()
        try:
            data = self.app._run_backend(str(path))
            self.app._plot_battery_currents(data["batteries"])
            self.assertEqual(len(self.app.ax_current.get_lines()), 3)
            self.assertEqual(len(self.app.ax_current.patches), 1)  # dengesizlik bandı
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_three_batteries_render_as_three_rows_in_heatmap(self):
        path = self._write_three_battery_log()
        try:
            data = self.app._run_backend(str(path))
            self.app.battery_view_toggle.set("Isı Haritası")
            self.app._on_battery_view_change("Isı Haritası")
            self.app._plot_battery_currents(data["batteries"])
            self.assertEqual(len(self.app.ax_current.get_images()), 1)
            self.assertEqual(
                [t.get_text() for t in self.app.ax_current.get_yticklabels()],
                ["Batarya 1", "Batarya 2", "Batarya 3"],
            )
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_three_batteries_appear_in_comparison_table(self):
        """Karşılaştırma tablosu satır=metrik/sütun=uçuş şeklinde; burada tek
        bir 3 bataryalı uçuşun kendi metrikleri (akım aralığı vb.) 3
        bataryanın birleşimini yansıtmalı ve çökmemeli."""
        path = self._write_three_battery_log()
        try:
            data = self.app._run_backend(str(path))
            metrics = frontend_main._flight_summary_metrics(data)
            self.assertEqual(metrics["current_min"], 2.0)
            self.assertEqual(metrics["current_max"], 10.0)
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)

    # --- Gerçek akım sensörlü sabit kanat logu --------------------------------

    def test_real_fixed_wing_log_with_current_sensor_draws_both_panels(self):
        """data/ArduPlane-GpsSensorPreArmEAHRS-00000115.BIN: bu turdan önce
        gerçek akım verisi olan bir sabit kanat logu yoktu (px4_fixed_wing_
        flight.ulg'de esc_status hiç yok, ArduCopter örnekleri multirotor).
        Burada hem batarya hem motor akımı gerçek veri, mesaj yerine çizgi
        çizilmeli."""
        data = self._load_and_plot("ArduPlane-GpsSensorPreArmEAHRS-00000115.BIN")
        self.assertEqual(data["meta"]["vehicle_type"], "fixed_wing")
        self.assertEqual(len(self.app.ax_current.get_lines()), 1)
        self.assertEqual(len(self._data_lines(self.app.ax_current)), 1)

        self.app._plot_motor_currents(data["motors"])
        self.assertEqual(len(self.app.ax_motors.get_lines()), 1)
        self.assertEqual(len(self.app.ax_motors.texts), 0)  # "veri yok" mesajı çıkmamalı

    def test_real_quadplane_log_with_no_battery_data_does_not_crash(self):
        """data/ArduPlane-FlyEachFrame-00000182.BIN: gerçek bir ArduPilot
        QuadPlane SITL logu, hiç batarya (BAT/CURR) verisi yok ama 5
        motorun ESC telemetrisi var (bkz. backend'in ArduPlaneQuadPlaneRealLogTests'i
        — bu senaryo backend'in "en az bir batarya" kuralını gevşetmesine
        yol açtı). Voltaj paneli eskiden bu durumda hiçbir mesaj göstermeden
        boş kalıyordu — artık diğer "veri yok" panelleriyle tutarlı."""
        data = self._load_and_plot("ArduPlane-FlyEachFrame-00000182.BIN")
        self.assertEqual(data["batteries"], [])
        self.assertEqual(len(data["motors"]), 5)

        voltage_texts = [t.get_text() for t in self.app.ax_voltage.texts]
        self.assertIn(frontend_main.NO_BATTERY_MESSAGE, voltage_texts)

        current_texts = [t.get_text() for t in self.app.ax_current.texts]
        self.assertIn(frontend_main.NO_BATTERY_CURRENT_MESSAGE, current_texts)

        self.app._plot_motor_currents(data["motors"])
        self.assertEqual(len(self.app.ax_motors.get_lines()), 5)

    def test_time_axis_starts_at_zero_and_matches_duration_card(self):
        """PX4 zaman damgaları kontrolcünün açılışından itibaren sayılıyor;
        px4_hexarotor_flight.ulg'de ilk örnek ~1453.5 s'deydi. Süre kartı
        ilk-son farkını (doğru: ~90 s) gösterirken grafik ekseni mutlak
        1450-1545 aralığını gösteriyordu — kullanıcı bunu haklı olarak
        tutarsızlık (hatta regresyon) sanıyordu. Zaman ekseni artık veri
        girişinde ortak t0'a göre normalize ediliyor (_normalize_time_axis):
        eksen 0'dan başlar, son örnek = süre kartındaki değer."""
        data = self._load_and_plot("px4_hexarotor_flight.ulg")

        all_times = [t for battery in data["batteries"] for t in battery["time_s"]]
        first, last = min(all_times), max(all_times)
        self.assertAlmostEqual(first, 0.0, places=6)
        # Kayıt ~90 s; normalizasyon olmasaydı last ~1543.5 olurdu.
        self.assertLess(last, 200.0)

        duration_text = self.app.stat_labels["duration"].cget("text")
        self.assertEqual(duration_text, f"{last - first:.1f} s")

        # Seriler arası hizalama korunmalı: motor serileri de AYNI t0 ile
        # kaymalı, kendi ilk örneklerine göre ayrı ayrı sıfırlanmamalı.
        motor_first = min(t for motor in data["motors"] for t in motor["time_s"])
        self.assertGreaterEqual(motor_first, 0.0)
        self.assertLess(motor_first, last)

        line = self._data_lines(self.app.ax_voltage)[0]
        self.assertAlmostEqual(float(line.get_xdata()[0]), first, places=6)

    # --- Stat kartları cilası ------------------------------------------------

    def test_stat_tile_tooltips_cover_every_tile(self):
        """Her kutucuğun bir açıklaması olmalı — yeni bir kutucuk eklenirken
        tooltip'i unutulursa bu test yakalar (build sırasında KeyError da
        olur ama bu test nedeni açıkça söyler)."""
        tile_keys = {key for key, _title in frontend_main.STAT_TILE_DEFINITIONS}
        self.assertEqual(set(frontend_main.STAT_TILE_TOOLTIPS.keys()), tile_keys)

    def test_negative_current_tile_shows_warning_prefix(self):
        """Kart negatifken sadece renk değiştiriyordu; ⚠ öneki, uyarı
        listesiyle bağı renk körü kullanıcılar için de kurar."""
        self._load_and_plot("px4_hexarotor_flight.ulg")  # min akım -0.73 A
        text = self.app.stat_labels["current_range"].cget("text")
        self.assertTrue(text.startswith("⚠"), text)

    def test_remaining_time_range_constant_current_gives_single_value(self):
        """Tüketim hızı sabitse aralık üretmek sahte hassasiyet olur —
        eski tek-değerli formülün sonucu dönmeli (lo == hi)."""
        result = frontend_main._remaining_time_range_min(600.0, 50.0, [10.0] * 60)
        self.assertIsNotNone(result)
        lo, hi = result
        self.assertEqual(lo, hi)
        self.assertAlmostEqual(lo, 600.0 * 50.0 / 50.0 / 60, places=6)

    def test_remaining_time_range_higher_recent_draw_shrinks_estimate(self):
        """Son 1/3 pencerede akım uçuş geneli ortalamasının üstündeyse
        kalan süre tahmini kısalmalı: aralığın ALT ucu ölçeklenmiş tahmin,
        üst ucu taban tahmin olmalı (lo < hi)."""
        current = [5.0] * 40 + [15.0] * 20  # genel ort 8.33, son 1/3 ort 15
        result = frontend_main._remaining_time_range_min(600.0, 50.0, current)
        lo, hi = result
        self.assertLess(lo, hi)
        base = 600.0 * 50.0 / 50.0 / 60
        self.assertAlmostEqual(hi, base, places=6)
        self.assertAlmostEqual(lo, base * (500.0 / 60) / 15.0, places=6)

    def test_remaining_time_range_full_battery_returns_none(self):
        self.assertIsNone(frontend_main._remaining_time_range_min(600.0, 100.0, [5.0] * 60))
        self.assertIsNone(frontend_main._remaining_time_range_min(0.0, 50.0, [5.0] * 60))

    def test_remaining_time_range_short_window_falls_back_to_single(self):
        """10 örnekten kısa 'son pencere' gürültüye açık — aralık yerine
        tek değer dönmeli."""
        result = frontend_main._remaining_time_range_min(600.0, 50.0, [5.0] * 12)
        lo, hi = result
        self.assertEqual(lo, hi)


    # --- Karşılaştırma dialogu yardımcıları ----------------------------------

    def test_middle_ellipsis_keeps_short_names_and_truncates_long(self):
        self.assertEqual(frontend_main._middle_ellipsis("kisa.ulg"), "kisa.ulg")
        long_name = "ArduPlane-GpsSensorPreArmEAHRS-00000115.BIN"
        shortened = frontend_main._middle_ellipsis(long_name)
        self.assertLessEqual(len(shortened), 28)
        self.assertIn("…", shortened)
        # Kuyruk (numara + uzantı) görünür kalmalı ki benzer adlar ayırt edilsin.
        self.assertTrue(shortened.endswith(long_name[-10:]), shortened)

    def test_comparison_mixed_vehicles_detected(self):
        mixed = [("a", {"vehicle": "Multirotor"}), ("b", {"vehicle": "Sabit Kanat"})]
        same = [("a", {"vehicle": "Multirotor"}), ("b", {"vehicle": "Multirotor"})]
        unknown = [("a", {"vehicle": "Multirotor"}), ("b", {"vehicle": None})]
        self.assertTrue(frontend_main._comparison_has_mixed_vehicles(mixed))
        self.assertFalse(frontend_main._comparison_has_mixed_vehicles(same))
        # Tipi bilinmeyen "karışık" sayılmamalı (yanlış alarm olurdu).
        self.assertFalse(frontend_main._comparison_has_mixed_vehicles(unknown))

    def test_comparison_notable_cells_flags_two_times_median(self):
        """39 mΩ vs 10-14 mΩ senaryosu: max, diğerlerinin medyanının 2 katını
        aşınca işaretlenir; 1.5 katı gibi sınır durumlar İŞARETLENMEZ (kuralın
        muhafazakârlığının mutasyon bekçisi)."""
        flagged = [("u1", {"resistance_mohm": 10.0, "voltage_sag_pct": None}),
                   ("u2", {"resistance_mohm": 14.0, "voltage_sag_pct": None}),
                   ("u3", {"resistance_mohm": 39.0, "voltage_sag_pct": None})]
        notable = frontend_main._comparison_notable_cells(flagged)
        self.assertEqual(notable, [("resistance_mohm", 2, "u3")])

        borderline = [("u1", {"resistance_mohm": 10.0, "voltage_sag_pct": None}),
                      ("u2", {"resistance_mohm": 15.0, "voltage_sag_pct": None})]
        self.assertEqual(frontend_main._comparison_notable_cells(borderline), [])

        single = [("u1", {"resistance_mohm": 39.0, "voltage_sag_pct": None}),
                  ("u2", {"resistance_mohm": None, "voltage_sag_pct": None})]
        self.assertEqual(frontend_main._comparison_notable_cells(single), [])

    def test_comparison_current_range_negative_gets_warning_prefix(self):
        metrics = {"current_min": -0.7, "current_max": 23.0}
        self.assertTrue(
            frontend_main._format_comparison_value("current_range", metrics).startswith("⚠"))
        metrics_ok = {"current_min": 0.0, "current_max": 23.0}
        self.assertFalse(
            frontend_main._format_comparison_value("current_range", metrics_ok).startswith("⚠"))


if __name__ == "__main__":
    unittest.main()
