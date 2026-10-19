# RMLink 公开连接组件 API v1.2（开发版）

本文的基础接口来自服务／SDK 0.2.7；新增内容更新至连接服务 0.2.14、固件候选 0.2.40，SDK 仍为 0.2.8。较早固件的差异另行标注。[ESP32 缓存烧录协议与验收](buffered-flash-protocol.md)支持 backend 选择、进度、取消和断连清理。最新真实 Orbit 开发宿主验收见 [Timeline 稳定性记录](timeline-stability-20261016.md)，历史采样条件见 [采样性能记录](subscription-performance-20261015.md)。

## 接入与能力

Orbit 开发工作树现可用 `"probe":"rmlink"` 直接选择本服务后端，保留单一目标 owner；`rmlinkDeviceId` 可选，省略时仅在恰有一台已配对且在线的设备时自动选择。多台设备返回 AmbiguousDevice，不自动抢占。该入口默认启用 F5 烧录；`flashBeforeDebug:false` 仍明确跳过烧录。有线标准 CMSIS-DAP 继续使用 `probe:cmsis-dap`；标准无线接入改为 OpenOCD TCP，旧虚拟 USB 已从当前源码删除。

服务 0.2.11 起，增强 target/connect 遇到其他会话占用时返回 HTTP 409、`code:TargetBusy`、`write_completion_may_be_unknown:false`，不发送 SWD 命令、不释放其他 owner。租约建立成功不等于获得目标所有权；调用者须处理该冲突。原始 `/dap` 路径仍返回标准 CMSIS-DAP 的拒绝响应。

安装组件后，低频控制通过 `http://127.0.0.1:3243/v1`；要求 Bearer token，token 保存在当前用户受 ACL 保护的 `.rmlink/connector.json`，SDK 自动读取，不应记录或传给远程网页。标准无线 CMSIS-DAP 由 OpenOCD 直接连接板端 TCP 3241，增强能力由客户端调用本 API；本机不再监听 USB/IP 3240，也不再提供挂载/卸载接口或驱动状态字段。

`GET /v1/capabilities` 提供 API 版本、支持的目标命令、订阅类型、字段/指针/周期等限制。该接口报告软件实现范围；物理设备须在线且成功取得 owner。当前源码使用[统一目标算法包](shared-target-package.md)，按选定型号核对身份、容量、包标识及各独立 RAM/Flash 窗口。F103C8 的历史实板证据不代表新增 F407/H723 或当前固件已通过实板验收。

`POST /v1/sessions`：`{"device_id":"50787d15a924"}`，返回 session_id、heartbeat_seconds=5、expiry_seconds=30 和 packet_size。SDK 自动续期；最多 64 个本地租约，每块物理目标仅一个控制 owner。`DELETE /v1/sessions/{id}` 停止订阅、尝试清理本会话断点并释放 owner，未知写不重放。

`POST /v1/transport`：`{"device_id":"50787d15a924","transport":"usb"}`或wifi，只允许在该设备无owner且全部本地租约已关闭时执行。USB用原生WinUSB独占句柄，Wi-Fi用原有TCP；同一设备只保留一个 Broker，切换后重新协商实际包容量。偏好保存并用于后续重连；离线时返回connected=false及connection_error，不自动改选其他传输。首版尚未自动发现／配对未配置热点的全新USB设备，应先完成已有配对。已配对设备的有线采集不需要TCP数据socket。

## 高层目标操作

`POST /v1/sessions/{id}/target`，body 包含 command 和参数。connect 默认 `clock_hz=4000000`；第三方显式时钟仍有效。增强会话取得目标后，其 `/dap` 原始入口被拒绝，避免跨请求的 MEM-AP 缓存竞争。

| command | 参数 / 结果 |
| --- | --- |
| connect | 可选 clock_hz；返回 DP/CPUID/DEV_ID 和 profile，实际 SWCLK 尚未量测 |
| state | 返回 dhcsr 与 Running/Halted；0.2.4协商能力后通过同一执行器的一次只读操作查询，不跨无线往返持有采样屏障；旧固件保持原路径 |
| halt / run / step / reset | 有界状态确认；0.2.14起reset通过VC_CORERESET捕获SYSRESETREQ并保持Halted，保留订阅，随后run恢复Timeline；物理NRST不在当前验收内 |
| run_to_address | 0.2.14起；address为F103前64 KiB Flash地址（支持Thumb位），reset为布尔值、默认true。临时FPB断点运行到指定地址后确认Halted/PC，恢复原比较器和DEMCR；复用现有owner。返回pc/lr/dhcsr、cleanupOk、临时/共享槽及忽略的用户槽诊断。无空槽、未命中入口或超时以409返回，恢复失败/目标访问失效仍报503，不重放未知写入 |
| read_memory | address、byte_length，最多 4096 B；返回 data hex |
| read_batch | reads 数组，1..32 个范围，总量最多 4096 B |
| write_memory | address、data hex，按对齐 word 写，最多 4096 B；按包和 1 KiB 边界独立设置地址 |
| read_register / write_register | index 0..20、可选 value；核心必须暂停 |
| set_breakpoint / clear_breakpoint | F103 Flash FPB 地址 / id；只使用空比较器 |
| benchmark_local | address、stride、read_count≤10000、field_count≤32；ESP32 本地计时连续块/离散组读，最长约 500 ms |

目标控制通过同一 owner 和控制屏障执行。halt/step 使 Timeline 暂停，run 只恢复由控制操作暂停的订阅；用户显式暂停不会自动恢复。服务 0.2.9 起，在已确认暂停的会话中恢复或新建的 Timeline 也保持暂停，等待同一 owner 的 run；Watch 快照不受此规则影响。复位和烧录先撤销所有旧订阅，重新配置地址/RTT 后才开始新的采集。

0.2.4板端能力0x81/0的feature word bit4表示CORE_STATE（0x81/17）。它只读取DHCSR，要求匹配现有owner，不接受任意地址，也不创建第二执行器；成功返回command/sub/status+dhcsr32。STATE查询失败明确传播，不重放控制。服务连接时读取能力后选择此路径，标准CMSIS-DAP流量不插入该命令。

固件 0.2.32 起，feature bit7 表示有界 RAM/Flash 快照读取。`0x81/20`（十进制 20，即 `0x14`）请求为 command/sub + address32 LE + byte_length16 LE；响应为 command/sub/status + 原始字节。单次 1..128 B，同时不超过该传输的 packet_size−3，只允许当前 F103 profile 的 RAM/Flash。要求匹配现有 owner，由统一 SWD 执行器完成，不跨网络往返持有采样屏障。服务 0.2.9 起在能力允许、全部范围为已知 RAM/Flash 时自动用于 read_memory/read_batch；周边寄存器和旧固件仍走原屏障路径。重复范围不会合并；不保证跨包、跨变量的原子快照，写操作不使用此路径。

## Watch / Timeline / RTT 订阅

`POST /v1/sessions/{id}/subscriptions`。类型以通用名称公开，和 Orbit 面板解耦。

```json
{"kind":"memory_series","addresses":[536871168,536871232],"period_us":1000}
```

```json
{"kind":"memory_snapshot","fields":[{"address":536871808,"offsets":[0,0,4],"byte_length":4}],"period_us":100000}
```

```json
{"kind":"rtt_stream","control_block_address":536871936,"channel":0,"period_us":5000}
```

固定字段可用 addresses（默认 32-bit）；fields 支持 byte_length 1/2/4 和最多 4 层 offsets。每层读取当前地址的 ptr32，再加该 offset；到最后地址读取字段。指针存储地址须 4 字节对齐，最终值按宽度对齐。Timeline 只在一帧内复用相同指针依赖，下一帧重新解析；空指针/越界字段无效但其他字段继续，SWD 通信错误则暂停任务。

当前固定槽：Timeline=1、Watch=2、RTT channel0=3、第二个 RTT channel=4。每类槽只允许一个订阅，字段最多 32，周期 200..10000000 us；这些是输入范围，不保证对应采样率。`strict_deadlines` 默认 false：来不及执行的周期增加序号与 deadline_missed，完整采样继续回传，预算超限保留 flags bit1。true 时完整帧执行超过周期便暂停并报告 budget_overrun。旧版未带该字段的板端 PREPARE 命令保持严格行为。

返回 subscription_id、capture_id、stream_cursor 和接受的配置。stream_cursor 是启动新任务前的服务游标，用于排除历史任务。`GET .../subscriptions/{id}` 返回 state、kind、completed、deadline_missed、ring_dropped、failures、budget_overrun、stop_reason 和服务缓存状态。completed 对内存订阅为已生成帧数（含 partial），RTT 为已提交字节数。

`POST .../subscriptions/{id}` 支持 action=stop/pause/resume；`DELETE` 停止并移除服务注册。配置更新首版采用停止/删除/重新创建，不提供运行中原子 PATCH。重新创建产生新的 capture_id；不能把旧缓存的帧当作新任务数据。

固件 0.2.8 起，Watch 可复用正在运行的 Timeline 的相同逻辑字段，包括最多四层指针。根地址、全部 offsets、指针深度、byte_length 必须完全相同，源字段有效且整帧已完成；执行器选用结果时，源样本距当前时间不得超过 Watch 的 period_us。Timeline 停止／暂停、正在采集下一帧、源结果过期或计划不匹配时，Watch 自行解析指针并读取。目标控制屏障清除旧源结果，避免内存写入、暂停或复位后继续复用先前值。Timeline 重新配置会清除其旧状态；不将上次解析到的物理地址长期绑定为 Watch 地址。0.2.7 及以前仅复用静态重合 32-bit 字段，且没有上述年龄限制。

复用标记为 sample flags bit2：表示本 Watch 帧至少有一个字段复用，不能据此认定所有字段来自同一次采样。时间戳取本帧最早的采样来源，duration_us 包含到帧结束的跨度，不是纯 SWD 执行时间。年龄条件检查发生在板端选用字段时；网络与服务排队会继续增加前端收到数据时的年龄，前端可结合 TIME_SYNC 估计，不能把 period_us 当作端到端最大延迟。

RTT Up 根据控制块和环形描述符读取、提交 RdOff；完成状态未知时保留待提交记录并暂停，显式恢复前重读偏移。目标端已丢弃的日志数量不可从环形缓冲精确推断。

## 二进制流与缓存

`GET /v1/sessions/{id}/stream?after={cursor}` 使用相同 Authorization，返回 HTTP chunked 的 `application/vnd.rmlink.stream`。每个本地记录是 cursor(uint64 LE) + frame_length(uint32 LE) + frame；length=0 是保活，不是样本。帧最大 384 B，慢消费者可从 cursor 跳变识别服务缓存缺口。Python/Node/C SDK 默认保存启动游标并在消费后推进；显式 after 用于有界历史补读，不会让板端重放目标操作。

`POST .../events` 提供 JSON 拉取：after、limit=1..256、wait_ms=0..5000；返回 records、cursor、consumer_gap 和 host_history_evicted。后者是服务历史队列累计淘汰数，包含已经被消费者读取的旧记录，不能当作丢样数；旧名称 host_dropped 保留为同值兼容别名。consumer_gap 表示该次请求的游标已落后于保留窗口。板端序号缺口、deadline_missed、ring_dropped 和每消费者缺口须分别分析。

板端独立数据连接为 TCP3244，RMS1 + 一次性随机票据绑定控制会话；公开 SDK 由服务代为处理，前端无需直接连接。错误票据、重复绑定和 owner 释放后的连接被拒绝/关闭。

固件 0.2.37 的 feature bit8 声明紧凑 TCP 数据格式。服务 0.2.10 仅在协商该位后发送 `RMS2` + 同样票据；响应仍为 `OKAY`。旧服务发送 RMS1 时，固件保持原 RML1；旧固件没有 bit8，新服务也仍发送 RMS1。USB_PULL 和公开本地 stream、Python/Node/C SDK 格式均不改变。

RMS2 连接输出连续 RMC2 组：组头 20 B，布局为 `magic[4]="RMC2", kind8, subscription_id8, record_count16, capture_id32, revision32, body_length32`，整数均为 LE。连续同 kind/slot/capture/revision 的样本共用组头；组体每条为原 RML1 的 `payload_crc32 + 完整 sample payload`，payload 中 data_len16 指示本条长度。单组体最多 4096 B、条数 1..73；服务逐条还原原 RML1 帧并执行同样 CRC/schema 验证，再交给现有消费者。保留每个样本的绝对序号、探针时间戳、duration、valid bitmap、flags 和原始数值，序号跳变仍是缺样，不插值、不补点、不重放目标操作。三个 32 位字段的格式预算从每帧 68 B 降至每条 40 B 加共享组头，5000 Hz 约 202.5 KB/s（按每 8 ms 一组），不等同于实测无线速率。

固件0.2.40增加feature bit9（512）声明RMS3，服务0.2.13优先发送`RMS3 + ticket`，没有该位仍协商RMS2/RMS1。USB_PULL、公开本地RML1 stream及各SDK格式保持原样。RMS3支持独立RMC3组，组头布局与RMC2相同、magic改为`RMC3`。组体每条为`payload_crc32, payload_length16, encoded_length16, stored_bytes`；payload长度24..352 B，组体最多4096 B、1..73条。encoded_length=0时stored_bytes是完整原payload；否则encoded_length必须小于payload_length，stored_bytes为当前payload逐字节异或上一条payload后的零游程编码。每组第一条以全零作前值，前条较短时其范围之外补零，前值在每条解码后更新，包括raw条目。控制字节低7位加1为长度，高位1表示该长度零字节，高位0表示随后该长度的字面量。每个游程1..128 B，解码结果必须恰好等于payload_length。

服务还原原RML1帧后逐条检查原始CRC和schema，再交给现有消费者。时间戳、序号、duration、有效位、flags及数值的原始字节全部保留，不合并相同值的样本，也不改变SWD操作；每组重置前值，独立解码，不跨连接续用压缩状态。RMS3解码器同时接受RMC2；旧RMS2解码器拒绝未协商RMC3。真实十变量记录中RMS3的bytes/raw_bytes约20.9%，取决于变量变化情况，不能作为任意布局的固定压缩率。

有线数据走0x81/16 USB_PULL：请求frame_id(uint32 LE，初始1)、offset(uint16 LE)。回应为command/sub/status、frame_id32、total16、offset16、chunk_len8和最多52 B数据。total=0为空轮询，不推进frame_id；每帧最多384 B，重复读取相同id/offset稳定，下一帧id递增并跳过0，旧id／跳号拒绝。USB_PULL与TCP数据绑定互斥。服务组帧后使用同样CRC／schema和本地stream接口；分片格式不是标准 CMSIS-DAP 命令，标准无线软件通过 OpenOCD TCP 使用普通 DAP 命令。

帧头为 32 B：RML1、version、kind、subscription_id、flags、capture_id、revision、sequence、payload_len、record_count、header_len、payload CRC32。内存样本头为 24 B：seq32、timestamp_us64、duration_us32、valid_bitmap32、sample_flags16、data_len16，之后是每字段 uint32 原始值。sample_flags：bit0 partial、bit1 执行预算超限、bit2 Watch 复用。RTT 数据部分为 channel32 + byte_sequence64 + length32 + bytes。

RML1 每记录独立带帧头，10×32-bit/1 kHz 为约 96 KB/s，再加 TCP 开销；这是格式预算。探针缓存：0.2.33–0.2.38 有 PSRAM 时 Timeline 为 256 KiB，0.2.39 增至 2 MiB；无 PSRAM 或分配失败时保持 48 KiB 内部内存回退。Watch4 KiB、两个 RTT 各6 KiB，因此 0.2.39 总量为2064/64 KiB；0.2.37 起能力查询的缓存字节数报告实际分配总量。四个32位字段每条环形记录约80 B，2 MiB在5 kHz请求下约能容纳5.2秒；字段数、采样频率及实际分配影响可承受时间，不能把它当作固定断网保证。内存缓存满时有界丢旧并计数，最多回收8条；若并发发送仍占用空间则丢新，绝不阻塞SWD。RTT无空间不消费目标缓冲。服务最多保留4096条记录，各消费者游标独立。服务端原始录制见下文；应用确认重传尚未实现。

0.2.39 的独立数据 socket 发送等待上限为5秒，握手接收仍为2秒，控制 socket 的超时与所有权规则不随之延长。较大缓存用于保留网络短暂停顿期间的样本，实时画面仍可能停顿后追上；不插值、隐藏溢出或保证长期断线可恢复。

连接服务0.2.12支持启动前设置 `RMLINK_STREAM_DIAGNOSTICS=1`：每10秒记录物理数据连接的接收字节/帧数、最长read等待、解码耗时、Windows socket TCP计数及服务事件循环最长延迟，并记录目标控制屏障耗时。计数只含元数据，不打印token/热点密码/样本值；关闭该变量不启用上述周期诊断。read等待包含无数据和网络等待，不能单独解释为CPU解码慢；Windows retransmitted为本机发送方向，不能以0推导ESP没有重传。TCP即时ACK是每连接的可选设置，不支持时记录警告并保留正常连接，不修改系统TCP注册表。

timestamp表示探针单调时基，duration是采集跨度，变量不是原子快照。Orbit实验分支用首帧对齐主机时间并保留探针间隔；SDK提供TIME_SYNC映射供其他客户端使用，Orbit尚未使用其绝对时间映射。断电/断线末尾丢失可能无法精确计数。

`POST /v1/devices/{device_id}/time-sync`，body `{"samples":8}`（1..16），不创建会话、不占目标，可与现有owner共存。固件0x81/13回显随机token并返回执行器接收／回复时间；服务记录主机往返，选择最小不确定度的映射，返回probe_us、host_monotonic_ns、offset_ns、uncertainty_ns和全部测量。64-bit主机时间以十进制字符串返回。探针重启被观察到时更换clock_epoch；服务重启也换epoch。host_wall_time_ns在检测到明显系统时间跳变时为null。误差范围包含剩余往返的一半及1 us量化，不代表单程延迟已测或已补偿时钟漂移；客户端需周期重测并识别epoch。

## 通用烧录接口

`POST .../flash`：action=begin/chunk/commit/cancel；`GET .../flash` 查询进度。当前仅支持 F103C8 主 Flash 的完整1 KiB页镜像，不支持选项字节、部分页保留或通用 ELF 上传。

1. begin：address、byte_length（最多64 KiB、均按1 KiB页对齐）、sha256、backend=auto/host/probe、verify_on_host（默认false）。
2. chunk：offset、data hex，每片最多4096 B。重叠内容不一致拒绝。
3. commit：先检查覆盖/哈希、芯片容量，再停止订阅。probe路径将镜像封存在ESP32后由单执行器自主加载RAM算法、擦除/编程/逐半字确认、全镜像回读比较、选项字节核对和软件复位。完整64 KiB可使用受保护的全主Flash擦除，局部完整页继续逐页擦除。verify_on_host=true再加主机全量SHA256；host路径始终主机回读。结果以verification/probe_verified/host_verified明确比较发生在哪里。保留新程序。
4. cancel：请求有界取消。正在执行的写入可能完成；失败时尝试暂停核心，不重放、不自动恢复旧程序。

提交后 complete/failed/cancelled 的上传不能再次 commit，必须 begin 一个新操作。HTTP 超时不代表操作未执行；查询状态后决定下一步。算法从 Orbit 固定提交独立构建，来源和 MIT 许可在 `host/algorithms/stm32f103c8/`。

auto仅在明确不支持板端功能或BEGIN报告缓存内存不足时使用host；传输或未知写入失败不回退、不重放。Node AbortSignal可独立请求取消并等待原COMMIT终态；Python提供flash_status/cancel_flash。C rm_call的输出过小也可能已执行写入，应GET状态，不能重发COMMIT。完整线协议见[缓存烧录协议](buffered-flash-protocol.md)。

## 设备健康与时钟

`GET /v1/devices/{id}/health` 无需目标会话，返回内部RAM/PSRAM的free/largest/minimum、请求时钟和最后采集IO失败详情；旧固件明确supported=false。Python device_health、Node deviceHealth、C rm_device_health共享此入口。

0.3.3 增加可选的 `read_fault_recovery`：`supported`、`attempts`、`recovered`、`exhausted`、`write_faults_not_replayed`、`last_dp_ctrl_stat`、`last_fault_uptime_us`。计数在探针重启后清零；只对只读访问中的单独 WDATAERR 做一次有界恢复，目标写入失败不自动重放，原始 IO 失败诊断保留。旧固件对此子快照返回 `supported=false`，不会使其他健康字段失效。协议与限制见 [USB 连续回传](usb-throughput.md)。

目标命令 `measure_swd_clock`（mode=sequence/dp_read）需已连接会话、停止采样，通过原控制屏障执行。返回原始RMT符号和数字周期统计，不改变Flash。80 MHz内部参考不是外部仪器，不能判定目标端模拟波形质量。字段、能力协商和二进制布局见[诊断协议](device-diagnostics.md)。

## 服务端录制

`POST .../recordings`：`{"subscription_ids":[1,2,3],"strict":true}`；订阅须已存在，省略 subscription_ids 选择当前全部订阅，strict 默认 true。返回 recording_id、path、phase、frames、bytes、gaps、invalid_samples、error、finalized。每个会话最多一个正在录制或收尾的文件；生成文件名位于当前用户 `.rmlink/recordings`，客户端不能指定覆盖其他文件。

`GET .../recordings` 返回会话当前/最近一次录制，`GET .../recordings/{id}` 查询状态；`POST .../recordings/{id}` 的 `{"action":"stop"}` 排空已接收队列、写入尾部并执行 fsync。应先停止录制，再删除订阅或关闭会话。界面无需持续读 events，原始帧在服务历史淘汰前送给独立录制器。

严格模式遇到板端序号缺口、RTT 字节序号缺口、partial/预算超限样本即失败，暂停所选运行任务；写队列满、存储失败、采集流失败和会话关闭也明确失败，不自动补写或重放目标操作。文件 finalized=true 只表示容器完整结束；还须 phase=complete、error=null 才能作为成功录制。strict=false 保留带缺口/无效标记的实时数据，仍记录统计，存储异常仍失败。

RMLR v1 文件保存配置、原始 RML1 帧与服务游标以及最终统计。`scripts/inspect_recording.py FILE` 流式检查长度、每帧 CRC、完整尾部及帧数，给出整个文件 SHA256。详见 [录制格式](recording-format.md)。当前录制窗口始于订阅已经启动之后，结束于停止入口前已被服务接收的记录；不保证订阅第一帧或停止时仍在网络/探针中的末尾数据。TCP 可靠传输不等于断电后完整录制，尚无应用确认、断线续传或文件分段轮转。

0.2.5独立组件可直接运行 `RMLinkConnector.exe inspect-recording FILE` 离线校验，无需Python、token、在线服务或目标连接。磁盘flush/fsync失败会返回finalized=false并尽力移除完整尾部；截断也失败时附invalidation_error，不能据文件旧尾部忽略运行状态中的存储错误。

## SDK 与验证范围

- Python：`host/rmlink_connector/client.py`；示例 `host/examples/capture.py`。flash_image(data) 封装上传与提交。
- Node/TypeScript：`host/sdk/` 的 `@rmlink/client`；订阅、取消、二进制帧 CRC 与 typed sample；flashImage(data) 走同一公开API。
- C ABI：`host/sdk/c/rmlink.h`，Build-Sdk.ps1 生成 DLL、MinGW import library、DEF 和头文件。MSVC 可用 `lib /def:rmlink.def /machine:x64 /out:rmlink.lib` 生成导入库。rm_open(NULL,...) 配合 rm_devices／rm_capabilities 查询设备，不创建会话或占用目标；传 device_id 才建立带心跳的会话。回调帧内存只在回调期间有效；取消流后需等待流线程结束再关闭句柄。rm_call 公开通用目标和烧录路由，输出缓冲不足不重放请求。

当前证据分开记录：主机协议/模型/实际WinHTTP SDK、ESP32构建/刷写/启动、真实 STM32 采样/烧录/标准调试、Orbit独立工作树构建及回归。已测的 ESP32 本地 SWD 吞吐是该实现、该请求时钟下的结果；安装后的 VS Code 界面、物理 NRST、其他芯片、Keil/IAR 与正式签名分发仍须独立验收。
