# RMLink

基于 ESP32-S3 的 USB / Wi-Fi CMSIS-DAP 调试器，面向 Orbit 扩展板端采样与批量操作。

2026-10-15 开发版本：固件、连接服务和 SDK 均为 0.2.0。已实现默认请求 SWD 4000 kHz、Watch／Timeline／RTT 板端订阅、独立数据流、通用目标控制和 F103C8 页镜像烧录接口。Orbit 在独立工作树通过公开 SDK 接入；主机模型与构建通过，真实 ESP32 的协议、错误路径和虚拟 USB 回归通过。STM32 当前返回 NO_ACK，4 MHz 完整烧录、有效采样率和增强烧录实板验收尚未通过，详见 [实施记录](docs/subscriptions-implementation.md)。

标准 CMSIS-DAP 以虚拟 USB 为主入口，测试 STM32 默认保留新程序。完整架构、协议、调度和实施验收见 [订阅采集与 Orbit 接入方案](docs/subscription-design.md)；第三方接入见 [公开 API 与 SDK](docs/api-v1.md)。

连接组件按通用平台设计：虚拟 USB 驱动、用户态服务/目标引擎、公开 API 和 SDK 统一提供，第三方前端无需依赖 Orbit 即可接入；通用订阅与数据管理集中在组件中。

2026-10-14 历史性能基线：固件 0.1.6 / 服务 0.1.4，TCP / 无线虚拟 USB 为 512 字节，原生 USB 为 64。相同会话交替比较，64 KiB 回读约 6.29 → 2.45 秒；虚拟 OpenOCD 全流程烧录 / 校验约 7.28 秒。该结果对应请求 2 MHz，不能替代 0.2.0 的 4 MHz 或订阅验收。范围与原始证据见 [提速结果](docs/performance-results.md)。

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
- [Watch / Timeline / RTT 订阅完整方案](docs/subscription-design.md)
- [0.2.0 实施与分层验证](docs/subscriptions-implementation.md)
- [公开 API 与 SDK](docs/api-v1.md)
- [构建、连接与接线](docs/quickstart.md)
- [已实现的开发协议](docs/protocol-v1.md)
- [Windows 连接组件评估](docs/windows-connector-evaluation.md)
- [Windows 连接组件使用与验收](docs/windows-connector.md)
- [虚拟 USB 取消问题与测试驱动](docs/virtual-usb-cancellation.md)
- [ESP32 / 电脑传输速率与延迟](docs/transport-benchmark.md)
- [完整提速计划与阶段验收](docs/performance-plan.md)
- [提速实现与实板结果](docs/performance-results.md)

当前硬件：普通双 USB-C ESP32-S3 开发板；COM13 为 CH343 下载/日志口。
已通过芯片工具读取确认 16 MB Flash、8 MB 内置 PSRAM；GPIO4 / GPIO5 接 STM32F103C8 的 SWDIO / SWCLK，并共地；GPIO6 预留 NRST，当前缺线 / 未验收。

当前开发按阶段推进，构建或主机测试通过不代表目标 STM32 已通过实板调试、烧录或性能验收。

2026-10-13：当前固件 0.1.4。USB / Wi-Fi 三线 SWD 已完成烧录、64 KiB 回读、暂停 / 继续、寄存器 / RAM、断点 / 单步、DWT、软件复位及异常恢复，原主 Flash 已恢复。新增 Windows 连接服务与 usbip-win2 虚拟 USB 封装，OpenOCD 通过虚拟 CMSIS-DAP 经 Wi-Fi 完成同样的实板验收，64 KiB 烧录 / 校验约 11.3 秒且无 Error 行。NRST 缺线；直接 OpenOCD TCP 的旧验收仍有兼容模式 / 主机报错限制，详见 [实板验收](docs/stm32-acceptance.md) 与 [连接组件](docs/windows-connector.md)。

连接服务已实现 mDNS 发现、明确配对、后台重连、自动重挂载、本机 API 与虚拟 USB / API 会话互斥；独立 EXE 无需安装 Python。此前无 OTG 启动与 0.1.1 测速是独立历史证据。0.2.0 已增加采样与 Orbit 实验适配，实际 STM32 与已安装 VS Code 验收仍待完成；HID、统一产品安装界面和更多软件兼容性另行验证。

2026-10-14：固件 0.1.5 / 服务 0.1.1 补齐 Connect 前设置时钟的会话规则。修补 UDE 已通过本地测试签名加载，虚拟 USB 空读取消和 pyOCD 0.45.1 的目标识别、暂停、读 PC、继续运行均通过。OpenOCD 经虚拟 USB / Wi-Fi 完成 64 KiB 烧录、校验约 12.85 秒及三线调试验收；原 Flash 已恢复，完整回读匹配备份，选项字节未变。API / 虚拟 USB 互斥和 STA 断连自动恢复也通过，重挂载后再次验收空读及 pyOCD 成功。证据见 [Windows 连接组件](docs/windows-connector.md) 和 [驱动取消问题](docs/virtual-usb-cancellation.md)。

当前开发电脑使用“官方 filter / CLI ＋本地测试签名修补 UDE”，Secure Boot 关闭、TESTSIGNING 生效、内存完整性保持开启。正式微软驱动签名与分发仍待完成；物理 NRST、HID、Keil / IAR 尚未验收。测试模式状态保留用于后续开发，回退脚本已准备但未实际运行。
