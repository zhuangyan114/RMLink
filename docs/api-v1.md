# RMLink 公开 API（v1）与 SDK

本机连接服务启动后，在 `http://127.0.0.1:3243/v1` 提供控制与二进制流接口。客户端使用 `Bearer` Token 鉴权（保存在当前用户受 ACL 保护的 `%USERPROFILE%\.rmlink\connector.json`，公开 SDK 会自动读取，不要把 Token 写进日志或暴露给外部网页）。

> 标准无线 CMSIS-DAP 由 OpenOCD 直连探针 TCP `3241`，不经过本机 HTTP 接口；本页对应增强模式（`probe: "rmlink"`）和第三方 SDK 接入。完整烧录线协议见 [缓存烧录协议](buffered-flash-protocol.md)，录制文件格式见 [录制格式](recording-format.md)，目标范围见 [统一目标算法包](shared-target-package.md)。

## 设备、会话与传输

- `GET /v1/capabilities`：返回 API 版本、支持的目标命令、订阅类型及字段/周期上限。
- `POST /v1/sessions`：传入 `{"device_id":"..."}` 创建租约，返回 `session_id`、`heartbeat_seconds`（5 秒）、`expiry_seconds`（30 秒）和协商的 `packet_size`。单台服务最多允许 64 个租约，每台物理探针同时只有一个控制 owner。
- `DELETE /v1/sessions/{id}`：停止该会话名下订阅、清理本会话临时断点并释放 owner。
- `POST /v1/transport`：传入 `{"device_id":"...","transport":"usb"|"wifi"}` 切换传输方式，仅在该设备没有活动 owner 且无未关闭租约时允许执行。偏好会持久化到配置中。

在 Orbit 中使用 `"probe": "rmlink"` 时，如果恰好只有一台已配对在线设备可省略 `rmlinkDeviceId`，多台设备时返回 `AmbiguousDevice`。若目标已被其他会话占用，`target/connect` 返回 HTTP `409`（`code: "TargetBusy"`、`write_completion_may_be_unknown: false`），不会抢占现有会话。

## 高层目标操作

通过 `POST /v1/sessions/{id}/target` 发送 `{"command": "...", ...}`。`connect` 默认请求 `clock_hz=4000000`。增强会话拿到目标控制权后，探针原始 `/dap` 入口会被拒绝，避免双端同时修改 MEM-AP 寄存器缓存。

| 命令 | 说明与约束 |
| --- | --- |
| `connect` | 可选 `clock_hz`；核对目标 DP/CPUID/DEV_ID 和算法包配置 |
| `state` | 读取 `DHCSR` 与 `Running`/`Halted`。支持 `0x81/0` bit4（`CORE_STATE`，命令 `0x81/17`）时走单次只读查询，不跨无线往返持锁 |
| `halt` / `run` / `step` / `reset` | 均带有限状态确认；`reset` 通过 `VC_CORERESET` + `SYSRESETREQ` 停在复位态并保留现有订阅，随后 `run` 自动恢复由控制暂停的订阅 |
| `run_to_address` | 指定 Flash 地址（支持 Thumb 位，默认先 `reset=true`），使用空闲 FPB 槽跑至入口后恢复原比较器与 `DEMCR`；无空闲槽或超时报 `409` |
| `read_memory` / `write_memory` | 指定 `address` 与 `byte_length` / `data`（hex），单次最多 4096 B，按 1 KiB 边界自动分段；写操作按对齐字执行，未知写入不重放 |
| `read_batch` | `reads` 数组（`1..32` 段，总量最多 4096 B） |
| `read_register` / `write_register` | 寄存器索引 `0..20`，要求目标处于暂停态 |
| `set_breakpoint` / `clear_breakpoint` | Flash FPB 硬断点，仅使用空闲比较器 |
| `benchmark_local` | ESP32 本地计时批量读取测试（`read_count <= 10000`、`field_count <= 32`，最长约 500 ms） |
| `measure_swd_clock` | 需已连接且暂停采样，`mode=sequence/dp_read` 返回 80 MHz RMT 内部参考周期统计（不能替代外部示波器波形量测） |

> 固件支持 `0x81/0` bit7 时（命令 `0x81/20`），针对已知 RAM/Flash 窗口且单次不超过 `min(128, packet_size - 3)` 字节的 `read_memory` / `read_batch` 会走有界快照路径，不跨网络持锁。外设寄存器和旧固件仍走标准控制屏障。

## Watch / Timeline / RTT 订阅

通过 `POST /v1/sessions/{id}/subscriptions` 创建，请求体示例：

```json
{"kind": "memory_series", "addresses": [536871168, 536871232], "period_us": 1000}
```

```json
{"kind": "memory_snapshot", "fields": [{"address": 536871808, "offsets": [0, 0, 4], "byte_length": 4}], "period_us": 100000}
```

```json
{"kind": "rtt_stream", "control_block_address": 536871936, "channel": 0, "period_us": 5000}
```

- **槽位与参数范围**：固定槽位为 Timeline=`1`、Watch=`2`、RTT 通道 0=`3`、RTT 通道 1=`4`（每槽同时一个任务）。每次最多 `32` 个字段，请求周期 `200..10000000` µs（仅代表可接受输入范围，实际能否跑满取决于 SWD 总线耗时）。
- **指针链与对齐**：`fields` 支持 `byte_length` `1/2/4` 和最多 `4` 层 `offsets`。每层先读 4 字节对齐的 32 位指针再加偏移，最后一跳读取目标值。空指针或越界只让当前字段标为无效，不影响同帧其余字段；发生 SWD 通信错误则暂停任务。
- **Watch 复用 Timeline**：当 Watch 与运行中的 Timeline 包含完全一致的逻辑字段（根地址、各级偏移、宽度一致），且 Timeline 样本有效、距今未超过 Watch 周期时，板端直接复用，样本 `flags bit2` 置位；控制屏障或重新配置会清除复用状态。
- **`strict_deadlines`**：默认 `false`。来不及采样的周期会递增序号并累计 `deadline_missed`，帧标记 `flags bit1`；设为 `true` 时超预算直接暂停并报告 `budget_overrun`。
- **控制与重建**：`POST .../subscriptions/{id}` 支持 `action=stop/pause/resume`；`DELETE` 停止并移除注册。控制暂停（`halt`/`step`）会在 `run` 时恢复，用户显式 `pause` 不会自动恢复。
- **断流重建**：如果只是数据流断开而控制 owner 还在，`stream_error` 会记录原因、关闭旧流并结束当前录制（标记 `failed`）。此时可在同一控制会话内 `DELETE` 订阅再重新 `POST` 创建（递增 `stream_cursor` 与 `capture_id`），客户端重新打开本地流按新 `capture_id` 过滤即可。

### 实时优先流（`realtime` 与 `RMS4`）

仅无限采样且 `strict_deadlines=false` 的 `memory_series` 允许设置 `"realtime": true`：
- **断流检测**：新建或 `resume` 后有 **5 秒宽限期**；之后只要该 `capture_id` 连续 `max(2.5 秒, 3 个采样周期)` 没有收到完整帧，服务主动关闭独立 TCP 数据流并报告 `Realtime capture stalled`（避开 1.5 秒基础 TCP 重传窗口；目标暂停或显式 `pause/stop` 期间不计时；RTT 或旧 `capture_id` 数据不算进展）。USB 流不启用此检测。
- **重采退避**：Orbit 侧自动重采后，需连续收到至少 **3 秒**、帧间隔不超过 `max(1 秒, 3 个采样周期)` 的当前样本，才重置退避计时。
- **与 RMLR 录制互斥**：启用 `realtime` 或共用 `RMS4` 数据流的会话不允许发起 RMLR 严格/非严格录制；需要完整录制时需先删除相关订阅并以 `realtime=false` 重建。

## 二进制流、压缩协议与板端缓冲

- **本机消费接口**：
  - `GET /v1/sessions/{id}/stream?after={cursor}`：HTTP chunked `application/vnd.rmlink.stream`，每条记录为 `cursor(u64 LE) + frame_length(u32 LE) + frame`（`length=0` 为保活心跳，帧上限 384 B）。
  - `POST /v1/sessions/{id}/events`：JSON 拉取（`after`、`limit=1..256`、`wait_ms=0..5000`），返回 `records`、`cursor`、`consumer_gap` 和 `host_history_evicted`（服务历史队列总共 4096 条，已读旧记录正常淘汰也计入此数，不等同于硬件丢样）。
- **帧格式**：`RML1` 帧头 32 字节（含魔数、版本、类型、槽位、标志、`capture_id`、`revision`、起始序号、载荷长、条数、头长、载荷 CRC32）；内存样本头 24 字节（`seq32`、`timestamp_us64`、`duration_us32`、`valid_bitmap32`、`sample_flags16`、`data_len16`），随后是字段原始值；RTT 数据为 `channel32 + byte_sequence64 + length32 + bytes`。`sample_flags` 中 `bit0` 表示 partial，`bit1` 表示预算超限，`bit2` 表示 Watch 复用。

### 探针端传输协议协商（TCP `3244` / USB）

| 协议版本 | 能力位 | 说明 |
| --- | --- | --- |
| `RMS1` | 默认 | 原始 `RML1` 单帧发送 |
| `RMS2` | bit8 (`256`) | `RMC2` 分组打包（20 B 共享组头 + 最多 4096 B / 1..73 条），大幅减少每帧头部开销 |
| `RMS3` | bit9 (`512`) | `RMC3` 分组打包，支持按组内上一条样本逐字节 XOR 后做零游程压缩（载荷 24..352 B） |
| `RMS4` | bit12 (`4096`) | 仅在 `realtime=true` 新开 TCP 流时协商（需同时具备 bit9）。保留 `RMC3/RMC2` 编码，板端启用非阻塞发送与 `max(2.5s, 宽限剩余)` 批次总时限，超时即断流以免阻塞新采样 |
| `USB_PULL` | `0x81/16` | 有线模式备用拉取（每次最多 52 B 分片），新版固件优先使用独立 USB Bulk 连续流；与 TCP 数据绑定互斥 |

### 固件侧缓存与无线参数（0.3.4 起）

- **32 KiB 固定片内采样环**：Timeline 16 KiB、Watch 4 KiB、两个 RTT 通道各 6 KiB，全部静态分配在内部 DRAM，不依赖 PSRAM。满环时最多丢弃 8 条旧数据以腾出空间（若发送仍占着则丢新帧），绝不阻塞 SWD 执行。
- **窄超时与防积压调优**：lwIP TCP tick 设为 `50 ms`、初始 RTO 为 `300 ms`、默认发送缓冲 `5744 B`，Wi-Fi 动态/缓存队列上限均为 `16`。支持 1 秒 STA 短暂重连宽限（同一 BSSID 且 IP 未变时保留现有控制与采集会话）。
- **服务侧流诊断**：启动连接服务前设置 `RMLINK_STREAM_DIAGNOSTICS=1`，每 10 秒打印一次接收字节/帧、等待耗时、本机 TCP 计数和控制屏障耗时（不含 Token、密码和样本明文）。

### 时间同步：`POST /v1/devices/{id}/time-sync`

请求 `{"samples": 8}`（`1..16`），通过 `0x81/13` 测算探针微秒时基 `probe_us` 与主机单调时钟 `host_monotonic_ns` 的偏移和半往返不确定度 `uncertainty_ns`（不占目标控制权）。重启探针或服务会更换 `clock_epoch`。

## 缓存烧录、健康检查与录制

- **缓存烧录（`POST/GET .../sessions/{id}/flash`）**：`action=begin/chunk/commit/cancel`，支持 `backend=auto/host/probe` 与 `verify_on_host`。`probe` 路径先把镜像完整传到 ESP32，再由探针自主加载目标 RAM 算法完成擦除、编程、全镜像比对并复位运行（支持统一算法包覆盖的四款 STM32）。`cancel` 触发有界取消，已发出的写入不回退也不重放。详见 [缓存烧录协议](buffered-flash-protocol.md)。
- **设备健康（`GET /v1/devices/{id}/health`）**：免会话查询内部 RAM / PSRAM 余量、请求 SWD 时钟、最近采集 IO 错误及只读 `WDATAERR` 恢复统计（`read_fault_recovery`）。
- **服务端录制（`POST/GET .../sessions/{id}/recordings`）**：生成保存在 `%USERPROFILE%\.rmlink\recordings` 的 `RMLR v1` 文件。`strict=true` 时只要出现序号缺口、partial 或预算超限就立刻标失败并停采；`strict=false` 允许带缺口继续录制。停止时需调 `action=stop` 写入文件尾并 `fsync`。可用 `RMLinkConnector.exe inspect-recording <文件>` 或 `scripts/inspect_recording.py` 离线校验每帧 CRC 与文件摘要。

## 公开 SDK 入口

| 语言 | 位置与主要接口 |
| --- | --- |
| **Python** | `host/rmlink_connector/client.py`（示例 `host/examples/capture.py`），`flash_image(data)`、`device_health()` |
| **Node / TypeScript** | `host/sdk/`（`@rmlink/client`），含类型声明、二进制帧解码、CRC 校验、`flashImage(data)` 与 `AbortSignal` 取消 |
| **C ABI** | `host/sdk/c/rmlink.h`（通过 `scripts/Build-Sdk.ps1` 生成 DLL / `.def` / MinGW 导入库，MSVC 可用 `lib /def:rmlink.def /machine:x64 /out:rmlink.lib`）。`rm_open(NULL, ...)` 配合 `rm_devices` 不占目标；传 `device_id` 建立带心跳会话；`rm_call` 暴露通用路由 |
