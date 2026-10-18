# RMLink 终端客户端

客户端 0.3.1 完善 OTA 确认和进度提示，与本机现有连接服务 0.3.0 兼容。固件从 0.3.0 开始支持 OTA；SDK 使用各自版本，目标程序和 Orbit 工作树保留。

0.3.1 交互修复：导入后保留固件信息停顿页，展示当前版本到所选版本的变化，底部以蓝色明确显示「Enter 确认安装并重启 RMLink」，`q / Esc` 取消并返回固件菜单；不再把回车标成返回，也不再追加第二个安装菜单。确认后立即显示 0% 进度，持续显示上传百分比、校验、等待重连秒数和最终成功提示。181 项主机测试通过，冻结 EXE 在 Windows PowerShell 伪终端配合隔离模拟服务完成取消不上传、完整上传、VALID 等待与各阶段提示验收；记录在本机 `artifacts/tests/ota-ui-031-conpty/acceptance.json`。已安装客户端还在真实设备上完成信息页／版本对比／q 取消检查，设备分区和镜像摘要保持一致，记录为 `artifacts/tests/ota-ui-031-installed.json`。客户端文件与构建哈希一致，保留原服务及真实设备 0.3.0，留给用户体验真实升级。

0.3.0 OTA 软件验收：174 项全量主机测试通过；随后加入 TCP512 覆盖后，13 项 OTA 专项测试通过。生产 C 状态机通过 native 故障注入，包含 USB64 分块和 TCP512 跨描述头写入。safe 和当前目标板 TCP512／PSRAM／streamdiag／video 配置均构建成功；冻结 EXE 通过 10 步隔离本机 API 验收。已安装客户端和连接服务 0.3.0，并在 PowerShell 5.1 检查固件子菜单、本地导入／GitHub 入口和旧固件迁移提示。最终应用资产位于本机 `artifacts/packages/ota-0.3.0-ready/`；详细范围见 [OTA 升级](ota-upgrade.md)。

随后经用户授权完成原 0.2.40 备份、首次 USB 分区迁移及 Wi-Fi／USB 两次真实 OTA。安装后的 `rmlink.exe` 上传、重启及镜像摘要／VALID 确认均通过，运行分区依次为 ota_0、ota_1，最终固件 0.3.0、Wi-Fi 偏好恢复。原 Wi-Fi 配置保留；STM32 核心运行，USB 和 Wi-Fi 各完成 100 Hz／100 帧只读采样。报告为本机 `artifacts/tests/ota-hardware-acceptance-20261017.json`，备份位于 `artifacts/backups/ota-bootstrap-20261017-180722/`。两份验收镜像均为 0.3.0，以不同 ELF 摘要区分；不将同版本号视为没有实际写入。GitHub 实际查询明确返回尚无正式 Release，已发布资产的完整下载升级、断电及首次启动异常回滚尚未实测。完整证据见[实板 OTA 记录](ota-acceptance-20261017.md)。

## 安装与启动

开发环境构建及安装：

```powershell
.\scripts\Build-Connector.ps1
.\scripts\Install-Connector.ps1 -Install
rmlink
```

安装到当前用户的 `%USERPROFILE%\.rmlink\app\bin`，将该目录加入用户 PATH。程序、状态和日志均使用普通终端与打包应用可共享的用户目录，避开 AppData 的包级文件虚拟化。若原有 PATH 已配置用户 `bin` 或 `.local\bin`，安装器同时在该目录放置 `rmlink.cmd` 启动入口，让尚未刷新 PATH 的终端立即使用。否则需完整退出 Windows Terminal 再重新打开；仅创建新标签页可能继续继承旧环境。运行安装脚本的 PowerShell 会话立即更新 PATH。

安装器通过打开 EXE 的文件句柄查询真实路径；如果文件实际落在 `Packages/.../LocalCache` 私有缓存，安装会明确失败。旧 `%LOCALAPPDATA%\RMLink\bin` 的 PATH 注册由新安装器迁移，原有配对仍保存在 `%USERPROFILE%\.rmlink\connector.json`。可直接使用完整路径启动：

```powershell
& "$env:USERPROFILE\.rmlink\app\bin\rmlink.exe"
```

不带参数进入交互控制台：RMLINK 字标采用参考图中的规整像素块风格，保持七行高度，宽度为 66 列；`RM` 深蓝、`LINK` 浅蓝，搭配设备概览、实时速率及八个操作入口。主菜单和操作子菜单使用同一字标；窗口空间不足时自动使用紧凑字标，优先显示菜单与操作提示。仅当前选中的菜单项变蓝，上下键选择，左右键切换两列同一行，Enter 打开，也支持数字快捷键；`q`、Ctrl+C 退出界面，后台连接服务继续运行。窄终端使用单列菜单，左右键依次选择；`NO_COLOR` 或 `--no-color` 关闭颜色，同时保留选中标记。重定向输出时不启动交互循环。

Wi-Fi、设备管理、连接与挂载、驱动管理、诊断与日志、设置均使用上下键选择、Enter 进入的列表。详情页返回后保留子菜单焦点；选择「返回上一级」或按 `q`／Esc 返回。只有网络名称、密码、路径、别名等文字表单需要输入文本，密码隐藏输入。电脑热点状态可在未发现设备时查看。

2026-10-17 最新界面调整通过 18 项终端测试和全部 162 项主机测试，覆盖两种菜单布局的单项蓝色、左右切换后 Enter 打开正确入口、Windows 原生／VT 方向键、Wi-Fi 状态／配网表单／无设备热点查询、取消菜单不执行操作、长列表滚动及小终端布局。重建并更新本机 `rmlink.exe`，在 Windows PowerShell 5.1 的 ConPTY 中确认 Wi-Fi 子菜单上下选择、Enter 打开电脑热点状态、`q` 返回后保留焦点、Esc 返回主菜单及 `q` 恢复原终端；本轮未写入 Wi-Fi 凭据或维护驱动。

客户端在需要时启动隐藏的每用户后台进程，沿用 `.rmlink/connector.json` 中的配对和本机凭据；不注册 Windows SCM 系统服务。可选登录自启：

```powershell
rmlink service autostart on
rmlink service autostart off
```

## 常用命令

```powershell
rmlink devices
rmlink pair 50787d15a924
rmlink config default 50787d15a924
rmlink config alias "工作台"
rmlink wifi status
rmlink wifi hotspot
rmlink wifi configure --ssid "电脑热点"
rmlink connect --transport wifi
rmlink mount
rmlink monitor
rmlink unmount
rmlink disconnect
rmlink unpair
rmlink info
rmlink driver status
rmlink doctor --output .\rmlink-diagnostics.json
rmlink logs --lines 80
rmlink service status
rmlink service restart
rmlink firmware status
rmlink firmware inspect .\rmlink.bin
```

所有命令支持 `--json`、`--device <完整 MAC 编号>` 和 `--no-color`。多设备且未选择默认设备时要求明确选择，不自动选择第一块设备。USB 发现只查询真实 OTG 设备，忽略虚拟 USB；USB 配对默认使用有线承载，已有传输偏好保留。

密码使用隐藏输入；脚本可使用 `--password-stdin` 通过 UTF-8 stdin 传入，开放网络需显式 `--open-network`。不提供密码命令行参数，不回读或显示已保存密码，不将密码写入主机设置或诊断文件。SSID 和密码按 UTF-8 字节校验，USB 侧重发完整字段；提交未确认时不自动重试。

`disconnect` 保留配对并暂停自动重连，`connect` 恢复自动连接。切换传输、取消配对、断开及配网在存在目标 owner 或活动 API 租约时拒绝执行。首次配网无需先配对，通过 `devices` 或 Wi-Fi 菜单发现 USB 设备即可。

## 监控口径

速率以连接服务实际收发的探针应用层字节计数求差：包含 TCP DAP 帧头、压缩后的采样流和 USB OUT 填充，不包含 TCP/IP、无线及 USB 总线开销。原生 USB 计数包含成功完成的批次；失败批次的部分完成字节不估算。连接重建会更换计数代次，第一帧显示 `--`，不会因计数清零显示尖峰。

订阅信息来自现有 owner 的缓存，仅显示已存在任务，不创建订阅、不连接目标、不读写目标内存。实际均值以收到样本的板端时间戳计算，与请求采样率分开；没有样本、没有任务或旧服务未提供统计时显示不可用，不伪造频率和丢样指标。首页读取本机缓存，不主动测速。

## 驱动维护与升级边界

客户端管理现有 USBip 驱动的安装状态和服务状态，设备通过原有挂载接口呈现为虚拟 CMSIS-DAP。`driver status` 同时核对 USBip CLI、内核服务状态及文件签名；签名有效不等价于正式微软发布认证。

安装／修复入口接受经审核的本地 `driver-package.json`，校验所有列明文件 SHA256 和 CAT 信任状态后通过 Windows UAC 调用 pnputil。不修改 Secure Boot、TESTSIGNING 或内存完整性，不自动重启。当前不自动下载或替换本机已验证的修补驱动；普通上游包不能替代本机修补驱动的验收结论。

```powershell
rmlink driver install --package .\drivers\driver-package.json
rmlink driver repair --package .\drivers\driver-package.json
rmlink driver uninstall --published-name oem123.inf
```

清单格式如下，文件需来自同一经审核的签名驱动包：

```json
{
  "format": "rmlink-driver-v1",
  "infs": ["usbip2_ude.inf"],
  "files": [
    {"path": "usbip2_ude.inf", "sha256": "完整 SHA256"},
    {"path": "usbip2_ude.sys", "sha256": "完整 SHA256"},
    {"path": "usbip2_ude.cat", "sha256": "完整 SHA256"}
  ]
}
```

卸载只接受明确的 Windows `oem数字.inf` 发布名并核对 USBip 内容，不使用强制删除。当前驱动安装／修复／卸载入口需要独立系统验收，开发客户端验证不等价于驱动发布或替换验收。

`firmware` 专指 RMLink 自身。0.3.0 支持本地 `.bin` 导入和 GitHub 最新正式固件获取，使用官方双升级分区和启动确认／失败回滚。`inspect` 离线核对芯片、应用结构、镜像完整性、版本及硬件配置；`update 文件 --yes` 或 `update --latest --yes` 在空闲设备上写入备用分区并等待重启确认。旧固件明确返回首次 USB 迁移要求，不发送 STM32 Flash 命令。完整协议、发布清单、迁移和验证边界见 [OTA 升级](ota-upgrade.md)。

0.3.2 增加独立的「从 GitHub 获取测试固件」菜单；脚本使用 `firmware latest --prerelease` 或 `firmware update --latest --prerelease --yes`。测试版在确认页显式标注，默认正式通道不选择 Pre-release；公共 API 限流时使用 GitHub 官方发布订阅／重定向，无需写入 GitHub 凭据。本轮完整检查见 [0.3.2 测试版验收](test-release-0.3.2.md)。

## 本机 API 扩展

本轮沿用认证和唯一 Broker，API 1.4 新增：

| 接口 | 用途 |
| --- | --- |
| GET /v1/usb-devices | 查询真实 USB 探针身份并加入发现列表 |
| GET / POST /v1/preferences | 读取／保存默认设备 |
| POST /v1/alias | 保存已配对设备别名 |
| POST /v1/connect、/disconnect、/unpair | 管理连接与配对 |
| GET /v1/devices/{id}/metrics | 被动收发计数、现有订阅缓存 |
| GET / POST /v1/devices/{id}/wifi | 读取网络状态／通过 USB 配网 |
| GET /v1/devices/{id}/firmware | 固件版本与自身升级支持状态 |
| POST /v1/devices/{id}/firmware | 固件上传 begin／write／finish／abort，维护锁和严格偏移保护 |

既有 RMLinkConnector 开发命令和 Python／Node／C SDK 保留。原服务正在运行时客户端复用它；新增管理接口需连接服务 0.2.15，旧服务应在结束调试后重启升级。

## 验证

主机测试覆盖管理操作占用保护、配对配置保留、配网 UTF-8 边界／开放网络／拒绝后不提交、被动指标不访问目标、重连速率代次、离线检查及 CLI JSON／窄终端输出。实际构建、安装、终端交互及本机 API 验收结果追加在此处；Wi-Fi 凭据写入、驱动替换和目标访问不由主机测试代替。

2026-10-17 本轮结果：

- 全部 152 项主机测试通过；后续锁文件并发启动修复完成独立双进程检查，调整后的 8 项终端测试再次通过。独立 TCP peer 检查包含分片响应及应用层字节计数。
- Windows x64 的 `rmlink.exe` 与 `RMLinkConnector.exe` 构建成功。冻结 EXE 使用带空格的隔离状态目录，通过两个并发启动、重复启动复用同一 PID、设备发现、真实驱动状态／签名查询、电脑热点查询、日志、诊断导出、凭据不出现在公共输出及服务停止共 10 步验收；报告见本机 `artifacts/tests/terminal-client-acceptance-20261017-03/acceptance.json`，`concurrent_start=true`。
- ConPTY 中验证彩色交互、ANSI 方向键切换、数字菜单、Wi-Fi 子菜单及 `q` 退出恢复原屏幕；`TERM=dumb`／`NO_COLOR` 下完成无颜色模式操作。Windows PowerShell 子进程固定系统模块路径及安全模块来源，避免从 PowerShell 7 或冻结 EXE 启动时的类型数据冲突。
- 本机 USBip CLI 已安装，filter 和修补 UDE 为 Running，文件签名检查 Valid。本轮只读查询，未安装、替换或卸载内核驱动，未改变启动安全设置。
- 0.2.15 曾安装到逻辑路径 `C:\Users\22690\AppData\Local\RMLink\bin\rmlink.exe`。同一应用环境内文件哈希和服务检查通过，但用户普通 PowerShell 实际无法访问，后续确认是文件虚拟化，不能将当时验证视为外部终端安装验收通过。
- 首次补充 PATH 启动入口后，同一应用环境内 Windows PowerShell 5.1 检查通过，用户外部终端仍报告找不到路径。该失败保留：补充启动器解决了命令发现，但未解决 EXE 位于包私有缓存的问题。
- 验收时未发现在线探针，未写入 Wi-Fi 凭据、未执行实际带设备采样测速或固件写入。USB 配网、真实流量／采样显示和驱动维护写入仍需对应实机验收；自身固件升级保持明确的预留状态。

0.2.16 安装修复：

- 用 `GetFinalPathNameByHandle` 确认，旧逻辑 AppData 路径实际位于 `D:\WpSystem\...\Packages\OpenAI.Codex_...\LocalCache\Local\RMLink`，是当前应用的私有缓存；用户终端能读取公共 `bin/rmlink.cmd`，但无法通过旧逻辑路径找到 EXE。
- 两种安装器统一改到 `%USERPROFILE%\.rmlink\app`；日志改到 `%USERPROFILE%\.rmlink\logs`，保留原有配对。命令注册迁移旧 PATH 项，启动器使用用户目录，登录自启原已开启时迁移到新位置。
- 安装器新增真实文件路径检查，明确拒绝包私有缓存。新文件的逻辑路径和真实路径均为 `C:\Users\22690\.rmlink\app\bin\rmlink.exe`，安装文件与构建输出 SHA256 一致。
- 153 项主机测试通过，含打包应用与普通终端 AppData 不同时共享日志位置的回归检查；旧私有缓存安装位置拒绝测试通过。Windows PowerShell 5.1 由用户主目录解析公共启动器，0.2.16 版本／状态查询和完整交互界面启动通过。
- 后台服务已从公共新目录启动为0.2.16。安装证据在本机 `artifacts/tests/terminal-client-0216-installation.json`；这些验证不替代用户外部终端的最终确认或设备实机验收。
