#pragma once
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERROR_CHECK(value) assert((value)==ESP_OK)
#define ESP_IMAGE_HEADER_MAGIC 0xe9
#define ESP_CHIP_ID_ESP32S3 9
#define ESP_APP_DESC_MAGIC_WORD 0xabcd5432
#define ESP_PARTITION_TYPE_DATA 1
#define ESP_PARTITION_TYPE_APP 0
#define ESP_PARTITION_SUBTYPE_DATA_OTA 0
#define ESP_PARTITION_SUBTYPE_APP_OTA_0 16
#define ESP_PARTITION_SUBTYPE_APP_OTA_1 17
#define OTA_WITH_SEQUENTIAL_WRITES 0xffffffffu
typedef struct {uint8_t magic,segments,pad[10];uint16_t chip_id;uint8_t tail[10];} esp_image_header_t;
typedef struct {uint32_t address,size;} esp_image_segment_header_t;
typedef struct {uint32_t magic_word,secure_version,reserved[2];char version[32],project_name[32],time[16],date[16],idf_ver[32];uint8_t app_elf_sha256[32];uint32_t tail[20];} esp_app_desc_t;
typedef struct {uint8_t subtype;uint32_t size;} esp_partition_t;
typedef enum {ESP_OTA_IMG_NEW,ESP_OTA_IMG_PENDING_VERIFY,ESP_OTA_IMG_VALID} esp_ota_img_states_t;
typedef uint32_t esp_ota_handle_t;
typedef void *esp_timer_handle_t;
typedef struct {void (*callback)(void *);const char *name;} esp_timer_create_args_t;
typedef struct {uint8_t hash[32];size_t count;} mbedtls_sha256_context;
const esp_app_desc_t *esp_app_get_description(void);
const esp_partition_t *esp_ota_get_next_update_partition(const void *);
const esp_partition_t *esp_ota_get_running_partition(void);
const esp_partition_t *esp_partition_find_first(int,int,const char *);
esp_err_t esp_ota_get_state_partition(const esp_partition_t *,esp_ota_img_states_t *);
esp_err_t esp_ota_begin(const esp_partition_t *,size_t,esp_ota_handle_t *);
esp_err_t esp_ota_write(esp_ota_handle_t,const void *,size_t);
esp_err_t esp_ota_end(esp_ota_handle_t);
esp_err_t esp_ota_abort(esp_ota_handle_t);
esp_err_t esp_ota_set_boot_partition(const esp_partition_t *);
esp_err_t esp_ota_mark_app_valid_cancel_rollback(void);
esp_err_t esp_ota_mark_app_invalid_rollback_and_reboot(void);
int64_t esp_timer_get_time(void);
esp_err_t esp_timer_create(const esp_timer_create_args_t *,esp_timer_handle_t *);
esp_err_t esp_timer_start_once(esp_timer_handle_t,uint64_t);
void esp_restart(void);
void mbedtls_sha256_init(mbedtls_sha256_context *);
void mbedtls_sha256_free(mbedtls_sha256_context *);
int mbedtls_sha256_starts(mbedtls_sha256_context *,int);
int mbedtls_sha256_update(mbedtls_sha256_context *,const uint8_t *,size_t);
int mbedtls_sha256_finish(mbedtls_sha256_context *,uint8_t *);
int mbedtls_sha256(const uint8_t *,size_t,uint8_t *,int);
