# Windows 统一连接组件评估

> 历史评估：2026-10-19 已决定停止虚拟 USB 路线，标准无线 CMSIS-DAP 仅通过 TCP 提供给 OpenOCD。下文 USB/IP、UDE 与签名方案保留作技术记录，后续以[当前项目方向](current-direction.md)为准。

2026-10-13：下文保留最初源码评估。现已选择本机 USB/IP 代理，后台服务、签名驱动封装及 OpenOCD 实板验收已完成，实际使用与证据见 [Windows 连接组件](windows-connector.md)。

## 当前版本的控制与数据路径

usbip-win2 固定版本见 reference/versions.json，许可证为 BSD-2-Clause，原 LICENSE.txt 保留。

- userspace/libusbip/src/vhci.cpp：open 打开 VHCI 设备接口；attach 使用 PLUGIN_HARDWARE / PLUGIN_HARDWARE_ONCE 的 DeviceIoControl，参数含 hostname、service、busid、serial 和接收模式。
- 同文件提供 get_imported_devices、detach、stop_attach_attempts、read_device_state；后台服务可调用 SDK 管理挂载、取消、状态与断连，而无需让用户手工执行命令。
- drivers/ude/vhci_ioctl.cpp：导入阶段发送 OP_REQ_IMPORT、接收 OP_REP_IMPORT，建立 USB 设备信息，经 UdecxUsbDevicePlugIn 插入虚拟设备并启动数据接收。
- drivers/libdrv/wsk_cpp.cpp：驱动通过 WSK socket 连接与收发；USB 请求数据不是经由用户态 attach SDK 逐包传送。

以上表明 SDK 能封装控制操作；它本身没有自动提供 RMLink 发现、配对、协议翻译、Orbit 本地 API 或物理目标占用策略。SDK 的公开接口为 C++17，当前实现 / 构建使用 C++23、WDK，x64 / ARM64 与驱动签名分别验证。

## 对 RMLink 的约束

当前板端仅提供 8 字节头的 TCP DAP，未提供 USBIP 导入及 URB 协议，不能把端口 3241 直接交给 usbip-win2 当作 USBIP 服务器。

后续可以评估两条具体路径：

1. 板端增加 USBIP 服务器：吸收 ESP32-Debugger 的描述符 / USBIP / elaphureLink 思路，把 DAP 请求统一送入现有执行器。
2. 本地服务提供 USBIP 代理：虚拟驱动连接本地 USBIP 端点，服务模拟所需 USB 描述符和控制请求，将 Bulk DAP 传至板端 TCP。需验证 URB、超时、取消与断连，不能只包装 attach。

首版选择第二条：在电脑侧完成 USB/IP / TCP DAP 转换，保留当前板端 TCP 与统一 SWD 执行器。Windows 0.9.8.1 驱动的真实 URB 差异已经实测并适配，包括普通 URB 的 -1 标记和 WinUSB 扩大的 IN 请求。板端 USB/IP 与自研 UDE 保留为后续评估方向。

最终服务还需统一管理物理设备发现、身份配对、USB / Wi-Fi 选择、客户端会话、断连恢复和 Orbit 本地接口。虚拟 USB 软件与 Orbit 不能分别持有竞争的目标连接；板端独占约束必须同时生效。直接 USB、TCP 和未来 USBIP 共用相同 SWD 内核。

## 验证顺序

STM32 有线 / TCP 验收后，已完成 USB/IP 导入和 OpenOCD 虚拟 Bulk 的完整目标验证。pyOCD 0.45.1 尚未通过启动空读取消；Keil / IAR、HID 与其他系统版本仍待逐项验证。具体软件结果见 windows-connector.md，不作全软件兼容承诺。

正式分发另行完成签名、安装权限、Windows 版本与 x64 / ARM64 兼容性验收；现有参考仓库未自动形成可分发的统一连接组件。
