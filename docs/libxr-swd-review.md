# LibXR SWD 参考与受控试验

2026-10-16，按用户提供的仓库拉取到 `reference/libxr/`，浅克隆固定为 `5161373dbe6d9d9fb1904d90e0ebd4344a44047a`，Apache-2.0，完整 LICENSE 保留在参考仓库及[参考许可副本](reference-licenses/libxr/LICENSE)，引用记录在 `reference-versions.json`。参考目录及其 CodeGraph 索引不纳入根 Git；上游源码未修改。

群友提到的“1M/s”没有主控、时钟、读写类型或测试截图。当前仓库的调试代码和 README 未提供可复现的对应实测，暂不把它当作 ESP32-S3 的已知性能。若意指1 MB/s的有效SWD连续读取，以每32位至少46线时钟估算，即使忽略WAIT、软件及地址配置，也需要约11.5 MHz SWCLK；4 MHz下理想上界约348 kB/s。它与当前五条指针的离散采样不是相同口径。

## 源码核对

- `src/driver/debug/swd_general_gpio.hpp`：模板GPIO、缓存SWDIO方向、延时／无延时路径分开；无延时读取按字节展开八位，完整字后计算奇偶校验。
- `src/driver/debug/swd.hpp`：WAIT重试、SELECT缓存、AP posted read与RDBUFF辅助入口。RMLink已有相应核心机制，不能额外加入另一套目标连接或GPIO执行任务。
- `driver/esp/esp_gpio.hpp`：ESP普通GPIO HAL。RMLink已使用ESP32-S3专用GPIO，整套替换并不能据源码保证更快。
- LibXR底层非OK响应直接跳过数据；RMLink保留现有CMSIS-DAP的可配置turnaround、WAIT/FAULT data phase、时间戳、Abort和截止语义，不照搬简化分支。

源码链接：[SwdGeneralGPIO](https://github.com/xrobot-org/libxr/blob/5161373dbe6d9d9fb1904d90e0ebd4344a44047a/src/driver/debug/swd_general_gpio.hpp)、[Swd](https://github.com/xrobot-org/libxr/blob/5161373dbe6d9d9fb1904d90e0ebd4344a44047a/src/driver/debug/swd.hpp)。

## 实际试验

只对现有4 MHz专用路径试验字节读取展开与字级奇偶校验；保留原Fast、通用Slow路径，仍通过统一`SWD_Transfer`及唯一CPU0执行器。为本地C重写，吸收上述组织方式，没有引入LibXR框架依赖或复制其平台层。保留ARM文件原始版权，引用来源与许可证记录不省略。

测试条件固定72 MHz F103、请求4 MHz SWD、五条独立三级链＋五个直接字段、200 us请求周期、Watch/RTT共存。先记录0.2.28同机基线，再编译／协议边沿模型、内部RMT时钟与CPUID、同组短测、标准USB回归。只有正确性及实际性能均通过才保留；速度结论不能用提高线时钟替代同条件对照。

三种候选均完成TCP512构建、刷写、RMT／CPUID／完整目标Flash比较及三轮各15秒同组短测。所有短测中的三订阅均正常，无无效数据、ring丢弃或实时消费者缺口。下表的时钟是内部RMT对DP读取事务各周期的中位，不是外部仪器测量，周期并不均匀。

| 版本 | 改动 | 实际采样率中位 | DP周期中位对应时钟 | 决策 |
| --- | --- | ---: | ---: | --- |
| 原版0.2.28，本轮前置对照 | 原有逐位循环 | 1097.123 Hz | 3.810 MHz | 基线 |
| 候选0.2.29 | 字节展开、字级奇偶校验，延时仍7 | 1145.490 Hz | 4.444 MHz | 实际时钟提高，不能算同线速算法收益；不采用 |
| 候选0.2.30 | 全阶段延时加到8 | 1038.967 Hz | 3.810 MHz | 其他阶段也变慢，回退明显；不采用 |
| 候选0.2.31 | 只对展开的32位读数据段补偿到8 | 1090.883 Hz | 4.000 MHz | 未证明胜过原版；不采用 |
| 恢复原版0.2.28，后置对照 | 原始镜像原样恢复 | 1101.023 Hz | 3.810 MHz | 三轮短测和原生／虚拟USB完整回读通过 |

0.2.31比前置基线约低0.57%，不据这个小差值声称存在确定回退；结论是没有证实值得引入的性能收益。时钟补偿会改变不同阶段的耗时分布，不能只比较DP周期中位，也不能只比较主机请求的4000 kHz配置。

**运行固件恢复原有0.2.28，运行源码与试验前逐字节相同。** 从此前已核验的0.2.8开发包恢复相同应用、bootloader、分区表，保留配网NVS。应用SHA256仍为`0fe987f36c91a1203d15246cfbbcb7b6f8c1d3fd5091914a2aa43d914a6c6df4`。没有用候选短测替换原900秒验收，也没有把任何候选宣称为稳定版本。

三份候选补丁保留在[experiments/libxr-candidates](experiments/libxr-candidates/)，相对于试验前`f11494a`的SW_DP.c，仅供复现实验，不参与固件构建；原始候选二进制、配置、刷写身份及日志保留在`artifacts/checkpoints/0.2.29-libxr-uncompensated`、`0.2.30-libxr-allphase-compensation`和`0.2.31-libxr-read-compensation`。

## 保留的成果与验证边界

保留新参考仓库、固定版本和许可、审阅记录、候选补丁，以及针对真实`SW_DP.c`的逐时钟主机模型。`scripts/Test-SwdBits.ps1`直接编译现有内核，比较通用与4 MHz专用路径的输出：24576组读写数据、奇偶校验错误、WAIT/FAULT可选data phase、1–4周期turnaround、idle和timestamp均一致。模型不验证真实纳秒时序；时序由实板RMT单独检查。

原版恢复后重复同组短测及原生／虚拟USB64 KiB完整回读，记录在`libxr-restored-0228.json`、`libxr-restored-0228-clock.json`、`libxr-restored-usb.json`。全部候选试验的未参与运行部分不会留在默认SWD路径中。112项主机测试通过。

原生USB完整64 KiB回读耗时0.413秒，约155 KiB/s；无线虚拟USB单次1.645秒。两者均包含主机、USB/CMSIS-DAP及SWD，不是纯SWD连续有效字节的直接测量，也不用于证明未知条件下的1 MB/s不可能。当前4 MHz约348 kB/s的协议理想上界另行计算，不能与此实测混为一谈。记录与原始文件身份汇总见[libxr-swd-experiment.json](libxr-swd-experiment.json)。

`scripts/verify_swd_4mhz.py`新增45秒等待设备重连、当前72 MHz RCC校验和当前fixture全量比较，避免旧诊断脚本硬编码8 MHz fixture哈希。RMT返回的`delay_iterations`是通用DAP基础配置；候选的局部补偿须结合对应补丁解释。

本次没有验证群友的1 MB/s，也没有复现未知主控／未知线时钟下的连续传输。未来若评估更高SWD时钟或硬件外设输出，应作为独立条件测试；不能将其增益混入当前4 MHz指针采样对照。
