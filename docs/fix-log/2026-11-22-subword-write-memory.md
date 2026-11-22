# 支持 `write_memory` 按 8/16/32 位 MEM-AP 宽度写入以修复 Watch 亚字变量写入失败（上位机 0.3.6）

## 日期与用户确认

- 确认日期：2026-11-22。
- 用户确认：“验收通过”。
- 本次记录覆盖升级至包含该修复的连接服务（最终随本次发布升至 `0.3.6`）在 RMLink V1 探针 `b81f3fc30470`（接入 `STM32H723VGT6`）上的自动化测试、实板读写验收，以及配合开发版 Orbit 对 `uint8_t abcd` 变量的手动实测；STM32F407 / STM32F103 实板、外设寄存器亚字写入以及 Orbit 自带 `cmsis-dap-helper` 迁移至方案 A 本次未实测（已在 Orbit 侧预留 `TODO`）。

## 问题描述

用户反馈在 Orbit for VS Code 1.2.1 + RMLink（SWD）调试 `STM32F407IG` 时，Watch 面板修改 `volatile uint8_t test_enable` 与 `test_online` 均失败，日志显示 `write_memory` 返回 HTTP `400`（`code=InvalidRequest`，`error=Error: invalid_request`）；将变量类型改为 `volatile uint32_t` 后写入恢复正常。

实机复现时，升级 RMLink 连接服务后在开发版 Orbit（`C:\Users\22690\Desktop\AI\Orbit`）对 `STM32H723VGT6` 工程 `h7vgt6_test` 中的 `uint8_t abcd`（`0x2000a9d4`）写入 `0` 仍不生效，且 `dll.log` 无 `write_memory` 报错记录。

## 根因分析

存在两层沿用旧契约的 4 字节对齐硬限制：

1. **RMLink 连接服务侧（`host/rmlink_connector/target.py`）**：
   - `read_memory` 早已在已知 RAM/Flash 窗口支持亚字读，但 `write_memory` / `write_range` 强制校验 `address % 4 == 0` 且 `len(data) % 4 == 0`（`Aligned word write required`），并将 `ValueError` 在 `service.py` 中统一映射为 HTTP `400 {"error": "invalid_request"}`。
   - Orbit Watch 按 DWARF 变量类型长度构造缓冲区（`uint8_t` 为 1 字节，`uint16_t` 为 2 字节）直接调用 `write_memory`，导致所有 1/2 字节或非 4 字节对齐变量写入均被直接拒绝。
2. **开发版 Orbit 客户端侧（`rmlink-target-channel.ts`）**：
   - `writeMemoryWithPriority` 为配合旧版 RMLink 契约额外加了客户端校验 `(address % 4) !== 0 || (bytes.length % 4) !== 0`，在发送 HTTP 请求前就本地返回 `UnsupportedCapability`，导致请求未到达已升级的 `RMLinkConnector.exe`。

## 修改方案

采用 **方案 A（DAPLink 风格 MEM-AP `CSW` 8/16/32 位原生宽度写）**，无需先读后写（避免运行态 RMW 并发覆写同一 32 位字内的相邻字节），也不需要修改 ESP32 探针固件；同时将上位机版本从 `0.3.5` 升至 `0.3.6` 与固件版本对齐：

### 1. RMLink 上位机连接服务（`0.3.6`）
- **`host/rmlink_connector/target.py`**：
  - `execute('write_memory', ...)` 在进入控制屏障前校验 `address`（非负 uint32）与 `data`（hex 字符串，1..4096 B，不越出 32 位地址空间），移除 `% 4` 硬限制，避免非法参数触发无谓的采集屏障暂停/恢复。
  - `write_range` 按当前地址对齐与剩余字节数自动选择 MEM-AP `CSW` 访问宽度与数据通道移位：
    - `cur % 4 == 0 and rem >= 4`：`CSW = 0x23000052`（32-bit，`width = 4`），按数据包预算与 1 KiB 边界批量写入；
    - `cur % 2 == 0 and rem >= 2`：`CSW = 0x23000051`（16-bit，`width = 2`），数据左移 `(cur & 2) * 8` 写入对应 halfword lane；
    - 其余字节：`CSW = 0x23000050`（8-bit，`width = 1`），数据左移 `(cur & 3) * 8` 写入对应 byte lane。
  - 发生 `TargetAccessError` 时按当前 `width` 精确计算 `error.completed_bytes`。
- **`host/rmlink_connector/__init__.py`**：版本号从 `0.3.5` 升至 `0.3.6`。
- **`docs/api-v1.md`**：更新 `write_memory` 契约说明为按对齐拆分为 8/16/32 位 MEM-AP 传输。
- **`tests/host/test_target_engine.py`**：升级 `TargetModel` 模拟 `CSW.Size`（`0/1/2`）lane 写入与 `1 << size` 地址自增，新增 `test_subword_and_unaligned_write_memory_uses_mem_ap_widths` 覆盖 64 B / 512 B 包长下的单字节、半字、非对齐 2 字节、跨 1 KiB 边界 259 字节写入、非法参数拦截与故障 `completed_bytes`。

### 2. 联动改动（外部 Orbit 仓库，不纳入本仓库 Git 提交）
- `C:\Users\22690\Desktop\AI\Orbit\src\ozone-backend\rmlink-target-channel.ts`：放开 `writeMemoryWithPriority` 的 `% 4` 客户端拦截（RTT Down Buffer 仍保留 `writeAlignmentBytes: 4`）；同步更新 `segger-rtt-memory.test.ts` 并重建 `dist/`。
- 在 Orbit 仓库及本仓库 `artifacts/worktrees/orbit-rmlink` 的 `native/cmsis-dap-helper/src/cmsis_dap_target.cpp` 与 `mock_transport.cpp` 中留下 `TODO`，记录后续将 CMSIS-DAP helper 从 32-bit RMW 迁移至方案 A 的改造点。

## 涉及文件

- 本仓库源码、测试与文档：`host/rmlink_connector/target.py`、`host/rmlink_connector/__init__.py`、`tests/host/test_target_engine.py`、`docs/api-v1.md`。
- 外部 Orbit 仓库（独立仓库，不在本仓库提交范围内）：`C:\Users\22690\Desktop\AI\Orbit\src\ozone-backend\rmlink-target-channel.ts`、`src\ozone-backend\segger-rtt-memory.test.ts`、`native\cmsis-dap-helper\src\cmsis_dap_target.cpp`、`native\cmsis-dap-helper\src\mock_transport.cpp`。

## 验证步骤与结果

### 1. 自动化测试
- **RMLink Python 主机测试**：
  - `PYTHONPATH='host;scripts' python -m unittest tests\host\test_target_engine.py -v`：**7 项通过**。
  - `PYTHONPATH='host;scripts' python -m unittest discover -s tests\host -q`：**282 项全量测试通过**（含升版 `0.3.6` 后复测）。
- **Orbit 侧测试与构建**：
  - `npx vitest run src/ozone-backend/segger-rtt-memory.test.ts`：**5/5 通过**；
  - `npm run build` 与 `npm run typecheck` 通过（0 错误）。

### 2. 实板自动化验收（`b81f3fc30470` → `STM32H723VGT6`，Wi-Fi `packet_size = 512`，4 MHz SWD）
- **源码直启连接服务实板全量用例**（[subword-write-hw-20261122.json](../../artifacts/logs/subword-write-hw-20261122.json)）：
  - DTCM RAM（`0x200003f0`）与 AXI SRAM（`0x2404e3fc`）：1 字节写（offset `0..3`）、2 字节半字对齐写（offset `0`、`2`）、2 字节非对齐写（offset `1`）全部回读一致，同 32 位字内相邻字节均完整保留。
  - 跨 1 KiB 边界非对齐写（`0x2404e3ff` 写 259 字节跨越 `0x2404e400`）：`bytes_written = 259`，全量回读匹配，前后边界保护字节（`a5a5a5` / `a5a5`）未破坏。
  - 运行态（`Running`）单字节写与恢复（`0x2404e3fd`）：`17e378ec` → `17e278ec` → `17e378ec`，相邻字节不受影响。
  - 非法参数检查：`data: ""` 与 `data: "zz"` 返回 HTTP `400 invalid_request`。
  - 测试完成后原 DTCM / AXI 字节全部恢复校验通过（`restored: true`），目标恢复 `Running`。
- **本地安装打包冒烟**：构建并安装 `RMLinkConnector.exe` 后，实板单字节写入与恢复冒烟通过（`17e378ec` → `17e278ec` → `17e378ec`）。

### 3. 用户手动验证
- 2026-11-22 17:00 左右用户重启开发版 Orbit 调试会话（`h7vgt6_test` / `STM32H723VGT6`），于 Watch 面板对 `uint8_t abcd` 写入 `0`（Orbit 日志 `09:00:57.965 [Eval] doSetWatchValue: "abcd" = 0`，`dll.log` 无错误），随后明确确认：“验收通过”。

## 原始证据

- `artifacts/logs/subword-write-hw-20261122.json`：`STM32H723VGT6` 实板亚字/非对齐写与运行态单字节写完整回读记录。
- `C:\Users\22690\Desktop\AI\Orbit\outputs\Log\eval.log`、`dll.log`：手动验收时的开发版 Orbit 调试日志（本机临时会话日志）。

所有原始日志与构建产物均保留在 `artifacts/`，不纳入 Git 提交。
