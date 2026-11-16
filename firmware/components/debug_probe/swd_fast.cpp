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
#if CONFIG_IDF_TARGET_ESP32S3
extern "C" {
#include "DAP_config.h"
#include "DAP.h"
}

namespace {
uint8_t selected_level;

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
    uint32_t value, uint32_t dm, uint32_t cm, uint32_t mask, uint32_t pb)
{
#ifdef RMLINK_SWD_FAST_MODEL
    for (int i = 0; i < Bits + Parity; ++i) {
        const uint32_t bit = i < Bits ? (value >> i) & 1U : pb;
        dedic_gpio_cpu_ll_write_mask(mask, bit ? dm : 0);
        dedic_gpio_cpu_ll_write_mask(mask, (bit ? dm : 0) | cm);
    }
#else
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
#endif
}

/* Sample immediately before the rising edge. Assemble the first 32 bits in
 * the high halves; raw[0..1] hold parity/turnaround when Count is 34. */
template<int N, int Count>
static inline __attribute__((always_inline)) uint32_t read_bits(
    uint32_t *raw, uint32_t dm, uint32_t high, uint32_t mask, uint32_t shift)
{
#ifdef RMLINK_SWD_FAST_MODEL
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
    const uint32_t req = 0x81U | ((request & 15U) << 1) | (parity(request & 15U) << 5);
    debug_probe_swdio_out_enable();
    write_bits<N, 8, 0>(req, dm, cm, mask, 0);
    debug_probe_swdio_out_disable();
    const uint8_t ack = (read_bits<N, 4>(raw, dm, dm | cm, mask, shift) >> 1) & 7U;
    if (ack != DAP_TRANSFER_OK) {
        if (ack == DAP_TRANSFER_WAIT || ack == DAP_TRANSFER_FAULT)
            (void)read_bits<N, 1>(raw, dm, dm | cm, mask, shift);
        else
            (void)read_bits<N, 34>(raw, dm, dm | cm, mask, shift);
        debug_probe_swdio_out_enable();
        /* Keep the generic engine's high park level, including error returns. */
        dedic_gpio_cpu_ll_write_mask(mask, dm | cm);
        return ack;
    }
    uint8_t result = ack;
    if (request & DAP_TRANSFER_RnW) {
        const uint32_t value = read_bits<N, 34>(raw, dm, dm | cm, mask, shift);
        debug_probe_swdio_out_enable();
        if (data) *data = value;
        if (((raw[0] >> shift) & 1U) != parity(value)) result = DAP_TRANSFER_ERROR;
    } else {
        (void)read_bits<N, 1>(raw, dm, dm | cm, mask, shift);
        debug_probe_swdio_out_enable();
        write_bits<N, 32, 1>(*data, dm, cm, mask, parity(*data));
    }
    if (request & DAP_TRANSFER_TIMESTAMP) DAP_Data.timestamp = TIMESTAMP_GET();
    for (uint32_t n = DAP_Data.transfer.idle_cycles; n; --n)
        write_bits<N, 1, 0>(0, dm, cm, mask, 0);
    dedic_gpio_cpu_ll_write_mask(mask, dm | cm);
    return result;
}
} // namespace

extern "C" void SWD_OptimizedSetClock(uint32_t clock)
{
    /* Only common fast levels occupy IRAM. Slower requests retain generic I/O. */
    selected_level = clock >= 10000000U ? 2 : clock >= 8000000U ? 1 : 0;
}

extern "C" bool SWD_OptimizedTransfer(uint32_t request, uint32_t *data, uint8_t *ack)
{
    if (!selected_level || DAP_Data.swd_conf.turnaround != 1 || DAP_Data.swd_conf.data_phase)
        return false;
    *ack = selected_level == 2 ? transfer<10>(request, data) : transfer<13>(request, data);
    return true;
}
#else
extern "C" void SWD_OptimizedSetClock(uint32_t) {}
extern "C" bool SWD_OptimizedTransfer(uint32_t, uint32_t *, uint8_t *) { return false; }
#endif
