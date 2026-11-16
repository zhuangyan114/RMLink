# RMLink

基于 ESP32-S3 的 USB / Wi-Fi CMSIS-DAP 调试器，配合 Windows 控制台和后台连接服务使用。除了标准有线、无线（OpenOCD TCP）烧录和调试，搭配 [Orbit](https://github.com/zhuangyan114/Orbit) 还能直接在探针端完成高频变量采样（Watch / Timeline / RTT）和整包缓存烧录。

## 功能特点

- **有线与无线双模**：原生 USB 口走标准 CMSIS-DAP 与独立 Bulk 连续采样；无线连入电脑 2.4 GHz 热点后，可通过 TCP 进行高速调试和采样。
- **免额外内核驱动**：有线直接使用 Windows 自带 WinUSB，无线走 TCP 端口，不再依赖虚拟 USB 内核驱动，也不需要改动系统安全启动或测试模式。
- **板端订阅与缓存烧录**：支持板端定时读取变量和指针链，以及先整包上传到 ESP32 再由探针自主擦写校验的快速烧录。
- **支持芯片**：内置算法包覆盖 `STM32F103C8`、`STM32F407VE`、`STM32F407IG`、`STM32H723VG`（其他标准调试可通过 OpenOCD TCP 使用）。
- **OTA 在线升级**：支持从 GitHub Release 或本地 `.bin` 文件直接通过 USB / Wi-Fi 升级探针固件，带双分区和启动失败回滚保护。

## 安装与使用（Windows）

### 1. 安装客户端

到 [GitHub Releases](https://github.com/zhuangyan114/RMLink/releases/latest) 下载最新的 Windows 安装包（如 `RMLink-0.3.4-windows-x64-setup-unsigned.exe`），双击安装即可。

- 默认安装到当前用户的 `%USERPROFILE%\.rmlink\app`，不需要管理员权限，也不需要预先安装 Python 或 ESP-IDF。
- 安装包目前未做商业代码签名（文件名带 `unsigned`），下载或首次打开时 Windows SmartScreen 可能会弹窗提示，确认放行即可。

### 2. 硬件接线

以 **RMLink V1** 硬件为例，板载 10 Pin 调试接口定义如下：

| P1 针脚 | 信号 | ESP32-S3 GPIO | 说明 |
| :---: | --- | :---: | --- |
| 1 | GND | GND | 务必与目标板共地 |
| 2 | SWCLK / TCK | GPIO18 | 串联 33 Ω |
| 3 | 3.3 V | 3V3 | 电源脚 |
| 4 | SWDIO / TMS | GPIO17 | 串联 33 Ω |
| 5 | TDO / SWO | GPIO7 | 串联 0 Ω |
| 6 | TDI | GPIO8 | 串联 33 Ω |
| 7 | UART RX | GPIO5 | 串联 0 Ω |
| 8 | NRST | GPIO6 | 串联 100 Ω（未接时用软件复位） |
| 9 | UART TX | GPIO4 | 串联 33 Ω |
| 10 | USB VBUS | 电源网络 | 5 V 输入网络 |

> [!CAUTION]
> 目标 SWD 为 **3.3 V** 电平，切勿将 5 V 接到 SWDIO / SWCLK / NRST 引脚。

板上其他接口：
- **原生 USB 口**（GPIO19 / GPIO20）：日常有线调试、初次 USB 配网与高速 USB 采集请插这个口。
- **串口下载口**（CH343）：用于查看底层串口日志或救砖烧录 bootloader。
- **按键与状态灯**：WS2812 状态灯接 GPIO1，用户按键接 GPIO2（未连上时按住 2 秒进入模式切换，短按在 Wi-Fi 与 USB 间切换）。
- *注：早期 ESP32-S3 普通开发板测试配置使用 GPIO4（SWDIO）、GPIO5（SWCLK）、GPIO6（NRST）。*

### 3. 配对与 Wi-Fi 配网

安装完成后，从开始菜单打开 **RMLink**，或者在终端直接输入：

```powershell
rmlink
```

控制台支持键盘方向键、Enter 以及鼠标点击/滚轮操作。首次使用建议先插上原生 USB 线：

1. 在控制台进入 **设备管理**，选中扫描到的设备完成配对；也可以设为默认设备或起个别名。
2. 在电脑设置中开启 **2.4 GHz 移动热点**，进入控制台 **Wi-Fi 配置**，输入热点名称和密码下发给 RMLink（如果热点已开且只想从命令行配网，可直接运行 `rmlink wifi configure --ssid "你的热点名称"`）。
3. 配网成功后拔掉 USB 线，RMLink 上电会自动连入热点并出现在设备列表里。

### 4. 开始调试

#### 搭配 Orbit 使用

在项目的 `launch.json` 中把 `probe` 设为 `"rmlink"` 即可接入增强后端（含板端缓存烧录和 Watch / Timeline / RTT 订阅）：

```json
{
  "name": "Orbit RMLink",
  "type": "orbit",
  "request": "launch",
  "probe": "rmlink",
  "device": "STM32H723VG",
  "interface": "SWD",
  "speedKHz": 4000,
  "program": "${workspaceFolder}/build/Debug/app.elf",
  "flashBeforeDebug": true,
  "runToEntryPoint": "main"
}
```

- 只有一台在线且已配对的 RMLink 时可以省略 `rmlinkDeviceId`；多台设备共存时建议填上设备 MAC（可在 `rmlink devices` 里查看）。
- 详细配置与常见问题见 [Orbit 专用连接说明](docs/orbit-rmlink-connection.md)。

#### 搭配标准 OpenOCD（无线 TCP）使用

RMLink 同一时间只允许一个工具控制目标 MCU。如果要改用标准 OpenOCD 通过 Wi-Fi 调试，先释放连接服务的占用：

```powershell
rmlink disconnect
```

在 OpenOCD 配置中指定 TCP 后端和板端默认端口 **3241**（注意需使用包含 `cmsis-dap backend tcp` 的较新 OpenOCD 构建）：

```tcl
adapter driver cmsis-dap
cmsis-dap backend tcp
cmsis-dap tcp host rmlink-xxxxxx.local
cmsis-dap tcp port 3241
```

调试结束后，执行 `rmlink connect` 即可把设备交回后台服务继续自动重连。

## 常用 CLI 命令

除了交互式界面，`rmlink` 也提供纯命令行子命令（均支持 `--json`）：

```powershell
rmlink devices                          # 查看已发现与已配对设备
rmlink pair <设备ID>                    # 配对设备
rmlink wifi configure --ssid "热点名"   # 通过 USB 下发 Wi-Fi 账号密码
rmlink connect --transport wifi         # 恢复连接并切到 Wi-Fi 优先
rmlink disconnect                       # 断开服务占用（供 OpenOCD TCP 直接接入）
rmlink firmware status                  # 查看当前固件版本与 OTA 状态
rmlink firmware update --latest --yes   # 从 GitHub 拉取并升级最新正式固件
rmlink doctor                           # 诊断连接服务、网络与设备状态
```

> 0.3.0 之前的老固件还没有 OTA 分区表，首次升级需要按 [OTA 升级说明](docs/ota-upgrade.md) 通过串口迁移一次引导程序和分区表；0.3.0 及以上版本可直接在终端内在线升级。

---

## 仓库结构与源码构建

如果你需要修改固件或自行打包安装器，下面是仓库的主要目录：

| 目录 | 说明 |
| --- | --- |
| `firmware/` | 基于 ESP-IDF（`esp-usb-bridge`）的 ESP32-S3 固件源码 |
| `host/` | Windows 连接服务、`rmlink` 控制台、目标 Flash 算法包与公开 SDK |
| `scripts/` | 固件编译、串口迁移、Windows 安装器打包与测试验证脚本 |
| `tests/` | 主机单元测试、C 原生状态机模型与 STM32 测试程序 |
| `docs/` | 协议规范、API 文档、硬件记录与修复日志 |
| `reference/` | 上游参考仓库（本地对照用，不随代码提交） |

### 自行构建

构建固件需要本机 ESP-IDF v5.1.2 环境；构建 Windows 安装器需要 Python 构建环境、Arm GNU 工具链及 Inno Setup 6.7.3+。

```powershell
# 编译 RMLink V1 固件
.\scripts\Build-Firmware.ps1 -TargetProfile -BoardProfile rmlink-v1 -TcpPacketSize 512 -ImageCachePsram -StreamIpTos 160

# 打包 Windows 安装器（输出到 artifacts/packages/）
.\scripts\Build-Installer.ps1
```

### 更多开发文档

- **协议与接口**：[API v1 与公开 SDK](docs/api-v1.md) · [开发协议 v1](docs/protocol-v1.md) · [统一目标算法包](docs/shared-target-package.md) · [订阅采集设计](docs/subscription-design.md)
- **运维与打包**：[终端客户端详述](docs/terminal-client.md) · [Windows 安装器构建与签名](docs/windows-installer.md) · [OTA 升级机制](docs/ota-upgrade.md)
- **工程与记录**：[当前项目方向](docs/current-direction.md) · [RMLink V1 接线与验证](docs/rmlink-v1-bringup.md) · [修复与功能记录](docs/fix-log/README.md) · [仓库协作与参考说明](docs/git-management.md)
