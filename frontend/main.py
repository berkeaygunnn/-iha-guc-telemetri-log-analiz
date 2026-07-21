"""İHA Güç/Telemetri Log Analiz Aracı - Frontend giriş noktası.

Seçilen log dosyası backend'e (C++) verilir; backend onu parse edip
shared/power_log_schema.md şemasına uygun bir JSON üretir, frontend de bu
JSON'u okuyup grafiği çizer. ArduPilot .bin ve PX4 .ulog, ikisi de batarya ve
motor/ESC verisiyle destekleniyor; format dosya içeriğinden (magic byte)
otomatik algılanıyor.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import customtkinter as ctk
from tkinter import filedialog, PhotoImage
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

def _find_backend_exe() -> Path:
    """Windows'ta .exe uzantılı, Linux/macOS'ta uzantısız üretildiği için
    ikisini de dener (bkz. backend/tests/run_tests.py'deki aynı mantık).

    PyInstaller ile paketlenmiş haldeyken (`sys.frozen`) backend.exe, spec
    dosyasındaki `binaries` girdisiyle ana .exe'yle aynı klasöre kopyalanıyor;
    geliştirme ortamında ise CMake'in ürettiği `backend/build/` kullanılıyor.
    """
    if getattr(sys, "frozen", False):
        build_dir = Path(sys.executable).resolve().parent
    else:
        build_dir = Path(__file__).resolve().parent.parent / "backend" / "build"

    for name in ("power_log_backend.exe", "power_log_backend"):
        candidate = build_dir / name
        if candidate.exists():
            return candidate
    return build_dir / "power_log_backend.exe"  # bulunamadıysa hata mesajında gösterilecek varsayılan yol


def _find_icon_path() -> Path:
    """Pencere ikonu için PNG dosyasının yolu. `_find_backend_exe()` ile aynı
    frozen/geliştirme ayrımı: paketlenmişken ana .exe'yle aynı klasördeki
    `assets/`e, geliştirmede `frontend/assets/`e bakar."""
    if getattr(sys, "frozen", False):
        assets_dir = Path(sys.executable).resolve().parent / "assets"
    else:
        assets_dir = Path(__file__).resolve().parent / "assets"
    return assets_dir / "icon.png"


BACKEND_EXE = _find_backend_exe()
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ICON_PATH = _find_icon_path()

# Uygulama genelinde kullanılacak tema ayarları (koyu tema + mavi renk paleti)
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Koyu tema renk paleti (dataviz rehberinin doğrulanmış referans paletinden).
# Grafik burada kendi renklerini tanımlıyor ki CustomTkinter'ın koyu temasıyla
# birebir uyumlu olsun; matplotlib'in varsayılan beyaz arka planı kullanılmıyor.
SURFACE = "#1a1a19"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#c3c2b7"
TEXT_MUTED = "#898781"
GRIDLINE = "#2c2c2a"
AXIS_LINE = "#383835"
COLOR_CRITICAL = "#d03b3b"  # durum paleti: kritik/hata (backend hatası)
COLOR_WARNING = "#d9a334"   # durum paleti: uyarı (backend'in kural tabanlı yorumları)

# Birden fazla batarya/motor olduğunda her birine sabit sırada, kategorik bir
# renk atamak için (kategori kimliği). Bir bataryanın voltaj ve akım çizgisi
# iki farklı panelde de AYNI rengi taşır ki paneller arasında göz takibiyle
# eşleştirilebilsin. Sayı 4'ü geçerse (hexa/octo, çoklu batarya) baştan sarılır.
SERIES_COLORS = ["#3987e5", "#008300", "#d55181", "#c98500"]

# Isı haritası için ardışık (sequential) renk skalası: koyu yüzeyden başlayıp
# markaya ait maviden geçip açık bir tona çıkar (düşük değer yüzeyde erir,
# yüksek değer parlar) — koyu temada okunaklı olması için bu yönde. Hem motor
# hem batarya/busbar ısı haritası aynı skalayı kullanıyor (ikisi de "akım
# yoğunluğu" anlamında aynı şeyi gösteriyor, aynı anda ekranda olmuyorlar).
HEATMAP_COLORS = ["#141a22", SERIES_COLORS[0], "#cde2fb"]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("İHA Güç/Telemetri Log Analiz Aracı")
        self.geometry("1050x850")
        self.minsize(700, 600)  # panel/toolbar düzeni bundan daha küçükte bozuluyor
        self.configure(fg_color=SURFACE)

        if ICON_PATH.exists():
            self.iconphoto(True, PhotoImage(file=str(ICON_PATH)))

        self.motor_view_mode = "line"  # "line" ya da "heatmap"
        self._motor_colorbar = None
        self._last_motors = None

        self.battery_view_mode = "line"  # "line" ya da "heatmap" (busbar yüklenmesi)
        self._battery_colorbar = None
        self._last_batteries = None

        self._build_toolbar()
        self._build_stats_row()
        self._build_warnings_area()
        self._build_status_label()
        self._build_battery_view_toggle()
        self._build_motor_view_toggle()
        self._build_plot_area()

    def _build_battery_view_toggle(self):
        """Toplam akım (busbar yüklenmesi) panelini çizgi grafiği/ısı haritası
        arasında değiştiren seçici. Birden fazla batarya (ör. yedekli güç
        hattı) olduğunda hangi busbar'ın ne zaman daha yüklü olduğunu
        karşılaştırmak için kullanışlı."""
        toggle_row = ctk.CTkFrame(self, fg_color="transparent")
        toggle_row.pack(side="top", fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(
            toggle_row, text="Busbar Görünümü:", text_color=TEXT_MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(0, 8))

        self.battery_view_toggle = ctk.CTkSegmentedButton(
            toggle_row, values=["Çizgi Grafiği", "Isı Haritası"],
            command=self._on_battery_view_change,
        )
        self.battery_view_toggle.set("Çizgi Grafiği")
        self.battery_view_toggle.pack(side="left")

    def _on_battery_view_change(self, value: str):
        self.battery_view_mode = "heatmap" if value == "Isı Haritası" else "line"
        if self._last_batteries is not None:
            self._plot_battery_currents(self._last_batteries)
            self.figure.tight_layout()
            self.canvas.draw()

    def _build_motor_view_toggle(self):
        """Motor panelini çizgi grafiği/ısı haritası arasında değiştiren seçici."""
        toggle_row = ctk.CTkFrame(self, fg_color="transparent")
        toggle_row.pack(side="top", fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(
            toggle_row, text="Motor Görünümü:", text_color=TEXT_MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(0, 8))

        self.motor_view_toggle = ctk.CTkSegmentedButton(
            toggle_row, values=["Çizgi Grafiği", "Isı Haritası"],
            command=self._on_motor_view_change,
        )
        self.motor_view_toggle.set("Çizgi Grafiği")
        self.motor_view_toggle.pack(side="left")

    def _on_motor_view_change(self, value: str):
        self.motor_view_mode = "heatmap" if value == "Isı Haritası" else "line"
        if self._last_motors is not None:
            self._plot_motor_currents(self._last_motors)
            self.figure.tight_layout()
            self.canvas.draw()

    def _build_toolbar(self):
        """Üst kısımdaki dosya yükleme butonu ve seçilen dosya etiketi."""
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(side="top", fill="x", padx=16, pady=(16, 8))

        self.load_button = ctk.CTkButton(
            toolbar, text="Log Dosyası Yükle", command=self._on_load_file_click
        )
        self.load_button.pack(side="left", padx=(0, 12))

        self.file_label = ctk.CTkLabel(
            toolbar, text="Henüz dosya seçilmedi.", text_color=TEXT_SECONDARY
        )
        self.file_label.pack(side="left")

    def _build_stats_row(self):
        """Dosya yüklendikten sonra süre/örnek sayısı/aralık gibi özet değerleri gösteren satır."""
        self.stats_row = ctk.CTkFrame(self, fg_color="transparent")
        self.stats_row.pack(side="top", fill="x", padx=16, pady=(0, 8))

        self.stat_labels = {}
        for key, title in [
            ("duration", "Süre"),
            ("samples", "Örnek Sayısı"),
            ("voltage_range", "Voltaj Aralığı"),
            ("current_range", "Akım Aralığı"),
        ]:
            tile = ctk.CTkFrame(self.stats_row, fg_color=GRIDLINE, corner_radius=8)
            tile.pack(side="left", padx=(0, 8), ipadx=12, ipady=8)

            ctk.CTkLabel(
                tile, text=title, text_color=TEXT_MUTED, font=ctk.CTkFont(size=11)
            ).pack(anchor="w")
            value_label = ctk.CTkLabel(
                tile, text="—", text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=15, weight="bold")
            )
            value_label.pack(anchor="w")
            self.stat_labels[key] = value_label

    def _build_warnings_area(self):
        """Backend'in kural tabanlı ürettiği uyarıları (ör. aşırı voltaj düşümü,
        motor akım dengesizliği) gösteren satır. Uyarı yoksa boş kalır, ekstra
        yer kaplamaz."""
        self.warnings_label = ctk.CTkLabel(
            self, text="", text_color=COLOR_WARNING, justify="left", anchor="w"
        )
        self.warnings_label.pack(side="top", fill="x", padx=16, pady=(0, 4))

    def _update_warnings(self, warnings: list):
        if not warnings:
            self.warnings_label.configure(text="")
            return
        self.warnings_label.configure(text="\n".join(f"⚠ {message}" for message in warnings))

    def _build_status_label(self):
        """Hata mesajları için ayrı bir durum satırı (dosya etiketiyle karışmasın diye)."""
        self.status_label = ctk.CTkLabel(self, text="", text_color=COLOR_CRITICAL)
        self.status_label.pack(side="top", fill="x", padx=16, pady=(0, 4))

    def _build_plot_area(self):
        """Voltaj / toplam akım / motor akımını ayrı panellerde (ortak zaman
        eksenini paylaşarak) çizen grafik alanı.

        Not: farklı ölçekli veriler için tek grafikte çift y-ekseni (twinx)
        kullanmak yanıltıcı olabiliyor; bunun yerine üst üste panel tercih edildi.
        """
        figure = Figure(figsize=(5, 6), dpi=100)
        figure.set_facecolor(SURFACE)

        self.ax_voltage = figure.add_subplot(311)
        self.ax_current = figure.add_subplot(312, sharex=self.ax_voltage)
        self.ax_motors = figure.add_subplot(313, sharex=self.ax_voltage)
        self._style_axes(self.ax_voltage, "Voltaj (V)")
        self._style_axes(self.ax_current, "Toplam Akım (A)")
        self._style_axes(self.ax_motors, "Motor Akımı (A)")
        self.ax_motors.set_xlabel("Zaman (s)", color=TEXT_SECONDARY)
        figure.tight_layout()

        self.figure = figure
        self.canvas = FigureCanvasTkAgg(figure, master=self)
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True, padx=16, pady=(0, 16))

    def _style_axes(self, ax, ylabel: str):
        """Bir eksene koyu tema görünümünü (arka plan, ince gridline, soluk çerçeve) uygular."""
        ax.set_facecolor(SURFACE)
        ax.set_ylabel(ylabel, color=TEXT_SECONDARY)
        ax.grid(True, color=GRIDLINE, linewidth=1, linestyle="-")
        ax.set_axisbelow(True)
        ax.tick_params(colors=TEXT_MUTED)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("bottom", "left"):
            ax.spines[side].set_color(AXIS_LINE)

    def _on_load_file_click(self):
        """Dosya seçme penceresini açar; sadece .bin ve .ulog dosyalarını listeler."""
        dialog_kwargs = {}
        if DATA_DIR.exists():  # paketlenmiş dağıtımda örnek data/ klasörü bulunmaz
            dialog_kwargs["initialdir"] = str(DATA_DIR)

        file_path = filedialog.askopenfilename(
            title="Log Dosyası Seç",
            filetypes=[
                ("Uçuş logları", "*.bin *.ulog"),
                ("ArduPilot log (.bin)", "*.bin"),
                ("PX4 log (.ulog)", "*.ulog"),
                ("Tüm dosyalar", "*.*"),
            ],
            **dialog_kwargs,
        )
        if not file_path:
            return

        self.file_label.configure(text=file_path)
        self.status_label.configure(text="İşleniyor...", text_color=TEXT_SECONDARY)
        self.load_button.configure(state="disabled")
        self.update_idletasks()  # "İşleniyor..." metnini backend bitmeden ekrana yansıt

        try:
            data = self._run_backend(file_path)
            self._plot_power_data(data)
            self.status_label.configure(text="")
        except Exception as error:
            self.status_label.configure(text=f"⚠ Hata: {error}", text_color=COLOR_CRITICAL)
        finally:
            self.load_button.configure(state="normal")

    def _run_backend(self, input_path: str) -> dict:
        """Backend'i seçilen log dosyasıyla çalıştırıp ürettiği JSON'u okur."""
        if not BACKEND_EXE.exists():
            raise RuntimeError(
                f"Backend derlenmemiş: {BACKEND_EXE} bulunamadı. Önce CMake ile derleyin."
            )

        output_path = Path(tempfile.gettempdir()) / "iha_power_log_output.json"
        result = subprocess.run(
            [str(BACKEND_EXE), input_path, str(output_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or f"Backend hata koduyla sonlandı: {result.returncode}"
            raise RuntimeError(message)

        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _plot_power_data(self, data: dict):
        """Her bataryanın voltajı/akımı ve motor akımlarını, ortak zaman
        eksenini paylaşan panellerde çizer. Birden fazla batarya varsa her
        biri kendi rengiyle (iki panelde de aynı renk) çizilir."""
        batteries = data.get("batteries", [])
        self._update_stats(batteries)
        self._update_warnings(data.get("warnings", []))

        self.ax_voltage.clear()
        self._style_axes(self.ax_voltage, "Voltaj (V)")

        for i, battery in enumerate(batteries):
            color = SERIES_COLORS[i % len(SERIES_COLORS)]
            label = f"Batarya {battery['id']}"
            self.ax_voltage.plot(battery["time_s"], battery["voltage_v"], color=color, linewidth=2, label=label)

        if len(batteries) > 1:
            self.ax_voltage.legend(
                loc="upper right", facecolor=SURFACE, edgecolor=AXIS_LINE,
                labelcolor=TEXT_SECONDARY, fontsize=9,
            )

        self._last_batteries = batteries
        self._plot_battery_currents(batteries)

        self._last_motors = data.get("motors", [])
        self._plot_motor_currents(self._last_motors)

        self.figure.tight_layout()
        self.canvas.draw()

    def _plot_battery_currents(self, batteries: list):
        """Toplam akım (busbar yüklenmesi) panelini seçili görünüme (çizgi/ısı
        haritası) göre çizer. Motor paneliyle aynı sebeple (colorbar grid
        yerleşimini kalıcı değiştiriyor) eksen sıfırdan yeniden oluşturulur."""
        self.figure.delaxes(self.ax_current)
        self.ax_current = self.figure.add_subplot(312, sharex=self.ax_voltage)
        self._battery_colorbar = None

        if self.battery_view_mode == "heatmap":
            self._plot_battery_heatmap(batteries)
        else:
            self._plot_battery_lines(batteries)

    def _plot_battery_lines(self, batteries: list):
        """Her bataryanın (busbar'ın) toplam akımını kendi renginde çizer."""
        self._style_axes(self.ax_current, "Toplam Akım (A)")

        for i, battery in enumerate(batteries):
            color = SERIES_COLORS[i % len(SERIES_COLORS)]
            self.ax_current.plot(
                battery["time_s"], battery["current_a"], color=color, linewidth=2,
                label=f"Batarya {battery['id']}",
            )

        if len(batteries) > 1:
            self.ax_current.legend(
                loc="upper right", facecolor=SURFACE, edgecolor=AXIS_LINE,
                labelcolor=TEXT_SECONDARY, fontsize=9,
            )

    def _plot_battery_heatmap(self, batteries: list):
        """Batarya/busbar x zaman ısı haritası: renk = o andaki toplam akım.

        Birden fazla batarya (ör. yedekli güç hattı) olduğunda hangi busbar'ın
        ne zaman daha fazla yüklendiğini karşılaştırmak için kullanışlı; tek
        bataryalı loglarda tek satırlık bir şerit olarak görünür.
        """
        self._style_axes(self.ax_current, "Busbar")
        self.ax_current.grid(False)  # ısı haritasında gridline gürültü yapar

        if not batteries:
            return

        all_times = sorted({t for battery in batteries for t in battery["time_s"]})
        grid = np.array([
            np.interp(all_times, battery["time_s"], battery["current_a"])
            for battery in batteries
        ])

        cmap = LinearSegmentedColormap.from_list("battery_heat", HEATMAP_COLORS)
        extent = [all_times[0], all_times[-1], 0.5, len(batteries) + 0.5]
        image = self.ax_current.imshow(
            grid, aspect="auto", origin="lower", extent=extent, cmap=cmap,
        )
        self.ax_current.set_yticks(range(1, len(batteries) + 1))
        self.ax_current.set_yticklabels([f"Batarya {battery['id']}" for battery in batteries])

        self._battery_colorbar = self.figure.colorbar(image, ax=self.ax_current, pad=0.01)
        self._battery_colorbar.set_label("Akım (A)", color=TEXT_SECONDARY)
        self._battery_colorbar.ax.tick_params(colors=TEXT_MUTED)

    def _plot_motor_currents(self, motors: list):
        """Motor panelini seçili görünüme (çizgi/ısı haritası) göre çizer.

        Isı haritası bir colorbar eksen ekliyor ve bu, panelin grid
        yerleşimini kalıcı değiştiriyor; bir sonraki çizimde (özellikle
        çizgi grafiğine dönüşte) eskisini silmeye çalışmak yerine ekseni
        sıfırdan yeniden oluşturmak matplotlib'de daha güvenilir.
        """
        self.figure.delaxes(self.ax_motors)
        self.ax_motors = self.figure.add_subplot(313, sharex=self.ax_voltage)
        self._motor_colorbar = None

        if self.motor_view_mode == "heatmap":
            self._plot_motor_heatmap(motors)
        else:
            self._plot_motor_lines(motors)

    def _plot_motor_lines(self, motors: list):
        """Her motorun akımını kendi renginde çizer; motor yoksa panel boş kalır."""
        self._style_axes(self.ax_motors, "Motor Akımı (A)")
        self.ax_motors.set_xlabel("Zaman (s)", color=TEXT_SECONDARY)

        for i, motor in enumerate(motors):
            color = SERIES_COLORS[i % len(SERIES_COLORS)]
            self.ax_motors.plot(
                motor["time_s"], motor["current_a"], color=color, linewidth=2,
                label=f"Motor {motor['id']}",
            )

        if motors:
            self.ax_motors.legend(
                loc="upper right", facecolor=SURFACE, edgecolor=AXIS_LINE,
                labelcolor=TEXT_SECONDARY, fontsize=9,
            )

    def _plot_motor_heatmap(self, motors: list):
        """Motor x zaman ısı haritası: renk = o andaki akım.

        Motorların örnekleme zamanları birebir aynı olmayabileceği için, ortak
        bir zaman eksenine (tüm motorların zamanlarının birleşimi) interpolate
        edilir.
        """
        self._style_axes(self.ax_motors, "Motor")
        self.ax_motors.set_xlabel("Zaman (s)", color=TEXT_SECONDARY)
        self.ax_motors.grid(False)  # ısı haritasında gridline gürültü yapar

        if not motors:
            return

        all_times = sorted({t for motor in motors for t in motor["time_s"]})
        grid = np.array([
            np.interp(all_times, motor["time_s"], motor["current_a"])
            for motor in motors
        ])

        cmap = LinearSegmentedColormap.from_list("motor_heat", HEATMAP_COLORS)
        extent = [all_times[0], all_times[-1], 0.5, len(motors) + 0.5]
        image = self.ax_motors.imshow(
            grid, aspect="auto", origin="lower", extent=extent, cmap=cmap,
        )
        self.ax_motors.set_yticks(range(1, len(motors) + 1))
        self.ax_motors.set_yticklabels([f"Motor {motor['id']}" for motor in motors])

        self._motor_colorbar = self.figure.colorbar(image, ax=self.ax_motors, pad=0.01)
        self._motor_colorbar.set_label("Akım (A)", color=TEXT_SECONDARY)
        self._motor_colorbar.ax.tick_params(colors=TEXT_MUTED)

    def _update_stats(self, batteries: list):
        """Üstteki özet satırını günceller: tüm bataryaların birleşimi olarak
        (süre = en uzunu, voltaj/akım aralığı = hepsinin ortak min-maks'ı)."""
        all_voltage = [v for battery in batteries for v in battery["voltage_v"]]
        all_current = [c for battery in batteries for c in battery["current_a"]]
        last_times = [battery["time_s"][-1] for battery in batteries if battery["time_s"]]
        total_samples = sum(len(battery["time_s"]) for battery in batteries)

        if not last_times:
            for label in self.stat_labels.values():
                label.configure(text="—")
            return

        self.stat_labels["duration"].configure(text=f"{max(last_times):.1f} s")
        self.stat_labels["samples"].configure(text=str(total_samples))
        self.stat_labels["voltage_range"].configure(
            text=f"{min(all_voltage):.2f}–{max(all_voltage):.2f} V"
        )
        self.stat_labels["current_range"].configure(
            text=f"{min(all_current):.1f}–{max(all_current):.1f} A"
        )


if __name__ == "__main__":
    app = App()
    app.mainloop()
