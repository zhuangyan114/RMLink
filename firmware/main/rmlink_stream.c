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
            setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
            /* Bulk timestamped samples benefit from full TCP segments. The
             * independent control socket keeps NODELAY for control latency. */
            int no_delay=0; setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&no_delay,sizeof(no_delay));
            uint8_t hello[20]; uint32_t generation;
            if(!exact(fd,hello,20,false) || memcmp(hello,"RMS1",4) || !rmlink_capture_bind(hello+4,&generation)) { close(fd); continue; }
            uint8_t ok[4]={'O','K','A','Y'};
            if(!exact(fd,ok,4,true)) { rmlink_capture_stream_lost(generation); close(fd); continue; }
            uint32_t batches=0,max_wake_us=0,max_send_us=0;uint64_t bytes=0,last_warning=0;
            while(rmlink_capture_stream_live(generation)) {
                uint8_t batch[4096];size_t used=rmlink_capture_take(batch,sizeof(batch),10);
                if(used) {
                    /* Bound stream aggregation to 2 ms. One 96-byte TCP packet
                     * per sample otherwise consumes Wi-Fi TX buffers far
                     * faster than application bytes suggest. Control uses a
                     * separate NODELAY socket and never waits for this timer. */
                    ulTaskNotifyTake(pdTRUE,0);
                    atomic_store(&s_batch_fired,0);
                    if(esp_timer_start_once(s_batch_timer,2000)==ESP_OK)
                        ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(20));
                    uint32_t fired=atomic_load(&s_batch_fired);
                    uint32_t lag=fired?(uint32_t)esp_timer_get_time()-fired:0;
                    if(lag>max_wake_us)max_wake_us=lag;
                }
                do {size_t n=rmlink_capture_take(batch+used,sizeof(batch)-used,0);if(!n)break;used+=n;}
                while(used+384<=sizeof(batch));
                uint64_t before_send=esp_timer_get_time();
                bool sent=!used || exact(fd,batch,used,true);
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
            ESP_LOGI("rmlink_stream","closed: batches=%u bytes=%llu wake_max_us=%u send_max_us=%u free_internal=%u min_internal=%u",
                (unsigned)batches,(unsigned long long)bytes,
                (unsigned)max_wake_us,(unsigned)max_send_us,
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),
                (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));
            rmlink_capture_stream_lost(generation); shutdown(fd,SHUT_RDWR); close(fd);
        }
        close(listener);
    }
}
esp_err_t rmlink_stream_init(void) {
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
