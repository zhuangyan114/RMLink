/*
 * SPDX-FileCopyrightText: 2020-2025 Espressif Systems (Shanghai) CO LTD
 * SPDX-FileCopyrightText: 2026 RMLink contributors
 * SPDX-License-Identifier: Apache-2.0
 * RMLink extends the upstream task and preserves the shared ARM SWD implementation.
 */
#include "sdkconfig.h"
#if CONFIG_DEBUG_PROBE_IFACE_SWD
#include <string.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/ringbuf.h"
#include "esp_log.h"
#include "esp_check.h"
#include "debug_probe.h"
#include "rmlink_executor.h"
#include "DAP_config.h"
#include "DAP.h"
#include "rmlink_capture.h"

typedef struct {
    uint32_t session;
    uint8_t transport;
    bool close;
    size_t len;
    uint8_t request[RMLINK_PACKET_SIZE];
    uint8_t *response;
    size_t *response_len;
    SemaphoreHandle_t done;
    rmlink_reply_fn reply;
    void *context;
} request_t;
/* DWC2 DMA requires a word-aligned payload, not just an aligned allocation. */
typedef struct { uint32_t session; uint32_t len; uint8_t data[RMLINK_USB_PACKET_SIZE]; } usb_reply_t;
_Static_assert(offsetof(usb_reply_t, data) % 4 == 0, "USB DMA payload must be word aligned");
static QueueHandle_t s_requests;
static RingbufHandle_t s_usb_replies;
static atomic_uint s_usb_session = 1;
static TaskHandle_t s_executor;
static rmlink_owner_t s_owner;
static atomic_uint s_tcp_session;
static portMUX_TYPE s_active_mux = portMUX_INITIALIZER_UNLOCKED;
static rmlink_owner_t s_active;
static void capture_wake(void) { if (s_executor) xTaskNotifyGive(s_executor); }

void rmlink_abort(uint8_t transport, uint32_t session)
{
    portENTER_CRITICAL(&s_active_mux);
    if (rmlink_owner_matches(&s_active, transport, session))
        __atomic_store_n(&DAP_TransferAbort, 1, __ATOMIC_RELAXED);
    portEXIT_CRITICAL(&s_active_mux);
}

static bool session_live(const request_t *job)
{
    return job->session == atomic_load(job->transport == RMLINK_USB ? &s_usb_session : &s_tcp_session);
}

__attribute__((weak)) void rmlink_vendor_transport_closed(uint8_t transport) {}

__attribute__((weak)) size_t rmlink_vendor_command(uint8_t transport, const uint8_t *request,
        size_t len, uint8_t *response, const rmlink_owner_t *owner)
{ response[0] = 0x80; response[1] = len > 1 ? request[1] : 0xff; response[2] = 0xff; return 3; }

static size_t process(const request_t *job, uint8_t *out)
{
    const uint8_t *r = job->request;
    size_t capacity = job->transport == RMLINK_USB ? RMLINK_USB_PACKET_SIZE : RMLINK_PACKET_SIZE;
    if (r[0] == 0x81)
        return rmlink_capture_command(job->transport, job->session, r, job->len, out, &s_owner);
    if (r[0] == 0x80) {
        if (job->len >= 2 && r[1] == 0) {
            memset(out, 0, RMLINK_PACKET_SIZE);
            out[0] = 0x80; out[2] = 0; out[3] = 1;
            out[5] = 2; out[6] = 1; /* firmware 0.2.1: realtime buffers + clock sync */
            out[7] = 1; /* USB */
#if CONFIG_RMLINK_WIFI_ENABLED
            out[7] |= 6; /* TCP + USB provisioning */
#endif
#if CONFIG_RMLINK_TARGET_PINS_CONFIRMED
            out[9] = 1;
#endif
            out[10] = s_owner.transport;
            for (unsigned i = 0; i < 4; ++i) out[11 + i] = (uint8_t)(s_owner.session >> (i * 8));
            out[15] = (uint8_t)capacity; out[16] = (uint8_t)(capacity >> 8);
            return 17;
        }
        return rmlink_vendor_command(job->transport, r, job->len, out, &s_owner);
    }
    /* Unsupported commands must return DAP_Invalid, including disabled modes. */
    if (!rmlink_dap_supported(r[0])) { out[0] = 0xff; return 1; }
    if (!rmlink_dap_validate_for_size(r, job->len, capacity)) return rmlink_dap_error(r[0], out);
    if (r[0] == 0 && r[1] == 0xff) {
        out[0] = 0; out[1] = 2; out[2] = (uint8_t)capacity; out[3] = (uint8_t)(capacity >> 8);
        return 4;
    }
    if (r[0] == 0x00 || r[0] == 0x01) return DAP_ProcessCommand(r, out) & 0xffff;
    if (r[0] == 0x03) {
        if (!s_owner.session) { out[0] = 3; out[1] = 0; return 2; }
        if (!rmlink_owner_release(&s_owner, job->transport, job->session)) return rmlink_dap_error(3, out);
        rmlink_capture_closed(job->transport, job->session);
        return DAP_ProcessCommand(r, out) & 0xffff;
    }
#if !CONFIG_RMLINK_TARGET_PINS_CONFIRMED
    return rmlink_dap_error(r[0], out);
#else
    if (!rmlink_capture_raw_allowed()) return rmlink_dap_error(r[0], out);
    if (r[0] == 2) {
        if (r[1] > 1 || !rmlink_owner_claim(&s_owner, job->transport, job->session)) return rmlink_dap_error(2, out);
        size_t n = DAP_ProcessCommand(r, out) & 0xffff;
        if (!out[1]) rmlink_owner_release(&s_owner, job->transport, job->session);
        return n;
    }
    /* pyOCD sets SWJ clock before Connect. Reserve the same whole session. */
    if (r[0] == 0x11) {
        bool newly_claimed = !s_owner.session;
        if (!rmlink_owner_claim(&s_owner, job->transport, job->session)) return rmlink_dap_error(r[0], out);
        size_t n = DAP_ProcessCommand(r, out) & 0xffff;
        if (out[1] && newly_claimed) rmlink_owner_release(&s_owner, job->transport, job->session);
        return n;
    }
    if (!rmlink_owner_matches(&s_owner, job->transport, job->session)) return rmlink_dap_error(r[0], out);
    return DAP_ProcessCommand(r, out) & 0xffff;
#endif
}
static void swd_task(void *arg)
{
    DAP_Setup();
    request_t job;
    uint8_t out[RMLINK_PACKET_SIZE];
    for (;;) {
        if (xQueueReceive(s_requests, &job, 0) != pdTRUE) {
            portENTER_CRITICAL(&s_active_mux);
            DAP_TransferBegin();
            s_active = s_owner;
            portEXIT_CRITICAL(&s_active_mux);
            bool sampled = rmlink_capture_run();
            portENTER_CRITICAL(&s_active_mux);
            memset(&s_active, 0, sizeof(s_active));
            portEXIT_CRITICAL(&s_active_mux);
            if (!sampled) ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
            continue;
        }
        if (job.close) {
            rmlink_vendor_transport_closed(job.transport);
            rmlink_capture_closed(job.transport, job.session);
            if (rmlink_owner_release(&s_owner, job.transport, job.session)) {
                const uint8_t disconnect[] = {3};
                DAP_ProcessCommand(disconnect, out);
            }
            if (job.done) xSemaphoreGive(job.done);
            continue;
        }
        memset(out, 0, sizeof(out));
        bool stale = !session_live(&job);
        if (stale && !job.done) continue;
        /* A synchronous caller must always be released, including after USB reset. */
        bool transfer = !stale && (job.request[0] == 5 || job.request[0] == 6 || job.request[0] == 0x81);
        if (transfer) {
            portENTER_CRITICAL(&s_active_mux);
            DAP_TransferBegin();
            s_active = (rmlink_owner_t){ .transport = job.transport, .session = job.session };
            /* Close may have arrived just before publishing the active request. */
            if (!session_live(&job)) __atomic_store_n(&DAP_TransferAbort, 1, __ATOMIC_RELAXED);
            portEXIT_CRITICAL(&s_active_mux);
        }
        size_t n = stale ? rmlink_dap_error(job.request[0], out) : process(&job, out);
        if (transfer) {
            portENTER_CRITICAL(&s_active_mux);
            memset(&s_active, 0, sizeof(s_active));
            portEXIT_CRITICAL(&s_active_mux);
        }
        size_t capacity = job.transport == RMLINK_USB ? RMLINK_USB_PACKET_SIZE : RMLINK_PACKET_SIZE;
        if (!n || n > capacity) n = rmlink_dap_error(job.request[0], out);
        if (job.done) {
            memcpy(job.response, out, n); *job.response_len = n;
            xSemaphoreGive(job.done);
        } else if (job.reply && session_live(&job)) {
            job.reply(job.context, out, n);
        } else if (job.transport == RMLINK_USB && session_live(&job)) {
            usb_reply_t reply = { .session = job.session, .len = n };
            memcpy(reply.data, out, n);
            if (xRingbufferSend(s_usb_replies, &reply, sizeof(reply), pdMS_TO_TICKS(100)) != pdTRUE)
                ESP_LOGW("rmlink", "USB response buffer full; host must reconnect");
        }
    }
}
esp_err_t debug_probe_init(void)
{
    if (s_executor) return ESP_OK;
    _Static_assert(DEBUG_PROBE_PACKET_SIZE == RMLINK_USB_PACKET_SIZE && DAP_PACKET_SIZE == RMLINK_PACKET_SIZE,
                   "Native USB and shared core capacities must match their declarations");
    s_requests = xQueueCreate(DAP_PACKET_COUNT, sizeof(request_t));
    s_usb_replies = xRingbufferCreate(1024, RINGBUF_TYPE_NOSPLIT);
    if (!s_requests || !s_usb_replies) return ESP_ERR_NO_MEM;
    esp_err_t capture_result = rmlink_capture_init(capture_wake);
    if (capture_result != ESP_OK)
        ESP_LOGW("rmlink", "Capture unavailable: %s; standard DAP remains enabled", esp_err_to_name(capture_result));
    if (xTaskCreatePinnedToCore(swd_task, "rmlink_executor", 6144, NULL,
        DEBUG_PROBE_TASK_PRI, &s_executor, esp_cpu_get_core_id()) != pdPASS) return ESP_ERR_NO_MEM;
    return ESP_OK;
}
esp_err_t debug_probe_process_data(const uint8_t *data, size_t len)
{
    if (!s_requests || !data || !len || len > RMLINK_USB_PACKET_SIZE) return ESP_ERR_INVALID_ARG;
    request_t job = { .transport = RMLINK_USB, .session = atomic_load(&s_usb_session), .len = len };
    if (data[0] == 7) { rmlink_abort(RMLINK_USB, job.session); return ESP_OK; }
    memcpy(job.request, data, len);
    bool queued = xQueueSend(s_requests, &job, pdMS_TO_TICKS(100)) == pdTRUE;
    if (queued) capture_wake();
    return queued ? ESP_OK : ESP_ERR_TIMEOUT;
}
esp_err_t rmlink_execute(uint8_t transport, uint32_t session, const uint8_t *data,
        size_t len, uint8_t *response, size_t *response_len)
{
    if (!s_requests || (transport != RMLINK_USB && transport != RMLINK_TCP) || !session
        || !data || !len || len > (transport == RMLINK_USB ? RMLINK_USB_PACKET_SIZE : RMLINK_PACKET_SIZE)
        || !response || !response_len)
        return ESP_ERR_INVALID_ARG;
    if (data[0] == 7) { rmlink_abort(transport, session); *response_len = 0; return ESP_OK; }
    SemaphoreHandle_t done = xSemaphoreCreateBinary();
    if (!done) return ESP_ERR_NO_MEM;
    request_t job = { .transport = transport, .session = session, .len = len,
        .response = response, .response_len = response_len, .done = done };
    memcpy(job.request, data, len);
    if (xQueueSend(s_requests, &job, pdMS_TO_TICKS(100)) != pdTRUE) {
        vSemaphoreDelete(done); return ESP_ERR_TIMEOUT;
    }
    capture_wake();
    /* Don't abandon a queued job: it references the caller's response and semaphore. */
    xSemaphoreTake(done, portMAX_DELAY);
    vSemaphoreDelete(done); return ESP_OK;
}
void rmlink_transport_closed(uint8_t transport, uint32_t session)
{
    if (!s_requests) return;
    if (transport == RMLINK_USB) session = atomic_fetch_add(&s_usb_session, 1);
    else { unsigned expected = session; atomic_compare_exchange_strong(&s_tcp_session, &expected, 0); }
    rmlink_abort(transport, session);
    request_t job = { .transport = transport, .session = session, .close = true };
    /* TCP owns callback context until this FIFO barrier completes. */
    StaticSemaphore_t storage;
    if (transport == RMLINK_TCP) job.done = xSemaphoreCreateBinaryStatic(&storage);
    xQueueSend(s_requests, &job, portMAX_DELAY);
    capture_wake();
    if (job.done) { xSemaphoreTake(job.done, portMAX_DELAY); vSemaphoreDelete(job.done); }
}
void rmlink_tcp_session_open(uint32_t session) { atomic_store(&s_tcp_session, session); }
esp_err_t rmlink_submit_tcp(uint32_t session, const uint8_t *data, size_t len,
        rmlink_reply_fn reply, void *context)
{
    if (!s_requests || !data || !len || len > RMLINK_PACKET_SIZE || !reply || !session)
        return ESP_ERR_INVALID_ARG;
    if (data[0] == 7) { rmlink_abort(RMLINK_TCP, session); return ESP_OK; }
    request_t job = { .transport = RMLINK_TCP, .session = session, .len = len,
                     .reply = reply, .context = context };
    memcpy(job.request, data, len);
    bool queued = xQueueSend(s_requests, &job, pdMS_TO_TICKS(100)) == pdTRUE;
    if (queued) capture_wake();
    return queued ? ESP_OK : ESP_ERR_TIMEOUT;
}
uint8_t *debug_probe_get_data_to_send(size_t *len, TickType_t timeout)
{
    for (;;) {
        size_t total = 0;
        usb_reply_t *reply = xRingbufferReceive(s_usb_replies, &total, timeout);
        if (!reply) return NULL;
        if (total == sizeof(*reply) && reply->session == atomic_load(&s_usb_session)) {
            *len = reply->len; return reply->data;
        }
        vRingbufferReturnItem(s_usb_replies, reply);
    }
}
void debug_probe_free_sent_data(uint8_t *data)
{ if (data) vRingbufferReturnItem(s_usb_replies, data - offsetof(usb_reply_t, data)); }
int debug_probe_get_proto_caps(void *dest) { return 0; }
void debug_probe_handle_esp32_tdi_bootstrapping(bool rebooting) {}
debug_probe_cmd_response_t debug_probe_handle_command(uint8_t cmd, uint16_t value)
{ return (debug_probe_cmd_response_t){0}; }
#endif
