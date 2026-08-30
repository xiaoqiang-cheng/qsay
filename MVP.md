# dox MVP 设计文档

状态：Draft v0.2

日期：2026-08-30

产品名称：dox

## 1. 产品定义

dox 是一个轻量、本地优先的自然语言命令路由器。用户用一句中文或英文描述终端任务，模型负责理解意图和提取参数；确定性代码负责结构校验、平台适配、风险升级、确认和执行。

```text
dox "解压 backup.tar 到 ./backup"
```

首版支持 macOS、Linux 和 Windows。Windows 仅支持 PowerShell，不支持 `cmd.exe`。命令名使用 `dox`；`do` 在常见 Shell 中存在语法冲突，不作为可执行文件名。

## 2. 不可妥协的原则

1. 轻量：核心使用 Python 标准库，模型运行时和 API 能力按需安装或配置。
2. 易安装：目标是一次包管理器命令完成安装，不要求 Node.js、容器或常驻服务。
3. 准确且快速优先：离线是重要能力，但不以牺牲结果正确性和响应速度为代价。默认 Provider 应由评估门槛决定；下载本地模型后仍必须可以完全断网运行。
4. 模型优先：不使用关键词规则承担自然语言意图识别。模型负责路由和参数提取。
5. 受控执行：模型不能绕过结构校验、风险策略和用户确认；默认不静默执行命令。
6. 可评估：本地模型和 API 模型必须使用同一组用例、工具定义和指标比较。

## 3. MVP 范围

### 3.1 目标用户

- 忘记命令或复杂参数、但知道自己想完成什么的终端用户
- 希望比通用 Coding Agent 更快得到一条命令的开发者
- 需要离线、隐私友好或低成本命令规划的用户

### 3.2 做什么

- 接受中文、英文和中英混合请求
- 本地 Qwen3-0.6B 或 OpenAI-compatible API 规划
- 展示命令、假设和风险
- 用户确认后执行，或只打印、复制、输出 JSON
- 用 Git 风格配置切换模型、后端和语言
- 评估模型的工具路由、参数提取、安全误调用和延迟

### 3.3 不做什么

- 不理解整个代码库，不执行长上下文、多轮编程任务
- 不默认读取项目文件、终端历史或网络内容
- 不自动安装命令依赖
- 不支持 Windows `cmd.exe`
- MVP 不从远程仓库安装 Adapter 或可执行插件
- 不承诺 0.6B 模型可以可靠生成所有任意 Shell 命令

## 4. 用户体验

默认模式展示计划并等待确认：

```text
意图：archive.extract
命令：mkdir -p './backup' && tar -xf 'backup.tar' -C './backup'
假设：目标目录不存在时创建
风险：会写入文件

执行？
  y 执行
  N 取消（默认）
  e 编辑（MVP 暂未实现）
  c 复制
```

选项含义：

- `--print`：只打印生成的命令，不执行
- `--json`：输出结构化计划
- `--copy`：复制命令，不执行
- `--yes`：跳过普通确认；高风险命令仍不自动执行
- `--local` / `--offline`：强制使用本地模型
- `--api`：强制使用配置的 API LLM
- `--backend llama-cpp|mlx|server`：选择本地推理后端
- `--model-path PATH`：临时指定本地模型
- `--lang zh|en`：切换界面语言，默认中文

缺少影响正确性或安全性的参数时，模型应返回澄清问题而不是猜测。非交互终端必须显式使用 `--print`、`--copy`、`--json` 或 `--yes`。

## 5. 技术方案

### 5.1 Python 核心

MVP 全部使用 Python 3.9+ 实现：

```text
dox/
  cli.py          CLI、确认和执行
  config.py       Git 风格分层配置
  providers.py    本地与 API Provider
  schema.py       计划解析、Shell 与风险校验
  evaluation.py   评估集、统计和报表
```

基础安装不含第三方运行时依赖。`llama-cpp-python`、`mlx-lm` 和下载工具均为可选依赖。Python 的取舍是安装包不再是 Rust 单文件二进制，但能明显降低模型接入和实验成本；启动性能主要由模型加载与推理决定，而不是 CLI 语言。

### 5.2 处理链路

```text
自然语言输入
  → Provider（本地 Qwen 或 API LLM）
  → 结构化计划 / 工具调用
  → Schema 与目标 Shell 校验
  → 确定性风险升级
  → 展示与用户确认
  → PowerShell 或 POSIX Shell 执行
```

当前命令模式让模型返回包含 `command` 的 JSON 计划，以获得开放命令覆盖；评估模式则让模型从固定工具 Schema 中选择工具并提取参数。发布质量的目标架构是后者：

```text
模型输出 {tool, arguments}
  → Adapter 确定性渲染 macOS/Linux/PowerShell 命令
```

这项收敛很重要。真实冒烟中，Qwen3-0.6B 曾把普通 `.tar` 错写成 `tar -xzf`，并忽略目标目录。因此“能生成命令”不等于“命令正确”；在核心 Adapter 完成前，本地任意 Shell 输出只视为需要人工审查的原型能力。

### 5.3 Provider 接口

所有后端暴露统一接口：

```text
Provider.complete(messages, tools?, max_tokens) -> assistant message
```

- `local`：完全离线，适合隐私、断网和低成本场景
- `api`：默认；OpenAI-compatible `/chat/completions`，作为当前高质量快速路径
- API Key 只从用户指定的环境变量读取
- 请求只包含当前自然语言请求、必要的目标平台信息和工具 Schema，不默认发送项目内容或历史
- API 默认使用零温度、短输出和无思考过程提示；API 服务是否支持真正关闭隐藏 reasoning 取决于服务商，没有跨供应商统一参数
- llama.cpp/server 追加 Qwen 的 `/no_think`，MLX 显式使用 `enable_thinking=False`
- Provider 返回的 token 用量进入计划的 `token_usage`，供交互展示和 JSON/评估报表使用

## 6. 默认本地模型与跨平台方案

默认模型为 Qwen3-0.6B。默认量化目标是 Q4_K_M GGUF，以较小磁盘和内存占用换取可接受的路由质量。

### 6.1 三种后端

| Backend | 平台 | 优点 | 代价 |
|---|---|---|---|
| `llama-cpp` | macOS/Linux/Windows | GGUF 通用、CPU 可运行、完全离线 | 进程内首次加载较慢；Python wheel 可用性需逐平台验证 |
| `mlx` | Apple Silicon macOS | Metal 加速好，延迟通常更低 | 只适用于 Apple Silicon，模型格式不与 GGUF 共用 |
| `server` | 全平台 | 模型常驻，连续调用快；可连接 llama.cpp/Ollama 等 | 需要用户单独启动和管理本地服务 |

`auto` 在 MVP 中解析为 `llama-cpp`，确保三平台默认行为一致。如果配置了本地 endpoint，则可使用 `server`。MLX 是 Apple Silicon 的显式优化项，而不是跨平台默认项。

### 6.2 内嵌库与 daemon 的决定

MVP 默认采用进程内 `llama-cpp-python`：概念和安装路径最简单，不引入后台进程生命周期、端口、IPC 或服务权限。

同时保留 `server` backend 作为可选常驻方案，它已经能连接任何 OpenAI-compatible 本地服务。暂不自研 `doxd`，因为它会增加安装、升级、崩溃恢复和 Windows 服务管理成本。若冷启动实测成为主要体验瓶颈，再把受控的本地 daemon 作为独立里程碑。

### 6.3 模型分发

开发阶段的 `dox model download` 使用公开的社区 Q4_K_M GGUF。正式发行不能仅依赖社区转换，必须从 Qwen 官方 Apache-2.0 权重构建发布文件，并固定来源、版本和 SHA-256。模型不嵌入 Python wheel，以免基础安装膨胀；用户可以下载默认模型，也可以配置本地路径。

## 7. 配置

配置方式向 Git 学习。优先级为：系统级 < 全局 < 当前目录 < 命令行。

- macOS/Linux 系统级：`/etc/doxconfig`
- Windows 系统级：`%PROGRAMDATA%\dox\config`
- macOS/Linux 全局：`~/.doxconfig`
- Windows 全局：`%USERPROFILE%\.doxconfig`
- 当前目录：`./.dox/config`

```bash
dox config --global llm.provider api
dox config --global llm.base-url https://api.openai.com/v1
dox config --global llm.model gpt-4o-mini
dox config --global llm.api-key-env OPENAI_API_KEY
```

API 尚未配置时，dox 必须打印可直接复制的配置命令和 API Key 环境变量示例，不自动回退到本地模型。离线模式显式配置：

```bash
dox config --global llm.provider local
dox config --global local.backend auto
dox config --global local.model-path /path/to/Qwen3-0.6B-Q4_K_M.gguf
dox config --global core.language zh
dox config --list
```

macOS/Linux 的全局配置文件是 `~/.doxconfig`，Windows 是 `%USERPROFILE%\.doxconfig`；项目级文件是当前目录的 `.dox/config`。文件只在用户执行对应的配置写入命令后创建。

## 8. 评估模式

命令：

```bash
dox eval --local --output reports/qwen-local.json --verbose
dox eval --api --output reports/api.json --verbose
```

本地模型和 API 使用相同的 OpenAI function tool Schema 和 JSONL 用例。首版报表包含：

- 工具 exact match
- 正常请求的参数 exact match
- negative、irrelevant 和危险拒绝场景的 critical false-call
- 单条请求错误数
- p50、p95 和平均延迟
- 输入、输出和总 token 用量
- 失败明细与完整 JSON 行级结果

可通过 `--cases` 使用自定义数据集，通过可重复的 `--locale` 筛选 `zh`、`en`、`mixed`，通过 `--limit` 做冒烟测试。单条失败不会中止整份报告。

退出码：

- `0`：评估完成且没有 critical false-call
- `1`：评估完成但存在 critical false-call
- `2`：配置、用例或整体初始化失败

注意：退出码 `0` 不代表准确率达标；CI 或发布流程还需要读取 JSON 指标并应用质量门槛。

## 9. Adapter 与命令覆盖

“尽可能多的意图”不等于把每个自然语言表达写成规则。模型负责把各种表达路由到结构化能力，Adapter 负责确定性命令生成。

优先能力：

- 文件：复制、移动、目录、大小、批量重命名
- 查找：名称、扩展名、大小、时间
- 文本：搜索、统计、日志尾部
- 归档：tar、zip、7z 打包和解压
- Git：status、log、diff、分支和安全 checkout
- 系统：进程、端口、磁盘和系统信息
- 网络：DNS、HTTP 基础诊断
- 包管理：常见语言包管理器的显式安装请求

当 Adapter 数量增长时，先用模型选择类别或候选集合，再进行工具调用，避免把数百个完整 Schema 全部塞入一次上下文。新增 Adapter 应包含工具名、参数 Schema、平台、PowerShell/POSIX 渲染器、风险和测试，不要求重新训练模型。

MVP 只加载内置能力和用户本地文件。这里的“远程 Adapter 包”是指从官方或社区仓库下载新的命令描述，不是上传用户文件。远程分发需要签名、审核和供应链策略，推迟到后续版本。

### 9.1 工具帮助的按需检索

当 Adapter 已知时，优先直接由确定性渲染器生成命令，不额外执行 `-h`。对于未覆盖但本机已安装的工具，可以增加受控的二阶段规划：

```text
第一次模型调用：识别候选可执行文件和所需子命令
  → dox 验证可执行文件来自 PATH
  → 以 argv、超时、输出长度上限运行 tool --help / subcommand --help
  → 第二次模型调用：基于用户请求和帮助文本生成结构化计划
```

这能减少模型对冷门参数的记忆依赖，但不能作为主要正确性来源：帮助文本可能很长、版本相关、写入 stderr、启动缓慢，少数程序的 `-h` 甚至有副作用。禁止让模型决定任意探测命令；只允许执行已验证程序的静态帮助选项，并缓存 `可执行文件路径 + 版本 + 帮助参数` 的结果。

更稳妥的顺序是：内置 Adapter → 缓存的可信文档/帮助 → 受控本机 `--help` → API 规划 → 无法验证则只展示或拒绝。帮助检索会增加一次进程和通常一次模型调用，因此只在低置信、未知工具或 API 模型明确请求文档时触发。

## 10. 安全边界

- 默认取消，执行前始终展示最终命令
- 模型声明的风险只能被确定性代码升级，不能降级
- 高风险命令禁止通过 `--yes` 静默执行
- 拒绝控制字符和不匹配当前平台的 Shell
- Windows 只允许 PowerShell 计划
- 删除、根目录、权限、磁盘、`sudo`、强制 Git 和下载执行需要更严格策略
- 命令不存在时明确报错，不自动安装
- API Key 不写入 dox 配置，不上传遥测或命令历史

当前风险扫描只是 MVP 下限，不是完整 Shell parser。进入可发布阶段前，需要将高频能力迁移到 Adapter/argv 执行，并为管道、重定向、引号和路径边界增加平台测试。

## 11. 里程碑

### P0：Python 模型闭环（当前）

- Python CLI 与 Git 风格配置
- 默认 OpenAI-compatible API；未配置时输出配置指引
- 本地 Qwen3-0.6B 离线 Provider
- llama.cpp、MLX、server 三种本地 backend
- OpenAI-compatible API Provider
- 命令计划展示、复制、确认和执行
- 中文默认、英文支持、Windows PowerShell
- `dox eval` 终端与 JSON 报表

### P0.5：安装与质量收敛

- macOS/Linux/Windows 安装矩阵和预构建 wheel 验证
- 从官方权重产出带校验和的默认 GGUF
- 扩充真实中文、英文、混合表达与拒绝用例
- 为准确率、安全误调用、冷/热延迟建立发布门槛

### P1：确定性 Adapter 闭环

- 将高频命令从模型直出 Shell 迁移为工具调用
- POSIX 与 PowerShell 确定性渲染
- 依赖探测、缺参追问和 Adapter 自动测试
- 根据数量增加分层候选路由

### P2：性能与扩展

- 根据实测决定是否提供 `doxd`
- 声明式本地 Adapter
- 经过签名和审核的远程 Adapter 仓库
- 受限的当前会话命令修正

## 12. 验收标准

- 工具 exact match 与正常参数 exact match 均达到约定门槛；初始目标 90%
- critical false-call 为 0
- 缺少关键参数时拒绝调用或提出澄清，不猜测
- macOS、Linux、Windows PowerShell 均有自动化测试和安装验证
- 本地模式断网可用；产品默认 Provider 由准确率、critical false-call 和延迟门槛决定
- 报表记录 Provider、平台、逐条结果和延迟
- 所有执行路径经过结构、Shell、风险和确认层

当前本地前三条冒烟用例通过不代表完整质量达标；必须以完整评估集、跨平台执行测试和失败样本回归作为发布依据。

## 13. 已确定与后续决策

已确定：

- 全部切换到 Python 3.9+
- 产品默认 Provider 为 OpenAI-compatible API；本地模型默认候选为 Qwen3-0.6B
- 跨平台默认为 GGUF + llama.cpp
- Apple Silicon 可选 MLX，连续调用可选本地 server
- Windows 只支持 PowerShell
- 默认中文，同时支持英文和混合输入
- API Provider 默认，本地离线能力不依赖 API
- 不用关键词规则承担意图识别
- 增加本地/API 共用的评估模式和 JSON 报表

仍需用数据决定：

- Q4_K_M 正式转换的最终模型文件、校验和和分发地址
- 三个平台最省心的安装包形式，尤其是 Windows 的 llama.cpp 依赖
- Qwen3-0.6B 达不到完整质量门槛时，是优化提示、微调路由模型，还是把复杂请求升级到 API
- 是否以及何时自研常驻 `doxd`
