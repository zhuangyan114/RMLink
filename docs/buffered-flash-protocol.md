# 缓存烧录接口：固件 0.2.12、服务／SDK 0.2.7

2026-10-19：[统一目标算法包](shared-target-package.md)扩展到 F407VE/F407IG/H723VG。目标选择、包 tag、64 KiB ProgramPage 编码与可选保持暂停扩展见该文档；以下保留 F103 历史协议和性能条件。

0.2.13 扩展：features bit3 表示完整 64 KiB 镜像批量擦除，mask=15；结果增加 erase_strategy=mass/page，旧字段和命令兼容。[条件与验收](buffered-flash-0213.md)。

服务 0.2.8 起，默认在板端完成全镜像比较；`verify_on_host=true` 额外做独立主机 SHA256。verification、probe_verified、host_verified 明确区分层级，见 [校验模式](buffered-flash-0214.md)。下文 0.2.7 的历史耗时包含两种回读。

Windows 连接服务统一接收镜像；ESP32 缓存后在唯一 SWD 执行器调度 STM32 RAM 算法。Python、Node、C ABI 均可调用，标准调试软件继续通过虚拟 USB 使用标准 CMSIS-DAP。增强目标当前限定 STM32F103C8 已声明的 64 KiB 主 Flash、整 1 KiB 页镜像，不写选项字节。

## 本地服务

持有 owner 后向 `POST /v1/sessions/{id}/flash` 发送：

1. `{"action":"begin","address":134217728,"byte_length":65536,"sha256":"64个小写十六进制字符","backend":"auto"}`。
2. `{"action":"chunk","offset":0,"data":"十六进制数据"}`，每片 1..4096 B；完全覆盖、无冲突后允许提交。
3. `{"action":"commit"}`，等待终态；独立请求 `GET` 相同路径读取进度，`POST {"action":"cancel"}` 请求取消。

backend 为 auto／probe／host。auto 协商板端 feature bit5，旧固件走 host；新固件只有 BEGIN 明确返回内存不足、尚未上传／提交时允许回退 host。probe 强制缓存路径，host 保留主机逐页调度用于兼容和对照。丢失回复或未知 COMMIT 均不自动重放。

commit 验证暂存镜像后独占作业、撤销旧订阅；其他目标操作和订阅创建被拒绝，GET 进度和 cancel 仍可用。完成后需要新建订阅。完成后的 cancel 返回 `cancel_requested=false, already_terminal=true`，保留成功状态。

只有 `phase=complete, verified=true` 表示完整成功。probe backend 完成半字比较和板端全镜像逐字节比较，可额外做主机 SHA256；host backend 完成主机全镜像 SHA256。两者均检查选项字节和软件复位。结果另有 backend、verification、host_verified、probe_verified、probe_verified_bytes、programmed_bytes、erased_pages、halt_confirmed、write_completion_may_be_unknown。上传完成不等于已烧录。取消返回 HTTP 409 / FlashCancelled，失败返回 FlashFailed，均附 progress。

timings 单位 ms：probe_upload_ms、probe_commit_ms、host_verify_ms、enhanced_total_ms。probe_stage_us 是 ESP32 本地累计 upload/preflight/loader/erase/program/page_verify/full_verify，单位 us。主机和 ESP32 分别计时，不相减推断单向无线时延。

Python `flash_image(data,address,backend='auto')`，Node `flashImage(data,address,signal,{backend:'auto'})`。Node AbortSignal 发送独立 cancel 并等待原 commit 终态。C ABI `rm_call` 使用 suffix=`/flash` 和相同 JSON action；应预留足够响应缓冲，`-122` 仍可能表示该命令已执行，不能重发 commit，应改用 GET `/flash` 查询服务保留的进度。这不是可无副作用调用的“先查询所需大小”接口。

## 板端厂商命令 0x82

整数 little-endian。请求 `[82,sub,payload...]`，响应 `[82,sub,status,payload...]`；十六进制 82 为命令号，status 非零表示拒绝。USB64／TCP512 使用相同命令，容量 P 由 DAP_Info 0xff 协商；0x81/0 feature word bit5 声明支持，未声明标准 Atomic Commands。

| sub | 请求 payload | 成功响应 payload |
| --- | --- | --- |
| 0 CAPS | 无 | version u8=1，profile u8=1，max_image u32=65536，page u16=1024，chunk u16=P−16，features u8=7 |
| 1 BEGIN | operation u32 非零，address u32，length u32，SHA256 原始 32 B | 空 |
| 2 CHUNK | operation u32，offset u32，length u16，CRC32 u32，数据 | received_bytes u32 |
| 3 SEAL | operation u32 | 空；实际 SHA256 验证后 ready |
| 4 COMMIT | operation u32 | 空；一次受理、异步执行 |
| 5 STATUS | operation u32 | 下述 43 B 状态 |
| 6 CANCEL | operation u32 | 空；有界步骤之间停止 |
| 9 TIMINGS | operation u32 | 上述七阶段，各 u64 us |

features bit0 缓存／完整性封存，bit1 异步提交／状态，bit2 取消。CHUNK CRC32 使用反射多项式 0xedb88320，初值／末尾异或 0xffffffff。顺序上传；已收到区间内完全一致的重叠可重复确认，返回当前 received_bytes；缺口、冲突或跨入未收到尾部的重叠拒绝。SEAL 前不擦写目标。

STATUS payload 为 `<BBBIIIIIIIQI`：state/error/flags 各 u8；operation/address/length/received/erased_pages/programmed_bytes/verified_bytes 各 u32；elapsed_us u64；error_address u32。flags bit0 已发起擦除、Flash 可能改变，bit1 板端完整比较通过，bit2 失败／取消后已确认核心暂停。error_address 是最后相关访问地址，不是已修改范围的精确边界。

state：0 idle、1 upload、2 ready、3 preflight、4 load、5 erase、6 program、7 page_verify、8 full_verify、9 reset、10 complete、11 failed、12 cancelled。

status/error：0 OK、1 参数、2 owner、3 状态、4 内存、5 CRC、6 SHA、7 目标身份、8 SWD、9 RAM 算法、10 超时、11 取消、12 回读不一致、13 选项字节变化、14 忙。

缓存绑定 transport + session + operation；其他 owner 不得查询、提交或取消。ready 之外的 COMMIT 拒绝。关闭连接释放缓存、停止旧作业并尽力暂停核心；新连接须新操作，断电不续烧。终态保留到新 BEGIN 或会话关闭。

## 存储与验收边界

`-ImageCachePsram` 为已确认的 Octal 80 MHz／8 MiB PSRAM 开启独立配置，镜像和 48 KiB Timeline 记录 payload 显式分配外部 RAM。环控制、栈、SWD 数据、USB DMA 保持内部 RAM；传输任务先复制记录再发送。PSRAM 不可用时环回退内部 RAM，镜像不足显式报错，标准 DAP 仍可用。

实板通过 USB／Wi-Fi 上传异常、实际 SHA256 拒绝、重复提交、擦除后取消、新作业完整恢复；跨 USB owner 拒绝；实际 TCP close 后约 30 ms 释放且不重放旧作业；Node AbortSignal 取消和终态保持。静默网络黑洞、目标断电和物理 NRST 未在本轮测试。

同一全密度 64 KiB fixture、请求 4 MHz、同一目标和热点，交替各五次中位 host 18.552 s、probe 9.119 s。随后连续读优化单次 probe 7.395 s，主机校验 1.481 s；单次不替代最终重复中位数。实际 SWCLK 未量测。原始证据和后续进度见 [计划](buffered-flash-plan.md)。

1464 B MIT RAM 算法来自 Orbit 3928bbb6f4990ff336dfbe71f745f5f593b12e48；来源和版权在 `host/algorithms/stm32f103c8/provenance.json`、`LICENSE` 保留。其余状态机和接口由 RMLink 实现。
