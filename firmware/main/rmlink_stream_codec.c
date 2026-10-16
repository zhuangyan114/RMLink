/* SPDX-License-Identifier: Apache-2.0 */
#include "rmlink_stream_codec.h"
#include <string.h>

static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
}
static void put32(uint8_t *p,uint32_t n) {
    for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(n>>(8*i));
}
static int validate(const uint8_t *data,size_t length) {
    if(!length || length>4096)return 0;
    /* Validate the whole input before writing any bytes. The producer already
     * computed each payload CRC; preserve it verbatim instead of recomputing. */
    size_t read=0;
    while(read<length) {
        const uint8_t *p=data+read;
        if(length-read<56 || memcmp(p,"RML1",4) || p[4]!=1 || p[5]<1 || p[5]>3 ||
           !p[6] || p[6]>4 || p[7] || p[24]!=1 || p[25] || p[26]!=32 || p[27])return 0;
        uint32_t n=get32(p+20);
        if(n<24 || n>352 || n+32>length-read || get32(p+16)!=get32(p+32) ||
           n!=24+(unsigned)p[54]+((unsigned)p[55]<<8))return 0;
        read+=32+n;
    }
    return 1;
}
size_t rmlink_stream_pack(uint8_t *data,size_t length) {
    if(!validate(data,length))return 0;
    size_t read=0,written=0;
    while(read<length) {
        uint8_t kind=data[read+5],slot=data[read+6];
        uint32_t capture=get32(data+read+8),revision=get32(data+read+12);
        size_t group=written;written+=20;unsigned count=0;
        do {
            uint32_t n=get32(data+read+20),crc=get32(data+read+28);
            /* Destinations remain behind unread source, including the first
             * member (20-byte group header versus 32-byte original header). */
            put32(data+written,crc);
            memmove(data+written+4,data+read+32,n);
            written+=4+n;read+=32+n;count++;
        } while(read<length && data[read+5]==kind && data[read+6]==slot &&
                get32(data+read+8)==capture && get32(data+read+12)==revision);
        memcpy(data+group,"RMC2",4);data[group+4]=kind;data[group+5]=slot;
        data[group+6]=(uint8_t)count;data[group+7]=(uint8_t)(count>>8);
        put32(data+group+8,capture);put32(data+group+12,revision);
        put32(data+group+16,(uint32_t)(written-group-20));
    }
    return written;
}

size_t rmlink_stream_pack_delta(uint8_t *data,size_t length) {
    if(!validate(data,length))return 0;
    size_t read=0,written=0;
    uint8_t previous[352],delta[352],encoded[704];
    while(read<length) {
        uint8_t kind=data[read+5],slot=data[read+6];
        uint32_t capture=get32(data+read+8),revision=get32(data+read+12);
        size_t group=written,previous_length=0;written+=20;unsigned count=0;
        do {
            size_t n=get32(data+read+20),packed=0,pos=0;
            uint32_t crc=get32(data+read+28);
            const uint8_t *payload=data+read+32;
            for(size_t i=0;i<n;i++)delta[i]=payload[i]^(i<previous_length?previous[i]:0);
            while(pos<n) {
                size_t start=pos;
                if(!delta[pos]) {
                    while(pos<n && !delta[pos] && pos-start<128)pos++;
                    encoded[packed++]=(uint8_t)(0x80|(pos-start-1));
                } else {
                    while(pos<n && delta[pos] && pos-start<128)pos++;
                    encoded[packed++]=(uint8_t)(pos-start-1);
                    memcpy(encoded+packed,delta+start,pos-start);packed+=pos-start;
                }
            }
            /* Capture the original before the in-place destination moves it.
             * Even raw records save 24 B versus RML1, so unread input is safe. */
            memcpy(previous,payload,n);previous_length=n;
            size_t stored=packed<n?packed:n;
            put32(data+written,crc);
            data[written+4]=(uint8_t)n;data[written+5]=(uint8_t)(n>>8);
            data[written+6]=packed<n?(uint8_t)packed:0;
            data[written+7]=packed<n?(uint8_t)(packed>>8):0;
            if(packed<n)memcpy(data+written+8,encoded,stored);
            else memmove(data+written+8,payload,stored);
            written+=8+stored;read+=32+n;count++;
        } while(read<length && data[read+5]==kind && data[read+6]==slot &&
                get32(data+read+8)==capture && get32(data+read+12)==revision);
        memcpy(data+group,"RMC3",4);data[group+4]=kind;data[group+5]=slot;
        data[group+6]=(uint8_t)count;data[group+7]=(uint8_t)(count>>8);
        put32(data+group+8,capture);put32(data+group+12,revision);
        put32(data+group+16,(uint32_t)(written-group-20));
    }
    return written;
}
