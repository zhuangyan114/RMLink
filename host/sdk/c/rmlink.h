/* SPDX-License-Identifier: Apache-2.0 */
#ifndef RMLINK_SDK_H
#define RMLINK_SDK_H
#include <stdint.h>
#include <stddef.h>
#ifdef RMLINK_BUILD
#define RM_API __declspec(dllexport)
#else
#define RM_API __declspec(dllimport)
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef struct rm_client rm_client;
/* Callback bytes are borrowed until return; copy to retain. Return nonzero to stop. */
typedef int (*rm_record_callback)(uint64_t cursor,const uint8_t *frame,size_t length,void *context);
/* token=NULL loads the current user's protected .rmlink/connector.json.
 * device_id=NULL creates a discovery-only client, with no target/session claim.
 */
RM_API rm_client *rm_open(const char *device_id,uint16_t api_port,const char *token);
RM_API void rm_close(rm_client *client);
RM_API int rm_devices(rm_client *client,char *out,size_t capacity,size_t *required);
RM_API int rm_capabilities(rm_client *client,char *out,size_t capacity,size_t *required);
/* Generic authenticated public API. Return HTTP status, or negative Win32 error.
 * required includes the trailing NUL. A short buffer returns -122 without replay.
 * one instance supports a control thread and a stream thread; no concurrent close.
 * /flash commit may block up to 180 seconds; cancel via another rm_call thread.
 */
RM_API int rm_call(rm_client *client,const char *method,const char *suffix,const char *json_body,
                  char *output,size_t capacity,size_t *required);
RM_API int rm_target_connect(rm_client *client,uint32_t clock_hz,char *out,size_t size);
RM_API int rm_subscribe(rm_client *client,const char *json_configuration,char *out,size_t size);
RM_API int rm_subscription_action(rm_client *client,unsigned id,const char *action,char *out,size_t size);
RM_API int rm_stream(rm_client *client,rm_record_callback callback,void *context);
RM_API void rm_stream_cancel(rm_client *client);
#ifdef __cplusplus
}
#endif
#endif
