/* Independent CMSIS-DAP frame oracle for the real Flash transport port. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "rmlink_flash.h"
#include "rmlink_capture.h"
#include "flash_port_platform.h"
static rmlink_flash_io_t port;
static uint32_t clock_hz=8000000,deadline_us,sticky;
static unsigned transfers,clears,invalidations,diagnostics;
static uint8_t ack=1,abort_reason;
static bool malformed;
static uint32_t get(const uint8_t *p) {
    return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
}
static void put(uint8_t *p,uint32_t value) {for(unsigned i=0;i<4;i++)p[i]=value>>(8*i);}
int64_t esp_timer_get_time(void) {return transfers*100;}
uint32_t DAP_GetSWJClock(void) {return clock_hz;}
uint8_t DAP_GetTransferAbortReason(void) {return abort_reason;}
void DAP_TransferDeadline(uint32_t us) {deadline_us=us;}
void rmlink_flash_init(const rmlink_flash_io_t *io) {port=*io;}
void rmlink_capture_invalidate_ap(void) {invalidations++;}
void rmlink_capture_record_io_failure(size_t size,uint8_t requested,uint8_t completed,
    uint8_t status,uint8_t aborted,bool write,uint32_t elapsed) {
    (void)size;(void)requested;(void)completed;(void)status;(void)aborted;(void)write;(void)elapsed;
    diagnostics++;
}
int mbedtls_sha256(const uint8_t *data,size_t n,uint8_t digest[32],int is224) {
    (void)data;(void)n;(void)digest;(void)is224;return 0;
}
uint32_t DAP_ProcessCommand(const uint8_t *r,uint8_t *out) {
    transfers++;
    if(r[0]==0x11) {clock_hz=get(r+1);out[0]=0x11;out[1]=0;return 2;}
    assert(r[0]==5 && r[1]==0);
    out[0]=5;out[1]=r[2];out[2]=1;
    if(r[2]==1 && r[3]==6) {put(out+3,sticky);return 7;}
    if(r[2]==1 && r[3]==0) {
        assert(get(r+4)==0x1e);clears++;sticky=0;return 3;
    }
    /* Every memory request must rebuild SELECT=0, CSW32 and the exact TAR. */
    assert(r[3]==8 && get(r+4)==0 && r[8]==1 && get(r+9)==0x23000052);
    assert(r[13]==5 && get(r+14)==0x24010000);
    if(malformed)return 2;
    if(ack!=1) {out[1]=r[2]-1;out[2]=ack;return 3;}
    unsigned count=r[2]-3;
    bool read=r[18]==15;
    for(unsigned i=0;i<count;i++) {
        assert(r[18+(read?1:5)*i]==(read?15:13));
        if(read)put(out+3+4*i,0x12345678);
    }
    return read?3+4*count:3;
}
int main(void) {
    rmlink_flash_init_port();uint8_t data[32]={0};
    assert(port.write(0x24010000,data,sizeof(data)) && transfers==1);
    assert(!port.recover_swd(false));
    ack=4;sticky=0x80;
    assert(!port.write(0x24010000,data,sizeof(data)) && transfers==2 && diagnostics==1);
    assert(port.recover_swd(false) && transfers==2);
    assert(port.recover_swd(true) && clears==1 && invalidations==1 && transfers==5);
    ack=1;assert(port.write(0x24010000,data,sizeof(data)) && transfers==6);
    assert(port.read(0x24010000,data,sizeof(data)) && get(data)==0x12345678);
    for(unsigned i=0;i<3;i++) {
        ack=(uint8_t[]){7,8,4}[i];abort_reason=i==2?1:0;
        unsigned before=transfers;
        assert(!port.write(0x24010000,data,sizeof(data)) && transfers==before+1);
        assert(!port.recover_swd(false) && !port.recover_swd(true) && transfers==before+1);
    }
    abort_reason=0;malformed=true;
    assert(!port.write(0x24010000,data,sizeof(data)) && !port.recover_swd(false));
    malformed=false;ack=1;port.swj_clock(1000);
    assert(port.read(0x24010000,data,sizeof(data)) && deadline_us>=2000+12*64000);
    puts("Flash port: fresh addressed frames, explicit FAULT clear, no automatic write replay, hard-error rejection and low-clock deadline passed");
    return 0;
}
