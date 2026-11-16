# RMLink 终端客户端

`rmlink` 是随安装包一起分发的控制台入口，既可以当交互式菜单用，也能直接跑命令行脚本。退出界面后后台连接服务仍会留在用户态继续运行（它不注册 Windows 系统服务，配对和配置保存在 `%USERPROFILE%\.rmlink\connector.json`，日志在 `%USERPROFILE%\.rmlink\logs`）。

## 启动与界面操作

装好后在开始菜单点开 **RMLink**，或者在终端里直接运行 `rmlink` 进入主菜单。主菜单有七个入口：**设备管理**、**Wi-Fi 配置**、**连接管理**、**实时监控**、**固件升级**、**诊断与日志**、**设置**。

- **键盘操作**：方向键移动焦点，`Enter` 进入或确认，也支持数字快捷键；`q` 或 `Esc` 返回上一级（首页按 `q` 或 `Ctrl+C` 退出界面）。需要颜色关闭时可用 `--no-color` 或环境变量 `NO_COLOR`。
- **鼠标操作**：支持左键点击菜单和底部按钮、滚轮翻页或滚动日志、右键返回上一级。输入 Wi-Fi 名称、密码或路径等文字表单时会自动暂停鼠标捕获（密码全程隐藏显示）；需要复制文字时可按住 `Shift` 拖选。

> 鼠标和交互界面已在 Windows Terminal / PowerShell 5.1 ConPTY 下测试；传统旧版 `conhost` 控制台和 Linux / macOS 终端暂未实测。

如果是从源码开发环境安装（非安装包），可以运行：

```powershell
.\scripts\Build-Connector.ps1
.\scripts\Install-Connector.ps1 -Install
rmlink
```

## 常用命令行

所有子命令都支持 `--json`（方便脚本解析）、`--device <完整 MAC>`（多设备共存时指定目标）和 `--no-color`：

```powershell
rmlink devices                              # 列出已发现和已配对设备
rmlink pair <设备ID>                        # 配对设备
rmlink config default <设备ID>              # 设为默认设备
rmlink config alias "工作台"                # 设置设备别名
rmlink wifi status                          # 查询板端 Wi-Fi 状态
rmlink wifi hotspot                         # 读取本机当前开启的 2.4 GHz 热点信息
rmlink wifi configure --ssid "热点名称"     # 通过 USB 下发热点凭据（交互隐藏输密码）
rmlink connect --transport wifi             # 恢复后台连接并优先走 Wi-Fi
rmlink disconnect                           # 释放服务对设备的占用（保留配对）
rmlink unpair                               # 取消配对
rmlink monitor                              # 查看实时收发速率与活动订阅
rmlink info                                 # 查看设备版本与连接信息
rmlink doctor --output .\rmlink-diag.json   # 导出环境、服务与设备诊断包
rmlink logs --lines 80                      # 查看最近后台日志
rmlink service status                       # 检查后台连接服务状态
rmlink service restart                      # 重启后台连接服务
rmlink service autostart on                 # 开启登录后自动启动后台服务（off 关闭）
rmlink firmware status                      # 查看设备当前固件与 OTA 分区状态
rmlink firmware inspect .\rmlink.bin        # 离线检查固件镜像与硬件指纹
rmlink firmware update --latest --yes       # 从 GitHub 升级最新正式固件（加 --prerelease 查测试版）
```

### 几条使用边界

- **配网安全**：脚本自动化传密码可以用 `--password-stdin`（UTF-8 标准输入），开放热点需显式加 `--open-network`。工具不会提供明文密码参数，也不会把密码落盘到本机配置或诊断文件里。
- **操作互斥**：只要目标当前正被 Orbit 或其他 API 会话占用，切换传输、断开连接、取消配对、USB 配网和 OTA 升级都会被直接拒绝。
- **监控指标口径**：首页和 `monitor` 里显示的速率按连接服务实际收发的应用层字节求差（含 DAP 帧头和压缩采样流，不含底层 TCP/IP 或 USB 总线开销）。采样频率按探针自带时间戳计算；如果没有任务在跑会直接显示不可用，不会拿历史缓存伪造当前速率。

## 本机后台 HTTP 接口（API 1.4）

控制台和公开 SDK 底层都通过 `http://127.0.0.1:3243/v1` 与后台服务通信，除了核心调试与订阅接口（见 [API v1 说明](api-v1.md)），终端管理功能对应以下路由：

| 路由 | 用途 |
| --- | --- |
| `GET /v1/usb-devices` | 查询已连接的原生 USB 探针并合并到设备列表 |
| `GET / POST /v1/preferences` | 读取或保存默认设备 |
| `POST /v1/alias` | 修改已配对设备别名 |
| `POST /v1/connect`、`/disconnect`、`/unpair` | 控制设备连接状态与配对 |
| `GET /v1/devices/{id}/metrics` | 读取被动收发计数与当前订阅缓存统计 |
| `GET / POST /v1/devices/{id}/wifi` | 查询网络状态或通过 USB 写入热点凭据 |
| `GET / POST /v1/devices/{id}/firmware` | 查询 OTA 状态，或执行分片上传（`begin/write/finish/abort`）与提交重启 |
