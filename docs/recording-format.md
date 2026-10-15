# RMLink 原始录制格式 RMLR v1

服务 0.2.3 的原始记录容器，供第三方工具离线解析。全部整数为小端；文件不是 Python pickle，不执行载入代码。公开 Python 读入器是 `host/rmlink_connector/recording_reader.py`，命令入口为 `scripts/inspect_recording.py`。

| 顺序 | 字段 | 长度 |
| --- | --- | --- |
| 文件头 | ASCII `RMLR` | 4 B |
| 元信息长度 | uint32，1..65536 | 4 B |
| 元信息 | UTF-8 JSON，含 version=1、connector_version、device_id、created_at、strict、configs | 指定长度 |
| 逐条记录 | cursor:uint64、frame_length:uint32、原始 RML1 frame | 12 + 56..384 B |
| 结束哨兵 | cursor=0:uint64、frame_length=0:uint32 | 12 B |
| 尾部长度 | uint32，1..65536 | 4 B |
| 最终状态 | UTF-8 JSON，含 frames、gaps、invalid_samples、phase、error、finalized、window_end | 指定长度 |

元信息 freezes 当前所选订阅配置和 capture_id。逐条记录保留探针时间、序号、有效性、原始值、RTT 字节；服务游标跨订阅递增，探针 sequence 分订阅递增。不要根据服务游标推算采样时间，或将不同 capture_id 拼接为同一次采集。

文件尾部必须存在，之后不允许多余字节；尾部 frames 必须等于解析到的帧数。每帧按 RML1 验证 schema、长度和 payload CRC32。停止返回的 SHA256 覆盖整个文件，包括元信息、记录和尾部；SHA256 不嵌入自身。读取器可边读边回调，不随文件长度增长常驻内存。

录制器在历史缓存之外工作，写入队列最多 2048 帧，每批最多 128 帧交给后台文件线程。慢界面只影响该界面自己的游标。磁盘持续跟不上、磁盘满或网络断开仍可能让录制失败；有界队列不承诺无限缓存。

`finalized=true` 和 `phase=failed` 可同时出现：容器已完整封存，但记录质量不满足请求，分析程序必须保留失败原因。磁盘写入/flush/fsync 失败时 finalized=false；没有完整尾部的文件被读取器拒绝。严格模式的第一条缺口/无效样本不会假装为成功数据写入，尾部保留对应错误和统计。

窗口从服务收到 startRecording 后的新匹配帧开始，可能不包含任务第一帧；停止后不再接收新帧，仅排空队列。尾部 `window_end=last_received_record` 明确这一边界。尚未实现覆盖订阅整个生命周期的停止栅栏、应用 ACK、重传、断线续录、分段轮转或绝对时间漂移补偿。
