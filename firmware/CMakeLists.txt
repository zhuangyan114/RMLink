# The following five lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.5)

# Direct firmware / VS Code builds target the RMLink V1 board by default.
# Build-Firmware.ps1 supplies its own SDKCONFIG_DEFAULTS for explicit profiles.
if(NOT DEFINED SDKCONFIG_DEFAULTS)
    set(SDKCONFIG_DEFAULTS
        "sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.rmlink.defaults;sdkconfig.rmlink-core0.defaults;sdkconfig.rmlink-target.defaults;sdkconfig.rmlink-psram.defaults;sdkconfig.rmlink-v1.defaults")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

if(${IDF_VERSION_MAJOR} LESS 5)
    message(FATAL_ERROR "ESP-IDF v${IDF_VERSION_MAJOR}.${IDF_VERSION_MINOR} is not supported! "
        "Please use v5.0 or newer.")
endif()

set(RMLINK_DAP_PACKET_SIZE 512 CACHE STRING "TCP DAP capacity; native USB remains 64")
if(NOT RMLINK_DAP_PACKET_SIZE MATCHES "^(64|128|256|512)$")
    message(FATAL_ERROR "RMLINK_DAP_PACKET_SIZE must be 64, 128, 256 or 512")
endif()
add_compile_definitions(RMLINK_PACKET_SIZE=${RMLINK_DAP_PACKET_SIZE})
set(PROJECT_VER "0.3.6")
project(rmlink)
