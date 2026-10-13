/*
 * SPDX-FileCopyrightText: 2020-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stddef.h>
#include "sdkconfig.h"

#define EUB_VENDORD_EPSIZE          64 /* full speed */
#if CONFIG_DEBUG_PROBE_IFACE_SWD
#define EUB_VENDORD_IFACE_SUBCLASS  0x00
#define EUB_VENDORD_IFACE_PROTOCOL  0x00
#else
#define EUB_VENDORD_IFACE_SUBCLASS  0xFF
#define EUB_VENDORD_IFACE_PROTOCOL  0x01
#endif
#define EUB_VENDORD_IFACE_STR_IDX   5

#define VENDOR_REQUEST_MICROSOFT 0x20  // Can be any value between 0x20 and 0xFF
#if CONFIG_RMLINK_TARGET_UART_ENABLED || CONFIG_RMLINK_MSC_ENABLED
#define MS_OS_20_DESC_LEN  0xB2
#else
#define MS_OS_20_DESC_LEN  0xA2
#endif

void eub_vendord_start(void);
int eub_vendord_send_item(const void *buf, const size_t size);
