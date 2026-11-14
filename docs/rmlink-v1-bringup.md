# RMLink V1 实板接线与调试验收

2026-11-09，通过嘉立创 MCP 读取 RMLink 工程 Board1 的 P1 原理图网表与 PCB1 器件焊盘网络，两者的下列连接一致。U1 为 ESP32-S3-WROOM-1-N16R8。

## 引脚连接

| 信号 | ESP32-S3 GPIO | 串联器件 | P1 针号 |
| --- | --- | --- | --- |
| GND | GND | 直连 | 1 |
| SWCLK / TCK | 18 | R11，33 Ω | 2 |
| 3.3 V | 3V3 | 直连 | 3 |
| SWDIO / TMS | 17 | R10，33 Ω | 4 |
| TDO / SWO | 7 | R8，0 Ω | 5 |
| TDI | 8 | R9，33 Ω | 6 |
| UART RX | 5 | R7，0 Ω | 7 |
| NRST | 6 | R5，100 Ω | 8 |
| UART TX | 4 | R6，33 Ω | 9 |
| USB VBUS | 电源网络 | 直连 | 10 |

原生 USB D− 为 GPIO19，经 R3 22 Ω 接 DN。D+ 为 GPIO20，经 R4 22 Ω 接 DP。
WS2812 数据输入为 GPIO1，经 R13 330 Ω 接 LED2。用户按键为 GPIO2，按下接地。
ESP32 的 GPIO0 经 R19 10 kΩ 上拉，TP1 接 ESP_BOOT，TP2 接 GND。
下载日志接口 X1 的 1、2、3 针分别为 RXD0、TXD0、GND，TXD0 串联 R15 33 Ω。

本次用户已接 STM32H723VGT6 的 3.3 V、GND、SWDIO、SWCLK，并用万用表确认。NRST 外接线尚未提供，本轮使用软件复位。UART、TDI、SWO、LED 和按键的功能验收另行记录。

## 构建与 OTA 迁移

新板最初运行 0.3.3 安全配置，配置指纹为 `016a775b7c25d07b`，GPIO 描述为 4 / 5 / 6，目标 GPIO 和 PSRAM 均关闭。直接上传新板配置会被原有完整元数据匹配检查拒绝。

分两次 OTA。过渡应用保持原安全配置，只额外接受以下完整元数据。目标 GPIO 开启，PSRAM 开启，SWDIO 17、SWCLK 18、NRST 6，UART 和 MSC 关闭，schema 和 layout 均为 1，保留字段全零。其他配置继续拒绝。

```powershell
.\scripts\Build-Firmware.ps1 -BoardMigration -BuildTag v1-20261109
.\scripts\Build-Firmware.ps1 -TargetProfile -BoardProfile rmlink-v1 -TcpPacketSize 512 -ImageCachePsram -StreamDiagnostics -StreamIpTos 160 -BuildTag bringup-20261109
```

过渡应用的 OTA INFO 能力 bit2 表示有指定的迁移配置。只读子命令 `0x83,6` 返回 `83 06 00` 加 8 字节迁移配置指纹，保持 64 字节 DAP 兼容。连接服务与本地固件导入同时核对该指纹，设备在擦写备用分区前仍逐字节核对完整 64 字节元数据。

正常 V1 应用关闭过渡选项，后续升级恢复同配置匹配。每次 OTA 都使用原有备用应用分区、镜像验证、重启和 VALID 启动确认流程。

## 实板记录

设备 ID 为 `cc68c7a5995c`，mDNS 名称为 `rmlink-a5995c.local`。本轮连接为 Wi-Fi，最终协商 DAP 容量为 512 字节。

| 项目 | 实测结果 |
| --- | --- |
| 过渡应用 OTA | Wi-Fi 上传、自动重启、运行 ELF 摘要和 VALID 确认通过 |
| V1 配置迁移 | Wi-Fi OTA 通过，最终指纹为 `7912ec692d3a985d`，PSRAM 已启用 |
| 100 kHz / 1 MHz / 4 MHz SWD | 三档身份检查和 128 字节向量表读取一致，频率为请求值 |
| H723 身份 | DPIDR `0x6ba02477`、CPUID `0x411fc272`、DBGMCU IDCODE `0x10016483`，Flash 容量 1024 KiB |
| RAM / 寄存器 | AXI SRAM 写入回读、R0 写入回读通过，随后恢复测试 RAM 与寄存器 |
| 单步 / 硬件断点 | RAM 指令两次单步分别得到 R0=1、2，继续后在硬件断点停止，R0=3 |
| 板端缓存烧录 | `0x080e0000` 起的完整 128 KiB 扇区，通过板端比较和独立主机回读 SHA256 |
| 主机烧录路径 | 同一扇区恢复原内容，主机 SHA256 验证通过 |
| 软件复位 / 继续 | 复位后暂停、继续运行通过 |
| 板端订阅 | 四个 Flash 字段以 100 Hz 连续采集 200 帧，序号连续，数据全部一致 |
| 标准 OpenOCD TCP | 官方 `0.12.0+dev-g6fbe9e7` Windows 构建通过识别、单步、128 KiB 烧录/校验和原扇区恢复，未启用 quirk，日志无 Error |

原程序已备份为 `artifacts/hardware/rmlink-v1-20261109/h723-original-flash.bin`。整片 1 MiB Flash 最终与备份一致，SHA256 为 `a457d8c6c7d81ec012b4e5cb4ef8f32c7c9dd6a18db73c5cfad25754247f3a9f`。`FLASH_OPTSR_CUR` 和 `FLASH_WPSN_CUR1` 前后相同。

最终 ESP32 应用 ELF SHA256 为 `7ce4b32459d31effff5870f79ed0e17ea90e0fed00f1294287874de1444a054c`，版本字段仍为 0.3.3。电脑端客户端和连接服务已更新为本轮源码构建的 0.3.4，旧 EXE 的副本保存在该实板记录目录的 `client-before/`。

本轮修复了两项实测问题。

- 快照读取原来固定限制为 2 ms，100 kHz 的正常多字传输也会超时。现按请求 SWD 时钟和操作数增加线速预算，实板三档读取复测通过。
- 继续执行后立即命中硬件断点时，服务原来一直等待 Running，最终误报超时。现清除旧 DFSR 原因并识别新的停止事件，源码服务及安装后的 EXE 均通过复测。

主机全量 220 项测试通过，OTA 原 C 状态机普通/迁移两种构建通过，采样原 C 状态机在 64/512 字节配置下通过。未量测外部 SWCLK 波形，原生 USB、外接 NRST、UART、SWO、LED 和按键仍待各自的功能验收。

### 标准 OpenOCD 构建

旧的 `v0.12.0-esp32-20260831` Windows 构建在初始化阶段出现 `WSAGetLastError=10035 / error reading header`。本轮使用官方镜像的 `6fbe9e7` 构建完成默认排队模式验收。构建下载自官方发布页，归档 SHA256 为 `d4ed61f96aec07fcacac532e27e9f356c03ecc24d7a209fe65c893d3cea6b6ec`。上游当前 TCP 实现包含 Windows socket 错误和读取状态处理。来源见 [官方发布页](https://github.com/openocd-org/openocd/releases/tag/latest) 和 [TCP 后端源码](https://github.com/openocd-org/openocd/blob/6fbe9e7/src/jtag/drivers/cmsis_dap_tcp.c)。

已验收的可执行文件为 `artifacts/tools/openocd-upstream-6fbe9e7/bin/openocd.exe`。使用标准 TCP 调试前，先执行 `rmlink disconnect --device cc68c7a5995c` 释放连接服务的物理连接，调试结束后执行 `rmlink connect --device cc68c7a5995c` 恢复。OpenOCD 配置选择 `cmsis-dap backend tcp`、设备地址及端口 `3241`，再加载 `target/stm32h7x.cfg`。

逐项结果、构建身份、原始日志及引脚网络快照保存在 `artifacts/hardware/rmlink-v1-20261109/`。该目录包含设备数据，按仓库规范保留在本机。
