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
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
#ifndef RMLINK_HOST_TEST
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_rom_crc.h"
#include "DAP.h"
#endif
#include "rmlink_capture.h"
#ifndef RMLINK_HOST_TEST
#include "rmlink_clock_trace.h"
#endif

#define SLOTS 4
#define FIELDS 32
#define CACHE_WORDS (FIELDS*5) /* four pointer reads and one value per field */
#define RECORD_MAX 384
#define LEASE_US 15000000LL
typedef struct {
    uint8_t kind, count, state, index; /* 0 empty, 1 prepared, 2 running, 3 paused */
    uint8_t reason; /* 1 pause,2 stop,3 SWD,4 lease,5 stream,6 budget,7 limit */
    uint8_t read_retry; /* 0 unused, 1 pending next executor turn, 2 consumed */
    uint32_t period, limit, supplied, revision, capture, seq;
    uint32_t address[FIELDS], values[FIELDS], valid;
    uint8_t depth[FIELDS], width[FIELDS];
    int32_t offset[FIELDS][4];
    uint32_t cache_address[CACHE_WORDS],cache_value[CACHE_WORDS];
    uint8_t cache_map[256]; /* index+1, cleared for every frame */
    unsigned cache_count;
    uint32_t resolve_address[FIELDS],resolved;
    uint8_t resolve_depth[FIELDS];
    uint64_t next, started, timestamp, last_timestamp, frames, missed, dropped, failures;
    bool reused;
    bool strict_deadlines;
    uint32_t rtt_channel, rtt_bytes, rtt_pending_rd, rtt_old_rd;
    uint32_t rtt_base,rtt_size,rtt_cursor;
    uint8_t rtt_phase;
    uint32_t budget_overrun;
    uint8_t pending[256];
    bool rtt_pending, rtt_commit_unknown;
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
#if CONFIG_SPIRAM && !defined(RMLINK_HOST_TEST)
/* CPU-only record payload; the ring control, task stacks, SWD data and USB DMA
 * remain internal. Stream/USB workers copy a record before sending it. */
static StaticRingbuffer_t s_external_record_control;
#endif
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
/* Consecutive capture work reuses SELECT=0 / CSW32 until an external command.
 * No TAR, target value or resolved pointer survives through this cache. */
static bool s_memap_configured;
static uint8_t s_failure[24];
static uint32_t s_io_commands,s_io_words,s_io_frames,s_io_max_us;
static uint64_t s_io_us;
static uint32_t s_read_retry_attempts,s_read_retry_recovered,s_read_retry_exhausted;
static uint64_t s_last_read_retry;
static bool s_lower_turn;
static unsigned s_lower_cursor=1;
void rmlink_capture_invalidate_ap(void) { s_memap_configured=false; }

static uint32_t u32(const uint8_t *p) { return p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24; }
static void w16(uint8_t *p, uint16_t v) { p[0]=v; p[1]=v>>8; }
static void w32(uint8_t *p, uint32_t v) { for(unsigned i=0;i<4;i++) p[i]=v>>(8*i); }
static void w64(uint8_t *p, uint64_t v) { for(unsigned i=0;i<8;i++) p[i]=v>>(8*i); }
static uint32_t crc32(const uint8_t *p,size_t n) {
#ifndef RMLINK_HOST_TEST
    return esp_rom_crc32_le(0,p,n);
#else
    uint32_t v=~0u;
    while(n--) { v^=*p++; for(unsigned b=0;b<8;b++) v=(v>>1)^(0xedb88320u & (0u-(v&1))); }
    return ~v;
#endif
}
/* First target family: F103C8. Unknown/peripheral regions must be explicit. */
static bool memory(uint32_t a, size_t n, bool write) {
    if (!n || n>65536 || a>UINT32_MAX-n) return false;
    return (a>=0x20000000u && a+n<=0x20005000u) ||
        (!write && a>=0x08000000u && a+n<=0x08010000u);
}
void rmlink_capture_failure_stats(uint8_t output[24]) {memcpy(output,s_failure,24);}
void rmlink_capture_profile_enable(bool enabled) {
#ifndef RMLINK_HOST_TEST
    SWD_ProfileEnable(enabled);
#else
    (void)enabled;
#endif
}
void rmlink_capture_read_recovery_stats(uint8_t out[21]) {
    out[0]=1;w32(out+1,s_read_retry_attempts);w32(out+5,s_read_retry_recovered);
    w32(out+9,s_read_retry_exhausted);w64(out+13,s_last_read_retry);
}
void rmlink_capture_performance_stats(uint8_t out[60]) {
    memset(out,0,60);out[0]=2;
#ifndef RMLINK_HOST_TEST
    DAP_SWDMetrics_t m;SWD_GetMetrics(&m);
    w32(out+1,m.transfers);w32(out+5,m.waits);w32(out+9,m.errors);
    w64(out+13,m.elapsed_cycles);w64(out+21,m.wire_clocks);
    w32(out+53,CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ*1000000u);
    out[57]=SWD_ProfileEnabled();
#endif
    w32(out+29,s_io_commands);w64(out+33,s_io_us);w32(out+41,s_io_words);
    w32(out+45,s_io_frames);w32(out+49,s_io_max_us);
}
static bool transfer(const uint8_t *request, uint8_t *out, uint8_t count,bool target_write) {
    /* Bound bad-address WAIT handling, keep asynchronous Abort effective. */
    int64_t started=esp_timer_get_time();
    DAP_TransferDeadline(2000);
    size_t n=DAP_ProcessCommand(request,out)&0xffff;
    uint32_t duration=(uint32_t)(esp_timer_get_time()-started);
    s_io_commands++;s_io_us+=duration;if(duration>s_io_max_us)s_io_max_us=duration;
    bool ok=n>=3 && out[0]==5 && out[1]==count && out[2]==1;
    s_memap_configured=ok; /* any partial/failed response makes state unknown */
    if(!ok) {
        uint64_t now=esp_timer_get_time(),elapsed=now-started;
        w32(s_failure,u32(s_failure)+1);w64(s_failure+4,now);
        w32(s_failure+12,elapsed>UINT32_MAX?UINT32_MAX:(uint32_t)elapsed);w16(s_failure+16,n);
        s_failure[18]=count;s_failure[19]=n>=3?out[1]:0;s_failure[20]=n>=3?out[2]:0;
        s_failure[21]=DAP_GetTransferAbortReason();s_failure[22]=target_write;s_failure[23]=0;
    }
#ifndef RMLINK_HOST_TEST
    /* Subscriptions stop on failure. Preserve the DAP reason without logging
     * successful samples or retrying a possibly completed target write. */
    if(!ok) ESP_LOGW("rmlink_capture","SWD failed: reply=%u count=%u/%u status=0x%02x elapsed=%u us abort=%u target_write=%u",
        (unsigned)n,n>=3?out[1]:0,count,n>=3?out[2]:0,
        (unsigned)(esp_timer_get_time()-started),s_failure[21],s_failure[22]);
#else
    (void)started;
#endif
    return ok;
}
static size_t memap_prefix(uint8_t *request) {
    request[0]=5;request[1]=0;request[2]=0;
    if(s_memap_configured)return 3;
    request[2]=2;request[3]=8;w32(request+4,0); /* DP SELECT=0 */
    request[8]=1;w32(request+9,0x23000052); /* AP CSW32, increment */
    return 13;
}
static bool word(uint32_t addr,uint32_t *value,bool write) {
    uint8_t req[24],reply[32];size_t p=memap_prefix(req);
    req[p++]=5;w32(req+p,addr);p+=4;req[p++]=write?13:15;
    if(write)w32(req+p,*value);
    req[2]+=2;
    if(!transfer(req,reply,req[2],write)) return false;
    if(!write) *value=u32(reply+3);
    if(!write)s_io_words++;
    return true;
}
static bool block(uint32_t address,uint32_t *values,unsigned count) {
    uint8_t request[32],reply[40];
    if(!count || count>8 || (address&3) || !memory(address,count*4,false) ||
       (address&0x3ff)+count*4>0x400) return false;
    size_t p=memap_prefix(request);
    request[p++]=5;w32(request+p,address);p+=4;
    memset(request+p,15,count);request[2]+=1+count;
    if(!transfer(request,reply,request[2],false))return false;
    for(unsigned i=0;i<count;i++) values[i]=u32(reply+3+i*4);
    s_io_words+=count;
    return true;
}
static bool scattered(const uint32_t *addresses,uint32_t *values,unsigned count) {
    uint8_t request[64],reply[40];
    if(!count || count>8)return false;
    size_t p=memap_prefix(request);
    for(unsigned i=0;i<count;i++) {
        if(!i || addresses[i]!=addresses[i-1]+4 || !(addresses[i]&0x3ff)) {
            request[p++]=5;w32(request+p,addresses[i]);p+=4;request[2]++;
        }
        request[p++]=15;request[2]++;
    }
    if(!transfer(request,reply,request[2],false))return false;
    for(unsigned i=0;i<count;i++)values[i]=u32(reply+3+i*4);
    s_io_words+=count;
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
    uint64_t now=esp_timer_get_time();
    /* swd_task immediately calls run() again while work is ready. An overdue
     * subscription needs no 20 us timer interrupt in the middle of SWD. */
    if(due<=now) {if(running)esp_timer_stop(s_timer);return;}
    if(running && due==s_timer_due)return;
    if(running)esp_timer_stop(s_timer);
    uint64_t delay=due-now;
    s_timer_due=due;
    esp_timer_start_once(s_timer,delay<20?20:delay);
}
esp_err_t rmlink_capture_init(void (*wake)(void)) {
    rmlink_capture_invalidate_ap();
    s_wake=wake;
    const size_t sizes[SLOTS]={48*1024,4*1024,6*1024,6*1024};
    for(unsigned i=0;i<SLOTS;i++) {
#if CONFIG_SPIRAM && !defined(RMLINK_HOST_TEST)
        if(i==0) {
            uint8_t *storage=heap_caps_malloc(sizes[i],MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
            if(storage) {
                s_records[i]=xRingbufferCreateStatic(sizes[i],RINGBUF_TYPE_NOSPLIT,storage,&s_external_record_control);
                if(!s_records[i])free(storage);
                else ESP_LOGI("rmlink_capture","Timeline ring: %u bytes PSRAM",(unsigned)sizes[i]);
            }
        }
#endif
        if(!s_records[i])s_records[i]=xRingbufferCreate(sizes[i],RINGBUF_TYPE_NOSPLIT);
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
    rmlink_capture_invalidate_ap();
    if(s_transport!=transport || s_session!=session) return;
#ifndef RMLINK_HOST_TEST
    rmlink_clock_trace_clear();
#endif
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
    s->frames++;s_io_frames++; s->index=0; s->started=0; s->seq++;
    if(s->kind==1)s_lower_turn=true;
    if(overrun) {
        s->budget_overrun++;
        if(s->strict_deadlines) {s->state=3;s->reason=6;}
    }
    /* Keep the next ideal start here. run() accounts for missed slots when
     * the next frame actually begins. Rounding up now would add a needless
     * 0..period idle after every already-overdue realtime frame. */
    if(s->limit && s->frames>=s->limit) { s->state=3; s->reason=7; }
}
static bool reuse_timeline(const subscription_t *watch,unsigned field,uint32_t *value) {
    subscription_t *t=&s_sub[0];
    uint64_t now=esp_timer_get_time();
    if(t->state!=2 || !t->frames || t->started || !t->last_timestamp ||
       now<t->last_timestamp || now-t->last_timestamp>watch->period) return false;
    /* Match the logical expression, never only a previously resolved address.
     * A changed root must remain observable on the next Timeline frame. */
    for(unsigned i=0;i<t->count;i++) if(t->address[i]==watch->address[field] &&
        t->depth[i]==watch->depth[field] && t->width[i]==watch->width[field] &&
        !memcmp(t->offset[i],watch->offset[field],t->depth[i]*sizeof(int32_t)) && (t->valid&(1u<<i))) {
        *value=t->values[i]; return true;
    }
    return false;
}
static unsigned cache_hash(uint32_t address) {
    return (((address>>2)^(address>>10))*0x9e3779b1u)>>24;
}
static bool cached_word(subscription_t *s,uint32_t address,uint32_t *value) {
    unsigned at=cache_hash(address);
    while(s->cache_map[at]) {
        unsigned i=s->cache_map[at]-1;
        if(s->cache_address[i]==address) {*value=s->cache_value[i];return true;}
        at=(at+1)&255;
    }
    return false;
}
static void cache_store(subscription_t *s,uint32_t address,uint32_t value) {
    unsigned at=cache_hash(address),i=s->cache_count++;
    while(s->cache_map[at])at=(at+1)&255;
    s->cache_address[i]=address;s->cache_value[i]=value;s->cache_map[at]=i+1;
}
/* Advance every field using this frame's cache. Each unresolved chain contributes
 * only its next dependency. Deduplicate shared roots/suffixes before physical IO.
 * Range checks apply before queueing, so invalid branches cannot poison peers. */
static unsigned resolve_fields(subscription_t *s,uint32_t missing[8]) {
    unsigned count=0;
    for(unsigned i=0;i<s->count;i++) {
        uint32_t bit=1u<<i,a=s->resolve_address[i],value=0;
        if(s->resolved&bit)continue;
        if(s->kind==2 && reuse_timeline(s,i,&value)) {
            s->reused=true;
            if(s_sub[0].last_timestamp<s->timestamp)s->timestamp=s_sub[0].last_timestamp;
            goto valid;
        }
        while(s->resolve_depth[i]<s->depth[i]) {
            unsigned d=s->resolve_depth[i];
            if((a&3) || !memory(a,4,false))goto invalid;
            if(!cached_word(s,a,&value))goto pending;
            int64_t next=(int64_t)value+s->offset[i][d];
            if(next<0 || next>UINT32_MAX)goto invalid;
            s->resolve_address[i]=a=(uint32_t)next;s->resolve_depth[i]++;
        }
        unsigned width=s->width[i];
        if(a%width || !memory(a,width,false))goto invalid;
        {
            uint32_t aligned=a&~3u;
            if(!cached_word(s,aligned,&value)) {a=aligned;goto pending;}
            value>>=(a&3)*8;
            if(width<4)value&=(1u<<(width*8))-1;
        }
valid:
        s->values[i]=value;s->valid|=bit;s->resolved|=bit;continue;
invalid:
        s->failures++;s->resolved|=bit;continue;
pending:;
        unsigned j=0;for(;j<count;j++)if(missing[j]==a)break;
        if(j==count && count<8)missing[count++]=a;
    }
    s->index=__builtin_popcount(s->resolved);
    return count;
}
static bool deep_root_window(subscription_t *s,uint32_t root) {
    if(root<0x20000000u || root>0x20005000u-12 || (root&0x3ff)>0x400-12)return false;
    uint32_t ignored;
    if(cached_word(s,root+4,&ignored) || cached_word(s,root+8,&ignored))return false;
    for(unsigned i=0;i<s->count;i++)if(!(s->resolved&(1u<<i)) && !s->resolve_depth[i] &&
        s->depth[i]>=3 && s->address[i]==root)return true;
    return false;
}
static bool dynamic_step(subscription_t *s) {
    uint32_t missing[8],addresses[8],values[8];unsigned count=resolve_fields(s,missing),planned=0;
    if(!count)return true;
    unsigned remaining=0,extra=0;
    for(unsigned i=0;i<s->count;i++)if(!(s->resolved&(1u<<i)))
        remaining+=s->depth[i]-s->resolve_depth[i]+1;
    /* Plan the entire bounded turn before IO. Deep-root windows and already
     * known ordinary addresses share ONE DAP packet. No dependency value is
     * guessed, and all pointer interpretation waits for the completed reply. */
    if(count>1)for(unsigned a=0;a<count && planned<=5;a++) {
        uint32_t root=missing[a];
        if(s->cache_count+remaining+extra+2>CACHE_WORDS || !deep_root_window(s,root))continue;
        bool overlap=false;
        for(unsigned j=0;j<planned;j++)if(addresses[j]>=root && addresses[j]<=root+8)overlap=true;
        if(overlap)continue;
        for(unsigned j=0;j<3;j++)addresses[planned++]=root+4*j;
        extra+=2;
    }
    unsigned windows=planned;
    for(unsigned a=0;a<count && planned<8;a++) {
        uint32_t address=missing[a];bool included=false;
        for(unsigned j=0;j<planned;j++)if(addresses[j]==address)included=true;
        if(included || (windows && deep_root_window(s,address)))continue;
        addresses[planned++]=address;
    }
    if(!planned || s->cache_count+planned>CACHE_WORDS)return false;
    if(!scattered(addresses,values,planned))return false;
    for(unsigned i=0;i<planned;i++)cache_store(s,addresses[i],values[i]);
    resolve_fields(s,missing);return true;
}
static bool rtt_run(subscription_t *s,unsigned id) {
    /* F103 32-bit RTT descriptor: name,buffer,size,WrOff,RdOff,flags. */
    uint32_t cb=s->address[0],desc=cb+24+s->rtt_channel*24;
    uint8_t header[20],d[16];
    if(s->rtt_phase==0) {
        if(!bytes_read(cb,header,sizeof(header)) || memcmp(header,"SEGGER RTT",10) ||
           u32(header+16)>16 || s->rtt_channel>=u32(header+16))goto fail;
        s->rtt_phase=1;return false;
    }
    if(s->rtt_phase==1) {
        /* Name, flags and down-channel count are not consumed by this reader.
         * Keep signature, up-count, base/size and both offsets freshly read. */
        if(!bytes_read(desc+4,d,sizeof(d)))goto fail;
        uint32_t base=u32(d),size=u32(d+4),wr=u32(d+8),rd=u32(d+12);
        if(size<2 || size>65536 || wr>=size || rd>=size || !memory(base,size,true))goto fail;
        if(s->rtt_pending) {
            if(base!=s->rtt_base || size!=s->rtt_size)goto fail;
            if(rd==s->rtt_pending_rd)goto publish;
            if(rd!=s->rtt_old_rd)goto fail;
            s->rtt_phase=3;return false;
        }
        unsigned available=(wr+size-rd)%size;
        if(!available) {s->rtt_phase=0;return true;}
        if(xRingbufferGetCurFreeSize(s_records[id-1])<RECORD_MAX+16) {s->dropped++;s->rtt_phase=0;return true;}
        s->rtt_bytes=available>64?64:available;s->rtt_old_rd=rd;
        s->rtt_pending_rd=(rd+s->rtt_bytes)%size;s->rtt_base=base;s->rtt_size=size;s->rtt_cursor=0;
        s->rtt_phase=2;return false;
    }
    if(s->rtt_phase==2) {
        /* Yield between bounded payload pieces so a due Timeline or control
         * can run before the rest of this background RTT transaction. */
        unsigned at=(s->rtt_old_rd+s->rtt_cursor)%s->rtt_size;
        unsigned n=s->rtt_bytes-s->rtt_cursor;
        if(n>16)n=16;
        if(n>s->rtt_size-at)n=s->rtt_size-at;
        if(!bytes_read(s->rtt_base+at,s->pending+s->rtt_cursor,n))goto fail;
        s->rtt_cursor+=n;
        if(s->rtt_cursor==s->rtt_bytes) {s->rtt_pending=true;s->rtt_phase=3;}
        return false;
    }
    if(s->rtt_phase!=3)goto fail;
    uint32_t wanted=s->rtt_pending_rd;
    s->rtt_commit_unknown=true;
    if(!word(desc+16,&wanted,true))goto fail;
    s->rtt_commit_unknown=false;
publish:;
    uint8_t payload[80]; w32(payload,s->rtt_channel); w64(payload+4,s->frames);
    w32(payload+12,s->rtt_bytes); memcpy(payload+16,s->pending,s->rtt_bytes);
    emit(s,id,esp_timer_get_time(),0,payload,16+s->rtt_bytes,0,0);
    s->frames+=s->rtt_bytes; s->seq++; s->rtt_pending=false;s->rtt_commit_unknown=false;s->rtt_phase=0;return true;
fail:
    s->failures++; s->state=3; s->reason=3;s->rtt_phase=0;return true;
}
bool rmlink_capture_run(void) {
    if(!s_timer)return false;
    uint64_t now=esp_timer_get_time();
    if(!s_session || s_barrier) return false;
    if(now>s_lease || atomic_load(&s_stream_failed)) {
        for(unsigned i=0;i<SLOTS;i++) if(s_sub[i].state==2) { s_sub[i].state=3; s_sub[i].reason=now>s_lease?4:5; }
        timer_refresh(); return false;
    }
    /* Finish a Timeline frame without interleaving background target reads.
     * Then grant one bounded lower-rate turn. An overloaded realtime series
     * must not permanently starve Watch/RTT; control still wins in swd_task. */
    unsigned order[SLOTS]={0,1,2,3};
    if(s_lower_turn && !s_sub[0].started) {
        for(unsigned i=0;i<SLOTS-1;i++)order[i]=1+(s_lower_cursor-1+i)%(SLOTS-1);
        order[SLOTS-1]=0;
    }
    for(unsigned at=0;at<SLOTS;at++) {
        unsigned k=order[at];
        subscription_t *s=&s_sub[k];
        if(s->state!=2 || (!s->started && now<s->next)) continue;
        if(s_lower_turn) {s_lower_turn=false;if(k)s_lower_cursor=k%(SLOTS-1)+1;}
        if(!s->started) {
            uint64_t missed=(now-s->next)/s->period;
            s->missed+=missed; s->seq+=(uint32_t)missed;
            s->next+=(missed+1)*s->period;
            s->started=now; s->timestamp=now; s->reused=false; s->valid=0; memset(s->values,0,sizeof(s->values));
            s->read_retry=0;
            s->cache_count=0;s->resolved=0;
            memset(s->cache_map,0,sizeof(s->cache_map));
            memcpy(s->resolve_address,s->address,sizeof(s->address));
            memset(s->resolve_depth,0,sizeof(s->resolve_depth));
        }
        if(s->kind==3) { if(rtt_run(s,k+1))s->started=0;timer_refresh();return true; }
        unsigned end=s->index+8; if(end>s->count) end=s->count;
        bool failed=false;
        uint32_t failure_before=u32(s_failure);
        bool retry_pending=s->read_retry==1;
        bool static_group=s->kind==1;
        for(unsigned i=0;i<s->count;i++)if(s->depth[i] || s->width[i]!=4)static_group=false;
        if(static_group) {
            bool contiguous=true;
            for(unsigned i=s->index+1;i<end;i++)if(s->address[i]!=s->address[i-1]+4)contiguous=false;
            unsigned count=end-s->index;
            bool ok=contiguous && (s->address[s->index]&0x3ff)+count*4<=0x400?
                block(s->address[s->index],s->values+s->index,count):
                scattered(s->address+s->index,s->values+s->index,count);
            if(ok) {
                for(;s->index<end;s->index++)s->valid|=1u<<s->index;
            }else { failed=true; }
        }
        if(!static_group && !dynamic_step(s))failed=true;
        if(failed && !s->read_retry && u32(s_failure)!=failure_before &&
           s_failure[21]==2 && !s_failure[22] &&
           (s_failure[20]==1 || s_failure[20]==2) && s_failure[19]<s_failure[18]) {
            /* RAM/Flash reads only. Discard the failed reply, yield to queued
             * control, then rebuild addressed IO with fresh SELECT/CSW/TAR.
             * Never resume a raw posted DRW stream or replay an RTT write. */
            s->read_retry=1;s_read_retry_attempts++;s_last_read_retry=esp_timer_get_time();
            timer_refresh();return true;
        }
        if(retry_pending) {
            if(failed)s_read_retry_exhausted++;else s_read_retry_recovered++;
            s->read_retry=2;
        }
        if(failed)s->failures++;
        if(s->index==s->count || failed) {
            sample_finish(s,k+1);
            if(failed) { s->state=3; s->reason=3; }
        }
        timer_refresh(); return true;
    }
    return false;
}
static void pause_all(void) {
    rmlink_capture_invalidate_ap();
    for(unsigned i=0;i<SLOTS;i++) {
        subscription_t *s=&s_sub[i];
        if(s->started && s->kind!=3) sample_finish(s,i+1);
        s->started=0;s->rtt_phase=0;
    }
    /* Control may change memory or pointer roots before the next Timeline
     * period is due. Its old completed values must not feed Watch afterward. */
    s_sub[0].last_timestamp=0;
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
        w32(out+20,63
#ifndef RMLINK_HOST_TEST
            |64 /* RMT clock trace, owner control only */
#endif
        );
        return 24;
    }
#if !CONFIG_RMLINK_TARGET_PINS_CONFIRMED
    out[2]=2; return 3;
#endif
    if(!rmlink_owner_matches(owner,transport,session)) { out[2]=2; return 3; }
    if(s_session && (s_session!=session || s_transport!=transport)) { out[2]=2; return 3; }
    s_session=session; s_transport=transport; s_lease=esp_timer_get_time()+LEASE_US;
#ifndef RMLINK_HOST_TEST
    if(r[1]==18 || r[1]==19) {
        if(r[1]==18 && !rmlink_capture_raw_allowed()) {out[2]=3;return 3;}
        return rmlink_clock_trace_command(r,len,out,transport==RMLINK_USB?RMLINK_USB_PACKET_SIZE:RMLINK_PACKET_SIZE);
    }
#endif
    if(r[1]==17) {
        /* A single independently addressed read on this same executor. State
         * monitoring must not hold the sampling barrier across Wi-Fi RTTs. */
        uint32_t dhcsr;
        if(!word(0xe000edf0u,&dhcsr,false)) {out[2]=5;return 3;}
        out[2]=0;w32(out+3,dhcsr);return 7;
    }
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
        /* A normal STOP may discard an uncommitted read: RdOff is unchanged,
         * so a new task can consume it once. An attempted write with a lost
         * response remains pending until descriptor reconciliation on resume. */
        if(r[1]==4 && !s->rtt_commit_unknown)s->rtt_pending=false;
        s->started=0;s->rtt_phase=0; s->state=3; s->reason=r[1]==4?2:1; break;
    case 6: /* RESUME */
        if(s->state!=3) return 3;
        s->state=2;s->rtt_phase=0; s->reason=0; s->next=esp_timer_get_time(); atomic_store(&s_stream_failed,false); break;
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
