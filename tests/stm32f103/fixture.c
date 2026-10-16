/* SPDX-License-Identifier: Apache-2.0
 * Standalone acceptance fixture: verified 8 MHz HSE -> PLL x9 -> 72 MHz.
 * No GPIO/peripheral outputs. HSI fallback is detectable in RCC and rejected
 * by performance acceptance. Keep HSI on for the Flash programming clock.
 */
#include <stdint.h>
__attribute__((section(".state")))
volatile struct { uint32_t magic, boots, counter, guard; } fixture_state;
__attribute__((section(".capture"))) volatile uint32_t capture_values[10][16];
__attribute__((section(".pointers"))) volatile uint32_t capture_pointers[4];
/* Typed DWARF view of the existing three-pointer chain. The alias adds no RAM
 * storage or target instructions, so direct/pointer tests keep one Flash image. */
typedef struct { uint32_t values[16]; } pointer_leaf_t;
typedef struct { volatile pointer_leaf_t *leaf; } pointer_middle_t;
typedef struct { volatile pointer_middle_t *next; } pointer_root_t;
extern volatile pointer_root_t *capture_root __attribute__((alias("capture_pointers")));
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
static int clock_wait(volatile uint32_t *reg,uint32_t mask,uint32_t expected) {
    for(unsigned n=0;n<1000000;n++)if((*reg&mask)==expected)return 1;
    return 0;
}
static unsigned clock_72mhz(void) {
    volatile uint32_t *cr=(uint32_t*)0x40021000,*cfgr=(uint32_t*)0x40021004;
    *(volatile uint32_t*)0x40022000=0x12; /* prefetch, 2 Flash wait states */
    *cr|=1;if(!clock_wait(cr,2,2))return 8;
    *cfgr&=~3u;if(!clock_wait(cfgr,12,0))return 8;
    *cr&=~(1u<<24);if(!clock_wait(cr,1u<<25,0))return 8;
    *cr|=1u<<16;if(!clock_wait(cr,1u<<17,1u<<17))return 8;
    /* AHB/APB2 72 MHz; APB1 36 MHz; ADC 12 MHz; USB 48 MHz. */
    *cfgr=0x001d8400;
    *cr|=1u<<24;if(!clock_wait(cr,1u<<25,1u<<25))return 8;
    *cfgr=(*cfgr&~3u)|2;
    if(!clock_wait(cfgr,12,8))return 8;
    return 72;
}
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
    unsigned mhz=clock_72mhz();
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
        /* Preserve roughly the previous producer load instead of emitting RTT
         * nine times faster solely because the CPU clock changed. */
        for (volatile unsigned delay = 0; delay < 8000*(mhz/8); ++delay) __asm volatile("nop");
    }
}
