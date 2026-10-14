/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifndef RMLINK_PACKET_SIZE
#define RMLINK_PACKET_SIZE 64u
#endif
#define RMLINK_USB_PACKET_SIZE 64u
#if RMLINK_PACKET_SIZE < 64 || RMLINK_PACKET_SIZE > 512 || (RMLINK_PACKET_SIZE & (RMLINK_PACKET_SIZE - 1))
#error "RMLINK_PACKET_SIZE must be 64, 128, 256 or 512"
#endif
#define RMLINK_TCP_HEADER_SIZE 8u
typedef enum { RMLINK_USB = 1, RMLINK_TCP = 2 } rmlink_transport_t;
typedef struct { uint32_t session; uint8_t transport; } rmlink_owner_t;
bool rmlink_owner_claim(rmlink_owner_t *owner, uint8_t transport, uint32_t session);
bool rmlink_owner_matches(const rmlink_owner_t *owner, uint8_t transport, uint32_t session);
bool rmlink_owner_release(rmlink_owner_t *owner, uint8_t transport, uint32_t session);
bool rmlink_dap_validate(const uint8_t *request, size_t len);
bool rmlink_dap_validate_for_size(const uint8_t *request, size_t len, size_t packet_size);
bool rmlink_dap_supported(uint8_t command);
size_t rmlink_dap_error(uint8_t command, uint8_t *response);
bool rmlink_tcp_header_decode(const uint8_t *header, size_t *len);
void rmlink_tcp_header_encode(uint8_t *header, size_t len);
