# RMLink 0.3.3 USB 连续回传测试

本页为测试候选的验收记录。发布前持续测试、兼容性与原程序恢复仍需完成，未完成的项目不算通过。

## 已完成

- 同一 ESP32-S3、原生 USB、WinUSB 和电脑上，旧请求／响应流水基线中位数 272.96 KiB/s。
- 独立连续 Bulk 通道三轮各 8 MiB，中位数约 9.01 Mbit/s、1.126 MB/s；期间控制请求约 0.22 ms 中位数，最大约 0.35 ms。
- 10 字段、1 ms 周期的 PSRAM 背压测试：暂停主机读取 2 秒，保留 2026 帧，恢复后接收 5030 帧，环形缓冲丢帧与 SWD 失败均为零。
- 安装后的 0.3.3 客户端 USB 15 秒短测：Timeline 约 1000 Hz，Watch、RTT、三层独立指针链、5 个直接字段和录制同时工作，consumer gap 为零。
- 原 STM32 64 KiB 程序已重新备份，SHA256 为 `eeb3aebb22d7b5967282934a4b0ae732c083da67753c197add68d54d718b9360`；选项字节已留存。测试中暂装无外设输出的 F103 fixture。

后续记录补充最终二进制摘要、带绝对块序号的逐字节测速、持续采集、取消／重连、旧固件与第三方 DAP、OTA、恢复原程序和公开包核验结果。

## 保留的失败记录

首轮复杂 USB 1 kHz 持续测试在 182.902 秒失败。此前 Timeline 为 999.9999 Hz，无序号缺口、无 consumer gap、无环形缓冲丢弃；随后出现无效样本和 SWD FAULT。停止后读取 DP CTRL/STAT 为 `0xf0000080`，包含 WDATAERR。重新初始化后核心仍运行、72 MHz 时钟及 4 KiB 指针区内容未变。WDATAERR 表示 SWD 写数据阶段错误或已接收写入被丢弃，见 [Arm 定义](https://documentation-service.arm.com/static/5f36a71560a93e65927c7dcb)；目前不能据此证明是具体接线、供电或软件时序原因，也不把该次运行列为稳定性通过。

最终候选补充 USB 完成结果／长度检查、取消时的读线程回收和设备停流检查。带绝对 4 KiB 块序号的三轮 8 MiB 逐字节测速中位数 **8.9981 Mbit/s**，零错序／内容错误，控制响应最大约 **0.29 ms**。20 次中途取消、阻塞读取取消约 **7.3 ms**、无效 ticket／重放拒绝和进程异常退出后的恢复均通过。最终候选的持续采样另行记录，不以这些修复推断 SWD WDATAERR 已消除。

## 条件和限制

本机 Windows，DOIT ESP32-S3 开发板，16 MiB Flash、8 MiB Octal PSRAM，原生 USB VID/PID 303A:1002；STM32F103C8 通过 GPIO4 SWDIO、GPIO5 SWCLK、GND 接入。请求 SWD 4 MHz，没有提高到 8 MHz，没有测量实际 SWCLK、目标电压或物理 NRST。

12 Mbit/s 是 USB 物理信号速率，标准 Bulk 有效载荷理想上限约 9.728 Mbit/s；不能用 12 Mbit/s 作为应用数据的通过阈值。纯 PSRAM 回传不代表 SWD 能以同样数据速率采集任意指针链。实现、兼容回退与复现方式见 [USB 连续回传](usb-throughput.md)。

发布仅使用标注测试的 Pre-release，不覆盖 0.3.2 已验收资产。第三方库、驱动签名、Windows 启动设置、Wi-Fi 凭据和目标选项字节均不属于本次修改范围。
