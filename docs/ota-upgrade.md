# RMLink OTA 升级

客户端、连接服务和固件从 0.3.0 开始支持 RMLink 自身升级。升级操作不使用 STM32 的 Flash 算法，也不获取 SWD 控制权。

## 引导系统来源

复用 GitHub [espressif/esp-idf v5.1.2](https://github.com/espressif/esp-idf/tree/v5.1.2) 的官方二级 bootloader、`app_update` 和 OTA 状态机，参考 [native_ota_example](https://github.com/espressif/esp-idf/tree/v5.1.2/examples/system/ota/native_ota_example)。SDK 提交为 `482a8fb2d78e3b58eb21b26da8a5bedf90623213`；本机 SDK 中 bootloader、app_update、bootloader_support 与该提交无差异。SDK 代码按上游 Apache-2.0 分发，示例为 CC0。没有另写一套 Flash 引导器。

启用 `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`。新镜像完整写入、镜像验证成功后才切换启动分区。首次启动完成 executor、USB 栈／任务及已启用的网络组件初始化后，调用 `esp_ota_mark_app_valid_cancel_rollback()`；初始化失败时回滚。首次启动确认前发生崩溃、复位或断电，由官方 bootloader 在后续启动中选择之前可用的镜像。该行为基于 [官方 OTA 文档](https://docs.espressif.com/projects/esp-idf/en/v5.1.2/esp32s3/api-reference/system/ota.html)，真实断电／回滚仍需板上验收。

## 分区与首次 USB 迁移

当前布局针对已确认的 16 MiB Flash ESP32-S3：

| 分区 | 地址 | 大小 | 用途 |
|---|---:|---:|---|
| nvs | 0x9000 | 0x6000 | 保留原 Wi-Fi 参数 |
| phy_init | 0xf000 | 0x1000 | 保留 PHY 配置 |
| factory | 0x10000 | 3 MiB | USB 初始化后的恢复应用 |
| otadata | 0x310000 | 0x2000 | 官方冗余启动记录 |
| ota_0 | 0x320000 | 3 MiB | 升级应用 A |
| ota_1 | 0x620000 | 3 MiB | 升级应用 B |

旧 0.2.40 只有 factory 分区，不含 OTA 协议，必须先通过 USB 更新一次 bootloader、分区表、factory 应用和初始 otadata。普通 OTA 只接收应用 `.bin`，不修改引导程序、分区表、factory 或 NVS。

本机已准备与当前硬件配置一致的独立构建，原实板性能检查点构建和最后刷写收据保留：

```powershell
.\scripts\Build-Firmware.ps1 -TargetProfile -TcpPacketSize 512 -ImageCachePsram -StreamDiagnostics -StreamIpTos 160 -BuildTag ota
.\scripts\Install-OtaBootstrap.ps1 -BuildDirectory artifacts/build/firmware-target-tcp512-core0-psram-streamdiag-video-ota
```

第二行默认只输出计划、文件哈希和写入范围，不打开串口。实际迁移前结束所有调试、停止连接服务，核对 COM 口及接线；追加 `-Apply -TargetWiringConfirmed -Port COM13` 才执行。脚本检查真实 Flash 为 16 MiB，先备份 `0x0..0x311fff`，再写四个明确区域，保存迁移收据；不执行整片擦除。备份含设备数据，只保存在忽略提交的 `artifacts/backups/`。写入后需要确认启动、USB、Wi-Fi 和目标调试后再接受迁移。

## 客户端使用

运行 `rmlink`，进入「固件升级」后可用方向键选择：状态、本地导入、GitHub 最新正式固件、GitHub 测试固件、设置 GitHub 仓库。客户端 0.3.2 保留导入后的镜像信息确认页，显示版本变化；蓝色提示「Enter 确认安装并重启 RMLink」，`q / Esc` 取消。回车后立即进入 0% 上传进度，随后显示已传字节、百分比、固件校验和等待重连秒数，启动确认后显示安装成功。其他详情页的回车返回行为保留。

```powershell
rmlink firmware status
rmlink firmware inspect .\rmlink.bin
rmlink firmware update .\rmlink.bin --yes
rmlink firmware latest --repo zhuangyan114/RMLink
rmlink firmware update --latest --repo zhuangyan114/RMLink --yes
rmlink firmware latest --prerelease
rmlink firmware update --latest --prerelease --yes
```

`--no-wait` 在提交并收到重启确认后返回 `rebooting`，由后续 `firmware status` 查询。默认等待最多 90 秒，只有运行镜像 ELF SHA256 与所选镜像一致、且 boot 状态为 VALID 才报告 `boot_confirmed`。没有确认时明确报错，不自动重放 COMMIT。

设备占用时拒绝升级，包括尚未访问目标的 API 租约。传输过程中连接服务设置维护锁，阻止虚拟 USB 和其他 API 申请目标控制权、切换传输或停止服务。已挂载的虚拟 USB 在开始时卸载，原自动挂载偏好保留，重启后由正常重连恢复。上传会话无活动约 100 秒时撤销，设备端约 120 秒失效检查；传输关闭、校验失败或显式取消均不切换启动分区。

## 本地校验与硬件兼容性

主机校验 ESP32-S3 芯片 ID、应用段边界、校验和、镜像附加 SHA256、项目名、语义版本及文件 SHA256。官方固定偏移的自定义应用描述位于 288，携带协议／分区布局版本、目标 GPIO 是否启用、PSRAM、UART／MSC、SWDIO／SWCLK／NRST。文件的配置指纹必须与当前设备一致。设备在擦写备用分区前，完整核对前 352 字节，包括自定义描述的全部 64 字节；最终再由 `esp_ota_end()` 验证应用。

该版本接受未合并的普通 RMLink 应用镜像；签名扩展、合并 Flash 镜像、bootloader、其他项目及缺少 OTA 描述的旧固件均被拒绝。SHA256 是完整性校验，不等同于固件签名；当前仍使用现有局域网调试传输的信任边界，没有改变 eFuse 或启用安全启动。

## GitHub Release 格式

默认仓库为 `zhuangyan114/RMLink`，可在客户端设置为其他 `owner/repo`。正式通道优先查询 `/releases/latest` API；遭遇公共 IP 配额限制时，使用 GitHub 官方 latest-release 重定向。测试通道需显式选择菜单或 `--prerelease`，从非草稿 Release 中按数字版本选择最新版本；公共 API 限流时读取同一仓库的官方 Atom 发布订阅。仅接受 GitHub HTTPS 下载地址及其资产 CDN。测试发布在 GitHub 标记 Pre-release，OTA 清单标记 `channel: testing`，不会自动成为正式升级候选。

Release 必须有 `rmlink-ota-manifest.json` 和清单列出的应用 `.bin`。标签必须为 `v` 加固件版本。客户端按设备配置指纹选择镜像，下载到用户固件缓存，并核对大小、SHA256、版本和配置；缓存再次使用时重新校验。缺少清单时不会按文件名猜测镜像，也不会下载整仓库或执行仓库脚本。

生成资产：

```powershell
python scripts/package_ota.py --build artifacts/build/firmware-core0 --build artifacts/build/firmware-target-tcp512-core0-psram-streamdiag-video-ota --output artifacts/packages/ota-0.3.0
```

清单示例：

```json
{"schema":1,"chip":"esp32s3","version":"0.3.0","images":[{"file":"rmlink-0.3.0-16445f574834cf7b.bin","size":888464,"sha256":"<64 hex>","profile":"16445f574834cf7b"}]}
```

`.github/workflows/firmware-release.yml` 使用官方 ESP-IDF v5.1.2 容器，构建 safe、target、target-psram 三种配置，并校验标签与镜像版本一致。标签推送和普通手动运行只保存 CI 构建资产；只有在版本标签上手动勾选 `publish` 才创建测试 Release，不设为 latest。实板验收后的本地资产可单独发布，自动标签构建不会覆盖其已记录的二进制摘要。当前硬件验收仅覆盖 target／PSRAM 这一配置，其他配置编译成功不代表实板通过。

本仓库使用 v2 组件锁，构建环境的 IDF Component Manager 固定为本机使用的 2.4.11。官方旧版 IDF 容器需在导出环境后执行 `python -m pip install idf-component-manager==2.4.11`，不能通过删除 `dependencies.lock` 来绕过格式不兼容。

## 管理协议

复用已有 USB／TCP CMSIS-DAP 会话，新增供应商命令 `0x83`。每个应答以 `83 sub status` 开始。

| 子命令 | 请求载荷 | 行为 |
|---|---|---|
| 0 INFO | 无 | 63 字节：协议、能力、传输状态、容量、进度、版本、ELF SHA256、配置指纹、启动状态、分区 |
| 1 BEGIN | uint32 大小 + 32 字节 SHA256 | 空闲时建立维护会话，尚不擦写 |
| 2 DATA | uint32 偏移 + uint16 长度 + 数据 | 严格顺序写入备用分区，应答准确累计偏移 |
| 3 FINISH | 无 | 大小／SHA256／ESP 镜像验证，尚不切换启动 |
| 4 COMMIT | 无 | 设置启动分区，约 1 秒后重启 |
| 5 ABORT | 无 | 丢弃会话，原启动分区不变 |

本机 HTTP 使用原有凭据：GET `/v1/devices/{id}/firmware`；POST 同路径，`action` 为 `begin/write/finish/abort`。每个 write 最多 4096 字节 Base64，按协商 DAP 容量拆包，每批最多 8 包，不自动重试写命令。首次调用 begin 返回 upload_id，后续操作必须提供。普通 API／虚拟 USB 的任意 DAP 通路不能伪造 OTA 写入来源。

## 验证边界

已完成 safe 与当前板型配置的固件编译、真实构建镜像解析、主机 OTA／GitHub 模拟测试、原 C 状态机 native 故障注入，以及首次迁移计划检查。C 测试直接编译生产 OTA 状态机，Flash、SHA 和 bootloader API 为替身，覆盖占用、错会话、乱序、错配置在擦写前拒绝、摘要失败、镜像验证失败、关闭／超时撤销、提交去重及启动确认／回滚 API；真实镜像摘要另由 Python 测试和构建输出验证。

2026-10-17 经用户授权已完成首次 USB 迁移、Wi-Fi／USB 实际上传及 ota_0／ota_1 轮换。两次均由安装后的 CLI 完成整包上传、重启，并核对运行 ELF 摘要与 VALID；原 Wi-Fi 配置保留。两次 OTA 之间 STM32 64 KiB Flash 摘要一致，核心运行；最终 USB、Wi-Fi 各完成 100 Hz／100 帧只读采样，无丢帧或 SWD 失败。真实设备还验证了占用保护、错误配置拒绝、部分上传取消、摘要失败及设备端配置描述拒绝后启动分区不变。备份、镜像身份和逐项结果见[实板验收记录](ota-acceptance-20261017.md)。

2026-10-18 的扩展验收见 [0.3.2 测试版验收](test-release-0.3.2.md)，包含升级／降级、上传复位、首次启动失败回滚、挂载恢复和目标功能回归。CH343 RTS 复位与真正断电不同；真实断电、NRST 实体接线和其他板型仍须单独验收。
