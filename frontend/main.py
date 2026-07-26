"""İHA Güç/Telemetri Log Analiz Aracı - Frontend giriş noktası.

Seçilen log dosyası backend'e (C++) verilir; backend onu parse edip
shared/power_log_schema.md şemasına uygun bir JSON üretir, frontend de bu
JSON'u okuyup grafiği çizer. ArduPilot .bin ve PX4 .ulog, ikisi de batarya ve
motor/ESC verisiyle destekleniyor; format dosya içeriğinden (magic byte)
otomatik algılanıyor.
"""

import csv
import ctypes
import json
import math
import queue
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import customtkinter as ctk
import tkinter as tk
from tkinter import Canvas, filedialog, PhotoImage
from PIL import Image, ImageDraw, ImageTk
from matplotlib import cbook
from matplotlib.backend_bases import MouseButton, _Mode
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

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
APP_VERSION = "0.1.0"  # MVP sürümü; sürüm etiketlendiğinde burası güncellenir

# Tema tercihi ve uyarı eşikleri gibi kalıcı ayarlar aynı klasörde,
# recent_files.json'un yanında tutulur.
SETTINGS_PATH = Path.home() / ".iha_guc_telemetri_analiz" / "settings.json"


def _load_settings() -> dict:
    """Kalıcı ayarları diskten okur; dosya yoksa/bozuksa boş sözlük döner
    (ilk çalıştırma ya da elle silinmiş bir config dosyası için normal bir
    durum, hata sayılmaz — eksik anahtarlar için çağıran taraf varsayılan
    değerleri kullanır)."""
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_settings(settings: dict):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# Koyu ve açık tema renk paletleri (koyu, dataviz rehberinin doğrulanmış
# referans paletinden). Grafik burada kendi renklerini tanımlıyor ki
# CustomTkinter'ın appearance mode'uyla birebir uyumlu olsun; matplotlib'in
# varsayılan beyaz arka planı kullanılmıyor. Giriş ekranının kendi ayrı
# LANDING_* paleti (aşağıda) bilinçli olarak bundan bağımsız kalır — zaten
# analiz ekranının teması ne olursa olsun sabit/"dikkat çekici" olacak
# şekilde tasarlanmıştı.
DARK_PALETTE = {
    "SURFACE": "#1a1a19",
    "TEXT_PRIMARY": "#ffffff",
    "TEXT_SECONDARY": "#c3c2b7",
    "TEXT_MUTED": "#898781",
    "GRIDLINE": "#2c2c2a",
    "AXIS_LINE": "#383835",
    "COLOR_CRITICAL": "#d03b3b",  # durum paleti: kritik/hata (backend hatası)
    "COLOR_WARNING": "#d9a334",   # durum paleti: uyarı (backend'in kural tabanlı yorumları)
    # PWM sapma ısı haritasının ıraksak (diverging) skalası: soğuk uç
    # (ortalamanın altı) → NÖTR GRİ orta nokta (sapma yok) → sıcak uç
    # (ortalamanın üstü). Orta noktanın gri olması şart: sapmasız bölgeler
    # göze çarpmamalı, dikkat sapmanın olduğu yere gitmeli. Durum paletiyle
    # (COLOR_WARNING/CRITICAL) bilerek aynı tonlar seçilmedi — bu bir ölçüm
    # skalası, "uyarı" değil.
    "PWM_DEVIATION_COLORS": ("#5aa9f0", "#2c2c2a", "#eb9b4f"),
}
LIGHT_PALETTE = {
    "SURFACE": "#f4f5f2",
    "TEXT_PRIMARY": "#1c1c1a",
    # İkincil/soluk tonlar bilerek koyu temadaki muadilleriyle AYNI kontrast
    # oranına ayarlandı; ilk değerleri açık zeminde soluk kalıyordu. Ölçüm
    # (WCAG, yüzey / istatistik kutucuğu zemini üzerinde):
    #   TEXT_SECONDARY  8.15 -> 9.83  (koyu temada 9.72)
    #   TEXT_MUTED      4.48 -> 6.06  ve kutucukta 3.74 -> 5.05; eskisi normal
    #                                  metin için AA sınırının (4.5) altındaydı
    #   COLOR_WARNING   4.12 -> 5.58  (uyarı satırları için)
    # Tonlar korundu, sadece açıklık düşürüldü.
    "TEXT_SECONDARY": "#3e3e39",
    "TEXT_MUTED": "#615c54",
    "GRIDLINE": "#e1e1db",
    "AXIS_LINE": "#c6c6bf",
    "COLOR_CRITICAL": "#b5231f",
    "COLOR_WARNING": "#8b5616",
    # Koyu temanın tersi yönde: açık zeminde uçların KOYU, orta noktanın açık
    # gri olması gerekiyor ki aynı "sapma parlar, sapmasızlık geri çekilir"
    # okuması korunsun. (Ardışık HEATMAP_COLORS'ın aksine bu skala temaya göre
    # değişiyor; orta noktanın zeminle uyumlu kalması buna bağlı.)
    "PWM_DEVIATION_COLORS": ("#1f6fb8", "#e1e1db", "#c2701a"),
}

# Tema tercihi yeniden başlatınca uygulanır (canlı geçiş değil — bkz. tema
# butonunun yorumu): renkler burada, uygulama içindeki her widget'tan ÖNCE,
# import zamanında bir kez seçilir.
_settings = _load_settings()
ACTIVE_THEME = "light" if _settings.get("theme") == "light" else "dark"
_ACTIVE_PALETTE = LIGHT_PALETTE if ACTIVE_THEME == "light" else DARK_PALETTE

ctk.set_appearance_mode(ACTIVE_THEME)
ctk.set_default_color_theme("blue")

SURFACE = _ACTIVE_PALETTE["SURFACE"]
TEXT_PRIMARY = _ACTIVE_PALETTE["TEXT_PRIMARY"]
TEXT_SECONDARY = _ACTIVE_PALETTE["TEXT_SECONDARY"]
TEXT_MUTED = _ACTIVE_PALETTE["TEXT_MUTED"]
GRIDLINE = _ACTIVE_PALETTE["GRIDLINE"]
AXIS_LINE = _ACTIVE_PALETTE["AXIS_LINE"]
COLOR_CRITICAL = _ACTIVE_PALETTE["COLOR_CRITICAL"]
COLOR_WARNING = _ACTIVE_PALETTE["COLOR_WARNING"]
PWM_DEVIATION_COLORS = _ACTIVE_PALETTE["PWM_DEVIATION_COLORS"]

# CustomTkinter widget'ları için (AÇIK, KOYU) renk ÇİFTLERİ. Yukarıdaki tekil
# sabitlerle (SURFACE, ...) FARKI ve NEDEN ikisi de gerekli:
#   - Tekil sabitler matplotlib figürü, giriş ekranı ve ham Tk navigasyon
#     toolbar'ı için kullanılır (bunları ctk yönetmez). Tema değişince
#     _on_theme_toggle_click içinde elle güncellenip grafik yeniden çizilir.
#   - Bir CTk widget'ına renk ÇİFTİ verilirse, ctk.set_appearance_mode()
#     çağrısında widget'ı PENCEREYE DOKUNMADAN, yerinde otomatik yeniden
#     renklendirir. Canlı tema geçişinin (pencereyi gizleyip yeniden kurmadan,
#     titremeden) çalışmasının anahtarı bu — analiz ekranındaki her CTk widget
#     bu UI_* çiftleriyle kurulur. Sıra (açık, koyu) olmalı; ctk "Light"i 0,
#     "Dark"ı 1. indeks olarak okur.
UI_SURFACE = (LIGHT_PALETTE["SURFACE"], DARK_PALETTE["SURFACE"])
UI_TEXT_PRIMARY = (LIGHT_PALETTE["TEXT_PRIMARY"], DARK_PALETTE["TEXT_PRIMARY"])
UI_TEXT_SECONDARY = (LIGHT_PALETTE["TEXT_SECONDARY"], DARK_PALETTE["TEXT_SECONDARY"])
UI_TEXT_MUTED = (LIGHT_PALETTE["TEXT_MUTED"], DARK_PALETTE["TEXT_MUTED"])
UI_GRIDLINE = (LIGHT_PALETTE["GRIDLINE"], DARK_PALETTE["GRIDLINE"])
UI_AXIS_LINE = (LIGHT_PALETTE["AXIS_LINE"], DARK_PALETTE["AXIS_LINE"])
UI_COLOR_CRITICAL = (LIGHT_PALETTE["COLOR_CRITICAL"], DARK_PALETTE["COLOR_CRITICAL"])
UI_COLOR_WARNING = (LIGHT_PALETTE["COLOR_WARNING"], DARK_PALETTE["COLOR_WARNING"])

# Backend'deki computeWarnings'in kendi varsayılanlarıyla (main.cpp,
# DEFAULT_VOLTAGE_SAG_WARNING_THRESHOLD vb.) BİREBİR AYNI kalmalı — ayarlar
# penceresinde kullanıcı bir eşiği hiç değiştirmediyse burası kullanılır.
DEFAULT_VOLTAGE_SAG_THRESHOLD = 0.15
DEFAULT_CURRENT_IMBALANCE_THRESHOLD = 0.20
DEFAULT_NEGATIVE_CURRENT_THRESHOLD = -0.1


# Eşikler araç tipi başına ayrı ayrı saklanabilir; bu sözlüğün anahtarı
# meta.vehicle_type ("rover", "multirotor", ...). Bir tip için kayıt yoksa
# genel (tipten bağımsız) eşikler geçerli olur.
#
# Neden tipe göre ayrı VARSAYILAN yok: örnek loglar ölçüldüğünde voltaj
# düşümü tüm araç tiplerinde %0.5-6.2 aralığında çıktı (eşik %15) ve tipler
# arasında anlamlı bir ayrışma görülmedi; dengesizlik ise her tipte sadece
# 1-2 logda ölçülebiliyor. Tipe özel sayılar uydurmak yerine yapı kurulup
# varsayılanlar ortak bırakıldı — kullanıcı kendi filosuna göre kalibre eder.
SETTINGS_VEHICLE_THRESHOLDS_KEY = "thresholds_by_vehicle"
THRESHOLD_KEYS = (
    "voltage_sag_threshold",
    "current_imbalance_threshold",
    "negative_current_threshold",
)


def _get_general_thresholds() -> dict:
    """Tipten bağımsız (genel) eşikler; hiç ayarlanmamışsa varsayılanlar."""
    settings = _load_settings()
    return {
        "voltage_sag_threshold": settings.get("voltage_sag_threshold", DEFAULT_VOLTAGE_SAG_THRESHOLD),
        "current_imbalance_threshold": settings.get(
            "current_imbalance_threshold", DEFAULT_CURRENT_IMBALANCE_THRESHOLD
        ),
        "negative_current_threshold": settings.get(
            "negative_current_threshold", DEFAULT_NEGATIVE_CURRENT_THRESHOLD
        ),
    }


def _get_vehicle_threshold_overrides() -> dict:
    """Araç tipi -> eşik sözlüğü. Sadece kullanıcının o tip için AYRICA
    kaydettiği eşikler burada bulunur; hepsi backend'e geçirilir ve backend
    ayrıştırdığı araç tipine uyanı seçer (bkz. selectThresholds, main.cpp).
    Bozuk/eksik bir kayıt sessizce atlanır — ayar dosyası elle düzenlenmiş
    olabilir."""
    raw = _load_settings().get(SETTINGS_VEHICLE_THRESHOLDS_KEY, {})
    if not isinstance(raw, dict):
        return {}

    overrides = {}
    for vehicle_type, values in raw.items():
        if not isinstance(values, dict):
            continue
        if all(isinstance(values.get(key), (int, float)) for key in THRESHOLD_KEYS):
            overrides[vehicle_type] = {key: float(values[key]) for key in THRESHOLD_KEYS}
    return overrides


def _get_warning_thresholds(vehicle_type: str = None) -> dict:
    """Verilen araç tipi için geçerli eşikler; o tipe özel kayıt yoksa genel
    eşikler. Hem backend'i --flag'lerle çağırırken hem de grafik üzerindeki
    eşik çizgilerini çizerken tek bir yerden okunur."""
    if vehicle_type:
        override = _get_vehicle_threshold_overrides().get(vehicle_type)
        if override:
            return override
    return _get_general_thresholds()

# Giriş (splash) ekranı için ayrı, "dikkat çekici" bir palet — sadece landing
# ekranında kullanılır, analiz ekranının sakin koyu teması (SURFACE vb.)
# bundan etkilenmez. Ana mavi (SERIES_COLORS[0]) ile aynı aile, ama daha
# doygun/parlak.
LANDING_BG = "#07131f"              # koyu lacivert taban
LANDING_ACCENT = "#3987e5"          # mevcut ana mavi ile tutarlılık
LANDING_ACCENT_BRIGHT = "#5fd4ff"   # parlak camgöbeği, ikon/vurgu için
LANDING_TEXT = "#eaf4ff"
LANDING_CARD = "#152d45"            # geçmiş dosya kartlarının arka planı — sidebar
                                     # zemininden (#0c1e30) belirgin ayrışsın diye
                                     # bilerek epey açık, artık gerçek bir "kutu" gibi okunuyor
LANDING_CARD_BORDER = "#24445f"      # kartın ince kenarlığı, dikdörtgen sınırı netleştirir
LANDING_BADGE_ULOG = "#1c3f66"       # .ulog/.ulg format rozeti — LANDING_ACCENT'tan (hover rengi)
                                     # BİLEREK farklı, daha koyu lacivert: aksi halde imleç kartın
                                     # üzerine gelince rozet arka planla aynı renge karışıp kayboluyordu
LANDING_BADGE_BIN = "#c9822f"        # .bin format rozeti (ULG'den ayırt edilsin diye sıcak ton)
LANDING_TEXT_SECONDARY = "#a9c3de"   # geçmiş kartlarındaki "az önce · 1544s" meta yazısı — TEXT_SECONDARY
                                     # KASITLI olarak kullanılmıyor: o tema değişince (koyu/açık) değişen bir
                                     # sabit, LANDING_CARD'ın her zaman koyu lacivert kalan zeminiyle açık
                                     # temada neredeyse hiç kontrastı kalmazdı (koyu gri/koyu lacivert)

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

# Üst istatistik satırı VE PDF raporunun özet sayfası aynı kutucukları
# gösterir; tek bir yerden tanımlanır ki biri güncellenip diğeri unutulmasın.
STAT_TILE_DEFINITIONS = [
    ("duration", "Süre"),
    ("samples", "Örnek Sayısı"),
    ("voltage_range", "Voltaj Aralığı"),
    ("current_range", "Akım Aralığı"),
    ("energy_wh", "Enerji Tüketimi"),
    ("peak_power_w", "Tepe Güç"),
    ("resistance_est", "Tahmini İç Direnç"),
    ("capacity_used", "Tüketilen Kapasite"),
    ("remaining_time", "Tahmini Kalan Süre"),
]


# Akım ölçümü olmayan panellerde, sessiz/boş bir grafik yerine gösterilen
# açıklamalar (sıcaklık panelindeki "Bu logda sıcaklık verisi yok." ile aynı
# desen). Bir rover/sabit kanat logunda akım sensörü çoğu zaman hiç bağlı
# değildir; bunu sıfır çizgisi olarak çizmek "hiç akım çekilmemiş" gibi
# yanlış bir izlenim veriyordu.
NO_BATTERY_CURRENT_MESSAGE = "Bu logda akım sensörü verisi yok."
NO_MOTOR_TOPIC_MESSAGE = "Bu logda motor (ESC) akım verisi yok."
NO_MOTOR_CURRENT_MESSAGE = "ESC telemetrisi var ama akım bildirilmiyor."
NO_PWM_DATA_MESSAGE = "Bu logda PWM çıkış verisi yok."

# Alt panelin görünüm modu ile seçicideki etiketi arasındaki eşleme. İki mod
# arasında geçiş yapan eski if/else, üçüncü mod (PWM) eklenince okunaksız
# olacaktı; tek bir sözlük çifti hem seçiciyi hem kayıtlı modu besliyor.
MOTOR_VIEW_MODES = {
    "Çizgi Grafiği": "line",
    "Isı Haritası": "heatmap",
    "PWM Çıkışı": "pwm",
    "PWM Sapma": "pwm_deviation",
}
MOTOR_VIEW_LABELS = {mode: label for label, mode in MOTOR_VIEW_MODES.items()}

# Dışa aktarma açılır menüsü: etiket -> App üzerindeki işleyicinin adı.
# Menünün kendi başlığı seçim yapılsa da değişmez (bkz. _on_export_selected).
# Görünüm seçicileri (CTkSegmentedButton) hiç renk verilmeden kuruluyordu,
# yani uygulamanın paletini değil CustomTkinter'ın kendi varsayılanını
# kullanıyorlardı: açık temada seçili OLMAYAN segmentlerin metni zeminle
# karışacak kadar soluk kalıyordu. Seçili segment marka mavisinde kalıyor
# (orada metin beyaz), seçili olmayanlar ise yüzeyle aynı zemine ve normal
# metin rengine çekiliyor.
def _segmented_button_style() -> dict:
    """Üç görünüm seçicisinin ortak renkleri. Fonksiyon olarak veriliyor
    çünkü UI_* renk çiftleri modül yüklenirken tanımlanıyor ve tema canlı
    değiştiğinde ctk bunları kendisi çeviriyor."""
    return {
        "text_color": UI_TEXT_PRIMARY,
        "fg_color": UI_GRIDLINE,
        "unselected_color": UI_GRIDLINE,
        "unselected_hover_color": UI_AXIS_LINE,
    }


EXPORT_MENU_LABEL = "Dışa Aktar"
EXPORT_MENU_ACTIONS = {
    "Grafik (PNG)": "_on_export_png_click",
    "Rapor (PDF)": "_on_export_pdf_click",
    "Ham veri (CSV)": "_on_export_csv_click",
}

# meta.vehicle_type (backend) -> arayüzde gösterilecek Türkçe etiket.
VEHICLE_TYPE_LABELS = {
    "multirotor": "Multirotor",
    "fixed_wing": "Sabit Kanat",
    "rover": "Rover",
    "vtol": "VTOL",
    "airship": "Zeplin",
    "submarine": "Denizaltı",
}


def _has_current_data(series: dict) -> bool:
    """Bir batarya/motorun akım ölçümünün gerçek olup olmadığı — backend'in
    has_current_data bayrağı (bkz. shared/power_log_schema.md). Akım sensörü
    bağlı değilken ArduPilot/PX4 alanı boş bırakmaz, her örneğe tam 0.0 yazar;
    bayrağı üreten tespit backend'de. Bayrağı taşımayan (eski sürümden kalma)
    bir JSON gelirse veri var sayılır, yani önceki davranış korunur."""
    return series.get("has_current_data", True)


def _with_current_data(series_list: list) -> list:
    """Batarya/motor listesinden sadece gerçek akım ölçümü olanları süzer."""
    return [series for series in series_list if _has_current_data(series)]


def _motor_empty_message(motors: list) -> str:
    """Motor panelinde ölçüm olmamasının iki ayrı sebebini ayırt eder: ya
    esc_status/ESC konusu logda hiç yok (rover ve sabit kanat loglarının
    çoğu böyle), ya da ESC'ler loglanmış ama akım alanını doldurmuyor
    (akım ölçümü desteklemeyen ESC'ler). Kullanıcı için bu fark önemli:
    ilki 'bu araçta ESC telemetrisi kurulu değil', ikincisi 'kurulu ama
    akım okumuyor' demek."""
    return NO_MOTOR_TOPIC_MESSAGE if not motors else NO_MOTOR_CURRENT_MESSAGE


def _flight_duration_seconds(batteries: list):
    """Kaydın ilk ve son örneği arasında geçen süre (üst istatistik satırı ve
    Geçmiş Dosyalar listesi aynı hesaplamayı paylaşır). Veri yoksa None döner.

    İlk zaman damgasının ÇIKARILMASI şart: PX4 logları uçuş kontrolcüsünün
    açılışından beri geçen süreyi damgalıyor, sıfırdan başlamıyor. Bunu
    yapmayan eski hali `px4_fixed_wing_flight.ulg` için 4104.7 s gösteriyordu
    — gerçek kayıt 90.8 saniye. Aynı hata `_update_capacity_stats`'taki kalan
    süre tahminine de taşınıyordu (oradaki doğrusal ekstrapolasyon süreyle
    çarpıldığı için sonuç ~45 kat şişiyordu)."""
    spans = [(battery["time_s"][0], battery["time_s"][-1])
             for battery in batteries if battery["time_s"]]
    if not spans:
        return None
    return max(end for _, end in spans) - min(start for start, _ in spans)


def _battery_energy_wh(batteries: list) -> float:
    """Her bataryanın voltaj*akım eğrisini zamana göre integre ederek (Ws)
    toplam tüketilen enerjiyi Wh cinsinden döner."""
    total_ws = 0.0
    for battery in batteries:
        time_s = battery["time_s"]
        if len(time_s) < 2:
            continue
        power_w = [v * c for v, c in zip(battery["voltage_v"], battery["current_a"])]
        total_ws += np.trapezoid(power_w, time_s)
    return total_ws / 3600.0


def _battery_peak_power_w(batteries: list) -> float:
    """Tüm bataryalar arasında noktasal voltaj*akım çarpımının en yükseği."""
    peaks = [
        max((v * c for v, c in zip(battery["voltage_v"], battery["current_a"])), default=0.0)
        for battery in batteries
    ]
    return max(peaks, default=0.0)


def _battery_internal_resistance_estimate(batteries: list):
    """Voltaj~akım arasındaki doğrusal regresyon eğiminden kaba bir iç direnç
    tahmini (mΩ) çıkarır — bir arıza eşiği DEĞİL, sadece uçuştan uçuşa
    karşılaştırılabilecek ham bir sayı (artış trendi bataryanın yaşlandığına
    işaret edebilir). Akım varyasyonu çok düşükse (regresyon anlamsızlaşır)
    o batarya atlanır. Birden fazla batarya varsa en yükseği (en zayıf
    görüneni) döner. Hiçbiri için hesaplanamazsa None."""
    best = None  # (battery_id, resistance_mohm)
    for battery in batteries:
        current_a = battery["current_a"]
        voltage_v = battery["voltage_v"]
        if len(current_a) < 2 or np.ptp(current_a) < 0.5:
            continue
        slope, _ = np.polyfit(current_a, voltage_v, 1)
        resistance_mohm = abs(slope) * 1000.0
        if best is None or resistance_mohm > best[1]:
            best = (battery["id"], resistance_mohm)
    return best


# Karşılaştırma tablosunun satırları: (anahtar, başlık, birim biçimi).
# Üst istatistik satırından (STAT_TILE_DEFINITIONS) ayrı tutuluyor çünkü
# karşılaştırmada anlamlı olan alt küme farklı: "Örnek Sayısı" iki uçuşu
# kıyaslarken bilgi vermez, "Araç Tipi" ve "Uyarı Sayısı" ise burada gerekli.
COMPARISON_ROWS = [
    ("vehicle", "Araç Tipi"),
    ("duration_s", "Süre"),
    ("voltage_range", "Voltaj Aralığı"),
    ("current_range", "Akım Aralığı"),
    ("energy_wh", "Enerji Tüketimi"),
    ("peak_power_w", "Tepe Güç"),
    ("capacity_used_mah", "Tüketilen Kapasite"),
    ("resistance_mohm", "İç Direnç (tahmini)"),
    ("voltage_sag_pct", "Voltaj Düşümü"),
    ("warning_count", "Uyarı Sayısı"),
]


def _flight_summary_metrics(data: dict) -> dict:
    """Bir uçuşun, başka uçuşlarla yan yana konabilecek özet metrikleri.

    Hesaplamalar üst istatistik satırıyla AYNI yardımcıları kullanır
    (_battery_energy_wh vb.), böylece iki yerde farklı sayı çıkmaz. Akıma
    dayanan metrikler yalnızca gerçekten ölçümü olan bataryalardan
    hesaplanır (bkz. _has_current_data); ölçüm yoksa değer None olur ve
    tabloda "—" görünür — sıfır göstermek "hiç akım çekilmemiş" gibi
    yanlış okunurdu.

    Değerler ham (sayı) döner, biçimlendirme çağırana ait: testler sayıyı
    doğrulayabilsin, tablo da kendi birimini seçebilsin."""
    batteries = data.get("batteries", [])
    # Hiç örneği olmayan bir batarya (bozuk/yarım JSON) metriklere girmemeli:
    # aksi halde enerji ve tepe güç "0.0" olarak hesaplanıp tabloda "0.00 Wh"
    # görünüyor, yani "ölçüm yok" yine "ölçüm sıfır" gibi okunuyordu.
    measured = [battery for battery in _with_current_data(batteries) if battery["time_s"]]
    all_voltage = [v for battery in batteries for v in battery["voltage_v"]]
    all_current = [c for battery in measured for c in battery["current_a"]]

    resistance = _battery_internal_resistance_estimate(measured)
    capacities = [
        b["capacity_used_mah"] for b in measured if b.get("capacity_used_mah") is not None
    ]

    # Voltaj düşümü, backend'in uyarı kuralıyla aynı tanım: ilk örneğe göre
    # en düşük voltaja inişin yüzdesi (bataryalar arasında en kötüsü).
    voltage_sag_pct = None
    for battery in batteries:
        voltages = battery["voltage_v"]
        if not voltages or voltages[0] <= 0:
            continue
        sag = (voltages[0] - min(voltages)) / voltages[0] * 100
        voltage_sag_pct = sag if voltage_sag_pct is None else max(voltage_sag_pct, sag)

    return {
        "vehicle": VEHICLE_TYPE_LABELS.get(data.get("meta", {}).get("vehicle_type", "")),
        "duration_s": _flight_duration_seconds(batteries),
        "voltage_min": min(all_voltage) if all_voltage else None,
        "voltage_max": max(all_voltage) if all_voltage else None,
        "current_min": min(all_current) if all_current else None,
        "current_max": max(all_current) if all_current else None,
        "energy_wh": _battery_energy_wh(measured) if measured else None,
        "peak_power_w": _battery_peak_power_w(measured) if measured else None,
        "capacity_used_mah": sum(capacities) if capacities else None,
        "resistance_mohm": resistance[1] if resistance else None,
        "voltage_sag_pct": voltage_sag_pct,
        "warning_count": len(data.get("warnings", [])),
    }


def _format_comparison_value(key: str, metrics: dict) -> str:
    """Bir özet metriğini tabloda gösterilecek metne çevirir; ölçülememiş
    değerler "—" olur (bkz. _flight_summary_metrics)."""
    if key == "voltage_range":
        low, high = metrics["voltage_min"], metrics["voltage_max"]
        return f"{low:.2f}–{high:.2f} V" if low is not None else "—"
    if key == "current_range":
        low, high = metrics["current_min"], metrics["current_max"]
        return f"{low:.1f}–{high:.1f} A" if low is not None else "—"

    value = metrics.get(key)
    if value is None:
        return "—"
    if key == "vehicle":
        return value
    if key == "duration_s":
        return f"{value:.1f} s"
    if key == "energy_wh":
        return f"{value:.2f} Wh"
    if key == "peak_power_w":
        return f"{value:.0f} W"
    if key == "capacity_used_mah":
        return f"{value:.0f} mAh"
    if key == "resistance_mohm":
        return f"{value:.0f} mΩ"
    if key == "voltage_sag_pct":
        return f"%{value:.1f}"
    return str(value)


def _draw_voltage_sag_line(ax, first_voltage: float, threshold: float, color: str):
    """Backend'in voltaj düşümü kuralıyla (ilk örneğe göre %eşik düşüş) aynı
    hesapla, o bataryanın kendi rengiyle kesikli bir eşik çizgisi çizer —
    sadece çizgi modunda anlamlı, ısı haritasında çağrılmaz.

    Label kasıtlı olarak "_" ile başlıyor: hem matplotlib'in legend()'ı
    otomatik dışlasın diye (bkz. az aşağıdaki batarya/motor çizgilerinin
    "Batarya N"/"Motor N" etiketleri), hem de get_lines()'ı gerçek veri
    sayısıyla karşılaştıran testler bu çizgiyi ayırt edip filtreleyebilsin diye."""
    if first_voltage <= 0:
        return
    ax.axhline(
        first_voltage * (1 - threshold), color=color, linestyle="--", linewidth=1, alpha=0.5,
        label="_voltage_sag_threshold",
    )


def _draw_imbalance_band(ax, series_means: list, threshold: float):
    """2+ seri (batarya/motor) varsa, backend'in dengesizlik kuralıyla aynı
    mantıkla (genel ortalama ± eşik) hafif bir bant çizer — dengesizlik
    uyarısının grafikteki görsel karşılığı. Tek seri varsa (kural zaten
    değerlendirilmiyor) hiçbir şey çizmez."""
    if len(series_means) < 2:
        return
    overall_mean = sum(series_means) / len(series_means)
    if overall_mean <= 0:
        return
    ax.axhspan(overall_mean * (1 - threshold), overall_mean * (1 + threshold), color=COLOR_WARNING, alpha=0.08, zorder=0)


def _format_relative_time(when: datetime) -> str:
    """'X dk önce' / '2 saat önce' / 'dün' / '3 gün önce' gibi kısa, göreli
    bir zaman etiketi üretir (Geçmiş Dosyalar listesinde mutlak tarih yerine)."""
    delta = datetime.now() - when
    seconds = delta.total_seconds()
    if seconds < 60:
        return "az önce"
    if seconds < 3600:
        return f"{int(seconds // 60)} dk önce"
    if seconds < 86400:
        return f"{int(seconds // 3600)} saat önce"
    days = int(seconds // 86400)
    if days == 1:
        return "dün"
    if days < 30:
        return f"{days} gün önce"
    return when.strftime("%Y-%m-%d")


def _format_duration_short(seconds: float) -> str:
    """Geçmiş Dosyalar listesindeki kompakt süre gösterimi, ör. '1543s'."""
    return f"{int(round(seconds))}s"


def _truncate_to_width(text: str, max_width_px: float, font) -> str:
    """Metni GERÇEK piksel genişliğine göre kırpar (karakter sayısına göre
    değil) — sabit karakter sayısıyla kırpma, harf genişliği değişken
    olduğu için bazı uzun dosya adlarını tutarsızca (bazen gereğinden erken,
    bazen taşacak kadar geç) kesiyordu. İkili arama ile sığan en uzun
    önek + '...' bulunur."""
    if max_width_px <= 0 or font.measure(text) <= max_width_px:
        return text
    ellipsis_width = font.measure("...")
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid]) + ellipsis_width <= max_width_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "..."


def _add_toolbar_tooltip(widget, text: str):
    """Bir araç çubuğu butonuna, fare üzerine gelince çıkan küçük bir ipucu
    balonu bağlar.

    matplotlib'in kendi yardımcısı private bir modülde ve sürümden sürüme yer
    değiştiriyor (3.11'de `_backend_tk.add_tooltip`, daha eskilerde
    `ToolTip.createToolTip`). İpucu, ikonlu bir butonun ne işe yaradığını
    anlatan tek şey olduğu için o API'ye bağlı kalmak yerine burada kendi
    küçük sürümümüz kuruluyor; ek fayda olarak balon uygulamanın koyu/açık
    temasına uyuyor (renkler her gösterimde canlı okunuyor)."""
    state = {"window": None}

    def show(_event=None):
        if state["window"] is not None:
            return
        window = tk.Toplevel(widget)
        window.wm_overrideredirect(True)  # çerçevesiz, başlıksız balon
        window.wm_geometry(
            f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + widget.winfo_height() + 4}"
        )
        tk.Label(
            window, text=text, background=GRIDLINE, foreground=TEXT_PRIMARY,
            relief="solid", borderwidth=1, padx=6, pady=3,
        ).pack()
        state["window"] = window

    def hide(_event=None):
        if state["window"] is not None:
            state["window"].destroy()
            state["window"] = None

    # add="+" : matplotlib'in kendi bağladığı olay işleyicileri ezilmesin.
    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<ButtonPress>", hide, add="+")


class _PanPreviewToolbar(NavigationToolbar2Tk):
    """Standart NavigationToolbar2Tk'dan farkları:

    1. Pan (taşı) aracı açıkken yapılan sürüklemeler (sol ya da sağ tuşla,
       her yöne) KALICIDIR — fareyi bırakmak eski görünüme dönmez, istediğin
       kadar sürükleyip grafiği inceleyebilirsin. Kaydedilen "Pan'a girmeden
       önceki görünüm"e dönüş sadece başka bir araca (Geri/İleri/Yakınlaştır/
       Kaydet) geçildiğinde olur (bkz. `pan`/`_restore_pan_view_if_pending`).
    2. İkonlar, matplotlib'in varsayılan siyah/gri boyamasının yerine,
       uygulamanın koyu temasına uygun mavi tonlarda çiziliyor (bkz.
       `_set_image_for_button`).
    3. "Subplots" (kenar boşluğu/aralık ayarlama) butonu kaldırıldı: bu
       uygulama grafik düzenini bilerek SABİT marjlarla çiziyor (geçmişteki
       "grafik kayması" hatasından ders alınarak `tight_layout` kasıtlı
       kullanılmıyor); o araçla yapılacak bir değişiklik, eksenler her
       yeniden çizildiğinde (ısı haritası geçişi, yeni dosya) sessizce
       sıfırlanıyordu — kafa karıştırıcı ve bu uygulama için gereksiz bir
       matplotlib güç-kullanıcı özelliği.
    4. "Ana Sayfa" (ev) butonu kaldırıldı; görevini Pan'ın yanındaki
       "Sıfırla" butonu devraldı (bkz. `_add_reset_button`/`reset_view`).
    """

    # "Subplots" (yukarıdaki 3. madde) ve "Home" çıkarılıyor. Home'un işini
    # artık Pan'ın yanındaki "Sıfırla" butonu yapıyor (bkz. _add_reset_button):
    # ev simgesi, uygulamada giriş ekranına dönen "← Ana Sayfa" butonu da
    # olduğu için oraya götürüyormuş gibi duruyordu ve iki buton aynı işi
    # yapıyordu.
    toolitems = tuple(
        item for item in NavigationToolbar2Tk.toolitems
        if item[0] not in ("Subplots", "Home")
    )

    def __init__(self, *args, **kwargs):
        self._tinted_icons = {}  # PhotoImage referansları GC'ye kaybolmasın diye canlı tutulur
        super().__init__(*args, **kwargs)
        self._pre_pan_view = None
        self._add_reset_button()

    def _add_reset_button(self):
        """Pan (taşı) ile Yakınlaştır arasına metin tabanlı bir "Sıfırla"
        butonu ekler.

        Ev ikonu da aynı işi yapıyor ama ne yaptığı belirsiz kalıyordu:
        uygulamada bir de giriş ekranına dönen "← Ana Sayfa" butonu olduğu
        için ev simgesi oraya götürüyormuş gibi duruyor. Elle yakınlaştırma/
        kaydırma yapılan butonların yanında duran, adı açıkça yazan ikinci bir
        giriş bu tereddüdü ortadan kaldırıyor.

        Buton `toolitems`'a EKLENMİYOR: matplotlib oradaki her kaydın ikon
        dosyasını `images/{image_file}.png` diye çözmeye çalışıyor, yani
        matplotlib'in hazır setinde olmayan bir simge oradan tanımlanamıyor.
        Bunun yerine buton normal şekilde kurulup Tk'nin `pack(before=...)`
        özelliğiyle doğru konuma alınıyor. `_buttons` sözlüğüne de yazılıyor ki
        tema boyaması (`_style_nav_toolbar`) onu da kapsasın.

        Görünüm diğer araç butonlarıyla birebir aynı: metin yok, aynı ölçüde
        bir ikon var ve adı fare üzerine gelince tooltip olarak çıkıyor."""
        button = tk.Button(
            master=self, command=self.reset_view,
            relief="flat", overrelief="groove", borderwidth=1,
        )
        # Bu işaret, ikon boyama mantığının (bkz. _set_image_for_button) bu
        # butonda dosyadan değil çizilerek üretilen simgeyi kullanmasını sağlar.
        button._is_reset_button = True
        button._image_file = None
        self._set_image_for_button(button)

        _add_toolbar_tooltip(button, "Sıfırla — görünümü ilk haline döndür")

        zoom_button = self._buttons.get("Zoom")
        if zoom_button is not None:
            button.pack(side=tk.LEFT, before=zoom_button)
        else:
            button.pack(side=tk.LEFT)
        self._buttons["Reset"] = button

    def _Button(self, text, image_file, toggle, command):
        """matplotlib'in orijinali (NavigationToolbar2Tk._Button) ile birebir
        aynı, TEK fark: ikon atarken `NavigationToolbar2Tk._set_image_for_button`
        yerine `self._set_image_for_button` (polymorphic) çağrılıyor ki alttaki
        override devreye girsin. matplotlib kendi kaynağında bilerek sabit
        sınıf üzerinden çağırıyor (bkz. orijinal kaynaktaki yorum); bu yüzden
        alt sınıfta override etmek TEK BAŞINA yeterli olmuyor, bu metodun
        kendisinin de (küçük bir farkla) kopyalanması gerekiyor. Bu, ilk
        atanan ikonun DOĞRUDAN mavi olmasını sağlıyor — Geri/İleri gibi
        başlangıçta disabled olan butonlarda önce siyah ikonla oluşturulup
        sonradan maviye 'değiştirilen' bir buton, Tk'de bulanık/karışık bir
        görüntüye dönüşüyordu (gözlemlenen bir Tk tuhaflığı); ikon hiç
        değişmediği için bu sorun hiç oluşmuyor."""
        if not toggle:
            b = tk.Button(
                master=self, text=text, command=command,
                relief="flat", overrelief="groove", borderwidth=1,
            )
        else:
            var = tk.IntVar(master=self)
            b = tk.Checkbutton(
                master=self, text=text, command=command, indicatoron=False,
                variable=var, offrelief="flat", overrelief="groove", borderwidth=1,
            )
            b.var = var
        b._image_file = image_file
        if image_file is not None:
            self._set_image_for_button(b)
        else:
            b.configure(font=self._label_font)
        b.pack(side=tk.LEFT)
        return b

    def _set_image_for_button(self, button):
        """matplotlib'in varsayılan ikon boyama mantığının (arka plana göre
        siyah ya da beyaz) yerine geçer: ikonu doğrudan uygulamanın ana
        mavisiyle, büyük kaynak PNG'den (netlik için) üretir."""
        if getattr(button, "_is_reset_button", False):
            # "Sıfırla" ikonu matplotlib'in setinde yok; dosya yerine çizilir
            # (bkz. _make_reset_icon). Diğer butonlarla aynı ölçüde ayarlanıyor.
            icon = self._make_reset_icon(size=26)
            self._tinted_icons["__reset__"] = icon
            button.configure(image=icon, height="20p", width="20p")
            return

        image_file = getattr(button, "_image_file", None)
        if image_file is None:
            return
        icon = self._load_tinted_icon(image_file, size=26)
        if icon is None:
            super()._set_image_for_button(button)
            return
        self._tinted_icons[image_file] = icon
        image_kwargs = {"image": icon}
        if isinstance(button, tk.Checkbutton):
            image_kwargs["selectimage"] = icon
        button.configure(**image_kwargs, height="20p", width="20p")

    def set_history_buttons(self):
        """matplotlib'in orijinali Geri/İleri butonlarını `state=disabled`
        yaparak pasifleştiriyor; ama Windows'ta temalı (visual styles) Tk
        butonlarında `-state disabled`, verdiğimiz ikonu YOK SAYIP yerine
        işletim sisteminin kendi 'devre dışı' desenini (gri çizgili/damalı bir
        doku) çiziyor — ikon tamamen görünmez oluyordu (gözlemlenen bir Tk/
        Windows tuhaflığı). Bunun yerine `state` HİÇ değiştirilmiyor (buton
        hep tıklanabilir görünüyor); gidilecek yer yoksa sadece ikon soluk bir
        tona çevriliyor. Fonksiyonel olarak zararsız: matplotlib'in kendi
        `_Stack.back()`/`forward()`'ı zaten sınır kontrolü yapıyor, aralıq
        dışında bir tıklama sessizce hiçbir şey yapmıyor."""
        can_back = self._nav_stack._pos > 0
        can_forward = self._nav_stack._pos < len(self._nav_stack) - 1
        for name, can_use in (("Back", can_back), ("Forward", can_forward)):
            button = self._buttons.get(name)
            if button is None:
                continue
            image_file = getattr(button, "_image_file", None)
            color = None if can_use else TEXT_MUTED
            icon = self._load_tinted_icon(image_file, size=26, color=color) if image_file else None
            if icon is None:
                continue
            self._tinted_icons[(image_file, color)] = icon
            image_kwargs = {"image": icon}
            if isinstance(button, tk.Checkbutton):
                image_kwargs["selectimage"] = icon
            button.configure(**image_kwargs)

    @staticmethod
    def _make_reset_icon(size: int, color: str = None):
        """"Sıfırla" butonunun dairesel ok simgesini ÇİZEREK üretir.

        matplotlib'in hazır ikon setinde (home/back/forward/move/zoom/filesave)
        "yeniden başlat" anlamına gelen bir simge yok. Diske yeni bir PNG
        eklemek yerine çizmenin iki faydası var: PyInstaller paketine ek bir
        varlık girmiyor (bkz. scripts/build_release.ps1) ve simge her seferinde
        o anki tema rengiyle üretildiği için canlı tema geçişinde diğer
        ikonlarla aynı tonu tutuyor.

        Büyük (96px) çizilip küçültülmesi, `_load_tinted_icon`'daki `_large`
        kaynak tercihiyle aynı sebeple: eğri kenarların yumuşak görünmesi."""
        rgb = tuple(int((color or "#3987E5").lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        source = 96
        image = Image.new("RGBA", (source, source), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        margin, thickness = 18, 10
        center = source / 2
        radius = center - margin
        # Yay tam çember değil: kalan boşluk ok başına ayrılıyor.
        # (PIL'de 0° saat 3 yönüdür ve açılar saat yönünde artar.)
        end_deg = 310.0
        draw.arc(
            [margin, margin, source - margin, source - margin],
            start=0.0, end=end_deg, fill=rgb + (255,), width=thickness,
        )

        # Ok başı, yayın bitiş ucunda duran bir üçgen: tabanı yayı dik kesiyor
        # (biri dışa biri içe taşan iki köşe), ucu ise yayın gittiği yönde
        # (teğet) uzanıyor. Taban yarı genişliğinin yay kalınlığından belirgin
        # büyük olması şart — eşit olduğunda üçgen yayla kaynaşıp ok değil düz
        # kesik bir uç gibi görünüyordu.
        end_rad = math.radians(end_deg)
        cos_end, sin_end = math.cos(end_rad), math.sin(end_rad)
        tangent_x, tangent_y = -sin_end, cos_end
        base_x, base_y = center + radius * cos_end, center + radius * sin_end
        half_width, tip_length = 16.0, 28.0
        draw.polygon(
            [
                (base_x + half_width * cos_end, base_y + half_width * sin_end),
                (base_x - half_width * cos_end, base_y - half_width * sin_end),
                (base_x + tip_length * tangent_x, base_y + tip_length * tangent_y),
            ],
            fill=rgb + (255,),
        )

        return ImageTk.PhotoImage(image.resize((size, size), Image.LANCZOS))

    @staticmethod
    def _load_tinted_icon(image_file: str, size: int, color: str = None):
        """matplotlib'in yüksek çözünürlüklü (_large, 48x48) ikon PNG'sini
        yükleyip siyah piksellerini verilen renkle (varsayılan: uygulamanın
        ana mavisi) değiştirir; küçük kaynak yerine büyük kaynaktan ölçeklemek
        ok gibi ince detaylı ikonlarda net bir görünüm sağlar.

        `image_file`, buraya matplotlib'in `_init_toolbar`'ı tarafından ZATEN
        tam bir dosya yoluna çözülmüş halde gelir (ör. ".../mpl-data/images/
        home.png"), kısa bir isim ("home") değil — bu yüzden büyük kaynağı
        bulmak için sadece dosya adının gövdesi (stem) kullanılıyor. Bu
        detayın gözden kaçması, ikonların hep siyah/varsayılan görünüp asla
        mavi boyanmamasının asıl sebebiydi (yol bulunamayınca sessizce None
        dönüp `super()._set_image_for_button` devreye giriyordu)."""
        try:
            stem = Path(image_file).stem
            path_large = cbook._get_data_path("images", f"{stem}_large.png")
            source_path = path_large if path_large.exists() else Path(image_file)
            rgb = tuple(int((color or "#3987E5").lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            with Image.open(source_path) as im:
                im = im.convert("RGBA")
                data = np.asarray(im).copy()
                black_mask = (data[..., :3] == 0).all(axis=-1)
                data[black_mask, :3] = rgb
                tinted = Image.fromarray(data).resize((size, size), Image.LANCZOS)
                return ImageTk.PhotoImage(tinted)
        except (OSError, ValueError):
            return None

    def _snapshot_view(self):
        return [
            (ax, ax.get_xlim(), ax.get_ylim())
            for ax in self.canvas.figure.get_axes() if ax.get_navigate()
        ]

    def _restore_pan_view_if_pending(self):
        if self._pre_pan_view is None:
            return
        for ax, xlim, ylim in self._pre_pan_view:
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)
        self._pre_pan_view = None
        self.canvas.draw_idle()

    def pan(self, *args):
        """Pan aracı KAPALIYKEN açılırken (başka bir araçtan ya da hiçbir
        araç seçili değilken geçilirken) o anki görünüm bir kere kaydedilir.
        Bu oturum içinde kaç kez sürüklenirse sürüklensin (sol ya da sağ
        tuşla, her yöne) sonuç KALICI kalır — artık her fare bırakışında eski
        hale dönmüyor. Kaydedilen görünüme dönüş, ancak başka bir araca
        (Geri/İleri/Yakınlaştır/Kaydet) geçildiğinde gerçekleşiyor (bkz. o
        metodların başındaki `_restore_pan_view_if_pending` çağrıları);
        Pan'ı aynı simgeye tekrar basarak kapatmak görünümü DEĞİŞTİRMEZ.

        "Sıfırla" bir istisna: aracı kapatmadan görünümü başa alır ve
        kaydedilen noktayı sıfırlanmış görünümle değiştirir (bkz.
        `reset_view`)."""
        if self.mode != _Mode.PAN:
            self._pre_pan_view = self._snapshot_view()
        super().pan(*args)

    def drag_pan(self, event):
        """Sol tıkla sürüklerken grafiğin sol kenarı, verinin gerçek başlangıç
        zamanının gerisine hiç geçmiyor — mantıken uçuş başlamadan önceki boş
        bir bölgeyi göstermenin anlamı yok. Sağ tıkla yakınlaştırma (zoom)
        hareketinde bu sınırlama uygulanmıyor."""
        super().drag_pan(event)
        if self._pan_info is None or self._pan_info.button != MouseButton.LEFT:
            return
        for ax in self._pan_info.axes:
            data_left = ax.dataLim.x0
            left, right = ax.get_xlim()
            if left < data_left:
                ax.set_xlim(data_left, data_left + (right - left))
        self.canvas.draw_idle()

    def zoom(self, *args):
        self._restore_pan_view_if_pending()
        super().zoom(*args)

    def home(self, *args):
        self._restore_pan_view_if_pending()
        super().home(*args)

    def reset_view(self, *args):
        """"Sıfırla" butonunun eylemi: grafiği yakınlaştırma/kaydırma
        yapılmamış ilk haline döndürür. Ev butonunun görevini devraldı, o
        yüzden ev simgesi araç çubuğundan kaldırıldı (bkz. `toolitems`).

        SEÇİLİ ARACA DOKUNMAZ: Pan (ya da Yakınlaştır) açıkken sıfırlamak o
        aracı kapatmaz. İlk sürümde kapatıyordu ve kullanıcı her sıfırlamadan
        sonra incelemeye devam edebilmek için Pan'a yeniden basmak zorunda
        kalıyordu — sıfırlamanın amacı incelemeyi kesmek değil, incelenen
        görünümü başa almak."""
        # Pan'a girilirken alınan "geri dönülecek görünüm" anlık taşınıyor:
        # önce düşürülüyor (restore EDİLMEDEN, yoksa home()'un uygulayacağı
        # görünümle çakışırdı), sonra Pan hâlâ açıksa yeni referans olarak
        # sıfırlanmış görünüm alınıyor. Böylece buradan sonra başka bir araca
        # geçildiğinde sıfırlanmış hale dönülür, eski kaydırmaya değil.
        self._pre_pan_view = None
        super().home(*args)  # kendi home() override'ımızı atla: restore zaten yapıldı
        if self.mode == _Mode.PAN:
            self._pre_pan_view = self._snapshot_view()

    def back(self, *args):
        self._restore_pan_view_if_pending()
        super().back(*args)

    def forward(self, *args):
        self._restore_pan_view_if_pending()
        super().forward(*args)

    def save_figure(self, *args):
        self._restore_pan_view_if_pending()
        super().save_figure(*args)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        # CustomTkinter, Windows'ta appearance mode değişince BAŞLIK ÇUBUĞUNU
        # (koyu/açık) yeniden boyamak için pencereyi withdraw()+deiconify()
        # yapıyor (bkz. ctk_tk.py _windows_set_titlebar_color). Tema
        # değiştirirken yaşanan "ekran kapanıp açılıyor / bi gidiyor"ın ASIL
        # kaynağı buydu — benim kodum değil. Bu bayrak o davranışı komple kapatır;
        # başlık çubuğu rengini pencereyi HİÇ gizlemeden kendimiz yönetiyoruz
        # (bkz. _apply_titlebar_theme). Başlangıç rengi zaten super().__init__()
        # sırasında (bayrak daha False'ken) doğru ayarlandı.
        self._deactivate_windows_window_header_manipulation = True
        self.title("İHA Güç/Telemetri Log Analiz Aracı")
        self.geometry("1050x850")
        self.minsize(700, 600)  # panel/toolbar düzeni bundan daha küçükte bozuluyor
        self.configure(fg_color=UI_SURFACE)  # renk çifti: tema değişince ctk pencereyi yerinde günceller

        if ICON_PATH.exists():
            self.iconphoto(True, PhotoImage(file=str(ICON_PATH)))

        self.motor_view_mode = "line"  # "line", "heatmap" ya da "pwm"
        self._motor_colorbar = None
        self._last_motors = None
        # PWM çıkışları motor akımından ayrı bir seri (bkz. _plot_pwm_lines);
        # aynı panelde ama farklı bir görünüm modunda çiziliyor.
        self._last_pwm_outputs = None
        # Yüklü logun araç tipi: grafikteki eşik çizgileri backend'in o log
        # için kullandığı eşiklerle AYNI olmalı, o yüzden saklanıyor.
        self._last_vehicle_type = None

        self.voltage_view_mode = "voltage"  # "voltage" ya da "temperature" (üst panel)
        self.battery_view_mode = "line"  # "line" ya da "heatmap" (busbar yüklenmesi)
        self._battery_colorbar = None
        self._last_batteries = None
        # _load_file arka plan thread'i çalışırken True; testlerin yükleme
        # bitene kadar beklemesi için dışarıdan okunabilir basit bir bayrak.
        self._is_loading = False
        # Ayarlar penceresinden eşik değiştirilince aynı dosyayı otomatik
        # yeniden yükleyebilmek için (bkz. _on_settings_saved).
        self._last_loaded_path = None

        self._current_warnings = []  # PDF raporunun ham uyarı listesine ihtiyacı var
        self._hover_annotation = None  # grafik üzerindeki hover tooltip'i (bkz. _on_plot_hover)
        # _style_nav_toolbar'ın kendini periyodik yeniden zamanlayan after()
        # çağrısının id'si — tema canlı değişiminde (_on_theme_toggle_click)
        # eski döngüyü iptal edebilmek için (bkz. o metodun yorumu).
        self._nav_toolbar_after_id = None

        self._build_ui()

        # Klavye kısayolları: Ctrl+O dosya seç, Ctrl+S grafiği PNG kaydet.
        # self'e (pencerenin kendisi) bağlı, bir kez.
        self.bind("<Control-o>", lambda event: self._on_load_file_click())
        self.bind("<Control-s>", lambda event: self._on_export_png_click())

        self._show_landing()

    def _build_ui(self):
        """Tüm widget ağacını (giriş + analiz ekranı) SADECE bir kez, __init__'ten
        kurar. Tema değişiminde ağaç YENİDEN KURULMAZ — analiz ekranındaki CTk
        widget'ları UI_* renk çiftleriyle oluşturulduğu için ctk onları yerinde
        günceller (bkz. _on_theme_toggle_click), matplotlib figürü de ayrıca
        yeniden çizilir."""
        # Uygulama iki "ekran" (frame) arasında geçiş yapıyor: açılışta
        # gösterilen giriş ekranı ve dosya yüklendikten sonra gösterilen
        # analiz ekranı. İkisi de aynı pencerenin (self) çocuğu; ayrı bir
        # Toplevel pencere DEĞİL, sadece pack/pack_forget ile görünürlük
        # değiştiriliyor.
        self.landing_frame = ctk.CTkFrame(self, fg_color=LANDING_BG)
        self.analysis_frame = ctk.CTkFrame(self, fg_color=UI_SURFACE)

        self._build_landing_screen()

        self._build_toolbar()
        self._build_stats_row()
        self._build_warnings_area()
        self._build_status_label()
        self._build_voltage_view_toggle()
        self._build_battery_view_toggle()
        self._build_motor_view_toggle()
        self._build_plot_area()

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

        # Logo en üstte: önceki sürümde başlığın altındaydı, üstte olması
        # bir "marka" imzası gibi okunuyor (bkz. kullanıcı geri bildirimi).
        # Aynı vektörel drone ikonu kullanılıyor, sadece daha küçük — artık
        # sayfanın odak noktası değil, başlığın üstündeki bir işaret.
        icon_canvas = Canvas(content_block, width=190, height=146, bg=LANDING_BG, highlightthickness=0)
        icon_canvas.pack(pady=(0, 12))
        self._draw_drone_icon(icon_canvas, scale=0.86)

        ctk.CTkLabel(
            content_block, text="İHA Güç/Telemetri Analiz", text_color=LANDING_TEXT,
            font=ctk.CTkFont(size=32, weight="bold"),
        ).pack(pady=(0, 8))
        ctk.CTkLabel(
            content_block, text="Uçuş logunu yükleyip güç/telemetri analizine başla",
            text_color=LANDING_ACCENT_BRIGHT, font=ctk.CTkFont(size=14),
        ).pack(pady=(0, 28))

        ctk.CTkButton(
            content_block, text="Dosya Yükle (Ctrl+O)", command=self._on_load_file_click,
            fg_color=LANDING_ACCENT_BRIGHT, text_color=LANDING_BG, hover_color=LANDING_ACCENT,
            font=ctk.CTkFont(size=16, weight="bold"), width=240, height=48, corner_radius=10,
        ).pack(pady=(0, 14))

        # Desteklenen format rozetleri: hem "hangi dosyalar kabul ediliyor"
        # sorusuna görsel bir yanıt, hem de butonun altındaki boşluğu
        # dolduran, sayfayı daha "dolu"/dengeli gösteren bir içerik satırı.
        formats_row = ctk.CTkFrame(content_block, fg_color="transparent")
        formats_row.pack(pady=(0, 18))
        for label in (".bin", ".ulog", ".ulg"):
            ctk.CTkLabel(
                formats_row, text=label, text_color=LANDING_ACCENT_BRIGHT,
                font=ctk.CTkFont(size=11, weight="bold"), fg_color=LANDING_CARD,
                corner_radius=6, width=52, height=22,
            ).pack(side="left", padx=4)

        sample_file = self._pick_sample_data_file()
        if sample_file is not None:
            sample_link = ctk.CTkButton(
                content_block, text="veya örnek veri ile dene →", command=self._on_try_sample_click,
                fg_color="transparent", hover_color=LANDING_BG, text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=12, underline=True), width=0, height=20,
            )
            sample_link.pack()
            sample_link.bind("<Enter>", lambda _e: sample_link.configure(text_color=LANDING_ACCENT_BRIGHT))
            sample_link.bind("<Leave>", lambda _e: sample_link.configure(text_color=TEXT_MUTED))

        ctk.CTkLabel(
            self.landing_frame,
            text=f"İHA Güç/Telemetri Log Analiz Aracı · MIT Lisansı ile açık kaynak · v{APP_VERSION}",
            text_color="#3a5a78", font=ctk.CTkFont(size=11),
        ).pack(side="bottom", pady=12)

    def _pick_sample_data_file(self):
        """Onboarding için: takım arkadaşı kendi log dosyası olmadan da
        uygulamayı deneyebilsin diye `data/` altındaki örneklerden birini
        seçer. Paketlenmiş dağıtımda `data/` klasörü genelde yoktur, o
        durumda None döner ve giriş ekranında link hiç gösterilmez."""
        if not DATA_DIR.exists():
            return None
        preferred = DATA_DIR / "px4_sample_log_small.ulg"
        if preferred.exists():
            return preferred
        candidates = sorted(DATA_DIR.glob("*.bin")) + sorted(DATA_DIR.glob("*.BIN")) + \
            sorted(DATA_DIR.glob("*.ulog")) + sorted(DATA_DIR.glob("*.ulg"))
        return candidates[0] if candidates else None

    def _on_try_sample_click(self):
        sample_file = self._pick_sample_data_file()
        if sample_file is not None:
            self._load_file(str(sample_file))

    def _build_recent_sidebar(self):
        """Giriş ekranının sol tarafındaki 'Geçmiş Dosyalar' paneli."""
        sidebar = ctk.CTkFrame(self.landing_frame, fg_color="#0c1e30", corner_radius=0, width=290)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ctk.CTkLabel(
            sidebar, text="Geçmiş Dosyalar", text_color=LANDING_TEXT,
            font=ctk.CTkFont(size=15, weight="bold"), anchor="w",
        ).pack(fill="x", padx=16, pady=(24, 12))

        # Varsayılan CTkScrollableFrame kaydırma çubuğu parlak/beyaz geliyor
        # ve koyu giriş temasıyla uyumsuz duruyordu; renklerini paletle
        # eşleştiriyoruz.
        self._recent_sidebar_list = ctk.CTkScrollableFrame(
            sidebar, fg_color="transparent",
            scrollbar_fg_color="#0c1e30", scrollbar_button_color=LANDING_ACCENT,
            scrollbar_button_hover_color=LANDING_ACCENT_BRIGHT,
        )
        self._recent_sidebar_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Karşılaştırma girişi buraya konuldu, analiz ekranının toolbar'ına
        # değil: karşılaştırılacak uçuşlar zaten bu listeden seçiliyor ve
        # toolbar dolu (geçmişte taşma sorununa yol açmıştı).
        self.compare_button = ctk.CTkButton(
            sidebar, text="⇄ Uçuşları Karşılaştır", fg_color="transparent",
            border_width=1, border_color=LANDING_ACCENT, text_color=LANDING_TEXT,
            hover_color=LANDING_ACCENT, command=self._on_compare_click,
        )
        self.compare_button.pack(fill="x", padx=16, pady=(0, 20))

        self._refresh_recent_sidebar(self._load_recent_files())

    def _refresh_recent_sidebar(self, entries: list):
        """Giriş ekranındaki geçmiş dosyalar listesini yeniden çizer. Liste
        en fazla MAX_RECENT_FILES (5) öğe olduğu için her seferinde
        temizleyip yeniden oluşturmanın maliyeti önemsiz.

        Her kart artık sadece dosya adını değil, format rozetini (BIN/ULG),
        ne zaman açıldığını ('2 saat önce') ve uçuş süresini de gösteriyor —
        önceden her satır birbirinin aynısı görünüyordu, bu meta veri hangi
        dosyanın hangisi olduğunu bir bakışta ayırt etmeyi kolaylaştırıyor."""
        for child in self._recent_sidebar_list.winfo_children():
            child.destroy()

        if not entries:
            ctk.CTkLabel(
                self._recent_sidebar_list, text="Henüz geçmiş yok.",
                text_color=TEXT_MUTED, font=ctk.CTkFont(size=12),
            ).pack(pady=8)
            return

        # Bu üç font, aşağıdaki her kart için TEK SEFER oluşturulup yeniden
        # kullanılıyor (5 kart x 3 font yerine 3 font nesnesi yeterli).
        name_font = ctk.CTkFont(size=12, weight="bold")
        meta_font = ctk.CTkFont(size=10)
        badge_font = ctk.CTkFont(size=10, weight="bold")
        for entry in entries:
            self._build_recent_card(entry, name_font, meta_font, badge_font)

    def _build_recent_card(self, entry: dict, name_font, meta_font, badge_font):
        """Geçmiş Dosyalar listesindeki tek bir satırı çizer.

        Önceki sürüm bir CTkButton'ın üzerine üç ayrı CTkLabel (rozet/ad/
        meta) bindiriyordu — bu, üç ayrı sorunun kök nedeniydi: (1) her
        label kendi doğal metin genişliğine göre boyutlanıp kenarlardan
        taşabiliyordu, (2) fare label'lar arasında geçtikçe Tk'nin <Leave>/
        <Enter> çiftini ART ARDA tetiklemesi yüzünden kartın arka planıyla
        label'ların rengi birbirinden bağımsız kalıp senkron bozuluyordu
        (rozet/metin okunmaz hale geliyordu), (3) sabit karakter sayısıyla
        kırpma harf genişliği değişken olduğu için tutarsız kesim
        üretiyordu. TEK bir Canvas'a geçmek üçünü de kökten çözüyor: kart,
        rozet, ad ve meta yazısı aynı çizim yüzeyinin parçaları, hover
        rengi tek bir yerden (`state`) atomik olarak güncelleniyor ve
        kırpma gerçek piksel genişliğine göre hesaplanıyor."""
        path_obj = Path(entry["path"])
        is_bin = path_obj.suffix.lower() == ".bin"
        badge_text = "BIN" if is_bin else "ULG"
        badge_color = LANDING_BADGE_BIN if is_bin else LANDING_BADGE_ULOG

        meta_parts = []
        if entry.get("last_opened"):
            meta_parts.append(_format_relative_time(datetime.fromisoformat(entry["last_opened"])))
        if entry.get("duration_s") is not None:
            meta_parts.append(_format_duration_short(entry["duration_s"]))
        meta_text = " · ".join(meta_parts)

        card_height = 60
        badge_w, badge_h = 36, 18
        text_x = 10 + badge_w + 10
        state = {"color": LANDING_CARD}

        canvas = tk.Canvas(self._recent_sidebar_list, height=card_height, bg=LANDING_BG, highlightthickness=0)
        canvas.pack(fill="x", pady=4)

        def redraw(_e=None):
            width = canvas.winfo_width()
            if width <= 1:
                return
            canvas.delete("all")
            canvas.create_rectangle(
                0, 0, width - 1, card_height - 1,
                fill=state["color"], outline=LANDING_CARD_BORDER, width=1,
            )
            badge_y = card_height / 2
            canvas.create_rectangle(
                10, badge_y - badge_h / 2, 10 + badge_w, badge_y + badge_h / 2,
                fill=badge_color, outline="",
            )
            canvas.create_text(10 + badge_w / 2, badge_y, text=badge_text, fill=LANDING_BG, font=badge_font)

            available = width - text_x - 12  # sağ kenardan da pay bırak
            canvas.create_text(
                text_x, 20, text=_truncate_to_width(path_obj.stem, available, name_font),
                fill=LANDING_TEXT, font=name_font, anchor="w",
            )
            if meta_text:
                # LANDING_TEXT_SECONDARY kullanılıyor (TEXT_SECONDARY DEĞİL):
                # TEXT_SECONDARY tema değişince (koyu/açık) renk değiştiriyor,
                # ama bu kartın zemini (LANDING_CARD) her zaman koyu lacivert
                # kalıyor — açık temada ikisi neredeyse aynı tona düşüp
                # okunmaz hale gelirdi. Hover'da (parlak mavi zemin) LANDING_TEXT'e
                # geçiyor, aksi halde orada da soluk kalırdı.
                meta_color = LANDING_TEXT if state["color"] == LANDING_ACCENT else LANDING_TEXT_SECONDARY
                canvas.create_text(
                    text_x, 42, text=_truncate_to_width(meta_text, available, meta_font),
                    fill=meta_color, font=meta_font, anchor="w",
                )

        def on_enter(_e=None):
            state["color"] = LANDING_ACCENT
            redraw()

        def on_leave(_e=None):
            state["color"] = LANDING_CARD
            redraw()

        def on_click(_e=None):
            self._load_file(entry["path"])

        canvas.bind("<Configure>", redraw)
        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)
        canvas.bind("<Button-1>", on_click)

    def _draw_drone_icon(self, canvas: Canvas, scale: float = 1.0):
        """Orijinal, sade bir çeyrek-kanat (quadcopter) ikonu çizer: Canvas
        ilkelleriyle (çizgi + daire) oluşturulmuş vektörel bir siluet, bir
        fotoğraf DEĞİL — telif riski yok, her boyutta net görünür.

        Önceki sürüme göre daha net okunsun diye: kalın, yuvarlak uçlu kollar,
        pervane detayını belirtmek için rotor dairelerinin içinde çapraz
        kanatçıklar, gövdenin altında iki iniş ayağı eklendi.

        `scale`, aynı çizimi giriş ekranının üstündeki küçük bir logo olarak
        da kullanabilmek için (bkz. `_build_landing_screen`) tüm boyutları
        orantılı küçültüp büyütüyor."""
        cx, cy = 110 * scale, 85 * scale
        arm_len = 68 * scale
        rotor_r = 22 * scale
        leg_w = max(1, round(3 * scale))
        arm_w = max(1, round(6 * scale))
        rotor_outline_w = max(1, round(3 * scale))
        blade_w = max(1, round(2 * scale))
        body_outline_w = max(1, round(2 * scale))

        # İniş ayakları (gövdenin arkasında kalsın diye kollardan/gövdeden önce çizilir).
        for dx in (-1, 1):
            canvas.create_line(
                cx + dx * 14 * scale, cy + 10 * scale, cx + dx * 20 * scale, cy + 34 * scale,
                fill=LANDING_ACCENT_BRIGHT, width=leg_w, capstyle="round",
            )

        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            end_x, end_y = cx + dx * arm_len, cy + dy * arm_len * 0.55
            canvas.create_line(
                cx, cy, end_x, end_y, fill=LANDING_ACCENT_BRIGHT, width=arm_w, capstyle="round",
            )
            canvas.create_oval(
                end_x - rotor_r, end_y - rotor_r, end_x + rotor_r, end_y + rotor_r,
                outline=LANDING_ACCENT_BRIGHT, width=rotor_outline_w,
            )
            # Pervane kanatçıkları: rotor dairesinin içinde çapraz iki çizgi.
            canvas.create_line(
                end_x - rotor_r + 4 * scale, end_y, end_x + rotor_r - 4 * scale, end_y,
                fill=LANDING_ACCENT_BRIGHT, width=blade_w, capstyle="round",
            )
            canvas.create_line(
                end_x, end_y - rotor_r + 4 * scale, end_x, end_y + rotor_r - 4 * scale,
                fill=LANDING_ACCENT_BRIGHT, width=blade_w, capstyle="round",
            )

        # Gövde (kollardan sonra çizilir ki kolların gövdeye giriş noktaları temiz görünsün).
        canvas.create_oval(
            cx - 30 * scale, cy - 18 * scale, cx + 30 * scale, cy + 18 * scale,
            fill=LANDING_ACCENT, outline=LANDING_ACCENT_BRIGHT, width=body_outline_w,
        )
        # Ön sensör/kamera noktası — gövdeye küçük bir karakter/detay katar.
        canvas.create_oval(
            cx - 6 * scale, cy - 6 * scale, cx + 6 * scale, cy + 6 * scale,
            fill=LANDING_BG, outline=LANDING_ACCENT_BRIGHT, width=body_outline_w,
        )

    def _build_voltage_view_toggle(self):
        """Üst paneli voltaj/sıcaklık arasında değiştiren seçici — akım/motor
        panellerindeki çizgi↔ısı haritası geçişiyle aynı desen (sabit
        gridspec'e dokunmadan, delaxes+add_subplot yerine burada sadece
        ax_voltage'ı temizleyip yeniden çiziyoruz, çünkü ikisi de düz çizgi
        grafiği — heatmap gibi ayrı bir colorbar ekseni gerekmiyor)."""
        toggle_row = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
        toggle_row.pack(side="top", fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(
            toggle_row, text="Üst Panel:", text_color=UI_TEXT_MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(0, 8))

        self.voltage_view_toggle = ctk.CTkSegmentedButton(
            toggle_row, values=["Voltaj", "Sıcaklık"], command=self._on_voltage_view_change,
            **_segmented_button_style(),
        )
        # Mevcut modu yansıtır (sabit "Voltaj" değil): kullanıcı sıcaklık
        # görünümündeyken başka bir dosya yükleyip toggle yeniden kurulsa da
        # seçili görünüm korunsun diye.
        self.voltage_view_toggle.set("Sıcaklık" if self.voltage_view_mode == "temperature" else "Voltaj")  # kayıtlı modu yansıt
        self.voltage_view_toggle.pack(side="left")

    def _on_voltage_view_change(self, value: str):
        self.voltage_view_mode = "temperature" if value == "Sıcaklık" else "voltage"
        if self._last_batteries is not None:
            self._plot_voltage_panel(self._last_batteries)
            self.canvas.draw()

    def _build_battery_view_toggle(self):
        """Toplam akım (busbar yüklenmesi) panelini çizgi grafiği/ısı haritası
        arasında değiştiren seçici. Birden fazla batarya (ör. yedekli güç
        hattı) olduğunda hangi busbar'ın ne zaman daha yüklü olduğunu
        karşılaştırmak için kullanışlı."""
        toggle_row = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
        toggle_row.pack(side="top", fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(
            toggle_row, text="Busbar Görünümü:", text_color=UI_TEXT_MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(0, 8))

        self.battery_view_toggle = ctk.CTkSegmentedButton(
            toggle_row, values=["Çizgi Grafiği", "Isı Haritası"],
            command=self._on_battery_view_change, **_segmented_button_style(),
        )
        self.battery_view_toggle.set("Isı Haritası" if self.battery_view_mode == "heatmap" else "Çizgi Grafiği")
        self.battery_view_toggle.pack(side="left")

    def _on_battery_view_change(self, value: str):
        self.battery_view_mode = "heatmap" if value == "Isı Haritası" else "line"
        if self._last_batteries is not None:
            self._plot_battery_currents(self._last_batteries)
            self.canvas.draw()
            self.nav_toolbar.push_current()  # yeni görünüm "başa dön" hedefi olsun

    def _build_motor_view_toggle(self):
        """Alt paneli motor akımı (çizgi/ısı haritası) ile çıkış PWM'i arasında
        değiştiren seçici. PWM ayrı bir panel değil bu panelin üçüncü modu:
        akım sensörü olmayan araçlarda (birçok rover) motor akımı zaten hiç
        yok, PWM tam onun yerine geçiyor."""
        toggle_row = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
        toggle_row.pack(side="top", fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(
            toggle_row, text="Motor Görünümü:", text_color=UI_TEXT_MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(0, 8))

        self.motor_view_toggle = ctk.CTkSegmentedButton(
            toggle_row, values=list(MOTOR_VIEW_MODES),
            command=self._on_motor_view_change, **_segmented_button_style(),
        )
        self.motor_view_toggle.set(MOTOR_VIEW_LABELS[self.motor_view_mode])
        self.motor_view_toggle.pack(side="left")

    def _on_motor_view_change(self, value: str):
        self.motor_view_mode = MOTOR_VIEW_MODES.get(value, "line")
        # PWM modunda motor akımı hiç olmayabilir (rover); bu yüzden çizim
        # koşulu _last_motors DEĞİL, "bir dosya yüklenmiş mi" olmalı.
        if self._last_motors is not None or self._last_pwm_outputs is not None:
            self._plot_motor_currents(self._last_motors or [])
            self.canvas.draw()
            self.nav_toolbar.push_current()  # yeni görünüm "başa dön" hedefi olsun

    def _build_toolbar(self):
        """Üst kısımdaki dosya yükleme/dışa aktarma/temizle butonları, son
        kullanılan dosyalar açılır listesi ve seçilen dosya etiketi."""
        toolbar = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
        toolbar.pack(side="top", fill="x", padx=16, pady=(16, 8))

        self.home_button = ctk.CTkButton(
            toolbar, text="← Ana Sayfa", fg_color="transparent", border_width=1,
            border_color=UI_AXIS_LINE, text_color=UI_TEXT_SECONDARY, command=self._show_landing,
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
            toolbar, text="Henüz dosya seçilmedi.", text_color=UI_TEXT_SECONDARY
        )
        self.file_label.pack(side="left")

        # Backend arka planda çalışırken gösterilir (bkz. _load_file);
        # varsayılan olarak paketlenmez, sadece yükleme sırasında görünür.
        self.loading_progress = ctk.CTkProgressBar(toolbar, mode="indeterminate", width=120)

        self.theme_button = ctk.CTkButton(
            toolbar, text=("☀ Açık Tema" if ACTIVE_THEME == "dark" else "🌙 Koyu Tema"),
            fg_color="transparent", border_width=1, border_color=UI_AXIS_LINE,
            text_color=UI_TEXT_SECONDARY, command=self._on_theme_toggle_click,
        )
        self.theme_button.pack(side="right", padx=(0, 12))

        self.settings_button = ctk.CTkButton(
            toolbar, text="⚙ Ayarlar", fg_color="transparent", border_width=1,
            border_color=UI_AXIS_LINE, text_color=UI_TEXT_SECONDARY, command=self._on_settings_click,
        )
        self.settings_button.pack(side="right", padx=(0, 12))

        # Üç ayrı dışa aktarma butonu yerine tek açılır menü: ölçüldü, üç
        # buton toolbar'ın istediği genişliği 1638px'e çıkarıyordu ve 1360px'lik
        # yaygın bir ekranda "Temizle"/"CSV" butonları ekran dışında kalıyordu.
        # Menü aynı işi ~150px'te yapıyor ve ileride yeni bir biçim eklenirse
        # toolbar'ı yine büyütmüyor.
        #
        # CTkOptionMenu burada bir "seçim" değil EYLEM menüsü olarak
        # kullanılıyor: seçim kalıcı değil, her seçimden sonra etiket geri
        # dönüyor (recent_menu de aynı deseni kullanıyor).
        self.export_menu = ctk.CTkOptionMenu(
            toolbar, values=list(EXPORT_MENU_ACTIONS), command=self._on_export_selected,
            width=150,
        )
        self.export_menu.set(EXPORT_MENU_LABEL)
        self.export_menu.pack(side="right", padx=(0, 12))

        self.clear_button = ctk.CTkButton(
            toolbar, text="Temizle", fg_color="transparent", border_width=1,
            border_color=UI_AXIS_LINE, text_color=UI_TEXT_SECONDARY, command=self._on_clear_click,
        )
        self.clear_button.pack(side="right", padx=(0, 12))

    def _apply_titlebar_theme(self, mode: str):
        """Windows başlık çubuğunu (uygulama adının olduğu üst OS çubuğu) koyu/açık
        yapar — ama ctk'nin yaptığı gibi pencereyi withdraw()+deiconify() ETMEDEN.
        DWM 'immersive dark mode' özniteliğini ayarlayıp SetWindowPos ile SADECE
        pencere çerçevesini (non-client alan) yeniden çizdiririz; içerik/pencere
        kıpırdamaz, ekran kaybolmaz. Windows dışında ya da API başarısız olursa
        sessizce hiçbir şey yapmaz (başlık çubuğu teması kritik değil)."""
        if not sys.platform.startswith("win"):
            return
        try:
            value = ctypes.c_int(1 if mode == "dark" else 0)
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
            for attr in (DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
                )
            # Yeni özniteliğin başlık çubuğuna HEMEN yansıması için sadece çerçeveyi
            # yeniden çizdir (SWP_FRAMECHANGED). Taşıma/boyutlandırma/z-sıra yok,
            # pencere gizlenmiyor — withdraw'daki gibi bir "gidip gelme" olmaz.
            SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x0002, 0x0001, 0x0004, 0x0020
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
            )
        except Exception:
            pass

    def _on_theme_toggle_click(self):
        """Açık/koyu tema arasında CANLI geçiş. Pencere HİÇ gizlenmez/kaybolmaz,
        boyutu/konumu değişmez — sadece renkler değişir.

        Önemli: 'ekran kapanıp açılıyor / bi gidiyor' sorununun asıl kaynağı
        CustomTkinter'ın Windows başlık çubuğunu boyamak için pencereyi
        withdraw()+deiconify() yapmasıydı. __init__'te
        _deactivate_windows_window_header_manipulation=True ile bunu kapattık;
        başlık çubuğunu _apply_titlebar_theme ile pencereyi gizlemeden kendimiz
        güncelliyoruz.

        Renk geçişi iki koldan: (1) CTk widget'ları UI_* renk ÇİFTLERİYLE
        kurulduğundan ctk.set_appearance_mode onları yerinde çevirir; (2)
        matplotlib figürü ctk'nin dışında olduğundan renkleri elle güncellenip
        yeniden çizilir."""
        global ACTIVE_THEME, SURFACE, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED
        global GRIDLINE, AXIS_LINE, COLOR_CRITICAL, COLOR_WARNING, PWM_DEVIATION_COLORS

        new_theme = "dark" if ACTIVE_THEME == "light" else "light"
        settings = _load_settings()
        settings["theme"] = new_theme
        _save_settings(settings)

        # (1) Matplotlib/giriş ekranı/nav-toolbar'ın kullandığı tekil renk
        # sabitlerini yeni paletle güncelle (CTk widget'ları bunları KULLANMAZ;
        # onlar UI_* çiftlerini kullanır, set_appearance_mode ile kendi geçer).
        ACTIVE_THEME = new_theme
        palette = LIGHT_PALETTE if new_theme == "light" else DARK_PALETTE
        SURFACE = palette["SURFACE"]
        TEXT_PRIMARY = palette["TEXT_PRIMARY"]
        TEXT_SECONDARY = palette["TEXT_SECONDARY"]
        TEXT_MUTED = palette["TEXT_MUTED"]
        GRIDLINE = palette["GRIDLINE"]
        AXIS_LINE = palette["AXIS_LINE"]
        COLOR_CRITICAL = palette["COLOR_CRITICAL"]
        COLOR_WARNING = palette["COLOR_WARNING"]
        PWM_DEVIATION_COLORS = palette["PWM_DEVIATION_COLORS"]

        # (2) Tüm pencereyi HEDEF renkte tek bir opak dikdörtgenle (örtü) kapla.
        # Neden: Windows, tema değişiminde her widget'ı (kutuyu) AYRI bir bölge
        # olarak yeniden boyuyor; bu yüzden açıktan koyuya geçerken kutular sırayla
        # dönüyormuş gibi "kare kare" görünüyordu — bu CustomTkinter/Tk'nin temel
        # bir davranışı, tek boyamaya indirsek bile bölge bölge basılıyor. Örtü
        # TEK bir widget olduğundan TEK, anlık bir boyama = ekran bir anda "dümdüz"
        # yeni renge döner. Tüm kademeli yeniden boyama bu örtünün ALTINDA görünmez
        # olur; içerik aynı tema ailesinden (koyu-üstüne-koyu) ortaya çıktığı için
        # örtü kalkınca yumuşak görünür, yüksek kontrastlı "kare kare" olmaz.
        overlay = tk.Frame(self, bg=SURFACE, highlightthickness=0, bd=0)
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        overlay.lift()
        self.update_idletasks()  # örtüyü HEMEN, tek boyamada bas

        try:
            # (3) Matplotlib figürünü yeni renklere çevir ve çiz (örtü altında).
            # canvas'ın ham Tk zemini varsayılan BEYAZ; SURFACE'e sabitle.
            self.figure.set_facecolor(SURFACE)
            self.canvas.get_tk_widget().configure(bg=SURFACE)
            if self._last_batteries is not None:
                self._plot_voltage_panel(self._last_batteries)
                self._plot_battery_currents(self._last_batteries)
            if self._last_motors is not None:
                self._plot_motor_currents(self._last_motors)
            self.canvas.draw()

            # (4) CTk widget'larını yeni temaya geçir. Kademeli boyama örtü altında
            # görünmez; yine de gereksiz 80 ara boyamayı hızlandırmak için ctk'nin
            # her widget'ta çağırdığı update_idletasks'i geçici olarak etkisiz kıl.
            _real_update_idletasks = tk.Misc.update_idletasks
            tk.Misc.update_idletasks = lambda _self: None
            try:
                ctk.set_appearance_mode(new_theme)
            finally:
                tk.Misc.update_idletasks = _real_update_idletasks
            self.theme_button.configure(
                text=("☀ Açık Tema" if new_theme == "dark" else "🌙 Koyu Tema")
            )

            # (5) Başlık çubuğunu (OS üst çubuğu) pencereyi gizlemeden güncelle.
            self._apply_titlebar_theme(new_theme)

            # (6) Navigasyon toolbar'ı ham Tk widget'ı; ctk yönetmez.
            if self._nav_toolbar_after_id is not None:
                self.after_cancel(self._nav_toolbar_after_id)
                self._nav_toolbar_after_id = None
            self._style_nav_toolbar()
        finally:
            # (7) Her şey yeni temada hazır; örtüyü kaldır ve tek boyamada ortaya
            # çıkar. Alttaki içerik aynı renk ailesinden olduğu için bu geçiş
            # yumuşak görünür.
            overlay.destroy()
            self.update_idletasks()

    def _on_export_selected(self, choice: str):
        """Dışa aktarma menüsünden bir biçim seçildiğinde ilgili kaydetme
        akışını başlatır. Menü bir seçim değil eylem listesi olduğu için
        etiket hemen geri alınıyor — aksi halde son seçilen biçim menüde
        seçili kalır ve "şu an CSV modundayım" gibi yanlış bir izlenim verir."""
        self.export_menu.set(EXPORT_MENU_LABEL)
        handler = EXPORT_MENU_ACTIONS.get(choice)
        if handler:
            getattr(self, handler)()

    def _on_settings_click(self):
        """Uyarı eşiklerini (voltaj düşümü %, akım dengesizliği %, negatif
        akım A) düzenleyen basit bir pencere. Değerler her yüklemede backend'e
        --flag olarak geçiliyor (bkz. _run_backend); burada sadece diske
        yazılır. Bir dosya zaten yüklüyse, kaydedince yeni eşiklerle otomatik
        yeniden yüklenir — kullanıcı farkı hemen görsün diye.

        Eşikler ARAÇ TİPİ BAŞINA saklanabilir: üstteki seçici "Genel"deyken
        tüm araçlar için geçerli değerler, bir araç tipi seçiliyken sadece o
        tip için geçerli olanlar düzenlenir. Açılışta, yüklü logun araç tipi
        varsa doğrudan o seçili gelir. Varsayılanlar tüm tiplerde aynıdır —
        örnek loglarda araç tipine göre farklı sayılar önermeyi destekleyen
        bir ayrışma ölçülmedi (bkz. SETTINGS_VEHICLE_THRESHOLDS_KEY)."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Uyarı Eşikleri")
        dialog.geometry("400x400")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog, text="Uyarı Eşikleri", font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(pady=(16, 8))

        # Seçici etiketi -> ayarların saklanacağı araç tipi (None = genel).
        scope_options = {"Genel (tüm araçlar)": None}
        for vehicle_type, label in VEHICLE_TYPE_LABELS.items():
            scope_options[f"Sadece {label}"] = vehicle_type

        scope_row = ctk.CTkFrame(dialog, fg_color="transparent")
        scope_row.pack(fill="x", padx=20, pady=(0, 4))
        ctk.CTkLabel(
            scope_row, text="Hangi araç için?", text_color=TEXT_SECONDARY, anchor="w",
        ).pack(side="top", fill="x")
        scope_menu = ctk.CTkOptionMenu(scope_row, values=list(scope_options))
        scope_menu.pack(side="top", fill="x", pady=(4, 0))

        # Yüklü logun tipi varsa doğrudan onunla açılır: kullanıcı çoğunlukla
        # az önce baktığı uçuş için eşik ayarlamak ister.
        current_label = next(
            (label for label, value in scope_options.items() if value == self._last_vehicle_type),
            "Genel (tüm araçlar)",
        )
        scope_menu.set(current_label)

        fields = [
            ("voltage_sag_pct", "Voltaj Düşümü Eşiği (%)"),
            ("current_imbalance_pct", "Akım Dengesizliği Eşiği (%)"),
            ("negative_current_a", "Negatif Akım Eşiği (A)"),
        ]
        entries = {}
        for key, label in fields:
            row = ctk.CTkFrame(dialog, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=6)
            ctk.CTkLabel(row, text=label, text_color=TEXT_SECONDARY, anchor="w").pack(side="top", fill="x")
            entry = ctk.CTkEntry(row)
            entry.pack(side="top", fill="x", pady=(4, 0))
            entries[key] = entry

        hint_label = ctk.CTkLabel(dialog, text="", text_color=TEXT_MUTED, font=ctk.CTkFont(size=11))
        hint_label.pack(pady=(2, 0))

        def fill_fields(scope_label: str):
            """Seçilen kapsamın kayıtlı değerlerini alanlara yazar. O kapsam
            için özel bir kayıt yoksa genel değerler gösterilir (kullanıcı
            kaydetmedikçe yeni bir kayıt oluşmaz)."""
            vehicle_type = scope_options.get(scope_label)
            overrides = _get_vehicle_threshold_overrides()
            has_override = vehicle_type is not None and vehicle_type in overrides
            values = overrides[vehicle_type] if has_override else _get_general_thresholds()

            for key, value in (
                ("voltage_sag_pct", values["voltage_sag_threshold"] * 100),
                ("current_imbalance_pct", values["current_imbalance_threshold"] * 100),
                ("negative_current_a", values["negative_current_threshold"]),
            ):
                entries[key].delete(0, "end")
                entries[key].insert(0, f"{value:g}")

            if vehicle_type is None:
                hint_label.configure(text="Kendi eşiği tanımlı olmayan tüm araçlar için geçerli.")
            elif has_override:
                hint_label.configure(text="Bu araç tipinin kendi eşikleri tanımlı.")
            else:
                hint_label.configure(text="Şu an genel eşikleri kullanıyor; kaydedersen ayrılır.")

        fill_fields(scope_menu.get())
        scope_menu.configure(command=fill_fields)

        error_label = ctk.CTkLabel(dialog, text="", text_color=COLOR_CRITICAL)
        error_label.pack(pady=(4, 0))

        def on_save():
            try:
                voltage_sag_pct = float(entries["voltage_sag_pct"].get())
                current_imbalance_pct = float(entries["current_imbalance_pct"].get())
                negative_current_a = float(entries["negative_current_a"].get())
            except ValueError:
                error_label.configure(text="Lütfen geçerli sayılar girin.")
                return
            if not (0 < voltage_sag_pct < 100) or not (0 < current_imbalance_pct < 100):
                error_label.configure(text="Yüzde değerleri 0-100 arasında olmalı.")
                return
            if negative_current_a >= 0:
                error_label.configure(text="Negatif akım eşiği 0'dan küçük olmalı.")
                return

            values = {
                "voltage_sag_threshold": voltage_sag_pct / 100,
                "current_imbalance_threshold": current_imbalance_pct / 100,
                "negative_current_threshold": negative_current_a,
            }
            settings = _load_settings()
            vehicle_type = scope_options.get(scope_menu.get())
            if vehicle_type is None:
                settings.update(values)
            else:
                by_vehicle = settings.get(SETTINGS_VEHICLE_THRESHOLDS_KEY)
                if not isinstance(by_vehicle, dict):
                    by_vehicle = {}
                by_vehicle[vehicle_type] = values
                settings[SETTINGS_VEHICLE_THRESHOLDS_KEY] = by_vehicle
            _save_settings(settings)
            dialog.destroy()

            if self._last_loaded_path:
                self._load_file(self._last_loaded_path)  # yeni eşiklerle otomatik yeniden yükle
            else:
                self.status_label.configure(text="Yeni eşikler kaydedildi.", text_color=UI_TEXT_SECONDARY)

        button_row = ctk.CTkFrame(dialog, fg_color="transparent")
        button_row.pack(pady=16)
        ctk.CTkButton(button_row, text="Kaydet", command=on_save).pack(side="left", padx=6)
        ctk.CTkButton(
            button_row, text="İptal", fg_color="transparent", border_width=1,
            border_color=AXIS_LINE, text_color=TEXT_SECONDARY, command=dialog.destroy,
        ).pack(side="left", padx=6)

    def _on_compare_click(self):
        """Geçmiş dosyalardan 2+ uçuş seçip özet metriklerini yan yana
        gösteren pencereyi açar.

        Neden grafik değil tablo: örnek loglar ölçüldüğünde uçuşların zaman
        eksenleri hiç örtüşmüyordu (ilk damgalar 3 s ile 4014 s arasında) ve
        batarya sınıfları farklıydı (3S/6S/12S). Aynı panele çizmek okunaksız
        olurdu; özet metrikler ise ölçekten ve süreden bağımsız
        karşılaştırılabiliyor."""
        entries = self._load_recent_files()
        if len(entries) < 2:
            self.status_label.configure(
                text="⚠ Karşılaştırma için en az iki geçmiş dosya gerekli.",
                text_color=UI_COLOR_WARNING,
            )
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Uçuşları Karşılaştır")
        dialog.geometry("920x720")
        dialog.transient(self)

        ctk.CTkLabel(
            dialog, text="Karşılaştırılacak uçuşları seç",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(pady=(16, 4))

        # Düz bir frame (kaydırılabilir değil): geçmiş en fazla
        # MAX_RECENT_FILES öğe tuttuğu için liste zaten kısa, kaydırılabilir
        # yapmak sabit yükseklikte durmayıp tabloya ayrılan yeri yiyordu.
        selection_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        selection_frame.pack(fill="x", padx=20)

        checkboxes = []
        for entry in entries:
            variable = ctk.BooleanVar(value=False)
            ctk.CTkCheckBox(
                selection_frame, text=Path(entry["path"]).name, variable=variable,
            ).pack(anchor="w", pady=3)
            checkboxes.append((entry["path"], variable))

        status = ctk.CTkLabel(dialog, text="", text_color=TEXT_MUTED)
        status.pack(pady=(6, 0))

        result_frame = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        result_frame.pack(fill="both", expand=True, padx=20, pady=(8, 12))

        def on_compare():
            selected = [path for path, variable in checkboxes if variable.get()]
            if len(selected) < 2:
                status.configure(text="En az iki uçuş seçmelisin.", text_color=COLOR_WARNING)
                return
            status.configure(text="Uçuşlar işleniyor...", text_color=TEXT_MUTED)
            compare_button.configure(state="disabled")
            self._run_comparison(selected, result_frame, status, compare_button)

        button_row = ctk.CTkFrame(dialog, fg_color="transparent")
        button_row.pack(pady=(0, 12))
        compare_button = ctk.CTkButton(button_row, text="Karşılaştır", command=on_compare)
        compare_button.pack(side="left", padx=6)
        ctk.CTkButton(
            button_row, text="Kapat", fg_color="transparent", border_width=1,
            border_color=AXIS_LINE, text_color=TEXT_SECONDARY, command=dialog.destroy,
        ).pack(side="left", padx=6)

    def _run_comparison(self, paths: list, result_frame, status_label, compare_button):
        """Seçilen logları arka planda backend'den geçirip tabloyu çizer.

        Her log için ayrı bir backend çağrısı yapılıyor ve bu saniyeler
        sürebildiğinden iş ayrı bir thread'de; sonuç Queue üzerinden ana
        thread'e taşınıyor (Tkinter widget'ları başka thread'den güvenle
        güncellenemez — bkz. _load_file'daki aynı desen)."""
        result_queue: queue.Queue = queue.Queue()

        def worker():
            results, errors = [], []
            for path in paths:
                try:
                    results.append((Path(path).name, _flight_summary_metrics(self._run_backend(path))))
                except Exception as error:  # bir log bozuksa diğerleri yine gösterilsin
                    errors.append(f"{Path(path).name}: {error}")
            result_queue.put((results, errors))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                results, errors = result_queue.get_nowait()
            except queue.Empty:
                self.after(100, poll)
                return

            if compare_button.winfo_exists():
                compare_button.configure(state="normal")
            # Tek kontrol yeterli: result_frame ile status_label aynı
            # pencerenin çocukları, pencere kapatılınca ikisi birden yok olur.
            if not result_frame.winfo_exists():
                return  # kullanıcı pencereyi kapatmış

            self._build_comparison_table(result_frame, results)
            if errors:
                status_label.configure(text="⚠ " + " | ".join(errors), text_color=COLOR_CRITICAL)
            else:
                status_label.configure(
                    text=f"{len(results)} uçuş karşılaştırıldı.", text_color=TEXT_MUTED
                )

        self.after(100, poll)

    def _build_comparison_table(self, parent, results: list):
        """Satır = metrik, sütun = uçuş olacak şekilde özet tabloyu çizer.
        Ölçülemeyen değerler "—" gösterilir (bkz. _format_comparison_value)."""
        for child in parent.winfo_children():
            child.destroy()
        if not results:
            return

        header_font = ctk.CTkFont(size=12, weight="bold")
        ctk.CTkLabel(parent, text="", width=150).grid(row=0, column=0, padx=6, pady=4)
        for column, (name, _metrics) in enumerate(results, start=1):
            ctk.CTkLabel(
                parent, text=name, font=header_font, text_color=TEXT_PRIMARY,
                wraplength=210, justify="center",
            ).grid(row=0, column=column, padx=6, pady=4, sticky="ew")

        for row, (key, title) in enumerate(COMPARISON_ROWS, start=1):
            ctk.CTkLabel(
                parent, text=title, text_color=TEXT_SECONDARY, anchor="w",
            ).grid(row=row, column=0, padx=6, pady=3, sticky="w")
            for column, (_name, metrics) in enumerate(results, start=1):
                ctk.CTkLabel(
                    parent, text=_format_comparison_value(key, metrics),
                    text_color=TEXT_PRIMARY, anchor="center",
                ).grid(row=row, column=column, padx=6, pady=3, sticky="ew")

        for column in range(1, len(results) + 1):
            parent.grid_columnconfigure(column, weight=1)

    def _load_recent_files(self) -> list:
        """Kalıcı listeyi diskten okur; dosya yoksa/bozuksa boş liste döner
        (bu, ilk çalıştırma ya da elle silinmiş bir config dosyası için
        normal bir durum, hata sayılmaz). Her öğe {"path", "last_opened",
        "duration_s"} sözlüğü; eski sürümden kalma düz metin (sadece yol)
        girdileri de kabul edilir (kullanıcının diskindeki eski dosya
        otomatik olarak yeni formata göç eder)."""
        if not RECENT_FILES_PATH.exists():
            return []
        try:
            with open(RECENT_FILES_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        return [
            item if isinstance(item, dict) else {"path": item, "last_opened": None, "duration_s": None}
            for item in raw
        ]

    def _save_recent_files(self, entries: list):
        RECENT_FILES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RECENT_FILES_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

    def _add_recent_file(self, file_path: str, data: dict):
        """Başarıyla yüklenen dosyayı listenin başına taşır (zaten varsa
        eski konumundan çıkarıp), en fazla MAX_RECENT_FILES kadar tutar.
        Geçmiş Dosyalar listesinde gösterilecek 'ne zaman' ve 'ne kadar
        sürdü' bilgisi burada, dosya başarıyla parse edildiği anda kaydedilir."""
        entries = [e for e in self._load_recent_files() if e["path"] != file_path]
        entries.insert(0, {
            "path": file_path,
            "last_opened": datetime.now().isoformat(),
            "duration_s": _flight_duration_seconds(data.get("batteries", [])),
        })
        entries = entries[:MAX_RECENT_FILES]
        self._save_recent_files(entries)
        self._refresh_recent_menu(entries)
        self._refresh_recent_sidebar(entries)

    def _refresh_recent_menu(self, entries: list):
        """Açılır listenin gösterdiği etiketleri günceller. Aynı dosya adı
        birden fazla klasörden geldiyse (nadir ama olası) üst klasör adıyla
        ayırt edilir, aksi halde sadece dosya adı gösterilir."""
        if not entries:
            self._recent_label_to_path = {}
            self.recent_menu.configure(values=["(yok)"], state="disabled")
            self.recent_menu.set("Son Kullanılanlar")
            return

        name_counts = {}
        for entry in entries:
            name = Path(entry["path"]).name
            name_counts[name] = name_counts.get(name, 0) + 1

        label_to_path = {}
        labels = []
        for entry in entries:
            path_obj = Path(entry["path"])
            label = path_obj.name
            if name_counts[label] > 1:
                label = f"{label} ({path_obj.parent.name})"
            labels.append(label)
            label_to_path[label] = entry["path"]

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
        for key, title in STAT_TILE_DEFINITIONS:
            tile = ctk.CTkFrame(self.stats_row, fg_color=UI_GRIDLINE, corner_radius=8)
            tile.pack(side="left", padx=(0, 8), ipadx=12, ipady=8)

            # fill="x" + anchor="center": metin kutunun tam soluna değil,
            # ortasına doğru kayar (tile genişliği en uzun değere göre
            # otomatik ayarlanır, metin o genişlik içinde ortalanır).
            ctk.CTkLabel(
                tile, text=title, text_color=UI_TEXT_MUTED, font=ctk.CTkFont(size=11)
            ).pack(fill="x", anchor="center")
            value_label = ctk.CTkLabel(
                tile, text="—", text_color=UI_TEXT_PRIMARY, font=ctk.CTkFont(size=15, weight="bold")
            )
            value_label.pack(fill="x", anchor="center")
            self.stat_labels[key] = value_label

    def _build_warnings_area(self):
        """Backend'in kural tabanlı ürettiği uyarıları (ör. aşırı voltaj düşümü,
        motor akım dengesizliği) gösteren satır. Uyarı yoksa boş kalır, ekstra
        yer kaplamaz."""
        self.warnings_label = ctk.CTkLabel(
            self.analysis_frame, text="", text_color=UI_COLOR_WARNING, justify="left", anchor="w"
        )
        self.warnings_label.pack(side="top", fill="x", padx=16, pady=(0, 4))

    def _update_warnings(self, warnings: list):
        self._current_warnings = warnings  # PDF raporu ham listeye ihtiyaç duyuyor
        if not warnings:
            self.warnings_label.configure(text="")
            return
        self.warnings_label.configure(text="\n".join(f"⚠ {message}" for message in warnings))

    def _build_status_label(self):
        """Hata mesajları için ayrı bir durum satırı (dosya etiketiyle karışmasın diye)."""
        # wraplength: uzun backend hata mesajları (ör. system_power mesajı
        # birkaç cümle) pencere genişliğinde kesilmesin, alt satıra sarsın.
        self.status_label = ctk.CTkLabel(
            self.analysis_frame, text="", text_color=UI_COLOR_CRITICAL, justify="left", anchor="w",
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

        # Zoom/pan araç çubuğu: matplotlib'in hazır gelen NavigationToolbar2Tk'ı.
        # canvas'ın hemen üstüne, side="top" ile paketlenen son widget olarak
        # eklenir (analysis_frame'de bu noktaya kadar "top" dışında bir side
        # kullanılmadığı için mevcut düzeni bozmaz).
        nav_toolbar_frame = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
        nav_toolbar_frame.pack(side="top", fill="x", padx=16)
        self.nav_toolbar = _PanPreviewToolbar(self.canvas, nav_toolbar_frame)
        self.nav_toolbar.update()
        self._style_nav_toolbar()

        # FigureCanvasTkAgg'in altındaki ham Tk widget'ının arka planı varsayılan
        # olarak BEYAZ. Tema değişiminde (ör. açık→koyu) figür yeniden çizilene
        # kadar bu beyaz zemin bir an görünüp "ekran komple beyaz oldu" etkisine
        # yol açıyordu. Zemini SURFACE'e sabitleyince, çizim tamamlanana kadarki
        # o kısacık boşlukta bile doğru tema rengi görünür — beyaz parlama olmaz.
        self.canvas.get_tk_widget().configure(bg=SURFACE)
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True, padx=16, pady=(0, 16))

        # Hover tooltip: canvas bir kere oluşturulduğunda bağlanır (axes'ler
        # her ısı haritası/çizgi geçişinde yeniden yaratılsa da canvas hep
        # aynı kalır). _on_plot_hover, self.ax_... öz niteliklerini HER
        # ÇAĞRIDA canlı okur (closure'da önbelleklemez).
        self.canvas.mpl_connect("motion_notify_event", self._on_plot_hover)

    def _style_nav_toolbar(self):
        """NavigationToolbar2Tk varsayılan olarak açık gri bir Tk temasıyla
        geliyor; arka planları koyu temaya (SURFACE) çevirir ve ikonları
        yeniden mavi tonlarda çizdirir. CustomTkinter'ın arka planda SÜREKLİ
        çalışan DPI/ölçekleme döngüsü (`ScalingTracker.check_dpi_scaling`,
        kendini `after()` ile sonsuza kadar yeniden zamanlıyor) ara sıra bu
        özel (Tk sınıfı içine gömülü) butonların ikonunu kendi varsayılanına
        sıfırlıyor. Tek seferlik bir düzeltme yeterli olmadığı için bu metod
        da kendini periyodik olarak (1 saniyede bir) yeniden çağırıp ikonları
        sürekli doğru renkte tutuyor — maliyeti (birkaç configure() çağrısı)
        önemsiz. `finally` bloğu, gövdede beklenmedik bir hata (ör. bir
        widget'ın o an yok olması) çıksa bile yeniden zamanlamanın hep
        gerçekleşmesini garanti ediyor; aksi halde döngü sessizce durup bir
        daha hiç düzeltme yapmaz hale gelirdi."""
        if not self.winfo_exists():
            return
        try:
            toolbar = self.nav_toolbar
            toolbar.configure(background=SURFACE)
            for child in toolbar.winfo_children():
                try:
                    child.configure(background=SURFACE)
                except tk.TclError:
                    pass

            for button in toolbar._buttons.values():
                button.configure(
                    background=SURFACE, activebackground=GRIDLINE, highlightbackground=SURFACE,
                )
                if isinstance(button, tk.Checkbutton):
                    button.configure(selectcolor=GRIDLINE)
                if button not in (toolbar._buttons.get("Back"), toolbar._buttons.get("Forward")):
                    toolbar._set_image_for_button(button)
            toolbar.set_history_buttons()  # Geri/İleri'yi kendi (etkin/soluk) mantığıyla yeniden boyar

            toolbar._message_label.configure(background=SURFACE, foreground=TEXT_SECONDARY)
        finally:
            # id saklanır ki tema canlı değişiminde (bkz. _on_theme_toggle_click)
            # eski döngü iptal edilebilsin — yoksa her tema değişiminde bir
            # tane daha sonsuz self.after() zinciri birikir (self, yani App
            # penceresi, rebuild sırasında hiç yok edilmiyor).
            self._nav_toolbar_after_id = self.after(1000, self._style_nav_toolbar)

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

    def _on_plot_hover(self, event):
        """Fare grafik üzerindeyken en yakın örneğin zaman/değerini gösteren
        bir tooltip (annotation) çizer. ax_current/ax_motors her ısı haritası/
        çizgi geçişinde YENİDEN yaratıldığı için burada self.ax_voltage/
        ax_current/ax_motors HER ÇAĞRIDA canlı okunur, closure'da tutulmaz."""
        if self._hover_annotation is not None:
            self._hover_annotation.remove()
            self._hover_annotation = None

        ax = event.inaxes
        if ax not in (self.ax_voltage, self.ax_current, self.ax_motors) or event.xdata is None:
            self.canvas.draw_idle()
            return

        # Isı haritası modunda ilgili axes'in çizgisi olmadığından (get_lines()
        # boş) döngü hiçbir şey bulamaz ve tooltip doğal olarak gösterilmez.
        best = None
        for line in ax.get_lines():
            xdata, ydata = line.get_xdata(), line.get_ydata()
            if len(xdata) == 0:
                continue
            idx = int(np.searchsorted(xdata, event.xdata))
            for candidate in (idx - 1, idx):
                if 0 <= candidate < len(xdata):
                    distance = abs(ydata[candidate] - event.ydata)
                    if best is None or distance < best[0]:
                        best = (distance, line.get_label(), xdata[candidate], ydata[candidate])

        if best is None:
            self.canvas.draw_idle()
            return

        _, label, x, y = best
        unit = "V" if ax is self.ax_voltage else "A"

        # Nokta eksenin üst/sağ kenarına yakınsa tooltip'i o yöne doğru
        # değil, ters yöne (aşağı/sola) doğru aç — aksi halde etiket
        # figürün kenarından taşıp tamamen görünmez oluyordu (ör. "Batarya 1"
        # üst uçtaki bir değerin üzerindeyken hiç gözükmüyordu).
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        x_frac = (x - xlim[0]) / (xlim[1] - xlim[0]) if xlim[1] != xlim[0] else 0.5
        y_frac = (y - ylim[0]) / (ylim[1] - ylim[0]) if ylim[1] != ylim[0] else 0.5
        offset_x, ha = (-12, "right") if x_frac > 0.85 else (12, "left")
        offset_y, va = (-12, "top") if y_frac > 0.85 else (12, "bottom")

        self._hover_annotation = ax.annotate(
            f"{label}\n{x:.1f}s → {y:.2f}{unit}",
            xy=(x, y), xytext=(offset_x, offset_y), textcoords="offset points",
            ha=ha, va=va,
            bbox=dict(boxstyle="round", fc=SURFACE, ec=AXIS_LINE),
            color=TEXT_PRIMARY, fontsize=9,
        )
        self.canvas.draw_idle()

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
        hangisinden çağrılırsa çağrılsın analiz ekranına geçer.

        Backend çağrısı büyük log dosyalarında saniyeler sürebildiğinden ayrı
        bir thread'de çalıştırılır (yoksa Tk ana thread'i donar); thread'in
        sonucu bir Queue'ya konur, ana thread bunu after() ile periyodik
        kontrol eder (Tkinter widget'ları başka thread'den güvenle
        güncellenemez, bu yüzden çizim/etiket güncellemeleri hep ana
        thread'deki _poll_backend_result'ta yapılır)."""
        self._show_analysis()
        self._last_loaded_path = file_path
        # Tam yol yerine sadece dosya adı gösterilir: uzun mutlak yollar
        # toolbar'daki diğer butonları (ör. "Grafiği Kaydet") ekran dışına
        # itip kesilmelerine yol açıyordu.
        self.file_label.configure(text=Path(file_path).name)
        self.status_label.configure(text="İşleniyor...", text_color=UI_TEXT_SECONDARY)
        self.load_button.configure(state="disabled")
        self.recent_menu.configure(state="disabled")
        self.loading_progress.pack(side="left", padx=(12, 0))
        self.loading_progress.start()
        self._is_loading = True

        result_queue: queue.Queue = queue.Queue()

        def worker():
            try:
                result_queue.put(("ok", self._run_backend(file_path)))
            except Exception as error:
                result_queue.put(("error", error))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll_backend_result, file_path, result_queue)

    def _poll_backend_result(self, file_path: str, result_queue: "queue.Queue"):
        try:
            status, payload = result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_backend_result, file_path, result_queue)
            return

        self.loading_progress.stop()
        self.loading_progress.pack_forget()
        self.load_button.configure(state="normal")
        self._is_loading = False

        if status == "ok":
            data = payload
            self._plot_power_data(data)
            self.status_label.configure(text="")
            self._add_recent_file(file_path, data)  # kendi içinde recent_menu'yu yeniden etkinleştirir
        else:
            error = payload
            self.status_label.configure(text=f"⚠ Hata: {error}", text_color=UI_COLOR_CRITICAL)
            self._refresh_recent_menu(self._load_recent_files())  # doğru enabled/disabled durumunu geri yükle

    def _on_export_png_click(self):
        """Mevcut grafiği kullanıcının seçtiği bir PNG dosyasına kaydeder."""
        file_path = filedialog.asksaveasfilename(
            title="Grafiği Kaydet",
            defaultextension=".png",
            initialfile="analiz",
            filetypes=[("PNG görüntü", "*.png"), ("Tüm dosyalar", "*.*")],
        )
        if not file_path:
            return
        try:
            self.figure.savefig(file_path, facecolor=SURFACE)
            self.status_label.configure(text=f"Grafik kaydedildi: {file_path}", text_color=UI_TEXT_SECONDARY)
        except OSError as error:
            self.status_label.configure(text=f"⚠ Grafik kaydedilemedi: {error}", text_color=UI_COLOR_CRITICAL)

    def _on_export_csv_click(self):
        """Yüklü batarya/motor/PWM zaman serilerini ham CSV olarak kaydeder —
        Excel gibi başka araçlarla ileri analiz için. Her satır tipi kendi
        sütununu doldurur, diğerleri boş kalır (batarya voltaj+akım, motor
        sadece akım, pwm sadece pwm_us); "id" sütunu PWM satırlarında sayı
        değil kanal etiketidir ("MAIN 2"), çünkü kanalların anlamlı olan
        kimliği bu (bkz. shared/power_log_schema.md)."""
        file_path = filedialog.asksaveasfilename(
            title="CSV Dışa Aktar",
            defaultextension=".csv",
            initialfile="analiz",
            filetypes=[("CSV dosyası", "*.csv"), ("Tüm dosyalar", "*.*")],
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["tip", "id", "zaman_s", "voltaj_v", "akim_a", "pwm_us"])
                for battery in self._last_batteries or []:
                    for t, v, c in zip(battery["time_s"], battery["voltage_v"], battery["current_a"]):
                        writer.writerow(["batarya", battery["id"], t, v, c, ""])
                for motor in self._last_motors or []:
                    for t, c in zip(motor["time_s"], motor["current_a"]):
                        writer.writerow(["motor", motor["id"], t, "", c, ""])
                for output in self._last_pwm_outputs or []:
                    for t, pwm in zip(output["time_s"], output["pwm_us"]):
                        writer.writerow(["pwm", output["label"], t, "", "", pwm])
            self.status_label.configure(text=f"CSV kaydedildi: {file_path}", text_color=UI_TEXT_SECONDARY)
        except OSError as error:
            self.status_label.configure(text=f"⚠ CSV kaydedilemedi: {error}", text_color=UI_COLOR_CRITICAL)

    def _on_export_pdf_click(self):
        """İstatistikler + uyarılar + grafiği tek bir PDF dosyasına (2 sayfa)
        kaydeder. Ana grafik (self.figure) olduğu gibi kullanılır — sabit
        marj/gridspec kurallarına (bkz. _build_plot_area) hiç dokunulmaz;
        özet sayfası tamamen ayrı, bağımsız bir Figure'dür."""
        file_path = filedialog.asksaveasfilename(
            title="Uçuş Raporu Kaydet",
            defaultextension=".pdf",
            initialfile="analiz",
            filetypes=[("PDF belgesi", "*.pdf"), ("Tüm dosyalar", "*.*")],
        )
        if not file_path:
            return
        try:
            with PdfPages(file_path) as pdf:
                pdf.savefig(self._build_report_summary_figure(), facecolor=SURFACE)
                pdf.savefig(self.figure, facecolor=SURFACE)
            self.status_label.configure(text=f"Rapor kaydedildi: {file_path}", text_color=UI_TEXT_SECONDARY)
        except OSError as error:
            self.status_label.configure(text=f"⚠ Rapor kaydedilemedi: {error}", text_color=UI_COLOR_CRITICAL)

    def _build_report_summary_figure(self) -> Figure:
        """PDF raporunun ilk sayfası: dosya adı, istatistik kartları ve
        uyarılar metin olarak. A4 oranında, eksensiz (sadece fig.text) bir figür."""
        fig = Figure(figsize=(8.27, 11.69), dpi=100)  # A4
        fig.set_facecolor(SURFACE)
        fig.text(0.08, 0.93, "İHA Güç/Telemetri Uçuş Raporu", color=TEXT_PRIMARY,
                  fontsize=18, weight="bold")
        fig.text(0.08, 0.89, f"Dosya: {self.file_label.cget('text')}", color=TEXT_SECONDARY, fontsize=11)
        fig.text(0.08, 0.86, f"Oluşturulma: {datetime.now():%Y-%m-%d %H:%M}",
                  color=TEXT_SECONDARY, fontsize=11)

        y = 0.78
        for key, title in STAT_TILE_DEFINITIONS:
            fig.text(0.08, y, f"{title}: {self.stat_labels[key].cget('text')}", color=TEXT_PRIMARY, fontsize=12)
            y -= 0.04

        y -= 0.03
        fig.text(0.08, y, "Uyarılar:", color=TEXT_PRIMARY, fontsize=13, weight="bold")
        y -= 0.04
        if not self._current_warnings:
            fig.text(0.08, y, "Uyarı yok.", color=TEXT_SECONDARY, fontsize=11)
        else:
            for warning in self._current_warnings:
                fig.text(0.08, y, f"• {warning}", color=COLOR_WARNING, fontsize=11)
                y -= 0.035
        return fig

    def _on_clear_click(self):
        """Yüklü veriyi ve tüm panelleri başlangıç (boş) durumuna döndürür;
        ikinci bir dosyayı temiz bir ekrandan yüklemek isteyenler için."""
        self.file_label.configure(text="Henüz dosya seçilmedi.")
        self.status_label.configure(text="")
        self._update_warnings([])
        self._update_stats([])
        self._last_batteries = None
        self._last_motors = None
        self._last_pwm_outputs = None
        self._last_loaded_path = None

        self._plot_voltage_panel([])
        self._plot_battery_currents([])
        self._plot_motor_currents([])

        self.canvas.draw()

    def _run_backend(self, input_path: str) -> dict:
        """Backend'i seçilen log dosyasıyla çalıştırıp ürettiği JSON'u okur.

        Çıktı dosyası HER ÇAĞRI İÇİN AYRI bir geçici dizine yazılır. Sabit bir
        ad (eskiden "iha_power_log_output.json") kullanmak, aynı anda iki
        backend çağrısı olduğunda iki çağrının aynı dosyaya yazmasına yol
        açıyordu: uçuş karşılaştırma arka planda çalışırken ana pencereden bir
        dosya yüklemek yeterliydi. Sonuç ya yarım yazılmış dosyanın okunması
        (JSONDecodeError) ya da -- daha sinsisi -- bir logun verisinin öbürüne
        ait sanılmasıydı. (Aynı desen backend/tests/run_tests.py'de zaten
        böyleydi; uygulama kodu geride kalmıştı.)"""
        if not BACKEND_EXE.exists():
            raise RuntimeError(
                f"Backend derlenmemiş: {BACKEND_EXE} bulunamadı. Önce CMake ile derleyin."
            )

        thresholds = _get_general_thresholds()
        with tempfile.TemporaryDirectory(prefix="iha_power_log_") as tmp_dir:
            output_path = Path(tmp_dir) / "output.json"
            command = [
                str(BACKEND_EXE), input_path, str(output_path),
                f"--voltage-sag={thresholds['voltage_sag_threshold']}",
                f"--current-imbalance={thresholds['current_imbalance_threshold']}",
                f"--negative-current={thresholds['negative_current_threshold']}",
            ]
            # Araç tipine özel eşiklerin HEPSİ geçiriliyor: hangisinin geçerli
            # olduğuna backend karar veriyor, çünkü araç tipi ancak log
            # ayrıştırıldıktan sonra biliniyor. Böylece log tek geçişte okunuyor.
            for vehicle_type, values in sorted(_get_vehicle_threshold_overrides().items()):
                command.append(
                    f"--vehicle-thresholds={vehicle_type}"
                    f":{values['voltage_sag_threshold']}"
                    f":{values['current_imbalance_threshold']}"
                    f":{values['negative_current_threshold']}"
                )
            result = subprocess.run(command, capture_output=True, text=True)
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
        self._update_vehicle_label(data.get("meta", {}))
        self._update_stats(batteries)
        self._update_capacity_stats(batteries)
        self._update_warnings(data.get("warnings", []))

        self._plot_voltage_panel(batteries)

        self._last_batteries = batteries
        self._plot_battery_currents(batteries)

        self._last_motors = data.get("motors", [])
        self._last_pwm_outputs = data.get("pwm_outputs", [])
        self._plot_motor_currents(self._last_motors)

        self.canvas.draw()
        # Grafik son halini aldıktan SONRA "başa dön" görünümünü tazele; aksi
        # halde panelleri yeniden kuran nav_toolbar.update() çağrıları
        # navigasyon yığınını boş bırakıyor ve Sıfırla/Ana Sayfa butonları
        # (yığın boşken matplotlib sessizce hiçbir şey yapar) etkisiz kalıyor.
        self.nav_toolbar.push_current()

    def _update_vehicle_label(self, meta: dict):
        """Toolbar'daki dosya adının yanına araç tipini ekler ("ucus.ulg ·
        Rover"). Araç tipi SADECE bilgi amaçlı bir etiket — hangi panellerin
        çizileceğini belirlemez, o karar verinin gerçekten var olup olmadığına
        göre verilir (bkz. _has_current_data). Sebebi ölçülmüş bir gerçek:
        aynı araç tipinin ESC telemetrisi olan da olmayan da oluyor.

        Log araç tipini içermiyorsa ("unknown", ör. eski ArduPilot logları ya
        da sentetik test dosyaları) etiket eklenmez, sadece dosya adı kalır."""
        self._last_vehicle_type = meta.get("vehicle_type") or None
        label = VEHICLE_TYPE_LABELS.get(meta.get("vehicle_type", "unknown"))
        file_name = Path(self._last_loaded_path).name if self._last_loaded_path else ""
        self.file_label.configure(text=f"{file_name} · {label}" if label else file_name)

    def _plot_voltage_panel(self, batteries: list):
        """Üst paneli seçili görünüme (voltaj/sıcaklık) göre çizer. Akım/motor
        panellerinden farklı olarak ikisi de düz çizgi grafiği (heatmap yok),
        bu yüzden delaxes+add_subplot gerekmez — ax_voltage'ı temizleyip
        yeniden çizmek yeterli, sabit gridspec'e hiç dokunulmaz."""
        self.ax_voltage.clear()
        if self.voltage_view_mode == "temperature":
            self._plot_temperature_lines(batteries)
        else:
            self._plot_voltage_lines(batteries)

    def _plot_voltage_lines(self, batteries: list):
        self._style_axes(self.ax_voltage, "Voltaj (V)")

        voltage_sag_threshold = _get_warning_thresholds(
            self._last_vehicle_type)["voltage_sag_threshold"]
        for i, battery in enumerate(batteries):
            color, linestyle = _series_style(i)
            self.ax_voltage.plot(
                battery["time_s"], battery["voltage_v"], color=color, linestyle=linestyle,
                linewidth=2, label=f"Batarya {battery['id']}",
            )
            if battery["voltage_v"]:
                _draw_voltage_sag_line(self.ax_voltage, battery["voltage_v"][0], voltage_sag_threshold, color)

        if len(batteries) > 1:
            self.ax_voltage.legend(
                loc="upper right", facecolor=SURFACE, edgecolor=AXIS_LINE,
                labelcolor=TEXT_SECONDARY, fontsize=9,
            )

    def _plot_temperature_lines(self, batteries: list):
        """Batarya sıcaklığı (varsa) çizer. Log bu alanı hiç içermiyorsa
        temperature_c boş dizidir; ayrıca bazı batarya izleyicileri alanı
        tanımlayıp hiç doldurmaz (her örnek 0.0) — bu da gerçek bir ölçüm
        değil, sensör bağlı olmadığının işareti sayılır, bu yüzden o batarya
        da 'veri yok' muamelesi görür. Hiçbir batarya için kullanılabilir
        veri yoksa açık bir durum mesajı gösterilir (sessiz boş grafik yerine)."""
        self._style_axes(self.ax_voltage, "Sıcaklık (°C)")

        plotted = 0
        for i, battery in enumerate(batteries):
            temperature_c = battery.get("temperature_c") or []
            if not temperature_c or all(t == 0 for t in temperature_c):
                continue
            color, linestyle = _series_style(i)
            self.ax_voltage.plot(
                battery["time_s"], temperature_c, color=color, linestyle=linestyle,
                linewidth=2, label=f"Batarya {battery['id']}",
            )
            plotted += 1

        if plotted > 1:
            self.ax_voltage.legend(
                loc="upper right", facecolor=SURFACE, edgecolor=AXIS_LINE,
                labelcolor=TEXT_SECONDARY, fontsize=9,
            )
        elif plotted == 0:
            self.ax_voltage.text(
                0.5, 0.5, "Bu logda sıcaklık verisi yok.", transform=self.ax_voltage.transAxes,
                ha="center", va="center", color=TEXT_MUTED,
            )

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
        # Zoom/pan geçmiş yığını eski (silinen) axes'e referans tutabilir;
        # yeni axes'le senkron kalması için sıfırlanır. Bekleyen bir pan
        # önizlemesi varsa da (bkz. _PanPreviewToolbar) eski axes'e işaret
        # etmemesi için temizlenir.
        self.nav_toolbar.update()
        self.nav_toolbar._restore_pan_view_if_pending()

        if self.battery_view_mode == "heatmap":
            self._plot_battery_heatmap(batteries)
        else:
            self._plot_battery_lines(batteries)

    def _plot_battery_lines(self, batteries: list):
        """Her bataryanın (busbar'ın) toplam akımını kendi renginde çizer.
        Akım sensörü olmayan bataryalar atlanır; hiçbirinde ölçüm yoksa düz
        bir sıfır çizgisi yerine açık bir durum mesajı gösterilir."""
        self._style_axes(self.ax_current, "Toplam Akım (A)")

        plotted = 0
        for i, battery in enumerate(batteries):
            if not _has_current_data(battery):
                continue
            # Renk indeksi olarak orijinal sıra (i) kullanılıyor, çizilen
            # serinin sırası değil: bir batarya atlansa bile kalanların rengi
            # voltaj panelindeki aynı bataryanın rengiyle eşleşmeye devam etsin.
            color, linestyle = _series_style(i)
            self.ax_current.plot(
                battery["time_s"], battery["current_a"], color=color, linestyle=linestyle,
                linewidth=2, label=f"Batarya {battery['id']}",
            )
            plotted += 1

        if plotted == 0:
            self.ax_current.text(
                0.5, 0.5, NO_BATTERY_CURRENT_MESSAGE, transform=self.ax_current.transAxes,
                ha="center", va="center", color=TEXT_MUTED,
            )
            return

        if plotted > 1:
            self.ax_current.legend(
                loc="upper right", facecolor=SURFACE, edgecolor=AXIS_LINE,
                labelcolor=TEXT_SECONDARY, fontsize=9,
            )
        means = [
            sum(b["current_a"]) / len(b["current_a"])
            for b in _with_current_data(batteries) if b["current_a"]
        ]
        _draw_imbalance_band(
            self.ax_current, means,
            _get_warning_thresholds(self._last_vehicle_type)["current_imbalance_threshold"],
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

        # Ölçümü olmayan bataryalar ısı haritasında tamamen tek renk bir şerit
        # olarak çıkardı ("hep aynı yük" gibi görünüyordu); çizgi grafiğiyle
        # aynı şekilde süzülüp, hiçbiri kalmazsa mesaj gösteriliyor.
        batteries = _with_current_data(batteries)
        if not batteries:
            self.ax_current.text(
                0.5, 0.5, NO_BATTERY_CURRENT_MESSAGE, transform=self.ax_current.transAxes,
                ha="center", va="center", color=TEXT_MUTED,
            )
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
        # Zoom/pan geçmiş yığını eski (silinen) axes'e referans tutabilir;
        # yeni axes'le senkron kalması için sıfırlanır. Bekleyen bir pan
        # önizlemesi varsa da (bkz. _PanPreviewToolbar) eski axes'e işaret
        # etmemesi için temizlenir.
        self.nav_toolbar.update()
        self.nav_toolbar._restore_pan_view_if_pending()

        if self.motor_view_mode == "pwm":
            # PWM, motor akımından bağımsız bir seri; parametreden değil
            # saklanan son yüklemeden okunur (bkz. _build_motor_view_toggle).
            self._plot_pwm_lines(self._last_pwm_outputs or [])
        elif self.motor_view_mode == "pwm_deviation":
            self._plot_pwm_deviation_heatmap(self._last_pwm_outputs or [])
        elif self.motor_view_mode == "heatmap":
            self._plot_motor_heatmap(motors)
        else:
            self._plot_motor_lines(motors)

    def _plot_motor_lines(self, motors: list):
        """Her motorun akımını kendi renginde çizer; kullanılabilir ölçüm
        yoksa (rover/sabit kanat loglarının çoğunda esc_status hiç yok) panel
        sessizce boş kalmak yerine nedenini yazar."""
        self._style_axes(self.ax_motors, "Motor Akımı (A)")
        self.ax_motors.set_xlabel("Zaman (s)", color=TEXT_SECONDARY)

        plotted = 0
        for i, motor in enumerate(motors):
            if not _has_current_data(motor):
                continue
            color, linestyle = _series_style(i)
            self.ax_motors.plot(
                motor["time_s"], motor["current_a"], color=color, linestyle=linestyle,
                linewidth=2, label=f"Motor {motor['id']}",
            )
            plotted += 1

        if plotted == 0:
            self.ax_motors.text(
                0.5, 0.5, _motor_empty_message(motors), transform=self.ax_motors.transAxes,
                ha="center", va="center", color=TEXT_MUTED,
            )
            return

        self.ax_motors.legend(
            loc="upper right", facecolor=SURFACE, edgecolor=AXIS_LINE,
            labelcolor=TEXT_SECONDARY, fontsize=9,
        )
        means = [
            sum(m["current_a"]) / len(m["current_a"])
            for m in _with_current_data(motors) if m["current_a"]
        ]
        _draw_imbalance_band(
            self.ax_motors, means,
            _get_warning_thresholds(self._last_vehicle_type)["current_imbalance_threshold"],
        )

    def _plot_pwm_lines(self, pwm_outputs: list):
        """Uçuş kontrolcüsünün çıkış kanallarının PWM darbe genişliğini çizer.

        Bu bir GÜÇ ölçümü değil, kontrol çıktısı — akım sensörü olmayan
        araçlarda motor aktivitesinin tek görünür kanıtı. Kanal etiketleri
        ("MAIN 2", "AUX 1", "Kanal 3") backend'de üretilir; hangi kanalın
        motor hangisinin servo/direksiyon olduğu loglarda yazmadığı için
        burada da tahmin yürütülmez, kanallar oldukları adla gösterilir.

        Dengesizlik bandı (_draw_imbalance_band) bilerek çizilmez: o kural
        akım içindir, farklı işlevlerdeki PWM kanallarını birbiriyle
        kıyaslamak anlamsız olurdu."""
        self._style_axes(self.ax_motors, "Çıkış PWM (µs)")
        self.ax_motors.set_xlabel("Zaman (s)", color=TEXT_SECONDARY)

        for i, output in enumerate(pwm_outputs):
            color, linestyle = _series_style(i)
            self.ax_motors.plot(
                output["time_s"], output["pwm_us"], color=color, linestyle=linestyle,
                linewidth=2, label=output["label"],
            )

        if not pwm_outputs:
            self.ax_motors.text(
                0.5, 0.5, NO_PWM_DATA_MESSAGE, transform=self.ax_motors.transAxes,
                ha="center", va="center", color=TEXT_MUTED,
            )
            return

        self.ax_motors.legend(
            loc="upper right", facecolor=SURFACE, edgecolor=AXIS_LINE,
            labelcolor=TEXT_SECONDARY, fontsize=9, ncol=2 if len(pwm_outputs) > 4 else 1,
        )

    def _plot_pwm_deviation_heatmap(self, pwm_outputs: list):
        """PWM kanallarının, AYNI çıkış rayındaki diğer kanalların o andaki
        ortalamasından farkını ısı haritası olarak çizer.

        Neden mutlak PWM değil: gerçek loglarla denendi ve mutlak skala
        okunaksız çıktı. Aynı haritada hem motorlar (uçuş boyunca ~1000-1750
        arasında, birbirine çok yakın) hem de servo/gimbal kanalları (900-2100,
        çoğu zaman uçta sabit) bulunuyor; servolar skalayı domine edip
        motorlar arasındaki asıl bilgiyi -- birinin diğerlerinden fazla
        çalışması -- tek düze bir renge çeviriyordu. Kanal başına normalize
        etmek de işe yaramadı (motorlar zaten senkron hareket ettiği için
        hepsi aynı desene dönüşüyor). Kasıtlı olarak +120us kaydırılmış bir
        motorla test edildiğinde sadece bu sapma görünümü dengesizliği açıkça
        gösterdi.

        Gruplama, backend'in ürettiği etiketin ilk kelimesine ("MAIN", "AUX",
        "Kanal") göre yapılıyor — bunlar donanımdaki ayrı çıkış rayları, yani
        "hangi kanal motor" tahmini YAPILMIYOR (bkz. shared/power_log_schema.md).
        Sadece aynı raydaki kanallar birbiriyle karşılaştırılıyor. Tek kanallı
        bir grupta karşılaştırma anlamsız olduğundan o satır nötr (0) kalır."""
        self._style_axes(self.ax_motors, "PWM Sapması (µs)")
        self.ax_motors.set_xlabel("Zaman (s)", color=TEXT_SECONDARY)
        self.ax_motors.grid(False)  # ısı haritasında gridline gürültü yapar

        if not pwm_outputs:
            self.ax_motors.text(
                0.5, 0.5, NO_PWM_DATA_MESSAGE, transform=self.ax_motors.transAxes,
                ha="center", va="center", color=TEXT_MUTED,
            )
            return

        all_times = sorted({t for output in pwm_outputs for t in output["time_s"]})
        grid = np.array([
            np.interp(all_times, output["time_s"], output["pwm_us"])
            for output in pwm_outputs
        ])

        groups = {}
        for index, output in enumerate(pwm_outputs):
            groups.setdefault(output["label"].rsplit(" ", 1)[0], []).append(index)

        deviation = np.zeros_like(grid)
        for rows in groups.values():
            if len(rows) < 2:
                continue
            block = grid[rows]
            deviation[rows] = block - block.mean(axis=0)

        # Renk sınırı simetrik: 0 (sapma yok) her zaman skalanın tam ortasındaki
        # nötr griye denk gelmeli, yoksa "az sapma" ile "hiç sapma" karışır.
        limit = max(float(np.abs(deviation).max()), 1.0)
        cmap = LinearSegmentedColormap.from_list("pwm_deviation", PWM_DEVIATION_COLORS)
        image = self.ax_motors.imshow(
            deviation, aspect="auto", origin="lower", cmap=cmap,
            vmin=-limit, vmax=limit,
            extent=[all_times[0], all_times[-1], 0.5, len(pwm_outputs) + 0.5],
        )
        self.ax_motors.set_yticks(range(1, len(pwm_outputs) + 1))
        self.ax_motors.set_yticklabels([output["label"] for output in pwm_outputs])

        self._motors_cax.axis("on")
        self._motor_colorbar = self.figure.colorbar(image, cax=self._motors_cax)
        self._motor_colorbar.set_label("Ray ortalamasından fark (µs)", color=TEXT_SECONDARY)
        self._motor_colorbar.ax.tick_params(colors=TEXT_MUTED)

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

        # Çizgi görünümüyle aynı süzme: akım bildirmeyen ESC'ler ısı
        # haritasında düz tek renk bir satır olarak yanıltıcı görünüyordu.
        measured_motors = _with_current_data(motors)
        if not measured_motors:
            self.ax_motors.text(
                0.5, 0.5, _motor_empty_message(motors), transform=self.ax_motors.transAxes,
                ha="center", va="center", color=TEXT_MUTED,
            )
            return
        motors = measured_motors

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
        total_samples = sum(len(battery["time_s"]) for battery in batteries)
        duration = _flight_duration_seconds(batteries)

        if duration is None:
            for label in self.stat_labels.values():
                label.configure(text="—", text_color=UI_TEXT_PRIMARY)
            return

        self.stat_labels["duration"].configure(text=f"{duration:.1f} s")
        self.stat_labels["samples"].configure(text=str(total_samples))
        self.stat_labels["voltage_range"].configure(
            text=f"{min(all_voltage):.2f}–{max(all_voltage):.2f} V"
        )

        # Akıma dayanan dört kutucuk (aralık, enerji, tepe güç, iç direnç)
        # sadece gerçekten ölçüm yapan bataryalardan hesaplanır. Akım sensörü
        # olmayan bir araçta (birçok rover) bunları 0 göstermek "hiç akım
        # çekilmemiş / 0 Wh harcanmış" gibi okunuyordu; ölçüm yoksa "—" hem
        # dürüst hem de kapasite kutucuklarıyla tutarlı.
        measured = _with_current_data(batteries)
        if not measured:
            for key in ("current_range", "energy_wh", "peak_power_w", "resistance_est"):
                self.stat_labels[key].configure(text="—", text_color=UI_TEXT_PRIMARY)
            return

        # Negatif akım fiziksel olarak beklenmez (bkz. backend'in "veri
        # kalitesi" uyarısı); kartın rengini de uyarı rengine çevirmek bu
        # durumu grafiğe bakmadan da fark ettirir.
        all_current = [c for battery in measured for c in battery["current_a"]]
        min_current = min(all_current)
        self.stat_labels["current_range"].configure(
            text=f"{min_current:.1f}–{max(all_current):.1f} A",
            text_color=UI_COLOR_WARNING if min_current < 0 else UI_TEXT_PRIMARY,
        )

        self.stat_labels["energy_wh"].configure(text=f"{_battery_energy_wh(measured):.1f} Wh")
        self.stat_labels["peak_power_w"].configure(text=f"{_battery_peak_power_w(measured):.0f} W")

        resistance = _battery_internal_resistance_estimate(measured)
        if resistance is None:
            self.stat_labels["resistance_est"].configure(text="—")
        else:
            battery_id, resistance_mohm = resistance
            self.stat_labels["resistance_est"].configure(text=f"B{battery_id}: {resistance_mohm:.0f} mΩ")

    def _update_capacity_stats(self, batteries: list):
        """Backend'in (varsa) ürettiği capacity_used_mah/remaining_pct
        alanlarından tüketilen kapasite ve kaba bir kalan uçuş süresi tahmini
        gösterir. Log formatı bu alanları içermiyorsa (eski ArduPilot logları,
        ya da remaining yayınlamayan PX4 araçları) kutucuklar sessizce "—"
        gösterir — bu bir hata değil, alanın loglarda opsiyonel olmasındandır.

        Bu iki alan akım ölçümünün zaman integralinden türetilir; akım sensörü
        yoksa uçuş kontrolcüsü alanı yine doldurur ama değer anlamsızdır
        (gerçek rover logunda 90 saniyelik sürüş sonunda 0.01 mAh tüketim ve
        sabit %100 kalan). Bu yüzden hesap sadece ölçümü olan bataryalarla
        yapılır — akıma dayanan diğer kutucuklarla (bkz. _update_stats) aynı
        kural."""
        measured = _with_current_data(batteries)
        capacities = [b["capacity_used_mah"] for b in measured if b.get("capacity_used_mah") is not None]
        self.stat_labels["capacity_used"].configure(
            text=f"{sum(capacities):.0f} mAh" if capacities else "—"
        )

        remaining_pcts = [b["remaining_pct"] for b in measured if b.get("remaining_pct") is not None]
        duration = _flight_duration_seconds(batteries)
        remaining_text = "—"
        if remaining_pcts and duration:
            # En az kalanı olan batarya belirleyici (en kritik senaryo).
            remaining_pct = min(remaining_pcts)
            if remaining_pct < 100:
                # Sabit ortalama tüketim hızı varsayımıyla kaba doğrusal
                # ekstrapolasyon: şu ana kadar geçen sürede bu kadar % tüketildi,
                # aynı hızla devam edilirse kalan % ne kadar sürede biter.
                remaining_min = duration * remaining_pct / (100 - remaining_pct) / 60
                remaining_text = f"~{remaining_min:.0f} dk"
        self.stat_labels["remaining_time"].configure(text=remaining_text)


if __name__ == "__main__":
    app = App()
    app.mainloop()
