/* SPDX-License-Identifier: Apache-2.0. Windows WinHTTP wrapper, no Python dependency. */
#define RMLINK_BUILD
#include "rmlink.h"
#include <windows.h>
#include <winhttp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
struct rm_client {
    HINTERNET http; INTERNET_PORT port;
    wchar_t auth[160]; char session[80];
    HANDLE stop,heartbeat; volatile LONG cancel;
};
static int wide(const char *src,wchar_t *out,unsigned n) {
    return MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,src,-1,out,(int)n);
}
static HINTERNET request(rm_client *c,const char *method,const char *path,const char *body,HINTERNET *connection) {
    wchar_t wm[16],wp[512];
    if(!wide(method,wm,16) || !wide(path,wp,512)) return NULL;
    *connection=WinHttpConnect(c->http,L"127.0.0.1",c->port,0);
    if(!*connection)return NULL;
    HINTERNET r=WinHttpOpenRequest(*connection,wm,wp,NULL,WINHTTP_NO_REFERER,WINHTTP_DEFAULT_ACCEPT_TYPES,0);
    if(!r)return NULL;
    int receive_timeout=strstr(path,"/flash")?180000:(strstr(path,"/time-sync") || !strcmp(path,"/v1/transport"))?30000:3000;
    WinHttpSetTimeouts(r,3000,3000,3000,receive_timeout);
    WinHttpAddRequestHeaders(r,c->auth,(DWORD)-1,WINHTTP_ADDREQ_FLAG_ADD);
    WinHttpAddRequestHeaders(r,L"Content-Type: application/json",(DWORD)-1,WINHTTP_ADDREQ_FLAG_ADD);
    size_t n=body?strlen(body):0;
    if(n>9216 || !WinHttpSendRequest(r,WINHTTP_NO_ADDITIONAL_HEADERS,0,(void*)body,(DWORD)n,(DWORD)n,0) || !WinHttpReceiveResponse(r,NULL)) {
        WinHttpCloseHandle(r);return NULL;
    }
    return r;
}
static int call_path(rm_client *c,const char *method,const char *path,const char *body,char *out,size_t capacity,size_t *required) {
    HINTERNET conn=NULL,r=request(c,method,path,body,&conn);
    if(!r) { int error=-(int)GetLastError();if(conn)WinHttpCloseHandle(conn);return error?error:-1; }
    DWORD status=0,n=sizeof(status);WinHttpQueryHeaders(r,WINHTTP_QUERY_STATUS_CODE|WINHTTP_QUERY_FLAG_NUMBER,NULL,&status,&n,NULL);
    char *buffer=malloc(65537);size_t used=0;int error=0;
    if(!buffer)error=-8;
    while(!error && used<65536) { DWORD got=0;if(!WinHttpReadData(r,buffer+used,(DWORD)(65536-used),&got)){error=-(int)GetLastError();break;}if(!got)break;used+=got; }
    if(used==65536)error=-122;
    if(required)*required=used+1;
    if(!error) { buffer[used]=0;if(out && capacity>=used+1)memcpy(out,buffer,used+1);else error=-122; }
    free(buffer);WinHttpCloseHandle(r);WinHttpCloseHandle(conn);return error?error:(int)status;
}
static bool token_from_state(char *token,size_t capacity) {
    char path[2048];DWORD n=GetEnvironmentVariableA("USERPROFILE",path,sizeof(path)-32);
    if(!n || n>=sizeof(path)-32)return false;
    strcat(path,"\\.rmlink\\connector.json");FILE *f=fopen(path,"rb");if(!f)return false;
    char data[16385];size_t used=fread(data,1,sizeof(data)-1,f);fclose(f);data[used]=0;
    char *p=strstr(data,"\"token\"");if(!p || !(p=strchr(p,':')) || !(p=strchr(p,'\"')))return false;
    char *end=strchr(++p,'\"');if(!end || (size_t)(end-p)>=capacity)return false;
    memcpy(token,p,(size_t)(end-p));token[end-p]=0;return true;
}
static DWORD WINAPI heartbeat(void *arg) {
    rm_client *c=arg;char out[512];
    while(WaitForSingleObject(c->stop,5000)==WAIT_TIMEOUT) {
        int status=rm_call(c,"POST","/heartbeat","{}",out,sizeof(out),NULL);
        if(status!=200) { InterlockedExchange(&c->cancel,1);break; }
    }
    return 0;
}
rm_client *rm_open(const char *device,uint16_t port,const char *provided) {
    if(device && (strlen(device)!=12 || strspn(device,"0123456789abcdefABCDEF")!=12))return NULL;
    char token[80];if(provided) { if(strlen(provided)>=sizeof(token))return NULL;strcpy(token,provided); }
    else if(!token_from_state(token,sizeof(token)))return NULL;
    if(!*token || strspn(token,"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-")!=strlen(token))return NULL;
    rm_client *c=calloc(1,sizeof(*c));if(!c)return NULL;c->port=port?port:3243;
    char auth[160];snprintf(auth,sizeof(auth),"Authorization: Bearer %s",token);memset(token,0,sizeof(token));wide(auth,c->auth,160);
    c->http=WinHttpOpen(L"RMLink SDK/1",WINHTTP_ACCESS_TYPE_NO_PROXY,WINHTTP_NO_PROXY_NAME,WINHTTP_NO_PROXY_BYPASS,0);
    if(!c->http){free(c);return NULL;}
    if(!device)return c;
    char body[128],out[1024];snprintf(body,sizeof(body),"{\"device_id\":\"%s\"}",device);
    if(call_path(c,"POST","/v1/sessions",body,out,sizeof(out),NULL)!=200)goto failed;
    char *p=strstr(out,"\"session_id\"");if(!p || !(p=strchr(p,':')) || !(p=strchr(p,'\"')))goto failed;
    char *end=strchr(++p,'\"');if(!end || (size_t)(end-p)>=sizeof(c->session))goto failed;
    memcpy(c->session,p,(size_t)(end-p));
    c->stop=CreateEvent(NULL,TRUE,FALSE,NULL);if(!c->stop)goto failed;
    c->heartbeat=CreateThread(NULL,0,heartbeat,c,0,NULL);if(!c->heartbeat)goto failed;
    return c;
failed:
    if(c->stop)CloseHandle(c->stop);
    WinHttpCloseHandle(c->http);memset(c,0,sizeof(*c));free(c);return NULL;
}
int rm_call(rm_client *c,const char *method,const char *suffix,const char *body,char *out,size_t capacity,size_t *required) {
    if(!c || !c->session[0] || !method || !suffix || strlen(suffix)>256 || (suffix[0] && suffix[0]!='/'))return -87;
    char path[512];snprintf(path,sizeof(path),"/v1/sessions/%s%s",c->session,suffix);
    return call_path(c,method,path,body,out,capacity,required);
}
int rm_devices(rm_client *c,char *out,size_t capacity,size_t *required) {
    return c?call_path(c,"GET","/v1/devices",NULL,out,capacity,required):-87;
}
int rm_capabilities(rm_client *c,char *out,size_t capacity,size_t *required) {
    return c?call_path(c,"GET","/v1/capabilities",NULL,out,capacity,required):-87;
}
int rm_time_sync(rm_client *c,const char *device,unsigned samples,char *out,size_t capacity,size_t *required) {
    if(!c || !device || strlen(device)!=12 || strspn(device,"0123456789abcdefABCDEF")!=12 || !samples || samples>16)return -87;
    char path[128],body[64];snprintf(path,sizeof(path),"/v1/devices/%s/time-sync",device);
    snprintf(body,sizeof(body),"{\"samples\":%u}",samples);
    return call_path(c,"POST",path,body,out,capacity,required);
}
int rm_set_transport(rm_client *c,const char *device,int native_usb,char *out,size_t capacity,size_t *required) {
    if(!c || !device || strlen(device)!=12 || strspn(device,"0123456789abcdefABCDEF")!=12 || (native_usb!=0 && native_usb!=1))return -87;
    char body[128];snprintf(body,sizeof(body),"{\"device_id\":\"%s\",\"transport\":\"%s\"}",device,native_usb?"usb":"wifi");
    return call_path(c,"POST","/v1/transport",body,out,capacity,required);
}
int rm_target_connect(rm_client *c,uint32_t clock,char *out,size_t size) {
    char body[128];snprintf(body,sizeof(body),"{\"command\":\"connect\",\"clock_hz\":%lu}",(unsigned long)(clock?clock:4000000));
    return rm_call(c,"POST","/target",body,out,size,NULL);
}
int rm_subscribe(rm_client *c,const char *json,char *out,size_t size) { return rm_call(c,"POST","/subscriptions",json,out,size,NULL); }
int rm_subscription_action(rm_client *c,unsigned id,const char *action,char *out,size_t size) {
    if(!action || (strcmp(action,"stop") && strcmp(action,"pause") && strcmp(action,"resume") && strcmp(action,"status")))return -87;
    char suffix[80],body[80];snprintf(suffix,sizeof(suffix),"/subscriptions/%u",id);snprintf(body,sizeof(body),"{\"action\":\"%s\"}",action);
    return rm_call(c,strcmp(action,"status")?"POST":"GET",suffix,strcmp(action,"status")?body:NULL,out,size,NULL);
}
static bool exact(HINTERNET request,uint8_t *out,unsigned count,rm_client *c) {
    while(count && !InterlockedCompareExchange(&c->cancel,0,0)) {
        DWORD got;if(!WinHttpReadData(request,out,count,&got) || !got)return false;out+=got;count-=got;
    }
    return count==0;
}
int rm_stream(rm_client *c,rm_record_callback callback,void *context) {
    if(!c || !c->session[0] || !callback)return -87;
    InterlockedExchange(&c->cancel,0);
    char path[256];snprintf(path,sizeof(path),"/v1/sessions/%s/stream",c->session);
    HINTERNET conn=NULL,r=request(c,"GET",path,NULL,&conn);if(!r){if(conn)WinHttpCloseHandle(conn);return -(int)GetLastError();}
    DWORD status=0,n=sizeof(status);WinHttpQueryHeaders(r,WINHTTP_QUERY_STATUS_CODE|WINHTTP_QUERY_FLAG_NUMBER,NULL,&status,&n,NULL);
    int result=(int)status;
    if(status==200) for(;;) {
        uint8_t wrapper[12],frame[384];if(!exact(r,wrapper,12,c)){result=InterlockedCompareExchange(&c->cancel,0,0)?200:-1;break;}
        uint64_t cursor;uint32_t size;memcpy(&cursor,wrapper,8);memcpy(&size,wrapper+8,4);
        if(!size)continue;
        if(size<56 || size>384){result=-13;break;}
        if(!exact(r,frame,size,c)){result=-1;break;}
        if(callback(cursor,frame,size,context))break;
    }
    WinHttpCloseHandle(r);WinHttpCloseHandle(conn);return result;
}
void rm_stream_cancel(rm_client *c) { if(c)InterlockedExchange(&c->cancel,1); }
void rm_close(rm_client *c) {
    if(!c)return;
    rm_stream_cancel(c);
    if(c->heartbeat) {
        SetEvent(c->stop);
        if(WaitForSingleObject(c->heartbeat,30000)!=WAIT_OBJECT_0)return; /* preserve live thread storage */
        CloseHandle(c->heartbeat);CloseHandle(c->stop);
    }
    if(c->session[0]) {char out[512];rm_call(c,"DELETE","",NULL,out,sizeof(out),NULL);}
    WinHttpCloseHandle(c->http);memset(c,0,sizeof(*c));free(c);
}
