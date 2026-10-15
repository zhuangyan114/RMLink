# 2026-10-15 实板采样与增强组件验收

当前检查点为 0.2.3。用户重新插紧 SWD 线后，STM32F103C8 恢复应答；此前 NO_ACK 的失败日志保留。ESP32-S3 经电脑 2.4 GHz 热点通信，请求 SWD 4,000,000 Hz，实际 SWCLK 未用仪表量测。GPIO4→SWDIO、GPIO5→SWCLK，共地；没有实体 NRST 线。

## 条件与性能口径

STM32 保留同一份 64 KiB fixture，SHA256 `2a65c721fa1be9c4fcddba2a32bb96ccaeece439b517adb167f00ea928c031a4`。10 个动态 32-bit 字段位于 `0x20000100+i*64`，每个字段高 8 位标识字段编号；采样脚本逐帧核对编号、完整 bitmap、CRC、探针时间和序号。共存模式增加前四字段的 Watch（100 ms）和 RTT（5 ms 检查）。

每档五轮，每轮约 5 秒，交替先测独立/共存，避免固定先后顺序。有效 Hz 按探针首末时间计算，PC 收包批次不作采样时刻。完整数值、文件身份及原始日志 SHA256 见 [机器可读结果](subscription-performance-20261015.json)。这不是无线链路或 SWD 硬件的理论极限。

## 10 字段无线 Timeline

最终静态读取块为 8 字段；控制可在块之间插入。所有 30 轮均没有 SWD 错误、partial 样本、板端 ring_dropped 或消费者游标缺口。

| 请求周期 | 单独运行：中位 Hz（五轮范围） | Watch + RTT 共存：中位 Hz（五轮范围） |
| --- | --- | --- |
| 2000 us / 500 Hz | 500.00（500.00–500.00） | 499.81（499.61–500.00） |
| 1000 us / 1000 Hz | 941.91（931.85–943.60） | 834.28（832.00–851.99） |
| 200 us / 5000 Hz | 1173.43（1170.58–1175.89） | 1050.64（1044.74–1054.98） |

更高请求频率不保证按周期执行。实时模式将来不及执行的时槽记为 sequence 跳号和 deadline_missed，继续输出完整样本；执行跨度超过请求周期带 budget_overrun 标记。上表高频档有这些缺口和超限，不能标成“无丢样的 5 kHz”。严格 deadline 模式可在超限后暂停。

扩块前同条件 4 字段块的 40 轮记录保留：100 Hz 基本跟随；500 Hz 为 500.00/499.81；1 kHz 为 935.64/783.51；5 kHz 请求为 1121.55/1020.64（单独/共存）。8 字段块对该目标的改善有限，共存 1 kHz 档约提升 6.5%，未解决全部调度与 SWD 开销。

## STM32 → ESP32 本地读取

不逐次经过 PC 往返，由 ESP32 本地执行并计时；每次命令上限约 500 ms，因此离散基准可能未完成请求的 10000 次读取。

| 地址模式 | 完成 word 数 / 探针用时 | 有效数据吞吐 |
| --- | --- | --- |
| 连续 32-bit / 8 word 块 | 10000 / 357188 us | 109.36 KiB/s（约 112.0 kB/s） |
| 64 B 间隔离散字段 / 8 word 组 | 7190 / 500017 us | 56.17 KiB/s（约 57.52 kB/s） |

这是当前实现的局部吞吐测量，未排除所有 ESP32 调度开销，不是电气接口上限。10 字段约 1173 帧/s 对应约 46.94 kB/s 的变量净数据、112.65 kB/s 的 RML1 帧。历史纯 TCP 连续回传约 0.726 MB/s，明显高于本次变量净数据率；优先优化地址访问、SWD 和调度有依据，不能只提高 Wi-Fi 带宽。

同次主机 API 对 10 地址批量轮询中位约 16.13 Hz，包含本地 HTTP 及控制屏障的多次 Wi-Fi 往返；这不是 Orbit/J-Link 的实测基线。订阅不再为每一帧等待 PC 请求，但实际 Orbit 调试适配器还有状态监控与调试控制开销。

## 标准虚拟 USB 与增强 Flash

最终 8 字段固件下，OpenOCD 通过 Windows 虚拟 USB → 本机 USB/IP → Wi-Fi 完成 64 KiB 擦除/编程/校验约 5.27 秒，无 Error 行；独立完整回读约 1.10 秒，哈希一致、选项字节未变、新 fixture 保留且核心继续运行。暂停/寄存器/运行、RAM、断点/单步、DWT、软件复位、FAULT 恢复、SWD_Sequence 与取消/期限检查通过。pyOCD 0.45.1 通过同一虚拟设备完成识别、暂停、读 PC、继续。物理 NRST 排除在验收外。

增强服务 Flash API 的同镜像对照：首版逐寄存器访问约 104.35 秒，改为标准 DAP Value Match 轮询和成组读写核心寄存器后约 21.89 秒，约 4.8 倍改善；均有独立完整回读哈希及选项字节核对。它仍慢于标准 OpenOCD 路径，不能把两种计时互换。

## 三订阅、录制与第三方 SDK

真实目标 10 秒严格录制通过：Timeline 1007 帧、Watch 100 帧、RTT 916 帧，共 2023 帧，gaps=0、invalid_samples=0。100 个 Watch 帧均观察到 Timeline 复用；14816 B RTT 数据逐条核对魔数/XOR 校验。PC 未读 UI events，原始文件仍完整封存，并由独立读取器核对每帧 CRC、帧数和整个文件 SHA256。

另一次单字段 1 ms 订阅期间不消费 UI，独立实时文件保留 10022 帧、gaps=0。恢复读取时该消费者已落后历史窗口 5954 条；这是消费者缺口，文件并未丢失这些帧。host_history_evicted 是历史保留淘汰计数，不可当作板端丢帧。该测试不保证更长运行、断电末尾或磁盘满时无损。

三层指针、共享后缀、1/2/4 字节宽度、无效空指针字段隔离及 sample_limit=8 均通过。该组刻意无效字段使 failures 增加 8，其他四字段仍有效，任务以 sample_limit 结束；不能将这个统计归入 SWD 通信错误。

实际 WinHTTP C DLL 收到并校验 40 个有效样本；先消费 20 个结束流，再以保存游标续读 20 个，无重放。20 轮控制对照中，空闲 halt/run 中位约 71.44/40.86 ms；高负载 Timeline 时约 72.59/57.98 ms，观测最大 halt 111.39 ms。计时包括本地 API、Wi-Fi 及控制屏障，不是板端抢占延迟。

## Orbit 调试适配器实机范围

隔离工作树的真实 `dist/debugadapter.js` 子进程通过 stdio DAP 连接本机服务和真实目标，完成启动、三标量 Timeline、Watch 推送、RTT 输出、暂停/继续和退出释放。该试验 3 字段请求 500 Hz，实际约 457.57 Hz；主机逐样读取计数为 0，数据来自板端订阅。它与上表 10 离散字段服务基准的字段、控制监控条件不同，不能互换。

实机发现并修正了控制返回值缺少目标状态导致 `halt returned Unknown` 的适配错误：服务返回经 DHCSR 确认的 state，Orbit 同时兼容已有 DHCSR 响应。另一次十个二维数组表达式被 Orbit 现有快速采样规划器拒绝，未假装通过；服务显式地址/指针 API 已支持相同十地址测试。二维数组表达式规划和状态监控对采样的额外影响继续推进。

没有安装或切换用户当前 VS Code 扩展，也未验证 Timeline 界面长时间显示、Keil/IAR、HID、正式驱动签名或其他目标家族。当前 UDE 仍为此前授权加载的本地测试签名版本。

## 重跑与证据

先构建并保留 `Build-Stm32Fixture.ps1` 的 fixture；目标可擦写的标准验收使用 `accept_virtual_usb.py --allow-target-write --clock 4000000`，默认不恢复旧 Flash。采样使用 `benchmark_subscriptions.py --seconds 5 --repetitions 5 --periods-us 2000 1000 200 --output FILE`；正确性/录制用 `verify_subscriptions.py`，控制和 C SDK 用 `verify_enhanced_target.py`，Orbit 子进程用 `verify_orbit_adapter.py`。各工具需依次占用同一物理目标。

本地原始文件位于 `artifacts/logs/subscriptions-20261015/`：`subscriptions-{swd023,batch8}-five-runs.json`、`recording-target-023.json`、`controls-c-sdk-023.json`、`accept-4mhz-batch8.json`、`pyocd-batch8.json`、`orbit-adapter-scalars-023.json`、两份 enhanced-flash 对照。早期失败文件保留在同目录。该目录按仓库约定不提交，汇总与原始文件校验值进入本页 JSON。
