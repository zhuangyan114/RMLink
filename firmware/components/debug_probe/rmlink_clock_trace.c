/* SPDX-License-Identifier: Apache-2.0
 * RMT observes the existing SWCLK pad. It never generates debug clocks.
 */
#include "sdkconfig.h"
#if CONFIG_DEBUG_PROBE_IFACE_SWD
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "driver/rmt_rx.h"
#include "esp_log.h"
#include "soc/gpio_periph.h"
#include "debug_gpio.h"
#include "DAP.h"
#include "rmlink_clock_trace.h"

#define SYMBOLS 64
#define RESOLUTION 80000000u
static rmt_symbol_word_t s_symbols[SYMBOLS];
static volatile unsigned s_count;
static uint32_t s_next,s_id;
static rmt_channel_handle_t s_channel;
static bool s_enabled;
static StaticSemaphore_t s_done_storage;
static SemaphoreHandle_t s_done;
_Static_assert(sizeof(rmt_symbol_word_t)==4,"Trace wire symbol width");
static uint32_t u32(const uint8_t *p) {return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void w32(uint8_t *p,uint32_t v) {for(unsigned i=0;i<4;i++)p[i]=v>>(i*8);}
static bool received(rmt_channel_handle_t channel,const rmt_rx_done_event_data_t *event,void *context) {
    s_count=event->num_symbols>SYMBOLS?SYMBOLS:event->num_symbols;
    BaseType_t wake=pdFALSE;xSemaphoreGiveFromISR(s_done,&wake);return wake==pdTRUE;
}
static bool release_receiver(void) {
    if(!s_channel)return true;
    if(s_enabled) {
        if(rmt_disable(s_channel)!=ESP_OK)return false;
        s_enabled=false;
    }
    if(rmt_del_channel(s_channel)!=ESP_OK)return false;
    s_channel=NULL;return true;
}
void rmlink_clock_trace_clear(void) {
    s_id=0;s_count=0;
    if(!release_receiver())ESP_LOGW("rmlink_clock","RMT release pending; at most one receiver is retained");
}
size_t rmlink_clock_trace_command(const uint8_t *r,size_t len,uint8_t *out,size_t capacity) {
    out[0]=0x81;out[1]=len>1?r[1]:255;out[2]=1;
    if(len<2 || capacity<64)return 3;
    if(r[1]==19) {
        if(len<8 || !s_id || u32(r+2)!=s_id || r[6]>s_count || !r[7])return 3;
        size_t n=s_count-r[6];if(n>r[7])n=r[7];if(n>(capacity-9)/4)n=(capacity-9)/4;
        out[2]=0;w32(out+3,s_id);out[7]=r[6];out[8]=n;
        for(unsigned i=0;i<n;i++)w32(out+9+i*4,s_symbols[r[6]+i].val);
        return 9+n*4;
    }
    if(r[1]!=18 || len<3 || r[2]>1)return 3;
    uint32_t clock=DAP_GetSWJClock();
    if(DAP_Data.debug_port!=DAP_PORT_SWD || clock<100000 || clock>10000000) {out[2]=5;return 3;}
    if(!s_done)s_done=xSemaphoreCreateBinaryStatic(&s_done_storage);
    if(!s_done || !release_receiver()) {out[2]=4;return 3;}
    s_id=0;s_count=0;xSemaphoreTake(s_done,0);
    uint32_t mux=REG_READ(GPIO_PIN_MUX_REG[GPIO_SWCLK]);
    uint32_t output=REG_READ(GPIO_FUNC0_OUT_SEL_CFG_REG+GPIO_SWCLK*4);
    bool enabled=(REG_READ(GPIO_SWCLK<32?GPIO_ENABLE_REG:GPIO_ENABLE1_REG)&(1u<<(GPIO_SWCLK&31)))!=0;
    /* Match the inactive GPIO latch to the pad before RMT's GPIO setup, then
     * restore the dedicated output route before issuing the existing DAP call. */
    gpio_ll_input_enable(dedic_gpio_dev,GPIO_SWCLK);gpio_set_level(GPIO_SWCLK,gpio_get_level(GPIO_SWCLK));
    rmt_rx_channel_config_t config={.gpio_num=GPIO_SWCLK,.clk_src=RMT_CLK_SRC_APB,
        .resolution_hz=RESOLUTION,.mem_block_symbols=64,.flags.io_loop_back=1};
    esp_err_t error=rmt_new_rx_channel(&config,&s_channel);
    uint8_t ack=255,response[8]={0};uint32_t value=0;bool sequence_sent=false,complete=false;
    if(error==ESP_OK) {
        REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG+GPIO_SWCLK*4,output);
        rmt_rx_event_callbacks_t callbacks={.on_recv_done=received};
        error=rmt_rx_register_event_callbacks(s_channel,&callbacks,NULL);
        if(error==ESP_OK) {error=rmt_enable(s_channel);s_enabled=error==ESP_OK;}
        rmt_receive_config_t receive={.signal_range_min_ns=0,.signal_range_max_ns=100000};
        if(error==ESP_OK)error=rmt_receive(s_channel,s_symbols,sizeof(s_symbols),&receive);
        if(error==ESP_OK) {
            if(!r[2]) {
                /* Idle clocks (SWDIO low) preserve the established DP/AP state.
                 * A line reset here would invalidate the caller's AP pipeline. */
                const uint8_t sequence[]={0x12,56,0,0,0,0,0,0,0};
                DAP_ProcessCommand(sequence,response);sequence_sent=true;
            }else {
                const uint8_t read_id[]={5,0,1,2};DAP_TransferDeadline(2000);
                size_t n=DAP_ProcessCommand(read_id,response)&0xffff;
                ack=n>=3?response[2]:0;if(n==7 && response[1]==1 && ack==1)value=u32(response+3);
            }
            complete=xSemaphoreTake(s_done,pdMS_TO_TICKS(20))==pdTRUE;
        }
    }
    bool released=release_receiver();
    if(enabled)gpio_ll_output_enable(dedic_gpio_dev,GPIO_SWCLK);else gpio_ll_output_disable(dedic_gpio_dev,GPIO_SWCLK);
    /* gpio_ll_output_disable resets the output mux. Restore the dedicated
     * peripheral OE route last; GPIO_ENABLE alone does not describe that pad. */
    REG_WRITE(GPIO_FUNC0_OUT_SEL_CFG_REG+GPIO_SWCLK*4,output);
    REG_WRITE(GPIO_PIN_MUX_REG[GPIO_SWCLK],mux);
    if(sequence_sent) {const uint8_t idle[]={0x12,8,0};DAP_ProcessCommand(idle,response);}
    if(error!=ESP_OK || !released || !complete || s_count<4) {
        out[2]=error!=ESP_OK || !released?4:6;return 3;
    }
    if(++s_next==0)++s_next;
    s_id=s_next;
    out[2]=0;out[3]=1;out[4]=r[2];out[5]=s_count;out[6]=0;
    w32(out+7,s_id);w32(out+11,RESOLUTION);w32(out+15,clock);
    w32(out+19,DAP_Data.clock_delay);w32(out+23,value);out[27]=DAP_Data.fast_clock;out[28]=ack;
    return 29;
}
#endif
