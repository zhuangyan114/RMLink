# RMLink 0.2.0 订阅实施记录

更新：2026-10-15。本轮从 RMLink c394931、Orbit 3928bbb6f4990ff336dfbe71f745f5f593b12e48 开始，按 [设计](subscription-design.md) 实施。用户授权自主决策、ESP32 刷写及 STM32 擦写；标准 CMSIS-DAP 验收走虚拟 USB，STM32 默认保留新测试程序。旧基线和测量保持原条件。

**最新实板检查点为 0.2.6**：指针依赖分批读取、RTT STOP／重建修正通过模型、标准虚拟 USB 全流程、pyOCD、原生 USB 三订阅和十分钟独立四级指针持续检查。79 项主机测试通过，CPU0 的 TCP64／512 构建通过。重启恢复、一次 Orbit 子进程异常退出和控制长尾仍未解决，未宣称十变量持续完整 1 kHz。最新身份、性能和限制见 [检查点](subscription-checkpoint-026.json) 及 [采样性能记录](subscription-performance-20261015.md)。下文按检查点保留各版本的历史发现、阻塞和产物身份，不能作为当前故障状态。

## 首个检查点：0.2.0（历史）

## 已实现的调用链

第三方前端／Orbit → Python、Node 或 C SDK → 本地认证 API → TargetEngine／Subscriptions → Broker 的唯一物理 TCP → ESP32 固定核 SWD 执行器 → STM32。

标准调试软件 → 原有 UDE 虚拟 USB → 本机 USB/IP 代理 → 同一个 Broker 与物理 TCP → 同一 SWD 执行器。增强会话和标准会话不能同时控制目标；数据流不会创建第二个 SWD owner。

通用逻辑位于连接组件，核心使用不依赖 Orbit。内核驱动继续负责虚拟 USB 和 URB，未把网络、Flash 或采样算法放入内核。

| 层 | 主要实现 | 结果和约束 |
| --- | --- | --- |
| 默认与验收 | DAP_config、rmlink_swd、accept_virtual_usb | 请求 4 MHz；显式第三方时钟仍受尊重；恢复旧程序仅 --restore-original |
| 板端采集 | rmlink_capture.c、swd.c | 四槽、32字段、1/2/4字节、4层指针、绝对周期、500 us传输期限、有界工作块和控制屏障 |
| 数据回传 | rmlink_stream.c | TCP3244、一次性 owner 票据、CRC、序号／时间戳／valid bitmap、最多4 KiB发送聚合 |
| 内存与 RTT | 独立64 KiB环形缓存 | Timeline48 KiB、Watch4 KiB、RTT各6 KiB；Watch复用静态重合32-bit字段；RTT提交结果未知时暂停核对 |
| 通用服务 | target.py、subscriptions.py、service.py | 目标连接、控制、寄存器／内存、硬件断点、三类订阅、心跳、游标分发、错误分类和会话结束清理 |
| 通用烧录 | flash.py、host/algorithms/stm32f103c8 | F103C8完整1 KiB页镜像；RAM算法、逐页校验、独立回读哈希、选项字节核对、取消与无自动重放 |
| SDK | host/sdk、client.py | Node／TypeScript、独立WinHTTP C DLL、Python；C discovery-only入口不创建目标会话 |
| Orbit实验适配 | 独立工作树 artifacts/worktrees/orbit-rmlink | 唯一服务 owner；Timeline使用探针间隔，Watch消费推送，RTT消费服务缓存；ELF按LMA映射到页镜像 |

更多协议、字段和调用示例见 [API](api-v1.md)。Flash算法只复制独立源文件与MIT许可，来源固定于 provenance.json；未将其他完整仓库纳入根仓库。

## 分层验证

| 层次 | 已取得证据 | 不代表什么 |
| --- | --- | --- |
| 真实采集C源码的主机模型 | Test-Capture：配置覆盖、控制屏障、Watch复用／接管、指针共享与逐帧更新、无效字段、SWD失败、RTT环绕／单次RdOff、票据／owner与20,000随机输入通过 | 未量测ESP32调度延迟或STM32采样率 |
| 主机协议与服务 | 52项测试通过；64／512字节目标模型、实际HTTP／二进制socket、实际WinHTTP C ABI、清理顺序、Flash完整镜像／哈希拒绝／失败不重放 | Flash测试使用算法入口模型，不是STM32执行RAM算法 |
| Orbit实验工作树 | 类型检查、构建通过；76文件、1052通过、1跳过；包含独占owner、Watch并发合并、探针时间间隔／旧事件隔离、RTT缓存、ELF LMA与取消测试 | 未部署到当前VS Code，未完成真实界面／目标验收 |
| ESP32构建与刷写 | 固件0.2.0 target profile／TCP512构建与COM13刷写hash通过；修正了初版网络栈初始化前创建stream socket导致的 Invalid mbox | 不等于目标烧录或有效采样通过 |
| 真实ESP32协议 | 2026-10-15：错误／外来票据拒绝、三订阅SWD失败暂停、partial帧／valid=0、owner关闭后数据流EOF通过 | 接收的是失败样本，不能用于声称1 kHz有效采样 |
| 真实USB／虚拟USB | 原生64、虚拟512、API512各8个完整响应及后续短响应通过；6个空读取消、返回121／0字节／缓冲不变与后续正常查询通过 | 本轮没有STM32完整烧录／调试证据 |

主要本地证据：artifacts/logs/host-subscriptions-tests.log、capture-host-tests.log、orbit-rmlink-{typecheck,build,full-tests}.log、build-subscriptions.log、flash-subscriptions.log，以及 artifacts/logs/subscriptions-20261015/ 下的 esp32-fault-transport.json、packet-regression.json、virtual-idle.json。历史数据不会因默认改为4 MHz而重新标注。

## 当前硬件阻塞

4 MHz固件首次连接曾读到CPUID，随后在完整Flash读取阶段返回050007。之后100 kHz、1／2／4 MHz均无法读到DP IDCODE，原生USB诊断也不能恢复。该验收在目标擦写前失败；本轮没有擦除、编程或恢复STM32，新的采样／RTT fixture只完成构建。

ESP32和虚拟USB可以正常查询、回传和执行失败处理，不能据此确定STM32故障根因。已请求用户恢复目标供电／共地／GPIO4→SWDIO／GPIO5→SWCLK并按目标RESET。NRST依旧缺线；GPIO6输出不能作为物理复位验收。

## 产物身份

- 当前刷入ESP32镜像SHA256：CF3586570BBE9C03CE79DF876AB9650A7B71760543B7A65E81DE88789D207BB8。
- 当前连接组件EXE SHA256：251CEED736D559B4CA808D4D2DA5376DE7198D626AC0F9D4A53BB8B2B91F49CA。
- C SDK DLL SHA256：2218812C471452A6832CCE0EA6FB63E2E4350DA62AB3CABE5FD54D024E3333C7。
- STM32新fixture SHA256：2A65C721FA1BE9C4FCDDBA2A32BB96CCAEECE439B517ADB167F00EA928C031A4，未烧录。
- 独立Flash算法SHA256：199F933F156823C53A62A6E470057D6840FD84306815830A31B056716CE0B979，尚未在本轮真实STM32运行。

后续产物变化应重新生成身份记录，不能沿用上述hash。

## 尚未实现或验收

首版输入支持200 us周期，不保证5 kHz；10字段／1 kHz的96 KB/s是格式预算，不能当作实测速率。STM32纯SWD基准、有效1 kHz／更高频率、Watch与RTT共存、控制延迟、长期运行及增强Flash实板验收均待完成。

当前内存缓存满时丢新帧，尚未达到设计中的实时丢旧策略；严格录制、应用确认重传、TIME_SYNC、原子配置更新、跨进程只读授权和原生USB数据拉取尚未实现。Orbit源码单步into为有界逐指令执行，step-over／step-out暂未支持；服务没有声明源码单步能力。Keil／IAR／HID／正式驱动签名／统一安装界面尚未验收。

本记录是首个实现检查点。后续继续推进时间同步、缓存策略、压力验证与产品打包；目标恢复后先重跑虚拟USB 4 MHz验收，再烧录新fixture、增强Flash回读和三订阅性能对照，不自动恢复旧程序。

## 后续检查点：0.2.1

首个实现提交为2daec01；Orbit隔离适配为b05f528，压缩补丁与来源保存到host/orbit。继续完成了TIME_SYNC和有界丢旧缓存策略，未停在首个检查点。

54项主机测试通过，真实采集C源码增加2000帧缓存压力模型：保留最新帧、顺序不倒退、回收数量准确；该模型不能替代ESP32高负载实测。Orbit按subscription_id／capture_id隔离旧任务数据，防止旧RTT日志混入新订阅；原工作区仍未修改。

真实ESP32时间同步10组×8次通过，选中映射的不确定度中位约0.97545 ms，组间范围0.9082～1.112 ms；外部时间查询未改变已有owner，C SDK discovery-only时间查询通过。原始80次记录见artifacts/logs/subscriptions-20261015/clock-sync-021.json。这是该主机／热点路径的时间映射误差估计，不是单程延迟或采样性能。

0.2.1实际构建／刷写：ESP32 SHA256 DE8090FC66BBF161C005EA88348123B98E3A0D0B019C6EE50FC1248CB17F6107；Connector SHA256 905E69424D9FDDA547DF7D2786DE81516FEB7C1FE4F5B76C1CEE2AEB1F56D692；C DLL SHA256 A5E4611E0849A3CE4203CBE88DB53D464F6E664020418AB15164795A7A3DF6BD。build/flash日志为*-021.log，失败样本／票据／关闭回归再次通过，原始esp32-fault-transport-021.json保留。

严格录制、应用确认重传、漂移补偿、原生USB数据拉取及STM32有效采样仍待完成。API文档按0.2.1更新，上文0.2.0限制和hash作为首个检查点历史保留。

## 后续检查点：0.2.2

增加原生USB_PULL有线帧读取、USB／Wi-Fi显式选路及同一API的有线订阅消费；WinUSB基础类从脚本提升为共享主机模块，旧脚本保留兼容导入。关闭传输时等待同步I/O线程完成再释放句柄；关闭活跃的无owner查询会明确结束等待者。

61项主机测试通过，包括分片／空轮询／错误id／长度变化、同一公开API的有线订阅、切换时租约约束、未完成I/O句柄生命周期。真实ESP32完成USB选路（64字节）、相同片重复读取、60字节失败样本的52+8分片重组、外来控制拒绝、活动会话期间拒绝切换和Wi-Fi恢复（512字节）。目标仍NO_ACK，收到的是partial／valid=0，不能算有效采样。证据wired-capture-022.json、packet-regression-022.json。

已刷入0.2.2 ESP32镜像SHA256 27C128DEF2A289842DCCABC1DFB0261017569FE951BEC289F6E907772A402059；连接组件SHA256 0CFA0811CAC40E672782091F949F77E163A7956E70772F52E452B5A639D76D25。C SDK最新hash应从本地构建清单读取。尚未完成纯离线USB新设备自动发现、自动无缝选路、有效采样和严格录制；当前传输选择是用户／客户端的明确请求。

## 后续检查点：0.2.3

用户重新插紧接线后，已完成真实F103C8虚拟USB请求4 MHz全流程验收，64 KiB新fixture保留。三订阅有效采样、三层指针、无效字段隔离、RTT二进制校验、控制暂停/恢复、独立原始录制与C SDK续流均取得实板证据。完整性能、原始文件SHA256和条件见 [性能记录](subscription-performance-20261015.md) 及其JSON；此前NO_ACK文件保留。

性能修改集中在现有链路：调试组件使用O2；Xtensa慢路径延时循环避免栈读写；单次采集传输期限改为2 ms；周期10 kHz唤醒改为最近deadline的一次性定时器；静态Timeline读取块由4扩大至8字段；实时deadline模式显式跳过过期时槽，严格模式继续支持超限暂停。每个块重新建立SELECT/CSW/TAR，标准CMSIS-DAP的第三方显式时钟与1秒期限不变。

服务read_batch将多个范围放入实际独立寻址的DAP包；增强Flash的核心寄存器读写使用Value Match在探针内轮询S_REGRDY，避免每个寄存器反复无线往返。匹配使用无地址递增CSW，后续操作显式设置TAR；完整64 KiB实测104.35→21.89秒，独立回读一致。

新增RMLR v1录制器、流式文件验证、公共录制路由及Python/Node方法；录制在服务历史窗口之外工作。新增host_history_evicted明确保留窗口淘汰，host_dropped仅为兼容别名。SDK保存启动和消费游标，避免新任务读取4096条历史前缀。具体窗口、严格失败和尾部边界见 [API](api-v1.md) 与 [文件格式](recording-format.md)。应用ACK、断线重传、停止栅栏及文件轮转仍未实现。

主机66项测试、真实采集C模型和固件构建通过；Orbit隔离工作树76文件/1053项通过，另1跳过，类型检查和构建通过。实机子进程发现控制状态丢失，已在服务和Orbit适配层修正并补充回归。已验证真实Orbit子进程三标量Timeline/Watch/RTT/暂停继续，未安装至用户VS Code。二维数组快速表达式规划、状态监控屏障对采样的开销、有线有效采样压力、长稳与正式产品分发继续推进。

## 后续检查点：0.2.4

继续减少监控干扰：能力feature bit4的CORE_STATE命令在同一执行器内独立查询DHCSR，服务协商后使用，旧固件回退原屏障路径。真实Orbit三标量500 Hz请求五轮中位500.0001 Hz，1 kHz请求单轮996.52 Hz；标准CMSIS-DAP不被插入增强命令。真实有线USB64三订阅严格录制、无线60秒录制、严格超载暂停/新文件显式恢复均通过，详见 [0.2.4证据](subscription-checkpoint-024.json)。

69项主机测试、采集C模型及核心64/USB64的20000解析输入检查通过。补齐录制失败回调的capture代际约束和未完成失败收尾互斥，Python SDK的旧心跳不能终止重开的会话。最终独立EXE构建并运行，固件已刷写；同一版本再次通过虚拟USB完整Flash/调试及pyOCD。未安装新驱动，也未改变用户VS Code或原Orbit工作区。

## 后续检查点：0.2.5

RTT拆成可让出执行器的步骤，十变量/Watch/RTT/Orbit监控共存1 kHz请求实际五轮中位940.27 Hz，比0.2.4约提高10.9%，仍未标性能目标完成。正确性模型覆盖RdOff未知提交恢复、Timeline插入、屏障重启、环绕与读取工作量上限；真实有线/无线三订阅、严格录制和超载恢复再次通过。

真实STA断连时旧录制失败封存，旧租约终止，自动重新枚举虚拟设备；旧计划不重放，显式新建订阅有效。73项主机测试及固件构建通过；增加独立EXE离线文件校验和fsync失败后的尾部失效处理。标准虚拟USB完整验收和pyOCD通过，目标保留相同fixture。证据见 [0.2.5检查点](subscription-checkpoint-025.json)，下一步评估执行核/网络任务隔离和更长运行。
