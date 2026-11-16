/* Independent timing/error scenarios for the production realtime send loop. */
#include <assert.h>
#include <stdio.h>
#include "../../firmware/main/rmlink_stream_deadline.h"

typedef struct {int64_t time,available_at;int calls,waits,total;int step;bool alive,fatal,wait_error;} model_t;
static int64_t now(void *p) {return ((model_t*)p)->time;}
static bool live(void *p) {return ((model_t*)p)->alive;}
static int write_bytes(void *p,const uint8_t *data,size_t size) {
    model_t *m=p;(void)data;m->calls++;
    if(m->fatal)return -1;
    if(m->time<m->available_at)return 0;
    int sent=m->step;if(sent>(int)size)sent=(int)size;
    if(sent){m->time+=200000;m->total+=sent;}
    return sent;
}
static bool wait_ready(void *p,int64_t left) {
    model_t *m=p;m->waits++;
    assert(left>0 && left<=5000000);
    m->time+=left<100000?left:100000;
    return !m->wait_error;
}
int main(void) {
    uint8_t bytes[64]={0};
    model_t m={.alive=true};
    rmlink_stream_io_t io={&m,now,live,write_bytes,wait_ready};
    assert(rmlink_stream_write_deadline(&io,bytes,64,1000000)==RMLINK_SEND_TIMEOUT);
    assert(m.time==1000000 && m.total==0 && m.waits==10);
    m=(model_t){.alive=true,.step=1};
    assert(rmlink_stream_write_deadline(&io,bytes,64,1000000)==RMLINK_SEND_TIMEOUT);
    assert(m.time==1000000 && m.total==5); /* partial progress never resets the batch deadline */
    m=(model_t){.alive=true,.step=64};
    assert(rmlink_stream_write_deadline(&io,bytes,64,1000000)==RMLINK_SEND_OK);
    assert(m.total==64 && m.waits==0);
    m=(model_t){.alive=false,.step=64};
    assert(rmlink_stream_write_deadline(&io,bytes,64,1000000)==RMLINK_SEND_CANCELLED);
    assert(m.calls==0);
    m=(model_t){.alive=true,.fatal=true};
    assert(rmlink_stream_write_deadline(&io,bytes,64,1000000)==RMLINK_SEND_ERROR);
    assert(m.calls==1 && m.waits==0);
    m=(model_t){.alive=true,.wait_error=true};
    assert(rmlink_stream_write_deadline(&io,bytes,64,1000000)==RMLINK_SEND_ERROR);
    assert(m.waits==1);
    m=(model_t){.alive=true,.step=64};
    assert(rmlink_stream_write_deadline(&io,bytes,64,0)==RMLINK_SEND_TIMEOUT);
    assert(m.calls==0);
    assert(rmlink_stream_write_deadline(&io,bytes,0,1000000)==RMLINK_SEND_OK);
    m=(model_t){.alive=true,.step=64};
    assert(rmlink_stream_write_deadline(&io,bytes,64,100000)==RMLINK_SEND_TIMEOUT);
    m=(model_t){.alive=true,.step=64,.available_at=1500000};
    assert(rmlink_stream_write_deadline(&io,bytes,64,rmlink_stream_realtime_budget(-10000000,0))==RMLINK_SEND_OK);
    assert(m.time==1700000 && m.total==64); /* one real TCP RTO can complete */
    m=(model_t){.alive=true,.step=64,.available_at=3500000};
    assert(rmlink_stream_write_deadline(&io,bytes,64,rmlink_stream_realtime_budget(0,0))==RMLINK_SEND_OK);
    assert(m.total==64 && m.time==3700000); /* newly bound connection gets time to settle */
    m=(model_t){.alive=true};
    assert(rmlink_stream_write_deadline(&io,bytes,64,rmlink_stream_realtime_budget(-10000000,0))==RMLINK_SEND_TIMEOUT);
    assert(m.time==2500000); /* established stalls still have a bounded cutoff */
    puts("stream deadline: 12 scenarios passed");return 0;
}
