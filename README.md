# RMLink

基于 ESP32-S3 的 USB / Wi-Fi CMSIS-DAP 调试器，面向 Orbit 扩展板端采样与批量操作。

## 目录

| 目录 | 用途 |
| --- | --- |
| firmware/ | 以乐鑫 esp-usb-bridge 为基础的 RMLink 固件源码 |
| reference/ | 上游参考仓库及引用版本记录；保持上游源码可对照 |
| scripts/ | 可复现构建、设备检查及验证脚本 |
| docs/ | 项目方向、源码核对、协议、接线与开发进度 |
| artifacts/ | 构建产物、日志、设备备份和测试证据 |
| tests/ | STM32 实板验收程序源码 |

根目录保留项目入口与编辑器设置，临时文件和日志放入 artifacts/，不混入文档或参考源码。
项目根目录统一管理固件、脚本、测试与文档；reference/ 下各项目保留独立历史但全部排除，详见 [仓库管理](docs/git-management.md)。

## 入口

- [最初方向记录（2026-10-12）](docs/RMLink项目方向记录.md)：保留讨论历史，后续实施以新的源码核对与开发记录为准。
- [当前源码核对](docs/source-audit.md)
- [开发与验证进度](docs/development-status.md)
- [当前项目方向](docs/current-direction.md)
- [构建、连接与接线](docs/quickstart.md)
- [已实现的开发协议](docs/protocol-v1.md)
- [Windows 连接组件评估](docs/windows-connector-evaluation.md)
- [ESP32 / 电脑传输速率与延迟](docs/transport-benchmark.md)

当前硬件：普通双 USB-C ESP32-S3 开发板；COM13 为 CH343 下载/日志口。
已通过芯片工具读取确认 16 MB Flash、8 MB 内置 PSRAM；用户已按 GPIO4 / 5 / 6 接上 STM32F103C8T8 的 SWDIO / SWCLK / NRST，并连接 GND。

当前开发按阶段推进，构建或主机测试通过不代表目标 STM32 已通过实板调试、烧录或性能验收。

2026-10-13：RMLink 0.1.0 已构建、烧入开发板；USB / Wi-Fi 传输、配网与恢复测试通过。当前已启用目标 GPIO，STM32F103 的 USB / TCP 只读访问、会话互斥和主机排队读取通过；目标暂停 / 继续、RAM 写入、复位和烧录待测。板端采样、Orbit 后端、USBIP 和 Windows 自动连接组件尚未实现。

同日补测：拔掉直通 USB&OTG、只保留 CH343 供电 / 日志后，三次复位启动均自动加入热点并通过 TCP 查询，确认无线服务不依赖原生 USB 枚举。完全断电上电仍待测，详见进度记录。

最新固件 0.1.1 增加传输诊断：ESP32 / PC 的 USB 和 Wi-Fi RTT、64 字节 DAP 吞吐、直接 TCP 连续回传已实测。临时测试口默认关闭，测试后关闭；本轮未访问 STM32。目标只读证据对应保留的 0.1.0 target 镜像。
