# RMLink 0.2.0 订阅实施记录

更新：2026-10-15。本轮从 RMLink c394931、Orbit 3928bbb6f4990ff336dfbe71f745f5f593b12e48 开始，按 [设计](subscription-design.md) 实施。用户授权自主决策、ESP32 刷写及 STM32 擦写；标准 CMSIS-DAP 验收走虚拟 USB，STM32 默认保留新测试程序。旧基线和测量保持原条件。

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
