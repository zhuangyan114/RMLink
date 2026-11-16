# Orbit 与 RMLink 统一目标算法包

为了不让 Orbit 普通 CMSIS-DAP 和 RMLink 增强路径各写一套芯片配置和 Flash 算法，两边现在共用同一份描述文件 `host/algorithms/targets.json` 和同一套独立编译的 MIT 算法源码。

## 当前覆盖的芯片

这里指统一目标包已经接入的四组 STM32 型号（不代表支持任意外部 `.FLM` 文件或同家族未列出的其他容量型号）：

| 目标 | 接受的名称 | Flash | 验证情况 |
| --- | --- | :---: | --- |
| `STM32F103C8T6` | `STM32F103C8T6`、`STM32F103C8` | 64 KiB | 早期固件已有实板验收；统一包接入后主要经主机/C 模型验证，新固件待回归 |
| `STM32F407VET6` | `STM32F407VET6`、`STM32F407VE` | 512 KiB | 源码、主机模型与编译验证通过，待实板全面验收 |
| `STM32F407IG` | `STM32F407IG`、`STM32F407IGT6/IGT7/IGH6/IGH7` | 1 MiB | 源码、主机模型与编译验证通过，待实板验收 |
| `STM32H723VGT6` | `STM32H723VGT6`、`STM32H723VG` | 1 MiB | 2026-11-09 在 V1 板上通过 Wi-Fi 调试、双路径 128 KiB 烧录与 OpenOCD TCP（见 [V1 实板记录](rmlink-v1-bringup.md)） |

## 唯一来源与分工

- **`host/algorithms/targets.json`**：维护型号别名、DPIDR、内核 ID、芯片 ID/容量寄存器、各独立 RAM/Flash 窗口、擦除扇区表、Loader 布局、选项寄存器（如 ST `stm32h723xx.h` 的 `FLASH_OPTSR_CUR` `0x1C` 与 `FLASH_WPSN_CUR1` `0x38`）及算法摘要。
- **`host/algorithms/<algorithm>/algorithm.c/.ld`**：独立构建的 MIT 算法源码（`F407IG` 共用 `F407` 源码，宏定义切为 1024 KiB）。采用统一自研 ABI，Verify 成功返回 `0`（暂未适配通用 CMSIS-Pack `.FLM`）。
- **`scripts/Build-FlashAlgorithm.ps1`**：统一编译算法 ELF/bin，计算各文件和二进制的 SHA256，生成规范化包标识（`package_tag`，当前四目标为 `0xD026A0B6`）以及固件内嵌 C 数据和分发包（`artifacts/build/target-pack/`）。
- **`host/rmlink_connector/target_pack.py` & `firmware/components/debug_probe/rmlink_target.c`**：服务端负责校验范围与扇区规划，固件在唯一 SWD 执行器内复核芯片身份与目标窗口。
- **`host/orbit/patches/unified-target-pack.patch`**：Orbit 侧的增量适配补丁。

## 连接、烧录与协议约束

1. **严格身份核对**：Orbit 传入的 `device` 必须在服务和固件两层同时核对 DPIDR、CPUID、家族 ID、Flash 容量与 `package_tag`，不匹配直接拒绝，防止混用旧算法或错刷 Flash。
2. **独立内存窗口**：Watch、Timeline、RTT 和指针链只允许访问目标声明的独立 RAM/Flash 区间，不把不连续的 SRAM 块拼成连续地址。
3. **非连续扇区与 H7 ECC**：擦除扇区、编程页大小（如 H7 为 32 B ECC 字合并）与每次上传长度解耦。不相邻扇区拆成多组作业依次下发，中间作业校验后保持暂停，最后一组才复位运行；`F103` 保留整片 64 KiB 擦除优化，其余芯片按扇区擦除。
4. **选项寄存器保护**：烧录前后核对选项寄存器；烧录至终态期间锁定目标配置。

协议扩展要点：
- `0x81/CAPS` bit11（`2048`）声明支持统一包选型，子命令 `0x81, 21, profile_u8, package_tag_u32le` 在空闲且持有 owner 时生效。
- `0x82/CAPS` 中 `F103` 保留 `version=1`、其余目标为 `2`，`features bit4`（`16`）支持在 `BEGIN` 附带 `0xA5, reset_after_u8`。

## 通用板端烧录恢复策略

自 2026-11-11 起，板端缓存烧录的恢复逻辑统一收口在公共 Flash 状态机中，各目标沿用同一套流程：

- **RAM 阶段重试**：往目标 RAM 加载算法或写镜像页缓冲遇到可恢复 SWD FAULT 时，每段最多额外重试 **2 次**（清 DP sticky 错误，完整重配 SELECT/CSW/TAR 后重传并回读核对）。
- **降频重擦当前扇区**：重试耗尽且当前频率高于 **4 MHz** 时，本次作业自动降速到 4 MHz，确认停核后回到当前扇区起点重新擦写（算法加载失败则重装算法）；降频后仍失败即终止，作业结束或断开后恢复原调试频率。
- **未知写入不乱重放**：触发 Flash 编程/擦除命令时若完成状态未知直接报错退出，不盲目重放。
- **恢复快照**：`0x82/CAPS` 能力位 `0x20` 表示支持子命令 `10`（43 字节），向服务上报 `probe_recovery`（含最后动作：0 无恢复 / 1 RAM 重试 / 2 已降频、请求/当前时钟、累计重试、重擦扇区及耗时），供 Orbit 日志展示。

## 构建与同步到 Orbit

```powershell
# 1. 编译算法并生成固件 C 数据与分发包
.\scripts\Build-FlashAlgorithm.ps1
# 2. 同步描述、算法与补丁到 Orbit 开发工作树（有冲突会拒绝覆盖）
python .\scripts\sync_orbit_target_pack.py .\artifacts\worktrees\orbit-rmlink
# 3. 编译固件与连接服务
.\scripts\Build-Firmware.ps1 -TargetProfile -BoardProfile rmlink-v1 -TcpPacketSize 512 -ImageCachePsram -BuildTag target-pack
.\scripts\Build-Connector.ps1
```

离线检查打包一致性（无需连接硬件）：

```powershell
python .\scripts\verify_target_package.py --orbit .\artifacts\worktrees\orbit-rmlink --firmware <rmlink.bin> --connector <RMLinkConnector.exe>
```

新增目标芯片请参照 [新增芯片适配规范](adding-targets.md) 与 [适配记录模板](templates/target-adaptation.md)。
