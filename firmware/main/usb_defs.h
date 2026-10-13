/*
 * SPDX-FileCopyrightText: 2020-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once
#include "sdkconfig.h"

// USB Interface Numbers
enum {
#if CONFIG_RMLINK_TARGET_UART_ENABLED
    ITF_NUM_CDC = 0,
    ITF_NUM_CDC_DATA,
#endif
    ITF_NUM_VENDOR,
#if CONFIG_RMLINK_MSC_ENABLED
    ITF_NUM_MSC,
#endif
    ITF_NUM_TOTAL
};

// USB Endpoint Numbers
#define EPNUM_CDC       2
#define EPNUM_VENDOR    3
#define EPNUM_MSC       4
