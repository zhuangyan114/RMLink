# Windows 统一连接组件评估

2026-10-13：源码检查阶段。未构建 / 安装 usbip-win2 驱动，未实现 RMLink 后台服务。

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

先以第一条作为板端复用方案的评估候选，兼容性或资源表现不合适再比较代理路径 / 自研 UDE；当前尚未选择或实现最终数据路径。

最终服务还需统一管理物理设备发现、身份配对、USB / Wi-Fi 选择、客户端会话、断连恢复和 Orbit 本地接口。虚拟 USB 软件与 Orbit 不能分别持有竞争的目标连接；板端独占约束必须同时生效。直接 USB、TCP 和未来 USBIP 共用相同 SWD 内核。

## 验证顺序

先完成 STM32 有线 / TCP 目标验收，再做 USBIP 最小导入和一个软件的 Bulk CMSIS-DAP 验证；依次测试 pyOCD、OpenOCD、Keil 等实际安装版本的识别、Bulk / HID 需求、连接 / 退出、超时和热点断连。名称仅是待测清单，本次没有兼容性通过结论。

正式分发另行完成签名、安装权限、Windows 版本与 x64 / ARM64 兼容性验收；现有参考仓库未自动形成可分发的统一连接组件。
