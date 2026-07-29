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
import re
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
    # Seri (batarya/motor/PWM kanalı) renkleri — kategori kimliği. Koyu
    # zeminde parlaklığı yüksek tonlar; açık temada bunlar soluk kaldığı
    # için LIGHT_PALETTE kendi (daha koyu/doygun) sürümünü taşır. Slot
    # sırası iki temada da AYNI varlığa denk gelir (Batarya 1 hep mavi).
    "SERIES_COLORS": (
        "#3987e5", "#008300", "#d55181", "#c98500",
        "#8a5fd1", "#1fada4", "#e0574a", "#b8b83c",
    ),
    # Isı haritası ardışık skalası: koyu yüzeyden markaya ait maviden (seri
    # slot 1 ile aynı ton) açık uca — düşük değer yüzeyde erir, yüksek değer
    # parlar.
    "HEATMAP_COLORS": ("#141a22", "#3987e5", "#cde2fb"),
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
    # Koyu temadaki tonların açık zemin (#f4f5f2) için koyulaştırılmış/
    # doygunlaştırılmış halleri — koyu paletteki değerler açık zeminde soluk
    # kalıp birbirine karışıyordu (kullanıcı geri bildirimi). Slot başına ton
    # kimliği korunur (Batarya 1 iki temada da mavi). Değerler ölçülerek
    # seçildi: her renk zemine karşı yeterli kontrast taşıyor ve ardışık
    # çiftler renk körlüğü simülasyonunda da ayrışıyor; 7. (kırmızı) bilerek
    # daha koyu, 8. (zeytin) bilerek daha açık — naif koyulaştırılmış çift
    # protanopide birbirinin aynısı çıkıyordu.
    "SERIES_COLORS": (
        "#2a78d6", "#006f00", "#c13d6c", "#9c6a00",
        "#6d44b8", "#147d77", "#b03225", "#8f9422",
    ),
    # Açık zeminde yön ters (PWM_DEVIATION_COLORS'daki açık-tema notuyla aynı
    # mantık): düşük uç zemine karışacak kadar açık, yüksek uç koyulaşarak
    # öne çıkar — "düşük değer erir, yüksek değer belirginleşir" okuması
    # iki temada da korunur.
    "HEATMAP_COLORS": ("#dce8f7", "#2a78d6", "#0d366b"),
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
SERIES_COLORS = _ACTIVE_PALETTE["SERIES_COLORS"]
HEATMAP_COLORS = _ACTIVE_PALETTE["HEATMAP_COLORS"]

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
LANDING_FOOTER_TEXT = "#3a5a78"      # alt bilgi satırı ("... MIT Lisansı ile açık kaynak ...") — bilerek
                                     # soluk: sayfanın en az önemli metni, dikkat çekmemeli
LANDING_SIDEBAR_WIDTH = 290          # _build_recent_sidebar'daki sabit genişlik; başlık/açıklama
                                     # metinlerinin kalan alana göre ne zaman sarması gerektiğini
                                     # hesaplayabilmek için burada da adlandırılmış halde tutuluyor
# "Uçuşları Karşılaştır" penceresi SADECE giriş ekranından açılıyor ve giriş
# ekranı temadan bağımsız hep koyu lacivert — dialog aktif temayı takip
# edince açık temada "koyu zemin üstünde beyaz pencere" gibi kopuk duruyordu
# (kullanıcı geri bildirimi). Dialog bu yüzden LANDING paletiyle çiziliyor.
LANDING_ROW_STRIPE = "#0f2236"       # karşılaştırma tablosunun zebra bandı: LANDING_BG ile
                                     # LANDING_CARD arasında bir ara ton
LANDING_WARNING = DARK_PALETTE["COLOR_WARNING"]    # koyu zeminde okunan uyarı/kritik tonları —
LANDING_CRITICAL = DARK_PALETTE["COLOR_CRITICAL"]  # temalı singles açık temada laciverte karşı
                                                   # okunaksız kalabilirdi, sabitlere bağlandı

# Birden fazla batarya/motor olduğunda her birine sabit sırada, kategorik bir
# renk atamak için (kategori kimliği). Bir bataryanın voltaj ve akım çizgisi
# iki farklı panelde de AYNI rengi taşır ki paneller arasında göz takibiyle
# eşleştirilebilsin. 8 renk hexa/octokopterin tüm motorlarını (6/8) tek
# döngüde ayrı renkte tutmaya yetiyor; bunun ötesine geçilirse (nadir) renk
# döngüsü _series_style()'daki çizgi stiliyle birlikte tekrar başa sarılır.
#
# SERIES_COLORS ve HEATMAP_COLORS artık TEMA BAŞINA paletlerde tanımlı
# (yukarıda, PWM_DEVIATION_COLORS deseniyle) ve tekil sabitler olarak burada
# değil, modül singles bloğunda unpack ediliyor; tema toggle'ı ikisini de
# yeniden bağlıyor. Çizgi stilleri tema bağımsız, paylaşımlı kalır.
SERIES_LINESTYLES = ["-", "--", ":", "-."]


def _series_style(index: int) -> tuple:
    """index'e göre (renk, çizgi_stili) döner; SERIES_COLORS tükenirse
    çizgi stili değişerek seriler yine ayırt edilebilir kalır."""
    color = SERIES_COLORS[index % len(SERIES_COLORS)]
    linestyle = SERIES_LINESTYLES[(index // len(SERIES_COLORS)) % len(SERIES_LINESTYLES)]
    return color, linestyle

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

# Kutucuk başına, fare üzerine gelince gösterilen kısa açıklama. Ayrı bir
# sözlük (STAT_TILE_DEFINITIONS'a üçüncü öğe olarak eklemek yerine): tanım
# listesini PDF raporu da paylaşıyor, oradaki tüketiciyi değiştirmemek için.
# "B2: 39 mΩ" gibi kısaltmalı değerlerin ne anlama geldiğini yeni kullanıcıya
# anlatan tek yer burası (kullanıcı geri bildirimi).
STAT_TILE_TOOLTIPS = {
    "duration": "Kaydın ilk ve son örneği arasında geçen süre.",
    "samples": "Tüm bataryaların toplam ölçüm (örnek) sayısı.",
    "voltage_range": "Tüm bataryaların ortak en düşük–en yüksek voltajı.",
    "current_range": "Ölçüm yapan bataryaların ortak akım aralığı; negatif değer\n"
                     "genellikle sensör gürültüsüne işaret eder (⚠ ile işaretlenir).",
    "energy_wh": "Voltaj × akım eğrisinin zaman integrali —\nuçuş boyunca çekilen toplam enerji.",
    "peak_power_w": "Anlık voltaj × akım çarpımının en yüksek değeri.",
    "resistance_est": "Voltaj düşümü / akım ilişkisinden (regresyon eğimi) tahmin\n"
                      "edilen iç direnç; B = batarya numarası. Uçuştan uçuşa\n"
                      "artış, yaşlanan bataryaya işaret eder.",
    "capacity_used": "Uçuş kontrolcüsünün raporladığı tüketilen kapasite\n(akım integralinden).",
    "remaining_time": "Kalan % ve şu ana kadarki tüketim hızından kaba tahmin;\n"
                      "uçuş geneli ile son bölümün hızları farklıysa aralık gösterilir.",
}


# Akım ölçümü olmayan panellerde, sessiz/boş bir grafik yerine gösterilen
# açıklamalar (sıcaklık panelindeki "Bu logda sıcaklık verisi yok." ile aynı
# desen). Bir rover/sabit kanat logunda akım sensörü çoğu zaman hiç bağlı
# değildir; bunu sıfır çizgisi olarak çizmek "hiç akım çekilmemiş" gibi
# yanlış bir izlenim veriyordu.
NO_BATTERY_CURRENT_MESSAGE = "Bu logda akım sensörü verisi yok."
NO_MOTOR_TOPIC_MESSAGE = "Bu logda motor (ESC) akım verisi yok."
NO_MOTOR_CURRENT_MESSAGE = "ESC telemetrisi var ama akım bildirilmiyor."
NO_PWM_DATA_MESSAGE = "Bu logda PWM çıkış verisi yok."
# Backend artık batarya (BAT/CURR) hiç yoksa da motor/PWM varsa logu kabul
# ediyor (bkz. gerçek bir ArduPilot QuadPlane SITL logu, data/ArduPlane-
# FlyEachFrame-00000182.BIN) — bu durumda voltaj paneli boş kalıp hiçbir
# açıklama göstermiyordu, diğer "veri yok" panelleriyle tutarsızdı.
NO_BATTERY_MESSAGE = "Bu logda batarya verisi yok."

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


# Üst araç çubuğu yerleşimi (bkz. _build_toolbar / _layout_toolbar).
TOOLBAR_GAP = 12  # bileşenler arası boşluk (px)
# Dosya adı etiketine tek satır modunda ayrılan asgari yer. Tek satırın
# sığıp sığmadığına bu sabitle karar veriliyor; etiketin gerçek genişliğiyle
# karar verilseydi uzun bir dosya adı toolbar'ı gereksiz yere ikinci satıra
# sarardı (etiket zaten kısaltılabiliyor, butonlar kısaltılamıyor).
TOOLBAR_FILE_LABEL_MIN_WIDTH = 120
# Üst istatistik satırındaki kutucuklar arası boşluk (bkz. _layout_stats_row).
STATS_TILE_GAP = 8

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


def _normalize_time_axis(data: dict) -> dict:
    """Tüm zaman serilerini (batarya/motor/PWM) ortak en erken damgaya göre
    kaydırıp ekseni 0'dan başlatır.

    Neden: PX4 zaman damgaları kontrolcünün AÇILIŞINDAN itibaren sayılıyor;
    px4_hexarotor_flight.ulg'de ilk örnek 1453.5 s'de. Süre kartı ilk-son
    farkını (doğru: 90.0 s) gösterirken grafik ekseni mutlak değerleri
    (1450-1545) gösteriyordu — ikisi tutarsız görünüyor ve "regresyon mu var?"
    sorusuna yol açıyordu. Kaydırma TEK ortak t0 ile yapılır ki seriler arası
    hizalama (ör. akım çekimi ile PWM hareketinin örtüşmesi) bozulmasın;
    süre/enerji/kapasite gibi tüm istatistikler zaman FARKLARINA dayandığı
    için kaydırmadan etkilenmez. Backend JSON'ına dokunulmaz (şemadaki
    time_s mutlak kalır); bu sadece görüntüleme katmanının normalizasyonu."""
    series_keys = ("batteries", "motors", "pwm_outputs")
    starts = [series["time_s"][0]
              for key in series_keys
              for series in data.get(key, []) if series.get("time_s")]
    if not starts:
        return data
    t0 = min(starts)
    if t0 <= 0:
        return data
    for key in series_keys:
        for series in data.get(key, []):
            series["time_s"] = [t - t0 for t in series["time_s"]]
    return data


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


def _remaining_time_range_min(duration_s, remaining_pct, current_a):
    """Tahmini kalan uçuş süresi için (alt, üst) dakika aralığı; hesap
    yapılamıyorsa None. Tek sayı ("~5 dk") tahmin belirsizliğini gizleyip
    olduğundan iddialı duruyordu (kullanıcı geri bildirimi) — aralık daha
    dürüst.

    İki tahmin üretilir:
    - Taban: mevcut doğrusal ekstrapolasyon (uçuş genelinin ortalama tüketim
      hızı sabit varsayılır) — eski tek-değerli formülün kendisi.
    - İkinci: son 1/3 pencerenin ortalama akımı uçuş genelinden farklıysa,
      taban o oranla ölçeklenir (son bölümde çekiş yüksekse kalan süre
      kısalır: base * genel / son).
    İkisi %10 içinde ise (ya da ikinci tahmin hesaplanamıyorsa) aralık
    üretmek sahte hassasiyet olurdu — tek değer (lo == hi) döner."""
    if remaining_pct is None or remaining_pct >= 100 or not duration_s or duration_s <= 0:
        return None
    base_min = duration_s * remaining_pct / (100 - remaining_pct) / 60

    recent_start = len(current_a) - len(current_a) // 3
    recent = current_a[recent_start:]
    # 10 örnekten kısa pencere gürültüye açık; ortalamalardan biri <= 0 ise
    # oran anlamsız (akım sensörü olmayan/duran araç) — tek değere düş.
    if len(recent) < 10:
        return (base_min, base_min)
    overall_mean = sum(current_a) / len(current_a)
    recent_mean = sum(recent) / len(recent)
    if overall_mean <= 0 or recent_mean <= 0:
        return (base_min, base_min)

    scaled_min = base_min * overall_mean / recent_mean
    lo, hi = sorted((base_min, scaled_min))
    if hi <= lo * 1.10:
        return (base_min, base_min)
    return (lo, hi)


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
        # Batarya var ama hiçbirinde gerçek akım verisi yoksa True (rover
        # durumu) — karşılaştırma tablosunda "—" hücrelerine, sebebi akım
        # sensörü eksikliği olduğunda açıklayıcı tooltip eklemek için.
        "current_data_missing": bool(batteries) and not measured,
    }


def _format_comparison_value(key: str, metrics: dict) -> str:
    """Bir özet metriğini tabloda gösterilecek metne çevirir; ölçülememiş
    değerler "—" olur (bkz. _flight_summary_metrics)."""
    if key == "voltage_range":
        low, high = metrics["voltage_min"], metrics["voltage_max"]
        return f"{low:.2f}–{high:.2f} V" if low is not None else "—"
    if key == "current_range":
        low, high = metrics["current_min"], metrics["current_max"]
        if low is None:
            return "—"
        # Ana ekrandaki Akım Aralığı kartıyla aynı işaret: negatif minimum
        # muhtemelen sensör gürültüsü, tabloda da görünür olmalı.
        return ("⚠ " if low < 0 else "") + f"{low:.1f}–{high:.1f} A"

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


# Backend'in tüm uyarı cümleleri "Batarya N: ..." ya da "Motor N: ..." ile
# başlıyor (computeWarnings şablonları) ve grafik serilerinin etiketleri de
# birebir "Batarya N"/"Motor N". Bu önek, bir uyarıyı grafikteki serisine
# bağlamamızı sağlıyor (uyarıya tıkla -> seri vurgulansın).
WARNING_TARGET_RE = re.compile(r"^(Batarya|Motor) (\d+): ")


def _series_highlight_alpha(highlight, label: str, panel_labels: list) -> float:
    """Bir serinin çizim alfası: vurgu yoksa ya da bu seri vurgunun hedefiyse
    tam opak, hedefin PANELİNDEKİ diğer seriler soluk. Hedef bu panelde hiç
    yoksa (ör. "Motor 5" vurgusu voltaj panelinde) kimse soluklaştırılmaz —
    yabancı bir hedef tüm paneli söndürmemeli."""
    if highlight is None:
        return 1.0
    target_label = f"{highlight[0]} {highlight[1]}"
    if target_label not in panel_labels:
        return 1.0
    return 1.0 if label == target_label else 0.15


def _middle_ellipsis(text: str, max_chars: int = 28) -> str:
    """Uzun dosya adını ortadan kısaltır ("çokUzunBirAd…0067.BIN") — uzantı
    ve numara kuyruğu görünür kalır ki benzer adlı loglar ayırt edilebilsin.
    Karşılaştırma tablosu başlıkları için: sarmak (wraplength) başlıkları iki
    satıra taşırıp tabloyu dalgalandırıyordu."""
    if len(text) <= max_chars:
        return text
    head = max_chars - 11  # 10 karakter kuyruk + 1 "…"
    return text[:head] + "…" + text[-10:]


def _comparison_has_mixed_vehicles(results: list) -> bool:
    """Karşılaştırılan uçuşların araç tipleri farklı mı? (results:
    (ad, metrikler) listesi.) Tipi bilinmeyenler (None) sayılmaz — "unknown
    + multirotor" karışık sayılıp yanlış alarm vermesin."""
    types = {metrics.get("vehicle") for _name, metrics in results}
    types.discard(None)
    return len(types) > 1


# Dikkat çekici değer vurgusunda hangi metriklerin "yüksekliği kötüdür"
# anlamı taşıdığı: iç direnç (yaşlanan batarya) ve voltaj düşümü. Diğer
# satırlar (tepe güç, enerji...) araç sınıfına göre doğal olarak farklıdır,
# onları vurgulamak yanlış "sorun" iması olur.
COMPARISON_NOTABLE_KEYS = ("resistance_mohm", "voltage_sag_pct")

# _format_comparison_value'nun "—" bastığı satırlardan hangileri akım
# sensörü eksikliğinden kaynaklanıyor (bkz. current_data_missing) — sadece
# bu satırlarda dash'e açıklayıcı tooltip eklenir; "vehicle"/"warning_count"
# gibi başka None sebepleri buna dahil değil.
COMPARISON_CURRENT_DEPENDENT_KEYS = (
    "current_range", "energy_wh", "peak_power_w", "capacity_used_mah", "resistance_mohm",
)
NO_CURRENT_DATA_TOOLTIP = "— : bu araç için akım verisi log'da bulunamadı."


def _comparison_notable_cells(results: list) -> list:
    """Tablodaki dikkat çekici hücreleri bulur: [(metrik_anahtarı,
    sütun_indeksi, uçuş_adı), ...]. Kural bilinçli muhafazakâr: bir uçuşun
    değeri, DİĞERLERİNİN medyanının en az 2 KATIysa işaretlenir (ör. 39 mΩ
    vs 10-14 mΩ). Ölçülmüş bir eşik değil ama "belirgin farklı" için makul
    ve açıklanabilir bir oran; 1.5x gibi sınır durumlar bilerek işaretlenmez
    (yanlış alarm, vurgunun değerini düşürür)."""
    notable = []
    for key in COMPARISON_NOTABLE_KEYS:
        values = [(i, m.get(key)) for i, (_n, m) in enumerate(results)]
        present = [(i, v) for i, v in values if v is not None]
        if len(present) < 2:
            continue
        max_index, max_value = max(present, key=lambda pair: pair[1])
        others = sorted(v for i, v in present if i != max_index)
        median = others[len(others) // 2]
        if median > 0 and max_value >= 2 * median:
            notable.append((key, max_index, results[max_index][0]))
    return notable


def _overlay_battery_for_flight(batteries: list) -> dict:
    """Karşılaştırma dialogundaki voltaj overlay grafiği için, bir uçuşun
    bataryalarından TEK birini seçer (ilk voltaj örneği olan). 5 uçuş × N
    batarya çizmek okunmaz olurdu; uçuş başına tek çizgi grafiği tablo gibi
    ölçekten bağımsız okunabilir tutuyor. Hiçbir bataryada voltaj örneği
    yoksa None döner (çağıran o uçuşu atlar)."""
    for battery in batteries:
        if battery.get("voltage_v"):
            return battery
    return None


def _draw_voltage_sag_line(ax, first_voltage: float, threshold: float, color: str,
                           alpha_factor: float = 1.0):
    """Backend'in voltaj düşümü kuralıyla (ilk örneğe göre %eşik düşüş) aynı
    hesapla, o bataryanın kendi rengiyle kesikli bir eşik çizgisi çizer —
    sadece çizgi modunda anlamlı, ısı haritasında çağrılmaz.

    Label kasıtlı olarak "_" ile başlıyor: hem matplotlib'in legend()'ı
    otomatik dışlasın diye (bkz. az aşağıdaki batarya/motor çizgilerinin
    "Batarya N"/"Motor N" etiketleri), hem de get_lines()'ı gerçek veri
    sayısıyla karşılaştıran testler bu çizgiyi ayırt edip filtreleyebilsin diye.

    alpha_factor: seri vurgulama (bkz. _series_highlight_alpha) soluklaştırılan
    bataryanın eşik çizgisini de aynı oranda soluklaştırsın diye."""
    if first_voltage <= 0:
        return
    ax.axhline(
        first_voltage * (1 - threshold), color=color, linestyle="--", linewidth=1,
        alpha=0.5 * alpha_factor,
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
    # Bandın ne olduğu etiketsiz belirsizdi (kullanıcı geri bildirimi) — tek
    # satır açıklama, panel köşesinde, ölçeği/veriyi etkilemeyen transAxes'te.
    ax.text(
        0.99, 0.02, f"bant: ortalama ±%{threshold * 100:.0f} dengesizlik eşiği",
        transform=ax.transAxes, ha="right", va="bottom", fontsize=7, color=TEXT_MUTED,
    )


def _draw_negative_current_threshold(ax, series: list, threshold: float):
    """Negatif akım eşiğini kesikli bir çizgiyle görünür kılar — eskiden
    eşik sadece stat kartındaki "⚠" önekiyle vardı, grafikte hiç işaretli
    değildi. Gerçekten negatif örnek YOKSA çizilmez: her panelde sabit bir
    çizgi, çoğu uçuşta (negatif akım nadir) gereksiz gürültü olurdu.
    label alttan çizgi konvansiyonuyla aynı ("_" ile başlıyor -> legend'e
    girmiyor, bkz. _voltage_sag_threshold)."""
    has_negative = any(
        c < 0 for item in _with_current_data(series) for c in item["current_a"]
    )
    if not has_negative:
        return
    ax.axhline(
        threshold, color=COLOR_WARNING, linestyle="--", linewidth=1, alpha=0.5,
        label="_negative_current_threshold",
    )


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


def _make_format_coord(ylabel: str):
    """Toolbar'ın varsayılan "(x, y) = (...)" okunuşu yerine Türkçe, birimli
    bir metin üretir. ylabel zaten doğru birimi taşıyor (_style_axes'e
    geçirilen "Voltaj (V)", "Sıcaklık (°C)" gibi değerler), bu yüzden ayrı
    bir birim eşlemesi kurmaya gerek yok."""
    def format_coord(x, y):
        return f"Zaman: {x:.2f}s, {ylabel}: {y:.2f}"
    return format_coord


def _add_toolbar_tooltip(widget, text):
    """Bir araç çubuğu butonuna, fare üzerine gelince çıkan küçük bir ipucu
    balonu bağlar. `text` bir metin ya da metin döndüren bir fonksiyon
    olabilir (fonksiyon, kısaltılmış dosya adı gibi değişen içerikler için).

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
        content = text() if callable(text) else text
        if not content:  # dinamik ipucu boşsa balon hiç açılmasın
            return
        window = tk.Toplevel(widget)
        window.wm_overrideredirect(True)  # çerçevesiz, başlıksız balon
        window.wm_geometry(
            f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + widget.winfo_height() + 4}"
        )
        tk.Label(
            window, text=content, background=GRIDLINE, foreground=TEXT_PRIMARY,
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


def _add_tile_tooltip(tile, text):
    """Bir istatistik kutucuğuna (çerçeve + içindeki başlık/değer etiketleri)
    ipucu balonu bağlar. _add_toolbar_tooltip tek widget'a bağlanıyor; kutucuk
    üç widget'tan oluştuğu için imleç çerçeveden etikete geçerken Enter/Leave
    olayları art arda tetikleniyor. Balonu hangi widget açtıysa açsın konum
    hep ÇERÇEVEYE göre hesaplanıyor ve Leave yalnızca imleç kutucuğun sınırları
    tamamen dışına çıkınca kapatıyor — böylece balon etiket geçişlerinde
    titremiyor."""
    state = {"window": None}

    def show(_event=None):
        if state["window"] is not None:
            return
        window = tk.Toplevel(tile)
        window.wm_overrideredirect(True)
        window.wm_geometry(
            f"+{tile.winfo_rootx()}+{tile.winfo_rooty() + tile.winfo_height() + 4}"
        )
        tk.Label(
            window, text=text, background=GRIDLINE, foreground=TEXT_PRIMARY,
            relief="solid", borderwidth=1, padx=6, pady=3, justify="left",
        ).pack()
        state["window"] = window

    def hide_if_outside(_event=None):
        if state["window"] is None:
            return
        x, y = tile.winfo_pointerxy()
        inside = (tile.winfo_rootx() <= x < tile.winfo_rootx() + tile.winfo_width()
                  and tile.winfo_rooty() <= y < tile.winfo_rooty() + tile.winfo_height())
        if not inside:
            state["window"].destroy()
            state["window"] = None

    for widget in (tile, *tile.winfo_children()):
        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide_if_outside, add="+")
        widget.bind("<ButtonPress>", hide_if_outside, add="+")


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

        # Boyut göz kararı değil ÖLÇÜMLE seçildi: matplotlib komşu ikonlarının
        # (back/forward/move/zoom/filesave) 26x26'ya ölçeklenmiş dolu-piksel
        # (ink) kutuları 21x22 ile 26x25 arasında, ortalama ~23x23. Bu
        # parametrelerle sıfırla'nın ink kutusu 23x22 çıkıyor — ortalamaya en
        # yakın aday. (Önceki iki deneme göz kararıydı ve ikisi de ıskaladı:
        # ilk hali 19x19'du "büyük görünüyor" şikayeti aldı, küçültülünce
        # 17x16'ya düşüp bu kez gerçekten küçük kaldı.)
        margin, thickness = 10, 9
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
        half_width, tip_length = 15.0, 26.0
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
        # Tıklanmış uyarının hedef serisi ("Motor", 5) — grafiklerde o seri
        # vurgulanır, panelindeki diğerleri soluklaşır (bkz. _on_warning_click).
        self._highlight_series = None
        self._hover_annotation = None  # grafik üzerindeki hover tooltip'i (bkz. _on_plot_hover)
        # Isı haritası modunda imlecin altındaki HÜCREYİ bulabilmek için gereken
        # veri (satır etiketleri + ortak zaman ekseni + değer biçimleyici).
        # Çizgi görünümünde None olur; o zaman hover, eksendeki çizgilerden
        # okunur (bkz. _on_plot_hover).
        self._current_heatmap = None
        self._motors_heatmap = None
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

        # Footer, "center" çerçevesinden ÖNCE side="bottom" ile pack ediliyor.
        # Sıra önemli: pack cavity'yi çağrı sırasına göre bölüyor; footer
        # center'dan SONRA eklenirse (center zaten expand=True ile kalan tüm
        # genişliği aldıktan sonra), footer'a ayrılan pay center'ın genişliğini
        # de daraltıyor (ölçüldü: center 700px pencerede 410 yerine 200px
        # kalıyordu, başlık/açıklama bu yüzden sidebar'ın altına gömülüp
        # sağdan taşıyordu). Önce ayrılırsa footer tüm genişlikte bir şerit
        # alır, center kalan yüksekliğin TAMAMINI (ve doğru genişliği) alır.
        self.landing_footer_label = ctk.CTkLabel(
            self.landing_frame,
            text=f"İHA Güç/Telemetri Log Analiz Aracı · MIT Lisansı ile açık kaynak · v{APP_VERSION}",
            text_color=LANDING_FOOTER_TEXT, font=ctk.CTkFont(size=11), justify="center",
        )
        self.landing_footer_label.pack(side="bottom", pady=12)

        center = ctk.CTkFrame(self.landing_frame, fg_color="transparent")
        center.pack(side="left", fill="both", expand=True)

        # İçerik "center"ın tam ortasına place() ile konumlandırılıyor (sabit
        # pady değerleriyle üste yaslamak yerine). place, relx/rely oranlarını
        # kullandığı için pencere büyütülüp küçültüldükçe (ör. IDE'nin yanında
        # daraltılmış bir pencerede) içerik her zaman "center" alanının tam
        # ortasında kalacak şekilde otomatik yeniden hesaplanır.
        content_block = ctk.CTkFrame(center, fg_color="transparent")
        content_block.place(relx=0.5, rely=0.5, anchor="center")

        # Logo en üstte: önceki sürümde başlığın altındaydı, üstte olması
        # bir "marka" imzası gibi okunuyor (bkz. kullanıcı geri bildirimi).
        # Aynı vektörel drone ikonu kullanılıyor, sadece daha küçük — artık
        # sayfanın odak noktası değil, başlığın üstündeki bir işaret.
        icon_canvas = Canvas(content_block, width=190, height=146, bg=LANDING_BG, highlightthickness=0)
        icon_canvas.pack(pady=(0, 12))
        self._draw_drone_icon(icon_canvas, scale=0.86)

        self.landing_title_label = ctk.CTkLabel(
            content_block, text="İHA Güç/Telemetri Analiz", text_color=LANDING_TEXT,
            font=ctk.CTkFont(size=32, weight="bold"), justify="center",
        )
        self.landing_title_label.pack(pady=(0, 8))
        self.landing_desc_label = ctk.CTkLabel(
            content_block, text="Uçuş logunu yükleyip güç/telemetri analizine başla",
            text_color=LANDING_ACCENT_BRIGHT, font=ctk.CTkFont(size=14), justify="center",
        )
        self.landing_desc_label.pack(pady=(0, 28))

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
            # text_color eskiden TEXT_MUTED'du (tema değişince renk
            # değiştiren tekil sabit); açık temada LANDING_BG'nin (her
            # zaman koyu lacivert) üzerinde neredeyse hiç kontrastı
            # kalmıyordu — LANDING_TEXT_SECONDARY diğer landing metinleri
            # gibi bu zemin için sabit/güvenli. height de diğer butonlardan
            # (48px) belirgin küçük bir tıklama hedefiydi, büyütüldü.
            sample_link = ctk.CTkButton(
                content_block, text="veya örnek veri ile dene →", command=self._on_try_sample_click,
                fg_color="transparent", hover_color=LANDING_BG, text_color=LANDING_TEXT_SECONDARY,
                font=ctk.CTkFont(size=12, underline=True), width=0, height=32,
            )
            sample_link.pack()
            sample_link.bind("<Enter>", lambda _e: sample_link.configure(text_color=LANDING_ACCENT_BRIGHT))
            sample_link.bind("<Leave>", lambda _e: sample_link.configure(text_color=LANDING_TEXT_SECONDARY))

        self.landing_frame.bind("<Configure>", self._on_landing_frame_configure)

    def _on_landing_frame_configure(self, event=None):
        """content_block, sidebar'dan arta kalan alanın (relx=0.5 ile) tam
        ortasına yerleşiyor. Pencere `minsize` kadar (700px) daraldığında
        başlık/açıklama kenarlardan taşabiliyordu; wraplength'i anchor
        noktasının HER İKİ yanındaki en dar mesafenin iki katına göre
        hesaplamak, relx değişse bile metni taşmadan sığdırır. Footer tüm
        pencere genişliğinde, ayrı hesaplanır."""
        frame_width = event.width if event is not None else self.landing_frame.winfo_width()
        center_width = max(1, frame_width - LANDING_SIDEBAR_WIDTH)
        anchor_x = center_width * 0.5
        fit_width = 2 * min(anchor_x, center_width - anchor_x) - 24
        content_wraplength = max(160, int(fit_width))
        self.landing_title_label.configure(wraplength=content_wraplength)
        self.landing_desc_label.configure(wraplength=content_wraplength)
        self.landing_footer_label.configure(wraplength=max(160, frame_width - 32))

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
        sidebar = ctk.CTkFrame(self.landing_frame, fg_color="#0c1e30", corner_radius=0,
                                width=LANDING_SIDEBAR_WIDTH)
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
            canvas.create_polygon(
                self._rounded_rect_points(0, 0, width - 1, card_height - 1, radius=10),
                smooth=True, fill=state["color"], outline=LANDING_CARD_BORDER, width=1,
            )
            badge_y = card_height / 2
            canvas.create_polygon(
                self._rounded_rect_points(
                    10, badge_y - badge_h / 2, 10 + badge_w, badge_y + badge_h / 2, radius=6
                ),
                smooth=True, fill=badge_color, outline="",
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

    @staticmethod
    def _rounded_rect_points(x0: float, y0: float, x1: float, y1: float, radius: float,
                              segments_per_corner: int = 6) -> list:
        """Yuvarlak köşeli bir dikdörtgenin çokgen noktalarını üretir.

        Canvas'ın yerleşik bir "rounded rectangle" ilkeli yok; geçmiş dosya
        kartları ve rozeti eskiden `create_rectangle` ile keskin köşeli
        çiziliyordu — oysa hero buton/format pilleri/stat kutucukları hep
        yuvarlak (`corner_radius`). Dört köşeye çeyrek daire yayı yerleştirip
        `create_polygon(..., smooth=True)` ile birleştirmek aynı görünümü
        veriyor; yöntem `_blade_polygon`'daki yerel açı tarama + döndürme
        tekniğiyle aynı aileden."""
        radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
        corners = (
            (x1 - radius, y0 + radius, 270, 360),  # sağ üst
            (x1 - radius, y1 - radius, 0, 90),     # sağ alt
            (x0 + radius, y1 - radius, 90, 180),   # sol alt
            (x0 + radius, y0 + radius, 180, 270),  # sol üst
        )
        points = []
        for cx, cy, start_deg, end_deg in corners:
            for i in range(segments_per_corner + 1):
                angle = math.radians(start_deg + (end_deg - start_deg) * i / segments_per_corner)
                points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        return points

    @staticmethod
    def _blade_polygon(cx: float, cy: float, angle_deg: float, length: float, width: float,
                        points: int = 14) -> list:
        """Bir pervane bıçağını (uzun, ince bir elips) çokgen noktaları olarak
        üretir — Canvas'ın döndürülmüş elips çizme yeteneği yok, bu yüzden
        yerel eksende bir elips üretilip döndürülüyor. Aynı yöntem uygulama
        ikonunu üreten `scripts/generate_icon.py`'deki `_draw_rotor` ile
        birebir aynı; ikisi tutarlı görünsün diye bilerek aynı teknik."""
        angle = math.radians(angle_deg)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        result = []
        for i in range(points):
            t = 2 * math.pi * i / points
            ex, ey = length * math.cos(t), width * math.sin(t)
            result.append((cx + ex * cos_a - ey * sin_a, cy + ex * sin_a + ey * cos_a))
        return result

    def _draw_drone_icon(self, canvas: Canvas, scale: float = 1.0):
        """Uygulama ikonuyla (frontend/assets/icon.png, bkz.
        scripts/generate_icon.py) aynı görsel dile sahip bir quadcopter çizer:
        dolgulu X biçimli pervane bıçakları, ince koyu kollar, dikey oval
        gövde. Canvas ilkelleriyle (çokgen + daire) oluşturulmuş vektörel bir
        siluet, bir fotoğraf DEĞİL — telif riski yok, her boyutta net görünür.

        `scale`, aynı çizimi giriş ekranının üstündeki küçük bir logo olarak
        da kullanabilmek için (bkz. `_build_landing_screen`) tüm boyutları
        orantılı küçültüp büyütüyor."""
        cx, cy = 110 * scale, 85 * scale
        # Kanvas (190x146 @ scale=0.86) genişten kısa; pervaneler dikeyde
        # yataydan daha az açılıyor ki ikon dikdörtgen çerçeveye tam otursun.
        rotor_offset_x, rotor_offset_y = 74 * scale, 42 * scale
        rotor_r = 20 * scale
        arm_w = max(2, round(7 * scale))
        rotor_outline_w = max(1, round(2.5 * scale))
        body_outline_w = max(1, round(2.5 * scale))

        directions = ((-1, -1), (1, -1), (-1, 1), (1, 1))
        rotor_centers = [
            (cx + dx * rotor_offset_x, cy + dy * rotor_offset_y) for dx, dy in directions
        ]

        # Kollar: koyu, gövdenin arkasında kalacak şekilde önce çizilir.
        # LANDING_BADGE_ULOG (koyu lacivert) burada da kullanılıyor — hem
        # kollar hem format rozeti için "vurgudan koyu ama zeminden açık" aynı
        # renk ihtiyacı var.
        for ex, ey in rotor_centers:
            canvas.create_line(
                cx, cy, ex, ey, fill=LANDING_BADGE_ULOG, width=arm_w, capstyle="round",
            )

        for ex, ey in rotor_centers:
            canvas.create_oval(
                ex - rotor_r, ey - rotor_r, ex + rotor_r, ey + rotor_r,
                outline=LANDING_ACCENT_BRIGHT, width=rotor_outline_w,
            )
            for angle in (45, 135):
                canvas.create_polygon(
                    self._blade_polygon(ex, ey, angle, rotor_r * 0.78, rotor_r * 0.24),
                    fill=LANDING_ACCENT_BRIGHT,
                )
            hub = rotor_r * 0.22
            canvas.create_oval(
                ex - hub, ey - hub, ex + hub, ey + hub, fill=LANDING_BG, outline="",
            )

        # Gövde: dikey oval (kollardan sonra çizilir ki giriş noktaları temiz
        # görünsün), uygulama ikonundaki gövdeyle aynı oran.
        body_w, body_h = 17 * scale, 30 * scale
        canvas.create_oval(
            cx - body_w, cy - body_h, cx + body_w, cy + body_h,
            fill=LANDING_CARD, outline=LANDING_ACCENT_BRIGHT, width=body_outline_w,
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
        kullanılan dosyalar açılır listesi ve seçilen dosya etiketi.

        Yerleşim ÜÇ PARÇA halinde kuruluyor: sol grup (dosya işlemleri), dosya
        adı etiketi ve sağ grup (dışa aktarma/ayarlar). Butonlar doğrudan
        toolbar'a değil kendi grup çerçevelerine konuyor, çünkü dar ekranda
        sağ grup ikinci satıra iniyor (bkz. _layout_toolbar) — Tk'da bir
        widget'ın ebeveyni sonradan değiştirilemediği için taşınan şey butonlar
        değil, grup çerçevesinin ızgaradaki yeri oluyor."""
        toolbar = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
        toolbar.pack(side="top", fill="x", padx=16, pady=(16, 8))
        self.toolbar = toolbar

        left_group = ctk.CTkFrame(toolbar, fg_color="transparent")
        right_group = ctk.CTkFrame(toolbar, fg_color="transparent")
        self._toolbar_left_group = left_group
        self._toolbar_right_group = right_group

        self.home_button = ctk.CTkButton(
            left_group, text="← Ana Sayfa", fg_color="transparent", border_width=1,
            border_color=UI_AXIS_LINE, text_color=UI_TEXT_SECONDARY, command=self._show_landing,
        )
        self.home_button.pack(side="left", padx=(0, TOOLBAR_GAP))

        self.load_button = ctk.CTkButton(
            left_group, text="Log Dosyası Yükle (Ctrl+O)", command=self._on_load_file_click
        )
        self.load_button.pack(side="left", padx=(0, TOOLBAR_GAP))

        self._recent_label_to_path = {}
        self.recent_menu = ctk.CTkOptionMenu(
            left_group, values=["(yok)"], command=self._on_recent_file_selected, width=200,
            anchor="center",
        )
        self.recent_menu.pack(side="left")
        self._refresh_recent_menu(self._load_recent_files())

        # Backend arka planda çalışırken gösterilir (bkz. _load_file);
        # varsayılan olarak paketlenmez, sadece yükleme sırasında görünür.
        self.loading_progress = ctk.CTkProgressBar(
            left_group, mode="indeterminate", width=120
        )

        # Uzunluğu dosya adına bağlı olan TEK bileşen bu; ölçüldüğünde uzun bir
        # ad 409px istiyor ve sağdaki butonları ekran dışına itiyordu. Bu yüzden
        # etiket kalan boşluğa kısaltılarak sığdırılıyor (bkz. _fit_file_label),
        # tam metin ise fare ipucunda duruyor.
        self.file_label = ctk.CTkLabel(
            toolbar, text="", text_color=UI_TEXT_SECONDARY, anchor="w",
        )
        self._file_label_text = "Henüz dosya seçilmedi."
        self._file_label_fit_key = None
        _add_toolbar_tooltip(
            self.file_label,
            # Ancak kısaltılmışsa ipucu göster: tam metin zaten görünüyorsa
            # balon gereksiz gürültü olurdu.
            lambda: self._file_label_text if self.file_label.cget("text") != self._file_label_text else "",
        )

        self.clear_button = ctk.CTkButton(
            right_group, text="Temizle", fg_color="transparent", border_width=1,
            border_color=UI_AXIS_LINE, text_color=UI_TEXT_SECONDARY, command=self._on_clear_click,
        )
        self.clear_button.pack(side="left", padx=(0, TOOLBAR_GAP))

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
            right_group, values=list(EXPORT_MENU_ACTIONS), command=self._on_export_selected,
            width=150, anchor="center",
        )
        self.export_menu.set(EXPORT_MENU_LABEL)
        self.export_menu.pack(side="left", padx=(0, TOOLBAR_GAP))

        self.settings_button = ctk.CTkButton(
            right_group, text="⚙ Ayarlar", fg_color="transparent", border_width=1,
            border_color=UI_AXIS_LINE, text_color=UI_TEXT_SECONDARY, command=self._on_settings_click,
        )
        self.settings_button.pack(side="left", padx=(0, TOOLBAR_GAP))

        self.theme_button = ctk.CTkButton(
            right_group, text=("☀ Açık Tema" if ACTIVE_THEME == "dark" else "🌙 Koyu Tema"),
            fg_color="transparent", border_width=1, border_color=UI_AXIS_LINE,
            text_color=UI_TEXT_SECONDARY, command=self._on_theme_toggle_click,
        )
        self.theme_button.pack(side="left")

        self._toolbar_two_rows = None  # henüz yerleşmedi; _layout_toolbar dolduracak
        self._layout_toolbar(two_rows=False)
        toolbar.bind("<Configure>", self._on_toolbar_configure)

    def _layout_toolbar(self, two_rows: bool, width: int = None):
        """Grupları ızgaraya yerleştirir. `two_rows` ise sağ grup ikinci
        satıra iner; boşluğu her zaman dosya adı sütunu yutar, böylece sağ
        grup tek satır modunda sağ kenara yaslanır. `width` verilmezse
        toolbar'ın o anki genişliği kullanılır (bkz. _toolbar_width)."""
        if self._toolbar_two_rows == two_rows:
            return
        self._toolbar_two_rows = two_rows

        self._toolbar_left_group.grid(row=0, column=0, sticky="w")
        self.file_label.grid(row=0, column=1, sticky="ew", padx=(TOOLBAR_GAP, TOOLBAR_GAP))
        self._toolbar_left_group.master.columnconfigure(1, weight=1)

        if two_rows:
            # Sağ grup satırın tamamını kaplasın ki sütun genişlikleri üstteki
            # satırın butonlarıyla hizalanmaya zorlanmasın.
            self._toolbar_right_group.grid(
                row=1, column=0, columnspan=3, sticky="w", pady=(TOOLBAR_GAP, 0)
            )
        else:
            self._toolbar_right_group.grid(row=0, column=2, sticky="e", pady=0)

        self._fit_file_label(width)

    def _toolbar_single_row_width(self) -> int:
        """Tek satır yerleşimin istediği genişlik. Dosya adı için sabit bir
        alt sınır kullanılıyor (etiketin GERÇEK genişliği değil): aksi halde
        sırf uzun bir dosya adı yüzünden toolbar ikinci satıra sarardı."""
        return (
            self._toolbar_left_group.winfo_reqwidth()
            + self._toolbar_right_group.winfo_reqwidth()
            + TOOLBAR_FILE_LABEL_MIN_WIDTH
            + 2 * TOOLBAR_GAP
        )

    def _toolbar_width(self, event=None) -> int:
        """Toolbar'ın GÜNCEL genişliği.

        `<Configure>` işlenirken `winfo_width()` hâlâ ESKİ genişliği döndürüyor;
        yeni genişlik yalnızca olay nesnesinde var. Bu gözden kaçtığında dosya
        adı etiketi bir adım geriden geliyordu — pencere genişletilse bile
        etiket dar hâline göre kısaltılmış kalıyordu."""
        return event.width if event is not None else self.toolbar.winfo_width()

    def _on_toolbar_configure(self, event=None):
        """Pencere yeniden boyutlandıkça yerleşimi seçer.

        Karar HER ZAMAN tek satır ihtiyacına göre veriliyor; iki satır modunun
        (daha dar olan) kendi ihtiyacına bakılsaydı yerleşim iki mod arasında
        salınırdı."""
        width = self._toolbar_width(event)
        self._layout_toolbar(two_rows=width < self._toolbar_single_row_width(), width=width)
        self._fit_file_label(width)

    def _refresh_toolbar_layout(self):
        """Bir bileşen gösterilip gizlendikten sonra yerleşimi yeniden hesaplar.

        `after_idle` şart: `pack()`/`pack_forget()` hemen etkili olmuyor, Tk
        geometri hesabını boşta kalınca yapıyor. Hemen okunduğunda grup
        `winfo_reqwidth()`'i ESKİ değerini veriyordu — yükleme bittikten sonra
        dosya adı etiketi, artık görünmeyen ilerleme çubuğunun 132 pikselini
        hâlâ ayırıyor ve gereksiz yere kısa kalıyordu."""
        self.after_idle(self._on_toolbar_configure)

    def _set_file_label(self, text: str):
        """Dosya adı etiketinin TAM metnini saklar ve görünen kısmı sığdırır.
        Tam metin ayrıca saklanıyor çünkü PDF raporu dosya adını buradan
        okuyor — kısaltılmış hali rapora düşmemeli."""
        self._file_label_text = text
        self._file_label_fit_key = None  # metin değişti, yeniden ölç
        self._fit_file_label()

    def _fit_file_label(self, width: int = None):
        """Etiketi, sol/sağ grupların artığı kadar boşluğa kısaltarak sığdırır."""
        if width is None:
            width = self.toolbar.winfo_width()
        available = width - self._toolbar_left_group.winfo_reqwidth() - 2 * TOOLBAR_GAP
        if not self._toolbar_two_rows:
            available -= self._toolbar_right_group.winfo_reqwidth() + TOOLBAR_GAP

        key = (self._file_label_text, available)
        if self._file_label_fit_key == key:
            return  # her <Configure> olayında yeniden ölçmemek için
        self._file_label_fit_key = key
        self.file_label.configure(
            text=_truncate_to_width(self._file_label_text, available, self.file_label.cget("font"))
        )

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
        global SERIES_COLORS, HEATMAP_COLORS

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
        SERIES_COLORS = palette["SERIES_COLORS"]
        HEATMAP_COLORS = palette["HEATMAP_COLORS"]

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
        # 400 yükseklik içeriğe yetmiyordu (ölçüldü: gerçek içerik yüksekliği
        # 466px, eşik alanlarının GRIDLINE çerçeveye alınmasından sonra daha
        # da arttı) — buton satırı kırpılıyordu. 480 rahat pay bırakıyor.
        dialog.geometry("400x480")
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
        # 3 eşik alanı, üstteki "Hangi araç için?" seçicisinden görsel olarak
        # ayrılsın diye tonlu bir çerçeveye alınıyor (stat kutucuklarıyla aynı
        # GRIDLINE dili, bkz. _build_stats_row) — aksi halde hepsi tek bir düz
        # yığın gibi görünüp seçicinin "üçünü de etkileyen" bir kapsam kontrolü
        # olduğu görsel olarak belli olmuyordu.
        thresholds_group = ctk.CTkFrame(dialog, fg_color=GRIDLINE, corner_radius=8)
        thresholds_group.pack(fill="x", padx=20, pady=(4, 8))
        for key, label in fields:
            row = ctk.CTkFrame(thresholds_group, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=6)
            ctk.CTkLabel(row, text=label, text_color=TEXT_SECONDARY, anchor="w").pack(side="top", fill="x")
            entry = ctk.CTkEntry(row)
            entry.pack(side="top", fill="x", pady=(4, 0))
            entries[key] = entry

        hint_label = ctk.CTkLabel(dialog, text="", text_color=TEXT_MUTED, font=ctk.CTkFont(size=11))
        hint_label.pack(padx=20, pady=(2, 0))

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
        error_label.pack(padx=20, pady=(4, 0))

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

        # Dialog LANDING paletiyle çiziliyor (temayı takip ETMİYOR): sadece
        # hep-koyu giriş ekranından açılıyor; açık temada ctk varsayılanları
        # "koyu zemin üstünde beyaz pencere" gibi kopuk duruyordu (kullanıcı
        # geri bildirimi, bkz. LANDING_ROW_STRIPE'ın yorumu).
        dialog = ctk.CTkToplevel(self, fg_color=LANDING_BG)
        dialog.title("Uçuşları Karşılaştır")
        # Tablo altına voltaj overlay grafiği eklendiğinde eski 920x720
        # yetersiz kaldı (grafik result_frame'in dışına taşıp kırpılıyordu).
        dialog.geometry("960x860")
        dialog.transient(self)
        dialog.grab_set()  # Ayarlar dialoguyla aynı: açıkken ana pencere
        # etkileşilebilir kalmasın (aksi halde tema değiştirilebiliyordu ve
        # bu dialogdaki renkler açılış anındaki modül sabitleri olduğu için
        # -- UI_* çiftleri değil -- yerinde güncellenmeyip donuk kalıyordu).

        ctk.CTkLabel(
            dialog, text="Karşılaştırılacak uçuşları seç", text_color=LANDING_TEXT,
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
                text_color=LANDING_TEXT, fg_color=LANDING_ACCENT,
                hover_color=LANDING_ACCENT, border_color=LANDING_CARD_BORDER,
                checkmark_color=LANDING_TEXT,
            ).pack(anchor="w", pady=3)
            checkboxes.append((entry["path"], variable))

        status = ctk.CTkLabel(dialog, text="", text_color=LANDING_TEXT_SECONDARY)
        status.pack(padx=20, pady=(6, 0))

        result_frame = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        result_frame.pack(fill="both", expand=True, padx=20, pady=(8, 12))

        def on_compare():
            selected = [path for path, variable in checkboxes if variable.get()]
            if len(selected) < 2:
                status.configure(text="En az iki uçuş seçmelisin.", text_color=LANDING_WARNING)
                return
            status.configure(text="Uçuşlar işleniyor...", text_color=LANDING_TEXT_SECONDARY)
            compare_button.configure(state="disabled")
            self._run_comparison(selected, result_frame, status, compare_button)

        button_row = ctk.CTkFrame(dialog, fg_color="transparent")
        button_row.pack(pady=(0, 12))
        compare_button = ctk.CTkButton(
            button_row, text="Karşılaştır", command=on_compare,
            fg_color=LANDING_ACCENT, hover_color="#2d6fc0", text_color=LANDING_TEXT,
        )
        compare_button.pack(side="left", padx=6)
        ctk.CTkButton(
            button_row, text="Kapat", fg_color="transparent", border_width=1,
            border_color=LANDING_CARD_BORDER, text_color=LANDING_TEXT_SECONDARY,
            hover_color=LANDING_CARD, command=dialog.destroy,
        ).pack(side="left", padx=6)

    def _run_comparison(self, paths: list, result_frame, status_label, compare_button):
        """Seçilen logları arka planda backend'den geçirip tabloyu çizer.

        Her log için ayrı bir backend çağrısı yapılıyor ve bu saniyeler
        sürebildiğinden iş ayrı bir thread'de; sonuç Queue üzerinden ana
        thread'e taşınıyor (Tkinter widget'ları başka thread'den güvenle
        güncellenemez — bkz. _load_file'daki aynı desen)."""
        result_queue: queue.Queue = queue.Queue()

        def worker():
            results, chart_series, errors = [], [], []
            for path in paths:
                try:
                    data = self._run_backend(path)
                    name = Path(path).name
                    results.append((name, _flight_summary_metrics(data)))
                    chart_series.append((name, data.get("batteries", [])))
                except Exception as error:  # bir log bozuksa diğerleri yine gösterilsin
                    errors.append(f"{Path(path).name}: {error}")
            result_queue.put((results, chart_series, errors))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                results, chart_series, errors = result_queue.get_nowait()
            except queue.Empty:
                self.after(100, poll)
                return

            if compare_button.winfo_exists():
                compare_button.configure(state="normal")
            # Tek kontrol yeterli: result_frame ile status_label aynı
            # pencerenin çocukları, pencere kapatılınca ikisi birden yok olur.
            if not result_frame.winfo_exists():
                return  # kullanıcı pencereyi kapatmış

            next_row = self._build_comparison_table(result_frame, results)
            self._build_comparison_chart(result_frame, next_row, chart_series)
            if errors:
                status_label.configure(text="⚠ " + " | ".join(errors), text_color=LANDING_CRITICAL)
            else:
                status_label.configure(
                    text=f"{len(results)} uçuş karşılaştırıldı.", text_color=LANDING_TEXT_SECONDARY
                )

        self.after(100, poll)

    def _build_comparison_table(self, parent, results: list) -> int:
        """Satır = metrik, sütun = uçuş olacak şekilde özet tabloyu çizer.
        Ölçülemeyen değerler "—" gösterilir (bkz. _format_comparison_value).
        Renkler LANDING paleti: dialog temayı takip etmiyor (bkz.
        _on_compare_click'teki açıklama). Kullandığı son grid satırının bir
        altını döner ki _build_comparison_chart aynı parent'a alt satırdan
        devam edebilsin."""
        for child in parent.winfo_children():
            child.destroy()
        if not results:
            return 0

        next_row = 0
        # Multirotor ile sabit kanatı yan yana koymak "34 W vs 541 W" gibi
        # doğa farklarını sorunmuş gibi gösterebilir — dürüst bir not düş.
        if _comparison_has_mixed_vehicles(results):
            ctk.CTkLabel(
                parent,
                text="Farklı araç tipleri karşılaştırılıyor — mutlak değerler doğrudan kıyaslanamaz.",
                text_color=LANDING_TEXT_SECONDARY, font=ctk.CTkFont(size=11),
            ).grid(row=next_row, column=0, columnspan=len(results) + 1, pady=(0, 4), sticky="w")
            next_row += 1

        header_font = ctk.CTkFont(size=12, weight="bold")
        ctk.CTkLabel(parent, text="", width=150).grid(row=next_row, column=0, padx=6, pady=4)
        for column, (name, _metrics) in enumerate(results, start=1):
            # Uzun ad başlığı iki satıra sarıp tabloyu dalgalandırıyordu;
            # ortadan kısaltılıyor, tam ad fare ipucunda.
            header = ctk.CTkLabel(
                parent, text=_middle_ellipsis(name), font=header_font,
                text_color=LANDING_TEXT, justify="center",
            )
            header.grid(row=next_row, column=column, padx=6, pady=4, sticky="ew")
            if _middle_ellipsis(name) != name:
                _add_toolbar_tooltip(header, name)
        next_row += 1

        # Dikkat çekici hücreler (ör. diğerlerinin 2 katı iç direnç) uyarı
        # rengiyle vurgulanır; kural _comparison_notable_cells'te.
        notable = _comparison_notable_cells(results)
        notable_positions = {(key, column_index) for key, column_index, _n in notable}

        # Tek/çift satırlara bant (stat kutucuklarındaki desenle aynı amaç,
        # LANDING_ROW_STRIPE tonuyla). pady sıfıra yakın tutuluyor ki bant
        # KESİNTİSİZ görünsün; nefes payı ipady ile veriliyor.
        data_start = next_row
        for row, (key, title) in enumerate(COMPARISON_ROWS, start=data_start):
            row_color = LANDING_ROW_STRIPE if (row - data_start) % 2 == 1 else "transparent"
            ctk.CTkLabel(
                parent, text=title, text_color=LANDING_TEXT_SECONDARY, anchor="w",
                fg_color=row_color, corner_radius=0,
            ).grid(row=row, column=0, padx=(6, 2), pady=1, ipady=4, sticky="nsew")
            for column, (_name, metrics) in enumerate(results, start=1):
                is_notable = (key, column - 1) in notable_positions
                cell = ctk.CTkLabel(
                    parent, text=_format_comparison_value(key, metrics),
                    text_color=LANDING_WARNING if is_notable else LANDING_TEXT,
                    anchor="center", fg_color=row_color, corner_radius=0,
                )
                cell.grid(row=row, column=column, padx=2, pady=1, ipady=4, sticky="nsew")
                if key in COMPARISON_CURRENT_DEPENDENT_KEYS and metrics.get("current_data_missing"):
                    _add_toolbar_tooltip(cell, NO_CURRENT_DATA_TOOLTIP)
        next_row = data_start + len(COMPARISON_ROWS)

        # Vurgulanan her hücre için tablo altına tek satır otomatik özet —
        # sayının NEDEN işaretlendiğini söylemeden vurgu yarım kalırdı.
        row_titles = dict(COMPARISON_ROWS)
        for key, _column_index, flight_name in notable:
            ctk.CTkLabel(
                parent,
                text=f"⚠ {flight_name}: {row_titles[key]} diğer uçuşların medyanının 2 katı ve üzeri.",
                text_color=LANDING_WARNING, font=ctk.CTkFont(size=11), anchor="w",
            ).grid(row=next_row, column=0, columnspan=len(results) + 1, pady=(6, 0), sticky="w")
            next_row += 1

        for column in range(1, len(results) + 1):
            parent.grid_columnconfigure(column, weight=1)
        return next_row

    def _build_comparison_chart(self, parent, row: int, chart_series: list):
        """Tablo altına, uçuş başına tek çizgi olacak şekilde voltaj overlay
        grafiği çizer (bkz. _overlay_battery_for_flight). Zaman ekseni her
        uçuşta zaten t=0'dan başlıyor (_normalize_time_axis), bu yüzden ek
        hizalama gerekmiyor. Dialog hep LANDING paletinde olduğu için renkler
        DARK_PALETTE'ten sabit alınıyor -- tema-takip eden modül singles'ı
        açık temada bu koyu zemine karşı yanlış olurdu."""
        overlays = [
            (name, battery) for name, batteries in chart_series
            for battery in [_overlay_battery_for_flight(batteries)] if battery is not None
        ]
        if not overlays:
            return

        figure = Figure(figsize=(8.6, 2.6), dpi=100, facecolor=LANDING_BG)
        ax = figure.add_subplot(111)
        ax.set_facecolor(LANDING_BG)
        colors = DARK_PALETTE["SERIES_COLORS"]
        for i, (name, battery) in enumerate(overlays):
            ax.plot(
                battery["time_s"], battery["voltage_v"],
                color=colors[i % len(colors)], linewidth=1.5, label=_middle_ellipsis(name),
            )
        ax.set_xlabel("Zaman (s)", color=LANDING_TEXT_SECONDARY, fontsize=9)
        ax.set_ylabel("Voltaj (V)", color=LANDING_TEXT_SECONDARY, fontsize=9)
        ax.tick_params(colors=LANDING_TEXT_SECONDARY, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(LANDING_CARD_BORDER)
        ax.grid(True, color=LANDING_CARD_BORDER, alpha=0.3)
        ax.legend(
            loc="upper right", facecolor=LANDING_CARD, edgecolor=LANDING_CARD_BORDER,
            labelcolor=LANDING_TEXT_SECONDARY, fontsize=8,
        )
        figure.tight_layout()

        canvas = FigureCanvasTkAgg(figure, master=parent)
        canvas.draw()
        canvas.get_tk_widget().grid(
            row=row, column=0, columnspan=len(chart_series) + 1, sticky="nsew", pady=(14, 4),
        )

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
        """Dosya yüklendikten sonra süre/örnek sayısı/aralık gibi özet değerleri
        gösteren satır.

        Kutucuklar `grid` ile yerleşiyor (yan yana pack DEĞİL): dokuz kutucuk
        dar bir pencereye sığmıyor ve sağdakiler kesiliyordu. Sütun sayısı
        pencere genişliğine göre seçilip artan kutucuklar alt satıra iniyor
        (bkz. _layout_stats_row)."""
        self.stats_row = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
        self.stats_row.pack(side="top", fill="x", padx=16, pady=(0, 8))

        self.stat_labels = {}
        self._stat_tiles = []
        for key, title in STAT_TILE_DEFINITIONS:
            tile = ctk.CTkFrame(self.stats_row, fg_color=UI_GRIDLINE, corner_radius=8)

            # fill="x" + anchor="center": metin kutunun tam soluna değil,
            # ortasına doğru kayar (tile genişliği en uzun değere göre
            # otomatik ayarlanır, metin o genişlik içinde ortalanır).
            ctk.CTkLabel(
                tile, text=title, text_color=UI_TEXT_MUTED, font=ctk.CTkFont(size=11)
            ).pack(fill="x", anchor="center", padx=12, pady=(8, 0))
            value_label = ctk.CTkLabel(
                tile, text="—", text_color=UI_TEXT_PRIMARY, font=ctk.CTkFont(size=15, weight="bold")
            )
            value_label.pack(fill="x", anchor="center", padx=12, pady=(0, 8))
            self.stat_labels[key] = value_label
            self._stat_tiles.append(tile)
            # "B2: 39 mΩ" gibi kısaltmalı değerlerin ne olduğunu yeni
            # kullanıcıya anlatan tek yer (kullanıcı geri bildirimi).
            _add_tile_tooltip(tile, STAT_TILE_TOOLTIPS[key])

        self._stat_columns = None  # henüz yerleşmedi
        self._layout_stats_row()
        self.stats_row.bind("<Configure>", self._on_stats_row_configure)

    @staticmethod
    def _stat_row_width(widths: list, columns: int) -> int:
        """Belirli bir sütun sayısıyla satırın kaplayacağı genişlik. Sütun
        genişliği, o sütuna düşen kutucukların EN GENİŞİ kadar olur (grid
        kuralı) — kutucuklar eşit genişlikte değil ("Süre" 79px, "Voltaj
        Aralığı" 133px), bu yüzden basit bir bölme doğru sonucu vermiyor."""
        total = 0
        for column in range(columns):
            in_column = widths[column::columns]
            if in_column:
                total += max(in_column) + STATS_TILE_GAP
        return total

    def _stat_columns_that_fit(self, widths: list, available: int) -> int:
        """Kutucukların sığdığı sütun sayısı.

        Önce sığan en fazla sütun bulunuyor; sarma gerekiyorsa sütunlar
        satırlara EŞİT dağıtılıyor. Yoksa dokuz kutucuk sekiz sütuna sığdığında
        yerleşim 8+1 oluyordu — tek başına kalan kutucuk hata gibi duruyordu;
        aynı iki satırda 5+4 hem dengeli hem de daha dar."""
        widest = 1
        for columns in range(len(widths), 1, -1):
            if self._stat_row_width(widths, columns) <= available:
                widest = columns
                break

        rows = -(-len(widths) // widest)  # yukarı yuvarlayan bölme
        balanced = -(-len(widths) // rows)
        if balanced < widest and self._stat_row_width(widths, balanced) <= available:
            return balanced
        return widest

    def _layout_stats_row(self, width: int = None):
        """Kutucukları, sığdıkları kadar sütunla ızgaraya yerleştirir."""
        if width is None:
            width = self.stats_row.winfo_width()
        widths = [tile.winfo_reqwidth() for tile in self._stat_tiles]
        columns = self._stat_columns_that_fit(widths, width)
        if self._stat_columns == columns:
            return
        self._stat_columns = columns

        for index, tile in enumerate(self._stat_tiles):
            row = index // columns
            tile.grid(
                row=row, column=index % columns, sticky="w",
                padx=(0, STATS_TILE_GAP), pady=(0 if row == 0 else STATS_TILE_GAP, 0),
            )

    def _on_stats_row_configure(self, event=None):
        # <Configure> sırasında winfo_width() eski değeri veriyor; yeni genişlik
        # olay nesnesinde (aynı tuzak toolbar'da da vardı, bkz. _toolbar_width).
        self._layout_stats_row(event.width if event is not None else None)

    def _refresh_stats_layout(self):
        """Kutucuk metinleri değiştikten sonra yerleşimi yeniden hesaplar.
        `after_idle`: Tk genişlik hesabını boşta yapıyor, hemen okunan
        `winfo_reqwidth()` eski metnin genişliğini verirdi."""
        self._stat_columns = None  # genişlikler değişti, kararı yenile
        self.after_idle(self._layout_stats_row)

    def _build_warnings_area(self):
        """Backend'in kural tabanlı ürettiği uyarıları (ör. aşırı voltaj düşümü,
        motor akım dengesizliği) gösteren satır. Uyarı yoksa boş kalır, ekstra
        yer kaplamaz.

        Uyarılar tek bir birleşik etiket DEĞİL, uyarı başına ayrı etiket:
        "Batarya N:"/"Motor N:" ile başlayanlar tıklanabilir ve tıklanınca
        ilgili seri grafikte vurgulanıyor (diğerleri soluklaşıyor) — tek
        etikette hangi satıra tıklandığı bilinemezdi."""
        self.warnings_frame = ctk.CTkFrame(self.analysis_frame, fg_color="transparent")
        self.warnings_frame.pack(side="top", fill="x", padx=16, pady=(0, 4))
        self._warning_labels = []
        self._warning_wraplength = 1000  # ilk <Configure>'a kadar yer tutucu

    def _update_warnings(self, warnings: list):
        self._current_warnings = warnings  # PDF raporu ham listeye ihtiyaç duyuyor
        # Yeni dosya yükleme de Temizle de buradan geçiyor — vurgunun ayrıca
        # temizlenmesi gereken başka bir kanca yok.
        self._highlight_series = None
        for label in self._warning_labels:
            label.destroy()
        self._warning_labels = []
        for message in warnings:
            label = ctk.CTkLabel(
                self.warnings_frame, text=f"⚠ {message}", text_color=UI_COLOR_WARNING,
                justify="left", anchor="w", wraplength=self._warning_wraplength,
            )
            label.pack(side="top", fill="x")
            match = WARNING_TARGET_RE.match(message)
            if match:
                target = (match.group(1), int(match.group(2)))
                label.configure(cursor="hand2")
                label.bind("<Button-1>",
                           lambda _e, t=target, l=label: self._on_warning_click(t, l))
            self._warning_labels.append(label)

    def _on_warning_click(self, target, clicked_label):
        """Uyarıya tıklanınca hedef seri vurgulanır (aynı hedefe ikinci tık
        vurguyu kaldırır). Basılı görünüm: aktif uyarının zemini tonlanır.
        Replot backend'e gitmeden cache'ten yapılır — tema toggle'ının
        kullandığı replot kümesiyle aynı, maliyeti bilinir."""
        self._highlight_series = None if self._highlight_series == target else target
        for label in self._warning_labels:
            label.configure(
                fg_color=UI_GRIDLINE
                if (label is clicked_label and self._highlight_series is not None)
                else "transparent"
            )
        self._plot_voltage_panel(self._last_batteries or [])
        self._plot_battery_currents(self._last_batteries or [])
        self._plot_motor_currents(self._last_motors or [])
        self.canvas.draw()

    def _build_status_label(self):
        """Hata mesajları için ayrı bir durum satırı (dosya etiketiyle karışmasın diye)."""
        # wraplength: uzun backend hata mesajları (ör. system_power mesajı
        # birkaç cümle) pencere genişliğinde kesilmesin, alt satıra sarsın.
        # Başlangıç değeri (1000) sadece ilk <Configure> olayına kadar geçerli
        # bir yer tutucu; gerçek genişlik _on_analysis_frame_configure'da
        # toolbar/stats-row'daki ile AYNI desenle yeniden hesaplanıyor —
        # warnings_label da aynı sorunu yaşıyordu (hiç wraplength'i yoktu),
        # o yüzden ikisi birlikte tek bir <Configure> bağıyla güncelleniyor.
        self.status_label = ctk.CTkLabel(
            self.analysis_frame, text="", text_color=UI_COLOR_CRITICAL, justify="left", anchor="w",
            wraplength=1000,
        )
        self.status_label.pack(side="top", fill="x", padx=16, pady=(0, 4))
        self.analysis_frame.bind("<Configure>", self._on_analysis_frame_configure)

    def _on_analysis_frame_configure(self, event=None):
        """Dar pencerede uzun bir uyarı/hata metni görünür genişlikten daha
        geniş bir noktada sarmasın diye wraplength'i güncel genişliğe göre
        yeniden hesaplar (bkz. _toolbar_width — aynı `<Configure>` sırasında
        `winfo_width()` eski değeri verme tuzağı burada da geçerli, bu yüzden
        olay nesnesinin genişliği kullanılıyor)."""
        width = event.width if event is not None else self.analysis_frame.winfo_width()
        wraplength = max(1, width - 32)  # padx=16 iki yandan
        # Saklanıyor: uyarı etiketleri her _update_warnings'te yeniden
        # kurulduğu için yenileri de güncel genişlikle doğmalı.
        self._warning_wraplength = wraplength
        for label in self._warning_labels:
            label.configure(wraplength=wraplength)
        self.status_label.configure(wraplength=wraplength)

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
        ax.format_coord = _make_format_coord(ylabel)
        ax.grid(True, color=GRIDLINE, linewidth=1, linestyle="-")
        ax.set_axisbelow(True)
        ax.tick_params(colors=TEXT_MUTED)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("bottom", "left"):
            ax.spines[side].set_color(AXIS_LINE)

    def _on_plot_hover(self, event):
        """Fare grafik üzerindeyken imlecin altındaki örneğin/hücrenin
        zaman ve değerini gösteren bir tooltip (annotation) çizer.
        ax_current/ax_motors her ısı haritası/çizgi geçişinde YENİDEN
        yaratıldığı için burada self.ax_voltage/ax_current/ax_motors HER
        ÇAĞRIDA canlı okunur, closure'da tutulmaz."""
        if self._hover_annotation is not None:
            self._hover_annotation.remove()
            self._hover_annotation = None

        ax = event.inaxes
        if ax not in (self.ax_voltage, self.ax_current, self.ax_motors) or event.xdata is None:
            self.canvas.draw_idle()
            return

        # Isı haritasında eksende hiç çizgi yok; hücre bilgisi çizim sırasında
        # saklanan ızgaradan okunur (bkz. _plot_*_heatmap).
        heatmap = self._heatmap_for_axes(ax)
        if heatmap is not None:
            found = self._hover_heatmap_cell(heatmap, event)
        else:
            found = self._hover_nearest_line_point(ax, event)

        if found is None:
            self.canvas.draw_idle()
            return

        label, x, y, value_text = found
        self._show_hover_annotation(ax, x, y, f"{label}\n{x:.1f}s → {value_text}")
        self.canvas.draw_idle()

    def _heatmap_for_axes(self, ax):
        """Verilen eksen şu an ısı haritası çiziyorsa hover verisini döndürür."""
        if ax is self.ax_current:
            return self._current_heatmap
        if ax is self.ax_motors:
            return self._motors_heatmap
        return None

    def _hover_nearest_line_point(self, ax, event):
        """Çizgi grafiğinde imlece en yakın örneği bulur.
        Dönüş: (etiket, zaman, değer, gösterilecek metin) ya da None."""
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
            return None

        _, label, x, y = best
        if ax is self.ax_voltage:
            # Üst panel sıcaklık moduna alınabiliyor; birim onunla değişmeli
            # (bu dal olmadan tooltip "24.50V" yazıyordu).
            value_text = (f"{y:.1f} °C" if self.voltage_view_mode == "temperature"
                          else f"{y:.2f}V")
        elif ax is self.ax_motors and self.motor_view_mode == "pwm":
            # PWM bir akım değil; bu dal olmadan tooltip "1650.00A" yazıyordu.
            value_text = f"{y:.0f} µs"
        else:
            value_text = f"{y:.2f}A"
        return label, x, y, value_text

    def _hover_heatmap_cell(self, heatmap, event):
        """Isı haritasında imlecin üstündeki hücreyi bulur.

        Satırlar `imshow` extent'i gereği 1'den başlar ve her satır 1 birim
        yüksektir; yani y=2.3 → 2. satır (dizide indeks 1). Sütun ise ortak
        zaman eksenine en yakın örnektir. Dönüş: çizgi dalıyla aynı dörtlü."""
        row = int(round(event.ydata)) - 1
        if not 0 <= row < len(heatmap["labels"]):
            return None

        times = heatmap["times"]
        if len(times) == 0:
            return None
        col = int(np.searchsorted(times, event.xdata))
        col = min(max(col, 0), len(times) - 1)
        # searchsorted ekleme noktasını verir; bir öncekiyle kıyaslayıp
        # gerçekten en yakın örneği seç (aksi halde tooltip hep sağdaki
        # hücreyi gösteriyordu).
        if col > 0 and abs(times[col - 1] - event.xdata) <= abs(times[col] - event.xdata):
            col -= 1

        # y olarak satırın merkezi kullanılır (imlecin tam yeri değil), böylece
        # tooltip hücrenin ortasına tutturulur.
        return heatmap["labels"][row], float(times[col]), row + 1.0, heatmap["format_cell"](row, col)

    def _show_hover_annotation(self, ax, x, y, text):
        """Tooltip'i çizer.

        Nokta eksenin üst/sağ kenarına yakınsa tooltip o yöne doğru değil,
        ters yöne (aşağı/sola) doğru açılır — aksi halde etiket figürün
        kenarından taşıp tamamen görünmez oluyordu (ör. "Batarya 1" üst
        uçtaki bir değerin üzerindeyken hiç gözükmüyordu)."""
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        x_frac = (x - xlim[0]) / (xlim[1] - xlim[0]) if xlim[1] != xlim[0] else 0.5
        y_frac = (y - ylim[0]) / (ylim[1] - ylim[0]) if ylim[1] != ylim[0] else 0.5
        offset_x, ha = (-12, "right") if x_frac > 0.85 else (12, "left")
        offset_y, va = (-12, "top") if y_frac > 0.85 else (12, "bottom")

        self._hover_annotation = ax.annotate(
            text,
            xy=(x, y), xytext=(offset_x, offset_y), textcoords="offset points",
            ha=ha, va=va,
            bbox=dict(boxstyle="round", fc=SURFACE, ec=AXIS_LINE),
            color=TEXT_PRIMARY, fontsize=9,
        )

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
        self._set_file_label(Path(file_path).name)
        self.status_label.configure(text="İşleniyor...", text_color=UI_TEXT_SECONDARY)
        self.load_button.configure(state="disabled")
        self.recent_menu.configure(state="disabled")
        self.loading_progress.pack(side="left", padx=(TOOLBAR_GAP, 0))
        self.loading_progress.start()
        self._refresh_toolbar_layout()  # çubuk sol grubu genişletti
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
        self._refresh_toolbar_layout()
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
        # Widget'ın metni değil saklanan TAM metin: etiket dar toolbar'da
        # kısaltılabiliyor, rapora kısaltılmış ad düşmemeli.
        fig.text(0.08, 0.89, f"Dosya: {self._file_label_text}", color=TEXT_SECONDARY, fontsize=11)
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
            # y sabit adımlarla (0.035) azalıyor; sayfa alt kenarı y=0.
            # Çok sayıda uyarı varsa (ör. çok motorlu bir araçta her motor
            # çifti için ayrı dengesizlik uyarısı) liste sayfa dışına taşıp
            # SESSİZCE kayboluyordu. Sığmayanlar artık "+N tane daha" ile
            # özetleniyor — o satırın kendisi de sığacak şekilde bir satır
            # payı ayrılıyor.
            PAGE_BOTTOM_MARGIN = 0.04
            LINE_HEIGHT = 0.035
            max_lines = max(1, int((y - PAGE_BOTTOM_MARGIN) / LINE_HEIGHT))
            warnings = self._current_warnings
            if len(warnings) > max_lines:
                visible = warnings[:max_lines - 1]
                hidden_count = len(warnings) - len(visible)
            else:
                visible = warnings
                hidden_count = 0
            for warning in visible:
                fig.text(0.08, y, f"• {warning}", color=COLOR_WARNING, fontsize=11)
                y -= LINE_HEIGHT
            if hidden_count > 0:
                fig.text(0.08, y, f"… ve {hidden_count} uyarı daha (tam liste için uygulamaya bakın).",
                         color=TEXT_SECONDARY, fontsize=10)
        return fig

    def _on_clear_click(self):
        """Yüklü veriyi ve tüm panelleri başlangıç (boş) durumuna döndürür;
        ikinci bir dosyayı temiz bir ekrandan yüklemek isteyenler için."""
        self._set_file_label("Henüz dosya seçilmedi.")
        self.status_label.configure(text="")
        self._update_warnings([])
        self._update_stats([])
        self._refresh_stats_layout()  # kutucuklar "—"ye döndü, daralabilirler
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
                # Zaman ekseni burada, veri uygulamaya girer girmez
                # normalize edilir — böylece grafikler, tooltip'ler, ısı
                # haritaları ve CSV dışa aktarımı hepsi aynı (0'dan
                # başlayan) ekseni görür, süre kartıyla çelişki kalmaz.
                return _normalize_time_axis(json.load(f))

    def _plot_power_data(self, data: dict):
        """Her bataryanın voltajı/akımı ve motor akımlarını, ortak zaman
        eksenini paylaşan panellerde çizer. Birden fazla batarya varsa her
        biri kendi rengiyle (iki panelde de aynı renk) çizilir."""
        batteries = data.get("batteries", [])
        self._update_vehicle_label(data.get("meta", {}))
        self._update_stats(batteries)
        self._update_capacity_stats(batteries)
        # Yeni değerler kutucukların genişliğini değiştirdi; kaç sütunun
        # sığdığı yeniden hesaplansın.
        self._refresh_stats_layout()
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
        self._set_file_label(f"{file_name} · {label}" if label else file_name)

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

        if not batteries:
            self.ax_voltage.text(
                0.5, 0.5, NO_BATTERY_MESSAGE, transform=self.ax_voltage.transAxes,
                ha="center", va="center", color=TEXT_MUTED,
            )
            return

        voltage_sag_threshold = _get_warning_thresholds(
            self._last_vehicle_type)["voltage_sag_threshold"]
        panel_labels = [f"Batarya {battery['id']}" for battery in batteries]
        for i, battery in enumerate(batteries):
            color, linestyle = _series_style(i)
            label = f"Batarya {battery['id']}"
            alpha = _series_highlight_alpha(self._highlight_series, label, panel_labels)
            self.ax_voltage.plot(
                battery["time_s"], battery["voltage_v"], color=color, linestyle=linestyle,
                linewidth=2, label=label, alpha=alpha,
            )
            if battery["voltage_v"]:
                _draw_voltage_sag_line(self.ax_voltage, battery["voltage_v"][0],
                                       voltage_sag_threshold, color, alpha_factor=alpha)

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

        panel_labels = [f"Batarya {battery['id']}" for battery in batteries]
        plotted = 0
        for i, battery in enumerate(batteries):
            temperature_c = battery.get("temperature_c") or []
            if not temperature_c or all(t == 0 for t in temperature_c):
                continue
            color, linestyle = _series_style(i)
            label = f"Batarya {battery['id']}"
            self.ax_voltage.plot(
                battery["time_s"], temperature_c, color=color, linestyle=linestyle,
                linewidth=2, label=label,
                alpha=_series_highlight_alpha(self._highlight_series, label, panel_labels),
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
        self._current_heatmap = None  # ısı haritası çizilirse aşağıda yeniden doldurulur
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

        panel_labels = [f"Batarya {battery['id']}"
                        for battery in batteries if _has_current_data(battery)]
        plotted = 0
        for i, battery in enumerate(batteries):
            if not _has_current_data(battery):
                continue
            # Renk indeksi olarak orijinal sıra (i) kullanılıyor, çizilen
            # serinin sırası değil: bir batarya atlansa bile kalanların rengi
            # voltaj panelindeki aynı bataryanın rengiyle eşleşmeye devam etsin.
            color, linestyle = _series_style(i)
            label = f"Batarya {battery['id']}"
            self.ax_current.plot(
                battery["time_s"], battery["current_a"], color=color, linestyle=linestyle,
                linewidth=2, label=label,
                alpha=_series_highlight_alpha(self._highlight_series, label, panel_labels),
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
        _draw_negative_current_threshold(
            self.ax_current, batteries,
            _get_warning_thresholds(self._last_vehicle_type)["negative_current_threshold"],
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

        self._current_heatmap = {
            "times": np.asarray(all_times),
            "labels": [f"Batarya {battery['id']}" for battery in batteries],
            # Hücrenin metnini üreten kapanış: interpolasyon ızgarasını (grid)
            # kendisi tutar, böylece _on_plot_hover her panelin birimini
            # bilmek zorunda kalmaz.
            "format_cell": lambda row, col: f"{grid[row][col]:.2f} A",
        }

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
        self._motors_heatmap = None  # ısı haritası çizilirse aşağıda yeniden doldurulur
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

        panel_labels = [f"Motor {motor['id']}"
                        for motor in motors if _has_current_data(motor)]
        plotted = 0
        for i, motor in enumerate(motors):
            if not _has_current_data(motor):
                continue
            color, linestyle = _series_style(i)
            label = f"Motor {motor['id']}"
            self.ax_motors.plot(
                motor["time_s"], motor["current_a"], color=color, linestyle=linestyle,
                linewidth=2, label=label,
                alpha=_series_highlight_alpha(self._highlight_series, label, panel_labels),
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
        _draw_negative_current_threshold(
            self.ax_motors, motors,
            _get_warning_thresholds(self._last_vehicle_type)["negative_current_threshold"],
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

        self._motors_heatmap = {
            "times": np.asarray(all_times),
            "labels": [output["label"] for output in pwm_outputs],
            # Sapmanın yanında MUTLAK PWM de yazılıyor: bu görünüm mutlak
            # değeri bilerek gizliyor (skalayı servolar domine ediyordu), ama
            # tek bir hücreye bakarken "1650 µs" bilgisi hâlâ değerli.
            "format_cell": lambda row, col: (
                f"{deviation[row][col]:+.1f} µs sapma ({grid[row][col]:.0f} µs)"
            ),
        }

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

        self._motors_heatmap = {
            "times": np.asarray(all_times),
            "labels": [f"Motor {motor['id']}" for motor in motors],
            "format_cell": lambda row, col: f"{grid[row][col]:.2f} A",
        }

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
        # ⚠ öneki, kartla uyarı listesi arasındaki bağı renk körü kullanıcılar
        # için de kurar (yalnız renk değişimi yeterince fark edilmiyordu).
        self.stat_labels["current_range"].configure(
            text=("⚠ " if min_current < 0 else "") + f"{min_current:.1f}–{max(all_current):.1f} A",
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

        duration = _flight_duration_seconds(batteries)
        remaining_text = "—"
        # En az kalanı olan batarya belirleyici (en kritik senaryo) — akım
        # serisi de AYNI bataryadan alınır ki hız tahmini tutarlı olsun.
        with_pct = [b for b in measured if b.get("remaining_pct") is not None]
        if with_pct and duration:
            critical = min(with_pct, key=lambda b: b["remaining_pct"])
            estimate = _remaining_time_range_min(
                duration, critical["remaining_pct"], critical["current_a"])
            if estimate is not None:
                lo, hi = round(estimate[0]), round(estimate[1])
                # Yuvarlama SONRASI karşılaştırma: 9.6 ve 9.9 "10–10 dk"
                # diye yazılmasın.
                remaining_text = f"~{lo:.0f} dk" if lo == hi else f"~{lo:.0f}–{hi:.0f} dk"
        self.stat_labels["remaining_time"].configure(text=remaining_text)


if __name__ == "__main__":
    app = App()
    app.mainloop()
