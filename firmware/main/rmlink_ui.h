/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

esp_err_t rmlink_ui_init(void);
void rmlink_ui_usb_connected(bool connected);
bool rmlink_ui_usb_is_connected(void);
/* Read-only initialization / LED transfer snapshot, wire schema 1. */
void rmlink_ui_diagnostics(uint8_t output[20]);
