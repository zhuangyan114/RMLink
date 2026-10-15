/* SPDX-License-Identifier: Apache-2.0
 * Probe subscriptions reuse ARM DAP_ProcessCommand on the existing executor.
 * No other task calls GPIO, SWD_Transfer or this module's target operations.
 */
#ifdef RMLINK_HOST_TEST
#include "capture_platform.h"
#else
#include "sdkconfig.h"
#endif
#if CONFIG_DEBUG_PROBE_IFACE_SWD
#include <string.h>
#include <stdatomic.h>
#ifndef RMLINK_HOST_TEST
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "DAP.h"
#endif
#include "rmlink_capture.h"

#define SLOTS 4
#define FIELDS 32
#define RECORD_MAX 384
#define LEASE_US 15000000LL
typedef struct {
    uint8_t kind, count, state, index; /* 0 empty, 1 prepared, 2 running, 3 paused */
    uint8_t reason; /* 1 pause,2 stop,3 SWD,4 lease,5 stream,6 budget,7 limit */
    uint32_t period, limit, supplied, revision, capture, seq;
    uint32_t address[FIELDS], values[FIELDS], valid;
    uint8_t depth[FIELDS], width[FIELDS];
    int32_t offset[FIELDS][4];
    uint32_t cache_address[128],cache_value[128];
    unsigned cache_count;
    uint64_t next, started, timestamp, last_timestamp, frames, missed, dropped, failures;
    bool reused;
    bool strict_deadlines;
    uint32_t rtt_channel, rtt_bytes, rtt_pending_rd, rtt_old_rd;
    uint32_t budget_overrun;
    uint8_t pending[256];
    bool rtt_pending;
} subscription_t;
static subscription_t s_sub[SLOTS];
static uint8_t s_transport;
static uint32_t s_session, s_capture;
static bool s_barrier;
static uint64_t s_lease;
static esp_timer_handle_t s_timer;
static uint64_t s_timer_due;
static void (*s_wake)(void);
static RingbufHandle_t s_records[SLOTS];
static portMUX_TYPE s_stream_mux = portMUX_INITIALIZER_UNLOCKED;
static uint8_t s_ticket[16];
static uint32_t s_generation;
static bool s_ticket_valid, s_bound;
static bool s_usb_pull;
static uint8_t s_usb_frame[RECORD_MAX];
static size_t s_usb_length;
static uint32_t s_usb_frame_id;
static uint64_t s_last_stream;
static atomic_bool s_stream_failed;

static uint32_t u32(const uint8_t *p) { return p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24; }
static void w16(uint8_t *p, uint16_t v) { p[0]=v; p[1]=v>>8; }
static void w32(uint8_t *p, uint32_t v) { for(unsigned i=0;i<4;i++) p[i]=v>>(8*i); }
static void w64(uint8_t *p, uint64_t v) { for(unsigned i=0;i<8;i++) p[i]=v>>(8*i); }
static uint32_t crc32(const uint8_t *p,size_t n) {
    uint32_t v=~0u;
    while(n--) { v^=*p++; for(unsigned b=0;b<8;b++) v=(v>>1)^(0xedb88320u & (0u-(v&1))); }
    return ~v;
}
/* First target family: F103C8. Unknown/peripheral regions must be explicit. */
static bool memory(uint32_t a, size_t n, bool write) {
    if (!n || n>65536 || a>UINT32_MAX-n) return false;
    return (a>=0x20000000u && a+n<=0x20005000u) ||
        (!write && a>=0x08000000u && a+n<=0x08010000u);
}
static bool transfer(const uint8_t *request, uint8_t *out, uint8_t count) {
    /* Bound bad-address WAIT handling, keep asynchronous Abort effective. */
    DAP_TransferDeadline(2000);
    size_t n=DAP_ProcessCommand(request,out)&0xffff;
    return n>=3 && out[0]==5 && out[1]==count && out[2]==1;
}
static bool word(uint32_t addr,uint32_t *value,bool write) {
    uint8_t req[24]={5,0,4,8},reply[32];
    /* SELECT=0, CSW=32-bit single increment, TAR, DRW. No cached state. */
    w32(req+4,0); req[8]=1; w32(req+9,0x23000052);
    req[13]=5; w32(req+14,addr); req[18]=write?13:15;
    if(write) w32(req+19,*value);
    if(!transfer(req,reply,4)) return false;
    if(!write) *value=u32(reply+3);
    return true;
}
static bool block(uint32_t address,uint32_t *values,unsigned count) {
    uint8_t request[32]={5,0,0,8},reply[40];
    if(!count || count>8 || (address&3) || !memory(address,count*4,false) ||
       (address&0x3ff)+count*4>0x400) return false;
    w32(request+4,0);request[8]=1;w32(request+9,0x23000052);
    request[13]=5;w32(request+14,address);
    memset(request+18,15,count);request[2]=3+count;
    if(!transfer(request,reply,3+count))return false;
    for(unsigned i=0;i<count;i++) values[i]=u32(reply+3+i*4);
    return true;
}
static bool scattered(const uint32_t *addresses,uint32_t *values,unsigned count) {
    uint8_t request[64]={5,0,0,8},reply[40];
    if(!count || count>8)return false;
    w32(request+4,0);request[8]=1;w32(request+9,0x23000052);
    size_t p=13;
    for(unsigned i=0;i<count;i++) { request[p++]=5;w32(request+p,addresses[i]);p+=4;request[p++]=15; }
    request[2]=2+count*2;
    if(!transfer(request,reply,request[2]))return false;
    for(unsigned i=0;i<count;i++)values[i]=u32(reply+3+i*4);
    return true;
}
static bool bytes_read(uint32_t addr,uint8_t *out,size_t n) {
    if(!memory(addr,n,false)) return false;
    while(n) {
        if(!(addr&3) && n>=4) {
            uint32_t values[8];unsigned words=n/4;
            if(words>8)words=8;
            unsigned boundary=(0x400-(addr&0x3ff))/4;if(words>boundary)words=boundary;
            if(!block(addr,values,words))return false;
            for(unsigned i=0;i<words;i++)w32(out+4*i,values[i]);
            addr+=words*4;out+=words*4;n-=words*4;continue;
        }
        uint32_t v;
        if(!word(addr&~3u,&v,false)) return false;
        unsigned offset=addr&3u, part=4-offset;
        if(part>n) part=n;
        for(unsigned i=0;i<part;i++) out[i]=(uint8_t)(v>>(8*(offset+i)));
        addr+=part; out+=part; n-=part;
    }
    return true;
}
static void timer_cb(void *unused) { (void)unused; if(s_wake) s_wake(); }
static void timer_refresh(void) {
    uint64_t due=UINT64_MAX;
    bool in_flight=false;
    for(unsigned i=0;i<SLOTS;i++) if(s_sub[i].state==2) {
        if(s_sub[i].started)in_flight=true;
        if(s_sub[i].next<due)due=s_sub[i].next;
    }
    bool running=esp_timer_is_active(s_timer);
    /* The executor immediately continues partial frames. A 10 kHz periodic
     * timer otherwise preempts every SWD block even for a 10 Hz subscription. */
    if(in_flight || due==UINT64_MAX) {
        if(running)esp_timer_stop(s_timer);
        return;
    }
    if(running && due==s_timer_due)return;
    if(running)esp_timer_stop(s_timer);
    uint64_t now=esp_timer_get_time(),delay=due>now?due-now:1;
    s_timer_due=due;
    esp_timer_start_once(s_timer,delay<20?20:delay);
}
esp_err_t rmlink_capture_init(void (*wake)(void)) {
    s_wake=wake;
    const size_t sizes[SLOTS]={48*1024,4*1024,6*1024,6*1024};
    for(unsigned i=0;i<SLOTS;i++) {
        s_records[i]=xRingbufferCreate(sizes[i],RINGBUF_TYPE_NOSPLIT);
        if(!s_records[i]) return ESP_ERR_NO_MEM;
    }
    esp_timer_create_args_t args={.callback=timer_cb,.name="rmlink_capture",.skip_unhandled_events=true};
    return esp_timer_create(&args,&s_timer);
}
static void stream_reset(void) {
    portENTER_CRITICAL(&s_stream_mux);
    ++s_generation; s_ticket_valid=false; s_bound=false;s_usb_pull=false;
    portEXIT_CRITICAL(&s_stream_mux);
    atomic_store(&s_stream_failed,false);
    s_usb_length=0;s_usb_frame_id=0;
    size_t n; void *p;
    for(unsigned i=0;i<SLOTS;i++)
        while((p=xRingbufferReceive(s_records[i],&n,0))) vRingbufferReturnItem(s_records[i],p);
}
void rmlink_capture_closed(uint8_t transport,uint32_t session) {
    if(s_transport!=transport || s_session!=session) return;
    memset(s_sub,0,sizeof(s_sub)); s_session=0; s_transport=0; s_barrier=false;
    timer_refresh(); stream_reset();
}
bool rmlink_capture_raw_allowed(void) {
    if(s_barrier) return true;
    for(unsigned i=0;i<SLOTS;i++) if(s_sub[i].state==2) return false;
    return true;
}
static void emit(subscription_t *s,unsigned id,uint64_t timestamp,uint32_t duration,
                 const uint8_t *data,size_t bytes,uint32_t valid,uint16_t flags) {
    uint8_t r[RECORD_MAX]={0}; size_t payload=24+bytes,total=32+payload;
    memcpy(r,"RML1",4); r[4]=1; r[5]=s->kind; r[6]=id;
    w32(r+8,s->capture); w32(r+12,s->revision); w32(r+16,s->seq);
    w32(r+20,payload); w16(r+24,1); w16(r+26,32);
    w32(r+32,s->seq); w64(r+36,timestamp); w32(r+44,duration);
    w32(r+48,valid); w16(r+52,flags); w16(r+54,bytes);
    memcpy(r+56,data,bytes); w32(r+28,crc32(r+32,payload));
    RingbufHandle_t ring=s_records[id-1];
    if(xRingbufferSend(ring,r,total,0)==pdTRUE)return;
    if(s->kind!=3) {
        /* Realtime memory subscriptions retain recent complete frames. Bound
         * disposal even if a concurrent sender still holds an unreturned item. */
        for(unsigned retry=0;retry<8;retry++) {
            size_t old_size;void *old=xRingbufferReceive(ring,&old_size,0);
            if(!old)break;
            vRingbufferReturnItem(ring,old);s->dropped++;
            if(xRingbufferSend(ring,r,total,0)==pdTRUE)return;
        }
    }
    s->dropped++; /* New frame could not fit; never wait in the SWD executor. */
}
static void sample_finish(subscription_t *s,unsigned id) {
    uint8_t values[FIELDS*4];
    for(unsigned i=0;i<s->count;i++) w32(values+4*i,s->values[i]);
    uint32_t all=s->count==32?~0u:((1u<<s->count)-1);
    uint64_t ended=esp_timer_get_time();
    uint32_t duration=(uint32_t)(ended-s->timestamp);
    bool overrun=s->index==s->count && ended-s->started>s->period;
    emit(s,id,s->timestamp,duration,values,s->count*4,s->valid,(s->valid==all?0:1)|(overrun?2:0)|(s->reused?4:0));
    s->last_timestamp=s->timestamp;
    s->frames++; s->index=0; s->started=0; s->seq++;
    if(overrun) {
        s->budget_overrun++;
        if(s->strict_deadlines) {s->state=3;s->reason=6;}
    }
    if(!s->strict_deadlines && ended>=s->next) {
        uint64_t skipped=(ended-s->next)/s->period+1;
        s->next+=skipped*s->period;s->missed+=skipped;s->seq+=(uint32_t)skipped;
    }
    if(s->limit && s->frames>=s->limit) { s->state=3; s->reason=7; }
}
static bool reuse_timeline(uint32_t address,uint32_t *value) {
    subscription_t *t=&s_sub[0];
    if(t->state!=2 || !t->frames || t->started) return false;
    for(unsigned i=0;i<t->count;i++) if(t->address[i]==address && !t->depth[i] && t->width[i]==4 && (t->valid&(1u<<i))) {
        *value=t->values[i]; return true;
    }
    return false;
}
static bool frame_word(subscription_t *s,uint32_t address,uint32_t *value) {
    for(unsigned i=0;i<s->cache_count;i++)if(s->cache_address[i]==address) { *value=s->cache_value[i];return true; }
    if(!word(address,value,false))return false;
    if(s->cache_count<128) {
        s->cache_address[s->cache_count]=address;s->cache_value[s->cache_count++]=*value;
    }
    return true;
}
/* 0: SWD failure, 1: valid, 2: currently invalid pointer/range (keep sampling). */
static unsigned field(subscription_t *s,unsigned i,uint32_t *value) {
    uint32_t address=s->address[i];
    for(unsigned d=0;d<s->depth[i];d++) {
        uint32_t pointer;
        if((address&3) || !memory(address,4,false)) return 2;
        if(!frame_word(s,address,&pointer)) return 0;
        int64_t next=(int64_t)pointer+s->offset[i][d];
        if(next<0 || next>UINT32_MAX) return 2;
        address=(uint32_t)next;
    }
    unsigned width=s->width[i];
    if(address%width || !memory(address,width,false)) return 2;
    if(!frame_word(s,address&~3u,value)) return 0;
    *value>>=(address&3)*8;
    if(width<4) *value&=(1u<<(width*8))-1;
    return 1;
}
static void rtt_run(subscription_t *s,unsigned id) {
    /* F103 32-bit RTT descriptor: name,buffer,size,WrOff,RdOff,flags. */
    uint32_t cb=s->address[0],desc=cb+24+s->rtt_channel*24;
    uint8_t header[24],d[24];
    if(!bytes_read(cb,header,24) || memcmp(header,"SEGGER RTT",10) ||
       u32(header+16)>16 || s->rtt_channel>=u32(header+16) || !bytes_read(desc,d,24)) goto fail;
    uint32_t base=u32(d+4),size=u32(d+8),wr=u32(d+12),rd=u32(d+16);
    if(size<2 || size>65536 || wr>=size || rd>=size || !memory(base,size,true)) goto fail;
    if(s->rtt_pending) {
        if(rd==s->rtt_pending_rd) goto publish;
        if(rd!=s->rtt_old_rd) goto fail;
        uint32_t wanted=s->rtt_pending_rd;
        if(!word(desc+16,&wanted,true)) { s->state=3; s->reason=3; s->failures++; return; }
        goto publish;
    }
    unsigned available=(wr+size-rd)%size;
    if(!available) return;
    unsigned n=available>64?64:available;
    /* Reserve enough space before consuming target bytes. */
    if(xRingbufferGetCurFreeSize(s_records[id-1])<RECORD_MAX+16) { s->dropped++; return; }
    unsigned first=size-rd; if(first>n) first=n;
    if(!bytes_read(base+rd,s->pending,first) ||
       (n>first && !bytes_read(base,s->pending+first,n-first))) goto fail;
    s->rtt_bytes=n; s->rtt_old_rd=rd; s->rtt_pending_rd=(rd+n)%size; s->rtt_pending=true;
    uint32_t wanted=s->rtt_pending_rd;
    if(!word(desc+16,&wanted,true)) { s->state=3; s->reason=3; s->failures++; return; }
publish:;
    uint8_t payload[80]; w32(payload,s->rtt_channel); w64(payload+4,s->frames);
    w32(payload+12,s->rtt_bytes); memcpy(payload+16,s->pending,s->rtt_bytes);
    emit(s,id,esp_timer_get_time(),0,payload,16+s->rtt_bytes,0,0);
    s->frames+=s->rtt_bytes; s->seq++; s->rtt_pending=false; return;
fail:
    s->failures++; s->state=3; s->reason=3;
}
bool rmlink_capture_run(void) {
    if(!s_timer)return false;
    uint64_t now=esp_timer_get_time();
    if(!s_session || s_barrier) return false;
    if(now>s_lease || atomic_load(&s_stream_failed)) {
        for(unsigned i=0;i<SLOTS;i++) if(s_sub[i].state==2) { s_sub[i].state=3; s_sub[i].reason=now>s_lease?4:5; }
        timer_refresh(); return false;
    }
    /* Timeline first, low-rate Watch next, bounded RTT last. */
    for(unsigned k=0;k<SLOTS;k++) {
        subscription_t *s=&s_sub[k];
        if(s->state!=2 || (!s->started && now<s->next)) continue;
        if(!s->started) {
            uint64_t missed=(now-s->next)/s->period;
            s->missed+=missed; s->seq+=(uint32_t)missed;
            s->next+=(missed+1)*s->period;
            s->started=now; s->timestamp=now; s->reused=false; s->valid=0; memset(s->values,0,sizeof(s->values));
            s->cache_count=0;
        }
        if(s->kind==3) { rtt_run(s,k+1); s->started=0; timer_refresh(); return true; }
        unsigned end=s->index+(s->kind==1?8:4); if(end>s->count) end=s->count;
        bool failed=false;
        uint64_t block_start=esp_timer_get_time();
        bool static_group=s->kind==1;
        for(unsigned i=s->index;i<end;i++)if(s->depth[i] || s->width[i]!=4)static_group=false;
        if(!static_group && end>s->index+4u)end=s->index+4u;
        if(static_group) {
            bool contiguous=true;
            for(unsigned i=s->index+1;i<end;i++)if(s->address[i]!=s->address[i-1]+4)contiguous=false;
            unsigned count=end-s->index;
            bool ok=contiguous && (s->address[s->index]&0x3ff)+count*4<=0x400?
                block(s->address[s->index],s->values+s->index,count):
                scattered(s->address+s->index,s->values+s->index,count);
            if(ok) {
                for(;s->index<end;s->index++)s->valid|=1u<<s->index;
            }else { s->failures++;failed=true; }
        }
        for(;!static_group && s->index<end;s->index++) {
            uint32_t v=0;
            unsigned ok=s->kind==2 && !s->depth[s->index] && s->width[s->index]==4 && reuse_timeline(s->address[s->index],&v);
            if(ok) { s->reused=true; if(s_sub[0].last_timestamp<s->timestamp) s->timestamp=s_sub[0].last_timestamp; }
            if(!ok) ok=field(s,s->index,&v);
            if(ok==1) { s->values[s->index]=v; s->valid|=1u<<s->index; }
            else if(ok==2) s->failures++;
            else { s->failures++; failed=true; break; }
            if(esp_timer_get_time()-block_start>=200) { s->index++; break; }
        }
        if(s->index==s->count || failed) {
            sample_finish(s,k+1);
            if(failed) { s->state=3; s->reason=3; }
        }
        timer_refresh(); return true;
    }
    return false;
}
static void pause_all(void) {
    for(unsigned i=0;i<SLOTS;i++) {
        subscription_t *s=&s_sub[i];
        if(s->started && s->kind!=3) sample_finish(s,i+1);
        s->started=0;
    }
}
size_t rmlink_capture_command(uint8_t transport,uint32_t session,const uint8_t *r,
                            size_t len,uint8_t *out,const rmlink_owner_t *owner) {
    out[0]=0x81; out[1]=len>1?r[1]:255; out[2]=1;
    if(len<2) return 3;
    if(r[1]==13) { /* TIME_SYNC: echo token64, executor receive/send timestamps. */
        uint64_t received=esp_timer_get_time();
        if(len<10)return 3;
        out[2]=0;memcpy(out+3,r+2,8);w64(out+11,received);
        w64(out+19,esp_timer_get_time());return 27;
    }
    if(!s_timer) { out[2]=4; return 3; }
    if(r[1]==0) {
        out[2]=0; out[3]=1; out[4]=FIELDS; out[5]=SLOTS; w16(out+6,3244);
        w32(out+8,4000000); w32(out+12,200); w32(out+16,64*1024);
        w32(out+20,15); /* time sync, drop-old, USB pull, deadline policy */
        return 24;
    }
#if !CONFIG_RMLINK_TARGET_PINS_CONFIRMED
    out[2]=2; return 3;
#endif
    if(!rmlink_owner_matches(owner,transport,session)) { out[2]=2; return 3; }
    if(s_session && (s_session!=session || s_transport!=transport)) { out[2]=2; return 3; }
    s_session=session; s_transport=transport; s_lease=esp_timer_get_time()+LEASE_US;
    if(r[1]==16) { /* USB_PULL: frame_id32, offset16; duplicate reads are stable. */
        if(transport!=RMLINK_USB || len<8) {out[2]=2;return 3;}
        uint32_t id=u32(r+2);unsigned offset=r[6]|(unsigned)r[7]<<8;
        uint32_t next=s_usb_frame_id+1;if(!next)next=1;
        if(!id || (id!=s_usb_frame_id && (id!=next || offset)))return 3;
        portENTER_CRITICAL(&s_stream_mux);
        if(s_bound && !s_usb_pull) {portEXIT_CRITICAL(&s_stream_mux);out[2]=3;return 3;}
        s_bound=true;s_usb_pull=true;s_ticket_valid=false;
        portEXIT_CRITICAL(&s_stream_mux);
        if(id!=s_usb_frame_id) {
            size_t n=rmlink_capture_take(s_usb_frame,sizeof(s_usb_frame),0);
            if(!n) {out[2]=0;w32(out+3,id);memset(out+7,0,5);return 12;}
            s_usb_frame_id=id;s_usb_length=n;
        }
        if(offset>s_usb_length)return 3;
        size_t chunk=s_usb_length-offset;if(chunk>RMLINK_USB_PACKET_SIZE-12)chunk=RMLINK_USB_PACKET_SIZE-12;
        out[2]=0;w32(out+3,id);w16(out+7,s_usb_length);w16(out+9,offset);out[11]=chunk;
        memcpy(out+12,s_usb_frame+offset,chunk);return 12+chunk;
    }
    if(r[1]==12) { out[2]=0; return 3; }
    if(r[1]==11) {
        uint8_t ticket[16]; esp_fill_random(ticket,16);
        portENTER_CRITICAL(&s_stream_mux);
        if(s_bound) { portEXIT_CRITICAL(&s_stream_mux); out[2]=3; return 3; }
        memcpy(s_ticket,ticket,16); s_ticket_valid=true; s_last_stream=esp_timer_get_time();
        portEXIT_CRITICAL(&s_stream_mux);
        out[2]=0; memcpy(out+3,ticket,16); return 19;
    }
    if(r[1]==8) { pause_all(); s_barrier=true; out[2]=0; return 3; }
    if(r[1]==9) { s_barrier=false; out[2]=0; return 3; }
    if(r[1]==10 && len>=15) {
        uint32_t addr=u32(r+2),stride=u32(r+6),count=u32(r+10); unsigned fields=r[14];
        if(!fields || fields>FIELDS || !count || count>10000 || (stride&3) || (addr&3)) return 3;
        for(unsigned j=0;j<fields;j++) {
            uint64_t a=(uint64_t)addr+(uint64_t)j*stride;
            if(a>UINT32_MAX || !memory(a,4,false)) return 3;
        }
        if(!rmlink_capture_raw_allowed()) { out[2]=3; return 3; }
        uint64_t start=esp_timer_get_time(); uint32_t done=0,checksum=0;
        while(done<count && esp_timer_get_time()-start<500000) {
            unsigned j=done%fields,chunk=fields-j;
            if(chunk>count-done)chunk=count-done;
            if(chunk>8)chunk=8;
            uint32_t values[8];bool ok;
            if(stride==4) {
                unsigned boundary=(0x400-((addr+j*stride)&0x3ff))/4;
                if(chunk>boundary)chunk=boundary;
                ok=block(addr+j*stride,values,chunk);
            }else {
                uint32_t addresses[8];for(unsigned i=0;i<chunk;i++)addresses[i]=addr+(j+i)*stride;
                ok=scattered(addresses,values,chunk);
            }
            if(!ok)break;
            for(unsigned i=0;i<chunk;i++)checksum^=values[i];
            done+=chunk;
        }
        out[2]=0; w32(out+3,done); w64(out+7,esp_timer_get_time()-start); w32(out+15,checksum); return 19;
    }
    if(len<3 || !r[2] || r[2]>SLOTS) return 3;
    subscription_t *s=&s_sub[r[2]-1];
    switch(r[1]) {
    case 1: /* PREPARE: id,kind,count,period32,limit32,RTTchannel32 */
        if(len<17 || r[3]<1 || r[3]>3 || !r[4] || r[4]>FIELDS ||
           (r[3]==3 && (r[4]!=1 || r[2]<3)) || (r[3]==1 && r[2]!=1) || (r[3]==2 && r[2]!=2)) return 3;
        uint32_t period=u32(r+5); if(period<200 || period>10000000) return 3;
        if(s->state==2 || s->rtt_pending) { out[2]=3; return 3; }
        memset(s,0,sizeof(*s)); s->kind=r[3]; s->count=r[4]; s->period=period;
        s->strict_deadlines=len<18 || r[17]!=0; /* old clients retain strict behavior */
        memset(s->width,4,sizeof(s->width));
        s->limit=u32(r+9); s->rtt_channel=u32(r+13); s->state=1; s->revision=1; break;
    case 2: /* ADD: id,offset,count,addresses32 */
        if(len<5 || s->state!=1 || !r[4] || r[3]+r[4]>s->count || len<5u+4u*r[4]) return 3;
        for(unsigned j=0;j<r[4];j++) { uint32_t a=u32(r+5+4*j); if(!memory(a,1,false)) return 3; }
        for(unsigned j=0;j<r[4];j++) { s->address[r[3]+j]=u32(r+5+4*j); s->supplied|=1u<<(r[3]+j); } break;
    case 3: /* START */
        if(s->state!=1 || s->supplied!=(s->count==32?~0u:((1u<<s->count)-1))) return 3;
        for(unsigned i=0;i<s->count;i++)
            if((s->depth[i] && (s->address[i]&3)) || (!s->depth[i] && (s->address[i]%s->width[i] || !memory(s->address[i],s->width[i],false)))) return 3;
        s->capture=++s_capture; s->state=2; s->next=esp_timer_get_time(); s->seq=0;
        atomic_store(&s_stream_failed,false); break;
    case 4: /* STOP retains statistics */
    case 5: /* PAUSE */
        if(!s->state) return 3;
        if(s->started && s->kind!=3) sample_finish(s,r[2]);
        s->started=0; s->state=3; s->reason=r[1]==4?2:1; break;
    case 6: /* RESUME */
        if(s->state!=3) return 3;
        s->state=2; s->reason=0; s->next=esp_timer_get_time(); atomic_store(&s_stream_failed,false); break;
    case 7: /* STATS */
        out[2]=0; out[3]=s->state; out[4]=s->kind; out[5]=s->count;
        w32(out+6,s->capture); w32(out+10,s->seq); w64(out+14,s->frames);
        w64(out+22,s->missed); w64(out+30,s->dropped); w64(out+38,s->failures);
        w32(out+46,s->budget_overrun); out[50]=s->reason; return 51;
    case 15: /* FIELD: id,index,depth,width, signed offsets32. */
        if(len<6 || s->state!=1 || s->kind==3 || r[3]>=s->count || r[4]>4 ||
           (r[5]!=1 && r[5]!=2 && r[5]!=4) || len<6u+4u*r[4]) return 3;
        s->depth[r[3]]=r[4]; s->width[r[3]]=r[5];
        for(unsigned d=0;d<r[4];d++) s->offset[r[3]][d]=(int32_t)u32(r+6+4*d);
        break;
    default:return 3;
    }
    out[2]=0; timer_refresh(); return 3;
}
bool rmlink_capture_bind(const uint8_t ticket[16],uint32_t *generation) {
    bool ok;
    portENTER_CRITICAL(&s_stream_mux);
    ok=s_ticket_valid && !s_bound && memcmp(ticket,s_ticket,16)==0 &&
        (uint64_t)esp_timer_get_time()-s_last_stream<3000000;
    if(ok) { s_ticket_valid=false; s_bound=true; *generation=s_generation; }
    portEXIT_CRITICAL(&s_stream_mux); return ok;
}
bool rmlink_capture_stream_live(uint32_t generation) {
    portENTER_CRITICAL(&s_stream_mux); bool live=s_bound && !s_usb_pull && generation==s_generation;
    portEXIT_CRITICAL(&s_stream_mux); return live;
}
size_t rmlink_capture_take(uint8_t *out,size_t capacity,unsigned timeout_ms) {
    /* Weighted fairness; separate rings prevent RTT or Watch exhausting Timeline. */
    static unsigned turn;
    const unsigned order[]={0,0,0,0,1,2,3};
    for(unsigned j=0;j<7;j++) {
        unsigned i=order[(turn++)%7]; size_t n;
        void *p=xRingbufferReceive(s_records[i],&n,0);
        if(!p) continue;
        if(n<=capacity) memcpy(out,p,n);
        vRingbufferReturnItem(s_records[i],p); return n<=capacity?n:0;
    }
    if(timeout_ms) vTaskDelay(pdMS_TO_TICKS(timeout_ms));
    return 0;
}
void rmlink_capture_stream_lost(uint32_t generation) {
    portENTER_CRITICAL(&s_stream_mux);
    if(generation==s_generation) { s_bound=false; s_ticket_valid=false; atomic_store(&s_stream_failed,true); }
    portEXIT_CRITICAL(&s_stream_mux);
    if(s_wake) s_wake();
}
#endif
