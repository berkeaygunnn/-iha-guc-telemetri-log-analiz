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

from PyInstaller.utils.hooks import collect_data_files

# SPECPATH, PyInstaller'ın bu dosyanın bulunduğu klasörü otomatik tanımladığı
# bir değişken - çalışma dizini neresi olursa olsun yollar hep doğru çözülsün diye.
backend_exe = os.path.join(SPECPATH, "..", "backend", "build", "power_log_backend.exe")

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

a = Analysis(
    ["main.py"],
    pathex=[SPECPATH],
    binaries=[(backend_exe, ".")],
    datas=customtkinter_datas,
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
