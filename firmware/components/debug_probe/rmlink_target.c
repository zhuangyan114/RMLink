/* SPDX-License-Identifier: Apache-2.0
 * Generated profile data and target RAM algorithms share one package with Orbit.
 * Selection and all access happen on the existing SWD executor only.
 */
#include "rmlink_target.h"
#include "rmlink_target_pack_data.inc"
static const rmlink_target_profile_t *selected=&rmlink_targets[0];
static bool locked;
const rmlink_target_profile_t *rmlink_target_current(void) {return selected;}
uint32_t rmlink_target_pack_tag(void) {return RMLINK_TARGET_PACK_TAG;}
const rmlink_target_profile_t *rmlink_target_find(uint8_t id) {
    for(size_t i=0;i<sizeof(rmlink_targets)/sizeof(rmlink_targets[0]);i++)if(rmlink_targets[i].id==id)return &rmlink_targets[i];
    return NULL;
}
bool rmlink_target_select(const rmlink_target_profile_t *profile) {if(locked || !profile)return false;selected=profile;return true;}
bool rmlink_target_locked(void) {return locked;}
void rmlink_target_lock(bool value) {locked=value;}
void rmlink_target_reset(void) {locked=false;selected=&rmlink_targets[0];}
bool rmlink_target_memory(uint32_t a,size_t n,bool ram_only) {
    if(!n || n>UINT32_MAX || a>UINT32_MAX-n)return false;
    for(unsigned i=0;i<selected->ram_count;i++)
        if(a>=selected->ram[i].address && (uint64_t)a+n<=(uint64_t)selected->ram[i].address+selected->ram[i].size)return true;
    return !ram_only && a>=selected->flash_base && (uint64_t)a+n<=(uint64_t)selected->flash_base+selected->flash_size;
}
uint32_t rmlink_target_sector_size(uint32_t a) {
    for(unsigned i=0;i<selected->sector_count;i++)if(selected->sectors[i].address==a)return selected->sectors[i].size;
    return 0;
}
bool rmlink_target_flash_range(uint32_t a,uint32_t n) {
    if(!n || a<selected->flash_base || (uint64_t)a+n>(uint64_t)selected->flash_base+selected->flash_size)return false;
    uint32_t end=a+n;
    while(a<end) {uint32_t size=rmlink_target_sector_size(a);if(!size || size>end-a)return false;a+=size;}
    return a==end;
}
