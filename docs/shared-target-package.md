# Orbit 与 RMLink 统一目标算法包

2026-10-19。本轮将 Orbit 普通 CMSIS-DAP 与 RMLink 增强后端的芯片描述、算法源码和构建元数据统一维护。

## 范围与状态

这里的 DAPLink 指 Orbit 普通 CMSIS-DAP 路径，当前覆盖它已经注册的四组目标。
这不表示支持整个 DAPLink/pyOCD 生态的全部器件或任意 FLM 文件。

| 目标 | 接受的名称 | Flash | RMLink 状态 |
| --- | --- | --- | --- |
| STM32F103C8T6 | STM32F103C8T6、STM32F103C8 | 64 KiB | 历史实板证据保留；本轮新固件待回归 |
| STM32F407VET6 | STM32F407VET6、STM32F407VE | 512 KiB | 源码、主机模型与构建验证；待实板 |
| STM32F407IG | STM32F407IG、STM32F407IGT6/IGT7/IGH6/IGH7 | 1 MiB | 源码、主机模型与构建验证；待实板 |
| STM32H723VGT6 | STM32H723VGT6、STM32H723VG | 1 MiB | 源码、主机模型与构建验证；待实板 |

同家族其他型号、容量和其他 H7 不自动归入这些配置。家族 ID 不能识别精确封装，完整型号仍由板卡/工程提供。
已有的 RMLink source step-over/step-out 限制独立保留；本轮对齐芯片范围，不宣称两条路径全部调试功能等同。

## 唯一来源

- `host/algorithms/targets.json`：型号/别名、DPIDR、内核 part、芯片 ID/容量寄存器、各 RAM 窗口、擦除扇区、loader 布局、选项寄存器及算法元数据。
- `host/algorithms/<algorithm>/algorithm.c/.ld`：可独立构建的 MIT 目标 RAM 算法；LICENSE 和 provenance 随源码保留。F407IG 从同一 F407 源码构建，容量宏明确设为 1024 KiB。
- `scripts/Build-FlashAlgorithm.ps1`：统一构建 ELF/bin；生成器更新源码、链接脚本和二进制 SHA256，输出固件 C 数据、manifest 及分发目录。散列字段由构建维护。
- `host/rmlink_connector/target_pack.py`：服务端描述解析、范围/扇区检查及算法完整性检查。
- `firmware/components/debug_probe/rmlink_target.c`：板端目标与内存窗口，只在原 SWD 执行器访问。
- `host/orbit/patches/unified-target-pack.patch`：本轮增量适配，排除 Orbit 工作树既有的其他修改。

普通 CMSIS-DAP 由 Orbit 主机执行器调用目标 RAM 算法；RMLink 由服务上传镜像，ESP32 缓存并自主调用同一来源的目标 RAM 算法。
两条路径可以使用不同页缓冲区/栈地址，均须在声明的可访问 RAM 中，且代码、缓冲区和栈互不重叠。

当前内置算法采用既有自研 ABI，Verify 成功返回 0。CMSIS-Pack 的 Verify 成功可能返回 `address + size`，以及分阶段 Init/UnInit 等差异，仍须由后续导入器完整适配。
目前没有任意 FLM 直接导入入口；添加数据文件不会自动使未适配 ABI 可用。

新增 H723 选项寄存器的偏移核对了 ST 的 [stm32h723xx.h](https://github.com/STMicroelectronics/cmsis-device-h7/blob/master/Include/stm32h723xx.h) 中 `FLASH_TypeDef`：`OPTSR_CUR` 为 0x1C、`WPSN_CUR1` 为 0x38（2026-10-19 访问，仅引用寄存器事实）。算法 ABI 对照 [Open-CMSIS-Pack 函数约定](https://open-cmsis-pack.github.io/Open-CMSIS-Pack-Spec/main/html/algorithmFunc.html)。既有算法来源/许可仍由各目录 provenance 与 LICENSE 记录。

## 连接、采样与烧录

1. Orbit 将 `device` 传给同一个 RMLink 服务 owner。未知名称在建立目标连接前拒绝。
2. 服务核对 DPIDR、CPUID part、家族 ID 和容量；固件选择目标时再次只读核对身份。
3. 服务与固件核对包 tag。tag 是排序规范化 JSON 的 SHA256 前四字节，包含构建写入的源码/链接脚本/算法二进制 SHA256，防止同名配置混用旧算法。
4. Watch、Timeline、RTT 和指针链使用所选目标的独立 RAM/Flash 窗口，不将各 RAM 容量相加当成连续地址空间。
5. 擦除扇区、ProgramPage 大小、物理编程粒度与实际 RAM 数据长度独立处理。128 KiB 擦除不要求 128 KiB 页缓冲。
6. Orbit 复用原 ELF/Flash 规划，保留 `.data` LMA/VMA、重叠/越界检查及 H7 ECC 字合并。单作业覆盖完整擦除扇区，未提供的扇区内字节填 0xFF。
7. 不相邻扇区分组上传；中间作业校验后保持暂停，最后一组才复位运行，不擦除两组之间的其他扇区。
8. 上传至终态期间锁定目标，禁止切换 profile。选项寄存器前后比较，失败/取消后的未知写入不自动重放，保留现有 owner 与调度优先级。

F103 保留明确的 64 KiB 整片擦除优化；其他配置默认逐扇区擦除。

## 协议与兼容

`0x81/CAPS` 的 features bit11（2048）表示统一包目标选择。
命令 `0x81,21,profile_u8,package_tag_u32le` 只在持有 owner、没有订阅/烧录作业时执行；成功响应 `0x81,21,0,profile_u8,package_tag_u32le`。
身份、tag、owner 或活动任务不匹配时拒绝，不执行目标写入。

`0x82/CAPS` 保留原 11 字节 payload：version、profile、最大镜像 u32、ProgramPage u16、上传 chunk u16、features。
F103 保留 version=1，其他配置 version=2；ProgramPage u16 为 0 表示 65536 字节。
features bit4（16）表示 BEGIN 可以附加 `0xA5,reset_after_u8`。
旧请求/USB 零填充没有 0xA5 标记时，继续使用原来的完成后复位/运行行为。

公开 Flash BEGIN 新增可选 `reset_after`（默认 true）；Python SDK 同名，Node SDK 为 `resetAfter` 并可指定 `timeoutMs`。
`erased_pages` 保留字段名，在其他目标中计数擦除扇区。
旧固件继续支持原 F103 配置；其他目标明确要求升级，不自动切换型号、算法或 owner。

## 构建与同步

在 RMLink 根目录依次执行：

```powershell
.\scripts\Build-FlashAlgorithm.ps1
python .\scripts\sync_orbit_target_pack.py .\artifacts\worktrees\orbit-rmlink
.\scripts\Build-Firmware.ps1 -TargetProfile -TcpPacketSize 512 -ImageCachePsram -BuildTag target-pack
.\scripts\Build-Connector.ps1
```

使用 Python 3.11；本机可使用 `D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe`。
分发目录为 `artifacts/build/target-pack/`。固件包含同一构建生成的算法数据。
同步脚本复制描述、算法源码/bin、SDK 类型声明和专用测试，应用增量补丁，并在 helper 输出目录写同步凭据。
补丁上下文不同或算法源码存在其他修改时拒绝覆盖，需先检查差异。
Orbit `npm run build:native` 会重新构建本地算法，随后再运行同步脚本即可恢复统一构建产物。

新增目标统一遵守[新增芯片适配规范](adding-targets.md)，使用[适配记录模板](templates/target-adaptation.md)记录资料、实现、测试、实际产物和实板结果。
新目标进入两端共用注册表后，仍须核对 Orbit 原生算法构建/检查入口，重建配套服务与固件并完成同步；登记不等于硬件验收。

## 验证边界

自动化覆盖四组目标的身份/容量、边界、算法 BKPT/hash、H723 独立 RAM、FPB v2、主机及实际固件 C 状态机的大扇区、整镜像比较、取消、未知写入不重放及稀疏作业暂停。
Orbit 专用测试覆盖所有注册目标、`.data` LMA、H7 ECC 合并、稀疏扇区分组和不同容量拒绝。

本轮结果：

- Python 主机全量 206 项通过；末次 ABI/描述边界收紧后又通过 6 项目标包及 3 项主机烧录聚焦测试。
- 固件 C 烧录模型在 64/512 字节包下均通过，覆盖原 F103 和 F407VE/F407IG/H723 的最后一个 128 KiB 扇区；采样 C 模型通过新目标选择及 RAM 边界。
- Node SDK 7 项通过。
- Orbit 类型检查通过；完整 Vitest 单 worker：79 文件、1089 项通过、1 项跳过。此前 4 worker 有一次工作进程退出，单 worker 完整复验通过，未修改无关测试。
- Orbit 原生 helper 构建、CMSIS-DAP mock/算法检查、J-Link mock、插件构建通过。
- ESP32-S3 目标/TCP512/CPU0/PSRAM 固件构建通过，`rmlink.bin` 为 898512 字节；Windows 服务/终端 EXE 构建及离线 help 启动通过。
- `scripts/verify_target_package.py` 从冻结 EXE 解出四份算法，并与 Orbit 输出及固件内算法逐字节比较通过；包 tag 为 `0xD026A0B6`。

本机证据：`artifacts/logs/target-pack-orbit-tests.json`、`target-pack-cmsis-mock.log`、`target-pack-jlink-mock.log`、`target-pack-connector-build.log`、`shared-target-package-verification.json`。
冻结产物检查使用装有 PyInstaller 的 Python 环境执行 `scripts/verify_target_package.py --orbit <Orbit目录> --firmware <rmlink.bin> --connector <RMLinkConnector.exe>`，不连接设备。

未执行任何真实目标擦写/复位/暂停运行或 ESP32 刷写/OTA；未切换正在使用的服务及已安装扩展。
H7 缓存、RAM 域可达性、实际 Flash 时序及新固件长稳仍需实板分别检查。源码/模型/构建通过不能替代这层验收。
