# PyInstaller spec dosyası: frontend'i "onedir" (tek klasör) olarak paketler.
#
# Neden CLI bayrakları yerine spec dosyası: paketleme adımları (backend.exe'yi
# dahil etmek, CustomTkinter'ın tema/font dosyalarını taşımak) tekrar tekrar
# elle yazılacak kadar basit değil; burada bir kere yazılıp versiyon kontrolüne
# girer, herkes aynı komutla aynı sonucu üretir.
#
# Çalıştırma: scripts/build_release.ps1 (önerilen) ya da doğrudan
#   pyinstaller frontend/power_log_frontend.spec --noconfirm
# (backend'in önceden `cmake --build backend/build` ile derlenmiş olması gerekir.)

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

# SPECPATH, PyInstaller'ın bu dosyanın bulunduğu klasörü otomatik tanımladığı
# bir değişken - çalışma dizini neresi olursa olsun yollar hep doğru çözülsün diye.
# Backend Windows'ta .exe uzantılı, Linux'ta uzantısız üretiliyor
# (frontend/main.py'deki _find_backend_exe() ile aynı ikili-uzantı mantığı -
# henüz Linux'ta paketleme yapılmıyor ama bu kontrol olmadan spec Linux'ta
# her zaman "derlenmemiş" hatasıyla patlardı).
backend_build_dir = os.path.join(SPECPATH, "..", "backend", "build")
for _name in ("power_log_backend.exe", "power_log_backend"):
    _candidate = os.path.join(backend_build_dir, _name)
    if os.path.exists(_candidate):
        backend_exe = _candidate
        break
else:
    backend_exe = os.path.join(backend_build_dir, "power_log_backend.exe")

if not os.path.exists(backend_exe):
    raise SystemExit(
        f"Backend derlenmemiş: {backend_exe} bulunamadı.\n"
        "Önce 'cmake -S backend -B backend/build -G Ninja' ve "
        "'cmake --build backend/build' ile derleyin, sonra tekrar deneyin."
    )

# CustomTkinter kendi tema (json) ve font (ttf) dosyalarını paket içinde taşımak
# için resmi bir PyInstaller hook'u sağlamıyor; bu yüzden elle topluyoruz.
# Bunu atlarsak paketlenmiş uygulama ya temasız/hatalı görünür ya da açılışta patlar.
customtkinter_datas = collect_data_files("customtkinter")

# Pencere ikonu (frontend/main.py'deki _find_icon_path/ICON_PATH, _find_ico_path/
# ICO_PATH bunları ana .exe ile aynı klasördeki "assets/" içinde arıyor -
# contents_directory="." sayesinde datas girdileri de düz kök klasöre düşüyor).
# uygulama.ico, .exe dosyasının kendi simgesi olan icon_ico'dan (Gezgin/görev
# çubuğu için EXE parametresine verilir) AYRI: main.py çalışırken pencerenin
# başlık çubuğu ikonunu ayarlamak için iconbitmap ile açtığı dosya bu.
# .ico, Windows'a özgü bir ikon konteyner formatı; EXE()'nin icon= parametresi
# de sadece Windows PE .exe'sine gömülüyor. Linux'ta bu None geçilir (henüz
# Linux paketleme yapılmıyor ama ileride yapıldığında .ico dosyasını hiç
# aramaya/kullanmaya çalışmasın diye şimdiden platforma göre ayrılıyor).
icon_ico = os.path.join(SPECPATH, "assets", "icon.ico") if sys.platform.startswith("win") else None
icon_datas = [
    (os.path.join(SPECPATH, "assets", "icon.png"), "assets"),
    (os.path.join(SPECPATH, "assets", "uygulama.ico"), "assets"),
]

a = Analysis(
    ["main.py"],
    pathex=[SPECPATH],
    binaries=[(backend_exe, ".")],
    datas=customtkinter_datas + icon_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="iha_guc_telemetri_analiz",
    debug=False,
    strip=False,
    upx=True,
    console=False,  # arayüz uygulaması; konsol penceresi açılmasın
    icon=icon_ico,  # Windows'ta .exe dosyasının kendi simgesi (gezgin/görev çubuğu)
    # PyInstaller 6+ varsayılan olarak her şeyi gizli bir "_internal" alt
    # klasörüne koyar; backend.exe'yi ana .exe'yle aynı düz klasörde
    # bulmak istediğimiz için bu ayrımı kapatıyoruz.
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="iha_guc_telemetri_analiz",
)
