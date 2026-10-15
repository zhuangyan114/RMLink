# The following five lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.5)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

if(${IDF_VERSION_MAJOR} LESS 5)
    message(FATAL_ERROR "ESP-IDF v${IDF_VERSION_MAJOR}.${IDF_VERSION_MINOR} is not supported! "
        "Please use v5.0 or newer.")
endif()

set(RMLINK_DAP_PACKET_SIZE 64 CACHE STRING "TCP DAP capacity; native USB remains 64")
if(NOT RMLINK_DAP_PACKET_SIZE MATCHES "^(64|128|256|512)$")
    message(FATAL_ERROR "RMLINK_DAP_PACKET_SIZE must be 64, 128, 256 or 512")
endif()
add_compile_definitions(RMLINK_PACKET_SIZE=${RMLINK_DAP_PACKET_SIZE})
set(PROJECT_VER "0.2.15")
project(rmlink)
