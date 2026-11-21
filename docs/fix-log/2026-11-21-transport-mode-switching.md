# 统一上位机与板端模式切换逻辑并升级至 0.3.5

## 日期与用户确认

- 确认日期：2026-11-21。
- 用户确认：“ok，正常使用”。
- 本次记录覆盖已升级 `0.3.5` 连接器、终端客户端与 RMLink V1 固件在实板 `b81f3fc30470` 上的用户确认结果；实板物理按键长按、物理 USB 插拔与其余离线设备升级未由 agent 单独实测。

## 问题描述与根因

此前板端与上位机各自维护了一套互不感知、语义冲突的传输模式状态机，导致切换传输时频繁失联或报错：

1. **双端互盲导致双双失联**：板端按成 `UI_WIFI` 后会执行 `tud_disconnect()` 从 USB 总线电气断开；上位机在 `connector.json` 中固定保存 `transport: "usb" | "wifi"`，执行 `POST /v1/transport` 时只关掉本机 `Broker` 并盲连另一通道，不查询也不通知板端。板端在 `UI_WIFI` 时上位机切 USB 找不到 WinUSB 接口；板端在 `UI_USB` 时禁用 Wi-Fi，上位机只守着 Wi-Fi 反复重连失败。
2. **`UI_USB` 下通过 USB 配网假成功**：`0x80/03` 保存 Wi-Fi 凭据后未恢复 `s_enabled`，导致 `connection_task` 永不发起 `esp_wifi_connect()`，而上位机仍提示“正在连接”。
3. **`UI_WIFI` 丧失 USB 救砖与配网能力**：拉断原生 USB 后一旦热点变化，插线也无法重新配网。
4. **板端按键与灯语缺陷**：`UI_AUTO` 下同时连着 USB 与 Wi-Fi 时硬编码优先亮蓝灯，即使业务实际走 Wi-Fi；`UI_CONFIGURE` 黄灯选择态无超时、无取消且盲选；按键切换未检查 `s_owner` / Flash / OTA 忙状态；`rmlink_network_set_enabled(false)` 未停 `s_reconnect_timer` 与清零 `s_reconnect_deadline`；`rmlink_stream.c` 监听与实时流也未检查 `rmlink_network_is_enabled()`。
5. **上位机管理与 CLI 缺陷**：`scan_usb()` 在未连 Wi-Fi 时会用 `0.0.0.0` 覆盖设备已有有效 IP，且因 WinUSB 独占打开会漏掉本服务已连接的 USB 设备；`POST /v1/transport` 忽略 `auto_connect=False`、缺少 `connected` 的 `broker.failed` 检查、把 `TargetBusy`/参数错误吞成 `400 invalid_request`；CLI `connect --transport` 连续发两次请求跑两遍 mDNS 发现。

## 修改方案

采用**方案一（板端 USB 常开保底 + 协议补齐状态与 Wi-Fi 开关 + 上位机统一调度 `auto / usb / wifi`）**，并将固件与上位机版本号均从 `0.3.4` 升至 `0.3.5`：

### 1. 板端固件（`0.3.5`）
- **USB 常开保底与按键/LED 重构**（`rmlink_ui.c` / `rmlink_ui.h` / `Kconfig.projbuild`）：移除 `apply_usb_mode` 与 `eub_vendord_disconnect()`；短按（< 2 s）忽略防误触；空闲长按 ≥ 2 s 切换板端 Wi-Fi 开关（黄灯常亮 0.5 s 确认），SWD/Flash/OTA 忙时拒绝并黄灯 5 Hz 快闪 1 s；LED 优先级改为“忙/确认黄灯 → USB 活跃蓝常亮 → Wi-Fi 活跃绿常亮 → USB 待机蓝 1 Hz 慢闪 → Wi-Fi 待机绿 1 Hz 慢闪 → 无链路红 1 Hz 慢闪”。
- **执行器与网络协议**（`rmlink_executor.h` / `swd.c` / `rmlink_network.h` / `rmlink_network.c` / `rmlink_stream.c`）：
  - 导出只读原子快照 `rmlink_active_owner_transport()`、`rmlink_busy_flags()`（owner/flash/ota）与 `rmlink_usb_client_active()`（12 s 活跃窗）。
  - 新增 `0x80/18`（12 字节只读链路/忙状态快照，USB/TCP 均可调用，OTA 忙期间放行）与 `0x80/19`（空闲时开关板端 Wi-Fi，TCP 关闭时延迟 150 ms 生效以便先回传 ACK，并带序列号防竞态）。
  - 修复 `rmlink_network_set_enabled(false)` 立即停止 `s_reconnect_timer` 并清零 `s_reconnect_deadline`；`0x80/03` 配网提交自动恢复 `s_enabled = true`；`0x80/05` 在禁用 Wi-Fi 时返回失败；`rmlink_stream.c` 监听与实时发送循环检查 `rmlink_network_is_enabled()`。

### 2. 上位机连接服务与终端（`0.3.5`）
- **统一三态调度**（`service.py` / `diagnostics.py` / `broker.py` / `rmlink_native_usb.py`）：
  - 支持 `'auto' | 'usb' | 'wifi'`，新配对默认 `'auto'`（USB 优先，拔线回退 Wi-Fi）；空闲时在 `auto` 下检测到 USB 在位会做 make-before-break 升级，有活动会话或 owner 时不自动切换。
  - `/v1/status` 与 `/v1/devices` 增加 `preferred_transport`、`active_transport`、`board_link`；`POST /v1/transport` 支持 `auto/usb/wifi` 与 `connect` 参数，从 USB 切向 Wi-Fi 时自动通过 `0x80/19` 打开板端 Wi-Fi 并轮询读取新 IP，忙时返回 `409 TargetBusy`，参数错误返回 `ManagementError`；mDNS 发现新 IP 时回写 `connector.json`；兼容不认识 `0x80/18` 的旧固件（配合 `UsbClient.has_interfaces()`）。
- **设备管理与 CLI/UI**（`management.py` / `cli.py` / `console.py`）：
  - 修复 `scan_usb()` 纳入本服务已连接的 USB 探针，且 `0.0.0.0` 不覆盖已有有效 Wi-Fi IP。
  - `GET/POST /v1/devices/{id}/wifi` 增加 `wifi_enabled` 字段并支持 `{"enabled": bool}` 远程开关；CLI 新增 `connect --transport auto|usb|wifi`（单次 `/v1/transport` 请求）与 `wifi on/off`，交互菜单显示实时偏好与当前通道。

## 涉及文件

- 固件与配置：`firmware/CMakeLists.txt`、`firmware/components/debug_probe/include/rmlink_executor.h`、`firmware/components/debug_probe/swd.c`、`firmware/main/Kconfig.projbuild`、`firmware/main/rmlink_network.h`、`firmware/main/rmlink_network.c`、`firmware/main/rmlink_stream.c`、`firmware/main/rmlink_ui.h`、`firmware/main/rmlink_ui.c`。
- 上位机连接器与终端：`host/rmlink_connector/__init__.py`、`host/rmlink_native_usb.py`、`host/rmlink_connector/diagnostics.py`、`host/rmlink_connector/broker.py`、`host/rmlink_connector/service.py`、`host/rmlink_connector/management.py`、`host/rmlink_connector/cli.py`、`host/rmlink_connector/console.py`。
- 测试与文档：`tests/ui/ui_test.c`、`tests/network/network_test.c`、`tests/host/test_transport_selection.py`、`tests/host/test_management.py`、`tests/host/test_connector.py`、`tests/host/test_terminal_client.py`、`docs/protocol-v1.md`、`docs/api-v1.md`、`docs/terminal-client.md`。

## 构建与安装

| 项目 | 本次结果 |
| --- | --- |
| 实板升级设备 | `b81f3fc30470`（`rmlink-二号机`，`192.168.137.102`，RMLink V1 / ESP32-S3-WROOM-1-N16R8） |
| 固件版本与配置 | `0.3.5`，`-TargetProfile -BoardProfile rmlink-v1 -TcpPacketSize 512 -ImageCachePsram -StreamIpTos 160`，profile `7912ec692d3a985d` |
| 固件大小与摘要 | `964256` 字节；BIN SHA256 `00a20858225fb61e40012c0f264ae5e3e38d5beb45c6ae76614f1cdbd3a34ac3`；ELF SHA256 `e6a418f7412b867dd6b56c804394c9cbdcec2b5530e4cdc8baf921446ae82924` |
| 上位机安装版本 | `0.3.5`，安装至 `%USERPROFILE%\.rmlink\app\bin\` |
| `RMLinkConnector.exe` SHA256 | `97CEDF1AE111BC6FA1A5AC57E397CC020809DF9C4DA069DEFEB097F6CE274EDB` |
| `rmlink.exe` SHA256 | `460880CCFDA5B3F5DAB4D5F990B55DA4B2E3AF5C7D73D3E0E3E7742095634FD4` |
| 其余设备 | `cc68c7a5995c`（`0.3.3`）、`84c7bb788408`（`0.3.4`）、`10bda310a850`（`0.3.4`）本次均离线，未升级 |

## 验证步骤与结果

### 自动化测试与构建
- **C 主机测试**：
  - `tests/ui/ui_test.c`（`-Wall -Wextra -Werror`）：通过，覆盖短按忽略、2 s 长按 Wi-Fi 切换、忙态快闪拒绝、活跃/待机/无链路 LED 优先级及 32 位毫秒回绕。
  - `tests/network/network_test.c`（`-Wall -Wextra -Wno-unused-variable`）：通过，覆盖 `rmlink_network_set_enabled(false)` 立即停止 `s_reconnect_timer` 与清零 `s_reconnect_deadline`。
  - `scripts/Test-Core.ps1`：通过（含 64/USB64 边界及 20000 组解析用例）。
- **Python 主机全量测试**：`PYTHONPATH='host;scripts' python -m unittest discover -s tests\host -v` 运行 **265 项测试全部通过**（`Ran 265 tests in 31.123s`，0 失败 0 错误）。
- **固件编译**：`Build-Firmware.ps1` 完成 `mode-v1-check` 与 `release-035` 两次编译，修改文件 0 警告，产物版本检查均为 `0.3.5`。

### 实板部署检查
- 停止旧版 `0.3.4` 后台服务后安装并启动 `0.3.5` 连接器；通过 Wi-Fi OTA 将 `b81f3fc30470` 升至 `0.3.5`，返回 `boot_confirmed`。
- 只读核验 `firmware status` 返回 `version: "0.3.5"`、`boot_state: 2`、`slot: 17` 且 `elf_sha256` 匹配；`status` 返回 `packet_size: 512` 与完整 `board_link` 快照（`dual_link_supported: true`、`wifi_toggle_supported: true`）；`wifi status` 返回 `wifi_enabled: true`、`rssi_dbm: -41`。
- agent 本次未对目标 STM32 执行 RAM/Flash 写入，也未在实板执行物理 USB 插拔与物理按键实操测试。

### 用户手动验证
- 用户于 2026-11-21 明确回复“ok，正常使用”，确认安装新版 `0.3.5` 连接器及固件后日常使用正常。

## 原始证据

- `artifacts/build/firmware-target-rmlink-v1-tcp512-core0-psram-video-release-035/`：实板 OTA 使用的 `0.3.5` 固件镜像与构建产物。
- `artifacts/build/firmware-target-rmlink-v1-tcp512-core0-psram-mode-v1-check/`：开发验收编译检查产物。
- `artifacts/build/connector/dist/`：`RMLinkConnector.exe` 与 `rmlink.exe`（`0.3.5`）打包产物。
- `artifacts/tests/`：`rmlink-ui-test.exe`、`rmlink-network-test.exe` 与 `rmlink-core-test-*.exe` 测试可执行文件。

构建产物与测试二进制均保留在 `artifacts/`，不纳入 Git 提交。
