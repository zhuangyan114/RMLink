# RMLink Windows 安装器

适用于 Windows 10/11 x64，打包了 `rmlink` 控制台、后台连接服务、目标 Flash 算法包以及独立的 Python 运行环境。最终用户直接安装即可使用，不用额外配置 Python 或 ESP-IDF。

## 安装、升级与卸载

- **安装**：到 GitHub Releases 下载 `RMLink-<版本>-windows-x64-setup.exe`（未签名版带 `-unsigned` 后缀）双击运行。默认装在当前用户的 `%USERPROFILE%\.rmlink\app`，无需管理员权限。安装向导可选是否加入用户 `PATH` 和开机登录自启。如果选了加入 `PATH`，新开终端就能直接敲 `rmlink`（已经开着的 Windows Terminal 可能需要彻底退出重开才能刷新环境变量）。
- **升级**：直接运行新版安装器覆盖即可。升级前请先结束调试会话并关闭控制台；处于空闲状态的后台服务会正常退出再替换文件，安装器不会强行杀掉正在调试的进程。配对信息、Wi-Fi 配置、日志和录制文件都留在 `%USERPROFILE%\.rmlink` 里，升级不会清掉。
- **卸载**：在 Windows **设置 → 应用 → 已安装的应用** 中卸载。卸载会清理程序文件、快捷方式、安装器写入的 `PATH` 和登录自启项，但会保留 `%USERPROFILE%\.rmlink` 里的用户数据，也不会删除你手动放进安装目录的额外文件。

## 关于驱动与签名

本安装器**不包含内核驱动**。有线模式直接使用 Windows 自带 WinUSB，无线模式走 TCP，因此不需要申请 WHQL 签名、安装测试证书，也不用关闭 Secure Boot 或内存完整性。

如果文件名里带有 `unsigned`，说明该安装包和里面的 EXE 没有配置商业代码签名证书（带 `preview` 则表示构建时 Git 工作区存在未提交改动）。未签名程序在下载或首次启动时可能会被 Windows SmartScreen 拦截提示；开启了严格应用控制策略的电脑也可能会阻止运行。正式对外长期分发建议配置可信的 Authenticode 代码签名和时间戳（参见 [Microsoft SmartScreen 说明](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)）。

## 自行构建安装包

构建端需要 Python 构建环境、Arm GNU 工具链（用于生成目标算法包）以及 Inno Setup 6.7.3 或更新版本：

```powershell
.\scripts\Build-Installer.ps1
```

构建脚本会先重新打包客户端与后台服务，再按白名单收集文件和许可证，最终输出到 `artifacts\packages\`。

- **正式构建要求 Git 工作区干净**；开发调试时可加 `-AllowUncommitted`，产物会自动带上 `-preview` 后缀。
- 同名安装包不会被覆盖，可用 `-OutputDirectory` 指定新的输出目录，用 `-Iscc` 指定 `ISCC.exe` 路径。
- 如需签名，可通过 `-SignCommand` 传入签名命令（`$f` 占位符代表待签名文件），构建器会依次对内部 EXE、安装器和卸载器签名并校验状态：

```powershell
.\scripts\Build-Installer.ps1 -SignCommand 'signtool.exe sign /sha1 证书指纹 /fd SHA256 /tr https://timestamp.digicert.com /td SHA256 $f'
```

## 静默安装与许可证

- 静默安装：`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`（`/TASKS="addtopath,startlogon"` 勾选可选项，`/TASKS=""` 全不选）。
- 静默卸载：运行安装目录下的 `unins000.exe` 并附带相同静默参数。
- 简体中文向导翻译基于 Inno Setup 6.7.3 的 `Files/Languages/Unofficial/ChineseSimplified.isl`（[上游仓库](https://github.com/jrsoftware/issrc/tree/is-6_7_3)），所有组件许可证一并随安装包放在安装目录的 `licenses/` 下。

> 本机自动化已覆盖中文/空格路径静默安装、覆盖升级、进程占用拒绝升级、修复安装和卸载清理；干净无开发环境的新电脑首次点击向导与代码签名流程尚未单独实测。
