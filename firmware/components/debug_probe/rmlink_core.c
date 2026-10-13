/* SPDX-License-Identifier: Apache-2.0 */
#include "rmlink_core.h"

bool rmlink_owner_matches(const rmlink_owner_t *o, uint8_t t, uint32_t s)
{ return o && s && o->session == s && o->transport == t; }
bool rmlink_owner_claim(rmlink_owner_t *o, uint8_t t, uint32_t s)
{
    if (!o || !s || (t != RMLINK_USB && t != RMLINK_TCP)) return false;
    if (o->session) return rmlink_owner_matches(o, t, s);
    o->session = s; o->transport = t; return true;
}
bool rmlink_owner_release(rmlink_owner_t *o, uint8_t t, uint32_t s)
{
    if (!rmlink_owner_matches(o, t, s)) return false;
    o->session = 0; o->transport = 0; return true;
}
static bool take(size_t *p, size_t n, size_t len)
{
    if (*p > len || n > len - *p) return false;
    *p += n; return true;
}
bool rmlink_dap_supported(uint8_t command)
{
    return command <= 0x0a || (command >= 0x10 && command <= 0x13) || command == 0x1d;
}
bool rmlink_dap_validate(const uint8_t *r, size_t len)
{
    if (!r || !len || len > RMLINK_PACKET_SIZE) return false;
    size_t p = 0, output = 0;
    switch (r[0]) {
    case 0x00: case 0x02: return len >= 2;
    case 0x13: return len >= 2 && !(r[1] & 0xf8);
    case 0x01: case 0x09: return len >= 3;
    case 0x03: case 0x07: case 0x0a: return true;
    case 0x04: case 0x08: return len >= 6;
    case 0x10: return len >= 7 && !(r[2] & 0x7c) &&
        ((uint32_t)r[3] | ((uint32_t)r[4] << 8) | ((uint32_t)r[5] << 16) | ((uint32_t)r[6] << 24)) <= 1000000u;
    case 0x11: {
        if (len < 5) return false;
        uint32_t hz = (uint32_t)r[1] | ((uint32_t)r[2] << 8) | ((uint32_t)r[3] << 16) | ((uint32_t)r[4] << 24);
        return hz >= 1000u && hz <= 10000000u;
    }
    case 0x12: return len >= 2 && len >= 2u + ((r[1] ? r[1] : 256u) + 7u) / 8u;
    case 0x05:
        if (len < 3) return false;
        p = 3; output = 3;
        for (unsigned i = 0; i < r[2]; ++i) {
            if (!take(&p, 1, len)) return false;
            uint8_t flags = r[p - 1];
            if ((flags & 0xc0) || ((flags & 0x10) && !(flags & 2))
                || ((flags & 0x20) && (flags & 2)) || ((flags & 0x80) && (flags & 0x30))) return false;
            if (!(flags & 2) || (flags & 0x10)) { if (!take(&p, 4, len)) return false; }
            if ((flags & 2) && !(flags & 0x10)) output += 4;
            if (flags & 0x80) output += 4;
            if (output > RMLINK_PACKET_SIZE) return false;
        }
        return true;
    case 0x06: {
        if (len < 5) return false;
        size_t count = (size_t)r[2] | ((size_t)r[3] << 8);
        if (r[4] & 0xf0) return false; /* TransferBlock only defines bits 0..3. */
        if (r[4] & 2) return count <= (RMLINK_PACKET_SIZE - 4) / 4;
        return count <= (len - 5) / 4;
    }
    case 0x1d:
        if (len < 2) return false;
        p = 2; output = 2;
        for (unsigned i = 0; i < r[1]; ++i) {
            if (!take(&p, 1, len)) return false;
            uint8_t flags = r[p - 1];
            size_t bytes = (((flags & 63) ? (flags & 63) : 64u) + 7u) / 8u;
            if (flags & 0x40) return false;
            if (flags & 0x80) output += bytes;
            else if (!take(&p, bytes, len)) return false;
            if (output > RMLINK_PACKET_SIZE) return false;
        }
        return true;
    case 0x7e: case 0x7f: return false; /* don't advertise unsupported atomic commands */
    default: return r[0] < 0x80; /* unsupported standard commands return DAP_Invalid */
    }
}
size_t rmlink_dap_error(uint8_t cmd, uint8_t *r)
{
    r[0] = cmd;
    if (cmd == 0x02) { r[1] = 0; return 2; }
    if (cmd == 0x05) { r[1] = 0; r[2] = 8; return 3; }
    if (cmd == 0x06) { r[1] = 0; r[2] = 0; r[3] = 8; return 4; }
    r[1] = 0xff; return 2;
}
bool rmlink_tcp_header_decode(const uint8_t *h, size_t *len)
{
    if (!h || !len || h[0] != 'D' || h[1] != 'A' || h[2] != 'P' || h[3] || h[6] != 1 || h[7]) return false;
    *len = (size_t)h[4] | ((size_t)h[5] << 8);
    return *len && *len <= RMLINK_PACKET_SIZE;
}
void rmlink_tcp_header_encode(uint8_t *h, size_t len)
{
    h[0] = 'D'; h[1] = 'A'; h[2] = 'P'; h[3] = 0;
    h[4] = (uint8_t)len; h[5] = (uint8_t)(len >> 8); h[6] = 2; h[7] = 0;
}
