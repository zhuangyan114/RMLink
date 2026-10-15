# 0.2.13：全镜像擦除与通用组件验收

固件 0.2.13、服务／SDK 0.2.7，在 `f24e492` 缓存烧录实现上继续优化。当前 F103C8、同一全密度 64 KiB fixture、请求 SWD 4 MHz、电脑 2.4 GHz 热点、CPU0 执行器，保留 fixture，不恢复旧 Flash。

## 结果

同一版本内交替各五次，中位数：

| 调度方式 | 电脑暂存 ms | commit ms | 合计 ms |
| --- | ---: | ---: | ---: |
| 主机逐页 | 162.14 | 19646.45 | 19803.52 |
| ESP32 缓存＋完整镜像批量擦除 | 147.23 | 6074.04 | 6178.80 |

本组约 3.21 倍。合计包含电脑暂存、ESP32 上传、目标擦写、板端完整比较、主机独立 SHA256 回读和复位；额外验收回读另计。此前 0.2.12 同组为 18.552→9.119 s，属于不同时间的测量，不将两组噪声简单相减。

首轮板端阶段约 3.83 s，擦除累计 50,351 us（此前逐页约 1.60 s）、编程约 2.84 s、逐页校验 0.336 s、完整板端比较 0.500 s；主机独立校验约 1.5–1.9 s。实际 SWCLK 仍未量测，4 MHz 是请求值。

## 擦除范围与失败处理

只有 address=0x08000000、length=65536、身份／容量匹配且主 Flash 未写保护时选择批量擦除。RAM 入口 0x600 再核对地址、长度和 0x524D4C4B 调用保护值，完成后检查所有已声明主 Flash word 为 0xffffffff。其他镜像继续逐页擦除；首末部分页及其他芯片仍不在本增强 profile 内。

过程参考 [ST PM0075 Rev 2，第 2.3.3 节](https://www.st.com/resource/en/programming_manual/pm0075-stm32f10xxx-flash-memory-microcontrollers-stmicroelectronics.pdf)。未写信息块或选项字节。声明 64 KiB 不等同于承诺保留某些芯片可能存在的未公开地址空间。

能力 0x82/0 的 features bit3 表示完整镜像批量擦除（mask 从 7 扩展为 15）；其他字段、协议版本和标准 CMSIS-DAP 保持兼容。结果新增 erase_strategy=mass/page。旧固件继续 page，明确内存不足的 auto 回退继续 host/page，未知写入不回退或重放。

RAM 算法从 1464 B 增至 1856 B，原有 Init／UnInit／EraseSector／ProgramPage／Verify／BKPT 入口不变。provenance.json 记录本地新增入口和依据；生成的 C 数组现包含完整 MIT 版权／许可。

## 验证

- 97 项主机测试、原有 7 项 Node SDK 取消测试；实际 Flash C 状态机在 64／512 容量下通过完整镜像只擦除一次、非零地址局部镜像保留邻页、写保护预检不触碰目标、取消／异常／超时／不重放。64／512 PSRAM 构建通过。
- 实板缓存路径完整镜像交替重复通过。USB 上传异常及最后 1 KiB 页烧录后全镜像一致；批量擦除后取消／暂停和新作业完整恢复通过。
- 实际关闭 TCP 后约 27.40 ms 释放，USB 可接手，旧作业不能从 USB 或新 TCP 会话重放；新操作恢复完整 fixture。
- C ABI 经真实 WinHTTP 调用支持并发 cancel、409/FlashCancelled 进度及新操作恢复。小输出缓冲返回 -122 时，commit 已完成；GET /flash 可确认终态，重复 POST commit 被拒绝。调用方不能用副作用请求探测输出大小。
- 标准虚拟 USB 的 OpenOCD 完整烧录／校验／复位和调试检查、pyOCD 暂停／寄存器／继续通过；C ABI 最终复测通过首 1 KiB 页烧录后其余页保持。十分钟独立四级指针共存持续验收仍在运行，单独记录终态。

0.2.12 的 54 轮多级指针／Watch／RTT 和改向测试已经完成；不把它当成新增烧录算法或后续时钟诊断的持续采样验收。数据文件与选中二进制应以实际刷写 receipt 和每次测试身份为准。

选中固件 SHA256 `3783e63a149240d7e6a5d84c1975bb726a59d45b4f6e928ca362168c4755078f`，sdkconfig `8463372e0b97a3a20daeccae7c1a659c39c6838ba604917e6e2c3ec2bc28a3f4`；服务 EXE `03a13c81833e6b47e54da9a937c91c363a3691abb3117c1eea133ec2b9874e9d`。原始文件：`artifacts/logs/flash-host-probe-five-0213.json`、`flash-faults-usb-0213.json`、`flash-disconnect-ownership-0213.json`、`c-sdk-buffered-flash-final-027-0213.json`、`accept-virtual-usb-0213.json`、`pyocd-virtual-0213-env.json`。参考库与产物未纳入根 Git。

## 后续

继续检查内部 RAM 余量和真实 SWCLK 脉冲，公开通用设备健康／时钟诊断接口。SWD 组件本来就强制 -O2，不能假设把主应用改为 Release 就会使 SWD 提速。先测量再判断固定时序开销和 AP posted-read 处理成本；实验结果与通过的基线分别保存。
