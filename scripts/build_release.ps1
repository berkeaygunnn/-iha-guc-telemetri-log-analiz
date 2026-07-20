# Uygulamayi tek komutla dagitilabilir bir pakete (dist/iha_guc_telemetri_analiz/)
# donusturur: once C++ backend'i derler, sonra PyInstaller ile frontend'i paketler.
#
# Kullanim (repo kokunden):
#   .\scripts\build_release.ps1

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# 1) Backend'i derle (Ninja generator - Unicode/Turkce yol karakterleriyle
# MinGW Makefiles'in yasadigi sorunu atlamak icin bu proje bunu zaten kullaniyor).
Write-Host "--- Backend deriliyor ---"
cmake -S "$repoRoot\backend" -B "$repoRoot\backend\build" -G "Ninja"
cmake --build "$repoRoot\backend\build"

# 2) PyInstaller'i (ve diger paketleme bagimliliklarini) kur.
Write-Host "--- PyInstaller kuruluyor ---"
pip install -r "$repoRoot\frontend\requirements-dev.txt"
pip install -r "$repoRoot\frontend\requirements.txt"

# 3) Frontend'i spec dosyasina gore paketle.
Write-Host "--- Frontend paketleniyor ---"
pyinstaller "$repoRoot\frontend\power_log_frontend.spec" --noconfirm --distpath "$repoRoot\dist" --workpath "$repoRoot\frontend\build"

Write-Host "--- Tamamlandi ---"
Write-Host "Dagitilabilir paket: $repoRoot\dist\iha_guc_telemetri_analiz\"
Write-Host "Calistirmak icin: $repoRoot\dist\iha_guc_telemetri_analiz\iha_guc_telemetri_analiz.exe"
