/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
/* DWC2 assigns TX FIFO by endpoint number; ESP32-S3 has FIFO 0..4.
 * CDC uses IN1/IN2, DAP uses IN3 and MSC uses IN4. The full composite
 * profile keeps its standard DAP path when no extra IN FIFO is available. */
#if !defined(RMLINK_HOST_TEST) && CONFIG_DEBUG_PROBE_IFACE_SWD && \
    !(CONFIG_RMLINK_TARGET_UART_ENABLED && CONFIG_RMLINK_MSC_ENABLED)
#define RMLINK_USB_STREAM_AVAILABLE 1
#if CONFIG_RMLINK_MSC_ENABLED
#define RMLINK_USB_STREAM_EP 0x81
#else
#define RMLINK_USB_STREAM_EP 0x84
#endif
#else
#define RMLINK_USB_STREAM_AVAILABLE 0
#endif
