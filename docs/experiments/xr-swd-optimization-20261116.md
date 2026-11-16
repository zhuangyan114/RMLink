# XR-DAP SWD 优化与 H723 实机测试

完成时间：2026-11-17 凌晨；实验起始日期为 2026-11-16。

本实验由用户于 2026-11-16 授权：逐步吸收 XR-DAP 的 SWD 实现，在已通过 Wi-Fi 连接的 RMLink 和 STM32H723VGT6 上实测，每完成一部分进行一次本地 Git 提交。用户允许覆盖测试所需的目标内存和程序，不要求备份、恢复原程序。

本记录区分代码检查、主机测试、构建、代理执行的实板测试和用户手动验收。用户尚未手动验收，因此暂不写入 `docs/fix-log/`。

## 已取得的结果

已把 XR-DAP 的合并边沿输出、换向完成确认、方向状态复用、平衡半周期汇编、启动标定和低速周期调度引入 RMLink。最终固件已通过 Wi-Fi OTA 部署到二号机，版本仍为当前开发版 0.3.4，默认时钟仍为 4 MHz，公开上限仍为 10 MHz。

同一 H723 测试程序的 A/B 中，旧源码基线在 8 MHz 第 5 轮写入 FAULT，最终方案在 4/8/10 MHz 各通过 24 轮 4 KiB 写读。最终 10 MHz 的板内连续读耗时为 9.855 ms，旧基线已通过的 4 MHz 为 17.836 ms；收益来自更高时钟档位通过实测。同频 4 MHz 未取得吞吐收益，保留原通用内核。三档 FAULT 注入与清除、4/10 MHz 各三次完整烧录均通过。

采样完成三档各 30 s 短测及 10 MHz 十分钟并行快照测试。长测逐帧检查 1,198,318 帧，内容错误和 SWD 失败为零，但累计缺口 2217，约 0.185%：调度漏拍 908，探针环形缓冲丢弃 1309。本次通过了内容与 SWD 正确性检查，未达到连续无丢帧，丢弃的上游成因尚未定位。测试程序已覆盖 H723 原 Flash 第一扇区，原应用未备份、未恢复。

## XR-DAP 中吸收了什么

参考目录为 `D:\STM32\XR-DAP`，已按授权建立 CodeGraph 索引。重点对照 XRDAP-ESP 的本地提交 `75a7f2cd56e5c5aed98ebc575c4b168f45fcb070`，源码入口如下。新后端注明 Apache-2.0 和原实现来源，仓库已有同版 `LICENSE`。

| XR-DAP 中的做法与源码入口 | RMLink 的取舍 | 原因与验证 |
| --- | --- | --- |
| `firmware/esp32sx/main/swd_fast.cpp` 的 `WriteBits/ReadBits`：下降沿更新数据，上升沿前采样，利用高半周期移位拼字 | 采用调度方式，改成带掩码的专用 GPIO 写入 | 保留本核其他专用输出；RMT 数字回读核对 8/10 MHz 数据区半周期 |
| `swd_fast.cpp` 的 `XferBody` 和 GPIO 输出使能读回：把换向、ACK、数据和校验分开处理 | 采用换向读回确认，接入原 CMSIS-DAP 状态机 | 保留全部原始 ACK、IdleCycles、时间戳、空指针读取语义；特殊配置回退 |
| `third_party/libxr/src/driver/debug/swd_general_gpio.hpp` 的 `SetSwdioDriveMode/SetSwdioSampleMode`：缓存当前方向 | 用单一执行器的方向状态缓存实现 | 减少冗余 APB 操作，初始化和释放时同步缓存 |
| `firmware/esp32sx/main/swd_engine.hpp` 的 `Calibrate/PacedTransfer`，以及 `swd_fast.cpp` 的 `MeasureHalfX16` | 启动实测档位；低速按 CPU 周期调度边沿 | 当前 1 MHz 用 paced，4 MHz 不满足该路径的标定下限，保留通用内核 |
| 多档展开与 IRAM 常用档 | 只保留 8/10 MHz，32 位数据改用硬件计数循环 | 最终 IRAM 相对展开实验少 14860 字节，相对原始基线仍多 6128 字节 |
| `XferBody/PacedTransfer` 中的 AP 写后空闲 | 做过对照，撤除固定追加策略 | WAIT 减少但总周期和离散读取耗时增加；见第 5 节 |

上表源码路径均相对 `D:\STM32\XR-DAP\XRDAP-ESP`。没有移植整套 libxr，也没有采用其更高时钟范围。RMLink 的传输所有权、烧录恢复、采样缓存和 Wi-Fi 架构继续由原实现负责，本次重点落在位级 SWD。

## 设备与基线

- RMLink：`b81f3fc30470`（二号机），Wi-Fi `192.168.137.72:3241`，512 字节 DAP 包。
- 硬件配置：ESP32-S3，PSRAM，SWDIO GPIO17、SWCLK GPIO18、NRST GPIO6；OTA 配置指纹 `7912ec692d3a985d`。
- 初始固件：0.3.3，ELF SHA256 `5ab3ca9b3e822a365b0a6f4062b3b70201d30d41570c085889c9738b4d9c2f23`。
- 目标已通过 DP、CPUID、DBGMCU 和 Flash 容量核对为 STM32H723VGT6。
- 原始数据目录：`artifacts/diagnostics/xr-swd-20261116/`，不纳入 Git。
- 基准脚本：`scripts/benchmark_xr_swd.py`。暂停目标，覆盖 AXI SRAM `0x24010000` 起的测试区，使用全零、全一、交替位和确定种子的随机数据逐轮写读比较；另测探针内部连续、离散地址读取耗时。
- SWCLK 使用 ESP32-S3 RMT 从现有引脚数字回读，参考时钟 80 MHz，分辨率 12.5 ns；不代表外部示波器测量或模拟信号完整性验收。

## 分阶段结果

### 0. 建立可重复的实机基准

初始固件在请求 1 MHz、4 MHz 时各完成 6 轮 4 KiB SRAM 写读比较，每档 12576 次 SWD 传输无 WAIT、无错误。4 MHz 的单次 SWD 平均耗时约 13.69 µs；1024 字连续读取中位数 17.758 ms，64 字节间隔的离散读取中位数 40.409 ms。

8 MHz 在前三轮写读通过后，第 4 轮写入返回部分完成和 FAULT（`053104`）；脚本记录失败并以 1 MHz 重新连接成功，没有重放失败的写命令，也没有继续提高时钟。此结果限定于旧固件和当前接线，不将 8 MHz 作为已通过的速度。

证据：`artifacts/diagnostics/xr-swd-20261116/00-original-033-r4.json`。此前 `00-original-033*.json` 保留了基准脚本调通时的三种问题：混用增强会话与原始 DAP 被服务拒绝、CPUID 修订号检查过严、1 MHz 离散读取超过已有 500 ms 工作预算。这些属于测试工具调试，未计作固件 SWD 故障。

### 1. 合并输出与换向写入完成确认

写数据与 SWCLK 下降沿合为一条带掩码的专用 GPIO 写入，保留其他专用输出通道。SWDIO 输出使能和禁用后读回 GPIO_ENABLE，等待 APB 写缓冲完成；不重新连接 GPIO 矩阵。

主机引脚模型通过 24576 组协议轨迹；构建通过并通过 Wi-Fi OTA 启动确认。1/4/8/10 MHz 各通过 8 轮 4 KiB 写读，错误计数均为零。8/10 MHz 各记录 96 次 WAIT，由原有协议层处理。10 MHz 的单次传输平均约 6.03 µs，板内 1024 字连续/离散读取中位数为 8.736/19.152 ms。

当前源码的未优化 0.3.4 对照镜像在 4/8 MHz 通过 6 轮，10 MHz 第 2 轮写入 FAULT。相同源码基线与第一阶段的 4 MHz 连续读取分别约 17.828/18.176 ms，约慢 2%；该组合方案在当前接线下通过了更高时钟档位的测试，没有独立分离合并输出和换向读回各自的贡献，不能宣称所有速度都提升。

构建：ESP-IDF 5.1.2，S3 240 MHz，RMLink V1，TCP512，执行器 core0，PSRAM 镜像缓存，IP TOS 160；固件版本 0.3.4。第一阶段应用 951088 字节，ELF SHA256 `33ad6445027398589f87db75f37b2e133b501b55b69bccc176946d30217f88ec`。

证据：`00-source-baseline-r2.json`、`01-gpio.json`、`01-gpio-build.log`、`01-gpio-ota.json`，均位于实验原始数据目录。`00-source-baseline.json` 是 OTA 重连尚未结束时误提前启动的测试，记为设备离线，不计作固件故障。

### 2. 平衡半周期的汇编路径

增加独立 `swd_fast.cpp`，仅展开 8/10 MHz 两档，保留带掩码输出。请求、ACK、数据与校验的时序布局取自 Apache-2.0 的 XRDAP-ESP，未引入 libxr 依赖。非默认 turnaround 或 data phase 继续走原 CMSIS-DAP 内核。时间戳、IdleCycles、全部 ACK 原值和空指针丢弃读取的语义保持一致。

24576 组原有轨迹和新增 32768 组快速/通用状态机对照通过；新增模型替代了汇编位操作，仅验证协议控制，汇编本身在硬件验证。512 字节包的所有权/解析器测试与采样状态机测试通过。

4/8/10 MHz 各完成 16 轮 4 KiB 写读，错误数为零。8 MHz 无 WAIT，10 MHz 记录 192 次 WAIT。RMT 在 10 MHz 的数据区稳定记录高/低各 4 tick（各 50 ns），8 MHz 各约 5 tick（62.5 ns），改善了通用循环的高低不等长；通用 10 MHz 的部分记录只有 42 个符号，因此其频率估计不能视为完整帧测量。

本阶段没有取得吞吐提升：10 MHz 的 1024 字连续/离散读中位数为 10.345/22.156 ms，第一阶段为 8.736/19.152 ms。其收益是数据区半周期一致及更接近请求频率，额外的调度/阶段开销仍需后续处理，不能把端到端 Wi-Fi 波动当作性能收益。

应用 965776 字节，ELF SHA256 `80c4c701ab9d137fed062163df95ef05360b47a9eefbaa61b601392ae7fea3f8`；新增快速函数位于 IRAM `0x40388974`，14636 字节（约 14.3 KiB），仅两档。证据：`02-fast.json`、`02-host-protocol.log`、`02-fast-build-r3.log`、`02-fast-ota.json`、`02-image.json`。

### 3. 启动标定、低速边沿调度与方向状态复用

每次建立专用 GPIO 后，在 SWDIO 释放时测量两档快速读时序，取四次测量中的最小值；另测读写混合时序可按绝对周期调度的最小半周期。完成后恢复主机驱动，主机随后照常执行 SWJ/SWD 初始化。该标定会产生调试时钟，不复位目标 CPU。

在满足启动标定下限的低速范围使用周期计数器调度边沿；普通等待粒度误差不累计，迟到超过 4 CPU 周期后从实际时刻重新计时。此次设备标定的下限为 32～36 CPU 周期，因此 1 MHz 使用 paced，4 MHz 保留通用路径。不能将其描述为“所有低速都改为精确调度”。快速档测得约 7.90/9.85 MHz（包含测量开销），RMT 数据区约 8/10 MHz，后者仅有 12.5 ns 分辨率。

SWDIO 方向状态由唯一执行器持有，初始化/释放同步更新；已经处于所需方向时省掉重复 APB 写和读回。`0x80/16` 新增只读标定诊断，给出请求频率、估计频率、当前传输后端和标定下限，不增加产品界面选项。

24576 组原有轨迹与 131072 组四档模型对照、迟到/回绕检查通过。1/4/8/10 MHz 各通过 16 轮 4 KiB 写读，错误为零；10 MHz 的板内连续/离散 1024 字读取中位数 9.719/22.271 ms。对板内纯读取单独启用计数后，两种地址布局均未观察到 WAIT；混合大块写读在 10 MHz 观察到 192 次 WAIT，需要下一阶段再判断插空闲时钟是否合算。

应用 972064 字节，ELF SHA256 `9915af4907b339f2bf0a75fecd0dd04b5af14d6f12e741d657cea65e89c231d1`。证据：`03-calibrated.json`、`03-calibrated-build.log`、`03-calibrated-ota-r2.json`、`03-host-protocol.log`、`03-network-native.log`、`03-image.json`。

期间检测到另一增强会话占用设备，并已执行 256 KiB 烧录和采样；OTA 首次请求被占用保护拒绝。后续升级在设备释放后成功。此前分阶段数据不能排除目标运行环境变化的影响，因此另用同一测试程序完成基线与最终镜像的 A/B 对照，见第 7 节。

### 4. 固定目标测试程序与完整烧录链路

新增可独立构建的 H723 测试程序，不配置外设或执行机构，使用复位后的 HSI 时钟，不启用 PLL 或 Cache。`0x24000000` 放置标识与递增计数，`0x24000008` 起为十个带字段编号的变量，便于后续验证连续采样值。程序主体 572 字节，用确定数据填充为一个 128 KiB Flash 扇区，避免用全零或全 FF 图像替代真实传输负载。

通过 4 MHz 增强烧录路径擦写并在探针和主机两侧完整比对，131072 字节全部通过；目标复位后运行，读到标识 `0x58525357`。未读取、保存或恢复原目标程序，原第一扇区已被测试程序覆盖。选项字节未变。

测试图像 SHA256 `9583e9447fef2b444a2137eec39f2b236f8157124ab36e54a942e71922d83349`；探针内擦写校验约 3.521 s，含上传、主机再次校验与复位的完整服务调用约 5.922 s。此项是单次流程验证，不是多次烧录性能统计。证据：`04-fixture-flash.json` 与 `artifacts/build/h723-swd-fixture/fixture.json`。

### 5. AP 写后固定空闲时钟：测试后不采用

在固定 H723 测试程序上，以第三阶段无额外空闲为对照，实验性地为每次 AP 写补足 `2 + ceil(实际估计频率 × 350 ns)` 个空闲时钟，扣除主机已经要求的 IdleCycles。计数器同时纳入追加时钟，避免把额外开销藏在线路统计之外。

8/10 MHz 各 16 轮 4 KiB 写读均通过；两档混合负载的 WAIT 都从 192 次变为 0，但累计 CPU 周期增加约 2.6%。10 MHz 连续 1024 字读取中位数从 9.719 ms 增至 9.926 ms，离散读从 21.623 ms 增至 23.387 ms（慢约 8.2%）；8 MHz 离散读慢约 6.3%。纯读取原本就没有 WAIT，增加空闲不能带来收益。

因此移除这段实验代码，最终方案不默认追加 AP 写空闲。结论限于本次 350 ns、全 AP 写策略与当前目标，不推断所有目标或自适应策略都无效。实验补丁与镜像保存在原始数据目录，源码不保留未取得收益的控制选项。

证据：`05-ap-idle-baseline.json`、`05-all-ap-idle.json`、`05-all-ap-idle-experiment.patch`、`05-all-ap-build.log`。实验镜像 ELF SHA256 为 `e33dfd73249c860426e57ecdfbe6dbe589b8dcf24ef3628ac68a3ba77b87c266`。

该次 OTA 客户端等待超时，设备 IP/TCP 仍可访问；直接读取 OTA 状态确认新 ELF 已启动且 boot_state=VALID。重启本机连接服务、刷新发现并重新连接后恢复；该现象记录为服务重连问题，未据此认定固件崩溃或回滚。证据：`05-all-ap-ota.json`、`05-all-ap-boot-confirmed.json`。

### 6. 精简常用汇编档，保留原默认 4 MHz

8/10 MHz 数据区使用 Xtensa 硬件计数循环，将移位与拼字安排到高半周期；请求和短阶段仍展开。此文件关闭编译器的计数循环优化，反汇编核对显式循环位置，避免与内联汇编占用的循环状态嵌套。仅 S3 的 SWD 配置编译实际内核，其他配置编译空后端。

曾增加并实测 4 MHz 汇编档，1/4/8/10 MHz 各 24 轮通过，但 4 MHz 板内连续读约 19.55 ms，比原通用路径更慢，因此撤下该档。最终默认 4 MHz 保留通用内核，且用一个快速状态标志跳过不需要的 C++ 调用。8/10 MHz 保留紧凑内核，低速在标定范围内使用 paced。

最终镜像的 4/8/10 MHz 各通过 24 轮 4 KiB 写读；计数器错误为零，混合写读出现少量 WAIT 并由原协议层处理。一次 3 s、10 字段、500 µs 周期并行 20 ms 快照的采样冒烟测试通过，6161 帧内容均有效、SWD 失败为零；存在 7 次采样序号跳跃，与 deadline_missed 对应，不能称为零跳拍。

最终应用 957200 字节，SHA256 `8702013dbe07e9e2ebd0cbdb3972136099af17180fdd450731d5e417e453979e`，ELF SHA256 `bc470abf644199b4c9dc31e53129953dedc664a5758515797b7e4e7c42d61985`。IRAM 加载段从第三阶段的 150284 字节降为 135424 字节（减少 14860 字节）；相对原始源码基线 129296 字节净增 6128 字节。不能将相对展开方案的节省说成比原固件更省内存。

24576 组原有引脚轨迹和 131072 组新内核协议模型通过；251 项主机单元测试通过。首次主机测试因 PYTHONPATH 缺少 scripts 导致 5 项导入错误，补齐路径后重跑通过，未修改无关测试。S3 JTAG 配置的新空后端编译通过，但完整固件仍在链接时缺少 `rmlink_tcp_session_open` 等执行器符号；原基线的执行器也受 SWD 条件编译限制，本次未扩展 JTAG 功能，不能宣称 JTAG 完整构建通过。

证据：`06-compact.json`（含未保留的 4 MHz 档）、`06-compact-final.json`、`06-compact-build-r4.log`、`06-compact-final-ota.json`、`06-final-image.json`、`image-comparison.json`、`06-host-protocol.log`、`host-suite-r2.log`、`jtag-build.log`、`07-capture-smoke.json`。

### 7. 同一目标程序的最终 A/B

固定 H723 测试程序、接线与构建配置，先 OTA 回未优化的 0.3.4 源码基线 A，确认启动后测试；再 OTA 最终固件 B，确认启动后用同一命令测试。A 的 ELF SHA256 为 `b3ac38f62ad2d8784d9d43cf128a71fb5fd297a8f508a4d990bab6850ccfa2d8`，B 为第 6 节最终镜像。基线源码对应本分支起点 `756b53e`。各档计划 24 轮，每轮写入并完整比对 4 KiB；板内连续和离散 1024 字读各取 3 次中位数，计时不含 Wi-Fi 往返。

| 固件与请求时钟 | 4 KiB 写读 | 连续读中位数 | 离散读中位数 |
| --- | --- | --- | --- |
| A，4 MHz | 24/24 轮通过 | 17.836 ms | 40.773 ms |
| A，8 MHz | 第 5 轮写入 FAULT，已完成 4 轮 | 未执行 | 未执行 |
| B，4 MHz | 24/24 轮通过 | 17.998 ms | 41.474 ms |
| B，8 MHz | 24/24 轮通过 | 11.863 ms | 26.014 ms |
| B，10 MHz | 24/24 轮通过 | 9.855 ms | 22.171 ms |

A 在 8 MHz 返回 `052a04`，随后以 1 MHz 重连成功；没有重放失败写入，也没有继续测 A 的 10 MHz。B 的三档分别记录 50592 次传输、288 次 WAIT 和零错误。WAIT 是已完成处理的协议响应，不等同于传输失败。

同频 4 MHz 的最终连续/离散读分别慢约 0.9%/1.7%，本次不能证明同频吞吐提升。B 的 10 MHz 相对 A 已通过的 4 MHz，连续/离散吞吐约为 1.81/1.84 倍；这是可用时钟提高后的结果，不能写成“算法同频加速 1.8 倍”。4 KiB 主机调用还受 Wi-Fi 和服务调度影响，不据其波动推断纯 SWD 收益。24 轮通过也不代表任意接线或目标都保证 10 MHz。

证据：`07-ab-A.json`、`07-ab-A-ota.json`、`07-ab-B.json`、`07-ab-B-ota.json`。

### 8. 实际 FAULT 清除与恢复

新增 `scripts/verify_xr_swd_faults.py`。在固定测试程序运行时，分别以 4/8/10 MHz 读取未映射地址 `0xfffffffc`；三档均实际返回 ACK=FAULT，DP CTRL/STAT 的 STICKYERR 置位。写 ABORT 清除、恢复 SELECT/CSW 后，三档均读回正确的 DP ID `0x6ba02477` 和测试标识 `0x58525357`，STICKYERR 清零，目标仍在运行。

这是只读总线错误注入，没有重放应用内存写入。它验证错误响应和清除后的后续读取，未覆盖每种电气故障、掉电或所有自动恢复分支。硬件上的 WAIT 已在混合写读中出现；全部 ACK 原值、校验错误、特殊 turnaround/data phase 的控制路径由主机模型另外覆盖。

证据：`07-faults-r2.json`。

### 9. 整扇区烧录的多轮验证

新增 `scripts/verify_xr_swd_flash.py`，在最终固件上以 4 MHz 和 10 MHz 各执行三次 128 KiB 擦写。每次都通过探针全量校验和主机独立回读比对，随后复位运行并读回测试标识。六次均没有 RAM 重试或降频，实际保留请求档位，选项字节未变。

| 最终固件的请求时钟 | 探针内擦写校验中位数 | 其中编程 | 其中完整校验 | 含上传、主机校验与复位的调用中位数 |
| --- | --- | --- | --- | --- |
| 4 MHz，3/3 次通过 | 3.507 s | 1.819 s | 0.726 s | 6.067 s |
| 10 MHz，3/3 次通过 | 2.884 s | 1.516 s | 0.403 s | 5.070 s |

此处比较的是同一最终固件的两个时钟档位。探针内总耗时减少约 17.8%，端到端调用中位数减少约 16.4%；Flash 擦除仍约 0.93 s，不随 SWD 时钟成比例缩短。主机总耗时范围为 4 MHz 的 6.067～6.600 s、10 MHz 的 5.062～5.102 s，样本量各三次，不能外推为所有固件尺寸的固定比例收益。

证据：`07-flash4.json`、`07-flash10.json`；测试图像摘要见第 4 节。

### 10. 连续采样与并行快照

新增 `scripts/verify_xr_swd_capture.py`。固定测试程序持续更新十个带字段编号的变量；采样订阅每 500 µs 读取十字段，同时另开每 20 ms 读取两字段的快照订阅。逐帧核对有效位图、错误标志和各字段高字节编号，并确认低 24 位计数持续变化。两订阅共用真实 Wi-Fi 流，但下表帧数与耗时只统计十字段订阅。

| 请求时钟，各 30 s | 验证帧数 | 实际帧率 | 内容错误 / SWD 失败 | 已交付序号跳跃 / consumer_gap | 采集跨度 p50 / p99 |
| --- | --- | --- | --- | --- | --- |
| 4 MHz | 60067 | 1998.80 Hz | 0 / 0 | 36 / 0 | 210 / 420 µs |
| 8 MHz | 60106 | 1998.70 Hz | 0 / 0 | 39 / 0 | 138 / 335 µs |
| 10 MHz | 60172 | 1998.70 Hz | 0 / 0 | 39 / 0 | 121 / 313 µs |

快照订阅三档也均保持运行、SWD 失败为零。主采样的探针 `deadline_missed` 分别为 36、40、39，`budget_overrun` 为 71、29、24；两个订阅的 `ring_dropped` 都为零。状态读取发生在取流窗口之后，故 8 MHz 的 deadline_missed 可比已交付序号跳跃多一次。采集跨度不是完整的主机显示延迟，也不证明十字段在同一 CPU 指令时刻采到；测试程序无 Cache，未覆盖带 D-Cache 应用的一致性。

这些结果说明采样内容与会话能正常持续，不能称为严格零跳拍的 2 kHz。`host_history_evicted`（兼容字段名 `host_dropped`）是服务淘汰有界历史记录的累计值；本次实时游标的 `consumer_gap=0`，不能把历史淘汰直接当成实时网络丢帧。数字序号和状态也不足以定位每次跳拍的中断或 Wi-Fi 原因。

证据：`07-capture-matrix.json`、同名 `.progress.jsonl`。

### 11. 十分钟连续测试：内容正确，仍有丢帧

2026-11-16 23:52 至 11-17 00:02，在最终固件 10 MHz 下，十字段每 500 µs 采样并行两字段每 20 ms 快照，持续 600 s 后正常停止。目标全程保持运行，未发生连接中断、SWD 失败或订阅异常停止。

| 指标 | 十分钟实测 |
| --- | --- |
| 逐帧检查的主采样 | 1,198,318 帧，内容错误 0，计数变化 1,198,317 次 |
| 按主采样时间戳计算的有效帧率 | 1996.31 Hz |
| 已交付序号缺口 | 2217，约占预计序号总数 0.185% |
| 主采样探针计数 | deadline_missed=908，ring_dropped=1309，failures=0，budget_overrun=340 |
| 快照探针计数 | deadline_missed=0，ring_dropped=0，failures=0 |
| 主机实时游标 | consumer_gap=0，stream_error 为空 |
| 末 65536 帧采集跨度 p50/p95/p99/max | 121/161/308/775 µs |
| 末 65536 帧帧间隔 p50/p99/max | 500/651/1512 µs |

缺口总数与 `908 + 1309 = 2217` 一致。其中环形缓冲丢弃首次出现在约第 510 s，约第 570 s 的十秒观察区间新增 1026 帧丢弃；服务的最大取流间隔为 209.2 ms。它们说明丢帧已在探针缓冲环节计数，但现有证据不足以确定为何缓冲未及时排空，不能直接归因于射频丢包、Wi-Fi 重传或 SWD 内核。未通过扩大 PSRAM 采样环来掩盖此问题，原小容量内部 RAM 采样环保持不变。

采样脚本 JSON 中的 `passed=true` 表示内容有效、数值变化且 SWD/订阅未失败；零缺口不在该字段的判定条件内，必须结合上述交付和缓冲计数看待。此次长测应记为“内容正确性通过、无丢帧条件未通过”。中途进度更新所述的零环形缓冲丢弃，仅说明截至当时累计计数仍为零，不能代表全程。

脚本检查了全部交付的主采样帧，但只保存前 8 帧示例、十秒进度和汇总；没有归档全部百万帧原始值。跨度与帧间隔分位数采用最后 65536 帧的有界窗口，不能作为全程最大值。内容编号验证能发现位错误或字段错位，但不证明多字段原子同步，也不能测量主机显示延迟。

证据：`08-capture-soak.json`、`08-capture-soak.progress.jsonl`。长测没有覆盖整夜运行，不据十分钟数据作更长时间可靠性保证。

### 12. 最终低速检查与交付状态

用最终镜像补测 1 MHz：8 轮 4 KiB 写读通过，16768 次 SWD 传输、零 WAIT、零错误。只读诊断确认 backend=1（paced）、半周期 120 个 CPU 周期、估计 1 MHz；板内连续/离散读取中位数 65.092/154.382 ms。这是最终低速路径的可用性检查，没有同程序低速 A/B，不作低速加速结论。

2026-11-17 00:04 完成交付核对：固件 ELF 与第 6 节相同，OTA boot_state=2（VALID），Wi-Fi 连接正常，设备所有者为空。H723 测试程序处于 Running，两次读取计数发生变化；测试结束时重新请求 4 MHz，诊断确认使用通用后端。未留下测试订阅或占用会话。最初一次临时收尾检查在未建立 SWD 上下文时读取 vendor 命令被拒绝，补齐原始 DAP 初始化后核对通过；这不属于 SRAM/Flash 或 SWD 传输失败。

证据：`08-final-1mhz.json`、`09-final-state.json`。用户下一次使用前需要重新烧入自己的 H723 应用，当前第一扇区保留的是独立测试程序。

## 软件检查和覆盖边界

| 检查 | 结果 | 证据文件，均位于实验原始数据目录 |
| --- | --- | --- |
| 原有 C 引脚轨迹模型 | 24576 组通过 | `06-host-protocol.log` |
| 新后端 C++ 协议模型 | 131072 组通过，含 ACK、校验、时间戳、空读取、回退、周期迟到/回绕 | `06-host-protocol.log` |
| Core，包长 64/512 | 每档 20000 组解析器用例及所有权、边界检查通过 | `core64.log`、`core512.log` |
| 原生采样状态机、网络测试 | 通过 | `capture-native.log`、`03-network-native.log` |
| 主机单元测试 | 正确设置 PYTHONPATH 后 251 项通过 | `host-suite-r2.log` |
| ESP32-S3 SWD 最终配置 | 编译、链接、OTA、启动确认通过 | `06-compact-build-r4.log`、`06-compact-final-ota.json`、`07-ab-B-ota.json` |
| S3 JTAG 附加构建 | 空后端编译通过，完整固件链接失败；未宣称支持完成 | `jtag-build.log` |

协议模型以可移植引脚操作替代 Xtensa 汇编，验证状态机和输出语义，不证明实际指令周期或电气时序。真实汇编另由 H723 写读、RMT、烧录和采样验证。构建检查时尝试过 GCC 不支持的汇编循环寄存器 clobber 与 `-mno-loops`，最终采用单文件 `-fno-branch-count-reg`，并检查反汇编中的显式计数循环。未将失败尝试算作通过结果。

实板覆盖限定为这一台 ESP32-S3、当前接线、H723 测试程序、Wi-Fi 512 字节 DAP 包。尚未完成 USB 物理链路、其他探针或 MCU、真实应用带 Cache/复杂中断负载、外部示波器模拟信号、物理 NRST 引脚、Orbit/VS Code 界面以及用户手动验收。CPU 周期调度遇到中断会延长半周期，快速档标定也是测量估计，不是晶振级频率保证。JTAG 链接问题不在此次 SWD 工作范围内。

## 文件与复现

主要实现位于 `firmware/components/debug_probe/` 下的 `swd_fast.cpp`、`swd_fast.h`、`debug_gpio.c/.h`、`DAP/Source/SW_DP.c`、`DAP/Config/DAP_config.h` 和组件 `CMakeLists.txt`。只读诊断声明位于该组件的 `include/rmlink_swd_timing.h`，命令分发位于 `firmware/main/rmlink_network.c`。非默认 turnaround/data phase 等特殊配置继续走原协议内核；没有将未受益的实验开关留给产品用户。

诊断 `0x80/16` 的 schema=1，返回七个小端 uint32：请求频率、估计频率、backend、半周期 CPU 周期数、paced 最小半周期、8 MHz 档半周期×16、10 MHz 档半周期×16。backend 为 0/1/2/3，分别表示通用、paced、快速 8 MHz、快速 10 MHz；通用后端的估计频率返回 0，不能解读为实际 SWCLK 为零。原始 DAP 调用须先建立 SWD 上下文，不能将增强命令与原始交换混在同一会话。

以下命令在仓库根目录 PowerShell 执行。基准覆盖 `0x24010000` 起的 AXI SRAM 并暂停目标；整扇区脚本覆盖 Flash 第一扇区；采样与故障脚本要求测试程序已经运行。`--allow-target-write` 是复用脚本时对覆盖范围的显式确认，本次已有用户授权。每次使用新的输出文件名，脚本拒绝覆盖旧证据。

```powershell
# 软件检查
./scripts/Test-SwdBits.ps1
./scripts/Test-Core.ps1 -PacketSize 64
./scripts/Test-Core.ps1 -PacketSize 512
./scripts/Test-Capture.ps1
python scripts/test_network_native.py
$env:PYTHONPATH = "$PWD\host;$PWD\scripts"
python -m unittest discover -s tests/host -q

# 构建与本次完全相同的配置（输出路径的 xr-swd 是本地实验标签）
./scripts/Build-Firmware.ps1 -TargetProfile -BoardProfile rmlink-v1 `
  -TcpPacketSize 512 -ImageCachePsram -StreamIpTos 160 -BuildTag xr-swd
./scripts/Build-H723SwdFixture.ps1

# 结束其他调试会话后，Wi-Fi OTA；命令须完成 boot_confirmed 再继续
$probeImage = 'artifacts/build/firmware-target-rmlink-v1-tcp512-core0-psram-video-xr-swd/rmlink.bin'
& "$env:USERPROFILE\.rmlink\app\bin\rmlink.exe" firmware update $probeImage `
  --device b81f3fc30470 --yes --json
if ($LASTEXITCODE -ne 0) { throw 'OTA 未确认完成，先检查设备状态' }

# 首次安装独立 H723 测试程序：擦写第一扇区、双重比对、复位运行
@'
from pathlib import Path
from rmlink_connector.client import ApiClient
with ApiClient().open('b81f3fc30470') as c:
    c.target('connect', device='STM32H723VG', clock_hz=4000000)
    print(c.flash_image(Path('artifacts/build/h723-swd-fixture/fixture-128k.bin').read_bytes(),
                        verify_on_host=True, reset_after=True))
'@ | python -

# 固定测试程序就绪后的验证；不要与其他调试会话同时运行
python scripts/benchmark_xr_swd.py --allow-target-write `
  --clocks 4000000 8000000 10000000 --rounds 24 --label reproduction `
  --output artifacts/diagnostics/xr-swd-reproduction/benchmark.json
# 上一基准会留下暂停态；故障脚本在注入前通过 DHCSR 恢复运行
python scripts/verify_xr_swd_faults.py `
  --output artifacts/diagnostics/xr-swd-reproduction/faults.json
python scripts/verify_xr_swd_flash.py --allow-target-write --clock 10000000 `
  --output artifacts/diagnostics/xr-swd-reproduction/flash.json
python scripts/verify_xr_swd_capture.py --clocks 10000000 --seconds 600 --coexist `
  --output artifacts/diagnostics/xr-swd-reproduction/capture.json
```

上述 `flash_image` 使用目标包默认 Flash 起址 `0x08000000`；`verify_xr_swd_flash.py` 的标识检查用于防止误把普通应用当成此测试程序，不能用于首次安装。烧录脚本以复位运行为结束态，采样脚本启动前也会恢复运行。原始记录保存在 `artifacts/`，源码、脚本、测试和报告按阶段本地提交，不推送远端。新构建的镜像应重新记录摘要；第 6 节摘要唯一标识本次实际部署并测试的镜像，不能仅凭版本号认为两次构建相同。

## 本地提交与证据保留

分支为 `codex/xr-swd-optimization`，起点 `756b53e`。已按完成部分提交：

| 提交 | 完成部分 |
| --- | --- |
| `ffb0a33` | 可重复 SWD 基准与初始结果 |
| `2fa6c71` | 合并 GPIO 输出与换向完成确认 |
| `831ba5e` | 两档平衡半周期路径 |
| `8d510a0` | 启动标定与低速调度 |
| `2e213e6` | H723 独立测试程序与首次完整烧录 |
| `bfef1d0` | AP 写空闲对照与无收益策略撤除 |
| `94e89c8` | 紧凑汇编与默认通用时序 |
| `59b8f5c` | 同程序 A/B、FAULT 清除、多轮烧录及短采样脚本 |

本报告的最终提交可用 `git log --oneline 756b53e..HEAD` 查看。参考仓库、构建镜像、日志和设备数据均未纳入 Git，也未添加 submodule/gitlink。原始证据目录中的 `evidence-manifest.json` 记录文件大小和 SHA256，便于核对本地留存内容。文档读者审阅只验证表述与复现步骤是否自洽，不代替源码检查或用户验收；提出的首次 OTA/安装步骤和运行状态衔接问题已经补齐。
