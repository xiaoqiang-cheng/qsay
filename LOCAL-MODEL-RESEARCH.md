# dox 本地小模型调研

状态：Research v0.1  
日期：2026-08-30  
结论：Needle 2 是当前最值得做 PoC 的候选，但在确认中文效果和 Rust 集成成本前，不直接承诺作为默认模型。

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

### 2.3 主要风险

1. **中文能力未被官方资料证明。** 官方示例和环境套件以英文为主，不能从 45M 参数或英文工具调用结果推断中文准确率。中文、英文和混合表达必须单独评测。
2. **目录检索需要验证。** 官方描述只渲染 top-5 工具；dox 未来可能有数百个 Adapter，需要测试相似工具、同义词和跨类别请求的召回率。
3. **引擎集成路径仍需确认。** Python 包 `cactus-needle` 的运行时依赖很小，但官方 Python API 通过 ctypes 加载平台动态库；模型仓库还提供平台 runner、C 头文件和库的线索。Rust 侧应先验证 C ABI 和分发许可，再决定 FFI 还是 sidecar 进程。
4. **版本和量化需要锁定。** 官方仓库近期仍在快速迭代，公开 issue 中出现过 JSON Grammar 边界、量化后微调行为变化和部分抽取错误。dox 初期应使用固定 base engine，不依赖微调和 2-bit 以外的实验特性，所有计划仍需 dox 自己的 JSON、安全校验。
5. **256-token 窗口是优势也是边界。** 对 dox 的短请求足够，但不能把长目录、长历史或完整项目上下文塞入模型；这与产品边界一致。

### 2.4 工程接入候选

按复杂度从低到高：

1. **sidecar runner（推荐 PoC）：** `dox` 启动可选的 `dox-model`，通过 stdin/stdout 传递 NDJSON；模型进程崩溃不拖垮 CLI，Rust 核心不需要绑定 C ABI。缺点是多一个文件和进程。
2. **Rust FFI：** 将 Needle 的平台动态库或静态库作为可选模型包加载，直接调用 `needle_init`、`needle_complete` 等 C API。延迟和用户体验最好，但需要维护跨平台 ABI、模型包版本和许可证。
3. **Python 嵌入：** 不推荐。虽然官方包最容易验证，但会破坏 dox 的轻量和单文件安装原则。

PoC 先采用 sidecar，因为它能最快回答“模型对 dox 请求是否足够好”，而不会先陷入跨平台链接问题。确认质量后，再决定是否把 runner 收进单一安装包。

## 3. 对比候选

| 候选 | 规模/定位 | 结构化工具调用 | CPU 与分发 | 中文风险 | dox 建议 |
|---|---|---|---|---|---|
| Needle 2 | 45M，专门工具调用/抽取 | 原生 Schema Grammar、top-5 检索、confidence | 约 14MB 引擎、约 28MB RAM；需验证 Rust C ABI | 官方资料未证明 | 首选 PoC |
| FunctionGemma 270M | 专门函数调用的小型 Gemma，适合围绕自有工具微调 | 有函数调用训练；约束解码依赖推理栈 | 权重和运行时明显大于 Needle；生态更广 | 需实测 | 第二候选/质量基线 |
| LFM2.5 350M/1.2B | 紧凑通用模型，部分版本面向指令/工具使用 | 通常需要模板、Grammar 或额外微调 | 350M 仍可 CPU；1.2B 更偏质量而非极致轻量 | 需实测 | 作为通用模型基线 |
| xLAM 1B | 专门函数调用，偏质量和工具复杂度 | 函数调用训练较成熟 | 约 1B，CPU 可行但内存/延迟更高 | 需实测 | 质量 fallback，不是最小默认 |
| Qwen3 0.6B | 多语言通用小模型 | 原生工具调用能力不应假定；需 Grammar/模板 | CPU 可行，但模型包远大于 Needle | 中文相对值得验证 | 中文质量对照组 |

表中的“需实测”不是否定候选，而是表示不能用通用聊天或英文基准替代 dox 的中文路由评测。模型选择必须以同一工具目录、同一测试集和同一安全策略比较。

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
dox-model-needle    可选 Needle 2 runner 和模型引擎
dox-model-qwen      可选中文质量对照模型
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

- Needle 中文核心意图 ≥ 90%、英文 ≥ 95%、critical case 为 0：进入默认本地 Provider 候选
- 中文低于 90%：不直接上线为中文默认，先尝试英文 canonical schema、双语示例或轻量 LoRA，再与 Qwen3-0.6B 比较
- Needle 检索在 100+ 工具目录不稳定：增加外部候选检索或按类别分片，不扩大模型上下文
- 所有本地候选均不达标：保留 API Provider，不为了离线承诺牺牲安全性

## 6. 当前结论

Needle 2 是最符合 dox “极小、快速、工具调用、拒绝未知请求”目标的模型，值得立即做 PoC；它不是无需验证的确定答案。第一实验应优先回答中文能力、100 工具检索和 Rust/sidecar 分发三个问题。FunctionGemma 和 Qwen3-0.6B 作为质量与中文对照，LFM2.5/xLAM 作为更大质量 fallback。

## 7. 资料与版本记录

- Needle GitHub 当前仓库和 README：2026-08-30 读取
- Needle API/行为契约：2026-08-30 读取
- `cactus-needle` PyPI 最新版本查询到 2.0.11：2026-08-30 读取
- Needle 仓库许可证字段为 Apache-2.0；模型/引擎二进制的单独分发许可仍需发布前确认

