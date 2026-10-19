# 虚拟 USB 空读取消：定位、修复与实机验收

> 历史故障记录：2026-10-19 已停止推进虚拟 USB 路线，标准无线 CMSIS-DAP 改为 OpenOCD TCP 接入。本文驱动安装、测试模式及签名步骤仅记录当时实验，不作为新路线的安装要求；相关补丁与脚本已从当前源码删除，可在检查点 `022ff12` 查阅；本次未执行卸载或更改系统配置。见[当前项目方向](current-direction.md)。

日期：2026-10-14，Windows 11 10.0.26100。14:15:25 启动后的官方 usbip-win2 0.9.8.1 仍出现 pyOCD 0.45.1 启动问题；以下先保留定位时的证据。16:32:28 重启进入测试模式后，修补 UDE 已加载；虚拟 Bulk 空读取消、pyOCD 启动、STM32 三线 SWD 烧录 / 调试及断连重连均通过。本机当前 Secure Boot 关闭、TESTSIGNING 生效、内存完整性保持开启；修补驱动仅为本地测试签名，正式分发待办。

## 复现与驱动日志

连接服务通过 mDNS 发现 DHCP 地址从 192.168.137.151 变为 192.168.137.243，完整 MAC 保持 `50787d15a924`；服务自动重连与挂载成功。pyOCD 在 start_rx 的启动空读中持续循环，20 秒限时终止，尚未进入目标操作。

以新挂载的虚拟接口进行无 OUT 请求的原生 WinUSB 读取，100 ms 后返回成功、64 字节，缓冲内容被改写；再次读取立刻成功。脚本记录返回码、计数与缓冲区是否改变，不输出错误返回的缓冲内容。直连 USB 的同类读取返回错误 121（ERROR_SEM_TIMEOUT），实际计数为零，缓冲未变。

通过安装包自带 PDB 与 WPP 跟踪获得以下证据，日志位于 artifacts/logs/post-reboot-20261014：

- usbip-ude.txt：512 字节 IN URB 的 seqnum=45；随后 CMD_UNLINK 指向 45；服务回复 RET_UNLINK status=-104；UDE 完成日志为 `USBD_CANCELED / STATUS_CANCELLED / Information 0`。
- usbip-filter.txt：上层过滤驱动也观察到 `BULK_OR_INTERRUPT_TRANSFER / USBD_STATUS_CANCELED / STATUS_CANCELLED`。
- idle-native-fresh.json：WinUSB 的第一次读取仍报告成功、64 字节，耗时 104.03 ms；第二次读取立即成功。

这证明取消已经到达服务和驱动，并非服务丢失 UNLINK。WinUSB 结果与驱动取消日志之间存在完成长度不一致。

## 源码原因与补丁

已核对实际发行源码：usbip-win2 tag `v.0.9.8.1`，commit `55e1fa7f0c2157017b02dc1f3236e98169a535e4`。这是发行包对应源码，与 reference-versions.json 中用于最初评估的较新提交分开记录。

正常 RET_SUBMIT 路径在 `drivers/ude/wsk_receive.cpp` 的 ret_submit_urb 中设置实际完成字节数。`drivers/ude/request_list.cpp` 的取消完成路径设置状态，但保留了 URB 原始请求长度。定位时结合 WPP 与 WinUSB 的结果推断：WinUSB 将这个长度当作取消前已有的部分数据，并从尚未填充的缓冲区返回数据。加载补丁后的前后对照已确认清零取消长度修复了本机实测 Bulk 空读取消与 pyOCD 启动问题；没有独立验收 Interrupt、chained MDL 或其他取消路径。

修复候选在 `host/windows/patches/usbip-win2-cancel-length.patch`，仅修改取消的 Bulk / Interrupt URB：完成前将 TransferBufferLength 清零。保留正常响应、会话仲裁、RET_UNLINK 和已执行写命令的语义。

## 构建结果

隔离工作区：`artifacts/build/usbip-win2-cancel`，参考源码没有修改。已用 Visual Studio 2026 / MSVC 14.51、SDK 10.0.28000.2705、WDK NuGet 10.0.28000.2526 构建 x64 Release，生成 SYS、INF、CAT；编译和链接通过，Universal API 校验通过，Inf2Cat 报告 Errors=None、Warnings=None。日志为 build-ude-cancel.log。

源码补丁已经实际编译，无签名构建产物保留在 build 目录，另制作了本地测试签名包。构建与签名本身不等于运行验收；后续实际加载结果见下文。本地构建为实验版，关闭了 Spectre 库选项，不作为正式发行包。

## 已选定当前电脑测试

用户明确选择在当前电脑开启测试模式。2026-10-14 15:38 的管理员只读预检：Secure Boot 开启，Memory Integrity / HVCI 开启，TESTSIGNING 未生效，C 盘 BitLocker 保护状态 Off。预检记录 artifacts/driver-test/preflight.json；未改变启动配置，未自动重启。

已生成专用 RSA 3072 / SHA256 代码签名证书 `RMLink Local Driver Test`，私钥留在当前用户证书存储且不可导出，没有 PFX 或密码文件。用户已确认 Windows 证书信任提示，公共证书导入当前用户 Root，仅用于实验验证。SYS 先嵌入签名，再重新生成 CAT 并签名；SignTool 的 SYS 签名、CAT 签名与 SYS 文件哈希均验证通过。测试包附上 usbip-win2 的 BSD-2-Clause 许可。

测试包：artifacts/driver-test/package。签名 SYS SHA256 `E44D55A36843154D22D06E6CE1271EA74676CB3E233FBFA724CC61C3DF717D31`；完整文件哈希、证书指纹与有效期在 artifacts/driver-test/test-driver.json。实际安装的官方 SYS / INF / CAT 已复制至 artifacts/driver-test/official-backup 并记录哈希；官方 CAT 的微软签名 Valid。

要求来源是 [微软测试签名说明](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/the-testsigning-boot-configuration-option)：Secure Boot 会阻止设置 TESTSIGNING，修改后需重启；HVCI 开启时需要对 SYS 本身做测试签名。当前已经嵌入签名，先保持内存完整性开启；不自动修改其设置。正式分发仍遵循 [驱动签名政策](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/kernel-mode-code-signing-policy--windows-vista-and-later-)。

## 启用与加载步骤

1. 保存工作，手动进入 UEFI / BIOS 暂时关闭 Secure Boot，然后回到 Windows。可用 Windows 设置 → 系统 → 恢复 → 高级启动 → 立即重新启动 → 疑难解答 → 高级选项 → UEFI 固件设置进入。不同电脑的 UEFI 菜单位置不同。
2. 在 PowerShell 运行以下命令。会弹 UAC，脚本核对 Secure Boot 已关闭、C 盘 BitLocker 保护 Off，首次备份 BCD，再设置 TESTSIGNING ON；不自动重启。随后手动重启 Windows，使测试模式生效。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\STM32\RMLink\scripts\Test-VirtualUsbDriver.ps1" -Action EnableTestMode -Elevate
```

3. 重启后继续验收。安装入口如下：核对测试包哈希与证书、检查本次启动确已启用测试模式、检查没有目标占用及其他 USBip 导入设备，再导入机器级测试证书、安装 UDE 包并重启该虚拟控制器。保留官方 filter / CLI；不重启物理 USB 3.0 Hub，不自动重启电脑。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\STM32\RMLink\scripts\Test-VirtualUsbDriver.ps1" -Action Install -Elevate
```

Install 回执核对当前控制器的 INF / SYS 文件哈希；这仍需接下来的真实数据测试确认运行行为。如果 PnP 报告需要系统重启，先处理回执中的 restartRequired，再进行验收。

## 16:32 重启后的实际加载与运行结果

用户完成 Secure Boot 关闭与测试模式重启。16:35:27 的 artifacts/driver-test/install.json 记录本次启动为 16:32:28，Secure Boot=false、TESTSIGNING=true、HVCI=true、C 盘 BitLocker 保护 Off；安装回执 restartRequired=false，脚本没有发起电脑重启。控制器 ROOT\\USB\\0000 使用 oem123.inf、版本 15.9.4.119，状态 Running，SYS SHA256 为上述 `E44D55A36843154D22D06E6CE1271EA74676CB3E233FBFA724CC61C3DF717D31`。17:01:54 的 current-state.json 再次核对相同文件哈希，嵌入签名 Valid，签名者 `CN=RMLink Local Driver Test`；官方 filter 与 CLI 保留。

电脑热点在本次启动后恢复，服务 0.1.1 自动发现固件 0.1.5 的同一完整 MAC，地址变为 192.168.137.13，并自动挂载虚拟探针。实板验收报告均在 artifacts/logs/test-driver-20261014：

- idle-physical.json：直连 USB 对照在 50 / 100 / 500 ms 各两次空读，全部返回 FALSE、error=121、count=0、缓冲未变；每组之后 DAP_Info(FF) 正常返回 64 字节包能力。
- idle-patched.json：修补虚拟 USB 相同六次空读全部通过；50 ms 实测 57.20 / 57.45 ms，100 ms 为 105.95 / 111.10 ms，500 ms 为 509.43 / 504.96 ms。三次取消后查询均通过，没有输出错误缓冲内容。
- pyocd-patched.json：pyOCD 0.45.1 按虚拟序列号 `RM50787D15A924` 读取 CPUID `0x411fc231`、暂停、读取 PC `0x08000D7C`、继续运行通过；本轮未用 pyOCD 烧录。
- accept-virtual-usb.json：OpenOCD 经 Wi-Fi 虚拟 Bulk 完成 64 KiB 烧录 / 校验 / 软件复位，12845.70 ms、无 Error 行；独立完整回读 8120.89 ms。暂停 / 继续、寄存器、3072 字节 RAM、FPB / 软件断点、单步、DWT、FAULT 恢复、SWD_Sequence、异步 Abort、执行期限与部分块传输通过。恢复原 Flash 用时 12288.24 ms，完整哈希匹配原备份，选项字节未变，核心继续运行。
- connector-preconnect.json：真实 API / 虚拟 USB 双向互斥、Connect 前时钟配置、非法时钟不占会话及释放通过。
- connector-reconnect.json：仅断开 RMLink STA、电脑热点保持开启；旧会话 2370.14 ms 报失联，6528.11 ms 自动恢复并重新挂载。旧租约保持失效，新 API 会话与虚拟 USB 都读取到 CPUID，结束 owner 为空。独立复测保存为 connector-preconnect-final.json / connector-reconnect-final.json；恢复耗时不是性能保证。
- idle-after-reconnect.json / pyocd-after-reconnect.json：重挂载后六次空读及三次正常查询再次通过，pyOCD 再次暂停 / 读 PC / 继续运行通过。
- final-target-state.json：物理 USB 独立完整回读 64 KiB 与原备份一致，板端 owner=0、服务 owner=null、核心未暂停，无线已连接、虚拟探针已挂载。

artifacts/driver-test/runtime-acceptance.json 汇总上述 11 份通过报告，runtimeAcceptanceComplete=true，范围限定 Windows Wi-Fi 虚拟 CMSIS-DAP Bulk、STM32F103 64 KiB 三线 SWD 和软件 SYSRESETREQ。安装时回执内的 runtimeAcceptanceComplete=false 是安装当时的历史状态，当前验收结论以该汇总与原始报告为准。物理 NRST 缺线、HID、Keil / IAR、其他取消路径与正式微软签名仍未验收或完成。

复测使用新的日志目录，避免覆盖已有验收记录。下例 `newrun` 应替换为本次独立目录名；父进程默认限时 20 秒并记录子进程日志，驱动回执支持带 BOM 的 UTF-8。

```powershell
python.exe -B scripts/test_virtual_idle.py --output artifacts/logs/newrun/idle.json --driver-metadata artifacts/driver-test/install.json
python.exe -B scripts/test_virtual_idle.py --serial 50787D15A924 --bcd-device 0x0002 --output artifacts/logs/newrun/idle-physical.json
```

## 回退入口

恢复脚本只移除本次测试包对应的 INF，重新选择已核验的官方包，再关闭 TESTSIGNING，移除本次测试证书与私钥。没有自动重启，完成后用户手动重启并在 UEFI 恢复 Secure Boot。

回退入口与官方包备份已准备，本轮没有执行回退，不能标记为已完成运行验证。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\STM32\RMLink\scripts\Test-VirtualUsbDriver.ps1" -Action Restore -Elevate
```

脚本入口 scripts/Prepare-TestVirtualUsbDriver.ps1 与 scripts/Test-VirtualUsbDriver.ps1；操作回执在 artifacts/driver-test，日志为 artifacts/logs/test-driver-*.log。签名产物和设备备份均排除在 Git 外。

## 独立的 pyOCD 连接前时钟问题

直连 USB 不受上述 UDE 空读问题影响，但此前运行 pyOCD 时快速报 `DAP_SWJ_CLOCK failed`。pyOCD 在 DAP_Connect 前调用 set_clock，固件 0.1.4 要求全部目标配置必须先 Connect，因而拒绝合法顺序。

固件 0.1.5 / 服务 0.1.1 允许有效的 SWJ_Clock 先申请完整会话，后续 Connect 延续相同 owner；非法时钟不申请会话，其他客户端的时钟 / Connect / Transfer / Disconnect 被拒绝。目标 GPIO 未确认的默认配置仍禁止目标操作。已编译刷写并完成以下验证，均不涉及目标烧录：

- 19 项主机测试及固件主机核心 / 20000 个解析输入通过。
- pyOCD 0.45.1 直连 USB：读 CPUID `0x411fc231`、暂停、读 PC `0x080006AC`、继续运行通过。
- USB / TCP 两个方向的连接前时钟、互斥、Disconnect / TCP 关闭释放通过。
- 真实服务 API / Windows 虚拟 WinUSB 双向互斥与 CPUID 读取通过，API 关闭未 Connect 的时钟会话也能释放。
- USB / TCP 分别完整回读 64 KiB Flash，SHA256 均为 `75bcca6e52d436f377542beb94629ef101d153e214ef085b6af7893d40b347ff`，匹配原备份。
- Node SDK 经新服务完成批量查询与释放，固件版本返回 0.1.5。

上述连接前时钟的独立修复证据在 artifacts/logs/post-reboot-20261014；修补驱动实际加载后的虚拟路径验收另见 artifacts/logs/test-driver-20261014，不与此前官方驱动结果混用。

当前实测 Bulk 空读取消、pyOCD 启动、正常 DAP、OpenOCD 三线烧录 / 调试、目标互斥与 STA 断连重连已通过。正式签名与产品分发仍待完成；本地测试签名不能替代面向用户分发的微软签名。
