# RMLink 开发协议 v1

当前开发固件 0.2.11，更新：2026-10-15。本页描述标准 DAP 承载与 0x80 设备管理接口；板端采样、指针解析、离散地址读取和三类订阅已实现。

已实现的 Watch / Timeline / RTT 使用 0x81 控制、TCP3244 或原生 USB_PULL 回传，字段、命令和公开接入见 [API](api-v1.md)，架构见 [订阅方案](subscription-design.md)。实现和验收范围见 [进度](development-status.md)，不能把支持的周期范围当作实测采样频率。

## 标准 DAP 与传输

原生 USB Bulk OUT 0x03、IN 0x83，最大 DAP 逻辑包仍为 64 字节。TCP 可构建为 64 / 128 / 256 / 512 字节，虚拟 USB 使用对应 TCP 容量；两种传输进入同一个 SWD 内核。DAP_Info 0xFF 及定制状态返回当前传输的真实容量，0xFE 声明排队容量 8。主机先查询容量，再据此组织命令、接收缓冲和边界检查；开发 WinUSB 工具的首次查询使用短 OUT，后续 OUT 填充至协商容量，IN 返回实际响应长度。虚拟 Full-Speed USB 端点物理包为 64 字节，UDE URB 可承载较大的逻辑包；尚未实现原生 USB 的多包组装。HID、SWO、Atomic Commands 未实现 / 启用。

TCP 默认端口 3241，8 字节头如下。所有长度为当前帧 DAP 数据长度，不包括头。

| 偏移 | 长度 | 内容 |
| --- | --- | --- |
| 0 | 4 | 44 41 50 00，即 DAP + NUL |
| 4 | 2 | little-endian 长度，1..当前 TCP 容量，最高 512 |
| 6 | 1 | 1 = 请求，2 = 响应 |
| 7 | 1 | reserved = 0 |
| 8 | length | 原始 DAP 请求 / 响应 |

接收器支持 TCP 分片和一个 TCP 写入中连续多个帧；头无效则关闭该连接。部分帧读 / 写设有 2 秒 socket 超时，收到有进展的分片时该超时重新计时。普通请求进入唯一 SWD 执行器。TCP 收发独立，0x07 在接收路径取消所属活动传输，不排队、不回复 TCP 帧；原传输返回实际数量。发生断连时，进行中的写入完成状态可能未知；主机工具不自动重放请求。

网络下线不重启 ESP32；旧 TCP 流可能在主机表现为关闭、复位或超时，需要建立新连接。设备保存热点参数，开机自动加入。mDNS 主机名为 rmlink- + MAC 后三字节，例如 rmlink-15a924.local；服务为 _rmlink._tcp.local。Windows 连接组件已实现发现与连接，以完整 MAC 校验设备身份，再使用发现的 IPv4 建立 TCP；详见 windows-connector.md。

## SWD 命令与异常

SWD 路径支持标准命令 0x00～0x0A、0x10～0x13 和 0x1D；0x07 无响应，应调用 abort_transfer()，不能用等待回复的 exchange()。未实现命令返回单字节 0xFF，包含关闭的 Atomic / JTAG / SWO / UART。

Transfer / TransferBlock 使用原子取消标志及 1 秒执行期限，保留实际数量 / ACK / 匹配状态。时钟请求范围 1 kHz～10 MHz，SWJ_Pins 等待上限 1 秒；实际 SWCLK 待仪表确认。TCP 断连使旧会话失效，取消活动传输并清理旧排队任务；屏障完成后再释放回复上下文，外来 Abort 被忽略，不自动重放未知完成状态的写入。

DAP_ResetTarget 为 GPIO6 的 10 ms 开漏脉冲，实现返回值不证明目标已复位。本轮缺 NRST 线，常规复位通过标准 DAP_Transfer 写 AIRCR.SYSRESETREQ 完成；实体复位待验收。

## 定制命令

统一前缀 0x80。所有命令回复前三字节均为 [0x80, subcommand, status]；status 0 成功，1 参数 / 操作失败，2 当前传输不允许或网络管理不可用。未提供 Wi-Fi 配置读取命令。

| 子命令 | 请求 | 响应 / 行为 |
| --- | --- | --- |
| 0 | 80 00 | 17 字节设备信息，见下表 |
| 2 | 80 02 field offset total chunkLength data... | 暂存热点参数片段；field 0 = SSID，1 = 密码；USB 专用 |
| 3 | 80 03 | 检查两个字段完整后保存一个 NVS blob，并通知 STA 管理任务；USB 专用 |
| 4 | 80 04 | 10 字节：[80,4,0,configured,connected,rssi,ip0,ip1,ip2,ip3]，RSSI 是 int8，IP 是网络顺序四字节 |
| 5 | 80 05 | 断开 STA，随后管理任务自动重连；USB 专用，用于网络恢复验证 |
| 6 | 80 06 | 15 字节：前三字节 + uint64 little-endian 启动以来微秒数 + uint32 little-endian ESP-IDF reset reason |
| 7 | 80 07 sequenceLE32 | 当前传输容量字节的诊断响应：[80,7,0] + 原序号 4 字节 + 容量减 7 字节确定性数据，不访问目标 |
| 8 | 80 08 secondsLE16 | USB 专用、无目标 owner 时允许；0 关闭测试服务，1..300 临时开启 TCP 3242；不访问目标 |
| 9 | 80 09 | 9 字节：[80,9,0] + STA MAC 的 6 个字节；USB / TCP 均允许，只读身份查询 |

信息响应布局：

| 偏移 | 长度 | 内容 |
| --- | --- | --- |
| 0 | 3 | 80 00 00 |
| 3 | 1 | 协议版本，当前 1 |
| 4 | 3 | 固件 major / minor / patch，当前开发版 0 / 2 / 11；按实际查询结果识别版本 |
| 7 | 2 | little-endian 能力；bit0 USB、bit1 TCP、bit2 USB 配网；当前 0x0007 |
| 9 | 1 | 编译配置是否允许目标 GPIO |
| 10 | 1 | 当前目标 owner：0 无、1 USB、2 TCP |
| 11 | 4 | little-endian owner session，0 无 |
| 15 | 2 | little-endian 当前传输的 DAP 包容量 |

配网请求的 field、offset、total、chunkLength 各为一个 uint8 字节，分别位于请求偏移 2 / 3 / 4 / 5；片段从偏移 6 开始，chunkLength 表示随后有效数据的字节数，填充不计入。最大单片段 58 字节，当前主机工具使用最多 48 字节。

长度按 UTF-8 字节计算：SSID 1..32，密码 8..63；固件也允许显式提交长度为 0 的开放网络密码，当前主机工具只提供带密码路径。片段必须覆盖完整字段，不允许内嵌 NUL；提交成功或 USB 总线 reset / 拔出时清空暂存。仅关闭 WinUSB 句柄无法被板端识别为总线会话结束，主机每次配网应从 offset 0 重新发送两个完整字段。

自动配网时，密码经本地子进程 stdin 传入工具，再通过 USB 配网报文发送给设备并保存到 NVS。工具不打印密码、不落密码文件。

## 会话约束与后续接口

DAP_Info、HostStatus 和设备 / 网络信息查询不占用目标。目标 GPIO 已启用时，DAP_Connect 或有效的 DAP_SWJ_Clock 申请整段会话；允许 pyOCD 在 Connect 之前设置时钟。无效时钟不申请会话，时钟已申请的会话在后续 Connect 中延续；其他客户端的时钟、Connect、Transfer 和 Disconnect 均被拒绝。后续目标命令必须来自同一 transport + session。DAP_Disconnect、USB reset / 拔出、TCP 关闭释放相应会话，并走同一执行器清理。

当前标准 USB 会话由 USB 总线代次区分；固件无法从相同端点判断两个主机进程身份。本机开发工具以独占句柄打开设备。连接服务独占一条物理 TCP，通过租约与 USB/IP 导入会话管理客户端；板端继续独立约束 USB / TCP 目标所有权。服务不能感知直接有线调试软件的句柄退出，调试软件应发送 DAP_Disconnect；遗留占用可在确认接口未被使用后清理。0.2.0增加有界采样和整段控制屏障，增强会话在结束／到期／失联时停止订阅并清理目标。

主机工具 exchange_many 可连续提交 1..8 个标准 DAP 包，再按序接收相同数量的响应；TCP 将多个独立帧合入一次发送，USB 连续提交多个 OUT。逐请求内存访问在调用起点和 MEM-AP 1 KiB 边界设置 TAR，检查当前块完整成功后才沿用地址递增。流水线每个包使用一条独立的标准 DAP_Transfer，将 TAR 设置和读写组合在同一个包中；前一个包部分完成或被取消后，后续包仍携带自己的正确地址。两条路径均不跨越 1 KiB 边界。

容量 P 下，流水线每包最多 floor((P-3)/4) 个 read word 或 floor((P-8)/5) 个 write word；逐请求 TransferBlock 为 floor((P-4)/4) 个 read word 或 floor((P-5)/4) 个 write word。64 KiB 连续流水线回读的命令数在 64 / 128 / 256 / 512 档分别为 1152 / 576 / 320 / 192；逐块单独 TAR + 15-word block 的旧路径为 2304 条。调用间不缓存地址，响应数量 / ACK / 完成数量均检查，批次失败不重放未知完成状态的写入。此前跨流水线包缓存 TAR 的检查点已替换，不能在异常路径中依赖它。这里仍使用标准命令与主机排队，不增加 Atomic Commands 或板端采样能力。

定制订阅使用厂商命令0x81，独立于标准 DAP_Info 能力位；没有将自定义采样伪装成标准 Atomic Commands。配置、运行、屏障、统计和二进制帧格式见 [API](api-v1.md)，验证范围见 [实施记录](subscriptions-implementation.md)。标准能力声明保持与已有内核相符。

## 临时 TCP 传输测试

默认诊断端口 3242，只有 USB 显式开启后监听，到期或命令关闭后停止，最多持续 300 秒。该服务与标准 DAP TCP 3241 分开，不占用目标会话。

请求头共 16 字节：[RMB1 4 字节][mode LE32][size LE32][seed LE32]。mode=1 下，size 必须为 1..16 MiB，设备连续发送 size 字节，数据为 (字节偏移 + seed) & 255；mode=2 下，size 必须为 1..1024，头后跟 size 字节，设备原样回传。响应仅含数据，无额外帧头。无效请求关闭该 socket，部分读 / 写超时为 2 秒；主机测试明确区分直接 TCP 测试与 64 字节 DAP 的速率口径，见 transport-benchmark.md。
