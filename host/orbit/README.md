# Orbit 实验适配补丁

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
