# Orbit 在断流后重新勾选 Timeline 仍显示 0 Hz

## 日期与用户确认

- 确认日期：2026-11-14。
- 用户先报告：“timeline0hz，重新勾选仍然0hz，重启调试后恢复正常”。
- 修复并重新构建后，用户确认：“现在很稳定了，遮挡后也能恢复，连续采样50min没有问题，写入并提交”。
- 用户确认的是这次 Orbit 修复与 [后台重采修复](2026-11-14-capture-stream-retry.md) 配合后的结果。没有将这份反馈扩展为其他设备、无线环境或全部故障注入场景的验收。

## 问题与现场证据

后台已允许同一控制会话重建采样，但 Orbit 的本地消费任务没有随数据流结束而清除。用户重新勾选后，后台恢复收帧，Timeline 仍显示 0 Hz；重启调试创建新 channel 后才恢复。

现场日志显示：本地 13:57:24，旧采样数据流以 `Probe capture stream closed` 结束；14:02:57 至 14:03:39，新板端数据流正常接收，`capture_id` 随重新勾选变为 3、4、5、6、8、9，接收帧数从 33960 增至 158814，`error=null`。这将故障范围缩小到后台收帧之后的消费路径。原会话的 DAP 日志已被重启覆盖，原始 EOF 回调没有直接保留。

实际调试适配器来自 `C:/Users/22690/Desktop/AI/Orbit/dist/debugadapter.js`，通过 RMLink 服务访问设备 `cc68c7a5995c`，唯一 target owner 为 `cmsis-dap`。

## 根因分析

`RMLinkTargetChannel.subscribeSerial()` 只在 `streamTask` 为空时打开本地二进制数据流。SDK 的异步迭代正常结束后，原代码仍保留已经完成的 Promise；正常 EOF 不会进入异常分支，因此后续订阅一直跳过创建新读任务。

异常分支还有另一个问题：独立采样流抛错时直接设置 `connected=false`，把数据通道失败当成控制 owner 丢失。控制请求仍可用时，这会阻止同一会话继续使用。

新增回归在修改源码前分别复现这两条路径：EOF 后只打开过一次数据流，异常后控制 owner 被误判失效。

## 修改方案

- 在读任务的 `finally` 中清理 `streamTask` 和 `streamAbort`，使用同一 `AbortController` 的身份检查，避免旧任务清掉新任务。
- 先保存任务引用，再进入异步读循环，保证立即结束或抛错也能清理。
- 数据流正常结束或抛错后，下一次明确订阅可以在原控制 owner 上重新打开本地流。控制 owner 是否失效继续由控制和心跳请求确认；真实控制失联仍沿用原处理。
- 增加订阅、流打开、结束及失败日志；不逐帧输出，不另开目标 owner，不自动重放订阅。

## 涉及文件

- Orbit：`src/ozone-backend/rmlink-target-channel.ts`，修改 `subscribeSerial`、`stopCaptureStream`。
- Orbit：`src/ozone-backend/rmlink-target-channel.test.ts`，新增 EOF／异常后重订阅的回归，检查新流能交付 Timeline 样本且只建立过一个控制会话。
- RMLink：`host/orbit/patches/capture-stream-restart.patch` 及同名 provenance 文件，保留两文件增量与来源。
- RMLink：`host/orbit/README.md`，增加补丁入口。

## 验证步骤与结果

### 自动化

1. 新增两条回归后先运行旧实现，两条均失败。
2. 应用修复后，RMLink channel、DAP RMLink 订阅和 owner 路由相关的 3 文件／60 项测试通过。
3. 完整 Vitest 96 文件／1308 项通过，1 项跳过；类型检查、完整构建和差异检查通过。

自动化使用模拟目标和数据流，不写成无线实板长时间测试。

### 构建与实板状态

修复通过正常构建写入当前开发插件的 `dist/debugadapter.js`，构建摘要为 `6ea68de1096ab535aab7793add6f7b95fa3a9b8da44a881e5b32437b03140e0b`。已加载的调试进程不会热更新该文件，因此交付时说明下一次启动调试才加载修复。

这次没有修改 ESP32 固件，也没有为验证额外执行目标烧录、复位或变量写入。

### 用户手动验证

用户于 2026-11-14 确认整体使用稳定，遮挡后可恢复，连续采样 50 分钟没有问题，并授权写入和提交。没有另行量测该窗口的丢帧数、恢复耗时或精确采样率。

## 原始证据与关联记录

- `artifacts/diagnostics/timeline-zero-retry-20261114/`：故障前日志快照、流摘要、修改前源码和构建产物、两条旧实现失败结果、聚焦／全量测试、类型检查及构建日志。
- `artifacts/diagnostics/timeline-recovery-acceptance-20261114/`：用户确认原文及提交范围检查。
- Orbit 的 `docs/bug-fix-log.md`：同日配套记录，源码及日志提交为 `0cd6ac68541198a50703aafc56ee433fed8070af`。

补丁只包含 Orbit 的相关源码和测试；完整参考仓库、构建产物与原始日志不随 RMLink 提交。
