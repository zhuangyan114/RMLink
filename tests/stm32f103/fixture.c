/* SPDX-License-Identifier: Apache-2.0
 * Standalone acceptance fixture: HSI after reset, no GPIO/peripheral outputs.
 */
#include <stdint.h>
volatile struct { uint32_t magic, boots, counter, guard; } fixture_state;
void probe_step(void);
void Reset_Handler(void);
void Default_Handler(void) { for (;;) {} }
__attribute__((section(".vectors"), used))
const uintptr_t vectors[48] = {
    [0] = 0x20005000, [1] = (uintptr_t)Reset_Handler,
    [2 ... 47] = (uintptr_t)Default_Handler,
};
__attribute__((naked, noinline)) void probe_step(void) {
    __asm volatile("ldr r0, =fixture_state\nldr r1, [r0, #8]\n"
                   "adds r1, #1\nstr r1, [r0, #8]\nbx lr\n");
}
void Reset_Handler(void) {
    fixture_state.magic = 0x524d4c4b;
    fixture_state.boots = 1;
    fixture_state.counter = 0;
    fixture_state.guard = 0x13579bdf;
    for (;;) {
        probe_step();
        for (volatile unsigned delay = 0; delay < 8000; ++delay) __asm volatile("nop");
    }
}
