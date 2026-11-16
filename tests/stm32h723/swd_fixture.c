/* SPDX-License-Identifier: Apache-2.0
 * Standalone H723 SWD fixture. Uses reset HSI clock, no peripherals or actuators.
 * Overwrites sector 0 when explicitly programmed by the hardware test runner.
 */
#include <stdint.h>
void Reset_Handler(void);
static void Default_Handler(void) { for (;;) __asm volatile ("nop"); }
__attribute__((section(".vectors"), used))
const uintptr_t vectors[128] = {
    [0] = 0x24008000U,
    [1] = (uintptr_t)Reset_Handler,
    [2 ... 127] = (uintptr_t)Default_Handler,
};
typedef struct {
    uint32_t magic, sequence, values[10], inverse;
} fixture_t;
void Reset_Handler(void)
{
    __asm volatile ("cpsid i");
    volatile fixture_t *const state = (volatile fixture_t *)0x24000000U;
    state->magic = 0x58525357U;
    state->sequence = 0;
    for (;;) {
        const uint32_t sequence = state->sequence + 1;
        for (uint32_t i = 0; i < 10; ++i)
            state->values[i] = ((i + 1) << 24) | (sequence & 0x00ffffffU);
        state->inverse = ~sequence;
        state->sequence = sequence;
    }
}
