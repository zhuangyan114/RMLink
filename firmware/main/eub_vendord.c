/*
 * SPDX-FileCopyrightText: 2020-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "sdkconfig.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "tusb.h"
#include "device/usbd_pvt.h"
#include "tusb_config.h"
#include "esp_timer.h"

#include "eub_vendord.h"
#include "debug_probe.h"
#include "rmlink_executor.h"
#include "usb_defs.h"
#include "util.h"

static const char *TAG = "eub_vendor";
#define USB_BUSY_TOUT_US    100000 /* 100ms */

typedef struct {
    uint8_t ep_in;
    uint8_t ep_out;
    TaskHandle_t usb_tx_task_handle;
    uint8_t epout_buf[EUB_VENDORD_EPSIZE];  /* Endpoint Transfer buffer */
} eub_vendord_interface_t;

static eub_vendord_interface_t s_eub_vendord_itf;
static const uint8_t s_rhport = 0;

static void eub_vendord_init(void)
{
    ESP_LOGD(TAG, "%s", __func__);

    memset(&s_eub_vendord_itf, 0x00, sizeof(s_eub_vendord_itf));
}

static void eub_vendord_reset(uint8_t rhport)
{
    ESP_LOGD(TAG, "%s", __func__);

    s_eub_vendord_itf.ep_in = 0;
    s_eub_vendord_itf.ep_out = 0;
#if CONFIG_DEBUG_PROBE_IFACE_SWD
    rmlink_transport_closed(RMLINK_USB, 0);
#endif
    /* do not reset the FreeRTOS handlers */
}

static uint16_t eub_vendord_open(uint8_t rhport, tusb_desc_interface_t const *desc_intf, uint16_t max_len)
{
    ESP_LOGD(TAG, "%s", __func__);

    TU_VERIFY(TUSB_CLASS_VENDOR_SPECIFIC == desc_intf->bInterfaceClass &&
              EUB_VENDORD_IFACE_SUBCLASS == desc_intf->bInterfaceSubClass &&
              EUB_VENDORD_IFACE_PROTOCOL == desc_intf->bInterfaceProtocol &&
              EUB_VENDORD_IFACE_STR_IDX == desc_intf->iInterface, 0);

    uint8_t const *p_desc = tu_desc_next(desc_intf);
    eub_vendord_interface_t *p_vendor = &s_eub_vendord_itf;

    // Open endpoint pair with usbd helper
    usbd_open_edpt_pair(rhport, p_desc, desc_intf->bNumEndpoints, TUSB_XFER_BULK, &p_vendor->ep_out, &p_vendor->ep_in);

    ESP_LOGI(TAG, "EP_OUT:0x%x EP_IN:0x%x", p_vendor->ep_out, p_vendor->ep_in);

    p_desc += desc_intf->bNumEndpoints * sizeof(tusb_desc_endpoint_t);

    // Prepare for incoming data
    if (p_vendor->ep_out) {
        usbd_edpt_xfer(rhport, p_vendor->ep_out, p_vendor->epout_buf, sizeof(p_vendor->epout_buf));
    }

    return (uint16_t)((uintptr_t)p_desc - (uintptr_t)desc_intf);
}

static const uint8_t desc_ms_os_20[] = {
    // Microsoft OS 2.0 Descriptor
    // Values are taken from https://github.com/hathach/tinyusb/tree/master/examples/device/webusb_serial
    //
    // Set header: length, type, windows version, total length
    // 0x000A = size of the header
    // MS_OS_20_SET_HEADER_DESCRIPTOR = 0x00 (descriptor type)
    // 0x06030000 = Windows version 6.3 (Windows 8.1 and later)
    // MS_OS_20_DESC_LEN = total length of all descriptors
    U16_TO_U8S_LE(0x000A), U16_TO_U8S_LE(MS_OS_20_SET_HEADER_DESCRIPTOR), U32_TO_U8S_LE(0x06030000), U16_TO_U8S_LE(MS_OS_20_DESC_LEN),

#if CONFIG_RMLINK_TARGET_UART_ENABLED || CONFIG_RMLINK_MSC_ENABLED
    // Configuration/function subsets apply to the optional composite profile.
    // The single-interface profile declares compatible ID at device scope.
    // Configuration subset header
    // 0x0008 = size of the header
    // MS_OS_20_SUBSET_HEADER_CONFIGURATION = 0x01 (descriptor type)
    // 0 = configuration index (first configuration)
    // 0 = reserved
    // MS_OS_20_DESC_LEN - 0x0A = remaining length (total - header length)
    U16_TO_U8S_LE(0x0008), U16_TO_U8S_LE(MS_OS_20_SUBSET_HEADER_CONFIGURATION), 0, 0, U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A),

    // Function Subset header
    // 0x0008 = size of the header
    // MS_OS_20_SUBSET_HEADER_FUNCTION = 0x02 (descriptor type)
    // ITF_NUM_VENDOR = interface number for vendor class
    // 0 = reserved
    // MS_OS_20_DESC_LEN - 0x0A - 0x08 = remaining length (total - header - config subset)
    U16_TO_U8S_LE(0x0008), U16_TO_U8S_LE(MS_OS_20_SUBSET_HEADER_FUNCTION), ITF_NUM_VENDOR, 0, U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A - 0x08),
#endif

    // MS OS 2.0 Compatible ID descriptor
    // 0x0014 = size of the descriptor
    // MS_OS_20_FEATURE_COMPATBLE_ID = 0x03 (descriptor type)
    // 'WINUSB' = compatible ID string
    // 8 bytes of zeros = sub-compatible ID (not used)
    U16_TO_U8S_LE(0x0014), U16_TO_U8S_LE(MS_OS_20_FEATURE_COMPATBLE_ID), 'W', 'I', 'N', 'U', 'S', 'B', 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, // sub-compatible

    // MS OS 2.0 Registry property descriptor
    // length = remaining bytes (total - all previous sections)
    // MS_OS_20_FEATURE_REG_PROPERTY = 0x04 (descriptor type)
    // 0x0007 = REG_MULTI_SZ (property data type)
    // 0x002A = 42 bytes for property name length ("DeviceInterfaceGUIDs" in UTF-16)
    U16_TO_U8S_LE(132), U16_TO_U8S_LE(MS_OS_20_FEATURE_REG_PROPERTY),
    U16_TO_U8S_LE(0x0007), U16_TO_U8S_LE(0x002A), // wPropertyDataType, wPropertyNameLength and PropertyName "DeviceInterfaceGUIDs\0" in UTF-16
    'D', 0x00, 'e', 0x00, 'v', 0x00, 'i', 0x00, 'c', 0x00, 'e', 0x00, 'I', 0x00, 'n', 0x00, 't', 0x00, 'e', 0x00,
    'r', 0x00, 'f', 0x00, 'a', 0x00, 'c', 0x00, 'e', 0x00, 'G', 0x00, 'U', 0x00, 'I', 0x00, 'D', 0x00, 's', 0x00, 0x00, 0x00,
    // 0x0050 = 80 bytes for property data length (GUID string in UTF-16)
    U16_TO_U8S_LE(0x0050), // wPropertyDataLength
    // Common CMSIS-DAP WinUSB interface GUID (REG_MULTI_SZ, two NULs).
    0x7b, 0x00, 0x43, 0x00, 0x44, 0x00, 0x42, 0x00, 0x33, 0x00, 0x42, 0x00, 0x35, 0x00, 0x41, 0x00,
    0x44, 0x00, 0x2d, 0x00, 0x32, 0x00, 0x39, 0x00, 0x33, 0x00, 0x42, 0x00, 0x2d, 0x00, 0x34, 0x00,
    0x36, 0x00, 0x36, 0x00, 0x33, 0x00, 0x2d, 0x00, 0x41, 0x00, 0x41, 0x00, 0x33, 0x00, 0x36, 0x00,
    0x2d, 0x00, 0x31, 0x00, 0x41, 0x00, 0x41, 0x00, 0x45, 0x00, 0x34, 0x00, 0x36, 0x00, 0x34, 0x00,
    0x36, 0x00, 0x33, 0x00, 0x37, 0x00, 0x37, 0x00, 0x36, 0x00, 0x7d, 0x00, 0x00, 0x00, 0x00, 0x00,
};

TU_VERIFY_STATIC(sizeof(desc_ms_os_20) == MS_OS_20_DESC_LEN, "Incorrect size");

bool tud_vendor_control_xfer_cb(const uint8_t rhport, const uint8_t stage, tusb_control_request_t const *request)
{
    // nothing to with DATA & ACK stage
    if (stage != CONTROL_STAGE_SETUP) {
        return true;
    }

    switch (request->bmRequestType_bit.type) {
    case TUSB_REQ_TYPE_VENDOR:
        ESP_LOGI(TAG, "bRequest: (%d) wValue: (%d) wIndex: (%d)",
                 request->bRequest, request->wValue, request->wIndex);

        switch (request->bRequest) {

        case VENDOR_REQUEST_MICROSOFT:
            return tud_control_xfer(rhport, request, (void *)(uintptr_t)desc_ms_os_20, MS_OS_20_DESC_LEN);
        default:
            debug_probe_cmd_response_t response = debug_probe_handle_command(
                    request->bRequest,
                    request->wValue
                                                  );

            if (!response.success) {
                return false;
            }

            // Handle USB response
            if (response.data_len > 0) {
                return tud_control_xfer(rhport, request, (void *)response.data, response.data_len);
            } else {
                return tud_control_status(rhport, request);
            }
        }

    default:
        return false;
    }
}

static bool eub_endpt_transfer(uint8_t rhport, uint8_t ep_addr, uint8_t *buf, uint16_t total_bytes)
{
    uint64_t end = esp_timer_get_time() + USB_BUSY_TOUT_US;

    while (usbd_edpt_busy(rhport, ep_addr)) {
        if (esp_timer_get_time() > end) {
            ESP_LOGE(TAG, "usbd_edpt_busy timeout!");
            return false;
        }
        if (tu_edpt_dir(ep_addr) == TUSB_DIR_IN &&
            xTaskGetCurrentTaskHandle() == s_eub_vendord_itf.usb_tx_task_handle) {
            /* Completion wakes immediately instead of imposing a 10 ms RTOS tick. */
            ulTaskNotifyTake(pdTRUE, 1);
        } else {
            vTaskDelay(1);
        }
    }

    /* Keep DMA data alive until the endpoint completes. Ring items may be returned
     * immediately after this call, so the IN endpoint owns a separate buffer. */
    static uint8_t epin_buf[EUB_VENDORD_EPSIZE] TU_ATTR_ALIGNED(4);
    if (tu_edpt_dir(ep_addr) == TUSB_DIR_IN) {
        if (total_bytes > sizeof(epin_buf)) return false;
        memcpy(epin_buf, buf, total_bytes);
        buf = epin_buf;
    }
    return usbd_edpt_xfer(rhport, ep_addr, buf, total_bytes);
}

static bool eub_vendord_xfer_cb(uint8_t rhport, uint8_t ep_addr, xfer_result_t result, uint32_t xferred_bytes)
{
    TU_VERIFY(xferred_bytes > 0 && xferred_bytes <= 64, false);
    TU_VERIFY(ep_addr == s_eub_vendord_itf.ep_out || ep_addr == s_eub_vendord_itf.ep_in, false);

    const uint8_t ep_dir = tu_edpt_dir(ep_addr);

    ESP_LOGD(TAG, "%s xfer from ep:%x recvd:%" PRId32 " bytes", __func__, ep_addr, xferred_bytes);

    if (ep_dir == TUSB_DIR_IN) {
        if (s_eub_vendord_itf.usb_tx_task_handle) xTaskNotifyGive(s_eub_vendord_itf.usb_tx_task_handle);
        return true;
    } else if (ep_dir == TUSB_DIR_OUT) {
        esp_err_t process_result = debug_probe_process_data(s_eub_vendord_itf.epout_buf, xferred_bytes);
        if (process_result != ESP_OK) {
            ESP_LOGW(TAG, "Debug probe failed to process data: %s", esp_err_to_name(process_result));
            return false;
        }

        // Prepare for next incoming data
        if (!eub_endpt_transfer(rhport, ep_addr, s_eub_vendord_itf.epout_buf, EUB_VENDORD_EPSIZE)) {
            ESP_LOGE(TAG, "USB transfer error on EP:%x", ep_addr);
            eub_abort();
        }

        return true;
    }

    return false;
}

static void usb_send_task(void *pvParameters)
{
    size_t total_bytes;

    ESP_LOGI(TAG, "usb_send_task is ready!");

    for (;;) {
        uint8_t *buf_copy = debug_probe_get_data_to_send(&total_bytes, portMAX_DELAY);

        if (!buf_copy) continue;
        if (tud_mounted() && s_eub_vendord_itf.ep_in &&
            !eub_endpt_transfer(s_rhport, s_eub_vendord_itf.ep_in, buf_copy, total_bytes)) {
            ESP_LOGE(TAG, "USB transfer error on EP:%x", s_eub_vendord_itf.ep_in);
        }

        debug_probe_free_sent_data(buf_copy);
    }

    vTaskDelete(NULL);
}

void eub_vendord_start(void)
{
    ESP_LOGD(TAG, "%s", __func__);

    static bool init = false;

    if (!init) {
        if (xTaskCreate(usb_send_task,
                        "usb_send_task",
                        4 * 1024,
                        NULL,
                        DEBUG_PROBE_TASK_PRI - 1,
                        &s_eub_vendord_itf.usb_tx_task_handle) != pdPASS) {
            ESP_LOGE(TAG, "Cannot create USB send task!");
            eub_abort();
        }

        init = true;
    }
}

const usbd_class_driver_t s_eub_vendord_driver = {
#if CFG_TUSB_DEBUG >= 2
    .name = "EUB-VENDOR",
#endif
    .init = eub_vendord_init,
    .reset = eub_vendord_reset,
    .open = eub_vendord_open,
    .control_xfer_cb = tud_vendor_control_xfer_cb,
    .xfer_cb = eub_vendord_xfer_cb,
    .sof = NULL,
};

const usbd_class_driver_t *usbd_app_driver_get_cb(uint8_t *driver_count)
{
    ESP_LOGD(TAG, "%s", __func__);

    *driver_count = 1;
    return &s_eub_vendord_driver;
}
