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

型号和设备 ID 应按实际修改；ELF 继续使用 Orbit 现有配置。增强 Flash 实板验收未完成，当前示例采用 attach 行为；需要测试增强烧录时显式启用 flashBeforeDebug。Timeline/Watch/RTT 使用板端订阅；step-over/step-out 尚未实现。

验证为隔离工作树的类型检查、构建和模型回归，未安装至用户 VS Code。Orbit 原许可继续适用，SDK 自身为 Apache-2.0；补丁仅作本地可复现开发记录，未推送上游。
