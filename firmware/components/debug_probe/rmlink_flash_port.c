/* SPDX-License-Identifier: Apache-2.0 */
#include <stdlib.h>
#include <string.h>
#include "rmlink_flash.h"
#include "DAP_config.h"
#include "DAP.h"
#include "mbedtls/sha256.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

static void put(uint8_t *p,uint32_t v) {for(unsigned i=0;i<4;i++)p[i]=v>>(i*8);}
static bool access(uint32_t a,uint8_t *data,size_t n,bool write) {
    if(!n || n>32 || (n&3) || (a&3) || (a&0x3ff)+n>0x400)return false;
    uint8_t request[64]={5,0,0,8},reply[40];unsigned count=n/4;
    put(request+4,0);request[8]=1;put(request+9,0x23000052);request[13]=5;put(request+14,a);
    size_t p=18;
    for(unsigned i=0;i<count;i++) {request[p++]=write?13:15;if(write) {memcpy(request+p,data+i*4,4);p+=4;}}
    request[2]=3+count;DAP_TransferDeadline(2000);
    size_t result=DAP_ProcessCommand(request,reply)&0xffff;
    if(result!=(write?3u:3u+n) || reply[0]!=5 || reply[1]!=request[2] || reply[2]!=1)return false;
    if(!write)memcpy(data,reply+3,n);
    return true;
}
static bool read_memory(uint32_t a,uint8_t *p,size_t n) {return access(a,p,n,false);}
static bool write_memory(uint32_t a,const uint8_t *p,size_t n) {return access(a,(uint8_t *)p,n,true);}
static bool digest(const uint8_t *p,size_t n,uint8_t out[32]) {return mbedtls_sha256(p,n,out,0)==0;}
static uint64_t now(void) {return esp_timer_get_time();}
static void *image_allocate(size_t n) {
    void *p=heap_caps_malloc(n,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(p) {ESP_LOGI("rmlink_flash","Image cache: %u bytes PSRAM",(unsigned)n);return p;}
    p=heap_caps_malloc(n,MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    if(!p) ESP_LOGW("rmlink_flash","Image cache allocation failed: free=%u largest=%u requested=%u",
        (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),
        (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT),(unsigned)n);
    else ESP_LOGI("rmlink_flash","Image cache: %u bytes internal RAM",(unsigned)n);
    return p;
}
void rmlink_flash_init_port(void) {
    const rmlink_flash_io_t port={read_memory,write_memory,digest,now,image_allocate,free};rmlink_flash_init(&port);
}
