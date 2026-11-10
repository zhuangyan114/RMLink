/* SPDX-License-Identifier: Apache-2.0 */
#include <stdlib.h>
#include <string.h>
#include "rmlink_flash.h"
#include "rmlink_capture.h"
#ifdef RMLINK_FLASH_PORT_HOST_TEST
#include "flash_port_platform.h"
#else
#include "DAP_config.h"
#include "DAP.h"
#include "mbedtls/sha256.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#endif

static bool failure_recorded;
static uint8_t last_ack,last_abort;
static void put(uint8_t *p,uint32_t v) {for(unsigned i=0;i<4;i++)p[i]=v>>(i*8);}
static uint32_t transfer_budget(unsigned count) {
    uint32_t clock=DAP_GetSWJClock();
    uint32_t wire_us=clock?((uint64_t)(count+1)*64*1000000+clock-1)/clock:0;
    return 2000+wire_us;
}
static bool access(uint32_t a,uint8_t *data,size_t n,bool write) {
    if(!n || n>32 || (n&3) || (a&3) || (a&0x3ff)+n>0x400)return false;
    /* CPUID is the first access of each Flash preflight. Keep its first
     * failure intact when error cleanup subsequently encounters sticky FAULT. */
    if(!write && a==0xe000ed00)failure_recorded=false;
    uint8_t request[64]={5,0,0,8},reply[40];unsigned count=n/4;
    put(request+4,0);request[8]=1;put(request+9,0x23000052);request[13]=5;put(request+14,a);
    size_t p=18;
    for(unsigned i=0;i<count;i++) {request[p++]=write?13:15;if(write) {memcpy(request+p,data+i*4,4);p+=4;}}
    request[2]=3+count;
    int64_t started=esp_timer_get_time();
    DAP_TransferDeadline(transfer_budget(request[2]));
    size_t result=DAP_ProcessCommand(request,reply)&0xffff;
    last_ack=result>=3?reply[2]:0;last_abort=DAP_GetTransferAbortReason();
    if(result!=(write?3u:3u+n) || reply[0]!=5 || reply[1]!=request[2] || reply[2]!=1) {
        if(!failure_recorded) {
            rmlink_capture_record_io_failure(result,request[2],result>=3?reply[1]:0,
                result>=3?reply[2]:0,DAP_GetTransferAbortReason(),write,
                (uint32_t)(esp_timer_get_time()-started));
            failure_recorded=true;
        }
        return false;
    }
    if(!write)memcpy(data,reply+3,n);
    return true;
}
static bool read_memory(uint32_t a,uint8_t *p,size_t n) {return access(a,p,n,false);}
static bool write_memory(uint32_t a,const uint8_t *p,size_t n) {return access(a,(uint8_t *)p,n,true);}
static bool recover_swd(bool clear) {
    if(!clear)return last_ack==4 && !last_abort;
    if(last_abort)return false;
    if(last_ack==1)return true;
    if(last_ack!=4)return false;
    /* Only RAM staging calls this hook. Clear sticky DP flags, then every
     * retry builds a fresh SELECT/CSW/TAR request at its exact RAM address. */
    const uint8_t query[]={5,0,1,6},abort[]={5,0,1,0,0x1e,0,0,0};
    uint8_t reply[8];
    DAP_TransferDeadline(transfer_budget(1));
    size_t size=DAP_ProcessCommand(query,reply)&0xffff;
    if(size!=7 || reply[0]!=5 || reply[1]!=1 || reply[2]!=1)return false;
    DAP_TransferDeadline(transfer_budget(1));
    size=DAP_ProcessCommand(abort,reply)&0xffff;
    if(size!=3 || reply[0]!=5 || reply[1]!=1 || reply[2]!=1)return false;
    rmlink_capture_invalidate_ap();
    DAP_TransferDeadline(transfer_budget(1));
    size=DAP_ProcessCommand(query,reply)&0xffff;
    if(size!=7 || reply[0]!=5 || reply[1]!=1 || reply[2]!=1 || (reply[3]&0xb2))return false;
    last_ack=1;
    return true;
}
static bool digest(const uint8_t *p,size_t n,uint8_t out[32]) {return mbedtls_sha256(p,n,out,0)==0;}
static uint64_t now(void) {return esp_timer_get_time();}
static uint32_t swj_clock(uint32_t clock_hz) {
    if(clock_hz) {
        uint8_t request[5]={0x11},reply[2];
        put(request+1,clock_hz);
        (void)DAP_ProcessCommand(request,reply);
    }
    return DAP_GetSWJClock();
}
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
    const rmlink_flash_io_t port={read_memory,write_memory,digest,now,image_allocate,free,swj_clock,recover_swd};rmlink_flash_init(&port);
}
