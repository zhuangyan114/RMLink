/* Fault-injected native test of the production OTA state machine.
 * SHA/flash/bootloader are mocked; real image SHA validation is tested in Python.
 */
#include <stdio.h>
#include <string.h>
#include "ota_stubs.h"
#include "rmlink_ota.h"
static esp_partition_t slot={16,0x300000},running={0,0x300000};
static esp_app_desc_t app={.project_name="rmlink"};
static bool layout=true,fail_end=false;
static unsigned begins,writes,ends,commits,aborts,validations,rollbacks;
static int64_t now;
static uint64_t restart_delay;
static esp_ota_img_states_t boot_state=ESP_OTA_IMG_VALID;
extern const uint8_t rmlink_ota_metadata[64];
const esp_app_desc_t *esp_app_get_description(void){return &app;}
const esp_partition_t *esp_ota_get_next_update_partition(const void *p){(void)p;return &slot;}
const esp_partition_t *esp_ota_get_running_partition(void){return &running;}
const esp_partition_t *esp_partition_find_first(int type,int subtype,const char *name){(void)type;(void)subtype;(void)name;return layout?&slot:NULL;}
esp_err_t esp_ota_get_state_partition(const esp_partition_t *p,esp_ota_img_states_t *state){(void)p;*state=boot_state;return 0;}
esp_err_t esp_ota_begin(const esp_partition_t *p,size_t size,esp_ota_handle_t *h){assert(p==&slot);(void)size;*h=1;begins++;return 0;}
esp_err_t esp_ota_write(esp_ota_handle_t h,const void *p,size_t n){assert(h==1);(void)p;writes+=(unsigned)n;return 0;}
esp_err_t esp_ota_end(esp_ota_handle_t h){assert(h==1);ends++;return fail_end?-1:0;}
esp_err_t esp_ota_abort(esp_ota_handle_t h){assert(h==1);aborts++;return 0;}
esp_err_t esp_ota_set_boot_partition(const esp_partition_t *p){assert(p==&slot);commits++;return 0;}
esp_err_t esp_ota_mark_app_valid_cancel_rollback(void){validations++;return 0;}
esp_err_t esp_ota_mark_app_invalid_rollback_and_reboot(void){rollbacks++;return 0;}
int64_t esp_timer_get_time(void){return now;}
esp_err_t esp_timer_create(const esp_timer_create_args_t *a,esp_timer_handle_t *h){assert(a->callback);*h=(void *)1;return 0;}
esp_err_t esp_timer_start_once(esp_timer_handle_t h,uint64_t delay){assert(h);restart_delay=delay;return 0;}
void esp_restart(void){assert(!"The test must not execute a hardware reset");}
void mbedtls_sha256_init(mbedtls_sha256_context *c){memset(c,0,sizeof(*c));}
void mbedtls_sha256_free(mbedtls_sha256_context *c){(void)c;}
int mbedtls_sha256_starts(mbedtls_sha256_context *c,int n){(void)n;memset(c,0,sizeof(*c));return 0;}
int mbedtls_sha256_update(mbedtls_sha256_context *c,const uint8_t *p,size_t n){for(size_t i=0;i<n;i++)c->hash[c->count++%32]^=p[i];return 0;}
int mbedtls_sha256_finish(mbedtls_sha256_context *c,uint8_t *out){memcpy(out,c->hash,32);return 0;}
int mbedtls_sha256(const uint8_t *p,size_t n,uint8_t *out,int mode){mbedtls_sha256_context c;mbedtls_sha256_starts(&c,mode);mbedtls_sha256_update(&c,p,n);return mbedtls_sha256_finish(&c,out);}
static uint8_t image[416],out[64];
static rmlink_owner_t owner;
static uint8_t command(const uint8_t *r,size_t n,uint8_t transport,uint32_t session)
{memset(out,0,sizeof(out));rmlink_ota_command(transport,session,r,n,out,&owner,true);return out[2];}
static uint8_t simple(unsigned sub){uint8_t r[]={0x83,(uint8_t)sub};return command(r,2,2,123);}
static uint8_t begin(bool bad_hash)
{
    uint8_t r[38]={0x83,1};uint32_t size=sizeof(image);memcpy(r+2,&size,4);
    mbedtls_sha256(image,sizeof(image),r+6,0);if(bad_hash)r[6]^=1;
    return command(r,sizeof(r),2,123);
}
static uint8_t upload_chunked(unsigned limit)
{
    for(uint32_t offset=0;offset<sizeof(image);) {
        uint8_t r[512]={0x83,2};unsigned count=sizeof(image)-offset;if(count>limit)count=limit;
        memcpy(r+2,&offset,4);r[6]=(uint8_t)count;r[7]=(uint8_t)(count>>8);memcpy(r+8,image+offset,count);
        uint8_t result=command(r,8+count,2,123);if(result)return result;
        offset+=count;assert(!memcmp(out+3,&offset,4));
    }
    return 0;
}
static uint8_t upload(void){return upload_chunked(56);}
int main(void)
{
    _Static_assert(sizeof(esp_image_header_t)==24,"header");
    _Static_assert(sizeof(esp_app_desc_t)==256,"descriptor");
    image[0]=0xe9;image[12]=9;uint32_t magic=ESP_APP_DESC_MAGIC_WORD;memcpy(image+32,&magic,4);
    memcpy(image+80,"rmlink",7);memcpy(image+288,rmlink_ota_metadata,64);
    assert(simple(0)==0 && out[4]==3);
    layout=false;assert(begin(false)==2);layout=true;
    owner.session=9;assert(begin(false)==3);owner.session=0;
    assert(begin(false)==0);uint8_t bad_offset[9]={0x83,2,1,0,0,0,1,0,0};
    assert(command(bad_offset,sizeof(bad_offset),2,123)==4);
    assert(command(bad_offset,sizeof(bad_offset),1,123)==3);
    assert(simple(5)==0 && begins==0);
    image[288]^=1;assert(begin(false)==0);assert(upload()==7 && begins==0);image[288]^=1;
    assert(begin(true)==0 && upload()==0);assert(simple(3)==5 && commits==0 && ends==0);
    fail_end=true;assert(begin(false)==0 && upload()==0);assert(simple(3)==5 && commits==0);fail_end=false;
    assert(begin(false)==0);rmlink_ota_closed(2,124);assert(rmlink_ota_busy());rmlink_ota_closed(2,123);assert(!rmlink_ota_busy());
    assert(begin(false)==0);now+=121000000;assert(!rmlink_ota_busy());
    boot_state=ESP_OTA_IMG_PENDING_VERIFY;rmlink_ota_confirm_boot(true);rmlink_ota_confirm_boot(false);
    assert(validations==1 && rollbacks==1);
    assert(begin(false)==0 && upload_chunked(504)==0 && simple(3)==0 && commits==0);
    assert(simple(4)==0 && commits==1 && restart_delay==1000000);
    assert(simple(4)==4 && commits==1);rmlink_ota_closed(2,123);assert(rmlink_ota_busy());
    printf("OTA native fault tests passed; inactive writes=%u bytes, aborts=%u, commits=%u\n",writes,aborts,commits);
    return 0;
}
