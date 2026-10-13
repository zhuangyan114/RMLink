# ESP32 与电脑的传输速率及延迟

2026-10-13，Asia/Shanghai。实测设备为现有 ESP32-S3 开发板，电脑 Windows 2.4 GHz 热点，RSSI 查询为 -19 dBm。只测试 ESP32 / PC，不读取或控制 STM32。

## 结果

| 通道 | 有效数据吞吐中位值 | RTT 中位数 | RTT P95 | RTT P99 | RTT 最大值 |
| --- | --- | --- | --- | --- | --- |
| USB，当前 64 字节 DAP 通道 | 289.27 KiB/s，约 2.370 Mbps | 0.1910 ms | 0.2721 ms | 0.3498 ms | 1.2503 ms |
| Wi-Fi，当前 64 字节 DAP TCP 通道 | 18.65 KiB/s，约 0.153 Mbps | 3.3384 ms | 54.8789 ms | 78.2072 ms | 157.1962 ms |
| Wi-Fi，直接 TCP 连续回传 | 5.134 Mbps，约 0.642 MB/s / 0.612 MiB/s | 2.3086 ms | 44.1034 ms | 58.6713 ms | 159.0471 ms |

RTT 的平均值分别为 0.2067 / 9.3882 / 6.2347 ms。P95 表示本组 95% 样本不超过该值。每条延迟路径是三轮各 500 个有效样本，共 1500；每轮预热 30 个样本，不计入统计。使用 PC perf_counter_ns，包含主机调用、传输及 ESP32 响应处理；不是单程延迟，不将 RTT 除以 2。TCP 计时不含 DNS 解析或建连。

## 吞吐方法与各轮结果

USB 和 Wi-Fi DAP 使用 80/07 诊断命令：逻辑请求 6 字节，响应 64 字节，其中 7 字节命令 / 状态 / 序号、57 字节可校验数据。USB 请求实际填充为 64 字节；TCP 每帧另有 8 字节头。延迟按单次请求响应测量；吞吐按连续提交 8 个请求并接收 8 个响应测量。

有效吞吐只计算 57 字节数据，响应吞吐另保存在原始 JSON。三轮每轮至少 2 秒，包含主机包处理及校验开销；各轮 USB 有效吞吐为 294.56 / 288.50 / 289.27 KiB/s，Wi-Fi 为 16.56 / 21.36 / 18.65 KiB/s。

直接 TCP 采用单独端口 3242，绕过 DAP 执行器。小包延迟发送 16 字节测试头 + 64 字节数据，设备原样回传 64 字节。连续回传发送一个请求，ESP32 从内存持续发送，不逐块等候新的主机请求。发送缓冲 4096 字节，接收按字节流处理；数据生成在发送前完成。每轮 4 MiB，共 5 轮、20 MiB，逐字节校验通过。

| 直接 TCP 轮次 | 有效速率 |
| --- | --- |
| 1 | 5.202 Mbps |
| 2 | 4.783 Mbps |
| 3 | 5.134 Mbps |
| 4 | 6.926 Mbps |
| 5 | 4.542 Mbps |

本次全部完成，数据错位 / 内容错误及超时均为 0；ESP32 运行时间持续增长，未重启，目标 owner 始终为 0。测试后 USB / TCP 的标准设备查询通过，诊断口显式关闭和自动超时关闭均通过验证。

## 环境与解释

- 固件 0.1.1，target profile，SHA256 EFD46B89990B272D2909461F7C6BC535651C5EC40E51F2E5353FCA2EF9EFA8F4。本轮新增传输诊断，ARM SWD 内核未改，GPIO 允许配置保留，但未申请目标会话。
- 原 0.1.0 target 镜像和刷写记录保存在 artifacts/hardware/rmlink-0.1.0-target.bin / rmlink-0.1.0-target-flash.json；先前 STM32 只读验证对应该版本。
- Windows 热点 On，TwoPointFourGigahertz；系统显示 Wi-Fi Direct 虚拟适配器链路速率 72.2 Mbps，物理 WLAN 为 Intel AX200。该系统链路显示值与上述应用有效吞吐使用不同口径。
- ESP-IDF 5.1.2，CPU 240 MHz，Wi-Fi power save 关闭，TCP_NODELAY 开启。当前 lwIP 配置及主机 / 驱动 / 热点条件均会影响实测，未寻找 ESP32 Wi-Fi 或 USB 总线的硬件极限。
- USB 结果仅覆盖目前 64 字节 DAP + WinUSB 主机工具；没有独立测量 USB 单向大块流接口。直接 TCP 的结果覆盖当前测试服务，未经过 SWD / 采样 / Flash 算法。

无线的直接连续回传具有比当前 DAP 小包通道更高的有效吞吐，说明减少往返、缓存批量回传有可用空间；具体板端采样效果仍需后续实测。Wi-Fi 小包存在明显长尾，实时控制与波形传输不能只采用中位延迟设计。当前结果不构成稳定带宽、烧录速度或变量采样率保证。

## 重跑与证据

```powershell
$rmlinkPython = 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe'
$env:PYTHONPYCACHEPREFIX = 'D:\STM32\RMLink\artifacts\cache\python'
& $rmlinkPython 'D:\STM32\RMLink\scripts\benchmark_transport.py' --output 'D:\STM32\RMLink\artifacts\logs\benchmark-transport.json'
```

需要 USB 与热点均已连接、无其他目标控制会话。脚本通过 USB 临时开启 TCP 测试口，最多 300 秒，完成后关闭；不会改变热点参数，也不调用 DAP_Connect / SWD。

原始每个 RTT 样本、各轮吞吐和设备前后状态：artifacts/logs/benchmark-transport.json。运行输出：benchmark-transport-run.log。构建 / 刷写 / 启动：build-transport-bench.log、flash-transport-bench.log、boot-transport-bench.log。诊断边界 / 自动关闭：verify-transport-bench.json。标准查询回归：verify-usb-after-transport-bench.log、verify-tcp-after-transport-bench.log。
