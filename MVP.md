# dox MVP 设计文档

状态：Draft v0.1  
日期：2026-08-30  
产品名称：dox

方向调整（2026-08-30）：P0 改为模型优先。dox 不再依赖关键词或固定规则识别用户意图，首先调用 OpenAI-compatible LLM 生成结构化命令计划；规则/Adapter 仅负责后续的平台适配、校验、风险控制和扩展，不作为主要自然语言理解方案。

## 1. 产品定义

dox 是一个本地优先的自然语言命令路由器。用户用一句话描述想完成的终端任务，dox 将其解析为结构化意图，选择合适的工具和参数，生成当前平台可执行的命令，展示风险并等待用户确认。

核心承诺：

- 未来本地 Provider 可完全离线工作；P0 API 模式需要网络
- 常见请求响应足够快，目标是在普通设备上也能在 1 秒内完成
- 默认只展示和确认，不自动执行
- 命令、假设、风险对用户透明
- 新增命令主要通过适配器扩展，不依赖重新训练模型

品牌名为 dox，实际命令也使用 `dox`。`do` 在 Bash、Zsh、PowerShell 中是保留字，不作为可执行文件名。

### 1.1 不可妥协的设计原则

1. 轻量：核心二进制小、启动快、运行时依赖少；模型通过可选 Provider 接入，不把模型运行时硬编码进核心 CLI。
2. 易安装：优先发布单文件二进制和各平台原生包，不要求用户预装 Python、Node.js 或容器运行时。
3. 离线优先：核心校验和命令执行不依赖网络；P0 的自然语言规划使用显式配置的 API LLM，未来本地模型 Provider 接入后提供完整离线规划。

## 2. MVP 范围

### 2.1 目标用户

- 经常使用终端、但不熟悉全部参数的开发者
- 需要快速查命令的初中级用户
- 受网络、隐私或企业环境限制的用户

### 2.2 目标平台与 Shell

首版支持：

- macOS：Zsh、Bash
- Linux：Bash、Zsh
- Windows：PowerShell（首版优先保证 PowerShell 7+，再验证 Windows PowerShell 5.1）

Windows `cmd.exe` 不在支持范围内。用户使用 `cmd.exe` 时，dox 可以提示切换到 PowerShell，但不承诺生成或执行 `cmd.exe` 语法。

### 2.3 明确不做的事

- 不承担代码库理解、长上下文规划和多轮编程任务
- 不默认读取项目文件、网络内容或终端历史
- 不让模型直接获得任意 Shell 执行权限
- 不追求首版覆盖所有命令
- 不绑定任何特定云厂商；P0 的 API Provider 由用户显式配置

## 3. 用户体验

### 3.1 基本调用

```text
dox "解压 xxx.tar 到 xxx 文件夹"
```

也允许将未加引号的参数拼接为空格分隔的自然语言，但文档和示例优先使用引号。

### 3.2 默认输出

```text
意图：解压归档
命令：mkdir -p 'xxx' && tar -xf 'xxx.tar' -C 'xxx'
假设：目标目录不存在时自动创建；是否覆盖同名文件未指定
风险：会写入文件

执行？ [y/N/e/c]
  y 执行
  N 取消（默认）
  e 编辑命令后执行
  c 复制命令
```

命令在执行前必须完整展示。用户确认后才可执行；高风险操作需要额外确认。

### 3.3 推荐选项

```text
--offline       只使用内置适配器和本地模型
--local         强制使用本地模型
--api           允许使用配置的 API LLM
--lang zh|en    设置界面语言（默认 zh）
--config PATH   使用指定配置文件
--copy          输出并复制命令，不执行
--print         只打印命令，适合脚本调用
--explain       显示意图、参数和命令解释
--yes           跳过普通确认；高风险命令仍受策略限制
--json          输出结构化 IntentPlan
```

默认模式是生成、展示、确认执行。脚本调用应显式使用 `--print` 或 `--json`。

### 3.4 用户配置

默认语言为中文；英文输入始终可以被识别。使用 `--lang en` 可切换英文界面，`--lang zh` 切回中文。配置优先级为：命令行参数 > 配置文件 > 默认值。

配置采用 Git 风格层级，优先级为：系统级 < 全局 < 当前目录 < 命令行参数。路径为：

- 系统级：macOS/Linux `/etc/doxconfig`；Windows `%PROGRAMDATA%\\dox\\config`
- 全局：macOS/Linux `~/.doxconfig`；Windows `%USERPROFILE%\\.doxconfig`
- 当前目录：`./.dox/config`

通过 `dox config` 读写配置，不保存 API Key 本身，只保存 API Key 环境变量名：

```bash
dox config --global llm.provider openai-compatible
dox config --global llm.base-url https://api.openai.com/v1
dox config --global llm.model gpt-4o-mini
dox config --global llm.api-key-env OPENAI_API_KEY
dox config --global core.language zh
dox config --list
```

API Key 通过环境变量提供。`--config PATH` 可临时指定单个配置文件。当前生效的 LLM 配置项为 `llm.provider`、`llm.base-url`、`llm.model`、`llm.api-key-env` 和 `llm.timeout-seconds`。

### 3.5 命令修正（不属于 P0）

MVP 只支持当前请求的一次生成。用户可以选择 `e` 手动编辑命令。

后续可增加会话内的自然语言修正，例如：

```text
dox "查找大于 100MB 的日志文件"
# 生成 find 命令

补充：排除 node_modules，只看最近 7 天
```

这个功能只需要保留当前 `IntentPlan`，不需要完整的对话历史或项目上下文，适合作为 P1 的受限功能。

## 4. 总体架构

```text
输入文本
  → 环境探测（平台、Shell、已安装工具）
  → 意图识别与槽位提取
  → 适配器选择
  → 平台命令渲染
  → Shell/参数安全检查
  → 风险评估与必要追问
  → 展示、确认、执行或复制
```

### 4.1 结构化中间表示

模型和规则层统一输出 `IntentPlan`，禁止直接把自由文本交给执行器：

```json
{
  "intent": "archive.extract",
  "args": {
    "source": "xxx.tar",
    "destination": "xxx",
    "overwrite": null
  },
  "platform": "macos",
  "shell": "zsh",
  "tools": ["tar"],
  "risk": "write",
  "assumptions": ["目标目录不存在时创建"],
  "clarification": null
}
```

如果缺少影响正确性或安全性的参数，`clarification` 必须有值，dox 应先追问而不是猜测。

### 4.2 执行模型

内部执行优先使用原生进程 API 和参数数组：

```text
["tar", "-xf", "xxx.tar", "-C", "xxx"]
```

只有确实需要管道、重定向或条件连接时才构造 Shell AST，并使用目标 Shell 的渲染器。展示给用户的 Shell 文本与内部执行计划必须保持可追溯对应关系。

### 4.3 支持判定与能力分级

dox 采用模型优先、受控执行的判定：模型可以理解开放式请求并提出命令，但只有通过结构化 Schema、平台检查和安全策略的计划才允许进入确认流程。未知请求不能直接执行。

每次规划应归入以下状态之一：

- `verified`：模型输出通过 Schema、平台和安全校验，并有对应测试或明确的工具能力
- `clarify`：意图已识别，但缺少影响正确性或安全性的参数
- `inferred`：由模型完成路由或参数推断，经过 Schema 和安全检查后可预览，但尚无完整测试
- `unsupported`：模型无法可靠提出计划，或当前平台/依赖不满足
- `blocked`：命令可能危险，或无法通过安全检查

覆盖范围不应只用“关键词命中率”衡量。评估集需要分别统计：同义表达、参数缺失、复杂组合、未知命令和危险请求。

## 5. 适配器设计

每个命令能力由 Adapter 描述。Adapter 可内置，也可从本地插件目录加载。

```yaml
id: archive.extract
version: 1
platforms: [macos, linux, windows]
tools: [tar, 7z]

slots:
  source: {type: path, required: true}
  destination: {type: path, required: true}
  overwrite: {type: boolean, required: false}

render:
  posix: ...
  powershell: ...

risk: write
examples:
  - 解压 xxx.tar 到 xxx
  - extract an archive into a folder

tests:
  - ...
```

Adapter 必须声明：

- 支持的平台和 Shell
- 依赖的可执行文件及探测方式
- 参数 Schema、默认值和必填项
- 各平台渲染器
- 风险等级和是否支持 dry-run
- 自然语言示例及可自动化测试

新增 Adapter 不应要求重新训练模型。模型只需将请求路由到 `id` 并填充 `slots`。

### 5.1 Adapter 的分发边界

“远程包”指从 URL、Git 仓库或官方 Adapter 仓库安装新的命令描述，不是上传用户文件或终端数据。例如，社区可以额外提供 `ffmpeg`、`kubectl` 或企业内部 CLI 的 Adapter。

远程 Adapter 的价值是：新增命令无需升级 dox 主程序，也无需重新训练模型。它同时带来供应链和执行安全风险，尤其是包含任意插件代码时。

因此 MVP 只支持：

- 内置 Adapter
- 用户从本地路径加载的声明式 Adapter

后续再考虑官方远程仓库。远程包应优先采用无代码的声明式格式，包含版本、平台、依赖、风险和测试；需要执行代码的插件必须经过签名、权限声明和用户确认。

## 6. 首版意图集合

优先实现高频、可测试、边界清晰的能力：

- 文件：复制、移动、创建目录、查看大小、批量重命名
- 查找：按名称、扩展名、大小、修改时间查找
- 文本：grep/rg 搜索、统计行数、查看日志尾部
- 归档：tar/zip/7z 打包和解压
- Git：status、log、diff、分支查看、普通 checkout
- 系统：进程、端口、磁盘空间、系统信息
- 网络：DNS/HTTP 基础诊断

删除、批量覆盖、权限修改、磁盘写入、强制 Git 操作、带 `sudo` 的命令在首版只允许生成和展示，默认不自动执行。

## 7. 模型策略

本地模型调研和候选比较见 [LOCAL-MODEL-RESEARCH.md](LOCAL-MODEL-RESEARCH.md)。当前结论是先以 Needle 2 做 PoC，同时保留 FunctionGemma 和 Qwen3-0.6B 作为对照，不在验证前锁定默认本地模型。

### 7.1 路由顺序

```text
用户配置的 API LLM → 本地小模型 → 可选的确定性降级
```

P0 先使用 API LLM 获得自然语言覆盖能力。规则不再承担主要意图识别，只用于输入规范化、输出校验、风险识别和必要的安全阻断。后续本地模型作为离线 Provider 接入；确定性降级仅覆盖明确的安全场景。

### 7.2 本地模型

- 支持 0.5B～3B 级量化指令模型
- 纯 CPU 可运行，模型格式优先选择便于跨平台分发的格式
- 采用约束解码，只允许输出 `IntentPlan` Schema
- 模型包可独立安装，不应阻塞 CLI 的 API 功能
- 通过常驻进程减少冷启动时间

### 7.3 API LLM

实现统一 Provider 接口，首版支持 OpenAI-compatible endpoint，并预留 Ollama 等本地服务：

```text
Provider.generate(request) -> IntentPlan
```

API 调用必须显式配置或显式启用。默认只发送用户请求和必要的环境信息，不发送项目内容、历史记录或文件内容。

### 7.4 本地模型：内嵌库与独立 daemon

两种方式都通过同一个 `Provider` 接口暴露，避免上层架构绑定具体部署方式。

内嵌库（CLI 进程内加载模型）：

- 优点：安装简单、没有 IPC 和后台进程管理、离线行为直观、跨平台实现成本低
- 缺点：每次启动可能重复加载模型，冷启动较慢；CLI 进程会占用更多内存；同时切换模型或服务多个请求不方便

独立 daemon（例如 `doxd`）：

- 优点：模型常驻内存，连续请求延迟稳定；可以统一管理模型下载、更新和多个 Provider；未来可服务编辑器或其他客户端
- 缺点：安装和升级更复杂；需要处理后台进程生命周期、权限、崩溃恢复和本地 IPC 安全；Windows 需要额外适配 named pipe 或服务管理

建议：P0 先实现 API Provider，并保留统一 `Provider` 接口。未来本地模型采用内嵌库或可选 `doxd`，不改变上层规划和确认流程。

### 7.5 模型接入难度与边界

模型接入本身不是最大难点，让模型直接生成任意 Shell 并在多平台可靠执行才是难点。推荐模型只负责：

- 从 Adapter 候选中选择意图
- 提取和补全参数槽位
- 判断是否需要追问

命令渲染、依赖检查、风险评估和执行仍由确定性代码完成。

接入顺序：

1. 先定义 `Provider` 接口和固定的 `IntentPlan` Schema
2. 让 API LLM 直接生成结构化计划，不要求模型进行长推理
3. 对输出做 Schema、平台、依赖和安全校验
4. 校验失败时降级为 `clarify` 或 `unsupported`，不能直接执行
5. 用同一接口接入本地小模型，比较质量、延迟和隐私收益

API LLM Provider 的工程工作量最低，但需要处理网络、隐私、密钥和 JSON 校验。本地 CPU 模型需要增加推理后端、模型分发、量化格式、跨平台构建和冷启动测试。具体模型型号应在建立评测集后选择，不在 P0 代码中绑定。

当 Adapter 数量增长到上百个时，应先按类别和工具做候选检索，再交给模型，而不是把整个命令目录塞进一次上下文。新增 Adapter 仍然只需要定义 Schema、示例、渲染器和测试，不需要重新训练模型。

## 8. 安全策略

- 默认不执行，默认确认选项为取消
- 命令、参数、假设、风险必须在执行前展示
- 对删除、覆盖、权限、网络、`sudo`、磁盘和强制 Git 操作提高确认等级
- 尽可能使用 argv 执行，避免字符串拼接和隐式 Shell 解析
- 对路径、引号、通配符、重定向和管道进行平台相关转义和检查
- 能够 dry-run 的工具优先先执行 dry-run
- 命令不存在或依赖缺失时明确提示，不自动安装
- 插件声明权限和风险；远程插件需要签名或用户明确确认
- 记录本地执行历史时提供关闭选项，默认不上传

### 8.1 执行历史

执行历史不是 Shell 历史。它是 dox 生成的 `IntentPlan`、最终命令、时间、结果状态和模型来源，用于审计、重复执行和后续修正。

历史可能包含本地路径、主机名或用户输入，因此建议 MVP 采用：

- 仅保存在本机
- 默认保留最近 100 条或 30 天，先到者清理
- 不保存命令输出，不上传遥测
- 对 API Key、Token 等常见敏感值做脱敏
- 提供 `dox history`、`dox history clear` 和关闭历史的配置

这比永久保存完整终端记录更容易解释，也足以支持 P1 的会话内修正。是否启用跨设备同步属于更后续的产品决策。

## 9. 工程建议

核心 CLI 选用 Rust，以获得单文件分发、较小运行时开销、快速启动和可靠的跨平台进程控制。适配器使用声明式 YAML/JSON，复杂能力再通过插件进程扩展。P0 尽量只使用 Rust 标准库；模型推理以后端可选组件接入，避免模型能力成为基础安装依赖。

预期目录结构：

```text
src/
  cli/
  planner/
  adapters/
  renderer/
  safety/
  providers/
adapters/
  builtin/
tests/
  fixtures/
  integration/
docs/
```

## 10. 里程碑

### P0：API 模型闭环

- CLI 参数解析
- 平台和 Shell 探测
- OpenAI-compatible API Provider
- 结构化 IntentPlan 输出
- 命令预览、复制、确认执行
- 基础风险分级
- macOS/Linux/Windows CI

### P1：本地模型与扩展

- 本地模型 Provider
- Needle 2 sidecar PoC 及中文/英文评测
- IntentPlan 约束输出
- 20～30 个意图
- 缺参追问
- 量化模型安装和版本管理
- 当前会话内的自然语言命令修正

### P2：扩展生态

- 用户自定义 Adapter
- Adapter 测试命令
- 官方远程 Adapter 仓库
- 本地缓存和用户偏好

## 11. MVP 验收指标

- API 模型在评测集上的结构化计划语义成功率达到 90% 以上
- 经过校验的计划整体执行成功率达到 90% 以上
- 高风险命令误执行在测试集中的目标为 0
- API 请求的 p95 延迟、超时和失败率单独记录；本地模型接入后再记录热/冷启动延迟
- 缺少关键参数时主动追问，而不是生成猜测命令
- 三个平台的核心 Adapter 均有自动化测试
- 所有执行路径都能追溯到结构化 IntentPlan

测试集应同时包含中文、英文、混合技术词、空格路径、特殊字符路径和故意含糊的请求。

## 12. 已确定与待决定事项

已确定：

- Windows 首版只支持 PowerShell，不支持 `cmd.exe`
- P0 使用 API Provider，并通过 Provider 抽象为本地模型和 daemon 留出空间
- P0 只支持内置和本地声明式 Adapter，不安装远程包
- 执行历史默认本地保留最近 100 条或 30 天，不保存输出、不上传
- 自然语言修正推迟到 P1，只保留当前会话的 `IntentPlan`
- 核心 CLI 选用 Rust，P0 不要求 Python、Node.js 或容器运行时
- 安全校验和命令执行在无网络时仍可运行；P0 的自然语言规划需要显式配置 API Provider，后续本地模型接入后才能完全离线规划
- 本地模型首选 Needle 2 做 PoC；FunctionGemma、Qwen3-0.6B 作为对照

仍待决定：

- 本地模型具体格式和推理后端
- 是否在 P1 发布可选 `doxd`
- 远程 Adapter 仓库的签名、审核和版本策略
- 是否提供匿名、完全自愿的质量遥测
