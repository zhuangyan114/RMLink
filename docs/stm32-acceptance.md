# STM32 烧录与调试验收

最新补充（2026-10-14）：固件 0.1.5 / 连接服务 0.1.1 / 本地测试签名修补 UDE，通过虚拟 USB 经 Wi-Fi 重新完成三线 SWD 全部验收。64 KiB OpenOCD 烧录 / 校验 12845.70 ms，正常日志无 Error 行，独立完整回读 8120.89 ms；暂停 / 继续、寄存器、RAM、FPB / 软件断点、单步、DWT、软件复位、FAULT 恢复、SWD_Sequence、异步 Abort 与执行期限均通过。原程序恢复 12288.24 ms，虚拟 USB 与后续直连 USB 完整回读均匹配原备份，选项字节未变、核心运行，未遗留 owner。报告在 artifacts/logs/test-driver-20261014/accept-virtual-usb.json，最终状态在同目录 final-target-state.json。

pyOCD 0.45.1 在同一虚拟无线探针上完成 CPUID、暂停、PC 读取和继续运行；STA 断连重挂载后再次通过。本轮 pyOCD 未用于烧录。物理 NRST 仍缺线；正式驱动签名、HID 与 Keil / IAR 兼容尚未验收，详见 [Windows 组件](windows-connector.md)。下面保留固件 0.1.3 的双通道历史验收，计时与限制按各自版本阅读。

日期：2026-10-13。固件 RMLink 0.1.3，用户本轮将目标标为 STM32F103C8T6；读取到 DEV_ID=0x410、CPUID=0x411FC231、64 KiB 主 Flash。早期记录为 C8T8，寄存器不能区分完整封装 / 温度后缀。本轮按用户允许的目标烧录权限执行。

USB Bulk 与 Wi-Fi TCP 的**三线 SWD 常规烧录、调试及软件复位验收通过**。NRST 实体复位未通过：GPIO6 自身可拉低 / 释放，但目标未复位；用户说明没有多余杜邦线，故明确采用 SWDIO / SWCLK / GND，并使用 Cortex-M SYSRESETREQ 软件复位。接上线后需要单独重新验收 NRST，不以 ESP32 引脚读回或命令返回成功代替目标复位证据。

## 本轮实现

- 0x07 DAP_TransferAbort 从接收路径直接取消当前会话的传输，不排在待取消请求之后；本命令没有响应，原 Transfer / TransferBlock 返回实际数量和状态。[ARM 规范](https://arm-software.github.io/CMSIS-DAP/latest/group__DAP__TransferAbort.html)
- TCP RX 与回复发送分离，RX 位于执行器以外的 CPU；唯一固定核 SWD 执行器负责目标访问。断连先使会话失效、取消活动传输，FIFO 清理完成后才释放回复上下文；旧排队请求不再执行。回复队列超时关闭连接，避免丢失响应后继续复用不确定的数据流。
- 取消标志使用原子访问；外来 transport / session 不能取消 owner 的传输。TransferBlock 同时检查成功 word 之间的取消，避免只在 WAIT 时响应取消。
- 单个 Transfer / TransferBlock 的执行期限为 1 秒，保留现有 WAIT 重试与匹配失败状态；不自动重放失败写入。SWD 时钟允许设置 1 kHz～10 MHz，SWJ 引脚等待上限 1 秒，超范围请求拒绝。实际 SWCLK 未仪表量测。
- 未实现命令返回单字节 0xFF；不声明 JTAG、SWO、UART、时间戳或 Atomic Commands。64 字节请求 / 响应边界与非法标志检查仍生效。
- 补齐 SWCLK 真实输入读回，修正 SWJ_Pins 等待计时与位值比较；SWD 模式不返回伪 TDO 信号。

## 目标验收矩阵

同一份可复现测试镜像 fixture-64k.bin：SHA256 c35172f8d5ea7b6771048c5bcea93a5fb1940ec3e127cce73c0bc67f782eb771。程序只使用复位后的 HSI、更新 SRAM 计数器，不驱动目标 GPIO / 外设；程序以外区域填充确定性地址模式，使验收覆盖完整 64 KiB Flash。请求 SWD 时钟为 2 MHz。

| 项目 | USB | Wi-Fi TCP |
| --- | --- | --- |
| 独立 OpenOCD 擦除、烧录 64 KiB、校验、软件复位运行 | 通过 | 兼容模式通过，见下节 |
| 原生客户端独立读取完整 64 KiB、逐字节比较及 SHA256 | 通过 | 通过 |
| 运行计数器持续增长、暂停时停止、继续后运行 | 通过 | 通过 |
| 读取 R0～R15 / xPSR，R7 写入与读回 | 通过 | 通过 |
| SRAM 3072 字节批量写入 / 读回，跨 MEM-AP 1 KiB 边界 | 通过 | 通过 |
| FPB Flash 硬件断点，命中地址 0x080000C2 | 通过 | 通过 |
| SRAM 软件断点、移除后单步 MOVS / ADD，R0=17→18 | 通过 | 通过 |
| DWT 写观察点，真实计数器写入触发暂停 | 通过 | 通过 |
| SYSRESETREQ 复位后重新启动、标记恢复 | 通过 | 通过 |
| SWD_Sequence 输出 / 输入、SWJ 控制 / 线复位、SWCLK 高低读回 | 通过 | 通过 |
| 真实越界 Flash 访问 FAULT，WriteABORT 清除后继续读 CPUID | 通过 | 通过 |
| 不可能 DP 匹配请求被异步取消，取消本身无回复 | 通过 | 通过 |
| 慢速 DP TransferBlock 取消，15 word 中实际只完成 1 word | 通过 | 通过 |
| 单条匹配传输约 1 秒期限及之后继续通信 | 通过 | 通过 |
| 非法 / 超大命令拒绝、未实现命令回复 0xFF | 通过 | 通过 |
| DAP_Disconnect 后 owner=0 | 通过 | 通过 |
| NRST GPIO6→目标实体复位 / DAP_ResetTarget 引脚复位 | 缺接线，待验收 | 缺接线，待验收 |

异常专项另验证：外来 TCP 会话的 Abort 不能取消 USB owner（原请求仍在 1000.66 ms 期限结束）；TCP 在长请求和 3 条排队请求期间关闭，约 22.40 ms 释放 owner，USB 能重新接管；制造真实 NO_ACK=7 后，经 SWJ 线复位及首先读取 DPIDR 恢复。5 类无效 TCP 头关闭连接，部分头约 2035.30 ms 超时，两条通道各 160 个排队信息响应内容与顺序正确。

WAIT 重试沿用 ARM 内核并加取消 / 期限保护；本轮没有通过实物条件单独制造持续 WAIT，因此不将这项描述为独立实板验收。USB 物理拔线取消、热点真实关闭、完全断电启动和长期压力测试未在本轮补测。

## Windows OpenOCD 兼容限制

使用乐鑫官方 v0.12.0-esp32-20260831 Win64 包，下载 ZIP 的 SHA256 为 D508BBE1D166E38415D57B495DD0409F46883251087433CFB3EABF513BE93FF7，放在 artifacts/tools/，不进入开发仓库。[官方版本](https://github.com/espressif/openocd-esp32/releases/tag/v0.12.0-esp32-20260831)

USB 正常烧录，无 Error 日志。Windows TCP 默认排队模式的烧录曾失败；其 cmsis_dap_tcp.c 使用 errno 检查 Windows socket 的 WSAEWOULDBLOCK，出现 WSAGetLastError=10035 / error reading header。启用 cmsis-dap quirk enable（禁止排队并增加连接兼容行为）后，退出码为 0、Verified OK、实际完整 Flash 字节比对均通过；**仍记录 128 行该类主机报错**，不能描述为干净的 Windows TCP 默认模式兼容。原生 RMLink TCP 客户端的 8 包排队、批量 RAM / Flash 操作通过，不依赖此 OpenOCD 配置。

后续连接组件应采用修正 socket 处理的后端，再恢复并验收默认排队；当前未修改 / 替换已安装 OpenOCD，也未修改参考仓库。[对应版本源码](https://github.com/espressif/openocd-esp32/blob/v0.12.0-esp32-20260831/src/jtag/drivers/cmsis_dap_tcp.c)、[上游现行实现](https://github.com/openocd-org/openocd/blob/master/src/jtag/drivers/cmsis_dap_tcp.c)

| 单次计时 | USB | Wi-Fi |
| --- | --- | --- |
| OpenOCD 进程总耗时（包括初始化、擦除、烧录、校验、复位；不是纯烧录吞吐） | 3390.58 ms | 56787.58 ms，含主机错误处理 / 兼容模式开销 |
| 原生客户端完整 64 KiB Flash 回读 | 1845.07 ms | 10259.89 ms |
| 匹配请求取消总耗时，包含主机先等待 80 ms | 84.75 ms | 83.72 ms |
| 1 kHz 设置下，块取消总耗时，包含主机等待 80 ms | 145.32 ms | 198.87 ms |
| 无主机取消时单条匹配请求期限 | 1000.50 ms | 1002.11 ms |

以上是单次功能验收计时，不能替代多轮性能基准或延迟分布。此前 0.1.1 的 ESP32 / PC 传输测速是独立历史证据，不能用其结果直接代表 0.1.3。

## 原程序恢复与证据

测试结束后，经 USB 重新烧录原 64 KiB 主 Flash。USB 与 TCP 分别完整回读并比较，均与原备份 SHA256 75bcca6e52d436f377542beb94629ef101d153e214ef085b6af7893d40b347ff 一致；16 字节选项数据读取前后相同，没有写入选项字节。两路最终观察核心运行，未遗留 target owner。本次没有恢复原运行期间 RAM 快照，恢复是复位后运行原 Flash 程序。

| 本地路径 | 内容 |
| --- | --- |
| artifacts/logs/accept-stm32.json | 同目标双通道逐项检查及恢复结果；complete=true 仅指声明的三线 SWD 范围 |
| artifacts/logs/accept-stm32-run.log | 验收执行顺序与结果 |
| artifacts/logs/accept-openocd-usb.log / accept-openocd-wifi.log / accept-openocd-restore.log | 标准软件烧录 / 校验 / 恢复日志 |
| artifacts/logs/verify-target-exceptions.json | 跨会话 Abort、NO_ACK、断连清理、无效头与排队回归 |
| artifacts/logs/verify-target-ownership-final.json | 0.1.3 三轮 USB / TCP 互斥与 STA 恢复期间真实目标读取 |
| artifacts/logs/diagnose-nrst.json | GPIO 读回与目标未复位的诊断，软件复位对照 |
| artifacts/logs/accept-stm32-before-*.json | 早期失败的完整报告，保留追溯 |
| artifacts/logs/build-swd-acceptance-final.log / flash-swd-acceptance-final.log / boot-swd-acceptance-final.log | 0.1.3 编译、刷写校验和正常启动 |

## 重跑

必须先停止其他占用目标的调试软件。以下流程会擦除 / 烧录主 Flash，结束时尝试恢复指定备份；备份失败或恢复失败会保存报告并返回失败，不自动重复不确定的写入。当前脚本针对本机已核对的 64 KiB 备份哈希，换目标需要明确准备匹配的备份。

```powershell
& D:\STM32\RMLink\scripts\Build-Stm32Fixture.ps1
$rmlinkPython = 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe'
& $rmlinkPython -B D:\STM32\RMLink\scripts\accept_stm32.py --allow-target-write --without-nrst
& $rmlinkPython -B D:\STM32\RMLink\scripts\test_target_exceptions.py
```

有 NRST 接线后去掉 --without-nrst，脚本会要求引脚复位及 DAP_ResetTarget 后目标程序标记真正恢复。源码在 tests/stm32f103/，生成镜像在 artifacts/build/stm32-fixture/，工具与备份均不进入 Git。
