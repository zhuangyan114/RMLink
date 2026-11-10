# RMLink 与 F407 一起上电后首次烧录超时，返回 error=10

## 日期与用户确认

- 日期为 2026-11-10。
- 用户报告 RMLink 与 STM32 一起重新上电后，第一次烧录会稳定触发 `Flash failed: FlashFailed: Error: Probe Flash ended in failed, error=10`。
- 新固件安装后，用户按要求将 RMLink 与 F407 一起断电再上电，并回复“已一起断电上电”。随后自动执行冷启动后的第一次烧录验收。
- 用户在验收完成后明确回复“确认已经没有之前的问题”，并要求建立本目录、详细记录已手动验证通过的修复和功能。

## 问题描述

Orbit 使用 RMLink 连接服务、板端订阅与缓存烧录路径，唯一 target owner 为 `cmsis-dap`。烧录 `D:\STM32\RoboMaster\27_Sao_Bin\UP\build\Debug\frame.elf` 时，界面先显示 `Flashing still running (5s)...`，随后报 `error=10`。

`error=10` 对应板端 `RM_FLASH_TIMEOUT`。失败发生在 RAM Flash 算法加载与初始化阶段，尚未开始擦除或编程。

## 故障证据与根因分析

2026-11-10 20:51:22 的实板失败记录包含以下状态。

| 项目 | 观测结果 |
| --- | --- |
| 板端作业总耗时 | 5010802 µs |
| loader 阶段耗时 | 5009574 µs |
| 已擦除扇区 | 0 |
| 已编程字节 | 0 |
| PC | `0x0800FCE8`，位于 `HardFault_Handler` |
| xPSR / IPSR | `0x21000003` / 3，核心处于 HardFault |
| CFSR | `0x00040000`，INVPC 置位 |
| HFSR | `0x40000000`，FORCED 置位 |

旧流程直接暂停正在执行应用的核心，加载 RAM 算法，再替换 SP、LR、PC 与 xPSR。进入算法前缺少复位停核，也没有统一设置 CONTROL、PRIMASK 等特殊寄存器。应用残留的活动异常、PSP 或 FPU 上下文可能影响算法入口执行。算法内部的 `CPSID` 位于第一条 `PUSH` 之后，入口开始执行时仍存在上下文与中断窗口。

实板证据确认了初始化未完成、核心进入 HardFault 和 INVPC 置位。故障当次触发 INVPC 的具体中断及异常转换未被完整捕获。新增模型覆盖活动异常和挂起 IRQ，旧实现会超时，修复后通过。

## 修改方案

### 准备复位停核状态

完成目标身份、容量与选项字节检查并暂停核心后，保存 `DEMCR`，设置 `VC_CORERESET`，通过 `AIRCR.SYSRESETREQ` 请求复位。轮询 `DHCSR`，确认复位状态清除且核心已经停下，再恢复原 `DEMCR`，随后加载 RAM 算法。

该顺序清理应用的活动异常和处理器上下文，复位向量捕获使核心在复位后保持暂停。

### 准备算法寄存器与中断状态

每次算法调用前，通过 `DCRSR` 的寄存器选择值 20 写入 CONTROL、FAULTMASK、BASEPRI、PRIMASK 组合值 `1`，选择特权 MSP 并设置 `PRIMASK=1`。这一步在替换 SP、PC 和恢复执行之前完成，覆盖第一次 Init 调用。

### 处理取消与异常退出

准备阶段使用 1 秒复位停核期限。若过程中取消、超时或发生 I/O 错误，结束路径会尝试恢复保存的 `DEMCR`。回归测试覆盖复位后始终未停核、向量捕获期间取消和 `DEMCR` 恢复。

### 保留跨调试启动的故障证据

配套的 Orbit 修改在 RMLink 烧录失败后，将设备、ELF 路径、服务错误和板端 Flash 进度写入 `outputs/Log/dll-history.log`。后续调试启动保留该历史文件，使本次失败的阶段、耗时与已编程字节等信息可以继续查询。

## 涉及文件

### RMLink

- `firmware/components/debug_probe/rmlink_flash.c`，修改 `rmlink_flash_run`、`call_begin` 与 `finish`，实现复位停核、算法上下文准备和退出恢复。
- `tests/flash/flash_test.c`，补充挂起 IRQ、活动异常、复位停核超时、取消及寄存器恢复模型。
- `tests/flash/target_pack_test.c`，对 F407VE、F407IG、H723 算法调用检查准备状态，保留身份拒绝、完整回读和未知写入不重放等覆盖。

### Orbit 配套诊断

- `src/utils/logger.ts`、`src/utils/logger.test.ts`，增加跨启动保留的历史日志及测试。
- `src/ozone-backend/rmlink-target-channel.ts`、`src/ozone-backend/rmlink-target-channel.test.ts`，持久化失败进度，并验证记录在 owner 清理后仍可追溯。

## 构建与固件安装

| 项目 | 本次配置 |
| --- | --- |
| RMLink 设备 | `cc68c7a5995c`，ESP32-S3，rmlink-v1 |
| ESP-IDF | 5.1.2 |
| 固件版本 | 0.3.3，沿用版本号，以 ELF 摘要区分构建 |
| 构建标签 | `flash-context-20261110` |
| 运行配置 | TCP packet 512，executor core 0，PSRAM 镜像缓存，stream diagnostics，IP TOS 160 |
| 引脚 | SWDIO GPIO17，SWCLK GPIO18，NRST GPIO6 |
| 配置摘要 | `7912ec692d3a985d` |
| BIN 大小 | 913856 字节 |
| BIN SHA256 | `1214518b8767525292fc0457f6d4d5d65aae82f58926a0f69e7c8f5fa598db5e` |
| 固件 ELF SHA256 | `d681ace0e6bd9fc95c53fa03b18f052fcf6cbc36b59cc4b2de855d3e4cc26d84` |
| 安装方式 | Wi-Fi OTA |
| 安装确认 | `boot_confirmed`，重启后运行镜像摘要匹配，boot_state 为 VALID |

## 验证步骤与结果

### 自动化与构建

- `scripts/Test-BufferedFlash.ps1` 通过，覆盖 64 / 512 字节传输、F103 / F407VE / F407IG / H723 算法状态机模型，以及完整镜像、CRC / hash、owner / busy、取消、超时和不重放行为。
- 新增挂起 IRQ、活动异常回归在旧代码上复现失败，应用修复后通过。复位停核超时与取消模型通过。
- `python -B -m unittest discover -s tests/host -q` 通过，共 225 项主机测试。
- `scripts/test_network_native.py` 与 `scripts/test_ui_native.py` 通过。
- ESP32-S3 固件构建及相关差异检查通过。
- Orbit 配套诊断修改的聚焦测试、类型检查和构建通过，全量 Vitest 为 1264 项通过、1 项跳过。

### F407 实板

测试使用 STM32F407IG、SWD 4 MHz、同一份 `frame.elf`，通过真实 Orbit 调试入口和已选定的 `cmsis-dap` owner 执行。

每轮先启动带烧录的 `RMLink Debug`，等待停在 `main`，读取 Flash 结果、核心寄存器和故障状态，再通过该 DAP 会话独立回读 131072 字节计算 SHA256。检查完成后继续运行应用 2 秒，结束本轮调试。

| 轮次 | 场景 | loader 耗时 | 烧录与独立回读 |
| --- | --- | --- | --- |
| 1 | 新固件安装后首次测试 | 12.092 ms | 通过 |
| 2 | 应用运行后的再次接管 | 12.391 ms | 通过 |
| 3 | 应用运行后的再次接管 | 12.309 ms | 通过 |
| 4 | 应用运行后的再次接管 | 11.698 ms | 通过 |
| 5 | 应用运行后的再次接管 | 12.406 ms | 通过 |
| 6 | 用户将 RMLink 与 F407 一起断电上电后的第一次烧录 | 11.838 ms | 通过 |

6 轮均编程 131072 字节，板端完整比较通过，主机独立回读 SHA256 均为 `262b6e3214ecc223c353c8bcd1ba907c31e5c750eedfcfe5dc673aec35759c87`。每轮到达入口后 CFSR、HFSR 均为 0，IPSR 为 0。

F407 ELF 的 SHA256 为 `0adf730f9456cd6faa0ea7167a1b293d438a7a20dcec5d9ac6dbbd47288901a5`。

### 用户手动验证

用户执行了 RMLink 与 F407 一起断电再上电，并在上述验收完成后确认“已经没有之前的问题”。本条按该确认归档。

实板覆盖对象为本次 RMLink 与 STM32F407IG 组合，共 6 轮烧录，其中 1 轮为联合断电后的首次烧录。其他目标型号在本次记录中的验证层级为自动化模型。

## 原始证据位置

以下路径相对于 RMLink 仓库，原始数据保存在本地 `artifacts/` 中。

- `artifacts/logs/flash-diagnosis-20261110/attempt-02-failure.json`，修复前的失败进度。
- `artifacts/logs/flash-diagnosis-20261110/attempt-02-analysis.json`，PC、HardFault 与故障寄存器分析。
- `artifacts/logs/flash-fix-20261110/verification.json`，固件、6 轮验收及测试覆盖汇总。
- `artifacts/logs/flash-fix-20261110/trial-1794316119021-{1..5}.json`，前 5 轮实板记录。
- `artifacts/logs/flash-fix-20261110/trial-1794316271567-1.json`，联合断电后的首次烧录记录。
- `artifacts/logs/flash-fix-20261110/native-flash-after.log`，修复后的板端模型测试输出。
- `artifacts/logs/flash-fix-20261110/firmware-build.log` 与 `ota-update.json`，构建及 OTA 安装结果。
