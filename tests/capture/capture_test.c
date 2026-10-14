/* SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include "capture_platform.h"
#include "rmlink_capture.h"
uint64_t host_clock=1000000;
static uint32_t ram[5120], reads, writes;
static bool fail;
uint32_t DAP_ProcessCommand(const uint8_t *r,uint8_t *out) {
    assert(r[0]==5 && r[3]==8 && r[8]==1);
    host_clock+=20;
    out[0]=5; out[1]=fail?0:r[2]; out[2]=fail?4:1;
    if(fail) return 3;
    unsigned pos=3,n=3;uint32_t address=0;
    for(unsigned i=0;i<r[2];i++) {
        uint8_t flags=r[pos++];uint32_t value;
        if(flags&2) {
            assert(flags==15 && address>=0x20000000 && address<0x20005000 && !(address&3));
            memcpy(out+n,&ram[(address-0x20000000)/4],4);n+=4;reads++;address=(address&~0x3ffu)|((address+4)&0x3ff);
        }else {
            memcpy(&value,r+pos,4);pos+=4;
            if(flags==5)address=value;
            else if(flags==13){assert(address>=0x20000000 && address<0x20005000);ram[(address-0x20000000)/4]=value;writes++;address+=4;}
        }
    }
    return n;
}
static rmlink_owner_t owner={.transport=2,.session=7};
static uint8_t out[512];
static size_t command(uint8_t *r,size_t n) { return rmlink_capture_command(2,7,r,n,out,&owner); }
static void action(unsigned sub,unsigned sid) { uint8_t r[]={0x81,sub,sid}; command(r,3); assert(out[2]==0); }
static void prepare(unsigned sid,unsigned kind,unsigned count) {
    uint8_t r[17]={0x81,1,sid,kind,count}; uint32_t period=1000; memcpy(r+5,&period,4);
    command(r,sizeof(r)); assert(out[2]==0);
}
static void address(unsigned sid,unsigned index,uint32_t a) {
    uint8_t r[9]={0x81,2,sid,index,1}; memcpy(r+5,&a,4); command(r,9); assert(out[2]==0);
}
int main(void) {
    assert(rmlink_capture_init(NULL)==0);
    uint8_t sync[10]={0x81,13,1,2,3,4,5,6,7,8};
    unsigned sync_reads=reads;owner.session=8;
    assert(command(sync,sizeof(sync))==27 && out[2]==0);
    assert(memcmp(out+3,sync+2,8)==0 && reads==sync_reads);
    owner.session=7;
    prepare(1,1,2); address(1,0,0x20000000);
    uint8_t start[]={0x81,3,1}; command(start,3); assert(out[2]==1); assert(reads==0);
    address(1,1,0x20000004); ram[0]=0x13579bdf; ram[1]=0x2468ace0;
    action(3,1); assert(!rmlink_capture_raw_allowed()); assert(rmlink_capture_run());
    uint8_t frame[384]; assert(rmlink_capture_take(frame,sizeof(frame),0)==64);
    assert(memcmp(frame,"RML1",4)==0 && frame[5]==1 && frame[48]==3);
    uint32_t v; memcpy(&v,frame+56,4); assert(v==ram[0]);
    action(8,1); assert(rmlink_capture_raw_allowed()); host_clock+=2000;
    assert(!rmlink_capture_run()); action(9,1); assert(rmlink_capture_run());
    assert(rmlink_capture_take(frame,sizeof(frame),0)==64); assert(frame[16]==2); /* one missed slot */
    prepare(2,2,1); address(2,0,0x20000000); action(3,2);
    unsigned before=reads; assert(rmlink_capture_run()); assert(reads==before); /* Watch reused Timeline */
    assert(rmlink_capture_take(frame,sizeof(frame),0)==60);
    action(4,1); host_clock+=2000; assert(rmlink_capture_run()); assert(reads==before+1); /* Watch takeover */
    rmlink_capture_closed(2,7);
    prepare(1,1,1); address(1,0,0x20000000); ram[0]=0x20000100; ram[65]=0xdead1234;
    uint8_t ptr[10]={0x81,15,1,0,1,4,4,0,0,0}; command(ptr,sizeof(ptr)); assert(out[2]==0);
    action(3,1); assert(rmlink_capture_run()); assert(rmlink_capture_take(frame,sizeof(frame),0)==60);
    memcpy(&v,frame+56,4); assert(v==0xdead1234);
    fail=true; host_clock+=2000; assert(rmlink_capture_run()); assert(rmlink_capture_take(frame,sizeof(frame),0)==60);
    assert(frame[52]==1 && frame[48]==0); action(7,1); assert(out[3]==3); fail=false;
    rmlink_capture_closed(2,7);
    /* RTT wrap: six bytes at tail/head, commit read offset once. */
    prepare(1,1,2); address(1,0,0x20000000); address(1,1,0x20000004);
    ram[0]=0; ram[1]=0x12345678; ptr[6]=0; command(ptr,sizeof(ptr)); assert(out[2]==0);
    action(3,1); assert(rmlink_capture_run()); assert(rmlink_capture_take(frame,sizeof(frame),0)==64);
    assert(frame[48]==2 && frame[52]==1); action(7,1); assert(out[3]==2);
    rmlink_capture_closed(2,7);
    /* Two fields share pointer storage: one pointer read in this frame only. */
    prepare(1,1,2);address(1,0,0x20000000);address(1,1,0x20000000);
    ram[0]=0x20000100;ram[64]=11;ram[65]=22;
    ptr[3]=0;ptr[6]=0;command(ptr,sizeof(ptr));assert(out[2]==0);
    ptr[3]=1;ptr[6]=4;command(ptr,sizeof(ptr));assert(out[2]==0);
    action(3,1);before=reads;assert(rmlink_capture_run());assert(reads==before+3);
    assert(rmlink_capture_take(frame,sizeof(frame),0)==64);
    ram[0]=0x20000104;host_clock+=1000;before=reads;assert(rmlink_capture_run());assert(reads==before+3);
    assert(rmlink_capture_take(frame,sizeof(frame),0)==64);memcpy(&v,frame+56,4);assert(v==22);
    rmlink_capture_closed(2,7);
    memcpy(ram,"SEGGER RTT",10); ram[4]=1; ram[5]=0;
    ram[7]=0x20000200; ram[8]=16; ram[9]=4; ram[10]=14;
    uint8_t *buffer=(uint8_t*)&ram[128]; for(unsigned i=0;i<16;i++) buffer[i]=i;
    prepare(3,3,1); address(3,0,0x20000000); action(3,3); assert(rmlink_capture_run());
    assert(ram[10]==4 && writes==1); assert(rmlink_capture_take(frame,sizeof(frame),0)==78);
    uint8_t expected[]={14,15,0,1,2,3}; assert(memcmp(frame+72,expected,6)==0);
    rmlink_capture_closed(2,7);
    uint8_t ticket[]={0x81,11}; command(ticket,2); assert(out[2]==0);
    uint8_t nonce[16]; memcpy(nonce,out+3,16); uint32_t generation;
    assert(rmlink_capture_bind(nonce,&generation)); assert(!rmlink_capture_bind(nonce,&generation));
    assert(rmlink_capture_stream_live(generation)); rmlink_capture_closed(2,7);
    assert(!rmlink_capture_stream_live(generation));
    uint8_t bad[]={0x81,1,1,1,1,0,0,0,0}; command(bad,sizeof(bad)); assert(out[2]==1);
    owner.session=8; command(ticket,2); assert(out[2]==2);
    owner.session=7;rmlink_capture_closed(2,7);
    prepare(1,1,1);address(1,0,0x20000000);action(3,1);
    for(unsigned i=0;i<2000;i++) {host_clock+=980;ram[0]=i;assert(rmlink_capture_run());}
    action(5,1);unsigned retained=0;uint32_t last_value=0;
    while(rmlink_capture_take(frame,sizeof(frame),0)) {
        memcpy(&v,frame+56,4);if(!retained)assert(v>0);
        assert(!retained || v>last_value);last_value=v;retained++;
    }
    assert(retained && retained<2000 && last_value==1999);
    action(7,1);uint64_t drops;memcpy(&drops,out+30,8);assert(drops==2000-retained);
    rmlink_capture_closed(2,7);
    uint32_t seed=0x718293a4;uint8_t fuzz[512];
    for(unsigned iteration=0;iteration<20000;iteration++) {
        seed=seed*1664525u+1013904223u;unsigned length=seed%sizeof(fuzz);
        for(unsigned i=0;i<length;i++){seed=seed*1664525u+1013904223u;fuzz[i]=seed>>24;}
        if(length)fuzz[0]=0x81;
        size_t reply=command(fuzz,length);assert(reply>=3 && reply<=64);
    }
    rmlink_capture_closed(2,7);
    printf("Capture state machine: configuration, samples, barriers, Watch reuse/takeover, pointers, faults, RTT wrap, tickets, ownership, TIME_SYNC, realtime overflow passed\n");
    return 0;
}
