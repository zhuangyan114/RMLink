# Orbit 的 RMLink 专用连接

当前入口在 `artifacts/worktrees/orbit-rmlink` 开发工作树中；需从该工作树构建、启动扩展开发宿主。Orbit 主仓库和已安装扩展不随此处文件自动更新。

## 调试配置

```json
{
  "name": "Orbit RMLink",
  "type": "orbit",
  "request": "launch",
  "probe": "rmlink",
  "rmlinkDeviceId": "50787d15a924",
  "device": "STM32F103C8",
  "interface": "SWD",
  "speedKHz": 4000,
  "program": "${workspaceFolder}/build/Debug/f103_mpu6050_src.elf",
  "flashBeforeDebug": true,
  "runToEntryPoint": "main"
}
```

`rmlinkDeviceId` 可以省略：仅当连接服务恰有一台已配对且在线设备时自动选择；多台时必须指定。`program` 也可由原有 ELF/AXF 自动查找填写。首次使用需已运行连接服务、完成配网和配对，并给 STM32 供电、共地、连接 SWD。

F5 经同一个服务会话执行连接、缓存上传、板端擦写和完整比较；连接成功后才开始烧录。`flashBeforeDebug:false` 明确跳过烧录。增强后端当前支持 STM32F103 medium-density Cortex-M3。连接服务0.2.14配合当前Orbit工作树，F5烧录后和调试工具栏复位默认停在`main`；也可指定其他ELF函数符号，显式`runToEntryPoint:false`保留不运行到入口的行为。无烧录的连接不会为了入口设置而自动复位正在运行的程序。

复位保留板端Timeline订阅并暂停它，在入口停住时Hz显示0是正常现象；按继续后同一订阅恢复采样。Watch可在暂停时读取。RMLink使用探针时间戳记录实际暂停间隔，超过5 ms按真实缺口分段，不压缩暂停时间或补点。主机显示和网络到达的剩余抖动见验收记录。

`probe:rmlink` 不需要 `cmsisDapTransport`、USB 序列号或虚拟 USB 挂载。Watch、Timeline、RTT 使用服务的订阅能力。标准 CMSIS-DAP/其他软件仍可走虚拟 USB，对应 Orbit 的 `probe:cmsis-dap`；同一物理目标始终只允许一个控制会话。

## 常见失败

- `TargetBusy`：目标被其他调试会话占用。结束原会话后重试；不会为了连接新客户端而抢占、复位或断开原客户端。
- `DeviceNotFound` / `Device offline`：确认连接服务已启动、电脑 2.4 GHz 热点已开、RMLink 已配对并上线。刚重启服务需要先完成设备连接。
- `AmbiguousDevice`：填入具体的 `rmlinkDeviceId`。
- 旧提示 `Target command 11 failed: 11ff`：发生在 SWD 时钟设置阶段，尚未烧录。新版将服务内的会话占用报告为 `TargetBusy`；原始标准 DAP 客户端继续遵循标准拒绝格式。

## 验证边界

F5、STM32 全量比较、实际 VS Code 数据接收与占用冲突均有实板记录。Timeline 的瞬时 Hz 不代表整段无丢样；持续稳定性以探针时间戳、序号、有效位和溢出计数判断，见 [本轮记录](timeline-stability-20261016.md)。
