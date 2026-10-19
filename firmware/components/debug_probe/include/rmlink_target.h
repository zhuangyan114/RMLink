/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef struct {uint32_t address,size;} rmlink_memory_region_t;
typedef struct {
    uint8_t id,ram_count,sector_count,option_count;
    uint32_t core_part,dp_id,id_address,device_id,size_address,flash_base,flash_size;
    const rmlink_memory_region_t *ram,*sectors;
    const uint32_t *options;
    uint32_t base,buffer,stack,stack_size,page,static_base,erase_timeout_ms,program_timeout_ms;
    uint32_t init,uninit,erase,program,verify,bkpt,erase_chip;
    const uint8_t *code;uint32_t code_size;
} rmlink_target_profile_t;
const rmlink_target_profile_t *rmlink_target_current(void);
const rmlink_target_profile_t *rmlink_target_find(uint8_t id);
uint32_t rmlink_target_pack_tag(void);
bool rmlink_target_select(const rmlink_target_profile_t *profile);
bool rmlink_target_locked(void);
void rmlink_target_lock(bool locked);
void rmlink_target_reset(void);
bool rmlink_target_memory(uint32_t address,size_t length,bool ram_only);
bool rmlink_target_flash_range(uint32_t address,uint32_t length);
uint32_t rmlink_target_sector_size(uint32_t address);
