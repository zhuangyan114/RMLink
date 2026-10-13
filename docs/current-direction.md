# 当前项目方向

更新：2026-10-13。以用户本次开发要求为准；最初方向记录保留讨论历史。

RMLink 使用普通 ESP32-S3 开发板，以乐鑫 esp-usb-bridge 为固件基座，保留一套 ARM CMSIS-DAP / SWD 内核，为 Orbit 增加无线接入、板端采样和批量操作。

## 协议与传输

| 协议 | 有线 | 无线 |
| --- | --- | --- |
| 标准 CMSIS-DAP | USB Bulk；HID 根据软件兼容性再评估 | TCP DAP；USBIP 路径另行实现并验证 |
| 定制 RMLink | 首版使用 CMSIS-DAP 厂商命令 | 同一命令通过 TCP 承载；后续评估采样连续数据接口 |

USB、网络和未来后台采样共用一个固定 CPU 核的目标执行器。同一目标只允许一个控制会话；未来执行调度遵循 control > watch > timeline > background，控制请求优先于后台采样。逐次 GPIO 加锁不能替代会话所有权。

无线拓扑固定为：电脑开启 2.4 GHz 热点 → RMLink STA 加入 → 电脑访问 RMLink。电脑不需要额外插一块无线发送器；开发板和目标板均需供电。

## 基座与引用

三套固件参考同时保留在 reference/，版本见 source-audit.md 和 reference/versions.json。firmware/ 为独立的乐鑫基座开发仓库；网络能力吸收前两套的协议、单端与发现思路，不引入另一套 SWD 实现。参考源码和第三方许可证保留。

Orbit 的实际目标所有权、后端调用链已经开始核对；尚未修改或接入 Orbit。Windows 组件先评估 usbip-win2 的控制接口及数据路径，兼容性验证后再决定是否自研 UDE 驱动。

## 硬件

实物为 DOIT 标识的双 USB-C ESP32-S3 开发板。CH343 下载 / 日志口为本机 COM13，另一个直通 USB&OTG 口已识别为原生 USB。芯片工具实测为 16 MB Flash、8 MB 内置 PSRAM；当前固件未启用 PSRAM。

GPIO19 / GPIO20 留给原生 USB。用户已确认 GPIO4 / GPIO5 / GPIO6 分别接 STM32F103C8T8 的 SWDIO / SWCLK / NRST，并连接 GND；当前烧入 target profile，已通过 USB / TCP 只读 SWD 和会话互斥测试。目标电压未仪表量测；NRST 实际复位、暂停 / 继续和烧录仍待验收。

## 推进顺序

1. 建立固定版本的基座、板级配置、构建脚本和源码来源记录。
2. 接上 STM32，验收 USB 识别、SWD 连接、内存读写、复位、烧录和调试。
3. 连接电脑热点，验收 TCP、USB 配网、参数保存和网络独立恢复。目标未接线期间已先开展这一阶段的传输验证。
4. 接入 Orbit 专用后端，实现采样列表、周期和条件；板端时间戳、序号、缓存、批量回传及丢样 / 溢出状态。复用原有目标所有权。
5. 实现 Windows 自动连接组件：发现、配对、传输选择、占用管理、重连、本地 Orbit 接口和虚拟 USB；逐项验证主流调试软件的 HID / Bulk、识别和超时。

性能对照使用同一目标板、目标固件及变量集合，分别测量现成调试器、USB DAP、Wi-Fi 逐请求和板端批量采样。Wi-Fi 带宽、SWD 时钟、内存吞吐、烧录校验、采样率、控制延迟、丢样和恢复时间分开记录。构建或设备查询通过，不能推导 STM32 调试和性能验收通过。

实际完成情况与证据见 development-status.md；操作见 quickstart.md；已实现命令见 protocol-v1.md。
