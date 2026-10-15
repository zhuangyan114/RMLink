# 0.2.8：指针 Watch 复用与新鲜度

日期：2026-10-15。固件候选 0.2.8，连接服务／SDK 仍为 0.2.6；协议布局和公开调用没有变更。基于已提交的 0.2.7 网络恢复修正，新增 Watch 对相同指针表达式的复用与过期接管。标准 CMSIS-DAP 继续走虚拟 USB，内核驱动和 Orbit 源码本轮未改。

## 行为变化

旧版本仅复用静态、重合的 32-bit 字段，没有按 Watch 周期检查源数据年龄。Watch 的指针表达式会自行重读整条链；慢 Timeline 的静态旧结果也可能反复供给较快的 Watch。

现在匹配完整逻辑字段：根地址、指针深度、全部有符号偏移、读取宽度均相同，Timeline 的该字段有效且该帧已结束采集，选用时源样本年龄不超过 Watch 周期，才复用值。没有缓存并长期绑定上次解析到的物理地址。Timeline 正在采集下一帧、暂停、停止、结果过期或配置不匹配时，Watch 自行解析和读取；目标控制屏障清除源结果年龄，避免控制或内存写入之后继续沿用旧值。

sample flags bit2 继续表示至少一个字段复用，时间戳取本帧最早来源。采集跨度包括复用样本的年龄；不是纯 SWD 工作时间，也不是前端收到时的最大年龄。Timeline 自己仍每帧重新解析指针。服务、前端和第三方 SDK 使用相同公开接口，详细语义见 [API](api-v1.md)。

## 实现与模型

改动位于 firmware/components/debug_probe/rmlink_capture.c 的 reuse_timeline、resolve_fields、pause_all。固件版本位于 firmware/CMakeLists.txt 和 debug_probe/swd.c；tests/capture/capture_test.c 增加针对行为的检查，scripts/verify_pointer_watch.py 为真实 fixture 验证入口。

真实采集 C 源码模型通过：相同三级指针四字段复用时额外物理读数为零；超过 Watch 周期后自行读取并观察新根指针；控制屏障后观察新值；同根不同偏移或宽度不能错误复用；暂停 Timeline 后 Watch 继续。把新模型测试链接到 1fc221d 的旧采集实现时，零额外读取／复用标记断言按预期失败。79 项主机测试通过，CPU0 的 TCP64 和 TCP512 固件构建通过。

最初主机测试环境漏了 scripts 的模块搜索路径，两个模块导入失败；补齐路径后 79 项通过。首次实板脚本在服务刚启动、mDNS 尚未发现设备时返回 Device offline；设备在线后另存结果重跑通过。失败原始日志保留，不归类为固件采样失败。

## 真实目标结果

同一 ESP32-S3、STM32F103C8 fixture、GPIO4/5 SWD、CPU0、TCP512、电脑 2.4 GHz 热点。请求 SWD 4 MHz，实际 SWCLK 未仪表量测；NRST 仍缺线。Flash fixture SHA256 为 2a65c721fa1be9c4fcddba2a32bb96ccaeece439b517adb167f00ea928c031a4，测试后保留并运行。

应用镜像 SHA256：8ACF0AA665CCB3553CE06802E1E88DB4C8B45B85CACC4D490AC75549076C62F2。实际刷写配置为 firmware-target-tcp512-core0，sdkconfig SHA256 为 0401E92008615BF8F8CA8E39649894F3BDA799504DE339207112F1BA566B2211。镜像、配置和刷写收据保存在 artifacts/checkpoints/0.2.8。此前 0.2.7 的应用在刷入本版前从 ESP32 读取，逐字节长度及 SHA256 与其刷写收据相符，独立保存。

指针复用专项每阶段约两秒，四个三级指针字段、叶节点间隔 64 B：

| 阶段 | 周期 | Watch 帧 | 复用 | 自行读取 |
| --- | --- | ---: | ---: | ---: |
| Timeline 运行 | Timeline 2 ms，Watch 100 ms | 20 | 20 | 0 |
| 暂停 Timeline 并将指针改向下一叶节点 | Watch 100 ms | 21 | 0 | 21 |
| 恢复 Timeline | Timeline 2 ms，Watch 100 ms | 21 | 20 | 1 |
| Timeline 慢于 Watch | Timeline 100 ms，Watch 10 ms | 201 | 20 | 181 |

全部检查字段有效、指向预期新值，Watch 序号缺口和消费者缺口均 0，SWD failures／ring_dropped 均 0。实板标记表示选用了复用路径；零额外物理读数由模型直接计数验证。

标准虚拟 USB 的 64 KiB 擦写／校验、完整回读哈希、选项字节、寄存器／RAM、断点／单步、DWT、软件复位及异常／取消回归通过；本轮编程／校验为 5379.14 ms，只作为该次功能计时，不与历史不同网络条件推导提速。pyOCD 暂停／读 PC／继续通过。

无线三订阅严格录制 2044 帧、gaps=0、invalid_samples=0，通过独立哈希和 CRC 校验。慢消费者检查中，录制独立保留 10020 帧、零缺口；故意延迟的事件消费者报告 5972 条缺口，与录制丢失分开记录。RTT 连续替换 50 次通过，检查 167 帧／4896 B；Timeline 和 Watch 保持运行。原生 USB64 三订阅严格录制 2026 帧通过，结束后恢复 Wi-Fi 选路。

深度 0–4、共享／独立、独占／Watch+RTT 共存共 18 个两秒布局通过，指针改向、空指针、未对齐、越界、有符号偏移和 1/2/4 字节读取检查通过。十条独立四级链共存短测为 231.56 Hz，帧跨度 P50 3807 us；共享四级链、离散叶节点共存为 499.26 Hz。与此前相同类别结果接近，不能把这次去重优化写成整体帧率大幅提升。

真实 Orbit 后端子进程五轮，各采集约五秒，共享三级链、连续十变量、Watch+RTT，结果为 920.34、937.34、934.20、936.28、946.89 Hz，中位 936.28 Hz。均完成字段／RTT／暂停继续检查，退出码 0，未生成 Node fatal report。该中位低于 0.2.6 历史五轮的 958.62 Hz；没有隔离无线及主机负载差异，不能据此证明提速或确定回退原因。此前一次 0xC0000409 仍未定位，短测未复现不代表修复。未验收已安装 VS Code 界面。

## 持续采样

同一最终镜像实际运行 600.0053 秒。十条独立四级链、64 B 叶间隔、全部十字段动态变化，Timeline 请求 1 ms、Watch 四字段 100 ms、RTT 5 ms，同时每两秒查询核心状态。开始时 RSSI 为 −19 dBm 快照，未持续测量射频条件。

独立录制共 197303 帧、20833076 B：Timeline 141145 帧／235.18045 Hz，Watch 6002 帧／约 10 Hz，RTT 50156 帧／872144 B／54509 条 fixture 记录。文件 SHA256 为 57a0c7b7f586b85b9ce637bfaeebac3318667940d7d899e67fd950df57e89d1e，CRC、字段身份、有效位和 RTT 校验和均通过；独立离线重读结果相同。实时消费者缺口、板端 ring_dropped、SWD failures 均 0。

Timeline 所有帧仍有预算超限标记，录制窗口内合计跳过 459008 个计划时隙；RTT 轮询序号也有 42087 个跳过时隙，不能把轮询时隙当作 RTT 数据包丢失。录制 invalid_samples=141145 将预算超限也计入，因此不能写成该计数为零；字段有效位均完整，没有 partial 字段帧。录制 gaps 是缺口事件数，也不能直接等同于跳过时隙总数。查询状态、实时消费和最终封存不是同一采样边界，各自计数略有差异，原始值分别保留。

RTT 提交后的 byte_sequence 连续。生产端序号只出现一次跳号：15045 → 15067，跳过 21 条，位于首个 RTT 帧之后 169173 us；其余记录没有生产端跳号或重复。该开始阶段积压不等于传输字节缺口。被动串口覆盖持续测试的大部分时间及结尾，未观察到 SWD 失败、崩溃或重启；没有声称覆盖了测试开始之前的串口。

另完成空载／十条独立四级链负载各 20 次暂停与继续，以及 C SDK 40 帧两次消费、CRC 和游标续读检查。控制尾延迟仍按原始分布保留，不能据功能通过宣称 P95≤100 ms 达标。

## 边界与后续

四级独立链的 1 kHz 目标和控制长尾仍未达标。新鲜度约束不能把并发目标对象变成原子快照；Timeline 在每帧重新解析，但没有实现首尾根指针一致性重读。真正电脑重启和静默 TCP 半断连仍待验；0.2.7 的五轮 STA 重连结果不能替代它们。物理 NRST、其他目标、Keil／IAR、正式驱动签名及已安装前端界面按原状态保留。

下一项性能工作先分离板端 SWD 工作时间、执行器等待与网络／服务积压，再评估减少重复 DP SELECT／AP CSW 设置的收益。当前每个 word／block／scattered 都明确设置 SELECT／CSW／TAR，避免其他控制操作改变 MEM-AP 状态；跨工作块缓存这些寄存器之前必须设计统一失效边界，不能直接删除设置写入。

机器可读证据与原始文件 SHA256 见 [检查点](pointer-watch-028.json)。复现指针专项使用 scripts/verify_pointer_watch.py；持续检查使用 scripts/verify_subscription_soak.py --seconds 600 --pointer-depth 4 --independent --period-us 1000，离线复核使用 scripts/inspect_fixture_recording.py，均通过 --output 指定独立结果文件。既有 RMLink-0.2.6-windows-x64-dev.zip 保持不变，本轮未生成跨版本统一安装包。
