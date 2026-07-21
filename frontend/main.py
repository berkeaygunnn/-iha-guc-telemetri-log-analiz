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
from tkinter import Canvas, filedialog, PhotoImage
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
# Son kullanılan dosyalar listesi kullanıcının home klasöründe tutulur (dev/
# paketlenmiş ayrımından bağımsız, her zaman yazılabilir). Testler bu sabiti
# monkeypatch'leyerek gerçek kullanıcı verisini etkilemeden test edebiliyor.
RECENT_FILES_PATH = Path.home() / ".iha_guc_telemetri_analiz" / "recent_files.json"
MAX_RECENT_FILES = 5

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

# Giriş (splash) ekranı için ayrı, "dikkat çekici" bir palet — sadece landing
# ekranında kullanılır, analiz ekranının sakin koyu teması (SURFACE vb.)
# bundan etkilenmez. Ana mavi (SERIES_COLORS[0]) ile aynı aile, ama daha
# doygun/parlak.
LANDING_BG = "#07131f"              # koyu lacivert taban
LANDING_ACCENT = "#3987e5"          # mevcut ana mavi ile tutarlılık
LANDING_ACCENT_BRIGHT = "#5fd4ff"   # parlak camgöbeği, ikon/vurgu için
LANDING_TEXT = "#eaf4ff"

# Birden fazla batarya/motor olduğunda her birine sabit sırada, kategorik bir
# renk atamak için (kategori kimliği). Bir bataryanın voltaj ve akım çizgisi
# iki farklı panelde de AYNI rengi taşır ki paneller arasında göz takibiyle
# eşleştirilebilsin. 8 renk hexa/octokopterin tüm motorlarını (6/8) tek
# döngüde ayrı renkte tutmaya yetiyor; bunun ötesine geçilirse (nadir) renk
# döngüsü _series_style()'daki çizgi stiliyle birlikte tekrar başa sarılır.
SERIES_COLORS = [
    "#3987e5", "#008300", "#d55181", "#c98500",
    "#8a5fd1", "#1fada4", "#e0574a", "#b8b83c",
]
# Renk paleti tükenip (>8 seri) baştan sarıldığında ikinci "tur"un çizgi
# stilini değiştirerek (ör. motor 1 ve motor 9 aynı renkte ama biri düz,
# diğeri kesikli çizgi) yine de ayırt edilebilir kalmasını sağlar.
SERIES_LINESTYLES = ["-", "--", ":", "-."]


def _series_style(index: int) -> tuple:
    """index'e göre (renk, çizgi_stili) döner; SERIES_COLORS tükenirse
    çizgi stili değişerek seriler yine ayırt edilebilir kalır."""
    color = SERIES_COLORS[index % len(SERIES_COLORS)]
    linestyle = SERIES_LINESTYLES[(index // len(SERIES_COLORS)) % len(SERIES_LINESTYLES)]
    return color, linestyle

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

        # Uygulama iki "ekran" (frame) arasında geçiş yapıyor: açılışta
        # gösterilen giriş ekranı ve dosya yüklendikten sonra gösterilen
        # analiz ekranı. İkisi de aynı pencerenin (self) çocuğu; ayrı bir
        # Toplevel pencere DEĞİL, sadece pack/pack_forget ile görünürlük
        # değiştiriliyor.
        self.landing_frame = ctk.CTkFrame(self, fg_color=LANDING_BG)
        self.analysis_frame = ctk.CTkFrame(self, fg_color=SURFACE)

        self._build_landing_screen()

        self._build_toolbar()
        self._build_stats_row()
        self._build_warnings_area()
        self._build_status_label()
        self._build_battery_view_toggle()
        self._build_motor_view_toggle()
        self._build_plot_area()

        # Klavye kısayolları: Ctrl+O dosya seç, Ctrl+S grafiği PNG kaydet.
        self.bind("<Control-o>", lambda event: self._on_load_file_click())
        self.bind("<Control-s>", lambda event: self._on_export_png_click())

        self._show_landing()

    def _show_landing(self):
        self.analysis_frame.pack_forget()
        self.landing_frame.pack(fill="both", expand=True)

    def _show_analysis(self):
        self.landing_frame.pack_forget()
        self.analysis_frame.pack(fill="both", expand=True)

    def _build_landing_screen(self):
        """Uygulama ilk açıldığında gösterilen karşılama ekranı: solda
        geçmiş dosyalar listesi, ortada bir drone ikonu ve büyük bir
        'Dosya Yükle' butonu. Analiz ekranından ayrı, daha canlı bir
        renk paleti kullanır (bkz. LANDING_* sabitleri)."""
        self._build_recent_sidebar()

        center = ctk.CTkFrame(self.landing_frame, fg_color="transparent")
        center.pack(side="left", fill="both", expand=True)

        # İçerik "center"ın tam ortasına place() ile konumlandırılıyor (sabit
        # pady değerleriyle üste yaslamak yerine). place, relx/rely oranlarını
        # kullandığı için pencere büyütülüp küçültüldükçe (ör. IDE'nin yanında
        # daraltılmış bir pencerede) içerik her zaman "center" alanının tam
        # ortasında kalacak şekilde otomatik yeniden hesaplanır.
        content_block = ctk.CTkFrame(center, fg_color="transparent")
        content_block.place(relx=0.56, rely=0.5, anchor="center")

        ctk.CTkLabel(
            content_block, text="İHA Güç/Telemetri Analiz", text_color=LANDING_TEXT,
            font=ctk.CTkFont(size=32, weight="bold"),
        ).pack(pady=(0, 8))
        ctk.CTkLabel(
            content_block, text="Uçuş logunu yükleyip güç/telemetri analizine başla",
            text_color=LANDING_ACCENT_BRIGHT, font=ctk.CTkFont(size=14),
        ).pack(pady=(0, 32))

        icon_canvas = Canvas(content_block, width=220, height=170, bg=LANDING_BG, highlightthickness=0)
        icon_canvas.pack(pady=(0, 24))
        self._draw_drone_icon(icon_canvas)

        ctk.CTkLabel(
            content_block,
            text="ArduPilot (.bin) veya PX4 (.ulog/.ulg) log dosyanızı yükleyin;\n"
                 "voltaj, akım ve motor verilerini görün.",
            text_color=TEXT_MUTED, font=ctk.CTkFont(size=12), justify="center",
        ).pack(pady=(0, 20))

        ctk.CTkButton(
            content_block, text="Dosya Yükle (Ctrl+O)", command=self._on_load_file_click,
            fg_color=LANDING_ACCENT_BRIGHT, text_color=LANDING_BG, hover_color=LANDING_ACCENT,
            font=ctk.CTkFont(size=16, weight="bold"), width=240, height=48, corner_radius=10,
        ).pack()

        ctk.CTkLabel(
            self.landing_frame, text="İHA Güç/Telemetri Log Analiz Aracı · MIT Lisansı ile açık kaynak",
            text_color="#3a5a78", font=ctk.CTkFont(size=11),
        ).pack(side="bottom", pady=12)

    def _build_recent_sidebar(self):
        """Giriş ekranının sol tarafındaki 'Geçmiş Dosyalar' paneli."""
        sidebar = ctk.CTkFrame(self.landing_frame, fg_color="#0c1e30", corner_radius=0, width=260)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ctk.CTkLabel(
            sidebar, text="Geçmiş Dosyalar", text_color=LANDING_TEXT,
            font=ctk.CTkFont(size=15, weight="bold"), anchor="w",
        ).pack(fill="x", padx=16, pady=(24, 12))

        self._recent_sidebar_list = ctk.CTkScrollableFrame(sidebar, fg_color="transparent")
        self._recent_sidebar_list.pack(fill="both", expand=True, padx=8, pady=(0, 16))

        self._refresh_recent_sidebar(self._load_recent_files())

    def _refresh_recent_sidebar(self, paths: list):
        """Giriş ekranındaki geçmiş dosyalar listesini yeniden çizer. Liste
        en fazla MAX_RECENT_FILES (5) öğe olduğu için her seferinde
        temizleyip yeniden oluşturmanın maliyeti önemsiz."""
        for child in self._recent_sidebar_list.winfo_children():
            child.destroy()

        if not paths:
            ctk.CTkLabel(
                self._recent_sidebar_list, text="Henüz geçmiş yok.",
                text_color=TEXT_MUTED, font=ctk.CTkFont(size=12),
            ).pack(pady=8)
            return

        for path_str in paths:
            path_obj = Path(path_str)
            ctk.CTkButton(
                self._recent_sidebar_list, text=path_obj.name, anchor="w",
                fg_color="transparent", hover_color=LANDING_ACCENT,
                text_color=LANDING_TEXT, font=ctk.CTkFont(size=12),
                command=lambda p=path_str: self._load_file(p),
            ).pack(fill="x", pady=2)

    def _draw_drone_icon(self, canvas: Canvas):
        """Orijinal, sade bir çeyrek-kanat (quadcopter) ikonu çizer: Canvas
        ilkelleriyle (çizgi + daire) oluşturulmuş vektörel bir siluet, bir
        fotoğraf DEĞİL — telif riski yok, her boyutta net görünür.

        Önceki sürüme göre daha net okunsun diye: kalın, yuvarlak uçlu kollar,
        pervane detayını belirtmek için rotor dairelerinin içinde çapraz
        kanatçıklar, gövdenin altında iki iniş ayağı eklendi."""
        cx, cy = 110, 85
        arm_len = 68
        rotor_r = 22

        # İniş ayakları (gövdenin arkasında kalsın diye kollardan/gövdeden önce çizilir).
        for dx in (-1, 1):
            canvas.create_line(
                cx + dx * 14, cy + 10, cx + dx * 20, cy + 34,
                fill=LANDING_ACCENT_BRIGHT, width=3, capstyle="round",
            )

        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            end_x, end_y = cx + dx * arm_len, cy + dy * arm_len * 0.55
            canvas.create_line(
                cx, cy, end_x, end_y, fill=LANDING_ACCENT_BRIGHT, width=6, capstyle="round",
            )
            canvas.create_oval(
                end_x - rotor_r, end_y - rotor_r, end_x + rotor_r, end_y + rotor_r,
                outline=LANDING_ACCENT_BRIGHT, width=3,
            )
            # Pervane kanatçıkları: rotor dairesinin içinde çapraz iki çizgi.
            canvas.create_line(
                end_x - rotor_r + 4, end_y, end_x + rotor_r - 4, end_y,
                fill=LANDING_ACCENT_BRIGHT, width=2, capstyle="round",
            )
            canvas.create_line(
                end_x, end_y - rotor_r + 4, end_x, end_y + rotor_r - 4,
                fill=LANDING_ACCENT_BRIGHT, width=2, capstyle="round",
            )

        # Gövde (kollardan sonra çizilir ki kolların gövdeye giriş noktaları temiz görünsün).
        canvas.create_oval(
            cx - 30, cy - 18, cx + 30, cy + 18,
            fill=LANDING_ACCENT, outline=LANDING_ACCENT_BRIGHT, width=2,
        )
        # Ön sensör/kamera noktası — gövdeye küçük bir karakter/detay katar.
        canvas.create_oval(
            cx - 6, cy - 6, cx + 6, cy + 6,
            fill=LANDING_BG, outline=LANDING_ACCENT_BRIGHT, width=2,
        )

    def _build_battery_view_toggle(self):
        """Toplam akım (busbar yüklenmesi) panelini çizgi grafiği/ısı haritası
        arasında değiştiren seçici. Birden fazla batarya (ör. yedekli güç
        hattı) olduğunda hangi busbar'ın ne zaman daha yüklü olduğunu
        karşılaştırmak için kullanışlı."""
        toggle_row = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
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
            self.canvas.draw()

    def _build_motor_view_toggle(self):
        """Motor panelini çizgi grafiği/ısı haritası arasında değiştiren seçici."""
        toggle_row = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
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
            self.canvas.draw()

    def _build_toolbar(self):
        """Üst kısımdaki dosya yükleme/dışa aktarma/temizle butonları, son
        kullanılan dosyalar açılır listesi ve seçilen dosya etiketi."""
        toolbar = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
        toolbar.pack(side="top", fill="x", padx=16, pady=(16, 8))

        self.home_button = ctk.CTkButton(
            toolbar, text="← Ana Sayfa", fg_color="transparent", border_width=1,
            border_color=AXIS_LINE, text_color=TEXT_SECONDARY, command=self._show_landing,
        )
        self.home_button.pack(side="left", padx=(0, 12))

        self.load_button = ctk.CTkButton(
            toolbar, text="Log Dosyası Yükle (Ctrl+O)", command=self._on_load_file_click
        )
        self.load_button.pack(side="left", padx=(0, 12))

        self._recent_label_to_path = {}
        self.recent_menu = ctk.CTkOptionMenu(
            toolbar, values=["(yok)"], command=self._on_recent_file_selected, width=200,
        )
        self.recent_menu.pack(side="left", padx=(0, 12))
        self._refresh_recent_menu(self._load_recent_files())

        self.file_label = ctk.CTkLabel(
            toolbar, text="Henüz dosya seçilmedi.", text_color=TEXT_SECONDARY
        )
        self.file_label.pack(side="left")

        self.export_button = ctk.CTkButton(
            toolbar, text="Grafiği Kaydet (PNG) (Ctrl+S)", command=self._on_export_png_click
        )
        self.export_button.pack(side="right")

        self.clear_button = ctk.CTkButton(
            toolbar, text="Temizle", fg_color="transparent", border_width=1,
            border_color=AXIS_LINE, text_color=TEXT_SECONDARY, command=self._on_clear_click,
        )
        self.clear_button.pack(side="right", padx=(0, 12))

    def _load_recent_files(self) -> list:
        """Kalıcı listeyi diskten okur; dosya yoksa/bozuksa boş liste döner
        (bu, ilk çalıştırma ya da elle silinmiş bir config dosyası için
        normal bir durum, hata sayılmaz)."""
        if not RECENT_FILES_PATH.exists():
            return []
        try:
            with open(RECENT_FILES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _save_recent_files(self, paths: list):
        RECENT_FILES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RECENT_FILES_PATH, "w", encoding="utf-8") as f:
            json.dump(paths, f, ensure_ascii=False, indent=2)

    def _add_recent_file(self, file_path: str):
        """Başarıyla yüklenen dosyayı listenin başına taşır (zaten varsa
        eski konumundan çıkarıp), en fazla MAX_RECENT_FILES kadar tutar."""
        paths = [p for p in self._load_recent_files() if p != file_path]
        paths.insert(0, file_path)
        paths = paths[:MAX_RECENT_FILES]
        self._save_recent_files(paths)
        self._refresh_recent_menu(paths)
        self._refresh_recent_sidebar(paths)

    def _refresh_recent_menu(self, paths: list):
        """Açılır listenin gösterdiği etiketleri günceller. Aynı dosya adı
        birden fazla klasörden geldiyse (nadir ama olası) üst klasör adıyla
        ayırt edilir, aksi halde sadece dosya adı gösterilir."""
        if not paths:
            self._recent_label_to_path = {}
            self.recent_menu.configure(values=["(yok)"], state="disabled")
            self.recent_menu.set("Son Kullanılanlar")
            return

        name_counts = {}
        for p in paths:
            name = Path(p).name
            name_counts[name] = name_counts.get(name, 0) + 1

        label_to_path = {}
        labels = []
        for p in paths:
            path_obj = Path(p)
            label = path_obj.name
            if name_counts[label] > 1:
                label = f"{label} ({path_obj.parent.name})"
            labels.append(label)
            label_to_path[label] = p

        self._recent_label_to_path = label_to_path
        self.recent_menu.configure(values=labels, state="normal")
        self.recent_menu.set("Son Kullanılanlar")

    def _on_recent_file_selected(self, label: str):
        file_path = self._recent_label_to_path.get(label)
        if file_path:
            self._load_file(file_path)

    def _build_stats_row(self):
        """Dosya yüklendikten sonra süre/örnek sayısı/aralık gibi özet değerleri gösteren satır."""
        self.stats_row = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
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

            # fill="x" + anchor="center": metin kutunun tam soluna değil,
            # ortasına doğru kayar (tile genişliği en uzun değere göre
            # otomatik ayarlanır, metin o genişlik içinde ortalanır).
            ctk.CTkLabel(
                tile, text=title, text_color=TEXT_MUTED, font=ctk.CTkFont(size=11)
            ).pack(fill="x", anchor="center")
            value_label = ctk.CTkLabel(
                tile, text="—", text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=15, weight="bold")
            )
            value_label.pack(fill="x", anchor="center")
            self.stat_labels[key] = value_label

    def _build_warnings_area(self):
        """Backend'in kural tabanlı ürettiği uyarıları (ör. aşırı voltaj düşümü,
        motor akım dengesizliği) gösteren satır. Uyarı yoksa boş kalır, ekstra
        yer kaplamaz."""
        self.warnings_label = ctk.CTkLabel(
            self.analysis_frame, text="", text_color=COLOR_WARNING, justify="left", anchor="w"
        )
        self.warnings_label.pack(side="top", fill="x", padx=16, pady=(0, 4))

    def _update_warnings(self, warnings: list):
        if not warnings:
            self.warnings_label.configure(text="")
            return
        self.warnings_label.configure(text="\n".join(f"⚠ {message}" for message in warnings))

    def _build_status_label(self):
        """Hata mesajları için ayrı bir durum satırı (dosya etiketiyle karışmasın diye)."""
        # wraplength: uzun backend hata mesajları (ör. system_power mesajı
        # birkaç cümle) pencere genişliğinde kesilmesin, alt satıra sarsın.
        self.status_label = ctk.CTkLabel(
            self.analysis_frame, text="", text_color=COLOR_CRITICAL, justify="left", anchor="w",
            wraplength=1000,
        )
        self.status_label.pack(side="top", fill="x", padx=16, pady=(0, 4))

    def _build_plot_area(self):
        """Voltaj / toplam akım / motor akımını ayrı panellerde (ortak zaman
        eksenini paylaşarak) çizen grafik alanı.

        Not: farklı ölçekli veriler için tek grafikte çift y-ekseni (twinx)
        kullanmak yanıltıcı olabiliyor; bunun yerine üst üste panel tercih edildi.
        """
        figure = Figure(figsize=(5, 6), dpi=100)
        figure.set_facecolor(SURFACE)

        # 2 sütunlu grid: sol sütun asıl grafikler, sağ (dar) sütun ısı
        # haritası modunda kullanılan colorbar için HER ZAMAN ayrılmış sabit
        # bir alan. Kenar boşlukları (left/right/top/bottom/hspace) burada
        # ELLE, SABİT olarak veriliyor ve bir daha hiç değiştirilmiyor —
        # tight_layout()/constrained_layout gibi "otomatik" yerleşim
        # motorları, her çizimde o anki içeriğin (ör. ısı haritasındaki
        # 'Batarya 1' gibi metin tick etiketlerinin sayısal etiketlerden
        # daha geniş olması) genişliğine göre kenar boşluklarını yeniden
        # hesaplıyor; bu da tam olarak kullanıcının şikayet ettiği "grafik
        # sağa/sola kayıyor" görünümüne yol açıyordu. Sabit kenar boşluğu,
        # görünüm (çizgi/ısı haritası) ne olursa olsun panelin konumunu
        # değiştirmez.
        gs = figure.add_gridspec(
            3, 2, width_ratios=[40, 1],
            left=0.11, right=0.90, top=0.97, bottom=0.08, hspace=0.35, wspace=0.05,
        )
        self._current_gs_cell = gs[1, 0]
        self._motors_gs_cell = gs[2, 0]

        self.ax_voltage = figure.add_subplot(gs[0, 0])
        self.ax_current = figure.add_subplot(self._current_gs_cell, sharex=self.ax_voltage)
        self.ax_motors = figure.add_subplot(self._motors_gs_cell, sharex=self.ax_voltage)
        self._style_axes(self.ax_voltage, "Voltaj (V)")
        self._style_axes(self.ax_current, "Toplam Akım (A)")
        self._style_axes(self.ax_motors, "Motor Akımı (A)")
        self.ax_motors.set_xlabel("Zaman (s)", color=TEXT_SECONDARY)

        self._current_cax = figure.add_subplot(gs[1, 1])
        self._current_cax.axis("off")
        self._motors_cax = figure.add_subplot(gs[2, 1])
        self._motors_cax.axis("off")

        self.figure = figure
        self.canvas = FigureCanvasTkAgg(figure, master=self.analysis_frame)
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
                ("Uçuş logları", "*.bin *.ulog *.ulg"),
                ("ArduPilot log (.bin)", "*.bin"),
                ("PX4 log (.ulog/.ulg)", "*.ulog *.ulg"),
                ("Tüm dosyalar", "*.*"),
            ],
            **dialog_kwargs,
        )
        if not file_path:
            return
        self._load_file(file_path)

    def _load_file(self, file_path: str):
        """Verilen log dosyasını backend'e verip sonucu çizer. Giriş
        ekranındaki 'Dosya Yükle' butonundan, geçmiş dosyalar listesinden
        (giriş ekranı ya da analiz ekranındaki dropdown) çağrılabilir;
        hangisinden çağrılırsa çağrılsın analiz ekranına geçer."""
        self._show_analysis()
        # Tam yol yerine sadece dosya adı gösterilir: uzun mutlak yollar
        # toolbar'daki diğer butonları (ör. "Grafiği Kaydet") ekran dışına
        # itip kesilmelerine yol açıyordu.
        self.file_label.configure(text=Path(file_path).name)
        self.status_label.configure(text="İşleniyor...", text_color=TEXT_SECONDARY)
        self.load_button.configure(state="disabled")
        self.update_idletasks()  # "İşleniyor..." metnini backend bitmeden ekrana yansıt

        try:
            data = self._run_backend(file_path)
            self._plot_power_data(data)
            self.status_label.configure(text="")
            self._add_recent_file(file_path)
        except Exception as error:
            self.status_label.configure(text=f"⚠ Hata: {error}", text_color=COLOR_CRITICAL)
        finally:
            self.load_button.configure(state="normal")

    def _on_export_png_click(self):
        """Mevcut grafiği kullanıcının seçtiği bir PNG dosyasına kaydeder."""
        file_path = filedialog.asksaveasfilename(
            title="Grafiği Kaydet",
            defaultextension=".png",
            filetypes=[("PNG görüntü", "*.png"), ("Tüm dosyalar", "*.*")],
        )
        if not file_path:
            return
        try:
            self.figure.savefig(file_path, facecolor=SURFACE)
            self.status_label.configure(text=f"Grafik kaydedildi: {file_path}", text_color=TEXT_SECONDARY)
        except OSError as error:
            self.status_label.configure(text=f"⚠ Grafik kaydedilemedi: {error}", text_color=COLOR_CRITICAL)

    def _on_clear_click(self):
        """Yüklü veriyi ve tüm panelleri başlangıç (boş) durumuna döndürür;
        ikinci bir dosyayı temiz bir ekrandan yüklemek isteyenler için."""
        self.file_label.configure(text="Henüz dosya seçilmedi.")
        self.status_label.configure(text="")
        self._update_warnings([])
        self._update_stats([])
        self._last_batteries = None
        self._last_motors = None

        self.ax_voltage.clear()
        self._style_axes(self.ax_voltage, "Voltaj (V)")
        self._plot_battery_currents([])
        self._plot_motor_currents([])

        self.canvas.draw()

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
            color, linestyle = _series_style(i)
            label = f"Batarya {battery['id']}"
            self.ax_voltage.plot(
                battery["time_s"], battery["voltage_v"], color=color, linestyle=linestyle,
                linewidth=2, label=label,
            )

        if len(batteries) > 1:
            self.ax_voltage.legend(
                loc="upper right", facecolor=SURFACE, edgecolor=AXIS_LINE,
                labelcolor=TEXT_SECONDARY, fontsize=9,
            )

        self._last_batteries = batteries
        self._plot_battery_currents(batteries)

        self._last_motors = data.get("motors", [])
        self._plot_motor_currents(self._last_motors)

        self.canvas.draw()

    def _plot_battery_currents(self, batteries: list):
        """Toplam akım (busbar yüklenmesi) panelini seçili görünüme (çizgi/ısı
        haritası) göre çizer. Ana eksen sıfırdan yeniden oluşturulur (grid/
        yticks gibi ayarların temiz kalması için); colorbar için ayrılan sabit
        eksen (_current_cax) ise SİLİNMEZ, sadece temizlenip gizlenir — bu
        sayede ana grafik alanının genişliği iki görünüm arasında değişmez."""
        self.figure.delaxes(self.ax_current)
        self.ax_current = self.figure.add_subplot(self._current_gs_cell, sharex=self.ax_voltage)
        self._current_cax.clear()
        self._current_cax.axis("off")
        self._battery_colorbar = None

        if self.battery_view_mode == "heatmap":
            self._plot_battery_heatmap(batteries)
        else:
            self._plot_battery_lines(batteries)

    def _plot_battery_lines(self, batteries: list):
        """Her bataryanın (busbar'ın) toplam akımını kendi renginde çizer."""
        self._style_axes(self.ax_current, "Toplam Akım (A)")

        for i, battery in enumerate(batteries):
            color, linestyle = _series_style(i)
            self.ax_current.plot(
                battery["time_s"], battery["current_a"], color=color, linestyle=linestyle,
                linewidth=2, label=f"Batarya {battery['id']}",
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
        # Panel başlığı çizgi grafiğiyle AYNI ("Toplam Akım (A)") kalır; ısı
        # haritasında farklı bir metne ("Busbar") değişmesi kafa karıştırıcıydı.
        self._style_axes(self.ax_current, "Toplam Akım (A)")
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

        self._current_cax.axis("on")
        self._battery_colorbar = self.figure.colorbar(image, cax=self._current_cax)
        self._battery_colorbar.set_label("Akım (A)", color=TEXT_SECONDARY)
        self._battery_colorbar.ax.tick_params(colors=TEXT_MUTED)

    def _plot_motor_currents(self, motors: list):
        """Motor panelini seçili görünüme (çizgi/ısı haritası) göre çizer.
        Ana eksen sıfırdan yeniden oluşturulur (grid/yticks temiz kalsın
        diye); colorbar için ayrılan sabit eksen (_motors_cax) SİLİNMEZ,
        sadece temizlenip gizlenir — ana grafik alanının genişliği iki
        görünüm arasında değişmesin diye."""
        self.figure.delaxes(self.ax_motors)
        self.ax_motors = self.figure.add_subplot(self._motors_gs_cell, sharex=self.ax_voltage)
        self._motors_cax.clear()
        self._motors_cax.axis("off")
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
            color, linestyle = _series_style(i)
            self.ax_motors.plot(
                motor["time_s"], motor["current_a"], color=color, linestyle=linestyle,
                linewidth=2, label=f"Motor {motor['id']}",
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
        # Panel başlığı çizgi grafiğiyle AYNI ("Motor Akımı (A)") kalır; ısı
        # haritasında farklı bir metne ("Motor") değişmesi kafa karıştırıcıydı.
        self._style_axes(self.ax_motors, "Motor Akımı (A)")
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

        self._motors_cax.axis("on")
        self._motor_colorbar = self.figure.colorbar(image, cax=self._motors_cax)
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
