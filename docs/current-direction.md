# 当前项目方向

更新：2026-10-15。以用户最新决定为准；最初方向记录保留讨论历史。完整实施设计见 [订阅采集与 Orbit 接入方案](subscription-design.md)，当前 0.2.5 实现与证据见 [实施记录](subscriptions-implementation.md) 和 [采样性能记录](subscription-performance-20261015.md)。

RMLink 使用普通 ESP32-S3 开发板，以乐鑫 esp-usb-bridge 为固件基座，保留一套 ARM CMSIS-DAP / SWD 内核，为 Orbit 增加无线接入、板端采样和批量操作。

首要目标是提高 Timeline 的实际采样率。Watch、Timeline、RTT 全部采用板端订阅和批量回传；Watch 复用 Timeline 已采集字段，RTT 独立限额，三者共用唯一目标 owner。

RMLink 驱动配套组件面向所有前端：以内核虚拟 USB + 用户态服务/通用目标引擎 + 公开 SDK 统一分发。通用设备管理、目标控制、计划编译、订阅、缓存和数据分发放在组件中；Orbit 通过公开接口适配，核心能力不依赖 Orbit。标准软件继续走虚拟 USB，采用增强 API 的其他前端可获得板端订阅能力。

默认请求 SWD **4000 kHz（4 MHz）**已统一到固件、目标引擎、SDK 和主要验收脚本；用户显式设置优先，第三方标准时钟命令仍按其请求执行。STM32 接线恢复后已完成该请求时钟下的虚拟 USB 全流程与增强 Flash、有效采样验收，实际 SWCLK 另行量测。

STM32 测试默认保留新 fixture，旧备份及历史恢复证据保留。验收脚本将恢复旧程序设为显式 --restore-original；当前目标已烧录新的 64 KiB 采样／RTT fixture，以其字段标识、指针链和二进制 RTT 校验数据为预期。

## 协议与传输

| 协议 | 有线 | 无线 |
| --- | --- | --- |
| 标准 CMSIS-DAP | 保留原生 USB Bulk 能力；HID 按兼容性需求评估 | 统一使用虚拟 USB；TCP DAP 保留为 Connector 内部承载，不再主推调试软件直接 TCP |
| 定制 RMLink | 0x81 厂商控制及USB_PULL已实现，服务提供相同订阅API | 0x81 控制 + TCP3244 二进制流已实现，三类订阅共用一个 owner |

USB、网络和后台采样共用一个固定 CPU 核的目标执行器。同一目标只允许一个控制会话；控制队列优先，采样拆成有界小块并保持绝对周期。整段控制屏障暂停采样，暂停核心后 Timeline 保持暂停，继续运行恢复控制暂停的任务。逐次 GPIO 加锁不能替代会话所有权。

无线拓扑固定为：电脑开启 2.4 GHz 热点 → RMLink STA 加入 → 电脑访问 RMLink。电脑不需要额外插一块无线发送器；开发板和目标板均需供电。

## 基座与引用

三套固件参考同时保留在 reference/，固定提交见 reference-versions.json。firmware/ 为乐鑫基座的开发源码，现由根目录 Git 统一管理；参考仓库和构建产物均排除。网络能力吸收前两套的协议、单端与发现思路，保留一套 SWD 实现与第三方许可证。

Orbit 的实际所有权 / 后端链已核对，实验适配位于独立工作树，通过公开 SDK 选择唯一服务 owner，Watch／Timeline／RTT 不再由主机逐样读取目标。原 Orbit 用户工作区保持原状；当前适配只经过构建及模型验证。Windows 使用 usbip-win2 + 自有本机 USB/IP / TCP DAP 代理，当前 UDE 为本地测试签名修补版本；正式签名分发与其他软件逐项验收仍待完成。

## 硬件

实物为 DOIT 标识的双 USB-C ESP32-S3 开发板。CH343 下载 / 日志口为本机 COM13，另一个直通 USB&OTG 口已识别为原生 USB。芯片工具实测为 16 MB Flash、8 MB 内置 PSRAM；当前固件未启用 PSRAM。

GPIO19 / GPIO20 留给原生 USB。GPIO4 / GPIO5 接 STM32F103C8 的 SWDIO / SWCLK 并共地，GPIO6 预留 NRST；用户缺少额外杜邦线，实体 NRST 尚未验收。当前 target profile 已通过 USB、TCP 与 Windows 虚拟 Bulk 的三线烧录 / 调试，目标电压与实际时钟未仪表量测。

## 推进顺序

以下列出原有项目阶段；前 3 阶段及 Windows 组件原型已有独立证据。下一轮按新方案 P0–P5 推进：4 MHz 与测试基线 → 板端 SWD 基准和协议 → Timeline 纵向闭环 → Watch/指针链/RTT → Orbit 三订阅接入 → 有线与产品化。

1. 建立固定版本的基座、板级配置、构建脚本和源码来源记录。
2. 接上 STM32，验收 USB 识别、SWD 连接、内存读写、复位、烧录和调试。
3. 连接电脑热点，验收 TCP、USB 配网、参数保存和网络独立恢复。目标未接线期间已先开展这一阶段的传输验证。
4. 接入 Orbit 专用后端，实现采样列表、周期和条件；板端时间戳、序号、缓存、批量回传及丢样 / 溢出状态。复用原有目标所有权。
5. 实现 Windows 自动连接组件：发现、配对、传输选择、占用管理、重连、本地 Orbit 接口和虚拟 USB；逐项验证主流调试软件的 HID / Bulk、识别和超时。

性能对照使用同一目标板、目标固件及变量集合，分别测量现成调试器、USB DAP、Wi-Fi 逐请求和板端批量采样。Wi-Fi 带宽、SWD 时钟、内存吞吐、烧录校验、采样率、控制延迟、丢样和恢复时间分开记录。构建或设备查询通过，不能推导 STM32 调试和性能验收通过。

实际完成情况与证据见 development-status.md；操作见 quickstart.md；已实现命令见 protocol-v1.md。
