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
#include "rmlink_network.h"
#include "rmlink_stream_codec.h"
#include "rmlink_stream.h"
#include "rmlink_stream_deadline.h"
#include "esp_wifi.h"

/* One bounded snapshot per batch; never hold this lock during IO or logging. */
static portMUX_TYPE s_diagnostic_mux=portMUX_INITIALIZER_UNLOCKED;
static struct {
    uint32_t identity,batches,calls,partial,errors,last_errno;
    uint32_t last_send_us,max_send_us,stalls,max_network_us;
    uint32_t last_encode_us,max_encode_us,max_wake_us,last_batch;
    uint64_t dequeued_at,sent_at,wire_bytes,raw_bytes;
    int64_t send_started,network_started;
    uint16_t peer_port;
    bool active;
} s_diag;
static struct {
    uint32_t identity,available,rto_ms,retries,dupacks,cwnd,window,sndbuf;
    uint32_t unacked,unsent,inflight,queued,callback_wait_us;
    uint64_t at;
} s_tcp_diag;
static atomic_llong s_diagnostic_requested;
static void diag32(uint8_t *p,uint32_t v) {for(unsigned i=0;i<4;i++)p[i]=v>>(8*i);}
static void diag64(uint8_t *p,uint64_t v) {for(unsigned i=0;i<8;i++)p[i]=v>>(8*i);}
bool rmlink_stream_diagnostics(unsigned page,uint8_t out[60]) {
    if(page>3)return false;
    int64_t now=esp_timer_get_time();
    portENTER_CRITICAL(&s_diagnostic_mux);
    __typeof__(s_diag) d=s_diag;
    __typeof__(s_tcp_diag) tcp=s_tcp_diag;
    portEXIT_CRITICAL(&s_diagnostic_mux);
    memset(out,0,60);out[0]=1;out[1]=page;
    out[2]=d.active | (d.send_started?4:0) | (d.network_started?8:0);
#if CONFIG_RMLINK_STREAM_DIAGNOSTICS
    out[2]|=2;
#endif
    if(page==0) {
        uint32_t values[]={d.identity,d.batches,d.calls,d.partial,d.errors,d.last_errno};
        for(unsigned i=0;i<6;i++)diag32(out+4+4*i,values[i]);
        diag64(out+28,now);diag64(out+36,d.dequeued_at);
        diag64(out+44,d.sent_at);diag64(out+52,d.wire_bytes);
    } else if(page==1) {
        uint32_t values[]={d.last_send_us,d.max_send_us,d.send_started?(uint32_t)(now-d.send_started):0,
            d.stalls,d.network_started?(uint32_t)(now-d.network_started):0,d.max_network_us,
            d.last_encode_us,d.max_encode_us,d.max_wake_us,d.last_batch};
        for(unsigned i=0;i<10;i++)diag32(out+4+4*i,values[i]);
        diag64(out+44,d.raw_bytes);diag64(out+52,atomic_load(&s_diagnostic_requested));
    } else if(page==2) {
        uint32_t values[]={tcp.identity,tcp.available,tcp.rto_ms,tcp.retries,tcp.dupacks,
            tcp.cwnd,tcp.window,tcp.sndbuf,tcp.unacked,tcp.unsent,tcp.inflight,tcp.queued};
        for(unsigned i=0;i<12;i++)diag32(out+4+4*i,values[i]);
        diag64(out+52,tcp.at);
    } else {
        rmlink_capture_buffer_stats(out+4);
        diag32(out+36,heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));
        diag32(out+40,heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));
        wifi_ap_record_t ap;
        if(esp_wifi_sta_get_ap_info(&ap)==ESP_OK) {diag32(out+44,(int32_t)ap.rssi);diag32(out+48,ap.primary);}
        diag32(out+52,rmlink_network_is_connected());diag32(out+56,tcp.callback_wait_us);
    }
    return true;
}
#if CONFIG_RMLINK_STREAM_DIAGNOSTICS
#include "lwip/tcpip.h"
/* ESP-IDF 5.1.2 pinned by this project. PCB access is confined to tcpip_thread. */
#include "lwip/priv/tcp_priv.h"
static esp_timer_handle_t s_diagnostic_timer;
static atomic_bool s_diagnostic_pending;
static int64_t s_diagnostic_last;
static void tcp_diagnostic(void *unused) {
    (void)unused;
    int64_t now=esp_timer_get_time();
    portENTER_CRITICAL(&s_diagnostic_mux);
    uint32_t identity=s_diag.identity;uint16_t peer=s_diag.peer_port;
    int64_t started=s_diag.send_started;bool active=s_diag.active;
    portEXIT_CRITICAL(&s_diagnostic_mux);
    for(struct tcp_pcb *p=tcp_active_pcbs;p;p=p->next) {
        if(!active || p->local_port!=3244 || p->remote_port!=peer || p->state!=ESTABLISHED)continue;
        unsigned unacked=0,unsent=0;
        for(struct tcp_seg *s=p->unacked;s;s=s->next)unacked++;
        for(struct tcp_seg *s=p->unsent;s;s=s->next)unsent++;
        uint32_t callback_wait=(uint32_t)(now-atomic_load(&s_diagnostic_requested));
        portENTER_CRITICAL(&s_diagnostic_mux);
        if(s_diag.active && s_diag.identity==identity) {
            s_tcp_diag=(__typeof__(s_tcp_diag)){identity,1,p->rto*TCP_SLOW_INTERVAL,p->nrtx,p->dupacks,
                p->cwnd,p->snd_wnd,p->snd_buf,unacked,unsent,p->snd_nxt-p->lastack,p->snd_lbb-p->lastack,callback_wait,now};
        }
        portEXIT_CRITICAL(&s_diagnostic_mux);
        if(started && now-started>=100000)ESP_LOGW("rmlink_stream","TCP sender: stream=%u at_us=%llu blocked_us=%u tcpip_wait_us=%u rto_ms=%u retries=%u dupacks=%u cwnd=%u wnd=%u sndbuf=%u unacked=%u unsent=%u inflight=%u queued=%u",
            (unsigned)identity,(unsigned long long)now,started?(unsigned)(now-started):0,(unsigned)callback_wait,
            (unsigned)p->rto*TCP_SLOW_INTERVAL,(unsigned)p->nrtx,(unsigned)p->dupacks,
            (unsigned)p->cwnd,(unsigned)p->snd_wnd,(unsigned)p->snd_buf,unacked,unsent,
            (unsigned)(p->snd_nxt-p->lastack),(unsigned)(p->snd_lbb-p->lastack));
    }
    atomic_store(&s_diagnostic_pending,false);
}
static void diagnose_stalled_send(void *unused) {
    (void)unused;
    int64_t now=esp_timer_get_time();
    if(now-s_diagnostic_last<1000000)return;
    bool expected=false;
    if(!atomic_compare_exchange_strong(&s_diagnostic_pending,&expected,true))return;
    atomic_store(&s_diagnostic_requested,now);s_diagnostic_last=now;
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
        int64_t waiting=esp_timer_get_time();
        if(sending) {portENTER_CRITICAL(&s_diagnostic_mux);s_diag.network_started=waiting;portEXIT_CRITICAL(&s_diagnostic_mux);}
        bool link=rmlink_network_wait_for_link();
        if(sending) {
            uint32_t elapsed=(uint32_t)(esp_timer_get_time()-waiting);
            portENTER_CRITICAL(&s_diagnostic_mux);
            s_diag.network_started=0;if(elapsed>s_diag.max_network_us)s_diag.max_network_us=elapsed;
            s_diag.calls++;
            portEXIT_CRITICAL(&s_diagnostic_mux);
        }
        if(!link)return false;
        int part=sending?send(fd,p,n,0):recv(fd,p,n,0);
        int error=part<0?errno:0;
        if(sending) {
            portENTER_CRITICAL(&s_diagnostic_mux);
            if(part>0) {s_diag.wire_bytes+=part;if((size_t)part<n)s_diag.partial++;}
            else if(error!=EINTR) {s_diag.errors++;s_diag.last_errno=error;}
            portEXIT_CRITICAL(&s_diagnostic_mux);
        }
        if(part<0 && error==EINTR)continue;
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
typedef struct {int fd;uint32_t generation;} realtime_socket_t;
static int64_t realtime_now(void *context) {(void)context;return esp_timer_get_time();}
static bool realtime_live(void *context) {
    realtime_socket_t *socket=context;
    return rmlink_network_is_enabled() && rmlink_capture_stream_live(socket->generation);
}
static int realtime_write(void *context,const uint8_t *data,size_t size) {
    realtime_socket_t *socket=context;
    int sent=send(socket->fd,data,size,MSG_DONTWAIT);
    int error=sent<0?errno:0;
    portENTER_CRITICAL(&s_diagnostic_mux);
    s_diag.calls++;
    if(sent>0) {s_diag.wire_bytes+=sent;if((size_t)sent<size)s_diag.partial++;}
    portEXIT_CRITICAL(&s_diagnostic_mux);
    if(sent<0 && (error==EAGAIN || error==EWOULDBLOCK || error==EINTR))return 0;
    return sent>0?sent:-1;
}
static bool realtime_wait(void *context,int64_t remaining_us) {
    realtime_socket_t *socket=context;
    fd_set read,write;FD_ZERO(&read);FD_ZERO(&write);
    FD_SET(socket->fd,&read);FD_SET(socket->fd,&write);
    int64_t slice=remaining_us<100000?remaining_us:100000;
    struct timeval timeout={.tv_sec=0,.tv_usec=(long)slice};
    int ready=select(socket->fd+1,&read,&write,NULL,&timeout);
    if(ready<0)return errno==EINTR;
    /* The host sends no bytes after binding. Readability is EOF/cancel or an
     * invalid upstream message; neither may wait behind the send queue. */
    if(ready>0 && FD_ISSET(socket->fd,&read)) {errno=ECONNRESET;return false;}
    return true;
}
static bool realtime_send(int fd,uint32_t generation,uint8_t *data,size_t size,int64_t started_us) {
    realtime_socket_t socket={fd,generation};
    rmlink_stream_io_t io={&socket,realtime_now,realtime_live,realtime_write,realtime_wait};
    int64_t budget=rmlink_stream_realtime_budget(started_us,esp_timer_get_time());
    rmlink_send_result_t result=rmlink_stream_write_deadline(&io,data,size,budget);
    if(result==RMLINK_SEND_OK)return true;
    int error=result==RMLINK_SEND_TIMEOUT?ETIMEDOUT:result==RMLINK_SEND_CANCELLED?ECANCELED:errno;
    portENTER_CRITICAL(&s_diagnostic_mux);s_diag.errors++;s_diag.last_errno=error;portEXIT_CRITICAL(&s_diagnostic_mux);
    ESP_LOGW("rmlink_stream","realtime send ended: result=%d errno=%d",result,error);
    return false;
}
static void stream_task(void *unused) {
    s_stream_task=xTaskGetCurrentTaskHandle();
    for(;;) {
        while(!rmlink_network_is_enabled() || !rmlink_network_is_connected()) vTaskDelay(pdMS_TO_TICKS(250));
        int listener=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP), reuse=1;
        if(listener<0) { vTaskDelay(pdMS_TO_TICKS(1000)); continue; }
        setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&reuse,sizeof(reuse));
        struct sockaddr_in addr={.sin_family=AF_INET,.sin_addr.s_addr=INADDR_ANY,.sin_port=htons(3244)};
        if(bind(listener,(struct sockaddr*)&addr,sizeof(addr)) || listen(listener,1)) {
            close(listener); vTaskDelay(pdMS_TO_TICKS(1000)); continue;
        }
        while(rmlink_network_is_enabled()) {
            fd_set lfds; FD_ZERO(&lfds); FD_SET(listener, &lfds);
            struct timeval lpoll = { .tv_usec = 250000 };
            int lready = select(listener + 1, &lfds, NULL, NULL, &lpoll);
            if(lready < 0) break;
            if(lready == 0) continue;
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
            if(!exact(fd,hello,20,false) || (memcmp(hello,"RMS1",4) && memcmp(hello,"RMS2",4) && memcmp(hello,"RMS3",4) && memcmp(hello,"RMS4",4)) || !rmlink_capture_bind(hello+4,&generation)) { close(fd); continue; }
            bool realtime=hello[3]=='4';
            unsigned compact=realtime?2:hello[3]-'1';
            struct sockaddr_in peer;socklen_t peer_size=sizeof(peer);
            uint16_t peer_port=getpeername(fd,(struct sockaddr*)&peer,&peer_size)==0?ntohs(peer.sin_port):0;
            portENTER_CRITICAL(&s_diagnostic_mux);
            uint32_t identity=s_diag.identity+1;
            memset(&s_diag,0,sizeof(s_diag));memset(&s_tcp_diag,0,sizeof(s_tcp_diag));
            s_diag.identity=identity;s_diag.active=true;s_diag.peer_port=peer_port;
            portEXIT_CRITICAL(&s_diagnostic_mux);
            uint8_t ok[4]={'O','K','A','Y'};
            if(!exact(fd,ok,4,true)) {
                portENTER_CRITICAL(&s_diagnostic_mux);s_diag.active=false;portEXIT_CRITICAL(&s_diagnostic_mux);
                rmlink_capture_stream_lost(generation); close(fd); continue;
            }
            uint32_t batches=0,max_wake_us=0,max_send_us=0;uint64_t bytes=0,raw_bytes=0,last_warning=0;
            int64_t stream_started_us=esp_timer_get_time();
#if CONFIG_RMLINK_STREAM_DIAGNOSTICS
            esp_timer_start_periodic(s_diagnostic_timer,100000);
#endif
            while(rmlink_network_is_enabled() && rmlink_capture_stream_live(generation)) {
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
                uint64_t encoding_at=esp_timer_get_time();size_t raw_size=used;
                if(compact && used) {used=compact==2?rmlink_stream_pack_delta(batch,used):rmlink_stream_pack(batch,used);if(!used)break;}
                uint64_t before_send=esp_timer_get_time();
                if(used) {
                    portENTER_CRITICAL(&s_diagnostic_mux);
                    s_diag.dequeued_at=encoding_at;s_diag.raw_bytes+=raw_size;s_diag.last_batch=used;
                    s_diag.last_encode_us=(uint32_t)(before_send-encoding_at);
                    if(s_diag.last_encode_us>s_diag.max_encode_us)s_diag.max_encode_us=s_diag.last_encode_us;
                    s_diag.max_wake_us=max_wake_us;s_diag.send_started=before_send;
                    portEXIT_CRITICAL(&s_diagnostic_mux);
                }
                bool sent=!used || (realtime?realtime_send(fd,generation,batch,used,stream_started_us):exact(fd,batch,used,true));
                uint32_t send_us=(uint32_t)(esp_timer_get_time()-before_send);
                if(used) {
                    portENTER_CRITICAL(&s_diagnostic_mux);
                    s_diag.send_started=0;s_diag.last_send_us=send_us;
                    if(send_us>s_diag.max_send_us)s_diag.max_send_us=send_us;
                    if(send_us>50000)s_diag.stalls++;
                    if(sent) {s_diag.sent_at=esp_timer_get_time();s_diag.batches++;}
                    portEXIT_CRITICAL(&s_diagnostic_mux);
                }
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
            portENTER_CRITICAL(&s_diagnostic_mux);s_diag.active=false;portEXIT_CRITICAL(&s_diagnostic_mux);
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
#include "rmlink_stream.h"
bool rmlink_stream_diagnostics(unsigned page,uint8_t out[60]) {(void)page;(void)out;return false;}
esp_err_t rmlink_stream_init(void) { return ESP_OK; }
#endif
