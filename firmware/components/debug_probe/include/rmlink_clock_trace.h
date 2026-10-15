/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stddef.h>
#include <stdint.h>
/* Called only by the owning, fixed-core executor under its control barrier. */
size_t rmlink_clock_trace_command(const uint8_t *request,size_t length,uint8_t *response,size_t capacity);
void rmlink_clock_trace_clear(void);
