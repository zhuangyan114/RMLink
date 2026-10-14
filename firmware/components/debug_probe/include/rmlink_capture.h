/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef RMLINK_HOST_TEST
typedef int esp_err_t;
#else
#include "esp_err.h"
#endif
#include "rmlink_core.h"

/* Only the existing fixed-core executor calls command/run/closed. */
esp_err_t rmlink_capture_init(void (*wake)(void));
size_t rmlink_capture_command(uint8_t transport, uint32_t session,
    const uint8_t *request, size_t len, uint8_t *out, const rmlink_owner_t *owner);
bool rmlink_capture_run(void);
bool rmlink_capture_raw_allowed(void);
void rmlink_capture_closed(uint8_t transport, uint32_t session);
/* These only move completed records. They never touch the target. */
bool rmlink_capture_bind(const uint8_t ticket[16], uint32_t *generation);
bool rmlink_capture_stream_live(uint32_t generation);
size_t rmlink_capture_take(uint8_t *out, size_t capacity, unsigned timeout_ms);
void rmlink_capture_stream_lost(uint32_t generation);
