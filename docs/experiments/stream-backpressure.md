# TCP 采样流回压检查

日期：2026-10-15。自然异常见 [0.2.11 检查点](../memap-reuse-0211.md)：首轮十分钟独立四级链、Watch 与 RTT 共存时出现 Timeline 缓存丢弃，较大帧间隔集中在约第 52–66 秒。CRC、有效字段与已提交 RTT 字节连续不等于没有丢弃。

本检查主动让电脑暂时不读取 TCP3244，用于验证板端缓存满时的行为。即使复现同样的计数，也不能据此认定首轮异常来自电脑接收停顿；自然事件还缺少同时采集的发送耗时、缓存水位与主机接收间隔。

## 条件与步骤

- 同一 ESP32-S3、STM32F103C8 fixture，固件 0.2.11、连接服务 0.2.6，请求 SWD 4 MHz；实际 SWCLK 未量测。
- Timeline 十个静态地址的动态 32-bit 叶字段，地址间隔 64 B，请求周期 1 ms；Watch 前四字段、100 ms；RTT 5 ms。此处不使用指针链，以较高的产出速率施加回压，不能当作四级指针性能结果。
- 公共 API 持有一个原始 DAP 会话，控制命令与采样共用原有执行器；测试脚本自行消费一次性票据绑定的数据流，不启用高层 TargetEngine 或第二个 SWD owner。
- 连接数据 socket 前请求接收缓存 8192 B，并记录系统返回的实际值。独立接收线程正常工作三秒，再分别暂停读取 50 ms、1500 ms，每次恢复接收三秒；暂停期间每隔至多 100 ms 发送采样心跳，状态查询不阻塞接收线程。
- 校验 CRC、字段身份／有效位、序号单调和 RTT 已提交字节连续性；记录三个槽的丢弃、失败、期限跳过和完成计数。短暂停顿要求无溢出，长暂停顿要求真正触发 Timeline 板端缓存丢弃。
- 恢复后额外观察三秒，要求丢弃计数不再增长、三类任务继续产出、SWD failures 为零、核心仍运行；停止订阅后读 CPUID 与 fixture 标识，最后确认 owner 释放。

测试不擦写 Flash、不修改应用 RAM、不暂停或复位 STM32。CoreSight 连接与采样配置会更新调试寄存器。这里只验证实时丢旧及恢复，不能作为严格无损录制通过的证据，也没有模拟热点断开、电脑重启或静默半断连。

```powershell
& 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe' `
  'D:\STM32\RMLink\scripts\verify_stream_backpressure.py' -h
# 确认连接的是本项目 F103 fixture，且没有其他目标会话：
& 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe' `
  'D:\STM32\RMLink\scripts\verify_stream_backpressure.py' `
  --fixture-confirmed --output 'D:\STM32\RMLink\artifacts\logs\stream-pressure-new.json'
```

## 结果

<!-- PRESSURE_RESULT -->
最终独立接收线程版实板通过。实际接收缓存为 8192 B；停读 50.52 ms 时三槽丢弃增量均零，停读 1500.38 ms 时 Timeline 丢弃增量 794、Watch 0、RTT 压力计数 123。RTT 该计数包含推迟 poll，已提交字节仍连续；不能直接称为丢失 123 帧 RTT 数据。

恢复后的额外 3.0025 秒窗口，Timeline 计数保持 794、RTT 保持 123，未继续增长；接收到 2963 个 Timeline 帧，按探针时间戳计算约 952.43 Hz。三任务均继续产出，SWD failures 为零，核心仍运行，后续 CPUID／fixture 标识正常，owner 最终为空。这是恢复后观察窗口的采样率，不是从解除停读到恢复的精确耗时。

原始报告：`artifacts/logs/stream-pressure-threaded-0211.json`。此前保留两份诊断：`stream-pressure-0211.json` 在同步查询状态后仅验证仍能产出；`stream-pressure-settled-0211.json` 增加三秒计数不增长条件后失败（Timeline 1509 → 2030）。两版在同一线程查询状态和读数据，查询期间也会额外停读，混入了测试脚本自身的回压；不能拿它们的恢复窗口或丢弃量直接与最终版对比。固件与服务均未改，修正的是试验隔离方式。

最终脚本只在显式停读窗口关闭接收开关，并等待已开始的至多一次 recv 结束。数据线程独立进行 CRC、字段及 RTT 连续性检查；主线程继续查询同一个 owner 下的状态。这一修正没有放宽“恢复后不再丢弃”的标准。

## 后续诊断

若自然异常再现，先增加有界汇总诊断：板端单次 `send()` 的最大／累计等待、分档次数、错误与本轮流身份；各订阅环形缓存最小余量；主机接收间隔与事件循环停顿。所有统计应在控制 API 中提供，不逐帧打印串口、不无界保存日志，也不改变数据帧格式或重新申请目标所有权。

对照普通版评估诊断本身的开销，再分别施加数据接收暂停、慢录盘和无线波动。依据同一时间段的计数区分板端调度、网络发送、主机接收及录盘压力；随后才决定缓冲、任务或传输策略改动。不能通过增大缓存、关闭溢出检查或忽略 RTT 状态来宣称问题已解决。
