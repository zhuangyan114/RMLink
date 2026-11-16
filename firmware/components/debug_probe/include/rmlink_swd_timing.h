/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* requested Hz, estimated Hz, backend (0 generic/1 paced/2..7 fast nominal
 * 8/10/12/15/18/20 MHz; use estimated Hz for the calibrated rate),
 * paced half cycles, minimum paced half cycles, fast8/fast10 half cycles x16.
 * Cycle-counter calibration estimates execution timing, not pad signal quality. */
void SWD_OptimizedGetTiming(uint32_t output[7]);
#ifdef __cplusplus
}
#endif
