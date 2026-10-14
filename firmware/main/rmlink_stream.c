/* SPDX-License-Identifier: Apache-2.0 */
#include "sdkconfig.h"
#if CONFIG_RMLINK_WIFI_ENABLED && CONFIG_DEBUG_PROBE_IFACE_SWD
#include <errno.h>
#include <unistd.h>
#include <string.h>
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "rmlink_capture.h"
static bool exact(int fd, uint8_t *p, size_t n, bool sending) {
    while(n) { int part=sending?send(fd,p,n,0):recv(fd,p,n,0); if(part<=0) return false; p+=part; n-=part; }
    return true;
}
static void stream_task(void *unused) {
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
            int no_delay=1; setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&no_delay,sizeof(no_delay));
            uint8_t hello[20]; uint32_t generation;
            if(!exact(fd,hello,20,false) || memcmp(hello,"RMS1",4) || !rmlink_capture_bind(hello+4,&generation)) { close(fd); continue; }
            uint8_t ok[4]={'O','K','A','Y'};
            if(!exact(fd,ok,4,true)) { rmlink_capture_stream_lost(generation); close(fd); continue; }
            while(rmlink_capture_stream_live(generation)) {
                uint8_t batch[4096]; size_t used=0;
                do { size_t n=rmlink_capture_take(batch+used,sizeof(batch)-used,used?0:10); if(!n) break; used+=n; }
                while(used+384<=sizeof(batch));
                if(used && !exact(fd,batch,used,true)) break;
                fd_set read; FD_ZERO(&read); FD_SET(fd,&read); struct timeval poll={0};
                if(select(fd+1,&read,NULL,NULL,&poll)>0) break; /* EOF or invalid upstream bytes */
            }
            rmlink_capture_stream_lost(generation); shutdown(fd,SHUT_RDWR); close(fd);
        }
        close(listener);
    }
}
esp_err_t rmlink_stream_init(void) {
    return xTaskCreate(stream_task,"rmlink_stream",6144,NULL,3,NULL)==pdPASS?ESP_OK:ESP_ERR_NO_MEM;
}
#else
#include "esp_err.h"
esp_err_t rmlink_stream_init(void) { return ESP_OK; }
#endif
