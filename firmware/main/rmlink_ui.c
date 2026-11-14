/* SPDX-License-Identifier: Apache-2.0 */
#include <stdbool.h>
#include <stdint.h>

#ifndef RMLINK_UI_HOST_TEST
#include "sdkconfig.h"
#include "rmlink_ui.h"
#endif

#if CONFIG_RMLINK_UI_ENABLED
enum ui_mode { UI_AUTO, UI_WIFI, UI_USB };
enum ui_phase { UI_NORMAL, UI_CONFIGURE, UI_CONNECTING };
enum ui_color { UI_OFF, UI_RED, UI_GREEN, UI_BLUE, UI_YELLOW };

typedef struct {
    enum ui_mode mode;
    enum ui_mode choice;
    enum ui_phase phase;
    bool raw_pressed;
    bool pressed;
    bool long_press_sent;
    uint32_t raw_changed_ms;
    uint32_t pressed_ms;
    uint8_t pattern;
    uint32_t pattern_started_ms;
} ui_state_t;

static void enter_configuration(ui_state_t *state, bool usb_connected)
{
    state->phase = UI_CONFIGURE;
    state->choice = state->mode;
    if (state->mode == UI_AUTO) state->choice = usb_connected ? UI_USB : UI_WIFI;
    state->long_press_sent = true;
}

static enum ui_color ui_step(ui_state_t *state, uint32_t now_ms, bool pressed,
                             bool wifi_connected, bool usb_connected)
{
    if (pressed != state->raw_pressed) {
        state->raw_pressed = pressed;
        state->raw_changed_ms = now_ms;
    }
    if (state->pressed != pressed && now_ms - state->raw_changed_ms >= 30) {
        state->pressed = pressed;
        if (pressed) {
            state->pressed_ms = state->raw_changed_ms;
            state->long_press_sent = false;
        } else if (!state->long_press_sent) {
            /* Include a release sampled exactly at the two-second boundary. */
            if (state->raw_changed_ms - state->pressed_ms >= 2000) {
                enter_configuration(state, usb_connected);
            } else if (state->phase != UI_NORMAL) {
                state->choice = state->choice == UI_WIFI ? UI_USB : UI_WIFI;
                state->mode = state->choice;
                state->phase = UI_CONNECTING;
            }
        }
    }
    if (state->pressed && pressed && !state->long_press_sent &&
        now_ms - state->pressed_ms >= 2000) {
        enter_configuration(state, usb_connected);
    }

    bool connected = state->mode == UI_USB ? usb_connected : wifi_connected;
    if (state->mode == UI_AUTO) connected = wifi_connected || usb_connected;
    if (state->phase == UI_CONNECTING && connected) state->phase = UI_NORMAL;

    enum ui_color color;
    bool blink = true;
    if (state->phase == UI_CONFIGURE) {
        color = UI_YELLOW;
    } else if (state->phase == UI_CONNECTING) {
        color = state->mode == UI_USB ? UI_BLUE : UI_GREEN;
    } else if (connected) {
        color = state->mode == UI_USB || (state->mode == UI_AUTO && usb_connected) ? UI_BLUE : UI_GREEN;
        blink = false;
    } else {
        color = UI_RED;
    }
    uint8_t pattern = (uint8_t)color | (blink ? 0x80 : 0);
    if (pattern != state->pattern) {
        state->pattern = pattern;
        state->pattern_started_ms = now_ms;
    }
    return blink && (now_ms - state->pattern_started_ms) % 1000 >= 500 ? UI_OFF : color;
}

#ifndef RMLINK_UI_HOST_TEST
#include <stdatomic.h>
#include "driver/gpio.h"
#include "driver/rmt_tx.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "tusb.h"
#include "device/usbd_pvt.h"
#include "eub_vendord.h"
#include "rmlink_network.h"

static atomic_bool s_usb_connected;
static rmt_channel_handle_t s_led_channel;
static rmt_encoder_handle_t s_led_encoder;
static rmt_symbol_word_t s_led_frame[25];

void rmlink_ui_usb_connected(bool connected)
{
    atomic_store(&s_usb_connected, connected);
}

/* Run attachment and endpoint cleanup on TinyUSB's own task. */
static void apply_usb_mode(void *context)
{
    if ((uintptr_t)context) {
        tud_connect();
    } else {
        atomic_store(&s_usb_connected, false);
        eub_vendord_disconnect();
    }
}

static esp_err_t show_color(enum ui_color color)
{
    const uint8_t brightness = CONFIG_RMLINK_UI_BRIGHTNESS;
    const uint8_t grb[] = {
        color == UI_GREEN || color == UI_YELLOW ? brightness : 0,
        color == UI_RED || color == UI_YELLOW ? brightness : 0,
        color == UI_BLUE ? brightness : 0,
    };
    /* 10 MHz RMT ticks. GRB, MSB first, with a 300 us reset for V6. */
    for (unsigned bit = 0; bit < 24; ++bit) {
        bool one = (grb[bit / 8] & (0x80u >> (bit % 8))) != 0;
        s_led_frame[bit] = (rmt_symbol_word_t) {
            .level0 = 1, .duration0 = one ? 7 : 3,
            .level1 = 0, .duration1 = one ? 6 : 10,
        };
    }
    s_led_frame[24] = (rmt_symbol_word_t) { .duration0 = 1500, .duration1 = 1500 };
    rmt_transmit_config_t config = {0};
    esp_err_t err = rmt_transmit(s_led_channel, s_led_encoder, s_led_frame, sizeof(s_led_frame), &config);
    if (err != ESP_OK) return err;
    /* The frame buffer stays owned by this transfer until completion. */
    return rmt_tx_wait_all_done(s_led_channel, -1);
}

static void ui_task(void *unused)
{
    (void)unused;
    ui_state_t state = {0};
    enum ui_color shown = UI_RED;
    for (;;) {
        enum ui_mode previous_mode = state.mode;
        enum ui_color color = ui_step(&state, (uint32_t)(esp_timer_get_time() / 1000),
            gpio_get_level(CONFIG_RMLINK_UI_KEY_GPIO) == 0,
            rmlink_network_is_connected(), atomic_load(&s_usb_connected));
        if (state.mode != previous_mode) {
            rmlink_network_set_enabled(state.mode != UI_USB);
            usbd_defer_func(apply_usb_mode, (void *)(uintptr_t)(state.mode != UI_WIFI), false);
            ESP_LOGI("rmlink_ui", "Selected %s mode", state.mode == UI_USB ? "USB" : "Wi-Fi");
        }
        if (color != shown) {
            esp_err_t err = show_color(color);
            if (err == ESP_OK) shown = color;
            else ESP_LOGW("rmlink_ui", "LED update failed: %s", esp_err_to_name(err));
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

esp_err_t rmlink_ui_init(void)
{
    gpio_config_t key = {
        .pin_bit_mask = 1ULL << CONFIG_RMLINK_UI_KEY_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&key);
    if (err != ESP_OK) return err;
    rmt_tx_channel_config_t led = {
        .gpio_num = CONFIG_RMLINK_UI_LED_GPIO,
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10000000,
        .mem_block_symbols = 64,
        .trans_queue_depth = 1,
    };
    err = rmt_new_tx_channel(&led, &s_led_channel);
    if (err != ESP_OK) return err;
    rmt_copy_encoder_config_t encoder = {};
    err = rmt_new_copy_encoder(&encoder, &s_led_encoder);
    if (err != ESP_OK) goto cleanup_channel;
    err = rmt_enable(s_led_channel);
    if (err != ESP_OK) goto cleanup_encoder;
    err = show_color(UI_RED);
    if (err == ESP_OK && xTaskCreate(ui_task, "rmlink_ui", 3072, NULL, 1, NULL) != pdPASS)
        err = ESP_ERR_NO_MEM;
    if (err == ESP_OK) return ESP_OK;
    rmt_disable(s_led_channel);
cleanup_encoder:
    rmt_del_encoder(s_led_encoder);
    s_led_encoder = NULL;
cleanup_channel:
    rmt_del_channel(s_led_channel);
    s_led_channel = NULL;
    return err;
}
#endif
#elif !defined(RMLINK_UI_HOST_TEST)
esp_err_t rmlink_ui_init(void) { return ESP_OK; }
void rmlink_ui_usb_connected(bool connected) { (void)connected; }
#endif
