# 当前项目方向

## 2026-10-19 无线 CMSIS-DAP 接入决策

用户决定：**不再通过虚拟 USB 支持无线 CMSIS-DAP，只通过 TCP 向 OpenOCD 提供 CMSIS-DAP。** 本节覆盖此前将虚拟 USB 作为无线兼容主线的决定。

- 标准无线链路为 `OpenOCD → TCP/Wi-Fi → RMLink → 唯一 SWD 执行器 → 目标 MCU`。复用现有 TCP DAP 承载，板端默认端口为 **3241**；增强采样数据端口 3244 单独保留。
- 停止推进 USB/IP、usbip-win2/UDE、虚拟 USB 挂载及其驱动签名分发路线，不再以虚拟 USB 兼容其他调试软件作为交付目标。
- 保留原生 USB CMSIS-DAP 有线能力，以及 RMLink 增强 API、SDK、板端 Watch/Timeline/RTT 订阅、缓存烧录和统一目标算法包。设备发现、USB 配网和 OTA 仍由用户态组件承担。
- 所有入口继续共用唯一目标 owner 和 SWD 执行器；OpenOCD 与增强会话的目标访问必须保持互斥，不能各自占用同一目标。

[OpenOCD 官方手册](https://www.openocd.org/doc/html/Debug-Adapter-Configuration.html)提供 `cmsis-dap backend tcp`、`cmsis-dap tcp host` 和 `cmsis-dap tcp port`。选定的 OpenOCD 构建必须包含该后端，并显式设置 RMLink 端口 3241；手册中的默认端口为 4441。

切换前源码已形成本地检查点 `022ff12`。随后按用户要求删除当前源码中的 USB/IP 代理、描述符模拟、驱动封装、挂载 API/CLI、自动挂载、驱动构建/安装入口与专用验收脚本；OTA、传输切换和原生 USB 工具同步解除相关依赖。终端收敛为七个菜单。已发布包与本机已安装程序/驱动未替换或卸载；历史实现可在该检查点查阅，验收结果保留其原条件。

下一步先固定支持 TCP 后端的 Windows OpenOCD 构建，处理并复验[历史 TCP 排队/socket 错误](stm32-acceptance.md#windows-openocd-兼容限制)，再验证连接、读写、烧录及独立回读、调试、会话互斥和断线释放。此前虚拟 USB 验收通过不能代替 TCP 路径验收；仅开启 quirk 后烧录成功但仍有主机错误日志，也不作为正常默认模式通过。

## 已有能力与实施基线

2026-10-19 新决定与实现：Orbit 普通 CMSIS-DAP 和 RMLink 共用 [统一目标算法包](shared-target-package.md)，以 `host/algorithms/targets.json` 和可构建源码为唯一描述来源，覆盖 F103C8、F407VE、F407IG、H723VG。保留唯一 SWD owner、板端订阅和缓存烧录；用身份/容量/包 tag 与内存窗口拒绝不匹配。新目标本轮只有源码、模型和构建证据，实板验收单独推进。以下版本及性能数据保留其历史条件。

更新：2026-10-16。以用户最新决定为准；最初方向记录保留讨论历史。完整实施设计见 [订阅采集与 Orbit 接入方案](subscription-design.md)。当前固件候选0.2.28、连接服务／SDK 0.2.8；缓存烧录已实现，本轮只优化五条独立三级指针链＋五个普通字段，结果与各版失败见 [千赫兹优化记录](mixed-1khz-optimization.md)。更早的 [0.2.11记录](memap-reuse-0211.md) 等保留其原条件，不代表当前版本验收。

RMLink 使用普通 ESP32-S3 开发板，以乐鑫 esp-usb-bridge 为固件基座，保留一套 ARM CMSIS-DAP / SWD 内核，为 Orbit 增加无线接入、板端采样和批量操作。

首要目标是提高 Timeline 的实际采样率。Watch、Timeline、RTT 全部采用板端订阅和批量回传；Watch 复用 Timeline 已采集字段，RTT 独立限额，三者共用唯一目标 owner。

RMLink 用户态连接组件通过通用目标引擎和公开 SDK 为增强前端提供设备管理、目标控制、计划编译、订阅、缓存与数据分发；Orbit 通过公开接口适配，核心能力不依赖 Orbit。标准无线 CMSIS-DAP 仅向 OpenOCD 提供 TCP 接入，后续分发不再以虚拟 USB 内核驱动为前提。

默认请求 SWD **4000 kHz（4 MHz）**已统一到固件、目标引擎、SDK 和主要验收脚本；用户显式设置优先，第三方标准时钟命令仍按其请求执行。内部RMT已观察到典型DP周期约4 MHz，周期并不完全均匀，目标引脚的外部模拟波形尚未量测。

后续 STM32F103 性能测试统一 **72 MHz**。测试固件使用已识别的8 MHz HSE、PLL×9，并正确配置APB1、ADC与Flash等待；性能脚本检查RCC并拒绝HSI回退。此决定只规定测试基线，连接服务不会自动修改用户应用的系统时钟。

STM32 测试默认保留新 fixture，旧备份及历史恢复证据保留。验收脚本将恢复旧程序设为显式 --restore-original；当前目标已烧录新的 64 KiB 采样／RTT fixture，以其字段标识、指针链和二进制 RTT 校验数据为预期。

## 协议与传输

| 协议 | 有线 | 无线 |
| --- | --- | --- |
| 标准 CMSIS-DAP | 保留原生 USB Bulk 能力；HID 按兼容性需求评估 | 仅通过 TCP 向 OpenOCD 提供 CMSIS-DAP，不再走虚拟 USB |
| 定制 RMLink | 0x81 控制 + 独立 USB Bulk 连续流；旧固件保留 USB_PULL，服务提供相同订阅 API | 0x81 控制 + TCP3244 二进制流已实现，三类订阅共用一个 owner |

USB、网络和后台采样共用一个固定 CPU 核的目标执行器。同一目标只允许一个控制会话；控制队列优先，采样拆成有界小块并保持绝对周期。整段控制屏障暂停采样，暂停核心后 Timeline 保持暂停，继续运行恢复控制暂停的任务。逐次 GPIO 加锁不能替代会话所有权。

无线拓扑固定为：电脑开启 2.4 GHz 热点 → RMLink STA 加入 → 电脑访问 RMLink。电脑不需要额外插一块无线发送器；开发板和目标板均需供电。

## 基座与引用

三套固件参考同时保留在 reference/，固定提交见 reference-versions.json。firmware/ 为乐鑫基座的开发源码，现由根目录 Git 统一管理；参考仓库和构建产物均排除。网络能力吸收前两套的协议、单端与发现思路，保留一套 SWD 实现与第三方许可证。

Orbit 的实际所有权 / 后端链已核对，实验适配位于独立工作树，通过公开 SDK 选择唯一服务 owner，Watch／Timeline／RTT 不再由主机逐样读取目标。原 Orbit 用户工作区保持原状；真实调试适配器子进程已通过实际目标的表达式、三订阅与暂停／继续检查，已安装 VS Code 的界面仍未验收。Windows 曾使用 usbip-win2 + 自有本机 USB/IP / TCP DAP 代理，本地测试签名修补 UDE 的结果作为历史证据保留；该虚拟 USB 路线已停止推进。

## 硬件

实物为 DOIT 标识的双 USB-C ESP32-S3 开发板。CH343 下载 / 日志口为本机 COM13，另一个直通 USB&OTG 口已识别为原生 USB。芯片工具实测为 16 MB Flash、8 MB Octal PSRAM；当前profile已启用PSRAM，用于镜像、采样环及Wi-Fi/lwIP能力分配。普通malloc和USB DMA仍使用内部RAM。

GPIO19 / GPIO20 留给原生 USB。GPIO4 / GPIO5 接 STM32F103C8 的 SWDIO / SWCLK 并共地，GPIO6 预留 NRST；用户缺少额外杜邦线，实体 NRST 尚未验收。当前 target profile 已通过 USB、TCP 与 Windows 虚拟 Bulk 的三线烧录 / 调试，目标电压与实际时钟未仪表量测。

## 推进顺序

以下列出原有项目阶段；前 3 阶段及 Windows 组件原型已有独立证据。下一轮按新方案 P0–P5 推进：4 MHz 与测试基线 → 板端 SWD 基准和协议 → Timeline 纵向闭环 → Watch/指针链/RTT → Orbit 三订阅接入 → 有线与产品化。

1. 建立固定版本的基座、板级配置、构建脚本和源码来源记录。
2. 接上 STM32，验收 USB 识别、SWD 连接、内存读写、复位、烧录和调试。
3. 连接电脑热点，验收 TCP、USB 配网、参数保存和网络独立恢复。目标未接线期间已先开展这一阶段的传输验证。
4. 接入 Orbit 专用后端，实现采样列表、周期和条件；板端时间戳、序号、缓存、批量回传及丢样 / 溢出状态。复用原有目标所有权。
5. 收敛用户态连接组件的发现、配对、传输选择、占用管理、重连和增强 API；标准无线入口提供 OpenOCD TCP 配置与配套版本，逐项验收识别、排队、超时及断线恢复。

性能对照使用同一目标板、目标固件及变量集合，分别测量现成调试器、USB DAP、Wi-Fi 逐请求和板端批量采样。Wi-Fi 带宽、SWD 时钟、内存吞吐、烧录校验、采样率、控制延迟、丢样和恢复时间分开记录。构建或设备查询通过，不能推导 STM32 调试和性能验收通过。

实际完成情况与证据见 development-status.md；操作见 quickstart.md；已实现命令见 protocol-v1.md。
