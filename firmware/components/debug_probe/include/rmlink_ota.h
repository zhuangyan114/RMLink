/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "rmlink_core.h"
bool rmlink_ota_busy(void);
void rmlink_ota_closed(uint8_t transport, uint32_t session);
void rmlink_ota_confirm_boot(bool healthy);
size_t rmlink_ota_command(uint8_t transport, uint32_t session, const uint8_t *request,
    size_t length, uint8_t *response, const rmlink_owner_t *owner, bool target_idle);
