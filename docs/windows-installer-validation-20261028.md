# Windows 安装器 0.3.4 本机验收

日期：2026-10-28。客户端与连接组件为 0.3.4，未修改固件。安装器使用 Inno Setup 6.7.3 构建，没有内核驱动。

本次产物：`artifacts/packages/installer-preview-20261028-verified/RMLink-0.3.4-windows-x64-setup-unsigned-preview.exe`，12,441,366 字节，SHA256 为 `e8d4908aab6e5fd1295e1be7d5b819689555a4bdf72ccdd9edff907e08fd9dd6`。工作区有未提交修改，产物明确标为 `preview`；未配置证书，Authenticode 状态为 `NotSigned`。

## 已验证

通过实际 EXE 安装器，在含中文和空格的独立目录完成 12 项检查：

- 使用默认选项静默安装成功，不自动启动服务。
- 启用 PATH 和登录自启，并核对对应的当前用户注册项。
- 已安装客户端能启动独立状态目录、独立端口的后台服务。
- 运行中的空闲服务正常停止后，升级成功；没有访问默认端口上的既有服务。
- 升级保留测试用户数据，按 manifest 核对全部 20 个文件的 SHA256。
- 发现打开的控制台时阻止升级，没有替换文件或终止进程。
- 后台服务停止命令失败时阻止升级，没有替换文件或终止进程。
- 关闭可选项后修复安装，移除本安装目录对应的 PATH 和登录自启。
- 卸载先正常停止本安装目录的服务，再删除程序及卸载注册项。
- 卸载保留程序目录中的额外用户文件、独立状态目录和其他路径的登录自启项。

检查结果与日志保存在 `artifacts/tests/windows-installer-20261028-03/`。脚本的 12 个检查标签包含安装步骤及其后置检查，上述条目按功能合并表述。测试结束后恢复原有用户 PATH 和登录自启注册项；原有服务进程仍在运行。

与终端和生命周期相关的 23 项 Python 测试通过；PowerShell 语法检查及 `git diff --check` 通过。使用一个返回成功但不实际签名的命令验证签名入口，构建被拒绝，没有生成伪称已签名的安装器。

复验命令：

```powershell
.\scripts\Build-Installer.ps1 -AllowUncommitted -OutputDirectory artifacts\packages\新的输出目录
python scripts\verify_windows_installer.py --installer 安装器完整路径 --directory artifacts\tests\新的验收目录
```

验收脚本会短暂注册测试安装的当前用户卸载项、PATH 和登录自启，并在结束时恢复 PATH／自启。已有安装器管理的 RMLink 时会拒绝运行，应使用独立 Windows 测试账户。进程冲突测试使用系统 .NET Framework C# 编译器生成本地模拟进程，通过关闭标准输入使其退出。

## 尚未验证

本轮采用静默安装并关闭快捷方式创建，没有做安装向导、开始菜单和桌面快捷方式的可视化验收。停止失败分支使用本地模拟进程验证，没有占用真实目标制造调试冲突。

尚未在干净 Windows 电脑、其他 Windows 版本或组织应用控制策略下验证；没有验证实际可信证书签名、时间戳及签名卸载器。本轮没有重新进行 USB 首次枚举、Wi-Fi 配网、无线 OpenOCD 调试或硬件烧录验收。
