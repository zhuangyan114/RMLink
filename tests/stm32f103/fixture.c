/* SPDX-License-Identifier: Apache-2.0
 * Standalone acceptance fixture: HSI after reset, no GPIO/peripheral outputs.
 */
#include <stdint.h>
__attribute__((section(".state")))
volatile struct { uint32_t magic, boots, counter, guard; } fixture_state;
__attribute__((section(".capture"))) volatile uint32_t capture_values[10][16];
__attribute__((section(".pointers"))) volatile uint32_t capture_pointers[4];
typedef struct { const char *name; volatile uint8_t *buffer; uint32_t size; volatile uint32_t wr,rd; uint32_t flags; } rtt_up_t;
typedef struct { char signature[16]; uint32_t up_count,down_count; rtt_up_t up; } rtt_cb_t;
__attribute__((section(".rtt"))) volatile rtt_cb_t fixture_rtt;
__attribute__((section(".rttbuffer"))) volatile uint8_t fixture_rtt_buffer[1024];

static void publish_rtt(uint32_t seq) {
    uint32_t wr=fixture_rtt.up.wr,rd=fixture_rtt.up.rd;
    if((rd+1024-wr-1)%1024<16) return; /* Nonblocking skip. */
    uint32_t data[]={0x524d5254,seq,fixture_state.counter,0x524d5254^seq^fixture_state.counter};
    for(unsigned i=0;i<16;i++) fixture_rtt_buffer[(wr+i)%1024]=((uint8_t*)data)[i];
    __asm volatile("dmb" ::: "memory");
    fixture_rtt.up.wr=(wr+16)%1024;
}
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
    for(unsigned i=0;i<10;i++) for(unsigned j=0;j<16;j++) capture_values[i][j]=(i<<16)|j;
    capture_pointers[0]=(uint32_t)&capture_pointers[1];
    capture_pointers[1]=(uint32_t)&capture_pointers[2];
    capture_pointers[2]=(uint32_t)&capture_values[9][0];
    fixture_rtt.up_count=1; fixture_rtt.down_count=0;
    fixture_rtt.up.name=0; fixture_rtt.up.buffer=fixture_rtt_buffer;
    fixture_rtt.up.size=1024; fixture_rtt.up.wr=fixture_rtt.up.rd=0; fixture_rtt.up.flags=0;
    const char sig[]="SEGGER RTT";
    for(unsigned i=0;i<16;i++) fixture_rtt.signature[i]=i<sizeof(sig)?sig[i]:0;
    for (;;) {
        probe_step();
        for(unsigned i=0;i<10;i++) capture_values[i][0]=(i<<24)|(fixture_state.counter&0xffffff);
        publish_rtt(fixture_state.counter);
        for (volatile unsigned delay = 0; delay < 8000; ++delay) __asm volatile("nop");
    }
}
