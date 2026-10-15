/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "rmlink_core.h"

typedef struct {
    bool (*read)(uint32_t address, uint8_t *data, size_t length);
    bool (*write)(uint32_t address, const uint8_t *data, size_t length);
    bool (*sha256)(const uint8_t *data, size_t length, uint8_t digest[32]);
    uint64_t (*now)(void);
    void *(*allocate)(size_t length);
    void (*release)(void *memory);
} rmlink_flash_io_t;
enum { RM_FLASH_IDLE,RM_FLASH_UPLOAD,RM_FLASH_READY,RM_FLASH_PREFLIGHT,
       RM_FLASH_LOAD,RM_FLASH_ERASE,RM_FLASH_PROGRAM,RM_FLASH_PAGE_VERIFY,
       RM_FLASH_VERIFY,RM_FLASH_RESET,RM_FLASH_COMPLETE,RM_FLASH_FAILED,RM_FLASH_CANCELLED };
enum { RM_FLASH_OK,RM_FLASH_ARGUMENT,RM_FLASH_OWNER,RM_FLASH_STATE,RM_FLASH_MEMORY,
       RM_FLASH_CRC,RM_FLASH_HASH,RM_FLASH_TARGET,RM_FLASH_SWD,RM_FLASH_ALGORITHM,
       RM_FLASH_TIMEOUT,RM_FLASH_CANCEL,RM_FLASH_MISMATCH,RM_FLASH_OPTIONS,RM_FLASH_BUSY };
/* All functions, including close and run, belong to the existing SWD executor. */
void rmlink_flash_init(const rmlink_flash_io_t *io);
void rmlink_flash_init_port(void);
size_t rmlink_flash_command(uint8_t transport,uint32_t session,const uint8_t *request,
    size_t length,uint8_t *response,size_t capacity,const rmlink_owner_t *owner,bool may_start);
bool rmlink_flash_busy(void);
bool rmlink_flash_run(void);
void rmlink_flash_closed(uint8_t transport,uint32_t session);
