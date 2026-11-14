/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include "esp_err.h"
esp_err_t rmlink_network_init(void);
bool rmlink_network_is_connected(void);
/* The connection manager applies mode changes without touching saved credentials. */
void rmlink_network_set_enabled(bool enabled);
/* Wait through the bounded STA recovery window on the current network. */
bool rmlink_network_wait_for_link(void);
