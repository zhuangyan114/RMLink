# 设备健康与 SWD 时钟诊断（固件 0.2.15／服务 0.2.8）

诊断由统一连接服务提供，Python、Node 和 C 客户端均可调用。健康查询只读取 ESP32 元数据，不获取目标控制权；时钟测量需要已连接的增强会话，经过原控制屏障和固定 CPU0 的执行器，不允许与后台采样交错。

## 健康快照

`GET /v1/devices/{id}/health`，Python `device_health(id)`、Node `deviceHealth(id)`、C `rm_device_health`。旧固件返回 `supported=false`，离线返回 `connected=false`。字段单位为字节、微秒和 Hz。

厂商命令 `80 0a` 返回固定 64 字节：

| 偏移 | 内容 |
| --- | --- |
| 0..3 | `80 0a 00 01`：命令、成功、版本 1 |
| 4..27 | 六个 LE32：内部 RAM free/largest/minimum，PSRAM free/largest/minimum |
| 28..31 | 请求的 SWJ 时钟 Hz；这是配置值 |
| 32..39 | 快照启动时间 LE64，us |
| 40..63 | `<IQIH6B>`：累计采集 IO 失败次数、最后失败时间 us、耗时 us、响应长度、请求/完成操作数、DAP 状态、终止原因、是否写目标、保留 0 |

终止原因 0=未观察到 Abort，1=显式 Abort，2=原有 2 ms 墙钟截止触发。计数为 0 时最后失败为 null。快照跨会话保留、设备重启清零；不改变传输重试和超时策略。一次 deadline 并不能证明线材或目标硬件故障。

## 时钟测量

增强目标命令 `measure_swd_clock`，mode 为 `sequence` 或 `dp_read`。前者发送 SWDIO=0 的空闲时钟，后者读取 DP IDCODE，均通过现有 DAP 内核。RMT 在既有 GPIO5 数字输入上以 80 MHz 参考观察脉冲；不接第二套 SWD 输出，不复位 MCU，不改变目标 Flash。初版使用 line-reset 序列导致后续 AP 访问失效，已改为空闲零序列，并保留失败日志。

`81 12 mode` 返回 29 字节，除三字节通用头外为 `<BBBBIIIIIBB>`：版本、模式、符号数、保留 0、trace ID、参考 Hz、请求 Hz、延时循环数、DP IDCODE、fast 标志、DAP ACK。`81 13 id32 offset8 count8` 分片读取原始 32 位 RMT 符号；返回头为命令/状态、ID、offset、count。USB64 每片最多13符号，TCP512可一次返回64符号。关闭会话后 trace 失效。

每个符号低 15 位为 duration0、bit15 为 level0、bit16..30 为 duration1、bit31 为 level1。服务保留所有原始符号；计算周期时剔除首尾的启动/停止空闲段，再给出完整周期的 minimum/median/p95/maximum，时钟拉长仍计入。

这属于内部数字脉冲测量，时间量化 12.5 ns；不能代替外部示波器检查目标端电压、上升沿、振铃和长线信号质量。测试在每次测量后读取 CPUID 并检查核心运行状态，最后完整读回64 KiB Flash 比较 SHA256。

0.2.15 实板记录 `probe-diagnostics-idle-ready-0215.json`：100 kHz 请求测得约74.7 kHz；1 MHz约0.72 MHz；2 MHz约1.40 MHz；4 MHz在空闲序列约2.58 MHz、DP读取约2.67 MHz。每模式/档位3次，24次均通过，Flash SHA256保持 fixture。结果用于后续修正 Xtensa 延时换算；不是外部仪器实测。
