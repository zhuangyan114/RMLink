# 0.2.14／服务 0.2.8：完整校验模式

标准 OpenOCD 本轮经虚拟 USB 完成同一 64 KiB fixture 的 program/verify/reset 为 5.041 s。0.2.13 增强缓存路径约 6.179 s，其中额外主机全量回读约 1.5–1.9 s。此前“约 3.21 倍”只针对原增强主机调度接口的同组对照，不能说对所有现有烧录软件都提高三倍。

默认保留 SHA256 缓存封存、RAM ProgramPage 对每个半字的比较、ESP32 对全部目标镜像的逐字节回读比较、选项字节检查、复位与核心状态确认。ProgramPage 已逐半字确认，删除每页重复调用 Verify 的调试寄存器调度；最终全镜像比较仍然必做。原 Verify 入口保留，旧 host 路径继续使用。

服务 0.2.8 的 begin 新增 `verify_on_host`，默认 false；true 再执行独立主机 SHA256 回读。Python `flash_image(...,verify_on_host=True)`；Node `flashImage(...,{backend:'probe',verifyOnHost:true})`。C ABI 传相同 JSON 字段。旧 host backend 始终执行主机全量 SHA256。

`verified=true` 始终要求至少一种完整镜像回读比较通过。结果明确区分：

| verification | probe_verified | host_verified |
| --- | --- | --- |
| probe_full_compare | true | false |
| probe_and_host | true | true |
| host_sha256 | false | true |

GET /flash 保留 address、byte_length、image_sha256（期望值）及终态。sha256 只在完整校验成功后填入。新增 Python flash_status/cancel_flash 和 Node flashStatus/cancelFlash，均调用原公开服务接口。

`benchmark_buffered_flash.py` 默认记录完整板端校验的时间，`--verify-on-host` 明确开启额外回读。两种模式验收后都再独立读取全部目标 Flash 并比较 fixture，额外验收时间单列。历史 0.2.12／0.2.13 测量包含提交内部的主机回读，比较时须保留该差异。

源码检查：98 项主机检查、7 项 Node SDK 检查、真实 Flash C 状态机 64／512 容量、ESP32 TCP512 构建通过；本候选尚未刷入。不会把预期节省时间写成实测结果。

持续采样：0.2.13 首轮十分钟在约 430 s 收到一个 Timeline partial 帧（flags=1、valid_bitmap=0、duration=4527 us），前面的 CRC／数据字段和其他订阅正常。已保留失败录制与 SHA256，第二轮同步 UART 重测，未改变正在运行的固件。此问题与额外主机校验模式分别处理。
