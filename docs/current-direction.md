# 当前项目方向

## 核心架构与无线接入方式

2026-10-19 起，RMLink 不再维护虚拟 USB（USB/IP 与 `usbip-win2` UDE 驱动），无线标准 CMSIS-DAP 改为**直接通过 TCP 接入 OpenOCD**（默认端口 `3241`）。

整个链路分工如下：

- **标准无线调试**：`OpenOCD -> TCP/Wi-Fi (3241) -> RMLink -> SWD 执行器 -> 目标 MCU`。OpenOCD 需支持 `cmsis-dap backend tcp`、`cmsis-dap tcp host` 与 `cmsis-dap tcp port`（参见 [OpenOCD 手册](https://www.openocd.org/doc/html/Debug-Adapter-Configuration.html)，注意手册默认端口是 4441，RMLink 使用 3241）。
- **标准有线调试**：原生 USB 口直接提供标准 CMSIS-DAP Bulk 接口，使用 Windows 自带 WinUSB，无需安装第三方内核驱动。
- **增强模式（Orbit / 公开 SDK）**：通过本地连接服务（`127.0.0.1:3243`）管理设备发现、USB 配网、OTA 升级、整包缓存烧录以及板端 Watch / Timeline / RTT 订阅。采样数据在有线模式下走独立 USB Bulk 通道，在无线模式下走 TCP `3244`。

> 切换前的虚拟 USB 实现保存在本地检查点 `022ff12`。当前源码已移除 USB/IP 代理、驱动挂载和相关脚本；旧发行包里的测试记录仅代表当时的条件。

## 几个固定设计原则

1. **唯一目标控制权（Single Owner）**：ESP32 只有一个固定核上的 SWD 执行器。同一时间只能由一个客户端控制目标 MCU——要么是连接服务的某个增强会话（如 Orbit），要么是直连 TCP `3241` 的 OpenOCD，不能两边同时抢占。使用 OpenOCD 直连前需先 `rmlink disconnect`，用完后 `rmlink connect` 交回服务。
2. **板端采样，批量回传**：Watch、Timeline、RTT 都直接在 ESP32 端按周期读取目标，不再依赖主机逐条请求往返。三者共用同一个 owner，控制操作优先于采样。
3. **默认 4 MHz SWD**：固件、连接服务、SDK 和默认调试配置统一请求 `4000 kHz`（4 MHz），同时尊重用户或第三方工具显式指定的频率。
4. **统一的芯片算法包**：Orbit 普通 CMSIS-DAP 与 RMLink 增强路径共用 `host/algorithms/targets.json` 和同一套 MIT 算法源码，目前涵盖 `STM32F103C8`、`STM32F407VE`、`STM32F407IG`、`STM32H723VG`（见 [统一目标算法包](shared-target-package.md)）。
5. **无线组网固定为电脑热点**：电脑开启 2.4 GHz 移动热点，RMLink 作为 STA 连入，电脑再通过 mDNS（`rmlink-xxxxxx.local`）或 IP 访问设备，不需要额外的 USB 无线发射器。

## 硬件平台

- **RMLink V1 自制板**（主线硬件）：ESP32-S3-WROOM-1-N16R8（16 MiB Flash、8 MiB Octal PSRAM），GPIO17/18/6 分别接 SWDIO/SWCLK/NRST，GPIO19/20 接原生 USB，GPIO1 接 WS2812，GPIO2 接用户按键，CH343 串口用于下载和看底层日志。详见 [RMLink V1 接线与验证](rmlink-v1-bringup.md)。
- **早期普通 ESP32-S3 开发板**：GPIO4/5/6 接 SWDIO/SWCLK/NRST，同样配备原生 USB 口与 CH343 口。

## 参考文档

- 协议与 API：[开发协议 v1](protocol-v1.md)、[API v1 与公开 SDK](api-v1.md)、[订阅采集设计](subscription-design.md)
- 实板验证与进展：[RMLink V1 接线与验证](rmlink-v1-bringup.md)、[STM32 验收记录](stm32-acceptance.md)、[开发进度](development-status.md)、[快速构建与接线](quickstart.md)
