# 仓库管理

唯一开发仓库根目录为 D:\STM32\RMLink，统一管理 firmware/、host/、scripts/、tests/ 和 docs/。firmware/ 是本项目选定并改造的固件源码，保留上游 Apache-2.0 许可证、版权声明和来源记录。

reference/ 中的独立参考仓库全部忽略，不记录为 submodule 或 gitlink；其提交和来源清单在 docs/reference-versions.json，可从原地址重建。artifacts/、下载工具、构建输出、设备备份、日志、缓存、CodeGraph 索引、生成的 sdkconfig 和 managed_components 均不进入 Git。热点凭据保存在设备 NVS，不写入源码。

原 firmware/.git 元数据完整移到 artifacts/git-history/firmware-upstream.git，保留原工作区历史与暂存信息；这不是删除原历史。根仓库管理实际固件文件，不依赖一个包含未提交修改的嵌套仓库。

常用检查：

```powershell
git -C D:\STM32\RMLink status --short
git -C D:\STM32\RMLink diff --check
git -C D:\STM32\RMLink ls-files --stage
```

提交前检查暂存内容，没有 reference/、artifacts/、managed_components/ 或 mode=160000 的 gitlink。初始化与本地提交不会自动创建远端或推送。
