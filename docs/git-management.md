# 仓库协作与管理说明

本项目使用单一根仓库 `D:\STM32\RMLink` 管理所有源码、脚本、测试和文档，主要目录包括 `firmware/`、`host/`、`scripts/`、`tests/` 和 `docs/`。其中 `firmware/` 基于乐鑫 `esp-usb-bridge` 改造，保留原有的 Apache-2.0 许可、版权声明和来源记录。

## 哪些文件不进 Git

- **`reference/`**：上游参考仓库（固定版本清单记录在 [reference-versions.json](reference-versions.json)），全部忽略，不建 submodule 或 gitlink。
- **构建产物与运行数据**：`artifacts/`、下载的工具链、编译输出、设备备份、日志、缓存、`.codegraph` 索引、生成的 `sdkconfig` 以及 `managed_components/` 都不纳入版本控制。
- **敏感配置**：Wi-Fi 账号和密码只写进设备 NVS，绝不写进源码或提交到仓库。

> 原先 `firmware/.git` 的完整历史已备份移至 `artifacts/git-history/firmware-upstream.git`，根仓库直接追踪固件源码，不再嵌套子仓库。

## 提交规范

- 提交标题和正文统一用**中文**；代码标识符、协议名和常用技术缩写（如 SWD、OTA、PSRAM、TCP、WinUSB）保留英文原文。
- 每次提交前顺手核对一下暂存区和格式，确认没有误加 `artifacts/`、`reference/`、`managed_components/` 或 `160000` gitlink：

```powershell
git status --short
git diff --check
git ls-files --stage
```

- 本地提交不会自动推送到远端；除非明确要求推送，否则只做本地提交。
