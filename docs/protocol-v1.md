# RMLink 开发协议 v1

本页说明 RMLink 的底层线协议：标准 CMSIS-DAP（USB / TCP `3241`）、SWD 执行约束、厂商命令 `0x80`（设备信息与 USB 配网）以及临时测速端口 `3242`。

> 衍生协议各自独立成文：板端订阅与流格式见 [API v1 说明](api-v1.md)，缓存烧录 `0x82` 见 [缓存烧录协议](buffered-flash-protocol.md)，OTA 升级 `0x83` 见 [OTA 升级机制](ota-upgrade.md)。2026-10-19 起无线标准调试改走 OpenOCD TCP，已停止虚拟 USB 路线（见 [当前项目方向](current-direction.md)）。

## 标准 CMSIS-DAP 与传输

- **原生 USB**：使用 Bulk OUT `0x03` / IN `0x83`，逻辑包上限固定为 `64` 字节（多包组装未启用）。HID、SWO 和 Atomic Commands 均未启用。
- **TCP（默认端口 `3241`）**：支持编译期配置 `64 / 128 / 256 / 512` 字节容量（RMLink V1 默认 `512` 字节）。`DAP_Info 0xFF` 返回当前传输的实际包容量，`0xFE` 声明排队深度为 `8`。

TCP 帧在原始 DAP 数据前加 8 字节头（长度字段不含头本身）：

| 偏移 | 长度 | 内容 |
| :---: | :---: | --- |
| 0 | 4 | `44 41 50 00`（`DAP\0`） |
| 4 | 2 | little-endian 长度，`1..当前 TCP 容量`（最大 512） |
| 6 | 1 | `1` = 请求，`2` = 响应 |
| 7 | 1 | 保留字段 `0` |
| 8 | `length` | 原始 DAP 请求或响应 |

TCP 接收器支持粘包与拆包；帧头非法直接关闭连接。单次部分读写的 socket 超时为 2 秒，只要收到新分片就重新计时。`DAP_TransferAbort`（`0x07`）在接收路径直接生效，不进执行队列也不回帧，被取消的传输按实际完成数返回。如果无线断连，ESP32 不会重启，旧 TCP 句柄会关闭或超时；设备端保存 Wi-Fi 参数，断开后会自动重连，mDNS 主机名为 `rmlink-<MAC后三字节>.local`（服务类型 `_rmlink._tcp.local`）。

## SWD 支持范围与执行规则

- 支持标准命令 `0x00..0x0A`、`0x10..0x13`、`0x1D`。未实现命令统一返回单字节 `0xFF`。
- `DAP_Transfer` / `DAP_TransferBlock` 带有原子取消标志和 1 秒超时，失败时如实返回已完成的传输数量和 ACK/匹配状态。
- 请求时钟支持 `1 kHz..10 MHz`；`DAP_SWJ_Pins` 等待上限 1 秒。
- `DAP_ResetTarget` 在 NRST 脚（GPIO6）输出 10 ms 开漏脉冲；若未接物理 NRST 线，常规复位通过写 `AIRCR.SYSRESETREQ` 完成。

## 厂商命令 `0x80`（信息、网络与配网）

所有 `0x80` 响应前三个字节固定为 `[0x80, subcommand, status]`：`status=0` 表示成功，`1` 表示参数或执行失败，`2` 表示当前传输不允许（例如某些操作仅限 USB）。

| 子命令 | 请求格式 | 响应与行为 |
| :---: | --- | --- |
| `0` | `80 00` | 返回 17 字节设备信息（布局见下表） |
| `2` | `80 02 field offset total chunkLength data...` | 暂存 Wi-Fi 参数分片：`field=0` 为 SSID，`1` 为密码（仅 USB） |
| `3` | `80 03` | 校验两端点字段完整后写入 NVS，自动恢复启用 Wi-Fi 并通知 STA 任务重连（仅 USB） |
| `4` | `80 04` | 返回 10 字节：`[80, 04, 00, configured, connected, rssi, ip0..ip3]`（`rssi` 为 int8） |
| `5` | `80 05` | 主动断开 STA 触发自动重连（仅 USB 且 Wi-Fi 已启用时有效，用于验证恢复） |
| `6` | `80 06` | 返回 15 字节：前缀 + 启动微秒数（u64 LE）+ ESP-IDF reset reason（u32 LE） |
| `7` | `80 07 seqLE32` | 返回当前传输容量的诊断包（前缀 + 原 `seq` + 确定性填充），不访问目标 |
| `8` | `80 08 secLE16` | 仅 USB 且无目标占用时有效：`0` 关闭端口 `3242` 测试服务，`1..300` 临时开启对应秒数 |
| `9` | `80 09` | 返回 9 字节：`[80, 09, 00]` + 6 字节 STA MAC（USB / TCP 均只读可用） |
| `18` | `80 12` | 返回 12 字节只读链路状态：`[80, 12, 00, schema=1, wifi_enabled, wifi_configured, wifi_connected, usb_mounted, active_transport, owner_transport, busy_flags, capabilities]`（`active/owner`: `0` 无/`1` USB/`2` TCP；`busy_flags`: `bit0` owner/`bit1` flash/`bit2` ota；`capabilities`: `bit0` 双通保底、`bit1` 支持 `0x80/19`） |
| `19` | `80 13 enable` | 仅空闲时（无 owner/flash/ota）开关板端 Wi-Fi（`0` 关闭、`1` 开启；USB 常开不断开；通过 TCP 关闭时延迟 150 ms 生效以便先回传 ACK；忙时返回 `status=3`） |

`0x80 00` 信息布局：

| 偏移 | 长度 | 内容 |
| :---: | :---: | --- |
| 0 | 3 | `80 00 00` |
| 3 | 1 | 协议版本（当前为 `1`） |
| 4 | 3 | 固件 `major / minor / patch` |
| 7 | 2 | u16 LE 能力位：`bit0` USB、`bit1` TCP、`bit2` USB 配网 |
| 9 | 1 | 是否允许目标 GPIO（`0/1`） |
| 10 | 1 | 当前目标 owner：`0` 无、`1` USB、`2` TCP |
| 11 | 4 | u32 LE 当前 owner session（无则为 `0`） |
| 15 | 2 | u16 LE 当前传输的 DAP 包容量 |

### USB 配网分片规范

`80 02` 请求中 `field / offset / total / chunkLength` 各占 1 字节（偏移 `2..5`），有效字节紧随其后（单包最多 58 字节，主机端默认每片最多 48 字节）。按 UTF-8 字节长度校验：SSID 为 `1..32` 字节，密码为 `8..63` 字节（开放网络允许长度 `0`），不能包含 NUL 字节。提交成功或发生 USB 复位/拔出时会清空暂存，因此每次配网应从 `offset=0` 重新发送完整字段。

## 会话占用与主机流水线

- **占用规则**：`DAP_Info`、`DAP_HostStatus` 和 `0x80` 信息/网络查询不占用目标。在目标 GPIO 启用的固件上，`DAP_Connect` 或有效的 `DAP_SWJ_Clock` 会申请整段控制会话（兼容 pyOCD 在 Connect 之前设时钟的做法），之后所有目标命令必须来自同一传输和会话，直到 `DAP_Disconnect`、USB 复位/拔出或 TCP 断线释放。
- **流水线与 1 KiB 边界**：主机可将 1..8 条独立 `DAP_Transfer` 打包提交（`exchange_many`），每包自带 `TAR` 和读写且不跨越 MEM-AP 1 KiB 自动递增边界。在容量 `P` 下，单包最多容纳 `floor((P-3)/4)` 个读字或 `floor((P-8)/5)` 个写字（`TransferBlock` 为 `floor((P-4)/4)` 读 / `floor((P-5)/4)` 写）。写入失败且完成状态未知时，主机不自动重放。

## 临时测速端口 `3242`

仅在通过 USB `80 08` 显式启用后监听（最长 300 秒），与调试和采样隔离。请求固定 16 字节头：`[RMB1 (4B)][mode LE32][size LE32][seed LE32]`：
- `mode=1`：`size` 为 `1..16 MiB`，板端连续回传 `size` 字节的 `(偏移 + seed) & 255` 数据流。
- `mode=2`：`size` 为 `1..1024`，头后紧跟 `size` 字节载荷，板端原样回显。

非法请求直接断开，部分读写超时 2 秒。
