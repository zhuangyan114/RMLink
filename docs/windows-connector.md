# RMLink Windows 连接组件

2026-10-15：当前0.2.3将通用目标控制、三类板端订阅、缓存、二进制分发、原始录制和F103C8页镜像Flash接口集中到用户态服务。独立Python／Node／WinHTTP C SDK及公开API供第三方前端调用，Orbit实验适配通过相同API。STM32接线恢复后，虚拟USB请求4 MHz全流程、增强Flash、有效采样、录制和真实Orbit适配器子进程已通过对应验收，见 [性能与范围](subscription-performance-20261015.md)。内核UDE保持现有版本；统一产品界面、正式签名和已安装VS Code界面另行验证。下文0.1.x结果为独立历史记录。

最新提速实现：2026-10-14，服务 0.1.4、固件 0.1.6。服务按 DAP_Info 协商 64 / 128 / 256 / 512 容量，虚拟 USB / API / SDK 共用该容量；原生 USB 保留 64。已知 DAP 命令按实际结构去除零填充后发送 Wi-Fi，保留真实写入零值和未知命令。39 项主机测试通过；各档实板结果与后续阶段见 [提速计划](performance-plan.md)。RMLINK_USBIP_BATCH_US 为 0..2000 us 的候选聚合开关，默认 0。下面保留服务 0.1.1 驱动修复验收的独立记录。

最终服务 0.1.4 / 512 档的完整 OpenOCD 烧录 / 校验 / 调试、原 Flash 恢复、WinUSB 空读取消、pyOCD、SDK、大包与短包、互斥和 STA 自动重挂载通过；重挂载后再次验证取消及 pyOCD。原始日志在 artifacts/logs/speed-packets-20261014/，数据与限制见 [提速结果](performance-results.md)。

2026-10-14，连接服务原型 0.1.1，配套固件 0.1.5。修补 UDE 已实际加载，虚拟 Bulk 空读取消、pyOCD 0.45.1 读目标 / 暂停 / 继续运行、OpenOCD 经 Wi-Fi 的 STM32 三线烧录 / 调试、真实 API 互斥与 STA 断连重连均通过。当前使用本地测试签名驱动，Secure Boot 关闭、TESTSIGNING 生效、内存完整性开启；正式微软签名与其他软件兼容性待办，暂不作为通用兼容发布版。完整证据见 [取消修复与实机验收](virtual-usb-cancellation.md)。

## 实现路径

标准调试软件 → Windows usbip-win2 UDE 驱动 → 本机 USB/IP 服务（127.0.0.1:3240）→ 统一会话执行器 → Wi-Fi TCP DAP → ESP32-S3 SWD 执行器 → STM32。

Orbit 的后续专用后端通过本机 HTTP API（127.0.0.1:3243）使用同一个会话执行器。每块探针仅建立一个物理 TCP 连接；USB/IP 客户端与 API 客户端通过 DAP_Connect 或有效的 DAP_SWJ_Clock 取得整个目标会话的所有权，允许 Connect 前设置时钟；外来时钟、连接和 Disconnect 被拒绝。控制、watch、timeline、background 请求按优先级调度，已执行的单批命令不会被拆散。断连使原会话失效，写请求不自动重放。

首版基于 usbip-win2 0.9.8.1，并封装其公开 CLI 的 attach / detach；本次实验保留官方 filter / CLI，UDE 使用本地测试签名的取消长度补丁。控制 CLI 不承担 USB 数据转发，数据由内核 WSK socket 经本机 USB/IP 端点传输。RMLink 自有服务实现 USB 描述符、控制请求、Bulk URB、取消、发现、配对与目标会话管理。驱动参考源码保留在忽略的 reference/ 下，本仓库仅管理补丁与构建入口。

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

挂载成功后记录 auto_mount，服务在线时负责发现、重连和再次挂载。目标原会话在断连后失效，客户端必须新建会话；恢复网络不恢复之前的写命令。STA 断连后的自动重挂载已验收；电脑重启、热点恢复与 DHCP 换址后也自动发现并挂载成功，本次设备地址为 192.168.137.13。

## 驱动安装包

固定官方包为 [usbip-win2 0.9.8.1 x64](https://github.com/vadimgrn/usbip-win2/releases/tag/v.0.9.8.1)，本地文件在 artifacts/tools/usbip-win2。安装包 SHA256：`38CAD6D4432B52D5BB9409D9AD03B72FDFFC4ADA4CD3A48FBECA1A2752A8518A`。当前 Authenticode 状态 Valid，安装包发布者 Cloudyne Systems (Scheibling Consulting AB)；安装包签名与驱动目录签名是两项独立检查。

```powershell
# 默认只核验、展示，不安装。
.\scripts\Install-VirtualUsbDriver.ps1
# 经用户确认后才执行系统安装，会弹出 Windows UAC。
.\scripts\Install-VirtualUsbDriver.ps1 -Install
```

[上游 README](https://github.com/vadimgrn/usbip-win2#installation) 明确说明安装会重启所有 USB 3.0 Hub，可能短暂影响当前 USB 设备。因此官方系统安装作为单独确认步骤。脚本不请求自动重启电脑。官方签名包不需要关闭 Secure Boot；本地修补测试驱动另按用户已授权的 [测试模式路径](virtual-usb-cancellation.md) 操作。

引用源码版本记录在 reference-versions.json；实际发行包独立固定为 0.9.8.1。上游公告该版本存在覆盖序列号的已知问题；本轮未使用覆盖参数，WinUSB 实际读到 `RM50787D15A924`，OpenOCD 用该序列号选择虚拟探针。usbip CLI 的 port 输出仍显示空 serial，不能与 USB 描述符序列号混淆。usbip-win2 使用 BSD-2-Clause，构建目录保留 LICENSE；打包同时保留 PyInstaller 的许可证及 bootloader exception。

官方包的两个 CAT 具有 Valid 的 Microsoft Windows Hardware Compatibility Publisher 签名。2026-10-13 的完整验收使用固件 0.1.4 / 服务 0.1.0 / 官方驱动；2026-10-14 14:15 的重启未消除虚拟 pyOCD 空读问题。随后用户开启测试模式，16:35 实际加载修补 UDE：oem123.inf、版本 15.9.4.119、Running，SYS SHA256 `E44D55A36843154D22D06E6CE1271EA74676CB3E233FBFA724CC61C3DF717D31`，嵌入签名 Valid，签名者 `CN=RMLink Local Driver Test`；官方 filter / CLI 保留。install.json 的 restartRequired=false；17:01 的 current-state.json 再次核对加载状态，完整运行结果见 runtime-acceptance.json。回退脚本和官方包备份已准备，本轮未实际运行回退。

## 本地 API 与 Orbit 接入

Node SDK 在 `host/sdk/rmlink-client.mjs`。`init()` 读取本机 token；`open(device_id)` 新建租约、取得 packetSize 并每 5 秒续期；`exchange(packets, priority)` 每批 1–8 个、每个最多协商容量的 CMSIS-DAP 命令，最高 512 字节；`abort()` 单独发送无响应的 TransferAbort；`close()` 释放目标。租约 30 秒过期。失联通过 onLost 通知调用方，并要求重新取得会话。HTTP 请求上限包含 8 个完整 512 字节包的 hex 编码及 JSON 开销，服务仍逐包检查容量。

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

- 主机测试：19 项通过，涵盖所有权、连接前时钟配置、优先级、Abort、关闭时取消待执行请求、描述符、DNS 边界、USB/IP 控制 / Bulk / unlink、Windows 普通 URB 的 -1 标记 / 512 字节 IN 与 API 认证。
- 实板数据通路：自动发现 `50787d15a924`；API 与独立 USB/IP 协议客户端均经 Wi-Fi 读到 STM32 CPUID `0x411fc231`；双向互斥通过；连续 200 个响应核对通过；结束时服务目标 owner 为空。证据 artifacts/logs/verify-connector-device.json。
- 固件 0.1.5：修正连接前时钟的会话入口，已构建、刷写；直连 USB 的 pyOCD 暂停、读 PC、继续运行通过。USB / TCP 的连接前互斥与关闭释放，以及真实 API / 虚拟 USB 的双向互斥通过。两条物理路径完整回读主 Flash 均匹配原备份。
- Windows 虚拟 USB：标准 GUID 可被 WinUSB 打开，bcdDevice=0x0100 与直连设备的 0x0002 区分。修补 UDE 的 50 / 100 / 500 ms 各两次原生空读全部返回 FALSE / error121 / 零字节 / 缓冲未变，三次取消后正常 DAP_Info(FF) 查询通过；直连 USB 同类对照及断连重挂载后的复测也通过。范围限定实测 Bulk 空读取消，未独立验收 Interrupt / chained MDL。
- OpenOCD：本次固件 0.1.5 / 服务 0.1.1 / 修补 UDE 经 usb_bulk 选择虚拟序列号，64 KiB 烧录 / 校验 / 软件复位 12845.70 ms、无 Error 行；独立完整回读 8120.89 ms；暂停 / 继续、寄存器、3072 字节 RAM、FPB / 软件断点、单步、DWT、FAULT 恢复、SWD_Sequence、异步 Abort 与 1 秒执行期限通过。原 Flash 恢复耗时 12288.24 ms，完整 SHA256 对比通过，选项字节未变、核心继续运行。证据 artifacts/logs/test-driver-20261014/accept-virtual-usb.json。此前官方驱动的 11324.08 / 8627.22 ms 数据保留为固件 0.1.4 / 服务 0.1.0 历史记录。
- 实际 WinUSB / API：新组合的两个方向占用互斥通过。通过 USB 厂商命令让单块 RMLink STA 断开，PC 热点保持开启；旧 API 会话 2370.14 ms 报告失联，6528.11 ms 自动恢复连接 / 虚拟挂载，旧租约保持失效，新租约与重挂载后的虚拟探针均读到 CPUID。证据 artifacts/logs/test-driver-20261014/connector-reconnect.json；独立复测也通过并另存 *-final.json，耗时不是性能保证。STA 注入断连不代替调试期间电脑热点实际关闭或完全断电测试。
- 最终状态：物理 USB 独立完整回读 64 KiB 与原 Flash 备份一致，板端 owner=0、服务 owner=null、核心继续运行，无线在线且虚拟探针已挂载；证据 artifacts/logs/test-driver-20261014/final-target-state.json。
- Node SDK：本轮再次通过真实服务连接、8 包查询与释放，协议版本查询为 CMSIS-DAP 2.1.2，RMLink 固件版本独立为 0.1.5。实际 Orbit 后端尚未接入。

| 调试软件 / 路径 | 当前结果 |
| --- | --- |
| OpenOCD v0.12.0-esp32-20260831，虚拟 USB Bulk，固件 0.1.5 / 服务 0.1.1 / 修补 UDE | STM32 三线完整验收通过，原 Flash 恢复及独立完整回读一致 |
| pyOCD 0.45.1，直连 USB Bulk，固件 0.1.5 | 读 CPUID、暂停、读 PC、继续运行通过；本轮未用 pyOCD 烧录 |
| pyOCD 0.45.1，虚拟 USB Bulk，修补 UDE | 空读启动、读 CPUID、暂停、读 PC、继续运行及重挂载后复测通过；本轮未用 pyOCD 烧录 |
| Keil / IAR 等，Bulk 或 HID | 尚未测试；HID 尚未实现 |

官方驱动的历史 WPP 跟踪证实 UDE 与上层过滤驱动均完成取消，但 WinUSB 空读仍报告成功 / 非零长度；发行源码的取消路径保留原始请求长度。zero-copy 与 low-latency 模式也未让 pyOCD 启动通过。实际加载清零长度补丁后，本机原生 Bulk 空读与 pyOCD 启动的前后对照通过，确认修复了这些已测路径；不能扩大为所有 URB 取消类型均已验证。状态、来源、哈希、原始失败与新运行证据见 [专门记录](virtual-usb-cancellation.md)；当前仍为本地 TESTSIGNING 验证，面向用户分发需要正式微软签名。

服务目前只代理 Wi-Fi TCP。直连 USB 仍由标准调试软件直接使用，自动 USB / Wi-Fi 选路尚未实现。`owner` 是服务客户端所有权，板端外部 USB 占用仍由固件拒绝 Connect；服务状态没有实时展示外部 USB owner。虚拟导入连接保持期间无法识别每个 Windows 调试软件的句柄退出，正常退出应发送 DAP_Disconnect；遗留虚拟占用可用 unmount / mount 清理。

以上为0.1.x限制。0.2.2增加原生WinUSB服务承载及显式transport选择，同一设备只有一个选定Broker，有线使用64字节与USB_PULL，无线使用512字节与TCP数据流；API与虚拟USB均复用该承载。自动无缝切换尚未实现，活动owner／租约期间不能改变传输；具体调用和实测范围见api-v1.md及subscriptions-implementation.md。

当前只提供 CMSIS-DAP v2 Bulk 虚拟接口；HID、JTAG、SWO 等尚未支持，不能宣称所有 CMSIS-DAP 软件都兼容。物理 NRST 缺线的限制仍存在。
