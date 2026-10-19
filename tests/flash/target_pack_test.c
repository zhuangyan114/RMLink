/* Independent large-sector oracle for the actual firmware Flash state machine.
 * It models target RAM/Flash/control registers; it does not execute ARM code. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rmlink_flash.h"
#include "rmlink_target.h"
static uint8_t ram[320*1024],flash[1024*1024],image[128*1024],response[512];
static uint32_t registers[32],dcrdr,dhcsr,ticks,erases,programs,resets;
static uint32_t base,buffer,stack,device,capacity,cpuid,id_address,size_address,page,last_sector;
static bool wrong_capacity,reset_pending,unknown_write;
static uint32_t u32(const uint8_t *p) {return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void put(uint8_t *p,uint32_t v) {for(unsigned i=0;i<4;i++)p[i]=v>>(8*i);}
static bool read_target(uint32_t a,uint8_t *p,size_t n) {
    if(a>=0x08000000u && (uint64_t)a+n<=0x08100000u) {memcpy(p,flash+a-0x08000000u,n);return true;}
    if(a>=base && (uint64_t)a+n<=stack) {memcpy(p,ram+a-base,n);return true;}
    assert(n==4);uint32_t v=0;
    if(a==0xe000ed00u)v=cpuid;
    else if(a==id_address)v=device;
    else if(a==(size_address&~3u))v=(wrong_capacity?64:capacity)<<((size_address&3)*8);
    else if(a==0xe000edf0u)v=dhcsr|0x10000u;
    else if(a==0xe000edf8u)v=dcrdr;
    else if(a==0xe0002000u)v=base==0x24000000?0x10000063:0x63;
    else if(a==0x40023c14u || a==0x5200201cu || a==0x52002038u)v=0x12345678;
    else assert(false);
    put(p,v);return true;
}
static bool write_target(uint32_t a,const uint8_t *p,size_t n) {
    if(a>=base && (uint64_t)a+n<=stack) {memcpy(ram+a-base,p,n);return true;}
    assert(n==4);uint32_t v=u32(p);
    if(a==0xe000edf8u)dcrdr=v;
    else if(a==0xe000edf4u) {if(v&0x10000)registers[v&31]=dcrdr;else dcrdr=registers[v&31];}
    else if(a==0xe000edf0u) {
        dhcsr=v&2?0x20000:0;
        if(!(v&2) && !reset_pending && registers[15]>=base && registers[15]<base+0x700) {
            uint32_t entry=(registers[15]&~1u)-base;
            assert(registers[13]==stack && registers[14]==base+0x501 && registers[9]==base);
            if(entry==0x200) {
                assert(registers[0]==last_sector);memset(flash+last_sector-0x08000000,255,sizeof(image));erases++;
            }else if(entry==0x300) {
                uint32_t at=registers[0]-0x08000000;
                assert(registers[1]==page && registers[2]==buffer && erases==1);
                assert(at>=last_sector-0x08000000 && at+page<=capacity*1024);
                memcpy(flash+at,ram+buffer-base,page);programs++;
                if(unknown_write)return false;
            }else assert(entry==0 || entry==0x100);
            registers[0]=0;registers[15]=base+0x500;dhcsr=0x20000;
        }
        if(reset_pending) {reset_pending=false;registers[15]=0x08000001;}
    }else if(a==0xe000ed0cu) {resets++;reset_pending=true;}
    else assert(a==0xe000ed30u || (a>=0xe0002008u && a<0xe0002088u));
    return true;
}
static bool hash(const uint8_t *p,size_t n,uint8_t out[32]) {assert(n==sizeof(image) && !memcmp(p,image,n));memset(out,0x42,32);return true;}
static uint64_t now(void) {return ticks+=10;}
static void *allocate(size_t n) {return malloc(n);}
static const rmlink_flash_io_t io={read_target,write_target,hash,now,allocate,free};
static const rmlink_owner_t owner={.transport=2,.session=7};
static uint32_t crc(const uint8_t *p,size_t n) {uint32_t c=~0u;while(n--){c^=*p++;for(unsigned i=0;i<8;i++)c=(c>>1)^(0xedb88320u&(0u-(c&1)));}return ~c;}
static void command(uint8_t *p,size_t n) {
    assert(rmlink_flash_command(2,7,p,n,response,RMLINK_PACKET_SIZE,&owner,true)<=RMLINK_PACKET_SIZE);
    assert(response[0]==0x82 && response[1]==p[1] && response[2]==0);
}
static void configure(unsigned id) {
    rmlink_flash_init(&io);rmlink_target_reset();assert(rmlink_target_select(rmlink_target_find(id)));
    base=id==4?0x24000000:0x20000000;buffer=base+0x10000;stack=base+(id==4?320:128)*1024;
    device=id==4?0x483:0x413;capacity=id==2?512:1024;
    cpuid=id==4?0x411fc271:0x410fc241;id_address=id==4?0x5c001000:0xe0042000;
    size_address=id==4?0x1ff1e880:0x1fff7a22;page=id==4?65536:16384;
    last_sector=id==2?0x08060000:0x080e0000;
    ticks=1;erases=programs=resets=0;wrong_capacity=reset_pending=unknown_write=false;dhcsr=0;
    memset(ram,0,sizeof(ram));memset(registers,0,sizeof(registers));memset(flash,0xa5,sizeof(flash));
    for(unsigned i=0;i<sizeof(image);i++)image[i]=(uint8_t)(i*37+i/17);
}
static void job(bool reset_after) {
    uint8_t request[512]={0x82,1};put(request+2,123);put(request+6,last_sector);put(request+10,sizeof(image));memset(request+14,0x42,32);
    request[46]=0xa5;request[47]=reset_after;command(request,48);
    assert(rmlink_target_locked() && !rmlink_target_select(rmlink_target_find(1)));
    for(uint32_t offset=0;offset<sizeof(image);) {
        unsigned n=sizeof(image)-offset;if(n>RMLINK_PACKET_SIZE-16)n=RMLINK_PACKET_SIZE-16;
        request[1]=2;put(request+6,offset);request[10]=n;request[11]=n>>8;
        put(request+12,crc(image+offset,n));memcpy(request+16,image+offset,n);command(request,n+16);offset+=n;
    }
    request[1]=3;command(request,6);assert(!erases && !programs);
    request[1]=4;command(request,6);
    unsigned turns=0;while(rmlink_flash_run())assert(++turns<2000000);
    request[1]=5;command(request,6);
}
int main(void) {
    for(unsigned id=2;id<=4;id++) {
        configure(id);job(true);
        assert(response[3]==RM_FLASH_COMPLETE && (response[5]&2) && !rmlink_target_locked());
        assert(erases==1 && programs==sizeof(image)/page && resets==1);
        assert(!memcmp(flash+last_sector-0x08000000,image,sizeof(image)));
        for(unsigned i=0;i<last_sector-0x08000000;i++)assert(flash[i]==0xa5);
        configure(id);wrong_capacity=true;job(true);
        assert(response[3]==RM_FLASH_FAILED && response[4]==RM_FLASH_TARGET && !erases && !programs && !resets);
        configure(id);job(false);
        assert(response[3]==RM_FLASH_COMPLETE && !resets && (dhcsr&0x20000));
        configure(id);unknown_write=true;job(true);
        assert(response[3]==RM_FLASH_FAILED && response[4]==RM_FLASH_SWD && programs==1 && !resets);
    }
    puts("Shared target package: F407VE/F407IG/H723 last 128KiB sector, RAM/stack, FPB v2, full verify, identity refusal, sparse halt and no write replay passed");
    return 0;
}
