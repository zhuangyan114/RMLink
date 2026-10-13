/*
 * SPDX-FileCopyrightText: 2020-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize serial bridge
 *
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_bridge_init(void);

#ifdef __cplusplus
}
#endif
