/* Compare the real fast protocol state machine (portable bit I/O model)
 * against the existing CMSIS-DAP engine. Assembly timing is tested on S3. */
#include <cassert>
#include <cstdio>
#include <cstring>
#define RMLINK_SWD_FAST_MODEL 1
#include "swd_fast_platform.h"
host_dap_data_t DAP_Data;
static unsigned clock_level, output_level, driving, edge_count;
static uint8_t inputs[256], edges[256];
void host_clock(unsigned level) {
    if (level && !clock_level) {
        assert(edge_count < sizeof(edges));
        edges[edge_count++] = (driving ? 2U : 0U) | output_level;
    }
    clock_level = level;
}
void host_output(unsigned value) { output_level = value; }
void host_direction(unsigned enabled) { driving = enabled; }
unsigned host_input() { assert(!driving); return inputs[edge_count]; }
#include "../../firmware/components/debug_probe/swd_fast.cpp"
#include "../../firmware/components/debug_probe/DAP/Source/SW_DP.c"
struct Result { uint8_t ack; uint32_t data, timestamp; unsigned count; uint8_t trace[256]; };
static Result run(bool optimized, uint32_t req, uint32_t value, unsigned ack,
                  unsigned idle, bool bad, bool discard) {
    clock_level = output_level = driving = 1;
    edge_count = 0;
    memset(inputs, 1, sizeof(inputs));
    for (unsigned i = 0; i < 3; ++i) inputs[9 + i] = (ack >> i) & 1U;
    for (unsigned i = 0; i < 32; ++i) inputs[12 + i] = (value >> i) & 1U;
    inputs[44] = parity(value) ^ bad;
    DAP_Data = {};
    DAP_Data.clock_delay = 7;
    DAP_Data.swd_conf.turnaround = 1;
    DAP_Data.transfer.idle_cycles = idle;
    SWD_SetClock(4000000);
    SWD_OptimizedSetClock(10000000);
    Result result = {};
    uint32_t data = value;
    if (optimized) assert(SWD_OptimizedTransfer(req, discard ? nullptr : &data, &result.ack));
    else result.ack = SWD_Transfer(req, discard ? nullptr : &data);
    result.data = data;
    result.timestamp = DAP_Data.timestamp;
    result.count = edge_count;
    memcpy(result.trace, edges, edge_count);
    assert(driving && output_level == 1 && clock_level == 1);
    return result;
}
int main() {
    uint32_t seed = 0x13579bdf;
    unsigned comparisons = 0;
    const unsigned acks[] = {0, 1, 2, 3, 4, 5, 6, 7};
    for (unsigned trial = 0; trial < 4096; ++trial) {
        seed = seed * 1664525U + 1013904223U;
        for (unsigned ack : acks) {
            const uint32_t req = (trial & 15U) | ((trial & 16U) ? DAP_TRANSFER_TIMESTAMP : 0);
            const bool discard = (req & 2) && (trial & 128U);
            Result a = run(false, req, seed, ack, trial % 5, trial & 32, discard);
            Result b = run(true, req, seed, ack, trial % 5, trial & 32, discard);
            assert(a.ack == b.ack && a.data == b.data && a.timestamp == b.timestamp);
            assert(a.count == b.count && !memcmp(a.trace, b.trace, a.count));
            ++comparisons;
        }
    }
    uint8_t ack = 0;
    for (unsigned turn = 1; turn <= 4; ++turn) for (unsigned phase = 0; phase <= 1; ++phase) {
        DAP_Data.swd_conf.turnaround = turn;
        DAP_Data.swd_conf.data_phase = phase;
        if (turn != 1 || phase) assert(!SWD_OptimizedTransfer(2, nullptr, &ack));
    }
    printf("PASS: %u fast/generic protocol traces; all ACK values, parity, timestamp, idle, discarded reads and configuration fallback\n", comparisons);
}
