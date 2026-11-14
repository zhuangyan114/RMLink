# 采样停顿日志

这些日志用于区分板端采样、缓存出队、TCP 发送、主机接收与解码的停顿。只增加诊断，不改变采样帧格式、周期、缓存容量、TCP 优先级或自动重连策略。尚未完成实板对照和用户验收。

## 主机日志

连接服务在 TCP 采样期间每 5 秒向现有 `service-error.log` 写入一条 `RMLink stream {JSON}`，关闭时再写一条。报告任务独立于 socket 读取，因此 `read()` 一直等待时仍会输出。`RMLINK_STREAM_DIAGNOSTICS=0` 关闭定时输出，订阅状态中的 `host_stream` 仍可读取。

- `time`：UTC 时间；`stream_id`：本次主机数据连接标识，不是访问令牌。
- `read_idle_ms`、`sample_idle_ms`：距上次收到字节、上次解析到完整帧的时间。两者不同可识别碎片持续到达但帧未完成的情况。
- `max_decode_ms`、`max_publish_ms`：解码及发布到服务队列的最大耗时。
- `max_loop_lag_ms`：独立报告任务的最大调度延迟。
- `bytes`、`frames`、`subscriptions`：累计接收量及每个订阅最近的 capture ID、序号和探针时间戳。
- `tcp`：Windows 当前 socket 的 TCP_INFO；不支持时明确记录不可用。
- `probe`：独立后台任务每 5 秒读取的板端发送、TCP 和缓存快照；`probe_snapshot_age_ms` 标明新鲜度。控制连接阻塞时，该任务可等待，主机进度日志仍继续输出。旧固件不支持时记录一次后停止该后台查询。
- `configured_ids`、`paused_ids`、`error`：区分未采样、主动暂停和异常。空闲时间长本身不代表故障。

计时最大值覆盖当前主机数据连接；日志频率有界，不逐帧打印。socket 计数查询或日志输出失败不会终止采样。

## 板端快照

`GET /v1/devices/{device_id}/health` 新增 `capture_stream`。旧固件返回 `supported: false`。新固件的基础发送统计始终可用，TCP PCB 详情需启用 `CONFIG_RMLINK_STREAM_DIAGNOSTICS`。PCB 读取只在 lwIP 线程执行，每秒一次；通过远端端口和连接代次匹配当前流。快照包含自己的探针时间和代次，不能把旧连接的 TCP 数据当成新连接数据。

- `send_in_progress`、`blocked_send_us`：当前整批发送是否仍在进行、已经过多久；包含批内多次部分发送。
- `network_wait_in_progress`、`network_wait_us`：是否在等待 Wi-Fi 重新关联。
- `last_dequeued_uptime_us`、`last_send_completed_uptime_us`、`batches`：判断任务是否持续取出、发送完成数据。
- `last_send_us`、`max_send_us`、`send_stalls_over_50ms`：发送耗时及超过 50 ms 的次数。
- `partial_sends`、`send_errors`、`last_errno`：部分发送和错误。
- `last_encode_us`、`max_encode_us`、`max_wake_us`：编码与批处理唤醒的耗时。
- `buffers`：四个订阅缓存的容量、最大可写记录大小 `max_item_bytes`；查询不取走任何记录。NOSPLIT 的该值上限为半个环减去记录头，不能把它当成总空闲字节或用来直接计算占用率。与订阅的 `completed`、`ring_dropped` 同看。
- `tcp`：RTO、当前重传次数、重复 ACK、拥塞窗口、接收端窗口、发送缓存余量、未确认/待发送段数。`available` 和 `same_stream` 必须检查；`snapshot_uptime_us` 说明新鲜度。
- `tcp_request_uptime_us`、`tcp_callback_wait_us`：回调请求时间及最近一次回调的调度等待。请求前进而 TCP 快照不前进时，需要检查 lwIP 线程是否阻塞。
- `rssi_dbm`、`channel`、`network_up` 和内部内存余量：无线与内存上下文。`channel=0` 表示没有有效 AP 信息。

基础发送统计在每批数据及 `send` 调用边界更新，不在 SWD 逐字段采样路径打印日志。缓存余量在健康查询时读取，不代表全程精确最小水位。不同页面按顺序读取，不是跨线程原子快照。

线协议为探针命令 `80 0f page`，page 为 0–3。每页响应 63 字节：`80 0f status` 后跟版本、页号、标志、保留字节，以及 56 字节小端数据。版本为 1，成功 status=0；标志位依次表示数据连接活动、TCP 诊断启用、发送中、等待网络中。保留字节为 0。

## 采集步骤

保持原来的 Orbit 会话和复现负载，运行：

```powershell
python D:\STM32\RMLink\scripts\collect_capture_diagnostics.py `
  --device-id cc68c7a5995c --duration 120 --interval 2 `
  --output D:\STM32\RMLink\artifacts\diagnostics\capture-stall-new.jsonl
```

脚本复用当前 owner，只查询设备健康和 Timeline 状态；不建立新的目标会话、不启动或停止采样、不写目标 RAM。文件必须不存在，以免覆盖旧证据。订阅状态查询会续租，因此不能用该次采集验证租约自然过期。结束后同时保存 `%USERPROFILE%\.rmlink\logs\service-error.log` 与 Orbit `outputs/Log/dap.log`。

诊断镜像应沿用设备原有参数。例如当前 V1 构建使用：

```powershell
./scripts/Build-Firmware.ps1 -TargetProfile -BoardProfile rmlink-v1 `
  -TcpPacketSize 512 -ExecutorCore 0 -ImageCachePsram `
  -StreamDiagnostics -StreamIpTos 160 -BuildTag capturediag20261111
```

该命令只构建，不部署。其他板卡需使用对应原有配置；不要在采日志时同时改变发送缓存、无线优先级或采样周期。

## 如何判断

| 同一时间段的现象 | 下一步定位 |
| --- | --- |
| `completed` 增长、缓存可写块缩小、`blocked_send_us` 增长 | 查看发送 TCP 的重传、窗口和未确认段 |
| `retries` / RTO 增长、未确认段持续存在 | 优先排查无线丢包、ACK 路径和热点/驱动 |
| 接收端窗口接近零 | 检查主机是否及时排空 socket，再看解码与事件循环耗时 |
| `network_wait_us` 增长 | 对照关联状态、RSSI、Wi-Fi 事件 |
| 板端发送进度正常，主机 `read_idle_ms` 很大 | 对照两个方向的 TCP 计数及接收任务调度 |
| `max_decode_ms` 或 `max_loop_lag_ms` 很大 | 检查主机解码、录盘和事件循环阻塞 |
| TCP 回调快照长期不更新，回调请求仍在更新 | 检查 lwIP 回调队列与线程阻塞；不可直接用旧窗口值判断 |
| 订阅状态停止，`stop_reason` 有值 | 根据 SWD、断流、租约或样本上限单独排查 |

这些组合用于缩小范围；单项计数不能证明根因。先保留原负载复现，再做降采样率或更换接入点的独立对照。
