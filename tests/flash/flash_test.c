/* Independent target/loader model for the actual buffered Flash C state machine. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rmlink_flash.h"
static uint8_t ram[20480],flash[65536],image[65536],out[520];
static uint32_t regs[32],dcrdr,dhcsr,clock_us,io_writes,loader_calls,erase_calls,mass_calls,reset_calls;
static bool mismatch,no_memory,io_failure,bad_return,bad_pc,not_ready,reset_pending,corrupt_verify,corrupt_options;
static bool write_protected;
static size_t allocated;
static uint32_t get(const uint8_t *p) {return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void put(uint8_t *p,uint32_t v) {for(unsigned i=0;i<4;i++)p[i]=v>>(8*i);}
static uint32_t crc(const uint8_t *p,size_t n) {
    uint32_t v=~0u;while(n--) {v^=*p++;for(unsigned i=0;i<8;i++)v=(v>>1)^(0xedb88320u&(0u-(v&1)));}return ~v;
}
static bool read_target(uint32_t a,uint8_t *p,size_t n) {
    if(io_failure)return false;
    if(a>=0x08000000 && a+n<=0x08010000) {memcpy(p,flash+a-0x08000000,n);return true;}
    if(a>=0x20000000 && a+n<=0x20005000) {memcpy(p,ram+a-0x20000000,n);return true;}
    assert(n==4);uint32_t v=0;
    if(a==0xe000ed00)v=0x411fc231;
    else if(a==0xe0042000)v=mismatch?0x411:0x410;
    else if(a==0x1ffff7e0)v=64;
    else if(a==0x40022020)v=write_protected?0xffff0000:0xffffffff;
    else if(a>=0x1ffff800 && a<0x1ffff810)v=corrupt_options && erase_calls?0xfffffffe:0xffffffff;
    else if(a==0xe000edf0)v=dhcsr|(not_ready?0:0x10000);
    else if(a==0xe000edf8)v=dcrdr;
    else if(a==0xe0002000)v=0x63;
    else assert(false);
    put(p,v);return true;
}
static bool write_target(uint32_t a,const uint8_t *p,size_t n) {
    io_writes++;if(io_failure)return false;
    if(a>=0x20000000 && a+n<=0x20005000) {memcpy(ram+a-0x20000000,p,n);return true;}
    assert(n==4);uint32_t v=get(p);
    if(a==0xe000edf8)dcrdr=v;
    else if(a==0xe000edf4) {if(v&0x10000)regs[v&31]=dcrdr;else dcrdr=regs[v&31];}
    else if(a==0xe000edf0) {
        dhcsr=v&2?0x20000:0;
        if(!(v&2) && !reset_pending && regs[15]>=0x20000000 && regs[15]<0x20000700 && (regs[15]&~1u)!=0x20000500) {
            loader_calls++;uint32_t entry=(regs[15]&~1u)-0x20000000;
            uint32_t offset=regs[0]-0x08000000;
            if(entry==0x600) {
                assert(!offset && regs[1]==65536 && regs[2]==0x524d4c4b && !write_protected);
                memset(flash,255,sizeof(flash));erase_calls++;mass_calls++;
            }
            else if(entry==0x200) {assert(offset<65536 && !(offset&1023));memset(flash+offset,255,1024);erase_calls++;}
            else if(entry==0x300 || entry==0x400) {
                assert(offset+regs[1]<=65536 && regs[1]==1024 && regs[2]==0x20003c00);
                if(entry==0x300)memcpy(flash+offset,ram+0x3c00,regs[1]);
                else assert(!memcmp(flash+offset,ram+0x3c00,regs[1]));
            }else {assert(entry==0 || entry==0x100);if(entry==0x100 && corrupt_verify)flash[3]^=1;}
            regs[0]=bad_return?3:0;regs[15]=bad_pc?0x08001000:0x20000500;dhcsr=0x20000;
        }
        if(reset_pending) {reset_pending=false;regs[15]=0x08000001;}
    }else if(a==0xe000ed0c) {reset_calls++;reset_pending=true;}
    else assert(a==0xe000ed30 || (a>=0xe0002008 && a<0xe0002088));
    return true;
}
static bool hash(const uint8_t *p,size_t n,uint8_t digest[32]) {
    assert(n<=65536 && !memcmp(p,image,n));memset(digest,0x42,32);return true;
}
static uint64_t now(void) {clock_us+=100;return clock_us;}
static void *allocate(size_t n) {if(no_memory)return NULL;allocated+=n;return malloc(n);}
static void release(void *p) {assert(p);allocated=0;free(p);}
static const rmlink_flash_io_t port={read_target,write_target,hash,now,allocate,release};
static const rmlink_owner_t owner={.transport=2,.session=7};
static uint8_t command(uint8_t sub,const uint8_t *payload,size_t n) {
    uint8_t request[512]={0x82,sub};memcpy(request+2,payload,n);memset(out,0xcc,sizeof(out));
    size_t size=rmlink_flash_command(2,7,request,n+2,out,RMLINK_PACKET_SIZE,&owner,true);
    assert(size<=RMLINK_PACKET_SIZE && out[0]==0x82 && out[1]==sub && out[RMLINK_PACKET_SIZE]==0xcc);return out[2];
}
static void reset(void) {
    rmlink_flash_init(&port);assert(!allocated);memset(ram,0,sizeof(ram));memset(flash,0xa5,sizeof(flash));memset(regs,0,sizeof(regs));
    io_writes=loader_calls=erase_calls=mass_calls=reset_calls=0;dhcsr=0;clock_us=1;
    mismatch=no_memory=io_failure=bad_return=bad_pc=not_ready=reset_pending=corrupt_verify=corrupt_options=false;
    write_protected=false;
    for(unsigned i=0;i<65536;i++)image[i]=(uint8_t)(i*37+i/256);
}
static uint32_t id=123;
static void id_bytes(uint8_t p[4]) {put(p,id);}
static void begin_at(uint32_t address,unsigned length,bool wrong_hash) {
    uint8_t p[44];put(p,id);put(p+4,address);put(p+8,length);memset(p+12,wrong_hash?0x43:0x42,32);
    assert(command(1,p,sizeof(p))==0);
}
static void begin(unsigned length,bool wrong_hash) {begin_at(0x08000000,length,wrong_hash);}
static void upload(unsigned length) {
    uint8_t p[510];unsigned limit=RMLINK_PACKET_SIZE-16;
    for(unsigned at=0;at<length;) {
        unsigned n=length-at;if(n>limit)n=limit;
        put(p,id);put(p+4,at);p[8]=n;p[9]=n>>8;put(p+10,crc(image+at,n));memcpy(p+14,image+at,n);
        assert(command(2,p,n+14)==0 && get(out+3)==at+n);
        if(!at) {assert(command(2,p,n+14)==0);p[14]^=1;assert(command(2,p,n+14)==RM_FLASH_CRC);p[14]^=1;}
        at+=n;
    }
}
static unsigned status(void) {uint8_t p[4];id_bytes(p);assert(!command(5,p,4));return out[3];}
static void start(void) {uint8_t p[4];id_bytes(p);assert(!command(3,p,4));assert(!command(4,p,4));assert(command(4,p,4)==RM_FLASH_STATE);}
static void run(void) {unsigned turns=0;while(rmlink_flash_run())assert(++turns<1000000);}
int main(void) {
    rmlink_flash_init(&port);reset();uint8_t p[4];id_bytes(p);
    begin(65536,false);upload(65536);assert(!io_writes && !loader_calls);start();run();
    assert(status()==RM_FLASH_COMPLETE && !memcmp(flash,image,65536) && erase_calls==1 && mass_calls==1 && reset_calls==1 && !allocated);
    assert((out[5]&3)==3 && get(out+26)==65536 && get(out+30)==65536);
    unsigned before=loader_calls;assert(command(4,p,4)==RM_FLASH_STATE);run();assert(loader_calls==before);
    reset();begin_at(0x08000400,2048,false);upload(2048);start();run();
    assert(status()==RM_FLASH_COMPLETE && !memcmp(flash+1024,image,2048) && erase_calls==2 && !mass_calls);
    for(unsigned i=0;i<sizeof(flash);i++)if(i<1024 || i>=3072)assert(flash[i]==0xa5);
    reset();begin(65536,false);upload(65536);write_protected=true;start();run();
    assert(status()==RM_FLASH_FAILED && out[4]==RM_FLASH_TARGET && !io_writes && !erase_calls && !(out[5]&1));
    reset();begin(65536,false);upload(65536);start();while(!mass_calls)assert(rmlink_flash_run());
    assert(!command(6,p,4));run();assert(status()==RM_FLASH_CANCELLED && (out[5]&5)==5 && !reset_calls && !allocated);
    reset();begin(1024,true);upload(1024);assert(command(3,p,4)==RM_FLASH_HASH && !io_writes && !allocated);
    reset();begin(1024,false);assert(command(3,p,4)==RM_FLASH_STATE && !io_writes);
    uint8_t request[6]={0x82,4};put(request+2,id);assert(rmlink_flash_command(2,8,request,6,out,RMLINK_PACKET_SIZE,&owner,true)==3 && out[2]==RM_FLASH_OWNER);
    upload(1024);assert(!command(3,p,4));assert(rmlink_flash_command(2,7,request,6,out,RMLINK_PACKET_SIZE,&owner,false)==3 && out[2]==RM_FLASH_BUSY);
    assert(!io_writes);assert(!command(6,p,4));assert(status()==RM_FLASH_CANCELLED && !allocated);
    reset();begin(1024,false);upload(1024);mismatch=true;start();run();assert(status()==RM_FLASH_FAILED && out[4]==RM_FLASH_TARGET && !io_writes && !erase_calls);
    reset();begin(1024,false);upload(1024);start();while(!erase_calls)assert(rmlink_flash_run());assert(!command(6,p,4));run();
    assert(status()==RM_FLASH_CANCELLED && erase_calls==1 && !reset_calls && (out[5]&5)==5);
    reset();begin(1024,false);upload(1024);bad_return=true;start();run();assert(status()==RM_FLASH_FAILED && !erase_calls && out[4]==RM_FLASH_ALGORITHM);
    reset();begin(1024,false);upload(1024);not_ready=true;start();run();assert(status()==RM_FLASH_FAILED && out[4]==RM_FLASH_TIMEOUT && !erase_calls);
    reset();begin(1024,false);upload(1024);bad_pc=true;start();run();assert(status()==RM_FLASH_FAILED && out[4]==RM_FLASH_ALGORITHM && !erase_calls);
    reset();begin(1024,false);upload(1024);corrupt_verify=true;start();run();assert(status()==RM_FLASH_FAILED && out[4]==RM_FLASH_MISMATCH && !reset_calls);
    reset();begin(1024,false);upload(1024);corrupt_options=true;start();run();assert(status()==RM_FLASH_FAILED && out[4]==RM_FLASH_OPTIONS && !reset_calls);
    reset();begin(1024,false);upload(1024);start();io_failure=true;run();assert(status()==RM_FLASH_FAILED && out[4]==RM_FLASH_SWD && !erase_calls);
    reset();begin(1024,false);upload(1024);start();while(!erase_calls)assert(rmlink_flash_run());rmlink_flash_closed(2,8);assert(rmlink_flash_busy());
    rmlink_flash_closed(2,7);assert(!rmlink_flash_busy() && !allocated && (dhcsr&0x20000));
    reset();no_memory=true;uint8_t b[44];put(b,id);put(b+4,0x08000000);put(b+8,65536);memset(b+12,0x42,32);assert(command(1,b,sizeof(b))==RM_FLASH_MEMORY && !io_writes);
    puts("Buffered Flash model: full image, no pre-commit IO, CRC/hash/owner/busy, cancellation, timeout and no replay passed");return 0;
}
