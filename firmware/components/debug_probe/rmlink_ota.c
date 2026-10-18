/* SPDX-License-Identifier: Apache-2.0
 * Uses Espressif ESP-IDF v5.1.2 app_update / second-stage bootloader APIs.
 * See examples/system/ota/native_ota_example in espressif/esp-idf (CC0).
 * Only the inactive application slot is written. Factory/NVS are never erased.
 */
#include <string.h>
#include "sdkconfig.h"
#include "esp_ota_ops.h"
#include "esp_app_format.h"
#include "esp_app_desc.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "mbedtls/sha256.h"
#include "rmlink_ota.h"

#ifndef CONFIG_SPIRAM
#define CONFIG_SPIRAM 0
#endif
#ifndef CONFIG_RMLINK_TARGET_PINS_CONFIRMED
#define CONFIG_RMLINK_TARGET_PINS_CONFIRMED 0
#endif
#ifndef CONFIG_RMLINK_TARGET_UART_ENABLED
#define CONFIG_RMLINK_TARGET_UART_ENABLED 0
#endif
#ifndef CONFIG_RMLINK_MSC_ENABLED
#define CONFIG_RMLINK_MSC_ENABLED 0
#endif

typedef struct {
    char magic[8];
    uint32_t schema, layout, flags;
    int32_t swdio, swclk, nrst;
    uint8_t reserved[32];
} ota_metadata_t;
_Static_assert(sizeof(ota_metadata_t) == 64, "OTA metadata wire layout");
/* IDF places this immediately after esp_app_desc_t, at file offset 288. */
const __attribute__((section(".rodata_custom_desc"), used)) ota_metadata_t rmlink_ota_metadata = {
    .magic = "RMLKOTA1", .schema = 1, .layout = 1,
    .flags = CONFIG_RMLINK_TARGET_PINS_CONFIRMED | (CONFIG_SPIRAM << 1) |
        (CONFIG_RMLINK_TARGET_UART_ENABLED << 2) | (CONFIG_RMLINK_MSC_ENABLED << 3),
    .swdio = CONFIG_DEBUG_PROBE_GPIO_TMS, .swclk = CONFIG_DEBUG_PROBE_GPIO_TCK,
    .nrst = CONFIG_RMLINK_NRST_GPIO,
};
enum { IDLE, RECEIVING, READY, COMMITTED };
enum { OTA_OK, ARGUMENT, UNSUPPORTED, OTA_BUSY, SEQUENCE, IMAGE, FLASH, PROFILE };
#define PREFIX_SIZE (sizeof(esp_image_header_t) + sizeof(esp_image_segment_header_t) + sizeof(esp_app_desc_t) + sizeof(ota_metadata_t))
static struct {
    uint8_t state, transport;
    uint32_t session, size, received;
    int64_t touched;
    uint8_t digest[32], prefix[PREFIX_SIZE];
    mbedtls_sha256_context sha;
    esp_ota_handle_t handle;
    const esp_partition_t *partition;
} s;
static esp_timer_handle_t s_restart;

static uint32_t u32(const uint8_t *p)
{ return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24; }
static void put32(uint8_t *p, uint32_t v)
{ for (unsigned i=0;i<4;i++) p[i]=(uint8_t)(v>>(8*i)); }
static void clear(void)
{
    if (s.handle) esp_ota_abort(s.handle);
    if (s.state) mbedtls_sha256_free(&s.sha);
    memset(&s, 0, sizeof(s));
}
bool rmlink_ota_busy(void)
{
    /* Called only on the serialized executor, never from a timer callback. */
    if (s.state && s.state != COMMITTED && esp_timer_get_time()-s.touched > INT64_C(120000000)) clear();
    return s.state != IDLE;
}
void rmlink_ota_closed(uint8_t transport, uint32_t session)
{
    if (s.state != COMMITTED && s.transport == transport && s.session == session) clear();
}
static bool layout_ready(void)
{
    return esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_OTA, NULL) &&
        esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_0, NULL) &&
        esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_1, NULL);
}
static void restart(void *unused) { (void)unused; esp_restart(); }
void rmlink_ota_confirm_boot(bool healthy)
{
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(esp_ota_get_running_partition(), &state) == ESP_OK && state == ESP_OTA_IMG_PENDING_VERIFY) {
        if (healthy) ESP_ERROR_CHECK(esp_ota_mark_app_valid_cancel_rollback());
        else ESP_ERROR_CHECK(esp_ota_mark_app_invalid_rollback_and_reboot());
    }
}
static bool prefix_valid(void)
{
    const esp_image_header_t *header = (const void *)s.prefix;
    const esp_app_desc_t *app = (const void *)(s.prefix + sizeof(esp_image_header_t) + sizeof(esp_image_segment_header_t));
    return header->magic == ESP_IMAGE_HEADER_MAGIC && header->chip_id == ESP_CHIP_ID_ESP32S3 &&
        app->magic_word == ESP_APP_DESC_MAGIC_WORD &&
        !memcmp(app->project_name, esp_app_get_description()->project_name, sizeof(app->project_name)) &&
        !memcmp(s.prefix + PREFIX_SIZE - sizeof(rmlink_ota_metadata), &rmlink_ota_metadata, sizeof(rmlink_ota_metadata));
}
size_t rmlink_ota_command(uint8_t transport, uint32_t session, const uint8_t *r,
    size_t len, uint8_t *out, const rmlink_owner_t *owner, bool target_idle)
{
    out[0]=0x83; out[1]=len>1?r[1]:0xff; out[2]=ARGUMENT;
    rmlink_ota_busy();
    if (len<2) return 3;
    if (r[1]==0) {
        const esp_partition_t *next=esp_ota_get_next_update_partition(NULL);
        const esp_partition_t *running=esp_ota_get_running_partition();
        uint8_t hash[32];
        if (mbedtls_sha256((const void *)&rmlink_ota_metadata,sizeof(rmlink_ota_metadata),hash,0)) {out[2]=FLASH;return 3;}
        esp_ota_img_states_t app_state;
        out[2]=OTA_OK; out[3]=1; out[4]=layout_ready()?1:0;
#if CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
        out[4]|=2;
#endif
        out[5]=s.state;
        put32(out+6,next?next->size:0); put32(out+10,s.received); put32(out+14,s.size);
        out[18]=RMLINK_VERSION_MAJOR; out[19]=RMLINK_VERSION_MINOR; out[20]=RMLINK_VERSION_PATCH;
        memcpy(out+21,esp_app_get_description()->app_elf_sha256,32); memcpy(out+53,hash,8);
        out[61]=esp_ota_get_state_partition(running,&app_state)==ESP_OK?(uint8_t)app_state:255;
        out[62]=running?running->subtype:255;
        return 63;
    }
    if (r[1]==1) {
        if (len<38 || !session) return 3;
        if (!layout_ready()) {out[2]=UNSUPPORTED;return 3;}
        if (s.state || owner->session || !target_idle) {out[2]=OTA_BUSY;return 3;}
        const esp_partition_t *next=esp_ota_get_next_update_partition(NULL);
        uint32_t size=u32(r+2);
        if (!next || next==esp_ota_get_running_partition() || size<PREFIX_SIZE || size>next->size) return 3;
        s.state=RECEIVING; s.transport=transport; s.session=session; s.size=size; s.partition=next;
        memcpy(s.digest,r+6,32); s.touched=esp_timer_get_time();
        mbedtls_sha256_init(&s.sha);
        if (mbedtls_sha256_starts(&s.sha,0)) {clear();out[2]=FLASH;return 3;}
        out[2]=OTA_OK;return 3;
    }
    if (!s.state || s.transport!=transport || s.session!=session) {out[2]=OTA_BUSY;return 3;}
    s.touched=esp_timer_get_time();
    if (r[1]==5 && s.state!=COMMITTED) {clear();out[2]=OTA_OK;return 3;}
    if (r[1]==2 && s.state==RECEIVING) {
        if (len<8) return 3;
        uint32_t offset=u32(r+2); size_t count=(size_t)r[6]|(size_t)r[7]<<8;
        if (!count || count>len-8 || offset!=s.received || count>s.size-s.received) {out[2]=SEQUENCE;return 3;}
        const uint8_t *data=r+8; size_t used=0;
        if (s.received<PREFIX_SIZE) {
            used=PREFIX_SIZE-s.received; if (used>count) used=count;
            memcpy(s.prefix+s.received,data,used);
            if (s.received+used==PREFIX_SIZE) {
                if (!prefix_valid()) {clear();out[2]=PROFILE;return 3;}
                if (esp_ota_begin(s.partition,OTA_WITH_SEQUENTIAL_WRITES,&s.handle)!=ESP_OK ||
                    esp_ota_write(s.handle,s.prefix,PREFIX_SIZE)!=ESP_OK) {clear();out[2]=FLASH;return 3;}
            }
        }
        if (count>used && esp_ota_write(s.handle,data+used,count-used)!=ESP_OK) {clear();out[2]=FLASH;return 3;}
        if (mbedtls_sha256_update(&s.sha,data,count)) {clear();out[2]=FLASH;return 3;}
        s.received+=(uint32_t)count;
        out[2]=OTA_OK;put32(out+3,s.received);return 7;
    }
    if (r[1]==3 && s.state==RECEIVING) {
        if (s.received!=s.size || !s.handle) {out[2]=SEQUENCE;return 3;}
        uint8_t hash[32];
        if (mbedtls_sha256_finish(&s.sha,hash)) {clear();out[2]=FLASH;return 3;}
        if (memcmp(hash,s.digest,32)) {clear();out[2]=IMAGE;return 3;}
        esp_err_t result=esp_ota_end(s.handle);s.handle=0;
        if (result!=ESP_OK) {clear();out[2]=IMAGE;return 3;}
        s.state=READY;out[2]=OTA_OK;return 3;
    }
    if (r[1]==4 && s.state==READY) {
        if (!s_restart) {
            esp_timer_create_args_t args={.callback=restart,.name="rmlink_ota_restart"};
            if (esp_timer_create(&args,&s_restart)!=ESP_OK) {out[2]=FLASH;return 3;}
        }
        if (esp_ota_set_boot_partition(s.partition)!=ESP_OK) {clear();out[2]=FLASH;return 3;}
        s.state=COMMITTED;
        if (esp_timer_start_once(s_restart,1000000)!=ESP_OK) {out[2]=FLASH;return 3;}
        out[2]=OTA_OK;return 3;
    }
    out[2]=SEQUENCE;return 3;
}
