/* SPDX-License-Identifier: Apache-2.0
 * Network lifecycle and CMSIS-DAP TCP framing reference bkuschak/cmsis_dap_tcp_esp32.
 * See docs/source-audit.md for pinned references. SWD requests use the upstream executor.
 */
#include "sdkconfig.h"
#if CONFIG_RMLINK_WIFI_ENABLED
#include <errno.h>
#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "mdns.h"
#include "lwip/sockets.h"
#include "rmlink_executor.h"
#include "rmlink_capture.h"
#include "rmlink_network.h"
#include "rmlink_transport_bench.h"

typedef struct { uint8_t version; char ssid[33]; char password[64]; } credentials_t;
static credentials_t s_config = { .version = 1 };
static credentials_t s_staged = { .version = 1 };
static uint8_t s_total[2];
static uint64_t s_received[2];
static bool s_field_set[2];
static portMUX_TYPE s_config_mux = portMUX_INITIALIZER_UNLOCKED;
static atomic_uint s_revision;
static atomic_bool s_up;
/* A brief down/up transition can fit between TCP task polls. Keep its history
 * so old sockets cannot survive across STA associations or DHCP changes. */
static atomic_uint s_network_generation;
static atomic_bool s_connecting;
static TaskHandle_t s_manager;
static esp_netif_t *s_sta;

void rmlink_vendor_transport_closed(uint8_t transport)
{
    if (transport != RMLINK_USB) return;
    memset(&s_staged, 0, sizeof(s_staged)); s_staged.version = 1;
    memset(s_total, 0, sizeof(s_total)); memset(s_received, 0, sizeof(s_received));
    memset(s_field_set, 0, sizeof(s_field_set));
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        atomic_store(&s_up, false);
        atomic_fetch_add(&s_network_generation, 1);
        atomic_store(&s_connecting, false);
        ESP_LOGI("rmlink_wifi", "Disconnected; USB and target executor remain active");
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        atomic_fetch_add(&s_network_generation, 1);
        atomic_store(&s_up, true);
        atomic_store(&s_connecting, false);
        ip_event_got_ip_t *ip = data;
        ESP_LOGI("rmlink_wifi", "STA address " IPSTR, IP2STR(&ip->ip_info.ip));
    }
}
static void connection_task(void *arg)
{
    unsigned current_revision = UINT32_MAX;
    credentials_t config;
    for (;;) {
        unsigned revision = atomic_load(&s_revision);
        if (revision != current_revision) {
            portENTER_CRITICAL(&s_config_mux);
            config = s_config;
            portEXIT_CRITICAL(&s_config_mux);
            current_revision = revision;
            esp_wifi_disconnect();
            atomic_store(&s_up, false);
            atomic_store(&s_connecting, false);
            if (config.ssid[0]) {
                wifi_config_t wifi = {0};
                memcpy(wifi.sta.ssid, config.ssid, sizeof(wifi.sta.ssid));
                memcpy(wifi.sta.password, config.password, sizeof(wifi.sta.password));
                wifi.sta.threshold.authmode = config.password[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
                esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &wifi);
                if (err != ESP_OK) {
                    ESP_LOGE("rmlink_wifi", "STA configuration failed: %s; USB remains active", esp_err_to_name(err));
                    config.ssid[0] = 0; /* Wait for a new configuration revision. */
                }
            }
        }
        if (config.ssid[0] && !atomic_load(&s_up) && !atomic_load(&s_connecting)) {
            atomic_store(&s_connecting, true);
            if (esp_wifi_connect() != ESP_OK) atomic_store(&s_connecting, false);
        }
        /* Bounded retry rate; callbacks never reboot the MCU or touch SWD GPIO. */
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(3000));
    }
}
static bool recv_exact(int fd, uint8_t *buf, size_t n)
{
    while (n) {
        int got = recv(fd, buf, n, 0);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0 || !atomic_load(&s_up)) {
            ESP_LOGW("rmlink_wifi","recv ended: result=%d errno=%d remaining=%u free_internal=%u min_internal=%u",
                got,got<0?errno:0,(unsigned)n,
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),
                (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));
            return false;
        }
        buf += got; n -= got;
    }
    return true;
}
static bool send_exact(int fd, const uint8_t *buf, size_t n)
{
    while (n) {
        int sent = send(fd, buf, n, 0);
        if (sent < 0 && errno == EINTR) continue;
        if (sent <= 0 || !atomic_load(&s_up)) {
            ESP_LOGW("rmlink_wifi","send ended: result=%d errno=%d remaining=%u free_internal=%u min_internal=%u",
                sent,sent<0?errno:0,(unsigned)n,
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),
                (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));
            return false;
        }
        buf += sent; n -= sent;
    }
    return true;
}
typedef struct { size_t len; uint8_t data[RMLINK_PACKET_SIZE]; } tcp_reply_t;
typedef struct {
    int fd;
    unsigned network_generation;
    QueueHandle_t replies;
    SemaphoreHandle_t stopped;
    atomic_bool closing;
} tcp_connection_t;

static bool network_current(unsigned generation)
{
    return atomic_load(&s_up) && generation == atomic_load(&s_network_generation);
}

static bool tcp_configure_keepalive(int client)
{
    /* Probe the TCP peer, without expiring healthy idle CMSIS-DAP sessions or
     * adding target commands. ESP-IDF 5.1 lwIP supports these seconds options. */
    int enabled = 1, idle = 5, interval = 2, count = 3;
    if (setsockopt(client, SOL_SOCKET, SO_KEEPALIVE, &enabled, sizeof(enabled)) ||
        setsockopt(client, IPPROTO_TCP, TCP_KEEPIDLE, &idle, sizeof(idle)) ||
        setsockopt(client, IPPROTO_TCP, TCP_KEEPINTVL, &interval, sizeof(interval)) ||
        setsockopt(client, IPPROTO_TCP, TCP_KEEPCNT, &count, sizeof(count))) {
        ESP_LOGW("rmlink_wifi", "TCP keepalive configuration failed: %d", errno);
        return false;
    }
    return true;
}

static void tcp_reply(void *arg, const uint8_t *data, size_t len)
{
    tcp_connection_t *c = arg;
    if (atomic_load(&c->closing) || !network_current(c->network_generation)) return;
    tcp_reply_t reply = { .len = len };
    memcpy(reply.data, data, len);
    if (xQueueSend(c->replies, &reply, pdMS_TO_TICKS(100)) != pdTRUE) {
        ESP_LOGW("rmlink_wifi", "TCP reply queue timeout, response=%u", (unsigned)len);
        atomic_store(&c->closing, true);
        shutdown(c->fd, SHUT_RDWR); /* Never silently lose a reply and keep the stream alive. */
    }
}
static void tcp_send_task(void *arg)
{
    tcp_connection_t *c = arg;
    tcp_reply_t reply;
    while (!atomic_load(&c->closing) && network_current(c->network_generation)) {
        if (xQueueReceive(c->replies, &reply, pdMS_TO_TICKS(100)) != pdTRUE) continue;
        if (!network_current(c->network_generation)) break;
        uint8_t frame[RMLINK_TCP_HEADER_SIZE + RMLINK_PACKET_SIZE];
        rmlink_tcp_header_encode(frame, reply.len);
        memcpy(frame + RMLINK_TCP_HEADER_SIZE, reply.data, reply.len);
        if (!send_exact(c->fd, frame, RMLINK_TCP_HEADER_SIZE + reply.len)) {
            ESP_LOGW("rmlink_wifi", "TCP response send failed: errno=%d", errno);
            atomic_store(&c->closing, true); shutdown(c->fd, SHUT_RDWR);
        }
    }
    atomic_store(&c->closing, true);
    shutdown(c->fd, SHUT_RDWR); /* Wake RX even after a short STA down/up. */
    xSemaphoreGive(c->stopped);
    vTaskDelete(NULL);
}
static void tcp_task(void *arg)
{
    uint32_t session = 1;
    for (;;) {
        while (!atomic_load(&s_up)) vTaskDelay(pdMS_TO_TICKS(250));
        unsigned network_generation = atomic_load(&s_network_generation);
        int listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listener < 0) { vTaskDelay(pdMS_TO_TICKS(1000)); continue; }
        int reuse = 1;
        setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
        struct sockaddr_in addr = { .sin_family = AF_INET, .sin_addr.s_addr = INADDR_ANY,
            .sin_port = htons(CONFIG_RMLINK_TCP_PORT) };
        if (bind(listener, (struct sockaddr *)&addr, sizeof(addr)) || listen(listener, 1)) {
            close(listener); vTaskDelay(pdMS_TO_TICKS(1000)); continue;
        }
        while (network_current(network_generation)) {
            fd_set fds; FD_ZERO(&fds); FD_SET(listener, &fds);
            struct timeval poll = { .tv_usec = 250000 };
            if (select(listener + 1, &fds, NULL, NULL, &poll) <= 0) continue;
            int client = accept(listener, NULL, NULL);
            if (client < 0) continue;
            if (!network_current(network_generation)) { close(client); break; }
            if (!tcp_configure_keepalive(client)) { close(client); continue; }
            if (++session == 0) ++session;
            struct timeval timeout = { .tv_sec = 2 };
            setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
            setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
            int no_delay = 1;
            setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &no_delay, sizeof(no_delay));
            tcp_connection_t connection = { .fd = client, .network_generation = network_generation };
            connection.replies = xQueueCreate(8, sizeof(tcp_reply_t));
            connection.stopped = xSemaphoreCreateBinary();
            if (!connection.replies || !connection.stopped ||
                xTaskCreate(tcp_send_task, "rmlink_tcp_tx", 4096, &connection, 4, NULL) != pdPASS) {
                if (connection.replies) vQueueDelete(connection.replies);
                if (connection.stopped) vSemaphoreDelete(connection.stopped);
                close(client); continue;
            }
            rmlink_tcp_session_open(session);
            unsigned received_requests=0;
            const char *end_reason="network or sender closed";
            while (network_current(network_generation) && !atomic_load(&connection.closing)) {
                FD_ZERO(&fds); FD_SET(client, &fds); poll = (struct timeval){ .tv_usec = 250000 };
                int ready = select(client + 1, &fds, NULL, NULL, &poll);
                if (ready < 0) break;
                if (ready == 0) continue;
                uint8_t header[RMLINK_TCP_HEADER_SIZE], request[RMLINK_PACKET_SIZE];
                size_t len = 0;
                if (!recv_exact(client, header, sizeof(header))) {end_reason="header read";break;}
                if (!rmlink_tcp_header_decode(header, &len)) {end_reason="invalid header";break;}
                if (!recv_exact(client, request, len)) {end_reason="payload read";break;}
                if (!network_current(network_generation) || atomic_load(&connection.closing)) break;
                if (rmlink_submit_tcp(session, request, len, tcp_reply, &connection) != ESP_OK) {end_reason="executor submit";break;}
                received_requests++;
            }
            ESP_LOGI("rmlink_wifi", "TCP session end: %s, requests=%u errno=%d",end_reason,received_requests,errno);
            atomic_store(&connection.closing, true);
            shutdown(client, SHUT_RDWR);
            /* Invalidate/abort then drain the executor before freeing callback context. */
            rmlink_transport_closed(RMLINK_TCP, session);
            xSemaphoreTake(connection.stopped, portMAX_DELAY);
            close(client);
            vQueueDelete(connection.replies);
            vSemaphoreDelete(connection.stopped);
        }
        close(listener); /* Network-only recovery; USB state is untouched. */
    }
}
size_t rmlink_vendor_command(uint8_t transport, const uint8_t *r, size_t len,
        uint8_t *out, const rmlink_owner_t *owner)
{
    out[0] = 0x80; out[1] = len > 1 ? r[1] : 0xff; out[2] = 1;
    if (len < 2) return 3;
    if (r[1] == 9) { /* Stable probe identity, no target access or credentials. */
        out[2] = 0;
        esp_efuse_mac_get_default(out + 3);
        return 9;
    }
    if (r[1] == 7) { /* Transport-capacity deterministic reply; no target access. */
        if (len < 6) return 3;
        out[2] = 0; memcpy(out + 3, r + 2, 4);
        size_t capacity = transport == RMLINK_USB ? RMLINK_USB_PACKET_SIZE : RMLINK_PACKET_SIZE;
        for (unsigned i = 7; i < capacity; ++i)
            out[i] = (uint8_t)(r[2] + (i - 7) * 31u);
        return capacity;
    }
    if (r[1] == 6) {
        out[2] = 0;
        uint64_t uptime = (uint64_t)esp_timer_get_time();
        uint32_t reason = (uint32_t)esp_reset_reason();
        for (unsigned i = 0; i < 8; ++i) out[3 + i] = (uint8_t)(uptime >> (8 * i));
        for (unsigned i = 0; i < 4; ++i) out[11 + i] = (uint8_t)(reason >> (8 * i));
        return 15;
    }
    if (r[1] == 10) { /* Fixed USB64 snapshot; no target GPIO or owner claim. */
        out[2]=0;out[3]=1;
        const uint32_t caps[]={MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT};
        for(unsigned j=0;j<2;j++) {
            uint32_t stats[]={heap_caps_get_free_size(caps[j]),heap_caps_get_largest_free_block(caps[j]),heap_caps_get_minimum_free_size(caps[j])};
            for(unsigned k=0;k<3;k++)for(unsigned i=0;i<4;i++)out[4+j*12+k*4+i]=stats[k]>>(i*8);
        }
#if CONFIG_DEBUG_PROBE_IFACE_SWD
        uint32_t requested=rmlink_requested_swj_clock();
        rmlink_capture_failure_stats(out+40);
#else
        uint32_t requested=0;memset(out+40,0,24);
#endif
        for(unsigned i=0;i<4;i++)out[28+i]=requested>>(i*8);
        uint64_t now=esp_timer_get_time();for(unsigned i=0;i<8;i++)out[32+i]=now>>(i*8);
        return 64;
    }
    if (r[1] == 11) {
#if CONFIG_DEBUG_PROBE_IFACE_SWD
        out[2]=0;rmlink_capture_performance_stats(out+3);return 63;
#else
        out[2]=2;return 3;
#endif
    }
    if (r[1] == 12) {
#if CONFIG_DEBUG_PROBE_IFACE_SWD
        out[2]=0;rmlink_capture_read_recovery_stats(out+3);return 24;
#else
        out[2]=2;return 3;
#endif
    }
    if (r[1] == 13) {
#if CONFIG_DEBUG_PROBE_IFACE_SWD
        out[2]=0;rmlink_capture_fault_recovery_stats(out+3);return 32;
#else
        out[2]=2;return 3;
#endif
    }
    if (r[1] == 14) { /* Probe-only diagnostic switch; default off. */
#if CONFIG_DEBUG_PROBE_IFACE_SWD
        if(len==3 && r[2]<=1) {rmlink_capture_profile_enable(r[2]);out[2]=0;}
#else
        out[2]=2;
#endif
        return 3;
    }
    if (r[1] == 4) {
        out[2] = 0; out[3] = s_config.ssid[0] != 0; out[4] = atomic_load(&s_up);
        int8_t rssi = 0; esp_netif_ip_info_t ip = {0};
        if (out[4]) {
            wifi_ap_record_t ap;
            if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) rssi = ap.rssi;
            esp_netif_get_ip_info(s_sta, &ip);
        }
        out[5] = (uint8_t)rssi; memcpy(out + 6, &ip.ip.addr, 4); return 10;
    }
    if (transport != RMLINK_USB || !s_manager) { out[2] = 2; return 3; }
    if (r[1] == 8) { /* Explicit USB arming; expires automatically, never claims SWD. */
        if (len < 4 || owner->session) return 3;
        unsigned seconds = (unsigned)r[2] | ((unsigned)r[3] << 8);
        out[2] = rmlink_transport_bench_arm(seconds) == ESP_OK ? 0 : 1;
        return 3;
    }
    if (r[1] == 5) { /* Network-only reconnect, useful for recovery acceptance. */
        out[2] = esp_wifi_disconnect() == ESP_OK ? 0 : 1;
        return 3;
    }
    if (r[1] == 2 && len >= 6) {
        unsigned field = r[2], offset = r[3], total = r[4], chunk = r[5];
        if (field > 1 || total > (field ? 63u : 32u) || offset > total || chunk > total - offset
            || chunk > len - 6 || (!field && !total)) return 3;
        char *dest = field ? s_staged.password : s_staged.ssid;
        for (unsigned i = 0; i < chunk; ++i) if (!r[6 + i]) return 3;
        if (!offset || total != s_total[field]) {
            memset(dest, 0, field ? sizeof(s_staged.password) : sizeof(s_staged.ssid));
            s_received[field] = 0;
        }
        s_total[field] = total; s_field_set[field] = true;
        memcpy(dest + offset, r + 6, chunk);
        for (unsigned i = 0; i < chunk; ++i) s_received[field] |= UINT64_C(1) << (offset + i);
        out[2] = 0; return 3;
    }
    if (r[1] == 3) {
        if (!s_field_set[0] || !s_field_set[1] || !s_total[0] || (s_total[1] && s_total[1] < 8)) return 3;
        for (unsigned field = 0; field < 2; ++field)
            if (s_received[field] != ((UINT64_C(1) << s_total[field]) - 1)) return 3;
        nvs_handle_t nvs;
        esp_err_t err = nvs_open("rmlink", NVS_READWRITE, &nvs);
        if (err != ESP_OK) return 3;
        err = nvs_set_blob(nvs, "wifi", &s_staged, sizeof(s_staged));
        if (err == ESP_OK) err = nvs_commit(nvs);
        nvs_close(nvs);
        if (err != ESP_OK) return 3;
        portENTER_CRITICAL(&s_config_mux); s_config = s_staged; portEXIT_CRITICAL(&s_config_mux);
        atomic_fetch_add(&s_revision, 1); xTaskNotifyGive(s_manager);
        memset(&s_staged, 0, sizeof(s_staged)); s_staged.version = 1;
        memset(s_field_set, 0, sizeof(s_field_set)); memset(s_received, 0, sizeof(s_received));
        out[2] = 0; return 3;
    }
    return 3;
}
esp_err_t rmlink_network_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err != ESP_OK) return err; /* Don't erase an existing board's entire NVS. */
    nvs_handle_t nvs;
    if (nvs_open("rmlink", NVS_READONLY, &nvs) == ESP_OK) {
        credentials_t saved; size_t size = sizeof(saved);
        if (nvs_get_blob(nvs, "wifi", &saved, &size) == ESP_OK && size == sizeof(saved)
            && saved.version == 1 && memchr(saved.ssid, 0, sizeof(saved.ssid))
            && memchr(saved.password, 0, sizeof(saved.password))) s_config = saved;
        nvs_close(nvs);
    }
    ESP_RETURN_ON_ERROR(esp_netif_init(), "rmlink_wifi", "netif init");
    ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), "rmlink_wifi", "event init");
    s_sta = esp_netif_create_default_wifi_sta();
    if (!s_sta) return ESP_ERR_NO_MEM;
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&cfg), "rmlink_wifi", "wifi init");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL), "rmlink_wifi", "Wi-Fi events");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL), "rmlink_wifi", "IP events");
    ESP_RETURN_ON_ERROR(esp_wifi_set_storage(WIFI_STORAGE_RAM), "rmlink_wifi", "Wi-Fi storage");
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), "rmlink_wifi", "Wi-Fi mode");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), "rmlink_wifi", "Wi-Fi start");
    esp_wifi_set_ps(WIFI_PS_NONE);
    uint8_t mac[6]; esp_efuse_mac_get_default(mac);
    char hostname[32]; snprintf(hostname, sizeof(hostname), "rmlink-%02x%02x%02x", mac[3], mac[4], mac[5]);
    if (mdns_init() == ESP_OK) {
        mdns_hostname_set(hostname); mdns_instance_name_set("RMLink development");
        mdns_service_add(NULL, "_rmlink", "_tcp", CONFIG_RMLINK_TCP_PORT, NULL, 0);
    }
    if (xTaskCreate(connection_task, "rmlink_wifi", 4096, NULL, 4, &s_manager) != pdPASS) return ESP_ERR_NO_MEM;
    /* RX must run during a busy SWD loop on the other CPU, without touching GPIO. */
    int network_core=0;
#if !CONFIG_FREERTOS_UNICORE
    network_core=1-esp_cpu_get_core_id();
#if CONFIG_DEBUG_PROBE_IFACE_SWD
    network_core=1-CONFIG_RMLINK_EXECUTOR_CORE;
#endif
#endif
    if (xTaskCreatePinnedToCore(tcp_task, "rmlink_tcp", 6144, NULL, 4, NULL,
                              network_core) != pdPASS) return ESP_ERR_NO_MEM;
    esp_err_t bench_result = rmlink_transport_bench_init();
    if (bench_result != ESP_OK) ESP_LOGW("rmlink_wifi", "Transport benchmark unavailable: %s", esp_err_to_name(bench_result));
    ESP_LOGI("rmlink_wifi", "STA + TCP ready; configure credentials using USB vendor commands");
    return ESP_OK;
}
#else
#include "rmlink_network.h"
esp_err_t rmlink_network_init(void) { return ESP_OK; }
#endif
