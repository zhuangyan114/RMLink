# RMLink

基于 ESP32-S3 的 USB / Wi-Fi CMSIS-DAP 调试器，面向 Orbit 扩展板端采样与批量操作。

当前开发候选为 **固件0.2.28／连接服务与SDK 0.2.8**。“五条独立三级指针链＋五个普通变量”，72 MHz F103、4 MHz SWD 下15分钟三订阅共存平均 **1108.68 Hz**，全部应用流约 **110.01 KiB/s**；无数据无效、板端丢帧或实时消费者缺口，仍有期限跳过、时序抖动和网络发送长尾。过程、失败及布局边界见[千赫兹优化记录](docs/mixed-1khz-optimization.md)。

新增[LibXR SWD参考与实板试验](docs/libxr-swd-review.md)：已拉取固定版本，试验字节展开与奇偶校验优化。实际线时钟校准后未证明优于原版，候选未采用，保留原0.2.28及新增底层协议模型；“1M/s”的原测试条件未知。

标准CMSIS-DAP经虚拟USB接入。0.2.28的OpenOCD完整烧录/调试和原生／虚拟USB完整回读通过；pyOCD保留此前版本的验收结论。增强目标接口当前针对STM32F103C8，正式签名分发、Keil/IAR及已安装VS Code界面仍待单独验收。

ESP32缓存镜像并通过唯一SWD执行器自主烧录。同一全密度64 KiB镜像，默认包含板端完整回读比较，五次总耗时中位 **3.623 s**；可再加主机SHA256回读。支持进度、取消、断连停止和新作业恢复，未知写入不自动重放。见[缓存烧录协议](docs/buffered-flash-protocol.md)。

Watch、Timeline、RTT均为板端订阅。十条独立四级链与Watch/RTT共存的一小时实测 **328.30 Hz**，无无效数据、SWD失败和板端缓存丢弃。新增“五个三级指针＋五个普通变量”组合，5000 Hz请求下共存实测：独立链 **676.22 Hz**、共享链 **1139.74 Hz**（各三次×5 s中位）。请求频率、实际频率和超期分别记录，见[时钟与混合采样对照](docs/clock-calibration-0216.md)。

默认请求SWD **4000 kHz**；0.2.21起内部RMT测得典型DP脉冲约4 MHz，未用外部仪器确认目标端模拟波形。后续F103测试默认72 MHz并核对RCC，STM32测试后保留新程序。架构见[订阅设计](docs/subscription-design.md)，第三方前端使用[公开API与Python/Node/C SDK](docs/api-v1.md)。历史检查点与失败证据保留在[开发进度](docs/development-status.md)，旧包不覆盖。

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
- [实现检查点与分层验证](docs/subscriptions-implementation.md)
- [最新订阅性能与实板证据](docs/subscription-performance-20261015.md)
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

连接服务已实现 mDNS 发现、明确配对、后台重连、自动重挂载、本机 API 与虚拟 USB / API 会话互斥；独立 EXE 无需安装 Python。此前无 OTG 启动与 0.1.1 测速是独立历史证据。Orbit 实验适配位于隔离工作树；已安装 VS Code 界面、HID、统一产品安装界面和更多软件兼容性仍须分别验证。

2026-10-14：固件 0.1.5 / 服务 0.1.1 补齐 Connect 前设置时钟的会话规则。修补 UDE 已通过本地测试签名加载，虚拟 USB 空读取消和 pyOCD 0.45.1 的目标识别、暂停、读 PC、继续运行均通过。OpenOCD 经虚拟 USB / Wi-Fi 完成 64 KiB 烧录、校验约 12.85 秒及三线调试验收；原 Flash 已恢复，完整回读匹配备份，选项字节未变。API / 虚拟 USB 互斥和 STA 断连自动恢复也通过，重挂载后再次验收空读及 pyOCD 成功。证据见 [Windows 连接组件](docs/windows-connector.md) 和 [驱动取消问题](docs/virtual-usb-cancellation.md)。

当前开发电脑使用“官方 filter / CLI ＋本地测试签名修补 UDE”，Secure Boot 关闭、TESTSIGNING 生效、内存完整性保持开启。正式微软驱动签名与分发仍待完成；物理 NRST、HID、Keil / IAR 尚未验收。测试模式状态保留用于后续开发，回退脚本已准备但未实际运行。
