/* Exercise the production key and indicator state machine without hardware. */
#include <assert.h>
#include <stdio.h>
#define RMLINK_UI_HOST_TEST 1
#define CONFIG_RMLINK_UI_ENABLED 1
#include "../../firmware/main/rmlink_ui.c"

static enum ui_color step(ui_state_t *state, uint32_t now, bool pressed, bool wifi, bool usb)
{
    return ui_step(state, now, pressed, wifi, usb);
}

static void hold(ui_state_t *state, uint32_t start, bool wifi, bool usb)
{
    step(state, start, true, wifi, usb);
    step(state, start + 30, true, wifi, usb);
    step(state, start + 1999, true, wifi, usb);
    assert(state->phase != UI_CONFIGURE);
    assert(step(state, start + 2000, true, wifi, usb) == UI_YELLOW);
    assert(state->phase == UI_CONFIGURE);
    step(state, start + 2100, false, wifi, usb);
    step(state, start + 2130, false, wifi, usb);
    assert(state->phase == UI_CONFIGURE);
}

static void click(ui_state_t *state, uint32_t start, bool wifi, bool usb)
{
    step(state, start, true, wifi, usb);
    step(state, start + 30, true, wifi, usb);
    step(state, start + 100, false, wifi, usb);
    step(state, start + 130, false, wifi, usb);
}

int main(void)
{
    ui_state_t state = {0};
    assert(step(&state, 0, false, false, false) == UI_RED);
    assert(step(&state, 499, false, false, false) == UI_RED);
    assert(step(&state, 500, false, false, false) == UI_OFF);
    assert(step(&state, 999, false, false, false) == UI_OFF);
    assert(step(&state, 1000, false, false, false) == UI_RED);
    assert(step(&state, 1100, false, true, false) == UI_GREEN);
    assert(step(&state, 1700, false, true, false) == UI_GREEN);
    assert(step(&state, 1800, false, true, true) == UI_BLUE);
    assert(step(&state, 2400, false, true, true) == UI_BLUE);
    assert(step(&state, 2500, false, false, false) == UI_RED);

    /* A short press in normal operation and sub-debounce pulses do nothing. */
    click(&state, 2600, false, false);
    assert(state.mode == UI_AUTO && state.phase == UI_NORMAL);
    step(&state, 2800, true, false, false);
    step(&state, 2820, false, false, false);
    step(&state, 2850, false, false, false);
    assert(!state.pressed && state.mode == UI_AUTO);

    hold(&state, 3000, true, false);
    assert(state.mode == UI_AUTO && state.choice == UI_WIFI);
    assert(step(&state, 5500, false, true, false) == UI_OFF);
    assert(step(&state, 6000, false, true, false) == UI_YELLOW);
    click(&state, 6100, true, false);
    assert(state.mode == UI_USB && state.phase == UI_CONNECTING);
    assert(step(&state, 6230, false, true, false) == UI_BLUE);
    assert(step(&state, 6730, false, true, false) == UI_OFF);
    assert(step(&state, 60 * 60 * 1000 + 6230, false, true, false) == UI_BLUE);
    assert(state.phase == UI_CONNECTING); /* No timeout or fallback to Wi-Fi. */
    assert(step(&state, 60 * 60 * 1000 + 6240, false, true, true) == UI_BLUE);
    assert(state.phase == UI_NORMAL);
    assert(step(&state, 60 * 60 * 1000 + 6840, false, true, true) == UI_BLUE);
    assert(step(&state, 60 * 60 * 1000 + 6850, false, true, false) == UI_RED);

    /* Further short presses can change the pending mode before it connects. */
    state = (ui_state_t){0};
    hold(&state, 100, false, false);
    click(&state, 2300, false, false);
    assert(state.mode == UI_USB && state.phase == UI_CONNECTING);
    click(&state, 2500, false, true);
    /* A successful connection closes selection, so this press is ignored. */
    assert(state.mode == UI_USB && state.phase == UI_NORMAL);
    hold(&state, 2800, false, true);
    click(&state, 5000, false, true);
    assert(state.mode == UI_WIFI && state.phase == UI_CONNECTING);
    assert(step(&state, 5130, false, false, true) == UI_GREEN);
    assert(step(&state, 5630, false, false, true) == UI_OFF);
    click(&state, 5700, false, false);
    assert(state.mode == UI_USB && state.phase == UI_CONNECTING);
    click(&state, 5900, false, false);
    assert(state.mode == UI_WIFI && state.phase == UI_CONNECTING);
    assert(step(&state, 6100, false, true, false) == UI_GREEN);
    assert(state.phase == UI_NORMAL);

    /* The initial selected mode follows an already-enumerated USB connection. */
    state = (ui_state_t){0};
    hold(&state, 100, true, true);
    assert(state.choice == UI_USB);
    click(&state, 2300, false, true);
    assert(state.mode == UI_WIFI && state.phase == UI_CONNECTING);

    /* Release debounce must not turn a 1999 ms hold into a long press. */
    state = (ui_state_t){0};
    step(&state, 100, true, false, false);
    step(&state, 130, true, false, false);
    step(&state, 2099, false, false, false);
    step(&state, 2129, false, false, false);
    assert(state.phase == UI_NORMAL && state.mode == UI_AUTO);
    step(&state, 2200, true, false, false);
    step(&state, 2230, true, false, false);
    step(&state, 4200, false, false, false);
    step(&state, 4230, false, false, false);
    assert(state.phase == UI_CONFIGURE && state.mode == UI_AUTO);

    /* Millisecond counter rollover preserves both hold and blink timing. */
    state = (ui_state_t){0};
    hold(&state, UINT32_MAX - 1000, false, false);
    assert(state.phase == UI_CONFIGURE);
    assert(step(&state, 1499, false, false, false) == UI_OFF);
    assert(step(&state, 1999, false, false, false) == UI_YELLOW);
    puts("UI tests passed (2 s hold, debounce, 1 Hz colors, pending modes, reconnect, rollover)");
    return 0;
}
