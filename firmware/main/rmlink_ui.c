/* SPDX-License-Identifier: Apache-2.0 */
#include <stdbool.h>
#include <stdint.h>

#ifndef RMLINK_UI_HOST_TEST
#include <stdatomic.h>
#include "sdkconfig.h"
#include "rmlink_ui.h"

static atomic_bool s_usb_connected;

void rmlink_ui_usb_connected(bool connected)
{
    atomic_store(&s_usb_connected, connected);
}

bool rmlink_ui_usb_is_connected(void)
{
    return atomic_load(&s_usb_connected);
}
#endif

#if CONFIG_RMLINK_UI_ENABLED
enum ui_color { UI_OFF, UI_RED, UI_GREEN, UI_BLUE, UI_YELLOW };
enum ui_feedback { UI_FEEDBACK_NONE, UI_FEEDBACK_ACK, UI_FEEDBACK_BUSY };

typedef struct {
    bool raw_pressed;
    bool pressed;
    bool long_press_sent;
    uint32_t raw_changed_ms;
    uint32_t pressed_ms;
    enum ui_feedback feedback;
    uint32_t feedback_started_ms;
    uint8_t pattern;
    uint32_t pattern_started_ms;
} ui_state_t;

static void trigger_long_press(ui_state_t *state, uint32_t now_ms, bool busy, bool *toggle_wifi)
{
    state->long_press_sent = true;
    state->feedback_started_ms = now_ms;
    if (busy) {
        state->feedback = UI_FEEDBACK_BUSY;
    } else {
        state->feedback = UI_FEEDBACK_ACK;
        if (toggle_wifi) *toggle_wifi = true;
    }
}

static enum ui_color ui_step(ui_state_t *state, uint32_t now_ms, bool pressed,
                             bool wifi_enabled, bool wifi_connected,
                             bool usb_mounted, uint8_t active_transport,
                             bool busy, bool *toggle_wifi)
{
    (void)wifi_enabled;
    if (toggle_wifi) *toggle_wifi = false;
    if (pressed != state->raw_pressed) {
        state->raw_pressed = pressed;
        state->raw_changed_ms = now_ms;
    }
    if (state->pressed != pressed && (uint32_t)(now_ms - state->raw_changed_ms) >= 30u) {
        state->pressed = pressed;
        if (pressed) {
            state->pressed_ms = state->raw_changed_ms;
            state->long_press_sent = false;
        } else if (!state->long_press_sent &&
                   (uint32_t)(state->raw_changed_ms - state->pressed_ms) >= 2000u) {
            /* Include a release sampled exactly at the two-second boundary. */
            trigger_long_press(state, now_ms, busy, toggle_wifi);
        }
    }
    if (state->pressed && pressed && !state->long_press_sent &&
        (uint32_t)(now_ms - state->pressed_ms) >= 2000u) {
        trigger_long_press(state, now_ms, busy, toggle_wifi);
    }

    if (state->feedback == UI_FEEDBACK_BUSY && (uint32_t)(now_ms - state->feedback_started_ms) >= 1000u) {
        state->feedback = UI_FEEDBACK_NONE;
    } else if (state->feedback == UI_FEEDBACK_ACK && (uint32_t)(now_ms - state->feedback_started_ms) >= 500u) {
        state->feedback = UI_FEEDBACK_NONE;
    }

    enum ui_color color;
    uint32_t period_ms = 0;
    uint8_t pattern;
    if (state->feedback == UI_FEEDBACK_BUSY) {
        color = UI_YELLOW;
        period_ms = 200u;
        pattern = (uint8_t)color | 0x40u;
    } else if (state->feedback == UI_FEEDBACK_ACK) {
        color = UI_YELLOW;
        pattern = (uint8_t)color;
    } else if (active_transport == 1) {
        color = UI_BLUE;
        pattern = (uint8_t)color;
    } else if (active_transport == 2) {
        color = UI_GREEN;
        pattern = (uint8_t)color;
    } else if (usb_mounted) {
        color = UI_BLUE;
        period_ms = 1000u;
        pattern = (uint8_t)color | 0x80u;
    } else if (wifi_connected) {
        color = UI_GREEN;
        period_ms = 1000u;
        pattern = (uint8_t)color | 0x80u;
    } else {
        color = UI_RED;
        period_ms = 1000u;
        pattern = (uint8_t)color | 0x80u;
    }
    if (pattern != state->pattern) {
        state->pattern = pattern;
        state->pattern_started_ms = now_ms;
    }
    if (period_ms && (uint32_t)(now_ms - state->pattern_started_ms) % period_ms >= period_ms / 2u) {
        return UI_OFF;
    }
    return color;
}

#ifndef RMLINK_UI_HOST_TEST
#include "driver/gpio.h"
#include "driver/rmt_tx.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "rmlink_network.h"
#if CONFIG_DEBUG_PROBE_IFACE_SWD
#include "rmlink_executor.h"
#endif

static atomic_bool s_ui_initialized;
static atomic_int s_ui_init_error;
static atomic_int s_led_error;
static atomic_uint s_led_frames;
static atomic_uint s_led_color;
static rmt_channel_handle_t s_led_channel;
static rmt_encoder_handle_t s_led_encoder;
static rmt_symbol_word_t s_led_frame[25];

void rmlink_ui_diagnostics(uint8_t output[20])
{
    const uint32_t values[] = {
        (uint32_t)atomic_load(&s_ui_init_error),
        (uint32_t)atomic_load(&s_led_error), atomic_load(&s_led_frames),
    };
    output[0]=1;output[1]=1;output[2]=atomic_load(&s_ui_initialized);output[3]=0;
    for(unsigned j=0;j<3;j++)for(unsigned i=0;i<4;i++)output[4+j*4+i]=values[j]>>(8*i);
    output[16]=atomic_load(&s_led_color);output[17]=atomic_load(&s_usb_connected);
    output[18]=CONFIG_RMLINK_UI_LED_GPIO;output[19]=CONFIG_RMLINK_UI_KEY_GPIO;
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
    /* The frame buffer stays owned by this transfer until completion. */
    if (err == ESP_OK) err = rmt_tx_wait_all_done(s_led_channel, -1);
    atomic_store(&s_led_error, err);
    if (err == ESP_OK) {
        atomic_fetch_add(&s_led_frames, 1);
        atomic_store(&s_led_color, color);
    }
    return err;
}

static void ui_task(void *unused)
{
    (void)unused;
    ui_state_t state = {0};
    enum ui_color shown = UI_RED;
    for (;;) {
        bool wifi_enabled = rmlink_network_is_enabled();
        bool wifi_connected = rmlink_network_is_connected();
        bool usb_mounted = rmlink_ui_usb_is_connected();
#if CONFIG_DEBUG_PROBE_IFACE_SWD
        uint8_t owner = rmlink_active_owner_transport();
        uint8_t active_transport = owner ? owner
            : ((usb_mounted && rmlink_usb_client_active()) ? RMLINK_USB : (rmlink_network_has_client() ? RMLINK_TCP : 0));
        bool busy = rmlink_busy_flags() != 0;
#else
        uint8_t active_transport = rmlink_network_has_client() ? 2 : 0;
        bool busy = false;
#endif
        bool toggle_wifi = false;
        enum ui_color color = ui_step(&state, (uint32_t)(esp_timer_get_time() / 1000),
            gpio_get_level(CONFIG_RMLINK_UI_KEY_GPIO) == 0,
            wifi_enabled, wifi_connected, usb_mounted, active_transport, busy, &toggle_wifi);
        if (toggle_wifi) {
            bool next_enabled = !wifi_enabled;
            rmlink_network_set_enabled(next_enabled);
            ESP_LOGI("rmlink_ui", "Wi-Fi %s by user key", next_enabled ? "enabled" : "disabled");
        }
        if (color != shown) {
            esp_err_t err = show_color(color);
            if (err == ESP_OK) shown = color;
            else ESP_LOGW("rmlink_ui", "LED update failed: %s", esp_err_to_name(err));
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

static esp_err_t ui_hardware_init(void)
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

esp_err_t rmlink_ui_init(void)
{
    esp_err_t err = ui_hardware_init();
    atomic_store(&s_ui_init_error, err);
    atomic_store(&s_ui_initialized, true);
    return err;
}
#endif
#elif !defined(RMLINK_UI_HOST_TEST)
esp_err_t rmlink_ui_init(void) { return ESP_OK; }
void rmlink_ui_diagnostics(uint8_t output[20])
{
    for(unsigned i=0;i<20;i++)output[i]=0;
    output[0]=1;
}
#endif
