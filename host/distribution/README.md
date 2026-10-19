# RMLink Windows x64 开发组件

本包为开发验证用途。服务、SDK及ESP32应用镜像均有SHA256清单；不携带用户热点凭据或配对状态。标准CMSIS-DAP使用本机已安装的虚拟USB驱动；服务也能选择原生USB作为内部承载。

安装后在新终端输入 `rmlink` 进入设备控制台。`rmlink --help` 查看子命令，`rmlink doctor` 检查连接与驱动。交互界面退出后后台服务继续运行，完整说明见 docs/terminal-client.md。驱动维护入口需要明确选择审核过的本地驱动包。0.3.3 增加独立 USB 连续采集通道，保留本地固件导入与 GitHub 正式／测试通道；测试固件需显式选择菜单或 `--prerelease`。旧设备需先通过 USB 迁移官方 OTA 引导程序和分区表，见 docs/ota-upgrade.md；本轮条件及限制见 docs/test-release-0.3.3.md、docs/usb-throughput.md。

先运行 `Install.ps1` 检查文件身份，追加 `-Install` 安装到当前用户 `%USERPROFILE%\.rmlink\app`，避免 AppData 被打包应用重定向到私有缓存。需要登录启动时追加 `-StartAtLogon`。也可在解压目录运行 `Start.ps1`；后台窗口隐藏，`-Foreground`可查看前台日志。检查状态：`bin\RMLinkConnector.exe status`。关闭：`bin\RMLinkConnector.exe stop`。

已有开发服务在线时不应启动第二份服务；安装脚本不会替换正在运行的已安装EXE。配对／目标会话沿用当前用户`.rmlink`状态，公共API和SDK用法见docs/api-v1.md。SDK包含Node／TypeScript包及独立WinHTTP C DLL，前端不需要Orbit或Python。

当前增强目标profile限于STM32F103中容量Cortex-M3，Flash接口进一步限于F103C8的64 KiB。Watch／Timeline／RTT板端订阅、有线分片、无线流、独立录制和错误处理已经实现；真实F103C8有效采样、增强烧录及标准虚拟USB调试已有分版本验收，性能与条件见随包文档。后续性能测试使用72 MHz STM32基线；连接服务不修改用户应用时钟。默认请求4 MHz，内部RMT典型DP周期约4 MHz，目标端外部波形仍未量测；其他目标和已安装VS Code界面仍需单独验证。

firmware/包含已确认开发板GPIO4=SWDIO、GPIO5=SWCLK、GPIO6=预留NRST的应用镜像、bootloader、分区表和flasher_args.json；仅适用本轮已验证的ESP32-S3、16MB Flash／8MB Octal PSRAM开发板。不能直接套用于不同GPIO或内存的成品调试器。请从源仓库构建／刷写脚本操作；manifest分别记录组件版本、固件版本、实际刷入profile和配置哈希，二者版本号不要求相同。

本包不安装、替换或改变内核驱动、启动配置。当前开发电脑使用本地测试签名的usbip-win2 UDE修补驱动；正式微软签名和Keil／IAR等兼容性尚未验收。上游签名版本0.9.8.1在本机仍有已记录的WinUSB空读取消问题；仅安装该版本不能等价于本机修补驱动验收结果。

根仓库、参考版本与许可见manifest及licenses，Orbit适配压缩补丁保留在源码仓库，未默认安装VS Code扩展。
