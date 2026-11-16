# RMLink V1 实板接线与调试验收

2026-11-09 依据嘉立创工程 `Board1` 的原理图 `P1` 网表与 `PCB1` 焊盘网络核对完成，主控模组为 `ESP32-S3-WROOM-1-N16R8`。

## 硬件引脚定义

| 信号 | ESP32-S3 GPIO | 串联器件 | P1 针号 |
| --- | --- | --- | :---: |
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

其余板载脚位：
- **原生 USB**：D− 接 GPIO19（经 R3 22 Ω）、D+ 接 GPIO20（经 R4 22 Ω）。
- **状态灯与按键**：WS2812 数据脚接 GPIO1（经 R13 330 Ω 到 LED2）；用户按键接 GPIO2（低电平有效）。
- **下载与日志口**：GPIO0 经 R19 10 kΩ 上拉（TP1 接 ESP_BOOT，TP2 接 GND）；串口口 `X1` 的 1/2/3 针为 RXD0、TXD0（经 R15 33 Ω）、GND。

## 首次跨配置 OTA 迁移（无需按 BOOT）

出厂或初期刷成旧开发板安全配置（profile `016a775b7c25d07b`，GPIO4/5/6、关闭目标脚与 PSRAM）的新板，直接升 V1 固件会被元数据检查拒绝，需分两步走 Wi-Fi OTA：

1. 先编译并刷入**过渡固件**（保持旧安全引脚配置，但打开 `CONFIG_RMLINK_OTA_BOARD_V1_MIGRATION`，只额外放行 V1 完整元数据：`target_pins=1, psram=1, swdio=17, swclk=18, nrst=6, uart=0, msc=0`）：
2. 再编译并刷入**正式 V1 固件**（关掉过渡开关，恢复常规严格配置校验）。

```powershell
# 1. 过渡固件
.\scripts\Build-Firmware.ps1 -BoardMigration -BuildTag v1-20261109
# 2. 正式 V1 固件
.\scripts\Build-Firmware.ps1 -TargetProfile -BoardProfile rmlink-v1 -TcpPacketSize 512 -ImageCachePsram -StreamDiagnostics -StreamIpTos 160 -BuildTag bringup-20261109
```

> 协议细节：过渡固件在 OTA INFO 能力 bit2 声明迁移能力，子命令 `0x83,6` 返回 `83 06 00` + 8 字节迁移指纹；擦写前板端仍逐字节比对前 64 字节完整元数据。

## 2026-11-09 实板调试结果（STM32H723VGT6）

测试探针：`cc68c7a5995c`（`rmlink-a5995c.local`，Wi-Fi 连接，协商 DAP 容量 512 B）。当时未接物理 NRST 线，复位使用软件复位。

| 检查项目 | 实测情况 |
| --- | --- |
| 两阶段 Wi-Fi OTA | 过渡固件与 V1 正式固件均顺利上传、重启、校验 ELF 并确认 `VALID`，最终指纹 `7912ec692d3a985d`（启用 PSRAM） |
| 多档 SWD 识别 | `100 kHz` / `1 MHz` / `4 MHz` 下读取芯片 ID 与 128 B 向量表一致 |
| H723 芯片信息 | DPIDR `0x6ba02477`、CPUID `0x411fc272`、DBGMCU IDCODE `0x10016483`、Flash 1024 KiB |
| 内存与寄存器 | AXI SRAM 与 R0 读写核对通过，测试后已恢复原值 |
| 单步与硬件断点 | RAM 指令连续单步两次（R0=1, 2），继续运行后正常命中硬件断点（R0=3） |
| 板端缓存烧录 | 擦写 `0x080e0000` 起整块 128 KiB 扇区，板端逐字节比较与主机回读 SHA256 均通过 |
| 主机烧录路径 | 用主机路径写回同一扇区原内容，回读校验通过 |
| 软复位 / 订阅 | 软复位后暂停/继续正常；4 个 Flash 字段 100 Hz 采样 200 帧，序号连续无错 |
| 标准 OpenOCD TCP | 使用官方 `0.12.0+dev-g6fbe9e7` Windows 版（存档 SHA256 `d4ed61f96aec07fcacac532e27e9f356c03ecc24d7a209fe65c893d3cea6b6ec`）直连 TCP `3241`（`cmsis-dap backend tcp`、`target/stm32h7x.cfg`），默认排队模式完成识别、单步、128 KiB 烧录与原扇区恢复，无 Error 日志 |

原 1 MiB Flash 已完整备份并还原，整片回读 SHA256 与备份一致（`a457d8c6c7d81ec012b4e5cb4ef8f32c7c9dd6a18db73c5cfad25754247f3a9f`），`FLASH_OPTSR_CUR` 与 `FLASH_WPSN_CUR1` 保持不变。本轮实测固件版本字段为 `0.3.3`（ELF SHA256 `7ce4b32459d31effff5870f79ed0e17ea90e0fed00f1294287874de1444a054c`），原始日志与备份保存在本机 `artifacts/hardware/rmlink-v1-20261109/`。

### 顺手修掉的两个实测问题

- **低速 SWD 快照读取超时**：以前固定限制 2 ms，在 100 kHz 下读多个字会超时。现按请求时钟和操作数动态计算预算，100 kHz / 1 MHz / 4 MHz 复测均通过。
- **继续运行立刻命中断点误报超时**：运行前未清旧 DFSR 原因导致误判。现先清 DFSR 再检查 DHCSR，已在源码服务和打包 EXE 上复测通过。

> **本轮未测项**：外部示波器 SWCLK 波形、V1 板原生 USB 口、外接物理 NRST、UART、SWO、LED 与按键功能尚待后续单独验证。另外 OpenOCD 须用较新上游构建（如 `6fbe9e7`），旧版 `v0.12.0-esp32-20260831` 在 Windows TCP 初始化会报 `WSAGetLastError=10035`。
