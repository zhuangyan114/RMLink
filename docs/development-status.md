# 开发与验证进度

2026-10-19 USB 连续回传候选：固件与客户端 0.3.3 增加独立 Bulk IN，保留标准 CMSIS-DAP 和旧 USB_PULL 回退。纯 PSRAM 回传约 9.00 Mbit/s，2 秒停止读取后 2026 帧恢复、无环形缓冲丢弃；194 项主机测试通过。复杂 1 kHz 长测首轮约 183 秒出现目标 SWD FAULT，回读 DP WDATAERR，原指针区未变、核心继续运行，重新初始化恢复；保留失败记录并带 UART 日志复测。最终通过范围与恢复状态以 [0.3.3 测试记录](test-release-0.3.3.md) 为准。硬件推进按用户计划先画 PCB，再验证更高 SWD；当前保持请求 4 MHz。

2026-10-18 发布后验收：GitHub v0.3.2 已标记 Pre-release。匿名下载应用和 Windows 包的摘要／清单校验通过；实板先降至 0.3.1，再从公开测试通道经 Wi-Fi 升到 0.3.2，76.094 秒完成并返回 VALID。最终 STM32 原 64 KiB 程序恢复且独立回读一致，选项字节未变；连接服务 0.3.2，Wi-Fi 在线、无 owner、未挂载。USB 100 Hz 持续 5 分钟的 51754 帧独立录制通过，零缺口／无效／超预算／缓存丢弃。首轮标签 CI 的组件管理器锁格式问题已在分支修复，官方容器三种配置编译／清单校验成功；发布固件和 ZIP 保持原摘要，具体失败与边界均见验收报告。

2026-10-18 全面测试：固件／客户端／连接服务 0.3.2，SDK 0.2.8；在保留备份后完成 USB／Wi-Fi 升降级、损坏镜像拒绝、上传复位、实际热点关闭恢复、首次启动失败自动回滚和虚拟 USB 重挂载。发现并修复原生 USB 补齐请求使 READ_MEMORY 被拒绝的问题，新增生产 C 用例并重新构建／安装实测。184 项主机测试、冻结终端及开发包预检、OpenOCD／pyOCD／Node／C SDK、三订阅与断线恢复通过。最终镜像 Wi-Fi 15 分钟混合十字段采集为 999.17 Hz，964594 帧录制独立复核通过；期限跳过、超预算及 USB64 复杂 1 kHz 压力失败均保留，见 [0.3.2 测试版验收](test-release-0.3.2.md)。正式／测试通道分开，发布标记为 Pre-release，不替代正式签名驱动或其他板型验收。下面保留各历史检查点及当时未完成事项。

2026-10-17 OTA 界面修复：用户体验发现导入页只有「Enter 返回」提示，实际还需进入另一个安装菜单。按用户要求保留固件信息停顿页，将其改为一次明确的 Enter 安装确认，q／Esc 取消；下一页从 0% 开始显示上传、校验、重连及成功状态。客户端 0.3.1 已构建和安装，181 项主机测试通过；Windows PowerShell 伪终端加隔离模拟服务验证完整可见流程与取消无上传，未代替用户执行真实安装。本机后台服务及设备仍为 0.3.0，体验用 0.3.1 应用文件保留，见[终端客户端](terminal-client.md)。

2026-10-17 用户升级体验准备：固件源码版本提高至 0.3.1，以现有功能生成独立体验构建，沿用已验证的 target／TCP512／core0／PSRAM／streamdiag／video 配置。构建、OTA 镜像完整性、版本宏、硬件指纹、分区容量及安装后 CLI 的离线识别均通过；真实文件路径已确认，导入文件为 `C:\Users\22690\Downloads\rmlink-0.3.1.bin`。准备结束时设备仍为 0.3.0／ota_1／VALID，未执行安装，留给用户在「固件升级 → 导入本地固件」体验 0.3.0 → 0.3.1。准备记录为 `artifacts/tests/ota-031-user-experience-ready.json`，资产位于 `artifacts/packages/ota-0.3.1-experience/`；0.3.1 的实际安装和启动结果待用户操作后核对。

2026-10-17 OTA 实板验收：客户端／连接服务及探针固件均为 0.3.0。用户授权后，经 COM13 先备份原 0.2.40 的 `0x0..0x311fff`，核对旧应用逐字节一致，再迁移官方回滚 bootloader、分区表、factory 与 otadata；保留 NVS 并自动连回原 Wi-Fi。安装后的 CLI 分别通过 Wi-Fi（74.641 s）和原生 USB（60.328 s）上传两份同版本、不同 ELF 摘要的镜像，实际运行分区依次为 ota_0／ota_1，均返回 VALID／boot_confirmed。占用、错误配置、取消、摘要失败和设备端错误描述拒绝通过。用户重新接好 SWD 后，两轮间 STM32 64 KiB Flash 摘要一致、核心保持运行；USB 和 Wi-Fi 各完成 100 Hz／100 帧静态 Flash 字采样，无失败或丢帧。最终恢复 Wi-Fi、无 owner；旧刷写收据已归档，当前收据更新为实测 OTA 镜像。断电／首次启动异常回滚、挂载期间 OTA 自动卸载重挂载及正式 GitHub Release 下载仍待单独验收，详见[本轮实板记录](ota-acceptance-20261017.md)。以下性能条目保留各自历史版本条件。

2026-10-17安装修复：客户端／连接服务0.2.16确认旧AppData安装文件被当前应用重定向到包私有缓存，导致用户PowerShell能找到启动器却无法找到EXE。安装、PATH、启动器、自启及日志统一迁移到USERPROFILE下的共享目录；安装器通过文件句柄真实路径检查拒绝私有缓存。153项主机测试、真实文件路径／哈希和Windows PowerShell 5.1交互启动检查通过，新后台服务已启动；保留前两次应用环境内验证未覆盖外部终端的失败记录，见[终端客户端](terminal-client.md)。

2026-10-17：新增客户端／连接服务0.2.15 [RMLink终端控制台](terminal-client.md)，提供 `rmlink` 交互入口、USB配网、设备别名／默认设备、连接和虚拟USB挂载、被动收发统计、驱动检查、诊断导出及自身固件升级预留接口。152项主机测试、Windows EXE构建、隔离状态的并发启动／重复启动／真实驱动查询等10步本机验收通过，方向键和退出恢复屏幕已验证。当前未发现在线探针，未写入Wi-Fi参数或替换驱动，真实带设备监控与配网须另行验收；以下实板性能结果保留各自原版本条件。

2026-10-16 最新开发检查点：固件0.2.40、连接服务0.2.14、SDK0.2.8，Orbit开发工作树已构建并加载。RMS3无损编码配合数据socket的显式WMM视频profile，动态十变量526秒窗口为3461.22 Hz、最大采样间隔1.926 ms、无板端丢弃/SWD失败，电脑到达仍有100–300 ms长尾，未宣称完全流畅。实时显示新增按分辨率推送尾部，历史仍查询完整记录。随后修复复位删除订阅、RMLink强制禁用main入口和暂停后探针时间戳回退：实际F5停在main PC=0x08008EC0，最终代码三轮复位/继续均保留同一十字段订阅并恢复采样；132项主机测试、Orbit78文件1085通过/1跳过、类型检查和构建通过。详细条件、失败及持续验收见[最新记录](timeline-stability-20261016.md)与[连接配置](orbit-rmlink-connection.md)。以下条目是各自历史版本的独立窗口，不代表当前任意环境稳定性。

2026-10-16十变量追加：按用户要求将RMLink板端订阅的显示分段改为相邻有效样本间隔>5 ms；序号跳过不再直接触发分段。真实旧轮十变量产生每通道222,010段并复现CSV 8 MiB队列溢出；修复后256.956秒、893,580点/通道、3477.55 Hz，只有首点初始段，最大间隔1.640 ms，无写盘溢出。另修复快速加变量时旧启动晚返回误停新配置、重启后仍选中旧历史页，增加持久化失败共享日志。最终代码真实F5烧录55,296 B约3.97秒、同owner独立回读哈希一致，十变量连续段302.880秒、1,057,538点/通道、3491.60 Hz，最大4.350 ms，无新分段/缓存丢弃/写盘停止；最终77文件1078测试通过、1跳过。中途另有一次真实SWD异常ACK导致owner失效，服务仍在线，该失败保留，不能宣称所有断连均已解决。传感器错误导致最终轮六轴为0，较早对照轮有真实传感器变化。测试会话已释放、虚拟USB在port1。源码、数据口径与边界见[本轮十变量记录](timeline-stability-20261016.md)。以下四变量及更早结果分别保留原条件。

2026-10-16 真实 Orbit 后续验收：固件候选0.2.39、服务0.2.12、SDK0.2.8；开发工作树新增 `probe:rmlink`，直接选择连接服务、板端订阅和缓存烧录，并提供唯一在线已配对设备自动选择。目标占用改为HTTP409/TargetBusy，已实测不干扰原owner。实际F5烧录55,296 B/54页、板端全量比较通过，两次约3.93/3.95秒，独立主机回读哈希一致。已复现1.8秒接收停顿使256 KiB环丢2227帧，改为2 MiB PSRAM环后同条件丢弃为0、最大采样间隔1.362毫秒。最终真实Orbit四通道、正常展开Watch持续618.7秒，每通道3051968点、4936.70 Hz，最大间隔2.120毫秒、无>10毫秒缺口或缓存丢弃/SWD失败，包含1.8秒服务暂停恢复；deadline_missed仍存在，不能称精确5 kHz或任意条件无损。验收会话已结束，虚拟USB/自动挂载已恢复。连接、Timeline缺口和显示优化及剩余稳定性问题见[本轮记录](timeline-stability-20261016.md)，使用配置见[专用连接](orbit-rmlink-connection.md)。以下0.2.28及更早条目保留为历史条件。

2026-10-16追加LibXR参考：固定`5161373`到`reference/libxr`并建立索引；三种候选均构建、刷写、RMT及同组短测。未经补偿的1145.49 Hz伴随实际时钟4.44 MHz，补偿版本未证明采样收益，已恢复原0.2.28镜像及运行源码；新保留24576组SWD逐时钟模型和完整试验记录。见[LibXR审阅](libxr-swd-review.md)。

2026-10-16追加：当前刷入0.2.28候选，服务/SDK仍0.2.8。用户指定的五条独立三级链＋五个普通字段，在72 MHz STM32与4 MHz SWD下三轮15秒中位1099.45 Hz。72 MHz已写入测试固件，脚本校验RCC并拒绝HSI回退。0.2.24/25持续测试分别在65.9秒、37.3秒、47.2秒连接失效；采样TCP发送超时是直接证据，异步DNS没有消除此问题。0.2.26缓解内部RAM压力仍发生丢帧；0.2.27分别计时发现定时唤醒不足1 ms而send等待310 ms。0.2.28扩大PSRAM中的TCP发送缓冲并仅对样本流启用Nagle，最终900.015秒三路共存验收平均1108.68 Hz、无无效数据／板端丢帧／实时消费者缺口，1063102帧录制独立复核通过；发送最长377 ms仍保留为限制。OpenOCD完整烧录／调试及原生／虚拟USB64 KiB回读通过，112项主机测试、C模型和TCP64/512构建通过。布局、完整条件及失败见[千赫兹优化记录](mixed-1khz-optimization.md)，身份见[检查点](mixed-subscription-checkpoint-0228.json)。

以下0.2.17及更早段落为历史检查点，保留当时的主频、布局与验证范围。

2026-10-16当前检查点：固件0.2.17、服务/SDK0.2.8。缓存烧录与校验模式、内部RAM压力修复、时钟校准、通用健康/失败诊断、烧录后游标和订阅恢复已完成。64 KiB默认完整比较五次中位3.623 s；标准OpenOCD一次4.753 s，比较口径分别保留。54轮各版本指针矩阵、真实Node/C取消、USB异常、TCP断连不重放、最终有线/无线三订阅和标准调试回归通过。一小时独立四级链共存为328.30 Hz，1544458帧完整录制复核通过，动态字段持续变化、无效数据/SWD失败/板端缓存丢弃/消费者缺口均0；期限跳过仍存在。

用户追加的5个三级指针＋5个普通变量，24轮测试完成：共存请求1 kHz→5 kHz时，独立链499.02→676.22 Hz，共享链951.89→1139.74 Hz，各3×5 s中位，十字段均为动态值且地址间隔64 B。5000 Hz没有作为实际达成值。源码、构建、硬件和限制见[时钟及混合采样](clock-calibration-0216.md)及[本轮报告](overnight-20261016.md)；下文保留各历史阶段原条件。

2026-10-16 新检查点：固件候选 0.2.12、服务／SDK 0.2.7，完成 ESP32 缓存镜像、单执行器异步烧录、SHA256 封存、进度、取消和断连停止。全密度 64 KiB 交替各五次中位由主机调度 18.552 s 降至板端缓存 9.119 s；主机连续读优化后单次 7.395 s，保留双重完整比较／独立哈希。USB／无线异常、跨 owner、真实断连、新作业恢复、Node 取消与 54 轮指针矩阵通过。PSRAM 分担 Timeline 环以解决内部 RAM 紧张导致的短回复突发停顿；完整条件、失败证据及后续验证见 [缓存烧录计划](buffered-flash-plan.md) 和 [公开协议](buffered-flash-protocol.md)。本轮标准虚拟 USB／C ABI／长时间采样尚待最终固件回归，下文 0.2.11 为此前基线。

已封存基线（2026-10-15）：固件／服务／SDK 0.2.6。STM32F103C8 请求 4 MHz 的虚拟 USB 完整烧录、调试、异常与取消验收通过，本轮 64 KiB 烧录／校验约 12.40 秒，独立回读／选项字节一致、新 fixture 保留。这是本轮条件下的功能计时，不作为对此前 7.11 秒的加速结论。pyOCD、原生 USB 三订阅及无线三订阅回归通过。该版本结果见 [采样性能记录](subscription-performance-20261015.md)，代码与身份见 [检查点](subscription-checkpoint-026.json)。实际 SWCLK 与物理 NRST 未量测，已安装 VS Code 界面仍待验证。

0.2.6 每帧解析指针依赖，去重共享节点，每次执行最多八个物理字读取；连续地址使用块读，其余批量指定 TAR。共享三级指针链、连续十变量的真实 Orbit 适配器短测为 498.51 → 958.04 Hz，最终五轮中位 958.62 Hz；深度 0–4、共享／独立、独占／Watch+RTT 共存的两版各 54 轮离散地址对照已完成。79 项主机测试、采集模型和协议边界检查通过，RTT 连续重建 50 次通过。最终镜像十分钟独立四级指针共存测试约 232.36 Hz，191062 帧独立校验通过，SWD failures／ring_dropped／consumer_gap 均 0；实时超期和序号跳号仍明确存在。电脑重启后的 TCP 恢复和一次 Orbit 子进程异常退出尚未解决，具体条件和已知限制见性能记录文末。

用户决定：默认请求 SWD 4000 kHz，标准 CMSIS-DAP 主线为虚拟 USB，STM32 测试后无需恢复旧程序；Watch、Timeline、RTT 全部板端订阅，优先提高 Timeline 采样率。已形成 [完整设计与 P0–P5 验收方案](subscription-design.md)，默认和恢复脚本已调整；下文历史测量和恢复记录保留原条件。

当前源码与刷入开发候选为 **0.2.11**，服务／SDK 仍为 0.2.6。帧内复用 MEM-AP SELECT／CSW，外部任务、控制屏障、每个新帧及失败均使其失效，指针和 TAR 不缓存。三轮各五秒中位，共享三级离散十字段 499.91 → 702.12 Hz，独立四级 231.68 → 247.22 Hz；实际 Orbit 子进程的共享三级连续十字段五轮中位 988.47 Hz，布局条件不同。83 项主机测试、真实采集模型及两项负向检查、TCP64／512 构建、标准虚拟 USB 完整烧录／调试、pyOCD、无线和原生 USB 三订阅、RTT 重建与 18 种指针布局通过。

0.2.11 首轮十分钟因 Timeline／RTT 缓存压力计数失败；未改变固件，两分钟复测及第二轮十分钟通过。后一轮十条独立四级链与 Watch／RTT 共存为 **248.93 Hz**，209672 帧录制独立哈希／CRC／字段检查一致，板端丢弃／SWD failures／consumer_gap 均零；十个动态叶节点各有 54453 次变化。期限跳过仍存在，首轮失败根因未明，不能称为稳定无损或持续 1 kHz。证据、二进制身份与各轮边界见 [0.2.11 记录](memap-reuse-0211.md)；下一步优先查明发送／接收／缓存停滞，再继续优化访问成本与控制长尾。

以下 0.2.7–0.2.10 段落保留该阶段结果和恢复状态。

后续 0.2.7 增加 TCP 保活和 STA generation，避免瞬态 down/up 掩盖旧连接失效。健康空闲 20 秒、五轮网络重连期间 USB owner 保留、连接服务恢复、虚拟 USB 全流程／pyOCD／三订阅回归通过。Orbit 子进程交替矩阵／三级指针共 20 次短启动通过，但此前 0xC0000409 原因未查明，不能说已修复。真正电脑重启、静默半断连未进行，见 [网络恢复检查点](network-recovery-027.md)。

0.2.8 阶段，服务／SDK 仍为 0.2.6：相同多重指针字段的 Watch 复用、过期接管、暂停／改向／恢复和控制屏障失效通过模型及实板检查；79 项主机测试、TCP64／512 构建、虚拟 USB 全流程、pyOCD、无线／原生 USB 三订阅和 RTT 重建通过。真实 Orbit 五轮共享三级链中位 936.28 Hz，低于 0.2.6 历史 958.62 Hz，未宣称本改动提高总体帧率。条件、持续采样状态与证据见 [0.2.8 记录](pointer-watch-028.md)。

0.2.8 最终十分钟独立四级指针共存采样为 235.18 Hz。197303 帧离线复核的哈希／CRC／字段有效位一致，十个动态叶节点各观测到 54449 次变化；采样频率与 fixture 约 91 次／秒的观测值变化分别记录。新增冻结字段检测与计数器回绕检查后 81 项主机测试通过。

随后 0.2.9 批次均衡 A–E 试验没有采用：模型把 50 word 的十个板内批次减至七个，独立三级／四级链提高，但共享二级链多轮回退。完整 54 轮对照及各 12 轮重点复测、验证范围和补丁见 [试验记录](experiments/balanced-pointer-scheduler.md)。默认源码及实际镜像已恢复 0.2.8，恢复后 Watch／pyOCD、完整 Flash 回读和 Running 状态复核通过；没有 0.2.9 十分钟验收或安装包。下一步先分离 SWD 工作与执行器等待。

耗时分解已完成：普通 0.2.8／0.2.10-profile／恢复 0.2.8 各 15 轮，静态、共享二级／三级、独立三级／四级链均与 Watch／RTT 共存。计时版独立四级帧内均值 3880.70 us，其中 DAP 路径 3593.68 us（92.60%），工作段之外 63.40 us；这包含抢占，不能当作纯总线时间。诊断会影响临界布局，保留三版采样率和补丁，见 [分解记录](experiments/capture-path-profile.md)。诊断版未成为默认版本；恢复后的 Watch／pyOCD、完整 Flash 哈希、Running 与 owner 释放复核通过。下一步评估 MEM-AP 前置寄存器状态复用及统一失效规则。

## 2026-10-14 及以前的阶段记录

以下保留当时条件：固件 0.1.6，连接服务 0.1.4，TCP / 无线虚拟 USB 512 字节、原生 USB 64 字节。128 / 256 / 512 三档编译、刷写、启动和实板验收完成；最终原生 USB 和虚拟 USB 的完整烧录 / 校验 / 三线调试通过，当时恢复了原 Flash。直接 OpenOCD TCP 功能完成但仍有旧版 Windows socket 错误，NRST 缺线。历史性能和范围见 [提速结果](performance-results.md)。

最新提速检查点：流水线每包独立携带 TAR 和读写，修正首版地址缓存在部分完成时可能错位的问题。相同会话中各五次交替使用 64 / 512 字节内存块预算，64 KiB 回读约 6.29 → 2.45 秒，命令 1152 → 192，批次 144 → 24。39 项主机测试、7 项 TCP peer 测试、四档 C 解析边界 / 随机输入检查通过；服务协商容量并去除已知命令的零填充，SDK 8 个完整 512 请求通过。原生 USB 全流程约 3.35 秒，虚拟 USB 约 7.28 秒，均无 Error 行；这是功能验收计时。聚合等待窗口默认 0。时钟扫描已完成只读检查，该检查点测试使用请求 2 MHz；后续默认改为 4 MHz 的决定见上文。完整提速历史见 [提速计划](performance-plan.md)。

本轮新增：修复 pyOCD 在 Connect 前设置时钟的兼容性，固件 / 服务均从有效时钟配置开始保留整段会话；无效配置不占用目标，其他客户端不能抢占。本地测试签名的 UDE 取消长度修补已加载，虚拟 USB 的空读超时、零字节计数、缓冲不变及后续正常查询均通过，pyOCD 0.45.1 经虚拟无线探针完成目标识别、暂停、读 PC 和继续运行。OpenOCD 在固件 0.1.5 / 服务 0.1.1 / 修补驱动组合下完成 64 KiB 烧录与三线调试回归，原 Flash 恢复并独立回读一致；API / 虚拟 USB 互斥、STA 断连自动恢复及重挂载后的 pyOCD 再连接均通过，见 [驱动取消问题](virtual-usb-cancellation.md)。

## 阶段状态

| 阶段 | 源码 / 实现 | 构建 / 主机验证 | 实板状态 |
| --- | --- | --- | --- |
| 1：基座与环境 | 五个参考仓库固定提交；独立乐鑫基座；脚本、板级 profile、组件锁定和 CodeGraph | 上游基座、RMLink 默认、目标 GPIO 开启配置均编译通过 | S3 rev0.2、16 MB Flash、8 MB PSRAM 已读出；完整原 Flash 已备份；PSRAM 未启用 |
| 2：USB / 目标 SWD | Bulk、统一执行器、会话规则、NRST 钩子、边界和主机排队读取已实现 | target profile 已编译并刷写；主机核心通过 | USB / TCP 的 64 KiB 烧录与回读、暂停 / 继续、寄存器 / RAM、断点 / 单步、DWT 和软件复位通过；NRST 缺线 |
| 3：STA / TCP / USB 配网 | NVS、自动入网、mDNS、分片帧和网络独立恢复已实现 | 主机 TCP client 6 项测试通过 | 配网 / STA / TCP / 无 OTG 复位通过；STA 恢复时 USB 的真实目标读取与 owner 保持通过；完全断电及真正热点掉线待测 |
| 4：Orbit / 板端采样 | 已核对 Orbit 所有权链；新增本机 API / Node SDK；实际 Orbit 与采样尚未接入 | SDK 经真实服务完成连接、批量查询和释放 | API 读 STM32、与虚拟 USB 双向互斥通过；板端采样未验收 |
| 5：Windows 自动组件 | mDNS、配对、一个物理 TCP、USB/IP 代理、自动重挂载与 UDE 驱动控制 | 独立 EXE 构建、19 项主机测试通过；UDE 补丁构建与测试签名通过，已实际加载 | 空读取消、pyOCD 虚拟调试、OpenOCD 烧录调试及双向互斥通过；本轮 STA 断连约 6.53 秒恢复，重挂载后的空读与 pyOCD 通过；正式驱动签名仍待完成 |

当前 target profile 为 firmware-target-tcp512，固件 SHA256 D8EBDDD7507C33B830AE8A0AC0188788F61DD752871CECBAA36E6BB66809831F。0.1.6 扩大 TCP / 共享核心容量，保留原生 USB64 和 SWD 位操作内核；最后刷写记录在 artifacts/hardware/rmlink-last-flash.json。0.1.5 / 64 字节基线保留于 artifacts/hardware/speed64-baseline/；其他历史固件及本轮连接服务旧版也保留。最新证据在 artifacts/logs/speed-packets-20261014/。

本次驱动回归证据统一在 artifacts/logs/test-driver-20261014：虚拟 64 KiB 烧录 / 校验 12845.70 ms，独立完整回读 8120.89 ms；原程序恢复 12288.24 ms，选项字节未变，核心运行、板端与服务 owner 均已释放。用户已完成测试模式重启，当前 Secure Boot 关闭、TESTSIGNING 生效、HVCI 保持开启；安装仅重启虚拟控制器，无额外系统重启要求。空读脚本带进程期限并精确选择虚拟序列号，历史验收日志保留。正式微软签名、物理 NRST 及其他调试软件兼容性另行验证。

新增异步 TransferAbort、TCP 独立收发、断连取消 / 排队清理、传输期限、未实现命令 0xFF 响应和 SWJ 引脚监测 / 等待修正。唯一 SWD 执行器与整段会话所有权保留。

以下测速、首轮只读与无 OTG 启动内容是历史证据，对应各段注明的 0.1.0 / 0.1.1，不代替当前版本验收。

## ESP32 / 电脑传输专项

本轮按用户要求只测 ESP32 / PC，未调用 SWD 或占用目标。0.1.1 增加 64 字节可校验响应和临时直接 TCP 测试口，ARM SWD 内核未改。每条延迟路径 1500 个有效样本；直接 TCP 回传 5 轮各 4 MiB，全部内容校验通过，设备未重启。结果及重跑方法详见 [传输专项记录](transport-benchmark.md)。

| 通道 | 有效吞吐中位值 | RTT 中位数 | RTT P95 |
| --- | --- | --- | --- |
| USB 64 字节 DAP 通道 | 289.27 KiB/s | 0.1910 ms | 0.2721 ms |
| Wi-Fi 64 字节 DAP 通道 | 18.65 KiB/s | 3.3384 ms | 54.8789 ms |
| Wi-Fi 直接 TCP 连续回传 | 5.134 Mbps，约 0.642 MB/s | 2.3086 ms | 44.1034 ms |

直接 TCP 各轮 4.542～6.926 Mbps；Wi-Fi 有明显延迟长尾。以上是当前应用与主机路径的有效吞吐 / 往返延迟，未量测单程延迟或硬件链路极限。测试服务结束后关闭，自动到期关闭已验证；USB / TCP 标准设备查询回归通过。新增固件未在本轮重新访问 STM32，目标只读的实板证据仍为 0.1.0。

## STM32 首轮只读验收

2026-10-13，用户报告目标为 STM32F103C8T8，按 GPIO4→SWDIO、GPIO5→SWCLK、GPIO6→NRST、GND 共地连接，并接回原生 OTG。已重新构建 / 刷写 target profile，串口启动正常，USB / TCP 均报告 targetPinsConfirmed=true。

| 读取内容 | USB / TCP 结果 |
| --- | --- |
| SWD DP IDCODE | 0x1BA01477 |
| DP CTRL/STAT，调试供电请求 / 应答 | 0xF0000040 |
| AHB-AP IDR | 0x14770011 |
| Cortex-M CPUID | 0x411FC231，对应 Cortex-M3 |
| DBGMCU_IDCODE | 0x00006410，DEV_ID=0x410 |
| Flash 容量寄存器 | 64 KiB |
| 唯一 ID 和 Flash 向量表 | 两个传输一致；初始 SP=0x20005000，复位向量=0x08001A81 |
| 100 次 CPUID 内存读取，100 kHz 设置 | USB 458.56 ms；TCP 2660.02 ms，全部通过 |

两条路径读取时均观察到核心未暂停，NRST 输入为高；未下发暂停 / 复位命令。工具会配置 CoreSight DP / AP 调试寄存器，不写目标 RAM / Flash。芯片信息与用户型号及 [ST STM32F103x8 数据手册](https://www.st.com/resource/en/datasheet/stm32f103c8.pdf) 的 Cortex-M3 / 64 KiB 配置相符，不能据此独立认证芯片真伪或完整料号。供电电压、实际 SWCLK 频率和 NRST 波形未仪表量测。

三轮会话测试通过：USB 占用时 TCP 的 Connect / Transfer / Disconnect 被拒绝；TCP 占用时 USB 同样被拒绝，现有 owner 不变。正常 Disconnect 释放会话；TCP socket 关闭但未主动 Disconnect 时，板端仍清理会话，USB 可重新接管并读 CPUID。最后 owner 为 0，无遗留占用。

另一次 STA 主动断连恢复约 3934.52 ms，期间同一 USB owner 下 255 次目标 CPUID 读取全部通过，最大单次约 22.11 ms；owner 不变、ESP32 运行时间增长。网络恢复后 TCP 仍不能抢占 USB，USB 释放后 TCP 可继续读取。此结论是保持只读目标访问，完整调试会话、热点实际关闭恢复尚未验收。

## 同目标 Flash 读取对照

先通过 USB 读取 64 KiB 主 Flash 到 artifacts/hardware/stm32f103c8-before-rmlink.bin，SHA256 75bcca6e52d436f377542beb94629ef101d153e214ef085b6af7893d40b347ff。备份不包含选项字节、RAM 或系统 ROM，恢复尚未验收；仅本地保留。

使用同一 target 固件、同一目标和主 Flash 起始 4096 字节，顺序请求与最多 8 个标准 DAP 包连续提交各读三次，内容均与备份相同。下表为三次主机计时的中位吞吐：

| DAP 请求时钟 | USB 顺序 | USB 8 包连续提交 | Wi-Fi 顺序 | Wi-Fi 8 包连续提交 |
| --- | --- | --- | --- | --- |
| 1 MHz | 18.38 KiB/s | 19.60 KiB/s | 1.71 KiB/s | 2.98 KiB/s |
| 2 MHz | 31.54 KiB/s | 35.09 KiB/s | 2.18 KiB/s | 8.17 KiB/s |

另一次时钟阶梯测试中，100 kHz、500 kHz、1 MHz、2 MHz 在 USB / TCP 下各三次顺序读取均通过。无线计时存在明显波动，以上不是保证速度、烧录速度或采样率。实际 SWCLK 未量测；主机提交按 TAR + TransferBlock 成对排队，单包最多读 15 word，并处理 1 KiB 地址递增边界。板端周期采样及更大的定制批量命令尚未实现。

## 当前 ESP32 设备实测

- COM13 为 CH343 下载 / 日志口；原生 OTG 应用 VID303A / PID1002、序列号 50787D15A924、Bulk OUT03 / IN83。
- Windows 自动绑定 WINUSB，设备状态 OK；本次没有另行安装 USB 驱动。USB ID 暂沿用基座用于本机原型，正式分发前另行确认可使用的 ID。
- 热点已开启且为 TwoPointFourGigahertz；凭据通过 USB 保存，后续刷写及串口复位后仍自动加入。
- 当前 DHCP 地址 192.168.137.151，rmlink-15a924.local 在 Windows 成功解析。
- TCP 分片、连续帧、无效标识 / 长度 / 类型 / reserved 拒绝、部分头超时后新连接恢复通过；TCP 配网被拒绝。
- 最新三次 USB 命令主动断开 STA 的恢复耗时约 7.18 / 2.68 / 2.87 秒。期间同一 USB 句柄累计 612 次查询通过，最大单次约 5.64 ms；设备微秒运行时间持续增长，未观察到 MCU 重启。主机旧流为关闭或复位，新 TCP 连接可继续查询。

最后一项验证的是设备主动断开 / 自动重连，不等同于电脑热点关闭恢复，也不等同于 STM32 USB 调试不中断。

## 无原生 USB 枚举的无线启动

2026-10-13 17:52（Asia/Shanghai），用户拔掉直通 USB&OTG 线，只保留 CH343 供电 / 日志。主机检查确认只有 CH343 COM13，RMLink 原生 USB 设备未枚举。通过 CH343 RTS 复位三次，分别捕获启动日志；每次仅有一次 ROM 启动，无 USB mount / endpoint / 发送任务日志，无崩溃记录。

| 复位次数 | 日志中取得 IP 的启动时间 | TCP 连续查询 | 100 次查询耗时 |
| --- | --- | --- | --- |
| 1 | 1962 ms | 100 / 100 通过 | 837.64 ms |
| 2 | 7652 ms | 100 / 100 通过 | 1130.81 ms |
| 3 | 2072 ms | 100 / 100 通过 | 1013.78 ms |

每次从已保存参数自动加入热点，rmlink-15a924.local 可访问，网络状态 configured / connected 均为 true，IP 192.168.137.151。未重新配网或刷写固件，目标 GPIO 仍关闭。这证明当前无线启动 / 执行器不依赖原生 USB 枚举；CH343 仅承担供电及复位 / 日志。此次是复位启动，尚未完成完全断电再上电测试，亦未验证 STM32 SWD。

## 初步传输计时

同一当前固件的设备查询，未执行目标 SWD：

| 测试 | 请求量 | 最新单次结果 |
| --- | --- | --- |
| USB 逐请求 DAP_Info | 100 | 15.93 ms |
| Wi-Fi 逐请求 DAP_Info | 100 | 824.17 ms |
| USB 8 包排队，混合静态信息 | 800 | 260.09 ms，内容 / 顺序通过 |
| Wi-Fi 每批连续发送 8 帧 DAP_Info | 800 | 2247.44 ms，内容 / 顺序通过 |

计时已改为 Python perf_counter；早期日志中的 monotonic 在该 Windows Python 环境有粗粒度，不能用于微秒延迟判断。批量 USB 修复前 800 包约 7 秒，主要受发送等待节拍影响，旧证据保留。

这些是早期 GPIO 关闭版本的设备查询计时，不是目标读取或完整基准。当前目标 Flash 读取计时另见上表；Wi-Fi 原始带宽、真实 SWD 时钟、目标写入、烧录校验、变量采样率、丢样和完整控制延迟尚未验收。

## 验证证据

| 路径（相对项目根目录） | 证据层次 |
| --- | --- |
| artifacts/logs/build-upstream.log | 未改乐鑫基座构建 |
| artifacts/logs/build-rmlink.log / flash-rmlink.log | 早期 GPIO 关闭配置编译与刷写、hash 校验 |
| artifacts/logs/build-target-profile.log / flash-target-profile.log / boot-target-profile.log | 当前 GPIO 开启配置构建、刷写 hash 校验及启动；首轮干净构建为 1002 步，后续为增量 |
| artifacts/logs/boot-network.log | 早期配置启动、16 MB Flash、STA 自动加入、IP 与 USB mount |
| artifacts/logs/test-core.log | 主机 owner / 8 字节头 / 边界及 20000 随机报文 |
| artifacts/logs/test-tcp-client.log | 独立主机 TCP peer 的 6 项测试，包括连续提交与整批发送前验证 |
| artifacts/logs/provision-hotspot.log | 热点频段、USB 保存、加入；无密码输出 |
| artifacts/logs/verify-device-transport.log | 早期 GPIO 关闭配置的 USB 800 包、TCP 800 帧、无效帧及恢复 |
| artifacts/logs/verify-swd-usb.json / verify-swd-tcp.json | 当前 target profile 的真实目标信息、向量表、100 次内存读取和 owner |
| artifacts/logs/verify-target-ownership.json | 三轮会话互斥、TCP 关闭释放、USB 重接管和 STA 断连期间目标读取 |
| artifacts/logs/benchmark-swd-reads.json / benchmark-swd-pipeline.json | 同目标时钟阶梯与顺序 / 连续提交的 Flash 读取对照 |
| artifacts/logs/benchmark-transport.json / benchmark-transport-run.log | 0.1.1 ESP32 / PC 的全部 RTT 样本及 DAP / 直接 TCP 吞吐 |
| artifacts/logs/build-transport-bench.log / flash-transport-bench.log / boot-transport-bench.log | 当前传输诊断版本的构建、刷写和正常启动 |
| artifacts/logs/verify-transport-bench.json / verify-usb-after-transport-bench.log / verify-tcp-after-transport-bench.log | 诊断控制 / 边界 / 自动关闭及标准查询回归 |
| artifacts/logs/verify-flash-backup.json | USB / TCP 分别重读完整 64 KiB 并与备份逐字节比对 |
| artifacts/logs/verify-swd-final-state.json | 最终只读目标检查：GPIO 开启、核心未暂停、Disconnect 后 owner=0 |
| artifacts/logs/verify-wireless-startup.json / verify-wireless-startup-run.log | 无 OTG 枚举、三次 CH343 复位、保存参数自动入网及 300 次 TCP 查询 |
| artifacts/logs/boot-wireless-1.log / boot-wireless-2.log / boot-wireless-3.log | 三次无原生 USB mount 的完整串口启动日志 |
| artifacts/logs/verify-device-before-usb-notify.log | USB 节拍修改前；旧计时及旧流状态字段仅供历史对照 |
| artifacts/logs/verify-usb-before-dma-fix.log / usb-raw-diagnostic.log | 早期失败证据，保留追溯 |
| artifacts/validation-snapshot.json | 引用提交、产物哈希、sdkconfig 与源代码状态 |
| artifacts/hardware/esp32s3-before-rmlink.bin | 修改前完整 16 MB 备份，仅本地保留 |
| artifacts/hardware/stm32f103c8-before-rmlink.bin | STM32 64 KiB 主 Flash 只读备份，仅本地保留 |

reference/ 保留上游源码，CodeGraph 元数据不计为实现改动。产物和日志统一在 artifacts/，缓存不放根目录或 scripts/。这描述早期固件工作区；现已迁入根目录统一 Git，本地提交不推送。原基座首次构建目录在 Codex 工作区；后续 RMLink 构建统一使用项目内绝对路径。

## 下一验收点

三线常规烧录 / 调试已通过，原程序和选项数据核对完成。补充 NRST 实体接线、真实 WAIT、USB 物理拔出、ESP32 完全断电上电、实际热点关闭恢复、SWCLK 波形和长期压力测试。Windows TCP 默认排队模式需采用修正 socket 处理的后端，再验收。

继续接入 Orbit，补充板端采样、缓存和控制优先级；Windows 自动连接组件独立推进。项目根目录现统一 Git 管理，reference/、artifacts/ 与生成依赖排除，原 firmware/.git 已归档，见 [仓库管理](git-management.md)。
