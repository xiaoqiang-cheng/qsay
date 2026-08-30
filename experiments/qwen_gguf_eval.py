#!/usr/bin/env python3
"""Evaluate Qwen3-0.6B GGUF on CPU with llama.cpp."""
from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import sys
import time
from pathlib import Path

from llama_cpp import Llama

from needle_eval import TOOLS, evaluate, load_cases


def parse_call(text):
    match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.S)
    if not match:
        return None
    try:
        obj = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
        return None
    return {"name": obj["name"], "arguments": obj.get("arguments") or {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--cases", type=Path, default=Path(__file__).parent.parent / "qsay" / "cases.jsonl")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--max-new-tokens", type=int, default=64)
    args = ap.parse_args()

    start_load = time.perf_counter()
    llm = Llama(model_path=str(args.model), n_ctx=2048, n_threads=args.threads, n_threads_batch=args.threads, n_gpu_layers=0, verbose=False)
    load_ms = (time.perf_counter() - start_load) * 1000
    tools = [{"type": "function", "function": tool} for tool in TOOLS]
    system = ("You route requests to tools. If supported, emit exactly one tool "
              "call. If unsupported, missing required information, a negated "
              "request, or dangerous request, emit no tool call. Never explain.")
    rows = []
    for case in load_cases(args.cases):
        # Qwen3 GGUF's soft switch must appear in the user turn. It avoids a long
        # reasoning preamble and is closer to the non-thinking MLX experiment.
        messages = [{"role": "system", "content": system}, {"role": "user", "content": case["request"] + "\n/no_think"}]
        start = time.perf_counter()
        response = llm.create_chat_completion(messages=messages, tools=tools, temperature=0.0, max_tokens=args.max_new_tokens, seed=0)
        elapsed_ms = (time.perf_counter() - start) * 1000
        raw = response["choices"][0]["message"].get("content") or ""
        actual = parse_call(raw)
        normalized = {"function_calls": [actual] if actual else []}
        tool_ok, args_ok, critical, _ = evaluate(normalized, case)
        rows.append({**case, "elapsed_ms": round(elapsed_ms, 3), "raw_output": raw, "actual": actual, "tool_exact": tool_ok, "args_exact": args_ok, "critical_false_call": critical})
        print("{id:24} {ms:7.1f}ms tool={tool} args={arg}".format(id=case["id"], ms=elapsed_ms, tool="ok" if tool_ok else "FAIL", arg="ok" if args_ok else "FAIL"))
        llm.reset()
    normal = [r for r in rows if r["class"] == "normal"]
    times = sorted(r["elapsed_ms"] for r in rows)
    summary = {"model": "qwen3-0.6b", "model_path": str(args.model), "format": "Q4_K_M GGUF", "backend": "llama.cpp CPU", "platform": platform.platform(), "python": sys.version.split()[0], "cases": len(rows), "load_ms": round(load_ms, 3), "tool_exact_rate": sum(r["tool_exact"] for r in rows) / len(rows), "argument_exact_rate": sum(r["args_exact"] for r in normal) / len(normal), "critical_false_calls": sum(r["critical_false_call"] for r in rows), "latency_ms": {"p50": statistics.median(times), "p95": times[max(0, int(len(times) * .95) - 1)]}}
    result = {"summary": summary, "rows": rows}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
