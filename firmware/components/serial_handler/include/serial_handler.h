/*
 * SPDX-FileCopyrightText: 2020-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SERIAL_HANDLER_TASK_PRI 5

#define DOWNLOAD_MODE  1
#define RUNNING_MODE   0
#define RESET_ASSERT   1
#define RESET_RELEASE  0

typedef enum {
    TRANSPORT_TYPE_UART,
    // Future transport types...
} transport_type_t;

/**
 * @brief Data received callback - called when data is received from transport
 *
 * NOTE: This callback is only invoked when flashing is NOT in progress.
 * When flashing is active, all received data is used internally for ESP loader operations.
 *
 * @param data Received data buffer
 * @param len Length of received data
 */
typedef void (*transport_data_received_cb_t)(const uint8_t *data, size_t len);

/**
 * @brief Serial TX activity notification callback
 * @param active true when transmitting data, false when TX idle
 */
typedef void (*serial_tx_notify_cb_t)(bool active);

/**
 * @brief Serial RX activity notification callback
 * @param active true when receiving data, false when RX idle
 */
typedef void (*serial_rx_notify_cb_t)(bool active);

/**
 * @brief Register callback for serial TX activity notifications
 *
 * This callback will be called to indicate UART TX activity.
 * Useful for driving TX activity LEDs or other status indicators.
 *
 * @param callback Callback function, can be NULL to disable notifications
 */
void serial_handler_register_tx_activity_callback(serial_tx_notify_cb_t callback);

/**
 * @brief Register callback for serial RX activity notifications
 *
 * This callback will be called to indicate UART RX activity.
 * Useful for driving RX activity LEDs or other status indicators.
 *
 * @param callback Callback function, can be NULL to disable notifications
 */
void serial_handler_register_rx_activity_callback(serial_rx_notify_cb_t callback);

/**
 * @brief Register callback for received data
 *
 * The callback will be invoked when data is received, but ONLY when
 * flashing is not in progress. During flashing operations, all data
 * is handled internally by the ESP loader.
 *
 * @param callback Callback function to call when data is received
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_handler_register_data_callback(transport_data_received_cb_t callback);

/**
 * @brief Initialize comm handler with specified transport type
 *
 * @param type Transport type to initialize (UART, SPI, I2C, etc.)
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_handler_init(transport_type_t type);

/**
 * @brief Send data through transport
 *
 * Can be used for bridge operations when flashing is not active,
 * or for ESP loader operations when flashing is active.
 *
 * @param data Data to send
 * @param len Length of data
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_handler_send_data(const uint8_t *data, size_t len);

/**
 * @brief Set transport baudrate/speed
 *
 * @param baud Baudrate for UART, clock speed for SPI, etc.
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_handler_set_baudrate(uint32_t baud);

/**
 * @brief Connect to ESP chip for flashing
 *
 * This function takes exclusive access to the transport and connects
 * to the ESP chip using the ESP loader. While connected, bridge callbacks
 * are suspended.
 *
 * @param baud_rate Initial baudrate for connection
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_handler_flash_connect(uint32_t baud_rate);

/**
 * @brief Change baudrate during flashing
 *
 * @param chip_id ESP chip ID
 * @param new_baud New baudrate
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_handler_flash_change_baudrate(uint32_t chip_id, uint32_t new_baud);

/**
 * @brief Prepare for flash operation at specified address
 *
 * This function prepares for a flash operation but doesn't start writing yet.
 * The actual flashing is done by subsequent calls to flash_write.
 *
 * @param addr Flash address
 * @param data Data to flash (for validation/preparation)
 * @param len Length of data to flash
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_handler_flash_start(uint32_t addr, const uint8_t *data, uint32_t len);

/**
 * @brief Write data to flash with chunking
 *
 * This function handles the ESP loader start operation and writes the data
 * in optimal chunks for the hardware.
 *
 * @param data Data to write
 * @param len Length of data
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_handler_flash_write(const uint8_t *data, uint32_t len);

/**
 * @brief Read data from flash
 *
 * @param data Buffer to store read data
 * @param addr Flash address to read from
 * @param len Length of data to read
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_handler_flash_read(uint8_t *data, uint32_t addr, uint32_t len);

/**
 * @brief Finish flashing and disconnect
 *
 * This function finishes the flashing process, disconnects from the ESP chip,
 * and releases exclusive access to the transport. Bridge callbacks are resumed.
 *
 * If reboot is true, starts a non-blocking timer-based reset sequence for the target.
 * The reset status can be checked using serial_handler_is_reset_active().
 * This non-blocking approach prevents blocking USB/tinyUSB tasks.
 *
 * @param reboot Whether to reboot the ESP chip after flashing (starts reset timer)
 * @return esp_err_t ESP_OK on success
 */
esp_err_t serial_handler_flash_finish(bool reboot);

/**
 * @brief Check if target reset is currently active
 *
 * This function checks if a reset timer was started by serial_handler_flash_finish()
 * and is still active. During the reset period, the target is held in reset state.
 *
 * @return true if reset timer is active (target is being reset), false otherwise
 */
bool serial_handler_is_reset_active(void);

/**
 * @brief Check if flashing is currently in progress
 *
 * @return true if flashing is active, false otherwise
 */
bool serial_handler_is_flashing(void);

/**
 * @brief Set target boot and reset pin levels
 *
 * This function sets the GPIO levels of both BOOT and RESET pins.
 * The function handles the pin state internally based on the provided values.
 *
 * @param boot_pin true to set boot pin HIGH, false to set it LOW
 * @param reset_pin true to set reset pin HIGH, false to set it LOW
 */
void serial_handler_set_boot_reset_pins(bool boot_pin, bool reset_pin);

#ifdef __cplusplus
}
#endif
