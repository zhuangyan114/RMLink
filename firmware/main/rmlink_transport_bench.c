/* SPDX-License-Identifier: Apache-2.0
 * Opt-in ESP32/PC transport diagnostics. No DAP executor or target GPIO access.
 */
#include "sdkconfig.h"
#include "rmlink_transport_bench.h"
#if CONFIG_RMLINK_WIFI_ENABLED
#include <errno.h>
#include <string.h>
#include <unistd.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "lwip/sockets.h"

static atomic_uint s_until_seconds;
static TaskHandle_t s_task;

static bool enabled(void)
{
    uint32_t until = atomic_load(&s_until_seconds);
    uint32_t now = (uint32_t)(esp_timer_get_time() / 1000000);
    return until && (int32_t)(until - now) > 0;
}

esp_err_t rmlink_transport_bench_arm(unsigned seconds)
{
    if (!s_task || seconds > 300) return ESP_ERR_INVALID_ARG;
    atomic_store(&s_until_seconds, seconds ? (uint32_t)(esp_timer_get_time() / 1000000) + seconds : 0);
    return ESP_OK;
}

static uint32_t word_le(const uint8_t *p)
{ return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }

static bool receive(int fd, uint8_t *data, size_t size)
{
    while (size && enabled()) {
        int n = recv(fd, data, size, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        data += n; size -= n;
    }
    return size == 0;
}

static bool transmit(int fd, const uint8_t *data, size_t size)
{
    while (size && enabled()) {
        int n = send(fd, data, size, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        data += n; size -= n;
    }
    return size == 0;
}

static void bench_task(void *arg)
{
    /* Filled once per stream; no per-byte generation cost inside timed sends. */
    static uint8_t buffer[4096];
    for (;;) {
        while (!enabled()) vTaskDelay(pdMS_TO_TICKS(250));
        int listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listener < 0) { vTaskDelay(pdMS_TO_TICKS(250)); continue; }
        int yes = 1;
        setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
        struct sockaddr_in address = { .sin_family = AF_INET, .sin_addr.s_addr = INADDR_ANY,
            .sin_port = htons(CONFIG_RMLINK_TCP_PORT + 1) };
        if (bind(listener, (struct sockaddr *)&address, sizeof(address)) || listen(listener, 1)) {
            close(listener); vTaskDelay(pdMS_TO_TICKS(250)); continue;
        }
        while (enabled()) {
            fd_set fds; FD_ZERO(&fds); FD_SET(listener, &fds);
            struct timeval poll = { .tv_usec = 250000 };
            if (select(listener + 1, &fds, NULL, NULL, &poll) <= 0) continue;
            int client = accept(listener, NULL, NULL);
            if (client < 0) continue;
            struct timeval timeout = { .tv_sec = 2 };
            setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
            setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
            setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
            while (enabled()) {
                uint8_t header[16];
                if (!receive(client, header, sizeof(header)) || memcmp(header, "RMB1", 4)) break;
                uint32_t mode = word_le(header + 4), size = word_le(header + 8), seed = word_le(header + 12);
                if (mode == 1 && size && size <= 16 * 1024 * 1024) {
                    for (unsigned i = 0; i < sizeof(buffer); ++i) buffer[i] = (uint8_t)(i + seed);
                    uint32_t remaining = size;
                    bool ok = true;
                    while (remaining) {
                        size_t chunk = remaining < sizeof(buffer) ? remaining : sizeof(buffer);
                        if (!transmit(client, buffer, chunk)) { ok = false; break; }
                        remaining -= chunk;
                    }
                    if (!ok) break;
                } else if (mode == 2 && size && size <= 1024) {
                    if (!receive(client, buffer, size) || !transmit(client, buffer, size)) break;
                } else break;
            }
            close(client);
        }
        close(listener);
    }
}

esp_err_t rmlink_transport_bench_init(void)
{
    if (s_task) return ESP_OK;
    return xTaskCreate(bench_task, "rmlink_bench", 4096, NULL, 3, &s_task) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
#else
esp_err_t rmlink_transport_bench_init(void) { return ESP_ERR_NOT_SUPPORTED; }
esp_err_t rmlink_transport_bench_arm(unsigned seconds) { return ESP_ERR_NOT_SUPPORTED; }
#endif
