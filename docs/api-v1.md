# RMLink 公开连接组件 API v1.2（开发版）

适用于固件/服务/SDK 0.2.3–0.2.4。2026-10-15 接线恢复后，STM32F103C8 已通过请求 4 MHz 的虚拟 USB 完整烧录/调试、三订阅有效采样与增强 Flash 引擎验收；实际 SWCLK 尚未用仪表测量。性能条件和原始证据见 [采样性能记录](subscription-performance-20261015.md)。公开 API 的实现和测量不等同于已安装 VS Code 界面验收。

## 接入与能力

安装组件后，低频控制通过 `http://127.0.0.1:3243/v1`；要求 Bearer token，token 保存在当前用户受 ACL 保护的 `.rmlink/connector.json`，SDK 自动读取，不应记录或传给远程网页。USB/IP 环回与普通软件仍使用现有虚拟 CMSIS-DAP。增强能力需要客户端调用本 API。

`GET /v1/capabilities` 提供 API 版本、支持的目标命令、订阅类型、字段/指针/周期等限制。该接口报告软件实现范围；物理设备须在线且成功取得 owner。增强目标当前限定 F103 medium-density Cortex-M3，采样允许的 RAM 为 20 KiB、Flash 为前 64 KiB；烧录进一步检查实际容量为 64 KiB。其他芯片先使用标准虚拟 USB，不能将本版增强 profile 泛化。

`POST /v1/sessions`：`{"device_id":"50787d15a924"}`，返回 session_id、heartbeat_seconds=5、expiry_seconds=30 和 packet_size。SDK 自动续期；最多 64 个本地租约，每块物理目标仅一个控制 owner。`DELETE /v1/sessions/{id}` 停止订阅、尝试清理本会话断点并释放 owner，未知写不重放。

`POST /v1/transport`：`{"device_id":"50787d15a924","transport":"usb"}`或wifi，只允许在该设备无owner且全部本地租约已关闭时执行。USB用原生WinUSB独占句柄，Wi-Fi用原有TCP；同一设备只保留一个Broker，虚拟USB重新挂载并协商实际64／512字节。偏好保存并用于后续重连；离线时返回connected=false及connection_error，不自动改选其他传输。首版尚未自动发现／配对未配置热点的全新USB设备，应先完成已有配对。已配对设备的有线采集不需要TCP数据socket。

## 高层目标操作

`POST /v1/sessions/{id}/target`，body 包含 command 和参数。connect 默认 `clock_hz=4000000`；第三方显式时钟仍有效。增强会话取得目标后，其 `/dap` 原始入口被拒绝，避免跨请求的 MEM-AP 缓存竞争。

| command | 参数 / 结果 |
| --- | --- |
| connect | 可选 clock_hz；返回 DP/CPUID/DEV_ID 和 profile，实际 SWCLK 尚未量测 |
| state | 返回 dhcsr 与 Running/Halted；0.2.4协商能力后通过同一执行器的一次只读操作查询，不跨无线往返持有采样屏障；旧固件保持原路径 |
| halt / run / step / reset | 有界状态确认；reset 为 SYSRESETREQ，物理 NRST 不在当前验收内 |
| read_memory | address、byte_length，最多 4096 B；返回 data hex |
| read_batch | reads 数组，1..32 个范围，总量最多 4096 B |
| write_memory | address、data hex，按对齐 word 写，最多 4096 B；按包和 1 KiB 边界独立设置地址 |
| read_register / write_register | index 0..20、可选 value；核心必须暂停 |
| set_breakpoint / clear_breakpoint | F103 Flash FPB 地址 / id；只使用空比较器 |
| benchmark_local | address、stride、read_count≤10000、field_count≤32；ESP32 本地计时连续块/离散组读，最长约 500 ms |

目标控制通过同一 owner 和控制屏障执行。halt/step 使 Timeline 暂停，run 只恢复由控制操作暂停的订阅；用户显式暂停不会自动恢复。复位和烧录先撤销所有旧订阅，重新配置地址/RTT 后才开始新的采集。

0.2.4板端能力0x81/0的feature word bit4表示CORE_STATE（0x81/17）。它只读取DHCSR，要求匹配现有owner，不接受任意地址，也不创建第二执行器；成功返回command/sub/status+dhcsr32。STATE查询失败明确传播，不重放控制。服务连接时读取能力后选择此路径，标准CMSIS-DAP流量不插入该命令。

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

固定字段可用 addresses（默认 32-bit）；fields 支持 byte_length 1/2/4 和最多 4 层 offsets。每层读取当前地址的 ptr32，再加该 offset；到最后地址读取字段。指针存储地址须 4 字节对齐，最终值按宽度对齐。只在一帧内复用相同指针前缀，下一帧重新解析；空指针/越界字段无效但其他字段继续，SWD 通信错误则暂停任务。

当前固定槽：Timeline=1、Watch=2、RTT channel0=3、第二个 RTT channel=4。每类槽只允许一个订阅，字段最多 32，周期 200..10000000 us；这些是输入范围，不保证对应采样率。`strict_deadlines` 默认 false：来不及执行的周期增加序号与 deadline_missed，完整采样继续回传，预算超限保留 flags bit1。true 时完整帧执行超过周期便暂停并报告 budget_overrun。旧版未带该字段的板端 PREPARE 命令保持严格行为。

返回 subscription_id、capture_id、stream_cursor 和接受的配置。stream_cursor 是启动新任务前的服务游标，用于排除历史任务。`GET .../subscriptions/{id}` 返回 state、kind、completed、deadline_missed、ring_dropped、failures、budget_overrun、stop_reason 和服务缓存状态。completed 对内存订阅为已生成帧数（含 partial），RTT 为已提交字节数。

`POST .../subscriptions/{id}` 支持 action=stop/pause/resume；`DELETE` 停止并移除服务注册。配置更新首版采用停止/删除/重新创建，不提供运行中原子 PATCH。重新创建产生新的 capture_id；不能把旧缓存的帧当作新任务数据。

Watch 静态 32-bit 重合字段复用正在运行的 Timeline；Timeline 停止/暂停后，Watch 自身读取继续。复用标记为 sample flags bit2，时间戳与跨度包含较早源样本时间。RTT Up 根据控制块和环形描述符读取、提交 RdOff；完成状态未知时保留待提交记录并暂停，显式恢复前重读偏移。目标端已丢弃的日志数量不可从环形缓冲精确推断。

## 二进制流与缓存

`GET /v1/sessions/{id}/stream?after={cursor}` 使用相同 Authorization，返回 HTTP chunked 的 `application/vnd.rmlink.stream`。每个本地记录是 cursor(uint64 LE) + frame_length(uint32 LE) + frame；length=0 是保活，不是样本。帧最大 384 B，慢消费者可从 cursor 跳变识别服务缓存缺口。Python/Node/C SDK 默认保存启动游标并在消费后推进；显式 after 用于有界历史补读，不会让板端重放目标操作。

`POST .../events` 提供 JSON 拉取：after、limit=1..256、wait_ms=0..5000；返回 records、cursor、consumer_gap 和 host_history_evicted。后者是服务历史队列累计淘汰数，包含已经被消费者读取的旧记录，不能当作丢样数；旧名称 host_dropped 保留为同值兼容别名。consumer_gap 表示该次请求的游标已落后于保留窗口。板端序号缺口、deadline_missed、ring_dropped 和每消费者缺口须分别分析。

板端独立数据连接为 TCP3244，RMS1 + 一次性随机票据绑定控制会话；公开 SDK 由服务代为处理，前端无需直接连接。错误票据、重复绑定和 owner 释放后的连接被拒绝/关闭。

有线数据走0x81/16 USB_PULL：请求frame_id(uint32 LE，初始1)、offset(uint16 LE)。回应为command/sub/status、frame_id32、total16、offset16、chunk_len8和最多52 B数据。total=0为空轮询，不推进frame_id；每帧最多384 B，重复读取相同id/offset稳定，下一帧id递增并跳过0，旧id／跳号拒绝。USB_PULL与TCP数据绑定互斥。服务组帧后使用同样CRC／schema和本地stream接口；分片格式不是标准CMSIS-DAP命令，标准软件仍使用虚拟USB。

帧头为 32 B：RML1、version、kind、subscription_id、flags、capture_id、revision、sequence、payload_len、record_count、header_len、payload CRC32。内存样本头为 24 B：seq32、timestamp_us64、duration_us32、valid_bitmap32、sample_flags16、data_len16，之后是每字段 uint32 原始值。sample_flags：bit0 partial、bit1 执行预算超限、bit2 Watch 复用。RTT 数据部分为 channel32 + byte_sequence64 + length32 + bytes。

每记录独立带帧头，10×32-bit/1 kHz 为约 96 KB/s，再加 TCP 开销；这是格式预算。探针缓存：Timeline48 KiB、Watch4 KiB、两个 RTT 各6 KiB，总64 KiB。内存缓存满时有界丢旧并计数，最多回收8条；若并发发送仍占用空间则丢新，绝不阻塞SWD。RTT无空间不消费目标缓冲。服务最多保留4096条记录，各消费者游标独立。服务端原始录制见下文；应用确认重传尚未实现。

timestamp表示探针单调时基，duration是采集跨度，变量不是原子快照。Orbit实验分支用首帧对齐主机时间并保留探针间隔；SDK提供TIME_SYNC映射供其他客户端使用，Orbit尚未使用其绝对时间映射。断电/断线末尾丢失可能无法精确计数。

`POST /v1/devices/{device_id}/time-sync`，body `{"samples":8}`（1..16），不创建会话、不占目标，可与现有owner共存。固件0x81/13回显随机token并返回执行器接收／回复时间；服务记录主机往返，选择最小不确定度的映射，返回probe_us、host_monotonic_ns、offset_ns、uncertainty_ns和全部测量。64-bit主机时间以十进制字符串返回。探针重启被观察到时更换clock_epoch；服务重启也换epoch。host_wall_time_ns在检测到明显系统时间跳变时为null。误差范围包含剩余往返的一半及1 us量化，不代表单程延迟已测或已补偿时钟漂移；客户端需周期重测并识别epoch。

## 通用烧录接口

`POST .../flash`：action=begin/chunk/commit/cancel；`GET .../flash` 查询进度。当前仅支持 F103C8 主 Flash 的完整1 KiB页镜像，不支持选项字节、部分页保留或通用 ELF 上传。

1. begin：address、byte_length（最多64 KiB、均按1 KiB页对齐）、sha256。
2. chunk：offset、data hex，每片最多4096 B。重叠内容不一致拒绝。
3. commit：先检查覆盖/哈希、芯片容量，再停止订阅、加载RAM算法、逐页擦除/编程/校验、主机独立回读哈希、选项字节核对和软件复位。保留新程序。
4. cancel：请求有界取消。正在执行的写入可能完成；失败时尝试暂停核心，不重放、不自动恢复旧程序。

提交后 complete/failed/cancelled 的上传不能再次 commit，必须 begin 一个新操作。HTTP 超时不代表操作未执行；查询状态后决定下一步。算法从 Orbit 固定提交独立构建，来源和 MIT 许可在 `host/algorithms/stm32f103c8/`。

## 服务端录制

`POST .../recordings`：`{"subscription_ids":[1,2,3],"strict":true}`；订阅须已存在，省略 subscription_ids 选择当前全部订阅，strict 默认 true。返回 recording_id、path、phase、frames、bytes、gaps、invalid_samples、error、finalized。每个会话最多一个正在录制或收尾的文件；生成文件名位于当前用户 `.rmlink/recordings`，客户端不能指定覆盖其他文件。

`GET .../recordings` 返回会话当前/最近一次录制，`GET .../recordings/{id}` 查询状态；`POST .../recordings/{id}` 的 `{"action":"stop"}` 排空已接收队列、写入尾部并执行 fsync。应先停止录制，再删除订阅或关闭会话。界面无需持续读 events，原始帧在服务历史淘汰前送给独立录制器。

严格模式遇到板端序号缺口、RTT 字节序号缺口、partial/预算超限样本即失败，暂停所选运行任务；写队列满、存储失败、采集流失败和会话关闭也明确失败，不自动补写或重放目标操作。文件 finalized=true 只表示容器完整结束；还须 phase=complete、error=null 才能作为成功录制。strict=false 保留带缺口/无效标记的实时数据，仍记录统计，存储异常仍失败。

RMLR v1 文件保存配置、原始 RML1 帧与服务游标以及最终统计。`scripts/inspect_recording.py FILE` 流式检查长度、每帧 CRC、完整尾部及帧数，给出整个文件 SHA256。详见 [录制格式](recording-format.md)。当前录制窗口始于订阅已经启动之后，结束于停止入口前已被服务接收的记录；不保证订阅第一帧或停止时仍在网络/探针中的末尾数据。TCP 可靠传输不等于断电后完整录制，尚无应用确认、断线续传或文件分段轮转。

## SDK 与验证范围

- Python：`host/rmlink_connector/client.py`；示例 `host/examples/capture.py`。flash_image(data) 封装上传与提交。
- Node/TypeScript：`host/sdk/` 的 `@rmlink/client`；订阅、取消、二进制帧 CRC 与 typed sample；flashImage(data) 走同一公开API。
- C ABI：`host/sdk/c/rmlink.h`，Build-Sdk.ps1 生成 DLL、MinGW import library、DEF 和头文件。MSVC 可用 `lib /def:rmlink.def /machine:x64 /out:rmlink.lib` 生成导入库。rm_open(NULL,...) 配合 rm_devices／rm_capabilities 查询设备，不创建会话或占用目标；传 device_id 才建立带心跳的会话。回调帧内存只在回调期间有效；取消流后需等待流线程结束再关闭句柄。rm_call 公开通用目标和烧录路由，输出缓冲不足不重放请求。

当前证据分开记录：主机协议/模型/实际WinHTTP SDK、ESP32构建/刷写/启动、真实 STM32 采样/烧录/标准调试、Orbit独立工作树构建及回归。已测的 ESP32 本地 SWD 吞吐是该实现、该请求时钟下的结果；安装后的 VS Code 界面、物理 NRST、其他芯片、Keil/IAR 与正式签名分发仍须独立验收。
