# 新增芯片适配规范

适用于 Orbit 普通 CMSIS-DAP 与 RMLink 增强路径共用的目标算法包。依据 2026-10-19 的实现整理；架构、支持矩阵和协议见[统一目标算法包](shared-target-package.md)，每次适配使用[适配记录模板](templates/target-adaptation.md)。本文中的“必须”为适配要求，“建议”为可按条件调整的做法。

## 1. 适配范围与交付物

一次适配必须明确完整型号、容量、接受的别名和目标能力。共享同一算法不等于共享实板验收结果；普通 CMSIS-DAP 与 RMLink、不同探针和传输方式分别记录结果。不得用一个型号成功推断整个系列可用。

完整的统一包适配应交付：器件资料表、目标描述、可复现构建的算法及许可、两条路径的自动化用例、构建产物一致性记录和实板验收状态。没有板卡可以提交资料贡献或实现 Draft，明确待验收项。

| 贡献类型 | 本次必需交付 | 尚未存在或未执行的项目 |
| --- | --- | --- |
| 仅资料 | 精确范围、来源/许可判断、缺口与后续适配计划；检查文档事实、路径和链接 | profile/算法/产物填“未实现”或“不适用”并说明；硬件填“未测”，不要求构建或设备操作 |
| 实现 Draft | 描述/算法、相关测试、生成数据、两端构建及实际产物核对 | 缺板项目填“未测/待实板”，不能因缺板省略已实现能力的自动化 |
| 补充实板验收 | 被测版本/摘要、板卡与传输条件、允许区域、实际结果及缺口 | 未改实现时不要求重复全部构建；必须确认设备与被测产物版本一致 |

当前 schema 要求每个注册目标关联完整算法，不支持用空算法登记“仅调试”目标。仅调试贡献应说明独立实现范围；不得填入未经确认的 Flash 参数凑齐表项。任意 FLM、非 Cortex-M 架构及新安全/多核模型不能只靠新增 JSON 声称支持。

## 2. 先建立可追溯的资料表

优先使用厂商数据手册、参考手册、勘误和官方 DFP/CMSIS 设备头文件。社区代码用于寻找线索；寄存器事实、源码复制与二进制再分发的许可分别判断。引用必须记录 URL、文档编号/版本、页码或章节、访问日期；算法来源另记仓库、完整 commit、文件路径、许可证和改动说明。

| 必需信息 | 记录内容与判定要求 |
| --- | --- |
| 精确器件 | 完整 SKU、别名、封装/容量范围、内核与端序；家族 ID 不能证明的型号信息由板卡或工程补充 |
| 身份预检 | DPIDR、CPUID part、芯片 ID/容量寄存器地址、位宽/掩码及读取前提；身份或容量不符必须在擦写前拒绝 |
| Flash | 基址、容量、bank、全部擦除扇区、扇区编号到控制器编号的映射、读写保护与电压/时钟条件 |
| 编程约束 | 物理编程粒度、地址/长度对齐、尾页、重复编程限制、ECC、缓存维护、超时及错误状态清理 |
| RAM | 每个独立地址窗口、调试器可达性、上电/复位可用性、loader 代码/数据缓冲/栈；不把不连续 RAM 相加作为连续空间 |
| 调试与运行时 | 复位方式、FPB/断点限制、异常状态、低功耗/安全状态；RTT/采样所需 RAM 的可达性 |
| 算法与许可 | Init/UnInit/EraseSector/ProgramPage/Verify 的调用和返回约定、静态数据、可信 BKPT、来源及再分发依据 |

资料存在冲突或缺口时记录原始依据和影响，保持待确认状态。不得从相邻容量、兼容芯片或营销名称猜测边界。

## 3. 文件职责与最小改动范围

先检查 `git status --short`，保留已有修改。芯片参数统一从 [targets.json](../host/algorithms/targets.json) 维护；公共传输、UI、会话控制中不增加散落的型号字符串分支。

| 位置（相对 RMLink 根目录） | 应修改的内容 |
| --- | --- |
| `host/algorithms/targets.json` | 新目标、明确别名、身份/容量、扇区/RAM、loader 和算法元数据 |
| `host/algorithms/<algorithm>/algorithm.c`、`algorithm.ld`、`LICENSE`、`provenance.json` | 新算法或明确容量构建；保留来源和许可 |
| `tests/host/test_target_pack.py`、相关 target/flash 测试 | 独立的型号/容量/边界预期、ABI 与错误路径 |
| `tests/flash/target_pack_test.c`、`tests/capture/capture_test.c` | 运行真实固件状态机的主机模型；新增目标不能只靠枚举数量断言 |
| `host/orbit/tests/shared-target-pack.test.ts` | 同步到 Orbit 的共享目标测试；覆盖 ELF 规划及目标差异 |
| `scripts/`、`host/rmlink_connector/`、`firmware/components/debug_probe/` | 仅在现有描述/执行器无法表达器件时扩展；协议、解析、生成器和测试同步 |
| `host/orbit/patches/`、`host/orbit/README.md` | 必要的 Orbit 增量修改及来源/验证记录；保留已有 RMLink 集成基线 |
| 本规范、适配记录、`docs/shared-target-package.md` | 新能力、资料、实际支持范围与各层验收状态 |

生成文件 `firmware/components/debug_probe/rmlink_target_pack_data.inc` 必须随源码重新生成并检查差异，不手工改数组。`artifacts/build/` 的 JSON/bin/manifest、Orbit 的 `target-pack.json`、同步测试和 SDK 副本由脚本产生或复制，不作为第二个维护入口。

新增算法还必须检查 Orbit 工作树的 `scripts/build-native.ps1`、`scripts/cmsis-dap/verify-flash-algorithm.js`；当前这些入口仍列举已知算法。必要修改应作为可重放的增量补丁保存在 RMLink，更新 provenance。只修改忽略目录中的工作树不构成交付。不得用整个脏工作树的 diff 生成补丁而夹带既有修改。

## 4. 目标描述填写规则

### 4.1 `targets[]`

地址、长度、容量使用字节，超时使用毫秒；只有字段名明确为 `KiB` 时使用 KiB。JSON 数值使用整数。文档中的地址建议写十六进制，并列出容量单位。

| 字段 | 要求 |
| --- | --- |
| `name`、`aliases` | 明确的大写名称，全包唯一；允许输入 trim/大小写归一化，不做系列前缀或模糊容量匹配 |
| `profileId` | 全包唯一整数 `1..255`；新增时分配未用 ID，已有 ID 不改号、不挪作其他器件 |
| `dpIdcode`、`corePart` | 分别为完整 DP 身份和 CPUID part，不能互相替代 |
| `preflight` | `idcodeAddress`、`deviceId`、`flashSizeRegisterAddress`、`expectedFlashSizeKiB`；容量必须等于 `flashSize / 1024` |
| `flashBase`、`flashSize`、`sectors` | 单个连续 Flash 窗口；扇区 `number` 从 0 连续编号，`address/size` 从基址无缝覆盖到末尾，禁止超出 32 位地址空间 |
| `ramRegions` | 各窗口分别写 `name/address/size`，长度为正、互不重叠；可描述不连续 RAM，不能允许读写跨过窗口间空洞 |
| `loaderRamIndex`、`loader` | 选定一块可用 RAM；`address` 等于该 RAM 基址；`buffer`、`stack`、`stackSize` 按下述布局核对 |
| `algorithm` | 对应 `algorithms` 的明确键名；不能因构建文件缺失而换用邻近目标 |
| `optionRegisters` | 只读比较的选项/保护寄存器地址，最多 4 个；不代表允许写 Option Bytes |
| `flashDiagnostics` | 当前配置使用 `statusAddress/controlAddress`；地址必须有该器件依据 |
| `flashWordSize` | 需要时声明 ELF 规划使用的 Flash 字合并粒度，例如 ECC 编程字；不等于擦除扇区或 ProgramPage 大小 |
| `eraseTimeoutMs`、`programTimeoutMs` | 显式提供正值，并记录依据；不得用无界等待掩盖故障 |
| `validation` | 记录状态及证据入口；当前 `rmlink: "pending-hardware"` 只是声明，不触发验收或能力开关 |

当前固件描述每个目标最多 255 个 RAM 窗口和 255 个擦除扇区。新器件需要多 Flash 窗口、不同身份比较规则或更多条目时，应先扩展数据模型与两端执行器；不得删减真实扇区或关闭检查来通过生成。

### 4.2 `algorithms.<algorithm>`

每个算法键对应 `host/algorithms/<algorithm>/`。键名建议使用小写字母、数字和下划线，以便生成合法的 C 标识符。

| 字段 | 要求 |
| --- | --- |
| `fileName`、`cpu`、`defines` | 唯一输出文件名、实际 `-mcpu` 和容量/控制器宏；与链接脚本一致 |
| `entries` | 相对算法镜像起点的偶数字节偏移，必须在镜像内；必需 `init/uninit/eraseSector/programPage/bkpt`，提供 Verify 时登记 `verify` |
| `staticBase` | 当前执行器中非零值作为 R9 的绝对地址；0 表示采用算法装载基址，不是任意 FLM 的重定位约定 |
| `pageSize` | ProgramPage 的最大数据缓冲大小；`0 < pageSize <= 65536`，为 4 的倍数，并整除每个擦除扇区大小 |
| `preservesPageBuffer` | 按真实实现填写；声称保留缓冲必须验证算法调用不会改写待复用数据 |
| `verifyReturn` | 当前仅允许 `"zero"`，成功返回 0；返回末地址的外部 Verify 不能直接登记 |
| `eraseChip`、`eraseChipAbi` | 可选扩展；当前 F103 的 `rmlink-guarded` 有特定调用契约，不得复制到其他芯片宣称整片擦除可用 |
| `sourceSha256/linkerSha256/binarySha256` | 由构建生成，不手工伪造或沿用旧摘要 |

loader 必须同时满足：

```text
RAM 起点 = loader.address
loader.address + 算法镜像字节数 <= loader.buffer
loader.buffer + pageSize <= loader.stack - loader.stackSize
loader.stack <= 所选 RAM 末尾的下一地址
loader.stack 为 8 字节对齐，代码、缓冲区、栈均位于同一已声明 RAM 窗口
```

算法镜像非空、长度为 4 的倍数，`bkpt` 处必须有可信 `BKPT #0`（字节 `00 BE`）。PC/LR 的 Thumb 位、xPSR、参数寄存器、R9、栈及返回码都必须符合实际调用约定。链接地址必须与装载地址兼容，不能把绝对链接镜像随意搬到另一块 RAM。

只有控制器寄存器、错误处理、编程/ECC 约束和 ABI 一致时才可复用算法。同一源码针对不同容量构建独立镜像时，明确编译宏、容量上限和回归用例；参考 F407VE/F407IG，不能仅放宽目标表的 `flashSize`。外部 FLM 的静态数据、重定位、分阶段 Init/UnInit 和 Verify 语义须先适配并验证。

## 5. 必须保留的执行契约

1. 普通 CMSIS-DAP 通过已选定的 helper owner 执行，RMLink 通过原服务 owner 和唯一 SWD 执行器执行。Watch/Timeline/RTT 复用该 owner，保留 `control > watch > timeline > background` 及会话隔离。
2. 擦写前完成身份、容量、ELF、算法、RAM 布局和包 tag 检查。未知型号、错误容量、旧算法或不匹配的固件明确报错，不隐式回退到其他型号、算法或调试器。
3. `flashBeforeDebug: false` 不执行擦除、编程、Verify 或 Flash 专用复位；正常调试复位仍按配置处理。
4. ELF 使用 `PT_LOAD` 的 Flash LMA；`.data` 的 RAM VMA 不作为 Flash 写入地址，纯 `.bss` 不生成烧录数据。
5. 擦除扇区、物理编程粒度、ProgramPage、实际 RAM payload 分别核对。最大扇区的擦除调用不能虚构同等长度的 RAM payload。
6. 缓存烧录作业拥有完整擦除扇区，扇区内未提供的字节填 `0xFF`，不自动保留旧内容；使用前确认该扇区全部可被覆盖。不连续扇区分组，空洞扇区不擦除；中间组保持暂停，最后一组按请求复位。
7. 保留完整回读比较和选项寄存器前后比较。取消、超时、掉线或写入结果未知时停止后续写入，不自动重放；不得自动清保护或写 Option Bytes。

## 6. 构建、同步与自动化

### 6.1 环境与生成顺序

以下为 PowerShell 示例，起点是 RMLink 根目录；按本机安装填写工具路径和 Orbit 集成工作树。Python 使用 3.11，C 模型用主机 GCC，算法用 ARM GCC，Orbit 另需其原生构建工具。原生命令非零退出时立即停止。

当前 `Build-FlashAlgorithm.ps1` 内仍固定 SDK Python 路径，`Build-Connector.ps1` 内部会再次调用它，且使用默认 ARM GCC。运行前核对这两处配置与本机工具一致；`Build-Connector.ps1 -Python` 仅指定连接组件环境的基础 Python，不能覆盖算法脚本的 Python/GCC。移植构建环境的脚本改动应另行说明。

```powershell
$ErrorActionPreference = 'Stop'
$RepoRoot = (Get-Location).Path
$OrbitRoot = Join-Path $RepoRoot 'artifacts/worktrees/orbit-rmlink'
$Python = 'D:\ESP32\ESP32idf\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe'
$HostGcc = 'C:\mingw64\bin\gcc.exe'

.\scripts\Build-FlashAlgorithm.ps1
if ($LASTEXITCODE -ne 0) { throw '算法构建失败' }
.\scripts\Build-Connector.ps1 -Python $Python
if ($LASTEXITCODE -ne 0) { throw '连接组件构建失败' }
.\scripts\Build-Firmware.ps1 -TargetProfile -TcpPacketSize 512 -ImageCachePsram -BuildTag target-pack
if ($LASTEXITCODE -ne 0) { throw '固件构建失败' }
& $Python .\scripts\sync_orbit_target_pack.py $OrbitRoot
if ($LASTEXITCODE -ne 0) { throw 'Orbit 同步失败' }
```

以上仅构建和同步，不安装、刷写或 OTA。新 schema/固件表/算法改变后，服务、固件、Orbit 必须使用同一最终包；三个构建不要并发修改共用输出。`packVersion` 是人工维护的包版本，`schemaVersion` 是解析契约版本；协议 tag 根据规范化 JSON（包括算法摘要）自动计算，不能用手工改版本号代替同步。

进入 Orbit 工作树，先构建 helper，再同步一次以恢复统一来源的算法二进制：

```powershell
Push-Location $OrbitRoot
try {
    npm run build:native
    if ($LASTEXITCODE -ne 0) { throw 'Orbit 原生构建失败' }
    & $Python (Join-Path $RepoRoot 'scripts/sync_orbit_target_pack.py') $OrbitRoot
    if ($LASTEXITCODE -ne 0) { throw 'Orbit 构建后同步失败' }
} finally { Pop-Location }
```

同步脚本要求已有的 RMLink 集成基线，不能在任意干净 Orbit checkout 上代替完整集成。补丁上下文不符或算法存在不同的本地修改时，先检查差异并保留用户改动，不强制覆盖。新增 helper/原生构建文件改动若不在同步脚本的补丁范围内，须按适配记录重放相应补丁。

### 6.2 必需的用例与命令

新增用例的预期值应来自资料中的独立常量，不能从生产表读出期望值再比较自身。至少覆盖本次新增/修改的能力：

- 精确名称/别名、错误 ID/内核/容量、未知型号、tag 或算法摘要不符，且拒绝发生在写入前。
- 首个、最大、最后扇区，每个 bank 的边界，最后合法字节与下一个非法字节；旧容量不能因新容量放宽。
- 跨扇区、稀疏 ELF、`.data` LMA、纯 `.bss`、段重叠/溢出、尾页、对齐及 ECC 合并。
- 独立 RAM 窗口及空洞、loader/缓冲/栈重叠、入口和 BKPT、返回码、页面缓冲复用。
- 控制器错误、保护/缓存条件、超时、取消、未知写入不重放、清理、profile 锁定及会话重建。
- 两端 Flash/非 Flash 启动和既有目标回归；触及公共 Orbit 行为时包含 J-Link 回归。

RMLink 必须覆盖 Python 实现及运行实际 `rmlink_flash.c/rmlink_target.c` 的 C 模型；涉及采样边界时加 capture 模型。普通 CMSIS-DAP 必须跨过真实 native helper 的参数校验边界，不能只 mock 掉 `runAlgorithm()`。这些主机模型均不执行真实 MCU 指令，算法入口检查也不证明控制器时序正确。

在 RMLink 根目录先运行聚焦用例，补齐用例后再运行全量：

```powershell
$env:PYTHONPATH = "$RepoRoot\host;$RepoRoot\scripts"
New-Item -ItemType Directory -Force (Join-Path $RepoRoot 'artifacts/tests') | Out-Null
& $Python -m unittest discover -s tests/host -p test_target_pack.py
if ($LASTEXITCODE -ne 0) { throw '目标包测试失败' }
.\scripts\Test-BufferedFlash.ps1 -Gcc $HostGcc
.\scripts\Test-Capture.ps1 -Gcc $HostGcc
& $Python -m unittest discover -s tests/host
if ($LASTEXITCODE -ne 0) { throw '主机回归失败' }
node --test tests/sdk/flash.test.mjs
if ($LASTEXITCODE -ne 0) { throw 'SDK 回归失败' }
```

在 Orbit 根目录依次执行下列命令，每项成功后再继续。新算法须加入原生算法检查清单，不能让已有四种算法通过冒充新算法已测：

```powershell
npx vitest run src/ozone-backend/shared-target-pack.test.ts src/ozone-backend/cmsis-dap-flasher.test.ts src/ozone-backend/session-target-channel.test.ts src/debug/dap-session-cmsis-dap.test.ts
if ($LASTEXITCODE -ne 0) { throw 'Orbit 聚焦测试失败' }
npm run typecheck
if ($LASTEXITCODE -ne 0) { throw 'Orbit 类型检查失败' }
npm run test:cmsis-dap:algorithm
if ($LASTEXITCODE -ne 0) { throw 'CMSIS-DAP 算法检查失败' }
npm run test:cmsis-dap:mock
if ($LASTEXITCODE -ne 0) { throw 'CMSIS-DAP native mock 失败' }
npm run test:cpp-channel:mock
if ($LASTEXITCODE -ne 0) { throw 'J-Link mock 回归失败' }
npm test -- --maxWorkers=1 --minWorkers=1
if ($LASTEXITCODE -ne 0) { throw 'Orbit 全量回归失败' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Orbit 扩展构建失败' }
```

最后回到 RMLink，用构建连接组件产生的 PyInstaller 环境检查最终产物：

```powershell
$VerifyPython = Join-Path $RepoRoot 'artifacts/tools/connector-build-venv/Scripts/python.exe'
& $VerifyPython .\scripts\verify_target_package.py `
    --orbit $OrbitRoot `
    --firmware .\artifacts\build\firmware-target-tcp512-core0-psram-target-pack\rmlink.bin `
    --connector .\artifacts\build\connector\dist\RMLinkConnector.exe
if ($LASTEXITCODE -ne 0) { throw '最终目标包一致性检查失败' }
git diff --check
```

该检查读取冻结 EXE 中的包及算法、Orbit 算法和固件内算法字节/tag，输出 `artifacts/logs/shared-target-package-verification.json`。它不证明设备已刷入该固件、VSIX 包含相同产物或已安装扩展已经更新；需要交付 VSIX 时，另核对包内 helper/算法/依赖及哈希。

## 7. 分层验收与实板基线

每个型号/容量 × 探针固件 × 实际 transport 单独记录。普通 CMSIS-DAP 的 HID/WinUSB，RMLink 的实际 USB/Wi-Fi 路径及虚拟 USB 均不能互相替代证据。记录板卡、电源/VTref、SWD/NRST、SWD 频率、选定 owner、服务/helper/固件/ELF 版本及 SHA256、包 tag；serial 可脱敏，寄存器、地址、返回码和错误状态保留。

| 层级 | 必须完成的内容 | 可写的结论 |
| --- | --- | --- |
| 资料/源码 | 资料、描述、算法、许可齐全，或明确列出缺口 | 已登记/已实现；不能写实板可用 |
| 自动化/构建 | 相关主机模型、native、构建与最终产物核对通过 | 自动化及构建通过 |
| 基本实板调试 | 下表 1、6、7 | 指定条件下基本调试通过 |
| 实板 Flash | 在基本调试基础上完成 2–5，明确实际覆盖区域 | 指定区域及条件下 Flash 通过 |
| 运行时/异常/长稳 | 下表 8、9、10 分项记录 | 只写实际通过的组合、轮数和时长 |

下面是新增芯片的最低验收基线，仅在已有授权范围内操作；只读资料或 Draft 贡献不要求补造硬件结果。

| 阶段 | 操作与通过条件 |
| --- | --- |
| 1. 身份 | 经正式 owner 读取安全可读的 DP/CPUID/芯片/容量，保存 raw 值，配置一致 |
| 2. RAM stub | 在允许的 loader RAM 执行最小返回，核对 PC/LR/SP/xPSR、可信 BKPT 和返回状态 |
| 3. 初始化 | Init/UnInit 各至少 1 次，完成可确认，无 fault/lockup |
| 4. 测试扇区 | 在允许区域擦除、编程已知模式、完整回读比较，选项字节未变；记录实际扇区/bank |
| 5. ELF | 具体 fixture 的完整烧录、校验、入口运行及 `.data` 初始化正确，`.bss` 不生成 Flash 数据 |
| 6. 非 Flash 启动 | `flashBeforeDebug: false` 启停至少 3 轮，无 Flash 操作/专用复位，owner 正常释放并可重开 |
| 7. 基本调试 | halt/continue/reset、用户断点和支持的单步组合至少 10 轮，PC/state 正确，临时断点清理；RMLink 当前 step-over/step-out 填“未实现”，不得标通过 |
| 8. 运行时 | 声称支持的 Watch/Timeline/RTT 共存至少 60 秒，控制后恢复，记录成功率、实际频率和延迟 |
| 9. 异常恢复 | 授权的断线/owner 退出后重建会话至少 3 轮，旧结果不进入新会话，未知写入不重放 |
| 10. 长稳 | 声称支持的组合连续至少 30 分钟，记录样本/字节、丢失/超期、延迟、内存及 owner 泄漏情况 |

首个/最大/最后扇区、各 bank 和容量末尾必须有自动化覆盖；实板仅测试获准区域，未测边界明确留空。缓存、保护状态、复位接线等条件不适用时写明理由；未执行填写“未测”，不能填“不适用”掩盖缺失。

出现身份不符、非法地址/入口/BKPT、保护错误、Verify mismatch、超时或未知写入时停止后续编程。保留失败与修复后重验记录，列出已修改区域和恢复方法；不得通过放宽校验、换邻近型号、清保护或自动重试继续。

## 8. 评审、提交与完成标准

从[模板](templates/target-adaptation.md)建立 `docs/targets/<型号>.md` 作为长期记录，PR 可复用其摘要。按能力和链路逐项列“通过/失败/未测/不适用及原因”，附可共享的源码 fixture、命令、版本和证据摘要；原始私有固件、日志和设备数据留在 `artifacts/` 或本地。

提交前按第 1 节的贡献类型检查。下列生成数据、测试及产物要求适用于新增/修改实现；仅资料贡献检查资料与文档，补充实板记录核对被测版本和证据，不要求为未修改的实现重复全套构建。

实现适配必须满足：

- 目标和算法的唯一来源明确，生成数据已更新，旧 ID、容量和未涉及目标保持原有契约。
- 两端自动化与实际产物检查有本次结果；缺板的实现标为 Draft/待实板，不沿用旧版本的全部通过结论。
- README、支持矩阵、来源/许可和适配记录一致；没有把未实现单步或未测 transport 写成支持。
- 给出撤回该目标/算法及恢复上一组配套服务、固件、Orbit 包的方法；不混搭包 tag。
- 仅在用户要求提交时提交，标题/正文使用中文；推送另按用户指令处理。

执行 `git diff --check`；准备提交后再检查 `git diff --cached --check`、`git diff --cached --name-only`、`git ls-files --stage`。仅暂存本仓库源码、脚本、测试、生成的固件源码和文档，不提交 `reference/`、`artifacts/`、日志、构建二进制或 `160000` gitlink。部署、安装、OTA 与发布分别记录完成状态，构建完成不代表这些动作已执行。
