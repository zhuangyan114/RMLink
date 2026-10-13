/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "esp_err.h"
#include "rmlink_core.h"
/* Calls block until this executor owns/completes the request. No GPIO access here. */
esp_err_t rmlink_execute(uint8_t transport, uint32_t session, const uint8_t *request,
                         size_t len, uint8_t *response, size_t *response_len);
void rmlink_transport_closed(uint8_t transport, uint32_t session);
/* RX can cancel without waiting for GPIO execution. Abort has no response. */
void rmlink_abort(uint8_t transport, uint32_t session);
typedef void (*rmlink_reply_fn)(void *context, const uint8_t *data, size_t len);
void rmlink_tcp_session_open(uint32_t session);
esp_err_t rmlink_submit_tcp(uint32_t session, const uint8_t *request, size_t len,
                          rmlink_reply_fn reply, void *context);
/* Network-provisioning hook; runs in the executor, must not access target GPIO. */
size_t rmlink_vendor_command(uint8_t transport, const uint8_t *request, size_t len,
                             uint8_t *response, const rmlink_owner_t *owner);
