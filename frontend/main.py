"""İHA Güç/Telemetri Log Analiz Aracı - Frontend giriş noktası.

Seçilen log dosyası backend'e (C++) verilir; backend onu parse edip
shared/power_log_schema.md şemasına uygun bir JSON üretir, frontend de bu
JSON'u okuyup grafiği çizer. ArduPilot .bin (batarya + motor/ESC) ve PX4 .ulog
(şimdilik sadece batarya) destekleniyor; format dosya içeriğinden (magic byte)
otomatik algılanıyor.
"""

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import customtkinter as ctk
from tkinter import filedialog
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

BACKEND_EXE = Path(__file__).resolve().parent.parent / "backend" / "build" / "power_log_backend.exe"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

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
COLOR_VOLTAGE = "#3987e5"   # kategorik slot 1 (mavi)
COLOR_CURRENT = "#e66767"   # kategorik slot 8 (kırmızı)
COLOR_CRITICAL = "#d03b3b"  # durum paleti: kritik/hata

# Motor akımı paneli için kategorik slotlar (sabit sırada, kategori kimliği).
# Motor sayısı 4'ü geçerse (hexa/octo) baştan sarılır.
MOTOR_COLORS = ["#3987e5", "#008300", "#d55181", "#c98500"]

# Isı haritası için ardışık (sequential) renk skalası: koyu yüzeyden başlayıp
# markaya ait maviden geçip açık bir tona çıkar (düşük değer yüzeyde erir,
# yüksek değer parlar) — koyu temada okunaklı olması için bu yönde.
MOTOR_HEATMAP_COLORS = ["#141a22", COLOR_VOLTAGE, "#cde2fb"]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("İHA Güç/Telemetri Log Analiz Aracı")
        self.geometry("1050x850")
        self.configure(fg_color=SURFACE)

        self.motor_view_mode = "line"  # "line" ya da "heatmap"
        self._motor_colorbar = None
        self._last_motors = None

        self._build_toolbar()
        self._build_stats_row()
        self._build_status_label()
        self._build_motor_view_toggle()
        self._build_plot_area()

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

        load_button = ctk.CTkButton(
            toolbar, text="Log Dosyası Yükle", command=self._on_load_file_click
        )
        load_button.pack(side="left", padx=(0, 12))

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
        file_path = filedialog.askopenfilename(
            title="Log Dosyası Seç",
            initialdir=str(DATA_DIR),
            filetypes=[
                ("Uçuş logları", "*.bin *.ulog"),
                ("ArduPilot log (.bin)", "*.bin"),
                ("PX4 log (.ulog)", "*.ulog"),
                ("Tüm dosyalar", "*.*"),
            ],
        )
        if not file_path:
            return

        self.file_label.configure(text=file_path)
        self.status_label.configure(text="")
        try:
            data = self._run_backend(file_path)
            self._plot_power_data(data)
        except Exception as error:
            self.status_label.configure(text=f"⚠ Hata: {error}")

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
        """Batarya voltajı/toplam akımı ve motor akımlarını, ortak zaman
        eksenini paylaşan panellerde çizer."""
        battery = data["battery"]
        time_s = battery["time_s"]
        voltage_v = battery["voltage_v"]
        current_a = battery["current_a"]

        self._update_stats(battery)

        self.ax_voltage.clear()
        self._style_axes(self.ax_voltage, "Voltaj (V)")
        self.ax_voltage.plot(time_s, voltage_v, color=COLOR_VOLTAGE, linewidth=2)

        self.ax_current.clear()
        self._style_axes(self.ax_current, "Toplam Akım (A)")
        self.ax_current.plot(time_s, current_a, color=COLOR_CURRENT, linewidth=2)

        self._last_motors = data.get("motors", [])
        self._plot_motor_currents(self._last_motors)

        self.figure.tight_layout()
        self.canvas.draw()

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
            color = MOTOR_COLORS[i % len(MOTOR_COLORS)]
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

        cmap = LinearSegmentedColormap.from_list("motor_heat", MOTOR_HEATMAP_COLORS)
        extent = [all_times[0], all_times[-1], 0.5, len(motors) + 0.5]
        image = self.ax_motors.imshow(
            grid, aspect="auto", origin="lower", extent=extent, cmap=cmap,
        )
        self.ax_motors.set_yticks(range(1, len(motors) + 1))
        self.ax_motors.set_yticklabels([f"Motor {motor['id']}" for motor in motors])

        self._motor_colorbar = self.figure.colorbar(image, ax=self.ax_motors, pad=0.01)
        self._motor_colorbar.set_label("Akım (A)", color=TEXT_SECONDARY)
        self._motor_colorbar.ax.tick_params(colors=TEXT_MUTED)

    def _update_stats(self, battery: dict):
        """Üstteki özet satırını (süre, örnek sayısı, min/maks aralıklar) günceller."""
        time_s = battery["time_s"]
        voltage_v = battery["voltage_v"]
        current_a = battery["current_a"]

        if not time_s:
            for label in self.stat_labels.values():
                label.configure(text="—")
            return

        self.stat_labels["duration"].configure(text=f"{time_s[-1]:.1f} s")
        self.stat_labels["samples"].configure(text=str(len(time_s)))
        self.stat_labels["voltage_range"].configure(
            text=f"{min(voltage_v):.2f}–{max(voltage_v):.2f} V"
        )
        self.stat_labels["current_range"].configure(
            text=f"{min(current_a):.1f}–{max(current_a):.1f} A"
        )


if __name__ == "__main__":
    app = App()
    app.mainloop()
