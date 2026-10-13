# RMLink

基于 ESP32-S3 的 USB / Wi-Fi CMSIS-DAP 调试器，面向 Orbit 扩展板端采样与批量操作。

## 目录

| 目录 | 用途 |
| --- | --- |
| firmware/ | 以乐鑫 esp-usb-bridge 为基础的 RMLink 固件源码 |
| host/ | Windows 连接服务、本机 USB/IP 代理及 Orbit 接入 SDK |
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
- [STM32 烧录 / 调试验收](docs/stm32-acceptance.md)
- [当前项目方向](docs/current-direction.md)
- [构建、连接与接线](docs/quickstart.md)
- [已实现的开发协议](docs/protocol-v1.md)
- [Windows 连接组件评估](docs/windows-connector-evaluation.md)
- [Windows 连接组件使用与验收](docs/windows-connector.md)
- [ESP32 / 电脑传输速率与延迟](docs/transport-benchmark.md)

当前硬件：普通双 USB-C ESP32-S3 开发板；COM13 为 CH343 下载/日志口。
已通过芯片工具读取确认 16 MB Flash、8 MB 内置 PSRAM；用户已按 GPIO4 / 5 / 6 接上 STM32F103C8 的 SWDIO / SWCLK 与 GND；GPIO6 预留 NRST，当前缺线 / 未验收。

当前开发按阶段推进，构建或主机测试通过不代表目标 STM32 已通过实板调试、烧录或性能验收。

2026-10-13：当前固件 0.1.4。USB / Wi-Fi 三线 SWD 已完成烧录、64 KiB 回读、暂停 / 继续、寄存器 / RAM、断点 / 单步、DWT、软件复位及异常恢复，原主 Flash 已恢复。新增 Windows 连接服务与 usbip-win2 虚拟 USB 封装，OpenOCD 通过虚拟 CMSIS-DAP 经 Wi-Fi 完成同样的实板验收，64 KiB 烧录 / 校验约 11.3 秒且无 Error 行。NRST 缺线；直接 OpenOCD TCP 的旧验收仍有兼容模式 / 主机报错限制，详见 [实板验收](docs/stm32-acceptance.md) 与 [连接组件](docs/windows-connector.md)。

连接服务已实现 mDNS 发现、明确配对、后台重连、自动重挂载、本机 API 与虚拟 USB / API 会话互斥；独立 EXE 无需安装 Python。此前无 OTG 启动与 0.1.1 测速是独立历史证据。板端采样、实际 Orbit 后端接入、HID 及统一产品安装界面仍未实现，不能宣称所有 CMSIS-DAP 软件均兼容。
