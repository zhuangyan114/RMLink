# SWD 8 MHz 实板试验（2026-10-16）

结论：当前开发板、接线和固件0.2.40未通过8 MHz请求下的完整调试验收。实际时钟确实提高，但烧录和持续采样均失败；已恢复4 MHz日常配置和正常运行的Orbit会话。本轮没有修改生产固件或后端代码，没有提交/推送。

## 条件和时钟证据

ESP32-S3→STM32F103C8，同一应用镜像55,296 B，SHA256 `df40eb098698b8648241b7c31090e44a942a53796c4ae41e735d6593c588de77`。读取当前ELF的PT_LOAD段并按Orbit的1 KiB页边界及0xff填充方式重建，确认与4 MHz目标Flash回读一致。普通objcopy按section输出的填充不同，未将其不同的SHA当成目标固件变化。

Orbit使用probe=rmlink→连接服务0.2.14→同一板端SWD执行器。实验原活动会话为`080bb542-1d63-46a8-9ae7-ebe24953d67e`。保存当前4 MHz基线后，结束该会话，在没有其他owner时单独通过公开API测量；测量owner关闭后才重新启动Orbit。没有旁路访问活动DAP owner。

通过固件已有诊断，在现有SWCLK引脚使用ESP32-S3的80 MHz RMT输入采集，每种模式各3次：

| 请求值 | SWJ序列脉冲周期对应频率 | 普通DP读取脉冲周期对应频率 |
| --- | ---: | ---: |
| 4 MHz | 3.478 MHz | 3.810 MHz |
| 8 MHz | 6.667 MHz | 7.273 MHz |

这是诊断路径内完整高低周期的数字测量，不是外部示波器测量或模拟信号完整性保证。每个时钟下100次CPUID读取、暂停后的4字节原值RAM写入/回读、55,296 B Flash独立回读均通过，目标运行状态保持。证据`artifacts/logs/orbit-timeline-20261016/swd8-pad-test.json`。仅这些短检查不足以证明长时间或烧录稳定。

## 8 MHz失败与恢复

1. 临时把实际配置`Orbit RMLink Enhanced`的speedKHz改为8000，真实F5开始烧录后返回`OutcomeUnknown: Probe Flash ended in failed, error=8`。固件枚举中8为RM_FLASH_SWD。失败DAP日志保留为`swd8-flash-failed-dap.log`、启动输出为`swd8-entry-output.log`，没有自动重复该8 MHz编程操作。
2. 在无其他owner时以4 MHz读取状态和完整Flash：目标停在RAM算法PC=0x20000500，Flash SHA变为`f9858690dba6f8d140e82db445a30f87cae09ad7e97456640d2cc88f572c374c`，确认本次编程已部分改变Flash。故不能只说“连接失败、目标未动”。见`swd8-recovery.json`。
3. 使用4 MHz明确重新烧录此前读回且SHA核对一致的同一镜像；板端完整比较、主机独立完整回读均与原SHA一致，选项字节核对通过，然后reset/run恢复应用。该恢复烧录约4026.96 ms，见`swd8-reflash4.json`。没有修改用户应用源码。
4. 为分开观察烧录和采样，在应用已运行时启动一个仅运行时覆盖8 MHz的Orbit会话：flashBeforeDebug=false、runToEntryPoint=false，未将这两个覆盖写入日常配置。会话`1d98677f-a6b5-4907-b147-07f9593f8c76`连接成功，但十变量仅得到1517点/通道、首尾311.002 ms后停止，板端subscription记录failures=1、stop_reason=swd_failure。随后DAP因TargetOwnerUnavailable结束。此短段不能作为稳定采样率或有效提速结论。
5. 该8 MHz故障的设备诊断保留requested_operations=11、completed_operations=0、dap_status=4（FAULT）、elapsed_us=5、abort_reason=none、target_write=false；另一次失败使计数到2。它表明该轮失效发生于SWD目标访问，不能仅凭此确定是杜邦线、目标芯片上限或固件时序中的哪一项。见`swd8-8000-health-start.json`、`swd8-8000-health-failed.json`、`swd8-8000-run.json`、`swd8-no-flash-dap.log`。

早期一次4 MHz DAP表达式辅助检查在暂停后返回`running`且没有memoryReference，因此该辅助脚本在任何RAM写入前退出；保留`swd8-4000-memory.json`。后续上述公开API的原值RAM写入/回读另有实际成功证据，不将前一脚本算作通过。另有误复制的旧入口快照已标为`swd8-stale-entry-copy-invalid.json`，不用于本次入口结论。

## 最终4 MHz复核与保留状态

实际launch.json与试验前备份的SHA256完全一致，speedKHz仍为4000。随后真实F5再次烧录并完整比较通过，DAP stackTrace确认main入口；最终会话`d94a25fa-ac43-4867-b26a-550539ee5754`继续运行。其相同十字段为rpy_deg三项、abb、accel_g三项、gyro_dps三项，目标间隔0.2 ms（5 kHz目标），前端发送15 ms。

恢复后的首尾探针跨度约63.801秒，每通道220,569点，最大样本间隔1.563 ms，未出现>10 ms缺口；板端当前capture_id=3、failures=0、ring_dropped=0、stream_error=null。先前设备诊断中的8 MHz故障计数保留，不把历史故障计数误报为本段4 MHz失败。见`swd8-final4-entry.json`、`swd8-final4-run.json`、`swd8-final4-health.json`。试验前短基线约47.918秒、164,934点/通道，见`swd8-4000-run.json`；两段均为约3.4 kHz量级，不将0.31秒的8 MHz失败片段与其作稳定性能比较。

本轮仅做配置/硬件试验，没有新增实现或重新执行全量单元测试、ESP构建。未测试6 MHz、其他接线、目标芯片或其他变量组合；未重新确认当前应用实际主频。用户原有的活动调试已恢复为4 MHz的可见Orbit会话，保留运行供后续使用，验收监听器已释放。当前不能将“8 MHz请求可设置”描述成“8 MHz烧录及调试稳定可用”。
