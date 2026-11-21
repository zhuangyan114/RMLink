/* Exercise the production key and indicator state machine without hardware. */
#include <assert.h>
#include <stdio.h>
#define RMLINK_UI_HOST_TEST 1
#define CONFIG_RMLINK_UI_ENABLED 1
#include "../../firmware/main/rmlink_ui.c"

static enum ui_color step(ui_state_t *state, uint32_t now, bool pressed,
                          bool wifi_enabled, bool wifi_connected,
                          bool usb_mounted, uint8_t active_transport,
                          bool busy, bool *toggle_wifi)
{
    return ui_step(state, now, pressed, wifi_enabled, wifi_connected,
                   usb_mounted, active_transport, busy, toggle_wifi);
}

static void click(ui_state_t *state, uint32_t start, bool wifi_enabled,
                  bool wifi_connected, bool usb_mounted, uint8_t active, bool busy)
{
    bool toggle = false;
    step(state, start, true, wifi_enabled, wifi_connected, usb_mounted, active, busy, &toggle);
    assert(!toggle);
    step(state, start + 30, true, wifi_enabled, wifi_connected, usb_mounted, active, busy, &toggle);
    assert(!toggle);
    step(state, start + 100, false, wifi_enabled, wifi_connected, usb_mounted, active, busy, &toggle);
    assert(!toggle);
    step(state, start + 130, false, wifi_enabled, wifi_connected, usb_mounted, active, busy, &toggle);
    assert(!toggle);
}

int main(void)
{
    ui_state_t state = {0};
    bool toggle = false;

    /* No link ready -> 1 Hz red blink regardless of wifi_enabled. */
    assert(step(&state, 0, false, true, false, false, 0, false, &toggle) == UI_RED);
    assert(!toggle);
    assert(step(&state, 499, false, true, false, false, 0, false, &toggle) == UI_RED);
    assert(step(&state, 500, false, false, false, false, 0, false, &toggle) == UI_OFF);
    assert(step(&state, 999, false, false, false, false, 0, false, &toggle) == UI_OFF);
    assert(step(&state, 1000, false, true, false, false, 0, false, &toggle) == UI_RED);

    /* Standby links blink at 1 Hz: Wi-Fi green, USB blue (with USB priority). */
    assert(step(&state, 1100, false, true, true, false, 0, false, &toggle) == UI_GREEN);
    assert(step(&state, 1599, false, true, true, false, 0, false, &toggle) == UI_GREEN);
    assert(step(&state, 1600, false, true, true, false, 0, false, &toggle) == UI_OFF);
    assert(step(&state, 2099, false, true, true, false, 0, false, &toggle) == UI_OFF);
    assert(step(&state, 2100, false, true, true, false, 0, false, &toggle) == UI_GREEN);

    assert(step(&state, 2200, false, true, true, true, 0, false, &toggle) == UI_BLUE);
    assert(step(&state, 2699, false, true, true, true, 0, false, &toggle) == UI_BLUE);
    assert(step(&state, 2700, false, true, true, true, 0, false, &toggle) == UI_OFF);

    /* Active transport takes solid priority, even if the other link is also up. */
    assert(step(&state, 2800, false, true, true, true, 2, false, &toggle) == UI_GREEN);
    assert(step(&state, 3400, false, true, true, true, 2, false, &toggle) == UI_GREEN);
    assert(step(&state, 3500, false, true, true, true, 1, false, &toggle) == UI_BLUE);
    assert(step(&state, 4100, false, true, true, true, 1, false, &toggle) == UI_BLUE);

    /* Short press and sub-debounce noise do nothing. */
    click(&state, 4200, true, false, true, 1, false);
    assert(step(&state, 4400, true, true, false, true, 1, false, &toggle) == UI_BLUE);
    assert(step(&state, 4420, false, true, false, true, 1, false, &toggle) == UI_BLUE);
    assert(step(&state, 4450, false, true, false, true, 1, false, &toggle) == UI_BLUE);
    assert(!state.pressed && !toggle);

    /* Idle 2s hold emits one Wi-Fi toggle and shows solid yellow ACK for 500 ms. */
    step(&state, 5000, true, true, false, true, 1, false, &toggle);
    step(&state, 5030, true, true, false, true, 1, false, &toggle);
    assert(step(&state, 6999, true, true, false, true, 1, false, &toggle) == UI_BLUE);
    assert(!toggle);
    assert(step(&state, 7000, true, true, false, true, 1, false, &toggle) == UI_YELLOW);
    assert(toggle);
    assert(step(&state, 7499, true, false, false, true, 1, false, &toggle) == UI_YELLOW);
    assert(!toggle);
    assert(step(&state, 7500, false, false, false, true, 1, false, &toggle) == UI_BLUE);
    step(&state, 7530, false, false, false, true, 1, false, &toggle);

    /* Busy 2s hold refuses toggle and flashes yellow fast (200 ms period) for 1000 ms. */
    step(&state, 8000, true, false, false, true, 1, true, &toggle);
    step(&state, 8030, true, false, false, true, 1, true, &toggle);
    assert(step(&state, 10000, true, false, false, true, 1, true, &toggle) == UI_YELLOW);
    assert(!toggle);
    assert(step(&state, 10099, true, false, false, true, 1, true, &toggle) == UI_YELLOW);
    assert(step(&state, 10100, true, false, false, true, 1, true, &toggle) == UI_OFF);
    assert(step(&state, 10199, true, false, false, true, 1, true, &toggle) == UI_OFF);
    assert(step(&state, 10200, true, false, false, true, 1, true, &toggle) == UI_YELLOW);
    assert(step(&state, 10999, false, false, false, true, 1, true, &toggle) == UI_OFF);
    assert(step(&state, 11000, false, false, false, true, 1, true, &toggle) == UI_BLUE);
    step(&state, 11030, false, false, false, true, 1, true, &toggle);

    /* Release debounce must not turn a 1999 ms hold into a long press, while 2000 ms qualifies. */
    state = (ui_state_t){0};
    step(&state, 100, true, true, false, false, 0, false, &toggle);
    step(&state, 130, true, true, false, false, 0, false, &toggle);
    step(&state, 2099, false, true, false, false, 0, false, &toggle);
    step(&state, 2129, false, true, false, false, 0, false, &toggle);
    assert(!toggle && state.feedback == UI_FEEDBACK_NONE);
    step(&state, 2200, true, true, false, false, 0, false, &toggle);
    step(&state, 2230, true, true, false, false, 0, false, &toggle);
    assert(step(&state, 4200, false, true, false, false, 0, false, &toggle) == UI_RED);
    assert(!toggle);
    assert(step(&state, 4230, false, true, false, false, 0, false, &toggle) == UI_YELLOW);
    assert(toggle);

    /* Millisecond counter rollover preserves both hold and blink timing. */
    state = (ui_state_t){0};
    step(&state, UINT32_MAX - 1000, true, true, false, false, 0, false, &toggle);
    step(&state, UINT32_MAX - 970, true, true, false, false, 0, false, &toggle);
    assert(step(&state, 999, true, true, false, false, 0, false, &toggle) == UI_YELLOW);
    assert(toggle);
    assert(step(&state, 1500, false, false, false, false, 0, false, &toggle) == UI_RED);
    assert(step(&state, 2000, false, false, false, false, 0, false, &toggle) == UI_OFF);
    puts("UI tests passed (2s Wi-Fi toggle, busy reject, active/standby LEDs, rollover)");
    return 0;
}
