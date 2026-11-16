#pragma once
#define CONFIG_IDF_TARGET_ESP32S3 1
extern "C" {
#include "DAP_config.h"
#include "DAP.h"
}
static constexpr uint32_t rmlink_swdio_out_mask = 1;
static constexpr uint32_t rmlink_swclk_out_mask = 2;
static constexpr uint32_t rmlink_swdio_in_mask = 1;
static inline void debug_probe_swdio_out_enable() { host_direction(1); }
static inline void debug_probe_swdio_out_disable() { host_direction(0); }
static inline void dedic_gpio_cpu_ll_write_mask(uint32_t mask, uint32_t value) {
    if (mask & 1) host_output(value & 1);
    if (mask & 2) host_clock((value >> 1) & 1);
}
