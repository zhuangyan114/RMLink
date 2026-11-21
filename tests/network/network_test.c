/* Drive the production event handlers in the same order as ESP-IDF. */
#include <stdio.h>
#define RMLINK_NETWORK_HOST_TEST 1
#include "../../firmware/main/rmlink_network.c"

void __real_esp_netif_action_disconnected(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)base; (void)id; (void)data;
    esp_netif_t *netif = arg;
    netif->down++;
    memset(&netif->ip, 0, sizeof(netif->ip));
}

static esp_netif_t station;
static host_timer_t timer;
static int64_t host_reassociate_at;
static wifi_event_sta_connected_t access_point = { .bssid = {1, 2, 3, 4, 5, 6} };

static void drain_events(void)
{
    for (unsigned i = 0; i < host_event_count; i++)
        wifi_event(NULL, host_events[i].base, host_events[i].id, &host_events[i].ip);
    host_event_count = 0;
}

static unsigned online(void)
{
    memset(&station, 0, sizeof(station));
    memset(&timer, 0, sizeof(timer));
    s_sta = &station;
    s_reconnect_timer = &timer;
    s_manager = &station;
    s_reconnect_deadline = 0;
    s_ip_address = 0;
    s_associated = false;
    atomic_store(&s_enabled, true);
    atomic_store(&s_up, false);
    atomic_store(&s_tcp_network_live, false);
    host_time = 1000000;
    host_reassociate_at = 0;
    host_event_count = host_notifications = 0;
    host_post_failure = false;
    wifi_event(NULL, WIFI_EVENT, WIFI_EVENT_STA_CONNECTED, &access_point);
    station.ip.ip.addr = 0x01020304;
    ip_event_got_ip_t ip = { .esp_netif = &station, .ip_info = station.ip };
    wifi_event(NULL, IP_EVENT, IP_EVENT_STA_GOT_IP, &ip);
    return atomic_load(&s_network_generation);
}

static void disconnect(void)
{
    __wrap_esp_netif_action_disconnected(s_sta, WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, NULL);
    wifi_event(NULL, WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, NULL);
}

static void reassociate(wifi_event_sta_connected_t *ap)
{
    esp_netif_action_connected(s_sta, WIFI_EVENT, WIFI_EVENT_STA_CONNECTED, ap);
    wifi_event(NULL, WIFI_EVENT, WIFI_EVENT_STA_CONNECTED, ap);
    drain_events();
}

void host_delay(unsigned ticks)
{
    host_time += ticks * 1000;
    if (host_reassociate_at && host_time >= host_reassociate_at) {
        host_reassociate_at = 0;
        reassociate(&access_point);
    }
    if (timer.active && host_time >= timer.due) {
        timer.active = false;
        reconnect_expired(NULL);
        drain_events();
    }
}

int main(void)
{
    unsigned generation = online();
    disconnect();
    host_reassociate_at = host_time + 500000;
    assert(rmlink_network_wait_for_link());
    assert(host_time == 1500000 && network_current(generation));

    generation = online();
    disconnect();
    assert(!rmlink_network_wait_for_link());
    assert(host_time == 2000000 && !network_current(generation));

    generation = online();
    disconnect();
    assert(!atomic_load(&s_up) && network_current(generation));
    assert(station.down == 0 && timer.active && host_notifications == 1);
    host_time += 500000;
    reassociate(&access_point);
    assert(atomic_load(&s_up) && network_current(generation));
    assert(station.down == 0 && !timer.active && s_reconnect_deadline == 0);
    reconnect_expired(NULL); drain_events(); /* A timer queued before recovery is harmless. */
    assert(network_current(generation));

    generation = online();
    disconnect();
    int64_t first_deadline = s_reconnect_deadline;
    host_time += 400000;
    disconnect();
    assert(s_reconnect_deadline == first_deadline); /* Repeated failures cannot renew grace. */
    host_time = first_deadline;
    reconnect_expired(NULL); drain_events();
    assert(station.down == 1 && !network_current(generation) && !atomic_load(&s_up));
    reassociate(&access_point);
    assert(!atomic_load(&s_up)); /* Normal recovery waits for a fresh DHCP address. */

    generation = online();
    disconnect();
    host_time += WIFI_RECONNECT_GRACE_US + 1;
    reassociate(&access_point); /* Expiry event can be queued after STA_CONNECTED. */
    assert(station.down == 1 && !network_current(generation));
    assert(station.connected == 2);

    generation = online();
    disconnect();
    wifi_event_sta_connected_t other_ap = { .bssid = {6, 5, 4, 3, 2, 1} };
    reassociate(&other_ap);
    assert(station.down == 1 && !network_current(generation));

    generation = online();
    ip_event_got_ip_t changed = { .esp_netif = &station, .ip_info = station.ip };
    changed.ip_info.ip.addr++;
    wifi_event(NULL, IP_EVENT, IP_EVENT_STA_GOT_IP, &changed);
    assert(!network_current(generation) && atomic_load(&s_up));

    generation = online();
    disconnect();
    host_post_failure = true;
    host_time = s_reconnect_deadline;
    reconnect_expired(NULL);
    assert(timer.due == host_time + 10000);
    host_post_failure = false;
    host_time += 10000;
    reconnect_expired(NULL); drain_events();
    assert(!network_current(generation));

    generation = online();
    disconnect();
    host_time += 500000;
    host_post_failure = true;
    reassociate(&access_point);
    assert(station.down == 1 && !network_current(generation));

    generation = online();
    atomic_store(&s_tcp_network_live, false); /* Explicit configuration change. */
    atomic_store(&s_up, false);
    disconnect();
    assert(station.down == 1 && !network_current(generation));

    generation = online();
    esp_netif_t other = {0};
    __wrap_esp_netif_action_disconnected(&other, WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, NULL);
    assert(other.down == 1 && station.down == 0 && network_current(generation));
    generation = online();
    assert(rmlink_network_is_connected());
    rmlink_network_set_enabled(false);
    assert(!rmlink_network_is_connected() && !network_current(generation));
    unsigned disabled_generation = atomic_load(&s_network_generation);
    rmlink_network_set_enabled(false);
    assert(atomic_load(&s_network_generation) == disabled_generation);
    ip_event_got_ip_t late_ip = { .esp_netif = &station, .ip_info = station.ip };
    wifi_event(NULL, IP_EVENT, IP_EVENT_STA_GOT_IP, &late_ip);
    assert(!atomic_load(&s_up) && !atomic_load(&s_tcp_network_live));
    /* A GOT_IP callback already past its enable check cannot revive USB mode. */
    atomic_store(&s_up, true);
    atomic_store(&s_tcp_network_live, true);
    assert(!rmlink_network_is_connected() && !network_current(disabled_generation));
    disconnect();
    assert(!timer.active); /* Manual USB selection bypasses reassociation grace. */
    rmlink_network_set_enabled(true);
    assert(!rmlink_network_is_connected());
    reassociate(&access_point);
    wifi_event(NULL, IP_EVENT, IP_EVENT_STA_GOT_IP, &late_ip);
    assert(rmlink_network_is_connected());

    /* Disabling Wi-Fi during the reassociation grace period immediately cancels timer and deadline. */
    generation = online();
    disconnect();
    assert(timer.active && atomic_load(&s_reconnect_deadline) > 0);
    rmlink_network_set_enabled(false);
    assert(!rmlink_network_is_enabled() && !timer.active && atomic_load(&s_reconnect_deadline) == 0);
    atomic_store(&s_tcp_client_connected, true);
    assert(!rmlink_network_has_client());
    rmlink_network_set_enabled(true);
    assert(rmlink_network_is_enabled() && rmlink_network_has_client());
    puts("Wi-Fi recovery tests passed (reassociation, expiry, event races, manual mode selection)");
    return 0;
}
