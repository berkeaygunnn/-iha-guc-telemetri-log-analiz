cmake_minimum_required(VERSION 3.15)
project(iha_power_log_backend CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(power_log_backend src/main.cpp)

if(WIN32)
    target_link_libraries(power_log_backend shell32)
endif()

# Sadece bilgilendirici uyarilar (-Werror YOK) - build'i kirmaz, gelecekte
# olasi hatalari (ornegin isaretli/isaretsiz donusum) erken yakalamaya yardimci olur.
if(MSVC)
    target_compile_options(power_log_backend PRIVATE /W4)
else()
    target_compile_options(power_log_backend PRIVATE -Wall -Wextra -Wconversion)
endif()
