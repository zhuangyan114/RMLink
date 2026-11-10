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
static uint32_t demcr,preparation_resets,reset_status_reads;
static bool active_exception;
static uint32_t requested_clock=4000000,clock_changes;
static unsigned ram_failures,recovery_calls,buffer_writes;
static bool high_clock_fault,recovery_failure,low_clock_fault;
static uint8_t last_fault;
static uint32_t u32(const uint8_t *p) {return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void put(uint8_t *p,uint32_t v) {for(unsigned i=0;i<4;i++)p[i]=v>>(8*i);}
static bool read_target(uint32_t a,uint8_t *p,size_t n) {
    if(a>=0x08000000u && (uint64_t)a+n<=0x08100000u) {memcpy(p,flash+a-0x08000000u,n);return true;}
    if(a>=base && (uint64_t)a+n<=stack) {memcpy(p,ram+a-base,n);return true;}
    assert(n==4);uint32_t v=0;
    if(a==0xe000ed00u)v=cpuid;
    else if(a==id_address)v=device;
    else if(a==(size_address&~3u))v=(wrong_capacity?64:capacity)<<((size_address&3)*8);
    else if(a==0xe000edf0u) {
        v=dhcsr|0x10000u;
        if(reset_status_reads) {v|=0x02000000;reset_status_reads--;}
    }
    else if(a==0xe000edfcu)v=demcr;
    else if(a==0xe000edf8u)v=dcrdr;
    else if(a==0xe0002000u)v=base==0x24000000?0x10000063:0x63;
    else if(a==0x40023c14u || a==0x5200201cu || a==0x52002038u)v=0x12345678;
    else assert(false);
    put(p,v);return true;
}
static bool write_target(uint32_t a,const uint8_t *p,size_t n) {
    if(a>=base && (uint64_t)a+n<=stack) {
        if(a>=buffer && a<buffer+page) {
            buffer_writes++;
            if(programs>=1 && (ram_failures || (high_clock_fault && requested_clock>4000000) || low_clock_fault)) {
                if(ram_failures)ram_failures--;
                memcpy(ram+a-base,p,n/2);last_fault=4;return false;
            }
        }
        memcpy(ram+a-base,p,n);return true;
    }
    assert(n==4);uint32_t v=u32(p);
    if(a==0xe000edf8u)dcrdr=v;
    else if(a==0xe000edf4u) {if(v&0x10000)registers[v&31]=dcrdr;else dcrdr=registers[v&31];}
    else if(a==0xe000edf0u) {
        dhcsr=v&2?0x20000:0;
        if(!(v&2) && !reset_pending && registers[15]>=base && registers[15]<base+0x700) {
            if(active_exception || !(registers[20]&1))return true;
            uint32_t entry=(registers[15]&~1u)-base;
            assert(registers[13]==stack && registers[14]==base+0x501 && registers[9]==base);
            if(entry==0x200) {
                assert(registers[0]==last_sector);memset(flash+last_sector-0x08000000,255,sizeof(image));erases++;
            }else if(entry==0x300) {
                uint32_t at=registers[0]-0x08000000;
                assert(registers[1]==page && registers[2]==buffer && erases>=1);
                assert(at>=last_sector-0x08000000 && at+page<=capacity*1024);
                memcpy(flash+at,ram+buffer-base,page);programs++;
                if(unknown_write)return false;
            }else assert(entry==0 || entry==0x100);
            registers[0]=0;registers[15]=base+0x500;dhcsr=0x20000;
        }
        if(reset_pending) {reset_pending=false;registers[15]=0x08000001;}
    }else if(a==0xe000edfcu)demcr=v;
    else if(a==0xe000ed0cu) {
        if(demcr&1) {
            preparation_resets++;reset_status_reads=1;active_exception=false;
            memset(registers,0,sizeof(registers));registers[15]=0x08000001;dhcsr=0x20000;
        }else {resets++;reset_pending=true;}
    }
    else assert(a==0xe000ed30u || (a>=0xe0002008u && a<0xe0002088u));
    return true;
}
static bool hash(const uint8_t *p,size_t n,uint8_t out[32]) {assert(n==sizeof(image) && !memcmp(p,image,n));memset(out,0x42,32);return true;}
static uint64_t now(void) {return ticks+=10;}
static void *allocate(size_t n) {return malloc(n);}
static uint32_t swj_clock(uint32_t hz) {
    if(hz) {requested_clock=hz;clock_changes++;}
    return requested_clock;
}
static bool recover_swd(bool clear) {
    if(!clear)return last_fault==4;
    recovery_calls++;
    if(recovery_failure)return false;
    last_fault=0;
    return true;
}
static const rmlink_flash_io_t io={read_target,write_target,hash,now,allocate,free,swj_clock,recover_swd};
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
    demcr=0x01000010;preparation_resets=reset_status_reads=0;active_exception=true;
    ram_failures=recovery_calls=buffer_writes=0;
    high_clock_fault=recovery_failure=low_clock_fault=false;last_fault=0;
    memset(ram,0,sizeof(ram));memset(registers,0,sizeof(registers));memset(flash,0xa5,sizeof(flash));
    for(unsigned i=0;i<sizeof(image);i++)image[i]=(uint8_t)(i*37+i/17);
}
static void job(bool reset_after,unsigned stop) {
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
    if(stop==1) {request[1]=6;command(request,6);}
    if(stop==2) {
        rmlink_flash_closed(2,7);
        assert(!rmlink_flash_busy() && !rmlink_target_locked());
        return;
    }
    unsigned turns=0;
    while(rmlink_flash_run()) {
        assert(++turns<2000000);
        if(stop>=3 && requested_clock==4000000) {
            if(stop==4) {rmlink_flash_closed(2,7);return;}
            request[1]=6;command(request,6);stop=0;
        }
    }
    request[1]=5;command(request,6);
}
int main(void) {
    const uint32_t clocks[]={1000,2000000,4000000,8000000,10000000};
    for(unsigned id=2;id<=4;id++) {
      for(unsigned ci=0;ci<sizeof(clocks)/sizeof(clocks[0]);ci++) {
        requested_clock=clocks[ci];clock_changes=0;
        configure(id);job(true,0);
        assert(response[3]==RM_FLASH_COMPLETE && (response[5]&2) && !rmlink_target_locked());
        assert(erases==1 && programs==sizeof(image)/page && resets==1);
        assert(preparation_resets==1 && demcr==0x01000010);
        assert(requested_clock==clocks[ci] && clock_changes==0);
        assert(!memcmp(flash+last_sector-0x08000000,image,sizeof(image)));
        for(unsigned i=0;i<last_sector-0x08000000;i++)assert(flash[i]==0xa5);
        configure(id);wrong_capacity=true;job(true,0);
        assert(response[3]==RM_FLASH_FAILED && response[4]==RM_FLASH_TARGET && !erases && !programs && !resets && !preparation_resets);
        configure(id);job(false,0);
        assert(response[3]==RM_FLASH_COMPLETE && !resets && (dhcsr&0x20000));
        configure(id);unknown_write=true;job(true,0);
        assert(response[3]==RM_FLASH_FAILED && response[4]==RM_FLASH_SWD && programs==1 && !resets);
        assert(requested_clock==clocks[ci]);
        configure(id);job(false,1);
        assert(response[3]==RM_FLASH_CANCELLED && !erases && !programs && requested_clock==clocks[ci]);
        configure(id);job(false,2);
        assert(!erases && !programs && requested_clock==clocks[ci]);
      }
      requested_clock=8000000;clock_changes=0;
      configure(id);ram_failures=1;job(false,0);
      assert(response[3]==RM_FLASH_COMPLETE && erases==1 && recovery_calls==1 && clock_changes==0);
      assert(!memcmp(flash+last_sector-0x08000000,image,sizeof(image)));
      uint8_t metrics[]={0x82,10,123,0,0,0};command(metrics,sizeof(metrics));
      assert(response[3]==1 && u32(response+15)==1 && response[6]==0);
      configure(id);ram_failures=2;job(false,0);
      assert(response[3]==RM_FLASH_COMPLETE && erases==1 && recovery_calls==2);
      configure(id);high_clock_fault=true;job(false,0);
      assert(response[3]==RM_FLASH_COMPLETE && erases==2 && clock_changes==2 && requested_clock==8000000);
      assert(!memcmp(flash+last_sector-0x08000000,image,sizeof(image)));
      assert(programs==sizeof(image)/page+1);
      command(metrics,sizeof(metrics));
      assert(response[6]==1 && u32(response+15)==2 && u32(response+23)==last_sector);
      assert(u32(response+27)==sizeof(image) && u32(response+31)==2);
      configure(id);high_clock_fault=low_clock_fault=true;job(false,0);
      assert(response[3]==RM_FLASH_FAILED && erases==2 && requested_clock==8000000);
      configure(id);ram_failures=1;recovery_failure=true;job(false,0);
      assert(response[3]==RM_FLASH_FAILED && erases==1 && requested_clock==8000000);
      configure(id);high_clock_fault=true;job(false,3);
      assert(response[3]==RM_FLASH_CANCELLED && erases==1 && requested_clock==8000000);
      configure(id);high_clock_fault=true;job(false,4);
      assert(!rmlink_flash_busy() && !rmlink_target_locked() && requested_clock==8000000);
    }
    /* A newly registered profile inherits recovery solely from its sector/RAM
     * description, with no chip-ID branch in the production recovery path. */
    configure(3);rmlink_target_profile_t future=*rmlink_target_current();future.id=42;future.page=16;page=16;
    assert(rmlink_target_select(&future));high_clock_fault=true;job(false,0);
    assert(response[3]==RM_FLASH_COMPLETE && erases==2 && requested_clock==8000000);
    assert(!memcmp(flash+last_sector-0x08000000,image,sizeof(image)));
    puts("Shared target package: F407VE/F407IG/H723 and new profile inherit RAM retry, sector recovery, full verify and bounded failure handling");
    return 0;
}
