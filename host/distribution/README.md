# RMLink Windows x64 开发组件

本包为开发验证用途。服务、SDK及ESP32应用镜像均有SHA256清单；不携带用户热点凭据或配对状态。标准CMSIS-DAP使用本机已安装的虚拟USB驱动；服务也能选择原生USB作为内部承载。

先运行 `Install.ps1` 检查文件身份，追加 `-Install` 安装到当前用户LOCALAPPDATA。需要登录启动时追加 `-StartAtLogon`。也可在解压目录运行 `Start.ps1`；后台窗口隐藏，`-Foreground`可查看前台日志。检查状态：`bin\RMLinkConnector.exe status`。关闭：`bin\RMLinkConnector.exe stop`。

已有开发服务在线时不应启动第二份服务；安装脚本不会替换正在运行的已安装EXE。配对／目标会话沿用当前用户`.rmlink`状态，公共API和SDK用法见docs/api-v1.md。SDK包含Node／TypeScript包及独立WinHTTP C DLL，前端不需要Orbit或Python。

当前增强目标profile限于STM32F103中容量Cortex-M3，Flash接口进一步限于F103C8的64 KiB。Watch／Timeline／RTT板端订阅、有线分片、无线流和错误处理已经实现；有效采样率和增强烧录尚未在当前STM32完成验收，详见实施记录。默认请求4 MHz不等于已量测的SWCLK。

firmware/rmlink.bin是已确认开发板GPIO4=SWDIO、GPIO5=SWCLK、GPIO6=预留NRST的应用镜像；不能直接套用于不同GPIO的成品调试器。完整ESP32烧录需要固定基座的bootloader／partition配置和源仓库构建脚本。

本包不安装、替换或改变内核驱动、启动配置。当前开发电脑使用本地测试签名的usbip-win2 UDE修补驱动；正式微软签名和Keil／IAR等兼容性尚未验收。上游签名版本0.9.8.1在本机仍有已记录的WinUSB空读取消问题；仅安装该版本不能等价于本机修补驱动验收结果。

根仓库、参考版本与许可见manifest及licenses，Orbit适配压缩补丁保留在源码仓库，未默认安装VS Code扩展。
