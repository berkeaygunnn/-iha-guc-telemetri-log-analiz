cmake_minimum_required(VERSION 3.15)
project(iha_power_log_backend CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(power_log_backend src/main.cpp)

if(WIN32)
    target_link_libraries(power_log_backend shell32)
endif()

if(WIN32 AND MINGW)
    # GUI (WIN32) alt sistemiyle derlemek, frontend'in her backend çağrısında
    # kısa bir konsol penceresi açıp kapatmasını kaynağında engelliyor (bkz.
    # frontend/main.py _run_backend'deki CREATE_NO_WINDOW/STARTUPINFO'ya
    # rağmen bazı Windows 11 kurulumlarında pencerenin yine de görünmesi).
    # stdout/stderr'e yazma davranışı ETKİLENMİYOR: Python tarafı zaten hep
    # capture_output=True ile pipe üzerinden okuyor, bu subsystem'den
    # bağımsız çalışır - sadece konsolsuz interaktif çalıştırmada (ör. elle
    # `power_log_backend.exe ...` çalıştırıp çıktıyı canlı izlemek) çıktı
    # görünmez, yönlendirme (`> log.txt 2>&1`) ile yine okunabilir.
    #
    # SADECE MinGW: MinGW-w64 CRT, int main() tanımlıyken -mwindows ile
    # otomatik bir WinMain köprüsü sağlıyor. MSVC'de aynı şey main()
    # tanımlıyken /SUBSYSTEM:WINDOWS ile linker hatası veriyor ("unresolved
    # external symbol WinMain") - CI'nin windows-latest runner'ı generator
    # belirtilmeden varsayılan MSVC/Visual Studio generator'ını kullandığı
    # için bu, gerçek bir CI kırılmasıyla (test-windows job'ı) keşfedildi.
    # Projenin dağıtılan derlemesi (scripts/build_release.ps1) her zaman
    # Ninja+MinGW kullanıyor, bu yüzden MSVC'yi kapsam dışı bırakmak
    # kullanıcıya giden .exe'yi etkilemiyor.
    set_target_properties(power_log_backend PROPERTIES WIN32_EXECUTABLE TRUE)
endif()

# Sadece bilgilendirici uyarilar (-Werror YOK) - build'i kirmaz, gelecekte
# olasi hatalari (ornegin isaretli/isaretsiz donusum) erken yakalamaya yardimci olur.
if(MSVC)
    target_compile_options(power_log_backend PRIVATE /W4)
else()
    target_compile_options(power_log_backend PRIVATE -Wall -Wextra -Wconversion)
endif()
