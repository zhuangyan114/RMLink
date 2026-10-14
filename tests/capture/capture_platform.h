/* SPDX-License-Identifier: Apache-2.0
 * Host adapters for running the actual capture state machine, no device I/O.
 */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define CONFIG_DEBUG_PROBE_IFACE_SWD 1
#define CONFIG_RMLINK_TARGET_PINS_CONFIRMED 1
#define ESP_OK 0
#define pdTRUE 1
#define ESP_ERR_NO_MEM -1
#define RINGBUF_TYPE_NOSPLIT 0
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
#define pdMS_TO_TICKS(n) (n)
static inline void vTaskDelay(unsigned ticks) { (void)ticks; }
typedef int portMUX_TYPE;
typedef struct { bool active; } timer_stub_t;
typedef timer_stub_t *esp_timer_handle_t;
typedef struct { void (*callback)(void*); const char *name; bool skip_unhandled_events; } esp_timer_create_args_t;
extern uint64_t host_clock;
static inline int64_t esp_timer_get_time(void) { return host_clock; }
static inline int esp_timer_create(const esp_timer_create_args_t *args,esp_timer_handle_t *out) { (void)args; *out=calloc(1,sizeof(**out)); return *out?0:-1; }
static inline bool esp_timer_is_active(esp_timer_handle_t t) { return t->active; }
static inline void esp_timer_start_periodic(esp_timer_handle_t t,unsigned n) { (void)n; t->active=true; }
static inline void esp_timer_stop(esp_timer_handle_t t) { t->active=false; }
static inline void esp_fill_random(void *out,size_t n) { static uint8_t seed; memset(out,++seed,n); }
typedef struct node { struct node *next; size_t len; uint8_t data[]; } node_t;
typedef struct { node_t *first,*last; size_t bytes,capacity; } ring_stub_t;
typedef ring_stub_t *RingbufHandle_t;
static inline RingbufHandle_t xRingbufferCreate(size_t n,int mode) { (void)mode; RingbufHandle_t p=calloc(1,sizeof(*p)); p->capacity=n; return p; }
static inline int xRingbufferSend(RingbufHandle_t r,const void *data,size_t n,unsigned ticks) {
    (void)ticks; if(r->bytes+n>r->capacity) return 0;
    node_t *p=malloc(sizeof(*p)+n); p->next=NULL; p->len=n; memcpy(p->data,data,n);
    if(r->last) r->last->next=p; else r->first=p; r->last=p; r->bytes+=n; return 1;
}
static inline void *xRingbufferReceive(RingbufHandle_t r,size_t *n,unsigned ticks) {
    (void)ticks; node_t *p=r->first; if(!p) return NULL;
    r->first=p->next; if(!r->first) r->last=NULL; r->bytes-=p->len; *n=p->len; return p->data;
}
static inline void vRingbufferReturnItem(RingbufHandle_t r,void *data) { (void)r; free((uint8_t*)data-sizeof(node_t)); }
static inline size_t xRingbufferGetCurFreeSize(RingbufHandle_t r) { return r->capacity-r->bytes; }
static inline void DAP_TransferDeadline(uint32_t us) { (void)us; }
uint32_t DAP_ProcessCommand(const uint8_t *request,uint8_t *response);
