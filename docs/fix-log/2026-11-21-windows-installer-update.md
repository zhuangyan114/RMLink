# 从 GitHub 更新 Windows 客户端

## 日期与用户确认

- 确认日期为 2026-11-21。用户在复测安装向导后回复“可以了”。
- 控制台退出后向导未打开的问题另见 [Windows 安装向导启动修复](2026-11-21-windows-installer-handoff.md)。

## 需求与实现

用户可以在控制台里查询、下载并安装 GitHub 上发布的 Windows 安装包。主菜单第 5 项改为“固件/驱动升级”，分别提供探针 OTA、Windows 安装包的正式和测试渠道，以及共用的发布仓库设置。控制台确认页展示版本、签名状态和 SHA256，确认后才下载，并显示进度。CLI 提供 `rmlink update check`、`download` 和 `install`，脚本需使用 `--yes` 确认安装，`--silent` 用于静默安装。

客户端沿用固件下载的 GitHub HTTPS 地址限制。遇到 API 限流时，使用 Release 跳转或 Atom 查询。选择资产时优先匹配带签名命名的安装包，允许回退到 `-unsigned`，过滤 `-preview`。下载与运行前均核对同名 `.sha256`，缓存通过校验后可复用。有活动调试会话时，客户端会拦截安装。

发布工作流分别构建 ESP32-S3 OTA 镜像和 Windows x64 安装包，可从同一标签发布 OTA 清单、镜像和安装包校验文件。正式渠道使用 RMLink V1 的 512 B TCP、Core 0、八线 PSRAM 配置。Windows 安装包包括客户端、连接服务与烧录算法，USB 使用 Windows 自带的 WinUSB。

## 涉及文件

- 下载、校验与交互涉及 `host/rmlink_connector/installer.py`、`cli.py`、`console.py`、`controller.py`、`tui.py`、`formatters.py` 和 `__main__.py`。
- 构建与发布涉及 `.github/workflows/firmware-release.yml`、`scripts/Build-Connector.ps1` 和 `scripts/Build-FlashAlgorithm.ps1`。
- 测试与文档涉及 `tests/host/test_installer_update.py`、`test_terminal_client.py`、`test_console_mouse.py`、`test_ota_ui.py` 和 `docs/terminal-client.md`。

## 验证

- **自动化测试**覆盖资产筛选、限流回退、SHA256 校验和缓存、活动调试拦截，以及安装确认和取消。Python 3.13 下 281 项主机测试通过，冻结版终端集成检查 9 项通过。
- **本机构建与实测**使用 PyInstaller 6.16.0 生成 `0.3.5` Windows x64 客户端。从 GitHub 查询 `v0.3.4`、命中已校验缓存后成功打开真实中文向导，在许可协议页取消。
- **实板验收**使用的 `0.3.5` OTA 镜像及设备信息见 [上位机与板端模式切换记录](2026-11-21-transport-mode-switching.md)。本次 Windows 更新验证未烧录探针。
- **用户手动验证**确认安装向导可以打开。以上结果覆盖安装交接到向导页面，未使用 `--silent` 覆盖本机进行验收。

原始测试日志、命令输出和向导验证结果保存在 `artifacts/tests/installer-handoff-20261121/`。
