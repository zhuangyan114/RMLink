/* SPDX-License-Identifier: Apache-2.0 */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "rmlink_core.h"
int main(void)
{
    rmlink_owner_t o = {0};
    assert(!rmlink_owner_claim(&o, 1, 0));
    assert(!rmlink_owner_claim(&o, 9, 1));
    assert(rmlink_owner_claim(&o, 1, 7));
    assert(rmlink_owner_claim(&o, 1, 7));
    assert(!rmlink_owner_claim(&o, 2, 7));
    assert(!rmlink_owner_claim(&o, 1, 8));
    assert(!rmlink_owner_release(&o, 2, 7));
    assert(!rmlink_owner_release(&o, 1, 8));
    assert(rmlink_owner_matches(&o, 1, 7));
    assert(rmlink_owner_release(&o, 1, 7));
    assert(rmlink_owner_claim(&o, 2, 7));
    assert(rmlink_owner_release(&o, 2, 7));

    const uint8_t golden_header[] = {0x44,0x41,0x50,0,0x40,0,1,0};
    size_t n = 0; assert(rmlink_tcp_header_decode(golden_header, &n) && n == 64);
    uint8_t h[8]; memcpy(h, golden_header, 8);
    h[4] = 65; assert(!rmlink_tcp_header_decode(h, &n));
    h[4] = 0; assert(!rmlink_tcp_header_decode(h, &n));
    h[4] = 2; h[6] = 2; assert(!rmlink_tcp_header_decode(h, &n));
    rmlink_tcp_header_encode(h, 17);
    const uint8_t expected[] = {'D','A','P',0,17,0,2,0}; assert(memcmp(h, expected, 8) == 0);

    uint8_t r[64] = {0};
    assert(!rmlink_dap_validate(r, 0)); assert(!rmlink_dap_validate(r, 65));
    r[0] = 0x11; assert(!rmlink_dap_validate(r, 5)); r[1] = 0xe8; r[2] = 3; assert(rmlink_dap_validate(r, 5));
    memset(r, 0, 64); r[0] = 0x05; r[2] = 1; r[3] = 2;
    assert(!rmlink_dap_validate(r, 3)); assert(rmlink_dap_validate(r, 4));
    r[3] = 0; assert(!rmlink_dap_validate(r, 7)); assert(rmlink_dap_validate(r, 8));
    r[3] = 0x42; assert(!rmlink_dap_validate(r, 8));
    r[3] = 0x10; assert(!rmlink_dap_validate(r, 8)); /* match value requires a read */
    r[3] = 0x22; assert(!rmlink_dap_validate(r, 8)); /* match mask requires a write */
    r[3] = 0x92; assert(!rmlink_dap_validate(r, 8)); /* timestamp cannot accompany match */
    r[3] = 0x12; assert(rmlink_dap_validate(r, 8));
    r[3] = 0x20; assert(rmlink_dap_validate(r, 8));
    memset(r, 0, 64); r[0] = 0x05; r[2] = 16; memset(r + 3, 2, 16);
    assert(!rmlink_dap_validate(r, 19)); /* 16 reads can't fit in a 64-byte response */
    r[2] = 15; assert(rmlink_dap_validate(r, 18));
    memset(r, 0, 64); r[0] = 0x06; r[2] = 15; r[4] = 2; assert(rmlink_dap_validate(r, 5));
    r[4] = 0x82; assert(!rmlink_dap_validate(r, 5)); r[4] = 2;
    r[2] = 16; assert(!rmlink_dap_validate(r, 5)); r[2] = 0xff; r[3] = 0xff;
    assert(!rmlink_dap_validate(r, 64));
    memset(r, 0, 64); r[0] = 0x1d; r[1] = 1; r[2] = 0;
    assert(!rmlink_dap_validate(r, 10)); assert(rmlink_dap_validate(r, 11));
    r[2] = 0x80; assert(rmlink_dap_validate(r, 3));
    r[0] = 0x7f; assert(!rmlink_dap_validate(r, 64));
    r[0] = 0x12; r[1] = 0; assert(!rmlink_dap_validate(r, 33)); assert(rmlink_dap_validate(r, 34));
    assert(rmlink_dap_supported(7));
    assert(!rmlink_dap_supported(0x0b));
    assert(!rmlink_dap_supported(0x14));
    assert(!rmlink_dap_supported(0x7f));
    memset(r, 0, 64); r[0] = 0x10; r[3] = 0x41; r[4] = 0x42; r[5] = 0x0f;
    assert(!rmlink_dap_validate(r, 7)); /* bounded pin wait */
    r[3] = 0x40; assert(rmlink_dap_validate(r, 7));
    r[2] = 4; assert(!rmlink_dap_validate(r, 7)); /* unavailable JTAG pin */
    uint8_t out[64]; assert(rmlink_dap_error(6, out) == 4);
    const uint8_t block_error[] = {6,0,0,8}; assert(!memcmp(out, block_error, 4));

    /* Arbitrary bounded packets exercise all parser branches, not an echo implementation. */
    uint32_t random = 0x23456789;
    for (unsigned i = 0; i < 20000; ++i) {
        for (unsigned j = 0; j < 64; ++j) { random = random * 1664525u + 1013904223u; r[j] = random >> 24; }
        (void)rmlink_dap_validate(r, i % 65);
    }
    puts("PASS: exclusive owner, TCP golden bytes, DAP bounds, 20000 parser cases");
    return 0;
}
