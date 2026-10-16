# 真实 Orbit Timeline 与 STM32 烧录验收（2026-10-16）

## 问题与范围

用户反馈实际 VS Code 中波形断续、拖动或显示卡顿。本次授权包括修改、ESP32 刷写、实际 Orbit 界面和 STM32 烧录。代码位于本仓库与 `artifacts/worktrees/orbit-rmlink` 的 `rmlink/subscriptions-20261014` 分支；未修改 Orbit 主 checkout、用户 STM32 应用源文件或 `docs/bug-fix-log.md`，未提交 Git。

以下资料与原始证据集中于 `artifacts/logs/orbit-timeline-20261016/`。历史失败保留，不用短测替代持续运行，也不把采样时钟、有效采样率或 UI 标签显示的 Hz 混为一谈。

## 后续无线到达与实时显示优化：0.2.40 / 服务0.2.14

下文早期四变量及十变量的稳定窗口不能代表后续用户报告的停顿。本次进一步区分探针采样、ESP发送阻塞、电脑读包、扩展通知、范围查询、动画帧等待和Canvas绘图。最终固件仍请求SWD 4 MHz，未修改用户STM32程序；当前应用主频未重新量测，不能套用历史72 MHz fixture结论。

### 来源定位与失败试验

- 早期十变量旧流约239 kB/s，实机出现ESP send阻塞数秒、收包3秒长尾；这不是仅凭低Canvas耗时推断。后续203秒诊断轮源时间最大间隔约1.84 ms、ring_dropped=0，但到达仍可间隔455 ms。独立TCP/IP线程回调通常几十微秒，发送缓冲32 KiB用尽；该轮没有重传重试，不能把全部停顿归为丢包。
- 将Wi-Fi任务移到CPU1后发生2.67秒发送等待和SWD失效，已恢复CPU0。关闭AMPDU后发送最长8.714秒、出现重传/RTO增大，已恢复开启。Windows WLAN media-streaming临时请求也出现约3.28秒到达间隔和溢出，已在finally释放该请求，没有遗留系统策略改动。证据分别为`wifi1-three-failures-dap.log`、`noampdu-live.log`、`windows-streaming-trial.json`。
- 电脑5 GHz上网、2.4 GHz热点并行是可能影响因素；用户没有网线，未做改变上网链路的受控对照，因此不将其断言为唯一根因。

### 保留的改动与实测

固件0.2.40新增协商式RMS3无损编码，使用逐条XOR及零游程，保留原CRC、序号、时间戳和全部值；组间独立解码。服务在本机还原为原有RML1，各SDK格式不变，旧设备保留RMS2/RMS1回退。动态十字段初测发送量为原帧的20.9%，非QoS试验仍有停顿，不能单靠压缩宣称修复。

当前保留数据socket IP_TOS=160（WMM视频类，AMPDU开启），控制socket不改。此项是显式profile选项，默认仍0；构建/刷写脚本会核对配置并拒绝拼错的未知参数。官方映射见[ESP-IDF 5.1.2 Wi-Fi QoS](https://docs.espressif.com/projects/esp-idf/zh_CN/v5.1.2/esp32s3/api-guides/wifi.html)。二进制SHA256 `0B4E38D317C0DB5108523EB950733C39744C86CFF17863672F978885523D8414`，镜像身份见`qos-flash-receipt.json`。

真实Orbit十字段（3角度、abb、加速度3轴、陀螺仪3轴）请求200 us、DAP发送15 ms，正常展开Watch。会话`747021d2-bce5-4ab9-920b-c57b3d49d943`连续526.234秒主机窗口，每通道1,820,635点，首尾探针跨度526.009820秒，有效3461.22 Hz，最大相邻间隔1.926 ms，只有初始段，0处>10 ms；ring_dropped=0、failures=0、stream_error=null。期限跳过仍有810,127次，不是精确5 kHz。六轴当时有实际变化，角度仍处于标定。见`final-cadence-run.json`、`final-cadence-health.json`。ESP关闭摘要发送38,435,765 B / 原174,969,696 B（约22.0%），发送调用最长1.172 ms；电脑到Orbit到达仍有约100–300 ms长尾（该被动观察窗口最大333.511 ms）。本次未发生秒级发送阻塞，不能保证任意无线环境都如此。

界面先改为现有动画帧内直接绘图，变量数值DOM每100 ms刷新，范围调度保留16 ms；33 ms试验降低实际更新频率，已撤回。后续为实时页直接推送按当前显示分辨率整理的新数据，避免每批再做一次范围查询：按generation/resolution验证后，数据和覆盖范围在同一动画帧接受；大于8192显示点整批回退查询，隐藏页不推送，历史和缩放仍读取完整记录。峰谷及真实分段保留，原始CSV不减采样、不插值。初测每10秒新数据绘制由约170–210次增至约270–300次，但该初测传感器报错、六轴为常量，属于不同显示负载，不能作为动态场景的严格提速对照。后续动态轮部分窗口为约180–230次，动画帧等待仍有长尾；不能用Canvas耗时或该数值替代网络到达频率、用户流畅感。某些窗口仍有100–300 ms停顿，属于剩余限制。

同一固件实际F5两轮完成55,296 B/54页烧录和板端完整比较；主机经同一DAP owner独立回读SHA256一致，读回7399.31 ms，暂停/继续一轮224.89 ms。见`qos-readback.json`、`qos-control.json`；持续统计封存后才执行这些控制。一次后续传感器恢复试验出现NO_ACK并结束会话，见`live-tail-uart.log`，不掩盖此失败；传感器常量轮不冒充动态六轴验收。

复现固件命令：

```powershell
.\scripts\Build-Firmware.ps1 -TargetProfile -TcpPacketSize 512 -ExecutorCore 0 -ImageCachePsram -StreamDiagnostics -StreamIpTos 160
.\scripts\Flash-Firmware.ps1 -Port COM13 -TargetProfile -TargetWiringConfirmed -TcpPacketSize 512 -ExecutorCore 0 -ImageCachePsram -StreamDiagnostics -StreamIpTos 160
```

构建通过不替代实板：RMS3实际C编码与独立Python oracle、任意TCP分片、CRC/长度负向、RTT/混合组和协商回退均有主机覆盖。0.2.40已构建/刷入，当前硬件不是物理原生USB全套新验收；虚拟USB仍由连接服务管理。复位相关的后续修复与验收记录于文末。

## 测试条件与所有权

- ESP32-S3，设备 ID `50787d15a924`，GPIO4/5 为 SWDIO/SWCLK，PC 2.4 GHz 热点，Wi-Fi TCP；原生 USB/CH343 同时接入。物理 NRST 仍缺接线。
- STM32F103C8，运行用户 `f103_mpu6050_src` 的 MPU6050 应用。前段 ELF 的镜像为 54,272 B，后段当前 ELF 为 55,296 B；分别保留哈希，不把不同镜像的烧录耗时作等条件加速比较。
- SWD 请求 4 MHz。本次未新测物理 SWCLK；目标主频另由实际读取结果记录，不能仅沿用旧 fixture 的 72 MHz 结论。
- 初始 Timeline 为 `g_imu_demo.rpy_deg[0..2]`，请求周期 200 us，即 5000 Hz。后段实际界面已有用户新增的 `abb`，因此后段为四通道；保留该设置，不偷偷恢复为三通道。
- Watch 包含 `g_imu_demo`、`hi2c1`，保留结构体和数组展开能力；压力段额外每 500 ms 通过同一 DAP 的 dataSample 读取两项与子项，单个请求未完成时不叠加。
- 所有目标操作均经活动 Orbit DAP → RMLinkTargetChannel/NativeScheduler → 连接服务 → 同一板端 SWD 执行器。没有第二个 helper、J-Link、OpenOCD 或绕过会话的目标访问。
- 实际 VS Code Extension Development Host 使用工作树编译产物，通过 VS Code `debug.startDebugging` 启动实际配置，`flashBeforeDebug:true`；并通过实际窗口消息操作 Timeline、PrintWindow 留图。独立适配器子进程结果单独列出，不冒充 UI 验收。

## 已定位的阻塞与实现

1. **Watch 读取让采样跨无线往返暂停。** 实际 Watch 的 dataSample 会读取完整结构体；原 read_memory 使用 begin/end 控制屏障，长网络等待期间板端停止采样。新增能力 bit7 与 `0x81/20` 有界只读命令，每次最多128 B，只限 F103 RAM/Flash，在同一执行器内完成；服务协商能力后选用。周边寄存器、旧固件、写入和真正控制操作保持原屏障，所有权检查保留。
2. **已暂停会话的新 Timeline 提前启动。** 服务跟踪控制导致的暂停状态；恢复配置时保持 Timeline 暂停，等原 owner 的 Continue；Watch 快照仍可更新。用户暂停不会被该恢复机制无条件撤销。
3. **数据连接发送停顿与环形缓存溢出。** 串口记录发送等待约70～400 ms，部分失败轮次更长；不能用 SWD 失败为0推导无丢样。PSRAM Timeline 环由48增至256 KiB，内部内存回退仍48 KiB。网络按8 ms聚合，已有满批积压时立即发送，避免再次等待聚合窗口。
4. **重复帧头消耗带宽。** 能力 bit8 协商 RMS2/RMC2，连续同配置样本共用组头；原样保留每个样本的 CRC、时间戳、绝对序号、有效位、状态和数值，服务还原公开 RML1。三个变量每条68 B降至40 B加共享组头；四个变量为72 B降至44 B加共享组头。旧 RMS1、USB_PULL、本地流和三个语言 SDK 格式继续兼容。
5. **错误诊断。** Orbit 增加服务请求失败的 command/status/code 以及数据流异常日志，沿用共享 logger；保留原 owner 失效传播，不通过重连掩盖未知写结果。

协议细节见 [API v1](api-v1.md)。压缩只在数据发送任务进行，不改变 GPIO/SWD 采样计划、不插值补样，也不连接真正断开的曲线。

## 对照结果与保留的失败

| 版本/条件 | 观测 | 判断 |
| --- | --- | --- |
| 修改前，独立实际 adapter + 实板，三通道、展开 Watch、约12 s | 909.24 Hz，最大缺口3639.2 ms | 可重复的 Watch/采样阻塞证据 |
| 0.2.32，相同独立 adapter 条件 | 4759.83 Hz，最大缺口319.4 ms | 短批读取显著改善，仍有发送/缓存缺口 |
| 0.2.32，真实 VS Code，约186 s | 约4231 Hz，最大缺口3494 ms | 未通过持续无大缺口验收 |
| 0.2.33，真实 VS Code，约379 s | 约4584 Hz，最大缺口1025.8 ms | 扩缓存仍未解决持续发送问题 |
| 0.2.34 | 发生物理 TCP 超时，DAP owner 失效 | 失败，未作为稳定基线 |
| 0.2.35，真实 VS Code，约304 s | 1,458,474 点/通道，最大缺口503.4 ms | 较此前改善，但不能称无损/完整修复 |
| 0.2.36，真实 VS Code | 发送积压仍在；末尾出现 SWD NO_ACK | 不把该轮结尾归因为单纯网络，后续重新连接/烧录通过 |
| 0.2.37，RMC2，真实 VS Code四通道 | 首轮约31 s收到153,358点/通道，最大缺口1.519 ms；随后较长 Watch 压力轮仍发生缓存丢弃 | 短测不能替代长测，继续调整发送等待 |

初始 CSV `ui-before.csv` 在5.0466 s收到13,051点/通道，约2585.90 Hz，最大缺口483.4 ms。它与12 s压力测试的负载/窗口不同，分别保留。此前版本的 Timeline UI 瞬时 Hz 在频繁新分段时可能接近5000；本报告的有效频率以收到的点数除探针时间戳跨度计算，并同时保留缺口。

## STM32 实际烧录

0.2.35 实际 VS Code 启动：54,272 B、53页，`probe_full_compare` 全量比较通过，约4.003 s。SHA256 `d49d4821b6cafec250a1f284e636ed1497782ae96cc06ba12dd51e082e854969`。

0.2.37 第二轮实际 VS Code 启动：55,296 B、54页，`probe_verified_bytes=55296`、`probe_verified=true`，约4.227 s。上传约514 ms；板端擦除约1303 ms、编程1919 ms、完整比较293 ms。SHA256 `c890d625adc17843459252c339f13d4f66adab09f0a9c73c9372fcb2f45e2bf5`。证据 `ui-037-health.json` 和 DAP 的 Flash successful 输出。

上述是当前 enhanced owner 缓存烧录；不冒称通用虚拟 USB Flash 算法有同样耗时。选项字节保持不变，无未知写入重放。

## 0.2.37 阶段自动化与待验收项目

- 主机119项测试通过，包括实际 C 压缩器与独立 Python 格式参考逐字节比较、混合内存/RTT、最大字段、序号回绕、拆包、损坏CRC、超长/畸形输入和能力协商回退。
- C采样模型64/512字节构建与运行通过，覆盖新增只读命令、owner、越界、USB响应上限、缓存压力及既有指针/RTT行为。
- Orbit 全量77文件：1065通过、1跳过；类型检查通过。此前工作树的构建和 native/mock 记录与本次实际开发宿主分别保存。
- 真实开发宿主已完成 STM32 Flash、Watch展开、Timeline真实数据接收；0.2.35 的实际 pause/continue 查询为 Running→Halted→Running，控制整轮约205 ms。最终固件须继续复测，不能拿历史轮次替代。
- 最终持续采样、UI拖动/缩放、独立回读、旧RMS1兼容和清理结果将在完成后追加。当前文档为工作记录，不将候选版本写为无损稳定版。

## 0.2.38–0.2.39：再次大规模缺口后的定位

用户再次提供长缺口截图后，分别记录板端send等待、探针采样时间戳、板端ring_dropped、服务read等待/解码和事件循环、实际Webview绘图/历史查询耗时。旧轮次的短测通过不作为问题已经消失的证据。

- 独占传输测试0.2.38的五次TCP连续回传为8.75/15.421/16.611/15.217/14.176 Mbps，中位15.217 Mbps（约1.814 MiB/s）。四字段紧凑样本在5 kHz下的格式预算约222.5 KB/s；平均链路带宽有余量，但这不能排除数秒发送停顿。证据 `transport-038-exclusive.json`。
- 真实Orbit旧轮次记录到板端最长send等待3.786秒；另有正常Watch负载轮出现约1.9秒采样缺口。服务解码、Webview绘图和范围查询通常远低于此量级，不能靠修UI把已经丢掉的样本找回。
- 新增每连接即时ACK与TCP_NODELAY、数据读取和事件循环诊断。即时ACK不是已证明的单一修复：被动观察轮仍有1.619秒缺口，并有SWD异常。因此保留失败，未作系统TCP/驱动注册表修改。
- 试过把SWD执行器移到CPU1，但实际F5烧录两次返回RM_FLASH_SWD；已拒绝该配置，恢复CPU0后实际烧录重新通过。当前0.2.39仍为执行器CPU0、独立数据任务CPU1。
- 真实Orbit的区间抽稀另有独立问题：密集短分段被LOD合并或恒值段端点被丢弃。现按bucket与segment共同归并，并在摘要不足时下钻，保留首/极值/尾点及分段边界；新增恒值多段回归。曲线仍保留真实缺口，不跨缺口连接。

### 同条件背压复现

`direct-stall.py` 使用独占物理TCP owner与当前用户程序，未另烧测试fixture：`g_imu_demo.rpy_deg[0..2]` 的三个实际地址和 `abb`，SWD4 MHz、请求200 us。独立心跳继续运行，收包线程在第2秒暂停1.8秒，再恢复；每轮约10秒。解码检查CRC和有效位，不插值。

| 条件 | 收到帧数 | 有效频率 | 最大采样间隔 | >10 ms间隔 | 环形缓存丢弃 | 无效值/控制错误/SWD失败 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 0.2.38，256 KiB | 47,358 | 4735.40 Hz | 244.399 ms | 2 | 2227 | 均0 |
| 0.2.39，2 MiB | 49,518 | 4952.69 Hz | 1.362 ms | 0 | 0 | 均0 |

证据 `stall-038-256k.json`、`stall-039-2m.json`。这证明该背压条件下原缓存会溢出、扩大缓存后可保留数据；不表示所有网络停顿已消除。0.2.39将独立数据socket发送上限从2秒改为5秒，控制链路保持原规则。四字段每条环形记录约80 B，2 MiB在5 kHz下约能容纳5.2秒；字段更多时承受时间会变短。PSRAM分配失败仍明确回退48 KiB，能力查询报告实际容量。

### 当前实际F5与自动化

0.2.39、编译后的连接服务EXE0.2.12、SDK0.2.8，在真实开发宿主启动 `probe:rmlink` 配置：55,296 B/54页，SHA256 `df40eb098698b8648241b7c31090e44a942a53796c4ae41e735d6593c588de77`，板端完整比较通过、选项字节不变，总计3931.71 ms（上传302.50 ms、commit3582.25 ms）。见 `ui-039-health-running.json`。未把该板端比较当作第二条独立主机回读；此前运行中的DAP回读尝试被取消，不列作通过。

最终主机123项通过；0.2.39实际C采集模型64/512字节均构建运行通过；固件构建/刷写校验通过。Orbit前次全量77文件1072通过、1跳过；最后配置边界调整后相关4文件102通过、类型检查通过。构建EXE SHA256 `9e0305ef5f29616e386cdfb8af21ae083dad0daecbb42cbb7c0261176b476067`。这些自动化不替代持续实机验证。

0.2.39最终真实Orbit持续运行、正常展开Watch、实际拖动缩放及连接服务短暂停顿的结果记录于下一节。请求5 kHz仍可能发生deadline_missed/短分段；“无缓存丢弃”“没有>10 ms缺口”和“精确每200 us一帧”是不同结论。

## 0.2.39 最终真实 Orbit 结果

使用普通编译EXE0.2.12（非临时Python诊断包装器），开发工作树扩展、`probe:rmlink`，SWD请求4 MHz、四变量请求200 us，正常展开 `g_imu_demo` Watch，没有额外500 ms压力定时器。主验收会话 `8645bb6c-641a-41d3-bde0-af0743f6fe21`，618.722秒主机窗口；统计严格匹配该会话ID，用户或新会话的数据不会混入。

| 项目 | 结果 |
| --- | --- |
| 每通道收到点数 | 3,051,968 |
| 首尾探针时间跨度 | 618.220205秒 |
| 有效采样率 | 4936.70 Hz，按 `(点数-1)/首尾跨度` |
| 最大相邻采样间隔 | 2.120117 ms |
| >10 ms采样间隔 | 0 |
| 板端ring_dropped / failures | 0 / 0 |
| 服务stream_error | null |
| 终段deadline_missed / budget_overrun | 39,162 / 100,776；并非精确5 kHz、无超期 |
| 内存 | 运行中PSRAM仍约6.2 MB可用；内部内存最低约84.8 kB，见health原始快照 |

第209秒附近通过受控脚本将当前连接服务进程暂停1800 ms并在finally恢复；服务记录事件循环lag1.578秒、板端发送等待约1.069秒。恢复后前述原始样本统计仍没有>10 ms缺口、无板端缓存丢弃，说明这轮积压经过板端、服务缓存和Orbit消费者后仍完整保留了已采集数据。没有把服务历史淘汰数当作实时消费者丢样；历史队列本来只保留4096条。此操作和独立收包线程暂停是不同故障注入，分别保留证据。

实际窗口完成历史拖动、缩放和追踪切换，并留有 `ui-039-pan.png`、`ui-039-restored.png` 等图。窗口部分时间被最小化后恢复，不把618秒全部描述成前台渲染压力测试。屏幕上 `abb` 持续增长，三角度为0；当前Watch同时显示MPU6050_BUS_ERROR/HAL_BUSY，因此不将角度不变误判为网络冻结，也未改动用户传感器程序。

主持续统计在最终主动暂停/回读之前封存为 `ui-039-run.json`、`ui-039-health-final.json`，避免将验收控制暂停算成网络丢样。随后经同一实际DAP owner完成：

- Running→Halted→Running，整轮118.32 ms，`ui-039-control.json`。
- 暂停确认后，通过实际DAP `readMemory` 逐块独立回读55,296 B，SHA256与镜像一致，约4759.38 ms；完成后Continue。`ui-039-readback.json`。这是主机独立回读，区别于烧录时板端完整比较。
- 停止后服务owner为null；随后立即重新F5，第二次烧录同镜像约3945.52 ms，板端完整比较与选项字节核对通过，正常开始四变量订阅。`ui-039-restart-health.json`、`ui-039-restart-stop-trace.json`。
- 最后结束第二个验收会话、确认无owner，停止串口采集，保留正常连接服务0.2.12运行；虚拟USB恢复挂载到port1，并恢复自动挂载偏好。`ui-039-restart-stopped.json`、`virtual-usb-039-restored.json`。没有遗留后台目标测试占用。

剩余边界：本轮约十分钟与1.8秒故障注入没有重现用户的大规模缺口，但不等于任意热点/时长都已稳定无损。发送停顿仍存在，更长的断网或超过缓存容量的积压会报告真实缺口；瞬时Hz不是整段完整性保证。本轮未重新执行虚拟USB/OpenOCD全套或实板旧RMS1采样，兼容格式有主机/C模型覆盖，历史实板结果不冒充0.2.39新验收。更换目标芯片、缺失的NRST接线和完整断电/断网续传也不在本轮通过范围。

## 十变量卡顿复现与 5 ms 分段规则

用户追加十变量卡顿、重启后近乎不可用，并指定断线显示阈值为5 ms。本节是后续真实开发宿主测试，不能用上节四变量结果替代。固件保持0.2.39、普通连接服务EXE保持0.2.12、SDK0.2.8；修改和构建发生在Orbit开发工作树。目标仍为用户STM32F103C8应用，SWD请求4 MHz；没有修改用户应用、提高SWD频率或新增目标访问者。当前应用的CPU频率没有在本节重新量测。

十个表达式为 `g_imu_demo.rpy_deg[0..2]`、`abb`、`g_imu_demo.sample.accel_g[0..2]` 和 `g_imu_demo.sample.gyro_dps[0..2]`。请求周期200 us，发送周期15 ms，正常展开Watch。有效频率按 `(每通道点数-1)/首尾探针时间跨度`，不用界面瞬时Hz替代。

### 复现出的三个独立问题

1. **把调度超期当成每一段断线。** 固件序号也包含未执行的调度槽，旧Orbit遇到序号不连续就新分段。十变量约3.5 kHz时，序号跳过并不意味着实际样本间隔很长。旧规则164.357秒主机窗口内每通道561,139点，产生222,010个分段；完整轮次还出现29处>10 ms间隔、最大471.215 ms。真实警告为“CSV 写盘队列超过 8 MiB，持久记录已停止”；扩展宿主事件循环最长阻塞1637.88 ms。分段、历史摘要和范围查询的负担与采集链路同时存在，不能将这轮全部归因为Wi-Fi带宽。
2. **重启仍选中上一轮历史页。** 新实时记录已经接收数据，但原先只在选中调试模板时切换到实时页。现从调试记录重启会自动选中新实时记录，并保留旧历史；用户主动选中的导入CSV页仍保持原选择。
3. **快速加变量的启动/停止竞态。** 老配置的异步启动晚返回后会发送Stop，可能将较新的采样配置停掉。新增回归在修复前确实失败；现将远端启动、停止串行处理，执行前跳过过期配置，保持原DebugSession检查与单owner访问链。连续补回六个字段的实际公开命令测试只下发最终十字段配置，未被旧Stop停止。删除六字段的中间重配会造成有意的停采样，不计作网络随机缺口。

### 最终显示规则与诊断

RMLink板端订阅路径按**每个字段最后一个有效样本的实际时间戳**决定分段：相邻间隔 `>5 ms` 才开始新段，恰好5 ms保持连接；首个有效点仍开始新段。序号跳过、deadline_missed、缓存丢弃等诊断继续保留。这个规则只决定画线，不补点、不插值、不修改原始时间戳，也不代表精确每200 us采到一帧。普通本地轮询及其他后端的分段规则未在此改动。

保存失败现在通过既有共享DAP日志记录原始错误、recordId和store指标，方便将写盘停止与真实目标断连区分。没有扩大8 MiB写盘队列来掩盖溢出。

### 真实 Orbit 对照与保留的失败

证据目录为 `artifacts/logs/orbit-timeline-20261016`。

| 条件 | 探针时间跨度 | 每通道点数 / 有效频率 | 分段与实际缺口 | 验收范围 |
| --- | ---: | --- | --- | --- |
| 旧规则十变量，`many-baseline.json` | 163.717 s | 561,139 / 3427.49 Hz | 222,010段，最大471.215 ms，29处>10 ms | 实际复现8 MiB持久记录停止；失败 |
| 5 ms与新实时页修复，`many-fixed-threshold-only.json` | 256.956 s | 893,580 / 3477.55 Hz | 只有首点初始段，最大1.640 ms，0处>10 ms | 257.305 s主机窗口，真实六轴传感器曲线、2 s/div与10 s/div、拖动/追踪，未出现写盘溢出；本轮尚未包含后续启动竞态修复 |
| 加入启动竞态修复，动态补回六字段后，`many-final-ten-run.json` | 197.752 s | 687,885 / 3478.52 Hz | 重置统计后没有新段，最大1.623 ms，0处>10 ms | 该稳定数据段后发生真实SWD访问失败并终止会话；整轮不列为稳定性通过 |
| 最终代码重新F5，控制/独立回读后连续段，`many-restart-final.json` | 302.880 s | 1,057,538 / 3491.60 Hz | 重置统计后没有新段，最大4.350 ms，0处>10 ms | 五分钟窗口无目标失联、缓存丢弃或持久化停止；有意暂停/回读不混入本行统计 |

第二轮真实Webview绘制最长5.70 ms；范围查询最长387 ms；记录中最大排队966,400 B，没有写盘溢出。消除巨量分段后有明显改善，但仍存在历史查询长尾，不能写成所有交互都只需几毫秒。末轮1 ms/div和10 s/div实际切换、拖动的日志另记录一次501 ms范围查询，绘制本身仍低于一帧预算；后台/前台窗口状态会影响帧数。

失败会话 `e19ce54d-eb81-41ce-b822-059769a4a3a8` 在15:33:08本地时间结束。服务进程仍在、Wi-Fi设备仍连接；DLL日志记录 `state` 返回 `DapStateReadFailed: 811105`，随后只读快照返回 `811405`，Orbit按原owner丢失规则结束会话。结束前持久记录已接受12,330,623行、写入12,327,753行，队列367,360 B；没有8 MiB溢出警告。结束后health记录最近采集失败 `dap_status=7`、完成0/请求11个操作、`abort_reason=none`。这支持SWD访问失败，不能据此独断为物理松线，也不能归为Webview卡死；没有用自动重连或重放未知写入掩盖该失败。证据 `many-final-ten-failed-dap.log`、`many-final-ten-failed-dll.log`、`many-final-ten-service.log`、`many-final-ended-health.json`。`many-final-ten-run.json` 的主机elapsed包含结束后的检查时间，只以上表首尾探针时间跨度计算采样率。

### STM32 再次烧录、控制及独立回读

在上述失败后重新启动真实开发宿主的同一 `probe:rmlink` 配置，未手工重置开发板或更换SWD频率：会话 `1e9094a5-4a01-43e1-9e31-3431d2b55a31` 正常建立，并自动选中新实时页。

- F5缓存烧录55,296 B / 54页约3973.16 ms，`probe_full_compare` 完整比较通过，选项字节不变，SHA256仍为 `df40eb098698b8648241b7c31090e44a942a53796c4ae41e735d6593c588de77`。见 `many-restart-health.json`。前一末轮同镜像烧录约3891.00 ms，见 `many-final-ten-start-health.json`。
- 同一真实DAP owner完成 Running→Halted→Running，整轮116.27 ms，见 `many-final-control.json`。
- 确认暂停后经同一DAP `readMemory` 分块独立回读55,296 B，约4773.60 ms，SHA256与镜像一致；finally继续运行。见 `many-final-readback.json`。

重新烧录后的应用Watch显示 `MPU6050_BUS_ERROR / HAL_BUSY`，六轴和角度为0，`abb`仍增长。当前轮能验证十字段采样/持久化负载，不能把它称作十条动态波形；此前256.956秒轮有实际传感器变化及截图 `many-fixed-pan.png`、`many-fixed-80s-settled.png`。传感器故障未通过修改用户程序处理。

### 自动化与剩余边界

最终Orbit类型检查、构建通过；77个测试文件1078通过、1跳过，`test:cmsis-dap:all`通过。回归覆盖5 ms精确边界、序号跳过、字段无效造成的独立缺口、重启记录选择、手动CSV隔离、旧启动晚返回与移除最后字段。证据 `many-final-full-tests.log`、`many-final-native-mocks.log`；失败复现保留于 `many-sampling-race-red.log`。

最终重启轮在真实F5、控制/回读后重置统计，主机窗口302.769秒（只用于测试持续时间，采样率使用上表探针跨度）。最后health：subscription completed=1,176,829、deadline_missed=508,552、budget_overrun=1,176,826，ring_dropped=0、failures=0、stream_error=null；该health包含重置前控制/回读阶段，不能把completed直接当作本行统计点数。`host_history_evicted=1,172,515`是服务4096条历史环的正常淘汰，不是Orbit实时消费者丢样。见 `many-restart-final-health.json`。

这轮Webview绘制最长4.40 ms、范围查询最长3 ms、CSV最大队列1,280,000 B，扩展宿主事件循环最长40.01 ms，没有8 MiB溢出警告；这些是本轮已记录摘要中的最大值，不是任意历史操作的性能上限。十变量页面截图 `many-restart-final.png`。结束后确认owner=null，连接服务仍运行、虚拟USB仍由服务管理在port1，见 `many-end-status.json`；没有遗留后台目标测试会话。没有提交或推送代码。

本节修复和短中期对照不等于完整消除所有断连。异常SWD ACK、历史查询长尾、任意长时间运行及更大字段数仍需独立验收；NRST仍未接线。本轮没有刷ESP固件、安装驱动、更改系统启动配置或重新跑虚拟USB/OpenOCD全套。

## 0.2.14 复位保留订阅与 main 入口（后续）

用户18:39复现：调试工具栏复位后Watch可读取、Timeline降为0 Hz；启动和复位停在HAL函数而非main。原始会话`81a3bdb0-0990-49bb-9e49-34cdeed82c95`的日志显示10:39:17 UTC复位后没有继续采样，服务`subscriptions/1`返回invalid_request；证据`reset-bug-before-dap.log`、`reset-bug-health.json`。

源码有三处关联问题：

1. 服务target/reset显式delete全部订阅，但Orbit仍持有activeCaptures和监听器，没有重新订阅。现在复位先暂停Timeline并保留原配置、capture_id和数据连接；停在入口期间维持暂停，owner的run恢复同一订阅。Watch暂停读取保留，控制仍经唯一执行器屏障。
2. Orbit配置归一化和DAP launch两处将RMLink的runToEntryPoint强制为false。现在默认main并保留自定义符号/显式false。服务新增`run_to_address`：SYSRESETREQ配合VC_CORERESET先捕获复位，再用临时FPB比较器运行到ELF符号地址，确认Halted/PC后恢复原比较器和DEMCR；无空槽/入口未命中/超时返回结构化错误，不另建目标owner。纯reset本身保持Halted，未接物理NRST。
3. 三轮首次复测虽然恢复了订阅，第三次触发“采样时钟回退”并新建不完整记录。原因是把主机累计暂停时长从已经采集的探针时间戳扣掉，TCP中延迟的旧批次在恢复后变成更早时间。新增回归确实得到-99 ms而应为+1 ms，保留`reset-clock-red.log`；修复为仅用一次锚定偏移转换探针时间，不从中扣除后来发生的主机暂停。实际停采样间隔保留为真实缺口，仍按>5 ms分段，不通过钳制时间或补点掩盖。

最终服务EXE 0.2.14 SHA256 `0F7EAA26FA758391FD9E8E54688DBF972F4582E00B1E72CBA60F0E583E31E6DB`，固件未再变更。最终Orbit78文件1085通过、1跳过，132项主机测试通过，类型检查/构建通过。模型覆盖FPB槽耗尽、共享用户槽、其他断点恢复、错误PC、超时清理、无效地址不写目标，以及HTTP复位后保留/恢复订阅；这些模型项不冒充全部实板异常验收。

最终真实会话`161b1abf-4be7-4a3d-9d47-699c92c2c737`，F5通过55,296 B/54页烧录及板端完整比较，PC=0x08008EC0，ELF nm与标准DAP stackTrace均指向main/main.c:68；见`reset-entry-inspect.json`和`reset-final-entry-output.log`。同一会话连续三轮实际DAP restart→检查main→continue，每轮等待5秒观察所有十通道点数增长；capture_id始终3，暂停state=3、继续state=2，SWD failures/ring_dropped为0，未再出现clock rollback/持久化失败。见`reset-repeat-accept.json`、`reset-final-dap.log`。前一未修正时钟的失败仍保留在`reset-repeat-before-clock-fix.json`、`reset-before-clock-fix-dap.log`。

真实Watch显示MPU6050_OK/HAL_OK和六轴动态值，角度仍在标定阶段为0，截图`reset-final-after-repeat.png`。本次专门验证调试工具栏的软件复位，不包括目标物理断电、独立NRST复位、所有软件/芯片或任意长时间运行。电脑热点数据到达长尾仍是独立限制；不把入口/订阅修复写成无线卡顿已全部消除。服务刚重启尚未发现设备时的一次F5返回Device offline也保留，待设备在线后才执行成功验收，没有静默重复烧录未知操作。

最终三轮复位结束后，单独重置统计继续动态十字段采样：首尾探针跨度131.067672秒，每通道455,626点，有效3476.26 Hz，最大间隔1.506 ms，0个新段、0处>10 ms；无缓存丢弃/SWD失败/持久化停止。该段不包含有意复位的暂停；更长144.294秒被动到达观察窗口最大205.412 ms，仍非完全均匀显示。见`reset-final-continuous-run.json`、`reset-final-continuous-health.json`、`reset-final-arrival.json`、`reset-final-report.json`。本次F5总计3925.12 ms，完整比较SHA256与上文镜像一致。

随后结束Orbit验收会话，使用公开API在无其他owner时建立一个独立验收会话：设置main和另一Flash地址两处硬件断点，运行到main复用原main槽、临时忽略另一个槽，确认PC=0x08008EC0；操作前后FPB区域32字节逐字节一致，cleanupOk=true、sharedUserSlot=true，约438.55 ms。移除这两个测试断点，纯reset确认Halted，再run确认Running，finally关闭owner。见`reset-breakpoint-accept.json`。最终服务0.2.14正常运行、设备在线、owner=null、虚拟USB由服务管理在port1，未遗留测试目标占用；未提交或推送。最新版开发宿主与服务已加载，用户下一次F5默认停在main，按继续后Timeline恢复采样。

## 整页交互与绘图开销（后续）

用户进一步澄清：整个前端看起来卡，鼠标提示框延迟只是表现。本轮据此检查连续采样、绘图、React更新和拖动/缩放，而不是只修改提示框外观。

### 已核实并修改的路径

- 原先每个pointermove都会更新TimelinePlot的两个React状态，进而遍历工具栏/变量列表并重画全部曲线、网格。现在独立DOM交互层复用文本节点、用CSS transform移动十字线和提示框；ResizeObserver缓存宽度，保留右边界翻转。标签使用textContent。
- 新数据已在接收批次的动画帧内绘制，随后React提交还可能为同一数据、视口和样式重复绘制。现在用数据修订号、视口、尺寸、DPR、变量样式和A/B游标作为失效条件，避免这些重复绘制；清空数据显式增加修订号。拖动历史视口通过requestAnimationFrame合并绘制，不逐次等待React提交后的effect。
- 采样率文字最多每250 ms更新；侧栏数值仍按100 ms刷新，波形随新数据按帧绘制，全量原始记录继续保存。
- 常规npm run build原先包含React开发运行时。三个浏览器bundle现在显式选择production；--watch保留development，Node扩展和调试适配器构建配置未调整。生产模式单项对照收益很小，因此不把它描述为唯一原因。
- 增加可见页面rAF间隔、>25/50/100 ms次数、指针派发等待和指针处理到下一个rAF的摘要日志。可见性切换会重置基线。这些指标是JS/浏览器调度观察，不是屏幕扫描或GPU实际呈现延迟。

实现位于Orbit工作树`src/webview/timeline/app.tsx`、新增`timeline-hover-layer.ts`及`esbuild.config.js`。本轮未修改服务/ESP固件，也未修改STM32应用源码。最终timeline.js为339,091 B，SHA256 `5f9c724e7e85995752fd5c1aa244c35f398f62a99e92ae3918efd95130d8db94`；已重新加载到真实开发宿主的Webview。

### 浏览器模型与回归

使用独立headless Edge和本地HTTP夹具加载真实dist/timeline.js，通过CDP派发原生鼠标/滚轮事件；没有把它称作Playwright或真实VS Code帧率验收。夹具为10通道、20 ms通知、2 ms范围回复、2 s/div，走范围查询路径。固定静止数据后的60次鼠标移动，旧版确实重画60次，最终版0次。保留旧版失败证据`frontend-before-interactions.json`。

最终12项浏览器断言通过：静止悬停不重画、十行提示框右边界翻转、离开隐藏、历史拖动、拖动后正确失效重画、滚轮缩放、A/B放置、拖动A不带动B或视口、尺寸变化重画、标签按文本显示、创建新记录发送clearData、清空后实际重画并移除波形。证据`frontend-final-interactions.json`和生成该轮脚本的`frontend-interactions.cjs`。最终独立浏览器启动阶段仍记录过93 ms长任务，不能声称所有阶段均无长任务。

连续数据夹具中，240次原生鼠标移动的观察如下（每次工具调用等待真实浏览器响应，因而各轮持续时间不同）：

| 构建 | 整图绘制次数 | 观察到的rAF跨度 | 最大事件派发等待 | 处理后DOM更新P95 |
| --- | ---: | ---: | ---: | ---: |
| 原版 | 635 | 14.209 s | 114.6 ms | 4.3 ms |
| 仅production React | 623 | 14.094 s | 100.7 ms | 4.2 ms |
| 最终交互/绘制改动 | 362 | 9.038 s | 35.1 ms | 0.4 ms |

绘制次数减少和交互脚本更快完成是该夹具的观察值，不能把次数比例直接当作实际帧率提升比例。headless rAF存在较大长尾，亦不据此推断显卡故障。完整数据、CPU采样文件和计数脚本见`frontend-report.json`、`frontend-*-input.json`、`frontend-*.cpuprofile`、`frontend-profile.cjs`。类型检查、常规构建、git diff --check通过；全量78文件1085项通过、1跳过，见`frontend-full-tests.log`。

### 真实Orbit、STM32与剩余边界

真实会话`936febb7-89ce-4de2-94ab-9f8389a825e0`使用probe=rmlink、请求SWD 4 MHz、相同F103 ELF。F5烧录55,296 B/54页共4120.795 ms，板端完整比较通过，SHA256仍为`df40eb098698b8648241b7c31090e44a942a53796c4ae41e735d6593c588de77`，选项字节不变。DAP stackTrace确认停在main/main.c:68，PC=0x08008EC0。证据`frontend-entry-inspect.json`和`frontend-real-health.json`。

继续后十字段持续采样，探针首尾469.304582秒，每通道1,627,309点，有效3467.488 Hz，最大实际间隔2.329 ms；每通道仅记录起点一个段，没有新增>5 ms断点，ring_dropped=0、failures=0、stream_error=null。Watch显示MPU6050_OK/HAL_OK、六轴动态值，角度在标定期间为0。本轮没有重新测量STM32主频，不能称作已确认72 MHz的性能基准。此段包含Webview重载、浏览器夹具和回归测试的主机负载，亦不是469秒全程前台交互。

Windows脚本先确认目标窗口为前台，19:55:30—19:55:37实际执行120次鼠标移动、60步历史拖动、两次滚轮和恢复追踪，finally释放鼠标并恢复位置。截图`frontend-real-panned.png`、`frontend-real-follow.png`及日志`frontend-real-gestures.json`。对应两段10秒Webview摘要：指针派发最大33.2 ms，处理后等待下一rAF最大19.8 ms，rAF最长45.9 ms，无>50 ms JS长任务；这不是GPU实际呈现耗时测量。

同时记录到独立的数据停顿：上述首段摘要新数据通知最长734.9 ms、范围回复最长495.4 ms，而本地rAF最长41.7 ms；整个被动收包窗口进入Orbit的到达间隔最长790.904 ms。其时探针样本仍连续，说明新数据批量到达的卡顿不能仅靠减少绘图耗时解决。无线/主机/通知链路的剩余长尾仍需继续定位；不据这些日志宣称所有卡顿消失，也不将其直接归因SWD极限或GPU。原始日志保留于`frontend-real-dap.log`、`frontend-real-arrival.json`。

最终已结束本轮验收owner、释放监听器。连接服务0.2.14和固件0.2.40保持在线，owner=null，虚拟USB仍由服务管理在port1；见`frontend-end-status.json`。没有提交或推送，也未向bug-fix-log添加“已完全解决”的记录。
