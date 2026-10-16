/* SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif
#include "rmlink_stream_codec.h"
int main(int argc,char **argv) {
    (void)argv;
#ifdef _WIN32
    _setmode(_fileno(stdin),_O_BINARY);_setmode(_fileno(stdout),_O_BINARY);
#endif
    uint8_t data[4097];size_t n=fread(data,1,sizeof(data),stdin);
    size_t packed=argc>1?rmlink_stream_pack_delta(data,n):rmlink_stream_pack(data,n);
    if(!packed)return 2;
    return fwrite(data,1,packed,stdout)==packed?0:3;
}
