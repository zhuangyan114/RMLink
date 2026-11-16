/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Nonblocking writes, bounded waits and one deadline for the entire batch.
 * The callbacks keep the exact production loop testable without ESP hardware. */
typedef struct {
    void *context;
    int64_t (*now)(void *);
    bool (*live)(void *);
    int (*write)(void *,const uint8_t *,size_t); /* 0: retry, -1: fatal */
    bool (*wait)(void *,int64_t remaining_us);
} rmlink_stream_io_t;
typedef enum { RMLINK_SEND_OK, RMLINK_SEND_TIMEOUT, RMLINK_SEND_CANCELLED, RMLINK_SEND_ERROR } rmlink_send_result_t;

/* Let the 1500 ms base TCP retransmission complete before replacing a live
 * connection. A newly bound stream gets 5 s to settle, then batches get 2.5 s. */
static inline int64_t rmlink_stream_realtime_budget(int64_t started_us,int64_t now_us) {
    int64_t grace=started_us+5000000-now_us;
    return grace>2500000?grace:2500000;
}

static inline rmlink_send_result_t rmlink_stream_write_deadline(const rmlink_stream_io_t *io,
        const uint8_t *data,size_t size,int64_t budget_us) {
    int64_t deadline=io->now(io->context)+budget_us;
    while(size) {
        if(!io->live(io->context))return RMLINK_SEND_CANCELLED;
        if(io->now(io->context)>=deadline)return RMLINK_SEND_TIMEOUT;
        int sent=io->write(io->context,data,size);
        if(sent<0 || (size_t)sent>size)return RMLINK_SEND_ERROR;
        if(sent) { data+=sent;size-=sent;continue; }
        int64_t left=deadline-io->now(io->context);
        if(left<=0)return RMLINK_SEND_TIMEOUT;
        if(!io->wait(io->context,left))return RMLINK_SEND_ERROR;
    }
    return io->now(io->context)>deadline?RMLINK_SEND_TIMEOUT:RMLINK_SEND_OK;
}
