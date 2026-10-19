# 已退役的 Windows 虚拟 USB 实验

2026-10-19：无线 CMSIS-DAP 改为 TCP/OpenOCD 后，已按用户要求删除本目录的 UDE 补丁、WDK/MSBuild 适配与专用许可证副本，以及仓库中的虚拟 USB 驱动构建、签名、安装和验收脚本。

原实现可在切换前本地检查点 `022ff12` 查阅。[取消问题与实机验收](../../docs/virtual-usb-cancellation.md)保留当时的证据，不作为当前安装流程。此次源码清理未卸载本机驱动或改变启动配置。

当前接入方式见[项目方向](../../docs/current-direction.md)。
