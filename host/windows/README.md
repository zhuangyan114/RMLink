# Windows 驱动实验补丁

`patches/usbip-win2-cancel-length.patch` 针对 usbip-win2 `v.0.9.8.1` / `55e1fa7f0c2157017b02dc1f3236e98169a535e4`，只修正取消的 Bulk / Interrupt URB 完成长度。上游采用 BSD-2-Clause，版权归 Vadym Hrynchyshyn；发行测试包同时附带原始 LICENSE.txt，参考源码和构建副本不纳入 RMLink Git。

`msbuild/` 为本项目的本地构建工具集适配，连接 VS 2026 与固定版本的 WDK NuGet 包，不修改系统 Visual Studio 安装。构建与签名分别使用 scripts/Build-PatchedVirtualUsbDriver.ps1 和 scripts/Prepare-TestVirtualUsbDriver.ps1。

运行验收、测试模式操作和回退见 [驱动取消问题记录](../../docs/virtual-usb-cancellation.md)。本地测试签名仅供当前电脑实验，不能作为产品驱动签名分发。
