# OTA 实板验收（2026-10-17）

用户授权“现在备份、迁移并实测 OTA”后，已完成原固件备份、首次 USB 迁移、Wi-Fi 与原生 USB 两次完整上传及 A/B 启动确认。客户端、连接服务和最终设备固件均为 0.3.0。实现与引导系统来源见 [OTA 升级](ota-upgrade.md)。本记录不替代此前性能检查点。

## 硬件与配置

- ESP32-S3 rev0.2，COM13 为 CH343 下载口；工具再次读取确认 16 MiB Flash。原生 USB 身份为 `303A:1002`，设备编号 `50787d15a924`。
- 沿用 target／TCP512／executor core0／PSRAM／streamdiag／video 配置；原生 USB 仍为 64 字节。配置指纹 `16445f574834cf7b`。
- GPIO4 → STM32 PA13／SWDIO，GPIO5 → PA14／SWCLK，GND 共地；GPIO6／NRST 本轮未接入、未验收。
- 原 Wi-Fi 在迁移前后均为 configured、connected，IP `192.168.137.84`。本轮没有重新下发 SSID 或密码。

## 备份和首次迁移

`Install-OtaBootstrap.ps1 -Apply -TargetWiringConfirmed -Port COM13` 先读取 `0x0..0x311fff`，得到 3,219,456 字节备份，覆盖原 bootloader、分区表、NVS、factory 与后续初始 otadata 区域。这是原布局所需区域的备份，不是全 16 MiB 备份。

| 项目 | 本机位置／结果 |
|---|---|
| 备份 | `artifacts/backups/ota-bootstrap-20261017-180722/before-migration.bin` |
| 备份 SHA256 | `1c541ca5c328f54939f3766c1cfa2eedd62f4d39fe81868331c69b3a5176dacc` |
| 原应用核对 | 备份的 factory 应用与原 0.2.40 构建逐字节一致 |
| 迁移收据 | 同目录 `migration.json`，`completed=true` |
| 写入日志 | `artifacts/logs/ota-bootstrap-20261017.log` |

仅写入 bootloader `0x0`、分区表 `0x8000`、factory `0x10000`、otadata `0x310000`；没有整片擦除，NVS 保留。各区域烧录后的工具校验通过。重启后设备实际报告 0.3.0、OTA supported、rollback enabled，并自动连回原 Wi-Fi。

备份包含设备配置，只在忽略 Git 提交的 `artifacts/` 中保留。

## 两次真实 OTA

通过已安装的 `C:\Users\22690\.rmlink\app\bin\rmlink.exe firmware update ... --yes --json` 执行。两份镜像版本均为 0.3.0、配置一致，独立构建的时间描述使 ELF 摘要不同；本地导入允许安装同版本的不同镜像，使用摘要和实际运行分区验证写入。

| 阶段 | 通道 | 应用字节数 | 整体耗时 | 实际运行分区 | 启动状态 |
|---|---|---:|---:|---|---|
| 首次迁移后 | CH343 ROM 下载 | 888,464 | 单独日志记录 | factory（0） | factory，不适用 OTA VALID |
| 第一次 OTA | Wi-Fi／TCP512 | 888,464 | 74.641 s | ota_0（16） | VALID（2）、boot_confirmed |
| 第二次 OTA | 原生 USB64 | 888,464 | 60.328 s | ota_1（17） | VALID（2）、boot_confirmed |

耗时包含客户端检查、上传、设备验证、重启和重新连接，不作为纯链路速率。

| 镜像 | 应用 SHA256 | ELF SHA256 |
|---|---|---|
| factory／最终 ota_1 | `68adb8b4289b8ed8b8d18832486bdffb5778b10ee988abe9a66fd78c76a50409` | `9455316099a2a4060246091846691007eb2ff9efe61ab21a87a255abf6bd7cf4` |
| 第一次 OTA／ota_0 | `33067272f70c04c9747770ebc21068969ac5f092f3910e4bf79810f594051035` | `86463b35145ec2bf4221208f65260068a180efc96956a0c421c1bd4815cf1f4c` |

构建分别位于 `artifacts/build/firmware-target-tcp512-core0-psram-streamdiag-video-ota/` 与带 `-ota-acceptance` 后缀的目录。第二份初次编译出现一条无诊断的 ccache 子命令失败；禁用本轮 ccache 后构建成功，保留原日志及 `ota-acceptance-build-retry.log`。

当前刷写收据 `artifacts/hardware/rmlink-last-flash.json` 已在实板确认后更新。原 0.2.40 收据归档为 `rmlink-last-flash-before-ota-20261017.json`，原性能检查点构建保留。

## 保护和目标回归

真实设备检查通过：

- 仅建立、尚未访问目标的调试 API 租约也会阻止 OTA。
- 主机拒绝硬件指纹不匹配的开始请求。
- 写入 4096 字节后取消，启动分区与运行摘要保持不变。
- 完整发送所声明的 352 字节但提供错误摘要，完成请求被拒绝，未切换启动分区。
- 使用匹配的开始请求，但传入 safe 配置的实际镜像描述，设备端返回“固件硬件配置不匹配”；当前分区与摘要保持不变。
- 再次选择当前运行的同一镜像，CLI 返回 `up_to_date`，不重复提交。

最初 SWD 在 4 MHz 与 100 kHz 均收到 NO_ACK。用户按上述 GPIO 重新接线后，识别到 F103：DP ID `0x1ba01477`、CPUID `0x411fc231`、DBG ID `0x6410`。该接线前失败保留在 `ota-board-bootstrap.json`，没有归因于 OTA。

两次 OTA 之间读取 STM32 全 64 KiB 主 Flash，SHA256 均为 `eeb3aebb22d7b5967282934a4b0ae732c083da67753c197add68d54d718b9360`。读取时核心未暂停。本轮没有写入目标 RAM／Flash、暂停或复位目标。

最终以 USB、Wi-Fi 分别对静态 Flash 字 `0x08000000` 请求 100 Hz、采集 100 帧；两路均 100 帧有效、序号连续、deadline_missed／ring_dropped／failures 为 0，设备时间跨度分别为 990016／990007 微秒。目标最终为 Running。这仅证明基本只读采样回归，不替代复杂指针、RTT、多订阅或长期性能验收。

最终恢复 Wi-Fi 连接偏好，设备 connected，owner 为空，保留本轮原有未挂载状态。

## 证据和边界

完整汇总为 `artifacts/tests/ota-hardware-acceptance-20261017.json`，引用同目录下 `ota-board-before/bootstrap/negative/wifi/after-wifi/usb/after-usb/final.json` 及 `ota-github-live.json`。主机测试、C 状态机替身故障注入和冻结 EXE 软件验收仍按 [终端客户端记录](terminal-client.md) 分别保留。

开发包选择器已适配本轮经过实板确认的完整 profile，继续核对应用和 sdkconfig 哈希；按允许的烧录区域收集文件，补齐新布局必需的 `ota_data_initial.bin`，拒绝额外 NVS 区域或缺失文件。2 项打包回归测试及真实构建资产检查通过。安装器和开发包清单包含本记录，已安装目录的三份 OTA／终端文档与源码哈希一致；本轮未生成要求干净提交状态的完整开发 ZIP。

GitHub 实际查询 `zhuangyan114/RMLink` 返回“仓库尚无正式 Release；请先发布带 OTA 清单的版本”。获取入口、清单与发布工作流已经实现；没有创建远端发布、提交或推送。已发布资产的下载升级尚未完成实测。

仍待验证：写入时断电、首次启动异常触发官方 bootloader 回滚，以及 OTA 期间已挂载虚拟 USB 的自动卸载／重挂载。启用回滚和正常启动确认为本轮事实，不能据此声称上述故障场景已通过实板验收。
