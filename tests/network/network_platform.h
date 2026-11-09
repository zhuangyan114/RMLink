/* Host adapters for the actual Wi-Fi event handlers. No device I/O. */
#pragma once
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdatomic.h>

#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_EVENT_DEFINE_BASE(name) const char *name = #name
#define ESP_LOGI(...) ((void)0)
#define portMUX_INITIALIZER_UNLOCKED 0
#define pdMS_TO_TICKS(n) (n)
typedef int portMUX_TYPE;
typedef void *TaskHandle_t;
typedef const char *esp_event_base_t;
static const char *WIFI_EVENT = "wifi";
static const char *IP_EVENT = "ip";
enum { WIFI_EVENT_STA_CONNECTED, WIFI_EVENT_STA_DISCONNECTED, WIFI_EVENT_STA_STOP, IP_EVENT_STA_GOT_IP };
typedef struct { uint8_t bssid[6]; } wifi_event_sta_connected_t;
typedef struct { uint32_t addr; } ip_address_t;
typedef struct { ip_address_t ip, netmask, gw; } esp_netif_ip_info_t;
typedef struct { esp_netif_ip_info_t ip; unsigned down, connected; } esp_netif_t;
typedef struct { esp_netif_t *esp_netif; esp_netif_ip_info_t ip_info; bool ip_changed; } ip_event_got_ip_t;
typedef struct { bool active; int64_t due; } host_timer_t;
typedef host_timer_t *esp_timer_handle_t;

static int64_t host_time;
static unsigned host_notifications;
static bool host_post_failure;
static struct {
    esp_event_base_t base;
    int32_t id;
    ip_event_got_ip_t ip;
} host_events[16];
static unsigned host_event_count;

static inline int64_t esp_timer_get_time(void) { return host_time; }
static inline int esp_timer_stop(esp_timer_handle_t timer) { timer->active = false; return ESP_OK; }
static inline int esp_timer_start_once(esp_timer_handle_t timer, int64_t delay)
{
    timer->active = true;
    timer->due = host_time + delay;
    return ESP_OK;
}
static inline int esp_event_post(esp_event_base_t base, int32_t id, const void *data, size_t size, unsigned wait)
{
    (void)wait;
    if (host_post_failure) return ESP_FAIL;
    assert(host_event_count < 16 && size <= sizeof(ip_event_got_ip_t));
    host_events[host_event_count].base = base;
    host_events[host_event_count].id = id;
    if (size) memcpy(&host_events[host_event_count].ip, data, size);
    host_event_count++;
    return ESP_OK;
}
static inline int esp_netif_get_ip_info(esp_netif_t *netif, esp_netif_ip_info_t *ip)
{
    *ip = netif->ip;
    return ESP_OK;
}
static inline void esp_netif_action_connected(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)base; (void)id; (void)data;
    ((esp_netif_t *)arg)->connected++;
}
static inline void xTaskNotifyGive(TaskHandle_t task) { (void)task; host_notifications++; }
void host_delay(unsigned ticks);
static inline void vTaskDelay(unsigned ticks) { host_delay(ticks); }
