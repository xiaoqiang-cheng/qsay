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

Needle Python API 的 `weights=` 参数是 LoRA/微调权重，会关闭未校准的 confidence head；评估官方 base model 时应省略 `--checkpoint`。下载的 `needle2.cact` 只用于微调或部署归档，不作为本报告的 base 结果。

## Qwen3 / LFM2.5

Apple Silicon 上可使用 `mlx-lm`，跨平台发布仍建议转换为 GGUF 后通过 llama.cpp 运行。模型的工具调用模板不同，比较脚本必须把输出规范化为：

```json
{"name":"tool_name","arguments":{"key":"value"}}
```

评测报告应同时记录模型版本、量化格式、冷启动/热运行 p50/p95、峰值 RSS、模型包大小和许可证。任何 `negative`、`irrelevant` 或不允许的 `dangerous` 用例产生调用，都算 critical false-call。
