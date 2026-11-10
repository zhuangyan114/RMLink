# RMLink 与 CMSIS-DAP 详细烧录日志

## 日期与验收状态

- 日期为 2026-11-10。用户明确确认“rmlink已验收”，并要求先归档、提交。
- 普通 CMSIS-DAP 的 HID/WinUSB 链路已完成自动化与 mock 验证，用户明确说明“dap暂未验收”。本条按用户要求提前记录该部分，保留待验收状态。
- 用户随后提供的 H723 烧录 error=8 属于新的故障，另行排查。日志功能验收不代表该故障已经修复。

## 功能需求与设计依据

烧录时需要看到准备、上传、擦除、写入与校验的当前状态、实际地址范围和耗时，以便定位耗时及失败阶段。RMLink 的擦除和写入由板端执行，连接服务提供进度缓存。普通 CMSIS-DAP 由现有 flasher 调用算法，使用每次操作的实际报告生成日志。

## 修改方案

- 公开 JavaScript SDK 的 flashImage 增加可选 onProgress。COMMIT 期间每 100 ms 查询连接服务的缓存状态，一次只保留一个查询。保留单次 COMMIT 的最终结果，观察回调异常或进度查询失败不改变烧录结果。
- Orbit 的 RMLink 日志显示镜像提交、板端上传百分比、当前擦除扇区、写入进度、校验及完成汇总。仅由已验证的最终结果确认成功。失败时显示已擦除扇区、已写入字节、故障地址和实际已用时间。
- 普通 CMSIS-DAP 在现有算法调用前后输出初始化、擦除、写入、校验及结束信息，完成行包含实际地址范围与操作耗时，异常路径保留原有错误和未知完成状态处理。
- 进度通过现有 DAP 会话转发至 Debug Console，共用已选定的 cmsis-dap owner 和调度临界区。

## 涉及文件

RMLink 的维护入口为 host/sdk/rmlink-client.mjs、host/sdk/rmlink-client.d.ts、tests/sdk/flash.test.mjs。Orbit 增量补丁和来源摘要位于 host/orbit/patches/detailed-flash-progress.patch 及同名 provenance.json。

Orbit 的主要实现位于 src/ozone-backend/rmlink-flash-progress.ts、rmlink-target-channel.ts、cmsis-dap-flasher.ts、session-target-channel.ts、commander.ts、types.ts 与 src/debug/dap-session.ts，以及对应测试和 clients/rmlink/ SDK 副本。

## 验证步骤与结果

### 自动化与构建

- SDK 烧录测试 10 项通过，覆盖缓存进度、回调异常、延迟响应和进度查询失败。
- Orbit 全量 Vitest 1269 项通过、1 项跳过。类型检查、扩展构建、CMSIS-DAP native mock、算法验证与现有 J-Link mock 回归通过。
- 普通 CMSIS-DAP 的日志顺序、地址范围、耗时、verify=false、回读校验、失败与观察回调抛错均有自动化覆盖。普通 DAP 实板与用户验收仍待完成。

### RMLink 实板

- 探针为 cc68c7a5995c，Wi-Fi，SWD 请求 4 MHz，目标 STM32F407IG，运行探针版本 0.3.3、连接服务 0.3.4。
- 应用为 D:/STM32/RoboMaster/27_Sao_Bin/UP/build/Debug/frame.elf，Debug 构建，ELF SHA256 为 0adf730f9456cd6faa0ea7167a1b293d438a7a20dcec5d9ac6dbbd47288901a5。
- 烧录范围 0x08000000 至 0x0801FFFF，共 131072 字节、5 个扇区。上传 1.110 s、擦除 1.318 s、写入 1.491 s、完整校验 0.715 s，服务总耗时 4.723 s。Debug Console 详细输出已核对。
- 同一 Orbit DAP 会话独立回读 131072 字节，SHA256 为 262b6e3214ecc223c353c8bcd1ba907c31e5c750eedfcfe5dc673aec35759c87，与烧录镜像匹配，目标停在 main。
- 本功能修改 SDK 与 Orbit，无探针固件改动。上述固件版本仅用于标识测试环境。

### 用户手动验证

用户于 2026-11-10 明确确认 RMLink 已验收，普通 DAP 暂未验收。H723 新报告停在已写入 131072/262144 字节，故障地址 0x24016AC0，error=8，纳入后续修复范围。

## 原始证据位置

以下路径相对于 RMLink 仓库，原始数据保存在本地 artifacts 中。

- artifacts/logs/orbit-wifi-f407ig-2026-11-10T13-35-04-116Z，首次阶段耗时与回读证据。
- artifacts/logs/detailed-flash-logging-2026-11-10T13-52-16-801Z，RMLink 详细日志、烧录结果、会话与回读摘要。
- artifacts/logs/detailed-dap-logging-2026-11-10T14-15-12-543Z，普通 DAP 自动化、native mock、类型检查与构建记录。
