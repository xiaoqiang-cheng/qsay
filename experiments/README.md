# 本地模型路由评测

评测只测“工具选择 + 参数提取”，不会执行模型输出的命令。所有候选模型使用同一份 `cases.jsonl` 和等价的工具 Schema。

## Needle 2

```bash
python3 -m venv .venv
.venv/bin/pip install cactus-needle==2.0.11
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \
  .venv/bin/needle download Cactus-Compute/needle2/needle2.cact \
  --out experiments/models
NEEDLE_TELEMETRY=0 .venv/bin/python experiments/needle_eval.py \
  --output experiments/results/needle2-base.json
```

`experiments/models/` 被 `.gitignore` 忽略；结果 JSON 可以提交，用于比较不同机器和模型版本。没有 Hugging Face 镜像时，将 `HF_ENDPOINT` 去掉即可使用官方地址；核心运行阶段不联网。

脚本在每条 case 前调用 `agent.reset()`，因为每次 dox CLI 请求应是独立会话；否则 Needle 的 256-token 滑动窗口会让前一个测试污染后一个测试，尤其会复用旧路径参数。

Needle Python API 的 `weights=` 参数是 LoRA/微调权重，会关闭未校准的 confidence head；评估官方 base model 时应省略 `--checkpoint`。下载的 `needle2.cact` 只用于微调或部署归档，不作为本报告的 base 结果。

## Qwen3 / LFM2.5

Apple Silicon 上可使用 `mlx-lm`，跨平台发布仍建议转换为 GGUF 后通过 llama.cpp 运行。模型的工具调用模板不同，比较脚本必须把输出规范化为：

```json
{"name":"tool_name","arguments":{"key":"value"}}
```

Qwen3-0.6B 的 MLX 实验（需要 Apple Silicon）：

```bash
.venv/bin/pip install 'mlx-lm==0.29.1'
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \
  .venv/bin/python -m mlx_lm.convert --hf-path Qwen/Qwen3-0.6B -q \
  --q-bits 4 --q-group-size 64 --mlx-path experiments/models/qwen3-0.6b-4bit
.venv/bin/python experiments/qwen_eval.py \
  --model experiments/models/qwen3-0.6b-4bit \
  --output experiments/results/qwen3-0.6b-4bit.json
```

评测关闭 thinking、使用 greedy decoding，并通过 Qwen 的 `<tool_call>` 模板解析输出。该脚本是质量实验，不代表最终跨平台运行时；Windows/Linux 需要使用 llama.cpp/GGUF 或其他 CPU backend 做同等复测。

评测报告应同时记录模型版本、量化格式、冷启动/热运行 p50/p95、峰值 RSS、模型包大小和许可证。任何 `negative`、`irrelevant` 或不允许的 `dangerous` 用例产生调用，都算 critical false-call。

LFM2.5-1.2B 的跨平台 GGUF 实验：

```bash
.venv/bin/pip install 'llama-cpp-python==0.3.16'
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \
  .venv/bin/python -c 'from huggingface_hub import hf_hub_download; hf_hub_download("LiquidAI/LFM2.5-1.2B-Instruct-GGUF", "LFM2.5-1.2B-Instruct-Q4_K_M.gguf", local_dir="experiments/models/lfm2.5-1.2b-gguf")'
.venv/bin/python experiments/lfm_eval.py \
  --model experiments/models/lfm2.5-1.2b-gguf/LFM2.5-1.2B-Instruct-Q4_K_M.gguf \
  --output experiments/results/lfm2.5-1.2b-q4-k-m.json
```

该模型按官方文档偏好输出 Pythonic tool call；脚本同时兼容其偶发的 JSON list 输出。这里明确限制 `n_gpu_layers=0`，测的是 CPU 路径。
