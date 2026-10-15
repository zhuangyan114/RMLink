# Orbit 实验适配补丁

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

型号和设备 ID 应按实际修改；ELF 继续使用 Orbit 现有配置。增强 Flash 服务已通过F103C8完整镜像验收，当前示例采用attach行为；需要烧录时显式启用flashBeforeDebug。Timeline/Watch/RTT 使用板端订阅；step-over/step-out 和二维数组快速采样表达式规划尚未实现。

隔离工作树通过类型检查、构建、76文件/1053项测试（另1跳过）；真实调试适配器子进程经公开服务完成启动、三标量Timeline、Watch、RTT和暂停/继续，尚未安装至用户VS Code。实测条件与限制见docs/subscription-performance-20261015.md。Orbit原许可继续适用，SDK自身为Apache-2.0；补丁仅作本地可复现开发记录，未推送上游。
