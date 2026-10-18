set(DAP_sources
    "DAP/Source/DAP_vendor.c"
    "DAP/Source/DAP.c"
    "DAP/Source/JTAG_DP.c"
    "DAP/Source/SW_DP.c"
)

set(debug_probe_sources
    "swd.c"
    "esp_usb_jtag.c"
    "debug_gpio.c"
    "rmlink_core.c"
    "rmlink_capture.c"
    "rmlink_flash.c"
    "rmlink_flash_port.c"
    "rmlink_ota.c"
    "rmlink_clock_trace.c"
)

set(include_dirs
    "include"
    "DAP/Include"
    "DAP/Config"
)

set(priv_include_dirs
    "."
)

set(dependencies
    "esp_timer"
    "esp_hw_support"
    "hal"
    "util"
    "esp_ringbuf"
    "mbedtls"
    "app_update"
)

# Starting from esp-idf v5.3, the GPIO driver is moved to a separate component
if("${IDF_VERSION_MAJOR}.${IDF_VERSION_MINOR}" VERSION_GREATER_EQUAL "5.3")
    list(APPEND dependencies "esp_driver_gpio" "esp_driver_rmt")
else()
    list(APPEND dependencies "driver")
endif()

idf_component_register(
    SRCS
        ${DAP_sources}
        ${debug_probe_sources}
    INCLUDE_DIRS
        ${include_dirs}
    PRIV_INCLUDE_DIRS
        ${priv_include_dirs}
    REQUIRES
        ${dependencies}
    LDFRAGMENTS
        "noflash.lf"
)

# The appliance's SWD loops need predictable optimized code, independent of the
# main application's debug build. Keep the original CMSIS-DAP executor/kernel.
target_compile_options(${COMPONENT_LIB} PRIVATE -O2)
target_link_libraries(${COMPONENT_LIB} INTERFACE "-u rmlink_ota_metadata")

# Keep vendor discovery identity tied to the application version, not a second
# manually maintained byte tuple.
idf_build_get_property(rmlink_version PROJECT_VER)
if(NOT rmlink_version MATCHES "^([0-9]+)\\.([0-9]+)\\.([0-9]+)$")
    message(FATAL_ERROR "RMLink wire version requires major.minor.patch")
endif()
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    RMLINK_VERSION_MAJOR=${CMAKE_MATCH_1}
    RMLINK_VERSION_MINOR=${CMAKE_MATCH_2}
    RMLINK_VERSION_PATCH=${CMAKE_MATCH_3})
