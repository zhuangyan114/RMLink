/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include "esp_err.h"
esp_err_t rmlink_network_init(void);
/* Wait through the bounded STA recovery window on the current network. */
bool rmlink_network_wait_for_link(void);
