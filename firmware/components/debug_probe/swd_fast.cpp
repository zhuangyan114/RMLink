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
extern "C" { bool SWD_OptimizedActive; }
#if CONFIG_IDF_TARGET_ESP32S3 && (CONFIG_DEBUG_PROBE_IFACE_SWD || defined(RMLINK_SWD_FAST_MODEL))
extern "C" {
#include "DAP_config.h"
#include "DAP.h"
}

namespace {
uint8_t selected_level;
bool initialized;
uint32_t requested_hz = 4000000U;
uint32_t half_cycles, min_half_cycles;
/* Backend IDs 2..7: nominal 8/10/12/15/18/20 MHz. Diagnostics report
 * the calibrated rate; the 25 MHz experiment did not pass on our H723. */
uint32_t fast_half_x16[6];
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
    } else if constexpr (Bits == 32) {
        /* At N >= 4 a hardware loop leaves enough instruction slots for
         * shifting the word. This avoids duplicating 32 padded bit bodies in
         * IRAM. This file disables compiler counted-loop optimization: the asm owns
         * LCOUNT/LBEG/LEND, which this GCC does not expose as asm clobbers. */
        uint32_t low, high, bit;
        const uint32_t count = 32;
        asm volatile(
            "extui %[bit], %[value], 0, 1\n"
            "movi %[low], 0\n"
            "movnez %[low], %[dm], %[bit]\n"
            "loop %[count], 1f\n"
            "ee.wr_mask_gpio_out %[low], %[mask]\n"
            "or %[high], %[low], %[cm]\n"
            ".rept %c[n]\n nop\n .endr\n"
            "ee.wr_mask_gpio_out %[high], %[mask]\n"
            "srli %[value], %[value], 1\n"
            "extui %[bit], %[value], 0, 1\n"
            "movi %[low], 0\n"
            "movnez %[low], %[dm], %[bit]\n"
            ".rept %c[n] - 3\n nop\n .endr\n"
            "1:\n"
            : [value] "+&r"(value), [low] "=&r"(low), [high] "=&r"(high), [bit] "=&r"(bit)
            : [dm] "r"(dm), [cm] "r"(cm), [mask] "r"(mask), [count] "r"(count), [n] "i"(N)
            : "memory");
        if constexpr (Parity) write_bits<N, 1, 0>(pb, dm, cm, mask, 0, pacer);
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
    } else if constexpr (Count >= 32) {
        uint32_t data, input, bit;
        const uint32_t count = 32;
        asm volatile(
            "ssr %[shift]\n"
            "movi %[data], 0\n"
            "loop %[count], 1f\n"
            "ee.wr_mask_gpio_out %[low], %[mask]\n"
            ".rept %c[n]\n nop\n .endr\n"
            "ee.get_gpio_in %[input]\n"
            "ee.wr_mask_gpio_out %[high], %[mask]\n"
            "srli %[data], %[data], 1\n"
            "srl %[bit], %[input]\n"
            "extui %[bit], %[bit], 0, 1\n"
            "slli %[bit], %[bit], 31\n"
            "or %[data], %[data], %[bit]\n"
            ".rept %c[n] - 4\n nop\n .endr\n"
            "1:\n"
            ".set rm_tail, 0\n"
            ".rept %c[tail]\n"
            "ee.wr_mask_gpio_out %[low], %[mask]\n"
            ".rept %c[n]\n nop\n .endr\n"
            "ee.get_gpio_in %[input]\n"
            "ee.wr_mask_gpio_out %[high], %[mask]\n"
            "s32i %[input], %[raw], 4*rm_tail\n"
            ".rept %c[n]\n nop\n .endr\n"
            ".set rm_tail, rm_tail + 1\n"
            ".endr\n"
            : [data] "=&r"(data), [input] "=&r"(input), [bit] "=&r"(bit)
            : [raw] "r"(raw), [low] "r"(dm), [high] "r"(high), [mask] "r"(mask),
              [shift] "r"(shift), [count] "r"(count), [tail] "i"(Count - 32), [n] "i"(N)
            : "memory");
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
    SWD_OptimizedActive = false;
    if (!initialized || !clock) return;
    const uint32_t wanted = static_cast<uint64_t>(cpu_hz) * 8U / clock;
    /* Select the fastest calibrated level that does not exceed the request.
     * Integer instruction slots quantize the rate; use the diagnostic
     * estimate instead of treating a requested rate as a pad measurement. */
    if (clock >= 8000000U)
        for (unsigned i = 0; i < 6; ++i)
            if (fast_half_x16[i] >= wanted) selected_level = i + 2;
    half_cycles = (static_cast<uint64_t>(cpu_hz) + 2ULL * clock - 1) / (2ULL * clock);
    if (!selected_level && half_cycles >= min_half_cycles) selected_level = 1;
    SWD_OptimizedActive = selected_level != 0;
}

extern "C" void SWD_OptimizedInit(void)
{
#ifdef RMLINK_SWD_FAST_MODEL
    const uint32_t model_halves[] = {240, 192, 160, 128, 112, 96};
    for (unsigned i = 0; i < 6; ++i) fast_half_x16[i] = model_halves[i];
    min_half_cycles = 24;
#else
    debug_probe_swdio_out_disable();
    fast_half_x16[0] = measure_half_x16<13>();
    fast_half_x16[1] = measure_half_x16<10>();
    fast_half_x16[2] = measure_half_x16<8>();
    fast_half_x16[3] = measure_half_x16<6>();
    fast_half_x16[4] = measure_half_x16<5>();
    fast_half_x16[5] = measure_half_x16<4>();
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
    const uint32_t half_x16 = backend >= 2 ? fast_half_x16[backend - 2] : 0;
    out[0] = requested_hz;
    out[1] = backend == 1 ? cpu_hz / (2U * half_cycles) : half_x16 ?
        static_cast<uint64_t>(cpu_hz) * 8U / half_x16 : 0;
    out[2] = backend; out[3] = half_cycles; out[4] = min_half_cycles;
    out[5] = fast_half_x16[0]; out[6] = fast_half_x16[1];
}

extern "C" bool SWD_OptimizedTransfer(uint32_t request, uint32_t *data, uint8_t *ack)
{
    if (!selected_level || DAP_Data.swd_conf.turnaround != 1 || DAP_Data.swd_conf.data_phase)
        return false;
    switch (selected_level) {
    case 1: *ack = transfer<0>(request, data); break;
    case 2: *ack = transfer<13>(request, data); break;
    case 3: *ack = transfer<10>(request, data); break;
    case 4: *ack = transfer<8>(request, data); break;
    case 5: *ack = transfer<6>(request, data); break;
    case 6: *ack = transfer<5>(request, data); break;
    case 7: *ack = transfer<4>(request, data); break;
    default: return false;
    }
    return true;
}
#else
extern "C" void SWD_OptimizedSetClock(uint32_t) {}
extern "C" void SWD_OptimizedInit(void) {}
extern "C" void SWD_OptimizedGetTiming(uint32_t out[7]) { for (unsigned i = 0; i < 7; ++i) out[i] = 0; }
extern "C" bool SWD_OptimizedTransfer(uint32_t, uint32_t *, uint8_t *) { return false; }
#endif
