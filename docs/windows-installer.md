# RMLink Windows 安装器

RMLink 安装器适用于 Windows 10/11 x64，包含控制台、后台连接组件和目标算法包。程序已包含 Python 运行环境，使用时无需安装 Python、ESP-IDF 或开发工具链。

双击 `RMLink-版本-windows-x64-setup.exe`，按提示安装。默认目录是当前用户的 `%USERPROFILE%\.rmlink\app`，安装过程无需管理员权限。安装后从开始菜单打开 RMLink；选择加入 PATH 后，也可以在新终端输入 `rmlink`。已经打开的 Windows Terminal 可能需要完全退出再打开。

安装器提供登录时启动后台服务、桌面快捷方式两个可选项。关闭控制台后，后台连接组件仍可运行。首次无线连接请在控制台配置 2.4 GHz Wi-Fi 并选择设备；有线连接请使用设备的原生 USB 口。

升级时直接运行新版安装器。请先结束调试会话并关闭控制台；空闲的已安装后台服务会正常停止，再替换程序文件。安装器不会强制终止调试进程。升级保留配对、Wi-Fi 配置、日志和录制数据。

在 Windows“设置 → 应用 → 已安装的应用”中卸载 RMLink。卸载删除程序、快捷方式、对应的 PATH 和登录自启项，保留 `%USERPROFILE%\.rmlink` 中的用户数据。用户自己放进程序目录的额外文件也不会被递归清除。

## 签名

本安装器不包含内核驱动。有线使用 Windows 自带的 WinUSB，无线使用 TCP；无需申请 WHQL 或安装测试签名证书，也无需更改 Secure Boot 或内存完整性。

`unsigned` 文件名表示没有发布者代码签名，`preview` 表示预览构建。未签名文件在下载或启动时可能触发 SmartScreen；启用 Smart App Control 或组织应用控制策略的电脑还可能阻止运行。正式对外分发建议为应用 EXE、安装器和卸载器使用可信发布者的 Authenticode 代码签名并加时间戳。新签名程序也可能需要积累信誉，签名不保证消除所有提示。参见[微软 SmartScreen 说明](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)。

## 构建

构建需要现有连接组件构建环境（Python、Arm GNU 工具链及目标算法包生成脚本），并安装 Inno Setup 6.7.3 或更新版本。构建环境与终端用户使用环境分开；安装包使用者无需这些工具。运行：

```powershell
.\scripts\Build-Installer.ps1
```

构建器重新生成客户端和服务，按白名单收集程序与许可证，输出到 `artifacts\packages`。正式构建要求 Git 工作区干净；开发验证可追加 `-AllowUncommitted`，产物始终带 `preview`。同名产物不会被覆盖，可用 `-OutputDirectory` 指定新的输出目录。`-Iscc` 可指定 `ISCC.exe` 路径。

签名构建可通过 `-SignCommand` 传入 Inno Setup SignTool 命令，其中 `$f` 表示待签名文件。命令会用于应用、安装器和卸载器；证书或硬件密钥由发布者提供，不随源码或安装包分发。例如在已配置证书的构建环境中：

```powershell
.\scripts\Build-Installer.ps1 -SignCommand 'signtool.exe sign /sha1 证书指纹 /fd SHA256 /tr https://timestamp.digicert.com /td SHA256 $f'
```

无人值守安装可使用 `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`；不会自动打开控制台。`/TASKS="addtopath,startlogon"` 可选择 PATH 和登录自启，`/TASKS=""` 可关闭所有可选项。无人值守卸载使用安装目录内的 `unins000.exe` 和相同静默参数。

安装器的本机安装、升级和卸载验证不能替代干净电脑首次使用、USB 枚举、无线 OpenOCD 调试以及 Windows 应用控制策略下的验收。

中文安装消息来自 Inno Setup 6.7.3 的 `Files/Languages/Unofficial/ChineseSimplified.isl`，保留原作者说明；上游地址为 https://github.com/jrsoftware/issrc/tree/is-6_7_3 。安装包包含 Inno Setup 及各运行组件的许可证。
