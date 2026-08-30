# dox 本地小模型调研

状态：Research v0.2
日期：2026-08-30
结论：Needle 2 base 已被实测排除为中文默认模型；当前 PoC 首选 Qwen3-0.6B 4-bit，但它仍未达到安全上线门槛。Python 可以用于模型 runner，核心选择不再受 Rust FFI 限制。

## 1. 任务与评估标准

dox 的本地模型不是聊天模型，也不是需要长上下文的 coding agent。它只需要在很短的请求内完成：

```text
自然语言请求 → 工具/意图选择 → 参数槽位提取 → 拒绝或追问
```

命令文本由 Adapter/平台渲染器生成，模型不应直接获得执行权限。候选模型要重点满足：

- 高频意图和工具路由准确
- 参数只取自用户明确表达的内容，不凭空补全
- 能拒绝无关、否定、危险或缺参请求
- 输出结构稳定，最好支持 Grammar/JSON Schema 约束
- 纯 CPU 可运行，常驻内存和冷启动都可接受
- 支持中文、英文和中英混合技术表达
- 可以在 macOS、Linux、Windows PowerShell 目标上分发
- 不要求用户安装 Python、Node.js 或联网下载模型才能启动核心 CLI

## 2. Needle 2

### 2.1 官方资料显示的能力

Needle 2 是 Cactus Compute 发布的 45M 参数工具调用、设备控制和结构化抽取模型。官方仓库描述：模型和引擎合并为约 14MB 的单一二进制，完整会话约使用 28MB RAM；采用 CQ2-bit 量化和自有推理引擎。资料还明确提供：

- 文本输入、结构化 JSON 工具调用输出
- 根据声明的 Schema 编译字节级 Grammar，约束工具名、参数键和值
- 每次响应带置信度，可设置阈值后执行或升级到更大模型
- 大工具目录的内置检索，只把最高相关的五个工具放入当前解码集合
- 256-token 滑动窗口，工具作为 KV sink 保留
- 未声明工具能完成的请求返回空调用，而不是自由文本答案
- 缺少证据的可选参数省略，不主动猜测

这些特征与 dox 的“意图识别 + 路由 + 拒绝”需求高度匹配，尤其是工具检索、约束解码和置信度门控。

官方资料：

- [Needle GitHub](https://github.com/cactus-compute/needle)
- [Needle API 与行为契约](https://github.com/cactus-compute/needle/blob/main/doc/apis.md)
- [Needle 2 模型仓库](https://huggingface.co/Cactus-Compute/needle2)

### 2.2 对 dox 的直接映射

每个 Adapter 可以映射成 Needle 工具 Schema：

```json
{
  "name": "archive_extract",
  "description": "Extract an archive into a destination directory.",
  "parameters": {
    "type": "object",
    "properties": {
      "source": {"type": "string", "description": "archive path"},
      "destination": {"type": "string", "description": "destination path"}
    },
    "required": ["source", "destination"]
  }
}
```

模型返回 `archive_extract(source, destination)`，然后由 dox 的 Adapter 根据平台渲染为 tar、7z 或 PowerShell 命令。模型不返回或执行任意 Shell 字符串。

### 2.3 实测结果：中文不合格

在 macOS arm64、`cactus-needle 2.0.11` / Needle engine 2.0.3 上使用 7 个工具和 33 个共享用例实测。每条用例前调用 `agent.reset()`，模拟独立 CLI 请求：

| 指标 | Needle 2 base |
|---|---:|
| 全部用例工具 exact match | 57.6% |
| 正常请求参数 exact match | 26.3% |
| critical false-call | 2 |
| 热运行 p50 / p95 | 304 / 366 ms |
| 引擎报告 RAM | 约 38–45MB |

英文简单调用可用，例如 `Extract backup.tar into ./backup` 能正确选工具并抽取路径；中文不能稳定使用：

```text
解压 backup.tar 到 ./backup
→ copy_file(source="backup.tar", destination="到 ./backup")
→ confidence 0.8859

不要删除 build 目录
→ extract_archive(source="不要删除", destination="目录")
→ confidence 0.9778
```

这证明两个关键点：

1. Needle 2 base 的中文能力不是“略弱”，而是会出现高置信误路由，无法通过 confidence 阈值修补。
2. 英文也会在缺参时捏造参数，例如 `Extract backup.tar` 将 destination 设为 `backup.tar`，confidence 为 1.0；否定句也出现高置信调用。

因此 Needle 2 base **不能成为 dox 的中文默认模型，也不能单独承担拒绝和安全决策**。它的速度、尺寸和 Grammar 仍然有价值，后续只保留“用 dox 中英数据专门微调/LoRA”的研究支线，而且必须与 Qwen 路线按相同用例复测。

### 2.4 工程接入候选

按复杂度从低到高：

1. **sidecar runner（推荐 PoC）：** `dox` 启动可选的 `dox-model`，通过 stdin/stdout 传递 NDJSON；模型进程崩溃不拖垮 CLI，Rust 核心不需要绑定 C ABI。缺点是多一个文件和进程。
2. **Rust FFI：** 将 Needle 的平台动态库或静态库作为可选模型包加载，直接调用 `needle_init`、`needle_complete` 等 C API。延迟和用户体验最好，但需要维护跨平台 ABI、模型包版本和许可证。
3. **Python sidecar：** 现在可接受。用户明确允许 Python，只要交互足够快。Python 不应强制成为核心 CLI 依赖，可使用 `uv`/独立环境或打包后的可选 runner；模型进程常驻以避免每次加载。

PoC 采用 Python sidecar，因为它能最快回答“模型对 dox 请求是否足够好”，而不会先陷入跨平台链接问题。生产分发优先 llama.cpp/GGUF；Apple MLX 只用于快速实验和 macOS 性能对照。

## 3. 对比候选

| 候选 | 规模/定位 | 结构化工具调用 | CPU 与分发 | 中文风险 | dox 建议 |
|---|---|---|---|---|---|
| Needle 2 | 45M，专门工具调用/抽取 | 原生 Grammar、检索、confidence | 约 14MB 引擎、约 38–45MB 实测 | 中文高置信误调用 | 排除 base；保留专项微调支线 |
| Qwen3 0.6B | 多语言通用小模型 | 原生 Qwen tool-call 模板，关闭 thinking | MLX 4-bit 335MB；需补 GGUF CPU 测试 | 共享用例中最好 | 当前首选 PoC |
| LFM2.5 1.2B Instruct | 明确支持中英和 function calling | Pythonic tool call/JSON | Q4_K_M 697MiB；CPU p50 2.6s | 多工具混淆严重 | 当前排除默认候选 |
| FunctionGemma 270M | 专门函数调用的可微调底座 | 自有 function call token/template | 4-bit 约 151MB MLX / 241MiB GGUF | base 中文未证明 | 值得做 dox 专项微调，不用 base 直出 |
| Granite 3.2 2B | Apache-2.0，多语言、function calling | 模板/Grammar | 约 2B，明显更重 | 明确支持中文 | 质量 fallback，尚未实测 |
| xLAM 1B | 专门函数调用 | 函数调用训练 | 约 1B | 中文未证明 | 仓库访问/许可确认后再测 |

FunctionGemma 官方明确说明 270M base 旨在针对具体函数调用任务微调，而不是直接作为通用对话模型。它的官方仓库需要接受 Gemma 许可；社区转换权重虽然可下载，发布时仍受 Gemma 条款约束。LFM2.5 使用 LFM Open License v1.0，商业使用对年收入 1,000 万美元及以上的法律实体有限制，不适合作为 dox 无条件默认分发模型。

### 3.1 三模型统一实测

实验机器为 macOS arm64。结果在 `experiments/results/`，小数据集用于方向筛选，不是发布验收：

| 模型/运行时 | 包体 | 工具 exact | 正常参数 exact | critical false-call | p50 / p95 |
|---|---:|---:|---:|---:|---:|
| Needle 2 base / 原生 CPU engine | 约 14MB | 57.6% | 26.3% | 2 | 304 / 366ms |
| Qwen3-0.6B 4-bit / MLX | 335MB | **87.9%** | **94.7%** | 2 | **782 / 890ms** |
| LFM2.5-1.2B Q4_K_M / llama.cpp CPU | 697MiB | 39.4% | 52.6% | 2 | 2609 / 3203ms |

Qwen 的 2 个 critical case 都是根目录/当前目录删除请求产生 `remove_path` 调用；这类路径可以且必须由确定性安全层拒绝。Qwen 还会在少数缺参请求中自行补 `.` 或 `backup`，因此模型返回后仍要做参数证据校验和追问，不能直接执行。

LFM 的低分不意味着模型完全没有中文能力。单个解压工具配合中文提示可正确调用；问题出现在多工具路由时，它频繁把 copy、Git、find、install 错选为 `extract_archive`，且输出格式在 Pythonic call、JSON 和自然语言之间漂移。它在本任务上更慢、更大、受许可证限制，当前没有继续优化为默认模型的价值。

## 4. 推荐的 Provider 形态

模型不直接返回 Shell。统一接口建议为：

```text
LocalProvider.plan(request, tool_catalog, environment)
  -> { tool_call, arguments, confidence, clarification }
```

Provider 的输出再经过：

```text
Schema 校验 → Adapter 渲染 → 依赖检查 → 风险扫描 → 用户确认
```

模型部署建议采用可选包：

```text
dox                 核心 CLI，规则之外的安全与执行逻辑
dox-model-qwen      默认候选，本地 4-bit runner
dox-model-needle    可选的实验/专项微调 runner
```

基础 `dox` 不因未安装模型而无法启动；`dox model install needle2` 才下载或导入模型。离线机器可在联网机器下载后复制模型包，运行时不自动联网。

## 5. 最小评测方案

### 5.1 工具目录

先建立 100 个高频 Adapter，覆盖文件、查找、文本、归档、Git、进程、网络、包管理、Docker 和常用开发工具；每个 Adapter 声明平台、参数、依赖、风险和中英文示例。

### 5.2 请求集

建议至少 1,000 条，比例大致为：

- 60% 正常请求：中文、英文、中英混合、同义改写
- 15% 缺参或歧义：应追问
- 10% 无关请求：应拒绝
- 10% 否定/危险请求：应拒绝或进入高风险状态
- 5% 边界输入：空格路径、特殊字符、长路径、平台差异

### 5.3 指标和门槛

- 工具选择 exact match：核心意图 ≥ 95%，整体 ≥ 90%
- 必填参数 exact match：≥ 90%
- 缺参/无关/否定请求误调用：0 个 critical case
- 危险命令未经明确确认自动执行：0 个
- Needle 工具检索 hit@5：≥ 98%
- 置信度阈值以下的请求应升级、追问或拒绝，不得静默执行
- macOS Apple Silicon、Linux x86_64、Windows x86_64 分别记录 p50/p95 延迟、冷启动、热运行 RAM

### 5.4 决策门

- Qwen3-0.6B 在 1,000+ 用例达到中文核心意图 ≥ 95%、英文 ≥ 95%、模型 critical false-call 可被确定性安全层 100% 阻断：进入默认本地 Provider 候选
- Qwen 未达标：使用 dox 领域数据 LoRA，并与 FunctionGemma 专项微调比较；不通过关键词规则冒充模型能力
- 100+ 工具目录路由下降：先用 embedding/模型检索缩到 top-k 工具，再由调用模型选择；检索本身也必须模型化，不写关键词意图规则
- 所有本地候选均不达标：保留 API Provider，不为了离线承诺牺牲安全性

## 6. 当前结论

本轮已经回答了最关键的问题：Needle 2 base 不支持达到 dox 要求的中文路由，且 confidence 不能防止高置信错误；LFM2.5-1.2B 在该任务上不比更小的 Qwen 更好。Qwen3-0.6B 4-bit 是当前最有希望的平衡点，335MB 包体和亚秒 MLX 热延迟可接受，但 33 条小用例的 87.9% 工具准确率仍不足以上线。

下一阶段不应把某个模型匆忙接入主 CLI，而是：

1. 将评测集扩到至少 1,000 条，覆盖 50–100 个真实 Adapter 和三平台表述。
2. 在 llama.cpp Qwen GGUF CPU 路径复测冷启动、p50/p95、RSS，验证 Windows/Linux 可交付性。
3. 实现确定性参数证据校验和危险路径拦截，再复算端到端 critical false-call。
4. 以同一训练集比较 Qwen3-0.6B LoRA 与 FunctionGemma-270M 专项微调；后者只有在显著缩小包体且质量不降时才值得采用。
5. 只有达到决策门后，才把本地 Provider 设为默认；此前 API Provider 继续作为可靠路径。

## 7. 资料与版本记录

- Needle GitHub 当前仓库和 README：2026-08-30 读取
- Needle API/行为契约：2026-08-30 读取
- `cactus-needle` PyPI 最新版本查询到 2.0.11：2026-08-30 读取
- Needle 仓库许可证字段为 Apache-2.0；模型/引擎二进制的单独分发许可仍需发布前确认
- Qwen3-0.6B 模型卡：Apache-2.0、100+ 语言、tool calling、可关闭 thinking；2026-08-30 读取并实测
- LFM2.5-1.2B-Instruct 模型卡及 LFM Open License v1.0；2026-08-30 读取并实测
- FunctionGemma 270M 模型卡：官方明确要求面向具体任务微调；Gemma license；2026-08-30 读取，base 仅做探针，未列入统一分数
- 复现脚本与原始结果：`experiments/`
