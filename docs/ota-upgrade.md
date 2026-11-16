# RMLink OTA 升级机制

从 0.3.0 开始，RMLink 支持直接通过原生 USB 或 Wi-Fi 给探针本身升级固件。整个过程只操作 ESP32 自己的备用 OTA 分区，不会占用 SWD 控制权，也不会触碰目标 STM32。

## 分区布局与引导回滚

引导与双分区切换直接复用 [espressif/esp-idf v5.1.2](https://github.com/espressif/esp-idf/tree/v5.1.2) 官方二级 bootloader、`app_update` 和 [native_ota_example](https://github.com/espressif/esp-idf/tree/v5.1.2/examples/system/ota/native_ota_example) 状态机（SDK 提交 `482a8fb2d78e3b58eb21b26da8a5bedf90623213`），启用 `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`。

针对 16 MiB Flash 的 ESP32-S3 分区如下：

| 分区 | 起始地址 | 大小 | 说明 |
| --- | ---: | ---: | --- |
| `nvs` | `0x9000` | 24 KiB | 保存已配网的 Wi-Fi 凭据 |
| `phy_init` | `0xf000` | 4 KiB | PHY 校准参数 |
| `factory` | `0x10000` | 3 MiB | 出厂恢复应用 |
| `otadata` | `0x310000` | 8 KiB | 官方双分区启动记录 |
| `ota_0` | `0x320000` | 3 MiB | OTA 分区 A |
| `ota_1` | `0x620000` | 3 MiB | OTA 分区 B |

新镜像完整写入且校验通过后才会修改启动分区；重启后需等 SWD 执行器、USB 栈和网络组件全部初始化完毕，固件才会调用 `esp_ota_mark_app_valid_cancel_rollback()` 确认启动成功。如果在确认前崩掉或复位，bootloader 下次启动会自动切回上一版可用固件。

### 从 0.3.0 之前的老固件迁移

0.2.40 及更早版本只有单个 `factory` 分区，没有 OTA 分区表和 `0x83` 升级协议，必须通过串口/USB 刷一次引导程序、分区表和初始 `otadata`：

```powershell
# 先编译目标配置
.\scripts\Build-Firmware.ps1 -TargetProfile -BoardProfile rmlink-v1 -TcpPacketSize 512 -ImageCachePsram -StreamIpTos 160 -BuildTag ota
# 先预览计划（不写设备），确认串口与接线后加上 -Apply -TargetWiringConfirmed 执行
.\scripts\Install-OtaBootstrap.ps1 -BuildDirectory artifacts\build\firmware-target-rmlink-v1-tcp512-core0-psram-video-ota
```

脚本会先备份 `0x0..0x311fff` 到 `artifacts/backups/`，再分四个区域精确写入，保留 NVS 中的 Wi-Fi 配置。如果是从旧安全配置向 RMLink V1 硬件引脚迁移，可参考 [RMLink V1 接线与验证](rmlink-v1-bringup.md) 中的两阶段 Wi-Fi OTA 迁移方法。

## 客户端升级操作

在交互控制台进入 **固件升级** 菜单，可以选本地 `.bin`、GitHub 最新正式版或 GitHub 测试版；命令行用法如下：

```powershell
rmlink firmware status                                  # 查看当前运行分区、版本、指纹和启动状态
rmlink firmware inspect .\rmlink.bin                    # 离线核对镜像信息
rmlink firmware update .\rmlink.bin --yes               # 使用本地文件升级
rmlink firmware latest --repo zhuangyan114/RMLink       # 查询 GitHub 最新正式版
rmlink firmware update --latest --yes                   # 自动下载并升级最新正式版
rmlink firmware latest --prerelease                     # 查询最新测试版（含 Pre-release）
rmlink firmware update --latest --prerelease --yes      # 下载并升级测试版
```

- 默认等待最多 90 秒，直到设备重启重连且运行中的 `elf_sha256` 一致、`boot_state` 为 `VALID` 才报告 `boot_confirmed`（加 `--no-wait` 则在提交重启后立即返回）。
- 设备正被调试会话占用时拒绝开始升级；上传期间服务会加维护锁，阻止其他操作抢占、切传输或关闭服务。如果上传中断超过约 100 秒（板端 120 秒），会话自动作废，原启动分区不动。

## 镜像校验与 GitHub Release 格式

为防止刷错板子，RMLink 在应用镜像偏移 `288` 处嵌入了 64 字节自定义描述（魔数 `RMLKOTA1`），包含协议/分区布局版本、是否启用目标引脚、是否启用 PSRAM、UART/MSC 开关以及 SWDIO/SWCLK/NRST 引脚号，并取前 16 位 hex SHA256 作为**硬件配置指纹（profile）**。主机上传前和设备擦写前都会逐字节核对配置指纹，不匹配一律拒绝；同时只接收未合并的普通应用 `.bin`（不超过 3 MiB），不接收 bootloader 或合并镜像。

> 注意：这里的 SHA256 用于防损坏与防混刷校验，不是密码学签名；当前仍信任局域网与本机配对边界，未开启 ESP32 安全启动 eFuse。

从 GitHub 获取固件时，客户端遵守以下约定：

- Tag 必须为 `v` + 三段版本号（如 `v0.3.4`），Release 必须包含 `rmlink-ota-manifest.json` 和清单中列出的 `.bin`。
- 正式通道请求 `/releases/latest`；测试通道需显式指定 `--prerelease`，从非草稿 Release 中取最高版本号。遇到 GitHub 公共 API 限流（403/429）时自动回退到 GitHub 官方重定向或 `releases.atom` 订阅，全程只允许 GitHub HTTPS 下载地址。
- 生成 OTA 清单使用 `scripts/package_ota.py`：

```powershell
python scripts/package_ota.py --build <构建目录> --output artifacts/packages/ota-out --channel stable
```

仓库中的 `.github/workflows/firmware-release.yml` 使用官方 `espressif/idf:v5.1.2` 容器构建并打包 OTA 资产（由于仓库使用 v2 `dependencies.lock`，CI 中会额外固定安装 `idf-component-manager==2.4.11`）。

## 协议与已知验证范围

OTA 复用标准 USB/TCP DAP 通道，使用厂商命令 `0x83`：

| 子命令 | 用途 |
| --- | --- |
| `0 INFO` | 返回 63 字节状态（协议、能力、当前分区、容量、版本、ELF SHA256、8 字节硬件指纹、启动状态） |
| `1 BEGIN` | 传入镜像总长和 SHA256，在空闲时建立维护会话 |
| `2 DATA` | 按偏移顺序写入备用分区（`offset` + `len` + 数据） |
| `3 FINISH` | 验证总长度、SHA256 和 ESP-IDF 应用结构（尚不切分区） |
| `4 COMMIT` | 切换启动分区并在约 1 秒后重启 |
| `5 ABORT` | 放弃本次会话，原启动分区不变 |
| `6 MIGRATION` | 仅过渡构建支持，返回允许迁移的目标硬件指纹 |

> 本机 HTTP 接口为 `GET / POST /v1/devices/{id}/firmware`，每次 `write` 最多 4096 B Base64，拆包发送且不自动重试写命令。
> 已在实机上验证过串口初次迁移、USB 与 Wi-Fi 双分区轮换升级/降级、占用保护、错配置拒绝及串口 RTS 复位回滚；真实拔电中断、外接 NRST 引脚以及其他未实测硬件配置仍需单独验收。
