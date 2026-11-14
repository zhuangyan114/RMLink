/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include "esp_err.h"

esp_err_t rmlink_ui_init(void);
void rmlink_ui_usb_connected(bool connected);
