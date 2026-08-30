# dox

轻量、模型优先的自然语言命令路由器。输入一句话，dox 使用本地 Qwen3-0.6B 或 OpenAI-compatible API 生成结构化命令计划，展示风险并在确认后执行。

当前实现已经全部迁移到 Python。默认 Provider 是 OpenAI-compatible API，以优先获得更高的命令准确率和更低延迟；本地 Qwen3-0.6B 是显式的离线选项。macOS、Linux 和 Windows 共用 GGUF + llama.cpp 路径，Apple Silicon 可以选择 MLX 加速。Windows 只支持 PowerShell，不支持 `cmd.exe`。

## 安装

需要 Python 3.9+。开发环境安装：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[local,dev]'
```

Windows PowerShell：

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[local,dev]"
```

安装完成后可直接运行 `dox`。默认 API 模式不需要本地推理依赖，可执行 `python -m pip install -e .`；需要离线模式时再安装 `.[local]`。

### 本地模型

默认模型为 Qwen3-0.6B Q4_K_M GGUF：

```bash
dox model path
python -m pip install huggingface-hub
dox model download
```

`dox model download` 当前使用公开的社区 Q4_K_M 转换以减小包体。正式发行包会由项目从 Qwen 官方 Apache-2.0 权重自行转换并固定 SHA-256。也可以配置自己验证过的模型：

```bash
dox config --global local.model-path /path/to/Qwen3-0.6B-Q4_K_M.gguf
```

## 使用

默认使用 API。首次使用先配置 OpenAI-compatible endpoint、模型名和 API Key 环境变量：

```bash
dox config --global llm.base-url https://api.openai.com/v1
dox config --global llm.model gpt-4o-mini
dox config --global llm.api-key-env OPENAI_API_KEY
export OPENAI_API_KEY="你的 API Key"
```

未配置时，dox 会打印上述配置方法，不会自动回退到准确率未达门槛的本地模型。配置完成后：

```bash
dox "解压 backup.tar 到 ./backup"
dox --print "查找 src 下所有 rs 文件"
dox --json "查看当前 git 状态"
```

常用选项：

- `--print`：只打印命令，不执行
- `--json`：输出结构化计划
- `--copy`：复制命令
- `--yes`：跳过普通确认，高风险命令除外
- `--local` / `--offline`：强制本地模型
- `--api`：强制 API Provider（当前默认）
- `--backend llama-cpp|mlx|server`：临时选择本地 backend
- `--model-path PATH`：临时指定本地模型
- `--lang zh|en`：设置界面语言，默认中文

## 本地推理后端

| Backend | 平台 | 用途 |
|---|---|---|
| `llama-cpp` | macOS/Linux/Windows | 默认；GGUF、纯 CPU、跨平台 |
| `mlx` | Apple Silicon macOS | 可选加速；需要 `mlx-lm` 和 MLX 模型目录 |
| `server` | 全平台 | 连接本机的 llama.cpp/Ollama/vLLM 等 OpenAI-compatible 服务 |

默认 `auto` 当前解析为 `llama-cpp`，确保三平台语义一致。MLX 需要显式启用：

```bash
python -m pip install mlx-lm
dox config --global local.backend mlx
dox config --global local.model-path /path/to/qwen3-0.6b-mlx-4bit
```

连接常驻本地服务可以避免每次 CLI 调用重新加载模型：

```bash
dox config --global local.backend server
dox config --global local.endpoint http://127.0.0.1:8080/v1
dox config --global local.model Qwen3-0.6B
```

这是当前最值得继续优化的跨平台方案：dox 核心保持纯 Python 标准库，模型进程可以常驻；底层服务仍可由 llama.cpp 在 CPU、Metal、CUDA 或 Vulkan 上运行。

## API Provider

配置方式保持 Git 风格，API Key 只从环境变量读取。由于 API 是默认 Provider，正常使用时无需再添加 `--api`：

```bash
dox config --global llm.base-url https://api.openai.com/v1
dox config --global llm.model gpt-4o-mini
dox config --global llm.api-key-env OPENAI_API_KEY
export OPENAI_API_KEY="你的 API Key"

dox --print "解压 backup.tar 到 ./backup"
```

API Provider 接受 OpenAI-compatible `/chat/completions` 接口。

默认请求使用 `temperature=0`、短输出上限和“只返回最终 JSON”的提示，不要求模型输出思考过程。对于本地 Qwen，llama.cpp/server 会追加 `/no_think`，MLX 使用 `enable_thinking=False`；通用 API 没有统一的隐藏推理开关，具体服务是否在内部推理由服务商和模型决定。

普通交互输出会显示本轮 token 用量，例如：

```text
Token：输入 128，输出 18，总计 146
```

API 返回的用量是服务端统计值；MLX 若服务不提供统计，则显示本地 tokenizer 的估算值；Provider 没有返回用量时会明确显示“服务未返回用量”。`--print` 保持只输出命令，便于脚本使用；`--json` 会在 `token_usage` 字段中保留统计。

## 模型评估模式

`dox eval` 使用相同工具 Schema、相同请求集比较本地模型和 API LLM，统计工具 exact match、正常请求参数 exact match、critical false-call、请求错误和 p50/p95 延迟。

```bash
# 本地模型
dox eval --local --output reports/qwen-local.json --verbose

# 已配置的 API 模型
dox eval --api --output reports/api.json --verbose

# 临时指定 API，不修改配置
dox eval --api \
  --base-url https://api.example.com/v1 \
  --model example-model \
  --api-key-env EXAMPLE_API_KEY \
  --output reports/example-model.json
```

筛选和自定义用例：

```bash
dox eval --api --locale zh --locale mixed --limit 20
dox eval --api --cases ./my-cases.jsonl
```

评估返回码：无 critical false-call 时 `0`，存在 critical false-call 时 `1`，配置或整体请求失败时 `2`。即使单条 API 请求失败，报告仍会继续生成并记录错误。

评估报表也会记录逐条 `token_usage` 以及累计输入/输出 token，方便比较 API、本地模型和不同 Prompt 的成本与延迟。

当前 Qwen3-0.6B Q4_K_M 在本开发机的 33 条完整基线为：工具 exact match 72.7%、正常参数 exact match 94.7%、4 次 critical false-call，p50/p95 约 1.85/1.96 秒。它足以验证本地闭环，但尚未达到发布门槛；否定句、无关请求和危险路径必须继续由确定性安全层阻断。

用例格式：

```json
{"id":"extract","locale":"zh","request":"解压 a.tar 到 ./out","expected_tool":"extract_archive","expected_args":{"source":"a.tar","destination":"./out"},"class":"normal"}
```

## 配置

优先级为系统级 < 全局 < 当前目录 < 命令行：

- 系统级：macOS/Linux `/etc/doxconfig`；Windows `%PROGRAMDATA%\dox\config`
- 全局：macOS/Linux `~/.doxconfig`；Windows `%USERPROFILE%\.doxconfig`
- 当前目录：`./.dox/config`

在 macOS/Linux 上，`dox config --global ...` 写入 `~/.doxconfig`；Windows 写入 `%USERPROFILE%\.doxconfig`。如果从未执行过全局配置命令，文件可能尚不存在，此时使用内置默认值。项目级配置写入当前目录的 `.dox/config`。

```bash
dox config --list
dox config --global core.language en
dox config --global llm.provider api
dox config --global local.backend auto
dox config --unset --global local.model-path
```

完整配置示例：

```toml
[core]
language = "zh"

[llm]
provider = "api"
base-url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api-key-env = "OPENAI_API_KEY"
timeout-seconds = "60"

[local]
model = "Qwen3-0.6B"
backend = "auto"
model-path = "/path/to/Qwen3-0.6B-Q4_K_M.gguf"
endpoint = ""
threads = "0"
context-size = "2048"
```

## 开发与测试

```bash
.venv/bin/pytest -q
.venv/bin/python -m dox --help
.venv/bin/python -m dox eval --help
```

本地 Qwen 冒烟测试：

```bash
.venv/bin/python -m dox --local --print "查看 git 状态"
.venv/bin/python -m dox eval --local --limit 3 --verbose
```

llama.cpp 的 Metal/kernel 探测日志默认隐藏。排查本地推理后端时，可用
`DOX_LLAMA_LOG=1 dox ...` 临时显示原生日志。

模型只负责规划和路由。命令风险升级、确认、高风险 `--yes` 阻断及执行仍由确定性代码控制；模型输出不会直接静默执行。

设计细节见 [MVP.md](MVP.md)，模型实验见 [LOCAL-MODEL-RESEARCH.md](LOCAL-MODEL-RESEARCH.md) 和 [experiments/README.md](experiments/README.md)。
