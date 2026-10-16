/* SPDX-License-Identifier: Apache-2.0 */
#include "sdkconfig.h"
#if CONFIG_RMLINK_WIFI_ENABLED && CONFIG_DEBUG_PROBE_IFACE_SWD
#include <errno.h>
#include <unistd.h>
#include <string.h>
#include <stdatomic.h>
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "rmlink_capture.h"
#include "rmlink_stream_codec.h"
#if CONFIG_RMLINK_STREAM_DIAGNOSTICS
#include "esp_wifi.h"
#include "lwip/tcpip.h"
/* ESP-IDF 5.1.2 pinned by this project. PCB access is confined to tcpip_thread. */
#include "lwip/priv/tcp_priv.h"
static esp_timer_handle_t s_diagnostic_timer;
static atomic_llong s_send_started;
static atomic_bool s_diagnostic_pending;
static int64_t s_diagnostic_requested, s_diagnostic_last;
static void tcp_diagnostic(void *unused) {
    (void)unused;
    int64_t now=esp_timer_get_time(),started=atomic_load(&s_send_started);
    for(struct tcp_pcb *p=tcp_active_pcbs;p;p=p->next) {
        if(p->local_port!=3244 || p->state!=ESTABLISHED)continue;
        unsigned unacked=0,unsent=0;
        for(struct tcp_seg *s=p->unacked;s;s=s->next)unacked++;
        for(struct tcp_seg *s=p->unsent;s;s=s->next)unsent++;
        ESP_LOGW("rmlink_stream","TCP sender: blocked_us=%u tcpip_wait_us=%u rto_ms=%u retries=%u dupacks=%u cwnd=%u wnd=%u sndbuf=%u unacked=%u unsent=%u inflight=%u queued=%u",
            started?(unsigned)(now-started):0,(unsigned)(now-s_diagnostic_requested),
            (unsigned)p->rto*TCP_SLOW_INTERVAL,(unsigned)p->nrtx,(unsigned)p->dupacks,
            (unsigned)p->cwnd,(unsigned)p->snd_wnd,(unsigned)p->snd_buf,unacked,unsent,
            (unsigned)(p->snd_nxt-p->lastack),(unsigned)(p->snd_lbb-p->lastack));
    }
    atomic_store(&s_diagnostic_pending,false);
}
static void diagnose_stalled_send(void *unused) {
    (void)unused;
    int64_t now=esp_timer_get_time(),started=atomic_load(&s_send_started);
    if(!started || now-started<100000 || now-s_diagnostic_last<1000000)return;
    bool expected=false;
    if(!atomic_compare_exchange_strong(&s_diagnostic_pending,&expected,true))return;
    s_diagnostic_requested=now;s_diagnostic_last=now;
    if(tcpip_try_callback(tcp_diagnostic,NULL)!=ERR_OK)atomic_store(&s_diagnostic_pending,false);
}
#endif
static esp_timer_handle_t s_batch_timer;
static TaskHandle_t s_stream_task;
static atomic_uint s_batch_fired;
static void batch_ready(void *unused) {
    (void)unused;atomic_store(&s_batch_fired,(uint32_t)esp_timer_get_time());
    if(s_stream_task)xTaskNotifyGive(s_stream_task);
}
static bool exact(int fd, uint8_t *p, size_t n, bool sending) {
    while(n) {
        int part=sending?send(fd,p,n,0):recv(fd,p,n,0);
        if(part<0 && errno==EINTR)continue;
        if(part<=0) {
            ESP_LOGW("rmlink_stream","%s failed: result=%d errno=%d remaining=%u free_internal=%u min_internal=%u",
                sending?"send":"recv",part,part<0?errno:0,(unsigned)n,
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),
                (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));
            return false;
        }
        p+=part;n-=part;
    }
    return true;
}
static void stream_task(void *unused) {
    s_stream_task=xTaskGetCurrentTaskHandle();
    for(;;) {
        int listener=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP), reuse=1;
        if(listener<0) { vTaskDelay(pdMS_TO_TICKS(1000)); continue; }
        setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&reuse,sizeof(reuse));
        struct sockaddr_in addr={.sin_family=AF_INET,.sin_addr.s_addr=INADDR_ANY,.sin_port=htons(3244)};
        if(bind(listener,(struct sockaddr*)&addr,sizeof(addr)) || listen(listener,1)) {
            close(listener); vTaskDelay(pdMS_TO_TICKS(1000)); continue;
        }
        for(;;) {
            int fd=accept(listener,NULL,NULL); if(fd<0) break;
            struct timeval timeout={.tv_sec=2};
            setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
            /* The independent control socket remains responsive. Allow this
             * data socket to recover within the bounded PSRAM capture budget. */
            timeout.tv_sec=5;
            setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
            /* Application batching bounds packet rate. Do not add Nagle's
             * partial-segment wait on top of the host's delayed TCP ACKs. */
            int no_delay=1; setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&no_delay,sizeof(no_delay));
#if CONFIG_RMLINK_STREAM_IP_TOS
            int tos=CONFIG_RMLINK_STREAM_IP_TOS;
            if(setsockopt(fd,IPPROTO_IP,IP_TOS,&tos,sizeof(tos)))
                ESP_LOGW("rmlink_stream","IP_TOS failed: errno=%d",errno);
#endif
            uint8_t hello[20]; uint32_t generation;
            if(!exact(fd,hello,20,false) || (memcmp(hello,"RMS1",4) && memcmp(hello,"RMS2",4) && memcmp(hello,"RMS3",4)) || !rmlink_capture_bind(hello+4,&generation)) { close(fd); continue; }
            unsigned compact=hello[3]-'1';
            uint8_t ok[4]={'O','K','A','Y'};
            if(!exact(fd,ok,4,true)) { rmlink_capture_stream_lost(generation); close(fd); continue; }
            uint32_t batches=0,max_wake_us=0,max_send_us=0;uint64_t bytes=0,raw_bytes=0,last_warning=0;
#if CONFIG_RMLINK_STREAM_DIAGNOSTICS
            esp_timer_start_periodic(s_diagnostic_timer,100000);
#endif
            while(rmlink_capture_stream_live(generation)) {
                uint8_t batch[4096];size_t used=rmlink_capture_take(batch,sizeof(batch),10);
                /* Drain an existing backlog before delaying. Waiting another
                 * 8 ms for an already full batch caps recovery throughput and
                 * turns a transient TCP stall into continuing ring overflow. */
                while(used && used+384<=sizeof(batch)) {
                    size_t n=rmlink_capture_take(batch+used,sizeof(batch)-used,0);
                    if(!n)break;
                    used+=n;
                }
                if(used && used+384<=sizeof(batch)) {
                    /* Bound stream aggregation to 8 ms. One 96-byte TCP packet
                     * per sample otherwise consumes Wi-Fi TX buffers far
                     * faster than application bytes suggest. Control uses a
                     * separate NODELAY socket and never waits for this timer. */
                    ulTaskNotifyTake(pdTRUE,0);
                    atomic_store(&s_batch_fired,0);
                    if(esp_timer_start_once(s_batch_timer,8000)==ESP_OK)
                        ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(20));
                    uint32_t fired=atomic_load(&s_batch_fired);
                    uint32_t lag=fired?(uint32_t)esp_timer_get_time()-fired:0;
                    if(lag>max_wake_us)max_wake_us=lag;
                }
                do {size_t n=rmlink_capture_take(batch+used,sizeof(batch)-used,0);if(!n)break;used+=n;}
                while(used+384<=sizeof(batch));
                raw_bytes+=used;
                if(compact && used) {used=compact==2?rmlink_stream_pack_delta(batch,used):rmlink_stream_pack(batch,used);if(!used)break;}
                uint64_t before_send=esp_timer_get_time();
#if CONFIG_RMLINK_STREAM_DIAGNOSTICS
                atomic_store(&s_send_started,(int64_t)before_send);
#endif
                bool sent=!used || exact(fd,batch,used,true);
#if CONFIG_RMLINK_STREAM_DIAGNOSTICS
                atomic_store(&s_send_started,0);
#endif
                uint32_t send_us=(uint32_t)(esp_timer_get_time()-before_send);
                if(send_us>max_send_us)max_send_us=send_us;
                if(send_us>50000 && before_send-last_warning>1000000) {
                    ESP_LOGW("rmlink_stream","send stall: us=%u bytes=%u wake_max_us=%u core=%d priority=%u",
                        (unsigned)send_us,(unsigned)used,(unsigned)max_wake_us,esp_cpu_get_core_id(),(unsigned)uxTaskPriorityGet(NULL));
                    last_warning=before_send;
                }
                if(!sent)break;
                if(used){batches++;bytes+=used;}
                fd_set read; FD_ZERO(&read); FD_SET(fd,&read); struct timeval poll={0};
                if(select(fd+1,&read,NULL,NULL,&poll)>0) break; /* EOF or invalid upstream bytes */
            }
#if CONFIG_RMLINK_STREAM_DIAGNOSTICS
            esp_timer_stop(s_diagnostic_timer);
            wifi_ap_record_t ap;
            if(esp_wifi_sta_get_ap_info(&ap)==ESP_OK)
                ESP_LOGI("rmlink_stream","radio: rssi_dbm=%d channel=%u",ap.rssi,ap.primary);
#endif
            ESP_LOGI("rmlink_stream","closed: compact=%u batches=%u bytes=%llu raw_bytes=%llu wake_max_us=%u send_max_us=%u free_internal=%u min_internal=%u",
                compact,(unsigned)batches,(unsigned long long)bytes,(unsigned long long)raw_bytes,
                (unsigned)max_wake_us,(unsigned)max_send_us,
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),
                (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));
            rmlink_capture_stream_lost(generation); shutdown(fd,SHUT_RDWR); close(fd);
        }
        close(listener);
    }
}
esp_err_t rmlink_stream_init(void) {
#if CONFIG_RMLINK_STREAM_DIAGNOSTICS
    esp_timer_create_args_t diagnostic={.callback=diagnose_stalled_send,.name="rmlink_tcpdiag"};
    esp_err_t diagnostic_error=esp_timer_create(&diagnostic,&s_diagnostic_timer);
    if(diagnostic_error!=ESP_OK)return diagnostic_error;
#endif
    esp_timer_create_args_t timer={.callback=batch_ready,.name="rmlink_batch"};
    esp_err_t error=esp_timer_create(&timer,&s_batch_timer);if(error!=ESP_OK)return error;
    int core=0;
#if !CONFIG_FREERTOS_UNICORE
    core=1-CONFIG_RMLINK_EXECUTOR_CORE;
#endif
    if(xTaskCreatePinnedToCore(stream_task,"rmlink_stream",8192,NULL,4,NULL,core)==pdPASS)return ESP_OK;
    esp_timer_delete(s_batch_timer);s_batch_timer=NULL;return ESP_ERR_NO_MEM;
}
#else
#include "esp_err.h"
esp_err_t rmlink_stream_init(void) { return ESP_OK; }
#endif
