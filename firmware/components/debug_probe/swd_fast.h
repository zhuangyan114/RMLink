/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void SWD_OptimizedSetClock(uint32_t clock);
extern bool SWD_OptimizedActive;
/* Called only after the dedicated GPIO bundles are ready, on their owning CPU. */
void SWD_OptimizedInit(void);
/* false leaves the full CMSIS-DAP configuration to the generic engine. */
bool SWD_OptimizedTransfer(uint32_t request, uint32_t *data, uint8_t *ack);
#ifdef __cplusplus
}
#endif
