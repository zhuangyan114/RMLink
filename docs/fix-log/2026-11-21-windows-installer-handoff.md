# 修复控制台退出后 Windows 安装向导未打开

## 日期与用户确认

- 确认日期为 2026-11-21。
- 用户报告“rmlink控制台自己退出后安装向导没有打开”，复测修复版后确认“可以了”。

## 根因与修复

旧版同时用 `DETACHED_PROCESS` 和 `CREATE_NO_WINDOW` 启动 Windows PowerShell。前者会使后者失效。本机用同样的参数执行最小脚本时，PowerShell 以退出码 `0` 结束，脚本没有执行，控制台却按交接成功退出。

`host/rmlink_connector/installer.py` 改为通过 `CREATE_NO_WINDOW` 隐藏运行交接助手，并用 `-EncodedCommand` 传入脚本，安装包路径继续通过环境变量传递。控制台退出前取得 PyInstaller 父进程 PID，助手核对父进程的可执行文件路径后，等待控制台和打包父进程退出。等待超时或启动失败会写入安装包目录下独立的 `installer-launch-*.log`。图形模式通过 `/LANG=chinesesimplified` 直接打开简体中文向导。

`tests/host/test_installer_update.py` 增加真实 Windows PowerShell 回归测试，验证助手确实执行，并在安装文件不可用时留下错误日志。

## 验证

- **自动化测试**中，8 项安装包专项检查通过。Python 3.13 全量主机测试 281 项通过，冻结版终端集成检查 9 项通过。Python 3.11 下有 1 项 USB/Wi-Fi 切换测试失败，原始提交 `a884db7` 可复现，详情见 `baseline-transport.log`。
- **本机构建**使用 Python 3.11.2 和 PyInstaller 6.16.0 生成 Windows x64 `rmlink.exe`，版本为 `0.3.5`，SHA256 为 `301efb30442315c30380c12676e56472fda16de0f20c6866e77353dbf9a08f8b`。仅替换本机控制台可执行文件，原有连接服务继续运行。
- **Windows 实测**走菜单 `5 → 6 → Enter`，控制台以 `0` 退出，约 0.5 秒后识别到 `v0.3.4` 安装包的简体中文许可协议页。冻结版 CLI `rmlink update install --prerelease --yes` 同样打开该向导。两次均在许可协议页取消，保留本机 `0.3.5`。
- **用户手动验证**确认向导可以打开。本次修复未更改固件或执行板端操作。

原始证据位于 `artifacts/tests/installer-handoff-20261121/`。菜单与 CLI 的窗口验证记录分别为 `installed-tui/wizard-evidence.json` 和 `frozen-wizard/wizard-evidence.json`。
