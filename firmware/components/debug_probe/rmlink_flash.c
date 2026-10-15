/* SPDX-License-Identifier: Apache-2.0
 * F103C8 buffered programming state machine. No socket, GPIO or second owner.
 */
#include <string.h>
#include "rmlink_flash.h"
#include "rmlink_f103_algorithm.inc"

#define BASE 0x20000000u
#define BUFFER 0x20003c00u
#define STACK 0x20005000u
#define PAGE 1024u
#define DHCSR 0xe000edf0u
#define DCRSR 0xe000edf4u
#define DCRDR 0xe000edf8u

static rmlink_flash_io_t io;
static struct {
    uint8_t state,error,flags,transport,step,call_step,reg_index,reg_count;
    uint32_t session,id,address,length,received,erased,programmed,verified,error_address;
    uint32_t offset,code_offset,buffer_offset,options[4],fp_count,call_entry,call_args[3];
    uint32_t reg_ids[8],reg_values[8],call_pc;
    uint8_t digest[32],*image;
    bool cancel,call_done,touched;
    uint64_t phase_start,start,end,call_deadline,reset_at,timings[7];
} s;
static uint32_t u32(const uint8_t *p) {return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void w32(uint8_t *p,uint32_t v) {for(unsigned i=0;i<4;i++)p[i]=v>>(i*8);}
static void w64(uint8_t *p,uint64_t v) {for(unsigned i=0;i<8;i++)p[i]=v>>(i*8);}
static uint32_t crc32(const uint8_t *p,size_t n) {
    uint32_t v=~0u;while(n--) {v^=*p++;for(unsigned j=0;j<8;j++)v=(v>>1)^(0xedb88320u&(0u-(v&1)));}return ~v;
}
static bool whole_image(void) {return s.address==0x08000000u && s.length==65536;}
static int timing_index(unsigned state) {
    if(state==RM_FLASH_UPLOAD)return 0;
    return state>=RM_FLASH_PREFLIGHT && state<=RM_FLASH_VERIFY?(int)state-2:-1;
}
static void phase(uint8_t state) {
    uint64_t now=io.now();int index=timing_index(s.state);
    if(index>=0)s.timings[index]+=now-s.phase_start;
    s.state=state;s.phase_start=now;s.step=0;
}
bool rmlink_flash_busy(void) {return s.state>=RM_FLASH_PREFLIGHT && s.state<=RM_FLASH_RESET;}
static bool rd(uint32_t a,uint32_t *v) {
    uint8_t b[4];s.error_address=a;if(!io.read(a,b,4))return false;*v=u32(b);return true;
}
static bool wr(uint32_t a,uint32_t v) {uint8_t b[4];w32(b,v);s.error_address=a;return io.write(a,b,4);}
static void finish(uint8_t state,uint8_t error) {
    uint32_t fault_address=s.error_address;
    if(state!=RM_FLASH_COMPLETE && s.touched) {
        uint32_t dhcsr;
        if(wr(DHCSR,0xa05f0003) && rd(DHCSR,&dhcsr) && (dhcsr&0x20000))s.flags|=4;
    }
    phase(state);s.error=error;s.error_address=error?fault_address:0;s.end=io.now();
    if(s.image) {io.release(s.image);s.image=NULL;}
}
void rmlink_flash_init(const rmlink_flash_io_t *port) {
    if(s.image && io.release)io.release(s.image);
    memset(&s,0,sizeof(s));io=*port;
}
void rmlink_flash_closed(uint8_t transport,uint32_t session) {
    if(s.transport!=transport || s.session!=session)return;
    if(rmlink_flash_busy())finish(RM_FLASH_CANCELLED,RM_FLASH_CANCEL);
    if(s.image)io.release(s.image);
    memset(&s,0,sizeof(s));
}
static void call_begin(uint32_t entry,uint32_t a,uint32_t b,uint32_t c,unsigned argc) {
    s.call_entry=entry;s.call_done=false;s.call_step=1;s.reg_index=0;s.reg_count=argc;
    s.call_args[0]=a;s.call_args[1]=b;s.call_args[2]=c;
    for(unsigned i=0;i<argc;i++) {s.reg_ids[i]=i;s.reg_values[i]=s.call_args[i];}
    const uint32_t ids[]={9,13,14,15,16};
    const uint32_t values[]={BASE,STACK,BASE+0x501,BASE+entry+1,0x01000000};
    for(unsigned i=0;i<5;i++) {s.reg_ids[s.reg_count]=ids[i];s.reg_values[s.reg_count++]=values[i];}
    s.call_deadline=io.now()+3000000;
}
static void call_run(void) {
    uint32_t v;
    if(io.now()>=s.call_deadline) {finish(RM_FLASH_FAILED,RM_FLASH_TIMEOUT);return;}
    switch(s.call_step) {
    case 1:if(!wr(DHCSR,0xa05f0003))goto swd;s.call_step=2;break;
    case 2:if(!rd(DHCSR,&v))goto swd;if(v&0x20000)s.call_step=3;break;
    case 3:if(!wr(DCRDR,s.reg_values[s.reg_index]))goto swd;s.call_step=4;break;
    case 4:if(!wr(DCRSR,s.reg_ids[s.reg_index]|0x10000))goto swd;s.call_step=5;break;
    case 5:
        if(!rd(DHCSR,&v))goto swd;
        if(v&0x10000) {s.reg_index++;s.call_step=s.reg_index<s.reg_count?3:6;}break;
    case 6:if(!wr(0xe000ed30,0x1f))goto swd;s.call_step=7;break;
    case 7:if(!wr(DHCSR,0xa05f0001))goto swd;s.call_step=8;break;
    case 8:if(!rd(DHCSR,&v))goto swd;if(v&0x20000)s.call_step=9;break;
    case 9:if(!wr(DCRSR,15))goto swd;s.call_step=10;break;
    case 10:if(!rd(DHCSR,&v))goto swd;if(v&0x10000)s.call_step=11;break;
    case 11:
        if(!rd(DCRDR,&v))goto swd;
        s.call_pc=v;
        if((v&~1u)!=BASE+0x500 && (v&~1u)!=BASE+0x502) {finish(RM_FLASH_FAILED,RM_FLASH_ALGORITHM);return;}
        s.call_step=12;break;
    case 12:if(!wr(DCRSR,0))goto swd;s.call_step=13;break;
    case 13:if(!rd(DHCSR,&v))goto swd;if(v&0x10000)s.call_step=14;break;
    case 14:
        if(!rd(DCRDR,&v))goto swd;
        if(v) {finish(RM_FLASH_FAILED,RM_FLASH_ALGORITHM);return;}
        s.call_step=0;s.call_done=true;break;
    default:finish(RM_FLASH_FAILED,RM_FLASH_STATE);break;
    }
    return;
swd:finish(RM_FLASH_FAILED,RM_FLASH_SWD);
}
bool rmlink_flash_run(void) {
    if(!rmlink_flash_busy())return false;
    if(s.cancel) {finish(RM_FLASH_CANCELLED,RM_FLASH_CANCEL);return true;}
    if(io.now()-s.start>=120000000) {finish(RM_FLASH_FAILED,RM_FLASH_TIMEOUT);return true;}
    if(s.call_step) {call_run();return true;}
    uint32_t v;uint8_t data[32];size_t n;
    switch(s.state) {
    case RM_FLASH_PREFLIGHT:
        if(s.step==0) {if(!rd(0xe000ed00,&v))goto swd;if(v!=0x411fc231)goto target;s.step++;}
        else if(s.step==1) {if(!rd(0xe0042000,&v))goto swd;if((v&0xfff)!=0x410)goto target;s.step++;}
        else if(s.step==2) {if(!rd(0x1ffff7e0,&v))goto swd;if((v&0xffff)!=64)goto target;s.step++;}
        else if(s.step<7) {if(!rd(0x1ffff800+(s.step-3)*4,&s.options[s.step-3]))goto swd;s.step++;}
        else if(s.step==7) {
            if(whole_image()) {if(!rd(0x40022020,&v))goto swd;if((v&0xffff)!=0xffff)goto target;}
            s.touched=true;if(!wr(DHCSR,0xa05f0003))goto swd;s.call_deadline=io.now()+1000000;s.step++;
        }
        else if(s.step==8) {if(!rd(DHCSR,&v))goto swd;if(v&0x20000)s.step++;else if(io.now()>=s.call_deadline)goto timeout;}
        else if(s.step==9) {
            if(!rd(0xe0002000,&v))goto swd;
            if(v>>28)goto target;
            s.fp_count=((v>>4)&15)|((v>>8)&0x70);if(s.fp_count>32)goto target;s.step++;
        }else if(s.step<10+s.fp_count) {if(!wr(0xe0002008+4*(s.step-10),0))goto swd;s.step++;}
        else phase(RM_FLASH_LOAD);
        break;
    case RM_FLASH_LOAD:
        if(s.code_offset<sizeof(rmlink_f103_algorithm)) {
            n=sizeof(rmlink_f103_algorithm)-s.code_offset;if(n>32)n=32;
            s.error_address=BASE+s.code_offset;
            if(!io.write(s.error_address,rmlink_f103_algorithm+s.code_offset,n))goto swd;
            s.code_offset+=n;
        }else if(!s.step) {call_begin(0,s.address,8000000,2,3);s.step=1;}
        else if(s.call_done)phase(RM_FLASH_ERASE);
        break;
    case RM_FLASH_ERASE:
        if(!s.step) {
            s.flags|=1;
            if(whole_image())call_begin(0x600,s.address,s.length,0x524d4c4b,3);
            else call_begin(0x200,s.address+s.offset,0,0,1);
            s.step=1;
        }
        else if(s.call_done) {s.erased=whole_image()?s.length/PAGE:s.erased+1;s.buffer_offset=0;phase(RM_FLASH_PROGRAM);}
        break;
    case RM_FLASH_PROGRAM:
        if(s.buffer_offset<PAGE) {
            s.error_address=BUFFER+s.buffer_offset;
            if(!io.write(s.error_address,s.image+s.offset+s.buffer_offset,32))goto swd;
            s.buffer_offset+=32;
        }else if(!s.step) {call_begin(0x300,s.address+s.offset,PAGE,BUFFER,3);s.step=1;}
        else if(s.call_done) {s.programmed+=PAGE;phase(RM_FLASH_PAGE_VERIFY);}
        break;
    case RM_FLASH_PAGE_VERIFY:
        /* This pinned ProgramPage compares each programmed halfword before a
         * successful return. Full-image probe readback remains mandatory. */
        s.offset+=PAGE;s.buffer_offset=0;
        phase(s.offset<s.length?(whole_image()?RM_FLASH_PROGRAM:RM_FLASH_ERASE):RM_FLASH_VERIFY);
        break;
    case RM_FLASH_VERIFY:
        if(!s.step) {call_begin(0x100,2,0,0,1);s.step=1;}
        else if(s.step==1) {if(s.call_done)s.step=2;}
        else if(s.verified<s.length) {
            s.error_address=s.address+s.verified;
            if(!io.read(s.error_address,data,32))goto swd;
            if(memcmp(data,s.image+s.verified,32)) {finish(RM_FLASH_FAILED,RM_FLASH_MISMATCH);return true;}
            s.verified+=32;
        }else {s.flags|=2;phase(RM_FLASH_RESET);}
        break;
    case RM_FLASH_RESET:
        if(s.step<4) {
            if(!rd(0x1ffff800+s.step*4,&v))goto swd;
            if(v!=s.options[s.step]) {finish(RM_FLASH_FAILED,RM_FLASH_OPTIONS);return true;}s.step++;
        }else if(s.step==4) {if(!wr(0xe000ed0c,0x05fa0004))goto swd;s.reset_at=io.now()+20000;s.step++;}
        else if(s.step==5 && io.now()>=s.reset_at) {if(!wr(DHCSR,0xa05f0001))goto swd;s.call_deadline=io.now()+1000000;s.step++;}
        else if(s.step==6) {if(!rd(DHCSR,&v))goto swd;if(!(v&0x20000))finish(RM_FLASH_COMPLETE,0);else if(io.now()>=s.call_deadline)goto timeout;}
        break;
    default:finish(RM_FLASH_FAILED,RM_FLASH_STATE);break;
    }
    return true;
swd:finish(RM_FLASH_FAILED,RM_FLASH_SWD);return true;
target:finish(RM_FLASH_FAILED,RM_FLASH_TARGET);return true;
timeout:finish(RM_FLASH_FAILED,RM_FLASH_TIMEOUT);return true;
}
size_t rmlink_flash_command(uint8_t transport,uint32_t session,const uint8_t *r,size_t len,
    uint8_t *out,size_t capacity,const rmlink_owner_t *owner,bool may_start) {
    out[0]=0x82;out[1]=len>1?r[1]:255;out[2]=RM_FLASH_ARGUMENT;
    if(len<2 || capacity<64 || capacity>RMLINK_PACKET_SIZE || len>capacity)return 3;
    if(!r[1]) {
        out[2]=0;out[3]=1;out[4]=1;w32(out+5,65536);out[9]=0;out[10]=4;
        out[11]=(uint8_t)(capacity-16);out[12]=(capacity-16)>>8;out[13]=15;return 14;
    }
    if(!rmlink_owner_matches(owner,transport,session) ||
       (s.session && (s.session!=session || s.transport!=transport))) {out[2]=RM_FLASH_OWNER;return 3;}
    if(len<6 || !u32(r+2))return 3;
    uint32_t id=u32(r+2);
    if(r[1]==1) {
        if(rmlink_flash_busy()) {out[2]=RM_FLASH_BUSY;return 3;}
        if(len<46)return 3;
        uint32_t address=u32(r+6),size=u32(r+10);
        if((address&(PAGE-1)) || !size || (size&(PAGE-1)) || size>65536 || address<0x08000000 || address>0x08010000-size)return 3;
        if(s.id==id && s.state) {out[2]=RM_FLASH_STATE;return 3;}
        if(s.image)io.release(s.image);
        memset(&s,0,sizeof(s));s.image=io.allocate(size);
        if(!s.image) {out[2]=RM_FLASH_MEMORY;return 3;}
        s.transport=transport;s.session=session;s.id=id;s.address=address;s.length=size;
        memcpy(s.digest,r+14,32);phase(RM_FLASH_UPLOAD);out[2]=0;return 3;
    }
    if(id!=s.id || !s.state) {out[2]=RM_FLASH_STATE;return 3;}
    if(r[1]==5) { /* STATUS: fixed 46 bytes fits native USB64. */
        out[2]=0;out[3]=s.state;out[4]=s.error;out[5]=s.flags;w32(out+6,s.id);
        w32(out+10,s.address);w32(out+14,s.length);w32(out+18,s.received);w32(out+22,s.erased);
        w32(out+26,s.programmed);w32(out+30,s.verified);
        w64(out+34,s.start?(rmlink_flash_busy()?io.now():s.end)-s.start:0);w32(out+42,s.error_address);return 46;
    }
    if(r[1]==9) {
        out[2]=0;int index=timing_index(s.state);
        for(unsigned i=0;i<7;i++)w64(out+3+i*8,s.timings[i]+((int)i==index?io.now()-s.phase_start:0));
        return 59;
    }
    if(r[1]==6) {
        if(rmlink_flash_busy())s.cancel=true;
        else if(s.state==RM_FLASH_UPLOAD || s.state==RM_FLASH_READY)finish(RM_FLASH_CANCELLED,RM_FLASH_CANCEL);
        else {out[2]=RM_FLASH_STATE;return 3;}out[2]=0;return 3;
    }
    if(r[1]==2) {
        if(s.state!=RM_FLASH_UPLOAD) {out[2]=RM_FLASH_STATE;return 3;}
        if(len<16)return 3;
        uint32_t offset=u32(r+6);size_t n=r[10]|(size_t)r[11]<<8;
        if(!n || n>capacity-16 || len<16+n || offset>s.length || n>s.length-offset || offset>s.received)return 3;
        if(crc32(r+16,n)!=u32(r+12)) {out[2]=RM_FLASH_CRC;return 3;}
        if(offset<s.received) {if(n>s.received-offset || memcmp(s.image+offset,r+16,n))return 3;}
        else {memcpy(s.image+offset,r+16,n);s.received+=n;}
        out[2]=0;w32(out+3,s.received);return 7;
    }
    if(r[1]==3) {
        if(s.state!=RM_FLASH_UPLOAD || s.received!=s.length) {out[2]=RM_FLASH_STATE;return 3;}
        uint8_t digest[32];if(!io.sha256(s.image,s.length,digest) || memcmp(digest,s.digest,32)) {
            finish(RM_FLASH_FAILED,RM_FLASH_HASH);out[2]=RM_FLASH_HASH;return 3;
        }
        phase(RM_FLASH_READY);out[2]=0;return 3;
    }
    if(r[1]==4) {
        if(s.state!=RM_FLASH_READY) {out[2]=RM_FLASH_STATE;return 3;}
        if(!may_start) {out[2]=RM_FLASH_BUSY;return 3;}
        s.start=io.now();phase(RM_FLASH_PREFLIGHT);out[2]=0;return 3;
    }
    return 3;
}
