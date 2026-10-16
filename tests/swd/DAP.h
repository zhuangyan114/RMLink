/* Minimal host surface for compiling the actual SW_DP.c pin-level engine. */
#pragma once
#include <stdint.h>
#define DAP_TRANSFER_OK 1U
#define DAP_TRANSFER_WAIT 2U
#define DAP_TRANSFER_FAULT 4U
#define DAP_TRANSFER_ERROR 8U
#define DAP_TRANSFER_RnW 2U
#define DAP_TRANSFER_TIMESTAMP 0x80U
#define SWD_SEQUENCE_CLK 0x3fU
#define SWD_SEQUENCE_DIN 0x80U
typedef struct {
    uint8_t fast_clock;
    uint32_t clock_delay,timestamp;
    struct {uint8_t turnaround,data_phase;} swd_conf;
    struct {uint8_t idle_cycles;} transfer;
} host_dap_data_t;
extern host_dap_data_t DAP_Data;
typedef struct {
    uint32_t transfers,waits,errors;
    uint64_t elapsed_cycles,wire_clocks;
} DAP_SWDMetrics_t;
#define PIN_DELAY_SLOW(value) ((void)(value))
#define PIN_DELAY_FAST() ((void)0)
#define TIMESTAMP_GET() 123456U
