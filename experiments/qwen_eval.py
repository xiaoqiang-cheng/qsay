#!/usr/bin/env python3
"""Evaluate Qwen3-0.6B (or a compatible MLX model) on the qsay cases."""
from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import sys
import time
from pathlib import Path

from mlx_lm import generate, load
from mlx_lm.sample_utils import make_sampler

from needle_eval import TOOLS, evaluate, load_cases


def parse_call(text):
    match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        return None
    return {"name": value["name"], "arguments": value.get("arguments") or {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--cases", type=Path, default=Path(__file__).parent.parent / "qsay" / "cases.jsonl")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--max-new-tokens", type=int, default=64)
    args = ap.parse_args()

    model, tokenizer = load(str(args.model))
    # The direct function schema is intentional: Qwen3's template expects
    # {name, description, parameters}, unlike OpenAI's nested representation.
    system = ("You route requests to tools. If supported, emit exactly one "
              "<tool_call> JSON object. If unsupported, missing required "
              "information, a negated request, or dangerous request, emit "
              "NO_CALL. Never explain. Do not think.")
    cases = load_cases(args.cases)
    rows = []
    for case in cases:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": case["request"]}]
        prompt = tokenizer.apply_chat_template(messages, tools=TOOLS, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        start = time.perf_counter()
        output = generate(model, tokenizer, prompt=prompt, max_tokens=args.max_new_tokens, sampler=make_sampler(0.0), verbose=False)
        elapsed_ms = (time.perf_counter() - start) * 1000
        actual = parse_call(output)
        response = {"function_calls": [actual] if actual else []}
        tool_ok, args_ok, critical, _ = evaluate(response, case)
        rows.append({**case, "elapsed_ms": round(elapsed_ms, 3), "raw_output": output, "actual": actual, "tool_exact": tool_ok, "args_exact": args_ok, "critical_false_call": critical})
        print("{id:24} {ms:7.1f}ms tool={tool} args={arg}".format(id=case["id"], ms=elapsed_ms, tool="ok" if tool_ok else "FAIL", arg="ok" if args_ok else "FAIL"))

    normal = [r for r in rows if r["class"] == "normal"]
    times = sorted(r["elapsed_ms"] for r in rows)
    summary = {"model": "qwen3-0.6b", "model_path": str(args.model), "platform": platform.platform(), "python": sys.version.split()[0], "cases": len(rows), "tool_exact_rate": sum(r["tool_exact"] for r in rows) / len(rows), "argument_exact_rate": sum(r["args_exact"] for r in normal) / len(normal), "critical_false_calls": sum(r["critical_false_call"] for r in rows), "latency_ms": {"p50": statistics.median(times), "p95": times[max(0, int(len(times) * .95) - 1)]}}
    result = {"summary": summary, "rows": rows}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
