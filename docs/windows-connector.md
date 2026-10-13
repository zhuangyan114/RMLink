# RMLink Windows 连接组件

2026-10-13，连接服务原型 0.1.0，配套固件 0.1.4。已完成服务构建、签名驱动安装、WinUSB 枚举、OpenOCD 经 Wi-Fi 的 STM32 烧录 / 调试、真实 API 互斥与自动重连验收。pyOCD 0.45.1 的启动空读超时未通过，暂不作为通用兼容发布版。

## 实现路径

标准调试软件 → Windows usbip-win2 UDE 驱动 → 本机 USB/IP 服务（127.0.0.1:3240）→ 统一会话执行器 → Wi-Fi TCP DAP → ESP32-S3 SWD 执行器 → STM32。

Orbit 的后续专用后端通过本机 HTTP API（127.0.0.1:3243）使用同一个会话执行器。每块探针仅建立一个物理 TCP 连接；USB/IP 客户端与 API 客户端通过 DAP_Connect 取得整个目标会话的所有权，外来连接和 Disconnect 被拒绝。控制、watch、timeline、background 请求按优先级调度，已执行的单批命令不会被拆散。断连使原会话失效，写请求不自动重放。

首版复用 usbip-win2 的签名驱动，并封装其公开 CLI 的 attach / detach；控制 CLI 不承担 USB 数据转发，数据由内核 WSK socket 经本机 USB/IP 端点传输。RMLink 自有服务实现 USB 描述符、控制请求、Bulk URB、取消、发现、配对与目标会话管理。驱动源码保留在忽略的 reference/ 下，没有并入本仓库。

## 构建与操作

```powershell
cd D:\STM32\RMLink
.\scripts\Build-Connector.ps1
.\scripts\Start-Connector.ps1
$connector = '.\artifacts\build\connector\dist\RMLinkConnector.exe'
& $connector devices
& $connector pair 50787d15a924
& $connector status
# 安装驱动后才执行 mount；无需填写 IP 或手工执行 usbip attach。
& $connector mount 50787d15a924
& $connector unmount 50787d15a924
& $connector stop
```

构建使用隔离环境和 PyInstaller 6.16.0，产出独立 EXE，使用者无需安装 Python。启动脚本运行隐藏的用户进程，日志写入 artifacts/logs；当前尚未注册 Windows SCM 服务、开机启动或提供图形安装向导。

设备通过热点接口上的 mDNS 自动发现，使用固件厂商命令 80/09 获取完整 MAC 作为 device_id，首次配对需要明确选择设备。此身份校验用于避免误连，不是加密认证。配对和本机 API token 保存在 `%USERPROFILE%\.rmlink\connector.json`，启动脚本设置用户 / SYSTEM / Administrators 文件权限；不要复制该文件到代码库。HTTP API 要求 Bearer token，拒绝浏览器 Origin 请求。标准 USB/IP 环回端点没有 token 认证，适用于受信任本机客户端。

挂载成功后记录 auto_mount，服务在线时负责发现、重连和再次挂载。目标原会话在断连后失效，客户端必须新建会话；恢复网络不恢复之前的写命令。自动重挂载仍需要 Windows 实机验收。

## 驱动安装包

固定官方包为 [usbip-win2 0.9.8.1 x64](https://github.com/vadimgrn/usbip-win2/releases/tag/v.0.9.8.1)，本地文件在 artifacts/tools/usbip-win2。安装包 SHA256：`38CAD6D4432B52D5BB9409D9AD03B72FDFFC4ADA4CD3A48FBECA1A2752A8518A`。当前 Authenticode 状态 Valid，安装包发布者 Cloudyne Systems (Scheibling Consulting AB)；安装包签名与驱动目录签名是两项独立检查。

```powershell
# 默认只核验、展示，不安装。
.\scripts\Install-VirtualUsbDriver.ps1
# 经用户确认后才执行系统安装，会弹出 Windows UAC。
.\scripts\Install-VirtualUsbDriver.ps1 -Install
```

[上游 README](https://github.com/vadimgrn/usbip-win2#installation) 明确说明安装会重启所有 USB 3.0 Hub，可能短暂影响当前 USB 设备。因此系统安装作为单独确认步骤。脚本不请求自动重启电脑。当前没有要求关闭 Secure Boot 或开启测试签名。

引用源码版本记录在 reference-versions.json；实际发行包独立固定为 0.9.8.1。上游公告该版本存在覆盖序列号的已知问题；本轮未使用覆盖参数，WinUSB 实际读到 `RM50787D15A924`，OpenOCD 用该序列号选择虚拟探针。usbip CLI 的 port 输出仍显示空 serial，不能与 USB 描述符序列号混淆。usbip-win2 使用 BSD-2-Clause，构建目录保留 LICENSE；打包同时保留 PyInstaller 的许可证及 bootloader exception。

本机 `usbip2_ude` 与 `usbip2_filter` 均已加载，两个 CAT 的 Authenticode 状态 Valid，签名者为 Microsoft Windows Hardware Compatibility Publisher。安装器记录需重启 Windows，当前实板验收在没有重启电脑的情况下进行；没有自动重启电脑。

## 本地 API 与 Orbit 接入

Node SDK 在 `host/sdk/rmlink-client.mjs`。`init()` 读取本机 token；`open(device_id)` 新建租约并每 5 秒续期；`exchange(packets, priority)` 每批 1–8 个、每个最多 64 字节的 CMSIS-DAP 命令；`abort()` 单独发送无响应的 TransferAbort；`close()` 释放目标。租约 30 秒过期。失联通过 onLost 通知调用方，并要求重新取得会话。

| 接口 | 用途 |
| --- | --- |
| GET /v1/status、/v1/devices | 状态、设备发现与占用 |
| POST /v1/pair、/v1/mount、/v1/unmount | device_id 配对与虚拟 USB 管理 |
| POST /v1/sessions | 新建客户端租约 |
| POST /v1/sessions/{id}/dap | packets 十六进制数组与 priority；返回 responses |
| POST /v1/sessions/{id}/abort、/heartbeat | 中断传输与续期 |
| DELETE /v1/sessions/{id} | 清理待执行请求并释放所有权 |

SDK 是后续 Orbit transport 的基础；实际 Orbit 后端尚未修改，板端采样尚未实现。接入时保留 Orbit 原有 SessionTargetSelector / NativeScheduler，避免在调试、Watch 与 Timeline 之间新建竞争的探针连接。

## 验证状态

- 主机测试：16 项通过，涵盖所有权、优先级、Abort、关闭时取消待执行请求、描述符、DNS 边界、USB/IP 控制 / Bulk / unlink、Windows 普通 URB 的 -1 标记 / 512 字节 IN 与 API 认证。
- 实板数据通路：自动发现 `50787d15a924`；API 与独立 USB/IP 协议客户端均经 Wi-Fi 读到 STM32 CPUID `0x411fc231`；双向互斥通过；连续 200 个响应核对通过；结束时服务目标 owner 为空。证据 artifacts/logs/verify-connector-device.json。
- 固件 0.1.4：新增只读设备标识命令，已构建、刷写、验证；SWD 内核沿用此前 0.1.3 的实板验收版本。
- Windows 虚拟 USB：标准 GUID 可被 WinUSB 打开，bcdDevice=0x0100 与直连设备的 0x0002 区分；正常 DAP 短响应核对通过。实际复用 usbip-win2 0.9.8.1 的签名驱动，没有自行构建内核驱动。
- OpenOCD：经 usb_bulk 选择虚拟序列号，64 KiB 烧录 / 校验 / 软件复位 11324.08 ms、无 Error 行；独立完整回读 8627.22 ms；暂停 / 继续、寄存器、3072 字节 RAM、FPB / 软件断点、单步、DWT、FAULT 恢复、SWD_Sequence、异步 Abort 与 1 秒执行期限通过。原 Flash 恢复及完整 SHA256 对比通过，选项字节未变、核心继续运行。证据 accept-virtual-usb.json。
- 实际 WinUSB / API：两个方向的占用互斥均通过。通过 USB 厂商命令让单块 RMLink STA 断开，PC 热点保持开启；旧 API 会话 3032.88 ms 报告失联，5279.69 ms 自动恢复连接 / 虚拟挂载，旧租约保持失效，新租约与重挂载后的虚拟探针均读到 CPUID。证据 verify-connector-reconnect.json；这不代替电脑热点实际关闭、DHCP 换址或完全断电测试。
- Node SDK：通过真实服务连接、8 包查询与释放，协议版本查询为 CMSIS-DAP 2.1.2，RMLink 固件版本独立为 0.1.4。实际 Orbit 后端尚未接入。

| 调试软件 / 路径 | 当前结果 |
| --- | --- |
| OpenOCD v0.12.0-esp32-20260831，虚拟 USB Bulk | STM32 三线完整验收通过 |
| pyOCD 0.45.1，虚拟 USB Bulk | 未通过：启动 start_rx 的空 Bulk 读取 / 取消后仍被主机报告成功，导致清空循环持续运行；受限时测试已终止，未开始目标操作 |
| Keil / IAR 等，Bulk 或 HID | 尚未测试；HID 尚未实现 |

pyOCD 问题另有 WinUSB 空读复现与 USB/IP 元数据跟踪：可见 IN 和 UNLINK 成对到达；服务按标准移除待执行 IN 并回复 RET_UNLINK。取消后主机仍返回成功及非 DAP 数据，具体 Windows / 驱动 / libusb 完成链的根因尚未确认。zero-copy 与 low-latency 模式均未让 pyOCD 启动通过。不能以正常数据和模拟 unlink 测试通过替代 Windows 空读取消验收。重启后的状态及其他驱动版本需独立确认，当前不自行改驱动签名或降低系统签名要求。

服务目前只代理 Wi-Fi TCP。直连 USB 仍由标准调试软件直接使用，自动 USB / Wi-Fi 选路尚未实现。`owner` 是服务客户端所有权，板端外部 USB 占用仍由固件拒绝 Connect；服务状态没有实时展示外部 USB owner。虚拟导入连接保持期间无法识别每个 Windows 调试软件的句柄退出，正常退出应发送 DAP_Disconnect；遗留虚拟占用可用 unmount / mount 清理。

当前只提供 CMSIS-DAP v2 Bulk 虚拟接口；HID、JTAG、SWO 等尚未支持，不能宣称所有 CMSIS-DAP 软件都兼容。物理 NRST 缺线的限制仍存在。
