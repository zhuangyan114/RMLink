# 当前源码核对（2026-10-13）

当前实施基座为 espressif/esp-usb-bridge。2026-10-12 的方向记录作为历史资料保留；此次选定普通 ESP32-S3 开发板及乐鑫固件基座，不再采用该记录中的旧硬件/固件候选作为默认基础。

## 引用版本

| 仓库 | 本次拉取的提交 | 用途 |
| --- | --- | --- |
| [esp-usb-bridge](https://github.com/espressif/esp-usb-bridge/tree/fc89a71a77b2c6ce1730aaa7821541616bc4b118) | fc89a71a77b2c6ce1730aaa7821541616bc4b118 | 选定基座、原生 USB Bulk、ARM CMSIS-DAP / SWD |
| [cmsis_dap_tcp_esp32](https://github.com/bkuschak/cmsis_dap_tcp_esp32/tree/cc547237006452be22fcf03e25f84cffb0de6a65) | cc547237006452be22fcf03e25f84cffb0de6a65 | TCP 帧格式、STA / NVS、网络生命周期参考 |
| [ESP32-Debugger](https://github.com/MGod-monkey/ESP32-Debugger/tree/c70aa4e0497825df69ec0b0b9f43e959bff43259) | c70aa4e0497825df69ec0b0b9f43e959bff43259 | 单端模式、mDNS、USBIP / elaphureLink 参考 |
| [Orbit](https://github.com/zhuangyan114/Orbit/tree/3928bbb6f4990ff336dfbe71f745f5f593b12e48) | 3928bbb6f4990ff336dfbe71f745f5f593b12e48 | 后端与目标所有权参考 |
| [usbip-win2](https://github.com/vadimgrn/usbip-win2/tree/07146875dd21fd3c2f1f185bb753f63974f1df71) | 07146875dd21fd3c2f1f185bb753f63974f1df71 | Windows 虚拟 USB / SDK 评估 |

固件参考仓库均保留原许可证；RMLink 保留基座 Apache-2.0 许可证和 ARM / Espressif 源文件声明。网络实现参考前两套的协议和生命周期设计，业务代码在 RMLink 中实现，不引入第二套 SWD 内核。Windows 驱动参考单独保留，尚未并入固件或安装包。

## 用户列出的发现

| 项目 | 引用版本源码证据与结论 |
| --- | --- |
| 默认 ESP JTAG | debug_probe/Kconfig 默认 DEBUG_PROBE_IFACE_JTAG；另有 sdkconfig.swd.defaults。已确认。 |
| USB mount 时初始化 | 上游 main/main.c 的 tud_mount_cb 调用 debug_probe_init。RMLink 已移至 app_main 启动；拔掉 OTG 后三次复位启动、自动联网及 TCP 查询通过，主机未枚举原生 USB，启动日志无 mount。完全断电上电待测。 |
| 专用 GPIO / 固定核任务 | swd.c 的 swd_init 创建固定核任务；debug_gpio.c 使用 dedic_gpio_new_bundle。网络必须提交请求给该执行链路。 |
| NRST 空钩子 | DAP/Config/DAP_config.h 的 PIN_nRESET_IN 返回 0、PIN_nRESET_OUT 不操作硬件；RESET_TARGET 也未实现专用复位序列。 |
| DAP 包为 64 字节 | DAP_PACKET_SIZE、DEBUG_PROBE_PACKET_SIZE、USB endpoint 缓冲与描述符均为 64；首版保留，不单独放大某一层。 |
| 第二套 TCP 8 字节头 | cmsis_dap_tcp.c：little-endian signature 0x00504144（DAP + NUL）、uint16 长度、类型 1 请求 / 2 响应、1 字节 reserved。 |
| 无线断连后重启 | 第二套 wifi.c：service_started 后断连调用 reboot()。不沿用该恢复策略。 |
| 第一套默认双端 | include/dap_configuration.h：SINGLE_MODE=0；切为 1 才走单接收端路径，当前默认调试模式为有线。 |
| 原生 CDC 是目标串口桥 | README / serial_bridge / serial_handler 路径确认；没有 USB 配网命令。 |
| 周期采样等额外能力 | 当前基座未提供 RMLink 采样计划、离散地址批量读取、缓存 / 流式回传。需要另行实现。 |

## 新发现

1. DAP.c 的 DAP_Info 固定声明 Atomic Commands，但 swd_task 只调用 DAP_ProcessCommand，未使用 DAP_ExecuteCommand，也没有 QueueCommands 的传输层队列语义。首版关闭对应能力声明，后续补齐完整语义后再开启。
2. debug_gpio.c 的 SWD 输出 GPIO 数组有两个槽位，却只写入 SWCLK；另一个保持为 0。会把 GPIO0 纳入专用输出束，首版改为只包含 SWCLK。
3. SWD reset_pins 同时重置未用的 TDI / TDO 引脚，且没有已初始化判断；首版只清理实际 SWD 引脚和 NRST。
4. 基座自定义 WinUSB interface GUID 与常见 CMSIS-DAP GUID 不同，Orbit 接入和 Windows 自动识别需要核对实际枚举条件。

## 本次实现及实测补充

- components/debug_probe/swd.c：将 USB 专用任务扩展为 USB / TCP 共用请求队列、固定核执行器和会话所有权；后台采样及控制 / 采样优先级尚未实现。
- debug_gpio.c / .h：只建立实际 SWCLK / SWDIO 束，读取 SDK 分配的通道 mask；清理只涉及实际引脚，补充 NRST 开漏输入 / 输出。STM32F103 的 SWD 读取和多轮初始化 / 清理已通过；NRST 实际复位及 SWCLK 波形尚未量测。
- DAP_config.h / DAP.c：RMLink 字符串、NRST 钩子、10 ms reset pulse；关闭未完整实现的 Atomic Commands 能力。保持 64 字节，检查 Transfer / TransferBlock / SWD_Sequence 请求和响应边界及非法标志。
- main/main.c / eub_vendord.*：启动时初始化执行器；默认关闭目标 CDC / MSC / LED，避免未确认引脚。修复单接口 MS OS 2.0 描述符作用域和 CMSIS-DAP GUID，Windows 从 Code 28 变为自动 WINUSB 绑定。可选复合接口未实板验收。
- 实测发现 reply.data 原先位于结构偏移 6，导致 USB DMA 返回错位内容；改为偏移 8 并静态检查。另用持久且对齐的 IN buffer，避免 ring item 释放后 DMA 数据失效。
- USB IN 完成回调唤醒发送任务，避免等待 10 ms FreeRTOS 节拍。8 包排队 / 800 包内容顺序测试通过；修复前约 7 秒，当前约 260 ms。设备查询耗时不能视为 SWD 吞吐或采样频率。
- main/rmlink_network.c：STA / NVS blob、mDNS、TCP 帧、USB 参数分片提交、网络状态及运行时间查询；网络恢复独立于 USB。TCP 头和响应合并发送，不引入第二套 SWD 内核。
- 网络初始化 / 配置错误使用可返回的错误处理，避免该路径主动重启整个设备；主动 STA 断连期间 USB 查询持续通过且运行时间增长。接上 STM32 后，STA 恢复期间 255 次 USB 目标读取通过、owner 保持；真正热点关闭恢复及完整调试持续性 / 长期稳定性待测。

网络文件参考 bkuschak 的 TCP wire 格式和网络生命周期、MGod 的单端 / mDNS 思路后在 RMLink 中实现；未移植它们的 SWD 内核或 USBIP 服务器。Windows SDK / 驱动数据路径结果见 windows-connector-evaluation.md。

## 构建来源

ESP-IDF v5.1.2，SDK 提交 482a8fb2d78e3b58eb21b26da8a5bedf90623213。本机 SDK 有原有修改，报告 v5.1.2-dirty；RMLink 未改 SDK。组件锁定为 TinyUSB 0.18.0~6、mDNS 1.2.5、esp-serial-flasher 1.11.0 等，完整版本 / 哈希见 firmware/dependencies.lock。

上游 SWD 基座、RMLink 默认传输配置和目标 GPIO 开启配置均已编译。用户确认接线后已烧入 target profile，GPIO 启用；新增 rmlink_swd.py、test_target_ownership.py 和只读 benchmark 脚本验证真实目标。主机 USB / TCP client 新增最多 8 包连续提交，TCP client 6 项测试通过；本轮未修改 ARM SWD 内核。源码、构建、主机、ESP32 传输和 STM32 验收分别记录于 development-status.md。

## 0.1.3 SWD 补齐与实板验收

旧传输未在活动请求期间设置内核 TransferAbort 标志。现从接收路径按活动 transport / session 原子取消，TCP RX 固定到另一核，回复独立，目标执行仍只有一套。断连使会话失效，取消并清理旧排队请求，FIFO 屏障保证回调上下文有效。

ARM DAP.c 本轮修改：取消初始化移到执行器、原子读取及 1 秒期限、TransferBlock 成功 word 之间取消、SWJ_Pins 微秒等待和正确比较单 bit。SWCLK 空输入钩子改为实际 GPIO 读回。此前“未修改 ARM 内核”仅适用于 0.1.0 阶段。

0.1.3 完成三线烧录 / 调试并恢复原 Flash；NRST 缺线、WAIT 未独立实物触发和 Windows TCP 主机兼容限制见 stm32-acceptance.md。引用仓库未改，许可 / 版权保留。
