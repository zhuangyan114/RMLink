/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stddef.h>
#include <stdint.h>

/* Lossless, in-place RML1 -> RMC2 groups. Returns 0 on invalid input.
 * This only changes transport framing, never target IO or sample contents. */
size_t rmlink_stream_pack(uint8_t *data,size_t length);

/* Negotiated RMS3: independent groups, bytewise XOR against the preceding
 * payload and bounded zero-run encoding. Raw payloads remain a fallback.
 * Restoring a group reproduces every original RML1 byte, including its CRC. */
size_t rmlink_stream_pack_delta(uint8_t *data,size_t length);
