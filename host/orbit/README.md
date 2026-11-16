# Orbit 实验适配补丁

2026-11-14 后续根因核查发现重采后仍发送旧 capture 队列的问题。RMLink 已改为先清理被替换采样槽，再绑定新流并 START；Timeline 使用固定 16 KiB 片内缓冲，采样槽总计 32 KiB，网络保留按芯片条件分配普通内存，TCP 计时改为 50 ms tick / 300 ms 初始 RTO。Orbit 自动重采仍作为失败后的恢复措施，最初丢包位置尚未定位，实板长期验收待完成。对照 XR-DAP 的来源、确定性复现和验证边界见 `artifacts/diagnostics/root-cause-review-20261114/report.md`。

2026-11-14 新增并修订 `patches/fast-stream-recovery.patch`，基于 Orbit `ff32402`，让 Timeline 显式启用 `realtime`。首版 1 秒时限会早于当前 1.5 秒基础 TCP 重传触发，现场出现反复重采；现改为新连接 5 秒宽限、稳定期 2.5 秒无完整帧时限，板端使用对应的整批发送预算。重采后连续稳定收帧 3 秒才重置退避，短暂突发不算稳定恢复；保留断流缺口。修订后的后台和 V1 固件已部署并核对摘要与启动状态，插件已构建，实际连续恢复待用户验收。普通订阅沿用原策略；realtime 订阅不用于 RMLR 完整录制。初版证据见 `artifacts/diagnostics/fast-stream-recovery-20261114/`，本次修订见 `artifacts/diagnostics/recovery-repeat-stall-20261114/` 和同名 provenance 文件。

2026-11-14 新增 `patches/capture-stream-auto-recovery.patch`：针对数据流已经关闭、Timeline 仍保持启用但一直等待的情况，在原控制 owner 上自动重建 Timeline 订阅，失败时以 500–5000 ms 间隔重试。取消采样会撤销重试并清理已在执行的重建；保留旧 capture 过滤与时间轴断流空白。该增量补丁基于 Orbit `8f669f8`，与前一份手动重采修复配合使用。全量 1317 项测试、类型检查和构建通过；开发插件构建已更新，须下次启动调试加载，实机验收待完成。来源、摘要及覆盖范围见同名 provenance 文件，现场证据见 `artifacts/diagnostics/timeline-stall-20261114/`。

2026-11-14 新增 `patches/capture-stream-restart.patch`，修复采样数据流结束后 Orbit 保留已完成的收帧任务，导致重新勾选 Timeline 仍为 0 Hz 的问题。数据任务结束时清理本地状态，下一次明确订阅可在同一控制 owner 中重新打开数据流；控制连接是否失效仍由控制和心跳请求确认。完整 1308 项测试、类型检查和构建通过；用户于同日确认遮挡后可恢复、连续采样 50 分钟正常。来源和摘要见同名 provenance 文件，分层验收见 [正式修复记录](../../docs/fix-log/2026-11-14-timeline-zero-retry.md)。

2026-11-11 新增 `patches/debug-console-summary.patch`，删除 RMLink 调试路径中的“ / 连接服务（板端订阅、缓存烧录）”，并在 J-Link 烧录成功输出末尾追加烧录总用时，保留其他输出和流程。补丁基于已合入详细烧录日志的 Orbit 主仓库，构建产物已更新；来源、摘要、自动化验证及待确认项见同名 provenance 文件。

2026-11-11 新增 patches/generic-flash-recovery.patch，显示板端通用恢复策略的 RAM 重试、降频、扇区重擦、进度回退和实际恢复耗时。策略维护于公共固件状态机，芯片差异由目标描述提供。连接服务须同步更新以读取恢复快照，旧固件沿用原协议字段。协议、边界及验证范围见 [统一目标算法包](../../docs/shared-target-package.md) 的通用板端烧录恢复策略一节。

2026-11-10 新增 `patches/detailed-flash-progress.patch`，为 RMLink 和普通 CMSIS-DAP（HID/WinUSB）链路补充详细烧录日志。RMLink 显示缓存上传、当前擦除扇区、写入进度和阶段耗时汇总，普通 CMSIS-DAP 逐次记录扇区擦除、分页写入、校验的开始与完成、地址范围及耗时。补丁同时包含公开 SDK 的可选 `onProgress` 回调，SDK 的维护入口为 `host/sdk/`。在已有 RMLink 集成的 Orbit 源码中执行 `git apply <补丁绝对路径>`，随后运行 `npm run typecheck` 和 `npm run build`。来源、补丁摘要及分层验证结果保存在同名 provenance 文件中。

新增或扩展芯片时遵守[新增芯片适配规范](../../docs/adding-targets.md)，以[适配记录模板](../../docs/templates/target-adaptation.md)交付资料、增量补丁、自动化与分层验收结果。

2026-10-19：新增 `patches/unified-target-pack.patch` 与 `tests/shared-target-pack.test.ts`，将普通 CMSIS-DAP 与 RMLink 的芯片描述/算法统一。构建算法后运行 `scripts/sync_orbit_target_pack.py <Orbit工作树>`，在现有 RMLink 集成工作树上应用增量并同步源码、产物和 SDK。脚本检查补丁上下文，不覆盖不同上下文的既有修改。完整设计、四组目标范围、构建和本轮验证见 [统一目标算法包](../../docs/shared-target-package.md)。下文为此前集成记录。

Orbit 源码在忽略的独立工作树 `artifacts/worktrees/orbit-rmlink`，原用户工作区未修改。此处只保存本轮适配的压缩补丁和固定版本记录，不包含完整 Orbit 仓库。

`patches/provenance.json` 记录原始提交、适配提交和压缩包 SHA256。用 Python 的 gzip 解压后，在所记录的基线 Orbit 独立工作树执行 `git am <patch>`；不要对有用户改动的主工作区直接应用。

适配通过 `rmlinkDeviceId` 选择 RMLink 公开 SDK owner。示例 launch 配置：

```json
{
  "type": "ozone",
  "request": "launch",
  "name": "RMLink experimental",
  "device": "STM32F103C8T6",
  "rmlinkDeviceId": "50787d15a924",
  "speedKHz": 4000,
  "flashBeforeDebug": false,
  "runToEntryPoint": false
}
```

型号和设备 ID 应按实际修改；ELF 继续使用 Orbit 现有配置。增强 Flash 服务已通过F103C8完整镜像验收，当前示例采用attach行为；需要烧录时显式启用flashBeforeDebug。Timeline/Watch/RTT 使用板端订阅；step-over/step-out 尚未实现。二维静态数组维度、行跨度及各维度越界检查已补齐，十个基准变量已通过真实适配器。

隔离工作树通过类型检查、构建、76文件/1055项测试（另1跳过）；真实调试适配器子进程经公开服务完成启动、十个离散二维数组字段的Timeline、Watch、RTT和暂停/继续，尚未安装至用户VS Code。五轮各档实测条件与限制见docs/subscription-performance-20261015.md。Orbit原许可继续适用，SDK自身为Apache-2.0；补丁仅作本地可复现开发记录，未推送上游。

2026-10-16独立工作树更新公共SDK至0.2.8，删除重复取消路径，使用SDK的取消/终态等待；烧录后停止旧本地流并可重新订阅。模型覆盖烧录前后RTT恢复及结构化取消，完整测试/构建再次通过。真实适配器完成同一全密度64KiB ELF的启动前烧录，服务终态backend=probe、verification=probe_full_compare，随后矩阵和共享三级链Timeline/Watch/RTT及暂停继续通过，完整Flash读回SHA256保持一致。约997Hz属于共享连续字段短测，不能替代五条独立三级链＋五个离散普通变量的约676Hz结果。
