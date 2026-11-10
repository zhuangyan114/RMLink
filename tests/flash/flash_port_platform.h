#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_INTERNAL 2
#define MALLOC_CAP_8BIT 4
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
int64_t esp_timer_get_time(void);
uint32_t DAP_GetSWJClock(void);
uint8_t DAP_GetTransferAbortReason(void);
void DAP_TransferDeadline(uint32_t us);
uint32_t DAP_ProcessCommand(const uint8_t *request,uint8_t *response);
int mbedtls_sha256(const uint8_t *data,size_t length,uint8_t digest[32],int is224);
static inline void *heap_caps_malloc(size_t n,int caps) {(void)caps;return malloc(n);}
static inline size_t heap_caps_get_free_size(int caps) {(void)caps;return 0;}
static inline size_t heap_caps_get_largest_free_block(int caps) {(void)caps;return 0;}
