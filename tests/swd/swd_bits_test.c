/* Pin-level differential test of the real generic and specialized SWD engine. */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "DAP.h"
host_dap_data_t DAP_Data;
static unsigned clock_level,output_level,driving,input_at,input_count,edge_count;
static uint8_t inputs[40],edges[256];
void host_clock(unsigned level) {
    if(level && !clock_level) {
        assert(edge_count<sizeof(edges));
        edges[edge_count++]=(driving?2U:0U)|output_level;
    }
    clock_level=level;
}
void host_output(unsigned value) {output_level=value;}
void host_direction(unsigned enabled) {driving=enabled;}
unsigned host_input(void) {
    assert(!driving && input_at<input_count);
    return inputs[input_at++];
}
#include "../../firmware/components/debug_probe/DAP/Source/SW_DP.c"

typedef struct {uint8_t ack;uint32_t value,timestamp;unsigned count;uint8_t trace[256];} result_t;
static unsigned parity32(uint32_t value) {
    unsigned parity=0;for(unsigned i=0;i<32;i++)parity^=(value>>i)&1U;return parity;
}
static result_t execute(bool specialized,uint32_t request,uint32_t value,unsigned ack,
                        unsigned turnaround,unsigned phase,unsigned idle,bool bad_parity) {
    clock_level=output_level=driving=1;input_at=input_count=edge_count=0;
    for(unsigned i=0;i<3;i++)inputs[input_count++]=(ack>>i)&1U;
    if(ack==1 && (request&2)) {
        for(unsigned i=0;i<32;i++)inputs[input_count++]=(value>>i)&1U;
        inputs[input_count++]=parity32(value)^bad_parity;
    }
    DAP_Data=(host_dap_data_t){.clock_delay=7,.swd_conf={turnaround,phase},.transfer={idle}};
    SWD_SetClock(specialized?4000000U:2000000U);
    uint32_t data=value;
    result_t r={.ack=SWD_Transfer(request,&data),.value=data,.timestamp=DAP_Data.timestamp};
    assert(input_at==input_count && driving);
    r.count=edge_count;memcpy(r.trace,edges,edge_count);
    unsigned expected=11+2*turnaround;
    if(ack==1)expected+=33+idle;
    else if(ack==2 || ack==4)expected+=phase?33:0;
    else expected+=33;
    assert(r.count==expected);
    uint32_t req_byte=1|((request&15)<<1)|(parity32(request&15)<<5)|(1U<<7);
    for(unsigned i=0;i<8;i++)assert(r.trace[i]==(2U|((req_byte>>i)&1U)));
    if(ack==1 && !(request&2)) {
        unsigned start=11+2*turnaround;
        for(unsigned i=0;i<32;i++)assert(r.trace[start+i]==(2U|((value>>i)&1U)));
        assert(r.trace[start+32]==(2U|parity32(value)));
    }
    assert(r.ack==(ack==1 && (request&2) && bad_parity?8:ack));
    assert(r.value==value);
    return r;
}
int main(void) {
    uint32_t seed=0x31415926;unsigned comparisons=0;
    for(unsigned trial=0;trial<4096;trial++) {
        seed=seed*1664525U+1013904223U;
        for(unsigned ack_index=0;ack_index<6;ack_index++) {
            const unsigned acks[]={1,2,4,0,3,7};unsigned ack=acks[ack_index];
            uint32_t req=(trial&15)|((trial&16)?0x80:0);
            unsigned turn=1+(trial%4),phase=(trial>>3)&1,idle=(trial>>5)%5;
            bool bad=(trial&32)!=0;
            result_t generic=execute(false,req,seed,ack,turn,phase,idle,bad);
            result_t specialized=execute(true,req,seed,ack,turn,phase,idle,bad);
            assert(generic.ack==specialized.ack && generic.value==specialized.value);
            assert(generic.timestamp==specialized.timestamp && generic.count==specialized.count);
            assert(!memcmp(generic.trace,specialized.trace,generic.count));comparisons++;
        }
    }
    printf("PASS: %u SWD pin traces, read/write data, parity errors, WAIT/FAULT data phase, turnaround, idle and timestamp\n",comparisons);
    return 0;
}
