/* SPDX-License-Identifier: Apache-2.0
 * Bit scheduling adapted from Jiu-xiao/XRDAP-ESP firmware/esp32sx/main/swd_fast.cpp
 * (Apache-2.0, local reference 75a7f2c). RMLink retains its CMSIS-DAP executor,
 * ACK values, idle cycles, timestamps and configurable generic fallback.
 */
#ifdef RMLINK_SWD_FAST_MODEL
#include "swd_fast_platform.h"
#else
#include "sdkconfig.h"
#endif
#include "swd_fast.h"
#include "rmlink_swd_timing.h"
#include "esp_cpu.h"
#if CONFIG_IDF_TARGET_ESP32S3
extern "C" {
#include "DAP_config.h"
#include "DAP.h"
}

namespace {
uint8_t selected_level;
bool initialized;
uint32_t requested_hz = 4000000U;
uint32_t half_cycles, min_half_cycles;
uint32_t fast8_half_x16, fast10_half_x16;
#ifdef RMLINK_SWD_FAST_MODEL
constexpr uint32_t cpu_hz = 240000000U;
#else
constexpr uint32_t cpu_hz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ * 1000000U;
#endif

struct Pacer { uint32_t next, half; };
static inline __attribute__((always_inline)) void advance(Pacer &p, uint32_t now)
{
    /* Normal wait granularity does not accumulate. After a late interrupt or
     * computation, restart at now instead of compressing the next half-cycle. */
    p.next = (static_cast<int32_t>(now - p.next) > 4 ? now : p.next) + p.half;
}
static inline __attribute__((always_inline)) uint32_t wait_edge(const Pacer &p)
{
    uint32_t now;
    do { now = esp_cpu_get_cycle_count(); }
    while (static_cast<int32_t>(now - p.next) < 0);
    return now;
}
static inline __attribute__((always_inline)) void paced_edge(Pacer &p, uint32_t mask, uint32_t value)
{
    const uint32_t now = wait_edge(p);
    dedic_gpio_cpu_ll_write_mask(mask, value);
    advance(p, now);
}

static inline __attribute__((always_inline)) uint32_t parity(uint32_t value)
{
    value ^= value >> 16;
    value ^= value >> 8;
    value ^= value >> 4;
    value ^= value >> 2;
    return (value ^ (value >> 1)) & 1U;
}

/* The falling edge and data are one write. Both halves have N+2 instruction
 * slots; preparation of the next bit occupies the otherwise idle high half.
 * Masked writes preserve other dedicated outputs on the same CPU. */
template<int N, int Bits, int Parity>
static inline __attribute__((always_inline)) void write_bits(
    uint32_t value, uint32_t dm, uint32_t cm, uint32_t mask, uint32_t pb, Pacer *pacer)
{
#ifdef RMLINK_SWD_FAST_MODEL
    (void)pacer;
    for (int i = 0; i < Bits + Parity; ++i) {
        const uint32_t bit = i < Bits ? (value >> i) & 1U : pb;
        dedic_gpio_cpu_ll_write_mask(mask, bit ? dm : 0);
        dedic_gpio_cpu_ll_write_mask(mask, (bit ? dm : 0) | cm);
    }
#else
    if constexpr (N == 0) {
        for (int i = 0; i < Bits + Parity; ++i) {
            const uint32_t bit = i < Bits ? (value >> i) & 1U : pb;
            const uint32_t low = bit ? dm : 0;
            paced_edge(*pacer, mask, low);
            paced_edge(*pacer, mask, low | cm);
        }
    } else {
    uint32_t low, high, bit;
    asm volatile(
        "extui %[bit], %[value], 0, 1\n"
        "movi %[low], 0\n"
        "movnez %[low], %[dm], %[bit]\n"
        ".set rm_bit, 0\n"
        ".rept %c[count]\n"
        "ee.wr_mask_gpio_out %[low], %[mask]\n"
        "or %[high], %[low], %[cm]\n"
        ".rept %c[n]\n nop\n .endr\n"
        "ee.wr_mask_gpio_out %[high], %[mask]\n"
        ".if rm_bit + 1 < %c[bits]\n"
        "extui %[bit], %[value], rm_bit + 1, 1\n"
        "movi %[low], 0\n"
        "movnez %[low], %[dm], %[bit]\n"
        ".rept %c[n] - 2\n nop\n .endr\n"
        ".elseif (rm_bit + 1 == %c[bits]) && %c[parity]\n"
        "movi %[low], 0\n"
        "movnez %[low], %[dm], %[pb]\n"
        ".rept %c[n] - 1\n nop\n .endr\n"
        ".else\n"
        ".rept %c[n] + 1\n nop\n .endr\n"
        ".endif\n"
        ".set rm_bit, rm_bit + 1\n"
        ".endr\n"
        : [low] "=&r"(low), [high] "=&r"(high), [bit] "=&r"(bit)
        : [value] "r"(value), [dm] "r"(dm), [cm] "r"(cm), [mask] "r"(mask),
          [pb] "r"(pb), [count] "i"(Bits + Parity), [bits] "i"(Bits),
          [parity] "i"(Parity), [n] "i"(N)
        : "memory");
    }
#endif
}

/* Sample immediately before the rising edge. Assemble the first 32 bits in
 * the high halves; raw[0..1] hold parity/turnaround when Count is 34. */
template<int N, int Count>
static inline __attribute__((always_inline)) uint32_t read_bits(
    uint32_t *raw, uint32_t dm, uint32_t high, uint32_t mask, uint32_t shift, Pacer *pacer)
{
#ifdef RMLINK_SWD_FAST_MODEL
    (void)pacer;
    uint32_t data = 0;
    for (int i = 0; i < Count; ++i) {
        dedic_gpio_cpu_ll_write_mask(mask, dm);
        const uint32_t input = host_input() << shift;
        dedic_gpio_cpu_ll_write_mask(mask, high);
        if (i < 32) data |= ((input >> shift) & 1U) << i;
        else raw[i - 32] = input;
    }
    return data;
#else
    if constexpr (N == 0) {
        uint32_t data = 0;
        for (int i = 0; i < Count; ++i) {
            paced_edge(*pacer, mask, dm);
            const uint32_t now = wait_edge(*pacer);
            const uint32_t input = dedic_gpio_cpu_ll_read_in();
            dedic_gpio_cpu_ll_write_mask(mask, high);
            advance(*pacer, now);
            if (i < 32) data |= ((input >> shift) & 1U) << i;
            else raw[i - 32] = input;
        }
        return data;
    } else {
    uint32_t data, input, bit;
    asm volatile(
        "ssr %[shift]\n"
        "movi %[data], 0\n"
        ".set rm_bit, 0\n"
        ".rept %c[count]\n"
        "ee.wr_mask_gpio_out %[low], %[mask]\n"
        ".rept %c[n]\n nop\n .endr\n"
        "ee.get_gpio_in %[input]\n"
        "ee.wr_mask_gpio_out %[high], %[mask]\n"
        ".if rm_bit < 32\n"
        "srl %[bit], %[input]\n"
        "extui %[bit], %[bit], 0, 1\n"
        ".if rm_bit > 0\n"
        "slli %[bit], %[bit], rm_bit\n"
        ".rept %c[n] - 3\n nop\n .endr\n"
        ".else\n"
        ".rept %c[n] - 2\n nop\n .endr\n"
        ".endif\n"
        "or %[data], %[data], %[bit]\n"
        ".else\n"
        "s32i %[input], %[raw], 4*(rm_bit-32)\n"
        ".rept %c[n]\n nop\n .endr\n"
        ".endif\n"
        ".set rm_bit, rm_bit + 1\n"
        ".endr\n"
        : [data] "=&r"(data), [input] "=&r"(input), [bit] "=&r"(bit)
        : [raw] "r"(raw), [low] "r"(dm), [high] "r"(high), [mask] "r"(mask),
          [shift] "r"(shift), [count] "i"(Count), [n] "i"(N)
        : "memory");
    return data;
    }
#endif
}

template<int N>
static uint8_t transfer(uint32_t request, uint32_t *data)
{
    const uint32_t dm = rmlink_swdio_out_mask;
    const uint32_t cm = rmlink_swclk_out_mask;
    const uint32_t mask = dm | cm;
    const uint32_t shift = __builtin_ctz(rmlink_swdio_in_mask);
    uint32_t raw[2];
    Pacer pacer{esp_cpu_get_cycle_count(), half_cycles};
    const uint32_t req = 0x81U | ((request & 15U) << 1) | (parity(request & 15U) << 5);
    debug_probe_swdio_out_enable();
    write_bits<N, 8, 0>(req, dm, cm, mask, 0, &pacer);
    debug_probe_swdio_out_disable();
    const uint8_t ack = (read_bits<N, 4>(raw, dm, dm | cm, mask, shift, &pacer) >> 1) & 7U;
    if (ack != DAP_TRANSFER_OK) {
        if (ack == DAP_TRANSFER_WAIT || ack == DAP_TRANSFER_FAULT)
            (void)read_bits<N, 1>(raw, dm, dm | cm, mask, shift, &pacer);
        else
            (void)read_bits<N, 34>(raw, dm, dm | cm, mask, shift, &pacer);
        debug_probe_swdio_out_enable();
        /* Keep the generic engine's high park level, including error returns. */
        dedic_gpio_cpu_ll_write_mask(mask, dm | cm);
        return ack;
    }
    uint8_t result = ack;
    if (request & DAP_TRANSFER_RnW) {
        const uint32_t value = read_bits<N, 34>(raw, dm, dm | cm, mask, shift, &pacer);
        debug_probe_swdio_out_enable();
        if (data) *data = value;
        if (((raw[0] >> shift) & 1U) != parity(value)) result = DAP_TRANSFER_ERROR;
    } else {
        (void)read_bits<N, 1>(raw, dm, dm | cm, mask, shift, &pacer);
        debug_probe_swdio_out_enable();
        write_bits<N, 32, 1>(*data, dm, cm, mask, parity(*data), &pacer);
    }
    if (request & DAP_TRANSFER_TIMESTAMP) DAP_Data.timestamp = TIMESTAMP_GET();
    for (uint32_t n = DAP_Data.transfer.idle_cycles; n; --n)
        write_bits<N, 1, 0>(0, dm, cm, mask, 0, &pacer);
    dedic_gpio_cpu_ll_write_mask(mask, dm | cm);
    return result;
}

template<int N>
uint32_t measure_half_x16()
{
    uint32_t raw[2], best = UINT32_MAX;
    for (unsigned i = 0; i < 4; ++i) {
        uint32_t start = esp_cpu_get_cycle_count();
        (void)read_bits<N, 32>(raw, rmlink_swdio_out_mask,
            rmlink_swdio_out_mask | rmlink_swclk_out_mask,
            rmlink_swdio_out_mask | rmlink_swclk_out_mask,
            __builtin_ctz(rmlink_swdio_in_mask), nullptr);
        uint32_t used = esp_cpu_get_cycle_count() - start;
        if (used < best) best = used;
    }
    return (best * 16U + 32U) / 64U;
}
} // namespace

extern "C" void SWD_OptimizedSetClock(uint32_t clock)
{
    requested_hz = clock;
    selected_level = 0;
    if (!initialized || !clock) return;
    const uint32_t wanted = static_cast<uint64_t>(cpu_hz) * 8U / clock;
    /* Each selected fast level is no faster than the requested rate according
     * to the calibration. Only these two unrolled levels occupy IRAM. */
    if (clock >= 8000000U && fast8_half_x16 >= wanted) selected_level = 2;
    if (clock >= 8000000U && fast10_half_x16 >= wanted) selected_level = 3;
    half_cycles = (static_cast<uint64_t>(cpu_hz) + 2ULL * clock - 1) / (2ULL * clock);
    if (!selected_level && half_cycles >= min_half_cycles) selected_level = 1;
}

extern "C" void SWD_OptimizedInit(void)
{
#ifdef RMLINK_SWD_FAST_MODEL
    fast8_half_x16 = 240;
    fast10_half_x16 = 192;
    min_half_cycles = 24;
#else
    debug_probe_swdio_out_disable();
    fast8_half_x16 = measure_half_x16<13>();
    fast10_half_x16 = measure_half_x16<10>();
    const uint32_t dm = rmlink_swdio_out_mask, mask = dm | rmlink_swclk_out_mask;
    const uint32_t shift = __builtin_ctz(rmlink_swdio_in_mask);
    min_half_cycles = 80;
    for (uint32_t half = 8; half < 80; ++half) {
        uint32_t best = UINT32_MAX;
        for (unsigned i = 0; i < 4; ++i) {
            uint32_t raw[2];
            Pacer p{esp_cpu_get_cycle_count(), half};
            const uint32_t start = esp_cpu_get_cycle_count();
            (void)read_bits<0, 32>(raw, dm, mask, mask, shift, &p);
            write_bits<0, 32, 0>(0xa5a5a5a5U, dm, rmlink_swclk_out_mask, mask, 0, &p);
            paced_edge(p, mask, dm);
            const uint32_t used = esp_cpu_get_cycle_count() - start;
            if (used < best) best = used;
        }
        if (best <= half * 129U + 24U) { min_half_cycles = half; break; }
    }
    dedic_gpio_cpu_ll_write_mask(mask, dm);
    debug_probe_swdio_out_enable();
#endif
    initialized = true;
    SWD_OptimizedSetClock(requested_hz);
}

extern "C" void SWD_OptimizedGetTiming(uint32_t out[7])
{
    uint32_t backend = selected_level;
    if (DAP_Data.swd_conf.turnaround != 1 || DAP_Data.swd_conf.data_phase) backend = 0;
    const uint32_t half_x16 = backend == 2 ? fast8_half_x16 : backend == 3 ? fast10_half_x16 : 0;
    out[0] = requested_hz;
    out[1] = backend == 1 ? cpu_hz / (2U * half_cycles) : half_x16 ?
        static_cast<uint64_t>(cpu_hz) * 8U / half_x16 : 0;
    out[2] = backend; out[3] = half_cycles; out[4] = min_half_cycles;
    out[5] = fast8_half_x16; out[6] = fast10_half_x16;
}

extern "C" bool SWD_OptimizedTransfer(uint32_t request, uint32_t *data, uint8_t *ack)
{
    if (!selected_level || DAP_Data.swd_conf.turnaround != 1 || DAP_Data.swd_conf.data_phase)
        return false;
    *ack = selected_level == 1 ? transfer<0>(request, data) :
        selected_level == 3 ? transfer<10>(request, data) : transfer<13>(request, data);
    return true;
}
#else
extern "C" void SWD_OptimizedSetClock(uint32_t) {}
extern "C" void SWD_OptimizedInit(void) {}
extern "C" void SWD_OptimizedGetTiming(uint32_t out[7]) { for (unsigned i = 0; i < 7; ++i) out[i] = 0; }
extern "C" bool SWD_OptimizedTransfer(uint32_t, uint32_t *, uint8_t *) { return false; }
#endif
