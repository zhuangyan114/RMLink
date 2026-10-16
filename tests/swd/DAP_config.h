#pragma once
#include <stdint.h>
#define DAP_SWD 1
#define DAP_JTAG 0
void host_clock(unsigned level);
void host_output(unsigned value);
void host_direction(unsigned enabled);
unsigned host_input(void);
#define PIN_SWCLK_TCK_SET() host_clock(1)
#define PIN_SWCLK_TCK_CLR() host_clock(0)
#define PIN_SWDIO_OUT(value) host_output((value)&1U)
#define PIN_SWDIO_IN() host_input()
#define PIN_SWDIO_OUT_ENABLE() host_direction(1)
#define PIN_SWDIO_OUT_DISABLE() host_direction(0)
#define PIN_SWDIO_TMS_SET() host_output(1)
#define PIN_SWDIO_TMS_CLR() host_output(0)
