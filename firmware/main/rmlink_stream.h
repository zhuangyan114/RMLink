/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Versioned 60-byte probe-only snapshots. No target access or socket changes. */
bool rmlink_stream_diagnostics(unsigned page, uint8_t output[60]);
