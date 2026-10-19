# USB 连续回传

## 目标与计量

USB Full Speed 的 12 Mbit/s 是物理信号速率。64 字节 Bulk 包每个 1 ms 帧最多约 19 个，对应有效载荷上限 1,216,000 B/s（9.728 Mbit/s），还未计入主机调度、位填充等影响。依据见 [Microchip USB 帧说明](https://developerhelp.microchip.com/xwiki/bin/view/applications/usb/how-it-works/frame/)。纯回传测速、真实 SWD 采集、控制延迟分别记录；不能把纯回传带宽换算成实际目标采样率。

0.3.3 的开发板实测约 1.126 MB/s、9.01 Mbit/s，有效载荷约达到上述理想上限的 92.6%。三轮各 8 MiB 校验通过；最终镜像、持续采集和恢复结果见 [0.3.3 测试记录](test-release-0.3.3.md)。

## 原因与改造

旧 USB_PULL 每片最多带 52 字节记录，一个 96 字节采样帧需两次请求／响应，各片还经过主机线程、目标锁和执行器。实测原始 DAP 流水请求约 273 KiB/s，100 Hz 的旧稳定性记录只是测试条件。持续采集没有独立推流通道，不能由该记录推断 USB 带宽只有 100 Hz。

新通道复用同一个 WinUSB 句柄和目标 owner：

1. alternate 0 保留原来的 OUT03、IN83 CMSIS-DAP 端点，第三方调试软件使用该配置。
2. RMLink 主机显式选择 alternate 1，增加独立 Bulk IN。普通配置使用 IN84；MSC 开启且 CDC 关闭时使用 IN81。
3. 采样仍由既有固定核心 SWD 执行器完成。Timeline 保留 2 MiB PSRAM 环形缓冲；USB 任务只搬运完成的记录，绝不访问 SWD。
4. 两个内部 RAM 的 4096 字节暂存区保持 USB/DMA 数据有效。每次硬件提交最多 64 个物理包，主机每次读取最多 8192 字节，解码任意分片与合并记录。
5. 控制和数据使用不同端点，USB 连续流不占目标控制锁。控制优先级、单一目标所有权、订阅租约和录制 CRC 保持统一。

调试中确认的两项硬件／驱动限制：

- 本板 GHWCFG3=`0x00c804b5`，包计数器为 7 位。8192 / 64 = 128 会被截断；不能把软件缓冲长度直接当成一次硬件传输长度。TinyUSB 上游也有 [同类计数限制报告](https://github.com/hathach/tinyusb/issues/3825)。
- 当前 DWC2 驱动按端点号选择 TX FIFO。ESP32-S3 的可用 IN FIFO 为 0–4，因此不使用 IN85。CDC、DAP、MSC 全部启用时已占满可用 IN FIFO，该组合保留 USB_PULL，不宣告新通道能力。

当前 TinyUSB 的单端点 close 在此平台为无操作。配置时只分配一次 FIFO，alternate 切换使用 DWC2 的分配／激活分离 API；该 API 的名称带 iso，但其激活实现接受 Bulk 描述符。反复 open 会重复分配并耗尽 FIFO。相关代码随本仓库源码维护，没有直接修改 managed_components 中的第三方库。

## 协议

采集能力 `0x81/0` 的 feature bit `1024` 表示支持独立 USB 连续流。旧固件或端点预算不足时主机继续使用 USB_PULL，不自动改变 SWD 时钟。

EP0 vendor request `bRequest=0x22`：

| wValue | 方向／长度 | 行为 |
|---|---|---|
| 0 | OUT，0 字节 | 停止连续流，采集流通知现有订阅状态机 |
| 1 | OUT，16 字节 | 消费 `0x81/11` 在同一 owner 下签发的一次性 ticket，绑定采集记录流 |
| 2 | IN，8 字节 | PSRAM 纯回传测速；wIndex=1..128 MiB，返回总长度和 64 KiB 模板周期 |
| 3 | IN，32 字节 | 八个 LE u32：版本 1、模式、代次、alternate、累计提交字节、完成字节、错误码、完成块数 |
| 4 | IN，32 字节 | 仅 ESP32-S3 诊断构建开放的固定 USB 寄存器快照，不读取目标内存 |

模式为 0=停止、1=采集、2=测速。错误码为 1=提交失败、2=完成超时、3=USB 完成结果或长度不匹配。统计为本次设备启动后的累计值，不是物理链路利用率。

采集载荷沿用 RML1：板端时间戳、序号、有效位与 CRC，支持跨 USB 读取拼接。整 64 字节结尾的采集批次补 ZLP，保证低流量和暂停时主机能够结束当前读取。完成回调确认成功与长度后才重用暂存区；主机阻塞最长 5 秒，超时结束流并暂停订阅。

测速从 PSRAM 的不可变 64 KiB 模板复制数据，内部暂存区每 4096 字节块头写入绝对块序号。主机校验每个字节和该序号，避免整页重复掩盖错序。测速不访问 STM32，PSRAM 不可用时不能执行此项测速。

主机取消时先结束设备推流、AbortPipe，等待同步读取线程退出，再恢复 alternate 0 和关闭句柄。打开流期间取消也必须等 EP0 操作结束后清理。空闲读取超时检查设备状态；设备已经停止时将错误传给录制，不能无限显示等待。

## 只读 SWD 错误恢复

长测另外暴露了 SWD 读访问 FAULT。读取 DP CTRL/STAT 后确认 WDATAERR，说明写数据阶段校验／帧错误或已接收写入被丢弃；这个标志本身不能证明具体接线或软件时序原因。

只读采集现在只对单独的 WDATAERR 做一次有界恢复：在原 SWD 执行器和 owner 内，以总计 500 µs 为上限读取状态并向 DP ABORT 写 WDERRCLR；随后让出执行器，下一轮重新设置 SELECT、CSW、TAR，重读失败的片段。部分失败回复不会合并进有效样本，仍保持每轮最多 8 个目标数据读取。每帧／RTT 只读阶段最多重试一次，重复故障、非法地址 STICKYERR、显式 Abort 等仍停止并保留错误。

RTT 的 RdOff 实际写入失败可能已经生效，因此不自动清除并重放该写入。保留未知提交状态，明确恢复时先重读描述符核对，避免重复消费。恢复行为不等价于验证了布线和更高 SWD 时钟。

`0x80/13` 返回 32 字节：三字节成功前缀、版本 1，以及 LE `attempts32, recovered32, exhausted32, write_faults_not_replayed32, last_dp_ctrl_stat32, last_fault_uptime_us64`。公共健康接口以可选字段 `read_fault_recovery` 暴露；旧固件返回 `supported=false`。原始 I/O 失败记录仍保留，成功恢复也不会抹掉传输异常。

## 复现

普通用户更新匹配的 0.3.3 客户端和固件后，选择 USB 传输，原有 Timeline／Watch／RTT API 自动采用新通道。

开发者在设备空闲且未挂载虚拟 USB 时运行：

```powershell
$env:PYTHONPATH = "$PWD\host;$PWD\scripts"
python scripts/benchmark_usb_stream.py --mib 8 --rounds 3 --output artifacts/tests/usb-bulk.json
```

真实缓冲恢复测试要求先备份并安装本仓库 F103 测试固件，完成后恢复原程序：

```powershell
python scripts/verify_usb_backpressure.py --fixture-confirmed --output artifacts/tests/usb-pause.json
python scripts/verify_read_fault_recovery.py --fixture-confirmed --output artifacts/tests/usb-read-recovery.json
python scripts/verify_subscription_soak.py --transport usb --seconds 900 --pointer-depth 3 --independent --direct-fields 5 --period-us 1000 --output artifacts/tests/usb-soak.json
```

错误恢复脚本只在 DP SELECT 寄存器写入中故意使用错误校验位，确认标志后验证自动恢复，不对应用数据或 Flash 注入错误。实板 10 次采集、1 次内存读、1 次核心状态读共 12 次恢复通过，Flash 和选项字节未变。

这些脚本不更改物理 SWD 接线；测试条件仍是请求 4 MHz、未仪表测量 SWCLK，未使用或验收物理 NRST。更高 SWD 频率等用户自制 PCB 完成后另行验收。
