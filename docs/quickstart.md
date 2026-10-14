# 构建、连接与接线

更新：2026-10-13。用户已接 STM32F103C8T8，已烧入 target profile、启用 GPIO4 / 5 / 6；热点参数已保存。USB / TCP 目标只读访问通过，目标暂停、复位、RAM 写入及 Flash 烧录尚待验证。

## 两个 USB 口

CH343 口用于下载和日志，本机 COM13。直通 USB&OTG 口用于原生 RMLink USB Bulk，目前 Windows 已自动绑定 WINUSB。不要以端口左右位置推断电气连接，应以板上标注 / 本机枚举为准。

芯片实测：ESP32-S3 rev0.2、40 MHz 晶振、16 MB Flash、8 MB 内置 PSRAM。当前使用 16 MB Flash 配置、3 MB factory app 分区，PSRAM 尚未启用。

## 本机构建环境

- ESP-IDF：D:\ESP32\ESP32idf\Espressif\frameworks\esp-idf-v5.1.2。
- IDF Python：D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe。
- SDK 提交：482a8fb2d78e3b58eb21b26da8a5bedf90623213；本机原有修改使其报告 v5.1.2-dirty，未由 RMLink 修改 SDK。
- 固件基座提交见 source-audit.md；组件版本及哈希保留于 firmware/dependencies.lock。

脚本默认适配上述本机路径。换机器时通过 Invoke-Idf.ps1 的 IdfPath / ToolsPath 参数或调整包装脚本配置路径；需要相同工具链及组件锁定版本。已有 sdkconfig 会优先于 defaults，改变编译 profile 应使用独立构建目录，不能仅编辑 defaults 后假定已有配置改变。本次记录表示可重跑的构建环境，未进行跨机器或字节一致性验收。

PowerShell：

```powershell
& 'D:\STM32\RMLink\scripts\Build-Firmware.ps1'
& 'D:\STM32\RMLink\scripts\Flash-Firmware.ps1' -Port COM13
& 'D:\STM32\RMLink\scripts\Test-Core.ps1'
```

默认传输配置与 sdkconfig 输出到 artifacts/build/firmware。-TargetProfile 另外编译 artifacts/build/firmware-target，并允许目标 GPIO；接线确认后已烧入该 profile。脚本记录最后成功刷写的 profile 与哈希至 artifacts/hardware/rmlink-last-flash.json。

当前接线确认后使用：

```powershell
& 'D:\STM32\RMLink\scripts\Build-Firmware.ps1' -TargetProfile
& 'D:\STM32\RMLink\scripts\Flash-Firmware.ps1' -Port COM13 -TargetProfile -TargetWiringConfirmed
```

以后运行不带 -TargetProfile 的刷写入口会切回目标 GPIO 关闭版本；profile 选择需与验证目的一致。

## 配网与设备检查

电脑热点选择 2.4 GHz。自动读取当前已开启的 Windows 热点参数并通过 USB 保存到设备：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'D:\STM32\RMLink\scripts\Configure-WindowsHotspot.ps1'
```

该脚本需要 Windows PowerShell 5.1 的 WinRT 接口，pwsh 不用于这一入口。只读取当前热点，不修改 Windows 热点设置；密码经子进程 stdin 传递，不出现在命令参数、聊天或日志中。

设备查询工具：

```powershell
$rmlinkPython = 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe'
$env:PYTHONPYCACHEPREFIX = 'D:\STM32\RMLink\artifacts\cache\python'
& $rmlinkPython 'D:\STM32\RMLink\scripts\rmlink_tool.py' verify
& $rmlinkPython 'D:\STM32\RMLink\scripts\rmlink_tool.py' network
& $rmlinkPython 'D:\STM32\RMLink\scripts\rmlink_tool.py' verify --host rmlink-15a924.local
```

也可运行 rmlink_tool.py provision 手动本地输入账号密码。IP 由热点 DHCP 分配，重启后的本轮地址为 192.168.137.243，不能当作永久固定值。TCP 默认端口 3241；现有调试软件是否支持这种 TCP DAP，需要逐软件验证。Windows USB/IP 服务和自动挂载已实现，使用入口与限制见 [Windows 连接组件](windows-connector.md)；Orbit 专用后端仍待接入。

test_device_transport.py 需要 USB 和 Wi-Fi 均在线，会主动三次断开 STA，检查 USB 持续查询、运行时间和新 TCP 连接；仅供传输验收，不进行 STM32 操作。运行时应停止其他占用该设备的工具。

Test-WirelessStartup.ps1 用于检查无原生 USB 枚举的启动：先拔掉直通 OTG 线、保留 CH343，脚本通过 CH343 RTS 复位三次、捕获日志，确认无 mount，并使用已保存热点参数进行 TCP 查询。2026-10-13 已通过，证据在 artifacts/logs/verify-wireless-startup.json 和 boot-wireless-1/2/3.log。该入口不会刷写或重新配网；复位测试不等同于完全断电上电。

## 当前 STM32 接线

用户本轮将目标标为 STM32F103C8T6（早期报 C8T8），USB / TCP 已通过三线 SWD 烧录 / 调试；寄存器不能区分完整料号后缀。供电电压未通过仪表量测；保持此前的 3.3 V 逻辑、自供电方案。

| RMLink 开发板 | STM32 目标 |
| --- | --- |
| GPIO4 | SWDIO |
| GPIO5 | SWCLK |
| GPIO6 | NRST，开漏控制，当前缺线 / 未验收 |
| GND | GND，共地 |

首轮使用 3.3 V 逻辑目标，由目标板自身供电；暂不从 RMLink 给目标供电。GPIO19 / 20 为原生 USB，N16R8 模组的 Flash / PSRAM 引脚及板载 UART、RGB 等占用需保留。不要将开发板的 5 V 接到 STM32 SWD 引脚。当前方案没有专用 VTref 检测或电平转换，不能直接据此接受其他电压目标。

已从 100 kHz 设置开始，完成芯片身份、内存读取、USB / TCP 会话互斥 / 切换及主机请求排队对照。100 kHz、500 kHz、1 MHz、2 MHz 是 DAP 请求的 SWD 时钟，尚未用示波器或逻辑分析仪量测实际 SWCLK。0.1.3 已执行软件复位、暂停 / 继续、寄存器 / RAM、断点 / 单步及 64 KiB 烧录 / 校验，详见 [本轮验收](stm32-acceptance.md)。

只读目标检查：

```powershell
& $rmlinkPython 'D:\STM32\RMLink\scripts\rmlink_swd.py' --clock 100000
& $rmlinkPython 'D:\STM32\RMLink\scripts\rmlink_swd.py' --host rmlink-15a924.local --clock 100000
```

该入口会配置 CoreSight DP / AP 寄存器，并读芯片信息和 Flash；不写目标 RAM / Flash。test_target_ownership.py 验证会话排他及 STA 断连时 USB 目标读取；benchmark_swd_reads.py / benchmark_swd_pipeline.py 对照同一段 Flash 的顺序请求和最多 8 包连续提交。后者是主机标准 DAP 排队，板端周期采样尚未实现。

## 本地原固件备份

artifacts/hardware/esp32s3-before-rmlink.bin 是刷入 RMLink 前的完整 16 MB Flash 备份，仅本地保留。备份 SHA256 为 9C7CB67284D4384C5C708A10A5C971D4E49C253D22399052B74F80B209AFEF54。固件可能包含原设备配置，勿作为公开发布内容。

artifacts/hardware/stm32f103c8-before-rmlink.bin 为本次从 STM32 读取的 64 KiB 主 Flash 备份，保存原程序；验收后已重新烧录恢复，并由 USB / TCP 完整回读核对。它不包含 RAM、选项字节或系统 ROM；选项数据仅作读取前后比较，未写入。原程序备份仅在本地保留，不放入文档分享副本。
