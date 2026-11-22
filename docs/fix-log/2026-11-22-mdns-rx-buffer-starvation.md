# 修复高负载采样下 mDNS 饥饿耗尽 Wi-Fi 动态 RX 缓冲导致的无线卡顿与断联（固件 0.3.6）

## 日期与用户确认

- 确认日期：2026-11-22。
- 用户确认：“连续稳定运行20分钟，手动验收通过”。
- 本次记录覆盖升级至 `0.3.6` 固件的 RMLink V1 探针 `b81f3fc30470`（接入 STM32H723VGT6 只读 SWD）上的实板回归与用户手动验收；未升级的其余设备（`cc68c7a5995c`、`84c7bb788408`、`10bda310a850`）、非 PSRAM 固件配置与上位机安装包本次未实测，上位机连接器版本仍为 `0.3.5`。

## 问题描述

无线连接探针并开启高频 Timeline 采样（如 200 us / 10 MHz 或 1000 us / 4 MHz 三级指针链）约 50–110 秒后，探针与主机之间出现持续数秒至十余秒的无线下行接收黑洞：

- **3244 采样流**：探针发往主机的采样数据不断触发重传，主机侧收不到新数据并在 2.5 s 后报 `Realtime capture stalled for 2.5s`（旧探针 `cc68c7a5995c` 在 `0.3.3` 下也复现过该 3244 停流，表现为板端未确认数据堆积与发送缓冲耗尽）；
- **3241 控制通道**：主机发往探针的 3241 控制请求与心跳在 TCP 层反复重传 6 秒无响应，触发 `TimeoutError`，Orbit 因失去设备 owner 退出调试会话；
- **独立链路表现**：同期 ICMP ping 探针连续超时，但 USB 同步取证确认探针未重启、未触发 Wi-Fi 断链事件、片内与 PSRAM 空闲内存充足。

## 根因分析

通过独立 2.4 GHz 手机 AP 对照、板端 `lwip_stats` / `__wrap_esp_netif_receive` / `3241 PCB` 同步取证，以及纯 CPU 与活跃 SWD 单变量对照，最终定位到完整因果链：

1. **Core 0 上 `swd_task` 连续运行饿死同核 `mdns` 任务**：
   - `firmware/components/debug_probe/swd.c` 中 `swd_task`（`rmlink_executor`）固定在 Core 0（`CONFIG_RMLINK_EXECUTOR_CORE=0`），优先级为 `5`。当实时采样任务过载饱和（单帧耗时超过周期、`due <= now`）时，`rmlink_capture_run()` 持续返回 `true`，`swd_task` 连续执行不进入 `ulTaskNotifyTake()` 休眠，导致 Core 0 上所有优先级 `< 5` 的任务得不到调度。
   - 全固件中 `rmlink_tcp`、`rmlink_stream` 已绑在 Core 1，`tcpip_thread` 为无核亲和，但 `espressif__mdns` 保持默认配置 `CONFIG_MDNS_TASK_PRIORITY=1` 与 **`CONFIG_MDNS_TASK_AFFINITY_CPU0=y`**，成为唯一绑死在 Core 0 的低优先级网络任务。
2. **Raw LwIP mDNS 队列钉死全部 16 个 Wi-Fi 动态 RX 缓冲**：
   - `firmware/managed_components/espressif__mdns/mdns_networking_lwip.c` 使用 LwIP Raw UDP 接收回调 `_udp_recv()`（运行在优先级 18 的 `tcpip_thread` 上，不受 Core 0 饥饿影响）。每收到一个 `224.0.0.251:5353` 组播包，`_udp_recv()` 不立即释放 `pbuf`，而是将零拷贝持有 Wi-Fi 动态 RX 缓冲区的 `packet->pb` 送入 `_mdns_server->action_queue`，直到 `mdns` 任务消费后才调用 `pbuf_free()` 归还。
   - `sdkconfig.rmlink.defaults` 与 `sdkconfig.rmlink-psram.defaults` 此前将 `CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM` 下调为 **`16`**，恰好等于 `CONFIG_MDNS_ACTION_QUEUE_LEN=16`。
   - 局域网持续发来的 mDNS 组播包在 50–110 秒内填满 15–16 个槽位，将全部 Wi-Fi 动态 RX 缓冲钉死在 `action_queue` 中。此后 ESP32-S3 仍能在 802.11 硬件层回复 ACK，但 `ppTask` 无法分配动态 RX 缓冲，下行帧在进入 `esp_netif_receive()` 前被静默丢弃（板端 `lwip.*_drop = 0`、堆内存充足、Wi-Fi 未断链）。

此前已验证排除的方向也全部与该根因吻合：独立手机 AP 仍复现（非 Windows 热点）；纯 CPU 100% 占满 Core 0 无需任何 SWD/GPIO 即复现（非 RF EMI 或 `rmlink_capture`）；静态字段 200 us / 10 MHz 以 5.7× 帧率和 2× Wi-Fi 吞吐通过（因每帧结束后 `swd_task` 休眠，`mdns` 能及时消费队列）；早先“分核对照”把 Wi-Fi 任务迁到了 Core 1 而 `swd_task` 与 `mdns` 仍留在 Core 0；早先“静态 RX 缓冲 10 改 16”未改变动态 RX 缓冲上限。

## 修改方案

在保持 SWD 采样路径零性能损失的前提下做两层修复，并将固件版本号从 `0.3.5` 升至 **`0.3.6`**：

1. **解除 `mdns` 与 `swd_task` 的同核饥饿**：
   - 在 `firmware/sdkconfig.rmlink.defaults` 中将 `CONFIG_MDNS_TASK_PRIORITY` 提至 `4`（与 `rmlink_wifi`/`rmlink_tcp` 一致），默认核亲和改为 `CONFIG_MDNS_TASK_AFFINITY_NO_AFFINITY=y`；
   - 在 `firmware/sdkconfig.rmlink-core0.defaults` 中将 `mdns` 显式绑到 CPU1（`CONFIG_MDNS_TASK_AFFINITY_CPU1=y`）；
   - 在 `firmware/sdkconfig.rmlink-core1.defaults` 中将 `mdns` 显式绑到 CPU0（`CONFIG_MDNS_TASK_AFFINITY_CPU0=y`）。
2. **打破 `mdns` 队列与动态 RX 缓冲池的 1:1 死锁上限**：
   - 在 `firmware/sdkconfig.rmlink.defaults` 中将 `CONFIG_MDNS_ACTION_QUEUE_LEN` 降至 `8`（小于非 PSRAM 下 `DYNAMIC_RX_BUFFER_NUM=16`）；
   - 在 `firmware/sdkconfig.rmlink-psram.defaults` 中将 `CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM` 提至 `32`（同时高于 `CONFIG_ESP_WIFI_RX_BA_WIN=16` 与 `CONFIG_MDNS_ACTION_QUEUE_LEN=8`）。
3. **编译期防回归守卫与构建缓存同步**：
   - 在 `firmware/main/rmlink_network.c` 中加入 `_Static_assert`，编译期强制约束 `CONFIG_MDNS_ACTION_QUEUE_LEN < CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM`，且双核 SWD 配置下 `CONFIG_MDNS_TASK_AFFINITY != CONFIG_RMLINK_EXECUTOR_CORE`；
   - 在 `scripts/Build-Firmware.ps1` 中同步更新既有构建缓存的动态 RX 缓冲数与 `mdns` 优先级、队列深度、核亲和策略。
4. **版本提升**：
   - 将 `firmware/CMakeLists.txt` 中的 `PROJECT_VER` 从 `0.3.5` 升至 `0.3.6`。

## 涉及文件

- 固件版本与源码：`firmware/CMakeLists.txt`、`firmware/main/rmlink_network.c`。
- 固件默认配置：`firmware/sdkconfig.rmlink.defaults`、`firmware/sdkconfig.rmlink-core0.defaults`、`firmware/sdkconfig.rmlink-core1.defaults`、`firmware/sdkconfig.rmlink-psram.defaults`。
- 构建脚本：`scripts/Build-Firmware.ps1`。

## 固件构建与部署摘要

| 项目 | 本次结果 |
| --- | --- |
| 实板升级设备 | `b81f3fc30470`（`rmlink-二号机`，`192.168.137.226`，RMLink V1 / ESP32-S3-WROOM-1-N16R8） |
| 固件版本与配置 | `0.3.6`，`-TargetProfile -BoardProfile rmlink-v1 -TcpPacketSize 512 -ExecutorCore 0 -ImageCachePsram -StreamIpTos 160 -BuildTag release-036`，profile `7912ec692d3a985d` |
| 生效关键配置 | `CONFIG_RMLINK_EXECUTOR_CORE=0`、`CONFIG_MDNS_TASK_AFFINITY_CPU1=y`（`0x1`）、`CONFIG_MDNS_TASK_PRIORITY=4`、`CONFIG_MDNS_ACTION_QUEUE_LEN=8`、`CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=32`、`CONFIG_ESP_WIFI_RX_BA_WIN=16` |
| 固件大小与摘要 | `964256` 字节；BIN SHA256 `2435a9e851a198c204797d82f6ac46ce84e42913afe64d114995b49688620067`；ELF SHA256 `24906e64b9ca1c8f53b02e6175c9ece66fd830e63079f0b4eda8928c2eaf4a87` |
| 其余设备 | `cc68c7a5995c`（`0.3.3`）、`84c7bb788408`（`0.3.4`）、`10bda310a850`（`0.3.5`）本次未升级 |

## 验证步骤与结果

### 1. 诊断期单变量 A1 → B → A2 对照（`b81f3fc30470`）
- **A1（`mdns` 在 Core 0）**：
  - 真实 200 us / 10 MHz 三级指针链采集：`112.17 s` 复现 3241 `TimeoutError`，末尾连续 9 s ICMP 全丢，板端 `lwip.*_drop = 0`；
  - Mode 1 @ 100% 纯 CPU（无 SWD/GPIO）：`82.5 s` 出现下行断崖，`90.66 s` 3242 断流，`45/162` ICMP 丢包，`38` 次超时，`161.2 s` 触发 3241 `TimeoutError`；
  - Mode 3 @ 100% 活跃 H723 只读 DPIDR（无 `rmlink_capture`/3244）：`81.75 s` 复现 3241 `TimeoutError`，连续 `4.9 s` `esp_netif_receive = 0`。
- **B（仅改 `CONFIG_MDNS_TASK_AFFINITY_CPU1=y`，零 C 代码改动）**：
  - Mode 1 @ 100% 纯 CPU：`180.14 s` 全通，3241 `0` 重传 / `0` 超时，3242 传输 `9.19 MB`，板端 ICMP `178/178`；
  - 真实 200 us / 10 MHz 三级指针链采集：`200.01 s` 全通，采集 `189,845` 帧（`10.06 MB`），3241 `0` 超时（最大等待 `136.2 ms`），3244 最大读间隔 `209.3 ms`，板端 ICMP `199/199`。
- **A2（恢复原版 `0.3.5` 回切验证）**：
  - 同接线同参数 200 us / 10 MHz 指针链采集：`100.45 s` 再次复现，3241 累计 6 次超时 / `300 B` 重传，3244 出现 42 次 >50 ms 阻塞并触发 `Realtime capture stalled for 2.5s`。

### 2. 自动化测试与构建
- **Python 主机全量测试**：`PYTHONPATH='host;scripts' python -m unittest discover -s tests/host -q` 运行 **281 项测试全部通过**（`46.816 s`）。
- **C 原生主机模型测试**：
  - `scripts/test_network_native.py`：通过；
  - `scripts/test_ota_native.py`：通过；
  - `scripts/test_ui_native.py`：通过；
  - `scripts/test_stream_deadline_native.py`：12 项场景通过；
  - `scripts/Test-Core.ps1`：通过（64/USB64 边界及 20000 解析用例）；
  - `scripts/Test-Capture.ps1`：通过。
- **固件构建**：`Build-Firmware.ps1` 编译 `release-036` 退出码 `0`，新增 `_Static_assert` 通过编译校验，`rmlink.exe firmware inspect` 核对版本号、profile 与 SHA256 正确。

### 3. 实板验收（`b81f3fc30470`，`0.3.6`）
- 通过 USB OTA 刷入 `0.3.6` 镜像并确认 `state: "boot_confirmed"`，`rmlink.exe devices --json` 显示 `firmware: "0.3.6"`。
- 运行 210 秒 Wi-Fi 200 us / 10 MHz 三级指针链满载采集（只读 SWD，未写目标 STM32）：
  - **210.09 秒完整通过**，无重启、`broker_failure = null`、`stream_error = null`；
  - 3241 控制面：`0` 次重传、`0` 次超时、最大等待 `133.1 ms`；
  - 3244 采样流：`198,126` 帧（`10.56 MB`），**`send_stalls_over_50ms = 0`**，最大单次发送 `43.4 ms`，最大读间隔 `156.3 ms`；
  - 板端片内空闲 RAM `54,428 B`（最低 `46,312 B`）。

### 4. 用户手动验证
- 用户于 2026-11-22 实机确认：“连续稳定运行20分钟，手动验收通过”。

## 原始证据

- 正式构建产物与日志：
  - `artifacts/build/firmware-target-rmlink-v1-tcp512-core0-psram-video-release-036/`
  - `artifacts/diagnostics/timeline-control-loss-20261122/release-036-build.log`
  - `artifacts/diagnostics/timeline-control-loss-20261122/release-036-acceptance-200us.jsonl`
- 诊断与 A-B-A 对照原始数据：
  - `artifacts/diagnostics/timeline-control-loss-20261122/ap-aba-a1-winhotspot.jsonl`、`ap-aba-b-phoneap.jsonl`（独立 2.4 GHz AP 对照）
  - `artifacts/diagnostics/timeline-control-loss-20261122/diag-rx-real-cap-200us-10mhz.jsonl`、`diag-rx-real-cap-static-200us-10mhz.jsonl`
  - `artifacts/diagnostics/timeline-control-loss-20261122/diag-rx-v2-mode3-h723-100pct.jsonl`、`diag-rx-v2-mode1-cpu-100pct.jsonl`、`diag-rx-v2-mode1-cpu-95pct.jsonl`
  - `artifacts/diagnostics/timeline-control-loss-20261122/diag-rx-v2-mdns-cpu1-sdkconfig-delta.txt`、`diag-rx-v2-mdns-cpu1-mode1-100pct.jsonl`、`diag-rx-v2-mdns-cpu1-real-cap-200us-10mhz.jsonl`
  - `artifacts/diagnostics/timeline-control-loss-20261122/aba-a2-stock035-200us.jsonl`
  - `artifacts/worktrees/diag-rx-20261122/`（临时诊断工作树，不纳入主仓库）

所有原始日志、临时工作树与构建产物均留在 `artifacts/`，不纳入 Git 提交。
