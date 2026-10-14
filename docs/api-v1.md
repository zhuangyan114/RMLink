# RMLink 公开连接组件 API v1.1（开发版）

适用于固件/服务 0.2.2。三订阅、高层控制和烧录已实现源码与主机模型；真实 ESP32 的流绑定/错误路径、时间同步及有线分片回传已验证。当前 STM32 无应答，4 MHz 全流程、新采样率和新烧录引擎尚未通过实板验收。标准虚拟 USB 的历史验收不等同于本版增强能力验收。

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
| state | 返回 dhcsr 与 Running/Halted |
| halt / run / step / reset | 有界状态确认；reset 为 SYSRESETREQ，物理 NRST 不在当前验收内 |
| read_memory | address、byte_length，最多 4096 B；返回 data hex |
| read_batch | reads 数组，1..32 个范围，总量最多 4096 B |
| write_memory | address、data hex，按对齐 word 写，最多 4096 B；按包和 1 KiB 边界独立设置地址 |
| read_register / write_register | index 0..20、可选 value；核心必须暂停 |
| set_breakpoint / clear_breakpoint | F103 Flash FPB 地址 / id；只使用空比较器 |
| benchmark_local | address、stride、read_count≤10000、field_count≤32；ESP32 本地计时连续块/离散组读，最长约 500 ms |

目标控制通过同一 owner 和控制屏障执行。halt/step 使 Timeline 暂停，run 只恢复由控制操作暂停的订阅；用户显式暂停不会自动恢复。复位和烧录先撤销所有旧订阅，重新配置地址/RTT 后才开始新的采集。

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

当前固定槽：Timeline=1、Watch=2、RTT channel0=3、第二个 RTT channel=4。每类槽只允许一个订阅，字段最多 32，周期 200..10000000 us；这些是输入范围，不保证对应采样率。完整帧执行超过周期时暂停并报告 budget_overrun。

返回 subscription_id、capture_id、接受的配置。`GET .../subscriptions/{id}` 返回 state、kind、completed、deadline_missed、ring_dropped、failures、budget_overrun、stop_reason 和服务缓存状态。completed 对内存订阅为已生成帧数（含 partial），RTT 为已提交字节数。

`POST .../subscriptions/{id}` 支持 action=stop/pause/resume；`DELETE` 停止并移除服务注册。配置更新首版采用停止/删除/重新创建，不提供运行中原子 PATCH。重新创建产生新的 capture_id；不能把旧缓存的帧当作新任务数据。

Watch 静态 32-bit 重合字段复用正在运行的 Timeline；Timeline 停止/暂停后，Watch 自身读取继续。复用标记为 sample flags bit2，时间戳与跨度包含较早源样本时间。RTT Up 根据控制块和环形描述符读取、提交 RdOff；完成状态未知时保留待提交记录并暂停，显式恢复前重读偏移。目标端已丢弃的日志数量不可从环形缓冲精确推断。

## 二进制流与缓存

`GET /v1/sessions/{id}/stream` 使用相同 Authorization，返回 HTTP chunked 的 `application/vnd.rmlink.stream`。每个本地记录是 cursor(uint64 LE) + frame_length(uint32 LE) + frame；length=0 是保活，不是样本。帧最大 384 B，慢消费者可从 cursor 跳变识别服务缓存缺口。

板端独立数据连接为 TCP3244，RMS1 + 一次性随机票据绑定控制会话；公开 SDK 由服务代为处理，前端无需直接连接。错误票据、重复绑定和 owner 释放后的连接被拒绝/关闭。

有线数据走0x81/16 USB_PULL：请求frame_id(uint32 LE，初始1)、offset(uint16 LE)。回应为command/sub/status、frame_id32、total16、offset16、chunk_len8和最多52 B数据。total=0为空轮询，不推进frame_id；每帧最多384 B，重复读取相同id/offset稳定，下一帧id递增并跳过0，旧id／跳号拒绝。USB_PULL与TCP数据绑定互斥。服务组帧后使用同样CRC／schema和本地stream接口；分片格式不是标准CMSIS-DAP命令，标准软件仍使用虚拟USB。

帧头为 32 B：RML1、version、kind、subscription_id、flags、capture_id、revision、sequence、payload_len、record_count、header_len、payload CRC32。内存样本头为 24 B：seq32、timestamp_us64、duration_us32、valid_bitmap32、sample_flags16、data_len16，之后是每字段 uint32 原始值。sample_flags：bit0 partial、bit1 执行预算超限、bit2 Watch 复用。RTT 数据部分为 channel32 + byte_sequence64 + length32 + bytes。

每记录独立带帧头，10×32-bit/1 kHz 为约 96 KB/s，再加 TCP 开销；这是格式预算，不是已测采样率。探针缓存：Timeline48 KiB、Watch4 KiB、两个 RTT 各6 KiB，总64 KiB。0.2.1的内存缓存满时有界丢旧并计数，最多回收8条；若并发发送仍占用空间则丢新，绝不阻塞SWD。RTT无空间不消费目标缓冲。服务最多保留4096条记录，各消费者游标独立。严格录制和应用确认重传尚未实现。

timestamp表示探针单调时基，duration是采集跨度，变量不是原子快照。Orbit实验分支用首帧对齐主机时间并保留探针间隔；SDK提供TIME_SYNC映射供其他客户端使用，Orbit尚未使用其绝对时间映射。断电/断线末尾丢失可能无法精确计数。

`POST /v1/devices/{device_id}/time-sync`，body `{"samples":8}`（1..16），不创建会话、不占目标，可与现有owner共存。固件0x81/13回显随机token并返回执行器接收／回复时间；服务记录主机往返，选择最小不确定度的映射，返回probe_us、host_monotonic_ns、offset_ns、uncertainty_ns和全部测量。64-bit主机时间以十进制字符串返回。探针重启被观察到时更换clock_epoch；服务重启也换epoch。host_wall_time_ns在检测到明显系统时间跳变时为null。误差范围包含剩余往返的一半及1 us量化，不代表单程延迟已测或已补偿时钟漂移；客户端需周期重测并识别epoch。

## 通用烧录接口

`POST .../flash`：action=begin/chunk/commit/cancel；`GET .../flash` 查询进度。当前仅支持 F103C8 主 Flash 的完整1 KiB页镜像，不支持选项字节、部分页保留或通用 ELF 上传。

1. begin：address、byte_length（最多64 KiB、均按1 KiB页对齐）、sha256。
2. chunk：offset、data hex，每片最多4096 B。重叠内容不一致拒绝。
3. commit：先检查覆盖/哈希、芯片容量，再停止订阅、加载RAM算法、逐页擦除/编程/校验、主机独立回读哈希、选项字节核对和软件复位。保留新程序。
4. cancel：请求有界取消。正在执行的写入可能完成；失败时尝试暂停核心，不重放、不自动恢复旧程序。

提交后 complete/failed/cancelled 的上传不能再次 commit，必须 begin 一个新操作。HTTP 超时不代表操作未执行；查询状态后决定下一步。算法从 Orbit 固定提交独立构建，来源和 MIT 许可在 `host/algorithms/stm32f103c8/`。

## SDK 与验证范围

- Python：`host/rmlink_connector/client.py`；示例 `host/examples/capture.py`。flash_image(data) 封装上传与提交。
- Node/TypeScript：`host/sdk/` 的 `@rmlink/client`；订阅、取消、二进制帧 CRC 与 typed sample；flashImage(data) 走同一公开API。
- C ABI：`host/sdk/c/rmlink.h`，Build-Sdk.ps1 生成 DLL、MinGW import library、DEF 和头文件。MSVC 可用 `lib /def:rmlink.def /machine:x64 /out:rmlink.lib` 生成导入库。rm_open(NULL,...) 配合 rm_devices／rm_capabilities 查询设备，不创建会话或占用目标；传 device_id 才建立带心跳的会话。回调帧内存只在回调期间有效；取消流后需等待流线程结束再关闭句柄。rm_call 公开通用目标和烧录路由，输出缓冲不足不重放请求。

当前证据分开记录：主机协议/模型/实际WinHTTP SDK、ESP32构建/刷写/启动、真实ESP32错误路径与USB取消、Orbit独立工作树构建及回归。STM32纯SWD极限、1 kHz和三订阅共存、增强烧录、安装后的VSCode实际运行均待验证。
