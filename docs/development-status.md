# 开发与验证进度

更新日期：2026-10-13。当前固件 0.1.4，连接服务 0.1.0。USB / TCP 三线 SWD 的既有验收保持为独立记录；新增 Windows 虚拟 USB 经 Wi-Fi 的 64 KiB 烧录、回读、调试与异常恢复验收通过，原 Flash 已恢复。NRST 缺线，直接 OpenOCD TCP 仍有旧版主机限制；虚拟 Bulk 路径的 OpenOCD 日志无 Error，详见 [实板验收](stm32-acceptance.md) 与 [Windows 组件](windows-connector.md)。

## 阶段状态

| 阶段 | 源码 / 实现 | 构建 / 主机验证 | 实板状态 |
| --- | --- | --- | --- |
| 1：基座与环境 | 五个参考仓库固定提交；独立乐鑫基座；脚本、板级 profile、组件锁定和 CodeGraph | 上游基座、RMLink 默认、目标 GPIO 开启配置均编译通过 | S3 rev0.2、16 MB Flash、8 MB PSRAM 已读出；完整原 Flash 已备份；PSRAM 未启用 |
| 2：USB / 目标 SWD | Bulk、统一执行器、会话规则、NRST 钩子、边界和主机排队读取已实现 | target profile 已编译并刷写；主机核心通过 | USB / TCP 的 64 KiB 烧录与回读、暂停 / 继续、寄存器 / RAM、断点 / 单步、DWT 和软件复位通过；NRST 缺线 |
| 3：STA / TCP / USB 配网 | NVS、自动入网、mDNS、分片帧和网络独立恢复已实现 | 主机 TCP client 6 项测试通过 | 配网 / STA / TCP / 无 OTG 复位通过；STA 恢复时 USB 的真实目标读取与 owner 保持通过；完全断电及真正热点掉线待测 |
| 4：Orbit / 板端采样 | 已核对 Orbit 所有权链；新增本机 API / Node SDK；实际 Orbit 与采样尚未接入 | SDK 经真实服务完成连接、批量查询和释放 | API 读 STM32、与虚拟 USB 双向互斥通过；板端采样未验收 |
| 5：Windows 自动组件 | mDNS、配对、一个物理 TCP、USB/IP 代理、自动重挂载与 UDE 驱动控制 | 独立 EXE 构建、16 项主机测试通过；官方 usbip-win2 0.9.8.1 已安装 | 驱动与 WinUSB 枚举 / Bulk 实测通过，OpenOCD 烧录 / 调试通过；STA 断连约 5 秒自动恢复，跨 Windows / ARM64 / HID 兼容性待测 |

当前 target profile 固件为 0.1.4，SHA256 5F4AD7C96AC411AED846A9767B0AF3333CDC362B488465D78AB3F16152C64BB4。0.1.4 新增只读 MAC 查询，SWD 内核沿用 0.1.3。最后刷写记录在 artifacts/hardware/rmlink-last-flash.json；[实板验收](stm32-acceptance.md) 分开记录各版本的证据。

新增异步 TransferAbort、TCP 独立收发、断连取消 / 排队清理、传输期限、未实现命令 0xFF 响应和 SWJ 引脚监测 / 等待修正。唯一 SWD 执行器与整段会话所有权保留。

以下测速、首轮只读与无 OTG 启动内容是历史证据，对应各段注明的 0.1.0 / 0.1.1，不代替当前版本验收。

## ESP32 / 电脑传输专项

本轮按用户要求只测 ESP32 / PC，未调用 SWD 或占用目标。0.1.1 增加 64 字节可校验响应和临时直接 TCP 测试口，ARM SWD 内核未改。每条延迟路径 1500 个有效样本；直接 TCP 回传 5 轮各 4 MiB，全部内容校验通过，设备未重启。结果及重跑方法详见 [传输专项记录](transport-benchmark.md)。

| 通道 | 有效吞吐中位值 | RTT 中位数 | RTT P95 |
| --- | --- | --- | --- |
| USB 64 字节 DAP 通道 | 289.27 KiB/s | 0.1910 ms | 0.2721 ms |
| Wi-Fi 64 字节 DAP 通道 | 18.65 KiB/s | 3.3384 ms | 54.8789 ms |
| Wi-Fi 直接 TCP 连续回传 | 5.134 Mbps，约 0.642 MB/s | 2.3086 ms | 44.1034 ms |

直接 TCP 各轮 4.542～6.926 Mbps；Wi-Fi 有明显延迟长尾。以上是当前应用与主机路径的有效吞吐 / 往返延迟，未量测单程延迟或硬件链路极限。测试服务结束后关闭，自动到期关闭已验证；USB / TCP 标准设备查询回归通过。新增固件未在本轮重新访问 STM32，目标只读的实板证据仍为 0.1.0。

## STM32 首轮只读验收

2026-10-13，用户报告目标为 STM32F103C8T8，按 GPIO4→SWDIO、GPIO5→SWCLK、GPIO6→NRST、GND 共地连接，并接回原生 OTG。已重新构建 / 刷写 target profile，串口启动正常，USB / TCP 均报告 targetPinsConfirmed=true。

| 读取内容 | USB / TCP 结果 |
| --- | --- |
| SWD DP IDCODE | 0x1BA01477 |
| DP CTRL/STAT，调试供电请求 / 应答 | 0xF0000040 |
| AHB-AP IDR | 0x14770011 |
| Cortex-M CPUID | 0x411FC231，对应 Cortex-M3 |
| DBGMCU_IDCODE | 0x00006410，DEV_ID=0x410 |
| Flash 容量寄存器 | 64 KiB |
| 唯一 ID 和 Flash 向量表 | 两个传输一致；初始 SP=0x20005000，复位向量=0x08001A81 |
| 100 次 CPUID 内存读取，100 kHz 设置 | USB 458.56 ms；TCP 2660.02 ms，全部通过 |

两条路径读取时均观察到核心未暂停，NRST 输入为高；未下发暂停 / 复位命令。工具会配置 CoreSight DP / AP 调试寄存器，不写目标 RAM / Flash。芯片信息与用户型号及 [ST STM32F103x8 数据手册](https://www.st.com/resource/en/datasheet/stm32f103c8.pdf) 的 Cortex-M3 / 64 KiB 配置相符，不能据此独立认证芯片真伪或完整料号。供电电压、实际 SWCLK 频率和 NRST 波形未仪表量测。

三轮会话测试通过：USB 占用时 TCP 的 Connect / Transfer / Disconnect 被拒绝；TCP 占用时 USB 同样被拒绝，现有 owner 不变。正常 Disconnect 释放会话；TCP socket 关闭但未主动 Disconnect 时，板端仍清理会话，USB 可重新接管并读 CPUID。最后 owner 为 0，无遗留占用。

另一次 STA 主动断连恢复约 3934.52 ms，期间同一 USB owner 下 255 次目标 CPUID 读取全部通过，最大单次约 22.11 ms；owner 不变、ESP32 运行时间增长。网络恢复后 TCP 仍不能抢占 USB，USB 释放后 TCP 可继续读取。此结论是保持只读目标访问，完整调试会话、热点实际关闭恢复尚未验收。

## 同目标 Flash 读取对照

先通过 USB 读取 64 KiB 主 Flash 到 artifacts/hardware/stm32f103c8-before-rmlink.bin，SHA256 75bcca6e52d436f377542beb94629ef101d153e214ef085b6af7893d40b347ff。备份不包含选项字节、RAM 或系统 ROM，恢复尚未验收；仅本地保留。

使用同一 target 固件、同一目标和主 Flash 起始 4096 字节，顺序请求与最多 8 个标准 DAP 包连续提交各读三次，内容均与备份相同。下表为三次主机计时的中位吞吐：

| DAP 请求时钟 | USB 顺序 | USB 8 包连续提交 | Wi-Fi 顺序 | Wi-Fi 8 包连续提交 |
| --- | --- | --- | --- | --- |
| 1 MHz | 18.38 KiB/s | 19.60 KiB/s | 1.71 KiB/s | 2.98 KiB/s |
| 2 MHz | 31.54 KiB/s | 35.09 KiB/s | 2.18 KiB/s | 8.17 KiB/s |

另一次时钟阶梯测试中，100 kHz、500 kHz、1 MHz、2 MHz 在 USB / TCP 下各三次顺序读取均通过。无线计时存在明显波动，以上不是保证速度、烧录速度或采样率。实际 SWCLK 未量测；主机提交按 TAR + TransferBlock 成对排队，单包最多读 15 word，并处理 1 KiB 地址递增边界。板端周期采样及更大的定制批量命令尚未实现。

## 当前 ESP32 设备实测

- COM13 为 CH343 下载 / 日志口；原生 OTG 应用 VID303A / PID1002、序列号 50787D15A924、Bulk OUT03 / IN83。
- Windows 自动绑定 WINUSB，设备状态 OK；本次没有另行安装 USB 驱动。USB ID 暂沿用基座用于本机原型，正式分发前另行确认可使用的 ID。
- 热点已开启且为 TwoPointFourGigahertz；凭据通过 USB 保存，后续刷写及串口复位后仍自动加入。
- 当前 DHCP 地址 192.168.137.151，rmlink-15a924.local 在 Windows 成功解析。
- TCP 分片、连续帧、无效标识 / 长度 / 类型 / reserved 拒绝、部分头超时后新连接恢复通过；TCP 配网被拒绝。
- 最新三次 USB 命令主动断开 STA 的恢复耗时约 7.18 / 2.68 / 2.87 秒。期间同一 USB 句柄累计 612 次查询通过，最大单次约 5.64 ms；设备微秒运行时间持续增长，未观察到 MCU 重启。主机旧流为关闭或复位，新 TCP 连接可继续查询。

最后一项验证的是设备主动断开 / 自动重连，不等同于电脑热点关闭恢复，也不等同于 STM32 USB 调试不中断。

## 无原生 USB 枚举的无线启动

2026-10-13 17:52（Asia/Shanghai），用户拔掉直通 USB&OTG 线，只保留 CH343 供电 / 日志。主机检查确认只有 CH343 COM13，RMLink 原生 USB 设备未枚举。通过 CH343 RTS 复位三次，分别捕获启动日志；每次仅有一次 ROM 启动，无 USB mount / endpoint / 发送任务日志，无崩溃记录。

| 复位次数 | 日志中取得 IP 的启动时间 | TCP 连续查询 | 100 次查询耗时 |
| --- | --- | --- | --- |
| 1 | 1962 ms | 100 / 100 通过 | 837.64 ms |
| 2 | 7652 ms | 100 / 100 通过 | 1130.81 ms |
| 3 | 2072 ms | 100 / 100 通过 | 1013.78 ms |

每次从已保存参数自动加入热点，rmlink-15a924.local 可访问，网络状态 configured / connected 均为 true，IP 192.168.137.151。未重新配网或刷写固件，目标 GPIO 仍关闭。这证明当前无线启动 / 执行器不依赖原生 USB 枚举；CH343 仅承担供电及复位 / 日志。此次是复位启动，尚未完成完全断电再上电测试，亦未验证 STM32 SWD。

## 初步传输计时

同一当前固件的设备查询，未执行目标 SWD：

| 测试 | 请求量 | 最新单次结果 |
| --- | --- | --- |
| USB 逐请求 DAP_Info | 100 | 15.93 ms |
| Wi-Fi 逐请求 DAP_Info | 100 | 824.17 ms |
| USB 8 包排队，混合静态信息 | 800 | 260.09 ms，内容 / 顺序通过 |
| Wi-Fi 每批连续发送 8 帧 DAP_Info | 800 | 2247.44 ms，内容 / 顺序通过 |

计时已改为 Python perf_counter；早期日志中的 monotonic 在该 Windows Python 环境有粗粒度，不能用于微秒延迟判断。批量 USB 修复前 800 包约 7 秒，主要受发送等待节拍影响，旧证据保留。

这些是早期 GPIO 关闭版本的设备查询计时，不是目标读取或完整基准。当前目标 Flash 读取计时另见上表；Wi-Fi 原始带宽、真实 SWD 时钟、目标写入、烧录校验、变量采样率、丢样和完整控制延迟尚未验收。

## 验证证据

| 路径（相对项目根目录） | 证据层次 |
| --- | --- |
| artifacts/logs/build-upstream.log | 未改乐鑫基座构建 |
| artifacts/logs/build-rmlink.log / flash-rmlink.log | 早期 GPIO 关闭配置编译与刷写、hash 校验 |
| artifacts/logs/build-target-profile.log / flash-target-profile.log / boot-target-profile.log | 当前 GPIO 开启配置构建、刷写 hash 校验及启动；首轮干净构建为 1002 步，后续为增量 |
| artifacts/logs/boot-network.log | 早期配置启动、16 MB Flash、STA 自动加入、IP 与 USB mount |
| artifacts/logs/test-core.log | 主机 owner / 8 字节头 / 边界及 20000 随机报文 |
| artifacts/logs/test-tcp-client.log | 独立主机 TCP peer 的 6 项测试，包括连续提交与整批发送前验证 |
| artifacts/logs/provision-hotspot.log | 热点频段、USB 保存、加入；无密码输出 |
| artifacts/logs/verify-device-transport.log | 早期 GPIO 关闭配置的 USB 800 包、TCP 800 帧、无效帧及恢复 |
| artifacts/logs/verify-swd-usb.json / verify-swd-tcp.json | 当前 target profile 的真实目标信息、向量表、100 次内存读取和 owner |
| artifacts/logs/verify-target-ownership.json | 三轮会话互斥、TCP 关闭释放、USB 重接管和 STA 断连期间目标读取 |
| artifacts/logs/benchmark-swd-reads.json / benchmark-swd-pipeline.json | 同目标时钟阶梯与顺序 / 连续提交的 Flash 读取对照 |
| artifacts/logs/benchmark-transport.json / benchmark-transport-run.log | 0.1.1 ESP32 / PC 的全部 RTT 样本及 DAP / 直接 TCP 吞吐 |
| artifacts/logs/build-transport-bench.log / flash-transport-bench.log / boot-transport-bench.log | 当前传输诊断版本的构建、刷写和正常启动 |
| artifacts/logs/verify-transport-bench.json / verify-usb-after-transport-bench.log / verify-tcp-after-transport-bench.log | 诊断控制 / 边界 / 自动关闭及标准查询回归 |
| artifacts/logs/verify-flash-backup.json | USB / TCP 分别重读完整 64 KiB 并与备份逐字节比对 |
| artifacts/logs/verify-swd-final-state.json | 最终只读目标检查：GPIO 开启、核心未暂停、Disconnect 后 owner=0 |
| artifacts/logs/verify-wireless-startup.json / verify-wireless-startup-run.log | 无 OTG 枚举、三次 CH343 复位、保存参数自动入网及 300 次 TCP 查询 |
| artifacts/logs/boot-wireless-1.log / boot-wireless-2.log / boot-wireless-3.log | 三次无原生 USB mount 的完整串口启动日志 |
| artifacts/logs/verify-device-before-usb-notify.log | USB 节拍修改前；旧计时及旧流状态字段仅供历史对照 |
| artifacts/logs/verify-usb-before-dma-fix.log / usb-raw-diagnostic.log | 早期失败证据，保留追溯 |
| artifacts/validation-snapshot.json | 引用提交、产物哈希、sdkconfig 与源代码状态 |
| artifacts/hardware/esp32s3-before-rmlink.bin | 修改前完整 16 MB 备份，仅本地保留 |
| artifacts/hardware/stm32f103c8-before-rmlink.bin | STM32 64 KiB 主 Flash 只读备份，仅本地保留 |

reference/ 保留上游源码，CodeGraph 元数据不计为实现改动。产物和日志统一在 artifacts/，缓存不放根目录或 scripts/。这描述早期固件工作区；现已迁入根目录统一 Git，本地提交不推送。原基座首次构建目录在 Codex 工作区；后续 RMLink 构建统一使用项目内绝对路径。

## 下一验收点

三线常规烧录 / 调试已通过，原程序和选项数据核对完成。补充 NRST 实体接线、真实 WAIT、USB 物理拔出、ESP32 完全断电上电、实际热点关闭恢复、SWCLK 波形和长期压力测试。Windows TCP 默认排队模式需采用修正 socket 处理的后端，再验收。

继续接入 Orbit，补充板端采样、缓存和控制优先级；Windows 自动连接组件独立推进。项目根目录现统一 Git 管理，reference/、artifacts/ 与生成依赖排除，原 firmware/.git 已归档，见 [仓库管理](git-management.md)。
