/* SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include "capture_platform.h"
#include "rmlink_capture.h"
uint64_t host_clock=1000000;
uint8_t host_abort_reason;
static unsigned deadline_failures;
static uint8_t injected_status=2,injected_abort=2;
static uint32_t ram[5120], reads, writes, commands;
static uint32_t dp_select,ap_csw,select_writes,csw_writes;
static uint32_t operation_us=20;
static bool fail;
static bool fail_after_write;
uint32_t DAP_ProcessCommand(const uint8_t *r,uint8_t *out) {
    assert(r[0]==5 && r[1]==0);
    commands++;
    host_clock+=operation_us;
    out[0]=5; out[1]=fail?0:r[2]; out[2]=fail?4:1;
    if(fail) {dp_select=0x10;ap_csw=0;return 3;}
    unsigned pos=3,n=3;uint32_t address=0;
    for(unsigned i=0;i<r[2];i++) {
        if(deadline_failures && i+1==r[2]) {
            deadline_failures--;host_abort_reason=injected_abort;host_clock+=2100;
            out[1]=i;out[2]=injected_status;dp_select=0x10;ap_csw=0;return n;
        }
        uint8_t flags=r[pos++];uint32_t value;
        if(flags&2) {
            assert(flags==15 && !(address&3) && dp_select==0 && ap_csw==0x23000052);
            uint32_t observed=0x30003;
            if(address!=0xe000edf0) {assert(address>=0x20000000 && address<0x20005000);observed=ram[(address-0x20000000)/4];}
            memcpy(out+n,&observed,4);n+=4;reads++;address=(address&~0x3ffu)|((address+4)&0x3ff);
        }else {
            memcpy(&value,r+pos,4);pos+=4;
            if(flags==8){dp_select=value;select_writes++;}
            else if(flags==1){assert(dp_select==0);ap_csw=value;csw_writes++;}
            else if(flags==5){assert(dp_select==0 && ap_csw==0x23000052);address=value;}
            else if(flags==13){assert(dp_select==0 && ap_csw==0x23000052 && address>=0x20000000 && address<0x20005000);ram[(address-0x20000000)/4]=value;writes++;address+=4;
                if(fail_after_write){dp_select=0x10;ap_csw=0;host_abort_reason=2;out[1]=0;out[2]=2;return 3;}}
            else assert(false);
        }
    }
    return n;
}
static rmlink_owner_t owner={.transport=2,.session=7};
static uint8_t out[512];
/* Mirror the fixed-core executor's conservative boundary for queued work. */
static size_t command(uint8_t *r,size_t n) { rmlink_capture_invalidate_ap();return rmlink_capture_command(2,7,r,n,out,&owner); }
static size_t usb_command(uint8_t *r,size_t n) {rmlink_capture_invalidate_ap();return rmlink_capture_command(1,7,r,n,out,&owner);}
static void action(unsigned sub,unsigned sid) { uint8_t r[]={0x81,sub,sid}; command(r,3); assert(out[2]==0); }
static void prepare_period(unsigned sid,unsigned kind,unsigned count,uint32_t period) {
    uint8_t r[17]={0x81,1,sid,kind,count}; memcpy(r+5,&period,4);
    command(r,sizeof(r)); assert(out[2]==0);
}
static void prepare(unsigned sid,unsigned kind,unsigned count) {prepare_period(sid,kind,count,1000);}
static void address(unsigned sid,unsigned index,uint32_t a) {
    uint8_t r[9]={0x81,2,sid,index,1}; memcpy(r+5,&a,4); command(r,9); assert(out[2]==0);
}
static void pointer_at(unsigned sid,unsigned field,unsigned depth,int32_t last_offset) {
    uint8_t r[22]={0x81,15,sid,field,depth,4};
    assert(depth && depth<=4);memcpy(r+6+4*(depth-1),&last_offset,4);
    command(r,6+4*depth);assert(out[2]==0);
}
static void pointer(unsigned field,unsigned depth,int32_t last_offset) {pointer_at(1,field,depth,last_offset);}
static size_t next_record(uint8_t *frame) {
    for(unsigned i=0;i<32;i++) {
        unsigned before=reads;if(!rmlink_capture_run())host_clock+=1000;assert(reads-before<=8);
        size_t n=rmlink_capture_take(frame,384,0);if(n)return n;
    }
    assert(false);return 0;
}
static void memap_cache_test(void) {
    prepare(1,1,10);
    for(unsigned i=0;i<10;i++){address(1,i,0x20000000+i*8);ram[i*2]=0x123400+i;}
    action(3,1);uint8_t frame[384];
    unsigned sel=select_writes,csw=csw_writes,cmd=commands;
    assert(next_record(frame)==96 && commands==cmd+2);
    assert(select_writes==sel+1 && csw_writes==csw+1);
    /* Consecutive frames reuse only AP configuration and reread every leaf. */
    ram[0]++;host_clock+=2000;sel=select_writes;csw=csw_writes;unsigned before=reads;
    assert(next_record(frame)==96 && reads==before+10);
    assert(select_writes==sel && csw_writes==csw);
    uint32_t value;memcpy(&value,frame+56,4);assert(value==ram[0]);
    /* A queued external access can change the bank and CSW arbitrarily. */
    sel=select_writes;csw=csw_writes;
    dp_select=0x10;ap_csw=0;rmlink_capture_invalidate_ap();host_clock+=2000;
    assert(next_record(frame)==96 && select_writes==sel+1 && csw_writes==csw+1);
    action(8,1);dp_select=0x20;ap_csw=0;action(9,1);host_clock+=2000;
    sel=select_writes;assert(next_record(frame)==96 && select_writes==sel+1);
    /* A possibly partial transfer invalidates configuration before recovery. */
    fail=true;host_clock+=2000;assert(next_record(frame)==96 && frame[52]&1);
    fail=false;uint8_t resume[]={0x81,6,1};
    assert(rmlink_capture_command(2,7,resume,sizeof(resume),out,&owner)==3 && out[2]==0);
    sel=select_writes;assert(next_record(frame)==96 && select_writes==sel+1 && frame[48]==255 && frame[49]==3);
    rmlink_capture_closed(2,7);dp_select=0x30;ap_csw=0;
    prepare(1,1,1);address(1,0,0x20000000);action(3,1);
    sel=select_writes;assert(next_record(frame)==60 && select_writes==sel+1);
    rmlink_capture_closed(2,7);
    /* A queued command between two chunks of one frame must fence the cache,
     * even when that command itself performs no target IO (heartbeat here). */
    prepare(1,1,10);for(unsigned i=0;i<10;i++)address(1,i,0x20000000+i*8);action(3,1);
    sel=select_writes;csw=csw_writes;
    assert(rmlink_capture_run() && !rmlink_capture_take(frame,sizeof(frame),0));
    dp_select=0x40;ap_csw=0;uint8_t heartbeat[]={0x81,12};
    assert(command(heartbeat,sizeof(heartbeat))==3 && out[2]==0);
    assert(next_record(frame)==96 && select_writes==sel+2 && csw_writes==csw+2);
    rmlink_capture_closed(2,7);
}
static void bounded_read_recovery_test(void) {
    uint8_t frame[384],stats[21];uint32_t v;
    rmlink_capture_read_recovery_stats(stats);uint32_t attempted;memcpy(&attempted,stats+1,4);
    prepare(1,1,8);
    for(unsigned i=0;i<8;i++){address(1,i,0x20000100+i*8);ram[64+i*2]=100+i;}
    action(3,1);deadline_failures=1;unsigned before=reads;
    assert(rmlink_capture_run() && reads-before==7);
    assert(!rmlink_capture_take(frame,sizeof(frame),0)); /* yield before retry */
    unsigned sel=select_writes;before=reads;
    assert(rmlink_capture_run() && reads-before==8 && select_writes==sel+1);
    assert(rmlink_capture_take(frame,sizeof(frame),0)==88 && frame[48]==255 && !(frame[52]&1));
    for(unsigned i=0;i<8;i++){memcpy(&v,frame+56+i*4,4);assert(v==100+i);}
    rmlink_capture_read_recovery_stats(stats);memcpy(&v,stats+1,4);assert(v==attempted+1);
    memcpy(&v,stats+5,4);assert(v==1);rmlink_capture_closed(2,7);
    prepare(1,1,8);for(unsigned i=0;i<8;i++)address(1,i,0x20000100+i*8);action(3,1);
    deadline_failures=2;assert(rmlink_capture_run());assert(rmlink_capture_run());
    assert(rmlink_capture_take(frame,sizeof(frame),0)==88 && (frame[52]&1));
    action(7,1);assert(out[3]==3 && out[50]==3);
    rmlink_capture_read_recovery_stats(stats);memcpy(&v,stats+9,4);assert(v==1);
    rmlink_capture_closed(2,7);
    for(unsigned i=0;i<2;i++) {
        /* NO_ACK and explicit Abort must not enter read-deadline recovery. */
        injected_status=i?2:7;injected_abort=i?1:0;
        prepare(1,1,1);address(1,0,0x20000100);action(3,1);deadline_failures=1;
        rmlink_capture_read_recovery_stats(stats);memcpy(&attempted,stats+1,4);
        assert(rmlink_capture_run() && rmlink_capture_take(frame,sizeof(frame),0)==60 && (frame[52]&1));
        action(7,1);assert(out[3]==3);
        rmlink_capture_read_recovery_stats(stats);memcpy(&v,stats+1,4);assert(v==attempted);
        rmlink_capture_closed(2,7);
    }
    injected_status=2;injected_abort=2;
}
static void independent_pointer_prefetch_test(void) {
    uint8_t frame[384];uint32_t v;
    prepare(1,1,10);
    for(unsigned i=0;i<10;i++) {
        ram[64+i*16]=100+i;
        if(i<5) {
            unsigned root=1024+i*16;
            ram[root]=0x20000000+(root+1)*4;ram[root+1]=0x20000000+(root+2)*4;
            ram[root+2]=0x20000100+i*64;address(1,i,0x20000000+root*4);pointer(i,3,0);
        } else address(1,i,0x20000100+i*64);
    }
    action(3,1);unsigned before=reads;
    assert(next_record(frame)==96 && reads-before==25 && frame[48]==255 && frame[49]==3);
    for(unsigned i=0;i<10;i++){memcpy(&v,frame+56+i*4,4);assert(v==100+i);}
    rmlink_capture_closed(2,7);
    /* Arbitrary nodes outside the speculative window still resolve normally. */
    prepare(1,1,5);
    for(unsigned i=0;i<5;i++) {
        unsigned root=1024+i*16,middle=1536+i*16,last=1792+i*16;
        ram[root]=0x20000000+middle*4;ram[middle]=0x20000000+last*4;ram[last]=0x20000100+i*64;
        address(1,i,0x20000000+root*4);pointer(i,3,0);
    }
    action(3,1);assert(next_record(frame)==76 && frame[48]==31 && !(frame[52]&1));
    for(unsigned i=0;i<5;i++){memcpy(&v,frame+56+i*4,4);assert(v==100+i);}
    rmlink_capture_closed(2,7);
    /* Cache capacity must still support 32 independent four-level chains. */
    prepare(1,1,32);
    for(unsigned i=0;i<32;i++) {
        unsigned root=1024+i*8;
        for(unsigned d=0;d<4;d++)ram[root+d]=0x20000000+(root+d+1)*4;
        ram[root+4]=1000+i;address(1,i,0x20000000+root*4);pointer(i,4,0);
    }
    action(3,1);before=reads;assert(next_record(frame)==184 && reads-before==160);
    memcpy(&v,frame+48,4);assert(v==UINT32_MAX);
    for(unsigned i=0;i<32;i++){memcpy(&v,frame+56+i*4,4);assert(v==1000+i);}
    rmlink_capture_closed(2,7);
}
static void snapshot_read_test(void) {
    prepare(1,1,1);address(1,0,0x20000000);action(3,1);
    uint8_t frame[384];next_record(frame);
    for(unsigned i=0;i<5120;i++)ram[i]=0xa5000000u+i;
    uint8_t request[]={0x81,20,0xff,3,0,0x20,12,0};
    unsigned before=reads,write_before=writes;
    owner.session=8;assert(command(request,sizeof(request))==3 && out[2]==2 && reads==before);
    owner.session=7;
    assert(command(request,sizeof(request))==15 && out[2]==0);
    assert(memcmp(out+3,(uint8_t*)ram+0x3ff,12)==0 && writes==write_before);
    assert(!rmlink_capture_raw_allowed()); /* Capture was never paused. */
    host_clock+=2000;assert(next_record(frame)==60);
    request[6]=0;assert(command(request,sizeof(request))==3 && out[2]==1);
    request[6]=129;assert(command(request,sizeof(request))==3 && out[2]==1);
    request[6]=4;request[5]=0xe0;
    assert(command(request,sizeof(request))==3 && out[2]==1); /* No peripheral reads. */
    request[5]=0x20;request[2]=0xff;request[3]=0x4f;request[6]=2;
    assert(command(request,sizeof(request))==3 && out[2]==1); /* End of RAM. */
    assert(command(request,7)==3 && out[2]==1);
    request[2]=0;request[3]=0;request[6]=128;
    if(RMLINK_PACKET_SIZE>=256)assert(command(request,8)==131 && out[2]==0);
    else assert(command(request,8)==3 && out[2]==1);
    rmlink_capture_closed(2,7);owner.transport=1;request[6]=61;
    assert(usb_command(request,8)==64 && out[2]==0);
    request[6]=62;assert(usb_command(request,8)==3 && out[2]==1);
    rmlink_capture_closed(1,7);owner.transport=2;
}

int main(void) {
    assert(rmlink_capture_init(NULL)==0);memap_cache_test();bounded_read_recovery_test();independent_pointer_prefetch_test();
    snapshot_read_test();
    memset(ram,0,sizeof(ram));reads=0;writes=0;commands=0;
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
    uint8_t state_query[]={0x81,17};unsigned state_reads=reads;
    owner.session=8;assert(command(state_query,2)==3 && out[2]==2 && reads==state_reads);
    owner.session=7;assert(command(state_query,2)==7 && out[2]==0);memcpy(&v,out+3,4);assert(v==0x30003);
    assert(!rmlink_capture_raw_allowed()); /* no pause or partial-frame flush */
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
    action(3,1); assert(next_record(frame)==60);
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
    action(3,1);before=reads;assert(next_record(frame)==64);assert(reads==before+3);
    ram[0]=0x20000104;host_clock+=1000;before=reads;
    assert(next_record(frame)==64 && reads==before+3);memcpy(&v,frame+56,4);assert(v==22);
    rmlink_capture_closed(2,7);
    /* Shared three-level chain: one read per distinct pointer/value in a frame,
     * at most five DAP packets for ten leaf fields, not thirteen round trips. */
    prepare(1,1,10);ram[0]=0x20000004;ram[1]=0x20000008;ram[2]=0x20001000;
    for(unsigned i=0;i<10;i++) {address(1,i,0x20000000);pointer(i,3,i*4);ram[1024+i]=i+100;}
    action(3,1);before=reads;unsigned before_commands=commands;
    assert(next_record(frame)==96 && reads==before+13 && commands-before_commands<=5);
    assert(frame[48]==255 && frame[49]==3 && frame[52]==0);
    for(unsigned i=0;i<10;i++){memcpy(&v,frame+56+i*4,4);assert(v==i+100);}
    /* A root that changes between frames must be fetched again. */
    ram[0]=0x2000000c;ram[3]=0x20000010;ram[4]=0x20001100;
    for(unsigned i=0;i<10;i++)ram[1088+i]=i+200;
    host_clock+=1000;before=reads;assert(next_record(frame)==96 && reads==before+13);
    memcpy(&v,frame+56,4);assert(v==200);
    /* Invalid shared root does not attempt target IO at address zero. */
    ram[0]=0;host_clock+=1000;before=reads;assert(next_record(frame)==96 && reads==before+1);
    assert(frame[48]==0 && frame[52]==1);action(7,1);assert(out[3]==2);
    rmlink_capture_closed(2,7);
    /* A control barrier between pointer levels ends the partial frame. On
     * release, the next frame must fetch a changed root rather than resume
     * the old dependency cache; no target reads may run behind the barrier. */
    prepare(1,1,10);ram[0]=0x20000004;ram[1]=0x20000008;ram[2]=0x20001000;
    for(unsigned i=0;i<10;i++){address(1,i,0x20000000);pointer(i,3,i*4);ram[1024+i]=i+100;}
    action(3,1);before=reads;assert(rmlink_capture_run() && reads==before+1);
    assert(!rmlink_capture_take(frame,sizeof(frame),0));action(8,1);
    assert(rmlink_capture_take(frame,sizeof(frame),0)==96 && frame[48]==0 && (frame[52]&1));
    before=reads;assert(!rmlink_capture_run() && reads==before);
    ram[0]=0x2000000c;ram[3]=0x20000010;ram[4]=0x20001100;
    for(unsigned i=0;i<10;i++)ram[1088+i]=i+300;
    host_clock+=1000;action(9,1);before=reads;
    assert(next_record(frame)==96 && reads==before+13 && frame[52]==0);
    for(unsigned i=0;i<10;i++){memcpy(&v,frame+56+i*4,4);assert(v==i+300);}
    rmlink_capture_closed(2,7);
    /* Full advertised capacity: 32 independent four-level chains need 160
     * distinct words, without cache exhaustion, duplicate reads or deadlock. */
    prepare(1,1,32);
    for(unsigned i=0;i<32;i++) {
        address(1,i,0x20000000+i*16);pointer(i,4,0);
        for(unsigned d=0;d<4;d++)ram[i*4+d]=d<3?0x20000000+i*16+(d+1)*4:0x20001000+i*4;
        ram[1024+i]=0x5000+i;
    }
    action(3,1);before=reads;before_commands=commands;
    assert(next_record(frame)==184 && reads==before+160 && commands-before_commands<=20);
    assert(frame[48]==255 && frame[49]==255 && frame[50]==255 && frame[51]==255 && frame[52]==0);
    for(unsigned i=0;i<32;i++){memcpy(&v,frame+56+i*4,4);assert(v==0x5000+i);}
    rmlink_capture_closed(2,7);
    /* Watch reuses an identical pointer expression without rereading its chain,
     * but a faster Watch takes over when the Timeline result is too old. */
    prepare_period(1,1,4,10000);ram[0]=0x20000004;ram[1]=0x20000008;ram[2]=0x20001000;
    for(unsigned i=0;i<4;i++){address(1,i,0x20000000);pointer(i,3,i*4);ram[1024+i]=i+100;}
    action(3,1);assert(next_record(frame)==72);uint64_t timeline_timestamp;memcpy(&timeline_timestamp,frame+36,8);
    prepare(2,2,4);
    for(unsigned i=0;i<4;i++){address(2,i,0x20000000);pointer_at(2,i,3,i*4);}
    action(3,2);before=reads;assert(next_record(frame)==72 && reads==before && (frame[52]&4));
    uint64_t watch_timestamp;memcpy(&watch_timestamp,frame+36,8);assert(watch_timestamp==timeline_timestamp);
    for(unsigned i=0;i<4;i++){memcpy(&v,frame+56+i*4,4);assert(v==i+100);}
    ram[0]=0x2000000c;ram[3]=0x20000010;ram[4]=0x20001100;
    for(unsigned i=0;i<4;i++)ram[1088+i]=i+200;
    host_clock+=1500;before=reads;assert(next_record(frame)==72 && reads==before+7 && !(frame[52]&4));
    for(unsigned i=0;i<4;i++){memcpy(&v,frame+56+i*4,4);assert(v==i+200);}
    rmlink_capture_closed(2,7);
    /* A control barrier invalidates even a fresh completed Timeline result.
     * A newly started Watch must observe writes before the next Timeline tick. */
    prepare_period(1,1,4,10000);
    for(unsigned i=0;i<4;i++){address(1,i,0x20000000);pointer(i,3,i*4);}
    action(3,1);assert(next_record(frame)==72);action(8,1);ram[1088]=300;action(9,1);
    prepare(2,2,1);address(2,0,0x20000000);pointer_at(2,0,3,0);action(3,2);
    before=reads;assert(next_record(frame)==60 && reads==before+4 && !(frame[52]&4));
    memcpy(&v,frame+56,4);assert(v==300);
    /* Matching only the root would incorrectly reuse a different leaf. */
    action(4,2);action(4,1);prepare_period(1,1,1,10000);address(1,0,0x20000000);pointer(0,3,0);action(3,1);
    assert(next_record(frame)==60);
    prepare(2,2,1);address(2,0,0x20000000);pointer_at(2,0,3,16);ram[1092]=444;action(3,2);
    before=reads;assert(next_record(frame)==60 && reads==before+4 && !(frame[52]&4));memcpy(&v,frame+56,4);assert(v==444);
    /* A byte load is not the same expression as a uint32 load at that root. */
    action(4,2);prepare(2,2,1);address(2,0,0x20000000);
    uint8_t byte_pointer[18]={0x81,15,2,0,3,1};command(byte_pointer,sizeof(byte_pointer));assert(out[2]==0);action(3,2);
    before=reads;assert(next_record(frame)==60 && reads==before+4 && !(frame[52]&4));memcpy(&v,frame+56,4);assert(v==(300&255));
    /* Paused Timeline cannot keep Watch pinned to its old pointer result. */
    action(5,1);action(4,2);prepare(2,2,1);address(2,0,0x20000000);pointer_at(2,0,3,0);ram[1088]=555;action(3,2);
    before=reads;assert(next_record(frame)==60 && reads==before+4 && !(frame[52]&4));memcpy(&v,frame+56,4);assert(v==555);
    rmlink_capture_closed(2,7);
    /* Normal STOP before the RdOff write leaves target data unconsumed. A
     * replacement must be allowed to read it once, without getting BUSY from
     * the discarded, never-committed local payload. */
    memcpy(ram,"SEGGER RTT",10);ram[4]=1;ram[5]=0;
    ram[7]=0x20000200;ram[8]=64;ram[9]=16;ram[10]=0;
    for(unsigned i=0;i<16;i++)((uint8_t*)&ram[128])[i]=i;
    prepare(3,3,1);address(3,0,0x20000000);action(3,3);
    unsigned stop_writes=writes;
    for(unsigned i=0;i<3;i++)assert(rmlink_capture_run());
    assert(writes==stop_writes && ram[10]==0 && !rmlink_capture_take(frame,sizeof(frame),0));
    action(4,3);prepare(3,3,1);address(3,0,0x20000000);action(3,3);
    assert(next_record(frame)==88 && writes==stop_writes+1 && ram[10]==16);
    for(unsigned i=0;i<16;i++)assert(frame[72+i]==i);
    rmlink_capture_closed(2,7);
    memcpy(ram,"SEGGER RTT",10); ram[4]=1; ram[5]=0;
    ram[7]=0x20000200; ram[8]=16; ram[9]=4; ram[10]=14;
    uint8_t *buffer=(uint8_t*)&ram[128]; for(unsigned i=0;i<16;i++) buffer[i]=i;
    prepare(3,3,1); address(3,0,0x20000000); action(3,3);
    unsigned wrap_writes=writes;
    assert(next_record(frame)==78);assert(ram[10]==4 && writes==wrap_writes+1);
    uint8_t expected[]={14,15,0,1,2,3}; assert(memcmp(frame+72,expected,6)==0);
    rmlink_capture_closed(2,7);
    /* Timeline and a control barrier preempt a partially read RTT poll. The
     * uncommitted poll restarts from its header and does not consume twice. */
    ram[10]=14;prepare(3,3,1);address(3,0,0x20000000);action(3,3);
    unsigned preempt_writes=writes;assert(rmlink_capture_run());
    assert(writes==preempt_writes && !rmlink_capture_take(frame,sizeof(frame),0));
    prepare(1,1,1);address(1,0,0x20000000);action(3,1);assert(rmlink_capture_run());
    assert(rmlink_capture_take(frame,sizeof(frame),0)==60 && frame[5]==1 && writes==preempt_writes);
    action(4,1);action(8,3);assert(!rmlink_capture_run());action(9,3);
    assert(next_record(frame)==78 && frame[5]==3 && writes==preempt_writes+1);
    assert(memcmp(frame+72,expected,6)==0);rmlink_capture_closed(2,7);
    /* A lost RdOff response can follow a completed physical write. Resume
     * re-reads the descriptor and publishes once, with no second write. */
    ram[10]=14;prepare(3,3,1);address(3,0,0x20000000);action(3,3);
    unsigned before_writes=writes;fail_after_write=true;
    for(unsigned i=0;i<16;i++) {
        if(!rmlink_capture_run())break;
        action(7,3);if(out[3]==3)break;
    }
    action(7,3);assert(out[3]==3 && out[50]==3 && ram[10]==4 && writes==before_writes+1);
    assert(!rmlink_capture_take(frame,sizeof(frame),0));
    /* STOP must not discard a write whose response was lost. Keep PREPARE
     * denied until descriptor reconciliation publishes the retained payload. */
    action(4,3);uint8_t unknown_prepare[17]={0x81,1,3,3,1};uint32_t unknown_period=1000;
    memcpy(unknown_prepare+5,&unknown_period,4);command(unknown_prepare,sizeof(unknown_prepare));assert(out[2]==3);
    fail_after_write=false;action(6,3);
    assert(next_record(frame)==78 && writes==before_writes+1);
    assert(memcmp(frame+72,expected,6)==0);rmlink_capture_closed(2,7);
    uint8_t ticket[]={0x81,11}; command(ticket,2); assert(out[2]==0);
    uint8_t nonce[16]; memcpy(nonce,out+3,16); uint32_t generation;
    assert(rmlink_capture_bind(nonce,&generation)); assert(!rmlink_capture_bind(nonce,&generation));
    assert(rmlink_capture_stream_live(generation)); rmlink_capture_closed(2,7);
    assert(!rmlink_capture_stream_live(generation));
    uint8_t bad[]={0x81,1,1,1,1,0,0,0,0}; command(bad,sizeof(bad)); assert(out[2]==1);
    owner.session=8; command(ticket,2); assert(out[2]==2);
    owner.session=7;rmlink_capture_closed(2,7);
    prepare(1,1,1);address(1,0,0x20000000);action(3,1);
    for(unsigned i=0;i<10000;i++) {host_clock+=980;ram[0]=i;assert(rmlink_capture_run());}
    action(5,1);unsigned retained=0;uint32_t last_value=0;
    while(rmlink_capture_take(frame,sizeof(frame),0)) {
        memcpy(&v,frame+56,4);if(!retained)assert(v>0);
        assert(!retained || v>last_value);last_value=v;retained++;
    }
    assert(retained && retained<10000 && last_value==9999);
    action(7,1);uint64_t drops;memcpy(&drops,out+30,8);assert(drops==10000-retained);
    rmlink_capture_closed(2,7);
    owner.transport=1;
    uint8_t usb_prepare[17]={0x81,1,1,1,1};uint32_t usb_period=1000;memcpy(usb_prepare+5,&usb_period,4);
    assert(usb_command(usb_prepare,sizeof(usb_prepare))==3 && out[2]==0);
    uint8_t usb_address[]={0x81,2,1,0,1,0,0,0,0x20};assert(usb_command(usb_address,sizeof(usb_address))==3 && out[2]==0);
    uint8_t usb_start[]={0x81,3,1};assert(usb_command(usb_start,sizeof(usb_start))==3 && out[2]==0);
    assert(rmlink_capture_run());uint8_t usb_pull[]={0x81,16,1,0,0,0,0,0};
    uint8_t usb_copy[60];assert(usb_command(usb_pull,sizeof(usb_pull))==64 && out[11]==52);
    memcpy(usb_copy,out+12,52);uint8_t first_copy[64];memcpy(first_copy,out,64);
    assert(usb_command(usb_pull,sizeof(usb_pull))==64 && memcmp(out,first_copy,64)==0);
    usb_pull[6]=52;assert(usb_command(usb_pull,sizeof(usb_pull))==20 && out[11]==8);memcpy(usb_copy+52,out+12,8);
    assert(memcmp(usb_copy,"RML1",4)==0);
    uint8_t usb_ticket[]={0x81,11};usb_command(usb_ticket,2);assert(out[2]==3);
    usb_pull[2]=2;usb_pull[6]=0;assert(usb_command(usb_pull,sizeof(usb_pull))==12 && out[11]==0);
    usb_pull[2]=3;assert(usb_command(usb_pull,sizeof(usb_pull))==3 && out[2]==1);
    rmlink_capture_closed(1,7);owner.transport=2;
    uint8_t realtime[18]={0x81,1,1,1,1};uint32_t realtime_period=1000;memcpy(realtime+5,&realtime_period,4);
    command(realtime,sizeof(realtime));assert(out[2]==0);address(1,0,0x20000000);action(3,1);
    operation_us=1200;assert(rmlink_capture_run());action(7,1);
    assert(out[3]==2 && out[46]==1 && out[50]==0); // report an overrun, keep realtime capture alive
    assert(rmlink_capture_take(frame,sizeof(frame),0)==60 && (frame[52]&2));
    assert(rmlink_capture_run()); /* no artificial wait after an overrun */
    assert(rmlink_capture_take(frame,sizeof(frame),0)==60);memcpy(&v,frame+32,4);assert(v==1);
    host_clock+=800;assert(rmlink_capture_run());
    assert(rmlink_capture_take(frame,sizeof(frame),0)==60);memcpy(&v,frame+32,4);assert(v==3); // skipped deadline is visible
    rmlink_capture_closed(2,7);
    prepare(1,1,1);address(1,0,0x20000000);action(3,1);assert(rmlink_capture_run());action(7,1);
    assert(out[3]==3 && out[50]==6); // old 17-byte PREPARE keeps strict deadline semantics
    operation_us=20;rmlink_capture_closed(2,7);
    uint32_t seed=0x718293a4;uint8_t fuzz[512];
    for(unsigned iteration=0;iteration<20000;iteration++) {
        seed=seed*1664525u+1013904223u;unsigned length=seed%sizeof(fuzz);
        for(unsigned i=0;i<length;i++){seed=seed*1664525u+1013904223u;fuzz[i]=seed>>24;}
        if(length)fuzz[0]=0x81;
        size_t reply=command(fuzz,length);assert(reply>=3 && reply<=64);
    }
    rmlink_capture_closed(2,7);
    printf("Capture state machine: configuration, samples, barriers, Watch reuse/takeover, pointers, faults, RTT wrap, tickets, ownership, TIME_SYNC, realtime overflow, USB_PULL passed\n");
    return 0;
}
